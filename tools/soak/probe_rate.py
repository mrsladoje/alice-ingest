#!/usr/bin/env python3
"""Find the highest rate this machine can still offer cleanly.

The rig lost its performance cores mid-plan and now runs everything on four
efficiency cores. Stages D through G cannot run at 20,000 records a second on
that, because gate zero fails before any knob is read.

Lowering the rate is allowed. Lowering it too far is not: stage C already
showed that at 1,000 records a second the flush ordering inverts and ranks
nothing. So the rate has to be the HIGHEST one the machine still offers
cleanly, not merely a rate that passes.

Each rung is a short cell at the control configuration. The first one whose
offer is inside gate zero's tolerance wins.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SOAK = os.path.join(HERE, "soak.py")

LADDER = [12000, 9000, 6000, 4000, 2500]
DURATION = 240
SETTLE = 45
TOLERANCE = 2.0


def newest(before):
    now = set(os.listdir(RUNS))
    fresh = sorted(now - before)
    return os.path.join(RUNS, fresh[-1]) if fresh else ""


def rung(rate):
    before = set(os.listdir(RUNS))
    argv = [sys.executable, SOAK, "run", "p6",
            "--cell", "PROBE-%d" % rate,
            "--sink", "cluster",
            "--rate", str(rate),
            "--duration", str(DURATION),
            "--settle", str(SETTLE),
            "--flush", "0.5",
            "--max-chunks-up", "64",
            "--total-limit-size", "256M",
            "--retry-limit", "10",
            "--storage-type", "filesystem",
            "--pause-on-overlimit", "off",
            "--live-lane", "off",
            "--pattern", "STEADY",
            "--cpus", "4",
            "--os-buffer-size", "False"]
    subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    run = newest(before)
    try:
        with open(os.path.join(run, "summary.json")) as handle:
            summary = json.load(handle)
    except OSError:
        return None, run
    return summary, run


def main():
    results = []
    for rate in LADDER:
        summary, run = rung(rate)
        if not summary:
            print("%6d/s  no summary — the cell did not complete" % rate,
                  flush=True)
            results.append({"rate": rate, "ok": False, "error_pct": None})
            continue
        gate = summary.get("gate_zero") or {}
        error = gate.get("error_pct")
        state = (summary.get("verdict") or {}).get("state")
        ok = abs(error or 99) <= TOLERANCE
        print("%6d/s  offer %+7.2f%%  %-6s %s"
              % (rate, error or 0, state, "USABLE" if ok else ""), flush=True)
        results.append({"rate": rate, "ok": ok, "error_pct": error,
                        "state": state, "run": os.path.basename(run)})
        if ok:
            break
    with open(os.path.join(RUNS, "rate-probe.json"), "w") as handle:
        json.dump(results, handle, indent=2)
    usable = [row for row in results if row["ok"]]
    print("\nhighest usable rate: %s"
          % (usable[0]["rate"] if usable else "NONE on this machine"),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
