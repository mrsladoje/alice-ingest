#!/usr/bin/env python3
"""Stage S2c: freeze the two numbers every later gate reads.

The minimum practically important difference comes from measurement, not from
taste: it is the half-width of the bootstrap interval on the paired difference
between the two lexical controls, or 0.02 nDCG@10, whichever is larger. A run
that skips this step is a run whose gates mean nothing.

The product performance contract is split in two and the halves are labelled.
The hard constraints are read off the deployment and remove a candidate at Stage
S10. The latency targets are machine-authored, provisional, and remove nothing.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import compare
import systems

FLOOR = 0.02
Z_ALPHA = 1.959963984540054
Z_BETA = 0.8416212335729143

HARD_CONSTRAINTS = {
    "source": ["deploy/group_vars/all.yml",
               "deploy/roles/sweet_opensearch/defaults/main.yml",
               "deploy/roles/sweet_template_catalog/defaults/main.yml",
               "deploy/roles/sweet_collector/defaults/main.yml"],
    "read_on": "2026-09-08",
    "local_only": ("the deployment runs on the EPN farm with no external "
                   "inference service, so a candidate that needs a hosted API "
                   "is removed"),
    "opensearch_version": "3.7.0",
    "opensearch_heap_per_node_bytes": 1073741824,
    "opensearch_heap_note": ("opensearch_heap_size and opensearch_worker_heap_size "
                             "are both 1g"),
    "worker_processors": 4,
    "alice_service_memory_high": "256M",
    "alice_service_memory_max": "512M",
    "template_catalog_memory_max": "512M",
    "template_catalog_max_templates": 20000,
    "fluent_bit_memory_high": "384M",
    "fluent_bit_memory_max": "768M",
    "removes_a_candidate_at": "Stage S10 decision-order step 3",
}

PROVISIONAL_LATENCY = {
    "authored_by": "machine, during the unattended run of 2026-09-08",
    "approved_by": "nobody",
    "status": "provisional",
    "removes_a_candidate": False,
    "why_not": ("the plan forbids inventing latency limits during model "
                "comparison and requires an owner's approval, and no subagent "
                "may give it"),
    "targets_ms": {
        "query_encode_p95": 50,
        "retrieval_engine_p95": 100,
        "end_to_end_p95": 300,
        "end_to_end_p99": 600,
    },
    "basis": ("an operator typing a search expects an answer inside the time "
              "they would wait for a web page; these are round numbers, not "
              "measurements"),
}


def detectable_difference(standard_deviation, groups, z_alpha=Z_ALPHA, z_beta=Z_BETA):
    """The smallest paired difference this many groups can detect.

    Inverts the paired-sample power formula. Raising the threshold to make a
    result significant is forbidden, so this number limits the conclusion
    instead.
    """
    if not groups or standard_deviation is None:
        return None
    return (z_alpha + z_beta) * standard_deviation / math.sqrt(groups)


def paired_sd(a, b):
    keys = sorted(set(a) & set(b))
    diffs = [a[k] - b[k] for k in keys
             if a.get(k) is not None and b.get(k) is not None]
    n = len(diffs)
    if n < 2:
        return None, n
    mean = sum(diffs) / n
    variance = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    return math.sqrt(variance), n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/frozen-numbers.json")
    parser.add_argument("--heldout-groups", type=int, default=62)
    parser.add_argument("--replicates", type=int, default=1000)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path))
    natural = {k: v for k, v in queries.items()
               if v.get("task") == "natural_language"}
    qrels = bench.Qrels.load(args.qrels)

    client = systems.client()
    settings = client.indices.get_settings(index=systems.INDEX)
    key = list(settings)[0]
    info = {"opensearch_version": systems.engine_version(client),
            "index": systems.INDEX,
            "index_uuid": settings[key]["settings"]["index"]["uuid"],
            "primary_shards": 1, "replicas": 0}

    scored = {}
    for name, builder in (("L0-development", systems.l0_body),
                          ("L1-development", systems.l1_body)):
        results, manifest = compare.lexical_run(
            client, name, natural, builder, "03640622026320ba",
            "development-97-2026-09-08", os.path.basename(args.qrels), info,
            depth=200)
        path = bench.write_run("downloads/frozen/runs/%s.json" % name, name, name,
                               "natural_language", manifest, results, depth=200)
        scored[name] = bench.score_run(bench.read_run(path), qrels, natural)

    a = {g: row.get("ndcg@10") for g, row in scored["L1-development"]["per_intent"].items()}
    b = {g: row.get("ndcg@10") for g, row in scored["L0-development"]["per_intent"].items()}
    boot = bench.bootstrap_paired(a, b, replicates=args.replicates)
    half = (boot["high"] - boot["low"]) / 2.0 if boot["difference"] is not None else None
    practical = max(half, FLOOR) if half is not None else FLOOR

    sd, pairs = paired_sd(a, b)
    detectable = detectable_difference(sd, args.heldout_groups)

    coverage = bench.coverage_cases(qrels, natural)
    payload = {
        "created": "2026-09-08",
        "corpus_id": "03640622026320ba",
        "judgment_set": args.qrels,
        "development_intents_scored": scored["L0-development"]["intents_scored"],
        "minimum_practically_important_difference": {
            "bootstrap_half_width": half,
            "floor": FLOOR,
            "frozen_value": practical,
            "which_won": "bootstrap half-width" if half and half > FLOOR else "the 0.02 floor",
            "recipe": ("L1 minus L0 paired nDCG@10 over development natural-language "
                       "intent groups, %d bootstrap replicates over intent groups, "
                       "95 percent percentile interval, half its width" % args.replicates),
            "bootstrap": boot,
            "l0_summary": scored["L0-development"]["summary"],
            "l1_summary": scored["L1-development"]["summary"],
        },
        "held_out_power": {
            "intent_groups": args.heldout_groups,
            "paired_standard_deviation_on_development": sd,
            "pairs_used": pairs,
            "alpha_two_sided": 0.05,
            "target_power": 0.80,
            "smallest_detectable_difference": detectable,
            "verdict": None,
            "rule": ("never raise the threshold to make a result significant; "
                     "limit the conclusion to what the set can detect"),
        },
        "corpus_coverage_cases": coverage,
        "hard_constraints": HARD_CONSTRAINTS,
        "provisional_latency_targets": PROVISIONAL_LATENCY,
    }
    if detectable is not None:
        payload["held_out_power"]["verdict"] = (
            "the sealed set can detect the frozen practical difference"
            if detectable <= practical else
            "the sealed set cannot detect the frozen practical difference; every "
            "held-out conclusion is limited to differences of at least %.4f nDCG@10"
            % detectable)
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps({
        "practical_difference": practical,
        "half_width": half,
        "which_won": payload["minimum_practically_important_difference"]["which_won"],
        "l0_ndcg": scored["L0-development"]["summary"].get("ndcg@10"),
        "l1_ndcg": scored["L1-development"]["summary"].get("ndcg@10"),
        "judged@10": {n: s["summary"].get("judged@10") for n, s in scored.items()},
        "paired_sd": sd,
        "detectable_at_62_groups": detectable,
        "verdict": payload["held_out_power"]["verdict"],
        "coverage_cases": len(coverage),
    }, indent=2))


if __name__ == "__main__":
    main()
