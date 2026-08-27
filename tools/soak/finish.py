#!/usr/bin/env python3
"""Stages C-extension through G, unattended, choosing each winner from the runs.

Stage D needs the flush stage C chose, E and G need D's heap, and F's second
half is whatever the three of them settled on. Waiting for a person between
each of those is what makes the back half of the plan take days, so the rules
are written down here instead:

  flush  — lowest mean total core-seconds per million at 20,000/s, but only
           adopted over the next value up if it wins by more than the measured
           1.9 % floor. A tie goes to the LONGER flush, which sends fewer
           requests and is the value already carrying evidence.
  heap   — same rule, same rate, against the same floor.
  cores  — lowest total among the three splits, subject to losing nothing.

Every choice is written to runs/finish-decisions.json with the numbers behind
it, so a choice made at four in the morning can be argued with at nine.
"""

import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
CELLS = os.path.join(HERE, "cells.py")
DECISIONS = os.path.join(RUNS, "finish-decisions.json")
LOG = os.path.join(RUNS, "finish.log")

FLOOR_PCT = 1.9
LABELS = ["fb", "os", "store1", "store2"]

state = {"steps": [], "choices": {}}

STAGE_ORDER = ["C", "D", "E", "G", "F"]


def resume_from(stage):
    """Reload the choices an earlier run already made.

    A stage that dies halfway leaves its winners on disk. Re-deriving them from
    the runs would work, but reading back what was written is the version that
    cannot silently disagree with the log the first run printed.
    """
    if os.path.exists(DECISIONS):
        with open(DECISIONS) as handle:
            state["choices"] = json.load(handle).get("choices", {})
    say("resuming at stage %s with %s"
        % (stage, json.dumps({k: v["chosen"]
                              for k, v in state["choices"].items()})))


def recalled(name):
    return state["choices"][name]["chosen"]


def say(message):
    stamp = time.strftime("%H:%M:%S")
    line = "[finish %s] %s" % (stamp, message)
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def save():
    with open(DECISIONS, "w") as handle:
        json.dump(state, handle, indent=2)


def run_cells(stage, only):
    say("stage %s: running %s" % (stage, ",".join(only)))
    argv = [sys.executable, CELLS, "run", stage, "--only", ",".join(only)]
    code = subprocess.call(argv)
    say("stage %s: cells.py exited %d" % (stage, code))
    return code


def index(stage):
    path = os.path.join(RUNS, "stage-%s-index.json" % stage)
    with open(path) as handle:
        return {row["cell"]: row for row in json.load(handle)["cells"]}


def cost(run):
    """Total core-seconds per million across every service the four cores pay
    for. The generator is rig overhead and is left out on purpose."""
    with open(os.path.join(RUNS, run, "summary.json")) as handle:
        summary = json.load(handle)
    recorder = summary.get("recorder") or {}
    records = recorder.get("ingested_records") or 0
    if not records:
        return None
    targets = recorder.get("targets") or {}
    total = sum((targets.get(label) or {}).get("core_seconds", 0)
                for label in LABELS)
    return {
        "total_per_million": total * 1e6 / records,
        "peak_memory_mb": recorder.get("peak_memory_mb", 0),
        "records": records,
    }


def group(stage, wanted, key):
    """Mean cost per value of `key`, over the cells that passed."""
    rows = index(stage)
    out = {}
    for name, value in wanted.items():
        entry = rows.get(name)
        if not entry or entry.get("verdict") != "PASS" or not entry.get("run"):
            say("  %s: no usable result (%s)"
                % (name, entry.get("verdict") if entry else "missing"))
            continue
        measured = cost(entry["run"])
        if measured:
            out.setdefault(value, []).append(measured)
    return {value: {
        "n": len(items),
        "total_per_million": round(statistics.mean(
            [item["total_per_million"] for item in items]), 2),
        "peak_memory_mb": round(statistics.mean(
            [item["peak_memory_mb"] for item in items]), 1),
    } for value, items in out.items()}


def choose(name, means, order):
    """Cheapest value, but only over the incumbent if it clears the floor.

    `order` runs from the most conservative value to the least, so a tie keeps
    the conservative end.
    """
    ranked = [value for value in order if value in means]
    if not ranked:
        say("  %s: nothing measurable, keeping %s" % (name, order[0]))
        return order[0]
    best = min(ranked, key=lambda value: means[value]["total_per_million"])
    incumbent = ranked[0]
    for value in ranked:
        if value == best:
            break
        gap = (100.0 * (means[value]["total_per_million"]
                        - means[best]["total_per_million"])
               / means[value]["total_per_million"])
        if gap <= FLOOR_PCT:
            say("  %s: %s beats %s by only %.2f%%, inside the %.1f%% floor "
                "— keeping %s" % (name, best, value, gap, FLOOR_PCT, value))
            best = value
            break
    state["choices"][name] = {"chosen": best, "means": means,
                              "incumbent": incumbent}
    save()
    say("  %s: chose %s from %s" % (name, best, json.dumps(means)))
    return best


def patch(replacements, **tokens):
    """Rewrite cells.py. The cell literals contain their own %d and %s, so the
    winners go in as @NAME@ tokens rather than through string formatting."""
    text = open(CELLS).read()
    for old, new in replacements:
        for name, value in tokens.items():
            new = new.replace("@%s@" % name, str(value))
        if text.count(old) == 1:
            text = text.replace(old, new)
        elif text.count(new) >= 1:
            say("  cells.py already carries this edit, leaving it alone")
        else:
            raise SystemExit("finish: cannot patch cells.py on %r" % old[:60])
    open(CELLS, "w").write(text)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "C"
    if start not in STAGE_ORDER:
        raise SystemExit("finish: start stage must be one of %s"
                         % ", ".join(STAGE_ORDER))
    todo = STAGE_ORDER[STAGE_ORDER.index(start):]
    say("=" * 60)
    say("running: %s" % ", ".join(todo))
    if start != "C":
        resume_from(start)

    # ---- stage C, extended below the grid's edge ------------------------
    if "C" in todo:
        run_cells("C", ["C16", "C17", "C18", "C19"])
        wanted = {"C6": 0.5, "C6r1": 0.5, "C6r2": 0.5, "C6r3": 0.5,
                  "C7": 1, "C7r1": 1, "C7r2": 1, "C7r3": 1,
                  "C16": 0.25, "C18": 0.25, "C17": 0.125, "C19": 0.125}
        flush = choose("flush", group("C", wanted, "flush"),
                       [1, 0.5, 0.25, 0.125])
    else:
        flush = recalled("flush")

    # ---- stage D, at that flush -----------------------------------------
    patch([(
        'cell("D%d" % (index + 1), "heap %s at 1,000/s" % value,\n'
        '                       rate=1000, duration=GRID, os_heap=value)',
        'cell("D%d" % (index + 1), "heap %s at 1,000/s" % value,\n'
        '                       rate=1000, duration=GRID, os_heap=value,\n'
        '                       flush=@FLUSH@)',
    ), (
        'cell("D%d" % (index + 4), "heap %s at 20,000/s" % value,\n'
        '                         rate=20000, duration=GRID, os_heap=value)',
        'cell("D%d" % (index + 4), "heap %s at 20,000/s" % value,\n'
        '                         rate=20000, duration=GRID, os_heap=value,\n'
        '                         flush=@FLUSH@)',
    )], FLUSH=flush)
    run_cells("D", ["D1", "D2", "D3", "D4", "D5", "D6"])
    heap = choose("heap", group("D", {"D4": "1g", "D5": "2g", "D6": "3g"},
                                "heap"), ["1g", "2g", "3g"])

    # D7 is the winning heap pushed to 50,000 a second.
    patch([(
        'cell("D7", "the winning heap at 50,000", rate=50000,\n'
        '                         duration=GRID, pattern="OVER")',
        'cell("D7", "the winning heap at 50,000", rate=50000,\n'
        '                         duration=GRID, pattern="OVER",\n'
        '                         flush=@FLUSH@, os_heap="@HEAP@")',
    )], FLUSH=flush, HEAP=heap)
    run_cells("D", ["D7"])

    # ---- stage E, at the winning flush and heap -------------------------
    patch([(
        'cell("E1", "collector 1 core, OpenSearch 3", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0", os_cpuset="1-3"),',
        'cell("E1", "collector 1 core, OpenSearch 3", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0", os_cpuset="1-3",\n'
        '                 flush=@FLUSH@, os_heap="@HEAP@"),',
    ), (
        'cell("E2", "collector 2 cores shared, OpenSearch 3", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0-1", os_cpuset="1-3"),',
        'cell("E2", "collector 2 cores shared, OpenSearch 3", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0-1", os_cpuset="1-3",\n'
        '                 flush=@FLUSH@, os_heap="@HEAP@"),',
    ), (
        'cell("E3", "collector 2 cores, OpenSearch 2", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0-1", os_cpuset="2-3"),',
        'cell("E3", "collector 2 cores, OpenSearch 2", rate=20000,\n'
        '                 duration=GRID, fb_cpuset="0-1", os_cpuset="2-3",\n'
        '                 flush=@FLUSH@, os_heap="@HEAP@"),',
    )], FLUSH=flush, HEAP=heap)
    run_cells("E", ["E1", "E2", "E3"])
    split = choose("core_split",
                   group("E", {"E1": "fb1-os3", "E2": "fb2-shared",
                               "E3": "fb2-os2"}, "split"),
                   ["fb1-os3", "fb2-shared", "fb2-os2"])
    CPUSETS = {"fb1-os3": ("0", "1-3"), "fb2-shared": ("0-1", "1-3"),
               "fb2-os2": ("0-1", "2-3")}
    fb_cpuset, os_cpuset = CPUSETS[split]

    # ---- stage G, the interaction checks --------------------------------
    runner_up = "2g" if heap != "2g" else "3g"
    patch([(
        'cell("G1", "winning threading arm at the winning flush",\n'
        '                 rate=20000, duration=GRID),',
        'cell("G1", "t3, the cheapest threading arm, at the winning flush",\n'
        '                 rate=20000, duration=GRID, arm="t3",\n'
        '                 flush=@FLUSH@, os_heap="@HEAP@"),',
    ), (
        'cell("G2", "lane on at the winning flush", rate=20000,\n'
        '                 duration=GRID, live_lane="on", live_lane_host="lane",\n'
        '                 live_lane_port=8092),',
        'cell("G2", "lane on at the winning flush", rate=20000,\n'
        '                 duration=GRID, live_lane="on", live_lane_host="lane",\n'
        '                 live_lane_port=8092, flush=@FLUSH@,\n'
        '                 os_heap="@HEAP@"),',
    ), (
        'cell("G3", "heap ranking at the shipped flush", rate=20000,\n'
        '                 duration=GRID, flush=5),',
        'cell("G3", "winning heap at the shipped flush", rate=20000,\n'
        '                 duration=GRID, flush=5, os_heap="@HEAP@"),\n'
        '            cell("G4", "runner-up heap at the shipped flush",\n'
        '                 rate=20000, duration=GRID, flush=5,\n'
        '                 os_heap="@RUNNERUP@"),',
    )], FLUSH=flush, HEAP=heap, RUNNERUP=runner_up)
    run_cells("G", ["G1", "G2", "G3", "G4"])

    # ---- stage F, the chosen configuration against the control ----------
    chosen = []
    for number, (rate, repeat) in enumerate(
            [(1000, 1), (1000, 2), (20000, 1), (20000, 2),
             (50000, 1), (50000, 2)], start=7):
        chosen.append(
            'cell("F' + str(number) + '", "chosen, repeat ' + str(repeat)
            + ' at ' + str(rate) + '/s", rate=' + str(rate) + ',\n'
            '                 duration=GRID, flush=' + str(flush)
            + ', os_heap="' + heap + '",\n'
            '                 fb_cpuset="' + fb_cpuset + '", os_cpuset="'
            + os_cpuset + '",\n'
            '                 pattern="'
            + ("OVER" if rate == 50000 else "STEADY") + '"),')
    patch([(
        '                      [(1000, 1), (1000, 2), (20000, 1), (20000, 2),\n'
        '                       (50000, 1), (50000, 2)], start=1)],',
        '                      [(1000, 1), (1000, 2), (20000, 1), (20000, 2),\n'
        '                       (50000, 1), (50000, 2)], start=1)]\n'
        '                 + [\n            ' + "\n            ".join(chosen)
        + '\n        ],',
    )])
    run_cells("F", ["F1", "F7", "F3", "F9", "F5", "F11",
                    "F2", "F8", "F4", "F10", "F6", "F12"])

    state["done"] = True
    save()
    say("all stages finished. decisions in %s" % DECISIONS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
