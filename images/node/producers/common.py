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
# Each of node-01..node-10 gets a distinct NODE_ID injected by docker-compose,
# so dashboards can filter by node. Falls back to a sentinel if run bare.
NODE_ID = os.environ.get("NODE_ID", "node-unknown")


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
