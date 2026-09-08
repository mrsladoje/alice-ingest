#!/usr/bin/env python3
"""Cache a late-interaction model's corpus token vectors to disk.

Reranking sweeps candidate depths, and re-encoding 5,301 documents for every
sweep would spend the night re-deriving identical vectors. The cache holds the
concatenated token matrix and the per-document offsets, so any candidate set can
be scored with one matrix product.
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench


def build(model, corpus, arm, out):
    adapter = adapters.LateAdapter(model)
    texts = [bench.represent(doc, arm) for doc in corpus.docs]
    started = time.time()
    vectors = adapter.encode_document(texts)
    seconds = time.time() - started
    lengths = np.array([v.shape[0] for v in vectors], dtype=np.int64)
    offsets = np.concatenate([[0], np.cumsum(lengths)])
    tokens = np.concatenate(vectors, axis=0).astype(np.float32)
    np.savez(out, tokens=tokens, offsets=offsets,
             ids=np.array(corpus.ids, dtype=object), arm=arm, model=model)
    return {"documents": len(texts), "token_rows": int(tokens.shape[0]),
            "dimensions": int(tokens.shape[1]),
            "encode_seconds": round(seconds, 1),
            "megabytes": round(tokens.nbytes / 1e6, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="LateOn")
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--out", default="downloads/frozen/lateon-tokens.npz")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()
    corpus = bench.Corpus.load(args.corpus)
    info = build(args.model, corpus, args.arm, args.out)
    print(info, flush=True)


if __name__ == "__main__":
    main()
