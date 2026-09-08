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
- **It has a different scope from the `sweet_opensearch` role.** That role configures
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
│  index templates        14, one per log family and derived index           │
│  cluster settings       auto_create_index, query insights,                 │
│                         anomaly-detection batch pacing, admission control  │
│  register_node.sh       once per worker identity — the same file that      │
│                         worker runs as ExecStartPre at boot                │
│  pre-created indices    the 11 derived indices, so nothing races a mapping │
│  live mapping updates   fields added to indices that predate them          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. ism.sh — retention, one policy per family ─────────────────────────────┐
│  alice-application-local-retention        8d                               │
│  alice-application-central-retention      35d                              │
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
- **The three fixed Templates-page indices appear in no ISM policy; the two
  bucket families do.** `template-catalog`, `template-triage` and
  `shifter-queries` never roll over. Their retention is document expiry through
  a bounded delete-by-query, which is the pattern `cockpit-metrics` and
  `trend-rollup` already use. The stamper's bucket documents go into date-named
  indices, `template-buckets-5m-<day>` and `template-buckets-1h-<month>`, that
  an ISM age policy on the pattern deletes; they are not rolled over, because a
  bucket is republished in place while it is inside the worker's ledger and a
  rollover alias would put the republication in a new index beside the old
  document. The age is padded by one index period because `min_index_age`
  counts from creation.
- **Every fixed index gets its template's mapping pushed onto the live index.**
  `ensure_index` skips an index that already exists, so a changed mapping would
  otherwise reach only a cluster that had never held that index. The loop after
  the `ensure_index` calls reads each index template back and PUTs its
  `properties` onto the index itself. `template-catalog` is in that loop because
  it is not a new index and its mapping changed; without the PUT, `last_observed`
  would stay unmapped under `dynamic: false`, the 90-day expiry would match
  nothing and the inactive history would come back empty, both without an error.
- **The `template-catalog` mapping holds three kinds of document.** Template
  definitions (`kind: template`), keyed by the version identifier and upserted
  by every stamper that observed the version; one watermark per node
  (`kind: watermark`); and the check results (`kind: check`). It carries no
  count: volume is a nested aggregation over the hourly bucket documents.
- **The bucket mappings index `versions` flat and `counts` nested.** A search
  expands a version to its descendants and routes by the `versions` keyword;
  the 28-day sum is one nested aggregation on `counts.version_id` and
  `counts.count`. The total beside the counts is what the conservation check
  compares them against.
- **Both log component mappings carry `template_version`, `template_id` and
  `template_status`.** The InfoLogger mapping is `dynamic: strict`, so the
  stamper's fields are not optional there.

### The shard inventory the Templates-page indices land in

The plan quotes an earlier 45-shard figure and asks for the real one. The 45 is
the two storage-tier log families at one primary and full retention, and it
counts nothing else. `deploy/test_provisioning.py` recomputes the whole
inventory from the role defaults and both inventories, and reproduces both of
this repository's own published numbers: 45 shards for those two families at one
primary, and 135 at three.

A rollover family holds `delete_days // rollover_days + 1` backing indices at
full retention, and each backing index costs `primaries x (1 + replicas)`
shards. The two date-named bucket families follow the same arithmetic with the
index period in place of the rollover period: four day indices of the
five-minute series and three month indices of the hourly series at full
retention, one replica each.

| | `inventory.yml` | `inventory.epn.yml` |
|---|---|---|
| Storage primaries | 1 | 3 |
| Workers | 2 | 3 |
| At first bootstrap, before the Templates page | 33 | 46 |
| At first bootstrap, now | **41** | **54** |
| At full retention, before the Templates page | 92 | 191 |
| At full retention, now | **112** | **211** |

The two fixed new indices add four shards; the bucket families add four at
first bootstrap and sixteen at full retention.

On the farm only the shards pinned by `index.routing.allocation.require.role=storage`
consume storage-tier heap: 179 of the 211 at full retention. The three storage
nodes carry an 8 GB heap each, so at the repository's own rule of roughly 20
shards per gigabyte the budget is about 480 shards. The inventory uses 37
percent of it and the Templates-page indices use 4 percent.

`inventory.yml` is tighter for a reason that predates this change. Its three
storage nodes carry 1 GB heaps, which is a budget of about 60 shards, and the
storage-pinned inventory at full retention is 89 — 69 before the Templates
page. The staging cluster is over that guideline once the 56-day InfoLogger
retention fills, with or without the Templates page. At first bootstrap it is
38 shards and inside the budget.

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
| Retention | `ism_retention_application_local`, `ism_retention_application_central`, `ism_retention_infologger`, `ism_retention_ad_results`, `ism_retention_alert_history`, `ism_retention_alert_actions`, `ism_retention_template_buckets_5m`, `ism_retention_template_buckets_1h` |
| Cluster settings | `admission_control_mode`, `admission_control_cpu_limit`, `ad_max_batch_task_per_node`, `ad_batch_task_piece_interval_seconds` |
| Index names | `cockpit_metrics_index`, `trend_rollup_index`, `fleet_roster_index`, `lane_state_index`, `signals_index`, `incidents_index`, `notifications_index`, `template_catalog_index`, `template_buckets_5m_prefix`, `template_buckets_1h_prefix`, `template_buckets_replicas`, `template_triage_index`, `shifter_queries_index` |
| Templates page retention | `template_catalog_active_days`, `template_catalog_definition_retention_days`, `stamper_ledger_hours` |

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
- **`alice_ops_templates_script` must match
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
  `alice-application-local-retention`. A variable would only let one end of the set
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
