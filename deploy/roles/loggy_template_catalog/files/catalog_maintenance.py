#!/usr/bin/env python3
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get(
    "ALICE_SHARED_PATH", "/opt/loggy/shared"))

import template_contract as contract                          # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
CATALOG_INDEX = os.environ.get("CATALOG_INDEX", contract.CATALOG_INDEX)
QUERIES_INDEX = os.environ.get("QUERIES_INDEX", contract.QUERIES_INDEX)
BUCKETS_1H_PATTERN = os.environ.get(
    "BUCKETS_1H_PATTERN", contract.bucket_index_pattern(
        contract.COARSE_BUCKET_MS))
SHARED_INDICES = [name for name in os.environ.get(
    "CATALOG_SHARED_INDICES", "application-logs-central,infologger").split(",")
    if name]

RETENTION_DAYS = int(os.environ.get(
    "CATALOG_RETENTION_DAYS", str(contract.DEFINITION_RETENTION_DAYS)))
RETENTION_MS = RETENTION_DAYS * contract.DAY_MS
CHECK_RETENTION_DAYS = int(os.environ.get(
    "CATALOG_CHECK_RETENTION_DAYS", str(contract.COARSE_RETENTION_DAYS)))
CHECK_RETENTION_MS = CHECK_RETENTION_DAYS * contract.DAY_MS

QUERY_RETENTION_DAYS = int(os.environ.get(
    "CATALOG_QUERY_RETENTION_DAYS", str(contract.QUERY_RETENTION_DAYS)))
QUERY_RETENTION_MS = QUERY_RETENTION_DAYS * contract.DAY_MS
QUERY_INTERVAL_HOURS = int(os.environ.get(
    "CATALOG_QUERY_CLEANUP_INTERVAL_HOURS", "24"))
QUERY_INTERVAL_MS = QUERY_INTERVAL_HOURS * contract.HOUR_MS

BATCH_DOCUMENTS = int(os.environ.get("CATALOG_CLEANUP_BATCH", "1000"))
MAX_DOCUMENTS = int(os.environ.get("CATALOG_CLEANUP_MAX_DOCUMENTS", "50000"))
REQUESTS_PER_SECOND = int(os.environ.get("CATALOG_CLEANUP_RPS", "500"))
SCROLL_SIZE = int(os.environ.get("CATALOG_CLEANUP_SCROLL", "500"))
DELETE_TIMEOUT = int(os.environ.get("CATALOG_CLEANUP_TIMEOUT", "300"))
MAX_REPORTED_FAILURES = 5

CHECK_HOURS = int(os.environ.get("CATALOG_CHECK_HOURS", "1"))
CHECK_LAG_HOURS = int(os.environ.get("CATALOG_CHECK_LAG_HOURS", "1"))
CHECK_PAGE = int(os.environ.get("CATALOG_CHECK_PAGE", "200"))
CHECK_MAX_BUCKETS = int(os.environ.get("CATALOG_CHECK_MAX_BUCKETS", "5000"))
BULK_DOCUMENTS = int(os.environ.get("CATALOG_BULK_DOCUMENTS", "500"))

STATE_DIR = os.environ.get("CATALOG_MAINTENANCE_STATE_DIR",
                           "/var/lib/alice-catalog-maintenance")
STATE_FILE = "maintenance-state.json"
STATE_VERSION = 2

CATALOG_SECTION = "catalog"
QUERIES_SECTION = "queries"
CHECKS_SECTION = "checks"
HEALTH_SECTION = "health"
SECTIONS = (CATALOG_SECTION, QUERIES_SECTION, CHECKS_SECTION)

SECTION_AGE = {CATALOG_SECTION: "catalog_age_ms",
               QUERIES_SECTION: "queries_age_ms",
               CHECKS_SECTION: "checks_age_ms"}

KIND_MAINTENANCE_REPORT = "catalog_maintenance"
REPORT_ID_PREFIX = "maintenance"
MAINTENANCE_ID = socket.gethostname()

COARSE = contract.COARSE_BUCKET_MS


class MaintenanceError(Exception):
    pass


FAULTS = (MaintenanceError, contract.ContractError, urllib.error.URLError,
          OSError, ValueError)


def log(message):
    print("[catalog-maintenance] %s" % message, file=sys.stderr, flush=True)


class Transport(object):

    def __init__(self, base_url=OS_URL, timeout=DELETE_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, path, body=None, method="GET"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return json.load(response)

    def bulk(self, lines):
        body = ("\n".join(lines) + "\n").encode()
        req = urllib.request.Request(
            self.base_url + "/_bulk?refresh=false", data=body, method="POST",
            headers={"Content-Type": "application/x-ndjson"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return json.load(response)


def transport():
    return Transport()


def verify_bulk(result, expected_ids, ok_statuses=(200, 201)):
    if not isinstance(result, dict) or not isinstance(result.get("items"),
                                                      list):
        raise MaintenanceError("the bulk answer carries no item list")
    seen = set()
    for item in result["items"]:
        if not isinstance(item, dict) or len(item) != 1:
            raise MaintenanceError("a bulk item is not one named operation")
        body = list(item.values())[0]
        if not isinstance(body, dict):
            raise MaintenanceError("a bulk item body is not an object")
        if body.get("error") or body.get("status") not in ok_statuses:
            raise MaintenanceError("bulk write of %s failed: %s"
                                   % (body.get("_id"),
                                      json.dumps(body.get("error"))))
        seen.add(body.get("_id"))
    missing = [value for value in expected_ids if value not in seen]
    if missing:
        raise MaintenanceError("the bulk answer says nothing about %d "
                               "documents; first: %s"
                               % (len(missing), missing[0]))
    return len(seen)


def search_is_complete(page):
    if not isinstance(page, dict):
        return False
    if page.get("timed_out"):
        return False
    shards = page.get("_shards") or {}
    if shards.get("failed"):
        return False
    total = shards.get("total")
    parts = [shards.get(k) for k in ("successful", "skipped", "failed")]
    if isinstance(total, int) and all(isinstance(p, int) for p in parts):
        if sum(parts) < total:
            return False
    return True


def state_path():
    return os.path.join(STATE_DIR, STATE_FILE)


def fresh_state():
    state = {"version": STATE_VERSION}
    for name in SECTIONS:
        state[name] = None
    return state


def load_state(path=None):
    try:
        with open(path or state_path()) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return fresh_state()
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return fresh_state()
    for name in SECTIONS:
        state.setdefault(name, None)
    return state


def save_state(state, path=None):
    target = path or state_path()
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=directory, prefix=".maintenance-state-", delete=False)
    try:
        json.dump(state, handle)
        handle.flush()
        handle.close()
        os.replace(handle.name, target)
    except BaseException:
        handle.close()
        os.unlink(handle.name)
        raise
    return state


def cleanup_age_ms(state, section, now_ms):
    last = (state or {}).get(section)
    if not isinstance(last, int):
        return None
    return max(0, now_ms - last)


def definition_cutoff(now_ms, retention_ms=RETENTION_MS):
    return now_ms - retention_ms


def query_cutoff(now_ms, retention_ms=QUERY_RETENTION_MS):
    return now_ms - retention_ms


def check_cutoff(now_ms, retention_ms=CHECK_RETENTION_MS):
    return now_ms - retention_ms


def delete_path(index, max_docs, requests_per_second, scroll_size,
                timeout_seconds):
    throttle = requests_per_second if requests_per_second > 0 else -1
    return ("/%s/_delete_by_query?conflicts=proceed&refresh=true"
            "&wait_for_completion=true&max_docs=%d&scroll_size=%d"
            "&requests_per_second=%d&timeout=%ds"
            % (index, max_docs, min(scroll_size, max_docs), throttle,
               timeout_seconds))


def delete_batch(carrier, index, body, max_docs,
                 requests_per_second=REQUESTS_PER_SECOND,
                 scroll_size=SCROLL_SIZE, timeout_seconds=DELETE_TIMEOUT):
    answer = carrier.request(
        delete_path(index, max_docs, requests_per_second, scroll_size,
                    timeout_seconds),
        body, "POST")
    if not isinstance(answer, dict):
        raise MaintenanceError(
            "the delete answer from %s is %r and not an object; this pass "
            "cannot say what it removed" % (index, answer))
    return answer


def expiry_report(index, retention_ms, cutoff, batch, max_documents,
                  requests_per_second):
    return {
        "index": index,
        "retention_days": retention_ms // contract.DAY_MS,
        "cutoff": cutoff,
        "cutoff_iso": contract.iso_utc(max(0, cutoff)),
        "batch_documents": batch,
        "max_documents": max_documents,
        "requests_per_second": requests_per_second,
        "deleted": 0,
        "version_conflicts": 0,
        "batches": 0,
        "throttled_ms": 0,
        "took_ms": 0,
        "bounded": False,
        "conflicted": False,
        "failures": [],
    }


def expire_documents(carrier, index, body, report, batch, max_documents,
                     requests_per_second, scroll_size, timeout_seconds):
    while report["deleted"] < max_documents:
        room = min(batch, max_documents - report["deleted"])
        if room <= 0:
            break
        answer = delete_batch(carrier, index, body, room,
                              requests_per_second, scroll_size,
                              timeout_seconds)
        deleted = int(answer.get("deleted") or 0)
        conflicts = int(answer.get("version_conflicts") or 0)
        report["deleted"] += deleted
        report["version_conflicts"] += conflicts
        report["batches"] += 1
        report["throttled_ms"] += int(answer.get("throttled_millis") or 0)
        report["took_ms"] += int(answer.get("took") or 0)
        if conflicts:
            report["conflicted"] = True
        failures = answer.get("failures") or []
        if failures:
            report["failures"].extend(
                failures[:MAX_REPORTED_FAILURES - len(report["failures"])])
            break
        if answer.get("timed_out"):
            report["failures"].append(
                {"reason": "the delete request timed out after %d documents"
                           % deleted})
            break
        if deleted < room:
            break
    if report["deleted"] >= max_documents:
        report["bounded"] = True
    return report


def expire_catalog(carrier, now_ms, index=CATALOG_INDEX,
                   retention_ms=RETENTION_MS, batch=BATCH_DOCUMENTS,
                   max_documents=MAX_DOCUMENTS,
                   requests_per_second=REQUESTS_PER_SECOND,
                   scroll_size=SCROLL_SIZE, timeout_seconds=DELETE_TIMEOUT,
                   check_retention_ms=CHECK_RETENTION_MS):
    cutoff = definition_cutoff(now_ms, retention_ms)
    report = expiry_report(index, retention_ms, cutoff, batch, max_documents,
                           requests_per_second)
    expire_documents(carrier, index, contract.expired_definitions(cutoff),
                     report, batch, max_documents, requests_per_second,
                     scroll_size, timeout_seconds)
    checks = expiry_report(index, check_retention_ms,
                           check_cutoff(now_ms, check_retention_ms), batch,
                           max_documents, requests_per_second)
    if not report["failures"]:
        expire_documents(carrier, index,
                         contract.expired_checks(checks["cutoff"]), checks,
                         batch, max_documents, requests_per_second,
                         scroll_size, timeout_seconds)
    report["checks_deleted"] = checks["deleted"]
    report["checks_cutoff"] = checks["cutoff"]
    report["failures"].extend(checks["failures"])
    return report


def expire_queries(carrier, now_ms, index=QUERIES_INDEX,
                   retention_ms=QUERY_RETENTION_MS, batch=BATCH_DOCUMENTS,
                   max_documents=MAX_DOCUMENTS,
                   requests_per_second=REQUESTS_PER_SECOND,
                   scroll_size=SCROLL_SIZE, timeout_seconds=DELETE_TIMEOUT):
    cutoff = query_cutoff(now_ms, retention_ms)
    report = expiry_report(index, retention_ms, cutoff, batch, max_documents,
                           requests_per_second)
    report["due"] = True
    return expire_documents(carrier, index, contract.expired_queries(cutoff),
                            report, batch, max_documents, requests_per_second,
                            scroll_size, timeout_seconds)


def check_window(now_ms, hours=CHECK_HOURS, lag_hours=CHECK_LAG_HOURS):
    end = contract.bucket_start_ms(now_ms, COARSE) - (lag_hours - 1) * COARSE
    return end - hours * COARSE, end


def bucket_documents(carrier, start_ms, end_ms, pattern=BUCKETS_1H_PATTERN,
                     page=CHECK_PAGE, limit=CHECK_MAX_BUCKETS):
    after = None
    seen = 0
    while seen < limit:
        body = {
            "size": min(page, limit - seen),
            "track_total_hits": False,
            "sort": [{"bucket_start": {"order": "asc"}},
                     {"bucket_id": {"order": "asc"}}],
            "query": {"bool": {"filter": [
                {"term": {"kind": contract.KIND_BUCKET}},
                {"term": {"late": False}},
                {"range": {"bucket_start": {"gte": start_ms, "lt": end_ms,
                                            "format": "epoch_millis"}}}]}},
        }
        if after is not None:
            body["search_after"] = after
        answer = carrier.request(
            "/%s/_search?ignore_unavailable=true&allow_no_indices=true"
            % pattern, body, "POST")
        if not search_is_complete(answer):
            raise MaintenanceError(
                "%s answered for only some of its shards; a partial listing "
                "cannot say which buckets exist, so the checks stop here"
                % pattern)
        hits = ((answer.get("hits") or {}).get("hits") or [])
        if not hits:
            return
        for hit in hits:
            seen += 1
            yield hit.get("_source") or {}
        after = hits[-1].get("sort")
        if after is None or len(hits) < body["size"]:
            return


def conservation_check(document, now_ms):
    try:
        contract.validate_bucket(document)
        ok = True
        detail = ""
    except contract.ContractError as exc:
        ok = False
        detail = str(exc)
    rows = document.get("counts") or []
    total = 0
    for row in rows:
        try:
            total += contract.decode_int((row or {}).get("count"))
        except contract.ContractError:
            continue
    try:
        published = contract.decode_int(document.get("total"))
    except contract.ContractError:
        published = 0
    resolution = int(document.get("resolution_seconds") or 3600) * 1000
    check = contract.check_document(
        contract.CHECK_CONSERVATION, document.get("node"),
        document.get("family"), resolution, int(document.get("bucket_start")),
        published, total, now_ms, detail=detail)
    check["ok"] = ok and published == total
    return check


def indexed_versions(carrier, index, node, family, start_ms, end_ms):
    body = {
        "size": 0, "track_total_hits": False,
        "query": {"bool": {"filter": [
            {"term": {contract.NODE_FIELD: node}},
            {"range": {contract.COLLECTOR_TIME_FIELD: {
                "gte": start_ms, "lt": end_ms, "format": "epoch_millis"}}},
            {"prefix": {contract.TEMPLATE_VERSION_FIELD: family + ":"}}]}},
        "aggregations": {"versions": {"terms": {
            "field": contract.TEMPLATE_VERSION_FIELD,
            "size": contract.MAX_BUCKET_VERSIONS}}},
    }
    answer = carrier.request(
        "/%s/_search?ignore_unavailable=true&allow_no_indices=true" % index,
        body, "POST")
    if not search_is_complete(answer):
        raise MaintenanceError(
            "%s answered for only some of its shards for %s/%s at %d"
            % (index, node, family, start_ms))
    rows = (((answer.get("aggregations") or {}).get("versions") or {})
            .get("buckets") or [])
    return {row["key"]: int(row["doc_count"]) for row in rows}


def stamped_check(document, indexed, index, now_ms):
    stamped = {}
    for row in document.get("counts") or []:
        stamped[row["version_id"]] = contract.decode_int(row["count"])
    over = sorted(identity for identity, count in indexed.items()
                  if count > stamped.get(identity, 0))
    detail = ""
    if over:
        detail = ("%d versions have more indexed records than stamped ones"
                  % len(over))
    resolution = int(document.get("resolution_seconds") or 3600) * 1000
    check = contract.check_document(
        contract.CHECK_STAMPED_AGAINST_INDEXED, document["node"],
        document["family"], resolution, int(document["bucket_start"]),
        sum(stamped.values()), sum(indexed.values()), now_ms, index=index,
        detail=detail, versions_short=over)
    check["ok"] = not over
    return check


def publish_checks(carrier, checks, index=CATALOG_INDEX,
                   bulk_documents=BULK_DOCUMENTS):
    written = 0
    for start in range(0, len(checks), bulk_documents):
        batch = checks[start:start + bulk_documents]
        lines = []
        for check in batch:
            lines.append(json.dumps({"index": {"_index": index,
                                               "_id": check["check_id"]}}))
            lines.append(json.dumps(check))
        verify_bulk(carrier.bulk(lines), [c["check_id"] for c in batch])
        written += len(batch)
    return written


def run_checks(carrier, now_ms, catalog_index=CATALOG_INDEX,
               pattern=BUCKETS_1H_PATTERN, shared_indices=None,
               hours=CHECK_HOURS, lag_hours=CHECK_LAG_HOURS, page=CHECK_PAGE,
               limit=CHECK_MAX_BUCKETS, bulk_documents=BULK_DOCUMENTS):
    shared = SHARED_INDICES if shared_indices is None else list(shared_indices)
    start, end = check_window(now_ms, hours, lag_hours)
    report = {
        "pattern": pattern,
        "shared_indices": shared,
        "window_start": start,
        "window_end": end,
        "window_start_iso": contract.iso_utc(start),
        "window_end_iso": contract.iso_utc(end),
        "buckets": 0,
        "conservation_failures": 0,
        "stamped_checks": 0,
        "stamped_failures": 0,
        "published": 0,
        "truncated": False,
        "failures": [],
    }
    to_publish = []
    try:
        for document in bucket_documents(carrier, start, end, pattern, page,
                                         limit):
            report["buckets"] += 1
            conservation = conservation_check(document, now_ms)
            if not conservation["ok"]:
                report["conservation_failures"] += 1
                to_publish.append(conservation)
                continue
            for index in shared:
                try:
                    indexed = indexed_versions(
                        carrier, index, document["node"], document["family"],
                        int(document["bucket_start"]),
                        int(document["bucket_end"]))
                except FAULTS as exc:
                    report["failures"].append(
                        {"index": index, "node": document.get("node"),
                         "reason": str(exc)})
                    continue
                if not indexed:
                    continue
                check = stamped_check(document, indexed, index, now_ms)
                report["stamped_checks"] += 1
                if not check["ok"]:
                    report["stamped_failures"] += 1
                to_publish.append(check)
        if report["buckets"] >= limit:
            report["truncated"] = True
            report["failures"].append(
                {"reason": "more than %d bucket documents in the window; the "
                           "buckets beyond the ceiling were not checked"
                           % limit})
    except FAULTS as exc:
        report["failures"].append({"reason": str(exc)})
    if to_publish:
        try:
            report["published"] = publish_checks(carrier, to_publish,
                                                 catalog_index, bulk_documents)
        except FAULTS as exc:
            report["failures"].append({"reason": "the check results were "
                                                 "not published: %s" % exc})
    return report


def report_id(section):
    return "%s:%s" % (REPORT_ID_PREFIX, section)


def report_document(section, now_ms, age_ms, result, node_id=MAINTENANCE_ID):
    document = {
        "kind": KIND_MAINTENANCE_REPORT,
        "schema_version": contract.SCHEMA_VERSION,
        "maintenance_id": node_id,
        "section": section,
        "published_at": now_ms,
        "cleanup_age_ms": age_ms,
        "failures": len(result["failures"]),
        "detail": json.dumps(result, sort_keys=True),
    }
    if section == CHECKS_SECTION:
        document["deleted"] = 0
        document["version_conflicts"] = (result["conservation_failures"]
                                         + result["stamped_failures"])
    else:
        document["deleted"] = result["deleted"]
        document["version_conflicts"] = result["version_conflicts"]
    return document


def publish_report(carrier, report, now_ms, index=CATALOG_INDEX,
                   node_id=MAINTENANCE_ID):
    lines = []
    identifiers = []
    for section in SECTIONS:
        identifier = report_id(section)
        identifiers.append(identifier)
        lines.append(json.dumps({"index": {"_index": index,
                                           "_id": identifier}}))
        lines.append(json.dumps(report_document(
            section, now_ms, report[SECTION_AGE[section]], report[section],
            node_id)))
    verify_bulk(carrier.bulk(lines), identifiers)
    return identifiers


def run_pass(carrier, now_ms, state=None, catalog_index=CATALOG_INDEX,
             retention_ms=RETENTION_MS, batch=BATCH_DOCUMENTS,
             max_documents=MAX_DOCUMENTS,
             requests_per_second=REQUESTS_PER_SECOND,
             scroll_size=SCROLL_SIZE, timeout_seconds=DELETE_TIMEOUT,
             queries_index=QUERIES_INDEX,
             query_retention_ms=QUERY_RETENTION_MS,
             query_interval_ms=QUERY_INTERVAL_MS,
             check_retention_ms=CHECK_RETENTION_MS,
             pattern=BUCKETS_1H_PATTERN, shared_indices=None,
             check_hours=CHECK_HOURS, check_lag_hours=CHECK_LAG_HOURS,
             node_id=MAINTENANCE_ID):
    if state is None:
        state = fresh_state()
    queries_age = cleanup_age_ms(state, QUERIES_SECTION, now_ms)
    report = {"started": now_ms, "started_iso": contract.iso_utc(now_ms),
              "catalog_age_ms": cleanup_age_ms(state, CATALOG_SECTION, now_ms),
              "queries_age_ms": queries_age,
              "checks_age_ms": cleanup_age_ms(state, CHECKS_SECTION, now_ms)}
    try:
        catalog = expire_catalog(carrier, now_ms, catalog_index, retention_ms,
                                 batch, max_documents, requests_per_second,
                                 scroll_size, timeout_seconds,
                                 check_retention_ms)
    except FAULTS as exc:
        catalog = {"index": catalog_index, "deleted": 0,
                   "version_conflicts": 0, "batches": 0, "throttled_ms": 0,
                   "took_ms": 0, "bounded": False, "conflicted": False,
                   "checks_deleted": 0, "failures": [{"reason": str(exc)}]}
    if not catalog["failures"]:
        state[CATALOG_SECTION] = now_ms
    if queries_age is not None and queries_age < query_interval_ms:
        queries = expiry_report(queries_index, query_retention_ms,
                                query_cutoff(now_ms, query_retention_ms),
                                batch, max_documents, requests_per_second)
        queries["due"] = False
    else:
        try:
            queries = expire_queries(carrier, now_ms, queries_index,
                                     query_retention_ms, batch, max_documents,
                                     requests_per_second, scroll_size,
                                     timeout_seconds)
        except FAULTS as exc:
            queries = {"index": queries_index, "due": True, "deleted": 0,
                       "version_conflicts": 0, "batches": 0,
                       "throttled_ms": 0, "took_ms": 0, "bounded": False,
                       "conflicted": False,
                       "failures": [{"reason": str(exc)}]}
        if not queries["failures"]:
            state[QUERIES_SECTION] = now_ms
    checks = run_checks(carrier, now_ms, catalog_index, pattern,
                        shared_indices, check_hours, check_lag_hours)
    if not checks["failures"]:
        state[CHECKS_SECTION] = now_ms
    report[CATALOG_SECTION] = catalog
    report[QUERIES_SECTION] = queries
    report[CHECKS_SECTION] = checks
    health = {"index": catalog_index, "documents": [], "failures": []}
    try:
        health["documents"] = publish_report(carrier, report, now_ms,
                                             catalog_index, node_id)
    except FAULTS as exc:
        health["failures"].append({"reason": str(exc)})
    report[HEALTH_SECTION] = health
    report["failures"] = (len(catalog["failures"])
                          + len(queries["failures"])
                          + len(checks["failures"])
                          + len(health["failures"]))
    return report, state


def main():
    started = time.time()
    now_ms = int(started * 1000)
    state = load_state()
    report, state = run_pass(transport(), now_ms, state)
    report["duration_ms"] = int((time.time() - started) * 1000)
    try:
        save_state(state)
    except OSError as exc:
        log("the maintenance state could not be saved: %s" % exc)
    print(json.dumps(report))
    for section in SECTIONS + (HEALTH_SECTION,):
        for failure in report[section]["failures"]:
            log("%s: %s" % (section, json.dumps(failure)))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
