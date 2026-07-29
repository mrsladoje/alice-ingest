"""Generate the trend-* Alerting monitor definitions in files/monitors/.

Build-time tool, like gen_cockpit.py — not copied to the VMs. Re-run after
changing BUCKET_MINUTES, the dwell length, or the count floors, and commit the
regenerated JSON:

    python3 deploy/roles/dashboards/files/gen_trend_monitors.py

BUCKET_MINUTES must match trend_rollup_bucket_seconds in group_vars. The count
floors here are the values baked into the JSON; group_vars
trend_min_slice_docs / trend_min_slice_errors override them at bootstrap.
"""

import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitors")
ROLLUP = "trend-rollup"
COMPOSITE_SIZE = 2000

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

DWELL = (
    "Reads the {rollup} 10m rollup, not raw logs: the 7d baseline is a few "
    "thousand tiny docs per entity. Dwell: fire only if the breach holds on "
    "3 consecutive 10m rollup buckets (~30m), offset 10m so the newest "
    "bucket is always complete. Baseline 7d excluding the last 40m; 24h "
    "fallback when 7d is empty. Schedule 10m. Throttle 30m per alert key "
    "via alice-incluster-alert-sink."
)


def action():
    return {
        "name": "notify",
        "destination_id": "alice-incluster-alert-sink",
        "subject_template": {
            "source": "{{ctx.monitor.name}}", "lang": "mustache"},
        "message_template": {
            "source": (
                "{\"monitor\":\"{{ctx.monitor.name}}\","
                "\"trigger\":\"{{ctx.trigger.name}}\","
                "\"severity\":\"{{ctx.trigger.severity}}\","
                "\"period_end\":\"{{ctx.periodEnd}}\"}"),
            "lang": "mustache"},
        "throttle_enabled": True,
        "throttle": {"value": 30, "unit": "MINUTES"},
        "action_execution_policy": {
            "action_execution_scope": {
                "per_alert": {"actionable_alerts": ["DEDUPED", "NEW"]}}},
    }


def window_aggs(metric):
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


def bucket_monitor(name, description, family, entity_kind, metric, script,
                   paths_key, enabled=True):
    return {
        "type": "monitor",
        "name": name,
        "description": description,
        "monitor_type": "bucket_level_monitor",
        "enabled": enabled,
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
                            "aggregations": window_aggs(metric),
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
                "actions": [action()],
            }
        }],
    }


def query_monitor(name, description, query, script, severity="2",
                  interval=B):
    return {
        "type": "monitor",
        "name": name,
        "description": description,
        "monitor_type": "query_level_monitor",
        "enabled": True,
        "schedule": {"period": {"interval": interval, "unit": "MINUTES"}},
        "inputs": [{"search": {"indices": [ROLLUP], "query": query}}],
        "triggers": [{
            "query_level_trigger": {
                "name": name,
                "severity": severity,
                "condition": {"script": {
                    "source": script, "lang": "painless"}},
                "actions": [action()],
            }
        }],
    }


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

CEILING_CLAUSE = (
    " double lagCeiling = 3600000.0;"
    " if (params.s0_lag > lagCeiling || params.s1_lag > lagCeiling"
    " || params.s2_lag > lagCeiling) { return false; }"
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

monitors = []

for name, family, kind, label, min_docs in [
    ("trend-il-volume", "infologger", "host", "InfoLogger per-host",
     MIN_SLICE_DOCS),
    ("trend-other-volume", "other", "host",
     "generic-log-other per-host", MIN_SLICE_DOCS),
    ("trend-info-volume", "info", "host", "generic-log-info per-host",
     MIN_SLICE_DOCS),
]:
    monitors.append(bucket_monitor(
        name,
        f"Warn when {label} log volume share spikes or collapses. "
        + SHARE_NOTE.format(min_docs=min_docs) + " " + DWELL.format(
            rollup=ROLLUP),
        family, kind, "volume",
        VOLUME_SCRIPT.format(min_docs=f"{min_docs}.0"), "volume"))

for name, family, kind, label, min_docs, min_ef in [
    ("trend-il-ef", "infologger", "host", "InfoLogger per-host error",
     MIN_SLICE_DOCS, MIN_SLICE_ERRORS),
    ("trend-other-errors", "other", "host",
     "generic-log-other per-host error", MIN_SLICE_DOCS, MIN_SLICE_ERRORS),
]:
    monitors.append(bucket_monitor(
        name,
        f"Warn when the {label} share of that host's own volume rises. "
        + EF_NOTE.format(min_docs=min_docs, min_ef=min_ef) + " "
        + DWELL.format(rollup=ROLLUP),
        family, kind, "ef",
        EF_SCRIPT.format(min_docs=f"{min_docs}.0", min_ef=f"{min_ef}.0"),
        "ef"))

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
    monitors.append(bucket_monitor(
        name,
        f"Warn when {label} p95 {field} rises. "
        + LAG_NOTE.format(lag_field=field, floor=250)
        + " " + DWELL.format(rollup=ROLLUP) + extra,
        family, kind, metric,
        LAG_SCRIPT.replace("__CEILING__", ceiling), "lag"))

monitors.append(query_monitor(
    "trend-rollup-stale",
    "Page when the alice-trend-rollup service stops writing. Every trend-* "
    "monitor reads the " + ROLLUP + " index, so a dead rollup silently "
    "blinds the whole trend lane. Fires when the rollup wrote _meta "
    "heartbeats at some point in the last 24h but none in the last 40m "
    "(four rollup periods). The 24h precondition is what keeps a fresh "
    "deploy - where the index is legitimately empty until the service "
    "first runs - from paging on its own bootstrap.",
    {
        "size": 0,
        "query": {"bool": {"filter": [
            {"term": {"family": "_meta"}},
            {"range": {"ts": {
                "gte": "{{period_end}}||-24h",
                "lte": "{{period_end}}",
                "format": "epoch_millis"}}},
        ]}},
        "aggregations": {
            "recent": {"filter": {"range": {"ts": {
                "gte": "{{period_end}}||" + OFFSET,
                "lte": "{{period_end}}",
                "format": "epoch_millis"}}}},
        },
    },
    "def r = ctx.results[0];"
    " if (r.hits.total.value == 0) { return false; }"
    " return r.aggregations.recent.doc_count == 0;",
    severity="1"))

monitors.append(query_monitor(
    "trend-entity-cap",
    "Warn when the trend lane is close to its entity ceiling. The rollup "
    "pages its composite aggregation but stops at trend_rollup_max_entities, "
    "and each trend monitor's own composite is capped at "
    f"{COMPOSITE_SIZE}; past that, entities are silently unmonitored. Fires "
    "when any rollup bucket in the last hour reports entity_count at or "
    "above the cap (substituted from trend_entity_cap_warn at bootstrap) or "
    "flags itself truncated.",
    {
        "size": 0,
        "query": {"bool": {"filter": [
            {"term": {"family": "_meta"}},
            {"range": {"ts": {
                "gte": "{{period_end}}||-1h",
                "lte": "{{period_end}}",
                "format": "epoch_millis"}}},
        ]}},
        "aggregations": {
            "max_entities": {"max": {"field": "entity_count"}},
            "truncated": {"filter": {"term": {"truncated": True}}},
        },
    },
    "double entityCap = 1800.0;"
    " def agg = ctx.results[0].aggregations;"
    " if (agg.truncated.doc_count > 0) { return true; }"
    " def m = agg.max_entities.value;"
    " return m != null && m >= entityCap;"))

for m in monitors:
    path = os.path.join(OUT, m["name"] + ".json")
    with open(path, "w") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    print(f"wrote {path}")
