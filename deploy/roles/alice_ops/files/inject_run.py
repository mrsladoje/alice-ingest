#!/usr/bin/env python3
"""One fault-injection run: inject, observe, restore, score.

This is the single implementation behind both front doors. `make inject` and
the /ops page's injection button both write the same request file and start
the same unit, so the scorecard cannot depend on which one was used.

The physical steps that do not live on the control host — stopping a worker's
Fluent Bit, loading a worker's CPUs, stopping the signal projector on its own
node — go through that node's fault agent over HTTP, because the control host
has no Ansible and no SSH into the fleet.
"""

import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OS_URL = os.environ.get("OS_URL", "http://localhost:9200").rstrip("/")
METRICS_INDEX = os.environ.get("METRICS_INDEX", "cockpit-metrics")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
NOTIFICATIONS_INDEX = os.environ.get(
    "NOTIFICATIONS_INDEX", "alice-notifications")
GRADE_FLOOR = os.environ.get("GRADE_FLOOR", "0.5")

REQUEST_PATH = os.environ.get(
    "INJECT_REQUEST_PATH", "/var/lib/alice-inject/request.json")
STATUS_PATH = os.environ.get(
    "INJECT_STATUS_PATH", "/var/lib/alice-inject/status.json")
REPORT_DIR = os.environ.get(
    "INJECT_REPORT_DIR", "/var/lib/alice-inject/runs")
SCORE_SCRIPT = os.environ.get(
    "INJECT_SCORE_SCRIPT", "/opt/alice-ingest/score_injection.py")
CAUSAL_EDGES = os.environ.get(
    "CAUSAL_EDGES", "/opt/alice-ingest/init/causal_edges.json")

FAULT_TOKEN = os.environ.get("INJECT_FAULT_TOKEN", "")
METRICS_SERVICE = os.environ.get("INJECT_METRICS_SERVICE", "alice-metrics")
PROJECTOR_SERVICE = os.environ.get(
    "INJECT_PROJECTOR_SERVICE", "alice-signal-projector")
FLUENT_BIT_SERVICE = os.environ.get(
    "INJECT_FLUENT_BIT_SERVICE", "fluent-bit")
PROJECTOR_AGENT = os.environ.get("INJECT_PROJECTOR_AGENT", "").rstrip("/")
PROJECTOR_CATCHUP_RETRIES = int(
    os.environ.get("INJECT_PROJECTOR_CATCHUP_RETRIES", "24"))
PROJECTOR_CATCHUP_DELAY = int(
    os.environ.get("INJECT_PROJECTOR_CATCHUP_DELAY", "5"))
INGEST_PIPELINE = os.environ.get(
    "INJECT_INGEST_PIPELINE", "alice-add-ingest-time")
INJECTED_PIPELINE = f"{INGEST_PIPELINE}-injected"
LOG_INDEX_TARGET = "infologger*,generic-log-*"

SCENARIOS = (
    "kill-fluent-bit",
    "drop-epn-stream",
    "cpu-stress-worker",
    "stop-alice-metrics",
    "stop-projector",
    "replay-end",
    "observe-only",
)

WORKER_SCENARIOS = ("kill-fluent-bit", "cpu-stress-worker", "replay-end")
ENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_STOP = False
_STATUS = {}


class InjectError(RuntimeError):
    pass


def _pairs(raw):
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, url = item.partition("=")
        if url:
            out.append((name.strip(), url.strip().rstrip("/")))
    return out


WORKER_AGENTS = dict(_pairs(os.environ.get("INJECT_WORKER_AGENTS", "")))
WORKER_REPLAY = dict(_pairs(os.environ.get("INJECT_WORKER_REPLAY", "")))


def now_ms():
    return int(time.time() * 1000)


def iso(moment=None):
    if moment is None:
        moment = now_ms()
    return datetime.datetime.fromtimestamp(
        moment / 1000, tz=datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path, value):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as handle:
        json.dump(value, handle, indent=2, default=str)
        handle.write("\n")
    os.replace(tmp, path)


def say(message):
    print(message, flush=True)
    _STATUS.setdefault("lines", []).append(f"{iso()} {message}")
    del _STATUS["lines"][:-200]
    publish()


def publish(**fields):
    _STATUS.update(fields)
    started = _STATUS.get("started_ms")
    if started:
        _STATUS["elapsed_seconds"] = int((now_ms() - started) / 1000)
    try:
        _atomic_json(STATUS_PATH, _STATUS)
    except Exception as exc:
        print(f"[inject] could not write status: {exc}", flush=True)


def _request(method, url, body=None, timeout=30, headers=None):
    data = None
    head = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        head["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def _search(index, query):
    code, body = _request(
        "POST",
        f"{OS_URL}/{index}/_search"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"size": 0, "track_total_hits": True, "query": query})
    if code != 200:
        return None
    try:
        total = json.loads(body).get("hits", {}).get("total", 0)
    except ValueError:
        return None
    return total.get("value", 0) if isinstance(total, dict) else total


def _agent(base, path, query=None):
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"X-Fault-Token": FAULT_TOKEN} if FAULT_TOKEN else {}
    code, body = _request("POST", url, timeout=60, headers=headers)
    if code != 200:
        raise InjectError(
            f"fault agent {base} refused {path}: HTTP {code} {body.strip()[:300]}")
    return body


def _local_service(verb, name):
    proc = subprocess.run(
        ["/usr/bin/systemctl", verb, name],
        capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise InjectError(
            f"systemctl {verb} {name} failed: "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}")


def install_injected_pipeline(entity):
    code, body = _request(
        "PUT", f"{OS_URL}/_ingest/pipeline/{INJECTED_PIPELINE}",
        {"description":
            "Injection only. Delegates to the real pipeline first, so every "
            "record still gets its ingest_time, both lag fields and "
            "origin_host, then drops every record born on one EPN so that host "
            "goes silent while the rest of the fleet keeps logging. The order "
            "matters: origin_host is set by the real pipeline, so a drop that "
            "ran first would test a null field and silently never fire.",
         "processors": [
             {"pipeline": {"name": INGEST_PIPELINE}},
             {"drop": {"if": f"ctx.origin_host == '{entity}'"}}]})
    if code not in (200, 201):
        raise InjectError(
            f"could not create {INJECTED_PIPELINE}: HTTP {code} {body[:300]}")


def point_indices_at(pipeline):
    code, body = _request(
        "PUT",
        f"{OS_URL}/{LOG_INDEX_TARGET}/_settings"
        "?ignore_unavailable=true&allow_no_indices=true",
        {"index.default_pipeline": pipeline})
    if code != 200:
        raise InjectError(
            f"could not point the log indices at {pipeline}: "
            f"HTTP {code} {body[:300]}")


def apply_injection(req):
    scenario = req["scenario"]
    target = req.get("target_worker") or ""
    if scenario == "kill-fluent-bit":
        _agent(WORKER_AGENTS[target], "/service-stop",
               {"name": FLUENT_BIT_SERVICE})
        say(f"stopped {FLUENT_BIT_SERVICE} on {target}")
    elif scenario == "cpu-stress-worker":
        seconds = req["observe_minutes"] * 60 + 60
        _agent(WORKER_AGENTS[target], "/cpu-stress", {"seconds": seconds})
        say(f"saturated the CPUs on {target} for {seconds}s")
    elif scenario == "replay-end":
        code, body = _request(
            "POST", WORKER_REPLAY[target] + "/replay-stop", timeout=30)
        say(f"asked {target} to end its replay: HTTP {code} {body.strip()[:120]}")
    elif scenario == "drop-epn-stream":
        install_injected_pipeline(req["independent_entity"])
        point_indices_at(INJECTED_PIPELINE)
        say(f"silenced {req['independent_entity']} at ingest; the rest of the "
            f"fleet keeps logging")
    elif scenario == "stop-alice-metrics":
        _local_service("stop", METRICS_SERVICE)
        say(f"stopped {METRICS_SERVICE} on the control host")
    elif scenario == "stop-projector":
        if not PROJECTOR_AGENT:
            raise InjectError(
                "no projector fault agent is configured, so the projector "
                "cannot be stopped from here")
        _agent(PROJECTOR_AGENT, "/service-stop", {"name": PROJECTOR_SERVICE})
        say(f"stopped {PROJECTOR_SERVICE} on its own node")
    elif scenario == "observe-only":
        say("no fault applied — this run measures the stack as it stands")


def wait_for_projector_catchup(since_ms):
    query = {"bool": {"filter": [
        {"term": {"kind": "projector"}},
        {"term": {"projector_cycle_ok": 1}},
        {"range": {"@timestamp": {"gte": since_ms, "format": "epoch_millis"}}}]}}
    for _ in range(PROJECTOR_CATCHUP_RETRIES):
        found = _search(METRICS_INDEX, query)
        if found:
            say("the projector completed a catch-up cycle after its restart")
            return True
        time.sleep(PROJECTOR_CATCHUP_DELAY)
    say("WARNING: the projector restarted but never reported a completed "
        "catch-up cycle — a running process is not recovery, so read the "
        "re-send metrics below with that in mind")
    return False


def restore(req):
    scenario = req["scenario"]
    target = req.get("target_worker") or ""
    if not req.get("restore", True):
        say("restore=false — the injected fault is left in place on purpose")
        return
    publish(state="restoring")
    if scenario == "kill-fluent-bit":
        _agent(WORKER_AGENTS[target], "/service-start",
               {"name": FLUENT_BIT_SERVICE})
        say(f"started {FLUENT_BIT_SERVICE} again on {target}")
    elif scenario == "cpu-stress-worker":
        _agent(WORKER_AGENTS[target], "/cpu-stress-stop")
        say(f"dropped the load generators on {target}")
    elif scenario == "drop-epn-stream":
        point_indices_at(INGEST_PIPELINE)
        say("pointed the log indices back at the real ingest pipeline")
    elif scenario == "stop-alice-metrics":
        _local_service("start", METRICS_SERVICE)
        say(f"started {METRICS_SERVICE} again")
    elif scenario == "stop-projector":
        restart_ms = now_ms()
        _agent(PROJECTOR_AGENT, "/service-start", {"name": PROJECTOR_SERVICE})
        say(f"started {PROJECTOR_SERVICE} again")
        wait_for_projector_catchup(restart_ms)
    elif scenario == "replay-end":
        say("the replay stays stopped — mass silence is the observable this "
            "scenario exists to produce")


def observe(req):
    seconds = req["observe_minutes"] * 60
    deadline = time.time() + seconds
    publish(state="observing", observe_seconds=seconds)
    say(f"observing for {req['observe_minutes']} minutes")
    while time.time() < deadline and not _STOP:
        publish(remaining_seconds=int(max(0, deadline - time.time())))
        time.sleep(min(5, max(1, deadline - time.time())))
    publish(remaining_seconds=0)
    if _STOP:
        say("stop requested — ending the observation window early")


def score(req, start_ms):
    publish(state="scoring")
    env = dict(
        os.environ,
        OS_URL=OS_URL,
        SIGNALS_INDEX=SIGNALS_INDEX,
        INCIDENTS_INDEX=INCIDENTS_INDEX,
        NOTIFICATIONS_INDEX=NOTIFICATIONS_INDEX,
        GRADE_FLOOR=str(GRADE_FLOOR),
        CAUSAL_EDGES=CAUSAL_EDGES,
        SCENARIO=req["scenario"],
        START_MS=str(start_ms),
        END_MS=str(now_ms()),
        INDEPENDENT_ENTITY=req.get("independent_entity") or "",
        REQUIRE_INDEPENDENT_RECALL=(
            "true" if req["scenario"] == "drop-epn-stream" else "false"),
        STRICT="true" if req.get("strict", True) else "false")
    proc = subprocess.run(
        ["/usr/bin/python3", SCORE_SCRIPT],
        env=env, capture_output=True, text=True, timeout=1800, check=False)
    report = None
    if proc.stdout.strip():
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            report = None
    summary = [line for line in (proc.stderr or "").splitlines() if line.strip()]
    if report is None:
        raise InjectError(
            "the scorecard did not come back as JSON: "
            + " / ".join(summary)[-400:])
    return report, summary, proc.returncode


def verdict_of(summary, rc, strict):
    failures = [line.strip()[6:].strip() for line in summary
                if line.strip().startswith("FAIL:")]
    if failures or rc != 0:
        return ("failed" if strict else "failed-not-gated"), failures
    return "passed", []


def _flag(value, default=True):
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("true", "yes", "on", "1")


def read_request():
    with open(REQUEST_PATH) as handle:
        req = json.load(handle)
    scenario = str(req.get("scenario") or "")
    if scenario not in SCENARIOS:
        raise InjectError(
            f"scenario must be one of {', '.join(SCENARIOS)}, not "
            f"'{scenario}'")
    req["scenario"] = scenario
    try:
        req["observe_minutes"] = int(req.get("observe_minutes") or 45)
    except (TypeError, ValueError):
        raise InjectError("observe_minutes must be a whole number of minutes")
    if req["observe_minutes"] < 1 or req["observe_minutes"] > 720:
        raise InjectError(
            "observe_minutes must be between 1 and 720 — the observation "
            "window is the measurement, so it is never zero")
    req["restore"] = _flag(req.get("restore"), True)
    req["strict"] = _flag(req.get("strict"), True)
    req["independent_entity"] = str(req.get("independent_entity") or "").strip()
    if scenario == "drop-epn-stream":
        if not req["independent_entity"]:
            raise InjectError(
                "drop-epn-stream needs independent_entity=epnNNN. That host "
                "is what the scenario silences and what independent-event "
                "recall is then scored against; without it the run cannot "
                "establish the metric it exists for")
        if not ENTITY_PATTERN.match(req["independent_entity"]):
            raise InjectError(
                f"'{req['independent_entity']}' is not a host name. It is "
                f"compared inside an ingest pipeline condition, so it may "
                f"only hold letters, digits, dots, dashes and underscores")
    target = str(req.get("target_worker") or "").strip()
    if scenario in WORKER_SCENARIOS:
        if not target:
            target = next(iter(WORKER_AGENTS), "") or next(
                iter(WORKER_REPLAY), "")
        table = WORKER_REPLAY if scenario == "replay-end" else WORKER_AGENTS
        if target not in table:
            raise InjectError(
                f"{scenario} needs a target worker this control host can "
                f"reach; known workers: {', '.join(sorted(table)) or 'none'}")
    req["target_worker"] = target
    return req


def _handle_stop(signum, frame):
    global _STOP
    _STOP = True


def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    _STATUS.clear()
    _STATUS.update({
        "state": "starting",
        "started_ms": now_ms(),
        "started_at": iso(),
        "lines": [],
        "report": None,
        "verdict": None,
    })
    publish()

    try:
        req = read_request()
    except FileNotFoundError:
        publish(state="failed", error=(
            f"FAILED — no injection request at {REQUEST_PATH}. Start this "
            f"run from the /ops page or with make inject, which both write "
            f"that file first."))
        return 2
    except (InjectError, ValueError) as exc:
        publish(state="failed", error=f"FAILED — {exc}")
        return 2

    publish(
        scenario=req["scenario"],
        target_worker=req["target_worker"],
        observe_minutes=req["observe_minutes"],
        independent_entity=req["independent_entity"],
        strict=req["strict"],
        restore=req["restore"],
        state="injecting")
    say(f"scenario={req['scenario']} target={req['target_worker'] or 'control'} "
        f"observe={req['observe_minutes']}m strict={req['strict']}")

    start_ms = now_ms()
    publish(injection_start_ms=start_ms)
    injected = False
    try:
        apply_injection(req)
        injected = True
        observe(req)
    except InjectError as exc:
        say(f"FAILED — {exc}")
    except Exception as exc:
        say(f"FAILED — the injection stopped with an unexpected error: {exc}")

    restore_error = None
    if injected:
        try:
            restore(req)
        except Exception as exc:
            restore_error = str(exc)
            say(f"FAILED to restore the injected component: {exc}. Restore it "
                f"by hand before the next run, or the next scorecard measures "
                f"this fault as well.")

    if not injected:
        publish(state="failed", finished_ms=now_ms(), finished_at=iso())
        return 1

    try:
        report, summary, rc = score(req, start_ms)
    except Exception as exc:
        say(f"FAILED — {exc}")
        publish(state="failed", finished_ms=now_ms(), finished_at=iso())
        return 1

    verdict, failures = verdict_of(summary, rc, req["strict"])
    for line in summary:
        print(line, flush=True)

    run_id = f"{iso().replace(':', '').replace('-', '')}-{req['scenario']}"
    record = {
        "run_id": run_id,
        "request": req,
        "injection_start_ms": start_ms,
        "scored_at": iso(),
        "verdict": verdict,
        "failures": failures,
        "restore_error": restore_error,
        "report": report,
        "summary": summary,
    }
    try:
        _atomic_json(os.path.join(REPORT_DIR, f"{run_id}.json"), record)
    except Exception as exc:
        say(f"could not write the run report: {exc}")

    publish(
        state="stopped" if _STOP else "done",
        run_id=run_id,
        verdict=verdict,
        failures=failures,
        restore_error=restore_error,
        report=report,
        summary=summary,
        finished_ms=now_ms(),
        finished_at=iso())
    say(f"scorecard {verdict}")
    if verdict == "failed" and req["strict"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
