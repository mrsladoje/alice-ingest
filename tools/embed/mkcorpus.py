#!/usr/bin/env python3
"""Assemble one corpus file per log family, for the frozen retrieval corpus.

`tools/templating/corpus.py` pulls the archive and `refamily.py` splits the
process tree into its two formats; both write family, source, message. Two of
the seven families are not in the archive at all — the InfoLogger daemon log and
the journal are written on a live node and captured by
`tools/epnsurvey/mkbundle.sh` — and one of those keeps its severity outside the
message, in the journal's own PRIORITY field.

So this writes four columns, family, source, severity, message. The severity
column is empty for every format that carries its severity in the line, where
the parser cascade recovers it, and carries the journal's PRIORITY digit
unchanged, which is what the collector copies into `severity` in production.

Archive messages are already escaped by `corpus.py` and pass through untouched.
Captured text is escaped here, so a corpus line is always one record.
"""
import argparse
import glob
import gzip
import json
import os
import re
import sys

ARCHIVE_FAMILIES = ("dpl", "datadist", "dds", "infologger")
DEFAULT_CAPS = {}
NODE = re.compile(r"-([A-Za-z0-9]+)\.(?:log|json)\.gz$")


def clean(text):
    return (text.replace("\\", "\\\\").replace("\t", "\\t")
                .replace("\n", "\\n").replace("\r", ""))


def node_of(path):
    found = NODE.search(os.path.basename(path))
    return found.group(1) if found else "unknown"


def row(handle, family, source, severity, message):
    handle.write("%s\t%s\t%s\t%s\n" % (family, source, severity, message))


def archive(path, out_dir, caps):
    handles = {f: open(os.path.join(out_dir, "fam-%s.tsv" % f), "w",
                       buffering=1 << 20) for f in ARCHIVE_FAMILIES}
    counts = dict.fromkeys(ARCHIVE_FAMILIES, 0)
    done = set()
    with open(path, errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            family, source, message = parts
            if family not in handles or family in done:
                continue
            row(handles[family], family, source, "", message)
            counts[family] += 1
            cap = caps.get(family, 0)
            if cap and counts[family] >= cap:
                done.add(family)
                if len(done) == len(handles):
                    break
    for handle in handles.values():
        handle.close()
    return counts


def ildaemon(census, out_dir):
    written = 0
    with open(os.path.join(out_dir, "fam-ildaemon.tsv"), "w",
              buffering=1 << 20) as out:
        for path in sorted(glob.glob(os.path.join(census, "ildaemon-*.log.gz"))):
            node = node_of(path)
            with gzip.open(path, "rt", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    row(out, "ildaemon", node, "", clean(line))
                    written += 1
    return {"ildaemon": written}


def journald(census, out_dir):
    written = skipped = 0
    with open(os.path.join(out_dir, "fam-journald.tsv"), "w",
              buffering=1 << 20) as out:
        for path in sorted(glob.glob(os.path.join(census, "journal-*.json.gz"))):
            node = node_of(path)
            with gzip.open(path, "rt", errors="replace") as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        skipped += 1
                        continue
                    message = record.get("MESSAGE")
                    if not isinstance(message, str) or not message:
                        skipped += 1
                        continue
                    identity = (record.get("SYSLOG_IDENTIFIER")
                                or record.get("_COMM")
                                or record.get("COMM") or "unknown")
                    priority = record.get("PRIORITY", "")
                    row(out, "journald", "%s/%s" % (node, identity),
                        str(priority), clean(message))
                    written += 1
    return {"journald": written, "journald_skipped": skipped}


def odc(census, out_dir):
    written = 0
    with open(os.path.join(out_dir, "fam-odc.tsv"), "w", buffering=1 << 20) as out:
        for path in sorted(glob.glob(os.path.join(census, "odc-*.log.gz"))):
            node = node_of(path)
            with gzip.open(path, "rt", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    row(out, "odc", node, "", clean(line))
                    written += 1
    return {"odc": written}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default=os.path.expanduser(
        "~/.cache/corpus-formats.tsv"),
        help="the refamilied archive corpus: family, source, message")
    ap.add_argument("--census", default=os.path.expanduser(
        "~/.cache/epncensus/corpora"),
        help="the farm captures the archive has no equivalent of")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", action="append", default=[],
                    help="FAMILY=N, a line ceiling for one archive family. "
                         "There is no ceiling by default: a cap costs "
                         "templates, and the corpus is the thing being frozen")
    ap.add_argument("--only", default="",
                    help="comma separated: archive, ildaemon, journald, odc")
    args = ap.parse_args()

    caps = dict(DEFAULT_CAPS)
    for item in args.cap:
        name, _, value = item.partition("=")
        caps[name] = int(value)

    os.makedirs(args.out, exist_ok=True)
    wanted = ([s.strip() for s in args.only.split(",") if s.strip()]
              or ["archive", "ildaemon", "journald", "odc"])
    counts = {}
    for name in wanted:
        if name == "archive":
            counts.update(archive(args.archive, args.out, caps))
        elif name == "ildaemon":
            counts.update(ildaemon(args.census, args.out))
        elif name == "journald":
            counts.update(journald(args.census, args.out))
        elif name == "odc":
            counts.update(odc(args.census, args.out))
        else:
            print("unknown part %r" % name, file=sys.stderr)
            return 2
        print(name, json.dumps(counts, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
