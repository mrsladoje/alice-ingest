#!/usr/bin/env python3
"""Adaptive, labelled fault data for the live one-minute detection lane.

This is deliberately a detector calibration tool, not another log producer.
The real S3 replay supplies the baseline and trains the production detector
models.  Once those models are RUNNING, this process discovers entities that
already exist in that baseline and writes a short, extreme, fully-labelled
window into the same write aliases.  It then follows the evidence all the way
from native AD results to projected incident episodes and Alerting monitors.

The 30-minute detectors are intentionally out of scope.  Physical failures
(dead Fluent Bit, a stopped metrics poller/projector, and replay silence) stay
in playbooks/inject.yml; manufacturing those failures by inserting documents would test
the query but not the absence/dead-man contract.
"""

import collections
import datetime
import json
import math
import os
import secrets
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200").rstrip("/")
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
ROSTER_INDEX = os.environ.get("ROSTER_INDEX", "cockpit-fleet")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
STATUS_PATH = os.environ.get(
    "POISON_STATUS_PATH", "/var/lib/alice-poison-replay/status.json")
REPORT_DIR = os.environ.get(
    "POISON_REPORT_DIR", "/var/lib/alice-poison-replay/runs")
WORKERS = [
    value for value in os.environ.get(
        "POISON_WORKER_TRIGGERS", "").split(",") if value
]
REPLAY_FAMILIES = os.environ.get(
    "POISON_REPLAY_FAMILIES", "infologger,dds,stdout")

GRADE_FLOOR = float(os.environ.get("POISON_GRADE_FLOOR", "0.5"))
CONFIDENCE_FLOOR = float(os.environ.get("POISON_CONFIDENCE_FLOOR", "0.0"))
WARMUP_TIMEOUT = int(os.environ.get("POISON_WARMUP_TIMEOUT_SECONDS", "4200"))
WARMUP_POLL = int(os.environ.get("POISON_WARMUP_POLL_SECONDS", "120"))
OBSERVE_SECONDS = int(os.environ.get("POISON_OBSERVE_SECONDS", "330"))
OBSERVE_POLL = int(os.environ.get("POISON_OBSERVE_POLL_SECONDS", "30"))
MAX_BURSTS = int(os.environ.get("POISON_MAX_BURSTS", "3"))
VOLUME_MULTIPLIER = float(os.environ.get(
    "POISON_VOLUME_MULTIPLIER", "12"))
MIN_LOG_DOCS = int(os.environ.get("POISON_MIN_LOG_DOCS", "300"))
MAX_LOG_DOCS = int(os.environ.get("POISON_MAX_LOG_DOCS", "3000"))
METRIC_DOCS = int(os.environ.get("POISON_METRIC_DOCS", "30"))

FAST_DETECTORS = {
    "dashboards-health",
    "il-collector-shipping-lag",
    "il-per-epn",
    "il-per-epn-entry-lag",
    "local-collector-shipping-lag",
    "local-per-epn-entry-lag",
    "local-volume",
    "ingest-flow",
    "node-health",
    "central-per-epn",
}

# These are deterministic consequences of the injected documents.  Dead-man
# and min-over-time monitors are intentionally left to make inject: a healthy
# sample beside a fake bad sample must keep those monitors quiet.
MONITOR_TARGETS = {
    "ad-high-grade",
    "cluster-red",
    "collector-down",
    "data-loss",
    "disk-cliff-page",
    "fleet-fb-silence",
    "shipping-breaking",
}

DETECTOR_ENTITY = {
    "dashboards-health": None,
    "il-collector-shipping-lag": ("il", "node"),
    "il-per-epn": ("il", "origin_host"),
    "il-per-epn-entry-lag": ("il", "origin_host"),
    "local-collector-shipping-lag": ("local", "node"),
    "local-per-epn-entry-lag": ("local", "origin_host"),
    "local-volume": ("local", "origin_host"),
    "ingest-flow": ("fluentbit", "collector_id"),
    "node-health": ("node", "os_node"),
    "central-per-epn": ("central", "origin_host"),
}

_STOP = False
_STATUS = {}


class PoisonError(RuntimeError):
    pass


def now_ms():
    return int(time.time() * 1000)


def iso(moment=None):
    if moment is None:
        moment = now_ms()
    return datetime.datetime.fromtimestamp(
        moment / 1000, tz=datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path, value):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def publish(state=None, message=None, **fields):
    if state:
        _STATUS["state"] = state
    if message:
        print(f"[poison-replay] {message}", flush=True)
        messages = _STATUS.setdefault("messages", [])
        messages.append({"at": iso(), "message": message})
        del messages[:-30]
    _STATUS.update(fields)
    _STATUS["updated_at"] = iso()
    _STATUS["updated_at_ms"] = now_ms()
    _atomic_json(STATUS_PATH, _STATUS)


def stop_requested(signum=None, _frame=None):
    global _STOP
    _STOP = True
    if signum is not None:
        publish(state="stopping", message=f"received signal {signum}")


def wait_for(seconds, state=None, message=None, tick=None):
    if state or message:
        publish(state=state, message=message)
    deadline = time.time() + max(0, seconds)
    while time.time() < deadline:
        if _STOP:
            raise PoisonError("run cancelled")
        if tick:
            tick()
        time.sleep(min(10, max(0.1, deadline - time.time())))


def _request_url(method, url, payload=None, raw=None, timeout=60):
    if payload is not None and raw is not None:
        raise ValueError("payload and raw are mutually exclusive")
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = "application/x-ndjson"
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if not body:
                return response.status, {}
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, {"text": body.decode(
                    "utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"text": body.decode("utf-8", "replace")}
        return exc.code, parsed
    except Exception as exc:
        return 0, {"error": str(exc)}


def request(method, path, payload=None, raw=None, timeout=60, ok=(200,)):
    code, body = _request_url(
        method, OS_URL + path, payload=payload, raw=raw, timeout=timeout)
    if code not in ok:
        raise PoisonError(
            f"{method} {path} returned HTTP {code}: "
            f"{json.dumps(body)[:500]}")
    return body


def _hits(body):
    return ((body.get("hits") or {}).get("hits") or [])


def _total(body):
    value = (body.get("hits") or {}).get("total", 0)
    return int(value.get("value", 0) if isinstance(value, dict) else value)


def is_one_minute(detector):
    period = ((detector.get("detection_interval") or {}).get("period") or {})
    unit = str(period.get("unit", "")).lower()
    return int(period.get("interval", 0)) == 1 and unit.startswith("min")


def detectors():
    body = request(
        "POST", "/_plugins/_anomaly_detection/detectors/_search",
        {"size": 200, "query": {"match_all": {}}})
    found = {}
    for hit in _hits(body):
        source = hit.get("_source") or {}
        if source.get("name") and is_one_minute(source):
            found[source["name"]] = {
                "id": hit.get("_id"), "source": source}
    missing = sorted(FAST_DETECTORS - set(found))
    unsupported = sorted(set(found) - FAST_DETECTORS)
    if missing or unsupported:
        details = []
        if missing:
            details.append(f"missing expected detectors {missing}")
        if unsupported:
            details.append(
                "new one-minute detectors have no poison recipe "
                f"{unsupported}")
        raise PoisonError("; ".join(details))
    return found


def _number(value):
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def detector_readiness(found):
    ready = True
    rows = {}
    for name, detector in sorted(found.items()):
        did = detector["id"]
        path = (
            f"/_plugins/_anomaly_detection/detectors/{did}/_profile/"
            "state,init_progress,total_entities,error")
        code, profile = _request_url("GET", OS_URL + path)
        if code != 200:
            rows[name] = {"ready": False, "reason": f"profile HTTP {code}"}
            ready = False
            continue
        state = str(profile.get("state") or "UNKNOWN").upper()
        progress = profile.get("init_progress") or {}
        needed = _number(progress.get("needed_shingles"))
        pct = _number(progress.get("percentage"))
        entity_count = profile.get("total_entities")
        category = detector["source"].get("category_field") or []
        reasons = []
        if state != "RUNNING":
            reasons.append(f"state={state}")
        if needed is not None and needed > 0:
            reasons.append(f"needed_shingles={needed:g}")
        if pct is not None and pct < 100:
            reasons.append(f"init={pct:g}%")
        if category and entity_count is not None and int(entity_count) < 1:
            reasons.append("no modelled entities")
        error = str(profile.get("error") or "").strip()
        if error:
            reasons.append(f"error={error[:160]}")
        rows[name] = {
            "ready": not reasons,
            "state": state,
            "init_percentage": pct,
            "needed_shingles": needed,
            "total_entities": entity_count,
            "reason": ", ".join(reasons) if reasons else "ready",
        }
        ready = ready and not reasons
    return ready, rows


def replay_running():
    running = []
    for worker in WORKERS:
        code, body = _request_url("GET", worker + "/replay-status", timeout=5)
        if code == 200 and body.get("running"):
            running.append(worker)
    return running


def ensure_replay():
    if not WORKERS:
        return []
    running = replay_running()
    started = []
    query = urllib.parse.urlencode({"family": REPLAY_FAMILIES})
    for worker in WORKERS:
        if worker in running:
            continue
        code, body = _request_url(
            "POST", worker + "/replay?" + query, timeout=20)
        if code in (202, 409):
            started.append(worker)
        else:
            publish(message=(
                f"could not start baseline replay on {worker}: HTTP {code} "
                f"{json.dumps(body)[:200]}"))
    if started:
        publish(message=(
            f"started baseline replay on {len(started)} missing worker(s); "
            f"{len(running) + len(started)}/{len(WORKERS)} are active"))
    return running + started


def recent_sources(index, filters, fields, size=500, time_field=None):
    clauses = list(filters)
    if time_field:
        clauses.append({"range": {time_field: {"gte": "now-2h"}}})
    body = request(
        "POST",
        f"/{index}/_search?ignore_unavailable=true&allow_no_indices=true",
        {"size": size, "_source": fields,
         "sort": [{time_field: "desc"}] if time_field else [],
         "query": {"bool": {
             "filter": clauses,
             "must_not": [{"exists": {"field": "poison_run_id"}}],
         }}})
    return [hit.get("_source") or {} for hit in _hits(body)]


def common_pair(index, time_field, first, second=None):
    required = [{"exists": {"field": first}}]
    fields = [first]
    if second:
        required.append({"exists": {"field": second}})
        fields.append(second)
    rows = recent_sources(
        index, required, fields, time_field=time_field)
    keys = []
    for row in rows:
        values = tuple(str(row.get(field)) for field in fields)
        if all(value not in ("", "None") for value in values):
            keys.append(values)
    if not keys:
        raise PoisonError(
            f"no clean recent entity with {fields} in {index}; keep the "
            "paced replay running")
    values, _ = collections.Counter(keys).most_common(1)[0]
    return dict(zip(fields, values))


def latest_kind(kind, field):
    rows = recent_sources(
        METRICS_INDEX,
        [{"term": {"kind": kind}}, {"exists": {"field": field}}],
        [field], size=20, time_field="@timestamp")
    if not rows:
        raise PoisonError(
            f"no clean recent kind:{kind} sample carrying {field} in "
            f"{METRICS_INDEX}")
    return {field: str(rows[0][field])}


def latest_roster():
    body = request(
        "POST", f"/{ROSTER_INDEX}/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 1, "sort": [{"effective_from": "desc"}],
         "query": {"term": {"doc_kind": "roster"}}})
    rows = _hits(body)
    if not rows:
        raise PoisonError(
            "no published fleet roster; monitor attribution would be guessed")
    source = rows[0].get("_source") or {}
    collectors = [str(value) for value in source.get("collectors") or []]
    if not collectors:
        raise PoisonError("latest fleet roster contains no collectors")
    return {
        "collectors": collectors,
        "topology_version": str(source.get("topology_version") or "none"),
    }


def discover_samples():
    return {
        "il": common_pair(
            "infologger", "collector_time", "origin_host", "node"),
        "local": common_pair(
            "application-logs-local-*", "collector_time", "origin_host", "node"),
        "central": common_pair(
            "application-logs-central", "collector_time", "origin_host", "node"),
        "fluentbit": latest_kind("fluentbit", "collector_id"),
        "node": latest_kind("node", "os_node"),
        "roster": latest_roster(),
    }


def baseline_per_minute(index, entity_field, entity):
    body = request(
        "POST",
        f"/{index}/_search?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0,
         "query": {"bool": {
             "filter": [
                 {"term": {entity_field: entity}},
                 {"range": {"collector_time": {"gte": "now-45m"}}},
             ],
             "must_not": [{"exists": {"field": "poison_run_id"}}],
         }},
         "aggs": {"minutes": {"date_histogram": {
             "field": "collector_time", "fixed_interval": "1m",
             "min_doc_count": 1}}}})
    counts = [int(row.get("doc_count", 0)) for row in
              (((body.get("aggregations") or {}).get("minutes") or {})
               .get("buckets") or [])]
    if not counts:
        return 1
    counts.sort()
    return counts[min(len(counts) - 1, math.ceil(len(counts) * 0.95) - 1)]


def poison_fields(run_id, stage, targets):
    return {
        "synthetic": True,
        "poison_run_id": run_id,
        "poison_stage": stage,
        "poison_targets": sorted(targets),
    }


def log_doc(family, sample, run_id, stage, sequence, anchor_ms):
    # Both lag fields are explicit because the bulk request bypasses the normal
    # ingest pipeline.  ingest_time is a virtual clock here: keeping
    # collector_time in the current detector window while representing a
    # fifteen-minute shipping delay is the controlled fault being injected.
    shipping_lag = 15 * 60 * 1000
    entry_lag = 365 * 24 * 60 * 60 * 1000
    targets = {
        "il": {
            "il-per-epn", "il-per-epn-entry-lag",
            "il-collector-shipping-lag"},
        "local": {
            "local-volume", "local-per-epn-entry-lag",
            "local-collector-shipping-lag"},
        "central": {"central-per-epn"},
    }[family]
    doc = {
        "@timestamp": anchor_ms - entry_lag,
        "collector_time": anchor_ms,
        "ingest_time": anchor_ms + shipping_lag,
        "ingest_lag_ms": shipping_lag,
        "enter_system_lag_ms": entry_lag,
        "node": sample["node"],
        "origin_host": sample["origin_host"],
        "message": (
            f"ALICE controlled poison replay {run_id} stage={stage} "
            f"sequence={sequence}"),
        **poison_fields(run_id, stage, targets),
    }
    if family == "il":
        doc.update({
            "log_source": "infologger", "severity": "E",
            "severity_norm": "error", "level": 4,
            "hostname": sample["origin_host"], "rolename": "poison-replay",
            "pid": 1, "username": "alice", "system": "poison-replay",
            "facility": "calibration", "detector": "ALL",
            "partition": "calibration", "run": 0, "errcode": 9001,
            "errline": sequence, "errsource": "poison_replay.py",
        })
    elif family == "local":
        doc.update({
            "log_source": "stdout", "severity": "Info",
            "severity_norm": "info", "host": sample["origin_host"],
            "source": "poison-replay", "facility": "calibration",
        })
    else:
        doc.update({
            "log_source": "dds", "severity": "err",
            "severity_norm": "error", "host": sample["origin_host"],
            "source": "poison-replay", "facility": "calibration",
        })
    return doc


def build_burst(samples, run_id, burst, intensity):
    stage = f"burst-{burst}"
    anchor = now_ms()
    docs = []
    family_specs = [
        ("il", "infologger", "infologger"),
        ("local", "application-logs-local-*", None),
        ("central", "application-logs-central", "application-logs-central"),
    ]
    volumes = {}
    for family, read_index, fixed_target in family_specs:
        sample = samples[family]
        baseline = baseline_per_minute(
            read_index, "origin_host", sample["origin_host"])
        count = min(
            MAX_LOG_DOCS,
            max(MIN_LOG_DOCS,
                math.ceil(baseline * VOLUME_MULTIPLIER * intensity)))
        target = fixed_target or f"application-logs-local-{sample['node']}"
        volumes[family] = {"baseline_p95": baseline, "injected": count}
        for sequence in range(count):
            doc_id = f"poison:{run_id}:{stage}:{family}:{sequence}"
            docs.append((
                target, doc_id,
                log_doc(family, sample, run_id, stage, sequence, anchor)))

    metric_count = max(1, min(120, int(METRIC_DOCS * intensity)))
    common = poison_fields(run_id, stage, {
        "dashboards-health", "ingest-flow", "node-health",
        "cluster-red", "collector-down", "data-loss", "disk-cliff-page",
        "fleet-fb-silence", "shipping-breaking"})
    collector = samples["fluentbit"]["collector_id"]
    os_node = samples["node"]["os_node"]
    for sequence in range(metric_count):
        ts = anchor - (sequence % 1000)
        fluent = {
            "@timestamp": ts, "kind": "fluentbit",
            "collector_id": collector, "fb_up": 1, "fb_healthy": 1,
            "output_records_delta": 1000000,
            "output_errors_delta": 250000,
            "output_retries_delta": 250000,
            "output_retries_failed_delta": 1000,
            "output_dropped_delta": 1000,
            **common,
        }
        node = {
            "@timestamp": ts, "kind": "node", "os_node": os_node,
            "heap_percent": 99, "cpu_percent": 100,
            "disk_used_percent": 95.0, "indexing_delta": 1000000000,
            **common,
        }
        osd = {
            "@timestamp": ts, "kind": "osd", "osd_state": "degraded",
            "osd_state_code": 2, "event_loop_delay": 120000.0,
            "response_avg_ms": 120000.0, "response_max_ms": 180000.0,
            "requests_total": 1000000000, **common,
        }
        for kind, source in (("fluentbit", fluent), ("node", node),
                             ("osd", osd)):
            docs.append((
                METRICS_INDEX,
                f"poison:{run_id}:{stage}:{kind}:{sequence}", source))

    for sequence in range(2):
        docs.append((
            METRICS_INDEX,
            f"poison:{run_id}:{stage}:cluster:{sequence}",
            {"@timestamp": anchor, "kind": "cluster",
             "cluster_status": "red", "cluster_status_code": 2,
             "unassigned_shards": 1, **common}))

    roster = samples["roster"]
    repeats = max(4, int(8 * intensity))
    for collector_id in roster["collectors"]:
        for sequence in range(repeats):
            docs.append((
                METRICS_INDEX,
                f"poison:{run_id}:{stage}:fleet:{collector_id}:{sequence}",
                {"@timestamp": anchor, "kind": "fleet",
                 "collector_id": collector_id, "heartbeat_missing": 1,
                 "heartbeat_age_ms": 600000,
                 "roster_size": len(roster["collectors"]),
                 "roster_missing": len(roster["collectors"]),
                 "topology_version": roster["topology_version"], **common}))
    return docs, volumes


def bulk_index(docs):
    inserted = 0
    for offset in range(0, len(docs), 500):
        lines = []
        chunk = docs[offset:offset + 500]
        for index, doc_id, source in chunk:
            lines.append(json.dumps({"index": {
                "_index": index, "_id": doc_id}}))
            lines.append(json.dumps(source, separators=(",", ":")))
        body = ("\n".join(lines) + "\n").encode()
        response = request(
            "POST", "/_bulk?pipeline=_none&refresh=wait_for", raw=body,
            timeout=120)
        if response.get("errors"):
            failed = []
            for item in response.get("items") or []:
                result = item.get("index") or {}
                if int(result.get("status", 500)) >= 300:
                    failed.append(result)
            raise PoisonError(
                f"bulk rejected {len(failed)}/{len(chunk)} poison documents; "
                f"first={json.dumps(failed[:1])[:500]}")
        inserted += len(chunk)
    return inserted


def expected_entity(name, samples):
    route = DETECTOR_ENTITY[name]
    if route is None:
        return None
    source, field = route
    return str(samples[source][field])


def selected_entity_readiness(found, samples):
    ready = True
    rows = {}
    for name, detector in sorted(found.items()):
        categories = detector["source"].get("category_field") or []
        if not categories:
            continue
        entity = expected_entity(name, samples)
        if entity is None or len(categories) != 1:
            rows[name] = {
                "ready": False,
                "reason": "poison recipe cannot identify every category field",
            }
            ready = False
            continue
        payload = {"entity": [{"name": categories[0], "value": entity}]}
        path = (
            f"/_plugins/_anomaly_detection/detectors/{detector['id']}"
            "/_profile?_all=true")
        code, profile = _request_url(
            "GET", OS_URL + path, payload=payload, timeout=60)
        if code != 200:
            rows[name] = {
                "ready": False, "entity": entity,
                "reason": f"entity profile HTTP {code}",
            }
            ready = False
            continue
        state = str(profile.get("state") or "UNKNOWN").upper()
        progress = profile.get("init_progress") or {}
        pct = _number(progress.get("percentage"))
        needed = _number(progress.get("needed_shingles"))
        reasons = []
        if state != "RUNNING":
            reasons.append(f"state={state}")
        if pct is not None and pct < 100:
            reasons.append(f"init={pct:g}%")
        if needed is not None and needed > 0:
            reasons.append(f"needed_shingles={needed:g}")
        if profile.get("is_active") is False:
            reasons.append("entity model is not active")
        if not profile.get("model") and profile.get("is_active") is not True:
            reasons.append("no entity model returned")
        error = str(profile.get("error") or "").strip()
        if error:
            reasons.append(f"error={error[:160]}")
        rows[name] = {
            "ready": not reasons,
            "entity": entity,
            "state": state,
            "init_percentage": pct,
            "needed_shingles": needed,
            "last_sample_timestamp": profile.get("last_sample_timestamp"),
            "last_active_timestamp": profile.get("last_active_timestamp"),
            "reason": ", ".join(reasons) if reasons else "ready",
        }
        ready = ready and not reasons
    return ready, rows


def preexisting_open_episodes(samples):
    names = sorted(FAST_DETECTORS | MONITOR_TARGETS)
    body = request(
        "POST", f"/{INCIDENTS_INDEX}/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 1000, "query": {"bool": {"filter": [
            {"terms": {"alertname": names}},
            {"term": {"state": "firing"}},
        ]}}})
    detectors_open = set()
    monitors_open = set()
    for hit in _hits(body):
        source = hit.get("_source") or {}
        name = source.get("alertname")
        if source.get("source_kind") == "monitor" and name in MONITOR_TARGETS:
            monitors_open.add(name)
        elif source.get("source_kind") == "detector" and name in FAST_DETECTORS:
            entity = expected_entity(name, samples)
            if entity is None or str(source.get("entity_id")) == entity:
                detectors_open.add(name)
    return {
        "detectors": sorted(detectors_open),
        "monitors": sorted(monitors_open),
    }


def result_has_entity(source, entity):
    if entity is None:
        return True
    return any(str(row.get("value")) == entity
               for row in source.get("entity") or [])


def detector_results(found, samples, evidence_start):
    matrix = {}
    for name, detector in sorted(found.items()):
        body = request(
            "POST", "/.opendistro-anomaly-results*/_search"
            "?ignore_unavailable=true&allow_no_indices=true",
            {"size": 500,
             "sort": [{"execution_end_time": "asc"}],
             "query": {"bool": {
                 "filter": [
                     {"term": {"detector_id": detector["id"]}},
                     {"range": {"execution_end_time": {
                         "gte": evidence_start, "format": "epoch_millis"}}},
                     {"range": {"data_end_time": {
                         "gte": evidence_start, "format": "epoch_millis"}}},
                 ],
                 "must_not": [{"exists": {"field": "task_id"}}],
             }}})
        entity = expected_entity(name, samples)
        rows = [hit.get("_source") or {} for hit in _hits(body)]
        rows = [row for row in rows if result_has_entity(row, entity)]
        high = [row for row in rows
                if float(row.get("anomaly_grade") or 0) > GRADE_FLOOR
                and float(row.get("confidence") or 0) >= CONFIDENCE_FLOOR]
        matrix[name] = {
            "detector_id": detector["id"],
            "entity": entity or "whole-stream",
            "evaluations": len(rows),
            "raw_hits": len(high),
            "max_grade": round(max(
                [float(row.get("anomaly_grade") or 0) for row in rows]
                or [0]), 4),
            "max_confidence": round(max(
                [float(row.get("confidence") or 0) for row in rows]
                or [0]), 4),
            "result_windows": sorted({
                int(row.get("data_end_time") or 0) for row in high}),
        }
    return matrix


def incident_rows(names, source_kind, evidence_start):
    body = request(
        "POST", f"/{INCIDENTS_INDEX}/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 1000,
         "query": {"bool": {"filter": [
             {"term": {"source_kind": source_kind}},
             {"terms": {"alertname": sorted(names)}},
             {"range": {"episode_start": {
                 "gte": evidence_start, "format": "epoch_millis"}}},
         ]}}})
    return [hit.get("_source") or {} for hit in _hits(body)]


def attach_detector_episodes(matrix, samples, evidence_start):
    rows = incident_rows(FAST_DETECTORS, "detector", evidence_start)
    for name, result in matrix.items():
        entity = expected_entity(name, samples)
        matched = [row for row in rows if row.get("alertname") == name
                   and (entity is None or str(row.get("entity_id")) == entity)]
        result["episodes"] = len({row.get("episode_id") for row in matched})
        result["episode_states"] = sorted({
            str(row.get("episode_state") or row.get("state") or "unknown")
            for row in matched})


def monitor_matrix(evidence_start):
    rows = incident_rows(MONITOR_TARGETS, "monitor", evidence_start)
    matrix = {}
    for name in sorted(MONITOR_TARGETS):
        matched = [row for row in rows if row.get("alertname") == name]
        matrix[name] = {
            "episodes": len({row.get("episode_id") for row in matched}),
            "episode_states": sorted({
                str(row.get("episode_state") or row.get("state") or "unknown")
                for row in matched}),
            "entities": sorted({str(row.get("entity_id") or "none")
                                for row in matched}),
        }
    return matrix


def score(found, samples, evidence_start):
    det = detector_results(found, samples, evidence_start)
    attach_detector_episodes(det, samples, evidence_start)
    monitors = monitor_matrix(evidence_start)
    missing_detectors = sorted(
        name for name, row in det.items()
        if row["raw_hits"] < 1 or row["episodes"] < 1)
    missing_monitors = sorted(
        name for name, row in monitors.items() if row["episodes"] < 1)
    return {
        "detectors": det,
        "monitors": monitors,
        "missing_detectors": missing_detectors,
        "missing_monitors": missing_monitors,
        "passed": not missing_detectors and not missing_monitors,
    }


def warmup():
    deadline = time.time() + WARMUP_TIMEOUT
    last_reason = None
    while time.time() < deadline:
        if _STOP:
            raise PoisonError("run cancelled")
        active_replay = ensure_replay()
        found = detectors()
        ready, readiness = detector_readiness(found)
        samples = None
        sample_error = None
        entity_readiness = {}
        preexisting = {"detectors": [], "monitors": []}
        replay_error = None
        if WORKERS and len(set(active_replay)) != len(WORKERS):
            replay_error = (
                f"baseline replay active on {len(set(active_replay))}/"
                f"{len(WORKERS)} workers")
            ready = False
        try:
            samples = discover_samples()
            entities_ready, entity_readiness = selected_entity_readiness(
                found, samples)
            ready = ready and entities_ready
            preexisting = preexisting_open_episodes(samples)
            if preexisting["detectors"] or preexisting["monitors"]:
                ready = False
        except PoisonError as exc:
            sample_error = str(exc)
            ready = False
        reason = "; ".join(
            [f"{name}: {row['reason']}" for name, row in readiness.items()
             if not row["ready"]]
            + [f"{name}/{row.get('entity', '?')}: {row['reason']}"
               for name, row in entity_readiness.items()
               if not row["ready"]]
            + (["pre-existing firing detector episodes: "
                + str(preexisting["detectors"])]
               if preexisting["detectors"] else [])
            + (["pre-existing firing monitor episodes: "
                + str(preexisting["monitors"])]
               if preexisting["monitors"] else [])
            + ([replay_error] if replay_error else [])
            + ([sample_error] if sample_error else []))
        publish(
            state="warming", detector_readiness=readiness,
            selected_entity_readiness=entity_readiness,
            preexisting_open_episodes=preexisting,
            replay_workers=len(set(active_replay)) if WORKERS else None,
            warmup_remaining_seconds=max(0, int(deadline - time.time())))
        if ready and samples is not None:
            publish(message=(
                "all 10 one-minute detectors are RUNNING and clean baseline "
                "entities are available"), selected_entities=samples)
            return found, samples
        if reason != last_reason:
            publish(message="waiting for a trained baseline: " + reason)
            last_reason = reason
        wait_for(min(WARMUP_POLL, max(1, deadline - time.time())))
    raise PoisonError(
        "warm-up timed out before all one-minute detectors were ready: "
        + (last_reason or "unknown readiness failure"))


def observe(found, samples, evidence_start, burst, deadline):
    latest = None
    while time.time() < deadline:
        if _STOP:
            raise PoisonError("run cancelled")
        latest = score(found, samples, evidence_start)
        publish(
            state="observing", current_burst=burst,
            detector_matrix=latest["detectors"],
            monitor_matrix=latest["monitors"],
            missing_detectors=latest["missing_detectors"],
            missing_monitors=latest["missing_monitors"],
            observe_remaining_seconds=max(0, int(deadline - time.time())))
        if latest["passed"]:
            return latest
        wait_for(min(OBSERVE_POLL, max(1, deadline - time.time())))
    return latest or score(found, samples, evidence_start)


def run():
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + \
        "-" + secrets.token_hex(3)
    _STATUS.clear()
    _STATUS.update({
        "schema_version": 1,
        "run_id": run_id,
        "state": "starting",
        "started_at": iso(),
        "started_at_ms": now_ms(),
        "scope": {
            "detectors": sorted(FAST_DETECTORS),
            "excluded": "all detection intervals longer than one minute",
            "monitors": sorted(MONITOR_TARGETS),
        },
        "configuration": {
            "max_bursts": MAX_BURSTS,
            "observe_seconds_per_burst": OBSERVE_SECONDS,
            "grade_floor": GRADE_FLOOR,
            "volume_multiplier": VOLUME_MULTIPLIER,
            "max_log_docs_per_family": MAX_LOG_DOCS,
        },
        "messages": [],
    })
    publish(message=(
        f"run {run_id} started; 30-minute detectors are excluded and no "
        "production service is stopped"))
    try:
        found, samples = warmup()
        evidence_start = now_ms() - 60000
        publish(evidence_start_ms=evidence_start)
        outcome = None
        total_inserted = 0
        for burst in range(1, MAX_BURSTS + 1):
            active_replay = ensure_replay()
            if WORKERS and len(set(active_replay)) != len(WORKERS):
                raise PoisonError(
                    "baseline replay is not active on every worker; refusing "
                    "to poison a partially live topology")
            intensity = float(3 ** (burst - 1))
            docs, volumes = build_burst(
                samples, run_id, burst, intensity)
            publish(
                state="injecting", current_burst=burst,
                burst_intensity=intensity, burst_volumes=volumes,
                message=(
                    f"burst {burst}/{MAX_BURSTS}: indexing {len(docs)} "
                    f"labelled documents at intensity {intensity:g}x"))
            inserted = bulk_index(docs)
            total_inserted += inserted
            publish(total_inserted=total_inserted, last_burst_inserted=inserted)
            outcome = observe(
                found, samples, evidence_start, burst,
                time.time() + OBSERVE_SECONDS)
            if outcome["passed"]:
                break
            publish(message=(
                f"burst {burst} incomplete; missing detectors="
                f"{outcome['missing_detectors']} monitors="
                f"{outcome['missing_monitors']}"))
        final_state = "passed" if outcome and outcome["passed"] else "failed"
        verdict = {
            "passed": bool(outcome and outcome["passed"]),
            "detectors_expected": len(FAST_DETECTORS),
            "detectors_with_raw_and_projected_evidence": sum(
                1 for row in (outcome or {}).get("detectors", {}).values()
                if row.get("raw_hits", 0) > 0 and row.get("episodes", 0) > 0),
            "monitors_expected": len(MONITOR_TARGETS),
            "monitors_with_projected_episodes": sum(
                1 for row in (outcome or {}).get("monitors", {}).values()
                if row.get("episodes", 0) > 0),
            "missing_detectors": (outcome or {}).get(
                "missing_detectors", sorted(FAST_DETECTORS)),
            "missing_monitors": (outcome or {}).get(
                "missing_monitors", sorted(MONITOR_TARGETS)),
        }
        publish(
            state=final_state, finished_at=iso(), verdict=verdict,
            detector_matrix=(outcome or {}).get("detectors", {}),
            monitor_matrix=(outcome or {}).get("monitors", {}),
            message=("PASS: every fast detector produced a native result and "
                     "a projected episode; every probe monitor fired"
                     if verdict["passed"] else
                     "NO-GO: the run ended without complete detector/monitor "
                     "evidence; inspect the missing matrix"))
        os.makedirs(REPORT_DIR, exist_ok=True)
        _atomic_json(os.path.join(REPORT_DIR, run_id + ".json"), _STATUS)
        return 0 if verdict["passed"] else 2
    except PoisonError as exc:
        state = "cancelled" if _STOP else "failed"
        publish(
            state=state, finished_at=iso(), error=str(exc),
            message=f"{state.upper()}: {exc}")
        os.makedirs(REPORT_DIR, exist_ok=True)
        _atomic_json(os.path.join(REPORT_DIR, run_id + ".json"), _STATUS)
        return 130 if _STOP else 2
    except Exception as exc:
        publish(
            state="failed", finished_at=iso(),
            error=f"unexpected {type(exc).__name__}: {exc}",
            message=f"FAILED unexpectedly: {type(exc).__name__}: {exc}")
        os.makedirs(REPORT_DIR, exist_ok=True)
        _atomic_json(os.path.join(REPORT_DIR, run_id + ".json"), _STATUS)
        return 2


def main():
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    return run()


if __name__ == "__main__":
    sys.exit(main())
