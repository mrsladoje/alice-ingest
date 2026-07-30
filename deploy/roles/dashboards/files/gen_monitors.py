"""Generate every Alerting monitor definition in files/monitors/.

Build-time tool, like gen_cockpit.py — not copied to the VMs. It owns all 25
monitors so the entity payload, trigger shape and identity fields cannot drift
apart across hand-edited JSON. Re-run and commit the regenerated files:

    python3 deploy/roles/dashboards/files/gen_monitors.py

BUCKET_MINUTES must match trend_rollup_bucket_seconds in group_vars. Numeric
guards baked in here are re-substituted from group_vars by alerting.sh at
bootstrap; entity kinds and notification scopes come from signal_catalog.json.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "monitors")
CATALOG = json.load(open(os.path.join(HERE, "signal_catalog.json")))

ROLLUP = "trend-rollup"
METRICS = "cockpit-metrics"
AD_RESULTS = ".opendistro-anomaly-results*"
SINK = "alice-incluster-alert-sink"
BREAKGLASS_SINK = "alice-breakglass-sink"
COMPOSITE_SIZE = 2000
FLEET_COMPOSITE_SIZE = 1000

BUCKET_MINUTES = 10
DWELL_SLICES = 3
MIN_SLICE_DOCS = 50
MIN_SLICE_ERRORS = 10

B = BUCKET_MINUTES
OFFSET = f"-{B * (DWELL_SLICES + 1)}m"
WINDOWS = (
    [(f"slice{i}", f"-{B * (i + 2)}m", f"-{B * (i + 1)}m")
     for i in range(DWELL_SLICES)]
    + [("baseline_7d", "-7d", OFFSET), ("baseline_24h", "-24h", OFFSET)]
)

BUCKET_ENTITY = "{{#ctx.newAlerts}}{{{bucket_keys}}} {{/ctx.newAlerts}}"


def identity(name):
    meta = CATALOG["monitors"][name]
    return meta["entity_kind"], meta["notification_scope"], meta["feed"]


def payload(name, entity_expr, delivery="alertmanager"):
    kind, scope, feed = identity(name)
    return (
        '{"monitor":"{{ctx.monitor.name}}",'
        '"trigger":"{{ctx.trigger.name}}",'
        '"severity":"{{ctx.trigger.severity}}",'
        '"period_start":"{{ctx.periodStart}}",'
        '"period_end":"{{ctx.periodEnd}}",'
        f'"entity_kind":"{kind}",'
        f'"notification_scope":"{scope}",'
        f'"feed":"{feed}",'
        f'"delivery_path":"{delivery}",'
        f'"entity":"{entity_expr}"}}'
    )


def action(name, entity_expr, per_alert, delivery="alertmanager"):
    sink = BREAKGLASS_SINK if delivery == "breakglass" else SINK
    act = {
        "name": "notify",
        "destination_id": sink,
        "subject_template": {
            "source": "{{ctx.monitor.name}}", "lang": "mustache"},
        "message_template": {
            "source": payload(name, entity_expr, delivery), "lang": "mustache"},
        "throttle_enabled": True,
        "throttle": {"value": 30, "unit": "MINUTES"},
    }
    if per_alert:
        act["action_execution_policy"] = {
            "action_execution_scope": {
                "per_alert": {"actionable_alerts": ["DEDUPED", "NEW"]}}}
    return act


def bucket_monitor(name, description, indices, query, aggregations,
                   buckets_path, script, severity, interval,
                   composite_field, composite_size):
    inner = dict(aggregations)
    return {
        "type": "monitor",
        "name": name,
        "description": description,
        "monitor_type": "bucket_level_monitor",
        "enabled": True,
        "schedule": {"period": {"interval": interval, "unit": "MINUTES"}},
        "inputs": [{
            "search": {
                "indices": [indices],
                "query": {
                    "size": 0,
                    "query": query,
                    "aggregations": {
                        "composite_agg": {
                            "composite": {
                                "size": composite_size,
                                "sources": [
                                    {composite_field: {
                                        "terms": {"field": composite_field}}}
                                ],
                            },
                            "aggregations": inner,
                        }
                    },
                },
            }
        }],
        "triggers": [{
            "bucket_level_trigger": {
                "name": name,
                "severity": severity,
                "condition": {
                    "buckets_path": buckets_path,
                    "parent_bucket_path": "composite_agg",
                    "script": {"source": script, "lang": "painless"},
                },
                "actions": [action(name, BUCKET_ENTITY, True)],
            }
        }],
    }


def query_monitor(name, description, indices, query, script, severity,
                  interval, entity, delivery="alertmanager"):
    return {
        "type": "monitor",
        "name": name,
        "description": description,
        "monitor_type": "query_level_monitor",
        "enabled": True,
        "schedule": {"period": {"interval": interval, "unit": "MINUTES"}},
        "inputs": [{"search": {"indices": [indices], "query": query}}],
        "triggers": [{
            "query_level_trigger": {
                "name": name,
                "severity": severity,
                "condition": {"script": {
                    "source": script, "lang": "painless"}},
                "actions": [action(name, entity, False, delivery)],
            }
        }],
    }


def window(minutes):
    return {"range": {"@timestamp": {
        "gte": "{{period_end}}||-" + f"{minutes}m",
        "lte": "{{period_end}}",
        "format": "epoch_millis"}}}


def kind_window(kind, minutes):
    return {"bool": {"filter": [{"term": {"kind": kind}}, window(minutes)]}}


ENTITY_NOTE = (
    "Every action payload names its entity, so a fleet-wide breach is "
    "attributable even when the plugin rewrites per_alert to per_execution "
    "above the actionable-alert limit."
)

monitors = []

monitors.append(bucket_monitor(
    "collector-down",
    "Page when a rostered collector stops heartbeating. After the Fluent Bit "
    "push cutover a dead collector emits nothing at all, so this reads the "
    "absence documents the thin poller derives from the published roster "
    "(kind:fleet, heartbeat_missing) rather than an observed fb_up:0 sample, "
    "which no longer exists. " + ENTITY_NOTE,
    METRICS, kind_window("fleet", 2),
    {"max_missing": {"max": {"field": "heartbeat_missing"}}},
    {"max_missing": "max_missing"},
    "params.max_missing == 1", "1", 1, "collector_id", FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "collector-unhealthy",
    "Warn when a collector is heartbeating but its own health check is "
    "failing. Bucketed on collector_id, never on the ambiguous node field. "
    + ENTITY_NOTE,
    METRICS, kind_window("fluentbit", 2),
    {"max_fb_healthy": {"max": {"field": "fb_healthy"}}},
    {"max_fb_healthy": "max_fb_healthy"},
    "params.max_fb_healthy == 0", "2", 1, "collector_id",
    FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "data-loss",
    "Page when a collector drops records. Bucketed on collector_id. "
    + ENTITY_NOTE,
    METRICS, kind_window("fluentbit", 2),
    {"sum_dropped": {"sum": {"field": "output_dropped_delta"}}},
    {"sum_dropped": "sum_dropped"},
    "params.sum_dropped > 0", "1", 1, "collector_id", FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "shipping-breaking",
    "Warn when a collector exhausts its output retries. Bucketed on "
    "collector_id. " + ENTITY_NOTE,
    METRICS, kind_window("fluentbit", 2),
    {"sum_retries_failed": {"sum": {"field": "output_retries_failed_delta"}}},
    {"sum_retries_failed": "sum_retries_failed"},
    "params.sum_retries_failed > 0", "2", 1, "collector_id",
    FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "disk-cliff-warn",
    "Warn before an OpenSearch node hits the read-only disk watermark. "
    "Bucketed on os_node, never on the ambiguous node field. " + ENTITY_NOTE,
    METRICS, kind_window("node", 2),
    {"max_disk": {"max": {"field": "disk_used_percent"}}},
    {"max_disk": "max_disk"},
    "params.max_disk > 85 && params.max_disk <= 92", "2", 1, "os_node",
    FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "disk-cliff-page",
    "Page when an OpenSearch node is about to go read-only. Bucketed on "
    "os_node. " + ENTITY_NOTE,
    METRICS, kind_window("node", 2),
    {"max_disk": {"max": {"field": "disk_used_percent"}}},
    {"max_disk": "max_disk"},
    "params.max_disk > 92", "1", 1, "os_node", FLEET_COMPOSITE_SIZE))

monitors.append(bucket_monitor(
    "heap-spiral",
    "Warn when an OpenSearch node stays above 90% heap for five minutes. "
    "Bucketed on os_node. " + ENTITY_NOTE,
    METRICS, kind_window("node", 5),
    {"min_heap": {"min": {"field": "heap_percent"}}},
    {"min_heap": "min_heap"},
    "params.min_heap > 90", "2", 1, "os_node", FLEET_COMPOSITE_SIZE))

monitors.append(query_monitor(
    "cluster-red",
    "Page when the cluster reports red. Entity is constant — the deployment "
    "itself — so the payload templates a literal scope rather than a bucket "
    "key.",
    METRICS,
    {"size": 0, "query": {"bool": {"filter": [
        {"term": {"kind": "cluster"}},
        {"term": {"cluster_status_code": 2}},
        window(2)]}}},
    "ctx.results[0].hits.total.value > 0", "1", 1, "alice-logs"))

monitors.append(query_monitor(
    "shards-stuck",
    "Warn when shards stay unassigned for five minutes. Constant entity.",
    METRICS,
    {"size": 0, "query": kind_window("cluster", 5),
     "aggregations": {"min_unassigned": {
         "min": {"field": "unassigned_shards"}}}},
    "ctx.results[0].aggregations.min_unassigned.value > 0", "2", 1,
    "alice-logs"))

monitors.append(query_monitor(
    "telemetry-silence",
    "Page when the control-plane sampler goes dark: no kind:cluster and no "
    "kind:osd documents for five minutes means alice-metrics is dead. This is "
    "deliberately NOT a whole-index silence test any more — collector "
    "heartbeats are pushed by Fluent Bit and survive a dead poller, so an "
    "undifferentiated silence alert could not scope either inhibition rule.",
    METRICS,
    {"size": 0, "query": {"bool": {"filter": [
        {"terms": {"kind": ["cluster", "osd"]}}, window(5)]}}},
    "ctx.results[0].hits.total.value == 0", "1", 1, "alice-metrics"))

monitors.append(query_monitor(
    "fleet-fb-silence",
    "Page when a large fraction of the rostered collectors stop heartbeating "
    "at once — the fleet-wide counterpart of collector-down, so an "
    "everyone-lost-credentials failure surfaces as one alert instead of "
    "waiting for N per-collector pages. Fires only while the poller is alive "
    "enough to write absence documents; total darkness is telemetry-silence.",
    METRICS,
    {"size": 0, "query": kind_window("fleet", 2),
     "aggregations": {
         "missing": {"sum": {"field": "heartbeat_missing"}},
         "rostered": {"value_count": {"field": "heartbeat_missing"}}}},
    "double fleetSilenceFraction = 0.5;"
    " def agg = ctx.results[0].aggregations;"
    " double rostered = agg.rostered.value;"
    " if (rostered <= 0) { return false; }"
    " return (agg.missing.value / rostered) >= fleetSilenceFraction;",
    "1", 1, "all"))

monitors.append(query_monitor(
    "ad-high-grade",
    "Fleet-wide tripwire on a high real-time anomaly grade. Deliberately a "
    "higher, non-canonical floor than the projection lane, and restricted to "
    "real-time results (must_not exists task_id) so a historical analysis run "
    "now can never page.",
    AD_RESULTS,
    {"size": 1,
     "sort": [{"anomaly_grade": {"order": "desc"}}],
     "query": {"bool": {
         "filter": [{"range": {"execution_end_time": {
             "gte": "{{period_end}}||-2m",
             "lte": "{{period_end}}",
             "format": "epoch_millis"}}}],
         "must_not": [{"exists": {"field": "task_id"}}]}},
     "aggregations": {"max_anomaly_grade": {
         "max": {"field": "anomaly_grade"}}}},
    "ctx.results[0].hits.total.value > 0"
    " && ctx.results[0].aggregations.max_anomaly_grade.value > 0.7"
    " && ctx.results[0].hits.hits[0]._source.confidence > 0.7",
    "1", 1, "all"))

METRIC_AGGS = {
    "volume": {
        "docs": {"sum": {"field": "doc_count"}},
        "fleet": {"sum": {"field": "fleet_count"}},
    },
    "ef": {
        "docs": {"sum": {"field": "doc_count"}},
        "ef": {"sum": {"field": "ef_count"}},
    },
    "entry_lag": {
        "lag": {"avg": {"field": "p95_entry_lag_ms"}},
        "docs": {"sum": {"field": "doc_count"}},
    },
    "shipping_lag": {
        "lag": {"avg": {"field": "p95_shipping_lag_ms"}},
        "docs": {"sum": {"field": "doc_count"}},
    },
}

BUCKETS_PATHS = {
    "volume": {
        "s0_docs": "slice0>docs", "s0_fleet": "slice0>fleet",
        "s1_docs": "slice1>docs", "s1_fleet": "slice1>fleet",
        "s2_docs": "slice2>docs", "s2_fleet": "slice2>fleet",
        "b7_docs": "baseline_7d>docs", "b7_fleet": "baseline_7d>fleet",
        "b24_docs": "baseline_24h>docs", "b24_fleet": "baseline_24h>fleet",
        "b24_buckets": "baseline_24h._count",
    },
    "ef": {
        "s0_docs": "slice0>docs", "s0_ef": "slice0>ef",
        "s1_docs": "slice1>docs", "s1_ef": "slice1>ef",
        "s2_docs": "slice2>docs", "s2_ef": "slice2>ef",
        "b7_docs": "baseline_7d>docs", "b7_ef": "baseline_7d>ef",
        "b24_docs": "baseline_24h>docs", "b24_ef": "baseline_24h>ef",
    },
    "lag": {
        "s0_lag": "slice0>lag", "s1_lag": "slice1>lag", "s2_lag": "slice2>lag",
        "s0_docs": "slice0>docs", "s1_docs": "slice1>docs",
        "s2_docs": "slice2>docs",
        "b7_lag": "baseline_7d>lag", "b24_lag": "baseline_24h>lag",
    },
}

VOLUME_SCRIPT = (
    "double minDocs = {min_docs};"
    " double f0 = params.s0_fleet; double f1 = params.s1_fleet;"
    " double f2 = params.s2_fleet;"
    " double bd = params.b7_docs; double bf = params.b7_fleet;"
    " if (bf <= 0) {{ bd = params.b24_docs; bf = params.b24_fleet; }}"
    " if (bf <= 0) {{ return false; }}"
    " double base = bd / bf;"
    " if (base <= 0) {{ return false; }}"
    " double sh0 = f0 > 0 ? params.s0_docs / f0 : 0.0;"
    " double sh1 = f1 > 0 ? params.s1_docs / f1 : 0.0;"
    " double sh2 = f2 > 0 ? params.s2_docs / f2 : 0.0;"
    " double r0 = sh0 / base; double r1 = sh1 / base; double r2 = sh2 / base;"
    " boolean hi = params.s0_docs >= minDocs && params.s1_docs >= minDocs"
    " && params.s2_docs >= minDocs && r0 >= 2.0 && r1 >= 2.0 && r2 >= 2.0;"
    " double liveBuckets = params.b24_buckets;"
    " boolean liveBase = params.b24_docs > 0 && liveBuckets > 0"
    " && (params.b24_docs / liveBuckets) >= minDocs;"
    " boolean lo = liveBase && r0 <= 0.5 && r1 <= 0.5 && r2 <= 0.5;"
    " return hi || lo;"
)

EF_SCRIPT = (
    "double minDocs = {min_docs}; double minEf = {min_ef};"
    " double minShare = 0.001;"
    " if (params.s0_docs < minDocs || params.s1_docs < minDocs"
    " || params.s2_docs < minDocs) {{ return false; }}"
    " if (params.s0_ef < minEf || params.s1_ef < minEf"
    " || params.s2_ef < minEf) {{ return false; }}"
    " double bd = params.b7_docs; double be = params.b7_ef;"
    " if (bd <= 0) {{ bd = params.b24_docs; be = params.b24_ef; }}"
    " if (bd <= 0) {{ return false; }}"
    " double base = be / bd;"
    " if (base < minShare) {{ base = minShare; }}"
    " double r0 = (params.s0_ef / params.s0_docs) / base;"
    " double r1 = (params.s1_ef / params.s1_docs) / base;"
    " double r2 = (params.s2_ef / params.s2_docs) / base;"
    " return r0 >= 2.0 && r1 >= 2.0 && r2 >= 2.0;"
)

LAG_SCRIPT = (
    "if (params.s0_lag == null || params.s1_lag == null"
    " || params.s2_lag == null) { return false; }"
    " double minLagDocs = 100.0;"
    " if (params.s0_docs < minLagDocs || params.s1_docs < minLagDocs"
    " || params.s2_docs < minLagDocs) { return false; }"
    " double lagFloor = 250.0;"
    " if (params.s0_lag < lagFloor || params.s1_lag < lagFloor"
    " || params.s2_lag < lagFloor) { return false; }"
    "__CEILING__"
    " double base = (params.b7_lag != null && params.b7_lag > 0)"
    " ? params.b7_lag : ((params.b24_lag != null) ? params.b24_lag : 0);"
    " if (base <= 0) { return false; }"
    " return (params.s0_lag / base) >= 2.0"
    " && (params.s1_lag / base) >= 2.0 && (params.s2_lag / base) >= 2.0;"
)

CEILING_CLAUSE = (
    " double lagCeiling = 3600000.0;"
    " if (params.s0_lag > lagCeiling || params.s1_lag > lagCeiling"
    " || params.s2_lag > lagCeiling) { return false; }"
)

DWELL = (
    "Reads the {rollup} 10m rollup, not raw logs: the 7d baseline is a few "
    "thousand tiny docs per entity. Dwell: fire only if the breach holds on "
    "3 consecutive 10m rollup buckets (~30m), offset 10m so the newest "
    "bucket is always complete. Baseline 7d excluding the last 40m; 24h "
    "fallback when 7d is empty. Schedule 10m. Throttle 30m per alert key "
    "via alice-incluster-alert-sink."
)

SHARE_NOTE = (
    "Metric is this entity's SHARE of fleet volume (its docs / all docs in "
    "the same 10m bucket), so a fleet-wide ramp such as run start cancels "
    "out and only a disproportionate host fires. Rising 2x share, collapse "
    "<=0.5x share - all three slices the same direction. An entity absent "
    "from a slice counts as share 0, so full silence is caught. Rising "
    "needs >={min_docs} docs in every slice and collapse needs a 24h "
    "baseline averaging >={min_docs} docs per present bucket, which is also "
    "the retired-host guard."
)

EF_NOTE = (
    "Metric is the error SHARE of this entity's own volume (error docs / "
    "all its docs), not an absolute count, so a host that doubles traffic "
    "and doubles errors stays quiet and does not double-alert alongside the "
    "volume monitor. Needs >={min_docs} docs and >={min_ef} error docs in "
    "every slice; a historically error-free entity is compared against a "
    "0.1% floor instead of dividing by zero."
)

LAG_NOTE = (
    "Metric is the per-bucket p95 of {lag_field}, not the mean: backlogs "
    "show in the tail first. The baseline averages ~1000 per-bucket p95 "
    "values, so a slice p95 is compared against a typical bucket's p95 - "
    "like against like. Needs >=100 records in every slice, because a p95 "
    "over a handful of records is just the maximum. Floor {floor} ms "
    "absolute as well as 2x baseline (both substituted at bootstrap)."
)

REPLAY_NOTE = (
    " Self-gating instead of flag-gated: a slice above the ceiling "
    "(trend_entry_lag_ceiling_ms, 1h) is archive age, not pipeline health, "
    "so under preserved June replay - where entry lag is about a month - "
    "this monitor is naturally silent, and in production - where entry lag "
    "is seconds - it is naturally live. Nothing to switch on at cutover."
)


def trend_window_aggs(metric):
    aggs = {}
    for name, gte, lt in WINDOWS:
        rng = {"gte": "{{period_end}}||" + gte,
               "lt": "{{period_end}}||" + lt,
               "format": "epoch_millis"}
        aggs[name] = {
            "filter": {"range": {"ts": rng}},
            "aggregations": json.loads(json.dumps(METRIC_AGGS[metric])),
        }
    return aggs


def trend_monitor(name, description, family, entity_kind, metric, script,
                  paths_key):
    return {
        "type": "monitor",
        "name": name,
        "description": description,
        "monitor_type": "bucket_level_monitor",
        "enabled": True,
        "schedule": {"period": {"interval": B, "unit": "MINUTES"}},
        "inputs": [{
            "search": {
                "indices": [ROLLUP],
                "query": {
                    "size": 0,
                    "query": {"bool": {"filter": [
                        {"term": {"family": family}},
                        {"term": {"entity_kind": entity_kind}},
                        {"range": {"ts": {
                            "gte": "{{period_end}}||-7d",
                            "lte": "{{period_end}}",
                            "format": "epoch_millis"}}},
                    ]}},
                    "aggregations": {
                        "composite_agg": {
                            "composite": {
                                "size": COMPOSITE_SIZE,
                                "sources": [
                                    {"entity": {
                                        "terms": {"field": "entity"}}}
                                ],
                            },
                            "aggregations": trend_window_aggs(metric),
                        }
                    },
                },
            }
        }],
        "triggers": [{
            "bucket_level_trigger": {
                "name": name,
                "severity": "2",
                "condition": {
                    "buckets_path": BUCKETS_PATHS[paths_key],
                    "parent_bucket_path": "composite_agg",
                    "script": {"source": script, "lang": "painless"},
                },
                "actions": [action(name, BUCKET_ENTITY, True)],
            }
        }],
    }


for name, family, kind, label in [
    ("trend-il-volume", "infologger", "host", "InfoLogger per-host"),
    ("trend-other-volume", "other", "host", "generic-log-other per-host"),
    ("trend-info-volume", "info", "host", "generic-log-info per-host"),
]:
    monitors.append(trend_monitor(
        name,
        f"Warn when {label} log volume share spikes or collapses. "
        + SHARE_NOTE.format(min_docs=MIN_SLICE_DOCS) + " "
        + DWELL.format(rollup=ROLLUP) + " " + ENTITY_NOTE,
        family, kind, "volume",
        VOLUME_SCRIPT.format(min_docs=f"{MIN_SLICE_DOCS}.0"), "volume"))

for name, family, kind, label in [
    ("trend-il-ef", "infologger", "host", "InfoLogger per-host error"),
    ("trend-other-errors", "other", "host",
     "generic-log-other per-host error"),
]:
    monitors.append(trend_monitor(
        name,
        f"Warn when the {label} share of that host's own volume rises. "
        + EF_NOTE.format(min_docs=MIN_SLICE_DOCS, min_ef=MIN_SLICE_ERRORS)
        + " " + DWELL.format(rollup=ROLLUP) + " " + ENTITY_NOTE,
        family, kind, "ef",
        EF_SCRIPT.format(min_docs=f"{MIN_SLICE_DOCS}.0",
                         min_ef=f"{MIN_SLICE_ERRORS}.0"), "ef"))

for name, family, kind, label, metric, field, extra in [
    ("trend-il-entry-lag", "infologger", "host", "InfoLogger per-host",
     "entry_lag", "enter_system_lag_ms", REPLAY_NOTE),
    ("trend-info-entry-lag", "info", "host", "generic-log-info per-host",
     "entry_lag", "enter_system_lag_ms", REPLAY_NOTE),
    ("trend-il-shipping-lag", "infologger", "node",
     "InfoLogger per-collector", "shipping_lag", "ingest_lag_ms", ""),
    ("trend-info-shipping-lag", "info", "node",
     "generic-log-info per-collector", "shipping_lag", "ingest_lag_ms", ""),
]:
    ceiling = CEILING_CLAUSE if metric == "entry_lag" else ""
    monitors.append(trend_monitor(
        name,
        f"Warn when {label} p95 {field} rises. "
        + LAG_NOTE.format(lag_field=field, floor=250)
        + " " + DWELL.format(rollup=ROLLUP) + " " + ENTITY_NOTE + extra,
        family, kind, metric,
        LAG_SCRIPT.replace("__CEILING__", ceiling), "lag"))

monitors.append(query_monitor(
    "trend-rollup-stale",
    "Page when the alice-trend-rollup service stops publishing COMPLETE "
    "bucket commits. A commit is written only after every required cohort "
    "query succeeded, every entity bulk item was accepted and every per-"
    "cohort metadata row landed, so this watchdog can no longer report a "
    "healthy lane while some cohorts are silently missing. Fires when "
    "complete commits existed at some point in the last 24h but none landed "
    "in the last 40m; the 24h precondition keeps a fresh deploy from paging "
    "on its own bootstrap.",
    ROLLUP,
    {"size": 0,
     "query": {"bool": {"filter": [
         {"term": {"family": "_commit"}},
         {"term": {"complete": True}},
         {"range": {"ts": {
             "gte": "{{period_end}}||-24h",
             "lte": "{{period_end}}",
             "format": "epoch_millis"}}}]}},
     "aggregations": {
         "recent": {"filter": {"range": {"ts": {
             "gte": "{{period_end}}||" + OFFSET,
             "lte": "{{period_end}}",
             "format": "epoch_millis"}}}}}},
    "def r = ctx.results[0];"
    " if (r.hits.total.value == 0) { return false; }"
    " return r.aggregations.recent.doc_count == 0;",
    "1", B, "alice-trend-rollup"))

monitors.append(query_monitor(
    "trend-entity-cap",
    "Warn when the trend lane is close to its entity ceiling. The rollup "
    "pages its composite aggregation but stops at trend_rollup_max_entities, "
    f"and each trend monitor's own composite is capped at {COMPOSITE_SIZE}; "
    "past that, entities are silently unmonitored. Fires when any cohort "
    "metadata row in the last hour reports entity_count at or above the cap "
    "(substituted from trend_entity_cap_warn at bootstrap) or flags itself "
    "truncated. Truncated cohorts still publish diagnostic metadata here, "
    "but they can never publish a complete bucket commit.",
    ROLLUP,
    {"size": 0,
     "query": {"bool": {"filter": [
         {"term": {"family": "_meta"}},
         {"range": {"ts": {
             "gte": "{{period_end}}||-1h",
             "lte": "{{period_end}}",
             "format": "epoch_millis"}}}]}},
     "aggregations": {
         "max_entities": {"max": {"field": "entity_count"}},
         "truncated": {"filter": {"term": {"truncated": True}}}}},
    "double entityCap = 1800.0;"
    " def agg = ctx.results[0].aggregations;"
    " if (agg.truncated.doc_count > 0) { return true; }"
    " def m = agg.max_entities.value;"
    " return m != null && m >= entityCap;",
    "2", B, "alice-trend-rollup"))

monitors.append(query_monitor(
    "signal-projector-stale",
    "Page when alice-signal-projector stops running. It is the only writer of "
    "alice-signals and the only thing re-sending active alerts to "
    "Alertmanager, so a stalled projector silently resolves live incidents. "
    "This monitor cannot page through the projector that just died, so its "
    "action posts straight to alice-notification-ingest, tagged "
    "delivery_path breakglass. That bypass is limited to this monitor and "
    "alertmanager-down.",
    METRICS,
    {"size": 0,
     "query": {"bool": {"filter": [
         {"term": {"kind": "projector"}},
         window(1440)]}},
     "aggregations": {
         "recent": {"filter": window(10)}}},
    "def r = ctx.results[0];"
    " if (r.hits.total.value == 0) { return false; }"
    " return r.aggregations.recent.doc_count == 0;",
    "1", 1, "alice-signal-projector", "breakglass"))

monitors.append(query_monitor(
    "alertmanager-down",
    "Page when the projector cannot reach Alertmanager. Alertmanager does not "
    "persist alerts, so a dead one loses every active notification until the "
    "next re-send succeeds. Break-glass delivery for the same reason as "
    "signal-projector-stale: the ordinary path runs through the component "
    "being reported dead.",
    METRICS,
    {"size": 0,
     "query": {"bool": {"filter": [
         {"term": {"kind": "alertmanager"}},
         window(5)]}},
     "aggregations": {"max_up": {"max": {"field": "am_up"}}}},
    "def r = ctx.results[0];"
    " if (r.hits.total.value == 0) { return false; }"
    " def v = r.aggregations.max_up.value;"
    " return v != null && v == 0;",
    "1", 1, "alertmanager", "breakglass"))


def main():
    names = {m["name"] for m in monitors}
    declared = set(CATALOG["monitors"].keys())
    if names != declared:
        raise SystemExit(
            f"generated monitors {sorted(names ^ declared)} disagree with "
            f"signal_catalog.json")
    for stale in sorted(os.listdir(OUT)):
        if stale.endswith(".json") and stale[:-5] not in names:
            os.remove(os.path.join(OUT, stale))
            print(f"removed {stale}")
    for m in monitors:
        path = os.path.join(OUT, m["name"] + ".json")
        with open(path, "w") as f:
            json.dump(m, f, indent=2)
            f.write("\n")
        print(f"wrote {path}")
    print(f"{len(monitors)} monitors")


if __name__ == "__main__":
    main()
