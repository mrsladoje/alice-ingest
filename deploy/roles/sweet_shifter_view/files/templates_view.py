import base64
import copy
import json
import os
import ssl
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.environ.get(
    "ALICE_SHARED_PATH", "/opt/sweet/shared"))
sys.path.insert(0, os.environ.get(
    "ALICE_TEMPLATING_PATH", "/opt/sweet/templating"))

import template_contract as contract                          # noqa: E402

try:
    import drainbench                                         # noqa: E402
    TEMPLATING_IMPORT_ERROR = ""
except Exception as exc:                                      # noqa: BLE001
    drainbench = None
    TEMPLATING_IMPORT_ERROR = repr(exc)

CATALOG_INDEX = os.environ.get("SHIFTER_CATALOG_INDEX", contract.CATALOG_INDEX)
FINE_BUCKETS = os.environ.get(
    "SHIFTER_BUCKETS_5M_PATTERN",
    contract.bucket_index_pattern(contract.FINE_BUCKET_MS))
COARSE_BUCKETS = os.environ.get(
    "SHIFTER_BUCKETS_1H_PATTERN",
    contract.bucket_index_pattern(contract.COARSE_BUCKET_MS))
INCIDENTS_INDEX = os.environ.get("SHIFTER_INCIDENTS_INDEX", "alice-incidents")
LOCAL_INDEX_PREFIX = os.environ.get("SHIFTER_LOCAL_INDEX_PREFIX",
                                    "application-logs-local-")
SHARED_INDICES = os.environ.get(
    "SHIFTER_OS_INDICES", "infologger,application-logs-central")

ACTIVE_MS = (int(os.environ.get("SHIFTER_ACTIVE_DAYS", "0")) * contract.DAY_MS
             or contract.ACTIVE_MS)
DEFINITION_RETENTION_MS = (
    int(os.environ.get("SHIFTER_DEFINITION_RETENTION_DAYS", "0"))
    * contract.DAY_MS or contract.DEFINITION_RETENTION_MS)

REFRESH_INTERVAL_MS = contract.PUBLISH_INTERVAL_MS
EPISODE_INTERVAL_MS = 30000

PAGE_ROWS_CEILING = 50
LINES_ROWS_CEILING = 500
DETAIL_CONCURRENCY_CEILING = 2
EPISODE_INTERVAL_FLOOR_MS = 30000
EPISODE_ROWS = 20
CATALOG_PAGE = 1000
WATERMARK_PAGE = 1000
ROUTE_NODES = 1000
MAX_ANCESTORS = 64
CATALOGUED_CACHE_ROWS = 512

SEARCH_TIMEOUT_MS = int(
    os.environ.get("SHIFTER_SEARCH_TIMEOUT_SECONDS", "30")) * 1000
SEARCH_TERMINATE_AFTER = int(
    os.environ.get("SHIFTER_SEARCH_TERMINATE_AFTER", "200000"))

MAX_VIEW_VERSIONS = contract.MAX_BUCKET_VERSIONS
MAX_METADATA_BYTES = 32 * 1024 * 1024

VERSION_RESIDENT_BYTES = 2344
SCOPE_MEMBER_BYTES = 184

STATUS_EXACT = "exact"
STATUS_INCOMPLETE = "incomplete"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"

STATUS_FOR_COVERAGE = {
    contract.COVERAGE_COMPLETE: STATUS_EXACT,
    contract.COVERAGE_PARTIAL: STATUS_INCOMPLETE,
    contract.COVERAGE_UNKNOWN: STATUS_UNKNOWN,
    contract.COVERAGE_UNAVAILABLE: STATUS_UNAVAILABLE,
}

SORT_VOLUME = "volume"
SORT_LAST_OBSERVED = "last_observed"
SORT_FIRST_CATALOGUED = "first_catalogued"
SORTS = (SORT_VOLUME, SORT_LAST_OBSERVED, SORT_FIRST_CATALOGUED)
VIEW_SORTS = (SORT_VOLUME, SORT_LAST_OBSERVED)

MODE_TEXT = "text"
MODE_SEMANTIC = "semantic"

CATALOG_FIELDS = list(contract.CATALOG_FIELDS)

RECORD_FIELDS = (
    "@timestamp", "collector_time", "node", "origin_host", "host", "hostname",
    "program", "log_source", "severity", "severity_norm", "message",
    "doc_id", "template_version", "template_id", "template_status", "run",
    "partition", "detector", "system", "facility", "pid", "level",
    "rolename", "source_file",
)

EPISODE_FIELDS = [
    "incident_id", "group_id", "alertname", "entity_kind", "entity_id",
    "severity", "state", "episode_start", "title", "diagnosis",
    "operator_action", "affected", "entity_samples", "signal_ids",
]

LINES_NOTE = (
    "Every line here carries the stamp of the selected version or of a "
    "narrower version it covers. The stamp is a fact about the line; the "
    "count of lines shown is a page, not the 28-day volume.")
ANCESTORS_NOTE = (
    "Lines stamped with a wider ancestor were fetched too and kept only "
    "when their tokens fit the selected template.")
ANCESTORS_UNAVAILABLE_NOTE = (
    "Ancestor lines were not searched: the mining recipe is not importable "
    "on this server, so a token-wise re-match cannot run.")
ROUTE_NOTE = (
    "The node set comes from the published bucket documents. A node that "
    "started writing this template inside the open five-minute bucket is "
    "not in it yet; search every node to include it.")
EVERY_NODE_NOTE = (
    "Every worker's local index was searched, so the answer waited for the "
    "slowest node.")


def log(msg):
    print(f"[templates] {msg}", flush=True)


class ViewRefused(Exception):
    def __init__(self, message, status=STATUS_UNAVAILABLE):
        super().__init__(message)
        self.status = status


class Limits:
    def __init__(self, page_rows=PAGE_ROWS_CEILING,
                 lines_rows=LINES_ROWS_CEILING,
                 detail_concurrency=DETAIL_CONCURRENCY_CEILING,
                 refresh_interval_ms=REFRESH_INTERVAL_MS,
                 episode_interval_ms=EPISODE_INTERVAL_MS,
                 episode_rows=EPISODE_ROWS, catalog_page=CATALOG_PAGE,
                 route_nodes=ROUTE_NODES, max_versions=MAX_VIEW_VERSIONS,
                 max_metadata_bytes=MAX_METADATA_BYTES):
        self.page_rows = _bounded(page_rows, 1, PAGE_ROWS_CEILING, "page_rows")
        self.lines_rows = _bounded(lines_rows, 1, LINES_ROWS_CEILING,
                                   "lines_rows")
        self.detail_concurrency = _bounded(
            detail_concurrency, 1, DETAIL_CONCURRENCY_CEILING,
            "detail_concurrency")
        self.refresh_interval_ms = _bounded(
            refresh_interval_ms, 1, None, "refresh_interval_ms")
        self.episode_interval_ms = _bounded(
            episode_interval_ms, EPISODE_INTERVAL_FLOOR_MS, None,
            "episode_interval_ms")
        self.episode_rows = _bounded(episode_rows, 1, 200, "episode_rows")
        self.catalog_page = _bounded(catalog_page, 1, 10000, "catalog_page")
        self.route_nodes = _bounded(route_nodes, 1, 10000, "route_nodes")
        self.max_versions = _bounded(max_versions, 1, None, "max_versions")
        self.max_metadata_bytes = _bounded(max_metadata_bytes, 1, None,
                                           "max_metadata_bytes")
        self.stale_after_ms = (contract.COARSE_BUCKET_MS
                               + contract.IDLE_NODE_MS
                               + self.refresh_interval_ms)
        self.expire_after_ms = 2 * self.stale_after_ms


def _bounded(value, floor, ceiling, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if value < floor:
        raise ValueError(f"{name} must be at least {floor}, got {value}")
    if ceiling is not None and value > ceiling:
        raise ValueError(f"{name} may be at most {ceiling}, got {value}")
    return value


class OpenSearchTransport:
    def __init__(self, url, user="", password="", verify=True, timeout=45.0,
                 search_timeout_ms=SEARCH_TIMEOUT_MS,
                 terminate_after=SEARCH_TERMINATE_AFTER):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.verify = verify
        self.timeout = timeout
        self.search_timeout_ms = _bounded(int(search_timeout_ms), 1, None,
                                          "search_timeout_ms")
        self.terminate_after = _bounded(int(terminate_after), 1, None,
                                        "terminate_after")

    def _context(self):
        if not self.url.startswith("https") or self.verify:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"{self.url}{path}", data=data,
                                         method="POST")
        request.add_header("Content-Type", "application/json")
        if self.user:
            raw = f"{self.user}:{self.password}".encode("utf-8")
            request.add_header(
                "Authorization",
                "Basic " + base64.b64encode(raw).decode("ascii"))
        with urllib.request.urlopen(request, timeout=self.timeout,
                                    context=self._context()) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def search(self, index, body, ignore_unavailable=False):
        bounded = dict(body)
        bounded["timeout"] = f"{self.search_timeout_ms}ms"
        bounded["terminate_after"] = self.terminate_after
        path = f"/{index}/_search"
        if ignore_unavailable:
            path += "?ignore_unavailable=true"
        return self._post(path, bounded)


def _scope_page(values, limit):
    ordered = sorted(values)
    return ordered[:limit], len(ordered) > limit


def _hits(result):
    return ((result or {}).get("hits") or {}).get("hits") or []


def _matched_total(result, rows):
    total = ((result or {}).get("hits") or {}).get("total") or {}
    if isinstance(total, dict):
        matched = total.get("value", len(rows))
        relation = total.get("relation", "eq")
    else:
        matched = total
        relation = "eq"
    if (result or {}).get("terminated_early") or (result or {}).get(
            "timed_out"):
        relation = "gte"
    return matched, relation


def _count_status(coverage_status):
    return STATUS_FOR_COVERAGE.get(coverage_status, STATUS_UNKNOWN)


def _buckets(result, *names):
    node = (result or {}).get("aggregations") or {}
    for name in names:
        node = node.get(name) or {}
    return node.get("buckets") or [], node.get("sum_other_doc_count") or 0


def _epoch_ms(value, name):
    if value is None or value == "":
        return None
    parsed = contract.parse_collector_time(value)
    if parsed.status != contract.TIME_OK:
        raise ViewRefused(f"{name} {value!r} is not a time this server "
                          f"reads; send epoch milliseconds or ISO 8601",
                          "refused")
    return parsed.epoch_ms


def rematch_available():
    return drainbench is not None


def line_fits(family, template, message):
    if drainbench is None or not isinstance(message, str) or not message:
        return False
    try:
        tokens = drainbench.recipe_tokens(family, message)
    except KeyError:
        return False
    return contract.covers(contract.template_tokens(template), tuple(tokens))


def resident_bytes(row):
    return (VERSION_RESIDENT_BYTES
            + SCOPE_MEMBER_BYTES * (len(row["programs"])
                                    + len(row["origin_hosts"])
                                    + len(row["log_sources"])
                                    + len(row["descendants"]))
            + len(row["template"]) + len(row["normalized"]))


def _row(document, count, count_status, activity_ms, active_ms,
         descendants=()):
    version = document.get("version_id")
    last_observed = document.get("last_observed") or 0
    active = contract.is_active(last_observed, activity_ms, active_ms)
    programs, cut_programs = _scope_page(document.get("programs") or [],
                                         contract.MAX_ENTRY_PROGRAMS)
    hosts, cut_hosts = _scope_page(document.get("origin_hosts") or [],
                                   contract.MAX_ENTRY_HOSTS)
    sources, cut_sources = _scope_page(document.get("log_sources") or [],
                                       contract.MAX_ENTRY_SOURCES)
    held = sorted(descendants)
    return {
        "version_id": version,
        "canonical_id": document.get("canonical_id"),
        "family": document.get("family"),
        "template": document.get("template"),
        "normalized": document.get("normalized"),
        "token_count": document.get("token_count"),
        "programs": programs,
        "origin_hosts": hosts,
        "log_sources": sources,
        "severity_norm": document.get("severity_norm"),
        "scope_truncated": bool(document.get("programs_truncated")
                                or document.get("origin_hosts_truncated")
                                or cut_programs or cut_hosts or cut_sources),
        "count": contract.encode_int(count),
        "count_status": count_status,
        "first_observed": document.get("first_observed"),
        "last_observed": last_observed,
        "first_catalogued": document.get("first_catalogued"),
        "active": active,
        "historical": not active,
        "widened_into": sorted(document.get("widened_into") or []),
        "widened_from": sorted(document.get("widened_from") or []),
        "descendants": held,
        "descendant_count": len(held),
        "canonical_versions": [],
        "label": None,
        "label_conflicts": 0,
        "watched": False,
        "score": None,
    }


def _sort_key(row, sort):
    count = contract.decode_int(row["count"])
    if sort == SORT_LAST_OBSERVED:
        return (-row["last_observed"], row["version_id"])
    return (-count, row["version_id"])


def _cursor_of(row, sort):
    if sort == SORT_LAST_OBSERVED:
        return [str(row["last_observed"]), row["version_id"]]
    return [str(contract.decode_int(row["count"])), row["version_id"]]


def _cursor_key(cursor):
    if not isinstance(cursor, (list, tuple)) or len(cursor) != 2:
        raise ViewRefused(
            "the page cursor is not the two-value cursor this list publishes; "
            "ask for the first page again", "refused")
    try:
        value = int(cursor[0])
    except (TypeError, ValueError):
        raise ViewRefused(
            "the page cursor does not carry an integer sort value; ask for "
            "the first page again", "refused")
    return (-value, str(cursor[1]))


def _empty_coverage(status, idle=()):
    gaps = [contract.coverage_gap(
        contract.GAP_NODE_IDLE, node=node,
        detail=f"{node} has not published for more than "
               f"{contract.IDLE_NODE_MS // contract.SECOND_MS} seconds")
        for node in sorted(idle)]
    return {"status": status, "cutoff": None, "window_start": None,
            "complete": [], "behind": [], "idle": sorted(idle), "gaps": gaps}


class TemplatesView:
    def __init__(self, window, coverage, watermarks, rows, refreshed_at,
                 cutoff=None, status=STATUS_EXACT, note="",
                 uncatalogued=None, expired=None, metadata_bytes=0):
        self.window = dict(window or {})
        self.coverage = dict(coverage or {})
        self.watermarks = copy.deepcopy(watermarks or {})
        self.refreshed_at = refreshed_at
        self.cutoff = cutoff
        self.status = status
        self.note = note
        self.uncatalogued = dict(uncatalogued or {"versions": 0,
                                                  "records": 0})
        self.expired = dict(expired) if expired else None
        self.metadata_bytes = metadata_bytes

        canonical = {}
        for row in rows:
            canonical.setdefault(row["canonical_id"], []).append(
                row["version_id"])
        for group in canonical.values():
            group.sort()
        for row in rows:
            row["canonical_versions"] = list(canonical[row["canonical_id"]])

        self.rows = tuple(rows)
        self.by_version = {row["version_id"]: row for row in self.rows}
        self.canonical_versions = {cid: tuple(ids)
                                   for cid, ids in canonical.items()}
        self._orders = {sort: tuple(sorted(
            self.rows, key=lambda row, s=sort: _sort_key(row, s)))
            for sort in VIEW_SORTS}
        total = 0
        for row in self.rows:
            total += contract.decode_int(row["count"])
        self.total_records = total
        self.records_status = _count_status(
            self.coverage.get("status", contract.COVERAGE_UNKNOWN))

    @classmethod
    def unavailable(cls, note, refreshed_at, watermarks=None, idle=(),
                    status=STATUS_UNAVAILABLE):
        coverage = _empty_coverage(
            contract.COVERAGE_UNKNOWN if idle or watermarks
            else contract.COVERAGE_UNAVAILABLE, idle)
        return cls({}, coverage, watermarks, [], refreshed_at, status=status,
                   note=note)

    def has_rows(self):
        return bool(self.rows)

    def group_count(self, canonical_id):
        total = 0
        for version in self.canonical_versions.get(canonical_id, ()):
            total += contract.decode_int(self.by_version[version]["count"])
        return total

    def canonical_group(self, canonical_id):
        versions = self.canonical_versions.get(canonical_id, ())
        return {
            "canonical_id": canonical_id,
            "versions": [dict(self.by_version[v]) for v in versions],
            "count": contract.encode_int(self.group_count(canonical_id)),
            "count_status": _count_status(
                self.coverage.get("status", contract.COVERAGE_UNKNOWN)),
        }

    def row(self, version_id):
        row = self.by_version.get(version_id)
        return dict(row) if row is not None else None

    def descendants(self, version_id):
        row = self.by_version.get(version_id)
        return list(row["descendants"]) if row is not None else []

    def ancestors(self, version_id, limit=MAX_ANCESTORS):
        found = []
        seen = {version_id}
        queue = [version_id]
        while queue and len(found) < limit:
            row = self.by_version.get(queue.pop(0))
            if row is None:
                continue
            for wider in row["widened_into"]:
                if wider in seen:
                    continue
                seen.add(wider)
                found.append(wider)
                queue.append(wider)
                if len(found) >= limit:
                    break
        return found

    def page(self, sort=SORT_VOLUME, page_size=PAGE_ROWS_CEILING, after=None,
             predicate=None):
        if sort not in self._orders:
            sort = SORT_VOLUME
        size = max(1, min(int(page_size), PAGE_ROWS_CEILING))
        start = _cursor_key(after) if after is not None else None
        rows = []
        matched = 0
        has_more = False
        for row in self._orders[sort]:
            if predicate is not None and not predicate(row):
                continue
            matched += 1
            if start is not None and _sort_key(row, sort) <= start:
                continue
            if len(rows) >= size:
                has_more = True
                continue
            rows.append(dict(row))
        return {
            "rows": rows,
            "page_size": size,
            "after": _cursor_of(rows[-1], sort) if rows else None,
            "has_more": has_more,
            "total": matched,
            "total_relation": "eq",
        }

    def snapshot(self, now_ms):
        window = dict(self.window)
        window["age_ms"] = now_ms - self.cutoff if self.cutoff else None
        return window

    def watermark_rows(self, now_ms):
        rows = {}
        for node in sorted(self.watermarks):
            held = self.watermarks[node]
            through = held.get("published_through")
            rows[node] = {
                "published_through": through,
                "published_through_iso": (contract.iso_utc(through)
                                          if through is not None else None),
                "published_at": held.get("published_at"),
                "age_ms": now_ms - held.get("published_at", now_ms),
                "ledger_start": held.get("ledger_start"),
            }
        return rows

    def totals(self):
        return {
            "versions": len(self.rows),
            "canonical_groups": len(self.canonical_versions),
            "records": contract.encode_int(self.total_records),
            "records_status": self.records_status,
        }


class TemplatesService:
    def __init__(self, transport, limits=None, clock=None,
                 catalog_index=CATALOG_INDEX, fine_pattern=FINE_BUCKETS,
                 coarse_pattern=COARSE_BUCKETS,
                 incidents_index=INCIDENTS_INDEX,
                 local_prefix=LOCAL_INDEX_PREFIX,
                 shared_indices=SHARED_INDICES, active_ms=ACTIVE_MS,
                 retention_ms=DEFINITION_RETENTION_MS):
        self._transport = transport
        self.limits = limits or Limits()
        self.active_ms = _bounded(int(active_ms), contract.COARSE_BUCKET_MS,
                                  None, "active_ms")
        self.retention_ms = _bounded(int(retention_ms), self.active_ms, None,
                                     "retention_ms")
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._catalog_index = catalog_index
        self._fine_pattern = fine_pattern
        self._coarse_pattern = coarse_pattern
        self._incidents_index = incidents_index
        self._local_prefix = local_prefix
        self._shared_indices = [name.strip() for name in
                                str(shared_indices or "").split(",")
                                if name.strip()]
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._detail_slots = threading.BoundedSemaphore(
            self.limits.detail_concurrency)
        self._stop = threading.Event()
        self._thread = None
        self._view = TemplatesView.unavailable(
            "no watermark has been read yet", self.now_ms())
        self._last_attempt_ms = None
        self._last_success_ms = None
        self._last_error = ""
        self._expired = None
        self._catalogued = {}
        self._episodes = {"episodes": [], "refreshed_at": 0, "stale": True}
        self._episodes_attempt_ms = None
        self.refreshes = 0
        self.failures = 0

    def now_ms(self):
        return int(self._clock())

    def current(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        with self._lock:
            view = self._view
            if not view.cutoff or now - view.cutoff <= \
                    self.limits.expire_after_ms:
                return view
            cached = self._expired
            if cached is not None and cached[0] is view:
                return cached[1]
            replacement = self._expired_view(view, now)
            self._expired = (view, replacement)
            return replacement

    def _expired_view(self, view, now_ms):
        age = now_ms - view.cutoff
        ceiling = self.limits.expire_after_ms
        expired = {"window_end": view.cutoff,
                   "window_end_iso": contract.iso_utc(view.cutoff),
                   "age_ms": age, "max_age_ms": ceiling}
        note = (f"the newest cutoff every live node had published past is "
                f"{contract.iso_utc(view.cutoff)}, {age // 1000} seconds "
                f"ago, past the {ceiling // 1000} second ceiling; that "
                f"window is history and this page serves no current volume "
                f"until a newer cutoff arrives")
        return TemplatesView(
            {}, _empty_coverage(contract.COVERAGE_UNKNOWN,
                                view.coverage.get("idle") or ()),
            view.watermarks, [], view.refreshed_at, status=STATUS_UNKNOWN,
            note=note, expired=expired)

    def start(self):
        if self._thread is not None:
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="templates-refresh")
        self._thread.start()
        return self._thread

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.maybe_refresh()
            except ViewRefused as exc:
                log(f"refresh refused: {exc}")
            except Exception as exc:
                log(f"refresh failed: {exc!r}")
            self._stop.wait(max(1.0, self.limits.refresh_interval_ms / 1000.0))

    def maybe_refresh(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        interval = self.limits.refresh_interval_ms
        if (self._last_attempt_ms is not None
                and now - self._last_attempt_ms < interval):
            return False
        self.refresh(now)
        return True

    def refresh(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        with self._refresh_lock:
            self._last_attempt_ms = now
            try:
                view = self._build(now)
            except ViewRefused as exc:
                self.failures += 1
                self._last_error = str(exc)
                raise
            except Exception as exc:
                self.failures += 1
                self._last_error = repr(exc)
                raise
            with self._lock:
                self._view = view
                self._expired = None
                self._catalogued = {}
            self.refreshes += 1
            self._last_success_ms = now
            self._last_error = ""
            return view

    def _build(self, now_ms):
        watermarks = self._watermarks()
        choice = contract.choose_cutoff(watermarks, now_ms)
        cutoff = choice["cutoff"]
        if cutoff is None:
            if watermarks:
                note = ("no node has published a watermark inside the idle "
                        "horizon; idle: " + ", ".join(choice["idle"]))
            else:
                note = "no node has published a watermark"
            if self.current(now_ms).has_rows():
                raise ViewRefused(note + "; the last view is kept and its "
                                  "age is shown", STATUS_UNKNOWN)
            return TemplatesView.unavailable(
                note, now_ms, watermarks=watermarks, idle=choice["idle"],
                status=STATUS_UNKNOWN)
        window_start, window_end = contract.window_bounds(cutoff,
                                                          self.active_ms)
        counts, nodes = self._volume(window_start, window_end)
        coverage = contract.window_coverage(window_end, nodes, watermarks,
                                            now_ms)
        documents = self._definitions(window_start)
        descendants = contract.cover_descendants(
            [(doc["version_id"], doc["family"], doc["template"])
             for doc in documents])
        status = _count_status(coverage["status"])
        rows = []
        resident = 0
        catalogued = set()
        for doc in documents:
            version = doc["version_id"]
            catalogued.add(version)
            row = _row(doc, counts.get(version, 0), status, window_end,
                       self.active_ms, descendants.get(version, ()))
            resident += resident_bytes(row)
            if resident > self.limits.max_metadata_bytes:
                raise ViewRefused(
                    f"{len(rows) + 1} template versions need more than the "
                    f"{self.limits.max_metadata_bytes} byte ceiling of "
                    f"resident metadata; a truncated view would claim a "
                    f"completeness it does not have", STATUS_UNAVAILABLE)
            rows.append(row)
        missing = [version for version in counts if version not in catalogued]
        uncatalogued = {"versions": len(missing),
                        "records": sum(counts[v] for v in missing)}
        window = {
            "window_start": window_start,
            "window_end": window_end,
            "window_start_iso": contract.iso_utc(window_start),
            "window_end_iso": contract.iso_utc(window_end),
            "window_days": self.active_ms // contract.DAY_MS,
            "bucket_seconds": contract.COARSE_BUCKET_MS // contract.SECOND_MS,
            "nodes": list(nodes),
        }
        return TemplatesView(window, coverage, watermarks, rows, now_ms,
                             cutoff=window_end, status=status,
                             uncatalogued=uncatalogued,
                             metadata_bytes=resident)

    def _watermarks(self):
        found = {}
        after = None
        while True:
            body = {
                "size": WATERMARK_PAGE,
                "track_total_hits": False,
                "sort": [{"node": {"order": "asc"}}],
                "query": {"bool": {"filter": [
                    {"term": {"kind": contract.KIND_WATERMARK}}]}},
            }
            if after is not None:
                body["search_after"] = after
            hits = _hits(self._transport.search(self._catalog_index, body))
            if not hits:
                break
            for hit in hits:
                document = hit.get("_source") or {}
                try:
                    contract.validate_watermark(document)
                except contract.ContractError as exc:
                    log(f"watermark {document.get('watermark_id')!r} is not "
                        f"readable: {exc}")
                    continue
                found[document["node"]] = {
                    "published_through": document["published_through"],
                    "published_at": document["published_at"],
                    "ledger_start": document["ledger_start"],
                }
            after = hits[-1].get("sort")
            if after is None or len(hits) < WATERMARK_PAGE:
                break
        return found

    def _volume(self, window_start, window_end):
        body = {
            "size": 0,
            "track_total_hits": False,
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_BUCKET}},
                {"term": {"late": False}},
                {"term": {"resolution_seconds":
                          contract.COARSE_BUCKET_MS // contract.SECOND_MS}},
                {"range": {"bucket_start": {"gte": window_start,
                                            "lt": window_end}}},
            ]}},
            "aggs": {
                "nodes": {"terms": {"field": "node",
                                    "size": self.limits.route_nodes}},
                "counts": {"nested": {"path": "counts"}, "aggs": {
                    "versions": {"terms": {
                        "field": "counts.version_id",
                        "size": self.limits.max_versions},
                        "aggs": {"total": {"sum": {"field": "counts.count"}}}},
                }},
            },
        }
        result = self._transport.search(self._coarse_pattern, body) or {}
        node_rows, more_nodes = _buckets(result, "nodes")
        if more_nodes:
            raise ViewRefused(
                f"more than {self.limits.route_nodes} nodes published "
                f"buckets in this window; the node set would be truncated "
                f"and coverage could not be named", STATUS_UNAVAILABLE)
        version_rows, more_versions = _buckets(result, "counts", "versions")
        if more_versions:
            raise ViewRefused(
                f"more than {self.limits.max_versions} template versions "
                f"have volume in this window; the view would be a truncated "
                f"total and is refused", STATUS_UNAVAILABLE)
        counts = {}
        for row in version_rows:
            total = (row.get("total") or {}).get("value") or 0
            counts[str(row.get("key"))] = int(round(total))
        nodes = sorted(str(row.get("key")) for row in node_rows)
        return counts, nodes

    def _definitions(self, window_start):
        documents = []
        after = None
        while True:
            body = {
                "size": self.limits.catalog_page,
                "track_total_hits": False,
                "sort": [{"version_id": {"order": "asc"}}],
                "query": {"bool": {"filter": [
                    {"term": {"kind": contract.KIND_CATALOG_TEMPLATE}},
                    {"range": {"last_observed": {"gte": window_start}}}]}},
                "_source": CATALOG_FIELDS,
            }
            if after is not None:
                body["search_after"] = after
            hits = _hits(self._transport.search(self._catalog_index, body))
            for hit in hits:
                document = hit.get("_source") or {}
                if not (document.get("version_id") and document.get("family")
                        and document.get("template")):
                    continue
                documents.append(document)
                if len(documents) > self.limits.max_versions:
                    raise ViewRefused(
                        f"more than {self.limits.max_versions} active "
                        f"definitions; the view would be truncated and is "
                        f"refused", STATUS_UNAVAILABLE)
            if len(hits) < self.limits.catalog_page:
                break
            after = hits[-1].get("sort")
            if after is None:
                break
        return documents

    def summary(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        view = self.current(now)
        return {
            "window": view.snapshot(now),
            "coverage": copy.deepcopy(view.coverage),
            "watermarks": view.watermark_rows(now),
            "totals": view.totals(),
            "uncatalogued": dict(view.uncatalogued),
            "semantic": {"status": STATUS_UNAVAILABLE, "groups": 0,
                         "vector_bytes": 0, "model_revision": "unset"},
            "expired_window": (dict(view.expired) if view.expired else None),
            "rematch_available": rematch_available(),
            "refreshed_at": view.refreshed_at,
            "stale": self.stale(now),
            "note": view.note,
            "last_error": self._last_error,
        }

    def stale(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        if self._last_success_ms is None:
            return True
        if now - self._last_success_ms > 2 * self.limits.refresh_interval_ms:
            return True
        cutoff = self.current(now).cutoff
        if not cutoff:
            return True
        return now - cutoff > self.limits.stale_after_ms

    def list_rows(self, request=None, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        request = request or {}
        mode = request.get("mode") or MODE_TEXT
        if mode == MODE_SEMANTIC:
            raise ViewRefused(
                "semantic ranking is not available on this server, so this "
                "list would not be the ranking you asked for; search the text "
                "instead", "refused")
        if mode != MODE_TEXT:
            raise ViewRefused(f"{mode!r} is not a template search mode",
                              "refused")
        sort = request.get("sort") or SORT_VOLUME
        page_size = request.get("page_size") or self.limits.page_rows
        try:
            page_size = int(page_size)
        except (TypeError, ValueError):
            page_size = self.limits.page_rows
        page_size = max(1, min(page_size, self.limits.page_rows))
        view = self.current(now)
        started = time.time()
        if request.get("include_inactive"):
            page = self._catalog_page(request, view, page_size, now,
                                      sort=sort)
        elif sort == SORT_FIRST_CATALOGUED:
            page = self._catalog_page(request, view, page_size, now,
                                      sort=sort, active_only=True)
        else:
            page = view.page(sort=sort, page_size=page_size,
                             after=request.get("after"),
                             predicate=_active_only(_predicate(request)))
        decorate_page(page, view)
        page["semantic"] = {"status": STATUS_UNAVAILABLE,
                            "model_revision": "unset"}
        page["took_ms"] = int((time.time() - started) * 1000)
        return page

    def catalogued_page(self, request, page_size, canonical_ids=None,
                        now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        size = max(1, min(int(page_size), self.limits.page_rows))
        return self._catalog_page(request, self.current(now), size, now,
                                  sort=SORT_FIRST_CATALOGUED,
                                  active_only=True,
                                  canonical_ids=canonical_ids)

    def _catalog_page(self, request, view, page_size, now_ms,
                      sort=SORT_LAST_OBSERVED, active_only=False,
                      canonical_ids=None):
        activity = view.cutoff or now_ms
        horizon = self.active_ms if active_only else self.retention_ms
        filters = [{"term": {"kind": contract.KIND_CATALOG_TEMPLATE}},
                   {"range": {"last_observed": {
                       "gte": activity - horizon + 1}}}]
        if canonical_ids is not None:
            filters.append({"terms": {"canonical_id": list(canonical_ids)}})
        query = (request.get("query") or "").strip()
        if query:
            filters.append({"bool": {"should": [
                {"term": {"version_id": query}},
                {"term": {"canonical_id": query}},
                {"match_phrase": {"template": query}},
                {"match_phrase": {"normalized": query}},
            ], "minimum_should_match": 1}})
        for field, name in (("family", "family"), ("program", "programs"),
                            ("host", "origin_hosts"),
                            ("severity", "severity_norm")):
            values = request.get(field) or []
            if values:
                filters.append({"terms": {name: list(values)}})
        if sort == SORT_FIRST_CATALOGUED:
            order = [{"first_catalogued": {"order": "desc"}},
                     {"version_id": {"order": "asc"}}]
        else:
            order = [{"last_observed": {"order": "desc"}},
                     {"version_id": {"order": "asc"}}]
        body = {
            "size": page_size,
            "track_total_hits": True,
            "sort": order,
            "query": {"bool": {"filter": filters}},
            "_source": CATALOG_FIELDS,
        }
        after = request.get("after")
        if after is not None:
            body["search_after"] = list(after)
        with self._detail_query():
            result = self._transport.search(self._catalog_index, body) or {}
        hits = _hits(result)
        rows = [self._catalog_row(hit.get("_source") or {}, view, activity)
                for hit in hits]
        matched, relation = _matched_total(result, rows)
        return {
            "rows": rows,
            "page_size": page_size,
            "after": hits[-1].get("sort") if hits else None,
            "has_more": len(hits) == page_size,
            "total": matched,
            "total_relation": relation,
        }

    def catalog_search(self, body, page_size, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        size = max(1, min(int(page_size), self.limits.page_rows))
        view = self.current(now)
        activity = view.cutoff or now
        with self._detail_query():
            result = self._transport.search(self._catalog_index, body) or {}
        hits = _hits(result)
        rows = [self._catalog_row(hit.get("_source") or {}, view, activity)
                for hit in hits]
        matched, relation = _matched_total(result, rows)
        return {
            "rows": rows,
            "page_size": size,
            "after": hits[-1].get("sort") if hits else None,
            "has_more": len(hits) == size,
            "total": matched,
            "total_relation": relation,
        }

    def _catalog_row(self, document, view, activity_ms):
        version = document.get("version_id")
        counted = view.by_version.get(version)
        count = contract.decode_int(counted["count"]) if counted else 0
        status = _count_status(view.coverage.get(
            "status", contract.COVERAGE_UNAVAILABLE))
        row = _row(document, count, status, activity_ms, self.active_ms,
                   counted["descendants"] if counted else ())
        row["canonical_versions"] = list(view.canonical_versions.get(
            document.get("canonical_id"), (version,)))
        return row

    def detail(self, version_id, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        view = self.current(now)
        row = view.row(version_id)
        if row is None:
            row = self._catalog_detail(version_id, view, now)
        if row is None:
            raise ViewRefused(
                f"no retained definition or counted version answers to "
                f"{version_id!r}", "refused")
        canonical = view.canonical_group(row["canonical_id"])
        if not canonical["versions"]:
            canonical = {"canonical_id": row["canonical_id"],
                         "versions": [dict(row)],
                         "count": row["count"],
                         "count_status": row["count_status"]}
        return {"version": row, "canonical_group": canonical,
                "ancestors": view.ancestors(version_id)}

    def _catalog_detail(self, version_id, view, now_ms):
        body = {
            "size": 1,
            "track_total_hits": False,
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_CATALOG_TEMPLATE}},
                {"term": {"version_id": version_id}}]}},
            "_source": CATALOG_FIELDS,
        }
        with self._detail_query():
            result = self._transport.search(self._catalog_index, body) or {}
        hits = _hits(result)
        if not hits:
            return None
        return self._catalog_row(hits[0].get("_source") or {}, view,
                                 view.cutoff or now_ms)

    def lines(self, request=None, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        request = request or {}
        version_id = str(request.get("version_id") or "").strip()
        if not version_id:
            raise ViewRefused(
                "a lines request names one version identifier", "refused")
        view = self.current(now)
        row = view.row(version_id)
        if row is None:
            row = self._catalog_detail(version_id, view, now)
        if row is None:
            raise ViewRefused(
                f"no retained definition answers to {version_id!r}",
                "refused")
        try:
            limit = int(request.get("limit") or self.limits.lines_rows)
        except (TypeError, ValueError):
            limit = self.limits.lines_rows
        limit = max(1, min(limit, self.limits.lines_rows))
        include_ancestors = bool(request.get("include_ancestors"))
        every_node = bool(request.get("every_node"))
        since = _epoch_ms(request.get("since"), "since")
        until = _epoch_ms(request.get("until"), "until")
        if since is None:
            since = view.window.get("window_start") or now - self.active_ms
        if until is not None and until < since:
            raise ViewRefused("until is before since", "refused")

        expanded = [version_id] + [v for v in row["descendants"]
                                   if v != version_id]
        ancestors = view.ancestors(version_id) if include_ancestors else []
        rematch = bool(ancestors) and rematch_available()
        wanted = list(expanded) + (ancestors if rematch else [])
        notes = [LINES_NOTE]
        if include_ancestors and ancestors and not rematch:
            notes.append(ANCESTORS_UNAVAILABLE_NOTE)
        elif rematch:
            notes.append(ANCESTORS_NOTE)

        nodes = []
        if not every_node:
            nodes, truncated = self._route(wanted, since, until, now)
            if truncated:
                every_node = True
        if every_node:
            indices = [self._local_prefix + "*"]
            notes.append(EVERY_NODE_NOTE)
        else:
            indices = [self._local_prefix + node for node in nodes]
            notes.append(ROUTE_NOTE)
        indices.extend(self._shared_indices)

        window = {"gte": since}
        if until is not None:
            window["lte"] = until
        body = {
            "size": limit,
            "track_total_hits": False,
            "sort": [{contract.COLLECTOR_TIME_FIELD: {
                "order": "desc", "missing": "_last"}}],
            "query": {"bool": {"filter": [
                {"terms": {contract.TEMPLATE_VERSION_FIELD: wanted}},
                {"range": {contract.COLLECTOR_TIME_FIELD: window}}]}},
            "_source": list(RECORD_FIELDS),
        }
        with self._detail_query():
            result = self._transport.search(",".join(indices), body,
                                            ignore_unavailable=True) or {}
        expanded_set = set(expanded)
        ancestor_set = set(ancestors) if rematch else set()
        rows = []
        fetched = 0
        for hit in _hits(result):
            source = hit.get("_source") or {}
            fetched += 1
            stamp = source.get(contract.TEMPLATE_VERSION_FIELD)
            if stamp in expanded_set:
                fits = True
            elif stamp in ancestor_set:
                fits = line_fits(row["family"], row["template"],
                                 source.get("message"))
            else:
                continue
            if not fits:
                continue
            record = {name: source[name] for name in RECORD_FIELDS
                      if name in source}
            record["_id"] = hit.get("_id")
            record["_index"] = hit.get("_index")
            rows.append(record)
        return {
            "rows": rows,
            "matched": len(rows),
            "fetched": fetched,
            "truncated": fetched >= limit,
            "nodes": nodes,
            "every_node": every_node,
            "indices": indices,
            "cutoff": view.cutoff,
            "cutoff_iso": (contract.iso_utc(view.cutoff)
                           if view.cutoff else None),
            "since": since,
            "until": until,
            "expanded": expanded,
            "ancestors": ancestors,
            "ancestors_included": rematch,
            "note": " ".join(notes),
        }

    def _route(self, wanted, since, until, now_ms):
        fine_since = max(since, now_ms - contract.LEDGER_MS)
        coarse = {"gte": contract.bucket_start_ms(since,
                                                  contract.COARSE_BUCKET_MS)}
        fine = {"gte": contract.bucket_start_ms(fine_since,
                                                contract.FINE_BUCKET_MS)}
        if until is not None:
            coarse["lte"] = until
            fine["lte"] = until
        second = contract.SECOND_MS
        body = {
            "size": 0,
            "track_total_hits": False,
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_BUCKET}},
                {"nested": {"path": "counts", "query": {
                    "terms": {"counts.version_id": list(wanted)}}}},
                {"bool": {"should": [
                    {"bool": {"filter": [
                        {"term": {"resolution_seconds":
                                  contract.COARSE_BUCKET_MS // second}},
                        {"range": {"bucket_start": coarse}}]}},
                    {"bool": {"filter": [
                        {"term": {"resolution_seconds":
                                  contract.FINE_BUCKET_MS // second}},
                        {"range": {"bucket_start": fine}}]}},
                ], "minimum_should_match": 1}},
            ]}},
            "aggs": {"nodes": {"terms": {"field": "node",
                                         "size": self.limits.route_nodes}}},
        }
        index = f"{self._fine_pattern},{self._coarse_pattern}"
        with self._detail_query():
            result = self._transport.search(index, body,
                                            ignore_unavailable=True) or {}
        rows, more = _buckets(result, "nodes")
        return sorted(str(row.get("key")) for row in rows), bool(more)

    def _detail_query(self):
        return _DetailSlot(self._detail_slots)

    def detail_slot(self, timeout=5.0):
        return _DetailSlot(self._detail_slots, timeout)

    def episodes(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        interval = self.limits.episode_interval_ms
        with self._lock:
            if (self._episodes_attempt_ms is not None
                    and now - self._episodes_attempt_ms < interval):
                return dict(self._episodes)
            self._episodes_attempt_ms = now
        body = {
            "size": self.limits.episode_rows,
            "track_total_hits": False,
            "sort": [{"episode_start": {"order": "desc"}}],
            "query": {"bool": {"filter": [{"term": {"state": "firing"}}]}},
            "_source": EPISODE_FIELDS,
        }
        try:
            hits = _hits(self._transport.search(self._incidents_index, body))
        except Exception as exc:
            log(f"incident summary unreadable: {exc!r}")
            with self._lock:
                summary = dict(self._episodes)
            summary["stale"] = True
            return summary
        episodes = []
        for hit in hits:
            document = hit.get("_source") or {}
            episodes.append({
                "incident_id": document.get("incident_id") or "",
                "grouping_key": document.get("group_id") or "",
                "alertname": document.get("alertname") or "",
                "entity_kind": document.get("entity_kind") or "",
                "entity_id": document.get("entity_id") or "",
                "severity": document.get("severity") or "",
                "state": document.get("state") or "",
                "episode_start": document.get("episode_start") or 0,
                "title": document.get("title") or "",
                "diagnosis": document.get("diagnosis") or "",
                "action": document.get("operator_action") or "",
                "affected": list(document.get("entity_samples") or []),
                "signals": list(document.get("signal_ids") or []),
            })
        with self._lock:
            self._episodes = {"episodes": episodes, "refreshed_at": now,
                              "stale": False}
            return dict(self._episodes)


class _DetailSlot:
    def __init__(self, semaphore, timeout=5.0):
        self._semaphore = semaphore
        self._timeout = timeout

    def __enter__(self):
        if not self._semaphore.acquire(timeout=self._timeout):
            raise ViewRefused(
                "this server already runs the two detail queries it allows; "
                "try again once one of them finishes", "refused")
        return self

    def __exit__(self, kind, value, trace):
        self._semaphore.release()
        return False


def decorate_page(page, view):
    page["window_end"] = view.window.get("window_end")
    page["window_end_iso"] = view.window.get("window_end_iso")
    page["coverage"] = {
        "status": view.coverage.get("status", contract.COVERAGE_UNAVAILABLE),
        "behind": list(view.coverage.get("behind") or []),
        "idle": list(view.coverage.get("idle") or []),
    }
    return page


def _active_only(predicate):
    if predicate is None:
        return lambda row: row["active"]
    return lambda row: row["active"] and predicate(row)


def _predicate(request):
    query = (request.get("query") or "").strip().lower()
    families = set(request.get("family") or [])
    programs = set(request.get("program") or [])
    hosts = set(request.get("host") or [])
    severities = set(request.get("severity") or [])
    watched_only = bool(request.get("watched_only"))
    if not (query or families or programs or hosts or severities
            or watched_only):
        return None

    def matches(row):
        if watched_only and not row["watched"]:
            return False
        if families and row["family"] not in families:
            return False
        if severities and row["severity_norm"] not in severities:
            return False
        if programs and not programs.intersection(row["programs"]):
            return False
        if hosts and not hosts.intersection(row["origin_hosts"]):
            return False
        if query:
            if (query in row["template"].lower()
                    or query in row["normalized"]
                    or query == row["version_id"]
                    or query == row["canonical_id"]):
                return True
            return False
        return True

    return matches
