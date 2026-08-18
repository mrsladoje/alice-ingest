#!/usr/bin/env python3

import argparse
import json
import multiprocessing
import os
import random
import shutil
import signal
import socket
import sys
import time
from datetime import datetime, timezone

TS_SENTINEL = "__SOAK_TS__"
FAMILIES = ("dds", "stdout", "infologger")


def load_fixture(path):
    with open(os.path.join(path, "dds.bodies"), "r") as handle:
        dds = [line.rstrip("\n") for line in handle if line.strip()]
    with open(os.path.join(path, "stdout.bodies"), "r") as handle:
        stdout = [line.rstrip("\n") for line in handle if line.strip()]
    il = []
    with open(os.path.join(path, "infologger.json"), "r") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            message = record.pop("message", "")
            record["timestamp"] = TS_SENTINEL
            body = json.dumps(record)[:-1]
            left, right = body.split('"%s"' % TS_SENTINEL, 1)
            il.append((left, right, json.dumps(message)[:-1]))
    if not dds or not stdout or not il:
        raise SystemExit("logburst: fixture is incomplete, rebuild it with mkfixture.py")
    return {"dds": dds, "stdout": stdout, "infologger": il}


def parse_mix(text):
    weights = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name not in FAMILIES:
            raise SystemExit("logburst: unknown family in --mix: %s" % name)
        weights[name] = float(value)
    total = sum(weights.values())
    if total <= 0:
        raise SystemExit("logburst: --mix weights must add up to more than zero")
    return {name: weights.get(name, 0.0) / total for name in FAMILIES}


def rate_at(args, elapsed):
    if args.mode == "steady" or args.mode == "selftest":
        return args.rate
    if args.mode == "ramp":
        step = int(elapsed // args.step_seconds)
        return min(args.start + step * args.step, args.max)
    if args.mode == "burst":
        period = args.on + args.off
        if period <= 0:
            return args.peak
        return args.peak if (elapsed % period) < args.on else args.base
    raise SystemExit("logburst: unknown mode %s" % args.mode)


def disk_free_pct(path):
    usage = shutil.disk_usage(path)
    return 100.0 * usage.used / max(1, usage.total)


class TailWriter:
    def __init__(self, root, family, hosts, max_bytes, rotate_wait):
        self.dir = os.path.join(root, family)
        os.makedirs(self.dir, exist_ok=True)
        self.max_bytes = max_bytes
        self.rotate_wait = rotate_wait
        self.hosts = hosts
        self.handles = {}
        self.sizes = {}
        self.rotated = []
        for host in hosts:
            self._open(host)

    def _open(self, host):
        path = os.path.join(self.dir, "%s.log" % host)
        handle = open(path, "a", buffering=1 << 20)
        self.handles[host] = handle
        self.sizes[host] = handle.tell()

    def write(self, host, payload):
        handle = self.handles[host]
        handle.write(payload)
        self.sizes[host] += len(payload)
        if self.sizes[host] >= self.max_bytes:
            self._rotate(host)

    def _rotate(self, host):
        handle = self.handles.pop(host)
        handle.flush()
        handle.close()
        path = os.path.join(self.dir, "%s.log" % host)
        target = "%s.rot.%d" % (path, int(time.time()))
        os.rename(path, target)
        self.rotated.append((time.time(), target))
        self._open(host)

    def tick(self):
        for handle in self.handles.values():
            handle.flush()
        now = time.time()
        keep = []
        for stamp, path in self.rotated:
            if now - stamp > self.rotate_wait:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            else:
                keep.append((stamp, path))
        self.rotated = keep

    def close(self):
        for handle in self.handles.values():
            handle.flush()
            handle.close()


class NullWriter:
    def __init__(self):
        self.bytes = 0

    def write(self, host, payload):
        self.bytes += len(payload)

    def tick(self):
        pass

    def close(self):
        pass


class InfoLoggerSender:
    def __init__(self, host, port):
        self.address = (host, port)
        self.sock = None
        self.failures = 0

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(self.address)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = sock

    def send(self, payload):
        data = payload.encode()
        for _ in range(3):
            try:
                if self.sock is None:
                    self.connect()
                self.sock.sendall(data)
                return True
            except OSError:
                self.failures += 1
                try:
                    if self.sock is not None:
                        self.sock.close()
                except OSError:
                    pass
                self.sock = None
                time.sleep(0.2)
        return False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


def worker(args, worker_id, hosts, share, result_path):
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    random.seed(args.seed + worker_id)
    fixture = load_fixture(args.fixture)
    mix = parse_mix(args.mix)

    if args.mode == "selftest" or not args.tail_root:
        dds_writer = NullWriter()
        stdout_writer = NullWriter()
    else:
        dds_writer = TailWriter(
            args.tail_root, "dds", hosts, args.max_file_bytes, args.rotate_wait)
        stdout_writer = TailWriter(
            args.tail_root, "stdout", hosts, args.max_file_bytes, args.rotate_wait)

    sender = None
    if args.mode != "selftest" and args.il_port > 0 and mix["infologger"] > 0:
        sender = InfoLoggerSender(args.il_host, args.il_port)

    dds_pool, stdout_pool, il_pool = (
        fixture["dds"], fixture["stdout"], fixture["infologger"])
    dds_n, stdout_n, il_n = len(dds_pool), len(stdout_pool), len(il_pool)

    emitted = {"dds": 0, "stdout": 0, "infologger": 0}
    bytes_out = 0
    seq = worker_id
    stride = args.workers
    carry = {"dds": 0.0, "stdout": 0.0, "infologger": 0.0}
    rows = []
    aborted = ""

    tick = args.tick
    start = time.perf_counter()
    wall_start = time.time()
    next_tick = start
    last_second = 0
    second_counts = {"dds": 0, "stdout": 0, "infologger": 0}
    second_bytes = 0
    guard_countdown = 0

    while True:
        now = time.perf_counter()
        elapsed = now - start
        if elapsed >= args.duration:
            break
        target = rate_at(args, elapsed) * share
        stamp = datetime.now(timezone.utc)
        text_ts = stamp.strftime("%Y-%m-%d %H:%M:%S.%f")
        epoch_ts = "%.6f" % stamp.timestamp()

        for family in FAMILIES:
            want = target * mix[family] * tick + carry[family]
            count = int(want)
            carry[family] = want - count
            if count <= 0:
                continue
            if family == "infologger":
                if sender is None:
                    continue
                parts = []
                for _ in range(count):
                    left, right, message = il_pool[random.randrange(il_n)]
                    parts.append("%s%s%s, \"message\": %s soakrun_%s soakseq_%d\"}\n" % (
                        left, epoch_ts, right, message, args.run_id, seq))
                    seq += stride
                payload = "".join(parts)
                if not sender.send(payload):
                    aborted = "infologger socket refused three times"
                    break
                emitted["infologger"] += count
                bytes_out += len(payload)
                second_counts["infologger"] += count
                second_bytes += len(payload)
                continue

            pool, pool_n = (dds_pool, dds_n) if family == "dds" else (stdout_pool, stdout_n)
            writer = dds_writer if family == "dds" else stdout_writer
            separator = "   " if family == "dds" else " "
            per_host = {}
            for _ in range(count):
                host = hosts[random.randrange(len(hosts))]
                per_host.setdefault(host, []).append(
                    "%s%s%s soakrun_%s soakseq_%d\n" % (
                        text_ts, separator, pool[random.randrange(pool_n)],
                        args.run_id, seq))
                seq += stride
            for host, lines in per_host.items():
                payload = "".join(lines)
                writer.write(host, payload)
                bytes_out += len(payload)
                second_bytes += len(payload)
            emitted[family] += count
            second_counts[family] += count

        if aborted:
            break

        dds_writer.tick()
        stdout_writer.tick()

        current_second = int(elapsed)
        if current_second != last_second:
            rows.append((
                last_second, round(rate_at(args, last_second) * share, 1),
                second_counts["dds"], second_counts["stdout"],
                second_counts["infologger"], second_bytes))
            second_counts = {"dds": 0, "stdout": 0, "infologger": 0}
            second_bytes = 0
            last_second = current_second
            guard_countdown -= 1
            if args.tail_root and args.disk_guard_pct > 0 and guard_countdown <= 0:
                guard_countdown = 5
                used = disk_free_pct(args.tail_root)
                if used >= args.disk_guard_pct:
                    aborted = "disk guard: %s is %.1f%% full" % (args.tail_root, used)
                    break

        next_tick += tick
        sleep_for = next_tick - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = time.perf_counter()

    dds_writer.close()
    stdout_writer.close()
    if sender is not None:
        sender.close()

    result = {
        "worker": worker_id,
        "emitted": emitted,
        "bytes": bytes_out,
        "seconds": time.perf_counter() - start,
        "wall_start": wall_start,
        "socket_failures": sender.failures if sender else 0,
        "aborted": aborted,
        "rows": rows,
    }
    with open(result_path, "w") as handle:
        json.dump(result, handle)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--tail-root", default="")
    parser.add_argument("--il-host", default="127.0.0.1")
    parser.add_argument("--il-port", type=int, default=5170)
    parser.add_argument("--mode", default="steady",
                        choices=["steady", "ramp", "burst", "selftest"])
    parser.add_argument("--rate", type=float, default=1000.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--start", type=float, default=1000.0)
    parser.add_argument("--step", type=float, default=1000.0)
    parser.add_argument("--step-seconds", type=float, default=60.0)
    parser.add_argument("--max", type=float, default=50000.0)
    parser.add_argument("--base", type=float, default=1000.0)
    parser.add_argument("--peak", type=float, default=20000.0)
    parser.add_argument("--on", type=float, default=30.0)
    parser.add_argument("--off", type=float, default=120.0)
    parser.add_argument("--mix", default="dds=2,stdout=2,infologger=6")
    parser.add_argument("--hosts", type=int, default=10)
    parser.add_argument("--host-prefix", default="epn")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--tick", type=float, default=0.05)
    parser.add_argument("--max-file-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--rotate-wait", type=float, default=15.0)
    parser.add_argument("--disk-guard-pct", type=float, default=85.0)
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--csv", default="")
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    if not args.run_id:
        args.run_id = "r%d" % int(time.time())
    args.run_id = args.run_id.replace("-", "").replace("_", "")

    hosts = ["%s%03d" % (args.host_prefix, i + 1) for i in range(args.hosts)]
    if args.workers < 1:
        args.workers = 1
    if args.workers > len(hosts):
        args.workers = len(hosts)

    tmpdir = args.summary and os.path.dirname(os.path.abspath(args.summary)) or "."
    os.makedirs(tmpdir, exist_ok=True)

    slices = [hosts[i::args.workers] for i in range(args.workers)]
    share = 1.0 / args.workers
    procs, paths = [], []
    for index in range(args.workers):
        path = os.path.join(tmpdir, "logburst-worker-%d.json" % index)
        paths.append(path)
        proc = multiprocessing.Process(
            target=worker, args=(args, index, slices[index], share, path))
        proc.start()
        procs.append(proc)

    try:
        for proc in procs:
            proc.join()
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.join()

    totals = {"dds": 0, "stdout": 0, "infologger": 0}
    total_bytes = 0
    seconds = 0.0
    aborts = []
    merged = {}
    for path in paths:
        try:
            with open(path, "r") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            aborts.append("worker result missing: %s" % path)
            continue
        for family in FAMILIES:
            totals[family] += data["emitted"][family]
        total_bytes += data["bytes"]
        seconds = max(seconds, data["seconds"])
        if data["aborted"]:
            aborts.append(data["aborted"])
        for row in data["rows"]:
            slot = merged.setdefault(row[0], [row[1], 0, 0, 0, 0])
            slot[0] = row[1] * args.workers
            slot[1] += row[2]
            slot[2] += row[3]
            slot[3] += row[4]
            slot[4] += row[5]

    if args.csv:
        with open(args.csv, "w") as handle:
            handle.write("second,target_rate,dds,stdout,infologger,total,bytes\n")
            for second in sorted(merged):
                target, dds, out, il, byte_count = merged[second]
                handle.write("%d,%.1f,%d,%d,%d,%d,%d\n" % (
                    second, target, dds, out, il, dds + out + il, byte_count))

    total = sum(totals.values())
    summary = {
        "run_id": args.run_id,
        "mode": args.mode,
        "workers": args.workers,
        "seconds": round(seconds, 2),
        "emitted": totals,
        "emitted_total": total,
        "achieved_rate": round(total / seconds, 1) if seconds else 0,
        "bytes": total_bytes,
        "mbytes_per_second": round(total_bytes / seconds / 1e6, 2) if seconds else 0,
        "aborted": aborts,
    }
    if args.summary:
        with open(args.summary, "w") as handle:
            json.dump(summary, handle, indent=2)
    print(json.dumps(summary, indent=2))
    return 1 if aborts else 0


if __name__ == "__main__":
    sys.exit(main())
