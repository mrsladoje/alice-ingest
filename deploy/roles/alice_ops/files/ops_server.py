import html
import json
import os
import re
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
POISON_SERVICE = os.environ.get(
    "OPS_POISON_SERVICE", "alice-poison-replay")
POISON_STATUS = os.environ.get(
    "OPS_POISON_STATUS", "/var/lib/alice-poison-replay/status.json")
INJECT_SERVICE = os.environ.get("OPS_INJECT_SERVICE", "alice-inject")
INJECT_STATUS = os.environ.get(
    "OPS_INJECT_STATUS", "/var/lib/alice-inject/status.json")
INJECT_REQUEST = os.environ.get(
    "OPS_INJECT_REQUEST", "/var/lib/alice-inject/request.json")
INJECT_WORKERS = [
    w for w in os.environ.get("OPS_INJECT_WORKERS", "").split(",") if w]
INJECT_OBSERVE_DEFAULT = int(
    os.environ.get("OPS_INJECT_OBSERVE_MINUTES", "45"))
INJECT_SCENARIOS = (
    ("kill-fluent-bit",
     "Stops one collector's Fluent Bit. Sets group_wait."),
    ("drop-epn-stream",
     "Silences one EPN at ingest. Scores independent-event recall."),
    ("cpu-stress-worker",
     "Saturates one collector's CPUs. Shipping lag before drops."),
    ("stop-alice-metrics",
     "Stops the thin poller. Does blindness read as health?"),
    ("stop-projector",
     "Stops the projector on its node. The re-send contract."),
    ("replay-end",
     "Ends one worker's replay. Mass-silence classification."),
    ("observe-only",
     "Injects nothing. Measures the stack as it stands."),
)
INJECT_WORKER_SCENARIOS = {
    "kill-fluent-bit", "cpu-stress-worker", "replay-end"}
INJECT_LIVE_STATES = {
    "starting", "injecting", "observing", "restoring", "scoring"}
RESET_TIMEOUT = float(os.environ.get("OPS_RESET_TIMEOUT", "900"))
COUNT_TARGET = "infologger,application-logs-*"
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


_JOB_LOCK = threading.Lock()
_JOB = None


def _job_view(job):
    finished = job["finished"] or time.time()
    return {
        "id": job["id"],
        "action": job["action"],
        "label": job["label"],
        "state": job["state"],
        "lines": list(job["lines"]),
        "seconds": int(finished - job["started"]),
    }


def job_status():
    with _JOB_LOCK:
        return _job_view(_JOB) if _JOB else None


def start_job(action, label, work):
    """Run one action in the background so the POST answers immediately."""
    global _JOB
    with _JOB_LOCK:
        if _JOB and _JOB["state"] == "running":
            return None, _JOB["label"]
        job = {
            "id": secrets.token_urlsafe(9),
            "action": action,
            "label": label,
            "state": "running",
            "lines": [],
            "started": time.time(),
            "finished": None,
        }
        _JOB = job

    def run():
        try:
            work(job["lines"])
        except Exception as exc:
            job["lines"].append(
                f"FAILED — {action} stopped with an unexpected error: {exc}")
        with _JOB_LOCK:
            job["finished"] = time.time()
            job["state"] = "done"

    threading.Thread(target=run, daemon=True).start()
    return job, None


def _log_families():
    return ["infologger", "application-logs-central"] + [
        f"application-logs-local-{n}" for n in INFO_NODES]


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


def reset_derived(mode="full", lines=None):
    lines = [] if lines is None else lines
    try:
        proc = subprocess.Popen(
            ["/usr/bin/python3", RESET_SCRIPT],
            env=dict(os.environ, OS_URL=OS_URL, MODE=mode,
                     LOG_FAMILIES=",".join(LOG_FAMILIES)),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except Exception as e:
        lines.append(f"FAILED to run {RESET_SCRIPT}: {e} — old alerts, "
                     f"anomalies and trend-rollup rows are still there")
        return lines
    killer = threading.Timer(RESET_TIMEOUT, proc.kill)
    killer.start()
    tail = []
    try:
        for raw in proc.stdout:
            text = raw.rstrip()
            if text.strip():
                lines.append(text)
                tail.append(text)
                del tail[:-4]
        proc.wait()
    finally:
        killer.cancel()
        proc.stdout.close()
    if proc.returncode != 0:
        lines.append(
            f"reset finished with exit {proc.returncode} — stale alerts "
            f"or rollup rows may survive this reload: "
            + " / ".join(tail)[-300:])
    return lines


def wipe(lines=None):
    lines = [] if lines is None else lines
    patterns = ["infologger-*", "application-logs-central-*"]
    patterns += [f"application-logs-local-{n}-*" for n in INFO_NODES]
    for pat in patterns:
        code, _ = _req(
            "DELETE",
            f"{OS_URL}/{pat}?ignore_unavailable=true&allow_no_indices=true")
        lines.append(f"delete {pat}: HTTP {code}")
    reset_derived("full", lines)
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


def _service_active(name):
    try:
        proc = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", name],
            timeout=5, check=False)
        return proc.returncode == 0
    except Exception:
        return False


def poison_status():
    running = _service_active(POISON_SERVICE)
    status = {"state": "never-run", "running": running}
    try:
        with open(POISON_STATUS) as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            status.update(loaded)
    except FileNotFoundError:
        pass
    except Exception as exc:
        status["status_error"] = str(exc)
    if not running and status.get("state") in {
            "starting", "warming", "injecting", "observing", "stopping"}:
        status["interrupted_state"] = status["state"]
        status["state"] = "interrupted"
    status["running"] = running
    return status


def start_poison(lines=None):
    lines = [] if lines is None else lines
    if _service_active(POISON_SERVICE):
        lines.append(
            "REFUSED — a poison replay is already running. Follow its live "
            "matrix below or stop it before starting another one.")
        return lines
    if _service_active(INJECT_SERVICE):
        lines.append(
            "REFUSED — a fault injection is running. Injected documents "
            "inside its scoring window would be counted as the fault's own "
            "evidence. Wait for it, or stop it first.")
        return lines
    try:
        subprocess.run(
            ["/usr/bin/systemctl", "reset-failed", POISON_SERVICE],
            capture_output=True, text=True, timeout=10, check=False)
        proc = subprocess.run(
            ["/usr/bin/systemctl", "--no-block", "start", POISON_SERVICE],
            capture_output=True, text=True, timeout=10, check=False)
    except Exception as exc:
        lines.append(f"FAILED to start {POISON_SERVICE}: {exc}")
        return lines
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown systemd error").strip()
        lines.append(f"FAILED to start {POISON_SERVICE}: {detail}")
        return lines
    lines += [
        "poison replay started in the background",
        "it will start/continue the paced S3 replay, wait until all ten "
        "one-minute detectors are trained, inject labelled outlier windows, "
        "and score native results plus projected episodes",
        "the 30-minute detectors are deliberately excluded; progress updates "
        "on this page every five seconds",
    ]
    return lines


def stop_poison(lines=None):
    lines = [] if lines is None else lines
    if not _service_active(POISON_SERVICE):
        lines.append("no poison replay is running")
        return lines
    try:
        proc = subprocess.run(
            ["/usr/bin/systemctl", "stop", POISON_SERVICE],
            capture_output=True, text=True, timeout=45, check=False)
    except Exception as exc:
        lines.append(f"FAILED to stop {POISON_SERVICE}: {exc}")
        return lines
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown systemd error").strip()
        lines.append(f"FAILED to stop {POISON_SERVICE}: {detail}")
        return lines
    lines.append(
        "poison replay stopped; its already-indexed labelled evidence remains "
        "available for audit and expires with the normal index retention")
    return lines


def inject_status():
    running = _service_active(INJECT_SERVICE)
    status = {"state": "never-run", "running": running}
    try:
        with open(INJECT_STATUS) as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            status.update(loaded)
    except FileNotFoundError:
        pass
    except Exception as exc:
        status["status_error"] = str(exc)
    if not running and status.get("state") in INJECT_LIVE_STATES:
        status["interrupted_state"] = status["state"]
        status["state"] = "interrupted"
    status["running"] = running
    return status


def _inject_request(params):
    scenario = (params.get("scenario") or "").strip()
    known = {name for name, _ in INJECT_SCENARIOS}
    if scenario not in known:
        return None, [
            f"REFUSED — '{scenario}' is not a scenario. Pick one of: "
            + ", ".join(sorted(known)) + "."]
    raw_minutes = (params.get("observe_minutes")
                   or str(INJECT_OBSERVE_DEFAULT)).strip()
    try:
        minutes = int(raw_minutes)
    except ValueError:
        return None, [
            f"REFUSED — observe minutes must be a whole number, not "
            f"'{raw_minutes}'."]
    if minutes < 1 or minutes > 720:
        return None, [
            "REFUSED — observe minutes must be between 1 and 720. The "
            "observation window is the measurement, so it is never zero."]
    entity = (params.get("independent_entity") or "").strip()
    if scenario == "drop-epn-stream":
        if not entity:
            return None, [
                "REFUSED — drop-epn-stream needs the EPN host it is supposed "
                "to silence. That host is what the scenario silences and what "
                "independent-event recall is then scored against; without it "
                "the run cannot establish the metric it exists for."]
        if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", entity):
            return None, [
                f"REFUSED — '{entity}' is not a host name. It is compared "
                f"inside an ingest pipeline condition, so it may only hold "
                f"letters, digits, dots, dashes and underscores."]
    target = (params.get("target_worker") or "").strip()
    if scenario in INJECT_WORKER_SCENARIOS:
        if not target and INJECT_WORKERS:
            target = INJECT_WORKERS[0]
        if target not in INJECT_WORKERS:
            return None, [
                f"REFUSED — {scenario} needs a target worker. Known workers: "
                + (", ".join(INJECT_WORKERS) or "none configured") + "."]
    else:
        target = ""
    return {
        "scenario": scenario,
        "observe_minutes": minutes,
        "target_worker": target,
        "independent_entity": entity,
        "strict": (params.get("strict") or "").lower() in ("true", "on", "1"),
        "restore": (params.get("restore") or "true").lower()
                   in ("true", "on", "1"),
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested_by": "ops-page",
    }, []


def start_inject(lines=None, params=None):
    lines = [] if lines is None else lines
    request, refusal = _inject_request(params or {})
    if refusal:
        lines += refusal
        return lines
    if _service_active(INJECT_SERVICE):
        lines.append(
            "REFUSED — an injection is already running. Follow it below, or "
            "stop it before starting another one. Two faults at once make "
            "every metric on the scorecard unattributable.")
        return lines
    if _service_active(POISON_SERVICE):
        lines.append(
            "REFUSED — the poison calibration run is still going. Its "
            "labelled outliers would land inside this run's scoring window "
            "and both scorecards would be unreadable. Stop it first.")
        return lines
    try:
        os.makedirs(os.path.dirname(INJECT_REQUEST), exist_ok=True)
        tmp = f"{INJECT_REQUEST}.tmp"
        with open(tmp, "w") as handle:
            json.dump(request, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, INJECT_REQUEST)
    except Exception as exc:
        lines.append(f"FAILED to write the injection request: {exc}")
        return lines
    try:
        subprocess.run(
            ["/usr/bin/systemctl", "reset-failed", INJECT_SERVICE],
            capture_output=True, text=True, timeout=10, check=False)
        proc = subprocess.run(
            ["/usr/bin/systemctl", "--no-block", "start", INJECT_SERVICE],
            capture_output=True, text=True, timeout=10, check=False)
    except Exception as exc:
        lines.append(f"FAILED to start {INJECT_SERVICE}: {exc}")
        return lines
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown systemd error").strip()
        lines.append(f"FAILED to start {INJECT_SERVICE}: {detail}")
        return lines
    lines += [
        f"injection started: {request['scenario']}"
        + (f" on {request['target_worker']}" if request["target_worker"]
           else ""),
        f"it injects now, observes for {request['observe_minutes']} minutes, "
        f"restores what it stopped, and then scores the seven metrics",
        "progress and the scorecard appear on this page; leaving the page "
        "does not stop the run",
    ]
    return lines


def stop_inject(lines=None, params=None):
    lines = [] if lines is None else lines
    if not _service_active(INJECT_SERVICE):
        lines.append("no injection is running")
        return lines
    try:
        proc = subprocess.run(
            ["/usr/bin/systemctl", "stop", INJECT_SERVICE],
            capture_output=True, text=True, timeout=300, check=False)
    except Exception as exc:
        lines.append(f"FAILED to stop {INJECT_SERVICE}: {exc}")
        return lines
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown systemd error").strip()
        lines.append(f"FAILED to stop {INJECT_SERVICE}: {detail}")
        return lines
    lines.append(
        "injection stopped; it ends the observation window early, still "
        "restores what it stopped, and still scores the shortened window")
    return lines


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
        "poison": poison_status(),
        "inject": inject_status(),
        "families": family_counts(),
        "job": job_status(),
    }


def stop_replays(workers, lines=None):
    lines = [] if lines is None else lines
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


def trigger_workers(lines=None):
    lines = [] if lines is None else lines
    if not WORKERS:
        lines.append("no worker triggers configured")
    for w in WORKERS:
        code, resp = _req("POST", f"{w}/replay?family={FAMILIES}")
        lines.append(f"replay {w}: HTTP {code} {resp.strip()}")
    return lines


def stop_only(lines=None):
    lines = [] if lines is None else lines
    busy = replay_in_flight()
    if not busy:
        lines.append("no replay is running")
        return lines
    stop_replays(busy, lines)
    lines.append(
        "stop requested — the workers finish the record they are on and then "
        "end the pass; the indices keep everything shipped so far")
    return lines


def clear_only(lines=None):
    return reset_derived("clear", lines)


def _quiet_the_workers(lines, button):
    busy = replay_in_flight()
    if not busy:
        return True
    lines.append(
        "a replay was already running on " + ", ".join(busy)
        + " — cancelling it first, since the indices may not be wiped while "
          "records are still arriving")
    stop_replays(busy, lines)
    if not wait_until_idle():
        lines += [
            "REFUSED — it did not stop within 45s, so nothing was wiped. "
            "Wiping while a load is in flight empties the indices without "
            "stopping the load, and lets ingest recreate the log indices "
            "with the wrong mapping.",
            f"Restart alice-replay on those workers, then press {button} "
            f"again.",
        ]
        return False
    lines.append("previous replay stopped")
    return True


def run_replay(fresh, lines=None):
    lines = [] if lines is None else lines
    if fresh:
        if not _quiet_the_workers(lines, "Reload data (fresh)"):
            return lines
        wipe(lines)
    trigger_workers(lines)
    return lines


def wipe_only(lines=None):
    lines = [] if lines is None else lines
    if not _quiet_the_workers(lines, "Delete logs"):
        return lines
    wipe(lines)
    lines.append(
        "the indices are empty, the write aliases are rebuilt, and nothing "
        "is loading — the next replay you start is the only data in there, "
        "so this is the clean baseline")
    return lines


ACTIONS = {
    "replay": ("Starting another paced replay pass",
               lambda lines, params: run_replay(False, lines)),
    "replay-fresh": ("Cancelling any running replay, wiping derived data, "
                     "rebuilding aliases and starting a clean reload",
                     lambda lines, params: run_replay(True, lines)),
    "stop": ("Asking the workers to stop the current pass",
             lambda lines, params: stop_only(lines)),
    "wipe": ("Stopping any replay, deleting the logs and everything derived "
             "from them, and rebuilding the aliases",
             lambda lines, params: wipe_only(lines)),
    "clear": ("Purging alerts, anomalies, incidents, signals and trend "
              "baselines", lambda lines, params: clear_only(lines)),
    "poison-replay": ("Starting the background detector calibration run",
                      lambda lines, params: start_poison(lines)),
    "poison-stop": ("Stopping the poison calibration process cleanly",
                    lambda lines, params: stop_poison(lines)),
    "inject": ("Arming and starting the fault-injection run", start_inject),
    "inject-stop": ("Stopping the fault-injection run", stop_inject),
}


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

 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}
 .grid.docs{grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;align-items:start}
 .act{display:flex;flex-direction:column;gap:0}
 .act .doc{
   margin:0;padding:9px 12px;border:1px solid var(--hair);border-top:none;
   border-radius:0 0 3px 3px;color:var(--faint);font-size:11px;line-height:1.5
 }
 .act form button{border-radius:3px 3px 0 0}
 .actgroup{margin-top:16px}
 .actgroup:first-of-type{margin-top:0}
 .actgroup>h3{
   margin:0 0 8px;font-size:11px;font-weight:600;letter-spacing:.08em;
   text-transform:uppercase;color:var(--faint)
 }
 .actgroup.destructive{
   margin-top:22px;padding:14px;border:1px solid rgba(224,100,93,.28);
   border-radius:3px;background:rgba(224,100,93,.04)
 }
 .actgroup.destructive>h3{color:#efaba6}
 .actgroup.destructive>h3 .note{
   display:block;margin-top:4px;font-weight:400;letter-spacing:0;
   text-transform:none;color:rgba(224,100,93,.72)
 }
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

 .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:12px}
 .field{display:flex;flex-direction:column;gap:6px;min-width:0}
 .field-l{color:var(--faint);font:600 9.5px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase}
 select,input[type="number"],input[type="text"]{
   width:100%;padding:9px 10px;border:1px solid var(--hair);border-radius:3px;
   background:var(--sunk);color:var(--text);font:400 12px/1.3 var(--sans);
   -webkit-appearance:none;appearance:none
 }
 select{background-image:none}
 select:focus-visible,input:focus-visible{outline:2px solid var(--ok);outline-offset:1px}
 input:disabled,select:disabled{opacity:.4;cursor:not-allowed}
 .field.check{flex-direction:row;align-items:center;gap:9px;align-self:end;padding-bottom:9px}
 .field.check input{width:14px;height:14px;accent-color:#5cba8a}
 .field.check span{color:var(--dim);font-size:11.5px}
 .field-hint{color:var(--faint);font-size:10.5px;line-height:1.35}

 .card{margin-top:14px;border:1px solid var(--hair);border-radius:3px;background:var(--sunk)}
 .card-h{display:flex;align-items:center;justify-content:space-between;gap:12px;
   padding:10px 13px;border-bottom:1px solid var(--hair-2)}
 .card-t{margin:0;color:var(--faint);font:600 10px/1 var(--mono);letter-spacing:.15em;text-transform:uppercase}
 .card-b{padding:6px 13px 13px}
 .verdict{display:inline-flex;align-items:center;gap:7px;padding:5px 9px;border:1px solid var(--hair);
   border-radius:2px;font:600 9.5px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
 .verdict.pass{color:var(--ok);border-color:rgba(92,186,138,.4);background:rgba(92,186,138,.08)}
 .verdict.fail{color:#efaba6;border-color:rgba(224,100,93,.4);background:rgba(224,100,93,.09)}
 td.metric-v{color:var(--text)}
 td.metric-v.bad{color:#efaba6}
 td.metric-v.good{color:var(--ok)}
 .fails{margin:10px 0 0;padding:0 0 0 17px;color:#efaba6;font-size:11.5px;line-height:1.55}
 .fails li{margin-top:5px}

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
      <p class="cap">Across <code>infologger</code> and <code>application-logs-*</code></p>
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
        <div id="result-slot">$result</div>
        <div class="actgroup">
          <h3>Safe — nothing is deleted</h3>
          <div class="grid docs">
            <div class="act">
              <form method="post" action="replay" data-busy="Starting another paced replay pass.">
                <button class="primary" type="submit" data-loading-label="Starting replay…">
                  <span><span class="button-label">Append replay</span><span class="button-sub">Adds one more paced pass to the data already loaded.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Loads the archive again on top of what is already there. Deletes
              nothing. Takes about an hour, because the load is paced so the detectors get
              enough separate minutes to learn from. Safe while other people are using the
              system; they will see the record count climb. If it stops partway, press it
              again — a second pass only adds more.</p>
            </div>
            <div class="act">
              <form method="post" action="stop" data-busy="Asking the workers to stop the current pass." data-confirm="Stop the replay currently running on the workers?">
                <button class="neutral" type="submit" data-loading-label="Stopping replay…">
                  <span><span class="button-label">Stop replay</span><span class="button-sub">Ends the pass after the record in flight. Data stays.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Ends the pass that is running now. Deletes nothing — every record
              already loaded stays. Takes a few seconds. Safe at any time. If it says no replay
              is running, there was nothing to stop and nothing went wrong.</p>
            </div>
            <div class="act">
              <form method="post" action="poison-replay" data-busy="Starting the background detector calibration run." data-confirm="Inject labelled synthetic outliers into trained production detector lanes? The evidence remains until normal index retention removes it.">
                <button id="poison-start" class="warning" type="submit" data-loading-label="Starting poison…">
                  <span><span class="button-label">Poison replay</span><span id="poison-status" class="button-sub">$poison_text</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Adds fake, clearly labelled bad records so you can check the
              detectors still catch things. Deletes nothing, but it does write into the real
              indices, and those records stay until normal retention removes them. It waits for
              a paced load to finish training first, so it can take over an hour before
              anything happens. Do not run it while someone is reading a result, because it
              will raise real alerts. If it fails, press Stop poison, then start it again.</p>
            </div>
            <div class="act">
              <form method="post" action="poison-stop" data-busy="Stopping the poison calibration process cleanly.">
                <button id="poison-stop" class="neutral" type="submit" data-loading-label="Stopping poison…"$poison_stop_disabled>
                  <span><span class="button-label">Stop poison</span><span class="button-sub">Cancels warm-up or observation. Indexed evidence stays labelled.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Stops the poison run. Deletes nothing — the fake records it already
              wrote stay, and stay labelled as fake. Takes a few seconds. Safe at any time. If
              the button is greyed out, nothing is running.</p>
            </div>
          </div>
        </div>

        <div class="actgroup destructive">
          <h3>Destructive — these delete data
            <span class="note">Read the paragraph under a button before you press it. None of this can be undone, and the staging machines are shared.</span>
          </h3>
          <div class="grid docs">
            <div class="act">
              <form method="post" action="clear" data-busy="Purging alerts, anomalies, incidents, signals, and trend baselines." data-confirm="Clear all derived findings and trend baselines while keeping the logs?">
                <button class="warning" type="submit" data-loading-label="Clearing findings…">
                  <span><span class="button-label">Clear findings</span><span class="button-sub">Drops alerts, incidents and baselines. Keeps the logs.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Deletes every alert, anomaly, incident, signal and trend baseline.
              Keeps all the logs. Takes a few seconds. Do not run it while someone is looking at
              an incident — it will vanish from under them. The detectors then have to learn
              again, so the system says little for the next half hour. If it fails partway, run
              it again; it is safe to repeat.</p>
            </div>
            <div class="act">
              <form method="post" action="replay-fresh" data-busy="Cancelling any running replay, wiping derived data, rebuilding aliases, and starting a clean reload. This can take up to a minute." data-confirm="Fresh reload deletes the current replayed logs and all derived findings before starting again. Continue?">
                <button class="danger" type="submit" data-loading-label="Resetting and reloading…">
                  <span><span class="button-label">Reload data · fresh</span><span class="button-sub">Deletes logs and findings, rebuilds aliases, reloads.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Deletes every replayed log and everything found from them, then
              loads the archive again from the start. Takes about an hour. Do not run this while
              someone is looking at a result they care about, and remember physicists run test
              runs through the staging control system. If it stops partway, run it again; it
              always starts clean.</p>
            </div>
            <div class="act">
              <form method="post" action="wipe" data-busy="Stopping any replay, deleting the logs and everything derived from them, and rebuilding the aliases."
                    data-confirm="Delete every replayed log and every finding derived from it? Any running replay is stopped first, and nothing starts loading afterwards — the stack stays empty until you start a replay yourself.">
                <button class="danger" type="submit" data-loading-label="Deleting logs…">
                  <span><span class="button-label">Delete logs</span><span class="button-sub">Empties the indices, rebuilds aliases, loads nothing.</span></span>
                  <span class="button-spinner" aria-hidden="true"></span>
                </button>
              </form>
              <p class="doc">Deletes every replayed log and everything found from them, and then
              loads nothing. The stack sits empty until you start a replay yourself. Takes under
              a minute. Do not run it while anyone else is using the system — they will find an
              empty stack with no warning. If it refuses because a replay would not stop, restart
              alice-replay on the workers it names and press it again. Nothing was deleted when
              it refuses.</p>
            </div>
          </div>
        </div>
        <div id="busy" class="busy" role="status" aria-live="polite">
          <span class="spinner" aria-hidden="true"></span><span id="busytext">Working…</span>
        </div>
      </div>
  </section>

  <section class="panel controls" aria-label="Fault injection">
      <div class="phead">
        <h2>Fault injection</h2>
        <span class="meta" id="inject-meta">$inject_meta</span>
      </div>
      <div class="pbody">
        <form id="inject-form" method="post" action="inject"
              data-busy="Arming the injection run and starting it in the background."
              data-confirm="This stops a real component, leaves it stopped for the whole observation window, then restores it and scores the run. Continue?">
          <div class="fields">
            <label class="field">
              <span class="field-l">Scenario</span>
              <select name="scenario" id="inject-scenario">$scenario_options</select>
              <span class="field-hint" id="scenario-hint">$scenario_hint</span>
            </label>
            <label class="field">
              <span class="field-l">Target worker</span>
              <select name="target_worker" id="inject-target">$worker_options</select>
              <span class="field-hint">Used by the collector and replay scenarios.</span>
            </label>
            <label class="field">
              <span class="field-l">Observe · minutes</span>
              <input type="number" name="observe_minutes" id="inject-minutes"
                     value="$observe_default" min="1" max="720" step="1">
              <span class="field-hint">The window the scorecard is measured over.</span>
            </label>
            <label class="field">
              <span class="field-l">EPN host to silence</span>
              <input type="text" name="independent_entity" id="inject-entity"
                     placeholder="epn001" autocomplete="off" spellcheck="false">
              <span class="field-hint">Required by drop-epn-stream only.</span>
            </label>
            <label class="field check">
              <input type="checkbox" name="strict" id="inject-strict" value="true" checked>
              <span>Gate on the scorecard</span>
            </label>
          </div>
        </form>
        <form id="inject-stop-form" method="post" action="inject-stop"
              data-busy="Stopping the injection run, restoring what it stopped, and scoring the shortened window."
              data-confirm="Stop the running injection? It restores the component and scores the shortened window."></form>
        <div class="grid docs">
          <div class="act">
          <button class="warning" type="submit" form="inject-form" id="inject-start"
                  data-loading-label="Starting injection…">
            <span><span class="button-label">Run injection</span><span class="button-sub" id="inject-sub">$inject_text</span></span>
            <span class="button-spinner" aria-hidden="true"></span>
          </button>
          <p class="doc">Breaks something on purpose to check the system notices. It stops a
          real component, leaves it stopped for the window you chose, then starts it again and
          scores what was detected. Deletes nothing, but real alerts fire and the part it stops
          genuinely stops working for that whole time. Takes the observe window plus a few
          minutes. Do not run it while anyone depends on the system — pick a quiet moment. If
          it fails or you press Stop, the component is still restored.</p>
          </div>
          <div class="act">
          <button class="neutral" type="submit" form="inject-stop-form" id="inject-stop"
                  data-loading-label="Stopping injection…"$inject_stop_disabled>
            <span><span class="button-label">Stop injection</span><span class="button-sub">Ends the window early. Still restores and still scores.</span></span>
            <span class="button-spinner" aria-hidden="true"></span>
          </button>
          <p class="doc">Ends the run early. It still starts the stopped component again and
          still scores the shortened window, so the system is left healthy either way. Deletes
          nothing. Takes a few seconds. Safe at any time. If the button is greyed out, no run is
          in progress.</p>
          </div>
        </div>
        <div id="scorecard"></div>
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
      <code>collector_time</code> instead. <strong>Every action runs in the background and reports its
      progress on this page, so the address bar never leaves /ops/ and a reload never repeats an
      action.</strong> Poison replay uses only already-modelled
      entities and reports native result and projected-episode evidence separately.</p>
    <div id="connection" class="clock"><span id="last-updated">Live status connected</span></div>
  </footer>
</main>

<script>
var jobRunning = false;
var stickUntil = 0;
var lastResult = '';
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
var SCENARIO_HINTS = $scenario_hints;
var WORKER_SCENARIOS = $worker_scenarios;
function clockText(seconds) {
  var n = Math.max(0, Number(seconds) || 0);
  var m = Math.floor(n / 60);
  var s = Math.floor(n % 60);
  return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
}
function msText(value) {
  if (value == null) { return 'not measured'; }
  return clockText(Number(value) / 1000);
}
function syncScenarioFields() {
  var picked = document.getElementById('inject-scenario').value;
  document.getElementById('scenario-hint').textContent = SCENARIO_HINTS[picked] || '';
  var target = document.getElementById('inject-target');
  var entity = document.getElementById('inject-entity');
  target.disabled = WORKER_SCENARIOS.indexOf(picked) === -1;
  entity.disabled = picked !== 'drop-epn-stream';
  entity.required = picked === 'drop-epn-stream';
}
function metricRow(label, value, tone, detail) {
  var tr = document.createElement('tr');
  tr.append(cell(label, 'name'), cell(value, ('metric-v ' + (tone || '')).trim()),
            cell(detail, 'entities'));
  return tr;
}
function scorecardTable(report) {
  var table = document.createElement('table');
  var head = document.createElement('thead');
  var hr = document.createElement('tr');
  ['Metric', 'Result', 'Detail'].forEach(function (text) {
    var th = document.createElement('th');
    th.textContent = text;
    hr.appendChild(th);
  });
  head.appendChild(hr);
  var body = document.createElement('tbody');

  var rec = report.signal_reconciliation || {};
  body.appendChild(metricRow('Signal reconciliation',
    rec.lossless ? 'lossless' : 'LOSSY', rec.lossless ? 'good' : 'bad',
    rec.projected_signals + ' rows for ' + rec.raw_alerts + ' alerts and ' +
    rec.raw_anomalies + ' anomalies'));

  var recall = report.independent_event_recall;
  body.appendChild(metricRow('Independent-event recall',
    recall ? (recall.surfaced ? 'surfaced' : 'ABSORBED') : 'not measured',
    recall ? (recall.surfaced ? 'good' : 'bad') : '',
    recall ? recall.entity + ' · ' + recall.signals + ' signals'
           : 'no entity was named for this scenario'));

  var purity = report.incident_purity || {};
  var mixed = Object.keys(purity.incidents_mixing_entity_or_topology || {}).length;
  var mixedGroups = Object.keys(purity.notification_groups_mixing_collectors || {}).length;
  body.appendChild(metricRow('Incident purity',
    purity.pure ? 'pure' : 'MIXED', purity.pure ? 'good' : 'bad',
    purity.incidents + ' episodes · ' + mixed + ' mixing entity or topology · ' +
    mixedGroups + ' groups mixing collectors'));

  var frag = report.fragmentation || {};
  var fragCount = Object.keys(frag.fragmented || {}).length;
  body.appendChild(metricRow('Fragmentation',
    fragCount ? fragCount + ' split' : 'none', fragCount ? 'bad' : 'good',
    fragCount ? Object.keys(frag.fragmented).join(', ')
              : 'no alert class made more episodes than it had entities'));

  body.appendChild(metricRow('Time to notify', msText(report.time_to_notify_ms),
    report.time_to_notify_ms == null ? 'bad' : '', 'from injection to the first delivery record'));
  body.appendChild(metricRow('Time to resolve', msText(report.time_to_resolve_ms),
    '', 'from injection to the first episode that resolved'));

  var inhibit = report.false_inhibition || {};
  body.appendChild(metricRow('False inhibition', String(inhibit.score),
    Number(inhibit.score) ? 'bad' : 'good',
    inhibit.page_signals + ' page signals · must be 0 before a rule is enabled'));

  var glass = report.breakglass || {};
  var glassBad = (glass.unexpected || []).length || (glass.missing_required || []).length;
  body.appendChild(metricRow('Break-glass',
    glassBad ? 'WRONG' : 'as specified', glassBad ? 'bad' : 'good',
    'delivered ' + ((glass.delivered || []).join(', ') || 'none') +
    ' · unexpected ' + ((glass.unexpected || []).join(', ') || 'none') +
    ' · missing ' + ((glass.missing_required || []).join(', ') || 'none')));

  body.appendChild(metricRow('Notifications', String(report.notification_volume),
    '', 'reported only — never gated on, because suppressing everything would win'));

  table.append(head, body);
  return table;
}
function paintScorecard(x) {
  var slot = document.getElementById('scorecard');
  if (!x || !x.report) {
    if (x && x.error) {
      var box = document.createElement('div');
      box.className = 'card';
      var note = document.createElement('pre');
      note.textContent = x.error;
      box.appendChild(note);
      slot.replaceChildren(box);
      return;
    }
    slot.replaceChildren();
    return;
  }
  var card = document.createElement('section');
  card.className = 'card';
  var head = document.createElement('div');
  head.className = 'card-h';
  var title = document.createElement('p');
  title.className = 'card-t';
  title.textContent = 'Scorecard · ' + (x.scenario || '?') +
    (x.finished_at ? ' · ' + x.finished_at : '');
  var verdict = document.createElement('span');
  var passed = x.verdict === 'passed';
  verdict.className = 'verdict ' + (passed ? 'pass' : 'fail');
  verdict.textContent = String(x.verdict || 'unscored').replace(/-/g, ' ');
  head.append(title, verdict);
  var wrap = document.createElement('div');
  wrap.className = 'card-b';
  var scroll = document.createElement('div');
  scroll.className = 'scroll';
  scroll.appendChild(scorecardTable(x.report));
  wrap.appendChild(scroll);
  if (x.failures && x.failures.length) {
    var list = document.createElement('ul');
    list.className = 'fails';
    for (var i = 0; i < x.failures.length; i++) {
      var li = document.createElement('li');
      li.textContent = x.failures[i];
      list.appendChild(li);
    }
    wrap.appendChild(list);
  }
  if (x.restore_error) {
    var warn = document.createElement('p');
    warn.className = 'fails';
    warn.textContent = 'Restore failed: ' + x.restore_error +
      ' — put it back by hand before the next run.';
    wrap.appendChild(warn);
  }
  card.append(head, wrap);
  slot.replaceChildren(card);
}
function paintInject(x) {
  x = x || {state: 'never-run', running: false};
  var state = String(x.state || 'never-run').replace(/-/g, ' ');
  var bits = [state];
  if (x.scenario) { bits.push(x.scenario); }
  if (x.running && x.state === 'observing' && x.remaining_seconds != null) {
    bits.push(clockText(x.remaining_seconds) + ' left');
  } else if (!x.running && x.verdict) {
    bits.push('scorecard ' + String(x.verdict).replace(/-/g, ' '));
  }
  document.getElementById('inject-sub').textContent = bits.join(' · ');
  document.getElementById('inject-meta').textContent = x.running
    ? 'Running · ' + clockText(x.elapsed_seconds) : state;
  if (!jobRunning) {
    document.getElementById('inject-start').disabled = !!x.running;
    document.getElementById('inject-stop').disabled = !x.running;
  }
  var fields = document.querySelectorAll('#inject-form select, #inject-form input');
  for (var i = 0; i < fields.length; i++) { fields[i].disabled = !!x.running; }
  if (!x.running) { syncScenarioFields(); }
  paintScorecard(x);
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

  paintJob(s.job);

  var poison = s.poison || {};
  var poisonState = String(poison.state || 'never-run');
  var poisonText = poisonState.replace(/-/g, ' ');
  if (poison.running && poison.current_burst) {
    poisonText += ' · burst ' + poison.current_burst;
  }
  if (poison.running && (poison.missing_detectors || poison.missing_monitors)) {
    poisonText += ' · missing ' + (poison.missing_detectors || []).length +
      ' detector / ' + (poison.missing_monitors || []).length + ' monitor';
  } else if (!poison.running && poison.verdict) {
    poisonText += ' · ' + poison.verdict.detectors_with_raw_and_projected_evidence +
      '/' + poison.verdict.detectors_expected + ' detectors';
  }
  document.getElementById('poison-status').textContent = poisonText;
  if (!jobRunning) {
    document.getElementById('poison-start').disabled = !!poison.running;
    document.getElementById('poison-stop').disabled = !poison.running;
  }

  paintInject(s.inject);

  document.getElementById('connection').className = 'clock';
  document.getElementById('last-updated').textContent =
    'Updated ' + new Date().toLocaleTimeString([], {hour12:false});
}
var timer = null;
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(poll, jobRunning ? 1000 : 5000);
}
function poll() {
  clearTimeout(timer);
  return fetch('status', {cache:'no-store'})
    .then(function (response) {
      if (!response.ok) { throw new Error('status ' + response.status); }
      return response.json();
    })
    .then(paint)
    .catch(function () {
      document.getElementById('connection').className = 'clock error';
      document.getElementById('last-updated').textContent = 'Live status unavailable';
    })
    .then(schedule);
}
function controls() {
  return document.querySelectorAll('.grid button');
}
function setControlsDisabled(on) {
  var buttons = controls();
  for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = on; }
}
function clearLoading() {
  var buttons = controls();
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].classList.remove('is-loading');
    buttons[i].removeAttribute('aria-busy');
    var label = buttons[i].querySelector('.button-label');
    if (label && label.dataset.original) { label.textContent = label.dataset.original; }
  }
}
function busyStrip(on, text) {
  if (text) { document.getElementById('busytext').textContent = text; }
  document.getElementById('busy').classList.toggle('on', on);
}
function hasProblem(lines) {
  for (var i = 0; i < lines.length; i++) {
    var text = String(lines[i]).toUpperCase();
    if (text.indexOf('FAILED') !== -1 || text.indexOf('REFUSED') !== -1 ||
        text.indexOf('ERROR') !== -1) { return true; }
  }
  return false;
}
function showResult(title, lines, error, sticky) {
  if (!sticky && Date.now() < stickUntil) { return; }
  if (sticky) { stickUntil = Date.now() + 8000; }
  var key = title + '\\u0000' + lines.join('\\n') + '\\u0000' + error;
  if (key === lastResult) { return; }
  lastResult = key;
  var slot = document.getElementById('result-slot');
  var section = document.createElement('section');
  section.className = error ? 'result error' : 'result';
  section.setAttribute('role', 'status');
  var head = document.createElement('p');
  head.className = 'result-title';
  head.textContent = title;
  var body = document.createElement('pre');
  body.textContent = lines.join('\\n');
  section.append(head, body);
  slot.replaceChildren(section);
}
function paintJob(job) {
  jobRunning = !!(job && job.state === 'running');
  if (jobRunning) {
    setControlsDisabled(true);
    busyStrip(true, job.label + ' · ' + job.seconds + 's');
  } else {
    clearLoading();
    setControlsDisabled(false);
    busyStrip(false);
  }
  if (!job) { return; }
  var lines = job.lines.length ? job.lines : ['working…'];
  var problem = hasProblem(lines);
  showResult(
    jobRunning ? job.label + ' · ' + job.seconds + 's'
               : (problem ? 'Action needs attention' : 'Action complete'),
    lines, problem && !jobRunning, false);
}
function runAction(form) {
  var clicked = form.querySelector('button[type="submit"]') ||
    document.querySelector('button[form="' + form.id + '"]');
  var label = clicked.querySelector('.button-label');
  if (!label.dataset.original) { label.dataset.original = label.textContent; }
  label.textContent = clicked.dataset.loadingLabel || 'Working…';
  clicked.classList.add('is-loading');
  clicked.setAttribute('aria-busy', 'true');
  setControlsDisabled(true);
  busyStrip(true, form.dataset.busy);
  var body = new URLSearchParams(new FormData(form)).toString();
  fetch(form.getAttribute('action'), {
    method: 'POST', cache: 'no-store', body: body || null,
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded'
    }
  }).then(function (response) {
    return response.json().catch(function () { return {}; });
  }).then(function (payload) {
    if (payload && payload.refused) {
      showResult('Action needs attention', payload.refused, true, true);
    }
    return poll();
  }).catch(function () {
    showResult('Action needs attention',
      ['The request never reached the ops server. Check that alice-ops is running, then try again.'],
      true, true);
    clearLoading();
    setControlsDisabled(false);
    busyStrip(false);
  });
}
var forms = document.querySelectorAll('form[data-busy]');
for (var f = 0; f < forms.length; f++) {
  forms[f].addEventListener('submit', function (event) {
    event.preventDefault();
    var question = this.dataset.confirm;
    if (question && !window.confirm(question)) { return; }
    runAction(this);
  });
}
document.getElementById('inject-scenario')
  .addEventListener('change', syncScenarioFields);
syncScenarioFields();
window.addEventListener('pageshow', function () { clearLoading(); poll(); });
if (window.location.search.indexOf('result=') !== -1) {
  window.history.replaceState(null,'',window.location.pathname);
}
poll();
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


def _poison_text(status):
    state = str(status.get("state") or "never-run").replace("-", " ")
    if status.get("running") and status.get("current_burst"):
        state += f" · burst {status['current_burst']}"
    verdict = status.get("verdict") or {}
    if not status.get("running") and verdict:
        state += (
            f" · {verdict.get('detectors_with_raw_and_projected_evidence', 0)}"
            f"/{verdict.get('detectors_expected', 10)} detectors")
    return html.escape(state)


def _inject_text(status):
    state = str(status.get("state") or "never-run").replace("-", " ")
    parts = [state]
    if status.get("scenario"):
        parts.append(str(status["scenario"]))
    if status.get("running") and status.get("state") == "observing":
        remaining = int(status.get("remaining_seconds") or 0)
        parts.append(f"{remaining // 60}m {remaining % 60:02d}s left")
    elif not status.get("running") and status.get("verdict"):
        parts.append(
            "scorecard " + str(status["verdict"]).replace("-", " "))
    return html.escape(" · ".join(parts))


def _scenario_options(selected=""):
    out = []
    for name, _ in INJECT_SCENARIOS:
        mark = " selected" if name == selected else ""
        safe = html.escape(name)
        out.append(f'<option value="{safe}"{mark}>{safe}</option>')
    return "".join(out)


def _worker_options():
    if not INJECT_WORKERS:
        return '<option value="">no workers configured</option>'
    return "".join(
        f'<option value="{html.escape(w)}">{html.escape(w)}</option>'
        for w in INJECT_WORKERS)


def _script_json(value):
    return json.dumps(value).replace("</", "<\\/")


def _has_problem(lines):
    return any(
        marker in line.upper()
        for line in lines
        for marker in ("FAILED", "REFUSED", "ERROR"))


def _result_block(lines, title, error):
    if not lines:
        return ""
    joined = html.escape("\n".join(lines))
    return (
        f'<section class="result{" error" if error else ""}" role="status">'
        f'<p class="result-title">{html.escape(title)}</p>'
        f"<pre>{joined}</pre></section>")


def render(result_lines=None):
    snap = snapshot()
    job = snap.get("job")
    if result_lines:
        problem = _has_problem(result_lines)
        result = _result_block(
            result_lines,
            "Action needs attention" if problem else "Action complete",
            problem)
    elif job:
        active = job["state"] == "running"
        problem = _has_problem(job["lines"])
        result = _result_block(
            job["lines"] or ["working…"],
            f"{job['label']} · {job['seconds']}s" if active
            else ("Action needs attention" if problem
                  else "Action complete"),
            problem and not active)
    else:
        result = ""
    running = snap["replay_running"]
    workers = snap["replay_workers"]
    incidents = snap["incidents"]
    poison = snap.get("poison") or {"state": "never-run", "running": False}
    inject = snap.get("inject") or {"state": "never-run", "running": False}
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
        poison_text=_poison_text(poison),
        poison_stop_disabled="" if poison.get("running") else " disabled",
        inject_text=_inject_text(inject),
        inject_meta=(
            f"Running · {int(inject.get('elapsed_seconds') or 0) // 60}m"
            if inject.get("running")
            else html.escape(
                str(inject.get("state") or "never-run").replace("-", " "))),
        inject_stop_disabled="" if inject.get("running") else " disabled",
        scenario_options=_scenario_options(inject.get("scenario") or ""),
        scenario_hint=html.escape(dict(INJECT_SCENARIOS).get(
            inject.get("scenario") or "", INJECT_SCENARIOS[0][1])),
        worker_options=_worker_options(),
        observe_default=INJECT_OBSERVE_DEFAULT,
        scenario_hints=_script_json(dict(INJECT_SCENARIOS)),
        worker_scenarios=_script_json(sorted(INJECT_WORKER_SCENARIOS)),
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
        location = "/ops/"
        if lines:
            token = _store_result(lines)
            location += "?result=" + urllib.parse.quote(token, safe="")
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

    def _wants_json(self):
        return "application/json" in (self.headers.get("Accept") or "")

    def _params(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return {}
        if length <= 0 or length > 65536:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        name = path.rsplit("/", 1)[-1]
        action = ACTIONS.get(name)
        params = self._params()
        if action is None:
            self._send(404, render(["not found"]))
            return
        label, action_work = action
        if name == "inject" and params.get("scenario"):
            label = (f"Arming the {params['scenario']} injection and starting "
                     f"it in the background")

        def work(lines):
            return action_work(lines, params)

        job, busy = start_job(name, label, work)
        refusal = None
        if job is None:
            refusal = [
                f"REFUSED — another action is still running: {busy}.",
                "Wait for it to finish, then press this again.",
            ]
        if self._wants_json():
            self._send(
                409 if refusal else 202,
                json.dumps({"job": job_status(), "refused": refusal}),
                "application/json")
        elif refusal:
            self._redirect_to_result(refusal)
        else:
            self._redirect_to_result([])

    def log_message(self, *args):
        pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", OPS_PORT), Handler)
    print(f"[ops] serving on 127.0.0.1:{OPS_PORT} "
          f"(workers={WORKERS}, info_nodes={INFO_NODES})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
