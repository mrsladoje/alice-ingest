import array
import collections
import math
import operator
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.environ.get("ALICE_SHARED_PATH",
                                  "/opt/loggy/shared"))

import template_contract as contract                        # noqa: E402

STATUS_READY = "ready"
STATUS_BUILDING = "building"
STATUS_UNAVAILABLE = "unavailable"
STATUSES = (STATUS_READY, STATUS_BUILDING, STATUS_UNAVAILABLE)

MODE_TEXT = "text"
MODE_SEMANTIC = "semantic"
MODES = (MODE_TEXT, MODE_SEMANTIC)

BACKEND_NONE = "none"
BACKEND_MODEL2VEC = "model2vec"
BACKEND_SENTENCE_TRANSFORMERS = "sentence_transformers"
BACKENDS = (BACKEND_NONE, BACKEND_MODEL2VEC, BACKEND_SENTENCE_TRANSFORMERS)

REASON_NONE = ""
REASON_DISABLED = "semantic_search_disabled"
REASON_NO_SNAPSHOT = "no_active_snapshot"
REASON_NO_CAPACITY = "no_vector_capacity"
REASON_MODEL_MISSING = "model_unavailable"
REASON_MODEL_DIMENSIONS = "model_dimensions_mismatch"
REASON_ENCODE_FAILED = "encode_failed"
REASON_ENCODER_BUSY = "encoder_busy"
REASON_VERSION_LIMIT = "version_limit_reached"
REASON_VECTOR_BYTES_LIMIT = "vector_bytes_limit_reached"
REASON_EMPTY_QUERY = "empty_query"
REASON_REFRESH_RUNNING = "refresh_running"
REASON_NOT_EMBEDDED = "version_not_embedded"

REASON_TEXT = {
    REASON_NONE: "",
    REASON_DISABLED: "semantic search is turned off on this server",
    REASON_NO_SNAPSHOT: "semantic search has no vectors yet",
    REASON_NO_CAPACITY: "the configured vector memory holds no version",
    REASON_MODEL_MISSING: "the retrieval model did not load",
    REASON_MODEL_DIMENSIONS: "the model emits a different number of dimensions "
                             "than the memory budget was set for",
    REASON_ENCODE_FAILED: "the encoder failed",
    REASON_ENCODER_BUSY: "the single encoding slot was busy",
    REASON_VERSION_LIMIT: "the active version limit was reached, so the "
                          "searched corpus is smaller than the active catalog",
    REASON_VECTOR_BYTES_LIMIT: "the vector memory limit was reached, so the "
                               "searched corpus is smaller than the active "
                               "catalog",
    REASON_EMPTY_QUERY: "the query was empty",
    REASON_REFRESH_RUNNING: "a refresh is already running",
    REASON_NOT_EMBEDDED: "this template version holds no vector in the "
                         "searched corpus",
}

VECTOR_VALUE_BYTES = 4
VERSION_RESIDENT_BYTES = 656
QUERY_SCRATCH_BYTES = 96

DEFAULT_DIMENSIONS = 512
DEFAULT_MAX_VERSIONS = 20000
DEFAULT_MAX_VECTOR_BYTES = 16777216
DEFAULT_BATCH = 256
DEFAULT_MAX_RESULTS = 50
DEFAULT_MAX_NEIGHBOURS = 5
DEFAULT_MAX_QUERY_CHARS = 512
DEFAULT_MODEL_REVISION = "unset"
DEFAULT_QUERY_WAIT_SECONDS = 2.0
DEFAULT_ENCODE_WAIT_SECONDS = 60.0
MAX_ENCODERS = 1
POLL_SECONDS = 1.0

VERSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,32}:[0-9a-f]{24}$")

Version = collections.namedtuple(
    "Version", ("version_id", "template", "last_observed"))

Hit = collections.namedtuple("Hit", ("version_id", "score"))

Snapshot = collections.namedtuple("Snapshot", (
    "status", "coverage", "reason", "detail", "versions", "versions_total",
    "vector_bytes", "vector_bytes_limit", "capacity", "truncated",
    "model_revision", "built_at_ms", "took_ms", "embedded"))

SearchResult = collections.namedtuple("SearchResult", (
    "status", "coverage", "reason", "detail", "hits", "truncated", "versions",
    "versions_total", "vector_bytes", "model_revision", "limit", "took_ms"))


class SemanticError(Exception):

    def __init__(self, reason, detail=""):
        Exception.__init__(self, REASON_TEXT.get(reason, reason))
        self.reason = reason
        self.detail = detail


class EncoderUnavailable(SemanticError):
    pass


class LimitReached(SemanticError):
    pass


def _flag(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _whole(value, default, floor=0):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= floor else default


def _number(value, default, floor=0.0):
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= floor else default


def _clamp(value, floor, ceiling):
    return max(floor, min(int(value), ceiling))


class Config(object):

    def __init__(self, enabled=False, backend=BACKEND_NONE, model_path="",
                 model_revision=DEFAULT_MODEL_REVISION,
                 corpus_revision="unset", dimensions=DEFAULT_DIMENSIONS,
                 max_versions=DEFAULT_MAX_VERSIONS,
                 max_vector_bytes=DEFAULT_MAX_VECTOR_BYTES,
                 batch_size=DEFAULT_BATCH, encoders=MAX_ENCODERS,
                 max_results=DEFAULT_MAX_RESULTS,
                 max_query_chars=DEFAULT_MAX_QUERY_CHARS,
                 query_wait_seconds=DEFAULT_QUERY_WAIT_SECONDS,
                 encode_wait_seconds=DEFAULT_ENCODE_WAIT_SECONDS,
                 active_ms=contract.ACTIVE_MS,
                 retention_ms=contract.DEFINITION_RETENTION_MS):
        self.enabled = bool(enabled)
        self.backend = backend if backend in BACKENDS else BACKEND_NONE
        self.model_path = str(model_path or "")
        self.model_revision = str(model_revision or DEFAULT_MODEL_REVISION)
        self.corpus_revision = str(corpus_revision or "unset")
        self.dimensions = max(1, int(dimensions))
        self.max_versions = max(0, int(max_versions))
        self.max_vector_bytes = max(0, int(max_vector_bytes))
        self.batch_size = max(1, int(batch_size))
        self.encoders = _clamp(encoders, 1, MAX_ENCODERS)
        self.max_results = max(1, int(max_results))
        self.max_query_chars = max(1, int(max_query_chars))
        self.query_wait_seconds = max(0.0, float(query_wait_seconds))
        self.encode_wait_seconds = max(0.0, float(encode_wait_seconds))
        self.active_ms = max(1, int(active_ms))
        self.retention_ms = max(1, int(retention_ms))

    @property
    def vector_row_bytes(self):
        return self.dimensions * VECTOR_VALUE_BYTES

    @property
    def vector_capacity(self):
        return min(self.max_versions,
                   self.max_vector_bytes // self.vector_row_bytes)

    @property
    def binding_limit(self):
        if self.max_versions <= self.max_vector_bytes // self.vector_row_bytes:
            return REASON_VERSION_LIMIT
        return REASON_VECTOR_BYTES_LIMIT

    def resident_ceiling(self):
        versions = self.vector_capacity
        return int(versions * (self.vector_row_bytes + VERSION_RESIDENT_BYTES)
                   + self.max_results * QUERY_SCRATCH_BYTES)

    @classmethod
    def from_environment(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            enabled=_flag(env.get("SHIFTER_SEMANTIC_ENABLED"), False),
            backend=env.get("SHIFTER_SEMANTIC_BACKEND", BACKEND_NONE),
            model_path=env.get("SHIFTER_SEMANTIC_MODEL_PATH", ""),
            model_revision=env.get("SHIFTER_SEMANTIC_MODEL_REVISION",
                                   DEFAULT_MODEL_REVISION),
            corpus_revision=env.get("SHIFTER_SEMANTIC_CORPUS_REVISION",
                                    "unset"),
            dimensions=_whole(env.get("SHIFTER_SEMANTIC_DIMENSIONS"),
                              DEFAULT_DIMENSIONS, 1),
            max_versions=_whole(env.get("SHIFTER_SEMANTIC_MAX_VERSIONS"),
                                DEFAULT_MAX_VERSIONS),
            max_vector_bytes=_whole(env.get("SHIFTER_VECTOR_CACHE_BYTES"),
                                    DEFAULT_MAX_VECTOR_BYTES),
            batch_size=_whole(env.get("SHIFTER_SEMANTIC_BATCH"),
                              DEFAULT_BATCH, 1),
            encoders=_whole(env.get("SHIFTER_TEMPLATE_ENCODERS"),
                            MAX_ENCODERS, 1),
            max_results=_whole(env.get("SHIFTER_TEMPLATE_PAGE_ROWS"),
                               DEFAULT_MAX_RESULTS, 1),
            max_query_chars=_whole(env.get("SHIFTER_SEMANTIC_QUERY_CHARS"),
                                   DEFAULT_MAX_QUERY_CHARS, 1),
            query_wait_seconds=_number(
                env.get("SHIFTER_SEMANTIC_QUERY_WAIT_SECONDS"),
                DEFAULT_QUERY_WAIT_SECONDS),
            encode_wait_seconds=_number(
                env.get("SHIFTER_SEMANTIC_ENCODE_WAIT_SECONDS"),
                DEFAULT_ENCODE_WAIT_SECONDS),
            active_ms=_whole(env.get("SHIFTER_ACTIVE_DAYS"), 0, 1) * 86400000
            or contract.ACTIVE_MS,
            retention_ms=_whole(env.get("SHIFTER_DEFINITION_RETENTION_DAYS"),
                                0, 1) * 86400000
            or contract.DEFINITION_RETENTION_MS)


def unit_vector(values, dimensions):
    if len(values) != dimensions:
        raise EncoderUnavailable(
            REASON_MODEL_DIMENSIONS,
            "a vector of %d values arrived where %d were expected"
            % (len(values), dimensions))
    total = 0.0
    for value in values:
        total += float(value) * float(value)
    if total <= 0.0:
        return array.array("f", [0.0] * dimensions)
    norm = math.sqrt(total)
    return array.array("f", [float(value) / norm for value in values])


class VectorStore(object):

    def __init__(self, dimensions, capacity):
        self.dimensions = max(1, int(dimensions))
        self.capacity = max(0, int(capacity))
        self._rows = {}
        self._ids = []
        self._values = array.array("f")

    def __len__(self):
        return len(self._ids)

    def __contains__(self, version_id):
        return version_id in self._rows

    @property
    def vector_bytes(self):
        return len(self._values) * self._values.itemsize

    def ids(self):
        return tuple(self._ids)

    def vector(self, version_id):
        row = self._rows.get(version_id)
        if row is None:
            return None
        dimensions = self.dimensions
        return self._values[row * dimensions:(row + 1) * dimensions]

    def add(self, version_id, values):
        vector = unit_vector(values, self.dimensions)
        row = self._rows.get(version_id)
        dimensions = self.dimensions
        if row is not None:
            self._values[row * dimensions:(row + 1) * dimensions] = vector
            return False
        if len(self._ids) >= self.capacity:
            raise LimitReached(
                REASON_VECTOR_BYTES_LIMIT,
                "the store holds %d versions and its capacity is %d"
                % (len(self._ids), self.capacity))
        self._rows[version_id] = len(self._ids)
        self._ids.append(version_id)
        self._values.extend(vector)
        return True

    def discard(self, version_id):
        row = self._rows.pop(version_id, None)
        if row is None:
            return False
        dimensions = self.dimensions
        last = len(self._ids) - 1
        if row != last:
            moved = self._ids[last]
            self._ids[row] = moved
            self._rows[moved] = row
            self._values[row * dimensions:(row + 1) * dimensions] = (
                self._values[last * dimensions:(last + 1) * dimensions])
        self._ids.pop()
        del self._values[last * dimensions:]
        return True

    def clear(self):
        self._rows = {}
        self._ids = []
        self._values = array.array("f")

    def search(self, query, limit, allowed=None):
        dimensions = self.dimensions
        values = self._values
        multiply = operator.mul
        scored = []
        for row, version_id in enumerate(self._ids):
            if allowed is not None and version_id not in allowed:
                continue
            scored.append((version_id, sum(map(
                multiply, values[row * dimensions:(row + 1) * dimensions],
                query))))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:max(0, int(limit))]


class Encoder(object):

    def __init__(self, model, dimensions, revision, call):
        self.model = model
        self.dimensions = int(dimensions)
        self.revision = str(revision)
        self._call = call

    def encode(self, texts):
        texts = list(texts)
        if not texts:
            return []
        rows = self._call(texts)
        vectors = []
        for row in rows:
            values = [float(value) for value in row]
            if len(values) != self.dimensions:
                raise EncoderUnavailable(
                    REASON_MODEL_DIMENSIONS,
                    "the model returned %d dimensions and %d were configured"
                    % (len(values), self.dimensions))
            vectors.append(values)
        if len(vectors) != len(texts):
            raise EncoderUnavailable(
                REASON_ENCODE_FAILED,
                "the model returned %d vectors for %d texts"
                % (len(vectors), len(texts)))
        return vectors


def _model2vec_encoder(config):
    try:
        from model2vec import StaticModel
    except Exception as exc:
        raise EncoderUnavailable(REASON_MODEL_MISSING, repr(exc))
    try:
        model = StaticModel.from_pretrained(config.model_path)
    except Exception as exc:
        raise EncoderUnavailable(REASON_MODEL_MISSING, repr(exc))

    def call(texts):
        return model.encode(texts)

    return model, int(getattr(model, "dim", config.dimensions)), call


def _sentence_transformers_encoder(config):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise EncoderUnavailable(REASON_MODEL_MISSING, repr(exc))
    try:
        model = SentenceTransformer(config.model_path, device="cpu")
    except Exception as exc:
        raise EncoderUnavailable(REASON_MODEL_MISSING, repr(exc))

    def call(texts):
        return model.encode(texts, convert_to_numpy=True,
                            show_progress_bar=False)

    dimensions = model.get_sentence_embedding_dimension()
    return model, int(dimensions or config.dimensions), call


def load_encoder(config):
    if config.backend == BACKEND_NONE:
        raise EncoderUnavailable(REASON_MODEL_MISSING,
                                 "no semantic backend is configured")
    if not config.model_path:
        raise EncoderUnavailable(REASON_MODEL_MISSING,
                                 "no semantic model path is configured")
    if config.backend == BACKEND_MODEL2VEC:
        model, dimensions, call = _model2vec_encoder(config)
    else:
        model, dimensions, call = _sentence_transformers_encoder(config)
    if dimensions != config.dimensions:
        raise EncoderUnavailable(
            REASON_MODEL_DIMENSIONS,
            "the model emits %d dimensions and the vector memory budget was "
            "set for %d" % (dimensions, config.dimensions))
    return Encoder(model, dimensions, config.model_revision, call)


def active_versions(entries, now_ms, active_ms=contract.ACTIVE_MS):
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        version_id = entry.get("version_id")
        template = entry.get("template")
        last_observed = entry.get("last_observed")
        if not version_id or not template:
            continue
        if not isinstance(last_observed, int) or isinstance(last_observed,
                                                            bool):
            continue
        if not contract.is_active(last_observed, now_ms, active_ms):
            continue
        found.append(Version(version_id, template, last_observed))
    found.sort(key=lambda version: (-version.last_observed,
                                    version.version_id))
    return found


def looks_like_identifier(text):
    return bool(VERSION_IDENTIFIER.match((text or "").strip()))


def search_modes(mode, include_inactive):
    active = MODE_SEMANTIC if mode == MODE_SEMANTIC else MODE_TEXT
    return active, MODE_TEXT if include_inactive else None


def history_query(text, now_ms, page_size=DEFAULT_MAX_RESULTS, after=None,
                  retention_ms=contract.DEFINITION_RETENTION_MS,
                  active_ms=contract.ACTIVE_MS, only_inactive=True,
                  max_query_chars=DEFAULT_MAX_QUERY_CHARS):
    now_ms = int(now_ms)
    filters = [{"term": {"kind": contract.KIND_CATALOG_TEMPLATE}},
               {"range": {"last_observed": {
                   "gte": now_ms - int(retention_ms) + 1}}}]
    if only_inactive:
        filters.append({"range": {"last_observed": {
            "lt": now_ms - int(active_ms) + 1}}})
    body = {
        "size": _clamp(page_size, 1, DEFAULT_MAX_RESULTS),
        "track_total_hits": after is None,
        "sort": [{"last_observed": {"order": "desc"}},
                 {"version_id": {"order": "asc"}}],
        "query": {"bool": {"filter": filters}},
        "_source": list(contract.CATALOG_FIELDS),
    }
    text = (text or "").strip()[:max(1, int(max_query_chars))]
    if text:
        should = [{"term": {"version_id": text}}]
        if not looks_like_identifier(text):
            should.append({"match_phrase": {"template": text}})
        body["query"]["bool"]["should"] = should
        body["query"]["bool"]["minimum_should_match"] = 1
    if after:
        body["search_after"] = list(after)
    return body


def apply_scores(rows, hits):
    scores = {hit.version_id: hit.score for hit in hits}
    for row in rows:
        row["score"] = scores.get(row.get("version_id"))
    return rows


def summary(snapshot):
    return {
        "status": snapshot.status,
        "versions": snapshot.versions,
        "vector_bytes": snapshot.vector_bytes,
        "model_revision": snapshot.model_revision,
        "coverage": snapshot.coverage,
        "truncated": snapshot.truncated,
        "versions_total": snapshot.versions_total,
        "reason": snapshot.reason,
        "detail": REASON_TEXT.get(snapshot.reason, snapshot.reason),
    }


def result_summary(result):
    return {
        "status": result.status,
        "versions": result.versions,
        "vector_bytes": result.vector_bytes,
        "model_revision": result.model_revision,
        "coverage": result.coverage,
        "truncated": result.truncated,
        "versions_total": result.versions_total,
        "reason": result.reason,
        "detail": REASON_TEXT.get(result.reason, result.reason),
    }


def unavailable_summary(reason=REASON_DISABLED, detail=""):
    return {
        "status": STATUS_UNAVAILABLE,
        "versions": 0,
        "vector_bytes": 0,
        "model_revision": DEFAULT_MODEL_REVISION,
        "coverage": contract.COVERAGE_UNAVAILABLE,
        "truncated": False,
        "versions_total": 0,
        "reason": reason,
        "detail": detail or REASON_TEXT.get(reason, reason),
    }


def resident_estimate(versions, dimensions):
    return int(versions * (dimensions * VECTOR_VALUE_BYTES
                           + VERSION_RESIDENT_BYTES))


class SemanticSearch(object):

    def __init__(self, config=None, encoder_factory=None, clock=None):
        self.config = config if config is not None else Config.from_environment()
        self._encoder_factory = encoder_factory or load_encoder
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._store = VectorStore(self.config.dimensions,
                                  self.config.vector_capacity)
        self._store_lock = threading.RLock()
        self._lock = threading.Lock()
        self._encode_slot = threading.BoundedSemaphore(self.config.encoders)
        self._encoder = None
        self._encoder_error = None
        self._refreshing = False
        self._pending = None
        self._thread = None
        self._stopping = False
        self._wake = threading.Event()
        self.last_error = ""
        self._snapshot = self._blank(self._initial_reason())

    def _initial_reason(self):
        if not self.config.enabled:
            return REASON_DISABLED
        if self.config.vector_capacity <= 0:
            return REASON_NO_CAPACITY
        return REASON_NO_SNAPSHOT

    def _blank(self, reason, detail="", status=STATUS_UNAVAILABLE,
               versions_total=0, took_ms=0):
        return Snapshot(
            status=status,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=reason,
            detail=detail,
            versions=0,
            versions_total=versions_total,
            vector_bytes=0,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self.config.vector_capacity,
            truncated=False,
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=took_ms,
            embedded=frozenset())

    def snapshot(self):
        return self._snapshot

    def status(self):
        return summary(self._snapshot)

    def start(self):
        if not self.config.enabled:
            return False
        with self._lock:
            if self._thread is not None:
                return False
            self._stopping = False
            thread = threading.Thread(target=self._serve,
                                      name="semantic-refresh", daemon=True)
            self._thread = thread
        thread.start()
        return True

    def stop(self, timeout=5.0):
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stopping = True
        self._wake.set()
        if thread is not None:
            thread.join(timeout)

    def submit(self, versions):
        if not self.config.enabled:
            return False
        with self._lock:
            self._pending = list(versions)
        self._wake.set()
        return True

    def _serve(self):
        while True:
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()
            if self._stopping:
                return
            with self._lock:
                versions = self._pending
                self._pending = None
            if versions is None:
                continue
            try:
                self.refresh(versions)
            except Exception as exc:
                self._fail(REASON_ENCODE_FAILED, repr(exc))

    def refresh(self, versions):
        started = time.time()
        if not self.config.enabled:
            self._snapshot = self._blank(REASON_DISABLED)
            return self._snapshot
        with self._lock:
            if self._refreshing:
                return self._snapshot
            self._refreshing = True
        try:
            return self._refresh(list(versions), started)
        except SemanticError as exc:
            return self._fail(exc.reason, exc.detail)
        except Exception as exc:
            return self._fail(REASON_ENCODE_FAILED, repr(exc))
        finally:
            with self._lock:
                self._refreshing = False

    def _refresh(self, versions, started):
        capacity = self._store.capacity
        if capacity <= 0:
            return self._fail(REASON_NO_CAPACITY, "")
        self._encoder_error = None
        ordered = sorted(versions, key=lambda version: (-version.last_observed,
                                                        version.version_id))
        total = len(ordered)
        wanted = ordered[:capacity]
        truncated = total > capacity
        keep = set(version.version_id for version in wanted)
        with self._store_lock:
            for version_id in self._store.ids():
                if version_id not in keep:
                    self._store.discard(version_id)
            missing = [version for version in wanted
                       if version.version_id not in self._store]
        reason = REASON_NONE
        detail = ""
        if missing:
            if not self._snapshot.versions:
                self._snapshot = self._snapshot._replace(
                    status=STATUS_BUILDING, versions_total=total)
            try:
                self._encode_versions(missing)
            except SemanticError as exc:
                reason = exc.reason
                detail = exc.detail
                self.last_error = "%s %s" % (exc.reason, exc.detail)
            except Exception as exc:
                reason = REASON_ENCODE_FAILED
                detail = repr(exc)
                self.last_error = detail
        if truncated and reason == REASON_NONE:
            reason = self.config.binding_limit
        return self._publish(wanted, total, truncated, reason, detail, started)

    def _encode_versions(self, versions):
        size = self.config.batch_size
        for start in range(0, len(versions), size):
            batch = versions[start:start + size]
            vectors = self._encode([version.template for version in batch],
                                   self.config.encode_wait_seconds)
            with self._store_lock:
                for version, values in zip(batch, vectors):
                    self._store.add(version.version_id, values)

    def _encode(self, texts, timeout):
        if not self._encode_slot.acquire(True, timeout):
            raise EncoderUnavailable(REASON_ENCODER_BUSY, "")
        try:
            return self._ensure_encoder().encode(texts)
        finally:
            self._encode_slot.release()

    def _ensure_encoder(self):
        if self._encoder is not None:
            return self._encoder
        if self._encoder_error is not None:
            raise self._encoder_error
        try:
            self._encoder = self._encoder_factory(self.config)
        except SemanticError as exc:
            self._encoder_error = exc
            raise
        except Exception as exc:
            self._encoder_error = EncoderUnavailable(REASON_MODEL_MISSING,
                                                     repr(exc))
            raise self._encoder_error
        return self._encoder

    def _publish(self, wanted, total, truncated, reason, detail, started):
        with self._store_lock:
            embedded = frozenset(version.version_id for version in wanted
                                 if version.version_id in self._store)
            vector_bytes = self._store.vector_bytes
        versions = len(embedded)
        took_ms = int((time.time() - started) * 1000)
        if versions == 0:
            self._snapshot = self._blank(
                reason or REASON_NO_SNAPSHOT, detail,
                versions_total=total, took_ms=took_ms)
            return self._snapshot
        if truncated or versions < total:
            coverage = contract.COVERAGE_PARTIAL
            if reason == REASON_NONE:
                reason = REASON_ENCODE_FAILED
        else:
            coverage = contract.COVERAGE_COMPLETE
        self._snapshot = Snapshot(
            status=STATUS_READY,
            coverage=coverage,
            reason=reason,
            detail=detail,
            versions=versions,
            versions_total=total,
            vector_bytes=vector_bytes,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self._store.capacity,
            truncated=truncated or versions < total,
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=took_ms,
            embedded=embedded)
        return self._snapshot

    def _fail(self, reason, detail):
        self.last_error = "%s %s" % (reason, detail)
        previous = self._snapshot
        self._snapshot = Snapshot(
            status=(previous.status if previous.versions
                    else STATUS_UNAVAILABLE),
            coverage=(contract.COVERAGE_PARTIAL if previous.versions
                      else contract.COVERAGE_UNAVAILABLE),
            reason=reason,
            detail=detail,
            versions=previous.versions,
            versions_total=previous.versions_total,
            vector_bytes=previous.vector_bytes,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self.config.vector_capacity,
            truncated=previous.truncated or bool(previous.versions),
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=previous.took_ms,
            embedded=previous.embedded)
        return self._snapshot

    def _result(self, snapshot, hits, limit, started, reason=None, detail=""):
        return SearchResult(
            status=snapshot.status,
            coverage=snapshot.coverage,
            reason=snapshot.reason if reason is None else reason,
            detail=detail or snapshot.detail,
            hits=hits,
            truncated=snapshot.truncated,
            versions=snapshot.versions,
            versions_total=snapshot.versions_total,
            vector_bytes=snapshot.vector_bytes,
            model_revision=snapshot.model_revision,
            limit=limit,
            took_ms=int((time.time() - started) * 1000))

    def search(self, text, limit=None):
        started = time.time()
        snapshot = self._snapshot
        limit = _clamp(limit or self.config.max_results, 1,
                       self.config.max_results)
        if snapshot.status != STATUS_READY:
            return self._result(snapshot, (), limit, started,
                                reason=snapshot.reason or REASON_NO_SNAPSHOT)
        query = (text or "").strip()[:self.config.max_query_chars]
        if not query:
            return self._result(snapshot, (), limit, started,
                                reason=REASON_EMPTY_QUERY)
        try:
            vector = unit_vector(
                self._encode([query], self.config.query_wait_seconds)[0],
                self.config.dimensions)
        except SemanticError as exc:
            self.last_error = "%s %s" % (exc.reason, exc.detail)
            return SearchResult(
                status=STATUS_UNAVAILABLE,
                coverage=contract.COVERAGE_UNAVAILABLE,
                reason=exc.reason, detail=exc.detail, hits=(),
                truncated=snapshot.truncated, versions=snapshot.versions,
                versions_total=snapshot.versions_total,
                vector_bytes=snapshot.vector_bytes,
                model_revision=snapshot.model_revision, limit=limit,
                took_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            self.last_error = repr(exc)
            return SearchResult(
                status=STATUS_UNAVAILABLE,
                coverage=contract.COVERAGE_UNAVAILABLE,
                reason=REASON_ENCODE_FAILED, detail=repr(exc), hits=(),
                truncated=snapshot.truncated, versions=snapshot.versions,
                versions_total=snapshot.versions_total,
                vector_bytes=snapshot.vector_bytes,
                model_revision=snapshot.model_revision, limit=limit,
                took_ms=int((time.time() - started) * 1000))
        with self._store_lock:
            scored = self._store.search(vector, limit, snapshot.embedded)
        hits = tuple(Hit(version_id, score) for version_id, score in scored)
        return self._result(snapshot, hits, limit, started)

    def neighbours(self, version_id, limit=None):
        started = time.time()
        snapshot = self._snapshot
        limit = _clamp(limit or DEFAULT_MAX_NEIGHBOURS, 1,
                       DEFAULT_MAX_NEIGHBOURS)
        if snapshot.status != STATUS_READY:
            return self._result(snapshot, (), limit, started,
                                reason=snapshot.reason or REASON_NO_SNAPSHOT)
        wanted = str(version_id or "").strip()
        if wanted not in snapshot.embedded:
            return self._result(snapshot, (), limit, started,
                                reason=REASON_NOT_EMBEDDED)
        with self._store_lock:
            vector = self._store.vector(wanted)
            scored = (() if vector is None
                      else self._store.search(vector, limit + 1,
                                              snapshot.embedded))
        if vector is None:
            return self._result(snapshot, (), limit, started,
                                reason=REASON_NOT_EMBEDDED)
        hits = tuple(Hit(found, score) for found, score in scored
                     if found != wanted)[:limit]
        return self._result(snapshot, hits, limit, started)


class DisabledSearch(object):

    def __init__(self, reason=REASON_DISABLED, detail=""):
        self.config = Config()
        self.reason = reason
        self.detail = detail
        self.last_error = detail

    def status(self):
        return unavailable_summary(self.reason, self.detail)

    def snapshot(self):
        return None

    def start(self):
        return False

    def stop(self, timeout=5.0):
        return None

    def submit(self, versions):
        return False

    def refresh(self, versions):
        return None

    def search(self, text, limit=None):
        return SearchResult(
            status=STATUS_UNAVAILABLE,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=self.reason, detail=self.detail, hits=(), truncated=False,
            versions=0, versions_total=0, vector_bytes=0,
            model_revision=DEFAULT_MODEL_REVISION,
            limit=_clamp(limit or DEFAULT_MAX_RESULTS, 1, DEFAULT_MAX_RESULTS),
            took_ms=0)

    def neighbours(self, version_id, limit=None):
        return SearchResult(
            status=STATUS_UNAVAILABLE,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=self.reason, detail=self.detail, hits=(), truncated=False,
            versions=0, versions_total=0, vector_bytes=0,
            model_revision=DEFAULT_MODEL_REVISION,
            limit=_clamp(limit or DEFAULT_MAX_NEIGHBOURS, 1,
                         DEFAULT_MAX_NEIGHBOURS),
            took_ms=0)


def build(config=None, encoder_factory=None, clock=None):
    try:
        search = SemanticSearch(config=config, encoder_factory=encoder_factory,
                                clock=clock)
    except Exception as exc:
        return DisabledSearch(REASON_ENCODE_FAILED, repr(exc))
    if not search.config.enabled:
        return search
    try:
        search.start()
    except Exception as exc:
        search.last_error = repr(exc)
    return search
