import json
import os
import sys
import time
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
OSD_URL = os.environ.get("OSD_URL", "http://127.0.0.1:5602")
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
ROSTER_INDEX = os.environ.get("ROSTER_INDEX", "cockpit-fleet")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
PRUNE_EVERY_SECONDS = int(os.environ.get("PRUNE_EVERY_SECONDS", "3600"))
MAX_BULK_FAILURES = int(os.environ.get("MAX_BULK_FAILURES", "20"))
HEARTBEAT_GRACE_SECONDS = int(
    os.environ.get("HEARTBEAT_GRACE_SECONDS", "90"))
EMIT_LEGACY_NODE = os.environ.get(
    "EMIT_LEGACY_NODE", "false").lower() == "true"

STATE_CODES = {"green": 0, "yellow": 1, "red": 2}

_prev = {}
_bulk_failures = 0


def log(msg):
    print(f"[metrics] {msg}", flush=True)


def fetch(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def get_json(url, timeout=10):
    status, body = fetch(url, timeout)
    if status != 200:
        log(f"GET {url} failed: status={status} {body[:120]!r}")
        return None
    try:
        return json.loads(body)
    except ValueError as e:
        log(f"GET {url} bad JSON: {e}")
        return None


def delta(key, value):
    if value is None:
        return 0
    prev = _prev.get(key)
    _prev[key] = value
    if prev is None:
        return 0
    return max(0, value - prev)


def post_json(path, payload, timeout=20):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OS_URL}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        log(f"POST {path} failed: {e}")
        return 0, {}


def latest_roster():
    status, body = post_json(
        f"/{ROSTER_INDEX}/_search?ignore_unavailable=true"
        f"&allow_no_indices=true",
        {"size": 1,
         "sort": [{"effective_from": "desc"}],
         "query": {"bool": {"filter": [{"term": {"doc_kind": "roster"}}]}}})
    if status != 200:
        return None
    hits = ((body.get("hits") or {}).get("hits") or [])
    if not hits:
        return None
    return hits[0].get("_source") or None


def observed_collectors():
    status, body = post_json(
        f"/{METRICS_INDEX}/_search?ignore_unavailable=true"
        f"&allow_no_indices=true",
        {"size": 0,
         "track_total_hits": False,
         "query": {"bool": {"filter": [
             {"term": {"kind": "fluentbit"}},
             {"range": {"@timestamp": {
                 "gte": f"now-{HEARTBEAT_GRACE_SECONDS}s"}}}]}},
         "aggregations": {"seen": {
             "terms": {"field": "collector_id", "size": 10000},
             "aggregations": {"last": {"max": {"field": "@timestamp"}}}}}})
    if status != 200:
        return None
    buckets = (((body.get("aggregations") or {}).get("seen") or {})
               .get("buckets") or [])
    return {b["key"]: (b.get("last") or {}).get("value") for b in buckets}


def fleet_docs(now_ms):
    roster = latest_roster()
    if not roster:
        log(f"no roster snapshot in {ROSTER_INDEX} — publishing no absence "
            f"documents rather than guessing who should be heartbeating; "
            f"collector-down stays silent until the deploy publishes one")
        return []
    seen = observed_collectors()
    if seen is None:
        log("heartbeat aggregation failed; skipping absence documents this "
            "tick rather than manufacturing a fleet-wide outage")
        return []
    collectors = roster.get("collectors") or []
    version = roster.get("topology_version", "none")
    missing_total = sum(1 for c in collectors if c not in seen)
    docs = []
    for collector in collectors:
        last = seen.get(collector)
        doc = {
            "kind": "fleet",
            "collector_id": collector,
            "heartbeat_missing": 0 if collector in seen else 1,
            "topology_version": version,
            "roster_size": len(collectors),
            "roster_missing": missing_total,
        }
        if last:
            doc["heartbeat_age_ms"] = max(0, now_ms - int(last))
        docs.append(doc)
    return docs


def cluster_docs():
    h = get_json(f"{OS_URL}/_cluster/health")
    if h is None:
        return []
    status = h.get("status", "unknown")
    return [{
        "kind": "cluster",
        "cluster_status": status,
        "cluster_status_code": STATE_CODES.get(status, 3),
        "nodes_count": h.get("number_of_nodes", 0),
        "active_shards": h.get("active_shards", 0),
        "relocating_shards": h.get("relocating_shards", 0),
        "initializing_shards": h.get("initializing_shards", 0),
        "unassigned_shards": h.get("unassigned_shards", 0),
        "pending_tasks": h.get("number_of_pending_tasks", 0),
    }]


def index_docs():
    rows = get_json(f"{OS_URL}/_cat/indices?format=json&bytes=b")
    if rows is None:
        return []
    stats = get_json(f"{OS_URL}/_stats/indexing?level=indices") or {}
    ops = {name: s.get("primaries", {}).get("indexing", {}).get(
        "index_total", 0) for name, s in stats.get("indices", {}).items()}
    docs = []
    for row in rows:
        name = row.get("index", "")
        if name.startswith(".") or name.startswith("top_queries-"):
            continue
        count = int(row.get("docs.count") or 0)
        indexing = ops.get(name, 0)
        docs.append({
            "kind": "index",
            "index_name": name,
            "index_health": row.get("health", "unknown"),
            "pri": int(row.get("pri") or 0),
            "rep": int(row.get("rep") or 0),
            "docs_count": count,
            "docs_delta": delta(("index", name), count),
            "store_bytes": int(row.get("store.size") or 0),
            "indexing_total": indexing,
            "indexing_delta": delta(("index_ops", name), indexing),
        })
    return docs


def node_docs():
    stats = get_json(f"{OS_URL}/_nodes/stats/jvm,os,fs,indices")
    if stats is None:
        return []
    docs = []
    for n in stats.get("nodes", {}).values():
        name = n.get("name", "unknown")
        fs = n.get("fs", {}).get("total", {})
        total = fs.get("total_in_bytes") or 0
        avail = fs.get("available_in_bytes") or 0
        used_pct = round(100 * (total - avail) / total, 1) if total else 0
        indexing = n.get("indices", {}).get("indexing", {}).get("index_total", 0)
        doc = {
            "kind": "node",
            "os_node": name,
            "heap_percent": n.get("jvm", {}).get("mem", {}).get(
                "heap_used_percent", 0),
            "cpu_percent": n.get("os", {}).get("cpu", {}).get("percent", 0),
            "disk_used_percent": used_pct,
            "docs_count": n.get("indices", {}).get("docs", {}).get("count", 0),
            "store_bytes": n.get("indices", {}).get("store", {}).get(
                "size_in_bytes", 0),
            "indexing_total": indexing,
            "indexing_delta": delta(("node", name), indexing),
        }
        if EMIT_LEGACY_NODE:
            doc["node"] = name
        docs.append(doc)
    return docs


def osd_docs():
    s = get_json(f"{OSD_URL}/api/status", timeout=5)
    if s is None:
        return [{"kind": "osd", "osd_state": "unreachable",
                 "osd_state_code": 3}]
    state = s.get("status", {}).get("overall", {}).get("state", "unknown")
    m = s.get("metrics", {})
    return [{
        "kind": "osd",
        "osd_state": state,
        "osd_state_code": STATE_CODES.get(state, 3),
        "rss_bytes": m.get("process", {}).get("memory", {}).get(
            "resident_set_size_in_bytes", 0),
        "event_loop_delay": m.get("process", {}).get("event_loop_delay", 0),
        "load_1m": m.get("os", {}).get("load", {}).get("1m", 0),
        "response_avg_ms": m.get("response_times", {}).get("avg_in_millis", 0)
        or 0,
        "response_max_ms": m.get("response_times", {}).get("max_in_millis", 0)
        or 0,
        "requests_total": m.get("requests", {}).get("total", 0),
        "concurrent_connections": m.get("concurrent_connections", 0),
    }]


def push(docs, ts):
    global _bulk_failures
    if not docs:
        log("nothing to push (all sources unreachable?)")
        return
    lines = []
    for d in docs:
        d["@timestamp"] = ts
        lines.append(json.dumps({"index": {"_index": METRICS_INDEX}}))
        lines.append(json.dumps(d))
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(
        f"{OS_URL}/_bulk", data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.load(r)
    except Exception as e:
        _bulk_failures += 1
        log(f"bulk push failed ({_bulk_failures} consecutive): {e}")
        if _bulk_failures >= MAX_BULK_FAILURES:
            log("too many consecutive bulk failures, exiting for restart")
            sys.exit(1)
        return
    if resp.get("errors"):
        failed = [i["index"] for i in resp.get("items", [])
                  if i.get("index", {}).get("status", 500) >= 300]
        _bulk_failures += 1
        first = failed[0].get("error", {}) if failed else {}
        log(f"bulk had {len(failed)}/{len(docs)} failed items "
            f"({_bulk_failures} consecutive): "
            f"{first.get('type')}: {str(first.get('reason'))[:200]}")
        if _bulk_failures >= MAX_BULK_FAILURES:
            log("too many consecutive bulk failures, exiting for restart")
            sys.exit(1)
        return
    _bulk_failures = 0
    log(f"pushed {len(docs)} docs")


def prune():
    if RETENTION_DAYS <= 0:
        return
    body = json.dumps({
        "query": {"range": {"@timestamp": {"lt": f"now-{RETENTION_DAYS}d"}}}
    }).encode()
    req = urllib.request.Request(
        f"{OS_URL}/{METRICS_INDEX}/_delete_by_query"
        "?conflicts=proceed&refresh=false",
        data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            deleted = json.load(r).get("deleted", 0)
        if deleted:
            log(f"pruned {deleted} metrics docs older than {RETENTION_DAYS}d")
    except Exception as e:
        log(f"prune failed: {e}")


def main():
    log(f"thin control-plane poller: cluster/index/node/osd every {INTERVAL}s "
        f"-> {METRICS_INDEX}, plus roster-derived collector absence from "
        f"{ROSTER_INDEX} (grace {HEARTBEAT_GRACE_SECONDS}s, retention "
        f"{RETENTION_DAYS}d). Fluent Bit telemetry is pushed by the "
        f"collectors themselves and is not scraped from here.")
    last_prune = 0.0
    while True:
        started = time.time()
        if started - last_prune >= PRUNE_EVERY_SECONDS:
            last_prune = started
            prune()
        now_ms = int(started * 1000)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        docs = (cluster_docs() + index_docs() + node_docs()
                + osd_docs() + fleet_docs(now_ms))
        push(docs, ts)
        time.sleep(max(1, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
