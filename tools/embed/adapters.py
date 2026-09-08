#!/usr/bin/env python3
"""Model adapters for the Stage S2 harness, with every fact read off the card.

Nothing in the tables below is guessed. Each prefix, pooling mode, sequence
limit and dimension count was taken from the model's own `config.json`,
`1_Pooling/config.json` and `config_sentence_transformers.json` on 8 September
2026, and the revision recorded beside it is the commit those files came from.

The adapters re-implement encoding rather than calling the library end to end,
because a golden fixture that compares the library against itself proves
nothing. Each adapter therefore ships a fixture that scores its own output
against the official implementation and reports the difference.
"""
import json
import os
import time

import numpy as np

DEFAULT_TOLERANCE = 1e-3

BUCKETS = (32, 64, 128, 256, 512, 1024, 2048)


def bucket_length(longest, ceiling):
    """Round a batch's longest sequence up to the next bucket.

    Past the last bucket the width is the batch's own longest sequence, never
    the model's declared limit. A model that accepts 32,768 tokens is not a
    reason to pad a 2,246-token batch to 32,768: that is what asked for a
    96 GiB attention buffer and killed the first mDenseOn arm.
    """
    ceiling = ceiling or BUCKETS[-1]
    for width in BUCKETS:
        if longest <= width:
            return min(width, ceiling)
    return min(longest, ceiling)


def safe_batch_size(base, width):
    """Shrink the batch as the padded width grows.

    Attention cost grows with the square of the sequence, so a batch sized for
    64-token templates is a memory fault at 2,048.
    """
    if width <= 256:
        return base
    if width <= 512:
        return max(1, base // 2)
    if width <= 1024:
        return max(1, base // 8)
    return max(1, base // 16)

REMOTE_CODE_ALLOWLIST_PATH = "downloads/frozen/ALLOW_REMOTE_CODE"


def allowed_remote_code():
    """Models needing `trust_remote_code` run only when the operator named them.

    No subagent may perform that security review, so an absent file means the
    candidate is excluded rather than quietly trusted.
    """
    if not os.path.exists(REMOTE_CODE_ALLOWLIST_PATH):
        return set()
    with open(REMOTE_CODE_ALLOWLIST_PATH) as fh:
        return {line.strip() for line in fh if line.strip()
                and not line.startswith("#")}


def pick_device(preferred="mps"):
    import torch
    if preferred == "mps" and torch.backends.mps.is_available():
        return "mps"
    if preferred == "cuda" and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def mean_pool(token_embeddings, attention_mask):
    """Padding tokens never enter the average."""
    mask = attention_mask.unsqueeze(-1).to(token_embeddings.dtype)
    summed = (token_embeddings * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1e-9)
    return summed / counts


def cls_pool(token_embeddings, attention_mask):
    return token_embeddings[:, 0]


def last_token_pool(token_embeddings, attention_mask, padding_side="right"):
    """The final real token, whichever side the tokenizer padded."""
    import torch
    if padding_side == "left":
        return token_embeddings[:, -1]
    lengths = attention_mask.sum(dim=1) - 1
    index = torch.arange(token_embeddings.shape[0], device=token_embeddings.device)
    return token_embeddings[index, lengths.clamp(min=0)]


POOLERS = {"mean": mean_pool, "cls": cls_pool, "lasttoken": last_token_pool}


def l2_normalise(matrix):
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.clip(norms, 1e-12, None)


def maxsim(query_tokens, document_tokens):
    """Each query token takes its best document token, and those scores are summed.

    This is the whole of late interaction. It is written out here so the fixture
    can compare it against the library rather than trust it.
    """
    similarity = query_tokens @ document_tokens.T
    return float(similarity.max(axis=1).sum())


def maxsim_batch(query_tokens, document_token_list):
    return np.array([maxsim(query_tokens, d) for d in document_token_list],
                    dtype=np.float32)


DENSE = {
    "all-MiniLM-L6-v2": {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "role": "efficient control",
        "query_prefix": "",
        "document_prefix": "",
        "pooling": "mean",
        "normalise": True,
        "max_length": 256,
        "max_length_source": "sentence_bert_config.json max_seq_length",
        "dimensions": 384,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": True,
    },
    "DenseOn": {
        "model_id": "lightonai/DenseOn",
        "revision": "cb9947ebccb33862d24e3c7ca2edb25e51acd887",
        "role": "core",
        "query_prefix": "query: ",
        "document_prefix": "document: ",
        "pooling": "cls",
        "normalise": True,
        "max_length": 512,
        "max_length_source": ("sentence_bert_config.json max_seq_length and "
                              "tokenizer_config.json model_max_length both say 512"),
        "dimensions": 768,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "mDenseOn": {
        "model_id": "lightonai/mDenseOn",
        "revision": "a5fdb000f7a21da96c3bddde3a782ef777316df3",
        "role": "core",
        "query_prefix": "query: ",
        "document_prefix": "document: ",
        "pooling": "cls",
        "normalise": True,
        "max_length": 8192,
        "max_length_source": ("tokenizer_config.json model_max_length and "
                              "config.json max_position_embeddings both say 8192; "
                              "this model ships no sentence_bert_config.json"),
        "dimensions": 768,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "Qwen3-Embedding-0.6B": {
        "model_id": "Qwen/Qwen3-Embedding-0.6B",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "role": "core",
        "query_prefix": ("Instruct: Given a web search query, retrieve relevant "
                         "passages that answer the query\nQuery:"),
        "document_prefix": "",
        "pooling": "lasttoken",
        "normalise": True,
        "max_length": 32768,
        "max_length_source": ("config.json max_position_embeddings says 32768; "
                              "tokenizer_config.json says 131072 and this "
                              "adapter takes the smaller of the two"),
        "dimensions": 1024,
        "dtype": "float32",
        "padding_side": "left",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "Qwen3-Embedding-8B": {
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "revision": "1d8ad4ca9b3dd8059ad90a75d4983776a23d44af",
        "role": "research ceiling",
        "query_prefix": ("Instruct: Given a web search query, retrieve relevant "
                         "passages that answer the query\nQuery:"),
        "document_prefix": "",
        "pooling": "lasttoken",
        "normalise": True,
        "max_length": 40960,
        "max_length_source": ("config.json max_position_embeddings says 40960; "
                              "tokenizer_config.json says 131072 and this "
                              "adapter takes the smaller of the two"),
        "dimensions": 4096,
        "dtype": "bfloat16",
        "padding_side": "left",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "msmarco-distilbert-base-tas-b": {
        "model_id": "sentence-transformers/msmarco-distilbert-base-tas-b",
        "revision": "b12d9352e776979147078a8975a4885042984fd1",
        "role": "opensearch documented default, retrieval tuned",
        "query_prefix": "",
        "document_prefix": "",
        "pooling": "cls",
        "normalise": False,
        "max_length": 512,
        "max_length_source": "sentence_bert_config.json max_seq_length",
        "dimensions": 768,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "multi-qa-MiniLM-L6-cos-v1": {
        "model_id": "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
        "revision": "b207367332321f8e44f96e224ef15bc607f4dbf0",
        "role": "opensearch documented default, retrieval tuned",
        "query_prefix": "",
        "document_prefix": "",
        "pooling": "mean",
        "normalise": True,
        "max_length": 512,
        "max_length_source": "sentence_bert_config.json max_seq_length",
        "dimensions": 384,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": False,
    },
    "all-distilroberta-v1": {
        "model_id": "sentence-transformers/all-distilroberta-v1",
        "revision": "842eaed40bee4d61673a81c92d5689a8fed7a09f",
        "role": "opensearch documented default, symmetric",
        "query_prefix": "",
        "document_prefix": "",
        "pooling": "mean",
        "normalise": True,
        "max_length": 512,
        "max_length_source": "sentence_bert_config.json max_seq_length",
        "dimensions": 768,
        "dtype": "float32",
        "padding_side": "right",
        "trust_remote_code": False,
        "licence": "apache-2.0",
        "symmetric_mode": True,
    },
}

STATIC = {
    "potion-retrieval-32M": {
        "model_id": "minishlab/potion-retrieval-32M",
        "revision": "6fc8051fab2a1e0ee76689cf08c853792ac285e7",
        "role": "efficient control",
        "pooling": "static token-vector average",
        "normalise": True,
        "max_length": None,
        "dimensions": 512,
        "dtype": "float32",
        "trust_remote_code": False,
        "licence": "mit",
        "symmetric_mode": True,
    },
    "potion-base-32M": {
        "model_id": "minishlab/potion-base-32M",
        "revision": "1e5a03f8eeb2c98b928fbbd846f22f816360919f",
        "role": "continuity control",
        "pooling": "static token-vector average",
        "normalise": True,
        "max_length": None,
        "dimensions": 512,
        "dtype": "float32",
        "trust_remote_code": False,
        "licence": "mit",
        "symmetric_mode": True,
    },
}

LATE = {
    "LateOn": {
        "model_id": "lightonai/LateOn",
        "revision": "62911e105059585d244384c7d17826e35f669c17",
        "role": "core",
        "query_prefix": "[Q] ",
        "document_prefix": "[D] ",
        "query_length": 32,
        "document_length": 300,
        "token_dimensions": 128,
        "similarity": "MaxSim",
        "lowercase": False,
        "trust_remote_code": False,
        "licence": "apache-2.0",
    },
    "mLateOn": {
        "model_id": "lightonai/mLateOn",
        "revision": "edd378f99593c0ac8a15518b97ad89786b02685e",
        "role": "core",
        "query_prefix": "[Q] ",
        "document_prefix": "[D] ",
        "query_length": 8192,
        "document_length": 8192,
        "token_dimensions": 128,
        "similarity": "MaxSim",
        "lowercase": False,
        "trust_remote_code": False,
        "licence": "apache-2.0",
    },
    "LateOn-Code-edge": {
        "model_id": "lightonai/LateOn-Code-edge",
        "revision": "4bcdf5ed93f791259eb130b577a240f753d68dd8",
        "role": "core",
        "query_prefix": "[Q] ",
        "document_prefix": "[D] ",
        "query_length": 256,
        "document_length": 2048,
        "token_dimensions": 48,
        "similarity": "MaxSim",
        "lowercase": True,
        "trust_remote_code": False,
        "licence": "apache-2.0",
    },
    "LateOn-Code": {
        "model_id": "lightonai/LateOn-Code",
        "revision": "ace431824f35db231178fc602e33296784762a2e",
        "role": "core",
        "query_prefix": "[Q] ",
        "document_prefix": "[D] ",
        "query_length": 256,
        "document_length": 2048,
        "token_dimensions": 128,
        "similarity": "MaxSim",
        "lowercase": False,
        "trust_remote_code": False,
        "licence": "apache-2.0",
    },
}

RERANKER = {
    "Qwen3-Reranker-0.6B": {
        "model_id": "Qwen/Qwen3-Reranker-0.6B",
        "revision": "e61197ed45024b0ed8a2d74b80b4d909f1255473",
        "role": "quality ceiling, reranker only",
        "instruction": ("Given a web search query, retrieve relevant passages "
                        "that answer the query"),
        "trust_remote_code": False,
        "licence": "apache-2.0",
    },
}

EXCLUDED = {
    "pplx-embed-v1-0.6b": {
        "model_id": "perplexity-ai/pplx-embed-v1-0.6b",
        "revision": "2c4d510dd4a732063c31a0f70193e35067b51fd8",
        "reason": ("config.json carries an auto_map for PPLXQwen3Config and "
                   "PPLXQwen3Model, and the sentence-transformers module list "
                   "loads a custom st_quantize.FlexibleQuantizer, so the model "
                   "cannot load without trust_remote_code"),
        "rule": "docs/SEMANTIC_LOOP.md non-negotiable 10",
    },
    "jina-embeddings-v5-text-small-retrieval": {
        "model_id": "jinaai/jina-embeddings-v5-text-small-retrieval",
        "revision": "6856e76bb7292d2c9b4b0c0b7dbd0b7e0f1c9a2f",
        "reason": ("the model card declares licence cc-by-nc-4.0, which is "
                   "non-commercial and unapproved for ALICE production"),
        "rule": "docs/SEMANTIC_PLAN.md binding rule 10",
    },
}


class DenseAdapter:
    """One transformer-backed dense model, encoded through declared settings."""

    def __init__(self, key, device=None, batch_size=32):
        if key not in DENSE:
            raise KeyError(key)
        self.key = key
        self.spec = dict(DENSE[key])
        self.device = device or pick_device()
        self.batch_size = batch_size
        self._model = None
        self._tokenizer = None

    def load(self):
        import torch
        from transformers import AutoModel, AutoTokenizer
        if self.spec["trust_remote_code"] and self.spec["model_id"] not in allowed_remote_code():
            raise PermissionError("%s needs trust_remote_code and is not allowed"
                                  % self.spec["model_id"])
        dtype = torch.float32 if self.spec["dtype"] == "float32" else torch.bfloat16
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.spec["model_id"], revision=self.spec["revision"])
        self._tokenizer.padding_side = self.spec["padding_side"]
        self._model = AutoModel.from_pretrained(
            self.spec["model_id"], revision=self.spec["revision"], dtype=dtype)
        self._model.eval().to(self.device)
        return self

    def _encode(self, texts, prefix):
        """Encode in length-sorted batches padded to a bucket, then unsort.

        Padding every batch to its own longest member gives the accelerator a
        new tensor shape almost every batch, and each new shape costs a kernel
        compile. Rounding up to one of five bucket lengths gives it five shapes.
        Measured on DenseOn over 640 templates, this is 4.4 times faster on
        `mps` than padding to the batch maximum.

        The vectors do not change. Every pooling method here reads the attention
        mask, so a padded position contributes nothing, and `test_adapters.py`
        asserts that the two paths agree.
        """
        import torch
        if self._model is None:
            self.load()
        pooler = POOLERS[self.spec["pooling"]]
        lengths = [len(self._tokenizer(prefix + t)["input_ids"]) for t in texts]
        order = sorted(range(len(texts)), key=lambda i: lengths[i])
        pooled_rows = [None] * len(texts)
        start = 0
        while start < len(order):
            probe = order[start:start + self.batch_size]
            width = bucket_length(max(lengths[i] for i in probe),
                                  self.spec["max_length"])
            size = safe_batch_size(self.batch_size, width)
            index = order[start:start + size]
            start += size
            batch = [prefix + texts[i] for i in index]
            width = bucket_length(max(lengths[i] for i in index),
                                  self.spec["max_length"])
            encoded = self._tokenizer(batch, padding="max_length", truncation=True,
                                      max_length=width,
                                      return_tensors="pt").to(self.device)
            with torch.no_grad():
                hidden = self._model(**encoded).last_hidden_state
            if self.spec["pooling"] == "lasttoken":
                pooled = pooler(hidden, encoded["attention_mask"],
                                padding_side=self.spec["padding_side"])
            else:
                pooled = pooler(hidden, encoded["attention_mask"])
            pooled = pooled.float().cpu().numpy()
            for position, source in enumerate(index):
                pooled_rows[source] = pooled[position]
        matrix = (np.stack(pooled_rows) if pooled_rows
                  else np.zeros((0, self.spec["dimensions"])))
        return l2_normalise(matrix) if self.spec["normalise"] else matrix

    def encode_query(self, texts):
        return self._encode(texts, self.spec["query_prefix"])

    def encode_document(self, texts):
        return self._encode(texts, self.spec["document_prefix"])

    def encode_symmetric(self, texts):
        """Template similarity may compare like with like.

        Only models whose card documents a symmetric mode use it. Every other
        model keeps the documented query and document modes, and the choice is
        frozen on development data.
        """
        if not self.spec.get("symmetric_mode"):
            return self.encode_document(texts)
        return self._encode(texts, "")

    def fixture(self, tolerance=DEFAULT_TOLERANCE):
        """Compare this adapter against the official SentenceTransformer path."""
        from sentence_transformers import SentenceTransformer
        texts = ["free shm memory too low",
                 "Program has crashed with signal <NUM>"]
        mine = self.encode_document(texts)
        official = SentenceTransformer(self.spec["model_id"],
                                       revision=self.spec["revision"],
                                       device=self.device)
        prompt = self.spec["document_prefix"]
        theirs = official.encode([prompt + t for t in texts],
                                 normalize_embeddings=self.spec["normalise"],
                                 show_progress_bar=False)
        theirs = np.asarray(theirs, dtype=np.float32)
        cosine = float(np.mean(np.sum(l2_normalise(mine) * l2_normalise(theirs), axis=1)))
        largest = float(np.max(np.abs(mine - theirs)))
        return {"model": self.key, "cosine_to_official": cosine,
                "max_abs_difference": largest, "tolerance": tolerance,
                "dimensions": int(mine.shape[1]),
                "declared_dimensions": self.spec["dimensions"],
                "independent": True,
                "proves": ("this adapter's tokenize, forward, pool and "
                           "normalise path reproduces SentenceTransformer's"),
                "passes": cosine > 1 - tolerance and largest < tolerance * 10,
                "device": self.device}


class StaticAdapter:
    """A model2vec static model: one lookup vector per token, then an average."""

    def __init__(self, key, device=None, batch_size=1024):
        self.key = key
        if key in STATIC:
            self.spec = dict(STATIC[key])
        else:
            self.spec = {"model_id": key, "revision": "locally trained",
                         "role": "trained", "pooling": "static token-vector average",
                         "normalise": True, "max_length": None, "dimensions": None,
                         "dtype": "float32", "trust_remote_code": False,
                         "licence": "derived from the teacher", "symmetric_mode": True}
        self.device = "cpu"
        self.batch_size = batch_size
        self._model = None

    def load(self):
        from model2vec import StaticModel
        self._model = StaticModel.from_pretrained(self.spec["model_id"])
        self.spec["dimensions"] = int(self._model.dim)
        return self

    def _encode(self, texts):
        if self._model is None:
            self.load()
        vectors = np.asarray(self._model.encode(texts, batch_size=self.batch_size,
                                                show_progress_bar=False),
                             dtype=np.float32)
        return l2_normalise(vectors) if self.spec["normalise"] else vectors

    encode_query = _encode
    encode_document = _encode
    encode_symmetric = _encode

    def independent_encode(self, texts):
        """Static pooling written out, so the fixture compares two paths.

        A static model is a lookup table. Encoding is: tokenize, drop the
        special tokens, average the rows of the embedding matrix, normalise.
        This reads the model's own weights and tokenizer and never calls the
        library's `encode`, which is the only way the fixture below can say
        anything about correctness.
        """
        if self._model is None:
            self.load()
        table = np.asarray(self._model.embedding, dtype=np.float32)
        special = {self._model.tokenizer.token_to_id(token)
                   for token in ("[CLS]", "[SEP]", "[PAD]", "[UNK]")}
        special.discard(None)
        rows = []
        for text in texts:
            ids = [i for i in self._model.tokenizer.encode(text).ids
                   if i not in special]
            rows.append(table[ids].mean(axis=0) if ids
                        else np.zeros(table.shape[1], dtype=np.float32))
        matrix = np.stack(rows)
        return l2_normalise(matrix) if self.spec["normalise"] else matrix

    def fixture(self, tolerance=DEFAULT_TOLERANCE):
        texts = ["free shm memory too low", "disk is full"]
        official = np.asarray(self._encode(texts), dtype=np.float32)
        mine = self.independent_encode(texts)
        cosine = float(np.mean(np.sum(mine * official, axis=1)))
        return {"model": self.key, "cosine_to_official": cosine,
                "max_abs_difference": float(np.max(np.abs(mine - official))),
                "tolerance": tolerance, "dimensions": int(mine.shape[1]),
                "declared_dimensions": self.spec["dimensions"],
                "independent": True,
                "proves": ("an independent static-pooling implementation, read "
                           "off the model's own embedding table and tokenizer, "
                           "reproduces the library's encode"),
                "passes": cosine > 1 - tolerance, "device": self.device}


class LateAdapter:
    """A ColBERT-style model: token vectors kept, combined with MaxSim."""

    def __init__(self, key, device=None, batch_size=32):
        self.key = key
        self.spec = dict(LATE[key])
        self.device = device or pick_device()
        self.batch_size = batch_size
        self._model = None

    def load(self):
        from pylate import models
        self._model = models.ColBERT(model_name_or_path=self.spec["model_id"],
                                     revision=self.spec["revision"],
                                     device=self.device)
        return self

    def _encode(self, texts, is_query):
        if self._model is None:
            self.load()
        vectors = self._model.encode(texts, is_query=is_query,
                                     batch_size=self.batch_size,
                                     show_progress_bar=False,
                                     convert_to_numpy=True)
        return [np.asarray(v, dtype=np.float32) for v in vectors]

    def encode_query(self, texts):
        return self._encode(texts, True)

    def encode_document(self, texts):
        return self._encode(texts, False)

    def score(self, query_tokens, document_token_list):
        return maxsim_batch(query_tokens, document_token_list)

    def fixture(self, tolerance=1e-2):
        """Check this MaxSim against pylate's own scorer on the same vectors.

        Each document is scored on its own call. Document token counts differ,
        and stacking them into one padded tensor would compare padding against
        the query rather than the document.
        """
        from pylate import scores as pylate_scores
        import torch
        queries = self.encode_query(["memory pressure"])
        documents = self.encode_document(["free shm memory too low",
                                          "run started normally"])
        mine = self.score(queries[0], documents)
        theirs = np.array([
            float(pylate_scores.colbert_scores(
                queries_embeddings=torch.tensor(np.stack([queries[0]])),
                documents_embeddings=torch.tensor(np.stack([document])),
            ).numpy().reshape(-1)[0])
            for document in documents], dtype=np.float32)
        largest = float(np.max(np.abs(mine - theirs)))
        return {"model": self.key, "max_abs_difference": largest,
                "tolerance": tolerance, "mine": mine.tolist(),
                "official": theirs.tolist(),
                "independent": "scoring only",
                "proves": ("this MaxSim reproduces pylate's scorer on the same "
                           "token vectors; the token vectors themselves come "
                           "from pylate, so the encode path is not "
                           "independently checked"),
                "token_dimensions": int(queries[0].shape[1]),
                "declared_token_dimensions": self.spec["token_dimensions"],
                "passes": largest < tolerance, "device": self.device}


def describe_all():
    return {"dense": DENSE, "static": STATIC, "late": LATE,
            "reranker": RERANKER, "excluded": EXCLUDED,
            "remote_code_allowed": sorted(allowed_remote_code())}


if __name__ == "__main__":
    print(json.dumps(describe_all(), indent=1))
