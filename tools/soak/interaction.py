#!/usr/bin/env python3
"""Is the heap-by-flush ranking flip real, or one unlucky cell each?

At flush 0.5 a 2 GB heap beats 3 GB by 21.7 %. At flush 5 the single cells say
3 GB beats 2 GB by 144 %. A swing that large across one knob is either a real
interaction or two bad cells, and one run of each cannot tell them apart.

This matters less for the recommendation than it looks — the plan ships flush
0.5, where 2 GB wins clearly — and more for what it says about the method: heap
and flush were treated as independent knobs, chosen one after the other. If they
interact this strongly, that assumption is wrong and the grid needs to be read
as a surface rather than as two lines.

Two runs of each of the four corners, alternating so drift lands on all of them.
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
LOG = os.path.join(RUNS, "interaction.log")
OUT = os.path.join(RUNS, "interaction-results.json")

RATE = int(os.environ.get("SOAK_RATE", "12000"))
INTERVAL = float(os.environ.get("SOAK_INTERVAL", "4"))
DURATION = int(os.environ.get("SOAK_DURATION", "480"))
SETTLE = 45
LABELS = ["fb", "fb2", "os", "store1", "store2"]
CORNERS = [(0.5, "2g"), (0.5, "3g"), (5, "2g"), (5, "3g")]
REPEATS = 2

state = {"rate": RATE, "cells": {}, "corners": {}}


def say(message):
    line = "[interaction %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def run(name, flush, heap):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6", "--cell", name,
            "--sink", "cluster", "--rate", str(RATE),
            "--duration", str(DURATION), "--settle", str(SETTLE),
            "--interval", str(INTERVAL), "--flush", str(flush),
            "--os-heap", heap,
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off", "--live-lane", "off",
            "--pattern", "STEADY", "--cpus", "4", "--os-buffer-size", "False"]
    say("running %s (flush %s, heap %s)" % (name, flush, heap))
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
    row = {"flush": flush, "heap": heap,
           "total": round(total * 1e6 / records, 2) if records else None,
           "memory": recorder.get("peak_memory_mb"),
           "gate_error_pct": gate.get("error_pct"),
           "state": (summary.get("verdict") or {}).get("state"),
           "run": os.path.basename(run_dir)}
    state["cells"][name] = row
    save()
    say("  %s -> %s core-s/M (offer %+.2f%%)"
        % (name, row["total"], row["gate_error_pct"] or 0))


def main():
    say("=" * 60)
    say("heap x flush interaction, %d runs per corner at %d/s" % (REPEATS, RATE))
    for repeat in range(1, REPEATS + 1):
        for flush, heap in CORNERS:
            run("IX-f%s-h%s-r%d" % (flush, heap, repeat), flush, heap)

    for flush, heap in CORNERS:
        rows = [row for row in state["cells"].values()
                if row["flush"] == flush and row["heap"] == heap
                and row.get("total")]
        if rows:
            state["corners"]["flush %s / heap %s" % (flush, heap)] = {
                "n": len(rows),
                "mean": round(statistics.mean([r["total"] for r in rows]), 2),
                "values": [r["total"] for r in rows],
            }
    save()
    say("")
    for name, row in state["corners"].items():
        say("%-24s mean %-9s from %s" % (name, row["mean"], row["values"]))

    def mean_at(flush, heap):
        row = state["corners"].get("flush %s / heap %s" % (flush, heap))
        return row["mean"] if row else None

    say("")
    for flush in (0.5, 5):
        two, three = mean_at(flush, "2g"), mean_at(flush, "3g")
        if two and three:
            winner = "2g" if two < three else "3g"
            gap = 100.0 * abs(three - two) / max(two, three)
            say("at flush %-4s %s wins by %.1f%%" % (flush, winner, gap))
    say("If the winner differs between the two rows, heap and flush interact "
        "and neither can be chosen without the other.")
    say("done. %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
