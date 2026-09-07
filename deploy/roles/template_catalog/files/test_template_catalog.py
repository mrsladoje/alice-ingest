#!/usr/bin/env python3
"""Tests for the four properties the catalog rests on.

Run: python3 deploy/roles/template_catalog/files/test_template_catalog.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.environ.setdefault("ALICE_TEMPLATING_PATH",
                      os.path.join(REPO, "tools", "templating"))
sys.path.insert(0, HERE)

import template_catalog as tc  # noqa: E402


class Identifier(unittest.TestCase):
    """A template must land on the same document every run.

    The first version used Python's hash(), which is randomised per process, so
    the same template would have grown a new document every ten minutes instead
    of a count.
    """

    def test_stable_across_calls(self):
        a = tc.template_id("dpl", "failed to allocate <NUM> bytes")
        b = tc.template_id("dpl", "failed to allocate <NUM> bytes")
        self.assertEqual(a, b)

    def test_stable_across_processes(self):
        import subprocess
        code = (
            "import os,sys;"
            "os.environ.setdefault('ALICE_TEMPLATING_PATH',%r);"
            "sys.path.insert(0,%r);"
            "import template_catalog as t;"
            "print(t.template_id('dpl','failed to allocate <NUM> bytes'))"
            % (os.environ["ALICE_TEMPLATING_PATH"], HERE))
        seen = set()
        for _ in range(3):
            out = subprocess.run([sys.executable, "-c", code],
                                 capture_output=True, text=True)
            seen.add(out.stdout.strip())
        self.assertEqual(len(seen), 1, seen)
        self.assertIn(tc.template_id("dpl", "failed to allocate <NUM> bytes"),
                      seen)

    def test_family_is_part_of_the_identity(self):
        self.assertNotEqual(tc.template_id("dpl", "same text"),
                            tc.template_id("dds", "same text"))


class FormatFamily(unittest.TestCase):
    """The catalog must split the process tree the same way the offline miner
    does, or a template mined on a worker and the same template mined from the
    archive land under different recipes and different document identifiers.

    Every record here is in the shape the PRODUCTION collector indexes. That is
    the whole point of these tests: the first build split on the message text
    against the DataDistribution prefix pattern, and the collector's own parser
    has already eaten that prefix into `time` and `severity` before the record
    is written. The test that passed fed a message the collector never emits.
    """

    def test_datadist_is_told_apart_from_dpl_as_indexed(self):
        # What the datadist parser leaves behind: no prefix, no log_time, and a
        # single upper-case letter as severity.
        record = {"message": "(Sub)TimeFrame Sink created.", "severity": "I"}
        self.assertEqual(tc.family_of(record, "stdout"), "datadist")

    def test_a_dpl_line_stays_dpl_as_indexed(self):
        record = {"message": "Processing timeslice:196", "severity": "INFO",
                  "log_time": "12:20:34"}
        self.assertEqual(tc.family_of(record, "stdout"), "dpl")

    def test_a_dpl_line_with_a_one_letter_severity_is_still_dpl(self):
        # The DPL regex allows [A-Z]+, so one letter is legal there too. What
        # separates the two is log_time, and it is checked first.
        record = {"message": "x", "severity": "W", "log_time": "12:20:34"}
        self.assertEqual(tc.family_of(record, "stdout"), "dpl")

    def test_root_and_noclock_and_unclaimed_are_dpl(self):
        # refamily.py maps dpl_noclock and stdout_root onto dpl offline. This
        # has to agree with it or the two catalogs disagree on the family.
        for record in ({"message": "x", "severity": "WARN"},
                       {"message": "x", "severity": "Warning"},
                       {"message": "x"}):
            self.assertEqual(tc.family_of(record, "stdout"), "dpl", record)

    def test_the_split_agrees_with_refamily_on_real_shapes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "refamily", os.path.join(REPO, "tools", "templating", "refamily.py"))
        refamily = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(refamily)
        self.assertEqual(refamily.FAMILY_OF["dpl_noclock"], "dpl")
        self.assertEqual(refamily.FAMILY_OF["stdout_root"], "dpl")
        self.assertEqual(refamily.FAMILY_OF["datadist"], "datadist")

    def test_other_sources_pass_through(self):
        for source in ("dds", "infologger", "journald", "ildaemon"):
            self.assertEqual(tc.family_of({"message": "anything"}, source),
                             source)


class Position(unittest.TestCase):
    """A resume must be exact, not approximately right.

    The first build stored only collector_time and resumed with a strict
    greater-than. A worker writes many records inside one millisecond, so every
    record sharing the last millisecond of a pass was skipped for good. The
    position is now a full sort key and it lives in the local state file, with
    the mining trees and the pass number, so all of it becomes durable in one
    atomic replace.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp
        self.calls = []

    def tearDown(self):
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_state_round_trips(self):
        tc.save_state({"version": tc.STATE_VERSION, "pass": 4,
                       "position": {"idx": [1750000000123, "abc"]},
                       "published": {"dpl\t1": "dpl:deadbeef"}, "trees": {}})
        got = tc.load_state()
        self.assertEqual(got["pass"], 4)
        self.assertEqual(got["position"]["idx"], [1750000000123, "abc"])
        self.assertEqual(got["published"]["dpl\t1"], "dpl:deadbeef")

    def test_no_state_reads_from_the_beginning(self):
        state = tc.load_state()
        self.assertEqual(state["pass"], 0)
        self.assertEqual(state["position"], {})

    def test_the_state_file_is_replaced_atomically(self):
        """A half-written state file is a lost batch or a doubled one.

        The temporary file must not be left behind either, or the state
        directory fills with them.
        """
        tc.save_state({"version": tc.STATE_VERSION, "pass": 1, "position": {},
                       "published": {}, "trees": {}})
        tc.save_state({"version": tc.STATE_VERSION, "pass": 2, "position": {},
                       "published": {}, "trees": {}})
        left = [n for n in os.listdir(self.tmp) if n.startswith(".catalog")]
        self.assertEqual(left, [], "a temporary state file was left behind")
        self.assertEqual(tc.load_state()["pass"], 2)

    def test_a_corrupt_state_file_starts_over_rather_than_crashing(self):
        with open(tc.state_path(), "w") as fh:
            fh.write("{ this is not json")
        self.assertEqual(tc.load_state()["pass"], 0)

    def _shard(self, checkpoint=99, shards=1, index="idx-000001"):
        """One concrete index with a stated global checkpoint per shard."""
        return index, {
            "_settings": {index: {"settings": {"index.number_of_shards":
                                               str(shards),
                                               "index.uuid": "u-" + index}}},
            "_stats": {"indices": {index: {"shards": {
                str(n): [{"seq_no": {"max_seq_no": checkpoint + 5,
                                     "local_checkpoint": checkpoint,
                                     "global_checkpoint": checkpoint}}]
                for n in range(shards)}}}},
        }

    def _serve(self, hits_for=None, checkpoint=99, shards=1):
        """A stubbed cluster: settings, stats, refresh and search."""
        index, canned = self._shard(checkpoint, shards)

        def fake(path, body=None, method="GET"):
            self.calls.append((method, path, body))
            if "_settings" in path:
                return canned["_settings"]
            if "_stats" in path:
                return canned["_stats"]
            if path.endswith("/_refresh"):
                return {"_shards": {"total": 1}}
            return {"hits": {"hits": (hits_for(body) if hits_for else [])}}

        return fake

    def _scan_body(self, marks=None, node_scoped=False, checkpoint=99):
        """The query one scan issues, with the cluster stubbed out."""
        real = tc.request
        tc.request = self._serve(checkpoint=checkpoint)
        try:
            list(tc.scan("idx", marks or {}, node_scoped))
        finally:
            tc.request = real
        return [c for c in self.calls if "_search" in c[1]][-1]

    def test_the_scan_is_bounded_by_the_global_checkpoint(self):
        """max_seq_no is not a boundary; the checkpoint is.

        A sequence number is handed out before the write reaches Lucene, so
        with concurrent writes number 1 can be searchable while number 0 is
        still in flight. Reading 1 and remembering it steps over 0 for ever.
        """
        _, _, body = self._scan_body(checkpoint=1)
        ranges = [c["range"]["_seq_no"] for c in body["query"]["bool"]["filter"]
                  if "range" in c]
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["lte"], 1)

    def test_a_shard_still_settling_is_not_read_at_all(self):
        """The reviewer's reproduction, at the boundary where it is decided.

        max_seq_no 1, global checkpoint -1: sequence number 1 completed and is
        searchable, 0 has not. Reading 1 here would advance the position past
        0 and lose it. The pass must read nothing and remember nothing.
        """
        real = tc.request

        def hits(body):
            return [{"_source": {"message": "the one that completed"},
                     "_seq_no": 1}]

        tc.request = self._serve(hits_for=hits, checkpoint=-1)
        try:
            got = list(tc.scan("idx", {}, False))
        finally:
            tc.request = real
        self.assertEqual(got, [], "a record above the checkpoint was read")
        self.assertFalse([c for c in self.calls if "_search" in c[1]],
                         "a settling shard should not be searched at all")

    def test_the_gap_is_read_once_the_checkpoint_moves_past_it(self):
        """The same shard, one pass later: both operations have completed."""
        real = tc.request

        def hits(body):
            low = body["query"]["bool"]["filter"][0]["range"]["_seq_no"]
            rows = [{"_source": {"message": "zero"}, "_seq_no": 0},
                    {"_source": {"message": "one"}, "_seq_no": 1}]
            rows = [r for r in rows if r["_seq_no"] <= low["lte"]]
            if "gt" in low:
                rows = [r for r in rows if r["_seq_no"] > low["gt"]]
            return rows if self.calls.count(("POST", "x", None)) == 0 else rows

        tc.request = self._serve(hits_for=hits, checkpoint=1)
        try:
            got = [r["message"] for r, _ in tc.scan("idx", {}, False)]
        finally:
            tc.request = real
        self.assertEqual(got, ["zero", "one"])

    def test_the_checkpoint_is_read_before_the_refresh(self):
        """The order is the argument.

        Everything at or below the checkpoint had completed when it was read;
        the refresh after that makes all of it searchable. Refreshing first
        would let the checkpoint include operations the refresh did not cover.
        """
        real = tc.request
        tc.request = self._serve(checkpoint=5)
        try:
            list(tc.scan("idx", {}, False))
        finally:
            tc.request = real
        order = [p for _, p, _ in self.calls]
        stats = next(i for i, p in enumerate(order) if "_stats" in p)
        refresh = next(i for i, p in enumerate(order) if p.endswith("/_refresh"))
        search = next(i for i, p in enumerate(order) if "_search" in p)
        self.assertLess(stats, refresh, "the checkpoint was read after the refresh")
        self.assertLess(refresh, search, "the scan ran before the refresh")

    def test_the_lowest_reported_checkpoint_wins(self):
        """A replica may report a staler checkpoint than the primary, and the
        smaller number is the safe one."""
        real = tc.request

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"i-1": {"settings": {"index.number_of_shards": "1"}}}
            if "_stats" in path:
                return {"indices": {"i-1": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 9}},
                    {"seq_no": {"global_checkpoint": 4}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            self.calls.append((method, path, body))
            return {"hits": {"hits": []}}

        tc.request = fake
        try:
            list(tc.scan("i-1", {}, False))
        finally:
            tc.request = real
        body = self.calls[-1][2]
        self.assertEqual(
            body["query"]["bool"]["filter"][0]["range"]["_seq_no"]["lte"], 4)

    def test_the_scan_orders_by_shard_sequence_number(self):
        """No field of the record can order it.

        collector_time is when the collector saw the line. ingest_time is
        stamped when an ingest node RECEIVES the document -- processing,
        primary execution and replication all follow it, so a write held up
        behind a later one carries the earlier stamp. Only _seq_no is assigned
        as the write is executed, so only it is monotonic with indexing.
        """
        _, _, body = self._scan_body()
        self.assertEqual(body["sort"], [{"_seq_no": "asc"}])
        self.assertTrue(body["seq_no_primary_term"])
        self.assertNotIn("ingest_time", body["_source"])

    def test_the_scan_pins_each_query_to_one_shard(self):
        """_seq_no is per shard, so a cross-shard sort mixes unrelated
        sequences that all start at zero."""
        _, path, _ = self._scan_body()
        self.assertIn("preference=_shards:0", path)
        self.assertTrue(path.startswith("/idx-000001/_search"))

    def test_the_scan_resumes_after_the_recorded_sequence_number(self):
        _, _, body = self._scan_body(
            marks={"idx-000001/u-idx-000001": {"0": 41}}, checkpoint=99)
        bounds = [c["range"]["_seq_no"] for c in body["query"]["bool"]["filter"]
                  if "range" in c][0]
        self.assertEqual(bounds["gt"], 41)
        self.assertEqual(bounds["lte"], 99)

    def test_the_position_is_keyed_by_concrete_index_and_shard(self):
        """A rollover alias has several backing indices, and each of them has a
        shard 0 whose sequence numbers start at zero."""
        real = tc.request
        served = []

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"a-000001": {"settings": {"index.number_of_shards": "1",
                                                  "index.uuid": "uuid-one"}},
                        "a-000002": {"settings": {"index.number_of_shards": "1",
                                                  "index.uuid": "uuid-two"}}}
            if "_stats" in path:
                concrete = path.split("/")[1]
                return {"indices": {concrete: {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            if "a-000001" in path and not served:
                served.append(1)
                return {"hits": {"hits": [
                    {"_source": {"message": "x"}, "_seq_no": 7}]}}
            return {"hits": {"hits": []}}

        tc.request = fake
        try:
            got = [where for _, where in tc.scan("a", {}, False)]
        finally:
            tc.request = real
        self.assertEqual(got, [("a-000001/uuid-one", "0", 7)])

    def test_a_shared_index_is_read_scoped_to_this_node(self):
        _, _, body = self._scan_body(node_scoped=True)
        self.assertIn({"term": {"node": tc.NODE_ID}},
                      body["query"]["bool"]["filter"])

    def test_a_page_boundary_does_not_skip_anything(self):
        """Three records, a page of two. A sequence number is unique on its
        shard, so there is no tie for the resume to fall into."""
        records = [{"_source": {"message": "one"}, "_seq_no": 5},
                   {"_source": {"message": "two"}, "_seq_no": 6},
                   {"_source": {"message": "three"}, "_seq_no": 7}]
        real = tc.request

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"idx-000001": {"settings":
                                       {"index.number_of_shards": "1",
                                        "index.uuid": "u-idx"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            low = -1
            for clause in body["query"].get("bool", {}).get("filter", []):
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            return {"hits": {"hits": [r for r in records
                                      if r["_seq_no"] > low][:2]}}

        tc.request = fake
        original, page = tc.MAX_LINES, tc.PAGE
        tc.MAX_LINES = tc.PAGE = 2
        try:
            first = list(tc.scan("idx", {}, False))
            self.assertEqual([r["message"] for r, _ in first], ["one", "two"])
            concrete, shard, seq = first[-1][1]
            tc.MAX_LINES = tc.PAGE = 10
            second = list(tc.scan("idx", {concrete: {shard: seq}}, False))
        finally:
            tc.MAX_LINES, tc.PAGE = original, page
            tc.request = real
        self.assertEqual([r["message"] for r, _ in second], ["three"])

    def test_a_state_file_from_an_older_scan_key_is_discarded(self):
        """Version 1 stored a collector_time key and version 2 an ingest_time
        key. Resuming from either would land in the wrong place."""
        for older in (1, 2):
            tc.save_state({"version": older, "pass": 9,
                           "position": {"idx": {"0": 5}},
                           "published": {}, "trees": {}})
            state = tc.load_state()
            self.assertEqual(state["pass"], 0)
            self.assertEqual(state["position"], {})


class Completeness(unittest.TestCase):
    """A partial answer must never be read as an empty one.

    OpenSearch reports a timed-out search and a failed shard with 200 OK and
    whatever hits it managed to collect. An empty page from a shard that failed
    is byte-identical to an empty page from a shard with nothing left in it, so
    the only thing that separates `there is no more` from `I could not tell you`
    is the flag beside the hits. Reading the second as the first advances the
    read position over records that were never read.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp
        self.calls = []
        self.real = tc.request

    def tearDown(self):
        tc.request = self.real
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cluster(self, refresh, search):
        def fake(path, body=None, method="GET"):
            self.calls.append((method, path))
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1", "index.uuid": "u"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return refresh
            return search
        return fake

    WHOLE = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
    HITS = [{"_source": {"message": "a record that exists"}, "_seq_no": 4}]

    def test_a_refresh_that_did_not_reach_every_shard_stops_the_pass(self):
        """A shard that did not refresh may hold completed operations below the
        checkpoint that are still not searchable. Reading now steps over them."""
        tc.request = self._cluster(
            {"_shards": {"total": 2, "successful": 1, "failed": 1}},
            {"hits": {"hits": self.HITS}, **self.WHOLE})
        self.assertEqual(list(tc.scan("idx", {}, False)), [])
        self.assertFalse([c for c in self.calls if "_search" in c[1]],
                         "an index that did not refresh was searched anyway")

    def test_a_search_that_timed_out_is_not_the_end_of_the_shard(self):
        tc.request = self._cluster(
            self.WHOLE, {"hits": {"hits": []}, "timed_out": True,
                         "_shards": {"total": 1, "successful": 1, "failed": 0}})
        self.assertEqual(list(tc.scan("idx", {}, False)), [])

    def test_a_search_whose_shard_failed_is_not_the_end_of_the_shard(self):
        tc.request = self._cluster(
            self.WHOLE, {"hits": {"hits": []}, "timed_out": False,
                         "_shards": {"total": 1, "successful": 0, "failed": 1}})
        self.assertEqual(list(tc.scan("idx", {}, False)), [])

    def test_a_whole_answer_is_read(self):
        """The control: the same shape, with nothing wrong reported."""
        served = []

        def once(path, body=None, method="GET"):
            self.calls.append((method, path))
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1", "index.uuid": "u"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return dict(self.WHOLE)
            hits = [] if served else list(self.HITS)
            served.append(1)
            return {"hits": {"hits": hits}, "timed_out": False, **self.WHOLE}

        tc.request = once
        got = list(tc.scan("idx", {}, False))
        self.assertEqual([r["message"] for r, _ in got],
                         ["a record that exists"])

    def test_a_partial_catalog_listing_refuses_to_clear_anything(self):
        """Stricter here than in the scan, and it has to be.

        A partial answer during a scan costs a delay. A partial answer while
        listing this node's own documents means some of them were not listed,
        so they are not cleared, and the rebuild that follows lands on top of
        counts that were never removed.
        """
        def fake(path, body=None, method="GET"):
            if path.endswith("/_refresh"):
                return {"_shards": {"total": 1, "successful": 1, "failed": 0}}
            return {"hits": {"hits": []}, "timed_out": True,
                    "_shards": {"total": 2, "successful": 1, "failed": 1}}

        tc.request = fake
        with self.assertRaises(SystemExit):
            tc.clear_this_node(0)


class Identity(unittest.TestCase):
    """A name is not an index. Deleting one and recreating it under the same
    name gives a new index whose sequence numbers start again at zero."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp
        self.real = tc.request

    def tearDown(self):
        tc.request = self.real
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _source(self, uuid, hits):
        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1", "index.uuid": uuid}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            low = -1
            for clause in body["query"]["bool"]["filter"]:
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            return {"hits": {"hits": [h for h in hits if h["_seq_no"] > low]}}
        return fake

    def test_a_recreated_source_index_is_read_from_the_beginning(self):
        """The position said shard 0 was read to 40. The index behind that name
        is now a different index whose records are numbered from zero, so every
        one of them is below the mark and would be skipped for good."""
        tc.request = self._source("first", [
            {"_source": {"message": "old"}, "_seq_no": 40}])
        first = list(tc.scan("idx", {}, False))
        marks = {}
        for _, (key, shard, seq) in first:
            marks.setdefault(key, {})[shard] = seq
        self.assertEqual(list(marks.values()), [{"0": 40}])

        tc.request = self._source("second", [
            {"_source": {"message": "new"}, "_seq_no": 0}])
        again = list(tc.scan("idx", marks, False))
        self.assertEqual([r["message"] for r, _ in again], ["new"],
                         "a recreated index was resumed from the old mark")

    def test_an_index_replaced_after_its_identity_was_read_is_not_marked(self):
        """Everything after the first request addresses the index by NAME.

        Replace the index between the identity read and the search and the new
        index answers to the old name. Its records are numbered from zero, they
        mean nothing against the checkpoint taken from the index before it, and
        the position is saved under the key of the index they did not come
        from. The next pass keys the position by the new UUID, finds no mark,
        and reads every one of them a second time: one source record, a
        published count of two.
        """
        live = {"uuid": "first"}
        records = [{"_source": {"message": "only once"}, "_seq_no": 0}]

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1",
                    "index.uuid": live["uuid"]}}}
            if "_stats" in path:
                # Between reading the identity and reading anything else,
                # the index is deleted and recreated under the same name.
                live["uuid"] = "second"
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 99}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            low = -1
            for clause in body["query"].get("bool", {}).get("filter", []):
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            return {"hits": {"hits": [r for r in records
                                      if r["_seq_no"] > low]}}

        tc.request = fake
        marks = {}
        seen = []
        for _ in range(2):
            for record, (key, shard, seq) in tc.scan("idx", marks, False):
                marks.setdefault(key, {})[shard] = seq
                seen.append(record["message"])
        self.assertEqual(seen, ["only once"],
                         "one source record was read %d times, so the count "
                         "published for its template would be %d"
                         % (len(seen), len(seen)))
        self.assertEqual(list(marks), ["idx-000001/second"],
                         "the position was saved under the index the records "
                         "did not come from")

    def test_the_same_index_still_resumes(self):
        """The control: the UUID has not changed, so neither has the mark."""
        tc.request = self._source("first", [
            {"_source": {"message": "old"}, "_seq_no": 40}])
        marks = {}
        for _, (key, shard, seq) in tc.scan("idx", {}, False):
            marks.setdefault(key, {})[shard] = seq
        self.assertEqual(list(tc.scan("idx", marks, False)), [])

    def _served(self, uuid):
        def fake(path, body=None, method="GET"):
            if path.startswith("/template-catalog/_settings"):
                return {"template-catalog": {"settings": {"index.uuid": uuid}}}
            return {}
        return fake

    def test_a_recreated_catalog_clears_what_was_published(self):
        """A present name is not enough. Everything this node published is gone,
        but the state still says it was published, so nothing is republished --
        and the source position has not moved, so nothing new is read either.
        The node then reports itself idle against an empty catalog for ever."""
        tc.save_state({"version": tc.STATE_VERSION, "pass": 7,
                       "position": {"idx/u": {"0": 4}}, "published": {"k": "v"},
                       "trees": {}, "pending": None, "catalog": "was-this-one"})
        tc.request = self._served("is-now-this-one")
        tc.run([], dry_run=False)
        state = tc.load_state()
        self.assertEqual(state["published"], {})
        self.assertEqual(state["catalog"], "is-now-this-one")

    def test_a_recreated_catalog_keeps_the_history_it_can_still_republish(self):
        """A rebuild, not a reset.

        Throwing the local state away sends the node back to the source
        indices, and those are the short-lived half of this system:
        informational records age out of the node-local index long before the
        templates mined from them stop being true. The trees hold every count
        this node has published, so they are what the rebuild republishes from.
        """
        import drainbench
        drainbench.install_merged_create_template()
        handler = tc._Buffered(None)
        miner = tc.miner_for("dpl", handler)
        miner.add_log_message("reader <NUM> opened channel alpha")
        trees = tc.dump_trees({"dpl": handler}, {"dpl": miner})
        key = "dpl\t%s" % list(miner.drain.id_to_cluster)[0]

        tc.save_state({"version": tc.STATE_VERSION, "pass": 7,
                       "position": {"idx/u": {"0": 4}}, "published": {"k": "v"},
                       "trees": trees, "programs": {key: ["TfBuilder"]},
                       "pending": None, "catalog": "was-this-one"})
        sent = []
        real_publish = tc.publish
        tc.publish = lambda payload: sent.append(payload) or True
        tc.request = self._served("is-now-this-one")
        try:
            tc.run([], dry_run=False)
        finally:
            tc.publish = real_publish
        state = tc.load_state()
        self.assertEqual(state["position"], {"idx/u": {"0": 4}},
                         "the read position was thrown away with the catalog")
        self.assertEqual(state["trees"], trees,
                         "the mining trees were thrown away with the catalog")
        self.assertEqual(state["programs"], {key: ["TfBuilder"]})

        # And it republished, rather than reporting itself idle against an
        # empty catalog: the count sent is the cluster's whole size in the
        # tree, which is history the source index may no longer hold.
        self.assertTrue(sent, "a replaced catalog was never rebuilt")
        upserts = [json.loads(b) for b in sent[0][1::2]]
        self.assertEqual([u["upsert"]["count"] for u in upserts], [1])
        self.assertEqual([u["upsert"]["programs"] for u in upserts],
                         [["TfBuilder"]])

    def _catalog(self, answers, created=None, put_fails=None):
        """The catalog lookup answers `answers` in order; None means absent."""
        remaining = list(answers)

        def fake(path, body=None, method="GET"):
            if method == "PUT" and path == "/template-catalog":
                if created is not None:
                    created.append(path)
                if put_fails is not None:
                    raise urllib.error.HTTPError(path, put_fails, "no", None,
                                                 None)
                return {"acknowledged": True}
            if path.startswith("/template-catalog/_settings"):
                uuid = remaining.pop(0) if remaining else None
                if uuid is None:
                    raise urllib.error.HTTPError(path, 404, "missing", None,
                                                 None)
                return {"template-catalog": {"settings": {"index.uuid": uuid}}}
            return {}
        return fake

    def test_an_absent_catalog_is_created_rather_than_left_to_the_write(self):
        """Letting the bulk write create the index is what made the identity
        unknowable until afterwards, and a lookup after the write cannot say
        which index received it. Creating it here is what makes the question
        answerable before anything is sent."""
        created = []
        tc.request = self._catalog([None, "made-here"], created=created)
        self.assertEqual(tc.establish(None), "made-here")
        self.assertEqual(created, ["/template-catalog"])

    def test_an_existing_catalog_is_not_created_again(self):
        created = []
        tc.request = self._catalog(["already-there"], created=created)
        self.assertEqual(tc.establish(None), "already-there")
        self.assertEqual(created, [])

    def test_another_node_winning_the_creation_race_is_not_an_error(self):
        """Its index is this node's index."""
        tc.request = self._catalog([None, "theirs"], put_fails=400)
        self.assertEqual(tc.establish(None), "theirs")

    def test_a_catalog_replaced_before_the_write_stops_the_publication(self):
        """The pass was built against one catalog and a different one is
        answering now. This batch is only what the pass READ; a replacement
        needs everything the trees hold, which is the next pass's job."""
        tc.request = self._catalog(["replacement"])
        self.assertIsNone(tc.establish("what-the-pass-was-built-against"))

    def test_an_unchanged_catalog_is_the_destination(self):
        tc.request = self._catalog(["same"])
        self.assertEqual(tc.establish("same"), "same")

    def test_an_identity_that_cannot_be_established_stops_the_run(self):
        """Created and still not readable. Nothing is published, because an
        identity this node cannot read must not be recorded as `no change`."""
        tc.request = self._catalog([None, None])
        self.assertIsNone(tc.establish(None))

    def test_commit_records_no_identity_of_its_own(self):
        """It used to look one up, and the lookup ran AFTER the write. A
        catalog replaced in between made that lookup succeed and name the empty
        replacement, which the node then recorded as the index it wrote to."""
        looked = []

        def fake(path, body=None, method="GET"):
            looked.append((method, path))
            return {}

        state = tc.fresh_state()
        state["catalog"] = None
        tc.request = fake
        tc.commit(state, {"pass": 5, "position": {}, "trees": {},
                          "touched": [], "programs": {}, "lines": 1}, {})
        self.assertEqual(looked, [])
        self.assertIsNone(state["catalog"])

    def test_a_recorded_catalog_is_not_looked_up_again(self):
        looked = []

        def fake(path, body=None, method="GET"):
            looked.append(path)
            return {}

        state = tc.fresh_state()
        state["catalog"] = "already-known"
        tc.request = fake
        tc.commit(state, {"pass": 1, "position": {}, "trees": {},
                          "touched": [], "programs": {}, "lines": 0}, {})
        self.assertEqual(looked, [])

    def test_an_unchanged_catalog_leaves_the_state_alone(self):
        tc.save_state({"version": tc.STATE_VERSION, "pass": 7,
                       "position": {"idx/u": {"0": 4}}, "published": {"k": "v"},
                       "trees": {}, "pending": None, "catalog": "same"})
        tc.request = lambda path, body=None, method="GET": (
            {"template-catalog": {"settings": {"index.uuid": "same"}}}
            if path.startswith("/template-catalog/_settings") else {})
        tc.run([], dry_run=False)
        self.assertEqual(tc.load_state()["position"], {"idx/u": {"0": 4}})


class PassNumbers(unittest.TestCase):
    """A pass number that goes backwards fences the node out of its own counts.

    Every update carries a guard that rejects a pass at or below the one already
    recorded for the node. A clock stepped backwards by NTP, or a virtual
    machine restored from a snapshot, produces exactly that: the records are
    read, the position is committed, every update takes the `noop` branch, and
    the run reports success while no count moves.
    """

    def test_the_clock_is_a_floor_and_never_the_whole_answer(self):
        now = int(time.time() * 1000)
        self.assertGreater(tc.next_pass(0), 0)
        self.assertGreaterEqual(tc.next_pass(0), now)
        rolled_back = now + 60000
        self.assertGreater(tc.next_pass(rolled_back), rolled_back,
                           "a pass below one already written would be ignored")

    def test_a_clear_starts_above_every_pass_the_catalog_records(self):
        """After a state loss the floor cannot come from local state, because
        there is none. It comes from the documents themselves."""
        real, sent = tc.request, []
        served = []

        def fake(path, body=None, method="GET"):
            if path.endswith("/_refresh"):
                return {"_shards": {"total": 1, "successful": 1, "failed": 0}}
            if served:
                return {"hits": {"hits": []},
                        "_shards": {"total": 1, "successful": 1, "failed": 0}}
            served.append(1)
            return {"hits": {"hits": [
                {"_id": "dpl:aaa", "sort": ["dpl:aaa"],
                 "_source": {"last_pass": {tc.NODE_ID: 99999999999999}}}]},
                "_shards": {"total": 1, "successful": 1, "failed": 0}}

        real_publish = tc.publish
        tc.request = fake
        tc.publish = lambda payload: sent.append(payload) or True
        try:
            count, chosen = tc.clear_this_node(0)
        finally:
            tc.request, tc.publish = real, real_publish
        self.assertEqual(count, 1)
        self.assertGreater(chosen, 99999999999999)
        self.assertEqual(json.loads(sent[0][1])["script"]["params"]["pass"],
                         chosen)


class Durability(unittest.TestCase):
    """The rename is the durable act, and a rename is a directory write."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp

    def tearDown(self):
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_directory_is_synchronised_after_the_rename(self):
        """Syncing only the file puts the bytes on the platter and leaves the
        rename that publishes them in write-back cache. A power cut between the
        two restores the OLD state file, and the batch it describes is read and
        counted a second time."""
        synced = []
        real = os.fsync

        def watch(fd):
            try:
                synced.append(os.fstat(fd).st_ino)
            except OSError:
                pass
            return real(fd)

        os.fsync = watch
        try:
            tc.save_state({"version": tc.STATE_VERSION, "pass": 1,
                           "position": {}, "published": {}, "trees": {}})
        finally:
            os.fsync = real
        self.assertIn(os.stat(self.tmp).st_ino, synced,
                      "the state directory was never synchronised")


class Growth(unittest.TestCase):
    """Two costs the mining tree had no bound on."""

    def setUp(self):
        import drainbench
        drainbench.install_merged_create_template()
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp

    def tearDown(self):
        import drainbench
        from drain3.drain import Drain
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE

    def test_the_tree_is_serialised_once_a_batch_and_not_once_a_record(self):
        """drain3 reads a persistence handler as an instruction to snapshot, and
        its rule fires on every message: a new cluster or a changed template
        snapshots by definition, and the periodic rule compares the seconds
        since the last save against an interval of zero, which is always true.

        Serialising the tree is the largest allocation this process makes --
        three times the size of the tree itself -- so paying it per record is
        both the slowest and the least bounded thing here."""
        handler = tc._Buffered(None)
        miner = tc.miner_for("dpl", handler)
        saves = []
        handler.save_state = lambda state: saves.append(len(state))
        for n in range(25):
            miner.add_log_message("reader <NUM> opened channel %d" % n)
        self.assertEqual(saves, [], "the tree was serialised while mining")
        tc.dump_trees({"dpl": handler}, {"dpl": miner})
        self.assertEqual(len(saves), 1, "the batch boundary must save once")

    def test_the_tree_is_still_loaded_from_the_handler(self):
        """Detaching the handler must not stop the tree being read back."""
        import base64
        handler = tc._Buffered(None)
        miner = tc.miner_for("dpl", handler)
        miner.add_log_message("reader <NUM> opened channel alpha")
        trees = tc.dump_trees({"dpl": handler}, {"dpl": miner})
        _, again = tc.miners_from(trees)
        self.assertEqual(len(again["dpl"].drain.clusters), 1)

    def test_at_the_ceiling_the_tree_stops_learning_and_keeps_counting(self):
        """drain3's own limit is an LRU that EVICTS, and eviction is not
        available here: the count published for a template is the cluster's
        absolute size in this tree, so an evicted cluster later rebuilt would
        report a size of one and SET the catalog count back to one."""
        handler = tc._Buffered(None)
        miner = tc.miner_for("dpl", handler)
        miner.add_log_message("reader <NUM> opened channel alpha")
        cluster = list(miner.drain.clusters)[0]
        self.assertEqual(cluster.size, 1)

        matched = miner.drain.match("reader <NUM> opened channel alpha")
        self.assertIsNotNone(matched)
        matched.size += 1
        self.assertEqual(cluster.size, 2)
        self.assertEqual(len(miner.drain.clusters), 1)

        self.assertIsNone(
            miner.drain.match("an utterly different shape of line entirely"),
            "matching must not create a cluster")
        self.assertEqual(len(miner.drain.clusters), 1)

    def test_the_ceiling_is_set_from_the_measured_cost_of_a_cluster(self):
        """20,000 clusters serialise at a 208 MB peak against the unit's 512 MB;
        50,000 peak at 437 MB. The whole 55,963,050-line archive pass mined
        4,221 templates, so this is 4.7 times the measured need."""
        self.assertEqual(tc.MAX_TEMPLATES, 20000)


class Provenance(unittest.TestCase):
    """Which programs write a template is a fact about the CLUSTER, not about
    the ten minutes in which it was last read."""

    def setUp(self):
        import drainbench
        drainbench.install_merged_create_template()
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp
        self.real_request, self.real_publish = tc.request, tc.publish
        self.sent = []
        tc.publish = lambda payload: self.sent.append(payload) or True

    def tearDown(self):
        import drainbench
        from drain3.drain import Drain
        tc.request, tc.publish = self.real_request, self.real_publish
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE

    def _serve(self, records, first_seq):
        """One shard holding `records`, numbered from `first_seq`."""
        def fake(path, body=None, method="GET"):
            if path.startswith("/template-catalog/_settings"):
                return {"template-catalog": {"settings":
                                             {"index.uuid": "catalog"}}}
            if path.startswith("/template-catalog/"):
                return {"hits": {"hits": []},
                        "_shards": {"total": 1, "successful": 1, "failed": 0}}
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1", "index.uuid": "u"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 10 ** 6}}]}}}}
            if path.endswith("/_refresh"):
                return {}
            low = -1
            for clause in body["query"]["bool"]["filter"]:
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            hits = [{"_source": r, "_seq_no": first_seq + n}
                    for n, r in enumerate(records)
                    if first_seq + n > low]
            return {"hits": {"hits": hits}}
        return fake

    def _upserts(self):
        """The document each bulk action creates, keyed by identifier."""
        out = {}
        for payload in self.sent:
            for action, body in zip(payload[0::2], payload[1::2]):
                doc_id = json.loads(action)["update"]["_id"]
                out[doc_id] = json.loads(body)
        return out

    def test_a_generalised_template_keeps_the_programs_it_had_before(self):
        """The count moves to a new document when drain rewrites the text, and
        the programs have to move with it. Sending only what THIS batch saw
        names the new document after whichever program happened to write in the
        ten minutes the move fell in, and loses every earlier one."""
        tc.request = self._serve([{"message": "reader opened channel alpha",
                                   "log_source": "stdout",
                                   "program": "epn-reader"}], 0)
        self.assertEqual(tc.run([("idx", False)]), 0)

        tc.request = self._serve([{"message": "reader opened channel beta",
                                   "log_source": "stdout",
                                   "program": "epn-writer"}], 1)
        self.assertEqual(tc.run([("idx", False)]), 0)

        upserts = self._upserts()
        current = [u for u in upserts.values()
                   if u.get("upsert", {}).get("template")
                   and "<*>" in u["upsert"]["template"]]
        self.assertTrue(current, "the template never generalised")
        for doc in current:
            self.assertEqual(sorted(doc["upsert"]["programs"]),
                             ["epn-reader", "epn-writer"])
            self.assertEqual(
                sorted(doc["script"]["params"]["programs"]),
                ["epn-reader", "epn-writer"],
                "the update carried only this batch's programs")

    def test_the_accumulated_programs_survive_in_the_state_file(self):
        tc.request = self._serve([{"message": "reader opened channel alpha",
                                   "log_source": "stdout",
                                   "program": "epn-reader"}], 0)
        tc.run([("idx", False)])
        stored = tc.load_state()["programs"]
        self.assertEqual(sorted(stored.values()), [["epn-reader"]])


class PartialAnswers(unittest.TestCase):
    """An unavailable copy is not a failed operation, and the difference decides
    whether the catalog reads anything at all."""

    def test_an_unassigned_replica_does_not_block_a_refresh(self):
        """One node and `number_of_replicas: 2` is the shipped worker layout,
        and every rolling restart produces an unassigned replica for a while. A
        refresh counts configured COPIES, so a completely healthy refresh there
        reports two total, one successful and zero failed. Reading that as a
        fault skipped the index every pass, for as long as the replica stayed
        unassigned, while the primary held records nobody was reading."""
        self.assertTrue(tc.refresh_is_complete(
            {"_shards": {"total": 2, "successful": 1, "failed": 0}}))

    def test_a_failed_refresh_is_still_a_failed_refresh(self):
        self.assertFalse(tc.refresh_is_complete(
            {"_shards": {"total": 2, "successful": 1, "failed": 1}}))

    def test_a_search_counts_shards_and_must_account_for_all_of_them(self):
        """Unlike a refresh, a search's `total` counts shards, not copies, so
        the arithmetic is meaningful: every shard is successful, skipped or
        failed."""
        self.assertTrue(tc.search_is_complete(
            {"_shards": {"total": 2, "successful": 1, "skipped": 1,
                         "failed": 0}}))
        self.assertFalse(tc.search_is_complete(
            {"_shards": {"total": 2, "successful": 1, "skipped": 0,
                         "failed": 0}}))
        self.assertFalse(tc.search_is_complete(
            {"timed_out": True,
             "_shards": {"total": 1, "successful": 1, "skipped": 0,
                         "failed": 0}}))

    def test_the_scan_reads_a_primary_whose_replica_is_unassigned(self):
        real = tc.request

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": "1", "index.uuid": "u"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {"0": [
                    {"seq_no": {"global_checkpoint": 9}}]}}}}
            if path.endswith("/_refresh"):
                # Two configured copies, one of them unassigned.
                return {"_shards": {"total": 2, "successful": 1, "failed": 0}}
            low = -1
            for clause in body["query"]["bool"]["filter"]:
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            hits = [{"_source": {"message": "on the primary"}, "_seq_no": 0}
                    ] if low < 0 else []
            return {"hits": {"hits": hits},
                    "_shards": {"total": 1, "successful": 1, "skipped": 0,
                                "failed": 0}}

        tc.request = fake
        try:
            got = list(tc.scan("idx", {}, False))
        finally:
            tc.request = real
        self.assertEqual([r["message"] for r, _ in got], ["on the primary"])


class ClearDurability(unittest.TestCase):
    """The clear removes this node from `nodes`, so once it has run the listing
    that found those documents can no longer find them. A crash between the
    clear and the commit therefore leaves a node that cannot rediscover the pass
    it just used -- and the guard the clear itself wrote then rejects everything
    the rebuild sends."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp
        self.real_request, self.real_publish = tc.request, tc.publish

    def tearDown(self):
        tc.request, tc.publish = self.real_request, self.real_publish
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _catalog_holding(self, last_pass):
        served = []

        def fake(path, body=None, method="GET"):
            if path.endswith("/_refresh"):
                return {"_shards": {"total": 1, "successful": 1, "failed": 0}}
            whole = {"_shards": {"total": 1, "successful": 1, "skipped": 0,
                                 "failed": 0}}
            if served:
                return dict({"hits": {"hits": []}}, **whole)
            served.append(1)
            return dict({"hits": {"hits": [
                {"_id": "dpl:aaa", "sort": ["dpl:aaa"],
                 "_source": {"last_pass": {tc.NODE_ID: last_pass}}}]}}, **whole)
        return fake

    def test_the_pass_a_clear_will_use_is_written_down_first(self):
        tc.request = self._catalog_holding(99999999999999)
        tc.publish = lambda payload: True
        state = tc.fresh_state()
        _, chosen = tc.clear_this_node(0, remember=lambda n: tc.issue(state, n))
        self.assertGreater(chosen, 99999999999999)
        self.assertEqual(tc.load_state()["issued"], chosen,
                         "the clear's pass number was never made durable")

    def test_a_crash_after_the_clear_does_not_lower_the_next_pass(self):
        """The state on disk after the crash is what the next run starts from."""
        tc.request = self._catalog_holding(99999999999999)
        tc.publish = lambda payload: True
        state = tc.fresh_state()
        _, chosen = tc.clear_this_node(0, remember=lambda n: tc.issue(state, n))
        # The crash: nothing else was committed. `pass` is still zero.
        after = tc.load_state()
        self.assertEqual(after.get("pass", 0), 0)
        floor = max(int(after.get("pass") or 0), int(after.get("issued") or 0))
        self.assertGreater(tc.next_pass(floor), chosen,
                           "the rebuild would be fenced out by the clear's own "
                           "guard")


class Fairness(unittest.TestCase):
    """One shared budget starting at shard zero starves every shard behind a
    busy one, for as long as it stays busy."""

    def _cluster(self, shards, per_shard):
        seen = {}

        def fake(path, body=None, method="GET"):
            if "_settings" in path:
                return {"idx-000001": {"settings": {
                    "index.number_of_shards": str(shards),
                    "index.uuid": "u"}}}
            if "_stats" in path:
                return {"indices": {"idx-000001": {"shards": {
                    str(n): [{"seq_no": {"global_checkpoint": 10 ** 6}}]
                    for n in range(shards)}}}}
            if path.endswith("/_refresh"):
                return {}
            shard = int(path.split("_shards:")[1])
            low = -1
            for clause in body["query"]["bool"]["filter"]:
                if "range" in clause:
                    low = clause["range"]["_seq_no"].get("gt", -1)
            start = int(low) + 1
            got = [{"_source": {"message": "shard %d record %d" % (shard, n)},
                    "_seq_no": n}
                   for n in range(start, min(start + body["size"], per_shard))]
            seen.setdefault(shard, 0)
            seen[shard] += len(got)
            return {"hits": {"hits": got}}

        return fake, seen

    def test_a_busy_shard_does_not_consume_the_other_shards_budget(self):
        real = tc.request
        fake, seen = self._cluster(shards=2, per_shard=10 ** 6)
        tc.request = fake
        limits = tc.MAX_LINES, tc.PAGE
        tc.MAX_LINES, tc.PAGE = 40, 10
        try:
            list(tc.scan("idx", {}, False))
        finally:
            tc.MAX_LINES, tc.PAGE = limits
            tc.request = real
        self.assertEqual(sorted(seen), [0, 1],
                         "a shard was never read at all")
        self.assertEqual(seen[0], seen[1])

    def test_the_starting_shard_rotates_between_passes(self):
        real = tc.request
        first_shard = []

        def watch(path, body=None, method="GET"):
            if "_search" in path and not first_shard:
                first_shard.append(int(path.split("_shards:")[1]))
            return fake(path, body, method)

        fake, _ = self._cluster(shards=3, per_shard=1)
        tc.request = watch
        try:
            for rotation in range(3):
                first_shard.clear()
                list(tc.scan("idx", {}, False, rotation))
                self.assertEqual(first_shard, [rotation])
        finally:
            tc.request = real


class ExactlyOnce(unittest.TestCase):
    """The two properties that make a retry safe, read off the bulk body.

    These check what is SENT. What the catalog then holds is checked against a
    real OpenSearch by tools/collector/mappingcheck.py, because a Painless
    script cannot be honestly emulated in a unit test.
    """

    def test_the_update_sets_an_absolute_count_and_never_adds(self):
        self.assertIn("ctx._source.counts_by_node[params.node] = params.n",
                      tc.APPLY)
        self.assertNotIn("+= params.n", tc.APPLY)

    def test_the_update_refuses_a_pass_it_has_already_applied(self):
        self.assertIn("if (seen != null && seen >= params.pass)", tc.APPLY)
        self.assertIn("ctx.op = 'noop'", tc.APPLY)

    def test_retiring_removes_this_node_and_leaves_the_others(self):
        self.assertIn("ctx._source.counts_by_node.remove(params.node)",
                      tc.RETIRE)
        self.assertIn("ctx._source.superseded_by = params.superseded_by",
                      tc.RETIRE)

    def test_a_document_is_superseded_only_when_nobody_still_counts_it(self):
        """`superseded_by` is a property of the DOCUMENT, not of one node.

        Two nodes publish the same template. One of them generalises, so for
        that node the text has moved. Setting `superseded_by` there marks a
        template the other node is still counting as no longer current, and
        every reader that filters superseded documents out -- which is what the
        field is for -- stops counting the other node.

        The Painless is checked against a real OpenSearch by mappingcheck; what
        is checked here is that the assignment is guarded at all, and that the
        guard is emptiness rather than the retiring node's own departure.
        """
        body = tc.RETIRE
        guard = body.index("ctx._source.superseded_by = params.superseded_by")
        before = body[:guard]
        self.assertIn("counts_by_node.isEmpty()", before)
        self.assertIn("else { ctx._source.remove('superseded_by'); }", body)
        self.assertLess(before.index("counts_by_node.remove(params.node)"),
                        before.index("counts_by_node.isEmpty()"),
                        "emptiness must be judged AFTER this node is removed")

    def test_the_count_is_the_sum_of_the_per_node_counts(self):
        for script in (tc.APPLY, tc.RETIRE):
            self.assertIn("ctx._source.count = total", script)


class Indices(unittest.TestCase):
    """Every route, not only the node-local one."""

    def test_the_default_covers_local_central_and_infologger(self):
        argv = sys.argv
        sys.argv = ["template_catalog.py", "--dry-run"]
        captured = {}
        real = tc.run
        tc.run = lambda indices, dry_run=False: captured.setdefault(
            "indices", indices) and 0 or 0
        try:
            tc.main()
        finally:
            tc.run = real
            sys.argv = argv
        names = dict(captured["indices"])
        self.assertIn("application-logs-local-%s" % tc.NODE_ID, names)
        self.assertIn(tc.CENTRAL_INDEX, names)
        self.assertIn(tc.INFOLOGGER_INDEX, names)

    def test_shared_indices_are_node_scoped_and_the_local_one_is_not(self):
        argv = sys.argv
        sys.argv = ["template_catalog.py", "--dry-run"]
        captured = {}
        real = tc.run
        tc.run = lambda indices, dry_run=False: captured.setdefault(
            "indices", indices) and 0 or 0
        try:
            tc.main()
        finally:
            tc.run = real
            sys.argv = argv
        names = dict(captured["indices"])
        self.assertFalse(names["application-logs-local-%s" % tc.NODE_ID])
        self.assertTrue(names[tc.CENTRAL_INDEX])
        self.assertTrue(names[tc.INFOLOGGER_INDEX])


class Recipe(unittest.TestCase):
    """The recipe is imported, not restated. If tools/templating changes, this
    changes with it; if someone forks the rules, this fails."""

    def test_every_family_the_collector_emits_has_a_recipe(self):
        import drainbench
        for family in ("dpl", "datadist", "dds", "infologger",
                       "journald", "ildaemon"):
            self.assertIn(family, drainbench.RECIPE_SIM, family)
            self.assertIn(family, drainbench.RECIPE_PAD, family)
            self.assertIn(family, drainbench.RECIPE_NUMERIC, family)
            self.assertIn(family, drainbench.RECIPE_STRIP, family)

    def test_preparation_strips_the_envelope(self):
        import drainbench
        prepared = drainbench.recipe_prepare(
            "dpl", "[12:20:34][INFO] Processing timeslice:196")
        self.assertNotIn("12:20:34", prepared)
        self.assertNotIn("INFO", prepared)

    def test_the_worker_installs_the_same_float_merge_as_the_archive(self):
        """Without this the two miners disagree on a template's text.

        The FLOAT/NUM merge is a monkeypatch on drain3's Drain class. The
        offline miner installs it for the length of a run; the first build of
        this service never installed it at all, so a slot holding both a float
        and an integer became a bare wildcard here and a <NUM> in the archive.
        """
        import drainbench
        from drain3.template_miner_config import TemplateMinerConfig
        from drain3 import TemplateMiner
        from drain3.drain import Drain

        # The differing token has to sit past the prefix depth the tree keys
        # on, or the two lines never reach the same cluster and create_template
        # is never called at all. That is why the merge shows up on a real
        # corpus and not on a three-word example.
        lines = ["timeframe builder reported buffer occupancy for link seven"
                 " at <FLOAT> percent",
                 "timeframe builder reported buffer occupancy for link seven"
                 " at <NUM> percent"]

        Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE
        plain = drainbench.recipe_miner("dpl")
        for line in lines:
            plain_result = plain.add_log_message(line)

        drainbench.install_merged_create_template()
        try:
            merged = drainbench.recipe_miner("dpl")
            for line in lines:
                merged_result = merged.add_log_message(line)
            self.assertIn("<*>", plain_result["template_mined"])
            self.assertTrue(merged_result["template_mined"].endswith(
                "at <NUM> percent"), merged_result["template_mined"])
            self.assertNotIn("<*>", merged_result["template_mined"])
        finally:
            Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE

    def test_run_installs_the_merge_before_it_mines(self):
        import drainbench
        from drain3.drain import Drain
        Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE
        real = tc.request

        def fake(path, body=None, method="GET"):
            raise tc.urllib.error.HTTPError(path, 404, "missing", None, None)

        tc.request = fake
        try:
            tc.run([], dry_run=True)
            self.assertIs(Drain.create_template,
                          drainbench._merged_create_template)
        finally:
            tc.request = real
            Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE


class Persistence(unittest.TestCase):
    """The drain tree has to survive between timer runs.

    Drain is incremental and order-dependent. A tree rebuilt from empty every
    ten minutes mines the same messages into different templates depending on
    which batch they fell in, and the catalog then holds two documents where
    there is one template.

    The tree is buffered in memory and written into the single state file, so
    it becomes durable at the same instant as the read position and the pass
    number. Three separate files could half-happen; one atomic replace cannot.
    """

    def setUp(self):
        import drainbench
        drainbench.install_merged_create_template()
        self.tmp = tempfile.mkdtemp()
        self.saved = tc.STATE_DIR
        tc.STATE_DIR = self.tmp

    def tearDown(self):
        import drainbench
        from drain3.drain import Drain
        tc.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        Drain.create_template = drainbench._PLAIN_CREATE_TEMPLATE

    def _pass(self, messages, state):
        """One mining pass, saving the tree the way run() does."""
        import base64
        raw = state.get("trees", {}).get("dpl")
        handler = tc._Buffered(base64.b64decode(raw) if raw else None)
        miner = tc.miner_for("dpl", handler)
        for message in messages:
            miner.add_log_message(message)
        state.setdefault("trees", {}).update(
            tc.dump_trees({"dpl": handler}, {"dpl": miner}))
        return miner

    def test_two_passes_mine_what_one_pass_mines(self):
        import drainbench
        first = ["reader <NUM> opened channel alpha",
                 "reader <NUM> opened channel beta"]
        second = ["reader <NUM> opened channel gamma",
                  "writer <NUM> closed channel alpha"]

        one = drainbench.recipe_miner("dpl")
        for message in first + second:
            one.add_log_message(message)
        single_pass = sorted(c.get_template() for c in one.drain.clusters)

        state = {"trees": {}}
        self._pass(first, state)
        resumed = self._pass(second, state)
        self.assertEqual(sorted(c.get_template() for c in resumed.drain.clusters),
                         single_pass)

    def test_a_fresh_tree_would_lose_it(self):
        """The control for the test above: no carried state, no carried tree."""
        import drainbench
        state = {"trees": {}}
        self._pass(["reader <NUM> opened channel alpha"], state)
        self.assertEqual(len(self._pass([], state).drain.clusters), 1)
        self.assertEqual(len(drainbench.recipe_miner("dpl").drain.clusters), 0)

    def test_the_tree_travels_in_the_same_file_as_the_position(self):
        """Not two files. A bulk write followed by a separate progress save has
        a window in which the write landed and the progress did not, and the
        retry counted everything again."""
        state = {"version": tc.STATE_VERSION, "pass": 1,
                 "position": {"idx": [500, "a"]},
                 "published": {}, "trees": {}}
        self._pass(["reader <NUM> opened channel alpha"], state)
        tc.save_state(state)
        reloaded = tc.load_state()
        self.assertIn("dpl", reloaded["trees"])
        self.assertEqual(reloaded["position"]["idx"], [500, "a"])
        self.assertEqual(os.listdir(self.tmp), [tc.STATE_FILE])


if __name__ == "__main__":
    unittest.main(verbosity=2)
