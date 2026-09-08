#!/usr/bin/env python3
"""Stage S9: build the deployable form of a finalist and test it against the
exact offline run.

A model that only ranks in a notebook is not a candidate. The production form
here is an OpenSearch `knn_vector` index on the pinned production version, which
is what the deployment would actually serve, and it is compared against the
exhaustive cosine ranking the screen used.

Parity is reported, never assumed. Approximate search that loses more than its
stated tolerance is a stop rule, not a rounding difference.
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
import systems

KNN_INDEX = "alice-templates-knn"


def knn_settings(dimensions, ef_construction=128, m=16):
    return {
        "settings": {
            "index": {"knn": True, "number_of_shards": systems.PRIMARY_SHARDS,
                      "number_of_replicas": systems.REPLICAS},
        },
        "mappings": {
            "properties": {
                "canonical_id": {"type": "keyword"},
                "vector": {
                    "type": "knn_vector",
                    "dimension": dimensions,
                    "method": {"name": "hnsw", "space_type": "cosinesimil",
                               "engine": "lucene",
                               "parameters": {"ef_construction": ef_construction,
                                              "m": m}},
                },
            }
        },
    }


def build(client, corpus, matrix, index=KNN_INDEX, ef_construction=128, m=16):
    from opensearchpy import helpers
    if client.indices.exists(index=index):
        client.indices.delete(index=index)
    client.indices.create(index=index,
                          body=knn_settings(int(matrix.shape[1]), ef_construction, m))
    actions = ({"_index": index, "_id": cid,
                "_source": {"canonical_id": cid, "vector": matrix[i].tolist()}}
               for i, cid in enumerate(corpus.ids))
    started = time.time()
    helpers.bulk(client, actions, chunk_size=500, request_timeout=300)
    client.indices.refresh(index=index)
    client.indices.flush(index=index)
    settings = client.indices.get_settings(index=index)
    key = list(settings)[0]
    stats = client.indices.stats(index=index)
    return {"index": index,
            "index_uuid": settings[key]["settings"]["index"]["uuid"],
            "documents": client.count(index=index)["count"],
            "primary_shards": int(settings[key]["settings"]["index"]["number_of_shards"]),
            "replicas": int(settings[key]["settings"]["index"]["number_of_replicas"]),
            "bytes": stats["_all"]["primaries"]["store"]["size_in_bytes"],
            "build_seconds": round(time.time() - started, 1),
            "ef_construction": ef_construction, "m": m}


def search_knn(client, vector, depth, index=KNN_INDEX, ef_search=256):
    body = {"size": depth,
            "query": {"knn": {"vector": {"vector": vector.tolist(), "k": depth}}}}
    response = client.search(index=index, body=body, request_timeout=180)
    return [(h["_id"], float(h["_score"])) for h in response["hits"]["hits"]], \
        response["took"]


def rank_agreement(exact, approximate, cutoff=10):
    """How much of the exact top-k the deployable form returns, and in what order.

    Overlap says whether the same documents come back. Kendall tau on the shared
    documents says whether they come back in the same order. Both are reported,
    because a form can keep every document and still reorder them.
    """
    out = []
    for qid, exact_rows in exact.items():
        gold = [cid for cid, _ in exact_rows[:cutoff]]
        got = [cid for cid, _ in approximate.get(qid, [])[:cutoff]]
        shared = set(gold) & set(got)
        overlap = len(shared) / cutoff if cutoff else 0.0
        gold_positions = {cid: i for i, cid in enumerate(gold)}
        got_positions = {cid: i for i, cid in enumerate(got)}
        pairs = sorted(shared)
        concordant = discordant = 0
        for a in range(len(pairs)):
            for b in range(a + 1, len(pairs)):
                one, two = pairs[a], pairs[b]
                sign = ((gold_positions[one] - gold_positions[two])
                        * (got_positions[one] - got_positions[two]))
                if sign > 0:
                    concordant += 1
                elif sign < 0:
                    discordant += 1
        total = concordant + discordant
        tau = (concordant - discordant) / total if total else 1.0
        out.append({"query_id": qid, "overlap": overlap, "kendall_tau": tau,
                    "shared": len(shared)})
    return {"queries": len(out),
            "mean_overlap": float(np.mean([r["overlap"] for r in out])),
            "min_overlap": float(np.min([r["overlap"] for r in out])),
            "mean_kendall_tau": float(np.mean([r["kendall_tau"] for r in out])),
            "queries_with_perfect_overlap":
                sum(1 for r in out if r["overlap"] == 1.0),
            "per_query": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--exact-run", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--tolerance", type=float, default=0.98,
                        help="mean overlap at rank 10 below which parity fails")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    name = args.name or ("P-%s" % args.model)
    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    builder = (adapters.StaticAdapter if args.model in adapters.STATIC
               else adapters.DenseAdapter)
    adapter = builder(args.model)
    offline = systems.DenseSystem(adapter, corpus, arm=args.arm)
    encode_info = offline.index()

    client = systems.client()
    index_info = build(client, corpus, offline.matrix)

    texts = [row["text"] for row in queries.values()]
    query_vectors = adapter.encode_query(texts)
    approximate, latencies = {}, []
    for (qid, _row), vector in zip(queries.items(), query_vectors):
        rows, took = search_knn(client, np.asarray(vector, dtype=np.float32),
                                args.depth)
        approximate[qid] = rows
        latencies.append(took)

    exact = {qid: [(cid, float(score)) for cid, score in rows]
             for qid, rows in bench.read_run(args.exact_run)["results"].items()}

    agreement = rank_agreement(exact, approximate, cutoff=10)
    manifest = bench.make_manifest(**compare.base_manifest(
        name, "03640622026320ba", "development-97-2026-09-08",
        os.path.basename(args.qrels),
        model_id=adapter.spec["model_id"], model_revision=adapter.spec["revision"],
        adapter={k: v for k, v in adapter.spec.items() if k != "role"},
        representation=args.arm,
        search_parameters={"engine": "opensearch knn hnsw lucene",
                           "space_type": "cosinesimil",
                           "ef_construction": index_info["ef_construction"],
                           "m": index_info["m"], "depth": args.depth},
        fusion_parameters="not applicable",
        opensearch_version=systems.engine_version(client),
        index_uuid=index_info["index_uuid"],
        index_schema_revision="production.knn_settings",
        searched_indices=[index_info["index"]],
        primary_shards=index_info["primary_shards"],
        replicas=index_info["replicas"], candidate_depth=args.depth,
        refresh_state="refreshed after bulk index",
        search_pipeline_revision="none", device=adapter.device,
        finished=compare.now()))
    manifest["index_bytes"] = index_info["bytes"]
    manifest["index_bytes_per_template"] = round(
        index_info["bytes"] / max(1, len(corpus)), 1)
    manifest["index_build_seconds"] = index_info["build_seconds"]
    manifest["document_encode_seconds"] = encode_info["encode_seconds"]
    manifest["engine_latency_ms"] = {
        "p50": float(np.percentile(latencies, 50)),
        "p95": float(np.percentile(latencies, 95)),
        "p99": float(np.percentile(latencies, 99)),
    }

    path = bench.write_run("downloads/frozen/runs/%s.json" % name, name, name,
                           "natural_language", manifest, approximate,
                           depth=args.depth)
    scored_production = bench.score_run(bench.read_run(path), qrels, queries)
    scored_exact = bench.score_run(bench.read_run(args.exact_run), qrels, queries)

    payload = {
        "system": name,
        "model": adapter.spec["model_id"],
        "representation": args.arm,
        "production_form": "opensearch knn_vector, hnsw, lucene engine",
        "index": index_info,
        "run_file": path,
        "rank_agreement_at_10": {k: v for k, v in agreement.items()
                                 if k != "per_query"},
        "exact_summary": scored_exact["summary"],
        "production_summary": scored_production["summary"],
        "relevance_change_ndcg10": (scored_production["summary"]["ndcg@10"]
                                    - scored_exact["summary"]["ndcg@10"]),
        "tolerance_mean_overlap": args.tolerance,
        "parity_passes": bool(agreement["mean_overlap"] >= args.tolerance),
        "manifest_missing": bench.manifest_is_complete(manifest),
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in payload.items()
                      if k not in ("index",)}, indent=2, default=str))


if __name__ == "__main__":
    main()
