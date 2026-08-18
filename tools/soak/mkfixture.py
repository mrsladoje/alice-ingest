#!/usr/bin/env python3

import argparse
import datetime
import json
import os
import random
import re
import sys

DDS_SOURCES = [
    "dds-agent", "o2-epn-topo-reco", "readout-proxy", "ctf-writer",
    "tpc-tracker", "its-tracker", "mft-tracker", "gpu-reco", "qc-task",
]
DDS_AGENT_MSGS = [
    "Sending device shutdown signal to lobby member {pid}",
    "Task <{pid}> with path <{wf}> finished with exit code 0",
    "Received a request to activate a topology (agent id: {pid})",
    "The DDS commander accepted the connection from {pid}",
    "Task assignment for agent {pid} on slot {r} completed",
    "Watchdog: pid {pid} rss 214MB vsz 3921MB state S",
]
DDS_WF_INF = [
    "Processing timeframe {tf} with {n} tracks",
    "Sent {n} messages to downstream device on channel from-{wf}",
    "TF {tf} clusters {n} compressed to {r}%",
    "Data processing device state change: RUNNING",
]
DDS_WF_ERR = [
    "Cannot connect channel {wf} after {n} attempts",
    "Dropping timeframe {tf}: input {wf} not ready",
    "Timeout waiting for {wf} reply after {n} ms",
]
STDOUT_ROOT = [
    ("TGeoManager::Init", "geometry TGeo already initialized, refusing to overwrite"),
    ("TClass::Init", "no dictionary for class o2::tpc::ClusterNative is available"),
    ("TFile::Open", "file alien:///alice/data/ccdb snapshot opened read-only"),
]
STDOUT_PLAIN = [
    "[INFO] Reading configuration from /home/epn/o2/config.json",
    "Loading shared library libO2TPCReconstruction.so",
    "[state] READY -> RUNNING",
    "processed {n} timeframes in {r} seconds",
    "free memory {n} kB / 515396075 kB",
]
IL_SYSTEMS = ["DPL", "ODC", "ECS", "QC"]
IL_FACILITIES = [
    "readout-proxy", "ctf-writer", "tpc-tracker", "its-tracker",
    "gpu-reco", "epn-topo", "qc-task-daq",
]
IL_DETECTORS = [None, None, None, "TPC", "ITS", "MFT", "FT0"]
IL_MESSAGES = [
    "RAW input rate {n} MB/s, dropped 0",
    "Timeframe {tf} accepted by writer",
    "Device state change from RUNNING to READY",
    "Cannot allocate shared memory segment of {n} bytes",
    "Calibration object loaded from CCDB for run {run}",
    "GPU reconstruction finished in {r} ms",
]
IL_COLUMNS = [
    "severity", "level", "timestamp", "hostname", "rolename", "pid", "username",
    "system", "facility", "detector", "partition", "run", "errcode", "errline",
    "errsource", "message",
]
SEV_CHARS = ["I"] * 90 + ["W"] * 8 + ["E"] * 2
DDS_SEVERITIES = ["inf"] * 80 + ["wrn"] * 12 + ["err"] * 8
STDOUT_SEVERITIES = [None] * 60 + ["Info"] * 25 + ["Warning"] * 10 + ["Error"] * 5

TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+")


def _handle():
    return "<0x%08x:0x%016x>" % (
        random.randint(0, 0xffffffff), random.randint(0, 0xffffffffffff))


def _fill(text):
    return text.format(
        pid=random.randint(2_000_000, 2_100_000),
        wf=random.choice(DDS_SOURCES),
        tf=random.randint(13_000_000, 13_999_999),
        n=random.randint(1, 4_000_000),
        r=random.randint(1, 999),
        run=random.randint(550000, 560000),
        cls="o2::tpc::ClusterNative",
    )


def dds_body():
    sev = random.choice(DDS_SEVERITIES)
    if random.random() < 0.5:
        source = "dds-agent"
        msg = _fill(random.choice(DDS_AGENT_MSGS))
    else:
        source = random.choice(DDS_SOURCES)
        pool = DDS_WF_ERR if sev == "err" else DDS_WF_INF
        msg = _fill(random.choice(pool))
    return "%-6s %-22s %s    %s" % (sev, source, _handle(), msg)


def stdout_body():
    sev = random.choice(STDOUT_SEVERITIES)
    if sev is None:
        return _fill(random.choice(STDOUT_PLAIN))
    facility, msg = random.choice(STDOUT_ROOT)
    return "%s in <%s>: %s" % (sev, facility, _fill(msg))


def il_record():
    sev = random.choice(SEV_CHARS)
    record = {
        "severity": sev,
        "level": random.choice([1, 6, 7, 11, 13]),
        "timestamp": 0.0,
        "hostname": "epn%03d" % random.randint(1, 320),
        "rolename": random.choice([None, None, None, "production"]),
        "pid": random.randint(100000, 999999),
        "username": "epn",
        "system": random.choice(IL_SYSTEMS),
        "facility": random.choice(IL_FACILITIES),
        "detector": random.choice(IL_DETECTORS),
        "partition": random.choice([None, "31o1EQhrXN9", "9aKlmZq2Vt4"]),
        "run": random.choice([None, random.randint(550000, 560000)]),
        "errcode": None,
        "errline": random.randint(50, 900),
        "errsource": random.choice(
            ["CTFWriterSpec.cxx", "ExternalFairMQDeviceProxy.cxx", "GPUReconstruction.cxx"]),
        "message": _fill(random.choice(IL_MESSAGES)),
    }
    return {k: record[k] for k in IL_COLUMNS}


def harvest(paths, limit):
    bodies = []
    for path in paths:
        try:
            with open(path, "r", errors="replace") as handle:
                for line in handle:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    stripped = TS_PREFIX.sub("", line)
                    if stripped:
                        bodies.append(stripped)
                    if len(bodies) >= limit:
                        return bodies
        except OSError:
            continue
    return bodies


def collect(root, family):
    found = []
    base = os.path.join(root, family)
    for directory in (base, root):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".log") and (family in name or directory == base):
                found.append(os.path.join(directory, name))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=20000)
    parser.add_argument("--from-logs", default="")
    parser.add_argument("--from-il", default="")
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()

    random.seed(args.seed)
    os.makedirs(args.out, exist_ok=True)

    dds, stdout = [], []
    if args.from_logs:
        dds = harvest(collect(args.from_logs, "dds"), args.count)
        stdout = harvest(collect(args.from_logs, "stdout"), args.count)
    if not dds:
        dds = [dds_body() for _ in range(args.count)]
    if not stdout:
        stdout = [stdout_body() for _ in range(args.count)]

    infologger = []
    if args.from_il:
        with open(args.from_il, "r", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    infologger.append(json.loads(line))
                except ValueError:
                    continue
                if len(infologger) >= args.count:
                    break
    if not infologger:
        infologger = [il_record() for _ in range(args.count)]

    with open(os.path.join(args.out, "dds.bodies"), "w") as handle:
        handle.write("\n".join(dds) + "\n")
    with open(os.path.join(args.out, "stdout.bodies"), "w") as handle:
        handle.write("\n".join(stdout) + "\n")
    with open(os.path.join(args.out, "infologger.json"), "w") as handle:
        for record in infologger:
            record["timestamp"] = 0.0
            handle.write(json.dumps(record) + "\n")

    meta = {
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seed": args.seed,
        "counts": {"dds": len(dds), "stdout": len(stdout), "infologger": len(infologger)},
        "source": args.from_logs or "synthetic",
        "mean_bytes": {
            "dds": sum(len(x) for x in dds) // max(1, len(dds)),
            "stdout": sum(len(x) for x in stdout) // max(1, len(stdout)),
            "infologger": sum(len(json.dumps(x)) for x in infologger[:500])
                          // max(1, min(500, len(infologger))),
        },
    }
    with open(os.path.join(args.out, "fixture.json"), "w") as handle:
        json.dump(meta, handle, indent=2)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
