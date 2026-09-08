#!/usr/bin/env python3
"""What the frozen corpus promises, as tests.

The two identifiers and the collapse rule are the contract every later stage
reads, so the cases here are the ones that would silently move a number: a
placeholder eaten off the end of a template, two programs merged into one rank,
a severity token the shipped parsers emit and this file does not know.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freeze  # noqa: E402


def instance(family, program, template, lines=1, severities=None):
    return {
        "instance_id": freeze.digest(family, program, template),
        "family": family,
        "program": program,
        "facility": "absent",
        "template": template,
        "parser": "dpl",
        "lines": lines,
        "share_of_template": 1.0,
        "severities": severities or {"info": lines},
        "message_time_first_seen": None,
        "message_time_last_seen": None,
        "examples": [],
    }


class Normalisation(unittest.TestCase):
    def test_mask_kinds_are_one_meaning(self):
        self.assertEqual(freeze.normalize("allocate <NUM> bytes"),
                         freeze.normalize("allocate <FLOAT> bytes"))

    def test_case_and_trailing_stop_do_not_separate(self):
        self.assertEqual(freeze.normalize("Failed to open file."),
                         freeze.normalize("failed to open file"))

    def test_a_trailing_placeholder_is_not_edge_punctuation(self):
        self.assertNotEqual(freeze.normalize("new client: <NUM>/<NUM>"),
                            freeze.normalize("new client"))

    def test_never_empty(self):
        self.assertTrue(freeze.normalize("..."))


class Collapse(unittest.TestCase):
    def test_one_event_from_two_families_is_one_group(self):
        records = freeze.collapse([
            instance("odc", "odc-grpc-server", "Status: found <NUM> partition(s)", 10),
            instance("infologger", "ODC", "status: found <NUM> partition(s).", 5),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lines"], 15)
        self.assertEqual(records[0]["distinct_programs"], 2)
        self.assertEqual(set(records[0]["families"]), {"odc", "infologger"})

    def test_two_programs_keep_two_instances_under_one_group(self):
        records = freeze.collapse([
            instance("dpl", "mft-tracker", "failed to allocate <NUM> bytes", 3),
            instance("dpl", "gpu-reconstruction", "failed to allocate <NUM> bytes", 7),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["instances"]), 2)
        self.assertEqual(len({i["instance_id"] for i in records[0]["instances"]}), 2)

    def test_different_events_stay_apart(self):
        records = freeze.collapse([
            instance("dpl", "mft-tracker", "link <NUM> is down"),
            instance("dpl", "mft-tracker", "link <NUM> is up"),
        ])
        self.assertEqual(len(records), 2)

    def test_the_biggest_instance_names_the_group(self):
        records = freeze.collapse([
            instance("dpl", "small", "Failed To Open File", 1),
            instance("dpl", "large", "failed to open file", 99),
        ])
        self.assertEqual(records[0]["template"], "failed to open file")

    def test_identifiers_do_not_move_with_order(self):
        first = freeze.collapse([
            instance("dpl", "a", "queue is full", 2),
            instance("dds", "b", "queue is full", 4),
        ])
        second = freeze.collapse([
            instance("dds", "b", "queue is full", 4),
            instance("dpl", "a", "queue is full", 2),
        ])
        self.assertEqual(first[0]["canonical_id"], second[0]["canonical_id"])
        self.assertEqual({i["instance_id"] for i in first[0]["instances"]},
                         {i["instance_id"] for i in second[0]["instances"]})

    def test_an_instance_keeps_the_metadata_the_plan_asks_for(self):
        item = instance("dpl", "mft-tracker", "link <NUM> is down", 4)
        item["examples"] = ["[12:00:00][ERROR] link 3 is down"]
        kept = freeze.collapse([item])[0]["instances"][0]
        for field in ("family", "parser", "program", "lines", "examples"):
            self.assertIn(field, kept)
        self.assertEqual(kept["examples"], ["[12:00:00][ERROR] link 3 is down"])

    def test_contentless_is_marked_and_not_dropped(self):
        records = freeze.collapse([instance("dpl", "a", "<NUM> = <NUM>")])
        self.assertTrue(records[0]["contentless"])


class Severity(unittest.TestCase):
    SHIPPED = ["INFO", "WARN", "ERROR", "FATAL", "STATE", "ALARM", "DEBUG",
               "TRACE", "Info", "Warning", "Error", "Fatal", "Sys", "Break",
               "I", "D", "W", "E", "F", "T",
               "inf", "wrn", "err", "fat", "dbg", "cout",
               "0", "1", "2", "3", "4", "5", "6", "7"]

    def test_every_shipped_token_has_a_class(self):
        unmapped = [s for s in self.SHIPPED
                    if freeze.severity_class(s) == "unknown"]
        self.assertEqual(unmapped, [])

    def test_missing_severity_is_absent_not_info(self):
        self.assertEqual(freeze.severity_class(""), "absent")
        self.assertEqual(freeze.severity_class(None), "absent")

    def test_an_unseen_token_is_unknown_not_guessed(self):
        self.assertEqual(freeze.severity_class("PANIC"), "unknown")


class Program(unittest.TestCase):
    def test_the_parser_wins_over_the_file_label(self):
        self.assertEqual(
            freeze.program_of("dds", "epn146", {"program": "dds-agent"}),
            "dds-agent")

    def test_infologger_splits_facility_from_program(self):
        self.assertEqual(freeze.program_of("infologger", "DPL/gpu-reconstruction", {}),
                         "gpu-reconstruction")
        self.assertEqual(freeze.facility_of("infologger", "DPL/gpu-reconstruction"),
                         "DPL")

    def test_journald_takes_the_identity_not_the_node(self):
        self.assertEqual(freeze.program_of("journald", "epn146/slurmd", {}), "slurmd")

    def test_an_unlabelled_source_is_parse_failed(self):
        self.assertEqual(freeze.program_of("datadist", "unknown", {}), "parse_failed")


if __name__ == "__main__":
    unittest.main()
