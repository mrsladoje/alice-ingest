"""Flight-2 replay engine — stream REAL CERN logs from s3://epn-backup-logs/
back through the SAME collector path the mock producers feed.

Design (decided with the user):
  * Transform -> existing shapes: we reshape real S3 records into the exact
    on-wire formats the Fluent Bit collector already parses. The collector and
    its parsers stay untouched; all real->shape mapping lives here.
      - InfoLogger: gzip'd mysqldump -> parse extended-INSERT rows in Python ->
        16-field JSON over TCP to the collector's `tcp` input (:5170), exactly
        like infologger_producer.py. Event-time = the real `timestamp` column
        (the collector's lua+parser promote it to @timestamp).
      - DDS: each run tarball holds one `dds_<date>.N.log` firehose whose line
        format is byte-identical to what the collector's dds_text parser eats.
        We stream the tar (r|gz), read ONLY that first member, and write its
        lines into a PER-NODE tail file /var/log/node/dds/<host>.log so the
        collector's tail (with path_key) can tag each record with its EPN host.
      - The InfoLogger daemon log, the journal and the run orchestrator's log
        have no S3 archive behind them: they are written on a live EPN and
        nothing backs them up. They come from a bundle captured with
        tools/epnsurvey/mkbundle.sh and are replayed out of REPLAY_BUNDLE_ROOT.
        The daemon log and the orchestrator log are PACED into the paths the
        collector tails; the journal is replayed BY BEING PRESENT, because
        libsystemd reads journal files rather than text. The orchestrator keeps
        one output file per captured day, under its own name, because those
        names carry the date the collector's glob rotates on.
  * Paced live-mimic: emit at a throttled rate so the stream flows in real time
    rather than dumping as one bulk load. Event-time still lands in the past
    (the data is historical) because we preserve the original timestamps.
  * Multi-collector fan-out: the ~31 EPN hosts of a run are PARTITIONED across N
    collector nodes (node-01..node-0N) by EPN number — each its own Fluent Bit +
    OpenSearch writer, tailing its own volume — so the replay simulates a
    multi-node farm, not one collector fronting everything. `node` = the collector
    a record was routed to (stamped from NODE_ID in collector.yaml); `host`/
    `hostname` = the real EPN it was born on (in the data). So node != host, with
    N node buckets. InfoLogger records route by their `hostname` to that
    collector's TCP input; DDS/stdout files land in that collector's tail dir.

Runs two ways:
  * `python replay.py serve`            -> HTTP trigger stub (the "replay button"
                                           hook): POST /replay kicks off a run.
  * `python replay.py run --family ...` -> one-shot CLI replay.
"""

import argparse
import datetime
import glob
import gzip
import io
import json
import os
import re
import socket
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import boto3
from botocore.config import Config

# --- config (env-overridable) ------------------------------------------------
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.cern.ch")
S3_BUCKET = os.environ.get("S3_BUCKET", "epn-backup-logs")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")  # Ceph RGW ignores it
RUN_TAG = os.environ.get("RUN_TAG", "33NXirFsSfT_38917")
INFOLOGGER_PREFIX = os.environ.get("INFOLOGGER_PREFIX", "infologger-")

IL_PORT = int(os.environ.get("INFOLOGGER_TCP_PORT", "5170"))

# --- collector fan-out -------------------------------------------------------
# The real EPN hosts are PARTITIONED across N collector nodes (node-01..node-0N),
# each a separate Fluent Bit with its OWN tail volume + OpenSearch writes, so the
# replay path simulates a multi-node farm rather than one collector fronting all
# ~31 hosts. A host is assigned to a collector by its EPN number (stable, ~even):
#   node = the collector it was routed to (stamped from NODE_ID in collector.yaml)
#   host = the real EPN it was born on (in the data)  -> node != host, N buckets.
NODE_COUNT = int(os.environ.get("NODE_COUNT", "3"))
# Each collector's tail volume is mounted at <NODES_ROOT>/<collector>/ inside the
# replay container; files go to <NODES_ROOT>/<collector>/(dds|stdout)/<host>.log.
NODES_ROOT = os.environ.get("NODES_ROOT", "/var/log/nodes")
COLLECTOR_HOSTS = [f"node-{i:02d}" for i in range(1, NODE_COUNT + 1)]


def _epn_num(host: str) -> int:
    digits = "".join(c for c in (host or "") if c.isdigit())
    return int(digits) if digits else 0


def node_index_for(host: str) -> int:
    """Stable 0-based collector index for an EPN host (balanced by EPN number)."""
    return _epn_num(host) % NODE_COUNT


def _family_dir(host: str, family: str) -> str:
    """Tail dir for this host's family on its assigned collector's volume."""
    return os.path.join(NODES_ROOT, COLLECTOR_HOSTS[node_index_for(host)], family)

# Paced live-mimic: records/sec per family (0 or negative => unthrottled).
DDS_RATE = float(os.environ.get("DDS_REPLAY_RATE", "400"))
IL_RATE = float(os.environ.get("IL_REPLAY_RATE", "500"))

# Cap objects per family; 0 => no cap. DDS is small per node (~10K lines) so the
# full run (all 31 nodes) is minutes — no cap. InfoLogger dumps are ~250K rows
# EACH (870 non-empty days across 2024-2026), so we bound it by default; set
# IL_MAX_OBJECTS=0 for the lot.
DDS_MAX_OBJECTS = int(os.environ.get("DDS_MAX_OBJECTS", "0"))
IL_MAX_OBJECTS = int(os.environ.get("IL_MAX_OBJECTS", "3"))

STDOUT_RATE = float(os.environ.get("STDOUT_REPLAY_RATE", "400"))
STDOUT_MAX_OBJECTS = int(os.environ.get("STDOUT_MAX_OBJECTS", "3"))
# One member is now one program's log file, kept under its own name, so this cap
# is also the number of distinct programs the replay can present. Four was set
# when every member was flattened into one file and the name was discarded.
STDOUT_MAX_MEMBERS = int(os.environ.get("STDOUT_MAX_MEMBERS", "40"))
STDOUT_MAX_LINES = int(os.environ.get("STDOUT_MAX_LINES_PER_MEMBER", "2000"))

HTTP_PORT = int(os.environ.get("REPLAY_HTTP_PORT", "8088"))

# Auto-load real logs once when the container starts (so `docker-compose up`
# streams real data with no manual trigger). Guarded by a marker on the shared
# volume so a restart does NOT re-ingest (the replay has no dedup). Remove the
# volume (docker-compose down -v) or POST /replay to load again.
AUTOSTART_REPLAY = os.environ.get("AUTOSTART_REPLAY", "false").lower() in (
    "1", "true", "yes", "on")
AUTOSTART_FAMILIES = os.environ.get(
    "AUTOSTART_FAMILIES", "infologger,dds,stdout,ildaemon,journald,odc")
AUTOSTART_MARKER = os.environ.get(
    "AUTOSTART_MARKER", "/var/log/node/.replay-autostart-done")

# The 16 InfoLogger columns, in mysqldump order (see the real CREATE TABLE).
IL_COLUMNS = [
    "severity", "level", "timestamp", "hostname", "rolename", "pid", "username",
    "system", "facility", "detector", "partition", "run", "errcode", "errline",
    "errsource", "message",
]

_HOST_RE = re.compile(r"_(epn[0-9]+)\.tar\.gz$")
_STDOUT_MEMBER_RE = re.compile(r"_(?:out|err)\.log$")

# Real stdout process-log filenames embed the process START time, e.g.
#   mft-tracker_t0_reco3_2026-06-20-12-15-21_9613..._out.log
#                        └── YYYY-MM-DD-HH-MM-SS ──┘
# The line itself carries a clock but NO date, so without this every stdout line
# would get INGEST time (today) and land weeks away from dds/infologger (which
# carry real 2026-06-20 event-times). We take the date from the name, the time
# of day from the line's own clock, and prepend the two as one full event time
# to each record-start line; the collector's `dpl` parser reads it as
# @timestamp. A line with no clock of its own inherits the previous one.
_STDOUT_TS_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})_")

# The clock O2 itself prints at the head of a record, optionally wrapped in the
# terminal colour escapes ErrorMonitorTask emits. Only the time is there; the
# date has to come from the filename.
_STDOUT_LINE_CLOCK_RE = re.compile(
    rb"^\[(?:\x1b\[[0-9;]*m)?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:\x1b\[[0-9;]*m)?\]")


def _stdout_event_ts(member_name):
    """Return (date, seconds-since-midnight) derived from a process-log
    filename, or None if the name doesn't carry the expected timestamp (then we
    fall back to ingest time for that member)."""
    m = _STDOUT_TS_RE.search(member_name)
    if not m:
        return None
    start = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
    return m.group(1), start


class _StdoutClock:
    """Rebuilds a full event time for one process log.

    The file name carries the date and the process start time. Each record
    carries its own wall clock, but no date. Combining the two is the only way
    to get an event time that moves with the run: the start time alone stamps
    every line of a half-hour process at the same second.

    A run crossing midnight makes the printed clock go backwards. Each backward
    step of more than an hour advances the date by one day; smaller ones are
    treated as out-of-order logging inside the same day and keep the date.
    """

    def __init__(self, date, start_seconds):
        self.day = datetime.date.fromisoformat(date)
        self.last = start_seconds
        self.ordinal = 0

    def stamp(self, raw):
        m = _STDOUT_LINE_CLOCK_RE.match(raw)
        if m is None:
            seconds = self.last
            fraction = None
        else:
            seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            fraction = m.group(4)
            if seconds < self.last - 3600:
                self.day += datetime.timedelta(days=1)
            self.last = seconds
        self.ordinal = (self.ordinal + 1) % 1000000
        if fraction:
            micro = int((fraction.decode() + "000000")[:6])
        else:
            micro = self.ordinal
        clock = "%02d:%02d:%02d" % (
            seconds // 3600, (seconds // 60) % 60, seconds % 60)
        return f"{self.day.isoformat()} {clock}.{micro:06d} ".encode()


def log(msg: str) -> None:
    print(f"[replay] {msg}", flush=True)


# --- S3 ----------------------------------------------------------------------
def s3_client():
    """Ceph RGW client. Creds come from the mounted ~/.aws/credentials profile
    (AWS_PROFILE / AWS_SHARED_CREDENTIALS_FILE are set in compose)."""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def list_objects(s3, prefix: str):
    """Yield (key, size) for every object under prefix (paginated)."""
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["Size"]


# --- pacing ------------------------------------------------------------------
class Pacer:
    """Throttle a loop to ~rate emissions/sec (paced live-mimic). rate<=0 = off."""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate and rate > 0 else 0.0

    def wait(self) -> None:
        if self.interval:
            time.sleep(self.interval)


# --- InfoLogger: mysqldump extended-INSERT parser ----------------------------
def _to_number(tok: str):
    tok = tok.strip()
    try:
        return int(tok)
    except ValueError:
        try:
            return float(tok)
        except ValueError:
            return tok  # leave as-is; shouldn't happen for valid dumps


def parse_insert_rows(stmt: str):
    """Yield one list-of-16-values per tuple in a single extended-INSERT line.

    Handles the mysqldump quoting we actually see: single-quoted strings with
    backslash escapes (\\', \\\\, \\n ...) and doubled '' quotes, NULL tokens,
    ints and floats. The `message` field is full of commas/quotes, so we scan
    character-by-character respecting quote state rather than splitting on ','.
    """
    marker = " VALUES "
    pos = stmt.find(marker)
    if pos < 0:
        return
    i = pos + len(marker)
    n = len(stmt)
    while i < n:
        if stmt[i] != "(":
            if stmt[i] == ";":
                return
            i += 1
            continue
        i += 1  # past '('
        vals = []
        while True:
            while i < n and stmt[i] == " ":
                i += 1
            ch = stmt[i]
            if ch == "'":
                i += 1
                buf = []
                while i < n:
                    c = stmt[i]
                    if c == "\\" and i + 1 < n:
                        esc = stmt[i + 1]
                        buf.append({"n": "\n", "t": "\t", "r": "\r",
                                    "0": "\0"}.get(esc, esc))
                        i += 2
                        continue
                    if c == "'":
                        if i + 1 < n and stmt[i + 1] == "'":  # doubled ''
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                vals.append("".join(buf))
            else:  # NULL or a bare number
                j = i
                while i < n and stmt[i] not in (",", ")"):
                    i += 1
                tok = stmt[j:i].strip()
                vals.append(None if tok == "NULL" else _to_number(tok))
            while i < n and stmt[i] == " ":
                i += 1
            if i < n and stmt[i] == ",":
                i += 1
                continue
            if i < n and stmt[i] == ")":
                i += 1
                break
        yield vals


def il_connect(host: str) -> socket.socket:
    """Block until a collector's tcp input accepts us (it may still be booting)."""
    while True:
        try:
            s = socket.create_connection((host, IL_PORT), timeout=5)
            log(f"infologger: connected to {host}:{IL_PORT}")
            return s
        except OSError as e:
            log(f"infologger: waiting for collector {host}:{IL_PORT}: {e}")
            time.sleep(1.0)


def replay_infologger(s3, stop: threading.Event) -> dict:
    """Stream every non-empty InfoLogger dump under the prefix, parse rows, and
    send each as a JSON line to the tcp input of the collector that OWNS its
    `hostname` (fan-out across node-01..node-0N), at a paced rate."""
    pacer = Pacer(IL_RATE)
    socks = {}  # collector index -> socket, opened lazily on first record for it

    def send(idx: int, payload: bytes) -> None:
        s = socks.get(idx)
        if s is None:
            s = socks[idx] = il_connect(COLLECTOR_HOSTS[idx])
        try:
            s.sendall(payload)
        except OSError:
            log(f"infologger: {COLLECTOR_HOSTS[idx]} lost, reconnecting")
            try:
                s.close()
            except OSError:
                pass
            s = socks[idx] = il_connect(COLLECTOR_HOSTS[idx])
            s.sendall(payload)

    sent, objects = 0, 0
    for key, size in list_objects(s3, INFOLOGGER_PREFIX):
        if stop.is_set():
            break
        if size <= 1000:  # 338 B objects are empty (DDL only)
            continue
        if IL_MAX_OBJECTS and objects >= IL_MAX_OBJECTS:
            break
        objects += 1
        log(f"infologger: {key} ({size/1e6:.1f} MB)")
        body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"]
        with gzip.GzipFile(fileobj=body) as gz:
            for raw in gz:
                if stop.is_set():
                    break
                line = raw.decode("utf-8", "replace")
                if not line.startswith("INSERT INTO"):
                    continue
                for vals in parse_insert_rows(line):
                    if len(vals) != len(IL_COLUMNS):
                        continue  # skip anything that didn't parse to 16 fields
                    record = dict(zip(IL_COLUMNS, vals))
                    payload = (json.dumps(record) + "\n").encode()
                    send(node_index_for(str(record.get("hostname") or "")), payload)
                    sent += 1
                    if sent % 5000 == 0:
                        log(f"infologger: {sent} records sent")
                    pacer.wait()
    for s in socks.values():
        try:
            s.close()
        except OSError:
            pass
    log(f"infologger: DONE — {sent} records from {objects} objects "
        f"across {len(socks)} collector(s)")
    return {"family": "infologger", "objects": objects, "records": sent}


# --- DDS + stdout: ONE streamed pass per tarball -----------------------------
def _write_members(members, stop, pacer, counter, host, label):
    """Pace out several process logs, each to its own file, on one thread.

    One pacer shared across them keeps the family's replay rate what it was when
    every process wrote into a single file."""
    n = 0
    for out_path, lines in members:
        if stop.is_set():
            break
        n += _write_lines(lines, out_path, stop, pacer, counter, host, label)
    return n


def _write_lines(lines, out_path, stop, pacer, counter, host, label):
    """Append buffered raw byte-lines to out_path at a paced rate. Reads from an
    in-memory list (not a live tar stream) so several families can be paced out
    CONCURRENTLY from a single tar pass — see replay_tarballs. `counter` is a
    running per-family total used only for progress logging. Returns lines written.
    Per-line caps are applied when buffering, so there's no max_lines here."""
    n = 0
    with open(out_path, "a", buffering=1) as out:
        for raw in lines:
            if stop.is_set():
                break
            out.write(raw.decode("utf-8", "replace"))
            n += 1
            counter[0] += 1
            if counter[0] % 5000 == 0:
                log(f"{label}[{host}]: {counter[0]} lines")
            pacer.wait()
    return n


def replay_tarballs(s3, stop: threading.Event, want_dds, want_stdout) -> list:
    """Stream each run tarball (r|gz) EXACTLY ONCE and dispatch its members to
    both families: the first dds_*.log firehose -> /var/log/node/dds/<host>.log
    (DDS), and each <proc>_*_{out,err}.log member -> its own file under
    /var/log/node/stdout/<host>/, keeping the name the farm gave it so the
    program identity survives as far as the collector.
    Formerly two functions that each downloaded the ~104 MB object; merging them
    halves S3 bandwidth per node. Per-family object caps still apply, and we stop
    reading a tar as soon as both enabled families are satisfied for that node.
    Within a node the two families are paced out CONCURRENTLY (a writer thread
    each) so dds and stdout land together rather than dds-then-stdout."""
    dds_pacer, stdout_pacer = Pacer(DDS_RATE), Pacer(STDOUT_RATE)
    prefix = f"dds/{RUN_TAG}_"
    dds_lines_c, stdout_lines_c = [0], [0]
    dds_nodes = stdout_nodes = stdout_members = 0
    for key, size in list_objects(s3, prefix):
        if stop.is_set():
            break
        m = _HOST_RE.search(key)
        if not m:
            continue
        dds_on = want_dds and (not DDS_MAX_OBJECTS or dds_nodes < DDS_MAX_OBJECTS)
        stdout_on = want_stdout and (
            not STDOUT_MAX_OBJECTS or stdout_nodes < STDOUT_MAX_OBJECTS)
        if not dds_on and not stdout_on:
            break
        host = m.group(1)
        # Route this host's files into its assigned collector's tail volume
        # (node != host: one collector fronts several EPNs).
        dds_dir, stdout_dir = _family_dir(host, "dds"), _family_dir(host, "stdout")
        if dds_on:
            os.makedirs(dds_dir, exist_ok=True)
        if stdout_on:
            os.makedirs(stdout_dir, exist_ok=True)
        dds_path = os.path.join(dds_dir, f"{host}.log")
        # One directory per EPN, one file per process, named as the farm names
        # it. That is what carries the program identity to the collector, and it
        # mirrors /scratch/jl/<run>/<epnNNN>.internal/ so the same tail pattern
        # and the same stdout_path parser serve replay and a live node.
        stdout_host_dir = os.path.join(stdout_dir, host)
        if stdout_on:
            os.makedirs(stdout_host_dir, exist_ok=True)
        firehose_done = not dds_on
        picked = 0
        log(f"tar: {key} (dds={int(dds_on)} stdout={int(stdout_on)})")
        body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"]
        # Buffer the members we want during the SINGLE tar pass (fast, unpaced),
        # then pace dds + stdout out CONCURRENTLY below. Buffering (rather than
        # stream-writing inline) is what lets both families start TOGETHER
        # regardless of member order in the tar: otherwise the ~25 s paced dds
        # firehose write blocks stdout until it finishes. DDS is ~10K lines/node
        # (a few MB), so holding it in memory briefly is cheap.
        dds_buf, stdout_buf = [], []
        with tarfile.open(fileobj=body, mode="r|gz") as tar:
            for member in tar:
                if stop.is_set():
                    break
                if not member.isfile():
                    continue
                name = member.name
                if (not firehose_done and re.search(
                        r"/dds_\d{4}-\d{2}-\d{2}\.\d+\.log$", "/" + name)):
                    src = tar.extractfile(member)
                    if src is not None:
                        dds_buf = list(src)          # whole firehose, uncapped
                    firehose_done = True
                elif (stdout_on and _STDOUT_MEMBER_RE.search(name)
                        and (not STDOUT_MAX_MEMBERS or picked < STDOUT_MAX_MEMBERS)):
                    src = tar.extractfile(member)
                    if src is not None:
                        ev = _stdout_event_ts(name)  # date + process start time
                        clock = _StdoutClock(*ev) if ev is not None else None
                        member_path = os.path.join(
                            stdout_host_dir, os.path.basename(name))
                        member_lines = []
                        kept = 0
                        for raw in src:
                            if STDOUT_MAX_LINES and kept >= STDOUT_MAX_LINES:
                                break
                            # Prepend derived event-time to record-START lines
                            # only; indented continuations (module-loading block,
                            # stack traces) keep their leading whitespace so the
                            # collector's multiline parser still folds them.
                            if clock is not None and raw[:1] not in (b" ", b"\t", b"\n"):
                                raw = clock.stamp(raw) + raw
                            member_lines.append(raw)
                            kept += 1
                        if member_lines:
                            stdout_buf.append((member_path, member_lines))
                        stdout_members += 1
                        picked += 1
                stdout_full = not stdout_on or (
                    STDOUT_MAX_MEMBERS and picked >= STDOUT_MAX_MEMBERS)
                if firehose_done and stdout_full:
                    break
        # Pace both families out at the same time — one writer thread each — so
        # dds and stdout for this node land concurrently instead of dds-then-stdout.
        writers = []
        if dds_buf:
            writers.append(threading.Thread(
                target=_write_lines, daemon=True,
                args=(dds_buf, dds_path, stop, dds_pacer, dds_lines_c, host, "dds")))
        if stdout_buf:
            writers.append(threading.Thread(
                target=_write_members, daemon=True,
                args=(stdout_buf, stop, stdout_pacer, stdout_lines_c,
                      host, "stdout")))
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        if dds_on:
            dds_nodes += 1
        if stdout_on:
            stdout_nodes += 1
        log(f"tar[{host}]: dds={'y' if dds_on else 'n'} stdout_logs={picked}")
    results = []
    if want_dds:
        log(f"dds: DONE — {dds_lines_c[0]} lines across {dds_nodes} nodes")
        results.append({"family": "dds", "nodes": dds_nodes,
                        "lines": dds_lines_c[0]})
    if want_stdout:
        log(f"stdout: DONE — {stdout_lines_c[0]} lines across {stdout_nodes} "
            f"nodes, {stdout_members} process logs")
        results.append({"family": "stdout", "nodes": stdout_nodes,
                        "members": stdout_members, "lines": stdout_lines_c[0]})
    return results


# --- the three farm-captured families ----------------------------------------
#
# InfoLogger, DDS and the process tree all come out of the S3 archive. The
# InfoLogger daemon log, the journal and the run orchestrator's log do not: they
# are written on a live EPN and nothing backs them up, so the only way a staging
# VM can carry them is a bundle captured from the farm with
# tools/epnsurvey/mkbundle.sh and shipped here.
#
# BUNDLE_ROOT holds that capture. Unset, all three are skipped and say so rather
# than failing, because a VM with no bundle is the ordinary case.
BUNDLE_ROOT = os.environ.get("REPLAY_BUNDLE_ROOT", "")
ILDAEMON_RATE = float(os.environ.get("ILDAEMON_REPLAY_RATE", "20"))
ILDAEMON_PATH = os.environ.get(
    "ILDAEMON_LOG_PATH", "/var/log/o2-infologger-daemon.log")
# The orchestrator writes about 100,000 lines a day, roughly one every second.
# Replaying at the speed of the disk would compress a day of run transitions
# into a moment and make every rate reading from it meaningless, the same way it
# would for the daemon log.
ODC_RATE = float(os.environ.get("ODC_REPLAY_RATE", "200"))
ODC_DIR = os.environ.get("ODC_LOG_DIR", "/var/log/odc/staging")


def replay_ildaemon(stop: threading.Event) -> dict:
    """Pace a captured daemon log into the path the collector tails.

    A tail input needs a file that grows, so this writes rather than links. The
    rate is low on purpose: the real file grows by about 930 lines a day, and
    replaying it at the speed of the disk would put a month of connection
    churn into one second and make every saturation figure meaningless.
    """
    if not BUNDLE_ROOT:
        log("ildaemon: no REPLAY_BUNDLE_ROOT, skipping")
        return {"family": "ildaemon", "lines": 0, "skipped": "no bundle"}
    sources = sorted(glob.glob(os.path.join(BUNDLE_ROOT, "ildaemon", "*.log")))
    if not sources:
        log(f"ildaemon: no *.log under {BUNDLE_ROOT}/ildaemon, skipping")
        return {"family": "ildaemon", "lines": 0, "skipped": "no capture"}
    os.makedirs(os.path.dirname(ILDAEMON_PATH), exist_ok=True)
    pacer = Pacer(ILDAEMON_RATE)
    counter = [0]
    total = 0
    for source in sources:
        with open(source, "rb") as fh:
            lines = fh.readlines()
        log(f"ildaemon: {os.path.basename(source)} — {len(lines)} lines")
        total += _write_lines(lines, ILDAEMON_PATH, stop, pacer, counter,
                              "local", "ildaemon")
        if stop.is_set():
            break
    log(f"ildaemon: DONE — {total} lines")
    return {"family": "ildaemon", "lines": total, "path": ILDAEMON_PATH}


def replay_odc(stop: threading.Event) -> dict:
    """Pace the captured orchestrator log into the directory the collector tails.

    One output file per captured file, keeping the capture's own name. The name
    carries the date and the collector's path is a glob, so a bundle holding
    several days replays as several files exactly as the farm writes them, and
    the rotation the tail has to follow is reproduced rather than flattened.
    """
    if not BUNDLE_ROOT:
        log("odc: no REPLAY_BUNDLE_ROOT, skipping")
        return {"family": "odc", "lines": 0, "skipped": "no bundle"}
    sources = sorted(glob.glob(os.path.join(BUNDLE_ROOT, "odc", "*.log")))
    if not sources:
        log(f"odc: no *.log under {BUNDLE_ROOT}/odc, skipping")
        return {"family": "odc", "lines": 0, "skipped": "no capture"}
    os.makedirs(ODC_DIR, exist_ok=True)
    pacer = Pacer(ODC_RATE)
    counter = [0]
    total = 0
    for source in sources:
        target = os.path.join(ODC_DIR, os.path.basename(source))
        with open(source, "rb") as fh:
            lines = fh.readlines()
        log(f"odc: {os.path.basename(source)} — {len(lines)} lines")
        total += _write_lines(lines, target, stop, pacer, counter,
                              "local", "odc")
        if stop.is_set():
            break
    log(f"odc: DONE — {total} lines")
    return {"family": "odc", "lines": total, "path": ODC_DIR}


def replay_journald(stop: threading.Event) -> dict:
    """Place a captured journal where the systemd input can read it.

    This one is not paced and does not write a line. libsystemd reads journal
    files, not text, so a captured journal is REPLAYED BY BEING PRESENT: the
    collector's systemd input is pointed at the directory with
    collector_journald_path and reads it from the beginning.

    That is a real difference from the other families and it has a consequence
    worth stating: journal entries carry their own capture timestamps and no
    pacing can move them, so a replayed journal does not advance with the run.
    """
    if not BUNDLE_ROOT:
        log("journald: no REPLAY_BUNDLE_ROOT, skipping")
        return {"family": "journald", "entries": 0, "skipped": "no bundle"}
    root = os.path.join(BUNDLE_ROOT, "journal")
    files = glob.glob(os.path.join(root, "*", "*.journal"))
    if not files:
        log(f"journald: no *.journal under {root}, skipping")
        return {"family": "journald", "entries": 0, "skipped": "no capture"}
    total = sum(os.path.getsize(f) for f in files)
    log(f"journald: {len(files)} journal files, {total // (1 << 20)} MB, "
        f"read in place from {root}")
    return {"family": "journald", "files": len(files), "bytes": total,
            "path": root}


# --- orchestration -----------------------------------------------------------
def run_replay(families, stop: threading.Event) -> list:
    # Run the families CONCURRENTLY (one thread each, own S3 client) so every
    # index appears within seconds — otherwise the dense InfoLogger phase blocks
    # the others for tens of minutes. InfoLogger streams over TCP; DDS + stdout
    # share ONE tarball per node, so they run together in a single pass (one
    # download feeds both) rather than fighting over a double get_object.
    want_dds, want_stdout = "dds" in families, "stdout" in families
    fns = []
    if "infologger" in families:
        fns.append(("infologger", replay_infologger))
    if want_dds or want_stdout:
        fns.append(("tarballs",
                    lambda s3, st: replay_tarballs(s3, st, want_dds, want_stdout)))
    # Neither of these touches S3, so the client argument is accepted and
    # ignored rather than making run_replay carry two kinds of worker.
    if "ildaemon" in families:
        fns.append(("ildaemon", lambda s3, st: replay_ildaemon(st)))
    if "journald" in families:
        fns.append(("journald", lambda s3, st: replay_journald(st)))
    if "odc" in families:
        fns.append(("odc", lambda s3, st: replay_odc(st)))

    results = [None] * len(fns)

    def runner(i, name, fn):
        try:
            results[i] = fn(s3_client(), stop)
        except Exception as e:  # one family failing must not kill the other
            log(f"{name}: ERROR {e}")

    threads = [threading.Thread(target=runner, args=(i, name, fn), daemon=True)
               for i, (name, fn) in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in results if r]


# --- HTTP trigger stub (the "replay button" hook) ----------------------------
_active = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self._json(200, {"status": "ok", "bucket": S3_BUCKET, "run": RUN_TAG})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/replay":
            self._json(404, {"error": "not found"})
            return
        qs = parse_qs(urlparse(self.path).query)
        families = qs.get(
            "family",
            ["infologger,dds,stdout,ildaemon,journald"])[0].split(",")
        families = [f.strip() for f in families if f.strip()]
        if not _active.acquire(blocking=False):
            self._json(409, {"error": "a replay is already running"})
            return

        def worker():
            try:
                run_replay(families, threading.Event())
            finally:
                _active.release()

        threading.Thread(target=worker, daemon=True).start()
        self._json(202, {"status": "started", "families": families,
                         "run": RUN_TAG})

    def log_message(self, *args):  # quiet the default access log
        pass


def _maybe_autostart() -> None:
    """On container start, load real logs once if AUTOSTART_REPLAY is set and we
    haven't already for this volume (marker file). Runs in a background thread so
    the HTTP server stays responsive; holds the same single-flight lock."""
    if not AUTOSTART_REPLAY:
        return
    if os.path.exists(AUTOSTART_MARKER):
        log(f"autostart: marker present ({AUTOSTART_MARKER}) — skipping "
            f"(POST /replay or `down -v` to load again)")
        return
    families = [f.strip() for f in AUTOSTART_FAMILIES.split(",") if f.strip()]
    if not _active.acquire(blocking=False):
        return

    def worker():
        try:
            # Write the marker up front so an interrupted load doesn't re-ingest
            # on the next restart (re-run explicitly via POST /replay instead).
            try:
                os.makedirs(os.path.dirname(AUTOSTART_MARKER), exist_ok=True)
                with open(AUTOSTART_MARKER, "w") as f:
                    f.write("autostarted\n")
            except OSError as e:
                log(f"autostart: could not write marker: {e}")
            log(f"autostart: loading real logs once — families={families}")
            run_replay(families, threading.Event())
        finally:
            _active.release()

    threading.Thread(target=worker, daemon=True).start()


def serve() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    log(f"serving replay trigger on :{HTTP_PORT} "
        f"(POST /replay?family=infologger,dds,stdout | GET /health)")
    _maybe_autostart()
    srv.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="CERN S3 log replay engine")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the HTTP trigger stub (default)")
    r = sub.add_parser("run", help="one-shot replay")
    r.add_argument("--family", default="infologger,dds,stdout",
                   help="comma list: infologger,dds,stdout")
    args = ap.parse_args()

    if args.cmd == "run":
        families = [f.strip() for f in args.family.split(",") if f.strip()]
        run_replay(families, threading.Event())
    else:
        serve()


if __name__ == "__main__":
    main()
