import json
import os

DEFAULT_PATH = os.environ.get(
    "SIGNAL_CATALOG", "/opt/alice-ingest/init/signal_catalog.json")

_cache = {}


# Operator-facing language belongs beside the signal identity contract.  Rule
# names are stable machine keys; they are not diagnoses.  Keep every source
# covered here so the incident projector can write a useful episode document
# without making the dashboard reverse-engineer monitor scripts.
MONITOR_PRESENTATION = {
    "collector-down": (
        "Collector stopped heartbeating",
        "No heartbeat arrived from rostered collector {entity} in the last "
        "two minutes.",
        "Check the host and Fluent Bit service, then its network path to the "
        "control node."),
    "collector-unhealthy": (
        "Collector health check is failing",
        "Collector {entity} is still heartbeating, but Fluent Bit reported "
        "itself unhealthy during the last two minutes.",
        "Inspect the Fluent Bit health endpoint and service journal on "
        "{entity}."),
    "data-loss": (
        "Collector dropped log records",
        "Collector {entity} reported one or more dropped output records in "
        "the last two minutes; those records cannot be recovered here.",
        "Inspect Fluent Bit output errors, retries, and destination capacity "
        "on {entity}."),
    "shipping-breaking": (
        "Collector exhausted output retries",
        "Collector {entity} reported at least one output retry that could not "
        "be completed in the last two minutes.",
        "Check OpenSearch reachability and the Fluent Bit output log on "
        "{entity}."),
    "disk-cliff-warn": (
        "OpenSearch disk is above 85%",
        "OpenSearch node {entity} used more than 85% and at most 92% of disk "
        "during the last two minutes.",
        "Free space or move shards before the node reaches the read-only "
        "watermark."),
    "disk-cliff-page": (
        "OpenSearch disk is above 92%",
        "OpenSearch node {entity} used more than 92% of disk during the last "
        "two minutes and is close to becoming read-only.",
        "Free space or move shards immediately; check for read-only index "
        "blocks."),
    "disk-fill-forecast": (
        "A node is predicted to run out of disk",
        "At least one node in the OpenSearch cluster is forecast to cross the "
        "disk warning watermark within the next 24 hours. This is a "
        "prediction, not an observation, and it names no node: forecast "
        "results carry their entity in a nested field, so the monitor is "
        "fleet-scoped.",
        "Open Forecasting in Dashboards and read the disk-fill chart to see "
        "which node is rising and when it crosses. Then free space or move "
        "shards before disk-cliff-warn fires on the real value."),
    "heap-spiral": (
        "OpenSearch heap stayed above 90%",
        "OpenSearch node {entity} remained above 90% JVM heap for five "
        "minutes.",
        "Inspect hot queries, indexing pressure, and garbage collection on "
        "{entity}."),
    "admission-rejections": (
        "OpenSearch refused requests to protect a node",
        "OpenSearch node {entity} rejected at least one search or write in the "
        "last five minutes because its rolling average processor use passed "
        "the admission-control limit. A refused search is a detection interval "
        "that never ran; a refused write is log records that were dropped.",
        "Find what is loading {entity}. On a worker that is usually "
        "reconstruction, not this stack. Read Query Insights for expensive "
        "searches, then confirm the detectors on that node still report."),
    "cluster-red": (
        "OpenSearch cluster is red",
        "The alice-logs cluster reported RED during the last two minutes, so "
        "at least one primary shard is unavailable.",
        "Open Index Management, identify unassigned primaries, and restore "
        "allocation or the failed node."),
    "shards-stuck": (
        "Shards remained unassigned",
        "One or more OpenSearch shards remained unassigned throughout the "
        "last five minutes.",
        "Use allocation explain to identify the blocking disk, replica, or "
        "node constraint."),
    "telemetry-silence": (
        "Control-plane telemetry stopped",
        "No cluster or Dashboards health sample was written for five "
        "minutes; alice-metrics is probably not completing cycles.",
        "Check alice-metrics and its access to OpenSearch and Dashboards."),
    "fleet-fb-silence": (
        "At least half the collector fleet is silent",
        "At least 50% of rostered collectors were missing heartbeats in the "
        "last two minutes.",
        "Look for a shared credential, network, destination, or deployment "
        "failure before treating collectors individually."),
    "log-family-silence": (
        "The {entity} log stream stopped",
        "{entity} logs were arriving steadily for the last 24 hours, then "
        "every host stopped at once, with no complete 10-minute bucket in "
        "the last 40 minutes.",
        "Treat this as one fault, not one per host. Check the producers, the "
        "replay, and the collectors for {entity} before reading any per-host "
        "volume alert."),
    "ad-high-grade": (
        "High-confidence fleet anomaly",
        "A real-time anomaly exceeded grade 0.7 and confidence 0.7 during the "
        "last two minutes.",
        "Open the episode's detector evidence and identify the affected "
        "feature before escalating."),
    "trend-rollup-stale": (
        "Trend rollup stopped committing",
        "The rollup had completed before, but no complete all-cohort commit "
        "landed in the last 40 minutes.",
        "Check alice-trend-rollup logs and failed cohort or bulk-write "
        "diagnostics."),
    "trend-entity-cap": (
        "Trend monitoring reached its entity cap",
        "A rollup cohort was truncated or reported at least 1,800 entities "
        "during the last hour, so some entities may be unmonitored.",
        "Inspect cohort metadata, then raise capacity or reduce the intended "
        "roster before relying on trend coverage."),
    "signal-projector-stale": (
        "Incident projector stopped",
        "alice-signal-projector had run before but wrote no successful "
        "heartbeat for ten minutes.",
        "Check alice-signal-projector immediately; incident state and "
        "Alertmanager re-sends are no longer trustworthy."),
    "alertmanager-down": (
        "Alertmanager is unreachable",
        "The projector reported Alertmanager unavailable during the last "
        "five minutes.",
        "Restore Alertmanager and verify active episode re-sends arrive."),
    "trend-il-volume": (
        "InfoLogger volume share changed persistently",
        "{entity}'s share of fleet InfoLogger volume stayed at least 2x its "
        "baseline or at most half its baseline for three complete 10-minute "
        "buckets.",
        "Compare this EPN with its peers and inspect whether production "
        "started, stopped, or became stuck."),
    "trend-il-ef": (
        "InfoLogger error share rose persistently",
        "{entity}'s InfoLogger error fraction stayed at least 2x baseline for "
        "three complete 10-minute buckets, with at least 50 records and 10 "
        "errors in each bucket.",
        "Inspect the dominant InfoLogger errors for {entity} and the service "
        "that emits them."),
    "trend-il-entry-lag": (
        "InfoLogger entry lag rose persistently",
        "{entity}'s p95 event-to-collector delay stayed at least 250 ms and "
        "2x baseline for three complete 10-minute buckets (archive-age "
        "values above one hour are excluded).",
        "Check the producer and collector path for queueing before logs enter "
        "the ingest system."),
    "trend-il-shipping-lag": (
        "InfoLogger shipping lag rose persistently",
        "Collector {entity}'s p95 collector-to-OpenSearch delay stayed at "
        "least 250 ms and 2x baseline for three complete 10-minute buckets.",
        "Inspect Fluent Bit buffering, retries, network latency, and "
        "OpenSearch indexing pressure on {entity}."),
    "trend-central-volume": (
        "application-logs-central volume share changed persistently",
        "{entity}'s share of application-logs-central volume stayed at least 2x its "
        "baseline or at most half its baseline for three complete 10-minute "
        "buckets.",
        "Compare this EPN with its peers and inspect the processes producing "
        "application-logs-central."),
    "trend-central-errors": (
        "application-logs-central error share rose persistently",
        "{entity}'s application-logs-central error fraction stayed at least 2x "
        "baseline for three complete 10-minute buckets, with at least 50 "
        "records and 10 errors in each bucket.",
        "Inspect the dominant application-logs-central errors for {entity}."),
    "trend-local-volume": (
        "application-logs-local volume share changed persistently",
        "{entity}'s share of application-logs-local volume stayed at least 2x its "
        "baseline or at most half its baseline for three complete 10-minute "
        "buckets.",
        "Compare this EPN with its peers and inspect the processes producing "
        "application-logs-local."),
    "trend-local-entry-lag": (
        "application-logs-local entry lag rose persistently",
        "{entity}'s p95 event-to-collector delay stayed at least 250 ms and "
        "2x baseline for three complete 10-minute buckets (archive-age "
        "values above one hour are excluded).",
        "Check the producer and collector path for queueing before logs enter "
        "the ingest system."),
    "trend-local-shipping-lag": (
        "application-logs-local shipping lag rose persistently",
        "Collector {entity}'s p95 collector-to-OpenSearch delay stayed at "
        "least 250 ms and 2x baseline for three complete 10-minute buckets.",
        "Inspect Fluent Bit buffering, retries, network latency, and "
        "OpenSearch indexing pressure on {entity}."),
}


# An alert that names no entity is never a fleet-wide breach. Both classes
# below describe the monitor itself, so the entity is the monitor name and the
# operator is told the rule is broken rather than shown an anonymous incident.
CLASS_PRESENTATION = {
    "monitor-error": (
        "Monitor {entity} cannot run",
        "OpenSearch Alerting reported an execution error for monitor "
        "{entity}, so the rule produced no per-entity result. Nothing it "
        "watches is being judged while this holds.",
        "Open Alerting, read the error message on {entity}, then check its "
        "input query and the index it reads."),
    "entity-missing": (
        "Monitor {entity} fired without naming an entity",
        "Monitor {entity} is declared per-entity, but its alert carried no "
        "bucket key. Folding those breaches together would produce one "
        "anonymous fleet incident and hide which entity broke.",
        "Check the monitor's composite aggregation and its trigger's "
        "parent_bucket_path, then confirm bucket_keys reaches the alert "
        "document."),
}


DETECTOR_PRESENTATION = {
    "ingest-flow": ("Unusual collector ingest flow",
                    "throughput, output errors, or output retries"),
    "node-health": ("Unusual OpenSearch node health",
                    "heap, CPU, indexing rate, or disk use"),
    "dashboards-health": ("Unusual Dashboards health",
                          "event-loop delay, response latency, or requests"),
    "il-per-epn": ("Unusual InfoLogger EPN behaviour",
                   "volume or error/fatal count"),
    "il-per-epn-slow": ("Long-window InfoLogger EPN anomaly",
                        "30-minute volume or error/fatal count"),
    "il-per-epn-entry-lag": ("Unusual InfoLogger entry lag",
                             "event-to-collector p95 delay"),
    "il-per-epn-entry-lag-slow": ("Long-window InfoLogger entry-lag anomaly",
                                  "30-minute event-to-collector p95 delay"),
    "il-collector-shipping-lag": ("Unusual InfoLogger shipping lag",
                                  "collector-to-OpenSearch p95 delay"),
    "il-collector-shipping-lag-slow": (
        "Long-window InfoLogger shipping-lag anomaly",
        "30-minute collector-to-OpenSearch p95 delay"),
    "central-per-epn": ("Unusual application-logs-central EPN behaviour",
                      "volume or error count"),
    "central-per-epn-slow": ("Long-window application-logs-central anomaly",
                           "30-minute volume or error count"),
    "local-volume": ("Unusual application-logs-local volume", "volume"),
    "local-volume-slow": ("Long-window application-logs-local volume anomaly",
                         "30-minute volume"),
    "local-per-epn-entry-lag": ("Unusual application-logs-local entry lag",
                               "event-to-collector p95 delay"),
    "local-per-epn-entry-lag-slow": (
        "Long-window application-logs-local entry-lag anomaly",
        "30-minute event-to-collector p95 delay"),
    "local-collector-shipping-lag": ("Unusual application-logs-local shipping lag",
                                    "collector-to-OpenSearch p95 delay"),
    "local-collector-shipping-lag-slow": (
        "Long-window application-logs-local shipping-lag anomaly",
        "30-minute collector-to-OpenSearch p95 delay"),
}


class UnknownSignal(Exception):
    pass


def load(path=None):
    path = path or DEFAULT_PATH
    cached = _cache.get(path)
    if cached is None:
        with open(path) as fh:
            cached = json.load(fh)
        _cache[path] = cached
    return cached


def sentinel(name, path=None):
    return (load(path).get("sentinels") or {}).get(name, "none")


def detector(name, path=None):
    meta = (load(path).get("detectors") or {}).get(name)
    if meta is None:
        raise UnknownSignal(
            f"detector {name!r} is not in the signal catalog; entity kind must "
            f"be declared explicitly, never inferred from an index name")
    return meta


def monitor(name, path=None):
    meta = (load(path).get("monitors") or {}).get(name)
    if meta is None:
        raise UnknownSignal(
            f"monitor {name!r} is not in the signal catalog")
    return meta


def detector_names(path=None):
    return set((load(path).get("detectors") or {}).keys())


def monitor_names(path=None):
    return set((load(path).get("monitors") or {}).keys())


def presentation(name, path=None):
    if name in MONITOR_PRESENTATION:
        title, diagnosis, action = MONITOR_PRESENTATION[name]
        return {"title": title, "diagnosis": diagnosis, "action": action}
    if name in DETECTOR_PRESENTATION:
        # Calling detector() is deliberate: a presentation entry must never
        # make an undeclared detector appear valid.
        detector(name, path)
        title, features = DETECTOR_PRESENTATION[name]
        return {
            "title": title,
            "diagnosis": (
                "The learned model found {features} for {entity} outside its "
                "recent baseline (grade above {grade_floor}).").format(
                    features=features, entity="{entity}",
                    grade_floor="{grade_floor}"),
            "action": (
                "Open the anomaly detector for this episode and inspect its "
                "feature values and direction before escalating."),
        }
    raise UnknownSignal(
        f"signal {name!r} has no operator presentation; every incident must "
        f"say what is wrong and what to inspect next")


def class_presentation(name):
    entry = CLASS_PRESENTATION.get(name)
    if entry is None:
        raise UnknownSignal(
            f"incident class {name!r} has no operator presentation")
    title, diagnosis, action = entry
    return {"title": title, "diagnosis": diagnosis, "action": action}


def entity_pairs(source):
    ents = source.get("entity") or []
    if isinstance(ents, dict):
        ents = [ents]
    out = []
    for e in ents:
        if not isinstance(e, dict):
            continue
        n, v = e.get("name"), e.get("value")
        if n is not None and v is not None:
            out.append((str(n), str(v)))
    return out


def entity_of(name, source, path=None):
    meta = detector(name, path)
    field = meta.get("category_field")
    pairs = entity_pairs(source)
    if not field:
        return (meta["entity_kind"],
                meta.get("entity_id") or sentinel("entity_id", path),
                "")
    for n, v in pairs:
        if n == field:
            return meta["entity_kind"], v, n
    if pairs:
        raise UnknownSignal(
            f"detector {name!r} declares category_field {field!r} but the "
            f"result carries {[n for n, _ in pairs]}")
    raise UnknownSignal(
        f"detector {name!r} declares category_field {field!r} but the result "
        f"carries no entity at all; a sentinel entity here folds every "
        f"entity's anomalies into one anonymous fleet incident")


def scope_string(source):
    pairs = entity_pairs(source)
    if not pairs:
        return "", "whole fleet"
    return ",".join(n for n, _ in pairs), ",".join(v for _, v in pairs)
