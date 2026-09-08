#!/usr/bin/env python3
"""Carry the old judgements onto the frozen corpus, and say what did not carry.

`docs/SEMANTIC_PLAN.md` Stage S1 opens with an audit: the 635 pairs in
`tools/embed/judgements.tsv` were labelled by an AI judge, they are development
data for ever, and they are not ground truth. Two things have to happen before
anyone can use them, and this tool does the mechanical one.

**It re-anchors a label from template text to a canonical identifier.** The old
file names a template by its text. Text moves when the recipe or the parser
moves; an identifier does not. Every migrated row therefore carries the
canonical group it now points at, or says the template is gone.

**It never invents a grade.** The old labels are binary and machine-made. They
arrive as `prior`, the grade arrives as `-1`, and the state arrives as
`unreviewed`. A four-grade judgement is a person's decision, and nothing here
can make one.

Output is `queries.jsonl` and `qrels.tsv`, the schemas the later stages read.
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import freeze  # noqa: E402

UNREVIEWED = -1


SEPARATORS = re.compile(r"\s*([=;:])\s*")


def loose(normalized):
    """The same text with the padded separators unpadded.

    The old labels were made against the `stdout` recipe, which padded `= ;`.
    The process tree is now mined as `dpl`, which pads nothing, so the same
    event is written `a=b` today and `a = b` then. Unpadding both is a migration
    convenience only. It is deliberately NOT the canonical rule: measured over
    the 4,523 shipped templates, folding separators merges two groups out of
    4,304, so the corpus does not pay for it.
    """
    return SEPARATORS.sub(r"\1", normalized)


def load_corpus(path):
    by_normal = {}
    by_loose = collections.defaultdict(set)
    groups = {}
    with open(path) as fh:
        for line in fh:
            record = json.loads(line)
            groups[record["canonical_id"]] = record
            by_normal[record["normalized"]] = record["canonical_id"]
            by_loose[loose(record["normalized"])].add(record["canonical_id"])
    single = {k: next(iter(v)) for k, v in by_loose.items() if len(v) == 1}
    return by_normal, single, groups


def load_judgements(path):
    rows = []
    with open(path, errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            query, label, template = parts[0], parts[1], "\t".join(parts[2:])
            rows.append((query, label.strip(), template))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="corpus.jsonl from freeze.py")
    ap.add_argument("--judgements",
                    default=os.path.join(HERE, "judgements.tsv"))
    ap.add_argument("--queries", default=os.path.join(HERE, "queries.txt"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus-id", default="")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    by_normal, by_loose, groups = load_corpus(args.corpus)
    rows = load_judgements(args.judgements)
    with open(args.queries) as fh:
        query_text = [line.strip() for line in fh if line.strip()]

    carried = collections.Counter()
    per_query = collections.defaultdict(lambda: {"carried": 0, "lost": 0,
                                                 "prior_relevant": 0})
    out_rows = []
    seen = set()
    for query, label, template in rows:
        normalized = freeze.normalize(template)
        canonical = by_normal.get(normalized)
        match = "exact" if canonical else ""
        if canonical is None:
            canonical = by_loose.get(loose(normalized))
            match = "separators unpadded" if canonical else "none"
        state = "carried" if canonical else "lost"
        carried[state] += 1
        carried["matched_%s" % match.replace(" ", "_")] += 1
        per_query[query][state] += 1
        if label == "1":
            per_query[query]["prior_relevant"] += 1
        if canonical and (query, canonical) in seen:
            carried["collapsed_onto_a_judged_group"] += 1
            continue
        if canonical:
            seen.add((query, canonical))
        out_rows.append({
            "query_id": "q%02d" % (query_text.index(query) + 1)
                        if query in query_text else "q??",
            "query": query,
            "canonical_id": canonical or "",
            "grade": UNREVIEWED,
            "state": "unreviewed",
            "assessor": "",
            "prior_binary": label,
            "prior_source": "ai-judge-2026-09-04",
            "matched_by": match,
            "template_then": template,
            "template_now": groups[canonical]["template"] if canonical else "",
        })

    qrels_path = os.path.join(args.out, "qrels.tsv")
    with open(qrels_path, "w") as fh:
        columns = ["query_id", "query", "canonical_id", "grade", "state",
                   "assessor", "prior_binary", "prior_source", "matched_by",
                   "template_then", "template_now"]
        fh.write("\t".join(columns) + "\n")
        for row in out_rows:
            fh.write("\t".join(str(row[k]) for k in columns) + "\n")

    queries_path = os.path.join(args.out, "queries.jsonl")
    with open(queries_path, "w") as fh:
        for number, text in enumerate(query_text, 1):
            fh.write(json.dumps({
                "query_id": "q%02d" % number,
                "text": text,
                "task": "natural_language",
                "split": "calibration",
                "intent_group": "g%02d" % number,
                "provenance": "written for round 6, not from an operator",
                "synthetic": True,
                "class": "unassigned",
            }) + "\n")

    report = {
        "corpus": os.path.relpath(args.corpus),
        "corpus_id": args.corpus_id,
        "pairs_in": len(rows),
        "pairs_out": len(out_rows),
        "carried": carried["carried"],
        "lost": carried["lost"],
        "collapsed_onto_a_judged_group": carried["collapsed_onto_a_judged_group"],
        "matched_exact": carried["matched_exact"],
        "matched_with_separators_unpadded": carried["matched_separators_unpadded"],
        "queries": len(query_text),
        "per_query": {q: dict(v) for q, v in sorted(per_query.items())},
        "grades": "every row is -1 and unreviewed; the prior binary label is kept "
                  "beside it and is not a grade",
    }
    with open(os.path.join(args.out, "migration.json"), "w") as fh:
        fh.write(json.dumps(report, indent=2) + "\n")

    print("pairs %d -> %d rows: %d carried, %d lost, %d collapsed onto a group "
          "already judged for that query"
          % (len(rows), len(out_rows), carried["carried"], carried["lost"],
             carried["collapsed_onto_a_judged_group"]))
    worst = sorted(per_query.items(), key=lambda kv: -kv[1]["lost"])[:5]
    for query, row in worst:
        print("  lost %3d of %3d  %s"
              % (row["lost"], row["lost"] + row["carried"], query))
    print(qrels_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
