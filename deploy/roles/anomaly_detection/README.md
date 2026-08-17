# `anomaly_detection`

Provisions the machine-learning layer of the platform on the control host: 17
Random Cut Forest anomaly detectors, 1 disk-fill forecaster, and the script that
proves the whole detection layer is present and correct.

It stages the definitions and the two upsert scripts, waits until the metrics
poller has produced the documents the detectors read, upserts and starts every
detector and the forecaster, removes the bootstrap seed documents, and then runs
`verify_detection.py`.

The role does not create indices, index templates or the alerting monitors. The
`opensearch_bootstrap` role creates the indices; the `alerting_monitors` role
creates the monitors.

## Why it is a separate role

The detectors and the forecaster are one closed set of artefacts with one
lifecycle. A detector definition, its JSON file, its upsert script and the count
that gates the deploy all change together. Splitting them out of the old
`dashboards` role means a person adding a detector touches one directory, and
means the detection layer can be re-run without reinstalling OpenSearch
Dashboards or nginx.

`verify_detection.py` lives here because it verifies detectors. Two other roles
run the same installed copy — see couplings.

## What it does

```
                          CONTROL HOST ONLY

┌─ 1. STAGE — into /opt/alice-ingest/init, which it does not create ─────────┐
│  detectors.sh          0750 root   rendered from detectors.sh.j2           │
│  forecasters.sh        0750 root   rendered from forecasters.sh.j2         │
│  detectors/            0640 root   17 detector definitions                 │
│  forecasters/          0640 root   1 forecaster definition                 │
│  backtest.py           0755 root   into /opt/alice-ingest, historical run  │
│  verify_detection.py   0750 root   the detection-layer gate                │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. WAIT — the detectors have no input until the poller writes ────────────┐
│  cockpit-metrics/_count  kind=node and kind=osd, 20 attempts, 6 s apart    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. UPSERT AND START ──────────────────────────────────────────────────────┐
│  detectors.sh     upsert by name, start each; unchanged detectors keep     │
│                   their trained RCF models                                 │
│  forecasters.sh   pins the two cluster forecast settings, then upserts     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. CLEAN UP THE SEEDS ────────────────────────────────────────────────────┐
│  DELETE alice-bootstrap-seed from infologger, generic-log-other and        │
│  generic-log-info-<node_id> for every collector node. Never fails the run. │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. VERIFY ────────────────────────────────────────────────────────────────┐
│  verify_detection.py   monitor count, detector count, forecaster count,    │
│                        ISM policy, the index set and the signal catalog    │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The wait on `cockpit-metrics` is a hard ordering constraint, not a
  convenience.** The `ingest-flow`, `node-health` and `dashboards-health`
  detectors read `cockpit-metrics`. Without documents of `kind=node` and
  `kind=osd` the detectors start against an empty index and never leave
  initialising. The `cockpit_metrics` role must run before this one.
- **Two window delays, not one.** Metric detectors use
  `ad_metrics_window_delay_minutes` (1 minute) because the poller writes on a
  30-second cycle. Log detectors use `ad_log_window_delay_minutes` (2 minutes)
  because a log document travels through Fluent Bit and a bulk queue first. The
  script selects by detector name, from the `METRICS_NAMES` list inside
  `detectors.sh.j2`.
- **`detectors.sh` preserves trained models when nothing changed.** It compares
  the desired definition against the running one field by field. Only a real
  difference triggers a stop, a `PUT` and a restart, which resets the RCF model
  and the initialisation progress. A definition edit therefore costs about 32
  detection intervals of blindness.
- **A changed `category_field` forces a delete and recreate.** OpenSearch treats
  that field as immutable. The script detects the case and recreates the
  detector rather than failing.
- **`forecast_max_primary_shards` must stay pinned at 1.** The forecast result
  index otherwise takes one primary per data node with `auto_expand_replicas`
  `0-2`. That is 15 shards for a few thousand tiny documents, against a
  storage-tier budget near 60.
- **The seed cleanup never fails the deploy.** A leftover
  `alice-bootstrap-seed` document has no `host`, `node` or `severity` field, so
  it forms no entity in any detector. The task carries `failed_when: false` and
  a debug report, on purpose.
- **The detector-start step reports `changed` on every run.** Both upsert scripts
  are `changed_when: true`. They are idempotent against the cluster, but Ansible
  cannot see that from a shell exit code.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `dashboards_bootstrap_detectors_script` | `/opt/alice-ingest/init/detectors.sh` | Where `detectors.sh.j2` is rendered. A literal — see couplings. |
| `dashboards_bootstrap_forecasters_script` | `/opt/alice-ingest/init/forecasters.sh` | Where `forecasters.sh.j2` is rendered. A literal — see couplings. |
| `dashboards_bootstrap_detectors_dir` | `/opt/alice-ingest/init/detectors` | Staged detector definitions. `detectors.sh` reads every `*.json` here. |
| `dashboards_bootstrap_forecasters_dir` | `/opt/alice-ingest/init/forecasters` | Staged forecaster definitions. Same pattern. |
| `ad_metrics_window_delay_minutes` | `1` | `window_delay` for the three metric detectors. |
| `ad_log_window_delay_minutes` | `2` | `window_delay` for the fourteen log detectors. |
| `forecast_interval_minutes` | `60` | `forecast_interval` of the disk-fill forecaster. |
| `forecast_window_delay_minutes` | `1` | Its `window_delay`. |
| `forecast_horizon` | `24` | Points predicted ahead. At an hourly interval, one day. |
| `forecast_history` | `168` | Points of history used. At an hourly interval, seven days. |
| `forecast_max_primary_shards` | `1` | `plugins.forecast.max_primary_shards`, set cluster-wide. See non-obvious settings. |
| `forecast_result_retention` | `14d` | `plugins.forecast.forecast_result_history_retention_period`. |
| `fleet_collector_node_ids` | `[]` | The `node_id` of every collector node. The seed cleanup derives `generic-log-info-<node_id>` from it. The playbook supplies it. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `dashboards_bootstrap_verify_script` | `group_vars/all.yml` | Where `verify_detection.py` is staged. Two other roles run the same path. |
| `dashboards_backtest_script` | `group_vars/all.yml` | Where `backtest.py` is staged. `playbooks/backtest.yml` runs the same path. |
| `dashboards_bootstrap_signal_catalog` | `group_vars/all.yml` | Passed to `verify_detection.py` as `SIGNAL_CATALOG`. Staged by `alice_runtime`. |
| `opensearch_http_port` | `group_vars/all.yml` | The REST port every task in this role calls on `localhost`. |
| `cockpit_metrics_index` | `group_vars/all.yml` | The index the wait step polls and the metric detectors read. |
| `trend_rollup_index` | `group_vars/all.yml` | Passed to `verify_detection.py` as `ROLLUP_INDEX`. |
| `fleet_roster_index` | `group_vars/all.yml` | Passed as `ROSTER_INDEX`. |
| `signals_index` | `group_vars/all.yml` | Passed as `SIGNALS_INDEX`. |
| `incidents_index` | `group_vars/all.yml` | Passed as `INCIDENTS_INDEX`. |
| `notifications_index` | `group_vars/all.yml` | Passed as `NOTIFICATIONS_INDEX`. |
| `lane_state_index` | `group_vars/all.yml` | Passed as `LANE_STATE_INDEX`. |
| `expected_monitors` | `group_vars/all.yml` | Asserted count, 28. The monitors belong to `alerting_monitors`. |
| `expected_detectors` | `group_vars/all.yml` | Asserted count, 17. Must equal the file count in `files/detectors/`. |
| `expected_forecasters` | `group_vars/all.yml` | Asserted count, 1. Must equal the file count in `files/forecasters/`. |
| `alerting_max_actionable_alert_count` | `group_vars/all.yml` | Passed to `verify_detection.py`. |

## Prerequisites

The role does not build the platform under it. Five things must be true first,
all satisfied by the play order in `playbooks/site.yml`.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `/opt/alice-ingest/init` exists, 0755 root:root | `alice_runtime` | Every staging task fails. This role writes into that directory and never creates it. |
| `/opt/alice-ingest` exists, 0755 root:root | `alice_runtime` | Staging `backtest.py` fails. |
| `signal_catalog.json` staged | `alice_runtime` | `verify_detection.py` exits non-zero on a missing catalog. |
| The indices, templates and ISM policy exist | `opensearch_bootstrap` | The detectors have no source indices and `verify_detection.py` fails its ISM check. |
| `cockpit-metrics` holds `kind=node` and `kind=osd` documents | `cockpit_metrics` | The wait step burns 20 attempts and then fails the play. |
| The 28 alerting monitors exist | `alerting_monitors` | `verify_detection.py` fails its `EXPECTED_MONITORS` assertion. |

## How to use it

In a playbook, against the control host:

```yaml
- name: Detection layer (control host only)
  hosts: control
  become: true
  vars:
    fleet_collector_node_ids: >-
      {{ groups['workers'] | map('extract', hostvars, 'node_id') | list }}
  roles:
    - anomaly_detection
```

- **Run it on the control host only.** Every task calls
  `localhost:{{ opensearch_http_port }}`, and detectors are cluster-wide
  objects. A second host would upsert the same 17 detectors again.
- **The role is safe to re-run.** Detectors whose definition has not changed keep
  their trained models and are only restarted if they were stopped.
- **`fleet_collector_node_ids` must be supplied.** With the default empty list the
  seed cleanup only clears `infologger` and `generic-log-other`. Nothing fails,
  but the per-node info seeds stay in place.

## Couplings

Pairs of values that must change together.

- **`expected_detectors` and the file count in `files/detectors/`.** The number
  lives in `group_vars/all.yml`; the files live here. Adding a detector without
  raising the number makes `verify_detection.py` fail in this role. This is the
  one place the split made worse — the count and the directory it counts are
  now in different trees.
- **`expected_forecasters` and `files/forecasters/`.** The same trap, with one
  file instead of seventeen.
- **`expected_monitors` and the `alerting_monitors` role.** This role asserts a
  count of artefacts another role creates. Adding a monitor there fails the gate
  here.
- **The four `dashboards_bootstrap_*` paths are literals, not references to
  `dashboards_bootstrap_root`.** A role default that reads another role's
  variable resolves lazily and makes this role unrunnable alone. They must stay
  equal to `{{ dashboards_bootstrap_root }}/detectors.sh`, `/forecasters.sh`,
  `/detectors` and `/forecasters`. The `alice_ops` role does the same for
  `dashboards_ops_templates_script`.
- **`verify_detection.py` is staged here and run by three roles.** This role runs
  it at the end of `detection.yml`. `cockpit_metrics` runs the installed copy
  again after the collector cutover, with `EXPECT_PUSH_HEARTBEATS` set from the
  live heartbeat switch. `signal_projector` runs it a third time with
  `CHECK_EPISODE_GROUPING=true`. Only this role copies the file; the other two
  read `dashboards_bootstrap_verify_script`. Changing the script's environment
  contract means changing three call sites.
- **`forecast_history` and `cockpit_metrics_retention_days`.** 168 hourly points
  is exactly the 7 days the metrics index keeps. Shortening the retention
  starves the forecaster.
- **`forecast_disk_threshold_percent` is not here.** It belongs to
  `alerting_monitors`, which raises the alert the forecaster's output feeds. The
  forecaster itself has no threshold.
- **`backtest.py` and `detection_status.py` are staged or run from this role's
  `files/`.** `playbooks/backtest.yml` runs the installed
  `dashboards_backtest_script`. `playbooks/status.yml` runs
  `roles/anomaly_detection/files/detection_status.py` straight out of the
  repository, so its path in that playbook must track this directory.

## What is frozen

- The 17 detector definitions and the single forecaster as a set. They are a
  design, not a preference. `verify_detection.py` asserts the counts.
- The `METRICS_NAMES` list inside `detectors.sh.j2`. It is what splits the two
  window delays.
- The `alice-bootstrap-seed` document id. `templates.sh` in
  `opensearch_bootstrap` writes it under that exact literal and this role
  deletes it under the same literal.

## What this role does not do

- **It does not create the alerting monitors.** `alerting_monitors` does.
- **It does not create indices, templates or ISM policies.**
  `opensearch_bootstrap` does.
- **It does not create `/opt/alice-ingest/init`.** `alice_runtime` does, and
  `opensearch_bootstrap` creates the same directory with the same owner, group
  and mode. Neither owns it. This role only writes files into it.
- **It does not run the detection verify after the collector cutover.**
  `cockpit_metrics` does that, from `post_collector.yml`, against the copy this
  role staged.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Checked in August 2026.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| [`opensearch-project/ansible-playbook`](https://github.com/opensearch-project/ansible-playbook) | Vendor | Nothing | It installs and configures OpenSearch nodes. It has no notion of an anomaly detector or a forecaster, and no task that talks to the `_plugins/_anomaly_detection` API. |
| OpenSearch Terraform / OpenSearch CDK providers | Vendor | The upsert scripts | Neither provider models anomaly detectors or forecasters. Adding a second provisioning tool for one resource type would also split the state of this tree in two. |
| Galaxy search for `opensearch anomaly detection` | Third-party | The upsert scripts | No result found. The detectors are this platform's own definitions, matched to its own index and field names, so a generic role would carry no content. |

What upstream cannot hold is all of this role: the 17 definitions, the two
window-delay classes, the model-preserving upsert comparison, and the verify
gate.

## Used by

- `playbooks/site.yml`, play "Control plane — Dashboards, nginx, ops page,
  monitors, roster, metrics and detectors (control host only)", against
  `control` — the only caller.
- `playbooks/backtest.yml` runs the installed `backtest.py` this role staged.
- `playbooks/status.yml` runs `files/detection_status.py` from the repository.
