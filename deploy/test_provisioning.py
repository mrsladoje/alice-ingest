import json
import os
import re
import subprocess
import tempfile

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

DEPLOY = os.path.dirname(os.path.abspath(__file__))
ROLES = os.path.join(DEPLOY, "roles")

CONTRACT_SHARDS = {
    "template-catalog": (1, 2),
    "template-triage": (1, 2),
    "shifter-queries": (1, 0),
    "template-buckets-5m-*": (1, 1),
    "template-buckets-1h-*": (1, 1),
}

REMOVED_CATALOG_FIELDS = ("count", "counts_by_node", "last_pass", "first_seen",
                          "last_seen", "last_seen_node", "collector_time")

REQUIRED_CATALOG_FIELDS = ("version_id", "canonical_id", "normalized",
                           "token_count", "first_observed", "last_observed",
                           "first_catalogued", "widened_into", "widened_from",
                           "nodes", "watermark_id", "published_through",
                           "check_id", "check", "ok", "stamped", "indexed")

REMOVED_SNAPSHOT_FIELDS = ("superseded_by", "supersedes",
                           "relationship_verified", "historical_programs",
                           "historical_origin_hosts", "historical_scope",
                           "incarnations")

STAMP_FIELDS = ("template_version", "template_id", "template_status")


def _load_yaml(path):
    with open(path) as handle:
        return yaml.safe_load(handle) or {}


def group_vars():
    return _load_yaml(os.path.join(DEPLOY, "group_vars", "all.yml"))


def role_defaults(role):
    return _load_yaml(os.path.join(ROLES, role, "defaults", "main.yml"))


def role_tasks(role, name="main.yml"):
    return _load_yaml(os.path.join(ROLES, role, "tasks", name))


def _environment(role):
    env = Environment(loader=FileSystemLoader(os.path.join(ROLES, role,
                                                           "templates")),
                      keep_trailing_newline=True)
    env.filters["ternary"] = lambda value, yes, no: yes if value else no
    env.filters["bool"] = bool
    env.filters["lower"] = lambda value: str(value).lower()
    return env


BOOTSTRAP_VARS = {
    "opensearch_cluster_config_worker_node_ids": ["node-01", "node-02"],
    "log_rollover_migrate_existing": False,
    "opensearch_info_search_idle_after": "10s",
    "opensearch_info_translog_sync_interval": "30s",
    "opensearch_info_merge_threads": 1,
}


def render_bootstrap(primaries=1):
    values = dict(group_vars())
    values.update(BOOTSTRAP_VARS)
    values["log_primary_shards_storage"] = primaries
    return _environment("sweet_opensearch").get_template(
        "templates.sh.j2").render(**values)


def schema_documents(primaries=1):
    values = dict(group_vars())
    values.update(BOOTSTRAP_VARS)
    values["log_primary_shards_storage"] = primaries
    environment = _environment("sweet_opensearch")
    found = {}
    for name in environment.list_templates():
        if not name.startswith("schema/") or not name.endswith(".json.j2"):
            continue
        found[name[len("schema/"):-len(".json.j2")]] = \
            environment.get_template(name).render(**values)
    return found


def index_templates(primaries=1):
    found = {}
    for body in schema_documents(primaries).values():
        document = json.loads(body)
        for pattern in document.get("index_patterns", []):
            found[pattern] = document
    return found


def test_rendered_bootstrap_is_valid_shell():
    script = render_bootstrap()
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        result = subprocess.run(["sh", "-n", path], capture_output=True,
                                text=True)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr


def test_every_schema_document_is_valid_json():
    documents = schema_documents()
    assert len(documents) == 28
    for name, body in documents.items():
        json.loads(body)


def test_every_schema_document_the_script_loads_exists():
    script = render_bootstrap()
    loaded = set(re.findall(r"\$\(load (\S+)\.json\)", script))
    # One load is per-worker and names a shell variable. Its source is the
    # single schema-per-worker template, rendered once per node id.
    dynamic = {name for name in loaded if "$" in name}
    assert dynamic == {"index-logs-application-local-$wn"}, dynamic
    assert os.path.exists(os.path.join(
        ROLES, "sweet_opensearch", "templates", "schema-per-worker",
        "index-logs-application-local.json.j2"))
    assert loaded - dynamic == set(schema_documents()), \
        (loaded - dynamic) ^ set(schema_documents())


@pytest.mark.parametrize("pattern,shards", sorted(CONTRACT_SHARDS.items()))
def test_new_and_changed_indices_carry_the_contract_shard_settings(pattern,
                                                                   shards):
    settings = index_templates()[pattern]["template"]["settings"]
    assert (settings["number_of_shards"],
            settings["number_of_replicas"]) == shards


def test_the_two_fixed_new_indices_add_exactly_four_shards():
    templates = index_templates()
    added = 0
    for pattern in ("template-triage", "shifter-queries"):
        settings = templates[pattern]["template"]["settings"]
        added += settings["number_of_shards"] * (
            1 + settings["number_of_replicas"])
    assert added == 4


def test_the_fixed_indices_never_roll_over_and_the_buckets_age_out():
    templates = index_templates()
    for pattern in ("template-triage", "shifter-queries", "template-catalog"):
        settings = templates[pattern]["template"]["settings"]
        assert "*" not in pattern
        assert "index.plugins.index_state_management.rollover_alias" not in settings
    ism = open(os.path.join(ROLES, "sweet_opensearch", "templates",
                            "ism.sh.j2")).read()
    for index in ("template-triage", "shifter-queries", "template-catalog"):
        assert index not in ism
    for pattern in ("template-buckets-5m-*", "template-buckets-1h-*"):
        settings = templates[pattern]["template"]["settings"]
        assert "index.plugins.index_state_management.rollover_alias" not in settings
        assert templates[pattern]["template"]["mappings"]["properties"][
            "counts"]["type"] == "nested"
    assert "alice-template-buckets-5m-retention" in ism
    assert "alice-template-buckets-1h-retention" in ism
    assert 'age_delete_policy "delete $BUCKETS_5M day indices' in ism
    assert 'age_delete_policy "delete $BUCKETS_1H month indices' in ism
    assert group_vars()["ism_retention_template_buckets_5m"] == "4d"
    assert group_vars()["ism_retention_template_buckets_1h"] == "66d"


def test_both_log_mappings_carry_the_three_stamp_fields():
    found = schema_documents()
    for name in ("component-logs-application-mappings",
                 "component-logs-infologger-mappings"):
        properties = json.loads(found[name])["template"]["mappings"][
            "properties"]
        for field in STAMP_FIELDS:
            assert properties[field] == {"type": "keyword"}, (name, field)
    assert json.loads(found["component-logs-infologger-mappings"])["template"]["mappings"][
        "dynamic"] == "strict"
    metrics = json.loads(found["index-cockpit-metrics"])["template"][
        "mappings"]["properties"]
    for field in ("stamper_up", "stamper_records", "stamper_records_delta",
                  "stamper_publication_failures", "stamper_peak_rss_bytes"):
        assert field in metrics


def test_the_three_existing_log_routes_are_unchanged():
    templates = index_templates()
    for pattern in ("infologger-*", "application-logs-central-*"):
        settings = templates[pattern]["template"]["settings"]
        assert settings["number_of_replicas"] == 2
        assert settings["index.plugins.index_state_management.rollover_alias"]
    worker = open(os.path.join(
        ROLES, "sweet_opensearch", "templates", "schema-per-worker",
        "index-logs-application-local.json.j2")).read()
    assert '"index_patterns": ["application-logs-local-{{ node }}-*"]' in worker
    registration = open(os.path.join(ROLES, "sweet_opensearch",
                                     "files", "register_node.sh")).read()
    assert "application-logs-local-%s-*" not in registration


def test_template_catalog_mapping_is_observation_based():
    properties = index_templates()["template-catalog"][
        "template"]["mappings"]["properties"]
    for field in REMOVED_CATALOG_FIELDS + REMOVED_SNAPSHOT_FIELDS:
        assert field not in properties
    for field in REQUIRED_CATALOG_FIELDS:
        assert field in properties
    assert properties["last_observed"]["type"] == "date"
    assert properties["counters"] == {"type": "object", "enabled": False}


def test_bootstrap_applies_the_templates_and_precreates_the_fixed_indices():
    script = render_bootstrap()
    assert script.count('put "/_index_template/') == 16
    assert script.count("ensure_index \"") == 10
    for template in ("alice-template-buckets-5m", "alice-template-buckets-1h",
                     "alice-template-triage", "alice-shifter-queries"):
        assert 'put "/_index_template/%s"' % template in script
    for index in ("template-triage", "shifter-queries"):
        assert 'ensure_index "%s" \'{}\'' % index in script
    assert "template-metrics" not in script
    assert 'ensure_index "template-buckets' not in script


def live_mapping_loop(script):
    body = re.search(r"^for idx_tpl in \\\n(.*?)^done$", script,
                     re.S | re.M)
    assert body, "the live _mapping loop is gone from the bootstrap script"
    return dict(re.findall(r'"([a-z0-9-]+):([a-z0-9-]+)"', body.group(1)))


def test_every_fixed_template_index_gets_its_mapping_on_the_live_index():
    script = render_bootstrap()
    shared = group_vars()
    pairs = live_mapping_loop(script)
    for variable, template in (
            ("template_catalog_index", "alice-template-catalog"),
            ("template_triage_index", "alice-template-triage"),
            ("shifter_queries_index", "alice-shifter-queries")):
        index = shared[variable]
        assert pairs.get(index) == template, index
        assert 'ensure_index "%s"' % index in script


def test_the_changed_catalog_mapping_reaches_an_index_that_already_exists():
    script = render_bootstrap()
    index = group_vars()["template_catalog_index"]
    create = script.index('ensure_index "%s"' % index)
    loop = script.index("for idx_tpl in")
    assert create < loop
    assert "index already present (skip create)" in script
    properties = index_templates()[index][
        "template"]["mappings"]["properties"]
    assert index_templates()[index]["template"][
        "mappings"]["dynamic"] is False
    for field in ("last_observed", "first_observed", "canonical_id",
                  "normalized", "version_id"):
        assert field in properties


ROLLOVER_FAMILIES = {
    "infologger": {"rollover_days": 7, "delete_days": 56, "copies": 3},
    "application-logs-central": {"rollover_days": 7, "delete_days": 35,
                                 "copies": 3},
    "alice-alert-actions": {"rollover_days": 7, "delete_days": 30,
                            "copies": 1, "primaries": 1},
}

WORKER_LOCAL = {"rollover_days": 1, "delete_days": 8, "copies": 1,
                "primaries": 1}

FIXED_STORAGE_INDICES = ("cockpit-metrics", "trend-rollup", "template-catalog",
                         "cockpit-fleet", "alice-lane-state", "alice-signals",
                         "alice-incidents", "alice-notifications")

NEW_INDICES = ("template-triage", "shifter-queries")

BUCKET_FAMILIES = {
    "template-buckets-5m": {"period_days": 1, "delete_days": 4, "copies": 2},
    "template-buckets-1h": {"period_days": 31, "delete_days": 66,
                            "copies": 2},
}


def backing_indices(rollover_days, delete_days):
    return delete_days // rollover_days + 1


def shard_inventory(primaries, workers, full_retention):
    total = {"storage_logs": 0, "worker_local": 0, "unpinned": 0,
             "fixed_existing": 0, "fixed_new": 0, "buckets": 0}
    for family, spec in ROLLOVER_FAMILIES.items():
        count = (backing_indices(spec["rollover_days"], spec["delete_days"])
                 if full_retention else 1)
        shards = count * spec.get("primaries", primaries) * spec["copies"]
        if family == "alice-alert-actions":
            total["unpinned"] += shards
        else:
            total["storage_logs"] += shards
    count = (backing_indices(WORKER_LOCAL["rollover_days"],
                             WORKER_LOCAL["delete_days"])
             if full_retention else 1)
    total["worker_local"] = workers * count * WORKER_LOCAL["primaries"] * \
        WORKER_LOCAL["copies"]
    total["fixed_existing"] = len(FIXED_STORAGE_INDICES) * 3
    total["fixed_new"] = 3 + 1
    for spec in BUCKET_FAMILIES.values():
        count = (backing_indices(spec["period_days"], spec["delete_days"])
                 if full_retention else 1)
        total["buckets"] += count * spec["copies"]
    return total


def inventory_shape(name):
    inventory = _load_yaml(os.path.join(DEPLOY, name))
    children = inventory["all"]["children"]["alice_nodes"]["children"]
    workers = len(children["workers"]["hosts"])
    storage = children["storage"]["hosts"]
    control = list(inventory["all"]["children"]["control"]["hosts"].items())
    primaries = 1
    for _, host_vars in control:
        if host_vars and "log_primary_shards_storage" in host_vars:
            primaries = host_vars["log_primary_shards_storage"]
    return workers, len(storage), primaries


def test_the_repository_shard_model_reproduces_its_own_documented_numbers():
    assert backing_indices(7, 56) == 9
    assert backing_indices(7, 35) == 6
    assert backing_indices(1, 8) == 9
    assert backing_indices(7, 30) == 5
    at_one = shard_inventory(1, 2, True)["storage_logs"]
    at_three = shard_inventory(3, 2, True)["storage_logs"]
    assert at_one == 45
    assert at_three == 135


def test_staging_inventory_shard_totals():
    workers, storage, primaries = inventory_shape("inventory.yml")
    assert (workers, storage, primaries) == (2, 3, 1)
    fresh = shard_inventory(primaries, workers, False)
    full = shard_inventory(primaries, workers, True)
    assert sum(fresh.values()) - fresh["fixed_new"] - fresh["buckets"] == 33
    assert fresh["buckets"] == 4
    assert sum(fresh.values()) == 41
    assert sum(full.values()) - full["fixed_new"] - full["buckets"] == 92
    assert full["buckets"] == 16
    assert sum(full.values()) == 112


def test_farm_inventory_shard_totals():
    workers, storage, primaries = inventory_shape("inventory.epn.yml")
    assert (workers, storage, primaries) == (3, 3, 3)
    fresh = shard_inventory(primaries, workers, False)
    full = shard_inventory(primaries, workers, True)
    assert sum(fresh.values()) - fresh["fixed_new"] - fresh["buckets"] == 46
    assert sum(fresh.values()) == 54
    assert sum(full.values()) - full["fixed_new"] - full["buckets"] == 191
    assert sum(full.values()) == 211


def test_staging_storage_tier_is_over_its_heap_shard_budget_at_full_retention():
    workers, storage, primaries = inventory_shape("inventory.yml")
    full = shard_inventory(primaries, workers, True)
    pinned = (full["storage_logs"] + full["fixed_existing"]
              + full["fixed_new"] + full["buckets"])
    assert pinned == 89
    assert pinned - full["fixed_new"] - full["buckets"] == 69
    fresh = shard_inventory(primaries, workers, False)
    assert fresh["storage_logs"] + fresh["fixed_existing"] + \
        fresh["fixed_new"] + fresh["buckets"] == 38
    assert pinned > storage * 1 * 20


def test_farm_storage_tier_fits_its_heap_shard_budget():
    workers, storage, primaries = inventory_shape("inventory.epn.yml")
    full = shard_inventory(primaries, workers, True)
    pinned = (full["storage_logs"] + full["fixed_existing"]
              + full["fixed_new"] + full["buckets"])
    assert pinned == 179
    heap_gb = storage * 8
    assert pinned <= heap_gb * 20


def _stamper_unit(**overrides):
    values = dict(group_vars())
    values.update(role_defaults("sweet_collector"))
    values.update({"node_id": "node-01", "opensearch_http_port": 9200,
                   "ansible_managed": "managed",
                   "stamper_listen_socket": "/run/alice/stamper.sock",
                   "stamper_return_socket": "/run/alice/stamped.sock",
                   "stamper_status_file": "/run/alice/stamper-status.json",
                   "stamper_templating_dir": "/opt/sweet/templating",
                   "stamper_script": "/opt/sweet/stamper.py",
                   "stamper_venv": "/opt/sweet/stamper-venv",
                   "stamper_memory_high": "384M",
                   "stamper_memory_max": "768M"})
    values.update(overrides)
    env = _environment("sweet_collector")
    env.filters["basename"] = os.path.basename
    return env.get_template("alice-stamper.service.j2").render(**values)


def _shifter_unit(**overrides):
    values = dict(group_vars())
    values.update(role_defaults("sweet_shifter_view"))
    values.update({"ansible_managed": "managed", "opensearch_http_port": 9200,
                   "shifter_port": 8092, "shifter_ingest_path": "/ingest",
                   "shifter_opensearch_url": "http://control:9200"})
    values.update(overrides)
    return _environment("sweet_shifter_view").get_template(
        "alice-shifter.service.j2").render(**values)


def _collector_config(**overrides):
    values = dict(group_vars())
    values.update(role_defaults("sweet_collector"))
    values.update({"ansible_managed": "managed",
                   "collector_config_dir": "/etc/fluent-bit",
                   "collector_health_script": "/opt/sweet/fb_health.py",
                   "collector_health_interval_seconds": 10,
                   "collector_journald_path": "/var/log/journal",
                   "stamper_listen_socket": "/run/alice/stamper.sock",
                   "stamper_return_socket": "/run/alice/stamped.sock",
                   "stamper_status_file": "/run/alice/stamper-status.json",
                   "shifter_enabled": True, "shifter_host": "lane"})
    values.update(overrides)
    return yaml.safe_load(_environment("sweet_collector").get_template(
        "collector.yaml.j2").render(**values))


def test_every_durable_collector_output_states_the_create_operation():
    outputs = _collector_config()["pipeline"]["outputs"]
    stamped = [output for output in outputs if output.get("id_key")]
    assert len(stamped) == 4
    for output in stamped:
        assert output["name"] == "opensearch"
        assert output["id_key"] == "doc_id"
        assert output["write_operation"] == "create"


def _unit_environment(unit):
    found = {}
    for line in unit.splitlines():
        if line.startswith("Environment="):
            key, _, value = line[len("Environment="):].partition("=")
            found[key] = value
    return found


def _python_environment_names(path):
    source = open(path).read()
    return set(re.findall(r"os\.environ\.get\(\s*\n?\s*\"([A-Z0-9_]+)\"",
                          source))


def test_the_stamper_unit_exports_every_variable_its_python_reads():
    exported = set(_unit_environment(_stamper_unit()))
    wanted = set()
    for name in ("stamper.py", "forward.py"):
        wanted |= _python_environment_names(
            os.path.join(ROLES, "sweet_collector", "files", name))
    wanted.discard("PATH")
    missing = wanted - exported
    assert missing == {"STAMPER_TICK_SECONDS"}, sorted(missing)
    assert not exported - wanted - {"PYTHONUNBUFFERED"}, sorted(
        exported - wanted)


def test_the_stamper_unit_carries_the_plan_limits_and_the_collector_ceiling():
    exported = _unit_environment(_stamper_unit())
    shared = group_vars()
    assert exported["STAMPER_MAX_TEMPLATES"] == "20000"
    assert exported["STAMPER_PUBLISH_SECONDS"] == "300"
    assert exported["STAMPER_LEDGER_HOURS"] == "48"
    assert exported["STAMPER_CHUNK_HOLD_MS"] == "3600000"
    assert exported["STAMPER_LISTEN_SOCKET"] == "/run/alice/stamper.sock"
    assert exported["STAMPER_RETURN_SOCKET"] == "/run/alice/stamped.sock"
    assert exported["STAMPER_LOCAL_INDEX"] == "application-logs-local-node-01"
    assert exported["CATALOG_INDEX"] == shared["template_catalog_index"]
    assert exported["ALICE_SHARED_PATH"] == shared["alice_shared_dir"]
    unit = _stamper_unit()
    assert "MemoryMax=768M" in unit
    assert "MemoryHigh=384M" in unit
    assert "StateDirectory=alice-stamper" in unit
    assert "RuntimeDirectory=alice" in unit
    assert "Restart=always" in unit


def test_the_stamper_pins_drain3_and_msgpack():
    defaults = role_defaults("sweet_collector")
    assert defaults["stamper_drain3_version"] == "0.9.11"
    tasks = role_tasks("sweet_collector", "stamper.yml")
    pins = [task for task in tasks if "ansible.builtin.pip" in task]
    assert pins
    for task in pins:
        assert "drain3=={{ stamper_drain3_version }}" in \
            task["ansible.builtin.pip"]["name"]
        assert "msgpack=={{ stamper_msgpack_version }}" in \
            task["ansible.builtin.pip"]["name"]


def test_the_collector_runs_every_log_record_through_the_stamper_loop():
    config = _collector_config()
    inputs = config["pipeline"]["inputs"]
    outputs = config["pipeline"]["outputs"]
    returns = [i for i in inputs if i["name"] == "forward"]
    assert len(returns) == 1
    assert returns[0]["unix_path"] == "/run/alice/stamped.sock"
    assert returns[0]["tag_prefix"] == "stamped."
    assert returns[0]["storage.type"] == "filesystem"
    sends = [o for o in outputs if o["name"] == "forward"]
    assert len(sends) == 1
    assert sends[0]["unix_path"] == "/run/alice/stamper.sock"
    assert sends[0]["require_ack_response"] is True
    assert sends[0]["workers"] == 1
    assert sends[0]["retry_limit"] == "no_limits"
    assert sends[0]["match_regex"] == \
        "^(infologger|ildaemon|family\\.local|family\\.central)$"
    matches = {o.get("match") for o in outputs if o["name"] == "opensearch"}
    assert matches == {"stamped.infologger", "stamped.family.local",
                       "stamped.family.central", "stamped.ildaemon", "health"}
    live = [o for o in outputs if o["name"] == "http"]
    assert live[0]["match_regex"] == \
        "^stamped\\.(infologger|ildaemon|family\\.central)$"
    health = [i for i in inputs if i["name"] == "exec"][0]
    assert "STAMPER_STATUS_FILE=/run/alice/stamper-status.json" in \
        health["command"]


def _plays_for(role_name):
    site = _load_yaml(os.path.join(DEPLOY, "playbooks", "site.yml"))
    found = []
    for play in site:
        for role in play.get("roles") or []:
            name = role["role"] if isinstance(role, dict) else role
            if name == role_name:
                found.append((play, role))
    return found


def test_the_stamper_runs_beside_the_collector_and_before_it():
    plays = _plays_for("sweet_collector")
    assert len(plays) == 1
    play, role = plays[0]
    assert play["hosts"] == "workers"
    assert role == "sweet_collector"

    imported = [task["ansible.builtin.import_tasks"]
                for task in role_tasks("sweet_collector")]
    assert imported == ["stamper.yml", "collector.yml"]


def test_the_socket_contract_is_declared_exactly_once():
    """The reason the two roles became one.

    Fluent Bit's Forward output writes to the socket alice-stamper listens on,
    and its Forward input reads the one the stamper writes back to. While these
    were two roles the same four strings were declared in two namespaces with
    nothing asserting they matched, so a rename on one side pointed the output
    at a socket nobody listened on: no error at deploy time, no records
    stamped.
    """
    defaults = role_defaults("sweet_collector")
    contract = ("stamper_socket_dir", "stamper_listen_socket",
                "stamper_return_socket", "stamper_socket_mode",
                "stamper_status_file")
    for name in contract:
        assert name in defaults, name
        assert "collector_" + name not in defaults

    role = os.path.join(ROLES, "sweet_collector")
    for directory in ("tasks", "templates"):
        for name in sorted(os.listdir(os.path.join(role, directory))):
            source = open(os.path.join(role, directory, name)).read()
            assert "collector_stamper_" not in source, f"{directory}/{name}"

    config = _collector_config()
    unit = _unit_environment(_stamper_unit())
    sends = [o for o in config["pipeline"]["outputs"]
             if o["name"] == "forward"][0]
    returns = [i for i in config["pipeline"]["inputs"]
               if i["name"] == "forward"][0]
    assert sends["unix_path"] == unit["STAMPER_LISTEN_SOCKET"]
    assert returns["unix_path"] == unit["STAMPER_RETURN_SOCKET"]


def test_the_maintenance_unit_resolves_against_the_role_defaults():
    values = dict(group_vars())
    values.update(role_defaults("sweet_template_catalog"))
    values.update({"ansible_managed": "managed", "opensearch_http_port": 9200})
    env = _environment("sweet_template_catalog")
    env.filters["basename"] = os.path.basename
    env.filters["int"] = int
    unit = env.get_template(
        "alice-catalog-maintenance.service.j2").render(**values)
    exported = _unit_environment(unit)
    wanted = _python_environment_names(
        os.path.join(ROLES, "sweet_template_catalog", "files",
                     "catalog_maintenance.py"))
    assert exported["CATALOG_RETENTION_DAYS"] == "90"
    assert exported["CATALOG_CHECK_RETENTION_DAYS"] == "35"
    assert exported["BUCKETS_1H_PATTERN"] == "template-buckets-1h-*"
    assert exported["CATALOG_SHARED_INDICES"] == \
        "application-logs-central,infologger"
    assert exported["CATALOG_CHECK_HOURS"] == "1"
    assert exported["CATALOG_CHECK_LAG_HOURS"] == "1"
    assert "MemoryMax=256M" in unit
    assert wanted - set(exported) == {"CATALOG_CLEANUP_SCROLL"}


def test_the_maintenance_unit_carries_the_query_history_expiry():
    values = dict(group_vars())
    values.update(role_defaults("sweet_template_catalog"))
    values.update({"ansible_managed": "managed", "opensearch_http_port": 9200})
    env = _environment("sweet_template_catalog")
    env.filters["basename"] = os.path.basename
    env.filters["int"] = int
    unit = env.get_template(
        "alice-catalog-maintenance.service.j2").render(**values)
    exported = _unit_environment(unit)
    defaults = role_defaults("sweet_template_catalog")
    assert exported["QUERIES_INDEX"] == group_vars()["shifter_queries_index"]
    assert exported["CATALOG_QUERY_RETENTION_DAYS"] == str(
        defaults["template_catalog_query_retention_days"])
    assert exported["CATALOG_QUERY_CLEANUP_INTERVAL_HOURS"] == str(
        defaults["template_catalog_query_cleanup_interval_hours"])
    assert defaults["template_catalog_query_retention_days"] == 365
    source = open(os.path.join(ROLES, "sweet_template_catalog", "files",
                               "catalog_maintenance.py")).read()
    for name in ("QUERIES_INDEX", "CATALOG_QUERY_RETENTION_DAYS",
                 "CATALOG_QUERY_CLEANUP_INTERVAL_HOURS"):
        assert 'os.environ.get(\n    "%s"' % name in source \
            or 'os.environ.get("%s"' % name in source, name


def test_the_maintenance_pass_expires_the_query_history_and_runs_the_checks():
    source = open(os.path.join(ROLES, "sweet_template_catalog", "files",
                               "catalog_maintenance.py")).read()
    assert "def expire_queries(" in source
    assert "def run_checks(" in source
    body = source.split("def run_pass(", 1)[1].split("\ndef ", 1)[0]
    assert "expire_queries(" in body
    assert "run_checks(" in body
    assert "QUERIES_SECTION" in body
    assert "CHECKS_SECTION" in body
    assert "queries_age_ms" in body
    assert "snapshot" not in source


def test_the_maintenance_timer_lands_on_exactly_one_host():
    """One play, one host group, and not a worker.

    Nothing the pass touches is worker-local: it expires documents in
    template-catalog and shifter-queries and aggregates over the shared log
    indices, all of which live on the storage tier. Running it on a worker put
    an hourly delete-by-query on a machine whose job is ingesting, and needed a
    host guard on every task to keep the fleet-wide pass to one host.
    """
    plays = _plays_for("sweet_template_catalog")
    assert len(plays) == 1
    play, role = plays[0]
    assert play["hosts"] == "control"
    assert role == "sweet_template_catalog"
    for task in role_tasks("sweet_template_catalog"):
        assert "when" not in task, json.dumps(task)
    assert "template_catalog_maintenance_host" not in group_vars()


def test_the_stamper_ships_the_shared_contract_the_recipe_and_its_modules():
    tasks = role_tasks("sweet_collector", "stamper.yml")
    copied = []
    for task in tasks:
        copy = task.get("ansible.builtin.copy")
        if copy:
            copied.append((copy.get("src"), copy.get("dest")))
    assert ("{{ alice_shared_contract_file }}",
            "{{ alice_shared_dir }}/template_contract.py") in copied
    loops = [task.get("loop") for task in tasks if task.get("loop")]
    assert ["stamper.py", "forward.py"] in loops
    assert ["drainbench.py", "masking.py"] in loops
    files = os.listdir(os.path.join(ROLES, "sweet_template_catalog", "files"))
    assert "template_catalog.py" not in files
    assert "snapshot.py" not in files
    assert "ledger.py" not in files


VENDORED_TEMPLATING = ("drainbench.py", "masking.py")


@pytest.mark.parametrize("role", ("sweet_collector", "sweet_shifter_view"))
@pytest.mark.parametrize("name", VENDORED_TEMPLATING)
def test_the_vendored_templating_copy_matches_its_source(role, name):
    source = os.path.join(DEPLOY, os.pardir, "tools", "templating", name)
    if not os.path.exists(source):
        pytest.skip("tools/templating is not present in this tree")
    with open(source, "rb") as handle:
        expected = handle.read()
    with open(os.path.join(ROLES, role, "files", name), "rb") as handle:
        got = handle.read()
    assert got == expected, (
        f"roles/{role}/files/{name} has drifted from tools/templating/{name}. "
        f"Edit tools/templating and copy it into both roles.")


def test_the_vendored_contract_copy_matches_its_source():
    source = os.path.join(DEPLOY, "shared", "template_contract.py")
    with open(source, "rb") as handle:
        expected = handle.read()
    copy = os.path.join(ROLES, "sweet_shifter_view", "files", "template_contract.py")
    with open(copy, "rb") as handle:
        got = handle.read()
    assert got == expected, (
        "roles/sweet_shifter_view/files/template_contract.py has drifted from "
        "deploy/shared/template_contract.py. Edit deploy/shared and copy it into "
        "the role.")


def test_the_vendored_replay_engine_matches_its_source():
    source = os.path.join(DEPLOY, os.pardir, "images", "replay", "replay.py")
    if not os.path.exists(source):
        pytest.skip("images/replay is not present in this tree")
    with open(source, "rb") as handle:
        expected = handle.read()
    with open(os.path.join(ROLES, "sweet_replay", "files", "replay.py"), "rb") as handle:
        got = handle.read()
    assert got == expected, (
        "roles/sweet_replay/files/replay.py has drifted from images/replay/replay.py. "
        "Edit images/replay and copy it into the role.")


@pytest.mark.parametrize("role,name", [
    ("sweet_collector", "stamper.yml"), ("sweet_collector", "collector.yml"),
    ("sweet_template_catalog", "main.yml"), ("sweet_shifter_view", "main.yml"),
])
def test_no_role_task_reaches_outside_its_own_directory(role, name):
    for task in role_tasks(role, name):
        for action in ("ansible.builtin.copy", "ansible.builtin.template"):
            src = (task.get(action) or {}).get("src")
            if src:
                assert os.pardir not in src.split("/"), \
                    f"{role}: {action} src escapes the role: {src}"
        for item in task.get("loop") or []:
            if isinstance(item, str):
                assert os.pardir not in item.split("/"), \
                    f"{role}: loop item escapes the role: {item}"


def test_the_shifter_unit_carries_the_new_serving_limits():
    exported = _unit_environment(_shifter_unit())
    defaults = role_defaults("sweet_shifter_view")
    shared = group_vars()
    assert exported["ALICE_SHARED_PATH"] == shared["alice_shared_dir"]
    assert "SHIFTER_METRICS_INDEX" not in exported
    assert "SHIFTER_SNAPSHOT_REFRESH_SECONDS" not in exported
    assert "SHIFTER_TEMPLATES_MANIFEST_PAGE" not in exported
    assert "SHIFTER_TEMPLATES_CHUNK_PAGE" not in exported
    assert "SHIFTER_SNAPSHOT_MAX_AGE_SECONDS" not in exported
    assert exported["SHIFTER_CATALOG_INDEX"] == shared["template_catalog_index"]
    assert exported["SHIFTER_TRIAGE_INDEX"] == shared["template_triage_index"]
    assert exported["SHIFTER_QUERIES_INDEX"] == shared["shifter_queries_index"]
    assert exported["SHIFTER_TEMPLATE_CONCURRENT_QUERIES"] == "2"
    assert exported["SHIFTER_TEMPLATE_ENCODERS"] == "1"
    assert exported["SHIFTER_TEMPLATE_PAGE_ROWS"] == "50"
    assert exported["SHIFTER_EPISODE_REFRESH_SECONDS"] == "30"
    assert exported["SHIFTER_VIEW_REFRESH_SECONDS"] == str(
        defaults["shifter_view_refresh_seconds"])
    assert exported["SHIFTER_SEARCH_TIMEOUT_SECONDS"] == str(
        defaults["shifter_search_timeout_seconds"])
    assert exported["SHIFTER_SEARCH_TERMINATE_AFTER"] == str(
        defaults["shifter_search_terminate_after"])
    assert exported["SHIFTER_TEMPLATE_WATCHED_MAX"] == "100"
    assert exported["SHIFTER_TEMPLATE_NOTE_MAX_CHARS"] == "2000"
    assert exported["SHIFTER_TEMPLATE_LABEL_HISTORY_MAX"] == "50"
    assert exported["SHIFTER_ACTIVE_DAYS"] == "28"
    assert exported["SHIFTER_DEFINITION_RETENTION_DAYS"] == "90"
    for key in ("SHIFTER_VECTOR_CACHE_BYTES", "SHIFTER_CATALOG_CACHE_BYTES",
                "SHIFTER_RESPONSE_CACHE_BYTES", "SHIFTER_AGGREGATION_BYTES"):
        assert int(exported[key]) == defaults[key.lower().replace(
            "shifter_", "shifter_")]


def test_semantic_search_ships_on_and_names_the_measured_revision():
    defaults = role_defaults("sweet_shifter_view")
    assert defaults["shifter_semantic_enabled"] is True
    assert defaults["shifter_semantic_backend"] == "model2vec"
    assert defaults["shifter_semantic_model_repo"] == \
        "minishlab/potion-retrieval-32M"
    assert defaults["shifter_semantic_model_revision"] == \
        "6fc8051fab2a1e0ee76689cf08c853792ac285e7"
    exported = _unit_environment(_shifter_unit())
    assert exported["SHIFTER_SEMANTIC_ENABLED"] == "true"
    assert exported["SHIFTER_SEMANTIC_BACKEND"] == "model2vec"
    assert exported["SHIFTER_SEMANTIC_MODEL_PATH"].endswith(
        "/models/potion-retrieval-32M")
    assert exported["SHIFTER_SEMANTIC_MODEL_REVISION"] == \
        defaults["shifter_semantic_model_revision"]
    assert exported["HF_HUB_OFFLINE"] == "1"


def test_the_unit_runs_the_venv_interpreter_whenever_it_needs_a_library():
    assert "ExecStart=/opt/sweet/shifter-venv/bin/python" in \
        _shifter_unit()
    off = _shifter_unit(shifter_semantic_enabled=False,
                        shifter_semantic_backend="none")
    assert "ExecStart=/opt/sweet/shifter-venv/bin/python" in off
    bare = _shifter_unit(shifter_semantic_enabled=False,
                         shifter_semantic_backend="none",
                         shifter_templates_enabled=False)
    assert "ExecStart=/usr/bin/python3" in bare
    assert "shifter-venv" not in bare


def test_a_host_that_turns_semantic_off_keeps_the_small_ceiling():
    staging = _load_yaml(os.path.join(DEPLOY, "inventory.yml"))
    host = staging["all"]["children"]["shifter"]["hosts"]["alice-ingest-5"]
    assert host["shifter_semantic_enabled"] is False
    assert host["shifter_semantic_backend"] == "none"
    assert host["shifter_memory_max"] == "384M"
    farm = _load_yaml(os.path.join(DEPLOY, "inventory.epn.yml"))
    assert farm["all"]["children"]["shifter"]["hosts"]["os-node-06"] in (
        None, {})


def test_the_shifter_caches_fit_inside_the_service_ceiling():
    defaults = role_defaults("sweet_shifter_view")
    caches = (defaults["shifter_vector_cache_bytes"]
              + defaults["shifter_catalog_cache_bytes"]
              + defaults["shifter_response_cache_bytes"]
              + defaults["shifter_aggregation_bytes"])
    assert caches == 167772160
    assert defaults["shifter_memory_high"] == "1G"
    assert defaults["shifter_memory_max"] == "2G"
    assert caches < 1024 * 1024 * 1024
    unit = _shifter_unit()
    assert "MemoryHigh=1G" in unit
    assert "MemoryMax=2G" in unit


def test_the_unit_hands_the_page_every_number_its_budget_check_needs():
    defaults = role_defaults("sweet_shifter_view")
    exported = _unit_environment(_shifter_unit())
    assert exported["SHIFTER_MEMORY_MAX"] == defaults["shifter_memory_max"]
    assert int(exported["SHIFTER_LIVE_LANE_BYTES"]) == defaults[
        "shifter_live_lane_bytes"]
    assert int(exported["SHIFTER_PROCESS_BASE_BYTES"]) == defaults[
        "shifter_process_base_bytes"]


MODEL_LOAD_PEAK_BYTES = 341311488


def test_the_declared_serving_peak_fits_the_service_ceiling():
    defaults = role_defaults("sweet_shifter_view")
    peak = (defaults["shifter_process_base_bytes"]
            + defaults["shifter_live_lane_bytes"]
            + defaults["shifter_vector_cache_bytes"]
            + defaults["shifter_catalog_cache_bytes"] * 2
            + defaults["shifter_response_cache_bytes"]
            + defaults["shifter_aggregation_bytes"])
    assert peak == 369098752
    with_model = peak + MODEL_LOAD_PEAK_BYTES
    assert with_model == 710410240
    assert with_model <= 1024 * 1024 * 1024
    assert with_model <= 2 * 1024 * 1024 * 1024


def test_the_ceiling_the_page_is_told_matches_the_unit():
    defaults = role_defaults("sweet_shifter_view")
    exported = _unit_environment(_shifter_unit())
    assert exported["SHIFTER_MEMORY_MAX"] == defaults["shifter_memory_max"]
    assert exported["SHIFTER_MEMORY_MAX"] == "2G"


def test_one_vector_per_canonical_group_fits_the_vector_cache():
    defaults = role_defaults("sweet_shifter_view")
    assert 5301 * 512 * 4 == 10856448
    assert defaults["shifter_vector_cache_bytes"] >= 10856448


def test_the_templates_page_is_off_when_the_query_lane_is_off():
    values = dict(group_vars())
    values.update(role_defaults("sweet_shifter_view"))
    values.update({"ansible_managed": "managed", "shifter_port": 8092,
                   "shifter_ingest_path": "/ingest",
                   "shifter_opensearch_url": ""})
    unit = _environment("sweet_shifter_view").get_template(
        "alice-shifter.service.j2").render(**values)
    assert "SHIFTER_CATALOG_INDEX" not in unit


TEMPLATE_SUPPLIED = {
    "ansible_managed", "node_id", "opensearch_http_port", "shifter_port",
    "shifter_ingest_path", "log_primary_shards_storage",
    "opensearch_cluster_config_worker_node_ids", "log_rollover_migrate_existing",
    "opensearch_info_search_idle_after", "opensearch_info_translog_sync_interval",
    "opensearch_info_merge_threads", "item", "wn",
}


SCHEMA_TEMPLATES = sorted(
    "schema/" + name
    for name in os.listdir(os.path.join(ROLES, "sweet_opensearch",
                                        "templates", "schema"))
    if name.endswith(".json.j2"))


@pytest.mark.parametrize("role,template", [
    ("sweet_opensearch", "templates.sh.j2"),
] + [("sweet_opensearch", name) for name in SCHEMA_TEMPLATES] + [
    ("sweet_collector", "alice-stamper.service.j2"),
    ("sweet_template_catalog", "alice-catalog-maintenance.service.j2"),
    ("sweet_template_catalog", "alice-catalog-maintenance.timer.j2"),
    ("sweet_shifter_view", "alice-shifter.service.j2"),
])
def test_every_variable_a_template_names_is_declared_somewhere(role, template):
    source = open(os.path.join(ROLES, role, "templates", template)).read()
    source = re.sub(r"\{%\s*raw\s*%\}.*?\{%\s*endraw\s*%\}", "", source,
                    flags=re.S)
    known = set(group_vars()) | set(role_defaults(role)) | TEMPLATE_SUPPLIED
    for other in ("sweet_collector", "sweet_template_catalog", "sweet_shifter_view",
                  "sweet_opensearch"):
        known |= set(role_defaults(other))
    used = set()
    for expression in re.findall(r"\{\{(.*?)\}\}", source, re.S):
        used |= set(re.findall(r"[a-z_][a-z0-9_]*", expression.split("|")[0]))
    for expression in re.findall(r"\{%\s*if(.*?)%\}", source, re.S):
        used |= set(re.findall(r"[a-z_][a-z0-9_]*", expression.split("|")[0]))
    undeclared = {name for name in used if name not in known}
    undeclared -= {"raw", "endraw", "join", "bool", "ternary", "length",
                   "lower", "true", "false", "and", "or", "not", "if", "else",
                   "map", "list", "unique", "string", "default", "in"}
    assert not undeclared, sorted(undeclared)
