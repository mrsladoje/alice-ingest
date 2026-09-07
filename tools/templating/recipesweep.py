#!/usr/bin/env python3
"""Sweep one family's mining recipe and report what each cell costs.

Round 6 froze a recipe for three families. The tree those families came from is
now known to be two formats, and three more sources have been added, so the
question is open again for the new ones and worth re-asking for the old.

Four knobs, the ones round 6 found actually move: which separators are padded
apart, the tree depth, the similarity threshold, and whether numeric tokens are
parametrised. Everything else is held at the shipped value.

Four numbers come back for each cell. Templates is how many clusters the corpus
produced. Core-seconds per million is what mining costs forever. Words kept is
how much of the original line survives into the template, and it is the
readability figure. Contentless is the share of templates that are nothing but
wildcards, which is the failure mode a too-aggressive recipe produces.
"""
import argparse
import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drainbench as db  # noqa: E402

WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
MASK = re.compile(r"<[A-Z_]+>|<\*>")


def contentless(template):
    return not WORD.findall(MASK.sub(" ", template))


def cell(paths, family, pad, depth, sim, numeric, limit):
    db.RECIPE_PAD[family] = pad
    db.RECIPE_SIM[family] = sim
    db.RECIPE_NUMERIC[family] = numeric
    db.RECIPE_DEPTH = depth
    report = db.recipe_run(paths, [family], limit, 20)
    stats = report["per_family"][family]
    templates = [r["template"] for r in report["all_templates"]
                 if r["family"] == family]
    empty = sum(1 for t in templates if contentless(t))
    return {
        "pad": pad or "(none)", "depth": depth, "sim": sim,
        "numeric": "parametrised" if numeric else "kept",
        "lines": stats["lines"],
        "templates": stats["templates"],
        "core_s_per_m": stats["core_seconds_per_million"],
        "words_kept_pct": stats["words_kept_pct"],
        "contentless_pct": (round(100.0 * empty / len(templates), 1)
                            if templates else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+")
    ap.add_argument("--family", required=True)
    ap.add_argument("--pads", default=",=,=;,=;:")
    ap.add_argument("--depths", default="6,8")
    ap.add_argument("--sims", default="0.4,0.5")
    ap.add_argument("--numeric", default="1,0")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    pads = args.pads.split(",")
    depths = [int(d) for d in args.depths.split(",") if d]
    sims = [float(v) for v in args.sims.split(",") if v]
    numerics = [v == "1" for v in args.numeric.split(",") if v]

    base_pad = db.RECIPE_PAD[args.family]
    base_sim = db.RECIPE_SIM[args.family]
    base_num = db.RECIPE_NUMERIC[args.family]
    base_depth = db.RECIPE_DEPTH

    rows = []
    print("%-8s %5s %5s %-12s %9s %8s %10s %10s %11s"
          % ("pad", "depth", "sim", "numeric", "lines", "templ",
             "core-s/M", "words %", "contentless"))
    for pad, depth, sim, numeric in itertools.product(pads, depths, sims, numerics):
        row = cell(args.corpus, args.family, pad, depth, sim, numeric, args.limit)
        rows.append(row)
        print("%-8s %5d %5.2f %-12s %9d %8d %10s %10s %10s %%"
              % (row["pad"], row["depth"], row["sim"], row["numeric"],
                 row["lines"], row["templates"], row["core_s_per_m"],
                 row["words_kept_pct"], row["contentless_pct"]))

    db.RECIPE_PAD[args.family] = base_pad
    db.RECIPE_SIM[args.family] = base_sim
    db.RECIPE_NUMERIC[args.family] = base_num
    db.RECIPE_DEPTH = base_depth

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
