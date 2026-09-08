#!/usr/bin/env python3
"""What the migration must not do.

The old labels are machine-made and binary. The risk in carrying them forward is
not that a row goes missing — that is counted and reported — but that a label
quietly becomes a grade, or that two old templates that are now one group are
counted as two pieces of evidence for the same answer.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qrels  # noqa: E402

CORPUS = [
    {"canonical_id": "aaaa000000000001",
     "template": "failed to allocate <NUM> bytes",
     "normalized": "failed to allocate <*> bytes"},
    {"canonical_id": "aaaa000000000002",
     "template": "region=<*> size=<NUM>",
     "normalized": "region=<*> size=<*>"},
    {"canonical_id": "aaaa000000000003",
     "template": "link <NUM> is down",
     "normalized": "link <*> is down"},
]

JUDGED = [
    ("memory pressure", "1", "Failed to allocate <NUM> bytes"),
    ("memory pressure", "1", "failed to allocate <FLOAT> bytes"),
    ("memory pressure", "0", "region = <*> size = <NUM>"),
    ("memory pressure", "0", "a template that no longer exists"),
]


class Migration(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus.jsonl")
        with open(self.corpus, "w") as fh:
            for record in CORPUS:
                fh.write(json.dumps(record) + "\n")
        self.judged = os.path.join(self.dir, "judgements.tsv")
        with open(self.judged, "w") as fh:
            for row in JUDGED:
                fh.write("\t".join(row) + "\n")
        self.queries = os.path.join(self.dir, "queries.txt")
        with open(self.queries, "w") as fh:
            fh.write("memory pressure\n")
        self.out = os.path.join(self.dir, "out")
        subprocess.run([sys.executable, os.path.join(HERE, "qrels.py"),
                        "--corpus", self.corpus, "--judgements", self.judged,
                        "--queries", self.queries, "--out", self.out],
                       check=True, capture_output=True)
        with open(os.path.join(self.out, "migration.json")) as fh:
            self.report = json.load(fh)
        with open(os.path.join(self.out, "qrels.tsv")) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            self.rows = [dict(zip(header, line.rstrip("\n").split("\t")))
                         for line in fh]

    def test_no_row_carries_a_grade(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertEqual(row["grade"], "-1")
            self.assertEqual(row["state"], "unreviewed")
            self.assertEqual(row["assessor"], "")

    def test_the_old_label_is_kept_as_a_prior_not_as_truth(self):
        priors = {row["prior_binary"] for row in self.rows}
        self.assertTrue(priors <= {"0", "1"})
        for row in self.rows:
            self.assertTrue(row["prior_source"])

    def test_two_old_templates_that_are_now_one_group_do_not_count_twice(self):
        self.assertEqual(self.report["collapsed_onto_a_judged_group"], 1)
        ids = [row["canonical_id"] for row in self.rows if row["canonical_id"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_padded_separator_still_finds_its_group(self):
        matched = [row for row in self.rows
                   if row["matched_by"] == "separators unpadded"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["canonical_id"], "aaaa000000000002")

    def test_a_template_that_is_gone_is_reported_not_dropped(self):
        self.assertEqual(self.report["lost"], 1)
        lost = [row for row in self.rows if not row["canonical_id"]]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["template_then"],
                         "a template that no longer exists")

    def test_the_query_set_is_labelled_synthetic(self):
        with open(os.path.join(self.out, "queries.jsonl")) as fh:
            queries = [json.loads(line) for line in fh]
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0]["synthetic"])
        self.assertEqual(queries[0]["split"], "calibration")


if __name__ == "__main__":
    unittest.main()
