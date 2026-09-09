#!/usr/bin/env python3
"""Single-partition wrapper around replay.py, which is deployed unchanged.

Each machine replays only its own epn_partition slice
(epn_num % NODE_COUNT == EPN_PARTITION) into its local log_root, and ships
InfoLogger strictly to 127.0.0.1:INFOLOGGER_TCP_PORT, never to another
machine's collector.

WHY A WRAPPER, and not an engine edit or a directory trick alone:
the engine's own fan-out (NODE_COUNT / NODES_ROOT / COLLECTOR_HOSTS) assumes
one process serving every collector. It partitions the EPN hosts by
`epn_num % NODE_COUNT` and, per family, either
  - DDS and stdout: writes NODES_ROOT/<collector>/dds/<host>.log and one file
    per process under NODES_ROOT/<collector>/stdout/<host>/ (an object-level
    decision: one S3 tarball belongs to exactly one host), or
  - InfoLogger: opens a TCP connection to <collector>:INFOLOGGER_TCP_PORT per
    record (a row-level decision: one dump object interleaves rows for many
    hosts, so the object cannot be pre-filtered).
A symlink arrangement alone makes the DDS and stdout side correct and leaves
InfoLogger broken, because its target is chosen per row inside
replay_infologger(). The engine owns what the archive produces; this wrapper
owns every deployment-shaped divergence. It imports the module and
monkeypatches its two extension points:

  1. list_objects(s3, prefix): any key that is a per-host DDS or stdout
     tarball (matches replay._HOST_RE) for a host outside this partition is
     dropped before the S3 GET, as is any object above REPLAY_MAX_OBJECT_BYTES.
     Every surviving tarball belongs to this partition, so the engine's
     _family_dir() always resolves to this machine's own collector name, and
     NODES_ROOT/<node_id> is a symlink at this machine's log_root.

  2. il_connect(host): when `host` is this machine's own collector name the
     connection goes to 127.0.0.1; for any other name an inert socket-like
     object is returned whose sendall() and close() do nothing. Rows for
     another partition are dropped locally instead of the engine retrying
     forever against a hostname that does not resolve here.

REPLAY_LOOP: run_replay is wrapped so that a finished pass is followed by
another one. The HTTP handler's _active lock stays held for the whole loop, so
a second POST /replay still gets 409. Every pass re-samples the shifted-clock
offset. The rate knobs stay the engine's own; pacing is configuration.

REPLAY_CLOCK=shifted: offset = now - sampled earliest event time; InfoLogger
timestamps, DDS line prefixes and stdout filename-derived times are slid
forward by it. The default, preserved, leaves the engine untouched.

Two endpoints are added: GET /replay-status and POST /replay-stop.

Everything else (rates, pacing, the autostart-marker guard, the HTTP trigger
and serve loop, argument parsing) is the engine's own; this wrapper only
narrows which host or collector each existing code path targets.

Configuration is by environment only, from the systemd unit: EPN_PARTITION
(this machine's 0-based slice) plus every variable the engine already reads,
plus REPLAY_CLOCK (preserved|shifted), REPLAY_LOOP and REPLAY_LOOP_PAUSE_SECONDS.
"""

import gzip
import os
import re
import sys
import tarfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay  # noqa: E402

try:
    EPN_PARTITION = int(os.environ["EPN_PARTITION"])
except (KeyError, ValueError) as exc:
    raise SystemExit(
        "replay_partition_wrapper: EPN_PARTITION must be set to this machine's "
        "0-based epn_partition from the inventory; refusing to guess and "
        "replay another machine's slice."
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
CLOCK_CACHE = os.environ.get(
    "REPLAY_CLOCK_CACHE", "/var/log/node/.replay-earliest-event").strip()
try:
    CLOCK_SCAN_TARBALLS = int(os.environ.get("REPLAY_CLOCK_SCAN_TARBALLS", "3"))
except ValueError:
    CLOCK_SCAN_TARBALLS = 3
try:
    CLOCK_SCAN_MEMBERS = int(os.environ.get("REPLAY_CLOCK_SCAN_MEMBERS", "8"))
except ValueError:
    CLOCK_SCAN_MEMBERS = 8
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
    """Stand-in for a socket to a collector that is not this machine. Silently
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


def _read_cached_earliest():
    if not CLOCK_CACHE:
        return None
    try:
        with open(CLOCK_CACHE) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_cached_earliest(earliest):
    if not CLOCK_CACHE:
        return
    try:
        os.makedirs(os.path.dirname(CLOCK_CACHE), exist_ok=True)
        with open(CLOCK_CACHE, "w") as f:
            f.write(repr(float(earliest)))
    except OSError as e:
        replay.log(f"clock: could not cache the earliest event time: {e}")


def _init_shifted_clock(s3, families):
    global _CLOCK_OFFSET_SEC
    _CLOCK_OFFSET_SEC = 0.0
    if REPLAY_CLOCK != "shifted":
        replay.log(f"clock: mode={REPLAY_CLOCK} (timestamps preserved)")
        return

    cached = _read_cached_earliest()
    if cached is not None:
        _CLOCK_OFFSET_SEC = time.time() - cached
        replay.log(
            f"clock: mode=shifted offset_sec={_CLOCK_OFFSET_SEC:.3f} "
            f"earliest={_format_wall_ts(cached)} (from {CLOCK_CACHE}; "
            f"delete that file to rescan)")
        return

    scan_started = time.time()
    replay.log(
        "clock: scanning S3 for the archive's earliest event time. This runs "
        "ONCE — the result is cached and every later load reuses it. No "
        "records ship until it finishes.")
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
            if checked >= CLOCK_SCAN_TARBALLS:
                break
            m = replay._HOST_RE.search(key)
            if not m:
                continue
            checked += 1
            body = s3.get_object(Bucket=replay.S3_BUCKET, Key=key)["Body"]
            sampled = 0
            with tarfile.open(fileobj=body, mode="r|gz") as tar:
                for member in tar:
                    if sampled >= CLOCK_SCAN_MEMBERS:
                        break
                    if not member.isfile():
                        continue
                    sampled += 1
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

    elapsed = time.time() - scan_started
    if earliest is None:
        replay.log(
            f"clock: shifted requested but no event timestamps found after "
            f"{elapsed:.0f}s; leaving preserved")
        return
    _write_cached_earliest(earliest)
    _CLOCK_OFFSET_SEC = time.time() - earliest
    replay.log(
        f"clock: mode=shifted offset_sec={_CLOCK_OFFSET_SEC:.3f} "
        f"earliest={_format_wall_ts(earliest)} (scan took {elapsed:.0f}s, "
        f"cached in {CLOCK_CACHE})")


def _json_dumps_shifted(obj, *args, **kwargs):
    if (_CLOCK_OFFSET_SEC and isinstance(obj, dict)
            and _IL_KEYS.issubset(obj.keys())):
        obj = dict(obj)
        obj["timestamp"] = _shift_epoch(obj.get("timestamp"))
    return _orig_json_dumps(obj, *args, **kwargs)


def _stdout_event_ts_shifted(member_name):
    """Shift the process-log start time, which is now a (date, seconds) pair.

    The engine used to return 'YYYY-MM-DD HH:MM:SS' as one string. It now
    returns the date and the seconds since midnight separately, because the
    event time is rebuilt per line from the file's date and the line's own
    clock rather than stamping every line with the process start. This shifts
    the pair and hands back a pair, so the engine's _StdoutClock still gets what
    it expects.
    """
    got = _orig_stdout_event_ts(member_name)
    if _CLOCK_OFFSET_SEC == 0 or got is None:
        return got
    date, start_seconds = got
    shifted = _shift_stdout_ts("%s %02d:%02d:%02d" % (
        date, start_seconds // 3600, (start_seconds // 60) % 60,
        start_seconds % 60))
    if shifted is None:
        return got
    new_date, _, clock = shifted.partition(" ")
    hours, minutes, seconds = (int(p) for p in clock.split(":"))
    return new_date, hours * 3600 + minutes * 60 + seconds


def _write_lines_shifted(lines, out_path, stop, pacer, counter, host, label):
    if _CLOCK_OFFSET_SEC and label == "dds":
        lines = [_shift_dds_line(line) for line in lines]
    return _orig_write_lines(
        lines, out_path, stop, pacer, counter, host, label)


def _run_replay_shifted(families, stop):
    global _CURRENT_STOP
    _CURRENT_STOP = stop
    results, passes = [], 0
    while True:
        passes += 1
        _init_shifted_clock(replay.s3_client(), families)
        results = _orig_run_replay(families, stop)
        if not REPLAY_LOOP or stop.is_set():
            if stop.is_set():
                replay.log("stop requested — replay ending")
            return results
        replay.log(
            f"loop: pass {passes} finished, next pass in "
            f"{REPLAY_LOOP_PAUSE:.0f}s (REPLAY_LOOP=true)")
        waited = 0.0
        while waited < REPLAY_LOOP_PAUSE and not stop.is_set():
            time.sleep(min(1.0, REPLAY_LOOP_PAUSE - waited))
            waited += 1.0
        if stop.is_set():
            replay.log("stop requested during the loop pause — replay ending")
            return results


_CURRENT_STOP = None
_orig_do_GET = replay.Handler.do_GET
_orig_do_POST = replay.Handler.do_POST


def _do_POST_with_stop(self):
    if replay.urlparse(self.path).path == "/replay-stop":
        was_running = replay._active.locked()
        if _CURRENT_STOP is not None:
            _CURRENT_STOP.set()
        self._json(202, {"stopping": was_running})
        return
    return _orig_do_POST(self)


def _do_GET_with_status(self):
    if replay.urlparse(self.path).path == "/replay-status":
        self._json(200, {"running": replay._active.locked(),
                         "loop": REPLAY_LOOP,
                         "collector": OWN_COLLECTOR})
        return
    return _orig_do_GET(self)


replay.Handler.do_GET = _do_GET_with_status
replay.Handler.do_POST = _do_POST_with_stop
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
