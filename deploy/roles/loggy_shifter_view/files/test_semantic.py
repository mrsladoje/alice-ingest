#!/usr/bin/env python3
import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.environ.setdefault("ALICE_SHARED_PATH",
                      os.path.join(REPO, "deploy", "shared"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "embed"))

import measure_serving                                       # noqa: E402
import semantic                                              # noqa: E402
import template_contract as contract                         # noqa: E402

NOW = 1788877215000
DAY = 86400000


def config(**kwargs):
    values = {
        "enabled": True,
        "backend": semantic.BACKEND_MODEL2VEC,
        "model_path": "unused-in-tests",
        "model_revision": "test-revision",
        "dimensions": 4,
        "max_versions": 100,
        "max_vector_bytes": 100 * 4 * 4,
        "batch_size": 2,
        "max_results": 10,
        "query_wait_seconds": 0.5,
        "encode_wait_seconds": 0.5,
    }
    values.update(kwargs)
    return semantic.Config(**values)


class WordEncoder(object):

    def __init__(self, dimensions=4, revision="test-revision"):
        self.dimensions = dimensions
        self.revision = revision
        self.calls = []
        self.texts = []
        self.fail_with = None
        self.delay = 0.0

    def encode(self, texts):
        texts = list(texts)
        if self.fail_with is not None:
            raise self.fail_with
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(len(texts))
        self.texts.extend(texts)
        return [self.vector(text) for text in texts]

    def vector(self, text):
        values = [0.0] * self.dimensions
        for index, word in enumerate(text.split()):
            values[index % self.dimensions] += float(len(word))
        if not any(values):
            values[0] = 1.0
        return values


def factory(encoder):
    def load(cfg):
        return encoder
    return load


def entry(version, template, last_observed):
    return {
        "version_id": version,
        "template": template,
        "last_observed": last_observed,
    }


def version(identifier, template, last_observed=NOW):
    return semantic.Version(identifier, template, last_observed)


class ConfigTest(unittest.TestCase):

    def test_defaults_are_off(self):
        cfg = semantic.Config.from_environment({})
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.backend, semantic.BACKEND_NONE)
        self.assertEqual(cfg.dimensions, 512)
        self.assertEqual(cfg.model_revision, "unset")

    def test_environment_reads_the_role_defaults(self):
        cfg = semantic.Config.from_environment({
            "SHIFTER_SEMANTIC_ENABLED": "true",
            "SHIFTER_SEMANTIC_BACKEND": "model2vec",
            "SHIFTER_SEMANTIC_MODEL_PATH": "/opt/loggy/model",
            "SHIFTER_SEMANTIC_MAX_VERSIONS": "20000",
            "SHIFTER_VECTOR_CACHE_BYTES": "16777216",
            "SHIFTER_TEMPLATE_PAGE_ROWS": "50",
            "SHIFTER_ACTIVE_DAYS": "28",
            "SHIFTER_DEFINITION_RETENTION_DAYS": "90",
        })
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_versions, 20000)
        self.assertEqual(cfg.max_vector_bytes, 16777216)
        self.assertEqual(cfg.max_results, 50)
        self.assertEqual(cfg.active_ms, contract.ACTIVE_MS)
        self.assertEqual(cfg.retention_ms, contract.DEFINITION_RETENTION_MS)

    def test_bad_values_fall_back_instead_of_raising(self):
        cfg = semantic.Config.from_environment({
            "SHIFTER_SEMANTIC_ENABLED": "maybe",
            "SHIFTER_SEMANTIC_MAX_VERSIONS": "twenty",
            "SHIFTER_VECTOR_CACHE_BYTES": "-1",
            "SHIFTER_SEMANTIC_BACKEND": "faiss",
        })
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_versions, semantic.DEFAULT_MAX_VERSIONS)
        self.assertEqual(cfg.max_vector_bytes,
                         semantic.DEFAULT_MAX_VECTOR_BYTES)
        self.assertEqual(cfg.backend, semantic.BACKEND_NONE)

    def test_only_one_encoding_task_is_permitted(self):
        cfg = semantic.Config.from_environment({"SHIFTER_TEMPLATE_ENCODERS":
                                                "8"})
        self.assertEqual(cfg.encoders, 1)

    def test_capacity_is_the_smaller_of_the_two_limits(self):
        cfg = config(max_versions=10, dimensions=4, max_vector_bytes=1000)
        self.assertEqual(cfg.vector_capacity, 10)
        self.assertEqual(cfg.binding_limit, semantic.REASON_VERSION_LIMIT)
        cfg = config(max_versions=1000, dimensions=4, max_vector_bytes=160)
        self.assertEqual(cfg.vector_capacity, 10)
        self.assertEqual(cfg.binding_limit,
                         semantic.REASON_VECTOR_BYTES_LIMIT)

    def test_the_plan_arithmetic_reproduces(self):
        cfg = semantic.Config(dimensions=512, max_versions=5571,
                              max_vector_bytes=1 << 30)
        self.assertEqual(cfg.vector_capacity, 5571)
        self.assertEqual(5571 * cfg.vector_row_bytes, 11409408)


class VectorStoreTest(unittest.TestCase):

    def test_bytes_are_four_per_value(self):
        store = semantic.VectorStore(512, 10)
        store.add("a", [1.0] * 512)
        self.assertEqual(store.vector_bytes, 512 * 4)
        store.add("b", [1.0] * 512)
        self.assertEqual(store.vector_bytes, 2 * 512 * 4)

    def test_vectors_are_stored_as_unit_length(self):
        store = semantic.VectorStore(2, 4)
        store.add("a", [3.0, 4.0])
        vector = store.vector("a")
        self.assertAlmostEqual(vector[0], 0.6, places=6)
        self.assertAlmostEqual(vector[1], 0.8, places=6)

    def test_a_zero_vector_stays_zero(self):
        store = semantic.VectorStore(3, 2)
        store.add("a", [0.0, 0.0, 0.0])
        self.assertEqual(list(store.vector("a")), [0.0, 0.0, 0.0])

    def test_wrong_dimensions_are_refused(self):
        store = semantic.VectorStore(4, 2)
        with self.assertRaises(semantic.EncoderUnavailable):
            store.add("a", [1.0, 2.0])

    def test_capacity_is_hard(self):
        store = semantic.VectorStore(2, 2)
        store.add("a", [1.0, 0.0])
        store.add("b", [0.0, 1.0])
        with self.assertRaises(semantic.LimitReached):
            store.add("c", [1.0, 1.0])
        self.assertEqual(len(store), 2)

    def test_replacing_a_version_does_not_grow_the_store(self):
        store = semantic.VectorStore(2, 2)
        store.add("a", [1.0, 0.0])
        self.assertFalse(store.add("a", [0.0, 1.0]))
        self.assertEqual(len(store), 1)
        self.assertAlmostEqual(store.vector("a")[1], 1.0, places=6)

    def test_discard_removes_the_row_and_its_bytes(self):
        store = semantic.VectorStore(2, 3)
        store.add("a", [1.0, 0.0])
        store.add("b", [0.0, 1.0])
        store.add("c", [1.0, 1.0])
        self.assertTrue(store.discard("a"))
        self.assertFalse(store.discard("a"))
        self.assertEqual(len(store), 2)
        self.assertEqual(store.vector_bytes, 2 * 2 * 4)
        self.assertIsNone(store.vector("a"))
        self.assertAlmostEqual(store.vector("b")[1], 1.0, places=6)
        self.assertAlmostEqual(store.vector("c")[0],
                               1.0 / math.sqrt(2.0), places=6)

    def test_search_is_exact_and_ordered(self):
        store = semantic.VectorStore(2, 4)
        store.add("a", [1.0, 0.0])
        store.add("b", [0.7, 0.7])
        store.add("c", [0.0, 1.0])
        hits = store.search([1.0, 0.0], 3)
        self.assertEqual([identifier for identifier, _ in hits],
                         ["a", "b", "c"])
        self.assertAlmostEqual(hits[0][1], 1.0, places=6)
        self.assertAlmostEqual(hits[2][1], 0.0, places=6)

    def test_ties_break_on_the_version_identifier(self):
        store = semantic.VectorStore(2, 4)
        for identifier in ("zz", "aa", "mm"):
            store.add(identifier, [1.0, 0.0])
        hits = store.search([1.0, 0.0], 3)
        self.assertEqual([identifier for identifier, _ in hits],
                         ["aa", "mm", "zz"])

    def test_search_only_returns_permitted_versions(self):
        store = semantic.VectorStore(2, 4)
        store.add("a", [1.0, 0.0])
        store.add("b", [1.0, 0.0])
        hits = store.search([1.0, 0.0], 5, {"b"})
        self.assertEqual([identifier for identifier, _ in hits], ["b"])


class ActiveVersionTest(unittest.TestCase):

    def test_every_active_version_is_its_own_entry(self):
        entries = [
            entry("dpl:1", "failed to allocate <NUM> bytes", NOW),
            entry("dpl:2", "failed to allocate <FLOAT> bytes", NOW - 10),
            entry("dpl:3", "reader <NUM> opened", NOW - 20),
        ]
        versions = semantic.active_versions(entries, NOW)
        self.assertEqual([v.version_id for v in versions],
                         ["dpl:1", "dpl:2", "dpl:3"])
        self.assertEqual(versions[0].template,
                         "failed to allocate <NUM> bytes")
        self.assertEqual(versions[0].last_observed, NOW)

    def test_inactive_entries_are_dropped(self):
        entries = [
            entry("dpl:1", "one", NOW),
            entry("dpl:2", "two", NOW - contract.ACTIVE_MS),
            entry("dpl:3", "three", NOW - contract.ACTIVE_MS + 1),
        ]
        versions = semantic.active_versions(entries, NOW)
        self.assertEqual([v.version_id for v in versions], ["dpl:1", "dpl:3"])

    def test_missing_or_invalid_fields_are_skipped(self):
        entries = [
            {"template": "one", "last_observed": NOW},
            {"version_id": "dpl:1", "last_observed": NOW},
            entry("dpl:2", "two", None),
            entry("dpl:3", "three", True),
            "not a dict",
        ]
        self.assertEqual(semantic.active_versions(entries, NOW), [])

    def test_order_is_freshest_first(self):
        entries = [
            entry("dpl:1", "one", NOW - 5000),
            entry("dpl:2", "two", NOW),
            entry("dpl:3", "three", NOW - 1000),
        ]
        versions = semantic.active_versions(entries, NOW)
        self.assertEqual([v.version_id for v in versions],
                         ["dpl:2", "dpl:3", "dpl:1"])


class RefreshTest(unittest.TestCase):

    def build(self, **kwargs):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(**kwargs),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        return search, encoder

    def test_a_disabled_server_never_loads_a_model(self):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(enabled=False),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        search.refresh([version("c1", "one")])
        self.assertEqual(search.status()["status"],
                         semantic.STATUS_UNAVAILABLE)
        self.assertEqual(search.status()["reason"], semantic.REASON_DISABLED)
        self.assertEqual(encoder.calls, [])
        self.assertFalse(search.start())

    def test_the_model_loads_only_when_a_refresh_needs_it(self):
        loaded = []

        def load(cfg):
            loaded.append(cfg)
            return WordEncoder()

        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=load,
                                         clock=lambda: NOW)
        self.assertEqual(loaded, [])
        search.refresh([])
        self.assertEqual(loaded, [])
        search.refresh([version("c1", "one")])
        self.assertEqual(len(loaded), 1)

    def test_each_version_is_embedded_once(self):
        search, encoder = self.build()
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(5)]
        search.refresh(versions)
        self.assertEqual(sum(encoder.calls), 5)
        search.refresh(versions)
        self.assertEqual(sum(encoder.calls), 5)
        self.assertEqual(search.status()["versions"], 5)

    def test_a_new_version_is_the_only_one_embedded_again(self):
        search, encoder = self.build()
        versions = [version("c1", "one"), version("c2", "two")]
        search.refresh(versions)
        encoder.texts = []
        search.refresh(versions + [version("c3", "three")])
        self.assertEqual(encoder.texts, ["three"])

    def test_the_template_text_is_what_is_encoded(self):
        search, encoder = self.build()
        search.refresh([version("c1", "Failed to allocate <NUM> bytes")])
        self.assertEqual(encoder.texts, ["Failed to allocate <NUM> bytes"])

    def test_expired_versions_lose_their_vector_in_the_same_refresh(self):
        search, _ = self.build()
        search.refresh([version("c1", "one"), version("c2", "two")])
        self.assertEqual(search.status()["versions"], 2)
        snapshot = search.refresh([version("c1", "one")])
        self.assertEqual(snapshot.versions, 1)
        self.assertEqual(snapshot.vector_bytes, 4 * 4)
        self.assertNotIn("c2", snapshot.embedded)

    def test_the_snapshot_is_replaced_and_not_mutated(self):
        search, _ = self.build()
        first = search.refresh([version("c1", "one")])
        second = search.refresh([version("c1", "one"), version("c2", "two")])
        self.assertIsNot(first, second)
        self.assertEqual(first.versions, 1)
        self.assertEqual(second.versions, 2)

    def test_complete_coverage_is_only_claimed_when_it_is_complete(self):
        search, _ = self.build()
        snapshot = search.refresh([version("c1", "one"), version("c2", "two")])
        self.assertEqual(snapshot.coverage, contract.COVERAGE_COMPLETE)
        self.assertFalse(snapshot.truncated)
        self.assertEqual(snapshot.reason, semantic.REASON_NONE)

    def test_the_version_limit_truncates_and_says_so(self):
        search, _ = self.build(max_versions=2)
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(5)]
        snapshot = search.refresh(versions)
        self.assertEqual(snapshot.status, semantic.STATUS_READY)
        self.assertEqual(snapshot.coverage, contract.COVERAGE_PARTIAL)
        self.assertTrue(snapshot.truncated)
        self.assertEqual(snapshot.versions, 2)
        self.assertEqual(snapshot.versions_total, 5)
        self.assertEqual(snapshot.reason, semantic.REASON_VERSION_LIMIT)
        self.assertEqual(sorted(snapshot.embedded), ["c0", "c1"])

    def test_the_vector_byte_limit_truncates_and_says_so(self):
        search, _ = self.build(max_versions=1000, max_vector_bytes=2 * 4 * 4)
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(4)]
        snapshot = search.refresh(versions)
        self.assertTrue(snapshot.truncated)
        self.assertEqual(snapshot.versions, 2)
        self.assertEqual(snapshot.vector_bytes, 2 * 4 * 4)
        self.assertLessEqual(snapshot.vector_bytes,
                             snapshot.vector_bytes_limit)
        self.assertEqual(snapshot.reason,
                         semantic.REASON_VECTOR_BYTES_LIMIT)

    def test_no_capacity_is_unavailable_and_not_empty_success(self):
        search, encoder = self.build(max_vector_bytes=0)
        snapshot = search.refresh([version("c1", "one")])
        self.assertEqual(snapshot.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(snapshot.coverage, contract.COVERAGE_UNAVAILABLE)
        self.assertEqual(snapshot.reason, semantic.REASON_NO_CAPACITY)
        self.assertEqual(encoder.calls, [])

    def test_a_failing_model_leaves_the_catalog_unavailable_not_complete(self):
        encoder = WordEncoder()
        encoder.fail_with = RuntimeError("no model here")
        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        snapshot = search.refresh([version("c1", "one")])
        self.assertEqual(snapshot.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(snapshot.coverage, contract.COVERAGE_UNAVAILABLE)
        self.assertNotEqual(snapshot.coverage, contract.COVERAGE_COMPLETE)

    def test_a_model_that_will_not_load_is_reported_not_raised(self):
        def load(cfg):
            raise semantic.EncoderUnavailable(semantic.REASON_MODEL_MISSING,
                                              "no such directory")

        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=load,
                                         clock=lambda: NOW)
        snapshot = search.refresh([version("c1", "one")])
        self.assertEqual(snapshot.reason, semantic.REASON_MODEL_MISSING)
        self.assertEqual(snapshot.status, semantic.STATUS_UNAVAILABLE)

    def test_a_partial_encode_never_reports_complete(self):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(batch_size=1),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        original = encoder.encode

        def once(texts):
            if len(encoder.calls) >= 1:
                raise RuntimeError("the encoder died")
            return original(texts)

        encoder.encode = once
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(3)]
        snapshot = search.refresh(versions)
        self.assertEqual(snapshot.versions, 1)
        self.assertEqual(snapshot.versions_total, 3)
        self.assertEqual(snapshot.coverage, contract.COVERAGE_PARTIAL)
        self.assertTrue(snapshot.truncated)

    def test_a_refresh_batches_at_the_configured_size(self):
        search, encoder = self.build(batch_size=2)
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(5)]
        search.refresh(versions)
        self.assertEqual(encoder.calls, [2, 2, 1])

    def test_a_dimension_mismatch_is_refused_at_load(self):
        cfg = config(dimensions=8)
        encoder = WordEncoder(dimensions=4)
        search = semantic.SemanticSearch(config=cfg,
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        snapshot = search.refresh([version("c1", "one")])
        self.assertEqual(snapshot.reason, semantic.REASON_MODEL_DIMENSIONS)
        self.assertEqual(snapshot.versions, 0)


class SearchTest(unittest.TestCase):

    def build(self, versions=None, **kwargs):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(**kwargs),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        if versions:
            search.refresh(versions)
        return search, encoder

    def test_search_before_a_refresh_is_unavailable(self):
        search, _ = self.build()
        result = search.search("anything")
        self.assertEqual(result.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_NO_SNAPSHOT)

    def test_the_nearest_version_comes_first(self):
        search, _ = self.build([
            version("c1", "aaa bb c"),
            version("c2", "zzzz yyyy xxxx wwww"),
        ])
        result = search.search("aaa bb c")
        self.assertEqual(result.status, semantic.STATUS_READY)
        self.assertEqual(result.hits[0].version_id, "c1")
        self.assertAlmostEqual(result.hits[0].score, 1.0, places=5)

    def test_the_result_limit_is_bounded_by_the_page_size(self):
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(20)]
        search, _ = self.build(versions, max_results=5)
        self.assertEqual(len(search.search("word1", limit=100).hits), 5)
        self.assertEqual(len(search.search("word1", limit=2).hits), 2)

    def test_an_empty_query_returns_nothing_and_says_why(self):
        search, _ = self.build([version("c1", "one")])
        result = search.search("   ")
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_EMPTY_QUERY)

    def test_a_long_query_is_cut_not_refused(self):
        search, encoder = self.build([version("c1", "one")],
                                     max_query_chars=10)
        search.search("x" * 50)
        self.assertEqual(len(encoder.texts[-1]), 10)

    def test_a_broken_encoder_never_raises_into_the_request(self):
        search, encoder = self.build([version("c1", "one")])
        encoder.fail_with = RuntimeError("the model went away")
        result = search.search("one")
        self.assertEqual(result.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_ENCODE_FAILED)

    def test_a_truncated_corpus_is_never_reported_as_complete(self):
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(6)]
        search, _ = self.build(versions, max_versions=3)
        result = search.search("word0")
        self.assertTrue(result.truncated)
        self.assertEqual(result.coverage, contract.COVERAGE_PARTIAL)
        self.assertEqual(result.versions, 3)
        self.assertEqual(result.versions_total, 6)
        self.assertEqual(semantic.result_summary(result)["coverage"],
                         contract.COVERAGE_PARTIAL)

    def test_results_carry_no_count_and_no_suppression(self):
        search, _ = self.build([version("c1", "one")])
        hit = search.search("one").hits[0]
        self.assertEqual(sorted(hit._fields), ["score", "version_id"])

    def test_scores_are_attached_without_touching_a_count(self):
        rows = [{"version_id": "c1", "count": 4211987,
                 "count_status": "exact"},
                {"version_id": "c2", "count": 5, "count_status": "exact"}]
        hits = [semantic.Hit("c1", 0.87)]
        semantic.apply_scores(rows, hits)
        self.assertEqual(rows[0]["score"], 0.87)
        self.assertIsNone(rows[1]["score"])
        self.assertEqual(rows[0]["count"], 4211987)
        self.assertEqual(rows[0]["count_status"], "exact")
        self.assertEqual(rows[1]["count"], 5)

    def test_a_search_during_a_refresh_waits_for_the_single_slot(self):
        encoder = WordEncoder()
        encoder.delay = 0.05
        search = semantic.SemanticSearch(
            config=config(batch_size=1, query_wait_seconds=5.0),
            encoder_factory=factory(encoder), clock=lambda: NOW)
        search.refresh([version("c1", "one")])
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(1, 8)] + [version("c1", "one")]
        results = []

        def refresh():
            search.refresh(versions)

        worker = threading.Thread(target=refresh)
        worker.start()
        time.sleep(0.02)
        results.append(search.search("one"))
        worker.join(10)
        self.assertEqual(results[0].status, semantic.STATUS_READY)
        self.assertEqual(results[0].hits[0].version_id, "c1")

    def test_a_busy_encoder_reports_itself_rather_than_blocking(self):
        encoder = WordEncoder()
        encoder.delay = 0.4
        search = semantic.SemanticSearch(
            config=config(batch_size=8, query_wait_seconds=0.01),
            encoder_factory=factory(encoder), clock=lambda: NOW)
        search.refresh([version("c1", "one")])
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(1, 9)] + [version("c1", "one")]
        worker = threading.Thread(target=lambda: search.refresh(versions))
        worker.start()
        time.sleep(0.05)
        result = search.search("one")
        worker.join(10)
        self.assertEqual(result.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(result.reason, semantic.REASON_ENCODER_BUSY)


class NeighbourTest(unittest.TestCase):

    def build(self, versions=None, **kwargs):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(**kwargs),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        if versions:
            search.refresh(versions)
        return search, encoder

    def corpus(self):
        return [
            version("c1", "aaa bb c"),
            version("c2", "aaa bb"),
            version("c3", "zzzz yyyy xxxx wwww"),
        ]

    def test_neighbours_before_a_refresh_are_unavailable(self):
        search, _ = self.build()
        result = search.neighbours("c1")
        self.assertEqual(result.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_NO_SNAPSHOT)

    def test_the_nearest_versions_come_back_without_the_selected_one(self):
        search, _ = self.build(self.corpus())
        result = search.neighbours("c1")
        self.assertEqual(result.status, semantic.STATUS_READY)
        self.assertEqual([hit.version_id for hit in result.hits],
                         ["c2", "c3"])
        self.assertGreater(result.hits[0].score, result.hits[1].score)

    def test_a_neighbour_carries_its_identifier_and_no_count(self):
        search, _ = self.build(self.corpus())
        hit = search.neighbours("c1").hits[0]
        self.assertEqual(hit.version_id, "c2")
        self.assertEqual(sorted(hit._fields), ["score", "version_id"])

    def test_an_unembedded_version_says_why_rather_than_guessing(self):
        search, _ = self.build(self.corpus())
        result = search.neighbours("c9")
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_NOT_EMBEDDED)
        self.assertIn("no vector",
                      semantic.REASON_TEXT[semantic.REASON_NOT_EMBEDDED])

    def test_reading_neighbours_never_calls_the_encoder(self):
        search, encoder = self.build(self.corpus())
        before = len(encoder.calls)
        search.neighbours("c1")
        self.assertEqual(len(encoder.calls), before)

    def test_the_neighbour_count_is_bounded(self):
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                    for i in range(20)]
        search, _ = self.build(versions)
        result = search.neighbours("c0", limit=100)
        self.assertEqual(len(result.hits), semantic.DEFAULT_MAX_NEIGHBOURS)
        self.assertEqual(len(search.neighbours("c0", limit=2).hits), 2)

    def test_a_disabled_server_offers_no_neighbour(self):
        search = semantic.DisabledSearch()
        result = search.neighbours("c1")
        self.assertEqual(result.status, semantic.STATUS_UNAVAILABLE)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.reason, semantic.REASON_DISABLED)


class BackgroundTaskTest(unittest.TestCase):

    def test_submit_builds_on_the_single_background_thread(self):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        self.assertTrue(search.start())
        self.assertFalse(search.start())
        try:
            self.assertTrue(search.submit([version("c1", "one"),
                                           version("c2", "two")]))
            deadline = time.time() + 10
            while time.time() < deadline:
                if search.status()["versions"] == 2:
                    break
                time.sleep(0.01)
        finally:
            search.stop()
        self.assertEqual(search.status()["versions"], 2)
        self.assertEqual(search.status()["status"], semantic.STATUS_READY)

    def test_a_disabled_server_starts_no_thread(self):
        search = semantic.SemanticSearch(config=config(enabled=False),
                                         encoder_factory=factory(WordEncoder()),
                                         clock=lambda: NOW)
        self.assertFalse(search.start())
        self.assertFalse(search.submit([version("c1", "one")]))
        search.stop()

    def test_a_second_refresh_does_not_run_beside_the_first(self):
        encoder = WordEncoder()
        encoder.delay = 0.2
        search = semantic.SemanticSearch(config=config(batch_size=1),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        versions = [version("c%d" % i, "word%d" % i, last_observed=NOW - i)
                  for i in range(4)]
        worker = threading.Thread(target=lambda: search.refresh(versions))
        worker.start()
        time.sleep(0.05)
        search.refresh([version("c9", "nine")])
        worker.join(10)
        self.assertNotIn("c9", search.snapshot().embedded)
        self.assertEqual(search.snapshot().versions, 4)


class IsolationTest(unittest.TestCase):

    def test_build_never_raises_when_the_configuration_is_broken(self):
        search = semantic.build(config="not a configuration")
        self.assertIsInstance(search, semantic.DisabledSearch)
        self.assertEqual(search.status()["status"],
                         semantic.STATUS_UNAVAILABLE)
        self.assertEqual(search.search("anything").hits, ())

    def test_the_disabled_stand_in_answers_every_call(self):
        search = semantic.DisabledSearch()
        self.assertFalse(search.start())
        self.assertFalse(search.submit([]))
        self.assertIsNone(search.refresh([]))
        self.assertIsNone(search.snapshot())
        search.stop()
        self.assertEqual(search.status()["reason"], semantic.REASON_DISABLED)

    def test_the_default_environment_builds_a_disabled_server(self):
        search = semantic.build(config=semantic.Config.from_environment({}))
        try:
            self.assertEqual(search.status()["status"],
                             semantic.STATUS_UNAVAILABLE)
            self.assertEqual(search.status()["reason"],
                             semantic.REASON_DISABLED)
        finally:
            search.stop()

    def test_unavailable_summary_uses_the_contract_vocabulary(self):
        block = semantic.unavailable_summary()
        self.assertEqual(block["coverage"], contract.COVERAGE_UNAVAILABLE)
        self.assertIn(block["status"], semantic.STATUSES)
        self.assertEqual(block["versions"], 0)
        self.assertEqual(block["vector_bytes"], 0)

    def test_the_summary_carries_the_contract_field_names(self):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        search.refresh([version("c1", "one")])
        block = search.status()
        for field in ("status", "versions", "vector_bytes", "model_revision"):
            self.assertIn(field, block)
        self.assertEqual(block["model_revision"], "test-revision")
        self.assertIn(block["status"], semantic.STATUSES)


class HistoryTest(unittest.TestCase):

    def test_history_is_never_semantic(self):
        self.assertEqual(semantic.search_modes("semantic", True),
                         ("semantic", "text"))
        self.assertEqual(semantic.search_modes("semantic", False),
                         ("semantic", None))
        self.assertEqual(semantic.search_modes("text", True),
                         ("text", "text"))

    def test_the_history_query_is_bounded_and_inactive_only(self):
        body = semantic.history_query("failed to allocate", NOW,
                                      page_size=500)
        self.assertEqual(body["size"], semantic.DEFAULT_MAX_RESULTS)
        filters = body["query"]["bool"]["filter"]
        self.assertIn({"term": {"kind": contract.KIND_CATALOG_TEMPLATE}},
                      filters)
        self.assertIn({"range": {"last_observed": {
            "gte": NOW - contract.DEFINITION_RETENTION_MS + 1}}}, filters)
        self.assertIn({"range": {"last_observed": {
            "lt": NOW - contract.ACTIVE_MS + 1}}}, filters)
        self.assertFalse(contract.is_retained_definition(
            NOW - contract.DEFINITION_RETENTION_MS, NOW))
        self.assertFalse(contract.is_active(NOW - contract.ACTIVE_MS, NOW))

    def test_the_history_query_searches_text_and_identifiers(self):
        body = semantic.history_query("failed to allocate", NOW)
        should = body["query"]["bool"]["should"]
        self.assertEqual(body["query"]["bool"]["minimum_should_match"], 1)
        self.assertIn({"term": {"version_id": "failed to allocate"}}, should)
        self.assertIn({"match_phrase": {"template": "failed to allocate"}},
                      should)

    def test_an_identifier_query_does_not_search_free_text(self):
        version = contract.version_id("dpl", "failed to allocate <NUM> bytes")
        body = semantic.history_query(version, NOW)
        should = body["query"]["bool"]["should"]
        self.assertEqual(should, [{"term": {"version_id": version}}])
        self.assertTrue(semantic.looks_like_identifier(version))
        self.assertFalse(semantic.looks_like_identifier("failed to allocate"))

    def test_an_empty_history_query_still_lists_bounded_history(self):
        body = semantic.history_query("", NOW)
        self.assertNotIn("should", body["query"]["bool"])
        self.assertEqual(body["sort"][0], {"last_observed": {"order": "desc"}})

    def test_history_paging_uses_search_after(self):
        body = semantic.history_query("", NOW, after=[123, "dpl:1"])
        self.assertEqual(body["search_after"], [123, "dpl:1"])
        self.assertFalse(body["track_total_hits"])

    def test_history_never_touches_the_vector_store(self):
        encoder = WordEncoder()
        search = semantic.SemanticSearch(config=config(),
                                         encoder_factory=factory(encoder),
                                         clock=lambda: NOW)
        search.refresh([version("c1", "one")])
        before = sum(encoder.calls)
        semantic.history_query("two", NOW)
        self.assertEqual(sum(encoder.calls), before)
        self.assertEqual(search.snapshot().versions, 1)


class MemoryTest(unittest.TestCase):

    def test_the_matrix_cost_is_stated_in_bytes(self):
        self.assertEqual(semantic.resident_estimate(0, 512), 0)
        matrix = 5571 * 512 * semantic.VECTOR_VALUE_BYTES
        self.assertEqual(matrix, 11409408)
        estimate = semantic.resident_estimate(5571, 512)
        self.assertGreater(estimate, matrix)
        self.assertLess(estimate, 384 * 1024 * 1024)

    def test_the_configured_ceiling_is_computable(self):
        cfg = semantic.Config(dimensions=512, max_versions=20000,
                              max_vector_bytes=16777216)
        self.assertEqual(cfg.vector_capacity, 8192)
        self.assertLess(cfg.resident_ceiling(), 384 * 1024 * 1024)


def measured(**kwargs):
    values = {
        "python": "3.9.18",
        "platform": "linux",
        "model_id": "a-candidate",
        "backend": "model2vec",
        "dimensions": 512,
        "model_loaded": True,
        "model_error": "",
        "baseline_rss_bytes": 12000000,
        "module_rss_bytes": 14000000,
        "model_rss_bytes": 180000000,
        "model_peak_rss_bytes": 200000000,
        "model_load_seconds": 0.3,
        "vector_matrix_bytes": 11409408,
        "corpus_versions": 5571,
        "snapshot_versions": 5571,
        "encode_seconds": 0.8,
        "encode_peak_rss_bytes": 210000000,
        "cold_start_seconds": 1.2,
        "query_p50_ms": 41.0,
        "query_p95_ms": 43.0,
        "query_samples": 100,
        "vector_capacity": 8192,
        "resident_ceiling_bytes": 22000000,
        "peak_rss_bytes": 210000000,
        "search_path": "exact dot product",
        "unmeasured": [],
    }
    values.update(kwargs)
    return values


class MeasurementTest(unittest.TestCase):

    def test_the_plan_numbers_are_the_ones_the_script_states(self):
        self.assertEqual(measure_serving.PLAN_VERSIONS, 5571)
        self.assertEqual(measure_serving.PLAN_DIMENSIONS, 512)
        self.assertEqual(measure_serving.PLAN_MATRIX_BYTES,
                         5571 * 512 * semantic.VECTOR_VALUE_BYTES)
        self.assertEqual(measure_serving.SERVICE_CEILING_BYTES,
                         384 * 1024 * 1024)

    def test_percentiles_use_nearest_rank(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        self.assertEqual(measure_serving.percentile(values, 0.50), 5.0)
        self.assertEqual(measure_serving.percentile(values, 0.95), 10.0)
        self.assertIsNone(measure_serving.percentile([], 0.5))

    def test_the_corpus_reader_takes_the_template_text(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                             delete=False)
        try:
            handle.write(json.dumps({"template": "one <NUM>"}) + "\n")
            handle.write("\n")
            handle.write(json.dumps({"normalized": "no template"}) + "\n")
            handle.write(json.dumps({"template": "one <NUM>"}) + "\n")
            handle.write(json.dumps({"template": "two"}) + "\n")
            handle.close()
            templates = measure_serving.read_corpus(handle.name)
            self.assertEqual(templates, ["one <NUM>", "two"])
            self.assertEqual(len(measure_serving.read_corpus(handle.name, 1)),
                             1)
        finally:
            os.unlink(handle.name)

    def test_queries_fall_back_and_fill_the_sample_count(self):
        queries = measure_serving.read_queries("/nonexistent/queries.txt", 25)
        self.assertEqual(len(queries), 25)
        self.assertEqual(queries[0], measure_serving.FALLBACK_QUERIES[0])

    def test_synthetic_vectors_are_deterministic(self):
        first = measure_serving.synthetic_vectors(3, 4)
        second = measure_serving.synthetic_vectors(3, 4)
        self.assertEqual(first, second)
        self.assertNotEqual(first, measure_serving.synthetic_vectors(3, 4, 1))

    def test_a_run_without_a_model_never_reads_as_a_pass(self):
        text = measure_serving.report(
            measured(model_loaded=False, model_error="no such directory",
                     model_rss_bytes=None, model_peak_rss_bytes=None,
                     encode_peak_rss_bytes=None, cold_start_seconds=None,
                     query_p50_ms=None, query_p95_ms=None, query_samples=0,
                     search_only_p50_ms=41.0, search_only_p95_ms=43.0,
                     search_only_note="synthetic vectors",
                     unmeasured=["cold start time to first query"]),
            {}, 2.0)
        self.assertIn("NOT measured", text)
        self.assertIn("Do not read the figures above as a pass", text)
        self.assertIn("no such directory", text)
        self.assertIn("Not measured: cold start time to first query", text)
        self.assertNotIn("IT FITS", text)

    def test_a_peak_above_the_ceiling_says_it_does_not_fit(self):
        text = measure_serving.report(
            measured(peak_rss_bytes=420000000), {}, 2.0)
        self.assertIn("IT DOES NOT FIT", text)
        self.assertNotIn("IT FITS", text)

    def test_a_peak_that_leaves_no_reserve_says_it_does_not_fit(self):
        peak = measure_serving.SERVICE_CEILING_BYTES - 1024
        text = measure_serving.report(measured(peak_rss_bytes=peak), {}, 2.0)
        self.assertIn("IT DOES NOT FIT", text)
        self.assertIn("the rest of the service needs more than that", text)

    def test_a_small_peak_says_it_fits(self):
        text = measure_serving.report(
            measured(peak_rss_bytes=100000000), {}, 2.0)
        self.assertIn("IT FITS", text)
        self.assertNotIn("IT DOES NOT FIT", text)

    def test_the_report_never_claims_the_deployment_host(self):
        text = measure_serving.report(measured(), {}, 2.0)
        self.assertIn("This machine is not the deployment host", text)
        self.assertIn("is a candidate, not a winner", text)
        self.assertIn("11,409,408", text)

    def test_an_unreadable_resident_size_is_not_a_verdict(self):
        text = measure_serving.report(measured(peak_rss_bytes=0), {}, 2.0)
        self.assertIn("not measured", text)
        self.assertNotIn("IT FITS", text)
        self.assertNotIn("IT DOES NOT FIT", text)


if __name__ == "__main__":
    unittest.main()
