#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RIG = os.path.join(HERE, "rig", "docker-compose.soak.yaml")
RUNS = os.path.join(HERE, "runs")

PROFILES = {
    "p0": {
        "what": "Fluent Bit's own ceiling, null sink, staircase",
        "sink": "null", "mode": "ramp", "duration": 600,
        "gun": {"start": 2000, "step": 2000, "step_seconds": 60, "max": 40000},
    },
    "p1": {
        "what": "pipeline ceiling against an HTTP sink that always says yes",
        "sink": "http", "mode": "ramp", "duration": 600,
        "gun": {"start": 2000, "step": 2000, "step_seconds": 60, "max": 40000},
    },
    "p1os": {
        "what": "the product's ceiling against a real OpenSearch",
        "sink": "opensearch", "mode": "ramp", "duration": 600,
        "gun": {"start": 1000, "step": 1000, "step_seconds": 60, "max": 15000},
    },
    "p2": {
        "what": "burst absorption: 30 s peak, 120 s quiet",
        "sink": "http", "mode": "burst", "duration": 900,
        "gun": {"base": 2000, "peak": 40000, "on": 30, "off": 120},
    },
    "p3": {
        "what": "durability: the sink disappears mid-run",
        "sink": "http", "mode": "steady", "duration": 1200,
        "gun": {"rate": 5000},
        "faults": [(120, "down"), (420, "up")],
    },
    "p5": {
        "what": "long soak, half the knee rate",
        "sink": "http", "mode": "steady", "duration": 86400,
        "gun": {"rate": 2000},
    },
    "p6": {
        "what": "one fixed point, for sweeping configuration knobs",
        "sink": "http", "mode": "steady", "duration": 300,
        "gun": {"rate": 8000},
    },
}


def say(text):
    print("[soak] %s" % text, flush=True)


def run(argv, env=None, check=True, capture=False):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    if capture:
        proc = subprocess.run(argv, env=merged, capture_output=True, text=True,
                              check=False)
        if check and proc.returncode != 0:
            raise SystemExit("soak: command failed: %s\n%s" % (
                " ".join(argv), proc.stderr.strip()))
        return proc.stdout.strip()
    proc = subprocess.run(argv, env=merged, check=False)
    if check and proc.returncode != 0:
        raise SystemExit("soak: command failed: %s" % " ".join(argv))
    return ""


def compose_binary():
    probe = subprocess.run(["docker", "compose", "version"],
                           capture_output=True, text=True, check=False)
    if probe.returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise SystemExit("soak: neither `docker compose` nor `docker-compose` is available")


COMPOSE = []


def compose(env, *args, profiles=(), check=True):
    if not COMPOSE:
        COMPOSE.extend(compose_binary())
    argv = COMPOSE + ["-f", RIG]
    for name in profiles:
        argv += ["--profile", name]
    argv += list(args)
    return run(argv, env=env, check=check)


def get_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def post(url, timeout=5):
    try:
        request = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except Exception:
        return 0


def wait_for(url, seconds, label):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if get_json(url) is not None:
            return True
        time.sleep(2)
    raise SystemExit("soak: %s never became reachable at %s" % (label, url))


def colima_profile():
    out = run(["colima", "list", "--json"], capture=True, check=False)
    for line in out.split("\n"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("status") == "Running":
            return entry.get("name", "")
    return ""


def set_memory_high(container, limit):
    if not limit:
        return "not set"
    container_id = run(["docker", "inspect", "-f", "{{.Id}}", container],
                       capture=True, check=False)
    if not container_id:
        return "container not found"
    profile = colima_profile()
    prefix = ["colima", "ssh"]
    if profile and profile != "default":
        prefix += ["--profile", profile]
    for pattern in ("/sys/fs/cgroup/system.slice/docker-%s.scope",
                    "/sys/fs/cgroup/docker/%s"):
        path = pattern % container_id
        probe = subprocess.run(
            prefix + ["--", "sudo", "sh", "-c",
                      "echo %s > %s/memory.high && cat %s/memory.high"
                      % (limit, path, path)],
            capture_output=True, text=True, check=False)
        if probe.returncode == 0 and probe.stdout.strip():
            return "memory.high=%s at %s" % (probe.stdout.strip(), path)
    return "could not set memory.high (Rig A enforces memory.max only)"


def build_fixture(run_dir, count, from_logs):
    target = os.path.join(run_dir, "fixture")
    argv = [sys.executable, os.path.join(HERE, "mkfixture.py"),
            "--out", target, "--count", str(count)]
    if from_logs:
        argv += ["--from-logs", from_logs]
    run(argv, capture=True)
    return target


def build_config(run_dir, args, sink, sink_host, sink_port, families="all",
                 folder="conf"):
    conf = os.path.join(run_dir, folder)
    os.makedirs(conf, exist_ok=True)
    argv = [sys.executable, os.path.join(HERE, "mkconfig.py"),
            "--out", os.path.join(conf, "collector.yaml"),
            "--parsers-out", os.path.join(conf, "parsers.yaml"),
            "--sink", "opensearch" if sink == "cluster" else sink,
            "--sink-host", sink_host,
            "--sink-port", str(sink_port),
            "--flush", str(args.flush),
            "--max-chunks-up", str(args.max_chunks_up),
            "--storage-type", args.storage_type,
            "--pause-on-overlimit", args.pause_on_overlimit,
            "--total-limit-size", args.total_limit_size,
            "--retry-limit", str(args.retry_limit),
            "--lua", args.lua,
            "--health", args.health,
            "--live-lane", args.live_lane,
            "--live-lane-host", args.live_lane_host,
            "--live-lane-port", str(args.live_lane_port),
            "--log-level", args.log_level,
            # t3 is not a rendering arm: its difference is the families
            # split below, and each of its two processes renders as shipped.
            "--arm", "t0" if args.arm == "t3" else args.arm,
            "--lane-own-tag", args.lane_own_tag,
            "--infologger-tap", args.infologger_tap,
            "--families", families]
    if args.os_buffer_size:
        argv += ["--os-buffer-size", args.os_buffer_size]
    if args.lane_compress:
        argv += ["--lane-compress", args.lane_compress]
    if args.mem_buf_limit:
        argv += ["--mem-buf-limit", args.mem_buf_limit]
    if args.backlog_mem_limit:
        argv += ["--backlog-mem-limit", args.backlog_mem_limit]
    if args.output_workers >= 0:
        argv += ["--output-workers", str(args.output_workers)]
    if args.compress:
        argv += ["--compress", args.compress]
    run(argv, capture=True)
    return conf


def gun_command(args, profile, run_dir):
    settings = dict(profile.get("gun", {}))
    mode = args.mode or profile["mode"]
    argv = ["python3", "/opt/soak/logburst.py",
            "--fixture", "/out/fixture",
            "--tail-root", "/var/log/node",
            "--il-host", "127.0.0.1", "--il-port", "5170",
            "--mode", mode,
            "--duration", str((args.duration or profile["duration"]) + args.settle),
            "--mix", args.mix,
            "--hosts", str(args.hosts),
            "--workers", str(args.gun_workers),
            "--run-id", args.run_id,
            "--max-file-bytes", str(args.max_file_bytes),
            "--disk-guard-pct", str(args.disk_guard_pct),
            "--csv", "/out/logburst.csv",
            "--summary", "/out/logburst-summary.json"]
    for key, flag in (("rate", "--rate"), ("start", "--start"), ("step", "--step"),
                      ("step_seconds", "--step-seconds"), ("max", "--max"),
                      ("base", "--base"), ("peak", "--peak"), ("on", "--on"),
                      ("off", "--off")):
        value = getattr(args, key, None)
        if value is None:
            value = settings.get(key)
        if value is not None:
            argv += [flag, str(value)]
    return argv


def apply_fault(mode, sink_ctl, env, service):
    if mode == "down":
        compose(env, "stop", service, check=False)
        return "stopped %s" % service
    if mode == "up":
        compose(env, "start", service, check=False)
        return "started %s" % service
    return "sink mode %s (http %s)" % (mode, post("%s?mode=%s" % (sink_ctl, mode)))


def schedule_faults(faults, sink_ctl, env, service, log):
    timers = []
    for offset, mode in faults:
        def fire(mode=mode, offset=offset):
            note = apply_fault(mode, sink_ctl, env, service)
            log.append({"t": offset, "action": mode, "result": note})
            say("t+%ds %s" % (offset, note))
        timer = threading.Timer(offset, fire)
        timer.daemon = True
        timer.start()
        timers.append(timer)
    return timers


def queue_depth(fb_url):
    doc = get_json(fb_url + "/api/v1/storage")
    if not isinstance(doc, dict):
        return -1
    chunks = (doc.get("storage_layer") or {}).get("chunks") or {}
    return chunks.get("total_chunks", -1)


def drain(fb_url, seconds):
    if seconds <= 0:
        return {"drained": False, "queue_at_end": queue_depth(fb_url), "seconds": 0}
    say("draining until the queue is empty, up to %ds" % seconds)
    start = time.time()
    deadline = start + seconds
    previous, stable = -1, 0
    while time.time() < deadline:
        depth = queue_depth(fb_url)
        if depth == 0:
            return {"drained": True, "queue_at_end": 0,
                    "seconds": round(time.time() - start, 1)}
        doc = get_json(fb_url + "/api/v1/metrics")
        total = 0
        if isinstance(doc, dict):
            for value in (doc.get("output") or {}).values():
                total += value.get("proc_records", 0)
        if total == previous:
            stable += 1
            if stable >= 8:
                return {"drained": False, "queue_at_end": depth,
                        "seconds": round(time.time() - start, 1)}
        else:
            stable = 0
        previous = total
        time.sleep(2)
    return {"drained": False, "queue_at_end": queue_depth(fb_url),
            "seconds": round(time.time() - start, 1)}


SELFTEST_FILE = os.path.join(HERE, "selftest.json")


def container_ceiling(heap):
    """The container has to hold more than the heap it is given.

    A JVM asked for -Xmx2g inside a 2g container cannot start: the heap is only
    part of what the process maps, and the rest — metaspace, thread stacks, the
    Lucene buffers OpenSearch keeps off-heap — has nowhere to go. The rig's
    default is a 1g heap in a 2g container, so the grid keeps that ratio rather
    than pinning the ceiling and calling the result a heap measurement.

    Without this, a heap grid does not measure heap. It measures which values
    fit inside a fixed ceiling, and every value above it reads as an unreachable
    cluster rather than as a cost.
    """
    text = str(heap).strip().lower()
    if text.endswith("g"):
        return "%dg" % (int(float(text[:-1]) * 2))
    if text.endswith("m"):
        return "%dm" % (int(float(text[:-1]) * 2))
    return text


def cpuset_for(count):
    """A pinned set of the first N cores. The plan enforces with cpuset and
    not with a quota, because a quota makes nr_throttled the only saturation
    signal and pinning leaves it at zero for ever."""
    count = max(1, int(count))
    return "0" if count == 1 else "0-%d" % (count - 1)


def cpuset_size(text):
    total = 0
    for part in (text or "").split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            try:
                total += int(high) - int(low) + 1
            except ValueError:
                pass
        elif part.isdigit():
            total += 1
    return total


def read_logburst(run_dir):
    path = os.path.join(run_dir, "logburst.csv")
    rows = []
    try:
        with open(path) as handle:
            header = handle.readline().strip().split(",")
            for line in handle:
                parts = line.strip().split(",")
                if len(parts) != len(header):
                    continue
                rows.append(dict(zip(header, parts)))
    except OSError:
        return []
    return rows


def gate_zero(run_dir, tolerance_pct, edge_seconds=2, settle=0):
    """The first gate, checked before anything else in the cell is read.

    A generator that quietly falls short produces a cell where delivered
    equals ingested and nothing drops — a pass, and a lie. So the offer the
    generator actually made is compared with the offer the cell asked for.
    The first and last seconds are dropped: they are partial by construction.
    """
    rows = read_logburst(run_dir)
    if settle:
        rows = [row for row in rows
                if float(row.get("second", 0) or 0) >= settle]
    if len(rows) <= 2 * edge_seconds:
        return {"checked": False, "why": "logburst.csv too short to judge"}
    body = rows[edge_seconds:-edge_seconds] or rows
    intended = achieved = 0.0
    worst = None
    for row in body:
        try:
            target = float(row["target_rate"])
            total = float(row["total"])
        except (KeyError, ValueError):
            continue
        intended += target
        achieved += total
        if target > 0:
            miss = 100.0 * (total - target) / target
            if worst is None or miss < worst[0]:
                worst = (round(miss, 2), int(float(row["second"])))
    if intended <= 0:
        return {"checked": False, "why": "no intended rate in logburst.csv"}
    error = 100.0 * (achieved - intended) / intended
    return {
        "checked": True,
        "seconds_judged": len(body),
        "intended_records": int(intended),
        "achieved_records": int(achieved),
        "error_pct": round(error, 2),
        "tolerance_pct": tolerance_pct,
        "worst_second_pct": worst[0] if worst else 0,
        "worst_second": worst[1] if worst else 0,
        "pass": abs(error) <= tolerance_pct,
    }


SELFTEST_MARGIN = 1.2


def selftest_evidence(rate):
    """Gate zero's second half: has the generator's own ceiling been proved
    above this rate? `soak.py selftest` writes the file this reads.

    A paced run that reaches exactly the rate it asked for proves pacing, not
    headroom — it never tried to go faster. The ceiling comes from the
    unpaced run, and the rate under test has to sit a clear margin below it.
    """
    try:
        with open(SELFTEST_FILE) as handle:
            proved = json.load(handle)
    except (OSError, ValueError):
        return {"proved": False, "why": "no selftest.json — run soak.py selftest"}
    ceiling = max((entry.get("achieved_rate", 0)
                   for entry in proved.get("runs", [])), default=0)
    paced = [entry for entry in proved.get("runs", [])
             if not entry.get("ceiling") and abs(entry.get("shortfall_pct", 99)) <= 1
             and entry.get("asked", 0) >= rate]
    if ceiling < rate * SELFTEST_MARGIN:
        return {"proved": False, "rate": rate, "generator_ceiling": ceiling,
                "why": "generator ceiling %.0f/s is under %.1f× this rate"
                       % (ceiling, SELFTEST_MARGIN)}
    return {"proved": True, "rate": rate,
            "generator_ceiling": ceiling,
            "headroom_x": round(ceiling / rate, 2) if rate else 0,
            "paced_cleanly_at": [entry["asked"] for entry in paced],
            "measured": proved.get("when", "")}


def wait_for_cluster(port, nodes, seconds):
    """Three containers have to become one cluster before a cell means
    anything. A cell run against a half-formed cluster measures the join."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        doc = get_json("http://127.0.0.1:%d/_cluster/health" % port, timeout=10)
        if isinstance(doc, dict) and doc.get("number_of_nodes", 0) >= nodes \
                and doc.get("status") in ("green", "yellow"):
            say("cluster: %d nodes, status %s" % (doc["number_of_nodes"],
                                                  doc["status"]))
            return doc
        time.sleep(3)
    raise SystemExit("soak: the cluster never reached %d nodes" % nodes)


def apply_bootstrap(run_dir, args):
    """Run the real `opensearch_bootstrap` script against the rig's cluster.

    Round 1's single bare container had no templates and no rollover aliases,
    collapsed at 2,000 records a second, and produced a number `docs/SOAK.md`
    tells the reader not to quote. This is what stops that happening twice.
    """
    script = os.path.join(run_dir, "templates.sh")
    run([sys.executable, os.path.join(HERE, "mkbootstrap.py"),
         "--out", script, "--node-id", args.node_id,
         "--replicas", str(args.replicas)], capture=True)
    # templates.sh resolves the registration script as
    # "$(dirname $0)/register_node.sh", so the deploy's own copy has to sit
    # beside it. It is a plain file, not a template.
    shutil.copyfile(
        os.path.join(REPO, "deploy", "roles",
                     "opensearch_local_index_registration", "files",
                     "register_node.sh"),
        os.path.join(run_dir, "register_node.sh"))
    os.chmod(os.path.join(run_dir, "register_node.sh"), 0o755)
    say("applying the deploy's own OpenSearch bootstrap (%d replica(s))"
        % args.replicas)
    proc = subprocess.run(
        ["sh", script],
        env=dict(os.environ, OS_URL="http://127.0.0.1:%d" % args.os_port,
                 SEED_EMPTY_INDICES="true"),
        capture_output=True, text=True, check=False)
    log_path = os.path.join(run_dir, "bootstrap.log")
    with open(log_path, "w") as handle:
        handle.write(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise SystemExit("soak: the OpenSearch bootstrap failed — see %s"
                         % log_path)
    say("bootstrap done, see %s" % log_path)


LOG_INDICES = "infologger*,application-logs-*"


def opensearch_count(port):
    base = "http://127.0.0.1:%d/%s" % (port, LOG_INDICES)
    doc = get_json(base + "/_count?ignore_unavailable=true", timeout=30)
    return doc.get("count", 0) if isinstance(doc, dict) else 0


def verdict(payload):
    """Gate zero first: a cell that fails it is void, not disqualified — it is
    rerun, not ranked. Then the four safety criteria, which only apply below
    the collector's ceiling. A 50,000/s cell records them and is described,
    never disqualified by them."""
    gate = payload.get("gate_zero", {})
    if gate.get("checked") and not gate.get("pass"):
        return {"state": "VOID", "why": "gate zero: offer was %.2f%% off intended"
                % gate.get("error_pct", 0)}
    if not gate.get("selftest", {}).get("proved", False):
        return {"state": "VOID", "why": "gate zero: generator headroom not proved "
                "at this rate — %s" % gate.get("selftest", {}).get("why", "")}
    if payload["gun"].get("aborted"):
        return {"state": "VOID", "why": "generator aborted: %s"
                % payload["gun"]["aborted"]}

    rec_probe = payload.get("recorder") or {}
    if not rec_probe.get("targets"):
        return {"state": "VOID", "why": "the recorder wrote no per-service "
                "figures — there is nothing here to rank"}
    sampling = rec_probe.get("sampling") or {}
    if sampling.get("checked") and sampling.get("late_pct", 0) > 2:
        return {"state": "VOID", "why": "the recorder itself stalled — %.1f%% "
                "of samples were late, worst gap %.1fs. A cell measured "
                "through a stall is not a measurement"
                % (sampling["late_pct"], sampling["max_gap_seconds"])}

    targets = (payload.get("recorder") or {}).get("targets") or {}
    for label, value in targets.items():
        if label in ("fb", "os"):
            continue
        if value.get("saturation_pct", 0) > 80:
            return {"state": "VOID", "why": "%s reached %.1f%% of its pinned "
                    "cores — the harness was the bottleneck"
                    % (label, value["saturation_pct"])}

    rec = payload.get("recorder") or {}
    families = payload.get("families") or {}
    lost = 0
    lane_dropped = 0
    lost_by_family = []
    for name, value in (rec.get("outputs") or {}).items():
        gone = value.get("dropped_records", 0) + value.get("retries_failed", 0)
        if (families.get(name) or {}).get("lane"):
            lane_dropped += gone
            continue
        lost += gone
        if gone:
            lost_by_family.append("%s lost %d" % (
                (families.get(name) or {}).get("family", name), gone))
    ingested = rec.get("ingested_records", 0)
    delivered = rec.get("output_records", 0)
    queue = (payload.get("drain") or {}).get("queue_at_end", -1)
    chunks_up = rec.get("peak_fs_chunks_up", 0)
    cap = payload.get("max_chunks_up", 64)
    failures = []
    if cap and chunks_up >= cap:
        failures.append("chunks up reached the cap (%d of %d)" % (chunks_up, cap))
    if lost:
        failures.append("lost %d records for good (%s)"
                        % (lost, "; ".join(lost_by_family)))
    if ingested and delivered < ingested * 0.999:
        # Fluent Bit's own counter is read one tick before the last chunks
        # complete, so a small gap at the end of a run is the recorder lagging
        # rather than a record going missing. If the queue drained empty, no
        # family lost anything, and the sink counted at least what was
        # ingested, every record arrived and the gap is arithmetic.
        drained = (payload.get("drain") or {}).get("drained")
        counted = payload.get("received", 0)
        if not (drained and not lost and counted >= ingested):
            failures.append("delivered %d of %d ingested" % (delivered, ingested))
    if queue not in (0, -1):
        failures.append("queue not empty at the end (%s chunks)" % queue)
    if payload.get("pattern") in ("OVER", "BURST"):
        return {"state": "DESCRIBED", "why": "%s cell: above the ceiling on "
                "purpose, so the four criteria are recorded, not applied"
                % payload.get("pattern"),
                "observed": failures or ["nothing broke"]}
    if failures:
        return {"state": "DISQUALIFIED", "why": "; ".join(failures),
                "lane_dropped": lane_dropped}
    note = ("delivered equals ingested, no log family lost a record, "
            "queue empty")
    if lane_dropped:
        note += (". The live lane dropped %d, which is the template's own "
                 "design: one retry and a 1 MB buffer, so a slow viewer never "
                 "pushes back on OpenSearch" % lane_dropped)
    return {"state": "PASS", "why": note, "lane_dropped": lane_dropped}


def core_seconds_per_million(targets, label, records):
    value = (targets.get(label) or {}).get("core_seconds", 0)
    if not records:
        return 0
    return round(value * 1e6 / records, 1)


def output_families(run_dir):
    """Fluent Bit names its outputs `http.0`, `http.1`, ... in configuration
    order, which tells the reader nothing about which family was lost. This
    reads the configuration that actually ran and puts the family back on the
    row.

    It also marks the live lane. The lane is best-effort **by construction** —
    the template gives it a 1 MB buffer and one retry precisely so a dead
    viewer never pushes back on OpenSearch — so lane drops are a design choice
    being exercised, not a failure. Counting them as loss would disqualify
    every lane cell in stage B for working as intended.
    """
    names = {}
    for folder, prefix in (("conf", ""), ("conf2", "proc2:")):
        path = os.path.join(run_dir, folder, "collector.yaml")
        try:
            with open(path) as handle:
                text = handle.read()
        except OSError:
            continue
        index = {}
        for block in text.split("- name: ")[1:]:
            head = block.split("\n")[0].strip()
            if head not in ("http", "opensearch", "null"):
                continue
            match = ""
            for line in block.split("\n"):
                stripped = line.strip()
                if stripped.startswith("match:"):
                    match = stripped.split(":", 1)[1].strip()
                    break
                if stripped.startswith("match_regex:"):
                    match = stripped.split(":", 1)[1].strip()
                    break
            lane = "uri: /ingest" in block
            position = index.get(head, 0)
            names["%s%s.%d" % (prefix, head, position)] = {
                "family": "live lane (best effort)" if lane else (match or "?"),
                "lane": lane,
            }
            index[head] = position + 1
    return names


def write_report(run_dir, payload):
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump(payload, handle, indent=2)

    gun = payload["gun"]
    rec = payload["recorder"]
    emitted = gun.get("emitted_total", 0)
    received = payload.get("received", 0)
    ingested = rec.get("ingested_records", 0)
    delivered = rec.get("output_records", 0)
    undelivered = ingested - delivered
    dropped = rec.get("output_dropped", 0) + rec.get("output_retries_failed", 0)
    drain_state = payload.get("drain", {})
    gate = payload.get("gate_zero", {})
    sampling = (rec.get("sampling") or {})
    call = payload.get("verdict", {})
    targets = (rec.get("targets") or {})
    threads = (rec.get("threads") or {})
    families = payload.get("families") or output_families(run_dir)
    million = max(1, ingested)
    lines = [
        "# soak run %s" % payload["run_id"],
        "",
        "**%s — %s**" % (call.get("state", "?"), call.get("why", "")),
        "",
        "## Gate zero, checked before anything else is read",
        "",
        "| check | value |",
        "|---|---|",
        "| offer achieved against intended | %s |" % (
            "%+.2f%% (tolerance %.1f%%) — %s" % (
                gate.get("error_pct", 0), gate.get("tolerance_pct", 0),
                "pass" if gate.get("pass") else "FAIL")
            if gate.get("checked") else gate.get("why", "not checked")),
        "| worst single second | %+.2f%% at t+%ss |" % (
            gate.get("worst_second_pct", 0), gate.get("worst_second", 0)),
        "| recorder kept up | %s |" % (
            ("yes, %d samples, worst gap %.1fs"
             % (sampling.get("samples", 0), sampling.get("max_gap_seconds", 0)))
            if sampling.get("clean") else
            ("NO — %.1f%% late, worst gap %.1fs, %.0fs lost"
             % (sampling.get("late_pct", 0), sampling.get("max_gap_seconds", 0),
                sampling.get("seconds_lost", 0)))
            if sampling.get("checked") else "not checked"),
        "| generator headroom proved | %s |" % (
            "yes, ceiling %.0f/s, %.1f× this rate"
            % (gate.get("selftest", {}).get("generator_ceiling", 0),
               gate.get("selftest", {}).get("headroom_x", 0))
            if gate.get("selftest", {}).get("proved")
            else "NO — %s" % gate.get("selftest", {}).get("why", "")),
        "",
        "## Processor time, the resource the EPN rations",
        "",
        "Core-seconds per million records ingested, so the figure carries "
        "between machines. Percentages do not.",
        "",
        "| service | pinned | core-seconds | per million records | mean cores | "
        "peak 1 s | saturation |",
        "|---|---|---|---|---|---|---|",
    ] + [
        "| %s | %s cores | %.1f | %.1f | %.3f | %.3f | %.1f%%%s |" % (
            label, value.get("pinned_cpus", 0), value.get("core_seconds", 0),
            core_seconds_per_million(targets, label, million),
            value.get("mean_cores", 0), value.get("peak_cores_1s", 0),
            value.get("saturation_pct", 0),
            " OVER 80%" if label not in ("fb", "os")
            and value.get("saturation_pct", 0) > 80 else "")
        for label, value in sorted(targets.items())
    ] + [
        "",
        "Saturation is read from `usage_usec`, never from `nr_throttled`: the "
        "rig pins with `cpuset`, which sets no quota, so throttling stays at "
        "zero whatever happens.",
        "",
        "Collector threads (this is where the main loop shows itself):",
        "",
        "| thread name | count | core-seconds | mean cores |",
        "|---|---|---|---|",
    ] + [
        "| %s | %d | %.1f | %.3f |" % (comm, value["threads"],
                                       value["core_seconds"],
                                       value["mean_cores"])
        for comm, value in sorted(threads.items(),
                                  key=lambda item: -item[1]["core_seconds"])
    ] + [
        "",
        "## The run",
        "",
        "| field | value |",
        "|---|---|",
        "| cell | %s (%s) |" % (payload.get("cell") or "unnamed",
                                payload.get("pattern", "STEADY")),
        "| pinning | %s |" % ", ".join(
            "%s=%s" % (name, value)
            for name, value in sorted((payload.get("cpusets") or {}).items())),
        "| profile | %s — %s |" % (payload["profile"], payload["what"]),
        "| started | %s |" % payload["started"],
        "| sink | %s |" % payload["sink"],
        "| knobs | %s |" % payload["knobs"],
        "| memory ceiling | %s (%s) |" % (payload["mem_max"], payload["mem_high"]),
        "| gun mode | %s |" % gun.get("mode"),
        "| emitted | %d records (%.0f/s achieved) |" % (
            emitted, gun.get("achieved_rate", 0)),
        "| Fluent Bit ingested | %d (plus %d re-injected by rewrite_tag) |" % (
            rec.get("ingested_records", 0), rec.get("reinjected_records", 0)),
        "| Fluent Bit output | %d |" % rec.get("output_records", 0),
        "| received by sink | %s |" % (received if received else "not counted"),
        "| undelivered when the run ended | %d (%.4f%%) |" % (
            undelivered, 100.0 * undelivered / ingested if ingested else 0),
        "| queue empty at the end | %s (%s chunks left, drained for %ss) |" % (
            drain_state.get("drained"), drain_state.get("queue_at_end"),
            drain_state.get("seconds")),
        "| lost for good | %d (dropped plus retries that gave up) |" % dropped,
        "| dropped_records | %d |" % rec.get("output_dropped", 0),
        "| retries | %d |" % rec.get("output_retries", 0),
        "| retries_failed | %d |" % rec.get("output_retries_failed", 0),
        "| peak memory | %.1f MB |" % rec.get("peak_memory_mb", 0),
        "| memory.high events | %d |" % rec.get("memory_high_events", 0),
        "| memory.max events | %d |" % rec.get("memory_max_events", 0),
        "| oom kills | %d (docker says OOMKilled=%s) |" % (
            rec.get("oom_kill_events", 0), rec.get("oom_killed")),
        "| peak chunks | %d total, %d up, %d on disk only (cap %s) |" % (
            rec.get("peak_total_chunks", 0), rec.get("peak_fs_chunks_up", 0),
            rec.get("peak_fs_chunks_down", 0), payload.get("max_chunks_up", 64)),
        "| peak buffer on disk | %.1f MB |" % rec.get("peak_storage_mb", 0),
        "| healthy at end | %s |" % rec.get("healthy_at_end"),
        "",
        "Per family (fan-out means these do not sum to the ingested total). "
        "**Loss is per family: round 1 lost every InfoLogger record an outage "
        "touched while the tailed families lost none, so a total hides the "
        "finding.**",
        "",
        "| output | family | delivered | dropped | retries failed | lost for good |",
        "|---|---|---|---|---|---|",
    ] + [
        "| %s | %s | %d | %d | %d | %d |" % (
            name, (families.get(name) or {}).get("family", "?"),
            value["proc_records"],
            value["dropped_records"], value["retries_failed"],
            value["dropped_records"] + value["retries_failed"])
        for name, value in sorted((rec.get("outputs") or {}).items())
    ] + [
        "",
        "Viewers on the lane: %s" % (
            "%d asked, %d connected, %d events, %d bytes"
            % (payload["viewers"].get("asked_viewers", 0),
               payload["viewers"].get("connected_viewers", 0),
               payload["viewers"].get("events_total", 0),
               payload["viewers"].get("bytes_total", 0))
            if payload.get("viewers") else "none"),
        "",
        "Faults fired: %s" % (payload["faults"] or "none"),
        "",
        "Per-second data: soakrec.csv (every service), threads.csv (the "
        "collector's threads) and logburst.csv (the generator).",
    ]
    with open(os.path.join(run_dir, "report.md"), "w") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def cmd_run(args):
    if args.profile not in PROFILES:
        raise SystemExit("soak: unknown profile %s (have %s)" % (
            args.profile, ", ".join(sorted(PROFILES))))
    profile = PROFILES[args.profile]
    sink = args.sink or profile["sink"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS, "%s-%s" % (stamp, args.profile))
    os.makedirs(run_dir, exist_ok=True)
    if not args.run_id:
        args.run_id = "%s%s" % (args.profile, stamp.replace("-", ""))

    say("run directory %s" % run_dir)
    build_fixture(run_dir, args.fixture_count, args.from_logs)

    sink_host = {"opensearch": "opensearch", "cluster": "os-worker"}.get(
        sink, "sink")
    sink_port = 9200
    two_process = args.arm == "t3"
    conf = build_config(run_dir, args, sink, sink_host, sink_port,
                        families="tailed" if two_process else "all")
    conf2 = build_config(run_dir, args, sink, sink_host, sink_port,
                         families="infologger",
                         folder="conf2") if two_process else ""

    env = {
        "SOAK_TOOLS": HERE,
        "SOAK_RUN": run_dir,
        "SOAK_CONF": conf,
        "SOAK_CONF2": conf2 or conf,
        "FB2_HTTP_PORT": str(args.fb2_port),
        "FB_MEM_MAX": args.mem_max,
        "FB_CPUSET": args.fb_cpuset or cpuset_for(args.cpus),
        "OS_CPUSET": args.os_cpuset,
        "SINK_CPUSET": args.sink_cpuset,
        "LANE_CPUSET": args.lane_cpuset,
        "GUN_CPUSET": args.gun_cpuset,
        "FB_VERSION": args.fb_version,
        "SINK_PORT": str(args.sink_port),
        "FB_HTTP_PORT": str(args.fb_port),
        "FB_TCP_PORT": str(args.tcp_port),
        "OS_PORT": str(args.os_port),
        "OS_HEAP": args.os_heap,
        "OS_MEM_MAX": container_ceiling(args.os_heap),
        "OS_STORAGE_HEAP": args.os_storage_heap,
        "OS_STORAGE_MEM_MAX": container_ceiling(args.os_storage_heap),
        "OS_STORAGE_CPUSET": args.os_storage_cpuset,
        "OS_STORAGE1_PORT": str(args.os_storage1_port),
        "OS_STORAGE2_PORT": str(args.os_storage2_port),
        "SINK2_PORT": str(args.sink2_port),
        "SOAK_NODE_ID": args.node_id,
        "SOAK_LIVE_LANE": os.path.join(REPO, "deploy", "roles", "shifter",
                                       "files"),
        "LANE_PORT": str(args.lane_port),
        "VIEWERS_CPUSET": args.viewers_cpuset,
    }
    services = ["sink", "fluent-bit"]
    profiles = []
    if sink == "opensearch":
        services = ["opensearch", "sink", "fluent-bit"]
        profiles = ["os"]
    if sink == "cluster":
        services = ["os-storage1", "os-storage2", "os-worker", "sink",
                    "fluent-bit"]
        profiles = ["cluster"]
    if args.live_lane == "on" and args.live_lane_host == "sink2":
        services = ["sink2"] + services
        profiles = profiles + ["livelane"]
    if args.live_lane == "on" and args.live_lane_host == "lane":
        services = ["lane"] + services
        profiles = profiles + ["reallane"]
    if args.infologger_tap == "file":
        profiles = profiles + ["appender"]

    say("bringing the rig up (%s)" % ", ".join(services))
    compose(env, "up", "-d", "--remove-orphans", *services, profiles=profiles)
    if args.infologger_tap == "file":
        say("InfoLogger tap: the appender owns port 5170 and the collector "
            "tails what it writes")
        compose(env, "up", "-d", "appender", profiles=profiles)

    fb_url = "http://127.0.0.1:%d" % args.fb_port
    if sink in ("opensearch", "cluster"):
        wait_for("http://127.0.0.1:%d/_cluster/health" % args.os_port, 300,
                 "OpenSearch")
    if sink == "cluster":
        wait_for_cluster(args.os_port, 3, 300)
        apply_bootstrap(run_dir, args)
    wait_for(fb_url + "/api/v1/metrics", 90, "Fluent Bit")
    fb_url2 = ""
    if two_process:
        say("second collector: InfoLogger only, inside the same four cores")
        compose(env, "up", "-d", "fluent-bit2", profiles=profiles)
        fb_url2 = "http://127.0.0.1:%d" % args.fb2_port
        wait_for(fb_url2 + "/api/v1/metrics", 90, "second Fluent Bit")
    mem_high_note = set_memory_high("soak-fluent-bit", args.mem_high)
    say(mem_high_note)

    if args.settle:
        say("settling for %ds before the recorder starts — the measured "
            "window excludes it" % args.settle)
    watched = ["fb=soak-fluent-bit", "sink=soak-sink", "gun=~gun"]
    if sink == "cluster":
        watched += ["os=soak-os-worker", "store1=soak-os-storage1",
                    "store2=soak-os-storage2"]
    if args.infologger_tap == "file":
        watched.append("appender=soak-appender")
    if two_process:
        watched.append("fb2=soak-fluent-bit2")
    if sink == "opensearch":
        watched.append("os=soak-opensearch")
    if args.live_lane == "on" and args.live_lane_host == "sink2":
        watched.append("lane=soak-sink2")
    if args.live_lane == "on" and args.live_lane_host == "lane":
        watched.append("lane=soak-lane")
    recorder_argv = [
        sys.executable, os.path.join(HERE, "soakrec.py"),
        "--fb-url", fb_url,
        "--container", "soak-fluent-bit",
        "--storage-path", "/var/log/flb-storage",
        "--storage-volume", "alice-soak_soakstorage",
        "--sink-stats-url", "http://127.0.0.1:%d/__stats" % args.sink_port,
        "--interval", str(args.interval),
        "--csv", os.path.join(run_dir, "soakrec.csv"),
        "--summary", os.path.join(run_dir, "soakrec.json"),
        "--thread-label", "fb",
    ]
    if fb_url2:
        recorder_argv += ["--fb-url2", fb_url2]
        watched.append("fb2=soak-fluent-bit2")
    for spec in watched:
        recorder_argv += ["--watch", spec]

    def start_recorder():
        return subprocess.Popen(recorder_argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

    recorder = None if args.settle else start_recorder()

    faults = []
    sink_ctl = "http://127.0.0.1:%d/__ctl" % args.sink_port
    sink_service = "opensearch" if sink == "opensearch" else "sink"
    schedule = list(profile.get("faults", []))
    if args.fault_at is not None:
        schedule = [(args.fault_at, args.fault_kind),
                    (args.fault_at + args.fault_seconds,
                     "up" if args.fault_kind == "down" else "ok")]
    timers = schedule_faults(schedule, sink_ctl, env, sink_service, faults)

    viewer_thread = None
    if args.viewers > 0 and args.live_lane == "on":
        def run_viewers():
            time.sleep(args.settle)
            compose(env, "run", "--rm", "--no-deps", "viewers",
                    "python3", "/opt/soak/viewers.py",
                    "--url", "http://lane:8092/stream",
                    "--health-url", "http://lane:8092/healthz",
                    "--viewers", str(args.viewers),
                    "--duration", str(args.duration or profile["duration"]),
                    "--summary", "/out/viewers.json",
                    profiles=["viewers"], check=False)
        viewer_thread = threading.Thread(target=run_viewers)
        viewer_thread.daemon = True
        say("%d viewers will attach to the lane once the settle is over"
            % args.viewers)

    argv = gun_command(args, profile, run_dir)
    say("firing the generator: %s" % " ".join(argv[2:]))
    holder = {}

    def launch_recorder_after_settle():
        time.sleep(args.settle)
        holder["recorder"] = start_recorder()

    if args.settle:
        waiter = threading.Thread(target=launch_recorder_after_settle)
        waiter.daemon = True
        waiter.start()
    if viewer_thread is not None:
        viewer_thread.start()
    compose(env, "run", "--rm", "--no-deps", "gun", *argv,
            profiles=["gun"], check=False)
    if viewer_thread is not None:
        viewer_thread.join(timeout=60)
    if args.settle:
        recorder = holder.get("recorder")

    for timer in timers:
        timer.cancel()
    if any(entry.get("action") == "down" for entry in faults) and \
            not any(entry.get("action") == "up" for entry in faults):
        apply_fault("up", sink_ctl, env, sink_service)
    post("%s?mode=ok" % sink_ctl)
    drain_state = drain(fb_url, args.drain)
    say("drain: %s" % json.dumps(drain_state))

    stop_marker = os.path.join(run_dir, "soakrec.stop")
    open(stop_marker, "w").close()
    if recorder is not None:
        try:
            recorder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            recorder.terminate()
    os.unlink(stop_marker)

    received = 0
    if sink in ("opensearch", "cluster"):
        run(["curl", "-s", "-XPOST",
             "http://127.0.0.1:%d/%s/_refresh?ignore_unavailable=true"
             % (args.os_port, LOG_INDICES)], check=False, capture=True)
        received = opensearch_count(args.os_port)
    else:
        stats = get_json("http://127.0.0.1:%d/__stats" % args.sink_port)
        received = stats.get("docs", 0) if isinstance(stats, dict) else 0

    def load(name, default):
        path = os.path.join(run_dir, name)
        try:
            with open(path) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default

    payload = {
        "run_id": args.run_id,
        "profile": args.profile,
        "what": profile["what"],
        "started": stamp,
        "sink": sink,
        "mem_max": args.mem_max,
        "mem_high": mem_high_note,
        "knobs": ("flush=%s max_chunks_up=%s storage=%s pause_on_overlimit=%s "
                  "total_limit_size=%s retry_limit=%s lua=%s workers=%s "
                  "health=%s live_lane=%s mem_buf_limit=%s backlog_mem_limit=%s "
                  "compress=%s arm=%s lane_own_tag=%s il_tap=%s") % (
            args.flush, args.max_chunks_up, args.storage_type,
            args.pause_on_overlimit, args.total_limit_size, args.retry_limit,
            args.lua, args.output_workers, args.health, args.live_lane,
            args.mem_buf_limit or "unset", args.backlog_mem_limit or "unset",
            args.compress or "off", args.arm, args.lane_own_tag,
            args.infologger_tap),
        "cell": args.cell,
        "pattern": args.pattern,
        "max_chunks_up": args.max_chunks_up,
        "cpusets": {"collector": env["FB_CPUSET"], "opensearch": env["OS_CPUSET"],
                    "sink": env["SINK_CPUSET"], "lane": env["LANE_CPUSET"],
                    "generator": env["GUN_CPUSET"]},
        "viewers": load("viewers.json", {}),
        "gun": load("logburst-summary.json", {}),
        "recorder": load("soakrec.json", {}),
        "received": received,
        "drain": drain_state,
        "faults": faults,
    }
    payload["families"] = output_families(run_dir)
    payload["gate_zero"] = gate_zero(run_dir, args.gate_tolerance,
                                     settle=args.settle)
    payload["gate_zero"]["selftest"] = selftest_evidence(
        float(args.rate or PROFILES[args.profile].get("gun", {}).get("rate", 0) or 0))
    payload["settle_seconds"] = args.settle
    payload["verdict"] = verdict(payload)
    write_report(run_dir, payload)

    if args.keep:
        say("rig left running; stop it with: python3 tools/soak/soak.py down")
    else:
        compose(env, "down", "-v", "--remove-orphans",
                profiles=["os", "cluster", "gun", "livelane", "reallane",
                          "appender", "twoproc", "viewers"],
                check=False)
    return 0


def cmd_selftest(args):
    """Measure the generator's own ceiling, at every rate the plan uses.

    Gate zero depends on this. A generator that quietly falls short at 20,000
    records a second produces a cell where delivered equals ingested and the
    queue is flat, and the whole thing measured 14,000 — a pass, and a lie.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS, "%s-selftest" % stamp)
    os.makedirs(run_dir, exist_ok=True)
    build_fixture(run_dir, args.fixture_count, "")

    env = {"SOAK_TOOLS": HERE, "SOAK_RUN": run_dir, "SOAK_CONF": run_dir,
           "GUN_CPUSET": args.gun_cpuset}
    results = []
    for rate in [float(value) for value in args.rates.split(",")]:
        say("selftest at %.0f records a second, pinned to %s"
            % (rate, args.gun_cpuset))
        summary_path = os.path.join(run_dir, "selftest-%d.json" % int(rate))
        compose(env, "run", "--rm", "--no-deps", "gunsolo",
                "python3", "/opt/soak/logburst.py",
                "--fixture", "/out/fixture",
                "--mode", "selftest",
                "--rate", str(rate),
                "--duration", str(args.duration),
                "--workers", str(args.gun_workers),
                "--summary", "/out/%s" % os.path.basename(summary_path),
                profiles=["gunsolo"], check=False)
        try:
            with open(summary_path) as handle:
                summary = json.load(handle)
        except (OSError, ValueError):
            say("selftest at %.0f produced no summary" % rate)
            continue
        results.append({"asked": rate, "achieved_rate": summary["achieved_rate"],
                        "workers": summary["workers"],
                        "seconds": summary["seconds"],
                        "shortfall_pct": round(
                            100.0 * (summary["achieved_rate"] - rate) / rate, 2)})
        say("asked %.0f/s, reached %.0f/s" % (rate, summary["achieved_rate"]))

    if args.ceiling_rate:
        say("ceiling probe: asking for %.0f records a second, which it will "
            "not reach — what it does reach is the ceiling" % args.ceiling_rate)
        summary_path = os.path.join(run_dir, "selftest-ceiling.json")
        compose(env, "run", "--rm", "--no-deps", "gunsolo",
                "python3", "/opt/soak/logburst.py",
                "--fixture", "/out/fixture",
                "--mode", "selftest",
                "--rate", str(args.ceiling_rate),
                "--duration", str(args.duration),
                "--workers", str(args.gun_workers),
                "--summary", "/out/selftest-ceiling.json",
                profiles=["gunsolo"], check=False)
        try:
            with open(summary_path) as handle:
                summary = json.load(handle)
            results.append({"asked": args.ceiling_rate, "ceiling": True,
                            "achieved_rate": summary["achieved_rate"],
                            "workers": summary["workers"],
                            "seconds": summary["seconds"],
                            "shortfall_pct": round(
                                100.0 * (summary["achieved_rate"] - args.ceiling_rate)
                                / args.ceiling_rate, 2)})
            say("ceiling is %.0f records a second on %s"
                % (summary["achieved_rate"], args.gun_cpuset))
        except (OSError, ValueError):
            say("ceiling probe produced no summary")

    payload = {"when": stamp, "cpuset": args.gun_cpuset,
               "workers": args.gun_workers, "runs": results}
    with open(SELFTEST_FILE, "w") as handle:
        json.dump(payload, handle, indent=2)
    compose(env, "down", "-v", "--remove-orphans", profiles=["gunsolo"],
            check=False)
    print(json.dumps(payload, indent=2))
    say("written to %s — gate zero reads it" % SELFTEST_FILE)
    return 0


def cmd_down(args):
    env = {"SOAK_TOOLS": HERE, "SOAK_RUN": RUNS, "SOAK_CONF": RUNS}
    compose(env, "down", "-v", "--remove-orphans",
            profiles=["os", "cluster", "gun", "gunsolo", "livelane",
                      "appender", "twoproc"],
            check=False)
    return 0


def cmd_list(args):
    for name in sorted(PROFILES):
        print("%-6s %s" % (name, PROFILES[name]["what"]))
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    runner = sub.add_parser("run")
    runner.add_argument("profile")
    runner.add_argument("--sink", default="",
                        choices=["", "null", "http", "opensearch", "cluster"])
    runner.add_argument("--mode", default="")
    runner.add_argument("--duration", type=float, default=0)
    runner.add_argument("--rate", type=float)
    runner.add_argument("--start", type=float)
    runner.add_argument("--step", type=float)
    runner.add_argument("--step-seconds", type=float)
    runner.add_argument("--max", type=float)
    runner.add_argument("--base", type=float)
    runner.add_argument("--peak", type=float)
    runner.add_argument("--on", type=float)
    runner.add_argument("--off", type=float)
    runner.add_argument("--mix", default="dds=2,stdout=2,infologger=6")
    runner.add_argument("--hosts", type=int, default=10)
    runner.add_argument("--gun-workers", type=int, default=2)
    runner.add_argument("--fixture-count", type=int, default=20000)
    runner.add_argument("--from-logs", default="")
    runner.add_argument("--run-id", default="")
    runner.add_argument("--flush", type=float, default=5)
    runner.add_argument("--max-chunks-up", type=int, default=64)
    runner.add_argument("--storage-type", default="filesystem",
                        choices=["filesystem", "memory"])
    runner.add_argument("--pause-on-overlimit", default="off",
                        choices=["on", "off"])
    runner.add_argument("--mem-buf-limit", default="")
    runner.add_argument("--backlog-mem-limit", default="")
    runner.add_argument("--total-limit-size", default="256M")
    runner.add_argument("--retry-limit", type=int, default=10)
    runner.add_argument("--output-workers", type=int, default=-1)
    runner.add_argument("--compress", default="")
    runner.add_argument("--lua", default="on", choices=["on", "off"])
    runner.add_argument("--health", default="off", choices=["on", "off"])
    runner.add_argument("--live-lane", default="off", choices=["on", "off"])
    runner.add_argument("--live-lane-host", default="sink")
    runner.add_argument("--live-lane-port", type=int, default=9200)
    runner.add_argument("--lane-port", type=int, default=8092)
    runner.add_argument("--viewers", type=int, default=0,
                        help="the lv cells: concurrent viewers on the lane, "
                             "pinned away from both the lane and the worker")
    runner.add_argument("--viewers-cpuset", default="4-7")
    runner.add_argument("--log-level", default="info")
    runner.add_argument("--arm", default="t0",
                        choices=["t0", "t1", "t2", "t3"])
    runner.add_argument("--lane-own-tag", default="off", choices=["on", "off"])
    runner.add_argument("--infologger-tap", default="tcp",
                        choices=["tcp", "file"])
    runner.add_argument("--mem-max", default="768m")
    runner.add_argument("--mem-high", default="384M")
    runner.add_argument("--cpus", type=float, default=4,
                        help="collector cores, turned into a pinned set of "
                             "the first N. --fb-cpuset overrides it.")
    runner.add_argument("--fb-cpuset", default="")
    runner.add_argument("--os-cpuset", default="0-3")
    runner.add_argument("--sink-cpuset", default="10-11")
    runner.add_argument("--lane-cpuset", default="10-11")
    runner.add_argument("--gun-cpuset", default="8-9")
    runner.add_argument("--gate-tolerance", type=float, default=2.0)
    runner.add_argument("--settle", type=float, default=0,
                        help="seconds of load before the recorder starts. The "
                             "measured window excludes it, and so does gate zero.")
    runner.add_argument("--cell", default="",
                        help="cell name from the manifest, written into the "
                             "report so a run can be found again")
    runner.add_argument("--pattern", default="STEADY",
                        choices=["STEADY", "OVER", "BURST", "OUTAGE"],
                        help="a 50,000/s cell is not a pass or fail cell; "
                             "this says which of the two tests it is")
    runner.add_argument("--fb-version", default="5.0.8")
    runner.add_argument("--fb-port", type=int, default=2020)
    runner.add_argument("--fb2-port", type=int, default=2021)
    runner.add_argument("--tcp-port", type=int, default=5170)
    runner.add_argument("--sink-port", type=int, default=9201)
    runner.add_argument("--sink2-port", type=int, default=9203)
    runner.add_argument("--os-port", type=int, default=9202)
    runner.add_argument("--os-buffer-size", default="")
    runner.add_argument("--lane-compress", default="",
                        choices=["", "off", "gzip", "zstd", "snappy"])
    runner.add_argument("--os-heap", default="1g")
    runner.add_argument("--os-storage-heap", default="2g")
    runner.add_argument("--os-storage-cpuset", default="4-7")
    runner.add_argument("--os-storage1-port", type=int, default=9204)
    runner.add_argument("--os-storage2-port", type=int, default=9205)
    runner.add_argument("--node-id", default="node-soak",
                        help="must match ALICE_NODE_ID in the rig, or the "
                             "collector writes to an index the bootstrap "
                             "never made")
    runner.add_argument("--replicas", type=int, default=1,
                        help="the rig has two storage nodes, so one replica "
                             "where production asks for two")
    runner.add_argument("--interval", type=float, default=1.0)
    runner.add_argument("--drain", type=float, default=600)
    runner.add_argument("--max-file-bytes", type=int, default=256 * 1024 * 1024)
    runner.add_argument("--disk-guard-pct", type=float, default=85.0)
    runner.add_argument("--fault-at", type=float, default=None)
    runner.add_argument("--fault-seconds", type=float, default=300)
    runner.add_argument("--fault-kind", default="down",
                        choices=["down", "stall", "429", "500"])
    runner.add_argument("--keep", action="store_true")
    runner.set_defaults(func=cmd_run)

    tester = sub.add_parser("selftest")
    tester.add_argument("--rates", default="1000,20000,50000")
    tester.add_argument("--duration", type=float, default=60)
    tester.add_argument("--gun-workers", type=int, default=2)
    tester.add_argument("--gun-cpuset", default="8-9")
    tester.add_argument("--fixture-count", type=int, default=20000)
    tester.add_argument("--ceiling-rate", type=float, default=400000)
    tester.set_defaults(func=cmd_selftest)

    down = sub.add_parser("down")
    down.set_defaults(func=cmd_down)

    listing = sub.add_parser("profiles")
    listing.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if not shutil.which("docker"):
        raise SystemExit("soak: docker is not on PATH")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
