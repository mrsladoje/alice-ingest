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


def build_config(run_dir, args, sink, sink_host, sink_port):
    conf = os.path.join(run_dir, "conf")
    os.makedirs(conf, exist_ok=True)
    argv = [sys.executable, os.path.join(HERE, "mkconfig.py"),
            "--out", os.path.join(conf, "collector.yaml"),
            "--parsers-out", os.path.join(conf, "parsers.yaml"),
            "--sink", sink, "--sink-host", sink_host,
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
            "--log-level", args.log_level]
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
            "--duration", str(args.duration or profile["duration"]),
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


LOG_INDICES = "infologger*,application-logs-*"


def opensearch_count(port):
    base = "http://127.0.0.1:%d/%s" % (port, LOG_INDICES)
    doc = get_json(base + "/_count?ignore_unavailable=true", timeout=30)
    return doc.get("count", 0) if isinstance(doc, dict) else 0


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
    lines = [
        "# soak run %s" % payload["run_id"],
        "",
        "| field | value |",
        "|---|---|",
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
        "| peak chunks | %d total, %d on disk only |" % (
            rec.get("peak_total_chunks", 0), rec.get("peak_fs_chunks_down", 0)),
        "| peak buffer on disk | %.1f MB |" % rec.get("peak_storage_mb", 0),
        "| healthy at end | %s |" % rec.get("healthy_at_end"),
        "",
        "Per output (fan-out means these do not sum to the ingested total):",
        "",
        "| output | delivered | dropped | retries failed |",
        "|---|---|---|---|",
    ] + [
        "| %s | %d | %d | %d |" % (name, value["proc_records"],
                                   value["dropped_records"],
                                   value["retries_failed"])
        for name, value in sorted((rec.get("outputs") or {}).items())
    ] + [
        "",
        "Faults fired: %s" % (payload["faults"] or "none"),
        "",
        "Per-second data: soakrec.csv (collector) and logburst.csv (generator).",
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

    sink_host = "opensearch" if sink == "opensearch" else "sink"
    sink_port = 9200
    conf = build_config(run_dir, args, sink, sink_host, sink_port)

    env = {
        "SOAK_TOOLS": HERE,
        "SOAK_RUN": run_dir,
        "SOAK_CONF": conf,
        "FB_MEM_MAX": args.mem_max,
        "FB_CPUS": str(args.cpus),
        "FB_VERSION": args.fb_version,
        "SINK_PORT": str(args.sink_port),
        "FB_HTTP_PORT": str(args.fb_port),
        "FB_TCP_PORT": str(args.tcp_port),
        "OS_PORT": str(args.os_port),
        "OS_HEAP": args.os_heap,
        "SINK2_PORT": str(args.sink2_port),
    }
    services = ["sink", "fluent-bit"]
    profiles = []
    if sink == "opensearch":
        services = ["opensearch", "sink", "fluent-bit"]
        profiles = ["os"]
    if args.live_lane == "on" and args.live_lane_host == "sink2":
        services = ["sink2"] + services
        profiles = profiles + ["livelane"]

    say("bringing the rig up (%s)" % ", ".join(services))
    compose(env, "up", "-d", "--remove-orphans", *services, profiles=profiles)

    fb_url = "http://127.0.0.1:%d" % args.fb_port
    if sink == "opensearch":
        wait_for("http://127.0.0.1:%d/_cluster/health" % args.os_port, 180,
                 "OpenSearch")
    wait_for(fb_url + "/api/v1/metrics", 90, "Fluent Bit")
    mem_high_note = set_memory_high("soak-fluent-bit", args.mem_high)
    say(mem_high_note)

    recorder = subprocess.Popen([
        sys.executable, os.path.join(HERE, "soakrec.py"),
        "--fb-url", fb_url,
        "--container", "soak-fluent-bit",
        "--storage-path", "/var/log/flb-storage",
        "--storage-volume", "alice-soak_soakstorage",
        "--sink-stats-url", "http://127.0.0.1:%d/__stats" % args.sink_port,
        "--interval", str(args.interval),
        "--csv", os.path.join(run_dir, "soakrec.csv"),
        "--summary", os.path.join(run_dir, "soakrec.json"),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    faults = []
    sink_ctl = "http://127.0.0.1:%d/__ctl" % args.sink_port
    sink_service = "opensearch" if sink == "opensearch" else "sink"
    schedule = list(profile.get("faults", []))
    if args.fault_at is not None:
        schedule = [(args.fault_at, args.fault_kind),
                    (args.fault_at + args.fault_seconds,
                     "up" if args.fault_kind == "down" else "ok")]
    timers = schedule_faults(schedule, sink_ctl, env, sink_service, faults)

    argv = gun_command(args, profile, run_dir)
    say("firing the generator: %s" % " ".join(argv[2:]))
    compose(env, "run", "--rm", "--no-deps", "gun", *argv,
            profiles=["gun"], check=False)

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
    try:
        recorder.wait(timeout=30)
    except subprocess.TimeoutExpired:
        recorder.terminate()
    os.unlink(stop_marker)

    received = 0
    if sink == "opensearch":
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
                  "compress=%s") % (
            args.flush, args.max_chunks_up, args.storage_type,
            args.pause_on_overlimit, args.total_limit_size, args.retry_limit,
            args.lua, args.output_workers, args.health, args.live_lane,
            args.mem_buf_limit or "unset", args.backlog_mem_limit or "unset",
            args.compress or "off"),
        "gun": load("logburst-summary.json", {}),
        "recorder": load("soakrec.json", {}),
        "received": received,
        "drain": drain_state,
        "faults": faults,
    }
    write_report(run_dir, payload)

    if args.keep:
        say("rig left running; stop it with: python3 tools/soak/soak.py down")
    else:
        compose(env, "down", "-v", "--remove-orphans",
                profiles=["os", "gun", "livelane"], check=False)
    return 0


def cmd_down(args):
    env = {"SOAK_TOOLS": HERE, "SOAK_RUN": RUNS, "SOAK_CONF": RUNS}
    compose(env, "down", "-v", "--remove-orphans", profiles=["os", "gun"],
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
    runner.add_argument("--sink", default="")
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
    runner.add_argument("--log-level", default="info")
    runner.add_argument("--mem-max", default="768m")
    runner.add_argument("--mem-high", default="384M")
    runner.add_argument("--cpus", type=float, default=2)
    runner.add_argument("--fb-version", default="5.0.8")
    runner.add_argument("--fb-port", type=int, default=2020)
    runner.add_argument("--tcp-port", type=int, default=5170)
    runner.add_argument("--sink-port", type=int, default=9201)
    runner.add_argument("--sink2-port", type=int, default=9203)
    runner.add_argument("--os-port", type=int, default=9202)
    runner.add_argument("--os-heap", default="1g")
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
