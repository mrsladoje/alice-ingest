#!/usr/bin/env python3

import argparse
import gzip
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "mode": "ok",
    "delay_ms": 0,
    "docs": 0,
    "requests": 0,
    "bytes": 0,
    "rejected": 0,
    "restarts": 0,
    "started": time.time(),
}
LOCK = threading.Lock()
STATS_PATH = ""
INFO = {
    "name": "soak-sink",
    "cluster_name": "alice-soak",
    "version": {"number": "2.17.0", "distribution": "opensearch",
                "build_type": "tar", "lucene_version": "9.11.1"},
    "tagline": "The OpenSearch Project",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/__stats"):
            self._send(200, snapshot())
            return
        self._send(200, INFO)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):
        self._send(200, {"acknowledged": True})

    def do_POST(self):
        if self.path.startswith("/__ctl"):
            query = self.path.partition("?")[2]
            params = dict(
                part.split("=", 1) for part in query.split("&") if "=" in part)
            with LOCK:
                if "mode" in params:
                    STATE["mode"] = params["mode"]
                if "delay_ms" in params:
                    STATE["delay_ms"] = int(params["delay_ms"])
                self._send(200, {"mode": STATE["mode"], "delay_ms": STATE["delay_ms"]})
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        # Record what actually arrived on the wire. A `compress` value the
        # output plugin does not implement is applied silently as "none", and
        # the only way to tell is to look at the header and the byte count.
        encoding = (self.headers.get("Content-Encoding") or "none").lower()
        with LOCK:
            seen = STATE.setdefault("encodings", {})
            slot = seen.setdefault(encoding, {"requests": 0, "wire_bytes": 0})
            slot["requests"] += 1
            slot["wire_bytes"] += len(body)
        if encoding == "gzip":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass

        with LOCK:
            mode = STATE["mode"]
            delay = STATE["delay_ms"] / 1000.0
        if delay:
            time.sleep(delay)

        if mode == "stall":
            time.sleep(3600)
            return
        if mode in ("429", "500", "503"):
            with LOCK:
                STATE["requests"] += 1
                STATE["rejected"] += 1
            self._send(int(mode), {"error": "soak sink refusing on purpose"})
            return

        lines = [line for line in body.split(b"\n") if line.strip()]
        docs = len(lines)
        if self.path.endswith("/_bulk"):
            docs = sum(1 for line in lines if not line.startswith(b'{"create"')
                       and not line.startswith(b'{"index"'))
            if docs == 0:
                docs = len(lines) // 2
        with LOCK:
            STATE["requests"] += 1
            STATE["docs"] += docs
            STATE["bytes"] += len(body)

        if self.path.endswith("/_bulk"):
            self._send(200, {"took": 1, "errors": False, "items": []})
        else:
            self._send(200, {"ok": True})

    def log_message(self, *args):
        return


def snapshot():
    with LOCK:
        data = dict(STATE)
    data["seconds"] = time.time() - data["started"]
    return data


def persist():
    if not STATS_PATH:
        return
    while True:
        try:
            with open(STATS_PATH + ".tmp", "w") as handle:
                json.dump(snapshot(), handle, indent=2)
            os.replace(STATS_PATH + ".tmp", STATS_PATH)
        except OSError:
            pass
        time.sleep(2)


def restore():
    if not STATS_PATH or not os.path.exists(STATS_PATH):
        return
    try:
        with open(STATS_PATH) as handle:
            previous = json.load(handle)
    except (OSError, ValueError):
        return
    with LOCK:
        for key in ("docs", "requests", "bytes", "rejected"):
            STATE[key] = previous.get(key, 0)
        STATE["restarts"] = previous.get("restarts", 0) + 1


def main():
    global STATS_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--stats-file", default="")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    STATS_PATH = args.stats_file
    if not args.fresh:
        restore()
    if STATS_PATH:
        keeper = threading.Thread(target=persist, daemon=True)
        keeper.start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True

    def dump(*_):
        if STATS_PATH:
            try:
                with open(STATS_PATH, "w") as handle:
                    json.dump(snapshot(), handle, indent=2)
            except OSError:
                pass
        os._exit(0)

    signal.signal(signal.SIGTERM, dump)
    signal.signal(signal.SIGINT, dump)
    print("soak sink listening on %d" % args.port, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
