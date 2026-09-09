# `sweet_collector`

The worker tier of the ALICE EPN log platform. It runs on every EPN machine and
installs two services: Fluent Bit and `alice-stamper`.

Fluent Bit tails the O2 process logs and DDS logs of this machine, takes
InfoLogger records over TCP, reads the journal and the run orchestrator's log,
parses each source into fields, retags every record by severity, sends it
through the stamper and back, and writes it to the OpenSearch node on the same
machine. Informational records land in this machine's own disposable local
index; warnings and worse go to the replicated central index on the storage
tier. `alice-stamper` stamps every record with its log template identity
(`template_id`, `template_version`, `template_status`) using drain3, and
publishes per-template counts every five minutes.

This is the only role that decides what a log line means. Every index template,
detector, monitor and dashboard downstream depends on the fields it produces.

## How it works

```
                                   ONE EPN WORKER

┌─ INPUTS: this machine only, never another worker ────────────────────────────────┐
│  /var/log/node/dds/*.log             tail, multiline    --> [dds]                │
│  /var/log/node/stdout/*/*.log        tail, multiline    --> [stdout]             │
│      one directory per EPN, one file per process; the file name is `program`     │
│  :5170  (the local producer)         tcp, json          --> [infologger]         │
│  /var/log/o2-infologger-daemon.log   tail               --> [ildaemon]           │
│  the journal, every unit             systemd            --> [journald]           │
│  /var/log/odc/staging/*.log          tail, multiline    --> [odc]                │
│      the run orchestrator, on the storage node only; elsewhere no match          │
│  fb_health.py, every 30 s            exec, json         --> [health]             │
│  /run/alice/stamped.sock             forward            --> [stamped.*]          │
│      the return half of the stamper loop, see STAMP below                        │
└──────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         v
┌─ PARSE, then stamp collector_time and doc_id ────────────────────────────────────┐
│  [dds]         dds_text                severity, program, tid, message;          │
│                                        then slot / channel / task on `message`   │
│  [stdout]      datadist, dpl,          two line formats tried in order;          │
│                dpl_noclock, stdout_rootfirst match wins                          │
│  [infologger]  il_event_time           the event epoch becomes @timestamp        │
│  [ildaemon]    ildaemon, _clients      connected clients / the ceiling           │
│  [journald]    kernel_trace fold,      Comm: joins a kernel fault back           │
│                kernel_comm, allowlist  to the O2 logs                            │
│  [odc]         odc                     severity, program, pid, partition, run    │
│  [health]      lua health_deltas       cumulative counters become *_delta        │
└──────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         v
┌─ RETAG (route by severity / priority) ───────────────────────────────────────────┐
│  [dds]       inf, dbg                     ─┐                                     │
│  [stdout]    INFO DEBUG TRACE Info I D T   ├--> [family.local]                   │
│  [journald]  PRIORITY 5..7                 │                                     │
│  [odc]       inf, dbg                     ─┘                                     │
│                                                                                  │
│  anything else, anything whose severity no parser recovered,                     │
│  and anything no parser claimed at all         --> [family.central]              │
│                                                                                  │
│  Not every source is split. [infologger] and [ildaemon] keep their tags          │
│  and every record of both is durable.                                            │
└──────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         v
┌─ STAMP ──────────────────────────────────────────────────────────────────────────┐
│  out_forward   infologger | ildaemon | family.local | family.central             │
│      --> /run/alice/stamper.sock   ack required, retries unlimited               │
│  alice-stamper: family_of --> recipe_tokens --> drain3 --> three fields          │
│      --> /run/alice/stamped.sock   in_forward, tag_prefix stamped.               │
│  [health] skips the stamper.                                                     │
└──────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         v
┌─ OUTPUTS: every one to localhost:9200 ───────────────────────────────────────────┐
│  [stamped.infologger]        -->  infologger                        replicated   │
│  [stamped.family.local]      -->  application-logs-local-<node_id>  this machine │
│  [stamped.family.central]    -->  application-logs-central          replicated   │
│  [stamped.ildaemon]          -->  application-logs-central          replicated   │
│  [health]                    -->  cockpit-metrics                                │
│                                                                                  │
│  [stamped.infologger|ildaemon|family.central]  --> live lane, HTTP, best effort  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- **Not every source is split.** InfoLogger has its own index and the daemon
  log has no severity column, so every record of both is durable.
- **No worker writes to another worker.** A worker owns
  `application-logs-local-<node_id>` and nothing else.
- **Two timestamps travel with every record.** `@timestamp` is the event's own
  time; `collector_time` is stamped here. Their difference is the
  machine-to-collector latency.
- **`doc_id` is assigned in a filter**, so a retried chunk carries the same
  identifier and `write_operation: create` refuses the duplicate.

## Why not an upstream role

Fluent publishes packages, a Helm chart and a Kubernetes operator, and no
Ansible content. Third-party roles, checked and rejected:

| Role | Why rejected |
|---|---|
| [devops-works/ansible-fluentbit](https://github.com/devops-works/ansible-fluentbit) | Debian and Ubuntu only. The EPNs run Alma 9. |
| [artem-shestakov/ansible_fluentbit](https://github.com/artem-shestakov/ansible_fluentbit) | Emits classic `.conf`. This pipeline needs YAML `multiline_parsers` and inline Lua. |
| [orachide](https://github.com/orachide/ansible-role-fluentbit), [sitewards](https://github.com/sitewards/ansible-role-fluentbit), [ricsanfre](https://galaxy.ansible.com/ricsanfre/fluentbit), [bimdata](https://galaxy.ansible.com/bimdata/fluentbit) | Low activity, and all the same shape: install plus a generic input/output dictionary. |

An upstream role would replace three tasks: add repository, install package,
write the systemd drop-in. The pipeline, the parsers, the Lua filters, the
severity routing, the health sampler and the stamper are the role.

## Requirements

`sweet_opensearch` must have run on the same host first. It writes
`/etc/sweet/opensearch-node.env`, installs `register_node.sh` and starts
the OpenSearch node on `localhost` that every output writes to. The role
carries its own copy of `template_contract.py` and ships it to
`alice_shared_dir`, where the maintenance job and the Shifter import it.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
fluent_bit_version: "4.0.14"
collector_blocked_version_prefixes: ["5."]
collector_allow_blocked_version: false
```

Fluent Bit 5.x loses bytes appended to a file after `logrotate` renames it
away, so the role refuses to install it.

```yaml
collector_journald_enabled: true
collector_journald_filters: []
collector_odc_enabled: true
collector_odc_log_path: /var/log/odc/staging/*.log
collector_dds_extractors: [dds_slot, dds_channel, dds_task]
collector_dpl_extractors: [mft_decoder_error]
collector_stdout_refresh_interval: 5
```

The journal is collected only if the packaged Fluent Bit has the `systemd`
input; the role probes the binary and can only turn the source off. Empty
filters mean every unit. The orchestrator log exists on the storage node only.
The process tree is on NFS, which has no inotify, so the refresh interval is
what finds a new process log.

```yaml
fluent_bit_flush_seconds: 1
fluent_bit_log_buffer_limit: "256M"
fluent_bit_log_retry_limit: 10
fluent_bit_memory_high: "384M"
fluent_bit_memory_max: "768M"
fluent_bit_cpuset: ""
collector_metrics_scrape_open: false
alice_manage_firewalld: true
```

The buffer limit is the per-output filesystem buffer; the forward output to
the stamper retries without limit inside it, so a stamper outage costs no
records while the disk holds. InfoLogger arrives over TCP with nothing behind
the socket, so it is the one source that loses records once the buffer fills.

```yaml
stamper_drain3_version: "0.9.11"
stamper_max_templates: 20000
stamper_publish_seconds: 300
stamper_checkpoint_seconds: 600
stamper_ledger_hours: 48
stamper_local_check: true
```

`drain3` is pinned because the stamping path calls internal methods of that
release. Past `stamper_max_templates` the least recently stamped cluster is
evicted, and a template that comes back is learned again under the same
version identifier.

From `group_vars` and the inventory: `node_id`, `log_root`,
`infologger_tcp_port`, `infologger_daemon_log_path`, `opensearch_http_port`,
`cockpit_metrics_index`, `cockpit_metrics_interval_seconds`,
`collector_metrics_scrape_source`, `shifter_enabled`, `shifter_host`,
`shifter_port`, `shifter_ingest_path`, `template_catalog_index`,
`alice_shared_dir`.

## The stamper

One service per worker, in-band between Fluent Bit's filters and its outputs.
Forward over a Unix socket and not HTTP, because the parsers set the record
time from the log line and an HTTP hop would replace it with arrival time.

| Field | Meaning |
|---|---|
| `template_version` | The exact template text the tree returned. Never rewritten. |
| `template_id` | The mask-class-collapsed text. |
| `template_status` | `matched`, `new` (new to this worker's tree) or `no_template` (nothing left after masking). |

Per chunk: stamp every record, send the chunk back and wait for the Forward
input's acknowledgement, count into the ledger and append one journal line,
then acknowledge the origin. A crash before the acknowledgement replays the
chunk; the chunk identifier stops it being counted twice and `create` with
`doc_id` stops it being indexed twice.

Every `stamper_publish_seconds` it writes bucket documents into
`template-buckets-5m-<day>` and `template-buckets-1h-<month>`, template
definitions and one watermark per node into `template-catalog`, and once an
hour checks the indexed count per template in this node's local index against
what it stamped. Its counters ride the collector's health record into
`cockpit-metrics`.

Stamper down: Fluent Bit buffers to disk and retries without limit. Stamper
crash mid-chunk: the chunk was not acknowledged and is resent.

## Example Playbook

```yaml
- hosts: workers
  become: true
  roles:
    - sweet_collector
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
