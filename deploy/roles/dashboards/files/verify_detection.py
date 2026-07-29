import json
import os
import sys
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
EXPECTED_MONITORS = int(os.environ.get("EXPECTED_MONITORS", "22"))
EXPECTED_DETECTORS = int(os.environ.get("EXPECTED_DETECTORS", "17"))
ROLLUP_INDEX = os.environ.get("ROLLUP_INDEX", "trend-rollup")

EXPECTED_MONITOR_NAMES = {
    "collector-down", "collector-unhealthy", "cluster-red", "shards-stuck",
    "data-loss", "shipping-breaking", "disk-cliff-warn", "disk-cliff-page",
    "heap-spiral", "telemetry-silence", "ad-high-grade",
    "trend-il-volume", "trend-il-ef", "trend-il-entry-lag", "trend-il-shipping-lag",
    "trend-other-volume", "trend-other-errors",
    "trend-info-volume", "trend-info-entry-lag", "trend-info-shipping-lag",
    "trend-rollup-stale", "trend-entity-cap",
}
TREND_MONITOR_NAMES = {
    n for n in EXPECTED_MONITOR_NAMES if n.startswith("trend-")}
EXPECTED_DETECTOR_NAMES = {
    "ingest-flow", "node-health", "dashboards-health",
    "il-per-epn", "il-per-epn-slow",
    "il-per-epn-entry-lag", "il-per-epn-entry-lag-slow",
    "il-collector-shipping-lag", "il-collector-shipping-lag-slow",
    "other-per-epn", "other-per-epn-slow",
    "info-volume", "info-volume-slow",
    "info-per-epn-entry-lag", "info-per-epn-entry-lag-slow",
    "info-collector-shipping-lag", "info-collector-shipping-lag-slow",
}
METRICS_DETECTORS = {"ingest-flow", "node-health", "dashboards-health"}
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

    sink_id = os.environ.get("ALERT_SINK_ID", "alice-incluster-alert-sink")
    code, sink = req("GET", f"/_plugins/_notifications/configs/{sink_id}")
    sink_ok = False
    if code == 200:
        for cfg in sink.get("config_list") or []:
            if cfg.get("config_id") == sink_id:
                sink_ok = True
                break
    if sink_ok:
        print(f"[verify-detection] notification channel present: {sink_id}")
    else:
        errors.append(f"missing notification channel: {sink_id}")

    _, mon = req("POST", "/_plugins/_alerting/monitors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    monitors = mon.get("hits", {}).get("hits", [])
    mon_names = set()
    throttle_missing = []
    wrong_source = []
    for h in monitors:
        src = h.get("_source", {})
        mon_obj = src.get("monitor", src)
        name = mon_obj.get("name") or src.get("name")
        if name:
            mon_names.add(name)
        if name not in EXPECTED_MONITOR_NAMES:
            continue
        if name in TREND_MONITOR_NAMES:
            indices = []
            for inp in mon_obj.get("inputs") or []:
                indices.extend((inp.get("search") or {}).get("indices") or [])
            if indices != [ROLLUP_INDEX]:
                wrong_source.append(f"{name}={indices}")
            if mon_obj.get("enabled") is not True:
                errors.append(f"trend monitor {name} is disabled")
        for t in mon_obj.get("triggers") or []:
            trig = t.get("bucket_level_trigger") or t.get(
                "query_level_trigger") or t
            actions = trig.get("actions") or []
            ok_throttle = False
            for a in actions:
                thr = a.get("throttle") or {}
                if (a.get("throttle_enabled") is True
                        and thr.get("value") == 30
                        and str(thr.get("unit", "")).upper() == "MINUTES"
                        and a.get("destination_id") == sink_id):
                    ok_throttle = True
                    break
            if not ok_throttle:
                throttle_missing.append(name)
                break
    print(f"[verify-detection] monitors={sorted(mon_names)}")
    missing_m = sorted(EXPECTED_MONITOR_NAMES - mon_names)
    if missing_m:
        errors.append(f"missing monitors: {missing_m}")
    if len(mon_names) < EXPECTED_MONITORS:
        errors.append(
            f"monitor count {len(mon_names)} < {EXPECTED_MONITORS}")
    if throttle_missing:
        errors.append(
            f"monitors missing 30m throttle action on {sink_id}: "
            f"{sorted(throttle_missing)}")
    if wrong_source:
        errors.append(
            f"trend monitors not reading {ROLLUP_INDEX}: "
            f"{sorted(wrong_source)}")

    for idx in ("infologger", "generic-log-other"):
        code, body = req("GET", f"/{idx}/_mapping")
        if code != 200:
            errors.append(f"cannot read mapping for {idx}: HTTP {code}")
            continue
        props = {}
        for mapping in body.values():
            props.update(
                (mapping.get("mappings") or {}).get("properties") or {})
        missing = [f for f in ("severity_norm", "origin_host")
                   if f not in props]
        if missing:
            errors.append(
                f"{idx} mapping missing normalized fields {missing} — "
                f"infologger is dynamic:strict, so unmapped fields reject "
                f"every document")
        else:
            print(f"[verify-detection] {idx}: severity_norm + origin_host "
                  f"mapped")

    for alias in ("infologger", "generic-log-other"):
        code, body = req("GET", f"/_alias/{alias}")
        if code == 200 and body:
            backing = sorted(body)
            print(f"[verify-detection] {alias}: write alias over "
                  f"{len(backing)} backing index(es) {backing}")
        else:
            print(f"[verify-detection] WARNING: {alias} is a plain index, "
                  f"not a rollover write alias — it will still be wiped "
                  f"whole when it ages out. Convert once with "
                  f"'make deploy-migrate-rollover'")

    code, _ = req("GET", f"/{ROLLUP_INDEX}")
    if code != 200:
        errors.append(f"rollup index missing: {ROLLUP_INDEX} (HTTP {code})")
    else:
        code, hb = req(
            "POST", f"/{ROLLUP_INDEX}/_search",
            {"size": 0, "track_total_hits": True,
             "query": {"bool": {"filter": [
                 {"term": {"family": "_meta"}},
                 {"range": {"ts": {"gte": "now-40m"}}}]}}})
        fresh = (hb.get("hits", {}).get("total", {}) or {}).get("value", 0)
        print(f"[verify-detection] {ROLLUP_INDEX}: "
              f"{fresh} heartbeat buckets in the last 40m "
              f"(0 is expected on a first deploy — alice-trend-rollup "
              f"starts after this bootstrap)")
    _, det = req("POST", "/_plugins/_anomaly_detection/detectors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    detectors = det.get("hits", {}).get("hits", [])
    det_names = {}
    det_time_fields = {}
    for h in detectors:
        src = h.get("_source", {})
        name = src.get("name")
        if not name:
            continue
        det_names.setdefault(name, []).append(h.get("_id"))
        tf = src.get("time_field")
        if tf:
            det_time_fields[name] = tf
    print(f"[verify-detection] detectors={sorted(det_names)}")
    missing_d = sorted(EXPECTED_DETECTOR_NAMES - set(det_names))
    if missing_d:
        errors.append(f"missing detectors: {missing_d}")
    for name, ids in det_names.items():
        if len(ids) > 1:
            errors.append(f"duplicate detector name {name}: {ids}")
    for h in detectors:
        src = h.get("_source", {})
        name = src.get("name")
        if name not in EXPECTED_DETECTOR_NAMES:
            continue
        for fa in src.get("feature_attributes") or []:
            for agg in (fa.get("aggregation_query") or {}).values():
                pct = (agg or {}).get("percentiles")
                if not pct:
                    continue
                percents = pct.get("percents") or []
                if len(percents) != 1:
                    errors.append(
                        f"detector {name} feature {fa.get('feature_name')}: "
                        f"percents={percents} — the plugin reads only the "
                        f"FIRST percentile (ascending), so more than one "
                        f"silently yields the lowest")
                if pct.get("method"):
                    errors.append(
                        f"detector {name} feature {fa.get('feature_name')}: "
                        f"percentiles method={pct['method']!r} — only the "
                        f"default TDigest implementation is parsed")

    for name in EXPECTED_DETECTOR_NAMES:
        if name not in det_time_fields:
            continue
        want = "@timestamp" if name in METRICS_DETECTORS else "collector_time"
        got = det_time_fields[name]
        if got != want:
            errors.append(f"detector {name} time_field={got!r} want={want!r}")
        else:
            print(f"[verify-detection] detector {name}: time_field={got}")

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
