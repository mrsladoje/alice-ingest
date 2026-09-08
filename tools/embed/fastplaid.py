#!/usr/bin/env python3
"""S6 approximate multi-vector search, measured against the exhaustive oracle.

The plan's order is explicit: exhaustive MaxSim is the ranking oracle, and an
approximate index is only allowed after export parity is verified against native
scores inside a stated tolerance. Losing more than that tolerance is a stop rule.

This is the piece that would make late interaction deployable over the whole
corpus rather than over a reranked candidate list.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import compare
from hybrids import LateReranker


def load_cache(path):
    blob = np.load(path, allow_pickle=True)
    tokens, offsets = blob["tokens"], blob["offsets"]
    ids = list(blob["ids"])
    docs = [tokens[offsets[i]:offsets[i + 1]] for i in range(len(ids))]
    return docs, ids, str(blob["model"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default="downloads/frozen/lateon-tokens.npz")
    parser.add_argument("--oracle-run", default="downloads/frozen/runs/S6-LateOn.json")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/s6-fastplaid.json")
    parser.add_argument("--index", default="downloads/frozen/fastplaid-index")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--nbits", type=int, default=4)
    parser.add_argument("--tolerance", type=float, default=0.95,
                        help="mean top-10 overlap with the exhaustive oracle")
    args = parser.parse_args()

    import torch
    from fast_plaid import search as fp

    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    docs, ids, model = load_cache(args.cache)
    tensors = [torch.tensor(d, dtype=torch.float32) for d in docs]

    import shutil
    if os.path.exists(args.index):
        shutil.rmtree(args.index)
    started = time.time()
    index = fp.FastPlaid(index=args.index, device="cpu")
    index.create(documents_embeddings=tensors, nbits=args.nbits)
    build_seconds = time.time() - started
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _d, fs in os.walk(args.index) for f in fs)

    reranker = LateReranker(args.cache)
    texts = [row["text"] for row in queries.values()]
    query_vectors = reranker.encode_queries(texts)
    qt = [torch.tensor(np.asarray(v, dtype=np.float32)) for v in query_vectors]

    started = time.time()
    hits = index.search(queries_embeddings=qt, top_k=args.depth,
                        show_progress=False)
    search_seconds = time.time() - started

    results = {}
    for (qid, _row), rows in zip(queries.items(), hits):
        results[qid] = [(ids[i], float(s)) for i, s in rows]

    oracle = bench.read_run(args.oracle_run)
    overlaps, taus = [], []
    for qid in queries:
        gold = [c for c, _ in oracle["results"].get(qid, [])][:10]
        got = [c for c, _ in results.get(qid, [])][:10]
        overlaps.append(len(set(gold) & set(got)) / 10.0)
    mean_overlap = float(np.mean(overlaps))

    manifest = bench.make_manifest(**compare.base_manifest(
        "S6-FastPlaid", "03640622026320ba", "development-97-2026-09-08",
        os.path.basename(args.qrels), model_id=model,
        model_revision="see LateOn adapter", adapter="fast-plaid approximate MaxSim",
        representation="R1",
        search_parameters={"nbits": args.nbits, "top_k": args.depth},
        fusion_parameters="not applicable", opensearch_version="not applicable",
        index_uuid="not applicable", index_schema_revision="fast-plaid",
        searched_indices=[args.index], primary_shards="not applicable",
        replicas="not applicable", candidate_depth=args.depth,
        refresh_state="not applicable", search_pipeline_revision="not applicable",
        device="cpu", finished=compare.now()))
    manifest["index_bytes"] = size
    manifest["index_build_seconds"] = round(build_seconds, 1)
    manifest["search_seconds_for_all_queries"] = round(search_seconds, 2)
    path = bench.write_run("downloads/frozen/runs/S6-FastPlaid.json", "S6-FastPlaid",
                           "S6-FastPlaid", "natural_language", manifest, results,
                           depth=args.depth)
    approx = bench.score_run(bench.read_run(path), qrels, queries)
    exact = bench.score_run(oracle, qrels, queries)
    a = {g: r.get("ndcg@10") for g, r in approx["per_intent"].items()}
    b = {g: r.get("ndcg@10") for g, r in exact["per_intent"].items()}
    boot = bench.bootstrap_paired(a, b)

    payload = {
        "model": model, "nbits": args.nbits,
        "index_bytes": size,
        "index_megabytes": round(size / 1e6, 1),
        "token_matrix_megabytes": round(reranker.tokens.nbytes / 1e6, 1),
        "build_seconds": round(build_seconds, 1),
        "search_seconds_all_queries": round(search_seconds, 2),
        "milliseconds_per_query": round(search_seconds * 1000 / max(1, len(queries)), 2),
        "exhaustive_ndcg10": exact["summary"]["ndcg@10"],
        "approximate_ndcg10": approx["summary"]["ndcg@10"],
        "loss_against_oracle": approx["summary"]["ndcg@10"] - exact["summary"]["ndcg@10"],
        "interval": [boot["low"], boot["high"]],
        "mean_top10_overlap_with_oracle": mean_overlap,
        "tolerance": args.tolerance,
        "export_parity_passes": bool(mean_overlap >= args.tolerance),
        "run_file": path,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps(payload, indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
