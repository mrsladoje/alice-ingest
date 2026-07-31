import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault(
    "SIGNAL_CATALOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "signal_catalog.json"))

import signal_identity  # noqa: E402
import signal_projector as sp  # noqa: E402
import score_injection as score  # noqa: E402
import ops_server as ops  # noqa: E402

REQUIRED_LABELS = ["alertname", "source", "cluster_id", "severity",
                   "entity_kind", "entity_id", "collector_id", "family",
                   "notification_scope"]

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def stub_roster(collectors, parents, version="v1", effective_from=0):
    sp._roster_cache = [{
        "topology_version": version,
        "effective_from": effective_from,
        "collectors": collectors,
        "parents": parents,
    }]
    sp._roster_loaded_at = sys.float_info.max


def latest_from(episodes):
    latest = {}
    for ep in episodes.values():
        prior = latest.get(ep["incident_id"])
        if prior is None or int(ep["episode_start"]) > int(
                prior["episode_start"]):
            latest[ep["incident_id"]] = ep
    return latest


def timelines_from(episodes):
    timelines = {}
    for ep in episodes.values():
        timelines.setdefault(ep["incident_id"], []).append(ep)
    for values in timelines.values():
        values.sort(key=lambda e: int(e["episode_start"]))
    return timelines


def only(episodes):
    check(len(episodes) == 1, f"expected one episode, got {sorted(episodes)}")
    return list(episodes.values())[0]


def score_fixture(signal_rows, note_rows, scenario="unnamed"):
    names = [
        "START_MS", "END_MS", "STRICT", "SCENARIO", "INDEPENDENT_ENTITY",
        "REQUIRE_INDEPENDENT_RECALL", "raw_alerts", "raw_anomalies", "signals",
        "incidents", "notifications",
    ]
    prior = {name: getattr(score, name) for name in names}
    try:
        score.START_MS = 1000
        score.END_MS = 100000
        score.STRICT = True
        score.SCENARIO = scenario
        score.INDEPENDENT_ENTITY = ""
        score.REQUIRE_INDEPENDENT_RECALL = False
        score.raw_alerts = lambda: {
            str(row["source_id"]) for row in signal_rows
            if row.get("source_kind", "monitor") == "monitor"}
        score.raw_anomalies = lambda: set()
        score.signals = lambda: list(signal_rows)
        score.incidents = lambda: []
        score.notifications = lambda: list(note_rows)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = score.main()
        return result, stdout.getvalue() + stderr.getvalue()
    finally:
        for name, value in prior.items():
            setattr(score, name, value)


def score_page(source_id, state, alertname="collector-down"):
    return {
        "@timestamp": 2000,
        "source_id": source_id,
        "source_kind": "monitor",
        "severity": "page",
        "state": state,
        "incident_id": f"incident-{source_id}",
        "episode_id": f"incident-{source_id}.2000",
        "alertname": alertname,
        "entity_kind": "service",
        "entity_id": alertname,
        "collector_id": signal_identity.sentinel("collector_id"),
        "topology_version": "v1",
        "notification_scope": "fleet",
    }


def alert_hit(alert_id, index, state, monitor, bucket_keys=None,
              start=1000, end=None, severity="1"):
    src = {"id": alert_id, "monitor_name": monitor, "trigger_name": monitor,
           "state": state, "severity": severity, "start_time": start,
           "last_notification_time": start, "monitor_id": "m1"}
    if bucket_keys is not None:
        src["bucket_keys"] = bucket_keys
    if end is not None:
        src["end_time"] = end
    return {"_id": alert_id, "_index": index, "_source": src}


def result_hit(doc_id, detector_id, grade, entity_name, entity_value,
               end_ms):
    return {"_id": doc_id, "_index": ".opendistro-anomaly-results-history-1",
            "_source": {"detector_id": detector_id, "anomaly_grade": grade,
                        "confidence": 0.9, "data_end_time": end_ms,
                        "data_start_time": end_ms - 60000,
                        "execution_end_time": end_ms,
                        "entity": [{"name": entity_name,
                                    "value": entity_value}]}}


def test_alert_identity_survives_the_move_to_history():
    stub_roster(["node-01"], {})
    live = sp.alert_signal(
        alert_hit("a-1", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    moved = sp.alert_signal(
        alert_hit("a-1", ".opendistro-alerting-alert-history-000001",
                  "COMPLETED", "collector-down", ["node-01"], end=2000),
        ".opendistro-alerting-alert-history-000001")
    check(live["source_uid"] == moved["source_uid"],
          f"identity continuity broken: current uid {live['source_uid']} != "
          f"history uid {moved['source_uid']}. AlertMover copies the alert "
          f"into history with the same id, so one logical alert must be one "
          f"signal row, not two.")
    merged = sp.merge_alert_rows([live, moved])
    check(len(merged) == 1,
          f"one alert produced {len(merged)} signal rows after merge")
    check(merged[0]["state"] == "resolved",
          "the terminal history row must win over the stale current-index row")
    check(merged[0]["source_index"].startswith(
        ".opendistro-alerting-alert-history"),
        "provenance must record the physical index the winning row came from")


def test_every_label_is_present_and_explicit():
    stub_roster(["node-01"], {"epn001": "node-01"})
    rows = [
        sp.alert_signal(alert_hit("a-2", sp.ALERTS_CURRENT, "ACTIVE",
                                  "cluster-red"), sp.ALERTS_CURRENT),
        sp.alert_signal(alert_hit("a-3", sp.ALERTS_CURRENT, "ACTIVE",
                                  "trend-il-volume", ["epn001"], severity="2"),
                        sp.ALERTS_CURRENT),
        sp.anomaly_row(result_hit("r-1", "d1", 0.9, "origin_host", "epn001",
                                  5000), {"d1": "il-per-epn"}),
        sp.anomaly_row(result_hit("r-2", "d2", 0.8, "collector_id", "node-01",
                                  5000), {"d2": "ingest-flow"}),
    ]
    for row in rows:
        check(row is not None, "a fixture row failed to project at all")
        row["incident_id"] = sp.incident_id(row)
        ep = sp.blank_incident(row, row["incident_id"], row["first_seen"])
        labels = sp.labels_for(ep)
        for label in REQUIRED_LABELS:
            value = labels.get(label)
            check(value is not None and value != "",
                  f"{row['alertname']}: label {label} missing or empty. "
                  f"Alertmanager treats a missing label and an empty one as "
                  f"the same thing, so an equal: rule would mute unrelated "
                  f"alerts.")
        check(labels["source"] in ("monitor", "detector"),
              f"{row['alertname']}: source label is {labels['source']!r}")


def test_epn_parent_comes_from_the_roster_not_from_arithmetic():
    stub_roster(["node-01", "node-02"], {"epn001": "node-02"})
    row = sp.anomaly_row(
        result_hit("r-3", "d1", 0.9, "origin_host", "epn001", 5000),
        {"d1": "il-per-epn"})
    check(row["collector_id"] == "node-02",
          f"EPN parent came out {row['collector_id']!r}; it must come from "
          f"the roster assignment, never from epn_num % NODE_COUNT")
    stub_roster(["node-01", "node-02"], {})
    row = sp.anomaly_row(
        result_hit("r-4", "d1", 0.9, "origin_host", "epn999", 5000),
        {"d1": "il-per-epn"})
    check(row["collector_id"] == signal_identity.sentinel("collector_id"),
          "a host with no assignment must fail closed to the sentinel so no "
          "collector-scoped inhibition is applied")


def test_episode_opens_recovers_and_resolves_on_evidence():
    stub_roster(["node-01"], {})
    meta = signal_identity.detector("ingest-flow")
    required = int(meta["healthy_windows_required"])
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-10", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})
    check(only(incidents)["episode_state"] == sp.OPEN,
          "a grade above the floor must open the episode")

    for i in range(required):
        healthy = sp.anomaly_row(
            result_hit(f"r-1{i}", "d2", 0.0, "collector_id", "node-01",
                       11000 + i * 60000), {"d2": "ingest-flow"})
        healthy["incident_id"] = sp.incident_id(healthy)
        sp.apply_detector(incidents, [healthy], timelines_from(incidents), {})
        expected = sp.RESOLVED if i + 1 >= required else sp.RECOVERING
        check(only(incidents)["episode_state"] == expected,
              f"after {i + 1} healthy windows the episode is "
              f"{only(incidents)['episode_state']}, expected {expected}")
    check(only(incidents)["state"] == "resolved",
          "K healthy windows must resolve the episode")


def test_inactivity_never_resolves_but_goes_stale():
    stub_roster(["node-01"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-20", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})

    meta = signal_identity.detector("ingest-flow")
    budget = (int(meta["expected_interval_minutes"])
              + int(meta["maximum_lateness_minutes"])) * 60000
    sp.age_episodes(incidents, 10000 + budget + 1)
    check(only(incidents)["episode_state"] == sp.STALE,
          f"a missing evaluation produced {only(incidents)['episode_state']}; "
          f"healthy windows are exactly what the cluster drops under "
          f"indexing pressure, so inactivity must never read as recovery")
    check(only(incidents)["state"] == "firing",
          "a STALE episode is still open — it may expire storage, never "
          "produce RESOLVED")


def test_retired_collector_closes_expected():
    stub_roster(["node-01", "node-02"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-30", "d2", 0.9, "collector_id", "node-02", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})
    stub_roster(["node-01"], {})
    sp.age_episodes(incidents, 20000)
    check(only(incidents)["episode_state"] == sp.CLOSED_EXPECTED,
          "an entity the roster no longer expects must close as expected, "
          "not page forever")


def test_below_floor_results_are_evidence_not_signal_rows():
    stub_roster(["node-01"], {})
    row = sp.anomaly_row(
        result_hit("r-40", "d2", 0.0, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    check(row["state"] == "evidence",
          "a grade-zero result is recovery evidence, not a firing signal")


def test_incident_identity_separates_monitor_and_detector_lanes():
    stub_roster(["node-01"], {})
    alert = sp.alert_signal(
        alert_hit("a-9", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    result = sp.anomaly_row(
        result_hit("r-50", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    check(sp.incident_id(alert) != sp.incident_id(result),
          "a monitor alert and a detector episode on the same entity must be "
          "distinct incidents, or ad-high-grade style completions could close "
          "entity-level episodes")


def test_old_history_cannot_resolve_a_newer_active_alert():
    stub_roster(["node-01"], {})
    old = sp.alert_signal(
        alert_hit("a-old", ".opendistro-alerting-alert-history-000001",
                  "COMPLETED", "collector-down", ["node-01"], start=1000,
                  end=2000), ".opendistro-alerting-alert-history-000001")
    new = sp.alert_signal(
        alert_hit("a-new", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=5000), sp.ALERTS_CURRENT)
    for order in ([new, old], [old, new]):
        rows = sp.merge_alert_rows(list(order))
        for row in rows:
            row["incident_id"] = sp.incident_id(row)
        episodes = {}
        sp.apply_monitor(episodes, rows, {}, {})
        scanned = [r["source_id"] for r in order]
        key = rows[0]["incident_id"]
        check(len(episodes) == 2,
              f"scanned as {scanned}: expected two episodes, got "
              f"{sorted(episodes)}")
        live = episodes.get(sp.episode_id(key, 5000))
        done = episodes.get(sp.episode_id(key, 1000))
        check(live and live["state"] == "firing",
              f"scanned as {scanned}: an older completed alert resolved the "
              f"newer active episode. Rows must be applied in lifecycle "
              f"order, never in the order the indices happened to be scanned.")
        check(done and done["state"] == "resolved",
              f"scanned as {scanned}: the old episode lost its own record")
        check(live and live["member_count"] == 1
              and done and done["member_count"] == 1,
              f"scanned as {scanned}: episodes must not share membership")


def test_repeated_cycles_do_not_churn_episodes():
    stub_roster(["node-01"], {})
    old = sp.alert_signal(
        alert_hit("a-c-old", ".opendistro-alerting-alert-history-000001",
                  "COMPLETED", "collector-down", ["node-01"], start=1000,
                  end=2000), ".opendistro-alerting-alert-history-000001")
    new = sp.alert_signal(
        alert_hit("a-c-new", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=5000), sp.ALERTS_CURRENT)
    rows = sp.merge_alert_rows([new, old])
    for row in rows:
        row["incident_id"] = sp.incident_id(row)
    key = rows[0]["incident_id"]

    seen = []
    latest, stored = {}, {}
    for _ in range(4):
        episodes = {}
        sp.apply_monitor(episodes, rows, latest, stored)
        seen.append(sorted(episodes))
        stored = dict(episodes)
        latest = {}
        for ep in episodes.values():
            prior = latest.get(ep["incident_id"])
            if prior is None or ep["episode_start"] > prior["episode_start"]:
                latest[ep["incident_id"]] = ep
    check(all(ids == seen[0] for ids in seen),
          f"episode identities churned across cycles: {seen}. The 15-minute "
          f"history overlap re-reads the same rows every 30 seconds, so a new "
          f"incident document per cycle is what production would actually do.")
    check(seen[0] == sorted([sp.episode_id(key, 1000),
                             sp.episode_id(key, 5000)]),
          f"unexpected episode set {seen[0]}")


def test_reopen_after_terminal_ages_out_does_not_reset():
    stub_roster(["node-01"], {})
    new = sp.alert_signal(
        alert_hit("a-r-new", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=5000), sp.ALERTS_CURRENT)
    new["incident_id"] = sp.incident_id(new)
    key = new["incident_id"]
    latest = {key: {"incident_id": key, "episode_start": 5000,
                    "episode_id": sp.episode_id(key, 5000),
                    "episode_state": sp.OPEN, "state": "firing",
                    "topology_version": new["topology_version"],
                    "signal_ids": ["a-r-new"], "member_count": 1,
                    "last_seen": 5000}}
    episodes = {}
    sp.apply_monitor(episodes, [new], latest, {})
    check(list(episodes) == [sp.episode_id(key, 5000)],
          f"after the previous terminal alert aged out of the overlap the "
          f"episode restarted as {list(episodes)}; the key is event-time "
          f"derived precisely so it cannot depend on what is still loaded")

    later = sp.alert_signal(
        alert_hit("a-r-later", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=90000), sp.ALERTS_CURRENT)
    later["incident_id"] = key
    resolved_latest = {key: {"incident_id": key, "episode_start": 5000,
                             "episode_id": sp.episode_id(key, 5000),
                             "episode_state": sp.RESOLVED,
                             "state": "resolved", "resolved_at": 8000,
                             "topology_version": later["topology_version"],
                             "last_seen": 8000}}
    episodes = {}
    sp.apply_monitor(episodes, [later], resolved_latest, {})
    check(list(episodes) == [sp.episode_id(key, 90000)],
          f"a reopen after a resolved episode must advance, not reuse or "
          f"reset: got {list(episodes)}")


def test_open_episode_continues_when_its_opening_row_is_gone():
    stub_roster(["node-01"], {})
    later = sp.alert_signal(
        alert_hit("a-cont", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=6000), sp.ALERTS_CURRENT)
    later["incident_id"] = sp.incident_id(later)
    key = later["incident_id"]
    latest = {key: {"incident_id": key, "episode_start": 3000,
                    "episode_id": sp.episode_id(key, 3000),
                    "episode_state": sp.OPEN, "state": "firing",
                    "topology_version": later["topology_version"],
                    "signal_ids": ["a-opener"], "member_count": 1,
                    "last_seen": 3000}}
    episodes = {}
    sp.apply_monitor(episodes, [later], latest, {})
    check(list(episodes) == [sp.episode_id(key, 3000)],
          f"a still-open episode whose opening alert has aged out of the "
          f"window was restarted as {list(episodes)}; a later row with no "
          f"terminal boundary before it belongs to the episode already open")
    check(later.get("episode_id") == sp.episode_id(key, 3000),
          "the continuing signal must carry the open episode, not a new one")


def test_detector_rows_are_not_restamped_onto_the_newest_episode():
    stub_roster(["node-01"], {})
    meta = signal_identity.detector("ingest-flow")
    required = int(meta["healthy_windows_required"])

    breach1 = sp.anomaly_row(
        result_hit("r-b1", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    healthy = [
        sp.anomaly_row(
            result_hit(f"r-h{i}", "d2", 0.0, "collector_id", "node-01",
                       20000 + i * 60000), {"d2": "ingest-flow"})
        for i in range(required)]
    breach2 = sp.anomaly_row(
        result_hit("r-b2", "d2", 0.9, "collector_id", "node-01", 250000),
        {"d2": "ingest-flow"})
    rows = [breach1] + healthy + [breach2]
    for row in rows:
        row["incident_id"] = sp.incident_id(row)
    key = breach1["incident_id"]

    episodes = {}
    sp.apply_detector(episodes, rows, {}, {})
    check(sorted(episodes) == sorted([sp.episode_id(key, 10000),
                                      sp.episode_id(key, 250000)]),
          f"cycle 1 produced {sorted(episodes)}")
    check(breach1.get("episode_id") == sp.episode_id(key, 10000),
          f"cycle 1 stamped the first breach {breach1.get('episode_id')}")

    timelines = {key: sorted(episodes.values(),
                             key=lambda e: e["episode_start"])}
    for cycle_no in (2, 3):
        for row in rows:
            row.pop("episode_id", None)
        replay = {}
        sp.apply_detector(replay, rows, timelines, dict(episodes))
        check(sorted(replay) == sorted([sp.episode_id(key, 10000),
                                        sp.episode_id(key, 250000)]),
              f"cycle {cycle_no} produced {sorted(replay)}; both episode "
              f"documents must stay stable across the overlap")
        check(breach1.get("episode_id") == sp.episode_id(key, 10000),
              f"cycle {cycle_no} restamped the first breach onto "
              f"{breach1.get('episode_id')}. alice-signals uses deterministic "
              f"source ids, so that silently overwrites the correct "
              f"attribution and points notification membership at the wrong "
              f"episode.")
        check(breach2.get("episode_id") == sp.episode_id(key, 250000),
              f"cycle {cycle_no} stamped the second breach "
              f"{breach2.get('episode_id')}")


def test_stale_dwell_survives_a_replayed_evaluation():
    stub_roster(["node-01"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-s1", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})

    meta = signal_identity.detector("ingest-flow")
    budget = (int(meta["expected_interval_minutes"])
              + int(meta["maximum_lateness_minutes"])) * 60000
    became_stale = 10000 + budget + 1
    sp.age_episodes(incidents, became_stale)
    ep = only(incidents)
    check(ep["episode_state"] == sp.STALE, "episode did not go stale")
    marked = ep.get("stale_since")
    check(marked == became_stale,
          f"stale_since was {marked}, expected {became_stale}")

    sp.apply_detector(incidents, [firing], timelines_from(incidents),
                      dict(incidents))
    ep = only(incidents)
    check(ep.get("stale_since") == marked,
          f"replaying the identical evaluation cleared stale provenance "
          f"({ep.get('stale_since')} != {marked}); only a strictly newer "
          f"accepted evaluation may clear it, and the overlap re-reads the "
          f"same rows every cycle by design")

    _, dwell = sp.age_episodes(incidents, became_stale + 60000)
    values = dwell.get("ingest-flow") or []
    check(values == [60000],
          f"stale dwell reported {values}, expected [60000]. This is the "
          f"number the S4 instrumentation exists to record, so a reset makes "
          f"the evidence the calibrated run collects worthless.")


def test_stale_clears_only_on_a_newer_evaluation():
    stub_roster(["node-01"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-s2", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})
    meta = signal_identity.detector("ingest-flow")
    budget = (int(meta["expected_interval_minutes"])
              + int(meta["maximum_lateness_minutes"])) * 60000
    sp.age_episodes(incidents, 10000 + budget + 1)

    fresh = sp.anomaly_row(
        result_hit("r-s3", "d2", 0.9, "collector_id", "node-01",
                   10000 + budget + 2000), {"d2": "ingest-flow"})
    fresh["incident_id"] = sp.incident_id(fresh)
    sp.apply_detector(incidents, [fresh], timelines_from(incidents),
                      dict(incidents))
    ep = only(incidents)
    check(ep["episode_state"] == sp.OPEN,
          f"a genuinely newer evaluation must lift the episode out of STALE, "
          f"got {ep['episode_state']}")
    check("stale_since" not in ep,
          "stale provenance must be cleared once the detector is evaluating "
          "again")


def test_fleet_level_silence_is_mass_silence_at_any_roster_size():
    for size in (2, 3, 100):
        collectors = [f"node-{i:02d}" for i in range(1, size + 1)]
        stub_roster(collectors, {})
        row = sp.alert_signal(
            alert_hit("a-fleet", sp.ALERTS_CURRENT, "ACTIVE",
                      "fleet-fb-silence"), sp.ALERTS_CURRENT)
        klass = sp.classify_mass_silence([row])
        check(klass == "unknown-mass-silence",
              f"roster {size}: a firing fleet-fb-silence classified as "
              f"{klass!r}. Its monitor predicate already proves at least "
              f"fleet_silence_fraction of the roster is silent, so counting "
              f"its single entity_id 'all' against the roster size makes the "
              f"class vanish on any roster larger than two.")

    stub_roster(["node-01", "node-02", "node-03"], {})
    one_down = sp.alert_signal(
        alert_hit("a-one", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    check(sp.classify_mass_silence([one_down]) is None,
          "a single collector-down must not be mass silence")
    downs = [sp.alert_signal(
        alert_hit(f"a-d{i}", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  [f"node-0{i}"]), sp.ALERTS_CURRENT) for i in (1, 2)]
    check(sp.classify_mass_silence(downs) == "unknown-mass-silence",
          "two of three collectors down is past the fraction and must page "
          "as mass silence")


def test_resolved_page_still_gates_false_inhibition():
    page = score_page("resolved-page", "resolved")
    result, output = score_fixture([page], [])
    check(result == 1,
          f"an unnotified page that resolved before scoring passed: {output}")
    check("resolved-page" in output,
          "the resolved page was absent from false-inhibition evidence")


def test_breakglass_has_its_own_delivery_contract():
    page = score_page(
        "deadman-page", "firing", alertname="signal-projector-stale")
    valid = {
        "@timestamp": 3000,
        "record_kind": "notification",
        "delivery_path": "breakglass",
        "group_key": "breakglass/signal-projector-stale",
        "alertname": "signal-projector-stale",
        "signal_ids": [],
        "episode_ids": [],
    }
    result, output = score_fixture(
        [page], [valid], scenario="stop-projector")
    check(result == 0,
          f"the required break-glass delivery failed scoring: {output}")

    invalid = dict(valid)
    invalid["group_key"] = "breakglass/cluster-red"
    invalid["alertname"] = "cluster-red"
    result, output = score_fixture([], [invalid])
    check(result == 1,
          f"an unauthorized alert used the break-glass path: {output}")
    check("cluster-red" in output,
          "the unauthorized break-glass alert was not named in the failure")


def test_stop_projector_waits_for_a_successful_catchup_cycle():
    import yaml
    here = os.path.dirname(os.path.abspath(__file__))
    path = None
    for _ in range(6):
        candidate = os.path.join(here, "inject.yml")
        if os.path.exists(candidate):
            path = candidate
            break
        here = os.path.dirname(here)
    if path is None:
        print("[signal-contract] "
              "test_stop_projector_waits_for_a_successful_catchup_cycle: "
              "skipped, inject.yml not beside this checkout")
        return
    plays = yaml.safe_load(open(path))
    tasks = []
    for play in plays:
        tasks.extend(play.get("tasks") or [])
    names = [task.get("name") for task in tasks]
    restore = names.index("Restore the injected component")
    barrier = names.index("Wait for a successful projector catch-up cycle")
    scoring = names.index("Score the run against all seven metrics")
    check(restore < barrier < scoring,
          "the projector catch-up barrier must sit between restart and scoring")
    task = tasks[barrier]
    contract = json.dumps(task)
    check("projector_cycle_ok" in contract and
          "_projector_restart_clock" in contract,
          "the catch-up barrier does not require a successful cycle newer than "
          "the restart boundary")


def test_signals_keep_their_own_episode():
    stub_roster(["node-01"], {})
    old = sp.alert_signal(
        alert_hit("a-s-old", ".opendistro-alerting-alert-history-000001",
                  "COMPLETED", "collector-down", ["node-01"], start=1000,
                  end=2000), ".opendistro-alerting-alert-history-000001")
    new = sp.alert_signal(
        alert_hit("a-s-new", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=5000), sp.ALERTS_CURRENT)
    rows = sp.merge_alert_rows([new, old])
    for row in rows:
        row["incident_id"] = sp.incident_id(row)
    key = rows[0]["incident_id"]
    sp.apply_monitor({}, rows, {}, {})
    by_id = {r["source_id"]: r for r in rows}
    check(by_id["a-s-old"].get("episode_id") == sp.episode_id(key, 1000),
          f"the old resolved signal was stamped "
          f"{by_id['a-s-old'].get('episode_id')}, not its own episode")
    check(by_id["a-s-new"].get("episode_id") == sp.episode_id(key, 5000),
          f"the new active signal was stamped "
          f"{by_id['a-s-new'].get('episode_id')}, not its own episode")


def test_old_episode_notification_cannot_cover_a_new_one():
    stub_roster(["node-01"], {})
    old = sp.alert_signal(
        alert_hit("a-n-old", ".opendistro-alerting-alert-history-000001",
                  "COMPLETED", "collector-down", ["node-01"], start=1000,
                  end=2000), ".opendistro-alerting-alert-history-000001")
    new = sp.alert_signal(
        alert_hit("a-n-new", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"], start=5000), sp.ALERTS_CURRENT)
    rows = sp.merge_alert_rows([new, old])
    for row in rows:
        row["incident_id"] = sp.incident_id(row)
    episodes = {}
    sp.apply_monitor(episodes, rows, {}, {})
    payloads = {a["annotations"]["episode_id"]: a
                for a in sp.alertmanager_payload(episodes)}
    key = rows[0]["incident_id"]
    check(sp.episode_id(key, 1000) in payloads
          and sp.episode_id(key, 5000) in payloads,
          f"each episode must notify under its own id: {sorted(payloads)}")
    check(payloads[sp.episode_id(key, 1000)]["annotations"]["episode_id"]
          != payloads[sp.episode_id(key, 5000)]["annotations"]["episode_id"],
          "a notification for the resolved episode would otherwise mark the "
          "still-firing one as covered, because both carry the same stable "
          "incident_id")


def test_replayed_healthy_result_cannot_manufacture_recovery():
    stub_roster(["node-01"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-60", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], {}, {})
    key = firing["incident_id"]

    healthy = sp.anomaly_row(
        result_hit("r-61", "d2", 0.0, "collector_id", "node-01", 70000),
        {"d2": "ingest-flow"})
    healthy["incident_id"] = sp.incident_id(healthy)
    for _ in range(5):
        sp.apply_detector(incidents, [healthy], timelines_from(incidents), {})
    check(only(incidents)["healthy_windows"] == 1,
          f"the same healthy evaluation counted "
          f"{only(incidents)['healthy_windows']} times; the overlap window "
          f"re-reads results by design, so a replayed document must not "
          f"advance recovery")
    check(only(incidents)["episode_state"] == sp.RECOVERING,
          f"one healthy window resolved the episode "
          f"({only(incidents)['episode_state']})")


def test_repeated_firing_row_is_idempotent():
    stub_roster(["node-01"], {})
    incidents = {}
    firing = sp.anomaly_row(
        result_hit("r-70", "d2", 0.9, "collector_id", "node-01", 10000),
        {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    for _ in range(4):
        sp.apply_detector(incidents, [firing], timelines_from(incidents), {})
    ep = only(incidents)
    check(ep["member_count"] == 1,
          f"member_count grew to {ep['member_count']} while re-processing one "
          f"source; current alerts are fully rescanned every cycle, so counts "
          f"would climb forever")
    check(len(ep["signal_ids"]) == 1,
          "signal_ids must dedupe alongside member_count")

    alert = sp.alert_signal(
        alert_hit("a-idem", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    alert["incident_id"] = sp.incident_id(alert)
    incidents = {}
    for _ in range(4):
        sp.apply_monitor(incidents, [alert], latest_from(incidents), {})
    ep = only(incidents)
    check(ep["member_count"] == 1,
          f"monitor membership is not idempotent: {ep['member_count']}")


def test_notifications_can_be_linked_back_to_signals():
    stub_roster(["node-01"], {})
    row = sp.alert_signal(
        alert_hit("a-link", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    row["incident_id"] = sp.incident_id(row)
    incidents = {}
    sp.apply_monitor(incidents, [row], {}, {})
    payload = sp.alertmanager_payload(incidents)
    annotations = payload[0]["annotations"]
    check(annotations.get("signal_ids"),
          "the Alertmanager payload carries no signal_ids, so a delivered "
          "notification can never be tied back to what it covered")
    check(row["source_id"] in annotations["signal_ids"].split(","),
          "the firing signal is not listed in the payload it was grouped into")
    check(annotations.get("incident_id") == row["incident_id"],
          "the payload must also carry the stable incident id")
    check(annotations.get("episode_id") == row.get("episode_id"),
          f"the payload episode id {annotations.get('episode_id')!r} does not "
          f"match the signal's {row.get('episode_id')!r}, so a notification "
          f"for one episode could cover another")


def test_injected_pipeline_preserves_the_real_one():
    import yaml
    here = os.path.dirname(os.path.abspath(__file__))
    path = None
    for _ in range(6):
        candidate = os.path.join(here, "inject.yml")
        if os.path.exists(candidate):
            path = candidate
            break
        here = os.path.dirname(here)
    if path is None:
        print("[signal-contract] test_injected_pipeline_preserves_the_real_one"
              ": skipped, inject.yml not beside this checkout")
        return
    plays = yaml.safe_load(open(path))
    processors = None
    for play in plays:
        for task in play.get("tasks", []):
            body = (task.get("ansible.builtin.uri") or {}).get("body") or {}
            if "processors" in body:
                processors = body["processors"]
    check(processors is not None, "the drop-epn-stream pipeline is missing")
    if processors:
        check(any("drop" in p for p in processors),
              "the injected pipeline does not drop the target host")
        check(any((p.get("pipeline") or {}).get("name")
                  == "alice-add-ingest-time" for p in processors),
              "the injected pipeline replaces alice-add-ingest-time instead "
              "of delegating to it, so every non-target record would lose "
              "ingest_time and both lag fields")


def test_push_heartbeat_gate_runs_after_collector_cutover():
    here = os.path.dirname(os.path.abspath(__file__))
    site = None
    role = None
    for _ in range(7):
        candidate = os.path.join(here, "site.yml")
        task_dir = os.path.join(
            here, "roles", "dashboards", "tasks")
        if os.path.exists(candidate) and os.path.isdir(task_dir):
            site = open(candidate).read()
            role = task_dir
            break
        here = os.path.dirname(here)
    if site is None:
        print("[signal-contract] "
              "test_push_heartbeat_gate_runs_after_collector_cutover"
              ": skipped, site.yml not beside this checkout")
        return
    detection = open(os.path.join(role, "detection.yml")).read()
    projector = open(os.path.join(role, "projector.yml")).read()
    post = open(os.path.join(role, "post_collector.yml")).read()
    collector_at = site.find("- name: Fluent Bit collector")
    gate_at = site.find("- name: Post-collector detection gate")
    check(collector_at >= 0 and gate_at > collector_at,
          "the pushed-heartbeat gate runs before the collector role installs "
          "the heartbeat producer")
    check("Wait for the collectors to push" not in detection,
          "detector bootstrap still blocks on heartbeats before collector "
          "cutover")
    check('EXPECT_PUSH_HEARTBEATS: \"false\"' in detection
          and 'EXPECT_PUSH_HEARTBEATS: \"false\"' in projector,
          "a pre-collector verifier still requires pushed heartbeats")
    check("loop: \"{{ groups['workers'] }}\"" in post,
          "the post-collector gate does not wait for every worker")
    check("EXPECT_PUSH_HEARTBEATS: \"{{ collector_health_push_enabled"
          in post,
          "the final verifier does not enforce the pushed-heartbeat contract")


def test_detector_category_migration_recreates_instead_of_updating():
    here = os.path.dirname(os.path.abspath(__file__))
    script = None
    for _ in range(7):
        candidate = os.path.join(
            here, "roles", "dashboards", "templates", "detectors.sh.j2")
        if os.path.exists(candidate):
            script = open(candidate).read()
            break
        here = os.path.dirname(here)
    if script is None:
        print("[signal-contract] "
              "test_detector_category_migration_recreates_instead_of_updating"
              ": skipped, detectors.sh.j2 not beside this checkout")
        return
    marker = "CMP='\n"
    end = "\n'\n\nstop_detector"
    check(marker in script and end in script,
          "the detector comparator cannot be isolated for migration testing")
    if marker not in script or end not in script:
        return
    comparator = script.split(marker, 1)[1].split(end, 1)[0]
    desired = {
        "name": "ingest-flow",
        "category_field": ["collector_id"],
        "feature_attributes": [],
    }
    current = {
        "anomaly_detector": {
            "name": "ingest-flow",
            "category_field": ["node"],
            "feature_attributes": [],
        },
        "anomaly_detector_job": {"enabled": False},
    }
    with tempfile.NamedTemporaryFile("w") as desired_file:
        with tempfile.NamedTemporaryFile("w") as current_file:
            json.dump(desired, desired_file)
            json.dump(current, current_file)
            desired_file.flush()
            current_file.flush()
            result = subprocess.run(
                [sys.executable, "-c", comparator, desired_file.name,
                 current_file.name],
                check=True, capture_output=True, text=True)
    check(result.stdout.strip() == "changed-immutable",
          "a category-field migration is not classified as immutable")
    branch = script.split("case \"$state\" in", 1)[1]
    immutable = branch.split(";;", 1)[0]
    check('delete_detector_by_name "$name"' in immutable
          and 'create_detector "$name"' in immutable,
          "an immutable detector migration still attempts update-in-place")


def test_ops_actions_redirect_before_a_refresh_can_repeat_them():
    snapshot = {
        "count": "7488",
        "active_alerts": "0",
        "anomalies_last_hour": "5",
        "open_incidents": "0",
        "open_signals": "0",
        "incidents": [],
        "replay_running": True,
        "replay_workers": 2,
        "families": [["infologger", 7132], ["generic-log-other", 119]],
    }
    prior_snapshot = ops.snapshot
    prior_stop = ops.stop_only
    calls = []
    server = None
    thread = None
    try:
        ops.snapshot = lambda: snapshot

        def fake_stop():
            calls.append("stop")
            return ["test stop action ran exactly once"]

        ops.stop_only = fake_stop
        with ops._FLASH_LOCK:
            ops._FLASH_RESULTS.clear()
        server = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/stop")
        response = conn.getresponse()
        response.read()
        location = response.getheader("Location") or ""
        check(response.status == 303,
              f"ops stop returned HTTP {response.status}, so refreshing can "
              f"repeat the destructive POST")
        check(location.startswith("/ops/?result="),
              f"ops redirect lost its one-time action result: {location!r}")
        check(response.getheader("Cache-Control") == "no-store",
              "the ops POST redirect is cacheable")
        conn.close()

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", location)
        response = conn.getresponse()
        first_get = response.read().decode()
        check(response.status == 200,
              f"the redirected ops result returned HTTP {response.status}")
        check("test stop action ran exactly once" in first_get,
              "the POST/redirect/GET flow discarded the operator result")
        check("data-loading-label" in first_get
              and "button-spinner" in first_get
              and "aria-busy" in first_get,
              "the ops controls have no per-button loading state")
        conn.close()

        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", location)
        response = conn.getresponse()
        second_get = response.read().decode()
        check("test stop action ran exactly once" not in second_get,
              "the one-time ops result survives refresh instead of being "
              "consumed")
        check(calls == ["stop"],
              f"refresh executed the stop action {len(calls)} times")
        conn.close()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        ops.snapshot = prior_snapshot
        ops.stop_only = prior_stop
        with ops._FLASH_LOCK:
            ops._FLASH_RESULTS.clear()


def _checkout_file(*parts):
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        candidate = os.path.join(here, *parts)
        if os.path.exists(candidate):
            return candidate
        here = os.path.dirname(here)
    return None


def test_dynamic_services_can_read_the_signal_catalog():
    import yaml

    path = _checkout_file(
        "roles", "dashboards", "tasks", "bootstrap.yml")
    if path is None:
        print("[signal-contract] "
              "test_dynamic_services_can_read_the_signal_catalog: skipped, "
              "bootstrap.yml not beside this checkout")
        return
    tasks = yaml.safe_load(open(path))
    by_name = {task.get("name"): task for task in tasks}
    root = by_name.get("Ensure the bootstrap scripts directory exists", {})
    catalog = by_name.get(
        "Stage the signal identity catalog (one explicit per-monitor and "
        "per-detector classifier, never inferred from an index name)", {})
    probe = by_name.get(
        "Prove a sandbox-style unprivileged service can read and parse the "
        "signal identity catalog", {})
    check((root.get("ansible.builtin.file") or {}).get("mode") == "0755",
          "DynamicUser services cannot traverse the signal catalog's parent "
          "directory")
    check((catalog.get("ansible.builtin.copy") or {}).get("mode") == "0644",
          "the root-owned signal catalog is not readable by DynamicUser "
          "services")
    argv = (probe.get("ansible.builtin.command") or {}).get("argv") or []
    check("/usr/sbin/runuser" in argv and "nobody" in argv
          and any("json.load(open(" in str(arg) for arg in argv),
          "deployment does not prove an unprivileged service can parse the "
          "signal catalog")

    digest_path = _checkout_file(
        "roles", "dashboards", "tasks", "digest.yml")
    projector_path = _checkout_file(
        "roles", "dashboards", "tasks", "projector.yml")
    digest = open(digest_path).read() if digest_path else ""
    projector = open(projector_path).read() if projector_path else ""
    check("Wait for the digest to complete a new lossless traversal cycle"
          in digest and ".get('updated_at', 0)" in digest,
          "deploy does not require a post-start digest cycle to advance its "
          "watermark")
    check("Wait for the newest signal-projector heartbeat to prove a "
          "successful cycle" in projector
          and "projector_cycle_ok" in projector
          and "_projector_gate_started.stdout" in projector,
          "deploy accepts a systemd-active but functionally failed signal "
          "projector")


def test_deploy_preserves_the_replay_runtime_dropin():
    import yaml

    producer_path = _checkout_file(
        "roles", "producer", "tasks", "main.yml")
    replay_path = _checkout_file("replay.yml")
    unit_path = _checkout_file(
        "roles", "producer", "templates", "replay.service.j2")
    if not all((producer_path, replay_path, unit_path)):
        print("[signal-contract] "
              "test_deploy_preserves_the_replay_runtime_dropin: skipped, "
              "producer sources not beside this checkout")
        return
    producer_tasks = yaml.safe_load(open(producer_path))
    managed = []
    for task in producer_tasks:
        copy = task.get("ansible.builtin.copy") or {}
        if str(copy.get("dest", "")).endswith("/clock.conf"):
            managed.append(task.get("name"))
    check(not managed,
          f"ordinary deploy still overwrites replay runtime state: {managed}")

    replay = open(replay_path).read()
    unit = open(unit_path).read()
    check("service.d/clock.conf" in replay
          and "Environment=REPLAY_LOOP={{ replay_loop" in replay,
          "replay.yml no longer owns a complete loop-aware runtime drop-in")
    check("Environment=REPLAY_LOOP={{ replay_loop" in unit
          and "Environment=REPLAY_CLOCK={{ replay_clock" in unit,
          "fresh producer installs have no default replay runtime settings")


def test_status_exposes_functional_projector_and_replay_health():
    status_path = _checkout_file("status.yml")
    probe_path = _checkout_file(
        "roles", "dashboards", "files", "detection_status.py")
    if not all((status_path, probe_path)):
        print("[signal-contract] "
              "test_status_exposes_functional_projector_and_replay_health: "
              "skipped, status sources not beside this checkout")
        return
    status = open(status_path).read()
    probe = open(probe_path).read()
    check("dashboards_signal_projector_service_name" in status
          and "dashboards_notification_ingest_service_name" in status,
          "make status still omits signal-path systemd services")
    check("/replay-status" in status and "running=" in status and "loop=" in status,
          "make status does not reveal that deploy stopped the replay loop")
    check("cycle failed|FATAL|Permission denied" in status,
          "make status treats a retrying but functionally dead service as "
          "healthy")
    check("SIGNAL PROJECTOR (functional heartbeat)" in probe
          and "projector_cycle_ok" in probe,
          "the detection probe does not inspect the projector heartbeat")
    check("realtime raw anomalies above 0.5 exist" in probe,
          "the detection probe cannot distinguish an empty digest from a "
          "broken digest")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    for test in TESTS:
        before = len(failures)
        try:
            test()
        except Exception as e:
            failures.append(f"{test.__name__} raised {e!r}")
        status = "ok" if len(failures) == before else "FAIL"
        print(f"[signal-contract] {test.__name__}: {status}")
    if failures:
        for f in failures:
            print(f"[signal-contract] FATAL: {f}")
        return 1
    print(f"[signal-contract] OK — {len(TESTS)} contracts hold "
          f"(identity continuity, label completeness, episode lifecycle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
