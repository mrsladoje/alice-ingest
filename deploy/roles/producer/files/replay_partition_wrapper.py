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

Everything else (rates, pacing, autostart-marker guard, the HTTP trigger
stub/serve loop, argument parsing) is exactly replay.py's own — this wrapper
only narrows WHICH host/collector each already-existing code path targets.

Configuration is via environment only (systemd Environment=, see
templates/replay.service.j2): EPN_PARTITION (this VM's 0-based slice) plus
every env var replay.py itself already reads (NODE_COUNT, NODES_ROOT,
S3_*, RUN_TAG, *_REPLAY_RATE, INFOLOGGER_TCP_PORT, AUTOSTART_*, ...).
"""

import os
import sys

# replay.py is copied alongside this file (producers_app_root) by the
# producer role — import it from there regardless of CWD.
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

_orig_list_objects = replay.list_objects


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


_orig_il_connect = replay.il_connect


def _partition_filtered_il_connect(host):
    if host != OWN_COLLECTOR:
        return _NullSocket()
    # LOCKED: InfoLogger goes over TCP to localhost ONLY, never a DNS name.
    return _orig_il_connect("127.0.0.1")


replay.list_objects = _partition_filtered_list_objects
replay.il_connect = _partition_filtered_il_connect


if __name__ == "__main__":
    replay.log(
        f"partition wrapper: EPN_PARTITION={EPN_PARTITION} "
        f"(collector={OWN_COLLECTOR}) of NODE_COUNT={replay.NODE_COUNT}"
    )
    replay.main()
