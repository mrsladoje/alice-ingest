# `alice_ops`

Installs the operator control panel on the control host. It is the page behind
the replay button: a small Python HTTP server on loopback, plus the two one-shot
units it starts on demand — the fault-injection run and the poison-replay
detector calibration.

The role installs and starts one long-running service, `alice-ops`. The other
two units are installed and left stopped. An operator starts them from the page
or from a `make` target; nothing in this role starts them.

It was split out of the `dashboards` role because it is a different machine
talking to different machines. `alice-ops` reaches the worker VMs — their replay
triggers and their fault agents — and it reaches OpenSearch. It never talks to
OpenSearch Dashboards. Its page is served by the same nginx instance the
`dashboards` role configures, which is the only thing the two share.

## What it does

```
                            CONTROL HOST ONLY

┌─ 1. THE OPS SERVER — one long-running service ─────────────────────────────┐
│  /opt/alice-ingest/ops_server.py     0755 root:root      --> restart       │
│  alice-ops.service                   binds 127.0.0.1:8090                  │
│  nine actions: replay, replay-fresh, stop, wipe, clear,                    │
│                poison-replay, poison-stop, inject, inject-stop             │
│  enabled + started here                                                    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. POISON REPLAY — armed, never started ──────────────────────────────────┐
│  /var/lib/alice-poison-replay       0750, status.json + runs/              │
│  /opt/alice-ingest/poison_replay.py 0755                                   │
│  alice-poison-replay.service        Restart=no, one shot, ProtectSystem    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. FAULT INJECTION — armed, never started ────────────────────────────────┐
│  /var/lib/alice-inject              0750, request.json, status.json, runs/ │
│  /opt/alice-ingest/inject_run.py    0755                                   │
│  alice-inject.service               Restart=no, one shot                   │
│  daemon-reload when that unit changed                                      │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. THE TWO HELPER SCRIPTS ────────────────────────────────────────────────┐
│  /opt/alice-ingest/reset_derived.py    0755  fresh-replay reset            │
│  /opt/alice-ingest/score_injection.py  0755  --> restart alice-metrics     │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **`alice-ops` binds loopback, not the network.** `ops_server.py` listens on
  `127.0.0.1:{{ ops_internal_port }}`. The page reaches an operator only because
  the nginx vhost in the `dashboards` role proxies `/ops/` to that port, and that
  vhost carries the TLS and the basic authentication. Nothing in this role opens
  a firewall port.
- **The two one-shot units are installed and left stopped.** `alice-inject` and
  `alice-poison-replay` both set `Restart=no`. Only the last task of `ops.yml`
  enables and starts a service, and that service is `alice-ops`. Starting a
  calibration run on every deploy would write synthetic documents into
  production detector lanes.
- **`daemon_reload` runs twice, on purpose.** The injection unit gets its own
  conditional reload right after it is templated, because the `alice-ops` unit
  is templated later and its reload would otherwise be the first one. The
  `alice-ops` handler and the final `systemd` task each reload as well.
- **Staging `score_injection.py` notifies `restart alice-metrics`.** The module
  is imported by the injection scorer, not by the metrics poller, but the notify
  is inherited verbatim from the single five-file staging loop this line came
  out of. Dropping it would change the restart graph. The handler itself lives
  in the `cockpit_metrics` role — see couplings.
- **The first task creates the app root from `dashboards_ops_script | dirname`.**
  It derives `/opt/alice-ingest` from the script path instead of naming it. The
  `alice_runtime` role creates the same directory with the same owner, group and
  mode, so the task is redundant once that role has run. It is kept because it
  makes this role runnable on a host `alice_runtime` has not reached.
- **`dashboards_ops_templates_script` is a literal string, not a reference.**
  A default that reads another role's variable resolves lazily and makes this
  role unrunnable alone. See couplings.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `dashboards_ops_script` | `/opt/alice-ingest/ops_server.py` | The installed ops server. The app root is derived from its directory. |
| `dashboards_ops_templates_script` | `/opt/alice-ingest/init/templates.sh` | The index-template script the page re-applies. A literal — see couplings. |
| `dashboards_score_injection_script` | `/opt/alice-ingest/score_injection.py` | The scoring module `inject_run.py` calls after a run. |
| `dashboards_poison_replay_state_dir` | `/var/lib/alice-poison-replay` | Holds the poison status file and the run reports. `0750`. |
| `dashboards_inject_script` | `/opt/alice-ingest/inject_run.py` | The injection engine, used by `make inject` and by the page button. |
| `dashboards_inject_state_dir` | `/var/lib/alice-inject` | Holds the injection request, status and reports. `0750`. |
| `dashboards_inject_report_dir` | `{{ dashboards_inject_state_dir }}/runs` | One report per injection run. |
| `dashboards_inject_default_observe_minutes` | `45` | How long an injection run watches before it scores, when the request does not say. |
| `poison_warmup_poll_seconds` | `120` | Seconds between warm-up checks while detectors train. |
| `poison_observe_seconds` | `330` | Seconds a burst is observed before it is scored. |
| `poison_observe_poll_seconds` | `30` | Seconds between checks during that observation. |
| `poison_max_bursts` | `3` | Attempts before the calibration run gives up. |
| `poison_volume_multiplier` | `12` | How many times normal volume a synthetic burst carries. |
| `poison_min_log_docs` | `300` | Floor on the synthetic log documents per burst. |
| `poison_max_log_docs` | `3000` | Ceiling on the same. |
| `poison_metric_docs` | `30` | Synthetic metric documents per burst. |

### Topology values the playbook supplies

These carry inventory topology. The role must not read `groups[]` itself, so
each one is declared here with an empty literal and filled in by
`group_vars/all.yml`. An empty value renders a unit that starts and reaches
nobody, which is why the play must set them.

| Variable | Default | The expression the playbook supplies |
|---|---|---|
| `worker_replay_trigger_urls` | `[]` | Every worker's `http://<ansible_host>:<replay_http_port>`. Joined with commas into `OPS_WORKER_TRIGGERS` and `POISON_WORKER_TRIGGERS`. |
| `worker_replay_endpoints` | `[]` | The same, prefixed `<inventory_hostname>=`. Joined into `INJECT_WORKER_REPLAY`. |
| `worker_fault_agent_endpoints` | `[]` | `<inventory_hostname>=http://<ansible_host>:<fault_agent_port>` per worker. Joined into `INJECT_WORKER_AGENTS`. |
| `worker_inventory_names` | `[]` | The worker inventory names. Joined into `OPS_INJECT_WORKERS`. |
| `fleet_collector_node_ids` | `[]` | Each worker's `node_id`. Joined into `OPS_WORKER_INFO_NODES`. Shared with `cockpit_metrics` and `anomaly_detection`. |
| `signal_projector_address` | `""` | The projector host's `ansible_host`. Becomes `INJECT_PROJECTOR_AGENT`. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `dashboards_ops_service_name` | `group_vars/all.yml` | The unit name. `status.yml` and `clear.yml` read it too. |
| `dashboards_poison_replay_service_name` | `group_vars/all.yml` | Unit name. Three poison playbooks read it. |
| `dashboards_poison_replay_script` | `group_vars/all.yml` | Installed path. `poison_replay.yml` asserts it exists. |
| `dashboards_poison_replay_status` | `group_vars/all.yml` | Status file. `poison_status.yml` reads it. |
| `dashboards_poison_replay_report_dir` | `group_vars/all.yml` | Report directory. `poison_replay.yml` names it. |
| `dashboards_inject_service_name` | `group_vars/all.yml` | Unit name. `inject.yml` reads it. |
| `dashboards_inject_status` | `group_vars/all.yml` | Status file. `inject.yml` and `status.yml` read it. |
| `dashboards_inject_request` | `group_vars/all.yml` | Request file the page and `make inject` both write. |
| `dashboards_reset_derived_script` | `group_vars/all.yml` | Fresh-replay reset script. `replay.yml` runs it. |
| `dashboards_bootstrap_causal_edges` | `group_vars/all.yml` | Passed to the injection run as `CAUSAL_EDGES`. The `alice_runtime` role installs the file. |
| `dashboards_signal_projector_service_name` | `group_vars/all.yml` | The service an injection stops and restarts. |
| `dashboards_metrics_service_name` | `group_vars/all.yml` | The same, for the metrics poller. Also the poison unit's `After=`. |
| `ops_internal_port` | `group_vars/all.yml` | The loopback port. The nginx vhost in `dashboards` proxies to it. |
| `opensearch_http_port` | `group_vars/all.yml` | `OS_URL` on all three units. |
| `replay_http_port` | `group_vars/all.yml` | Only inside the worker URL lists above. Not read by any template here. |
| `fault_agent_port` | `group_vars/all.yml` | The projector agent URL, and the worker list above. |
| `fault_agent_token` | `group_vars/all.yml` | `INJECT_FAULT_TOKEN`. Must match what the `faults` role installed. |
| `poison_warmup_timeout_seconds` | `group_vars/all.yml` | Warm-up ceiling. `poison_replay.yml` prints the same number. |
| `alice_service_memory_high` / `alice_service_memory_max` | `group_vars/all.yml` | `MemoryHigh` and `MemoryMax` on all three units. |
| `cockpit_metrics_index`, `trend_rollup_index`, `signals_index`, `incidents_index`, `notifications_index`, `lane_state_index`, `fleet_roster_index` | `group_vars/all.yml` | Index names passed to the units. |
| `anomaly_grade_floor` | `group_vars/all.yml` | The grade below which a run ignores an anomaly. |

## Prerequisites

The role does not bootstrap the machine. Four things must be true before the
services it installs do anything useful.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| nginx installed, with the `/ops/` proxy in its vhost | `dashboards` role | The page is unreachable. `alice-ops` binds loopback only, so nothing outside the control host can open it. |
| `templates.sh` present at `dashboards_ops_templates_script` | `opensearch_bootstrap` role | The page's fresh-replay and wipe buttons cannot rebuild the aliases. The unit still starts. |
| `os_cursor.py` in `/opt/alice-ingest` | `alice_runtime` role | `score_injection.py` fails its import, so an injection run produces no score. |
| `causal_edges.json` staged | `alice_runtime` role | An injection run cannot explain a symptom by its cause. |
| Fault agents running on the workers and the projector | `faults` role | An injection has nothing to inject. The `faults` play runs after this one in `site.yml`, which is safe because no run starts at deploy time. |

## How to use it

In a playbook, against the control host:

```yaml
- name: Operator control panel (control host only)
  hosts: control
  become: true
  roles:
    - alice_ops
```

- **Run it on the control host only.** The units reach the workers over HTTP.
  A second copy on a worker would give two panels driving the same fleet.
- **It is idempotent.** `alice-ops` restarts only when its script or its unit
  changed. Nothing else is started.
- **`make contract` runs `files/test_poison_replay.py`.** That test loads
  `poison_replay.py` and `ops_server.py` from this directory, and reads
  `templates/alice-poison-replay.service.j2`, the `Makefile` and the detector
  definitions in `anomaly_detection`.

## Couplings

- **`ops_internal_port` is one decision in two roles.** This role puts it in
  `OPS_PORT`; `dashboards.conf.j2` in the `dashboards` role proxies to the same
  number. Change one and the page 502s.
- **`dashboards_ops_templates_script` must equal
  `opensearch_bootstrap_templates_script`.** It is written here as a literal on
  purpose. A default that interpolates another role's variable resolves lazily,
  so this role could not run without that role's defaults loaded. The price is
  two places to change one path.
- **`restart alice-metrics` is notified here but defined in `cockpit_metrics`.**
  Both roles must appear in the same play. A static `roles:` list loads every
  handler in the play before the first task runs, so the notify resolves from
  any position in that list. A second copy of the handler here would restart the
  poller twice. The real ordering rule for this role is a different one: it must
  run after `dashboards`.
- **`fault_agent_token` is a shared secret.** The injection unit sends it to the
  agents the `faults` role installed. Rotating it means both roles, in one
  deploy.
- **The worker URL lists and their ports move together.** `replay_http_port` and
  `fault_agent_port` are now baked into the six topology values above by the
  playbook, not read by these templates. Changing a port means changing the
  expression in `group_vars/all.yml`, not this role.
- **The units name three services they stop and start:
  `dashboards_signal_projector_service_name`,
  `dashboards_metrics_service_name` and `fluent-bit`.** `fluent-bit` is a
  literal in `alice-inject.service.j2`, matching the `collector` role's unit
  name. A variable here would only let one end of the pair move.

## What is frozen

- **The nine ops actions.** `replay`, `replay-fresh`, `stop`, `wipe`, `clear`,
  `poison-replay`, `poison-stop`, `inject`, `inject-stop`. They are the panel,
  not a preference. `test_poison_replay.py` asserts two of them by name.
- **`Restart=no` on both one-shot units.** A restart policy on a calibration run
  would replay poison after a crash.

## What this role does not do

- **It does not install nginx, TLS material or the basic-auth file.** The
  `dashboards` role does, and its vhost is what exposes this page.
- **It does not create `/opt/alice-ingest/init`.** It only names
  `templates.sh` inside it.
- **It does not install `os_cursor.py` or `causal_edges.json`,** although two of
  its scripts need them. `alice_runtime` owns both.
- **It does not start an injection or a calibration run.** Only an operator
  does, through the page or through `make inject`, `make poison-replay`.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Checked in August 2026.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| Any Galaxy "systemd unit" role | Third-party | The three `template` tasks | The role is three units, five scripts and two state directories. A generic unit-file role would replace nine lines of YAML with a dependency and would not hold the environment blocks, which are the substance. |
| Any Galaxy "python app deploy" role | Third-party | The `copy` tasks | These are single-file scripts with no dependencies beyond the standard library and one sibling module. A virtualenv-and-requirements role has nothing to install. |

There is no upstream for the thing this role actually is: an operations page
for this fleet, driving this replay pipeline.

## Used by

- `playbooks/site.yml`, the control-host play that today runs the `dashboards`
  role — "Control plane — Dashboards, nginx, ops page, monitors, roster,
  metrics and detectors (control host only)". This role must be listed after
  `dashboards`, because the nginx vhost that exposes the page is written there.
- `playbooks/inject.yml`, `poison_replay.yml`, `poison_status.yml`,
  `poison_stop.yml`, `replay.yml`, `clear.yml` and `status.yml` all drive what
  this role installed. None of them include the role.
