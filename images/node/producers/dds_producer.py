"""DDS producer — the dense one.

Writes JSON-lines to a file that Fluent Bit `tail`s. Lean schema per the design
doc: severity (D/IMPORTANT/WARN/INFO), node, log, source_file.
This is the producer BURST spikes (default 15x).
"""

import json
import os
import random

from common import NODE_ID, pick_severity, sleep_for_rate

LOG_PATH = os.environ.get("DDS_LOG_PATH", "/var/log/node/dds.log")
BASE_RATE = float(os.environ.get("DDS_RATE", "50"))            # msgs/sec/node
BURST_MULTIPLIER = float(os.environ.get("DDS_BURST_MULTIPLIER", "15"))

# DDS speaks its own severity vocabulary; map the canonical ones onto it.
SEV_MAP = {"info": "INFO", "warn": "WARN", "error": "IMPORTANT", "debug": "D"}

SOURCE_FILES = [
    "DataDistribution.cxx", "SubTimeFrameBuilder.cxx", "TfScheduler.cxx",
    "StfSender.cxx", "TfBuilderDevice.cxx", "StfBuilderDevice.cxx",
]
MESSAGES = [
    "SubTimeFrame {} forwarded to builder",
    "TimeFrame {} assembled ({} STFs)",
    "Buffer occupancy at {}%",
    "Scheduler assigned TF {} to EPN",
    "Dropped incomplete STF for TF {}",
    "Backpressure signalled to {} senders",
]


def make_line() -> str:
    sev = pick_severity()
    record = {
        "severity": SEV_MAP[sev],
        "node": NODE_ID,
        "log": random.choice(MESSAGES).format(
            random.randint(1000, 99999), random.randint(1, 250)
        ),
        "source_file": random.choice(SOURCE_FILES),
    }
    return json.dumps(record)


def main() -> None:
    print(f"[dds] starting on {NODE_ID} -> {LOG_PATH} "
          f"@ {BASE_RATE}/s (burst x{BURST_MULTIPLIER})", flush=True)
    # buffering=1 => line-buffered: each line is flushed on '\n' so Fluent Bit's
    # tail sees new records immediately.
    with open(LOG_PATH, "a", buffering=1) as f:
        while True:
            f.write(make_line() + "\n")
            sleep_for_rate(BASE_RATE, BURST_MULTIPLIER)


if __name__ == "__main__":
    main()
