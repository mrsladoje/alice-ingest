#!/usr/bin/env python3
"""The deployable form of H7: vector candidates, then late-interaction reranking.

H7 is the best configuration this run measured, and Stage S9 refuses a finalist
without a deployable form. This builds one: OpenSearch `knn_vector` retrieves the
candidates on the production engine, and MaxSim reranks them from a token matrix
held beside it.

The point of the design is that the token matrix only has to serve the
candidates, not the corpus. `LateOn` was disqualified because exhaustive MaxSim
over 5,301 documents needs a multi-vector store. Reranking 100 candidates needs
an array and a matrix product.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench
import compare
import production
import systems
from hybrids import LateReranker

INDEX = "alice-templates-h7"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", default="DenseOn")
    parser.add_argument("--late-cache", default="downloads/frozen/lateon-tokens.npz")
    parser.add_argument("--offline-run", default="downloads/frozen/runs/H7-depth100.json")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/s9-production-h7.json")
    parser.add_argument("--candidate-depth", type=int, default=100)
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--tolerance", type=float, default=0.98)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    adapter = adapters.DenseAdapter(args.dense)
    offline = systems.DenseSystem(adapter, corpus, arm=args.arm)
    encode_info = offline.index()

    client = systems.client()
    index_info = production.build(client, corpus, offline.matrix, index=INDEX)
    print("vector index built:", index_info, flush=True)

    reranker = LateReranker(args.late_cache)
    texts = [row["text"] for row in queries.values()]
    dense_queries = adapter.encode_query(texts)
    late_queries = reranker.encode_queries(texts)
    late_by_qid = dict(zip(queries, late_queries))

    results, retrieve_ms, rerank_ms = {}, [], []
    for (qid, _row), dvec in zip(queries.items(), dense_queries):
        t0 = time.perf_counter()
        rows, _took = production.search_knn(client, np.asarray(dvec, dtype=np.float32),
                                            args.candidate_depth, index=INDEX)
        t1 = time.perf_counter()
        candidates = [cid for cid, _ in rows]
        scores = reranker.score(late_by_qid[qid], candidates)
        ranked = bench.rank([scores[c] for c in candidates], candidates)
        results[qid] = [(c, scores[c]) for c in ranked]
        t2 = time.perf_counter()
        retrieve_ms.append((t1 - t0) * 1000.0)
        rerank_ms.append((t2 - t1) * 1000.0)

    manifest = bench.make_manifest(**compare.base_manifest(
        "P-H7", "03640622026320ba", "development-97-2026-09-08",
        os.path.basename(args.qrels),
        model_id="%s + LateOn rerank" % adapter.spec["model_id"],
        model_revision="%s / %s" % (adapter.spec["revision"],
                                    adapters.LATE["LateOn"]["revision"]),
        adapter="opensearch knn candidates, MaxSim rerank from a token matrix",
        representation=args.arm,
        search_parameters={"candidate_depth": args.candidate_depth,
                           "engine": "opensearch knn hnsw lucene",
                           "rerank": "LateOn MaxSim"},
        fusion_parameters="not applicable, rerank rather than fusion",
        opensearch_version=systems.engine_version(client),
        index_uuid=index_info["index_uuid"],
        index_schema_revision="production.knn_settings",
        searched_indices=[INDEX], primary_shards=index_info["primary_shards"],
        replicas=index_info["replicas"], candidate_depth=args.candidate_depth,
        refresh_state="refreshed and flushed", search_pipeline_revision="none",
        device=adapter.device, finished=compare.now()))
    manifest["vector_index_bytes"] = index_info["bytes"]
    manifest["token_matrix_megabytes"] = round(reranker.tokens.nbytes / 1e6, 1)
    manifest["document_encode_seconds"] = encode_info["encode_seconds"]
    manifest["retrieve_ms"] = {"p50": float(np.percentile(retrieve_ms, 50)),
                               "p95": float(np.percentile(retrieve_ms, 95))}
    manifest["rerank_ms"] = {"p50": float(np.percentile(rerank_ms, 50)),
                             "p95": float(np.percentile(rerank_ms, 95))}
    manifest["end_to_end_ms"] = {
        "p50": float(np.percentile(np.add(retrieve_ms, rerank_ms), 50)),
        "p95": float(np.percentile(np.add(retrieve_ms, rerank_ms), 95)),
        "p99": float(np.percentile(np.add(retrieve_ms, rerank_ms), 99))}

    path = bench.write_run("downloads/frozen/runs/P-H7.json", "P-H7", "P-H7",
                           "natural_language", manifest, results, depth=args.depth)
    scored_production = bench.score_run(bench.read_run(path), qrels, queries)
    offline_run = bench.read_run(args.offline_run)
    scored_offline = bench.score_run(offline_run, qrels, queries)
    exact = {qid: [(c, float(s)) for c, s in rows]
             for qid, rows in offline_run["results"].items()}
    agreement = production.rank_agreement(exact, results, cutoff=10)

    payload = {
        "system": "P-H7", "candidate_depth": args.candidate_depth,
        "production_form": ("opensearch knn_vector candidates on the pinned engine, "
                            "MaxSim rerank from a %s MB token matrix"
                            % manifest["token_matrix_megabytes"]),
        "vector_index_bytes": index_info["bytes"],
        "vector_index_bytes_per_template": round(index_info["bytes"] / len(corpus), 1),
        "token_matrix_megabytes": manifest["token_matrix_megabytes"],
        "offline_ndcg10": scored_offline["summary"]["ndcg@10"],
        "production_ndcg10": scored_production["summary"]["ndcg@10"],
        "relevance_change": (scored_production["summary"]["ndcg@10"]
                             - scored_offline["summary"]["ndcg@10"]),
        "rank_agreement_at_10": {k: v for k, v in agreement.items() if k != "per_query"},
        "latency_ms": {"retrieve": manifest["retrieve_ms"],
                       "rerank": manifest["rerank_ms"],
                       "end_to_end": manifest["end_to_end_ms"]},
        "parity_passes": bool(agreement["mean_overlap"] >= args.tolerance),
        "manifest_missing": bench.manifest_is_complete(manifest),
        "run_file": path,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps(payload, indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
