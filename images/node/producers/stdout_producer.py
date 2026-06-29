"""stdout producer — unstructured application logs.

Mimics the free-form text a CERN process dumps to *its* stdout. Despite the
name it writes to a FILE (/var/log/node/stdout.log) that Fluent Bit tails — the
"stdout" refers to the log TYPE, not the channel. Plain text, not JSON.
Does not react to BURST (multiplier 1).
"""

import os
import random
from datetime import datetime, timezone

from common import NODE_ID, pick_severity, sleep_for_rate

LOG_PATH = os.environ.get("STDOUT_LOG_PATH", "/var/log/node/stdout.log")
BASE_RATE = float(os.environ.get("STDOUT_RATE", "15"))         # msgs/sec/node
BURST_MULTIPLIER = float(os.environ.get("STDOUT_BURST_MULTIPLIER", "1"))

SEV_MAP = {"info": "INFO", "warn": "WARN", "error": "ERROR", "debug": "DEBUG"}
MESSAGES = [
    "Processing event {} in {}ms",
    "Calibration object loaded for run {}",
    "Heartbeat ok, queue depth {}",
    "Reconnecting to FairMQ channel attempt {}",
    "Memory pool usage {}MB",
    "Worker thread {} idle",
]


def make_line() -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    sev = SEV_MAP[pick_severity()]
    msg = random.choice(MESSAGES).format(random.randint(1, 99999), random.randint(1, 999))
    # free-form, space-separated — deliberately NOT structured
    return f"{ts} {NODE_ID} [{sev}] {msg}"


def main() -> None:
    print(f"[stdout] starting on {NODE_ID} -> {LOG_PATH} @ {BASE_RATE}/s",
          flush=True)
    with open(LOG_PATH, "a", buffering=1) as f:
        while True:
            f.write(make_line() + "\n")
            sleep_for_rate(BASE_RATE, BURST_MULTIPLIER)


if __name__ == "__main__":
    main()
