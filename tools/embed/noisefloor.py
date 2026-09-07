#!/usr/bin/env python3
"""Measure the spread of macro source purity, so a ladder can be read at all.

Round 3 ran one pass per model, reported a ladder spanning 0.037 macro purity and
decided a gap of 0.027, and nobody measured what the metric's own spread is.
Stage H is the reason to insist: Drain3 read 17.32 and 22.33 core-seconds per
million on identical work, and four arms of one sweep had to be thrown away
because a contaminated reading looked plausible on its own. A cost metric that
noisy sitting beside a quality metric nobody has questioned is not a state to
rank models in.

The statistic is deterministic given the templates, so the spread being asked
about is not run-to-run jitter. It is sampling spread: this template set is one
draw from an archive that keeps producing new templates at every run and
partition boundary, and the question is how much of a gap between two models
would survive a different draw.

So the resampling is over templates, and no model is ever re-encoded. Vectors are
computed once, the full cosine matrix is cached, and each bootstrap replicate
gathers its own submatrix, finds neighbours inside it and recomputes macro purity
from the resampled source counts.

🔴 A template drawn twice would otherwise be its own nearest neighbour at
distance zero and share its own source, which inflates every replicate by
construction. Every copy of a template is masked out of its own neighbour search,
so a neighbour is always a different template.

Two bootstraps come out of this, because the plan's wording admits two and they
answer different questions. **Cached neighbours** holds the embedding space and
every neighbourhood fixed and resamples only which templates vote: it is the
spread of the averaging, and a neighbour may be a template the replicate did not
draw. **Recomputed neighbours** rebuilds each replicate's neighbourhoods inside
the replicate itself, so the set the model is scored on really is a different
draw. The second is the wider one and it is the one that answers whether a
ranking would survive a different sample of the archive, which stage H showed
keeps growing at every run and partition boundary. Both are reported, and which
one a gate is read against has to be said out loud.

The paired figure is the one that decides a ladder. Two models scored on the same
replicate move together, because a replicate that happens to be easy is easy for
both. The interval on the difference is therefore much narrower than the two
marginal intervals suggest, and it is the interval a claim like "0.027 separates
these two rungs" has to clear.
"""
import argparse
import json
import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np
from collections import Counter

import embedbench


def macro_purity_from_neighbours(neighbour_index, source_ids, k, min_templates,
                                 excluded=frozenset()):
    counts = np.bincount(source_ids)
    usable = np.asarray([s for s in np.nonzero(counts >= min_templates)[0]
                         if s not in excluded], dtype=np.int64)
    if usable.size == 0:
        return None, None
    same = (source_ids[neighbour_index[:, :k]] == source_ids[:, None])
    per_template = same.sum(axis=1) / k
    scores = []
    pool = source_ids.size
    nulls = []
    for source in usable:
        rows = source_ids == source
        scores.append(float(per_template[rows].mean()))
        nulls.append((counts[source] - 1) / (pool - 1))
    return float(np.mean(scores)), float(np.mean(nulls))


def neighbours_within(sim, index, k, block=512):
    """Nearest neighbours inside a resampled set, with self-copies masked out.

    `index` holds original template ids and may repeat. A row must not see any
    copy of itself, so the mask is on the original id rather than on the row."""
    n = index.size
    out = np.empty((n, k), dtype=np.int64)
    for start in range(0, n, block):
        stop = min(start + block, n)
        chunk = sim[np.ix_(index[start:stop], index)]
        chunk[index[start:stop][:, None] == index[None, :]] = -np.inf
        out[start:stop] = np.argpartition(-chunk, k, axis=1)[:, :k]
    return out


def bootstrap(vectors_by_model, sources, k, min_templates, replicates, seed):
    labels = sorted(set(sources))
    lookup = {s: i for i, s in enumerate(labels)}
    source_ids = np.asarray([lookup[s] for s in sources], dtype=np.int64)
    excluded = frozenset(i for s, i in lookup.items() if "unknown" in s)
    n = source_ids.size

    sims = {}
    observed = {}
    for name, vectors in vectors_by_model.items():
        unit = embedbench.normalise(np.asarray(vectors, dtype=np.float32))
        sim = unit @ unit.T
        np.fill_diagonal(sim, -np.inf)
        sims[name] = sim
        full = np.argpartition(-sim, k, axis=1)[:, :k]
        purity, null = macro_purity_from_neighbours(full, source_ids, k, min_templates,
                                                   excluded)
        observed[name] = {"macro_source_purity": purity, "macro_null": null}

    per_template = {}
    for name, sim in sims.items():
        full = np.argpartition(-sim, k, axis=1)[:, :k]
        per_template[name] = (source_ids[full[:, :k]] == source_ids[:, None]).sum(axis=1) / k

    rng = np.random.default_rng(seed)
    draws = {name: [] for name in sims}
    fixed_draws = {name: [] for name in sims}
    null_draws = []
    for _ in range(replicates):
        index = rng.integers(0, n, size=n)
        replicate_ids = source_ids[index]
        counts = np.bincount(replicate_ids, minlength=len(labels))
        usable = [s for s in np.nonzero(counts >= min_templates)[0] if s not in excluded]
        null_here = None
        for name, sim in sims.items():
            near = neighbours_within(sim, index, k)
            purity, null = macro_purity_from_neighbours(
                near, replicate_ids, k, min_templates, excluded)
            if purity is None:
                continue
            draws[name].append(purity)
            null_here = null
            if usable:
                scores = per_template[name][index]
                fixed_draws[name].append(float(np.mean(
                    [scores[replicate_ids == s].mean() for s in usable])))
        if null_here is not None:
            null_draws.append(null_here)

    def interval(values):
        arr = np.asarray(values, dtype=np.float64)
        low, high = np.percentile(arr, [2.5, 97.5])
        return {"replicates": int(arr.size),
                "mean": round(float(arr.mean()), 4),
                "std": round(float(arr.std(ddof=1)), 4),
                "ci95_low": round(float(low), 4),
                "ci95_high": round(float(high), 4),
                "ci95_width": round(float(high - low), 4)}

    report = {"templates": int(n), "sources": len(labels), "k": k,
              "min_templates_per_source": min_templates,
              "null_over_replicates": interval(null_draws) if null_draws else None,
              "models": {}}
    for name in sims:
        report["models"][name] = dict(observed[name], **{
            "macro_source_purity": round(observed[name]["macro_source_purity"], 4),
            "macro_null": round(observed[name]["macro_null"], 4),
            "bootstrap_neighbours_recomputed": interval(draws[name]),
            "bootstrap_neighbours_cached": (interval(fixed_draws[name])
                                            if fixed_draws[name] else None)})

    names = sorted(sims)
    pairs = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if len(draws[a]) != len(draws[b]) or not draws[a]:
                continue
            diff = np.asarray(draws[a]) - np.asarray(draws[b])
            row = interval(diff)
            if fixed_draws[a] and len(fixed_draws[a]) == len(fixed_draws[b]):
                row["cached_neighbours"] = interval(
                    np.asarray(fixed_draws[a]) - np.asarray(fixed_draws[b]))
            row["observed_gap"] = round(
                observed[a]["macro_source_purity"] - observed[b]["macro_source_purity"], 4)
            row["share_of_replicates_a_wins"] = round(float((diff > 0).mean()), 3)
            pairs["%s − %s" % (a, b)] = row
    report["paired_differences"] = pairs
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("templates")
    ap.add_argument("--only", default="potion-base-32M,potion-base-8M,all-MiniLM-L6-v2")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--min-templates", type=int, default=10)
    ap.add_argument("--replicates", type=int, default=400)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    texts, sources = embedbench.load_templates(args.templates)
    if any(s is None for s in sources):
        raise SystemExit("the dump carries no source column, so purity cannot be scored")
    sorted_texts, order = embedbench.sort_by_length(texts)
    print("templates: %d" % len(texts), flush=True)

    wanted = [r for r in embedbench.RUNGS if r["name"] in args.only.split(",")]
    vectors = {}
    for rung in wanted:
        if rung["kind"] == "static":
            vecs, cpu, _ = embedbench.encode_static(rung["id"], sorted_texts, args.batch_size)
        elif rung["kind"] == "onnx":
            vecs, cpu, _ = embedbench.encode_onnx(rung["id"], sorted_texts,
                                                  args.batch_size, args.max_tokens)
        else:
            vecs, cpu, _ = embedbench.encode_transformer(
                rung["id"], sorted_texts, args.batch_size, args.max_tokens,
                rung.get("quantize", False), rung.get("trust_remote_code", False))
        vectors[rung["name"]] = embedbench.unsort(vecs, order)
        print("%-26s encoded in %.1f core-s" % (rung["name"], cpu), flush=True)

    report = bootstrap(vectors, sources, args.k, args.min_templates,
                       args.replicates, args.seed)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
