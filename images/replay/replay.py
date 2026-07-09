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
  * Paced live-mimic: emit at a throttled rate so the stream flows in real time
    rather than dumping as one bulk load. Event-time still lands in the past
    (the data is historical) because we preserve the original timestamps.
  * One collector, node id in data: all 31 EPN nodes of a run flow through the
    single node-01 collector; identity rides in the data (InfoLogger hostname
    column; DDS via the per-node file path).

Runs two ways:
  * `python replay.py serve`            -> HTTP trigger stub (the "replay button"
                                           hook): POST /replay kicks off a run.
  * `python replay.py run --family ...` -> one-shot CLI replay.
"""

import argparse
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
INFOLOGGER_PREFIX = os.environ.get("INFOLOGGER_PREFIX", "infologger-2026/")

IL_HOST = os.environ.get("INFOLOGGER_HOST", "node-01")
IL_PORT = int(os.environ.get("INFOLOGGER_TCP_PORT", "5170"))

DDS_OUT_DIR = os.environ.get("DDS_OUT_DIR", "/var/log/node/dds")

# Paced live-mimic: records/sec per family (0 or negative => unthrottled).
DDS_RATE = float(os.environ.get("DDS_REPLAY_RATE", "400"))
IL_RATE = float(os.environ.get("IL_REPLAY_RATE", "500"))

# Cap objects per family; 0 => no cap. DDS is small per node (~10K lines) so the
# full run (all 31 nodes) is minutes — no cap. InfoLogger dumps are ~250K rows
# EACH (179 of them), so we bound it by default; set IL_MAX_OBJECTS=0 for the lot.
DDS_MAX_OBJECTS = int(os.environ.get("DDS_MAX_OBJECTS", "0"))
IL_MAX_OBJECTS = int(os.environ.get("IL_MAX_OBJECTS", "3"))

STDOUT_OUT_DIR = os.environ.get("STDOUT_OUT_DIR", "/var/log/node/stdout")
STDOUT_RATE = float(os.environ.get("STDOUT_REPLAY_RATE", "400"))
STDOUT_MAX_OBJECTS = int(os.environ.get("STDOUT_MAX_OBJECTS", "3"))
STDOUT_MAX_MEMBERS = int(os.environ.get("STDOUT_MAX_MEMBERS", "4"))
STDOUT_MAX_LINES = int(os.environ.get("STDOUT_MAX_LINES_PER_MEMBER", "2000"))

HTTP_PORT = int(os.environ.get("REPLAY_HTTP_PORT", "8088"))

# Auto-load real logs once when the container starts (so `docker-compose up`
# streams real data with no manual trigger). Guarded by a marker on the shared
# volume so a restart does NOT re-ingest (the replay has no dedup). Remove the
# volume (docker-compose down -v) or POST /replay to load again.
AUTOSTART_REPLAY = os.environ.get("AUTOSTART_REPLAY", "false").lower() in (
    "1", "true", "yes", "on")
AUTOSTART_FAMILIES = os.environ.get("AUTOSTART_FAMILIES", "infologger,dds,stdout")
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
# The file itself has NO per-line timestamp, so without this every stdout line
# would get INGEST time (today) and land weeks away from dds/infologger (which
# carry real 2026-06-20 event-times). We derive the start-time from the name and
# prepend it to each record-start line; the collector's stdout_root parser reads
# it as @timestamp. All lines in one file share that start-time (the reality —
# there's nothing finer to recover), with a monotonic microsecond bump so lines
# keep their order in Discover.
_STDOUT_TS_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})_")


def _stdout_event_ts(member_name):
    """Return 'YYYY-MM-DD HH:MM:SS' derived from a process-log filename, or None
    if the name doesn't carry the expected timestamp (then we fall back to
    ingest time for that member)."""
    m = _STDOUT_TS_RE.search(member_name)
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"


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


def il_connect() -> socket.socket:
    """Block until the collector's tcp input accepts us (it may still be booting)."""
    while True:
        try:
            s = socket.create_connection((IL_HOST, IL_PORT), timeout=5)
            log(f"infologger: connected to {IL_HOST}:{IL_PORT}")
            return s
        except OSError as e:
            log(f"infologger: waiting for collector {IL_HOST}:{IL_PORT}: {e}")
            time.sleep(1.0)


def replay_infologger(s3, stop: threading.Event) -> dict:
    """Stream every non-empty InfoLogger dump under the prefix, parse rows, and
    send them as JSON lines to the collector's tcp input at a paced rate."""
    pacer = Pacer(IL_RATE)
    sock = il_connect()
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
                    try:
                        sock.sendall(payload)
                    except OSError:
                        log("infologger: connection lost, reconnecting")
                        try:
                            sock.close()
                        except OSError:
                            pass
                        sock = il_connect()
                        sock.sendall(payload)
                    sent += 1
                    if sent % 5000 == 0:
                        log(f"infologger: {sent} records sent")
                    pacer.wait()
    try:
        sock.close()
    except OSError:
        pass
    log(f"infologger: DONE — {sent} records from {objects} objects")
    return {"family": "infologger", "objects": objects, "records": sent}


# --- DDS + stdout: ONE streamed pass per tarball -----------------------------
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
    (DDS), and the <proc>_*_{out,err}.log members -> the shared stdout tail file.
    Formerly two functions that each downloaded the ~104 MB object; merging them
    halves S3 bandwidth per node. Per-family object caps still apply, and we stop
    reading a tar as soon as both enabled families are satisfied for that node.
    Within a node the two families are paced out CONCURRENTLY (a writer thread
    each) so dds and stdout land together rather than dds-then-stdout."""
    dds_pacer, stdout_pacer = Pacer(DDS_RATE), Pacer(STDOUT_RATE)
    if want_dds:
        os.makedirs(DDS_OUT_DIR, exist_ok=True)
    if want_stdout:
        os.makedirs(STDOUT_OUT_DIR, exist_ok=True)
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
        dds_path = os.path.join(DDS_OUT_DIR, f"{host}.log")
        stdout_path = os.path.join(STDOUT_OUT_DIR, f"{host}.log")
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
                        ev = _stdout_event_ts(name)  # process start-time from name
                        kept = 0
                        for raw in src:
                            if STDOUT_MAX_LINES and kept >= STDOUT_MAX_LINES:
                                break
                            # Prepend derived event-time to record-START lines
                            # only; indented continuations (module-loading block,
                            # stack traces) keep their leading whitespace so the
                            # collector's multiline parser still folds them.
                            if ev is not None and raw[:1] not in (b" ", b"\t", b"\n"):
                                raw = f"{ev}.{kept % 1000000:06d} ".encode() + raw
                            stdout_buf.append(raw)
                            kept += 1
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
                target=_write_lines, daemon=True,
                args=(stdout_buf, stdout_path, stop, stdout_pacer, stdout_lines_c,
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
        families = qs.get("family", ["infologger,dds,stdout"])[0].split(",")
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
