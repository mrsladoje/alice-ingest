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
    cpu = time.process_time() - t0

    def again(more):
        return np.asarray(model.encode(more, batch_size=batch_size,
                                       show_progress_bar=False), dtype=np.float32)
    return np.asarray(vectors, dtype=np.float32), cpu, again


def encode_fasttext(model_path, texts, batch_size):
    """A gensim FastText model, averaged over the tokens of a template.

    FastText produces word vectors, not sentence vectors. The mean is the
    standard way to get one from the other, and it is what docs/RESEARCH.md's
    incident-corpus result used. Subword vectors mean an unseen ALICE identifier
    still gets a vector rather than a zero."""
    from gensim.models import FastText
    model = FastText.load(model_path)

    def encode(batch):
        out = np.zeros((len(batch), model.wv.vector_size), dtype=np.float32)
        for i, text in enumerate(batch):
            tokens = text.split()
            if not tokens:
                continue
            out[i] = np.mean([model.wv[t] for t in tokens], axis=0)
        return out

    t0 = time.process_time()
    vectors = encode(texts)
    return vectors, time.process_time() - t0, encode


def encode_onnx(model_name, texts, batch_size, max_tokens):
    """An ONNX Runtime model, pinned to one thread like every other rung.

    Round 3 measured int8 through torch's qnnpack backend and found it 5.4 times
    slower than fp32, and said plainly that the number might not survive a
    different backend or a different architecture. This is that different
    backend. The graph exposes a pooled, normalised sentence_embedding, so no
    pooling is done here."""
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from transformers import AutoTokenizer

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    path = hf_hub_download(model_name, "onnx/model.onnx")
    session = ort.InferenceSession(path, sess_options=options,
                                   providers=["CPUExecutionProvider"])
    tokeniser = AutoTokenizer.from_pretrained(model_name)

    def encode(batch):
        pieces = []
        for start in range(0, len(batch), batch_size):
            chunk = batch[start:start + batch_size]
            enc = tokeniser(chunk, padding=True, truncation=True,
                            max_length=max_tokens, return_tensors="np")
            pieces.append(session.run(["sentence_embedding"], {
                "input_ids": enc["input_ids"].astype("int64"),
                "attention_mask": enc["attention_mask"].astype("int64"),
            })[0])
        return np.concatenate(pieces).astype(np.float32)

    t0 = time.process_time()
    vectors = encode(texts)
    return vectors, time.process_time() - t0, encode


def encode_transformer(model_name, texts, batch_size, max_tokens, quantize,
                       trust_remote_code=False):
    import torch
    torch.set_num_threads(1)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name, device="cpu",
                                trust_remote_code=trust_remote_code)
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
    cpu = time.process_time() - t0

    def again(more):
        with torch.inference_mode():
            return np.asarray(model.encode(more, batch_size=batch_size,
                                           convert_to_numpy=True,
                                           show_progress_bar=False),
                              dtype=np.float32)
    return np.asarray(vectors, dtype=np.float32), cpu, again


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


def load_queries(path):
    with open(path, errors="replace") as fh:
        return [line.strip() for line in fh if line.strip()]


def retrieve(query_vectors, template_vectors, k):
    sims = normalise(query_vectors) @ normalise(template_vectors).T
    return np.argsort(-sims, axis=1)[:, :k]


def load_judgements(path):
    """query, 1 or 0, template — one judged pair a line.

    The middle column is the only one a judge edits, and the file is the same
    shape as the pool this tool writes, so judging is filling in a column."""
    judged = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[1] not in ("0", "1"):
                continue
            judged[(parts[0], parts[2])] = parts[1] == "1"
    return judged


def query_precision(order, queries, texts, judged, k):
    """Precision at k against the hand-judged set.

    An unjudged pair counts as not relevant. That is conservative, and it is
    conservative in the direction that matters: a post-trained model whose new
    neighbours nobody pooled is punished, not rewarded. The count of unjudged
    pairs is reported beside the score so the size of that penalty is visible."""
    hits = 0
    unjudged = 0
    per_query = []
    for i, query in enumerate(queries):
        relevant = 0
        for j in order[i][:k]:
            key = (query, texts[j])
            if key not in judged:
                unjudged += 1
            elif judged[key]:
                relevant += 1
        hits += relevant
        per_query.append(round(relevant / k, 3))
    return hits / (len(queries) * k), unjudged, per_query


CODE_QUERY_PREFIX = "Represent this query for searching relevant code: "

RUNGS = [
    {"name": "potion-base-8M", "id": "minishlab/potion-base-8M", "kind": "static"},
    {"name": "potion-base-32M", "id": "minishlab/potion-base-32M", "kind": "static"},
    {"name": "potion-retrieval-32M", "id": "minishlab/potion-retrieval-32M", "kind": "static"},
    {"name": "potion-code-16M", "id": "minishlab/potion-code-16M", "kind": "static"},
    {"name": "potion-code-16M-v2", "id": "minishlab/potion-code-16M-v2", "kind": "static"},
    {"name": "CodeRankEmbed", "id": "nomic-ai/CodeRankEmbed", "kind": "transformer",
     "trust_remote_code": True, "query_prefix": CODE_QUERY_PREFIX},
    {"name": "CodeRankEmbed-onnx-int8", "id": "mrsladoje/CodeRankEmbed-onnx-int8",
     "kind": "onnx", "query_prefix": CODE_QUERY_PREFIX},
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
    ap.add_argument("--model", action="append", default=[],
                    help="name=kind=id for a model outside the ladder; "
                         "kind is static, transformer or fasttext")
    ap.add_argument("--queries", default="")
    ap.add_argument("--judgements", default="")
    ap.add_argument("--pool-out", default="",
                    help="write every model's top k per query, for hand judging")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    texts, sources = load_templates(args.templates, args.limit)
    sorted_texts, order = sort_by_length(texts)
    print("templates: %d" % len(texts), flush=True)

    queries = load_queries(args.queries) if args.queries else []
    judged = load_judgements(args.judgements) if args.judgements else {}
    pool = {}

    wanted = [r for r in RUNGS if not args.only or r["name"] in args.only.split(",")]
    for spec in args.model:
        name, kind, ident = spec.split("=", 2)
        wanted.append({"name": name, "kind": kind, "id": ident})
    results = []
    vectors = {}
    encoders = {}
    prefixes = {}
    for rung in wanted:
        try:
            if rung["kind"] == "static":
                vecs, cpu, again = encode_static(rung["id"], sorted_texts, args.batch_size)
            elif rung["kind"] == "fasttext":
                vecs, cpu, again = encode_fasttext(rung["id"], sorted_texts, args.batch_size)
            elif rung["kind"] == "onnx":
                vecs, cpu, again = encode_onnx(rung["id"], sorted_texts, args.batch_size,
                                               args.max_tokens)
            else:
                vecs, cpu, again = encode_transformer(rung["id"], sorted_texts, args.batch_size,
                                                      args.max_tokens, rung.get("quantize", False),
                                                      rung.get("trust_remote_code", False))
        except Exception as exc:
            print("%-26s FAILED %s" % (rung["name"], exc), flush=True)
            results.append({"model": rung["name"], "error": str(exc)})
            continue
        vectors[rung["name"]] = unsort(vecs, order)
        encoders[rung["name"]] = again
        prefixes[rung["name"]] = rung.get("query_prefix", "")
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

        if queries:
            prefix = prefixes.get(row["model"], "")
            hits = retrieve(encoders[row["model"]]([prefix + q for q in queries]),
                            vectors[row["model"]], args.k)
            for i, query in enumerate(queries):
                for j in hits[i]:
                    pool.setdefault((query, texts[j]), None)
            if judged:
                precision, unjudged, per_query = query_precision(
                    hits, queries, texts, judged, args.k)
                row["query_precision_at_k"] = round(precision, 3)
                row["query_pairs_unjudged"] = unjudged
                row["query_precision_per_query"] = per_query

    if args.pool_out and pool:
        with open(args.pool_out, "w") as fh:
            for query, template in sorted(pool):
                fh.write("%s\t%s\t%s\n" % (
                    query, "1" if judged.get((query, template)) else "0", template))
        print("pool: %d pairs to %s" % (len(pool), args.pool_out), flush=True)

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
        "queries": len(queries),
        "judged_pairs": len(judged) or None,
        "rungs": results,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
