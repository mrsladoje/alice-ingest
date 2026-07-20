import html
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPS_PORT = int(os.environ.get("OPS_PORT", "8090"))
OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
WORKERS = [w for w in os.environ.get("OPS_WORKER_TRIGGERS", "").split(",") if w]
INFO_NODES = [n for n in os.environ.get("OPS_WORKER_INFO_NODES", "").split(",") if n]
FAMILIES = os.environ.get("OPS_REPLAY_FAMILIES", "infologger,dds,stdout")
COUNT_TARGET = "infologger,generic-log-*"


def _req(method, url, data=None, timeout=20):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def doc_count():
    code, body = _req(
        "GET", f"{OS_URL}/{COUNT_TARGET}/_count"
        "?ignore_unavailable=true&allow_no_indices=true")
    if code == 200:
        try:
            return str(json.loads(body).get("count", "?"))
        except ValueError:
            return "?"
    return "?"


def wipe():
    lines = []
    indices = ["infologger", "generic-log-other"]
    indices += [f"generic-log-info-{n}" for n in INFO_NODES]
    for idx in indices:
        code, _ = _req("DELETE", f"{OS_URL}/{idx}")
        lines.append(f"delete {idx}: HTTP {code}")
    for n in INFO_NODES:
        code, _ = _req(
            "PUT", f"{OS_URL}/generic-log-info-{n}?wait_for_active_shards=0",
            {"settings": {"index.routing.allocation.require.box": n}})
        lines.append(f"recreate generic-log-info-{n}: HTTP {code}")
    return lines


def trigger_workers():
    lines = []
    if not WORKERS:
        lines.append("no worker triggers configured")
    for w in WORKERS:
        code, resp = _req("POST", f"{w}/replay?family={FAMILIES}")
        lines.append(f"replay {w}: HTTP {code} {resp.strip()}")
    return lines


def run_replay(fresh):
    lines = []
    if fresh:
        lines += wipe()
    lines += trigger_workers()
    return lines


PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ALICE Cockpit — Ops</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:640px;margin:3rem auto;padding:0 1rem;color:#111}}
 h1{{font-size:1.5rem}}
 .count{{font-size:2.5rem;font-weight:700;margin:.2rem 0}}
 .muted{{color:#666}}
 form{{display:inline}}
 button{{font-size:1rem;padding:.6rem 1rem;border:0;border-radius:8px;cursor:pointer;margin:.3rem .3rem 0 0}}
 .fresh{{background:#c0392b;color:#fff}}
 .append{{background:#e0e0e0;color:#111}}
 a.dash{{display:inline-block;margin-top:1.2rem;font-weight:600}}
 pre{{background:#f5f5f5;padding:1rem;border-radius:8px;white-space:pre-wrap}}
</style>
<h1>🛰️ ALICE Cockpit — Ops</h1>
<p class="muted">Documents currently indexed (infologger + generic-log-*):</p>
<div class="count">{count}</div>
{result}
<form method="post" action="replay-fresh" onsubmit="return confirm('Wipe all log indices and reload from S3?');">
  <button class="fresh" type="submit">Reload data (fresh)</button>
</form>
<form method="post" action="replay">
  <button class="append" type="submit">Append replay</button>
</form>
<p class="muted">Fresh wipes first, then reloads — always a clean load. Append adds another
full pass (no dedup), so use it only deliberately. A load takes a few minutes.</p>
<a class="dash" href="/">← Open the ALICE Cockpit dashboard</a>
"""


def render(result_lines=None):
    result = ""
    if result_lines:
        joined = html.escape("\n".join(result_lines))
        result = f"<pre>{joined}</pre>"
    return PAGE.format(count=html.escape(doc_count()), result=result)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/ops"):
            self._send(200, render())
        elif path == "/status":
            self._send(200, json.dumps({"count": doc_count()}),
                       "application/json")
        else:
            self._send(404, render(["not found"]))

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/replay-fresh"):
            self._send(200, render(run_replay(fresh=True)))
        elif path.endswith("/replay"):
            self._send(200, render(run_replay(fresh=False)))
        else:
            self._send(404, render(["not found"]))

    def log_message(self, *args):
        pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", OPS_PORT), Handler)
    print(f"[ops] serving on 127.0.0.1:{OPS_PORT} "
          f"(workers={WORKERS}, info_nodes={INFO_NODES})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
