#!/usr/bin/env python3
"""P7: supervised static retrieval, trained on triples from the graded pool.

Every other static model in this run learned from a teacher. This one learns
from the judgements: a query, a template a machine assessor graded relevant, and
hard negatives drawn from what lexical and dense retrieval actually returned and
got wrong.

The plan keeps this branch separate from unlabeled Tokenlearn adaptation and
runs it only after P0 to P6 establish the baseline, which they now have.

**Held-out queries and held-out judgements are never touched.** The triples come
from development labels only.
"""
import argparse
import collections
import json
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench


def build_triples(qrels, queries, corpus, runs, negatives_per_positive=4, seed=7):
    """Query, a graded-relevant template, and hard negatives that a system ranked high.

    A hard negative is a template some system put in its top results and an
    assessor then graded 0 or 1. Random negatives teach almost nothing; these are
    the confusions the systems actually make.
    """
    rng = random.Random(seed)
    triples, stats = [], collections.Counter()
    retrieved = collections.defaultdict(list)
    for run in runs:
        for qid, rows in run["results"].items():
            for cid, _ in rows[:50]:
                retrieved[qid].append(cid)
    for qid, row in queries.items():
        positives = sorted(qrels.relevant(qid))
        if not positives:
            stats["queries_without_a_positive"] += 1
            continue
        stats["queries_used"] += 1
        if len(positives) > 1:
            stats["queries_with_multiple_positives"] += 1
        seen = set(positives)
        hard = [c for c in dict.fromkeys(retrieved.get(qid, []))
                if c not in seen and qrels.grade(qid, c) in (0, 1)]
        stats["hard_negatives_available"] += len(hard)
        for positive in positives:
            picks = hard[:negatives_per_positive] if hard else []
            if len(picks) < negatives_per_positive:
                pool = [d["canonical_id"] for d in corpus.docs]
                while len(picks) < negatives_per_positive:
                    candidate = rng.choice(pool)
                    if candidate not in seen:
                        picks.append(candidate)
                        stats["random_negatives_used"] += 1
            for negative in picks:
                triples.append((row["text"], positive, negative))
    return triples, dict(stats)


def train(base_path, triples, corpus, arm, epochs, lr, batch_size, out_dir):
    """Fit the lookup table so a query sits closer to its positive than its negative."""
    import torch
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(base_path)
    table = torch.tensor(np.asarray(model.embedding, dtype=np.float32),
                         requires_grad=True)
    tokenizer = model.tokenizer
    special = {tokenizer.token_to_id(t) for t in ("[CLS]", "[SEP]", "[PAD]", "[UNK]")}
    special.discard(None)

    def ids_of(text):
        return [i for i in tokenizer.encode(text).ids if i not in special] or [0]

    text_of = {d["canonical_id"]: bench.represent(d, arm) for d in corpus.docs}
    prepared = [(ids_of(q), ids_of(text_of[p]), ids_of(text_of[n]))
                for q, p, n in triples if p in text_of and n in text_of]

    def embed(batch_ids):
        return torch.stack([table[torch.tensor(i)].mean(dim=0) for i in batch_ids])

    optimiser = torch.optim.Adam([table], lr=lr)
    started = time.time()
    losses = []
    for epoch in range(epochs):
        random.Random(epoch).shuffle(prepared)
        total = 0.0
        for start in range(0, len(prepared), batch_size):
            chunk = prepared[start:start + batch_size]
            q = torch.nn.functional.normalize(embed([c[0] for c in chunk]), dim=1)
            p = torch.nn.functional.normalize(embed([c[1] for c in chunk]), dim=1)
            n = torch.nn.functional.normalize(embed([c[2] for c in chunk]), dim=1)
            loss = torch.clamp(0.2 - (q * p).sum(1) + (q * n).sum(1), min=0).mean()
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            total += float(loss) * len(chunk)
        losses.append(total / max(1, len(prepared)))
        print("  epoch %d margin loss %.4f" % (epoch + 1, losses[-1]), flush=True)

    model.embedding = table.detach().numpy().astype(np.float32)
    os.makedirs(out_dir, exist_ok=True)
    model.save_pretrained(out_dir)
    return {"triples_used": len(prepared), "epochs": epochs,
            "final_margin_loss": losses[-1] if losses else None,
            "loss_curve": losses, "seconds": round(time.time() - started, 1),
            "path": out_dir}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="downloads/frozen/static-models/P2-bge-base-en-v1.5-d512-alicevocab")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--runs", nargs="*", default=[])
    parser.add_argument("--out", default="downloads/frozen/s8-p7.json")
    parser.add_argument("--model-out", default="downloads/frozen/static-models/P7-supervised-d512")
    parser.add_argument("--arm", default="R1")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--holdout-fraction", type=float, default=0.0,
                        help="withhold this share of intent groups from training, "
                             "so the model can be scored on queries it never saw")
    parser.add_argument("--split-seed", type=int, default=20260908)
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)
    runs = [bench.read_run(p) for p in args.runs]

    withheld = []
    if args.holdout_fraction > 0:
        groups = sorted({q["intent_group"] for q in queries.values()})
        rng = random.Random(args.split_seed)
        rng.shuffle(groups)
        cut = int(len(groups) * args.holdout_fraction)
        withheld = sorted(groups[:cut])
        train_queries = {k: v for k, v in queries.items()
                         if v["intent_group"] not in set(withheld)}
        print("training on %d intent groups, withholding %d"
              % (len(groups) - cut, cut), flush=True)
    else:
        train_queries = queries

    triples, stats = build_triples(qrels, train_queries, corpus, runs)
    stats["withheld_intent_groups"] = len(withheld)
    stats["trained_on_intent_groups"] = len({q["intent_group"] for q in train_queries.values()})
    print("triples", len(triples), json.dumps(stats), flush=True)
    info = train(args.base, triples, corpus, args.arm, args.epochs, args.lr,
                 args.batch_size, args.model_out)
    payload = {"base": args.base, "triple_stats": stats,
               "supervision": "development judgements only, no held-out labels",
               "withheld_intent_groups": withheld,
               "warning": ("scoring this model on the queries it trained on measures "
                           "memorisation, not retrieval. Use --holdout-fraction and "
                           "score only the withheld groups."),
               **info}
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in payload.items() if k != "loss_curve"},
                     indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
