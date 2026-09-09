# `loggy_replay`

Installs `alice-replay` on every EPN worker: a service that streams the
archived logs of that machine's slice of the farm out of the CERN S3 backup
bucket and lays them down where the collector on the same machine already
reads. It installs the Python runtime and the firewall rule, copies the engine
and the single-partition wrapper, ships the captured bundle when one is given,
writes the S3 credentials from the vault, links the engine's output root to the
local log root, and installs a unit that is running, armed and idle. The
operator fires it over HTTP.

It is the only source of data on a machine with no live log stream: nothing
downstream has anything to show until it has run.

## How it works

```
                                ONE EPN WORKER  (epn146, epn_partition 0)

┌─ RUNTIME ────────────────────────────────────────────────────────────────────┐
│  dnf python3, python3-pip                                                    │
│  firewalld: :8088/tcp, one rich rule per replay_allowed_client_addresses     │
│  venv /opt/loggy/venv + boto3                    --> restart          │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       v
┌─ INSTALL: three things, two of them files ───────────────────────────────────┐
│  files/replay.py                  --> /opt/loggy/app/replay.py        │
│  files/replay_partition_wrapper.py --> /opt/loggy/app/  (imports it)  │
│  the captured bundle, rsync       --> /var/lib/loggy/bundle           │
│      only when replay_bundle_src is set; otherwise one message               │
│  each --> restart                                                            │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       v
┌─ CREDENTIALS: vault only ────────────────────────────────────────────────────┐
│  /root/.aws                 0700                                             │
│  /root/.aws/credentials     0600, no_log, profile [cern_s3]  --> restart     │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       v
┌─ THE SYMLINK: one line instead of an engine edit ────────────────────────────┐
│  /var/log/node/dds, /var/log/node/stdout        created if absent            │
│  /var/log/alice-replay-root/<node_id>  --->  /var/log/node                   │
│      the engine writes NODES_ROOT/<collector>/...; here that is log_root     │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       v
┌─ UNIT: armed, not fired ─────────────────────────────────────────────────────┐
│  alice-replay.service       AUTOSTART_REPLAY=false          --> restart      │
│  alice-replay.service.d/    created empty; clock.conf belongs to the         │
│                             replay playbook and is never written here        │
│  .replay-earliest-event     deleted unless replay_clock is shifted           │
│  flush handlers, then enable + start                                         │
└──────────────────────────────────────────────────────────────────────────────┘

                        WHAT A TRIGGERED PASS WRITES, per family

  S3 InfoLogger dump  [infologger]  rows for this partition, paced 94/s
      --> tcp 127.0.0.1:5170            the collector's infologger input
  S3 tarball          [dds]         this partition's hosts only, paced 10/s
      --> /var/log/node/dds/<host>.log
  S3 tarball          [stdout]      this partition's hosts only, paced 10/s
      --> /var/log/node/stdout/<host>/<process>.log
  bundle              [ildaemon]    paced 20/s
      --> /var/log/o2-infologger-daemon.log
  bundle              [odc]         paced 200/s, the capture's own file names
      --> /var/log/odc/staging/<file>.log
  bundle              [journald]    not paced: journal files read in place
      --> /var/lib/loggy/bundle/journal/
```

- **Each worker replays only its own slice.** A host belongs to partition
  `epn_num % node_count`. The wrapper drops every other partition's tarball
  before the S3 GET and discards every other partition's InfoLogger row
  locally; InfoLogger only ever goes to `127.0.0.1`.
- **The engine is not edited.** It owns what the archive produces. The
  wrapper imports it and replaces two functions, `list_objects` and
  `il_connect`, plus the pass loop for the shifted clock and `REPLAY_LOOP`.
- **A restart never re-ingests.** The autostart guard is a marker file in
  `log_root`, and a triggered pass is only ever started by a `POST`. A
  restart does end an in-flight pass without re-triggering it, so the role
  restarts only on a real change to the engine, the wrapper, the venv, the
  credentials, the bundle or the unit.
- **Three families need a bundle.** The daemon log, the journal and the run
  orchestrator's log are not in S3. Without `replay_bundle_src` the engine
  skips them and says so.

## Why not an upstream role

There is no vendor software here: the thing deployed is one Python file and
its wrapper. Roles checked and rejected:

| Role | Why rejected |
|---|---|
| [linux-system-roles/systemd](https://github.com/linux-system-roles/systemd) | Deploys unit files and manages units. It would replace two tasks and would not hold the `Environment=` block, which is where this role's substance is. |
| Generic application-deployment roles ([the `ansible-roles` topic](https://github.com/topics/ansible-roles?l=yaml)) | Built for release directories, a `current` symlink and rollback. Two files from a known commit gain ceremony and no safety from that. |

The venv and the copy are one task each. The wrapper, the symlink, the
vault-only credentials and the refusal to write `clock.conf` are the role.

## Requirements

`loggy_collector` must have run on the same host first. It owns `log_root`,
tails `dds/` and `stdout/` out of it, and listens on `infologger_tcp_port`;
without it the replay runs and writes into a void. The play must load
`group_vars/vault.yml`, which holds `vault_s3_access_key_id` and
`vault_s3_secret_access_key`; the credentials task is `no_log`, so a missing
one fails without naming itself. Every worker needs a distinct `epn_partition`
in the inventory, numbered `0 .. node_count-1`; the wrapper refuses to start
without one.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
replay_engine_source: "{{ role_path }}/files/replay.py"
replay_autostart: false
replay_autostart_families: "infologger,dds,stdout"
replay_allowed_client_addresses: []
alice_manage_firewalld: true
```

`replay_autostart: true` fires one pass per host lifetime, not one per
restart. The client list is empty so the role names no inventory group;
`group_vars` sets it to the control host.

```yaml
il_replay_rate: 94
dds_replay_rate: 10
stdout_replay_rate: 10
ildaemon_replay_rate: 20
odc_replay_rate: 200
il_max_objects: 15
dds_max_objects: 0
stdout_max_objects: 0
replay_max_object_bytes: 157286400
```

Rates are lines per second and stretch one pass over about an hour, which is
what the one-minute log detectors need to train. Zero means no cap; objects
above 150 MB are skipped.

```yaml
replay_clock: preserved
replay_clock_cache: /var/log/node/.replay-earliest-event
replay_loop: false
replay_loop_pause_seconds: 30
```

`preserved` keeps the archive's own event times, which is what production
looks like. `shifted` adds one constant offset, now minus the earliest event,
so `@timestamp` lands near now and the recent-window detectors see the data.

```yaml
replay_cpuset: ""
replay_memory_high: ""
replay_memory_max: ""
odc_replay_log_dir: /var/log/odc/staging
```

The engine stands in for logs that arrive on their own in production, so pin
it to cores the collector does not use. Empty leaves it unpinned and unbounded.

From `group_vars` and the inventory: `node_id`, `epn_partition`, `node_count`,
`log_root`, `infologger_tcp_port`, `infologger_daemon_log_path`,
`replay_http_port`, `replay_app_root`, `replay_venv_path`, `replay_bundle_src`,
`replay_bundle_root`, `s3_endpoint`, `s3_bucket`, `s3_region`,
`s3_aws_profile`, `s3_access_key_id`, `s3_secret_access_key`, `run_tag`,
`infologger_prefix`.

## The trigger

The unit serves the engine's own HTTP stub on `replay_http_port`; the wrapper
adds two paths.

| Method and path | Effect |
|---|---|
| `GET /health` | Liveness, the bucket and the run tag. |
| `GET /replay-status` | Whether a pass is running, the loop flag, this collector's name. |
| `POST /replay?family=infologger,dds,stdout` | `202` starts a pass in the background; `409` while one is running. |
| `POST /replay-stop` | Sets the stop flag; the pass ends at its next check. |

The replay playbook posts to `127.0.0.1` on each worker in turn. The ops page
posts to the workers' addresses, which is what the firewall rule admits.

Before it posts, the playbook writes `clock.conf`, a drop-in carrying the
clock, the three S3 rates and the loop settings the operator chose, and
restarts the unit. That file is runtime state. This role creates the drop-in
directory and writes nothing into it, so a deployment never resets a running
load to the group defaults.

Two clocks. Both this role and the playbook delete `replay_clock_cache`
whenever the configured clock is not `shifted`, so a preserved run cannot
inherit an offset from a shifted one. The cache exists because finding the
earliest event re-streams whole tarballs out of S3, and the archive never
changes.

## Example Playbook

```yaml
- hosts: workers
  become: true
  vars_files:
    - "{{ playbook_dir }}/../group_vars/vault.yml"
  roles:
    - loggy_replay
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
