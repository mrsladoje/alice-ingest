import hashlib
import json
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 60
SHARD_DOC = "_shard_doc"


class CursorError(Exception):
    pass


def request(os_url, method, path, payload=None, raw=None,
            ctype="application/json", timeout=DEFAULT_TIMEOUT):
    if raw is not None:
        body = raw.encode()
    elif payload is not None:
        body = json.dumps(payload).encode()
    else:
        body = None
    headers = {"Content-Type": ctype} if body else {}
    req = urllib.request.Request(
        os_url + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def source_uid(index, doc_id):
    return hashlib.sha1(
        f"{len(index)}:{index}:{doc_id}".encode("utf-8")).hexdigest()


def content_hash(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def index_exists(os_url, target):
    code, _ = request(
        os_url, "GET",
        f"/{target}?ignore_unavailable=true&allow_no_indices=true",
        timeout=15)
    return code == 200


def open_pit(os_url, target, keep_alive="2m"):
    code, body = request(
        os_url, "POST",
        f"/{target}/_search/point_in_time"
        f"?keep_alive={keep_alive}&allow_partial_pit_creation=false"
        f"&expand_wildcards=open",
        timeout=30)
    if code != 200:
        raise CursorError(
            f"cannot open point-in-time on {target}: HTTP {code} "
            f"{json.dumps(body)[:300]}")
    pit = body.get("pit_id") or body.get("id")
    if not pit:
        raise CursorError(f"point-in-time on {target} returned no id")
    return pit


def close_pit(os_url, pit):
    if not pit:
        return
    request(os_url, "DELETE", "/_search/point_in_time",
            {"pit_id": [pit]}, timeout=15)


def scan(os_url, target, query, sort_field, source=None, page=500,
         keep_alive="2m", after=None):
    pit = open_pit(os_url, target, keep_alive)
    cursor = list(after) if after else None
    try:
        while True:
            payload = {
                "size": page,
                "track_total_hits": False,
                "query": query,
                "sort": [{sort_field: "asc"}, {SHARD_DOC: "asc"}],
                "pit": {"id": pit, "keep_alive": keep_alive},
            }
            if source is not None:
                payload["_source"] = source
            if cursor:
                payload["search_after"] = cursor
            code, body = request(os_url, "POST", "/_search", payload)
            if code != 200:
                raise CursorError(
                    f"search over {target} failed: HTTP {code} "
                    f"{json.dumps(body)[:300]}")
            pit = body.get("pit_id") or pit
            hits = ((body.get("hits") or {}).get("hits") or [])
            if not hits:
                return
            yield hits
            cursor = hits[-1].get("sort")
            if not cursor or len(hits) < page:
                return
    finally:
        close_pit(os_url, pit)


def bulk(os_url, lines, refresh="false", timeout=120):
    if not lines:
        return 0, []
    code, body = request(
        os_url, "POST", f"/_bulk?refresh={refresh}",
        raw="\n".join(lines) + "\n",
        ctype="application/x-ndjson", timeout=timeout)
    if code != 200:
        raise CursorError(f"bulk failed: HTTP {code} {json.dumps(body)[:300]}")
    failures = []
    for item in body.get("items") or []:
        op = item.get("index") or item.get("create") or item.get("update") or {}
        if op.get("error") or int(op.get("status", 500)) >= 300:
            failures.append(op)
    return len(lines) // 2 - len(failures), failures


def read_watermark(os_url, index, lane):
    code, body = request(os_url, "GET", f"/{index}/_doc/{lane}", timeout=20)
    if code != 200:
        return None
    src = body.get("_source") or {}
    value = src.get("watermark_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def write_watermark(os_url, index, lane, watermark_ms, note=""):
    doc = {
        "lane": lane,
        "watermark_ms": int(watermark_ms),
        "updated_at": int(time.time() * 1000),
    }
    if note:
        doc["note"] = note
    code, body = request(
        os_url, "PUT", f"/{index}/_doc/{lane}?refresh=true", doc, timeout=20)
    if code not in (200, 201):
        raise CursorError(
            f"cannot persist watermark {lane}: HTTP {code} "
            f"{json.dumps(body)[:200]}")
