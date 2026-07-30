import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("INGEST_PORT", "8091"))
BIND = os.environ.get("INGEST_BIND", "127.0.0.1")
OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
INDEX = os.environ.get("NOTIFICATIONS_INDEX", "alice-notifications")
CLUSTER_ID = os.environ.get("CLUSTER_ID", "alice-logs")
TOKEN = os.environ.get("INGEST_TOKEN", "")
MAX_BODY = int(os.environ.get("INGEST_MAX_BODY_BYTES", "1048576"))
TIMEOUT = int(os.environ.get("INGEST_OS_TIMEOUT", "20"))
DEDUP_WINDOW_SECONDS = int(
    os.environ.get("INGEST_DEDUP_WINDOW_SECONDS", "60"))

_counters = {"accepted": 0, "rejected": 0, "failed": 0, "documents": 0}


def log(msg):
    print(f"[notification-ingest] {msg}", flush=True)


def bulk(lines):
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(
        f"{OS_URL}/_bulk?refresh=false", data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)
    failed = [i for i in resp.get("items", [])
              if (i.get("index") or {}).get("error")
              or int((i.get("index") or {}).get("status", 500)) >= 300]
    if failed:
        return False, json.dumps(failed[0])[:300]
    return True, ""


def kv(labels):
    return [f"{k}={v}" for k, v in sorted((labels or {}).items())]


def deterministic_id(record_kind, payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    window = int(time.time()) // max(1, DEDUP_WINDOW_SECONDS)
    return f"{record_kind}:{digest}:{window}"


def notification_docs(payload):
    group_labels = payload.get("groupLabels") or {}
    common = payload.get("commonLabels") or {}
    alerts = payload.get("alerts") or []
    signal_ids = []
    incident_ids = []
    truncated = False
    for alert in alerts:
        annotations = alert.get("annotations") or {}
        for raw in str(annotations.get("signal_ids") or "").split(","):
            value = raw.strip()
            if value and value not in signal_ids:
                signal_ids.append(value)
        if str(annotations.get("signal_ids_truncated", "")).lower() == "true":
            truncated = True
        episode = str(annotations.get("episode_id") or "").strip()
        if episode and episode not in incident_ids:
            incident_ids.append(episode)
    doc = {
        "@timestamp": int(time.time() * 1000),
        "record_kind": "notification",
        "status": payload.get("status", "unknown"),
        "receiver": payload.get("receiver", "unknown"),
        "group_key": payload.get("groupKey", ""),
        "alertname": group_labels.get("alertname")
        or common.get("alertname", ""),
        "cluster_id": common.get("cluster_id", CLUSTER_ID),
        "entity_kind": common.get("entity_kind", ""),
        "notification_scope": common.get("notification_scope", ""),
        "delivery_path": "alertmanager",
        "alert_count": len(alerts),
        "labels_kv": kv(group_labels) + kv(common),
        "signal_ids": signal_ids[:500],
        "episode_ids": incident_ids[:500],
        "signal_ids_truncated": truncated,
    }
    doc_id = deterministic_id("notification", {
        "group_key": doc["group_key"],
        "status": doc["status"],
        "episode_ids": sorted(incident_ids),
        "signal_ids": sorted(signal_ids),
    })
    return doc_id, doc


def event_docs(payload):
    doc = {
        "@timestamp": int(time.time() * 1000),
        "record_kind": "event",
        "status": str(payload.get("type") or payload.get("status") or ""),
        "receiver": str(payload.get("receiver") or ""),
        "group_key": str(payload.get("groupKey") or ""),
        "cluster_id": CLUSTER_ID,
        "delivery_path": "alertmanager",
        "labels_kv": kv(payload.get("labels")),
        "body": json.dumps(payload)[:8000],
    }
    doc_id = deterministic_id("event", payload)
    return doc_id, doc


def breakglass_docs(payload):
    doc = {
        "@timestamp": int(time.time() * 1000),
        "record_kind": "notification",
        "status": "firing",
        "receiver": "breakglass",
        "group_key": f"breakglass/{payload.get('monitor', 'unknown')}",
        "alertname": str(payload.get("monitor") or "unknown"),
        "cluster_id": CLUSTER_ID,
        "entity_kind": str(payload.get("entity_kind") or ""),
        "notification_scope": str(payload.get("notification_scope") or ""),
        "delivery_path": "breakglass",
        "alert_count": 1,
        "labels_kv": kv({
            "alertname": payload.get("monitor"),
            "entity_id": payload.get("entity"),
            "severity": payload.get("severity"),
            "feed": payload.get("feed"),
        }),
        "body": json.dumps(payload)[:8000],
    }
    doc_id = deterministic_id("breakglass", {
        "monitor": payload.get("monitor"),
        "period_end": payload.get("period_end"),
        "entity": payload.get("entity"),
    })
    return doc_id, doc


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if not TOKEN:
            return True
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None, "empty body"
        if length > MAX_BODY:
            return None, f"body larger than {MAX_BODY} bytes"
        try:
            return json.loads(self.rfile.read(length)), ""
        except ValueError as e:
            return None, f"invalid JSON: {e}"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/healthz"):
            self._send(200, {"ok": True})
        elif path == "/metrics":
            self._send(200, dict(_counters))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if not self._authorized():
            _counters["rejected"] += 1
            self._send(401, {"error": "unauthorized"})
            return
        payload, err = self._read()
        if err:
            _counters["rejected"] += 1
            self._send(400, {"error": err})
            return
        if path.endswith("/notifications"):
            if isinstance(payload, dict) and "alerts" in payload:
                doc_id, doc = notification_docs(payload)
            else:
                doc_id, doc = breakglass_docs(payload)
        elif path.endswith("/events"):
            doc_id, doc = event_docs(payload)
        else:
            self._send(404, {"error": "not found"})
            return
        ok, detail = bulk([
            json.dumps({"index": {"_index": INDEX, "_id": doc_id}}),
            json.dumps(doc),
        ])
        if not ok:
            _counters["failed"] += 1
            log(f"durable write failed for {doc_id}: {detail}")
            self._send(503, {"error": "not durably accepted",
                             "detail": detail})
            return
        _counters["accepted"] += 1
        _counters["documents"] += 1
        self._send(200, {"ok": True, "id": doc_id})

    def log_message(self, fmt, *args):
        log(f"{self.address_string()} {fmt % args}")


def main():
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    log(f"receiving Alertmanager notifications on {BIND}:{PORT} -> {INDEX} "
        f"(auth={'on' if TOKEN else 'off'}, max body {MAX_BODY} bytes). "
        f"record_kind=event is audit evidence from the experimental event "
        f"recorder and is never incident truth.")
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
