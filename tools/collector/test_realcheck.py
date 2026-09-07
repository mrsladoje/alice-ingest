#!/usr/bin/env python3
"""The bulk check must be able to SEE data loss.

Every figure realcheck.py prints -- severity recovered, unclassified, the field
extraction shares -- is computed over the records that arrived. A run that lost
half the corpus therefore reported 100 % severity recovery, 0 % unclassified,
and exited zero. The percentages were never wrong; they simply described the
survivors and were read as coverage.

Run: python3 tools/collector/test_realcheck.py
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import realcheck  # noqa: E402


class RecordStarts(unittest.TestCase):
    """Lines are not records. Three of the four tailed families fold."""

    def test_the_orchestrator_folds_its_continuations(self):
        lines = ["2026-01-20 08:56:29.045314 fat  odc-grpc-server     1 p:1  x",
                 "  a continuation of the topology script output",
                 "  and another",
                 "2026-01-20 08:56:30.000000 inf  odc-grpc-server     1 p:1  y"]
        self.assertEqual(realcheck.record_starts("odc", lines), 2)

    def test_dds_folds_on_the_same_rule(self):
        lines = ["2026-06-20 12:15:12.942587   inf    dds-agent  <0x0>  a",
                 "continuation with no date at all"]
        self.assertEqual(realcheck.record_starts("dds", lines), 1)

    def test_the_process_tree_folds_on_indentation(self):
        lines = ["[12:20:37][INFO] a record", "    indented continuation",
                 "[12:20:38][INFO] another record"]
        self.assertEqual(realcheck.record_starts("stdout", lines), 2)

    def test_the_daemon_log_has_no_multiline_parser(self):
        lines = ["2026-06-20 11:21:15.724736     New client: 557/2048",
                 "2026-06-20 11:21:16.100200     4 clients disconnected"]
        self.assertEqual(realcheck.record_starts("ildaemon", lines), 2)


class LossIsDetected(unittest.TestCase):
    """The reviewer's own reproduction, as a test.

    Two records in, one record out. The old success condition reported 100 %
    severity recovery, zero unclassified and exited zero.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus.tsv")
        with open(self.corpus, "w") as fh:
            fh.write("odc\todc-grpc-server\t2026-01-20 08:56:29.045314 err  "
                     "odc-grpc-server     1091836 2zRVEg9xZRp:2304     one\n")
            fh.write("odc\todc-grpc-server\t2026-01-20 08:56:30.045314 err  "
                     "odc-grpc-server     1091836 2zRVEg9xZRp:2304     two\n")
        self.real_run = realcheck.run

    def tearDown(self):
        realcheck.run = self.real_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, produced):
        realcheck.run = lambda *a, **k: produced
        argv = sys.argv
        sys.argv = ["realcheck.py", self.corpus, "--family", "odc",
                    "--lines", "10"]
        try:
            return realcheck.main()
        finally:
            sys.argv = argv

    def _record(self, message):
        return {"message": message, "severity": "err", "log_source": "odc",
                "program": "odc-grpc-server", "__tag__": "family.central"}

    def test_a_missing_record_fails_the_run(self):
        status = self._main([self._record("one")])
        self.assertEqual(status, 1, "one record of two must not pass")

    def test_a_complete_run_passes(self):
        status = self._main([self._record("one"), self._record("two")])
        self.assertEqual(status, 0)

    def test_health_records_do_not_count_as_arrivals(self):
        """The collector's own health records share the sink.

        Counting them would let one lost corpus record be masked by one health
        record, which is the same failure wearing a different hat.
        """
        rows = [self._record("one"),
                {"__tag__": "health", "input_records": 2}]
        self.assertEqual(self._main(rows), 1)

    def test_a_duplicate_also_fails(self):
        rows = [self._record("one"), self._record("two"), self._record("two")]
        self.assertEqual(self._main(rows), 1, "a duplicate is not completeness")

    def test_a_duplicate_does_not_cancel_a_missing_record(self):
        """The reviewer's second reproduction.

        Two different records in; the first shipped twice and the second lost.
        The totals match exactly, so a check that compares totals passes and
        the run reports full coverage of a corpus it half lost.
        """
        rows = [self._record("one"), self._record("one")]
        self.assertEqual(self._main(rows), 1,
                         "a duplicate cancelled a missing record")

    def test_the_expected_multiset_comes_from_the_real_cascade(self):
        """It is derived from the rendered production parsers, not restated."""
        lines = ["2026-01-20 08:56:29.045314 err  odc-grpc-server"
                 "     1091836 2zRVEg9xZRp:2304     alpha",
                 "2026-01-20 08:56:30.045314 err  odc-grpc-server"
                 "     1091836 2zRVEg9xZRp:2304     beta"]
        wanted = realcheck.expected_messages("odc", lines, "odc")
        self.assertEqual(dict(wanted), {"alpha": 1, "beta": 1})

    def test_a_folded_continuation_is_one_expected_record(self):
        """Onigmo's (?m) lets the message span the fold; Python's does not.

        Without the correction the continuation would be dropped from the
        expected message and every folded record would look like a mismatch.
        """
        lines = ["2026-01-20 08:35:39.457461 inf  odc-grpc-server"
                 "     1091836 2zRVEg9xZRp:0        stderr: \"Running",
                 "  Loading requirement: BASE/1.0",
                 "2026-01-20 08:35:40.000000 inf  odc-grpc-server"
                 "     1091836 2zRVEg9xZRp:0        next"]
        wanted = realcheck.expected_messages("odc", lines, "odc")
        self.assertEqual(sum(wanted.values()), 2)
        folded = [t for t in wanted if "Loading requirement" in t]
        self.assertEqual(len(folded), 1,
                         "the continuation did not stay with its record")


if __name__ == "__main__":
    unittest.main(verbosity=2)
