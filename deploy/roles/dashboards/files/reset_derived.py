import json
import os
import sys
import urllib.error
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
ROLLUP_INDEX = os.environ.get("ROLLUP_INDEX", "trend-rollup")
DIGEST_INDEX = os.environ.get("DIGEST_INDEX", "alice-anomalies")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
NOTIFICATIONS_INDEX = os.environ.get(
    "NOTIFICATIONS_INDEX", "alice-notifications")
LANE_STATE_INDEX = os.environ.get("LANE_STATE_INDEX", "alice-lane-state")

LOG_FAMILIES = [f.strip() for f in
                os.environ.get("LOG_FAMILIES", "").split(",") if f.strip()]
MODE = os.environ.get("MODE", "full").strip().lower()

PURGE = [
    ".opendistro-alerting-alerts",
    ".opendistro-alerting-alert-history-*",
    ".opendistro-anomaly-results*",
]


def log(msg):
    print(f"[reset] {msg}", flush=True)


def req(method, path, payload=None, timeout=180):
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
        return 0, {"error": str(e)}


def drop(index):
    code, body = req(
        "DELETE", f"/{index}?ignore_unavailable=true&allow_no_indices=true")
    if code in (200, 404):
        log(f"dropped {index} (templates.sh recreates it)")
        return True
    log(f"WARN could not drop {index}: HTTP {code} {json.dumps(body)[:200]}")
    return False


def purge(pattern):
    code, body = req(
        "POST",
        f"/{pattern}/_delete_by_query"
        "?refresh=true&conflicts=proceed"
        "&ignore_unavailable=true&allow_no_indices=true",
        {"query": {"match_all": {}}})
    if code == 200:
        log(f"purged {body.get('deleted', 0)} documents from {pattern}")
        return True
    if code == 404:
        log(f"{pattern} does not exist yet")
        return True
    log(f"WARN could not purge {pattern}: HTTP {code} "
        f"{json.dumps(body)[:200]}")
    return False


def is_alias(name):
    code, body = req("GET", f"/_cat/aliases/{name}?h=index&format=json")
    return code == 200 and bool(body)


def drop_blocking_index(name):
    if is_alias(name):
        return True
    code, _ = req("GET", f"/{name}")
    if code != 200:
        return True
    log(f"'{name}' is a concrete index, not a rollover write alias — it was "
        f"auto-created by ingest with dynamic mapping, which is why fields "
        f"like host came out as text and broke aggregations. Dropping it so "
        f"templates.sh can take the name back")
    return drop(name)


def main():
    ok = True
    if MODE == "full":
        log("clearing everything derived from the log data being wiped")
        for family in LOG_FAMILIES:
            ok = drop_blocking_index(family) and ok
    else:
        log("clearing alerts, anomalies and trend baselines; the log data "
            "itself is left alone")
    for index in (ROLLUP_INDEX, DIGEST_INDEX, SIGNALS_INDEX,
                  INCIDENTS_INDEX, NOTIFICATIONS_INDEX, LANE_STATE_INDEX):
        ok = purge(index) and ok
    for pattern in PURGE:
        ok = purge(pattern) and ok
    log("the traversal watermarks went too, so the projector and the digest "
        "re-read their overlap window from scratch instead of skipping the "
        "results that were just deleted; the fleet roster is deliberately "
        "kept, because it is topology history, not derived state")
    log("the trend baselines went with them, so a monitor that was firing on "
        "a collapse it can no longer measure will fall silent instead of "
        "re-firing on its next run")
    log("cockpit-metrics and the anomaly model checkpoints are deliberately "
        "kept: the first is live cluster telemetry unrelated to the replay, "
        "the second would cost every detector another 32 intervals of "
        "retraining")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
