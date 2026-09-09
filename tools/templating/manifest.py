#!/usr/bin/env python3
"""Freeze a corpus: what it is, where it came from, and what was done to it.

`docs/SEMANTIC_PLAN.md` requires a corpus manifest before any model comparison,
and the reason is not bookkeeping. Two of its own binding findings are that
parser changes can alter the template set more than a model change, and that a
result is only a result if the corpus behind it can be named. A benchmark run
against "the corpus" with no revision attached cannot be repeated and cannot be
compared with the run before it.

So this records the inputs (files, sizes, sha256, line counts, time windows),
the code that shaped them (git revision, and the revision of the parser and
recipe modules specifically), and the recipe itself, field by field, rather than
a pointer to whatever those files happen to say later.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import drainbench  # noqa: E402
import masking  # noqa: E402

TIMESTAMPS = [
    re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"),
]


def sha256(path, limit=0):
    digest = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            read += len(chunk)
            if limit and read >= limit:
                break
    return digest.hexdigest()


def git(*args):
    try:
        return subprocess.run(["git", "-C", REPO] + list(args),
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def file_revision(relative):
    """The last commit that touched one file, so a recipe change is visible
    even when the corpus and the repository revision did not move."""
    rev = git("log", "-1", "--format=%H %cI", "--", relative)
    if not rev:
        return None
    commit, _, when = rev.partition(" ")
    return {"path": relative, "commit": commit, "committed": when,
            "sha256": sha256(os.path.join(REPO, relative))}


def scan(paths):
    per_family = collections.defaultdict(lambda: {
        "lines": 0, "programs": collections.Counter(), "first": None,
        "last": None, "truncated_lines": 0})
    for path in paths:
        with open(path, errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t", 2)
                if len(parts) != 3:
                    continue
                family, source, message = parts
                row = per_family[family]
                row["lines"] += 1
                row["programs"][source] += 1
                # corpus.py escapes newlines and tabs, so a record is one line;
                # a line at the reader's ceiling is the one shape that could
                # have lost content, and it is counted rather than assumed away.
                if len(message) >= 65536:
                    row["truncated_lines"] += 1
                for pattern in TIMESTAMPS:
                    found = pattern.search(message)
                    if not found:
                        continue
                    stamp = found.group(1)
                    if row["first"] is None or stamp < row["first"]:
                        row["first"] = stamp
                    if row["last"] is None or stamp > row["last"]:
                        row["last"] = stamp
                    break
    return per_family


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+")
    ap.add_argument("--id", default="",
                    help="corpus identifier; defaults to a hash of the inputs")
    ap.add_argument("--out", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    sources = []
    for path in args.corpus:
        stat = os.stat(path)
        sources.append({
            "path": os.path.relpath(path, REPO) if path.startswith(REPO) else path,
            "bytes": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(
                stat.st_mtime, datetime.timezone.utc)
                .replace(microsecond=0).isoformat(),
            "sha256": sha256(path),
        })

    per_family = scan(args.corpus)
    families = {}
    for family, row in sorted(per_family.items()):
        known = family in drainbench.RECIPE_SIM
        families[family] = {
            "lines": row["lines"],
            "distinct_programs": len(row["programs"]),
            "top_programs": row["programs"].most_common(10),
            "unknown_program_lines": row["programs"].get("unknown", 0),
            "event_time_first_seen": row["first"],
            "event_time_last_seen": row["last"],
            "lines_at_the_reader_ceiling": row["truncated_lines"],
            "has_a_mining_recipe": known,
            "recipe": {
                "depth": drainbench.RECIPE_DEPTH,
                "max_children": drainbench.RECIPE_MAX_CHILDREN,
                "similarity": drainbench.RECIPE_SIM.get(family),
                "padded_separators": drainbench.RECIPE_PAD.get(family) or None,
                "parametrise_numeric_tokens": drainbench.RECIPE_NUMERIC.get(family),
                "envelope_strip": (
                    drainbench.RECIPE_STRIP.get(family).pattern
                    if drainbench.RECIPE_STRIP.get(family) is not None else None),
            } if known else None,
        }

    identifier = args.id or hashlib.sha256(
        "".join(s["sha256"] for s in sources).encode()).hexdigest()[:16]

    manifest = {
        "corpus_id": identifier,
        "created": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
        "note": args.note,
        "git_revision": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "code_revisions": [r for r in (
            file_revision("tools/templating/drainbench.py"),
            file_revision("tools/templating/masking.py"),
            file_revision("tools/templating/corpus.py"),
            file_revision("tools/templating/refamily.py"),
            file_revision("deploy/roles/sweet_collector/templates/parsers.yaml.j2"),
        ) if r],
        "sources": sources,
        "families": families,
        "total_lines": sum(f["lines"] for f in families.values()),
        # The masking rules by name and pattern, not by reference. A rule that
        # changes later must not silently change what this manifest described.
        "masking_rules": [{"mask": rule["mask_with"],
                           "pattern": rule["regex_pattern"]}
                          for rule in masking.REFERENCE],
        "truncation": "corpus.py escapes newline and tab, so one corpus line is "
                      "one record; nothing is truncated by length on write.",
        "clock_domains": {
            "infologger": "archive event time, from the mysqldump row",
            "dds": "archive event time, in the line",
            "dpl": "archive date from the file name, time of day from the line",
            "datadist": "archive event time, in the line",
            "ildaemon": "live node time",
            "journald": "live node time",
        },
    }
    text = json.dumps(manifest, indent=2, sort_keys=False)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
