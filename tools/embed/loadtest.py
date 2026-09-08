#!/usr/bin/env python3
"""S2 performance: warm and cold behaviour, concurrency, and load during updates.

Relevance says which system to build. This says whether it can be served. The
plan asks for warm and cold separately, a fixed concurrency and query rate, and
the same load repeated while template updates are in flight, because an index
that is quick when idle is not the thing the deployment runs.

Latency targets in this run are machine-authored and provisional. They remove no
candidate. These numbers are measured against them, not judged by them.
"""
import argparse
import json
import os
import random
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import systems

TARGETS_MS = {"end_to_end_p95": 300, "end_to_end_p99": 600}


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def one_pass(client, index, texts, builder, size):
    latencies = []
    for text in texts:
        started = time.perf_counter()
        client.search(index=index, body=builder(text), size=size,
                      request_timeout=120)
        latencies.append((time.perf_counter() - started) * 1000.0)
    return latencies


def cold(client, index, texts, builder, size):
    """Clear the caches the engine would keep warm between runs."""
    client.indices.clear_cache(index=index, query=True, request=True, fielddata=True)
    return one_pass(client, index, texts, builder, size)


def concurrent(client, index, texts, builder, size, workers, rounds):
    latencies, lock = [], threading.Lock()

    def run():
        local = []
        for _ in range(rounds):
            text = random.choice(texts)
            started = time.perf_counter()
            client.search(index=index, body=builder(text), size=size,
                          request_timeout=120)
            local.append((time.perf_counter() - started) * 1000.0)
        with lock:
            latencies.extend(local)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started
    return latencies, len(latencies) / elapsed


def updater(client, index, corpus, stop, counter):
    """Write template updates while the load runs, as the plan requires."""
    n = 0
    while not stop.is_set():
        doc = random.choice(corpus.docs)
        fields = bench.represent(doc, "R3")
        client.index(index=index, id=doc["canonical_id"],
                     body={"canonical_id": doc["canonical_id"],
                           "template": fields["template"],
                           "template_ident": fields["template"],
                           "identifiers": fields["identifiers"],
                           "identifiers_std": fields["identifiers"],
                           "program": fields["program"], "source": fields["source"],
                           "detector": fields["detector"],
                           "r1_text": bench.represent(doc, "R1"),
                           "r2_text": bench.represent(doc, "R2"),
                           "lines": doc.get("lines", 0)},
                     request_timeout=60)
        n += 1
        time.sleep(0.05)
    counter.append(n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--out", default="downloads/frozen/s2-loadtest.json")
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=25)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    texts = [r["text"] for r in queries.values()]

    client = systems.client()
    if not client.indices.exists(index=systems.INDEX):
        systems.build_index(client, corpus)
    builder = systems.lexical_builder("L1", "R2")

    report = {"engine": systems.engine_version(client), "index": systems.INDEX,
              "result_size": args.size, "queries": len(texts),
              "targets_ms": TARGETS_MS, "targets_are": "machine-authored and provisional"}

    cold_lat = cold(client, systems.INDEX, texts, builder, args.size)
    one_pass(client, systems.INDEX, texts, builder, args.size)
    warm_lat = one_pass(client, systems.INDEX, texts, builder, args.size)
    for label, lat in (("cold", cold_lat), ("warm", warm_lat)):
        report[label] = {"p50": percentile(lat, 50), "p95": percentile(lat, 95),
                         "p99": percentile(lat, 99), "mean": statistics.mean(lat)}
        print("%-5s p50=%.1f p95=%.1f p99=%.1f ms" % (
            label, report[label]["p50"], report[label]["p95"],
            report[label]["p99"]), flush=True)

    lat, qps = concurrent(client, systems.INDEX, texts, builder, args.size,
                          args.workers, args.rounds)
    report["concurrent"] = {"workers": args.workers, "queries": len(lat),
                            "queries_per_second": qps, "p50": percentile(lat, 50),
                            "p95": percentile(lat, 95), "p99": percentile(lat, 99)}
    print("concurrent workers=%d qps=%.1f p50=%.1f p95=%.1f p99=%.1f ms" % (
        args.workers, qps, report["concurrent"]["p50"],
        report["concurrent"]["p95"], report["concurrent"]["p99"]), flush=True)

    stop, counter = threading.Event(), []
    thread = threading.Thread(target=updater,
                              args=(client, systems.INDEX, corpus, stop, counter))
    thread.start()
    try:
        lat2, qps2 = concurrent(client, systems.INDEX, texts, builder, args.size,
                                args.workers, args.rounds)
    finally:
        stop.set()
        thread.join()
    report["concurrent_while_updating"] = {
        "workers": args.workers, "template_updates_written": counter[0] if counter else 0,
        "queries_per_second": qps2, "p50": percentile(lat2, 50),
        "p95": percentile(lat2, 95), "p99": percentile(lat2, 99)}
    print("while updating qps=%.1f p50=%.1f p95=%.1f p99=%.1f  (%d updates written)" % (
        qps2, report["concurrent_while_updating"]["p50"],
        report["concurrent_while_updating"]["p95"],
        report["concurrent_while_updating"]["p99"],
        report["concurrent_while_updating"]["template_updates_written"]), flush=True)

    report["against_targets"] = {
        "warm_p95_within_target": report["warm"]["p95"] <= TARGETS_MS["end_to_end_p95"],
        "concurrent_p95_within_target": report["concurrent"]["p95"] <= TARGETS_MS["end_to_end_p95"],
        "while_updating_p95_within_target": report["concurrent_while_updating"]["p95"] <= TARGETS_MS["end_to_end_p95"],
        "note": "these targets remove no candidate; they are machine-authored"}
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
