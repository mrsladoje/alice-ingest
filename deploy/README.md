# alice-ingest — Distributed Deployment (5-VM two-tier, native, no Docker)

This tree takes the ALICE O2 logging paper-airplane from "Docker Compose on
one machine" to **5 CERN OpenStack VMs, native systemd services, one 5-node
two-tier OpenSearch cluster** (2 worker + 3 storage), provisioned and
configured with pure Ansible. As of v4 the native stack runs **OpenSearch +
Dashboards 3.7.0** (see
[`../docs/CARDBOARD-AIRPLANE-V4.md`](../docs/CARDBOARD-AIRPLANE-V4.md) for the
migration and the finished cockpit; the compose stack deliberately stays on
2.17). It evolves the earlier flat 3-VM layout (tag
`cardboard-airplane-v1`) into a value-based storage split: high-volume, low-value
`info` logs stay strictly local and disposable on the worker that produced them,
while low-volume, high-value logs (`other` + infologger) are shipped to a
dedicated, replicated storage tier. It is
purely **additive**: `docker-compose.yml`, `docker-compose.mocks.yaml`,
`images/**`, `init/**` and the root Makefile/README are untouched and remain
the local dev path (Docker Compose on a single machine — see the root
[`README.md`](../README.md)). This document is the canonical plan/runbook for
`deploy/` (it replaces a deleted `docs/DEPLOY-DISTRIBUTED.md`).

---

## 1. Topology

```
                              CERN_NETWORK (internal only) — one OpenSearch cluster
    ┌──────────────────────── WORKER TIER (2) ────────────────────────┐
┌───────────────────┐                          ┌───────────────────┐
│ alice-ingest-1     │                          │ alice-ingest-2     │
│ OpenSearch node-01 │  node.roles:[data,ingest]│ OpenSearch node-02 │
│  attr role=worker  │  (never manager)         │  attr role=worker  │
│  attr box=node-01  │                          │  attr box=node-02  │
│ Fluent Bit -> localhost:9200 ONLY             │ Fluent Bit -> localhost:9200 ONLY
│ alice-replay  epn%2==0 slice                  │ alice-replay  epn%2==1 slice
│ generic-log-info-node-01 (1 shard,0 repl,     │ generic-log-info-node-02 (local,
│   pinned require.box=node-01, disposable)     │   pinned require.box=node-02)
└───────────────────┘                          └───────────────────┘
    └──────────────────────── STORAGE TIER (3) ───────────────────────┘
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ alice-ingest-3     │   │ alice-ingest-4     │   │ alice-ingest-5     │
│  (control)         │   │                    │   │                    │
│ OpenSearch node-03 │   │ OpenSearch node-04 │   │ OpenSearch node-05 │
│  roles:[cluster_manager,data]  attr role=storage on all three (quorum 2)
│ nginx (TLS+auth)   │   │                    │   │                    │
│  :5601 (SG-open)   │   │ generic-log-other  │   │ infologger         │
│ Dashboards :5602   │   │  + infologger:     │   │  + generic-log-other:
│ idempotent bootstrap│   │  3 shards, 2 repl, require.role=storage, balanced across the 3
│ alice-ops (/ops)   │   │  + cockpit-metrics: 1 shard, 2 repl, storage-pinned
│ alice-metrics poller   │                    │   │                    │
│ alice-trend-rollup │   │                    │   │                    │
│ alertmanager       │   │                    │   │                    │
│ alice-signal-projector · alice-notification-ingest                    │
└───────────────────┘   └───────────────────┘   └───────────────────┘
  Storage tier runs NO collector and NO producer — it never touches the ingest firehose.
```

Single OpenSearch cluster, `cluster.name: alice-logs`, split into two
hard-pinned tiers by shard-allocation `require` filtering:

- **Worker tier (2 nodes):** `node.roles: [data, ingest]` (never
  manager-eligible), `node.attr.role: worker`, `node.attr.box: <node_id>`. Runs
  the Fluent Bit collector + S3-replay producer for its own EPN slice, and holds
  only its own `generic-log-info-<node_id>` index (1 shard, **0 replicas**,
  `require.box: <node_id>`) — so the info firehose is written localhost → local
  shard with zero network hops and is deliberately disposable. The `ingest` role
  is required because every index sets `default_pipeline: alice-add-ingest-time`,
  and it is on the workers specifically so that local info write runs its pipeline
  locally rather than hopping to another node.
- **Storage tier (3 nodes):** `node.roles: [cluster_manager, data, ingest]`,
  `node.attr.role: storage`, quorum 2 (tolerates one storage node lost). Holds
  `generic-log-other` and `infologger` (3 shards, **2 replicas**,
  `require.role: storage`). Runs no collector and no producer.

Exactly one VM — `alice-ingest-3`, the first storage node, the "control" host —
additionally runs OpenSearch Dashboards, an nginx reverse proxy in front of it,
and the idempotent cluster bootstrap (tier-aware index templates + per-worker
index pre-creates, Dashboards index patterns).

---

## 2. Locked design decisions — and why

- **Native, no Docker anywhere in `deploy/`.** Official yum repos + RPMs +
  systemd units, not container images. This is a deliberate divergence from
  the Compose-based local dev stack: on real CERN VMs we want systemd-managed
  services with normal `systemctl`/journald operational ergonomics, not a
  second container runtime to operate on top of OpenStack.
- **Tier by severity, not by source.** The collector already routes by
  severity (`rewrite_tag` → `family.info` / `family.other`), so dds and stdout
  stay merged. `info` is simultaneously the bulk and the trash → kept local and
  disposable on the worker. `other` is simultaneously rare and valuable → shipped
  to the replicated storage tier (cheap, because low-volume, and worth
  replicating). Source-based placement ("dds local, stdout to storage") can't do
  that — it would ship the whole stdout-info bulk across the network while
  disposably discarding dds errors. The only edit to the v1 collector pipeline is
  the `family.info` output index name (`generic-log-info` →
  `generic-log-info-<node_id>`); the `family.other` and `infologger` outputs are
  byte-for-byte unchanged and now land on the storage tier via one network hop.
- **Fluent Bit → its own LOCAL OpenSearch node only.** Each worker's collector
  writes to `http://localhost:9200`; there is no cross-VM write hop from the
  collector. The `generic-log-info-<node_id>` index is pinned to that same VM
  (`require.box`), so the high-volume info path is localhost → local shard, zero
  network. `generic-log-other`/`infologger` are written to localhost too, then
  the local coordinator forwards them one hop to the storage tier — acceptable
  because that tier is low-volume. Only the 2 worker VMs run a collector.
- **One control VM for Dashboards/nginx/bootstrap, on the storage tier.**
  Dashboards and the cluster bootstrap only need to exist once per cluster (any
  node answers cluster-wide API calls). It lives on `alice-ingest-3`, the first
  storage node — deliberately a storage node so the UI/bootstrap host is one that
  never runs the ingest firehose. Nothing else about it is special.
- **`epn_num % 2` slicing (was `% 3`).** `images/replay/replay.py` (preserved,
  unmodified) partitions EPN hosts across `NODE_COUNT` collectors by
  `epn_num % NODE_COUNT`. `NODE_COUNT` now derives from the **`workers`** group
  (→ 2), so each worker's `epn_partition` (0 or 1 — set once in `inventory.yml`,
  pinned to `node-01`/`node-02`) selects exactly its slice. Dropping from 3
  workers to 2 means each replays ~50% more EPN hosts; `dds_replay_rate` /
  `il_replay_rate` are env-tunable if a worker saturates. `node` (the collector
  identity) is this VM's `node_id`; `host`/`hostname` (the EPN the log was born
  on) is untouched.
- **Heap `-Xms1g -Xmx1g`.** VMs are `m2.medium` (2 vCPU / 3.75 GB RAM).
  1 GB heap leaves ~2.5 GB for OS/page cache/Fluent Bit and raises the AD
  model memory budget (10% of heap). See `deploy/group_vars/all.yml`
  `opensearch_heap_size`.
- **Alertmanager is built, in the seam that was reserved for it.** The nginx
  vhost's "another control-host-only service behind this nginx" slot is now
  wired: `alertmanager_port` is a real variable, the service is control-host
  only and loopback-bound, and it is reachable at `/alertmanager/` behind the
  same TLS and basic-auth. It owns notification semantics only — grouping
  timers, inhibition matching, silences, routing. It is **not** the incident
  database: it does not persist alerts across a restart and expects the sender
  to keep re-sending, which is why the projector's re-send contract is
  load-bearing and has its own dead-man monitor.

---

## 3. Prerequisites

> ### ⚠️ Clean-slate prerequisite — this tree assumes an empty project or an existing v2
>
> v2 is self-contained and only ever expects the OpenStack project to be **empty**
> or **already running this same v2 deployment**. It does **not** know about, detect,
> or clean up v1, and it ships no v1-teardown step by design.
>
> The catch is that `provision.yml` reuses the fixed VM names `alice-ingest-1..5`
> with `state: present` (idempotent-by-name). If a **v1** deployment
> (`cardboard-airplane-v1`, VMs `alice-ingest-1..3`) is still live, running
> `make provision` here would *adopt* those three VMs and merely add `-4`/`-5` —
> silently producing an unsupported mixed v1/v2 cluster with the wrong node roles.
>
> **So: if a v1 stack is running, tear it down first** (from a `cardboard-airplane-v1`
> checkout: `cd deploy && ansible-playbook teardown.yml`), *then* provision v2 on the
> now-empty project. Re-running v2 provision/deploy against an existing **v2** stack is
> safe and idempotent.

### 3.1 CERN / lxplus access

- An lxplus account (`masladoj`) with access to OpenStack project
  `Personal masladoj` (10 cores / 40 GB quota).
- On lxplus:
  ```bash
  kinit                          # Kerberos ticket (klist to verify)
  export OS_AUTH_URL=https://keystone.cern.ch/v3
  export OS_AUTH_TYPE=v3fedkerb
  export OS_IDENTITY_PROVIDER=sssd
  export OS_PROTOCOL=kerberos
  export OS_PROJECT_DOMAIN_ID=default
  export OS_PROJECT_NAME="Personal masladoj"
  openstack token issue          # confirms auth works
  ```
  (Append the six `export` lines to `~/.bashrc` once, so future sessions only
  need `kinit`.) This is also the environment `provision.yml`/`teardown.yml`
  need — they authenticate via the `openstack.cloud` collection using exactly
  this ambient `OS_*` environment, never a hardcoded credential.

### 3.2 Control-node toolchain — `make bootstrap`

One command from the repo root builds a self-contained venv (`.venv/`, gitignored)
with every control-node dependency and installs the Galaxy collections:

```bash
make bootstrap
```

That installs, from `deploy/requirements.txt`: `ansible-core`; `openstacksdk`
(the SDK `openstack.cloud` modules run through); **`keystoneauth1[kerberos]`**
(required for `v3fedkerb` auth — without the `[kerberos]` extra, provision fails
with an `ImportError` the moment it tries the Kerberos plugin); and
`python-openstackclient` (the `openstack` CLI, used for the clean-slate check).
Then it installs the `requirements.yml` collections: `openstack.cloud`
(VM/network/security-group management), `ansible.posix` (sysctl, firewalld),
`community.general` (htpasswd, timezone), `community.crypto` (self-signed TLS
for the nginx/Dashboards proxy).

`make provision`/`deploy`/`teardown` use `.venv` automatically (falling back to
an already-activated venv on `PATH` if `.venv` is absent), so after
`make bootstrap` you don't need to activate anything. Override the location with
`make bootstrap VENV=/path/to/venv` (pass the same `VENV=` to the other targets).

> **Build prerequisite:** `keystoneauth1[kerberos]` compiles a small C extension
> (`pykerberos`), so the box needs `gcc` and `krb5-devel` (`krb5-config`). lxplus
> has both. On Python 3.9 (lxplus default) `ansible-core` resolves to 2.15.x and
> the newer collections log a "does not support 2.15" *warning* — non-fatal, and
> the version v1 deployed green on.

### 3.3 Vault setup (secrets)

Two secrets are vault-only, never plaintext in the repo: the `[cern_s3]`
AWS/Ceph RGW keys, and the nginx basic-auth password for the Dashboards
proxy.

```bash
cd deploy
cp group_vars/vault.yml.example group_vars/vault.yml
$EDITOR group_vars/vault.yml        # fill in real vault_s3_access_key_id,
                                     # vault_s3_secret_access_key,
                                     # vault_dashboards_basic_auth_password
ansible-vault encrypt group_vars/vault.yml
```
`group_vars/vault.yml` is gitignored (see `deploy/.gitignore`) specifically
so a plaintext draft can never be committed by accident — only the encrypted
file should ever exist on disk. Every subsequent playbook run needs
`--ask-vault-pass` (or `--vault-password-file <path>` if you keep one
locally; also gitignored).

---

## 4. Runbook

Run from `deploy/` (an `ansible.cfg` there points `inventory=` at
`inventory.yml,inventory.generated.yml`, so no `-i` flag is needed for any of
the commands below).

```bash
cd deploy

# 1. Provision the 5 OpenStack VMs (idempotent — safe to re-run).
kinit    # if the ticket has expired
ansible-playbook provision.yml

# 2. Configure everything (OpenSearch cluster, Dashboards+nginx+bootstrap,
#    Fluent Bit, replay producers) — needs the vault password.
#    This ARMS the pipeline but ingests nothing (AUTOSTART_REPLAY=false).
ansible-playbook site.yml --ask-vault-pass

# 3. Load real logs — the "replay button". No vault needed.
ansible-playbook replay.yml                  # first clean load
ansible-playbook replay.yml -e replay_fresh=true   # wipe + reload (no dedup!)
```

Equivalent shortcuts from the repo root: `make deploy`, `make replay`,
`make replay-fresh`.

**How the generated inventory works.** `deploy/inventory.yml` is the
committed source of truth for groups (`alice_nodes`, `control`), `node_id`,
and `epn_partition` — but its `ansible_host` values are placeholders
(`203.0.113.1x`). `provision.yml` creates the VMs, reads back each one's real
assigned IPv4, and writes `deploy/inventory.generated.yml` (gitignored).
`ansible.cfg` parses `inventory.yml` first and `inventory.generated.yml`
second, so the generated file's real IPs silently override the placeholders
for the same host names — nothing needs editing by hand, and `site.yml` never
sees a placeholder IP. Re-running `provision.yml` (e.g. after a VM was
recreated) just refreshes `inventory.generated.yml`.

### 4.1 Two auth paths

- **Default: lxplus as the control node.** lxplus reaches `CERN_NETWORK`
  directly — no `ProxyJump` needed. Run both playbooks from an lxplus
  session (after `kinit` + the `OS_*` exports above). This is what
  `deploy/inventory.yml`'s `ansible_user: root` assumes with no extra SSH
  args.
- **Alternative: from a Mac, outside CERN.** Use an OpenStack **application
  credential** for the `OS_*` env instead of Kerberos, and uncomment the
  documented SSH jump in `deploy/inventory.yml`:
  ```yaml
  # ansible_ssh_common_args: '-o ProxyJump=masladoj@lxplus.cern.ch'
  ```
  `host_key_checking = false` is already set in `ansible.cfg`. Also pass
  `-e provision_wait_for_ssh=false` to `provision.yml` on this path: its
  "wait for SSH" task is a local socket connect from the controller, which
  cannot traverse an SSH `ProxyJump` — `site.yml`'s own SSH connection
  attempts (which *do* honor `ansible_ssh_common_args`) are the equivalent
  gate when this flag is set.

### 4.2 Bring-up and restart behaviour (read before day-2 changes)

`site.yml` splits the OpenSearch role into two plays:

1. **Initial bring-up** (`hosts: alice_nodes`, no `serial`) — all 5 nodes
   start together, deliberately, because a brand-new cluster's
   `cluster.initial_cluster_manager_nodes` quorum (2-of-3 among the
   manager-eligible **storage** nodes) can only be reached if at least two
   storage nodes are up concurrently; a `serial: 1` rollout here would deadlock
   the first node waiting on a cluster that can never form. This play's config
   templates notify the `restart opensearch` handler, and the role flushes
   handlers at its end — so on a *fresh* deploy all nodes simply start once,
   which is correct.
2. **Health gate** (`hosts: alice_nodes`, `serial: 1`) — one node at a time,
   waits for that node's HTTP API to respond, then waits for `_cluster/health`
   to return a valid status (`red`/`yellow`/`green` — first-boot tolerant, not
   hard-requiring `green`) before moving to the next node.

**This is NOT a rolling-restart guarantee for reconfiguration.** Because the
restart handler flushes inside play 1 (which has no `serial`), *re-running
`site.yml` against a live cluster with a config change restarts every
OpenSearch node — including all 3 storage managers — at once*; by the time the
`serial: 1` gate in play 2 runs there is no pending handler left to flush, so
it only gates health, not the restart. This is inherited unchanged from v1 and
is acceptable for the v2 milestone (fresh provision → deploy → verify →
teardown), where every node starts exactly once. A true serialized
reconfiguration is a deliberate, real-cluster-tested follow-up — see §8. Fluent
Bit and the replay producers are stateless per node (a restart only re-tails
from saved offsets or resumes from the `AUTOSTART_MARKER` guard), so those two
roles are safe to bounce.

---

## 5. Verification

```bash
# From any VM, or via SSH:
curl -s http://localhost:9200/_cluster/health?pretty | grep status   # want: green (all 5 nodes joined)

curl -s 'http://localhost:9200/_cat/indices/infologger,generic-log-*?v'

# Verify tier placement — each index must land STRICTLY on its intended tier:
curl -s 'http://localhost:9200/_cat/shards/infologger,generic-log-*?v&h=index,shard,prirep,state,node'
```
Expected indices and placement (rendered by the native bootstrap, forked from
`init/opensearch/templates.sh`):

| index | shards | replicas | lands on |
|---|---|---|---|
| `generic-log-info-node-01-*` | 1 | **0** | worker node-01 only (`require.box: node-01`) — write alias `generic-log-info-node-01` |
| `generic-log-info-node-02-*` | 1 | **0** | worker node-02 only (`require.box: node-02`) — write alias `generic-log-info-node-02` |
| `generic-log-other-*` | 1 | **2** | storage tier only (`require.role: storage`) — write alias `generic-log-other` |
| `infologger-*` | 1 | **2** | storage tier only (`require.role: storage`) — write alias `infologger` |
| `cockpit-metrics` | 1 | **2** | storage tier only (`require.role: storage`) — health samples from the `alice-metrics` poller |
| `trend-rollup` | 1 | **2** | storage tier only (`require.role: storage`) — 10m per-entity aggregates from the `alice-trend-rollup` service |

The two per-worker `generic-log-info-*` indices are single-shard, zero-replica,
and hard-pinned to their own VM — if a worker dies its info index is lost with no
recovery (accepted: it is the disposable trash tier). The two storage indices are
9 shard copies balanced 3-per-node across the storage tier. `require` is a **hard**
rule: a shard that cannot satisfy it goes **unassigned** (cluster reports yellow)
rather than ever leaking onto the wrong tier.

**Dashboards:** `https://<control-VM-address>:5601` (control = `alice-ingest-3`),
basic-auth user `alice` (password = `vault_dashboards_basic_auth_password`),
self-signed cert (browser will warn — expected). The bootstrap (idempotent,
re-run on every deploy)
auto-provisions the three per-source index patterns (`infologger`,
`generic-log-info-*`, `generic-log-other`; `generic-log-info-*` is a wildcard so
both per-worker info indices appear together), plus the **unified**
`infologger,generic-log-*` pattern (set as the Discover default), the
`cockpit-metrics` pattern, seven seed saved searches, and the **ALICE Cockpit**
home dashboard — logs on top, a platform-health band (cluster status, per-index
state, Fluent Bit per node, Dashboards self-health) below, with drill-down links
to the bundled Index Management and Query Insights UIs — no manual setup. See
[`../docs/CARDBOARD-AIRPLANE-V3.md`](../docs/CARDBOARD-AIRPLANE-V3.md) and
[`../docs/CARDBOARD-AIRPLANE-V4.md`](../docs/CARDBOARD-AIRPLANE-V4.md).

**Gotcha — data is ~June 2026.** The replayed logs are historical (the
`RUN_TAG` in `group_vars/all.yml` pins a specific S3 replay window). If
Discover looks empty, the time range is almost certainly the problem — set it
to "Last 30 days" or an absolute window covering June 2026, not the live
"today" default.

---

## 6. Teardown

```bash
cd deploy
ansible-playbook teardown.yml
```
Deletes the 5 VMs (idempotent — a missing VM is not an error), re-closes the
`dashboards` security group's `5601/tcp` ingress rule and removes the group
itself, and deletes the local `inventory.generated.yml` so a later
`site.yml` run can never target a stale IP. It deliberately does **not**
touch `inventory.yml` (committed source of truth), the `masladoj-key`
keypair, or the `ssh` security group — all cheap to keep, reused across VM
generations.

---

## 7. Validation status

The v2 two-tier change surface was validated offline with a throwaway
`ansible-core` venv and the `deploy/requirements.yml` collections. Everything
below ran clean:

| Check | Result |
|---|---|
| `ansible-inventory --graph` on `inventory.yml` | pass — `alice_nodes` resolves to `workers` (2) + `storage` (3); `control` = `alice-ingest-3` (a storage node) |
| `ansible-playbook --syntax-check` on `site.yml`, `provision.yml`, `teardown.yml` | pass — zero syntax errors |
| `ansible-playbook site.yml --list-hosts` | pass — common/opensearch/gate target all 5, dashboards → control, collector + producer → the 2 workers only |
| `group_vars/all.yml` derivations (`ansible -m debug`) | pass — `node_count=2` (from `workers`); seeds/initial-managers/Dashboards-hosts = the 3 storage nodes; `opensearch_cluster_hosts` = all 5 (firewall mesh) |
| `opensearch.yml.j2` render (both tiers) | pass — workers get `node.roles: [data, ingest]` + `node.attr.role: worker` + `node.attr.box: <node_id>`; storage gets `[cluster_manager, data, ingest]` + `node.attr.role: storage` |
| `opensearch.yml.j2` ingest role | pass — every index sets `default_pipeline: alice-add-ingest-time`, and explicit `node.roles` drops the implicit `ingest` role, so both tiers list `ingest` (`[data, ingest]` / `[cluster_manager, data, ingest]`); workers stay ingest-capable so the local info path needs no cross-node hop for the pipeline |
| Native bootstrap render (`templates.sh.j2`, `patterns.sh.j2`) + `sh -n`, rendered under Ansible's real Jinja (`trim_blocks=True`) | pass — info template `generic-log-info-*` (1 shard/0 repl); `generic-log-other`+`infologger` carry `require.role: storage`; per-worker pre-creates emit `require.box` and are idempotent (`ensure_index` GET-then-PUT, no crash if the index already exists); all 6 JSON payloads parse; the OpenSearch mustache `{{{_ingest.timestamp}}}` is wrapped **inline** in `{% raw %}…{% endraw %}` (wrapping the whole heredoc glued `}` to the `JSON` terminator under `trim_blocks`, breaking the first PUT — inline wrapping leaves no newline for `trim_blocks` to eat) |

**Not validated** (requires the real infrastructure, out of scope for a
static/offline check): an actual `provision.yml` run against CERN OpenStack, an
actual `site.yml` run against real VMs, and therefore the live cluster health /
tier-placement / Dashboards / index verification steps in section 5 — in
particular that each shard lands **strictly** on its intended tier. Nothing was
skipped silently — this is the honest boundary of what can be checked without
CERN network access and real quota.

---

## 8. Open items

- **Alertmanager** — built (`roles/alertmanager`, single instance, no gossip
  HA: a restart is self-healing because the projector re-sends). External
  receivers are still absent by design; adding one is a receiver config change,
  not an architecture change.
- **`dds-other` value (out to Lubos).** The bootstrap folds `dds-other` into
  `generic-log-other` → storage tier (the safe default: keeping a possibly-valuable
  log is a cheaper mistake than trashing it). If dds turns out worthless
  end-to-end, re-cut so all dds stays on the worker tier. Nothing blocks on it.
- **Retention (ISM).** Still deferred, same as v1: `other` deserves longer
  retention than `info`, but meaningful retention needs time-based / rollover-aliased
  indices (a write-path change), not a settings tweak on these static index names.
- **Bootstrap assumes a fresh cluster.** The tier-aware index templates and
  `require.role`/`require.box` rules shape **new** indices only — OpenSearch does
  not retroactively re-settle existing shards when a template changes. v2 is a
  fresh-provision deliverable (different VM count and node roles than v1 — it is a
  new `make provision`, not an in-place v1→v2 upgrade), so this is the intended
  path. The per-worker index pre-creates are idempotent (skip if already present),
  so a control-VM rebuild against a surviving cluster is safe; but **reconciling
  allocation settings on already-populated `generic-log-other`/`infologger`
  indices** (e.g. if they were created before the storage-tier rules) is **not**
  done here and would need an explicit `PUT _settings` migration step.
- **Day-2 reconfiguration restarts all OpenSearch nodes at once (inherited from
  v1).** The initial bring-up play (`hosts: alice_nodes`, no `serial`) flushes the
  restart handler across all nodes together — deliberate and required for *fresh*
  cluster formation (quorum needs ≥2 managers up concurrently), but it means a
  later config change re-applied through the same play bounces every node,
  including all 3 storage managers, before the `serial: 1` gate play runs (which
  then has no pending handler left to flush). This is unchanged from v1 and is
  **not** a fresh-deploy blocker;   a proper rolling reconfiguration (serialize
  config+restart while still allowing concurrent first formation) is a deliberate,
  real-cluster-tested change left for a follow-up.
- **Quota ceiling.** v2 sits at exactly 10 cores / 5 instances (the binding
  constraint is instances, not cores — that is why the split is 2+3, not 3+3). A
  symmetric 3+3 tier with a spare needs an `instances` quota bump to 6.
- **Everything else in the LOCKED v2 topology and quality bar is implemented**:
  native no-Docker services, two-tier cluster (2 worker + 3 storage) hard-pinned
  by `require` shard-allocation filtering, per-worker local `generic-log-info-<node_id>`
  (1 shard/0 replicas/`require.box`), replicated `generic-log-other`+`infologger`
  on the storage tier (`require.role: storage`), worker-only Fluent Bit → local
  OpenSearch write path, single storage-host Dashboards/nginx/bootstrap, `epn_num % 2`
  slicing, 1 GB heap, inventory-driven discovery/publish/Dashboards-hosts Jinja
  (no IP typed twice), `serial: 1` health gate on first bring-up (see §4.2 — this is
  a health gate, **not** a rolling-restart guarantee for day-2 reconfiguration),
  vault-only secrets, tier-aware native bootstrap **forked** from the shared
  `init/` scripts (so `docker-compose.yml`/`docker-compose.mocks.yaml`/`images/**`/`init/**`
  stay untouched and the compose stacks keep working).

---

## 8. Detection layer runbook (wooden-plane)

Provisioned every `make deploy` by `bootstrap.yml`: monitors, detectors, ISM,
strict verify. Definitions live under
`roles/dashboards/files/{monitors,detectors}/`.

### Platform health is pushed, not scraped

`kind:fluentbit` documents are produced **by the collector itself**. Fluent Bit
runs a small `exec` input every 30 s that reads its own `:2020` metrics and
health endpoints over loopback, a Lua filter turns the cumulative counters into
the same `*_delta` fields the poller used to emit, and the existing OpenSearch
output writes the document into `cockpit-metrics`. Nothing scrapes worker
`:2020` from the control host any more; `fluent_bit_http_listen` is `127.0.0.1`
and the firewall rule that used to open the port is removed by the same task
that created it (`collector_metrics_scrape_open: false`).

> **Deviation from `HEALTH_METRICS_PLAN.md` § 3.1.** That section named two
> candidate inputs, `fluentbit_metrics` and `prometheus_scrape`, and left the
> choice to a Stage A implementability gate. Both emit *metric* chunks, which
> Lua filters do not process and which the OpenSearch output would ship in a
> metrics shape rather than the flat `cockpit-metrics` schema the cockpit,
> monitors and detectors already consume. The `exec` input emits an ordinary
> log record, so the whole existing filter/output chain applies unchanged and
> the schema contract in § 2 is genuinely preserved. It is still one daemon on
> the node and still the log ship path — the plan's actual argument.

`alice-metrics` on the control host keeps only the cluster-scoped kinds
(`cluster` / `index` / `node` / `osd`) plus the roster-derived `kind:fleet`
absence documents. `FB_TARGETS` is gone, and so is the info-index recreate,
which belonged to bootstrap rather than to health sampling — `templates.sh`
already rebuilds those aliases, including on the ops page's fresh reload.
The first deploy bootstraps the metrics index, monitors, detectors and
projector before configuring the workers. Those early verification passes
therefore do not require pushed heartbeats. After the collector role installs
and restarts Fluent Bit, a separate control-host gate waits for a fresh
heartbeat from every rostered collector and reruns the complete verifier with
the push contract enabled.
Detector provisioning updates changed definitions in place so unchanged IDs and
model state survive ordinary redeploys. OpenSearch deliberately forbids changing
an existing detector's category field, so a category migration is classified
separately: only that detector is stopped, deleted, recreated and started. This
is the required one-time path for the `ingest-flow` `node → collector_id` and
`node-health` `node → os_node` cutovers.

**Identity.** `node` was ambiguous by design: it meant the collector on
`kind:fluentbit` documents and the OpenSearch node on `kind:node` documents,
and on a worker VM both were `node-01`. Any label match on `node` alone would
have cross-suppressed unrelated conditions. `kind:fluentbit` now carries
`collector_id`, `kind:node` carries `os_node`, every monitor and detector reads
the explicit field, and `node` is no longer written to new metric documents.
Set `health_metrics_emit_legacy_node: true` to dual-write it during a staged
comparison window; nothing shipped reads it.

**Roster.** `make deploy` publishes an immutable topology snapshot into
`cockpit-fleet`: `collectors`, `assignments`, `topology_version` (a content
hash) and `effective_from`. Publication compares the computed version against
the *currently effective* snapshot: identical means no-op, different means a
new snapshot is appended with `_id = <effective_from>-<topology_version>` and a
`supersedes` pointer. Keying the document on the version alone would have been
wrong — going A → B → A would have collided with A's original document, kept
its old `effective_from`, and left B as the newest snapshot, so every later
signal would have been enriched against the wrong assignment map. Health monitors read only the latest effective snapshot;
the projector selects the snapshot effective at each signal's own event time
and never re-enriches an old signal from a newer roster. Nothing anywhere
recomputes a parent from `epn_num % NODE_COUNT` — that is replay placement, and
going from two collectors to three re-assigns two-thirds of hosts.

`roster_assignments` is configuration, not a live query, so that an unchanged
redeploy cannot mint a new version the first time a new EPN appears. Populate
it once from a real cluster:

```
make roster-discover        # prints YAML to review and commit into deploy/group_vars/control.yml
```

Until it is populated the assignment map is empty and every collector-scoped
decision falls closed to the `none` sentinel — no collector-scoped inhibition
is applied.

### Monitors (Layer 0 — `cockpit-metrics`)

| Monitor | Meaning | Action |
|---|---|---|
| `collector-down` | a **rostered** collector stopped heartbeating (absence, not an observed `fb_up:0`) | Check `fluent-bit` on that node; restart if dead |
| `collector-unhealthy` | `fb_healthy=0` for 2 min on a `collector_id` | Inspect Fluent Bit `/api/v2/health` and storage backlog |
| `cluster-red` | OpenSearch cluster status red | Check `_cluster/health` and unassigned shards |
| `shards-stuck` | `unassigned_shards > 0` for 5 min | Allocation explain; disk / node attrition |
| `data-loss` | `output_dropped_delta > 0` on a `collector_id` | Collector dropping — backpressure or OS down |
| `shipping-breaking` | `output_retries_failed_delta > 0` | OpenSearch reject/timeout path |
| `disk-cliff-warn` / `disk-cliff-page` | disk > 85% / > 92% on an `os_node` | Free disk on named node before read-only lock |
| `heap-spiral` | heap > 90% for 5 min on an `os_node` | GC death spiral risk; check load / queries |
| `telemetry-silence` | no `kind:cluster` **and** no `kind:osd` docs for 5 min | `alice-metrics` poller dead on control host |
| `fleet-fb-silence` | ≥ 50% of the roster missing heartbeats at once | Whole-fleet cause: credentials, network, OpenSearch rejecting writes |
| `ad-high-grade` | real-time RCF anomaly grade/confidence high | Open Anomaly Detection UI; correlate with Layer 0 |
| `signal-projector-stale` | `alice-signal-projector` stopped | **Break-glass** page. Nothing is re-sending to Alertmanager, so live incidents are resolving themselves |
| `alertmanager-down` | the projector cannot reach Alertmanager | **Break-glass** page. Alertmanager does not persist alerts |

**`telemetry-silence` is deliberately narrower than it used to be.** It used to
mean "zero `cockpit-metrics` documents", which after the push cutover is no
longer a meaningful question: collectors push their own heartbeats and keep
writing while the poller is dead. It now means exactly *the control-plane
sampler is dead*, and `fleet-fb-silence` means exactly *the fleet stopped
heartbeating*. One undifferentiated silence alert could scope neither
inhibition rule.

**`collector-down` is absence, not observation.** A dead Fluent Bit emits
nothing, so there is no `fb_up: 0` sample to find. The thin poller reads the
published roster, compares it against heartbeats seen in the last
`heartbeat_grace_seconds` (90 s), and writes one `kind:fleet` document per
rostered collector carrying `heartbeat_missing`. The monitor buckets those on
`collector_id`. The poller never manufactures `fb_up: 0` — that would put the
old single blind spot straight back.

### Trend rollup (`alice-trend-rollup` → `trend-rollup`)

Every trend monitor reads the **`trend-rollup`** index, never raw logs. The
`alice-trend-rollup` systemd service on the control node aggregates each closed
10-minute wall-clock bucket into one small document per entity:

```
ts entity family entity_kind doc_count ef_count fleet_count fleet_ef_count
entity_count p95_entry_lag_ms p95_shipping_lag_ms avg_entry_lag_ms avg_shipping_lag_ms
```

Five (family, entity) combinations are rolled up: `infologger`×`origin_host`,
`infologger`×`node`, `generic-log-other`×`origin_host`,
`generic-log-info-*`×`origin_host`, `generic-log-info-*`×`node`. Errors are
counted with one `severity_norm:(error or fatal)` filter across all three
families — no per-family severity enumeration anywhere in the lane. Document `_id` is deterministic
(`family.kind.entity.bucket_ts`), so each run re-writes the last
`trend_rollup_backfill_buckets` (3) buckets idempotently — late-arriving docs get
folded in and a restart self-heals without duplicates. One `family: _meta`
heartbeat document per combination per bucket carries `entity_count` and
`truncated`.

Why it exists:

- **Cost.** The 7d baseline is now a few thousand rollup docs per entity instead
  of a live scan over millions of log docs, so the monitors run every **10 min**
  on 2-vCPU nodes instead of every 30. This is Stage 7.4, built as an in-cluster
  service rather than an OpenSearch transform — see `docs/PLAN.md` Deviations.
- **`fleet_count` on every document** is what makes share-of-fleet possible. A
  bucket-level trigger can only read aggregations *inside* its own entity bucket,
  so the fleet total has to travel on the entity's own rows.
- **Retention.** Raw `generic-log-info` is kept 7d, which made a 7d raw baseline
  marginal. Rollups are tiny and kept `trend_rollup_retention_days` (30d) —
  pruned **by document** (hourly `_delete_by_query` on `ts`) inside the service,
  *not* by ISM. Every other index here uses whole-index delete-by-age; doing
  that to `trend-rollup` would drop the entire 7d baseline in one step every
  30 days and leave the whole trend lane silently inert for a week while it
  refilled. This is the one index where that failure mode matters, so it is the
  one index with doc-level retention.

`trend-rollup-stale` pages if no heartbeat lands for 40 min — a dead rollup
silently blinds the whole lane, so it is monitored like any other dependency.

### Trend monitors (deterministic — `trend-rollup`)

Severity warn. Each monitor runs every **10 min** and evaluates three consecutive
10-minute rollup buckets against a baseline of ~7d (24h fallback when 7d is
empty). The three slices are offset by 10 min (`-20m…-10m`, `-30m…-20m`,
`-40m…-30m`) so the newest bucket read is always complete; the baseline excludes
the last 40 min so the anomaly cannot dilute its own reference. The breach must
hold on **all three** slices in the **same direction** — one noisy window cannot
fire.

What each monitor actually compares:

- **Volume → share of fleet.** The metric is `sum(doc_count) / sum(fleet_count)`,
  this entity's share of everything logged in the same buckets. A fleet-wide ramp
  such as run start moves numerator and denominator together and cancels, so only
  a *disproportionate* host fires. Window length cancels too — no rate
  normalisation constants. An entity absent from a slice counts as share 0, so
  full silence is still caught.
- **Errors → share of that entity's own volume.** `sum(ef_count)/sum(doc_count)`,
  not an absolute count. A host that doubles traffic and doubles errors holds a
  flat error share and stays quiet, instead of double-alerting alongside the
  volume monitor.
- **Lag → p95, not mean.** `avg` of the per-bucket `p95_*_lag_ms`. Backlogs show
  in the tail before they move the average.

Guards:

- **Minimum counts.** Rising volume needs **≥50 docs in every slice**; rising
  errors need ≥50 docs **and** ≥10 error docs per slice. Without a floor a host
  whose baseline is a handful of documents trips on almost anything. An entity
  with no error history is compared against a 0.1% floor rather than dividing by
  zero.
- **Retired-host guard.** Collapse additionally requires a 24h baseline averaging
  ≥50 docs per bucket in which the entity appeared — "was alive yesterday, is
  quiet now". After 24h of silence a decommissioned host stops alerting by
  itself.
- **Lag record floor.** A p95 over a handful of records *is* the maximum, so the
  lag monitors also require **≥`trend_min_lag_docs` (100) records in every slice**.
  Without it the tail statistic silently degenerates to an outlier statistic exactly
  when the entity is quietest.
- **Lag floor.** `trend_lag_floor_ms` (default **250 ms**), substituted into the
  trigger scripts at bootstrap. With `collector_time` now stamped at
  millisecond resolution (see below) the old 2000 ms quantization floor is no
  longer needed; raise the variable, not the JSON, if the first soak shows
  residual jitter.
- **Entity cap.** The rollup pages its composite aggregation but stops at
  `trend_rollup_max_entities` (2000); each monitor's own composite is capped at
  2000. `trend-entity-cap` warns at `trend_entity_cap_warn` (1800) or on any
  `truncated` heartbeat, so silent blindness past the ceiling becomes an alert
  rather than a surprise.

#### Calibration (measured from the real S3 archive, 2026-07-27)

The floors are not guesses. Counted directly from `s3://epn-backup-logs/infologger-2026/`,
where **each object is one calendar day** for the whole fleet.

**Daily volume spans 220×.** Sampling only small objects gives a badly wrong picture,
so the range matters more than any single day:

| object | day | rows | fleet rate | median host per 10 min |
|---|---|---|---|---|
| `p104` | 2026-04-01 | 254,003 | 2.9/s | ~2 |
| `p100` | 2026-03-28 | 426,063 | 4.9/s | ~3 |
| `p102` | 2026-03-30 | 3,710,214 | 43/s | **112** |
| `p72` | — | 7,935,449 | 92/s | — |
| `p149` | — | **55,592,675** | **643/s** | — |

Object sizes: 179 non-empty, median 6.4 MB, p75 17 MB, max 584 MB. Compression varies
(33k–95k rows per compressed MB), so extrapolating rows from size is only a rough guide.

**The replay pacer mimics a busy day, not a typical one.** `il_replay_rate: 500` sits
just under the busiest real day (643/s) and ~150× above a quiet one. Anything calibrated
against the replay is calibrated against data-taking conditions — which is the regime
worth alerting on.

**The 50-line floor gates by activity, and that is the intent.** On a data-taking day a
median host produces ~112 lines per 10-minute bucket, comfortably above the floor. On an
idle day it produces ~2, and the rate rule correctly goes silent — there is genuinely
nothing to measure. Do **not** widen the bucket to compensate: it would trade fast
detection during data-taking for a rule that fires on statistical noise while idle. The
gap that leaves — a host dying while the fleet is quiet — is a *presence* question, not a
rate question, and wants its own rule.

**The 10-error floor sits at a fleet-typical error rate.** Fleet error share is highly
variable across days: 3.2 % (`p104`), 23.8 % (`p100`), 7.2 % (`p102`). At ~330 lines per
bucket, 10 errors is a 3.0 % share — the bottom of that range. That variability is also
why error share is compared against *this host's own* 7-day history rather than any fixed
number. The 0.1 % divide-by-zero floor is well below all of it.

**Host count is stable and larger than assumed: 211, 214 and 215** distinct hostnames on
the three days counted — not the ~31 this project assumed. See the HCAD sizing note under
Detectors.

**Concentration is a quiet-day artefact.** `epn-infra12` is ~45 % of the stream on quiet
days but only 6.8 % on the busy one, where load spreads across the EPNs. Share-of-fleet is
still the right metric — it is what makes a fleet-wide run-start ramp cancel — but not
because one host dominates.

Generic families do not have this problem: DDS is **2,013 objects, one per EPN, each
~99 MB**, so per-host volume is near-uniform.

| Monitor | Family | Entity | Metric | Enabled |
|---|---|---|---|---|
| `trend-il-volume` | `infologger` | `origin_host` | share of fleet volume | yes |
| `trend-il-ef` | `infologger` | `origin_host` | error share of own volume | yes |
| `trend-il-entry-lag` | `infologger` | `origin_host` | p95 `enter_system_lag_ms` | yes (self-gating) |
| `trend-il-shipping-lag` | `infologger` | `node` | p95 `ingest_lag_ms` | yes |
| `trend-other-volume` | `generic-log-other` | `origin_host` | share of fleet volume | yes |
| `trend-other-errors` | `generic-log-other` | `origin_host` | error share of own volume | yes |
| `trend-info-volume` | `generic-log-info-*` | `origin_host` | share of fleet volume | yes |
| `trend-info-entry-lag` | `generic-log-info-*` | `origin_host` | p95 `enter_system_lag_ms` | yes (self-gating) |
| `trend-info-shipping-lag` | `generic-log-info-*` | `node` | p95 `ingest_lag_ms` | yes |
| `trend-rollup-stale` | — | — | rollup heartbeat absent 40 min | yes |
| `trend-entity-cap` | — | — | entity ceiling approached | yes |

**Entry-lag monitors gate themselves — no cutover step.** Entry lag is
`collector_time − @timestamp`: under preserved June replay that is *archive age*,
about a month, and says nothing about pipeline health. The first design shipped
these two disabled behind a variable to be flipped at production cutover. That was
wrong: an alert that must be manually switched on is a silent gap if anyone forgets,
which is the exact failure mode the rest of this lane is built to avoid.

Instead the rule detects the nonsense itself. Any slice above
`trend_entry_lag_ceiling_ms` (1 h) cannot be pipeline health, so the trigger returns
false. Under replay entry lag is ~1 month → always over the ceiling → naturally
silent. In production entry lag is seconds → never near it → naturally live. Both
monitors ship **enabled**, and `verify_detection.py` now fails if any trend monitor
is disabled. Shipping-lag monitors have no ceiling: a multi-hour shipping backlog is
real and should page.

The matching *detectors* stay on regardless — an anomaly score is advisory and shows
up on a panel, where noise is cheap; a throttled page is not.

Alerts appear in Dashboards → Alerting and on the Cockpit **Detection** panels.
`/ops` headlines open incidents and signals firing, with active-alert and
anomalies-last-hour counts beside them. Notifications go through Alertmanager
to `alice-notification-ingest`; no external channel yet, which is now a
receiver config change rather than an architecture change.

### Detectors (Layer 0.5 / 1)

**Normalized fields (`severity_norm`, `origin_host`).** The collector's last
filter stamps two fields on every record, so nothing downstream enumerates
per-family vocabularies any more:

| raw `severity` | source | `severity_norm` |
|---|---|---|
| `I` `W` `E` `F` `D` | infologger | `info` `warning` `error` `fatal` `debug` |
| `Info` `Warning` `Error` `Fatal` `Sys` | stdout | `info` `warning` `error` `fatal` `system` |
| `inf` `err` `cout` | dds | `info` `error` `info` |
| absent (free-form stdout) | stdout | `unknown` |

`origin_host` copies `hostname` (infologger) or `host` (generic) — one field
meaning "the EPN this log was born on" across all three families. Both are
mapped explicitly in the component templates; `infologger` is `dynamic: strict`,
so a missing mapping would reject every document, and `verify_detection.py`
asserts both fields are present.

`rewrite_tag` routing deliberately still keys on the **raw** `severity`. Routing
decides which tier a document lands on, and `dds:cout` normalizes to `info`
while currently routing to `generic-log-other`; switching the rules to
`severity_norm` would silently move data between the storage and worker tiers.
That is a separate, deliberate decision — not a side effect of normalization.


Entity rule: `ingest_lag_ms` → collector `node`; everything EPN-scoped → `origin_host` (one field on all three families, so the rule no longer branches per family). Lag-only detectors have no ZERO imputation. `enter_system_*` AD is valid in production; under preserved replay scores reflect archive age (expected) — detectors still run.

`collector_time` is stamped **millisecond-resolution** at the head of the Fluent Bit
filter chain, from the event's own arrival timestamp (`time_as_table`), not from
`os.time()`. The old whole-second stamp made `ingest_lag_ms` mostly quantization —
and biased it high by ~500 ms, since truncation put `collector_time` before the true
accept time — which is why the `*-shipping-lag` detectors and monitors used to be
meaningful only for multi-second backlogs. Sub-second shipping latency is now
measurable; the first soak should confirm the distribution is continuous rather
than clustered on second boundaries.

| Detector | Signal |
|---|---|
| `ingest-flow` | collector throughput / errors / retries |
| `node-health` | heap, CPU, indexing delta, disk |
| `dashboards-health` | OSD event-loop / latency / requests |
| `il-per-epn` (+ `-slow`) | InfoLogger per-`origin_host` volume, error count |
| `il-per-epn-entry-lag` (+ `-slow`) | InfoLogger per-`origin_host` `p95(enter_system_lag_ms)` |
| `il-collector-shipping-lag` (+ `-slow`) | InfoLogger per-`node` `p95(ingest_lag_ms)` |
| `other-per-epn` (+ `-slow`) | generic-other per-`origin_host` volume / errors |
| `info-volume` (+ `-slow`) | generic-info per-`origin_host` volume |
| `info-per-epn-entry-lag` (+ `-slow`) | generic-info per-`origin_host` `p95(enter_system_lag_ms)` |
| `info-collector-shipping-lag` (+ `-slow`) | generic-info per-`node` `p95(ingest_lag_ms)` |

**17** detectors total (3 metrics + 14 log). RCF needs a few hundred intervals before scores are meaningful (~3–5 h at 1 min).

**Lag detectors use real p95.** The eight lag detectors moved from `avg` to a
`percentiles` aggregation with a single percent. Two constraints make this a trap,
so don't "tidy" it:

- **Exactly one entry in `percents`.** The plugin reads the *first* percentile from
  the iterator, and percentiles iterate ascending. Ask for `[50, 95]` and you
  silently get the median, with no error anywhere.
- **Never set `"method": "hdr"`.** Only the default TDigest implementation is
  handled; the HDR one falls through to `Failed to parse aggregation`.

(An earlier version of this section claimed percentiles were impossible as a
detector feature and used `max` instead. That was wrong — the plugin handles
`InternalTDigestPercentiles` explicitly. `max` was also the worse signal: one
garbage-collection pause moves it, so the model learns a wide noisy normal and gets
less sensitive to the real thing.)

**HCAD sizing — measured, and a real risk.** The archive has **211–214 distinct
hostnames**, not the ~31 this plan has been assuming. Ten EPN-scoped detectors
× ~214 entities is well past the ~256-model budget `docs/PLAN.md` estimated.
Run `_profile` on the first soak *before* trusting any of the log detectors;
the likely mitigations are dropping the `-slow` twins for EPN-scoped signals, or
filtering detectors to the hosts that carry meaningful volume.
Profile: `GET _plugins/_anomaly_detection/detectors/<id>/_profile`.

### Replay clock

Dual-clock (`collector_time` wall clock + preserved `@timestamp`) is what unlocks
log AD and a valid `ingest_lag_ms` (collector → OpenSearch) on preserved replay.
`enter_system_lag_ms` is implemented for production but is archive-age (huge) under
preserved June timestamps — expected.

- `make replay` / `make replay-fresh` → `replay_clock=preserved`: the archive's
  own March/June `@timestamp`, which is what production looks like. AD is
  unaffected — all 17 detectors key on `collector_time` or a metrics document's
  `@timestamp`, both stamped in this stack, so they see a live stream either way.
- `make replay-shifted` → every event timestamp slid forward by one constant
  offset so `@timestamp` lands near now. Two costs, both real: it collapses
  `enter_system_lag_ms` (= `collector_time - @timestamp`, the EPN → collector
  latency) to a constant, so the four `*-entry-lag` detectors train on nothing;
  and a constant offset preserves the archive's span, so its later months land
  in the **future** and fall outside any cockpit window ending at `now`.
- Unit default in `group_vars` is `preserved`, so the ops page's replay button —
  which POSTs the worker trigger directly and never goes through Ansible —
  matches `make replay-fresh`.
- The detectors carry a month-scale latency guard precisely so preserved
  timestamps do not fire alerts during replay while the signal stays valid in
  production.

### Retention — rolling window, never a wipe

Every log family writes to a **write alias**, not to a concrete index. `infologger`
is a nickname pointing at `infologger-000001`; ISM rolls it to `-000002` when the
current index turns `log_rollover_period` old (or hits `log_rollover_max_size`,
whichever comes first), and deletes each backing index once *it* passes the
retention age. Old data falls off the back one index at a time.

Nothing in the stack has to know: writes go to the alias, and reads through the
alias, a wildcard or a Dashboards index pattern see every backing index. Fluent
Bit outputs, detectors, monitors and saved objects were not changed.

The guarantee is one subtraction:

> **window you always have = retention − rollover period**

| Index | Rolls | Backing index deleted at | Always have | Shard copies |
|---|---|---|---|---|
| `infologger` | 7d / 20 GB | 56d | ≥49d | 8 × 3 = 24 |
| `generic-log-other` | 7d / 20 GB | 35d | ≥28d | 5 × 3 = 15 |
| `generic-log-info-<node>` | **1d** / 20 GB | 8d | ≥7d | 8 × 1 per worker |
| `cockpit-metrics` | — | 7d, **by document** | exactly 7d | 3 |
| `trend-rollup` | — | 30d, **by document** | exactly 30d | 3 |
| AD result history | plugin-rolled | 14d | — | small |
| alert history | plugin-rolled | 30d | — | small |

**The bulk info family rolls daily, not weekly.** Rollover period sets how much
*extra* data you carry: to guarantee 7 days you must retain `7d + rollover
period`, so a weekly roll would mean holding up to 14 days of the highest-volume,
lowest-value family. Rolling it daily (`log_rollover_period_info`) gets the same
7-day guarantee with a peak of 8 days — nearly halving worker disk. The price is
8 shards per worker instead of 2, which those nodes can easily afford since they
hold nothing else. The reverse trade applies to `infologger`: low volume, high
value, so a weekly roll and its coarser granularity cost almost nothing.

**Why the two small indices prune by document instead.** Deleting a whole index
is nearly free; deleting documents forces the engine to rewrite data files. That
makes rollover the right tool for the log firehose and the wrong tool for a small
index — `trend-rollup` at daily rollover would be 31 indices × 3 copies = 93
shards to avoid deleting ~40k tiny documents a day. Both small indices prune
hourly instead (`alice-trend-rollup` and `alice-metrics` each do their own), which
gives an exact window at no meaningful cost.

**Shard budget is the binding constraint, not disk.** Every index costs heap just
by existing; the rule of thumb is ~20 shards per GB, so ~60 across the 3-node
storage tier at 1 GB heap each. That is why `infologger` and `generic-log-other`
dropped from **3 primaries to 1** — at 3 primaries, eight weekly `infologger`
indices alone would be 72 shards. Current storage-tier total is ~45. Each extra
week of `infologger` retention costs 3 more shards.

**Migrating an existing cluster (one-time).** An alias cannot share a name with a
real index, so a cluster that already has a concrete `infologger` keeps the old
whole-index-wipe behaviour and the bootstrap prints a loud `WARN`;
`verify_detection.py` repeats it. Convert once — this **deletes** the old index,
which is fine here because the data is a replay you can re-run:

```
make deploy-migrate-rollover
```

After `generic-log-info-*` indices are deleted, `alice-metrics` recreates the
box-pinned write alias if it is missing.

### Signals, incidents and notification

```
  alert current + history indices  ─┐
  .opendistro-anomaly-results*     ─┤   (the alerting API is a reconciliation
  cockpit-fleet roster snapshots   ─┘    oracle, never the ingest path)
                 │
                 ▼
       alice-signal-projector          ← domain semantics
          │              │
          ▼              ▼
    alice-signals   alice-incidents     ← the durable record
          │
          ▼ (re-sends every active signal on a cadence under resolve_timeout)
     Alertmanager                       ← notification semantics only
          │
          └──► alice-notification-ingest ──► alice-notifications
```

**What an episode is.** An *incident* is one episode: the signals that share a
cause, with a count, entity samples and references back to every constituent
row. It is the record. `incident_id` is the stable key for a
(source, alertname, entity, scope); each time that condition re-opens after
resolving, a new **episode** is appended, identified by `episode_id =
<incident_id>.<episode_start>` where `episode_start` is the event time the
episode opened. That key is derived from event time rather than from a counter
deliberately: a counter would have to be read from whatever state happened to
be loaded, and the fifteen-minute history overlap re-reads the same rows every
thirty seconds — so a counter churned out a new incident document per cycle,
and reset to 1 once the previous terminal alert aged out of the window. Signals
and Alertmanager annotations both carry `episode_id`, so a notification for a
resolved episode can never mark a still-firing one as covered.

Rows are assigned to episodes by **time boundary**, never by "whichever episode
is currently open": the projector loads each incident's episode timeline and
picks the episode whose `episode_start` is the greatest not after the row's own
event time. Assigning to the newest open episode instead would restamp an older
breach onto a newer one every time the overlap re-read it — and because
`alice-signals` uses deterministic source ids, that overwrite is silent.

**Membership is idempotent.** Current alerts are fully re-scanned every cycle
and the anomaly overlap window deliberately re-reads results, so both lanes
dedupe: `member_count` is the size of the deduped `signal_ids` set, a replayed
firing row does not re-count, and a replayed *healthy* result does not advance
recovery — `last_healthy_window` and `last_breach_window` make the state
machine advance only on windows it has not already consumed. Without that, the
three-minute overlap alone would manufacture the K healthy windows needed to
resolve an episode. Alertmanager decides *when someone is told*;
`alice-incidents` decides *what is true*. Alertmanager does not persist alerts
and never becomes the database.

**How to see an incident's members.** `/ops` headlines open incidents; the
cockpit's Incidents panel does the same and the `alice-signals` saved search
sits directly beneath it. Every signal row carries the `incident_id` that ties
it back. Grouping never destroys a row — that is the property the S3 scorecard
calls *signal reconciliation*, and it is scored on every injection run.

**How to tell a suppressed signal from an absent one — partially.** A signal
that was suppressed is still present in `alice-signals` with its
`incident_id`; a signal that is absent from that index at all is a bug in the
projection lane, and `make inject` scores exactly that as *signal
reconciliation*. What is **not implemented** is attributing a suppression to
the rule that caused it: `suppressed_by` and `suppressed_count` are written as
the sentinel and zero, and nothing populates them. The ordinary Alertmanager
webhook reports notification *batches* and carries no indication of what was
silenced or inhibited; the only source that does is Alertmanager's
**experimental, feature-flagged** event recorder. The receiver exposes an
`/events` endpoint that would store such records as `record_kind: event`, but
nothing is configured to send to it, and an experimental facility must not
become the source of record. Until that gap is closed, read a suppression by
comparing the incident's members against what the notification covered — and
note that with inhibition off by default (below) nothing is being suppressed
in the first place.

**How to place a silence before maintenance.** Alertmanager is reachable behind
the same nginx vhost at `/alertmanager/` (same basic-auth as Dashboards). Place
a silence there — matching e.g. `cluster_id="alice-logs"` — before a
`make replay-fresh`, a deploy, or a press of the ops page's clear button.
Those are all windows where alerts are expected and should be muted
deliberately rather than ignored by habit.

**Inhibition ships OFF, because nothing has been demonstrated yet.**
`alertmanager_proven_inhibit_rules` is `[]`, so Alertmanager runs with an empty
`inhibit_rules` block and mutes nothing. S6 admits one rule at a time and only
after an injection run has shown its direction, its timing and a
false-inhibition score of zero, and zero injection runs have happened. Three
rule bodies are written and gated in the role template — `collector-down`
inhibits its own children on `equal: [cluster_id, collector_id]`; control-plane
silence inhibits the monitors fed by `cluster`/`index`/`node`/`osd` samples;
fleet FB silence inhibits the `kind:fluentbit`-fed ones — and each is enabled
by adding its name to that list once `make inject` has produced the evidence. `cluster-red` and `data-loss` are deliberately **not**
suppressors: `data-loss` is usually an impact rather than a cause, and disk
pressure can *produce* cluster-red, so the presumed causal direction is
reversible and a rule pointing the wrong way mutes the alert you needed. Every
label is always present with an explicit value (`none` / `all`, never omitted),
because Alertmanager treats a missing label and an empty one as the same thing
and an `equal:` rule applies when all its listed labels are absent from both
alerts.

**Break-glass.** `signal-projector-stale` and `alertmanager-down` cannot page
*through* the components they report dead, so those two monitors — and only
those two — post straight to `alice-notification-ingest`, tagged
`delivery_path: breakglass`. Nothing else may use that sink. The injection
scorer validates this path separately from ordinary Alertmanager notifications:
those two alert names are allowed without an episode link, any other name on the
break-glass path fails the run, and `stop-projector` requires a
`signal-projector-stale` delivery.

**Mass silence pages.** Fleet-wide silence cannot by itself distinguish "the run
ended" from "the farm is gone". The projector classes it
`unknown-mass-silence` and it pages. A firing `fleet-fb-silence` implies that
class on its own — its predicate already establishes that at least
`fleet_silence_fraction` of the roster is quiet — while individual
`collector-down` rows are counted against roster size. Folding the fleet alert's
constant `entity_id: all` into that count made the class depend on roster size
and disappear above two collectors. Only authoritative `run_id` / `run_active`
/ `phase` telemetry may downgrade it, and that telemetry does not exist yet.

### Fault injection — where the numbers come from

Every grouping and inhibition threshold is a percentage or a delay over storm
behaviour, so it is measured, not chosen:

```
make inject SCENARIO=kill-fluent-bit        # scenario 1 — sets group_wait
make inject SCENARIO=drop-epn-stream -e independent_entity=epn001
                                            # scenario 2 — independent-event recall
make inject SCENARIO=cpu-stress-worker      # scenario 3 — collector -> child direction
make inject SCENARIO=stop-alice-metrics     # scenario 5 — suppressor precedence
make inject SCENARIO=replay-end             # scenario 4 — mass-silence classification
make inject SCENARIO=stop-projector         # the S5 re-send gate
```

Each run injects, observes, restores, and prints a seven-metric scorecard —
signal reconciliation, independent-event recall, incident purity,
fragmentation, time-to-notify, time-to-resolve, false inhibition. **The
scorecard gates: a lossy reconciliation, an impure incident, fragmentation, a
non-zero false-inhibition score, or an absorbed independent event exits
non-zero and fails the play.** Pass `-e injection_strict=false` to report
without gating. Run each scenario at least twice and paste the scorecards under
§ Calibration below. Notification-volume reduction alone is not a pass: that
number rewards suppressing everything, which is why it is reported but never
gated on. A page remains accountable after it resolves: `alice-signals` is a
deterministic upsert whose final state may be terminal by scoring time, but the
alert still had to reach a human while it was firing. The `stop-projector`
scenario also waits for a `projector_cycle_ok: 1` heartbeat newer than the
restart boundary before scoring; a running process without a completed catch-up
cycle is not recovery.

> **Deviation on scenario 2.** `GROUPING_PLAN.md` words it as "drop one EPN's
> file mid-replay". A file-level drop is not usable here: `replay.py`'s
> `_write_lines` has no per-host error handling, so a failed write kills the
> whole dds+stdout family rather than one host, and simply deleting the file
> makes Fluent Bit re-read the recreated inode from the head — duplication, not
> silence. `drop-epn-stream` instead installs a temporary ingest pipeline that
> drops records whose `origin_host` is the named EPN, which produces the
> specified observable exactly — that host silent, the rest of the fleet
> unaffected — and reverses cleanly.

#### Calibration — absence-era timings and storm shapes

> **Not yet measured.** No gate in this section has been observed on the real
> VMs. The detection layer's own first soak (`docs/PLAN.md` § 0) has not run
> either, so nothing below can be filled in honestly yet. The values currently
> shipped are **design-derived placeholders**, and they are marked as such in
> `group_vars/all.yml`. Do not treat them as measurements, and do not carry any
> pull-era number across the push cutover.

| Quantity | Shipped value | Basis | Measured |
|---|---|---|---|
| `heartbeat_grace_seconds` | 90 s | one poller interval plus margin, inside the ~2 min page budget | — |
| kill-FB → `collector-down` page | ≤ ~2 min by design | grace (90 s) + poller tick (30 s) + monitor schedule (60 s) | — |
| kill-FB → first child symptom | unknown | children are the 10-minute trend monitors | — |
| `alertmanager_group_wait` | 3 m | worst-case absence lag, so the inhibiting alert arrives first | — |
| `alertmanager_group_interval` | 10 m | matches the `trend-*` monitor schedule | — |
| `alertmanager_resolve_timeout` | 5 m | set explicitly; the projector's 30 s re-send is derived from it | — |
| `fleet_silence_fraction` | 0.5 | half the roster | — |
| `plugins.alerting.max_actionable_alert_count` | 50 | pinned so the per_alert → per_execution rewrite point is known | — |
| throttle behaviour under `per_execution` | unknown | read in upstream source, never observed here | — |
| S3 scenario shapes (×5) | unknown | `make inject` produces them | — |
| seven-metric scorecard (×5) | unknown | requires the projector, so Phase 4 not Phase 3 | — |

### Re-measure window delay

The shipped log detectors use a **2-minute window delay that has never been measured** —
it is a placeholder, not a derived value. On the first real-VM soak, query p99
`ingest_lag_ms` per family on a fresh replay (shipping lag, not archive age) and bump
`ad_log_window_delay_minutes` in `group_vars/all.yml`. Repeat after any topology change,
or shingles skip.
