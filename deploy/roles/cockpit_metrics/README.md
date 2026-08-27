# `cockpit_metrics`

Publishes the fleet roster and runs the poller that fills the `cockpit-metrics`
index. The roster names every Fluent Bit collector the cluster expects. The
poller writes one health sample per node, per OpenSearch node, per Dashboards
instance and per collector, on a fixed interval.

Everything downstream of the cockpit health panels reads one of these two
outputs. The anomaly detectors train on `cockpit-metrics`. The absence monitors
compare live samples against the roster to decide that a collector went quiet.
Neither can start before this role has run.

It is a separate role because it produces data, not a service surface. The
`dashboards` role installs a web application; this role installs the two
control-host jobs that feed it, and it is the only place where the collector
roster is decided.

## What it does

```
                              CONTROL HOST

┌─ 1. STAGE — beside os_cursor.py, which both scripts import ────────────────┐
│  roster_publish.py      /opt/alice-ingest/    0755 root  --> alice-metrics │
│  discover_roster.py     /opt/alice-ingest/    0755 root  --> alice-metrics │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. ROSTER — content-addressed, append-only ───────────────────────────────┐
│  roster_publish.py      writes one snapshot into cockpit-fleet             │
│  read back              the latest snapshot by effective_from              │
│  assert                 exactly one effective roster, naming every         │
│                         collector in fleet_collector_node_ids              │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. POLLER — the cockpit health source ────────────────────────────────────┐
│  metrics_poller.py      /opt/alice-ingest/    0755 root  --> alice-metrics │
│  alice-metrics.service  DynamicUser, memory-capped        --> alice-metrics │
│  systemd enable + start                                                    │
│  _delete_by_query       drops kind=fluentbit samples for collectors that   │
│                         the roster no longer names                         │
└────────────────────────────────────────────────────────────────────────────┘

┌─ post_collector.yml — a second entry point, run after the collector play ──┐
│  wait                   every rostered collector pushed a heartbeat in     │
│                         the last 5 minutes (20 attempts, 6 s apart)        │
│  verify_detection.py    the whole detection layer, once the real collector │
│                         traffic is flowing                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

`tasks/main.yml` runs steps 1 to 3. `tasks/post_collector.yml` is not imported
by `main.yml`; `site.yml` includes it on its own, later, because it can only
pass once Fluent Bit is shipping.

## Non-obvious settings

- **The purge deletes only `kind: fluentbit` samples.** A collector that leaves
  the roster keeps producing nothing, but its old samples would keep the absence
  monitors quiet about it forever. Node, OpenSearch and Dashboards samples are
  never touched, because those kinds are not roster-scoped.
- **The purge accepts a 404.** On the first deploy `cockpit-metrics` may not
  exist yet. `conflicts=proceed` is set for the same reason the refresh is: the
  poller writes into the index while the delete runs.
- **The roster assertion demands exactly one effective snapshot.** The index is
  append-only, so old snapshots stay. The health monitors select only the latest
  one. Two live snapshots means the monitors page for retired collectors or miss
  live ones, which is why this is an assertion and not a report.
- **`changed_when` on the publish reads the script's own words.** The publisher
  is content-addressed: an unchanged fleet writes nothing and prints nothing, so
  the task reports changed only when the string `published topology_version`
  appears.
- **Both staged scripts import `os_cursor`.** Each inserts its own directory on
  `sys.path`, so they must land in `/opt/alice-ingest` beside `os_cursor.py`.
  The `alice_runtime` role owns that file and that directory.
- **The staging task keeps the name of the five-file loop it was split from.**
  Three roles now carry a task with that name, each staging its own two or one
  files. The name was left byte-identical on purpose, so that a diff against the
  old `dashboards` role shows a split and not a rewrite.
- **`alice-metrics.service` uses `DynamicUser=true`.** The poller reads nothing
  on disk except its own script, so it needs no stable user.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `cockpit_metrics_script` | `/opt/alice-ingest/metrics_poller.py` | Where the poller is installed. The unit's `ExecStart`. |
| `cockpit_metrics_roster_publish_script` | `/opt/alice-ingest/roster_publish.py` | Where the roster publisher is installed. Run once per deploy, not a service. |
| `fleet_collector_node_ids` | `[]` | The `node_id` of every Fluent Bit collector the cluster expects. The playbook supplies it. See couplings. |
| `roster_assignments` | `[]` | Explicit `origin_host` to `collector_id` rows written into the snapshot. Empty means the roster claims no assignment. `playbooks/roster_discover.yml` prints candidate rows to commit here. |
| `cockpit_metrics_retention_days` | `7` | `RETENTION_DAYS` on the unit. The poller prunes its own index. |
| `heartbeat_grace_seconds` | `90` | `HEARTBEAT_GRACE_SECONDS` on the unit. How long a rostered collector may stay silent before the poller records it absent. See couplings. |
| `collector_health_push_enabled` | `true` | Whether collectors push their own health. Gates the heartbeat wait in `post_collector.yml` and sets `EXPECT_PUSH_HEARTBEATS` for the verify. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `opensearch_http_port` | `group_vars/all.yml` | Every REST call this role makes, always against `localhost`. |
| `dashboards_internal_port` | `group_vars/all.yml` | `OSD_URL` on the unit. The poller samples the Dashboards status API. |
| `cockpit_metrics_service_name` | `group_vars/all.yml` | The unit name. `playbooks/status.yml` and the `alice_ops` role read the same name. |
| `cockpit_metrics_unit_path` | this role | Where that unit is written. The handler stats it before restarting, so a notification that arrives before the unit exists is a no-op rather than a failure. |
| `cockpit_metrics_discover_roster_script` | `group_vars/all.yml` | Install path of `discover_roster.py`. `playbooks/roster_discover.yml` runs that path. |
| `alice_bootstrap_verify_script` | `group_vars/all.yml` | The detection verify `post_collector.yml` runs. The file belongs to `anomaly_detection`. See couplings. |
| `cockpit_metrics_index` | `group_vars/all.yml` | The index the poller writes and the purge cleans. |
| `cockpit_metrics_interval_seconds` | `group_vars/all.yml` | `INTERVAL` on the unit. Shared with the `collector` role, which pushes on the same beat. |
| `fleet_roster_index` | `group_vars/all.yml` | The roster index. |
| `cluster_id` | `group_vars/all.yml` | Stamped into every roster snapshot. |
| `health_metrics_emit_legacy_node` | `group_vars/all.yml` | `EMIT_LEGACY_NODE` on the unit. Shared with the `collector` role. |
| `alice_service_memory_high` / `alice_service_memory_max` | `group_vars/all.yml` | `MemoryHigh` and `MemoryMax` on the unit. Shared by all the thin control-host services. |
| `expected_monitors`, `expected_detectors`, `expected_forecasters` | `group_vars/all.yml` | Counts asserted by the verify in `post_collector.yml`. |
| `trend_rollup_index`, `signals_index`, `incidents_index`, `notifications_index`, `lane_state_index` | `group_vars/all.yml` | Index names the same verify checks. |
| `alice_bootstrap_signal_catalog` | `group_vars/all.yml` | The classifier catalog the same verify reads. |
| `alerting_max_actionable_alert_count` | `group_vars/all.yml` | The actionable-alert ceiling the same verify asserts. |

## Prerequisites

The role writes into a running cluster and installs beside files it does not
own. Five things must be true first, all satisfied by the role order in
`playbooks/site.yml`.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| OpenSearch answering on `localhost:9200` | `opensearch` role | Every task here is a REST call. The roster publish fails first. |
| `cockpit-metrics` and `cockpit-fleet` index templates | `opensearch_bootstrap` role | The roster and the samples land with guessed field types, and the absence monitors match nothing. |
| `/opt/alice-ingest` and `os_cursor.py` | `alice_runtime` role | The two staged scripts have nowhere to land, and both fail on `import os_cursor`. |
| Dashboards answering on `dashboards_internal_port` | `dashboards` role | The poller starts, but every Dashboards sample is an error until the port opens. |
| Fluent Bit shipping on every worker | `collector` role | `post_collector.yml` only. The heartbeat wait times out after 2 minutes per collector. |

`post_collector.yml` additionally needs `anomaly_detection`, `alerting_monitors`
and `signal_projector` to have run, because the verify it calls counts their
monitors, detectors and forecasters and reads their indices.

## How to use it

In a playbook, against the control host:

```yaml
- name: Cockpit metrics and the fleet roster
  hosts: control
  become: true
  roles:
    - alice_runtime
    - cockpit_metrics
```

The post-collector gate is a separate include, later in the same playbook:

```yaml
- name: Post-collector detection gate (control host only)
  hosts: control
  become: true
  tasks:
    - name: Verify pushed heartbeats and roster-derived absence after collector cutover
      ansible.builtin.include_role:
        name: cockpit_metrics
        tasks_from: post_collector.yml
```

- **List this role after `alice_runtime` in the play.** See couplings.
- **The role is idempotent.** It restarts `alice-metrics` only when the poller
  script, one of the two staged scripts or the unit file changed. An unchanged
  fleet publishes no new roster snapshot.

## Couplings

- **`restart alice-metrics` is defined here and notified from three roles.**
  This role owns the only copy of the handler. The `alice_ops` role notifies it
  after it stages `score_injection.py`. The `alice_runtime` role stages
  `os_cursor.py` and `signal_identity.py` and notifies through the
  `alice_runtime_stage_notify` variable, which only the control-host play sets
  to this topic, because the poller exists only on that host. A static `roles:`
  list loads every handler in the play before the first task runs, so the notify
  resolves from any position. Dropping the `notify` instead would leave the
  poller running old code.
- **`alice_runtime` must run before this role.** This role copies
  `roster_publish.py` into `/opt/alice-ingest`, which only `alice_runtime`
  creates, and `ansible.builtin.copy` does not create a missing parent
  directory. `roster_publish.py` then imports `os_cursor`, which `alice_runtime`
  stages beside it. The reverse order fails on a fresh node at the copy, and on
  a redeploy at the import.
- **`fleet_collector_node_ids` and the `workers` inventory group change
  together.** It was `groups['workers'] | map('extract', hostvars, 'node_id')`
  inline, in three places in this role. It is now a plain list resolved in
  `group_vars/all.yml`, the same pattern as
  `opensearch_bootstrap_worker_node_ids`, which carries the identical
  expression. A host that runs the `collector` role but is missing from this
  list has its samples purged on every deploy.
- **`heartbeat_grace_seconds` and `cockpit_metrics_interval_seconds` are one
  decision.** The grace must stay comfortably above the push interval. At 90 and
  30 a collector may miss two pushes before it is called absent. Raising the
  interval without raising the grace turns normal jitter into a page.
- **`collector_health_push_enabled` and the collector's own push
  configuration.** The switch is read here and in the `collector` role. Turning
  it off here only skips the wait and relaxes the verify; it does not stop
  anything pushing.
- **`alice_bootstrap_verify_script` names a file `anomaly_detection`
  owns.** That role stages `verify_detection.py`; this role only runs it, and
  `signal_projector` runs it a third time. All three read one variable, so the
  path moves once. `anomaly_detection` must run before `post_collector.yml`.
- **The roster is published before the poller starts.** The poller derives
  collector absence from the roster. Starting it against a stale snapshot makes
  it report a decommissioned collector as live until the next deploy. This is
  why `main.yml` imports `roster.yml` first.
- **`collector_health_push_enabled` is declared in this role's defaults and in
  `group_vars/all.yml`.** Both values are `true`, and the group variable
  outranks the default. The default exists so the role can run outside this
  repository; the site value stays where the `collector` role can see it too.

## Upstream roles rejected

None searched, and none plausible. Every task here writes into this project's
own index names and runs this project's own scripts. There is no upstream role
for a bespoke poller.

## Used by

- `playbooks/site.yml`, play "Control plane — Dashboards, nginx, ops page,
  monitors, roster, metrics and detectors (control host only)", against
  `control`.
- `playbooks/site.yml`, play "Post-collector detection gate (control host
  only)", which includes `post_collector.yml` on its own.
- `playbooks/roster_discover.yml` runs the installed `discover_roster.py`. It
  includes no task from this role; it only needs the file to be on disk.
