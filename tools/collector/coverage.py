#!/usr/bin/env python3
"""Score the shipped parser cascade against a real log corpus.

The cascade is not restated here. It is read out of the configuration the
production renderer produces, so a parser added to `collector.yaml.j2` is scored
without touching this file and a parser removed from it stops being scored.

The engine is Python's `re`, not Onigmo. The patterns use no construct where the
two differ, and `tools/collector/replaycheck.py` runs the same cascade through
real Fluent Bit on fixtures covering every shape counted here. Treat this as the
coverage instrument and Fluent Bit as the authority.

Corpus format is one record a line: family, source, message, tab separated.
"""
import argparse
import collections
import os
import re
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MKCONFIG = os.path.join(REPO, "tools", "soak", "mkconfig.py")

NAMED = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")


def to_python(pattern):
    """Onigmo named groups are (?<name>...); Python wants (?P<name>...)."""
    return NAMED.sub(r"(?P<\1>", pattern)


def multiline_rules(tag, work):
    """The tail input's multiline rules for one tag, as (start, cont) regexes.

    Fluent Bit folds an indented continuation into the record above it before
    any parser runs. A corpus that stores one line per record has not had that
    done, so scoring it line by line counts every continuation of the O2PDPSuite
    module banner as a line with no severity.
    """
    config_path = os.path.join(work, "collector.yaml")
    with open(config_path) as fh:
        config = yaml.safe_load(fh)
    name = None
    for item in config["pipeline"]["inputs"]:
        if item.get("tag") == tag:
            name = item.get("multiline.parser")
    if not name:
        return None
    for item in config.get("multiline_parsers", []):
        if item.get("name") != name:
            continue
        states = {}
        for rule in item["rules"]:
            states[rule["state"]] = re.compile(to_python(rule["regex"].strip("/")))
        return states.get("start_state"), states.get("cont")
    return None


def cascade(tag, work):
    """The ordered body parsers the rendered configuration runs on one tag."""
    config_path = os.path.join(work, "collector.yaml")
    parsers_path = os.path.join(work, "parsers.yaml")
    # Every optional source turned on, so the cascade for any tag can be read
    # out of one render. The flags only ADD filters, and the tag selects which
    # of them this call cares about.
    subprocess.run([sys.executable, MKCONFIG, "--out", config_path,
                    "--parsers-out", parsers_path,
                    "--journald", "on", "--odc", "on"],
                   check=True, capture_output=True)
    with open(config_path) as fh:
        config = yaml.safe_load(fh)
    with open(parsers_path) as fh:
        library = {p["name"]: p for p in yaml.safe_load(fh)["parsers"]}
    names = []
    for item in config["pipeline"]["filters"]:
        if item.get("name") != "parser" or item.get("match") != tag:
            continue
        if item.get("key_name") != "log":
            continue
        names.append(item["parser"])
    return [(n, re.compile(to_python(library[n]["regex"]))) for n in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+")
    ap.add_argument("--family", default="stdout")
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--unmatched", type=int, default=25)
    ap.add_argument("--programs", type=int, default=15)
    # Stage S0 asks for a frozen catch-all threshold. This is it: a run that
    # leaves more than one line in a hundred without a severity fails, because
    # at that point a shape the parsers do not know has become common enough to
    # be worth a parser. Measured on 20.9 million process-tree lines the figure
    # is 0.17 %, so the threshold is roughly six times the observed value —
    # loose enough not to trip on a new program, tight enough to notice a format
    # change.
    ap.add_argument("--max-unclassified", type=float, default=1.0,
                    help="fail above this percentage of lines with no severity")
    ap.add_argument("--multiline", action="store_true",
                    help="fold continuations first, the way the tail input "
                         "does before any parser sees the record")
    args = ap.parse_args()
    tag = args.tag or args.family

    work = os.path.join(os.path.expanduser("~"), ".cache", "alice-coverage")
    os.makedirs(work, exist_ok=True)
    chain = cascade(tag, work)
    print("cascade for tag %r: %s" % (tag, ", ".join(n for n, _ in chain)))
    fold = multiline_rules(tag, work) if args.multiline else None
    if args.multiline:
        print("folding continuations with the input's own multiline rule: %s"
              % ("yes" if fold else "no rule on this tag"))

    hits = collections.Counter()
    severity = collections.Counter()
    per_program = collections.defaultdict(lambda: [0, 0])
    unmatched = collections.Counter()
    unclassified = collections.Counter()
    digits = re.compile(r"\d+")
    pending = None
    total = 0
    for path in args.corpus:
        with open(path, errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t", 2)
                if len(parts) != 3 or parts[0] != args.family:
                    continue
                _, source, message = parts
                message = message.replace("\\t", "\t")
                if fold is not None:
                    start, cont = fold
                    if pending is not None and cont.match(message) \
                            and not start.match(message):
                        pending[1] += "\n" + message
                        continue
                    held, pending = pending, [source, message]
                    if held is None:
                        continue
                    source, message = held
                total += 1
                per_program[source][0] += 1
                for name, rx in chain:
                    m = rx.match(message)
                    if m is None:
                        continue
                    hits[name] += 1
                    per_program[source][1] += 1
                    sev = m.groupdict().get("severity")
                    severity[sev if sev else "<none>"] += 1
                    if not sev:
                        unclassified[digits.sub("#", message[:90])] += 1
                    break
                else:
                    unmatched[digits.sub("#", message[:90])] += 1
                    unclassified[digits.sub("#", message[:90])] += 1
                if args.limit and total >= args.limit:
                    break
        if args.limit and total >= args.limit:
            break

    matched = sum(hits.values())
    classified = total - sum(unclassified.values())
    print("\nlines %d   matched %d   %.2f %%"
          % (total, matched, 100.0 * matched / max(total, 1)))
    # The gate metric. The last parser in the cascade has every group optional,
    # so it matches anything and a raw match rate is always 100 %. What decides
    # whether a line can be routed, charted or alerted on is whether a severity
    # came out of it.
    print("severity recovered on %d of %d lines, %.2f %%"
          % (classified, total, 100.0 * classified / max(total, 1)))
    print("\nper parser")
    for name, _ in chain:
        print("  %-14s %10d  %6.2f %%"
              % (name, hits[name], 100.0 * hits[name] / max(total, 1)))

    print("\nseverity, which is what routing keys on")
    for sev, n in severity.most_common():
        print("  %-10s %10d  %6.2f %%" % (sev, n, 100.0 * n / max(total, 1)))

    print("\nworst %d sources" % args.programs)
    if unclassified:
        print("\ntop %d shapes with no severity (%d lines, %.2f %%)"
              % (args.unmatched, sum(unclassified.values()),
                 100.0 * sum(unclassified.values()) / max(total, 1)))
        for shape, n in unclassified.most_common(args.unmatched):
            print("  %8d  %s" % (n, shape))

    ranked = sorted(per_program.items(),
                    key=lambda kv: (kv[1][1] / max(kv[1][0], 1), -kv[1][0]))
    for source, (seen, ok) in ranked[:args.programs]:
        print("  %-42s %8d  %6.2f %%"
              % (source, seen, 100.0 * ok / max(seen, 1)))

    if unmatched:
        print("\ntop %d unmatched shapes (%d lines, %.2f %%)"
              % (args.unmatched, total - matched,
                 100.0 * (total - matched) / max(total, 1)))
        for shape, n in unmatched.most_common(args.unmatched):
            print("  %8d  %s" % (n, shape))

    share = 100.0 * sum(unclassified.values()) / max(total, 1)
    if share > args.max_unclassified:
        print("\nFAIL %.2f %% of lines carry no severity, above the frozen "
              "threshold of %.2f %%. A shape the parsers do not know has become "
              "common; the shapes are listed above." % (share, args.max_unclassified))
        return 1
    print("\nunclassified %.2f %% is within the frozen threshold of %.2f %%"
          % (share, args.max_unclassified))
    return 0


if __name__ == "__main__":
    sys.exit(main())
