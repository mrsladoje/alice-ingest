#!/usr/bin/env python3
"""Mine templates out of a corpus written by corpus.py and price the work.

Two numbers come out of this, and they answer different questions.

The new-template rate decides whether round 3 is affordable: an embedding is
paid once per template, so a rate that keeps falling means the cost is bounded
and a rate that stays flat means it is not. It is reported per decile, because
the rate over the whole corpus is dominated by the first few thousand lines and
tells you nothing about the steady state.

The per-line cost is paid on every line forever. It is measured as processor
time, not wall time, and mining is timed apart from reading so that a slow disk
is not billed to Drain.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


MASKING = [
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)(/[-\w./]+)((?=[^A-Za-z0-9])|$)", "mask_with": "PATH"},
    {"regex_pattern": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "mask_with": "UUID"},
    {"regex_pattern": r"\b(\d{1,3}\.){3}\d{1,3}(:\d+)?\b", "mask_with": "IP"},
    {"regex_pattern": r"\b0[xX][0-9a-fA-F]+\b", "mask_with": "HEX"},
    {"regex_pattern": r"\b\d{1,3}(,\d{3})+\b", "mask_with": "NUM"},
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)([\-\+]?\d+\.\d+)((?=[^A-Za-z0-9])|$)", "mask_with": "FLOAT"},
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)([\-\+]?\d+)((?=[^A-Za-z0-9])|$)", "mask_with": "NUM"},
]


def miner(sim_threshold, depth, max_children):
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_threshold
    config.drain_depth = depth
    config.drain_max_children = max_children
    config.drain_max_clusters = None
    config.masking_instructions = []
    config.profiling_enabled = False
    config.snapshot_interval_minutes = 0
    tm = TemplateMiner(config=config)
    from drain3.masking import MaskingInstruction
    tm.masker.masking_instructions = [
        MaskingInstruction(m["regex_pattern"], m["mask_with"]) for m in MASKING
    ]
    return tm


def cost_split(messages, sim_threshold, depth, max_children):
    """Price masking apart from the tree, on the same lines.

    The third figure is the one worth reading: with masking removed every
    distinct number becomes a distinct token, the tree grows clusters it should
    never have had, and the similarity search walks all of them. Masking is not
    an overhead the tree pays — it is what keeps the tree cheap."""
    masked = miner(sim_threshold, depth, max_children)
    t0 = time.process_time()
    for m in messages:
        masked.masker.mask(m)
    mask_only = time.process_time() - t0

    full = miner(sim_threshold, depth, max_children)
    t0 = time.process_time()
    for m in messages:
        full.add_log_message(m)
    full_cost = time.process_time() - t0

    bare = miner(sim_threshold, depth, max_children)
    bare.masker.masking_instructions = []
    t0 = time.process_time()
    for m in messages:
        bare.add_log_message(m)
    bare_cost = time.process_time() - t0

    n = len(messages) or 1
    return {
        "lines": len(messages),
        "mask_only_core_seconds_per_million": round(1e6 * mask_only / n, 2),
        "full_core_seconds_per_million": round(1e6 * full_cost / n, 2),
        "unmasked_core_seconds_per_million": round(1e6 * bare_cost / n, 2),
        "templates_masked": len(full.drain.clusters),
        "templates_unmasked": len(bare.drain.clusters),
    }


def run(path, family_filter, sim_threshold, depth, max_children, limit, deciles):
    tm = miner(sim_threshold, depth, max_children)
    lines = 0
    read_lines = 0
    new_templates = 0
    per_family = Counter()
    per_family_templates = defaultdict(set)
    curve = []
    mining_cpu = 0.0
    started = time.process_time()
    decile_mark = 0

    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            read_lines += 1
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            family, source, message = parts
            if family_filter and family != family_filter:
                continue
            t0 = time.process_time()
            result = tm.add_log_message(message)
            mining_cpu += time.process_time() - t0
            lines += 1
            per_family[family] += 1
            per_family_templates[family].add(result["cluster_id"])
            if result["change_type"] == "cluster_created":
                new_templates += 1
            if deciles and lines % deciles == 0:
                curve.append({
                    "lines": lines,
                    "templates": len(tm.drain.clusters),
                    "new_in_block": len(tm.drain.clusters) - decile_mark,
                    "cpu_seconds": round(mining_cpu, 3),
                })
                decile_mark = len(tm.drain.clusters)
            if limit and lines >= limit:
                break

    total_cpu = time.process_time() - started
    templates = len(tm.drain.clusters)
    return {
        "corpus": path,
        "family_filter": family_filter or "all",
        "lines": lines,
        "templates": templates,
        "sim_threshold": sim_threshold,
        "depth": depth,
        "max_children": max_children,
        "template_rate_per_million": round(1e6 * templates / lines, 1) if lines else None,
        "mining_core_seconds_per_million": round(1e6 * mining_cpu / lines, 2) if lines else None,
        "loop_core_seconds_per_million": (
            round(1e6 * total_cpu / lines, 2)
            if lines and not family_filter else None),
        "read_lines": read_lines,
        "per_family_lines": dict(per_family),
        "per_family_templates": {k: len(v) for k, v in per_family_templates.items()},
        "curve": curve,
        "top_templates": [
            {"count": c.size, "template": c.get_template()}
            for c in sorted(tm.drain.clusters, key=lambda c: c.size, reverse=True)[:25]
        ],
    }


def per_source(path, family_filter, sim_threshold, depth, max_children, limit):
    """One tree per source against one tree for everything.

    A per-source tree cannot confuse two programs that happen to share a
    prefix, but it also cannot share a template between them, and every worker
    would hold as many trees as it sees programs. The comparison is here so the
    sidecar's shape is a measured choice rather than a habit."""
    miners = {}
    lines = 0
    cpu = 0.0
    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            family, source, message = parts
            if family_filter and family != family_filter:
                continue
            key = "%s/%s" % (family, source)
            tm = miners.get(key)
            if tm is None:
                tm = miners[key] = miner(sim_threshold, depth, max_children)
            t0 = time.process_time()
            tm.add_log_message(message)
            cpu += time.process_time() - t0
            lines += 1
            if limit and lines >= limit:
                break
    total = sum(len(tm.drain.clusters) for tm in miners.values())
    return {
        "lines": lines,
        "trees": len(miners),
        "templates": total,
        "core_seconds_per_million": round(1e6 * cpu / lines, 2) if lines else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--family", default="")
    ap.add_argument("--sim-threshold", type=float, default=0.4)
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--max-children", type=int, default=100)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--block", type=int, default=100000)
    ap.add_argument("--json", default="")
    ap.add_argument("--per-source", action="store_true",
                    help="also mine one tree per source and compare")
    ap.add_argument("--split-cost", type=int, default=0,
                    help="price masking apart from the tree over this many lines")
    args = ap.parse_args()

    report = run(args.corpus, args.family, args.sim_threshold, args.depth,
                 args.max_children, args.limit, args.block)
    if args.split_cost:
        messages = []
        with open(args.corpus, "r", errors="replace") as fh:
            for raw in fh:
                parts = raw.rstrip("\n").split("\t", 2)
                if len(parts) != 3:
                    continue
                if args.family and parts[0] != args.family:
                    continue
                messages.append(parts[2])
                if len(messages) >= args.split_cost:
                    break
        report["cost_split"] = cost_split(messages, args.sim_threshold,
                                          args.depth, args.max_children)
    if args.per_source:
        report["per_source"] = per_source(args.corpus, args.family,
                                          args.sim_threshold, args.depth,
                                          args.max_children, args.limit)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("curve", "top_templates")}, indent=2))
    print("\nblocks of %d lines: new templates" % args.block, file=sys.stderr)
    for point in report["curve"]:
        print("  %9d  %6d total  %5d new" % (
            point["lines"], point["templates"], point["new_in_block"]), file=sys.stderr)


if __name__ == "__main__":
    main()
