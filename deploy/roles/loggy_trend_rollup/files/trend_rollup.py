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
SILENCE_MEMORY_SECONDS = int(os.environ.get("SILENCE_MEMORY_SECONDS", "86400"))

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
        "family": "central",
        "indices": "application-logs-central",
        "entity_kind": "host",
        "entity_field": "origin_host",
    },
    {
        "family": "local",
        "indices": "application-logs-local-*",
        "entity_kind": "host",
        "entity_field": "origin_host",
    },
    {
        "family": "local",
        "indices": "application-logs-local-*",
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


def bulk(lines, timeout=60, refresh="false"):
    payload = "".join(lines).encode()
    r = urllib.request.Request(
        f"{OS_URL}/_bulk?refresh={refresh}", data=payload, method="POST",
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


def cohort_name(combo):
    return f"{combo['family']}.{combo['entity_kind']}"


def recent_entities(combo, start_ms):
    """Entities in this cohort that really logged inside the memory window.

    Only rows carrying doc_count above zero count, so an imputed zero can
    never keep an entity in its own roster and impute itself forever.
    """
    if SILENCE_MEMORY_SECONDS <= 0:
        return set()
    payload = {
        "size": 0,
        "track_total_hits": False,
        "query": {"bool": {"filter": [
            {"term": {"family": combo["family"]}},
            {"term": {"entity_kind": combo["entity_kind"]}},
            {"range": {
                "ts": {"gte": start_ms - SILENCE_MEMORY_SECONDS * 1000,
                       "lt": start_ms,
                       "format": "epoch_millis"}}},
            {"range": {"doc_count": {"gt": 0}}},
        ]}},
        "aggregations": {
            "ents": {"terms": {"field": "entity", "size": MAX_ENTITIES}}},
    }
    status, body = req(
        "POST", f"/{ROLLUP_INDEX}/_search?ignore_unavailable=true", payload)
    if status != 200:
        log(f"WARN {cohort_name(combo)}: silence roster query failed with "
            f"HTTP {status} — this bucket imputes no zero rows, so a host "
            f"that just went quiet stays invisible to the share monitors "
            f"until the next bucket")
        return None
    agg = (body.get("aggregations") or {}).get("ents") or {}
    return {b["key"] for b in agg.get("buckets") or []}


def impute_silent(combo, start_ms, entities):
    """Write doc_count 0 rows for entities that logged recently but not now.

    The share monitors aggregate per entity. A host with no row at all
    contributes no fleet_count either, so its slice looks identical to the
    whole family stopping and the monitor cannot separate the two. A zero row
    carrying the live fleet_count makes one silent host a real share of 0.

    Deliberately does nothing when the cohort collected nothing: that is the
    whole family stopping, which log-family-silence reports as one alert
    instead of one per host.
    """
    if not entities:
        return entities, 0, False
    roster = recent_entities(combo, start_ms)
    absent = sorted(roster - {e["entity"] for e in entities}) if roster else []
    if not absent:
        return entities, 0, False
    room = max(0, MAX_ENTITIES - len(entities))
    over_cap = len(absent) > room
    if over_cap:
        log(f"WARN {cohort_name(combo)}: {len(absent)} silent entities but "
            f"room for {room} under the {MAX_ENTITIES} entity cap — the "
            f"remainder is unmonitored and this bucket cannot commit complete")
        absent = absent[:room]
    filled = list(entities)
    for name in absent:
        filled.append({
            "entity": name,
            "doc_count": 0,
            "ef_count": 0,
            "p95_entry_lag_ms": None,
            "p95_shipping_lag_ms": None,
            "avg_entry_lag_ms": None,
            "avg_shipping_lag_ms": None,
            "imputed": True,
        })
    return filled, len(absent), over_cap


def bulk_failures(status, body):
    if status not in (200, 201):
        return [{"reason": f"bulk HTTP {status}"}]
    return [
        (i.get("index") or {})
        for i in body.get("items", [])
        if (i.get("index") or {}).get("error")
        or int((i.get("index") or {}).get("status", 500)) >= 300
    ]


def entity_docs(combo, start_ms, entities):
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
        if e.get("imputed"):
            doc["imputed"] = True
        doc_id = (f"{combo['family']}.{combo['entity_kind']}."
                  f"{e['entity']}.{start_ms}")
        lines.append(json.dumps(
            {"index": {"_index": ROLLUP_INDEX, "_id": doc_id}}) + "\n")
        lines.append(json.dumps(doc) + "\n")

    return lines, entity_count, fleet_count, fleet_ef_count


def meta_docs(combo, start_ms, entity_count, fleet_count, fleet_ef_count,
              truncated, imputed_count=0):
    # cohort_family and cohort_kind carry what family and entity_kind cannot:
    # those two are pinned to _meta so the metadata lane stays out of every
    # per-family query. log-family-silence keys on these instead, which is how
    # it can say "the rollup is alive and this family reported nothing" rather
    # than "these rows are missing", a sentence a dead rollup also satisfies.
    meta = {
        "ts": start_ms,
        "family": "_meta",
        "entity_kind": "_meta",
        "entity": cohort_name(combo),
        "cohort_family": combo["family"],
        "cohort_kind": combo["entity_kind"],
        "doc_count": fleet_count,
        "ef_count": fleet_ef_count,
        "fleet_count": fleet_count,
        "fleet_ef_count": fleet_ef_count,
        "entity_count": entity_count,
        "imputed_count": imputed_count,
        "truncated": truncated,
        "bucket_seconds": BUCKET_SECONDS,
    }
    meta_id = f"_meta.{combo['family']}.{combo['entity_kind']}.{start_ms}"
    return [
        json.dumps({"index": {"_index": ROLLUP_INDEX, "_id": meta_id}}) + "\n",
        json.dumps(meta) + "\n",
    ]


def commit_docs(start_ms, stats, complete, truncated):
    commit = {
        "ts": start_ms,
        "family": "_commit",
        "entity_kind": "_commit",
        "entity": "global",
        "complete": complete,
        "truncated": truncated,
        "expected_cohorts": [cohort_name(c) for c in COMBOS],
        "expected_cohort_count": len(COMBOS),
        "observed_cohort_count": len(stats),
        "commit_cohorts": stats,
        "entity_count": sum(s["entity_count"] for s in stats),
        "doc_count": sum(s["doc_count"] for s in stats),
        "committed_at": int(time.time() * 1000),
        "bucket_seconds": BUCKET_SECONDS,
    }
    commit_id = f"_commit.{start_ms}"
    return [
        json.dumps(
            {"index": {"_index": ROLLUP_INDEX, "_id": commit_id}}) + "\n",
        json.dumps(commit) + "\n",
    ]


def refresh_rollup():
    status, _ = req("POST", f"/{ROLLUP_INDEX}/_refresh", timeout=60)
    return status == 200


def roll_bucket(start_ms, end_ms):
    collected = []
    for combo in COMBOS:
        entities, truncated = collect(combo, start_ms, end_ms)
        if entities is None:
            log(f"bucket {start_ms}: cohort {cohort_name(combo)} query failed "
                f"— abandoning this bucket attempt, no metadata and no commit "
                f"are published, and the existing three-bucket backfill will "
                f"retry it deterministically")
            return False
        entities, imputed, over_cap = impute_silent(combo, start_ms, entities)
        collected.append((combo, entities, truncated or over_cap, imputed))

    entity_lines = []
    stats = []
    summary = []
    for combo, entities, truncated, imputed in collected:
        lines, n, total, ef_total = entity_docs(combo, start_ms, entities)
        entity_lines.extend(lines)
        stats.append({
            "cohort": cohort_name(combo),
            "entity_count": n,
            "doc_count": total,
            "truncated": truncated,
        })
        silent = f"/{imputed}z" if imputed else ""
        summary.append(f"{cohort_name(combo)}={n}e{silent}/{total}d")

    if entity_lines:
        failures = bulk_failures(*bulk(entity_lines))
        if failures:
            log(f"bucket {start_ms}: {len(failures)} entity documents "
                f"rejected, first={json.dumps(failures[0])[:200]} — a partial "
                f"bulk is a failed attempt, so no metadata and no commit are "
                f"published")
            return False

    meta_lines = []
    for (combo, entities, truncated, imputed), stat in zip(collected, stats):
        fleet_ef = sum(e["ef_count"] for e in entities)
        meta_lines.extend(meta_docs(
            combo, start_ms, stat["entity_count"], stat["doc_count"],
            fleet_ef, truncated, imputed))
    failures = bulk_failures(*bulk(meta_lines))
    if failures:
        log(f"bucket {start_ms}: {len(failures)} metadata rows rejected, "
            f"first={json.dumps(failures[0])[:200]} — no commit published")
        return False

    if not refresh_rollup():
        log(f"bucket {start_ms}: refresh failed — no commit published, "
            f"because a commit must never be searchable before the rows it "
            f"claims are complete")
        return False

    truncated_any = any(s["truncated"] for s in stats)
    complete = not truncated_any
    failures = bulk_failures(
        *bulk(commit_docs(start_ms, stats, complete, truncated_any)))
    if failures:
        log(f"bucket {start_ms}: commit rejected, "
            f"first={json.dumps(failures[0])[:200]}")
        return False
    state = "complete" if complete else "INCOMPLETE (truncated cohort)"
    log(f"bucket {start_ms}: {' '.join(summary)} -> {state} commit")
    return complete


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
