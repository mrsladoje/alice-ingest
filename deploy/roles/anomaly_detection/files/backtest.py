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
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0.5"))
ONLY = [d.strip() for d in os.environ.get("DETECTORS", "").split(",")
        if d.strip()]
SKIP_INDICES = [i.strip() for i in
                os.environ.get("SKIP_INDICES", "cockpit-metrics").split(",")
                if i.strip()]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "15"))
TIMEOUT_MINUTES = int(os.environ.get("TIMEOUT_MINUTES", "45"))
MAX_WINDOW_HOURS = float(os.environ.get("MAX_WINDOW_HOURS", "24"))
PAGE = int(os.environ.get("PAGE", "1000"))

AD = "/_plugins/_anomaly_detection/detectors"
DONE = ("FINISHED", "FAILED", "STOPPED")


def log(msg):
    print(f"[backtest] {msg}", flush=True)


def req(method, path, payload=None, raw=None, ctype="application/json",
        timeout=120):
    if raw is not None:
        body = raw.encode()
    elif payload is not None:
        body = json.dumps(payload).encode()
    else:
        body = None
    headers = {"Content-Type": ctype} if body else {}
    r = urllib.request.Request(
        OS_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def catalog():
    code, body = req("POST", f"{AD}/_search",
                     {"query": {"match_all": {}}, "size": 200})
    if code != 200:
        log(f"FATAL: cannot list detectors: HTTP {code}")
        sys.exit(1)
    out = []
    for h in body.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        name = src.get("name") or h.get("_id")
        indices = src.get("indices") or []
        if ONLY and name not in ONLY:
            continue
        if not ONLY and any(i.startswith(s)
                            for i in indices for s in SKIP_INDICES):
            continue
        out.append({
            "id": h.get("_id"),
            "name": name,
            "about": (src.get("description") or "").strip() or name,
            "measures": [f.get("feature_name")
                         for f in (src.get("feature_attributes") or [])
                         if f.get("feature_name")],
            "indices": indices,
            "time_field": src.get("time_field"),
            "interval_minutes": (((src.get("detection_interval") or {})
                                  .get("period") or {}).get("interval") or 1),
        })
    return sorted(out, key=lambda d: d["name"])


def window(det):
    idx = ",".join(det["indices"])
    tf = det["time_field"]
    code, body = req(
        "POST", f"/{idx}/_search?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True,
         "aggs": {"lo": {"min": {"field": tf}},
                  "hi": {"max": {"field": tf}}}})
    if code != 200:
        return None, None, 0
    aggs = body.get("aggregations") or {}
    lo = (aggs.get("lo") or {}).get("value")
    hi = (aggs.get("hi") or {}).get("value")
    total = ((body.get("hits") or {}).get("total") or {}).get("value", 0)
    if lo is None or hi is None:
        return None, None, total
    lo, hi = int(lo), int(hi)
    cap = int(MAX_WINDOW_HOURS * 3600000)
    if cap and hi - lo > cap:
        log(f"  {det['name']}: {(hi - lo) / 3600000.0:.1f}h of history spans "
            f"several loads; using the most recent {MAX_WINDOW_HOURS:g}h "
            f"(raise MAX_WINDOW_HOURS to widen)")
        lo = hi - cap
    return lo, hi, total


def start(det, lo, hi):
    code, body = req("POST", f"{AD}/{det['id']}/_start",
                     {"start_time": lo, "end_time": hi})
    if code not in (200, 201):
        return None, f"HTTP {code} {json.dumps(body)[:200]}"
    return body.get("_id") or body.get("id"), None


def task_of(det):
    code, body = req("GET", f"{AD}/{det['id']}?task=true")
    if code != 200:
        return {}
    return body.get("historical_analysis_task") or {}


def _progress(task):
    bits = []
    for label, key in (("init", "init_progress"), ("done", "task_progress")):
        v = task.get(key)
        if isinstance(v, (int, float)):
            bits.append(f"{label}={float(v):.0%}")
    return (" " + " ".join(bits)) if bits else ""


def wait(det):
    deadline = time.time() + TIMEOUT_MINUTES * 60
    task, last = {}, None
    while time.time() < deadline:
        task = task_of(det)
        state = str(task.get("state") or "").upper()
        if state != last:
            log(f"  {det['name']}: {state or 'unknown'}{_progress(task)}")
            last = state
        if state in DONE:
            return task
        time.sleep(POLL_SECONDS)
    return {"state": "TIMEOUT", "task_id": task.get("task_id")}


def project(hit, meta):
    src = hit.get("_source", {})
    grade = src.get("anomaly_grade")
    if grade is None:
        return None
    name = meta["detector"]
    entity_kind, entity_id, _entity_field = signal_identity.entity_of(
        name, src)
    _scope_field, scope = signal_identity.scope_string(src)
    return {
        "detector": name,
        "about": meta["about"],
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "scope": scope,
        "grade": round(float(grade), 4),
        "confidence": round(float(src.get("confidence") or 0), 4),
    }


def count_task(det, task_id):
    meta = {"detector": det["name"], "about": det["about"],
            "measures": det["measures"]}
    query = {"bool": {"filter": [
        {"term": {"task_id": task_id}},
        {"range": {"anomaly_grade": {"gt": GRADE_FLOOR}}}]}}
    found = 0
    try:
        for hits in os_cursor.scan(
                OS_URL, ".opendistro-anomaly-results*",
                query, "data_end_time",
                source=["detector_id", "anomaly_grade", "confidence",
                        "data_end_time", "data_start_time",
                        "execution_end_time", "entity", "task_id"],
                page=PAGE):
            for h in hits:
                row = project(h, meta)
                if row is None:
                    continue
                found += 1
    except (os_cursor.CursorError, signal_identity.UnknownSignal) as e:
        log(f"  {det['name']}: historical count aborted: {e}")
    return found


def main():
    dets = catalog()
    if not dets:
        log("no detectors selected — set DETECTORS or widen SKIP_INDICES")
        return 1
    log(f"backtesting {len(dets)} detectors: "
        f"{', '.join(d['name'] for d in dets)}")

    summary = []
    for det in dets:
        lo, hi, total = window(det)
        if lo is None:
            log(f"{det['name']}: SKIP — no {det['time_field']} values in "
                f"{','.join(det['indices'])} ({total} documents)")
            summary.append((det["name"], "no data", 0, 0))
            continue
        span_min = (hi - lo) / 60000.0
        intervals = span_min / max(1, det["interval_minutes"])
        log(f"{det['name']}: window {span_min:.0f} min "
            f"({intervals:.0f} intervals of {det['interval_minutes']}m) "
            f"over {total} documents")
        if intervals < 32:
            log(f"  WARNING: only {intervals:.0f} intervals — a detector needs "
                f"32 to finish training, so this run will stay in Init")
        task_id, err = start(det, lo, hi)
        if err:
            log(f"  {det['name']}: start refused: {err}")
            summary.append((det["name"], "start refused", 0, 0))
            continue
        task = wait(det)
        state = str(task.get("state") or "unknown").upper()
        tid = task.get("task_id") or task_id
        if state != "FINISHED":
            log(f"  {det['name']}: ended {state} "
                f"{task.get('error') or ''}".rstrip())
        found = count_task(det, tid) if tid else 0
        log(f"  {det['name']}: {state}, {found} historical anomalies above "
            f"grade {GRADE_FLOOR}")
        summary.append((det["name"], state, found, int(intervals)))

    log("")
    log(f"{'detector':34s} {'state':12s} {'anomalies':>10s} {'intervals':>10s}")
    for name, state, found, intervals in summary:
        log(f"{name:34s} {state:12s} {found:10d} {intervals:10d}")
    total_found = sum(s[2] for s in summary)
    log("")
    log(f"{total_found} historical anomalies above grade {GRADE_FLOOR} "
        "(report only; not written to alice-signals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
