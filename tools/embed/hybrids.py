#!/usr/bin/env python3
"""The hybrid systems Stage S7 requires and the first pass never ran.

H4 gets its tuned variant, H6 gets the development-only weights the plan asks
for, and H7, H8 and H10 add the reranking systems. Reranking is where a hybrid
usually earns its keep, so a fusion conclusion drawn without them says less than
it appears to.

Candidate recall is reported at every depth, because a reranker is never blamed
for a document its candidate generator never retrieved.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import compare


class LateReranker:
    """MaxSim scoring against a cached corpus token matrix."""

    def __init__(self, cache_path, model="LateOn"):
        blob = np.load(cache_path, allow_pickle=True)
        self.tokens = blob["tokens"]
        self.offsets = blob["offsets"]
        self.ids = list(blob["ids"])
        self.position = {cid: i for i, cid in enumerate(self.ids)}
        self.model = str(blob["model"])
        self._adapter = None

    def encode_queries(self, texts):
        import adapters
        if self._adapter is None:
            self._adapter = adapters.LateAdapter(self.model)
        return self._adapter.encode_query(texts)

    def score(self, query_tokens, candidate_ids):
        q = np.asarray(query_tokens, dtype=np.float32)
        out = {}
        for cid in candidate_ids:
            i = self.position.get(cid)
            if i is None:
                out[cid] = float("-inf")
                continue
            window = self.tokens[self.offsets[i]:self.offsets[i + 1]]
            out[cid] = float((window @ q.T).max(axis=0).sum())
        return out


class CrossEncoderReranker:
    """Qwen3-Reranker-0.6B, used as a reranker only, never as a retriever."""

    def __init__(self, corpus, arm="R1", batch_size=16):
        self.corpus = corpus
        self.arm = arm
        self.batch_size = batch_size
        self._model = None
        self.instruction = ("Given a web search query, retrieve relevant "
                            "passages that answer the query")

    def load(self):
        from sentence_transformers import CrossEncoder
        import adapters
        self._model = CrossEncoder("Qwen/Qwen3-Reranker-0.6B",
                                   revision=adapters.RERANKER["Qwen3-Reranker-0.6B"]["revision"],
                                   device=adapters.pick_device())
        return self

    def score(self, query, candidate_ids):
        if self._model is None:
            self.load()
        docs = [bench.represent(self.corpus.by_id[cid], self.arm)
                for cid in candidate_ids if cid in self.corpus.by_id]
        usable = [cid for cid in candidate_ids if cid in self.corpus.by_id]
        pairs = [[query, d] for d in docs]
        scores = self._model.predict(pairs, batch_size=self.batch_size,
                                     show_progress_bar=False)
        return {cid: float(s) for cid, s in zip(usable, scores)}


def union_candidates(runs, qid, depth):
    seen, out = set(), []
    for run in runs:
        for cid, _ in run["results"].get(qid, [])[:depth]:
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


def weighted_rrf(runs, qid, depth, constant, weights):
    lists = {name: [cid for cid, _ in run["results"].get(qid, [])[:depth]]
             for name, run in runs.items()}
    return bench.rrf(lists, constant=constant, weights=weights)


def weighted_quantile(runs, qid, depth, weights):
    rows = {name: [(cid, float(s)) for cid, s in run["results"].get(qid, [])[:depth]]
            for name, run in runs.items() if run["results"].get(qid)}
    return bench.score_fusion(rows, normaliser=bench.quantile_normalise,
                              weights=weights)


def build_run(name, results, base_manifest_fields, depth, run_dir):
    manifest = bench.make_manifest(**base_manifest_fields)
    return bench.write_run(os.path.join(run_dir, "%s.json" % name), name, name,
                           "natural_language", manifest, results, depth=depth)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lexical", required=True)
    parser.add_argument("--dense", required=True)
    parser.add_argument("--late", required=True)
    parser.add_argument("--late-cache", default="downloads/frozen/lateon-tokens.npz")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-dir", default="downloads/frozen/runs")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--practical", type=float, default=0.03197847477701114)
    parser.add_argument("--skip-cross-encoder", action="store_true")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    lexical = bench.read_run(args.lexical)
    dense = bench.read_run(args.dense)
    late = bench.read_run(args.late)
    pair = {"lex": lexical, "dense": dense}
    trio = {"lex": lexical, "dense": dense, "late": late}

    def fields(name, fusion, extra=None):
        f = compare.base_manifest(
            name, "03640622026320ba", "development-97-2026-09-08",
            os.path.basename(args.qrels),
            model_id=name, model_revision="see member manifests",
            adapter="hybrid over frozen run files", representation="R1 and R2",
            search_parameters={"depth": args.depth},
            fusion_parameters=fusion, opensearch_version="3.7.0",
            index_uuid="mixed", index_schema_revision="systems.SETTINGS",
            searched_indices="mixed", primary_shards=1, replicas=0,
            candidate_depth=args.depth, refresh_state="refreshed",
            search_pipeline_revision="none", device="cpu",
            finished=compare.now())
        if extra:
            f.update(extra)
        return f

    results_by_name, notes = {}, {}

    # H4 tuned: sweep the rank constant and the weight on the weaker system.
    best = None
    for constant in (10, 20, 60, 120):
        for w_lex in (0.1, 0.25, 0.5, 1.0):
            weights = {"lex": w_lex, "dense": 1.0}
            runs = {q: [(c, 1.0 / (i + 1)) for i, c in
                        enumerate(weighted_rrf(pair, q, args.depth, constant, weights))]
                    for q in queries}
            scored = bench.score_run({"results": runs}, qrels, queries)
            value = scored["summary"]["ndcg@10"]
            if best is None or value > best[0]:
                best = (value, constant, w_lex, runs)
    notes["H4-tuned"] = {"rank_constant": best[1], "lexical_weight": best[2],
                         "swept": "constant 10/20/60/120, lexical weight 0.1/0.25/0.5/1.0",
                         "tuned_on": "development only"}
    results_by_name["H4-tuned"] = best[3]
    print("H4-tuned  constant=%d lexical_weight=%.2f ndcg=%.4f"
          % (best[1], best[2], best[0]), flush=True)

    # H6 proper: quantile fusion with development-only weights.
    best6 = None
    for w_lex in (0.1, 0.25, 0.5, 1.0):
        weights = {"lex": w_lex, "dense": 1.0}
        runs = {q: [(c, 1.0 / (i + 1)) for i, c in
                    enumerate(weighted_quantile(pair, q, args.depth, weights))]
                for q in queries}
        scored = bench.score_run({"results": runs}, qrels, queries)
        value = scored["summary"]["ndcg@10"]
        if best6 is None or value > best6[0]:
            best6 = (value, w_lex, runs)
    notes["H6-weighted"] = {"lexical_weight": best6[1],
                            "swept": "lexical weight 0.1/0.25/0.5/1.0",
                            "tuned_on": "development only"}
    results_by_name["H6-weighted"] = best6[2]
    print("H6-weighted lexical_weight=%.2f ndcg=%.4f" % (best6[1], best6[0]), flush=True)

    # H7 and H8: late reranking over candidate sets at swept depths.
    reranker = LateReranker(args.late_cache)
    texts = [row["text"] for row in queries.values()]
    started = time.time()
    query_tokens = reranker.encode_queries(texts)
    encode_seconds = time.time() - started
    token_by_qid = dict(zip(queries.keys(), query_tokens))

    for label, sources in (("H7", [dense]), ("H8", [lexical, dense])):
        for candidate_depth in (20, 50, 100, 200):
            name = "%s-depth%d" % (label, candidate_depth)
            runs, recalls = {}, []
            for qid in queries:
                cands = union_candidates(sources, qid, candidate_depth)
                scores = reranker.score(token_by_qid[qid], cands)
                ranked = bench.rank([scores[c] for c in cands], cands)
                runs[qid] = [(c, scores[c]) for c in ranked]
                r = bench.candidate_recall_at_k(cands, qrels, qid, candidate_depth)
                if r is not None:
                    recalls.append(r)
            results_by_name[name] = runs
            notes[name] = {"candidate_generator": "dense" if label == "H7" else "lexical union dense",
                           "candidate_depth": candidate_depth,
                           "reranker": "LateOn MaxSim",
                           "candidate_recall_at_depth": round(float(np.mean(recalls)), 4)}
            scored = bench.score_run({"results": runs}, qrels, queries)
            print("%-14s ndcg=%.4f candidate_recall=%.4f"
                  % (name, scored["summary"]["ndcg@10"],
                     notes[name]["candidate_recall_at_depth"]), flush=True)

    # H10: the cross-encoder ceiling over the best candidate union.
    if not args.skip_cross_encoder:
        cross = CrossEncoderReranker(corpus, arm="R1")
        for candidate_depth in (20, 50):
            name = "H10-depth%d" % candidate_depth
            runs, recalls = {}, []
            started = time.time()
            for qid, row in queries.items():
                cands = union_candidates([lexical, dense], qid, candidate_depth)
                scores = cross.score(row["text"], cands)
                ranked = bench.rank([scores.get(c, float("-inf")) for c in cands], cands)
                runs[qid] = [(c, scores.get(c, 0.0)) for c in ranked]
                r = bench.candidate_recall_at_k(cands, qrels, qid, candidate_depth)
                if r is not None:
                    recalls.append(r)
            results_by_name[name] = runs
            notes[name] = {"reranker": "Qwen/Qwen3-Reranker-0.6B",
                           "candidate_depth": candidate_depth,
                           "candidate_recall_at_depth": round(float(np.mean(recalls)), 4),
                           "seconds": round(time.time() - started, 1)}
            scored = bench.score_run({"results": runs}, qrels, queries)
            print("%-14s ndcg=%.4f candidate_recall=%.4f %.0fs"
                  % (name, scored["summary"]["ndcg@10"],
                     notes[name]["candidate_recall_at_depth"],
                     notes[name]["seconds"]), flush=True)

    payload = {"arms": {}, "notes": notes,
               "late_query_encode_seconds": round(encode_seconds, 2)}
    for name, runs in results_by_name.items():
        path = build_run(name, runs, fields(name, notes.get(name, "see notes")),
                         args.depth, args.run_dir)
        scored = bench.score_run(bench.read_run(path), qrels, queries)
        payload["arms"][name] = {"summary": scored["summary"],
                                 "run_file": path, "notes": notes.get(name)}
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
