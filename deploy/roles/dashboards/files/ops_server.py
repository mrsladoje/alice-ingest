import html
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template

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
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0.5"))
FLASH_TTL_SECONDS = 300
FLASH_LIMIT = 32
_FLASH_RESULTS = {}
_FLASH_LOCK = threading.Lock()


def _store_result(lines):
    """Keep one action result briefly so POST can redirect to a safe GET."""
    token = secrets.token_urlsafe(18)
    now = time.time()
    with _FLASH_LOCK:
        expired = [
            key for key, (created, _) in _FLASH_RESULTS.items()
            if now - created > FLASH_TTL_SECONDS
        ]
        for key in expired:
            _FLASH_RESULTS.pop(key, None)
        while len(_FLASH_RESULTS) >= FLASH_LIMIT:
            oldest = min(
                _FLASH_RESULTS, key=lambda key: _FLASH_RESULTS[key][0])
            _FLASH_RESULTS.pop(oldest, None)
        _FLASH_RESULTS[token] = (now, list(lines))
    return token


def _take_result(token):
    if not token:
        return None
    with _FLASH_LOCK:
        item = _FLASH_RESULTS.pop(token, None)
    if item is None:
        return None
    created, lines = item
    if time.time() - created > FLASH_TTL_SECONDS:
        return None
    return lines


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


def open_incidents():
    return _search_count(
        INCIDENTS_INDEX,
        {"bool": {"filter": [{"term": {"state": "firing"}}]}})


def open_signals():
    return _search_count(
        SIGNALS_INDEX,
        {"bool": {"filter": [{"term": {"state": "firing"}}]}})


def anomalies_last_hour():
    return _search_count(
        ".opendistro-anomaly-results*",
        {"bool": {
            "filter": [
                {"range": {"execution_end_time": {"gte": "now-1h"}}},
                {"range": {"anomaly_grade": {"gt": GRADE_FLOOR}}},
            ],
            "must_not": [{"exists": {"field": "task_id"}}]}})


def incident_rows(limit=8):
    code, body = _req(
        "POST",
        f"{OS_URL}/{INCIDENTS_INDEX}/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": limit,
         "sort": [{"last_seen": "desc"}],
         "query": {"bool": {"filter": [{"term": {"state": "firing"}}]}}})
    if code != 200:
        return []
    rows = []
    for hit in json.loads(body).get("hits", {}).get("hits", []):
        src = hit.get("_source") or {}
        rows.append({
            "alertname": src.get("alertname", "?"),
            "severity": src.get("severity", "?"),
            "scope": src.get("notification_scope", "?"),
            "members": src.get("member_count", 0),
            "samples": ", ".join((src.get("entity_samples") or [])[:3]),
            "klass": src.get("class", "single"),
        })
    return rows


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


def family_counts():
    out = []
    for fam in LOG_FAMILIES:
        code, body = _req(
            "GET", f"{OS_URL}/{fam}/_count"
            "?ignore_unavailable=true&allow_no_indices=true")
        n = 0
        if code == 200:
            try:
                n = int(json.loads(body).get("count", 0))
            except (ValueError, TypeError):
                n = 0
        out.append((fam, n))
    return out


def snapshot():
    busy = replay_in_flight()
    return {
        "count": doc_count(),
        "active_alerts": active_alerts(),
        "anomalies_last_hour": anomalies_last_hour(),
        "open_incidents": open_incidents(),
        "open_signals": open_signals(),
        "incidents": incident_rows(),
        "replay_running": bool(busy),
        "replay_workers": len(busy),
        "families": family_counts(),
    }


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


PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#07111f">
<title>ALICE Cockpit — Operations</title>
<style>
 :root{
   color-scheme:dark;
   --bg:#07111f;--surface:#0d1b2a;--surface-2:#112338;
   --line:#20364d;--text:#f4f8fc;--muted:#93a8bd;
   --cyan:#4dd9e8;--blue:#5b8cff;--green:#35d07f;
   --amber:#f7b955;--red:#ff6b6b;--shadow:0 20px 60px rgba(0,0,0,.28)
 }
 *{box-sizing:border-box}
 body{
   margin:0;min-height:100vh;color:var(--text);
   font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   background:
     radial-gradient(circle at 8% 0%,rgba(77,217,232,.13),transparent 30rem),
     radial-gradient(circle at 100% 20%,rgba(91,140,255,.12),transparent 34rem),
     var(--bg)
 }
 body:before{
   content:"";position:fixed;inset:0;pointer-events:none;opacity:.32;
   background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),
     linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
   background-size:32px 32px;mask-image:linear-gradient(to bottom,#000,transparent 75%)
 }
 .shell{position:relative;width:min(1120px,calc(100% - 32px));margin:0 auto;padding:42px 0 64px}
 .topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:24px}
 .eyebrow{margin:0 0 8px;color:var(--cyan);font-size:.72rem;font-weight:800;letter-spacing:.18em;text-transform:uppercase}
 h1{margin:0;font-size:clamp(2rem,4vw,3.35rem);line-height:1;letter-spacing:-.045em}
 .lede{max-width:650px;margin:14px 0 0;color:var(--muted);line-height:1.6}
 .dash{
   display:inline-flex;align-items:center;gap:8px;flex:none;padding:10px 14px;
   border:1px solid var(--line);border-radius:12px;color:var(--text);
   background:rgba(13,27,42,.72);font-size:.88rem;font-weight:700;text-decoration:none;
   transition:border-color .2s,transform .2s,background .2s
 }
 .dash:hover{transform:translateY(-1px);border-color:#3e617f;background:var(--surface-2)}
 .dash:after{content:"↗";color:var(--cyan)}
 .panel{
   border:1px solid var(--line);border-radius:20px;background:rgba(13,27,42,.88);
   box-shadow:var(--shadow);backdrop-filter:blur(16px)
 }
 .hero{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(260px,.75fr);overflow:hidden;margin-bottom:18px}
 .hero-main{padding:28px 30px}
 .hero-side{padding:26px 28px;border-left:1px solid var(--line);background:rgba(17,35,56,.58)}
 .section-label{margin:0 0 8px;color:var(--muted);font-size:.72rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
 .total{margin:0;font-size:clamp(3rem,7vw,5.2rem);font-weight:780;line-height:1;letter-spacing:-.055em;font-variant-numeric:tabular-nums}
 .total-caption{margin:10px 0 0;color:var(--muted);font-size:.88rem}
 .pill{
   display:inline-flex;align-items:center;gap:9px;padding:8px 12px;border:1px solid;
   border-radius:999px;font-size:.78rem;font-weight:800;letter-spacing:.02em
 }
 .pill:before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 0 4px currentColor}
 .live{color:var(--green);border-color:rgba(53,208,127,.3);background:rgba(53,208,127,.08)}
 .live:before{box-shadow:0 0 0 4px rgba(53,208,127,.12),0 0 14px rgba(53,208,127,.75)}
 .idle{color:#aab9c7;border-color:var(--line);background:rgba(147,168,189,.06)}
 .connection{display:flex;align-items:center;gap:7px;margin-top:14px;color:var(--muted);font-size:.76rem}
 .connection-dot{width:6px;height:6px;border-radius:50%;background:var(--green)}
 .connection.error .connection-dot{background:var(--red)}
 .family-table{width:100%;margin-top:18px;border-collapse:collapse;font-size:.84rem}
 .family-table td{padding:8px 0;border-top:1px solid rgba(32,54,77,.7)}
 .family-table td:first-child{color:#b9c8d6}
 .n{text-align:right;font-variant-numeric:tabular-nums;font-weight:750}
 .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
 .stat{padding:20px 22px}
 .stat-value{margin:8px 0 0;font-size:2rem;font-weight:780;letter-spacing:-.035em;font-variant-numeric:tabular-nums}
 .workspace{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:18px;align-items:start}
 .actions,.incidents{padding:26px}
 h2{margin:0;font-size:1.1rem;letter-spacing:-.015em}
 .section-copy{margin:8px 0 20px;color:var(--muted);font-size:.86rem;line-height:1.55}
 .action-grid{display:grid;grid-template-columns:1fr 1fr;gap:11px}
 form{margin:0}
 button{
   position:relative;display:flex;align-items:center;justify-content:center;gap:10px;
   width:100%;min-height:48px;padding:12px 14px;border:1px solid transparent;
   border-radius:12px;color:var(--text);font:inherit;font-size:.86rem;font-weight:800;
   cursor:pointer;transition:transform .16s,border-color .16s,background .16s,opacity .16s
 }
 button:hover:not(:disabled){transform:translateY(-1px)}
 button:focus-visible{outline:3px solid rgba(77,217,232,.28);outline-offset:2px}
 button:disabled{cursor:wait;opacity:.48}
 button.is-loading{opacity:1}
 .primary{background:var(--blue);box-shadow:0 10px 24px rgba(91,140,255,.2)}
 .neutral{border-color:#2b4762;background:#162a40}
 .danger{border-color:rgba(255,107,107,.38);background:rgba(255,107,107,.11);color:#ffc1c1}
 .warning{border-color:rgba(247,185,85,.4);background:rgba(247,185,85,.11);color:#ffd894}
 .button-spinner{
   display:none;width:15px;height:15px;border:2px solid currentColor;
   border-right-color:transparent;border-radius:50%;animation:spin .72s linear infinite
 }
 button.is-loading .button-spinner{display:block}
 .busy{
   display:none;align-items:flex-start;gap:12px;margin-top:14px;padding:14px 15px;
   border:1px solid rgba(77,217,232,.25);border-radius:12px;
   background:rgba(77,217,232,.07);color:#c8f7fb;font-size:.82rem;line-height:1.45
 }
 .busy.on{display:flex}
 .busy .spinner{
   flex:none;width:17px;height:17px;margin-top:1px;border:2px solid var(--cyan);
   border-right-color:transparent;border-radius:50%;animation:spin .72s linear infinite
 }
 .result{
   margin:0 0 16px;padding:15px 16px;border:1px solid rgba(53,208,127,.28);
   border-radius:13px;background:rgba(53,208,127,.07)
 }
 .result.error{border-color:rgba(255,107,107,.34);background:rgba(255,107,107,.08)}
 .result-title{margin:0 0 8px;color:var(--green);font-size:.72rem;font-weight:850;letter-spacing:.1em;text-transform:uppercase}
 .result.error .result-title{color:var(--red)}
 pre{margin:0;color:#dbe8f2;white-space:pre-wrap;font:500 .78rem/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
 .incident-table{width:100%;border-collapse:collapse;font-size:.78rem}
 .incident-table th{padding:0 8px 9px;text-align:left;color:var(--muted);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase}
 .incident-table td{padding:10px 8px;border-top:1px solid var(--line);color:#c8d5e0;vertical-align:top}
 .incident-table th:first-child,.incident-table td:first-child{padding-left:0}
 .incident-table th:last-child,.incident-table td:last-child{padding-right:0}
 .empty{padding:28px 0;color:var(--muted);font-size:.85rem;text-align:center}
 .notes{margin-top:18px;padding:18px 20px;border:1px solid var(--line);border-radius:15px;color:var(--muted);font-size:.78rem;line-height:1.6;background:rgba(7,17,31,.45)}
 .notes strong{color:#d6e4ef}
 code{padding:2px 5px;border-radius:5px;background:#07111f;color:#b8ecf2;font-size:.92em}
 @keyframes spin{to{transform:rotate(360deg)}}
 @media(max-width:820px){
   .topbar{display:block}.dash{margin-top:18px}
   .hero,.workspace{grid-template-columns:1fr}.hero-side{border-left:0;border-top:1px solid var(--line)}
   .stats{grid-template-columns:1fr 1fr}
 }
 @media(max-width:520px){
   .shell{width:min(100% - 20px,1120px);padding-top:24px}
   .hero-main,.hero-side,.actions,.incidents{padding:20px}
   .action-grid{grid-template-columns:1fr}.stats{gap:9px}.stat{padding:16px}
   .stat-value{font-size:1.65rem}
 }
 @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation-duration:.01ms!important;transition:none!important}}
</style>
</head>
<body>
<main class="shell">
  <header class="topbar">
    <div>
      <p class="eyebrow">ALICE observability</p>
      <h1>Operations console</h1>
      <p class="lede">Replay control and live detection-layer telemetry. Every action reports its result after a refresh-safe redirect.</p>
    </div>
    <a class="dash" href="/">Open Cockpit</a>
  </header>

  <section class="panel hero">
    <div class="hero-main">
      <p class="section-label">Documents indexed</p>
      <p class="total" id="total">$count</p>
      <p class="total-caption">Across <code>infologger</code> and <code>generic-log-*</code></p>
    </div>
    <div class="hero-side">
      <span id="pill" class="pill $pill_class">$pill_text</span>
      <div id="connection" class="connection">
        <span class="connection-dot"></span>
        <span id="last-updated">Live status connected</span>
      </div>
      <table class="family-table" id="fam"><tbody>$families</tbody></table>
    </div>
  </section>

  <section class="stats" aria-label="Detection summary">
    <article class="panel stat"><p class="section-label">Open incidents</p><p class="stat-value" id="incidents">$incidents</p></article>
    <article class="panel stat"><p class="section-label">Signals firing</p><p class="stat-value" id="signals">$signals</p></article>
    <article class="panel stat"><p class="section-label">Active alerts</p><p class="stat-value" id="alerts">$alerts</p></article>
    <article class="panel stat"><p class="section-label">Anomalies · 1h</p><p class="stat-value" id="anom">$anomalies</p></article>
  </section>

  <section class="workspace">
    <article class="panel actions">
      <h2>Replay controls</h2>
      <p class="section-copy">Start, stop, or reset the paced S3 feed. Destructive actions ask for confirmation.</p>
      $result
      <div class="action-grid">
        <form method="post" action="replay" data-busy="Starting another paced replay pass.">
          <button class="primary" type="submit" data-loading-label="Starting replay…">
            <span class="button-spinner" aria-hidden="true"></span><span class="button-label">Append replay</span>
          </button>
        </form>
        <form method="post" action="stop" data-busy="Asking the workers to stop the current pass." data-confirm="Stop the replay currently running on the workers?">
          <button class="neutral" type="submit" data-loading-label="Stopping replay…">
            <span class="button-spinner" aria-hidden="true"></span><span class="button-label">Stop replay</span>
          </button>
        </form>
        <form method="post" action="replay-fresh" data-busy="Cancelling any running replay, wiping derived data, rebuilding aliases, and starting a clean reload. This can take up to a minute." data-confirm="Fresh reload deletes the current replayed logs and all derived findings before starting again. Continue?">
          <button class="danger" type="submit" data-loading-label="Resetting and reloading…">
            <span class="button-spinner" aria-hidden="true"></span><span class="button-label">Reload data · fresh</span>
          </button>
        </form>
        <form method="post" action="clear" data-busy="Purging alerts, anomalies, incidents, signals, and trend baselines." data-confirm="Clear all derived findings and trend baselines while keeping the logs?">
          <button class="warning" type="submit" data-loading-label="Clearing findings…">
            <span class="button-spinner" aria-hidden="true"></span><span class="button-label">Clear findings</span>
          </button>
        </form>
      </div>
      <div id="busy" class="busy" role="status" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span><span id="busytext">Working…</span>
      </div>
      <div class="notes">
        A paced load runs for about an hour. One-minute detectors need 32 consecutive windows before leaving initialization. Records keep their archive event time, while detection uses <code>collector_time</code>. <strong>Fresh reload</strong> replaces logs and derived findings; <strong>Clear findings</strong> leaves logs intact.
      </div>
    </article>

    <article class="panel incidents">
      <h2>Open incidents</h2>
      <p class="section-copy">Episodes group signals that share one cause. Source evidence remains in <code>alice-signals</code>.</p>
      <div id="incident-empty" class="empty"$empty_hidden>No open incidents</div>
      <table class="incident-table"$table_hidden>
        <thead><tr><th>Alert</th><th>Severity</th><th>Scope</th><th>Members</th><th>Entities</th></tr></thead>
        <tbody id="inclist">$incident_rows</tbody>
      </table>
    </article>
  </section>
</main>

<script>
function cell(text, className) {
  var el = document.createElement('td');
  el.textContent = text == null ? '' : String(text);
  if (className) { el.className = className; }
  return el;
}
function numberText(value) {
  var parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value);
}
function paint(s) {
  document.getElementById('total').textContent = numberText(s.count);
  document.getElementById('alerts').textContent = s.active_alerts;
  document.getElementById('anom').textContent = s.anomalies_last_hour;
  document.getElementById('incidents').textContent = s.open_incidents;
  document.getElementById('signals').textContent = s.open_signals;

  var incBody = document.getElementById('inclist');
  incBody.replaceChildren();
  for (var j = 0; j < s.incidents.length; j++) {
    var r = s.incidents[j];
    var tr = document.createElement('tr');
    tr.append(cell(r.alertname),cell(r.severity),cell(r.scope),cell(r.members,'n'),cell(r.samples));
    incBody.appendChild(tr);
  }
  var hasIncidents = s.incidents.length > 0;
  document.getElementById('incident-empty').hidden = hasIncidents;
  incBody.closest('table').hidden = !hasIncidents;

  var pill = document.getElementById('pill');
  if (s.replay_running) {
    pill.className = 'pill live';
    pill.textContent = 'Replay running on ' + s.replay_workers + ' worker' + (s.replay_workers === 1 ? '' : 's');
  } else {
    pill.className = 'pill idle';
    pill.textContent = 'No replay running';
  }

  var famBody = document.querySelector('#fam tbody');
  famBody.replaceChildren();
  for (var i = 0; i < s.families.length; i++) {
    var row = document.createElement('tr');
    row.append(cell(s.families[i][0]),cell(numberText(s.families[i][1]),'n'));
    famBody.appendChild(row);
  }
  document.getElementById('connection').className = 'connection';
  document.getElementById('last-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
}
function poll() {
  fetch('status', {cache:'no-store'})
    .then(function (response) {
      if (!response.ok) { throw new Error('status ' + response.status); }
      return response.json();
    })
    .then(paint)
    .catch(function () {
      document.getElementById('connection').className = 'connection error';
      document.getElementById('last-updated').textContent = 'Live status unavailable';
    });
}
function resetButtons() {
  var buttons = document.querySelectorAll('button');
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].disabled = false;
    buttons[i].classList.remove('is-loading');
    buttons[i].removeAttribute('aria-busy');
    var label = buttons[i].querySelector('.button-label');
    if (label && label.dataset.original) { label.textContent = label.dataset.original; }
  }
  document.getElementById('busy').classList.remove('on');
}
var forms = document.querySelectorAll('form[data-busy]');
for (var f = 0; f < forms.length; f++) {
  forms[f].addEventListener('submit', function (event) {
    var question = this.dataset.confirm;
    if (question && !window.confirm(question)) { event.preventDefault(); return; }
    var clicked = this.querySelector('button[type="submit"]');
    var label = clicked.querySelector('.button-label');
    label.dataset.original = label.textContent;
    label.textContent = clicked.dataset.loadingLabel || 'Working…';
    clicked.classList.add('is-loading');
    clicked.setAttribute('aria-busy','true');
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
    document.getElementById('busytext').textContent = this.dataset.busy;
    document.getElementById('busy').classList.add('on');
  });
}
window.addEventListener('pageshow', resetButtons);
if (window.location.search.indexOf('result=') !== -1) {
  window.history.replaceState(null,'',window.location.pathname);
}
setInterval(poll,5000);
</script>
</body>
</html>
""")


def _family_rows(families):
    return "".join(
        f'<tr><td>{html.escape(f)}</td><td class="n">{n:,}</td></tr>'
        for f, n in families)


def _incident_rows(rows):
    return "".join(
        f'<tr><td>{html.escape(str(r["alertname"]))}</td>'
        f'<td>{html.escape(str(r["severity"]))}</td>'
        f'<td>{html.escape(str(r["scope"]))}</td>'
        f'<td class="n">{r["members"]}</td>'
        f'<td>{html.escape(str(r["samples"]))}</td></tr>'
        for r in rows)


def render(result_lines=None):
    result = ""
    if result_lines:
        joined = html.escape("\n".join(result_lines))
        error = any(
            marker in line.upper()
            for line in result_lines
            for marker in ("FAILED", "REFUSED", "ERROR"))
        result = (
            f'<section class="result{" error" if error else ""}" '
            f'role="status"><p class="result-title">'
            f'{"Action needs attention" if error else "Action complete"}</p>'
            f"<pre>{joined}</pre></section>")
    snap = snapshot()
    running = snap["replay_running"]
    has_incidents = bool(snap["incidents"])
    return PAGE.substitute(
        count=html.escape(str(snap["count"])),
        alerts=html.escape(str(snap["active_alerts"])),
        anomalies=html.escape(str(snap["anomalies_last_hour"])),
        incidents=html.escape(str(snap["open_incidents"])),
        signals=html.escape(str(snap["open_signals"])),
        incident_rows=_incident_rows(snap["incidents"]),
        families=_family_rows(snap["families"]),
        pill_class="live" if running else "idle",
        pill_text=(f"Replay running on {snap['replay_workers']} worker(s)"
                   if running else "No replay running"),
        empty_hidden=" hidden" if has_incidents else "",
        table_hidden="" if has_incidents else " hidden",
        result=result)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect_to_result(self, lines):
        token = _store_result(lines)
        location = "/ops/?result=" + urllib.parse.quote(token, safe="")
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/", "/ops"):
            token = urllib.parse.parse_qs(parsed.query).get("result", [""])[0]
            self._send(200, render(_take_result(token)))
        elif path == "/status":
            self._send(200, json.dumps(snapshot()), "application/json")
        else:
            self._send(404, render(["not found"]))

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/replay-fresh"):
            self._redirect_to_result(run_replay(fresh=True))
        elif path.endswith("/replay"):
            self._redirect_to_result(run_replay(fresh=False))
        elif path.endswith("/clear"):
            self._redirect_to_result(clear_only())
        elif path.endswith("/stop"):
            self._redirect_to_result(stop_only())
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
