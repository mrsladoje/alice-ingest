# `collector`

Installs and configures Fluent Bit on a worker node. It tails the local log
tree, accepts InfoLogger records over TCP, parses and routes them into three
log families, samples its own health, and writes everything to the OpenSearch
node running on the same machine.

This is the only role that decides what a log line means. Everything downstream
— index templates, detectors, monitors, the cockpit — depends on the fields it
produces here.

## How it is wired

Four inputs, four tags, five outputs, one process. Every OpenSearch output goes
to `localhost` — this VM's own node.

```
                     ONE WORKER VM — alice-ingest-N

┌─ INPUTS: this VM only, never another worker ────────────────────────────────┐
│  /var/log/node/dds/*.log       tail, multiline       --> [dds]              │
│  /var/log/node/stdout/*.log    tail, multiline       --> [stdout]           │
│  :5170  (the local producer)   tcp, json             --> [infologger]       │
│  fb_health.py, every 30 s      exec, json            --> [health]           │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ PARSE, then STAMP collector_time ──────────────────────────────────────────┐
│  [dds]         parser dds_text          severity, source, tid, message      │
│  [stdout]      parser stdout_root       severity optional, facility, message│
│  [infologger]  parser il_event_time     event epoch becomes @timestamp      │
│  [health]      lua health_deltas        cumulative counters become *_delta  │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ ROUTE BY SEVERITY: two log families ───────────────────────────────────────┐
│  [dds]     severity == inf     ─┐                                           │
│  [stdout]  severity == Info    ─┴--> [family.info]                          │
│  either one, anything else      --> [family.other]                          │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ OUTPUTS ───────────────────────────────────────────────────────────────────┐
│  [infologger]    --> localhost:9200   infologger                            │
│  [family.info]   --> localhost:9200   generic-log-info-<node_id>            │
│  [family.other]  --> localhost:9200   generic-log-other                     │
│  [health]        --> localhost:9200   cockpit-metrics                       │
│                                                                             │
│  [infologger] + [family.other] --> live lane, HTTP, a different VM          │
└─────────────────────────────────────────────────────────────────────────────┘
```

**No worker ever writes to another worker.** A worker owns its own
`generic-log-info-<node_id>` index and nothing else. That is why the info tier
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

**Durability, measured in time and not in megabytes**

| Lane | Buffer | Retries | Worst case | Why |
|---|---|---|---|---|
| Three log families | `256M` | `10` | ~50 s best, ~109 min worst | This tier's job is not to lose logs during data taking. The spread is wide because the backoff is jittered. |
| Health | `512K` | `5` | seconds | A heartbeat is worth only its freshness. A late heartbeat is not worth the disk. |
| Live lane | `1M` | `1` | seconds | Best-effort by construction. A dead viewer must never push back on OpenSearch. |

**Latency and reachability**

| Setting | Value | Why |
|---|---|---|
| `flush` | `5` s | Service-level, so it is also the live lane's latency floor. |
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
| `/etc/alice-ingest/opensearch-node.env` exists | `opensearch` role | `fluent-bit.service` refuses to start — the `EnvironmentFile` has no leading dash on purpose. |
| An OpenSearch node listening on `localhost:{{ opensearch_http_port }}` | `opensearch` role | `register_node.sh` waits, then the unit times out. |
| The `alice-generic-info-retention` ISM policy and the ingest pipeline exist in the cluster | `opensearch_bootstrap` role, on the control host | Records still ship. Retention and field normalisation do not apply. |

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

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide.

| Variable | Default | Meaning |
|---|---|---|
| `fluent_bit_version` | `5.0.8` | Pinned RPM version. Matches `images/node/Dockerfile`. |
| `collector_repo_baseurl` | packages.fluentbit.io | Upstream yum repository. |
| `collector_repo_gpgkey` | packages.fluentbit.io key | Signing key for that repository. |
| `collector_service_name` | `fluent-bit` | systemd unit and package name. |
| `collector_binary_path` | `/opt/fluent-bit/bin/fluent-bit` | What `ExecStart` runs. |
| `collector_config_dir` | `/etc/fluent-bit` | Holds `collector.yaml` and `parsers.yaml`. |
| `collector_systemd_dropin_dir` | `/etc/systemd/system/fluent-bit.service.d` | Where `override.conf` is written. |
| `collector_health_script` | `/opt/alice-ingest/fb_health.py` | Health sampler, run by the `exec` input. |
| `collector_health_interval_seconds` | `cockpit_metrics_interval_seconds` (30) | How often that sampler runs. |
| `collector_env_dir` | `/etc/alice-ingest` | Directory for the node identity file. |
| `collector_env_file` | `/etc/alice-ingest/node.env` | This machine's identity. See below. |
| `collector_opensearch_env_file` | `/etc/alice-ingest/opensearch-node.env` | Written by the `opensearch` role. See below. |
| `collector_register_script` | `/opt/alice-ingest/register_node.sh` | Installed through the `node_registration` role. |
| `collector_start_timeout_seconds` | `600` | `TimeoutStartSec`. Coupled — see below. |
| `collector_metrics_scrape_open` | `false` | `true` opens the metrics port to the scrape source. |
| `fluent_bit_storage_path` | `/var/log/flb-storage` | Filesystem buffer and tail position databases. |
| `fluent_bit_http_port` | `2020` | Fluent Bit's own metrics and health endpoint. |
| `fluent_bit_http_listen` | `127.0.0.1` | Loopback since the push cutover. |
| `fluent_bit_flush_seconds` | `5` | Output flush interval. Also delays the live lane by 5 seconds. |
| `fluent_bit_log_buffer_limit` | `256M` | Per-output filesystem buffer for the three log families. |
| `fluent_bit_log_farm_buffer_limit` | `2G` | Reference only. Read by nobody. The farm figure for the line above. |
| `fluent_bit_log_retry_limit` | `10` | Output retries for the three log families. |
| `fluent_bit_memory_high` | `384M` | `MemoryHigh` on the unit. |
| `fluent_bit_memory_max` | `768M` | `MemoryMax` on the unit. The kernel kills the process above this. |

### Variables the role requires but does not own

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
| `live_lane_enabled`, `live_lane_host`, `live_lane_port`, `live_lane_ingest_path` | `group_vars/all.yml` | The second output lane. Disabled removes the output entirely. |

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
- **Every OpenSearch output writes to `localhost`.** A worker ships to its own
  OpenSearch node and to no other.
- **`storage.max_chunks_up: 64` is a literal.** It is the memory ceiling under
  burst, left unparameterised until it is measured under a real burst.

## Couplings

- **`collector_start_timeout_seconds` (600) must exceed `REGISTER_WAIT_ATTEMPTS ×
  (REGISTER_WAIT_MAX_TIME + REGISTER_WAIT_SLEEP)`** from `node_registration`.
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
  `generic-log-info-${ALICE_NODE_ID}` and `generic-log-other`. They are matched by
  the index templates, the ISM policies, 17 detectors and about 28 monitors, so
  renaming one is a cross-cutting change rather than a variable.

## What is frozen

The domain layer is not parameterised. A collector whose parsers are configurable
is a collector that does nothing.

- The five parsers in `parsers.yaml.j2`.
- The two multiline regular expressions, for DDS and stdout.
- The three Lua filters: health deltas, `collector_time` stamping, InfoLogger
  event time.
- The `rewrite_tag` rules that split `family.info` from `family.other`.

## What this role does not do

- **It does not define the info-tier index settings.** The `opensearch` role
  writes them to `opensearch-node.env`. A consequence: changing an
  `opensearch_info_*` value does not restart `fluent-bit`, because that file
  belongs to another role in another play. The control host re-runs
  `register_node.sh` for every worker on each deploy, so the cluster converges;
  the worker's own copy applies at its next boot.
- **It does not contain the registration script.** `node_registration` holds it,
  because the control host runs the same bytes.
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

- `playbooks/site.yml`, against the `workers` group.

## Includes

- `node_registration` — installs `register_node.sh` and notifies
  `restart fluent-bit` when it changes.
