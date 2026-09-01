import datetime
import json
import os
import random
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus

LANE = os.environ.get("FEED_LANE_URL", "http://127.0.0.1:8092/ingest")
STORE = os.environ.get("FEED_STORE_URL", "http://127.0.0.1:9209/_ingest")
CONTROL_PORT = int(os.environ.get("FEED_CONTROL_PORT", "8093"))
CALM_RATE = float(os.environ.get("FEED_CALM_RATE", "12"))
BURST_RATE = float(os.environ.get("FEED_BURST_RATE", "900"))
BURST_SECONDS = float(os.environ.get("FEED_BURST_SECONDS", "12"))
AUTO_BURST_EVERY = float(os.environ.get("FEED_AUTO_BURST_EVERY", "90"))

_rng = random.Random()
_burst_until = 0.0
_lock = threading.Lock()


def send(url, batch):
    data = json.dumps(batch).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except Exception:
        return False


def post(batch):
    stored = send(STORE, batch) if batch else True
    return send(LANE, batch) and stored


def start_burst(seconds):
    global _burst_until
    with _lock:
        _burst_until = max(_burst_until, time.time() + seconds)
    print(f"[feeder] burst for {seconds:.0f} s at {BURST_RATE:.0f} records/s",
          flush=True)


class Control(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/burst":
            start_burst(BURST_SECONDS)
            body = b"burst started\n"
        elif path == "/calm":
            global _burst_until
            with _lock:
                _burst_until = 0.0
            body = b"back to the calm rate\n"
        else:
            body = (f"GET /burst  -> {BURST_RATE:.0f} records/s for "
                    f"{BURST_SECONDS:.0f} s\nGET /calm   -> stop a burst\n"
                    ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def control_server():
    server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Control)
    server.daemon_threads = True
    server.serve_forever()


def main():
    threading.Thread(target=control_server, daemon=True).start()
    print(f"[feeder] pushing to {LANE} and {STORE}; control on "
          f"http://127.0.0.1:{CONTROL_PORT}/burst", flush=True)

    for attempt in range(60):
        if post([]):
            break
        time.sleep(0.5)

    next_auto = time.time() + AUTO_BURST_EVERY
    while True:
        now = time.time()
        with _lock:
            bursting = now < _burst_until
        if AUTO_BURST_EVERY > 0 and now >= next_auto and not bursting:
            start_burst(BURST_SECONDS)
            next_auto = now + AUTO_BURST_EVERY + BURST_SECONDS
            bursting = True

        rate = BURST_RATE if bursting else CALM_RATE
        batch_size = max(1, int(rate / 5))
        when = datetime.datetime.now(datetime.timezone.utc)
        batch = [corpus.one(_rng, when) for _ in range(batch_size)]
        post(batch)
        time.sleep(batch_size / rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
