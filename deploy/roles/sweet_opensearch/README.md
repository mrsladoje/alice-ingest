# `sweet_opensearch`

Installs one OpenSearch node and joins it to the `alice-logs` cluster. It opens
the two cluster ports to the other nodes, installs the version-pinned build,
writes the node's identity and tier into `opensearch.yml`, caps the heap, starts
the service, and proves that the detection plugins answer.

It installs **either** the vendor RPM **or** a podman container, chosen by
`opensearch_install_method`. One node per machine is an RPM. Several nodes on
one machine are containers, because a second RPM install cannot give a second
service, data directory and port pair on the same host.

**The role has two modes, and a host does exactly one of them.**
`opensearch_cluster_bootstrap` picks between them. Everything above is the node
mode, the default. With the flag on, the role skips all of it and instead
applies the cluster-wide state — indices, index templates, retention policies
and the ingest pipeline — once, from the control host. See
[Cluster bootstrap](#cluster-bootstrap--the-second-mode) below.

The two cannot share a play. The bootstrap needs a cluster that already answers,
which is not true while the batch of nodes is still coming up, so `site.yml`
runs the node mode on all five, gates the batch behind a rolling health check,
and only then runs the bootstrap mode against `control`.

The node mode runs on every node in one play, so that a storage-tier node can be
elected cluster manager while the whole cluster comes up together. "Node" means an
inventory host, not a machine: three inventory hosts may share one
`ansible_host`, and then this role runs three times on that machine and builds
three instances.

## What it does

```
                      EVERY NODE IN alice_nodes

┌─ 1. FIREWALL — cluster members only, never the world ──────────────────────┐
│  9200/tcp   HTTP        rich rule per address in opensearch_cluster_hosts  │
│  9300/tcp   transport   rich rule per address in opensearch_cluster_hosts  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2a. INSTALL, native — signed RPM, version-pinned ─────────────────────────┐
│  yum_repository             artifacts.opensearch.org, gpgcheck on          │
│  rpm_key                    signing key into the rpm keyring               │
│  dnf install                opensearch-{{ opensearch_version }}            │
│  DISABLE_INSTALL_DEMO_CONFIG   suppresses the demo security material       │
│  resource-limits.conf       rlimits + cpuset and memory on the vendor unit │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2b. INSTALL, container — one podman instance ─────────────────────────────┐
│  /etc/opensearch/<node_id>  this instance's own configuration directory    │
│  podman pull                opensearchproject/opensearch:{{ version }}     │
│  <node_id>.container        a quadlet unit; systemd generates the service  │
│  Network=host               distinct http.port/transport.port per instance │
│  --ulimit memlock=-1        the same limit the RPM path sets on the unit   │
│  DISABLE_INSTALL_DEMO_CONFIG   the same env the RPM path passes to dnf     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. DIRECTORIES ───────────────────────────────────────────────────────────┐
│  /var/lib/opensearch        0750, owned by opensearch                      │
│    .../<node_id> per instance and owned by uid 1000 on the container path  │
│  /var/log/opensearch        0750, owned by opensearch                      │
│  /etc/alice-ingest          0755, root — shared with the collector         │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. CONFIGURATION ─────────────────────────────────────────────────────────┐
│  opensearch-node.env        the info-tier index settings, for the worker   │
│  opensearch.yml             identity, tier, discovery, ports  --> restart  │
│  jvm.options.d/heap.options -Xms and -Xmx                     --> restart  │
│  resource-limits.conf       rlimits, cpuset, memory           --> restart  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. START, then PROVE ─────────────────────────────────────────────────────┐
│  flush_handlers             applies the config before the first start      │
│  systemd enable + start                                                    │
│  wait for localhost:9200    60 attempts, 5 s apart — 5 minutes             │
│  opensearch-plugin list     asserts all 7 required plugins                 │
│  anomaly-detection API      asserts it answers 200, or 404 for a config    │
│                             index the first detector has yet to create     │
└────────────────────────────────────────────────────────────────────────────┘
```

## The two tiers

One cluster, two node shapes. The tier comes from `node_tier`, a group variable
in the inventory.

| | Storage nodes | Worker nodes |
|---|---|---|
| `node.roles` | `cluster_manager, data, ingest` | `data, ingest` |
| `node.attr.role` | `storage` | `worker` |
| `node.attr.box` | not set | the node's `node_id` |
| `node.processors` | not set | capped, see below |
| `indices.memory.index_buffer_size` | default | `5%` |

- **Only storage nodes are cluster-manager eligible.** A worker runs the ingest
  firehose. Cluster-manager work must not queue behind it.
- **`node.attr.box` is what pins a worker's own info index to its own machine.**
  The index template sets `require.box`, so `application-logs-local-<node_id>` never
  leaves the VM that produced it. That is why the high-volume path is localhost
  to local shard, with no network hop.

## Non-obvious settings

- **`node.processors` is a cap, never a floor.** The template takes
  `min(opensearch_worker_processors, ansible_processor_vcpus)`. Set as a plain
  value, an EPN node would build thread pools for all its cores, and a 2-vCPU
  staging VM would have its pools inflated to 4 instead of trimmed. The setting
  also shrinks the merge scheduler, which derives from the same number.
- **`DISABLE_INSTALL_DEMO_CONFIG` is set on the install task.** Without it the
  RPM's post-install step generates demo certificates and a demo admin password,
  and writes security settings into `opensearch.yml`. The next task overwrites
  that file, so the material would survive on disk with nothing reading it.
- **`bootstrap.memory_lock: true` needs `LimitMEMLOCK=infinity`.** They are one
  decision in two files. Locking without the limit makes OpenSearch fail its own
  bootstrap check and refuse to start.
- **The handlers are flushed before the first start.** The service is enabled and
  started after `meta: flush_handlers`, so a fresh node never starts once on the
  packaged configuration and then restarts onto ours.
- **A 404 from the anomaly-detection API is accepted, but only one kind.** The
  plugin creates `.opendistro-anomaly-detectors` when the first detector is
  created, which happens later, in the `anomaly_detection` role. Until then the
  search returns `index_not_found_exception`. The task asserts that exact error
  type, so any other 404 still fails the run.
- **`plugins.security.disabled: true`.** Every port this role opens is
  unauthenticated. firewalld is the only boundary, which is why the two rules
  name the cluster addresses instead of opening the ports generally. The
  reverse proxy in front of Dashboards carries the TLS and the basic
  authentication for anything a person reaches.
- **The plugin assertion is a gate, not a setting.** The seven names are what the
  detection and alerting layers need. A distribution missing one of them fails
  here rather than in a detector three plays later.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `opensearch_version` | `3.7.0` | Pinned RPM version. See couplings. |
| `opensearch_yum_repo_baseurl` | artifacts.opensearch.org | Upstream yum repository, keyed on the major version. |
| `opensearch_yum_repo_gpgkey` | artifacts.opensearch.org key | Signing key for that repository. |
| `opensearch_package` | `opensearch-{{ opensearch_version }}` | The exact RPM installed. |
| `opensearch_service_name` | `opensearch` | systemd unit name. |
| `opensearch_cluster_hosts` | `[]` | Addresses allowed through the firewall to both ports. The playbook supplies it. |
| `opensearch_seed_hosts` | `[]` | `discovery.seed_hosts`. The playbook supplies it. |
| `opensearch_initial_cluster_manager_nodes` | `[]` | `cluster.initial_cluster_manager_nodes`. Node names, not addresses. |
| `opensearch_transport_port` | `9300` | Node-to-node port. |
| `opensearch_vm_max_map_count` | `262144` | The kernel bootstrap-check minimum for mmapped Lucene segments. Not a tuning knob. |
| `opensearch_sysctl_conf_file` | `/etc/sysctl.d/99-opensearch.conf` | Where the value above persists. See couplings. |
| `opensearch_data_path` | `/var/lib/opensearch` | `path.data`. |
| `opensearch_log_path` | `/var/log/opensearch` | `path.logs`. |
| `opensearch_heap_size` | `1g` | `-Xms` and `-Xmx`. Declared here on purpose — see couplings. |
| `opensearch_worker_heap_size` | `1g` | Reference only. Read by nobody. The farm value for the line above. Soak round 2 found 1, 2 and 3 GB indistinguishable. |
| `opensearch_worker_processors` | `4` | Worker-tier `node.processors` cap. |
| `opensearch_worker_index_buffer_size` | `5%` | Worker-tier `indices.memory.index_buffer_size`, down from the 10 % default. |
| `opensearch_limit_nofile` | `65536` | `LimitNOFILE` on the unit. |
| `opensearch_security_disabled` | `true` | `plugins.security.disabled`. See non-obvious settings. |
| `opensearch_network_host` | `[_local_]` | Bind addresses. The node's own `ansible_host` is always appended. |
| `opensearch_boot_wait_retries` | `60` | Attempts to reach the local HTTP API after the first start. |
| `opensearch_boot_wait_delay` | `5` | Seconds between those attempts. The two give 5 minutes. |
| `opensearch_ad_api_retries` | `10` | Attempts to reach the anomaly-detection REST API. |
| `opensearch_ad_api_delay` | `3` | Seconds between those attempts. |
| `opensearch_user` / `opensearch_group` | `opensearch` | Owner of the data, log and configuration files. |
| `opensearch_home` | `/usr/share/opensearch` | RPM install root. |
| `opensearch_plugin_bin` | `{{ opensearch_home }}/bin/opensearch-plugin` | Used by the plugin assertion. |
| `opensearch_config_dir` | `/etc/opensearch` | Holds `opensearch.yml`. |
| `opensearch_container_runtime_packages` | `[podman]` | The container runtime, installed only on the container path. |
| `opensearch_container_min_podman_version` | `4.4` | The version quadlet arrived in. Below it the run stops with the reason. |
| `opensearch_quadlet_dir` | `/etc/containers/systemd` | Where quadlet reads `.container` unit files from. |
| `opensearch_jvm_options_d` | `/etc/opensearch/jvm.options.d` | Holds `heap.options`. |
| `opensearch_systemd_dropin_dir` | `/etc/systemd/system/opensearch.service.d` | Holds `resource-limits.conf`. |
| `opensearch_node_env_dir` | `/etc/alice-ingest` | Shared with the `sweet_collector` role. |
| `opensearch_node_env_file` | `/etc/alice-ingest/opensearch-node.env` | The info-tier index settings. See below. |
| `opensearch_required_plugins` | 7 names | Asserted present after start. Not a setting — a gate. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `opensearch_cluster_name` | `group_vars/all.yml` | `cluster.name`. `cluster_id` derives from the same name. |
| `opensearch_http_port` | `group_vars/all.yml` | The REST port. The collector, the bootstrap and every service read it too. |
| `opensearch_info_search_idle_after` | `group_vars/all.yml` | Written into `opensearch-node.env`. Shared with `sweet_opensearch`. |
| `opensearch_info_translog_sync_interval` | `group_vars/all.yml` | Same. |
| `opensearch_info_merge_threads` | `group_vars/all.yml` | Same. |
| `node_id` | inventory, per host | `node.name` and, on a worker, `node.attr.box`. |
| `node_tier` | inventory, per group | Selects the storage or worker branch of `opensearch.yml.j2`. |
| `ansible_host` | inventory, per host | Bind address and `network.publish_host`. |
| `ansible_processor_vcpus` | gathered fact | The floor of the `node.processors` cap. |

`opensearch_version` is the one exception. It is declared in this role's
defaults **and** in `group_vars/all.yml`, because two of the role's own defaults
interpolate it and a role whose defaults reference an undeclared variable cannot
run outside this repository. The `group_vars` value outranks the default and
stays the site source of truth, shared with the `dashboards` role, which pins the
matching OpenSearch Dashboards RPM.

## Prerequisites

The role does not bootstrap the machine. Three things must be true first, all
satisfied by the role order in `playbooks/site.yml`.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `firewalld` installed and running | `common` role | The two firewall tasks fail. `ansible.posix.firewalld` needs the daemon up to apply an immediate rule. |
| Swap in place | `common` role | On a node that also carries the control plane, the kernel selects the OpenSearch JVM when memory runs out. |

## How to use it

In a playbook, against every node in the cluster:

```yaml
- name: OpenSearch cluster
  hosts: alice_nodes
  become: true
  roles:
    - sweet_opensearch
```

- **Run it against the whole cluster in one play, not one node at a time.** A
  fresh cluster needs its cluster-manager-eligible nodes to be reachable at the
  same time, or `cluster.initial_cluster_manager_nodes` cannot be satisfied and
  the first election never completes.
- **`site.yml` follows it with a `serial: 1` gate** that waits for each node's
  HTTP API and then for a cluster health status. That gate is where a rolling
  restart is made safe, not in this role.
- **The role is idempotent.** It restarts `opensearch` only when `opensearch.yml`,
  the heap options or the unit drop-in changed.

## Couplings

- **`bootstrap.memory_lock` and `LimitMEMLOCK` change together.** They live in
  two files, `opensearch.yml.j2` and `resource-limits.conf.j2`. Locking memory
  without the limit is a node that refuses to start.
- **`opensearch_heap_size` is declared here, not in `group_vars/all.yml`.** Role
  defaults rank below every group variable, so an assignment in `inventory.yml`
  now wins. While the same name also existed in `group_vars/all.yml`, the
  `workers` group assignment inside `inventory.yml` had no effect, because group
  variables written in the inventory file rank below `group_vars/all.yml`. Both
  values were `1g`, so the trap was invisible. Do not add the name back to
  `group_vars/all.yml`.
- **`opensearch_version` is shared with the `dashboards` role.** OpenSearch and
  OpenSearch Dashboards must run the same version. Change it in
  `group_vars/all.yml`, which both roles read.
- **The retention policy name in `opensearch-node.env.j2` is a literal.**
  `ism.sh.j2` in `sweet_opensearch` creates the policy under that exact name,
  `verify_detection.py` asserts it, and `register_node.sh` falls back to the same
  literal. A variable here would only let one end of the set move.
- **`opensearch_seed_hosts` carries ports, not bare addresses.** Three nodes on
  one machine share one IP, and discovery can only tell them apart by transport
  port. `group_vars/all.yml` builds the list as `address:port`, reading each
  host's `opensearch_transport_port` out of `hostvars` — which is why both ports
  are declared there and not only in this role's defaults. A role default never
  reaches `hostvars`.
- **The container runtime is prepared by this role, per node.**
  `container_runtime.yml` installs podman, asserts it is new enough for
  quadlet, and creates `opensearch_quadlet_dir` — the directory the next task
  file writes `opensearch-<node_id>.container` into. It runs once per node,
  so on a machine carrying three nodes two of the three passes are no-ops.
- **The container path is not enabled by systemd.** Quadlet generates the unit
  at `daemon-reload`, and a generated unit cannot be enabled — its `[Install]`
  section does that instead. `opensearch_service_enabled` is therefore false on
  the container path, and the role only ever starts the service.
- **`opensearch_cluster_hosts` only ever adds.** firewalld keeps a permanent rule
  once given one, so removing an address does not close the port on a node that
  already ran. Closing it means `state: disabled` or a fresh provision.

## What is frozen

The tier design is not parameterised. A cluster whose node roles are a variable
is a cluster with no design.

- The two `node.roles` lists and the `node_tier == 'storage'` branch.
- `node.attr.role` and `node.attr.box`. The index templates match on both.
- `opensearch_required_plugins` as a set. It is an assertion about what the
  detection layer needs, not a preference.

## What this role does not do

- **It does not create indices, index templates, the ingest pipeline or
  retention policies.** The `sweet_opensearch` role does, once, on the
  control host.
- **It does not install OpenSearch Dashboards.** The `dashboards` role does.
- **It does not own `/etc/alice-ingest`.** Both this role and `sweet_collector` create
  it, deliberately: the directory has no single owner, each role writes its own
  file into it, and this role also runs on storage nodes where the collector
  never does. Both use the same owner, group and mode, so the two cannot drift.
- **It does not restart the collector when the info-tier settings change.**
  `opensearch-node.env` is loaded by `fluent-bit.service`, but that unit belongs
  to another role in another play. The control host re-applies every worker's
  index template on each deploy, so the cluster converges; the worker's own
  rendered copy applies at its next boot.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Checked in August 2026.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| [`opensearch-project/ansible-playbook`](https://github.com/opensearch-project/ansible-playbook) | Vendor | Repository, key, package, directories, JVM options, unit drop-in — about 6 of 15 tasks | The strongest candidate and actively maintained: `main` tracks OpenSearch 3.x and the last release commit is August 2026. Rejected on four points. It is a playbook, not a Galaxy role, so there is no `ansible-galaxy install` path ([issue #44](https://github.com/opensearch-project/ansible-playbook/issues/44) is open). Its defaults read `groups['os-cluster']` and `groups['master']` directly, which is the inventory coupling this tree removed. Its substance is the security plugin — TLS material, an internal user database, role mappings — which this cluster disables. It installs from a tarball into `/usr/share/opensearch` where this role uses the signed yum repository, and AlmaLinux 9 is not a supported platform. |
| [`bbaassssiiee.opensearch`](https://galaxy.ansible.com/ui/repo/published/bbaassssiiee/opensearch/) | Third-party | Install and configure | The most recently updated Galaxy role found, October 2025. Single-maintainer, and it would not hold the two-tier `node.roles` split or the capped `node.processors`. |
| [`dimMaryanto93.opensearch`](https://galaxy.ansible.com/ui/standalone/roles/dimMaryanto93/opensearch/) | Third-party | Install and configure | Last updated July 2024. |
| Other GitHub results | Third-party | — | Every remaining match carries 0 or 1 stars and last moved between 2021 and 2025. |

What upstream cannot hold is the rest of this role: the two-tier `node.roles`
split, `node.attr.box`, the capped `node.processors`, the info-tier index
settings published to each node, and the plugin gate.

**Re-open this decision** if the OpenSearch project publishes its role to Ansible
Galaxy.

## The container path, in variables

Everything below is derived from `opensearch_install_method` and
`opensearch_instance_id`. Set the method on a group and the identity in
`group_vars/all.yml`; nothing else needs an inventory entry except the two
ports.

| Name | Native | Container |
|---|---|---|
| `opensearch_service_name` | `opensearch` | `opensearch-<node_id>` |
| `opensearch_service_enabled` | true | false — see the coupling above |
| `opensearch_config_dir` | `/etc/opensearch` | `/etc/opensearch/<node_id>` |
| `opensearch_data_path` | `/var/lib/opensearch` | `/var/lib/opensearch/<node_id>` |
| `opensearch_log_path` | `/var/log/opensearch` | `/var/log/opensearch/<node_id>` |
| `opensearch_path_data_setting` | the host path | `/usr/share/opensearch/data`, the path inside the container |
| `opensearch_dir_owner` | `opensearch` | `1000`, the image's own uid |
| `opensearch_plugin_list_cmd` | `opensearch-plugin list` | the same, through `podman exec` |

Two more are set per host in the inventory, because they are the only values
that must differ between instances on one machine: `opensearch_http_port` and
`opensearch_transport_port`.

`opensearch_container_memory_max` is empty by default. Set it on a machine that
carries several instances, or on a shared node, and it becomes the unit's
`MemoryMax`.

## What the container path does not simulate

Three containers on one machine give three services, three data directories and
a real cluster-manager quorum of three. They do **not** give fault tolerance. A
replica whose primary is on the same physical disk protects against nothing, and
the machine is a single failure domain. The layout exists so the tier design —
`node.attr.role`, the shard-allocation filters, the replica counts, the index
templates — stays byte-identical to the multi-machine one, and so the move to
several machines is an inventory change rather than a redesign.

## Cluster bootstrap — the second mode

Applies the cluster-wide OpenSearch state that must exist exactly once: the
ingest pipeline, the component and index templates, the persistent cluster
settings, the pre-created indices, and the retention policies. It runs on the
control host, against the cluster as a whole.

It writes no configuration on any node and starts no service. It talks only to
the local OpenSearch REST API, and every call is idempotent, so it runs on every
deploy.

It was part of `dashboards/tasks/bootstrap.yml` until August 2026, then a
separate `opensearch_bootstrap` role, and is now this role's second mode. What
kept it separate was that this role runs on all five nodes while the bootstrap
must run once; the flag settles that, and the play that carries it is the same
one line at the call site it always was. Where the rest of the original went:
the index patterns, the cockpit saved objects and the field-catalog hydration
stayed in `dashboards`; the alerting monitors are the `alerting_monitors` role;
the anomaly detectors, the forecaster and the verification are the
`anomaly_detection` role.

### What the bootstrap does

```
                    CONTROL HOST, ONCE PER DEPLOY

┌─ 1. GUARDS — fail here, not at the REST call ──────────────────────────────┐
│  admission_control_mode        must parse, or the settings PUT is a 400    │
│  worker node identity list     must not be empty, or there is no info tier │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. STAGE — /opt/alice-ingest/init ────────────────────────────────────────┐
│  templates.sh        rendered from templates.sh.j2                         │
│  ism.sh              rendered from ism.sh.j2                               │
│  schema/*.json       28 documents, plus one per worker rendered from       │
│                      templates/schema-per-worker/                          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. templates.sh — waits for cluster health, then applies ─────────────────┐
│  ingest pipeline        alice-add-ingest-time                              │
│  component templates    generic mappings, infologger mappings              │
│  index templates        14, one per log family and derived index           │
│  cluster settings       auto_create_index, query insights,                 │
│                         anomaly-detection batch pacing, admission control  │
│  per-worker objects     one index template, write alias and backing        │
│                         index per worker identity                          │
│  pre-created indices    the 11 derived indices, so nothing races a mapping │
│  live mapping updates   fields added to indices that predate them          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. ism.sh — retention, one policy per family ─────────────────────────────┐
│  alice-application-local-retention        8d                               │
│  alice-application-central-retention      35d                              │
│  alice-infologger-retention         56d                                    │
│  alice-ad-results-retention         14d                                    │
│  alice-alert-history-retention      30d                                    │
│  alice-alert-actions-retention      30d                                    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Where the schema lives

`templates.sh` holds no request body. Every one of them is a file under
`templates/schema/`, rendered to `/opt/alice-ingest/init/schema/` and read back
at run time as `$(dirname "$0")/schema/<name>`. The name prefix is the kind:

| Prefix | Count | What it is |
|---|---|---|
| `component-` | 2 | The two shared log mappings. |
| `index-` | 15 | One index template each. |
| `pipeline-` | 1 | The `alice-add-ingest-time` ingest pipeline. |
| `settings-` | 2 | Persistent cluster settings bodies. |
| `patch-` | 8 | Mapping fragments PUT onto indices that predate a field. |

The script keeps the parts that encode decisions — the seven helper functions
and the ordered sequence of calls — and nothing else. Adding an object is a new
file plus one `load` line; changing a mapping is a diff in one small file
instead of a diff inside a thousand-line script.

`load` fails the whole run if a document is missing, before any REST call, so a
file that is added to the script but not to the directory cannot reach a
cluster half-applied.

### Non-obvious settings — the bootstrap

- **The admission-control mode is checked before anything runs.**
  `AdmissionControlMode.fromName` parses only `disabled`, `monitor_only` and
  `enforced`, and throws on anything else. OpenSearch then answers 400 to the
  whole persistent settings body, which carries the anomaly-detection batch
  pacing down with it and makes `templates.sh` exit non-zero. The assertion names
  the cause; the 400 would not.
- **The per-worker objects are created here, not by the workers.** The control
  host cannot wait for the collectors to start, because the detectors
  provisioned later in the deploy match `application-logs-local-*` and the
  anomaly-detection plugin refuses a detector over an index holding no
  documents. So the bootstrap applies each worker's index template, write alias
  and backing index, and seeds it.
- **The worker's own `register_node.sh` is a self-heal, not the definition.** It
  runs as `ExecStartPre` of `fluent-bit.service` and rebuilds a worker's alias
  and backing index after a reboot that follows a cluster wipe or a disk
  replacement — cases no deploy is present for. It reads the same rendered index
  template this role installs on the worker, so there is one definition of the
  mapping, in `templates/schema-per-worker/`.
- **`action.auto_create_index` forbids the bare log-family names.** Those names
  belong to rollover write aliases. If ingest reaches one while its alias is
  briefly absent, OpenSearch would create a concrete index with a dynamic
  mapping, which blocks the alias permanently and turns `collector_time` into a
  long and `host` into text. Rejecting those writes loses seconds of records and
  is the better outcome.
- **`ism.sh` is the authoritative retention attach and runs after `templates.sh`.**
  `register_node.sh` attaches the same policy opportunistically on a fresh
  cluster; the run here is what makes it true.
- **The three fixed Templates-page indices appear in no ISM policy; the two
  bucket families do.** `template-catalog`, `template-triage` and
  `shifter-queries` never roll over. Their retention is document expiry through
  a bounded delete-by-query, which is the pattern `cockpit-metrics` and
  `trend-rollup` already use. The stamper's bucket documents go into date-named
  indices, `template-buckets-5m-<day>` and `template-buckets-1h-<month>`, that
  an ISM age policy on the pattern deletes; they are not rolled over, because a
  bucket is republished in place while it is inside the worker's ledger and a
  rollover alias would put the republication in a new index beside the old
  document. The age is padded by one index period because `min_index_age`
  counts from creation.
- **Every fixed index gets its template's mapping pushed onto the live index.**
  `ensure_index` skips an index that already exists, so a changed mapping would
  otherwise reach only a cluster that had never held that index. The loop after
  the `ensure_index` calls reads each index template back and PUTs its
  `properties` onto the index itself. `template-catalog` is in that loop because
  it is not a new index and its mapping changed; without the PUT, `last_observed`
  would stay unmapped under `dynamic: false`, the 90-day expiry would match
  nothing and the inactive history would come back empty, both without an error.
- **The `template-catalog` mapping holds three kinds of document.** Template
  definitions (`kind: template`), keyed by the version identifier and upserted
  by every stamper that observed the version; one watermark per node
  (`kind: watermark`); and the check results (`kind: check`). It carries no
  count: volume is a nested aggregation over the hourly bucket documents.
- **The bucket mappings index `versions` flat and `counts` nested.** A search
  expands a version to its descendants and routes by the `versions` keyword;
  the 28-day sum is one nested aggregation on `counts.version_id` and
  `counts.count`. The total beside the counts is what the conservation check
  compares them against.
- **Both log component mappings carry `template_version`, `template_id` and
  `template_status`.** The InfoLogger mapping is `dynamic: strict`, so the
  stamper's fields are not optional there.

#### The shard inventory the Templates-page indices land in

The plan quotes an earlier 45-shard figure and asks for the real one. The 45 is
the two storage-tier log families at one primary and full retention, and it
counts nothing else. `deploy/test_provisioning.py` recomputes the whole
inventory from the role defaults and both inventories, and reproduces both of
this repository's own published numbers: 45 shards for those two families at one
primary, and 135 at three.

A rollover family holds `delete_days // rollover_days + 1` backing indices at
full retention, and each backing index costs `primaries x (1 + replicas)`
shards. The two date-named bucket families follow the same arithmetic with the
index period in place of the rollover period: four day indices of the
five-minute series and three month indices of the hourly series at full
retention, one replica each.

| | `inventory.yml` | `inventory.epn.yml` |
|---|---|---|
| Storage primaries | 1 | 3 |
| Workers | 2 | 3 |
| At first bootstrap, before the Templates page | 33 | 46 |
| At first bootstrap, now | **41** | **54** |
| At full retention, before the Templates page | 92 | 191 |
| At full retention, now | **112** | **211** |

The two fixed new indices add four shards; the bucket families add four at
first bootstrap and sixteen at full retention.

On the farm only the shards pinned by `index.routing.allocation.require.role=storage`
consume storage-tier heap: 179 of the 211 at full retention. The three storage
nodes carry an 8 GB heap each, so at the repository's own rule of roughly 20
shards per gigabyte the budget is about 480 shards. The inventory uses 37
percent of it and the Templates-page indices use 4 percent.

`inventory.yml` is tighter for a reason that predates this change. Its three
storage nodes carry 1 GB heaps, which is a budget of about 60 shards, and the
storage-pinned inventory at full retention is 89 — 69 before the Templates
page. The staging cluster is over that guideline once the 56-day InfoLogger
retention fills, with or without the Templates page. At first bootstrap it is
38 shards and inside the budget.

### Bootstrap variables

| Variable | Default | Meaning |
|---|---|---|
| `opensearch_bootstrap_root` | `/opt/alice-ingest/init` | Where the scripts are staged. Shared — see couplings. |
| `opensearch_bootstrap_templates_script` | `{{ opensearch_bootstrap_root }}/templates.sh` | The rendered index-template script. |
| `opensearch_bootstrap_schema_root` | `{{ opensearch_bootstrap_root }}/schema` | Where the rendered schema documents land. `templates.sh` resolves it relative to itself, so moving one moves both. |
| `opensearch_bootstrap_ism_script` | `{{ opensearch_bootstrap_root }}/ism.sh` | The rendered retention script. |
| `opensearch_bootstrap_worker_node_ids` | `[]` | The worker identities that get a per-node index template, write alias and retention attach. The playbook supplies it. |

### Bootstrap variables it requires but does not own

All from `group_vars/all.yml`, and all read by the two scripts and the schema.

| Group | Variables |
|---|---|
| Connection | `opensearch_http_port` |
| Info tier | `opensearch_info_search_idle_after`, `opensearch_info_translog_sync_interval`, `opensearch_info_merge_threads` |
| Shards and rollover | `log_primary_shards_storage`, `log_rollover_period`, `log_rollover_period_info`, `log_rollover_max_size`, `log_rollover_migrate_existing`, `alert_actions_rollover_period`, `alert_actions_rollover_max_size` |
| Retention | `ism_retention_application_local`, `ism_retention_application_central`, `ism_retention_infologger`, `ism_retention_ad_results`, `ism_retention_alert_history`, `ism_retention_alert_actions`, `ism_retention_template_buckets_5m`, `ism_retention_template_buckets_1h` |
| Cluster settings | `admission_control_mode`, `admission_control_cpu_limit`, `ad_max_batch_task_per_node`, `ad_batch_task_piece_interval_seconds` |
| Index names | `cockpit_metrics_index`, `trend_rollup_index`, `fleet_roster_index`, `lane_state_index`, `signals_index`, `incidents_index`, `notifications_index`, `template_catalog_index`, `template_buckets_5m_prefix`, `template_buckets_1h_prefix`, `template_buckets_replicas`, `template_triage_index`, `shifter_queries_index` |
| Templates page retention | `template_catalog_active_days`, `template_catalog_definition_retention_days`, `stamper_ledger_hours` |

### Bootstrap couplings

- **`opensearch_bootstrap_root` is shared with the `alice_runtime` role.** Both
  roles create the directory, with the same owner, group and mode, and each
  writes its own scripts into it; `dashboards`, `alerting_monitors` and
  `anomaly_detection` only write into it. `alice_runtime` also stages a
  world-readable signal catalog there for the `DynamicUser` services, which is
  why the directory is `0755` and the scripts inside it are `0750`.
- **`alice_ops_templates_script` must match
  `opensearch_bootstrap_templates_script`.** The ops page re-applies the index
  templates through `alice-ops.service`. That unit is written by the `alice_ops`
  role, which holds the path as a literal: a default reading another role's
  variable resolves lazily and would make `alice_ops` unrunnable alone.
- **`tools/soak/mkbootstrap.py` renders the same schema for the soak rig.** It
  writes `schema/` beside the script it produces and forces `number_of_replicas`
  there, which is the rig's one stated divergence from production.
- **`playbooks/replay.yml` also runs `templates.sh`.** It carries the path as the
  play variable `bootstrap_root`, with `SEED_EMPTY_INDICES=false`, to rebuild the
  write aliases after a fresh replay. Moving the staging directory means changing
  it there too.
- **Retention policy names are literals in `ism.sh.j2`.** `verify_detection.py`
  asserts them by name and `register_node.sh` falls back to
  `alice-application-local-retention`. A variable would only let one end of the set
  move.

### What is frozen in the bootstrap

The domain layer is not parameterised. These scripts are the schema.

- The field mappings under `templates/schema/`, both component templates and
  every `patch-` mapping update.
- The ingest pipeline `alice-add-ingest-time`.
- The index and alias names, which are matched by the index templates, the
  detectors, the monitors and the cockpit.
- The ISM state machines in `ism.sh.j2`. Only their ages and sizes are variables.

## Used by

- `playbooks/site.yml`, play "OpenSearch cluster — initial bring-up", against
  `alice_nodes` — the node mode.
- `playbooks/site.yml`, play "OpenSearch cluster bootstrap", against `control`
  with `opensearch_cluster_bootstrap: true` — the bootstrap mode. It must run
  after the rolling-safety gate between the two plays, and before `dashboards`,
  `alerting_monitors` and `anomaly_detection`.

