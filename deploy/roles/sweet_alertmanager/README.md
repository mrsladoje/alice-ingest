# Ansible Role: `sweet_alertmanager`

Installs Prometheus Alertmanager on the control host and gives it the
notification rules of the ALICE EPN log platform. It reads the causal-edge
declarations and refuses to run when a proven edge could be muted by the page
tier's short wait, opens the port to the projector host alone, installs the
pinned release, renders a configuration that `amtool` has validated, restarts
the daemon inside the role and waits for it to answer ready. The configuration
groups every alert by cluster and name, gives page alerts their own fast tier,
generates the inhibition rules from the proven edges, and delivers every
notification to the receiver on the same host.

This is the only component that decides when a person is told. The projector
decides what an alert says; this role decides how long it waits, what it is
batched with, and what may mute it.

## How it works

On the farm, `control` is `os-node-04`, an Ansible host on the infrastructure
machine `epn-infra13`, and the inventory sets `alertmanager_port` to 9193 there
because 9093 is taken. The only client, the projector host `os-node-05`, is on
the same machine. nginx from `sweet_os_dashboards` publishes the web interface
at `/alertmanager/` behind the Dashboards TLS and basic auth; that is where a
silence is placed before a deploy or a replay. Staging runs the same layout on
OpenStack with the control host and the projector host on two VMs.

```
                    CONTROL HOST — hosts: control, every run

┌─ 1. GATE — read this role's causal_edges.json ────────────────────────────┐
│  proven edges = 0  -->  passes; inhibit_rules renders empty               │
│  proven edges > 0  -->  passes only when                                  │
│                         alertmanager_page_wait_covers_inhibition is true  │
│      the page tier waits 30 s, which covers no cause-to-symptom delay     │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 2. FIREWALL — one rich rule per client, when alice_manage_firewalld ─────┐
│  alertmanager_allowed_client_addresses  -->  accept alertmanager_port/tcp │
│      group_vars lists the projector host; the farm runs without firewalld │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 3. INSTALL ──────────────────────────────────────────────────────────────┐
│  user         alertmanager, nologin, no home directory                    │
│  directories  /opt/alertmanager  /etc/alertmanager         root           │
│               /var/lib/alertmanager                        alertmanager   │
│  binary       the release tarball from GitHub, unpacked only when         │
│               `alertmanager --version` does not report the pinned version │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 4. CONFIGURE — each file notifies a restart ─────────────────────────────┐
│  alertmanager.yml      route tree, generated inhibit_rules, one receiver  │
│                        0640 root:alertmanager, amtool check-config first  │
│  alertmanager.service  bind 0.0.0.0:alertmanager_port, gossip off         │
│                        external URL https://<host>:5601/alertmanager/     │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   v
┌─ 5. RUN ──────────────────────────────────────────────────────────────────┐
│  enable and start; flush the restart handler inside the role;             │
│  GET 127.0.0.1:alertmanager_port/-/ready, 12 attempts 5 s apart           │
└───────────────────────────────────────────────────────────────────────────┘
```

- **The gate refuses a proven edge under the 30 s page wait.** A wait that
  protects inhibition must cover the cause-to-symptom delay, so the
  suppressing alert is in the store before the target's wait expires. Raising
  `alertmanager_page_group_wait` and setting the flag are one change.
- **The bind address and the firewall rule are one decision.** The projector
  host pushes over the network, so the unit binds `0.0.0.0` and the allowlist
  is what restricts access. The signal contract test greps both lines.
- **Handlers are flushed inside the role.** `sweet_signal_projector` asserts
  against a running Alertmanager later in the same run, so a pending restart
  does not wait for the end of the play.

The configuration the role renders:

```
route   receiver alice-notification-ingest
        group_by [cluster_id, alertname]                     5m / 10m / 4h
├── notification_scope =~ "collector:.+"
│   group_by [cluster_id, alertname, notification_scope]     inherited
│   └── severity = "page"                                    30s / 2m / 4h
└── severity = "page"                                        30s / 2m / 4h
        timers: group_wait / group_interval / repeat_interval, from group_vars

inhibit_rules   one rule per proven cause, cause severity and equal set,
                matching every symptom of those edges as its targets;
                0 of 22 edges are proven, so the block renders empty
receiver        POST http://127.0.0.1:8091/notifications, send_resolved,
                max_alerts 0, Bearer notification_ingest_token when set
```

- **A child route inherits what it does not set, and the first match wins.**
  A collector-scoped `warn` alert stops at the middle node with per-collector
  grouping and the batch timers. Every route shares the one receiver.
- **The tiers exist because the trend monitors run every ten minutes.** A
  storm of them arrives as one notification, while a page leaves after 30 s.
  The projector stamps every alert `page` or `warn`, so the tree tiers on that
  one label.
- **`inhibit_rules` is generated, never written.** `files/causal_edges.json`
  is a copy of the file `sweet_signal_projector` owns, and the signal contract
  test keeps every copy byte-identical. Adding a rule means setting `proven`
  there and copying the file here.

Five candidate pairs are page against page and sit inside the fast tier, where
neither side has any margin; the other seventeen targets are `warn` and wait
five minutes while their suppressor waits 30 s.

| Suppressor | Page targets in the same tier |
|---|---|
| `collector-down` | `data-loss` |
| `telemetry-silence` | `cluster-red`, `disk-cliff-page` |
| `fleet-fb-silence` | `collector-down`, `data-loss` |

## Why not an upstream role

`prometheus.prometheus.alertmanager` is a real candidate, the closest call
among these roles, and it would replace the user, directory, download, unit,
configuration and service tasks. Kept ours because the four tasks that carry
the thinking stay either way: the inhibition gate, the firewall rule tied to
the bind address, the in-role handler flush and the readiness wait.

| Role | Why rejected |
|---|---|
| [prometheus.prometheus.alertmanager](https://galaxy.ansible.com/ui/repo/published/prometheus/prometheus/content/role/alertmanager/) | The route tree and the generated `inhibit_rules` become Jinja inside `group_vars`, where a reviewer cannot read what the deployment notifies on. Its `alertmanager_web_listen_address` replaces `alertmanager_port`, which three roles read. A second instance would change the answer: it handles clustering and this role does not. |
| [cloudalchemy.alertmanager](https://github.com/cloudalchemy/ansible-alertmanager) | Deprecated in favour of the collection role above. |
| [idealista/prometheus_alertmanager_role](https://github.com/idealista/prometheus_alertmanager_role) | Third-party and narrower; no advantage over the collection role. |

## Requirements

Nothing has to run first on this host; the daemon needs nothing from the
cluster. `sweet_signal_projector` must run its receiver entry point on this
same host, because the webhook posts to `127.0.0.1`, and its projector play
must come after this role, because the projector refuses to start until
`/-/ready` answers. `sweet_os_dashboards` owns the nginx virtual host that
publishes `/alertmanager/`. The firewall rule needs the `ansible.posix`
collection.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
alertmanager_version: "0.28.1"
alertmanager_arch: linux-amd64
alertmanager_download_url: "https://github.com/prometheus/alertmanager/releases/download/v{{ alertmanager_version }}/alertmanager-{{ alertmanager_version }}.{{ alertmanager_arch }}.tar.gz"
```

The version string is what the install probe searches for in the output of
`--version`, on both streams, and the download runs only when it is missing.
Override the URL for a mirror.

```yaml
alertmanager_allowed_client_addresses: []
alice_manage_firewalld: true
```

The addresses allowed through firewalld to `alertmanager_port`; `group_vars`
sets the list to the projector host's address. The farm runs without firewalld
and sets the switch false.

```yaml
alertmanager_install_root: /opt/alertmanager
alertmanager_config_dir: /etc/alertmanager
alertmanager_data_dir: /var/lib/alertmanager
alertmanager_system_user: alertmanager
```

The data directory holds the silences and the notification log, so a
reinstall keeps both. `amtool` is found under the install root.

From `group_vars` and the inventory: `alertmanager_port`,
`alertmanager_resolve_timeout`, `alertmanager_group_wait`,
`alertmanager_group_interval`, `alertmanager_repeat_interval`,
`alertmanager_page_group_wait`, `alertmanager_page_group_interval`,
`alertmanager_page_wait_covers_inhibition`, `notification_ingest_port`,
`notification_ingest_token`, `dashboards_external_port`.

- `--web.route-prefix=/` while the external URL ends in `/alertmanager/`:
  nginx strips the prefix before the request arrives, so a matching route
  prefix would answer 404 to every proxied request.
- `--cluster.listen-address=` is empty, which turns the gossip listener off.
  One instance runs; a second would duplicate every notification and share
  no silence state.
- `equal:` lists are written out in full. Alertmanager treats a missing label
  and an empty one as the same, so an unlisted label would let an unrelated
  pair match.

## Example Playbook

```yaml
- hosts: control
  become: true
  roles:
    - sweet_alertmanager
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
