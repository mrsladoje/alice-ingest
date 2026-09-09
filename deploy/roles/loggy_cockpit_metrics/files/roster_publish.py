import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os_cursor  # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
ROSTER_INDEX = os.environ.get("ROSTER_INDEX", "cockpit-fleet")
CLUSTER_ID = os.environ.get("CLUSTER_ID", "alice-logs")
COLLECTORS = [c for c in os.environ.get("COLLECTORS", "").split(",") if c]
ASSIGNMENTS_JSON = os.environ.get("ASSIGNMENTS", "[]")


def log(msg):
    print(f"[roster] {msg}", flush=True)


def canonical_assignments(raw):
    out = []
    for row in raw:
        origin = str(row.get("origin_host", "")).strip()
        collector = str(row.get("collector_id", "")).strip()
        if not origin or not collector:
            continue
        out.append({"origin_host": origin, "collector_id": collector})
    out.sort(key=lambda r: (r["origin_host"], r["collector_id"]))
    return out


def main():
    try:
        assignments = canonical_assignments(json.loads(ASSIGNMENTS_JSON))
    except ValueError as e:
        log(f"FATAL: ASSIGNMENTS is not valid JSON: {e}")
        return 1
    collectors = sorted(set(COLLECTORS))
    if not collectors:
        log("FATAL: no collectors supplied — the roster is what tells the "
            "absence monitor who should be heartbeating, so an empty one is "
            "never a valid publication")
        return 1

    unknown = sorted({a["collector_id"] for a in assignments}
                     - set(collectors))
    if unknown:
        log(f"FATAL: assignments reference collectors outside the roster: "
            f"{unknown}")
        return 1

    version = os_cursor.content_hash({
        "cluster_id": CLUSTER_ID,
        "collectors": collectors,
        "assignments": assignments,
    })

    code, body = os_cursor.request(
        OS_URL, "POST",
        f"/{ROSTER_INDEX}/_search?ignore_unavailable=true"
        f"&allow_no_indices=true",
        {"size": 1,
         "sort": [{"effective_from": "desc"}],
         "query": {"bool": {"filter": [{"term": {"doc_kind": "roster"}}]}}})
    if code != 200:
        log(f"FATAL: cannot read the current roster: HTTP {code} "
            f"{json.dumps(body)[:300]}")
        return 1
    hits = ((body.get("hits") or {}).get("hits") or [])
    latest = (hits[0].get("_source") or {}) if hits else {}

    if latest.get("topology_version") == version:
        log(f"topology unchanged — the effective snapshot is already "
            f"topology_version={version} with effective_from="
            f"{latest.get('effective_from')}; nothing appended, nothing "
            f"rewritten")
        return 0

    now_ms = int(time.time() * 1000)
    doc = {
        "doc_kind": "roster",
        "topology_version": version,
        "effective_from": now_ms,
        "published_at": now_ms,
        "cluster_id": CLUSTER_ID,
        "collectors": collectors,
        "collector_count": len(collectors),
        "assignments": assignments,
        "assignment_count": len(assignments),
        "supersedes": latest.get("topology_version", ""),
        "published_by": "ansible",
    }
    snapshot_id = f"{now_ms}-{version}"
    code, body = os_cursor.request(
        OS_URL, "PUT",
        f"/{ROSTER_INDEX}/_create/{snapshot_id}?refresh=true", doc)
    if code in (200, 201):
        log(f"appended snapshot {snapshot_id} topology_version={version} "
            f"collectors={len(collectors)} assignments={len(assignments)} "
            f"effective_from={now_ms} "
            f"supersedes={latest.get('topology_version') or '(none)'}")
        return 0
    log(f"FATAL: could not publish roster: HTTP {code} "
        f"{json.dumps(body)[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
