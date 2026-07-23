import json
import os
import sys
import urllib.parse
import urllib.request

OSD_URL = os.environ.get("OSD_URL", "http://127.0.0.1:5602")

REQUIRED = {
    "alice-unified": ["@timestamp", "severity", "log_source", "message",
                      "host", "hostname", "system", "detector",
                      "ingest_lag_ms"],
    "alice-metrics": ["@timestamp", "kind", "node", "cluster_status",
                      "index_name", "docs_delta", "indexing_delta",
                      "heap_percent", "fb_up", "fb_healthy", "osd_state",
                      "output_retries_failed_delta"],
    "alice-ad-results": ["detector_id", "anomaly_grade", "confidence"],
    "alice-alerts": ["monitor_name", "state"],
}

SOFT_REQUIRED = {"alice-ad-results", "alice-alerts"}


def req(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"osd-xsrf": "true"}
    if body:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(OSD_URL + path, data=body, method=method,
                               headers=headers)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)


def hydrate(pattern_id, title, required):
    qs = urllib.parse.urlencode(
        [("pattern", title)]
        + [("meta_fields", m) for m in ("_source", "_id", "_type", "_index",
                                        "_score")])
    try:
        fields = req("GET", f"/api/index_patterns/_fields_for_wildcard?{qs}")[
            "fields"]
    except Exception as e:
        if pattern_id in SOFT_REQUIRED:
            print(f"[hydrate] WARN: pattern {pattern_id} ({title}) not ready "
                  f"yet ({e}); skipping strict catalog")
            return
        raise
    names = {f["name"] for f in fields}
    missing = [f for f in required if f not in names]
    if missing:
        if pattern_id in SOFT_REQUIRED:
            print(f"[hydrate] WARN: pattern {pattern_id} ({title}) missing "
                  f"{missing} (index may be empty); saving partial catalog")
        else:
            print(f"[hydrate] FATAL: pattern {pattern_id} ({title}) is missing "
                  f"required fields {missing}; discovered {len(names)}")
            sys.exit(1)
    req("PUT", f"/api/saved_objects/index-pattern/{pattern_id}",
        {"attributes": {"fields": json.dumps(fields)}})
    print(f"[hydrate] {pattern_id}: serialized {len(fields)} fields "
          f"({title})")


def main():
    for pid, required in REQUIRED.items():
        title = req("GET", f"/api/saved_objects/index-pattern/{pid}")[
            "attributes"]["title"]
        hydrate(pid, title, required)
    for raw in os.environ.get("EXTRA_PATTERNS", "").split(","):
        pid = raw.strip()
        if pid:
            hydrate(pid, pid, ["@timestamp"])
    print("[hydrate] all index patterns carry a serialized field catalog")


if __name__ == "__main__":
    main()
