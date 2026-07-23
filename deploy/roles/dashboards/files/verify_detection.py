import json
import os
import sys
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
EXPECTED_MONITORS = int(os.environ.get("EXPECTED_MONITORS", "11"))
EXPECTED_DETECTORS = int(os.environ.get("EXPECTED_DETECTORS", "10"))

EXPECTED_MONITOR_NAMES = {
    "collector-down", "collector-unhealthy", "cluster-red", "shards-stuck",
    "data-loss", "shipping-breaking", "disk-cliff-warn", "disk-cliff-page",
    "heap-spiral", "telemetry-silence", "ad-high-grade",
}
EXPECTED_DETECTOR_NAMES = {
    "ingest-flow", "node-health", "dashboards-health",
    "il-per-epn", "il-per-epn-lag", "other-per-epn", "info-volume",
    "il-per-epn-slow", "other-per-epn-slow", "info-volume-slow",
}
OK_STATES = {"RUNNING", "INIT", "INITIALIZING", "INIT_PROGRESS"}


def req(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(
        OS_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        print(f"[verify-detection] FATAL: {method} {path}: {e}")
        sys.exit(1)


def main():
    errors = []

    _, mon = req("POST", "/_plugins/_alerting/monitors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    monitors = mon.get("hits", {}).get("hits", [])
    mon_names = set()
    for h in monitors:
        src = h.get("_source", {})
        name = src.get("monitor", {}).get("name") or src.get("name")
        if name:
            mon_names.add(name)
    print(f"[verify-detection] monitors={sorted(mon_names)}")
    missing_m = sorted(EXPECTED_MONITOR_NAMES - mon_names)
    if missing_m:
        errors.append(f"missing monitors: {missing_m}")
    if len(mon_names) < EXPECTED_MONITORS:
        errors.append(
            f"monitor count {len(mon_names)} < {EXPECTED_MONITORS}")

    _, det = req("POST", "/_plugins/_anomaly_detection/detectors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    detectors = det.get("hits", {}).get("hits", [])
    det_names = {}
    for h in detectors:
        name = h.get("_source", {}).get("name")
        if not name:
            continue
        det_names.setdefault(name, []).append(h.get("_id"))
    print(f"[verify-detection] detectors={sorted(det_names)}")
    missing_d = sorted(EXPECTED_DETECTOR_NAMES - set(det_names))
    if missing_d:
        errors.append(f"missing detectors: {missing_d}")
    for name, ids in det_names.items():
        if len(ids) > 1:
            errors.append(f"duplicate detector name {name}: {ids}")

    for name, ids in det_names.items():
        if name not in EXPECTED_DETECTOR_NAMES:
            continue
        did = ids[0]
        code, prof = req(
            "GET", f"/_plugins/_anomaly_detection/detectors/{did}/_profile")
        if code != 200:
            errors.append(f"profile failed for {name}: HTTP {code}")
            continue
        state = str(prof.get("state", "UNKNOWN")).upper()
        err = prof.get("error")
        print(f"[verify-detection] detector {name}: state={state} "
              f"error={err!r} models={len(prof.get('models') or [])} "
              f"total_size={prof.get('total_size_in_bytes')}")
        if state in {"DISABLED", "STOPPED", "FAILED", "UNKNOWN"}:
            errors.append(f"detector {name} state={state}")
        elif state not in OK_STATES:
            errors.append(f"detector {name} unexpected state {state}")
        if err and "memory" in str(err).lower():
            errors.append(f"detector {name} memory error: {err}")

    if len(det_names) < EXPECTED_DETECTORS:
        errors.append(
            f"detector count {len(det_names)} < {EXPECTED_DETECTORS}")

    needed = [
        "alice-cockpit-metrics-retention",
        "alice-generic-info-retention",
        "alice-generic-other-retention",
        "alice-infologger-retention",
        "alice-ad-results-retention",
        "alice-alert-history-retention",
    ]
    for pid in needed:
        c, _ = req("GET", f"/_plugins/_ism/policies/{pid}")
        if c != 200:
            errors.append(f"ISM policy missing: {pid}")

    if errors:
        for e in errors:
            print(f"[verify-detection] FATAL: {e}")
        sys.exit(1)
    print("[verify-detection] OK — detection layer present")


if __name__ == "__main__":
    main()
