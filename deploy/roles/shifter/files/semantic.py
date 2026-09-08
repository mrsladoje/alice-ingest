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
                                  "/opt/alice-ingest/shared"))

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
REASON_GROUP_LIMIT = "group_limit_reached"
REASON_VECTOR_BYTES_LIMIT = "vector_bytes_limit_reached"
REASON_EMPTY_QUERY = "empty_query"
REASON_REFRESH_RUNNING = "refresh_running"
REASON_NOT_EMBEDDED = "group_not_embedded"

REASON_TEXT = {
    REASON_NONE: "",
    REASON_DISABLED: "semantic search is turned off on this server",
    REASON_NO_SNAPSHOT: "semantic search has no vectors yet",
    REASON_NO_CAPACITY: "the configured vector memory holds no group",
    REASON_MODEL_MISSING: "the retrieval model did not load",
    REASON_MODEL_DIMENSIONS: "the model emits a different number of dimensions "
                             "than the memory budget was set for",
    REASON_ENCODE_FAILED: "the encoder failed",
    REASON_ENCODER_BUSY: "the single encoding slot was busy",
    REASON_GROUP_LIMIT: "the active group limit was reached, so the searched "
                        "corpus is smaller than the active catalog",
    REASON_VECTOR_BYTES_LIMIT: "the vector memory limit was reached, so the "
                               "searched corpus is smaller than the active "
                               "catalog",
    REASON_EMPTY_QUERY: "the query was empty",
    REASON_REFRESH_RUNNING: "a refresh is already running",
    REASON_NOT_EMBEDDED: "this canonical group holds no vector in the "
                         "searched corpus",
}

VECTOR_VALUE_BYTES = 4
GROUP_RESIDENT_BYTES = 544
VERSION_RESIDENT_BYTES = 112
QUERY_SCRATCH_BYTES = 96

DEFAULT_DIMENSIONS = 512
DEFAULT_MAX_GROUPS = 20000
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
CANONICAL_IDENTIFIER = re.compile(r"^[0-9a-f]{16}$")

Group = collections.namedtuple(
    "Group", ("canonical_id", "normalized", "version_ids", "last_observed"))

Hit = collections.namedtuple("Hit", ("canonical_id", "version_ids", "score"))

Snapshot = collections.namedtuple("Snapshot", (
    "status", "coverage", "reason", "detail", "groups", "groups_total",
    "versions", "vector_bytes", "vector_bytes_limit", "capacity", "truncated",
    "model_revision", "built_at_ms", "took_ms", "members"))

SearchResult = collections.namedtuple("SearchResult", (
    "status", "coverage", "reason", "detail", "hits", "truncated", "groups",
    "groups_total", "vector_bytes", "model_revision", "limit", "took_ms"))


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
                 max_groups=DEFAULT_MAX_GROUPS,
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
        self.max_groups = max(0, int(max_groups))
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
        return min(self.max_groups,
                   self.max_vector_bytes // self.vector_row_bytes)

    @property
    def binding_limit(self):
        if self.max_groups <= self.max_vector_bytes // self.vector_row_bytes:
            return REASON_GROUP_LIMIT
        return REASON_VECTOR_BYTES_LIMIT

    def resident_ceiling(self, versions_per_group=1.05):
        groups = self.vector_capacity
        return int(groups * (self.vector_row_bytes + GROUP_RESIDENT_BYTES)
                   + groups * versions_per_group * VERSION_RESIDENT_BYTES
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
            max_groups=_whole(env.get("SHIFTER_SEMANTIC_MAX_GROUPS"),
                              DEFAULT_MAX_GROUPS),
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

    def __contains__(self, canonical_id):
        return canonical_id in self._rows

    @property
    def vector_bytes(self):
        return len(self._values) * self._values.itemsize

    def ids(self):
        return tuple(self._ids)

    def vector(self, canonical_id):
        row = self._rows.get(canonical_id)
        if row is None:
            return None
        dimensions = self.dimensions
        return self._values[row * dimensions:(row + 1) * dimensions]

    def add(self, canonical_id, values):
        vector = unit_vector(values, self.dimensions)
        row = self._rows.get(canonical_id)
        dimensions = self.dimensions
        if row is not None:
            self._values[row * dimensions:(row + 1) * dimensions] = vector
            return False
        if len(self._ids) >= self.capacity:
            raise LimitReached(
                REASON_VECTOR_BYTES_LIMIT,
                "the store holds %d groups and its capacity is %d"
                % (len(self._ids), self.capacity))
        self._rows[canonical_id] = len(self._ids)
        self._ids.append(canonical_id)
        self._values.extend(vector)
        return True

    def discard(self, canonical_id):
        row = self._rows.pop(canonical_id, None)
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
        for row, canonical_id in enumerate(self._ids):
            if allowed is not None and canonical_id not in allowed:
                continue
            scored.append((canonical_id, sum(map(
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


def active_groups(entries, now_ms, active_ms=contract.ACTIVE_MS):
    collected = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        canonical_id = entry.get("canonical_id")
        version_id = entry.get("version_id")
        last_observed = entry.get("last_observed")
        if not canonical_id or not version_id:
            continue
        if not isinstance(last_observed, int) or isinstance(last_observed,
                                                            bool):
            continue
        if not contract.is_active(last_observed, now_ms, active_ms):
            continue
        normalized = entry.get("normalized")
        if not normalized:
            template = entry.get("template")
            if not template:
                continue
            normalized = contract.normalize(template)
        found = collected.get(canonical_id)
        if found is None:
            collected[canonical_id] = {
                "normalized": normalized,
                "versions": {version_id},
                "last_observed": last_observed,
                "lowest": version_id,
            }
            continue
        found["versions"].add(version_id)
        if last_observed > found["last_observed"]:
            found["last_observed"] = last_observed
        if version_id < found["lowest"]:
            found["lowest"] = version_id
            found["normalized"] = normalized
    groups = [Group(canonical_id, found["normalized"],
                    tuple(sorted(found["versions"])), found["last_observed"])
              for canonical_id, found in collected.items()]
    groups.sort(key=lambda group: (-group.last_observed, group.canonical_id))
    return groups


def looks_like_identifier(text):
    text = (text or "").strip()
    return bool(VERSION_IDENTIFIER.match(text)
                or CANONICAL_IDENTIFIER.match(text))


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
        should = [{"term": {"version_id": text}},
                  {"term": {"canonical_id": text}}]
        if not looks_like_identifier(text):
            should.append({"match_phrase": {"template": text}})
            should.append({"match_phrase": {"normalized": text}})
        body["query"]["bool"]["should"] = should
        body["query"]["bool"]["minimum_should_match"] = 1
    if after:
        body["search_after"] = list(after)
    return body


def apply_scores(rows, hits):
    scores = {hit.canonical_id: hit.score for hit in hits}
    for row in rows:
        row["score"] = scores.get(row.get("canonical_id"))
    return rows


def summary(snapshot):
    return {
        "status": snapshot.status,
        "groups": snapshot.groups,
        "vector_bytes": snapshot.vector_bytes,
        "model_revision": snapshot.model_revision,
        "coverage": snapshot.coverage,
        "truncated": snapshot.truncated,
        "groups_total": snapshot.groups_total,
        "reason": snapshot.reason,
        "detail": REASON_TEXT.get(snapshot.reason, snapshot.reason),
    }


def result_summary(result):
    return {
        "status": result.status,
        "groups": result.groups,
        "vector_bytes": result.vector_bytes,
        "model_revision": result.model_revision,
        "coverage": result.coverage,
        "truncated": result.truncated,
        "groups_total": result.groups_total,
        "reason": result.reason,
        "detail": REASON_TEXT.get(result.reason, result.reason),
    }


def unavailable_summary(reason=REASON_DISABLED, detail=""):
    return {
        "status": STATUS_UNAVAILABLE,
        "groups": 0,
        "vector_bytes": 0,
        "model_revision": DEFAULT_MODEL_REVISION,
        "coverage": contract.COVERAGE_UNAVAILABLE,
        "truncated": False,
        "groups_total": 0,
        "reason": reason,
        "detail": detail or REASON_TEXT.get(reason, reason),
    }


def resident_estimate(groups, versions, dimensions):
    return int(groups * (dimensions * VECTOR_VALUE_BYTES + GROUP_RESIDENT_BYTES)
               + versions * VERSION_RESIDENT_BYTES)


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
               groups_total=0, took_ms=0):
        return Snapshot(
            status=status,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=reason,
            detail=detail,
            groups=0,
            groups_total=groups_total,
            versions=0,
            vector_bytes=0,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self.config.vector_capacity,
            truncated=False,
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=took_ms,
            members={})

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

    def submit(self, groups):
        if not self.config.enabled:
            return False
        with self._lock:
            self._pending = list(groups)
        self._wake.set()
        return True

    def _serve(self):
        while True:
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()
            if self._stopping:
                return
            with self._lock:
                groups = self._pending
                self._pending = None
            if groups is None:
                continue
            try:
                self.refresh(groups)
            except Exception as exc:
                self._fail(REASON_ENCODE_FAILED, repr(exc))

    def refresh(self, groups):
        started = time.time()
        if not self.config.enabled:
            self._snapshot = self._blank(REASON_DISABLED)
            return self._snapshot
        with self._lock:
            if self._refreshing:
                return self._snapshot
            self._refreshing = True
        try:
            return self._refresh(list(groups), started)
        except SemanticError as exc:
            return self._fail(exc.reason, exc.detail)
        except Exception as exc:
            return self._fail(REASON_ENCODE_FAILED, repr(exc))
        finally:
            with self._lock:
                self._refreshing = False

    def _refresh(self, groups, started):
        capacity = self._store.capacity
        if capacity <= 0:
            return self._fail(REASON_NO_CAPACITY, "")
        self._encoder_error = None
        ordered = sorted(groups, key=lambda group: (-group.last_observed,
                                                    group.canonical_id))
        total = len(ordered)
        wanted = ordered[:capacity]
        truncated = total > capacity
        keep = set(group.canonical_id for group in wanted)
        with self._store_lock:
            for canonical_id in self._store.ids():
                if canonical_id not in keep:
                    self._store.discard(canonical_id)
            missing = [group for group in wanted
                       if group.canonical_id not in self._store]
        reason = REASON_NONE
        detail = ""
        if missing:
            if not self._snapshot.groups:
                self._snapshot = self._snapshot._replace(
                    status=STATUS_BUILDING, groups_total=total)
            try:
                self._encode_groups(missing)
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

    def _encode_groups(self, groups):
        size = self.config.batch_size
        for start in range(0, len(groups), size):
            batch = groups[start:start + size]
            vectors = self._encode([group.normalized for group in batch],
                                   self.config.encode_wait_seconds)
            with self._store_lock:
                for group, values in zip(batch, vectors):
                    self._store.add(group.canonical_id, values)

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
            members = {group.canonical_id: group.version_ids
                       for group in wanted
                       if group.canonical_id in self._store}
            vector_bytes = self._store.vector_bytes
        groups = len(members)
        versions = sum(len(ids) for ids in members.values())
        took_ms = int((time.time() - started) * 1000)
        if groups == 0:
            self._snapshot = self._blank(
                reason or REASON_NO_SNAPSHOT, detail,
                groups_total=total, took_ms=took_ms)
            return self._snapshot
        if truncated or groups < total:
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
            groups=groups,
            groups_total=total,
            versions=versions,
            vector_bytes=vector_bytes,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self._store.capacity,
            truncated=truncated or groups < total,
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=took_ms,
            members=members)
        return self._snapshot

    def _fail(self, reason, detail):
        self.last_error = "%s %s" % (reason, detail)
        previous = self._snapshot
        self._snapshot = Snapshot(
            status=previous.status if previous.groups else STATUS_UNAVAILABLE,
            coverage=(contract.COVERAGE_PARTIAL if previous.groups
                      else contract.COVERAGE_UNAVAILABLE),
            reason=reason,
            detail=detail,
            groups=previous.groups,
            groups_total=previous.groups_total,
            versions=previous.versions,
            vector_bytes=previous.vector_bytes,
            vector_bytes_limit=self.config.max_vector_bytes,
            capacity=self.config.vector_capacity,
            truncated=previous.truncated or bool(previous.groups),
            model_revision=self.config.model_revision,
            built_at_ms=self._clock(),
            took_ms=previous.took_ms,
            members=previous.members)
        return self._snapshot

    def _result(self, snapshot, hits, limit, started, reason=None, detail=""):
        return SearchResult(
            status=snapshot.status,
            coverage=snapshot.coverage,
            reason=snapshot.reason if reason is None else reason,
            detail=detail or snapshot.detail,
            hits=hits,
            truncated=snapshot.truncated,
            groups=snapshot.groups,
            groups_total=snapshot.groups_total,
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
                truncated=snapshot.truncated, groups=snapshot.groups,
                groups_total=snapshot.groups_total,
                vector_bytes=snapshot.vector_bytes,
                model_revision=snapshot.model_revision, limit=limit,
                took_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            self.last_error = repr(exc)
            return SearchResult(
                status=STATUS_UNAVAILABLE,
                coverage=contract.COVERAGE_UNAVAILABLE,
                reason=REASON_ENCODE_FAILED, detail=repr(exc), hits=(),
                truncated=snapshot.truncated, groups=snapshot.groups,
                groups_total=snapshot.groups_total,
                vector_bytes=snapshot.vector_bytes,
                model_revision=snapshot.model_revision, limit=limit,
                took_ms=int((time.time() - started) * 1000))
        with self._store_lock:
            scored = self._store.search(vector, limit, snapshot.members)
        hits = tuple(Hit(canonical_id, snapshot.members[canonical_id], score)
                     for canonical_id, score in scored)
        return self._result(snapshot, hits, limit, started)

    def neighbours(self, canonical_id, limit=None):
        started = time.time()
        snapshot = self._snapshot
        limit = _clamp(limit or DEFAULT_MAX_NEIGHBOURS, 1,
                       DEFAULT_MAX_NEIGHBOURS)
        if snapshot.status != STATUS_READY:
            return self._result(snapshot, (), limit, started,
                                reason=snapshot.reason or REASON_NO_SNAPSHOT)
        group_id = str(canonical_id or "").strip()
        if group_id not in snapshot.members:
            return self._result(snapshot, (), limit, started,
                                reason=REASON_NOT_EMBEDDED)
        with self._store_lock:
            vector = self._store.vector(group_id)
            scored = (() if vector is None
                      else self._store.search(vector, limit + 1,
                                              snapshot.members))
        if vector is None:
            return self._result(snapshot, (), limit, started,
                                reason=REASON_NOT_EMBEDDED)
        hits = tuple(Hit(found, snapshot.members[found], score)
                     for found, score in scored
                     if found != group_id)[:limit]
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

    def submit(self, groups):
        return False

    def refresh(self, groups):
        return None

    def search(self, text, limit=None):
        return SearchResult(
            status=STATUS_UNAVAILABLE,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=self.reason, detail=self.detail, hits=(), truncated=False,
            groups=0, groups_total=0, vector_bytes=0,
            model_revision=DEFAULT_MODEL_REVISION,
            limit=_clamp(limit or DEFAULT_MAX_RESULTS, 1, DEFAULT_MAX_RESULTS),
            took_ms=0)

    def neighbours(self, canonical_id, limit=None):
        return SearchResult(
            status=STATUS_UNAVAILABLE,
            coverage=contract.COVERAGE_UNAVAILABLE,
            reason=self.reason, detail=self.detail, hits=(), truncated=False,
            groups=0, groups_total=0, vector_bytes=0,
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
