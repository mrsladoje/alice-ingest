#!/usr/bin/env python3
"""The cell manifest from `docs/SOAK_PLAN.md`, run in batches.

Sixty cells at five to twenty minutes each is about ten hours of machine time.
Nobody sits through that, so a stage runs unattended and writes one index of
what it did: every cell, its gate-zero result and its verdict.

    python3 tools/soak/cells.py list A
    python3 tools/soak/cells.py run A
    python3 tools/soak/cells.py run A --from A4

Each cell tears its own volumes down. The plan says teardown belongs between
stages rather than between cells, but a kept volume carries the last cell's
tail files and buffered chunks into the next one, and two cells that share a
buffer are not two cells.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SOAK = os.path.join(HERE, "soak.py")

SCREEN = 300
GRID = 600
SETTLE = 60

# Stage C onward run with one stated deviation from the shipped template:
# the opensearch outputs get a response buffer. Without it the cell measures
# a response-buffer overflow that duplicates records, not the knob under test.
# See "A defect found while commissioning the cluster sink" in SOAK_RESULTS.md.
OS_BUFFER = "False"

CONTROL = {
    "profile": "p6",
    "flush": 5,
    "max_chunks_up": 64,
    "total_limit_size": "256M",
    "retry_limit": 10,
    "storage_type": "filesystem",
    "cpus": 4,
    "live_lane": "off",
    "settle": SETTLE,
}


def cell(name, why, **overrides):
    entry = dict(CONTROL)
    entry.update(overrides)
    entry["cell"] = name
    entry["why"] = why
    return entry


STAGES = {
    "A": {
        "what": "instrument and noise floor — nothing is comparable until "
                "processor time is sampled and the noise floor is a number",
        "sink": "http",
        "cells": [
            cell("A1", "control, repeat 1 of 3", rate=1000, duration=SCREEN),
            cell("A2", "control, repeat 2 of 3", rate=1000, duration=SCREEN),
            cell("A3", "control, repeat 3 of 3", rate=1000, duration=SCREEN),
            cell("A4", "control, repeat 1 of 3", rate=20000, duration=SCREEN),
            cell("A5", "control, repeat 2 of 3", rate=20000, duration=SCREEN),
            cell("A6", "control, repeat 3 of 3", rate=20000, duration=SCREEN),
        ],
        "note": "A7 is the generator selftest and is not a rig cell: "
                "python3 tools/soak/soak.py selftest --rates 1000,20000,50000",
    },
    "B": {
        "what": "collector screen — threading, the live lane and the "
                "InfoLogger tap, all at the shipped flush of 5, collector only",
        "sink": "http",
        "cells": [
            cell("B1", "t0 as shipped", rate=20000, duration=SCREEN),
            cell("B2", "t1 threaded inputs", rate=20000, duration=SCREEN,
                 arm="t1"),
            cell("B3", "t2 filters as processors", rate=20000, duration=SCREEN,
                 arm="t2"),
            cell("B4", "t3 two collector processes", rate=20000,
                 duration=SCREEN, arm="t3"),
            cell("B5", "t0 at the ceiling", rate=50000, duration=GRID,
                 pattern="OVER"),
            cell("B6", "t2 at the ceiling — the only arm that empties the "
                       "main loop", rate=50000, duration=GRID, arm="t2",
                 pattern="OVER"),
            cell("B7", "l1 lane on, real live_lane.py", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="lane",
                 live_lane_port=8092),
            cell("B8", "lw lane output at workers 0", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="lane",
                 live_lane_port=8092, output_workers=0),
            cell("B9", "lt lane on its own tag", rate=20000, duration=SCREEN,
                 live_lane="on", live_lane_host="lane", live_lane_port=8092,
                 lane_own_tag="on"),
            cell("B10", "lv lane server alone, 5 viewers", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="lane",
                 live_lane_port=8092, viewers=5),
            cell("B11", "lv lane server alone, 20 viewers", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="lane",
                 live_lane_port=8092, viewers=20),
            # The lane compresses on the worker, which is the scarce side.
            # All three share the fake sink so they compare to each other;
            # the real lane server cannot decompress zstd.
            cell("B16", "lane compression OFF — the ceiling on any swap",
                 rate=20000, duration=SCREEN, live_lane="on",
                 live_lane_host="sink2", live_lane_port=9200,
                 lane_compress="off"),
            cell("B17", "lane gzip — as shipped, same receiver",
                 rate=20000, duration=SCREEN, live_lane="on",
                 live_lane_host="sink2", live_lane_port=9200,
                 lane_compress="gzip"),
            cell("B18", "lane zstd — the 2026 candidate",
                 rate=20000, duration=SCREEN, live_lane="on",
                 live_lane_host="sink2", live_lane_port=9200,
                 lane_compress="zstd"),
            cell("B17r1", "lane gzip, repeat 1 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="gzip"),
            cell("B18r1", "lane zstd, repeat 1 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="zstd"),
            cell("B17r2", "lane gzip, repeat 2 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="gzip"),
            cell("B18r2", "lane zstd, repeat 2 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="zstd"),
            cell("B17r3", "lane gzip, repeat 3 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="gzip"),
            cell("B18r3", "lane zstd, repeat 3 of 3", rate=20000,
                 duration=SCREEN, live_lane="on", live_lane_host="sink2",
                 live_lane_port=9200, lane_compress="zstd"),
            cell("B12", "s0 InfoLogger over TCP, 15 min sink outage, 21 min cell",
                 rate=20000,
                 duration=1200, pattern="OUTAGE", fault_at=180,
                 fault_seconds=900),
            cell("B13", "s1 through the appender, 15 min sink outage, 21 min cell",
                 rate=20000, duration=1200, pattern="OUTAGE", fault_at=180,
                 fault_seconds=900, infologger_tap="file"),
            # B12/B13 tested the tap and the pause as alternatives. They are
            # complementary: pausing only helps if the backlog has somewhere
            # to wait, and a file is the only thing that gives it one.
            cell("B14", "s1 through the appender, WITH pause on overlimit",
                 rate=20000, duration=1200, pattern="OUTAGE", fault_at=180,
                 fault_seconds=900, infologger_tap="file",
                 pause_on_overlimit="on"),
            cell("B15", "s0 over TCP, WITH pause on overlimit — is the file "
                        "even needed?", rate=20000, duration=1200,
                 pattern="OUTAGE", fault_at=180, fault_seconds=900,
                 pause_on_overlimit="on"),
        ],
        "note": "B6 was filled in after B1-B4 were read. On cost the winner "
                "is t0, which B5 already measured at 50,000, so repeating it "
                "would answer nothing; B6 runs t2, the only arm that empties "
                "the main loop and so the only candidate for raising a "
                "ceiling the main loop sets. B10 and "
                "B11 measure the lane SERVER, not the collector — their "
                "viewers are pinned to the storage tier's idle cores so they "
                "steal from neither. B12 and B13 are 21-minute cells, not "
                "20: 180s of lead, then the 900s outage, then 180s of load "
                "after the restore. The OUTAGE LENGTH is what makes them "
                "comparable to round 1's loss finding, so it is 900s exactly "
                "and the cell is longer to fit it. B14 and B15 were added "
                "after B12 and B13: pausing the input only helps if the "
                "backlog has somewhere to wait, so the tap and the pause are "
                "complementary rather than alternatives. B15 is expected to "
                "back-pressure its sender; that is the finding, not a fault.",
    },
    "C": {
        "what": "the flush grid — the memory-against-cores trade, with "
                "OpenSearch in the path",
        "sink": "cluster",
        "cells": [cell("C%d" % (index + 1),
                       "flush %s at 1,000/s" % value, rate=1000,
                       duration=GRID, flush=value)
                  for index, value in enumerate([0.5, 1, 2, 5, 10])]
                 + [cell("C%d" % (index + 6),
                         "flush %s at 20,000/s" % value, rate=20000,
                         duration=GRID, flush=value)
                    for index, value in enumerate([0.5, 1, 2, 5, 10])]
                 + [cell("C%d" % (index + 11),
                         "flush %s at 50,000/s" % value, rate=50000,
                         duration=GRID, flush=value, pattern="BURST",
                         mode="burst", peak=50000, base=5000, on=30, off=120)
                    for index, value in enumerate([0.5, 1, 2, 5, 10])]
                 + [cell("C6r1", "flush 0.5 at 20,000/s, repeat 1 of 3", rate=20000,
                 duration=GRID, flush=0.5),
cell("C7r1", "flush 1 at 20,000/s, repeat 1 of 3", rate=20000,
                 duration=GRID, flush=1),
cell("C6r2", "flush 0.5 at 20,000/s, repeat 2 of 3", rate=20000,
                 duration=GRID, flush=0.5),
cell("C7r2", "flush 1 at 20,000/s, repeat 2 of 3", rate=20000,
                 duration=GRID, flush=1),
cell("C6r3", "flush 0.5 at 20,000/s, repeat 3 of 3", rate=20000,
                 duration=GRID, flush=0.5),
cell("C7r3", "flush 1 at 20,000/s, repeat 3 of 3", rate=20000,
                 duration=GRID, flush=1),
cell("C16", "flush 0.25 at 20,000/s, run 1 of 2", rate=20000,
                 duration=GRID, flush=0.25),
cell("C17", "flush 0.125 at 20,000/s, run 1 of 2", rate=20000,
                 duration=GRID, flush=0.125),
cell("C18", "flush 0.25 at 20,000/s, run 2 of 2", rate=20000,
                 duration=GRID, flush=0.25),
cell("C19", "flush 0.125 at 20,000/s, run 2 of 2", rate=20000,
                 duration=GRID, flush=0.125)],
"extra": True,
        "note": "the 50,000 row is BURST, not sustained: a sustained 50,000 "
                "into OpenSearch saturates identically at every flush value "
                "and separates nothing.",
    },
    "D": {
        "what": "the heap grid, at the flush stage C chose",
        "sink": "cluster",
        "cells": [cell("D%d" % (index + 1), "heap %s at 1,000/s" % value,
                       rate=1000, duration=GRID, os_heap=value,
                       flush=0.5)
                  for index, value in enumerate(["1g", "2g", "3g"])]
                 + [cell("D%d" % (index + 4), "heap %s at 20,000/s" % value,
                         rate=20000, duration=GRID, os_heap=value,
                         flush=0.5)
                    for index, value in enumerate(["1g", "2g", "3g"])]
                 + [cell("D7", "the winning heap at 50,000", rate=50000,
                         duration=GRID, pattern="OVER")],
        "note": "run this with --flush <winner from C>. D7 needs the winning "
                "heap set explicitly.",
    },
    "E": {
        "what": "the core split, at the winning flush and heap",
        "sink": "cluster",
        "cells": [
            cell("E1", "collector 1 core, OpenSearch 3", rate=20000,
                 duration=GRID, fb_cpuset="0", os_cpuset="1-3"),
            cell("E2", "collector 2 cores shared, OpenSearch 3", rate=20000,
                 duration=GRID, fb_cpuset="0-1", os_cpuset="1-3"),
            cell("E3", "collector 2 cores, OpenSearch 2", rate=20000,
                 duration=GRID, fb_cpuset="0-1", os_cpuset="2-3"),
        ],
        "note": "the plan's 1.5-core cell has no cpuset equivalent, because "
                "pinning cannot halve a core. E2 overlaps the two services on "
                "core 1 instead, which is the nearest honest thing, and it is "
                "recorded as a divergence rather than as 1.5 cores.",
    },
    "F": {
        "what": "confirmation — the chosen configuration against the shipped "
                "control, at all three rates, twice each",
        "sink": "cluster",
        "cells": [cell("F%d" % index,
                       "control, repeat %d at %s/s" % (repeat, rate),
                       rate=rate, duration=GRID,
                       pattern="OVER" if rate == 50000 else "STEADY")
                  for index, (rate, repeat) in enumerate(
                      [(1000, 1), (1000, 2), (20000, 1), (20000, 2),
                       (50000, 1), (50000, 2)], start=1)],
        "note": "the other six cells are the CHOSEN configuration and cannot "
                "be written until stages C, D and E have chosen it.",
    },
    "G": {
        "what": "the interaction checks — the three assumptions tested "
                "rather than trusted",
        "sink": "cluster",
        "cells": [
            cell("G1", "winning threading arm at the winning flush",
                 rate=20000, duration=GRID),
            cell("G2", "lane on at the winning flush", rate=20000,
                 duration=GRID, live_lane="on", live_lane_host="lane",
                 live_lane_port=8092),
            cell("G3", "heap ranking at the shipped flush", rate=20000,
                 duration=GRID, flush=5),
        ],
        "note": "every cell here needs the winners from C, D and B passed on "
                "the command line. If the lane-by-flush check flips a "
                "ranking, stage C's winner is re-opened.",
    },
}


def argv_for(entry, sink):
    argv = [sys.executable, SOAK, "run", entry["profile"],
            "--cell", entry["cell"],
            "--sink", sink,
            "--rate", str(entry["rate"]),
            "--duration", str(entry["duration"]),
            "--settle", str(entry["settle"]),
            "--flush", str(entry["flush"]),
            "--max-chunks-up", str(entry["max_chunks_up"]),
            "--total-limit-size", entry["total_limit_size"],
            "--retry-limit", str(entry["retry_limit"]),
            "--storage-type", entry["storage_type"],
            "--pause-on-overlimit", entry.get("pause_on_overlimit", "off"),
            "--live-lane", entry["live_lane"],
            "--pattern", entry.get("pattern", "STEADY")]
    if entry.get("fb_cpuset"):
        argv += ["--fb-cpuset", entry["fb_cpuset"]]
    else:
        argv += ["--cpus", str(entry["cpus"])]
    if sink == "cluster":
        argv += ["--os-buffer-size", OS_BUFFER]
    if entry.get("os_heap"):
        argv += ["--os-heap", entry["os_heap"]]
    if entry.get("os_cpuset"):
        argv += ["--os-cpuset", entry["os_cpuset"]]
    if entry.get("arm"):
        argv += ["--arm", entry["arm"]]
    if entry.get("lane_own_tag"):
        argv += ["--lane-own-tag", entry["lane_own_tag"]]
    if entry.get("infologger_tap"):
        argv += ["--infologger-tap", entry["infologger_tap"]]
    if entry.get("live_lane_host"):
        argv += ["--live-lane-host", entry["live_lane_host"],
                 "--live-lane-port", str(entry.get("live_lane_port", 9200))]
    if entry.get("output_workers") is not None:
        argv += ["--output-workers", str(entry["output_workers"])]
    if entry.get("viewers"):
        argv += ["--viewers", str(entry["viewers"])]
    if entry.get("lane_compress"):
        argv += ["--lane-compress", entry["lane_compress"]]
    if entry.get("mode"):
        argv += ["--mode", entry["mode"]]
        for key, flag in (("peak", "--peak"), ("base", "--base"),
                          ("on", "--on"), ("off", "--off")):
            if entry.get(key) is not None:
                argv += [flag, str(entry[key])]
    if entry.get("fault_at") is not None:
        argv += ["--fault-at", str(entry["fault_at"]),
                 "--fault-seconds", str(entry["fault_seconds"])]
    return argv


def latest_run(before):
    """soak.py names its own directory, so the newest one that did not exist
    before the cell started is this cell's."""
    now = set(os.listdir(RUNS)) if os.path.isdir(RUNS) else set()
    fresh = sorted(now - before)
    return os.path.join(RUNS, fresh[-1]) if fresh else ""


def read_summary(run_dir):
    try:
        with open(os.path.join(run_dir, "summary.json")) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def cmd_list(args):
    stage = STAGES[args.stage]
    print("stage %s — %s" % (args.stage, stage["what"]))
    total = 0
    for entry in stage["cells"]:
        minutes = (entry["duration"] + entry["settle"]) / 60.0
        total += minutes
        print("  %-4s %-34s %6.0f/s  %5.1f min  %s"
              % (entry["cell"], entry["why"], entry["rate"], minutes,
                 entry.get("pattern", "STEADY")))
    print("  %d cells, about %.0f minutes of load plus teardown" %
          (len(stage["cells"]), total))
    if stage.get("note"):
        print("  note: %s" % stage["note"])
    return 0


def cmd_run(args):
    stage = STAGES[args.stage]
    cells = stage["cells"]
    if args.only:
        cells = [entry for entry in cells if entry["cell"] in args.only.split(",")]
    elif args.start:
        names = [entry["cell"] for entry in cells]
        if args.start not in names:
            raise SystemExit("cells: no cell named %s in stage %s"
                             % (args.start, args.stage))
        cells = cells[names.index(args.start):]

    index_path = os.path.join(RUNS, "stage-%s-index.json" % args.stage)
    index = {"stage": args.stage, "what": stage["what"],
             "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "cells": []}
    if args.start or args.only:
        try:
            with open(index_path) as handle:
                index = json.load(handle)
        except (OSError, ValueError):
            pass

    for entry in cells:
        before = set(os.listdir(RUNS)) if os.path.isdir(RUNS) else set()
        argv = argv_for(entry, stage["sink"])
        print("\n[cells] %s — %s" % (entry["cell"], entry["why"]), flush=True)
        started = time.time()
        proc = subprocess.run(argv, check=False)
        run_dir = latest_run(before)
        summary = read_summary(run_dir)
        record = {
            "cell": entry["cell"],
            "why": entry["why"],
            "rate": entry["rate"],
            "pattern": entry.get("pattern", "STEADY"),
            "run": os.path.basename(run_dir),
            "minutes": round((time.time() - started) / 60.0, 1),
            "exit": proc.returncode,
            "verdict": (summary.get("verdict") or {}).get("state", "?"),
            "why_verdict": (summary.get("verdict") or {}).get("why", ""),
            "gate_zero_pct": (summary.get("gate_zero") or {}).get("error_pct"),
            "targets": {label: value.get("core_seconds")
                        for label, value
                        in ((summary.get("recorder") or {})
                            .get("targets") or {}).items()},
        }
        index["cells"] = [row for row in index["cells"]
                          if row["cell"] != entry["cell"]] + [record]
        with open(index_path, "w") as handle:
            json.dump(index, handle, indent=2)
        print("[cells] %s -> %s (%s)" % (entry["cell"], record["verdict"],
                                         record["run"]), flush=True)

    subprocess.run([sys.executable, SOAK, "down"], check=False)
    print("\n[cells] stage %s done, index at %s" % (args.stage, index_path))
    for row in sorted(index["cells"], key=lambda item: item["cell"]):
        print("  %-4s %-12s %s" % (row["cell"], row["verdict"], row["run"]))
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    lister = sub.add_parser("list")
    lister.add_argument("stage", choices=sorted(STAGES))
    lister.set_defaults(func=cmd_list)

    runner = sub.add_parser("run")
    runner.add_argument("stage", choices=sorted(STAGES))
    runner.add_argument("--from", dest="start", default="")
    runner.add_argument("--only", default="")
    runner.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
