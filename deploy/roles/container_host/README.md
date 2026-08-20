# `container_host`

Prepares a machine that has to carry **more than one OpenSearch node**. It
installs podman, proves the installed version can read quadlet unit files, and
creates the directory those unit files go in. It starts nothing and opens no
port.

It exists because of a hardware fact, not a design preference. The EPN farm
gives this project one machine for the storage tier and three storage nodes to
put on it. Three nodes on one host means three services, three data
directories and three port pairs — which is what a container gives cheaply and
what a second RPM install cannot give at all.

It runs once per **machine**, never once per node. `site.yml` targets the
`container_hosts` inventory group for exactly that reason: the three storage
hosts share one machine, and installing podman three times on it is three times
the runtime for one result.

## What it does

```
        ONE MACHINE THAT WILL RUN SEVERAL OPENSEARCH NODES

┌─ 1. RUNTIME ───────────────────────────────────────────────────────────────┐
│  dnf install podman         no daemon, and in the Alma 9 base repositories  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. VERSION GATE ──────────────────────────────────────────────────────────┐
│  podman --version           parsed and compared                            │
│  assert >= 4.4              quadlet is how .container files become units   │
│                             a older podman fails here, not at first boot   │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. UNIT DIRECTORY ────────────────────────────────────────────────────────┐
│  /etc/containers/systemd    where the opensearch role writes one           │
│                             .container file per instance                   │
└────────────────────────────────────────────────────────────────────────────┘
```

## Why podman and not docker

- **No daemon.** Every other service in this tree is a systemd unit. A quadlet
  `.container` file *is* a systemd unit, generated at `daemon-reload`. Docker
  would add a second supervisor beside systemd, with its own restart policy and
  its own opinion about boot order.
- **It is in the base repositories** of Alma 9, which is what the EPN nodes run.
  Nothing extra to trust, mirror or approve.
- **Rootful, deliberately.** The OpenSearch instances need `memlock` unlimited
  and `IPC_LOCK`, because `bootstrap.memory_lock: true` is on. Rootless podman
  can be given both, but only with extra host configuration that would then be
  a fourth thing to keep in step.

## Variables

| Name | Default | Meaning |
|---|---|---|
| `container_host_packages` | `[podman]` | What to install. |
| `container_host_min_podman_version` | `4.4` | The version quadlet arrived in. Below it, the assert fails with the reason. |
| `container_host_quadlet_dir` | `/etc/containers/systemd` | Where quadlet reads unit files from. |

## Couplings

- **`container_host_quadlet_dir` must equal `opensearch_quadlet_dir`.** This
  role creates the directory; the `opensearch` role writes
  `opensearch-<node_id>.container` into it. They are two names for one path, in
  two roles, because neither role may name the other's variable.
- **The kernel parameters are not here.** `common` already sets
  `vm.max_map_count` to 262144 on every node, including this machine. One
  sysctl, one owner.
- **This role does not decide which nodes are containers.** That is
  `opensearch_install_method`, set per group in the inventory. A machine can be
  in `container_hosts` before any node on it is a container; podman is then
  installed and unused, which is harmless.
