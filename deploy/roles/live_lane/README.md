# `live_lane`

Installs the live log lane on its own VM: a single-file Python server, a
vendored React page, a firewall rule per client, a systemd unit, and two proofs
that the port answers before any collector is told to push to it.

## What the live lane is, and what it is for

The live lane is the platform's `tail -f`: one page that shows log lines from
every node as they arrive, with no query to write and no search cluster in the
path. It is for watching — a run starting, a service being restarted, a fault
injection landing — not for asking questions about the past. Discover stays for
that.

Collectors POST records to it over HTTP. It holds the newest
`live_lane_replay_rows` in memory, pushes every record to every open browser
over Server-Sent Events, and writes nothing to disk. Each browser then keeps its
own window of the newest `live_lane_buffer_rows` records and filters inside that
window, locally.

Two properties follow from that shape, and they are the whole reason the lane
exists:

- **Watching costs the cluster nothing.** A live tail in Discover is a query
  re-run every few seconds against the same nodes that are indexing the data,
  once per watcher. The lane never reads OpenSearch, so a control-room screen
  left open all shift adds no search load at all.
- **It keeps working while the cluster is red.** The one moment everybody wants
  to read logs is the moment OpenSearch is unhappy. Every other viewing surface
  in this repository — Discover, the cockpit, the episode board — is a client of
  the cluster and goes dark with it. This one is fed straight from the
  collectors and does not.

The cost it does have grows with **readers, not with data**: one open connection
and one thread per viewer. That is why it runs on its own host and not on the
control host.

## What reaches it

| | |
|---|---|
| **In scope** | `infologger` and `generic-log-other` — the InfoLogger stream, and every stdout record whose severity is not `Info`. |
| **Never** | `generic-log-info`. The info tier is the bulk of the volume, and pushing all of it to a browser at farm scale gives a blur nobody can read. The severity split *is* the rate limiter here. |
| **Latency** | About `fluent_bit_flush_seconds` (5 s). The collector's flush interval is service-level, so it is the lane's latency floor too. "Live" means five seconds, not instant. |
| **Delivery** | Best-effort, deliberately. The collector's HTTP output gets a 1 MB buffer and one retry, so a lane that is down or a viewer that is slow can never push back on the OpenSearch path. |
| **Enrichment** | The lane does its own. Field normalization lives in the `alice-add-ingest-time` ingest pipeline, which only records going to OpenSearch pass through. `live_lane.py` therefore carries its own copy of the severity table and its own `origin_host` fallback. |

The producing end is the `collector` role: one `http` output, gzip-compressed,
matching those two tags. Turning `live_lane_enabled` off removes that output
entirely.

## What the page gives you

- **A status word and two counts in the header** — `live`, `reconnecting` or
  `paused`; how many rows pass the filters out of how many the tab holds; and,
  when it happened, how many records the server dropped for this viewer.
- **Filters that run in the browser, over rows it already has.** Severity chips
  (`fatal`, `error`, `warning`, `info`, `debug`, `system`, `unknown`), plus
  host, program, run and free-text boxes. The text box searches the message, the
  program and the host. Results are instant and no round trip is made.
- **A view that never jumps.** New records enter the buffer while you read. A
  `N new — show newest` button tells you how many arrived and renders them only
  when you press it.
- **One row per record**: clock, severity, host, program, message, coloured by
  severity. Click a row to open a panel with every field of that record and its
  full event time. Only the rows on screen are rendered, so the tab stays
  responsive with ten thousand records in it.
- **Honest gaps.** A missing run of records draws a marker row naming how many
  were missed; a lane restart draws a marker saying what it held before is gone.
  Neither is silent.
- **A phone layout**: one column, two lines per record, no horizontal scroll.
- **A link back to the Maintainer Cockpit**, matching the *LIVE LOG LANE* button
  in the cockpit that opens this page in a new tab.

## How to reach it

- `/live/` on the Dashboards vhost — nginx on the control host proxies it, so
  operators keep one address and one tunnel. This is the normal way in.
- `http://<livelane host>:8092/` (`live_lane_port`) directly, from an address the
  firewall rule allows.

## What it is not

- **Not a search tool.** No history beyond what your tab holds, no time range,
  no aggregation, nothing on disk. Reload the page and everything older than the
  server's replay backlog is gone. That is the trade for costing the cluster
  nothing.
- **Not complete.** Records can be dropped at both ends — by the collector when
  its small buffer fills, by the server for a viewer that cannot keep up. Both
  are counted and both are shown; neither is prevented.
- **Not the whole log stream.** The info tier never arrives here. If a record is
  missing and it was an `Info`, that is the design, not a fault.
- **Not authenticated by itself** in the default configuration. See
  `live_lane_token` below: the firewall is the boundary.

## The server's endpoints

| Method and path | Who calls it | What it does |
|---|---|---|
| `POST /ingest` (`live_lane_ingest_path`) | the collectors | Accepts one record or an array, gzip or plain. Returns `204`, `400` on a body it cannot parse, `401` when a token is set and not presented. |
| `GET /stream` | the page | Server-Sent Events. Opens with a `hello` event carrying the boot epoch, then the replay backlog, then live records. A keepalive comment after every `LIVE_KEEPALIVE_SECONDS` of silence. |
| `GET /healthz` | Ansible, and you | JSON: `ok`, `epoch`, `viewers`, `buffered`, `received`, `posts`, `bad_posts`, `dropped_slow_client`. This is where you look to tell "nothing is arriving" apart from "nobody is watching". |
| `GET /` and the assets | the browser | The page shell, the script, the stylesheet and the two React files. Flat: only basenames inside the static directory are served. |

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
- **The server's memory is `live_lane_replay_rows`, not
  `live_lane_buffer_rows`.** One list holds the newest `live_lane_replay_rows`
  records and is trimmed to it; that same list is the backlog a new viewer is
  sent. `live_lane_buffer_rows` is the *browser's* window. The unit also passes
  it as `LIVE_BUFFER_ROWS`, which the server reads into a constant and does not
  use.

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
| `live_lane_buffer_rows` | `10000` | Rows one browser tab holds and filters over. Rendered into the page as `bufferRows`; also passed to the unit as `LIVE_BUFFER_ROWS`, which the server does not act on. |
| `live_lane_replay_rows` | `500` | The server's whole in-memory buffer, and therefore what a viewer who connects mid-stream is sent, so a fresh page is not blank. |
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
- **The severity table is a second copy of the collector's normalization.**
  Records that go to OpenSearch are normalized by the `alice-add-ingest-time`
  ingest pipeline; the lane bypasses OpenSearch, so `live_lane.py` carries the
  same table. A severity added on one side and not the other shows here as
  `unknown`.
- **`live_lane_script` and `dashboards_app_root` must stay consistent.** The
  script path is a literal, not `{{ dashboards_app_root }}/live_lane.py`, so
  moving the app root moves the static directory but not the server file.
- **The four `register` names and the restart condition change together.**
  Adding a payload task without adding its `register` to the `state:`
  expression gives a file that is installed but never picked up.
- **`live_lane_buffer_rows` is rendered into the page shell.** The browser trims
  its table to it. The server's own trim is `live_lane_replay_rows`; the two are
  separate windows and are allowed to differ.
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
