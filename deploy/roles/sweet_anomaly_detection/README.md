# Ansible Role: sweet_anomaly_detection

Loads the detection layer into the running `alice-logs` cluster from the
control host: 30 alerting monitors, 17 Random Cut Forest anomaly detectors and
one disk-fill forecaster. It stages the definitions and their upsert scripts,
pins the plugin cluster settings, creates the two notification channels,
upserts the monitors, waits for the metrics poller's first documents, upserts
and starts the detectors and the forecaster, and ends with
`verify_detection.py`, which fails the deploy unless every object is present,
running and wired to its channel.

Every alert the signal projector turns into an incident starts here. Nothing
downstream fires on its own.

## How it works

```
                 CONTROL HOST: os-node-04, one of the three storage
                 containers. Every call goes to localhost:9200.

┌─ STAGE into /opt/sweet/init ──────────────────────────────────────────────┐
│  monitors.sh  detectors.sh  forecasters.sh       the three upsert scripts │
│  monitors/ 30   detectors/ 17   forecasters/ 1   the JSON definitions     │
│  verify_detection.py                             the gate at the end      │
│  backtest.py  --> /opt/sweet                     for playbooks/backtest   │
└───────────────────────────────────────────────────────────────────────────┘
                                     │
                                     v
┌─ MONITORS (monitors.sh) ──────────────────────────────────────────────────┐
│  pin      plugins.alerting.max_actionable_alert_count                     │
│  gate     alice-alert-actions must be a rollover write alias, else FATAL  │
│  channel  alice-incluster-alert-sink  --> alice-alert-actions, one        │
│               document per fire; the 30 min throttle needs a destination  │
│  channel  alice-breakglass-sink       --> the notification receiver on    │
│               127.0.0.1, for [signal-projector-stale] [alertmanager-down] │
│  upsert by name, thresholds rewritten from the variables on every run:    │
│    cockpit-metrics               14   every 1 min   collector, node and   │
│                                                     cluster health        │
│    trend-rollup                  12   every 10 min  per-entity volume,    │
│                                                     errors and lag        │
│    template-catalog               2   every 60 min  [template-count-check]│
│                                                     [template-new]        │
│    .opendistro-anomaly-results*   1   every 1 min   [ad-high-grade]       │
│    opensearch-forecast-results*   1   every 10 min  [disk-fill-forecast]  │
└───────────────────────────────────────────────────────────────────────────┘
                                     │
                                     v
┌─ WAIT for the metrics poller ─────────────────────────────────────────────┐
│  cockpit-metrics/_count   kind=node and kind=osd   20 attempts, 6 s apart │
└───────────────────────────────────────────────────────────────────────────┘
                                     │
                                     v
┌─ DETECTORS (detectors.sh) ────────────────────────────────────────────────┐
│  pin   plugins.anomaly_detection.max_multi_entity_anomaly_detectors = 50  │
│  every 1 min, each with a -slow twin every 30 min:   entity   window delay│
│    infologger              [il-per-epn]              origin_host   2 min  │
│                            [il-per-epn-entry-lag]    origin_host          │
│                            [il-collector-shipping-lag]   node             │
│    application-logs-local-*  [local-volume]          origin_host   2 min  │
│                            [local-per-epn-entry-lag] origin_host          │
│                            [local-collector-shipping-lag]  node           │
│    application-logs-central  [central-per-epn]       origin_host   2 min  │
│  every 1 min, no twin:                                                    │
│    cockpit-metrics  [ingest-flow]       kind=fluentbit  collector_id 1 min│
│                     [node-health]        kind=node       os_node          │
│                     [dashboards-health]  kind=osd        fleet-wide       │
│  upsert by name:  absent               --> create, start                  │
│                   same and running     --> leave it; the model survives   │
│                   same and stopped     --> start                          │
│                   changed              --> stop, PUT, start; trains again │
│                   category_field changed --> delete, create               │
└───────────────────────────────────────────────────────────────────────────┘
                                     │
                                     v
┌─ FORECASTER (forecasters.sh) ─────────────────────────────────────────────┐
│  pin   plugins.forecast.max_primary_shards                                │
│        plugins.forecast.forecast_result_history_retention_period          │
│  [disk-fill]  cockpit-metrics kind=node, per os_node, disk_used_percent   │
│               every 60 min, 168 points of history, 24 points ahead        │
│  same upsert outcomes as the detectors                                    │
└───────────────────────────────────────────────────────────────────────────┘
                                     │
                                     v
┌─ CLEAN UP, then VERIFY ───────────────────────────────────────────────────┐
│  DELETE alice-bootstrap-seed from infologger, application-logs-central    │
│  and application-logs-local-<node_id> on every worker; never fails        │
│  verify_detection.py   30 monitors, 17 detectors, 1 forecaster, their     │
│                        channels and throttle, the indices, the ISM policy │
│                        and the signal catalog; any miss fails the deploy  │
└───────────────────────────────────────────────────────────────────────────┘
```

- **Two window delays.** A metrics detector waits one minute because the
  poller writes every 30 seconds. A log detector waits two, because a record
  travels through Fluent Bit, the stamper and a bulk queue first.
- **An unchanged detector keeps its trained model.** The script compares the
  desired definition with the running one field by field, and only a real
  difference stops, rewrites and restarts it.
- **The thresholds live in the variables, not in the JSON.** `monitors.sh`
  rewrites the literals inside the trigger scripts from the environment on
  every run; the JSON values are only the fallback for a hand run.
- **`alice-incluster-alert-sink` notifies nobody.** It writes one document per
  fire so the per-alert throttle has a destination. People are told through
  the signal projector and Alertmanager.

## Why not an upstream role

The vendor publishes an Ansible playbook that installs OpenSearch nodes and
nothing that creates a monitor, a detector or a forecaster. Candidates checked
and rejected:

| Candidate | Why rejected |
|---|---|
| [opensearch-project/ansible-playbook](https://github.com/opensearch-project/ansible-playbook) | Installs and configures nodes. No task talks to the Alerting or Anomaly Detection API. |
| [opensearch-project/terraform-provider-opensearch](https://github.com/opensearch-project/terraform-provider-opensearch) | A second provisioning tool with its own state, for objects this role upserts with three shell loops. |
| Ansible Galaxy, `opensearch alerting` and `opensearch anomaly detection` | Nothing found. The definitions are this platform's own, matched to its index and field names, so a generic role would carry no content. |

## Requirements

`sweet_opensearch` must have run on the control host in its configure-the-
cluster mode first. It creates the indices the detectors read, the ISM policies
the gate asserts and the `alice-alert-actions` write alias the monitors need.
`alice_runtime` must have created `/opt/sweet` and `/opt/sweet/init` and staged
`signal_catalog.json`, `os_cursor.py` and `signal_identity.py` there;
`backtest.py` imports the two modules. `cockpit_metrics` must be running its
poller, or the wait step fails the play after two minutes.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths.

```yaml
ad_anomaly_grade_threshold: 0.7
ad_anomaly_confidence_threshold: 0.7
forecast_disk_threshold_percent: 85
fleet_silence_fraction: 0.5
```

`ad-high-grade` fires above both thresholds at once. `disk-fill-forecast`
fires when a node's predicted fill crosses the percentage, and
`fleet-fb-silence` when that fraction of the roster stops heartbeating.

```yaml
trend_lag_floor_ms: 250
trend_entry_lag_ceiling_ms: 3600000
trend_entity_cap_warn: 1800
trend_min_slice_docs: 50
trend_min_slice_errors: 10
trend_min_lag_docs: 100
```

The guards inside the twelve `trend-*` monitors. Lag under the floor is noise,
entry lag over the ceiling is archive age, and a rollup slice under the minimum
counts is too small to judge; `trend_entity_cap_warn` must stay below
`trend_rollup_max_entities` so the warning comes before the rollup truncates.

```yaml
ad_metrics_window_delay_minutes: 1
ad_log_window_delay_minutes: 2
```

`window_delay` for the three metrics detectors and for the fourteen log
detectors. The script picks by detector name.

```yaml
forecast_interval_minutes: 60
forecast_horizon: 24
forecast_history: 168
forecast_max_primary_shards: 1
forecast_result_retention: "14d"
```

Hourly points, one day ahead, from seven days of history: 168 points is what
`cockpit_metrics_retention_days` keeps, so shortening that retention starves
the forecaster. `forecast_max_primary_shards` stays at 1, or the result index
takes one primary per data node for a few thousand tiny documents.

From `group_vars`: `expected_monitors`, `expected_detectors` and
`expected_forecasters`, which must equal the file counts under `files/`;
`opensearch_http_port`, `notification_ingest_port`,
`alerting_max_actionable_alert_count`, `cockpit_metrics_index`,
`trend_rollup_index`, `fleet_roster_index`, `signals_index`,
`incidents_index`, `notifications_index`, `lane_state_index`,
`alice_bootstrap_verify_script`, `alice_bootstrap_signal_catalog`,
`anomaly_detection_backtest_script` and `fleet_collector_node_ids`.

## The gate and the two probes

| Script | Runs from | What it does |
|---|---|---|
| `verify_detection.py` | this role, then `cockpit_metrics` after the collectors are up, then `signal_projector` | Asserts the whole detection layer against the cluster. Exit 1 fails the deploy. |
| `backtest.py` | `playbooks/backtest.yml` | Historical analysis of every log detector over the replayed window, with a grade floor and a 45 minute timeout. |
| `detection_status.py` | `playbooks/status.yml`, out of `files/` | Read-only: data windows, job states and result counts per detector. |

The verify runs three times in a deploy. Only this role stages the file; the
other two roles run the installed copy through `alice_bootstrap_verify_script`,
so a change to its environment contract is a change to three call sites.

`gen_monitors.py` is a build-time tool and is never copied to a machine. It
owns all 30 monitor files: to change a monitor's shape, edit the generator,
run it, and commit the regenerated JSON.

## Example Playbook

```yaml
- hosts: control
  become: true
  roles:
    - sweet_anomaly_detection
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
