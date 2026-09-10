import copy
import fnmatch
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "deploy", "shared"))
sys.path.insert(0, os.path.join(ROOT, "tools", "templating"))
sys.path.insert(0, HERE)

import template_contract as contract                          # noqa: E402
import templates_view as view                                 # noqa: E402

HOUR = contract.HOUR_MS
FINE = contract.FINE_BUCKET_MS
CUTOFF = 1788876000000
NOW = CUTOFF + 7 * FINE + 30000
WINDOW_START = CUTOFF - contract.WINDOW_MS
STAMP = NOW - 60000


def definition(template, family="dpl", first=None, last=None,
               programs=("o2-gpu",), hosts=("epn146",), sources=("stdout",),
               severity="error", widened_into=(), widened_from=(),
               node="epn146"):
    last = CUTOFF - HOUR if last is None else last
    first = min(WINDOW_START + HOUR, last - HOUR) if first is None else first
    return contract.definition_document(
        family, template, node, first, last, first, programs, hosts, sources,
        severity, widened_into, widened_from)


def watermark(node, through=None, published_at=None, ledger_start=None):
    through = CUTOFF + 6 * FINE if through is None else through
    published_at = STAMP if published_at is None else published_at
    ledger_start = through - contract.LEDGER_MS if ledger_start is None \
        else ledger_start
    return contract.watermark_document(node, through, max(ledger_start, 0),
                                       published_at)


def bucket(node, family, start, counts, resolution=contract.COARSE_BUCKET_MS,
           late=False):
    return contract.bucket_document(node, family, resolution, start, counts,
                                    STAMP, late)


def version(template, family="dpl"):
    return contract.version_id(family, template)


def _field(doc, name):
    if name.endswith(".keyword"):
        name = name[:-len(".keyword")]
    return doc.get(name)


def _range_ok(value, spec):
    if value is None:
        return False
    if "gte" in spec and value < spec["gte"]:
        return False
    if "gt" in spec and value <= spec["gt"]:
        return False
    if "lte" in spec and value > spec["lte"]:
        return False
    if "lt" in spec and value >= spec["lt"]:
        return False
    return True


def matches(doc, clause):
    kind = next(iter(clause))
    body = clause[kind]
    if kind == "match_all":
        return True
    if kind == "bool":
        for part in body.get("filter", []) + body.get("must", []):
            if not matches(doc, part):
                return False
        for part in body.get("must_not", []):
            if matches(doc, part):
                return False
        should = body.get("should")
        if should:
            hits = sum(1 for part in should if matches(doc, part))
            if hits < body.get("minimum_should_match", 1):
                return False
        return True
    if kind == "term":
        field = next(iter(body))
        wanted = body[field]
        if isinstance(wanted, dict):
            wanted = wanted.get("value")
        return _field(doc, field) == wanted
    if kind == "terms":
        field = next(iter(body))
        held = _field(doc, field)
        if isinstance(held, list):
            return any(item in body[field] for item in held)
        return held in body[field]
    if kind == "range":
        field = next(iter(body))
        return _range_ok(_field(doc, field), body[field])
    if kind == "match_phrase":
        field = next(iter(body))
        needle = body[field]
        if isinstance(needle, dict):
            needle = needle.get("query", "")
        return str(needle).lower() in str(_field(doc, field) or "").lower()
    if kind == "nested":
        path = body["path"]
        rows = doc.get(path) or []
        inner = body["query"]
        for row in rows:
            flat = {f"{path}.{key}": value for key, value in row.items()}
            if matches(flat, inner):
                return True
        return False
    raise AssertionError(f"the fake cluster does not read {kind}")


class FakeTransport:
    def __init__(self, catalog=view.CATALOG_INDEX,
                 incidents=view.INCIDENTS_INDEX):
        self.catalog_index = catalog
        self.incidents_index = incidents
        self.catalog = {}
        self.buckets = {}
        self.records = {}
        self.incidents = []
        self.searches = 0
        self.bodies = []
        self.fail = None
        self.lock = threading.Lock()

    def put_definition(self, document):
        self.catalog[document["version_id"]] = copy.deepcopy(document)

    def put_watermark(self, document):
        self.catalog[document["watermark_id"]] = copy.deepcopy(document)

    def put_bucket(self, document):
        index = contract.bucket_index_name(
            document["resolution_seconds"] * contract.SECOND_MS,
            document["bucket_start"])
        self.buckets[(index, document["bucket_id"])] = copy.deepcopy(document)

    def put_record(self, index, record):
        self.records.setdefault(index, []).append(copy.deepcopy(record))

    def search(self, index, body, ignore_unavailable=False):
        with self.lock:
            self.searches += 1
            self.bodies.append((index, copy.deepcopy(body),
                                ignore_unavailable))
        if self.fail is not None:
            raise self.fail
        if index == self.catalog_index:
            return self._catalog(body)
        if index == self.incidents_index:
            return {"hits": {"hits": [{"_source": doc}
                                      for doc in self.incidents]}}
        if index.startswith(contract.FINE_BUCKETS_PREFIX) or \
                index.startswith(contract.COARSE_BUCKETS_PREFIX):
            return self._buckets(index, body)
        return self._records(index, body, ignore_unavailable)

    def _catalog(self, body):
        query = body.get("query") or {"match_all": {}}
        rows = [doc for doc in self.catalog.values() if matches(doc, query)]
        order = body.get("sort") or []
        keys = [(next(iter(item)), item[next(iter(item))].get("order", "asc"))
                for item in order]

        def sort_key(doc):
            return tuple(doc.get(name) or 0 for name, _ in keys)

        for name, direction in reversed(keys):
            rows.sort(key=lambda doc: doc.get(name) or 0,
                      reverse=direction == "desc")
        after = body.get("search_after")
        if after is not None:
            rows = [doc for doc in rows
                    if self._after(sort_key(doc), after, keys)]
        page = rows[:body.get("size", 10)]
        return {"hits": {"total": {"value": len(rows), "relation": "eq"},
                         "hits": [{"_source": copy.deepcopy(doc),
                                   "sort": list(sort_key(doc))}
                                  for doc in page]}}

    @staticmethod
    def _after(key, after, keys):
        for held, seen, (_, direction) in zip(key, after, keys):
            if held == seen:
                continue
            return held > seen if direction == "asc" else held < seen
        return False

    def _buckets(self, index, body):
        patterns = index.split(",")
        query = body.get("query") or {"match_all": {}}
        rows = [doc for (name, _), doc in self.buckets.items()
                if any(fnmatch.fnmatch(name, p) for p in patterns)
                and matches(doc, query)]
        aggs = body.get("aggs") or {}
        out = {}
        if "nodes" in aggs:
            size = aggs["nodes"]["terms"]["size"]
            nodes = {}
            for doc in rows:
                nodes[doc["node"]] = nodes.get(doc["node"], 0) + 1
            ordered = sorted(nodes.items(), key=lambda item: -item[1])
            out["nodes"] = {
                "buckets": [{"key": k, "doc_count": v}
                            for k, v in ordered[:size]],
                "sum_other_doc_count": sum(v for _, v in ordered[size:])}
        if "counts" in aggs:
            size = aggs["counts"]["aggs"]["versions"]["terms"]["size"]
            totals = {}
            for doc in rows:
                for row in doc["counts"]:
                    totals[row["version_id"]] = (
                        totals.get(row["version_id"], 0)
                        + contract.decode_int(row["count"]))
            ordered = sorted(totals.items(), key=lambda item: -item[1])
            out["counts"] = {"versions": {
                "buckets": [{"key": k, "doc_count": 1,
                             "total": {"value": float(v)}}
                            for k, v in ordered[:size]],
                "sum_other_doc_count": len(ordered[size:])}}
        return {"hits": {"hits": []}, "aggregations": out}

    def _records(self, index, body, ignore_unavailable):
        names = []
        for pattern in index.split(","):
            found = [name for name in self.records
                     if fnmatch.fnmatch(name, pattern)]
            if not found and "*" not in pattern and not ignore_unavailable:
                raise OSError(f"index {pattern} does not exist")
            names.extend(found)
        query = body.get("query") or {"match_all": {}}
        rows = []
        for name in names:
            for doc in self.records[name]:
                if matches(doc, query):
                    rows.append((name, doc))
        rows.sort(key=lambda item: item[1].get("collector_time") or 0,
                  reverse=True)
        page = rows[:body.get("size", 10)]
        wanted = body.get("_source")
        hits = []
        for name, doc in page:
            source = {k: v for k, v in doc.items()
                      if wanted is None or k in wanted}
            hits.append({"_id": doc.get("doc_id"), "_index": name,
                         "_source": source})
        return {"hits": {"hits": hits}}


def service(transport, limits=None, active_ms=view.ACTIVE_MS,
            clock=lambda: NOW):
    return view.TemplatesService(transport, limits=limits, clock=clock,
                                 active_ms=active_ms)


def farm(transport, nodes=("epn146", "epn228"), through=None):
    for node in nodes:
        transport.put_watermark(watermark(node, through))


A = "sent <NUM> bytes to <IP>"
W = "sent <*> bytes to <IP>"
X = "sent <*> <*> to <IP>"
OTHER = "queue full for detector <*>"


def seeded(transport=None):
    transport = transport or FakeTransport()
    farm(transport)
    for text in (A, W, X):
        transport.put_definition(definition(text))
    transport.put_definition(definition(OTHER, family="infologger",
                                        sources=("infologger",)))
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF - HOUR,
                                {version(A): 5, version(W): 2}))
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF - 2 * HOUR,
                                {version(A): 7}))
    transport.put_bucket(bucket("epn228", "dpl", CUTOFF - HOUR,
                                {version(A): 1, version(X): 3}))
    transport.put_bucket(bucket("epn228", "infologger", CUTOFF - 3 * HOUR,
                                {version(OTHER, "infologger"): 11}))
    return transport


def test_the_cutoff_is_the_hour_every_live_node_has_published_past():
    transport = FakeTransport()
    transport.put_watermark(watermark("epn146", CUTOFF + 7 * FINE))
    transport.put_watermark(watermark("epn228", CUTOFF + 11 * FINE))
    transport.put_watermark(watermark("epn323", CUTOFF - 2 * FINE,
                                      published_at=NOW - 2 * HOUR))
    transport.put_definition(definition(A))
    built = service(transport).refresh(NOW)
    assert built.cutoff == CUTOFF
    assert built.window["window_end"] == CUTOFF
    assert built.window["window_start"] == WINDOW_START
    assert built.window["window_end_iso"] == contract.iso_utc(CUTOFF)
    assert built.window["bucket_seconds"] == 3600
    assert built.window["window_days"] == contract.WINDOW_DAYS
    assert sorted(built.watermarks) == ["epn146", "epn228", "epn323"]


def test_the_volume_is_the_sum_over_the_hourly_buckets_in_the_window():
    built = service(seeded()).refresh(NOW)
    assert contract.decode_int(built.by_version[version(A)]["count"]) == 13
    assert contract.decode_int(built.by_version[version(W)]["count"]) == 2
    assert contract.decode_int(built.by_version[version(X)]["count"]) == 3
    assert contract.decode_int(
        built.by_version[version(OTHER, "infologger")]["count"]) == 11
    assert built.total_records == 29
    assert built.coverage["status"] == contract.COVERAGE_COMPLETE
    assert built.status == view.STATUS_EXACT
    assert all(row["count_status"] == view.STATUS_EXACT
               for row in built.rows)


def test_buckets_at_or_after_the_cutoff_and_late_buckets_are_not_counted():
    transport = seeded()
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF, {version(A): 100}))
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF - HOUR,
                                {version(A): 100}, late=True))
    transport.put_bucket(bucket("epn146", "dpl", WINDOW_START - HOUR,
                                {version(A): 100}))
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF - FINE,
                                {version(A): 100}, resolution=FINE))
    built = service(transport).refresh(NOW)
    assert contract.decode_int(built.by_version[version(A)]["count"]) == 13
    read = [index for index, body, _ in transport.bodies
            if index.startswith("template-buckets")]
    assert read == [view.COARSE_BUCKETS]


def test_partial_coverage_names_the_idle_and_unwatermarked_nodes():
    transport = seeded()
    transport.put_watermark(watermark("epn228",
                                      published_at=NOW - 2 * HOUR))
    transport.put_bucket(bucket("epn323", "dpl", CUTOFF - HOUR,
                                {version(A): 4}))
    built = service(transport).refresh(NOW)
    assert built.coverage["status"] == contract.COVERAGE_PARTIAL
    assert built.coverage["idle"] == ["epn228", "epn323"]
    assert built.coverage["behind"] == []
    assert built.coverage["complete"] == ["epn146"]
    assert built.status == view.STATUS_INCOMPLETE
    assert contract.decode_int(built.by_version[version(A)]["count"]) == 17
    assert built.by_version[version(A)]["count_status"] == \
        view.STATUS_INCOMPLETE
    reasons = [(gap["reason"], gap["node"]) for gap in built.coverage["gaps"]]
    assert reasons == [(contract.GAP_NODE_IDLE, "epn228"),
                       (contract.GAP_NODE_IDLE, "epn323")]


def test_a_node_whose_watermark_stops_before_the_cutoff_is_behind():
    transport = seeded()
    built = service(transport).refresh(NOW)
    coverage = contract.window_coverage(
        CUTOFF, ["epn146", "epn228"],
        {"epn146": built.watermarks["epn146"],
         "epn228": {"published_through": CUTOFF - FINE,
                    "published_at": STAMP}}, NOW)
    assert coverage["status"] == contract.COVERAGE_PARTIAL
    assert coverage["behind"] == ["epn228"]
    assert coverage["gaps"][0]["reason"] == contract.GAP_NODE_BEHIND


def test_no_live_watermark_gives_an_unknown_view_that_names_the_idle_nodes():
    transport = FakeTransport()
    transport.put_watermark(watermark("epn146",
                                      published_at=NOW - 2 * HOUR))
    built = service(transport).refresh(NOW)
    assert built.cutoff is None
    assert built.rows == ()
    assert built.status == view.STATUS_UNKNOWN
    assert built.coverage["status"] == contract.COVERAGE_UNKNOWN
    assert built.coverage["idle"] == ["epn146"]
    assert "epn146" in built.note


def test_descendants_follow_the_cover_relation_and_never_widen():
    built = service(seeded()).refresh(NOW)
    rows = built.by_version
    assert rows[version(X)]["descendants"] == sorted([version(A),
                                                      version(W)])
    assert rows[version(X)]["descendant_count"] == 2
    assert rows[version(W)]["descendants"] == [version(A)]
    assert rows[version(A)]["descendants"] == []
    assert rows[version(OTHER, "infologger")]["descendants"] == []
    assert built.descendants(version(W)) == [version(A)]
    assert built.descendants("dpl:missing") == []
    assert built.coverers(version(A)) == sorted([version(W), version(X)])
    assert built.coverers(version(X)) == []
    assert built.coverers("dpl:missing") == []


def test_a_covered_block_sums_the_version_and_the_versions_it_covers():
    built = service(seeded()).refresh(NOW)
    rows = built.by_version
    covered = built.covered(version(X))
    assert [row["version_id"] for row in covered["versions"]] == sorted(
        [version(A), version(W)])
    expected = sum(contract.decode_int(rows[v]["count"])
                   for v in (version(X), version(A), version(W)))
    assert contract.decode_int(covered["count"]) == expected
    assert built.covered(version(A))["versions"] == []
    assert contract.decode_int(built.covered(version(A))["count"]) == \
        contract.decode_int(rows[version(A)]["count"])


def test_ancestors_walk_the_observed_widening_links_transitively():
    transport = FakeTransport()
    farm(transport)
    transport.put_definition(definition(A, widened_into=[version(W)]))
    transport.put_definition(definition(W, widened_from=[version(A)],
                                        widened_into=[version(X)]))
    transport.put_definition(definition(X, widened_from=[version(W)]))
    built = service(transport).refresh(NOW)
    assert built.ancestors(version(A)) == [version(W), version(X)]
    assert built.ancestors(version(X)) == []
    assert built.ancestors(version(A), limit=1) == [version(W)]
    assert built.by_version[version(A)]["widened_into"] == [version(W)]
    assert built.by_version[version(W)]["widened_from"] == [version(A)]


def test_a_row_carries_the_new_shape_and_none_of_the_old_one():
    built = service(seeded()).refresh(NOW)
    row = built.by_version[version(A)]
    for name in ("version_id", "family", "template", "token_count",
                 "programs", "origin_hosts", "log_sources", "severity_norm",
                 "count", "count_status", "first_observed", "last_observed",
                 "first_catalogued", "active", "historical", "widened_into",
                 "widened_from", "descendants", "descendant_count", "label",
                 "label_conflicts", "watched", "score"):
        assert name in row, name
    for name in ("canonical_id", "normalized", "template_id",
                 "canonical_versions",
                 "superseded_by", "supersedes", "relationship_verified",
                 "historical_programs", "historical_origin_hosts",
                 "contributing_producers", "missing_producers",
                 "open_bucket_observed", "open_bucket_last_observed",
                 "awaiting_volume"):
        assert name not in row, name
    assert row["active"] is True
    assert row["historical"] is False
    assert row["token_count"] == 5
    assert row["first_catalogued"] == row["first_observed"]


def test_versions_counted_without_a_definition_are_tallied_not_shown():
    transport = seeded()
    transport.put_bucket(bucket("epn146", "dpl", CUTOFF - 4 * HOUR,
                                {version("orphan <NUM>"): 9}))
    built = service(transport).refresh(NOW)
    assert version("orphan <NUM>") not in built.by_version
    assert built.uncatalogued == {"versions": 1, "records": 9}
    assert built.total_records == 29


def test_the_list_is_ordered_by_volume_then_by_recency_and_pages_once():
    built = service(seeded()).refresh(NOW)
    first = built.page(sort=view.SORT_VOLUME, page_size=2)
    assert [row["version_id"] for row in first["rows"]] == [
        version(A), version(OTHER, "infologger")]
    assert first["has_more"] is True
    second = built.page(sort=view.SORT_VOLUME, page_size=2,
                        after=first["after"])
    assert [row["version_id"] for row in second["rows"]] == [
        version(X), version(W)]
    assert second["has_more"] is False
    with pytest.raises(view.ViewRefused):
        built.page(after=["not a number", "x"])


def test_the_summary_shows_the_cutoff_the_coverage_and_the_watermarks():
    served = service(seeded())
    served.refresh(NOW)
    summary = served.summary(NOW)
    assert summary["window"]["window_end"] == CUTOFF
    assert summary["window"]["age_ms"] == NOW - CUTOFF
    assert summary["coverage"]["status"] == contract.COVERAGE_COMPLETE
    assert summary["coverage"]["complete"] == ["epn146", "epn228"]
    assert summary["watermarks"]["epn146"]["published_through"] == \
        CUTOFF + 6 * FINE
    assert summary["watermarks"]["epn146"]["age_ms"] == NOW - STAMP
    assert summary["totals"]["records"] == 29
    assert summary["totals"]["records_status"] == view.STATUS_EXACT
    assert summary["stale"] is False
    assert summary["expired_window"] is None
    assert summary["rematch_available"] is True


def test_a_stale_view_is_marked_and_an_expired_one_is_never_current():
    transport = seeded()
    clock = {"now": NOW}
    served = service(transport, clock=lambda: clock["now"])
    served.refresh(NOW)
    assert served.stale(NOW) is False

    later = CUTOFF + served.limits.stale_after_ms + 1
    clock["now"] = later
    assert served.stale(later) is True
    assert served.current(later).has_rows()
    summary = served.summary(later)
    assert summary["stale"] is True
    assert summary["totals"]["records"] == 29

    much_later = CUTOFF + served.limits.expire_after_ms + 1
    clock["now"] = much_later
    expired = served.current(much_later)
    assert expired.has_rows() is False
    assert expired.status == view.STATUS_UNKNOWN
    assert expired.expired["window_end"] == CUTOFF
    summary = served.summary(much_later)
    assert summary["totals"]["records"] == 0
    assert summary["totals"]["records_status"] == view.STATUS_UNKNOWN
    assert summary["expired_window"]["window_end_iso"] == \
        contract.iso_utc(CUTOFF)
    assert "history" in summary["note"]


def test_a_refresh_with_no_live_node_keeps_the_last_view_and_says_so():
    transport = seeded()
    served = service(transport)
    served.refresh(NOW)
    for node in ("epn146", "epn228"):
        transport.put_watermark(watermark(node, published_at=NOW - 2 * HOUR))
    with pytest.raises(view.ViewRefused) as refused:
        served.refresh(NOW + 1000)
    assert "last view is kept" in str(refused.value)
    assert served.current(NOW + 1000).total_records == 29
    assert served.failures == 1
    assert served.summary(NOW + 1000)["last_error"]


def test_a_second_refresh_inside_the_interval_is_skipped():
    transport = seeded()
    served = service(transport)
    assert served.maybe_refresh(NOW) is True
    searches = transport.searches
    assert served.maybe_refresh(NOW + 1000) is False
    assert transport.searches == searches
    assert served.maybe_refresh(NOW + served.limits.refresh_interval_ms) \
        is True


def test_a_truncated_aggregation_refuses_the_view_rather_than_a_partial_sum():
    transport = seeded()
    served = service(transport, limits=view.Limits(max_versions=2))
    with pytest.raises(view.ViewRefused) as refused:
        served.refresh(NOW)
    assert "truncated" in str(refused.value)
    assert served.current(NOW).has_rows() is False


def test_the_inactive_history_reads_the_catalog_by_last_observed():
    transport = seeded()
    old = "an older message <NUM>"
    transport.put_definition(definition(old, last=WINDOW_START - HOUR))
    gone = "a forgotten message <NUM>"
    transport.put_definition(definition(
        gone, last=CUTOFF - contract.DEFINITION_RETENTION_MS - HOUR))
    served = service(transport)
    served.refresh(NOW)
    active = served.list_rows({}, NOW)
    assert version(old) not in [row["version_id"] for row in active["rows"]]
    assert active["coverage"] == {"status": contract.COVERAGE_COMPLETE,
                                  "behind": [], "idle": []}
    assert active["window_end"] == CUTOFF
    history = served.list_rows({"include_inactive": True}, NOW)
    identifiers = [row["version_id"] for row in history["rows"]]
    assert version(old) in identifiers
    assert version(gone) not in identifiers
    row = [r for r in history["rows"] if r["version_id"] == version(old)][0]
    assert row["historical"] is True
    assert row["active"] is False
    assert row["count"] == 0
    assert row["origin_hosts"] == ["epn146"]


def test_the_detail_reads_the_catalog_for_a_version_outside_the_view():
    transport = seeded()
    old = "an older message <NUM>"
    transport.put_definition(definition(old, last=WINDOW_START - HOUR))
    served = service(transport)
    served.refresh(NOW)
    detail = served.detail(version(old), NOW)
    assert detail["version"]["historical"] is True
    assert detail["covered"]["versions"] == []
    assert detail["covered"]["count"] == detail["version"]["count"]
    with pytest.raises(view.ViewRefused):
        served.detail("dpl:0123456789abcdef01234567", NOW)


def lines_farm():
    transport = seeded()
    transport.put_bucket(bucket("epn323", "dpl", CUTOFF + FINE,
                                {version(A): 2}, resolution=FINE))
    transport.put_watermark(watermark("epn323"))
    n = 0
    for node, stamp, message, when in (
            ("epn146", version(A), "sent 42 bytes to 10.0.0.7", CUTOFF - 1),
            ("epn146", version(W), "sent 42 bytes to 10.0.0.7", CUTOFF - 2),
            ("epn228", version(X), "sent 42 bytes to 10.0.0.9", CUTOFF - 3),
            ("epn228", version(X), "sent error report to 10.0.0.9",
             CUTOFF - 4),
            ("epn323", version(A), "sent 7 bytes to 10.0.0.1", CUTOFF + 5),
            ("epn999", version(A), "sent 9 bytes to 10.0.0.1", CUTOFF + 6),
            ("epn146", version(OTHER, "infologger"), "queue full", CUTOFF)):
        n += 1
        index = ("infologger" if stamp.startswith("infologger")
                 else f"application-logs-local-{node}")
        transport.put_record(index, {
            "doc_id": f"r{n}", "node": node, "collector_time": when,
            "message": message, "template_version": stamp,
            "template_status": "matched",
            "log_source": "stdout", "program": "o2-gpu",
            "origin_host": f"{node}.cern.ch", "severity_norm": "error"})
    return transport


def searched_indices(transport):
    return [index for index, body, _ in transport.bodies
            if "template_version" in str(body.get("query"))]


def test_lines_are_routed_to_the_nodes_the_buckets_name_and_no_other():
    transport = lines_farm()
    served = service(transport)
    served.refresh(NOW)
    found = served.lines({"version_id": version(W)}, NOW)
    assert found["expanded"] == [version(W), version(A)]
    assert found["nodes"] == ["epn146", "epn228", "epn323"]
    assert found["every_node"] is False
    assert found["indices"] == [
        "application-logs-local-epn146", "application-logs-local-epn228",
        "application-logs-local-epn323", "infologger",
        "application-logs-central"]
    assert [row["doc_id"] for row in found["rows"]] == ["r5", "r1", "r2"]
    assert found["matched"] == 3
    assert found["fetched"] == 3
    assert found["cutoff"] == CUTOFF
    assert found["cutoff_iso"] == contract.iso_utc(CUTOFF)
    assert found["ancestors_included"] is False
    assert "open five-minute bucket" in found["note"]
    index, body, ignore = [entry for entry in transport.bodies
                           if entry[0] == searched_indices(transport)[-1]][0]
    assert ignore is True
    assert body["size"] == served.limits.lines_rows
    assert body["query"]["bool"]["filter"][0] == {
        "terms": {"template_version": [version(W), version(A)]}}


def test_the_route_reads_both_bucket_resolutions_over_the_query_window():
    transport = lines_farm()
    served = service(transport)
    served.refresh(NOW)
    served.lines({"version_id": version(A)}, NOW)
    index, body, ignore = [entry for entry in transport.bodies
                           if entry[0].startswith("template-buckets-5m")][0]
    assert index == f"{view.FINE_BUCKETS},{view.COARSE_BUCKETS}"
    assert ignore is True
    filters = body["query"]["bool"]["filter"]
    assert filters[1] == {"nested": {"path": "counts", "query": {
        "terms": {"counts.version_id": [version(A)]}}}}
    assert "nodes" in body["aggs"]


def test_every_node_widens_the_search_to_the_local_pattern():
    transport = lines_farm()
    served = service(transport)
    served.refresh(NOW)
    found = served.lines({"version_id": version(A), "every_node": True}, NOW)
    assert found["every_node"] is True
    assert found["indices"] == ["application-logs-local-*", "infologger",
                                "application-logs-central"]
    assert [row["doc_id"] for row in found["rows"]] == ["r6", "r5", "r1"]
    assert found["nodes"] == []
    assert "slowest node" in found["note"]
    assert not [entry for entry in transport.bodies
                if entry[0].startswith("template-buckets-5m")]


def test_include_ancestors_rematches_token_wise_against_the_selection():
    transport = lines_farm()
    transport.put_definition(definition(W, widened_from=[version(A)],
                                        widened_into=[version(X)]))
    transport.put_definition(definition(X, widened_from=[version(W)]))
    served = service(transport)
    served.refresh(NOW)
    found = served.lines({"version_id": version(W),
                          "include_ancestors": True}, NOW)
    assert found["ancestors"] == [version(X)]
    assert found["ancestors_included"] is True
    assert found["fetched"] == 5
    assert [row["doc_id"] for row in found["rows"]] == ["r5", "r1", "r2",
                                                        "r3"]
    assert "tokens fit" in found["note"]
    filters = [entry for entry in transport.bodies
               if entry[0] == searched_indices(transport)[-1]][0][1]
    assert filters["query"]["bool"]["filter"][0]["terms"][
        "template_version"] == [version(W), version(A), version(X)]


def test_the_rematch_degrades_with_a_stated_note_without_the_recipe(
        monkeypatch):
    transport = lines_farm()
    transport.put_definition(definition(W, widened_into=[version(X)]))
    served = service(transport)
    served.refresh(NOW)
    monkeypatch.setattr(view, "drainbench", None)
    assert view.rematch_available() is False
    assert view.line_fits("dpl", W, "sent 42 bytes to 10.0.0.7") is False
    found = served.lines({"version_id": version(W),
                          "include_ancestors": True}, NOW)
    assert found["ancestors"] == [version(X)]
    assert found["ancestors_included"] is False
    assert "not importable" in found["note"]
    assert [row["doc_id"] for row in found["rows"]] == ["r5", "r1", "r2"]


def test_line_fits_reads_the_masked_tokens_the_stamper_mined():
    assert view.line_fits("dpl", W, "sent 42 bytes to 10.0.0.7")
    assert view.line_fits("dpl", A, "[14:02:11][ERROR] sent 42 bytes to "
                                    "10.0.0.7")
    assert not view.line_fits("dpl", W, "sent error report to 10.0.0.7")
    assert not view.line_fits("dpl", W, "sent 42 bytes to host7")
    assert view.line_fits("dpl", X, "sent error report to 10.0.0.7")
    assert not view.line_fits("dpl", W, "")
    assert not view.line_fits("dpl", W, None)
    assert not view.line_fits("no-such-family", W, "sent 42 bytes to 1.2.3.4")


def test_lines_honour_the_limit_the_bounds_and_refuse_bad_times():
    transport = lines_farm()
    served = service(transport)
    served.refresh(NOW)
    found = served.lines({"version_id": version(A), "limit": 1,
                          "every_node": True}, NOW)
    assert found["matched"] == 1
    assert found["truncated"] is True
    found = served.lines({"version_id": version(A), "every_node": True,
                          "since": CUTOFF, "until": CUTOFF + 5}, NOW)
    assert [row["doc_id"] for row in found["rows"]] == ["r5"]
    assert found["since"] == CUTOFF
    assert found["until"] == CUTOFF + 5
    found = served.lines({"version_id": version(A), "every_node": True,
                          "since": contract.iso_utc(CUTOFF + 6)}, NOW)
    assert [row["doc_id"] for row in found["rows"]] == ["r6"]
    with pytest.raises(view.ViewRefused):
        served.lines({"version_id": version(A), "since": "yesterday"}, NOW)
    with pytest.raises(view.ViewRefused):
        served.lines({"version_id": version(A), "since": CUTOFF,
                      "until": CUTOFF - 1}, NOW)
    with pytest.raises(view.ViewRefused):
        served.lines({}, NOW)
    with pytest.raises(view.ViewRefused):
        served.lines({"version_id": "dpl:0123456789abcdef01234567"}, NOW)


def test_a_route_over_the_node_ceiling_falls_back_to_every_node():
    transport = lines_farm()
    served = service(transport)
    served.refresh(NOW)
    served.limits.route_nodes = 1
    found = served.lines({"version_id": version(A)}, NOW)
    assert found["every_node"] is True
    assert found["indices"][0] == "application-logs-local-*"


def test_the_limits_hold_together_and_refuse_nonsense():
    limits = view.Limits()
    assert limits.stale_after_ms == (contract.COARSE_BUCKET_MS
                                     + contract.IDLE_NODE_MS
                                     + contract.PUBLISH_INTERVAL_MS)
    assert limits.expire_after_ms == 2 * limits.stale_after_ms
    with pytest.raises(ValueError):
        view.Limits(page_rows=0)
    with pytest.raises(ValueError):
        view.Limits(lines_rows=view.LINES_ROWS_CEILING + 1)
    with pytest.raises(ValueError):
        view.Limits(episode_interval_ms=1000)
    with pytest.raises(ValueError):
        view.Limits(page_rows="50")


def test_the_transport_stamps_a_deadline_and_the_ignore_flag():
    seen = {}

    class Probe(view.OpenSearchTransport):
        def _post(self, path, body):
            seen["path"] = path
            seen["body"] = body
            return {}

    probe = Probe("http://storage.invalid/", search_timeout_ms=1234,
                  terminate_after=99)
    probe.search("a,b", {"size": 1}, ignore_unavailable=True)
    assert seen["path"] == "/a,b/_search?ignore_unavailable=true"
    assert seen["body"]["timeout"] == "1234ms"
    assert seen["body"]["terminate_after"] == 99
    probe.search("a", {"size": 1})
    assert seen["path"] == "/a/_search"


def test_the_predicate_reads_every_filter():
    built = service(seeded()).refresh(NOW)
    rows = list(built.rows)
    assert view._predicate({}) is None
    only = view._predicate({"family": ["infologger"]})
    assert [r["version_id"] for r in rows if only(r)] == [
        version(OTHER, "infologger")]
    text = view._predicate({"query": "SENT <*> BYTES"})
    assert [r["version_id"] for r in rows if text(r)] == [version(W)]
    typed = view._predicate({"query": "sent <NUM> bytes"})
    assert [r["version_id"] for r in rows if typed(r)] == [version(A)]
    by_id = view._predicate({"query": version(A)})
    assert [r["version_id"] for r in rows if by_id(r)] == [version(A)]
    host = view._predicate({"host": ["epn999"]})
    assert [r for r in rows if host(r)] == []


def test_the_episodes_are_read_at_their_own_cadence():
    transport = seeded()
    transport.incidents = [{"incident_id": "i-1", "state": "firing",
                            "alertname": "Flood", "entity_id": "epn146",
                            "group_id": "g-1", "episode_start": CUTOFF}]
    served = service(transport)
    first = served.episodes(NOW)
    assert first["episodes"][0]["incident_id"] == "i-1"
    assert first["stale"] is False
    searches = transport.searches
    served.episodes(NOW + 1000)
    assert transport.searches == searches
    transport.fail = OSError("gone")
    later = served.episodes(NOW + served.limits.episode_interval_ms)
    assert later["stale"] is True
    assert later["episodes"][0]["incident_id"] == "i-1"
