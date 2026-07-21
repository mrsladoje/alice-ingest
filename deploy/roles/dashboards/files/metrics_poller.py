import json
import os
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

STATE_CODES = {"green": 0, "yellow": 1, "red": 2}

_prev = {}


def get_json(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
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
    docs = []
    for row in rows:
        name = row.get("index", "")
        if name.startswith(".") or name.startswith("top_queries-"):
            continue
        count = int(row.get("docs.count") or 0)
        docs.append({
            "kind": "index",
            "index_name": name,
            "index_health": row.get("health", "unknown"),
            "pri": int(row.get("pri") or 0),
            "rep": int(row.get("rep") or 0),
            "docs_count": count,
            "docs_delta": delta(("index", name), count),
            "store_bytes": int(row.get("store.size") or 0),
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
        m = get_json(f"{url}/api/v1/metrics")
        if m is None:
            docs.append({"kind": "fluentbit", "node": node, "fb_up": 0})
            continue
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
    s = get_json(f"{OSD_URL}/api/status")
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
    if not docs:
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
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except Exception as e:
        print(f"[metrics] bulk push failed: {e}", flush=True)


def main():
    print(f"[metrics] polling every {INTERVAL}s -> {METRICS_INDEX} "
          f"(fb_targets={FB_TARGETS})", flush=True)
    while True:
        started = time.time()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        docs = (cluster_docs() + index_docs() + node_docs()
                + fluentbit_docs() + osd_docs())
        push(docs, ts)
        time.sleep(max(1, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
