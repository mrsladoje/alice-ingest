import html
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPS_PORT = int(os.environ.get("OPS_PORT", "8090"))
OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
WORKERS = [w for w in os.environ.get("OPS_WORKER_TRIGGERS", "").split(",") if w]
INFO_NODES = [n for n in os.environ.get("OPS_WORKER_INFO_NODES", "").split(",") if n]
FAMILIES = os.environ.get("OPS_REPLAY_FAMILIES", "infologger,dds,stdout")
TEMPLATES_SCRIPT = os.environ.get(
    "OPS_TEMPLATES_SCRIPT", "/opt/alice-ingest/init/templates.sh")
RESET_SCRIPT = os.environ.get(
    "OPS_RESET_SCRIPT", "/opt/alice-ingest/reset_derived.py")
COUNT_TARGET = "infologger,generic-log-*"


def _log_families():
    return ["infologger", "generic-log-other"] + [
        f"generic-log-info-{n}" for n in INFO_NODES]


LOG_FAMILIES = _log_families()


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


def _search_count(index, query):
    code, body = _req(
        "POST",
        f"{OS_URL}/{index}/_search?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True, "query": query})
    if code != 200:
        return "?"
    try:
        total = json.loads(body).get("hits", {}).get("total", 0)
        if isinstance(total, dict):
            return str(total.get("value", 0))
        return str(total)
    except ValueError:
        return "?"


def active_alerts():
    return _search_count(
        ".opendistro-alerting-alerts",
        {"term": {"state": "ACTIVE"}})


def anomalies_last_hour():
    return _search_count(
        ".opendistro-anomaly-results*",
        {"bool": {"filter": [
            {"range": {"execution_end_time": {"gte": "now-1h"}}},
            {"range": {"anomaly_grade": {"gt": 0.5}}},
        ]}})


def reset_derived(mode="full"):
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", RESET_SCRIPT],
            env=dict(os.environ, OS_URL=OS_URL, MODE=mode,
                     LOG_FAMILIES=",".join(LOG_FAMILIES)),
            capture_output=True, text=True, timeout=600)
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if proc.returncode != 0:
            lines.append(
                f"reset finished with exit {proc.returncode} — stale alerts "
                f"or rollup rows may survive this reload: "
                f"{(proc.stderr or '').strip()[-300:]}")
        return lines
    except Exception as e:
        return [f"FAILED to run {RESET_SCRIPT}: {e} — old alerts, anomalies "
                f"and trend-rollup rows are still there"]


def wipe():
    lines = []
    patterns = ["infologger-*", "generic-log-other-*"]
    patterns += [f"generic-log-info-{n}-*" for n in INFO_NODES]
    for pat in patterns:
        code, _ = _req(
            "DELETE",
            f"{OS_URL}/{pat}?ignore_unavailable=true&allow_no_indices=true")
        lines.append(f"delete {pat}: HTTP {code}")
    lines += reset_derived()
    env = dict(os.environ, OS_URL=OS_URL, SEED_EMPTY_INDICES="false")
    try:
        proc = subprocess.run(
            [TEMPLATES_SCRIPT], env=env, capture_output=True, text=True,
            timeout=300)
        if proc.returncode == 0:
            lines.append("rebuilt rollover write aliases and mappings")
        else:
            lines.append(
                f"FAILED to rebuild write aliases (exit {proc.returncode}) — "
                f"ingest will auto-create plain indices and ROLLOVER WILL BE "
                f"INACTIVE until the next deploy: "
                f"{(proc.stdout or proc.stderr or '').strip()[-400:]}")
    except Exception as e:
        lines.append(f"FAILED to run {TEMPLATES_SCRIPT}: {e}")
    return lines


def replay_in_flight():
    busy = []
    for w in WORKERS:
        code, body = _req("GET", f"{w}/replay-status", timeout=5)
        if code != 200:
            continue
        try:
            if json.loads(body).get("running"):
                busy.append(w)
        except ValueError:
            continue
    return busy


def stop_replays(workers):
    lines = []
    for w in workers:
        code, body = _req("POST", f"{w}/replay-stop", timeout=15)
        lines.append(f"stop {w}: HTTP {code} {body.strip()}")
    return lines


def wait_until_idle(timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not replay_in_flight():
            return True
        time.sleep(2)
    return not replay_in_flight()


def trigger_workers():
    lines = []
    if not WORKERS:
        lines.append("no worker triggers configured")
    for w in WORKERS:
        code, resp = _req("POST", f"{w}/replay?family={FAMILIES}")
        lines.append(f"replay {w}: HTTP {code} {resp.strip()}")
    return lines


def stop_only():
    busy = replay_in_flight()
    if not busy:
        return ["no replay is running"]
    return stop_replays(busy) + [
        "stop requested — the workers finish the record they are on and then "
        "end the pass; the indices keep everything shipped so far"]


def clear_only():
    return reset_derived(mode="clear")


def run_replay(fresh):
    lines = []
    if fresh:
        busy = replay_in_flight()
        if busy:
            lines.append(
                "a replay was already running on " + ", ".join(busy)
                + " — cancelling it first, since a fresh reload replaces it")
            lines += stop_replays(busy)
            if not wait_until_idle():
                return lines + [
                    "REFUSED — it did not stop within 45s, so nothing was "
                    "wiped. Wiping while a load is in flight empties the "
                    "indices without starting a reload, and lets ingest "
                    "recreate the log indices with the wrong mapping.",
                    "Restart alice-replay on those workers, then press "
                    "Reload data (fresh) again.",
                ]
            lines.append("previous replay stopped")
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
 .row{{display:flex;gap:1.5rem;margin:1rem 0}}
 .stat{{flex:1}}
 .stat .count{{font-size:1.8rem}}
 .muted{{color:#666}}
 form{{display:inline}}
 button{{font-size:1rem;padding:.6rem 1rem;border:0;border-radius:8px;cursor:pointer;margin:.3rem .3rem 0 0}}
 .fresh{{background:#c0392b;color:#fff}}
 .append{{background:#e0e0e0;color:#111}}
 .clear{{background:#f0ad4e;color:#111}}
 a.dash{{display:inline-block;margin-top:1.2rem;font-weight:600}}
 pre{{background:#f5f5f5;padding:1rem;border-radius:8px;white-space:pre-wrap}}
</style>
<h1>ALICE Cockpit — Ops</h1>
<p class="muted">Documents currently indexed (infologger + generic-log-*):</p>
<div class="count">{count}</div>
<div class="row">
  <div class="stat"><p class="muted">Active alerts</p><div class="count">{alerts}</div></div>
  <div class="stat"><p class="muted">Anomalies (last hour)</p><div class="count">{anomalies}</div></div>
</div>
{result}
<form method="post" action="replay-fresh" onsubmit="return confirm('Wipe all log indices and reload from S3?');">
  <button class="fresh" type="submit">Reload data (fresh)</button>
</form>
<form method="post" action="replay">
  <button class="append" type="submit">Append replay</button>
</form>
<form method="post" action="stop">
  <button class="append" type="submit">Stop running replay</button>
</form>
<form method="post" action="clear" onsubmit="return confirm('Clear all alerts, anomalies and trend baselines? The log data stays.');">
  <button class="clear" type="submit">Clear alerts &amp; anomalies</button>
</form>
<p class="muted">Fresh cancels any load already in flight, wipes, then reloads — always a clean load. Append adds another
full pass (no dedup), so use it only deliberately. A load runs for about an hour:
it is paced so the anomaly detectors get enough consecutive one-minute windows to
finish training. Documents start appearing within seconds and climb throughout.</p>
<p class="muted">Clear removes every alert, anomaly record and trend baseline without
touching the logs or reloading anything. Use it when the panels are full of findings
about data you have since replaced. Live cluster telemetry and the trained detector
models are kept, so detection carries on from the next window.</p>
<a class="dash" href="/">Open the ALICE Cockpit dashboard</a>
"""


def render(result_lines=None):
    result = ""
    if result_lines:
        joined = html.escape("\n".join(result_lines))
        result = f"<pre>{joined}</pre>"
    return PAGE.format(
        count=html.escape(doc_count()),
        alerts=html.escape(active_alerts()),
        anomalies=html.escape(anomalies_last_hour()),
        result=result)


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
            self._send(200, json.dumps({
                "count": doc_count(),
                "active_alerts": active_alerts(),
                "anomalies_last_hour": anomalies_last_hour(),
            }), "application/json")
        else:
            self._send(404, render(["not found"]))

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/replay-fresh"):
            self._send(200, render(run_replay(fresh=True)))
        elif path.endswith("/replay"):
            self._send(200, render(run_replay(fresh=False)))
        elif path.endswith("/clear"):
            self._send(200, render(clear_only()))
        elif path.endswith("/stop"):
            self._send(200, render(stop_only()))
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
