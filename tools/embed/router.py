#!/usr/bin/env python3
"""Is a query router worth building, or is one dense model enough?

Sweet Search routes with a CatBoost model over three classes. The plan forbids
training a router on a query set this small, so this measures the two things
that decide the question without training anything:

A **rule router**, which sends a query that looks like a literal identifier to
the lexical engine and everything else to the dense model. Deterministic, no
fitting, inspectable.

An **oracle router**, which picks whichever engine actually scored better on each
query. It cannot be built — it reads the answers — but it is the ceiling on what
*any* router could achieve. If the oracle barely beats one model alone, no router
is worth writing.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench

LITERAL = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*(?:[A-Z][a-z0-9]*)+"      # camelCase
    r"|[A-Z][A-Z0-9_]{2,}"                                  # SCREAMING_CASE
    r"|[A-Za-z0-9_]*_[A-Za-z0-9_]+"                         # snake_case
    r"|0[xX][0-9a-fA-F]+"                                   # hexadecimal
    r"|/[A-Za-z0-9_./-]+"                                   # a path
    r"|[A-Za-z][A-Za-z0-9]*::[A-Za-z0-9_:]+"                # a C++ scope
    r")$")


def looks_literal(text):
    """One token that reads like something an operator pasted, not typed."""
    stripped = text.strip()
    if " " in stripped:
        return False
    return bool(LITERAL.match(stripped))


def route(queries):
    return {qid: ("lexical" if looks_literal(row["text"]) else "dense")
            for qid, row in queries.items()}


def combine(runs, choice):
    return {qid: runs[choice[qid]]["results"].get(qid, []) for qid in choice}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", nargs="+", required=True, help="one or more run files")
    parser.add_argument("--lexical", nargs="+", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", nargs="+", required=True)
    parser.add_argument("--out", default="downloads/frozen/router.json")
    parser.add_argument("--task", default="natural_language")
    args = parser.parse_args()

    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task=args.task))
    grades = {}
    for path in args.qrels:
        grades.update(bench.Qrels.load(path).grades)
    qrels = bench.Qrels(grades)

    dense = {"results": {}}
    for path in args.dense:
        dense["results"].update(bench.read_run(path)["results"])
    lexical = {"results": {}}
    for path in args.lexical:
        lexical["results"].update(bench.read_run(path)["results"])
    runs = {"dense": dense, "lexical": lexical}

    scored = {}
    for name in ("dense", "lexical"):
        scored[name] = bench.score_run(runs[name], qrels, queries)

    covered = set(dense["results"]) & set(lexical["results"])
    queries = {k: v for k, v in queries.items() if k in covered}
    for name in ("dense", "lexical"):
        scored[name] = bench.score_run(runs[name], qrels, queries)
    choice = route(queries)
    routed = {"results": combine(runs, choice)}
    scored["rule_router"] = bench.score_run(routed, qrels, queries)

    per_query = {}
    for name in ("dense", "lexical"):
        for qid, metrics in scored[name]["per_query"].items():
            per_query.setdefault(qid, {})[name] = metrics.get("ndcg@10")
    oracle_choice = {}
    for qid, values in per_query.items():
        d, l = values.get("dense"), values.get("lexical")
        oracle_choice[qid] = "lexical" if (l is not None and (d is None or l > d)) else "dense"
    oracle = {"results": {qid: runs[c]["results"].get(qid, [])
                          for qid, c in oracle_choice.items()}}
    scored["oracle_router"] = bench.score_run(oracle, qrels, queries)

    dense_intent = {g: r.get("ndcg@10")
                    for g, r in scored["dense"]["per_intent"].items()}
    report = {"queries": len(queries),
              "routed_to_lexical": sum(1 for v in choice.values() if v == "lexical"),
              "oracle_would_route_to_lexical": sum(1 for v in oracle_choice.values()
                                                   if v == "lexical"),
              "rule_agrees_with_oracle": sum(1 for q in choice
                                             if choice[q] == oracle_choice.get(q)),
              "summaries": {}, "against_dense": {}}
    for name, result in scored.items():
        report["summaries"][name] = {k: (round(v, 4) if isinstance(v, float) else v)
                                     for k, v in result["summary"].items()}
        if name != "dense":
            vals = {g: r.get("ndcg@10") for g, r in result["per_intent"].items()}
            report["against_dense"][name] = bench.bootstrap_paired(vals, dense_intent)

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print("queries %d, rule sends %d to lexical, oracle would send %d, they agree on %d"
          % (report["queries"], report["routed_to_lexical"],
             report["oracle_would_route_to_lexical"], report["rule_agrees_with_oracle"]))
    for name in ("dense", "lexical", "rule_router", "oracle_router"):
        s = report["summaries"][name]
        print("  %-14s ndcg@10=%.4f mrr@10=%.4f success@5=%.3f"
              % (name, s["ndcg@10"], s["mrr@10"], s["success@5"]))
    print("\nagainst the dense model alone:")
    for name, c in report["against_dense"].items():
        if c["difference"] is None:
            continue
        print("  %-14s %+.4f CI[%+.4f,%+.4f] p=%.3f"
              % (name, c["difference"], c["low"], c["high"], c["p"]))


if __name__ == "__main__":
    main()
