# Ansible Role: `sweet_signal_projector`

The signal layer of the ALICE EPN log platform. It installs `alice-signal-projector`
on the projector host and `alice-notification-ingest` on the control host. The
projector reads the alerts the monitors raise, the results the anomaly detectors
write and the roster of machines that should be reporting, and turns them into
named signals, incidents that survive across cycles, and lane state. It ranks the
likely causes of each incident from the declared causal edges, collapses a
fleet-wide silence into one incident instead of hundreds, and pushes every open
episode to Alertmanager on every cycle. The receiver takes Alertmanager's webhooks
and the break-glass path back and writes them durably into the notifications index.

This is the only component that decides what an alert *means*. The cockpit, the
shifter view and every notification a person ever sees read what it writes, not the
raw alerting and anomaly indices.

## Play order

The role has three entry points and runs in three plays, on two hosts. A play uses
exactly one of them.

| Play | Hosts | Entry point | Gate it must pass |
|---|---|---|---|
| 1 | `control` | `receiver.yml` | none; handlers are flushed so the receiver is running before the play ends |
| 2 | `projector` | `tasks/main.yml` (the default) | Alertmanager answers `/-/ready`; the running unit has finite memory bounds; one cycle writes a `projector_cycle_ok` heartbeat |
| 3 | `control` | `control.yml` | the receiver is still active and answers `/healthz`; the signal contract holds; the signal layer re-verifies |

The order is not free. Alertmanager posts to the receiver while the projector play
runs, and a refused webhook is never re-sent, so play 1 must come first. Play 3
reads a fact the projector host recorded in play 2 and asserts rows the projector
has already written, so it must come last.

On the farm, `projector` is `os-node-05` and `control` is `os-node-04`. Both are
Ansible hosts on the same infrastructure machine, `epn-infra13`, each addressing
its own OpenSearch storage container — the projector reaches port 9202, the
control host 9201. The split gives the projector its own memory budget and its own
cluster endpoint, not its own machine. Staging runs the same three plays with the
projector on a VM of its own.

## How it works

### On the projector host

```
                    PROJECTOR HOST — hosts: projector

┌─ 1. INSTALL ──────────────────────────────────────────────────────────────┐
│  signal_projector.py                /opt/sweet   0755 root:root           │
│  alice-signal-projector.service     0644 root:root                        │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 2. GATE BEFORE START ────────────────────────────────────────────────────┐
│  GET alertmanager_host_address:alertmanager_port/-/ready                  │
│      12 attempts, 5 s apart. A projector that cannot reach Alertmanager   │
│      resolves nothing, so it is not started at all.                       │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 3. START, restarted only on change ──────────────────────────────────────┐
│  restarted when the script, the unit, the shared modules, the signal      │
│  catalog or the causal edges changed; started otherwise                   │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 4. PROVE ────────────────────────────────────────────────────────────────┐
│  systemctl show      MemoryHigh and MemoryMax must both be finite         │
│  date -u             the earliest heartbeat this gate will accept         │
│  cluster health      this host's OpenSearch answers, 6 attempts           │
│  search              cockpit-metrics for kind=projector with              │
│                      projector_cycle_ok=1, 36 attempts 5 s apart          │
│  rescue              12 diagnostic commands, then projector_gate_failed   │
│  systemd             the unit is still active                             │
└───────────────────────────────────────────────────────────────────────────┘
```

- **The memory assertion reads the running unit, not the template.** A unit file
  with `MemoryMax=768M` proves nothing if systemd never reloaded, so the task asks
  systemd what it is actually enforcing.
- **The cycle gate reports and continues.** The rescue prints its diagnosis and
  sets `projector_gate_failed`; a final play turns that fact into a non-zero exit.
  The deploy converges first, so one unproven gate leaves nothing half-configured.
- **The role holds no `delegate_to`.** Each play targets the host its tasks belong
  on. The rescue's `set_fact` therefore lands on the projector host, and the play
  that reads it must reach into `hostvars`.
- **`signal_projector.py` is installed on both hosts.** The projector-host copy is
  the service; the control-host copy is never run as a service, and exists only so
  `test_signal_contract.py` can import it as a module.

### Each cycle, on the projector host

```
                        ONE CYCLE — every 30 s, forever

┌─ READ ────────────────────────────────────────────────────────────────────┐
│  .opendistro-alerting-alerts           the monitors firing right now      │
│  .opendistro-alerting-alert-history-*  from the stored watermark on       │
│  .opendistro-anomaly-results*          paged, grade >= anomaly_grade_floor│
│  alice-lane-state                      open episodes and the 2 watermarks │
│  cockpit-fleet                         the roster, to derive absence      │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ NAME ────────────────────────────────────────────────────────────────────┐
│  every row takes an incident_id, a group_id and a name from               │
│  signal_catalog.json                                                      │
│  monitor rows split into monitor-error, entity-missing or single          │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ EPISODE ─────────────────────────────────────────────────────────────────┐
│  a run of rows on one key becomes one episode that outlives the cycle     │
│  OPEN --> RECOVERING --> RESOLVED, or STALE when evaluations stop         │
│  a missing evaluation is never a recovery, so a stale episode stays open  │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ EXPLAIN ─────────────────────────────────────────────────────────────────┐
│  causal_edges.json --> noisy-OR --> up to 5 ranked candidate causes       │
│  more of the roster silent than signal_projector_mass_silence_fraction    │
│      --> one mass-silence incident instead of one per machine             │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ WRITE ───────────────────────────────────────────────────────────────────┐
│  alice-signals     every row, firing and cleared                          │
│  alice-incidents   one document per episode, with its candidate causes    │
│  alice-lane-state  the two watermarks, advanced only after a complete     │
│                    traversal                                              │
│  cockpit-metrics   kind=projector, kind=alertmanager, kind=projector_-    │
│                    detector; the deploy gate reads the first of the three │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ PUSH ────────────────────────────────────────────────────────────────────┐
│  every open episode --> Alertmanager, on every cycle                      │
│  re-sent well inside resolve_timeout, so nothing resolves between cycles  │
│  once an hour, after a good cycle: delete rows past the retention age     │
└───────────────────────────────────────────────────────────────────────────┘
```

- **A failed cycle holds the watermarks.** The exception is logged, a heartbeat
  with `projector_cycle_ok=0` is still written, and the next cycle re-reads the
  same window rather than skipping it.
- **`cockpit-metrics` is the health surface, not the log.** Nothing else reports
  whether the projector is doing its job; the deploy gate and the cockpit both
  read the same `kind=projector` document.
- **Retention is maintenance, never a prerequisite.** Pruning runs at most once an
  hour and only after a cycle succeeded, so it cannot delay the Alertmanager
  re-send.

### On the control host

```
              CONTROL HOST — one play before, one play after

┌─ receiver.yml, before the projector play ─────────────────────────────────┐
│  test_signal_contract.py     /opt/sweet   0755 root:root                  │
│  notification_ingest.py      0755 root:root                --> restart    │
│  alice-notification-ingest.service   0644 root:root        --> restart    │
│  signal_projector.py         0755, for the contract test to import        │
│  flush_handlers, so the receiver that answers is the one just installed   │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v      ( the projector play runs here )
┌─ control.yml, after the projector play ───────────────────────────────────┐
│  systemd                  the receiver is still active                    │
│  GET 127.0.0.1:8091/healthz                                               │
│  test_signal_contract.py  the emitted-signal contract, with no live       │
│                           signal and no running cluster                   │
│  verify_detection.py      the whole signal layer, now that the projector  │
│                           exists, with CHECK_EPISODE_GROUPING=true and    │
│                           GROUPING_SINCE = the projector's restart moment │
└───────────────────────────────────────────────────────────────────────────┘
```

- **The receiver binds `127.0.0.1`.** Alertmanager runs on the same host and posts
  to loopback. Moving the receiver elsewhere drops every notification silently,
  because Alertmanager does not fail a webhook that nothing answers.
- **The receiver has two clients, not one.** Alertmanager's webhook config and the
  break-glass sink the alerting monitors are given both point at the same port.

## Why not an upstream role

There is no upstream role, and none was rejected. The projector and the
notification receiver are this repository's own programs, and no Ansible Galaxy
content exists for software that exists only here. What the role does around them
— copy a script, template a unit, probe a port, start a service and wait for a
document — is builtin modules only.

## Requirements

The role stages its own inputs. It creates `/opt/sweet` and `/opt/sweet/init`,
copies `os_cursor.py` and `signal_identity.py` beside the projector, and writes
`signal_catalog.json` and `causal_edges.json` at mode 0644 — the projector runs
`DynamicUser=true` and cannot read them at 0640. It then proves an unprivileged
process can parse the catalog and that every causal edge is complete, before the
projector starts. `causal_edges.json`, `os_cursor.py` and `signal_identity.py`
are owned here; `signal_catalog.json` is owned by `sweet_anomaly_detection`.
`alice_ops`, `sweet_alertmanager`, `sweet_anomaly_detection` and
`sweet_cockpit_metrics` carry copies, and the contract test fails when any copy
differs from its source.

`sweet_opensearch` must have created the signals, incidents, notifications and
lane-state indices; a first cycle against a missing index creates it with dynamic
mappings the cockpit queries cannot use. `sweet_anomaly_detection` must have
loaded the monitors, detectors and forecasters the projector normalizes, and
staged `verify_detection.py` on the control host. `sweet_alertmanager` must be running
and reachable from the projector host, or the readiness gate fails the play.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
signal_projector_memory_high: "384M"
signal_projector_memory_max: "768M"
```

Higher than the bounds the other small services share, because the projector holds
the open episode set while it traverses the anomaly results. The deploy asserts
systemd is enforcing both.

```yaml
signal_projector_interval_seconds: 30
signal_projector_resolve_timeout_seconds: 300
signal_projector_overlap_minutes: 15
signal_projector_initial_lookback_minutes: 120
signal_projector_os_unreachable_cycles: 2
```

The service refuses to start when twice the interval reaches the resolve timeout,
because Alertmanager would resolve a live alert between two re-sends. The overlap
is how far back each cycle re-reads so a late document is not missed, and the
first cycle after a cold start reads the initial lookback instead.

```yaml
signal_projector_page: 500
signal_projector_bulk_documents: 500
signal_projector_pit_keep_alive: "10m"
signal_projector_retention_days: 30
signal_projector_mass_silence_fraction: 0.5
```

The page size and the keep-alive bound one point-in-time traversal of the anomaly
results. Retention deletes signals and notifications past the age, and incidents
only once they have reached a terminal state.

```yaml
projector_gate_retries: 36
projector_gate_delay: 5
alertmanager_host_address: ""
projector_gate_started_utc: ""
```

The two gate numbers give three minutes, which is what the first cycle after a
restart needs to read the whole overlap window. The two empty defaults are
declared so the role parses on its own and are useless at that value: the playbook
supplies the Alertmanager address on the projector play, and carries the
projector's restart moment across to the control play, where the register that
holds it does not exist.

From `group_vars/all.yml`: `signal_projector_service_name`,
`signal_projector_notification_ingest_service_name`, `opensearch_http_port`,
`alertmanager_port`, `notification_ingest_port`, `notification_ingest_token`,
`alice_service_memory_high`, `alice_service_memory_max`, `cluster_id`,
`signals_index`, `incidents_index`, `notifications_index`, `lane_state_index`,
`cockpit_metrics_index`, `fleet_roster_index`, `trend_rollup_index`,
`anomaly_grade_floor`, `alice_app_root`, `alice_bootstrap_root`,
`alice_os_cursor_script`, `alice_signal_identity_script`,
`alice_bootstrap_signal_catalog`,
`alice_bootstrap_causal_edges`, `alice_bootstrap_verify_script`,
`expected_monitors`, `expected_detectors`, `expected_forecasters`,
`alerting_max_actionable_alert_count` and `projector_gate_failed`.

## The notification receiver

One threaded HTTP server on the control host, bound to loopback, writing straight
into the notifications index. Every write is a bulk request with a deterministic
document identifier, so a retried post is not stored twice, and the caller gets
503 when the write did not land.

| Request | Body | What is stored |
|---|---|---|
| `POST /notifications` | carries `alerts` | one Alertmanager notification |
| `POST /notifications` | anything else | one break-glass record |
| `POST /events` | any | one event record |
| `GET /healthz` | — | nothing; the deploy gate reads it |
| `GET /metrics` | — | nothing; accepted, rejected, failed and document counts |

`notification_ingest_token` is the shared secret. An empty token, which is the
default, means the receiver authenticates nobody — acceptable only because it
binds loopback.

## Example Playbook

```yaml
- name: Notification receiver, before the projector
  hosts: control
  become: true
  tasks:
    - name: Install and start the notification receiver
      ansible.builtin.include_role:
        name: sweet_signal_projector
        tasks_from: receiver.yml

- name: Signal projector
  hosts: projector
  become: true
  vars:
    alertmanager_host_address: "{{ hostvars[groups['control'][0]].ansible_host }}"
  roles:
    - sweet_signal_projector

- name: The signal-layer proofs, after the projector
  hosts: control
  become: true
  vars:
    projector_gate_started_utc: >-
      {{ hostvars[groups['projector'][0]]._projector_gate_started.stdout
         | default('now-1h', true) }}
  tasks:
    - name: Assert the receiver stayed up and prove the signal contract
      ansible.builtin.include_role:
        name: sweet_signal_projector
        tasks_from: control.yml
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
