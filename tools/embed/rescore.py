#!/usr/bin/env python3
"""Rescore existing run files against a newer judgement set.

Topping up the pool changes the labels, not the rankings. Re-running a model to
get a new number would spend the encode again and risk a different result for a
reason nobody could name, so the run files stay and only the scoring repeats.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--task", default="natural_language")
    parser.add_argument("--reference", default="L0")
    parser.add_argument("--metric", default="ndcg@10")
    parser.add_argument("--out", required=True)
    parser.add_argument("--practical", type=float, default=None)
    args = parser.parse_args()

    qrels = bench.Qrels.load(args.qrels)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task=args.task))

    scored = {}
    for path in args.runs:
        run = bench.read_run(path)
        name = run.get("system") or os.path.basename(path)
        scored[name] = bench.score_run(run, qrels, queries, task=args.task)
        scored[name]["run_file"] = path

    payload = {"qrels": args.qrels, "task": args.task,
               "queries": len(queries),
               "intent_groups": len({q["intent_group"] for q in queries.values()}),
               "arms": {n: {"summary": s["summary"],
                            "intents_scored": s["intents_scored"],
                            "run_file": s["run_file"]}
                        for n, s in scored.items()}}

    if args.reference in scored:
        reference = {g: r.get(args.metric)
                     for g, r in scored[args.reference]["per_intent"].items()}
        comparisons = {}
        for name, result in scored.items():
            if name == args.reference:
                continue
            values = {g: r.get(args.metric)
                      for g, r in result["per_intent"].items()}
            comparisons[name] = bench.bootstrap_paired(values, reference)
        adjusted = bench.holm({n: c["p"] for n, c in comparisons.items()})
        for name in comparisons:
            comparisons[name]["p_holm"] = adjusted[name]
            if args.practical is not None:
                comparisons[name]["beats_practical_difference"] = bool(
                    comparisons[name]["difference"] is not None
                    and comparisons[name]["difference"] > args.practical
                    and comparisons[name]["low"] > 0)
        payload["comparisons"] = {"reference": args.reference,
                                  "metric": args.metric,
                                  "practical_difference": args.practical,
                                  "against_reference": comparisons}
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)

    for name in sorted(payload["arms"]):
        s = payload["arms"][name]["summary"]
        print("%-24s ndcg=%.4f judged10=%.3f mrr=%.4f r20=%.3f" % (
            name, s["ndcg@10"], s["judged@10"], s["mrr@10"], s["recall@20"]))
    if "comparisons" in payload:
        print("\nagainst %s (practical difference %s):" % (args.reference, args.practical))
        for name, c in sorted(payload["comparisons"]["against_reference"].items()):
            if c["difference"] is None:
                continue
            print("  %-24s diff=%+.4f CI[%+.4f,%+.4f] p=%.3f holm=%.3f %s" % (
                name, c["difference"], c["low"], c["high"], c["p"], c["p_holm"],
                "BEATS" if c.get("beats_practical_difference") else ""))


if __name__ == "__main__":
    main()
