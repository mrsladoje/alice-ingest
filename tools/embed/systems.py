#!/usr/bin/env python3
"""Retrieval systems: the lexical controls on OpenSearch, and exact dense search.

L0 and L1 are the mandatory lexical controls, so they run on the production
engine and version rather than on a Python reimplementation. Everything a later
gate compares against therefore comes from the same engine the deployment uses.

Dense search is exhaustive cosine over the whole corpus. Approximate search is a
speed decision, and it is measured against this exact reference rather than
substituted for it.
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench

INDEX = "alice-templates"
PRIMARY_SHARDS = 1
REPLICAS = 0

STANDARD_ANALYZER = "standard"
IDENTIFIER_ANALYZER = "alice_identifier"

SETTINGS = {
    "settings": {
        "index": {"number_of_shards": PRIMARY_SHARDS, "number_of_replicas": REPLICAS},
        "analysis": {
            "filter": {
                "alice_delimiter": {
                    "type": "word_delimiter_graph",
                    "preserve_original": True,
                    "catenate_all": True,
                    "split_on_case_change": True,
                    "split_on_numerics": False,
                    "stem_english_possessive": False,
                }
            },
            "analyzer": {
                IDENTIFIER_ANALYZER: {
                    "type": "custom",
                    "tokenizer": "whitespace",
                    "filter": ["alice_delimiter", "lowercase", "flatten_graph"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "canonical_id": {"type": "keyword"},
            "template": {"type": "text", "analyzer": STANDARD_ANALYZER},
            "template_ident": {"type": "text", "analyzer": IDENTIFIER_ANALYZER},
            "identifiers": {"type": "text", "analyzer": IDENTIFIER_ANALYZER},
            "identifiers_exact": {"type": "keyword"},
            "identifiers_std": {"type": "text", "analyzer": STANDARD_ANALYZER},
            "program": {"type": "text", "analyzer": STANDARD_ANALYZER,
                        "fields": {"exact": {"type": "keyword"}}},
            "source": {"type": "text", "analyzer": STANDARD_ANALYZER,
                       "fields": {"exact": {"type": "keyword"}}},
            "detector": {"type": "text", "analyzer": STANDARD_ANALYZER,
                         "fields": {"exact": {"type": "keyword"}}},
            "severity": {"type": "keyword"},
            "r1_text": {"type": "text", "analyzer": STANDARD_ANALYZER},
            "r2_text": {"type": "text", "analyzer": STANDARD_ANALYZER},
            "lines": {"type": "long"},
        }
    },
}


def client(host="localhost", port=9200):
    from opensearchpy import OpenSearch
    return OpenSearch([{"host": host, "port": port}], use_ssl=False,
                      verify_certs=False, timeout=120, max_retries=3,
                      retry_on_timeout=True)


def engine_version(os_client):
    return os_client.info()["version"]["number"]


def build_index(os_client, corpus, index=INDEX, refresh=True):
    """Rebuild the index from the frozen corpus and return its identity.

    The index universally unique identifier and the shard counts go into every
    manifest, because a fusion number measured on a different topology is a
    different number.
    """
    from opensearchpy import helpers
    if os_client.indices.exists(index=index):
        os_client.indices.delete(index=index)
    os_client.indices.create(index=index, body=SETTINGS)
    actions = []
    for doc in corpus.docs:
        fields = bench.represent(doc, "R3")
        actions.append({
            "_index": index,
            "_id": doc["canonical_id"],
            "_source": {
                "canonical_id": doc["canonical_id"],
                "template": fields["template"],
                "template_ident": fields["template"],
                "identifiers": fields["identifiers"],
                "identifiers_exact": bench.identifier_tokens(doc["template"]),
                "identifiers_std": fields["identifiers"],
                "program": fields["program"],
                "source": fields["source"],
                "detector": fields["detector"],
                "severity": fields["severity"],
                "r1_text": bench.represent(doc, "R1"),
                "r2_text": bench.represent(doc, "R2"),
                "lines": doc.get("lines", 0),
            },
        })
    started = time.time()
    helpers.bulk(os_client, actions, chunk_size=1000, request_timeout=180)
    if refresh:
        os_client.indices.refresh(index=index)
    stats = os_client.indices.stats(index=index)
    settings = os_client.indices.get_settings(index=index)
    key = list(settings)[0]
    return {
        "index": index,
        "index_uuid": settings[key]["settings"]["index"]["uuid"],
        "documents": os_client.count(index=index)["count"],
        "primary_shards": int(settings[key]["settings"]["index"]["number_of_shards"]),
        "replicas": int(settings[key]["settings"]["index"]["number_of_replicas"]),
        "bytes": stats["_all"]["primaries"]["store"]["size_in_bytes"],
        "build_seconds": round(time.time() - started, 1),
    }


def search(os_client, body, index=INDEX, size=200):
    response = os_client.search(index=index, body=body, size=size,
                                request_timeout=180)
    hits = response["hits"]["hits"]
    return [(h["_id"], float(h["_score"])) for h in hits], response["took"]


def l0_body(text, field="template"):
    """Plain BM25 over one analysed text field. The mandatory control."""
    return {"query": {"match": {field: {"query": text}}},
            "track_total_hits": False}


def l1_body(text, weights=None, fields=None):
    """BM25F through `combined_fields`, which is the maintained OpenSearch path.

    `combined_fields` treats the fields as one field, which is what BM25F means.
    A `multi_match` best_fields query is a different model and is not used here.

    Every field named here shares the standard analyzer, because OpenSearch
    rejects a `combined_fields` query whose fields analyse differently. The
    identifier-preserving copy of the same text therefore stays out of this
    query and is measured on its own in the Stage S4 analyzer screen.

    No weight falls below 1.0, because `combined_fields` rejects one that does.
    That is a floor on the search, not a tuned setting: Stage S4 tunes these
    weights on development queries and records what it chose.
    """
    weights = weights or {"template": 3.0, "program": 1.0, "identifiers_std": 2.0,
                          "source": 1.0, "detector": 1.0}
    fields = fields or ["%s^%s" % (name, weight) for name, weight in weights.items()]
    return {"query": {"combined_fields": {"query": text, "fields": fields,
                                          "operator": "or"}},
            "track_total_hits": False}


def identifier_body(text, weights=None):
    """The identifier-preserving path, with an exact keyword clause on top."""
    weights = weights or {"identifiers": 3.0, "template_ident": 2.0}
    return {
        "query": {
            "bool": {
                "should": [
                    {"terms": {"identifiers_exact": bench.identifier_tokens(text) or [text], "boost": 10.0}},
                    {"combined_fields": {
                        "query": text,
                        "fields": ["%s^%s" % (n, w) for n, w in weights.items()],
                        "operator": "or"}},
                ],
                "minimum_should_match": 1,
            }
        },
        "track_total_hits": False,
    }


def run_lexical(os_client, queries, body_builder, index=INDEX, depth=200):
    results, latencies = {}, []
    for qid, row in queries.items():
        rows, took = search(os_client, body_builder(row["text"]), index=index,
                            size=depth)
        results[qid] = rows
        latencies.append(took)
    return results, latencies


class DenseSystem:
    """Exhaustive cosine over the frozen corpus, in the declared representation."""

    def __init__(self, adapter, corpus, arm="R0"):
        self.adapter = adapter
        self.corpus = corpus
        self.arm = arm
        self.matrix = None
        self.encode_seconds = None

    def index(self, batch_report=None):
        texts = [bench.represent(doc, self.arm) for doc in self.corpus.docs]
        started = time.time()
        self.matrix = np.asarray(self.adapter.encode_document(texts),
                                 dtype=np.float32)
        self.encode_seconds = time.time() - started
        return {"documents": len(texts), "dimensions": int(self.matrix.shape[1]),
                "encode_seconds": round(self.encode_seconds, 1)}

    def run(self, queries, depth=200, mode="query"):
        results, latencies = {}, []
        ids = self.corpus.ids
        texts = [row["text"] for row in queries.values()]
        started = time.time()
        if mode == "symmetric":
            vectors = self.adapter.encode_symmetric(texts)
        else:
            vectors = self.adapter.encode_query(texts)
        query_seconds = time.time() - started
        for (qid, row), vector in zip(queries.items(), vectors):
            t0 = time.time()
            scores = self.matrix @ np.asarray(vector, dtype=np.float32)
            ranked = bench.rank(scores.tolist(), ids)
            lookup = {cid: float(scores[i]) for i, cid in enumerate(ids)}
            results[qid] = [(cid, lookup[cid]) for cid in ranked[:depth]]
            latencies.append((time.time() - t0) * 1000.0)
        return results, {"query_encode_seconds": round(query_seconds, 3),
                         "search_ms": latencies}


def exclude_self(results, queries, corpus):
    """Template similarity never returns the query's own canonical group.

    The group and every source instance in it leave the result list, which is
    what the plan means by excluding the query group.
    """
    out = {}
    for qid, rows in results.items():
        own = queries[qid].get("query_canonical_id")
        if not own:
            out[qid] = rows
            continue
        banned = {own} | set(corpus.duplicates.get(own, []))
        out[qid] = [(cid, score) for cid, score in rows if cid not in banned]
    return out


class LateSystem:
    """Exhaustive MaxSim over the whole corpus, which is the ranking oracle.

    The plan says to measure this rather than assume it is too expensive. Every
    document's token vectors are concatenated into one matrix, so one query is
    one matrix product followed by a segment maximum per document.
    """

    def __init__(self, adapter, corpus, arm="R0"):
        self.adapter = adapter
        self.corpus = corpus
        self.arm = arm
        self.tokens = None
        self.offsets = None
        self.encode_seconds = None

    def index(self):
        texts = [bench.represent(doc, self.arm) for doc in self.corpus.docs]
        started = time.time()
        vectors = self.adapter.encode_document(texts)
        self.encode_seconds = time.time() - started
        lengths = [v.shape[0] for v in vectors]
        self.offsets = np.cumsum([0] + lengths)
        self.tokens = np.concatenate(vectors, axis=0).astype(np.float32)
        return {"documents": len(texts), "token_rows": int(self.tokens.shape[0]),
                "token_dimensions": int(self.tokens.shape[1]),
                "mean_tokens_per_document": round(float(np.mean(lengths)), 1),
                "encode_seconds": round(self.encode_seconds, 1),
                "matrix_megabytes": round(self.tokens.nbytes / 1e6, 1)}

    def run(self, queries, depth=200, mode="query"):
        if self.tokens is None:
            self.index()
        ids = self.corpus.ids
        texts = [row["text"] for row in queries.values()]
        started = time.time()
        query_vectors = self.adapter.encode_query(texts)
        query_seconds = time.time() - started
        results, latencies = {}, []
        starts = self.offsets[:-1]
        for (qid, _row), query in zip(queries.items(), query_vectors):
            t0 = time.time()
            similarity = self.tokens @ np.asarray(query, dtype=np.float32).T
            per_document = np.maximum.reduceat(similarity, starts, axis=0)
            scores = per_document.sum(axis=1).astype(np.float32)
            ranked = bench.rank(scores.tolist(), ids)
            lookup = {cid: float(scores[j]) for j, cid in enumerate(ids)}
            results[qid] = [(cid, lookup[cid]) for cid in ranked[:depth]]
            latencies.append((time.time() - t0) * 1000.0)
        return results, {"query_encode_seconds": round(query_seconds, 3),
                         "search_ms": latencies}


def rerank(candidates, scores_by_id, depth):
    """Reorder a candidate list by a new score, keeping the rest untouched."""
    head = candidates[:depth]
    tail = candidates[depth:]
    ids = [cid for cid, _ in head]
    ordered = bench.rank([scores_by_id[cid] for cid in ids], ids)
    return [(cid, scores_by_id[cid]) for cid in ordered] + tail


REPRESENTATION_FIELD = {"R0": "template", "R1": "r1_text", "R2": "r2_text"}


def lexical_builder(system, representation="R0", weights=None):
    """Pick the query body for one lexical system in one representation.

    R0, R1 and R2 differ only in which analysed text field they search, so the
    representation screen compares the same scorer over three inputs rather than
    three different scorers.
    """
    if system == "L0":
        field = REPRESENTATION_FIELD.get(representation, "template")
        return lambda text: l0_body(text, field=field)
    if system == "L1":
        if representation == "R3":
            return lambda text: l1_body(text, weights=weights)
        field = REPRESENTATION_FIELD.get(representation, "template")
        combined = dict(weights or {})
        combined.setdefault(field, 3.0)
        combined.setdefault("program", 1.0)
        combined.setdefault("identifiers_std", 2.0)
        return lambda text: l1_body(text, weights=combined)
    if system == "ID":
        return identifier_body
    raise ValueError("unknown lexical system %r" % system)
