"""InfoLogger producer — the rich one, over a TCP socket.

Speaks the forward/TCP wire-path directly (no infoLoggerD daemon, per the design
doc's deliberate divergence). Sends JSON-lines to Fluent Bit's `tcp` input.

KEY RESILIENCE: supervisord starts this at the same time as Fluent Bit, so the
socket may not be listening yet. We retry-connect forever and reconnect on drop
— never exit on a refused connection (else supervisord's startretries gives up
and marks us FATAL).
"""

import json
import os
import random
import socket
import time

from common import NODE_ID, pick_severity, sleep_for_rate

HOST = os.environ.get("INFOLOGGER_HOST", "127.0.0.1")
PORT = int(os.environ.get("INFOLOGGER_TCP_PORT", "5170"))
BASE_RATE = float(os.environ.get("INFOLOGGER_RATE", "10"))     # msgs/sec/node
BURST_MULTIPLIER = float(os.environ.get("INFOLOGGER_BURST_MULTIPLIER", "1"))

SEV_MAP = {"info": "Info", "warn": "Warning", "error": "Error", "debug": "Debug"}

SYSTEMS = ["DAQ", "ECS", "QC", "PHYSICS"]
FACILITIES = ["readout", "stfbuilder", "qcrunner", "ecs-proxy"]
DETECTORS = ["TPC", "ITS", "TOF", "FT0", "MFT", "EMC", "PHS", "CPV"]
ERR_SOURCES = ["Readout.cxx", "MemoryBank.cxx", "Equipment.cxx", "DataBlock.cxx"]
MESSAGES = [
    "Run started, configuration applied",
    "Equipment readout nominal",
    "Page pool low watermark reached",
    "CRU link {} desynchronised",
    "DMA transfer completed",
    "Trigger rate {} kHz",
]


def make_record() -> dict:
    sev = pick_severity()
    record = {
        "hostname": NODE_ID,
        "severity": SEV_MAP[sev],
        "partition": f"part-{random.randint(0, 3)}",
        "system": random.choice(SYSTEMS),
        "facility": random.choice(FACILITIES),
        "detector": random.choice(DETECTORS),
        "run": random.randint(550000, 559999),
        "log": random.choice(MESSAGES).format(random.randint(0, 511)),
    }
    # Only error lines carry the rich error-location fields (per design doc).
    if sev == "error":
        record["errline"] = random.randint(1, 4000)
        record["errsource"] = random.choice(ERR_SOURCES)
    return record


def connect() -> socket.socket:
    """Block until Fluent Bit's tcp input accepts us. Never give up."""
    while True:
        try:
            s = socket.create_connection((HOST, PORT), timeout=5)
            print(f"[infologger] connected to {HOST}:{PORT}", flush=True)
            return s
        except OSError as e:
            print(f"[infologger] waiting for fluent-bit {HOST}:{PORT}: {e}",
                  flush=True)
            time.sleep(1.0)


def main() -> None:
    print(f"[infologger] starting on {NODE_ID} -> {HOST}:{PORT} @ {BASE_RATE}/s",
          flush=True)
    sock = connect()
    while True:
        line = (json.dumps(make_record()) + "\n").encode()
        try:
            sock.sendall(line)
        except OSError:
            print("[infologger] connection lost, reconnecting", flush=True)
            try:
                sock.close()
            except OSError:
                pass
            sock = connect()
            continue
        sleep_for_rate(BASE_RATE, BURST_MULTIPLIER)


if __name__ == "__main__":
    main()
