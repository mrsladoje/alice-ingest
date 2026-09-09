# Ansible Role: sweet_os_dashboards

Puts the browser in front of the ALICE log cluster. On the control host it
installs OpenSearch Dashboards from the vendor RPM at the cluster's version,
removes the bundled security plugin, binds the server to loopback under a hard
Node heap bound, and proves the bound took. It then puts nginx in front with a
self-signed certificate and one basic-auth account, the only door a person
uses; the same vhost carries the ops page, Alertmanager and the live log lane.
Last it provisions what a person sees: the three per-source index patterns, the
Maintainer Cockpit with its 59 saved objects, and the field catalog of all nine
patterns, so Discover and the cockpit open populated.

Every page an operator opens on the farm passes through this role's proxy.

## How it works

```
                  THE CONTROL HOST: os-node-04, on epn-infra13
       native RPMs beside the three storage containers; nothing on a worker

┌─ INSTALL ────────────────────────────────────────────────────────────────┐
│  yum repository + signing key   artifacts.opensearch.org, gpgcheck on    │
│  dnf                            opensearch-dashboards-<opensearch_version>│
└────────────────────────────────────┬─────────────────────────────────────┘
                                     v
┌─ REMOVE THE SECURITY PLUGIN ─────────────────────────────────────────────┐
│  stat plugins/securityDashboards                                         │
│  opensearch-dashboards-plugin remove securityDashboards  --> restart     │
│      the cluster runs without its security plugin; left installed, the   │
│      login page appears and no credential works                          │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     v
┌─ CONFIGURE, START, PROVE THE HEAP BOUND ─────────────────────────────────┐
│  opensearch_dashboards.yml      127.0.0.1:<dashboards_internal_port>     │
│      opensearch.hosts = the three storage nodes          --> restart     │
│  memory.conf drop-in            NODE_OPTIONS=--max-old-space-size        │
│                                                          --> restart     │
│  node.options                   the same bound, when the launcher ships  │
│                                 the file                 --> restart     │
│  systemd enable + start, then flush handlers                             │
│  wait for /api/status           60 tries, 5 s apart                      │
│  assert                         /proc/<pid> cmdline or environ carries   │
│                                 the bound; any other number fails        │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     v
┌─ TLS: self-signed, for the proxy only ───────────────────────────────────┐
│  /etc/nginx/tls  0750           RSA 2048 key, CSR, certificate for       │
│                                 3650 days                --> restart     │
│      CN = ansible_host, SAN = IP:ansible_host, DNS:inventory_hostname    │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     v
┌─ NGINX: the only door a person uses ─────────────────────────────────────┐
│  htpasswd                       one account, password from the vault     │
│                                                          --> restart     │
│  dashboards.conf vhost          :<dashboards_external_port> TLS + auth   │
│      / --> Dashboards    /ops/    /alertmanager/    /live/ when the      │
│      Shifter is enabled                                  --> restart     │
│  SELinux                        httpd_can_network_connect, http_port_t   │
│      only where SELinux is enabled; the farm has it disabled, so skipped │
│  systemd enable + start                                                  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     v
┌─ COCKPIT: re-applied on every run, never behind a marker ────────────────┐
│  patterns.sh                    3 index patterns, one per log source     │
│                                 [infologger] [application-logs-local-*]  │
│                                 [application-logs-central]               │
│  import cockpit.ndjson          59 saved objects, overwrite=true:        │
│                                 6 index patterns, 15 searches,           │
│                                 37 visualizations, 1 dashboard           │
│  verify                         successCount = 59 and no errors, or fail │
│  delete 4 retired ids           an import cannot remove what it no       │
│                                 longer ships                             │
│  hydrate_patterns.py            field catalog of all 9 patterns from the │
│                                 live mappings; a missing required field  │
│                                 fails                                    │
│  defaultIndex                   [alice-unified]                          │
│  defaultRoute                   the Maintainer Cockpit dashboard         │
└──────────────────────────────────────────────────────────────────────────┘
```

**Dashboards binds to loopback only.** Everything a person reaches is nginx on
`dashboards_external_port`. The cluster has no security plugin, so the proxy's
basic authentication is the only boundary in front of a person.

**The heap bound is written twice and then proved.** A launcher that passes its
own `--max-old-space-size` beats `NODE_OPTIONS`, so the play reads the bound
back out of the running process and fails when the number differs.

**`vis_type_vega.enableExternalUrls` is on.** The cockpit's status strip and the
episode board are Vega visualizations that query the cluster directly. They
render nothing without it.

**`cockpit.ndjson` is generated, never edited.** `gen_cockpit.py` writes it to
standard output; regenerate it after changing the generator and commit the
result. The generator never reaches a machine.

## Why not an upstream role

The vendor playbook and the maintained nginx role each cover three tasks of
this role, and neither knows the cluster runs without its security plugin.
The plugin removal, the proved heap bound, the verified import, the retired
object deletion and the field-catalog hydration are the role.

| Candidate | Would replace | Why rejected |
|---|---|---|
| [`opensearch-project/ansible-playbook`](https://github.com/opensearch-project/ansible-playbook) | repository, key, package, configuration file | A playbook, not a role; its defaults read inventory groups directly; its substance is the security plugin this cluster removes; AlmaLinux 9 is not a supported platform. |
| [`geerlingguy.nginx`](https://galaxy.ansible.com/ui/standalone/roles/geerlingguy/nginx/) | install and service tasks | Well maintained, but it owns `nginx.conf` site-wide and brings a vhost model this tree does not use. The vhost, the htpasswd file, the SELinux tasks and the certificate stay ours either way. |
| [`community.crypto`](https://galaxy.ansible.com/ui/repo/published/community/crypto/) | the three TLS tasks | Not rejected: it is what the TLS tasks call. |

## Requirements

`sweet_opensearch` must have run its cluster-configure mode on the same host
first. It creates the shared bootstrap directory this role writes into, and it
loads the index templates, the ingest pipeline and the pre-created indices
whose mappings the field-catalog hydration reads; without them the hydration
fails on a missing required field. The play must load `group_vars/vault.yml`,
which holds the basic-auth password. Alertmanager, the ops page and the live
log lane are proxied, not required: their paths answer 502 until each service
is up.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
dashboards_node_max_old_space_mb: 512
```

The V8 old-space bound, in megabytes, written to the systemd drop-in and to
`node.options`. The play asserts the running process got this exact number.

```yaml
dashboards_basic_auth_user: alice
dashboards_basic_auth_password: "{{ vault_dashboards_basic_auth_password }}"
dashboards_nginx_tls_cert_days: 3650
```

One account for every path the proxy serves. The certificate is self-signed
with the host's address as its common name, so a browser warns once per
machine and then matches the address in the URL.

```yaml
dashboards_index_patterns:
  - infologger
  - application-logs-local-*
  - application-logs-central
```

The per-source patterns, created by `patterns.sh` and hydrated from the same
list. The six cockpit patterns come from `cockpit.ndjson` and are hydrated
regardless.

- `opensearch_version` pins the Dashboards package. OpenSearch and Dashboards
  must run the same version, and the value lives in `group_vars` for both roles.
- A saved object that queries a field missing from the required map in
  `hydrate_patterns.py` renders empty; a required field absent from the cluster
  fails the play. An empty cockpit panel is worse than a failed run.
- The bootstrap directory is shared with `sweet_opensearch` and the roles that
  stage their scripts beside these. Both declarations of `alice_bootstrap_root`
  must stay `/opt/sweet/init`.

From `group_vars` and the inventory: `opensearch_version`,
`dashboards_internal_port`, `dashboards_external_port`,
`dashboards_opensearch_hosts`, `opensearch_http_port`, `ops_internal_port`,
`alertmanager_port`, `shifter_enabled`, `shifter_host`, `shifter_port`,
`alice_bootstrap_root`; from the vault `vault_dashboards_basic_auth_password`;
per host `ansible_host` and `inventory_hostname`, the certificate's names.

## The nginx proxy

One vhost on `dashboards_external_port`, TLS 1.2 and 1.3, basic authentication
on every path.

| Path | Upstream | Notes |
|---|---|---|
| `/` | `127.0.0.1:<dashboards_internal_port>` | websocket upgrade headers; 90 s read timeout |
| `/ops/` | `127.0.0.1:<ops_internal_port>` | the ops page; 120 s |
| `/alertmanager/` | `127.0.0.1:<alertmanager_port>` | 120 s |
| `/live/` | `<shifter_host>:<shifter_port>` | only when `shifter_enabled`; Server-Sent Events, so buffering is off and the read timeout is one hour |

The vhost is the seam between this role and the services behind it: `alice_ops`,
`alertmanager` and `sweet_shifter_view` bind those ports, this role publishes
them. Move a port and both ends change.

Without the websocket upgrade headers on `/`, nginx downgrades every
connection to plain HTTP and parts of the Dashboards UI stop updating. Without
buffering off on `/live/`, nginx holds records back until its buffer fills.

## Example Playbook

```yaml
- hosts: control
  become: true
  vars_files:
    - group_vars/vault.yml
  roles:
    - sweet_os_dashboards
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
