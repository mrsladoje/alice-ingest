import json
import os
import sys
import time
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
OSD_URL = os.environ.get("OSD_URL", "http://127.0.0.1:5602")
FB_TARGETS = [
    t.split("=", 1) for t in os.environ.get("FB_TARGETS", "").split(",") if t
]
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
MAX_BULK_FAILURES = int(os.environ.get("MAX_BULK_FAILURES", "20"))

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
        docs.append({
            "kind": "node",
            "node": name,
            "heap_percent": n.get("jvm", {}).get("mem", {}).get(
                "heap_used_percent", 0),
            "cpu_percent": n.get("os", {}).get("cpu", {}).get("percent", 0),
            "disk_used_percent": used_pct,
            "docs_count": n.get("indices", {}).get("docs", {}).get("count", 0),
            "store_bytes": n.get("indices", {}).get("store", {}).get(
                "size_in_bytes", 0),
            "indexing_total": indexing,
            "indexing_delta": delta(("node", name), indexing),
        })
    return docs


def fluentbit_docs():
    docs = []
    for node, url in FB_TARGETS:
        m = get_json(f"{url}/api/v1/metrics", timeout=5)
        if m is None:
            docs.append({"kind": "fluentbit", "node": node, "fb_up": 0,
                         "fb_healthy": 0})
            continue
        health_status, _ = fetch(f"{url}/api/v2/health", timeout=5)
        inp = sum(v.get("records", 0) for v in m.get("input", {}).values())
        outs = m.get("output", {}).values()
        out = sum(v.get("proc_records", 0) for v in outs)
        errors = sum(v.get("errors", 0) for v in outs)
        retries = sum(v.get("retries", 0) for v in outs)
        retries_failed = sum(v.get("retries_failed", 0) for v in outs)
        dropped = sum(v.get("dropped_records", 0) for v in outs)
        docs.append({
            "kind": "fluentbit",
            "node": node,
            "fb_up": 1,
            "fb_healthy": 1 if health_status == 200 else 0,
            "input_records": inp,
            "input_records_delta": delta(("fb_in", node), inp),
            "output_records": out,
            "output_records_delta": delta(("fb_out", node), out),
            "output_errors": errors,
            "output_errors_delta": delta(("fb_err", node), errors),
            "output_retries": retries,
            "output_retries_delta": delta(("fb_retry", node), retries),
            "output_retries_failed": retries_failed,
            "output_dropped": dropped,
            "output_dropped_delta": delta(("fb_drop", node), dropped),
        })
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


def main():
    log(f"polling every {INTERVAL}s -> {METRICS_INDEX} "
        f"(fb_targets={FB_TARGETS})")
    while True:
        started = time.time()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        docs = (cluster_docs() + index_docs() + node_docs()
                + fluentbit_docs() + osd_docs())
        push(docs, ts)
        time.sleep(max(1, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
