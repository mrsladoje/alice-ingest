# `faults`

Installs `alice-fault-agent` on every node an injection has to physically reach:
the two workers and the projector host. The agent is a small HTTP server that
can stop and start exactly the services its node owns, and burn that node's CPUs
for a bounded time.

It is the remote half of the injection engine. `alice_ops` decides *which*
failure to stage, when to restore it and how to score it; this role is what makes
the failure actually happen on a machine the control host cannot otherwise touch.
Everything else in the deploy exists to observe the platform. This is the only
role that exists to break it.

## Why it is a separate role

- **Its host set belongs to no other role.** It runs on `workers:projector` — a
  union nothing else in `site.yml` uses. Folding it into `collector` would leave
  the projector host without an agent; folding it into `signal_projector` would
  leave the workers without one. Two copies of one service is the alternative.
- **It is a service, not a step.** Its own unit, port, token, allowlist,
  readiness probe and firewall rule. The playbook that *drives* it
  (`playbooks/inject.yml`) runs on the control host and installs nothing.
- **It owns the port, so it owns the rule.** The rich rule for
  `fault_agent_port` sits next to the service that listens on it, the same
  convention `alertmanager` and `live_lane` follow.

## What it does

```
                      HOSTS: workers + projector, EVERY DEPLOY

┌─ 1. FIREWALL — the control node only, never the fleet ─────────────────────┐
│  {{ fault_agent_port }}/tcp   one rich rule per allowed client address     │
│  today that list is exactly the control host                               │
│  the agent binds 0.0.0.0 — these rules are what confine it                 │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. INSTALL ───────────────────────────────────────────────────────────────┐
│  /opt/alice-ingest              0755 root  — shared with other roles       │
│  fault_agent.py                 0755 root  --> restart alice-fault-agent   │
│  alice-fault-agent.service      0644 root  --> restart alice-fault-agent   │
│    the unit carries the allowlist as FAULT_AGENT_SERVICES                  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. RUN — enable, start, then flush handlers inside the role ──────────────┐
│  the flush matters: step 4 reads the allowlist the restart installs        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. GATE — GET /health, 12 tries, 5 s apart ───────────────────────────────┐
│  assert  health.allowed_services == fault_agent_services                   │
│  a mismatch fails the deploy instead of failing an injection later         │
└────────────────────────────────────────────────────────────────────────────┘
```

## The allowlist

A node may fault only the service it owns. Nothing else is reachable through the
agent, whatever the caller asks for.

| Node | `fault_agent_services` |
|---|---|
| `alice-ingest-1`, `alice-ingest-2` (workers) | `fluent-bit` |
| `alice-ingest-4` (projector) | `alice-signal-projector` |

`_service_action()` refuses any name outside that list with a `409` and the word
`REFUSED`. The list is resolved per host in `group_vars/all.yml`, rendered into
the unit as `FAULT_AGENT_SERVICES`, and read back over `/health` by the deploy
gate.

The gate is the point of the design. Without it, a scenario aimed at a service
the node cannot reach would be refused halfway through a 45-minute injection run,
after the observation window had already started. The assertion moves that
failure to deploy time, where it costs nothing.

## The agent's API

Every call carries `X-Fault-Token` when `fault_agent_token` is set. Responses are
JSON and always include the full health payload as `state`, so a caller never has
to poll separately to learn what changed.

| Method and path | Effect |
|---|---|
| `GET /`, `/health`, `/fault-status` | Node id, the allowlist, whether each listed service is active, whether CPU stress is running. |
| `POST /service-stop?name=<svc>` | `systemctl stop`, allowlist enforced. |
| `POST /service-start?name=<svc>` | `systemctl start`, allowlist enforced. Used by the restore step. |
| `POST /cpu-stress?seconds=<n>` | One busy-loop process per CPU, clamped to `fault_agent_cpu_stress_max_seconds`. Default 900. |
| `POST /cpu-stress-stop` | Kills them all, by tracked handle and by `pkill` on the argv tag. |

Which scenario uses which call is `alice_ops`'s decision, in `inject_run.py`:
`kill-fluent-bit` and `stop-projector` use `/service-stop`, `cpu-stress-worker`
uses `/cpu-stress`, and the restore pass uses the matching start or stop call.

## Non-obvious settings

- **The agent runs as root.** It calls `systemctl` on system units and starts
  processes. A dedicated user would need a sudoers rule per service name, which
  is a second allowlist that can disagree with the first one.
- **It binds `0.0.0.0`, not loopback.** The control host calls it over the
  network. The firewalld rich rule, not the bind address, is what restricts
  access — exactly the split `alertmanager` uses.
- **`fault_agent_token` defaults to empty, and empty means no authentication.**
  `_authorised()` returns `True` when `TOKEN` is falsy. On a CERN-internal
  network behind a single-source firewall rule that is a deliberate default, not
  an oversight — but set a token before this agent runs anywhere the firewall
  rule is wider than one address.
- **CPU stress is `sh -c 'while :; do :; done'` under `timeout`, not `stress-ng`.**
  Nothing has to be installed, and `timeout` caps the burn even if the agent dies.
  Each process carries `alice-fault-cpu-stress` as a trailing argv word purely so
  `pgrep -f` and `pkill -f` can find it — the shell ignores it.
- **Stress is bounded twice.** The `seconds` argument is clamped into
  `[1, fault_agent_cpu_stress_max_seconds]`, and `timeout` enforces it in the
  kernel. A caller cannot leave a worker pinned.
- **`MemoryHigh=256M` / `MemoryMax=512M`.** The agent is idle almost always, and
  these are the standard `alice-*` service bounds. The stress processes are
  children of the unit and count against them; they use CPU, not memory.
- **`log_message` is overridden to do nothing.** The agent would otherwise write
  a journal line per health poll, and the ops page polls continuously.
- **Handlers are flushed inside the role.** The allowlist assertion reads the
  running agent. Without the explicit flush a pending restart would fire at end
  of play, and the gate would check the previous deploy's allowlist.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `fault_agent_service_name` | `alice-fault-agent` | Unit name. Used by the handler and the unit file path. |
| `fault_agent_app_root` | `/opt/alice-ingest` | Directory the script lands in. Shared with other `alice-*` roles, which is why the role creates it rather than assuming it. |
| `fault_agent_script` | `/opt/alice-ingest/fault_agent.py` | `ExecStart` target. |
| `fault_agent_services` | `[]` | The allowlist. Empty here so the role is runnable alone; **the playbook supplies the real per-host list** — `fluent-bit` on a worker, `alice-signal-projector` on the projector host. |
| `fault_agent_allowed_client_addresses` | `[]` | Addresses permitted through the firewall to `fault_agent_port`. Empty for the same reason; the playbook supplies the control host. |

### Variables the role requires but does not own

| Variable | Source | Where it lands |
|---|---|---|
| `fault_agent_port` | `group_vars/all.yml` | Bind address, firewall rule, deploy gate, and `alice_ops`'s two endpoint lists |
| `fault_agent_token` | `group_vars/all.yml` | `FAULT_AGENT_TOKEN` here, `INJECT_FAULT_TOKEN` in `alice_ops` |
| `fault_agent_cpu_stress_max_seconds` | `group_vars/all.yml` | `FAULT_AGENT_MAX_STRESS_SECONDS`, the hard clamp |
| `node_id` | `inventory.yml`, per host | Unit description and `FAULT_AGENT_NODE_ID`, echoed in every health payload |
| `alice_service_memory_high`, `alice_service_memory_max` | `group_vars/all.yml` | The unit's memory bounds |

## How to use it

```yaml
- name: Fault agents — the nodes an injection has to reach (workers + the projector host)
  hosts: workers:projector
  become: true
  roles:
    - faults
```

- **`fault_agent_services` and `fault_agent_allowed_client_addresses` both come
  from `group_vars/all.yml`.** The role's own defaults are empty, so a play that
  supplies neither installs an agent that can fault nothing and answers nobody —
  and the deploy gate catches it on the first run.
- **Run it after `collector` and after `signal_projector`.** The deploy gate only
  proves the agent answers, but an allowlist naming a service that does not exist
  yet is a scenario that fails on its first call.
- **Do not run it on the control host.** The control host is the caller. An agent
  there would let a run stop the services it is using to observe itself.
- **Check an agent by hand:**
  `curl -s -H "X-Fault-Token: $TOK" http://<worker>:8089/health | jq`.
- **Stage a failure through the front door, not this one.** `make inject` and the
  `/ops` page both drive `alice-inject`, which handles the restore, the
  observation window and the scorecard. A raw `POST /service-stop` leaves the
  service down with nothing scheduled to bring it back.

## Couplings

- **`fault_agent_token` is a shared secret with `alice_ops`.** This role writes it
  into the unit; `alice_ops` writes the same value into `alice-inject.service` as
  `INJECT_FAULT_TOKEN`. Setting it in one place only makes every injection call
  return `403`, and the run reports the scenario as failed rather than as
  misconfigured.
- **`fault_agent_port` is read in four places.** The bind address and firewall
  rule here, and `worker_fault_agent_endpoints` plus `INJECT_PROJECTOR_AGENT` in
  `alice_ops`. It lives in `group_vars/all.yml` for that reason.
- **The bind address and the firewall rule are one decision.** `0.0.0.0` is only
  safe because the rule admits a single source address. Widening the rule without
  setting `fault_agent_token` hands anyone who reaches the port the ability to
  stop a service.
- **`fault_agent_services` is resolved per host in `group_vars/all.yml`, not
  here.** The expression reads `groups['workers']` and `signal_projector_host`,
  which is the one thing a role default must never do. It sits in `group_vars`
  beside `worker_fault_agent_endpoints`, next to `common_open_tcp_ports`, which
  is per-host in the same way. The role takes a plain list and names no inventory
  group, so moving the projector or adding a worker is a `group_vars` edit.
- **The firewall rule takes `fault_agent_allowed_client_addresses`, a list.**
  Same convention as `alertmanager_allowed_client_addresses` and
  `live_lane_allowed_client_addresses`. `group_vars` resolves it to
  `[control_host_address]` — one rule today, and widening it is one line in one
  file rather than an edit inside a task.
- **The allowlist and the scenario list must agree.** `inject_run.py` names
  `fluent-bit` and `alice-signal-projector` as literals. Renaming either service
  means editing that file, this role's default, and the deploy gate's message.

## What is frozen

Deployment knobs — the port, the token, the paths, the stress ceiling, the memory
bounds — are parameterised. The domain layer is not, and should not be:

- **The allowlist model itself.** A node faults the service it owns and nothing
  else. A knob that let an agent stop arbitrary units would turn a calibration
  tool into a remote-execution service on every worker.
- **The four fault verbs.** `/service-stop`, `/service-start`, `/cpu-stress`,
  `/cpu-stress-stop` are the physical failures the scoring model was built
  around. A new verb means a new scenario in `inject_run.py` and a new expected
  signal, not a configuration change.
- **The agent's refusal semantics.** `409` plus `REFUSED` is what the injection
  engine matches on to tell "the node said no" apart from "the node is down".

## Upstream roles rejected

Searched August 2026. There is no vendor role, because there is no vendor: the
agent is 150 lines of in-repo Python written for this platform's scoring model.

- [`CSCfi/ansible-role-stress`](https://github.com/CSCfi/ansible-role-stress) —
  installs `stress-ng` and `fio` and runs a stress test from Ansible. It covers
  one of our four verbs, and it covers it the wrong way round: the stress is
  driven from the controller at play time, not exposed as an endpoint the
  scoring run can call and cancel mid-window.
- [Chaos Toolkit's Ansible driver](https://chaostoolkit.org/drivers/ansible/) —
  a real chaos-engineering framework that can run commands over Ansible. It
  wants to own the experiment loop, which `alice-inject` already owns, including
  the observation window and the scorecard the paper needs.
- [`linux-system-roles.systemd`](https://galaxy.ansible.com/ui/standalone/roles/linux-system-roles/systemd/)
  — a maintained wrapper for deploying unit files. It would replace one of our
  seven tasks.

**Kept ours.** The boilerplate an upstream role could take is the unit file and
the directory. What is left — the allowlist, the deploy-time gate that proves it,
the token, the firewall rule tied to the bind address, and the four verbs the
injection engine calls — is the whole role. **What would change the answer:** a
need for fault types beyond stop, start and CPU burn — network partitions, disk
pressure, packet loss — where a framework's existing primitives would beat
growing our own.

## Used by

- `playbooks/site.yml`, play "Fault agents — the nodes an injection has to reach
  (workers + the projector host)", against `workers:projector`.
- `alice_ops`'s `alice-inject.service`, at run time, over HTTP.
- `playbooks/inject.yml` and `make inject`, indirectly, by arming that unit.
- The `/ops` page's injection button, through the same unit.

## Depends on

- `ansible.posix` for `firewalld`, already in `requirements.yml`.
- `common`, for firewalld itself and for `python3`.
- Nothing at run time: the agent imports only the Python standard library, and
  calls `systemctl`, `timeout`, `pgrep` and `pkill` by absolute path.
