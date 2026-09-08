import ast
import calendar
import hashlib
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import template_contract as tc  # noqa: E402

FREEZE = os.path.join(REPO, "tools", "embed", "freeze.py")
STAMPER = os.path.join(REPO, "deploy", "roles", "stamper", "files",
                       "stamper.py")
SHIFTER = os.path.join(REPO, "deploy", "roles", "shifter", "files",
                       "shifter.py")
TEMPLATING = os.path.join(REPO, "tools", "templating")
MASKING = os.path.join(TEMPLATING, "masking.py")

MASK_SAMPLES = [
    "/var/log/o2/epn146.log",
    "2026-09-08T14:00:00.123Z",
    "8f14e45f-ceea-467a-9c1a-1b2c3d4e5f60",
    "10.0.0.1:9200",
    "0xdeadBEEF",
    "1,234,567",
    "3.5",
    "-42",
    "+7",
    "12",
]

NORMALIZATION_CASES = [
    ("Failed to open file.", "failed to open file", "f14be5aac03b59f8"),
    ("allocate <NUM> bytes", "allocate <*> bytes", "3aba27572ede143e"),
    ("allocate <FLOAT> bytes", "allocate <*> bytes", "3aba27572ede143e"),
    ("new client: <NUM>/<NUM>", "new client: <*> / <*>", "b61dfcb8bed601b6"),
    ("...", "...", "ab5df625bc76dbd4"),
    ("  Ready   for  <*> run ", "ready for <*> run", "0e7dee73479398df"),
    ("Status: found <NUM> partition(s)", "status: found <*> partition(s)",
     "7bc7217aebc428d1"),
]

VERSION_CASES = [
    ("dpl", "failed to allocate <NUM> bytes",
     "dpl:e5cad426a5acaa0b4610f900"),
    ("dds", "same text", "dds:21847c6a7e8cdb3f4c428fa5"),
    ("infologger", "Status: found <NUM> partition(s)",
     "infologger:c2d905aad028564de87484b8"),
]

IDENTITY_NAMES = {"template_id", "version_id", "canonical_id", "digest",
                  "normalize"}

WINDOW_END = calendar.timegm((2026, 9, 8, 14, 0, 0, 0, 0, 0)) * 1000
WINDOW_START = calendar.timegm((2026, 8, 11, 14, 0, 0, 0, 0, 0)) * 1000
NODE = "epn146"


def extract(path, names):
    with open(path) as handle:
        tree = ast.parse(handle.read())
    namespace = {"re": re, "hashlib": hashlib}
    for node in tree.body:
        name = None
        if isinstance(node, ast.Assign) and isinstance(node.targets[0],
                                                       ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.FunctionDef):
            name = node.name
        if name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), path,
                         "exec"), namespace)
    return namespace


def parse(path):
    with open(path) as handle:
        return ast.parse(handle.read())


def defined_names(path):
    names = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def assigned_names(path):
    names = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def imported_names(path):
    names = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def calls_attribute(path, module, attribute):
    for node in ast.walk(parse(path)):
        if (isinstance(node, ast.Attribute)
                and node.attr == attribute
                and isinstance(node.value, ast.Name)
                and node.value.id == module):
            return True
    return False


class Normalisation(unittest.TestCase):
    def test_pinned_values(self):
        for template, normalized, identity in NORMALIZATION_CASES:
            self.assertEqual(tc.normalize(template), normalized)
            self.assertEqual(tc.canonical_id(template), identity)

    @unittest.skipUnless(os.path.exists(FREEZE), "freeze.py is not present")
    def test_identical_to_the_frozen_corpus_builder(self):
        namespace = extract(FREEZE, {"PLACEHOLDER", "SPACE", "EDGE",
                                     "normalize", "digest"})
        for template, _, _ in NORMALIZATION_CASES:
            self.assertEqual(tc.normalize(template),
                             namespace["normalize"](template))
            self.assertEqual(tc.canonical_id(template),
                             namespace["digest"](
                                 namespace["normalize"](template)))
        self.assertEqual(tc.digest("dpl", "mft-tracker", "link <NUM> is down"),
                         namespace["digest"]("dpl", "mft-tracker",
                                             "link <NUM> is down"))

    def test_typed_masks_and_wildcards_are_one_group(self):
        self.assertEqual(tc.canonical_id("allocate <NUM> bytes"),
                         tc.canonical_id("allocate <FLOAT> bytes"))

    def test_normalization_is_never_empty(self):
        self.assertTrue(tc.normalize("..."))
        self.assertTrue(tc.normalize("---"))


class VersionIdentity(unittest.TestCase):
    def test_pinned_values(self):
        for family, template, identity in VERSION_CASES:
            self.assertEqual(tc.version_id(family, template), identity)

    @unittest.skipUnless(os.path.exists(STAMPER), "the stamper is not present")
    def test_the_shipped_stamper_keeps_no_identifier_of_its_own(self):
        self.assertEqual(defined_names(STAMPER) & IDENTITY_NAMES, set())
        self.assertIn("template_contract", imported_names(STAMPER))
        self.assertTrue(calls_attribute(STAMPER, "contract", "version_id"))

    def test_family_separates_the_same_text(self):
        self.assertNotEqual(tc.version_id("dpl", "same text"),
                            tc.version_id("dds", "same text"))

    def test_a_widened_template_is_a_different_version(self):
        self.assertNotEqual(tc.version_id("dpl", "reader 7 opened"),
                            tc.version_id("dpl", "reader <NUM> opened"))

    def test_a_canonical_group_is_not_a_version(self):
        one = "reader <NUM> opened"
        two = "Reader <FLOAT> opened"
        self.assertEqual(tc.canonical_id(one), tc.canonical_id(two))
        self.assertNotEqual(tc.version_id("dpl", one), tc.version_id("dpl", two))


class SharedRecipe(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(STAMPER), "the stamper is not present")
    def test_the_shipped_stamper_keeps_no_family_mapping_of_its_own(self):
        self.assertNotIn("family_of", defined_names(STAMPER))
        self.assertTrue(calls_attribute(STAMPER, "contract", "family_of"))

    @unittest.skipUnless(os.path.exists(SHIFTER), "the shifter is not present")
    def test_the_shifter_keeps_no_mask_vocabulary_of_its_own(self):
        names = assigned_names(SHIFTER)
        self.assertNotIn("MASK_PATTERNS", names)
        self.assertNotIn("MASK_DEFAULT", names)
        self.assertNotIn("MASK_TOKEN", names)
        self.assertNotIn("template_pattern", defined_names(SHIFTER))
        self.assertNotIn("line_matches_template", defined_names(SHIFTER))

    def test_the_family_mapping_splits_the_process_tree(self):
        self.assertEqual(tc.family_of({"log_time": "12:00:00"}, "stdout"),
                         "dpl")
        self.assertEqual(tc.family_of({"severity": "I"}, "stdout"), "datadist")
        self.assertEqual(tc.family_of({"severity": "Info"}, "stdout"), "dpl")
        self.assertEqual(tc.family_of({}, "dds"), "dds")


class CollectorTime(unittest.TestCase):
    def test_milliseconds_are_taken_as_they_are(self):
        parsed = tc.parse_collector_time(WINDOW_END)
        self.assertEqual(parsed.status, tc.TIME_OK)
        self.assertEqual(parsed.epoch_ms, WINDOW_END)

    def test_seconds_become_milliseconds(self):
        parsed = tc.parse_collector_time(WINDOW_END // 1000)
        self.assertEqual(parsed.epoch_ms, WINDOW_END)

    def test_iso_text_is_read(self):
        for text, expected in (
                ("2026-09-08T14:00:00Z", WINDOW_END),
                ("2026-09-08 14:00:00", WINDOW_END),
                ("2026-09-08T14:00:00.250Z", WINDOW_END + 250),
                ("2026-09-08T16:00:00+02:00", WINDOW_END),
                ("2026-09-08T12:00:00-0200", WINDOW_END)):
            parsed = tc.parse_collector_time(text)
            self.assertEqual(parsed.status, tc.TIME_OK, text)
            self.assertEqual(parsed.epoch_ms, expected, text)

    def test_a_missing_time_is_missing_and_not_now(self):
        for value in (None, "", "   "):
            parsed = tc.parse_collector_time(value)
            self.assertEqual(parsed.status, tc.TIME_MISSING)
            self.assertIsNone(parsed.epoch_ms)

    def test_an_invalid_time_is_invalid_and_not_now(self):
        for value in ("not a time", "2026-13-40T99:99:99Z", -5, True,
                      float("nan"), {}, "2026-09-08T14:00:00XYZ"):
            parsed = tc.parse_collector_time(value)
            self.assertEqual(parsed.status, tc.TIME_INVALID, repr(value))
            self.assertIsNone(parsed.epoch_ms)

    def test_a_record_without_the_field_is_missing(self):
        parsed = tc.observation_time({"message": "hello"}, WINDOW_END)
        self.assertEqual(parsed.status, tc.TIME_MISSING)

    def test_a_time_beyond_tolerance_is_reported_and_kept(self):
        record = {"collector_time": WINDOW_END + tc.CLOCK_TOLERANCE_MS + 1}
        parsed = tc.observation_time(record, WINDOW_END)
        self.assertEqual(parsed.status, tc.TIME_OUT_OF_TOLERANCE)
        self.assertEqual(parsed.epoch_ms, WINDOW_END + tc.CLOCK_TOLERANCE_MS + 1)
        self.assertEqual(tc.gap_for_time(parsed.status), tc.GAP_CLOCK_TOLERANCE)

    def test_a_time_inside_tolerance_is_accepted(self):
        record = {"collector_time": WINDOW_END + tc.CLOCK_TOLERANCE_MS}
        self.assertEqual(tc.observation_time(record, WINDOW_END).status,
                         tc.TIME_OK)

    def test_every_bad_status_names_a_coverage_gap(self):
        for status in (tc.TIME_MISSING, tc.TIME_INVALID,
                       tc.TIME_OUT_OF_TOLERANCE):
            self.assertIn(tc.gap_for_time(status), tc.GAP_REASONS)
        self.assertIsNone(tc.gap_for_time(tc.TIME_OK))


class IntegerTransport(unittest.TestCase):
    def test_below_the_safe_limit_stays_a_number(self):
        for value in (0, 1, 4211, tc.JS_MAX_SAFE_INTEGER):
            self.assertEqual(tc.encode_int(value), value)
            self.assertIsInstance(tc.encode_int(value), int)

    def test_above_the_safe_limit_travels_as_a_decimal_string(self):
        value = tc.JS_MAX_SAFE_INTEGER + 1
        self.assertEqual(tc.encode_int(value), "9007199254740992")
        self.assertIsInstance(tc.encode_int(value), str)

    def test_the_round_trip_is_exact_on_both_sides(self):
        for value in (0, tc.JS_MAX_SAFE_INTEGER - 1, tc.JS_MAX_SAFE_INTEGER,
                      tc.JS_MAX_SAFE_INTEGER + 1, 2 ** 70 + 12345):
            self.assertEqual(tc.decode_int(tc.encode_int(value)), value)

    def test_the_round_trip_survives_json(self):
        value = 2 ** 64 + 7
        moved = json.loads(json.dumps({"count": tc.encode_int(value)}))
        self.assertEqual(tc.decode_int(moved["count"]), value)

    def test_a_float_is_refused(self):
        for value in (1.5, "1.5", True, None, "twelve"):
            with self.assertRaises(tc.ContractError):
                tc.decode_int(value)

    def test_counts_sum_exactly(self):
        entries = [{"count": tc.encode_int(tc.JS_MAX_SAFE_INTEGER)},
                   {"count": tc.encode_int(tc.JS_MAX_SAFE_INTEGER)}]
        self.assertEqual(tc.sum_counts(entries), 2 * tc.JS_MAX_SAFE_INTEGER)


class Checksums(unittest.TestCase):
    def test_a_checksum_does_not_depend_on_key_order(self):
        self.assertEqual(tc.checksum({"a": 1, "b": 2}),
                         tc.checksum({"b": 2, "a": 1}))

    def test_a_checksum_is_stable_across_processes(self):
        self.assertEqual(tc.checksum({"a": 1, "b": [2, 3]}),
                         hashlib.sha256(
                             b'{"a":1,"b":[2,3]}').hexdigest()[:32])

    def test_a_changed_value_changes_the_checksum(self):
        self.assertNotEqual(tc.checksum({"a": 1}), tc.checksum({"a": 2}))

    def test_a_document_that_cannot_be_encoded_is_an_error(self):
        with self.assertRaises(tc.ContractError):
            tc.checksum({"a": object()})


class Retention(unittest.TestCase):
    def test_activity_and_definition_retention_are_separate(self):
        self.assertEqual(tc.ACTIVE_MS, 28 * tc.DAY_MS)
        self.assertEqual(tc.WINDOW_MS, tc.ACTIVE_MS)
        self.assertEqual(tc.DEFINITION_RETENTION_MS, 7776000000)
        self.assertEqual(tc.LEDGER_MS, 48 * tc.HOUR_MS)
        self.assertEqual(tc.FINE_RETENTION_DAYS, 3)
        self.assertEqual(tc.COARSE_RETENTION_DAYS, 35)

    def test_the_query_history_is_kept_for_one_year(self):
        self.assertEqual(tc.QUERY_RETENTION_DAYS, 365)
        self.assertEqual(tc.QUERY_RETENTION_MS, 365 * tc.DAY_MS)

    def test_the_definition_expiry_query_selects_by_observation(self):
        body = tc.expired_definitions(WINDOW_END)
        filters = body["query"]["bool"]["filter"]
        self.assertEqual(filters[0]["term"]["kind"], tc.KIND_CATALOG_TEMPLATE)
        bounds = filters[1]["range"]["last_observed"]
        self.assertEqual(bounds["lte"], WINDOW_END)
        self.assertEqual(bounds["format"], "epoch_millis")

    def test_the_query_expiry_query_selects_by_issue_time(self):
        body = tc.expired_queries(WINDOW_END)
        filters = body["query"]["bool"]["filter"]
        self.assertEqual(filters[0]["term"]["kind"], tc.KIND_QUERY_EVENT)
        bounds = filters[1]["range"]["issued_at"]
        self.assertEqual(bounds["lt"], WINDOW_END)
        self.assertEqual(bounds["format"], "epoch_millis")

    def test_an_expiry_query_refuses_a_cutoff_that_is_not_a_whole_number(self):
        for builder in (tc.expired_definitions, tc.expired_queries):
            with self.assertRaises(tc.ContractError):
                builder("yesterday")

    def test_the_window_without_an_observation_ends_activity(self):
        self.assertTrue(tc.is_active(WINDOW_END, WINDOW_END + tc.ACTIVE_MS - 1))
        self.assertFalse(tc.is_active(WINDOW_END, WINDOW_END + tc.ACTIVE_MS))

    def test_ninety_days_without_an_observation_ends_the_definition(self):
        self.assertTrue(tc.is_retained_definition(
            WINDOW_END, WINDOW_END + tc.DEFINITION_RETENTION_MS - 1))
        self.assertFalse(tc.is_retained_definition(
            WINDOW_END, WINDOW_END + tc.DEFINITION_RETENTION_MS))

    def test_an_inactive_definition_is_still_retained(self):
        later = WINDOW_END + tc.ACTIVE_MS + 1
        self.assertFalse(tc.is_active(WINDOW_END, later))
        self.assertTrue(tc.is_retained_definition(WINDOW_END, later))

    def test_the_activity_period_is_its_own_setting(self):
        self.assertEqual(tc.ACTIVE_MS, tc.ACTIVE_DAYS * tc.DAY_MS)
        shorter = 3 * tc.DAY_MS
        self.assertNotEqual(shorter, tc.WINDOW_MS)
        self.assertTrue(tc.is_active(WINDOW_END, WINDOW_END + shorter - 1,
                                     shorter))
        self.assertFalse(tc.is_active(WINDOW_END, WINDOW_END + shorter,
                                      shorter))



class Buckets(unittest.TestCase):
    def test_the_two_resolutions_and_the_publish_cycle(self):
        self.assertEqual(tc.FINE_BUCKET_MS, 300000)
        self.assertEqual(tc.COARSE_BUCKET_MS, 3600000)
        self.assertEqual(tc.PUBLISH_INTERVAL_MS, tc.FINE_BUCKET_MS)
        with self.assertRaises(tc.ContractError):
            tc.bucket_start_ms(WINDOW_END, 600000)

    def test_starts_ends_and_the_coarse_bucket_of_a_fine_one(self):
        fine = tc.bucket_start_ms(WINDOW_END + 7 * 60000 + 1)
        self.assertEqual(fine, WINDOW_END + 5 * 60000)
        self.assertEqual(tc.bucket_end_ms(fine), fine + tc.FINE_BUCKET_MS)
        self.assertEqual(tc.coarse_bucket_of(fine), WINDOW_END)
        with self.assertRaises(tc.ContractError):
            tc.require_bucket_start(WINDOW_END + 1)

    def test_indices_are_named_by_the_buckets_own_day_and_month(self):
        self.assertEqual(tc.bucket_index_name(tc.FINE_BUCKET_MS, WINDOW_END),
                         "template-buckets-5m-2026.09.08")
        self.assertEqual(tc.bucket_index_name(tc.COARSE_BUCKET_MS,
                                              WINDOW_END),
                         "template-buckets-1h-2026.09")
        self.assertEqual(tc.bucket_index_pattern(tc.FINE_BUCKET_MS),
                         "template-buckets-5m-*")

    def test_the_identifier_is_the_four_keys_and_the_late_mark(self):
        self.assertEqual(
            tc.bucket_id(NODE, "dpl", tc.COARSE_BUCKET_MS, WINDOW_END),
            "bucket:epn146:dpl:3600:%d" % WINDOW_END)
        self.assertEqual(
            tc.bucket_id(NODE, "dpl", tc.FINE_BUCKET_MS, WINDOW_END, True),
            "bucket:epn146:dpl:300:%d:late" % WINDOW_END)
        with self.assertRaises(tc.ContractError):
            tc.bucket_id("epn:146", "dpl", tc.FINE_BUCKET_MS, WINDOW_END)

    def test_a_bucket_document_conserves_and_a_tampered_one_does_not(self):
        counts = {tc.version_id("dpl", "a <NUM>"): 3,
                  tc.version_id("dpl", "b"): 4}
        document = tc.bucket_document(NODE, "dpl", tc.COARSE_BUCKET_MS,
                                      WINDOW_END, counts, WINDOW_END + 1)
        self.assertEqual(document["total"], 7)
        self.assertEqual(document["version_count"], 2)
        self.assertEqual(set(document["versions"]), set(counts))
        self.assertIs(tc.validate_bucket(document), document)
        self.assertTrue(tc.conserved(document))
        document["total"] = 8
        self.assertFalse(tc.conserved(document))
        with self.assertRaises(tc.ContractError):
            tc.validate_bucket(document)

    def test_a_bucket_refuses_another_family_and_an_empty_count(self):
        with self.assertRaises(tc.ContractError):
            tc.bucket_document(NODE, "dpl", tc.FINE_BUCKET_MS, WINDOW_END,
                               {tc.version_id("dds", "x"): 1}, WINDOW_END)
        with self.assertRaises(tc.ContractError):
            tc.bucket_document(NODE, "dpl", tc.FINE_BUCKET_MS, WINDOW_END,
                               {}, WINDOW_END)
        with self.assertRaises(tc.ContractError):
            tc.bucket_document(NODE, "dpl", tc.FINE_BUCKET_MS, WINDOW_END,
                               {tc.version_id("dpl", "x"): 0}, WINDOW_END)


class Watermarks(unittest.TestCase):
    def test_a_watermark_names_the_node_and_carries_its_counters(self):
        document = tc.watermark_document(
            NODE, WINDOW_END, WINDOW_END - tc.LEDGER_MS, WINDOW_END + 5,
            counters={"records": 10})
        self.assertEqual(document["watermark_id"], "watermark:epn146")
        self.assertIs(tc.validate_watermark(document), document)
        self.assertEqual(document["counters"]["records"], 10)
        self.assertEqual(document["counters"]["chunks"], 0)
        with self.assertRaises(tc.ContractError):
            tc.watermark_document(NODE, WINDOW_END, WINDOW_END + 1,
                                  WINDOW_END)
        with self.assertRaises(tc.ContractError):
            tc.stamper_counters(unknown=1)

    def test_the_cutoff_is_the_hour_every_live_node_has_published_past(self):
        marks = {"a": {"published_through": WINDOW_END + 25 * 60000,
                       "published_at": WINDOW_END + 30 * 60000},
                 "b": {"published_through": WINDOW_END - 40 * 60000,
                       "published_at": WINDOW_END + 30 * 60000},
                 "c": {"published_through": WINDOW_END,
                       "published_at": WINDOW_END - tc.HOUR_MS}}
        chosen = tc.choose_cutoff(marks, WINDOW_END + 30 * 60000)
        self.assertEqual(chosen["cutoff"], WINDOW_END - tc.HOUR_MS)
        self.assertEqual(chosen["idle"], ["c"])
        self.assertEqual(tc.choose_cutoff({}, WINDOW_END)["cutoff"], None)

    def test_coverage_names_the_nodes_behind_and_idle(self):
        now = WINDOW_END + 30 * 60000
        marks = {"a": {"published_through": WINDOW_END + 25 * 60000,
                       "published_at": now},
                 "b": {"published_through": WINDOW_END - tc.HOUR_MS,
                       "published_at": now},
                 "c": {"published_through": WINDOW_END,
                       "published_at": now - tc.HOUR_MS}}
        coverage = tc.window_coverage(WINDOW_END, ["a", "b", "c", "d"],
                                      marks, now)
        self.assertEqual(coverage["status"], tc.COVERAGE_PARTIAL)
        self.assertEqual(coverage["complete"], ["a"])
        self.assertEqual(coverage["behind"], ["b"])
        self.assertEqual(coverage["idle"], ["c", "d"])
        self.assertEqual({gap["reason"] for gap in coverage["gaps"]},
                         {tc.GAP_NODE_BEHIND, tc.GAP_NODE_IDLE})
        self.assertEqual(coverage["window_start"], WINDOW_START)
        whole = tc.window_coverage(WINDOW_END, ["a"], marks, now)
        self.assertEqual(whole["status"], tc.COVERAGE_COMPLETE)
        self.assertEqual(tc.window_coverage(WINDOW_END, [], marks, now)
                         ["status"], tc.COVERAGE_UNKNOWN)
        with self.assertRaises(tc.ContractError):
            tc.window_coverage(WINDOW_END + 1, ["a"], marks, now)


class Checks(unittest.TestCase):
    def test_stamped_against_indexed_passes_at_or_below_and_fails_above(self):
        good = tc.check_document(tc.CHECK_STAMPED_AGAINST_INDEXED, NODE, "dpl",
                                 tc.COARSE_BUCKET_MS, WINDOW_END, 10, 9,
                                 WINDOW_END, index="infologger")
        self.assertTrue(good["ok"])
        self.assertEqual(good["difference"], 1)
        self.assertEqual(good["check_id"],
                         "check:stamped_against_indexed:epn146:dpl:3600:%d:"
                         "infologger" % WINDOW_END)
        bad = tc.check_document(tc.CHECK_STAMPED_AGAINST_INDEXED, NODE, "dpl",
                                tc.COARSE_BUCKET_MS, WINDOW_END, 10, 11,
                                WINDOW_END, versions_short=["dpl:x"])
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["versions_short"], ["dpl:x"])

    def test_conservation_is_equality(self):
        same = tc.check_document(tc.CHECK_CONSERVATION, NODE, "dpl",
                                 tc.COARSE_BUCKET_MS, WINDOW_END, 5, 5,
                                 WINDOW_END)
        self.assertTrue(same["ok"])
        less = tc.check_document(tc.CHECK_CONSERVATION, NODE, "dpl",
                                 tc.COARSE_BUCKET_MS, WINDOW_END, 5, 4,
                                 WINDOW_END)
        self.assertFalse(less["ok"])
        with self.assertRaises(tc.ContractError):
            tc.check_id("other", NODE, "dpl", tc.COARSE_BUCKET_MS, WINDOW_END)

    def test_the_check_expiry_query_selects_by_check_time(self):
        body = tc.expired_checks(WINDOW_END)
        filters = body["query"]["bool"]["filter"]
        self.assertEqual(filters[0]["term"]["kind"], tc.KIND_CHECK)
        self.assertEqual(filters[1]["range"]["checked_at"]["lt"], WINDOW_END)


class Definitions(unittest.TestCase):
    def test_a_definition_carries_its_identity_scope_and_links(self):
        document = tc.definition_document(
            "dpl", "reader <NUM> opened", NODE, WINDOW_START, WINDOW_END - 1,
            WINDOW_END, programs=["o2-reader"], origin_hosts=["epn146"],
            log_sources=["stdout"], severity_norm="info",
            widened_into=["dpl:b"], widened_from=["dpl:a"])
        self.assertEqual(document["version_id"],
                         tc.version_id("dpl", "reader <NUM> opened"))
        self.assertEqual(document["canonical_id"],
                         tc.canonical_id("reader <NUM> opened"))
        self.assertEqual(document["token_count"], 3)
        self.assertEqual(document["nodes"], [NODE])
        self.assertEqual(document["widened_into"], ["dpl:b"])
        self.assertEqual(document["widened_from"], ["dpl:a"])
        self.assertEqual(set(document), set(tc.CATALOG_FIELDS))

    def test_scope_is_bounded_and_the_truncation_is_stated(self):
        document = tc.definition_document(
            "dpl", "x", NODE, WINDOW_START, WINDOW_END, WINDOW_END,
            programs=["p%d" % i for i in range(40)])
        self.assertEqual(len(document["programs"]), tc.MAX_ENTRY_PROGRAMS)
        self.assertTrue(document["programs_truncated"])
        with self.assertRaises(tc.ContractError):
            tc.definition_document("dpl", "x", NODE, WINDOW_END,
                                   WINDOW_START, WINDOW_END)


class CoverRelation(unittest.TestCase):
    def test_a_wildcard_covers_a_mask_and_a_literal_and_nothing_else(self):
        wide = tc.template_tokens("sent <*> bytes to <IP>")
        narrow = tc.template_tokens("sent <NUM> bytes to <IP>")
        wider = tc.template_tokens("sent <*> <*> to <IP>")
        self.assertTrue(tc.covers(wide, narrow))
        self.assertFalse(tc.covers(narrow, wide))
        self.assertTrue(tc.covers(wider, wide))
        self.assertTrue(tc.covers(wider, tc.template_tokens(
            "sent error report to <IP>")))
        self.assertFalse(tc.covers(narrow, tc.template_tokens(
            "sent <FLOAT> bytes to <IP>")))
        self.assertFalse(tc.covers(wide, tc.template_tokens("sent <*> bytes")))

    def test_descendants_stay_inside_a_family_and_a_token_count(self):
        rows = [("a:1", "a", "sent <*> bytes to <IP>"),
                ("a:2", "a", "sent <NUM> bytes to <IP>"),
                ("a:3", "a", "sent <*> <*> to <IP>"),
                ("b:1", "b", "sent <*> bytes to <IP>"),
                ("a:4", "a", "sent <*> bytes")]
        out = tc.cover_descendants(rows)
        self.assertEqual(out["a:3"], {"a:1", "a:2"})
        self.assertEqual(out["a:1"], {"a:2"})
        self.assertEqual(out["a:2"], set())
        self.assertEqual(out["b:1"], set())
        self.assertEqual(out["a:4"], set())
        self.assertEqual(tc.family_of_version("a:1"), "a")


if __name__ == "__main__":
    unittest.main()
