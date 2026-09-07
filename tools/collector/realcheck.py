#!/usr/bin/env python3
"""Put a large volume of real log lines through the production configuration.

`replaycheck.py` runs fixtures: a few dozen lines chosen because each one broke
something once. `coverage.py` runs millions of lines, but through Python's `re`
rather than through Fluent Bit. Neither answers the question an operator would
ask, which is whether the shipped configuration handles the farm's actual
traffic.

This runs real lines — from the S3 archive or captured from an EPN — through the
rendered production configuration in real Fluent Bit, and reports what came out
the other side. It is also the only check that compares the two instruments: if
Onigmo and Python disagree about which lines carry a severity, the coverage
figures in docs/SOAK_RESULTS.md rest on the wrong engine.
"""
import argparse
import collections
import gzip
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import coverage as cov  # noqa: E402
import replaycheck as rc  # noqa: E402

HTTP_PORT = 2023


# How many RECORDS a file of lines becomes. Three of the four tailed families
# fold continuations, so lines and records are different numbers and comparing
# the wrong one either passes when data was lost or fails when nothing was.
DATE_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s")


def expected_messages(family, lines, tag):
    """The message each record SHOULD carry, from the production cascade.

    Comparing totals is not a completeness check: one duplicate cancels one
    missing record and the run passes. What is compared instead is the multiset
    of record contents, so a loss and a duplication no longer hide each other.

    Two things have to be reproduced faithfully or the multiset is wrong for
    reasons that have nothing to do with the collector:

      * the tail folds continuations before any parser runs, so the corpus is
        folded here with the input's OWN multiline rule, read out of the
        rendered configuration.
      * Onigmo reads `(?m)` the way Ruby does -- the dot matches a newline --
        while Python reads it as "^ and $ match at line breaks". On a folded
        record the two disagree about where `(?<message>.*)$` ends. `(?s)` is
        the Python spelling of what Onigmo does.

    The result is checked against the collector's own output on the real
    corpora rather than trusted: see docs/SOAK_RESULTS.md round 11.
    """
    work = os.path.join(os.path.expanduser("~"), ".cache", "alice-coverage")
    os.makedirs(work, exist_ok=True)
    chain = [(name, re.compile(rx.pattern.replace("(?m)", "(?s)"), rx.flags))
             for name, rx in cov.cascade(tag, work)]
    fold = cov.multiline_rules(tag, work)
    wanted = collections.Counter()

    def emit(text):
        for _, rx in chain:
            match = rx.match(text)
            if match is None:
                continue
            message = match.groupdict().get("message")
            wanted[message if message is not None else text] += 1
            return
        # Nothing claimed it. The collector ships the text in `log`, and this
        # side compares against that.
        wanted[text] += 1

    pending = None
    for line in lines:
        if fold is not None:
            start, cont = fold
            if pending is not None and cont.match(line) and not start.match(line):
                pending += "\n" + line
                continue
            held, pending = pending, line
            if held is None:
                continue
            line = held
        emit(line)
    if pending is not None:
        emit(pending)
    return wanted


def record_starts(family, lines):
    if family in ("dds", "odc"):
        return sum(1 for line in lines if DATE_START.match(line))
    if family == "ildaemon":
        return len(lines)          # no multiline parser on that input
    return sum(1 for line in lines if line[:1] not in (" ", "\t"))


def lay_out(family, lines, log_root, form):
    """Write the lines where the production tail pattern will find them."""
    if family == "dds":
        target = os.path.join(log_root, "dds", "epn146.log")
    elif family == "ildaemon":
        target = os.path.join(log_root, "varlog", "o2-infologger-daemon.log")
    elif family == "odc":
        target = os.path.join(log_root, "odc", "odc_2026-01-20.70.log")
    else:
        target = os.path.join(
            log_root, "stdout", "epn146",
            "realcheck_reco1_2026-06-20-12-15-21_1_out.log")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    written = 0
    with open(target, "w") as out:
        for line in lines:
            if family in ("dds", "ildaemon", "odc") or form == "live" \
                    or line[:1] in (" ", "\t"):
                out.write(line + "\n")
            else:
                out.write("2026-06-20 12:15:25.000001 " + line + "\n")
            written += 1
    return written


def feed_tcp(records, port, rate):
    """Speak InfoLogger records to the tcp input the way infoLoggerD does.

    InfoLogger is the one source that does not arrive from a file, so the
    file-based layout above cannot reach it and it was the one family with no
    real-traffic check. Paced, because an unpaced flood measures the socket
    rather than the parser.
    """
    end = time.time() + 60
    sock = None
    while time.time() < end:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            break
        except OSError:
            time.sleep(0.4)
    if sock is None:
        raise SystemExit("the infologger tcp input never accepted a connection")
    sent = 0
    interval = 1.0 / rate if rate > 0 else 0.0
    with sock:
        for record in records:
            sock.sendall((json.dumps(record) + "\n").encode())
            sent += 1
            if interval:
                time.sleep(interval)
    return sent


def infologger_records(path, limit):
    """Real InfoLogger rows, out of a real archive dump.

    An earlier build synthesised this: it took the message text from the corpus
    and filled the other fifteen columns with plausible constants, cycling four
    severities in turn. That exercises the mapping and it does NOT exercise the
    records production actually carries -- every timestamp was a millisecond
    apart, every run was the same number, and no field ever held the NULL or the
    embedded quote that a real dump is full of.

    The rows come out of the archive through replay.py's own reader, imported
    rather than restated, so what is fed here is byte-for-byte what the replay
    service sends. `path` is an InfoLogger dump object -- a gzip'd mysqldump, or
    a tar holding one -- exactly as it sits in the bucket.
    """
    sys.path.insert(0, os.path.join(REPO, "images", "replay"))
    if "boto3" not in sys.modules:
        # replay.py imports boto3 at module scope because in production it
        # always streams from the bucket. Only its dump PARSER is wanted here,
        # and installing an S3 client library to read a file already on disk
        # would be the wrong dependency. The stub is enough to import the
        # module; anything that actually reaches S3 fails loudly rather than
        # quietly doing something else.
        import types
        stub = types.ModuleType("boto3")

        def _no_s3(*args, **kwargs):
            raise RuntimeError("realcheck reads a dump from disk and has no "
                               "S3 client; install boto3 to stream instead")

        stub.client = _no_s3
        stub.Session = _no_s3
        sys.modules["boto3"] = stub
        botocore = types.ModuleType("botocore")
        config = types.ModuleType("botocore.config")
        config.Config = lambda *args, **kwargs: None
        botocore.config = config
        sys.modules["botocore"] = botocore
        sys.modules["botocore.config"] = config
    import replay

    out = []
    for line in _dump_lines(path):
        if not line.startswith("INSERT INTO"):
            continue
        for vals in replay.parse_insert_rows(line):
            if len(vals) != len(replay.IL_COLUMNS):
                continue
            out.append(dict(zip(replay.IL_COLUMNS, vals)))
            if limit and len(out) >= limit:
                return out
    return out


def _dump_lines(path):
    """Lines of a dump object, whether it is a bare gzip or a gzip'd tar.

    The bucket holds both shapes and which one a given partition is stored as is
    not something this has any business knowing.
    """
    with gzip.open(path, "rb") as fh:
        head = fh.read(512)
    is_tar = len(head) >= 265 and head[257:262] == b"ustar"
    if is_tar:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                for raw in handle:
                    yield raw.decode("utf-8", "replace")
        return
    with gzip.open(path, "rt", errors="replace") as fh:
        for raw in fh:
            yield raw


def synthetic_infologger_records(path, limit):
    """The old path, kept and renamed so nothing calls it by accident.

    Message text from a corpus with the other fifteen columns invented. It is
    still useful for a mapping check when no dump is at hand, and it must never
    again be described as a test against real records.
    """
    out = []
    severities = ["I", "W", "E", "D"]
    with open(path, errors="replace") as fh:
        for n, line in enumerate(fh):
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) != 3 or parts[0] != "infologger":
                continue
            system, _, facility = parts[1].partition("/")
            out.append({
                "severity": severities[n % len(severities)],
                "level": 11,
                "timestamp": 1750414525.331 + n * 0.001,
                "hostname": "epn146", "rolename": "epn", "pid": 1512832,
                "username": "epn", "system": system or "?",
                "facility": facility or "?", "detector": "MFT",
                "partition": "phys_1", "run": 552154, "errcode": 0,
                "errline": 0, "errsource": "",
                "message": parts[2].replace("\\t", "\t"),
            })
            if limit and len(out) >= limit:
                break
    return out


def read_family(path, family, limit):
    """Lines of one family out of a corpus, or out of a plain text file."""
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if "\t" in line:
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    if parts[0] != family:
                        continue
                    line = parts[2].replace("\\t", "\t")
            if not line:
                continue
            out.append(line)
            if limit and len(out) >= limit:
                break
    return out


def metrics():
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/v1/metrics" % HTTP_PORT, timeout=3) as r:
        return json.load(r)


def run(version, log_root, expected, timeout, il_records=(), tcp_rate=2000):
    root = os.path.join(os.path.expanduser("~"), ".cache", "alice-realcheck")
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run-", dir=root)
    config_dir = os.path.join(work, "cfg")
    out_dir = os.path.join(work, "out")
    storage = os.path.join(work, "storage")
    for path in (config_dir, out_dir, storage):
        os.makedirs(path)
    extra = []
    if os.path.isdir(os.path.join(log_root, "odc")):
        extra = ["--odc", "on", "--odc-path", "/logs/odc/*.log"]
    rc.render(config_dir, extra)
    ildaemon = os.path.join(log_root, "varlog", "o2-infologger-daemon.log")
    mounts = []
    if os.path.exists(ildaemon):
        mounts = ["-v", "%s:/var/log/o2-infologger-daemon.log:ro" % ildaemon]
    name = "realcheck-%d" % os.getpid()
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run([
        "docker", "run", "-d", "--name", name,
        "-p", "%d:2020" % HTTP_PORT,
        "-v", "%s:/etc/fluent-bit:ro" % config_dir,
        "-v", "%s:/logs:ro" % log_root,
        "-v", "%s:/out" % out_dir,
        "-v", "%s:/storage" % storage,
        "-e", "ALICE_NODE_ID=node-01", "-e", "ALICE_LOG_ROOT=/logs",
        "-e", "ALICE_FB_STORAGE_PATH=/storage",
        "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0", "-e", "ALICE_FB_HTTP_PORT=2020",
        "-e", "ALICE_INFOLOGGER_TCP_PORT=5172", "-e", "ALICE_OS_HTTP_PORT=9200",
        "-p", "5172:5172",
    ] + mounts + [
        "fluent/fluent-bit:%s" % version,
        "/fluent-bit/bin/fluent-bit", "-c", "/etc/fluent-bit/collector.yaml",
    ], check=True, capture_output=True)
    try:
        if il_records:
            for _ in range(240):
                try:
                    metrics()
                    break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.25)
            feed_tcp(il_records, 5172, tcp_rate)
        deadline = time.time() + timeout
        stable = 0
        last = -1
        while time.time() < deadline:
            time.sleep(1.0)
            size = sum(os.path.getsize(os.path.join(out_dir, f))
                       for f in os.listdir(out_dir)) if os.listdir(out_dir) else 0
            stable = stable + 1 if size and size == last else 0
            last = size
            # Longer than it looks like it needs to be, on purpose. The tail's
            # multiline parser holds the final record until its flush timeout
            # expires, so the sink goes quiet with one record still inside the
            # engine. The content check found exactly that: the last line of a
            # 190,000-line corpus, missing, once.
            if stable >= 12:
                break
    finally:
        subprocess.run(["docker", "stop", "-t", "10", name],
                       capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, check=False)

    records = []
    for fn in sorted(os.listdir(out_dir)):
        with open(os.path.join(out_dir, fn), errors="replace") as fh:
            for line in fh:
                head, sep, body = line.partition(": ")
                if not sep:
                    continue
                try:
                    _, record = json.loads(body)
                except (ValueError, TypeError):
                    continue
                record["__tag__"] = head
                records.append(record)
    shutil.rmtree(work, ignore_errors=True)
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--family", required=True)
    ap.add_argument("--lines", type=int, default=200000)
    ap.add_argument("--version", default="4.0.14")
    ap.add_argument("--input-form", default="replay",
                    choices=["replay", "live"])
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--tcp-rate", type=float, default=2000.0,
                    help="infologger records a second over the socket")
    ap.add_argument("--synthetic-infologger", action="store_true",
                    help="take the message text from a corpus and invent the "
                         "other fifteen columns. Exercises the mapping, proves "
                         "nothing about real records, and says so in the output.")
    args = ap.parse_args()

    il_records = []
    if args.family == "infologger":
        il_records = (synthetic_infologger_records(args.corpus, args.lines)
                      if args.synthetic_infologger
                      else infologger_records(args.corpus, args.lines))
        if not il_records:
            print("no infologger lines in %s" % args.corpus)
            return 1
        lines = [r["message"] for r in il_records]
    else:
        lines = read_family(args.corpus, args.family, args.lines)
        if not lines:
            print("no %s lines in %s" % (args.family, args.corpus))
            return 1

    root = os.path.join(os.path.expanduser("~"), ".cache", "alice-realcheck-logs")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    if il_records:
        written = expected = len(il_records)
    else:
        written = lay_out(args.family, lines, root, args.input_form)
        expected = record_starts(args.family, lines)
    provenance = "real lines"
    if args.family == "infologger":
        provenance = ("SYNTHETIC records (message text real, other columns "
                      "invented)" if args.synthetic_infologger
                      else "real archive rows, all sixteen columns")
    print("%s: %d %s, fluent-bit %s, %s form"
          % (args.family, written, provenance, args.version, args.input_form))

    tag = ("stdout" if args.family in ("dpl", "datadist", "stdout")
           else args.family)
    wanted = None
    if not il_records:
        try:
            wanted = expected_messages(args.family, lines, tag)
        except Exception as exc:                              # noqa: BLE001
            print("could not derive the expected records: %s" % exc)
    else:
        wanted = collections.Counter(str(r["message"]) for r in il_records)

    records = run(args.version, root, written, args.timeout, il_records,
                  args.tcp_rate)
    # The collector's own health records share the sink and are not part of the
    # corpus, so they must not be counted as arrivals of it.
    records = [r for r in records if r.get("__tag__") != "health"]
    severities = collections.Counter()
    tags = collections.Counter()
    programs = set()
    unparsed = 0
    for record in records:
        severities[record.get("severity") or "<none>"] += 1
        tags[record["__tag__"]] += 1
        if record.get("program"):
            programs.add(record["program"])
        if "log" in record:
            unparsed += 1
    total = len(records)
    classified = total - severities["<none>"]

    # Completeness, before any percentage. Every figure below is computed over
    # the records that ARRIVED, so a run that lost half the corpus can still
    # report 100 % severity recovery and 0 % unclassified. That is exactly what
    # this check exists to catch, and without it the success condition could not
    # see data loss at all.
    complete = total == expected
    print("\nfluent-bit produced %d records from %d lines; %d expected"
          % (total, written, expected))
    if not complete:
        print("INCOMPLETE: %d records %s. Every percentage below is over the "
              "records that arrived and cannot be read as coverage."
              % (abs(total - expected),
                 "missing" if total < expected else "more than expected"))

    # Totals are not identities. One duplicate cancels one missing record, so a
    # run that lost the second record and shipped the first twice used to
    # report a matching total and pass. What is compared here is the multiset of
    # record contents.
    if wanted is not None:
        # The one normalisation, and it is an artefact of the corpus rather
        # than of the collector: a record read from a file keeps the newline
        # that ended it, and the corpus lines have already been stripped of
        # theirs. Only the TRAILING newline is removed -- the ones inside a
        # folded record are content and are compared.
        seen = collections.Counter(
            str(r.get("message", r.get("log", ""))).rstrip("\n")
            for r in records)
        missing = wanted - seen
        extra = seen - wanted
        if missing or extra:
            complete = False
            print("CONTENT MISMATCH: %d record(s) missing, %d unexpected"
                  % (sum(missing.values()), sum(extra.values())))
            for text, n in list(missing.items())[:3]:
                print("   missing x%d: %s" % (n, text[:110]))
            for text, n in list(extra.items())[:3]:
                print("   unexpected x%d: %s" % (n, text[:110]))
        else:
            print("content: every record arrived exactly once, compared by "
                  "message rather than by count")
    print("severity recovered on %d, %.2f %%"
          % (classified, 100.0 * classified / max(total, 1)))
    print("no parser claimed %d, %.2f %%"
          % (unparsed, 100.0 * unparsed / max(total, 1)))
    print("routing: %s" % dict(tags))
    print("severities: %s" % dict(severities.most_common(12)))
    if programs:
        print("programs seen: %d" % len(programs))

    # What the extractors actually pulled out of real traffic. A parser that
    # matches every line and extracts nothing from any of them is the failure
    # this catches: the daemon log parsed cleanly for months while 36 % of it
    # was losing the only number on it.
    extracted = collections.Counter()
    for record in records:
        for field in ("clients", "client_limit", "slot_id", "channel_id",
                      "channel", "task", "bc", "orbit", "feeid", "chip",
                      "decoder_error", "comm", "log_time", "program",
                      "partition", "run", "pid"):
            if record.get(field) is not None:
                extracted[field] += 1
    if extracted:
        print("fields extracted from real traffic:")
        for field, n in extracted.most_common():
            print("   %-14s %8d  %6.2f %%" % (field, n, 100.0 * n / max(total, 1)))

    # InfoLogger arrives as JSON with sixteen named fields and has no regex
    # cascade at all, so there is no Python figure to compare against and the
    # comparison below would read 0 % against 100 % and mean nothing.
    if args.family == "infologger":
        print("\nno engine comparison: InfoLogger is parsed as JSON, not by a "
              "regex cascade, so there are no two engines to disagree")
        return 0 if complete else 1

    # The cross-check that matters: coverage.py scores the same lines with
    # Python's `re`. If the two engines disagree, every coverage figure in
    # docs/SOAK_RESULTS.md was measured on the wrong one.
    work = os.path.join(os.path.expanduser("~"), ".cache", "alice-coverage")
    os.makedirs(work, exist_ok=True)
    tag = "stdout" if args.family in ("dpl", "datadist", "stdout") else args.family
    try:
        chain = cov.cascade(tag, work)
    except Exception as exc:                                  # noqa: BLE001
        print("could not build the python cascade: %s" % exc)
        return 0 if complete else 1
    py_classified = 0
    for line in lines:
        for _, rx in chain:
            match = rx.match(line)
            if match:
                if match.groupdict().get("severity"):
                    py_classified += 1
                break
    py_share = 100.0 * py_classified / max(len(lines), 1)
    fb_share = 100.0 * classified / max(total, 1)
    print("\npython re says %.2f %%, onigmo says %.2f %%, difference %.2f points"
          % (py_share, fb_share, abs(py_share - fb_share)))
    if abs(py_share - fb_share) > 1.0:
        print("FAIL the two engines disagree by more than a point; the "
              "coverage figures rest on the wrong one")
        return 1
    print("the two engines agree")
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
