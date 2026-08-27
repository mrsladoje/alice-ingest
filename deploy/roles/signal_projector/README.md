# `signal_projector`

Runs the signal projector on the offload host, and the notification receiver on
the control host. The projector reads raw alerts, anomaly results and monitor
output, and turns them into named signals, incidents and lane state. The
receiver accepts Alertmanager webhooks and the break-glass path, and writes them
into the notifications index.

The role installs both service scripts, templates both systemd units, starts
both services, proves the projector completed one full cycle, and proves the
receiver answers a probe.

## Why it is a separate role

The projector was the last part of the `dashboards` role that did not run where
the play ran. It lives on `alice-ingest-4`, alone, because it holds a working
set the control host cannot spare. Inside `dashboards` that meant fourteen
`delegate_to: "{{ signal_projector_host }}"` lines, one per task, and a role
that silently did nothing useful if the delegation target changed.

As its own role on `hosts: projector`, every one of those lines is gone. The
tasks that genuinely belong on the control host are in two more task files,
`tasks/receiver.yml` and `tasks/control.yml`, and the playbook includes each of
them from its own control-host play — one before the projector play, one after.

## What it does

```
                         hosts: control   (tasks/receiver.yml)

┌─ INSTALL ──────────────────────────────────────────────────────────────────┐
│  test_signal_contract.py              0755 root:root                       │
│  notification_ingest.py               0755 root:root  --> restart          │
│  alice-notification-ingest.service    0644 root:root  --> restart          │
│  signal_projector.py                  0755 — the contract test imports it  │
│  flush_handlers          so the receiver is up before the projector starts │
└────────────────────────────────────────────────────────────────────────────┘

                         hosts: projector   (tasks/main.yml)

┌─ 1. INSTALL ───────────────────────────────────────────────────────────────┐
│  signal_projector.py                  0755 root:root                       │
│  alice-signal-projector.service       0644 root:root                       │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. GATE BEFORE START ─────────────────────────────────────────────────────┐
│  GET alertmanager_host_address:9093/-/ready   12 attempts, 5 s apart       │
│  A projector that cannot reach Alertmanager resolves nothing.              │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. START, restart only on change ─────────────────────────────────────────┐
│  restarted when the script, the unit, the shared modules, the catalog or   │
│  the causal edges changed; started otherwise                               │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. PROVE ─────────────────────────────────────────────────────────────────┐
│  systemctl show          MemoryHigh and MemoryMax must be finite           │
│  date -u                 the earliest acceptable heartbeat time            │
│  cockpit-metrics search  kind=projector with projector_cycle_ok=1,         │
│                          36 attempts, 5 s apart — 3 minutes                │
│  rescue                  12 diagnostic commands, then projector_gate_failed│
│  systemctl               the unit is still active                          │
└────────────────────────────────────────────────────────────────────────────┘

                          hosts: control   (tasks/control.yml)

┌─ PROVE ────────────────────────────────────────────────────────────────────┐
│  systemctl               alice-notification-ingest is still active         │
│  GET 127.0.0.1:8091/healthz                                                │
│  test_signal_contract.py the emitted-signal contract, without a live signal│
│  verify_detection.py     the whole signal layer, now that the projector    │
│                          exists, with CHECK_EPISODE_GROUPING=true          │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The notification receiver stays on the control host.** It binds
  `127.0.0.1`, and `alertmanager.yml.j2` posts its webhooks to
  `http://127.0.0.1:{{ notification_ingest_port }}/notifications`. Alertmanager
  runs on the control host. Moving the receiver to the projector host would drop
  every notification, silently, because Alertmanager does not fail a webhook
  that nothing answers. That is why this role has two task files and not one.
- **`signal_projector.py` is installed twice, on two hosts, on purpose.** The
  copy on the projector host is the service. The copy on the control host is
  never executed as a service — `test_signal_contract.py` imports it as a
  module. Deleting the control-host copy breaks `make contract`, not the
  projector.
- **The start task uses `state:` and not a handler.** The projector restarts
  only when one of its five inputs changed. A handler cannot express "restarted
  or started" in one place, and an unconditional restart would reset the
  projector's cursor on every deploy.
- **Three of those five change flags come from another role.**
  `_projector_remote_modules`, `_projector_remote_catalog` and
  `_projector_remote_edges` are registered by `alice_runtime`, which stages
  `os_cursor.py`, `signal_identity.py`, `signal_catalog.json` and
  `causal_edges.json` on this host. See couplings — this is the sharpest edge in
  the role.
- **The cycle gate reports and continues, it does not stop the deploy.** The
  rescue prints twelve diagnostics and sets `projector_gate_failed`. A final
  play in `site.yml` turns that fact into a non-zero exit. The deploy converges
  first, so one unproven gate does not leave the fleet half-configured.
- **`projector_gate_retries` is 36, not 24.** The first cycle after a restart
  reads the whole overlap window, which takes minutes on this host. The comment
  above the wait task says so.
- **The memory assertion reads the running unit, not the template.** A unit file
  with `MemoryMax=768M` proves nothing if systemd never reloaded. The task asks
  systemd what it is actually enforcing, and asserts the number is finite.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `signal_projector_memory_high` | `384M` | `MemoryHigh` on the projector unit. Higher than the shared service bound — this process holds a working set. |
| `signal_projector_memory_max` | `768M` | `MemoryMax` on the projector unit. The hard cap systemd kills at. |
| `signal_projector_interval_seconds` | `30` | Seconds between projector cycles. |
| `signal_projector_resolve_timeout_seconds` | `300` | A signal not seen again within this many seconds is resolved. |
| `signal_projector_overlap_minutes` | `15` | How far back each cycle re-reads, so a late document is not missed. |
| `signal_projector_initial_lookback_minutes` | `120` | How far back the first cycle after a cold start reads. |
| `signal_projector_os_unreachable_cycles` | `2` | Consecutive unreachable-OpenSearch cycles tolerated before the projector treats the cluster as down. |
| `signal_projector_retention_days` | `30` | Age at which the projector deletes its own signal, incident and lane-state rows. |
| `signal_projector_page` | `500` | Search page size. |
| `signal_projector_bulk_documents` | `500` | Documents per bulk write. |
| `signal_projector_pit_keep_alive` | `10m` | Point-in-time keep-alive for a paged read. |
| `signal_projector_mass_silence_fraction` | `0.5` | Fraction of the fleet that must go silent before the projector calls it one mass-silence incident instead of many. |
| `signal_projector_script` | `/opt/alice-ingest/signal_projector.py` | Installed path, on both hosts. |
| `signal_projector_notification_ingest_script` | `/opt/alice-ingest/notification_ingest.py` | Installed path, control host. |
| `signal_projector_contract_test` | `/opt/alice-ingest/test_signal_contract.py` | Installed path, control host. |
| `projector_gate_retries` | `36` | Attempts to find a successful-cycle heartbeat. |
| `projector_gate_delay` | `5` | Seconds between those attempts. The two give 3 minutes. |
| `alertmanager_host_address` | `""` | Address of the host running Alertmanager. **The playbook supplies it.** Replaces the inline `hostvars[groups['control'][0]].ansible_host`. |
| `projector_gate_started_utc` | `""` | The UTC instant the projector restarted, as `GROUPING_SINCE` for the control-host verify run. **The playbook supplies it.** See couplings. |

The two empty defaults are declared so the role parses and can be read on its
own. Both are useless at their default value. The playbook must set them.

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `signal_projector_service_name` | `group_vars/all.yml` | The unit name. `alice_ops` and `playbooks/status.yml` read the same name. |
| `signal_projector_notification_ingest_service_name` | `group_vars/all.yml` | The unit name. `playbooks/status.yml` reads it too. |
| `opensearch_http_port` | `group_vars/all.yml` | `OS_URL` for both services, and the gate's own searches. |
| `alertmanager_port` | `group_vars/all.yml` | The readiness probe and `ALERTMANAGER_URL`. |
| `notification_ingest_port` | `group_vars/all.yml` | The receiver's port. `alertmanager.yml.j2` posts to it. |
| `notification_ingest_token` | `group_vars/all.yml` | Shared secret. Empty means no authentication. |
| `alice_service_memory_high` / `alice_service_memory_max` | `group_vars/all.yml` | Memory bounds on the receiver unit, shared with the other small services. |
| `cluster_id` | `group_vars/all.yml` | Stamped on every emitted signal and notification. |
| `signals_index`, `incidents_index`, `notifications_index`, `lane_state_index` | `group_vars/all.yml` | The four indices the projector writes. `opensearch_bootstrap` creates them. |
| `cockpit_metrics_index` | `group_vars/all.yml` | Where the projector writes its heartbeat, and where the gate looks for it. |
| `fleet_roster_index` | `group_vars/all.yml` | The immutable roster the projector reads to derive absence. |
| `trend_rollup_index` | `group_vars/all.yml` | Read by the verify re-run. |
| `anomaly_grade_floor` | `group_vars/all.yml` | Lowest anomaly grade the projector turns into a signal. |
| `alice_bootstrap_signal_catalog` | `group_vars/all.yml` | The signal catalog. `alice_runtime` stages the file. |
| `alice_bootstrap_causal_edges` | `group_vars/all.yml` | The causal edges the projector ranks candidate causes from. `alice_runtime` stages the file. |
| `alice_bootstrap_verify_script` | `group_vars/all.yml` | The verify re-run. `anomaly_detection` stages the file. |
| `expected_monitors`, `expected_detectors`, `expected_forecasters` | `group_vars/all.yml` | Counts asserted by the verify re-run. |
| `alerting_max_actionable_alert_count` | `group_vars/all.yml` | Ceiling asserted by the verify re-run. |
| `projector_gate_failed` | `group_vars/all.yml` | Set to `true` by the rescue. `site.yml` reads it in its final play. Declared site-wide so that final play has a value even when this role never ran. |

## Prerequisites

The role does not bootstrap either machine. Six things must be true first.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `/opt/alice-ingest` and `/opt/alice-ingest/init` exist on the projector host | `alice_runtime` | Every copy task fails — the destination directory is missing. |
| `os_cursor.py` and `signal_identity.py` on the projector host | `alice_runtime` | The service starts and dies on import. Nothing in this role would notice until the cycle gate times out three minutes later. |
| `signal_catalog.json` and `causal_edges.json` on the projector host, mode 0644 | `alice_runtime` | The projector runs `DynamicUser=true`. At 0640 or 0600 it cannot read either file. |
| The projector's four indices exist | `opensearch_bootstrap` | The first cycle creates them with dynamic mappings, which is not the mapping the cockpit queries expect. |
| Monitors, detectors and forecasters exist | `alerting_monitors`, `anomaly_detection` | The projector has nothing to normalize, and the verify re-run fails its count assertions. |
| Alertmanager is up and reachable from the projector host | `alertmanager`, plus its firewall rule | The readiness gate retries twelve times and then fails the play. |
| `verify_detection.py` staged on the control host | `anomaly_detection` | The final control-host task fails — no such file. |

## How to use it

Three plays. The order matters: the first control-host play starts the receiver
the projector needs, and the last one asserts things the projector-host play
created.

```yaml
- name: Notification receiver (control host), before the projector
  hosts: control
  become: true
  tasks:
    - name: Install and start the notification receiver
      ansible.builtin.include_role:
        name: signal_projector
        tasks_from: receiver.yml

- name: Signal projector — on its own offload host
  hosts: projector
  become: true
  vars:
    alertmanager_host_address: "{{ hostvars[groups['control'][0]].ansible_host }}"
  roles:
    - alice_runtime
    - signal_projector

- name: The signal-layer proofs (control host)
  hosts: control
  become: true
  tasks:
    - name: Assert the receiver stayed up and prove the signal contract
      ansible.builtin.include_role:
        name: signal_projector
        tasks_from: control.yml
      vars:
        projector_gate_started_utc: >-
          {{ hostvars[groups['projector'][0]]._projector_gate_started.stdout
             | default('now-1h', true) }}
```

- **`alice_runtime` must be in the same play, before this role.** Not only for
  the files — three of the five change flags the start task reads are registers
  that `alice_runtime` sets. A register is a host fact, so it survives from one
  role to the next inside one play, and only there.
- **The `control.yml` play cannot come first.** It reads
  `_projector_gate_started` from the projector host, and its verify re-run
  asserts the projector already wrote signals.
- **The `receiver.yml` play must come first.** Alertmanager posts its webhooks
  to the receiver while the projector play runs, and a refused webhook is never
  re-sent. The gap between the two plays is also the dwell time that makes
  "stayed up" in `control.yml` mean something.

## Couplings

- **`projector_gate_started_utc` and the projector-host register are one
  value.** `tasks/main.yml` records the instant on the projector host, into
  `_projector_gate_started`. `tasks/control.yml` needs that same instant as
  `GROUPING_SINCE`, but it runs on another host, in another play, where the
  register does not exist. The playbook must carry it across, as shown above.
  The reason the two must match: the episode-grouping check may only judge rows
  the currently running projector wrote. Set it earlier and the previous
  projector's rows fail the deploy that replaces it. An empty string is not a
  valid date, so the playbook falls back to `now-1h` with
  `| default('now-1h', true)` when the register is missing.
- **The five change flags on the start task.** Two are this role's own
  registers, `_projector_remote_script` and `_projector_remote_unit`. Three are
  `alice_runtime`'s: `_projector_remote_modules`, `_projector_remote_catalog`,
  `_projector_remote_edges`. Each carries `| default(false)`, so a missing
  register does not fail the task — it silently means "nothing changed". If
  `alice_runtime` renames any of the three, a shared module can change and the
  projector will not restart onto it. The names are the contract.
- **`projector_gate_failed` and the final play in `site.yml`.** The rescue sets
  it with `set_fact` on the host running the play. That host used to be the
  control host and is now the projector host, while the final play is still
  `hosts: control`. The playbook resolves that: the final play in `site.yml`
  reads the fact out of `hostvars[groups['projector'][0]]`, so an unproven gate
  still stops the deploy. This role deliberately holds no `delegate_to` — see
  below.
- **`alertmanager_host_address` and the Alertmanager bind address.** The unit
  file and the readiness gate both reach Alertmanager across the network.
  Alertmanager must listen on `0.0.0.0` and its firewall must admit the
  projector host. Both live in the `alertmanager` role.
- **The receiver's bind address and the Alertmanager webhook target.**
  `INGEST_BIND=127.0.0.1` in `alice-notification-ingest.service.j2` and
  `http://127.0.0.1:{{ notification_ingest_port }}/notifications` in
  `alertmanager.yml.j2` are one decision in two files. Open one without the
  other and notifications are dropped without an error.

## What looks like a mistake and is not

- **`tasks/main.yml` no longer stages the projector's shared modules, catalog
  or causal edges.** Those four files have more than one consumer, so
  `alice_runtime` owns them on every host that runs an ALICE service. This role
  reads their paths and never writes them.
- **The rescue block's `set_fact` has no `delegate_to`.** Adding one would put
  an inventory group name back into a role, which is the coupling this refactor
  removed. The playbook owns the fix.
- **`tasks/receiver.yml` and `tasks/control.yml` are not imported by
  `tasks/main.yml`.** Two hosts, three entry points. A role cannot switch host
  mid-play, and the receiver has to start before the projector while the proofs
  have to run after it.

## Upstream roles rejected

None searched, and none plausible. The projector and the notification receiver
are this repository's own programs. There is no upstream role for software that
exists only here. What the role does around them — install a script, template a
unit, prove a cycle — is four builtin modules.

## Used by

- `playbooks/site.yml`, play "Signal projector on its own node — after the
  monitors and detectors whose signals it normalizes, and after Alertmanager,
  which it must reach before it starts", against `projector`.
- `playbooks/site.yml`, the control-host play that includes `receiver.yml`,
  before the projector play.
- `playbooks/site.yml`, the control-host play that includes `control.yml`,
  after the projector play.
- `make contract` runs `files/test_signal_contract.py` from the checkout.
