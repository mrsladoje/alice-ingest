import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.environ.setdefault("ALICE_TEMPLATING_PATH",
                      os.path.join(REPO, "tools", "templating"))
os.environ.setdefault("ALICE_SHARED_PATH",
                      os.path.join(REPO, "deploy", "shared"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ["ALICE_TEMPLATING_PATH"])
sys.path.insert(0, os.environ["ALICE_SHARED_PATH"])
import importlib.util                                         # noqa: E402

import drainbench                                             # noqa: E402
import stamper as st                                          # noqa: E402
import template_contract as contract                          # noqa: E402
from test_stamper import FakeTransport                        # noqa: E402


def _load_corpus():
    spec = importlib.util.spec_from_file_location(
        "shifterdev_corpus",
        os.path.join(REPO, "tools", "shifterdev", "corpus.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()

CANDIDATES = [os.environ.get("FLUENT_BIT", ""),
              "/opt/fluent-bit/bin/fluent-bit",
              "/opt/homebrew/bin/fluent-bit", "/usr/local/bin/fluent-bit"]
BINARY = next((p for p in CANDIDATES if p and os.path.exists(p)), None)
RECORDS = int(os.environ.get("STAMPER_ACCEPTANCE_RECORDS", "3000"))
WAIT_SECONDS = float(os.environ.get("STAMPER_ACCEPTANCE_WAIT", "90"))

CONFIG = """
service:
  flush: 1
  grace: 1
  log_level: warn
  storage.path: {root}/storage/
  parsers_file: {root}/parsers.yaml
pipeline:
  inputs:
    - name: tail
      path: {root}/in.jsonl
      parser: record
      tag: family.local
      read_from_head: true
      refresh_interval: 1
      db: {root}/tail.db
      storage.type: filesystem
    - name: forward
      unix_path: {root}/stamped.sock
      unix_perm: 0660
      tag_prefix: stamped.
      threaded: on
      storage.type: filesystem
  outputs:
    - name: forward
      match_regex: ^family\\.
      unix_path: {root}/stamper.sock
      require_ack_response: on
      workers: 1
      retry_limit: no_limits
      storage.total_limit_size: 64M
    - name: file
      match_regex: ^stamped\\.
      path: {root}/out
      file: stamped.log
"""

PARSERS = """
parsers:
  - name: record
    format: json
    time_key: event_time
    time_format: '%Y-%m-%dT%H:%M:%S.%L'
    time_keep: on
"""


def build_records(count):
    started = 1757340000
    rows = []
    made = corpus.make(count, 600, seed=11)
    for index, row in enumerate(made):
        record = {
            "message": row["message"],
            "log_source": "infologger",
            "node": "node-01",
            "severity": row.get("severity", "I"),
            "facility": row.get("facility", ""),
            "hostname": row.get("hostname", "epn146"),
            "collector_time": (started + index) * 1000 + 250,
            "event_time": "2026-09-08T12:%02d:%02d.500" % (
                (index // 60) % 60, index % 60),
            "doc_id": "node-01-boot-%d" % (index + 1),
        }
        rows.append(record)
    return rows


def offline_versions(records):
    drainbench.install_merged_create_template()
    miners = {}
    out = {}
    for record in records:
        family = contract.family_of(record, record["log_source"])
        miner = miners.get(family)
        if miner is None:
            miner = miners[family] = drainbench.recipe_miner(family)
        tokens = drainbench.recipe_tokens(family, record["message"])
        if not tokens:
            out[record["doc_id"]] = None
            continue
        cluster = drainbench.mine(miner, tokens)
        out[record["doc_id"]] = contract.version_id(family,
                                                    cluster.get_template())
    return out


def read_output(path):
    rows = {}
    times = {}
    if not os.path.exists(path):
        return rows, times
    with open(path) as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            tag, _, rest = raw.partition(": ")
            try:
                when, record = json.loads(rest)
            except ValueError:
                continue
            rows.setdefault(record["doc_id"], record)
            times[record["doc_id"]] = when
    return rows, times


class LoopHarness(object):

    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "out"))
        os.makedirs(os.path.join(root, "storage"))
        os.makedirs(os.path.join(root, "state"))
        with open(os.path.join(root, "collector.yaml"), "w") as handle:
            handle.write(CONFIG.format(root=root))
        with open(os.path.join(root, "parsers.yaml"), "w") as handle:
            handle.write(PARSERS)
        self.process = None
        self.machine = None

    def start_stamper(self, hook=None):
        transport = FakeTransport()
        machine = st.Stamper(node_id="node-01",
                             state_dir=os.path.join(self.root, "state"),
                             return_socket=os.path.join(self.root,
                                                        "stamped.sock"),
                             transport=transport, local_check=False,
                             status_file=os.path.join(self.root, "status"))
        if hook is not None:
            original = machine.handle

            def handle(tag, entries, options):
                original(tag, entries, options)
                hook(machine, tag, entries, options)

            machine.handle = handle
        machine.serve(listen_socket=os.path.join(self.root, "stamper.sock"))
        self.machine = machine
        return machine

    def stop_stamper(self, checkpoint=True):
        if self.machine is None:
            return
        self.machine.server.close()
        if checkpoint:
            self.machine.checkpoint()
        self.machine.client.close()
        self.machine.journal.close()
        self.machine = None

    def start_fluent_bit(self):
        self.process = subprocess.Popen(
            [BINARY, "-c", os.path.join(self.root, "collector.yaml")],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def stop_fluent_bit(self):
        if self.process is None:
            return b""
        self.process.terminate()
        try:
            _, err = self.process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            _, err = self.process.communicate()
        self.process = None
        return err

    def write_input(self, records):
        with open(os.path.join(self.root, "in.jsonl"), "w") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def wait_for(self, count, predicate=None):
        deadline = time.time() + WAIT_SECONDS
        path = os.path.join(self.root, "out", "stamped.log")
        while time.time() < deadline:
            rows, times = read_output(path)
            if len(rows) >= count and (predicate is None
                                       or predicate(rows)):
                return rows, times
            time.sleep(0.5)
        return read_output(path)


@unittest.skipUnless(BINARY, "no fluent-bit binary on this machine")
class TheForwardLoop(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="stamper-loop-")
        self.harness = LoopHarness(self.root)

    def tearDown(self):
        err = self.harness.stop_fluent_bit()
        self.harness.stop_stamper(checkpoint=False)
        if os.environ.get("STAMPER_ACCEPTANCE_KEEP"):
            print("kept", self.root, err.decode("utf-8", "replace")[-2000:])
        else:
            shutil.rmtree(self.root, ignore_errors=True)

    def test_every_stamp_equals_the_offline_miner_and_times_survive(self):
        records = build_records(RECORDS)
        expected = offline_versions(records)
        self.harness.write_input(records)
        machine = self.harness.start_stamper()
        self.harness.start_fluent_bit()
        rows, times = self.harness.wait_for(len(records))
        self.assertEqual(len(rows), len(records),
                         "fluent-bit delivered %d of %d records"
                         % (len(rows), len(records)))
        differences = []
        for record in records:
            got = rows[record["doc_id"]].get(contract.TEMPLATE_VERSION_FIELD)
            if got != expected[record["doc_id"]]:
                differences.append((record["doc_id"], got,
                                    expected[record["doc_id"]]))
        self.assertEqual(differences, [])
        for record in records:
            self.assertEqual(rows[record["doc_id"]].get(
                contract.TEMPLATE_STATUS_FIELD) in (contract.STAMP_NEW,
                                                    contract.STAMP_MATCHED),
                True)
        sample = records[0]["doc_id"]
        self.assertAlmostEqual(times[sample] % 60, 0.5, places=2)
        with machine.lock:
            total = sum(sum(b["counts"].values())
                        for b in machine.ledger.buckets.values())
            chunks = machine.counters["chunks"]
        self.assertEqual(total, len(records))
        self.assertGreater(chunks, 0)
        self.assertEqual(machine.counters["duplicate_chunks"], 0)
        machine.publish()
        buckets = [doc for name, index, _, doc in machine.transport.bulks
                   if doc.get("kind") == contract.KIND_BUCKET
                   and doc["resolution_seconds"] == 300]
        self.assertEqual(sum(contract.decode_int(d["total"])
                             for d in buckets), len(records))
        for document in buckets:
            contract.validate_bucket(document)

    def test_a_crash_between_return_and_journal_loses_no_count(self):
        records = build_records(max(600, RECORDS // 3))
        self.harness.write_input(records)
        crashed = {"done": False}

        def crash_once(machine, tag, entries, options):
            if crashed["done"] or machine.counters["chunks"] < 1:
                return
            crashed["done"] = True
            with machine.lock:
                machine.journal.close()
                lines = open(machine.journal.path).read().splitlines()
                with open(machine.journal.path, "w") as handle:
                    handle.write("\n".join(lines[:-1]) + "\n")
            raise st.StamperError("simulated crash after the return path")

        machine = self.harness.start_stamper(hook=crash_once)
        self.harness.start_fluent_bit()
        deadline = time.time() + WAIT_SECONDS
        while not crashed["done"] and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(crashed["done"], "the crash hook never fired")
        self.harness.stop_stamper(checkpoint=False)
        again = self.harness.start_stamper()
        rows, _ = self.harness.wait_for(len(records))
        self.assertEqual(len(rows), len(records))
        deadline = time.time() + 10
        while time.time() < deadline:
            with again.lock:
                total = sum(sum(b["counts"].values())
                            for b in again.ledger.buckets.values())
            if total >= len(records):
                break
            time.sleep(0.5)
        self.assertEqual(total, len(records))
        self.assertGreaterEqual(again.counters["duplicate_chunks"], 0)
        distinct = {record["doc_id"] for record in records}
        self.assertEqual(set(rows), distinct)


if __name__ == "__main__":
    unittest.main()
