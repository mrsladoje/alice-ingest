import ast
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# On the control host every module below is staged flat in /opt/alice-ingest,
# so the line above is enough. In the repository they are split across the
# roles that own them, so each of those files/ directories joins the path too.
_ROLES = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
for _sibling in ("alice_runtime", "alice_ops", "anomaly_detection",
                 "cockpit_metrics", "trend_rollup"):
    _dir = os.path.join(_ROLES, _sibling, "files")
    if os.path.isdir(_dir):
        sys.path.append(_dir)

os.environ.setdefault(
    "SIGNAL_CATALOG",
    next((p for p in (os.path.join(d, "signal_catalog.json")
                      for d in sys.path[:1] + sys.path[-5:])
          if os.path.exists(p)),
         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "signal_catalog.json")))

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
    """A bucket-level alert in the shape OpenSearch actually indexes.

    The key lives under agg_alert_content, never as a flat top-level
    bucket_keys. A fixture carrying the flat field validated the projector
    against a document the plugin never writes, so every bucket-level alert
    reached production unattributed while this suite stayed green.
    """
    src = {"id": alert_id, "monitor_name": monitor, "trigger_name": monitor,
           "state": state, "severity": severity, "start_time": start,
           "last_notification_time": start, "monitor_id": "m1"}
    if bucket_keys is not None:
        keys = list(bucket_keys) if isinstance(bucket_keys, list) \
            else [bucket_keys]
        src["agg_alert_content"] = {
            "parent_bucket_path": "composite_agg",
            "bucket_keys": keys,
            "bucket": {"key": {"entity": ",".join(str(k) for k in keys)},
                       "doc_count": 7}}
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


def test_alert_identity_uses_document_id_until_source_id_is_backfilled():
    stub_roster(["node-01"], {})
    live_hit = alert_hit(
        "a-generated", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
        ["node-01"])
    live_hit["_source"]["id"] = ""
    live = sp.alert_signal(live_hit, sp.ALERTS_CURRENT)
    moved = sp.alert_signal(
        alert_hit(
            "a-generated", ".opendistro-alerting-alert-history-000001",
            "COMPLETED", "collector-down", ["node-01"], end=2000),
        ".opendistro-alerting-alert-history-000001")
    check(live["source_id"] == "a-generated",
          "a new alert with an empty _source.id did not use its durable _id")
    check(live["source_uid"] == moved["source_uid"],
          "backfilling _source.id changed the signal identity after history move")

    mismatched = alert_hit(
        "document-id", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
        ["node-01"])
    mismatched["_source"]["id"] = "different-source-id"
    try:
        sp.alert_signal(mismatched, sp.ALERTS_CURRENT)
    except sp.os_cursor.CursorError:
        pass
    else:
        check(False, "a real _source.id/_id mismatch was silently accepted")


def test_anomaly_ingest_consumes_bounded_pages_in_event_order():
    stub_roster(["node-01"], {})
    first = result_hit(
        "r-page-1", "d2", 0.9, "collector_id", "node-01", 10000)
    second = result_hit(
        "r-page-2", "d2", 0.8, "collector_id", "node-01", 20000)
    first["_source"]["execution_end_time"] = 90000
    second["_source"]["execution_end_time"] = 80000
    pages = []
    captured = {}
    prior_scan = sp.os_cursor.scan
    prior_watermark = sp.watermark
    try:
        def fake_scan(os_url, target, query, sort_field, **kwargs):
            captured.update({
                "target": target,
                "query": query,
                "sort_field": sort_field,
                **kwargs,
            })
            yield [first]
            yield [second]

        sp.os_cursor.scan = fake_scan
        sp.watermark = lambda lane, minutes: 1000
        high, lane = sp.ingest_anomaly_results(
            {"d2": "ingest-flow"},
            lambda rows: pages.append([row["source_id"] for row in rows]))
    finally:
        sp.os_cursor.scan = prior_scan
        sp.watermark = prior_watermark

    check(pages == [["r-page-1"], ["r-page-2"]],
          f"anomaly traversal retained or merged PIT pages: {pages}")
    check(captured.get("sort_field") == "data_end_time",
          "anomaly lifecycle evidence is not traversed in event-time order")
    check(captured.get("keep_alive") == sp.PIT_KEEP_ALIVE,
          "the projector did not apply its extended PIT keep-alive")
    check((high, lane) == (90000, "projector-anomalies"),
          f"the execution watermark changed under event-order traversal: "
          f"{(high, lane)}")


def test_bulk_signal_writes_are_bounded():
    prior_bulk = sp.os_cursor.bulk
    prior_limit = sp.BULK_DOCUMENTS
    calls = []
    try:
        def fake_bulk(os_url, lines, refresh="false", timeout=120):
            calls.append(len(lines) // 2)
            return len(lines) // 2, []

        sp.os_cursor.bulk = fake_bulk
        sp.BULK_DOCUMENTS = 2
        written = sp.write_signals([
            {"source_kind": "detector", "source_uid": str(index)}
            for index in range(5)
        ])
    finally:
        sp.os_cursor.bulk = prior_bulk
        sp.BULK_DOCUMENTS = prior_limit
    check(calls == [2, 2, 1],
          f"signal bulk requests were not bounded at two documents: {calls}")
    check(written == 5, f"bounded signal writes reported {written}, expected 5")


def test_retention_is_async_and_runs_after_the_functional_heartbeat():
    prior_request = sp.os_cursor.request
    prior_retention = sp.RETENTION_DAYS
    prior_log = sp.log
    calls = []
    try:
        def fake_request(os_url, method, path, payload=None, **kwargs):
            calls.append((method, path, payload, kwargs))
            return 200, {"task": f"retention-{len(calls)}"}

        sp.os_cursor.request = fake_request
        sp.RETENTION_DAYS = 30
        sp.log = lambda message: None
        sp.prune()
    finally:
        sp.os_cursor.request = prior_request
        sp.RETENTION_DAYS = prior_retention
        sp.log = prior_log

    check(len(calls) == 3,
          f"retention scheduled {len(calls)} jobs instead of three")
    check(all("wait_for_completion=false" in path for _, path, _, _ in calls),
          f"a projector retention request can still block: {calls}")
    check(all(kwargs.get("timeout") == 30 for _, _, _, kwargs in calls),
          f"retention scheduling lost its bounded client timeout: {calls}")

    import inspect
    main = inspect.getsource(sp.main)
    heartbeat_at = main.find("heartbeat(result")
    prune_at = main.find("prune()")
    check(heartbeat_at >= 0 and prune_at > heartbeat_at,
          "the projector can still run retention before its first functional "
          "heartbeat")


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


def test_every_signal_has_an_operator_diagnosis():
    declared = signal_identity.monitor_names() | signal_identity.detector_names()
    presented = (set(signal_identity.MONITOR_PRESENTATION)
                 | set(signal_identity.DETECTOR_PRESENTATION))
    check(declared == presented,
          f"operator presentation drift: missing={sorted(declared-presented)} "
          f"undeclared={sorted(presented-declared)}")
    for name in sorted(declared):
        item = signal_identity.presentation(name)
        for field in ("title", "diagnosis", "action"):
            check(bool(item.get(field)),
                  f"{name} has no operator-facing {field}")

    stub_roster(["node-01"], {})
    row = sp.alert_signal(
        alert_hit("diagnosis-1", sp.ALERTS_CURRENT, "ACTIVE",
                  "collector-down", ["node-01"]),
        sp.ALERTS_CURRENT)
    row["incident_id"] = sp.incident_id(row)
    episode = sp.blank_incident(
        row, row["incident_id"], row["first_seen"])
    check(episode["title"] == "Collector stopped heartbeating",
          f"episode title is not human-facing: {episode.get('title')!r}")
    check("node-01" in episode["diagnosis"]
          and episode["affected"] == "collector node-01",
          "episode diagnosis does not state the affected collector")
    check("Fluent Bit" in episode["operator_action"],
          "episode does not tell the operator what to inspect next")


def test_cockpit_headlines_episodes_not_raw_detector_exhaust():
    generator = _checkout_file(
        "roles", "dashboards", "files", "gen_cockpit.py")
    if not generator:
        print("[signal-contract] "
              "test_cockpit_headlines_episodes_not_raw_detector_exhaust: "
              "skipped, generator not beside this checkout")
        return
    proc = subprocess.run(
        [sys.executable, generator], capture_output=True, text=True,
        timeout=30)
    check(proc.returncode == 0,
          f"cockpit generator failed: {proc.stderr[-500:]}")
    if proc.returncode:
        return
    objects = [json.loads(line) for line in proc.stdout.splitlines()
               if line.strip()]
    dashboard = next(o for o in objects if o.get("type") == "dashboard")
    references = {r["id"] for r in dashboard.get("references", [])}
    check({"alice-viz-open-incidents", "alice-viz-incidents"} <= references,
          "cockpit does not headline the incident episode summary and board")
    raw_panels = {
        "alice-viz-active-alerts", "alice-viz-anomaly-count",
        "alice-viz-alerts", "alice-viz-anomalies",
        "alice-search-active-alerts", "alice-search-anomalies",
        "alice-search-signals",
    }
    check(not (raw_panels & references),
          "raw alerts/anomaly windows are still dashboard panels: "
          f"{sorted(raw_panels & references)}")
    board = next(o for o in objects
                 if o.get("id") == "alice-viz-incidents")
    board_state = json.loads(board["attributes"]["visState"])
    check(board_state.get("type") == "vega"
          and "diagnosis" in board_state["params"]["spec"]
          and "operator_action" not in board_state["params"]["spec"],
          "episode board must render the diagnosis and no next-action line")
    spec = board_state["params"]["spec"]
    check("alice-search-signals?_g=(time:" in spec
          and "alice-search-incident-history?_g=(time:" in spec
          and spec.count("_q=(query:(language:kuery,query:") >= 2,
          "episode board does not link cards to their signals and detail")
    check("winFrom" in spec and "winTo" in spec
          and "opened_at" in spec and "last_seen" in spec,
          "episode links do not carry the episode's own time window, so "
          "Discover opens on the leftover picker range and undercounts")
    board_data = json.loads(spec)["data"][0]
    check(board_data["format"]["property"] == "aggregations.groups.buckets"
          and board_data["url"]["body"]["aggs"]["groups"]["terms"]["field"]
          == "group_id",
          "the board must aggregate open episodes into notification groups; "
          "listing raw episode documents puts one card per entity on screen "
          "and a single rule can then fill the whole board")
    group_aggs = board_data["url"]["body"]["aggs"]["groups"]["aggs"]
    counted = group_aggs["entity_total"]["cardinality"]["field"]
    check(group_aggs["entities"]["terms"]["field"] == "entity_id"
          and counted == "entity_id",
          "a grouped card must name the affected entities and count them, or "
          "grouping only hides the fan-out instead of summarising it")
    links = {t["as"]: t.get("expr", "")
             for data in json.loads(spec)["data"]
             for t in data.get("transform", [])
             if isinstance(t.get("as"), str)}
    check("group_id" in links.get("signalsUrl", "")
          and "episode_id" not in links.get("signalsUrl", ""),
          "Signals must scope to the group whose card was clicked, so every "
          "affected entity's rows arrive together")
    check("group_id" in links.get("detailsUrl", "")
          and "episode_id" not in links.get("detailsUrl", "")
          and "from:now-" in links.get("detailsUrl", ""),
          "Details must scope to the group over a lookback window, so it "
          "shows one row per affected entity and how often each came back")
    shadowed = sorted(set(links) & set(group_aggs))
    check(not shadowed,
          f"board formulas shadow the aggregation objects they read: "
          f"{shadowed}. A formula named after a sub-aggregation replaces that "
          f"object with a scalar, so every later datum.<name>.value is "
          f"undefined and the link windows become NaN.")

    import re
    read = set()
    for expr in links.values():
        read |= set(re.findall(
            r"datum\.(\w+)\.(?:value|doc_count|buckets)", expr))
    undeclared = sorted(read - set(group_aggs))
    check(not undeclared,
          f"board expressions read sub-aggregations the query never declares: "
          f"{undeclared}. Vega resolves those to undefined and prints them on "
          f"the card rather than failing, so the typo reaches the operator.")

    children = group_aggs.get("children", {}).get("terms", {})
    check(children.get("field") == "entity_id"
          and children.get("size", 0) > len(group_aggs["entities"]["terms"]),
          "a card must carry its affected entities, not only a sample of "
          "three; without them the board can never show what a group folded "
          "in without a second query per card")
    kid_aggs = group_aggs.get("children", {}).get("aggs", {})
    check({"kid_last", "kid_open", "kid_stale"} <= set(kid_aggs),
          "each child entity needs its own state and last-seen, or the "
          "unfolded list repeats the parent card instead of separating the "
          "entity that recovered from the one still open")
    check("top_hits" not in json.dumps(kid_aggs),
          "child rows must summarise with metric aggregations: a top_hits "
          "per entity multiplies into hundreds of stored hits on every board "
          "refresh")
    check("group_id" in links.get("kidUrl", "")
          and "entity_id" in links.get("kidUrl", ""),
          "a child row must open that one entity's history, scoped to the "
          "group and the entity; scoping to the group alone repeats DETAILS")

    spec_obj = json.loads(spec)
    signals = {s["name"]: s for s in spec_obj.get("signals", [])}
    check("expandedKey" in signals,
          "the board declares no expansion signal, so cards cannot unfold")
    clicked = {e.get("markname")
               for handler in signals.get("expandedKey", {}).get("on", [])
               for e in handler.get("events", [])}
    named = {m.get("name") for m in spec_obj["marks"][0].get("marks", [])}
    check({"cardChevron", "cardTitle", "cardMeta", "cardDiag"} <= clicked,
          "every drawn part of a card must answer the expand click. Vega "
          "sends a click to the topmost mark only, so a handler on the card "
          "background alone makes clicking the title do nothing.")
    check(clicked <= named,
          f"expand handlers name marks the board never draws: "
          f"{sorted(clicked - named)}")
    check("expandedRow" in signals and "expandedBlock" in signals
          and "expandedBlock" in signals["contentHeight"]["update"],
          "the scroll height must grow by the open card's children, or the "
          "unfolded rows fall outside the scrollable area and cannot be "
          "reached")

    import verify_detection as verify
    prior_path = verify.COCKPIT_NDJSON
    try:
        verify.COCKPIT_NDJSON = _checkout_file(
            "roles", "dashboards", "files", "cockpit.ndjson")
        body, prop = verify.board_query()
    finally:
        verify.COCKPIT_NDJSON = prior_path
    check(body is not None and prop == "aggregations.groups.buckets",
          f"the deploy gate cannot extract the board query from the shipped "
          f"saved objects (property {prop!r}); it would pass without ever "
          f"running the query it exists to run")
    check(body == board_data["url"]["body"],
          "the gate and the panel disagree on the query body, so the deploy "
          "would validate a query the operator never loads")
    ids = {o["id"] for o in objects}
    check("alice-search-incident-history" in ids,
          "the episode-history saved search the Details button opens is "
          "missing from the cockpit objects")
    check('"renderer": "svg"' in spec,
          "episode board must render as svg or its links are dead pixels")


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


def test_detector_episode_state_survives_page_boundaries():
    stub_roster(["node-01"], {})
    meta = signal_identity.detector("ingest-flow")
    required = int(meta["healthy_windows_required"])
    incidents = {}
    timelines = {}

    firing = sp.anomaly_row(
        result_hit("r-page-open", "d2", 0.9, "collector_id", "node-01",
                   10000), {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(incidents, [firing], timelines, {})

    for index in range(required):
        healthy = sp.anomaly_row(
            result_hit(
                f"r-page-healthy-{index}", "d2", 0.0, "collector_id",
                "node-01", 70000 + index * 60000),
            {"d2": "ingest-flow"})
        healthy["incident_id"] = sp.incident_id(healthy)
        sp.apply_detector(incidents, [healthy], timelines, {})

    key = firing["incident_id"]
    check(len(timelines.get(key, [])) == 1,
          "page-local detector state was not retained in the shared timeline")
    check(only(incidents)["episode_state"] == sp.RESOLVED,
          "healthy evidence on later pages did not resolve the opened episode")


def test_detector_prefetch_recovers_terminal_episode_outside_timeline_window():
    stub_roster(["node-01"], {})
    meta = signal_identity.detector("ingest-flow")
    required = int(meta["healthy_windows_required"])
    seed = {}
    seed_timelines = {}
    firing = sp.anomaly_row(
        result_hit("r-old-open", "d2", 0.9, "collector_id", "node-01",
                   10000), {"d2": "ingest-flow"})
    firing["incident_id"] = sp.incident_id(firing)
    sp.apply_detector(seed, [firing], seed_timelines, {})
    for index in range(required):
        healthy = sp.anomaly_row(
            result_hit(
                f"r-old-close-{index}", "d2", 0.0, "collector_id",
                "node-01", 70000 + index * 60000),
            {"d2": "ingest-flow"})
        healthy["incident_id"] = sp.incident_id(healthy)
        sp.apply_detector(seed, [healthy], seed_timelines, {})
    persisted = only(seed)
    eid = persisted["episode_id"]
    check(persisted["episode_state"] == sp.RESOLVED,
          "the prefetch fixture did not create a terminal episode")

    timelines = {}
    stored = {}
    requested = []
    prior_fetch = sp.fetch_episodes
    try:
        def fake_fetch(ids):
            requested.append(set(ids))
            return {eid: persisted} if eid in ids else {}

        sp.fetch_episodes = fake_fetch
        firing.pop("episode_id", None)
        sp.prefetch_detector_episodes([firing], timelines, stored)
    finally:
        sp.fetch_episodes = prior_fetch

    replay = {}
    sp.apply_detector(replay, [firing], timelines, stored)
    check(requested == [{eid}],
          f"the page did not request its persisted episode by exact ID: "
          f"{requested}")
    check(list(replay) == [eid]
          and replay[eid]["episode_state"] == sp.RESOLVED,
          "a replayed old firing row reopened or duplicated a terminal episode")


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


def test_interrupted_anomaly_traversal_never_advances_watermarks():
    stub_roster(["node-01"], {})
    row = sp.anomaly_row(
        result_hit("r-interrupted", "d2", 0.9, "collector_id", "node-01",
                   10000), {"d2": "ingest-flow"})
    names = [
        "detector_names", "snapshot_current_alerts", "ingest_alert_history",
        "ingest_anomaly_results", "load_episode_timelines", "fetch_episodes",
        "write_signals",
    ]
    prior = {name: getattr(sp, name) for name in names}
    prior_write_watermark = sp.os_cursor.write_watermark
    signal_batches = []
    watermark_writes = []
    interrupted = False
    try:
        sp.detector_names = lambda: {"d2": "ingest-flow"}
        sp.snapshot_current_alerts = lambda: []
        sp.ingest_alert_history = lambda: (
            [], 5000, "projector-alert-history")
        sp.load_episode_timelines = lambda: {}
        sp.fetch_episodes = lambda ids: {}
        sp.write_signals = lambda rows: signal_batches.append(
            [item["source_id"] for item in rows]) or len(rows)

        def fail_after_first_page(detectors, consume):
            consume([dict(row)])
            raise sp.os_cursor.CursorError("simulated PIT loss")

        sp.ingest_anomaly_results = fail_after_first_page
        sp.os_cursor.write_watermark = lambda *args, **kwargs: (
            watermark_writes.append((args, kwargs)))
        try:
            sp.cycle()
        except sp.os_cursor.CursorError:
            interrupted = True
    finally:
        for name, value in prior.items():
            setattr(sp, name, value)
        sp.os_cursor.write_watermark = prior_write_watermark

    check(interrupted, "the simulated interrupted PIT traversal did not fail")
    check(signal_batches == [["r-interrupted"]],
          f"the completed page was not written idempotently: {signal_batches}")
    check(not watermark_writes,
          "an interrupted traversal advanced a watermark and could lose rows")


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


def test_incident_identity_ignores_cluster_id():
    stub_roster(["node-01"], {})
    row = sp.alert_signal(
        alert_hit("a-cluster", sp.ALERTS_CURRENT, "ACTIVE", "collector-down",
                  ["node-01"]), sp.ALERTS_CURRENT)
    other = dict(row)
    other["cluster_id"] = "some-other-cluster"
    check(sp.incident_id(row) == sp.incident_id(other),
          "cluster_id is a constant on this stack and must not be hashed into "
          "incident_id")


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
    import inspect
    import inject_run as engine

    calls = []
    original_agent, original_search = engine._agent, engine._search
    original_projector = engine.PROJECTOR_AGENT
    original_status = engine.STATUS_PATH
    engine.PROJECTOR_AGENT = "http://projector-host:8089"
    engine.STATUS_PATH = os.path.join(
        tempfile.mkdtemp(prefix="inject-contract-"), "status.json")
    started_at = []

    def fake_agent(base, path, query=None):
        started_at.append(engine.now_ms())
        calls.append(("agent", path, dict(query or {})))
        return ""

    def fake_search(index, query):
        calls.append(("search", index, json.dumps(query)))
        return 1

    try:
        engine._agent = fake_agent
        engine._search = fake_search
        with redirect_stdout(StringIO()):
            engine.restore({"scenario": "stop-projector", "restore": True,
                            "target_worker": ""})
    finally:
        engine._agent, engine._search = original_agent, original_search
        engine.PROJECTOR_AGENT = original_projector
        engine.STATUS_PATH = original_status

    kinds = [call[0] for call in calls]
    check(kinds == ["agent", "search"],
          f"the stop-projector restore did not restart the projector and then "
          f"wait for it: {kinds}")
    if kinds == ["agent", "search"]:
        check(calls[0][1] == "/service-start",
              "the projector was never started again after the drill")
        barrier = calls[1][2]
        check("projector_cycle_ok" in barrier,
              "the catch-up barrier does not require a successful cycle")
        window = json.loads(barrier)["bool"]["filter"][2]["range"]["@timestamp"]
        check(int(window["gte"]) >= started_at[0] - 1000,
              "the catch-up barrier accepts a cycle older than the restart "
              "boundary, so a stale heartbeat would read as recovery")

    source = inspect.getsource(engine.main)
    check(source.index("restore(req)") < source.index("score(req"),
          "scoring runs before the injected component is restored, so the "
          "scorecard would measure the fault instead of the recovery")


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


def _trend_rows(specs):
    history = ".opendistro-alerting-alert-history-000001"
    hits = []
    for aid, state, start, end in specs:
        index = sp.ALERTS_CURRENT if state == "ACTIVE" else history
        hits.append(alert_hit(aid, index, state, "trend-other-volume",
                              ["epn001"], start=start, end=end))
    rows = sp.merge_alert_rows(
        [sp.alert_signal(h, h["_index"]) for h in hits])
    for row in rows:
        row["incident_id"] = sp.incident_id(row)
    return rows


def test_flapping_trend_monitor_stays_in_one_episode():
    stub_roster(["node-01"], {"epn001": "node-01"})
    rows = _trend_rows([("a-t-1", "COMPLETED", 1000, 2000),
                        ("a-t-2", "COMPLETED", 3000, 4000),
                        ("a-t-3", "ACTIVE", 5000, None)])
    key = rows[0]["incident_id"]
    episodes = {}
    sp.apply_monitor(episodes, rows, {}, {})
    check(sorted(episodes) == [sp.episode_id(key, 1000)],
          f"a flapping trend monitor split into {sorted(episodes)}; clears "
          f"below its healthy-window requirement must not close an episode")
    ep = episodes[sp.episode_id(key, 1000)]
    check(ep["member_count"] == 3 and ep["episode_state"] == sp.OPEN,
          f"the flapping episode reports {ep['member_count']} members in "
          f"state {ep['episode_state']}; it must fold all three and reopen")


def test_trend_monitor_still_closes_after_its_healthy_windows():
    stub_roster(["node-01"], {"epn001": "node-01"})
    rows = _trend_rows([("a-c-1", "COMPLETED", 1000, 1500),
                        ("a-c-2", "COMPLETED", 2000, 2500),
                        ("a-c-3", "COMPLETED", 3000, 3500),
                        ("a-c-4", "ACTIVE", 5000, None)])
    key = rows[0]["incident_id"]
    episodes = {}
    sp.apply_monitor(episodes, rows, {}, {})
    check(sorted(episodes) == sorted([sp.episode_id(key, 1000),
                                      sp.episode_id(key, 5000)]),
          f"hysteresis never releases: got {sorted(episodes)}; three clears "
          f"must close the episode so a later breach opens its own")
    check(episodes[sp.episode_id(key, 1000)]["episode_state"] == sp.RESOLVED,
          "the episode that met its healthy-window requirement stayed open")


def test_a_replayed_clear_cannot_count_twice_toward_closing():
    stub_roster(["node-01"], {"epn001": "node-01"})
    hit = alert_hit("a-r-linger", sp.ALERTS_CURRENT, "COMPLETED",
                    "trend-other-volume", ["epn001"], start=1000, end=1100)
    episodes = {}
    for cycle in range(1, 7):
        rows = sp.merge_alert_rows([sp.alert_signal(hit, sp.ALERTS_CURRENT)])
        for row in rows:
            row["incident_id"] = sp.incident_id(row)
        sp.apply_monitor(episodes, rows, latest_from(episodes),
                         dict(episodes))
        ep = only(episodes)
        check(ep["healthy_windows"] == 1
              and ep["episode_state"] == sp.RECOVERING,
              f"on cycle {cycle} one re-read clear had counted "
              f"{ep['healthy_windows']} times and left the episode "
              f"{ep['episode_state']}; a re-read clear must not close an "
              f"episode that requires three distinct ones")


def test_a_bucket_alert_names_the_entity_opensearch_actually_indexes():
    """The fixture is written out literally, not built by alert_hit().

    The helper and the projector can drift together into agreeing on a
    document the plugin never writes. Only a hand-written copy of the real
    alert keeps that agreement honest.
    """
    stub_roster(["node-01"], {"epn001": "node-01"})
    indexed = {
        "_id": "a-plugin-shape", "_index": sp.ALERTS_CURRENT,
        "_source": {
            "id": "a-plugin-shape", "monitor_name": "trend-other-volume",
            "trigger_name": "trend-other-volume", "state": "ACTIVE",
            "severity": "2", "start_time": 1000,
            "last_notification_time": 1000, "monitor_id": "m1",
            "agg_alert_content": {
                "parent_bucket_path": "composite_agg",
                "bucket_keys": ["epn001"],
                "bucket": {"key": {"entity": "epn001"}, "doc_count": 7}}}}
    row = sp.alert_signal(indexed, sp.ALERTS_CURRENT)
    check(row["entity_id"] == "epn001" and row["entity_kind"] == "epn",
          f"the projector read {row['entity_kind']}/{row['entity_id']} out of "
          f"the document OpenSearch indexes. Alerting nests the key under "
          f"agg_alert_content and exposes a flat bucket_keys only to the "
          f"mustache context, so reading the flat field leaves every "
          f"per-entity breach anonymous while the action payload still names "
          f"the entity")
    check(row["class"] == sp.SINGLE,
          f"a fully-keyed bucket alert was classed {row['class']}, so a "
          f"working monitor is reported as a broken rule")
    check(row["collector_id"] == "node-01",
          f"the named entity did not resolve to its collector: "
          f"{row['collector_id']}")
    check(row["evidence"]["bucket_keys"] == "epn001",
          f"evidence dropped the key: {row['evidence']['bucket_keys']!r}")
    helper = alert_hit("a-helper", sp.ALERTS_CURRENT, "ACTIVE",
                       "trend-other-volume", ["epn001"], severity="2")
    check("bucket_keys" not in helper["_source"]
          and ((helper["_source"].get("agg_alert_content") or {})
               .get("bucket_keys") == ["epn001"]),
          f"alert_hit() stopped building the indexed shape: "
          f"{helper['_source'].get('agg_alert_content')!r}. Every other "
          f"bucket-level test in this file reads that fixture, so they would "
          f"all go back to proving nothing")


def test_bucket_key_gate_runs_only_after_the_projector_restart():
    """The deploy gate for keyless bucket alerts, and the bound it needs.

    verify_detection runs three times in a deploy. Only the pass after the
    projector restarts carries GROUPING_SINCE as that restart instant. The two
    earlier passes leave it relative, and there rows written by the previous
    projector would fail the very deploy that replaces it.
    """
    verifier = _checkout_file(
        "roles", "anomaly_detection", "files", "verify_detection.py")
    if verifier is None:
        print("[signal-contract] "
              "test_bucket_key_gate_runs_only_after_the_projector_restart"
              ": skipped, roles/ not beside this checkout")
        return
    import verify_detection as verify
    with open(verifier) as fh:
        main = next(n for n in ast.parse(fh.read()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
    guarded, unguarded = set(), set()
    for node in main.body:
        called = {c.func.id for c in ast.walk(node)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) \
                and node.test.id == "CHECK_EPISODE_GROUPING":
            guarded |= called
        else:
            unguarded |= called
    check("check_bucket_alert_entities" in guarded
          and "check_bucket_alert_entities" not in unguarded,
          "the bucket-key gate does not sit behind CHECK_EPISODE_GROUPING. "
          "The bootstrap and post-collector passes run before the projector "
          "restarts, so entity-missing rows the previous projector wrote "
          "would fail the deploy that fixes them")

    seen = []

    def dirty(method, path, payload=None):
        seen.append((path, payload))
        return 200, {"hits": {"total": {"value": 12}},
                     "aggregations": {"rules": {"buckets": [
                         {"key": "trend-il-volume", "doc_count": 12}]}}}

    prior = verify.req
    try:
        verify.req = dirty
        dirty_errors = []
        verify.check_bucket_alert_entities(dirty_errors, {"trend-il-volume"})
        verify.req = lambda *a, **k: (200, {"hits": {"total": {"value": 0}}})
        clean_errors = []
        with redirect_stdout(StringIO()):
            verify.check_bucket_alert_entities(
                clean_errors, {"trend-il-volume"})
        verify.req = lambda *a, **k: (404, {})
        absent_errors = []
        verify.check_bucket_alert_entities(absent_errors, {"trend-il-volume"})
    finally:
        verify.req = prior
    check(len(dirty_errors) == 1 and "trend-il-volume" in dirty_errors[0],
          f"a cluster projecting keyless bucket alerts did not fail the "
          f"deploy: {dirty_errors}")
    check(not clean_errors and not absent_errors,
          f"the gate fails a healthy cluster ({clean_errors}) or one whose "
          f"signals index does not exist yet ({absent_errors})")
    filters = seen[0][1]["query"]["bool"]["filter"]
    check(seen[0][0].startswith(f"/{verify.SIGNALS_INDEX}"),
          f"the gate reads {seen[0][0]}, not the signals index")
    check({"term": {"class": "entity-missing"}} in filters,
          f"the gate does not select the entity-missing class: {filters}")
    check({"range": {"last_seen": {"gte": verify.GROUPING_SINCE}}} in filters,
          f"the gate is not scoped to this projector's own output: {filters}")


def test_a_bucket_alert_with_no_key_never_becomes_a_fleet_incident():
    stub_roster(["node-01"], {"epn001": "node-01"})
    named = sp.alert_signal(
        alert_hit("a-named", sp.ALERTS_CURRENT, "ACTIVE", "trend-other-volume",
                  ["epn001"], severity="2"), sp.ALERTS_CURRENT)
    for keys in (None, [], [""], ["  "]):
        row = sp.alert_signal(
            alert_hit("a-keyless", sp.ALERTS_CURRENT, "ACTIVE",
                      "trend-other-volume", keys, severity="2"),
            sp.ALERTS_CURRENT)
        check(row["entity_id"] != signal_identity.sentinel("entity_id"),
              f"bucket_keys={keys!r} produced the sentinel entity. Hashing it "
              f"into incident_id folds every EPN's breach into one anonymous "
              f"incident that says 'whole fleet' and names nobody.")
        check(row["class"] == sp.ENTITY_MISSING
              and row["entity_kind"] == "monitor"
              and row["entity_id"] == "trend-other-volume",
              f"bucket_keys={keys!r} was not attributed to the monitor: "
              f"{row['entity_kind']}/{row['entity_id']} class {row['class']}")
        check(sp.incident_id(row) != sp.incident_id(named),
              f"bucket_keys={keys!r} shares an incident with a real breach of "
              f"the same rule")
        ep = sp.blank_incident(row, sp.incident_id(row), row["first_seen"])
        check(ep["affected"] == "monitor trend-other-volume"
              and "without naming an entity" in ep["title"],
              f"the operator is not told the rule is broken: "
              f"{ep['title']!r} / {ep['affected']!r}")


def test_an_orphaned_anonymous_episode_closes_instead_of_paging_forever():
    stub_roster(["node-01"], {"epn001": "node-01"})
    named = sp.alert_signal(
        alert_hit("a-keep", sp.ALERTS_CURRENT, "ACTIVE", "trend-other-volume",
                  ["epn001"], severity="2"), sp.ALERTS_CURRENT)
    named["incident_id"] = sp.incident_id(named)
    incidents = {}
    sp.apply_monitor(incidents, [named], {}, {})

    stranded = dict(list(incidents.values())[0])
    stranded["entity_id"] = signal_identity.sentinel("entity_id")
    stranded["episode_id"] = "stranded.1000"
    stranded["episode_state"] = sp.OPEN
    stranded["state"] = "firing"
    incidents["stranded.1000"] = stranded

    sp.age_episodes(incidents, 900000)
    check(stranded["episode_state"] == sp.CLOSED_EXPECTED
          and stranded["state"] == "resolved",
          f"an episode built on the sentinel entity stayed "
          f"{stranded['episode_state']}. Nothing produces that identity any "
          f"more, so it can never gather a healthy window and would re-send "
          f"to Alertmanager and hold a cockpit card for ever.")
    live = [ep for eid, ep in incidents.items() if eid != "stranded.1000"]
    check(all(ep["episode_state"] == sp.OPEN for ep in live),
          "closing the orphan also closed the real per-entity episodes")


def test_monitor_execution_error_is_the_monitors_own_incident():
    stub_roster(["node-01"], {})
    hit = alert_hit("a-err", sp.ALERTS_CURRENT, "ERROR", "collector-down",
                    None)
    hit["_source"]["error_message"] = "x" * (sp.MAX_ERROR_NOTE + 500)
    row = sp.alert_signal(hit, sp.ALERTS_CURRENT)
    check(row["class"] == sp.MONITOR_ERROR
          and row["entity_id"] == "collector-down"
          and row["notification_scope"] == "fleet",
          f"an execution error was projected as an ordinary breach: "
          f"{row['class']} {row['entity_kind']}/{row['entity_id']} "
          f"scope {row['notification_scope']}")
    check(len(row["evidence"]["note"]) == sp.MAX_ERROR_NOTE,
          f"the error message was stored at {len(row['evidence']['note'])} "
          f"characters; an unbounded keyword can exceed the Lucene term limit "
          f"and reject the whole bulk request")
    ep = sp.blank_incident(row, sp.incident_id(row), row["first_seen"])
    check("cannot run" in ep["title"] and "collector-down" in ep["diagnosis"],
          f"the card does not say the rule cannot run: {ep['title']!r}")
    check(sp.classify_mass_silence([row]) is None,
          "a broken collector-down monitor was counted as a silent collector, "
          "so one monitor error could page as fleet-wide silence")


def test_the_deploy_gate_fails_on_a_board_query_opensearch_rejects():
    fixture = _checkout_file("roles", "dashboards", "files", "cockpit.ndjson")
    if not fixture:
        print("[signal-contract] "
              "test_the_deploy_gate_fails_on_a_board_query_opensearch_rejects: "
              "skipped, cockpit.ndjson not beside this checkout")
        return
    import verify_detection as verify

    body, prop = None, None
    prior_path = verify.COCKPIT_NDJSON
    try:
        verify.COCKPIT_NDJSON = fixture
        body, prop = verify.board_query()
    finally:
        verify.COCKPIT_NDJSON = prior_path
    if body is None:
        check(False, "cannot stage the board-query gate fixture")
        return
    group_agg = prop.split(".")[1]
    declared = sorted(body["aggs"][group_agg]["aggs"])

    def bucket(**overrides):
        row = {"key": "alice-logs/il-per-epn/fleet", "doc_count": 3}
        row.update({name: {"value": 1} for name in declared})
        row.update(overrides)
        return row

    def run(reply, path=prior_path):
        errors = []
        prior_req, prior_ndjson = verify.req, verify.COCKPIT_NDJSON
        try:
            verify.req = lambda *a, **k: reply
            verify.COCKPIT_NDJSON = path
            with redirect_stdout(StringIO()):
                verify.check_cockpit_board_query(errors)
        finally:
            verify.req = prior_req
            verify.COCKPIT_NDJSON = prior_ndjson
        return errors

    good = (200, {"aggregations": {group_agg: {"buckets": [bucket()]}}})
    check(not run(good, fixture),
          "the gate fails on a healthy board response")

    rejected = (400, {"error": {"type": "aggregation_execution_exception",
                                "reason": "no mapping found for [group_id]"}})
    errors = run(rejected, fixture)
    check(errors and "rejects" in errors[0] and "400" in errors[0],
          f"a board query OpenSearch refuses did not fail the deploy: "
          f"{errors}. That is the whole point of the gate — the panel would "
          f"draw an empty board and say nothing about why.")

    stripped = bucket()
    stripped.pop(declared[0])
    errors = run((200, {"aggregations": {group_agg: {"buckets": [stripped]}}}),
                 fixture)
    check(errors and declared[0] in errors[0],
          f"a bucket missing the {declared[0]!r} sub-aggregation passed; "
          f"every card expression reads it and would render undefined: "
          f"{errors}")

    empty = tempfile.NamedTemporaryFile(
        "w", suffix=".ndjson", delete=False)
    empty.write('{"type": "search", "id": "not-the-board"}\n')
    empty.close()
    errors = run(good, empty.name)
    check(errors and "incident board" in errors[0],
          f"a cockpit shipped without its incident board passed the gate: "
          f"{errors}")


def test_a_stale_error_message_cannot_steal_a_named_breach():
    stub_roster(["node-01"], {"epn001": "node-01"})
    hit = alert_hit("a-lingering", sp.ALERTS_CURRENT, "ACTIVE",
                    "trend-other-volume", ["epn001"], severity="2")
    hit["_source"]["error_message"] = "failed to evaluate 40 minutes ago"
    row = sp.alert_signal(hit, sp.ALERTS_CURRENT)
    check(row["class"] == sp.SINGLE and row["entity_id"] == "epn001"
          and row["entity_kind"] == "epn",
          f"an active alert that names epn001 was reclassified as "
          f"{row['class']} on {row['entity_kind']}/{row['entity_id']}. A "
          f"recovered alert can keep the error message from an earlier "
          f"failure, so reading the message as the class loses a real "
          f"per-entity breach — the exact fault this change prevents.")


def test_a_detector_result_with_no_entity_is_dropped_not_averaged():
    stub_roster(["node-01"], {})
    hit = result_hit("r-anon", "d1", 0.9, "origin_host", "epn001", 5000)
    hit["_source"].pop("entity")
    sp._dropped_rows.clear()
    row = sp.anomaly_row(hit, {"d1": "il-per-epn"})
    check(row is None,
          "a high-cardinality anomaly result carrying no entity became a "
          "signal; its sentinel entity would fold every EPN's anomalies into "
          "one anonymous fleet incident")
    check(any("no entity at all" in reason for reason in sp._dropped_rows),
          f"the drop was silent: {sorted(sp._dropped_rows)}")
    sp._dropped_rows.clear()


def test_one_card_covers_one_alertmanager_notification():
    stub_roster(["node-01", "node-02"], {"epn034": "node-01",
                                         "epn088": "node-02"})
    rows = [
        sp.anomaly_row(result_hit(f"r-g{i}", "d1", 0.9, "origin_host", host,
                                  5000), {"d1": "il-per-epn"})
        for i, host in enumerate(("epn034", "epn088"))]
    groups = {sp.group_id(row) for row in rows}
    check(len(groups) == 1,
          f"two EPNs breaching one fleet-scoped rule produced {len(groups)} "
          f"cards: {sorted(groups)}")
    check(len({sp.incident_id(row) for row in rows}) == 2,
          "grouping collapsed the per-entity episodes themselves; each entity "
          "must keep its own state machine and its own recovery")

    collector_rows = [
        sp.anomaly_row(result_hit(f"r-c{i}", "d2", 0.9, "collector_id", node,
                                  5000), {"d2": "ingest-flow"})
        for i, node in enumerate(("node-01", "node-02"))]
    check(len({sp.group_id(row) for row in collector_rows}) == 2,
          "collector-scoped rows shared one card, but Alertmanager routes "
          "them to separate notifications per collector, so one card would "
          "claim to be a notification nobody receives")

    for row in rows + collector_rows:
        row["incident_id"] = sp.incident_id(row)
        ep = sp.blank_incident(row, row["incident_id"], row["first_seen"])
        labels = sp.labels_for(ep)
        expected = "/".join((row["cluster_id"], row["alertname"],
                             labels["notification_scope"]))
        check(ep["group_id"] == expected,
              f"the card key {ep['group_id']!r} is not the key Alertmanager "
              f"groups on ({expected!r}); the two must be the same fields or "
              f"one card stops meaning one notification")
        payload = sp.alertmanager_payload({ep["episode_id"]: ep})[0]
        check(payload["annotations"]["group_id"] == ep["group_id"],
              "the notification cannot be traced back to the card")


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


def test_both_injection_front_doors_offer_the_same_scenarios():
    import yaml
    import inject_run as engine

    path = _checkout_file("playbooks", "inject.yml")
    if path is None:
        print("[signal-contract] "
              "test_both_injection_front_doors_offer_the_same_scenarios: "
              "skipped, playbooks/inject.yml not beside this checkout")
        return
    plays = yaml.safe_load(open(path))
    allowed = None
    for play in plays:
        for task in play.get("tasks") or []:
            assertion = (task.get("ansible.builtin.assert") or {})
            for clause in assertion.get("that") or []:
                if "scenario in [" in clause:
                    allowed = set(json.loads(
                        clause.split("in ", 1)[1].replace("'", '"')))
    check(allowed is not None,
          "make inject no longer states which scenarios it accepts")
    page = {name for name, _ in ops.INJECT_SCENARIOS}
    check(set(engine.SCENARIOS) == page,
          f"the ops page and the injection engine disagree on scenarios: "
          f"page-only {sorted(page - set(engine.SCENARIOS))}, engine-only "
          f"{sorted(set(engine.SCENARIOS) - page)}")
    if allowed:
        check(allowed == set(engine.SCENARIOS),
              f"make inject and the injection engine disagree on scenarios: "
              f"{sorted(allowed ^ set(engine.SCENARIOS))}")
    check(ops.INJECT_WORKER_SCENARIOS == set(engine.WORKER_SCENARIOS),
          "the ops page and the engine disagree on which scenarios need a "
          "target worker, so the page would accept a run the engine refuses")


def test_injection_refuses_to_share_a_window_with_poison():
    import inspect

    check("REFUSED" in inspect.getsource(ops.start_inject)
          and "POISON_SERVICE" in inspect.getsource(ops.start_inject),
          "an injection can start while poison replay is running, and the "
          "injected documents would be scored as the fault's own evidence")
    check("INJECT_SERVICE" in inspect.getsource(ops.start_poison),
          "poison replay can start inside a running injection window")


def test_injected_pipeline_preserves_the_real_one():
    import inject_run as engine

    bodies = []
    original = engine._request

    def fake_request(method, url, body=None, timeout=30, headers=None):
        bodies.append((method, url, body))
        return 200, "{}"

    try:
        engine._request = fake_request
        engine.install_injected_pipeline("epn001")
    finally:
        engine._request = original

    processors = None
    for _, _, body in bodies:
        if isinstance(body, dict) and "processors" in body:
            processors = body["processors"]
    check(processors is not None, "the drop-epn-stream pipeline is missing")
    if processors:
        check(any("epn001" in json.dumps(p) for p in processors),
              "the injected pipeline does not name the host it must silence")
    if processors:
        check(any("drop" in p for p in processors),
              "the injected pipeline does not drop the target host")
        check(any((p.get("pipeline") or {}).get("name")
                  == "alice-add-ingest-time" for p in processors),
              "the injected pipeline replaces alice-add-ingest-time instead "
              "of delegating to it, so every non-target record would lose "
              "ingest_time and both lag fields")


def test_every_local_import_survives_the_on_vm_layout():
    source = _checkout_file(
        "roles", "signal_projector", "files", "test_signal_contract.py")
    group_vars = _checkout_file("group_vars", "all.yml")
    if not source or not group_vars:
        print("[signal-contract] "
              "test_every_local_import_survives_the_on_vm_layout"
              ": skipped, roles/ not beside this checkout")
        return
    files_dir = os.path.dirname(source)
    # The install paths are no longer in one file. Each role declares the ones
    # it owns, and group_vars/all.yml holds the ones two roles share.
    roles_dir = os.path.dirname(os.path.dirname(files_dir))
    declarations = [group_vars] + sorted(
        os.path.join(roles_dir, role, "defaults", "main.yml")
        for role in os.listdir(roles_dir)
        if os.path.exists(
            os.path.join(roles_dir, role, "defaults", "main.yml")))
    beside = set()
    for declaration in declarations:
        with open(declaration) as fh:
            beside |= set(re.findall(
                r":\s*/opt/alice-ingest/(\w+)\.py\s*$", fh.read(), re.M))
    with open(source) as fh:
        tree = ast.parse(fh.read())

    def local_imports(nodes):
        return {alias.name
                for node in nodes
                for inner in ast.walk(node)
                if isinstance(inner, ast.Import)
                for alias in inner.names
                if os.path.exists(
                    os.path.join(files_dir, alias.name + ".py"))}

    stranded = local_imports(
        n for n in tree.body if isinstance(n, ast.Import)) - beside
    check(not stranded,
          f"{sorted(stranded)} is imported at module scope but is not staged "
          f"in /opt/alice-ingest, so this file cannot even load on the control "
          f"host where projector.yml runs it")

    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        if not fn.name.startswith("test_"):
            continue
        risky = local_imports([fn]) - beside
        if not risky:
            continue
        guarded = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id == "_checkout_file"
                      for n in ast.walk(fn))
        check(guarded,
              f"{fn.name} imports {sorted(risky)}, which the deploy stages "
              f"somewhere other than beside this file, and it reaches that "
              f"import without a _checkout_file guard. It passes here and "
              f"raises ModuleNotFoundError on the control host, failing the "
              f"deploy at a task that has nothing to do with the contract")


def test_episode_grouping_gate_runs_after_the_projector_upgrade():
    detection_path = _checkout_file(
        "roles", "anomaly_detection", "tasks", "detection.yml")
    control_path = _checkout_file(
        "roles", "signal_projector", "tasks", "control.yml")
    site_path = _checkout_file("playbooks", "site.yml")
    verifier_path = _checkout_file(
        "roles", "anomaly_detection", "files", "verify_detection.py")
    if not all((detection_path, control_path, site_path, verifier_path)):
        print("[signal-contract] "
              "test_episode_grouping_gate_runs_after_the_projector_upgrade"
              ": skipped, roles/ not beside this checkout")
        return
    detection = open(detection_path).read()
    control = open(control_path).read()
    site = open(site_path).read()
    verifier = open(verifier_path).read()
    check("CHECK_EPISODE_GROUPING" not in detection,
          "the bootstrap verifier checks episode grouping, but it runs before "
          "the signal_projector role installs the projector that writes "
          "group_id, so every pre-existing episode fails a gate no deploy can "
          "ever satisfy")
    check('CHECK_EPISODE_GROUPING: "true"' in control,
          "no pass checks episode grouping, so a projector that stops writing "
          "group_id ships silently and the cockpit board renders empty")
    check('GROUPING_SINCE: "{{ projector_gate_started_utc }}"' in control,
          "the episode-grouping gate is not scoped to this projector's own "
          "restart, so episodes written by the previous version fail it")
    check("_projector_gate_started.stdout" in site,
          "the control-host re-verify is not handed the projector host's own "
          "gate start time, so GROUPING_SINCE is empty and the gate either "
          "fails on rows the previous projector wrote or checks nothing")
    check("if CHECK_EPISODE_GROUPING:" in verifier,
          "check_episode_grouping is not behind the opt-in flag, so it runs in "
          "the bootstrap pass again")


def test_push_heartbeat_gate_runs_after_collector_cutover():
    site_path = _checkout_file("playbooks", "site.yml")
    detection_path = _checkout_file(
        "roles", "anomaly_detection", "tasks", "detection.yml")
    if site_path is None or detection_path is None:
        print("[signal-contract] "
              "test_push_heartbeat_gate_runs_after_collector_cutover"
              ": skipped, playbooks/site.yml not beside this checkout")
        return
    site = open(site_path).read()
    projector_path = _checkout_file(
        "roles", "signal_projector", "tasks", "control.yml")
    post_path = _checkout_file(
        "roles", "cockpit_metrics", "tasks", "post_collector.yml")
    if not all((projector_path, post_path)):
        print("[signal-contract] "
              "test_push_heartbeat_gate_runs_after_collector_cutover"
              ": skipped, roles/ not beside this checkout")
        return
    detection = open(detection_path).read()
    projector = open(projector_path).read()
    post = open(post_path).read()
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
    check('loop: "{{ fleet_collector_node_ids }}"' in post,
          "the post-collector gate does not wait for every worker")
    check("EXPECT_PUSH_HEARTBEATS: \"{{ collector_health_push_enabled"
          in post,
          "the final verifier does not enforce the pushed-heartbeat contract")


def test_control_host_is_quiet_before_fact_gathering():
    path = _checkout_file("playbooks", "site.yml")
    if path is None:
        print("[signal-contract] "
              "test_control_host_is_quiet_before_fact_gathering"
              ": skipped, playbooks/site.yml not beside this checkout")
        return

    import yaml
    plays = yaml.safe_load(open(path))
    quiet = next((play for play in plays if play.get("name") ==
                  "Quiet the control host before anything heavy runs on it"),
                 None)
    check(quiet is not None, "the control-host quiesce play is missing")
    if quiet is not None:
        check(quiet.get("gather_facts") is False,
              "the control-host quiesce play gathers facts before it stops "
              "the memory-heavy services")


def test_detector_category_migration_recreates_instead_of_updating():
    here = os.path.dirname(os.path.abspath(__file__))
    script = None
    for _ in range(7):
        candidate = os.path.join(
            here, "roles", "anomaly_detection", "templates",
            "detectors.sh.j2")
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


def test_ops_actions_run_in_background_and_refresh_is_safe():
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
    prior_action = ops.ACTIONS["stop"]
    with ops._JOB_LOCK:
        prior_job = ops._JOB
    calls = []
    server = None
    thread = None
    try:
        ops.snapshot = lambda: dict(snapshot, job=ops.job_status())

        def fake_stop(lines, params):
            calls.append("stop")
            lines.append("test stop action ran exactly once")
            return lines

        ops.ACTIONS["stop"] = ("Stopping the test replay", fake_stop)
        with ops._JOB_LOCK:
            ops._JOB = None
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
        check(location == "/ops/",
              f"background ops action redirected somewhere unsafe: {location!r}")
        check(response.getheader("Cache-Control") == "no-store",
              "the ops POST redirect is cacheable")
        conn.close()

        deadline = time.time() + 5
        while time.time() < deadline:
            job = ops.job_status()
            if job and job["state"] == "done":
                break
            time.sleep(0.01)
        check(job is not None and job["state"] == "done",
              f"the background ops action did not finish: {job}")

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
        check("test stop action ran exactly once" in second_get,
              "refresh lost the completed background action result")
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
        ops.ACTIONS["stop"] = prior_action
        with ops._JOB_LOCK:
            ops._JOB = prior_job
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


def test_deploy_validates_the_vault_before_retrying_hosts():
    makefile_path = _checkout_file("Makefile")
    if makefile_path is None:
        print("[signal-contract] "
              "test_deploy_validates_the_vault_before_retrying_hosts: "
              "skipped, Makefile not beside this checkout")
        return

    makefile = open(makefile_path).read()
    validation = makefile.find("$(ANSIBLE_VAULT) view")
    deploy_loop = makefile.find("for i in $$(seq 1 $(DEPLOY_ATTEMPTS))")
    check("VAULT_PASSWORD_ATTEMPTS ?= 3" in makefile,
          "make deploy does not permit a vault-password typo retry")
    check(validation >= 0 and deploy_loop >= 0 and validation < deploy_loop,
          "make deploy starts or retries hosts before proving that its vault "
          "password decrypts the local vault")
    check("not CERN/lxplus password" in makefile,
          "the deploy prompt does not distinguish the vault password from the "
          "operator's CERN login password")
    check("deployment stopped before contacting any VM" in makefile,
          "a vault failure does not state its no-impact boundary")


def test_projector_runtime_is_off_the_control_host():
    import yaml

    inventory_path = _checkout_file("inventory.yml")
    site_path = _checkout_file("playbooks", "site.yml")
    projector_path = _checkout_file(
        "roles", "signal_projector", "tasks", "main.yml")
    projector_control_path = _checkout_file(
        "roles", "signal_projector", "tasks", "control.yml")
    projector_unit_path = _checkout_file(
        "roles", "signal_projector", "templates",
        "alice-signal-projector.service.j2")
    alertmanager_unit_path = _checkout_file(
        "roles", "alertmanager", "templates", "alertmanager.service.j2")
    # The rule moved out of `common` when each role took the ports it owns.
    alertmanager_tasks_path = _checkout_file(
        "roles", "alertmanager", "tasks", "main.yml")
    group_vars_path = _checkout_file("group_vars", "all.yml")
    inject_path = _checkout_file("playbooks", "inject.yml")
    if not all((inventory_path, site_path, projector_path,
                projector_unit_path, alertmanager_unit_path,
                alertmanager_tasks_path, group_vars_path, inject_path)):
        print("[signal-contract] "
              "test_projector_runtime_is_off_the_control_host: skipped, "
              "deployment sources not beside this checkout")
        return

    inventory = yaml.safe_load(open(inventory_path))
    children = inventory["all"]["children"]
    control = set(children["control"]["hosts"])
    projector_hosts = set(children["projector"]["hosts"])
    check(projector_hosts == {"alice-ingest-4"},
          f"projector is not pinned to the intended offload host: "
          f"{sorted(projector_hosts)}")
    check(control.isdisjoint(projector_hosts),
          "the projector host is still the crowded control host")

    site = open(site_path).read()
    projector = open(projector_path).read()
    projector_unit = open(projector_unit_path).read()
    alertmanager_unit = open(alertmanager_unit_path).read()
    alertmanager_tasks = open(alertmanager_tasks_path).read()
    group_vars = open(group_vars_path).read()
    inject = open(inject_path).read()
    check("moved_services:" in site and "alice-signal-projector" in site,
          "deploy does not retire the old control-host projector unit")
    projector_play = re.search(
        r"^- name: Signal projector on its own node.*?(?=^- name: )",
        site, re.S | re.M)
    check(projector_play is not None
          and "hosts: projector" in projector_play.group(0)
          and "- signal_projector" in projector_play.group(0),
          "the signal_projector role no longer runs in a play targeting the "
          "projector host, so its files, service checks and diagnostics land "
          "back on the crowded control host")
    check("delegate_to" not in projector,
          "the projector role still delegates instead of running on the host "
          "its own play targets; one of the two is always wrong")
    check("Environment=ALERTMANAGER_URL=http://{{ alertmanager_host_address }}"
          in projector_unit,
          "the offloaded projector still targets loopback Alertmanager")
    check("MemoryHigh={{ signal_projector_memory_high }}" in projector_unit
          and "MemoryMax={{ signal_projector_memory_max }}" in projector_unit,
          "the offloaded projector lost its dedicated memory bounds")
    check("--web.listen-address=0.0.0.0:{{ alertmanager_port }}" in
          alertmanager_unit,
          "Alertmanager is still loopback-only after projector offload")
    check("port port=\"{{ alertmanager_port }}\" protocol=\"tcp\""
          in alertmanager_tasks
          and "loop: \"{{ alertmanager_allowed_client_addresses }}\""
          in alertmanager_tasks
          and "hostvars[signal_projector_host].ansible_host" in group_vars,
          "the Alertmanager firewall path is not restricted to the projector "
          "host")
    check("dashboards_inject_service_name" in inject
          and "delegate_to" not in inject,
          "make inject still reaches into the fleet itself instead of arming "
          "the control-host engine both front doors share")
    inject_unit = open(_checkout_file(
        "roles", "alice_ops", "templates", "alice-inject.service.j2")).read()
    check("INJECT_PROJECTOR_AGENT=http://{{ signal_projector_address }}"
          in inject_unit,
          "the stop-projector drill still operates on the control host "
          "instead of the projector's own node")


def test_dynamic_services_can_read_the_signal_catalog():
    import yaml

    path = _checkout_file(
        "roles", "alice_runtime", "tasks", "main.yml")
    if path is None:
        print("[signal-contract] "
              "test_dynamic_services_can_read_the_signal_catalog: skipped, "
              "alice_runtime not beside this checkout")
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
        "roles", "alice_runtime", "tasks", "digest.yml")
    projector_path = _checkout_file(
        "roles", "signal_projector", "tasks", "main.yml")
    projector_unit_path = _checkout_file(
        "roles", "signal_projector", "templates",
        "alice-signal-projector.service.j2")
    site_path = _checkout_file("playbooks", "site.yml")
    projector = open(projector_path).read() if projector_path else ""
    projector_unit = (open(projector_unit_path).read()
                      if projector_unit_path else "")
    site = open(site_path).read() if site_path else ""
    check(digest_path is None,
          "digest.yml still exists; the live anomaly digest was removed")
    check("tasks_from: digest.yml" not in site
          and "anomaly-realtime" not in site,
          "deploy still waits on the digest watermark or includes digest.yml")
    check("Wait for the newest signal-projector heartbeat to prove a "
          "successful cycle" in projector
          and "projector_cycle_ok" in projector
          and "_projector_gate_started.stdout" in projector,
          "deploy accepts a systemd-active but functionally failed signal "
          "projector")
    check("Assert the projector is actually running inside finite memory "
          "bounds" in projector
          and "MemoryHigh=[1-9][0-9]*" in projector
          and "MemoryMax=[1-9][0-9]*" in projector,
          "deploy does not prove the running projector has finite memory caps")
    check("MemoryHigh={{ signal_projector_memory_high }}" in projector_unit
          and "MemoryMax={{ signal_projector_memory_max }}" in projector_unit
          and "Environment=PIT_KEEP_ALIVE={{ "
          "signal_projector_pit_keep_alive }}" in projector_unit
          and "Environment=BULK_DOCUMENTS={{ "
          "signal_projector_bulk_documents }}" in projector_unit,
          "the projector unit dropped its memory or bounded-traversal settings")


def test_deploy_preserves_the_replay_runtime_dropin():
    import yaml

    producer_path = _checkout_file(
        "roles", "producer", "tasks", "main.yml")
    replay_path = _checkout_file("playbooks", "replay.yml")
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


def _resolve_from_playbook_dir(path):
    playbook = _checkout_file("playbooks", "site.yml")
    if playbook is None:
        return None
    return os.path.normpath(
        path.replace("{{ playbook_dir }}", os.path.dirname(playbook)))


def _ansible_tasks(path):
    import yaml

    loaded = yaml.safe_load(open(path))
    if not loaded:
        return []
    if isinstance(loaded, dict):
        loaded = [loaded]
    tasks = []
    for item in loaded:
        if isinstance(item, dict) and "tasks" in item:
            tasks.extend(item.get("tasks") or [])
        else:
            tasks.append(item)
    return tasks


def _role_files(*suffixes):
    roles_root = _checkout_file("roles")
    if roles_root is None:
        return None, []
    found = []
    for dirpath, dirnames, filenames in os.walk(roles_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(suffixes):
                full = os.path.join(dirpath, name)
                found.append((os.path.relpath(full, roles_root), full))
    return roles_root, sorted(found)


def test_playbook_relative_files_resolve_from_playbooks_dir():
    playbook_dir = os.path.dirname(
        _checkout_file("playbooks", "site.yml") or "")
    status_path = _checkout_file("playbooks", "status.yml")
    vars_path = _checkout_file("group_vars", "all.yml")
    if not all((playbook_dir, status_path, vars_path)):
        print("[signal-contract] "
              "test_playbook_relative_files_resolve_from_playbooks_dir: "
              "skipped, deployment sources not beside this checkout")
        return

    cmd = None
    for task in _ansible_tasks(status_path):
        script = (task.get("ansible.builtin.script") or {})
        if "detection_status.py" in str(script.get("cmd", "")):
            cmd = script["cmd"]
    check(cmd, "make status no longer runs the detection probe")
    check(os.path.isfile(_resolve_from_playbook_dir(cmd)),
          "make status still looks for the detection probe as if the "
          "playbook lived at the deploy root")

    # Site-level paths are allowed to point outside deploy/ — that is what
    # group_vars is for. They still have to resolve from playbooks/.
    site_paths = {}
    for line in open(vars_path):
        for key in ("causal_edges_file", "producer_replay_source"):
            if line.startswith(key + ":"):
                site_paths[key] = line.split(":", 1)[1].strip().strip('"')
    for key in ("causal_edges_file", "producer_replay_source"):
        value = site_paths.get(key)
        check(value, f"{key} is no longer set in group_vars/all.yml")
        check(value and os.path.isfile(_resolve_from_playbook_dir(value)),
              f"{key} does not resolve from playbooks/")


def test_no_role_reaches_outside_its_own_directory():
    roles_root, files = _role_files(".yml", ".yaml", ".j2")
    if roles_root is None:
        print("[signal-contract] "
              "test_no_role_reaches_outside_its_own_directory: "
              "skipped, roles/ not beside this checkout")
        return

    offenders = [rel for rel, full in files
                 if "playbook_dir" in open(full, errors="ignore").read()]
    check(not offenders,
          "a role must not depend on where the playbook sits, but these "
          "reference playbook_dir: " + ", ".join(offenders) + ". Take the "
          "path through a role variable and set it in group_vars instead.")


def test_the_registration_script_has_exactly_one_definition():
    roles_root, scripts = _role_files("register_node.sh")
    collector_path = _checkout_file("roles", "collector", "tasks", "main.yml")
    bootstrap_path = _checkout_file(
        "roles", "opensearch_bootstrap", "tasks", "main.yml")
    if roles_root is None or not all((collector_path, bootstrap_path)):
        print("[signal-contract] "
              "test_the_registration_script_has_exactly_one_definition: "
              "skipped, roles/ not beside this checkout")
        return

    # Each worker runs this as ExecStartPre and the control host runs the same
    # file once per worker. Two copies means two competing definitions of a
    # worker's index template.
    check([rel for rel, _ in scripts] ==
          [os.path.join("opensearch_local_index_registration", "files", "register_node.sh")],
          "register_node.sh must exist exactly once, inside the "
          "opensearch_local_index_registration role, but roles/ holds: " +
          ", ".join(rel for rel, _ in scripts))

    for path, label, expected_notify in (
            (collector_path, "collector", ["restart fluent-bit"]),
            (bootstrap_path, "opensearch_bootstrap", None)):
        includes = [
            task for task in _ansible_tasks(path)
            if (task.get("ansible.builtin.include_role") or {}).get("name")
            == "opensearch_local_index_registration"]
        check(len(includes) == 1,
              f"{label} no longer installs register_node.sh through the "
              f"opensearch_local_index_registration role ({len(includes)} include_role tasks)")
        if not includes:
            continue
        task_vars = includes[0].get("vars") or {}
        check(task_vars.get("opensearch_local_index_registration_dest"),
              f"{label} does not tell opensearch_local_index_registration where to install "
              "the script")
        if expected_notify is not None:
            check(task_vars.get("opensearch_local_index_registration_notify")
                  == expected_notify,
                  f"{label} no longer restarts its service when the "
                  "registration script changes; it runs as ExecStartPre, so "
                  "an unrestarted collector keeps the old one")


def test_status_exposes_functional_projector_and_replay_health():
    status_path = _checkout_file("playbooks", "status.yml")
    probe_path = _checkout_file(
        "roles", "anomaly_detection", "files", "detection_status.py")
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
    check("alice-signals" in probe and "source_kind" in probe
          and "nothing projected into alice-signals" in probe,
          "the detection probe cannot distinguish raw AD firing from a "
          "silent projector")
    check("digest projected none" not in probe
          and "ANOMALY DIGEST" not in probe,
          "the detection probe still talks about the removed digest")


def test_unreachable_opensearch_pages_without_touching_opensearch():
    sent = []
    saved = (sp.probe_opensearch, sp.send_to_alertmanager,
             sp.os_cursor.request, sp.OS_UNREACHABLE_CYCLES)

    def refuse(*a, **kw):
        raise AssertionError(
            "the opensearch-unreachable path called OpenSearch, which is the "
            "one component it must never depend on")

    def capture(alerts):
        sent.extend(alerts)
        return True, 1

    sp.probe_opensearch = lambda: (False, 1)
    sp.send_to_alertmanager = capture
    sp.os_cursor.request = refuse
    sp.OS_UNREACHABLE_CYCLES = 2
    try:
        state = {"failures": 0, "since_ms": None, "alert_sent": False}
        sp.track_opensearch_reach(state, False)
        check(not sent,
              "a single failed probe paged; one dropped connection must not "
              "raise a cluster-wide outage")
        sp.track_opensearch_reach(state, False)
        check(len(sent) == 1,
              f"two failed probes produced {len(sent)} alerts, expected 1")
        alert = sent[0]
        check(alert["labels"]["alertname"] == sp.OS_UNREACHABLE_ALERTNAME,
              f"unexpected alertname {alert['labels'].get('alertname')!r}")
        check(alert["labels"]["severity"] == "page",
              "an unreachable cluster must route as a page")
        for label in REQUIRED_LABELS:
            value = alert["labels"].get(label)
            check(value is not None and value != "",
                  f"opensearch-unreachable: label {label} missing or empty")
        check("endsAt" not in alert,
              "the firing alert already carried an end time")

        sp.track_opensearch_reach(state, False)
        check(len(sent) == 2 and "endsAt" not in sent[1],
              "the alert is not re-sent every cycle, so Alertmanager would "
              "resolve a live outage on its own timeout")
        check(sent[1]["labels"] == sent[0]["labels"],
              "the re-send carries different labels, so Alertmanager would "
              "fingerprint it as a second alert instead of updating the "
              "first")
        check(sent[1]["startsAt"] == sent[0]["startsAt"],
              "the re-send moved the outage start, so one outage would read "
              "as a stream of new ones")

        sp.probe_opensearch = lambda: (True, 1)
        sp.track_opensearch_reach(state, True)
        check(len(sent) == 3 and "endsAt" in sent[2],
              "recovery did not resolve the direct alert")
        check(sent[2]["labels"] == sent[0]["labels"],
              "the resolve carries different labels, so it would resolve "
              "nothing and the outage would stay firing until it timed out")
        check(state["alert_sent"] is False and state["since_ms"] is None,
              "the outage state survived recovery, so the next outage would "
              "reuse a stale start time")

        sp.track_opensearch_reach(state, True)
        check(len(sent) == 3,
              "a healthy cycle kept re-resolving an alert that was never "
              "firing")
    finally:
        (sp.probe_opensearch, sp.send_to_alertmanager,
         sp.os_cursor.request, sp.OS_UNREACHABLE_CYCLES) = saved

    check(sp.OS_UNREACHABLE_ALERTNAME not in signal_identity.monitor_names(),
          "opensearch-unreachable is declared as an in-cluster monitor; it "
          "exists precisely because in-cluster monitors cannot report this")


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
