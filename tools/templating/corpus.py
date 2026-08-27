#!/usr/bin/env python3
"""Pull a bounded sample of real ALICE log text out of the CERN S3 archive and
write it as a flat corpus for template mining.

One record per line, tab separated: family, source, message. Newlines and tabs
inside a message are escaped, so a corpus line is always one record — Drain
counts lines, and a message that breaks across two of them would be counted as
two templates that never repeat.
"""
import argparse
import gzip
import io
import os
import re
import sys
import tarfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "images", "replay"))
from replay import IL_COLUMNS, parse_insert_rows

BUCKET = os.environ.get("S3_BUCKET", "epn-backup-logs")
ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.cern.ch")
REGION = os.environ.get("S3_REGION", "us-east-1")

HOST_RE = re.compile(r"_(epn[0-9]+)\.tar\.gz$")
DDS_MEMBER_RE = re.compile(r"/dds_\d{4}-\d{2}-\d{2}\.\d+\.log$")
STDOUT_MEMBER_RE = re.compile(r"_(?:out|err)\.log$")
PROGRAM_RE = re.compile(r"/([A-Za-z0-9._-]+?)(?:_t\d+)?_reco\d+_\d{4}-\d{2}-\d{2}")


def client():
    import boto3
    return boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION)


def objects(s3, prefix, min_size=1000):
    pages = s3.get_paginator("list_objects_v2")
    for page in pages.paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get("Contents", []):
            if o["Size"] > min_size:
                yield o["Key"], o["Size"]


def clean(text):
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


def write(out, family, source, message):
    if not message:
        return 0
    out.write("%s\t%s\t%s\n" % (family, clean(source), clean(message)))
    return 1


def take_infologger(s3, out, max_objects, max_lines, stride):
    keys = [k for k, _ in objects(s3, "infologger-2026/")]
    keys = keys[::stride][:max_objects] if stride > 1 else keys[:max_objects]
    written = 0
    for n, key in enumerate(keys, 1):
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"]
        print("infologger %d/%d %s" % (n, len(keys), key), file=sys.stderr, flush=True)
        with gzip.GzipFile(fileobj=body) as gz:
            for raw in gz:
                line = raw.decode("utf-8", "replace")
                if not line.startswith("INSERT INTO"):
                    continue
                for vals in parse_insert_rows(line):
                    if len(vals) != len(IL_COLUMNS):
                        continue
                    rec = dict(zip(IL_COLUMNS, vals))
                    source = "%s/%s" % (rec.get("system") or "?", rec.get("facility") or "?")
                    written += write(out, "infologger", source, rec.get("message") or "")
                    if max_lines and written >= max_lines:
                        return written
    return written


def take_tarballs(s3, out, families, max_objects, max_lines, run_tag):
    keys = [k for k, _ in objects(s3, "dds/%s_" % run_tag)][:max_objects]
    written = {f: 0 for f in families}
    for n, key in enumerate(keys, 1):
        m = HOST_RE.search(key)
        host = m.group(1) if m else "unknown"
        print("tar %d/%d %s" % (n, len(keys), key), file=sys.stderr, flush=True)
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"]
        with tarfile.open(fileobj=body, mode="r|gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = "/" + member.name
                if "dds" in families and DDS_MEMBER_RE.search(name):
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    for raw in src:
                        written["dds"] += write(out, "dds", host,
                                                raw.decode("utf-8", "replace").rstrip("\n"))
                        if max_lines and written["dds"] >= max_lines:
                            break
                elif "stdout" in families and STDOUT_MEMBER_RE.search(name):
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    pm = PROGRAM_RE.search(name)
                    program = pm.group(1) if pm else "unknown"
                    kept = 0
                    for raw in src:
                        written["stdout"] += write(out, "stdout", program,
                                                   raw.decode("utf-8", "replace").rstrip("\n"))
                        kept += 1
                        if kept >= 20000:
                            break
                        if max_lines and written["stdout"] >= max_lines:
                            break
                if all(max_lines and written[f] >= max_lines for f in families):
                    break
        if all(max_lines and written[f] >= max_lines for f in families):
            break
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--families", default="infologger,dds,stdout")
    ap.add_argument("--il-objects", type=int, default=40)
    ap.add_argument("--il-stride", type=int, default=4)
    ap.add_argument("--tar-objects", type=int, default=4)
    ap.add_argument("--max-lines", type=int, default=0)
    ap.add_argument("--run-tag", default=os.environ.get("RUN_TAG", "33NXirFsSfT_38917"))
    args = ap.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    s3 = client()
    total = {}
    with open(args.out, "w", buffering=1 << 20) as out:
        if "infologger" in families:
            total["infologger"] = take_infologger(
                s3, out, args.il_objects, args.max_lines, args.il_stride)
        tar_families = [f for f in families if f in ("dds", "stdout")]
        if tar_families:
            total.update(take_tarballs(
                s3, out, tar_families, args.tar_objects, args.max_lines, args.run_tag))
    for family, n in sorted(total.items()):
        print("%s\t%d" % (family, n))


if __name__ == "__main__":
    main()
