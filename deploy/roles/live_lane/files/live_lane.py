import gzip
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = os.environ.get("LIVE_BIND", "0.0.0.0")
PORT = int(os.environ.get("LIVE_PORT", "8092"))
TOKEN = os.environ.get("LIVE_TOKEN", "")
INGEST_PATH = os.environ.get("LIVE_INGEST_PATH", "/ingest")
REPLAY_ROWS = int(os.environ.get("LIVE_REPLAY_ROWS", "500"))
CLIENT_QUEUE_MAX = int(os.environ.get("LIVE_CLIENT_QUEUE_MAX", "2000"))
BUFFER_ROWS = int(os.environ.get("LIVE_BUFFER_ROWS", "10000"))
STATIC_DIR = os.environ.get(
    "LIVE_STATIC_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "live"))
KEEPALIVE_SECONDS = int(os.environ.get("LIVE_KEEPALIVE_SECONDS", "20"))
MAX_BODY_BYTES = int(os.environ.get("LIVE_MAX_BODY_BYTES", str(16 * 1024 * 1024)))

SEVERITY_NORM = {
    "I": "info", "W": "warning", "E": "error", "F": "fatal", "D": "debug",
    "Info": "info", "Warning": "warning", "Error": "error",
    "Fatal": "fatal", "Sys": "system",
    "inf": "info", "err": "error", "cout": "info",
}

KEEP_FIELDS = (
    "@timestamp", "collector_time", "severity", "severity_norm", "origin_host",
    "host", "hostname", "node", "log_source", "source_file", "source",
    "facility", "message", "rolename", "run", "partition", "detector",
    "system", "pid", "username", "level", "errcode", "errsource", "errline",
)

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

_lock = threading.Lock()
_clients = []
_recent = []
_stats = {"received": 0, "dropped_slow_client": 0, "posts": 0, "bad_posts": 0}
_seq = 0


def log(msg):
    print(f"[live-lane] {msg}", flush=True)


class Client:
    def __init__(self):
        self.queue = queue.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.dropped = 0

    def offer(self, payload):
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            self.dropped += 1
            with _lock:
                _stats["dropped_slow_client"] += 1


def normalize(record):
    if not isinstance(record, dict):
        return None
    out = {k: record[k] for k in KEEP_FIELDS if k in record}
    severity = record.get("severity")
    if not out.get("severity_norm"):
        out["severity_norm"] = SEVERITY_NORM.get(
            severity if isinstance(severity, str) else "", "unknown")
    if not out.get("origin_host"):
        host = record.get("hostname") or record.get("host")
        if host:
            out["origin_host"] = host
    message = out.get("message")
    if message is None:
        message = record.get("log") or ""
    out["message"] = message if isinstance(message, str) else str(message)
    return out


def publish(records):
    global _seq
    payloads = []
    for record in records:
        normalized = normalize(record)
        if normalized is None:
            continue
        with _lock:
            _seq += 1
            normalized["_id"] = _seq
        payloads.append(json.dumps(normalized, default=str))
    if not payloads:
        return 0
    with _lock:
        _recent.extend(payloads)
        if len(_recent) > REPLAY_ROWS:
            del _recent[:len(_recent) - REPLAY_ROWS]
        _stats["received"] += len(payloads)
        targets = list(_clients)
    for client in targets:
        for payload in payloads:
            client.offer(payload)
    return len(payloads)


def decode_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_BODY_BYTES:
        return None
    body = handler.rfile.read(length)
    if (handler.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def authorized(handler):
    if not TOKEN:
        return True
    header = handler.headers.get("Authorization") or ""
    return header == f"Bearer {TOKEN}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "alice-live-lane"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
              extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != INGEST_PATH:
            self._send(404, b"not found")
            return
        if not authorized(self):
            self._send(401, b"unauthorized")
            return
        payload = decode_body(self)
        with _lock:
            _stats["posts"] += 1
        if payload is None:
            with _lock:
                _stats["bad_posts"] += 1
            self._send(400, b"bad payload")
            return
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            with _lock:
                _stats["bad_posts"] += 1
            self._send(400, b"bad payload")
            return
        publish(payload)
        self._send(204)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            with _lock:
                body = json.dumps({
                    "ok": True,
                    "viewers": len(_clients),
                    "buffered": len(_recent),
                    **_stats,
                }).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/stream":
            self.stream()
            return
        self.static(path)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        name = os.path.basename(path)
        if not name or name.startswith("."):
            self._send(404, b"not found")
            return
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            self._send(404, b"not found")
            return
        ext = os.path.splitext(name)[1].lower()
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, b"unreadable")
            return
        self._send(200, body, STATIC_TYPES.get(ext, "application/octet-stream"),
                   {"Cache-Control": "no-cache"})

    def stream(self):
        client = Client()
        with _lock:
            backlog = list(_recent)
            _clients.append(client)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for payload in backlog:
                self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    payload = client.queue.get(timeout=KEEPALIVE_SECONDS)
                    chunk = f"data: {payload}\n\n"
                except queue.Empty:
                    chunk = ": keepalive\n\n"
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                if client in _clients:
                    _clients.remove(client)
            if client.dropped:
                log(f"viewer disconnected after dropping {client.dropped} "
                    f"records it could not keep up with")


def main():
    if not os.path.isdir(STATIC_DIR):
        log(f"FATAL: static directory {STATIC_DIR} does not exist")
        return 1
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    log(f"listening on {BIND}:{PORT}; ingest {INGEST_PATH}; "
        f"replay {REPLAY_ROWS} rows; per-viewer queue {CLIENT_QUEUE_MAX}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
