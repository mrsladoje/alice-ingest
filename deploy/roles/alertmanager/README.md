# `alertmanager`

Installs Prometheus Alertmanager on the control host and gives it the
notification semantics the ALICE signal projector relies on: two grouping tiers
split on `severity`, one webhook receiver, and an `inhibit_rules` block generated
from the repository's causal-edge declarations.

It is the only component in the deploy that decides *when a human is told*. The
projector decides what an alert says; this role decides how long it waits, what
it batches it with, and what may mute it.

## Why it is a separate role

- **It is a distinct upstream daemon.** Its own binary, pinned version, system
  user, port, config file, systemd unit and readiness endpoint. None of that
  belongs to the projector that pushes into it.
- **It runs on a different host from its only client.** Alertmanager is control
  host only; the `signal_projector` role runs on the projector host. Two roles
  over one host boundary is the reason the bind address is not loopback and the
  reason this role carries a firewall rule.
- **It owns the port, so it owns the rule.** The firewalld rich rule for
  `alertmanager_port` moved out of `common` when each role took the ports it
  opens. A port opened far from the service that listens on it is a rule nobody
  finds when the service moves.

## What it does

```
                          CONTROL HOST, EVERY DEPLOY

┌─ 1. GATE — read causal_edges.json, refuse an unsafe combination ───────────┐
│  proven edges = 0   ->  assertion passes, inhibit_rules renders empty      │
│  proven edges > 0   ->  requires alertmanager_page_wait_covers_inhibition  │
│                         (see "The inhibition gate" below)                  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. FIREWALL — one rich rule per allowed client ───────────────────────────┐
│  alertmanager_allowed_client_addresses  ->  accept {{ alertmanager_port }} │
│  today that list is exactly the signal projector's host                    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. INSTALL ───────────────────────────────────────────────────────────────┐
│  system user     alertmanager, nologin, no home created                    │
│  directories     /opt/alertmanager, /etc/alertmanager (root)               │
│                  /var/lib/alertmanager (alertmanager)                      │
│  binary          pinned tarball from GitHub, unpacked only when the        │
│                  installed --version does not already report the pin       │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. CONFIGURE — alertmanager.yml.j2, validated by amtool before install ───┐
│  route tree      two tiers on severity, one collector-scoped branch        │
│  inhibit_rules   generated from the proven edges read in step 1            │
│  receiver        alice-notification-ingest -> 127.0.0.1 webhook            │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. RUN — unit, enable, flush handlers, then wait for /-/ready ────────────┐
│  handlers are flushed inside the role, not at end of play                  │
└────────────────────────────────────────────────────────────────────────────┘
```

## The routing tree

```
route  group_by [cluster_id, alertname]        5 m / 10 m / 4 h
├── notification_scope =~ "collector:.+"       + notification_scope in group_by
│   └── severity = "page"                      30 s / 2 m
└── severity = "page"                          30 s / 2 m
```

Child routes inherit the receiver, the `group_by` and any timer they do not set,
and the first matching child wins. A collector-scoped `warn` alert therefore
stops at the middle node: it keeps per-collector grouping and the batch timers.
Every route shares one receiver, so when reading `amtool config routes test
--tree ...` output, read the printed path rather than the receiver name.

The tiers exist because the ten `trend-*` monitors run on a ten-minute schedule
and a storm of them should arrive as one notification, while a dead Fluent Bit
should reach a human in about 2.5 minutes. The projector already stamps every
alert `page` or `warn`, so the tree tiers on that one label and no producer had
to change.

## The inhibition gate

`inhibit_rules` is **generated**, not written. `causal_edges.json` in the
`alice_runtime` role is the one place a cause-to-symptom edge is declared, and
each edge carries a `proven` flag. Only proven edges reach this template; the
rest are used elsewhere to rank candidate causes. Edges that share a cause, a
cause severity and an `equal` label set collapse into one rule whose
`target_matchers` is an alternation over their symptoms.

**Today 0 of 22 edges are proven, so the block renders empty and nothing is
suppressed.** That is the intended shipping state.

The assertion at the top of `tasks/main.yml` is the reason the role can be run
before that is true. `alertmanager_page_group_wait` is 30 s, chosen for *speed* —
one projector push cycle. A `group_wait` that protects inhibition has to do the
opposite: it must cover the cause-to-symptom arrival delay, so the suppressing
alert reliably reaches the store before the target's wait expires. 30 s does not.

Seventeen of the 22 target signals are `warn` and are made **safer** by the
split, because they now wait 5 m while their suppressor waits 30 s. Five pairs
are page against page and sit wholly inside the fast tier, where neither side
gets any margin:

| Suppressor | Page targets in the same tier |
|---|---|
| `collector-down` | `data-loss` |
| `telemetry-silence` | `cluster-red`, `disk-cliff-page` |
| `fleet-fb-silence` | `collector-down`, `data-loss` |

So the role refuses to run whenever any edge is `proven` unless
`alertmanager_page_wait_covers_inhibition` is `true`. The order is: measure the
delay with `make inject`, raise `alertmanager_page_group_wait` to cover it, then
set the flag. It is a deliberate stop, not a formality.

## Non-obvious settings

- **`--cluster.listen-address=` is empty on purpose.** It disables the gossip
  listener. One instance runs, there is no peer, and leaving the flag off its
  default would open 9094 for nothing.
- **`--web.route-prefix=/` while `--web.external-url` ends in `/alertmanager/`.**
  The nginx vhost proxies `location /alertmanager/` to
  `http://127.0.0.1:{{ alertmanager_port }}/` with a trailing slash, which strips
  the prefix before the request arrives. Making the route prefix match the
  external URL path would 404 every proxied request. The external URL is still
  needed so links Alertmanager generates point back through nginx.
- **The bind address is `0.0.0.0`, not loopback.** The signal projector was moved
  off the control host, so its readiness probe and its pushes arrive over the
  network. The firewalld allowlist, not the bind address, is what restricts
  access — and both halves are asserted by a contract test (see Couplings).
- **The version probe reads stdout *and* stderr.** Alertmanager has printed
  `--version` to either stream depending on release, so the two are concatenated
  before the pin is searched for. A one-stream check would re-download every run.
- **The config is validated with `amtool check-config` before it is installed.**
  That binary ships inside the same tarball, which is why the download task sits
  above the template task. Reordering them breaks a first run against an empty
  host.
- **Handlers are flushed inside the role.** The `signal_projector` role asserts
  against a *live* Alertmanager later in the same deploy. Without the explicit
  flush, a pending restart would fire at end of play — after that assertion.
- **The config file is `0640 root:alertmanager`.** It can carry
  `notification_ingest_token` as a bearer credential.
- **`equal:` label lists are written explicitly, never omitted.** Alertmanager
  treats a missing label and an empty one as identical, so an `equal:` rule
  applies when all its listed labels are absent from *both* alerts. Being
  explicit is what keeps an unrelated pair from matching.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `alertmanager_version` | `0.28.1` | The pinned release. Also the string the install probe searches for. |
| `alertmanager_arch` | `linux-amd64` | Tarball architecture suffix. |
| `alertmanager_download_url` | GitHub release URL built from the two above | Override for a mirror or an air-gapped source. |
| `alertmanager_install_root` | `/opt/alertmanager` | Unpack target. Also where `amtool` is found for config validation. |
| `alertmanager_binary` | `{{ alertmanager_install_root }}/alertmanager` | `ExecStart` and the version probe. |
| `alertmanager_config_dir` | `/etc/alertmanager` | Config directory, root-owned. |
| `alertmanager_config_file` | `{{ alertmanager_config_dir }}/alertmanager.yml` | Rendered config. |
| `alertmanager_data_dir` | `/var/lib/alertmanager` | `--storage.path`, and the service user's home. Holds silences and the notification log. |
| `alertmanager_service_name` | `alertmanager` | Unit name, used by the handler and the unit file path. |
| `alertmanager_system_user` | `alertmanager` | Service user and group. Also the config file's group. |
| `alertmanager_allowed_client_addresses` | `[]` | Addresses permitted through the firewall to `alertmanager_port`. Empty here so the role is runnable alone; the playbook supplies the real list. |

### Variables the role requires but does not own

All from `group_vars/all.yml`. The role declares none of them, because changing
any one of them alone would break something outside this role.

| Group | Variables | Where it lands |
|---|---|---|
| Port | `alertmanager_port` | Bind address, firewall rule, nginx `proxy_pass`, projector probe |
| Batch tier | `alertmanager_group_wait`, `alertmanager_group_interval`, `alertmanager_repeat_interval` | Root route |
| Page tier | `alertmanager_page_group_wait`, `alertmanager_page_group_interval` | Both `severity = "page"` branches, and the gate's message |
| Global | `alertmanager_resolve_timeout` | The projector's re-send cadence is derived from it |
| Inhibition | `causal_edges_file`, `alertmanager_page_wait_covers_inhibition` | The gate and the generated `inhibit_rules` |
| Receiver | `notification_ingest_port`, `notification_ingest_token` | Webhook URL and its bearer credential |
| External URL | `dashboards_external_port` | The unit's `--web.external-url`, through the nginx vhost |

`causal_edges_file` is read with `lookup('file')`, so it is resolved **on the
controller** through `playbook_dir`, not on the target host.

## How to use it

```yaml
- name: Alertmanager (control host only, behind the existing nginx seam)
  hosts: control
  become: true
  roles:
    - alertmanager
```

- **Run it on exactly one host.** There is no gossip cluster. A second instance
  would duplicate every notification and share no silence state.
- **Run it before `signal_projector`.** That role's readiness gate retries twelve
  times, five seconds apart, and then fails the play.
- **Run it after `dashboards`** if you want the `/alertmanager/` path to answer,
  since `dashboards` owns the nginx vhost that proxies it. The daemon itself does
  not depend on nginx.
- **Silences go through nginx**, at `/alertmanager/` behind the same basic-auth as
  Dashboards. Place one — matching e.g. `cluster_id="alice-logs"` — before a
  `make replay-fresh`, a deploy, or a press of the ops page's clear button.
- **Verify a routing change without a VM.** Render the template and run
  `amtool config routes test --config.file <rendered> --tree
  cluster_id=alice-logs alertname=data-loss severity=page
  notification_scope=collector:node-01`.

## Couplings

- **`alertmanager_port` is read in four places.** This role's bind address and
  firewall rule, the `dashboards` nginx vhost `proxy_pass`, and the
  `signal_projector` readiness probe and `ALERTMANAGER_URL`. It lives in
  `group_vars/all.yml` for that reason.
- **The bind address and the firewall rule are one decision.** Opening
  `0.0.0.0` is only safe because the allowlist is narrow. `test_signal_contract.py`
  asserts both by grepping this role's literal template and task strings —
  `--web.listen-address=0.0.0.0:{{ alertmanager_port }}` in the unit, and the
  rich rule plus the `alertmanager_allowed_client_addresses` loop in the tasks.
  Rewriting either line in a way that changes those substrings fails that test,
  even though the deploy would still work.
- **`alertmanager_allowed_client_addresses` derives from `signal_projector_host`
  in `group_vars`.** The role takes a plain list so it never names an inventory
  group of its own. Moving the projector to another host is a group-vars edit.
- **The webhook posts to `127.0.0.1`.** The notification receiver must therefore
  run on the control host, which is why `site.yml` installs it through
  `signal_projector`'s `receiver.yml` in a control-host play *before* the
  projector's own play.
- **`notification_ingest_token` is written into this config and read by the
  receiver.** Setting it in only one of the two places drops every notification
  with a 401.
- **`causal_edges_file` points into the `alice_runtime` role's `files/`.** That
  file is the single declaration of an edge; the anomaly and projector layers
  read the same file for candidate-cause ranking. Adding a rule here means
  flipping `proven` there, not editing this template.
- **`alertmanager_page_group_wait` and
  `alertmanager_page_wait_covers_inhibition` must move together.** Raising the
  wait without setting the flag leaves the role refusing to run; setting the flag
  without raising the wait defeats the gate.
- **`--web.external-url` embeds `ansible_host` and `dashboards_external_port`.**
  It assumes Alertmanager and the nginx vhost are on the same host. Splitting
  them means passing the public URL in as a variable instead.

## What is frozen

Deployment knobs — ports, paths, versions, timers, the allowlist — are fully
parameterised. The domain layer is not, and should not be:

- The shape of the route tree and the labels it matches on (`severity`,
  `notification_scope`, `cluster_id`, `alertname`). These are the projector's
  contract, not a preference.
- The receiver name `alice-notification-ingest` and the `/notifications` path.
  Both appear in the projector's tests and in the injection scorer.
- `send_resolved: true` and `max_alerts: 0`. The incident model needs the resolve
  edge, and truncating a storm would drop members the scorer counts.
- The inhibit-rule generation itself. The decision surface is `proven` in
  `causal_edges.json`; a knob here would just let the two disagree.

## Upstream roles rejected

Searched August 2026. This is the closest call of any role in this repository —
the candidate is real, so the reasoning is recorded rather than assumed.

- [`prometheus.prometheus.alertmanager`](https://galaxy.ansible.com/ui/repo/published/prometheus/prometheus/content/role/alertmanager/)
  — the maintained community-standard role, and the successor to the deprecated
  [`cloudalchemy.alertmanager`](https://github.com/cloudalchemy/ansible-alertmanager).
  It would genuinely replace seven of our eleven tasks: the user, the
  directories, the version probe, the download, the unit, the config templating
  with `amtool` validation, and the service start. It exposes
  `alertmanager_route`, `alertmanager_inhibit_rules` and `alertmanager_receivers`.
- [`idealista.prometheus_alertmanager_role`](https://github.com/idealista/prometheus_alertmanager_role)
  — third-party, narrower, no advantage over the above.

**Kept ours, for three reasons.**

1. **It relocates the domain layer without removing it.** The route tree and the
   causal-edge generation would become large expressions inside
   `group_vars/all.yml`. Fourteen lines of Jinja that read as a template read far
   worse as a variable value, and the reviewer's question — "what does this
   deploy actually notify on?" — gets harder to answer, not easier.
2. **Four tasks stay ours regardless**, and they are the four that carry the
   thinking: the inhibition gate, the firewall rule tied to the bind address, the
   in-role handler flush, and the readiness wait the projector depends on. We
   would keep a wrapper role either way.
3. **The variable namespace collides exactly.** The upstream role also uses
   `alertmanager_version`, `alertmanager_config_file` and friends, but splits our
   `alertmanager_port` into `alertmanager_web_listen_address`. A half-migrated
   tree where both meanings are live is worse than either end state.

**What would change the answer:** a second Alertmanager instance (the upstream
role handles clustering and we do not), or a second notification backend beyond
the one webhook. Neither is planned.

## Used by

- `playbooks/site.yml`, play "Alertmanager (control host only, behind the
  existing nginx seam)", against `control` — the only caller.

## Depends on

- `ansible.posix` for `firewalld`, already in `requirements.yml`.
- `alice_runtime`'s `files/causal_edges.json`, read from the controller.
- The `dashboards` role's nginx vhost, for browser access at `/alertmanager/`.
