import http.client
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


poison = load("poison_replay_under_test", HERE / "poison_replay.py")
ops = load("ops_server_under_poison_test", HERE / "ops_server.py")

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def repo_root():
    current = HERE
    for _ in range(8):
        if (current / "Makefile").exists() and (current / "deploy").is_dir():
            return current
        current = current.parent
    raise RuntimeError("repository root not found")


def test_fast_detector_inventory_matches_live_definitions():
    actual = set()
    for path in (HERE / "detectors").glob("*.json"):
        definition = json.loads(path.read_text())
        if poison.is_one_minute(definition):
            actual.add(definition["name"])
    check(actual == poison.FAST_DETECTORS,
          f"poison recipes {sorted(poison.FAST_DETECTORS)} do not exactly "
          f"cover one-minute definitions {sorted(actual)}")
    check(not any(name.endswith("-slow") for name in poison.FAST_DETECTORS),
          "a 30-minute slow detector leaked into poison replay")


def test_burst_is_labelled_and_uses_trained_entities():
    samples = {
        "il": {"origin_host": "epn101", "node": "node-01"},
        "info": {"origin_host": "epn102", "node": "node-02"},
        "other": {"origin_host": "epn103", "node": "node-01"},
        "fluentbit": {"collector_id": "node-01"},
        "node": {"os_node": "node-03"},
        "roster": {"collectors": ["node-01", "node-02"],
                   "topology_version": "v-test"},
    }
    old = (poison.baseline_per_minute, poison.MIN_LOG_DOCS,
           poison.MAX_LOG_DOCS, poison.METRIC_DOCS)
    try:
        poison.baseline_per_minute = lambda *_args: 2
        poison.MIN_LOG_DOCS = 3
        poison.MAX_LOG_DOCS = 4
        poison.METRIC_DOCS = 2
        docs, volumes = poison.build_burst(
            samples, "run-test", 1, 1.0)
    finally:
        (poison.baseline_per_minute, poison.MIN_LOG_DOCS,
         poison.MAX_LOG_DOCS, poison.METRIC_DOCS) = old
    check(volumes["il"]["injected"] == 4,
          f"adaptive volume cap was not applied: {volumes}")
    check(any(index == "generic-log-info-node-02" for index, _, _ in docs),
          "generic info poison did not target the selected trained collector")
    check(all(source.get("synthetic") is True for _, _, source in docs),
          "at least one poison document is not explicitly synthetic")
    check(all(source.get("poison_run_id") == "run-test"
              for _, _, source in docs),
          "at least one poison document cannot be attributed to its run")
    declared = set().union(*(
        set(source.get("poison_targets") or []) for _, _, source in docs))
    check(not any(name.endswith("-slow") for name in declared),
          f"a slow detector is named as a poison target: {sorted(declared)}")
    check(poison.FAST_DETECTORS <= declared,
          f"burst labels omit fast detector targets "
          f"{sorted(poison.FAST_DETECTORS - declared)}")


def test_status_publish_is_atomic_and_machine_readable():
    previous_path = poison.STATUS_PATH
    previous_status = dict(poison._STATUS)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poison.STATUS_PATH = os.path.join(tmp, "status.json")
            poison._STATUS.clear()
            poison._STATUS.update({"run_id": "run-status"})
            poison.publish(state="observing", current_burst=2)
            saved = json.loads(pathlib.Path(poison.STATUS_PATH).read_text())
            check(saved["state"] == "observing"
                  and saved["current_burst"] == 2,
                  f"published status lost fields: {saved}")
            leftovers = list(pathlib.Path(tmp).glob("*.tmp"))
            check(not leftovers, f"atomic status left temp files: {leftovers}")
    finally:
        poison.STATUS_PATH = previous_path
        poison._STATUS.clear()
        poison._STATUS.update(previous_status)


def test_selected_entity_must_have_an_active_trained_model():
    found = {
        "il-per-epn": {
            "id": "detector-1",
            "source": {"category_field": ["origin_host"]},
        }
    }
    samples = {"il": {"origin_host": "epn101"}}
    original = poison._request_url
    try:
        poison._request_url = lambda *_args, **_kwargs: (
            200,
            {"state": "RUNNING", "is_active": True,
             "init_progress": {"percentage": "100%"},
             "model": {"model_id": "m1"}},
        )
        ready, rows = poison.selected_entity_readiness(found, samples)
        check(ready and rows["il-per-epn"]["ready"],
              f"active trained entity was rejected: {rows}")
        poison._request_url = lambda *_args, **_kwargs: (
            200,
            {"state": "RUNNING", "is_active": False,
             "init_progress": {"percentage": "100%"}},
        )
        ready, rows = poison.selected_entity_readiness(found, samples)
        check(not ready and not rows["il-per-epn"]["ready"],
              "entity with no active model was accepted for poisoning")
    finally:
        poison._request_url = original


def test_ops_poison_route_precedes_generic_replay_suffix():
    called = []
    original_poison = ops.ACTIONS["poison-replay"]
    original_replay = ops.ACTIONS["replay"]
    with ops._JOB_LOCK:
        original_job = ops._JOB
    server = None
    thread = None
    try:
        def fake_poison(lines):
            called.append("poison")
            lines.append("started")

        def fake_replay(lines):
            called.append("replay")
            lines.append("wrong")

        ops.ACTIONS["poison-replay"] = ("Starting poison test", fake_poison)
        ops.ACTIONS["replay"] = ("Starting replay test", fake_replay)
        with ops._JOB_LOCK:
            ops._JOB = None
        server = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/ops/poison-replay")
        response = connection.getresponse()
        response.read()
        connection.close()
        deadline = time.time() + 5
        while time.time() < deadline:
            job = ops.job_status()
            if job and job["state"] == "done":
                break
            time.sleep(0.01)
        check(response.status == 303,
              f"poison action did not use refresh-safe redirect: {response.status}")
        check(response.getheader("Location") == "/ops/",
              "poison action did not return to the background-job ops page")
        check(called == ["poison"],
              f"/poison-replay fell through to generic replay: {called}")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        ops.ACTIONS["poison-replay"] = original_poison
        ops.ACTIONS["replay"] = original_replay
        with ops._JOB_LOCK:
            ops._JOB = original_job


def test_deploy_wires_mapping_service_make_and_ops_controls():
    root = repo_root()
    templates = (root / "deploy/roles/dashboards/templates/templates.sh.j2").read_text()
    unit = (root / "deploy/roles/dashboards/templates/alice-poison-replay.service.j2").read_text()
    makefile = (root / "Makefile").read_text()
    ops_source = (HERE / "ops_server.py").read_text()
    for field in ("synthetic", "poison_run_id", "poison_stage",
                  "poison_targets"):
        check(templates.count(f'"{field}"') >= 4,
              f"{field} is not mapped for new and existing log/metrics indices")
    check("ExecStart=/usr/bin/python3 {{ dashboards_poison_replay_script }}"
          in unit, "poison systemd unit does not execute the installed script")
    check("poison: poison-replay" in makefile
          and "poison-replay:" in makefile
          and "posion-replay: poison-replay" in makefile,
          "short, canonical, or requested misspelling target is absent")
    check("\ndeploy: deploy-preflight contract\n" in makefile
          and "deploy-preflight:" in makefile,
          "make deploy can reach the VMs before local contracts pass")
    check('action="poison-replay"' in ops_source
          and 'action="poison-stop"' in ops_source,
          "Ops lacks poison start/stop controls")


TESTS = [value for name, value in sorted(globals().items())
         if name.startswith("test_") and callable(value)]


if __name__ == "__main__":
    for test in TESTS:
        before = len(failures)
        try:
            test()
        except Exception as exc:
            failures.append(f"{test.__name__} raised {type(exc).__name__}: {exc}")
        state = "ok" if len(failures) == before else "FAIL"
        print(f"[poison-contract] {test.__name__}: {state}")
    if failures:
        for failure in failures:
            print(f"[poison-contract] FATAL: {failure}")
        raise SystemExit(1)
    print(f"[poison-contract] PASS ({len(TESTS)} tests)")
