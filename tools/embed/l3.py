#!/usr/bin/env python3
"""L3: the reduced ALICE lexical port, with each feature independently removable.

The plan wants differential fixtures against the pinned Sweet Search reference.
That reference cannot index log templates, so there is nothing to be differential
against. What can still be done is an ablation: build every feature, then remove
one at a time and measure what it was worth.

A feature that adds no measured value is removed and the removal is reported.
That rule survives the missing reference; the "custom against maintained
upstream" claim does not.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench
import systems

INDEX = "alice-templates-l3"

SETTINGS = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": {
            "filter": {
                "alice_delimiter": {
                    "type": "word_delimiter_graph", "preserve_original": True,
                    "catenate_all": True, "split_on_case_change": True,
                    "split_on_numerics": False, "stem_english_possessive": False},
                "alice_trigram": {"type": "ngram", "min_gram": 3, "max_gram": 3},
            },
            "analyzer": {
                "alice_identifier": {
                    "type": "custom", "tokenizer": "whitespace",
                    "filter": ["alice_delimiter", "lowercase", "flatten_graph"]},
                "alice_trigram_analyzer": {
                    "type": "custom", "tokenizer": "standard",
                    "filter": ["lowercase", "alice_trigram"]},
            },
        },
    },
    "mappings": {"properties": {
        "canonical_id": {"type": "keyword"},
        "template": {"type": "text", "analyzer": "standard"},
        "template_ident": {"type": "text", "analyzer": "alice_identifier"},
        "template_trigram": {"type": "text", "analyzer": "alice_trigram_analyzer"},
        "identifiers": {"type": "text", "analyzer": "alice_identifier"},
        "identifiers_exact": {"type": "keyword"},
        "identifiers_std": {"type": "text", "analyzer": "standard"},
        "program": {"type": "text", "analyzer": "standard"},
        "source": {"type": "text", "analyzer": "standard"},
        "detector": {"type": "text", "analyzer": "standard"},
    }},
}

FEATURES = ("identifier_anchoring", "field_weights", "term_rescue",
            "trigram_fallback", "score_calibration")


def build_index(client, corpus):
    from opensearchpy import helpers
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body=SETTINGS)
    actions = []
    for doc in corpus.docs:
        fields = bench.represent(doc, "R3")
        actions.append({"_index": INDEX, "_id": doc["canonical_id"], "_source": {
            "canonical_id": doc["canonical_id"],
            "template": fields["template"], "template_ident": fields["template"],
            "template_trigram": fields["template"],
            "identifiers": fields["identifiers"],
            "identifiers_exact": bench.identifier_tokens(doc["template"]),
            "identifiers_std": fields["identifiers"],
            "program": fields["program"], "source": fields["source"],
            "detector": fields["detector"]}})
    helpers.bulk(client, actions, chunk_size=1000, request_timeout=300)
    client.indices.refresh(index=INDEX)
    return client.count(index=INDEX)["count"]


def body_for(text, enabled):
    """One query, assembled from whichever features are switched on."""
    should = []
    if "field_weights" in enabled:
        should.append({"combined_fields": {
            "query": text,
            "fields": ["template^3.0", "identifiers_std^2.0", "program^1.0",
                       "source^1.0", "detector^1.0"],
            "operator": "or"}})
    else:
        should.append({"match": {"template": {"query": text}}})

    if "identifier_anchoring" in enabled:
        for token in bench.identifier_tokens(text) or []:
            should.append({"term": {"identifiers_exact": {"value": token,
                                                         "boost": 8.0}}})
        should.append({"match": {"template_ident": {"query": text, "boost": 1.5}}})

    if "term_rescue" in enabled:
        for term in [t for t in re.split(r"\W+", text) if len(t) > 3]:
            should.append({"match": {"template_ident": {
                "query": term, "boost": 0.4}}})

    if "trigram_fallback" in enabled:
        should.append({"match": {"template_trigram": {
            "query": text, "boost": 0.2, "minimum_should_match": "40%"}}})

    return {"query": {"bool": {"should": should, "minimum_should_match": 1}},
            "track_total_hits": False}


def calibrate(rows):
    """Map raw scores onto a per-query quantile scale.

    Lexical scores are not comparable across queries, so a stable downstream
    consumer needs a scale that does not move with query length.
    """
    if not rows:
        return rows
    values = [s for _, s in rows]
    lo, hi = min(values), max(values)
    if hi == lo:
        return [(cid, 1.0) for cid, _ in rows]
    return [(cid, (s - lo) / (hi - lo)) for cid, s in rows]


def run(client, queries, enabled, depth=200):
    results, latencies = {}, []
    for qid, row in queries.items():
        response = client.search(index=INDEX, body=body_for(row["text"], enabled),
                                 size=depth, request_timeout=180)
        rows = [(h["_id"], float(h["_score"])) for h in response["hits"]["hits"]]
        if "score_calibration" in enabled:
            rows = calibrate(rows)
        results[qid] = rows
        latencies.append(response["took"])
    return results, latencies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--out", default="downloads/frozen/l3-ablation.json")
    parser.add_argument("--task", default="natural_language")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--practical", type=float, default=0.03197847477701114)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task=args.task))
    qrels = bench.Qrels.load(args.qrels)

    client = systems.client()
    indexed = build_index(client, corpus)

    import compare
    arms = {"L3-full": set(FEATURES)}
    for feature in FEATURES:
        arms["L3-minus-%s" % feature] = set(FEATURES) - {feature}
    arms["L3-none"] = set()

    report = {"indexed": indexed, "task": args.task, "arms": {},
              "feature_value": {}}
    per_intent = {}
    for name, enabled in arms.items():
        results, latencies = run(client, queries, enabled, args.depth)
        manifest = bench.make_manifest(**compare.base_manifest(
            name, "03640622026320ba", "development-97-2026-09-08",
            os.path.basename(args.qrels), model_id="L3 reduced ALICE port",
            model_revision="this run", adapter="opensearch bool assembly",
            representation="R3", search_parameters={"features": sorted(enabled),
                                                    "depth": args.depth},
            fusion_parameters="not applicable",
            opensearch_version=systems.engine_version(client),
            index_uuid=client.indices.get_settings(index=INDEX)[INDEX]["settings"]["index"]["uuid"],
            index_schema_revision="l3.SETTINGS", searched_indices=[INDEX],
            primary_shards=1, replicas=0, candidate_depth=args.depth,
            refresh_state="refreshed", search_pipeline_revision="none",
            device="opensearch jvm", finished=compare.now()))
        path = bench.write_run("downloads/frozen/runs/%s.json" % name, name, name,
                               args.task, manifest, results, depth=args.depth)
        scored = bench.score_run(bench.read_run(path), qrels, queries, task=args.task)
        per_intent[name] = {g: r.get("ndcg@10") for g, r in scored["per_intent"].items()}
        report["arms"][name] = {"summary": scored["summary"],
                                "features": sorted(enabled), "run_file": path}
        print("%-28s ndcg=%.4f judged10=%.3f" % (
            name, scored["summary"]["ndcg@10"], scored["summary"]["judged@10"]),
            flush=True)

    for feature in FEATURES:
        removed = "L3-minus-%s" % feature
        boot = bench.bootstrap_paired(per_intent["L3-full"], per_intent[removed])
        boot["feature_is_worth_keeping"] = bool(
            boot["difference"] is not None and boot["difference"] > args.practical
            and boot["low"] > 0)
        report["feature_value"][feature] = boot
        print("  removing %-22s costs %+.4f CI[%+.4f,%+.4f]  keep=%s" % (
            feature, boot["difference"], boot["low"], boot["high"],
            boot["feature_is_worth_keeping"]), flush=True)

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
