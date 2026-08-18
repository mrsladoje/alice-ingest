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
    "started": time.time(),
}
LOCK = threading.Lock()
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
            with LOCK:
                self._send(200, dict(STATE))
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
        if self.headers.get("Content-Encoding") == "gzip":
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--stats-file", default="")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True

    def dump(*_):
        if args.stats_file:
            with LOCK:
                snapshot = dict(STATE)
            snapshot["seconds"] = time.time() - snapshot["started"]
            with open(args.stats_file, "w") as handle:
                json.dump(snapshot, handle, indent=2)
        os._exit(0)

    signal.signal(signal.SIGTERM, dump)
    signal.signal(signal.SIGINT, dump)
    print("soak sink listening on %d" % args.port, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
