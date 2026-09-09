#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.environ.setdefault("ALICE_SHARED_PATH", os.path.join(REPO, "deploy", "shared"))
sys.path.insert(0, os.path.join(REPO, "deploy", "shared"))
sys.path.insert(0, HERE)

import template_contract as contract  # noqa: E402
import catalog_maintenance as maintenance  # noqa: E402

CATALOG = "template-catalog"
QUERIES = "shifter-queries"
HOUR = contract.COARSE_BUCKET_MS
NOW = 1757343700000
CHECK_HOUR = contract.bucket_start_ms(NOW, HOUR) - HOUR


def bucket(node, family, start, counts, total=None):
    document = contract.bucket_document(node, family, HOUR, start, counts,
                                        NOW - 60000)
    if total is not None:
        document["total"] = total
    return document


class Cluster(object):

    def __init__(self):
        self.buckets = []
        self.indexed = {}
        self.deletes = []
        self.bulks = []
        self.fail_index = set()
        self.partial_listing = False
        self.delete_answer = {"deleted": 0, "version_conflicts": 0,
                              "failures": []}

    def request(self, path, body=None, method="GET"):
        head = path.split("?", 1)[0]
        if head.endswith("/_delete_by_query"):
            self.deletes.append((head.split("/")[1], body))
            return dict(self.delete_answer)
        if head.endswith("/_search"):
            index = head.split("/")[1]
            if index.startswith(contract.COARSE_BUCKETS_PREFIX):
                return self.list_buckets(body)
            return self.aggregate(index, body)
        raise AssertionError("unexpected request %s" % path)

    def list_buckets(self, body):
        filters = body["query"]["bool"]["filter"]
        bounds = [f["range"]["bucket_start"] for f in filters if "range" in f][0]
        rows = [b for b in self.buckets
                if bounds["gte"] <= b["bucket_start"] < bounds["lt"]]
        rows.sort(key=lambda b: (b["bucket_start"], b["bucket_id"]))
        after = body.get("search_after")
        if after is not None:
            rows = [b for b in rows
                    if (b["bucket_start"], b["bucket_id"]) > tuple(after)]
        page = rows[:body["size"]]
        answer = {"hits": {"hits": [
            {"_source": b, "sort": [b["bucket_start"], b["bucket_id"]]}
            for b in page]},
            "_shards": {"total": 1, "successful": 1, "skipped": 0,
                        "failed": 0}}
        if self.partial_listing:
            answer["_shards"]["failed"] = 1
        return answer

    def aggregate(self, index, body):
        if index in self.fail_index:
            raise OSError("%s is away" % index)
        filters = body["query"]["bool"]["filter"]
        node = [f["term"]["node"] for f in filters if "term" in f][0]
        prefix = [list(f["prefix"].values())[0] for f in filters
                  if "prefix" in f][0]
        held = self.indexed.get((index, node, prefix.rstrip(":")), {})
        return {"aggregations": {"versions": {"buckets": [
            {"key": k, "doc_count": v} for k, v in held.items()]}},
            "_shards": {"total": 1, "successful": 1, "skipped": 0,
                        "failed": 0}}

    def bulk(self, lines):
        items = []
        for index in range(0, len(lines), 2):
            action = json.loads(lines[index])
            meta = action["index"]
            self.bulks.append((meta["_index"], meta["_id"],
                               json.loads(lines[index + 1])))
            items.append({"index": {"_id": meta["_id"], "status": 200}})
        return {"items": items}


class Checks(unittest.TestCase):

    def setUp(self):
        self.cluster = Cluster()
        self.cluster.buckets.append(bucket(
            "node-01", "dpl", CHECK_HOUR, {"dpl:a": 5, "dpl:b": 2}))
        self.cluster.buckets.append(bucket(
            "node-02", "dpl", CHECK_HOUR, {"dpl:a": 1}))
        self.cluster.buckets.append(bucket(
            "node-01", "dpl", CHECK_HOUR - HOUR, {"dpl:a": 9}))

    def run_checks(self, **kw):
        return maintenance.run_checks(self.cluster, NOW, CATALOG,
                                      shared_indices=["central", "il"], **kw)

    def test_the_window_is_the_last_completed_hour_behind_the_lag(self):
        start, end = maintenance.check_window(NOW, 1, 1)
        self.assertEqual((start, end), (CHECK_HOUR, CHECK_HOUR + HOUR))
        start, end = maintenance.check_window(NOW, 3, 2)
        self.assertEqual(end, CHECK_HOUR)
        self.assertEqual(start, CHECK_HOUR - 3 * HOUR)

    def test_conservation_holds_and_nothing_is_published_when_all_is_well(self):
        report = self.run_checks()
        self.assertEqual(report["buckets"], 2)
        self.assertEqual(report["conservation_failures"], 0)
        self.assertEqual(report["stamped_checks"], 0)
        self.assertEqual(report["published"], 0)
        self.assertEqual(report["failures"], [])

    def test_a_bucket_whose_parts_do_not_sum_to_its_total_is_reported(self):
        self.cluster.buckets[0]["total"] = 99
        report = self.run_checks()
        self.assertEqual(report["conservation_failures"], 1)
        published = [doc for _, _, doc in self.cluster.bulks]
        self.assertEqual(len(published), 1)
        check = published[0]
        self.assertEqual(check["check"], contract.CHECK_CONSERVATION)
        self.assertFalse(check["ok"])
        self.assertEqual(check["stamped"], 99)
        self.assertEqual(check["indexed"], 7)
        self.assertEqual(check["node"], "node-01")
        self.assertEqual(check["bucket_start"], CHECK_HOUR)

    def test_indexed_at_or_below_stamped_passes_and_above_fails(self):
        self.cluster.indexed[("central", "node-01", "dpl")] = {"dpl:a": 5,
                                                              "dpl:b": 1}
        self.cluster.indexed[("il", "node-02", "dpl")] = {"dpl:a": 3}
        report = self.run_checks()
        self.assertEqual(report["stamped_checks"], 2)
        self.assertEqual(report["stamped_failures"], 1)
        checks = {(doc["node"], doc["index"]): doc
                  for _, _, doc in self.cluster.bulks}
        good = checks[("node-01", "central")]
        self.assertTrue(good["ok"])
        self.assertEqual(good["stamped"], 7)
        self.assertEqual(good["indexed"], 6)
        self.assertEqual(good["difference"], 1)
        bad = checks[("node-02", "il")]
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["versions_short"], ["dpl:a"])
        self.assertEqual(bad["check_id"], contract.check_id(
            contract.CHECK_STAMPED_AGAINST_INDEXED, "node-02", "dpl", HOUR,
            CHECK_HOUR, "il"))

    def test_an_unreadable_index_is_a_failure_and_the_others_still_run(self):
        self.cluster.fail_index.add("central")
        self.cluster.indexed[("il", "node-01", "dpl")] = {"dpl:a": 1}
        report = self.run_checks()
        self.assertEqual(len(report["failures"]), 2)
        self.assertEqual(report["stamped_checks"], 1)

    def test_a_partial_listing_stops_the_checks(self):
        self.cluster.partial_listing = True
        report = self.run_checks()
        self.assertEqual(report["buckets"], 0)
        self.assertEqual(len(report["failures"]), 1)

    def test_the_ceiling_on_buckets_is_stated(self):
        report = self.run_checks(limit=1, page=1)
        self.assertTrue(report["truncated"])
        self.assertEqual(report["buckets"], 1)


class Pass(unittest.TestCase):

    def test_a_pass_expires_definitions_checks_and_queries_and_reports(self):
        cluster = Cluster()
        cluster.delete_answer = {"deleted": 3, "version_conflicts": 1,
                                 "failures": []}
        report, state = maintenance.run_pass(
            cluster, NOW, None, CATALOG, shared_indices=[],
            queries_index=QUERIES)
        deleted = [(index, body["query"]["bool"]["filter"][0]["term"]["kind"])
                   for index, body in cluster.deletes]
        self.assertEqual(deleted, [
            (CATALOG, contract.KIND_CATALOG_TEMPLATE),
            (CATALOG, contract.KIND_CHECK),
            (QUERIES, contract.KIND_QUERY_EVENT)])
        self.assertEqual(report["catalog"]["deleted"], 3)
        self.assertEqual(report["catalog"]["checks_deleted"], 3)
        self.assertEqual(report["failures"], 0)
        self.assertEqual(state["catalog"], NOW)
        self.assertEqual(state["queries"], NOW)
        self.assertEqual(state["checks"], NOW)
        reports = {doc["section"]: doc for _, _, doc in cluster.bulks
                   if doc.get("kind") == maintenance.KIND_MAINTENANCE_REPORT}
        self.assertEqual(set(reports), {"catalog", "queries", "checks"})
        self.assertEqual(reports["catalog"]["deleted"], 3)
        self.assertEqual(reports["catalog"]["version_conflicts"], 1)
        self.assertIsInstance(reports["catalog"]["detail"], str)
        self.assertEqual(json.loads(reports["catalog"]["detail"])["deleted"], 3)
        self.assertEqual(reports["checks"]["deleted"], 0)
        identifiers = {identifier for index, identifier, _ in cluster.bulks}
        self.assertEqual(identifiers, {"maintenance:catalog",
                                       "maintenance:queries",
                                       "maintenance:checks"})

    def test_the_query_section_keeps_its_daily_clock_on_an_hourly_unit(self):
        cluster = Cluster()
        state = maintenance.fresh_state()
        state["queries"] = NOW - 3600000
        report, state = maintenance.run_pass(
            cluster, NOW, state, CATALOG, shared_indices=[],
            queries_index=QUERIES)
        self.assertFalse(report["queries"]["due"])
        self.assertEqual(state["queries"], NOW - 3600000)
        self.assertEqual([i for i, _ in cluster.deletes].count(QUERIES), 0)

    def test_a_failed_section_leaves_its_clock_alone(self):
        cluster = Cluster()

        def request(path, body=None, method="GET"):
            if "_delete_by_query" in path:
                raise OSError("no")
            return Cluster.request(cluster, path, body, method)

        cluster.request = request
        report, state = maintenance.run_pass(
            cluster, NOW, None, CATALOG, shared_indices=[],
            queries_index=QUERIES)
        self.assertIsNone(state["catalog"])
        self.assertIsNone(state["queries"])
        self.assertEqual(state["checks"], NOW)
        self.assertGreater(report["failures"], 0)

    def test_state_survives_a_round_trip_and_rejects_another_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state = maintenance.fresh_state()
            state["checks"] = NOW
            maintenance.save_state(state, path)
            self.assertEqual(maintenance.load_state(path)["checks"], NOW)
            with open(path, "w") as handle:
                json.dump({"version": 1, "catalog": NOW}, handle)
            self.assertIsNone(maintenance.load_state(path)["catalog"])


if __name__ == "__main__":
    unittest.main()
