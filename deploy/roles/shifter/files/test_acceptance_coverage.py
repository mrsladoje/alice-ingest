import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "deploy", "shared"))
sys.path.insert(0, os.path.join(ROOT, "tools", "templating"))
sys.path.insert(0, HERE)

import template_contract as contract                          # noqa: E402
import templates_view as view                                 # noqa: E402
import semantic                                               # noqa: E402

import test_templates_view as fx                              # noqa: E402

CUTOFF = fx.CUTOFF
NOW = fx.NOW
HOUR = fx.HOUR
FINE = fx.FINE
WINDOW_START = fx.WINDOW_START

ACTIVE = "queue full for detector alpha"
SLEEPING = "queue full for detector beta"


def versions(*texts):
    return [fx.version(text) for text in texts]


def farm_of(nodes, counts_by_node):
    transport = fx.FakeTransport()
    for node in nodes:
        transport.put_watermark(fx.watermark(node))
    for text in (ACTIVE, SLEEPING):
        transport.put_definition(fx.definition(text))
    for node, per_hour in counts_by_node.items():
        for hours_back, counts in per_hour.items():
            transport.put_bucket(fx.bucket(node, "dpl",
                                           CUTOFF - hours_back * HOUR,
                                           counts))
    return transport


def test_the_window_total_is_conserved_from_the_bucket_documents():
    active, sleeping = versions(ACTIVE, SLEEPING)
    transport = farm_of(("epn146", "epn228"), {
        "epn146": {1: {active: 5, sleeping: 1}, 2: {active: 4}},
        "epn228": {1: {active: 3}, 27 * 24: {sleeping: 2}},
    })
    served = fx.service(transport)
    built = served.refresh(NOW)
    published = 0
    for (_, _), document in transport.buckets.items():
        assert contract.conserved(document)
        published += contract.decode_int(document["total"])
    assert built.total_records == published == 15
    assert contract.decode_int(built.by_version[active]["count"]) == 12
    assert contract.decode_int(built.by_version[sleeping]["count"]) == 3
    assert built.coverage["status"] == contract.COVERAGE_COMPLETE
    assert built.coverage["complete"] == ["epn146", "epn228"]


def test_a_bucket_that_does_not_conserve_is_caught_by_the_contract():
    active = fx.version(ACTIVE)
    document = fx.bucket("epn146", "dpl", CUTOFF - HOUR, {active: 5})
    broken = copy.deepcopy(document)
    broken["total"] = 6
    assert contract.conserved(document)
    assert not contract.conserved(broken)
    with pytest.raises(contract.ContractError) as refused:
        contract.validate_bucket(broken)
    assert "publishes a total of 6 and its counts sum to 5" in \
        str(refused.value)


def test_the_cutoff_trails_the_slowest_live_node_and_names_the_idle_one():
    active = fx.version(ACTIVE)
    transport = farm_of((), {
        "epn146": {1: {active: 5}},
        "epn228": {1: {active: 3}},
        "epn323": {1: {active: 2}},
    })
    transport.put_watermark(fx.watermark("epn146", CUTOFF + 9 * FINE))
    transport.put_watermark(fx.watermark("epn228", CUTOFF + 2 * FINE))
    transport.put_watermark(fx.watermark("epn323", CUTOFF + 12 * FINE,
                                         published_at=NOW - 2 * HOUR))
    built = fx.service(transport).refresh(NOW)
    assert built.cutoff == CUTOFF
    assert built.coverage["status"] == contract.COVERAGE_PARTIAL
    assert built.coverage["idle"] == ["epn323"]
    assert built.coverage["behind"] == []
    assert built.coverage["complete"] == ["epn146", "epn228"]
    assert contract.decode_int(built.by_version[active]["count"]) == 10
    assert built.by_version[active]["count_status"] == view.STATUS_INCOMPLETE
    assert built.status == view.STATUS_INCOMPLETE
    gap = built.coverage["gaps"][0]
    assert gap["reason"] == contract.GAP_NODE_IDLE
    assert gap["node"] == "epn323"
    assert gap["reason"] in contract.GAP_REASONS


def test_the_cutoff_moves_forward_only_when_every_live_node_has_passed_it():
    active = fx.version(ACTIVE)
    transport = farm_of((), {"epn146": {1: {active: 5}},
                             "epn228": {1: {active: 3}}})
    transport.put_watermark(fx.watermark("epn146", CUTOFF + HOUR + FINE))
    transport.put_watermark(fx.watermark("epn228", CUTOFF + 11 * FINE))
    served = fx.service(transport)
    assert served.refresh(NOW).cutoff == CUTOFF

    transport.put_watermark(fx.watermark("epn228", CUTOFF + HOUR + FINE,
                                         published_at=NOW + FINE))
    transport.put_bucket(fx.bucket("epn228", "dpl", CUTOFF, {active: 8}))
    later = NOW + served.limits.refresh_interval_ms
    built = served.refresh(later)
    assert built.cutoff == CUTOFF + HOUR
    assert contract.decode_int(built.by_version[active]["count"]) == 16


def test_a_stale_figure_is_never_shown_as_current():
    active = fx.version(ACTIVE)
    transport = farm_of(("epn146",), {"epn146": {1: {active: 5}}})
    clock = {"now": NOW}
    served = fx.service(transport, clock=lambda: clock["now"])
    served.refresh(NOW)
    fresh = served.summary(NOW)
    assert fresh["stale"] is False
    assert fresh["totals"]["records"] == 5

    clock["now"] = CUTOFF + served.limits.stale_after_ms + 1
    aged = served.summary(clock["now"])
    assert aged["stale"] is True
    assert aged["totals"]["records"] == 5
    assert aged["window"]["window_end"] == CUTOFF

    clock["now"] = CUTOFF + served.limits.expire_after_ms + 1
    expired = served.summary(clock["now"])
    assert expired["totals"]["records"] == 0
    assert expired["totals"]["records_status"] == view.STATUS_UNKNOWN
    assert expired["expired_window"]["window_end"] == CUTOFF
    assert served.list_rows({}, clock["now"])["rows"] == []


def test_a_refresh_that_reads_nothing_new_keeps_the_last_figure_and_its_age():
    active = fx.version(ACTIVE)
    transport = farm_of(("epn146",), {"epn146": {1: {active: 5}}})
    served = fx.service(transport)
    served.refresh(NOW)
    transport.fail = OSError("the storage tier did not answer")
    with pytest.raises(OSError):
        served.refresh(NOW + served.limits.refresh_interval_ms)
    summary = served.summary(NOW + served.limits.refresh_interval_ms)
    assert summary["totals"]["records"] == 5
    assert summary["last_error"] == "OSError('the storage tier did not answer')"
    assert summary["stale"] is False
    assert served.summary(NOW + 3 * served.limits.refresh_interval_ms)[
        "stale"] is True


def test_a_narrower_template_is_covered_wholesale_and_a_wider_one_is_not():
    transport = fx.lines_farm()
    served = fx.service(transport)
    built = served.refresh(NOW)
    narrow, wide, wider = versions(fx.A, fx.W, fx.X)
    assert built.descendants(wide) == [narrow]
    assert wider not in built.descendants(wide)
    assert sorted(built.descendants(wider)) == sorted([narrow, wide])

    found = served.lines({"version_id": wide, "every_node": True}, NOW)
    stamps = {row["template_version"] for row in found["rows"]}
    assert stamps == {narrow, wide}
    assert wider not in stamps

    transport.put_definition(fx.definition(fx.W, widened_into=[wider]))
    served.refresh(NOW + served.limits.refresh_interval_ms)
    found = served.lines({"version_id": wide, "every_node": True,
                          "include_ancestors": True}, NOW)
    kept = [row for row in found["rows"] if row["template_version"] == wider]
    assert [row["message"] for row in kept] == ["sent 42 bytes to 10.0.0.9"]
    assert found["fetched"] == len(found["rows"]) + 1


def test_the_semantic_corpus_is_the_active_rows_at_the_cutoff():
    active, sleeping = versions(ACTIVE, SLEEPING)
    transport = farm_of(("epn146",), {"epn146": {1: {active: 5}}})
    transport.put_definition(fx.definition(SLEEPING,
                                           last=WINDOW_START - HOUR))
    built = fx.service(transport).refresh(NOW)
    groups = semantic.active_groups(built.rows, built.cutoff,
                                    contract.ACTIVE_MS)
    assert [group.version_ids for group in groups] == [(active,)]
    assert sleeping not in built.by_version
