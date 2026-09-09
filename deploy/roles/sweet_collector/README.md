# `sweet_collector`

Everything the worker tier does to a log line, and the fleet-wide upkeep of what
that produces. Two modes, chosen by `collector_catalog_maintenance`.

**Node mode** (the default) installs two services on every worker. `alice-stamper`
stamps every record with its template identity. Fluent Bit tails the local log
tree, accepts InfoLogger records over TCP, parses and routes into three log
families, hands every record through the stamper and back, samples its own
health, and writes everything to the OpenSearch node on the same machine.

**Catalog-maintenance mode** runs on one worker, named by
`template_catalog_maintenance_host`: definition expiry, query-history expiry and
the two counting checks, as a oneshot unit on an hourly timer.

This is the only role that decides what a log line means. Everything downstream
— index templates, detectors, monitors, the cockpit — depends on the fields it
produces here.

## Why one role

The collector and the stamper are one thing wearing two names. Fluent Bit's
Forward output writes to a socket `alice-stamper` listens on, and its Forward
input reads the socket the stamper writes back to. While these were two roles the
same four strings — the socket directory, the two socket paths and the status
file — were declared twice, in two namespaces, with nothing asserting they
matched. A rename on one side sent the Forward output to a socket nobody listened
on: no error at deploy time, and no records stamped. The collector also owned the
`tmpfiles.d` entry that creates the stamper's runtime directory, and the
stamper's memory limits were read out of the collector's defaults across a play.
One namespace removes all of that.

The maintenance job joins them because it owns nothing of its own. It expires and
checks the definitions, the bucket documents and the watermarks that the stamper
publishes, through the same `template_contract.py`. `sweet_opensearch` owns the
shape of those indices; this role owns what the documents mean.

## How it is wired

Six inputs, six tags, six outputs, one process. Every OpenSearch output goes
to `localhost` — this VM's own node.

```
                     ONE WORKER VM — alice-ingest-N

┌─ INPUTS: this VM only, never another worker ────────────────────────────────┐
│  /var/log/node/dds/*.log        tail, multiline      --> [dds]              │
│  /var/log/node/stdout/*/*.log   tail, multiline      --> [stdout]           │
│      one directory per EPN, one file per process, named as the farm         │
│      names it. That name is where `program` comes from.                     │
│  :5170  (the local producer)    tcp, json            --> [infologger]       │
│  /var/log/o2-infologger-daemon.log  tail             --> [ildaemon]         │
│  the journal, every unit        systemd              --> [journald]         │
│  /var/log/odc/staging/*.log     tail, multiline      --> [odc]              │
│      the run orchestrator, on the storage node only. A glob, because the     │
│      file is dated and rolls at midnight; elsewhere it matches nothing.      │
│  fb_health.py, every 30 s       exec, json           --> [health]           │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ PARSE, then STAMP collector_time ──────────────────────────────────────────┐
│  [dds]         dds_text                 severity, program, tid, message     │
│                + slot / channel / task, over `message` only                 │
│  [stdout]      datadist, then dpl,      the tree is two line formats, tried │
│                dpl_noclock, stdout_root in measured order; first match wins │
│  [infologger]  il_event_time            event epoch becomes @timestamp      │
│  [ildaemon]    ildaemon + _clients      connected clients / the ceiling     │
│  [journald]    kernel_trace multiline,  Comm: is the join key from a kernel │
│                then kernel_comm         fault back to the O2 logs           │
│  [odc]         odc                      severity, program, pid, and the     │
│                                         partition and run number that join  │
│                                         an orchestration failure to the     │
│                                         detector messages from that run     │
│  [health]      lua health_deltas        cumulative counters become *_delta  │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ ROUTE BY SEVERITY ─────────────────────────────────────────────────────────┐
│  [dds]       inf, dbg          ─┐                                           │
│  [stdout]    INFO DEBUG TRACE   │                                           │
│              Info, I, D, T      ├--> [family.local]                         │
│  [journald]  PRIORITY 5..7      │                                           │
│  [odc]       inf, dbg          ─┘                                           │
│                                                                             │
│  anything else, AND anything whose severity no parser                       │
│  recovered, AND anything no parser claimed at all      --> [family.central] │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ OUTPUTS ───────────────────────────────────────────────────────────────────┐
│  [infologger]      --> localhost:9200   infologger                          │
│  [ildaemon]        --> localhost:9200   application-logs-central            │
│  [family.local]    --> localhost:9200   application-logs-local-<node_id>    │
│  [family.central]  --> localhost:9200   application-logs-central            │
│  [health]          --> localhost:9200   cockpit-metrics                     │
│                                                                             │
│  [infologger] + [ildaemon] + [family.central] --> live lane, HTTP, a VM     │
└─────────────────────────────────────────────────────────────────────────────┘
```

`ildaemon` is the one source severity does not route, because the file has no
severity column. Its whole content is durable: a handful of lines a day, and
the connected-client count against the ceiling in `infoLoggerD.cfg` is only
useful as a series that outlives the node.

`odc` exists on one machine. The tail reads a directory through a glob and on a
worker that glob matches nothing, which is what the daemon-log input already
does on epn323 — so the input is unconditional rather than probed.

The journal is collected only if the packaged Fluent Bit was built with the
`systemd` input. The role asks the binary and turns the source off with a
message if it was not, because a configuration naming an input the binary does
not have aborts the whole service.

**No worker ever writes to another worker.** A worker owns its own
`application-logs-local-<node_id>` index and nothing else. That is why the info tier
is disposable and why cross-worker log shipping is out of scope.

**Two timestamps travel with every record.** `@timestamp` is the event's own
time, from the source. `collector_time` is stamped here, as the record passes
through. Their difference is the machine-to-collector latency, and four
detectors train on it.

### Why the settings are what they are

**Restart and catch-up**

| Setting | Value | Why |
|---|---|---|
| `storage.type` | `filesystem` | Buffered chunks and tail positions survive a restart, so a restart does not re-read the archive. |
| `db` | one per tail input | Where that tail position is kept. |
| `read_from_head` | `true` | The replay writes files before Fluent Bit ever sees them. Tailing from the end would skip the whole load. |
| `refresh_interval` | `5` s | New per-EPN files keep appearing during a replay. |

**Memory under burst**

| Setting | Value | Why |
|---|---|---|
| `storage.max_chunks_up` | `64` | The ceiling on chunks held in memory, roughly 128 MB. Chunks above it stay on disk only. |
| `MemoryHigh` | `384M` | An output needs about twice its input buffer to format a payload, plus 20 % allocator overhead: 128 × 2 × 1.2 ≈ 307 MB worst case. |
| `MemoryMax` | `768M` | Hard stop. The kernel kills the process above this. |

The commonly quoted "Fluent Bit uses 10 MB" figure does not hold for this
configuration. `MemoryHigh` and `MemoryMax` are what bound it.

**Measured, August 2026** (`tools/soak`, container rig, two processor cores, this
exact configuration). Peak resident memory rises with the offered rate and never
approaches 10 MB:

| Offered rate | Peak memory | Records lost |
|---|---|---|
| 20,000 /s | 133 MB | 0 |
| 35,000 /s | 172 MB | 0 |
| 50,000 /s | 228 MB | 0 |

Ingest saturates near **53,000 records a second**. Above that, Fluent Bit reads
its tail files more slowly than they are written and the surplus waits in the
files themselves — no loss, and the backlog drains once the load stops.

`flush` is the one setting that moves memory, and soak round 2 moved it from 5
to 1. At the same 20,000 /s, `flush: 1` peaks at 66 MB against 133 MB for
`flush: 5`, because a second of data is in flight instead of five. Round 2 then
priced the same change in processor time over eight values with three runs each:
the collector's own cost falls **24.5 %** and its peak memory **28.0 %**, and the
two arms' ranges do not overlap. Below 0.5 the cluster pays back more than the
collector saves. See `docs/SOAK_RESULTS.md`. `storage.max_chunks_up` at 32 or at 256 changes nothing
at steady state — the queue never gets deep enough to reach it. It is a ceiling
for backpressure, not a working-set control.

**Durability, measured in time and not in megabytes**

| Lane | Buffer | Retries | Worst case | Why |
|---|---|---|---|---|
| Three log families | `256M` | `10` | ~61 s at 20,000 /s, measured | This tier's job is not to lose logs during data taking. The window scales with the buffer: 2 GB held for 491 s at the same rate. |
| Health | `512K` | `5` | seconds | A heartbeat is worth only its freshness. A late heartbeat is not worth the disk. |
| Live lane | `1M` | `1` | seconds | Best-effort by construction. A dead viewer must never push back on OpenSearch. |

**The three families are not protected equally.** Measured with a fifteen-minute
sink outage at 20,000 records a second: every lost record was InfoLogger.
`family.info` and `family.other` lost **nothing**.

| Output | Delivered | Dropped |
|---|---|---|
| `infologger` | 4,314,450 | 10,085,550 |
| `family.info` | 3,674,985 | 0 |
| `family.other` | 3,600,948 | 0 |

The difference is the source, not the buffer. DDS and stdout arrive by `tail`, so
a slow collector simply leaves the data in the log files — the file is the
backpressure, and nothing is lost while the disk holds. InfoLogger arrives over
TCP, where nothing sits behind the socket: once the output buffer fills, those
records are gone. Raising `fluent_bit_log_buffer_limit` therefore buys time for
InfoLogger alone, and a queue in front of the TCP path would buy far more.

Retries never decided any of this. `retries_failed` stayed at zero throughout —
the buffer cap discards records long before the ten retries are exhausted.

**Latency and reachability**

| Setting | Value | Why |
|---|---|---|
| `flush` | `1` s | Service-level, so it is also the live lane's latency floor. Soak round 2's choice — see couplings. |
| `http_listen` | `127.0.0.1` | Push model. The collector sends its own health documents, so nothing scrapes port 2020 from outside. |
| `hc_errors_count` / `hc_retry_failure_count` / `hc_period` | `1` / `1` / `60` | Deliberately twitchy. `fb_healthy` is a reported signal, not a restart trigger — nothing in this role restarts on it. |

**The two catch-all routing rules key on different fields, deliberately.** DDS
routes on `$severity`, stdout on `$message`. The stdout parser makes the severity
group optional, so a plain line carries no `severity` key and a `$severity` rule
would never match it. The DDS parser requires severity, so the same rule is safe
there.

## Why this is a role and not an upstream one

**There is no vendor Ansible role.** Fluent publishes packages, a Helm chart and
a Kubernetes operator, and no Ansible content.

Third-party roles, checked and rejected in August 2026:

| Role | Why rejected |
|---|---|
| [devops-works/ansible-fluentbit](https://github.com/devops-works/ansible-fluentbit) | Debian and Ubuntu only. These targets are Alma 9. |
| [artem-shestakov/ansible_fluentbit](https://github.com/artem-shestakov/ansible_fluentbit) | Emits classic `.conf`. This config needs YAML `multiline_parsers` and inline Lua. |
| [orachide](https://github.com/orachide/ansible-role-fluentbit), [sitewards](https://github.com/sitewards/ansible-role-fluentbit), [ricsanfre](https://galaxy.ansible.com/ricsanfre/fluentbit), [bimdata](https://galaxy.ansible.com/bimdata/fluentbit) | Low activity, and all the same shape: install plus a generic input/output dictionary. |

- **An upstream role would replace three tasks** — add repository, install
  package, write the systemd drop-in. About a fifth of this role.
- **It cannot hold the other four fifths:** the 253-line pipeline, five parsers,
  three Lua filters, the severity routing, the health sampler and the node
  identity file. In a generic role's variable dictionary those are harder to
  read, not easier.
- **Re-open this decision** if Fluent ships a supported Ansible role.

## Prerequisites

The role does **not** bootstrap the machine. Four things must be true first, all
of them satisfied by the role order in `playbooks/site.yml`.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `firewalld` installed and running | `common` role | The firewall task fails. |
| `/etc/alice-ingest/opensearch-node.env` exists | `sweet_opensearch` role | `fluent-bit.service` refuses to start — the `EnvironmentFile` has no leading dash on purpose. |
| An OpenSearch node listening on `localhost:{{ opensearch_http_port }}` | `sweet_opensearch` role | `register_node.sh` waits, then the unit times out. |
| The `alice-application-local-retention` ISM policy and the ingest pipeline exist in the cluster | `sweet_opensearch` role, on the control host | Records still ship. Retention and field normalisation do not apply. |

**The `producer` role is not a prerequisite.** A collector with no producer starts
and ships nothing.

## How to use it

In a playbook, against the worker group:

```yaml
- name: Fluent Bit collector (worker VMs only)
  hosts: workers
  become: true
  roles:
    - collector
```

`site.yml` carries no tags, so there is no tag that runs this role alone. To
re-run it against workers that are already prepared, use a one-play playbook:

```yaml
- name: Collector only
  hosts: workers
  become: true
  roles:
    - collector
```

```
ansible-playbook -i inventory.yml collector-only.yml
```

- **That form skips the prerequisites**, so it is safe only on a node that has
  already had a full `site.yml` run.
- **The role is idempotent.** It restarts `fluent-bit` only when the config, the
  parsers, the health sampler, the identity file or the unit drop-in changed.

## Collector variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide.

| Variable | Default | Meaning |
|---|---|---|
| `collector_catalog_maintenance` | `false` | Which mode this host runs. `true` skips Fluent Bit and the stamper entirely and installs the maintenance timer instead. |
| `collector_app_root` | `/opt/alice-ingest` | One root for everything the role installs on a worker. It was three variables holding this same string while these were three roles. |
| `fluent_bit_version` | `4.0.14` | Pinned RPM version. **Was 5.0.8.** 5.x loses bytes appended to a file after `logrotate` renames it away — docs/SOAK_RESULTS.md round 10. The farm workers pin 4.x in the inventory; `epn-infra13` does not, and it is the node that would collect the one source that rotates daily. |
| `collector_blocked_version_prefixes` | `["5."]` | Versions the role refuses to install. Checked before the package task, not documented in a comment and hoped for. |
| `collector_allow_blocked_version` | `false` | Deliberate override, for someone who has re-tested rotation on that build. |
| `collector_repo_baseurl` | packages.fluentbit.io | Upstream yum repository. |
| `collector_repo_gpgkey` | packages.fluentbit.io key | Signing key for that repository. |
| `collector_service_name` | `fluent-bit` | systemd unit and package name. |
| `collector_binary_path` | `/opt/fluent-bit/bin/fluent-bit` | What `ExecStart` runs. |
| `collector_config_dir` | `/etc/fluent-bit` | Holds `collector.yaml` and `parsers.yaml`. |
| `collector_systemd_dropin_dir` | `/etc/systemd/system/fluent-bit.service.d` | Where `override.conf` is written. |
| `collector_health_script` | `{collector_app_root}/fb_health.py` | Health sampler, run by the `exec` input. |
| `collector_health_interval_seconds` | `cockpit_metrics_interval_seconds` (30) | How often that sampler runs. |
| `collector_env_dir` | `/etc/alice-ingest` | Directory for the node identity file. |
| `collector_env_file` | `/etc/alice-ingest/node.env` | This machine's identity. See below. |
| `collector_opensearch_env_file` | `/etc/alice-ingest/opensearch-node.env` | Written by the `sweet_opensearch` role. See below. |
| `collector_register_script` | `{collector_app_root}/register_node.sh` | Installed by `sweet_opensearch` on every worker. This role only names the path. |
| `collector_start_timeout_seconds` | `600` | `TimeoutStartSec`. Coupled — see below. |
| `collector_metrics_scrape_open` | `false` | `true` opens the metrics port to the scrape source. |
| `collector_stdout_refresh_interval` | `5` | How often the process-tree tail sweeps for new files. `/scratch` is NFS and NFS has no inotify, so this is the only thing that finds a program that started since the last sweep. |
| `infologger_daemon_log_path` | `/var/log/o2-infologger-daemon.log` | One explicit file. Never widen this to a `/var/log` wildcard — `/var/log/messages` is already in the journal and tailing it double-counts. |
| `collector_dds_extractors` | `dds_slot`, `dds_channel`, `dds_task` | Slot, channel and launched-task extraction over the DDS `message`. Set to `[]` to drop them. |
| `collector_journald_enabled` | `true` | The role probes the binary and turns this on only if the packaged build has the `systemd` input. A configuration naming an input the binary lacks aborts the whole service. |
| `collector_journald_filters` | `[]` — every unit | OR-ed journal matches. Empty means all of them: Lubos asked for all system logs and the census priced it at nothing. An allow-list only creates blind spots; volume is held down by the priority routing, not by refusing to read. |
| `collector_odc_enabled` | `true` | Whether to read the run orchestrator's log. An off switch, not a probe. |
| `collector_odc_log_path` | `/var/log/odc/staging/*.log` | The real path, on the farm and under replay alike. The replay engine writes the captured files here at a paced rate, keeping their own names. |
| `fluent_bit_storage_path` | `/var/log/flb-storage` | Filesystem buffer and tail position databases. |
| `fluent_bit_http_port` | `2020` | Fluent Bit's own metrics and health endpoint. |
| `fluent_bit_http_listen` | `127.0.0.1` | Loopback since the push cutover. |
| `fluent_bit_flush_seconds` | `1` | Output flush interval. Also delays the live lane by 1 second. |
| `fluent_bit_log_buffer_limit` | `256M` | Per-output filesystem buffer for the three log families. |
| `fluent_bit_log_farm_buffer_limit` | `2G` | Reference only. Read by nobody. The farm figure for the line above. |
| `fluent_bit_log_retry_limit` | `10` | Output retries for the three log families. |
| `fluent_bit_memory_high` | `384M` | `MemoryHigh` on the unit. |
| `fluent_bit_memory_max` | `768M` | `MemoryMax` on the unit. The kernel kills the process above this. |

### Variables the collector requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `node_id` | inventory, per host | `ALICE_NODE_ID`. Stamped on every record and used in the info index name. |
| `log_root` | `group_vars/all.yml` | The directory tree this role creates and the tail inputs watch. Shared with the `producer` role, which writes into it. The `dds/` and `stdout/` subdirectory names are not variables: they are the two families `collector.yaml` tails by name. |
| `infologger_tcp_port` | `group_vars/all.yml` | The TCP input port. Shared with the `producer` role. |
| `opensearch_http_port` | `group_vars/all.yml` | The local OpenSearch every output writes to. |
| `cockpit_metrics_index` | `group_vars/all.yml` | Where health documents land. |
| `cockpit_metrics_interval_seconds` | `group_vars/all.yml` | Backs `collector_health_interval_seconds`. |
| `health_metrics_emit_legacy_node` | `group_vars/all.yml` | Whether the health document also carries a `node` field. |
| `collector_metrics_scrape_source` | `group_vars/all.yml` | The one address allowed through the firewall to the metrics port. |
| `shifter_enabled`, `shifter_host`, `shifter_port`, `shifter_ingest_path` | `group_vars/all.yml` | The second output lane. Disabled removes the output entirely. |

## Non-obvious settings

- **`EnvironmentFile` has no leading dash, in either entry.** A dash makes the
  file optional. Fluent Bit expands an unset variable to an empty string without
  reporting it, so a missing identity file yields a running collector shipping
  into wrong index names. The unit must fail instead.
- **Port `{{ infologger_tcp_port }}` binds `0.0.0.0` with no rule opening it.**
  Its only writer is the replay producer on the same machine, and firewalld's
  default zone blocks the port from every other source. Narrowing the bind is
  safe; opening the firewall is not.
- **The metrics-port rule runs with `state: disabled`.**
  `collector_metrics_scrape_open` is `false`: the collector pushes its own health
  documents, so nothing scrapes port 2020. The task stays so that one variable
  reopens the endpoint for debugging.
- **The health and live-lane outputs get far smaller buffers than the log
  families** — `512K` with 5 retries and `1M` with 1 retry, against `256M`. Their
  durability budget is seconds of loss, not megabytes, and a dead live-lane viewer
  must never push back on OpenSearch.
- **The four log outputs state `write_operation: create`.** It is the plugin's
  default, and it is written out anyway because the template lane depends on it:
  a retried chunk comes back 409 and is refused, so a `doc_id` the cluster
  already holds is never overwritten by a second version. The catalog scan
  raises `GAP_OVERWRITTEN_SOURCE` on any hit with `_version` above one, and
  `deploy/test_provisioning.py` holds every output carrying an `id_key` to the
  stated operation.
- **Every OpenSearch output writes to `localhost`.** A worker ships to its own
  OpenSearch node and to no other.
- **`storage.max_chunks_up: 64` is a literal.** It is the memory ceiling under
  burst, left unparameterised until it is measured under a real burst.

## Couplings

- **`collector_start_timeout_seconds` (600) must exceed `REGISTER_WAIT_ATTEMPTS ×
  (REGISTER_WAIT_MAX_TIME + REGISTER_WAIT_SLEEP)`** from `register_node.sh`.
  `ExecStartPre` counts against `TimeoutStartSec`, so raising those waits without
  raising this makes systemd kill a collector that was only waiting.
- **`collector_health_interval_seconds` follows
  `cockpit_metrics_interval_seconds`.** The one default here that points at
  another role's variable, kept deliberately: health documents and poller
  documents must share a cadence, or the cockpit's rate panels compare unlike
  series.
- **Both environment files must exist.** `node.env` from this role,
  `opensearch-node.env` from `opensearch`, and the unit loads both. Moving either
  means updating `collector_opensearch_env_file`.
- **Index names are literals in `collector.yaml.j2`** — `infologger`,
  `application-logs-local-${ALICE_NODE_ID}` and `application-logs-central`. They are matched by
  the index templates, the ISM policies, 17 detectors and about 28 monitors, so
  renaming one is a cross-cutting change rather than a variable.

## What is frozen

The domain layer is not parameterised. A collector whose parsers are configurable
is a collector that does nothing.

- The five parsers in `parsers.yaml.j2`.
- The two multiline regular expressions, for DDS and stdout.
- The three Lua filters: health deltas, `collector_time` stamping, InfoLogger
  event time.
- The `rewrite_tag` rules that split `family.local` from `family.central`.

## What this role does not do

- **It does not define the info-tier index settings.** The `sweet_opensearch` role
  writes them to `opensearch-node.env`. A consequence: changing an
  `opensearch_info_*` value does not restart `fluent-bit`, because that file
  belongs to another role in another play. The control host re-runs
  `register_node.sh` for every worker on each deploy, so the cluster converges;
  the worker's own copy applies at its next boot.
- **It does not contain the registration script.** `sweet_opensearch` installs it
  on every worker, the way it already installs `opensearch-node.env`. This role
  names the path in `ExecStartPre` and includes no other role.
- **It does not normalise fields.** The `alice-add-ingest-time` ingest pipeline
  does, so anything bypassing OpenSearch — the live lane — must enrich itself.
- **It does not own `/etc/alice-ingest`.** Both this role and `opensearch` create
  it, deliberately: the directory has no single owner, each role writes its own
  file into it, and `opensearch` also runs on storage nodes where this role never
  does. Both use the same owner, group and mode, so the two cannot drift.

- **It does own `log_root`** and its `dds/` and `stdout/` subdirectories, which
  only the worker tier tails. `producer` also creates the two subdirectories,
  because it writes into them and must not depend on collector ordering.
  `ansible.builtin.file` creates parents, so either role alone is sufficient, and
  both use the same owner, group and mode.

## Used by

`playbooks/site.yml`, in two plays against the `workers` group. The first runs
node mode on every worker. The second runs the same role with
`collector_catalog_maintenance: true`, and carries
`when: inventory_hostname == template_catalog_maintenance_host`, so the
fleet-wide pass happens once. The two cannot share a play: a maintenance pass
reads bucket documents that every worker publishes, so it must follow the whole
fleet.

Within node mode, `tasks/stamper.yml` runs before `tasks/collector.yml`. The
listening socket must exist when Fluent Bit starts; a collector that starts first
only buffers and retries.

## Includes

Nothing. This role includes no other role.

## The stamper
Stamps every record with its template identity before it reaches OpenSearch.
One long-running service per worker, in-band between Fluent Bit's filters and
its outputs. `docs/TEMPLATES_FIX_PLAN.md` is the design.

### The loop

```
tail / tcp / systemd
  → parsers, doc_id, severity_norm, rewrite_tag                 (unchanged)
  → out_forward   unix_path=/run/alice/stamper.sock  require_ack_response=on  workers=1
  → alice-stamper (Python: family_of → recipe_tokens → drain3 → three fields)
  → in_forward    unix_path=/run/alice/stamped.sock  tag_prefix=stamped.  storage.type=filesystem
  → opensearch outputs, match stamped.family.local / stamped.family.central / stamped.infologger / stamped.ildaemon
  → live lane,  match_regex ^stamped\.(infologger|ildaemon|family\.central)$
```

The `health` tag bypasses the stamper. Forward over a Unix socket and not HTTP
because the parsers set the record time from the log line and an HTTP hop
would replace it with arrival time; the acceptance test proves the time comes
back intact.

drain3 does the stamping, with the frozen recipe, the masker and the four
Drain patches this role vendors in `files/`, copied onto the node and imported.
Any port would be a second implementation of a masker whose byte-identical
output is the identity.

`files/drainbench.py` and `files/masking.py` are copies of the two files of the
same name in `tools/templating`, which is where they are edited. `roles/shifter`
holds a third copy, for the same reason: a role depends on nothing outside its
own directory and can be lifted into another Ansible tree unchanged. `deploy/test_provisioning.py` fails
if a copy and its source ever differ, and skips that check in a tree that has no
`tools/`.

### Three fields on every record

| Field | Meaning |
|---|---|
| `template_version` | `version_id(family, template)`, the exact text the tree returned. Never rewritten. |
| `template_id` | `canonical_id(template)`, the mask-class-collapsed text. |
| `template_status` | `matched`; `new` when the record created the cluster; `unlearned` when the tree is at its state limit and refused a cluster; `no_template` when the recipe reduced the record to nothing. |

Both component mappings carry them. The InfoLogger mapping is `dynamic:
strict`, so an unmapped field would reject every document.

### Exact counting

Per chunk, in this order: stamp every record; send the chunk back through the
second socket and wait for the Forward input's acknowledgement; count every
record into the ledger and append one journal line; acknowledge the origin.

Three mechanisms make the count exact.

1. **Acknowledge after journaling.** Forward with `require_ack_response` is
   at-least-once. A crash before the acknowledgement replays the chunk; a
   crash after it loses nothing.
2. **Deduplicate by chunk identifier.** The journal line holds the chunk
   identifier Fluent Bit sends with every chunk. A resent chunk is stamped and
   returned again, so the index side stays complete, and it is not counted
   again. `create` with `doc_id` refuses the duplicate documents. The
   identifier set covers the resend window: one hour.
3. **Publish the bucket total beside the per-template counts**, so the storage
   tier checks that the parts sum to the whole without trusting the worker.

The observation clock is `collector_time`. A record whose collector time is
older than the 48-hour ledger goes into a flagged late bucket at stamp time:
the total stays exact, the attribution error is bounded and visible.

The one window left open is stated in the plan: a crash after the return and
before the journal line means the resent chunk is re-mined, and its count can
go to a version that differs from the one the indexed records carry. It
affects one chunk per crash and the stamped-against-indexed check shows it.

### State on the worker

Under `StateDirectory=alice-stamper`: a checkpoint (`stamper-state.json`: the
drain tree per family, the ledger, the pending definitions, the chunk
identifiers, the journal sequence) written atomically every
`stamper_checkpoint_seconds`, and the journal since it. On start the journal
is replayed into the ledger before a connection is accepted. The tree and the
journal are independent: counts come from the journal alone, so a tree older
than the journal costs a re-created cluster and never a count.

After a restart every bucket in the ledger is dirty and republished.
Overwrites make that safe.

### What it publishes, every five minutes

- **Bucket documents** into `template-buckets-5m-<day>` and
  `template-buckets-1h-<month>`: one node, one family, one bucket start, one
  resolution; the total and the nested per-version counts. The identifier is
  those four keys, so a republication overwrites. The index is named from the
  bucket's own start so the republication lands where the first write did.
  Two resolutions from one ledger; summation is exact.
- **Definitions** into `template-catalog`, keyed by the version identifier,
  upserted with a union script: programs, origin hosts, log sources, nodes,
  first and last observation, and the observed widening links as sets
  (`widened_into`, `widened_from`).
- **One watermark** per node into `template-catalog`: `published_through` is
  the start of the open five-minute bucket at publication time, and the
  stamper's counters ride along.
- **The worker-side check.** Once an hour, for the previous completed hour
  and each family: a terms aggregation on `template_version` over this node's
  local index must be at or below the stamped count for every version. The
  result is a `kind: check` document in the catalog.

A failed publication keeps every bucket dirty and increments
`publication_failures`; the next cycle retries.

### Health

The stamper writes its counters to `/run/alice/stamper-status.json` on every
cycle. The collector's `fb_health.py` reads that file and merges every
`stamper_*` field into the record it already pushes into `cockpit-metrics`, so
the stamper rides the existing health path: records and chunks, duplicate
chunks, return failures, unlearned and no-template records, late records,
journal bytes and lines, clusters per process, ledger size, publications and
failures, peak memory, socket backlog. Deltas for the counters that move are
computed by the same Lua filter that computes Fluent Bit's own.

### Failure semantics

- **Stamper down.** Fluent Bit buffers to disk and retries without limit
  (`retry_limit: no_limits` on the forward output), bounded by the storage
  limit. Tailed files survive any outage because the tail database resumes.
  The InfoLogger TCP input has no source to re-read, so its loss boundary is
  the buffer cap, as before.
- **Stamper slow.** The input pauses through the same buffer.
- **Stamper crash mid-chunk.** The chunk was not acknowledged; Fluent Bit
  resends it; the identifier makes the resend harmless.
- **State limit.** At `stamper_max_templates` clusters the tree stops learning
  and stamps `unlearned`; the count is in the health record.

systemd restarts the service (`Restart=always`). The unit gets the same memory
limits as the collector.

### Tests

`files/test_stamper.py` covers the Forward codec in every message mode, the
acknowledge-after-handler rule, byte-identical stamps against the offline
miner, widening links, exact counts and chunk deduplication, the failed return
path, journal replay after a crash, late buckets, unstamped records, the state
limit, bucket conservation at both resolutions, definitions and the watermark,
failed publications, republication after restart, the local check and the
cover relation.

`files/test_acceptance_stamper.py` needs a real Fluent Bit binary (it looks in
`/opt/fluent-bit/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, or `$FLUENT_BIT`)
and skips otherwise. It runs the whole loop — tail → forward output → stamper
→ forward input → file output — on a generated corpus and diffs every stamp
against the offline miner: zero differences, the same bar the masker passed.
It also proves the record time survives the loop and that the bucket totals
conserve. A second test kills the stamper between the return and the journal
line and proves the counts and the delivered records agree afterwards.

### Stamper variables

| Variable | Default | Meaning |
|---|---|---|
| `stamper_drain3_version` | `0.9.11` | Pinned; the stamping path calls reviewed internal methods. |
| `stamper_msgpack_version` | `1.1.2` | The Forward protocol codec. |
| `stamper_socket_dir` | `/run/alice` | Both sockets and the status file. `tasks/collector.yml` creates the directory through a `tmpfiles.d` entry, because the stamper's own `RuntimeDirectory=` would not survive the collector's restarts. |
| `stamper_state_dir` | `/var/lib/alice-stamper` | `StateDirectory=`. |
| `stamper_max_templates` | `20000` | Learning ceiling across every family. |
| `stamper_publish_seconds` | `300` | The publication cycle. |
| `stamper_checkpoint_seconds` | `600` | The ledger checkpoint; sets the journal size against the replay time. |
| `stamper_ack_timeout_seconds` | `30` | How long a returned chunk may wait for the Forward input's acknowledgement. |
| `stamper_ledger_hours` | `48` | From `group_vars/all.yml`; the worker keeps this much and nothing older. |
| `stamper_local_check` | `true` | The worker-side stamped-against-indexed check. |
| `stamper_memory_high`, `stamper_memory_max` | `fluent_bit_memory_high`, `fluent_bit_memory_max` | Same limits as Fluent Bit, now a value in the same defaults file rather than another role's read across a play. |

### Variables the stamper requires but does not own

| Variable | Owner |
|---|---|
| `template_catalog_index`, `alice_shared_dir`, `alice_shared_contract_file`, `stamper_ledger_hours` | `group_vars/all.yml` |
| `node_id`, `opensearch_http_port` | inventory and `group_vars/all.yml` |

## The central catalog maintenance
The central maintenance of the template catalog. One oneshot unit on a timer,
on one worker.

### What it does

The worker half of the old catalog producer is gone. Every record is stamped
in-band by `tasks/stamper.yml` above, which also publishes the template
definitions, the exact bucket counts and the per-node watermarks. See
`docs/TEMPLATES_FIX_PLAN.md`.

What stays runs on exactly one host, named by
`template_catalog_maintenance_host` in `group_vars/all.yml`:

- **Definition expiry.** A template definition is deleted 90 days after its
  last observation (`kind: template`, by `last_observed`).
- **Check expiry.** Check results are deleted after the hourly bucket
  retention (`kind: check`, by `checked_at`).
- **Query-history expiry.** `shifter-queries` documents older than one year
  are deleted. The unit runs hourly; this section skips a pass until 24 hours
  have passed since its last clean run.
- **The two counting checks** (plan section 4). Both read the hourly bucket
  documents of the last completed hour behind a one-hour lag.
  - *Conservation.* For every bucket document, the nested counts must sum to
    the total. A mismatch is a ledger bug.
  - *Stamped against indexed.* A terms aggregation on `template_version` over
    each shared index (`application-logs-central`, `infologger`), scoped to the
    node and the hour, must be at or below the stamped count for every
    version. The worker-local index is checked on the worker by the stamper
    itself, once an hour, with the same document shape.

  Failing conservation checks and every stamped-against-indexed result are
  written into `template-catalog` as `kind: check`
  (`contract.check_document`), so the Shifter can show them. The
  `template-count-check` monitor fires on any `ok: false` check in the last
  two hours.

### Every pass publishes what it did

Each pass writes three documents into `template-catalog` at the fixed
identifiers `maintenance:catalog`, `maintenance:queries` and
`maintenance:checks`, `kind: catalog_maintenance`. Each carries the age of that
section's last clean run, what it deleted or found, its failure count, and the
whole section report under `detail`.

### Maintenance variables

| Variable | Default | Meaning |
|---|---|---|
| `template_catalog_maintenance_calendar` | `hourly` | `OnCalendar` on the timer. |
| `template_catalog_maintenance_memory_max` | `256M` | `MemoryMax` on the unit. |
| `template_catalog_maintenance_page` | `1000` | Documents per delete-by-query batch. |
| `template_catalog_maintenance_requests_per_second` | `500` | The delete-by-query throttle. |
| `template_catalog_maintenance_max_docs` | `50000` | Documents one pass may delete per section. |
| `template_catalog_maintenance_timeout` | `300` | Deadline on one request. |
| `template_catalog_query_retention_days` | `365` | Expiry of `shifter-queries` by `issued_at`. |
| `template_catalog_query_cleanup_interval_hours` | `24` | The query section's own clock. |
| `template_catalog_check_retention_days` | `35` | Expiry of `kind: check` documents. |
| `template_catalog_shared_indices` | `application-logs-central,infologger` | The indices the central stamped-against-indexed check reads. |
| `template_catalog_check_hours` | `1` | Completed hours checked per pass. |
| `template_catalog_check_lag_hours` | `1` | Hours behind the current hour the checked window ends. |
| `template_catalog_check_page` | `200` | Bucket documents per listing page. |
| `template_catalog_check_max_buckets` | `5000` | Bucket documents one pass may check. |

### Variables the maintenance mode requires but does not own

| Variable | Owner | Used for |
|---|---|---|
| `template_catalog_index`, `template_buckets_1h_prefix`, `shifter_queries_index` | `group_vars/all.yml` | The catalog, the hourly bucket pattern and the query history. |
| `alice_shared_dir`, `alice_shared_contract_file` | `group_vars/all.yml` | The shared contract module. |
| `template_catalog_maintenance_host` | `group_vars/all.yml` | Derived from the `workers` group, so it cannot be a role default. `site.yml` puts the condition on the play, so no task in this role repeats it. |
| `template_catalog_definition_retention_days` | `group_vars/all.yml` | The 90-day definition retention. |
| `opensearch_http_port` | `group_vars/all.yml` | The local cluster endpoint. |

### Maintenance tests

`files/test_catalog_maintenance.py` drives the pass against a fake cluster:
the check window, conservation, stamped-against-indexed in both directions, an
unreadable index, a partial listing, the bucket ceiling, the three expiry
sections and their clocks, and the state file.
