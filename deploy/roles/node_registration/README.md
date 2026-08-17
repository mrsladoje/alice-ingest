# `node_registration`

Installs `register_node.sh`, the single definition of a worker's three
OpenSearch objects: its index template, its retention attachment and its write
alias.

## Why this is a role and not a file in another role

Two different machines need the same script, and they need it to be the same
bytes:

- **Each worker** runs it as `ExecStartPre` of `fluent-bit.service`, so a node
  repairs its own index at every boot without the inventory.
- **The control host** runs it once per worker during the Dashboards bootstrap.
  `templates.sh` calls it as `"$(dirname "$0")/register_node.sh"`.

If the two copies ever drift, a worker's index template gets two competing
definitions. Holding the file in one role keeps that impossible.

This role installs the file and nothing else. It starts no service, opens no
port and contacts no cluster.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `node_registration_dest` | `/opt/alice-ingest/register_node.sh` | Full path to install to. The parent directory is created. |
| `node_registration_owner` | `root` | Owner of the directory and the script. |
| `node_registration_group` | `root` | Group of the directory and the script. |
| `node_registration_mode` | `"0755"` | Mode of the script. Workers need `0755` because systemd executes it. The control host uses `0750`. |
| `node_registration_dir_mode` | `"0755"` | Mode of the parent directory. |
| `node_registration_notify` | `[]` | Handler topics to notify when the script changes. Lets a consumer restart its own service without this role knowing the handler name. |

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
| `ALICE_INFO_RETENTION_POLICY` | `alice-generic-info-retention` | Retention policy to attach. |

On a worker, `collector` writes these into `/etc/alice-ingest/node.env`, which
`fluent-bit.service` loads as its `EnvironmentFile`.

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
    name: node_registration
  vars:
    node_registration_dest: /opt/alice-ingest/register_node.sh
    node_registration_notify:
      - restart fluent-bit
```

## Used by

- `collector` — on every worker
- `dashboards` (`tasks/bootstrap.yml`) — on the control host
