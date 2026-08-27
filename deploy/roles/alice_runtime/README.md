# `alice_runtime`

Puts the shared runtime on every VM that runs an `alice-*` service: the
application root, the two Python modules the services import, and the two JSON
catalogs they read. It also proves both catalogs are readable and complete
before any service starts.

It installs no service, opens no port and starts nothing. Every other new role
assumes this one already ran on its host.

## Why it is a separate role

Five roles need the same four files, and they do not run on the same machine.
`cockpit_metrics`, `alice_ops` and `anomaly_detection` run on the control host,
`signal_projector` on the projector host, `trend_rollup` on a background node.
Before the split, the control host got the files from `bootstrap.yml`, the
projector host got a delegated copy of the same lines in `projector.yml`, and a
background node got a third, shorter copy in `offload_prep.yml`. Three copies of
one decision is three places for it to drift. This role is the one copy.

## What it does

```
                 EVERY HOST THAT RUNS AN alice-* SERVICE

┌─ 1. THE CATALOG DIRECTORY ────────────────────────────────────────────────┐
│  /opt/alice-ingest/init      0755 root:root — traversable, see below      │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    v
┌─ 2. THE TWO CATALOGS, EACH WITH ITS PROOF ────────────────────────────────┐
│  signal_catalog.json   0644   what each monitor and detector means        │
│    runuser -u nobody   parses it as an unprivileged process               │
│  causal_edges.json     0644   which signal explains which other signal    │
│    python3             parses it and asserts every edge is complete       │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    v
┌─ 3. THE APP ROOT AND THE SHARED MODULES ──────────────────────────────────┐
│  /opt/alice-ingest           0755 root:root                               │
│  os_cursor.py          0755   point-in-time + search_after paging         │
│  signal_identity.py    0755   signal naming and classification            │
│                        --> notify alice_runtime_stage_notify              │
└───────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The two catalogs are `0644`, everything else beside them is root-only.**
  `alice-signal-projector` and the other services run with `DynamicUser=true`,
  so they cannot join a stable Unix group. Both catalogs hold public
  classifiers, never credentials. The detector and monitor definitions in the
  same directory stay root-only.
- **The two proofs are tasks, not tests.** The `runuser -u nobody` parse and the
  causal-edge completeness check run on every deploy. A catalog that a sandboxed
  service cannot read fails here, not three plays later inside a service that
  restarts in a loop.
- **The role does not own `/opt/alice-ingest/init`.** `opensearch_bootstrap`
  creates the same directory with the same owner, group and mode, and runs
  first. Both creations are deliberate. See couplings.
- **The module staging task notifies whatever `alice_runtime_stage_notify`
  holds.** `metrics_poller.py` imports `os_cursor`, so a new module has to reach
  a running poller. Only the control host runs that poller, so only the
  control-host play sets the variable, to `restart alice-metrics`. Everywhere
  else it stays the empty list and the task notifies nothing. See couplings.
- **The app-root task is still named "Ensure the control-node app root exists".**
  The name is kept from `bootstrap.yml` so the refactor is a pure move. The task
  now runs on every host in the role, not only the control node.

## Role variables

| Variable | Default | What it does |
|---|---|---|
| `alice_runtime_stage_notify` | `[]` | The handler topics the module staging task notifies. Empty by default, because most hosts in this role have no `alice-metrics` unit. The control-host play sets it to `['restart alice-metrics']`. |

Every path the role writes is site-wide, read by more than one role, and
therefore lives in `group_vars/all.yml`. A second copy in this role's defaults
would be a second place to change one value.

## Variables the role requires but does not own

| Variable | Owner | Used for |
|---|---|---|
| `alice_bootstrap_root` | `group_vars/all.yml` | The catalog directory, `/opt/alice-ingest/init`. Shared with `opensearch_bootstrap`. |
| `alice_bootstrap_signal_catalog` | `group_vars/all.yml` | Destination of `signal_catalog.json`. |
| `alice_bootstrap_causal_edges` | `group_vars/all.yml` | Destination of `causal_edges.json`. |
| `alice_app_root` | `group_vars/all.yml` | The application root, `/opt/alice-ingest`. |
| `alice_os_cursor_script` | `group_vars/all.yml` | Destination of `os_cursor.py`. |
| `alice_signal_identity_script` | `group_vars/all.yml` | Destination of `signal_identity.py`. |
| `cockpit_metrics_service_name` | `group_vars/all.yml` | The unit the `cockpit_metrics` handler restarts. |

## Prerequisites

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `python3` on the host | the base image | Both proofs fail. The catalogs are still staged, so the failure is the proof, not the file. |
| `/usr/sbin/runuser` and the `nobody` account | the base image | The signal-catalog proof fails. |
| Nothing else | — | The role installs no package and starts no service. It can run first in any play. |

## How to use it

Against every host that runs an `alice-*` service, before the role that runs the
service:

```yaml
- name: Shared alice runtime
  hosts: control
  become: true
  vars:
    alice_runtime_stage_notify:
      - restart alice-metrics
  roles:
    - alice_runtime
    - cockpit_metrics
```

- **Set `alice_runtime_stage_notify` only where `alice-metrics` runs.** That is
  the control host. Leave it at its empty default everywhere else.

- **Run it before every role that imports the modules or reads the catalogs.**
  That is `cockpit_metrics`, `alice_ops`, `anomaly_detection`, `trend_rollup`
  and `signal_projector`.
- **The role is idempotent.** It copies four files and creates two directories.
  Only a changed module notifies a restart.

## Couplings

- **`/opt/alice-ingest/init` is created twice on the control host, on purpose.**
  `opensearch_bootstrap` creates it, this role creates it. The owner, group and
  mode are identical in both, so the two cannot drift, and each role stays
  runnable without the other. Change one, change the other.
- **`causal_edges.json` has two readers with two different paths.** This role
  copies the file to the hosts. The `alertmanager` role reads the **source** file
  from the repository through `causal_edges_file` in `group_vars/all.yml`, to
  generate its inhibition rules at template time. That variable must point at
  `roles/alice_runtime/files/causal_edges.json`. Moving the file without moving
  the variable breaks the Alertmanager play, not this one.
- **The notify and the host list change together.** The staging task keeps the
  `restart alice-metrics` notification from `bootstrap.yml`, but the role now
  also runs on hosts that never install `alice-metrics`. The notification is
  therefore a variable, `alice_runtime_stage_notify`, and only the control-host
  play sets it. This role defines no handler at all. `cockpit_metrics` owns the
  single, strict copy, so a restart failure on the control host still stops the
  deploy. Set the variable on a play whose hosts have no poller and the notify
  cannot resolve.
- **The `0644` mode on the two catalogs and `DynamicUser=true` in the service
  units are one decision in two roles.** Tightening the mode here makes the
  projector and the ops services fail to read their own classifiers.
- **`signal_catalog.json` and `expected_monitors` / `expected_detectors` move
  together.** `verify_detection.py` counts the catalog against those numbers.
  The catalog lives here, the counts live in `group_vars/all.yml`, and the JSON
  definitions they count live in `alerting_monitors` and `anomaly_detection`.

## Upstream roles rejected

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| None | — | — | The role stages four files that exist only in this repository. There is nothing upstream to reuse: `ansible.builtin.copy` and `ansible.builtin.file` are the whole implementation. |

## Used by

- `playbooks/site.yml`, on every play whose hosts run an `alice-*` service:
  the control-host Dashboards play, the background analytics play (where it
  replaces `dashboards/tasks/offload_prep.yml`) and the projector play.
