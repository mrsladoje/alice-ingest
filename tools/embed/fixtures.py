#!/usr/bin/env python3
"""Run each adapter's golden fixture and write the result as soon as it lands.

A download that fails twice records the adapter as failed and the run continues,
because no single missing model may stop a stage. Results append to a JSON file
after every model, so a crash leaves the fixtures that already passed on disk.
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters

OUT = "downloads/frozen/logs/fixtures.json"


def load_existing(path):
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            return {}
    return {}


def record(path, key, value):
    data = load_existing(path)
    data[key] = value
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def run_one(kind, key, device, attempts=2):
    builder = {"dense": adapters.DenseAdapter, "static": adapters.StaticAdapter,
               "late": adapters.LateAdapter}[kind]
    last = None
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            adapter = builder(key, device=device)
            result = adapter.fixture()
            result["kind"] = kind
            result["attempt"] = attempt
            result["seconds"] = round(time.time() - started, 1)
            result["revision"] = adapter.spec["revision"]
            result["model_id"] = adapter.spec["model_id"]
            return result
        except Exception as exc:
            last = {"model": key, "kind": kind, "attempt": attempt,
                    "passes": False, "error": "%s: %s" % (type(exc).__name__, exc),
                    "traceback": traceback.format_exc()[-1500:],
                    "seconds": round(time.time() - started, 1)}
            print("attempt %d failed for %s: %s" % (attempt, key, exc), flush=True)
    return last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--only", default=None)
    args = parser.parse_args()
    device = args.device or adapters.pick_device()
    plan = ([("static", k) for k in adapters.STATIC]
            + [("dense", k) for k in adapters.DENSE if k != "Qwen3-Embedding-8B"]
            + [("late", k) for k in adapters.LATE])
    if args.only:
        wanted = set(args.only.split(","))
        plan = [(kind, key) for kind, key in plan if key in wanted]
    done = load_existing(args.out)
    for kind, key in plan:
        if key in done and done[key].get("passes"):
            print("skip", key, "already passed", flush=True)
            continue
        print("=== fixture", kind, key, "on", device, flush=True)
        result = run_one(kind, key, device)
        record(args.out, key, result)
        print(json.dumps({k: v for k, v in result.items() if k != "traceback"}),
              flush=True)
    print("fixtures complete", flush=True)


if __name__ == "__main__":
    main()
