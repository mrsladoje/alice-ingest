import json
import os
import sys
import urllib.request

PORT = os.environ.get("FB_HTTP_PORT", "2020")
COLLECTOR_ID = os.environ.get("COLLECTOR_ID", "")
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT = float(os.environ.get("FB_HEALTH_TIMEOUT", "3"))


def fetch(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except Exception:
        return 0, b""


def main():
    doc = {
        "kind": "fluentbit",
        "collector_id": COLLECTOR_ID,
        "fb_up": 1,
        "fb_healthy": 0,
    }
    if os.environ.get("FB_EMIT_LEGACY_NODE", "false").lower() == "true":
        doc["node"] = COLLECTOR_ID

    status, _ = fetch("/api/v2/health")
    doc["fb_healthy"] = 1 if status == 200 else 0

    status, body = fetch("/api/v1/metrics")
    if status == 200:
        try:
            m = json.loads(body)
        except ValueError:
            m = None
        if isinstance(m, dict):
            outs = list((m.get("output") or {}).values())
            doc["input_records"] = sum(
                v.get("records", 0) for v in (m.get("input") or {}).values())
            doc["output_records"] = sum(
                v.get("proc_records", 0) for v in outs)
            doc["output_errors"] = sum(v.get("errors", 0) for v in outs)
            doc["output_retries"] = sum(v.get("retries", 0) for v in outs)
            doc["output_retries_failed"] = sum(
                v.get("retries_failed", 0) for v in outs)
            doc["output_dropped"] = sum(
                v.get("dropped_records", 0) for v in outs)

    sys.stdout.write(json.dumps(doc) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
