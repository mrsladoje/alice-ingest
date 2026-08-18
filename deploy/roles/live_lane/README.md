# `live_lane`

Installs the live log lane on its own VM: a single-file Python server, a
vendored React page, a firewall rule per client, a systemd unit, and two proofs
that the port answers before any collector is told to push to it.

The live lane is a tail, not a search. Collectors POST log lines to it over
HTTP, it keeps the last `live_lane_buffer_rows` in memory, and it pushes them to
every open browser over Server-Sent Events. Nothing is written to disk and
OpenSearch is never queried, so the page keeps working while the cluster is red.

## Why it is a separate role

It is the only part of the platform that runs on the `livelane` group. It was
already deployed from its own play in `site.yml` through `tasks_from`, which is
the shape of a role that has not been given its own directory yet. It shares no
file, no service and no handler with the Dashboards stack.

## What it does

```
                            HOSTS: livelane

┌─ 1. DIRECTORIES ───────────────────────────────────────────────────────────┐
│  /opt/alice-ingest          0755 root — also created by other roles         │
│  /opt/alice-ingest/live     0755 root — the static document root            │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. PAYLOAD ───────────────────────────────────────────────────────────────┐
│  live_lane.py               0755 the server                                │
│  live_lane.js + .css        0644 the page                                  │
│  react + react-dom          0644 vendored, see files/live/VENDORED.md      │
│  index.html                 0644 rendered — buffer size + cockpit link     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. FIREWALL — named clients only, never the world ────────────────────────┐
│  8092/tcp   one rich rule per address in live_lane_allowed_client_addresses│
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. START, then PROVE ─────────────────────────────────────────────────────┐
│  systemd unit + enable      restarts only when a payload file changed      │
│  GET /healthz               12 attempts, 5 s apart — 1 minute              │
│  GET /                      asserts the served page references live_lane.js│
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The restart is conditional, not a handler.** The service task computes
  `restarted` or `started` from the four `register` results above it. A handler
  would fire at the end of the play, after the two health proofs had already
  run against the old process.
- **`DynamicUser=true` with `ProtectSystem=strict`.** The server has no writable
  path and needs none: the buffer is in memory and the static files are read
  only. There is no `live_lane` system account to create.
- **The page proof reads the body, not the status code.** `GET /` returning 200
  only proves the server is up. Asserting `live_lane.js` appears in the body
  proves the static directory was populated, which is the failure that actually
  happens.
- **React is committed, not fetched.** The deploy has no network to unpkg and no
  build step. `files/live/VENDORED.md` records the versions, the source URLs and
  the SHA-256 sums.
- **The page closes its own stream when its tab is hidden, not when the user
  stops typing.** After `live_lane_hidden_grace_seconds` in the background the
  page closes the `EventSource` and offers a resume button; a visible tab is
  never closed, so a control room screen keeps running untouched. The cost this
  removes is mostly the browser's: `/live/` is proxied over HTTP/1.1 from the
  same origin as Dashboards, so each forgotten tab holds one of the six
  connections that origin is allowed. The server reclaims its thread at the next
  keepalive write, up to `LIVE_KEEPALIVE_SECONDS` later, not immediately.
- **A reconnect is deduplicated, because the server replays its backlog to
  every new connection.** The page ignores any `_id` at or below the highest it
  has seen, so resuming adds no duplicate row. If the first record after a
  reconnect is not the next `_id`, the page draws a marker row naming how many
  records it never received, and does not count them as a slow-client drop.
- **The stream begins with a `hello` event carrying the server's boot epoch.**
  `_seq` restarts at 0 on every service restart, and this role restarts the
  service whenever a payload file changes. The watermark above would then reject
  the whole new sequence and leave an open page reading *live* with nothing
  arriving. A changed epoch clears the watermark and draws a restart marker. The
  epoch is also reported by `/healthz`.
- **`live_lane_token` is empty by default.** An empty value makes the unit omit
  `LIVE_TOKEN`, and the server then accepts any POST from an address the
  firewall let through. The firewall is the boundary.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `live_lane_service_name` | `alice-live-lane` | systemd unit name. |
| `live_lane_script` | `/opt/alice-ingest/live_lane.py` | Installed server. See couplings. |
| `live_lane_static_dir` | `/opt/alice-ingest/live` | Document root. Passed to the unit as `LIVE_STATIC_DIR`. |
| `live_lane_cockpit_url` | `/app/dashboards` | The "back to the cockpit" link in the page. Relative on purpose — the reverse proxy in front of Dashboards is on another host and another scheme. |
| `live_lane_bind` | `0.0.0.0` | `LIVE_BIND`. All interfaces, because the collectors reach it across the network. |
| `live_lane_token` | `""` | `LIVE_TOKEN`. Empty means no shared secret on the ingest path. |
| `live_lane_buffer_rows` | `10000` | Lines held in memory. Also rendered into the page as `bufferRows`. |
| `live_lane_replay_rows` | `500` | Lines sent to a viewer who connects mid-stream, so a fresh page is not blank. |
| `live_lane_client_queue_max` | `2000` | Per viewer. A full queue drops for that viewer; it is never allowed to grow. |
| `live_lane_hidden_grace_seconds` | `120` | How long a browser tab may sit in the background before the page closes its own stream. A visible tab is never closed. `0` turns the behaviour off. Rendered into the page shell as `hiddenGraceSeconds`. |
| `live_lane_memory_high` | `192M` | `MemoryHigh` on the unit. |
| `live_lane_memory_max` | `384M` | `MemoryMax` on the unit. |
| `live_lane_allowed_client_addresses` | `[]` | Addresses allowed through the firewall to the lane port. The playbook supplies it. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `live_lane_port` | `group_vars/all.yml` | The listen port, the firewall rule and both health probes. The `collector` role writes the same number into its output. |
| `live_lane_ingest_path` | `group_vars/all.yml` | `LIVE_INGEST_PATH`. The `collector` role writes the same path into its output. |
| `live_lane_enabled` | `group_vars/all.yml` | Read by the play that calls this role and by the `collector` role. The role itself never reads it. |
| `live_lane_host` | `group_vars/all.yml` | Read by the `collector` role only. It names the `livelane` inventory group, so it cannot be a role default. |
| `dashboards_app_root` | `group_vars/all.yml` | `/opt/alice-ingest`, the parent of the static directory. Shared with every other alice service. |

`live_lane_allowed_client_addresses` replaces an inline
`groups['workers'] + groups['control']` expression. A role default must not name
an inventory group, so the playbook resolves the addresses and passes the list.
An empty list is valid and means no firewall rule is opened, which is what the
default gives a run outside this repository.

## Prerequisites

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `firewalld` installed and running | `common` role | The firewall task fails. `ansible.posix.firewalld` needs the daemon up to apply an immediate rule. |
| `python3` present | base image | The unit's `ExecStart` is `/usr/bin/python3`. The server has no third-party dependency. |

Nothing else. The role does not need OpenSearch, Dashboards or the collectors to
be up. It is deliberately the one path that survives a dead cluster.

## How to use it

In a playbook, against the live lane host:

```yaml
- name: Live lane — the standalone log viewer, off the control host
  hosts: livelane
  become: true
  roles:
    - role: live_lane
      when: live_lane_enabled | bool
      vars:
        live_lane_allowed_client_addresses: >-
          {{ (groups['workers'] + groups['control'])
             | map('extract', hostvars, 'ansible_host') | unique | list }}
```

- **Run it before the `collector` role.** The collectors are configured to push
  to this port. The two health proofs at the end of this role are what make that
  order safe.
- **The role is idempotent.** It restarts the service only when the server, the
  page assets, the rendered page shell or the unit file changed.

## Couplings

- **`live_lane_port` and `live_lane_ingest_path` are shared with the
  `collector` role.** The collector's HTTP output writes both numbers into
  `collector.yaml`. Change them in `group_vars/all.yml`, which both roles read.
  Changing them here alone gives a lane that listens where nothing pushes.
- **`live_lane_script` and `dashboards_app_root` must stay consistent.** The
  script path is a literal, not `{{ dashboards_app_root }}/live_lane.py`, so
  moving the app root moves the static directory but not the server file.
- **The four `register` names and the restart condition change together.**
  Adding a payload task without adding its `register` to the `state:`
  expression gives a file that is installed but never picked up.
- **`live_lane_buffer_rows` appears in two places for one reason.** The unit
  passes it as `LIVE_BUFFER_ROWS` and the page shell renders it as
  `bufferRows`. The server trims its ring buffer, the browser trims its table.
  They are one number and one template variable, so they cannot drift.
- **`live_lane_allowed_client_addresses` only ever adds.** firewalld keeps a
  permanent rule once given one, so removing an address does not close the port
  on a host that already ran. Closing it means `state: disabled` or a fresh
  provision.

## Upstream roles rejected

No upstream candidate was looked for and none applies. The role installs one
file this repository wrote, one page this repository wrote and one unit. There
is no third-party product here to be managed by a community role.

## Used by

- `playbooks/site.yml`, play "Live lane — the standalone log viewer, off the
  control host", against `livelane` — the only caller.
