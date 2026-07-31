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
<meta name="theme-color" content="#0a0b0d">
<title>ALICE — Operations</title>
<style>
 *,*::before,*::after{box-sizing:border-box}
 [hidden]{display:none!important}
 :root{
   color-scheme:dark;
   --bg:#0a0b0d;--panel:#101216;--panel-2:#15181d;--sunk:#0c0e11;
   --hair:#242830;--hair-2:#191c21;
   --text:#e9ebee;--dim:#98a0ab;--faint:#666d78;
   --ok:#5cba8a;--warn:#d8a244;--crit:#e0645d;
   --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
   --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif
 }
 html{-webkit-text-size-adjust:100%}
 body{
   margin:0;min-height:100vh;background:var(--bg);color:var(--text);
   font:400 13px/1.55 var(--sans);-webkit-font-smoothing:antialiased
 }
 .wrap{width:min(1180px,100% - 44px);margin:0 auto}
 .rail{position:fixed;top:0;left:0;right:0;height:2px;background:var(--hair-2);overflow:hidden;z-index:9}
 .rail:after{content:"";position:absolute;top:0;bottom:0;left:0;width:22%;background:var(--ok);opacity:0}
 body[data-replay="on"] .rail:after{opacity:1;animation:sweep 2.8s linear infinite}

 .mast{border-bottom:1px solid var(--hair);background:var(--sunk)}
 .mast-in{display:flex;align-items:center;gap:16px;min-height:58px;padding:11px 0;flex-wrap:wrap}
 .mark{margin:0;font:600 12px/1 var(--mono);letter-spacing:.24em;text-transform:uppercase}
 .mark span{color:var(--faint);font-weight:400}
 .mast-sub{margin:0;padding-left:16px;border-left:1px solid var(--hair);color:var(--dim);font-size:12px}
 .mast-act{display:flex;align-items:center;gap:10px;margin-left:auto}
 .pill{
   display:inline-flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid var(--hair);
   border-radius:3px;font:600 10px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase;color:var(--faint)
 }
 .pill:before{content:"";width:5px;height:5px;border-radius:1px;background:currentColor}
 .pill.live{color:var(--ok);border-color:rgba(92,186,138,.34);background:rgba(92,186,138,.07)}
 .pill.live:before{animation:blink 1.9s ease-in-out infinite}
 .link{
   display:inline-flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid var(--hair);
   border-radius:3px;background:var(--panel);color:var(--text);text-decoration:none;
   font:600 10px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase;
   transition:background .15s,border-color .15s
 }
 .link:after{content:"→";color:var(--faint);font-size:11px;letter-spacing:0}
 .link:hover{background:var(--panel-2);border-color:#343a44}

 main{padding:20px 0 34px}
 .metrics{
   display:grid;grid-template-columns:minmax(0,1.4fr) repeat(4,minmax(0,1fr));
   border:1px solid var(--hair);border-radius:4px;background:var(--panel)
 }
 .metric{padding:17px 20px;border-left:1px solid var(--hair-2);min-width:0}
 .metric:first-child{border-left:0}
 .lbl{margin:0;display:flex;align-items:center;gap:7px;color:var(--faint);
   font:600 10px/1.2 var(--mono);letter-spacing:.15em;text-transform:uppercase}
 .dot{flex:none;width:5px;height:5px;border-radius:1px;background:var(--warn);opacity:0}
 .dot.crit{background:var(--crit)}
 .metric[data-state="active"] .dot{opacity:1}
 .val{margin:13px 0 0;font-size:30px;font-weight:300;line-height:1;letter-spacing:-.022em;font-variant-numeric:tabular-nums}
 .metric:first-child .val{font-size:46px}
 .metric[data-state="zero"] .val{color:var(--faint)}
 .cap{margin:11px 0 0;color:var(--faint);font-size:11.5px;line-height:1.45}

 .controls{margin-top:14px}
 .work{display:grid;grid-template-columns:minmax(0,1.48fr) minmax(300px,1fr);gap:14px;margin-top:14px;align-items:start}
 .work>*{min-width:0}
 .panel{border:1px solid var(--hair);border-radius:4px;background:var(--panel)}
 .phead{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 18px;border-bottom:1px solid var(--hair-2)}
 h2{margin:0;color:var(--dim);font:600 11px/1 var(--mono);letter-spacing:.15em;text-transform:uppercase}
 .meta{color:var(--faint);font:600 10px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}
 .meta b{color:var(--dim);font-weight:600}
 .pbody{padding:18px}

 .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
 form{margin:0}
 button{
   display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;
   width:100%;height:100%;padding:12px 14px;border:1px solid var(--hair);border-radius:3px;
   background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer;
   transition:background .15s,border-color .15s,opacity .15s
 }
 .button-label{display:block;font-size:12.5px;font-weight:600;letter-spacing:-.005em}
 .button-sub{display:block;margin-top:3px;color:var(--faint);font-size:11px;line-height:1.35}
 .primary{background:var(--text);border-color:var(--text);color:#0a0b0d}
 .primary .button-sub{color:rgba(10,11,13,.6)}
 .primary:hover:not(:disabled){background:#fff;border-color:#fff}
 .neutral:hover:not(:disabled){background:var(--panel-2);border-color:#343a44}
 .danger{border-color:rgba(224,100,93,.32);color:#efaba6}
 .danger .button-sub{color:rgba(224,100,93,.66)}
 .danger:hover:not(:disabled){background:rgba(224,100,93,.09);border-color:rgba(224,100,93,.55)}
 .warning{border-color:rgba(216,162,68,.3);color:#eec684}
 .warning .button-sub{color:rgba(216,162,68,.64)}
 .warning:hover:not(:disabled){background:rgba(216,162,68,.08);border-color:rgba(216,162,68,.5)}
 button:focus-visible{outline:2px solid var(--ok);outline-offset:2px}
 button:disabled{cursor:wait;opacity:.4}
 .button-spinner{
   visibility:hidden;width:13px;height:13px;border:1.5px solid currentColor;border-right-color:transparent;
   border-radius:50%;animation:spin .7s linear infinite
 }
 button.is-loading .button-spinner{visibility:visible}

 .busy{
   display:none;align-items:center;gap:10px;margin-top:12px;padding:11px 13px;
   border:1px solid var(--hair);border-left:2px solid var(--ok);border-radius:3px;
   background:var(--sunk);color:var(--dim);font-size:11.5px;line-height:1.45
 }
 .busy.on{display:flex}
 .busy .spinner{
   flex:none;width:13px;height:13px;border:1.5px solid var(--ok);border-right-color:transparent;
   border-radius:50%;animation:spin .7s linear infinite
 }

 .result{margin:0 0 14px;border:1px solid var(--hair);border-left:2px solid var(--ok);border-radius:3px;background:var(--sunk)}
 .result-title{margin:0;padding:10px 13px;border-bottom:1px solid var(--hair-2);color:var(--ok);
   font:600 10px/1 var(--mono);letter-spacing:.15em;text-transform:uppercase}
 .result.error{border-left-color:var(--crit)}
 .result.error .result-title{color:var(--crit)}
 pre{margin:0;padding:13px;max-height:22rem;overflow:auto;color:#c6ced8;white-space:pre-wrap;
   font:400 11.5px/1.65 var(--mono)}

 .scroll{overflow-x:auto}
 table{width:100%;border-collapse:collapse}
 th{padding:0 10px 9px;text-align:left;border-bottom:1px solid var(--hair-2);color:var(--faint);
   font:600 9.5px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;white-space:nowrap}
 td{padding:11px 10px;border-bottom:1px solid var(--hair-2);color:var(--dim);font-size:12px;vertical-align:top}
 tr:last-child td{border-bottom:0}
 th:first-child,td:first-child{padding-left:0}
 th:last-child,td:last-child{padding-right:0}
 td.name{color:var(--text);font:600 11.5px/1.4 var(--mono)}
 td.scope{font:400 11px/1.4 var(--mono)}
 th.n,td.n{text-align:right}
 td.n{color:var(--text);font-variant-numeric:tabular-nums}
 td.entities{color:var(--faint);font:400 11px/1.5 var(--mono);word-break:break-word}
 .tag{display:inline-block;padding:3px 7px;border:1px solid var(--hair);border-radius:2px;color:var(--dim);
   font:600 9.5px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase}
 .tag.crit{color:#efaba6;border-color:rgba(224,100,93,.4);background:rgba(224,100,93,.09)}
 .tag.warn{color:#eec684;border-color:rgba(216,162,68,.36);background:rgba(216,162,68,.08)}
 .empty{padding:22px 14px;border:1px dashed var(--hair);border-radius:3px;text-align:center}
 .empty-t{margin:0;color:var(--faint);font:600 10px/1 var(--mono);letter-spacing:.15em;text-transform:uppercase}
 .empty-s{margin:9px 0 0;color:var(--faint);font-size:11.5px}

 .fam{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 14px;align-items:baseline;padding:0 0 15px}
 .fam:last-child{padding-bottom:0}
 .fam-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--dim);font:400 11.5px/1.3 var(--mono)}
 .fam-n{font-size:12.5px;font-weight:500;font-variant-numeric:tabular-nums;text-align:right}
 .fam-track{grid-column:1/-1;height:4px;background:#1b1f25;border-radius:2px;overflow:hidden}
 .fam-fill{display:block;height:100%;min-width:2px;background:#4f5a69;border-radius:2px}
 .fam:first-child .fam-fill{background:#63707f}
 .fam[data-empty="1"] .fam-n{color:var(--faint)}
 .fam[data-empty="1"] .fam-fill{min-width:0}

 .foot{display:flex;align-items:flex-start;justify-content:space-between;gap:28px;
   margin-top:18px;padding-top:15px;border-top:1px solid var(--hair-2);color:var(--faint);font-size:11.5px;line-height:1.65}
 .foot p{max-width:74ch;margin:0}
 .foot strong{color:var(--dim);font-weight:600}
 .clock{display:flex;align-items:center;gap:7px;white-space:nowrap;color:var(--faint);
   font:400 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase}
 .clock:before{content:"";width:5px;height:5px;border-radius:1px;background:var(--ok)}
 .clock.error{color:var(--crit)}
 .clock.error:before{background:var(--crit)}
 code{padding:1px 5px;border:1px solid var(--hair-2);border-radius:2px;background:var(--sunk);
   color:var(--dim);font-family:var(--mono);font-size:.92em}

 @keyframes spin{to{transform:rotate(360deg)}}
 @keyframes sweep{from{transform:translateX(-100%)}to{transform:translateX(555%)}}
 @keyframes blink{0%,100%{opacity:1}50%{opacity:.28}}

 @media(max-width:1040px){
   .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
   .metric{border-top:1px solid var(--hair-2)}
   .metric:first-child{grid-column:1/-1;border-top:0}
   .metric:nth-child(even){border-left:0}
   .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
   .work{grid-template-columns:minmax(0,1fr)}
 }
 @media(max-width:620px){
   .wrap{width:min(1180px,100% - 28px)}
   .mast-sub{padding-left:0;border-left:0;flex-basis:100%}
   .mast-act{margin-left:0}
   .grid{grid-template-columns:1fr}
   .metric:first-child .val{font-size:38px}
   .val{font-size:26px}
   .foot{display:block}
   .clock{margin-top:12px}
 }
 @media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;animation-iteration-count:1!important;transition:none!important}}
</style>
</head>
<body data-replay="$body_state">
<div class="rail" aria-hidden="true"></div>

<header class="mast">
  <div class="wrap mast-in">
    <h1 class="mark">ALICE <span>/ Operations</span></h1>
    <p class="mast-sub">Replay control and detection telemetry</p>
    <div class="mast-act">
      <span id="pill" class="pill $pill_class">$pill_text</span>
      <a class="link" href="/">Cockpit</a>
    </div>
  </div>
</header>

<main class="wrap">
  <section class="metrics" aria-label="Live counters">
    <article class="metric" data-state="$count_state">
      <p class="lbl">Documents indexed</p>
      <p class="val" id="total">$count</p>
      <p class="cap">Across <code>infologger</code> and <code>generic-log-*</code></p>
    </article>
    <article class="metric" data-state="$incidents_state">
      <p class="lbl"><span class="dot crit"></span>Open incidents</p>
      <p class="val" id="incidents">$incidents</p>
    </article>
    <article class="metric" data-state="$signals_state">
      <p class="lbl"><span class="dot"></span>Signals firing</p>
      <p class="val" id="signals">$signals</p>
    </article>
    <article class="metric" data-state="$alerts_state">
      <p class="lbl"><span class="dot"></span>Active alerts</p>
      <p class="val" id="alerts">$alerts</p>
    </article>
    <article class="metric" data-state="$anomalies_state">
      <p class="lbl"><span class="dot"></span>Anomalies · 1h</p>
      <p class="val" id="anom">$anomalies</p>
    </article>
  </section>

  <section class="panel controls">
      <div class="phead">
        <h2>Replay control</h2>
        <span class="meta">$worker_meta</span>
      </div>
      <div class="pbody">
        $result
        <div class="grid">
          <form method="post" action="replay" data-busy="Starting another paced replay pass.">
            <button class="primary" type="submit" data-loading-label="Starting replay…">
              <span><span class="button-label">Append replay</span><span class="button-sub">Adds one more paced pass to the data already loaded.</span></span>
              <span class="button-spinner" aria-hidden="true"></span>
            </button>
          </form>
          <form method="post" action="stop" data-busy="Asking the workers to stop the current pass." data-confirm="Stop the replay currently running on the workers?">
            <button class="neutral" type="submit" data-loading-label="Stopping replay…">
              <span><span class="button-label">Stop replay</span><span class="button-sub">Ends the pass after the record in flight. Data stays.</span></span>
              <span class="button-spinner" aria-hidden="true"></span>
            </button>
          </form>
          <form method="post" action="replay-fresh" data-busy="Cancelling any running replay, wiping derived data, rebuilding aliases, and starting a clean reload. This can take up to a minute." data-confirm="Fresh reload deletes the current replayed logs and all derived findings before starting again. Continue?">
            <button class="danger" type="submit" data-loading-label="Resetting and reloading…">
              <span><span class="button-label">Reload data · fresh</span><span class="button-sub">Deletes logs and findings, rebuilds aliases, reloads.</span></span>
              <span class="button-spinner" aria-hidden="true"></span>
            </button>
          </form>
          <form method="post" action="clear" data-busy="Purging alerts, anomalies, incidents, signals, and trend baselines." data-confirm="Clear all derived findings and trend baselines while keeping the logs?">
            <button class="warning" type="submit" data-loading-label="Clearing findings…">
              <span><span class="button-label">Clear findings</span><span class="button-sub">Drops alerts, incidents and baselines. Keeps the logs.</span></span>
              <span class="button-spinner" aria-hidden="true"></span>
            </button>
          </form>
        </div>
        <div id="busy" class="busy" role="status" aria-live="polite">
          <span class="spinner" aria-hidden="true"></span><span id="busytext">Working…</span>
        </div>
      </div>
  </section>

  <div class="work">
    <section class="panel">
      <div class="phead">
        <h2>Open incidents</h2>
        <span class="meta" id="inc-meta">$incident_meta</span>
      </div>
      <div class="pbody">
        <div id="incident-empty" class="empty"$empty_hidden>
          <p class="empty-t">Nothing firing</p>
          <p class="empty-s">Episodes appear here as soon as a detector opens one.</p>
        </div>
        <div class="scroll"$table_hidden>
          <table>
            <thead><tr><th>Alert</th><th>Severity</th><th>Scope</th><th class="n">Members</th><th>Entities</th></tr></thead>
            <tbody id="inclist">$incident_rows</tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="phead">
        <h2>Ingest by family</h2>
        <span class="meta">Total <b id="fam-total">$family_total</b></span>
      </div>
      <div class="pbody"><div id="fam">$families</div></div>
    </section>
  </div>

  <footer class="foot">
    <p>A paced load runs for about an hour. One-minute detectors need 32 consecutive windows before they
      leave initialization. Records keep their archive event time, while detection reads the field
      <code>collector_time</code> instead. <strong>Every action reports its result after a refresh-safe
      redirect, so a page reload never repeats it.</strong></p>
    <div id="connection" class="clock"><span id="last-updated">Live status connected</span></div>
  </footer>
</main>

<script>
var SEVERITY = {
  critical:'crit', crit:'crit', fatal:'crit', error:'crit', high:'crit', p1:'crit',
  warning:'warn', warn:'warn', medium:'warn', moderate:'warn', p2:'warn'
};
function severityClass(value) {
  return SEVERITY[String(value == null ? '' : value).toLowerCase()] || '';
}
function numberText(value) {
  var text = String(value == null ? '' : value);
  if (text === '' || /[^0-9]/.test(text)) { return text; }
  return text.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
}
function cell(text, className) {
  var el = document.createElement('td');
  el.textContent = text == null ? '' : String(text);
  if (className) { el.className = className; }
  return el;
}
function severityCell(value) {
  var el = document.createElement('td');
  var tag = document.createElement('span');
  tag.className = ('tag ' + severityClass(value)).trim();
  tag.textContent = value == null ? '' : String(value);
  el.appendChild(tag);
  return el;
}
function setMetric(id, value) {
  var el = document.getElementById(id);
  el.textContent = numberText(value);
  var owner = el.closest('.metric');
  if (owner) { owner.dataset.state = Number(value) > 0 ? 'active' : 'zero'; }
}
function paintIncidents(rows) {
  var body = document.getElementById('inclist');
  body.replaceChildren();
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var tr = document.createElement('tr');
    tr.append(cell(r.alertname, 'name'), severityCell(r.severity), cell(r.scope, 'scope'),
              cell(r.members, 'n'), cell(r.samples, 'entities'));
    body.appendChild(tr);
  }
  document.getElementById('incident-empty').hidden = rows.length > 0;
  body.closest('.scroll').hidden = rows.length === 0;
  document.getElementById('inc-meta').textContent =
    rows.length ? rows.length + ' shown' : 'None firing';
}
function paintFamilies(families) {
  var box = document.getElementById('fam');
  box.replaceChildren();
  var top = 0, total = 0, i;
  for (i = 0; i < families.length; i++) {
    var n = Number(families[i][1]) || 0;
    total += n;
    if (n > top) { top = n; }
  }
  for (i = 0; i < families.length; i++) {
    var count = Number(families[i][1]) || 0;
    var row = document.createElement('div');
    row.className = 'fam';
    if (!count) { row.dataset.empty = '1'; }
    var name = document.createElement('span');
    name.className = 'fam-name';
    name.textContent = families[i][0];
    name.title = families[i][0];
    var value = document.createElement('span');
    value.className = 'fam-n';
    value.textContent = numberText(count);
    var track = document.createElement('span');
    track.className = 'fam-track';
    var fill = document.createElement('span');
    fill.className = 'fam-fill';
    fill.style.width = (top ? Math.max(count ? 2 : 0, Math.round(count / top * 100)) : 0) + '%';
    track.appendChild(fill);
    row.append(name, value, track);
    box.appendChild(row);
  }
  document.getElementById('fam-total').textContent = numberText(total);
}
function paint(s) {
  setMetric('total', s.count);
  setMetric('alerts', s.active_alerts);
  setMetric('anom', s.anomalies_last_hour);
  setMetric('incidents', s.open_incidents);
  setMetric('signals', s.open_signals);
  paintIncidents(s.incidents);
  paintFamilies(s.families);

  var pill = document.getElementById('pill');
  if (s.replay_running) {
    pill.className = 'pill live';
    pill.textContent = 'Replay active · ' + s.replay_workers +
      ' worker' + (s.replay_workers === 1 ? '' : 's');
  } else {
    pill.className = 'pill idle';
    pill.textContent = 'Replay idle';
  }
  document.body.dataset.replay = s.replay_running ? 'on' : 'off';

  document.getElementById('connection').className = 'clock';
  document.getElementById('last-updated').textContent =
    'Updated ' + new Date().toLocaleTimeString([], {hour12:false});
}
function poll() {
  fetch('status', {cache:'no-store'})
    .then(function (response) {
      if (!response.ok) { throw new Error('status ' + response.status); }
      return response.json();
    })
    .then(paint)
    .catch(function () {
      document.getElementById('connection').className = 'clock error';
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

SEVERITY_TAGS = {
    "critical": "crit", "crit": "crit", "fatal": "crit", "error": "crit",
    "high": "crit", "p1": "crit", "warning": "warn", "warn": "warn",
    "medium": "warn", "moderate": "warn", "p2": "warn",
}


def _fmt(value):
    text = str(value)
    return "{:,}".format(int(text)) if text.isdigit() else text


def _num_state(value):
    text = str(value)
    return "active" if text.isdigit() and int(text) > 0 else "zero"


def _severity_class(value):
    return SEVERITY_TAGS.get(str(value).strip().lower(), "")


def _family_rows(families):
    top = max([n for _, n in families] or [0])
    rows = []
    for name, n in families:
        share = round(n / top * 100) if top else 0
        if n and share < 2:
            share = 2
        empty = "" if n else ' data-empty="1"'
        safe = html.escape(str(name))
        rows.append(
            f'<div class="fam"{empty}>'
            f'<span class="fam-name" title="{safe}">{safe}</span>'
            f'<span class="fam-n">{n:,}</span>'
            f'<span class="fam-track">'
            f'<span class="fam-fill" style="width:{share}%"></span>'
            f'</span></div>')
    return "".join(rows)


def _incident_rows(rows):
    out = []
    for r in rows:
        tag = _severity_class(r["severity"])
        klass = ("tag " + tag).strip()
        out.append(
            f'<tr><td class="name">{html.escape(str(r["alertname"]))}</td>'
            f'<td><span class="{klass}">'
            f'{html.escape(str(r["severity"]))}</span></td>'
            f'<td class="scope">{html.escape(str(r["scope"]))}</td>'
            f'<td class="n">{r["members"]}</td>'
            f'<td class="entities">{html.escape(str(r["samples"]))}</td></tr>')
    return "".join(out)


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
    workers = snap["replay_workers"]
    incidents = snap["incidents"]
    configured = len(WORKERS)
    return PAGE.substitute(
        body_state="on" if running else "off",
        count=_fmt(snap["count"]),
        count_state=_num_state(snap["count"]),
        alerts=_fmt(snap["active_alerts"]),
        alerts_state=_num_state(snap["active_alerts"]),
        anomalies=_fmt(snap["anomalies_last_hour"]),
        anomalies_state=_num_state(snap["anomalies_last_hour"]),
        incidents=_fmt(snap["open_incidents"]),
        incidents_state=_num_state(snap["open_incidents"]),
        signals=_fmt(snap["open_signals"]),
        signals_state=_num_state(snap["open_signals"]),
        incident_rows=_incident_rows(incidents),
        incident_meta=(f"{len(incidents)} shown" if incidents
                       else "None firing"),
        families=_family_rows(snap["families"]),
        family_total=_fmt(sum(n for _, n in snap["families"])),
        worker_meta=(f"{configured} worker" + ("" if configured == 1 else "s")
                     if configured else "No workers configured"),
        pill_class="live" if running else "idle",
        pill_text=(f"Replay active · {workers} worker"
                   + ("" if workers == 1 else "s")
                   if running else "Replay idle"),
        empty_hidden=" hidden" if incidents else "",
        table_hidden="" if incidents else " hidden",
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
