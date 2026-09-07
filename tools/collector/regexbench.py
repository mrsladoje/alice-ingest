#!/usr/bin/env python3
"""What the collector's regexes cost, measured through Fluent Bit.

Round 6 optimised the mining masker against Python's `re`, where a pattern is
fast when its first opcode is a literal, because that is what enables the
character-skip loop. None of that carries over here. Fluent Bit does not use
Python; it uses Onigmo, with its own optimiser and its own idea of what a cheap
pattern is. So a collector regex is measured here, in the engine that runs it.

Method: one container, one core, a null sink, and a file of real log lines.

What is reported is the container's own cumulative processor time, read from
the engine's cgroup accounting, not the wall-clock time the run took. Those are
different quantities and an earlier build reported the second under the name of
the first. On a laptop that is running other things, wall-clock includes every
moment the container was runnable and not running, which is exactly the noise
the arms are trying to see through.

The run ends when every record has been READ and every record has been WRITTEN.
Waiting on the input counter alone stops the clock while filter and output work
for the last chunks is still queued, so part of the cost being measured lands
after the measurement. Each arm runs several times, interleaved with the
others.

The baseline arm is exactly what the production renderer emits. Every other arm
is that same output with one thing changed, so a difference is attributable.
"""
import argparse
import http.client
import json
import os
import socket
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MKCONFIG = os.path.join(REPO, "tools", "soak", "mkconfig.py")
ROOT = os.path.join(os.path.expanduser("~"), ".cache", "alice-regexbench")
METRICS_PORT = 2021
DDS_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s")
# How long every counter has to stand still before the pipeline counts as
# drained. Longer than one flush interval, which the runs set to 1 second. The
# wait costs no processor time, which is the whole reason processor time can
# afford a settle window and wall-clock could not.
SETTLE_SECONDS = 2.0


def render(config_dir, extra):
    subprocess.run(
        [sys.executable, MKCONFIG,
         "--out", os.path.join(config_dir, "collector.yaml"),
         "--parsers-out", os.path.join(config_dir, "parsers.yaml"),
         "--sink", "null", "--flush", "1"] + extra,
        check=True, capture_output=True)


def edit_parser(config_dir, name, pattern):
    path = os.path.join(config_dir, "parsers.yaml")
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    found = False
    for item in doc["parsers"]:
        if item.get("name") == name:
            item["regex"] = pattern
            found = True
    if not found:
        raise SystemExit("no parser named %r to edit" % name)
    with open(path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, width=4096)


def reorder_cascade(config_dir, tag, order):
    """Reorder the body parsers on one tag, in place, as text.

    This used to load the YAML, reorder, and dump it. That silently destroyed
    the configuration: a rewrite_tag filter carries several `rule:` keys in one
    mapping, and a YAML load keeps only the last of them, so the arm ran with a
    router that had lost its rules. The arm looked slow for reasons that had
    nothing to do with parser order. Moving whole text blocks keeps every
    duplicate key exactly as the renderer wrote it.
    """
    path = os.path.join(config_dir, "collector.yaml")
    with open(path) as fh:
        lines = fh.readlines()

    blocks = {}
    order_seen = []
    start = None
    name = None
    for index, line in enumerate(lines + ["- name: __end__\n"]):
        if line.startswith("  - name: parser"):
            if start is not None and name:
                blocks[name] = (start, index)
                order_seen.append(name)
            start, name = index, None
        elif line.startswith("  - name:") or line.startswith("  outputs:"):
            if start is not None and name:
                blocks[name] = (start, index)
                order_seen.append(name)
            start, name = None, None
        elif start is not None:
            if line.strip() == "match: %s" % tag:
                name = name or "?"
            elif line.strip().startswith("parser: ") and name == "?":
                name = line.strip().split(None, 1)[1]
            elif line.strip().startswith("key_name: ") and \
                    line.strip() != "key_name: log":
                name = None if name == "?" else name
    picked = [n for n in order if n in blocks]
    if len(picked) != len(order):
        raise SystemExit("could not find %s on tag %r; found %s"
                         % (order, tag, sorted(blocks)))
    spans = sorted(blocks[n] for n in picked)
    body = []
    for n in order:
        a, b = blocks[n]
        body.extend(lines[a:b])
    out = lines[:spans[0][0]] + body + lines[spans[-1][1]:]
    with open(path, "w") as fh:
        fh.writelines(out)
    return

    with open(path) as fh:
        doc = yaml.safe_load(fh)
    body = [f for f in doc["pipeline"]["filters"]
            if f.get("name") == "parser" and f.get("match") == tag
            and f.get("key_name") == "log"]
    if not body:
        raise SystemExit("no body parsers on tag %r" % tag)
    by_name = {f["parser"]: f for f in body}
    first = doc["pipeline"]["filters"].index(body[0])
    for f in body:
        doc["pipeline"]["filters"].remove(f)
    for offset, name in enumerate(order):
        doc["pipeline"]["filters"].insert(first + offset, by_name[name])
    with open(path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, width=4096)


def metrics():
    url = "http://127.0.0.1:%d/api/v1/metrics" % METRICS_PORT
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


# A rewrite_tag emitter is reported as an input. `stdout_router` re-injects
# every record the cascade routed, so the input total for a 60,000-line file is
# about 120,000 and a run that stopped at "input records reached 60,000"
# stopped roughly halfway through the work it was timing.
SOURCE_INPUT = re.compile(r"^(?:tail|systemd|tcp|exec|storage_backlog)\.\d+$")


def source_records(doc):
    """Records read from a real source, with the routers' re-injections excluded."""
    return sum(v.get("records", 0) for name, v in doc.get("input", {}).items()
               if SOURCE_INPUT.match(name))


def input_records(doc):
    return sum(v.get("records", 0) for v in doc.get("input", {}).values())


def output_records(doc):
    return sum(v.get("proc_records", 0) for v in doc.get("output", {}).values())


def docker_socket():
    """Where the engine listens, from the active docker context.

    Reading cumulative processor time needs the engine API; the CLI exposes only
    an instantaneous percentage. There is no cgroup file to read on this host,
    because the container's cgroup lives inside the Linux virtual machine.
    """
    override = os.environ.get("DOCKER_HOST", "")
    if override.startswith("unix://"):
        return override[len("unix://"):]
    out = subprocess.run(["docker", "context", "inspect"],
                         capture_output=True, text=True)
    if out.returncode == 0:
        for entry in json.loads(out.stdout):
            host = entry.get("Endpoints", {}).get("docker", {}).get("Host", "")
            if host.startswith("unix://"):
                return host[len("unix://"):]
    return "/var/run/docker.sock"


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(self._path)
        self.sock = sock


def container_cpu_seconds(name, socket_path):
    """Cumulative processor time the container has used, in seconds.

    user plus system, over every thread, since the container started. This is
    the number the arms are compared on.
    """
    conn = _UnixHTTP(socket_path)
    try:
        conn.request("GET", "/containers/%s/stats?stream=false&one-shot=true"
                     % name)
        doc = json.load(conn.getresponse())
    finally:
        conn.close()
    usage = doc.get("cpu_stats", {}).get("cpu_usage", {})
    total = usage.get("total_usage")
    if total is None:
        raise SystemExit("the engine reported no cpu accounting for %s" % name)
    return total / 1e9


def clear(path):
    """Empty a directory without removing it.

    Removing and recreating a directory Colima has bind-mounted leaves the
    container with a mount that no longer resolves, and Fluent Bit fails with
    "cannot create stream path". Emptying it in place does not.
    """
    os.makedirs(path, exist_ok=True)
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            os.remove(full)


def one_run(version, config_dir, log_root, expected, timeout, socket_path):
    name = "regexbench-%d" % os.getpid()
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    # The published port is not free the instant the container is gone.
    time.sleep(1.0)
    storage = os.path.join(config_dir, "storage")
    clear(storage)
    subprocess.run([
        "docker", "run", "-d", "--name", name, "--cpus", "1",
        "-p", "%d:2020" % METRICS_PORT,
        "-v", "%s:/etc/fluent-bit:ro" % config_dir,
        "-v", "%s:/logs:ro" % log_root,
        "-v", "%s:/storage" % os.path.abspath(storage),
        "-e", "ALICE_NODE_ID=node-01", "-e", "ALICE_LOG_ROOT=/logs",
        "-e", "ALICE_FB_STORAGE_PATH=/storage",
        "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0", "-e", "ALICE_FB_HTTP_PORT=2020",
        "-e", "ALICE_INFOLOGGER_TCP_PORT=5171", "-e", "ALICE_OS_HTTP_PORT=9200",
        "fluent/fluent-bit:%s" % version,
        "/fluent-bit/bin/fluent-bit", "-c", "/etc/fluent-bit/collector.yaml",
    ], check=True, capture_output=True)
    try:
        deadline = time.time() + timeout
        ready = False
        for _ in range(200):
            try:
                metrics()
                ready = True
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        if not ready:
            logs = subprocess.run(["docker", "logs", name],
                                  capture_output=True, text=True)
            raise SystemExit("fluent-bit never served metrics\n%s"
                             % (logs.stderr or logs.stdout)[-2000:])
        # The clock starts after readiness, so container start-up and the
        # engine's own initialisation are outside every arm equally.
        cpu_start = container_cpu_seconds(name, socket_path)
        start = time.time()
        seen = 0
        settled_since = None
        last = None
        while time.time() < deadline:
            try:
                doc = metrics()
                seen = source_records(doc)
                progress = (seen, input_records(doc), output_records(doc))
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.05)
                continue
            # Reading the file is only the first part of the work. The cascade,
            # the routers and the sink all run downstream of the tail, so the
            # run is over when nothing is moving any more, not when the tail
            # has finished. Quiescence rather than arithmetic: records are
            # dropped by the routers and re-emitted under new tags, so no
            # counter equality holds across the whole pipeline.
            if seen >= expected and progress == last:
                if settled_since is None:
                    settled_since = time.time()
                elif time.time() - settled_since >= SETTLE_SECONDS:
                    cpu = container_cpu_seconds(name, socket_path) - cpu_start
                    return cpu, time.time() - start, seen
            else:
                settled_since = None
            last = progress
            time.sleep(0.05)
        return None, None, seen
    finally:
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, check=False)


def build_input(source, lines, log_root, family, form):
    if family == "dds":
        target = os.path.join(log_root, "dds", "epn146.log")
        stamp = "2026-06-20 12:15:12.942587   inf    dds-agent            " \
                "<0x001f6455:0x000014ffab8a1780>    "
    elif family == "odc":
        # The orchestrator's corpus already carries its full envelope, the same
        # way the DDS one does, so nothing is prepended.
        target = os.path.join(log_root, "odc", "odc_2026-01-20.70.log")
        stamp = None
    else:
        target = os.path.join(
            log_root, "stdout", "epn146",
            "bench_reco1_2026-06-20-12-15-21_1_out.log")
        stamp = None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    written = 0
    # How many RECORDS the input will produce, which is not how many lines are
    # written. Both tails fold continuations, so a file of 200,000 lines becomes
    # fewer records and a run waiting for 200,000 of them waits for ever. The
    # first build hid that behind the routers' re-injections, which pushed the
    # input total past the line count for a reason that had nothing to do with
    # the file.
    starts = 0
    with open(source, errors="replace") as src, open(target, "w") as out:
        for line in src:
            line = line.rstrip("\n")
            if not line:
                continue
            written += 1
            if family in ("dds", "odc"):
                starts += 1 if DDS_RECORD_START.match(line) else 0
            else:
                starts += 1 if line[:1] not in (" ", "\t") else 0
            if family in ("dds", "odc"):
                # Both corpora already carry their full envelope.
                out.write(line + "\n")
            else:
                # The tree the collector reads has a replay date in front of a
                # record-start line; a continuation keeps its indent. A live EPN
                # tail has no prefix, which is what `live` reproduces.
                if form == "live" or line[:1] in (" ", "\t"):
                    out.write(line + "\n")
                else:
                    out.write("2026-06-20 12:15:25.000001 " + line + "\n")
            if written >= lines:
                break
    return target, written, starts


ARMS = {
    "shipped": {},
    # Byte-for-byte the shipped configuration, run under a second name. Whatever
    # difference this arm shows against `shipped` is the instrument and the host,
    # not the configuration, and no smaller difference anywhere else in a table
    # means anything.
    "control": {},
    "no-ansi": {"edit": ("dpl", r'(?m)^(?:(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) )?\[(?<log_time>\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\[(?<severity>[A-Z]+)\]\s?(?<message>.*)$')},
    # There was a "no-replay-prefix" arm here that deleted the optional leading
    # date from the dpl regex while the input still carried one. It read 33 %
    # cheaper, and the reading was worthless: the regex then failed on every
    # line and the work it skipped was the parse itself. The honest form of that
    # question is --input-form live, which changes the input rather than
    # breaking the parser.
    "dpl-first": {"order": ("stdout", ["dpl", "datadist", "dpl_noclock", "stdout_root"])},
    "no-dpl-extractors": {"flags": ["--dpl-extractors", ""]},
    # Same match set, different leading literal. "error" begins with the
    # commonest letter in English and "bc/orbit" does not, so this asks whether
    # Onigmo is restarting a match attempt at every 'e' in every line.
    "dpl-rare-literal": {"edit": ("mft_decoder_error", r'bc/orbit (?<bc>\d+)/(?<orbit>\d+) on the FEEID:(?<feeid>0x[0-9a-fA-F]+) chip#(?<chip>-?\d+): (?<decoder_error>.*)')},
    "dpl-anchor-new": {"edit": ("mft_decoder_error", r'New error registered at bc/orbit (?<bc>\d+)/(?<orbit>\d+) on the FEEID:(?<feeid>0x[0-9a-fA-F]+) chip#(?<chip>-?\d+): (?<decoder_error>.*)')},
    "one-dpl-extractor": {"flags": ["--dpl-extractors", "mft_decoder_error"]},
    "no-dds-extractors": {"flags": ["--dds-extractors", ""]},
    # The orchestrator's partition and run number are an optional group inside
    # the one regex that reads the line. This is what dropping them would save.
    "odc-no-partition": {"edit": ("odc", r'(?m)^(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(?<severity>[a-z]{3})\s+(?<program>\S+)\s+(?<pid>\d+)\s+(?<message>.*)$')},
    # The rejected alternative from the census: a partition of six characters or
    # more. It would lose the real `test0` and `idd` partitions, so it is not a
    # candidate on correctness; this prices it anyway, so the choice rests on
    # correctness rather than on an unmeasured cost claim.
    "odc-long-partition": {"edit": ("odc", r'(?m)^(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(?<severity>[a-z]{3})\s+(?<program>\S+)\s+(?<pid>\d+)\s+(?:(?<partition>[A-Za-z0-9]{6,}):(?<run>\d+)\s+)?(?<message>.*)$')},
    # What dds_task looked like before the capture was narrowed to the binary.
    "dds-task-whole-line": {"edit": ("dds_task", r'Executing user task:\s+(?<task>.*)')},
    "one-dds-parser": {"flags": ["--dds-extractors", "dds_slot"]},
    "two-dds-parsers": {"flags": ["--dds-extractors", "dds_slot,dds_channel"]},
    "dds-task-only": {"flags": ["--dds-extractors", "dds_task"]},
    "dds-channel-only": {"flags": ["--dds-extractors", "dds_channel"]},
    "one-dds-extractor": {
        "flags": ["--dds-extractors", "dds_slot"],
        "edit": ("dds_slot", r'(?:ADD SLOT request,\s+id\s+=\s+(?<slot_id>\d+)|Added shared memory channel output with ID:\s+(?<channel_id>\d+)\s+name:\s+(?<channel>\S+)|Executing user task:\s+(?<task>.*))'),
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--family", default="stdout",
                    choices=["stdout", "dds", "odc"])
    ap.add_argument("--lines", type=int, default=400000)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--version", default="4.0.14")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--input-form", default="replay", choices=["replay", "live"],
                    help="replay puts a full event date in front of every "
                         "record-start line, the way the replay engine does. "
                         "live is what a tail on a real EPN sees.")
    ap.add_argument("--arm", action="append", default=[])
    args = ap.parse_args()
    arms = args.arm or list(ARMS)

    shutil.rmtree(ROOT, ignore_errors=True)
    log_root = os.path.join(ROOT, "logs")
    os.makedirs(log_root)
    _, written, expected = build_input(args.corpus, args.lines, log_root,
                                       args.family, args.input_form)
    socket_path = docker_socket()
    print("%s (%s form): %d lines folding to %d records, fluent-bit %s, one "
          "core, null sink, %d interleaved rounds, processor time from the "
          "engine's cgroup accounting"
          % (args.family, args.input_form, written, expected, args.version,
             args.repeat))

    config_dirs = {}
    for arm in arms:
        spec = ARMS[arm]
        config_dir = os.path.join(ROOT, "cfg-%s" % arm)
        os.makedirs(config_dir, exist_ok=True)
        flags = list(spec.get("flags", []))
        if args.family == "odc":
            flags += ["--odc", "on", "--odc-path", "/logs/odc/*.log"]
        render(config_dir, flags)
        if "edit" in spec:
            edit_parser(config_dir, *spec["edit"])
        if "order" in spec:
            reorder_cascade(config_dir, *spec["order"])
        config_dirs[arm] = config_dir

    # Interleaved, one round of every arm at a time, not all repeats of one arm
    # and then the next. This host drifts between two performance states on the
    # timescale of a single arm, and running arms in blocks turned that drift
    # into a 17 % "cost" that a later run could not reproduce. Round-robin makes
    # drift hit every arm equally.
    cpus = {arm: [] for arm in arms}
    walls = {arm: [] for arm in arms}
    for round_index in range(args.repeat):
        for arm in arms:
            cpu, elapsed, seen = one_run(args.version, config_dirs[arm],
                                         log_root, expected, args.timeout,
                                         socket_path)
            if cpu is None:
                print("  %-20s TIMED OUT after %d of %d records"
                      % (arm, seen, expected))
                continue
            cpus[arm].append(cpu)
            walls[arm].append(elapsed)

    results = {}
    print("  %-20s %9s %10s %10s %8s   %s"
          % ("arm", "min cpu-s", "med cpu-s", "cpu-s/M", "wall s", "cpu runs"))
    for arm in arms:
        if not cpus[arm]:
            continue
        # The minimum is the least-contended observation and is the better
        # estimator on a host that is not idle. The median is kept beside it so
        # a wide spread is visible rather than hidden.
        best = min(cpus[arm])
        results[arm] = best
        print("  %-20s %9.2f %10.2f %10.2f %8.2f   (%s)"
              % (arm, best, statistics.median(cpus[arm]),
                 best * 1e6 / expected, min(walls[arm]),
                 ", ".join("%.2f" % t for t in cpus[arm])))

    if "shipped" in results:
        print("\nagainst the shipped configuration, on the minimum "
              "processor time")
        for arm, best in sorted(results.items(), key=lambda kv: kv[1]):
            if arm == "shipped":
                continue
            change = 100.0 * (best - results["shipped"]) / results["shipped"]
            print("  %-20s %+6.1f %%" % (arm, change))
    return 0


if __name__ == "__main__":
    sys.exit(main())
