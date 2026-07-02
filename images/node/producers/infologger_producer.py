"""InfoLogger producer — the rich one, over a TCP socket.

Now emits the REAL O2 InfoLogger `messages` schema (16 fields) rather than the
Flight-1 placeholder set, so the synthetic stream mirrors what actually lands in
s3://epn-backup-logs/infologger-*/ (each object is a mysqldump of this exact
table). Transport is unchanged per the plan: JSON-lines to Fluent Bit's `tcp`
input on :5170.

Real row for reference (from tmp_messages_p104):
  ('I',7,1775001600.155993,'epn292',NULL,153515,'epn','DPL','readout-proxy',
   NULL,'31o1EQhrXN9',570609,NULL,378,'ExternalFairMQDeviceProxy.cxx','RAW ... report')

Schema (column -> meaning):
  severity  char(1)   I/W/E/F/D
  level     int       O2 verbosity (ops=1, support=6, developer=11, trace=21...)
  timestamp double    epoch seconds.micro   <- EVENT TIME (wired to @timestamp later)
  hostname  str       emitting node, e.g. epn292
  rolename  str|None  ECS role, usually null
  pid       int
  username  str       'epn'
  system    str       DPL / ODC / ...
  facility  str       device/role, e.g. readout-proxy, ctf-writer
  detector  str|None  TPC/ITS/... often null for infra logs
  partition str|None  run partition id (hash), null outside a run
  run       int|None  run number, null outside a run
  errcode   int|None  usually null
  errline   int|None  source line of the log call (present broadly, NOT just errors)
  errsource str|None  source file, e.g. CTFWriterSpec.cxx
  message   str       the log text

KEY RESILIENCE: unchanged — retry-connect forever, reconnect on drop, never exit
on a refused connection (else supervisord marks us FATAL).
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

# Node identity as a real EPN hostname: node-01 -> epn001.
_digits = "".join(c for c in NODE_ID if c.isdigit()) or "0"
HOSTNAME = f"epn{int(_digits):03d}"
USERNAME = os.environ.get("IL_USERNAME", "epn")

# Canonical severity -> real single-char code (see common.pick_severity mix).
SEV_CHAR = {"info": "I", "warn": "W", "error": "E", "debug": "D"}
# ...and a plausible O2 verbosity level per severity (real samples showed 7, 13).
SEV_LEVELS = {"info": (6, 7, 11), "warn": (6, 7), "error": (1, 6), "debug": (11, 13, 21)}

# rolename is null the vast majority of the time (mostly-null realism).
ROLENAMES = [None, None, None, None, "production"]


def _partition_id() -> str:
    """An 11-char run-partition hash, like the real '31o1EQhrXN9'."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choice(alphabet) for _ in range(11))


# O2 detector codes, used to synthesise realistic size-report messages.
DETECTORS = ["ITS", "TPC", "TRD", "TOF", "PHS", "CPV", "EMC", "HMP", "MFT",
             "MCH", "MID", "ZDC", "FT0", "FV0", "FDD", "CTP"]


def _raw_size_report() -> str:
    """readout-proxy: 'RAW <tf> size report:DET/RAWDATA:<n>,... - Total:<t>'."""
    dets = random.sample(DETECTORS, k=random.randint(8, len(DETECTORS)))
    total = 0
    parts = []
    for d in dets:
        n = random.randint(10_000, 500_000_000)
        total += n
        parts.append(f"{d}/RAWDATA:{n:,}")
    return f"RAW {random.randint(13_000_000, 13_999_999)} size report:" \
           + " ".join(parts) + f" - Total:{total:,}"


def _ctf_size_report() -> str:
    """ctf-writer: 'CTF <tf> size report: DET:<n> ... - Total:<t>'."""
    dets = random.sample(DETECTORS, k=random.randint(8, len(DETECTORS)))
    total = 0
    parts = []
    for d in dets:
        n = random.randint(10_000, 90_000_000)
        total += n
        parts.append(f"{d}:{n:,}")
    return f"CTF {random.randint(13_000_000, 13_999_999)} size report: " \
           + " ".join(parts) + f" - Total:{total:,}"


def _odc_status() -> str:
    return f"Status: found {random.randint(1, 4)} partition(s):"


def _stf_built() -> str:
    return (f"Built STF {random.randint(13_000_000, 13_999_999)} with "
            f"{random.randint(200, 4000)} messages, "
            f"{random.randint(10_000_000, 700_000_000):,} bytes")


def _decoded() -> str:
    return (f"Decoded {random.randint(1000, 5_000_000):,} digits from "
            f"{random.randint(10, 900)} RDHs")


def _qc_cycle() -> str:
    return (f"processed {random.randint(1, 5000)} objects in cycle "
            f"{random.randint(1, 999)}")


# Coherent (system, facility, errsource, detector, message-builder) bundles.
# These fields are CORRELATED in the real dump — a facility always logs from one
# source file, in one system — so we draw a whole bundle at once rather than each
# field independently (the Flight-1 mistake that could pair readout-proxy with
# CTFWriterSpec.cxx, which never happens in reality).
CONTEXTS = [
    ("DPL", "readout-proxy",       "ExternalFairMQDeviceProxy.cxx", None,  _raw_size_report),
    ("DPL", "ctf-writer",          "CTFWriterSpec.cxx",             None,  _ctf_size_report),
    ("ODC", "ODC",                 "InfoLogger.h",                  None,  _odc_status),
    ("DPL", "stfbuilder",          "SubTimeFrameBuilderDevice.cxx", None,  _stf_built),
    ("DPL", "its-stf-decoder",     "STFDecoderSpec.cxx",            "ITS", _decoded),
    ("DPL", "mft-tracker",         "TrackerSpec.cxx",               "MFT", _decoded),
    ("DPL", "gpu-reconstruction",  "GPUReconstructionCUDA.cxx",     "TPC", _decoded),
    ("QC",  "qc-task-TOF-TaskRaw", "TaskRunner.cxx",                "TOF", _qc_cycle),
    ("QC",  "qc-task-MCH-Errors",  "TaskRunner.cxx",                "MCH", _qc_cycle),
]


def pick_context():
    """Draw one coherent bundle; resolve its message builder to a string.

    Returns (system, facility, errsource, detector, message).
    """
    system, facility, errsource, detector, message_builder = random.choice(CONTEXTS)
    return system, facility, errsource, detector, message_builder()


# Session state: this node is "in a run" most of the time, with a stable
# run number + partition id for the life of the process (as a real EPN would be).
_RUN = random.randint(570000, 579999)
_PART = _partition_id()


def make_record() -> dict:
    sev = pick_severity()
    system, facility, errsource, detector, message = pick_context()
    in_run = random.random() < 0.9
    return {
        "severity": SEV_CHAR[sev],
        "level": random.choice(SEV_LEVELS[sev]),
        "timestamp": round(time.time(), 6),
        "hostname": HOSTNAME,
        "rolename": random.choice(ROLENAMES),
        "pid": random.randint(1000, 4_000_000),
        "username": USERNAME,
        "system": system,
        "facility": facility,
        "detector": detector,
        "partition": _PART if in_run else None,
        "run": _RUN if in_run else None,
        "errcode": None,
        "errline": random.randint(1, 900),
        "errsource": errsource,
        "message": message,
    }


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
    print(f"[infologger] starting on {HOSTNAME} -> {HOST}:{PORT} @ {BASE_RATE}/s",
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
