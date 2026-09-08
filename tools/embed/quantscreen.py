#!/usr/bin/env python3
"""S5 quantisation and dimension screens, against the float reference.

The plan's order is not negotiable here: exact float cosine first, then
quantisation, and the loss is always reported against that float reference
rather than against another quantised run.

Dimension reduction is measured the same way. Both questions are about cost, and
a cost saving is only worth stating beside the quality it spends.
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
import systems


def quantise(matrix, mode):
    """Return the quantised matrix and the bytes one vector costs."""
    if mode == "float32":
        return matrix.astype(np.float32), matrix.shape[1] * 4
    if mode == "float16":
        return matrix.astype(np.float16).astype(np.float32), matrix.shape[1] * 2
    if mode == "int8":
        scale = np.abs(matrix).max(axis=1, keepdims=True)
        scale[scale == 0] = 1.0
        q = np.clip(np.round(matrix / scale * 127), -127, 127).astype(np.int8)
        return (q.astype(np.float32) / 127.0) * scale, matrix.shape[1]
    if mode == "binary":
        signs = np.where(matrix >= 0, 1.0, -1.0).astype(np.float32)
        return signs, matrix.shape[1] / 8.0
    raise ValueError(mode)


def reduce_dimensions(matrix, dims):
    """Principal components, fitted on the corpus the system will serve."""
    from sklearn.decomposition import PCA
    if dims >= matrix.shape[1]:
        return matrix, 1.0
    pca = PCA(n_components=dims, random_state=0)
    reduced = pca.fit_transform(matrix)
    return reduced.astype(np.float32), float(pca.explained_variance_ratio_.sum())


def rank_all(matrix, queries_matrix, ids, depth):
    out = {}
    for qid, vector in queries_matrix.items():
        scores = matrix @ vector
        ranked = bench.rank(scores.tolist(), ids)
        lookup = {cid: float(scores[i]) for i, cid in enumerate(ids)}
        out[qid] = [(cid, lookup[cid]) for cid in ranked[:depth]]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="DenseOn")
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/s5-quant-dim.json")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--dims", nargs="+", type=int, default=[768, 512, 256, 128, 64])
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    adapter = adapters.DenseAdapter(args.model)
    system = systems.DenseSystem(adapter, corpus, arm=args.arm)
    info = system.index()
    reference = system.matrix.astype(np.float32)
    query_vectors = {qid: np.asarray(v, dtype=np.float32) for qid, v in
                     zip(queries, adapter.encode_query([r["text"] for r in queries.values()]))}

    def score(matrix, qvecs):
        runs = rank_all(matrix, qvecs, corpus.ids, args.depth)
        return bench.score_run({"results": runs}, qrels, queries), runs

    base_scored, base_runs = score(reference, query_vectors)
    base = base_scored["summary"]["ndcg@10"]
    base_intent = {g: r.get("ndcg@10") for g, r in base_scored["per_intent"].items()}
    report = {"model": adapter.spec["model_id"], "revision": adapter.spec["revision"],
              "representation": args.arm, "float_reference_ndcg10": base,
              "encode": info, "quantisation": {}, "dimensions": {}}
    print("float32 reference ndcg@10 = %.4f  (%d dims)" % (base, reference.shape[1]),
          flush=True)

    for mode in ("float16", "int8", "binary"):
        started = time.time()
        q, per_vector = quantise(reference, mode)
        scored, runs = score(q, {k: quantise(v.reshape(1, -1), mode)[0].reshape(-1)
                                 for k, v in query_vectors.items()})
        vals = {g: r.get("ndcg@10") for g, r in scored["per_intent"].items()}
        boot = bench.bootstrap_paired(vals, base_intent)
        agree = np.mean([len(set(c for c, _ in runs[q2][:10])
                             & set(c for c, _ in base_runs[q2][:10])) / 10.0
                         for q2 in queries])
        report["quantisation"][mode] = {
            "ndcg@10": scored["summary"]["ndcg@10"],
            "loss_against_float_reference": scored["summary"]["ndcg@10"] - base,
            "interval": [boot["low"], boot["high"]], "p": boot["p"],
            "bytes_per_vector": per_vector,
            "bytes_saved_share": 1 - per_vector / (reference.shape[1] * 4),
            "top10_overlap_with_float": float(agree),
            "seconds": round(time.time() - started, 1)}
        print("%-8s ndcg=%.4f loss=%+.4f CI[%+.4f,%+.4f] bytes/vec=%.0f top10 overlap=%.3f"
              % (mode, scored["summary"]["ndcg@10"],
                 scored["summary"]["ndcg@10"] - base, boot["low"], boot["high"],
                 per_vector, agree), flush=True)

    for dims in args.dims:
        reduced, explained = reduce_dimensions(reference, dims)
        reduced = reduced / np.clip(np.linalg.norm(reduced, axis=1, keepdims=True), 1e-9, None)
        if dims < reference.shape[1]:
            from sklearn.decomposition import PCA
            pca = PCA(n_components=dims, random_state=0).fit(reference)
            qred = {k: pca.transform(v.reshape(1, -1))[0] for k, v in query_vectors.items()}
            qred = {k: v / max(1e-9, float(np.linalg.norm(v))) for k, v in qred.items()}
        else:
            qred = query_vectors
        scored, _ = score(reduced.astype(np.float32),
                          {k: np.asarray(v, dtype=np.float32) for k, v in qred.items()})
        vals = {g: r.get("ndcg@10") for g, r in scored["per_intent"].items()}
        boot = bench.bootstrap_paired(vals, base_intent)
        report["dimensions"][dims] = {
            "ndcg@10": scored["summary"]["ndcg@10"],
            "loss_against_float_reference": scored["summary"]["ndcg@10"] - base,
            "interval": [boot["low"], boot["high"]],
            "explained_variance": explained,
            "bytes_per_vector": dims * 4}
        print("dims=%-4d ndcg=%.4f loss=%+.4f explained_variance=%.4f"
              % (dims, scored["summary"]["ndcg@10"],
                 scored["summary"]["ndcg@10"] - base, explained), flush=True)

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
