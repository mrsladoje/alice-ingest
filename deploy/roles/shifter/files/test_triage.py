import copy
import io
import os
import sys
import urllib.error

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "deploy", "shared"))
sys.path.insert(0, HERE)

import template_contract as contract                          # noqa: E402
import triage                                                 # noqa: E402

NOW = 1788877200000
TEMPLATE = "failed to allocate <NUM> bytes"
CANONICAL = contract.canonical_id(TEMPLATE)
VERSION = contract.version_id("dpl", TEMPLATE)
WIDER = contract.version_id("dpl", "failed to allocate <NUM> <*>")


def http_error(code):
    return urllib.error.HTTPError("http://test", code, "conflict", {},
                                  io.BytesIO(b"{}"))


class FakeTransport:
    def __init__(self):
        self.documents = {}
        self.seq = 0
        self.puts = 0
        self.gets = 0
        self.searches = 0

    def get(self, index, doc_id):
        self.gets += 1
        stored = self.documents.get((index, doc_id))
        if stored is None:
            return None
        return copy.deepcopy(stored)

    def put(self, index, doc_id, document, seq_no=None, primary_term=None,
            create=False, refresh="wait_for"):
        self.puts += 1
        key = (index, doc_id)
        stored = self.documents.get(key)
        if create and stored is not None:
            raise http_error(409)
        if seq_no is not None:
            if stored is None or stored["_seq_no"] != seq_no:
                raise http_error(409)
        self.seq += 1
        self.documents[key] = {
            "found": True,
            "_id": doc_id,
            "_seq_no": self.seq,
            "_primary_term": 1,
            "_source": copy.deepcopy(document),
        }
        return {"result": "created" if stored is None else "updated"}

    def rows(self, index):
        return [stored["_source"] for key, stored in self.documents.items()
                if key[0] == index]

    def search(self, index, body):
        self.searches += 1
        rows = self.rows(index)
        for clause in (body.get("query") or {}).get("bool", {}).get(
                "filter", []):
            if "term" in clause:
                field, value = list(clause["term"].items())[0]
                rows = [row for row in rows if row.get(field) == value]
        aggs = body.get("aggs") or {}
        if aggs:
            field = aggs["groups"]["terms"]["field"]
            keys = {}
            for row in rows:
                keys[row.get(field)] = keys.get(row.get(field), 0) + 1
            buckets = [{"key": key, "doc_count": count}
                       for key, count in sorted(keys.items())]
            return {"aggregations": {"groups": {"buckets": buckets}}}
        rows.sort(key=lambda row: row.get("label_id") or row.get("query_id")
                  or "")
        after = body.get("search_after")
        if after is not None:
            rows = [row for row in rows if [row.get("label_id")] > list(after)]
        size = body.get("size", 10)
        rows = rows[:size]
        return {"hits": {"hits": [
            {"_source": copy.deepcopy(row), "sort": [row.get("label_id")]}
            for row in rows]}}


def store(transport=None, **kwargs):
    return triage.LabelStore(transport or FakeTransport(),
                             clock=lambda: NOW, **kwargs)


def request(**kwargs):
    payload = {
        "canonical_id": CANONICAL,
        "reviewed_version_ids": [VERSION],
        "family": "dpl",
        "template": TEMPLATE,
        "label": triage.LABEL_KNOWN_BAD,
        "note": "",
        "author": "marko",
        "watched": False,
        "reviewed_programs": ["o2-gpu-reconstruction"],
        "reviewed_origin_hosts": ["epn146"],
        "revision": None,
    }
    payload.update(kwargs)
    return payload


def row(version_id=VERSION, programs=("o2-gpu-reconstruction",),
        hosts=("epn146",), **kwargs):
    out = {
        "version_id": version_id,
        "canonical_id": CANONICAL,
        "programs": list(programs),
        "origin_hosts": list(hosts),
        "active": False,
        "count": 41,
        "last_observed": NOW,
    }
    out.update(kwargs)
    return out


def test_two_simultaneous_edits_collide_on_the_revision_check():
    transport = FakeTransport()
    labels = store(transport)
    first = labels.write(request())
    assert first["revision"] == 1
    second = labels.write(request(revision=1, label=triage.LABEL_NOISY))
    assert second["revision"] == 2

    with pytest.raises(triage.LabelConflict) as caught:
        labels.write(request(revision=1, label=triage.LABEL_KNOWN_GOOD))
    assert caught.value.label["revision"] == 2
    assert caught.value.label["label"] == triage.LABEL_NOISY


def test_a_stale_write_that_races_the_read_is_refused_by_the_sequence_check():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request())

    original = transport.put
    raced = {"done": False}

    def put(index, doc_id, document, **kwargs):
        if not raced["done"]:
            raced["done"] = True
            raise http_error(409)
        return original(index, doc_id, document, **kwargs)

    transport.put = put
    with pytest.raises(triage.LabelConflict) as caught:
        labels.write(request(revision=1))
    assert caught.value.label["revision"] == 1


def test_a_note_over_its_bound_is_refused_and_nothing_is_stored():
    transport = FakeTransport()
    labels = store(transport, note_max=2000)
    with pytest.raises(triage.TriageRefused) as caught:
        labels.write(request(note="x" * 2001))
    assert "2000" in str(caught.value)
    assert transport.puts == 0


def test_the_label_history_document_stays_within_its_revision_bound():
    transport = FakeTransport()
    labels = store(transport, history_max=50)
    document = labels.write(request())
    for revision in range(1, 60):
        document = labels.write(
            request(revision=revision, note=f"r{revision}"))
    assert document["revision"] == 60
    assert len(document["history"]) == 50
    assert document["history"][-1]["revision"] == 60


def test_a_label_document_over_its_byte_ceiling_is_refused():
    transport = FakeTransport()
    labels = store(transport, note_max=4000, document_max_bytes=1024)
    with pytest.raises(triage.TriageRefused) as caught:
        labels.write(request(note="x" * 3000))
    assert "1024" in str(caught.value)
    assert transport.puts == 0


def test_a_label_for_another_group_is_refused_on_creation():
    labels = store()
    with pytest.raises(triage.TriageRefused):
        labels.write(request(template="a different message <NUM>"))


def test_an_unknown_label_and_a_bad_author_are_refused():
    labels = store()
    with pytest.raises(triage.TriageRefused):
        labels.write(request(label="looks_fine"))
    with pytest.raises(triage.TriageRefused):
        labels.write(request(author="Marko Sladojevic"))


def test_two_authors_keep_two_documents_and_the_conflict_stays_visible():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request(author="marko", label=triage.LABEL_KNOWN_BAD))
    labels.write(request(author="lubos", label=triage.LABEL_KNOWN_GOOD))
    documents = labels.read(CANONICAL)
    assert len(documents) == 2
    verdict = triage.review(row(), documents)
    assert verdict["label_conflicts"] == 1
    assert sorted(verdict["reviewed_labels"]) == ["known_bad", "known_good"]


def test_an_exact_returning_version_recovers_its_reviewed_label():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request())
    verdict = triage.review(row(), labels.read(CANONICAL))
    assert verdict["label"] == triage.LABEL_KNOWN_BAD
    assert verdict["label_scope"] == triage.SCOPE_REVIEWED


def test_a_broader_version_requires_review():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request())
    verdict = triage.review(row(version_id=WIDER), labels.read(CANONICAL))
    assert verdict["label"] is None
    assert verdict["label_scope"] == triage.SCOPE_BROADER_VERSION


def test_a_new_source_scope_requires_review():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request())
    verdict = triage.review(row(hosts=("epn146", "epn228")),
                            labels.read(CANONICAL))
    assert verdict["label"] is None
    assert verdict["label_scope"] == triage.SCOPE_NEW_SOURCE


def test_the_watched_limit_is_enforced_and_explains_itself():
    transport = FakeTransport()
    labels = store(transport, watched_max=2)
    for index in range(2):
        template = f"another template number <NUM> {index}"
        labels.write(request(canonical_id=contract.canonical_id(template),
                             template=template,
                             reviewed_version_ids=[
                                 contract.version_id("dpl", template)],
                             watched=True))
    with pytest.raises(triage.TriageRefused) as caught:
        labels.write(request(watched=True))
    assert "2 templates are already watched" in str(caught.value)


def test_a_label_never_makes_a_template_active_or_counted():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request(watched=True))
    labels.refresh(NOW)
    subject = row()
    labels.decorate([subject])
    assert subject["label"] == triage.LABEL_KNOWN_BAD
    assert subject["watched"] is True
    assert subject["active"] is False
    assert subject["count"] == 41
    assert subject["last_observed"] == NOW


def test_the_label_cache_reports_its_own_byte_bound():
    transport = FakeTransport()
    labels = store(transport, cache_max_bytes=1024)
    for index in range(40):
        template = f"cache filling template <NUM> {index}"
        labels.write(request(canonical_id=contract.canonical_id(template),
                             template=template,
                             reviewed_version_ids=[
                                 contract.version_id("dpl", template)],
                             note="n" * 200))
    labels.refresh(NOW)
    status = labels.cache_status()
    assert status["truncated"] is True
    assert status["bytes"] <= 1024 + 4096


def test_the_watched_identifiers_come_from_the_cache_without_a_query():
    transport = FakeTransport()
    labels = store(transport)
    labels.write(request(watched=True))
    labels.refresh(NOW)
    before = transport.searches
    assert labels.watched_ids() == {CANONICAL}
    assert transport.searches == before


def test_the_query_log_keeps_identifiers_and_ranks_but_no_payload():
    transport = FakeTransport()
    queries = triage.QueryLog(transport, clock=lambda: NOW)
    query_id = queries.record("why did allocation fail", "semantic", False,
                              "rev-1", "corpus-1", [VERSION, WIDER],
                              viewer="wall", latency_ms=14)
    stored = transport.documents[(triage.QUERIES_INDEX, query_id)]["_source"]
    assert stored["result_ids"] == [VERSION, WIDER]
    assert stored["result_ranks"] == {VERSION: 1, WIDER: 2}
    assert stored["query_text"] == "why did allocation fail"
    assert stored["model_revision"] == "rev-1"
    assert stored["issued_at"] == NOW
    for field in ("template", "normalized", "count", "rows", "programs"):
        assert field not in stored


def test_a_click_is_implicit_feedback_and_not_a_graded_judgment():
    transport = FakeTransport()
    queries = triage.QueryLog(transport, clock=lambda: NOW)
    query_id = queries.record("allocation", "text", False, "unset", "unset",
                              [VERSION])
    queries.opened(query_id, VERSION, rank=1)
    queries.opened(query_id, VERSION, rank=1)
    stored = transport.documents[(triage.QUERIES_INDEX, query_id)]["_source"]
    assert stored["opened_ids"] == [VERSION]
    for field in stored:
        assert "relevance" not in field
        assert "judgment" not in field


def test_a_click_on_an_unknown_search_is_refused():
    queries = triage.QueryLog(FakeTransport(), clock=lambda: NOW)
    with pytest.raises(triage.TriageRefused):
        queries.opened("q-unknown", VERSION, rank=1)


def test_the_query_history_expiry_is_one_year():
    queries = triage.QueryLog(FakeTransport(), clock=lambda: NOW)
    body = queries.expiry_query()
    clause = body["query"]["bool"]["filter"][1]["range"]["issued_at"]["lt"]
    assert NOW - clause == 365 * 86400000


def test_the_writer_and_the_central_deleter_share_one_query_shape():
    transport = FakeTransport()
    queries = triage.QueryLog(transport, clock=lambda: NOW)
    cutoff = NOW - queries.retention_ms
    assert queries.expiry_query() == contract.expired_queries(cutoff)

    query_id = queries.record("why did it stop", "text", False, "rev-1",
                              "corpus-1", [VERSION])
    stored = transport.documents[(triage.QUERIES_INDEX, query_id)]["_source"]
    selector = contract.expired_queries(cutoff)["query"]["bool"]["filter"]
    assert stored["kind"] == selector[0]["term"]["kind"]
    assert "issued_at" in stored and "issued_at" in selector[1]["range"]
