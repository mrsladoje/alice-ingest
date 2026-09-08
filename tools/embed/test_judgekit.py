#!/usr/bin/env python3
"""The judging kit, where a silent defect would be worst.

An agreement number is the gate on whether the rubric is usable, so a kappa that
is quietly wrong would pass a bad rubric or fail a good one. The three cases
below have hand-computable answers. The rest guard what an assessor is allowed
to see.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import judgekit  # noqa: E402


class Kappa(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        self.assertEqual(judgekit.weighted_kappa([(0, 0), (3, 3), (2, 2)]), 1.0)

    def test_opposite_grades_are_worse_than_chance(self):
        self.assertEqual(judgekit.weighted_kappa([(0, 3), (3, 0)]), -1.0)

    def test_an_assessor_who_never_varies_scores_chance(self):
        self.assertEqual(judgekit.weighted_kappa([(0, 0), (0, 1)]), 0.0)

    def test_one_grade_apart_beats_two_grades_apart(self):
        near = judgekit.weighted_kappa([(3, 2), (0, 1), (3, 3), (0, 0)])
        far = judgekit.weighted_kappa([(3, 1), (0, 2), (3, 3), (0, 0)])
        self.assertGreater(near, far)


class Grades(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "grades-primary-1.tsv")
        with open(self.path, "w") as fh:
            fh.write("item_id\tgrade\tnote\n")
            fh.write("aaa\t3\tnames the condition\n")
            fh.write("bbb\t7\tout of range\n")
            fh.write("ccc\tnot a number\tbroken\n")
            fh.write("ddd\t0\t\n")

    def test_a_header_and_a_broken_row_are_ignored(self):
        grades = judgekit.read_grades(self.path)
        self.assertEqual(sorted(grades), ["aaa", "ddd"])

    def test_an_out_of_range_grade_is_refused_not_clamped(self):
        self.assertNotIn("bbb", judgekit.read_grades(self.path))


class WhatTheAssessorSees(unittest.TestCase):
    def setUp(self):
        self.row = {"query_id": "q01", "query": "memory pressure",
                    "canonical_id": "abc123", "prior_binary": "1",
                    "prior_source": "ai-judge-2026-09-04"}
        self.group = {"canonical_id": "abc123",
                      "template": "Free SHM memory too low",
                      "programs": {"tfbuilder": 10, "stfbuilder": 2},
                      "distinct_programs": 2,
                      "families": {"infologger": 12},
                      "severity_class": "warning",
                      "lines": 12,
                      "contentless": False,
                      "examples": ["Free SHM memory too low: 1234 of 99999 at "
                                   "10.161.69.84"]}

    def test_the_prior_label_is_not_shown(self):
        item = judgekit.item_of(self.row, self.group)
        blob = json.dumps(item)
        self.assertNotIn("prior", blob)
        self.assertNotIn("ai-judge", blob)

    def test_no_system_identity_or_score_is_shown(self):
        item = judgekit.item_of(self.row, self.group)
        for banned in ("score", "rank", "model", "bm25", "system"):
            self.assertNotIn(banned, json.dumps(item).lower())

    def test_examples_are_redacted(self):
        item = judgekit.item_of(self.row, self.group)
        shown = item["examples"][0]
        self.assertNotIn("10.161.69.84", shown)
        self.assertIn("<IP>", shown)

    def test_the_item_identifier_is_stable_and_not_a_position(self):
        first = judgekit.item_of(self.row, self.group)["item_id"]
        second = judgekit.item_id("q01", "abc123")
        self.assertEqual(first, second)
        self.assertNotEqual(first, judgekit.item_id("q02", "abc123"))


if __name__ == "__main__":
    unittest.main()
