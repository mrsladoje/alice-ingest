"""Hand-computed checks for every metric the harness reports.

The Stage S2 gate says a metric without a test does not get reported, so each
expected value below is derived by hand from the definition rather than from a
second call into the code under test.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench


def make_qrels(rows):
    return bench.Qrels({(qid, cid): g for qid, cid, g in rows})


def test_gains_are_the_frozen_ladder():
    assert bench.GAINS == {0: 0.0, 1: 1.0, 2: 3.0, 3: 7.0}
    assert bench.RELEVANT == {2, 3}


def test_ndcg_perfect_single_document():
    qrels = make_qrels([("q", "a", 3)])
    assert bench.ndcg_at_k(["a"], qrels, "q", 10) == 1.0


def test_ndcg_unjudged_at_rank_one_costs_the_log_of_three():
    """One unjudged result above the only relevant one leaves gain 7 at rank 2.

    DCG is 7/log2(3) and the ideal is 7/log2(2), so the ratio is exactly
    1/log2(3) and no other term survives.
    """
    qrels = make_qrels([("q", "a", 3)])
    got = bench.ndcg_at_k(["zz", "a"], qrels, "q", 10)
    assert abs(got - 1.0 / math.log2(3)) < 1e-12


def test_ndcg_mixed_pool_matches_the_written_arithmetic():
    qrels = make_qrels([("q", "d1", 3), ("q", "d2", 2), ("q", "d3", 1), ("q", "d4", 0)])
    ranked = ["d2", "zz", "d1", "d3"]
    expected_dcg = 3 / math.log2(2) + 0 / math.log2(3) + 7 / math.log2(4) + 1 / math.log2(5)
    expected_idcg = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    got = bench.ndcg_at_k(ranked, qrels, "q", 10)
    assert abs(got - expected_dcg / expected_idcg) < 1e-12
    assert abs(got - 0.7378720384) < 1e-9


def test_ndcg_is_none_when_the_pool_has_no_reachable_answer():
    qrels = make_qrels([("q", "d1", 1), ("q", "d2", 0)])
    assert bench.ndcg_at_k(["d1", "d2"], qrels, "q", 10) is None


def test_ndcg_cutoff_ignores_gain_below_rank_ten():
    qrels = make_qrels([("q", "a", 3)])
    ranked = ["z%d" % i for i in range(10)] + ["a"]
    assert bench.ndcg_at_k(ranked, qrels, "q", 10) == 0.0


def test_unjudged_is_not_irrelevant():
    qrels = make_qrels([("q", "a", 0)])
    assert qrels.grade("q", "a") == 0
    assert qrels.grade("q", "missing") is bench.UNJUDGED
    assert bench.judged_at_k(["a", "missing"], qrels, "q", 2) == 0.5


def test_mrr_takes_the_first_grade_two_or_three():
    qrels = make_qrels([("q", "a", 1), ("q", "b", 0), ("q", "c", 2)])
    assert bench.mrr_at_k(["a", "b", "c"], qrels, "q", 10) == 1.0 / 3.0
    assert bench.mrr_at_k(["c", "a"], qrels, "q", 10) == 1.0
    assert bench.mrr_at_k(["a", "b"], qrels, "q", 10) == 0.0


def test_mrr_respects_its_cutoff():
    qrels = make_qrels([("q", "a", 3)])
    assert bench.mrr_at_k(["z"] * 10 + ["a"], qrels, "q", 10) == 0.0


def test_pooled_recall_uses_the_pool_not_the_corpus():
    qrels = make_qrels([("q", "a", 3), ("q", "b", 2), ("q", "c", 2), ("q", "d", 3),
                        ("q", "e", 1)])
    assert bench.recall_at_k(["a", "c"], qrels, "q", 20) == 0.5
    assert bench.recall_at_k(["e"], qrels, "q", 20) == 0.0
    assert bench.recall_at_k(["a", "b", "c", "d"], qrels, "q", 20) == 1.0


def test_recall_is_none_when_nothing_relevant_was_pooled():
    qrels = make_qrels([("q", "a", 1)])
    assert bench.recall_at_k(["a"], qrels, "q", 20) is None


def test_success_at_five():
    qrels = make_qrels([("q", "a", 2)])
    assert bench.success_at_k(["z", "z2", "z3", "z4", "a"], qrels, "q", 5) == 1.0
    assert bench.success_at_k(["z", "z2", "z3", "z4", "z5", "a"], qrels, "q", 5) == 0.0


def test_judged_at_ten_and_twenty():
    rows = [("q", "d%d" % i, 1) for i in range(4)]
    qrels = make_qrels(rows)
    ranked = ["d0", "d1", "d2", "d3"] + ["u%d" % i for i in range(16)]
    assert bench.judged_at_k(ranked, qrels, "q", 10) == 0.4
    assert bench.judged_at_k(ranked, qrels, "q", 20) == 0.2


def test_identifier_metrics():
    qrels = make_qrels([("q", "a", 3), ("q", "b", 2)])
    assert bench.success_at_k(["a"], qrels, "q", 1) == 1.0
    assert bench.success_at_k(["z", "a"], qrels, "q", 1) == 0.0
    assert bench.recall_at_k(["a", "z", "b"], qrels, "q", 10) == 1.0
    assert bench.recall_at_k(["a"], qrels, "q", 10) == 0.5


def test_candidate_recall_is_measured_on_the_candidate_list():
    qrels = make_qrels([("q", "a", 3), ("q", "b", 3)])
    candidates = ["a"] + ["z%d" % i for i in range(60)]
    assert bench.candidate_recall_at_k(candidates, qrels, "q", 20) == 0.5
    candidates_deep = ["z%d" % i for i in range(30)] + ["a", "b"]
    assert bench.candidate_recall_at_k(candidates_deep, qrels, "q", 20) == 0.0
    assert bench.candidate_recall_at_k(candidates_deep, qrels, "q", 50) == 1.0


def test_ties_break_on_canonical_id_not_corpus_order():
    ids = ["b7", "a3", "c1"]
    scores = [0.5, 0.5, 0.5]
    assert bench.rank(scores, ids) == ["a3", "b7", "c1"]
    assert bench.rank([0.1, 0.9, 0.5], ids) == ["a3", "c1", "b7"]


def test_ranking_is_stable_across_repeated_calls():
    ids = ["d%d" % i for i in range(50)]
    scores = [(i * 7) % 5 for i in range(50)]
    first = bench.rank(scores, ids)
    assert all(bench.rank(scores, ids) == first for _ in range(5))


def test_canonical_collapse_gives_one_group_one_rank():
    mapping = {"i1": "c1", "i2": "c1", "i3": "c1", "i4": "c2"}
    ranked = ["i1", "i2", "i3", "i4"]
    assert bench.collapse(ranked, mapping) == ["c1", "c2"]


def test_collapse_keeps_documents_that_are_already_canonical():
    assert bench.collapse(["c1", "c2"], {}) == ["c1", "c2"]


def test_intent_averaging_makes_one_value_per_group():
    queries = {
        "q1": {"intent_group": "g1"},
        "q2": {"intent_group": "g1"},
        "q3": {"intent_group": "g2"},
    }
    per_query = {
        "q1": {"ndcg@10": 0.2},
        "q2": {"ndcg@10": 0.6},
        "q3": {"ndcg@10": 1.0},
    }
    grouped = bench.by_intent(per_query, queries)
    assert grouped["g1"]["ndcg@10"] == 0.4
    assert grouped["g2"]["ndcg@10"] == 1.0
    assert len(grouped) == 2


def test_intent_averaging_skips_none_without_dropping_the_group():
    queries = {"q1": {"intent_group": "g1"}, "q2": {"intent_group": "g1"}}
    per_query = {"q1": {"ndcg@10": None}, "q2": {"ndcg@10": 0.8}}
    assert bench.by_intent(per_query, queries)["g1"]["ndcg@10"] == 0.8


def test_bootstrap_on_a_constant_difference_returns_that_constant():
    a = {"g%d" % i: 0.5 for i in range(20)}
    b = {"g%d" % i: 0.3 for i in range(20)}
    result = bench.bootstrap_paired(a, b, replicates=200)
    assert result["n"] == 20
    assert abs(result["difference"] - 0.2) < 1e-12
    assert abs(result["low"] - 0.2) < 1e-12
    assert abs(result["high"] - 0.2) < 1e-12
    assert result["p"] == 0.0


def test_bootstrap_on_no_difference_cannot_reject():
    a = {"g%d" % i: 0.5 for i in range(20)}
    result = bench.bootstrap_paired(a, dict(a), replicates=200)
    assert result["difference"] == 0.0
    assert result["p"] == 1.0


def test_bootstrap_is_reproducible_under_its_seed():
    a = {"g%d" % i: (i % 7) / 7.0 for i in range(30)}
    b = {"g%d" % i: (i % 5) / 5.0 for i in range(30)}
    first = bench.bootstrap_paired(a, b, replicates=300, seed=11)
    second = bench.bootstrap_paired(a, b, replicates=300, seed=11)
    assert first == second


def test_half_width_is_half_the_interval():
    a = {"g%d" % i: (i % 3) / 3.0 for i in range(24)}
    b = {"g%d" % i: (i % 4) / 4.0 for i in range(24)}
    result = bench.bootstrap_paired(a, b, replicates=400, seed=5)
    assert abs(bench.half_width(a, b, replicates=400, seed=5)
               - (result["high"] - result["low"]) / 2.0) < 1e-15


def test_holm_multiplies_by_the_descending_rank_and_stays_monotone():
    adjusted = bench.holm({"x": 0.01, "y": 0.02, "z": 0.04})
    assert abs(adjusted["x"] - 0.03) < 1e-12
    assert abs(adjusted["y"] - 0.04) < 1e-12
    assert abs(adjusted["z"] - 0.04) < 1e-12


def test_holm_caps_at_one():
    assert bench.holm({"a": 0.6, "b": 0.7})["a"] == 1.0


def test_rrf_uses_the_upstream_constant_of_sixty():
    runs = {"s1": ["a", "b"], "s2": ["b", "a"]}
    fused = bench.rrf(runs)
    assert fused == ["a", "b"]
    expected = 1 / 61 + 1 / 62
    totals = {}
    for name, ranked in runs.items():
        for position, doc in enumerate(ranked):
            totals[doc] = totals.get(doc, 0.0) + 1 / (60 + position + 1)
    assert abs(totals["a"] - expected) < 1e-15
    assert abs(totals["b"] - expected) < 1e-15


def test_rrf_promotes_the_document_two_systems_agree_on():
    runs = {"s1": ["a", "b", "c"], "s2": ["c", "b", "a"], "s3": ["b", "a", "c"]}
    assert bench.rrf(runs)[0] == "b"


def test_minmax_and_zscore_and_quantile():
    assert bench.minmax([1.0, 3.0, 5.0]) == [0.0, 0.5, 1.0]
    assert bench.minmax([2.0, 2.0]) == [0.0, 0.0]
    assert bench.zscore([1.0, 1.0]) == [0.0, 0.0]
    z = bench.zscore([1.0, 2.0, 3.0])
    assert abs(z[0] + math.sqrt(1.5)) < 1e-12 and z[1] == 0.0
    assert bench.quantile_normalise([10.0, 30.0, 20.0]) == [0.0, 1.0, 0.5]


def test_score_fusion_normalises_each_system_before_adding():
    runs = {"lex": [("a", 100.0), ("b", 0.0)], "dense": [("a", 0.1), ("b", 0.9)]}
    assert set(bench.score_fusion(runs)) == {"a", "b"}
    runs_agree = {"lex": [("a", 100.0), ("b", 0.0)], "dense": [("a", 0.9), ("b", 0.1)]}
    assert bench.score_fusion(runs_agree)[0] == "a"


def test_tasks_do_not_share_a_metric_set():
    qrels = make_qrels([("q", "a", 3)])
    natural = bench.evaluate_query(["a"], qrels, "q", task="natural_language")
    identifier = bench.evaluate_query(["a"], qrels, "q", task="exact_identifier")
    assert "success@1" not in natural
    assert "success@1" in identifier and "recall@10" in identifier


def test_deep_subset_adds_the_deep_metrics_only_when_asked():
    qrels = make_qrels([("q", "a", 3)])
    shallow = bench.evaluate_query(["a"], qrels, "q")
    deep = bench.evaluate_query(["a"], qrels, "q", deep=True,
                                candidates=["a"] + ["z%d" % i for i in range(250)])
    assert "recall@100" not in shallow
    assert "recall@100" in deep and "candidate_recall@200" in deep


def test_manifest_lists_every_required_field():
    manifest = bench.make_manifest(run_id="r1", corpus_id="c", model_id="m")
    assert bench.manifest_is_complete(manifest) == []
    assert manifest["run_id"] == "r1"
    assert manifest["fusion_parameters"] == "not applicable"


def test_manifest_completeness_reports_what_is_missing():
    assert "corpus_id" in bench.manifest_is_complete({"run_id": "r1"})


def test_representation_arms_exclude_volatile_fields():
    doc = {"canonical_id": "c1", "template": "failed to allocate <NUM> bytes",
           "normalized": "failed to allocate <*> bytes",
           "families": {"dpl": 10}, "programs": {"gpu-reconstruction": 10},
           "severities": {"error": 10}, "severity_class": "error"}
    assert bench.represent(doc, "R0") == "failed to allocate <*> bytes"
    assert bench.represent(doc, "R1").startswith("source=dpl program=gpu-reconstruction")
    assert "severity=error" in bench.represent(doc, "R2")
    fields = bench.represent(doc, "R3")
    assert set(fields) == {"template", "program", "source", "detector", "severity",
                           "identifiers"}
    dual = bench.represent(doc, "R4")
    assert set(dual) == {"lexical", "semantic"}
    for arm in ("R0", "R1", "R2"):
        assert "<NUM>" not in bench.represent(doc, arm)


def test_identifier_tokens_survive_where_the_standard_analyzer_splits():
    tokens = bench.identifier_tokens(
        "TfBuilderTask failed at /var/log/o2.log code 0xDEADBEEF in "
        "o2::framework::DataProcessor with max_retry_count set")
    assert "TfBuilderTask" in tokens
    assert "0xDEADBEEF" in tokens
    assert "/var/log/o2.log" in tokens
    assert "max_retry_count" in tokens


def test_coverage_cases_name_the_query_no_system_can_win():
    qrels = make_qrels([("q1", "a", 3), ("q2", "b", 1)])
    cases = {c["query_id"] for c in bench.coverage_cases(qrels, {"q1": {}, "q2": {}, "q3": {}})}
    assert cases == {"q2", "q3"}


def test_judged_at_k_divides_by_the_cutoff_not_by_what_was_returned():
    """A short result list must not buy perfect coverage.

    One judged result in a ten-deep window is 0.1, not 1.0. The development
    gate reads this number, so a system that returns almost nothing must not
    clear it.
    """
    qrels = make_qrels([("q", "a", 3)])
    assert bench.judged_at_k(["a"], qrels, "q", 10) == 0.1
    assert bench.judged_at_k([], qrels, "q", 10) == 0.0
    assert bench.judged_at_k(["a"] + ["u%d" % i for i in range(9)], qrels, "q", 10) == 0.1


def test_deep_recall_at_100_is_hand_computed():
    """Four relevant in the pool, two of them inside the first hundred ranks."""
    qrels = make_qrels([("q", "a", 3), ("q", "b", 2), ("q", "c", 3), ("q", "d", 2)])
    ranked = ["z%d" % i for i in range(50)] + ["a"] + ["y%d" % i for i in range(48)] \
        + ["b"] + ["c", "d"]
    assert ranked.index("b") == 99
    assert bench.recall_at_k(ranked, qrels, "q", 100) == 0.5
    assert bench.recall_at_k(ranked, qrels, "q", 20) == 0.0
    assert bench.recall_at_k(ranked, qrels, "q", 102) == 1.0


def test_candidate_recall_at_100_and_200_are_hand_computed():
    """Three relevant: one inside 100, one between 101 and 200, one past 200."""
    qrels = make_qrels([("q", "a", 3), ("q", "b", 3), ("q", "c", 2)])
    candidates = (["z%d" % i for i in range(99)] + ["a"]
                  + ["y%d" % i for i in range(99)] + ["b"]
                  + ["x%d" % i for i in range(50)] + ["c"])
    assert candidates.index("a") == 99 and candidates.index("b") == 199
    assert abs(bench.candidate_recall_at_k(candidates, qrels, "q", 100) - 1.0 / 3.0) < 1e-12
    assert abs(bench.candidate_recall_at_k(candidates, qrels, "q", 200) - 2.0 / 3.0) < 1e-12
    assert bench.candidate_recall_at_k(candidates, qrels, "q", 300) == 1.0
