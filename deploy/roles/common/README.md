# `common`

Prepares a bare Alma 9 VM so that every other role in this tree can assume a
working host. It creates the swap file, sets the two kernel parameters
OpenSearch needs, installs the baseline packages, fixes the clock, and starts
firewalld.

Every variable it reads is declared in its own `defaults/main.yml`. It names no
inventory group and reads no `hostvars` entry. One value, the list of ports to
open, is meant to be supplied by the playbook.

It runs on all five nodes, as the first play in `site.yml` that touches them.

## What it does

```
                  EVERY NODE IN alice_nodes — before any service role

┌─ 1. SWAP FILE — an out-of-memory guard, not a storage tier ────────────────┐
│  stat /swapfile             guards the dd and mkswap tasks                 │
│  dd if=/dev/zero            2048 MB — not fallocate, see below             │
│  chmod 0600, mkswap         root-only, formatted                           │
│  /etc/fstab                 fstype swap, opts sw — survives a reboot       │
│  swapon                     skipped when already in `swapon --show`        │
│  vm.swappiness = 60         --> /etc/sysctl.d/99-opensearch.conf           │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. BASELINE PACKAGES ─────────────────────────────────────────────────────┐
│  dnf install                python3, python3-pip, tar, chrony,             │
│                             firewalld, ca-certificates                     │
│  update_cache: false        a forced refresh costs ~700 MB resident        │
│  async 600 s, poll 5 s      bounds a stuck transaction                     │
│  lock_timeout 60 s          survives a stale rpm lock                      │
│  rescue                     names memory starvation or a stale lock        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. CLOCK — every timestamp in this stack is UTC ──────────────────────────┐
│  timezone                   UTC                                            │
│  chronyd                    enabled and started                            │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. KERNEL LIMIT FOR OPENSEARCH ───────────────────────────────────────────┐
│  vm.max_map_count = 262144  --> the same 99-opensearch.conf                │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. FIREWALL ──────────────────────────────────────────────────────────────┐
│  firewalld                  enabled and started                            │
│  common_open_tcp_ports      [] by default; the playbook supplies it        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ WHAT EVERY LATER ROLE THEN ASSUMES ───────────────────────────────────────┐
│  firewalld running          --> opensearch, collector, producer, faults,   │
│                                 alertmanager and dashboards/livelane       │
│                                 each add their own rules                   │
│  vm.max_map_count           --> opensearch passes its bootstrap check      │
│  swap in place              --> the site.yml pre-flight releases the       │
│                                 memory-heavy services                      │
└────────────────────────────────────────────────────────────────────────────┘
```

## Why it runs first

Each guarantee in the last band fails somewhere else. Without firewalld running,
a later role's rule task fails, because `ansible.posix.firewalld` needs the
daemon up to apply an immediate rule. Below `vm.max_map_count` 262144, the error
is OpenSearch's own bootstrap check, raised in the `opensearch` role and not
here. Without swap, the `site.yml` pre-flight holds the memory-heavy services
back and never releases them.

It is a role rather than tasks in `site.yml` so that the ordering is one line at
the call site instead of thirty tasks.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `common_packages` | `python3`, `python3-pip`, `tar`, `chrony`, `firewalld`, `ca-certificates` | Python for the services, `tar` for the DDS tarball replay, `chrony` and `firewalld` because later tasks start them, `ca-certificates` for S3 and the OpenSearch downloads. |
| `common_dnf_timeout` | `600` | Seconds allowed for the `dnf` transaction, as the `async` bound. |
| `common_timezone` | `UTC` | Do not change. Every timestamp here is UTC, and the replay's two-clock model compares stamps from different hosts. |
| `common_vm_max_map_count` | `262144` | OpenSearch's bootstrap-check minimum, not a tuning knob. |
| `common_sysctl_conf_file` | `/etc/sysctl.d/99-opensearch.conf` | Holds both sysctl values. See couplings. |
| `common_swapfile_path` | `/swapfile` | Swap file location. Also matched against `swapon --show`. See couplings. |
| `common_swapfile_size_mb` | `2048` | 2 GB, on an m2.medium (3.75 GB nominal, 3.66 GB usable). |
| `common_swappiness` | `60` | `vm.swappiness`. Deliberate, not an unreviewed default — see below. |
| `common_open_tcp_ports` | `[]` | Ports to open to any source. `group_vars/all.yml` supplies `[dashboards_external_port]` on the control host and `[]` elsewhere. |

## Non-obvious settings

- **Swap exists as an out-of-memory guard, not a storage tier.** A node carrying
  the control plane beside an OpenSearch cluster-manager and data role
  (Dashboards, nginx, Alertmanager and several Python services on one m2.medium —
  3.75 GB nominal, 3.66 GB usable, no swap by default) exceeds available memory,
  and the kernel selects the OpenSearch JVM (~1.75 GB anonymous resident set).
  Swap converts that kill into thrashing, which is harder to diagnose, so it is
  the last line and not the fix: the control node also carries a reduced
  `opensearch_heap_size` as a host variable in `inventory.yml`, and the analytics
  services are placed on other nodes.
- **`common_swappiness: 60`, not a lower value.** At 1 the kernel reclaims page
  cache before it swaps anonymous memory, so under pressure it evicts executable
  pages, sshd's included. `sshd` then cannot complete a login, Ansible reports
  `connection timed out during banner exchange`, and the host reads as
  unreachable. 60 lets idle anonymous memory swap and keeps text pages resident.
  OpenSearch's heap is `mlockall`'d, so it never swaps at any value.
- **`dd`, not `fallocate`.** `swapon` rejects the unwritten extents `fallocate`
  leaves on XFS, and these images are XFS. `fallocate` is faster and `mkswap`
  accepts its output, so the failure appears only at `swapon`.
- **`update_cache: false`.** `dnf` refreshes metadata by itself whenever it must
  resolve a missing package. Forcing a refresh costs a network round trip and a
  resident `dnf` near 700 MB, which on a sub-4 GB node is itself a contributor to
  the kill described above.
- **The `rescue` block.** Every baseline package is normally installed already,
  so the task returns in seconds. A stall therefore means memory starvation or an
  rpm transaction lock left by an interrupted run, and the rescue reports that
  rather than a bare `dnf` timeout. `lock_timeout: 60` covers the lock; `async`
  with `poll: 5` bounds the stall.

## Couplings

- **`common_sysctl_conf_file` does not migrate.** Change it and Ansible writes
  the new file, but the old one stays on disk and keeps applying at boot.
  `sysctl.d` loads in lexical order, so which value wins depends on the two
  names. Changing this means deleting the old file by hand, on every node.
- **`common_vm_max_map_count` belongs to OpenSearch.** Set here only because it
  must precede the `opensearch` role. Lowering it breaks that role, not this one.
- **`common_swapfile_path` is used twice.** As a path to create, and as a string
  matched against `swapon --show=NAME` to decide whether swap is already active.
  A path the kernel normalises differently makes that task run every pass.
- **`common_open_tcp_ports` only ever adds.** firewalld keeps a permanent rule
  once it has been given one, so removing a port from the list does not close it
  on a node that already ran. Closing a port means `state: disabled` or a fresh
  provision.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Upstream would replace
roughly half these tasks, all boilerplate; what it cannot hold is the reasoning
above and the ordering guarantee that makes this a first play.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| [`geerlingguy.swap`](https://github.com/geerlingguy/ansible-role-swap) | Third-party | Swap file, fstab entry, swappiness | Near one-to-one variable match, and it also uses `dd` for the XFS reason. But it persists swappiness to its own sysctl file, splitting the single file shared with `vm.max_map_count`. The eight tasks it would replace are stable and low-churn. |
| [`linux-system-roles.timesync`](https://github.com/linux-system-roles) | Vendor (Red Hat) | Installing and starting chrony | Strongest long-term candidate; ships as an RPM on Alma 9. Replaces two tasks today. Revisit when the farm needs real NTP servers, which those two tasks cannot express. |
| [`linux-system-roles.firewall`](https://github.com/linux-system-roles/firewall) | Vendor (Red Hat) | Starting firewalld, applying rules | Supports rich rules, so it could hold what this tree writes by hand. But the rules now live in six roles: adopting it is a tree-wide change, not a `common` one. |
| [`linux-system-roles.kernel_settings`](https://github.com/linux-system-roles) | Vendor (Red Hat) | Both sysctl values | Applies settings through a tuned profile rather than a `sysctl.d` file. `vm.max_map_count` must be live and persistent before OpenSearch starts; that mechanism is unverified. Verify before adopting. |

## Used by

`site.yml`, play "Common host prep", on `alice_nodes` — the only caller.
