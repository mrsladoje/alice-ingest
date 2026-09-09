#!/usr/bin/env python3
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def program_rule():
    with open(os.path.join(HERE, "corpus.py")) as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "PROGRAM_RE" for t in node.targets):
            continue
        namespace = {"re": re}
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "corpus.py", "exec"), namespace)
        return namespace["PROGRAM_RE"]
    raise AssertionError("corpus.py has no PROGRAM_RE")


class ProgramName(unittest.TestCase):
    def setUp(self):
        self.rule = program_rule()

    def program(self, path):
        found = self.rule.match(path.rsplit("/", 1)[-1])
        return found.group(1) if found else "unknown"

    def test_a_reco_name_keeps_working(self):
        self.assertEqual(
            self.program("/x/epn146/mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log"),
            "mft-tracker")

    def test_a_name_without_reco_is_no_longer_unknown(self):
        self.assertEqual(
            self.program("/x/epn146/TfBuilderTask_2026-06-20-12-15-19_1234_out.log"),
            "TfBuilderTask")

    def test_a_dated_directory_cannot_supply_the_program(self):
        self.assertEqual(
            self.program("/scratch/epn146_2026-06-20/TfBuilderTask_2026-06-20-12-15-19_1_out.log"),
            "TfBuilderTask")

    def test_a_hyphenated_program_survives_the_thread_suffix(self):
        self.assertEqual(
            self.program("/x/epn146/qc-task-TRD-PHTrackMatch_t0_reco1_2026-06-20-12-20-30_777_out.log"),
            "qc-task-TRD-PHTrackMatch")

    def test_a_name_with_no_stamp_stays_unknown(self):
        self.assertEqual(self.program("/x/epn146/no-stamp_out.log"), "unknown")

    def test_the_rule_matches_the_shipped_parser_on_optionality(self):
        with open(os.path.join(
                HERE, "..", "..", "deploy", "roles", "sweet_collector",
                "templates", "parsers.yaml.j2")) as fh:
            parsers = fh.read()
        shipped = [line for line in parsers.splitlines() if "stdout_path" in line]
        self.assertTrue(shipped)
        stanza = parsers.split("name: stdout_path", 1)[1]
        self.assertIn("(?:_reco\\d+)?", stanza.split("\n\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
