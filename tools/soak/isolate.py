#!/usr/bin/env python3
"""Confirm the one finding worth confirming: core isolation.

The sweep's largest effect was not a knob in the plan. Running the collector and
the worker's OpenSearch node on separate cores, instead of letting both float
across all four, cut total cost roughly in half — 158.78 core-seconds per
million against 76.83, at the same flush and the same heap.

It is also the only finding that changes the DEPLOY rather than a config file,
so it is the one that has to survive a repeat.

**Why this and not the flush basin.** The machine's run-to-run scatter grew from
about 3 % in the morning to nearly 40 % by mid-afternoon. A 1.7 % question — is
flush 0.5 better than 1 — cannot be answered against 40 % noise, and running it
anyway would produce a number with no meaning. A 52 % question still can.

Three runs of each arm, alternating.
"""

import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SOAK = os.path.join(HERE, "soak.py")
LOG = os.path.join(RUNS, "isolate.log")
OUT = os.path.join(RUNS, "isolate-results.json")

RATE = int(os.environ.get("SOAK_RATE", "12000"))
INTERVAL = float(os.environ.get("SOAK_INTERVAL", "4"))
DURATION = int(os.environ.get("SOAK_DURATION", "480"))
SETTLE = 45
LABELS = ["fb", "fb2", "os", "store1", "store2"]
REPEATS = 3

# Same flush, same heap, same rate. The only difference is whether the two
# services share four cores or get one and three.
ARMS = {
    "shared": {},
    "isolated": {"fb_cpuset": "0", "os_cpuset": "1-3"},
}

state = {"rate": RATE, "cells": {}, "summary": {}}


def say(message):
    line = "[isolate %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def run(name, arm):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6", "--cell", name,
            "--sink", "cluster", "--rate", str(RATE),
            "--duration", str(DURATION), "--settle", str(SETTLE),
            "--interval", str(INTERVAL), "--flush", "0.5",
            "--os-heap", "2g",
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off", "--live-lane", "off",
            "--pattern", "STEADY", "--os-buffer-size", "False"]
    knobs = ARMS[arm]
    if knobs:
        argv += ["--fb-cpuset", knobs["fb_cpuset"],
                 "--os-cpuset", knobs["os_cpuset"]]
    else:
        argv += ["--cpus", "4"]
    say("running %s (%s)" % (name, arm))
    subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    fresh = sorted(set(os.listdir(RUNS)) - before)
    run_dir = os.path.join(RUNS, fresh[-1]) if fresh else ""
    try:
        with open(os.path.join(run_dir, "summary.json")) as handle:
            summary = json.load(handle)
    except OSError:
        say("  %s produced no summary" % name)
        return
    recorder = summary.get("recorder") or {}
    records = recorder.get("ingested_records") or 0
    targets = recorder.get("targets") or {}
    total = sum((targets.get(label) or {}).get("core_seconds", 0)
                for label in LABELS)
    gate = summary.get("gate_zero") or {}
    row = {"arm": arm,
           "total": round(total * 1e6 / records, 2) if records else None,
           "collector": round((targets.get("fb") or {}).get("core_seconds", 0)
                              * 1e6 / records, 2) if records else None,
           "memory": recorder.get("peak_memory_mb"),
           "gate_error_pct": gate.get("error_pct"),
           "state": (summary.get("verdict") or {}).get("state"),
           "run": os.path.basename(run_dir)}
    state["cells"][name] = row
    save()
    say("  %s -> %s core-s/M (offer %+.2f%%, %s)"
        % (name, row["total"], row["gate_error_pct"] or 0, row["state"]))


def main():
    say("=" * 60)
    say("core isolation: shared four cores against one-and-three, %d runs each"
        % REPEATS)
    for repeat in range(1, REPEATS + 1):
        for arm in ARMS:
            run("ISO-%s-r%d" % (arm, repeat), arm)

    for arm in ARMS:
        values = [row["total"] for row in state["cells"].values()
                  if row["arm"] == arm and row.get("total")]
        if values:
            mean = statistics.mean(values)
            state["summary"][arm] = {
                "n": len(values), "mean": round(mean, 2),
                "min": round(min(values), 2), "max": round(max(values), 2),
                "range_pct": round(100.0 * (max(values) - min(values)) / mean, 1),
                "values": values,
            }
    save()
    say("")
    for arm, row in state["summary"].items():
        say("%-9s mean %-8s min %-8s max %-8s own spread %.1f%%"
            % (arm, row["mean"], row["min"], row["max"], row["range_pct"]))
    if len(state["summary"]) == 2:
        shared = state["summary"]["shared"]
        isolated = state["summary"]["isolated"]
        gap = 100.0 * (shared["mean"] - isolated["mean"]) / shared["mean"]
        floor = max(shared["range_pct"], isolated["range_pct"])
        overlap = isolated["max"] >= shared["min"]
        say("")
        say("isolation is %.1f%% cheaper on the means" % gap)
        say("widest arm spread: %.1f%%" % floor)
        say("ranges %s" % ("OVERLAP — not resolved" if overlap
                           else "DO NOT overlap — the effect is real"))
    say("done. %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
