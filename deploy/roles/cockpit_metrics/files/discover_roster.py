import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os_cursor  # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
LOOKBACK = os.environ.get("LOOKBACK", "now-7d")
MAX_HOSTS = int(os.environ.get("MAX_HOSTS", "5000"))
TARGETS = os.environ.get(
    "TARGETS", "infologger,application-logs-central,application-logs-local-*")


def log(msg):
    print(f"[discover-roster] {msg}", file=sys.stderr, flush=True)


def main():
    query = {
        "size": 0,
        "track_total_hits": False,
        "query": {"bool": {"filter": [
            {"range": {"collector_time": {"gte": LOOKBACK}}}]}},
        "aggregations": {"hosts": {
            "terms": {"field": "origin_host", "size": MAX_HOSTS},
            "aggregations": {"collectors": {
                "terms": {"field": "node", "size": 20,
                          "order": {"_count": "desc"}}}}}},
    }
    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{TARGETS}/_search?ignore_unavailable=true&allow_no_indices=true",
        query, timeout=120)
    if code != 200:
        log(f"FATAL: search failed: HTTP {code} {json.dumps(body)[:300]}")
        return 1

    buckets = (((body.get("aggregations") or {}).get("hosts") or {})
               .get("buckets") or [])
    rows = []
    ambiguous = []
    for b in buckets:
        parents = (b.get("collectors") or {}).get("buckets") or []
        if not parents:
            continue
        if len(parents) > 1:
            ambiguous.append(
                f"{b['key']} -> {[p['key'] for p in parents]}")
        rows.append({"origin_host": b["key"],
                     "collector_id": parents[0]["key"]})
    rows.sort(key=lambda r: r["origin_host"])

    if ambiguous:
        log(f"WARNING: {len(ambiguous)} hosts were observed on more than one "
            f"collector in the window; the most frequent parent was taken. "
            f"Review before committing: {ambiguous[:5]}")
    log(f"{len(rows)} host->collector assignments observed since {LOOKBACK}")
    log("Commit this into deploy/group_vars/control.yml so the published "
        "roster stays a deterministic function of configuration — a snapshot "
        "recomputed from live data on every deploy would mint a new "
        "topology_version whenever a new EPN appeared.")
    print("roster_assignments:")
    for row in rows:
        print(f"  - origin_host: {row['origin_host']}")
        print(f"    collector_id: {row['collector_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
