#!/usr/bin/env python3
"""Split the mined templates by source program, and write the training text.

Stage I of docs/SOAK_PLAN.md forbids a random split, for two reasons it states
plainly. A random split leaks, because the vocabulary is mined from the whole
corpus and a held-out template's rare tokens are in the lookup table before
scoring starts. And it tests the wrong risk: templates from one program are near
duplicates of each other, so a random split asks whether a message can be placed
beside its own siblings, which it can. The risk that matters is a program the
model has never seen.

The rule below is fixed before anything is scored, so the sets cannot be chosen
to flatter a result:

1. Keep sources with at least --min-templates templates, and drop any source
   whose name contains "unknown" — round 3 excluded that bucket because its
   program name is a regex failure, not a program
2. Drop the largest source from the evaluation pool. infologger/ODC/ODC owns
   57 % of the templates, and putting it on either side would make that side one
   program scoring against itself
3. Sort the rest by template count, largest first, name as tie-break
4. Take the top ten. Odd ranks go to dev, even ranks to held-out, so the two
   sets carry a similar size profile
5. Every other source is training text, including the giant one and every source
   too small to score

Each set gets its own null, and the null moves with the split. The macro null of
0.035 in docs/EMBEDDING_RESULTS.md is a property of the 23-source set, not of the
metric. A five-source set sits near 0.20, and a purity read against the wrong
null says the opposite of the truth.
"""
import argparse
import json
import os
from collections import Counter, defaultdict


def load_dump(path):
    rows = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not parts[3].strip():
                continue
            rows.append({"count": int(parts[0]), "source": parts[1],
                         "sources": int(parts[2]), "template": parts[3]})
    return rows


def macro_null(rows):
    """The purity random neighbours would reach inside this set alone.

    Averaged over sources, one vote each, exactly as the macro purity is. A null
    written per template instead is dominated by the largest source and comes out
    an order of magnitude too high."""
    counts = Counter(r["source"] for r in rows)
    pool = len(rows)
    if pool < 2 or not counts:
        return None
    return sum((n - 1) / (pool - 1) for n in counts.values()) / len(counts)


def choose(rows, min_templates, exclude_largest):
    per_source = Counter(r["source"] for r in rows)
    eligible = {s: n for s, n in per_source.items()
                if n >= min_templates and "unknown" not in s}
    dropped = None
    if exclude_largest and eligible:
        dropped = max(eligible.items(), key=lambda kv: (kv[1], kv[0]))[0]
        eligible.pop(dropped)
    ranked = sorted(eligible.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    dev = [s for i, (s, _) in enumerate(ranked) if i % 2 == 0]
    heldout = [s for i, (s, _) in enumerate(ranked) if i % 2 == 1]
    return dev, heldout, dropped, per_source


def write_dump(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write("%d\t%s\t%d\t%s\n" % (
                r["count"], r["source"], r["sources"], r["template"]))


def write_training_lines(corpus, out, evaluation_sources, limit):
    kept = 0
    read = 0
    with open(corpus, "r", errors="replace") as fh, open(out, "w") as dest:
        for raw in fh:
            read += 1
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            key = "%s/%s" % (parts[0], parts[1])
            if key in evaluation_sources:
                continue
            dest.write(parts[2] + "\n")
            kept += 1
            if limit and kept >= limit:
                break
    return read, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("templates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus", default="")
    ap.add_argument("--min-templates", type=int, default=10)
    ap.add_argument("--keep-largest", action="store_true")
    ap.add_argument("--train-line-limit", type=int, default=0)
    args = ap.parse_args()

    rows = load_dump(args.templates)
    dev_sources, heldout_sources, dropped, per_source = choose(
        rows, args.min_templates, not args.keep_largest)
    evaluation = set(dev_sources) | set(heldout_sources)

    os.makedirs(args.out, exist_ok=True)
    dev_rows = [r for r in rows if r["source"] in set(dev_sources)]
    heldout_rows = [r for r in rows if r["source"] in set(heldout_sources)]
    write_dump(os.path.join(args.out, "dev.tsv"), dev_rows)
    write_dump(os.path.join(args.out, "heldout.tsv"), heldout_rows)

    report = {
        "template_dump": args.templates,
        "templates_total": len(rows),
        "sources_total": len(per_source),
        "min_templates": args.min_templates,
        "excluded_largest": dropped,
        "dev": {
            "sources": sorted(dev_sources),
            "templates": len(dev_rows),
            "per_source": {s: per_source[s] for s in sorted(dev_sources)},
            "macro_null": round(macro_null(dev_rows), 4) if dev_rows else None,
        },
        "heldout": {
            "sources": sorted(heldout_sources),
            "templates": len(heldout_rows),
            "per_source": {s: per_source[s] for s in sorted(heldout_sources)},
            "macro_null": round(macro_null(heldout_rows), 4) if heldout_rows else None,
        },
        "train_sources": sorted(s for s in per_source if s not in evaluation),
    }

    if args.corpus:
        read, kept = write_training_lines(
            args.corpus, os.path.join(args.out, "train-lines.txt"),
            evaluation, args.train_line_limit)
        report["train_lines"] = {"corpus_lines_read": read, "lines_written": kept}

    with open(os.path.join(args.out, "splits.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
