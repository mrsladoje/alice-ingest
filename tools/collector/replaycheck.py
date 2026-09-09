#!/usr/bin/env python3
"""Replay fixtures through the production collector configuration.

The configuration is never hand-written here: it comes out of
`tools/soak/mkconfig.py`, the same renderer the soak rig uses, which reads
`deploy/roles/loggy_collector/templates/`. The outputs are swapped for the file sink,
so which file a record lands in is the routing assertion, and the JSON in that
file is the field assertion.

Fluent Bit runs in Docker at a named version. The farm does not run one version
-- 4.0.1 and 4.0.14 are both deployed -- so a result from a single version does
not prove production behaviour, and `--version` may be repeated.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MKCONFIG = os.path.join(REPO, "tools", "soak", "mkconfig.py")
PARSERS = os.path.join(
    REPO, "deploy", "roles", "collector", "templates", "parsers.yaml.j2")
FIXTURES = os.path.join(HERE, "fixtures")

NODE_ID = "node-01"
IL_PORT = 5170
# Fluent Bit's own metrics endpoint, published so readiness can be asked rather
# than guessed. Not 2020 on the host, to stay clear of anything else local.
HTTP_PORT = 2022


def render(config_dir, extra):
    out = os.path.join(config_dir, "collector.yaml")
    cmd = [sys.executable, MKCONFIG, "--out", out, "--sink", "file",
           "--sink-dir", "/out", "--flush", "1",
           "--parsers-out", os.path.join(config_dir, "parsers.yaml")] + extra
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def wait_ready(deadline=60.0):
    """Wait until Fluent Bit is actually up, rather than until Docker returns.

    `docker run -d` returns when the container exists. The published port is
    answered by Docker's proxy from that moment, so a client connects happily to
    a socket the engine inside has not bound yet, sends, and the records go
    nowhere. That raced on a loaded machine and looked exactly like a version
    difference between 4.0.1 and 4.0.14. Asking the metrics endpoint removes the
    guess.
    """
    end = time.time() + deadline
    while time.time() < end:
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/api/v1/metrics" % HTTP_PORT,
                timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def feed_infologger(records, port, deadline=20.0):
    """Speak to the tcp input the way infoLoggerD does: one JSON object a line."""
    end = time.time() + deadline
    sock = None
    while time.time() < end:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            time.sleep(0.4)
    if sock is None:
        raise RuntimeError("infologger tcp input never accepted a connection")
    with sock:
        for record in records:
            sock.sendall((json.dumps(record) + "\n").encode())
            time.sleep(0.01)


def run(version, log_root, il_records, seconds, keep=None, work=None,
        during=None, settle_after=0.0, during_when=None):
    # Colima shares the user's home, not /private/tmp, so a work tree outside
    # it silently gives the container an empty bind mount.
    root = os.path.join(os.path.expanduser("~"), ".cache", "alice-replaycheck")
    os.makedirs(root, exist_ok=True)
    if work is None:
        work = tempfile.mkdtemp(prefix="run-", dir=root)
    config_dir = os.path.join(work, "cfg")
    out_dir = os.path.join(work, "out")
    storage = os.path.join(work, "storage")
    for path in (config_dir, out_dir, storage):
        os.makedirs(path, exist_ok=True)
    for stale in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, stale))
    # The orchestrator's log is read through the fixture tree rather than at its
    # real absolute path, because the production path is a glob over a directory
    # and a directory is what the fixtures already provide.
    odc_dir = os.path.join(log_root, "odc")
    render(config_dir, ["--odc", "on", "--odc-path", "/logs/odc/*.log"]
           if os.path.isdir(odc_dir) else [])

    name = "replaycheck-%d" % os.getpid()
    subprocess.run(["docker", "rm", "-f", name],
                   capture_output=True, check=False)
    # The daemon log is read at its real absolute path, not through log_root,
    # because that is what the production input says. Mounting the fixture there
    # is what keeps this a test of the shipped configuration.
    ildaemon = os.path.join(log_root, "varlog", "o2-infologger-daemon.log")
    extra_mounts = []
    if os.path.exists(ildaemon):
        extra_mounts = ["-v", "%s:/var/log/o2-infologger-daemon.log:ro" % ildaemon]
    cmd = [
        "docker", "run", "-d", "--name", name,
        "-p", "%d:%d" % (IL_PORT, IL_PORT),
        "-p", "%d:2020" % HTTP_PORT,
        "-v", "%s:/etc/fluent-bit:ro" % config_dir,
        "-v", "%s:/logs:ro" % log_root,
        "-v", "%s:/out" % out_dir,
        "-v", "%s:/storage" % storage,
        "-e", "ALICE_NODE_ID=%s" % NODE_ID,
        "-e", "ALICE_LOG_ROOT=/logs",
        "-e", "ALICE_FB_STORAGE_PATH=/storage",
        "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0",
        "-e", "ALICE_FB_HTTP_PORT=2020",
        "-e", "ALICE_INFOLOGGER_TCP_PORT=%d" % IL_PORT,
        "-e", "ALICE_OS_HTTP_PORT=9200",
    ] + extra_mounts + [
        "fluent/fluent-bit:%s" % version,
        "/fluent-bit/bin/fluent-bit", "-c", "/etc/fluent-bit/collector.yaml",
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    try:
        if not wait_ready():
            raise SystemExit("fluent-bit never served its metrics endpoint")
        if il_records:
            feed_infologger(il_records, IL_PORT)
        event_at = None
        if during is not None:
            # Something that has to happen while the collector is RUNNING and
            # holding the files open. Rotation is the case: renaming a file the
            # tail already closed proves nothing at all.
            #
            # `during_when` is the precondition, ASKED rather than assumed. A
            # fixed sleep here was the whole flake: it cannot tell the
            # difference between a collector that has read the file and one
            # that has not started, and the metrics endpoint answers from
            # Docker's proxy before either. Waiting for the line to reach the
            # sink is the only statement that means "the tail is at the end of
            # this file".
            if during_when is not None:
                until = time.time() + 60.0
                while time.time() < until and not during_when(out_dir):
                    time.sleep(0.25)
                if not during_when(out_dir):
                    raise SystemExit(
                        "the precondition for the mid-run event never held; "
                        "the collector never read the file it was to act on")
            else:
                time.sleep(6.0)
            during()
            event_at = time.time()
        # Wait for the sink to stop growing rather than for a fixed period. A
        # fixed wait passed on one version and lost every InfoLogger record on
        # another, which looks exactly like a version difference and is not.
        #
        # Stability alone is not enough either. The tails flush seconds before
        # the records fed over TCP arrive, so on a loaded machine the output
        # goes quiet in the gap between them and a naive settle stops there.
        # The count fed in is known, so it is waited for by name.
        settled = 0.0
        stable = 0
        last = -1
        while settled < seconds:
            time.sleep(0.5)
            settled += 0.5
            size = sum(os.path.getsize(os.path.join(out_dir, f))
                       for f in os.listdir(out_dir))
            stable = stable + 1 if size and size == last else 0
            last = size
            if stable < 6 or settled < 8.0:
                continue
            # A mid-run event needs its own floor, and without one this test was
            # a race it lost about one time in three. The output goes quiet
            # within a second of the rotation, so the ordinary settle fires at
            # eight seconds -- before the tail's next directory sweep has even
            # happened. Nothing was wrong with Fluent Bit; the harness stopped
            # watching too early and called it a pass twice out of three.
            if event_at is not None and time.time() - event_at < settle_after:
                continue
            if il_records:
                path = os.path.join(out_dir, "infologger.jsonl")
                if not os.path.exists(path):
                    continue
                with open(path, errors="replace") as fh:
                    if sum(1 for line in fh if line.strip()) < len(il_records):
                        continue
            break
        logs = subprocess.run(["docker", "logs", name],
                              capture_output=True, text=True).stderr
    finally:
        subprocess.run(["docker", "stop", "-t", "10", name],
                       capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, check=False)

    records = {}
    for fn in sorted(os.listdir(out_dir)):
        if not fn.endswith(".jsonl"):
            continue
        rows = []
        with open(os.path.join(out_dir, fn), errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # out_file writes "<tag>: [<time>, {<record>}]".
                head, sep, body = line.partition(": ")
                if not sep:
                    rows.append({"__unparsed__": line})
                    continue
                try:
                    when, record = json.loads(body)
                except (ValueError, TypeError):
                    rows.append({"__unparsed__": line})
                    continue
                record["__tag__"] = head
                record["__time__"] = when
                rows.append(record)
        records[fn] = rows
    if keep:
        shutil.copytree(config_dir, os.path.join(keep, "cfg"), dirs_exist_ok=True)
        shutil.copytree(out_dir, os.path.join(keep, "out"), dirs_exist_ok=True)
    return records, logs, work


def dry_run(version, extra, label):
    """Ask Fluent Bit itself whether it would accept the configuration.

    This is the only check available for the journal. The systemd input reads a
    real journal and a laptop has none, so the records cannot be exercised --
    but a property the binary rejects makes the collector refuse to start, and
    that is worth catching without a farm. It already caught one: 4.0.1 and
    4.0.14 accept multiline.parser on the systemd input and 5.0.8 rejects it.
    """
    root = os.path.join(os.path.expanduser("~"), ".cache", "alice-replaycheck")
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="dry-", dir=root)
    try:
        render(work, extra)
        result = subprocess.run([
            "docker", "run", "--rm", "-v", "%s:/etc/fluent-bit:ro" % work,
            "-e", "ALICE_NODE_ID=node-01", "-e", "ALICE_LOG_ROOT=/logs",
            "-e", "ALICE_FB_STORAGE_PATH=/storage",
            "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0", "-e", "ALICE_FB_HTTP_PORT=2020",
            "-e", "ALICE_INFOLOGGER_TCP_PORT=5170", "-e", "ALICE_OS_HTTP_PORT=9200",
            "fluent/fluent-bit:%s" % version,
            "/fluent-bit/bin/fluent-bit", "--dry-run",
            "-c", "/etc/fluent-bit/collector.yaml",
        ], capture_output=True, text=True)
        text = (result.stdout or "") + (result.stderr or "")
        if "configuration test is successful" in text:
            print("   %s: the binary accepts the configuration" % label)
            return 0
        print("   FAIL %s: fluent-bit rejects the configuration" % label)
        for line in text.splitlines():
            if "error" in line.lower():
                print("      %s" % line)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def journal_check(version, journal_dir, seconds):
    """Run the systemd input over a captured journal directory.

    The journal is the one source that cannot be fixtured: its files are binary,
    at least eight megabytes each, and belong to the machine that wrote them.
    Point this at a directory captured from an EPN and the same input, the same
    multiline rule, the same parsers and the same routing run over real entries.

    Capture one with:
        ssh epn146 'sudo find /var/log/journal -name "system*.journal"'
        ssh epn146 "sudo cat <path>" > <dir>/<machine-id>/system.journal
    """
    root = os.path.join(os.path.expanduser("~"), ".cache", "alice-replaycheck")
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="journal-", dir=root)
    config_dir = os.path.join(work, "cfg")
    out_dir = os.path.join(work, "out")
    storage = os.path.join(work, "storage")
    for path in (config_dir, out_dir, storage):
        os.makedirs(path)
    render(config_dir, ["--journald", "on",
                        "--journald-path", "/var/log/journal"])
    name = "replaycheck-journal-%d" % os.getpid()
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run([
        "docker", "run", "-d", "--name", name,
        "-v", "%s:/etc/fluent-bit:ro" % config_dir,
        "-v", "%s:/var/log/journal:ro" % os.path.abspath(journal_dir),
        "-v", "%s:/logs:ro" % os.path.join(work, "cfg"),
        "-v", "%s:/out" % out_dir,
        "-v", "%s:/storage" % storage,
        "-e", "ALICE_NODE_ID=%s" % NODE_ID, "-e", "ALICE_LOG_ROOT=/logs",
        "-e", "ALICE_FB_STORAGE_PATH=/storage",
        "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0", "-e", "ALICE_FB_HTTP_PORT=2020",
        "-e", "ALICE_INFOLOGGER_TCP_PORT=5171", "-e", "ALICE_OS_HTTP_PORT=9200",
        "fluent/fluent-bit:%s" % version,
        "/fluent-bit/bin/fluent-bit", "-c", "/etc/fluent-bit/collector.yaml",
    ], check=True, capture_output=True)
    try:
        settled = 0.0
        last = -1
        stable = 0
        while settled < max(seconds, 60):
            time.sleep(1.0)
            settled += 1.0
            size = sum(os.path.getsize(os.path.join(out_dir, f))
                       for f in os.listdir(out_dir)) if os.listdir(out_dir) else 0
            stable = stable + 1 if size and size == last else 0
            last = size
            if stable >= 8 and settled >= 15:
                break
    finally:
        subprocess.run(["docker", "stop", "-t", "10", name],
                       capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, check=False)

    local = central = 0
    comms = set()
    fields = set()
    for fn in os.listdir(out_dir):
        for line in open(os.path.join(out_dir, fn), errors="replace"):
            head, sep, body = line.partition(": ")
            if not sep:
                continue
            try:
                _, record = json.loads(body)
            except (ValueError, TypeError):
                continue
            if record.get("log_source") != "journald":
                continue
            fields.update(record)
            if record.get("comm"):
                comms.add(record["comm"])
            if head == "family.central":
                central += 1
            else:
                local += 1

    problems = []
    if local + central == 0:
        problems.append("the systemd input produced no records from %s"
                        % journal_dir)
    if central == 0:
        problems.append("nothing routed to durable storage; the priority rule "
                        "never fired")
    if len(fields) > 40:
        problems.append("a journal record carries %d distinct fields; the "
                        "allowlist is not trimming" % len(fields))
    for line in problems:
        print("   FAIL %s" % line)
    if not problems:
        print("   journal: %d records, %d local and %d durable, %d fields kept"
              % (local + central, local, central, len(fields)))
        if comms:
            print("   journal: kernel traces name %s"
                  % ", ".join(sorted(comms)[:5]))
        else:
            print("   journal: no kernel trace carried a Comm: in this capture")
    shutil.rmtree(work, ignore_errors=True)
    return 1 if problems else 0


def find(records, spec):
    want_file = spec["file"]
    needle = spec.get("message_contains")
    for row in records.get(want_file, []):
        if needle is not None and needle not in str(row.get("message", "")):
            continue
        if needle is None and spec.get("log_key") is not None \
                and spec["log_key"] not in str(row.get("log", "")):
            continue
        return row
    return None


def check(records, expect):
    failures = []
    for case in expect.get("cases", []):
        row = find(records, case)
        if row is None:
            failures.append("%s: no record in %s matching %r"
                            % (case["name"], case["file"],
                               case.get("message_contains")))
            continue
        for key, want in (case.get("expect") or {}).items():
            got = row.get(key)
            # Type matters, and comparing as strings would hide the whole point
            # of the `types` directive: "4" and 4 look the same in a string
            # comparison and only one of them can be charted.
            if isinstance(want, bool) or not isinstance(want, (int, float)):
                if str(got) != str(want):
                    failures.append("%s: %s = %r, wanted %r"
                                    % (case["name"], key, got, want))
            elif not isinstance(got, (int, float)) or isinstance(got, bool):
                failures.append("%s: %s = %r, wanted the number %r"
                                % (case["name"], key, got, want))
            elif float(got) != float(want):
                failures.append("%s: %s = %r, wanted %r"
                                % (case["name"], key, got, want))
        # A second needle for the same record. It is what proves a multiline
        # fold actually joined two lines: the first needle finds the record by
        # its opening line and this one asserts the continuation came with it.
        for needle in (case.get("message_also_contains") or []) \
                if isinstance(case.get("message_also_contains"), list) \
                else ([case["message_also_contains"]]
                      if case.get("message_also_contains") else []):
            if needle not in str(row.get("message", "")):
                failures.append("%s: the record does not also contain %r; the "
                                "continuation was not folded in"
                                % (case["name"], needle))
        for key in (case.get("absent") or []):
            if key in row:
                failures.append("%s: %s present (%r), wanted absent"
                                % (case["name"], key, row[key]))
    for fn, want in (expect.get("counts") or {}).items():
        got = len(records.get(fn, []))
        if got != want:
            failures.append("count %s = %d, wanted %d" % (fn, got, want))
    for fn, keys in (expect.get("required_keys") or {}).items():
        for i, row in enumerate(records.get(fn, [])):
            for key in keys:
                if key not in row:
                    failures.append("%s row %d missing %s (%r)"
                                    % (fn, i, key, row))
                    break
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="append", default=[],
                    help="fluent-bit image tag; repeat for each deployed version")
    ap.add_argument("--fixtures", default=FIXTURES)
    # An upper bound, not a fixed wait: the run stops as soon as the sink stops
    # growing. See run().
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--keep", default="")
    ap.add_argument("--journal", default="",
                    help="a captured journal directory, <dir>/<machine-id>/*.journal. "
                         "Runs the systemd input over real entries, which is the "
                         "only way this source can be tested off the farm.")
    ap.add_argument("--rotate", action="store_true",
                    help="rotate a tailed file the way logrotate does and "
                         "check the tail follows it without duplicating")
    ap.add_argument("--restart", action="store_true",
                    help="also stop, append a line, and start again on the "
                         "same storage tree")
    ap.add_argument("--dump", action="store_true",
                    help="print every record instead of checking expectations")
    args = ap.parse_args()
    versions = args.version or ["4.0.14"]

    log_root = os.path.join(args.fixtures, "logs")
    il_path = os.path.join(args.fixtures, "infologger.json")
    il_records = []
    if os.path.exists(il_path):
        with open(il_path) as fh:
            il_records = json.load(fh)
    expect_path = os.path.join(args.fixtures, "expect.yaml")
    expect = {}
    if os.path.exists(expect_path):
        with open(expect_path) as fh:
            expect = yaml.safe_load(fh) or {}

    status = 0
    for version in versions:
        records, logs, work = run(version, log_root, il_records, args.seconds,
                                  args.keep or None)
        total = sum(len(v) for v in records.values())
        print("== fluent-bit %s: %d records across %d outputs"
              % (version, total, len(records)))
        if args.dump:
            for fn in sorted(records):
                print("-- %s (%d)" % (fn, len(records[fn])))
                for row in records[fn]:
                    print("   " + json.dumps(row, sort_keys=True))
            continue
        failures = check(records, expect)
        for line in failures:
            print("   FAIL %s" % line)
        if failures:
            status = 1
            for line in logs.splitlines():
                if "error" in line.lower() or "warn" in line.lower():
                    print("   fluent-bit: %s" % line)
        else:
            print("   all %d expectations passed"
                  % (len(expect.get("cases", []))
                     + len(expect.get("counts") or {})))
        status |= dry_run(version, ["--journald", "on"],
                          "journal configuration")
        if args.journal:
            status |= journal_check(version, args.journal, args.seconds)
        if args.restart:
            status |= restart_check(version, log_root, args.seconds, work,
                                    records)
        # After the restart check, never before it. Restoring the rotated
        # fixture necessarily creates a new inode, and the restart check reuses
        # the first pass's tail database — so a rotation run first makes the
        # collector re-read the whole orchestrator fixture and the restart check
        # counts 13 records where it wants 1.
        if args.rotate:
            status |= rotation_check(version, log_root, args.seconds,
                                     tempfile.mkdtemp(
                                         prefix="rotate-",
                                         dir=os.path.join(
                                             os.path.expanduser("~"),
                                             ".cache", "alice-replaycheck")))
        shutil.rmtree(work, ignore_errors=True)
    return status


def restart_check(version, log_root, seconds, work, first):
    """Stop the collector, add a line, start it again on the same storage.

    Two things have to hold and neither is obvious from a single pass. Nothing
    already shipped may ship twice, because the tail database is what remembers
    where each file was read to. And a line written while the collector was down
    must still arrive, because a worker restarts and a run does not wait for it.
    """
    grown = os.path.join(log_root, "dds", "epn146.log")
    marker = "2026-06-20 23:59:59.999999   wrn    dds-agent            " \
             "<0x0:0x0>    written while the collector was stopped\n"
    with open(grown, "a") as fh:
        fh.write(marker)
    try:
        second, _, _ = run(version, log_root, [], seconds, None, work)
    finally:
        text = open(grown).read()
        open(grown, "w").write(text[:-len(marker)])

    before = sum(len(v) for v in first.values())
    after = sum(len(v) for v in second.values())
    added = [r for rows in second.values() for r in rows
             if "written while the collector was stopped"
             in str(r.get("message", ""))]
    problems = []
    if after != 1:
        problems.append("restart shipped %d records, wanted exactly the 1 new "
                        "line (%d in the first pass)" % (after, before))
    if len(added) != 1:
        problems.append("the line written during the outage arrived %d times"
                        % len(added))
    elif added[0].get("__tag__") != "family.central":
        problems.append("the new warning went to %s, not durable storage"
                        % added[0].get("__tag__"))
    for line in problems:
        print("   FAIL %s" % line)
    if not problems:
        print("   restart: no duplicates, and the line written during the "
              "outage arrived once")
    return 1 if problems else 0


def rotation_check(version, log_root, seconds, work):
    """Rotate a tailed file the way logrotate does, WHILE the collector runs.

    The restart check above proves the tail database survives a stop. Rotation
    is a different failure and it was untested: logrotate renames the file the
    tail holds open and the writer creates a new one under the OLD name. A tail
    that keeps following the renamed inode goes quiet for ever without an
    error, and a tail that treats the new file as unseen ships everything twice.

    The orchestrator's log is the one that actually rotates on the farm --
    daily, by date, with the collector reading the directory through a glob --
    so that is what is exercised.

    Three lines, in three phases. One before the rotation, one appended to the
    renamed file after it, and one in the NEW file. All three must arrive, each
    exactly once. The rename happens from inside the run, six seconds in, with
    the collector holding the original open.
    """
    if not os.path.isdir(os.path.join(log_root, "odc")):
        return 0
    # On a COPY of the fixture tree, never on the tracked one. This test renames
    # and rewrites the file it reads, and a run that dies in the middle left the
    # repository holding a fixture under the wrong name. Two of these running at
    # once did exactly that.
    log_root = os.path.join(work, "fixtures", "logs")
    shutil.copytree(FIXTURES, os.path.join(work, "fixtures"))
    odc_dir = os.path.join(log_root, "odc")
    live = os.path.join(odc_dir, "odc_2026-01-20.70.log")
    rotated = os.path.join(odc_dir, "odc_2026-01-19.69.log")
    fresh = os.path.join(odc_dir, "odc_2026-01-21.71.log")

    def line(text, when="2026-01-20 23:59:59.999999"):
        return ("%s err  odc-grpc-server     1091836 2zRVEg9xZRp:2304     %s\n"
                % (when, text))

    before = "the line before the rotation"
    after = "appended to the renamed file"
    new_file = "written to the file created after the rotation"

    def rotate():
        os.rename(live, rotated)
        with open(rotated, "a") as fh:
            fh.write(line(after))
        with open(fresh, "w") as fh:
            fh.write(line(new_file, "2026-01-21 00:00:01.000001"))

    def before_has_landed(out_dir):
        """The tail has read to the end of the file it is about to lose."""
        for name in os.listdir(out_dir):
            try:
                with open(os.path.join(out_dir, name), errors="replace") as fh:
                    if before in fh.read():
                        return True
            except OSError:
                continue
        return False

    problems = []
    with open(live, "a") as fh:
        fh.write(line(before))
    # The floor is set from the configuration rather than guessed: the
    # orchestrator tail sweeps its directory every 10 seconds and Fluent Bit
    # holds a rotated file for its own Rotate_Wait on top of that, so the new
    # file cannot be seen sooner than one sweep and may take two.
    records, _, _ = run(version, log_root, [], max(seconds, 60.0), None,
                        work, during=rotate, settle_after=35.0,
                        during_when=before_has_landed)

    rows = [r for rows in records.values() for r in rows]
    for needle in (before, after, new_file):
        hits = [r for r in rows if needle in str(r.get("message", ""))]
        if len(hits) != 1:
            problems.append("the line %r arrived %d times, wanted once"
                            % (needle, len(hits)))
    for text in problems:
        print("   FAIL %s" % text)
    if problems and version.startswith("5."):
        # Characterised, not mysterious. See docs/SOAK_RESULTS.md round 10: on
        # 5.0.8 this fails every time, on 3.2.8, 4.0.1 and 4.0.14 it passes
        # every time, and neither rotate_wait nor inotify_watcher changes it.
        # It is left failing on purpose. A version that loses data at rotation
        # must not be able to report green.
        print("   NOTE this is the known 5.x rotation loss: bytes appended to "
              "a file after it is renamed away never arrive. The farm runs "
              "3.2.8, 4.0.1 and 4.0.14, none of which lose them.")
    if not problems:
        print("   rotation: the line before, the append to the renamed file "
              "and the new file each arrived exactly once")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
