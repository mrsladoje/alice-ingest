#!/usr/bin/env python3
"""Mine templates with PIPLUP instead of Drain3, and price the work the same way.

Stage H of docs/SOAK_PLAN.md compares the two parsers on one corpus in one
sitting. This file exists so the two arms are read off the same instrument:
every field it reports has the same name and the same definition as the one
drainbench.py reports, and mining is timed apart from reading in both.

PIPLUP as released is a batch parser. It reads a whole log file into a pandas
frame, keeps every line's identifier inside its cluster, and writes a structured
CSV at the end. None of that survives 45.6 million lines. The clustering and the
cluster update are genuinely online, so this file drives those two directly, one
message at a time, and keeps a hit count per cluster instead of a line list. The
algorithm is untouched; only the file handling around it is replaced.
"""
import argparse
import json
import os
import resource
import sys
import time
from collections import Counter, defaultdict


def load_piplup(path):
    root = os.path.join(path, "benchmark")
    if not os.path.isdir(os.path.join(root, "logparser", "PIPLUP")):
        raise SystemExit(
            "no PIPLUP checkout at %s — clone "
            "https://github.com/mooselab/PIPLUP-A-Configuration-Free-Statistic-Based-Log-Parser"
            % path)
    sys.path.insert(0, root)
    from logparser.PIPLUP.PIPLUP import LogParser, Logcluster
    from logparser.utils.preprocessing import preprocess, sequence
    return LogParser, Logcluster, preprocess, sequence


class Stream(object):
    """One message at a time through PIPLUP's own clustering and update steps.

    The body of add_log_message is the body of LogParser.parse's loop, with the
    line identifier dropped. A cluster's hit_time already counts its messages,
    so the logIDL the released code accumulates is dead weight here — at this
    corpus size it is several gigabytes of it."""

    def __init__(self, piplup_path, br_thresh, sim_thresh, hit_limit,
                 merge, preprocessing):
        LogParser, Logcluster, preprocess, sequence = load_piplup(piplup_path)
        self.Logcluster = Logcluster
        self.preprocess = preprocess
        self.parser = LogParser(
            log_format="<Content>", indir=".", outdir=".",
            hit_limit=hit_limit, merge=merge, preprocess=preprocessing,
            br_thresh=br_thresh, sim_thresh=sim_thresh)
        self.clusters = []
        self.lines = 0
        self.matched_types = []
        self.use_sequence = list(sequence)

    def tokenise(self, message):
        p = self.parser
        if not p.preprocess:
            return message.strip().split()
        if self.lines < 2000:
            masked, seq, matched = self.preprocess(message, estimation_stage=True)
            self.matched_types.extend(matched)
            self.use_sequence = seq
            return masked.strip().split()
        if self.lines == 2000:
            self.matched_types = set(self.matched_types)
            self.use_sequence = [i for i in self.use_sequence
                                 if i in self.matched_types]
        return self.preprocess(message, estimation_stage=False,
                               use_sequence=self.use_sequence).strip().split()

    def add_log_message(self, message):
        p = self.parser
        tokens = self.tokenise(message)
        self.lines += 1

        match, first_constant, new_template = p.treeSearch(tokens)

        if match is None:
            cluster = self.Logcluster(logTemplate=tokens, logIDL=[],
                                      br_thresh=p.br_thresh)
            self.clusters.append(cluster)
            constant = p.addSeqToPrefixTree(cluster)
            if p.merge:
                p.mergeTemplates(cluster, constant)
            return cluster, True

        match.hit_time += 1
        match.logTemplate = new_template
        constant = first_constant
        old_templates = match.templateList.copy()
        updated = match.updateSequence(tokens)

        if match.hit_time == p.hit_limit and first_constant == "None":
            for token in new_template:
                if any(ch.isalnum() for ch in token):
                    constant = token
                    break
            if constant in p.storage_tree:
                if len(tokens) not in p.storage_tree[constant]:
                    p.storage_tree[constant][len(tokens)] = []
            else:
                p.storage_tree[constant] = {len(tokens): []}
                p.shallow_tree[constant] = []
            if constant != "None":
                remaining = p.shallow_tree["None"].copy()
                for other in p.shallow_tree["None"]:
                    if constant in other.logTemplate:
                        p.shallow_tree[constant].append(other)
                        size = len(other.logTemplate)
                        if size in p.storage_tree[constant]:
                            p.storage_tree[constant][size].append(other)
                        else:
                            p.storage_tree[constant][size] = [other]
                        p.storage_tree["None"][size].remove(other)
                        remaining.remove(other)
                p.shallow_tree["None"] = remaining

        if updated and p.merge:
            p.mergeTemplates(match, constant, old_templates)
        return match, False

    def templates(self):
        seen = set()
        for cluster in self.clusters:
            seen.update(cluster.templateList)
        return seen


def run(path, piplup_path, family_filter, limit, block, br_thresh, sim_thresh,
        hit_limit, merge, preprocessing):
    stream = Stream(piplup_path, br_thresh, sim_thresh, hit_limit,
                    merge, preprocessing)
    lines = 0
    read_lines = 0
    per_family = Counter()
    per_family_clusters = defaultdict(set)
    cluster_sources = defaultdict(Counter)
    curve = []
    mining_cpu = 0.0
    started = time.process_time()
    block_mark = 0

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
            cluster, _ = stream.add_log_message(message)
            mining_cpu += time.process_time() - t0
            lines += 1
            per_family[family] += 1
            per_family_clusters[family].add(id(cluster))
            cluster_sources[id(cluster)]["%s/%s" % (family, source)] += 1
            if block and lines % block == 0:
                total = len(stream.templates())
                curve.append({
                    "lines": lines,
                    "templates": total,
                    "new_in_block": total - block_mark,
                    "cpu_seconds": round(mining_cpu, 3),
                })
                block_mark = total
            if limit and lines >= limit:
                break

    total_cpu = time.process_time() - started
    templates = stream.templates()
    multi = sum(1 for c in stream.clusters if len(c.templateList) > 1)
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak_rss *= 1024

    rows = []
    for cluster in stream.clusters:
        sources = cluster_sources[id(cluster)]
        rows.append({
            "count": cluster.hit_time,
            "template": cluster.templateList[0],
            "extra_templates": cluster.templateList[1:],
            "source": (sources.most_common(1) or [("?", 0)])[0][0],
            "sources": len(sources),
        })
    rows.sort(key=lambda r: r["count"], reverse=True)

    return {
        "parser": "PIPLUP",
        "corpus": path,
        "family_filter": family_filter or "all",
        "lines": lines,
        "clusters": len(stream.clusters),
        "templates": len(templates),
        "clusters_with_two_templates": multi,
        "br_thresh": br_thresh,
        "sim_thresh": sim_thresh,
        "hit_limit": hit_limit,
        "merge": merge,
        "preprocess": preprocessing,
        "template_rate_per_million": (round(1e6 * len(templates) / lines, 1)
                                      if lines else None),
        "mining_core_seconds_per_million": (round(1e6 * mining_cpu / lines, 2)
                                            if lines else None),
        "loop_core_seconds_per_million": (round(1e6 * total_cpu / lines, 2)
                                          if lines and not family_filter else None),
        "peak_rss_bytes": peak_rss,
        "read_lines": read_lines,
        "per_family_lines": dict(per_family),
        "per_family_clusters": {k: len(v) for k, v in per_family_clusters.items()},
        "curve": curve,
        "all_templates": rows,
        "top_templates": [{"count": r["count"], "template": r["template"]}
                          for r in rows[:25]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--piplup", default=os.environ.get("PIPLUP_HOME", ""),
                    help="path to a PIPLUP checkout")
    ap.add_argument("--family", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--block", type=int, default=100000)
    ap.add_argument("--br-thresh", type=int, default=2)
    ap.add_argument("--sim-thresh", default="default")
    ap.add_argument("--hit-limit", type=int, default=385)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--no-preprocess", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--dump-templates", default="")
    args = ap.parse_args()

    report = run(args.corpus, args.piplup, args.family, args.limit, args.block,
                 args.br_thresh, args.sim_thresh, args.hit_limit,
                 not args.no_merge, not args.no_preprocess)

    if args.dump_templates:
        with open(args.dump_templates, "w") as fh:
            for row in report["all_templates"]:
                fh.write("%d\t%s\t%d\t%s\n" % (
                    row["count"], row["source"], row["sources"], row["template"]))
    report.pop("all_templates", None)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("curve", "top_templates")}, indent=2))
    print("\nblocks of %d lines: new templates" % args.block, file=sys.stderr)
    for point in report["curve"]:
        print("  %9d  %6d total  %5d new" % (
            point["lines"], point["templates"], point["new_in_block"]),
            file=sys.stderr)


if __name__ == "__main__":
    main()
