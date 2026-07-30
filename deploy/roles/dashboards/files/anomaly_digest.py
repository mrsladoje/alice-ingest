import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os_cursor  # noqa: E402
import signal_identity  # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
DIGEST_INDEX = os.environ.get("DIGEST_INDEX", "alice-anomalies")
STATE_INDEX = os.environ.get("STATE_INDEX", "alice-lane-state")
RESULTS = ".opendistro-anomaly-results*"
LANE = "anomaly-realtime"
INTERVAL = int(os.environ.get("INTERVAL", "60"))
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0.5"))
OVERLAP_MINUTES = int(os.environ.get("OVERLAP_MINUTES", "15"))
INITIAL_LOOKBACK_MINUTES = int(
    os.environ.get("INITIAL_LOOKBACK_MINUTES", "120"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "14"))
PAGE = int(os.environ.get("PAGE", "500"))

HIGH = float(os.environ.get("SEVERITY_HIGH", "0.7"))
MEDIUM = float(os.environ.get("SEVERITY_MEDIUM", "0.4"))

SOURCE_FIELDS = ["detector_id", "anomaly_grade", "confidence",
                 "data_end_time", "data_start_time", "execution_end_time",
                 "entity", "task_id"]


def log(msg):
    print(f"[anomaly-digest] {msg}", flush=True)


def severity(grade):
    if grade >= HIGH:
        return "high"
    if grade >= MEDIUM:
        return "medium"
    return "low"


def plugin_catalog():
    code, body = os_cursor.request(
        OS_URL, "POST", "/_plugins/_anomaly_detection/detectors/_search",
        {"query": {"match_all": {}}, "size": 200})
    if code != 200:
        log(f"cannot list detectors: HTTP {code}")
        return {}
    out = {}
    for h in body.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        name = src.get("name") or h.get("_id")
        out[h.get("_id")] = {
            "detector": name,
            "about": (src.get("description") or "").strip() or name,
            "measures": [f.get("feature_name")
                         for f in (src.get("feature_attributes") or [])
                         if f.get("feature_name")],
        }
    return out


def realtime_query(lower_ms):
    return {"bool": {
        "filter": [
            {"range": {"execution_end_time": {
                "gte": lower_ms, "format": "epoch_millis"}}},
            {"range": {"anomaly_grade": {"gt": GRADE_FLOOR}}},
        ],
        "must_not": [{"exists": {"field": "task_id"}}],
    }}


def project(hit, meta, run):
    src = hit.get("_source", {})
    grade = src.get("anomaly_grade")
    if grade is None:
        return None, None
    name = meta["detector"]
    entity_kind, entity_id, entity_field = signal_identity.entity_of(name, src)
    scope_field, scope = signal_identity.scope_string(src)
    index = hit.get("_index")
    doc_id = hit.get("_id")
    uid = os_cursor.source_uid(index, doc_id)
    doc = {
        "@timestamp": src.get("data_end_time"),
        "window_start": src.get("data_start_time"),
        "execution_end_time": src.get("execution_end_time"),
        "detector": name,
        "about": meta["about"],
        "measures": meta["measures"],
        "scope_field": scope_field,
        "scope": scope,
        "scope_kind": entity_kind,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "entity_field": entity_field,
        "family": signal_identity.detector(name).get("family", "none"),
        "grade": round(float(grade), 4),
        "confidence": round(float(src.get("confidence") or 0), 4),
        "severity": severity(float(grade)),
        "detector_id": src.get("detector_id"),
        "run": run,
        "source_index": index,
        "source_id": doc_id,
        "source_uid": uid,
    }
    task_id = src.get("task_id")
    if task_id:
        doc["task_id"] = task_id
    return f"{run}:{uid}", doc


def cycle(catalog):
    now_ms = int(time.time() * 1000)
    watermark = os_cursor.read_watermark(OS_URL, STATE_INDEX, LANE)
    if watermark is None:
        watermark = now_ms - INITIAL_LOOKBACK_MINUTES * 60000
    lower = watermark - OVERLAP_MINUTES * 60000
    highest = watermark
    written = 0
    skipped = set()
    for hits in os_cursor.scan(
            OS_URL, RESULTS, realtime_query(lower), "execution_end_time",
            source=SOURCE_FIELDS, page=PAGE):
        lines = []
        for hit in hits:
            src = hit.get("_source", {})
            end = src.get("execution_end_time")
            if isinstance(end, (int, float)) and int(end) > highest:
                highest = int(end)
            meta = catalog.get(src.get("detector_id"))
            if not meta:
                continue
            try:
                doc_id, doc = project(hit, meta, "realtime")
            except signal_identity.UnknownSignal as e:
                skipped.add(str(e))
                continue
            if doc is None:
                continue
            lines.append(json.dumps(
                {"index": {"_index": DIGEST_INDEX, "_id": doc_id}}))
            lines.append(json.dumps(doc))
        ok, failures = os_cursor.bulk(OS_URL, lines)
        written += ok
        if failures:
            raise os_cursor.CursorError(
                f"{len(failures)} digest documents rejected; first: "
                f"{json.dumps(failures[0])[:300]}")
    for message in sorted(skipped):
        log(f"FATAL-CONFIG: {message}")
    if skipped:
        raise os_cursor.CursorError(
            "signal catalog does not cover every live detector; refusing to "
            "advance the watermark over results it cannot classify")
    os_cursor.write_watermark(
        OS_URL, STATE_INDEX, LANE, highest,
        note="realtime anomaly projection; advanced only after a complete "
             "traversal")
    return written


def prune():
    if RETENTION_DAYS <= 0:
        return
    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{DIGEST_INDEX}/_delete_by_query?conflicts=proceed",
        {"query": {"bool": {"filter": [
            {"range": {"@timestamp": {"lt": f"now-{RETENTION_DAYS}d"}}}]}}},
        timeout=180)
    if code == 200 and body.get("deleted"):
        log(f"pruned {body['deleted']} digest documents older than "
            f"{RETENTION_DAYS}d")


def main():
    log(f"projecting realtime anomalies into {DIGEST_INDEX} every {INTERVAL}s "
        f"(grade>{GRADE_FLOOR}, execution_end_time watermark in {STATE_INDEX}, "
        f"overlap {OVERLAP_MINUTES}m)")
    ticks = 0
    while True:
        started = time.time()
        try:
            catalog = plugin_catalog()
            if catalog:
                n = cycle(catalog)
                if n:
                    log(f"upserted {n} anomaly digest documents")
            ticks += 1
            if ticks % 60 == 0:
                prune()
        except Exception as e:
            log(f"cycle failed, watermark held: {e}")
        time.sleep(max(5, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
