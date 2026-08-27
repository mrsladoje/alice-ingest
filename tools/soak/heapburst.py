#!/usr/bin/env python3
"""Does a 1 GB heap still absorb a burst?

Steady state already answered the cost question: 1, 2 and 3 GB are
indistinguishable, so the worker heap is held at 1 GB for footprint. This asks
the only question that could overturn that — whether the smaller heap FAILS when
the pipeline is pushed past what it can retire in real time.

**Cost per record is the wrong metric here and measuring it would repeat a
mistake this round already made.** Under burst every arm falls behind by
definition, and core-seconds per million inflates for reasons that have nothing
to do with heap. So this measures absorption instead:

  lost        did any record die for good, per family
  chunks      how deep the queue got against the 64-chunk cap
  memory      peak, and whether it came back down
  drain       whether the queue emptied once the burst stopped

Those are meaningful even when the rig cannot keep up in the moment, provided it
drains afterwards — which is exactly the production question. A worker at 23
records a second is nowhere near a limit; a burst is the only time the heap has
consequences.

The burst is expressed as a MULTIPLE of what this machine retires cleanly, not
as an absolute rate, because what matters is the ratio of offered to capacity.
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
# TAG separates one burst block from another, so a second block at a harder
# peak writes its own results file instead of overwriting the first.
TAG = os.environ.get("SOAK_TAG", "15k60")
LOG = os.path.join(RUNS, "heapburst-%s.log" % TAG)
OUT = os.path.join(RUNS, "heapburst-%s-results.json" % TAG)

CLEAN = int(os.environ.get("SOAK_CLEAN_RATE", "5000"))
# The discriminating quantity is not the peak rate on its own, it is the peak
# against the burst window: the indexing buffer is 10 % of heap and our records
# are 309 bytes, so a window that some heaps can fill and others cannot is what
# separates the arms. Too high a peak and the 256M output cap binds first,
# which hides the heap entirely.
PEAK = int(os.environ.get("SOAK_PEAK", str(CLEAN * 3)))
BASE = int(os.environ.get("SOAK_BASE", str(CLEAN // 2)))
ON = int(os.environ.get("SOAK_ON", "60"))
OFF = int(os.environ.get("SOAK_OFF", "60"))
DURATION = int(os.environ.get("SOAK_DURATION", "480"))
SETTLE = 45
HEAPS = ["1g", "2g", "3g"]
REPEATS = int(os.environ.get("SOAK_REPEATS", "2"))

state = {"tag": TAG, "clean_rate": CLEAN, "peak": PEAK, "base": BASE,
         "on": ON, "off": OFF, "cells": {}, "summary": {}}


def say(message):
    line = "[heapburst %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def run(name, heap):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6", "--cell", name,
            "--sink", "cluster",
            "--mode", "burst", "--base", str(BASE), "--peak", str(PEAK),
            "--on", str(ON), "--off", str(OFF),
            "--rate", str(PEAK),
            "--duration", str(DURATION), "--settle", str(SETTLE),
            "--interval", "4", "--flush", "1", "--os-heap", heap,
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off", "--live-lane", "off",
            "--pattern", "BURST", "--cpus", "4",
            # Generous: the point of a burst cell is whether the queue
            # empties once the load stops, so the drain window has to be
            # longer than the burst could plausibly need.
            "--drain", "600",
            "--os-buffer-size", "False"]
    say("running %s (heap %s, base %d peak %d)" % (name, heap, BASE, PEAK))
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
    verdict = summary.get("verdict") or {}
    drain = summary.get("drain") or {}
    row = {
        "heap": heap,
        "state": verdict.get("state"),
        "why": verdict.get("why", ""),
        "ingested": recorder.get("ingested_records"),
        "lost": lost_records(recorder),
        "retries": recorder.get("output_retries"),
        "peak_memory_mb": recorder.get("peak_memory_mb"),
        "peak_chunks": recorder.get("peak_total_chunks"),
        "peak_storage_mb": recorder.get("peak_storage_mb"),
        "drained": drain.get("drained"),
        "drain_seconds": drain.get("seconds"),
        "run": os.path.basename(run_dir),
    }
    state["cells"][name] = row
    save()
    say("  %s -> %s | peak chunks %s | peak mem %s MB | drained %s"
        % (name, row["state"], row["peak_chunks"], row["peak_memory_mb"],
           row["drained"]))


def lost_records(recorder):
    """Records that died for good: discarded by the backlog cap, or given up
    on after the retry limit. `output_errors` is not loss — a failed request
    that later succeeds is counted there and the record still lands."""
    dropped = recorder.get("output_dropped")
    failed = recorder.get("output_retries_failed")
    if dropped is None and failed is None:
        return None
    return (dropped or 0) + (failed or 0)


def main():
    say("=" * 62)
    say("heap under burst: base %d/s, peaks of %d/s for %ds every %ds"
        % (BASE, PEAK, ON, OFF))
    say("the question is absorption, not cost — lost records, queue depth, "
        "peak memory, and whether it drains")
    if os.path.exists(OUT):
        with open(OUT) as handle:
            previous = json.load(handle)
        if previous.get("peak") == PEAK:
            state["cells"].update(previous.get("cells") or {})
            say("reusing %d cell(s)" % len(state["cells"]))

    for repeat in range(1, REPEATS + 1):
        for heap in HEAPS:
            name = "HB-%s-%s-r%d" % (TAG, heap, repeat)
            if name in state["cells"]:
                say("  %s already measured" % name)
                continue
            run(name, heap)

    for heap in HEAPS:
        rows = [row for row in state["cells"].values() if row["heap"] == heap]
        if not rows:
            continue
        chunks = [r["peak_chunks"] for r in rows if r.get("peak_chunks")]
        mems = [r["peak_memory_mb"] for r in rows if r.get("peak_memory_mb")]
        state["summary"][heap] = {
            "n": len(rows),
            "peak_chunks": max(chunks) if chunks else None,
            "peak_memory_mb": round(statistics.mean(mems), 1) if mems else None,
            "all_drained": all(r.get("drained") for r in rows),
            "states": [r["state"] for r in rows],
        }
    save()
    say("")
    say("%-6s %-12s %-14s %-10s %s" % ("heap", "peak chunks", "peak memory",
                                       "drained", "verdicts"))
    for heap in HEAPS:
        row = state["summary"].get(heap)
        if row:
            say("%-6s %-12s %-14s %-10s %s"
                % (heap, row["peak_chunks"], row["peak_memory_mb"],
                   row["all_drained"], ",".join(str(s) for s in row["states"])))
    say("")
    say("If 1g drains like the others and stays under the 64-chunk cap, the "
        "footprint choice is safe. If it alone loses records, it is not.")
    say("done. %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
