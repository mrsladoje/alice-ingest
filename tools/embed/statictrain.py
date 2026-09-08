#!/usr/bin/env python3
"""Stage S8: train ALICE static models and measure them against their controls.

A static model is a lookup table: one vector per token, averaged over the input.
It costs almost nothing to run, which is why the deployment cares about it.

The training text is built from the retrieval corpus, so **every model here is
transductive** and every report of it must say so. The plan permits that and
requires the label.

Source purity cannot promote anything. A trained model is promoted only if it
beats its stock control on natural-language retrieval by more than the frozen
practical difference.
"""
import argparse
import collections
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench

WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def build_training_text(corpus, example_cap=3, min_lines=0):
    """Deduplicated templates, frequency-capped examples, rare severities kept.

    56 million repeated messages must not be processed with equal weight, so a
    template contributes its normalised form once and at most `example_cap` raw
    examples, whatever its line count.
    """
    texts, caps = [], collections.Counter()
    for doc in corpus.docs:
        if doc.get("lines", 0) < min_lines:
            caps["skipped_below_min_lines"] += 1
            continue
        texts.append(bench.represent(doc, "R1"))
        caps["templates"] += 1
        for example in (doc.get("examples") or [])[:example_cap]:
            if example:
                texts.append(example)
                caps["examples"] += 1
        severities = set(doc.get("severities") or {})
        severities.discard("absent")
        if severities & {"error", "fatal", "critical", "warning"}:
            caps["high_severity_templates"] += 1
    return texts, dict(caps)


def build_vocabulary(corpus, top=6000, min_count=3):
    """An ALICE vocabulary: the domain tokens a general tokenizer splits apart."""
    counts = collections.Counter()
    for doc in corpus.docs:
        text = doc.get("template") or ""
        for token in bench.identifier_tokens(text):
            counts[token.lower()] += 1
        for match in WORD.finditer(text):
            counts[match.group(0).lower()] += 1
    chosen = [t for t, c in counts.most_common() if c >= min_count][:top]
    return chosen, {"candidates": len(counts), "min_count": min_count,
                    "vocabulary_size": len(chosen)}


def filter_vocabulary(teacher, vocabulary):
    """Drop tokens the teacher's tokenizer already holds.

    model2vec refuses a vocabulary entry that collides with an existing token,
    and the collision set differs per teacher, so the ALICE vocabulary has to be
    filtered against each one rather than built once.
    """
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(teacher)
    existing = set(tok.get_vocab())
    stripped = {t.lstrip("\u0120\u2581#") for t in existing}
    lowered = {t.lower() for t in stripped}
    out = []
    for token in vocabulary:
        forms = {token, "\u0120" + token, "\u2581" + token, token.lower()}
        if forms & existing or token in stripped or token.lower() in lowered:
            continue
        out.append(token)
    return out


def distil(teacher, vocabulary, pca_dims, out_dir, device=None):
    from model2vec.distill import distill
    started = time.time()
    if vocabulary:
        vocabulary = filter_vocabulary(teacher, vocabulary)
    model = distill(model_name=teacher, vocabulary=vocabulary,
                    pca_dims=pca_dims, device=device)
    os.makedirs(out_dir, exist_ok=True)
    model.save_pretrained(out_dir)
    return {"teacher": teacher, "pca_dims": pca_dims,
            "vocabulary_size": len(vocabulary) if vocabulary else 0,
            "dimensions": int(model.dim), "seconds": round(time.time() - started, 1),
            "path": out_dir}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="downloads/frozen/s8-static.json")
    parser.add_argument("--model-dir", default="downloads/frozen/static-models")
    parser.add_argument("--teachers", nargs="+",
                        default=["BAAI/bge-base-en-v1.5", "lightonai/DenseOn"])
    parser.add_argument("--dims", nargs="+", type=int, default=[256, 512])
    parser.add_argument("--example-cap", type=int, default=3)
    parser.add_argument("--vocab-top", type=int, default=6000)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    texts, caps = build_training_text(corpus, args.example_cap)
    vocabulary, vocab_info = build_vocabulary(corpus, args.vocab_top)

    os.makedirs(args.model_dir, exist_ok=True)
    with open(os.path.join(args.model_dir, "training-text.txt"), "w") as fh:
        for t in texts:
            fh.write(t.replace("\n", " ") + "\n")

    report = {
        "transductive": True,
        "transductive_note": ("training text is built from the retrieval corpus, "
                              "so every model here has seen the documents it is "
                              "scored on; it has never seen a query or a label"),
        "training_text_lines": len(texts),
        "sampling_caps": {**caps, "examples_per_template": args.example_cap},
        "alice_vocabulary": vocab_info,
        "variants": {},
        "failed": {},
    }

    plan = []
    for teacher in args.teachers:
        for dims in args.dims:
            plan.append(("P2-%s-d%d-alicevocab" % (teacher.split("/")[-1], dims),
                         teacher, vocabulary, dims))
            plan.append(("P2-%s-d%d-novocab" % (teacher.split("/")[-1], dims),
                         teacher, None, dims))

    for name, teacher, vocab, dims in plan:
        out_dir = os.path.join(args.model_dir, name)
        try:
            info = distil(teacher, vocab, dims, out_dir)
            report["variants"][name] = info
            print(name, json.dumps(info), flush=True)
        except Exception as exc:
            report["failed"][name] = "%s: %s" % (type(exc).__name__, exc)
            print("FAILED", name, exc, flush=True)
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=1, default=str)

    print(json.dumps({"trained": list(report["variants"]),
                      "failed": list(report["failed"]),
                      "training_lines": len(texts),
                      "vocabulary": vocab_info}, indent=1), flush=True)


if __name__ == "__main__":
    main()
