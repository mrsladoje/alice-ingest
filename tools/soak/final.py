#!/usr/bin/env python3
"""The whole remaining plan, at a rate where nothing is saturated.

The 12,000/s sweep answered the wrong question. Its cells' costs tracked how far
each cell fell BEHIND rather than what its knob did — the two cells that offered
cleanly cost about 77 core-seconds per million, the cell that fell 8 % behind
cost 278. Every comparison at that rate is contaminated by saturation, so the
whole set is repeated here at 5,000 a second, which the worst arm in the plan
(flush 10 on shared cores) offers with no shortfall at all.

**Order is deliberate.** The validation gate runs first, so a rate that cannot
rank stops the run in forty minutes instead of five hours. After that the phases
are ordered by how much the answer is worth, so an interruption loses the least
valuable work rather than the most.

**Repeats where the effect is small.** A 20 % effect needs one cell; a 2 %
effect needs three and a spread of its own. The flush basin and the confirmation
get three runs each, alternating so drift lands on every arm equally.
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
LOG = os.path.join(RUNS, "final.log")
OUT = os.path.join(RUNS, "final-results.json")

RATE = int(os.environ.get("SOAK_RATE", "5000"))
INTERVAL = float(os.environ.get("SOAK_INTERVAL", "4"))
DURATION = int(os.environ.get("SOAK_DURATION", "420"))
SETTLE = 45
LABELS = ["fb", "fb2", "os", "store1", "store2"]

# A cell that fell behind measured saturation, not its knob. At this rate none
# should, and any that does is dropped from its group with a line in the log.
CLEAN_OFFER = 1.0

state = {"rate": RATE, "interval": INTERVAL, "duration": DURATION,
         "cells": {}, "groups": {}, "validated": None}


def say(message):
    line = "[final %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def run(name, **knobs):
    done = state["cells"].get(name)
    if done and done.get("total") is not None:
        say("  %s already measured — %s core-s/M" % (name, done["total"]))
        return done
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6", "--cell", name,
            "--sink", "cluster", "--rate", str(knobs.pop("rate", RATE)),
            "--duration", str(DURATION), "--settle", str(SETTLE),
            "--interval", str(INTERVAL),
            "--flush", str(knobs.pop("flush", 0.5)),
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off",
            "--live-lane", knobs.pop("live_lane", "off"),
            "--pattern", "STEADY", "--os-buffer-size", "False"]
    if "fb_cpuset" in knobs:
        argv += ["--fb-cpuset", knobs.pop("fb_cpuset")]
    else:
        argv += ["--cpus", "4"]
    for flag, key in (("--os-heap", "os_heap"), ("--os-cpuset", "os_cpuset"),
                      ("--arm", "arm"), ("--live-lane-host", "live_lane_host"),
                      ("--live-lane-port", "live_lane_port")):
        if key in knobs:
            argv += [flag, str(knobs.pop(key))]
    if knobs:
        raise SystemExit("final: unknown knobs %s" % sorted(knobs))

    say("running %s" % name)
    subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    fresh = sorted(set(os.listdir(RUNS)) - before)
    run_dir = os.path.join(RUNS, fresh[-1]) if fresh else ""
    row = measure(run_dir)
    row["cell"] = name
    row["run"] = os.path.basename(run_dir)
    state["cells"][name] = row
    save()
    say("  %s -> %s core-s/M, %s MB, offer %+.2f%% %s"
        % (name, row.get("total"), row.get("memory"),
           row.get("gate_error_pct") or 0,
           "" if row.get("clean") else "  <-- FELL BEHIND, excluded"))
    return row


def measure(run_dir):
    try:
        with open(os.path.join(run_dir, "summary.json")) as handle:
            summary = json.load(handle)
    except OSError:
        return {"state": "NO-SUMMARY", "clean": False, "total": None}
    recorder = summary.get("recorder") or {}
    records = recorder.get("ingested_records") or 0
    targets = recorder.get("targets") or {}
    total = sum((targets.get(label) or {}).get("core_seconds", 0)
                for label in LABELS)
    gate = summary.get("gate_zero") or {}
    error = gate.get("error_pct")
    return {
        "state": (summary.get("verdict") or {}).get("state"),
        "why": (summary.get("verdict") or {}).get("why", ""),
        "gate_error_pct": error,
        "records": records,
        "total": round(total * 1e6 / records, 2) if records else None,
        "collector": round((targets.get("fb") or {}).get("core_seconds", 0)
                           * 1e6 / records, 2) if records else None,
        "memory": recorder.get("peak_memory_mb"),
        # `error or 99` reads an exact 0.0 as falsy and substitutes 99, so a
        # cell that offered PERFECTLY was excluded for falling behind. The
        # cleanest cells were the ones being thrown away.
        "clean": records > 0 and abs(99 if error is None else error)
                 <= CLEAN_OFFER,
    }


def cost(name):
    row = state["cells"].get(name) or {}
    return row["total"] if row.get("clean") and row.get("total") else None


def spread(values):
    if not values:
        return None
    mean = statistics.mean(values)
    return {"n": len(values), "mean": round(mean, 2),
            "min": round(min(values), 2), "max": round(max(values), 2),
            "range_pct": round(100.0 * (max(values) - min(values)) / mean, 1)}


def compare(title, arms):
    """arms: {label: [cell names]}. Reports means, spreads and whether the
    winner's margin clears the widest arm's own scatter."""
    table = {}
    for label, names in arms.items():
        values = [cost(name) for name in names]
        values = [value for value in values if value]
        if values:
            table[label] = spread(values)
    if not table:
        say("  %s: nothing clean to compare" % title)
        return None
    state["groups"][title] = table
    save()
    say("")
    say("  %s" % title)
    for label, row in sorted(table.items(), key=lambda kv: kv[1]["mean"]):
        say("    %-14s mean %-8s min %-8s max %-8s spread %.1f%% (n=%d)"
            % (label, row["mean"], row["min"], row["max"],
               row["range_pct"], row["n"]))
    best = min(table, key=lambda label: table[label]["mean"])
    floor = max(row["range_pct"] for row in table.values())
    for label, row in table.items():
        if label == best:
            continue
        gap = 100.0 * (row["mean"] - table[best]["mean"]) / row["mean"]
        verdict = "REAL" if gap > floor else "inside the %.1f%% floor" % floor
        say("    %s beats %-14s by %5.1f%%  — %s" % (best, label, gap, verdict))
    return best


def main():
    say("=" * 64)
    say("the full remaining plan at %d/s — the rate the worst arm offers "
        "cleanly" % RATE)
    if os.path.exists(OUT):
        with open(OUT) as handle:
            previous = json.load(handle)
        if previous.get("rate") == RATE and previous.get("duration") == DURATION:
            state["cells"].update(previous.get("cells") or {})
            say("reusing %d cell(s)" % len(state["cells"]))

    # ---- phase 1: the validation gate -----------------------------------
    say("phase 1: flush grid — the gate")
    for flush in [0.5, 1, 2, 5, 10]:
        run("F-flush-%s" % flush, flush=flush)
    costs = {flush: cost("F-flush-%s" % flush) for flush in [0.5, 1, 2, 5, 10]}
    costs = {flush: value for flush, value in costs.items() if value}
    tail = [costs[flush] for flush in (2, 5, 10) if flush in costs]
    climbs = len(tail) >= 2 and all(b > a for a, b in zip(tail, tail[1:]))
    order = sorted(costs, key=lambda flush: costs[flush])
    cheapest_short = bool(order) and order[0] in (0.5, 1)
    state["validated"] = climbs and cheapest_short
    state["groups"]["flush grid"] = costs
    save()
    say("  ordering %s | tail %s | %s"
        % (order, tail, "rises" if climbs else "does NOT rise"))
    if not state["validated"]:
        say("STOP — at %d/s the flush ordering does not reproduce, so this "
            "rate ranks nothing. Nothing below would mean anything." % RATE)
        return 2
    say("  VALIDATED at %d/s" % RATE)

    # ---- phase 2: the flush basin, three runs each -----------------------
    say("phase 2: the flush basin — 0.5, 0.75, 1, three runs each")
    for repeat in (1, 2, 3):
        for flush in (0.5, 0.75, 1):
            run("F-basin-%s-r%d" % (flush, repeat), flush=flush)
    compare("flush basin", {
        "flush 0.5": ["F-flush-0.5"] + ["F-basin-0.5-r%d" % r for r in (1, 2, 3)],
        "flush 0.75": ["F-basin-0.75-r%d" % r for r in (1, 2, 3)],
        "flush 1": ["F-flush-1"] + ["F-basin-1-r%d" % r for r in (1, 2, 3)],
    })

    # ---- phase 3: below the basin ---------------------------------------
    say("phase 3: below 0.5")
    for flush in (0.25, 0.125):
        run("F-flush-%s" % flush, flush=flush)

    # ---- phase 4: core isolation, the biggest claim ----------------------
    say("phase 4: core isolation — three runs each, alternating")
    for repeat in (1, 2, 3):
        run("F-shared-r%d" % repeat, os_heap="2g")
        run("F-isolated-r%d" % repeat, os_heap="2g",
            fb_cpuset="0", os_cpuset="1-3")
    compare("core isolation", {
        "shared 0-3": ["F-shared-r%d" % r for r in (1, 2, 3)],
        "1 + 3 split": ["F-isolated-r%d" % r for r in (1, 2, 3)],
    })

    # ---- phase 5: the heap grid, twice each ------------------------------
    say("phase 5: heap grid")
    for repeat in (1, 2):
        for heap in ("1g", "2g", "3g"):
            run("F-heap-%s-r%d" % (heap, repeat), os_heap=heap)
    heap = compare("heap grid", {
        heap: ["F-heap-%s-r%d" % (heap, r) for r in (1, 2)]
        for heap in ("1g", "2g", "3g")}) or "2g"

    # ---- phase 6: the rest of the core split -----------------------------
    say("phase 6: the other core splits")
    run("F-split-fb2-shared", fb_cpuset="0-1", os_cpuset="1-3", os_heap=heap)
    run("F-split-fb2-os2", fb_cpuset="0-1", os_cpuset="2-3", os_heap=heap)

    # ---- phase 7: heap x flush, the interaction that flipped -------------
    say("phase 7: heap by flush — does the heap ranking hold?")
    for repeat in (1, 2):
        for flush in (0.5, 5):
            for candidate in ("2g", "3g"):
                run("F-ix-f%s-h%s-r%d" % (flush, candidate, repeat),
                    flush=flush, os_heap=candidate)
    for flush in (0.5, 5):
        compare("heap at flush %s" % flush, {
            candidate: ["F-ix-f%s-h%s-r%d" % (flush, candidate, r)
                        for r in (1, 2)]
            for candidate in ("2g", "3g")})

    # ---- phase 8: threading and the lane at the winners -------------------
    say("phase 8: threading arm and the live lane")
    run("F-t3", arm="t3", os_heap=heap)
    run("F-t0", os_heap=heap)
    run("F-lane-on", live_lane="on", live_lane_host="lane",
        live_lane_port=8092, os_heap=heap)
    compare("threading", {"t0 shipped": ["F-t0"], "t3 two processes": ["F-t3"]})

    # ---- phase 9: confirmation ------------------------------------------
    say("phase 9: confirmation — shipped against chosen, three each")
    for repeat in (1, 2, 3):
        run("F-control-r%d" % repeat, flush=5)
        run("F-chosen-r%d" % repeat, flush=0.5, os_heap=heap,
            fb_cpuset="0", os_cpuset="1-3")
    compare("confirmation", {
        "shipped": ["F-control-r%d" % r for r in (1, 2, 3)],
        "chosen": ["F-chosen-r%d" % r for r in (1, 2, 3)]})

    say("")
    say("ALL PHASES DONE. %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
