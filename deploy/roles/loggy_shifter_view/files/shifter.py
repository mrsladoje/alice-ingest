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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import templates_view
    import template_contract as contract
    import semantic
    import triage
    TEMPLATES_IMPORT_ERROR = ""
except Exception as exc:                                      # noqa: BLE001
    templates_view = None
    contract = None
    semantic = None
    triage = None
    TEMPLATES_IMPORT_ERROR = repr(exc)

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

TEMPLATES_ENABLED = os.environ.get(
    "SHIFTER_TEMPLATES_ENABLED", "true").lower() not in ("0", "false", "no")
TEMPLATE_PAGE_ROWS = int(os.environ.get("SHIFTER_TEMPLATE_PAGE_ROWS", "50"))
TEMPLATE_CONCURRENT_QUERIES = int(
    os.environ.get("SHIFTER_TEMPLATE_CONCURRENT_QUERIES", "2"))
TEMPLATE_LINES_ROWS = int(
    os.environ.get("SHIFTER_TEMPLATE_LINES_ROWS", "50"))
TEMPLATE_LINES_CEILING = int(
    os.environ.get("SHIFTER_TEMPLATE_LINES_CEILING", "500"))
EPISODE_REFRESH_SECONDS = int(
    os.environ.get("SHIFTER_EPISODE_REFRESH_SECONDS", "30"))
VIEW_REFRESH_SECONDS = int(
    os.environ.get("SHIFTER_VIEW_REFRESH_SECONDS", "300"))
CATALOG_CACHE_BYTES = int(
    os.environ.get("SHIFTER_CATALOG_CACHE_BYTES", str(32 * 1024 * 1024)))
RESPONSE_CACHE_BYTES = int(
    os.environ.get("SHIFTER_RESPONSE_CACHE_BYTES", str(16 * 1024 * 1024)))
AGGREGATION_BYTES = int(
    os.environ.get("SHIFTER_AGGREGATION_BYTES", str(48 * 1024 * 1024)))
TEMPLATE_TICK_SECONDS = float(
    os.environ.get("SHIFTER_TEMPLATE_TICK_SECONDS", "5"))
VECTOR_CACHE_BYTES = int(
    os.environ.get("SHIFTER_VECTOR_CACHE_BYTES", str(16 * 1024 * 1024)))
LIVE_LANE_BYTES = int(
    os.environ.get("SHIFTER_LIVE_LANE_BYTES", str(32 * 1024 * 1024)))
PROCESS_BASE_BYTES = int(
    os.environ.get("SHIFTER_PROCESS_BASE_BYTES", str(128 * 1024 * 1024)))
MEMORY_MAX = os.environ.get("SHIFTER_MEMORY_MAX", "")
RESIDENT_VIEWS_AT_PEAK = 2

MEMORY_SUFFIXES = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
                   "T": 1024 ** 4, "KI": 1024, "MI": 1024 ** 2,
                   "GI": 1024 ** 3, "TI": 1024 ** 4}

SEMANTIC_FALLBACK_NOTE = (
    "Semantic ranking was unavailable, so these rows are text matches and "
    "every score is empty.")
NEIGHBOUR_NOTE = (
    "These are the nearest templates by vector distance. They are suggestions "
    "for a reviewer, not verified version relationships. No count, label or "
    "alert is inherited from them.")
NEIGHBOUR_UNAVAILABLE_NOTE = (
    "No nearest neighbour is available for this template, so this drawer "
    "suggests none.")

HISTORY_READY = "ready"
HISTORY_UNAVAILABLE = "unavailable"
HISTORY_UNAVAILABLE_NOTE = (
    "The ranked rows are the active templates only. The bounded search of "
    "inactive central definitions did not answer, so the historical half of "
    "this request was not run.")

# The same map the alice-add-ingest-time pipeline applies. The live lane does not
# go through OpenSearch, so it has to carry its own copy; if the two drift, a
# record reads one way in Discover and another way in the live view. Every
# severity spelling the five sources produce is here: DPL words,
# DataDistribution and InfoLogger single letters, DDS three-letter codes, ROOT
# words, and the journal's numeric priorities.
SEVERITY_NORM = {
    "I": "info", "W": "warning", "E": "error", "F": "fatal", "D": "debug",
    "T": "trace",
    "Info": "info", "Warning": "warning", "Error": "error",
    "Fatal": "fatal", "Sys": "system", "Break": "error",
    "inf": "info", "wrn": "warning", "err": "error", "fat": "fatal",
    "dbg": "debug", "cout": "info",
    "INFO": "info", "WARN": "warning", "ERROR": "error", "FATAL": "fatal",
    "DEBUG": "debug", "TRACE": "trace", "STATE": "state", "ALARM": "error",
    "0": "fatal", "1": "fatal", "2": "fatal", "3": "error", "4": "warning",
    "5": "info", "6": "info", "7": "debug",
}

KEEP_FIELDS = (
    "@timestamp", "collector_time", "severity", "severity_norm", "origin_host",
    "host", "hostname", "node", "log_source", "source_file",
    "facility", "message", "rolename", "run", "partition", "detector",
    "system", "pid", "username", "level", "errcode", "errsource", "errline",
    # The process that wrote the line, on every source that has one. Without it
    # the live view cannot tell a GPU reconstruction error from a tracker one.
    "program", "log_time", "comm", "clients", "client_limit",
    "template_version", "template_status",
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
    # `program` is a real field on every source that has one, so the search
    # target leads with it. rolename and source_file stay behind it because an
    # InfoLogger record has the first and a line no parser claimed has only the
    # second.
    "program": ("program", "rolename", "source_file"),
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
    "template_requests": 0,
    "bad_template_requests": 0,
}
_seq = 0
_epoch = f"{int(time.time() * 1000)}-{os.getpid()}"
_asset_cache = {}

TEMPLATES = None
TEMPLATES_DISABLED = "the templates page has not been started on this server"


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

    versions = (criterias.get("template_version") or {}).get("in") or []
    if versions:
        must.append({"terms": {"template_version": [str(v)
                                                    for v in versions]}})

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
    versions = (criterias.get("template_version") or {}).get("in") or []
    if versions:
        parts.append("template_version in (" + ", ".join(
            str(v) for v in versions) + ")")
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


def log_link(row):
    own = row.get("version_id")
    versions = [own] + [v for v in (row.get("descendants") or ())
                        if v != own]
    return {"criterias": {"template_version": {"in": [v for v in versions
                                                      if v]}},
            "options": {"mode": "wildcard", "limit": 2000}}


class TemplatesRuntime:
    def __init__(self, service, search, labels, queries, limits, clock=None,
                 query_runner=None):
        self._service = service
        self._search = search
        self._labels = labels
        self._queries = queries
        self.limits = limits
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._run_query = query_runner or run_query
        self._stop = threading.Event()
        self._thread = None
        self._submitted_at = None
        self.cycles = 0

    def now_ms(self):
        return int(self._clock())

    def start(self):
        if self._thread is not None:
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="templates-runtime")
        self._thread.start()
        return self._thread

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)
        try:
            self._search.stop(timeout)
        except Exception as exc:                              # noqa: BLE001
            log(f"semantic search did not stop cleanly: {exc!r}")

    def _loop(self):
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(TEMPLATE_TICK_SECONDS)

    def cycle(self):
        self.cycles += 1
        try:
            self._service.maybe_refresh()
        except Exception as exc:                              # noqa: BLE001
            log(f"template view refresh failed: {exc!r}")
        try:
            self._labels.maybe_refresh()
        except Exception as exc:                              # noqa: BLE001
            log(f"label cache refresh failed: {exc!r}")
        self._submit_corpus()

    def _submit_corpus(self):
        view = self._service.current()
        if (not view.window.get("window_end")
                or view.refreshed_at == self._submitted_at):
            return
        try:
            versions = semantic.active_versions(
                view.rows, view.cutoff or self.now_ms(),
                self._service.active_ms)
            if self._search.submit(versions):
                self._submitted_at = view.refreshed_at
        except Exception as exc:                              # noqa: BLE001
            log(f"semantic corpus was not submitted: {exc!r}")

    def semantic_status(self):
        try:
            return self._search.status()
        except Exception as exc:                              # noqa: BLE001
            return semantic.unavailable_summary(
                semantic.REASON_ENCODE_FAILED, repr(exc))

    def summary(self):
        payload = self._service.summary()
        payload["semantic"] = self.semantic_status()
        payload["labels"] = self._labels.cache_status()
        return payload

    def episodes(self):
        return self._service.episodes()

    def list_rows(self, payload):
        started = time.time()
        request = dict(payload or {})
        mode = request.get("mode") or semantic.MODE_TEXT
        watched_only = bool(request.get("watched_only"))
        include_inactive = bool(request.get("include_inactive"))
        if watched_only and include_inactive:
            raise templates_view.ViewRefused(
                "watched templates are read from the active view, so they "
                "cannot be listed together with the inactive history; turn "
                "one of the two off", "refused")
        if mode == semantic.MODE_SEMANTIC:
            page, semantic_block, note = self._semantic_page(request)
        else:
            if watched_only:
                page = self._watched_page(request)
            else:
                page = self._service.list_rows(request)
            semantic_block = self.semantic_status()
            note = ""
        self._labels.decorate(page["rows"], self._service.current().coverers)
        if watched_only:
            page["rows"] = [row for row in page["rows"] if row["watched"]]
        page["semantic"] = semantic_block
        if note:
            page["note"] = note
        page["took_ms"] = int((time.time() - started) * 1000)
        page["query_id"] = self._record_query(request, page, mode,
                                              include_inactive)
        return page

    def _decorated_page(self, page, view):
        return templates_view.decorate_page(page, view)

    def _page_size(self, request):
        try:
            size = int(request.get("page_size") or self.limits.page_rows)
        except (TypeError, ValueError):
            size = self.limits.page_rows
        return max(1, min(size, self.limits.page_rows))

    def _predicate(self, request, extra=None):
        without = dict(request)
        without.pop("watched_only", None)
        base = templates_view._predicate(without)

        def matches(row):
            if extra is not None and not extra(row):
                return False
            return base is None or base(row)

        return matches

    def _watched_versions(self, view):
        watched = set(self._labels.watched_ids())
        for version_id in list(watched):
            watched.update(view.descendants(version_id))
        return watched

    def _watched_page(self, request):
        view = self._service.current()
        watched = self._watched_versions(view)
        predicate = self._predicate(
            request, lambda row: row["version_id"] in watched)
        sort = request.get("sort") or templates_view.SORT_VOLUME
        if sort == templates_view.SORT_FIRST_CATALOGUED:
            page = self._service.catalogued_page(
                request, self._page_size(request),
                version_ids=sorted(watched))
            return self._decorated_page(page, view)
        page = view.page(sort=sort, page_size=self._page_size(request),
                         after=request.get("after"),
                         predicate=templates_view._active_only(predicate))
        return self._decorated_page(page, view)

    def _semantic_page(self, request):
        view = self._service.current()
        text = (request.get("query") or "").strip()
        page_size = self._page_size(request)
        result = None
        if text:
            try:
                result = self._search.search(text, limit=page_size)
            except Exception as exc:                          # noqa: BLE001
                log(f"semantic search failed: {exc!r}")
        if result is None or result.status != semantic.STATUS_READY:
            fallback = dict(request)
            fallback["mode"] = semantic.MODE_TEXT
            if bool(request.get("watched_only")):
                page = self._watched_page(fallback)
            else:
                page = self._service.list_rows(fallback)
            block = (semantic.result_summary(result) if result is not None
                     else self.semantic_status())
            return page, block, SEMANTIC_FALLBACK_NOTE
        watched = (self._watched_versions(view)
                   if request.get("watched_only") else None)
        filters = dict(request)
        filters.pop("query", None)
        predicate = self._predicate(
            filters,
            None if watched is None
            else lambda row: row["version_id"] in watched)
        include_inactive = bool(request.get("include_inactive"))
        rows = []
        for hit in result.hits:
            row = view.row(hit.version_id)
            if row is None or not row["active"] or not predicate(row):
                continue
            row["score"] = hit.score
            rows.append(row)
        note = ""
        history = None
        if include_inactive:
            history = self._history_page(request, page_size)
            if history["status"] != HISTORY_READY:
                note = HISTORY_UNAVAILABLE_NOTE
        seen = set(row["version_id"] for row in rows)
        historical = [row for row in (history["rows"] if history else ())
                      if row["version_id"] not in seen and predicate(row)]
        reserved = min(len(historical), page_size // 2)
        active_shown = rows[:page_size - reserved]
        history_shown = historical[:page_size - len(active_shown)]
        merged = active_shown + history_shown
        has_more = (len(rows) > len(active_shown)
                    or len(historical) > len(history_shown)
                    or bool(history and history["has_more"]))
        page = {
            "rows": merged,
            "page_size": page_size,
            "after": None,
            "has_more": has_more,
            "total": len(merged),
            "total_relation": "gte" if has_more else "eq",
        }
        if history is not None:
            page["history"] = {
                "status": history["status"],
                "after": history["after"],
                "total": history["total"],
                "total_relation": history["total_relation"],
                "note": history["note"],
            }
        return (self._decorated_page(page, view),
                semantic.result_summary(result), note)

    def _history_page(self, request, page_size):
        text = (request.get("query") or "").strip()
        body = semantic.history_query(
            text, self._service.now_ms(), page_size=page_size,
            after=request.get("history_after"),
            retention_ms=self._service.retention_ms,
            active_ms=self._service.active_ms)
        try:
            found = self._service.catalog_search(body, page_size)
        except Exception as exc:                              # noqa: BLE001
            log(f"the inactive history lane failed: {exc!r}")
            return {"status": HISTORY_UNAVAILABLE, "rows": [], "after": None,
                    "has_more": False, "total": 0, "total_relation": "eq",
                    "note": HISTORY_UNAVAILABLE_NOTE}
        found["status"] = HISTORY_READY
        found["note"] = ""
        return found

    def _record_query(self, request, page, mode, include_inactive):
        text = (request.get("query") or "").strip()
        if not text:
            return None
        block = page.get("semantic") or {}
        try:
            return self._queries.record(
                text, mode, include_inactive,
                block.get("model_revision"),
                self._search.config.corpus_revision,
                [row["version_id"] for row in page["rows"]],
                viewer=str(request.get("viewer") or ""),
                latency_ms=int(page.get("took_ms") or 0))
        except Exception as exc:                              # noqa: BLE001
            log(f"the query was answered but not recorded: {exc!r}")
            return None

    def detail(self, payload):
        version_id = str((payload or {}).get("version_id") or "").strip()
        if not version_id:
            raise templates_view.ViewRefused(
                "a detail request names one version identifier", "refused")
        detail = self._service.detail(version_id)
        version = detail["version"]
        rows = [version] + list(detail["covered"]["versions"])
        self._labels.decorate(rows, self._service.current().coverers)
        try:
            with self._service.detail_slot():
                labels = self._labels.read(version_id)
        except templates_view.ViewRefused:
            labels = self._labels.cached(version_id)
        except Exception as exc:                              # noqa: BLE001
            log(f"stored labels were unreadable: {exc!r}")
            labels = self._labels.cached(version_id)
        detail["labels"] = labels
        detail["episodes"] = self._related_episodes(version)
        detail["neighbours"] = self._neighbours(version)
        detail["log_link"] = log_link(version)
        detail["descendant_count"] = version["descendant_count"]
        detail["descendants"] = list(version["descendants"][:20])
        detail["widened_into"] = list(version["widened_into"])
        detail["widened_from"] = list(version["widened_from"])
        detail["rematch_available"] = templates_view.rematch_available()
        return detail

    def _neighbours(self, row):
        try:
            result = self._search.neighbours(row.get("version_id"))
        except Exception as exc:                              # noqa: BLE001
            log(f"semantic neighbours were unreadable: {exc!r}")
            return {"suggestions": [], "note": NEIGHBOUR_UNAVAILABLE_NOTE,
                    "semantic": self.semantic_status()}
        view = self._service.current()
        suggestions = []
        for hit in result.hits:
            found = view.row(hit.version_id)
            if found is None or not found["active"]:
                continue
            suggestions.append({
                "version_id": found["version_id"],
                "family": found["family"],
                "template": found["template"],
                "score": hit.score,
            })
        return {
            "suggestions": suggestions,
            "note": self._neighbour_note(result),
            "semantic": semantic.result_summary(result),
        }

    def _neighbour_note(self, result):
        if result.status == semantic.STATUS_READY:
            return NEIGHBOUR_NOTE
        words = semantic.REASON_TEXT.get(result.reason, result.reason)
        if not words:
            return NEIGHBOUR_UNAVAILABLE_NOTE
        return f"{NEIGHBOUR_UNAVAILABLE_NOTE} The server reports that {words}."

    def _related_episodes(self, row):
        if row.get("historical"):
            return []
        hosts = set(row.get("origin_hosts") or ())
        try:
            summary = self._service.episodes()
        except Exception as exc:                              # noqa: BLE001
            log(f"episode summary unreadable: {exc!r}")
            return []
        if not hosts:
            return list(summary.get("episodes") or [])
        return [episode for episode in (summary.get("episodes") or [])
                if episode.get("entity_id") in hosts]

    def lines(self, payload):
        request = dict(payload or {})
        if not request.get("limit"):
            request["limit"] = TEMPLATE_LINES_ROWS
        return self._service.lines(request)

    def label_read(self, payload):
        version_id = str((payload or {}).get("version_id") or "").strip()
        if not version_id:
            raise templates_view.ViewRefused(
                "a label read names one version identifier", "refused")
        with self._service.detail_slot():
            labels = self._labels.read(version_id)
        return {"version_id": version_id, "labels": labels,
                "limits": {"note_max_chars": self._labels.note_max,
                           "history_max": self._labels.history_max,
                           "watched_max": self._labels.watched_max}}

    def label_write(self, payload):
        document = self._labels.write(payload or {})
        return {"label": document, "revision": document["revision"]}

    def opened(self, payload):
        request = dict(payload or {})
        self._queries.opened(request.get("query_id"),
                             request.get("version_id"),
                             request.get("rank"))
        return None


def memory_bytes(text):
    body = str(text or "").strip()
    if not body or body.lower() == "infinity":
        return 0
    digits = 0
    while digits < len(body) and body[digits].isdigit():
        digits += 1
    if digits == 0:
        raise ValueError(f"{text!r} does not start with a number of bytes")
    suffix = body[digits:].strip().upper()
    if suffix not in MEMORY_SUFFIXES:
        raise ValueError(f"{text!r} carries no size suffix this server knows")
    return int(body[:digits]) * MEMORY_SUFFIXES[suffix]


def serving_peak_bytes():
    return {
        "process_base": PROCESS_BASE_BYTES,
        "live_lane": LIVE_LANE_BYTES,
        "vectors": VECTOR_CACHE_BYTES,
        "cached_metadata": CATALOG_CACHE_BYTES * RESIDENT_VIEWS_AT_PEAK,
        "decoded_responses": RESPONSE_CACHE_BYTES,
        "aggregation": AGGREGATION_BYTES,
    }


def memory_verdict(ceiling_text):
    parts = serving_peak_bytes()
    peak = sum(parts.values())
    try:
        ceiling = memory_bytes(ceiling_text)
    except ValueError as exc:
        return peak, (
            f"the templates page cannot check its memory budget because the "
            f"service ceiling is unreadable: {exc}")
    if not ceiling:
        return peak, (
            "the templates page needs the service memory ceiling to verify "
            "its combined peak, and SHIFTER_MEMORY_MAX is not set")
    if peak <= ceiling:
        return peak, ""
    named = ", ".join(f"{name} {value}"
                      for name, value in sorted(parts.items()))
    return peak, (
        f"the configured serving limits peak at {peak} bytes against a "
        f"{ceiling} byte service ceiling, so the templates page is not "
        f"started; lower a limit or raise MemoryMax ({named}, cached metadata "
        f"counted {RESIDENT_VIEWS_AT_PEAK} times because the previous view "
        f"stays resident while the next one is built)")


def build_templates():
    if not TEMPLATES_ENABLED:
        return None, "the templates page is turned off on this server"
    if templates_view is None:
        return None, (
            "the templates page needs the shared template contract, which did "
            f"not import: {TEMPLATES_IMPORT_ERROR}")
    if not OS_URL:
        return None, (
            "the templates page reads bucket documents and definitions "
            "through the query lane, and SHIFTER_OS_URL is empty")
    peak, refusal = memory_verdict(MEMORY_MAX)
    if refusal:
        return None, refusal
    log(f"templates serving limits peak at {peak} bytes, "
        f"inside the {MEMORY_MAX} service ceiling")
    try:
        limits = templates_view.Limits(
            page_rows=TEMPLATE_PAGE_ROWS,
            lines_rows=TEMPLATE_LINES_CEILING,
            detail_concurrency=TEMPLATE_CONCURRENT_QUERIES,
            refresh_interval_ms=VIEW_REFRESH_SECONDS * 1000,
            episode_interval_ms=EPISODE_REFRESH_SECONDS * 1000,
            max_metadata_bytes=CATALOG_CACHE_BYTES)
    except ValueError as exc:
        return None, f"the templates limits do not hold together: {exc}"
    transport = templates_view.OpenSearchTransport(
        OS_URL, OS_USER, OS_PASSWORD, OS_VERIFY, OS_TIMEOUT)
    service = templates_view.TemplatesService(
        transport, limits=limits, shared_indices=OS_INDICES)
    decisions = triage.Transport(OS_URL, OS_USER, OS_PASSWORD, OS_VERIFY,
                                 OS_TIMEOUT)
    runtime = TemplatesRuntime(service, semantic.build(),
                               triage.LabelStore(decisions),
                               triage.QueryLog(decisions), limits)
    return runtime, ""


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
        if path.startswith("/api/templates/"):
            self.templates(path, True)
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

    def templates(self, path, has_body):
        runtime = TEMPLATES
        with _lock:
            _stats["template_requests"] += 1
        if runtime is None:
            with _lock:
                _stats["bad_template_requests"] += 1
            self._json(503, {"error": TEMPLATES_DISABLED})
            return
        payload = {}
        if has_body:
            payload = decode_body(self)
            if not isinstance(payload, dict):
                with _lock:
                    _stats["bad_template_requests"] += 1
                self._json(400, {"error": "bad payload"})
                return
        name = path[len("/api/templates/"):]
        try:
            if name == "summary" and not has_body:
                self._json(200, runtime.summary())
            elif name == "episodes" and not has_body:
                self._json(200, runtime.episodes())
            elif name == "list" and has_body:
                self._json(200, runtime.list_rows(payload))
            elif name == "detail" and has_body:
                self._json(200, runtime.detail(payload))
            elif name == "lines" and has_body:
                self._json(200, runtime.lines(payload))
            elif name == "labels" and has_body:
                self._json(200, runtime.label_read(payload))
            elif name == "label" and has_body:
                self._json(200, runtime.label_write(payload))
            elif name == "opened" and has_body:
                runtime.opened(payload)
                self._send(204)
            else:
                self._send(404, b"not found")
        except templates_view.ViewRefused as exc:
            self._refused(400 if exc.status == "refused" else 503, str(exc))
        except triage.LabelConflict as exc:
            with _lock:
                _stats["bad_template_requests"] += 1
            self._json(409, {"error": str(exc), "label": exc.label})
        except triage.TriageRefused as exc:
            self._refused(400 if exc.status == "refused" else 503, str(exc))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            with _lock:
                _stats["bad_template_requests"] += 1
            log(f"templates request rejected by OpenSearch: {exc.code} "
                f"{detail}")
            self._json(502, {"error": f"OpenSearch answered {exc.code}",
                             "detail": detail})
        except Exception as exc:                              # noqa: BLE001
            with _lock:
                _stats["bad_template_requests"] += 1
            log(f"templates request failed: {exc!r}")
            self._json(502, {"error": str(exc)})

    def _refused(self, code, message):
        with _lock:
            _stats["bad_template_requests"] += 1
        self._json(code, {"error": message})

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
                    "templatesConfigured": TEMPLATES is not None,
                    **_stats,
                }).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path.startswith("/api/templates/"):
            self.templates(path, False)
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
    global TEMPLATES, TEMPLATES_DISABLED
    if not os.path.isdir(STATIC_DIR):
        log(f"FATAL: static directory {STATIC_DIR} does not exist")
        return 1
    TEMPLATES, TEMPLATES_DISABLED = build_templates()
    if TEMPLATES is not None:
        TEMPLATES.start()
    else:
        log(f"templates page off: {TEMPLATES_DISABLED}")
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
        if TEMPLATES is not None:
            TEMPLATES.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
