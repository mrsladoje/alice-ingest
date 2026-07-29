import json
import os
import sys
import time
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
DIGEST_INDEX = os.environ.get("DIGEST_INDEX", "alice-anomalies")
INTERVAL = int(os.environ.get("INTERVAL", "60"))
LOOKBACK = os.environ.get("LOOKBACK", "now-2h")
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
BATCH = int(os.environ.get("BATCH", "500"))

HIGH = float(os.environ.get("SEVERITY_HIGH", "0.7"))
MEDIUM = float(os.environ.get("SEVERITY_MEDIUM", "0.4"))


def log(msg):
    print(f"[anomaly-digest] {msg}", flush=True)


def req(method, path, payload=None, raw=None, ctype="application/json"):
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
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def detector_catalog():
    code, body = req("POST", "/_plugins/_anomaly_detection/detectors/_search",
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
            "indices": src.get("indices") or [],
        }
    return out


def severity(grade):
    if grade >= HIGH:
        return "high"
    if grade >= MEDIUM:
        return "medium"
    return "low"


def scope_of(src):
    ents = src.get("entity") or []
    if isinstance(ents, dict):
        ents = [ents]
    names, values = [], []
    for e in ents:
        if not isinstance(e, dict):
            continue
        n, v = e.get("name"), e.get("value")
        if n is not None and v is not None:
            names.append(str(n))
            values.append(str(v))
    if not values:
        return "", "whole fleet"
    return ",".join(names), ",".join(values)


def kind_of(scope_field, indices):
    if not scope_field:
        return "fleet-wide"
    if scope_field == "origin_host":
        return "EPN host"
    if scope_field == "node":
        for i in indices:
            if i.startswith("cockpit-metrics"):
                return "cluster node"
        return "collector"
    return scope_field


def harvest(catalog):
    code, body = req(
        "POST",
        "/.opendistro-anomaly-results*/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": BATCH,
         "sort": [{"data_end_time": "desc"}],
         "_source": ["detector_id", "anomaly_grade", "confidence",
                     "data_end_time", "data_start_time", "entity"],
         "query": {"bool": {"filter": [
             {"range": {"data_end_time": {"gte": LOOKBACK}}},
             {"range": {"anomaly_grade": {"gt": GRADE_FLOOR}}}]}}})
    if code != 200:
        log(f"cannot read anomaly results: HTTP {code}")
        return 0
    hits = body.get("hits", {}).get("hits", [])
    if not hits:
        return 0

    lines = []
    for h in hits:
        src = h.get("_source", {})
        did = src.get("detector_id")
        meta = catalog.get(did)
        if not meta:
            continue
        grade = src.get("anomaly_grade")
        if grade is None:
            continue
        scope_field, scope = scope_of(src)
        end = src.get("data_end_time")
        doc_id = f"{did}:{end}:{scope}"
        doc = {
            "@timestamp": end,
            "window_start": src.get("data_start_time"),
            "detector": meta["detector"],
            "about": meta["about"],
            "measures": meta["measures"],
            "scope_field": scope_field,
            "scope": scope,
            "scope_kind": kind_of(scope_field, meta["indices"]),
            "grade": round(float(grade), 4),
            "confidence": round(float(src.get("confidence") or 0), 4),
            "severity": severity(float(grade)),
            "detector_id": did,
            "run": "realtime",
        }
        lines.append(json.dumps(
            {"index": {"_index": DIGEST_INDEX, "_id": doc_id}}))
        lines.append(json.dumps(doc))

    if not lines:
        return 0
    code, resp = req("POST", "/_bulk?refresh=false",
                     raw="\n".join(lines) + "\n",
                     ctype="application/x-ndjson")
    if code != 200:
        log(f"bulk failed: HTTP {code} {json.dumps(resp)[:300]}")
        return 0
    failed = [i for i in resp.get("items", [])
              if (i.get("index") or {}).get("error")]
    if failed:
        log(f"{len(failed)} digest documents rejected; first: "
            f"{json.dumps(failed[0])[:300]}")
    return len(lines) // 2 - len(failed)


def prune():
    if RETENTION_DAYS <= 0:
        return
    code, body = req(
        "POST",
        f"/{DIGEST_INDEX}/_delete_by_query?conflicts=proceed",
        {"query": {"range": {
            "@timestamp": {"lt": f"now-{RETENTION_DAYS}d"}}}})
    if code == 200 and body.get("deleted"):
        log(f"pruned {body['deleted']} digest documents older than "
            f"{RETENTION_DAYS}d")


def main():
    log(f"writing readable anomalies to {DIGEST_INDEX} every {INTERVAL}s "
        f"(lookback={LOOKBACK}, grade>{GRADE_FLOOR})")
    ticks = 0
    while True:
        started = time.time()
        try:
            catalog = detector_catalog()
            if catalog:
                n = harvest(catalog)
                if n:
                    log(f"upserted {n} anomaly digest documents")
            ticks += 1
            if ticks % 60 == 0:
                prune()
        except Exception as e:
            log(f"cycle failed: {e}")
        time.sleep(max(5, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
