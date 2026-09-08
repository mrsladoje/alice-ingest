#!/usr/bin/env python3
"""Stage S7: combine ranked lists, and measure whether combining helps at all.

Reciprocal Rank Fusion with the upstream rank constant of 60 is the fixed
control. A tuned fusion is promoted only if it beats that control by more than
the frozen practical difference, and if fusion gives nothing the plan says to
deploy the simpler single system and say so.

Every fused run records the shard topology it was measured on, because a fusion
number from a different topology is a different number.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import compare


def load_runs(paths):
    runs = {}
    for path in paths:
        run = bench.read_run(path)
        runs[run.get("system") or os.path.basename(path)] = run
    return runs


def rank_lists(runs, qid, depth):
    out = {}
    for name, run in runs.items():
        rows = run["results"].get(qid, [])
        out[name] = [cid for cid, _ in rows[:depth]]
    return out


def score_lists(runs, qid, depth):
    out = {}
    for name, run in runs.items():
        rows = run["results"].get(qid, [])
        if rows:
            out[name] = [(cid, float(s)) for cid, s in rows[:depth]]
    return out


def fuse_run(runs, queries, method, depth=200, constant=60, weights=None,
             normaliser=None):
    results = {}
    for qid in queries:
        if method == "rrf":
            fused = bench.rrf(rank_lists(runs, qid, depth), constant=constant,
                              weights=weights)
            scores = {cid: 1.0 / (i + 1) for i, cid in enumerate(fused)}
        else:
            fused = bench.score_fusion(score_lists(runs, qid, depth),
                                       normaliser=normaliser or bench.minmax,
                                       weights=weights)
            scores = {cid: 1.0 / (i + 1) for i, cid in enumerate(fused)}
        results[qid] = [(cid, scores[cid]) for cid in fused[:depth]]
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", required=True,
                        help="name=path pairs")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-dir", default="downloads/frozen/runs")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--practical", type=float, default=0.03197847477701114)
    parser.add_argument("--reference", default="H4-rrf60")
    args = parser.parse_args()

    paths = {}
    for pair in args.systems:
        name, path = pair.split("=", 1)
        paths[name] = path
    runs = {name: bench.read_run(path) for name, path in paths.items()}
    for name in runs:
        runs[name]["system"] = name

    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    lexical = [n for n in runs if n.startswith("lex")]
    dense = [n for n in runs if n.startswith("dense")]
    late = [n for n in runs if n.startswith("late")]

    plan = []
    if lexical:
        plan.append(("H0-lexical", {n: runs[n] for n in lexical}, "single"))
    if dense:
        plan.append(("H2-dense", {n: runs[n] for n in dense}, "single"))
    if late:
        plan.append(("H3-late", {n: runs[n] for n in late}, "single"))
    if lexical and dense:
        pair = {n: runs[n] for n in lexical + dense}
        plan.append(("H4-rrf60", pair, "rrf"))
        plan.append(("H6-quantile", pair, "quantile"))
        plan.append(("H4b-minmax", pair, "minmax"))
    if lexical and dense and late:
        trio = {n: runs[n] for n in lexical + dense + late}
        plan.append(("H9-three-way-rrf", trio, "rrf"))
        plan.append(("H9b-three-way-quantile", trio, "quantile"))

    scored, manifests = {}, {}
    for name, members, method in plan:
        if method == "single":
            only = list(members)[0]
            results = {qid: [(cid, float(s)) for cid, s in
                             members[only]["results"].get(qid, [])[:args.depth]]
                       for qid in queries}
            fusion_parameters = "not applicable, single system"
        elif method == "rrf":
            results = fuse_run(members, queries, "rrf", depth=args.depth)
            fusion_parameters = {"method": "reciprocal rank fusion",
                                 "rank_constant": 60,
                                 "members": sorted(members)}
        elif method == "quantile":
            results = fuse_run(members, queries, "score", depth=args.depth,
                               normaliser=bench.quantile_normalise)
            fusion_parameters = {"method": "quantile-normalised score fusion",
                                 "members": sorted(members)}
        else:
            results = fuse_run(members, queries, "score", depth=args.depth,
                               normaliser=bench.minmax)
            fusion_parameters = {"method": "min-max normalised score fusion",
                                 "members": sorted(members)}

        base = list(members.values())[0]["manifest"]
        manifest = bench.make_manifest(**compare.base_manifest(
            name, "03640622026320ba", "development-97-2026-09-08",
            os.path.basename(args.qrels),
            model_id="+".join(sorted(members)),
            model_revision="see member manifests",
            adapter="fusion of frozen run files",
            representation="member representations",
            search_parameters={"depth": args.depth},
            fusion_parameters=fusion_parameters,
            opensearch_version=base.get("opensearch_version", "3.7.0"),
            index_uuid=base.get("index_uuid", "not applicable"),
            index_schema_revision=base.get("index_schema_revision", "systems.SETTINGS"),
            searched_indices=base.get("searched_indices", "mixed"),
            primary_shards=base.get("primary_shards", 1),
            replicas=base.get("replicas", 0),
            candidate_depth=args.depth,
            refresh_state=base.get("refresh_state", "not applicable"),
            search_pipeline_revision="none",
            device="cpu, fusion is arithmetic on frozen rankings",
            finished=compare.now()))
        path = bench.write_run(os.path.join(args.run_dir, "%s.json" % name),
                               name, name, "natural_language", manifest,
                               results, depth=args.depth)
        scored[name] = bench.score_run(bench.read_run(path), qrels, queries)
        manifests[name] = {"run_file": path, "fusion": fusion_parameters}
        s = scored[name]["summary"]
        print("%-24s ndcg=%.4f judged10=%.3f mrr=%.4f r20=%.3f" % (
            name, s["ndcg@10"], s["judged@10"], s["mrr@10"], s["recall@20"]),
            flush=True)

    payload = {"arms": {n: {"summary": s["summary"],
                            "intents_scored": s["intents_scored"],
                            **manifests[n]} for n, s in scored.items()},
               "practical_difference": args.practical}
    if args.reference in scored:
        reference = {g: r.get("ndcg@10")
                     for g, r in scored[args.reference]["per_intent"].items()}
        comparisons = {}
        for name, result in scored.items():
            if name == args.reference:
                continue
            values = {g: r.get("ndcg@10") for g, r in result["per_intent"].items()}
            comparisons[name] = bench.bootstrap_paired(values, reference)
            comparisons[name]["beats_practical_difference"] = bool(
                comparisons[name]["difference"] is not None
                and comparisons[name]["difference"] > args.practical
                and comparisons[name]["low"] > 0)
        adjusted = bench.holm({n: c["p"] for n, c in comparisons.items()})
        for name in comparisons:
            comparisons[name]["p_holm"] = adjusted[name]
        payload["against_the_fixed_control"] = {
            "control": args.reference, "comparisons": comparisons}
        print("\nagainst %s, the fixed control:" % args.reference)
        for name, c in sorted(comparisons.items()):
            if c["difference"] is None:
                continue
            print("  %-24s diff=%+.4f CI[%+.4f,%+.4f] p=%.3f %s" % (
                name, c["difference"], c["low"], c["high"], c["p"],
                "BEATS" if c["beats_practical_difference"] else ""))
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
