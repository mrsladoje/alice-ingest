#!/usr/bin/env python3
"""SP0: the maintained learned sparse control, on the production OpenSearch.

The plan names this as the maintained learned-sparse baseline, and the first
pass never deployed it, which meant no learned sparse system was compared at
all. This deploys the document-only encoder through the machine-learning plugin
and searches it with the matching tokenizer, which is the supported path.

Document-only means the model encodes documents at ingest and the query side is
a tokenizer. That is the configuration the deployment would run, because it
keeps model inference off the query path.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import systems

DOC_MODEL = "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte"
QUERY_TOKENIZER = "amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1"
INDEX = "alice-templates-sparse"
PIPELINE = "alice-sparse-ingest"


def settings(client):
    client.cluster.put_settings(body={"persistent": {
        "plugins.ml_commons.only_run_on_ml_node": False,
        "plugins.ml_commons.allow_registering_model_via_url": True,
        "plugins.ml_commons.native_memory_threshold": 99,
    }})


def wait_task(client, task_id, timeout=1800, label=""):
    started = time.time()
    while time.time() - started < timeout:
        task = client.transport.perform_request(
            "GET", "/_plugins/_ml/tasks/%s" % task_id)
        state = task.get("state")
        if state in ("COMPLETED", "COMPLETED_WITH_ERROR"):
            return task
        if state == "FAILED":
            raise RuntimeError("%s failed: %s" % (label, task.get("error")))
        time.sleep(5)
    raise TimeoutError("%s timed out after %ds" % (label, timeout))


def register_and_deploy(client, name, version="1.0.0", model_format="TORCH_SCRIPT"):
    body = {"name": name, "version": version, "model_format": model_format}
    response = client.transport.perform_request(
        "POST", "/_plugins/_ml/models/_register", body=body)
    task = wait_task(client, response["task_id"], label="register %s" % name)
    model_id = task["model_id"]
    response = client.transport.perform_request(
        "POST", "/_plugins/_ml/models/%s/_deploy" % model_id)
    wait_task(client, response["task_id"], label="deploy %s" % name)
    return model_id


def build(client, corpus, doc_model_id):
    client.transport.perform_request("PUT", "/_ingest/pipeline/%s" % PIPELINE, body={
        "description": "sparse encode the template text",
        "processors": [{"sparse_encoding": {
            "model_id": doc_model_id,
            "field_map": {"template": "template_sparse"}}}]})
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body={
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0,
                               "default_pipeline": PIPELINE}},
        "mappings": {"properties": {
            "canonical_id": {"type": "keyword"},
            "template": {"type": "text"},
            "template_sparse": {"type": "rank_features"}}}})
    from opensearchpy import helpers
    actions = [{"_index": INDEX, "_id": doc["canonical_id"],
                "_source": {"canonical_id": doc["canonical_id"],
                            "template": bench.represent(doc, "R1")}}
               for doc in corpus.docs]
    started = time.time()
    helpers.bulk(client, actions, chunk_size=100, request_timeout=900)
    client.indices.refresh(index=INDEX)
    client.indices.flush(index=INDEX)
    stats = client.indices.stats(index=INDEX)
    return {"documents": client.count(index=INDEX)["count"],
            "bytes": stats["_all"]["primaries"]["store"]["size_in_bytes"],
            "ingest_seconds": round(time.time() - started, 1)}


def search(client, text, query_model_id, depth=200):
    body = {"query": {"neural_sparse": {"template_sparse": {
        "query_text": text, "model_id": query_model_id}}}}
    response = client.search(index=INDEX, body=body, size=depth,
                             request_timeout=180)
    return [(h["_id"], float(h["_score"])) for h in response["hits"]["hits"]], \
        response["took"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/sp0.json")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task="natural_language"))
    qrels = bench.Qrels.load(args.qrels)

    client = systems.client()
    settings(client)
    report = {"doc_model": DOC_MODEL, "query_tokenizer": QUERY_TOKENIZER}

    print("registering the document encoder, this downloads inside the container",
          flush=True)
    doc_id = register_and_deploy(client, DOC_MODEL)
    report["doc_model_id"] = doc_id
    print("document encoder deployed:", doc_id, flush=True)

    query_id = register_and_deploy(client, QUERY_TOKENIZER)
    report["query_model_id"] = query_id
    print("query tokenizer deployed:", query_id, flush=True)

    report["index"] = build(client, corpus, doc_id)
    print("indexed:", report["index"], flush=True)

    results, latencies = {}, []
    for qid, row in queries.items():
        rows, took = search(client, row["text"], query_id, args.depth)
        results[qid] = rows
        latencies.append(took)

    import numpy as np
    import compare
    manifest = bench.make_manifest(**compare.base_manifest(
        "SP0", "03640622026320ba", "development-97-2026-09-08",
        os.path.basename(args.qrels), model_id=DOC_MODEL,
        model_revision="opensearch pretrained 1.0.0",
        adapter="neural sparse, document-only encoding",
        representation="R1", search_parameters={"depth": args.depth},
        fusion_parameters="not applicable",
        opensearch_version=systems.engine_version(client),
        index_uuid=client.indices.get_settings(index=INDEX)[INDEX]["settings"]["index"]["uuid"],
        index_schema_revision="sparse.build", searched_indices=[INDEX],
        primary_shards=1, replicas=0, candidate_depth=args.depth,
        refresh_state="refreshed and flushed", search_pipeline_revision=PIPELINE,
        device="opensearch ml node", finished=compare.now()))
    manifest["engine_latency_ms"] = {
        "p50": float(np.percentile(latencies, 50)),
        "p95": float(np.percentile(latencies, 95)),
        "p99": float(np.percentile(latencies, 99))}
    manifest["index_bytes"] = report["index"]["bytes"]
    manifest["index_bytes_per_template"] = round(
        report["index"]["bytes"] / max(1, len(corpus)), 1)
    path = bench.write_run("downloads/frozen/runs/SP0.json", "SP0", "SP0",
                           "natural_language", manifest, results, depth=args.depth)
    scored = bench.score_run(bench.read_run(path), qrels, queries)
    report["summary"] = scored["summary"]
    report["run_file"] = path
    report["latency_ms"] = manifest["engine_latency_ms"]
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps({"summary": scored["summary"],
                      "index_bytes_per_template": manifest["index_bytes_per_template"],
                      "latency_ms": manifest["engine_latency_ms"]},
                     indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
