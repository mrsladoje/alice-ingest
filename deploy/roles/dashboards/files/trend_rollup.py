import json
import os
import sys
import time
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
ROLLUP_INDEX = os.environ.get("ROLLUP_INDEX", "trend-rollup")
BUCKET_SECONDS = int(os.environ.get("BUCKET_SECONDS", "600"))
SETTLE_SECONDS = int(os.environ.get("SETTLE_SECONDS", "120"))
BACKFILL_BUCKETS = int(os.environ.get("BACKFILL_BUCKETS", "3"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "500"))
MAX_ENTITIES = int(os.environ.get("MAX_ENTITIES", "2000"))
INTERVAL = int(os.environ.get("INTERVAL", str(BUCKET_SECONDS)))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
PRUNE_EVERY_SECONDS = int(os.environ.get("PRUNE_EVERY_SECONDS", "3600"))

ERROR_SEVERITIES = ["error", "fatal"]

COMBOS = [
    {
        "family": "infologger",
        "indices": "infologger",
        "entity_kind": "host",
        "entity_field": "origin_host",
    },
    {
        "family": "infologger",
        "indices": "infologger",
        "entity_kind": "node",
        "entity_field": "node",
    },
    {
        "family": "other",
        "indices": "generic-log-other",
        "entity_kind": "host",
        "entity_field": "origin_host",
    },
    {
        "family": "info",
        "indices": "generic-log-info-*",
        "entity_kind": "host",
        "entity_field": "origin_host",
    },
    {
        "family": "info",
        "indices": "generic-log-info-*",
        "entity_kind": "node",
        "entity_field": "node",
    },
]


def log(msg):
    print(f"[trend-rollup] {msg}", flush=True)


def req(method, path, payload=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(
        OS_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        log(f"{method} {path} failed: {e}")
        return 0, {}


def bulk(lines, timeout=60):
    payload = "".join(lines).encode()
    r = urllib.request.Request(
        f"{OS_URL}/_bulk", data=payload, method="POST",
        headers={"Content-Type": "application/x-ndjson"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        log(f"bulk failed: {e}")
        return 0, {}


def pct95(agg):
    if not agg:
        return None
    values = agg.get("values") or {}
    for key in ("95.0", "95"):
        if key in values:
            v = values[key]
            if v is not None and v == v:
                return round(float(v), 3)
    return None


def mean(agg):
    if not agg:
        return None
    v = agg.get("value")
    if v is None or v != v:
        return None
    return round(float(v), 3)


def slice_query(combo, start_ms, end_ms, after_key):
    composite = {
        "size": PAGE_SIZE,
        "sources": [
            {"entity": {"terms": {"field": combo["entity_field"]}}}
        ],
    }
    if after_key:
        composite["after"] = after_key
    return {
        "size": 0,
        "track_total_hits": False,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "collector_time": {
                                "gte": start_ms,
                                "lt": end_ms,
                                "format": "epoch_millis",
                            }
                        }
                    }
                ]
            }
        },
        "aggregations": {
            "ents": {
                "composite": composite,
                "aggregations": {
                    "ef": {
                        "filter": {
                            "terms": {"severity_norm": ERROR_SEVERITIES}
                        }
                    },
                    "entry_p95": {
                        "percentiles": {
                            "field": "enter_system_lag_ms",
                            "percents": [95],
                        }
                    },
                    "ship_p95": {
                        "percentiles": {
                            "field": "ingest_lag_ms",
                            "percents": [95],
                        }
                    },
                    "entry_avg": {"avg": {"field": "enter_system_lag_ms"}},
                    "ship_avg": {"avg": {"field": "ingest_lag_ms"}},
                },
            }
        },
    }


def collect(combo, start_ms, end_ms):
    entities = []
    truncated = False
    after_key = None
    while True:
        status, body = req(
            "POST", f"/{combo['indices']}/_search?ignore_unavailable=true",
            slice_query(combo, start_ms, end_ms, after_key))
        if status != 200:
            log(f"search {combo['family']}.{combo['entity_kind']} "
                f"failed: HTTP {status}")
            return None, False
        agg = (body.get("aggregations") or {}).get("ents") or {}
        buckets = agg.get("buckets") or []
        for b in buckets:
            entities.append({
                "entity": b["key"]["entity"],
                "doc_count": b.get("doc_count", 0),
                "ef_count": (b.get("ef") or {}).get("doc_count", 0),
                "p95_entry_lag_ms": pct95(b.get("entry_p95")),
                "p95_shipping_lag_ms": pct95(b.get("ship_p95")),
                "avg_entry_lag_ms": mean(b.get("entry_avg")),
                "avg_shipping_lag_ms": mean(b.get("ship_avg")),
            })
        after_key = agg.get("after_key")
        if not after_key or not buckets:
            break
        if len(entities) >= MAX_ENTITIES:
            truncated = True
            log(f"WARN {combo['family']}.{combo['entity_kind']}: entity cap "
                f"{MAX_ENTITIES} reached, remaining entities not rolled up")
            break
    return entities, truncated


def build_docs(combo, start_ms, entities, truncated):
    fleet_count = sum(e["doc_count"] for e in entities)
    fleet_ef_count = sum(e["ef_count"] for e in entities)
    entity_count = len(entities)
    lines = []
    for e in entities:
        doc = {
            "ts": start_ms,
            "family": combo["family"],
            "entity_kind": combo["entity_kind"],
            "entity": e["entity"],
            "doc_count": e["doc_count"],
            "ef_count": e["ef_count"],
            "fleet_count": fleet_count,
            "fleet_ef_count": fleet_ef_count,
            "entity_count": entity_count,
            "bucket_seconds": BUCKET_SECONDS,
        }
        for field in ("p95_entry_lag_ms", "p95_shipping_lag_ms",
                      "avg_entry_lag_ms", "avg_shipping_lag_ms"):
            if e[field] is not None:
                doc[field] = e[field]
        doc_id = (f"{combo['family']}.{combo['entity_kind']}."
                  f"{e['entity']}.{start_ms}")
        lines.append(json.dumps(
            {"index": {"_index": ROLLUP_INDEX, "_id": doc_id}}) + "\n")
        lines.append(json.dumps(doc) + "\n")

    meta = {
        "ts": start_ms,
        "family": "_meta",
        "entity_kind": "_meta",
        "entity": f"{combo['family']}.{combo['entity_kind']}",
        "doc_count": fleet_count,
        "ef_count": fleet_ef_count,
        "fleet_count": fleet_count,
        "fleet_ef_count": fleet_ef_count,
        "entity_count": entity_count,
        "truncated": truncated,
        "bucket_seconds": BUCKET_SECONDS,
    }
    meta_id = (f"_meta.{combo['family']}.{combo['entity_kind']}.{start_ms}")
    lines.append(json.dumps(
        {"index": {"_index": ROLLUP_INDEX, "_id": meta_id}}) + "\n")
    lines.append(json.dumps(meta) + "\n")
    return lines, entity_count, fleet_count


def roll_bucket(start_ms, end_ms):
    lines = []
    summary = []
    for combo in COMBOS:
        entities, truncated = collect(combo, start_ms, end_ms)
        if entities is None:
            continue
        combo_lines, n, total = build_docs(
            combo, start_ms, entities, truncated)
        lines.extend(combo_lines)
        summary.append(
            f"{combo['family']}.{combo['entity_kind']}={n}e/{total}d")
    if not lines:
        log(f"bucket {start_ms}: nothing to write")
        return
    status, body = bulk(lines)
    if status not in (200, 201):
        log(f"bucket {start_ms}: bulk HTTP {status}")
        return
    if body.get("errors"):
        failed = [
            i.get("index", {}).get("error", {}).get("reason")
            for i in body.get("items", [])
            if i.get("index", {}).get("error")
        ]
        log(f"bucket {start_ms}: {len(failed)} doc failures, "
            f"first={failed[0] if failed else None}")
    log(f"bucket {start_ms}: {' '.join(summary)}")


def prune():
    if RETENTION_DAYS <= 0:
        return
    cutoff = int((time.time() - RETENTION_DAYS * 86400) * 1000)
    status, body = req(
        "POST", f"/{ROLLUP_INDEX}/_delete_by_query"
                "?conflicts=proceed&refresh=false",
        {"query": {"range": {"ts": {"lt": cutoff,
                                    "format": "epoch_millis"}}}},
        timeout=120)
    if status != 200:
        log(f"prune failed: HTTP {status}")
        return
    deleted = body.get("deleted", 0)
    if deleted:
        log(f"pruned {deleted} rollup docs older than {RETENTION_DAYS}d")


def main():
    log(f"start index={ROLLUP_INDEX} bucket={BUCKET_SECONDS}s "
        f"settle={SETTLE_SECONDS}s backfill={BACKFILL_BUCKETS} "
        f"interval={INTERVAL}s retention={RETENTION_DAYS}d")
    last_prune = 0.0
    while True:
        started = time.time()
        if started - last_prune >= PRUNE_EVERY_SECONDS:
            last_prune = started
            try:
                prune()
            except Exception as e:
                log(f"prune raised: {e}")
        last_end = int(
            (started - SETTLE_SECONDS) // BUCKET_SECONDS) * BUCKET_SECONDS
        for i in range(BACKFILL_BUCKETS - 1, -1, -1):
            end = last_end - i * BUCKET_SECONDS
            start = end - BUCKET_SECONDS
            try:
                roll_bucket(int(start * 1000), int(end * 1000))
            except Exception as e:
                log(f"bucket {int(start * 1000)} raised: {e}")
        elapsed = time.time() - started
        time.sleep(max(5.0, INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
