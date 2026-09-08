#!/usr/bin/env python3
"""Retrieval harness for docs/SEMANTIC_PLAN.md stages S2 to S11.

Three tasks are scored here and never averaged together: natural-language
operator retrieval, template similarity, and exact identifier lookup. The
headline number is natural language alone, because that is the question an
operator actually asks.

Two rules in this file exist to stop a metric flattering a system. Unjudged is
not irrelevant: an unjudged result earns no gain but is counted in Judged@k, so
a system that retrieves outside the pool is visible rather than silently
penalised. And canonical duplicate groups collapse before ranks are assigned, so
one template cannot occupy five of the top ten.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

GAINS = {0: 0.0, 1: 1.0, 2: 3.0, 3: 7.0}
RELEVANT = {2, 3}
UNJUDGED = None

VOLATILE_FIELDS = [
    "timestamp", "hostname", "process identifier", "run number",
    "internet protocol address", "raw numeric values", "random path segments",
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Corpus:
    """The frozen canonical template groups, in the order the file holds them.

    Ranking order must not depend on dictionary iteration, so document order is
    fixed here once and every system reuses it.
    """

    def __init__(self, docs, duplicates=None):
        self.docs = docs
        self.ids = [d["canonical_id"] for d in docs]
        self.by_id = {d["canonical_id"]: d for d in docs}
        self.position = {cid: i for i, cid in enumerate(self.ids)}
        self.duplicates = duplicates or {}
        self.canonical_of_instance = {}
        for canonical, instances in self.duplicates.items():
            for inst in instances:
                self.canonical_of_instance[inst] = canonical

    def __len__(self):
        return len(self.docs)

    @classmethod
    def load(cls, corpus_path, duplicates_path=None):
        docs = []
        with open(corpus_path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    docs.append(json.loads(line))
        duplicates = None
        if duplicates_path and os.path.exists(duplicates_path):
            duplicates = json.load(open(duplicates_path)).get("groups")
        return cls(docs, duplicates)

    def severity_coverage(self):
        """How many documents carry a severity that is not the placeholder.

        Stage S3 requires this beside every R2 result, because InfoLogger
        severity is absent for every line in this corpus and an R2 arm that
        silently prepends `severity=absent` is not testing what it claims.
        """
        known = 0
        for doc in self.docs:
            classes = set(doc.get("severities") or {})
            classes.discard("absent")
            classes.discard("unknown")
            if classes:
                known += 1
        return {"documents": len(self.docs), "with_severity": known,
                "share": known / len(self.docs) if self.docs else 0.0}


def _identity(doc):
    families = sorted(doc.get("families") or {}, key=lambda f: -doc["families"][f])
    programs = sorted(doc.get("programs") or {}, key=lambda p: -doc["programs"][p])
    family = families[0] if families else "unknown"
    program = programs[0] if programs else "unknown"
    return family, program


def _detector(doc):
    program = _identity(doc)[1]
    for token in re.split(r"[/_-]", program):
        if token in {"tpc", "its", "mft", "trd", "tof", "fv0", "ft0", "fdd",
                     "emc", "phs", "cpv", "hmp", "mch", "mid", "zdc", "ctp"}:
            return token
    return "unknown"


def represent(doc, arm):
    """Turn one canonical group into the text or fields a system searches.

    R3 returns a mapping because BM25F searches fields, not a string. Every
    other arm returns one string. Volatile fields never enter any arm.
    """
    template = doc.get("template") or ""
    normalized = doc.get("normalized") or template
    family, program = _identity(doc)
    severity = doc.get("severity_class") or "absent"
    detector = _detector(doc)
    if arm == "R0":
        return normalized
    if arm == "R1":
        return "source=%s program=%s\n%s" % (family, program, normalized)
    if arm == "R2":
        return "source=%s program=%s severity=%s detector=%s\n%s" % (
            family, program, severity, detector, normalized)
    if arm == "R3":
        return {
            "template": normalized,
            "program": program,
            "source": family,
            "detector": detector,
            "severity": severity,
            "identifiers": " ".join(identifier_tokens(template)),
        }
    if arm == "R5":
        return "source=%s\n%s" % (family, normalized)
    if arm == "R6":
        return "program=%s\n%s" % (program, normalized)
    if arm == "R7":
        example = (doc.get("examples") or [""])[0][:300]
        return "source=%s program=%s\n%s\nexample: %s" % (
            family, program, normalized, example)
    if arm == "R8":
        stripped = re.sub(r"<[^>]*>", " ", normalized)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return "source=%s program=%s\n%s" % (family, program, stripped)
    if arm == "R9":
        lines = doc.get("lines", 0)
        band = ("very common" if lines > 1_000_000 else
                "common" if lines > 10_000 else
                "occasional" if lines > 100 else "rare")
        return "source=%s program=%s frequency=%s\n%s" % (
            family, program, band, normalized)
    if arm == "R4":
        return {
            "lexical": represent(doc, "R3"),
            "semantic": represent(doc, "R2"),
        }
    raise ValueError("unknown representation arm %r" % arm)


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[A-Z][a-z0-9]*)+|"
                           r"[A-Za-z0-9_]*_[A-Za-z0-9_]+|"
                           r"0[xX][0-9a-fA-F]+|"
                           r"[A-Za-z][A-Za-z0-9]*::[A-Za-z0-9_:]+|"
                           r"/[A-Za-z0-9_./-]+")


def identifier_tokens(text):
    """Tokens an operator would paste verbatim: camelCase, snake_case, hex,
    C++ scopes and paths. The standard analyzer splits all of these, which is
    exactly what the Stage S4 analyzer screen is asked to measure."""
    seen, out = set(), []
    for match in IDENTIFIER_RE.finditer(text or ""):
        token = match.group(0)
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def rank(scores, doc_ids):
    """Descending score, ties broken on the canonical identifier.

    A tie broken on position would make the ranking depend on corpus file
    order, and two systems that score identically would then disagree for no
    reason a reader could check.
    """
    order = sorted(range(len(doc_ids)), key=lambda i: (-scores[i], doc_ids[i]))
    return [doc_ids[i] for i in order]


def collapse(ranked_ids, canonical_of_instance):
    """Keep the first appearance of each canonical group and drop the rest."""
    seen, out = set(), []
    for doc_id in ranked_ids:
        canonical = canonical_of_instance.get(doc_id, doc_id)
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return out


class Qrels:
    """Graded judgements, with unjudged kept distinct from grade zero."""

    def __init__(self, grades):
        self.grades = grades
        self.by_query = defaultdict(dict)
        for (qid, cid), grade in grades.items():
            self.by_query[qid][cid] = grade

    def grade(self, qid, cid):
        return self.by_query.get(qid, {}).get(cid, UNJUDGED)

    def relevant(self, qid):
        return {cid for cid, g in self.by_query.get(qid, {}).items() if g in RELEVANT}

    def judged_count(self, qid):
        return len(self.by_query.get(qid, {}))

    @classmethod
    def load(cls, path, states=("graded",)):
        grades = {}
        with open(path, "r") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            index = {name: i for i, name in enumerate(header)}
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < len(header):
                    continue
                state = parts[index["state"]] if "state" in index else "graded"
                if states and state not in states:
                    continue
                grade = int(parts[index["grade"]])
                if grade < 0:
                    continue
                grades[(parts[index["query_id"]], parts[index["canonical_id"]])] = grade
        return cls(grades)


def dcg(gains):
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked, qrels, qid, k=10):
    """Normalised discounted cumulative gain, gains 0, 1, 3, 7 for grades 0 to 3.

    The ideal ranking is built from the judged pool for this query. A query with
    no grade 2 or 3 in its pool has no reachable answer and returns None, so it
    leaves the effectiveness average instead of scoring every system zero.
    """
    judged = qrels.by_query.get(qid, {})
    if not any(g in RELEVANT for g in judged.values()):
        return None
    ideal = sorted((GAINS[g] for g in judged.values()), reverse=True)[:k]
    got = []
    for cid in ranked[:k]:
        grade = qrels.grade(qid, cid)
        got.append(0.0 if grade is UNJUDGED else GAINS[grade])
    denominator = dcg(ideal)
    return dcg(got) / denominator if denominator else None


def mrr_at_k(ranked, qrels, qid, k=10):
    for i, cid in enumerate(ranked[:k]):
        if qrels.grade(qid, cid) in RELEVANT:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(ranked, qrels, qid, k):
    """Recall against the frozen pool's known relevant set, never the corpus."""
    known = qrels.relevant(qid)
    if not known:
        return None
    hit = sum(1 for cid in ranked[:k] if cid in known)
    return hit / len(known)


def success_at_k(ranked, qrels, qid, k):
    return 1.0 if any(qrels.grade(qid, cid) in RELEVANT for cid in ranked[:k]) else 0.0


def judged_at_k(ranked, qrels, qid, k):
    """Judged results in the top k, divided by k, never by what was returned.

    Dividing by the returned window would let a system that returns one judged
    result score perfect coverage, which is the number the 95 percent
    development gate reads. A slot a system never filled is not a judged slot.
    """
    judged = sum(1 for cid in ranked[:k] if qrels.grade(qid, cid) is not UNJUDGED)
    return judged / float(k)


def candidate_recall_at_k(candidates, qrels, qid, k):
    """What the reranker was given, not what it produced.

    A reranker is never blamed for a relevant template that the candidate
    generator never retrieved, so this number goes beside every reranked score.
    """
    return recall_at_k(candidates, qrels, qid, k)


def evaluate_query(ranked, qrels, qid, task="natural_language", candidates=None,
                   deep=False):
    out = {
        "ndcg@10": ndcg_at_k(ranked, qrels, qid, 10),
        "mrr@10": mrr_at_k(ranked, qrels, qid, 10),
        "recall@20": recall_at_k(ranked, qrels, qid, 20),
        "success@5": success_at_k(ranked, qrels, qid, 5),
        "judged@10": judged_at_k(ranked, qrels, qid, 10),
        "judged@20": judged_at_k(ranked, qrels, qid, 20),
    }
    if task == "exact_identifier":
        out["success@1"] = success_at_k(ranked, qrels, qid, 1)
        out["recall@10"] = recall_at_k(ranked, qrels, qid, 10)
    if deep:
        out["recall@100"] = recall_at_k(ranked, qrels, qid, 100)
    if candidates is not None:
        out["candidate_recall@20"] = candidate_recall_at_k(candidates, qrels, qid, 20)
        out["candidate_recall@50"] = candidate_recall_at_k(candidates, qrels, qid, 50)
        if deep:
            out["candidate_recall@100"] = candidate_recall_at_k(candidates, qrels, qid, 100)
            out["candidate_recall@200"] = candidate_recall_at_k(candidates, qrels, qid, 200)
    return out


def by_intent(per_query, queries):
    """One value per intent group, paraphrases averaged first.

    The bootstrap resamples intents, so two paraphrases of one question must not
    count as two independent observations.
    """
    groups = defaultdict(list)
    for qid, metrics in per_query.items():
        group = queries[qid]["intent_group"]
        groups[group].append(metrics)
    out = {}
    for group, rows in groups.items():
        merged = {}
        for name in rows[0]:
            values = [r[name] for r in rows if r.get(name) is not None]
            merged[name] = sum(values) / len(values) if values else None
        out[group] = merged
    return out


def bootstrap_paired(a, b, replicates=1000, seed=20260908, alpha=0.05):
    """Paired bootstrap over intent groups, the unit the plan resamples.

    Returns the observed mean difference, its percentile interval and a
    two-sided p-value read off the same replicates.
    """
    keys = sorted(set(a) & set(b))
    diffs = [a[k] - b[k] for k in keys if a[k] is not None and b[k] is not None]
    n = len(diffs)
    if n == 0:
        return {"n": 0, "difference": None, "low": None, "high": None, "p": None}
    observed = sum(diffs) / n
    rng = random.Random(seed)
    means = []
    for _ in range(replicates):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    low = means[int(math.floor((alpha / 2) * replicates))]
    high = means[min(replicates - 1, int(math.ceil((1 - alpha / 2) * replicates)) - 1)]
    below = sum(1 for m in means if m <= 0.0) / replicates
    above = sum(1 for m in means if m >= 0.0) / replicates
    p = min(1.0, 2 * min(below, above))
    return {"n": n, "difference": observed, "low": low, "high": high, "p": p,
            "replicates": replicates, "seed": seed}


def holm(pvalues):
    """Holm step-down correction, monotone as the method requires."""
    items = sorted(pvalues.items(), key=lambda kv: (kv[1] is None, kv[1]))
    m = sum(1 for _, p in items if p is not None)
    out, running = {}, 0.0
    rank_index = 0
    for name, p in items:
        if p is None:
            out[name] = None
            continue
        adjusted = min(1.0, (m - rank_index) * p)
        running = max(running, adjusted)
        out[name] = running
        rank_index += 1
    return out


def half_width(a, b, replicates=1000, seed=20260908):
    result = bootstrap_paired(a, b, replicates=replicates, seed=seed)
    if result["difference"] is None:
        return None
    return (result["high"] - result["low"]) / 2.0


def coverage_cases(qrels, queries):
    """Queries whose pool holds no grade 2 or 3 cannot be won by any system."""
    out = []
    for qid in queries:
        judged = qrels.by_query.get(qid, {})
        if not judged:
            out.append({"query_id": qid, "reason": "no judged pool"})
        elif not any(g in RELEVANT for g in judged.values()):
            out.append({"query_id": qid, "reason": "pool holds no grade 2 or 3"})
    return out


def load_queries(path, task=None, split=None):
    out = {}
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if task and row.get("task") != task:
                continue
            if split and row.get("split") != split:
                continue
            out[row["query_id"]] = row
    return out


def git_revision():
    try:
        rev = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"],
                                        stderr=subprocess.DEVNULL).decode().strip()
        return rev, bool(dirty)
    except Exception:
        return "unknown", True


def dependency_lock_hash(python_bin=None):
    python_bin = python_bin or sys.executable
    try:
        frozen = subprocess.check_output([python_bin, "-m", "pip", "freeze"],
                                         stderr=subprocess.DEVNULL).decode()
        return sha256_text(frozen)
    except Exception:
        return "unknown"


REQUIRED_MANIFEST_FIELDS = [
    "run_id", "corpus_id", "query_set_id", "judgment_set_id", "code_revision",
    "dependency_lock_hash", "model_id", "model_revision", "adapter",
    "representation", "search_parameters", "fusion_parameters",
    "opensearch_version", "index_uuid", "index_schema_revision",
    "searched_indices", "primary_shards", "replicas", "candidate_depth",
    "refresh_state", "search_pipeline_revision", "hardware", "device",
    "warm_state", "seed", "started", "finished",
]


def make_manifest(**fields):
    """Every field the plan lists, present or explicitly not applicable.

    A number without a manifest is not a result, so a missing key fails here
    rather than reaching the results file unnoticed.
    """
    manifest = {name: fields.get(name, "not applicable")
                for name in REQUIRED_MANIFEST_FIELDS}
    manifest["hardware"] = fields.get("hardware") or "%s %s" % (
        platform.machine(), platform.platform())
    for name, value in fields.items():
        if name not in manifest:
            manifest[name] = value
    return manifest


def manifest_is_complete(manifest):
    return [name for name in REQUIRED_MANIFEST_FIELDS if name not in manifest]


def write_run(path, run_id, system, task, manifest, results, depth=200):
    payload = {
        "run_id": run_id,
        "system": system,
        "task": task,
        "manifest": manifest,
        "ranked_depth": depth,
        "corpus_fully_ranked": True,
        "results": {qid: [[cid, float(score)] for cid, score in rows[:depth]]
                    for qid, rows in results.items()},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return path


def read_run(path):
    return json.load(open(path))


def score_run(run, qrels, queries, task="natural_language", deep=False,
              candidates=None):
    per_query = {}
    for qid, rows in run["results"].items():
        if qid not in queries:
            continue
        ranked = [cid for cid, _ in rows]
        cand = candidates.get(qid) if candidates else None
        per_query[qid] = evaluate_query(ranked, qrels, qid, task=task,
                                        candidates=cand, deep=deep)
    intents = by_intent(per_query, queries)
    summary = {}
    for name in next(iter(intents.values())) if intents else {}:
        values = [row[name] for row in intents.values() if row.get(name) is not None]
        summary[name] = sum(values) / len(values) if values else None
    return {"per_query": per_query, "per_intent": intents, "summary": summary,
            "intents_scored": len(intents)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    parser.add_argument("--severity-coverage", action="store_true")
    args = parser.parse_args()
    corpus = Corpus.load(args.corpus, args.duplicates)
    if args.severity_coverage:
        print(json.dumps(corpus.severity_coverage(), indent=2))
        return
    print(json.dumps({"documents": len(corpus),
                      "duplicate_groups": len(corpus.duplicates),
                      "instances": len(corpus.canonical_of_instance)}, indent=2))


if __name__ == "__main__":
    main()


def rrf(runs, constant=60, weights=None):
    """Reciprocal Rank Fusion with the upstream rank constant of 60.

    The constant stays at the published value here because this function is the
    fixed control that a tuned fusion has to beat. A tuned constant belongs in a
    separate system identifier, never in the control.
    """
    weights = weights or {name: 1.0 for name in runs}
    totals = defaultdict(float)
    for name, ranked in runs.items():
        weight = weights.get(name, 1.0)
        for position, doc_id in enumerate(ranked):
            totals[doc_id] += weight / (constant + position + 1)
    ids = sorted(totals)
    return rank([totals[i] for i in ids], ids)


def minmax(scores):
    low, high = min(scores), max(scores)
    if high == low:
        return [0.0 for _ in scores]
    return [(s - low) / (high - low) for s in scores]


def zscore(scores):
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    sd = math.sqrt(variance)
    if sd == 0:
        return [0.0 for _ in scores]
    return [(s - mean) / sd for s in scores]


def quantile_normalise(scores):
    """Rank-to-quantile mapping, the shape-free normaliser H6 fuses on.

    Min-max lets one outlier compress every other score, and a z-score assumes a
    shape that lexical scores do not have. Quantiles assume neither.
    """
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    out = [0.0] * len(scores)
    n = len(scores)
    if n == 1:
        return [1.0]
    for position, index in enumerate(order):
        out[index] = position / (n - 1)
    return out


def score_fusion(runs, normaliser=minmax, weights=None):
    """Fuse raw scores after normalising each system separately."""
    weights = weights or {name: 1.0 for name in runs}
    totals = defaultdict(float)
    for name, rows in runs.items():
        ids = [cid for cid, _ in rows]
        values = normaliser([s for _, s in rows])
        weight = weights.get(name, 1.0)
        for doc_id, value in zip(ids, values):
            totals[doc_id] += weight * value
    ids = sorted(totals)
    return rank([totals[i] for i in ids], ids)
