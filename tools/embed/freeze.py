#!/usr/bin/env python3
"""Freeze the retrieval corpus: canonical template groups and their instances.

`docs/SEMANTIC_PLAN.md` asks for a corpus whose retrieval unit is decided before
any model is compared. The unit is the canonical template group: one record for
one normalised event meaning, carrying the source instances that produced it. A
model that returned the same event once for each of its five programs would
otherwise take five of the ten ranks a metric can see.

Two identifiers come out of this and both have to survive a model change, a
representation change and a re-mine of the same corpus, so neither is a position
in a list. The canonical identifier is a hash of the normalised template text;
the source-instance identifier is a hash of family, program and template.

Mining is the shipped recipe on the shipped path: the same miner, the same
per-family knobs and the same patched Drain that `drainbench.recipe_run` uses,
so a template here is the template a node would publish. Severity and program
come from the rendered production parser cascade rather than from a rule
restated here.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "templating"))
sys.path.insert(0, os.path.join(REPO, "tools", "collector"))

import drainbench  # noqa: E402
import manifest as manifest_tool  # noqa: E402
import masking  # noqa: E402
from coverage import cascade  # noqa: E402
from recipesweep import contentless  # noqa: E402

FAMILY_TAG = {"dpl": "stdout", "datadist": "stdout", "dds": "dds",
              "odc": "odc", "ildaemon": "ildaemon",
              "journald": None, "infologger": None}

SEVERITY_CLASS = {
    "DEBUG": "debug", "TRACE": "debug", "D": "debug", "T": "debug",
    "dbg": "debug", "Debug": "debug", "7": "debug",
    "INFO": "info", "Info": "info", "I": "info", "inf": "info",
    "STATE": "info", "Sys": "info", "5": "info", "6": "info",
    "WARN": "warning", "WARNING": "warning", "Warning": "warning",
    "W": "warning", "wrn": "warning", "4": "warning",
    "ERROR": "error", "Error": "error", "E": "error", "err": "error",
    "ALARM": "error", "Break": "error", "3": "error",
    "FATAL": "fatal", "Fatal": "fatal", "F": "fatal", "fat": "fatal",
    "0": "fatal", "1": "fatal", "2": "fatal",
    "cout": "console",
}

PLACEHOLDER = re.compile(r"<[A-Z_]+>|<\*>")
SPACE = re.compile(r"\s+")
EDGE = re.compile(r"^[\s.,;:!-]+|[\s.,;:!-]+$")
EXAMPLES_PER_GROUP = 3
EXAMPLES_PER_INSTANCE = 2
EXAMPLE_CHARS = 300
NO_BODY_PARSER = {"infologger": "none, a structured input",
                  "journald": "none, journal fields"}


def normalize(template):
    text = PLACEHOLDER.sub(" <*> ", template).lower()
    text = SPACE.sub(" ", text).strip()
    return EDGE.sub("", text) or text


def digest(*parts):
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()[:16]


def severity_class(value):
    if not value:
        return "absent"
    return SEVERITY_CLASS.get(value, "unknown")


def program_of(family, source, groups):
    named = groups.get("program")
    if named:
        return named
    if family == "infologger":
        facility, _, program = source.partition("/")
        return program or facility or "parse_failed"
    if family == "journald":
        _, _, identity = source.partition("/")
        return identity or "parse_failed"
    if family == "ildaemon":
        return "o2-infologger-daemon"
    if not source or source == "unknown":
        return "parse_failed"
    return source


def facility_of(family, source):
    if family == "infologger":
        facility, _, _ = source.partition("/")
        return facility or "absent"
    return "absent"


def read(path):
    with open(path, errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t", 3)
            if len(parts) != 4:
                continue
            yield parts


def new_instance(family, source):
    return {"lines": 0,
            "severities": collections.Counter(),
            "parsers": collections.Counter(),
            "facility": facility_of(family, source),
            "examples": [],
            "first": None,
            "last": None}


def mine_family(family, paths, work, limit):
    tag = FAMILY_TAG.get(family)
    chain = cascade(tag, work) if tag else []
    fallback = NO_BODY_PARSER.get(family, "none")
    tm = drainbench.recipe_miner(family)
    per_cluster = collections.defaultdict(lambda: {"lines": 0, "instances": {}})
    stats = {"lines": 0, "empty_token_lines": 0, "unparsed_lines": 0,
             "severity_absent_lines": 0, "first": None, "last": None}
    for path in paths:
        for _, source, column_severity, message in read(path):
            if limit and stats["lines"] >= limit:
                break
            body = message.replace("\\t", "\t")
            groups = {}
            severity = column_severity
            parser = fallback
            for name, rx in chain:
                found = rx.match(body)
                if found is None:
                    continue
                parser = name
                groups = {k: v for k, v in found.groupdict().items() if v}
                severity = severity or groups.get("severity") or ""
                break
            else:
                if chain:
                    stats["unparsed_lines"] += 1
                    parser = "no match"
            if not severity:
                stats["severity_absent_lines"] += 1
            tokens = drainbench.recipe_tokens(family, message)
            if not tokens:
                stats["empty_token_lines"] += 1
                continue
            cluster = drainbench.mine(tm, tokens)
            stats["lines"] += 1
            row = per_cluster[cluster.cluster_id]
            row["lines"] += 1
            program = program_of(family, source, groups)
            instance = row["instances"].get(program)
            if instance is None:
                instance = row["instances"][program] = new_instance(family, source)
            instance["lines"] += 1
            instance["severities"][severity_class(severity)] += 1
            instance["parsers"][parser] += 1
            if len(instance["examples"]) < EXAMPLES_PER_INSTANCE:
                instance["examples"].append(message[:EXAMPLE_CHARS])
            for pattern in manifest_tool.TIMESTAMPS:
                stamp = pattern.search(message)
                if not stamp:
                    continue
                value = stamp.group(1)
                for holder in (instance, stats):
                    if holder["first"] is None or value < holder["first"]:
                        holder["first"] = value
                    if holder["last"] is None or value > holder["last"]:
                        holder["last"] = value
                break
    templates = {c.cluster_id: c.get_template() for c in tm.drain.clusters}
    return per_cluster, templates, stats, [name for name, _ in chain]


def instances(family, per_cluster, templates):
    out = []
    for cluster_id, row in per_cluster.items():
        template = templates.get(cluster_id)
        if template is None:
            continue
        for program, instance in row["instances"].items():
            out.append({
                "instance_id": digest(family, program, template),
                "family": family,
                "program": program,
                "facility": instance["facility"],
                "parser": instance["parsers"].most_common(1)[0][0],
                "template": template,
                "lines": instance["lines"],
                "share_of_template": round(instance["lines"] / row["lines"], 4),
                "severities": dict(instance["severities"].most_common()),
                "message_time_first_seen": instance["first"],
                "message_time_last_seen": instance["last"],
                "examples": instance["examples"],
            })
    return out


def collapse(all_instances):
    groups = collections.OrderedDict()
    for item in sorted(all_instances, key=lambda i: (-i["lines"], i["instance_id"])):
        key = normalize(item["template"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "canonical_id": digest(key),
                "template": item["template"],
                "normalized": key,
                "contentless": contentless(item["template"]),
                "lines": 0,
                "families": collections.Counter(),
                "programs": collections.Counter(),
                "severities": collections.Counter(),
                "examples": [],
                "instances": [],
            }
        group["lines"] += item["lines"]
        group["families"][item["family"]] += item["lines"]
        group["programs"][item["program"]] += item["lines"]
        for name, count in item["severities"].items():
            group["severities"][name] += count
        for example in item["examples"]:
            if len(group["examples"]) < EXAMPLES_PER_GROUP:
                group["examples"].append(example)
        group["instances"].append(item)
    records = []
    for group in groups.values():
        group["families"] = dict(group["families"].most_common())
        group["programs"] = dict(group["programs"].most_common(20))
        group["distinct_programs"] = len(group["instances"])
        group["severities"] = dict(group["severities"].most_common())
        group["severity_class"] = next(iter(group["severities"]), "absent")
        records.append(group)
    records.sort(key=lambda g: (-g["lines"], g["canonical_id"]))
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+",
                    help="four column corpora from tools/embed/mkcorpus.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--id", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--limit", type=int, default=0,
                    help="lines per family, for a smoke run")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(os.path.expanduser("~"), ".cache", "alice-coverage")
    os.makedirs(work, exist_ok=True)

    by_family = collections.OrderedDict()
    for path in args.corpus:
        with open(path, errors="replace") as fh:
            head = fh.readline().split("\t", 1)
        if not head or head[0] not in FAMILY_TAG:
            print("skipping %s: no known family on its first line" % path,
                  file=sys.stderr)
            continue
        by_family.setdefault(head[0], []).append(path)

    drainbench.install_merged_create_template()
    all_instances = []
    per_family = {}
    try:
        for family, paths in by_family.items():
            per_cluster, templates, stats, chain = mine_family(
                family, paths, work, args.limit)
            found = instances(family, per_cluster, templates)
            all_instances.extend(found)
            per_family[family] = {
                "corpora": [os.path.relpath(p, REPO) if p.startswith(REPO) else p
                            for p in paths],
                "lines": stats["lines"],
                "templates": len(templates),
                "source_instances": len(found),
                "message_time_first_seen": stats["first"],
                "message_time_last_seen": stats["last"],
                "distinct_programs": len({i["program"] for i in found}),
                "parse_failed_program_lines": sum(
                    i["lines"] for i in found if i["program"] == "parse_failed"),
                "contentless_templates": sum(
                    1 for t in templates.values() if contentless(t)),
                "lines_no_parser_matched": stats["unparsed_lines"],
                "lines_without_severity": stats["severity_absent_lines"],
                "lines_that_masked_to_nothing": stats["empty_token_lines"],
                "parser_cascade": chain,
                "recipe": {
                    "depth": drainbench.RECIPE_DEPTH,
                    "max_children": drainbench.RECIPE_MAX_CHILDREN,
                    "similarity": drainbench.RECIPE_SIM.get(family),
                    "padded_separators": drainbench.RECIPE_PAD.get(family) or None,
                    "parametrise_numeric_tokens": drainbench.RECIPE_NUMERIC.get(family),
                    "envelope_strip": (
                        drainbench.RECIPE_STRIP.get(family).pattern
                        if drainbench.RECIPE_STRIP.get(family) is not None else None),
                },
            }
            print("%-11s %9d lines  %5d templates  %5d instances"
                  % (family, stats["lines"], len(templates), len(found)),
                  flush=True)
    finally:
        drainbench.restore_plain_drain()

    records = collapse(all_instances)
    corpus_path = os.path.join(args.out, "corpus.jsonl")
    with open(corpus_path, "w") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=False) + "\n")

    duplicates = {
        "created": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
        "retrieval_unit": "canonical template group",
        "normalisation": [
            "every mask placeholder and every Drain wildcard becomes <*>",
            "lower case",
            "runs of whitespace become one space",
            "leading and trailing punctuation and whitespace are removed",
        ],
        "reviewed_aliases": [],
        "groups": {r["canonical_id"]: [i["instance_id"] for i in r["instances"]]
                   for r in records},
    }
    with open(os.path.join(args.out, "duplicates.json"), "w") as fh:
        fh.write(json.dumps(duplicates, indent=2) + "\n")

    lines_per_file = collections.Counter()
    for family, paths in by_family.items():
        for path in paths:
            with open(path, errors="replace") as fh:
                lines_per_file[path] = sum(1 for _ in fh)
    sources = []
    for path in args.corpus:
        stat = os.stat(path)
        sources.append({
            "path": os.path.relpath(path, REPO) if path.startswith(REPO) else path,
            "lines": lines_per_file[path],
            "bytes": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(
                stat.st_mtime, datetime.timezone.utc)
                .replace(microsecond=0).isoformat(),
            "sha256": manifest_tool.sha256(path),
        })
    identifier = args.id or hashlib.sha256(
        "".join(s["sha256"] for s in sources).encode()).hexdigest()[:16]
    collapsed = sum(1 for r in records if r["distinct_programs"] > 1)
    manifest = {
        "corpus_id": identifier,
        "created": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
        "note": args.note,
        "retrieval_unit": "canonical template group",
        "git_revision": manifest_tool.git("rev-parse", "HEAD"),
        "git_dirty": bool(manifest_tool.git("status", "--porcelain")),
        "code_revisions": [r for r in (
            manifest_tool.file_revision("tools/embed/freeze.py"),
            manifest_tool.file_revision("tools/embed/mkcorpus.py"),
            manifest_tool.file_revision("tools/templating/drainbench.py"),
            manifest_tool.file_revision("tools/templating/masking.py"),
            manifest_tool.file_revision("tools/templating/corpus.py"),
            manifest_tool.file_revision("tools/templating/refamily.py"),
            manifest_tool.file_revision("deploy/roles/collector/templates/parsers.yaml.j2"),
        ) if r],
        "sources": sources,
        "families": per_family,
        "totals": {
            "lines": sum(f["lines"] for f in per_family.values()),
            "templates": sum(f["templates"] for f in per_family.values()),
            "source_instances": sum(f["source_instances"] for f in per_family.values()),
            "canonical_groups": len(records),
            "groups_with_more_than_one_instance": collapsed,
            "contentless_groups": sum(1 for r in records if r["contentless"]),
        },
        "severity_classes": SEVERITY_CLASS,
        "masking_rules": [{"mask": rule["mask_with"],
                           "pattern": rule["regex_pattern"]}
                          for rule in masking.REFERENCE],
        "truncation": "corpus lines are escaped, so one line is one record; "
                      "examples are cut at %d characters" % EXAMPLE_CHARS,
        "message_time_note": "the first and last timestamp found INSIDE the "
                             "message text; a family whose clock lives outside "
                             "the message reports only what its text carries",
        "clock_domains": {
            "infologger": "archive event time, from the mysqldump row",
            "dds": "archive event time, in the line",
            "dpl": "archive date from the file name, time of day from the line",
            "datadist": "archive event time, in the line",
            "odc": "live node time, in the line",
            "ildaemon": "live node time, in the line",
            "journald": "live node time, in the journal record",
        },
    }
    manifest_path = os.path.join(args.out, "manifest.json")
    manifest["corpus_sha256"] = manifest_tool.sha256(corpus_path)
    with open(manifest_path, "w") as fh:
        fh.write(json.dumps(manifest, indent=2) + "\n")

    print("\ncorpus %s: %d canonical groups from %d source instances, %d lines"
          % (identifier, len(records),
             manifest["totals"]["source_instances"],
             manifest["totals"]["lines"]))
    print(corpus_path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
