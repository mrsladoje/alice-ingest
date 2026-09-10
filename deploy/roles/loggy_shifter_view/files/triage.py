import base64
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.environ.get(
    "ALICE_SHARED_PATH", "/opt/loggy/shared"))

import template_contract as contract                          # noqa: E402

TRIAGE_INDEX = os.environ.get("SHIFTER_TRIAGE_INDEX", contract.TRIAGE_INDEX)
QUERIES_INDEX = os.environ.get("SHIFTER_QUERIES_INDEX", contract.QUERIES_INDEX)

LABEL_KNOWN_GOOD = "known_good"
LABEL_KNOWN_BAD = "known_bad"
LABEL_NEEDS_REVIEW = "needs_review"
LABEL_NOISY = "noisy"
LABEL_WATCHED = "watched"
LABELS = (LABEL_KNOWN_GOOD, LABEL_KNOWN_BAD, LABEL_NEEDS_REVIEW, LABEL_NOISY,
          LABEL_WATCHED)

SCOPE_REVIEWED = "reviewed"
SCOPE_BROADER_VERSION = "broader_version"
SCOPE_NEW_SOURCE = "new_source_scope"

AUTHOR_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

NOTE_MAX_CHARS = int(
    os.environ.get("SHIFTER_TEMPLATE_NOTE_MAX_CHARS", "2000"))
HISTORY_MAX = int(
    os.environ.get("SHIFTER_TEMPLATE_LABEL_HISTORY_MAX", "50"))
WATCHED_MAX = int(os.environ.get("SHIFTER_TEMPLATE_WATCHED_MAX", "100"))
LABEL_DOCUMENT_MAX_BYTES = int(
    os.environ.get("SHIFTER_TEMPLATE_LABEL_BYTES", "65536"))
LABEL_CACHE_MAX_BYTES = int(
    os.environ.get("SHIFTER_TEMPLATE_LABEL_CACHE_BYTES",
                   str(8 * 1024 * 1024)))
LABEL_CACHE_INTERVAL_MS = int(
    os.environ.get("SHIFTER_TEMPLATE_LABEL_CACHE_MS", "60000"))

MAX_REVIEWED_VERSIONS = 64
MAX_REVIEWED_SCOPE = 64
READ_ROWS = 32
CACHE_PAGE = 200
CACHE_PAGES = 25

QUERY_TEXT_MAX = 512
QUERY_RESULTS_MAX = 50
QUERY_OPENED_MAX = 200
QUERY_RETENTION_MS = contract.QUERY_RETENTION_MS

CACHE_FIELDS = ["label_id", "version_id", "reviewed_version_ids", "label",
                "author", "watched", "reviewed_programs",
                "reviewed_origin_hosts", "revision", "updated_at"]

DOCUMENT_FIELDS = ["kind", "schema_version", "label_id", "version_id",
                   "reviewed_version_ids", "family", "template",
                   "label", "note", "author", "watched", "reviewed_programs",
                   "reviewed_origin_hosts", "revision", "created_at",
                   "updated_at", "history"]


def log(msg):
    print(f"[triage] {msg}", flush=True)


class TriageRefused(Exception):
    def __init__(self, message, status="refused"):
        super().__init__(message)
        self.status = status


class LabelConflict(Exception):
    def __init__(self, message, label=None):
        super().__init__(message)
        self.label = label


class Transport:
    def __init__(self, url, user="", password="", verify=True, timeout=45.0):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.verify = verify
        self.timeout = timeout

    def _context(self):
        if not self.url.startswith("https") or self.verify:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _open(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"{self.url}{path}", data=data,
                                         method=method)
        request.add_header("Content-Type", "application/json")
        if self.user:
            raw = f"{self.user}:{self.password}".encode("utf-8")
            request.add_header(
                "Authorization",
                "Basic " + base64.b64encode(raw).decode("ascii"))
        with urllib.request.urlopen(request, timeout=self.timeout,
                                    context=self._context()) as response:
            payload = response.read().decode("utf-8", "replace")
        return json.loads(payload) if payload else {}

    def get(self, index, doc_id):
        path = f"/{index}/_doc/{urllib.parse.quote(doc_id, safe='')}"
        try:
            return self._open("GET", path)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def put(self, index, doc_id, document, seq_no=None, primary_term=None,
            create=False, refresh="wait_for"):
        quoted = urllib.parse.quote(doc_id, safe="")
        if create:
            path = f"/{index}/_create/{quoted}?refresh={refresh}"
        else:
            path = f"/{index}/_doc/{quoted}?refresh={refresh}"
            if seq_no is not None and primary_term is not None:
                path += f"&if_seq_no={seq_no}&if_primary_term={primary_term}"
        return self._open("PUT", path, document)

    def search(self, index, body):
        return self._open("POST", f"/{index}/_search", body)


def _text(value, name, limit):
    if not isinstance(value, str):
        raise TriageRefused(f"{name} must be text")
    value = value.strip()
    if not value:
        raise TriageRefused(f"{name} must not be empty")
    if len(value) > limit:
        raise TriageRefused(
            f"{name} holds {len(value)} characters and this server accepts "
            f"{limit}; shorten it rather than let the server cut it")
    return value


def _string_list(values, name, limit):
    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise TriageRefused(f"{name} must be a list of strings")
    out = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TriageRefused(f"every {name} entry must be text")
        out.append(value.strip())
    if len(out) > limit:
        raise TriageRefused(
            f"{name} holds {len(out)} entries and this server accepts "
            f"{limit}")
    return sorted(set(out))


def author_slug(value):
    slug = _text(value, "author", 32).lower()
    if not AUTHOR_SLUG.match(slug):
        raise TriageRefused(
            "an author name uses lower-case letters, digits and dashes, at "
            "most 32 characters; it is self-reported because this server has "
            "no login")
    return slug


def label_id(version_id, author):
    return f"label:{version_id}:{author}"


def scope_state(row, document):
    reviewed = set(document.get("reviewed_version_ids") or ())
    if row.get("version_id") not in reviewed:
        return SCOPE_BROADER_VERSION
    programs = set(document.get("reviewed_programs") or ())
    hosts = set(document.get("reviewed_origin_hosts") or ())
    if not set(row.get("programs") or ()) <= programs:
        return SCOPE_NEW_SOURCE
    if not set(row.get("origin_hosts") or ()) <= hosts:
        return SCOPE_NEW_SOURCE
    return SCOPE_REVIEWED


def review(row, documents):
    applied = None
    scope = None
    watched = False
    labels = set()
    states = []
    for document in documents:
        if document.get("watched"):
            watched = True
        value = document.get("label")
        if value:
            labels.add(value)
        state = scope_state(row, document)
        states.append(state)
        if state == SCOPE_REVIEWED and applied is None:
            applied = value
    if SCOPE_REVIEWED in states:
        scope = SCOPE_REVIEWED
    elif SCOPE_NEW_SOURCE in states:
        scope = SCOPE_NEW_SOURCE
    elif states:
        scope = SCOPE_BROADER_VERSION
    return {
        "label": applied,
        "label_scope": scope,
        "label_conflicts": max(0, len(labels) - 1),
        "watched": watched,
        "reviewed_labels": sorted(labels),
    }


class LabelStore:
    def __init__(self, transport, index=TRIAGE_INDEX, clock=None,
                 note_max=NOTE_MAX_CHARS, history_max=HISTORY_MAX,
                 watched_max=WATCHED_MAX,
                 document_max_bytes=LABEL_DOCUMENT_MAX_BYTES,
                 cache_max_bytes=LABEL_CACHE_MAX_BYTES,
                 cache_interval_ms=LABEL_CACHE_INTERVAL_MS):
        self._transport = transport
        self._index = index
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.note_max = max(1, int(note_max))
        self.history_max = max(1, int(history_max))
        self.watched_max = max(1, int(watched_max))
        self.document_max_bytes = max(1024, int(document_max_bytes))
        self.cache_max_bytes = max(1024, int(cache_max_bytes))
        self.cache_interval_ms = max(1000, int(cache_interval_ms))
        self._lock = threading.Lock()
        self._cache = {}
        self._cache_bytes = 0
        self._cache_truncated = False
        self._cache_refreshed_ms = None
        self.refreshes = 0
        self.last_error = ""

    def now_ms(self):
        return int(self._clock())

    def cache_status(self):
        with self._lock:
            return {
                "versions": len(self._cache),
                "bytes": self._cache_bytes,
                "truncated": self._cache_truncated,
                "refreshed_at": self._cache_refreshed_ms or 0,
                "last_error": self.last_error,
            }

    def cached(self, version_id):
        with self._lock:
            return [dict(doc) for doc in self._cache.get(version_id, ())]

    def watched_ids(self):
        with self._lock:
            return {version for version, documents in self._cache.items()
                    if any(document.get("watched")
                           for document in documents)}

    def decorate(self, rows, coverers=None):
        for row in rows:
            version = row.get("version_id")
            documents = self.cached(version)
            if coverers is not None:
                for wider in coverers(version):
                    documents.extend(self.cached(wider))
            if not documents:
                row["label"] = None
                row["label_scope"] = None
                row["label_conflicts"] = 0
                row["watched"] = False
                continue
            verdict = review(row, documents)
            row["label"] = verdict["label"]
            row["label_scope"] = verdict["label_scope"]
            row["label_conflicts"] = verdict["label_conflicts"]
            row["watched"] = verdict["watched"]
        return rows

    def maybe_refresh(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        if (self._cache_refreshed_ms is not None
                and now - self._cache_refreshed_ms < self.cache_interval_ms):
            return False
        self.refresh(now)
        return True

    def refresh(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        cache = {}
        used = 0
        truncated = False
        after = None
        for _ in range(CACHE_PAGES):
            body = {
                "size": CACHE_PAGE,
                "track_total_hits": False,
                "sort": [{"label_id": {"order": "asc"}}],
                "query": {"bool": {"filter": [
                    {"term": {"kind": contract.KIND_TRIAGE_LABEL}}]}},
                "_source": list(CACHE_FIELDS),
            }
            if after is not None:
                body["search_after"] = list(after)
            result = self._transport.search(self._index, body) or {}
            hits = ((result.get("hits") or {}).get("hits") or [])
            if not hits:
                break
            for hit in hits:
                document = hit.get("_source") or {}
                version = document.get("version_id")
                if not version:
                    continue
                used += contract.encoded_size(document)
                if used > self.cache_max_bytes:
                    truncated = True
                    break
                cache.setdefault(version, []).append(document)
            if truncated:
                break
            after = hits[-1].get("sort")
            if after is None or len(hits) < CACHE_PAGE:
                break
        else:
            truncated = True
        with self._lock:
            self._cache = cache
            self._cache_bytes = used
            self._cache_truncated = truncated
            self._cache_refreshed_ms = now
            self.refreshes += 1
            self.last_error = ""
        return cache

    def read(self, version_id):
        version = _text(version_id, "version_id", 128)
        body = {
            "size": READ_ROWS,
            "track_total_hits": False,
            "sort": [{"label_id": {"order": "asc"}}],
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_TRIAGE_LABEL}},
                {"term": {"version_id": version}}]}},
            "_source": list(DOCUMENT_FIELDS),
        }
        result = self._transport.search(self._index, body) or {}
        hits = ((result.get("hits") or {}).get("hits") or [])
        return [hit.get("_source") or {} for hit in hits]

    def watched_versions(self, exclude=None):
        body = {
            "size": 0,
            "track_total_hits": False,
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_TRIAGE_LABEL}},
                {"term": {"watched": True}}]}},
            "aggs": {"versions": {"terms": {
                "field": "version_id", "size": self.watched_max + 1}}},
        }
        result = self._transport.search(self._index, body) or {}
        buckets = ((result.get("aggregations") or {}).get("versions")
                   or {}).get("buckets") or []
        keys = {bucket.get("key") for bucket in buckets}
        keys.discard(exclude)
        return keys

    def _fetch(self, document_id):
        found = self._transport.get(self._index, document_id)
        if not found or not found.get("found", True):
            return None, None, None
        return (found.get("_source") or {}, found.get("_seq_no"),
                found.get("_primary_term"))

    def _stored(self, version_id, author):
        for document in self.read(version_id):
            if document.get("author") == author:
                return document
        return None

    def write(self, request, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        if not isinstance(request, dict):
            raise TriageRefused("a label write needs a JSON object")
        version = _text(request.get("version_id"), "version_id", 128)
        author = author_slug(request.get("author"))
        label = request.get("label")
        if label not in LABELS:
            raise TriageRefused(
                "a label is one of " + ", ".join(LABELS))
        note = request.get("note") or ""
        if not isinstance(note, str):
            raise TriageRefused("a note must be text")
        if len(note) > self.note_max:
            raise TriageRefused(
                f"this note holds {len(note)} characters and this server "
                f"accepts {self.note_max}; shorten it rather than let the "
                f"server cut it")
        versions = _string_list(request.get("reviewed_version_ids"),
                                "reviewed_version_ids", MAX_REVIEWED_VERSIONS)
        if not versions:
            raise TriageRefused(
                "a label records the exact version identifiers it reviewed; "
                "send at least one")
        programs = _string_list(request.get("reviewed_programs"),
                                "reviewed_programs", MAX_REVIEWED_SCOPE)
        hosts = _string_list(request.get("reviewed_origin_hosts"),
                             "reviewed_origin_hosts", MAX_REVIEWED_SCOPE)
        watched = bool(request.get("watched"))
        revision = request.get("revision")
        if revision is not None and (isinstance(revision, bool)
                                     or not isinstance(revision, int)):
            raise TriageRefused("revision must be the integer you read")

        document_id = label_id(version, author)
        stored, seq_no, primary_term = self._fetch(document_id)
        if stored is None:
            if revision:
                raise LabelConflict(
                    "this label was removed while you were editing it", None)
            family = _text(request.get("family"), "family", 64)
            template = _text(request.get("template"), "template", 4096)
            if contract.version_id(family, template) != version:
                raise TriageRefused(
                    "the family and template text do not hash to the version "
                    "identifier this label names; the label would describe "
                    "another template")
            created = now
            history = []
            next_revision = 1
        else:
            if stored.get("revision") != revision:
                raise LabelConflict(
                    "this label changed while you were editing it", stored)
            family = stored.get("family")
            template = stored.get("template")
            created = stored.get("created_at") or now
            history = list(stored.get("history") or ())
            next_revision = int(stored.get("revision") or 0) + 1

        if watched and (not stored or not stored.get("watched")):
            watching = self.watched_versions(exclude=version)
            if len(watching) >= self.watched_max:
                raise TriageRefused(
                    f"{self.watched_max} templates are already watched, which "
                    f"is the limit this server enforces; unwatch one first")

        history.append({
            "revision": next_revision,
            "label": label,
            "author": author,
            "watched": watched,
            "note": note,
            "reviewed_version_ids": versions,
            "at": now,
        })
        history = history[-self.history_max:]
        document = {
            "kind": contract.KIND_TRIAGE_LABEL,
            "schema_version": contract.SCHEMA_VERSION,
            "label_id": document_id,
            "version_id": version,
            "reviewed_version_ids": versions,
            "family": family,
            "template": template,
            "label": label,
            "note": note,
            "author": author,
            "watched": watched,
            "reviewed_programs": programs,
            "reviewed_origin_hosts": hosts,
            "revision": next_revision,
            "created_at": created,
            "updated_at": now,
            "history": history,
        }
        while (contract.encoded_size(document) > self.document_max_bytes
               and len(document["history"]) > 1):
            document["history"] = document["history"][1:]
        size = contract.encoded_size(document)
        if size > self.document_max_bytes:
            raise TriageRefused(
                f"this label document encodes to {size} bytes, above the "
                f"{self.document_max_bytes} byte ceiling, with one revision "
                f"of history left; shorten the note")

        try:
            self._transport.put(self._index, document_id, document,
                                seq_no=seq_no, primary_term=primary_term,
                                create=stored is None)
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
            current = self._stored(version, author)
            raise LabelConflict(
                "this label changed while you were editing it", current)
        with self._lock:
            cached = [doc for doc in self._cache.get(version, ())
                      if doc.get("author") != author]
            cached.append({field: document[field] for field in CACHE_FIELDS})
            self._cache[version] = cached
        return document


class QueryLog:
    def __init__(self, transport, index=QUERIES_INDEX, clock=None,
                 retention_ms=QUERY_RETENTION_MS):
        self._transport = transport
        self._index = index
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.retention_ms = int(retention_ms)
        self.last_error = ""
        self.written = 0

    def now_ms(self):
        return int(self._clock())

    def new_id(self):
        return "q-" + uuid.uuid4().hex[:12]

    def record(self, query_text, mode, include_inactive, model_revision,
               corpus_revision, result_ids, viewer="", latency_ms=0,
               now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        query_id = self.new_id()
        ordered = [str(value)
                   for value in list(result_ids)[:QUERY_RESULTS_MAX]]
        document = {
            "kind": contract.KIND_QUERY_EVENT,
            "schema_version": contract.SCHEMA_VERSION,
            "query_id": query_id,
            "query_text": str(query_text or "")[:QUERY_TEXT_MAX],
            "mode": str(mode or ""),
            "include_inactive": bool(include_inactive),
            "model_revision": str(model_revision or "unset"),
            "corpus_revision": str(corpus_revision or "unset"),
            "result_ids": ordered,
            "result_ranks": {value: rank
                             for rank, value in enumerate(ordered, start=1)},
            "opened_ids": [],
            "viewer": str(viewer or "")[:64],
            "latency_ms": int(latency_ms),
            "issued_at": now,
            "updated_at": now,
        }
        self._transport.put(self._index, query_id, document, create=True,
                            refresh="false")
        self.written += 1
        return query_id

    def opened(self, query_id, version_id, rank=None, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        query = _text(query_id, "query_id", 64)
        version = _text(version_id, "version_id", 128)
        if rank is not None and (isinstance(rank, bool)
                                 or not isinstance(rank, int)):
            raise TriageRefused("rank must be an integer")
        for _ in range(2):
            found = self._transport.get(self._index, query)
            if not found or not found.get("found", True):
                raise TriageRefused(
                    "this search is not in the query history, so the click "
                    "cannot be recorded against it")
            document = dict(found.get("_source") or {})
            opened = list(document.get("opened_ids") or ())
            if version not in opened:
                if len(opened) >= QUERY_OPENED_MAX:
                    return document
                opened.append(version)
            document["opened_ids"] = opened
            document["updated_at"] = now
            try:
                self._transport.put(self._index, query, document,
                                    seq_no=found.get("_seq_no"),
                                    primary_term=found.get("_primary_term"),
                                    refresh="false")
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                continue
            return document
        raise TriageRefused(
            "this search record was changed twice while the click was being "
            "recorded; the click is not stored", "unavailable")

    def expiry_query(self, now_ms=None):
        now = self.now_ms() if now_ms is None else now_ms
        return contract.expired_queries(now - self.retention_ms)
