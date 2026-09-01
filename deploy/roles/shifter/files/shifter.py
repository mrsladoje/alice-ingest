import base64
import gzip
import hashlib
import json
import os
import queue
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = os.environ.get("SHIFTER_BIND", "0.0.0.0")
PORT = int(os.environ.get("SHIFTER_PORT", "8092"))
TOKEN = os.environ.get("SHIFTER_TOKEN", "")
INGEST_PATH = os.environ.get("SHIFTER_INGEST_PATH", "/ingest")
REPLAY_ROWS = int(os.environ.get("SHIFTER_REPLAY_ROWS", "500"))
CLIENT_QUEUE_MAX = int(os.environ.get("SHIFTER_CLIENT_QUEUE_MAX", "2000"))
BUFFER_ROWS = int(os.environ.get("SHIFTER_BUFFER_ROWS", "10000"))
STATIC_DIR = os.environ.get(
    "SHIFTER_STATIC_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "live"))
KEEPALIVE_SECONDS = int(os.environ.get("SHIFTER_KEEPALIVE_SECONDS", "20"))
MAX_BODY_BYTES = int(os.environ.get("SHIFTER_MAX_BODY_BYTES", str(16 * 1024 * 1024)))

OS_URL = os.environ.get("SHIFTER_OS_URL", "").rstrip("/")
OS_INDICES = os.environ.get(
    "SHIFTER_OS_INDICES", "infologger,application-logs-central")
OS_USER = os.environ.get("SHIFTER_OS_USER", "")
OS_PASSWORD = os.environ.get("SHIFTER_OS_PASSWORD", "")
OS_VERIFY = os.environ.get("SHIFTER_OS_VERIFY", "true").lower() not in (
    "0", "false", "no")
OS_TIMEOUT = float(os.environ.get("SHIFTER_OS_TIMEOUT", "45"))
QUERY_MAX_ROWS = int(os.environ.get("SHIFTER_QUERY_MAX_ROWS", "20000"))
GZIP_MIN_BYTES = int(os.environ.get("SHIFTER_GZIP_MIN_BYTES", "1024"))
QUERY_PAGE_ROWS = int(os.environ.get("SHIFTER_QUERY_PAGE_ROWS", "500"))
ASSET_MAX_AGE = int(os.environ.get("SHIFTER_ASSET_MAX_AGE", "600"))

SEVERITY_NORM = {
    "I": "info", "W": "warning", "E": "error", "F": "fatal", "D": "debug",
    "Info": "info", "Warning": "warning", "Error": "error",
    "Fatal": "fatal", "Sys": "system",
    "inf": "info", "err": "error", "cout": "info",
}

KEEP_FIELDS = (
    "@timestamp", "collector_time", "severity", "severity_norm", "origin_host",
    "host", "hostname", "node", "log_source", "source_file", "source",
    "facility", "message", "rolename", "run", "partition", "detector",
    "system", "pid", "username", "level", "errcode", "errsource", "errline",
)

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

KEYWORD_TARGETS = {
    "host": ("origin_host", "hostname", "host"),
    "program": ("rolename", "source_file", "source"),
    "system": ("system",),
    "facility": ("facility",),
    "detector": ("detector",),
    "partition": ("partition",),
    "username": ("username",),
    "errsource": ("errsource",),
}

NUMBER_TARGETS = {
    "level": "level",
    "run": "run",
    "pid": "pid",
    "errcode": "errcode",
    "errline": "errline",
}

_lock = threading.Lock()
_clients = []
_recent = []
_stats = {
    "received": 0,
    "dropped_slow_client": 0,
    "posts": 0,
    "bad_posts": 0,
    "queries": 0,
    "bad_queries": 0,
}
_seq = 0
_epoch = f"{int(time.time() * 1000)}-{os.getpid()}"
_asset_cache = {}


class QueryRefused(Exception):
    pass


def log(msg):
    print(f"[live-lane] {msg}", flush=True)


class Client:
    def __init__(self):
        self.queue = queue.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.dropped = 0

    def offer(self, payload):
        while True:
            try:
                self.queue.put_nowait(payload)
                return
            except queue.Full:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    return
                self.dropped += 1
                with _lock:
                    _stats["dropped_slow_client"] += 1


def normalize(record):
    if not isinstance(record, dict):
        return None
    out = {k: record[k] for k in KEEP_FIELDS if k in record}
    severity = record.get("severity")
    if not out.get("severity_norm"):
        out["severity_norm"] = SEVERITY_NORM.get(
            severity if isinstance(severity, str) else "", "unknown")
    if not out.get("origin_host"):
        host = record.get("hostname") or record.get("host")
        if host:
            out["origin_host"] = host
    message = out.get("message")
    if message is None:
        message = record.get("log") or ""
    out["message"] = message if isinstance(message, str) else str(message)
    origin = out.get("origin_host")
    if origin:
        if out.get("hostname") == origin:
            out.pop("hostname", None)
        if out.get("host") == origin:
            out.pop("host", None)
    if out.get("collector_time") == out.get("@timestamp"):
        out.pop("collector_time", None)
    return out


def publish(records):
    global _seq
    payloads = []
    for record in records:
        normalized = normalize(record)
        if normalized is None:
            continue
        with _lock:
            _seq += 1
            normalized["_id"] = _seq
        payloads.append(json.dumps(normalized, default=str))
    if not payloads:
        return 0
    with _lock:
        _recent.extend(payloads)
        if len(_recent) > REPLAY_ROWS:
            del _recent[:len(_recent) - REPLAY_ROWS]
        _stats["received"] += len(payloads)
        targets = list(_clients)
    for client in targets:
        for payload in payloads:
            client.offer(payload)
    return len(payloads)


def decode_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_BODY_BYTES:
        return None
    body = handler.rfile.read(length)
    if (handler.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def authorized(handler):
    if not TOKEN:
        return True
    header = handler.headers.get("Authorization") or ""
    return header == f"Bearer {TOKEN}"


def split_values(raw, separator):
    if separator == "\n":
        parts = raw.replace("\r", "").split("\n")
    else:
        parts = raw.split(" ")
    return [p.strip() for p in parts if p.strip()]


def to_pattern(value):
    return value.replace("%", "*").replace("_", "?")


def keyword_clause(targets, value, mode):
    should = []
    for target in targets:
        if mode == "regex":
            should.append({"regexp": {
                target: {"value": value, "case_insensitive": True}}})
        elif "%" in value or "_" in value:
            should.append({"wildcard": {
                target: {"value": to_pattern(value),
                         "case_insensitive": True}}})
        else:
            should.append({"wildcard": {
                target: {"value": f"*{value}*", "case_insensitive": True}}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def message_clause(value, mode):
    if mode == "regex":
        return {"regexp": {
            "message.keyword": {"value": value, "case_insensitive": True}}}
    if "%" in value or "_" in value:
        return {"wildcard": {
            "message.keyword": {"value": to_pattern(value),
                                "case_insensitive": True}}}
    return {"match_phrase": {"message": value}}


def number_clause(target, value):
    values = []
    for part in split_values(value, " "):
        try:
            values.append(int(part))
        except ValueError:
            return None
    if not values:
        return None
    return {"terms": {target: values}}


def any_of(clauses):
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def build_query(criterias, mode):
    must = []
    must_not = []

    timestamp = criterias.get("timestamp") or {}
    since = timestamp.get("since") or None
    until = timestamp.get("until") or None
    if since or until:
        rng = {}
        if since:
            rng["gte"] = since
        if until:
            rng["lte"] = until
        must.append({"range": {"@timestamp": rng}})

    hide_since = timestamp.get("excludeSince") or None
    hide_until = timestamp.get("excludeUntil") or None
    if hide_since or hide_until:
        hide = {}
        if hide_since:
            hide["gte"] = hide_since
        if hide_until:
            hide["lte"] = hide_until
        must_not.append({"range": {"@timestamp": hide}})

    severity = (criterias.get("severity") or {}).get("in") or []
    if severity:
        must.append({"terms": {"severity_norm": list(severity)}})

    level_max = (criterias.get("level") or {}).get("max")
    if level_max is not None:
        must.append({"range": {"level": {"lte": int(level_max)}}})

    for field, targets in KEYWORD_TARGETS.items():
        spec = criterias.get(field) or {}
        include = (spec.get("match") or "").strip()
        exclude = (spec.get("exclude") or "").strip()
        if include:
            if mode == "regex":
                must.append(keyword_clause(targets, include, mode))
            else:
                must.append(any_of([
                    keyword_clause(targets, v, mode)
                    for v in split_values(include, " ")]))
        if exclude:
            if mode == "regex":
                must_not.append(keyword_clause(targets, exclude, mode))
            else:
                for v in split_values(exclude, " "):
                    must_not.append(keyword_clause(targets, v, mode))

    for field, target in NUMBER_TARGETS.items():
        spec = criterias.get(field) or {}
        include = (spec.get("match") or "").strip()
        exclude = (spec.get("exclude") or "").strip()
        if include:
            clause = number_clause(target, include)
            if clause is not None:
                must.append(clause)
        if exclude:
            clause = number_clause(target, exclude)
            if clause is not None:
                must_not.append(clause)

    spec = criterias.get("message") or {}
    include = (spec.get("match") or "").strip()
    exclude = (spec.get("exclude") or "").strip()
    if include:
        if mode == "regex":
            must.append(message_clause(include, mode))
        else:
            must.append(any_of([
                message_clause(v, mode)
                for v in split_values(include, "\n")]))
    if exclude:
        if mode == "regex":
            must_not.append(message_clause(exclude, mode))
        else:
            for v in split_values(exclude, "\n"):
                must_not.append(message_clause(v, mode))

    if not must and not must_not:
        return {"match_all": {}}
    body = {}
    if must:
        body["filter"] = must
    if must_not:
        body["must_not"] = must_not
    return {"bool": body}


def describe_query(criterias, mode, limit):
    parts = []
    timestamp = criterias.get("timestamp") or {}
    if timestamp.get("since"):
        parts.append(f"@timestamp >= {timestamp['since']}")
    if timestamp.get("until"):
        parts.append(f"@timestamp <= {timestamp['until']}")
    if timestamp.get("excludeSince") or timestamp.get("excludeUntil"):
        bounds = []
        if timestamp.get("excludeSince"):
            bounds.append(f">= {timestamp['excludeSince']}")
        if timestamp.get("excludeUntil"):
            bounds.append(f"<= {timestamp['excludeUntil']}")
        parts.append("NOT (@timestamp " + " AND @timestamp ".join(bounds) + ")")
    severity = (criterias.get("severity") or {}).get("in") or []
    if severity:
        parts.append("severity_norm in (" + ", ".join(severity) + ")")
    level_max = (criterias.get("level") or {}).get("max")
    if level_max is not None:
        parts.append(f"level <= {level_max}")
    for field in list(KEYWORD_TARGETS) + list(NUMBER_TARGETS) + ["message"]:
        spec = criterias.get(field) or {}
        if (spec.get("match") or "").strip():
            parts.append(f"{field} ~ {spec['match'].strip()!r}")
        if (spec.get("exclude") or "").strip():
            parts.append(f"{field} !~ {spec['exclude'].strip()!r}")
    where = " AND ".join(parts) if parts else "everything"
    return f"[{mode}] {OS_INDICES} WHERE {where} ORDER BY @timestamp DESC LIMIT {limit}"


def opensearch_search(body):
    url = f"{OS_URL}/{OS_INDICES}/_search"
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    if OS_USER:
        raw = f"{OS_USER}:{OS_PASSWORD}".encode("utf-8")
        request.add_header(
            "Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    context = None
    if url.startswith("https") and not OS_VERIFY:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=OS_TIMEOUT,
                                context=context) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def scans_the_term_dictionary(criterias, mode):
    spec = criterias.get("message") or {}
    include = (spec.get("match") or "").strip()
    exclude = (spec.get("exclude") or "").strip()
    if not include and not exclude:
        return False
    if mode == "regex":
        return True
    return any("%" in v or "_" in v for v in (include, exclude))


def run_query(payload):
    criterias = payload.get("criterias") or {}
    options = payload.get("options") or {}
    mode = options.get("mode") or "wildcard"
    if mode not in ("wildcard", "regex"):
        mode = "wildcard"
    try:
        limit = int(options.get("limit") or 2000)
    except (TypeError, ValueError):
        limit = 2000
    limit = max(1, min(limit, QUERY_MAX_ROWS))
    try:
        page = int(options.get("pageSize") or QUERY_PAGE_ROWS)
    except (TypeError, ValueError):
        page = QUERY_PAGE_ROWS
    page = max(1, min(page, limit, QUERY_MAX_ROWS))
    after = options.get("after")

    since = (criterias.get("timestamp") or {}).get("since")
    if scans_the_term_dictionary(criterias, mode) and not since:
        raise QueryRefused(
            "A message search with % or a regular expression has to read every "
            "distinct message in the index, which on the full archive is a scan "
            "of tens of millions of terms. Set a time range first — the Time "
            "column has 15m, 1h, 6h, 24h and 7d — and run it again.")

    body = {
        "size": page,
        "track_total_hits": after is None and bool(since),
        "sort": [
            {"@timestamp": {"order": "desc"}},
            {"ingest_time": {"order": "desc", "missing": "_last"}},
        ],
        "query": build_query(criterias, mode),
        "_source": list(KEEP_FIELDS),
    }
    if after:
        body["search_after"] = after

    started = time.time()
    result = opensearch_search(body)
    hits = result.get("hits") or {}
    raw = hits.get("hits") or []
    rows = []
    for hit in reversed(raw):
        record = normalize(hit.get("_source") or {})
        if record is None:
            continue
        record["_id"] = hit.get("_id")
        rows.append(record)
    total = hits.get("total") or {}
    if isinstance(total, dict):
        count = total.get("value", len(rows))
        relation = total.get("relation", "eq")
    else:
        count = total
        relation = "eq"
    return {
        "rows": rows,
        "count": count,
        "countRelation": relation,
        "limit": limit,
        "pageSize": page,
        "after": raw[-1].get("sort") if raw else None,
        "hasMore": len(raw) == page,
        "took": int((time.time() - started) * 1000),
        "queryAsString": describe_query(criterias, mode, limit),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "alice-shifter"

    def log_message(self, fmt, *args):
        return

    def _accepts_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
              extra=None):
        headers = dict(extra or {})
        if len(body) >= GZIP_MIN_BYTES and self._accepts_gzip():
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
            headers.setdefault("Vary", "Accept-Encoding")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/query":
            self.query()
            return
        if path != INGEST_PATH:
            self._send(404, b"not found")
            return
        if not authorized(self):
            self._send(401, b"unauthorized")
            return
        payload = decode_body(self)
        with _lock:
            _stats["posts"] += 1
        if payload is None:
            with _lock:
                _stats["bad_posts"] += 1
            self._send(400, b"bad payload")
            return
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            with _lock:
                _stats["bad_posts"] += 1
            self._send(400, b"bad payload")
            return
        publish(payload)
        self._send(204)

    def query(self):
        if not OS_URL:
            self._json(503, {
                "error": "query mode is not configured on this lane; "
                         "SHIFTER_OS_URL is empty"})
            return
        payload = decode_body(self)
        with _lock:
            _stats["queries"] += 1
        if not isinstance(payload, dict):
            with _lock:
                _stats["bad_queries"] += 1
            self._json(400, {"error": "bad payload"})
            return
        try:
            self._json(200, run_query(payload))
        except QueryRefused as exc:
            with _lock:
                _stats["bad_queries"] += 1
            self._json(400, {"error": str(exc)})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            with _lock:
                _stats["bad_queries"] += 1
            log(f"query rejected by OpenSearch: {exc.code} {detail}")
            self._json(502, {
                "error": f"OpenSearch answered {exc.code}", "detail": detail})
        except Exception as exc:
            with _lock:
                _stats["bad_queries"] += 1
            log(f"query failed: {exc!r}")
            self._json(502, {"error": str(exc)})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            with _lock:
                body = json.dumps({
                    "ok": True,
                    "epoch": _epoch,
                    "viewers": len(_clients),
                    "buffered": len(_recent),
                    "queryConfigured": bool(OS_URL),
                    **_stats,
                }).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/stream":
            self.stream()
            return
        self.static(path)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        name = os.path.basename(path)
        if not name or name.startswith("."):
            self._send(404, b"not found")
            return
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            self._send(404, b"not found")
            return
        ext = os.path.splitext(name)[1].lower()
        try:
            stamp = os.stat(full)
            cached = _asset_cache.get(full)
            if cached and cached[0] == (stamp.st_mtime_ns, stamp.st_size):
                body, etag = cached[1], cached[2]
            else:
                with open(full, "rb") as fh:
                    body = fh.read()
                etag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
                _asset_cache[full] = ((stamp.st_mtime_ns, stamp.st_size),
                                      body, etag)
        except OSError:
            self._send(500, b"unreadable")
            return

        shell = name == "index.html"
        cache = ("no-cache" if shell
                 else f"public, max-age={ASSET_MAX_AGE}, must-revalidate")
        if len(body) >= GZIP_MIN_BYTES and self._accepts_gzip():
            etag = etag[:-1] + '-gz"'
        if (self.headers.get("If-None-Match") or "") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, body, STATIC_TYPES.get(ext, "application/octet-stream"),
                   {"Cache-Control": cache, "ETag": etag,
                    "Vary": "Accept-Encoding"})

    def stream(self):
        client = Client()
        with _lock:
            backlog = list(_recent)
            _clients.append(client)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            hello = json.dumps({"epoch": _epoch})
            self.wfile.write(f"event: hello\ndata: {hello}\n\n".encode())
            for payload in backlog:
                self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    payload = client.queue.get(timeout=KEEPALIVE_SECONDS)
                    chunk = f"data: {payload}\n\n"
                except queue.Empty:
                    chunk = ": keepalive\n\n"
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                if client in _clients:
                    _clients.remove(client)
            if client.dropped:
                log(f"viewer disconnected after dropping {client.dropped} "
                    f"records it could not keep up with")


def main():
    if not os.path.isdir(STATIC_DIR):
        log(f"FATAL: static directory {STATIC_DIR} does not exist")
        return 1
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    log(f"listening on {BIND}:{PORT}; ingest {INGEST_PATH}; "
        f"replay {REPLAY_ROWS} rows; per-viewer queue {CLIENT_QUEUE_MAX}; "
        f"query {'-> ' + OS_URL + '/' + OS_INDICES if OS_URL else 'disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
