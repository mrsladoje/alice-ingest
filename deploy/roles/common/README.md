# `common`

Prepares a bare Alma 9 VM so that every other role in this tree can assume a
working host. It creates the swap file, sets the two kernel parameters
OpenSearch needs, installs the baseline packages, fixes the clock, creates the
local log directory tree, and starts firewalld.

It runs on all five nodes, as the first play in `site.yml` that touches them.

## Why this is a role and not tasks in `site.yml`

Every other role depends on the state this one leaves behind, and three of those
dependencies are silent — a service starts, then fails minutes later for a
reason that points somewhere else:

- **firewalld must already be running.** `opensearch`, `collector`, `producer`,
  `faults`, `alertmanager` and `dashboards/livelane.yml` each add their own
  rules. `ansible.posix.firewalld` needs the daemon up to apply an immediate
  rule, so every one of them assumes this role ran first.
- **`vm.max_map_count` must already be 262144.** OpenSearch refuses to start
  below it. A node that skipped this role produces a bootstrap-check failure in
  the `opensearch` role, not here.
- **The swap file must already exist.** The pre-flight in `site.yml` says so
  outright: the memory-heavy services stay stopped "until the roles below
  restart them, which happens only after the swap file and the memory slice are
  in place."

Holding this in a role keeps the ordering visible as one line in `site.yml`
(`roles: [common]`) instead of thirty tasks a reader has to scroll past.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `common_packages` | `python3`, `python3-pip`, `tar`, `chrony`, `firewalld`, `ca-certificates` | The baseline every node needs. `python3` and `python3-pip` are here for the Python services, `tar` for the DDS tarball replay, `chrony` and `firewalld` because two later tasks start them, `ca-certificates` for the S3 and OpenSearch download paths. |
| `common_dnf_timeout` | `600` | Seconds allowed for the `dnf` transaction, as the `async` bound. See "The dnf block" below. |
| `common_timezone` | `UTC` | System timezone. Do not change it. Every timestamp in this stack is UTC, and the replay's two-clock model compares stamps taken on different hosts. |
| `common_vm_max_map_count` | `262144` | OpenSearch's required minimum. Not a tuning knob — it is the value the bootstrap check tests for. |
| `common_sysctl_conf_file` | `/etc/sysctl.d/99-opensearch.conf` | Where both sysctl values are persisted. Read the coupling note below before changing it. |
| `common_swapfile_path` | `/swapfile` | Swap file location. Also the string matched against `swapon --show`, so it must be the path as the kernel reports it. |
| `common_swapfile_size_mb` | `2048` | Swap file size in mebibytes. 2 GB on a 3.66 GB node. |
| `common_swappiness` | `60` | `vm.swappiness`. This looks wrong for a server. It is deliberate — see "Why swappiness is 60" below. |
| `log_root` | `/var/log/node` | Root of the local log tree. Declared here so the role runs standalone; `group_vars/all.yml` sets the same value for the whole tree and wins on precedence. |
| `common_log_dirs` | `log_root`, `log_root/dds`, `log_root/stdout` | The directories to create. `dds` and `stdout` are the two families the producers write locally before Fluent Bit tails them. |
| `common_open_tcp_ports` | `[]` | TCP ports to open to any source on this host. Empty by default; the playbook supplies the list. |

## Why swappiness is 60

A reviewer will read `vm.swappiness: 60` on a log-ingest node, decide it is a
copy-paste default, and lower it. It was 1, and 1 broke the deploy.

At `vm.swappiness=1` the kernel reclaims page cache before it swaps anonymous
memory, so under pressure it evicted executable pages — sshd's among them —
rather than use the 2 GB swap file. The node then could not complete a login,
which surfaced as `connection timed out during banner exchange` and killed the
run. `60` lets idle anonymous memory move to swap and keeps resident text pages
resident. OpenSearch's own heap is `mlockall`'d, so it is never a swap candidate
at any swappiness.

The swap file itself exists because the kernel killed OpenSearch on
`alice-ingest-3` (1.75 GB anonymous resident set) and the node stayed down for
three days. Swap here is an out-of-memory guard, not a storage tier.

Note the honest limit: swap converts an out-of-memory kill into thrashing, which
is slower to diagnose. That is why `alice-ingest-3` also carries a reduced
OpenSearch heap as a host variable in `inventory.yml`, and why the analytics
services were moved off the control host. Swap is the last line, not the fix.

## Why `dd` and not `fallocate`

`swapon` rejects the unwritten extents `fallocate` leaves on XFS, and the VM
images are XFS. `fallocate` is faster and would appear to work — `mkswap`
accepts the file — and then `swapon` fails.

## The dnf block

Three settings on one task, each for a stated reason:

- `update_cache: false` — `dnf` fetches metadata by itself whenever it must
  resolve a missing package. Forcing a refresh on every run buys a network round
  trip and a resident `dnf` process near 700 MB on a node that has none to
  spare. A `dnf` run at that size was resident when the kernel chose its victim
  in the incident above.
- `lock_timeout: 60` — an interrupted previous run can leave an rpm transaction
  lock behind.
- `async: common_dnf_timeout` with `poll: 5` — bounds a stuck transaction
  instead of waiting forever.

The `rescue` block exists because the failure is misleading. Every baseline
package is normally installed already, so this task should return in seconds. A
stall means the node is starved for memory or holds a stale rpm lock, and the
rescue says exactly that instead of printing a `dnf` timeout.

## Couplings

**`common_sysctl_conf_file` does not migrate.** One file carries both
`vm.swappiness` and `vm.max_map_count`. Change the variable and Ansible writes
the new file, but the old `/etc/sysctl.d/99-opensearch.conf` stays on disk and
keeps applying at every boot. `sysctl.d` files load in lexical order, so which
value wins depends on the two filenames. Changing this variable means deleting
the old file by hand, on every node.

**`common_vm_max_map_count` belongs to OpenSearch.** It is set here because it
must be in place before the `opensearch` role runs, but the value is OpenSearch's
bootstrap-check minimum. Lowering it breaks the `opensearch` role, not this one.

**`common_swapfile_path` is used twice, differently.** Once as a path to create,
once as a string matched against `swapon --show=NAME` output to decide whether
the swap area is already active. A path the kernel normalises differently would
make the activation task run on every pass.

## What this role no longer does

Three firewalld rules used to live here. They moved to the roles that own the
ports, matching what `collector`, `producer`, `faults` and
`dashboards/livelane.yml` already did:

| Rule | Now in | Variable it reads |
|---|---|---|
| OpenSearch HTTP, cluster nodes only | `opensearch` | `opensearch_cluster_hosts` |
| OpenSearch transport, cluster nodes only | `opensearch` | `opensearch_cluster_hosts` |
| Alertmanager API, projector host only | `alertmanager` | `alertmanager_allowed_client_addresses` |

This role now names no inventory group and reads no `hostvars` entry. The
Dashboards external port is still opened here, but through
`common_open_tcp_ports`, which `group_vars/all.yml` resolves per host:

```yaml
common_open_tcp_ports: "{{ [dashboards_external_port] if inventory_hostname in groups['control'] else [] }}"
```

Removing a rule from this role does **not** remove it from a node that already
ran an older version. firewalld keeps the permanent rule it was given. On the
existing five nodes the rules are simply rewritten in their new location; a
fresh provision is the only run that proves the new owners write them.

## Upstream roles considered and rejected

Recorded so the question is not reopened at review time.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| [`geerlingguy.swap`](https://github.com/geerlingguy/ansible-role-swap) | Third-party | The swap file, fstab entry and swappiness | Variables map almost one-to-one, and it uses `dd` by default for the same XFS reason. But it persists swappiness to its own sysctl file, which splits the single file this role deliberately shares with `vm.max_map_count`. Eight stable tasks are not worth an external dependency plus that split. |
| [`linux-system-roles.timesync`](https://github.com/linux-system-roles) | Vendor (Red Hat) | Installing and starting chrony | The strongest long-term candidate, and it ships as an RPM on Alma 9. Deferred: it replaces two tasks today. Revisit when the farm needs real NTP servers configured, which the two tasks here cannot express. |
| [`linux-system-roles.firewall`](https://github.com/linux-system-roles/firewall) | Vendor (Red Hat) | Starting firewalld and applying rules | Supports rich rules, so it could hold what this tree writes by hand. Deferred because the rules now live in six different roles; adopting it is a tree-wide change, not a `common` change. |
| [`linux-system-roles.kernel_settings`](https://github.com/linux-system-roles) | Vendor (Red Hat) | Both sysctl values | Applies settings through a tuned profile rather than a `sysctl.d` file. `vm.max_map_count` must be both live and persistent before OpenSearch starts, and that mechanism was not verified. Verify before any adoption. |

The general reasoning: upstream roles would replace roughly half the tasks here,
all of them boilerplate. What they cannot hold is the part that matters — the
reasons above, and the ordering guarantee that makes this a first play.

## Used by

- `site.yml`, play "Common host prep", on `alice_nodes` — the only caller.
