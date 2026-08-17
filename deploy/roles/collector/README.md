# `collector`

Installs and configures Fluent Bit on a worker node. It tails the local log
tree, accepts InfoLogger records over TCP, parses and routes them into three
log families, samples its own health, and writes everything to the OpenSearch
node running on the same machine.

This is the only role that decides what a log line means. Everything downstream
— index templates, detectors, monitors, the cockpit — depends on the fields it
produces here.

## Why this is a role and not an upstream one

There is no vendor Ansible role for Fluent Bit. Fluent publishes packages, a
Helm chart and a Kubernetes operator, and no Ansible content at all.

The third-party roles were checked and rejected in August 2026:

| Role | Why rejected |
|---|---|
| [devops-works/ansible-fluentbit](https://github.com/devops-works/ansible-fluentbit) | Debian and Ubuntu only. Our targets are Alma 9. |
| [artem-shestakov/ansible_fluentbit](https://github.com/artem-shestakov/ansible_fluentbit) | Emits classic `.conf`. Our config needs YAML `multiline_parsers` and inline Lua. |
| [orachide](https://github.com/orachide/ansible-role-fluentbit), [sitewards](https://github.com/sitewards/ansible-role-fluentbit), [ricsanfre](https://galaxy.ansible.com/ricsanfre/fluentbit), [bimdata](https://galaxy.ansible.com/bimdata/fluentbit) | Low activity, and all the same shape: install plus a generic input/output dictionary. |

An upstream role would replace three tasks — add repository, install package,
write the systemd drop-in. That is about a fifth of this role. It cannot hold
the other four fifths: the 253-line pipeline, five parsers, three Lua filters,
the severity routing, the health sampler and the node identity file. Moving
those into a generic role's variable dictionary makes them harder to read, not
easier. Re-open this decision if Fluent ever ships a supported role.

## Prerequisites

The role does **not** bootstrap the machine. Four things must be true before it
runs. All four are satisfied by `playbooks/site.yml`, which runs the roles in
the right order.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `firewalld` installed and running; `log_root` and its `dds/` and `stdout/` subdirectories exist | `common` role | The firewall task fails, and the tail inputs find no directory to watch. |
| `/etc/alice-ingest/opensearch-node.env` exists | `opensearch` role | `fluent-bit.service` refuses to start — the `EnvironmentFile` has no leading dash on purpose. |
| An OpenSearch node listening on `localhost:{{ opensearch_http_port }}` | `opensearch` role | `register_node.sh` waits, then the unit times out. |
| The `alice-generic-info-retention` ISM policy and the ingest pipeline exist in the cluster | `dashboards` role, on the control host | Records still ship. Retention and field normalisation do not apply. |

The producer that writes into `log_root` is a separate role and is **not** a
prerequisite. A collector with no producer starts fine and ships nothing.

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

That skips the prerequisites, so it is safe only on a node that has already had
a full `site.yml` run.

The role is idempotent. It restarts `fluent-bit` only when the config, the
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
| `log_root` | `group_vars/all.yml` | The directory tree the tail inputs watch. |
| `infologger_tcp_port` | `group_vars/all.yml` | The TCP input port. Shared with the `producer` role. |
| `opensearch_http_port` | `group_vars/all.yml` | The local OpenSearch every output writes to. |
| `cockpit_metrics_index` | `group_vars/all.yml` | Where health documents land. |
| `cockpit_metrics_interval_seconds` | `group_vars/all.yml` | Backs `collector_health_interval_seconds`. |
| `health_metrics_emit_legacy_node` | `group_vars/all.yml` | Whether the health document also carries a `node` field. |
| `collector_metrics_scrape_source` | `group_vars/all.yml` | The one address allowed through the firewall to the metrics port. |
| `live_lane_enabled`, `live_lane_host`, `live_lane_port`, `live_lane_ingest_path` | `group_vars/all.yml` | The second output lane. Disabled removes the output entirely. |

## Things that look like mistakes and are not

**`EnvironmentFile` has no leading dash — twice.** A dash would make the file
optional. Fluent Bit expands an unset variable to an empty string and says
nothing, so an absent identity file would produce a running collector shipping
into wrong index names. The unit must fail instead.

**Port `{{ infologger_tcp_port }}` binds `0.0.0.0` and no rule ever opens it.**
The only writer is the replay producer on the same machine. `firewalld`'s
default zone blocks the port from everywhere else, so the wide bind is not
reachable. Narrow the bind if you like, but do not "fix" it by opening the
firewall.

**The metrics port firewall rule is normally removed, not added.**
`collector_metrics_scrape_open` is `false`, so the task runs with
`state: disabled`. Since the push cutover nothing scrapes port 2020 — the
collector pushes its own health documents instead. The rule stays in the code so
the endpoint can be reopened for debugging in one variable.

**The health output gets `512K` and 5 retries, the live lane `1M` and 1 retry.**
Both are deliberate and much smaller than the log families' `256M`. A durability
budget here is measured in seconds of loss, not megabytes. A dead live-lane
viewer must never push back on OpenSearch.

**Each OpenSearch output writes to `localhost`.** That is the architecture, not
an oversight. A worker ships to its own OpenSearch node and nowhere else.

**`storage.max_chunks_up: 64` is hardcoded.** It is the memory ceiling under
burst. Left as a literal until it is measured under a real burst.

## Couplings — change these together

**Start timeout and registration waits.** `collector_start_timeout_seconds`
(600) must stay above `REGISTER_WAIT_ATTEMPTS × (REGISTER_WAIT_MAX_TIME +
REGISTER_WAIT_SLEEP)` from the `node_registration` role. `ExecStartPre` counts
against `TimeoutStartSec`, so raising the waits without raising this makes
systemd kill a collector that was only waiting.

**`collector_health_interval_seconds` follows `cockpit_metrics_interval_seconds`.**
This is the one default in this role that points at another role's variable. It
is left that way on purpose: the health documents and the poller documents must
land on the same cadence, or the cockpit's rate panels compare unlike series.

**The two environment files must both be present.** `node.env` comes from this
role. `opensearch-node.env` comes from the `opensearch` role. The unit loads
both. If you change where one lands, change `collector_opensearch_env_file` too.

**Index names are hardcoded in the pipeline.** `infologger`,
`generic-log-info-${ALICE_NODE_ID}` and `generic-log-other` appear as literals
in `collector.yaml.j2`. They are matched by the index templates, the ISM
policies, 17 detectors and about 28 monitors. Renaming them is a cross-cutting
job, not a variable.

## What is frozen

Do not parameterise the domain layer. A collector whose parsers are
configurable is a collector that does nothing.

- The five parsers in `parsers.yaml.j2`.
- The two multiline regular expressions for DDS and stdout.
- The three Lua filters: health deltas, `collector_time` stamping, InfoLogger
  event time.
- The `rewrite_tag` rules that split `family.info` from `family.other`.

Two timestamps travel with every record. `@timestamp` is the event's own time,
from the source. `collector_time` is stamped here, as the record passes through.
Their difference is the machine-to-collector latency, and four detectors train
on it. Field normalisation happens in the `alice-add-ingest-time` ingest
pipeline, not here — so anything that bypasses OpenSearch, meaning the live
lane, must do that enrichment itself.

## What this role deliberately does not do

- **It does not create `log_root`.** The `common` role declares that tree in
  `common_log_dirs` and creates it on all five nodes. The producer writes there
  and this role only reads. One creator is correct.
- **It does not define the info-tier index settings.** The `opensearch` role
  writes them to `opensearch-node.env`.
- **It does not contain the registration script.** The `node_registration` role
  holds it, because the control host runs the same bytes.

`/etc/alice-ingest` is the one directory both this role and `opensearch` create.
That is on purpose and is not the `log_root` case. `log_root` has a declared
owner — `common_log_dirs` — so a second creator was redundant. This directory has
no owner: each role writes its own file into it, and `opensearch` also runs on
storage nodes where this role never does. Both create it with the same owner,
group and mode, so the two cannot drift.

One consequence worth knowing: changing an `opensearch_info_*` value no longer
restarts `fluent-bit` on a worker, because the file that changed belongs to
another role in another play. The cluster still converges — the control host
re-runs `register_node.sh` for every worker on every deploy. The worker's own
copy is applied at its next boot.

## Used by

- `playbooks/site.yml`, against the `workers` group.

## Includes

- `node_registration` — installs `register_node.sh` and notifies
  `restart fluent-bit` when it changes.
