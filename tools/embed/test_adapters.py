"""Hand-computed checks for pooling, padding, prompts and MaxSim.

These run without a network. The golden fixtures that compare each adapter
against the official implementation need the model weights and run separately,
under `--fixtures`, because a download is not a unit test.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters

torch = pytest.importorskip("torch")


def tensor(values):
    return torch.tensor(values, dtype=torch.float32)


def test_mean_pooling_ignores_padding():
    hidden = tensor([[[1.0, 2.0], [3.0, 4.0], [99.0, 99.0]]])
    mask = torch.tensor([[1, 1, 0]])
    got = adapters.mean_pool(hidden, mask).numpy()
    assert np.allclose(got, [[2.0, 3.0]])


def test_mean_pooling_of_one_real_token_is_that_token():
    hidden = tensor([[[5.0, 7.0], [99.0, 99.0]]])
    mask = torch.tensor([[1, 0]])
    assert np.allclose(adapters.mean_pool(hidden, mask).numpy(), [[5.0, 7.0]])


def test_cls_pooling_takes_position_zero():
    hidden = tensor([[[1.0, 2.0], [3.0, 4.0]]])
    mask = torch.tensor([[1, 1]])
    assert np.allclose(adapters.cls_pool(hidden, mask).numpy(), [[1.0, 2.0]])


def test_last_token_pooling_with_right_padding_skips_the_pad():
    hidden = tensor([[[1.0, 2.0], [3.0, 4.0], [99.0, 99.0]]])
    mask = torch.tensor([[1, 1, 0]])
    got = adapters.last_token_pool(hidden, mask, padding_side="right").numpy()
    assert np.allclose(got, [[3.0, 4.0]])


def test_last_token_pooling_with_left_padding_takes_the_final_position():
    hidden = tensor([[[99.0, 99.0], [1.0, 2.0], [3.0, 4.0]]])
    mask = torch.tensor([[0, 1, 1]])
    got = adapters.last_token_pool(hidden, mask, padding_side="left").numpy()
    assert np.allclose(got, [[3.0, 4.0]])


def test_left_and_right_padding_agree_on_the_same_sequence():
    right = adapters.last_token_pool(
        tensor([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]]),
        torch.tensor([[1, 1, 0]]), padding_side="right").numpy()
    left = adapters.last_token_pool(
        tensor([[[0.0, 0.0], [1.0, 2.0], [3.0, 4.0]]]),
        torch.tensor([[0, 1, 1]]), padding_side="left").numpy()
    assert np.allclose(right, left)


def test_every_declared_pooling_mode_has_an_implementation():
    declared = {spec["pooling"] for spec in adapters.DENSE.values()}
    assert declared <= set(adapters.POOLERS)


def test_l2_normalisation_gives_unit_rows():
    matrix = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = adapters.l2_normalise(matrix)
    assert np.allclose(out[0], [0.6, 0.8])
    assert np.allclose(out[1], [0.0, 0.0])


def test_maxsim_sums_each_query_token_best_match():
    query = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    document = np.array([[1.0, 0.0], [0.0, 0.5]], dtype=np.float32)
    assert abs(adapters.maxsim(query, document) - 1.5) < 1e-12


def test_maxsim_is_not_symmetric_in_its_arguments():
    query = np.array([[1.0, 0.0]], dtype=np.float32)
    document = np.array([[1.0, 0.0], [0.9, 0.0]], dtype=np.float32)
    assert abs(adapters.maxsim(query, document) - 1.0) < 1e-12
    assert abs(adapters.maxsim(document, query) - 1.9) < 1e-6


def test_maxsim_batch_keeps_document_order():
    query = np.array([[1.0, 0.0]], dtype=np.float32)
    documents = [np.array([[0.2, 0.0]], dtype=np.float32),
                 np.array([[0.9, 0.0]], dtype=np.float32)]
    assert np.allclose(adapters.maxsim_batch(query, documents), [0.2, 0.9])


def test_dense_prompts_are_the_documented_ones():
    assert adapters.DENSE["DenseOn"]["query_prefix"] == "query: "
    assert adapters.DENSE["DenseOn"]["document_prefix"] == "document: "
    assert adapters.DENSE["all-MiniLM-L6-v2"]["query_prefix"] == ""
    assert adapters.DENSE["Qwen3-Embedding-0.6B"]["document_prefix"] == ""
    assert adapters.DENSE["Qwen3-Embedding-0.6B"]["query_prefix"].startswith("Instruct:")


def test_late_prompts_and_limits_are_the_documented_ones():
    assert adapters.LATE["LateOn"]["query_prefix"] == "[Q] "
    assert adapters.LATE["LateOn"]["document_prefix"] == "[D] "
    assert adapters.LATE["LateOn"]["query_length"] == 32
    assert adapters.LATE["LateOn"]["document_length"] == 300
    assert adapters.LATE["LateOn-Code-edge"]["token_dimensions"] == 48
    assert adapters.LATE["LateOn"]["token_dimensions"] == 128


def test_every_adapter_declares_the_full_field_set():
    required = {"model_id", "revision", "query_prefix", "document_prefix",
                "pooling", "normalise", "max_length", "dimensions", "dtype",
                "padding_side", "trust_remote_code", "licence"}
    for key, spec in adapters.DENSE.items():
        assert required <= set(spec), key
    late_required = {"model_id", "revision", "query_prefix", "document_prefix",
                     "query_length", "document_length", "token_dimensions",
                     "similarity", "trust_remote_code", "licence"}
    for key, spec in adapters.LATE.items():
        assert late_required <= set(spec), key


def test_no_promoted_adapter_needs_remote_code():
    for group in (adapters.DENSE, adapters.STATIC, adapters.LATE, adapters.RERANKER):
        for key, spec in group.items():
            assert spec["trust_remote_code"] is False, key


def test_the_remote_code_model_is_excluded_and_says_why():
    excluded = adapters.EXCLUDED["pplx-embed-v1-0.6b"]
    assert "trust_remote_code" in excluded["reason"]
    assert "pplx-embed-v1-0.6b" not in adapters.DENSE


def test_the_non_commercial_model_is_excluded_and_says_why():
    excluded = adapters.EXCLUDED["jina-embeddings-v5-text-small-retrieval"]
    assert "cc-by-nc-4.0" in excluded["reason"]


def test_remote_code_allowlist_is_empty_without_the_operator_file(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "REMOTE_CODE_ALLOWLIST_PATH",
                        str(tmp_path / "absent"))
    assert adapters.allowed_remote_code() == set()
    path = tmp_path / "present"
    path.write_text("# operator note\nsome/model\n")
    monkeypatch.setattr(adapters, "REMOTE_CODE_ALLOWLIST_PATH", str(path))
    assert adapters.allowed_remote_code() == {"some/model"}


def test_bucket_length_rounds_up_and_respects_the_declared_ceiling():
    assert adapters.bucket_length(1, 512) == 32
    assert adapters.bucket_length(32, 512) == 32
    assert adapters.bucket_length(33, 512) == 64
    assert adapters.bucket_length(200, 512) == 256
    assert adapters.bucket_length(900, 512) == 512
    assert adapters.bucket_length(200, 128) == 128
    assert adapters.bucket_length(10, 256) == 32


def test_every_dense_adapter_says_where_its_max_length_came_from():
    for key, spec in adapters.DENSE.items():
        assert spec.get("max_length_source"), key


def test_max_lengths_match_the_card_not_a_sibling_model():
    assert adapters.DENSE["DenseOn"]["max_length"] == 512
    assert adapters.DENSE["mDenseOn"]["max_length"] == 8192
    assert adapters.DENSE["all-MiniLM-L6-v2"]["max_length"] == 256


def test_bucket_length_never_pads_to_the_model_ceiling():
    """Past the last bucket the width is the batch's longest sequence.

    Returning the ceiling here asked for a 96 GiB attention buffer on a model
    that accepts 8,192 tokens while the batch's longest sequence was 2,246.
    """
    assert adapters.bucket_length(2246, 8192) == 2246
    assert adapters.bucket_length(2246, 32768) == 2246
    assert adapters.bucket_length(40000, 32768) == 32768
    assert adapters.bucket_length(3000, 2048) == 2048


def test_safe_batch_size_shrinks_as_the_window_grows():
    assert adapters.safe_batch_size(32, 128) == 32
    assert adapters.safe_batch_size(32, 512) == 16
    assert adapters.safe_batch_size(32, 1024) == 4
    assert adapters.safe_batch_size(32, 2048) == 2
    assert adapters.safe_batch_size(1, 2048) == 1
