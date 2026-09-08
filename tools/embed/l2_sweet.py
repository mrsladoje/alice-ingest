#!/usr/bin/env python3
"""L2: the pinned Sweet Search reference, through a thin ALICE data adapter.

The reference repository is never modified. The adapter writes each canonical
template into its own file in a scratch tree, lets Sweet Search index that tree
with its own indexer, and maps the file paths in its output back to canonical
identifiers.

Sweet Search is a code search engine. Running it on log templates is exactly the
comparison the plan asks for: a maintained custom lexical system, measured on
ALICE data, against ALICE's own reduced port of it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench

REPO = "/Users/admin/Projects/sweet-search-private"
PINNED = "8bbbc14b9176ceb192c591b925c41b6a3198b482"
CLI = os.path.join(REPO, "core", "cli.js")


def verify_pin():
    """The plan pins a commit, so HEAD moving must not silently change L2."""
    head = subprocess.check_output(["git", "-C", REPO, "rev-parse", "HEAD"]).decode().strip()
    present = subprocess.run(["git", "-C", REPO, "cat-file", "-t", PINNED],
                             capture_output=True).stdout.decode().strip()
    return {"pinned_commit": PINNED, "head": head,
            "head_is_the_pin": head == PINNED,
            "pin_reachable": present == "commit"}


def materialise(corpus, root, extension=".txt"):
    """One file per canonical group, named by its identifier."""
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)
    for doc in corpus.docs:
        path = os.path.join(root, doc["canonical_id"] + extension)
        with open(path, "w") as fh:
            fh.write(bench.represent(doc, "R1") + "\n")
    return len(corpus.docs)


def run_cli(args, cwd, timeout=900):
    started = time.time()
    result = subprocess.run(["node", CLI] + args, cwd=cwd, capture_output=True,
                            timeout=timeout)
    return {"code": result.returncode, "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace")[-2000:],
            "seconds": round(time.time() - started, 1)}


def build(root):
    return run_cli(["index", "--full", "--quiet"], cwd=root)


def search(root, text, depth=200, mode="lexical"):
    out = run_cli([text, "--mode", mode, "--json", "--top", str(depth)], cwd=root)
    if out["code"] != 0:
        return [], out
    try:
        payload = json.loads(out["stdout"])
    except json.JSONDecodeError:
        return [], out
    rows = payload.get("results") or payload.get("hits") or []
    ranked = []
    for position, row in enumerate(rows):
        path = row.get("file") or row.get("path") or ""
        canonical = os.path.splitext(os.path.basename(path))[0]
        score = row.get("score")
        ranked.append((canonical, float(score) if score is not None
                       else float(depth - position)))
    return ranked, out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="downloads/frozen/l2-tree")
    parser.add_argument("--queries", nargs="*", default=[])
    parser.add_argument("--out", default="downloads/frozen/logs/l2-probe.json")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()

    report = {"pin": verify_pin()}
    if args.build:
        corpus = bench.Corpus.load(args.corpus)
        report["files"] = materialise(corpus, args.root)
        report["index"] = build(args.root)
    for text in args.queries:
        ranked, raw = search(args.root, text)
        report.setdefault("probes", []).append(
            {"query": text, "hits": len(ranked), "top": ranked[:5],
             "code": raw["code"], "seconds": raw["seconds"],
             "stderr": raw["stderr"][-400:]})
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report, indent=1)[:3000])


if __name__ == "__main__":
    main()
