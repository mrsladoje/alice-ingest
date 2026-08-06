import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os_cursor  # noqa: E402
import signal_identity  # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
ALERTMANAGER_URL = os.environ.get(
    "ALERTMANAGER_URL", "http://127.0.0.1:9093")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
NOTIFICATIONS_INDEX = os.environ.get(
    "NOTIFICATIONS_INDEX", "alice-notifications")
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
ROSTER_INDEX = os.environ.get("ROSTER_INDEX", "cockpit-fleet")
STATE_INDEX = os.environ.get("STATE_INDEX", "alice-lane-state")
AD_RESULTS = ".opendistro-anomaly-results*"
ALERTS_CURRENT = ".opendistro-alerting-alerts"
ALERTS_HISTORY = ".opendistro-alerting-alert-history-*"

ALERT_NAMESPACE = "opendistro-alerting"

CLUSTER_ID = os.environ.get("CLUSTER_ID", "alice-logs")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
PAGE = max(1, int(os.environ.get("PAGE", "500")))
PIT_KEEP_ALIVE = os.environ.get("PIT_KEEP_ALIVE", "10m")
BULK_DOCUMENTS = max(1, int(os.environ.get("BULK_DOCUMENTS", "500")))
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0.5"))
OVERLAP_MINUTES = int(os.environ.get("OVERLAP_MINUTES", "15"))
EVIDENCE_OVERLAP_MINUTES = int(
    os.environ.get("EVIDENCE_OVERLAP_MINUTES", "3"))
INITIAL_LOOKBACK_MINUTES = int(
    os.environ.get("INITIAL_LOOKBACK_MINUTES", "120"))
RESOLVE_TIMEOUT_SECONDS = int(
    os.environ.get("RESOLVE_TIMEOUT_SECONDS", "300"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
PRUNE_EVERY_SECONDS = int(os.environ.get("PRUNE_EVERY_SECONDS", "3600"))
MASS_SILENCE_FRACTION = float(
    os.environ.get("MASS_SILENCE_FRACTION", "0.5"))
MAX_OPEN_INCIDENTS = int(os.environ.get("MAX_OPEN_INCIDENTS", "5000"))
MAX_MEMBER_IDS = int(os.environ.get("MAX_MEMBER_IDS", "2000"))
MAX_ANNOTATION_IDS = int(os.environ.get("MAX_ANNOTATION_IDS", "50"))
EPISODE_LOOKBACK_HOURS = int(os.environ.get("EPISODE_LOOKBACK_HOURS", "24"))
MONITOR_HEALTHY_REQUIRED = int(
    os.environ.get("MONITOR_HEALTHY_REQUIRED", "1"))

ACTIVE_ALERT_STATES = {"ACTIVE", "ERROR"}
CLOSED_ALERT_STATES = {"COMPLETED", "DELETED"}

OPEN = "OPEN"
RECOVERING = "RECOVERING"
RESOLVED = "RESOLVED"
STALE = "STALE"
CLOSED_EXPECTED = "CLOSED_EXPECTED"
TERMINAL = {RESOLVED, CLOSED_EXPECTED}

_roster_cache = []
_roster_loaded_at = 0.0


def log(msg):
    print(f"[signal-projector] {msg}", flush=True)


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(int(ms) / 1000.0))


def sentinel(name):
    return signal_identity.sentinel(name)


def load_rosters():
    global _roster_cache, _roster_loaded_at
    if _roster_cache and time.time() - _roster_loaded_at < 60:
        return _roster_cache
    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{ROSTER_INDEX}/_search?ignore_unavailable=true"
        f"&allow_no_indices=true",
        {"size": 200,
         "sort": [{"effective_from": "desc"}],
         "query": {"bool": {"filter": [{"term": {"doc_kind": "roster"}}]}}})
    if code != 200:
        return _roster_cache
    snapshots = []
    for hit in ((body.get("hits") or {}).get("hits") or []):
        src = hit.get("_source") or {}
        snapshots.append({
            "topology_version": src.get("topology_version")
            or sentinel("topology_version"),
            "effective_from": int(src.get("effective_from") or 0),
            "collectors": src.get("collectors") or [],
            "parents": {a.get("origin_host"): a.get("collector_id")
                        for a in (src.get("assignments") or [])
                        if a.get("origin_host")},
        })
    snapshots.sort(key=lambda s: s["effective_from"], reverse=True)
    _roster_cache = snapshots
    _roster_loaded_at = time.time()
    return _roster_cache


def roster_at(event_ms):
    for snap in load_rosters():
        if snap["effective_from"] <= event_ms:
            return snap
    return None


def latest_roster():
    snapshots = load_rosters()
    return snapshots[0] if snapshots else None


def topology_for(entity_kind, entity_id, event_ms):
    snap = roster_at(event_ms)
    if snap is None:
        return sentinel("collector_id"), sentinel("topology_version")
    version = snap["topology_version"]
    if entity_kind == "collector":
        if entity_id in snap["collectors"]:
            return entity_id, version
        return sentinel("collector_id"), version
    if entity_kind == "epn":
        parent = snap["parents"].get(entity_id)
        if parent:
            return parent, version
        return sentinel("collector_id"), version
    return sentinel("collector_id"), version


def severity_of(raw):
    return "page" if str(raw).strip() == "1" else "warn"


def watermark(lane, default_minutes):
    value = os_cursor.read_watermark(OS_URL, STATE_INDEX, lane)
    if value is None:
        return now_ms() - default_minutes * 60000
    return value


def alert_signal(hit, source_index):
    src = hit.get("_source") or {}
    source_alert_id = src.get("id")
    hit_id = hit.get("_id")
    # OpenSearch indexes a new alert before the generated document ID is
    # copied back into the Alert object.  During that interval _source.id is
    # empty even though hit._id is already the durable identity that survives
    # the move to alert history.
    alert_id = source_alert_id or hit_id
    if not alert_id:
        raise os_cursor.CursorError(
            f"alert in {source_index} has neither _source.id nor _id; the "
            f"signal identity depends on one durable alert ID")
    if source_alert_id and hit_id and source_alert_id != hit_id:
        raise os_cursor.CursorError(
            f"alert id contract broken in {source_index}: _source.id="
            f"{source_alert_id} but _id={hit_id}")
    alertname = src.get("monitor_name") or src.get("trigger_name")
    try:
        meta = signal_identity.monitor(alertname)
    except signal_identity.UnknownSignal:
        return None
    state = str(src.get("state") or "").upper()
    if meta["entity_source"] == "constant":
        entity_id = meta.get("entity_id") or sentinel("entity_id")
    else:
        keys = src.get("bucket_keys")
        if isinstance(keys, list):
            entity_id = ",".join(str(k) for k in keys)
        else:
            entity_id = str(keys) if keys else sentinel("entity_id")
    start = int(src.get("start_time") or src.get("last_notification_time")
                or now_ms())
    collector_id, version = topology_for(
        meta["entity_kind"], entity_id, start)
    doc = {
        "@timestamp": start,
        "source_id": alert_id,
        "source_kind": "monitor",
        "source_index": source_index,
        "source_uid": os_cursor.source_uid(ALERT_NAMESPACE, alert_id),
        "alertname": alertname,
        "state": "firing" if state in ACTIVE_ALERT_STATES else "resolved",
        "severity": severity_of(src.get("severity")),
        "cluster_id": CLUSTER_ID,
        "entity_kind": meta["entity_kind"],
        "entity_id": entity_id,
        "collector_id": collector_id,
        "family": meta.get("family") or sentinel("family"),
        "notification_scope": meta["notification_scope"],
        "feed": meta["feed"],
        "run": sentinel("run_id"),
        "topology_version": version,
        "monitor_id": src.get("monitor_id"),
        "first_seen": start,
        "last_seen": int(src.get("last_notification_time") or start),
        "start_time": start,
        "evidence": {
            "bucket_keys": entity_id,
            "period_end": str(src.get("last_notification_time") or ""),
        },
    }
    if src.get("end_time"):
        doc["end_time"] = int(src["end_time"])
    if state in CLOSED_ALERT_STATES:
        doc["state"] = "resolved"
        doc.setdefault("end_time", doc["last_seen"])
    return doc


def merge_alert_rows(rows):
    merged = {}
    for doc in rows:
        key = doc["source_uid"]
        prior = merged.get(key)
        if prior is None:
            merged[key] = doc
            continue
        winner = doc if doc.get("end_time") else prior
        loser = prior if winner is doc else doc
        winner["first_seen"] = min(winner["first_seen"], loser["first_seen"])
        winner["last_seen"] = max(winner["last_seen"], loser["last_seen"])
        merged[key] = winner
    return list(merged.values())


def anomaly_row(hit, detector_names):
    src = hit.get("_source") or {}
    grade = src.get("anomaly_grade")
    if grade is None:
        return None
    name = detector_names.get(src.get("detector_id"))
    if not name:
        return None
    try:
        entity_kind, entity_id, _ = signal_identity.entity_of(name, src)
        meta = signal_identity.detector(name)
    except signal_identity.UnknownSignal:
        return None
    event_ms = int(src.get("data_end_time") or now_ms())
    collector_id, version = topology_for(entity_kind, entity_id, event_ms)
    index = hit.get("_index")
    uid = os_cursor.source_uid(index, hit.get("_id"))
    grade = float(grade)
    return {
        "@timestamp": event_ms,
        "source_id": hit.get("_id"),
        "source_kind": "detector",
        "source_index": index,
        "source_uid": uid,
        "alertname": name,
        "state": "firing" if grade > GRADE_FLOOR else "evidence",
        "severity": "warn",
        "cluster_id": CLUSTER_ID,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "collector_id": collector_id,
        "family": meta.get("family") or sentinel("family"),
        "notification_scope": meta["notification_scope"],
        "feed": meta["feed"],
        "run": sentinel("run_id"),
        "topology_version": version,
        "detector_id": src.get("detector_id"),
        "grade": round(grade, 4),
        "confidence": round(float(src.get("confidence") or 0), 4),
        "first_seen": event_ms,
        "last_seen": event_ms,
        "start_time": int(src.get("data_start_time") or event_ms),
    }


def incident_id(doc):
    scope = (doc["collector_id"] if doc["notification_scope"] == "collector"
             else "fleet")
    return os_cursor.content_hash({
        "cluster_id": doc["cluster_id"],
        "source_kind": doc["source_kind"],
        "alertname": doc["alertname"],
        "entity_kind": doc["entity_kind"],
        "entity_id": doc["entity_id"],
        "scope": scope,
    })


def snapshot_current_alerts():
    rows = []
    for hits in os_cursor.scan(
            OS_URL, ALERTS_CURRENT, {"bool": {"filter": [{"match_all": {}}]}},
            "start_time", page=PAGE, keep_alive=PIT_KEEP_ALIVE):
        for hit in hits:
            doc = alert_signal(hit, ALERTS_CURRENT)
            if doc:
                rows.append(doc)
    return rows


def ingest_alert_history():
    lane = "projector-alert-history"
    lower = watermark(lane, INITIAL_LOOKBACK_MINUTES) \
        - OVERLAP_MINUTES * 60000
    highest = lower
    query = {"bool": {"filter": [
        {"range": {"end_time": {"gte": lower, "format": "epoch_millis"}}}]}}
    rows = []
    for hits in os_cursor.scan(OS_URL, ALERTS_HISTORY, query, "end_time",
                               page=PAGE, keep_alive=PIT_KEEP_ALIVE):
        for hit in hits:
            value = (hit.get("_source") or {}).get("end_time")
            if isinstance(value, (int, float)) and int(value) > highest:
                highest = int(value)
            doc = alert_signal(hit, hit.get("_index") or ALERTS_HISTORY)
            if doc:
                rows.append(doc)
    return rows, highest, lane


def ingest_anomaly_results(detector_names, consume):
    """Consume one bounded page of normalized AD rows at a time.

    The query watermark remains execution_end_time, which catches late results.
    A PIT snapshot can safely be traversed in data_end_time order so lifecycle
    evidence reaches the episode reducer chronologically without retaining the
    complete initial lookback in memory.
    """
    lane = "projector-anomalies"
    lower = watermark(lane, INITIAL_LOOKBACK_MINUTES) \
        - EVIDENCE_OVERLAP_MINUTES * 60000
    highest = lower
    query = {"bool": {
        "filter": [{"range": {"execution_end_time": {
            "gte": lower, "format": "epoch_millis"}}}],
        "must_not": [{"exists": {"field": "task_id"}}]}}
    for hits in os_cursor.scan(
            OS_URL, AD_RESULTS, query, "data_end_time",
            source=["detector_id", "anomaly_grade", "confidence",
                    "data_end_time", "data_start_time",
                    "execution_end_time", "entity", "task_id"],
            page=PAGE, keep_alive=PIT_KEEP_ALIVE):
        rows = []
        for hit in hits:
            value = (hit.get("_source") or {}).get("execution_end_time")
            if isinstance(value, (int, float)) and int(value) > highest:
                highest = int(value)
            row = anomaly_row(hit, detector_names)
            if row:
                rows.append(row)
        if rows:
            consume(rows)
    return highest, lane


def detector_names():
    code, body = os_cursor.request(
        OS_URL, "POST", "/_plugins/_anomaly_detection/detectors/_search",
        {"query": {"match_all": {}}, "size": 200})
    if code != 200:
        return {}
    return {h.get("_id"): (h.get("_source") or {}).get("name")
            for h in (body.get("hits", {}).get("hits") or [])}


def load_episode_timelines():
    cutoff = now_ms() - EPISODE_LOOKBACK_HOURS * 3600000
    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{INCIDENTS_INDEX}/_search?ignore_unavailable=true"
        f"&allow_no_indices=true",
        {"size": MAX_OPEN_INCIDENTS,
         "sort": [{"episode_start": "desc"}],
         "query": {"bool": {"should": [
             {"bool": {"must_not": [
                 {"terms": {"episode_state": sorted(TERMINAL)}}]}},
             {"range": {"last_seen": {
                 "gte": cutoff, "format": "epoch_millis"}}}],
             "minimum_should_match": 1}}})
    if code != 200:
        return {}
    timelines = {}
    for hit in ((body.get("hits") or {}).get("hits") or []):
        src = hit.get("_source") or {}
        key = src.get("incident_id")
        if not key:
            continue
        timelines.setdefault(key, []).append(src)
    for episodes in timelines.values():
        episodes.sort(key=lambda e: int(e.get("episode_start", 0)))
    return timelines


def latest_of(timeline):
    return timeline[-1] if timeline else None


def episode_covering(timeline, window):
    covering = None
    for episode in timeline:
        if int(episode.get("episode_start", 0)) <= window:
            covering = episode
        else:
            break
    return covering


def fetch_episodes(doc_ids):
    if not doc_ids:
        return {}
    code, body = os_cursor.request(
        OS_URL, "POST", f"/{INCIDENTS_INDEX}/_mget",
        {"ids": sorted(doc_ids)})
    if code != 200:
        return {}
    out = {}
    for doc in body.get("docs") or []:
        if doc.get("found"):
            out[doc.get("_id")] = doc.get("_source") or {}
    return out


def episode_id(key, episode_start):
    return f"{key}.{int(episode_start)}"


def affected_label(doc):
    kind = doc.get("entity_kind") or "entity"
    entity = doc.get("entity_id") or sentinel("entity_id")
    if kind == "fleet" or entity in ("all", sentinel("entity_id")):
        return "whole fleet"
    labels = {
        "collector": "collector",
        "epn": "EPN",
        "os_node": "OpenSearch node",
        "service": "service",
        "cluster": "cluster",
    }
    return f"{labels.get(kind, kind)} {entity}"


def decorate_incident(ep):
    presentation = signal_identity.presentation(ep["alertname"])
    values = {
        "entity": ep.get("entity_id") or sentinel("entity_id"),
        "collector": ep.get("collector_id") or sentinel("collector_id"),
        "family": ep.get("family") or sentinel("family"),
        "grade_floor": f"{GRADE_FLOOR:g}",
    }
    ep["title"] = presentation["title"].format(**values)
    ep["diagnosis"] = presentation["diagnosis"].format(**values)
    ep["operator_action"] = presentation["action"].format(**values)
    ep["affected"] = affected_label(ep)
    ep["source_label"] = ("threshold rule" if ep.get("source_kind") == "monitor"
                          else "learned anomaly")
    return ep


def blank_incident(doc, key, episode_start):
    ep = {
        "incident_id": key,
        "episode_start": int(episode_start),
        "episode_id": episode_id(key, episode_start),
        "class": "single",
        "source_kind": doc["source_kind"],
        "alertname": doc["alertname"],
        "state": "resolved",
        "episode_state": OPEN,
        "severity": doc["severity"],
        "cluster_id": doc["cluster_id"],
        "entity_kind": doc["entity_kind"],
        "entity_id": doc["entity_id"],
        "collector_id": doc["collector_id"],
        "family": doc["family"],
        "notification_scope": doc["notification_scope"],
        "feed": doc["feed"],
        "topology_version": doc["topology_version"],
        "opened_at": int(episode_start),
        "last_seen": doc["last_seen"],
        "last_evaluation": doc["last_seen"],
        "last_breach_window": 0,
        "last_healthy_window": 0,
        "healthy_windows": 0,
        "member_count": 0,
        "member_count_truncated": False,
        "suppressed_count": 0,
        "entity_samples": [],
        "signal_ids": [],
    }
    if doc.get("grade") is not None:
        ep["latest_grade"] = float(doc["grade"])
        ep["worst_grade"] = float(doc["grade"])
        ep["latest_confidence"] = float(doc.get("confidence") or 0)
    return decorate_incident(ep)


def register_member(ep, doc):
    ids = ep.setdefault("signal_ids", [])
    if doc["source_id"] in ids:
        return False
    if len(ids) >= MAX_MEMBER_IDS:
        ep["member_count_truncated"] = True
        return False
    ids.append(doc["source_id"])
    ep["member_count"] = len(ids)
    samples = ep.setdefault("entity_samples", [])
    if doc["entity_id"] not in samples and len(samples) < 10:
        samples.append(doc["entity_id"])
    return True


def group_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["incident_id"], []).append(row)
    for group in grouped.values():
        group.sort(key=lambda r: (int(r["first_seen"]),
                                  int(r["last_seen"]),
                                  0 if r["state"] == "firing" else 1))
    return grouped


def monitor_healthy_required(group):
    try:
        meta = signal_identity.monitor(group[0]["alertname"])
    except (signal_identity.UnknownSignal, IndexError, KeyError):
        return MONITOR_HEALTHY_REQUIRED
    return int(meta.get("healthy_windows_required",
                        MONITOR_HEALTHY_REQUIRED))


def split_monitor_episodes(group, prior):
    required = monitor_healthy_required(group)
    runs = []
    end = None
    healthy = 0
    if prior and prior.get("episode_state") not in TERMINAL:
        healthy = int(prior.get("healthy_windows", 0))
    for row in group:
        if not runs:
            runs.append([int(row["first_seen"]), [row], healthy])
        elif end is not None and int(row["first_seen"]) > end:
            end = None
            healthy = 0
            runs.append([int(row["first_seen"]), [row], 0])
        else:
            runs[-1][1].append(row)
        if row["state"] == "resolved":
            healthy += 1
            if healthy >= required:
                terminated = int(row.get("end_time") or row["last_seen"])
                end = terminated if end is None else max(end, terminated)
        else:
            healthy = 0
            end = None
        runs[-1][2] = healthy
    if runs and prior and prior.get("episode_state") not in TERMINAL:
        prior_start = int(prior.get("episode_start", 0))
        if prior_start <= runs[0][0]:
            runs[0][0] = prior_start
    return runs


def detector_episode_start(timeline, row):
    window = int(row["@timestamp"])
    covering = episode_covering(timeline, window)
    if covering is not None:
        if covering.get("episode_state") not in TERMINAL:
            return int(covering["episode_start"])
        closed_at = int(covering.get("resolved_at")
                        or covering.get("last_seen")
                        or covering.get("episode_start", 0))
        if window <= closed_at:
            return int(covering["episode_start"])
    elif timeline:
        return None
    if row["state"] != "firing":
        return None
    return window


def apply_monitor(episodes, rows, latest, stored):
    for key, group in group_rows(rows).items():
        required = monitor_healthy_required(group)
        for start, members, healthy in split_monitor_episodes(
                group, latest.get(key)):
            eid = episode_id(key, start)
            ep = episodes.get(eid) or stored.get(eid) \
                or blank_incident(members[0], key, start)
            ep["episode_start"] = int(start)
            ep["episode_id"] = eid
            ep["opened_at"] = int(start)
            for doc in members:
                if ep.get("topology_version") != doc["topology_version"]:
                    ep = blank_incident(doc, key, start)
                ep["last_seen"] = max(int(ep.get("last_seen", 0)),
                                      doc["last_seen"])
                ep["last_evaluation"] = ep["last_seen"]
                register_member(ep, doc)
                if doc["severity"] == "page":
                    ep["severity"] = "page"
                if doc["state"] == "firing":
                    ep["episode_state"] = OPEN
                    ep["state"] = "firing"
                    ep.pop("resolved_at", None)
                else:
                    ep["resolved_at"] = doc.get("end_time") or doc["last_seen"]
                doc["episode_id"] = eid
            ep["healthy_windows"] = healthy
            if healthy >= required:
                ep["episode_state"] = RESOLVED
                ep["state"] = "resolved"
            elif healthy > 0:
                ep["episode_state"] = RECOVERING
                ep["state"] = "firing"
                ep.pop("resolved_at", None)
            episodes[eid] = ep


def remember_episode(timeline, ep):
    """Insert or replace an episode while keeping a timeline chronological."""
    start = int(ep.get("episode_start", 0))
    for index, current in enumerate(timeline):
        current_start = int(current.get("episode_start", 0))
        if current_start == start:
            timeline[index] = ep
            return
        if current_start > start:
            timeline.insert(index, ep)
            return
    timeline.append(ep)


def apply_detector(episodes, rows, timelines, stored):
    for key, group in group_rows(rows).items():
        # This is deliberately the shared timeline, not a copy. A detector
        # episode opened near the end of one PIT page must be visible to the
        # recovery evidence at the start of the next page.
        timeline = timelines.setdefault(key, [])
        for doc in group:
            meta = signal_identity.detector(doc["alertname"])
            close_threshold = float(meta.get("close_threshold", 0.0))
            required = int(meta.get("healthy_windows_required", 3))
            window = int(doc["@timestamp"])

            start = detector_episode_start(timeline, doc)
            if start is None:
                continue
            eid = episode_id(key, start)
            ep = episodes.get(eid) or stored.get(eid) \
                or blank_incident(doc, key, start)
            if ep.get("topology_version") != doc["topology_version"]:
                ep = blank_incident(doc, key, start)
            ep["episode_start"] = int(start)
            ep["episode_id"] = eid
            doc["episode_id"] = eid
            ep["latest_grade"] = float(doc["grade"])
            ep["worst_grade"] = max(
                float(ep.get("worst_grade", 0)), float(doc["grade"]))
            ep["latest_confidence"] = float(doc.get("confidence") or 0)

            if int(doc["last_seen"]) > int(ep.get("last_evaluation", 0)):
                ep["last_evaluation"] = int(doc["last_seen"])
                if ep.get("episode_state") == STALE:
                    ep["episode_state"] = OPEN
                    ep.pop("stale_since", None)

            if doc["state"] == "firing":
                if window > int(ep.get("last_breach_window", 0)):
                    ep["last_breach_window"] = window
                    ep["last_healthy_window"] = 0
                    ep["healthy_windows"] = 0
                    ep["episode_state"] = OPEN
                    ep["state"] = "firing"
                    ep.pop("resolved_at", None)
                    ep["last_seen"] = max(int(ep.get("last_seen", 0)),
                                          doc["last_seen"])
                    register_member(ep, doc)
            elif ep.get("episode_state") not in TERMINAL \
                    and doc["grade"] <= close_threshold \
                    and window > int(ep.get("last_healthy_window", 0)) \
                    and window > int(ep.get("last_breach_window", 0)):
                ep["last_healthy_window"] = window
                ep["healthy_windows"] = int(ep.get("healthy_windows", 0)) + 1
                if ep["healthy_windows"] >= required:
                    ep["episode_state"] = RESOLVED
                    ep["state"] = "resolved"
                    ep["resolved_at"] = doc["last_seen"]
                else:
                    ep["episode_state"] = RECOVERING
                    ep["state"] = "firing"
            episodes[eid] = ep
            remember_episode(timeline, ep)


def prefetch_detector_episodes(rows, timelines, stored):
    """Load persisted episodes a bounded page may reference by exact ID.

    Preserved-clock replay gives incidents old event timestamps even though the
    AD result was executed now. A terminal incident can therefore be outside
    the timeline lookback while its source row is inside the execution-time
    overlap. Exact-ID lookup keeps that replay attached to the old episode.
    """
    candidates = set()
    for key, group in group_rows(rows).items():
        timeline = timelines.get(key) or []
        for doc in group:
            start = detector_episode_start(timeline, doc)
            if start is not None:
                candidates.add(episode_id(key, start))
    missing = candidates.difference(stored)
    if not missing:
        return
    loaded = fetch_episodes(missing)
    stored.update(loaded)
    for ep in loaded.values():
        key = ep.get("incident_id")
        if key:
            remember_episode(timelines.setdefault(key, []), ep)


def age_episodes(incidents, moment):
    roster = latest_roster()
    collectors = set(roster["collectors"]) if roster else set()
    stale_dwell = {}
    stale_count = {}
    for key, ep in incidents.items():
        if ep.get("episode_state") in TERMINAL:
            continue
        if ep.get("source_kind") != "detector":
            continue
        if (ep.get("entity_kind") == "collector" and roster
                and ep.get("entity_id") not in collectors):
            ep["episode_state"] = CLOSED_EXPECTED
            ep["state"] = "resolved"
            ep["resolved_at"] = moment
            continue
        meta = signal_identity.detector(ep["alertname"])
        budget = (int(meta.get("expected_interval_minutes", 1))
                  + int(meta.get("maximum_lateness_minutes", 5))) * 60000
        last = int(ep.get("last_evaluation", ep.get("last_seen", moment)))
        if moment - last > budget:
            if ep.get("episode_state") != STALE or not ep.get("stale_since"):
                ep["stale_since"] = int(ep.get("stale_since") or moment)
            ep["episode_state"] = STALE
            ep["state"] = "firing"
            name = ep["alertname"]
            stale_count[name] = stale_count.get(name, 0) + 1
            stale_dwell.setdefault(name, []).append(
                moment - int(ep.get("stale_since", moment)))
    return stale_count, stale_dwell


def percentile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(round(fraction * (len(ordered) - 1)))))
    return int(ordered[index])


def classify_mass_silence(rows):
    firing = [r for r in rows if r["state"] == "firing"]
    if any(r["alertname"] == "fleet-fb-silence" for r in firing):
        return "unknown-mass-silence"
    silent = {r["entity_id"] for r in firing
              if r["alertname"] == "collector-down"}
    roster = latest_roster()
    size = len(roster["collectors"]) if roster else 0
    if size and len(silent) / float(size) >= MASS_SILENCE_FRACTION:
        return "unknown-mass-silence"
    return None


def write_signals(rows):
    lines = []
    written = 0
    for doc in rows:
        lines.append(json.dumps({"index": {
            "_index": SIGNALS_INDEX,
            "_id": f"{doc['source_kind']}:{doc['source_uid']}"}}))
        lines.append(json.dumps(doc))
        if len(lines) >= BULK_DOCUMENTS * 2:
            written += checked_bulk(lines, "signal")
            lines = []
    if lines:
        written += checked_bulk(lines, "signal")
    return written


def checked_bulk(lines, label):
    ok, failures = os_cursor.bulk(OS_URL, lines, refresh="false")
    if failures:
        raise os_cursor.CursorError(
            f"{len(failures)} {label} rows rejected; first: "
            f"{json.dumps(failures[0])[:300]}")
    return ok


def write_incidents(incidents, mass_class):
    lines = []
    written = 0
    for key, ep in incidents.items():
        if mass_class and ep.get("state") == "firing" \
                and ep.get("alertname") in ("fleet-fb-silence",
                                            "collector-down"):
            ep["class"] = mass_class
        decorate_incident(ep)
        ep["@timestamp"] = ep.get("opened_at", now_ms())
        lines.append(json.dumps(
            {"index": {"_index": INCIDENTS_INDEX, "_id": key}}))
        lines.append(json.dumps(ep))
        if len(lines) >= BULK_DOCUMENTS * 2:
            written += checked_bulk(lines, "incident")
            lines = []
    if lines:
        written += checked_bulk(lines, "incident")
    return written


def labels_for(ep):
    scope = ("fleet" if ep["notification_scope"] == "fleet"
             else f"collector:{ep['collector_id']}")
    return {
        "alertname": ep["alertname"],
        "source": ep["source_kind"],
        "cluster_id": ep["cluster_id"],
        "severity": ep["severity"],
        "entity_kind": ep["entity_kind"],
        "entity_id": ep.get("entity_id") or sentinel("entity_id"),
        "collector_id": ep.get("collector_id") or sentinel("collector_id"),
        "family": ep.get("family") or sentinel("family"),
        "notification_scope": scope,
    }


def alertmanager_payload(incidents):
    out = []
    for ep in incidents.values():
        alert = {
            "labels": labels_for(ep),
            "annotations": {
                "incident_id": ep["incident_id"],
                "episode_id": ep["episode_id"],
                "episode_state": ep.get("episode_state", OPEN),
                "member_count": str(ep.get("member_count", 0)),
                "signal_ids": ",".join(
                    (ep.get("signal_ids") or [])[:MAX_ANNOTATION_IDS]),
                "signal_ids_truncated": str(
                    len(ep.get("signal_ids") or []) > MAX_ANNOTATION_IDS
                    or bool(ep.get("member_count_truncated"))).lower(),
                "entity_samples": ",".join(ep.get("entity_samples") or []),
                "topology_version": ep.get("topology_version", "none"),
                "class": ep.get("class", "single"),
                "title": ep.get("title", ep["alertname"]),
                "diagnosis": ep.get("diagnosis", ""),
                "operator_action": ep.get("operator_action", ""),
                "affected": ep.get("affected", affected_label(ep)),
                "run_id": sentinel("run_id"),
            },
            "startsAt": iso(ep.get("opened_at", now_ms())),
        }
        if ep.get("state") == "resolved":
            alert["endsAt"] = iso(ep.get("resolved_at", ep.get("last_seen")))
        out.append(alert)
    return out


def send_to_alertmanager(alerts):
    if not alerts:
        return probe_alertmanager()
    body = json.dumps(alerts).encode()
    req = urllib.request.Request(
        f"{ALERTMANAGER_URL}/api/v2/alerts", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        log(f"alertmanager unreachable: {e}")
        return False, int((time.time() - started) * 1000)
    return ok, int((time.time() - started) * 1000)


def probe_alertmanager():
    started = time.time()
    try:
        with urllib.request.urlopen(
                f"{ALERTMANAGER_URL}/-/ready", timeout=10) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        log(f"alertmanager readiness probe failed: {e}")
        ok = False
    return ok, int((time.time() - started) * 1000)


def heartbeat(result, stale_count, stale_dwell):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    dwells = [d for values in stale_dwell.values() for d in values]
    docs = [
        {"kind": "projector", "@timestamp": ts,
         "signals_open": result["open_signals"],
         "incidents_open": result["open_incidents"],
         "projector_cycle_ok": 1 if result["cycle_ok"] else 0,
         "stale_episodes": sum(stale_count.values()),
         "stale_dwell_p50_ms": percentile(dwells, 0.5),
         "stale_dwell_p95_ms": percentile(dwells, 0.95),
         "projector_cycle_ms": result["cycle_ms"]},
        {"kind": "alertmanager", "@timestamp": ts,
         "am_up": 1 if result["am_ok"] else 0,
         "am_latency_ms": result["am_latency_ms"]},
    ]
    for name, count in sorted(stale_count.items()):
        docs.append({
            "kind": "projector_detector", "@timestamp": ts,
            "alertname": name, "stale_episodes": count,
            "stale_dwell_p50_ms": percentile(stale_dwell.get(name, []), 0.5),
            "stale_dwell_p95_ms": percentile(stale_dwell.get(name, []), 0.95),
        })
    lines = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": METRICS_INDEX}}))
        lines.append(json.dumps(doc))
    try:
        _, failures = os_cursor.bulk(OS_URL, lines, refresh="false")
        if failures:
            log(f"heartbeat write rejected {len(failures)} rows; first: "
                f"{json.dumps(failures[0])[:300]}")
    except os_cursor.CursorError as e:
        log(f"heartbeat write failed: {e}")


def schedule_prune(index, query):
    """Submit retention work without blocking the re-send loop.

    A synchronous delete-by-query may legitimately run for minutes on these
    small VMs.  The projector must never wait for it: Alertmanager relies on a
    30-second re-send and deploy relies on the first functional heartbeat.
    """
    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{index}/_delete_by_query?conflicts=proceed"
        "&wait_for_completion=false&slices=1",
        {"query": query}, timeout=30)
    if code != 200:
        raise os_cursor.CursorError(
            f"cannot schedule retention for {index}: HTTP {code} "
            f"{json.dumps(body)[:200]}")
    log(f"scheduled retention for {index}: task={body.get('task', 'unknown')}")


def prune():
    if RETENTION_DAYS <= 0:
        return
    for index in (SIGNALS_INDEX, NOTIFICATIONS_INDEX):
        schedule_prune(index, {"range": {
            "@timestamp": {"lt": f"now-{RETENTION_DAYS}d"}}})
    schedule_prune(INCIDENTS_INDEX, {"bool": {"filter": [
            {"terms": {"episode_state": sorted(TERMINAL)}},
            {"range": {"@timestamp": {
                "lt": f"now-{RETENTION_DAYS}d"}}}]}})


def cycle():
    started = time.time()
    moment = now_ms()
    names = detector_names()

    current = snapshot_current_alerts()
    history, history_high, history_lane = ingest_alert_history()
    alert_rows = merge_alert_rows(current + history)
    for row in alert_rows:
        row["incident_id"] = incident_id(row)
        row["suppressed_by"] = sentinel("entity_id")

    timelines = load_episode_timelines()
    latest = {k: latest_of(v) for k, v in timelines.items()
              if latest_of(v) is not None}

    # The timeline query already loaded every open and every recent terminal
    # episode. Seed the exact-ID lookup with those documents, then mget monitor
    # candidates that may fall outside the timeline window.
    stored = {}
    for key, timeline in timelines.items():
        for ep in timeline:
            eid = ep.get("episode_id") or episode_id(
                key, ep.get("episode_start", 0))
            stored[eid] = ep

    candidates = set()
    for key, group in group_rows(alert_rows).items():
        for start, _, _ in split_monitor_episodes(group, latest.get(key)):
            candidates.add(episode_id(key, start))
    stored.update(fetch_episodes(candidates))

    incidents = {}
    apply_monitor(incidents, alert_rows, latest, stored)
    anomaly_firing = 0

    def consume_anomaly_page(rows):
        nonlocal anomaly_firing
        for row in rows:
            row["incident_id"] = incident_id(row)
            row["suppressed_by"] = sentinel("entity_id")
        prefetch_detector_episodes(rows, timelines, stored)
        apply_detector(incidents, rows, timelines, stored)
        for row in rows:
            eid = row.get("episode_id")
            if eid in incidents:
                stored[eid] = incidents[eid]
        firing = [row for row in rows if row["state"] == "firing"]
        for row in firing:
            row.setdefault(
                "episode_id", episode_id(row["incident_id"], 0))
        if firing:
            write_signals(firing)
            anomaly_firing += len(firing)

    anomaly_high, anomaly_lane = ingest_anomaly_results(
        names, consume_anomaly_page)

    for key, ep in list(latest.items()):
        eid = ep.get("episode_id") or episode_id(key, ep.get("episode_start", 0))
        if eid not in incidents and ep.get("episode_state") not in TERMINAL:
            incidents[eid] = ep
    stale_count, stale_dwell = age_episodes(incidents, moment)

    for row in alert_rows:
        row.setdefault("episode_id", episode_id(row["incident_id"], 0))
    if alert_rows:
        write_signals(alert_rows)

    mass_class = classify_mass_silence(alert_rows)
    if incidents:
        write_incidents(incidents, mass_class)

    os_cursor.write_watermark(
        OS_URL, STATE_INDEX, history_lane, history_high,
        note="alert history; advanced only after a complete traversal")
    os_cursor.write_watermark(
        OS_URL, STATE_INDEX, anomaly_lane, anomaly_high,
        note="realtime anomaly results, evidence included; advanced only "
             "after a complete traversal")

    am_ok, am_latency = send_to_alertmanager(alertmanager_payload(incidents))
    open_incidents = sum(1 for e in incidents.values()
                         if e.get("state") == "firing")
    if mass_class:
        log(f"mass silence observed — class {mass_class}; it pages, because "
            f"only authoritative run/phase telemetry may downgrade it to a "
            f"run boundary")
    if stale_count:
        log(f"stale episodes by detector: {stale_count} — a missing "
            f"evaluation is never a recovery, so these stay open")
    return {
        "open_signals": (sum(1 for row in alert_rows
                             if row["state"] == "firing") + anomaly_firing),
        "open_incidents": open_incidents,
        "cycle_ok": True,
        "cycle_ms": int((time.time() - started) * 1000),
        "am_ok": am_ok,
        "am_latency_ms": am_latency,
    }, stale_count, stale_dwell


def main():
    log(f"projecting alerts and anomalies into {SIGNALS_INDEX} / "
        f"{INCIDENTS_INDEX} every {INTERVAL}s; re-sending every open episode "
        f"to {ALERTMANAGER_URL} well inside resolve_timeout="
        f"{RESOLVE_TIMEOUT_SECONDS}s")
    if INTERVAL * 2 >= RESOLVE_TIMEOUT_SECONDS:
        log(f"FATAL: re-send cadence {INTERVAL}s is not comfortably under "
            f"resolve_timeout {RESOLVE_TIMEOUT_SECONDS}s — Alertmanager would "
            f"resolve live alerts between cycles")
        return 1
    last_prune = 0.0
    first_cycle_succeeded = False
    log("starting the first projection cycle before retention maintenance")
    while True:
        started = time.time()
        result = {"open_signals": 0, "open_incidents": 0, "cycle_ms": 0,
                  "cycle_ok": False, "am_ok": False, "am_latency_ms": 0}
        stale_count, stale_dwell = {}, {}
        try:
            result, stale_count, stale_dwell = cycle()
        except Exception as e:
            log(f"cycle failed, watermarks held: {e}")
            result["am_ok"], result["am_latency_ms"] = probe_alertmanager()
        heartbeat(result, stale_count, stale_dwell)
        if result["cycle_ok"] and not first_cycle_succeeded:
            first_cycle_succeeded = True
            log(f"first projection cycle completed in "
                f"{result['cycle_ms']}ms; functional heartbeat written")
        # Retention is maintenance, never a prerequisite for projection.  Run
        # it only after a successful heartbeat and submit it asynchronously so
        # it cannot break the Alertmanager re-send cadence or the deploy gate.
        if result["cycle_ok"] and started - last_prune >= PRUNE_EVERY_SECONDS:
            last_prune = started
            try:
                prune()
            except Exception as e:
                log(f"prune scheduling raised: {e}")
        time.sleep(max(5, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
