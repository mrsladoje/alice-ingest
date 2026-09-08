#!/usr/bin/env python3
"""P3 to P6: Tokenlearn variants, trained on ALICE text.

Distillation copies a teacher's token vectors. Tokenlearn goes further: it fits
the static table so that averaging it reproduces the teacher's *sentence*
vectors on real ALICE text. That is the step that can teach a lookup table
something about this domain rather than about English in general.

Teacher features are computed once per teacher and reused across the vocabulary
and dimension variants, because recomputing them per variant is the expensive
mistake the plan warns about.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
from statictrain import build_training_text, build_vocabulary, filter_vocabulary


def teacher_features(teacher, texts, out_dir, batch_size=64, max_means=200000):
    """Encode the training text once with the teacher and cache the means."""
    from sentence_transformers import SentenceTransformer
    from tokenlearn.featurize import featurize
    import adapters
    os.makedirs(out_dir, exist_ok=True)
    marker = out_dir.rstrip("/") + ".done.json"
    if os.path.exists(marker):
        return json.load(open(marker))
    model = SentenceTransformer(teacher, device=adapters.pick_device())
    started = time.time()
    featurize(dataset=({"text": t} for t in texts), model=model,
              output_dir=out_dir, max_means=max_means,
              batch_size=batch_size, text_key="text")
    info = {"teacher": teacher, "texts": len(texts),
            "seconds": round(time.time() - started, 1)}
    json.dump(info, open(marker, "w"))
    return info


def train_variant(teacher, features_dir, vocabulary, pca_dims, out_dir, device):
    """Training runs on the processor, in single precision.

    model2vec distils to float16 by default and tokenlearn's training step
    multiplies against float32 targets, which raises a dtype mismatch. The table
    is therefore distilled to float32 before it is trained.
    """

    """Training runs on the processor.

    tokenlearn refuses `mps` on this torch build, citing known performance
    regressions, so the training step is pinned to cpu rather than left to pick
    a device it will then reject.
    """
    from pathlib import Path
    from tokenlearn.train import collect_means_and_texts, distill, train_model
    paths = sorted(Path(features_dir).glob("*"))
    texts, means = collect_means_and_texts(paths)
    started = time.time()
    base = distill(model_name=teacher, vocabulary=vocabulary, pca_dims=pca_dims,
                   device="cpu", quantize_to="float32")
    trained = train_model(base, texts, means, device="cpu", pca_dims=pca_dims)
    os.makedirs(out_dir, exist_ok=True)
    trained.save_pretrained(out_dir)
    return {"teacher": teacher, "pca_dims": pca_dims,
            "vocabulary_size": len(vocabulary) if vocabulary else 0,
            "training_pairs": len(texts),
            "dimensions": int(trained.dim),
            "seconds": round(time.time() - started, 1), "path": out_dir}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teachers", nargs="+",
                        default=["BAAI/bge-base-en-v1.5", "BAAI/bge-large-en-v1.5"])
    parser.add_argument("--dims", nargs="+", type=int, default=[256, 512])
    parser.add_argument("--out", default="downloads/frozen/s8-tokenlearn.json")
    parser.add_argument("--model-dir", default="downloads/frozen/static-models")
    parser.add_argument("--features-root", default="downloads/frozen/teacher-features")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    import adapters
    device = adapters.pick_device()
    corpus = bench.Corpus.load(args.corpus)
    texts, caps = build_training_text(corpus, example_cap=3)
    vocabulary, vocab_info = build_vocabulary(corpus, top=6000)

    report = {"transductive": True, "training_text_lines": len(texts),
              "sampling_caps": caps, "alice_vocabulary": vocab_info,
              "device": device, "teacher_features": {}, "variants": {},
              "failed": {}}

    for teacher in args.teachers:
        slug = teacher.split("/")[-1]
        features_dir = os.path.join(args.features_root, slug)
        try:
            report["teacher_features"][teacher] = teacher_features(
                teacher, texts, features_dir)
            print("features cached for", teacher,
                  report["teacher_features"][teacher], flush=True)
        except Exception as exc:
            report["failed"]["features-" + slug] = "%s: %s" % (type(exc).__name__, exc)
            print("FEATURES FAILED", teacher, exc, flush=True)
            continue

        filtered = filter_vocabulary(teacher, vocabulary)
        for dims in args.dims:
            for label, vocab in (("novocab", None), ("alicevocab", filtered)):
                name = "P%s-tokenlearn-%s-d%d-%s" % (
                    "3" if "base" in slug else "5", slug, dims, label)
                out_dir = os.path.join(args.model_dir, name)
                try:
                    info = train_variant(teacher, features_dir, vocab, dims,
                                         out_dir, device)
                    report["variants"][name] = info
                    print(name, json.dumps(info), flush=True)
                except Exception as exc:
                    report["failed"][name] = "%s: %s" % (type(exc).__name__, exc)
                    print("FAILED", name, str(exc)[:200], flush=True)
                with open(args.out, "w") as fh:
                    json.dump(report, fh, indent=1, default=str)

    print(json.dumps({"trained": list(report["variants"]),
                      "failed": list(report["failed"])}, indent=1), flush=True)


if __name__ == "__main__":
    main()
