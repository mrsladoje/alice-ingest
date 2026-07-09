"""Shared helpers for all three mock producers.

Two ideas live here so the producers stay tiny:
  1. The severity distribution (doc: ~90% info / 8% warn / 1.5% error / 0.5% debug).
  2. The BURST mechanism — a control file on a shared volume that, when present,
     multiplies a producer's send rate by its own multiplier.
"""

import os
import random
import time

# --- node identity -----------------------------------------------------------
# Each of node-01..node-0N gets a distinct NODE_ID injected by docker-compose.
# NODE_ID is the COLLECTOR identity (this Fluent Bit / VM); the collector stamps
# it onto every record as `node`. Falls back to a sentinel if run bare.
NODE_ID = os.environ.get("NODE_ID", "node-unknown")


# --- host pool (the GPU processing nodes this collector aggregates) -----------
# A collector is NOT the same as the host a log was born on. Each Fluent Bit
# collector (this node) fronts a RACK of EPNs — the GPU Event Processing Nodes
# where reconstruction runs and processes emit their logs. So `node` (collector,
# from NODE_ID) and `host`/`hostname` (emitting EPN) DIFFER, mirroring a real
# aggregator topology (one collector, many GPU hosts) rather than a degenerate
# 1:1. node-01 -> epn001..epn010, node-02 -> epn011..epn020, and so on.
HOST_POOL_SIZE = int(os.environ.get("HOST_POOL_SIZE", "10"))


def _node_index() -> int:
    """Ordinal from NODE_ID (node-03 -> 3); 1 for the bare-run sentinel."""
    digits = "".join(c for c in NODE_ID if c.isdigit())
    return int(digits) if digits else 1


def host_pool() -> list:
    """The EPN hostnames this collector serves, e.g. node-02 -> epn011..epn020."""
    start = (_node_index() - 1) * HOST_POOL_SIZE + 1
    return [f"epn{start + i:03d}" for i in range(HOST_POOL_SIZE)]


def pick_host() -> str:
    """One EPN from this collector's rack, drawn per record."""
    return random.choice(host_pool())


# --- severity distribution ---------------------------------------------------
# Canonical severities. Each producer maps these to its own vocabulary.
_SEVERITIES = ["info", "warn", "error", "debug"]
_WEIGHTS = [90.0, 8.0, 1.5, 0.5]


def pick_severity() -> str:
    """Return a canonical severity, weighted per the design doc's mix."""
    return random.choices(_SEVERITIES, weights=_WEIGHTS, k=1)[0]


# --- BURST control -----------------------------------------------------------
# A single file on a shared bind-mount toggles burst for the whole fleet:
#   touch ./control/burst   -> burst ON  for all nodes
#   rm    ./control/burst   -> burst OFF for all nodes
# If the control volume isn't mounted, the file is simply absent => no burst.
BURST_FILE = os.environ.get("BURST_FILE", "/control/burst")


def burst_active() -> bool:
    return os.path.exists(BURST_FILE)


def sleep_for_rate(base_rate: float, burst_multiplier: float) -> None:
    """Sleep so the calling loop emits ~`rate` messages/second.

    When the burst file is present the effective rate is multiplied. A producer
    that should NOT react to burst simply passes burst_multiplier=1.
    """
    rate = base_rate * burst_multiplier if burst_active() else base_rate
    if rate <= 0:
        rate = 1.0
    time.sleep(1.0 / rate)
