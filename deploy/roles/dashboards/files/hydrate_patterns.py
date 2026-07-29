import json
import os
import sys
import urllib.parse
import urllib.request

OSD_URL = os.environ.get("OSD_URL", "http://127.0.0.1:5602")

REQUIRED = {
    "alice-unified": ["@timestamp", "severity", "log_source", "message",
                      "host", "hostname", "system", "detector",
                      "collector_time", "ingest_lag_ms", "enter_system_lag_ms"],
    "alice-metrics": ["@timestamp", "kind", "node", "cluster_status",
                      "index_name", "docs_delta", "indexing_delta",
                      "heap_percent", "fb_up", "fb_healthy", "osd_state",
                      "output_retries_failed_delta"],
    "alice-ad-results": ["detector_id", "anomaly_grade", "confidence"],
    "alice-alerts": ["monitor_name", "state"],
}

SOFT_REQUIRED = {"alice-ad-results", "alice-alerts"}

META_FIELDS = ["_id", "_index", "_score", "_source", "_type"]

PLUGIN_SCHEMAS = {
    "alice-alerts": {
        "keyword": ["monitor_id", "monitor_name", "trigger_id", "trigger_name",
                    "state", "severity", "error_message", "execution_id",
                    "finding_ids", "related_doc_ids"],
        "date": ["start_time", "end_time", "last_notification_time",
                 "acknowledged_time"],
        "long": ["monitor_version", "schema_version"],
    },
    "alice-ad-results": {
        "keyword": ["detector_id", "task_id", "model_id", "error"],
        "date": ["data_start_time", "data_end_time", "execution_start_time",
                 "execution_end_time", "approx_anomaly_start_time"],
        "double": ["anomaly_score", "anomaly_grade", "confidence",
                   "threshold"],
        "boolean": ["is_anomaly"],
        "long": ["schema_version"],
    },
}

OSD_TYPE = {"keyword": "string", "date": "date", "double": "number",
            "long": "number", "boolean": "boolean"}


def _field(name, es_type):
    aggregatable = es_type != "text"
    return {"name": name, "type": OSD_TYPE[es_type], "esTypes": [es_type],
            "count": 0, "scripted": False, "searchable": True,
            "aggregatable": aggregatable, "readFromDocValues": True}


def _meta_field(name):
    return {"name": name, "type": "string" if name != "_score" else "number",
            "esTypes": [], "count": 0, "scripted": False,
            "searchable": name != "_source", "aggregatable": False,
            "readFromDocValues": False}


def plugin_catalog(pattern_id):
    schema = PLUGIN_SCHEMAS.get(pattern_id)
    if not schema:
        return []
    out = [_meta_field(m) for m in META_FIELDS]
    for es_type, names in schema.items():
        out.extend(_field(n, es_type) for n in names)
    return out


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
            print(f"[hydrate] pattern {pattern_id} ({title}) has no index yet "
                  f"({e}); using the declared plugin schema")
            fields = []
        else:
            raise
    names = {f["name"] for f in fields}
    grafted = [f for f in plugin_catalog(pattern_id) if f["name"] not in names]
    if grafted:
        fields = fields + grafted
        names |= {f["name"] for f in grafted}
        print(f"[hydrate] {pattern_id}: grafted "
              f"{[f['name'] for f in grafted]} from the declared plugin schema")
    missing = [f for f in required if f not in names]
    if missing:
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
