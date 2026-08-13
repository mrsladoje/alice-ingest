import json
import os
import sys
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
EXPECTED_MONITORS = int(os.environ.get("EXPECTED_MONITORS", "26"))
EXPECTED_DETECTORS = int(os.environ.get("EXPECTED_DETECTORS", "17"))
EXPECTED_FORECASTERS = int(os.environ.get("EXPECTED_FORECASTERS", "1"))
ROLLUP_INDEX = os.environ.get("ROLLUP_INDEX", "trend-rollup")
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
ROSTER_INDEX = os.environ.get("ROSTER_INDEX", "cockpit-fleet")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
NOTIFICATIONS_INDEX = os.environ.get(
    "NOTIFICATIONS_INDEX", "alice-notifications")
LANE_STATE_INDEX = os.environ.get("LANE_STATE_INDEX", "alice-lane-state")
CATALOG_PATH = os.environ.get(
    "SIGNAL_CATALOG", "/opt/alice-ingest/init/signal_catalog.json")
# Staged beside this script by bootstrap.yml, so the default needs no variable.
COCKPIT_NDJSON = os.environ.get(
    "COCKPIT_NDJSON",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cockpit.ndjson"))
BOARD_PANEL_ID = "alice-viz-incidents"
CHECK_EPISODE_GROUPING = os.environ.get(
    "CHECK_EPISODE_GROUPING", "false").lower() == "true"
GROUPING_SINCE = os.environ.get("GROUPING_SINCE", "now-1h")
MAX_ACTIONABLE = int(
    os.environ.get("ALERTING_MAX_ACTIONABLE_ALERT_COUNT", "50"))
EXPECT_PUSH_HEARTBEATS = os.environ.get(
    "EXPECT_PUSH_HEARTBEATS", "false").lower() == "true"

SINK_ID = os.environ.get("ALERT_SINK_ID", "alice-incluster-alert-sink")
BREAKGLASS_ID = os.environ.get("BREAKGLASS_SINK_ID", "alice-breakglass-sink")
BREAKGLASS_MONITORS = {"signal-projector-stale", "alertmanager-down"}

METRICS_DETECTORS = {"ingest-flow", "node-health", "dashboards-health"}
OK_STATES = {"RUNNING", "INIT", "INITIALIZING", "INIT_PROGRESS"}

FORECAST_OK_STATES = {"RUNNING", "INIT", "INITIALIZING", "INIT_FORECAST",
                      "AWAITING_DATA_TO_INIT", "AWAITING_DATA_TO_RESTART"}
FORECAST_FAILED_STATES = {"DISABLED", "STOPPED", "INACTIVE_STOPPED", "FAILED",
                          "FORECAST_FAILURE", "INIT_FORECAST_FAILED",
                          "INIT_TEST_FAILED"}

RETIRED_NODE_CONSUMERS = {
    "collector-down", "collector-unhealthy", "data-loss",
    "shipping-breaking", "disk-cliff-warn", "disk-cliff-page", "heap-spiral",
}

with open(CATALOG_PATH) as _fh:
    CATALOG = json.load(_fh)

EXPECTED_MONITOR_NAMES = set(CATALOG["monitors"])
EXPECTED_DETECTOR_NAMES = set(CATALOG["detectors"])
EXPECTED_FORECASTER_NAMES = set(CATALOG.get("forecasters") or {})
TREND_MONITOR_NAMES = {
    n for n in EXPECTED_MONITOR_NAMES if n.startswith("trend-")
    and n not in ("trend-rollup-stale", "trend-entity-cap")}
TREND_LANE_MONITORS = {"trend-rollup-stale", "trend-entity-cap"}
ENTITY_SENTINEL = (CATALOG.get("sentinels") or {}).get("entity_id", "none")
MONITOR_CLASSES = {"monitor-error", "entity-missing"}


def req(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(
        OS_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        print(f"[verify-detection] FATAL: {method} {path}: {e}")
        sys.exit(1)


def live_properties(index):
    code, body = req("GET", f"/{index}/_mapping")
    if code != 200:
        return None
    props = {}
    for mapping in body.values():
        props.update((mapping.get("mappings") or {}).get("properties") or {})
    return props


def check_channels(errors):
    for cid in (SINK_ID, BREAKGLASS_ID):
        code, sink = req("GET", f"/_plugins/_notifications/configs/{cid}")
        ok = code == 200 and any(
            c.get("config_id") == cid for c in (sink.get("config_list") or []))
        if ok:
            print(f"[verify-detection] notification channel present: {cid}")
        else:
            errors.append(f"missing notification channel: {cid}")


def check_actionable_limit(errors):
    code, body = req("GET", "/_cluster/settings?flat_settings=true")
    if code != 200:
        errors.append("cannot read cluster settings")
        return
    persistent = body.get("persistent") or {}
    value = persistent.get("plugins.alerting.max_actionable_alert_count")
    if value is None:
        errors.append(
            "plugins.alerting.max_actionable_alert_count is not pinned — the "
            "plugin silently rewrites per_alert to per_execution above this "
            "limit, and an unpinned default makes the storm shape untestable")
    elif int(value) != MAX_ACTIONABLE:
        errors.append(
            f"max_actionable_alert_count={value} want {MAX_ACTIONABLE}")
    else:
        print(f"[verify-detection] max_actionable_alert_count={value}")


def check_monitors(errors):
    _, mon = req("POST", "/_plugins/_alerting/monitors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    monitors = mon.get("hits", {}).get("hits", [])
    mon_names = set()
    bucket_names = set()
    throttle_missing = []
    wrong_source = []
    entity_missing = []
    dead_policy = []
    node_keyed = []

    for h in monitors:
        src = h.get("_source", {})
        mon_obj = src.get("monitor", src)
        name = mon_obj.get("name") or src.get("name")
        if name:
            mon_names.add(name)
        if name not in EXPECTED_MONITOR_NAMES:
            continue

        inputs_json = json.dumps(mon_obj.get("inputs") or [])
        if name in RETIRED_NODE_CONSUMERS and '"field": "node"' in inputs_json:
            node_keyed.append(name)

        if name in TREND_MONITOR_NAMES | TREND_LANE_MONITORS:
            indices = []
            for inp in mon_obj.get("inputs") or []:
                indices.extend((inp.get("search") or {}).get("indices") or [])
            if indices != [ROLLUP_INDEX]:
                wrong_source.append(f"{name}={indices}")
        if name in TREND_MONITOR_NAMES and mon_obj.get("enabled") is not True:
            errors.append(f"trend monitor {name} is disabled")

        want_sink = (BREAKGLASS_ID if name in BREAKGLASS_MONITORS
                     else SINK_ID)
        is_bucket = mon_obj.get("monitor_type") == "bucket_level_monitor"
        if is_bucket:
            bucket_names.add(name)
        for t in mon_obj.get("triggers") or []:
            if "bucket_level_trigger" not in t \
                    and "query_level_trigger" not in t:
                errors.append(
                    f"monitor {name} has a bare trigger object — two ad-hoc "
                    f"audits of this set have already been wrong because of "
                    f"the two shapes")
            trig = t.get("bucket_level_trigger") or t.get(
                "query_level_trigger") or t
            actions = trig.get("actions") or []
            ok_throttle = False
            for a in actions:
                thr = a.get("throttle") or {}
                if (a.get("throttle_enabled") is True
                        and thr.get("value") == 30
                        and str(thr.get("unit", "")).upper() == "MINUTES"
                        and a.get("destination_id") == want_sink):
                    ok_throttle = True
                template = ((a.get("message_template") or {})
                            .get("source") or "")
                if '"entity"' not in template:
                    entity_missing.append(f"{name}/{a.get('name')}")
                if not is_bucket and a.get("action_execution_policy"):
                    dead_policy.append(f"{name}/{a.get('name')}")
            if not ok_throttle:
                throttle_missing.append(f"{name}->{want_sink}")
                break

    print(f"[verify-detection] monitors={sorted(mon_names)}")
    missing_m = sorted(EXPECTED_MONITOR_NAMES - mon_names)
    if missing_m:
        errors.append(f"missing monitors: {missing_m}")
    if len(mon_names & EXPECTED_MONITOR_NAMES) < EXPECTED_MONITORS:
        errors.append(
            f"monitor count {len(mon_names & EXPECTED_MONITOR_NAMES)} < "
            f"{EXPECTED_MONITORS}")
    if throttle_missing:
        errors.append(
            f"monitors missing the 30m throttle on their declared sink: "
            f"{sorted(throttle_missing)}")
    if wrong_source:
        errors.append(
            f"trend monitors not reading {ROLLUP_INDEX}: "
            f"{sorted(wrong_source)}")
    if entity_missing:
        errors.append(
            f"action payloads with no entity — a fleet-wide breach would be "
            f"anonymous: {sorted(entity_missing)}")
    if dead_policy:
        errors.append(
            f"query-level monitors carrying an action_execution_policy, "
            f"which is a bucket-level concept and is ignored: "
            f"{sorted(dead_policy)}")
    if node_keyed:
        errors.append(
            f"monitors still bucketed on the ambiguous node field instead of "
            f"collector_id / os_node: {sorted(node_keyed)}")
    return bucket_names


def check_bucket_alert_entities(errors, bucket_monitors):
    """Every bucket-level alert must reach the signals index named.

    Alerting nests the key under agg_alert_content.bucket_keys, and exposes a
    flat bucket_keys only to the mustache context an action renders. A
    projector reading the flat field therefore sees a keyless alert, classes
    it entity-missing, and folds each breach into one monitor-level card that
    names nobody — while the notification payload still carries the entity, so
    the two lanes disagree.

    check_episode_grouping cannot catch this. It excludes the monitor classes
    from its anonymity test on purpose, because those rows are the intended
    output when a rule really is broken. This gate asks the prior question:
    whether the rule was broken at all.

    Runs only beside that gate, for its bound. GROUPING_SINCE is the instant
    recorded just before the projector restarts, so only rows the running
    projector wrote can fail the deploy. The two earlier verify runs in a
    deploy happen before that restart and leave GROUPING_SINCE at its relative
    default, where rows from the previous projector would fail the very deploy
    that replaces it.
    """
    if not bucket_monitors:
        return
    code, body = req(
        "POST", f"/{SIGNALS_INDEX}/_search",
        {"size": 0, "track_total_hits": True,
         "query": {"bool": {"filter": [
             {"term": {"class": "entity-missing"}},
             {"terms": {"alertname": sorted(bucket_monitors)}},
             {"range": {"last_seen": {"gte": GROUPING_SINCE}}}]}},
         "aggs": {"rules": {"terms": {"field": "alertname", "size": 20}}}})
    if code == 404:
        return
    if code != 200:
        errors.append(
            f"cannot read {SIGNALS_INDEX} for bucket-key attribution: "
            f"HTTP {code}")
        return
    total = ((body.get("hits") or {}).get("total") or {}).get("value", 0)
    if not total:
        print(f"[verify-detection] every bucket-level alert projected since "
              f"{GROUPING_SINCE} named its entity")
        return
    rules = sorted(
        f"{b.get('key')}={b.get('doc_count')}" for b in
        (((body.get("aggregations") or {}).get("rules") or {})
         .get("buckets") or []))
    errors.append(
        f"{total} bucket-level alerts projected since {GROUPING_SINCE} carry "
        f"no bucket key ({rules}) — the breach is real but unattributable, "
        f"and the entity is still recoverable from "
        f"agg_alert_content.bucket_keys on the alert and from the entity "
        f"field in {NOTIFICATIONS_INDEX}")


def check_detectors(errors):
    _, det = req("POST", "/_plugins/_anomaly_detection/detectors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    detectors = det.get("hits", {}).get("hits", [])
    det_names = {}
    det_time_fields = {}
    for h in detectors:
        src = h.get("_source", {})
        name = src.get("name")
        if not name:
            continue
        det_names.setdefault(name, []).append(h.get("_id"))
        if src.get("time_field"):
            det_time_fields[name] = src["time_field"]

    print(f"[verify-detection] detectors={sorted(det_names)}")
    missing_d = sorted(EXPECTED_DETECTOR_NAMES - set(det_names))
    if missing_d:
        errors.append(f"missing detectors: {missing_d}")
    uncatalogued = sorted(set(det_names) - EXPECTED_DETECTOR_NAMES)
    if uncatalogued:
        errors.append(
            f"detectors absent from the signal catalog: {uncatalogued} — "
            f"entity kind would have to be inferred from an index name, "
            f"which is exactly how kind_of() came to call collectors cluster "
            f"nodes")
    for name, ids in det_names.items():
        if len(ids) > 1:
            errors.append(f"duplicate detector name {name}: {ids}")

    for h in detectors:
        src = h.get("_source", {})
        name = src.get("name")
        if name not in EXPECTED_DETECTOR_NAMES:
            continue
        want_category = CATALOG["detectors"][name].get("category_field")
        got_category = (src.get("category_field") or [None])[0]
        if want_category != got_category:
            errors.append(
                f"detector {name} category_field={got_category!r} but the "
                f"catalog declares {want_category!r}")
        for fa in src.get("feature_attributes") or []:
            for agg in (fa.get("aggregation_query") or {}).values():
                pct = (agg or {}).get("percentiles")
                if not pct:
                    continue
                percents = pct.get("percents") or []
                if len(percents) != 1:
                    errors.append(
                        f"detector {name} feature {fa.get('feature_name')}: "
                        f"percents={percents} — the plugin reads only the "
                        f"FIRST percentile (ascending), so more than one "
                        f"silently yields the lowest")
                if pct.get("method"):
                    errors.append(
                        f"detector {name} feature {fa.get('feature_name')}: "
                        f"percentiles method={pct['method']!r} — only the "
                        f"default TDigest implementation is parsed")

    for name in EXPECTED_DETECTOR_NAMES:
        if name not in det_time_fields:
            continue
        want = "@timestamp" if name in METRICS_DETECTORS else "collector_time"
        got = det_time_fields[name]
        if got != want:
            errors.append(f"detector {name} time_field={got!r} want={want!r}")

    for name, ids in det_names.items():
        if name not in EXPECTED_DETECTOR_NAMES:
            continue
        code, prof = req(
            "GET", f"/_plugins/_anomaly_detection/detectors/{ids[0]}/_profile")
        if code != 200:
            errors.append(f"profile failed for {name}: HTTP {code}")
            continue
        state = str(prof.get("state", "UNKNOWN")).upper()
        err = prof.get("error")
        print(f"[verify-detection] detector {name}: state={state} "
              f"error={err!r} models={len(prof.get('models') or [])} "
              f"total_size={prof.get('total_size_in_bytes')}")
        if state in {"DISABLED", "STOPPED", "FAILED", "UNKNOWN"}:
            errors.append(f"detector {name} state={state}")
        elif state not in OK_STATES:
            errors.append(f"detector {name} unexpected state {state}")
        if err and "memory" in str(err).lower():
            errors.append(f"detector {name} memory error: {err}")

    if len(det_names) < EXPECTED_DETECTORS:
        errors.append(
            f"detector count {len(det_names)} < {EXPECTED_DETECTORS}")


def check_forecasters(errors):
    code, fc = req("POST", "/_plugins/_forecast/forecasters/_search",
                   {"query": {"match_all": {}}, "size": 200})
    if code != 200:
        errors.append(
            f"cannot search forecasters: HTTP {code} — the forecasting APIs "
            f"live in the anomaly-detection plugin and are enabled by "
            f"default, so a non-200 here means the plugin is missing or "
            f"plugins.forecast.enabled was turned off")
        return
    hits = fc.get("hits", {}).get("hits", [])
    names = {}
    for h in hits:
        src = h.get("_source", {})
        name = src.get("name")
        if not name:
            continue
        names.setdefault(name, []).append(h.get("_id"))

    print(f"[verify-detection] forecasters={sorted(names)}")
    missing = sorted(EXPECTED_FORECASTER_NAMES - set(names))
    if missing:
        errors.append(f"missing forecasters: {missing}")
    uncatalogued = sorted(set(names) - EXPECTED_FORECASTER_NAMES)
    if uncatalogued:
        errors.append(
            f"forecasters absent from the signal catalog: {uncatalogued} — "
            f"disk-fill-forecast reads every real-time forecast result and "
            f"compares it against a disk percentage, so a second forecaster "
            f"on any other unit would warn against a meaningless threshold")
    for name, ids in names.items():
        if len(ids) > 1:
            errors.append(f"duplicate forecaster name {name}: {ids}")

    for h in hits:
        src = h.get("_source", {})
        name = src.get("name")
        if name not in EXPECTED_FORECASTER_NAMES:
            continue
        want_category = CATALOG["forecasters"][name].get("category_field")
        got_category = (src.get("category_field") or [None])[0]
        if want_category != got_category:
            errors.append(
                f"forecaster {name} category_field={got_category!r} but the "
                f"catalog declares {want_category!r}")
        features = src.get("feature_attributes") or []
        if len(features) != 1:
            errors.append(
                f"forecaster {name} declares {len(features)} features — the "
                f"plugin supports exactly one and silently forecasts only "
                f"part of the configuration otherwise")

    for name, ids in names.items():
        if name not in EXPECTED_FORECASTER_NAMES:
            continue
        code, prof = req(
            "GET", f"/_plugins/_forecast/forecasters/{ids[0]}/_profile")
        if code != 200:
            errors.append(f"profile failed for forecaster {name}: HTTP {code}")
            continue
        state = str(prof.get("state", "UNKNOWN")).upper()
        err = prof.get("error")
        print(f"[verify-detection] forecaster {name}: state={state} "
              f"error={err!r} models={len(prof.get('models') or [])} "
              f"total_size={prof.get('total_size_in_bytes')}")
        if state in FORECAST_FAILED_STATES:
            errors.append(f"forecaster {name} state={state}")
        elif state not in FORECAST_OK_STATES:
            print(f"[verify-detection] forecaster {name} is still warming up "
                  f"(state={state}). At a 60-minute interval an RCF model "
                  f"needs roughly two days of poller history before it "
                  f"forecasts, so this is expected on a fresh cluster and "
                  f"does not fail the deploy.")
        if err and "memory" in str(err).lower():
            errors.append(f"forecaster {name} memory error: {err}")

    if len(names) < EXPECTED_FORECASTERS:
        errors.append(
            f"forecaster count {len(names)} < {EXPECTED_FORECASTERS}")


def check_forecast_shards(errors):
    code, body = req("GET", "/_cluster/settings?flat_settings=true")
    if code != 200:
        errors.append("cannot read cluster settings for the forecast lane")
        return
    persistent = body.get("persistent") or {}
    shards = persistent.get("plugins.forecast.max_primary_shards")
    if shards is None:
        errors.append(
            "plugins.forecast.max_primary_shards is not pinned — the result "
            "index defaults to one primary per data node with "
            "auto_expand_replicas 0-2, which is 15 shards for a few thousand "
            "tiny documents, and shard budget is the binding constraint on "
            "this cluster")
    else:
        print(f"[verify-detection] forecast max_primary_shards={shards}")


def check_live_mappings(errors):
    wanted = {
        METRICS_INDEX: ["collector_id", "os_node", "heartbeat_missing",
                        "roster_size", "topology_version", "am_up",
                        "signals_open", "projector_cycle_ok"],
        ROLLUP_INDEX: ["complete", "committed_at", "expected_cohorts",
                       "commit_cohorts"],
        SIGNALS_INDEX: ["alertname", "entity_kind", "entity_id",
                        "collector_id", "notification_scope", "incident_id",
                        "episode_id", "topology_version",
                        "source_uid"],
        INCIDENTS_INDEX: ["incident_id", "episode_id", "episode_start", "class",
                          "episode_state", "healthy_windows",
                          "last_healthy_window", "member_count",
                          "signal_ids", "title", "diagnosis",
                          "operator_action", "affected", "source_label",
                          "worst_grade", "latest_confidence"],
        NOTIFICATIONS_INDEX: ["record_kind", "group_key", "labels_kv",
                              "signal_ids", "episode_ids"],
        ROSTER_INDEX: ["topology_version", "effective_from", "collectors",
                       "assignments"],
        LANE_STATE_INDEX: ["lane", "watermark_ms"],
        "infologger": ["severity_norm", "origin_host"],
        "generic-log-other": ["severity_norm", "origin_host"],
    }
    for index, fields in wanted.items():
        props = live_properties(index)
        if props is None:
            errors.append(f"cannot read live mapping for {index}")
            continue
        missing = [f for f in fields if f not in props]
        if missing:
            errors.append(
                f"{index} live mapping missing {missing} — updating an index "
                f"template does not retrofit an existing concrete index, so "
                f"these fields would sit unindexed under dynamic:false and "
                f"every aggregation over them would quietly return nothing")
        else:
            print(f"[verify-detection] {index}: live mapping carries "
                  f"{len(fields)} required fields")


def check_roster(errors):
    code, body = req(
        "POST", f"/{ROSTER_INDEX}/_search",
        {"size": 2, "sort": [{"effective_from": "desc"}],
         "query": {"bool": {"filter": [{"term": {"doc_kind": "roster"}}]}}})
    if code != 200:
        errors.append(f"cannot read {ROSTER_INDEX}: HTTP {code}")
        return None
    hits = (body.get("hits") or {}).get("hits") or []
    if not hits:
        errors.append(
            f"no roster snapshot in {ROSTER_INDEX} — absence-based "
            f"collector-down has nothing to compare against, so a dead "
            f"collector would be invisible rather than paging")
        return None
    src = hits[0].get("_source") or {}
    if not str(hits[0].get("_id", "")).endswith(
            str(src.get("topology_version", ""))):
        errors.append(
            "roster snapshot id does not end in its topology_version; the id "
            "must be effective_from + version so returning to an earlier "
            "topology appends a new snapshot instead of colliding with the "
            "old one")
    if len(hits) > 1:
        newer, older = hits[0].get("_source") or {}, hits[1].get("_source") or {}
        if int(newer.get("effective_from") or 0) \
                <= int(older.get("effective_from") or 0):
            errors.append(
                "roster snapshots are not strictly ordered by effective_from, "
                "so an event-time lookup cannot pick the right assignment")
    for field in ("collectors", "assignments", "topology_version",
                  "effective_from"):
        if field not in src:
            errors.append(f"roster snapshot missing {field}")
    print(f"[verify-detection] roster: topology_version="
          f"{src.get('topology_version')} collectors="
          f"{src.get('collectors')} assignments="
          f"{src.get('assignment_count')} snapshots_seen={len(hits)}")
    return src.get("collectors") or []


def check_push_and_absence(errors, collectors):
    code, body = req(
        "POST", f"/{METRICS_INDEX}/_search",
        {"size": 0, "track_total_hits": True,
         "query": {"bool": {"filter": [
             {"term": {"kind": "fluentbit"}},
             {"range": {"@timestamp": {"gte": "now-5m"}}}]}},
         "aggregations": {
             "by_collector": {"terms": {"field": "collector_id", "size": 500}},
             "legacy_node": {"filter": {"exists": {"field": "node"}}}}})
    if code != 200:
        errors.append("cannot read pushed Fluent Bit heartbeats")
        return
    aggs = body.get("aggregations") or {}
    seen = {b["key"] for b in (aggs.get("by_collector") or {}).get(
        "buckets", [])}
    legacy = (aggs.get("legacy_node") or {}).get("doc_count", 0)
    print(f"[verify-detection] pushed heartbeats in the last 5m from "
          f"{sorted(seen)} (roster={sorted(collectors or [])})")
    if legacy:
        print(f"[verify-detection] WARNING: {legacy} kind:fluentbit documents "
              f"still carry the transitional node field. That is only "
              f"expected inside a Stage A dual-write window "
              f"(health_metrics_emit_legacy_node=true).")
    if collectors and EXPECT_PUSH_HEARTBEATS:
        absent = sorted(set(collectors) - seen)
        if absent:
            errors.append(
                f"rostered collectors with no pushed heartbeat: {absent}")

    code, body = req(
        "POST", f"/{METRICS_INDEX}/_search",
        {"size": 0, "track_total_hits": True,
         "query": {"bool": {"filter": [
             {"term": {"kind": "fleet"}},
             {"range": {"@timestamp": {"gte": "now-5m"}}}]}}})
    fleet_docs = ((body.get("hits") or {}).get("total") or {}).get("value", 0)
    print(f"[verify-detection] {fleet_docs} roster absence documents in the "
          f"last 5m (0 is expected until alice-metrics has run one tick after "
          f"the roster was published)")
    if EXPECT_PUSH_HEARTBEATS and collectors and fleet_docs == 0:
        errors.append(
            "no kind:fleet absence documents — collector-down would never "
            "fire, because after the push cutover a dead collector produces "
            "silence, not fb_up:0")


def check_rollup_commits(errors):
    code, _ = req("GET", f"/{ROLLUP_INDEX}")
    if code != 200:
        errors.append(f"rollup index missing: {ROLLUP_INDEX} (HTTP {code})")
        return
    code, body = req(
        "POST", f"/{ROLLUP_INDEX}/_search",
        {"size": 0, "track_total_hits": True,
         "query": {"bool": {"filter": [
             {"term": {"family": "_commit"}},
             {"term": {"complete": True}},
             {"range": {"ts": {"gte": "now-40m"}}}]}}})
    fresh = ((body.get("hits") or {}).get("total") or {}).get("value", 0)
    print(f"[verify-detection] {ROLLUP_INDEX}: {fresh} COMPLETE bucket "
          f"commits in the last 40m (0 is expected on a first deploy — "
          f"alice-trend-rollup starts after this bootstrap)")


def check_aliases():
    for alias in ("infologger", "generic-log-other"):
        code, body = req("GET", f"/_alias/{alias}")
        if code == 200 and body:
            print(f"[verify-detection] {alias}: write alias over "
                  f"{len(body)} backing index(es) {sorted(body)}")
        else:
            print(f"[verify-detection] WARNING: {alias} is a plain index, "
                  f"not a rollover write alias — it will still be wiped "
                  f"whole when it ages out. Convert once with "
                  f"'make deploy-migrate-rollover'")


def check_signal_labels(errors):
    code, body = req(
        "POST", f"/{SIGNALS_INDEX}/_search",
        {"size": 50, "query": {"match_all": {}}})
    if code != 200:
        errors.append(f"cannot read {SIGNALS_INDEX}: HTTP {code}")
        return
    hits = (body.get("hits") or {}).get("hits") or []
    if not hits:
        print(f"[verify-detection] {SIGNALS_INDEX} is empty (expected until "
              f"the first alert or anomaly is projected)")
        return
    required = ["alertname", "source_kind", "cluster_id", "severity",
                "entity_kind", "entity_id", "collector_id", "family",
                "notification_scope", "topology_version"]
    bad = []
    for hit in hits:
        src = hit.get("_source") or {}
        for field in required:
            value = src.get(field)
            if value is None or value == "":
                bad.append(f"{hit.get('_id')}:{field}")
    if bad:
        errors.append(
            f"signals with a missing or empty label: {sorted(set(bad))[:10]} "
            f"— Alertmanager treats a missing label and an empty one as the "
            f"same thing, and an equal: inhibition rule applies when all its "
            f"labels are absent from both alerts, so an omission silently "
            f"mutes unrelated alerts")
    else:
        print(f"[verify-detection] {len(hits)} sampled signals carry every "
              f"identity label with an explicit value")


def check_episode_grouping(errors):
    """The board aggregates on group_id, and no rule may fire anonymously.

    Two opposite failures live here. Fan-out is one card per entity, which the
    grouping fixes. Collapse is every entity folded into one incident that
    names nobody, which happens when a per-entity monitor produces an alert
    with no bucket key and the sentinel entity gets hashed into incident_id.

    Only episodes updated since GROUPING_SINCE are the running projector's own
    output, so only those can fail the deploy. An episode written before the
    projector was upgraded predates the field and is not evidence against the
    code now installed.
    """
    open_q = {"term": {"state": "firing"}}
    code, body = req(
        "POST", f"/{INCIDENTS_INDEX}/_search",
        {"size": 0, "track_total_hits": True, "query": open_q,
         "aggs": {
             "groups": {"cardinality": {"field": "group_id"}},
             "ungrouped": {"filter": {"bool": {
                 "filter": [{"range": {"last_seen": {"gte": GROUPING_SINCE}}}],
                 "must_not": [{"exists": {"field": "group_id"}}]}}},
             "stale_ungrouped": {"filter": {"bool": {
                 "filter": [{"range": {"last_seen": {"lt": GROUPING_SINCE}}}],
                 "must_not": [{"exists": {"field": "group_id"}}]}}},
             "widest": {"terms": {"field": "group_id", "size": 1,
                                  "order": {"_count": "desc"}}},
             "anonymous": {"filter": {"bool": {
                 "filter": [{"term": {"entity_id": ENTITY_SENTINEL}},
                            {"range": {"last_seen": {"gte": "now-1h"}}}],
                 "must_not": [{"terms": {"class": sorted(MONITOR_CLASSES)}}]}},
                 "aggs": {"rules": {"terms": {"field": "alertname",
                                              "size": 10}}}},
             "stale_anonymous": {"filter": {"bool": {
                 "filter": [{"term": {"entity_id": ENTITY_SENTINEL}}],
                 "must_not": [
                     {"terms": {"class": sorted(MONITOR_CLASSES)}}]}}},
         }})
    if code != 200:
        errors.append(f"cannot read {INCIDENTS_INDEX}: HTTP {code}")
        return
    aggs = body.get("aggregations") or {}
    episodes = ((body.get("hits") or {}).get("total") or {}).get("value", 0)
    if not episodes:
        print(f"[verify-detection] {INCIDENTS_INDEX} has no open episode "
              f"(expected on a quiet cluster)")
        return

    ungrouped = (aggs.get("ungrouped") or {}).get("doc_count", 0)
    if ungrouped:
        errors.append(
            f"{ungrouped} of {episodes} open episodes updated since "
            f"{GROUPING_SINCE} carry no group_id — the cockpit board "
            f"aggregates on that field, so those episodes are invisible on it")
    else:
        stale = (aggs.get("stale_ungrouped") or {}).get("doc_count", 0)
        if stale:
            print(f"[verify-detection] WARNING: {stale} open episodes carry no "
                  f"group_id but stopped updating before {GROUPING_SINCE}. "
                  f"They predate the projector that writes the field; clear "
                  f"them from the ops page with 'Clear findings'")

    live = (aggs.get("anonymous") or {})
    if live.get("doc_count"):
        rules = [b.get("key") for b in
                 ((live.get("rules") or {}).get("buckets") or [])]
        errors.append(
            f"{live['doc_count']} open episodes written in the last hour name "
            f"no entity ({sorted(rules)}) — a per-entity rule is folding "
            f"every breach into one anonymous incident, so the operator "
            f"cannot see which entity broke")
    else:
        old = (aggs.get("stale_anonymous") or {}).get("doc_count", 0)
        if old:
            print(f"[verify-detection] WARNING: {old} open episodes name no "
                  f"entity but stopped updating over an hour ago. The running "
                  f"projector is no longer producing them; clear the stale "
                  f"records from the ops page with 'Clear findings'")

    groups = (aggs.get("groups") or {}).get("value", 0)
    widest = ((aggs.get("widest") or {}).get("buckets") or [{}])[0]
    print(f"[verify-detection] {episodes} open episodes fold into {groups} "
          f"cockpit card(s); widest is {widest.get('key', 'none')} with "
          f"{widest.get('doc_count', 0)}")


def board_query():
    """The incident board's own aggregation, read from the saved objects.

    Read rather than restated. A copy here could drift from the panel the
    operator actually loads, and then the gate would pass on a query nobody
    runs.
    """
    with open(COCKPIT_NDJSON) as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("id") != BOARD_PANEL_ID:
                continue
            spec = json.loads(json.loads(
                obj["attributes"]["visState"])["params"]["spec"])
            data = spec["data"][0]
            return data["url"]["body"], data["format"]["property"]
    return None, None


def check_cockpit_board_query(errors):
    """Run the board's own query so a malformed one fails the deploy.

    A Vega panel whose query is rejected draws an empty card list. An empty
    board and a quiet cluster look identical to an operator, so the failure
    that matters most here is the one that is silent by default.
    """
    try:
        body, prop = board_query()
    except (OSError, ValueError, KeyError, IndexError) as e:
        errors.append(
            f"cannot read the incident board query from {COCKPIT_NDJSON}: {e}")
        return
    if body is None:
        errors.append(
            f"{COCKPIT_NDJSON} carries no {BOARD_PANEL_ID} panel, so the "
            f"cockpit has no incident board")
        return

    code, response = req("POST", f"/{INCIDENTS_INDEX}/_search", body)
    if code != 200:
        errors.append(
            f"OpenSearch rejects the incident board's own aggregation: HTTP "
            f"{code} {json.dumps(response)[:300]}. The panel would render an "
            f"empty board and say nothing about why.")
        return

    node = response
    for step in prop.split("."):
        node = node.get(step) if isinstance(node, dict) else None
    if node is None:
        errors.append(
            f"the board query ran but returned nothing at {prop}; the panel "
            f"reads its cards from exactly that path")
        return

    group_agg = prop.split(".")[1]
    declared = set(body["aggs"][group_agg]["aggs"])
    missing = set()
    for bucket in node:
        missing |= declared.difference(bucket)
    if missing:
        errors.append(
            f"the board query returned buckets without {sorted(missing)}; "
            f"every card expression reads those sub-aggregations and would "
            f"render undefined")
        return
    print(f"[verify-detection] the incident board's own aggregation runs and "
          f"returns {len(node)} card(s) with every declared sub-aggregation")


def check_ism(errors):
    for pid in ("alice-generic-info-retention",
                "alice-generic-other-retention",
                "alice-infologger-retention",
                "alice-ad-results-retention",
                "alice-alert-history-retention"):
        code, _ = req("GET", f"/_plugins/_ism/policies/{pid}")
        if code != 200:
            errors.append(f"ISM policy missing: {pid}")


def main():
    errors = []
    check_channels(errors)
    check_actionable_limit(errors)
    bucket_monitors = check_monitors(errors)
    check_live_mappings(errors)
    collectors = check_roster(errors)
    check_push_and_absence(errors, collectors)
    check_rollup_commits(errors)
    check_aliases()
    check_detectors(errors)
    check_forecast_shards(errors)
    check_forecasters(errors)
    check_signal_labels(errors)
    if CHECK_EPISODE_GROUPING:
        check_bucket_alert_entities(errors, bucket_monitors)
        check_episode_grouping(errors)
    check_cockpit_board_query(errors)
    check_ism(errors)

    if errors:
        for e in errors:
            print(f"[verify-detection] FATAL: {e}")
        sys.exit(1)
    print("[verify-detection] OK — detection, identity, roster and signal "
          "layers present")


if __name__ == "__main__":
    main()
