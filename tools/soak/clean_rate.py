#!/usr/bin/env python3
"""Find a rate at which EVERY arm keeps up, not merely the average one.

The 12,000/s sweep produced a near-perfect correlation between a cell's cost
and how far it fell behind: the two cells that offered cleanly cost about 77
core-seconds per million, and the cell that fell 8 % behind cost 278. That is
not a knob being measured, it is saturation being measured, and it contaminates
every comparison taken at that rate.

So the rate has to leave headroom for the WORST arm, not the typical one. This
probes with the most demanding configuration in the plan — flush 10 on shared
cores, which was the slowest cell of the whole sweep — and takes the first rate
where even that offers within half a per cent.

A rate that passes here passes for everything else by construction.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SOAK = os.path.join(HERE, "soak.py")

LADDER = [9000, 7000, 5000, 3500]
DURATION = 300
SETTLE = 45
CLEAN = 0.5


def probe(rate):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6",
            "--cell", "CLEAN-%d" % rate, "--sink", "cluster",
            "--rate", str(rate), "--duration", str(DURATION),
            "--settle", str(SETTLE), "--interval", "4",
            "--flush", "10", "--cpus", "4",
            "--max-chunks-up", "64", "--total-limit-size", "256M",
            "--retry-limit", "10", "--storage-type", "filesystem",
            "--pause-on-overlimit", "off", "--live-lane", "off",
            "--pattern", "STEADY", "--os-buffer-size", "False"]
    subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    fresh = sorted(set(os.listdir(RUNS)) - before)
    run_dir = os.path.join(RUNS, fresh[-1]) if fresh else ""
    try:
        with open(os.path.join(run_dir, "summary.json")) as handle:
            summary = json.load(handle)
    except OSError:
        return None
    return (summary.get("gate_zero") or {}).get("error_pct")


def main():
    results = []
    for rate in LADDER:
        error = probe(rate)
        clean = error is not None and abs(error) <= CLEAN
        print("%6d/s  worst-arm offer %+7.2f%%  %s"
              % (rate, error if error is not None else 0,
                 "CLEAN" if clean else ""), flush=True)
        results.append({"rate": rate, "error_pct": error, "clean": clean})
        if clean:
            break
    with open(os.path.join(RUNS, "clean-rate.json"), "w") as handle:
        json.dump(results, handle, indent=2)
    good = [row for row in results if row["clean"]]
    print("\nclean rate: %s" % (good[0]["rate"] if good else "NONE"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
