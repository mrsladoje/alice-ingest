#!/usr/bin/env python3
"""Price the embedding ladder in round 3 of docs/SOAK_PLAN.md, on real templates.

Two things come out of this. Throughput per core says whether a rung fits the
budget the worker has left. Neighbour agreement says whether the cheap rung
groups our own log templates the way the expensive one does — which is the only
question that matters here, because a published benchmark average was measured
on prose and our texts are not prose.

Everything runs on ONE core. The embedder will only ever get a fraction of four,
so a figure taken with every core busy would describe the laptop, not the plan.
"""
import argparse
import json
import os
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np
from collections import Counter, defaultdict


def load_templates(path, limit=0):
    """Accepts the two-column dump (count, template) and the four-column one
    (count, dominant source, source count, template). Without the source column
    the purity check below cannot run, and says so rather than inventing labels."""
    texts, sources = [], []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4 and parts[3].strip():
                texts.append(parts[3])
                sources.append(parts[1])
            elif len(parts) == 2 and parts[1].strip():
                texts.append(parts[1])
                sources.append(None)
            else:
                continue
            if limit and len(texts) >= limit:
                break
    return texts, sources


def sort_by_length(texts):
    """Batch padding costs whatever the longest member costs, so a batch of
    similar lengths pads to almost nothing. Returns the order too, because the
    vectors have to go back where they came from before anything compares them."""
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    return [texts[i] for i in order], order


def unsort(vectors, order):
    out = np.empty_like(vectors)
    out[np.asarray(order)] = vectors
    return out


def encode_static(model_name, texts, batch_size):
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(model_name)
    t0 = time.process_time()
    vectors = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32), time.process_time() - t0


def encode_transformer(model_name, texts, batch_size, max_tokens, quantize):
    import torch
    torch.set_num_threads(1)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name, device="cpu")
    model.max_seq_length = max_tokens
    if quantize:
        engines = torch.backends.quantized.supported_engines
        for candidate in ("qnnpack", "fbgemm", "onednn"):
            if candidate in engines:
                torch.backends.quantized.engine = candidate
                break
        model = torch.quantization.quantize_dynamic(
            model, {torch.nn.Linear}, dtype=torch.qint8)
    t0 = time.process_time()
    with torch.inference_mode():
        vectors = model.encode(texts, batch_size=batch_size,
                               convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32), time.process_time() - t0


def normalise(vectors):
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def neighbours(vectors, k):
    unit = normalise(vectors)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    return np.argpartition(-sims, k, axis=1)[:, :k]


def source_purity(neighbour_index, sources, k):
    """How often a template's nearest neighbours come from the same program.

    This is the only quality question our own data can answer without labels.
    Read it against the null: the purity random neighbours would reach on the
    same source distribution. A model that beats the null is finding structure
    that is really there; one that matches it is arranging noise."""
    known = [i for i, s in enumerate(sources) if s]
    if not known:
        return None, None
    hits = 0
    for i in known:
        hits += sum(1 for j in neighbour_index[i][:k] if sources[j] == sources[i])
    counts = Counter(sources[i] for i in known)
    pool = len(sources)
    null = sum((n / len(known)) * ((n - 1) / (pool - 1)) for n in counts.values())
    return hits / (len(known) * k), null


def macro_source_purity(neighbour_index, sources, k, min_templates):
    """The same question, averaged per source rather than per template.

    One source owns 57 % of the templates in this corpus, so the plain average
    is mostly that source scoring against itself and every model looks good.
    Averaging the sources instead gives each program one vote, which is what
    makes the number able to separate the rungs at all.

    The null is a macro average too: the chance a random template shares a given
    source, averaged over sources rather than over templates. A null written the
    other way is dominated by the one source that made the plain average
    useless, and it comes out an order of magnitude too high."""
    counts = Counter(s for s in sources if s and "unknown" not in s)
    usable = {s for s, n in counts.items() if n >= min_templates}
    if not usable:
        return None, None
    by_source = defaultdict(list)
    for i, s in enumerate(sources):
        if s in usable:
            hits = sum(1 for j in neighbour_index[i][:k] if sources[j] == s)
            by_source[s].append(hits / k)
    scores = [sum(v) / len(v) for v in by_source.values()]
    pool = len(sources)
    null = sum((counts[s] - 1) / (pool - 1) for s in usable) / len(usable)
    return sum(scores) / len(scores), null


def agreement(reference, candidate, k):
    """Mean overlap of the k nearest templates. 1.0 means the cheap model puts
    the same templates next to each other as the reference does."""
    hits = 0
    for a, b in zip(reference, candidate):
        hits += len(set(a.tolist()) & set(b.tolist()))
    return hits / (len(reference) * k)


RUNGS = [
    {"name": "potion-base-8M", "id": "minishlab/potion-base-8M", "kind": "static"},
    {"name": "potion-base-32M", "id": "minishlab/potion-base-32M", "kind": "static"},
    {"name": "potion-retrieval-32M", "id": "minishlab/potion-retrieval-32M", "kind": "static"},
    {"name": "paraphrase-MiniLM-L3-v2", "id": "sentence-transformers/paraphrase-MiniLM-L3-v2", "kind": "transformer"},
    {"name": "all-MiniLM-L6-v2", "id": "sentence-transformers/all-MiniLM-L6-v2", "kind": "transformer"},
    {"name": "all-MiniLM-L6-v2-int8", "id": "sentence-transformers/all-MiniLM-L6-v2", "kind": "transformer", "quantize": True},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("templates")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--reference", default="all-MiniLM-L6-v2")
    ap.add_argument("--min-templates", type=int, default=10,
                    help="sources with fewer templates are left out of the macro purity")
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    texts, sources = load_templates(args.templates, args.limit)
    sorted_texts, order = sort_by_length(texts)
    print("templates: %d" % len(texts), flush=True)

    wanted = [r for r in RUNGS if not args.only or r["name"] in args.only.split(",")]
    results = []
    vectors = {}
    for rung in wanted:
        try:
            if rung["kind"] == "static":
                vecs, cpu = encode_static(rung["id"], sorted_texts, args.batch_size)
            else:
                vecs, cpu = encode_transformer(rung["id"], sorted_texts, args.batch_size,
                                               args.max_tokens, rung.get("quantize", False))
        except Exception as exc:
            print("%-26s FAILED %s" % (rung["name"], exc), flush=True)
            results.append({"model": rung["name"], "error": str(exc)})
            continue
        vectors[rung["name"]] = unsort(vecs, order)
        rate = len(texts) / cpu if cpu else None
        results.append({
            "model": rung["name"],
            "dimensions": int(vecs.shape[1]),
            "core_seconds": round(cpu, 3),
            "templates_per_core_second": round(rate, 1) if rate else None,
            "core_seconds_per_million_templates": round(1e6 * cpu / len(texts), 1),
        })
        print("%-26s %6.1f /core-s  %4dd" % (
            rung["name"], rate, vecs.shape[1]), flush=True)

    ref = neighbours(vectors[args.reference], args.k) if args.reference in vectors else None
    null = None
    macro_null_value = [None]
    for row in results:
        if row["model"] not in vectors:
            continue
        cand = neighbours(vectors[row["model"]], args.k)
        if ref is not None:
            row["neighbour_agreement"] = round(agreement(ref, cand, args.k), 3)
        purity, null = source_purity(cand, sources, args.k)
        if purity is not None:
            row["source_purity"] = round(purity, 3)
        macro, macro_null = macro_source_purity(cand, sources, args.k, args.min_templates)
        if macro is not None:
            row["macro_source_purity"] = round(macro, 3)
            macro_null_value[0] = macro_null

    report = {
        "templates": len(texts),
        "batch_size": args.batch_size,
        "max_tokens": args.max_tokens,
        "k": args.k,
        "reference": args.reference,
        "source_purity_null": round(null, 3) if null else None,
        "macro_source_purity_null": (round(macro_null_value[0], 3)
                                     if macro_null_value[0] else None),
        "min_templates_per_source": args.min_templates,
        "rungs": results,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
