"""The rollup's silence contract, which the share monitors depend on.

trend-*-volume compares an entity's share of fleet volume against its own
baseline. That comparison has two failure modes that look identical inside a
per-entity aggregation, and only the rollup can separate them:

  one host stops while the fleet keeps logging  -> its share really is 0
  the whole family stops                        -> its share is undefined

An entity that writes no rollup row contributes no fleet_count either, so both
cases reach the monitor as docs 0 of fleet 0. Imputing a zero row for the first
case and nothing for the second is what makes the monitor's fleet_count guard
correct, and turns a dead stream into one log-family-silence page instead of
one warning per host.
"""

import importlib.util
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
os.environ.setdefault("OS_URL", "http://127.0.0.1:1")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rollup = load("trend_rollup_under_test", HERE / "trend_rollup.py")

failures = []

COMBO = {"family": "infologger", "indices": "infologger",
         "entity_kind": "host", "entity_field": "origin_host"}
NOW = 1786700000000


def check(condition, message):
    if not condition:
        failures.append(message)


def entity(name, docs):
    return {"entity": name, "doc_count": docs, "ef_count": 0,
            "p95_entry_lag_ms": 1.0, "p95_shipping_lag_ms": 2.0,
            "avg_entry_lag_ms": 1.0, "avg_shipping_lag_ms": 2.0}


def stub_roster(names, status=200):
    calls = []

    def request(method, path, payload=None, timeout=60):
        calls.append(payload)
        if status != 200:
            return status, {}
        return 200, {"aggregations": {
            "ents": {"buckets": [{"key": n} for n in names]}}}

    request.calls = calls
    rollup.req = request
    return request


def test_a_host_that_goes_quiet_keeps_a_row_with_the_live_fleet():
    stub_roster(["epn1", "epn2", "epn3"])
    filled, imputed, over_cap = rollup.impute_silent(
        COMBO, NOW, [entity("epn1", 500), entity("epn2", 400)])
    check(imputed == 1 and not over_cap,
          f"one silent host should be imputed, got {imputed}")
    lines, count, fleet, _ = rollup.entity_docs(COMBO, NOW, filled)
    docs = {json.loads(x)["entity"]: json.loads(x) for x in lines[1::2]}
    check(fleet == 900,
          f"imputed rows must not inflate fleet_count, got {fleet}")
    check(docs["epn3"]["doc_count"] == 0
          and docs["epn3"]["fleet_count"] == 900,
          "a silent host's slice must be 0 of a live fleet. At 0 of 0 the "
          "monitor cannot tell it apart from the whole family stopping, and "
          "the collapse branch either misses it or fires for every host")
    check(docs["epn3"].get("imputed") is True
          and "imputed" not in docs["epn1"],
          "only imputed rows may carry the imputed flag")
    check("p95_entry_lag_ms" not in docs["epn3"],
          "an imputed row must omit latency fields rather than invent them; "
          "a fabricated p95 would feed the lag monitors a real-looking value")


def test_a_wholly_silent_family_imputes_nothing():
    request = stub_roster(["epn1", "epn2", "epn3"])
    filled, imputed, _ = rollup.impute_silent(COMBO, NOW, [])
    check(filled == [] and imputed == 0,
          "a family with no entities at all must produce no rows, so every "
          "slice carries fleet_count 0 and trend-*-volume skips it. Imputing "
          "here would recreate one share-collapse warning per host for a "
          "single dead stream")
    check(request.calls == [],
          "a wholly silent family should not even query the roster")


def test_an_imputed_zero_cannot_keep_itself_alive():
    request = stub_roster(["epn1"])
    rollup.impute_silent(COMBO, NOW, [entity("epn1", 500)])
    clauses = request.calls[0]["query"]["bool"]["filter"]
    check({"range": {"doc_count": {"gt": 0}}} in clauses,
          "the roster must count only rows that really logged; counting "
          "imputed zeros too would keep a retired host in the roster forever "
          "and it would impute itself on every bucket")
    window = [c for c in clauses if "ts" in c.get("range", {})][0]["range"]
    check(window["ts"]["lt"] == NOW,
          "the roster window must end at this bucket, never overlap it")


def test_a_failed_roster_query_degrades_instead_of_inventing_rows():
    stub_roster([], status=503)
    filled, imputed, over_cap = rollup.impute_silent(
        COMBO, NOW, [entity("epn1", 5)])
    check(imputed == 0 and len(filled) == 1 and not over_cap,
          "a failed roster query must skip imputation for this bucket. The "
          "dwell is three buckets, so one skip delays a silence alert; "
          "guessing the roster would write rows for hosts that may be gone")


def test_imputation_respects_the_entity_cap_and_reports_it():
    original = rollup.MAX_ENTITIES
    try:
        rollup.MAX_ENTITIES = 4
        stub_roster([f"epn{i}" for i in range(10)])
        filled, _, over_cap = rollup.impute_silent(
            COMBO, NOW, [entity("epn0", 5), entity("epn1", 5)])
        check(len(filled) == 4,
              f"imputation must stop at the entity cap, got {len(filled)}")
        check(over_cap is True,
              "hitting the cap while imputing must mark the bucket truncated, "
              "so it cannot publish a complete commit while some entities are "
              "unmonitored")
    finally:
        rollup.MAX_ENTITIES = original


def test_a_complete_bucket_imputes_nothing():
    stub_roster(["epn1", "epn2"])
    filled, imputed, _ = rollup.impute_silent(
        COMBO, NOW, [entity("epn1", 5), entity("epn2", 5)])
    check(imputed == 0 and len(filled) == 2,
          "a bucket where every rostered entity logged must write no extra "
          "rows; steady state has to stay free of imputation")


def test_the_share_monitors_skip_a_slice_with_no_fleet():
    path = HERE / "monitors" / "trend-il-volume.json"
    script = json.loads(path.read_text())["triggers"][0][
        "bucket_level_trigger"]["condition"]["script"]["source"]
    check("if (f0 <= 0 || f1 <= 0 || f2 <= 0) { return false; }" in script,
          "trend-il-volume must skip a slice whose fleet_count is 0. Reading "
          "that as a share of 0 turns one dead stream into one warning per "
          "host, which is what log-family-silence exists to replace")
    check("minBaselineBuckets" in script,
          "trend-il-volume must require a minimum baseline width; against a "
          "single partial first bucket after a rollup reset, every entity in "
          "the cohort breaches at once")


TESTS = [value for name, value in sorted(globals().items())
         if name.startswith("test_") and callable(value)]


if __name__ == "__main__":
    for test in TESTS:
        before = len(failures)
        try:
            test()
        except Exception as exc:
            failures.append(
                f"{test.__name__} raised {type(exc).__name__}: {exc}")
        state = "ok" if len(failures) == before else "FAIL"
        print(f"[trend-rollup-contract] {test.__name__}: {state}")
    if failures:
        for failure in failures:
            print(f"[trend-rollup-contract] FATAL: {failure}")
        raise SystemExit(1)
    print(f"[trend-rollup-contract] PASS ({len(TESTS)} tests)")
