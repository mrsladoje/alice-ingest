#!/usr/bin/env python3
"""Run systems over a query set, score them, and compare them in pairs.

Every run written here carries a complete manifest, because the plan's rule is
that a number without a manifest is not a result. Comparisons are paired
bootstraps over intent groups, and exploratory families of comparisons get a
Holm correction.
"""
import argparse
import json
import os
import platform
import sys
import time
from collections import OrderedDict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench
import systems

RUN_DIR = "downloads/frozen/runs"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def base_manifest(run_id, corpus_id, query_set_id, judgment_set_id, **extra):
    revision, dirty = bench.git_revision()
    fields = {
        "run_id": run_id,
        "corpus_id": corpus_id,
        "query_set_id": query_set_id,
        "judgment_set_id": judgment_set_id,
        "code_revision": revision + ("+dirty" if dirty else ""),
        "dependency_lock_hash": bench.dependency_lock_hash(),
        "hardware": "%s %s" % (platform.machine(), platform.platform()),
        "warm_state": "warm",
        "seed": 20260908,
        "started": now(),
    }
    fields.update(extra)
    return fields


def lexical_run(os_client, name, queries, builder, corpus_id, query_set_id,
                judgment_set_id, index_info, depth=200, arm="R3"):
    started = time.time()
    results, latencies = systems.run_lexical(os_client, queries, builder,
                                             depth=depth)
    manifest = bench.make_manifest(**base_manifest(
        name, corpus_id, query_set_id, judgment_set_id,
        model_id="opensearch-bm25", model_revision=index_info["opensearch_version"],
        adapter="opensearch query DSL", representation=arm,
        search_parameters={"depth": depth, "similarity": "BM25 default"},
        fusion_parameters="not applicable",
        opensearch_version=index_info["opensearch_version"],
        index_uuid=index_info["index_uuid"], index_schema_revision="systems.SETTINGS",
        searched_indices=[index_info["index"]],
        primary_shards=index_info["primary_shards"], replicas=index_info["replicas"],
        candidate_depth=depth, refresh_state="refreshed after bulk index",
        search_pipeline_revision="none", device="opensearch jvm",
        finished=now()))
    manifest["engine_latency_ms"] = {
        "p50": float(np.percentile(latencies, 50)),
        "p95": float(np.percentile(latencies, 95)),
        "p99": float(np.percentile(latencies, 99)),
        "mean": float(np.mean(latencies)),
    }
    manifest["wall_seconds"] = round(time.time() - started, 2)
    return results, manifest


def dense_run(key, corpus, name, queries, corpus_id, query_set_id,
              judgment_set_id, arm="R1", depth=200, mode="query", device=None,
              kind=None):
    """`kind` decides the adapter, because a locally trained static model is a
    path rather than a registry key and would otherwise be routed to the dense
    adapter and fail."""
    if kind == "static" or (kind is None and key in adapters.STATIC):
        adapter = adapters.StaticAdapter(key)
    else:
        adapter = adapters.DenseAdapter(key, device=device)
    system = systems.DenseSystem(adapter, corpus, arm=arm)
    index_info = system.index()
    results, timing = system.run(queries, depth=depth, mode=mode)
    manifest = bench.make_manifest(**base_manifest(
        name, corpus_id, query_set_id, judgment_set_id,
        model_id=adapter.spec["model_id"], model_revision=adapter.spec["revision"],
        adapter={k: v for k, v in adapter.spec.items() if k != "role"},
        representation=arm,
        search_parameters={"depth": depth, "search": "exhaustive cosine",
                           "mode": mode},
        fusion_parameters="not applicable", opensearch_version="not applicable",
        index_uuid="not applicable", index_schema_revision="not applicable",
        searched_indices="in-memory matrix", primary_shards="not applicable",
        replicas="not applicable", candidate_depth=depth,
        refresh_state="not applicable", search_pipeline_revision="not applicable",
        device=getattr(adapter, "device", "cpu"), finished=now()))
    manifest["document_encode_seconds"] = index_info["encode_seconds"]
    manifest["query_encode_seconds"] = timing["query_encode_seconds"]
    manifest["search_ms"] = {
        "p50": float(np.percentile(timing["search_ms"], 50)),
        "p95": float(np.percentile(timing["search_ms"], 95)),
        "p99": float(np.percentile(timing["search_ms"], 99)),
    }
    manifest["dimensions"] = index_info["dimensions"]
    return results, manifest


def compare_systems(scored, reference, metric="ndcg@10", replicates=1000,
                    seed=20260908, holm_family=None):
    """Every system against one reference, on one metric, over intent groups."""
    out = {}
    reference_values = {g: row.get(metric) for g, row in scored[reference]["per_intent"].items()}
    for name, result in scored.items():
        if name == reference:
            continue
        values = {g: row.get(metric) for g, row in result["per_intent"].items()}
        out[name] = bench.bootstrap_paired(values, reference_values,
                                           replicates=replicates, seed=seed)
    if holm_family:
        adjusted = bench.holm({name: row["p"] for name, row in out.items()})
        for name in out:
            out[name]["p_holm"] = adjusted[name]
    return {"reference": reference, "metric": metric, "comparisons": out}


def save(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    os.replace(tmp, path)
    return path


def late_run(key, corpus, name, queries, corpus_id, query_set_id,
             judgment_set_id, arm="R1", depth=200, device=None):
    """One late-interaction arm, ranked by exhaustive MaxSim over the corpus."""
    adapter = adapters.LateAdapter(key, device=device)
    system = systems.LateSystem(adapter, corpus, arm=arm)
    index_info = system.index()
    results, timing = system.run(queries, depth=depth)
    manifest = bench.make_manifest(**base_manifest(
        name, corpus_id, query_set_id, judgment_set_id,
        model_id=adapter.spec["model_id"], model_revision=adapter.spec["revision"],
        adapter={k: v for k, v in adapter.spec.items() if k != "role"},
        representation=arm,
        search_parameters={"depth": depth, "search": "exhaustive MaxSim"},
        fusion_parameters="not applicable", opensearch_version="not applicable",
        index_uuid="not applicable", index_schema_revision="not applicable",
        searched_indices="in-memory token matrix", primary_shards="not applicable",
        replicas="not applicable", candidate_depth=depth,
        refresh_state="not applicable", search_pipeline_revision="not applicable",
        device=adapter.device, finished=now()))
    manifest["document_encode_seconds"] = index_info["encode_seconds"]
    manifest["query_encode_seconds"] = timing["query_encode_seconds"]
    manifest["token_rows"] = index_info["token_rows"]
    manifest["token_dimensions"] = index_info["token_dimensions"]
    manifest["matrix_megabytes"] = index_info["matrix_megabytes"]
    manifest["search_ms"] = {
        "p50": float(np.percentile(timing["search_ms"], 50)),
        "p95": float(np.percentile(timing["search_ms"], 95)),
        "p99": float(np.percentile(timing["search_ms"], 99)),
    }
    return results, manifest
