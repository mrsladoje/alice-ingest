#!/usr/bin/env python3
"""Relabel a corpus by the format family the collector's own cascade assigns.

`corpus.py` labels a line by where it was read from: everything under the
process-log tree is `stdout`. That is the collection family, not the format
family. The tree holds two formats, and they want different mining recipes,
because DataDistribution's own bracket carries a full date that the DPL strip
rule leaves in place.

The mapping is not restated here. It is the cascade out of the rendered
production configuration, so a parser added to the collector splits the corpus
the same way without anyone editing this file.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "collector"))
from coverage import cascade  # noqa: E402

# Which parser's match means which mining family.
FAMILY_OF = {
    "datadist": "datadist",
    "dpl": "dpl",
    "dpl_noclock": "dpl",
    "stdout_root": "dpl",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", default="stdout",
                    help="the collection family to split by format")
    ap.add_argument("--tag", default="stdout")
    args = ap.parse_args()

    work = os.path.join(os.path.expanduser("~"), ".cache", "alice-coverage")
    os.makedirs(work, exist_ok=True)
    chain = cascade(args.tag, work)
    counts = {}
    with open(args.out, "w", buffering=1 << 20) as out:
        for path in args.corpus:
            with open(path, errors="replace") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t", 2)
                    if len(parts) != 3:
                        continue
                    family, source, message = parts
                    if family == args.split:
                        family = "unclaimed"
                        for name, rx in chain:
                            if rx.match(message):
                                family = FAMILY_OF.get(name, name)
                                break
                    counts[family] = counts.get(family, 0) + 1
                    out.write("%s\t%s\t%s\n" % (family, source, message))
    for family, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("%-12s %10d" % (family, n))


if __name__ == "__main__":
    sys.exit(main())
