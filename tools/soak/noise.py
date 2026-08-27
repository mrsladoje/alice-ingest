#!/usr/bin/env python3
"""The processor-time noise floor, from repeats of the same cell.

Round 1 did this for memory, found 12 MB, and then ignored every difference
smaller than that. This is the same discipline for processor time, and stage A
exists to produce the number.

**A difference smaller than the noise floor is not a result.** Without this,
stage B would rank four threading arms on gaps that are really run-to-run
scatter.

    python3 tools/soak/noise.py A1 A2 A3
    python3 tools/soak/noise.py --stage A
"""

import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")


def load(run):
    path = os.path.join(RUNS, run, "summary.json")
    with open(path) as handle:
        return json.load(handle)


def index(stage):
    with open(os.path.join(RUNS, "stage-%s-index.json" % stage)) as handle:
        return json.load(handle)


def per_million(summary, label):
    targets = (summary.get("recorder") or {}).get("targets") or {}
    used = (targets.get(label) or {}).get("core_seconds", 0)
    records = (summary.get("recorder") or {}).get("ingested_records", 0)
    return round(used * 1e6 / records, 2) if records else 0


def spread(values):
    if len(values) < 2:
        return {"n": len(values), "mean": values[0] if values else 0}
    mean = statistics.mean(values)
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(mean, 3),
        "stdev": round(statistics.stdev(values), 4),
        "range": round(max(values) - min(values), 3),
        "range_pct": round(100.0 * (max(values) - min(values)) / mean, 2)
        if mean else 0,
    }


def report(groups):
    out = {}
    for name, runs in groups.items():
        summaries = []
        for run in runs:
            try:
                summaries.append(load(run))
            except OSError:
                print("noise: no summary for %s" % run, file=sys.stderr)
        if not summaries:
            continue
        labels = sorted({label
                         for summary in summaries
                         for label in ((summary.get("recorder") or {})
                                       .get("targets") or {})})
        group = {"runs": runs, "rate": summaries[0].get("gun", {})
                 .get("achieved_rate", 0)}
        for label in labels:
            group[label + "_core_seconds"] = spread(
                [((summary.get("recorder") or {}).get("targets") or {})
                 .get(label, {}).get("core_seconds", 0)
                 for summary in summaries])
            group[label + "_per_million"] = spread(
                [per_million(summary, label) for summary in summaries])
        group["peak_memory_mb"] = spread(
            [(summary.get("recorder") or {}).get("peak_memory_mb", 0)
             for summary in summaries])
        out[name] = group
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--stage", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    groups = {}
    if args.stage:
        data = index(args.stage)
        by_rate = {}
        for row in data["cells"]:
            if row.get("verdict") != "PASS" or not row.get("run"):
                continue
            by_rate.setdefault(row["rate"], []).append(row["run"])
        for rate, runs in by_rate.items():
            groups["%s at %s/s" % (args.stage, int(rate))] = runs
    else:
        cells = index("A")["cells"] if not args.runs else []
        lookup = {row["cell"]: row["run"] for row in cells}
        groups["given"] = [lookup.get(name, name) for name in args.runs]

    result = report(groups)
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2)

    print("\n-- the noise floor, stated as the rule stage B will apply --",
          file=sys.stderr)
    for name, group in result.items():
        floor = group.get("fb_per_million", {})
        print("%s: collector core-seconds per million records varies by "
              "%.2f over %d runs (%.2f%% of the mean). A stage B arm that "
              "moves it by less than that has moved nothing."
              % (name, floor.get("range", 0), floor.get("n", 0),
                 floor.get("range_pct", 0)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
