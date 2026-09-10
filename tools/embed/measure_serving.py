#!/usr/bin/env python3
"""Measure what bounded semantic search costs inside the shifter server.

The plan blocks deployment without these figures: docs/SHIFTER_COCKPIT_PLAN.md
sections 6 and 12. Run:

    downloads/frozen/venv/bin/python tools/embed/measure_serving.py

Nothing here selects a model. docs/SEMANTIC_PLAN.md owns that decision.
"""
import argparse
import json
import math
import os
import random
import resource
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SHIFTER_FILES = os.path.join(REPO, "deploy", "roles", "loggy_shifter_view", "files")
SHARED = os.path.join(REPO, "deploy", "shared")

CORPUS_DIR = os.path.join(REPO, "downloads", "frozen", "corpus-2026-09-08")
CORPUS_PATH = os.path.join(CORPUS_DIR, "corpus.jsonl")
MANIFEST_PATH = os.path.join(CORPUS_DIR, "manifest.json")
QUERIES_PATH = os.path.join(HERE, "queries.txt")

PLAN_VERSIONS = 5571
PLAN_DIMENSIONS = 512
PLAN_MATRIX_BYTES = 11409408
SERVICE_CEILING_BYTES = 384 * 1024 * 1024
LIVE_LANE_RESERVE_BYTES = 32 * 1024 * 1024

CANDIDATE_MODEL = "minishlab/potion-retrieval-32M"
CANDIDATE_REVISION = "6fc8051fab2a1e0ee76689cf08c853792ac285e7"
CANDIDATE_BACKEND = "model2vec"

MEASURED = "MEASURE_JSON "

FALLBACK_QUERIES = [
    "out of memory on the reconstruction node",
    "shared memory region is full",
    "the run stopped without a reason",
    "a partition failed to start",
    "too many clients connected to the daemon",
    "disk is full",
    "the timeframe builder dropped a frame",
    "calibration object not found",
    "connection refused by the orchestrator",
    "a device changed state to error",
]


def resident_bytes():
    try:
        with open("/proc/self/statm") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    try:
        output = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                                capture_output=True, text=True, timeout=30)
        return int(output.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def peak_resident_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(peak)
    return int(peak) * 1024


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(math.ceil(fraction * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def read_corpus(path, limit=None):
    templates = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            template = json.loads(line).get("template")
            if not template or template in templates:
                continue
            templates.append(template)
            if limit and len(templates) >= limit:
                break
    return templates


def read_manifest(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def read_queries(path, wanted):
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        lines = []
    if not lines:
        lines = list(FALLBACK_QUERIES)
    while len(lines) < wanted:
        lines = lines + lines
    return lines[:wanted]


def synthetic_vectors(count, dimensions, seed=20260908):
    source = random.Random(seed)
    return [[source.gauss(0.0, 1.0) for _ in range(dimensions)]
            for _ in range(count)]


def worker(args):
    started = args.started
    baseline = resident_bytes()
    result = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "baseline_rss_bytes": baseline,
        "model_id": args.model,
        "backend": args.backend,
        "dimensions": args.dimensions,
        "model_loaded": False,
        "model_error": "",
        "search_path": "exact dot product over array('f') rows, standard "
                       "library only",
        "unmeasured": [],
    }

    os.environ.setdefault("ALICE_SHARED_PATH", SHARED)
    sys.path.insert(0, SHIFTER_FILES)
    sys.path.insert(0, SHARED)
    import semantic

    result["module_rss_bytes"] = resident_bytes()

    config = semantic.Config(
        enabled=True, backend=args.backend, model_path=args.model,
        model_revision=args.revision, dimensions=args.dimensions,
        max_versions=args.max_versions,
        max_vector_bytes=args.max_vector_bytes,
        batch_size=args.batch_size, max_results=args.limit,
        encode_wait_seconds=600.0, query_wait_seconds=600.0)

    result["vector_capacity"] = config.vector_capacity
    result["resident_ceiling_bytes"] = config.resident_ceiling()

    corpus = read_corpus(args.corpus, args.versions)
    result["corpus_versions"] = len(corpus)
    result["corpus_rss_bytes"] = resident_bytes()

    encoder = None
    load_started = time.time()
    try:
        encoder = semantic.load_encoder(config)
    except Exception as exc:
        detail = getattr(exc, "detail", "")
        result["model_error"] = "%r %s" % (exc, detail) if detail else repr(exc)
    result["model_load_seconds"] = time.time() - load_started

    if encoder is None:
        result["unmeasured"] = [
            "resident memory after loading the selected model",
            "peak resident memory during a batch encode of the frozen corpus",
            "cold start time to first query",
            "per-query latency including query encoding",
        ]
        store = semantic.VectorStore(args.dimensions, config.vector_capacity)
        vectors = synthetic_vectors(len(corpus), args.dimensions)
        for template, values in zip(corpus, vectors):
            store.add(template, values)
        result["vector_matrix_bytes"] = store.vector_bytes
        latencies = []
        probes = synthetic_vectors(args.repeats, args.dimensions, seed=7)
        for probe in probes:
            query = semantic.unit_vector(probe, args.dimensions)
            probe_started = time.perf_counter()
            store.search(query, args.limit)
            latencies.append((time.perf_counter() - probe_started) * 1000.0)
        result["search_only_p50_ms"] = percentile(latencies, 0.50)
        result["search_only_p95_ms"] = percentile(latencies, 0.95)
        result["search_only_note"] = ("measured on synthetic unit vectors of "
                                      "the same shape; no model was loaded")
        result["peak_rss_bytes"] = peak_resident_bytes()
        print(MEASURED + json.dumps(result), flush=True)
        return 0

    result["model_loaded"] = True
    result["model_dimensions"] = encoder.dimensions
    result["model_rss_bytes"] = resident_bytes()
    result["model_peak_rss_bytes"] = peak_resident_bytes()

    search = semantic.SemanticSearch(config=config,
                                     encoder_factory=lambda cfg: encoder)
    versions = [semantic.Version(template, template, int(time.time() * 1000))
                for template in corpus]

    encode_started = time.time()
    snapshot = search.refresh(versions)
    result["encode_seconds"] = time.time() - encode_started
    result["encode_peak_rss_bytes"] = peak_resident_bytes()
    result["encode_rss_bytes"] = resident_bytes()
    result["snapshot_status"] = snapshot.status
    result["snapshot_coverage"] = snapshot.coverage
    result["snapshot_versions"] = snapshot.versions
    result["snapshot_truncated"] = snapshot.truncated
    result["vector_matrix_bytes"] = snapshot.vector_bytes

    queries = read_queries(args.queries, args.repeats)
    first = search.search(queries[0], limit=args.limit)
    result["cold_start_seconds"] = time.time() - started
    result["first_query_status"] = first.status
    result["first_query_hits"] = len(first.hits)

    latencies = []
    for query in queries:
        probe_started = time.perf_counter()
        search.search(query, limit=args.limit)
        latencies.append((time.perf_counter() - probe_started) * 1000.0)
    result["query_p50_ms"] = percentile(latencies, 0.50)
    result["query_p95_ms"] = percentile(latencies, 0.95)
    result["query_samples"] = len(latencies)
    result["serving_rss_bytes"] = resident_bytes()
    result["peak_rss_bytes"] = peak_resident_bytes()
    print(MEASURED + json.dumps(result), flush=True)
    return 0


def megabytes(value):
    if value is None:
        return "not measured"
    return "%.1f MiB" % (value / 1048576.0)


def integer(value):
    if value is None:
        return "not measured"
    return "{:,}".format(int(value))


def seconds(value):
    if value is None:
        return "not measured"
    return "%.3f s" % value


def milliseconds(value):
    if value is None:
        return "not measured"
    return "%.2f ms" % value


def row(label, measured, note=""):
    return "| %-58s | %18s | %s" % (label, measured, note)


def report(result, manifest, wall_seconds):
    lines = []
    corpus_versions = (manifest.get("totals") or {}).get(
        "templates", result.get("corpus_versions"))
    lines.append("Bounded semantic search: measured serving cost")
    lines.append("")
    lines.append("host python           %s on %s"
                 % (result.get("python", "?"), result.get("platform", "?")))
    lines.append("frozen corpus         %s, %s template versions"
                 % (manifest.get("corpus_id", "unknown"),
                    integer(corpus_versions)))
    lines.append("model measured        %s, backend %s, %d dimensions"
                 % (result.get("model_id"), result.get("backend"),
                    result.get("dimensions")))
    lines.append("model status          %s"
                 % ("loaded" if result.get("model_loaded")
                    else "NOT LOADED: " + result.get("model_error", "")))
    lines.append("selection             docs/SEMANTIC_PLAN.md owns it and has "
                 "not finished; this is a candidate, not a winner")
    lines.append("")
    lines.append(row("figure", "measured", "note"))
    lines.append("|" + "-" * 60 + "|" + "-" * 20 + "|" + "-" * 40)
    lines.append(row("interpreter baseline resident memory",
                     megabytes(result.get("baseline_rss_bytes")),
                     "%s bytes, before any import"
                     % integer(result.get("baseline_rss_bytes"))))
    lines.append(row("resident memory after importing semantic.py",
                     megabytes(result.get("module_rss_bytes")),
                     "standard library only"))
    lines.append(row("resident memory after loading the selected model",
                     megabytes(result.get("model_rss_bytes")),
                     "load took %s"
                     % seconds(result.get("model_load_seconds"))))
    model_only = None
    if result.get("model_rss_bytes") and result.get("module_rss_bytes"):
        model_only = (result["model_rss_bytes"] - result["module_rss_bytes"])
    lines.append(row("peak resident memory during the model load",
                     megabytes(result.get("model_peak_rss_bytes")),
                     "the model itself accounts for %s resident"
                     % megabytes(model_only)))
    lines.append(row("vector matrix bytes, %s versions at %d dimensions, "
                     "4 bytes" % (integer(PLAN_VERSIONS), PLAN_DIMENSIONS),
                     integer(PLAN_MATRIX_BYTES),
                     "%s; the plan states the same number"
                     % megabytes(PLAN_MATRIX_BYTES)))
    lines.append(row("vector matrix bytes actually built",
                     integer(result.get("vector_matrix_bytes")),
                     "%s over %s versions"
                     % (megabytes(result.get("vector_matrix_bytes")),
                        integer(result.get("snapshot_versions",
                                           result.get("corpus_versions"))))))
    lines.append(row("peak resident memory during a batch encode",
                     megabytes(result.get("encode_peak_rss_bytes")),
                     "encode took %s"
                     % seconds(result.get("encode_seconds"))))
    lines.append(row("cold start time to first query",
                     seconds(result.get("cold_start_seconds")),
                     "process start, model load, whole corpus, first answer"))
    lines.append(row("per-query latency, 50th percentile",
                     milliseconds(result.get("query_p50_ms")),
                     "%s samples"
                     % integer(result.get("query_samples", 0))))
    lines.append(row("per-query latency, 95th percentile",
                     milliseconds(result.get("query_p95_ms")),
                     result.get("search_path", "")))
    if not result.get("model_loaded"):
        lines.append(row("search-only latency, 50th percentile",
                         milliseconds(result.get("search_only_p50_ms")),
                         result.get("search_only_note", "")))
        lines.append(row("search-only latency, 95th percentile",
                         milliseconds(result.get("search_only_p95_ms")),
                         "encoding is not included"))
    lines.append(row("computed resident ceiling at the configured limits",
                     megabytes(result.get("resident_ceiling_bytes")),
                     "%s versions fit the vector budget"
                     % integer(result.get("vector_capacity"))))
    lines.append(row("peak resident memory over the whole run",
                     megabytes(result.get("peak_rss_bytes")),
                     "the number to compare against the unit"))
    lines.append("")
    lines.append("wall clock for this measurement: %s" % seconds(wall_seconds))
    lines.append("")

    peak = result.get("peak_rss_bytes")
    lines.append("Against the shifter unit's MemoryMax of 384 MiB (%s bytes):"
                 % integer(SERVICE_CEILING_BYTES))
    lines.append("  This machine is not the deployment host. Plan section 12 "
                 "still requires the same figures on an EPN node.")
    lines.append("  The live lane, the query lane and their buffers are not in "
                 "this process. %s is set aside for them below as a stated "
                 "allowance, not as a measurement."
                 % megabytes(LIVE_LANE_RESERVE_BYTES))
    if not result.get("model_loaded"):
        lines.append("  The model did not load, so the total is NOT measured "
                     "and this run does not answer the question.")
        lines.append("  Reason: %s" % result.get("model_error", "unknown"))
        for missing in result.get("unmeasured", []):
            lines.append("  Not measured: %s" % missing)
        lines.append("  Do not read the figures above as a pass.")
        return "\n".join(lines)
    if not peak:
        lines.append("  Resident memory could not be read on this host, so "
                     "the total is not measured.")
        return "\n".join(lines)
    total = peak + LIVE_LANE_RESERVE_BYTES
    lines.append("  Measured peak %s, plus the reserve, is %s."
                 % (megabytes(peak), megabytes(total)))
    if peak > SERVICE_CEILING_BYTES:
        lines.append("  IT DOES NOT FIT. The semantic path alone exceeds the "
                     "ceiling by %s."
                     % megabytes(peak - SERVICE_CEILING_BYTES))
    elif total > SERVICE_CEILING_BYTES:
        lines.append("  IT DOES NOT FIT. The semantic path alone leaves only "
                     "%s, and the rest of the service needs more than that."
                     % megabytes(SERVICE_CEILING_BYTES - peak))
    else:
        lines.append("  IT FITS, with %s left after the reserve."
                     % megabytes(SERVICE_CEILING_BYTES - total))
    if total > SERVICE_CEILING_BYTES:
        lines.append("  Semantic search must stay off on this unit as "
                     "measured. Either a smaller model is selected, or "
                     "MemoryMax rises with this measurement recorded beside "
                     "it.")
    return "\n".join(lines)


def parse(argv):
    parser = argparse.ArgumentParser(
        description="measure the serving cost of bounded semantic search")
    parser.add_argument("--model", default=os.environ.get(
        "SHIFTER_SEMANTIC_MODEL_PATH") or CANDIDATE_MODEL)
    parser.add_argument("--backend", default=os.environ.get(
        "SHIFTER_SEMANTIC_BACKEND") or CANDIDATE_BACKEND)
    parser.add_argument("--revision", default=os.environ.get(
        "SHIFTER_SEMANTIC_MODEL_REVISION") or CANDIDATE_REVISION)
    parser.add_argument("--dimensions", type=int, default=PLAN_DIMENSIONS)
    parser.add_argument("--corpus", default=CORPUS_PATH)
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--queries", default=QUERIES_PATH)
    parser.add_argument("--versions", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-versions", type=int, default=20000)
    parser.add_argument("--max-vector-bytes", type=int, default=16777216)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--json", default="")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--started", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.worker:
        return worker(args)

    if not os.path.isfile(args.corpus):
        print("the frozen corpus is not at %s; pass --corpus" % args.corpus)
        return 1

    child = [sys.executable, os.path.abspath(__file__), "--worker",
             "--model", args.model, "--backend", args.backend,
             "--revision", args.revision,
             "--dimensions", str(args.dimensions),
             "--corpus", args.corpus, "--queries", args.queries,
             "--versions", str(args.versions),
             "--repeats", str(args.repeats),
             "--limit", str(args.limit),
             "--batch-size", str(args.batch_size),
             "--max-versions", str(args.max_versions),
             "--max-vector-bytes", str(args.max_vector_bytes)]
    environment = dict(os.environ)
    if not args.allow_network:
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
    started = time.time()
    child = child + ["--started", repr(started)]
    finished = subprocess.run(child, capture_output=True, text=True,
                              env=environment)
    wall_seconds = time.time() - started

    payload = None
    for line in finished.stdout.splitlines():
        if line.startswith(MEASURED):
            payload = json.loads(line[len(MEASURED):])
    if payload is None:
        print("the measurement process produced no result")
        print(finished.stdout[-4000:])
        print(finished.stderr[-4000:])
        return 1

    text = report(payload, read_manifest(args.manifest), wall_seconds)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
