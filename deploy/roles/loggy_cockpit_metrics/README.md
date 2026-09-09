# Ansible Role: loggy_cockpit_metrics

Publishes the collector roster and runs the poller that fills `cockpit-metrics`,
both on the control host. The roster is one immutable snapshot in
`cockpit-fleet` naming every EPN worker whose collector must heartbeat. The
poller then samples the cluster, every index, every OpenSearch node and
Dashboards every 30 seconds, and marks each rostered collector present or
absent against the health samples the workers push. Every deploy ends with a
gate that waits for every rostered collector's first pushed sample and then
verifies the whole detection layer against the cluster.

Every health panel, detector and absence monitor reads one of these two
indices.

## Play order

| Hosts | Mode | Gate |
|---|---|---|
| `control`, after `loggy_os_dashboards` and `alice_ops` | publish and poll (`tasks/main.yml`) | the newest roster names exactly the rostered collectors; `alice-metrics` is enabled and started |
| `control`, after `loggy_collector` has run on `workers` | the collector gate (`tasks_from: post_collector.yml`) | every rostered collector pushed a sample in the last 5 minutes; `verify_detection.py` exits 0 |

## How it works: publish and poll

```
          CONTROL HOST: os-node-04, one of the three storage containers on
          epn-infra13. Every call goes to the OpenSearch node on localhost and
          to Dashboards on 127.0.0.1.

┌─ STAGE into /opt/loggy ────────────────────────────────────────────────────────┐
│  os_cursor.py          vendored copy; both roster scripts import it            │
│  roster_publish.py     run once per deploy, next box                           │
│  discover_roster.py    run by playbooks/roster_discover.yml; prints            │
│                        roster_assignments rows from the last 7 days of logs    │
│  metrics_poller.py     the alice-metrics service, two boxes down               │
│  any changed file --> restart alice-metrics                                    │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       v
┌─ PUBLISH the roster (roster_publish.py) ───────────────────────────────────────┐
│  topology_version = hash(cluster_id, collectors, assignments)                  │
│  equal to the newest snapshot in cockpit-fleet --> nothing written             │
│  else _create one document {epoch}-{hash}     --> cockpit-fleet               │
│      doc_kind=roster, collectors, assignments, effective_from,                 │
│      supersedes=<the previous version>. Old snapshots are never deleted.       │
│  read the newest snapshot back; assert it names exactly                        │
│  fleet_collector_node_ids                                                      │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       v
┌─ POLL (alice-metrics: metrics_poller.py, DynamicUser, MemoryMax 512M) ─────────┐
│  every 30 s, one _bulk into cockpit-metrics:                                   │
│    /_cluster/health                     --> [cluster]  1 sample                │
│    /_cat/indices + /_stats/indexing     --> [index]    1 per index             │
│    /_nodes/stats + admission_control    --> [node]     1 per OpenSearch node   │
│    Dashboards /api/status               --> [osd]      1 sample; unreachable   │
│                                                        is written as a sample  │
│    newest roster x [fluentbit] samples  --> [fleet]    1 per rostered          │
│      pushed inside heartbeat_grace_seconds             collector, with         │
│                                                        heartbeat_missing 0/1   │
│  [fluentbit] is pushed by every worker's collector on the same beat and is     │
│  not read from here. No roster --> no [fleet] samples, nothing is absent.      │
│  every hour: delete samples older than cockpit_metrics_retention_days          │
│  20 failed pushes in a row --> exit; systemd restarts it after 5 s             │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       v
┌─ PURGE ────────────────────────────────────────────────────────────────────────┐
│  _delete_by_query on cockpit-metrics: [fluentbit] samples whose collector_id   │
│  is not in fleet_collector_node_ids. Every other kind is kept.                 │
│  a missing index is accepted; the poller keeps writing while it runs          │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Both indices are replicated.** `cockpit-metrics` and `cockpit-fleet` have one
primary and two replicas pinned to the storage tier; their templates come from
`loggy_opensearch`.

**The roster is content-addressed and append-only.** An unchanged fleet writes
nothing, so the task reports `changed` only when a snapshot is appended. The
poller and the absence monitors read only the newest snapshot.

**The poller never scrapes a worker.** Collector health arrives as
`kind: fluentbit` samples that every worker pushes on the same 30-second beat.
The poller only compares them against the roster.

**Staging runs the same layout on fewer machines**: three workers and the
three storage containers on one VM.

## How it works: the collector gate

```
          CONTROL HOST, after the collector play has run on every worker

┌─ WAIT for every rostered collector ────────────────────────────────────────────┐
│  cockpit-metrics/_count   kind=fluentbit, collector_id=<it>, last 5 minutes    │
│  per collector in fleet_collector_node_ids: 20 attempts, 6 s apart             │
│  skipped when collector_health_push_enabled is false                           │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       v
┌─ VERIFY (verify_detection.py, staged by loggy_anomaly_detection) ──────────────┐
│  30 monitors, 17 detectors and 1 forecaster present, running and wired         │
│  cockpit-fleet holds a roster; cockpit-metrics carries the live mappings       │
│  a [fleet] sample exists for the roster, and pushed [fluentbit] samples        │
│  exist when EXPECT_PUSH_HEARTBEATS is true                                     │
│  exit 1 fails the deploy                                                       │
└────────────────────────────────────────────────────────────────────────────────┘
```

**The gate is a separate entry point.** `tasks/main.yml` does not import
`post_collector.yml`; the playbook includes it after the collector play,
because it can only pass once Fluent Bit is shipping on every worker.

## Why not an upstream role

Every task here writes into this platform's own index names, and the 30
monitors and 17 detectors query exactly those documents. No upstream role
knows the roster or writes `kind: fleet`.

| Candidate | Why not |
|---|---|
| [prometheus-community/ansible](https://github.com/prometheus-community/ansible) with the [Aiven exporter plugin](https://github.com/Aiven-Open/prometheus-exporter-plugin-for-opensearch) | Node and cluster stats go to Prometheus, a second store the monitors and detectors do not read. |
| [elastic/ansible-beats](https://github.com/elastic/ansible-beats) (Metricbeat) | Targets Elasticsearch and Kibana and writes ECS documents into `metricbeat-*`; OpenSearch is reachable only through an old Apache-licensed build. |
| [opensearch-project/performance-analyzer](https://github.com/opensearch-project/performance-analyzer) | A plugin with its own REST endpoint and store, not an index. Dashboards, the collector heartbeats and the roster are outside it. |

## Requirements

`loggy_opensearch`
must have configured the cluster, because the `cockpit-metrics` and
`cockpit-fleet` index templates come from it and the roster is published into
the running cluster on localhost. The Dashboards samples need the `loggy_os_dashboards`
role's instance on `dashboards_internal_port`. The gate additionally needs
`loggy_collector` on every worker and `loggy_anomaly_detection` on this host,
which stages the `verify_detection.py` it runs.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
fleet_collector_node_ids: []
roster_assignments: []
```

`fleet_collector_node_ids` is the `node_id` of every worker whose collector
must heartbeat; `group_vars` resolves it from the `workers` group, and an empty
list fails the publish. `roster_assignments` are explicit `origin_host` to
`collector_id` rows, which `playbooks/roster_discover.yml` prints from the
last 7 days of logs.

```yaml
cockpit_metrics_retention_days: 7
heartbeat_grace_seconds: 90
```

The poller deletes its own samples older than the retention once an hour. A
rostered collector with no pushed sample inside the grace is written as absent,
so at 90 seconds against the 30-second push interval a collector may miss two
pushes before it is called absent.

```yaml
collector_health_push_enabled: true
```

Whether the collectors push their own health samples. Off, the gate skips the
heartbeat wait and tells the verify not to expect heartbeats; it stops nothing
pushing.

From `group_vars`: `opensearch_http_port`, `dashboards_internal_port`,
`cluster_id`, `cockpit_metrics_index`, `fleet_roster_index`,
`cockpit_metrics_interval_seconds`, `cockpit_metrics_service_name`,
`cockpit_metrics_discover_roster_script`, `health_metrics_emit_legacy_node`,
`alice_service_memory_high`, `alice_service_memory_max`,
`alice_bootstrap_verify_script`, `alice_bootstrap_signal_catalog`,
`expected_monitors`, `expected_detectors`, `expected_forecasters`,
`alerting_max_actionable_alert_count`, and the index names the verify reads:
`trend_rollup_index`, `signals_index`, `incidents_index`,
`notifications_index`, `lane_state_index`.

- `restart alice-metrics` is defined here. The handler checks that the unit
  exists, so a notification before the unit is installed is a no-op.
- `os_cursor.py` is a vendored copy of the signal projector role's file. The
  contract test fails when the two differ. Change the projector's copy, then
  re-copy it here.
- `heartbeat_grace_seconds` and `cockpit_metrics_interval_seconds` change
  together. Raising the interval without the grace turns normal jitter into an
  absence.
- A worker that runs `loggy_collector` but is missing from
  `fleet_collector_node_ids` has its samples purged on every deploy and is
  never reported absent.

## Example Playbook

```yaml
- hosts: control
  become: true
  roles:
    - loggy_cockpit_metrics

- hosts: workers
  become: true
  roles:
    - loggy_collector

- hosts: control
  become: true
  tasks:
    - ansible.builtin.include_role:
        name: loggy_cockpit_metrics
        tasks_from: post_collector.yml
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
