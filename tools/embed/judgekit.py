#!/usr/bin/env python3
"""Hand a judging pool out to assessors, and read their grades back.

`docs/SEMANTIC_PLAN.md` puts three conditions on a judgement and all three are
mechanical, so they belong in a tool rather than in a person's discipline:

  * the assessor sees the template, its stable metadata, its frequency and
    redacted examples;
  * the assessor never sees which system returned the candidate, or its score;
  * candidate order is randomised before review.

`--deal` writes one item file per assessor. `--collect` reads the graded files
back, merges them into the qrels, and measures how much two assessors agreed —
raw agreement and weighted Cohen's kappa, which is what the plan asks for when a
second assessor judges a sample.

Nothing here decides who the assessor is. A machine assessor is recorded by
name, the same as a person, and `assessor_kind` says which it was. The plan
forbids machine labels from selecting a retrieval system; it does not forbid
them from rehearsing the pipeline that a person will later use.
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "templating"))

import masking  # noqa: E402

GRADES = (0, 1, 2, 3)
EXAMPLES_SHOWN = 2


def item_id(query_id, canonical_id):
    return hashlib.sha256(("%s|%s" % (query_id, canonical_id))
                          .encode("utf-8")).hexdigest()[:12]


def read_qrels(path):
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(header, line.rstrip("\n").split("\t"))) for line in fh]


def read_corpus(path):
    groups = {}
    with open(path) as fh:
        for line in fh:
            record = json.loads(line)
            groups[record["canonical_id"]] = record
    return groups


def redact(text):
    return masking.reference_mask(text)


def item_of(row, group):
    programs = list(group["programs"])[:5]
    examples = [redact(e) for e in group.get("examples", [])[:EXAMPLES_SHOWN]]
    return {
        "item_id": item_id(row["query_id"], row["canonical_id"]),
        "query": row["query"],
        "template": group["template"],
        "written_by": programs,
        "programs_in_total": group["distinct_programs"],
        "log_sources": list(group["families"]),
        "severity": group["severity_class"],
        "times_seen": group["lines"],
        "contentless": group["contentless"],
        "examples": examples,
    }


def deal(args):
    rows = [r for r in read_qrels(args.qrels) if r["canonical_id"]]
    groups = read_corpus(args.corpus)
    items = {}
    for row in rows:
        group = groups.get(row["canonical_id"])
        if group is None:
            continue
        item = item_of(row, group)
        items[item["item_id"]] = item

    ordered = sorted(items.values(), key=lambda i: i["item_id"])
    rng = random.Random(args.seed)
    rng.shuffle(ordered)

    by_query = collections.defaultdict(list)
    for item in ordered:
        by_query[item["query"]].append(item)
    queries = sorted(by_query)

    os.makedirs(args.out, exist_ok=True)
    assignments = []
    for number in range(args.primary):
        mine = [q for n, q in enumerate(queries) if n % args.primary == number]
        pool = [i for q in mine for i in by_query[q]]
        rng.shuffle(pool)
        assignments.append(("primary-%d" % (number + 1), pool, mine))

    if args.second_pass:
        stratified = []
        for query in queries:
            pool = by_query[query]
            take = max(1, int(round(len(pool) * args.sample / 100.0)))
            stratified.extend(rng.sample(pool, take))
        for number in range(args.second_pass):
            pool = list(stratified)
            rng.shuffle(pool)
            assignments.append(("second-%d" % (number + 1), pool, queries))

    manifest = []
    for name, pool, queries_covered in assignments:
        path = os.path.join(args.out, "items-%s.jsonl" % name)
        with open(path, "w") as fh:
            for item in pool:
                fh.write(json.dumps(item, sort_keys=True) + "\n")
        manifest.append({"assessor": name, "items": len(pool),
                         "queries": len(queries_covered), "path": path})
        print("%-10s %4d items over %2d queries  %s"
              % (name, len(pool), len(queries_covered), path))
    with open(os.path.join(args.out, "deal.json"), "w") as fh:
        fh.write(json.dumps({"seed": args.seed, "items": len(items),
                             "assessors": manifest}, indent=2) + "\n")
    return 0


def read_grades(path):
    grades = {}
    with open(path, errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 2 or parts[0] in ("item_id", ""):
                continue
            try:
                grade = int(parts[1])
            except ValueError:
                continue
            if grade not in GRADES:
                continue
            grades[parts[0]] = (grade, parts[2] if len(parts) > 2 else "")
    return grades


def weighted_kappa(pairs):
    """Cohen's kappa with quadratic weights, on ordered grades."""
    if not pairs:
        return None
    labels = sorted(GRADES)
    index = {g: n for n, g in enumerate(labels)}
    size = len(labels)
    observed = [[0.0] * size for _ in range(size)]
    for first, second in pairs:
        observed[index[first]][index[second]] += 1
    total = float(len(pairs))
    rows = [sum(row) / total for row in observed]
    columns = [sum(observed[r][c] for r in range(size)) / total
               for c in range(size)]
    numerator = denominator = 0.0
    for r in range(size):
        for c in range(size):
            weight = ((labels[r] - labels[c]) ** 2) / float((size - 1) ** 2)
            numerator += weight * observed[r][c] / total
            denominator += weight * rows[r] * columns[c]
    if denominator == 0:
        return 1.0
    return 1.0 - numerator / denominator


def collect(args):
    rows = read_qrels(args.qrels)
    graded = {}
    for path in sorted(glob.glob(os.path.join(args.graded, "grades-*.tsv"))):
        name = os.path.basename(path)[len("grades-"):-len(".tsv")]
        graded[name] = read_grades(path)
        print("%-10s %4d grades  %s" % (name, len(graded[name]), path))

    primary = {k: v for k, v in graded.items() if k.startswith("primary")}
    second = {k: v for k, v in graded.items() if k.startswith("second")}

    merged = {}
    for name, grades in primary.items():
        for key, (grade, note) in grades.items():
            merged[key] = (grade, note, name)

    out = []
    for row in rows:
        key = item_id(row["query_id"], row["canonical_id"]) \
            if row["canonical_id"] else ""
        if key in merged:
            grade, note, name = merged[key]
            row = dict(row, grade=grade, state="graded", assessor=name,
                       assessor_kind=args.assessor_kind, note=note)
        else:
            row = dict(row, assessor_kind="", note="")
        out.append(row)

    columns = ["query_id", "query", "canonical_id", "grade", "state",
               "assessor", "assessor_kind", "prior_binary", "prior_source",
               "matched_by", "note", "template_then", "template_now"]
    with open(args.out, "w") as fh:
        fh.write("\t".join(columns) + "\n")
        for row in out:
            fh.write("\t".join(str(row.get(c, "")).replace("\t", " ")
                               for c in columns) + "\n")

    agreement = {}
    names = sorted(graded)
    for first in range(len(names)):
        for other in range(first + 1, len(names)):
            one, two = names[first], names[other]
            shared = set(graded[one]) & set(graded[two])
            if len(shared) < args.min_overlap:
                continue
            pairs = [(graded[one][k][0], graded[two][k][0]) for k in sorted(shared)]
            exact = sum(1 for a, b in pairs if a == b)
            binary = sum(1 for a, b in pairs if (a >= 2) == (b >= 2))
            within_one = sum(1 for a, b in pairs if abs(a - b) <= 1)
            agreement["%s vs %s" % (one, two)] = {
                "overlapping_items": len(pairs),
                "raw_agreement": round(exact / len(pairs), 3),
                "agreement_within_one_grade": round(within_one / len(pairs), 3),
                "agreement_on_relevant_or_not": round(binary / len(pairs), 3),
                "weighted_cohens_kappa": round(weighted_kappa(pairs), 3),
            }
    kappas = [row["weighted_cohens_kappa"] for row in agreement.values()]
    if kappas:
        agreement["_summary"] = {
            "pairs_measured": len(kappas),
            "lowest_kappa": min(kappas),
            "highest_kappa": max(kappas),
            "threshold_frozen_in_the_rubric": args.kappa_threshold,
            "meets_the_threshold": min(kappas) >= args.kappa_threshold,
        }

    prior = collections.Counter()
    for row in out:
        if row.get("state") != "graded":
            continue
        prior["%s_prior%s" % ("relevant" if int(row["grade"]) >= 2
                              else "not_relevant", row["prior_binary"])] += 1

    per_query = collections.defaultdict(collections.Counter)
    for row in out:
        if row.get("state") == "graded":
            per_query[row["query"]][int(row["grade"])] += 1

    report = {
        "qrels": args.out,
        "assessor_kind": args.assessor_kind,
        "graded_rows": sum(1 for r in out if r.get("state") == "graded"),
        "ungraded_rows": sum(1 for r in out if r.get("state") != "graded"),
        "grade_histogram": dict(collections.Counter(
            int(r["grade"]) for r in out if r.get("state") == "graded")),
        "agreement": agreement,
        "against_the_old_binary_prior": dict(prior),
        # Round 6 reported this for the old binary labels and read every score
        # against it: with four queries holding one relevant template each,
        # precision at ten is capped by arithmetic well below 1.0.
        "precision_at_10_ceiling": (
            round(sum(min(sum(c[g] for g in (2, 3)), 10) / 10.0
                      for c in per_query.values()) / len(per_query), 3)
            if per_query else None),
        "relevant_per_query": {q: sum(c[g] for g in (2, 3))
                               for q, c in sorted(per_query.items())},
        "pooled_per_query": {q: sum(c.values())
                             for q, c in sorted(per_query.items())},
    }
    with open(args.report, "w") as fh:
        fh.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("graded_rows", "grade_histogram", "agreement")},
                     indent=2))
    print(args.out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qrels", required=True)
    ap.add_argument("--corpus", default="")
    ap.add_argument("--deal", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--graded", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--primary", type=int, default=4)
    ap.add_argument("--second-pass", type=int, default=2)
    ap.add_argument("--sample", type=float, default=20.0,
                    help="percent of each query's pool the second pass judges")
    ap.add_argument("--assessor-kind", default="model")
    ap.add_argument("--seed", type=int, default=20260908)
    ap.add_argument("--min-overlap", type=int, default=20,
                    help="items two assessors must share before their "
                         "agreement is worth reporting")
    ap.add_argument("--kappa-threshold", type=float, default=0.60,
                    help="the value frozen in docs/JUDGING_RUBRIC.md")
    args = ap.parse_args()
    if args.deal:
        if not args.corpus:
            ap.error("--deal needs --corpus")
        return deal(args)
    if args.collect:
        if not args.graded or not args.report:
            ap.error("--collect needs --graded and --report")
        return collect(args)
    ap.error("pass --deal or --collect")


if __name__ == "__main__":
    sys.exit(main())
