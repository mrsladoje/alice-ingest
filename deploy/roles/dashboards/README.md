# `dashboards`

Installs OpenSearch Dashboards on the control host, points it at the cluster,
caps the Node heap, puts an nginx reverse proxy with TLS and basic
authentication in front of it, and provisions the browsing surface: the
per-source index patterns, the Maintainer Cockpit saved objects and their field
catalogs.

The role stops at the user interface. It does not create indices, monitors,
detectors, forecasters or any background service. Those belong to
`opensearch_bootstrap`, `alerting_monitors`, `anomaly_detection`,
`cockpit_metrics`, `trend_rollup`, `alice_ops`, `shifter` and
`signal_projector`.

## Why it is a separate role

Until August 2026 this role was the whole control-plane play: the web tier, the
ops server, the metrics poller, the detection layer and the signal projector all
lived here. That made one role that installed an RPM, ran a Python service and
called nine REST APIs on three different hosts.

What is left is the one thing a person opens in a browser. Its lifecycle is the
OpenSearch Dashboards package: install, configure, restart, prove the port
answers. Everything that only *uses* Dashboards as a place to display something
now sits in its own role and can be re-run without touching the web tier.

## What it does

```
                              CONTROL HOST

┌─ 1. INSTALL — signed RPM, version-pinned ──────────────────────────────────┐
│  yum_repository             artifacts.opensearch.org, gpgcheck on          │
│  rpm_key                    signing key into the rpm keyring               │
│  dnf install                opensearch-dashboards-{{ opensearch_version }} │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. SECURITY PLUGIN — removed, not configured ─────────────────────────────┐
│  stat plugins/securityDashboards                                           │
│  plugin remove securityDashboards --allow-root          --> restart        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. CONFIGURE, START, then PROVE THE BOUND ────────────────────────────────┐
│  opensearch_dashboards.yml  bind 127.0.0.1, cluster hosts     --> restart  │
│  memory.conf drop-in        NODE_OPTIONS max-old-space-size   --> restart  │
│  node.options               the same bound, when the file exists           │
│  systemd enable + start, then flush_handlers                               │
│  wait for 127.0.0.1/api/status    60 attempts, 5 s apart — 5 minutes       │
│  read /proc/<pid>/cmdline + environ, assert the bound took                 │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. TLS — self-signed, for the proxy only ─────────────────────────────────┐
│  python3-cryptography, /etc/nginx/tls 0750                                 │
│  private key, CSR (CN = ansible_host, SAN = IP + inventory name)           │
│  self-signed certificate, 10 years                            --> restart  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. NGINX — the only door a person uses ───────────────────────────────────┐
│  htpasswd                   one user, from the vault          --> restart  │
│  dashboards.conf vhost      TLS + basic-auth + upgrade headers --> restart │
│  seboolean                  httpd_can_network_connect                      │
│  seport                     5601/tcp labelled http_port_t                  │
│  systemd enable + start                                                    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 6. COCKPIT — the browsing surface, re-applied every run ──────────────────┐
│  patterns.sh                the three log index patterns                   │
│  saved-object import        cockpit.ndjson, overwrite=true, then verified  │
│  delete retired objects     four ids the import cannot remove              │
│  hydrate_patterns.py        serialized field catalog per pattern           │
│  settings/defaultIndex      alice-unified                                  │
│  settings/defaultRoute      the ALICE Cockpit dashboard                    │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **Dashboards binds to loopback and nothing else.** `server.host` is
  `127.0.0.1`. The port a person reaches is nginx on
  `dashboards_external_port`, which carries the TLS and the basic
  authentication. The cluster itself runs with its security plugin disabled, so
  the proxy is the only boundary in front of a person.
- **The security plugin is removed, not disabled.** The bundled
  `securityDashboards` plugin expects a secured cluster. Left installed against
  an unsecured one, the login page appears and no credential works.
  `--allow-root` is needed because the removal runs as root.
- **The heap bound is written twice, then proved.** The systemd drop-in sets
  `NODE_OPTIONS`, and `node.options` gets the same number when the launcher
  ships that file. A launcher that passes its own `--max-old-space-size` on the
  command line beats `NODE_OPTIONS`, so the role reads the bound back out of the
  running process and fails the deploy when it did not take. Without the assert,
  a deploy that freed no memory on the control node still reports success.
- **`vis_type_vega.enableExternalUrls: true`.** The cockpit status strip is a
  Vega visualization that queries the cluster directly. It does not render
  without this.
- **The cockpit import runs on every deploy, not behind a marker.** It is an
  `overwrite=true` import, so editing `cockpit.ndjson` and re-running the play
  updates the saved objects in place. The import result is then verified
  line-for-line: `successCount` must equal the number of non-blank lines in the
  file, and the error list must be empty.
- **Four saved objects are deleted by id after the import.** An import can only
  create or overwrite. Objects retired from `cockpit.ndjson` would otherwise
  stay in a cluster that once had them. The task accepts 404 and never fails.
- **`patterns.sh.j2` takes its two URLs from the environment, not from Jinja.**
  The task sets `OSD_URL` and `OS_URL`. The only substitution in the template is
  the pattern list, so the script stays runnable by hand against any cluster.
- **`gen_cockpit.py` never reaches a VM.** It is the build-time generator for
  `cockpit.ndjson`. Run it by hand, commit the result, then deploy.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `dashboards_config_dir` | `/etc/opensearch-dashboards` | RPM configuration directory. |
| `dashboards_config_file` | `{{ dashboards_config_dir }}/opensearch_dashboards.yml` | The rendered server configuration. |
| `dashboards_service_name` | `opensearch-dashboards` | systemd unit name. The handler restarts it. |
| `dashboards_node_max_old_space_mb` | `512` | V8 old-space bound, in megabytes. Asserted against the running process. |
| `dashboards_systemd_dropin_dir` | `/etc/systemd/system/opensearch-dashboards.service.d` | Holds `memory.conf`. |
| `dashboards_memory_dropin` | `.../opensearch-dashboards.service.d/memory.conf` | The `NODE_OPTIONS` drop-in. |
| `dashboards_node_options_file` | `{{ dashboards_config_dir }}/node.options` | Second place the bound is written, when the launcher ships the file. |
| `dashboards_system_user` / `dashboards_system_group` | `opensearch-dashboards` | Owner of the configuration file. |
| `dashboards_home_dir` | `/usr/share/opensearch-dashboards` | RPM install root. |
| `dashboards_plugin_bin` | `{{ dashboards_home_dir }}/bin/opensearch-dashboards-plugin` | Used to remove the security plugin. |
| `dashboards_nginx_tls_dir` | `/etc/nginx/tls` | Holds the key, CSR and certificate. Mode 0750. |
| `dashboards_nginx_tls_cert` | `{{ dashboards_nginx_tls_dir }}/dashboards.crt` | Self-signed certificate. |
| `dashboards_nginx_tls_key` | `{{ dashboards_nginx_tls_dir }}/dashboards.key` | RSA 2048 private key. |
| `dashboards_nginx_tls_csr` | `{{ dashboards_nginx_tls_dir }}/dashboards.csr` | Signing request. |
| `dashboards_nginx_tls_cert_days` | `3650` | Certificate lifetime in days. |
| `dashboards_nginx_htpasswd_file` | `/etc/nginx/dashboards.htpasswd` | Basic-auth database. Owned by root, readable by nginx. |
| `dashboards_nginx_vhost_file` | `/etc/nginx/conf.d/dashboards.conf` | The rendered vhost. |
| `dashboards_basic_auth_user` | `alice` | The one basic-auth account. |
| `dashboards_basic_auth_password` | `{{ vault_dashboards_basic_auth_password }}` | Its password. See couplings. |
| `alice_bootstrap_root` | `/opt/alice-ingest/init` | Where the bootstrap scripts are staged. Also declared in `group_vars/all.yml` — see couplings. |
| `dashboards_bootstrap_patterns_script` | `{{ alice_bootstrap_root }}/patterns.sh` | The rendered index-pattern script. |
| `dashboards_bootstrap_cockpit_ndjson` | `{{ alice_bootstrap_root }}/cockpit.ndjson` | The staged saved objects. |
| `dashboards_bootstrap_hydrate_script` | `{{ alice_bootstrap_root }}/hydrate_patterns.py` | The staged field-catalog hydration script. |
| `dashboards_index_patterns` | `infologger`, `application-logs-local-*`, `application-logs-central` | The per-source index patterns. Read by both `patterns.sh` and the hydration step — see couplings. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `opensearch_version` | `group_vars/all.yml` | The yum repository major version and the pinned RPM. Shared with the `opensearch` role. |
| `dashboards_internal_port` | `group_vars/all.yml` | `server.port`, the readiness probe, the proxy target and every cockpit REST call. |
| `dashboards_external_port` | `group_vars/all.yml` | The nginx listener and its SELinux port label. `common` opens it in the firewall. |
| `dashboards_opensearch_hosts` | `group_vars/all.yml` | `opensearch.hosts`. Derived from the `storage` inventory group, so it cannot be a role default. |
| `opensearch_http_port` | `group_vars/all.yml` | `OS_URL` for `patterns.sh`. |
| `ops_internal_port` | `group_vars/all.yml` | The `/ops/` proxy target. Owned with the `alice_ops` role's service. |
| `alertmanager_port` | `group_vars/all.yml` | The `/alertmanager/` proxy target. |
| `shifter_enabled` | `group_vars/all.yml` | Whether the vhost gets a `/live/` location at all. |
| `shifter_host` | `group_vars/all.yml` | The `/live/` proxy target. Derived from the `shifter` inventory group. |
| `shifter_port` | `group_vars/all.yml` | Its port. |
| `vault_dashboards_basic_auth_password` | `group_vars/vault.yml` | The basic-auth password. The play loads the vault file. |
| `ansible_host` | inventory, per host | Certificate common name and subject alternative name. |
| `inventory_hostname` | inventory, per host | The DNS subject alternative name. |

## Prerequisites

The role does not bootstrap the machine or the cluster.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| A reachable OpenSearch cluster | `opensearch` role | Dashboards starts but `/api/status` never returns 200, and the readiness wait times out after 5 minutes. |
| Index templates, the ingest pipeline and the derived indices | `opensearch_bootstrap` role | `patterns.sh` still creates the patterns, but `hydrate_patterns.py` finds no fields to serialize and fails the required-field check. |
| `firewalld` running, with `dashboards_external_port` open | `common` role | nginx starts and nobody outside the host can reach it. |
| The vault file loaded at play level | `playbooks/site.yml` | The htpasswd task fails on an undefined `vault_dashboards_basic_auth_password`. |
| Alertmanager listening on `alertmanager_port` | `alertmanager` role | The vhost still renders. `/alertmanager/` returns 502 until the service is up. |

## How to use it

Against the control host only:

```yaml
- name: OpenSearch Dashboards + nginx proxy
  hosts: control
  become: true
  vars_files:
    - "{{ playbook_dir }}/../group_vars/vault.yml"
  roles:
    - dashboards
```

- **Run it after `opensearch_bootstrap`, not before.** The hydration step reads
  the real field mappings out of the cluster.
- **Run it before `alerting_monitors`, `anomaly_detection` and
  `signal_projector`.** They stage their scripts into the same directory and
  expect the cockpit that displays their output to exist.
- **It is idempotent, but the cockpit steps are not "changed" detectors.** The
  import, the hydration and the pattern script report changed on every run by
  design. They are cheap, and marking them unchanged would hide a real update.

## Couplings

- **`opensearch_version` is shared with the `opensearch` role.** OpenSearch and
  OpenSearch Dashboards must run the same version. Change it in
  `group_vars/all.yml`, which both roles read.
- **`alice_bootstrap_root` is declared here and in `group_vars/all.yml`.**
  Three of this role's own defaults interpolate it, and a role whose defaults
  reference an undeclared variable cannot run outside this repository. The
  `group_vars` value outranks the default and stays the site source of truth,
  shared with `opensearch_bootstrap`, `alerting_monitors`, `anomaly_detection`,
  `alice_ops` and `signal_projector`, which stage their own files into the same
  directory. Both values must stay `/opt/alice-ingest/init`.
- **This role does not create `/opt/alice-ingest/init`.** It only writes into it.
  `opensearch_bootstrap` creates it, root:root 0755, and runs first.
- **`dashboards_basic_auth_password` and the vault entry change together.** The
  default is an indirection to `vault_dashboards_basic_auth_password`, which
  resolves only in a play that loads `group_vars/vault.yml`. A play that does
  not load the vault cannot run this role.
- **The certificate and the vhost change together.** `tls.yml` writes the paths
  that `dashboards.conf.j2` names. Both notify `restart nginx`, so a new
  certificate reaches the running proxy in the same run.
- **`ops_internal_port`, `alertmanager_port` and the live-lane trio are proxy
  targets owned elsewhere.** The vhost is the seam: `alice_ops`, `alertmanager`
  and `shifter` bind those ports, this role publishes them. Move a port and
  both ends change.
- **`dashboards_index_patterns` feeds two consumers that must agree.**
  `patterns.sh` creates the patterns from it, and the hydration step fills the
  same list's field catalogs through `EXTRA_PATTERNS`. They were two hardcoded
  copies of one list until August 2026; a pattern created but not hydrated
  renders an empty field list in Discover.
- **`cockpit.ndjson` and `hydrate_patterns.py`'s `REQUIRED` map change
  together.** A saved object that queries a field the hydration script does not
  list will render empty; a field listed but absent from the cluster fails the
  deploy. That is deliberate — an empty cockpit panel is worse than a failed
  run.
- **`cockpit.ndjson` is generated, not edited.** Change `gen_cockpit.py`, re-run
  it, commit the NDJSON. Editing the NDJSON by hand puts the two out of step
  silently.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Checked in August 2026.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| [`opensearch-project/ansible-playbook`](https://github.com/opensearch-project/ansible-playbook) | Vendor | Repository, key, package, configuration file | Same four objections as in the `opensearch` role: it is a playbook and not a Galaxy role, its defaults read inventory groups directly, its substance is the security plugin this cluster removes, and AlmaLinux 9 is not a supported platform. |
| [`geerlingguy.nginx`](https://galaxy.ansible.com/ui/standalone/roles/geerlingguy/nginx/) | Third-party | `nginx.yml` install and service tasks | Well maintained, but it would replace 3 of 8 tasks and own `nginx.conf` site-wide. The vhost, the htpasswd file, the two SELinux tasks and the certificate stay ours either way, and the role brings a full vhost model this tree does not use. |
| `community.crypto` | Collection, already used | The three TLS tasks | Not rejected — it is what `tls.yml` calls. |

What upstream cannot hold is the rest: the removal of the bundled security
plugin, the two-place heap bound with its read-back assertion, the cockpit
import with its line-count verification, the retired-object deletion and the
field-catalog hydration.

## Used by

- `playbooks/site.yml`, play "Control plane — Dashboards, nginx, ops page,
  monitors, roster, metrics and detectors (control host only)", against
  `control` — the only caller.
