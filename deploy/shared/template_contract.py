import collections
import datetime
import hashlib
import json
import re

SCHEMA_VERSION = 2

CATALOG_INDEX = "template-catalog"
TRIAGE_INDEX = "template-triage"
QUERIES_INDEX = "shifter-queries"
FINE_BUCKETS_PREFIX = "template-buckets-5m"
COARSE_BUCKETS_PREFIX = "template-buckets-1h"

KIND_CATALOG_TEMPLATE = "template"
KIND_BUCKET = "bucket"
KIND_WATERMARK = "watermark"
KIND_CHECK = "check"
KIND_WATCHED_HISTORY = "watched_history"
KIND_TRIAGE_LABEL = "template_label"
KIND_QUERY_EVENT = "shifter_query"

COLLECTOR_TIME_FIELD = "collector_time"
RECORD_ID_FIELD = "doc_id"
NODE_FIELD = "node"
TEMPLATE_VERSION_FIELD = "template_version"
TEMPLATE_ID_FIELD = "template_id"
TEMPLATE_STATUS_FIELD = "template_status"
STAMP_FIELDS = (TEMPLATE_VERSION_FIELD, TEMPLATE_ID_FIELD,
                TEMPLATE_STATUS_FIELD)

STAMP_MATCHED = "matched"
STAMP_NEW = "new"
STAMP_UNLEARNED = "unlearned"
STAMP_NO_TEMPLATE = "no_template"
STAMP_STATUSES = (STAMP_MATCHED, STAMP_NEW, STAMP_UNLEARNED, STAMP_NO_TEMPLATE)

SECOND_MS = 1000
MINUTE_MS = 60000
HOUR_MS = 3600000
DAY_MS = 86400000

FINE_BUCKET_MS = 5 * MINUTE_MS
COARSE_BUCKET_MS = HOUR_MS
RESOLUTIONS = (FINE_BUCKET_MS, COARSE_BUCKET_MS)
PUBLISH_INTERVAL_MS = 5 * MINUTE_MS

LEDGER_HOURS = 48
LEDGER_MS = LEDGER_HOURS * HOUR_MS
WINDOW_DAYS = 28
WINDOW_MS = WINDOW_DAYS * DAY_MS
ACTIVE_DAYS = WINDOW_DAYS
ACTIVE_MS = WINDOW_MS
FINE_RETENTION_DAYS = 3
COARSE_RETENTION_DAYS = 35
DEFINITION_RETENTION_DAYS = 90
DEFINITION_RETENTION_MS = DEFINITION_RETENTION_DAYS * DAY_MS
QUERY_RETENTION_DAYS = 365
QUERY_RETENTION_MS = QUERY_RETENTION_DAYS * DAY_MS
CLOCK_TOLERANCE_MS = 5 * MINUTE_MS
CHUNK_IDENTIFIER_HOLD_MS = HOUR_MS
IDLE_NODE_MS = 3 * PUBLISH_INTERVAL_MS

EPOCH_SECONDS_CUTOFF = 100000000000

MAX_ENTRY_PROGRAMS = 32
MAX_ENTRY_HOSTS = 32
MAX_ENTRY_SOURCES = 8
MAX_ENTRY_NODES = 64
MAX_ENTRY_LINKS = 64
MAX_BUCKET_VERSIONS = 20000

JS_MAX_SAFE_INTEGER = 9007199254740991
JS_MIN_SAFE_INTEGER = -9007199254740991

WILDCARD = "<*>"
PLACEHOLDER = re.compile(r"<[A-Z_]+>|<\*>")
SPACE = re.compile(r"\s+")
EDGE = re.compile(r"^[\s.,;:!-]+|[\s.,;:!-]+$")

ID_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ISO_8601 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?$")

BUCKET_ID_PREFIX = "bucket"
WATERMARK_ID_PREFIX = "watermark"
CHECK_ID_PREFIX = "check"
LATE_MARK = "late"

CHECK_CONSERVATION = "conservation"
CHECK_STAMPED_AGAINST_INDEXED = "stamped_against_indexed"
CHECKS = (CHECK_CONSERVATION, CHECK_STAMPED_AGAINST_INDEXED)

TIME_OK = "ok"
TIME_MISSING = "missing"
TIME_INVALID = "invalid"
TIME_OUT_OF_TOLERANCE = "out_of_tolerance"
TIME_STATUSES = (TIME_OK, TIME_MISSING, TIME_INVALID, TIME_OUT_OF_TOLERANCE)

COVERAGE_COMPLETE = "complete"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNKNOWN = "unknown"
COVERAGE_UNAVAILABLE = "unavailable"
COVERAGE_STATUSES = (COVERAGE_COMPLETE, COVERAGE_PARTIAL, COVERAGE_UNKNOWN,
                     COVERAGE_UNAVAILABLE)

GAP_MISSING_TIME = "missing_observation_time"
GAP_INVALID_TIME = "invalid_observation_time"
GAP_CLOCK_TOLERANCE = "observation_time_beyond_tolerance"
GAP_STATE_LIMIT = "state_limit_reached"
GAP_UNMINABLE_RECORD = "record_has_no_template"
GAP_LATE_RECORD = "record_stamped_after_ledger_horizon"
GAP_NODE_BEHIND = "node_watermark_behind_cutoff"
GAP_NODE_IDLE = "node_watermark_idle"
GAP_PUBLICATION_FAILED = "publication_failed"
GAP_REASONS = (GAP_MISSING_TIME, GAP_INVALID_TIME, GAP_CLOCK_TOLERANCE,
               GAP_STATE_LIMIT, GAP_UNMINABLE_RECORD, GAP_LATE_RECORD,
               GAP_NODE_BEHIND, GAP_NODE_IDLE, GAP_PUBLICATION_FAILED)

STAMPER_COUNTERS = (
    "records", "chunks", "duplicate_chunks", "returned_chunks",
    "return_failures", "matched_records", "new_records", "unlearned_records",
    "no_template_records", "late_records", "missing_time_records",
    "invalid_time_records", "journal_bytes", "journal_lines", "clusters",
    "ledger_buckets", "ledger_versions", "publications",
    "publication_failures", "peak_rss_bytes", "socket_backlog",
)

CATALOG_FIELDS = (
    "kind", "version_id", "canonical_id", "family", "template", "normalized",
    "token_count", "programs", "programs_truncated", "origin_hosts",
    "origin_hosts_truncated", "log_sources", "severity_norm", "nodes",
    "nodes_truncated", "first_observed", "last_observed", "first_catalogued",
    "widened_into", "widened_from", "schema_version",
)

_UTC = datetime.timezone.utc
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=_UTC)

ObservationTime = collections.namedtuple("ObservationTime",
                                         ("status", "epoch_ms"))


class ContractError(Exception):
    pass


class ClockFault(ContractError):
    pass


def normalize(template):
    text = PLACEHOLDER.sub(" <*> ", template).lower()
    text = SPACE.sub(" ", text).strip()
    return EDGE.sub("", text) or text


def digest(*parts):
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()[:16]


def version_id(family, template):
    body = hashlib.sha1(("%s\n%s" % (family, template)).encode("utf-8"))
    return "%s:%s" % (family, body.hexdigest()[:24])


def canonical_id(template):
    return digest(normalize(template))


def family_of(record, source):
    if source != "stdout":
        return source
    if record.get("log_time"):
        return "dpl"
    severity = record.get("severity") or ""
    if len(severity) == 1 and severity.isupper():
        return "datadist"
    return "dpl"


def family_of_version(identity):
    return _text(identity, "version_id").split(":", 1)[0]


def template_tokens(template):
    return tuple(_text(template, "template").split())


def covers(wide, narrow):
    if len(wide) != len(narrow):
        return False
    for held, other in zip(wide, narrow):
        if held != other and held != WILDCARD:
            return False
    return True


def cover_descendants(versions):
    groups = {}
    for identity, family, template in versions:
        tokens = template_tokens(template)
        groups.setdefault((family, len(tokens)), []).append((identity, tokens))
    out = {identity: set() for identity, _, _ in versions}
    for members in groups.values():
        if len(members) < 2:
            continue
        for identity, tokens in members:
            if WILDCARD not in tokens:
                continue
            held = out[identity]
            for other, candidate in members:
                if other != identity and covers(tokens, candidate):
                    held.add(other)
    return out


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ContractError("%s must be a non-empty string, got %r"
                            % (name, value))
    return value


def _whole(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("%s must be an integer, got %r" % (name, value))
    return value


def _at_least(value, floor, name):
    _whole(value, name)
    if value < floor:
        raise ContractError("%s must be at least %d, got %d"
                            % (name, floor, value))
    return value


def _scope(values, name, limit):
    if isinstance(values, str) or not isinstance(values, (list, tuple, set,
                                                          frozenset)):
        raise ContractError("%s must be a list of strings, got %r"
                            % (name, values))
    checked = set()
    for value in values:
        checked.add(_text(value, "%s entry" % name))
    unique = sorted(checked)
    return unique[:limit], len(unique) > limit


def _id_part(value, name):
    _text(value, name)
    if not ID_PART.match(value):
        raise ContractError(
            "%s %r cannot appear in a document identifier; use letters, "
            "digits, dot, dash and underscore, at most 64 characters"
            % (name, value))
    return value


def require_resolution(resolution_ms):
    if resolution_ms not in RESOLUTIONS:
        raise ContractError("resolution %r is not one of %s milliseconds"
                            % (resolution_ms, ", ".join(
                                str(r) for r in RESOLUTIONS)))
    return resolution_ms


def bucket_start_ms(epoch_ms, resolution_ms=FINE_BUCKET_MS):
    require_resolution(resolution_ms)
    return (_whole(epoch_ms, "epoch_ms") // resolution_ms) * resolution_ms


def bucket_end_ms(bucket_start, resolution_ms=FINE_BUCKET_MS):
    return require_bucket_start(bucket_start, resolution_ms) + resolution_ms


def require_bucket_start(epoch_ms, resolution_ms=FINE_BUCKET_MS):
    require_resolution(resolution_ms)
    if _whole(epoch_ms, "bucket_start") % resolution_ms != 0:
        raise ContractError(
            "bucket start %d is not aligned to %d milliseconds"
            % (epoch_ms, resolution_ms))
    return epoch_ms


def coarse_bucket_of(fine_bucket_start):
    return bucket_start_ms(require_bucket_start(fine_bucket_start),
                           COARSE_BUCKET_MS)


def bucket_index_name(resolution_ms, bucket_start):
    require_bucket_start(bucket_start, resolution_ms)
    moment = _EPOCH + datetime.timedelta(milliseconds=bucket_start)
    if resolution_ms == FINE_BUCKET_MS:
        return "%s-%s" % (FINE_BUCKETS_PREFIX, moment.strftime("%Y.%m.%d"))
    return "%s-%s" % (COARSE_BUCKETS_PREFIX, moment.strftime("%Y.%m"))


def bucket_index_pattern(resolution_ms):
    require_resolution(resolution_ms)
    if resolution_ms == FINE_BUCKET_MS:
        return "%s-*" % FINE_BUCKETS_PREFIX
    return "%s-*" % COARSE_BUCKETS_PREFIX


def bucket_id(node, family, resolution_ms, bucket_start, late=False):
    parts = [BUCKET_ID_PREFIX, _id_part(node, "node"),
             _id_part(family, "family"),
             str(require_resolution(resolution_ms) // SECOND_MS),
             str(require_bucket_start(bucket_start, resolution_ms))]
    if late:
        parts.append(LATE_MARK)
    return ":".join(parts)


def bucket_document(node, family, resolution_ms, bucket_start, counts,
                    published_at_ms, late=False):
    if not isinstance(counts, dict) or not counts:
        raise ContractError("a bucket document carries at least one count")
    if len(counts) > MAX_BUCKET_VERSIONS:
        raise ContractError("a bucket document holds at most %d versions, "
                            "got %d" % (MAX_BUCKET_VERSIONS, len(counts)))
    rows = []
    total = 0
    for identity in sorted(counts):
        _text(identity, "version_id")
        if family_of_version(identity) != family:
            raise ContractError("version %s does not belong to family %s"
                                % (identity, family))
        count = _at_least(counts[identity], 1, "count")
        rows.append({"version_id": identity, "count": encode_int(count)})
        total += count
    if not isinstance(late, bool):
        raise ContractError("late must be true or false, got %r" % (late,))
    identifier = bucket_id(node, family, resolution_ms, bucket_start, late)
    return {
        "kind": KIND_BUCKET,
        "schema_version": SCHEMA_VERSION,
        "bucket_id": identifier,
        "node": node,
        "family": family,
        "resolution_seconds": resolution_ms // SECOND_MS,
        "bucket_start": bucket_start,
        "bucket_end": bucket_start + resolution_ms,
        "late": late,
        "total": encode_int(total),
        "version_count": len(rows),
        "versions": [row["version_id"] for row in rows],
        "counts": rows,
        "published_at": _at_least(published_at_ms, 0, "published_at"),
    }


def validate_bucket(document):
    if not isinstance(document, dict):
        raise ContractError("a bucket document must be an object, got %r"
                            % (document,))
    if document.get("kind") != KIND_BUCKET:
        raise ContractError("kind must be %r, got %r"
                            % (KIND_BUCKET, document.get("kind")))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("schema_version must be %d, got %r"
                            % (SCHEMA_VERSION, document.get("schema_version")))
    resolution = _whole(document.get("resolution_seconds"),
                        "resolution_seconds") * SECOND_MS
    start = require_bucket_start(document.get("bucket_start"), resolution)
    if document.get("bucket_end") != start + resolution:
        raise ContractError("bucket_end %r does not close the bucket at %d"
                            % (document.get("bucket_end"), start))
    late = document.get("late")
    if not isinstance(late, bool):
        raise ContractError("late must be true or false, got %r" % (late,))
    expected = bucket_id(document.get("node"), document.get("family"),
                         resolution, start, late)
    if document.get("bucket_id") != expected:
        raise ContractError("bucket_id %r does not address this bucket; it "
                            "must be %s" % (document.get("bucket_id"),
                                            expected))
    rows = document.get("counts")
    if not isinstance(rows, list) or not rows:
        raise ContractError("bucket %s carries no counts" % expected)
    if document.get("version_count") != len(rows):
        raise ContractError("bucket %s says %r versions and carries %d"
                            % (expected, document.get("version_count"),
                               len(rows)))
    seen = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError("a count row must be an object, got %r"
                                % (row,))
        identity = _text(row.get("version_id"), "version_id")
        if identity in seen:
            raise ContractError("bucket %s counts version %s twice"
                                % (expected, identity))
        if family_of_version(identity) != document["family"]:
            raise ContractError("bucket %s counts version %s of another family"
                                % (expected, identity))
        seen.add(identity)
        count = decode_int(row.get("count"))
        if count < 1:
            raise ContractError("bucket %s carries a count of %d for %s"
                                % (expected, count, identity))
        total += count
    versions = document.get("versions")
    if not isinstance(versions, list) or set(versions) != seen:
        raise ContractError("bucket %s lists versions that differ from its "
                            "counts" % expected)
    if decode_int(document.get("total")) != total:
        raise ContractError(
            "bucket %s publishes a total of %d and its counts sum to %d"
            % (expected, decode_int(document.get("total")), total))
    _at_least(document.get("published_at"), 0, "published_at")
    return document


def conserved(document):
    try:
        validate_bucket(document)
    except ContractError:
        return False
    return True


def watermark_id(node):
    return "%s:%s" % (WATERMARK_ID_PREFIX, _id_part(node, "node"))


def watermark_document(node, published_through_ms, ledger_start_ms,
                       published_at_ms, counters=None):
    _at_least(published_through_ms, 0, "published_through")
    _at_least(ledger_start_ms, 0, "ledger_start")
    if ledger_start_ms > published_through_ms:
        raise ContractError(
            "ledger_start %d is after published_through %d"
            % (ledger_start_ms, published_through_ms))
    document = {
        "kind": KIND_WATERMARK,
        "schema_version": SCHEMA_VERSION,
        "watermark_id": watermark_id(node),
        "node": node,
        "published_through": published_through_ms,
        "ledger_start": ledger_start_ms,
        "published_at": _at_least(published_at_ms, 0, "published_at"),
    }
    if counters is not None:
        document["counters"] = stamper_counters(**counters)
    return document


def validate_watermark(document):
    if not isinstance(document, dict):
        raise ContractError("a watermark must be an object, got %r"
                            % (document,))
    if document.get("kind") != KIND_WATERMARK:
        raise ContractError("kind must be %r, got %r"
                            % (KIND_WATERMARK, document.get("kind")))
    expected = watermark_id(document.get("node"))
    if document.get("watermark_id") != expected:
        raise ContractError("watermark_id %r must be %s"
                            % (document.get("watermark_id"), expected))
    through = _at_least(document.get("published_through"), 0,
                        "published_through")
    start = _at_least(document.get("ledger_start"), 0, "ledger_start")
    if start > through:
        raise ContractError("ledger_start %d is after published_through %d"
                            % (start, through))
    _at_least(document.get("published_at"), 0, "published_at")
    return document


def stamper_counters(**values):
    unknown = sorted(set(values) - set(STAMPER_COUNTERS))
    if unknown:
        raise ContractError("unknown counter %s; the stamper counters are %s"
                            % (", ".join(unknown), ", ".join(STAMPER_COUNTERS)))
    return {name: _at_least(values.get(name, 0), 0, name)
            for name in STAMPER_COUNTERS}


def check_id(name, node, family, resolution_ms, bucket_start, index=None):
    if name not in CHECKS:
        raise ContractError("%r is not a check; the checks are %s"
                            % (name, ", ".join(CHECKS)))
    parts = [CHECK_ID_PREFIX, name, _id_part(node, "node"),
             _id_part(family, "family"),
             str(require_resolution(resolution_ms) // SECOND_MS),
             str(require_bucket_start(bucket_start, resolution_ms))]
    if index is not None:
        parts.append(_text(index, "index"))
    return ":".join(parts)


def check_document(name, node, family, resolution_ms, bucket_start,
                   stamped, indexed, checked_at_ms, index=None, detail="",
                   versions_short=()):
    _at_least(stamped, 0, "stamped")
    _at_least(indexed, 0, "indexed")
    identifier = check_id(name, node, family, resolution_ms, bucket_start,
                          index)
    short, truncated = _scope(list(versions_short), "versions_short",
                              MAX_ENTRY_LINKS)
    ok = indexed <= stamped if name == CHECK_STAMPED_AGAINST_INDEXED \
        else indexed == stamped
    return {
        "kind": KIND_CHECK,
        "schema_version": SCHEMA_VERSION,
        "check_id": identifier,
        "check": name,
        "node": node,
        "family": family,
        "resolution_seconds": resolution_ms // SECOND_MS,
        "bucket_start": bucket_start,
        "index": index,
        "stamped": encode_int(stamped),
        "indexed": encode_int(indexed),
        "difference": encode_int(stamped - indexed),
        "ok": ok,
        "versions_short": short,
        "versions_short_truncated": truncated,
        "detail": detail if isinstance(detail, str) else "",
        "checked_at": _at_least(checked_at_ms, 0, "checked_at"),
    }


def definition_document(family, template, node, first_observed_ms,
                        last_observed_ms, catalogued_at_ms, programs=(),
                        origin_hosts=(), log_sources=(), severity_norm=None,
                        widened_into=(), widened_from=()):
    _text(family, "family")
    _text(template, "template")
    _at_least(first_observed_ms, 0, "first_observed")
    _at_least(last_observed_ms, 0, "last_observed")
    if last_observed_ms < first_observed_ms:
        raise ContractError("last_observed %d is before first_observed %d"
                            % (last_observed_ms, first_observed_ms))
    names, cut_programs = _scope(programs, "programs", MAX_ENTRY_PROGRAMS)
    hosts, cut_hosts = _scope(origin_hosts, "origin_hosts", MAX_ENTRY_HOSTS)
    sources, _ = _scope(log_sources, "log_sources", MAX_ENTRY_SOURCES)
    into, _ = _scope(widened_into, "widened_into", MAX_ENTRY_LINKS)
    origin, _ = _scope(widened_from, "widened_from", MAX_ENTRY_LINKS)
    if severity_norm is not None:
        _text(severity_norm, "severity_norm")
    identity = version_id(family, template)
    return {
        "kind": KIND_CATALOG_TEMPLATE,
        "schema_version": SCHEMA_VERSION,
        "version_id": identity,
        "canonical_id": canonical_id(template),
        "family": family,
        "template": template,
        "normalized": normalize(template),
        "token_count": len(template_tokens(template)),
        "programs": names,
        "programs_truncated": cut_programs,
        "origin_hosts": hosts,
        "origin_hosts_truncated": cut_hosts,
        "log_sources": sources,
        "severity_norm": severity_norm,
        "nodes": [_text(node, "node")],
        "nodes_truncated": False,
        "first_observed": first_observed_ms,
        "last_observed": last_observed_ms,
        "first_catalogued": _at_least(catalogued_at_ms, 0, "first_catalogued"),
        "widened_into": into,
        "widened_from": origin,
    }


def choose_cutoff(watermarks, now_ms, idle_ms=IDLE_NODE_MS):
    _whole(now_ms, "now_ms")
    live = {}
    idle = []
    for node in sorted(watermarks or {}):
        held = watermarks[node]
        through = _at_least(held.get("published_through"), 0,
                            "published_through")
        published = _at_least(held.get("published_at"), 0, "published_at")
        if now_ms - published > idle_ms:
            idle.append(node)
            continue
        live[node] = through
    if not live:
        return {"cutoff": None, "live": {}, "idle": idle}
    cutoff = bucket_start_ms(min(live.values()), COARSE_BUCKET_MS)
    return {"cutoff": cutoff, "live": live, "idle": idle}


def window_bounds(cutoff_ms, window_ms=WINDOW_MS):
    require_bucket_start(cutoff_ms, COARSE_BUCKET_MS)
    return cutoff_ms - window_ms, cutoff_ms


def window_coverage(cutoff_ms, nodes_in_window, watermarks, now_ms,
                    idle_ms=IDLE_NODE_MS):
    require_bucket_start(cutoff_ms, COARSE_BUCKET_MS)
    behind = []
    idle = []
    complete = []
    for node in sorted(set(nodes_in_window)):
        held = (watermarks or {}).get(node)
        if held is None:
            idle.append(node)
            continue
        if now_ms - _at_least(held.get("published_at"), 0,
                              "published_at") > idle_ms:
            idle.append(node)
            continue
        if _at_least(held.get("published_through"), 0,
                     "published_through") < cutoff_ms:
            behind.append(node)
            continue
        complete.append(node)
    if not nodes_in_window:
        status = COVERAGE_UNKNOWN
    elif behind or idle:
        status = COVERAGE_PARTIAL
    else:
        status = COVERAGE_COMPLETE
    gaps = []
    for node in behind:
        gaps.append(coverage_gap(GAP_NODE_BEHIND, node=node,
                                 detail="watermark of %s is before the "
                                        "cutoff %d" % (node, cutoff_ms)))
    for node in idle:
        gaps.append(coverage_gap(GAP_NODE_IDLE, node=node,
                                 detail="%s has not published for more than "
                                        "%d seconds" % (node,
                                                        idle_ms // SECOND_MS)))
    return {"status": status, "cutoff": cutoff_ms,
            "window_start": cutoff_ms - WINDOW_MS, "complete": complete,
            "behind": behind, "idle": idle, "gaps": gaps}


def coverage_gap(reason, detail="", records=0, node=None, index_name=None):
    if reason not in GAP_REASONS:
        raise ContractError("%r is not a coverage gap reason; use one of %s"
                            % (reason, ", ".join(GAP_REASONS)))
    gap = {"reason": reason, "detail": detail if isinstance(detail, str) else "",
           "records": _at_least(records, 0, "records")}
    if node is not None:
        gap["node"] = _text(node, "node")
    if index_name is not None:
        gap["index"] = _text(index_name, "index")
    return gap


def iso_utc(epoch_ms):
    _whole(epoch_ms, "epoch_ms")
    moment = _EPOCH + datetime.timedelta(milliseconds=epoch_ms)
    return "%s.%03dZ" % (moment.strftime("%Y-%m-%dT%H:%M:%S"),
                         moment.microsecond // 1000)


def _numeric_time(value):
    if value < 0:
        return ObservationTime(TIME_INVALID, None)
    if value < EPOCH_SECONDS_CUTOFF:
        return ObservationTime(TIME_OK, int(value) * 1000)
    return ObservationTime(TIME_OK, int(value))


def _iso_time(text):
    match = ISO_8601.match(text)
    if not match:
        return ObservationTime(TIME_INVALID, None)
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        moment = datetime.datetime(int(year), int(month), int(day), int(hour),
                                   int(minute), int(second), tzinfo=_UTC)
    except ValueError:
        return ObservationTime(TIME_INVALID, None)
    millis = 0
    if fraction:
        millis = int((fraction[1:] + "000")[:3])
    offset = 0
    if zone and zone not in ("Z", "z"):
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 3600000 + int(digits[2:]) * 60000)
    epoch_ms = (int((moment - _EPOCH).total_seconds()) * 1000 + millis
                - offset)
    if epoch_ms < 0:
        return ObservationTime(TIME_INVALID, None)
    return ObservationTime(TIME_OK, epoch_ms)


def parse_collector_time(value):
    if value is None:
        return ObservationTime(TIME_MISSING, None)
    if isinstance(value, bool):
        return ObservationTime(TIME_INVALID, None)
    if isinstance(value, int):
        return _numeric_time(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ObservationTime(TIME_INVALID, None)
        return _numeric_time(int(value))
    if not isinstance(value, str):
        return ObservationTime(TIME_INVALID, None)
    text = value.strip()
    if not text:
        return ObservationTime(TIME_MISSING, None)
    if (text[0].isdigit() and "-" not in text and "T" not in text
            and " " not in text):
        whole = text.split(".", 1)[0]
        try:
            return _numeric_time(int(whole))
        except ValueError:
            return ObservationTime(TIME_INVALID, None)
    return _iso_time(text)


def observation_time(record, now_ms, tolerance_ms=CLOCK_TOLERANCE_MS):
    if not isinstance(record, dict):
        return ObservationTime(TIME_MISSING, None)
    parsed = parse_collector_time(record.get(COLLECTOR_TIME_FIELD))
    if parsed.status != TIME_OK:
        return parsed
    if parsed.epoch_ms > _whole(now_ms, "now_ms") + tolerance_ms:
        return ObservationTime(TIME_OUT_OF_TOLERANCE, parsed.epoch_ms)
    return parsed


def gap_for_time(status):
    return {TIME_MISSING: GAP_MISSING_TIME,
            TIME_INVALID: GAP_INVALID_TIME,
            TIME_OUT_OF_TOLERANCE: GAP_CLOCK_TOLERANCE}.get(status)


def encode_int(value):
    _whole(value, "count")
    if JS_MIN_SAFE_INTEGER <= value <= JS_MAX_SAFE_INTEGER:
        return value
    return str(value)


def decode_int(value):
    if isinstance(value, bool):
        raise ContractError("a count must be an integer, got %r" % (value,))
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        body = text[1:] if text[:1] == "-" else text
        if body.isdigit() and body:
            return int(text)
    raise ContractError(
        "a count must be an integer or a decimal string, got %r" % (value,))


def sum_counts(entries):
    total = 0
    for entry in entries:
        total += decode_int(entry["count"])
    return total


def encode_document(document):
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ContractError("document is not JSON serialisable: %s" % exc)


def encoded_size(document):
    return len(encode_document(document).encode("utf-8"))


def checksum(value):
    return hashlib.sha256(
        encode_document(value).encode("utf-8")).hexdigest()[:32]


def is_active(last_observed_ms, now_ms, active_ms=ACTIVE_MS):
    return _whole(now_ms, "now_ms") - _whole(
        last_observed_ms, "last_observed") < active_ms


def is_retained_definition(last_observed_ms, now_ms,
                           retention_ms=DEFINITION_RETENTION_MS):
    return _whole(now_ms, "now_ms") - _whole(
        last_observed_ms, "last_observed") < retention_ms


def expired_definitions(cutoff_ms):
    return {"query": {"bool": {"filter": [
        {"term": {"kind": KIND_CATALOG_TEMPLATE}},
        {"range": {"last_observed": {"lte": _whole(cutoff_ms, "cutoff_ms"),
                                     "format": "epoch_millis"}}}]}}}


def expired_queries(cutoff_ms):
    return {"query": {"bool": {"filter": [
        {"term": {"kind": KIND_QUERY_EVENT}},
        {"range": {"issued_at": {"lt": _whole(cutoff_ms, "cutoff_ms"),
                                 "format": "epoch_millis"}}}]}}}


def expired_checks(cutoff_ms):
    return {"query": {"bool": {"filter": [
        {"term": {"kind": KIND_CHECK}},
        {"range": {"checked_at": {"lt": _whole(cutoff_ms, "cutoff_ms"),
                                 "format": "epoch_millis"}}}]}}}
