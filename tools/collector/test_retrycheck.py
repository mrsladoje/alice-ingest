#!/usr/bin/env python3
"""Tests for the retry check's own defences.

A harness that measures a correctness property is itself a place a correctness
property can be lost, and this one lost two. It compared what OpenSearch held
against the identifiers it had watched go past in the proxy, so a record lost
before the proxy ever saw it was missing from both sides at once and cancelled
out. And it asserted nothing about the one source whose records are rewritten by
an allowlist, which is the source that turned out to be dropping the identifier.

Both are checked here without a cluster, because a check that needs three
containers to run is a check that stops being run.

Run: python3 tools/collector/test_retrycheck.py
"""
import collections
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import retrycheck  # noqa: E402


class Expectation(unittest.TestCase):
    """The comparison has to come from outside the measurement.

    Feeding the checker a single fixture record produced no failure at all,
    because the expectation was derived from the same observation as the result.
    """

    LOCAL = "application-logs-local-node-01"
    CENTRAL = "application-logs-central"

    def wanted(self):
        return {
            self.LOCAL: collections.Counter({"one": 1, "two": 1, "three": 1}),
            self.CENTRAL: collections.Counter({"an error": 1}),
        }

    def test_a_complete_arm_passes(self):
        self.assertEqual(
            retrycheck.compare("arm", self.wanted(), self.wanted(),
                               allow_extra=False), [])

    def test_a_lost_record_is_reported(self):
        got = self.wanted()
        del got[self.LOCAL]["three"]
        problems = retrycheck.compare("arm", got, self.wanted(),
                                      allow_extra=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("missing 1 record", problems[0])

    def test_a_single_record_where_four_were_emitted_is_reported(self):
        """The reviewer's injection, exactly."""
        got = {self.LOCAL: collections.Counter({"one": 1})}
        problems = retrycheck.compare("arm", got, self.wanted(),
                                      allow_extra=False)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("missing" in p for p in problems))

    def test_a_duplicate_is_reported_on_the_identified_arm(self):
        got = self.wanted()
        got[self.LOCAL]["two"] += 1
        problems = retrycheck.compare("arm", got, self.wanted(),
                                      allow_extra=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("more than the collector emitted", problems[0])

    def test_a_duplicate_is_allowed_on_the_control(self):
        """The control is SUPPOSED to duplicate. It is still not allowed to
        lose anything, or it would show the same inequality for the wrong
        reason."""
        got = self.wanted()
        got[self.LOCAL]["two"] += 1
        self.assertEqual(
            retrycheck.compare("control", got, self.wanted(),
                               allow_extra=True), [])
        del got[self.LOCAL]["one"]
        self.assertTrue(
            retrycheck.compare("control", got, self.wanted(),
                               allow_extra=True))

    def test_a_record_in_the_wrong_index_is_both_missing_and_extra(self):
        """A misrouted record is neither lost nor duplicated, and a total that
        only adds up across all indices would not notice it."""
        got = self.wanted()
        del got[self.LOCAL]["one"]
        got[self.CENTRAL]["one"] += 1
        problems = retrycheck.compare("arm", got, self.wanted(),
                                      allow_extra=False)
        self.assertEqual(len(problems), 2, problems)
        self.assertEqual(sum(sum(c.values()) for c in got.values()),
                         sum(sum(c.values()) for c in self.wanted().values()),
                         "the totals match, which is why totals are not enough")


class Reader(unittest.TestCase):
    """The listing is where every count in this file comes from.

    A short listing understates BOTH arms at once, so the comparison between
    them still balances and the run passes. Two ways of coming back short were
    found here: an identifier reused across destinations, and a read that could
    not answer being read as an answer of nothing.
    """

    LOCAL = "application-logs-local-node-01"
    CENTRAL = "application-logs-central"

    def setUp(self):
        self.saved_call = retrycheck.call
        self.saved_indices = retrycheck.INDICES
        retrycheck.INDICES = [self.LOCAL, self.CENTRAL]

    def tearDown(self):
        retrycheck.call = self.saved_call
        retrycheck.INDICES = self.saved_indices

    def serve(self, pages, refresh=None):
        """`pages` maps an index to the one search answer it gives."""
        def call(path, body=None, method="GET", timeout=30):
            name = path.strip("/").split("/")[0]
            if path.endswith("/_refresh"):
                return (refresh or {}).get(name, (200, {"_shards": {
                    "total": 1, "successful": 1, "failed": 0}}))
            if path.endswith("/_search"):
                if body.get("search_after"):
                    return 200, {"_shards": {"total": 1, "successful": 1,
                                             "failed": 0},
                                 "hits": {"hits": []}}
                return pages[name]
            return 200, {}
        retrycheck.call = call

    @staticmethod
    def page(*ids):
        return 200, {
            "_shards": {"total": 1, "successful": 1, "failed": 0},
            "hits": {"hits": [
                {"_id": i, "sort": [i], "_source": {"doc_id": i,
                                                    "message": i}}
                for i in ids]},
        }

    def test_the_same_identifier_in_two_indices_is_two_documents(self):
        """The collector assigns ONE identifier per record and writes it to
        whichever index the record is routed to, so `_id` is unique only within
        an index. A record that reached two destinations -- one of the
        duplications this whole check exists to find -- collapsed into a single
        entry, and the listing came back one short of what the cluster held."""
        self.serve({self.LOCAL: self.page("a", "b", "c"),
                    self.CENTRAL: self.page("c", "d")})
        found = retrycheck.documents()
        self.assertEqual(len(found), 5,
                         "a duplicate across destinations was counted once")
        self.assertEqual(sorted(index for index, _ in found),
                         [self.CENTRAL, self.CENTRAL,
                          self.LOCAL, self.LOCAL, self.LOCAL])

    def test_a_complete_read_is_read(self):
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: self.page("b")})
        self.assertEqual(len(retrycheck.documents()), 2)

    def test_a_timed_out_search_is_not_an_empty_index(self):
        """Reported with 200 OK and whatever hits it had collected."""
        timed_out = (200, {"timed_out": True,
                           "_shards": {"total": 1, "successful": 1,
                                       "failed": 0},
                           "hits": {"hits": []}})
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: timed_out})
        with self.assertRaises(retrycheck.Incomplete):
            retrycheck.documents()

    def test_a_failed_shard_is_not_an_empty_index(self):
        """An empty page from a failed shard is byte-identical to an empty page
        from an index with nothing in it."""
        failed = (200, {"_shards": {"total": 2, "successful": 1, "failed": 1},
                        "hits": {"hits": []}})
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: failed})
        with self.assertRaises(retrycheck.Incomplete):
            retrycheck.documents()

    def test_a_refused_connection_is_not_an_empty_index(self):
        """`call` turns a refused connection into `(0, {})`, which is below
        every `>= 300` test ever written here and reads as an empty result."""
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: (0, {})})
        with self.assertRaises(retrycheck.Incomplete):
            retrycheck.documents()

    def test_a_failed_refresh_stops_the_read(self):
        """A write that is durable but not yet searchable is missing from the
        listing, and missing from the listing is what a lost record looks
        like."""
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: self.page("b")},
                   refresh={self.CENTRAL: (200, {"_shards": {
                       "total": 2, "successful": 1, "failed": 1}})})
        with self.assertRaises(retrycheck.Incomplete):
            retrycheck.documents()

    def test_a_missing_index_is_simply_empty(self):
        """The control for all of the above. A destination that received
        nothing does not exist, and 404 there is an answer, not a failure."""
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: (404, {})},
                   refresh={self.CENTRAL: (404, {})})
        self.assertEqual(len(retrycheck.documents()), 1)

    def test_an_unassigned_replica_does_not_stop_the_read(self):
        """`successful < total` on a refresh is the shipped single-node
        layout, not a fault. `failed` is the field that means something went
        wrong."""
        self.serve({self.LOCAL: self.page("a"), self.CENTRAL: self.page("b")},
                   refresh={self.CENTRAL: (200, {"_shards": {
                       "total": 3, "successful": 1, "failed": 0}})})
        self.assertEqual(len(retrycheck.documents()), 2)

    def test_a_listing_shorter_than_the_count_is_caught(self):
        """The reviewer's shape: the cluster holds 44 and the reader says 43.

        Nothing is writing by the time these two reads happen, so they have to
        agree; when they do not, it is the listing that is wrong.
        """
        with self.assertRaises(retrycheck.Incomplete):
            retrycheck.cross_check({("i", "a"): {}, ("i", "b"): {}}, 3)

    def test_a_listing_that_matches_the_count_passes(self):
        retrycheck.cross_check({("i", "a"): {}, ("i", "b"): {}}, 2)

    def test_a_count_that_could_not_be_read_does_not_fail_the_arm(self):
        """None is `not known`, and an unknown count cannot contradict."""
        retrycheck.cross_check({("i", "a"): {}}, None)

    def test_an_incomplete_count_does_not_settle_the_run(self):
        """The settle loop waits for the number to stop changing, so an
        undercount that repeats ends the run early and measures a
        half-written cluster."""
        def call(path, body=None, method="GET", timeout=30):
            if path.endswith("/_refresh"):
                return 200, {"_shards": {"total": 1, "successful": 1,
                                         "failed": 0}}
            return 0, {}
        retrycheck.call = call
        self.assertIsNone(retrycheck.total_documents())


class Allowlists(unittest.TestCase):
    """An allowlist is a DENY list for everything unnamed, and the identifier
    is not a field an operator reads -- so it is exactly what gets left off
    one. It was, on the journal, and the journal became the one source whose
    records reached OpenSearch with no identifier at all."""

    def check(self, text):
        path = os.path.join(self.tmp, "collector.yaml")
        with open(path, "w") as handle:
            handle.write(text)
        return retrycheck.identifier_survives_every_filter(path)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    KEEPS = """  filters:
    - name: record_modifier
      match: journald
      allowlist_key:
        - message
        - severity
        - doc_id
    - name: record_modifier
      match: journald
      record:
        - log_source journald
"""

    def test_a_list_that_keeps_the_identifier_passes(self):
        self.assertEqual(self.check(self.KEEPS), [])

    def test_a_list_that_drops_it_is_caught(self):
        missing = self.check(self.KEEPS.replace("        - doc_id\n", ""))
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0][0], "record_modifier")

    def test_a_list_ended_by_the_next_filter_is_still_checked(self):
        """The first version of this check only noticed a list that ended at a
        blank line, and the shipped configuration ends this one with the next
        filter -- so it checked nothing at all."""
        text = ("  filters:\n"
                "    - name: record_modifier\n"
                "      match: journald\n"
                "      allowlist_key:\n"
                "        - message\n"
                "    - name: modify\n"
                "      match: journald\n")
        self.assertEqual(len(self.check(text)), 1)

    def test_a_list_at_the_end_of_the_file_is_still_checked(self):
        text = ("  filters:\n"
                "    - name: record_modifier\n"
                "      allowlist_key:\n"
                "        - message\n")
        self.assertEqual(len(self.check(text)), 1)

    def test_a_key_literally_called_name_is_not_read_as_a_filter(self):
        text = ("  filters:\n"
                "    - name: record_modifier\n"
                "      allowlist_key:\n"
                "        - name\n"
                "        - doc_id\n")
        self.assertEqual(self.check(text), [])

    def test_the_shipped_configuration_keeps_it_everywhere(self):
        """The rendered production configuration, not a sample of one."""
        import subprocess
        out = os.path.join(self.tmp, "rendered.yaml")
        subprocess.run(
            [sys.executable,
             os.path.join(HERE, "..", "soak", "mkconfig.py"),
             "--out", out,
             "--parsers-out", os.path.join(self.tmp, "parsers.yaml"),
             "--sink", "opensearch", "--odc", "on", "--journald", "on"],
            check=True, capture_output=True)
        self.assertEqual(
            retrycheck.identifier_survives_every_filter(out), [],
            "a filter in the shipped configuration drops the identifier")


if __name__ == "__main__":
    unittest.main(verbosity=2)
