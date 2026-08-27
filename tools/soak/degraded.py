#!/usr/bin/env python3
"""The whole comparison set again, at a rate this machine can still offer.

The host lost its performance cores partway through the plan and now runs
everything on four efficiency cores. Twenty thousand records a second no longer
fits: gate zero fails before any knob is read. Lowering the rate is allowed,
because every question left in the plan is a RANKING — which flush, which heap,
which core split — and a ranking survives a slower machine as long as every cell
in the comparison runs on that machine, at that rate, on that instrument.

**What does not survive is the absolute number.** Core-seconds per million
measured here are not comparable with anything taken before 26 August, and the
report says so wherever they appear.

**The validation gate, and the reason this file is not just a rate change.**
Stage C already found that at 1,000 records a second the flush ordering inverts
and ranks nothing. A rate can therefore be low enough to pass gate zero and
still be useless. So the flush grid runs FIRST, and if it does not reproduce the
ordering the healthy rig measured at 20,000 a second — 0.5 cheapest, rising
monotonically through 1, 2, 5 and 10 — then this machine cannot rank anything
and the run stops rather than producing confident nonsense.
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
LOG = os.path.join(RUNS, "degraded.log")
OUT = os.path.join(RUNS, "degraded-results.json")

RATE = int(os.environ.get("SOAK_RATE", "12000"))
INTERVAL = float(os.environ.get("SOAK_INTERVAL", "4"))
DURATION = int(os.environ.get("SOAK_DURATION", "480"))
SETTLE = 45
# fb2 is the t3 arm's second collector process. Leaving it out does not make
# the arm look slightly cheap, it hides half of what the arm costs — the
# collector is split across two processes and only one of them is in the sum.
LABELS = ["fb", "fb2", "os", "store1", "store2"]

# The ordering the healthy rig measured at 20,000 a second. The flush grid has
# to reproduce it or nothing else in this file means anything.
HEALTHY_FLUSH_ORDER = [0.5, 1, 2, 5, 10]

state = {"rate": RATE, "interval": INTERVAL, "cells": {}, "groups": {},
         "validated": None}


def say(message):
    line = "[degraded %s] %s" % (time.strftime("%H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(OUT, "w") as handle:
        json.dump(state, handle, indent=2)


def load_previous():
    """Cells already measured are kept. Re-running them would not only waste an
    hour, it would mix two sittings of the machine into one comparison."""
    if not os.path.exists(OUT):
        return
    with open(OUT) as handle:
        previous = json.load(handle)
    if previous.get("rate") != RATE or previous.get("interval") != INTERVAL:
        say("previous results were taken at a different rate or interval — "
            "not reusing them")
        return
    state["cells"].update(previous.get("cells") or {})
    say("reusing %d cell(s) already measured at this rate"
        % len(state["cells"]))


def run(name, **knobs):
    done = state["cells"].get(name)
    if done and done.get("usable"):
        say("  %s already measured — total %s core-s/M" % (name, done["total"]))
        return done
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6",
            "--cell", name,
            "--sink", "cluster",
            "--rate", str(knobs.pop("rate", RATE)),
            "--duration", str(knobs.pop("duration", DURATION)),
            "--settle", str(SETTLE),
            "--interval", str(INTERVAL),
            "--max-chunks-up", "64",
            "--total-limit-size", "256M",
            "--retry-limit", "10",
            "--storage-type", "filesystem",
            "--pause-on-overlimit", "off",
            "--live-lane", knobs.pop("live_lane", "off"),
            "--pattern", knobs.pop("pattern", "STEADY"),
            "--os-buffer-size", "False",
            "--flush", str(knobs.pop("flush", 0.5))]
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
        raise SystemExit("degraded: unknown knobs %s" % sorted(knobs))

    say("running %s" % name)
    subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    fresh = sorted(set(os.listdir(RUNS)) - before)
    run_dir = os.path.join(RUNS, fresh[-1]) if fresh else ""
    row = measure(run_dir)
    row["cell"] = name
    row["run"] = os.path.basename(run_dir)
    state["cells"][name] = row
    save()
    say("  %s -> %s  total %s core-s/M  mem %s MB"
        % (name, row.get("state"), row.get("total"), row.get("memory")))
    return row


def measure(run_dir):
    try:
        with open(os.path.join(run_dir, "summary.json")) as handle:
            summary = json.load(handle)
    except OSError:
        return {"state": "NO-SUMMARY", "usable": False}
    verdict = (summary.get("verdict") or {}).get("state")
    why = (summary.get("verdict") or {}).get("why", "")
    recorder = summary.get("recorder") or {}
    records = recorder.get("ingested_records") or 0
    targets = recorder.get("targets") or {}
    total = sum((targets.get(label) or {}).get("core_seconds", 0)
                for label in LABELS)
    gate = summary.get("gate_zero") or {}
    return {
        "state": verdict,
        "why": why,
        "gate_error_pct": gate.get("error_pct"),
        "records": records,
        "total": round(total * 1e6 / records, 2) if records else None,
        "collector": round((targets.get("fb") or {}).get("core_seconds", 0)
                           * 1e6 / records, 2) if records else None,
        "memory": recorder.get("peak_memory_mb"),
        # Two different questions, and conflating them cost a run. A cell
        # measures its knob whenever it ingested records — cost is per ingested
        # record, so a short offer does not corrupt it. Whether the machine KEPT
        # UP is a separate axis, and for a flush grid it is a result in its own
        # right: the longer the flush, the further behind the rig falls.
        "usable": records > 0,
        "offer_clean": abs(99 if gate.get("error_pct") is None
                           else gate.get("error_pct")) <= 2.0,
    }


def mean_of(rows, key="total"):
    values = [row[key] for row in rows if row.get("usable") and row.get(key)]
    return round(statistics.mean(values), 2) if values else None


def main():
    say("=" * 62)
    say("rate %d/s, interval %.1fs, duration %ds — the degraded-machine set"
        % (RATE, INTERVAL, DURATION))
    load_previous()

    # ---- 1. the flush grid, which is also the validation gate -----------
    say("phase 1: flush grid — reproduces the healthy ordering, or we stop")
    flush_rows = {}
    for flush in [0.5, 1, 2, 5, 10]:
        flush_rows[flush] = run("DG-flush-%s" % flush, flush=flush)

    costs = {flush: row["total"] for flush, row in flush_rows.items()
             if row.get("usable") and row.get("total")}
    order = sorted(costs, key=lambda flush: costs[flush])
    state["groups"]["flush"] = {str(k): v["total"] for k, v in flush_rows.items()}
    say("  cheapest-first ordering here: %s" % order)
    say("  the healthy rig at 20,000/s gave: %s" % HEALTHY_FLUSH_ORDER)

    # The healthy rig's pattern was a monotonic RISE in cost with flush, with
    # 0.5 and 1 close enough together to sit inside the noise floor. So the gate
    # tests that shape, not a literal ordering: the long tail must climb, and
    # the cheapest value must be one of the two short ones.
    #
    # The first version of this gate asked whether flush 5 or 10 came last
    # among cells that passed gate zero — and excluded exactly those cells for
    # falling behind, which is what made them worst. It stopped a run whose data
    # had reproduced the pattern cleanly.
    tail = [costs[flush] for flush in (2, 5, 10) if flush in costs]
    climbs = len(tail) >= 2 and all(later > earlier
                                    for earlier, later in zip(tail, tail[1:]))
    cheapest_is_short = bool(order) and order[0] in (0.5, 1)
    ok = climbs and cheapest_is_short
    say("  cost across flush 2, 5, 10: %s — %s"
        % (tail, "rises" if climbs else "does not rise"))
    state["validated"] = ok
    save()
    if not ok:
        say("STOP. The flush ordering did not reproduce. At this rate this "
            "machine ranks nothing, exactly as the 1,000/s row did not. "
            "No further cell would mean anything.")
        return 2
    say("  VALIDATED — short flush still wins, long flush still loses. "
        "Rankings taken at this rate are readable.")

    # ---- 2. below the grid's edge, the question that was never answered --
    say("phase 2: below 0.5 — the lower bound the healthy rig never got")
    for flush in [0.25, 0.125]:
        run("DG-flush-%s" % flush, flush=flush)

    # ---- 3. the heap grid ------------------------------------------------
    say("phase 3: heap grid")
    heap_rows = {}
    for heap in ["1g", "2g", "3g"]:
        heap_rows[heap] = run("DG-heap-%s" % heap, os_heap=heap)
    usable = [(h, r["total"]) for h, r in heap_rows.items()
              if r.get("usable") and r.get("total")]
    heap = min(usable, key=lambda pair: pair[1])[0] if usable else "1g"
    state["groups"]["heap"] = {h: r["total"] for h, r in heap_rows.items()}
    say("  heap winner: %s" % heap)
    save()

    # ---- 4. the core split ----------------------------------------------
    say("phase 4: core split")
    splits = {"fb1-os3": ("0", "1-3"), "fb2-shared": ("0-1", "1-3"),
              "fb2-os2": ("0-1", "2-3")}
    split_rows = {}
    for name, (fb, os_set) in splits.items():
        split_rows[name] = run("DG-split-%s" % name, fb_cpuset=fb,
                               os_cpuset=os_set, os_heap=heap)
    usable = [(n, r["total"]) for n, r in split_rows.items()
              if r.get("usable") and r.get("total")]
    split = min(usable, key=lambda pair: pair[1])[0] if usable else "fb1-os3"
    state["groups"]["core_split"] = {n: r["total"] for n, r in split_rows.items()}
    say("  core split winner: %s" % split)
    save()

    # ---- 5. the interaction checks --------------------------------------
    say("phase 5: interactions")
    run("DG-t3-at-winning-flush", arm="t3", os_heap=heap)
    run("DG-lane-on", live_lane="on", live_lane_host="lane",
        live_lane_port=8092, os_heap=heap)
    run("DG-heap-%s-at-flush-5" % heap, flush=5, os_heap=heap)
    runner_up = "2g" if heap != "2g" else "3g"
    run("DG-heap-%s-at-flush-5" % runner_up, flush=5, os_heap=runner_up)

    # ---- 6. confirmation, chosen against shipped, alternating -----------
    say("phase 6: confirmation — chosen against shipped, alternating")
    fb_cpuset, os_cpuset = splits[split]
    for repeat in (1, 2):
        run("DG-control-%d" % repeat, flush=5)
        run("DG-chosen-%d" % repeat, flush=0.5, os_heap=heap,
            fb_cpuset=fb_cpuset, os_cpuset=os_cpuset)

    control = mean_of([state["cells"].get("DG-control-1", {}),
                       state["cells"].get("DG-control-2", {})])
    chosen = mean_of([state["cells"].get("DG-chosen-1", {}),
                      state["cells"].get("DG-chosen-2", {})])
    state["groups"]["confirmation"] = {"shipped": control, "chosen": chosen}
    if control and chosen:
        say("  shipped %s vs chosen %s core-s/M — %.1f%% better"
            % (control, chosen, 100.0 * (control - chosen) / control))
    save()
    say("done. results in %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
