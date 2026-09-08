#!/usr/bin/env python3
"""SP0 measured locally, because the container cannot reach the model repository.

The supported path is the OpenSearch machine-learning plugin pulling the
pretrained model. This container resolves `artifacts.opensearch.org` and then
fails every HTTPS request, so that path is blocked by the environment.

This runs the same published model through transformers and scores the same
sparse retrieval in memory. It measures the model. It does not measure the
OpenSearch integration, the ingest cost or the index size, and it must not be
reported as if it did.

Document-only encoding: the model expands each document into weighted vocabulary
terms, and the query side is the tokenizer. That keeps model inference off the
query path, which is why the deployment would choose it.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench

MODEL = "opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte"


def encode_documents(texts, batch_size=32, top_terms=256, device="cpu"):
    """Weighted vocabulary terms per document, keeping the heaviest terms."""
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForMaskedLM.from_pretrained(MODEL).eval().to(device)
    special = set(tokenizer.all_special_ids)
    out = []
    started = time.time()
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        encoded = tokenizer(batch, padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to(device)
        with torch.no_grad():
            logits = model(**encoded).logits
        mask = encoded["attention_mask"].unsqueeze(-1)
        weights = torch.log1p(torch.relu(logits)) * mask
        pooled = weights.max(dim=1).values
        for row in pooled:
            values, indices = torch.topk(row, k=min(top_terms, row.shape[0]))
            terms = {int(i): float(v) for i, v in zip(indices.cpu(), values.cpu())
                     if float(v) > 0 and int(i) not in special}
            out.append(terms)
    return out, tokenizer, round(time.time() - started, 1)


def build_inverted(doc_terms):
    index = defaultdict(list)
    for position, terms in enumerate(doc_terms):
        for token_id, weight in terms.items():
            index[token_id].append((position, weight))
    return index


def search(index, tokenizer, text, ids, depth=200):
    token_ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    scores = defaultdict(float)
    for token_id in set(token_ids):
        for position, weight in index.get(token_id, ()):
            scores[position] += weight
    if not scores:
        return []
    positions = list(scores)
    ranked = bench.rank([scores[p] for p in positions],
                        [ids[p] for p in positions])
    lookup = {ids[p]: scores[p] for p in positions}
    return [(cid, lookup[cid]) for cid in ranked[:depth]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/sp0-local.json")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--top-terms", type=int, default=256)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    import adapters
    device = adapters.pick_device()
    texts = [bench.represent(doc, "R1") for doc in corpus.docs]
    doc_terms, tokenizer, encode_seconds = encode_documents(
        texts, top_terms=args.top_terms, device=device)
    index = build_inverted(doc_terms)
    postings = sum(len(v) for v in index.values())

    results, latencies = {}, []
    for qid, row in queries.items():
        t0 = time.time()
        results[qid] = search(index, tokenizer, row["text"], corpus.ids, args.depth)
        latencies.append((time.time() - t0) * 1000.0)

    import compare
    manifest = bench.make_manifest(**compare.base_manifest(
        "SP0-local", "03640622026320ba", "development-97-2026-09-08",
        os.path.basename(args.qrels), model_id=MODEL,
        model_revision="huggingface main", adapter="document-only sparse expansion",
        representation="R1",
        search_parameters={"depth": args.depth, "top_terms_per_document": args.top_terms},
        fusion_parameters="not applicable", opensearch_version="not applicable",
        index_uuid="not applicable", index_schema_revision="in-memory inverted index",
        searched_indices="in-memory", primary_shards="not applicable",
        replicas="not applicable", candidate_depth=args.depth,
        refresh_state="not applicable", search_pipeline_revision="not applicable",
        device=device, finished=compare.now()))
    manifest["document_encode_seconds"] = encode_seconds
    manifest["postings"] = postings
    manifest["search_ms"] = {"p50": float(np.percentile(latencies, 50)),
                             "p95": float(np.percentile(latencies, 95))}
    manifest["deviation"] = ("run outside OpenSearch because the container has no "
                             "outbound HTTPS; measures the model, not the integration")
    path = bench.write_run("downloads/frozen/runs/SP0-local.json", "SP0-local",
                           "SP0-local", "natural_language", manifest, results,
                           depth=args.depth)
    scored = bench.score_run(bench.read_run(path), qrels, queries)
    payload = {"summary": scored["summary"], "run_file": path,
               "encode_seconds": encode_seconds, "postings": postings,
               "search_ms": manifest["search_ms"],
               "deviation": manifest["deviation"]}
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps(payload, indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
