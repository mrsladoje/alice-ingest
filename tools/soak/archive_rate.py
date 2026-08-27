#!/usr/bin/env python3
"""Derive the real per-node record rate from the CERN S3 archive.

Answers the question `docs/SOAK_PLAN.md` makes a pre-flight blocker: is
1,000 records a second a per-worker figure or a per-farm one?

Two stages, because the whole InfoLogger archive is ~4 GB compressed:

  window  scan every dump, keep only per-host totals and a farm-wide
          per-second histogram.  Prints the busiest windows.
  matrix  re-scan, counting only rows inside one window, and build the
          per-host per-second matrix the plan asks for.

InfoLogger dumps are gzip'd mysqldump extended INSERTs.  Rather than parse
all sixteen columns, both stages match the timestamp and hostname columns
directly, which is two orders of magnitude faster and enough to count.
"""

import argparse
import collections
import concurrent.futures
import gzip
import json
import os
import re
import sys
import time

import boto3
from botocore.config import Config

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.cern.ch")
S3_BUCKET = os.environ.get("S3_BUCKET", "epn-backup-logs")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
INFOLOGGER_PREFIX = os.environ.get("INFOLOGGER_PREFIX", "infologger-2026/")
RUN_TAG = os.environ.get("RUN_TAG", "33NXirFsSfT_38917")

ROW_RE = re.compile(rb",(1[0-9]{9}\.[0-9]+),'([A-Za-z0-9_.-]{1,64})',")
WORKER_RE = re.compile(r"^epn[0-9]+$")
DDS_TS_RE = re.compile(rb"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
DDS_MEMBER_RE = re.compile(r"/dds_\d{4}-\d{2}-\d{2}\.\d+\.log$")
HOST_RE = re.compile(r"_(epn[0-9]+)\.tar\.gz$")


def client():
    return boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name=S3_REGION,
                        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                                      max_pool_connections=32))


def retry(work, tries=4):
    """S3 streams break on long reads over the CERN link. Start the object
    again rather than losing the scan."""
    last = None
    for attempt in range(tries):
        try:
            return work()
        except Exception as error:  # noqa: BLE001 - any stream failure retries
            last = error
            time.sleep(2 * (attempt + 1))
    raise last


def objects(s3, prefix, min_size=1000):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=prefix)
    for page in pages:
        for obj in page.get("Contents", []):
            if obj["Size"] > min_size:
                yield obj["Key"], obj["Size"]


def scan_window(key):
    def once():
        s3 = client()
        body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"]
        hosts = collections.Counter()
        seconds = collections.Counter()
        with gzip.GzipFile(fileobj=body) as gz:
            for line in gz:
                if not line.startswith(b"INSERT INTO"):
                    continue
                for match in ROW_RE.finditer(line):
                    hosts[match.group(2).decode()] += 1
                    seconds[int(float(match.group(1)))] += 1
        return key, hosts, seconds
    return retry(once)


def scan_matrix(args):
    key, low, high = args

    def once():
        s3 = client()
        body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"]
        cells = collections.Counter()
        with gzip.GzipFile(fileobj=body) as gz:
            for line in gz:
                if not line.startswith(b"INSERT INTO"):
                    continue
                for match in ROW_RE.finditer(line):
                    second = int(float(match.group(1)))
                    if low <= second < high:
                        cells[(match.group(2).decode(), second)] += 1
        return key, cells
    return retry(once)


def scan_dds(key):
    import tarfile
    s3 = client()
    host = (HOST_RE.search(key) or [None, key])[1]
    body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"]
    seconds = collections.Counter()
    lines = 0
    with tarfile.open(fileobj=body, mode="r|gz") as tar:
        for member in tar:
            if not member.isfile() or not DDS_MEMBER_RE.search("/" + member.name):
                continue
            src = tar.extractfile(member)
            if src is None:
                break
            for raw in src:
                lines += 1
                match = DDS_TS_RE.match(raw)
                if match:
                    stamp = (match.group(1) + b" " + match.group(2)).decode()
                    seconds[stamp] += 1
            break
    return host, lines, seconds


def percentiles(values, points=(50, 90, 95, 99, 100)):
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for point in points:
        index = min(len(ordered) - 1, int(round(point / 100.0 * (len(ordered) - 1))))
        out["p%d" % point] = ordered[index]
    return out


def stamp(second):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(second))


def do_window(args):
    s3 = client()
    keys = [key for key, _ in objects(s3, INFOLOGGER_PREFIX)]
    if args.limit:
        keys = keys[: args.limit]
    hosts = collections.Counter()
    seconds = collections.Counter()
    done, failed = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_window, key): key for key in keys}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            try:
                key, host_counts, second_counts = future.result()
            except Exception as error:  # noqa: BLE001
                failed.append(futures[future])
                print("[%d/%d] FAILED %s: %s" % (done, len(keys),
                                                 futures[future], error),
                      file=sys.stderr, flush=True)
                continue
            hosts.update(host_counts)
            seconds.update(second_counts)
            print("[%d/%d] %s" % (done, len(keys), key), file=sys.stderr, flush=True)

    workers = {host: count for host, count in hosts.items() if WORKER_RE.match(host)}
    others = {host: count for host, count in hosts.items() if not WORKER_RE.match(host)}
    minutes = collections.Counter()
    for second, count in seconds.items():
        minutes[second - second % 60] += count
    top = minutes.most_common(10)

    result = {
        "objects": len(keys),
        "objects_failed": failed,
        "rows": sum(hosts.values()),
        "hosts": len(hosts),
        "worker_hosts": len(workers),
        "worker_rows": sum(workers.values()),
        "other_hosts": sorted(others, key=others.get, reverse=True)[:10],
        "other_rows": sum(others.values()),
        "first_second": min(seconds) if seconds else 0,
        "last_second": max(seconds) if seconds else 0,
        "seconds_with_records": len(seconds),
        "farm_per_second": percentiles(list(seconds.values())),
        "busiest_minutes": [{"start": start, "when": stamp(start), "rows": rows}
                            for start, rows in top],
        "top_hosts": [{"host": host, "rows": count}
                      for host, count in hosts.most_common(15)],
    }
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2)
    return 0


def do_matrix(args):
    s3 = client()
    keys = [key for key, _ in objects(s3, INFOLOGGER_PREFIX)]
    if args.limit:
        keys = keys[: args.limit]
    low, high = args.start, args.start + args.seconds
    cells = collections.Counter()
    done, failed = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_matrix, (key, low, high)): key for key in keys}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            try:
                key, part = future.result()
            except Exception as error:  # noqa: BLE001
                failed.append(futures[future])
                print("[%d/%d] FAILED %s: %s" % (done, len(keys),
                                                 futures[future], error),
                      file=sys.stderr, flush=True)
                continue
            cells.update(part)
            print("[%d/%d] %s" % (done, len(keys), key), file=sys.stderr, flush=True)

    per_host = collections.defaultdict(dict)
    for (host, second), count in cells.items():
        per_host[host][second] = count

    def summarise(selector):
        chosen = [host for host in per_host if selector(host)]
        rates = []
        for host in chosen:
            counts = per_host[host]
            rates.extend(counts.get(second, 0) for second in range(low, high))
        farm = [sum(per_host[host].get(second, 0) for host in chosen)
                for second in range(low, high)]
        active = [host for host in chosen if sum(per_host[host].values()) > 0]
        return {
            "hosts": len(chosen),
            "hosts_with_records": len(active),
            "rows": sum(sum(per_host[host].values()) for host in chosen),
            "per_host_per_second": percentiles(rates),
            "per_host_per_second_mean": round(sum(rates) / max(1, len(rates)), 3),
            "busiest_host_second": max(rates) if rates else 0,
            "farm_per_second": percentiles(farm),
            "farm_per_second_mean": round(sum(farm) / max(1, len(farm)), 1),
        }

    result = {
        "objects_failed": failed,
        "window_start": low,
        "window_when": stamp(low),
        "window_seconds": args.seconds,
        "workers_only": summarise(lambda host: bool(WORKER_RE.match(host))),
        "all_hosts": summarise(lambda host: True),
        "top_hosts_in_window": [
            {"host": host, "rows": sum(counts.values()),
             "peak_second": max(counts.values())}
            for host, counts in sorted(per_host.items(),
                                       key=lambda item: -sum(item[1].values()))[:15]],
    }
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2)
    return 0


def do_dds(args):
    s3 = client()
    keys = [key for key, _ in objects(s3, "dds/%s_" % RUN_TAG)]
    if args.limit:
        keys = keys[: args.limit]
    per_host = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for host, lines, seconds in pool.map(scan_dds, keys):
            per_host[host] = {"lines": lines, "seconds": seconds}
            print("%s: %d lines" % (host, lines), file=sys.stderr, flush=True)

    rates = []
    spans = []
    for host, data in per_host.items():
        counts = list(data["seconds"].values())
        rates.extend(counts)
        if data["seconds"]:
            spans.append(len(data["seconds"]))
    result = {
        "hosts": len(per_host),
        "lines": sum(data["lines"] for data in per_host.values()),
        "per_host_lines": {host: data["lines"] for host, data in per_host.items()},
        "per_host_active_seconds_median": sorted(spans)[len(spans) // 2] if spans else 0,
        "per_host_per_active_second": percentiles(rates),
        "note": "counted over seconds that carry at least one line, so this is the "
                "rate while a node is talking, not an average over the run",
    }
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["window", "matrix", "dds"])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.stage == "window":
        return do_window(args)
    if args.stage == "matrix":
        if not args.start:
            parser.error("matrix needs --start, from the window stage")
        return do_matrix(args)
    return do_dds(args)


if __name__ == "__main__":
    sys.exit(main())
