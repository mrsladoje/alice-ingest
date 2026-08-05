import json
import os
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("FAULT_AGENT_PORT", "8089"))
TOKEN = os.environ.get("FAULT_AGENT_TOKEN", "")
ALLOWED = [
    s.strip() for s in os.environ.get("FAULT_AGENT_SERVICES", "").split(",")
    if s.strip()
]
MAX_STRESS_SECONDS = int(
    os.environ.get("FAULT_AGENT_MAX_STRESS_SECONDS", "5400"))
NODE_ID = os.environ.get("FAULT_AGENT_NODE_ID", "")
SYSTEMCTL = "/usr/bin/systemctl"
STRESS_TAG = "alice-fault-cpu-stress"

_STRESS = []


def _run(argv, timeout=45):
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _service_active(name):
    code, _ = _run([SYSTEMCTL, "is-active", "--quiet", name], timeout=10)
    return code == 0


def _stress_active():
    _STRESS[:] = [p for p in _STRESS if p.poll() is None]
    if _STRESS:
        return True
    code, _ = _run(["/usr/bin/pgrep", "-f", STRESS_TAG], timeout=10)
    return code == 0


def _start_stress(seconds):
    seconds = max(1, min(int(seconds), MAX_STRESS_SECONDS))
    workers = os.cpu_count() or 2
    started = 0
    for _ in range(workers):
        try:
            _STRESS.append(subprocess.Popen(
                ["/usr/bin/timeout", str(seconds), "/bin/sh", "-c",
                 "while :; do :; done", STRESS_TAG],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            started += 1
        except Exception as exc:
            return False, f"could not start a load generator: {exc}"
    return True, f"{started} load generators for {seconds}s"


def _stop_stress():
    for proc in _STRESS:
        try:
            proc.kill()
        except Exception:
            pass
    _STRESS[:] = []
    _run(["/usr/bin/pkill", "-f", STRESS_TAG], timeout=15)
    return True, "load generators stopped"


def _service_action(name, verb):
    if name not in ALLOWED:
        return False, (
            f"REFUSED — {name} is not in this agent's allowlist "
            f"({', '.join(ALLOWED) or 'empty'})")
    code, detail = _run([SYSTEMCTL, verb, name])
    if code != 0:
        return False, f"systemctl {verb} {name} failed: {detail}"
    return True, f"{name} {verb}ped" if verb == "stop" else f"{name} started"


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        if not TOKEN:
            return True
        return self.headers.get("X-Fault-Token", "") == TOKEN

    def _health(self):
        return {
            "ok": True,
            "node_id": NODE_ID,
            "allowed_services": ALLOWED,
            "services": {name: _service_active(name) for name in ALLOWED},
            "cpu_stress": _stress_active(),
        }

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorised():
            self._json(403, {"ok": False, "error": "bad or missing token"})
            return
        if parsed.path.rstrip("/") in ("", "/health", "/fault-status"):
            self._json(200, self._health())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        if not self._authorised():
            self._json(403, {"ok": False, "error": "bad or missing token"})
            return
        name = (query.get("name") or [""])[0]
        if path == "/service-stop":
            ok, detail = _service_action(name, "stop")
        elif path == "/service-start":
            ok, detail = _service_action(name, "start")
        elif path == "/cpu-stress":
            ok, detail = _start_stress((query.get("seconds") or ["900"])[0])
        elif path == "/cpu-stress-stop":
            ok, detail = _stop_stress()
        else:
            self._json(404, {"ok": False, "error": "not found"})
            return
        self._json(200 if ok else 409,
                   {"ok": ok, "detail": detail, "state": self._health()})

    def log_message(self, *args):
        pass


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[fault-agent] serving on 0.0.0.0:{PORT} "
          f"(node={NODE_ID}, allowed={ALLOWED})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
