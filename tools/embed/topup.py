#!/usr/bin/env python3
"""Add the candidates a later system found that the first pool never held.

Pooling from four systems cannot anticipate what a fifth retrieves. When judged
coverage at rank 10 falls under the threshold the plan sets, the answer is to
judge the missing candidates, not to lower the threshold or to score an unjudged
result as irrelevant.

This writes a new pool file holding only the unjudged rows, in the same column
shape judgekit deals, so the second judging round reuses the same blinding.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
from pool import QRELS_COLUMNS


def coverage(runs, qrels, queries, cutoff=10):
    per_system = {}
    for name, run in runs.items():
        values = []
        for qid, rows in run["results"].items():
            if qid not in queries:
                continue
            ranked = [cid for cid, _ in rows]
            judged = bench.judged_at_k(ranked, qrels, qid, cutoff)
            if judged is not None:
                values.append(judged)
        per_system[name] = sum(values) / len(values) if values else None
    return per_system


def missing(runs, qrels, queries, cutoff=10):
    out = defaultdict(dict)
    for name, run in runs.items():
        for qid, rows in run["results"].items():
            if qid not in queries:
                continue
            for cid, _ in rows[:cutoff]:
                if qrels.grade(qid, cid) is bench.UNJUDGED and cid not in out[qid]:
                    out[qid][cid] = name
    return out


def write(path, gaps, queries, corpus, source):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    written = 0
    with open(path, "w") as fh:
        fh.write("\t".join(QRELS_COLUMNS) + "\n")
        for qid in queries:
            for cid, finder in gaps.get(qid, {}).items():
                doc = corpus.by_id.get(cid)
                if doc is None:
                    continue
                template = doc["template"].replace("\t", " ")
                row = [qid, queries[qid]["text"].replace("\t", " "), cid, -1,
                       "unreviewed", "", 0, source, finder, template, template]
                fh.write("\t".join(str(v) for v in row) + "\n")
                written += 1
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cutoff", type=int, default=10)
    parser.add_argument("--source", default="topup-2026-09-08")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    qrels = bench.Qrels.load(args.qrels)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path))
    runs = {}
    for path in args.runs:
        run = bench.read_run(path)
        runs[run.get("system") or os.path.basename(path)] = run

    before = coverage(runs, qrels, queries, args.cutoff)
    gaps = missing(runs, qrels, queries, args.cutoff)
    written = write(args.out, gaps, queries, corpus, args.source)
    report = {
        "cutoff": args.cutoff,
        "judged_coverage_before": before,
        "queries_with_gaps": len(gaps),
        "rows_needing_judgement": written,
        "runs": sorted(runs),
        "note": ("judged coverage below 0.95 at rank 10 is a stop rule for the "
                 "affected comparison until these rows are graded"),
    }
    with open(args.report, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
