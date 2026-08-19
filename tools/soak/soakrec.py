#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CGROUP_CANDIDATES = [
    "/sys/fs/cgroup/system.slice/docker-%s.scope",
    "/sys/fs/cgroup/docker/%s",
    "/sys/fs/cgroup/system.slice/containerd-%s.scope",
]
INGEST_INPUTS = ("tail", "tcp", "exec", "http", "forward", "dummy", "systemd")
COLUMNS = [
    "t", "wall", "in_records", "in_ingest", "in_emitter", "out_records", "out_errors", "out_retries",
    "out_retries_failed", "out_dropped", "total_chunks", "mem_chunks",
    "fs_chunks", "fs_chunks_up", "fs_chunks_down", "overlimit_inputs",
    "mem_current", "mem_peak", "ev_high", "ev_max", "ev_oom", "storage_bytes",
    "healthy", "sink_docs",
]


def fetch(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except Exception:
        return 0, b""


def fetch_json(url, timeout=3):
    status, body = fetch(url, timeout)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def colima_profile():
    code, out = run(["colima", "list", "--json"], timeout=20)
    if code != 0:
        return ""
    for line in out.split("\n"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("status") == "Running":
            return entry.get("name", "")
    return ""


def colima_ssh(script, timeout=25):
    profile = colima_profile()
    argv = ["colima", "ssh"]
    if profile and profile != "default":
        argv += ["--profile", profile]
    argv += ["--", "sh", "-c", script]
    return run(argv, timeout=timeout)


def run(argv, timeout=15):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except Exception:
        return 1, ""
    return proc.returncode, proc.stdout.strip()


class MemorySource:
    def __init__(self, container, cgroup, source):
        self.container = container
        self.cgroup = cgroup
        self.source = source
        self.container_id = ""
        self.vm_path = ""
        if container:
            code, out = run(["docker", "inspect", "-f", "{{.Id}}", container])
            if code == 0:
                self.container_id = out.strip()
        if self.source == "auto":
            self.source = self._detect()

    def _detect(self):
        if self.cgroup and os.path.isdir(self.cgroup):
            return "native"
        if self.container_id:
            code, out = run(["docker", "exec", self.container, "sh", "-c",
                             "cat /sys/fs/cgroup/memory.current"])
            if code == 0 and out.strip().isdigit():
                return "exec"
            for pattern in CGROUP_CANDIDATES:
                path = pattern % self.container_id
                code, out = colima_ssh("cat %s/memory.current" % path)
                if code == 0 and out.strip().isdigit():
                    self.vm_path = path
                    return "vmssh"
            return "stats"
        return "none"

    def _parse(self, text):
        parts = text.split("\n")
        values = {"mem_current": 0, "mem_peak": 0,
                  "ev_high": 0, "ev_max": 0, "ev_oom": 0}
        numbers = []
        for line in parts:
            line = line.strip()
            if not line:
                continue
            if line.isdigit() or line == "max":
                numbers.append(line)
                continue
            key, _, value = line.partition(" ")
            if key == "high":
                values["ev_high"] = int(value or 0)
            elif key == "max":
                values["ev_max"] = int(value or 0)
            elif key == "oom_kill":
                values["ev_oom"] = int(value or 0)
        if numbers:
            values["mem_current"] = int(numbers[0]) if numbers[0].isdigit() else 0
        if len(numbers) > 1:
            values["mem_peak"] = int(numbers[1]) if numbers[1].isdigit() else 0
        return values

    def sample(self):
        empty = {"mem_current": 0, "mem_peak": 0, "ev_high": 0,
                 "ev_max": 0, "ev_oom": 0}
        script = ("cat memory.current 2>/dev/null; cat memory.peak 2>/dev/null; "
                  "cat memory.events 2>/dev/null")
        if self.source == "native":
            out = []
            for name in ("memory.current", "memory.peak", "memory.events"):
                try:
                    with open(os.path.join(self.cgroup, name)) as handle:
                        out.append(handle.read().strip())
                except OSError:
                    pass
            return self._parse("\n".join(out))
        if self.source == "exec":
            code, out = run(["docker", "exec", self.container, "sh", "-c",
                             "cd /sys/fs/cgroup && " + script])
            return self._parse(out) if code == 0 else empty
        if self.source == "vmssh":
            code, out = colima_ssh("cd %s && %s" % (self.vm_path, script))
            return self._parse(out) if code == 0 else empty
        if self.source == "stats":
            code, out = run(["docker", "stats", "--no-stream", "--format",
                             "{{.MemUsage}}", self.container])
            if code != 0 or "/" not in out:
                return empty
            used = out.split("/")[0].strip()
            factor = 1
            for suffix, scale in (("GiB", 1 << 30), ("MiB", 1 << 20),
                                  ("KiB", 1 << 10), ("B", 1)):
                if used.endswith(suffix):
                    factor = scale
                    used = used[: -len(suffix)]
                    break
            try:
                empty["mem_current"] = int(float(used) * factor)
            except ValueError:
                pass
            return empty
        return empty


def _kilobytes(text):
    digits = text.strip().split("\n")[0].split("\t")[0].strip()
    return int(digits) * 1024 if digits.isdigit() else 0


def storage_bytes(container, path, volume, cache):
    if path and os.path.isdir(path):
        total = 0
        for root, _, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total
    if container and path:
        code, out = run(["docker", "exec", container, "sh", "-c",
                         "du -sk %s 2>/dev/null" % path])
        if code == 0 and out.strip():
            return _kilobytes(out)
    if volume:
        if "mount" not in cache:
            cache["mount"] = run(
                ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", volume])[1]
        mount = cache["mount"]
        if mount:
            code, out = colima_ssh("sudo du -sk %s 2>/dev/null" % mount)
            if code == 0 and out.strip():
                return _kilobytes(out)
    return 0


def oom_killed(container):
    if not container:
        return False
    code, out = run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", container])
    return code == 0 and out.strip() == "true"


def read_metrics(base):
    doc = fetch_json(base + "/api/v1/metrics")
    result = {"in_records": 0, "in_ingest": 0, "in_emitter": 0,
              "out_records": 0, "out_errors": 0, "out_retries": 0,
              "out_retries_failed": 0, "out_dropped": 0}
    if not isinstance(doc, dict):
        return result
    for name, value in (doc.get("input") or {}).items():
        records = value.get("records", 0)
        result["in_records"] += records
        plugin = name.rpartition(".")[0] or name
        if plugin in INGEST_INPUTS and name.rpartition(".")[2].isdigit():
            result["in_ingest"] += records
        elif plugin == "storage_backlog":
            continue
        else:
            result["in_emitter"] += records
    for value in (doc.get("output") or {}).values():
        result["out_records"] += value.get("proc_records", 0)
        result["out_errors"] += value.get("errors", 0)
        result["out_retries"] += value.get("retries", 0)
        result["out_retries_failed"] += value.get("retries_failed", 0)
        result["out_dropped"] += value.get("dropped_records", 0)
    return result


def per_output(base):
    doc = fetch_json(base + "/api/v1/metrics")
    result = {}
    if not isinstance(doc, dict):
        return result
    for name, value in (doc.get("output") or {}).items():
        result[name] = {
            "proc_records": value.get("proc_records", 0),
            "errors": value.get("errors", 0),
            "retries": value.get("retries", 0),
            "retries_failed": value.get("retries_failed", 0),
            "dropped_records": value.get("dropped_records", 0),
        }
    return result


def read_storage(base):
    doc = fetch_json(base + "/api/v1/storage")
    result = {"total_chunks": 0, "mem_chunks": 0, "fs_chunks": 0,
              "fs_chunks_up": 0, "fs_chunks_down": 0, "overlimit_inputs": 0}
    if not isinstance(doc, dict):
        return result
    layer = (doc.get("storage_layer") or {}).get("chunks") or {}
    for key in ("total_chunks", "mem_chunks", "fs_chunks",
                "fs_chunks_up", "fs_chunks_down"):
        result[key] = layer.get(key, 0)
    for value in (doc.get("input_chunks") or {}).values():
        status = value.get("status") or {}
        if status.get("overlimit"):
            result["overlimit_inputs"] += 1
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fb-url", default="http://127.0.0.1:2020")
    parser.add_argument("--container", default="")
    parser.add_argument("--cgroup", default="")
    parser.add_argument("--mem-source", default="auto",
                        choices=["auto", "native", "exec", "vmssh", "stats", "none"])
    parser.add_argument("--storage-path", default="/var/log/flb-storage")
    parser.add_argument("--storage-volume", default="")
    parser.add_argument("--storage-every", type=int, default=10)
    parser.add_argument("--sink-stats-url", default="")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--csv", default="")
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    memory = MemorySource(args.container, args.cgroup, args.mem_source)
    handle = open(args.csv, "w") if args.csv else None
    if handle:
        handle.write(",".join(COLUMNS) + "\n")

    start = time.time()
    peak = {"mem": 0, "chunks": 0, "fs_down": 0, "storage": 0}
    last = {}
    storage_size = 0
    storage_cache = {}
    tick = 0
    stop_path = os.path.join(os.path.dirname(args.csv or "."), "soakrec.stop")

    try:
        while True:
            now = time.time()
            elapsed = now - start
            if args.duration and elapsed >= args.duration:
                break
            if os.path.exists(stop_path):
                break

            row = {"t": round(elapsed, 1), "wall": round(now, 1)}
            row.update(read_metrics(args.fb_url))
            row.update(read_storage(args.fb_url))
            row.update(memory.sample())
            if tick % max(1, args.storage_every) == 0:
                storage_size = storage_bytes(
                    args.container, args.storage_path, args.storage_volume,
                    storage_cache)
            row["storage_bytes"] = storage_size
            status, _ = fetch(args.fb_url + "/api/v2/health")
            row["healthy"] = 1 if status == 200 else 0
            row["sink_docs"] = 0
            if args.sink_stats_url:
                stats = fetch_json(args.sink_stats_url)
                if isinstance(stats, dict):
                    row["sink_docs"] = stats.get("docs", 0)

            peak["mem"] = max(peak["mem"], row["mem_current"], row["mem_peak"])
            peak["chunks"] = max(peak["chunks"], row["total_chunks"])
            peak["fs_down"] = max(peak["fs_down"], row["fs_chunks_down"])
            peak["storage"] = max(peak["storage"], row["storage_bytes"])
            last = row

            if handle:
                handle.write(",".join(str(row.get(name, 0)) for name in COLUMNS) + "\n")
                handle.flush()

            tick += 1
            sleep_for = start + tick * args.interval - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass

    if handle:
        handle.close()

    summary = {
        "seconds": round(time.time() - start, 1),
        "memory_source": memory.source,
        "peak_memory_bytes": peak["mem"],
        "peak_memory_mb": round(peak["mem"] / 1e6, 1),
        "peak_total_chunks": peak["chunks"],
        "peak_fs_chunks_down": peak["fs_down"],
        "peak_storage_bytes": peak["storage"],
        "peak_storage_mb": round(peak["storage"] / 1e6, 1),
        "input_records": last.get("in_records", 0),
        "ingested_records": last.get("in_ingest", 0),
        "reinjected_records": last.get("in_emitter", 0),
        "output_records": last.get("out_records", 0),
        "output_errors": last.get("out_errors", 0),
        "output_retries": last.get("out_retries", 0),
        "output_retries_failed": last.get("out_retries_failed", 0),
        "output_dropped": last.get("out_dropped", 0),
        "memory_high_events": last.get("ev_high", 0),
        "memory_max_events": last.get("ev_max", 0),
        "oom_kill_events": last.get("ev_oom", 0),
        "oom_killed": oom_killed(args.container),
        "sink_docs": last.get("sink_docs", 0),
        "healthy_at_end": last.get("healthy", 0),
        "outputs": per_output(args.fb_url),
    }
    if args.summary:
        with open(args.summary, "w") as out:
            json.dump(summary, out, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
