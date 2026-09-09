import contextlib
import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "deploy", "shared"))
sys.path.insert(0, os.path.join(ROOT, "tools", "templating"))
sys.path.insert(0, HERE)

import template_contract as contract                          # noqa: E402
import templates_view as view                                 # noqa: E402
import semantic                                               # noqa: E402
import shifter                                                # noqa: E402
import triage                                                 # noqa: E402
from test_triage import FakeTransport as FakeTriageTransport   # noqa: E402
import test_templates_view as fx                              # noqa: E402

CUTOFF = fx.CUTOFF
NOW = fx.NOW
HOUR = fx.HOUR
TEMPLATE = "failed to allocate <NUM> bytes"
VERSION = contract.version_id("dpl", TEMPLATE)
CANONICAL = contract.canonical_id(TEMPLATE)
BIG = 2 ** 40 + 3


def farm(entries=((TEMPLATE, 7),), node="epn146"):
    transport = fx.FakeTransport()
    fx.farm(transport, nodes=(node,))
    counts = {}
    for text, count in entries:
        transport.put_definition(fx.definition(text, node=node))
        counts[contract.version_id("dpl", text)] = count
    if counts:
        transport.put_bucket(fx.bucket(node, "dpl", CUTOFF - HOUR, counts))
    return transport


class FailingSearch(semantic.DisabledSearch):
    def search(self, text, limit=None):
        raise RuntimeError("the model did not load on this host")


class ReadySearch(semantic.DisabledSearch):
    def __init__(self, hits, neighbours=()):
        semantic.DisabledSearch.__init__(self)
        self.hits = tuple(hits)
        self.neighbour_hits = tuple(neighbours)
        self.submitted = []

    def submit(self, groups):
        self.submitted.append(list(groups))
        return True

    def search(self, text, limit=None):
        return semantic.SearchResult(
            status=semantic.STATUS_READY,
            coverage=contract.COVERAGE_COMPLETE,
            reason=semantic.REASON_NONE, detail="", hits=self.hits,
            truncated=False, groups=len(self.hits),
            groups_total=len(self.hits),
            vector_bytes=2048, model_revision="rev-1",
            limit=limit or 50, took_ms=1)

    def neighbours(self, canonical_id, limit=None):
        return semantic.SearchResult(
            status=semantic.STATUS_READY,
            coverage=contract.COVERAGE_COMPLETE,
            reason=semantic.REASON_NONE, detail="",
            hits=self.neighbour_hits, truncated=False,
            groups=len(self.neighbour_hits),
            groups_total=len(self.neighbour_hits),
            vector_bytes=2048, model_revision="rev-1",
            limit=limit or 5, took_ms=1)


def runtime(transport=None, search=None, triage_store=None, queries=None,
            limits=None, active_ms=view.ACTIVE_MS):
    transport = transport if transport is not None else farm()
    limits = limits or view.Limits()
    service = view.TemplatesService(transport, limits=limits,
                                    clock=lambda: NOW, active_ms=active_ms)
    decisions = FakeTriageTransport()
    return shifter.TemplatesRuntime(
        service,
        search if search is not None else semantic.DisabledSearch(),
        triage_store or triage.LabelStore(decisions, clock=lambda: NOW),
        queries or triage.QueryLog(decisions, clock=lambda: NOW),
        limits, clock=lambda: NOW), transport


@contextlib.contextmanager
def serving(built):
    server = ThreadingHTTPServer(("127.0.0.1", 0), shifter.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = shifter.TEMPLATES
    shifter.TEMPLATES = built
    try:
        yield server.server_address
    finally:
        shifter.TEMPLATES = previous
        server.shutdown()
        server.server_close()
        thread.join(5)


def call(address, method, path, payload=None):
    connection = http.client.HTTPConnection(address[0], address[1], timeout=10)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    connection.close()
    return response.status, raw


def test_the_live_lane_and_the_query_lane_survive_a_dead_opensearch(
        monkeypatch):
    transport = farm()
    transport.fail = OSError("no route to the storage tier")
    built, _ = runtime(transport)
    built.cycle()

    monkeypatch.setattr(shifter, "OS_URL", "http://storage.invalid")
    monkeypatch.setattr(shifter, "opensearch_search", lambda body: {
        "hits": {"total": {"value": 1, "relation": "eq"},
                 "hits": [{"_id": "1", "_source": {
                     "@timestamp": "2026-09-08T14:00:00.000Z",
                     "message": "a stored line", "severity": "E"}}]}})

    with serving(built) as address:
        stream = http.client.HTTPConnection(address[0], address[1], timeout=10)
        stream.request("GET", "/stream")
        response = stream.getresponse()
        assert response.status == 200
        assert response.fp.readline().startswith(b"event: hello")
        response.fp.readline()
        response.fp.readline()

        status, _ = call(address, "POST", "/ingest",
                         [{"message": "the lane still ingests",
                           "severity": "E", "hostname": "epn146"}])
        assert status == 204

        seen = ""
        for _ in range(10):
            line = response.fp.readline().decode("utf-8")
            if "the lane still ingests" in line:
                seen = line
                break
        assert seen
        stream.close()

        status, raw = call(address, "POST", "/api/query",
                           {"criterias": {}, "options": {"limit": 10}})
        assert status == 200
        assert json.loads(raw)["rows"][0]["message"] == "a stored line"

        status, raw = call(address, "GET", "/api/templates/summary")
        assert status == 200
        payload = json.loads(raw)
        assert payload["coverage"]["status"] == contract.COVERAGE_UNAVAILABLE
        assert payload["totals"]["records_status"] == view.STATUS_UNAVAILABLE
        assert payload["stale"] is True
        assert payload["last_error"]

        status, raw = call(address, "POST", "/api/templates/list", {})
        assert status == 200
        assert json.loads(raw)["rows"] == []


def test_a_semantic_failure_leaves_the_list_and_the_query_lane_working(
        monkeypatch):
    built, _ = runtime(search=FailingSearch())
    built.cycle()
    monkeypatch.setattr(shifter, "OS_URL", "http://storage.invalid")
    monkeypatch.setattr(shifter, "opensearch_search", lambda body: {
        "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}})

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"query": "allocate", "mode": "semantic"})
        assert status == 200
        payload = json.loads(raw)
        assert [row["version_id"] for row in payload["rows"]] == [VERSION]
        assert payload["note"] == shifter.SEMANTIC_FALLBACK_NOTE
        status, raw = call(address, "POST", "/api/query",
                           {"criterias": {}, "options": {"limit": 10}})
        assert status == 200


def test_a_ready_semantic_search_ranks_the_rows_and_is_recorded():
    transport = farm([(TEMPLATE, 7), ("another line <NUM>", 4)])
    decisions = FakeTriageTransport()
    queries = triage.QueryLog(decisions, clock=lambda: NOW)
    search = ReadySearch([semantic.Hit(CANONICAL, (VERSION,), 0.87)])
    built, _ = runtime(transport, search=search, queries=queries)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"query": "allocation failed", "mode": "semantic"})
    payload = json.loads(raw)
    assert status == 200
    assert payload["semantic"]["status"] == semantic.STATUS_READY
    assert [row["version_id"] for row in payload["rows"]] == [VERSION]
    assert payload["rows"][0]["score"] == 0.87
    assert contract.decode_int(payload["rows"][0]["count"]) == 7
    assert payload["window_end"] == CUTOFF
    assert payload["coverage"] == {"status": contract.COVERAGE_COMPLETE,
                                   "behind": [], "idle": []}
    stored = decisions.documents[
        (triage.QUERIES_INDEX, payload["query_id"])]["_source"]
    assert stored["model_revision"] == "rev-1"
    assert stored["mode"] == "semantic"
    assert stored["result_ids"] == [VERSION]


def test_the_drawer_offers_neighbours_as_labelled_suggestions():
    other = "failed to allocate <NUM> bytes on device <NUM>"
    other_version = contract.version_id("dpl", other)
    other_canonical = contract.canonical_id(other)
    transport = farm([(TEMPLATE, 11), (other, 3)])
    search = ReadySearch(
        [], [semantic.Hit(other_canonical, (other_version,), 0.94)])
    built, _ = runtime(transport, search=search)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/detail",
                           {"version_id": VERSION})
    assert status == 200
    detail = json.loads(raw)
    block = detail["neighbours"]
    assert [item["version_id"] for item in block["suggestions"]] == [
        other_version]
    assert block["suggestions"][0]["score"] == 0.94
    assert "count" not in block["suggestions"][0]
    assert "suggestion" in block["note"]
    assert contract.decode_int(detail["version"]["count"]) == 11
    assert contract.decode_int(detail["canonical_group"]["count"]) == 11


def test_the_corpus_is_submitted_once_for_each_published_view():
    search = ReadySearch([])
    built, _ = runtime(search=search)
    for _ in range(4):
        built.cycle()
    assert len(search.submitted) == 1
    assert [group.canonical_id for group in search.submitted[0]] == [CANONICAL]


def test_the_corpus_uses_the_configured_activity_window():
    transport = fx.FakeTransport()
    fx.farm(transport, nodes=("epn146",))
    transport.put_definition(fx.definition("quiet for three days",
                                           last=CUTOFF - 3 * 86400000))
    search = ReadySearch([])
    built, _ = runtime(transport, search=search)
    built.cycle()
    assert [group.canonical_id for group in search.submitted[0]] == [
        contract.canonical_id("quiet for three days")]

    transport = fx.FakeTransport()
    fx.farm(transport, nodes=("epn146",))
    transport.put_definition(fx.definition("quiet for three days",
                                           last=CUTOFF - 3 * 86400000))
    search = ReadySearch([])
    built, _ = runtime(transport, search=search, active_ms=2 * 86400000)
    built.cycle()
    assert search.submitted == [[]]


def test_two_simultaneous_label_edits_collide_over_the_wire():
    built, _ = runtime()
    built.cycle()
    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/label", {
            "canonical_id": CANONICAL, "template": TEMPLATE, "family": "dpl",
            "reviewed_version_ids": [VERSION], "label": "known_bad",
            "author": "shift-a", "note": "seen on epn146"})
        assert status == 200
        first = json.loads(raw)["label"]
        status, raw = call(address, "POST", "/api/templates/label", {
            "canonical_id": CANONICAL, "template": TEMPLATE, "family": "dpl",
            "reviewed_version_ids": [VERSION], "label": "noisy",
            "author": "shift-a", "revision": first["revision"]})
        assert status == 200
        status, raw = call(address, "POST", "/api/templates/label", {
            "canonical_id": CANONICAL, "template": TEMPLATE, "family": "dpl",
            "reviewed_version_ids": [VERSION], "label": "known_good",
            "author": "shift-a", "revision": first["revision"]})
        assert status == 409
        assert "label" in json.loads(raw)


def test_a_count_above_the_32_bit_range_survives_a_real_request():
    built, _ = runtime(farm([(TEMPLATE, BIG)]))
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "GET", "/api/templates/summary")
        assert status == 200
        totals = json.loads(raw)["totals"]
        assert contract.decode_int(totals["records"]) == BIG

        status, raw = call(address, "POST", "/api/templates/list", {})
        assert status == 200
        row = json.loads(raw)["rows"][0]
        assert contract.decode_int(row["count"]) == BIG

        status, raw = call(address, "POST", "/api/templates/detail",
                           {"version_id": VERSION})
        assert status == 200
        detail = json.loads(raw)
        assert contract.decode_int(detail["version"]["count"]) == BIG


def test_one_refresh_task_serves_every_viewer():
    transport = farm()
    built, _ = runtime(transport)
    built.cycle()
    before = len(transport.bodies)

    with serving(built) as address:
        for _ in range(10):
            status, raw = call(address, "GET", "/api/templates/summary")
            assert status == 200
            status, raw = call(address, "POST", "/api/templates/list", {})
            assert status == 200
            assert json.loads(raw)["rows"][0]["version_id"] == VERSION
            status, raw = call(address, "POST", "/api/templates/detail",
                               {"version_id": VERSION})
            assert status == 200

    seen = [index for index, _, _ in transport.bodies[before:]]
    assert set(seen) == {view.INCIDENTS_INDEX}

    first = built.start()
    again = built.start()
    assert first is again
    assert [thread for thread in threading.enumerate()
            if thread.name == "templates-runtime"] == [first]
    built.stop()


def test_a_second_refresh_inside_the_interval_is_skipped():
    transport = farm()
    built, _ = runtime(transport)
    built.cycle()
    searches = transport.searches
    for _ in range(5):
        built.cycle()
    assert transport.searches == searches


def test_the_page_is_refused_plainly_when_the_feature_is_off():
    with serving(None) as address:
        status, raw = call(address, "GET", "/api/templates/summary")
    assert status == 503
    assert json.loads(raw)["error"] == shifter.TEMPLATES_DISABLED


def test_a_label_reaches_the_row_it_reviewed_and_leaves_the_count_alone():
    built, _ = runtime(farm([(TEMPLATE, 13)]))
    built.cycle()
    with serving(built) as address:
        status, _ = call(address, "POST", "/api/templates/label", {
            "canonical_id": CANONICAL, "template": TEMPLATE, "family": "dpl",
            "reviewed_version_ids": [VERSION], "label": "noisy",
            "reviewed_programs": ["o2-gpu"],
            "reviewed_origin_hosts": ["epn146"],
            "author": "shift-a", "watched": True})
        assert status == 200
        built._labels.refresh()
        status, raw = call(address, "POST", "/api/templates/list",
                           {"watched_only": True})
        rows = json.loads(raw)["rows"]
        assert [row["version_id"] for row in rows] == [VERSION]
        assert rows[0]["label"] == "noisy"
        assert rows[0]["watched"] is True
        assert contract.decode_int(rows[0]["count"]) == 13


def test_a_search_is_recorded_once_and_a_click_joins_it():
    decisions = FakeTriageTransport()
    queries = triage.QueryLog(decisions, clock=lambda: NOW)
    built, _ = runtime(queries=queries)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"query": "allocate"})
        assert status == 200
        query_id = json.loads(raw)["query_id"]
        assert query_id
        status, raw = call(address, "POST", "/api/templates/opened",
                           {"query_id": query_id, "version_id": VERSION,
                            "rank": 1})
        assert status == 204

    stored = decisions.documents[(triage.QUERIES_INDEX, query_id)]["_source"]
    assert stored["result_ids"] == [VERSION]
    assert stored["opened_ids"] == [VERSION]


def test_every_automatic_request_reads_a_fixed_index_only():
    transport = farm()
    built, _ = runtime(transport)
    built.cycle()
    with serving(built) as address:
        call(address, "GET", "/api/templates/summary")
        call(address, "POST", "/api/templates/list", {})
        call(address, "GET", "/api/templates/episodes")
    assert {index for index, _, _ in transport.bodies} == {
        view.CATALOG_INDEX, view.COARSE_BUCKETS, view.INCIDENTS_INDEX}


def test_the_inactive_history_is_an_explicit_option():
    transport = farm([(TEMPLATE, 2)])
    old = "an older message <NUM>"
    transport.put_definition(fx.definition(
        old, last=CUTOFF - 30 * 86400000, hosts=("epn228",)))
    built, _ = runtime(transport)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list", {})
        assert [row["version_id"] for row in json.loads(raw)["rows"]] == [
            VERSION]
        status, raw = call(address, "POST", "/api/templates/list",
                           {"include_inactive": True})
        rows = json.loads(raw)["rows"]
        assert rows[0]["version_id"] == VERSION
        assert rows[1]["version_id"] == contract.version_id("dpl", old)
        assert rows[1]["historical"] is True
        assert rows[1]["active"] is False
        assert rows[1]["origin_hosts"] == ["epn228"]
        assert rows[1]["count"] == 0


def test_watched_templates_and_the_inactive_history_are_not_mixed():
    built, _ = runtime()
    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"watched_only": True, "include_inactive": True})
    assert status == 400
    assert "cannot be listed together" in json.loads(raw)["error"]


def test_the_detail_drawer_carries_the_cover_relation_and_the_log_link():
    transport = fx.seeded()
    transport.put_definition(fx.definition(
        fx.W, widened_from=[fx.version(fx.A)], widened_into=[fx.version(fx.X)]))
    transport.incidents = [{
        "incident_id": "i-1", "group_id": "g-1", "alertname": "TemplateFlood",
        "entity_kind": "host", "entity_id": "epn146", "severity": "error",
        "state": "firing", "episode_start": CUTOFF,
        "entity_samples": ["epn146"], "signal_ids": ["s-1"]}]
    built, _ = runtime(transport)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/detail",
                           {"version_id": fx.version(fx.W)})
    payload = json.loads(raw)
    assert status == 200
    assert payload["labels"] == []
    assert payload["descendant_count"] == 1
    assert payload["descendants"] == [fx.version(fx.A)]
    assert payload["widened_from"] == [fx.version(fx.A)]
    assert payload["widened_into"] == [fx.version(fx.X)]
    assert payload["ancestors"] == [fx.version(fx.X)]
    assert payload["rematch_available"] is True
    assert payload["log_link"] == {
        "criterias": {"template_version": {"in": [fx.version(fx.W),
                                                  fx.version(fx.A)]}},
        "options": {"mode": "wildcard", "limit": 2000}}
    assert payload["episodes"][0]["incident_id"] == "i-1"
    for name in ("examples_available", "examples_reason", "superseded_by",
                 "supersedes", "relationship_verified"):
        assert name not in payload
        assert name not in payload["version"]


def test_the_lines_endpoint_routes_expands_and_rematches_over_the_wire():
    transport = fx.lines_farm()
    transport.put_definition(fx.definition(fx.W,
                                           widened_into=[fx.version(fx.X)]))
    built, _ = runtime(transport)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/lines",
                           {"version_id": fx.version(fx.W)})
        assert status == 200
        found = json.loads(raw)
        assert found["nodes"] == ["epn146", "epn228", "epn323"]
        assert found["every_node"] is False
        assert found["cutoff"] == CUTOFF
        assert [row["doc_id"] for row in found["rows"]] == ["r5", "r1",
                                                            "r2"]
        assert found["rows"][0]["template_version"] == fx.version(fx.A)

        status, raw = call(address, "POST", "/api/templates/lines",
                           {"version_id": fx.version(fx.W),
                            "include_ancestors": True, "every_node": True,
                            "limit": 3})
        assert status == 200
        found = json.loads(raw)
        assert found["every_node"] is True
        assert found["ancestors_included"] is True
        assert found["matched"] == 3
        assert found["fetched"] == 3
        assert found["truncated"] is True

        status, raw = call(address, "POST", "/api/templates/lines", {})
        assert status == 400
        status, raw = call(address, "POST", "/api/templates/examples",
                           {"version_id": fx.version(fx.W)})
        assert status == 404


def test_the_lines_default_page_is_the_configured_row_count(monkeypatch):
    monkeypatch.setattr(shifter, "TEMPLATE_LINES_ROWS", 2)
    transport = fx.lines_farm()
    built, _ = runtime(transport)
    built.cycle()
    found = built.lines({"version_id": fx.version(fx.A), "every_node": True})
    assert found["matched"] == 2
    found = built.lines({"version_id": fx.version(fx.A), "every_node": True,
                         "limit": 3})
    assert found["matched"] == 3


def test_an_unknown_version_is_refused_with_a_sentence():
    built, _ = runtime()
    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/detail",
                           {"version_id": "dpl:0123456789abcdef01234567"})
    assert status == 400
    assert "no retained definition" in json.loads(raw)["error"]


def test_the_ingest_path_and_the_static_page_are_untouched():
    built, _ = runtime()
    with serving(built) as address:
        status, _ = call(address, "POST", "/ingest",
                         [{"message": "ingest still answers",
                           "severity": "I"}])
        assert status == 204
        status, raw = call(address, "GET", "/healthz")
        assert status == 200
        health = json.loads(raw)
        assert health["ok"] is True
        assert health["templatesConfigured"] is True


def test_the_query_lane_filters_on_a_template_version_and_says_so():
    query = shifter.build_query(
        {"template_version": {"in": [VERSION, "dpl:other"]}}, "wildcard")
    assert query == {"bool": {"filter": [
        {"terms": {"template_version": [VERSION, "dpl:other"]}}]}}
    words = shifter.describe_query(
        {"template_version": {"in": [VERSION]}}, "wildcard", 10)
    assert f"template_version in ({VERSION})" in words
    assert shifter.build_query({"template_version": {"in": []}},
                               "wildcard") == {"match_all": {}}
    assert "template_version" in shifter.KEEP_FIELDS


def test_the_summary_carries_the_cutoff_the_coverage_and_the_watermarks():
    transport = fx.seeded()
    transport.put_watermark(fx.watermark("epn228",
                                         published_at=NOW - 2 * HOUR))
    built, _ = runtime(transport)
    built.cycle()
    with serving(built) as address:
        status, raw = call(address, "GET", "/api/templates/summary")
    payload = json.loads(raw)
    assert status == 200
    assert payload["window"]["window_end_iso"] == contract.iso_utc(CUTOFF)
    assert payload["window"]["window_days"] == 28
    assert payload["coverage"]["status"] == contract.COVERAGE_PARTIAL
    assert payload["coverage"]["idle"] == ["epn228"]
    assert payload["coverage"]["complete"] == ["epn146"]
    assert payload["coverage"]["gaps"][0]["reason"] == \
        contract.GAP_NODE_IDLE
    assert payload["watermarks"]["epn228"]["age_ms"] == 2 * HOUR
    assert payload["totals"]["records_status"] == view.STATUS_INCOMPLETE
    assert payload["semantic"]["status"] == semantic.STATUS_UNAVAILABLE
    assert "labels" in payload
    for name in ("snapshot", "backlog", "last_complete_snapshot",
                 "expired_snapshot"):
        assert name not in payload


def test_semantic_ranking_still_answers_the_inactive_history():
    transport = farm([(TEMPLATE, 7)])
    dormant = "allocation failed for an older message <NUM>"
    transport.put_definition(fx.definition(dormant,
                                           last=CUTOFF - 30 * 86400000))
    search = ReadySearch([semantic.Hit(CANONICAL, (VERSION,), 0.87)])
    built, _ = runtime(transport, search=search)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"query": "allocation failed", "mode": "semantic",
                            "include_inactive": True})
    payload = json.loads(raw)
    assert status == 200
    identifiers = [row["version_id"] for row in payload["rows"]]
    assert VERSION in identifiers
    assert contract.version_id("dpl", dormant) in identifiers
    assert payload["history"]["status"] == "ready"
    historical = [row for row in payload["rows"] if row["historical"]]
    assert [row["template"] for row in historical] == [dormant]


def test_a_failed_history_lane_is_stated_and_never_silent():
    class Broken(fx.FakeTransport):
        def search(self, index, body, ignore_unavailable=False):
            if index == view.CATALOG_INDEX and "match_phrase" in str(body):
                raise OSError("the catalog index did not answer")
            return fx.FakeTransport.search(self, index, body,
                                           ignore_unavailable)

    transport = Broken()
    fx.farm(transport, nodes=("epn146",))
    transport.put_definition(fx.definition(TEMPLATE))
    search = ReadySearch([semantic.Hit(CANONICAL, (VERSION,), 0.87)])
    built, _ = runtime(transport, search=search)
    built.cycle()

    with serving(built) as address:
        status, raw = call(address, "POST", "/api/templates/list",
                           {"query": "allocation failed", "mode": "semantic",
                            "include_inactive": True})
    payload = json.loads(raw)
    assert status == 200
    assert payload["history"]["status"] == "unavailable"
    assert "historical half" in payload["note"]
    assert [row["version_id"] for row in payload["rows"]] == [VERSION]


def test_the_service_memory_ceiling_is_read_the_way_systemd_writes_it():
    assert shifter.memory_bytes("384M") == 384 * 1024 * 1024
    assert shifter.memory_bytes("1G") == 1024 ** 3
    assert shifter.memory_bytes("2048K") == 2048 * 1024
    assert shifter.memory_bytes("1048576") == 1048576
    assert shifter.memory_bytes("infinity") == 0
    assert shifter.memory_bytes("") == 0


def test_the_templates_page_verifies_its_peak_against_the_unit_ceiling(
        monkeypatch):
    monkeypatch.setattr(shifter, "MEMORY_MAX", "384M")
    peak, refusal = shifter.memory_verdict(shifter.MEMORY_MAX)
    parts = shifter.serving_peak_bytes()
    assert refusal == ""
    assert peak == sum(parts.values())
    assert peak <= 384 * 1024 * 1024


def test_the_templates_page_refuses_to_start_above_the_unit_ceiling(
        monkeypatch):
    monkeypatch.setattr(shifter, "MEMORY_MAX", "64M")
    monkeypatch.setattr(shifter, "OS_URL", "http://storage.invalid")
    built, reason = shifter.build_templates()
    assert built is None
    assert "service ceiling" in reason


def test_the_templates_page_builds_against_the_bucket_indices(monkeypatch):
    monkeypatch.setattr(shifter, "MEMORY_MAX", "2G")
    monkeypatch.setattr(shifter, "OS_URL", "http://storage.invalid")
    built, reason = shifter.build_templates()
    assert reason == ""
    service = built._service
    assert service._coarse_pattern == view.COARSE_BUCKETS
    assert service._fine_pattern == view.FINE_BUCKETS
    assert service._shared_indices == ["infologger",
                                       "application-logs-central"]
    assert service.limits.refresh_interval_ms == \
        shifter.VIEW_REFRESH_SECONDS * 1000
    assert service.limits.lines_rows == shifter.TEMPLATE_LINES_CEILING


def test_the_old_regex_machinery_is_gone():
    for name in ("template_pattern", "template_matcher",
                 "line_matches_template", "template_phrases",
                 "example_phrase", "example_reason", "examples_available",
                 "example_criterias", "worker_local_only",
                 "WORKER_LOCAL_SEVERITIES", "MATCH_CACHE_MAX",
                 "SNAPSHOT_REFRESH_SECONDS", "SNAPSHOT_MAX_AGE_SECONDS",
                 "TEMPLATE_EXAMPLE_CANDIDATES"):
        assert not hasattr(shifter, name), name
    for name in ("METRICS_INDEX", "ROSTER_INDEX", "RosterRegistry",
                 "StaticRegistry", "MANIFEST_PAGE", "CHUNK_PAGE",
                 "SNAPSHOT_MAX_AGE_MS"):
        assert not hasattr(view, name), name
