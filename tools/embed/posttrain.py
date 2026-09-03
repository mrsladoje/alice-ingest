#!/usr/bin/env python3
"""Build the post-trained rungs of stage I on our own log text.

Three models come out of this, and they cost very different amounts.

Custom-vocabulary distillation is minutes on a processor. It gives every ALICE
identifier — O2DataRegion_TimeFrame, partitionId, ODC — one vector of its own
instead of four subword fragments, and changes nothing else.

tokenlearn is the full POTION recipe: run a teacher sentence transformer over
the training text for mean output embeddings, train the static model against
those, then re-regularise by token frequency, PCA and SIF weighting. It is hours,
and stage I runs it only if the cheap route moved the dev number.

FastText is round 3's named untested rung, trained on the same lines.

Every mode reads the training lines splits.py wrote, which exclude every source
in the dev and held-out sets. Mining a vocabulary from the whole corpus would put
a held-out template's rare tokens in the lookup table before it is ever scored.
"""
import argparse
import json
import os
import random
import time
from collections import Counter

import regex as re

TOKEN = re.compile(r"\w+|[^\w\s]+")
TEACHER = "baai/bge-base-en-v1.5"


def count_tokens(path, limit):
    """The vocabulary tokenlearn's own create_vocab would build, counted by
    streaming instead of by holding every training line in memory."""
    counts = Counter()
    lines = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            counts.update(TOKEN.findall(line.lower()))
            lines += 1
            if limit and lines >= limit:
                break
    return counts, lines


def sample_lines(path, wanted, dedupe, seed, scan):
    """A reservoir sample of the training text, deduplicated first.

    One template can account for a million identical lines. Feeding those to a
    teacher buys one text's worth of signal for a million forward passes."""
    rng = random.Random(seed)
    seen = set()
    kept = []
    read = 0
    candidates = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            read += 1
            if scan and read > scan:
                break
            text = line.rstrip("\n")
            if not text:
                continue
            if dedupe:
                if text in seen:
                    continue
                seen.add(text)
            candidates += 1
            if len(kept) < wanted:
                kept.append(text)
            else:
                j = rng.randrange(candidates)
                if j < wanted:
                    kept[j] = text
    return kept, read


def load_teacher(name, trust_remote_code):
    """The teacher, loaded once, through sentence-transformers for both arms.

    nomic-ai/CodeRankEmbed cannot be reached by model2vec's own distill(): its
    remote code looks for a pytorch_model.bin the repository does not carry, and
    its NomicBertModel never implements get_input_embeddings. Loading through
    sentence-transformers gets past the first, and the two-line shim below gets
    past the second. Both teachers then take the identical code path, which is
    the only way the arms compare."""
    from sentence_transformers import SentenceTransformer
    st = SentenceTransformer(name, device="cpu", trust_remote_code=trust_remote_code)
    model = st[0].auto_model
    kind = type(model)
    try:
        model.get_input_embeddings()
    except NotImplementedError:
        kind.get_input_embeddings = lambda self: self.embeddings.word_embeddings

        def _set(self, value):
            self.embeddings.word_embeddings = value
        kind.set_input_embeddings = _set
    return model, st.tokenizer


def build_vocab(args):
    from model2vec.distill import distill_from_model

    counts, lines = count_tokens(args.train_lines, args.vocab_scan_lines)
    vocab = [word for word, _ in counts.most_common(args.vocab_size)]
    teacher, tokeniser = load_teacher(args.teacher, args.trust_remote_code)
    t0 = time.process_time()
    model = distill_from_model(model=teacher, tokenizer=tokeniser, vocabulary=vocab,
                               pca_dims=args.pca_dims, sif_coefficient=args.sif,
                               quantize_to="float32")
    cpu = time.process_time() - t0
    model.save_pretrained(args.out)
    return {"mode": "vocab", "teacher": args.teacher, "vocab_size": len(vocab),
            "distinct_tokens_seen": len(counts), "lines_scanned": lines,
            "pca_dims": args.pca_dims, "sif_coefficient": args.sif,
            "distill_core_seconds": round(cpu, 1)}


def build_tokenlearn(args):
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from model2vec.distill import distill_from_model
    from tokenlearn.train import train_model

    texts, read = sample_lines(args.train_lines, args.train_texts,
                               not args.no_dedupe, args.seed,
                               args.sample_scan_lines)
    st = SentenceTransformer(args.teacher, device=args.device,
                             trust_remote_code=args.trust_remote_code)
    t0 = time.time()
    means = st.encode(texts, batch_size=args.batch_size,
                      convert_to_numpy=True, show_progress_bar=True)
    featurise_seconds = time.time() - t0

    counts, _ = count_tokens(args.train_lines, args.vocab_scan_lines)
    vocab = [word for word, _ in counts.most_common(args.vocab_size)]
    teacher, tokeniser = load_teacher(args.teacher, args.trust_remote_code)
    model = distill_from_model(model=teacher, tokenizer=tokeniser, vocabulary=vocab,
                               pca_dims=args.pca_dims, quantize_to="float32")

    t0 = time.time()
    model = train_model(model, texts, np.asarray(means, dtype=np.float32),
                        device=args.device, pca_dims=args.pca_dims)
    train_seconds = time.time() - t0
    model.save_pretrained(args.out)
    return {"mode": "tokenlearn", "teacher": args.teacher,
            "train_texts": len(texts), "lines_read": read,
            "vocab_size": len(vocab), "pca_dims": args.pca_dims,
            "device": args.device,
            "featurise_wall_seconds": round(featurise_seconds, 1),
            "train_wall_seconds": round(train_seconds, 1)}


def build_fasttext(args):
    from gensim.models import FastText

    class Corpus(object):
        def __init__(self, path, limit):
            self.path = path
            self.limit = limit

        def __iter__(self):
            n = 0
            with open(self.path, "r", errors="replace") as fh:
                for line in fh:
                    yield TOKEN.findall(line.lower())
                    n += 1
                    if self.limit and n >= self.limit:
                        break

    corpus = Corpus(args.train_lines, args.vocab_scan_lines)
    t0 = time.time()
    model = FastText(vector_size=args.pca_dims, window=5, min_count=5,
                     workers=args.workers, sg=1, epochs=args.epochs)
    model.build_vocab(corpus_iterable=corpus)
    model.train(corpus_iterable=corpus, total_examples=model.corpus_count,
                epochs=model.epochs)
    wall = time.time() - t0
    model.save(args.out)
    return {"mode": "fasttext", "vector_size": args.pca_dims,
            "vocabulary": len(model.wv), "epochs": args.epochs,
            "lines": model.corpus_count, "train_wall_seconds": round(wall, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("vocab", "tokenlearn", "fasttext"))
    ap.add_argument("--train-lines", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--teacher", default=TEACHER)
    ap.add_argument("--vocab-size", type=int, default=30000)
    ap.add_argument("--vocab-scan-lines", type=int, default=5000000)
    ap.add_argument("--pca-dims", type=int, default=256)
    ap.add_argument("--sif", type=float, default=1e-4)
    ap.add_argument("--train-texts", type=int, default=200000)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sample-scan-lines", type=int, default=10000000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-dedupe", action="store_true")
    ap.add_argument("--trust-remote-code", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    report = {"vocab": build_vocab, "tokenlearn": build_tokenlearn,
              "fasttext": build_fasttext}[args.mode](args)
    report["out"] = args.out
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
