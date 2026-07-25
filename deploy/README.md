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
│ [alertmanager slot]│   │                    │   │                    │
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
- **Alertmanager is a reserved seam, not built.** The nginx vhost leaves an
  explicit "when it lands, it's another control-host-only service behind this
  nginx" seam. Nothing elsewhere needs to change to add it later.

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
| `generic-log-info-node-01` | 1 | **0** | worker node-01 only (`require.box: node-01`) |
| `generic-log-info-node-02` | 1 | **0** | worker node-02 only (`require.box: node-02`) |
| `generic-log-other` | 3 | **2** | storage tier only (`require.role: storage`) |
| `infologger` | 3 | **2** | storage tier only (`require.role: storage`) |
| `cockpit-metrics` | 1 | **2** | storage tier only (`require.role: storage`) — health samples from the `alice-metrics` poller |

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

- **Alertmanager** — reserved seam only (`alertmanager_port` commented out in
  `group_vars/all.yml`; the dashboards role's nginx vhost has an explicit
  "when it lands" comment block). Not built, by design (LOCKED topology).
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

### Monitors (Layer 0 — `cockpit-metrics`)

| Monitor | Meaning | Action |
|---|---|---|
| `collector-down` | Fluent Bit `fb_up=0` on a worker | Check `fluent-bit` on that node; restart if dead |
| `collector-unhealthy` | `fb_healthy=0` for 2 min | Inspect Fluent Bit `/api/v2/health` and storage backlog |
| `cluster-red` | OpenSearch cluster status red | Check `_cluster/health` and unassigned shards |
| `shards-stuck` | `unassigned_shards > 0` for 5 min | Allocation explain; disk / node attrition |
| `data-loss` | `output_dropped_delta > 0` | Collector dropping — backpressure or OS down |
| `shipping-breaking` | `output_retries_failed_delta > 0` | OpenSearch reject/timeout path |
| `disk-cliff-warn` / `disk-cliff-page` | disk > 85% / > 92% | Free disk on named node before read-only lock |
| `heap-spiral` | heap > 90% for 5 min | GC death spiral risk; check load / queries |
| `telemetry-silence` | no `cockpit-metrics` docs for 5 min | `alice-metrics` poller dead on control host |
| `ad-high-grade` | RCF anomaly grade/confidence high | Open Anomaly Detection UI; correlate with Layer 0 |

### Trend monitors (deterministic — log indices, `collector_time`)

Recent = avg over ~6h; baseline = ~7d excluding recent (24h fallback). Fire at ≥2× (volume also ≤0.5× collapse). Severity warn. Run-start spikes may false-warn until gated.

| Monitor | Index | Entity | Metric |
|---|---|---|---|
| `trend-il-volume` | `infologger` | `hostname` | doc volume |
| `trend-il-ef` | `infologger` | `hostname` | E/F count |
| `trend-il-entry-lag` | `infologger` | `hostname` | `avg(enter_system_lag_ms)` |
| `trend-il-shipping-lag` | `infologger` | `node` | `avg(ingest_lag_ms)` |
| `trend-other-volume` | `generic-log-other` | `host` | volume |
| `trend-other-errors` | `generic-log-other` | `host` | error count |
| `trend-info-volume` | `generic-log-info-*` | `host` | volume |
| `trend-info-entry-lag` | `generic-log-info-*` | `host` | `avg(enter_system_lag_ms)` |
| `trend-info-shipping-lag` | `generic-log-info-*` | `node` | `avg(ingest_lag_ms)` |

Alerts appear in Dashboards → Alerting and on the Cockpit **Detection** panels.
`/ops` shows active-alert and anomalies-last-hour counts. No external channel yet
(nginx alertmanager slot remains the future seam).

### Detectors (Layer 0.5 / 1)

Entity rule: `ingest_lag_ms` → collector `node`; `enter_system_lag_ms` → EPN (`hostname`/`host`). Lag-only detectors have no ZERO imputation. `enter_system_*` AD is valid in production; under preserved replay scores reflect archive age (expected) — detectors still run.

| Detector | Signal |
|---|---|
| `ingest-flow` | collector throughput / errors / retries |
| `node-health` | heap, CPU, indexing delta, disk |
| `dashboards-health` | OSD event-loop / latency / requests |
| `il-per-epn` (+ `-slow`) | InfoLogger per-`hostname` volume, E/F |
| `il-per-epn-entry-lag` (+ `-slow`) | InfoLogger per-`hostname` `avg(enter_system_lag_ms)` |
| `il-collector-shipping-lag` (+ `-slow`) | InfoLogger per-`node` `avg(ingest_lag_ms)` |
| `other-per-epn` (+ `-slow`) | generic-other per-`host` volume / errors |
| `info-volume` (+ `-slow`) | generic-info per-`host` volume |
| `info-per-epn-entry-lag` (+ `-slow`) | generic-info per-`host` `avg(enter_system_lag_ms)` |
| `info-collector-shipping-lag` (+ `-slow`) | generic-info per-`node` `avg(ingest_lag_ms)` |

**17** detectors total (3 metrics + 14 log). RCF needs a few hundred intervals before scores are meaningful (~3–5 h at 1 min).
Profile: `GET _plugins/_anomaly_detection/detectors/<id>/_profile`.

### Replay clock

Dual-clock (`collector_time` wall clock + preserved `@timestamp`) is what unlocks
log AD and a valid `ingest_lag_ms` (collector → OpenSearch) on preserved replay.
`enter_system_lag_ms` is implemented for production but is archive-age (huge) under
preserved June timestamps — expected.

- `make replay` / `make replay-fresh` → currently still pass `replay_clock=shifted`
  (Discover “live stream” cosmetics; **optional**, not required for AD).
- `make replay-preserved` → historical June `@timestamp` (Discover / backtests;
  AD works via `collector_time`).
- Unit default in `group_vars` stays `preserved`.

### Retention (ISM)

Whole-index delete by age: info 7d, other 30d, infologger 90d, cockpit-metrics 7d,
AD result histories 14d, alert history 30d. After info-index delete, `alice-metrics`
re-creates the box-pinned `generic-log-info-*` indices if missing.

### Re-measure window delay

After topology changes, query p99 `ingest_lag_ms` per family on a fresh replay
(shipping lag, not archive age) and bump `ad_log_window_delay_minutes` in
`group_vars/all.yml` if shingles skip.
