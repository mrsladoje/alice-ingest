#!/usr/bin/env python3
"""Tests for the catalog verifier's own reads.

Every catalog sequence is judged on what `catalog_documents` returned, so a read
that comes back short makes the judgement wrong in the direction that PASSES: a
stale contribution nobody could see is a stale contribution nobody reports. The
sequences themselves need a cluster; these do not.

Run: python3 tools/collector/test_mappingcheck.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mappingcheck  # noqa: E402


def hit(doc_id, count, superseded=None):
    source = {"kind": "template", "family": "dpl", "count": count}
    if superseded:
        source["superseded_by"] = superseded
    return {"_id": doc_id, "_source": source}


WHOLE = {"total": 1, "successful": 1, "skipped": 0, "failed": 0}


class CatalogReads(unittest.TestCase):

    def setUp(self):
        self.saved = mappingcheck.call

    def tearDown(self):
        mappingcheck.call = self.saved

    def serve(self, search, refresh=(200, {"_shards": {"total": 1,
                                                       "successful": 1,
                                                       "failed": 0}})):
        def call(url, path, body=None, method="GET"):
            if path.endswith("/_refresh"):
                return refresh
            return search
        mappingcheck.call = call

    def test_a_whole_answer_is_read(self):
        self.serve((200, {"_shards": WHOLE,
                          "hits": {"hits": [hit("a", 2)]}}))
        docs = mappingcheck.catalog_documents("http://x", "dpl")
        self.assertEqual([d["count"] for d in docs], [2])

    def test_a_partial_answer_that_hides_a_stale_document(self):
        """The reviewer's control, exactly.

        The catalog holds two live documents totalling three. A response that
        reports a shard failure and returns only one of them looks like the
        answer the sequence wants -- one document counting two -- and the
        sequence would report no problem at all.
        """
        self.serve((200, {"_shards": {"total": 2, "successful": 1,
                                      "skipped": 0, "failed": 1},
                          "hits": {"hits": [hit("a", 2)]}}))
        with self.assertRaises(mappingcheck.Incomplete):
            mappingcheck.catalog_documents("http://x", "dpl")

    def test_a_timed_out_search_is_not_the_whole_catalog(self):
        """Reported with 200 OK and whatever hits it had collected."""
        self.serve((200, {"timed_out": True, "_shards": WHOLE,
                          "hits": {"hits": [hit("a", 2)]}}))
        with self.assertRaises(mappingcheck.Incomplete):
            mappingcheck.catalog_documents("http://x")

    def test_a_search_that_skipped_a_shard_is_not_the_whole_catalog(self):
        """A search reports `total` as SHARDS, so all of them have to be
        accounted for as successful, skipped or failed."""
        self.serve((200, {"_shards": {"total": 3, "successful": 1,
                                      "skipped": 0, "failed": 0},
                          "hits": {"hits": [hit("a", 2)]}}))
        with self.assertRaises(mappingcheck.Incomplete):
            mappingcheck.catalog_documents("http://x")

    def test_a_rejected_search_is_not_an_empty_catalog(self):
        self.serve((503, {}))
        with self.assertRaises(mappingcheck.Incomplete):
            mappingcheck.catalog_documents("http://x")

    def test_a_failed_refresh_is_not_a_read_at_all(self):
        """A write that is durable but not yet searchable is missing from the
        listing, and missing from the listing is what a cleared contribution
        looks like."""
        self.serve((200, {"_shards": WHOLE, "hits": {"hits": []}}),
                   refresh=(200, {"_shards": {"total": 2, "successful": 1,
                                              "failed": 1}}))
        with self.assertRaises(mappingcheck.Incomplete):
            mappingcheck.catalog_documents("http://x")

    def test_an_absent_catalog_is_an_answer(self):
        """Several sequences delete it on purpose. 404 is `nothing there`, not
        `I could not tell you`."""
        self.serve((404, {}), refresh=(404, {}))
        self.assertEqual(mappingcheck.catalog_documents("http://x"), [])

    def test_an_unassigned_replica_does_not_stop_a_refresh(self):
        """A refresh reports `total` as configured COPIES, so one node with a
        replica it cannot assign reports two total and one successful on a
        completely healthy refresh."""
        self.serve((200, {"_shards": WHOLE,
                          "hits": {"hits": [hit("a", 1)]}}),
                   refresh=(200, {"_shards": {"total": 3, "successful": 1,
                                              "failed": 0}}))
        self.assertEqual(len(mappingcheck.catalog_documents("http://x")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
