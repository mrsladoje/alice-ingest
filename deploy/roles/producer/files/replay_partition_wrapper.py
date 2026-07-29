#!/usr/bin/env python3
"""Single-partition wrapper around images/replay/replay.py (PRESERVED,
copied to the VM byte-for-byte — this file never imports a modified copy).

LOCKED topology (deploy/site.yml / group_vars/all.yml): each VM replays ONLY
its own epn_partition slice (epn_num % NODE_COUNT == EPN_PARTITION) into its
LOCAL log_root, and ships InfoLogger strictly to 127.0.0.1:INFOLOGGER_TCP_PORT
— never to another VM's collector.

WHY A WRAPPER (not an edit to replay.py, not env/dir tricks alone):
replay.py's own fan-out (NODE_COUNT / NODES_ROOT / COLLECTOR_HOSTS) assumes
ONE process serving ALL collectors: it partitions the ~31 real EPN hosts by
`epn_num % NODE_COUNT` and, per family, either
  - DDS/stdout: writes into NODES_ROOT/<collector-name>/<family>/<host>.log
    (an object-level decision — one whole S3 tarball belongs to exactly one
    host, hence exactly one collector), or
  - InfoLogger: opens a TCP connection to <collector-name>:INFOLOGGER_TCP_PORT
    per record (a ROW-level decision — one mysqldump object interleaves rows
    for MANY hosts/collectors; the object itself can't be pre-filtered).
A pure directory/symlink arrangement (route NODES_ROOT/<name> for every OTHER
collector into a black hole) would still make the DDS/stdout side correct, but
CANNOT filter InfoLogger, whose collector target is chosen per-row deep inside
replay_infologger(). Editing images/replay/replay.py is out of scope (PRESERVED
ground truth). So instead we import the module UNMODIFIED and monkeypatch its
two extension points:

  1. list_objects(s3, prefix) — wrapped so any S3 key that IS a per-host DDS/
     stdout tarball (matches replay._HOST_RE) for a host NOT in our partition
     is dropped before the S3 GET (no wasted bandwidth downloading other
     partitions). Keys that are NOT a per-host tarball (e.g. the InfoLogger
     dump objects, which don't match _HOST_RE) pass through untouched — every
     surviving DDS/stdout key now belongs to OUR partition, so replay.py's own
     _family_dir()/node_index_for() always resolve to OUR OWN collector name,
     and files land under NODES_ROOT/<our node_id>/... (see the producer
     role's tasks/main.yml: NODES_ROOT/<node_id> is a symlink straight at this
     VM's log_root — no other collector's directory is ever created).

  2. il_connect(host) — wrapped so that when `host` is OUR OWN collector name
     (node_id) we connect for real, to 127.0.0.1 (never a DNS name — LOCKED:
     strictly localhost), and for any OTHER collector's name we hand back an
     inert socket-like object whose sendall()/close() are no-ops. Rows destined
     for another VM's partition are silently dropped locally instead of
     replay.py's il_connect() retrying forever against a hostname ("node-02"
     etc.) that doesn't resolve on this VM.

Cardboard-only REPLAY_LOOP is applied here too: run_replay is wrapped so that
when it is set, each completed pass is followed by another one (the HTTP
handler's _active lock stays held for the whole loop, so a second POST /replay
still gets 409). Every pass re-samples the shifted-clock offset, so each pass
ships with fresh collector_time. Pass RATE knobs stay replay.py's own
(IL_REPLAY_RATE / DDS_REPLAY_RATE / STDOUT_REPLAY_RATE) — pacing is
configuration, not code.

Cardboard-only REPLAY_CLOCK=shifted is also applied here (never in images/):
offset = now − sampled earliest event time; IL timestamps, DDS line prefixes,
and stdout filename-derived times are slid forward. Default preserved leaves
replay.py behaviour unchanged.

Everything else (rates, pacing, autostart-marker guard, the HTTP trigger
stub/serve loop, argument parsing) is exactly replay.py's own — this wrapper
only narrows WHICH host/collector each already-existing code path targets.

Configuration is via environment only (systemd Environment=, see
templates/replay.service.j2): EPN_PARTITION (this VM's 0-based slice) plus
every env var replay.py itself already reads (NODE_COUNT, NODES_ROOT,
S3_*, RUN_TAG, *_REPLAY_RATE, INFOLOGGER_TCP_PORT, AUTOSTART_*, ...), plus
REPLAY_CLOCK (preserved|shifted).
"""

import gzip
import os
import re
import sys
import tarfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay  # noqa: E402  -- images/replay/replay.py, copied verbatim

try:
    EPN_PARTITION = int(os.environ["EPN_PARTITION"])
except (KeyError, ValueError) as exc:
    raise SystemExit(
        "replay_partition_wrapper: EPN_PARTITION must be set to this VM's "
        "0-based epn_partition (see inventory.yml) — refusing to guess and "
        "risk replaying someone else's slice."
    ) from exc

if not (0 <= EPN_PARTITION < replay.NODE_COUNT):
    raise SystemExit(
        f"replay_partition_wrapper: EPN_PARTITION={EPN_PARTITION} is out of "
        f"range for NODE_COUNT={replay.NODE_COUNT}"
    )

OWN_COLLECTOR = replay.COLLECTOR_HOSTS[EPN_PARTITION]

try:
    MAX_OBJECT_BYTES = int(os.environ.get("REPLAY_MAX_OBJECT_BYTES", "0"))
except ValueError:
    MAX_OBJECT_BYTES = 0

REPLAY_CLOCK = os.environ.get("REPLAY_CLOCK", "preserved").strip().lower()
REPLAY_LOOP = os.environ.get("REPLAY_LOOP", "false").strip().lower() in (
    "1", "true", "yes", "on")
try:
    REPLAY_LOOP_PAUSE = float(os.environ.get("REPLAY_LOOP_PAUSE_SECONDS", "30"))
except ValueError:
    REPLAY_LOOP_PAUSE = 30.0
_CLOCK_OFFSET_SEC = 0.0
_DDS_TS_RE = re.compile(rb"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")
_IL_KEYS = set(replay.IL_COLUMNS)

_orig_list_objects = replay.list_objects
_orig_il_connect = replay.il_connect
_orig_json_dumps = replay.json.dumps
_orig_stdout_event_ts = replay._stdout_event_ts
_orig_write_lines = replay._write_lines
_orig_run_replay = replay.run_replay


def _partition_filtered_list_objects(s3, prefix):
    """Same generator as replay.list_objects, minus other partitions' DDS/
    stdout tarballs (dropped before the S3 GET) and any object larger than
    REPLAY_MAX_OBJECT_BYTES. Non-host-tagged keys (e.g. InfoLogger dump
    objects) are only size-filtered here — see module docstring."""
    for key, size in _orig_list_objects(s3, prefix):
        if MAX_OBJECT_BYTES and size > MAX_OBJECT_BYTES:
            replay.log(f"skip oversize object ({size / 1e6:.0f} MB > "
                       f"{MAX_OBJECT_BYTES / 1e6:.0f} MB cap): {key}")
            continue
        m = replay._HOST_RE.search(key)
        if m is not None and replay.node_index_for(m.group(1)) != EPN_PARTITION:
            continue
        yield key, size


class _NullSocket:
    """Stand-in for a socket to a collector that isn't this VM. Silently
    discards InfoLogger rows destined for another partition."""

    def sendall(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


def _partition_filtered_il_connect(host):
    if host != OWN_COLLECTOR:
        return _NullSocket()
    return _orig_il_connect("127.0.0.1")


def _parse_wall_ts(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    text = str(s).strip()
    if not text:
        return None
    if (text[0].isdigit() and "-" not in text[:4]
            and "T" not in text and " " not in text):
        return float(text)
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(
                text.rstrip("Z"), fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _format_wall_ts(epoch, with_frac=True):
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if with_frac:
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _shift_epoch(value):
    if _CLOCK_OFFSET_SEC == 0:
        return value
    base = _parse_wall_ts(value)
    if base is None:
        return value
    return base + _CLOCK_OFFSET_SEC


def _shift_stdout_ts(ts_str):
    if _CLOCK_OFFSET_SEC == 0 or ts_str is None:
        return ts_str
    base = _parse_wall_ts(ts_str)
    if base is None:
        return ts_str
    return _format_wall_ts(base + _CLOCK_OFFSET_SEC, with_frac=False)


def _shift_dds_line(raw):
    if _CLOCK_OFFSET_SEC == 0:
        return raw
    m = _DDS_TS_RE.match(raw)
    if not m:
        return raw
    base = _parse_wall_ts(m.group(1).decode())
    if base is None:
        return raw
    new_ts = _format_wall_ts(base + _CLOCK_OFFSET_SEC, with_frac=True).encode()
    return new_ts + raw[m.end(1):]


def _init_shifted_clock(s3, families):
    global _CLOCK_OFFSET_SEC
    _CLOCK_OFFSET_SEC = 0.0
    if REPLAY_CLOCK != "shifted":
        replay.log(f"clock: mode={REPLAY_CLOCK} (timestamps preserved)")
        return
    earliest = None

    if "infologger" in families:
        scanned = 0
        for key, size in replay.list_objects(s3, replay.INFOLOGGER_PREFIX):
            if size <= 1000:
                continue
            scanned += 1
            if scanned > 8:
                break
            body = s3.get_object(Bucket=replay.S3_BUCKET, Key=key)["Body"]
            rows = 0
            with gzip.GzipFile(fileobj=body) as gz:
                for raw in gz:
                    line = raw.decode("utf-8", "replace")
                    if not line.startswith("INSERT INTO"):
                        continue
                    for vals in replay.parse_insert_rows(line):
                        if len(vals) != len(replay.IL_COLUMNS):
                            continue
                        ts = _parse_wall_ts(
                            vals[replay.IL_COLUMNS.index("timestamp")])
                        if ts is None:
                            continue
                        earliest = ts if earliest is None else min(earliest, ts)
                        rows += 1
                        if rows >= 200:
                            break
                    if rows >= 200:
                        break

    if "dds" in families or "stdout" in families:
        prefix = f"dds/{replay.RUN_TAG}_"
        checked = 0
        for key, size in replay.list_objects(s3, prefix):
            if checked >= 12:
                break
            m = replay._HOST_RE.search(key)
            if not m:
                continue
            checked += 1
            body = s3.get_object(Bucket=replay.S3_BUCKET, Key=key)["Body"]
            with tarfile.open(fileobj=body, mode="r|gz") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = member.name
                    if re.search(
                            r"/dds_\d{4}-\d{2}-\d{2}\.\d+\.log$", "/" + name):
                        src = tar.extractfile(member)
                        if src is not None:
                            for _ in range(20):
                                first = src.readline()
                                if not first:
                                    break
                                mts = _DDS_TS_RE.match(first)
                                if mts:
                                    ts = _parse_wall_ts(mts.group(1).decode())
                                    if ts is not None:
                                        earliest = (
                                            ts if earliest is None
                                            else min(earliest, ts))
                    elif replay._STDOUT_MEMBER_RE.search(name):
                        ev = _orig_stdout_event_ts(name)
                        ts = _parse_wall_ts(ev)
                        if ts is not None:
                            earliest = (
                                ts if earliest is None else min(earliest, ts))

    if earliest is None:
        replay.log(
            "clock: shifted requested but no event timestamps found; "
            "leaving preserved")
        return
    _CLOCK_OFFSET_SEC = time.time() - earliest
    replay.log(
        f"clock: mode=shifted offset_sec={_CLOCK_OFFSET_SEC:.3f} "
        f"earliest={_format_wall_ts(earliest)}")


def _json_dumps_shifted(obj, *args, **kwargs):
    if (_CLOCK_OFFSET_SEC and isinstance(obj, dict)
            and _IL_KEYS.issubset(obj.keys())):
        obj = dict(obj)
        obj["timestamp"] = _shift_epoch(obj.get("timestamp"))
    return _orig_json_dumps(obj, *args, **kwargs)


def _stdout_event_ts_shifted(member_name):
    return _shift_stdout_ts(_orig_stdout_event_ts(member_name))


def _write_lines_shifted(lines, out_path, stop, pacer, counter, host, label):
    if _CLOCK_OFFSET_SEC and label == "dds":
        lines = [_shift_dds_line(line) for line in lines]
    return _orig_write_lines(
        lines, out_path, stop, pacer, counter, host, label)


def _run_replay_shifted(families, stop):
    results, passes = [], 0
    while True:
        passes += 1
        _init_shifted_clock(replay.s3_client(), families)
        results = _orig_run_replay(families, stop)
        if not REPLAY_LOOP or stop.is_set():
            return results
        replay.log(
            f"loop: pass {passes} finished, next pass in "
            f"{REPLAY_LOOP_PAUSE:.0f}s (REPLAY_LOOP=true)")
        time.sleep(REPLAY_LOOP_PAUSE)


_orig_do_GET = replay.Handler.do_GET


def _do_GET_with_status(self):
    if replay.urlparse(self.path).path == "/replay-status":
        self._json(200, {"running": replay._active.locked(),
                         "loop": REPLAY_LOOP,
                         "collector": OWN_COLLECTOR})
        return
    return _orig_do_GET(self)


replay.Handler.do_GET = _do_GET_with_status
replay.list_objects = _partition_filtered_list_objects
replay.il_connect = _partition_filtered_il_connect
replay.json.dumps = _json_dumps_shifted
replay._stdout_event_ts = _stdout_event_ts_shifted
replay._write_lines = _write_lines_shifted
replay.run_replay = _run_replay_shifted


if __name__ == "__main__":
    replay.log(
        f"partition wrapper: EPN_PARTITION={EPN_PARTITION} "
        f"(collector={OWN_COLLECTOR}) of NODE_COUNT={replay.NODE_COUNT} "
        f"clock={REPLAY_CLOCK}"
    )
    replay.main()
