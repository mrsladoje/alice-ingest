# `opensearch_local_index_registration`

Installs `register_node.sh`, the single definition of the three OpenSearch
objects that make one worker's local log tier writable: its index template, its
retention attachment and its write alias.

The role installs the file and nothing else. It starts no service, opens no port
and contacts no cluster. Everything below the first diagram describes the script
the role delivers.

## How it is wired

One file, two installers, two trigger times. The script is idempotent, so both
paths can run it and the last one to look simply finds nothing to do.

```
┌─ THIS ROLE — install only ──────────────────────────────────────────────────┐
│  files/register_node.sh  ──copy──> {{ ..._dest }}, then notify the caller's │
│                                     handler if the bytes changed            │
└──────────┬───────────────────────────────────────────────┬──────────────────┘
           v                                               v
┌─ CONTROL HOST, at deploy ─────────────┐  ┌─ EACH WORKER, at every boot ─────┐
│  installed by opensearch_bootstrap    │  │  installed by collector          │
│  /opt/alice-ingest/init/, mode 0750   │  │  /opt/alice-ingest/, mode 0755   │
│                                       │  │                                  │
│  run by templates.sh, once per worker │  │  run as ExecStartPre of          │
│  identity, from the inventory roster  │  │  fluent-bit.service, for itself  │
│                                       │  │                                  │
│  REGISTER_STRICT=true                 │  │  REGISTER_STRICT unset           │
│  a dead cluster fails the deploy      │  │  a slow cluster only warns       │
│  MIGRATE_EXISTING may delete a        │  │  never deletes anything          │
│  concrete index squatting the alias   │  │                                  │
│                                       │  │  env from node.env +             │
│  env passed on the command line       │  │  opensearch-node.env             │
└──────────────────┬────────────────────┘  └────────────────┬─────────────────┘
                   └──────────────┬─────────────────────────┘
                                  v
┌─ WHAT THE SCRIPT APPLIES, for ALICE_NODE_ID=N ──────────────────────────────┐
│  0. wait   _cluster/health at yellow, else give up (strict decides how)     │
│                                                                             │
│  1. PUT    _index_template/alice-logs-application-local-N                   │
│              pattern application-logs-local-N-*, priority 300               │
│              1 shard, 0 replicas, zstd, pinned by require.box == N          │
│              no refresh_interval — search idle must stay on                 │
│                                                                             │
│  2. POST   _plugins/_ism/add/application-logs-local-N-*                     │
│              attaches alice-application-local-retention, best effort here;  │
│              ism.sh on the control host is the authoritative attach         │
│                                                                             │
│  3. alias  application-logs-local-N                                         │
│        absent        -> create application-logs-local-N-000001, write index │
│        healthy       -> leave it alone                                      │
│        red backing   -> _rollover onto a fresh index, never delete          │
│        concrete index-> strict fails, boot warns, migrate deletes           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Why the same script runs twice

The two runs answer two different events, and neither one can cover for the
other.

- **The deploy cannot wait for the workers.** The detectors, monitors and index
  patterns provisioned later in the same deploy need every worker's index
  template to already exist. `templates.sh` therefore registers all workers from
  the control host, in strict mode, before the collectors are even started.
- **The boot cannot wait for a deploy.** A worker that reboots at 03:00 has no
  Ansible, no inventory and no control host in the loop. After a disk loss its
  write alias still points at a backing index whose shards are gone, so
  create-if-absent passes and every write still fails. The `ExecStartPre` run
  finds that and rolls over, seconds before Fluent Bit's first write.

Only the deploy-time run may destroy anything, and only when explicitly asked
through `REGISTER_MIGRATE_EXISTING`. Repair at boot is limited to rolling over,
because a red index can also mean "recovering right now".

## Why this is a role and not a file in another role

Two roles that run on two different host groups need the same bytes.
`opensearch_bootstrap` runs on the control host only, and it would have to be
included on every worker just to deliver one file, dragging its guards, its two
rendered scripts and its cluster-wide REST calls onto machines that must not make
them. A worker role reaching into another role's `files/` by relative path is the
other option, and it breaks the moment either role moves.

So the file lives in the smallest role that can be included from both places. If
the two copies ever drifted, one worker's index template would have two
competing definitions, and which one won would depend on whether the machine
rebooted before or after the last deploy.

`roles/signal_projector/files/test_signal_contract.py` enforces this: it fails if
`register_node.sh` exists anywhere else in `roles/`, or if either caller stops
installing it through this role.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `opensearch_local_index_registration_dest` | `/opt/alice-ingest/register_node.sh` | Full path to install to. The parent directory is created. |
| `opensearch_local_index_registration_owner` | `root` | Owner of the directory and the script. |
| `opensearch_local_index_registration_group` | `root` | Group of the directory and the script. |
| `opensearch_local_index_registration_mode` | `"0755"` | Mode of the script. Workers need `0755` because systemd executes it. The control host uses `0750`. |
| `opensearch_local_index_registration_dir_mode` | `"0755"` | Mode of the parent directory. |
| `opensearch_local_index_registration_notify` | `[]` | Handler topics to notify when the script changes. Lets a consumer restart its own service without this role knowing the handler name. |

## The script's own settings

`register_node.sh` takes no arguments. It reads environment variables, and the
caller supplies them. It defaults every one of them, so it also runs by hand.

| Variable | Default | Meaning |
|---|---|---|
| `ALICE_NODE_ID` | *(none)* | Which worker to register. Required. |
| `OS_URL` | `http://localhost:${ALICE_OS_HTTP_PORT:-9200}` | Cluster to write to. |
| `REGISTER_STRICT` | `false` | `true` during the deploy, where an unreachable cluster must stop the run. Unset at boot, so a slow local OpenSearch never delays the storage tier. |
| `REGISTER_MIGRATE_EXISTING` | `false` | Deletes a concrete index squatting on the write alias. A deploy-time decision only, never a boot-time one. |
| `REGISTER_WAIT_ATTEMPTS` | `30` | Attempts to reach the cluster. |
| `REGISTER_WAIT_SLEEP` | `3` | Seconds between attempts. |
| `REGISTER_WAIT_MAX_TIME` | `10` | Timeout in seconds for one attempt. |
| `ALICE_INFO_SEARCH_IDLE_AFTER` | `10s` | Index setting written into the template. |
| `ALICE_INFO_TRANSLOG_SYNC_INTERVAL` | `30s` | Index setting written into the template. |
| `ALICE_INFO_MERGE_THREADS` | `1` | Index setting written into the template. |
| `ALICE_LOCAL_RETENTION_POLICY` | `alice-application-local-retention` | Retention policy to attach. |

On a worker these arrive from two files, both loaded by `fluent-bit.service` as
`EnvironmentFile`:

- `/etc/alice-ingest/node.env` — written by `collector`. Node identity, log root,
  Fluent Bit paths and ports, `ALICE_OS_HTTP_PORT`.
- `/etc/alice-ingest/opensearch-node.env` — written by `opensearch`. The four
  `ALICE_INFO_*` index settings, because those are index settings and the
  `opensearch` role owns them.

On the control host neither file is used. `templates.sh` passes the same
variables on the command line, once per worker.

## Timeout coupling

`REGISTER_WAIT_ATTEMPTS × (REGISTER_WAIT_MAX_TIME + REGISTER_WAIT_SLEEP)` is
the worst-case runtime. On a worker that time counts against
`TimeoutStartSec` of `fluent-bit.service`, which the `collector` role sets from
`collector_start_timeout_seconds`. Raise the waits and you must raise that
timeout, or systemd kills a collector that was only waiting.

## Example

```yaml
- name: Install the registration script and restart the collector if it changed
  ansible.builtin.include_role:
    name: opensearch_local_index_registration
  vars:
    opensearch_local_index_registration_dest: /opt/alice-ingest/register_node.sh
    opensearch_local_index_registration_notify:
      - restart fluent-bit
```

## Used by

- `collector` — on every worker
- `opensearch_bootstrap` (`tasks/main.yml`) — on the control host
