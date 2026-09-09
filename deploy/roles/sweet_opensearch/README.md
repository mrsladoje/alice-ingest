# `sweet_opensearch`

Builds the ALICE log cluster on the EPN farm: one OpenSearch cluster,
`alice-logs`, with a data node on every EPN machine and a three-node storage
tier behind them. First it installs and configures the OpenSearch nodes each
machine carries. Then, from the control host, it loads the schema into the
running cluster: the index templates, the ingest pipeline, the pre-created
indices and the retention policies.

## What the cluster looks like

The cluster has two tiers. Which tier a node is in comes from `node_tier` in
the inventory.

- **Worker nodes** (group `workers`): every EPN machine, 200 and more. Each one
  runs a small OpenSearch node that holds exactly one index,
  `application-logs-local-<node_id>`, with no replicas, pinned to that machine.
  The collector on the same machine writes to it over localhost, so the
  firehose never crosses the network and a worker's failure loses only its own
  local logs. Workers are not cluster-manager eligible.
- **Storage nodes** (group `storage`): three nodes, the durable part. They
  elect the cluster manager and hold the replicated indices: the central
  application log index, the InfoLogger index, and every derived index the
  cockpit, the detectors and the alerting write. Each has two replicas and is
  pinned to the storage tier.
- **The control host** (group `control`): one of the storage nodes. The schema
  is loaded from it, and Dashboards and the services run there.

```
        WORKERS — every EPN machine                STORAGE — 3 nodes, quorum

   ┌─ epn001 ───────────────────────┐        ┌─ node-04  = control ──────────┐
   │  collector ──localhost──>      │        │  cluster_manager, data, ingest │
   │  application-logs-local-epn001 │        │  application-logs-central-*    │
   │  data, ingest   (0 replicas,   │        │  infologger-*                  │
   │  pinned: require.box=epn001)   │        │  cockpit-metrics, signals, ... │
   └────────────────────────────────┘        ├─ node-05 ─────────────────────┤
   ┌─ epn002 ───────────────────────┐        │  same, replica copies         │
   │  same, for epn002              │        ├─ node-06 ─────────────────────┤
   └────────────────────────────────┘        │  same, replica copies         │
              ...                            └───────────────────────────────┘
   ┌─ epnNNN ───────────────────────┐          one machine, three containers
   │  same, for epnNNN              │        every index on the right:
   └────────────────────────────────┘        require.role=storage, 2 replicas
```

**Two ways to install a node**, chosen per group by `opensearch_install_method`:

| | `native` | `container` |
|---|---|---|
| What | the vendor RPM | a podman container |
| Nodes per machine | one | several, listed in `opensearch_instances` |
| Used for | every worker | the three storage nodes, all on one machine |

Three storage containers on one machine give a real quorum of three, not
fault tolerance. Staging runs the same layout on OpenStack: three worker VMs
and one VM with the three storage containers. Moving the storage tier onto
three machines is an inventory change only.

## The two modes, and the order to run them in

The role has two modes, and a host does exactly one of them per play.
**Install** puts OpenSearch on a machine and starts it. **Configure the
cluster** applies the cluster-wide state to the running cluster.
`opensearch_configure_cluster` picks the mode; the default is install.

```
 play 1  hosts: alice_nodes        sweet_opensearch, install
         every machine at once, so the storage nodes can elect a manager together

 play 2  hosts: alice_nodes        rolling health gate, serial: 1
         waits for each instance's API, then for cluster health

 play 3  hosts: control            sweet_opensearch, opensearch_configure_cluster: true
         configures the cluster that now answers
```

```yaml
- name: OpenSearch cluster
  hosts: alice_nodes
  become: true
  roles:
    - sweet_opensearch

- name: OpenSearch rolling gate
  hosts: alice_nodes
  become: true
  serial: 1
  tasks:
    - name: Wait for every instance on this machine to answer and the cluster to report a status
      ansible.builtin.uri:
        url: "http://localhost:{{ item.http_port }}/_cluster/health?timeout=5s"
      register: _gate
      until: _gate.status == 200 and _gate.json.status in ['red', 'yellow', 'green']
      retries: 60
      delay: 5
      loop: "{{ opensearch_instances }}"

- name: OpenSearch cluster configuration
  hosts: control
  become: true
  roles:
    - role: sweet_opensearch
      opensearch_configure_cluster: true
```

- **Run install against the whole cluster in one play.** A fresh cluster
  needs its cluster-manager-eligible nodes reachable at the same time, or the
  first election never completes. A machine installs every node it lists in
  `opensearch_instances` in that one run; the storage machine lists three.
- **The two modes cannot share a play.** Configuring the cluster needs a
  cluster that already answers, which is not true while the nodes are still
  coming up. The `serial: 1` gate between them is also what makes a rolling
  restart safe.
- **The role is idempotent.** It restarts an instance only when its
  `opensearch.yml`, its heap options, its quadlet unit or the RPM's unit
  drop-in changed. Configuring the cluster is safe on every deploy.

## Requirements

The role does not prepare the machine. It assumes two things about it, and
provides neither.

| Prerequisite | What breaks without it |
|---|---|
| `firewalld` installed and running, when `alice_manage_firewalld` is true | The two firewall tasks fail. `ansible.posix.firewalld` needs the daemon up to apply an immediate rule. The EPN farm sets the flag false and the tasks are skipped. |
| Swap in place | On a node that also carries the control plane, the kernel selects the OpenSearch JVM when memory runs out. |

Collections: `ansible.posix`.

## Dependencies

None. No role includes or reads from another.

## Install

Installs and configures the OpenSearch nodes one machine carries. As a
signed RPM when the machine carries one node, or as podman containers when it
carries several, chosen by `opensearch_install_method`. The machine-wide
steps run once; everything from the firewall down runs once per entry of
`opensearch_instances`.

```
                      EVERY MACHINE IN alice_nodes

┌─ 0. KERNEL ────────────────────────────────────────────────────────────────┐
│  vm.max_map_count = 262144   the bootstrap-check minimum, live + sysctl.d  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 1a. INSTALL, native — signed RPM, version-pinned ─────────────────────────┐
│  yum_repository             artifacts.opensearch.org, gpgcheck on          │
│  rpm_key                    signing key into the rpm keyring               │
│  dnf install                opensearch-{{ opensearch_version }}            │
│  DISABLE_INSTALL_DEMO_CONFIG   suppresses the demo security material       │
│  resource-limits.conf       rlimits, cpuset, memory on the unit --> restart│
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 1b. INSTALL, container — the runtime, once ───────────────────────────────┐
│  dnf install podman         and assert it is 4.4 or newer, for quadlet     │
│  /etc/containers/systemd    the quadlet directory                          │
│  podman pull                opensearchproject/opensearch:{{ version }}     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. SHARED FILES ──────────────────────────────────────────────────────────┐
│  /etc/sweet                 0755, root — shared with the collector         │
│  opensearch-node.env        what the worker's boot-time self-heal reads    │
│  workers only:                                                             │
│    local-index-template.json  this worker's own index template, rendered   │
│    register_node.sh           the boot-time self-heal fluent-bit.service   │
│                               runs as ExecStartPre                         │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
╔═ FOR EACH INSTANCE in opensearch_instances ════════════════════════════════╗
║                                                                            ║
║ ┌─ 3. FIREWALL — cluster members only, never the world ──────────────────┐ ║
║ │  http_port/tcp        rich rule per address in opensearch_cluster_hosts│ ║
║ │  transport_port/tcp   rich rule per address in opensearch_cluster_hosts│ ║
║ │  both skipped when alice_manage_firewalld is false (the EPN farm)      │ ║
║ └──────────────────────────────────┬─────────────────────────────────────┘ ║
║                                    v                                       ║
║ ┌─ 4. UNIT, container only ──────────────────────────────────────────────┐ ║
║ │  /etc/opensearch/<id>   this instance's own configuration directory    │ ║
║ │  opensearch-<id>.container   quadlet unit; cpuset, memory  --> restart │ ║
║ │  Network=host           distinct http.port/transport.port per instance │ ║
║ │  --ulimit memlock=-1    the same limit the RPM path sets on the unit   │ ║
║ └──────────────────────────────────┬─────────────────────────────────────┘ ║
║                                    v                                       ║
║ ┌─ 5. DIRECTORIES AND CONFIGURATION ─────────────────────────────────────┐ ║
║ │  /var/lib/opensearch[/<id>]   0750, owned by opensearch, or uid 1000   │ ║
║ │  /var/log/opensearch[/<id>]   0750, the same                           │ ║
║ │  opensearch.yml         identity, tier, discovery, ports  --> restart  │ ║
║ │  jvm.options.d/heap.options   -Xms and -Xmx               --> restart  │ ║
║ └──────────────────────────────────┬─────────────────────────────────────┘ ║
║                                    v                                       ║
║ ┌─ 6. START, then PROVE ─────────────────────────────────────────────────┐ ║
║ │  daemon-reload, enable, start   restart instead when 1a, 4 or 5 changed│ ║
║ │  wait for localhost:http_port   60 attempts, 5 s apart — 5 minutes     │ ║
║ │  opensearch-plugin list         asserts all 7 required plugins         │ ║
║ │  anomaly-detection API          asserts 200, or 404 for a config index │ ║
║ │                                 the first detector has yet to create   │ ║
║ └────────────────────────────────────────────────────────────────────────┘ ║
╚════════════════════════════════════════════════════════════════════════════╝
```

### What the tier changes in `opensearch.yml`

| | Storage nodes | Worker nodes |
|---|---|---|
| `node.roles` | `cluster_manager, data, ingest` | `data, ingest` |
| `node.attr.role` | `storage` | `worker` |
| `node.attr.box` | not set | the node's `node_id` |
| `node.processors` | not set | `min(opensearch_worker_processors, ansible_processor_vcpus)` |
| `indices.memory.index_buffer_size` | default | `5%` |

The index templates match on the two attributes. `require.role=storage` keeps
the replicated indices on the storage tier; `require.box=<node_id>` keeps each
worker's local index on the worker that produced it.

### Install variables

Defaults are in `defaults/main.yml`. Override site-wide in `group_vars`, or per
group or host in the inventory.

```yaml
opensearch_version: "3.7.0"
opensearch_install_method: native
```

The version is declared here **and** in `group_vars`, because two defaults
interpolate it and a role must run on its own defaults. `group_vars` outranks
the default and is the site value, shared with the `sweet_os_dashboards` role so both
products stay on one version. `native` installs the RPM: one node per machine.
`container` runs podman instances: several nodes on one machine, since a
second RPM cannot give a second service, data directory and port pair.

```yaml
opensearch_instance_id: default
opensearch_http_port: 9200
opensearch_transport_port: 9300
opensearch_instances:
  - id: "{{ opensearch_instance_id }}"
    http_port: "{{ opensearch_http_port }}"
    transport_port: "{{ opensearch_transport_port }}"
```

The nodes this machine carries. Each entry is one OpenSearch node: `id` is its
`node.name` and names its container, unit and directories; the two ports are
its own. The default is one node on the host-level ports, which is every
worker; `group_vars` sets the instance identity to `node_id`. The storage
machine lists three:

```yaml
opensearch_http_port: 9201
opensearch_instances:
  - { id: node-04, http_port: 9201, transport_port: 9301 }
  - { id: node-05, http_port: 9202, transport_port: 9302 }
  - { id: node-06, http_port: 9203, transport_port: 9303 }
```

The host-level `opensearch_http_port` stays the port other services connect
to on that machine, and the one the cluster configuration talks to.

```yaml
opensearch_yum_repo_baseurl: "https://artifacts.opensearch.org/releases/bundle/opensearch/{{ opensearch_version.split('.')[0] }}.x/yum"
opensearch_yum_repo_gpgkey: "https://artifacts.opensearch.org/publickeys/opensearch-release.pgp"
opensearch_package: "opensearch-{{ opensearch_version }}"
opensearch_home: /usr/share/opensearch
opensearch_user: opensearch
opensearch_group: opensearch
```

The native path.

```yaml
opensearch_container_image: "docker.io/opensearchproject/opensearch:{{ opensearch_version }}"
opensearch_container_uid: 1000
opensearch_container_gid: 1000
opensearch_container_runtime_packages: [podman]
opensearch_container_min_podman_version: "4.4"
opensearch_quadlet_dir: /etc/containers/systemd
opensearch_container_start_timeout_seconds: 900
```

The container path. Quadlet arrived in podman 4.4; below it the run stops with
the reason. The uid is the image's own user and owns the data and log
directories. The start timeout allows a first pull and unpack.

```yaml
opensearch_cluster_hosts: []
opensearch_seed_hosts: []
opensearch_initial_cluster_manager_nodes: []
opensearch_publish_host: "{{ ansible_host | default(inventory_hostname) }}"
opensearch_network_host: [_local_]
opensearch_security_disabled: true
```

Cluster identity. The playbook supplies the three lists; the role names no
inventory group. `opensearch_seed_hosts` carries `address:port`, because three
nodes on one machine share one IP. The publish host is `ansible_host` when the
inventory sets one and the inventory name otherwise, which on the farm is the
machine's DNS name; it is always appended to the bind addresses. With the
security plugin disabled every port this role opens is unauthenticated, and
the firewall rules, which name the cluster addresses, are the only boundary.

```yaml
opensearch_heap_size: "1g"
opensearch_worker_heap_size: "1g"
opensearch_worker_processors: 4
opensearch_worker_index_buffer_size: "5%"
opensearch_limit_nofile: 65536
opensearch_cpuset: ""
opensearch_memory_high: ""
opensearch_memory_max: ""
opensearch_vm_max_map_count: 262144
```

Resources. `opensearch_heap_size` is deliberately a role default and not a
`group_vars` entry, so that a group assignment in the inventory file wins.
`opensearch_worker_heap_size` is read by nobody; it records the measured farm
worker value. The cpuset and the two memory bounds land on every instance's
unit on both install paths; empty means none. Keep `MemoryMax` well above the
heap, or the JVM cannot start. `vm.max_map_count` is the bootstrap-check
minimum, not a tuning knob.

```yaml
opensearch_boot_wait_retries: 60
opensearch_boot_wait_delay: 5
opensearch_ad_api_retries: 10
opensearch_ad_api_delay: 3
opensearch_required_plugins: [opensearch-anomaly-detection, opensearch-alerting, opensearch-notifications, opensearch-notifications-core, opensearch-job-scheduler, opensearch-index-management, opensearch-sql]
```

Start-up checks. Sixty attempts at five seconds is five minutes for a first
boot. The plugin list is a gate, not a preference.

```yaml
opensearch_node_env_dir: /etc/sweet
opensearch_node_env_file: "{{ opensearch_node_env_dir }}/opensearch-node.env"
opensearch_register_script: /opt/sweet/register_node.sh
opensearch_register_script_dir: /opt/sweet
opensearch_local_index_template_file: "{{ opensearch_node_env_dir }}/local-index-template.json"
opensearch_info_search_idle_after: "10s"
opensearch_info_translog_sync_interval: "30s"
opensearch_info_merge_threads: 1
alice_manage_firewalld: true
```

Files shared with the collector. `sweet_collector` loads the env file and names
the script as `ExecStartPre`; this role installs both. The three info-tier
settings are rendered into every worker's local index template here and
passed to `templates.sh` by the other mode, so both ends come from one place.
`alice_manage_firewalld` is repeated in every role that writes a firewalld
rule, so one inventory line switches the whole stack.

### Derived per instance

Everything below follows from the install method and the instance being
installed. `opensearch_instance` is the current entry of `opensearch_instances`.

| Name | Native | Container |
|---|---|---|
| `opensearch_service_name` | `opensearch` | `opensearch-<id>` |
| `opensearch_service_enabled` | true | false, see couplings |
| `opensearch_config_dir` | `/etc/opensearch` | `/etc/opensearch/<id>` |
| `opensearch_data_path` | `/var/lib/opensearch` | `/var/lib/opensearch/<id>` |
| `opensearch_log_path` | `/var/log/opensearch` | `/var/log/opensearch/<id>` |
| `opensearch_path_data_setting` | the host path | `/usr/share/opensearch/data`, inside the container |
| `opensearch_dir_owner` | `opensearch` | `1000` |
| `opensearch_plugin_list_cmd` | `opensearch-plugin list` | the same, through `podman exec` |

Three containers on one machine give three services and a real quorum of three.
They give no fault tolerance: the machine is one failure domain. The layout
exists so the tier design stays byte-identical to the multi-machine one.

### Variables the role requires but does not own

Site-wide, and deliberately not duplicated into the defaults.

| Variable | Owner | Used for |
|---|---|---|
| `opensearch_cluster_name` | `group_vars` | `cluster.name`. |
| `node_id` | inventory, per host | The default instance identity through `group_vars`, and on a worker `node.attr.box` and the local index template. |
| `node_tier` | inventory, per group | Selects the storage or worker branch. |
| `ansible_host` | inventory, per host, optional | Bind address and `network.publish_host` when set. |
| `ansible_processor_vcpus` | gathered fact | The second operand of the `node.processors` `min`. |

## Configure the cluster

Applies the cluster-wide state to the running cluster: the ingest pipeline,
the templates, the cluster settings, the pre-created indices and aliases, and
the retention policies. It writes no machine configuration and starts no
service. It talks only to the local REST API, every call is idempotent, and it
runs on every deploy. It runs on the control host, but everything it creates
is cluster-wide.

```
                    CONTROL HOST, ONCE PER DEPLOY

┌─ 1. GUARDS — fail here, not at the REST call ──────────────────────────────┐
│  admission_control_mode        must parse, or the settings PUT is a 400    │
│  worker node identity list     must not be empty, or there is no info tier │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. STAGE — /opt/sweet/init ───────────────────────────────────────────────┐
│  templates.sh, ism.sh   rendered from the .j2 of the same name             │
│  schema/*.json          28 documents, plus one per worker rendered from    │
│                         templates/schema-per-worker/                       │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. templates.sh — waits for cluster health, then applies ─────────────────┐
│  ingest pipeline        alice-add-ingest-time                              │
│  component templates    generic mappings, infologger mappings              │
│  index templates        15, one per log family and derived index           │
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

`templates.sh` holds no request body. Each one is a file under
`templates/schema/`, rendered beside the script and read back as
`$(dirname "$0")/schema/<name>`. Adding an object is a new file plus one `load`
line, and `load` fails the run before any REST call if a file is missing.

| Prefix | Count | What it is |
|---|---|---|
| `component-` | 2 | The two shared log mappings. |
| `index-` | 15 | One index template each. |
| `pipeline-` | 1 | The `alice-add-ingest-time` ingest pipeline. |
| `settings-` | 2 | Persistent cluster settings bodies. |
| `patch-` | 8 | Mapping fragments PUT onto indices that predate a field. |

### Three things that are not obvious

- **The per-worker index, alias and retention are created here, not by the
  workers.** The worker's own `register_node.sh` is only a boot-time self-heal
  after a cluster wipe or a disk swap; it reads the same rendered template.
- **`action.auto_create_index` forbids the bare log-family names.** They are
  rollover write aliases. An index created under one by a stray write would
  block the alias for good, so those writes are rejected instead.
- **`ism.sh` runs after `templates.sh` and is the authoritative retention
  attach.** Both scripts are idempotent and run on every deploy.

### Configure-the-cluster variables

```yaml
opensearch_configure_cluster: false
opensearch_cluster_config_root: /opt/sweet/init
opensearch_cluster_config_templates_script: "{{ opensearch_cluster_config_root }}/templates.sh"
opensearch_cluster_config_schema_root: "{{ opensearch_cluster_config_root }}/schema"
opensearch_cluster_config_ism_script: "{{ opensearch_cluster_config_root }}/ism.sh"
opensearch_cluster_config_worker_node_ids: []
```

`templates.sh` resolves the schema directory relative to itself, so moving one
moves both. The playbook supplies the worker roster; an empty list configures a
cluster with no info tier, which the guard rejects.

```yaml
admission_control_mode: monitor_only
admission_control_cpu_limit: 95
ad_max_batch_task_per_node: 2
ad_batch_task_piece_interval_seconds: 10
log_primary_shards_storage: 1
log_rollover_period: "7d"
log_rollover_period_info: "1d"
log_rollover_max_size: "20gb"
log_rollover_migrate_existing: false
ism_retention_application_local: "8d"
ism_retention_application_central: "35d"
ism_retention_infologger: "56d"
ism_retention_ad_results: "14d"
ism_retention_alert_history: "30d"
ism_retention_alert_actions: "30d"
alert_actions_rollover_period: "7d"
alert_actions_rollover_max_size: "1gb"
template_buckets_5m_prefix: template-buckets-5m
template_buckets_replicas: 1
ism_retention_template_buckets_5m: "4d"
ism_retention_template_buckets_1h: "66d"
```

The cluster tunables, read by nothing outside this role. The farm sets
`admission_control_mode: enforced` and `log_primary_shards_storage` to the
storage node count, in the inventory. `log_rollover_period_info` is the one
value here that breaks the design at farm scale if shortened: 200 workers are
already 1600 indices at one day.

Required from `group_vars`, read by the two scripts and the schema:

| Group | Variables |
|---|---|
| Connection | `opensearch_http_port` |
| Index names | `cockpit_metrics_index`, `trend_rollup_index`, `fleet_roster_index`, `lane_state_index`, `signals_index`, `incidents_index`, `notifications_index`, `template_catalog_index`, `template_buckets_1h_prefix`, `template_triage_index`, `shifter_queries_index` |
| Templates page retention | `template_catalog_active_days`, `template_catalog_definition_retention_days`, `stamper_ledger_hours` |

### Configure-the-cluster couplings

- **`opensearch_cluster_config_root` is shared with the alice-service roles.**
  Each creates the directory with the same owner, group and mode. It is `0755`
  because `sweet_anomaly_detection` and `sweet_signal_projector` stage a
  world-readable signal catalog there.
- **`alice_ops_templates_script` must match
  `opensearch_cluster_config_templates_script`.** The `alice_ops` role holds the path
  as a literal, so it can run alone.
- **Retention policy names are literals in `ism.sh.j2`.** See the same coupling
  under Couplings below.

### What is frozen in the cluster configuration

These scripts are the schema.

- The field mappings under `templates/schema/`, including every `patch-`.
- The ingest pipeline `alice-add-ingest-time`.
- The index and alias names. The detectors, monitors and cockpit match on them.
- The ISM state machines in `ism.sh.j2`. Only their ages and sizes are variables.

## Couplings

- **`bootstrap.memory_lock` and `LimitMEMLOCK` change together.** They live in
  `opensearch.yml.j2` and `resource-limits.conf.j2`.
- **Do not add `opensearch_heap_size` to `group_vars`.** Inventory
  group variables rank below `group_vars`, so the `workers` assignment would
  silently stop winning.
- **`vm.max_map_count` is set by this role.** If a site baseline role sets it
  too, keep the two values equal; whichever runs last wins.
- **`opensearch_version` is shared with `sweet_os_dashboards`.** Change it in
  `group_vars`.
- **The retention policy name in `opensearch-node.env.j2` is a literal.**
  `ism.sh.j2` creates it, `verify_detection.py` asserts it and
  `register_node.sh` falls back to it. A variable would let one end move.
- **The host-level ports are declared in `group_vars` as well as here.** The
  seed-host list reads them out of `hostvars`, which a role default never
  reaches. A machine with several nodes lists them in `opensearch_instances`.
- **The container path is not enabled by systemd.** Quadlet generates the unit
  at `daemon-reload` and its `[Install]` section does the enabling, so
  `opensearch_service_enabled` is false there and the role only starts it.
- **`opensearch_cluster_hosts` only ever adds.** firewalld keeps a permanent
  rule; removing an address does not close the port on a node that already ran.
- **`/etc/sweet` has no single owner.** This role and `sweet_collector`
  both create it with the same owner, group and mode, because this role also
  runs on storage nodes where the collector never does.
- **A change to `opensearch-node.env` does not restart the collector.** That
  unit belongs to another role in another play. The cluster configuration
  re-applies every worker's template on each deploy, so the cluster converges.

## What is frozen

The tier design is not parameterised. A cluster whose node roles are a variable
is a cluster with no design.

- The two `node.roles` lists and the `node_tier == 'storage'` branch.
- `node.attr.role` and `node.attr.box`. The index templates match on both.
- `opensearch_required_plugins` as a set.

## Upstream roles rejected

Checked in August 2026, so the question is not reopened at each review.

| Candidate | Why rejected |
|---|---|
| [`opensearch-project/ansible-playbook`](https://github.com/opensearch-project/ansible-playbook), the vendor's, maintained | A playbook, not a Galaxy role ([issue #44](https://github.com/opensearch-project/ansible-playbook/issues/44)); reads `groups['os-cluster']` directly; built around the security plugin this cluster disables; tarball install, AlmaLinux 9 unsupported. |
| [`bbaassssiiee.opensearch`](https://galaxy.ansible.com/ui/repo/published/bbaassssiiee/opensearch/) | Single maintainer, last moved October 2025; cannot express the two-tier `node.roles` split. |
| [`dimMaryanto93.opensearch`](https://galaxy.ansible.com/ui/standalone/roles/dimMaryanto93/opensearch/) | Last updated July 2024. |
| Other GitHub results | 0 or 1 stars, last moved 2021 to 2025. |

None of them could hold the tier design, the info-tier settings published to
each node, or the plugin gate, which is most of this role. Re-open the decision
if the OpenSearch project publishes its playbook as a Galaxy role.
