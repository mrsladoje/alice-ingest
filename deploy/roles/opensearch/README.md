# `opensearch`

Installs one OpenSearch node and joins it to the `alice-logs` cluster. It opens
the two cluster ports to the other nodes, installs the version-pinned build,
writes the node's identity and tier into `opensearch.yml`, caps the heap, starts
the service, and proves that the detection plugins answer.

It installs **either** the vendor RPM **or** a podman container, chosen by
`opensearch_install_method`. One node per machine is an RPM. Several nodes on
one machine are containers, because a second RPM install cannot give a second
service, data directory and port pair on the same host.

The role does not create indices, index templates, retention policies or the
ingest pipeline. The `opensearch_bootstrap` role does that, once, on the control
host.

It runs on every node in one play, so that a storage-tier node can be elected
cluster manager while the whole cluster comes up together. "Node" means an
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
│  ulimits-override.conf      LimitMEMLOCK, LimitNOFILE on the vendor unit   │
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
│  ulimits-override.conf      LimitMEMLOCK, LimitNOFILE         --> restart  │
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
| `opensearch_data_path` | `/var/lib/opensearch` | `path.data`. |
| `opensearch_log_path` | `/var/log/opensearch` | `path.logs`. |
| `opensearch_heap_size` | `1g` | `-Xms` and `-Xmx`. Declared here on purpose — see couplings. |
| `opensearch_worker_heap_size` | `2g` | Reference only. Read by nobody. The farm value for the line above. |
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
| `opensearch_jvm_options_d` | `/etc/opensearch/jvm.options.d` | Holds `heap.options`. |
| `opensearch_systemd_dropin_dir` | `/etc/systemd/system/opensearch.service.d` | Holds `ulimits-override.conf`. |
| `opensearch_node_env_dir` | `/etc/alice-ingest` | Shared with the `collector` role. |
| `opensearch_node_env_file` | `/etc/alice-ingest/opensearch-node.env` | The info-tier index settings. See below. |
| `opensearch_required_plugins` | 7 names | Asserted present after start. Not a setting — a gate. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `opensearch_cluster_name` | `group_vars/all.yml` | `cluster.name`. `cluster_id` derives from the same name. |
| `opensearch_http_port` | `group_vars/all.yml` | The REST port. The collector, the bootstrap and every service read it too. |
| `opensearch_info_search_idle_after` | `group_vars/all.yml` | Written into `opensearch-node.env`. Shared with `opensearch_bootstrap`. |
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
| `vm.max_map_count` at least 262144 | `common` role | OpenSearch fails its own bootstrap check and the service never starts. |
| Swap in place | `common` role | On a node that also carries the control plane, the kernel selects the OpenSearch JVM when memory runs out. |

## How to use it

In a playbook, against every node in the cluster:

```yaml
- name: OpenSearch cluster
  hosts: alice_nodes
  become: true
  roles:
    - opensearch
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
  two files, `opensearch.yml.j2` and `ulimits-override.conf.j2`. Locking memory
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
  `ism.sh.j2` in `opensearch_bootstrap` creates the policy under that exact name,
  `verify_detection.py` asserts it, and `register_node.sh` falls back to the same
  literal. A variable here would only let one end of the set move.
- **`opensearch_seed_hosts` carries ports, not bare addresses.** Three nodes on
  one machine share one IP, and discovery can only tell them apart by transport
  port. `group_vars/all.yml` builds the list as `address:port`, reading each
  host's `opensearch_transport_port` out of `hostvars` — which is why both ports
  are declared there and not only in this role's defaults. A role default never
  reaches `hostvars`.
- **`opensearch_quadlet_dir` must equal `container_host_quadlet_dir`.** The
  `container_host` role creates that directory; this role writes
  `opensearch-<node_id>.container` into it.
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
  retention policies.** The `opensearch_bootstrap` role does, once, on the
  control host.
- **It does not install OpenSearch Dashboards.** The `dashboards` role does.
- **It does not own `/etc/alice-ingest`.** Both this role and `collector` create
  it, deliberately: the directory has no single owner, each role writes its own
  file into it, and this role also runs on storage nodes where the collector
  never does. Both use the same owner, group and mode, so the two cannot drift.
- **It does not restart the collector when the info-tier settings change.**
  `opensearch-node.env` is loaded by `fluent-bit.service`, but that unit belongs
  to another role in another play. The control host re-runs `register_node.sh`
  for every worker on each deploy, so the cluster converges; the worker's own
  copy applies at its next boot.

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

## Used by

- `playbooks/site.yml`, play "OpenSearch cluster — initial bring-up", against
  `alice_nodes` — the only caller.
