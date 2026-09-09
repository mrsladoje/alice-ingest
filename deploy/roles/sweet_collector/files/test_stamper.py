import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

import msgpack

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.environ.setdefault("ALICE_TEMPLATING_PATH",
                      os.path.join(REPO, "tools", "templating"))
os.environ.setdefault("ALICE_SHARED_PATH",
                      os.path.join(REPO, "deploy", "shared"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ["ALICE_TEMPLATING_PATH"])
sys.path.insert(0, os.environ["ALICE_SHARED_PATH"])

import drainbench                                             # noqa: E402
import forward                                                # noqa: E402
import stamper as st                                          # noqa: E402
import template_contract as contract                          # noqa: E402

NOW = 1757340000000
HOUR = contract.COARSE_BUCKET_MS
FINE = contract.FINE_BUCKET_MS


def record(message, offset_ms=0, source="stdout", doc_id=None, **extra):
    body = {"message": message, "log_source": source, "node": "node-01",
            "program": "o2-dpl", "host": "epn146",
            "collector_time": NOW + offset_ms,
            "doc_id": doc_id or "node-01-run-%d" % (NOW + offset_ms)}
    body.update(extra)
    return body


class FakeReturn(object):

    def __init__(self, fail_times=0):
        self.sent = []
        self.fail_times = fail_times
        self.path = "fake"

    def send(self, tag, entries, chunk_id=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise forward.ForwardError("return path down")
        self.sent.append((tag, [dict(r) for _, r in entries], chunk_id))
        return chunk_id

    def close(self):
        pass


class FakeTransport(object):

    def __init__(self, fail=False):
        self.bulks = []
        self.fail = fail
        self.searches = []
        self.search_answer = {"aggregations": {"versions": {"buckets": []}}}

    def bulk(self, lines):
        if self.fail:
            raise OSError("cluster away")
        items = []
        for index in range(0, len(lines), 2):
            action = json.loads(lines[index])
            name, meta = list(action.items())[0]
            self.bulks.append((name, meta["_index"], meta["_id"],
                               json.loads(lines[index + 1])))
            items.append({name: {"_id": meta["_id"], "status": 200}})
        return {"items": items}

    def request(self, path, body=None, method="GET"):
        self.searches.append((path, body))
        prefix = ""
        for clause in ((body or {}).get("query") or {}).get(
                "bool", {}).get("filter", []):
            if "prefix" in clause:
                prefix = list(clause["prefix"].values())[0]
        rows = (((self.search_answer.get("aggregations") or {})
                 .get("versions") or {}).get("buckets") or [])
        kept = [row for row in rows if row["key"].startswith(prefix)]
        return {"aggregations": {"versions": {"buckets": kept}}}


def make(tmp, transport=None, ret=None, clock=None, **kw):
    clock = clock or (lambda: NOW)
    machine = st.Stamper(node_id="node-01", state_dir=tmp,
                         return_socket=os.path.join(tmp, "unused.sock"),
                         transport=transport, clock=clock,
                         status_file=os.path.join(tmp, "status.json"), **kw)
    machine.client = ret or FakeReturn()
    machine.load()
    return machine


def offline(family, messages):
    drainbench.install_merged_create_template()
    miner = drainbench.recipe_miner(family)
    out = []
    for message in messages:
        cluster = drainbench.mine(miner, drainbench.recipe_tokens(family,
                                                                  message))
        out.append(contract.version_id(family, cluster.get_template()))
    return out


class ForwardCodec(unittest.TestCase):

    def test_forward_mode_with_event_time_and_metadata_round_trips(self):
        blob = forward.encode_forward("stdout", [(NOW, {"a": 1})], "c1")
        tag, entries, options = forward.decode_message(
            msgpack.unpackb(blob, raw=False, ext_hook=forward._ext_hook))
        self.assertEqual(tag, "stdout")
        self.assertEqual(entries, [(NOW, {"a": 1})])
        self.assertEqual(options["chunk"], "c1")
        with_meta = ["t", [[[forward.event_time(NOW), {"m": 1}], {"b": 2}]],
                     {"chunk": "c2"}]
        packed = msgpack.unpackb(msgpack.packb(with_meta), raw=False,
                                 ext_hook=forward._ext_hook)
        tag, entries, options = forward.decode_message(packed)
        self.assertEqual(entries, [(NOW, {"b": 2})])

    def test_message_and_packed_modes_decode(self):
        tag, entries, options = forward.decode_message(
            ["t", NOW // 1000, {"x": 1}, {"chunk": "m"}])
        self.assertEqual(entries, [(NOW // 1000 * 1000, {"x": 1})])
        self.assertEqual(options["chunk"], "m")
        stream = b"".join(msgpack.packb([forward.event_time(NOW + i),
                                         {"i": i}]) for i in range(3))
        tag, entries, options = forward.decode_message(
            ["t", stream, {"size": 3, "chunk": "p"}])
        self.assertEqual([r["i"] for _, r in entries], [0, 1, 2])
        self.assertEqual(entries[2][0], NOW + 2)

    def test_the_server_acknowledges_after_the_handler_and_not_on_failure(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "s.sock")
        seen = []
        state = {"fail": True}

        def handler(tag, entries, options):
            if state["fail"]:
                state["fail"] = False
                raise st.StamperError("not yet")
            seen.append((tag, len(entries)))

        server = forward.ForwardServer(path, handler).start()
        try:
            client = forward.ForwardClient(path, ack_timeout=2.0)
            with self.assertRaises(forward.ForwardError):
                client.send("x", [(NOW, {"a": 1})], chunk_id="first")
            chunk = client.send("x", [(NOW, {"a": 1})], chunk_id="first")
            self.assertEqual(chunk, "first")
            self.assertEqual(seen, [("x", 1)])
        finally:
            server.close()
            shutil.rmtree(tmp)


class Stamping(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_every_record_is_stamped_identically_to_the_offline_miner(self):
        messages = ["[12:00:01][INFO] sent 10 bytes to 10.0.0.1",
                    "[12:00:02][INFO] sent 20 bytes to 10.0.0.2",
                    "[12:00:03][INFO] sent error report to 10.0.0.2",
                    "[12:00:04][INFO] device ready"]
        ret = FakeReturn()
        machine = make(self.tmp, ret=ret)
        entries = [(NOW, record(m, i, log_time="12:00:0%d" % i))
                   for i, m in enumerate(messages)]
        machine.handle("family.local", entries, {"chunk": "c1"})
        expected = offline("dpl", messages)
        got = [r[contract.TEMPLATE_VERSION_FIELD] for r in ret.sent[0][1]]
        self.assertEqual(got, expected)
        statuses = [r[contract.TEMPLATE_STATUS_FIELD] for r in ret.sent[0][1]]
        self.assertEqual(statuses[0], contract.STAMP_NEW)
        self.assertIn(contract.STAMP_MATCHED, statuses)
        for row in ret.sent[0][1]:
            self.assertEqual(row[contract.TEMPLATE_ID_FIELD],
                             contract.canonical_id(
                                 machine.ledger.versions[
                                     row[contract.TEMPLATE_VERSION_FIELD]]
                                 ["template"]))

    def test_a_widening_is_recorded_as_a_set_valued_link(self):
        ret = FakeReturn()
        machine = make(self.tmp, ret=ret)
        first = record("[12:00:01][INFO] device ready now", 0, log_time="x")
        second = record("[12:00:02][INFO] device ready later", 1,
                        log_time="x")
        machine.handle("family.local", [(NOW, first)], {"chunk": "a"})
        machine.handle("family.local", [(NOW, second)], {"chunk": "b"})
        v1 = ret.sent[0][1][0][contract.TEMPLATE_VERSION_FIELD]
        v2 = ret.sent[1][1][0][contract.TEMPLATE_VERSION_FIELD]
        self.assertNotEqual(v1, v2)
        self.assertEqual(machine.ledger.versions[v1]["into"], {v2})
        self.assertEqual(machine.ledger.versions[v2]["from"], {v1})
        self.assertIn(v1, machine.ledger.dirty_versions)

    def test_counts_are_exact_and_a_resent_chunk_is_not_counted_twice(self):
        ret = FakeReturn()
        machine = make(self.tmp, ret=ret)
        entries = [(NOW, record("[1:0:0][INFO] hello %d" % i, i, log_time="x"))
                   for i in range(5)]
        machine.handle("family.local", entries, {"chunk": "same"})
        machine.handle("family.local", entries, {"chunk": "same"})
        self.assertEqual(len(ret.sent), 2)
        total = sum(sum(b["counts"].values())
                    for b in machine.ledger.buckets.values())
        self.assertEqual(total, 5)
        self.assertEqual(machine.counters["duplicate_chunks"], 1)
        self.assertEqual(machine.counters["records"], 5)

    def test_a_failed_return_path_counts_nothing_and_raises(self):
        ret = FakeReturn(fail_times=2)
        machine = make(self.tmp, ret=ret)
        entries = [(NOW, record("[1:0:0][INFO] hello", 0, log_time="x"))]
        with self.assertRaises(st.StamperError):
            machine.handle("family.local", entries, {"chunk": "z"})
        self.assertEqual(machine.ledger.buckets, {})
        self.assertEqual(machine.journal.lines, 0)
        self.assertNotIn("z", machine.chunks)
        machine.handle("family.local", entries, {"chunk": "z"})
        self.assertEqual(machine.counters["records"], 1)

    def test_the_journal_replays_after_a_crash_before_the_checkpoint(self):
        ret = FakeReturn()
        machine = make(self.tmp, ret=ret)
        first = [(NOW, record("[1:0:0][INFO] alpha %d" % i, i, log_time="x"))
                 for i in range(3)]
        machine.handle("family.local", first, {"chunk": "one"})
        machine.checkpoint(NOW)
        second = [(NOW, record("[1:0:0][INFO] alpha %d" % i, 10 + i,
                               log_time="x")) for i in range(4)]
        machine.handle("family.local", second, {"chunk": "two"})
        machine.journal.close()
        again = make(self.tmp, ret=FakeReturn())
        total = sum(sum(b["counts"].values())
                    for b in again.ledger.buckets.values())
        self.assertEqual(total, 7)
        self.assertIn("two", again.chunks)
        again.handle("family.local", second, {"chunk": "two"})
        total = sum(sum(b["counts"].values())
                    for b in again.ledger.buckets.values())
        self.assertEqual(total, 7)
        self.assertEqual(again.trees.learned, 1)

    def test_a_record_older_than_the_ledger_lands_in_a_flagged_late_bucket(self):
        machine = make(self.tmp)
        old = record("[1:0:0][INFO] ancient", -(st.LEDGER_MS + HOUR),
                     log_time="x")
        machine.handle("family.local", [(NOW, old)], {"chunk": "l"})
        keys = list(machine.ledger.buckets)
        self.assertEqual(len(keys), 1)
        family, start, late = keys[0]
        self.assertTrue(late)
        self.assertEqual(start, contract.bucket_start_ms(NOW, FINE))
        self.assertEqual(machine.counters["late_records"], 1)

    def test_records_without_a_template_are_counted_apart(self):
        machine = make(self.tmp)
        entries = [(NOW, record("", 0)),
                   (NOW, record("[1:0:0][INFO] fine", 1, log_time="x"))]
        machine.handle("family.local", entries, {"chunk": "n"})
        self.assertEqual(machine.counters["no_template_records"], 1)
        self.assertEqual(machine.counters["records"], 2)
        total = sum(sum(b["counts"].values())
                    for b in machine.ledger.buckets.values())
        self.assertEqual(total, 1)

    def test_an_oversize_message_is_shipped_but_not_templated(self):
        machine = make(self.tmp, max_message_length=64)
        long_line = "[1:0:0][INFO] " + "x" * 100
        entries = [(NOW, record(long_line, 0, log_time="x")),
                   (NOW, record("[1:0:0][INFO] fine", 1, log_time="x"))]
        machine.handle("family.local", entries, {"chunk": "n"})
        first = entries[0][1]
        self.assertEqual(first[contract.TEMPLATE_STATUS_FIELD],
                         contract.STAMP_NO_TEMPLATE)
        self.assertNotIn(contract.TEMPLATE_VERSION_FIELD, first)
        self.assertEqual(first["message"], long_line)
        self.assertEqual(machine.counters["oversize_records"], 1)
        self.assertEqual(machine.counters["no_template_records"], 1)
        self.assertEqual(machine.counters["clusters"], 1)

    def test_the_ceiling_evicts_the_least_recently_used_cluster(self):
        machine = make(self.tmp, max_templates=2)
        first = record("[1:0:0][INFO] one thing here", 0, log_time="x")
        second = record("[1:0:0][INFO] completely different words now", 1,
                        log_time="x")
        third = record("[1:0:0][INFO] a third unrelated shape", 2,
                       log_time="x")
        ret = machine.client
        machine.handle("family.local", [(NOW, first), (NOW, second)],
                       {"chunk": "u"})
        machine.handle("family.local", [(NOW, dict(first))], {"chunk": "v"})
        machine.handle("family.local", [(NOW, third)], {"chunk": "w"})
        statuses = [r[contract.TEMPLATE_STATUS_FIELD]
                    for sent in ret.sent for r in sent[1]]
        self.assertEqual(statuses, [contract.STAMP_NEW, contract.STAMP_NEW,
                                    contract.STAMP_MATCHED,
                                    contract.STAMP_NEW])
        self.assertEqual(machine.counters["clusters"], 2)
        held = machine.trees.clusters[next(iter(machine.trees.clusters))]
        self.assertEqual(len(held), 2)
        machine.handle("family.local", [(NOW, dict(second))], {"chunk": "x"})
        self.assertEqual(ret.sent[-1][1][0][contract.TEMPLATE_STATUS_FIELD],
                         contract.STAMP_NEW)
        self.assertEqual(ret.sent[-1][1][0][contract.TEMPLATE_VERSION_FIELD],
                         ret.sent[0][1][1][contract.TEMPLATE_VERSION_FIELD])
        self.assertEqual(machine.counters["clusters"], 2)

    def test_recency_survives_the_checkpoint(self):
        machine = make(self.tmp, max_templates=2)
        first = record("[1:0:0][INFO] one thing here", 0, log_time="x")
        second = record("[1:0:0][INFO] completely different words now", 1,
                        log_time="x")
        machine.handle("family.local", [(NOW, first), (NOW, second)],
                       {"chunk": "u"})
        machine.handle("family.local", [(NOW, dict(first))], {"chunk": "v"})
        machine.checkpoint()
        machine.trees.prune()
        self.assertEqual(len(machine.trees.identity), 2)
        machine.journal.close()
        again = make(self.tmp, ret=FakeReturn(), max_templates=2)
        self.assertEqual(again.trees.recent_list(),
                         machine.trees.recent_list())
        third = record("[1:0:0][INFO] a third unrelated shape", 2,
                       log_time="x")
        again.handle("family.local", [(NOW, third)], {"chunk": "w"})
        self.assertEqual(again.trees.learned, 2)
        held = again.trees.clusters[next(iter(again.trees.clusters))]
        self.assertNotIn(
            machine.trees.recent_list()[0][1], held)


class Publishing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fill(self, machine):
        entries = []
        for i in range(6):
            entries.append((NOW, record("[1:0:0][INFO] sent %d bytes" % i,
                                        i * 60000, log_time="x")))
        entries.append((NOW, record("2026-09-08 12:00:00.000 inf daemon up",
                                    0, source="dds")))
        machine.handle("family.local", entries, {"chunk": "p"})

    def test_bucket_documents_conserve_and_carry_both_resolutions(self):
        transport = FakeTransport()
        machine = make(self.tmp, transport=transport, local_check=False)
        self.fill(machine)
        report = machine.publish(NOW + 1000)
        self.assertEqual(report["buckets"], 3)
        buckets = [doc for name, index, _, doc in transport.bulks
                   if doc.get("kind") == contract.KIND_BUCKET]
        fine = [d for d in buckets if d["resolution_seconds"] == 300]
        coarse = [d for d in buckets if d["resolution_seconds"] == 3600]
        self.assertEqual(len(fine), 3)
        self.assertEqual(len(coarse), 2)
        for document in buckets:
            contract.validate_bucket(document)
            self.assertTrue(contract.conserved(document))
        self.assertEqual(sum(contract.decode_int(d["total"]) for d in fine),
                         7)
        self.assertEqual(sum(contract.decode_int(d["total"]) for d in coarse),
                         7)
        indices = {index for name, index, _, doc in transport.bulks
                   if doc.get("kind") == contract.KIND_BUCKET}
        self.assertIn(contract.bucket_index_name(FINE, contract.bucket_start_ms(
            NOW, FINE)), indices)
        self.assertIn(contract.bucket_index_name(
            HOUR, contract.bucket_start_ms(NOW, HOUR)), indices)
        self.assertEqual(machine.ledger.dirty, set())

    def test_definitions_and_a_watermark_go_to_the_catalog(self):
        transport = FakeTransport()
        machine = make(self.tmp, transport=transport, local_check=False)
        self.fill(machine)
        machine.publish(NOW + 1000)
        updates = [(index, doc) for name, index, _, doc in transport.bulks
                   if name == "update"]
        self.assertTrue(updates)
        for index, doc in updates:
            self.assertEqual(index, contract.CATALOG_INDEX)
            self.assertEqual(doc["upsert"]["kind"],
                             contract.KIND_CATALOG_TEMPLATE)
            self.assertEqual(doc["upsert"]["nodes"], ["node-01"])
            self.assertIn("o2-dpl", doc["upsert"]["programs"] + [None]
                          if doc["upsert"]["family"] == "dpl" else ["o2-dpl"])
        marks = [doc for name, index, _, doc in transport.bulks
                 if doc.get("kind") == contract.KIND_WATERMARK]
        self.assertEqual(len(marks), 1)
        contract.validate_watermark(marks[0])
        self.assertEqual(marks[0]["published_through"],
                         contract.bucket_start_ms(NOW + 1000, FINE))
        self.assertEqual(marks[0]["counters"]["records"], 7)
        self.assertEqual(machine.ledger.dirty_versions, set())

    def test_a_failed_publication_keeps_every_bucket_dirty(self):
        transport = FakeTransport(fail=True)
        machine = make(self.tmp, transport=transport, local_check=False)
        self.fill(machine)
        before = set(machine.ledger.dirty)
        report = machine.publish(NOW + 1000)
        self.assertIn("failed", report)
        self.assertEqual(machine.ledger.dirty, before)
        self.assertEqual(machine.counters["publication_failures"], 1)
        with open(os.path.join(self.tmp, "status.json")) as handle:
            status = json.load(handle)
        self.assertEqual(status["stamper_publication_failures"], 1)

    def test_a_restart_republishes_every_bucket_in_the_ledger(self):
        transport = FakeTransport()
        machine = make(self.tmp, transport=transport, local_check=False)
        self.fill(machine)
        machine.publish(NOW + 1000)
        machine.checkpoint(NOW + 2000)
        machine.journal.close()
        again = make(self.tmp, transport=FakeTransport(), local_check=False)
        self.assertEqual(len(again.ledger.dirty), 3)
        report = again.publish(NOW + 3000)
        self.assertEqual(report["buckets"], 3)

    def test_the_local_check_compares_indexed_against_stamped(self):
        transport = FakeTransport()
        machine = make(self.tmp, transport=transport, local_check=True)
        self.fill(machine)
        later = contract.bucket_start_ms(NOW, HOUR) + HOUR + 1000
        transport.search_answer = {"aggregations": {"versions": {"buckets": [
            {"key": v, "doc_count": 1} for v in machine.ledger.versions
            if v.startswith("dpl:")]}}}
        report = machine.publish(later)
        self.assertEqual(report["check"]["hour"],
                         contract.bucket_start_ms(NOW, HOUR))
        checks = [doc for name, index, _, doc in transport.bulks
                  if doc.get("kind") == contract.KIND_CHECK]
        self.assertEqual({c["family"] for c in checks}, {"dpl", "dds"})
        for check in checks:
            self.assertTrue(check["ok"], check)
            self.assertEqual(check["check"],
                             contract.CHECK_STAMPED_AGAINST_INDEXED)
        transport.search_answer = {"aggregations": {"versions": {"buckets": [
            {"key": "dpl:ffffffffffffffffffffffff", "doc_count": 9}]}}}
        machine.checked_hour = None
        machine.publish(later + 1000)
        bad = [doc for name, index, _, doc in transport.bulks
               if doc.get("kind") == contract.KIND_CHECK
               and doc["family"] == "dpl"][-1]
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["versions_short"],
                         ["dpl:ffffffffffffffffffffffff"])


class CoverRelation(unittest.TestCase):

    def test_wildcards_cover_masks_and_literals_but_not_the_reverse(self):
        wide = contract.template_tokens("sent <*> bytes to <IP>")
        narrow = contract.template_tokens("sent <NUM> bytes to <IP>")
        wider = contract.template_tokens("sent <*> <*> to <IP>")
        self.assertTrue(contract.covers(wide, narrow))
        self.assertFalse(contract.covers(narrow, wide))
        self.assertTrue(contract.covers(wider, wide))
        self.assertTrue(contract.covers(wider, contract.template_tokens(
            "sent error report to <IP>")))
        self.assertFalse(contract.covers(wide, contract.template_tokens(
            "sent <*> bytes to")))

    def test_descendants_are_computed_per_family_and_token_count(self):
        rows = [("a:1", "a", "sent <*> bytes to <IP>"),
                ("a:2", "a", "sent <NUM> bytes to <IP>"),
                ("a:3", "a", "sent <*> <*> to <IP>"),
                ("b:1", "b", "sent <*> bytes to <IP>"),
                ("a:4", "a", "sent <*> bytes")]
        out = contract.cover_descendants(rows)
        self.assertEqual(out["a:3"], {"a:1", "a:2"})
        self.assertEqual(out["a:1"], {"a:2"})
        self.assertEqual(out["a:2"], set())
        self.assertEqual(out["b:1"], set())
        self.assertEqual(out["a:4"], set())


class Cutoffs(unittest.TestCase):

    def test_the_cutoff_is_the_hour_every_live_node_has_published_past(self):
        marks = {"a": {"published_through": NOW, "published_at": NOW},
                 "b": {"published_through": NOW - 2 * HOUR,
                       "published_at": NOW},
                 "c": {"published_through": NOW, "published_at": NOW - HOUR}}
        chosen = contract.choose_cutoff(marks, NOW)
        self.assertEqual(chosen["cutoff"],
                         contract.bucket_start_ms(NOW - 2 * HOUR, HOUR))
        self.assertEqual(chosen["idle"], ["c"])
        coverage = contract.window_coverage(chosen["cutoff"], ["a", "b", "c"],
                                            marks, NOW)
        self.assertEqual(coverage["status"], contract.COVERAGE_PARTIAL)
        self.assertEqual(coverage["idle"], ["c"])
        self.assertEqual(coverage["complete"], ["a", "b"])
        complete = contract.window_coverage(chosen["cutoff"], ["a", "b"],
                                            marks, NOW)
        self.assertEqual(complete["status"], contract.COVERAGE_COMPLETE)


if __name__ == "__main__":
    unittest.main()
