# `opensearch_bootstrap`

Applies the cluster-wide OpenSearch state that must exist exactly once: the
ingest pipeline, the component and index templates, the persistent cluster
settings, the pre-created indices, and the retention policies. It runs on the
control host, against the cluster as a whole.

It writes no configuration on any node and starts no service. It talks only to
the local OpenSearch REST API, and every call is idempotent, so the role runs on
every deploy.

## Why it is a separate role

It was part of `dashboards/tasks/bootstrap.yml` until August 2026. Three reasons
it is not:

- **It is OpenSearch state, not Dashboards state.** Index templates, an ingest
  pipeline, mappings, cluster settings and ISM policies exist whether or not
  anything ever renders a chart.
- **It has a different scope from the `opensearch` role.** That role configures
  one node and runs on all five. This one configures the cluster and must run
  once. Folding it into `opensearch` would apply the same cluster-wide calls
  five times.
- **It has to run before the rest of the Dashboards bootstrap.** Index patterns,
  the cockpit import and the alerting monitors all need the indices to exist
  first. As a separate play the ordering is one line at the call site.

Where the rest of it went: the index patterns, the cockpit saved objects and
the field-catalog hydration stayed in `dashboards`; the alerting monitors are
now the `alerting_monitors` role; the anomaly detectors, the forecaster and the
verification are now the `anomaly_detection` role.

## What it does

```
                    CONTROL HOST, ONCE PER DEPLOY

┌─ 1. GUARDS — fail here, not at the REST call ──────────────────────────────┐
│  admission_control_mode        must parse, or the settings PUT is a 400    │
│  worker node identity list     must not be empty, or there is no info tier │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. STAGE — /opt/alice-ingest/init ────────────────────────────────────────┐
│  templates.sh        rendered from templates.sh.j2                         │
│  ism.sh              rendered from ism.sh.j2                               │
│  register_node.sh    installed through the opensearch_local_index_registration role          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. templates.sh — waits for cluster health, then applies ─────────────────┐
│  ingest pipeline        alice-add-ingest-time                              │
│  component templates    generic mappings, infologger mappings              │
│  index templates        10, one per log family and derived index           │
│  cluster settings       auto_create_index, query insights,                 │
│                         anomaly-detection batch pacing, admission control  │
│  register_node.sh       once per worker identity — the same file that      │
│                         worker runs as ExecStartPre at boot                │
│  pre-created indices    the 7 derived indices, so nothing races a mapping  │
│  live mapping updates   fields added to indices that predate them          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. ism.sh — retention, one policy per family ─────────────────────────────┐
│  alice-generic-info-retention        8d                                    │
│  alice-generic-other-retention      35d                                    │
│  alice-infologger-retention         56d                                    │
│  alice-ad-results-retention         14d                                    │
│  alice-alert-history-retention      30d                                    │
│  alice-alert-actions-retention      30d                                    │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The admission-control mode is checked before anything runs.**
  `AdmissionControlMode.fromName` parses only `disabled`, `monitor_only` and
  `enforced`, and throws on anything else. OpenSearch then answers 400 to the
  whole persistent settings body, which carries the anomaly-detection batch
  pacing down with it and makes `templates.sh` exit non-zero. The assertion names
  the cause; the 400 would not.
- **`register_node.sh` is run from here, once per worker.** Each worker also runs
  the same file as `ExecStartPre`. The control host cannot wait for the
  collectors to start, because the detectors provisioned later in the deploy need
  the worker index templates to exist already.
- **`templates.sh` finds the registration script as `$(dirname "$0")/register_node.sh`.**
  Both files must land in the same directory. That is why the role installs the
  script rather than pointing at the copy the `collector` role installs on a
  worker.
- **`action.auto_create_index` forbids the bare log-family names.** Those names
  belong to rollover write aliases. If ingest reaches one while its alias is
  briefly absent, OpenSearch would create a concrete index with a dynamic
  mapping, which blocks the alias permanently and turns `collector_time` into a
  long and `host` into text. Rejecting those writes loses seconds of records and
  is the better outcome.
- **`ism.sh` is the authoritative retention attach and runs after `templates.sh`.**
  `register_node.sh` attaches the same policy opportunistically on a fresh
  cluster; the run here is what makes it true.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `opensearch_bootstrap_root` | `/opt/alice-ingest/init` | Where the scripts are staged. Shared — see couplings. |
| `opensearch_bootstrap_templates_script` | `{{ opensearch_bootstrap_root }}/templates.sh` | The rendered index-template script. |
| `opensearch_bootstrap_ism_script` | `{{ opensearch_bootstrap_root }}/ism.sh` | The rendered retention script. |
| `opensearch_bootstrap_register_node_script` | `{{ opensearch_bootstrap_root }}/register_node.sh` | Installed through `opensearch_local_index_registration`. Must sit beside `templates.sh`. |
| `opensearch_bootstrap_worker_node_ids` | `[]` | The worker identities that get a per-node index template, write alias and retention attach. The playbook supplies it. |

### Variables the role requires but does not own

All from `group_vars/all.yml`, and all read by the two templates.

| Group | Variables |
|---|---|
| Connection | `opensearch_http_port` |
| Info tier | `opensearch_info_search_idle_after`, `opensearch_info_translog_sync_interval`, `opensearch_info_merge_threads` |
| Shards and rollover | `log_primary_shards_storage`, `log_rollover_period`, `log_rollover_period_info`, `log_rollover_max_size`, `log_rollover_migrate_existing`, `alert_actions_rollover_period`, `alert_actions_rollover_max_size` |
| Retention | `ism_retention_generic_info`, `ism_retention_generic_other`, `ism_retention_infologger`, `ism_retention_ad_results`, `ism_retention_alert_history`, `ism_retention_alert_actions` |
| Cluster settings | `admission_control_mode`, `admission_control_cpu_limit`, `ad_max_batch_task_per_node`, `ad_batch_task_piece_interval_seconds` |
| Index names | `cockpit_metrics_index`, `trend_rollup_index`, `fleet_roster_index`, `lane_state_index`, `signals_index`, `incidents_index`, `notifications_index` |

## How to use it

```yaml
- name: OpenSearch cluster bootstrap
  hosts: control
  become: true
  roles:
    - opensearch_bootstrap
```

- **Run it on exactly one host.** Every call is cluster-wide. Running it on five
  nodes applies the same state five times for no benefit.
- **The OpenSearch cluster must already answer.** `templates.sh` waits for
  `_cluster/health` at yellow for up to 3 minutes and then fails.
- **It must run before the `dashboards`, `alerting_monitors` and
  `anomaly_detection` roles.** Index patterns, the cockpit import and the
  alerting monitors all read indices this role creates.
- **The role reports `changed` on every run.** Both scripts are idempotent but
  give no machine-readable changed signal, so the tasks declare
  `changed_when: true` rather than claim a state they cannot detect.

## Couplings

- **`opensearch_bootstrap_root` is shared with the `alice_runtime` role.** Both
  roles create the directory, with the same owner, group and mode, and each
  writes its own scripts into it; `dashboards`, `alerting_monitors` and
  `anomaly_detection` only write into it. `alice_runtime` also stages a
  world-readable signal catalog there for the `DynamicUser` services, which is
  why the directory is `0755` and the scripts inside it are `0750`.
- **`dashboards_ops_templates_script` must match
  `opensearch_bootstrap_templates_script`.** The ops page re-applies the index
  templates through `alice-ops.service`. That unit is written by the `alice_ops`
  role, which holds the path as a literal: a default reading another role's
  variable resolves lazily and would make `alice_ops` unrunnable alone.
- **`playbooks/replay.yml` also runs `templates.sh`.** It carries the path as the
  play variable `bootstrap_root`, with `SEED_EMPTY_INDICES=false`, to rebuild the
  write aliases after a fresh replay. Moving the staging directory means changing
  it there too.
- **Retention policy names are literals in `ism.sh.j2`.** `verify_detection.py`
  asserts them by name and `register_node.sh` falls back to
  `alice-generic-info-retention`. A variable would only let one end of the set
  move.

## What is frozen

The domain layer is not parameterised. These scripts are the schema.

- The field mappings in `templates.sh.j2`, both component templates and every
  live mapping update.
- The ingest pipeline `alice-add-ingest-time`.
- The index and alias names, which are matched by the index templates, the
  detectors, the monitors and the cockpit.
- The ISM state machines in `ism.sh.j2`. Only their ages and sizes are variables.

## Upstream roles rejected

No candidate was searched for and none applies. This role holds the log
platform's schema and retention design. There is no generic role that could
express it, and a role that could would only be this file with its contents in a
variable dictionary.

The install-and-configure question is answered in
[`roles/opensearch/README.md`](../opensearch/README.md).

## Used by

- `playbooks/site.yml`, play "OpenSearch cluster bootstrap", against `control` —
  the only caller.

## Includes

- `opensearch_local_index_registration` — installs `register_node.sh` beside `templates.sh`.
