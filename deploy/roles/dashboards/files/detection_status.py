import json
import os
import sys
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
MIN_SAMPLES = int(os.environ.get("AD_MIN_SAMPLES", "32"))


def req(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
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
        print(f"FATAL: {method} {path}: {e}")
        sys.exit(1)


def rule(title):
    print()
    print(title)
    print("-" * len(title))


def data_window(indices, time_field):
    target = ",".join(indices)
    code, body = req(
        "POST",
        f"/{target}/_search?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True,
         "aggs": {
             "lo": {"min": {"field": time_field}},
             "hi": {"max": {"field": time_field}},
             "buckets": {"cardinality": {
                 "field": time_field,
                 "precision_threshold": 40000}}}})
    if code != 200:
        return None
    total = (body.get("hits", {}).get("total") or {}).get("value", 0)
    aggs = body.get("aggregations") or {}
    lo = (aggs.get("lo") or {}).get("value")
    hi = (aggs.get("hi") or {}).get("value")
    span_min = None
    if lo and hi:
        span_min = (hi - lo) / 60000.0
    return {
        "docs": total,
        "lo": (aggs.get("lo") or {}).get("value_as_string"),
        "hi": (aggs.get("hi") or {}).get("value_as_string"),
        "span_min": span_min,
    }


def interval_str(src):
    p = (src.get("detection_interval") or {}).get("period") or {}
    n = p.get("interval", 1)
    unit = str(p.get("unit", "MINUTES")).upper()
    if unit.startswith("MIN"):
        return f"{n}m"
    if unit.startswith("HOUR"):
        return f"{n}h"
    if unit.startswith("SEC"):
        return f"{n}s"
    return f"{n}m"


def _has_value(agg):
    if not isinstance(agg, dict):
        return False
    if "value" in agg:
        return agg["value"] is not None
    if "values" in agg:
        vals = agg["values"]
        if isinstance(vals, dict):
            return bool(vals) and all(v is not None for v in vals.values())
        if isinstance(vals, list):
            return bool(vals) and all(
                v.get("value") is not None for v in vals)
    return False


def feature_coverage(src, lookback="now-2h"):
    tf = src.get("time_field")
    indices = src.get("indices") or []
    if not tf or not indices:
        return None
    feats = {}
    for fa in src.get("feature_attributes") or []:
        if fa.get("feature_enabled") is False:
            continue
        for k, v in (fa.get("aggregation_query") or {}).items():
            feats[k] = v
    if not feats:
        return None
    hist = {"date_histogram": {"field": tf, "fixed_interval": interval_str(src),
                               "min_doc_count": 0},
            "aggs": feats}
    cat = (src.get("category_field") or [None])[0]
    if cat:
        aggs = {"ent": {"terms": {"field": cat, "size": 1,
                                  "order": {"_count": "desc"}},
                        "aggs": {"per_interval": hist}}}
    else:
        aggs = {"per_interval": hist}
    q = src.get("filter_query") or {"match_all": {}}
    body = {"size": 0,
            "query": {"bool": {"filter": [
                q, {"range": {tf: {"gte": lookback}}}]}},
            "aggs": aggs}
    code, resp = req(
        "POST",
        f"/{','.join(indices)}/_search"
        "?ignore_unavailable=true&allow_no_indices=true", body)
    if code != 200:
        return {"error": f"HTTP {code}: {json.dumps(resp)[:300]}"}
    a = resp.get("aggregations") or {}
    entity = None
    if cat:
        buckets = ((a.get("ent") or {}).get("buckets") or [])
        if not buckets:
            return {"entity": None, "total": 0, "usable": 0,
                    "names": list(feats)}
        entity = buckets[0].get("key")
        slots = ((buckets[0].get("per_interval") or {}).get("buckets") or [])
    else:
        slots = ((a.get("per_interval") or {}).get("buckets") or [])
    usable = 0
    for b in slots:
        if all(_has_value(b.get(name)) for name in feats):
            usable += 1
    return {"entity": entity, "total": len(slots), "usable": usable,
            "names": list(feats)}


def results_for(detector_id):
    code, body = req(
        "POST",
        "/.opendistro-anomaly-results*/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True,
         "query": {"term": {"detector_id": detector_id}},
         "aggs": {
             "last": {"max": {"field": "data_end_time"}},
             "top_grade": {"max": {"field": "anomaly_grade"}},
             "graded": {"filter": {"range": {"anomaly_grade": {"gt": 0}}}},
             "high": {"filter": {"range": {"anomaly_grade": {"gt": 0.5}}}}}})
    if code != 200:
        return {"total": 0, "graded": 0, "high": 0, "top": None, "last": None}
    aggs = body.get("aggregations") or {}
    return {
        "total": (body.get("hits", {}).get("total") or {}).get("value", 0),
        "graded": (aggs.get("graded") or {}).get("doc_count", 0),
        "high": (aggs.get("high") or {}).get("doc_count", 0),
        "top": (aggs.get("top_grade") or {}).get("value"),
        "last": (aggs.get("last") or {}).get("value_as_string"),
    }


def detectors_report():
    rule("DETECTORS")
    _, det = req("POST", "/_plugins/_anomaly_detection/detectors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    hits = det.get("hits", {}).get("hits", [])
    if not hits:
        print("no detectors exist")
        return
    hits.sort(key=lambda h: h.get("_source", {}).get("name") or "")
    stuck = []
    for h in hits:
        src = h.get("_source", {})
        did = h.get("_id")
        name = src.get("name", "?")
        tf = src.get("time_field", "?")
        indices = src.get("indices") or []
        interval = ((src.get("detection_interval") or {}).get("period") or {})
        every = f"{interval.get('interval', '?')}{str(interval.get('unit', ''))[:3].lower()}"

        code, prof = req(
            "GET",
            f"/_plugins/_anomaly_detection/detectors/{did}/_profile/"
            "state,init_progress,total_entities,error,total_size_in_bytes")
        if code != 200:
            prof = {}
        state = prof.get("state", "UNKNOWN")
        ip = prof.get("init_progress") or {}
        pct = ip.get("percentage", "")
        needed = ip.get("needed_shingles")
        entities = prof.get("total_entities")
        err = (prof.get("error") or "").strip()

        res = results_for(did)
        win = data_window(indices, tf) if indices else None

        print(f"{name:<34} {state:<12} every {every:<5} "
              f"entities={entities if entities is not None else '-'}")
        line = f"  init={pct or 'n/a'}"
        if needed is not None:
            line += f" needed_shingles={needed}"
        line += (f"  results={res['total']} graded>0={res['graded']} "
                 f">0.5={res['high']}")
        if res["top"] is not None:
            line += f" max_grade={res['top']:.3f}"
        print(line)
        if res["last"]:
            print(f"  last result at {res['last']}")
        if win is not None:
            span = ("?" if win["span_min"] is None
                    else f"{win['span_min']:.0f}")
            print(f"  source {','.join(indices)} [{tf}]: {win['docs']} docs "
                  f"spanning {span} min ({win['lo']} .. {win['hi']})")
            if (win["span_min"] is not None
                    and win["span_min"] < MIN_SAMPLES
                    and str(interval.get("unit", "")).upper() == "MINUTES"):
                stuck.append(
                    f"{name}: only {win['span_min']:.0f} min of data, needs "
                    f"{MIN_SAMPLES} intervals to leave INIT")
        cov = feature_coverage(src)
        if cov and cov.get("error"):
            print(f"  feature query FAILS: {cov['error']}")
        elif cov:
            scope = (f"entity {cov['entity']}" if cov.get("entity")
                     else "whole stream")
            print(f"  feature coverage ({scope}, last 2h): "
                  f"{cov['usable']}/{cov['total']} intervals have a value for "
                  f"all of {cov['names']}")
        if err:
            print(f"  error: {err}")
        print()
    if stuck:
        rule("WHY DETECTORS HAVE NOT PRODUCED ANOMALIES")
        for s in stuck:
            print(f"  {s}")


def jobs_report():
    rule("ANOMALY DETECTOR JOBS (is the schedule actually running)")
    code, body = req(
        "POST",
        "/.opendistro-anomaly-detector-jobs/_search?ignore_unavailable=true",
        {"size": 200, "query": {"match_all": {}}})
    if code != 200:
        print(f"cannot read the job index: HTTP {code} — no detector has ever "
              f"been started")
        return
    hits = body.get("hits", {}).get("hits", [])
    if not hits:
        print("job index is empty — every detector is configured but STOPPED")
        return
    for h in sorted(hits, key=lambda x: (x.get("_source", {})
                                         .get("name") or "")):
        s = h.get("_source", {})
        sched = ((s.get("schedule") or {}).get("interval") or {})
        print(f"  {s.get('name', h.get('_id')):<34} enabled={s.get('enabled')} "
              f"every {sched.get('period')}{str(sched.get('unit', ''))[:3].lower()} "
              f"enabled_at={s.get('enabled_time')}")


def result_errors_report():
    rule("ANOMALY RESULTS CARRYING AN ERROR")
    code, body = req(
        "POST",
        "/.opendistro-anomaly-results*/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 20, "track_total_hits": True,
         "query": {"bool": {"must": [{"exists": {"field": "error"}}]}},
         "sort": [{"execution_end_time": "desc"}]})
    if code != 200:
        print(f"cannot read results: HTTP {code}")
        return
    total = (body.get("hits", {}).get("total") or {}).get("value", 0)
    if not total:
        print("none")
        return
    print(f"{total} result document(s) carry an error; newest first:")
    for h in body.get("hits", {}).get("hits", []):
        s = h.get("_source", {})
        print(f"  detector={s.get('detector_id')} "
              f"at={s.get('execution_end_time')}")
        print(f"    {str(s.get('error'))[:400]}")


def results_index_report():
    rule("ANOMALY RESULTS INDEX")
    code, body = req(
        "POST",
        "/.opendistro-anomaly-results*/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True,
         "aggs": {"grades": {"histogram": {
             "field": "anomaly_grade", "interval": 0.1,
             "min_doc_count": 1}}}})
    if code != 200:
        print(f"cannot read .opendistro-anomaly-results*: HTTP {code}")
        return
    total = (body.get("hits", {}).get("total") or {}).get("value", 0)
    print(f"total results: {total}")
    buckets = ((body.get("aggregations") or {}).get("grades") or {}).get(
        "buckets") or []
    if not buckets:
        print("no graded results yet (every detector is still in INIT, "
              "or none has run)")
    for b in buckets:
        print(f"  grade >= {b['key']:.1f}: {b['doc_count']}")


def alerts_report():
    rule("ALERTS")
    code, body = req(
        "POST",
        "/.opendistro-alerting-alerts*/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 50, "track_total_hits": True,
         "sort": [{"start_time": "desc"}]})
    if code != 200:
        print(f"cannot read .opendistro-alerting-alerts*: HTTP {code} "
              f"(no monitor has fired yet)")
        return
    hits = body.get("hits", {}).get("hits", [])
    total = (body.get("hits", {}).get("total") or {}).get("value", 0)
    print(f"total alert documents: {total}")
    for h in hits:
        s = h.get("_source", {})
        print(f"  [{s.get('state', '?'):<9}] {s.get('monitor_name', '?')} / "
              f"{s.get('trigger_name', '?')}  since {s.get('start_time', '?')}")


def monitors_report():
    rule("MONITOR LAST RUN")
    _, mon = req("POST", "/_plugins/_alerting/monitors/_search",
                 {"query": {"match_all": {}}, "size": 200})
    rows = []
    for h in mon.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        m = src.get("monitor", src)
        name = m.get("name")
        if not name:
            continue
        last = (m.get("last_update_time") or 0)
        rows.append((name, m.get("enabled"), last))
    for name, enabled, _ in sorted(rows):
        print(f"  {name:<28} enabled={enabled}")
    print(f"  ({len(rows)} monitors)")


def rollup_report():
    rule("TREND ROLLUP")
    code, body = req(
        "POST", "/trend-rollup/_search?ignore_unavailable=true",
        {"size": 0, "track_total_hits": True,
         "aggs": {"last": {"max": {"field": "ts"}},
                  "families": {"terms": {"field": "family", "size": 20}}}})
    if code != 200:
        print(f"trend-rollup not readable: HTTP {code}")
        return
    total = (body.get("hits", {}).get("total") or {}).get("value", 0)
    aggs = body.get("aggregations") or {}
    print(f"rows: {total}   newest ts: "
          f"{(aggs.get('last') or {}).get('value_as_string')}")
    for b in ((aggs.get("families") or {}).get("buckets") or []):
        print(f"  family {b['key']}: {b['doc_count']}")


def patterns_report():
    rule("DASHBOARDS INDEX PATTERNS (field catalog)")
    osd = os.environ.get("OSD_URL", "http://127.0.0.1:5602")
    for pid in ("alice-unified", "alice-metrics", "alice-ad-results",
                "alice-alerts"):
        try:
            r = urllib.request.Request(
                f"{osd}/api/saved_objects/index-pattern/{pid}",
                headers={"osd-xsrf": "true"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                attrs = json.load(resp).get("attributes", {})
            raw = attrs.get("fields") or "[]"
            n = len(json.loads(raw))
            flag = "" if n else "   <-- EMPTY: every field-based panel on "\
                                "this pattern will error"
            print(f"  {pid:<18} {attrs.get('title', '?'):<32} "
                  f"{n} fields{flag}")
        except Exception as e:
            print(f"  {pid:<18} ERROR: {e}")


def main():
    print(f"detection status against {OS_URL}")
    detectors_report()
    jobs_report()
    result_errors_report()
    results_index_report()
    alerts_report()
    monitors_report()
    rollup_report()
    patterns_report()


if __name__ == "__main__":
    main()
