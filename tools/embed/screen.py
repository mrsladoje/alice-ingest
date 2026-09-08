#!/usr/bin/env python3
"""Run a set of arms over the development queries, score them, and write it all.

An arm is one system in one representation on one task. Every arm writes its own
run file with a complete manifest as soon as it finishes, so a crash leaves the
arms that already ran on disk rather than nothing.

Arms are compared against a named reference with a paired bootstrap over intent
groups, and a family of exploratory comparisons gets a Holm correction.
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import bench
import compare
import systems

LEXICAL = {"L0": systems.l0_body, "L1": systems.l1_body, "ID": systems.identifier_body}


def load_state(path):
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            return {}
    return {}


def save_state(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)


def run_arm(arm, corpus, queries, qrels, info, depth, run_dir, task,
            corpus_id, query_set_id, judgment_set_id, mode="query"):
    kind = arm["kind"]
    name = arm["name"]
    started = time.time()
    if kind == "lexical":
        client = systems.client()
        builder = systems.lexical_builder(arm["system"],
                                          arm.get("representation", "R3"),
                                          arm.get("weights"))
        results, manifest = compare.lexical_run(
            client, name, queries, builder, corpus_id,
            query_set_id, judgment_set_id, info, depth=depth,
            arm=arm.get("representation", "R3"))
    elif kind in ("dense", "static"):
        results, manifest = compare.dense_run(
            arm["system"], corpus, name, queries, corpus_id, query_set_id,
            judgment_set_id, arm=arm.get("representation", "R1"), depth=depth,
            mode=mode, kind=kind)
    elif kind == "late":
        results, manifest = compare.late_run(
            arm["system"], corpus, name, queries, corpus_id, query_set_id,
            judgment_set_id, arm=arm.get("representation", "R1"), depth=depth)
    else:
        raise ValueError("unknown arm kind %r" % kind)
    if task == "template_similarity":
        results = systems.exclude_self(results, queries, corpus)
    path = bench.write_run(os.path.join(run_dir, "%s.json" % name), name, name,
                           task, manifest, results, depth=depth)
    scored = bench.score_run(bench.read_run(path), qrels, queries, task=task)
    scored["run_file"] = path
    scored["wall_seconds"] = round(time.time() - started, 1)
    scored["manifest_missing"] = bench.manifest_is_complete(manifest)
    return scored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", required=True,
                        help="JSON file holding a list of arm definitions")
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--task", default="natural_language")
    parser.add_argument("--reference", default="L0")
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-dir", default="downloads/frozen/runs")
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--metric", default="ndcg@10")
    parser.add_argument("--corpus", default="downloads/frozen/corpus-2026-09-08/corpus.jsonl")
    parser.add_argument("--duplicates", default="downloads/frozen/corpus-2026-09-08/duplicates.json")
    parser.add_argument("--query-set-id", default="development-97-2026-09-08")
    args = parser.parse_args()

    corpus = bench.Corpus.load(args.corpus, args.duplicates)
    queries = {}
    for path in args.queries:
        queries.update(bench.load_queries(path, task=args.task))
    qrels = bench.Qrels.load(args.qrels)
    arms = json.load(open(args.arms))

    client = systems.client()
    settings = client.indices.get_settings(index=systems.INDEX)
    key = list(settings)[0]
    info = {"opensearch_version": systems.engine_version(client),
            "index": systems.INDEX,
            "index_uuid": settings[key]["settings"]["index"]["uuid"],
            "primary_shards": 1, "replicas": 0}

    state = load_state(args.out)
    state.setdefault("arms", {})
    state.setdefault("failed", {})
    state["task"] = args.task
    state["queries"] = len(queries)
    state["intent_groups"] = len({q["intent_group"] for q in queries.values()})
    mode = "symmetric" if args.task == "template_similarity" else "query"

    for arm in arms:
        name = arm["name"]
        if name in state["arms"]:
            print("skip", name, "already done", flush=True)
            continue
        print("=== arm", name, flush=True)
        try:
            scored = run_arm(arm, corpus, queries, qrels, info, args.depth,
                             args.run_dir, args.task, "03640622026320ba",
                             args.query_set_id, os.path.basename(args.qrels),
                             mode=mode)
            state["arms"][name] = {
                "summary": scored["summary"],
                "intents_scored": scored["intents_scored"],
                "run_file": scored["run_file"],
                "wall_seconds": scored["wall_seconds"],
                "manifest_missing": scored["manifest_missing"],
                "per_intent": scored["per_intent"],
                "definition": arm,
            }
            print(name, json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                    for k, v in scored["summary"].items()}), flush=True)
        except Exception as exc:
            state["failed"][name] = {"error": "%s: %s" % (type(exc).__name__, exc),
                                     "traceback": traceback.format_exc()[-1200:],
                                     "definition": arm}
            print("FAILED", name, exc, flush=True)
        save_state(args.out, state)

    if args.reference in state["arms"]:
        reference = {g: r.get(args.metric) for g, r
                     in state["arms"][args.reference]["per_intent"].items()}
        comparisons = {}
        for name, row in state["arms"].items():
            if name == args.reference:
                continue
            values = {g: r.get(args.metric) for g, r in row["per_intent"].items()}
            comparisons[name] = bench.bootstrap_paired(values, reference)
        adjusted = bench.holm({n: c["p"] for n, c in comparisons.items()})
        for name in comparisons:
            comparisons[name]["p_holm"] = adjusted[name]
        state["comparisons"] = {"reference": args.reference, "metric": args.metric,
                                "against_reference": comparisons}
        save_state(args.out, state)

    print(json.dumps({"arms": len(state["arms"]), "failed": list(state["failed"])},
                     indent=1))


if __name__ == "__main__":
    main()
