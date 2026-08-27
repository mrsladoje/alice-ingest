#!/usr/bin/env python3
"""Resolve the flush basin: 0.5 against 0.75 against 1, three runs each.

The grid was geometric — 0.5, 1, 2, 5, 10 — which is the right shape for
finding an optimum but the wrong shape for reading one. It found a basin rather
than a point: 180.41 core-seconds per million at 0.5 against 177.38 at 1, a
1.7 % gap inside a 2.8 % drift floor. That is a tie, and 0.75 was never run
because nothing pointed at it until the tie appeared.

**One cell at 0.75 would settle nothing.** A single point inside the noise is
noise. So each of the three values runs three times, ALTERNATING rather than
blocked, so that any drift over the two hours falls on all three equally instead
of landing on whichever ran last.

What this can answer: whether the basin has a floor between 0.5 and 1, and
whether either end beats the other once each has a mean and a spread of its own.
What it cannot answer: anything about the healthy machine, where 0.5 beat 1 by
11 % with no overlap. That measurement stands on its own and is the better
evidence; this one describes the degraded rig.
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
LOG = os.path.join(RUNS, "basin.log")
OUT = os.path.join(RUNS, "basin-results.json")

RATE = int(os.environ.get("SOAK_RATE", "12000"))
INTERVAL = float(os.environ.get("SOAK_INTERVAL", "4"))
DURATION = int(os.environ.get("SOAK_DURATION", "480"))
SETTLE = 45
VALUES = [0.5, 0.75, 1]
REPEATS = 3
LABELS = ["fb", "fb2", "os", "store1", "store2"]

state = {"rate": RATE, "interval": INTERVAL, "cells": {}, "summary": {}}


def say(message):
    line = "[basin %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def run(name, flush):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6",
            "--cell", name, "--sink", "cluster",
            "--rate", str(RATE), "--duration", str(DURATION),
            "--settle", str(SETTLE), "--interval", str(INTERVAL),
            "--flush", str(flush),
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off", "--live-lane", "off",
            "--pattern", "STEADY", "--cpus", "4",
            "--os-buffer-size", "False"]
    say("running %s (flush %s)" % (name, flush))
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
    row = {
        "flush": flush,
        "total": round(total * 1e6 / records, 2) if records else None,
        "collector": round((targets.get("fb") or {}).get("core_seconds", 0)
                           * 1e6 / records, 2) if records else None,
        "memory": recorder.get("peak_memory_mb"),
        "gate_error_pct": gate.get("error_pct"),
        "state": (summary.get("verdict") or {}).get("state"),
        "run": os.path.basename(run_dir),
    }
    state["cells"][name] = row
    save()
    say("  %s -> %s core-s/M, %s MB" % (name, row["total"], row["memory"]))


def spread(values):
    if not values:
        return None
    mean = statistics.mean(values)
    return {
        "n": len(values),
        "mean": round(mean, 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "range_pct": round(100.0 * (max(values) - min(values)) / mean, 2),
    }


def main():
    say("=" * 60)
    say("flush basin: %s, %d runs each, alternating, at %d/s"
        % (VALUES, REPEATS, RATE))
    if os.path.exists(OUT):
        with open(OUT) as handle:
            previous = json.load(handle)
        if previous.get("rate") == RATE:
            state["cells"].update(previous.get("cells") or {})
            say("reusing %d cell(s)" % len(state["cells"]))

    for repeat in range(1, REPEATS + 1):
        for flush in VALUES:
            name = "BASIN-%s-r%d" % (flush, repeat)
            if name in state["cells"] and state["cells"][name].get("total"):
                say("  %s already measured" % name)
                continue
            run(name, flush)

    for flush in VALUES:
        rows = [row for row in state["cells"].values()
                if row["flush"] == flush and row.get("total")]
        state["summary"][str(flush)] = {
            "cost": spread([row["total"] for row in rows]),
            "memory": spread([row["memory"] for row in rows
                              if row.get("memory")]),
        }
    save()

    say("")
    say("%-7s %-9s %-9s %-9s %-8s" % ("flush", "mean", "min", "max", "spread"))
    for flush in VALUES:
        cost = state["summary"][str(flush)]["cost"]
        if cost:
            say("%-7s %-9s %-9s %-9s %.1f%%"
                % (flush, cost["mean"], cost["min"], cost["max"],
                   cost["range_pct"]))
    means = {flush: state["summary"][str(flush)]["cost"]["mean"]
             for flush in VALUES if state["summary"][str(flush)]["cost"]}
    if means:
        best = min(means, key=lambda flush: means[flush])
        # The floor is the WIDEST of the three arms' own spreads. Using the
        # narrowest would let a lucky arm certify a difference the noisy one
        # cannot support.
        floors = [state["summary"][str(flush)]["cost"]["range_pct"]
                  for flush in VALUES
                  if state["summary"][str(flush)]["cost"]]
        floor = max(floors) if floors else 0
        rivals = [(flush, 100.0 * (means[flush] - means[best]) / means[flush])
                  for flush in means if flush != best]
        say("")
        say("cheapest: flush %s at %s core-s/M" % (best, means[best]))
        for flush, gap in sorted(rivals):
            verdict = "REAL" if gap > floor else "inside the %.1f%% floor" % floor
            say("  beats flush %-5s by %5.2f%%  — %s" % (flush, gap, verdict))
    say("done. %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
