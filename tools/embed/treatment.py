#!/usr/bin/env python3
"""Stage S11: run today's search as control and the winner as treatment.

This verifies wiring and usefulness on the same development queries. It does not
replace the held-out evaluation and it decides nothing about relevance quality.

What it produces is a per-query account of what changed: which relevant
templates the treatment gained, which the control had and the treatment lost,
and how the two differ in latency. Those gains and losses are the material a
person compares blind.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench


def compare_pair(control, treatment, qrels, queries, cutoff=10):
    rows = []
    gained_total = lost_total = 0
    for qid in queries:
        c = [cid for cid, _ in control["results"].get(qid, [])][:cutoff]
        t = [cid for cid, _ in treatment["results"].get(qid, [])][:cutoff]
        relevant_c = {cid for cid in c if qrels.grade(qid, cid) in bench.RELEVANT}
        relevant_t = {cid for cid in t if qrels.grade(qid, cid) in bench.RELEVANT}
        gained = sorted(relevant_t - relevant_c)
        lost = sorted(relevant_c - relevant_t)
        gained_total += len(gained)
        lost_total += len(lost)
        rows.append({
            "query_id": qid,
            "intent_group": queries[qid]["intent_group"],
            "control_relevant_at_k": len(relevant_c),
            "treatment_relevant_at_k": len(relevant_t),
            "gained": gained, "lost": lost,
            "control_ndcg": bench.ndcg_at_k(c, qrels, qid, cutoff),
            "treatment_ndcg": bench.ndcg_at_k(t, qrels, qid, cutoff),
            "unchanged_top_one": bool(c and t and c[0] == t[0]),
        })
    return rows, gained_total, lost_total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--pairs-out", default=None,
                        help="blind side-by-side file for a preference judge")
    parser.add_argument("--cutoff", type=int, default=10)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)
    control = bench.read_run(args.control)
    treatment = bench.read_run(args.treatment)

    rows, gained, lost = compare_pair(control, treatment, qrels, queries,
                                      args.cutoff)
    changed = [r for r in rows if r["gained"] or r["lost"]]
    better = [r for r in rows
              if r["treatment_ndcg"] is not None and r["control_ndcg"] is not None
              and r["treatment_ndcg"] > r["control_ndcg"]]
    worse = [r for r in rows
             if r["treatment_ndcg"] is not None and r["control_ndcg"] is not None
             and r["treatment_ndcg"] < r["control_ndcg"]]

    payload = {
        "control": control["system"], "treatment": treatment["system"],
        "cutoff": args.cutoff, "queries": len(rows),
        "queries_whose_results_changed": len(changed),
        "queries_where_treatment_scores_higher": len(better),
        "queries_where_control_scores_higher": len(worse),
        "relevant_templates_gained": gained,
        "relevant_templates_lost": lost,
        "queries_with_the_same_top_result": sum(1 for r in rows if r["unchanged_top_one"]),
        "control_latency_ms": control["manifest"].get("engine_latency_ms")
                              or control["manifest"].get("search_ms"),
        "treatment_latency_ms": treatment["manifest"].get("engine_latency_ms")
                                or treatment["manifest"].get("search_ms"),
        "control_errors": 0, "treatment_errors": 0,
        "per_query": rows,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)

    if args.pairs_out:
        import random
        rng = random.Random(20260908)
        pairs = []
        for r in rows:
            if not (r["gained"] or r["lost"]):
                continue
            c = [cid for cid, _ in control["results"].get(r["query_id"], [])][:args.cutoff]
            t = [cid for cid, _ in treatment["results"].get(r["query_id"], [])][:args.cutoff]
            def render(ids):
                return [corpus.by_id[cid]["template"][:160]
                        for cid in ids if cid in corpus.by_id]
            left_is_control = rng.random() < 0.5
            pairs.append({
                "pair_id": r["query_id"],
                "query": queries[r["query_id"]]["text"],
                "list_a": render(c if left_is_control else t),
                "list_b": render(t if left_is_control else c),
                "_which_is_control": "a" if left_is_control else "b",
            })
        rng.shuffle(pairs)
        with open(args.pairs_out, "w") as fh:
            for p in pairs:
                fh.write(json.dumps({k: v for k, v in p.items()
                                     if k != "_which_is_control"}) + "\n")
        with open(args.pairs_out + ".key", "w") as fh:
            json.dump({p["pair_id"]: p["_which_is_control"] for p in pairs}, fh,
                      indent=1)
        payload["blind_pairs"] = len(pairs)
        with open(args.out, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)

    print(json.dumps({k: v for k, v in payload.items() if k != "per_query"},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
