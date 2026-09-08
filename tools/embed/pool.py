#!/usr/bin/env python3
"""Build the judging pool for a query set, in the column shape judgekit deals.

Pooling uses published defaults, never tuned settings. An initial pooling run
cannot promote a system, so the only thing that matters here is that the pool is
diverse enough that a later winner was actually reachable.

Four families contribute: plain BM25, BM25F over the structured fields, and two
dense models that disagree with each other by construction — one small
sentence-transformer and one ModernBERT retrieval model.
"""
import argparse
import json
import os
import sys
import time
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench
import systems

QRELS_COLUMNS = ["query_id", "query", "canonical_id", "grade", "state",
                 "assessor", "prior_binary", "prior_source", "matched_by",
                 "template_then", "template_now"]


def load_query_files(paths, task=None):
    queries = OrderedDict()
    for path in paths:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if task and row.get("task") != task:
                    continue
                queries[row["query_id"]] = row
    return queries


_DENSE_CACHE = {}


def dense_pool(key, corpus, queries, arm, depth, mode="query"):
    """One corpus encode per model and representation, reused across tasks.

    The three task subsets search the same documents, so encoding the corpus
    once per subset would spend the night re-deriving identical vectors.
    """
    cache_key = (key, arm)
    if cache_key not in _DENSE_CACHE:
        builder = (adapters.StaticAdapter if key in adapters.STATIC
                   else adapters.DenseAdapter)
        adapter = builder(key)
        system = systems.DenseSystem(adapter, corpus, arm=arm)
        info = system.index()
        _DENSE_CACHE[cache_key] = (system, info)
    system, info = _DENSE_CACHE[cache_key]
    results, timing = system.run(queries, depth=depth, mode=mode)
    return results, {"index": info, "timing_query_encode_seconds":
                     timing["query_encode_seconds"]}


def merge(pools, depth):
    """One row per query and canonical group, whichever system found it."""
    merged = defaultdict(OrderedDict)
    contributions = defaultdict(int)
    for name, results in pools.items():
        for qid, rows in results.items():
            for cid, _score in rows[:depth]:
                if cid not in merged[qid]:
                    merged[qid][cid] = name
                    contributions[name] += 1
    return merged, dict(contributions)


def write_pool(path, merged, queries, corpus):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    written = 0
    with open(path, "w") as fh:
        fh.write("\t".join(QRELS_COLUMNS) + "\n")
        for qid in queries:
            for cid in merged.get(qid, {}):
                doc = corpus.by_id.get(cid)
                if doc is None:
                    continue
                template = doc["template"].replace("\t", " ")
                row = {
                    "query_id": qid,
                    "query": queries[qid]["text"].replace("\t", " "),
                    "canonical_id": cid,
                    "grade": -1,
                    "state": "unreviewed",
                    "assessor": "",
                    "prior_binary": 0,
                    "prior_source": "pool-2026-09-08",
                    "matched_by": merged[qid][cid],
                    "template_then": template,
                    "template_now": template,
                }
                fh.write("\t".join(str(row[c]) for c in QRELS_COLUMNS) + "\n")
                written += 1
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--deep-depth", type=int, default=200)
    parser.add_argument("--deep-subset", default="")
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    parser.add_argument("--dense", nargs="*", default=["all-MiniLM-L6-v2", "DenseOn"])
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = load_query_files(args.queries)
    natural = {k: v for k, v in queries.items() if v.get("task") == "natural_language"}
    identifier = {k: v for k, v in queries.items() if v.get("task") == "exact_identifier"}
    similarity = {k: v for k, v in queries.items() if v.get("task") == "template_similarity"}

    client = systems.client()
    version = systems.engine_version(client)
    if not client.indices.exists(index=systems.INDEX):
        systems.build_index(client, corpus)

    started = time.time()
    pools, notes = {}, {}
    lexical_targets = OrderedDict()
    if natural:
        lexical_targets["natural"] = natural
    if similarity:
        lexical_targets["similarity"] = similarity

    for label, subset in lexical_targets.items():
        rows, took = systems.run_lexical(client, subset, systems.l0_body,
                                         depth=args.depth)
        pools["L0-%s" % label] = rows
        rows, took = systems.run_lexical(client, subset, systems.l1_body,
                                         depth=args.depth)
        pools["L1-%s" % label] = rows

    if identifier:
        rows, _ = systems.run_lexical(client, identifier, systems.identifier_body,
                                      depth=args.depth)
        pools["ID-exact"] = rows
        rows, _ = systems.run_lexical(client, identifier, systems.l0_body,
                                      depth=args.depth)
        pools["ID-bm25"] = rows

    for key in args.dense:
        for label, subset, mode in (("natural", natural, "query"),
                                    ("similarity", similarity, "symmetric"),
                                    ("identifier", identifier, "query")):
            if not subset:
                continue
            rows, info = dense_pool(key, corpus, subset, args.arm, args.depth,
                                    mode=mode)
            if label == "similarity":
                rows = systems.exclude_self(rows, subset, corpus)
            pools["%s-%s" % (key, label)] = rows
            notes["%s-%s" % (key, label)] = info

    merged, contributions = merge(pools, args.depth)
    written = write_pool(args.out, merged, queries, corpus)

    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus_id": json.load(open(os.path.join(os.path.dirname(args.corpus),
                                                 "manifest.json")))["corpus_id"],
        "opensearch_version": version,
        "representation": args.arm,
        "depth": args.depth,
        "queries": {"total": len(queries), "natural_language": len(natural),
                    "exact_identifier": len(identifier),
                    "template_similarity": len(similarity)},
        "intent_groups": len({q["intent_group"] for q in queries.values()}),
        "systems": sorted(pools),
        "first_finder_counts": contributions,
        "pool_rows": written,
        "pool_rows_per_query": round(written / max(1, len(queries)), 1),
        "dense_notes": notes,
        "seconds": round(time.time() - started, 1),
        "note": ("published defaults only; an initial pooling run cannot "
                 "promote a system"),
    }
    with open(args.report, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("queries", "intent_groups", "pool_rows",
                       "pool_rows_per_query", "first_finder_counts",
                       "seconds")}, indent=2))


if __name__ == "__main__":
    main()
