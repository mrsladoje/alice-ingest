#!/usr/bin/env python3
"""Stage S10: score the frozen finalists against the sealed set, once.

This script is run by the custodian, never by the main session. It has two
phases. `--pool` runs the finalists over the sealed queries and writes a judging
pool. `--score` reads the grades back and writes metrics.

Nothing here prints a query or a template. The custodian's reply is counts,
metrics and intervals, and this script's own output obeys the same rule so a
stray console line cannot leak the sealed set.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench
import compare
import systems
from pool import QRELS_COLUMNS

HELDOUT = "downloads/frozen/heldout"

FINALISTS = {
    "P-DenseOn": {"kind": "dense", "model": "DenseOn", "arm": "R1"},
    "P-potion-retrieval": {"kind": "static", "model": "potion-retrieval-32M", "arm": "R1"},
    "L1-BM25F": {"kind": "lexical", "system": "L1", "arm": "R2"},
}


def load_sealed_queries(path, task="natural_language"):
    return bench.load_queries(path, task=task)


def exclude_self(results, queries, corpus):
    """Template similarity never returns the query's own canonical group."""
    out = {}
    for qid, rows in results.items():
        own = queries.get(qid, {}).get("query_canonical_id")
        if not own:
            out[qid] = rows
            continue
        banned = {own} | set(corpus.duplicates.get(own, []))
        out[qid] = [(cid, s) for cid, s in rows if cid not in banned]
    return out


def run_finalists(corpus, queries, depth=200, task="natural_language",
                  suffix=""):
    client = systems.client()
    settings = client.indices.get_settings(index=systems.INDEX)
    key = list(settings)[0]
    info = {"opensearch_version": systems.engine_version(client),
            "index": systems.INDEX,
            "index_uuid": settings[key]["settings"]["index"]["uuid"],
            "primary_shards": 1, "replicas": 0}
    runs = {}
    for name, spec in FINALISTS.items():
        if spec["kind"] == "lexical":
            builder = systems.lexical_builder(spec["system"], spec["arm"])
            results, manifest = compare.lexical_run(
                client, name, queries, builder, "03640622026320ba",
                "heldout-62-2026-09-08", "heldout-qrels", info, depth=depth,
                arm=spec["arm"])
        else:
            results, manifest = compare.dense_run(
                spec["model"], corpus, name, queries, "03640622026320ba",
                "heldout-62-2026-09-08", "heldout-qrels", arm=spec["arm"],
                depth=depth,
                mode="symmetric" if task == "template_similarity" else "query")
        if task == "template_similarity":
            results = exclude_self(results, queries, corpus)
        path = bench.write_run(
            os.path.join(HELDOUT, "run-%s%s.json" % (name, suffix)),
            name, name, task, manifest, results, depth=depth)
        runs[name] = path
    return runs


def write_pool(runs, queries, corpus, out, depth=20):
    seen = {}
    for name, path in runs.items():
        run = bench.read_run(path)
        for qid, rows in run["results"].items():
            for cid, _ in rows[:depth]:
                seen.setdefault(qid, {}).setdefault(cid, name)
    written = 0
    with open(out, "w") as fh:
        fh.write("\t".join(QRELS_COLUMNS) + "\n")
        for qid in queries:
            for cid, finder in seen.get(qid, {}).items():
                doc = corpus.by_id.get(cid)
                if doc is None:
                    continue
                template = doc["template"].replace("\t", " ")
                row = [qid, queries[qid]["text"].replace("\t", " "), cid, -1,
                       "unreviewed", "", 0, "heldout-pool", finder,
                       template, template]
                fh.write("\t".join(str(v) for v in row) + "\n")
                written += 1
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--queries", default=os.path.join(HELDOUT, "queries.jsonl"))
    parser.add_argument("--qrels", default=os.path.join(HELDOUT, "qrels.tsv"))
    parser.add_argument("--pool-out", default=os.path.join(HELDOUT, "pool.tsv"))
    parser.add_argument("--out", default=os.path.join(HELDOUT, "metrics.json"))
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--task", default="natural_language")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--reference", default="L1-BM25F")
    parser.add_argument("--practical", type=float, default=0.03197847477701114)
    parser.add_argument("--detectable", type=float, default=0.052345816167817485)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = load_sealed_queries(args.queries, task=args.task)
    if not queries:
        print(json.dumps({"error": "no queries for task", "task": args.task}))
        return

    if args.pool:
        runs = run_finalists(corpus, queries, depth=200, task=args.task,
                             suffix=args.suffix)
        rows = write_pool(runs, queries, corpus, args.pool_out, depth=args.depth)
        print(json.dumps({"phase": "pool", "systems": sorted(runs),
                          "queries": len(queries),
                          "intent_groups": len({q["intent_group"] for q in queries.values()}),
                          "pool_rows": rows,
                          "pool_file": args.pool_out}, indent=1))
        return

    if not args.score:
        parser.error("pass --pool or --score")

    qrels = bench.Qrels.load(args.qrels)
    scored = {}
    for name in FINALISTS:
        path = os.path.join(HELDOUT, "run-%s%s.json" % (name, args.suffix))
        scored[name] = bench.score_run(bench.read_run(path), qrels, queries,
                                       task=args.task)

    reference = {g: r.get("ndcg@10")
                 for g, r in scored[args.reference]["per_intent"].items()}
    payload = {"phase": "score", "task": args.task, "queries": len(queries),
               "intent_groups": len({q["intent_group"] for q in queries.values()}),
               "judgement_rows": len(qrels.grades),
               "practical_difference": args.practical,
               "smallest_detectable_difference": args.detectable,
               "leaderboard": {}, "against_bm25f": {}, "top_two": None}
    for name, result in scored.items():
        payload["leaderboard"][name] = {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in result["summary"].items()}
        payload["leaderboard"][name]["intents_scored"] = result["intents_scored"]
    for name, result in scored.items():
        if name == args.reference:
            continue
        values = {g: r.get("ndcg@10") for g, r in result["per_intent"].items()}
        boot = bench.bootstrap_paired(values, reference)
        boot["beats_practical_difference"] = bool(
            boot["difference"] is not None and boot["difference"] > args.practical
            and boot["low"] > 0)
        boot["above_what_this_set_can_detect"] = bool(
            boot["difference"] is not None and abs(boot["difference"]) > args.detectable)
        payload["against_bm25f"][name] = boot

    order = sorted(payload["leaderboard"],
                   key=lambda n: -payload["leaderboard"][n]["ndcg@10"])
    if len(order) >= 2:
        a = {g: r.get("ndcg@10") for g, r in scored[order[0]]["per_intent"].items()}
        b = {g: r.get("ndcg@10") for g, r in scored[order[1]]["per_intent"].items()}
        top = bench.bootstrap_paired(a, b)
        top["systems"] = [order[0], order[1]]
        top["above_what_this_set_can_detect"] = bool(
            top["difference"] is not None and abs(top["difference"]) > args.detectable)
        payload["top_two"] = top

    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps(payload, indent=1, default=str))


if __name__ == "__main__":
    main()
