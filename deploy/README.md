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
│ alice-fault-agent (control-triggered)         │ alice-fault-agent
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
│ alice-metrics      │   │ alice-trend-rollup │   │ alice-live-lane     │
│ alertmanager       │   │ alice-signal-      │   │                    │
│ notification-ingest│   │ projector          │   │                    │
│ alice-inject       │   │ alice-fault-agent  │   │                    │
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
index pre-creates, Dashboards index patterns). The heavier Python traversal is
not co-located with that UI stack: `alice-signal-projector` runs on
`alice-ingest-4`. The live lane runs on `alice-ingest-5`.

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
  only and reachable at `/alertmanager/` behind the same TLS and basic-auth.
  Its raw port is admitted by firewalld only from the node-04 projector host;
  this internal path lets the memory-heavy projector stay off node-03 without
  exposing Alertmanager generally. It owns notification semantics only — grouping
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

One command from the repo root builds a self-contained venv (`.venv/`, gitignored —
but see "the toolchain is kept off AFS" below for where it goes on lxplus) with
every control-node dependency and installs the Galaxy collections:

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

`make provision`/`deploy`/`teardown` use that venv automatically (falling back to
an already-activated venv on `PATH` if it is absent), so after
`make bootstrap` you don't need to activate anything. Override the location with
`make bootstrap VENV=/path/to/venv` (pass the same `VENV=` to the other targets).

**On lxplus the toolchain is kept off AFS, and that is deliberate.** When the
checkout is under `/afs/...` the Makefile puts the venv in
`$TMPDIR/alice-ingest-$USER/venv` (so `/tmp/alice-ingest-masladoj/venv`) and points
`ANSIBLE_LOCAL_TEMP` and `ANSIBLE_COLLECTIONS_PATH` at siblings of it. A checkout
anywhere else is untouched: still `./.venv`, still Ansible's own defaults.

A deploy is an unusually hostile AFS workload. `site.yml` forks one Ansible worker
per host; each worker imports several hundred small files out of the venv and the
four Galaxy collections, and writes its compiled module payload into `local_tmp` —
which defaults to `~/.ansible/tmp`, also AFS. AFS answers that load with
`[Errno 5] Input/output error` often enough to kill a run mid-play, and it does not
fail in a way that names the cause. In the run that prompted this change, Jinja2
plugin loading failed and the very next task died claiming a variable was undefined:

    [WARNING]: An unexpected error occurred during Jinja2 plugin loading:
    [Errno 5] Input/output error: b'.../.venv/lib64/python3.9/site-packages/ansible/plugins/filter'
    fatal: [alice-ingest-3]: FAILED! => {"msg": "The field 'environment' has an invalid
    value, which includes an undefined variable. The error was:
    'opensearch_http_port' is undefined ..."}

`opensearch_http_port` is set in `group_vars/all.yml`, and four earlier tasks in that
same file had already resolved it in that same run. Nothing was undefined — the
templating engine was. Treat any mid-deploy "undefined variable" naming a variable
that is plainly defined as an AFS I/O failure, not a playbook bug. Confirm with
`fs listquota ~` (a full home volume behaves the same way), `tokens`, and
`fs checkservers`.

The cost is that local disk is per-machine, and lxplus is a load-balanced alias. A
session on a different lxplus node has no venv, and `make deploy` then stops in
pre-flight, before it contacts any VM, with a message saying to re-run
`make bootstrap` — about a minute. That trade is the point: a missing venv fails
cleanly at pre-flight, while an AFS venv fails at task 137 of a run that has already
half-configured the cluster.

The repo itself stays on AFS. It is read once per run rather than several hundred
times per forked worker, and keeping it there is what lets `git pull` on lxplus work
normally.

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

`make deploy` first proves that the supplied Ansible-vault password decrypts
`group_vars/vault.yml`, before it contacts a VM. The prompt is for the vault
password chosen at encryption time, not the CERN/lxplus login password. A
typo can be retried three times; a failed vault check never consumes either
deployment convergence pass. An Ansible vault cannot be recovered without
its password, so if that password is lost, recreate the encrypted file from
the three source secrets described in section 3.3 (or run
`scripts/push-vault.sh` from the workstation that holds them).

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

> **Why `exec` and not `fluentbit_metrics` or `prometheus_scrape`.** Those two
> were the obvious candidates for the self-metrics input, and both were
> rejected. Both emit *metric* chunks, which
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
| `log-family-silence` | a whole log family (`infologger`, `info`, `other`) produced rollup buckets for 24 h and then none for 40 min | The stream stopped: producers, replay, or the collectors for that family. **Read this before any per-host volume alert** |
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
| `log-family-silence` | all three | `family` | family produced no rollup bucket for 40 min | yes |

**Share-of-fleet has a zero denominator, and it is not a share of zero.** The
share monitors ask "what fraction of the family's records came from this
host". When the family stops entirely there is no fraction to compute. Reading
that as a share of 0 fires the collapse branch for every host at once, so one
dead stream arrives as one warning per EPN — 167 of them on the 2026-08-14
poison run, folded into one card that no longer named the cause. The monitors
now skip a slice whose `fleet_count` is 0, and `log-family-silence` owns that
case as a single page naming the family.

Telling the two apart is the **rollup's** job, not the monitor's: a host with
no rollup row contributes no `fleet_count` either, so inside a per-entity
aggregation "this host went quiet" and "everything went quiet" are the same
numbers. `trend_rollup.py` therefore writes a `doc_count: 0` row (flagged
`imputed: true`) for any entity that logged within `SILENCE_MEMORY_SECONDS`
(24 h) but wrote nothing this bucket — and writes nothing at all when the
cohort collected nothing, which is the family-silent case. Only rows with
`doc_count > 0` count toward that roster, so an imputed zero can never keep an
entity alive and re-impute itself forever. Steady state writes no extra rows.

**The share monitors also need a baseline wide enough to mean something.** The
7-day baseline window is real history in production, but a freshly reset
rollup offers a single partial first bucket, and every entity in the cohort
breaches against it at once. All trend monitors now require
`MIN_BASELINE_BUCKETS` (6 buckets, one hour) before they may fire.

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
anomalies-last-hour counts beside them. Its buttons post in place and the work
runs as a background job, so the address bar never leaves `/ops/` and a fresh
reload that takes minutes no longer ends in an nginx 504. The page also starts
replay passes, poison replay, and fault injection — see § Fault injection.

Two buttons empty the stack, and they are not the same. **Delete logs** stops
any running replay, deletes the log indices and everything derived from them,
rebuilds the write aliases, and then loads nothing — use it when the next
replay must be the only data in there. **Reload data · fresh** does all of that
and immediately starts a new paced pass.

```
make clear      # the Delete logs button, from a terminal (make wipe is the same target)
make replay     # load data again when you are ready
```

`make clear` asks `alice-ops` for that exact action and follows the job, so the
button and the command share one implementation and one set of refusals. It
needs `alice-ops` running on the control host, and it fails if the wipe reports
a problem — a failed alias rebuild leaves rollover inactive, and ingest would
then auto-create the log indices with the wrong mapping. The name overlaps with
the page's **Clear findings** button, which is a different, smaller action: it
drops alerts, incidents and baselines and keeps the logs. Notifications go through Alertmanager
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

### Forecasting (`disk-fill`)

One forecaster, deliberately. Forecasting predicts *when a value crosses a
threshold*, so it earns its place only where a metric has a real absolute
threshold, moves slowly and smoothly, and arrives continuously in real time.
In this stack exactly one metric passes all three: filesystem fill per
OpenSearch node. Rates, lags and error shares are spiky and thresholdless —
anomaly detection and the trend lane already own them. Log-volume forecasting
would freeze in Initializing for the same reason the log detectors did: replay
arrives in bursts, not as a continuous stream.

| Piece | Value |
|---|---|
| Forecaster | `disk-fill`, HC on `os_node` (every node in the cluster, all 5) |
| Source | `cockpit-metrics`, `kind:node`, `max(disk_used_percent)` |
| Interval / horizon / history | 60 min / 24 buckets (1 day ahead) / 168 points (7 days) |
| Results | `opensearch-forecast-results*`, retention `forecast_result_retention` |
| Monitor | `disk-fill-forecast`, query-level, severity warn, 30 min throttle |
| Fires when | `max(forecast_value) > forecast_disk_threshold_percent` |

The disk figure is `fs.total` minus `fs.available`, so it covers the whole VM
filesystem — operating system, Fluent Bit buffers and shards together. On the
two workers that includes the collector's own buffers, reported under the
`os_node` identity rather than `collector_id`. Same machine, different field.

**The alert names no node.** Forecast results carry their entity in a *nested*
field, so a monitor cannot key per node without a flattened custom result
index. This lane makes the same trade `ad-high-grade` already makes: the
monitor is a fleet-scoped tripwire and per-entity attribution is read from the
Forecasting UI in Dashboards. Add `flatten_custom_result_index` and a
bucket-level monitor if per-node pages turn out to matter.

**It is silent for its first two days.** An RCF model needs roughly 40 points
at the configured interval, which at 60 minutes is about two days of poller
history. `verify_detection.py` prints the warm-up state and does **not** fail
the deploy for it; it fails only on a missing, duplicated, mis-categorized or
genuinely failed forecaster.

**`plugins.forecast.max_primary_shards` must stay pinned at 1.** The result
index otherwise takes one primary per data node with `auto_expand_replicas`
`0-2` — 15 shards for a few thousand tiny documents, against a storage-tier
budget near 60. `forecasters.sh` pins it and the retention period on every run,
and `verify_detection.py` fails the deploy if the pin is missing. Forecast
model memory is a *separate* 10 % heap slice from anomaly detection
(`plugins.forecast.model_max_size_percent`), so this lane does not compete with
the 17 detectors — but it is another ~100 MB claim on a 1 GB heap, which is why
it stays at five entities.

**Cardboard cannot validate this honestly.** Disk here is driven by replay runs
and lifecycle deletes, which is a sawtooth, not an organic ramp. Treat a green
lane on the test cluster as a contract proof, not as evidence the forecast is
accurate. The signal becomes real on a production flight, where disk fills
monotonically.

Profile: `GET _plugins/_forecast/forecasters/<id>/_profile`.
Backtest without waiting: `POST _plugins/_forecast/forecasters/<id>/_run_once`
(task-scoped, writes results carrying `task_id`, which the monitor excludes).

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
| `alice-alert-actions` | 7d / 1 GB | 30d | ≥23d | 5 × 1 |
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

**`alice-alert-actions` is an action log, not a notification path.** Every
trigger action writes one document into it, which is what gives the 30-minute
per-alert throttle a destination and keeps a record of each fire computed
independently of the signal projector. Nothing reads it, and the name "sink"
misled: it notifies nobody. It shipped as a concrete index with no retention of
any kind, so it grew without limit on a tier where disk is a paged alert. It now
writes through a rollover alias like the log families. A cluster that still has
the old concrete index is migrated on the next `make deploy`: the records are
reindexed into `alice-alert-actions-000001` first, so nothing is lost.

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

**One card is one notification.** An episode is per entity, and that is
deliberate: 40 EPNs breaching one rule are 40 episodes, because epn034 can
recover while epn159 is still bad and one shared state machine cannot hold two
answers. But 40 cards on a board that shows 20 is a fan-out that hides every
other problem, so the cockpit groups them for display only. Each episode
carries `group_id`, built from the same fields Alertmanager groups on —
`cluster_id`, `alertname`, and `notification_scope` on the collector route
(`alertmanager.yml.j2:7,14`). `scope_label()` in the projector produces both
that segment and the Alertmanager label, so the two can never drift, and one
card is exactly one message the operator would receive.

The board aggregates open episodes on `group_id` and draws one card per group,
naming the affected-entity count and sampling the first three. Grouping is
presentation only: `incident_id` is unchanged, per-entity recovery is
unchanged, and the *incident purity* gate — no incident may mix `entity_id` —
still holds, which it would not if episodes themselves were merged. **SIGNALS**
opens `alice-signals` filtered to the group over the group's own window;
filtering the `entity_id` field there narrows to one EPN. **DETAILS** opens one
row per affected entity. The projector heartbeat records `incident_groups` and
`incident_group_max`, so the fold ratio is measurable rather than assumed.

**A card unfolds in place.** Grouping answers "how many cards", but it left
"which entities" behind a tab switch. Clicking a card expands it to a list of
its affected entities, newest first, each with its own episode state, worst
grade and last-seen time, and each linking to that one entity's history. The
children ride along in the **same** aggregation as the parents — a second-level
`terms` on `entity_id` under the `group_id` terms, with metric sub-aggregations
only — so unfolding is layout, never a second round trip. A `top_hits` per
child was rejected: at 20 cards it stores hundreds of hits on every 30-second
board refresh. The list stops at `GROUP_CHILD_ROWS` (25) and says how many more
exist; DETAILS remains the complete list.

Two Vega details are load-bearing. One card is open at a time, so the vertical
offset is a signal read off the open row rather than a running total per row —
a `window` transform with `frame: [null, -1]` returned the whole partition sum
for every row, including the open one, which drew children straight through the
card below. And the expand handler names *every* text mark on the card, not
just the background rect: Vega dispatches a click to the topmost mark only, so
a handler on the background alone makes clicking the title do nothing. The
open card is held as its `group_id`, never as the datum, because the panel
refetches on every refresh and a frozen datum would lay the board out from
counts that no longer exist.

Three independent guards stand behind that board, because each of its failures
is silent. `hydrate_patterns.py` fails the deploy if `group_id` is absent from
either index pattern. `verify_detection.check_episode_grouping` fails it if an
open episode carries no `group_id`, or if a per-entity rule wrote an anonymous
episode in the last hour. And `verify_detection.check_cockpit_board_query`
**reads the board's own aggregation out of `cockpit.ndjson` and runs it**, so a
query OpenSearch would reject fails the deploy instead of drawing an empty
board. It reads the query rather than restating it: a second copy could drift
from the panel the operator actually loads, and then the gate would validate a
query nobody runs.

**The grouping gate runs in one pass only, and only against fresh episodes.**
`verify_detection.py` runs three times in a deploy, and `group_id` is written by
the projector — which `projector.yml` installs *after* `detection.yml` has
already verified the detection layer. Checking grouping in that first pass is
unsatisfiable by construction: on the deploy that introduces the field, every
episode still in the index was written by the previous projector and carries
nothing. The gate then fails, `projector.yml` never runs, the new projector is
never installed, and the next deploy fails identically. That deadlock shipped in
`0a42bea` and is why `CHECK_EPISODE_GROUPING` now defaults to false, set true
only by the post-projector re-verify.

`GROUPING_SINCE` bounds it further. It is set to `_projector_gate_started` — the
instant recorded just before the projector restart — so only episodes the
running projector itself updated can fail the deploy. Older ungrouped episodes
are reported as a warning and cleared from the ops page with **Clear findings**.
Without that bound the gate would still block, because an episode the new
projector never touched keeps whatever the old one wrote. This is a scope
restriction, not a weakening: a projector that stops writing `group_id` still
fails the deploy on its own next episode.

**An alert that names no entity is never a fleet-wide breach.** A per-entity
monitor declares one entity per alert. When no bucket key arrives, the entity
used to fall back to the sentinel `none`, which hashed into `incident_id` and
folded every entity's breach into one anonymous incident reading "whole fleet"
— the exact mirror of fan-out, and invisible to the purity gate because every
member carried the same sentinel. The projector now attributes such an alert to
the monitor itself: `entity_kind: monitor`, `entity_id` the monitor name, and
`class` either `monitor-error` (Alerting reported an execution error, which the
projector already counted as firing) or `entity-missing` (the alert simply
carried no key). The card then says the rule cannot run, which is true and
actionable. The same hole existed on the detector lane and closes the same way:
a high-cardinality result carrying no entity is dropped and counted in the
projector log, never averaged into a fleet incident.

**Where the bucket key actually lives.** Alerting writes the bucket identity
into the alert document nested under `agg_alert_content`: `parent_bucket_path`,
`bucket_keys` (an array) and `bucket`. The flat top-level `bucket_keys` exists
only in `Alert.asTemplateArg()`, the mustache context an action renders, and
there it is one comma-joined string. The two shapes share a field name, so a
reader of the flat field on a stored alert sees every bucket-level breach as
keyless while the action payload for the same breach names its entity
correctly. That mismatch made all 17 bucket-level monitors project
`entity-missing` the first time a poison run pushed them into a sustained
breach. The projector reads the nested field and keeps the flat read as a
fallback; `test_a_bucket_alert_names_the_entity_opensearch_actually_indexes`
pins a hand-written copy of the indexed document so the fixture cannot drift
back; and `check_bucket_alert_entities` fails the deploy when a bucket-level
monitor projects `entity-missing` after the projector restarts. That gate runs
beside the episode-grouping gate and shares its `GROUPING_SINCE` bound, for the
same reason: the two earlier verify runs in a deploy happen before the restart,
where rows from the previous projector would fail the very deploy that replaces
it. The grouping gate cannot cover this case itself, because it excludes the
monitor classes on purpose — those rows are the wanted output when a rule is
genuinely broken. The new gate asks whether the rule was broken at all.

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

**Two notification tiers, split on `severity`.** The projector already stamps
every alert `page` or `warn` (OpenSearch severity `1` versus anything else), so
the routing tree tiers on that label alone and no producer changed. Pages wait
`alertmanager_page_group_wait` (30 s, one projector push) before the first
notification and `alertmanager_page_group_interval` (2 m) before the next one
from an open group. Everything else waits `alertmanager_group_wait` (5 m) and
`alertmanager_group_interval` (10 m), which is what the ten `trend-*` monitors
want: they run on a ten-minute schedule and a storm of them should arrive as
one group, not ten. `repeat_interval` stays 4 h for both. The practical effect
is that a dead Fluent Bit reaches a human in roughly 2.5 min rather than 5,
while trend noise batches harder than before.

```
route  group_by [cluster_id, alertname]           5 m / 10 m / 4 h
├── notification_scope =~ "collector:.+"          + notification_scope in group_by
│   └── severity = "page"                         30 s / 2 m
└── severity = "page"                             30 s / 2 m
```

Child routes inherit the receiver, the `group_by` and any timer they do not
set, so each node writes only what differs, and the first matching child wins.
A collector-scoped warn alert therefore stops at the middle node and keeps both
its per-collector grouping and the batch timers. Verify the tree without a VM:
render the template and run
`amtool config routes test --config.file <rendered> --tree cluster_id=alice-logs
alertname=data-loss severity=page notification_scope=collector:node-01`.
Every route shares one receiver, so read the printed path, not the receiver
name.

**The page tier's 30 s is a speed choice, and inhibition needs the opposite.**
A `group_wait` that protects inhibition must cover the cause→consequence delay,
so a suppressing alert reliably arrives before what it suppresses. 30 s is far
too short for that. Note that what matters is *arrival*, not notification order:
the inhibit stage asks whether a matching source alert is currently active in
the store, so the target's wait is what buys the source time to get there.

The three written rules carry 22 target signals. Seventeen are `warn`, and the
split makes those **safer** than the old single 3 m wait: the suppressor now
waits 30 s while its targets wait 5 m, instead of both waiting 3 m and racing.
Five pairs are page-against-page and sit wholly inside the fast tier, where
neither side gets any margin:

| Suppressor | Page targets in the same tier |
|---|---|
| `collector-down` | `data-loss` |
| `telemetry-silence` | `cluster-red`, `disk-cliff-page` |
| `fleet-fb-silence` | `collector-down`, `data-loss` |

Those five are the reason the gate exists. The alertmanager role refuses to run whenever
`alertmanager_proven_inhibit_rules` is non-empty unless
`alertmanager_page_wait_covers_inhibition` is `true`. Measure the delay with
`make inject`, raise the page wait to cover it, then flip that flag. The gate is
a deliberate stop, not a formality.

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

The same runs start from the **Fault injection** panel on `/ops`: pick the
scenario, the target worker, the observation window and the EPN host to
silence, then press **Run injection**. The page shows the live state, the
countdown, and the finished scorecard metric by metric, with the failures
listed under it. **Stop injection** ends the observation window early; it still
restores the component and still scores the shortened window.

Both front doors drive one engine, `alice-inject` on the control VM
(`roles/dashboards/files/inject_run.py`). `make inject` writes
`/var/lib/alice-inject/request.json`, starts that unit, and then only follows
it, so a scorecard never depends on which door produced it and a dropped SSH
session never aborts a 45-minute run. Each finished run is also kept as JSON
under `/var/lib/alice-inject/runs/`.

The engine runs on the control VM, which has no Ansible and no SSH into the
fleet, so the physical steps reach their node through **`alice-fault-agent`**
(`roles/faults`), an HTTP agent on the workers and the projector host. It is
firewalled to the control host, accepts a token when `fault_agent_token` is
set, and each node's agent will only touch the service it owns — Fluent Bit on
a worker, the signal projector on the projector host. Nothing else is
reachable through it. Two runs may never overlap: an injection refuses to start
while poison replay is running and vice versa, because either one's evidence
would be scored as the other's.

Each run injects, observes, restores, and prints a seven-metric scorecard —
signal reconciliation, independent-event recall, incident purity,
fragmentation, time-to-notify, time-to-resolve, false inhibition. **The
scorecard gates: a lossy reconciliation, an impure incident, fragmentation, a
non-zero false-inhibition score, or an absorbed independent event exits
non-zero and fails the play.** Pass `-e injection_strict=false` to report
without gating, or clear **Gate on the scorecard** on `/ops`. Run each scenario at least twice and paste the scorecards under
§ Calibration below. Notification-volume reduction alone is not a pass: that
number rewards suppressing everything, which is why it is reported but never
gated on. A page remains accountable after it resolves: `alice-signals` is a
deterministic upsert whose final state may be terminal by scoring time, but the
alert still had to reach a human while it was firing. The `stop-projector`
scenario also waits for a `projector_cycle_ok: 1` heartbeat newer than the
restart boundary before scoring; a running process without a completed catch-up
cycle is not recovery.

> **Why scenario 2 drops records, not a file.** The obvious form of this
> injection is "drop one EPN's file mid-replay". A file-level drop is not
> usable here: `replay.py`'s
> `_write_lines` has no per-host error handling, so a failed write kills the
> whole dds+stdout family rather than one host, and simply deleting the file
> makes Fluent Bit re-read the recreated inode from the head — duplication, not
> silence. `drop-epn-stream` instead installs a temporary ingest pipeline that
> drops records whose `origin_host` is the named EPN, which produces the
> specified observable exactly — that host silent, the rest of the fleet
> unaffected — and reverses cleanly.

### Poison replay — one-minute detector calibration

`make poison-replay` is the complementary **data-plane** calibration. It runs
as `alice-poison-replay` on the control VM so neither Ansible nor the Ops HTTP
request stays open for the 32-window RCF warm-up:

```
make poison          # start in the background (poison-replay also works)
make poison-status   # safe, repeatable status + detector/monitor matrix
make poison-stop     # SIGTERM, publish CANCELLED, keep evidence
```

The same controls are on `/ops`. If no replay pass is active, the harness starts
one. It then refuses to inject until all **ten one-minute detectors** report
`RUNNING`, have no shingles left to initialize, and clean recent source rows are
available. It selects common `origin_host`, collector, and OpenSearch-node values
from those clean rows; inventing a new entity would only train a new HCAD model
and would not test the production baseline. The exact selected entity must have
an active trained model, and its target detector/monitor lanes must have no
pre-existing firing episode that could absorb or misattribute the injection.
All seven 30-minute detector definitions are deliberately excluded.

Each burst writes through the production log aliases and `cockpit-metrics`, but
uses `pipeline=_none` and explicitly supplies both lag fields. This is required
for a controlled shipping-lag fault: the record must remain in the current
`collector_time` window while its virtual `ingest_time` represents a 15-minute
delay. Every row is marked `synthetic:true`, `poison_run_id`, `poison_stage`, and
`poison_targets`; Bulk item failures abort the run. The injector adapts up to
three bursts (`1x`, `3x`, `9x`) and never equates a successful HTTP write with a
detector pass.

The strict pass is:

- all ten fast detectors have a real-time native result above
  `anomaly_grade_floor` for the selected trained entity;
- all ten have a corresponding projected `alice-incidents` episode; and
- projected episodes exist for `ad-high-grade`, `cluster-red`,
  `collector-down`, `data-loss`, `disk-cliff-page`, `fleet-fb-silence`, and
  `shipping-breaking`.

The latest machine-readable state is
`/var/lib/alice-poison-replay/status.json`; immutable per-run reports are under
`/var/lib/alice-poison-replay/runs/`. Injected documents remain as labelled audit
evidence and expire through the normal family/metrics retention policies.

This does **not** replace `make inject`. Direct documents validate feature
aggregation → RCF → AD result → projector episode and deterministic monitor
queries. They cannot validate parsers, Fluent Bit failure, or absence/min-over-
time dead-man semantics; those remain the physical `kill-fluent-bit`,
`stop-alice-metrics`, `stop-projector`, and `replay-end` scenarios above.

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
| `alertmanager_page_group_wait` | 30 s | one projector push cycle; speed, **not** a cause→consequence delay | — |
| `alertmanager_page_group_interval` | 2 m | a later page in an open group should not wait a trend cycle | — |
| `alertmanager_group_wait` | 5 m | batch tier; half a `trend-*` cycle, so a storm arrives as one group | — |
| `alertmanager_group_interval` | 10 m | matches the `trend-*` monitor schedule | — |
| `alertmanager_repeat_interval` | 4 h | both tiers; unchanged by the severity split | — |
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

### Message-pattern triage

Every monitor above answers *how much* and *how late*. None answers **what the
host is actually saying**. When `trend-il-volume`, `trend-il-ef` or
`trend-other-errors` names an entity, the next question is which messages
changed — and that is a query, not a detector.

PPL's `patterns` command groups raw messages into templates at query time. It
ships in `opensearch-sql`, which is now asserted in
`roles/opensearch/defaults/main.yml`. Run it in Query Workbench, or against
`POST _plugins/_ppl` on `:9200`:

```
source=infologger
| where origin_host='epn-infra12'
      and collector_time > '2026-08-05 14:20:00'
      and collector_time < '2026-08-05 14:30:00'
| patterns message method=brain mode=aggregation by severity_norm
| sort - pattern_count | head 20
```

Run it twice — once over the alert window, once over the hour before — and
compare. A template whose `pattern_count` jumped, or one that appears only in
the second result, is the message behind the alert. Swap `infologger` /
`origin_host` for `generic-log-other` or `generic-log-info-*` on the other
families; the field names are the same after `severity_norm` / `origin_host`
normalisation (Non-optimal §4/§5).

**Always bound the window and the entity.** `brain` clusters the matched
documents in two map-reduce passes, so an unbounded `source=infologger` asks
two 2-vCPU storage nodes to cluster the whole retention window. Ten minutes of
one host is the intended scale.

**This lane can never become an alert.** Alerting monitors accept query DSL
only — there is no PPL monitor type — so `patterns` stays an operator tool.
Making template behaviour alertable would mean materialising a template ID at
ingest, which is deliberately not built: the template set of a fixed
source-code corpus saturates within a run, so "new template" tracks software
deploys rather than farm health.

**Unverified.** PPL was unused anywhere in this tree before this section, and
the `brain` method has not been run against 3.7.0 on these VMs. The first soak
should run the query above once; if `method=brain` is rejected, the fallback is
`method=simple_pattern`, and the plugin assertion still holds either way.

---

## 9. Ported from logstack — what changed and why

Everything in this section comes from `docs/THANASIS_PLAN.md`, which compared
Thanasis's `logstack` against this tree. The plan is the argument; this section
is what was built. Items waiting on EPN access are named at the end.

### The three rules every port obeys

**R1 — every ported parser reads a full date or an epoch.** His parsers use
`Time_Format %H:%M:%S` with no date part, so Fluent Bit fills in the current
date. Any line that crosses midnight, or that is replayed on another day, gets
a wrong date and no warning. Our replay does exactly that, so the fault would
be certain rather than possible. The three parsers we already had comply;
`source_path` carries no `time_key` at all, so the rule does not bite there.

**R2 — ported parsers attach to source tags, before the severity split.** He
routes by source: one tag, one topic and one index per program, which costs
four edits in lockstep for every new log source. We route by severity, and that
stays. Our pipeline already parses per source before it routes per severity, so
a ported parser chain attaches at that point and never changes output routing.

**R3 — per-node values come from the environment, not from Ansible.** Ansible
writes `/etc/alice-ingest/node.env` once, at install time, and the collector
reads `${ALICE_NODE_ID}`, `${ALICE_LOG_ROOT}` and the ports from it. The
original reason was portability to Kubernetes, which is off the table; the
surviving reason is self-registration below — a machine cannot register itself
if its identity exists only in a central inventory.

The trap this introduces is handled: Fluent Bit expands an unset variable to an
empty string and says nothing, so a missing node id would write into an index
literally named `generic-log-info-`. `EnvironmentFile=` is declared without a
leading dash, so systemd fails the unit if the file is absent, and
`register_node.sh` exits non-zero if `ALICE_NODE_ID` is empty.

### Item 2 — the source filename is kept

`Path_Key source_file`, normalised to the basename, mapped as `keyword`. We
used to capture the path as `file`, extract the host from it, and delete it.

The value is the per-process entity key. Detection groups by host today; this
lets it group by the program that wrote the line, which is the level at which
an ALICE fault actually appears. Storing it as `keyword` rather than text is
the whole point — he extracts these values and stores them as text, so they can
never be aggregated or charted.

### Item 3 — a node registers itself

`deploy/files/register_node.sh` is the single definition of the three
per-worker objects: the `generic-log-info-<box>-*` index template, the
retention-policy attachment, and a writable rollover index behind the alias.
Two callers run the same file:

- `templates.sh`, once per worker, during the deploy. Ordering still matters
  there, because the detectors provisioned later in that play refuse an index
  that does not exist and cannot wait for the collectors to start.
- `fluent-bit.service`, as `ExecStartPre`, on the machine itself at every boot.

What this fixes: adding a machine needs no central action beyond installing it;
a machine that leaves needs no cleanup, because its indices expire on the 8-day
retention; and a machine that returns after a reinstall repairs its own write
alias at boot instead of waiting for the next deploy.

That last case is the one that survives regardless of how the farm is managed,
and it needed more than a create-if-absent check. After a reinstall the alias is
still in cluster state, pointing at a backing index whose shards died with the
old disk, so the alias exists and every write still fails. The script rolls the
alias onto a fresh index instead of deleting the red one — red can also mean
"recovering right now", and deleting that would destroy data that was coming
back.

Failure behaviour differs by caller on purpose. Under `REGISTER_STRICT=true`
(the deploy) an unreachable cluster fails the run. At boot it warns and exits
zero, because the same collector also ships `infologger` and
`generic-log-other` to the storage tier and that lane must not be held hostage
by the local one. `ExecStartPre` counts against the unit's start timeout, so
`TimeoutStartSec` is raised to cover the wait — change both together.

### Item 4 — what a data node costs on a machine that does reconstruction

The design does not change: one cluster, workers as `data, ingest` but never
cluster-manager eligible, the info tier pinned to its own box with no replicas.
Lubos requires that the bulk tier never crosses the wire. Only the cost changes.

1. **`node.processors`** caps every thread pool at once, and also shrinks the
   merge-scheduler default, which derives from the same number. OpenSearch
   otherwise builds pools for all 128 cores of an EPN node. `opensearch.yml.j2`
   takes the *smaller* of `opensearch_worker_processors` and the processors the
   machine really has — it is a cap, never a floor, or a 2-vCPU staging VM would
   have its pools inflated instead of trimmed.
2. **`refresh_interval` is never set on the info tier, and must stay unset.**
   With no explicit interval a shard refreshes every second until no search has
   touched it for `index.search.idle.after`, then goes idle and stops. Setting
   an explicit interval silently turns search idle *off*. It reads as a tuning
   improvement and is the opposite of one.

   `index.search.idle.after` is set against the detector interval, not on its
   own. Three detectors query this tier every minute — `info-volume`,
   `info-per-epn-entry-lag`, `info-collector-shipping-lag` — and a detector
   query counts as a search, so the shard never sleeps for long; it cycles. Any
   value at or above one minute is therefore **inert**.

   | `search.idle.after` | Refreshes per minute | Saving |
   |---|---|---|
   | 5m (or any value ≥ 1m) | 60 | none, the setting is inert |
   | 30s | about 31 | roughly half |
   | 10s | about 11 | roughly six times |

   We use 10s. The cost is that a search arriving during the idle period waits
   one refresh: irrelevant to a detector, a fraction of a second to a person on
   Discover, on a tier people query rarely. **Measure the refresh rate before
   and after — if it does not drop, the value is wrong and the setting is inert.**
3. **Async translog** on the info tier. The default fsyncs on every bulk
   request. The cost is up to one sync interval of records lost on an unclean
   crash, on a tier that already has zero replicas and an 8-day life. Disk input
   and output is what competes with reconstruction, so this is the largest
   saving on that axis.
4. **One merge thread per shard.** Merges are the irregular cost — they arrive
   when nobody chose. `auto_throttle` stays at its default of true; it already
   adjusts merge input and output against indexing load.
5. **The worker heap is written down, not inherited.** Every other number here
   derives from it. `opensearch_worker_heap_size` carries the EPN-farm value of
   2 GB, which holds a handful of small shards without strain and implies
   roughly 3 to 4 GB resident once the Java runtime and Lucene take memory
   outside the heap — under one percent of a 512 GB EPN machine.

   **The staging workers override it down to 1g in `inventory.yml`.** They are
   `m2.medium` with 3.75 GB in total, which 2 GB of heap would take from Fluent
   Bit and the replay producer. Raise the inventory value, not the group_vars
   one, when the workers get real memory.
6. **The smaller levers.** `indices.memory.index_buffer_size` drops from the
   10%-of-heap default to 5%. `bootstrap.memory_lock` stays on, so locked memory
   never swaps and this node can never push physics pages to disk. The `zstd`
   codec stays: it costs a little processor and saves a lot of disk, which is
   the right side of the trade on a machine where disk is contended. Query
   insights stays on — it is cluster-wide with no per-tier scope, it records at
   the node that coordinates the query, and workers coordinate their own writes
   but rarely serve searches, so the cost there is already near zero.
7. **Hold the index count. Do not shorten `log_rollover_period_info`.** The info
   tier rolls daily and deletes at 8 days. At 200 boxes that is about 1600
   indices and 1600 shards, inside what a cluster handles, with each index
   storing its own resolved mapping in cluster state. Shortening the period is
   the one setting here that would break this at farm scale.

### Item 5 — the collector filters got cheaper, and output did not change

1. `normalize_fields` ran twice on every record. `rewrite_tag` re-injects each
   record under a new tag and the record re-enters the filter chain from the
   top, and that filter matched both the source tags and the family tags. It is
   gone entirely — see 4 below.
2. `stamp_collector_time` no longer matches the family tags. The re-injected
   record still carries `collector_time`, so the second pass only ever ran the
   guard and returned unmodified.
3. Both `set_host` Lua filters are replaced by one native `parser` filter. Each
   did a single pattern match to pull `epnNNN` out of the path, and converting
   the record into a Lua table and back is the expensive part of any Lua filter.
   The `source_path` parser does the same work in C and also produces Item 2's
   `source_file`. It runs before the body parsers, which is safe because it
   reads `file` while they read `log`.
4. `severity_norm` and `origin_host` moved into the `alice-add-ingest-time`
   ingest pipeline as a literal port of the Lua. Routing reads the raw
   `severity` field, so nothing upstream depended on them, and all three log
   families already run that pipeline as their `default_pipeline`. **The
   collector now runs zero per-record Lua.** `health_deltas` remains and runs
   once per interval, not once per record.
5. `flush: 5` cuts bulk requests fivefold. `flush` is service-level, so it also
   delays the live lane by the same five seconds. That is accepted; ten is also
   acceptable if measurement shows the extra saving is worth it.

   **Clear the trend baselines when this first deploys.** `ingest_lag_ms` is
   `ingest_time - collector_time`, and `collector_time` is stamped when the
   record enters Fluent Bit — so the buffer wait is inside the measurement, and
   raising `flush` moves that metric by up to five seconds in one step. Four
   detectors and two trend monitors watch it. `trend-il-shipping-lag` and
   `trend-info-shipping-lag` fire at **twice a seven-day baseline**, and
   `trend_lag_floor_ms` of 250 says normal lag is sub-second, so an unmitigated
   step would hold both monitors firing fleet-wide until the baseline rolls
   forward. Run **Clear findings** on the ops page with the change; the RCF
   detectors re-learn on their own but will flag the step once.

One consequence to remember: because the enrichment now happens in OpenSearch,
anything that bypasses OpenSearch does not get it. The live lane bypasses
OpenSearch by design, so `live_lane.py` carries the same severity table.

**Rejected:** emitting `@timestamp` as epoch milliseconds to speed the Painless
parse. It is Fluent Bit's record time, set by the parser's `time_format`, and
making it an ordinary numeric field fights the time-key mechanism and breaks the
strict date mapping.

### Item 6 — anomaly detection stays on the workers and costs less

The detectors and their output do not change. Removing the plugins from workers
is rejected: the results matter, and an uneven plugin set across a cluster is
its own failure mode.

1. **Concurrent segment search is off on the info tier.** From OpenSearch 3.0 it
   is on by default in `auto` mode, which parallelises aggregations across a
   separate `index_searcher` pool. Detector feature queries *are* aggregations,
   so they are exactly what it parallelises — the 3.0 release notes warn that
   aggregation workloads may use more processor after upgrade for this reason.
   Turning it off runs the same query on one thread: identical results, higher
   latency, much smaller processor spike. The index-level setting overrides the
   cluster-level one, so the storage tier keeps the default.
2. **Historical analysis is slowed, not shrunk.** `batch_task_piece_interval_seconds`
   inserts a pause between pieces of a batch task and `max_batch_task_per_node`
   caps concurrency. Backtests take longer and return the same result; nothing
   about a backtest is time-critical. These are cluster-wide, so storage is
   slowed too. Accepted.
3. **`model_max_size_percent` is deliberately untouched.** It defaults to ten
   percent of each node's heap, and because it is a percentage a 2 GB worker
   already contributes far less model memory than a storage node. Lowering it
   would evict models and force cold starts, which **would** change the output.
4. **Admission control is an emergency valve, not a throttle.** A node whose
   rolling average processor use passes the limit answers HTTP 429 to `_search`
   and `_bulk`, so it sheds load rather than fighting reconstruction. But a
   rejected detector query is a missing detection interval, which *is* a change
   in output, so **the valve is not enforced on this tier.** 95 percent rolling
   processor use is a genuine emergency on a 128-core EPN node and an ordinary
   paced replay on a 2-vCPU VM, and enforcing it here would reject exactly the
   writes and detector queries the plan says must never be rejected.
   `admission_control_mode` is `monitor_only`: the valve counts what it would
   have rejected, `admission-rejections` alarms on that, and nothing is dropped.
   Set it to `enforced` on the farm. The IO valve is overridden off for the same
   reason, and stays off — the plan asks for a processor valve.

   **Only `disabled`, `monitor_only` and `enforced` parse.**
   `AdmissionControlMode.fromName` throws `IllegalArgumentException` on any
   other name, so OpenSearch answers 400 to the *whole* persistent settings
   body — including the anomaly-detection batch pacing that shares the call —
   and `templates.sh` exits non-zero under `set -eu`. The mode is therefore
   asserted in `roles/dashboards/tasks/bootstrap.yml` before anything renders.
   An earlier revision shipped `shadow`, which is the word the OpenSearch source
   comments use for this mode but not the name the enum accepts; it failed the
   deploy at that PUT.

   The counter does increment in `monitor_only`. `CpuBasedAdmissionController.
   applyForTransportLayer` calls `addRejectionCount` as soon as the limit is
   breached, and only then checks whether the mode is enforced before throwing.
   So the monitor is fed in this mode — and on a 2-vCPU VM a paced replay will
   breach 95 percent often enough that `admission-rejections` is an early
   warning about VM size, not a rare emergency. Raise `admission_control_cpu_limit`
   if that reads as noise.

### Item 7 — the live lane

One extra Fluent Bit output, one small service on **alice-ingest-5**, one
standalone page behind the nginx we already run. Scope is `infologger` and
`generic-log-other` only, never the info tier — our severity routing is the rate
limiter his design lacks, because he pushes every non-system record to a browser
and at farm scale that is a blur.

**The cockpit entry point is a markdown panel, not a drawn button.** The top
right of the Maintainer Cockpit carries a bordered *LIVE LOG LANE* box that
opens `/live/` in a new tab. It was a Vega panel first, and that had to change:
OSD's Vega hyperlinks are not links. Vega marks draw no `<a>` element, and a
click is served by `Handler.handleHref`, which builds a detached anchor from the
loader's `sanitize` result and dispatches the click on it. `sanitize` only emits
a `target` when the loader carries one in its options, and
`VegaBaseView.createViewConfig` builds that loader with no options, so no Vega
mark on any dashboard can open a tab. A markdown panel with
`openLinksInNewTab` renders a real `<a target="_blank" rel="noopener
noreferrer">`, so every gesture works, including the context menu. The same
switch is now on for all cockpit markdown, which is why the drill-down links
also open their own tab.

The consequence is recorded here so nobody re-litigates it: the **SIGNALS** and
**DETAILS** buttons on the incident episode board are Vega marks, so they
replace the cockpit tab and Back returns. Making them open a tab means giving up
the drawn card board and rendering the episode list as a table, where the
index-pattern URL field formatter can open a new tab. The board's per-episode
filter is the only thing lost by using the header's text links instead.

**Cost on the cluster is zero.** The lane never touches OpenSearch. That is the
point: Discover stays for real queries, and the common act of watching becomes
free.

It is not on the control host. That machine already runs Dashboards, nginx,
Alertmanager and the metrics poller, and the projector was deliberately moved
off it for memory. This service holds one open connection per viewer, so its
cost grows with readers rather than with data.

**A standalone page, not a Dashboards plugin.** A plugin must match the host's
major, minor *and patch* version — a plugin built for 3.7.0 does not load on
3.7.1. Patch releases carry security fixes and CERN networks are scanned, so we
would take every one of them: a forced rebuild several times a year, and a
rebuild that fails takes the page down. Standalone removes that tax completely.
This is only correct because the view is a fixed, purpose-built thing; a
general-purpose query tool should not be rebuilt outside Dashboards, and
Discover stays where it is.

Browser behaviour, as specified:

- One ring buffer of the newest 10000 rows. Nothing older is ever kept.
- New records enter the buffer and **the view does not move.** A button shows how
  many arrived since the last look; pressing it renders the newest and resets.
  One buffer is enough because the view always shows the newest — there is no
  second frozen copy and no unbounded growth.
- **Only visible rows are rendered.** This is about how many rows exist in the
  page, not about processor speed: ten thousand drawn rows is ten thousand
  layout boxes the browser must measure, style and paint, and that cost sits in
  the rendering engine where faster hardware does not remove it. Verified in a
  headless browser — 1200 buffered records paint 28 row elements.
- **The server drops for a slow client and never queues per client.** Each viewer
  has a bounded queue; a full queue drops the record for that viewer and counts
  it. Verified: a viewer that never reads had 7785 records dropped while the
  server stayed up.
- Filtering runs in the browser, over the buffer it already holds. Ten thousand
  rows filtered on each change is ordinary work for consumer hardware, the lane
  is low volume by construction, and the experience is better — instant results,
  history stays searchable, no round trip. Server-side matching stays available
  if a measured arrival rate ever makes the network the limit.
- Mobile: one column, no horizontal scroll, two lines per record, tap for the
  full record.

#### Two deviations from the plan's wording, both deliberate

**Server-Sent Events, not WebSocket.** The lane is one-way — the server pushes
and the browser filters locally — which is exactly what SSE is for. WebSocket
would mean hand-rolling RFC 6455 framing inside a stdlib server for a
bidirectional channel we never use, plus our own reconnect logic. SSE needs
`proxy_buffering off` in nginx, which is configured, and the browser reconnects
by itself. This changes the plan's wording, not its requirements.

**React is vendored, not built.** The plan chose React on one reason — whoever
maintains this after us — and that reason stands. But this tree has no Node, no
npm and no bundler, and adding a JavaScript toolchain to an Ansible deploy is
the same kind of permanent tax the plan rejected for Dashboards plugins. So
`react.production.min.js` and `react-dom.production.min.js` are committed under
`roles/dashboards/files/live/`, and the page calls `React.createElement`
directly, which is what JSX compiles into. Nothing builds and nothing fetches at
deploy time. See `live/VENDORED.md` for versions, hashes and provenance. React
19 removed UMD builds, which is why the vendored line is 18.

### Item 8 — the write load, and why the shard count is still 1 here

**The mechanism is built and the value is deliberately not yet raised.** The
primary count for `infologger` and `generic-log-other` is one setting,
`log_primary_shards_storage`, and it is pinned to **1** on this tier.

The plan asks for one primary per storage node. That is right on the farm and
wrong here, and the reason is already written above under *"Shard budget is the
binding constraint, not disk"*: roughly 20 shards per GB of heap gives about 60
across three 1 GB nodes, and dropping these two families from 3 primaries to 1
is what bought that budget. At 3 primaries they reach about 135 shards at full
retention — `infologger` alone is 9 backing indices × 9 copies = 81 — against a
budget of 60. The plan's own instruction is to set the primary count equal to
the number of storage nodes and *revisit it only if the tier is resized*; this
tier is three small VMs, so the honest reading is that the farm value waits for
farm hardware.

Raise `log_primary_shards_storage` to `{{ groups['storage'] | length }}` when
the storage tier has real heap. Nothing else has to change.

Every collector writes to its own localhost, so its OpenSearch node coordinates
the write. With a single primary that node had to forward every bulk request to
whichever node held it: one machine received the whole fleet's InfoLogger
traffic, indexed it, sent two copies back out, and serialised every write in the
cluster. Processor cost was already spread, because a replica does the same
indexing work as a primary — the funnel was not.

Durability is unchanged at three copies, still surviving the loss of two nodes.
Three primaries with one replica would cut per-node indexing and disk by a
third, but drops us to surviving one node loss; Lubos said farm storage can be
treated as unlimited, so we buy durability with it.

The change is cheap whenever it is made, because shard count is fixed at index
creation: it reaches an existing family at its next rollover, with no reindex and
no downtime. That is also why leaving it at 1 for now costs nothing later — the
funnel only bites at farm volume, and the farm is where the setting gets raised.

### Item 9 — the durable queue is not built, and has a named trigger

A queue in front of the storage tier would add durability across a worker loss
and let readers be added without touching the collectors. It is not built:

1. The OpenStack machines cannot host it. Getting the current stack up was
   already tight on memory.
2. The gain lands in the wrong place. It would mainly relieve the storage tier,
   and farm storage can be treated as unlimited. The worker tier is where we are
   short, and a queue does not help there.
3. The bulk tier must not cross the wire at all, so a queue could only ever
   carry `generic-log-other` and `infologger` — the small fraction.

**The trigger is a third consumer of the stream.** Two consumers — OpenSearch
and the live lane — are served by two Fluent Bit outputs. A third means editing
the collector on every machine, and that is when a queue pays for itself. The
live stream during Run 4 is the other trigger: today a lost record is
recoverable because the source is an S3 bucket and the replay runs again; on a
live farm it is gone.

A third thing a queue would fix, recorded so it is not forgotten: because every
collector writes to `localhost`, a worker whose own OpenSearch node is down
stops shipping `infologger` too, not only its local tier.

**If it is ever built it must be Apache Kafka.** CERN requires fully open
source, which rules out Redpanda — its core is source-available under a business
licence, not an open-source one. Make it the primary path for the durable tier,
not a failure path: Fluent Bit has no on-failure route, retries are internal to
the output plugin, and when `retry_limit` is reached the chunk is dropped with
no hook and no alternate destination, so a dead-letter design cannot be built.
Record the queue coordinates on every document — topic, partition, offset and
key. The offset is a replay position and partition-plus-offset is a
deduplication key; neither is reproducible from any other field.

### Item 10 — the operator's node-and-time form

**Not built yet, and it cannot be built inside Dashboards.** This is the
enforcement path for the index-filter rule below, and it is deferred with the
Operator Cockpit it belongs to.

Dashboards has no native control that selects an index: its input controls
produce filter pills on *fields*, and nothing native lets one panel change
another panel's index pattern. Our own cockpit shows the available parts — index
patterns, saved searches, visualisations, no control panels. He built his in
Grafana, whose variables can drive a data source. So a Dashboards version of
this form would have to filter on the `node` field, which is the exact query the
operating rule forbids: it would enforce the opposite of what it is for.

The form therefore lives on the standalone operator page, where we own the query
and the dropdown is ordinary work. Item 7 already established the serving path,
so it costs no new component. **Not taken:** his confirm modal showing old
against new values — it adds a click to guard a mistake that one dropdown change
undoes.

### Item 11 — every ops-page action is documented beside its button

All nine actions carry a paragraph under the button answering what it does, what
it deletes by name, roughly how long it takes, whether it is safe while other
people are using the system, and what to do if it fails. The safety question is
not optional: the staging machines are shared, and physicists run test runs
through the staging experiment control system.

The documentation is on the page, not in this repository, because a person about
to press `wipe` does not go and read a repository. It is written for a tired
person at three in the morning who has not used the page before: short
sentences, consequence before mechanism, real names for real things, no warning
tone on safe actions and no soft language on destructive ones.

The destructive actions are also grouped visually and boxed, apart from the safe
ones. They used to sit in one column and look alike.

### Item 12 — panels that hold their shape at any fleet size

**The rule: a panel's size must not grow with the fleet.** Everything follows
from that one sentence. `alice-viz-fb-status` drew one row per collector and the
shipping charts grouped by `collector_id` — fine at two collectors, a grey smear
at two hundred, and slow to draw.

- **A counter strip.** Collectors, healthy, degraded, down. Four numbers, the
  same four at any scale. Each counts distinct `collector_id` values in the
  window, so a machine that flapped can appear in two of them.
- **Ten-row tables.** "Not healthy now" names the machines that are down or
  unhealthy; "Worst ten by records lost" ranks by dropped records. On a small
  fleet they show however many exist, so there is no separate small-fleet design.
- **Charts show distribution, not machines.** Fleet median, 95th percentile and
  worst value — one line each, whatever the fleet size. Each document is one
  collector's sample in that window, so a percentile across documents *is* the
  distribution across the fleet.
- **A picker for detail**, so an operator can pin named machines over the
  distribution.

Mobile falls out of the same rule for free: four counters and a ten-row list fit
a phone; two hundred chart lines never will.

**The picker is native, and is not Item 10's dropdown.** This one filters
`collector_id` as a field over `cockpit-metrics`, which is correct here because
those indices are not partitioned per machine, so a field filter is the only
filter available and it is cheap. Item 10's dropdown selects an *index* over log
data and cannot be native. The two look alike and share nothing.

### Item 13 — two cockpits, for two different people

The current dashboard is renamed **Maintainer Cockpit**. It answers "is the log
pipeline healthy", which is a maintainer's question that a shifter never asks. A
shifter asks what the experiment is saying right now. One dashboard serving both
serves neither.

The Maintainer Cockpit stays in Dashboards — charts and saved searches over
indices is what Dashboards is for — and only gets renamed. It now links out to
the live log page, and that page links back.

**The Operator Cockpit is not built. It needs EPN access to finish**, and the
reason is adoption rather than plumbing. It must copy the InfoLogger interface
shifters have used for years: a tool that looks like the one they know needs no
training, and a tool that needs training does not get used. That is the
strongest single lever we have on whether this system is still running in Run 4,
and it cannot be done from memory. Two things happen in the same pass as the
Item 1 survey: find the InfoLogger interface documentation in the ALICE EPN
documentation, and look at the running interface to record its columns, filter
controls, severity colours and default view.

The data is already there. `templates.sh.j2` maps the full InfoLogger column
set, so this is a user-interface job with no data work behind it — but confirm
field by field against the real interface before building.

The log view itself is shared with Item 7 and is built once: dense, virtualised,
filterable, mobile. Item 7 feeds it from the live stream; the Operator Cockpit
will feed the same component from a query against `infologger`. Both are
standalone for the same reasons, and the shared component is what makes both
cheap — splitting them across a plugin and a page would mean two mounting paths
for one component, which is the worst of each.

### Item 14 — one causal edge file

Alertmanager `inhibit_rules` are a causal graph. We never called it one.

| His model | Ours |
|---|---|
| `ErrorPattern -> Cause` | `source_matchers` to `target_matchers` |
| `SeqCondition.context_keys` | `equal: ['cluster_id', 'collector_id']` |
| `SeqCondition.time_window` | `group_wait` and the episode window |
| `prob` on the edge | measured from injection runs, not authored |
| emits a ranked cause list | now both: it advises, and when proven it acts |

The difference was direction and confidence, not structure. Every edge is now
declared once in `roles/dashboards/files/causal_edges.json` — cause, symptom,
scope keys, probability and a `proven` flag — and the flag decides what the edge
does:

- **`proven: false`** — the edge appears on the incident card as a ranked
  candidate cause. It suppresses nothing.
- **`proven: true`** — the edge is rendered into `inhibit_rules`. It suppresses.

**This is the point of the item. The explanation feature ships now, on no
evidence, at zero risk.** A wrong ranking costs an operator half a minute; a
wrong suppression loses a page. Every edge earns promotion by surviving
injection, and the same file records that it did. All 22 edges ship
`proven: false`, so the generated `inhibit_rules` block is empty — byte-for-byte
the behaviour we had, from one declaration instead of hand-written rules plus a
separate enabled list in `group_vars`.

**Not a graph database.** About thirty edges, every query one hop. Neo4j exists
for millions of nodes and paths of unknown length. A new datastore also means
new backup, a new upgrade path and a new failure mode on a control host that
already runs four services — and his own prototype consumes a topic absent from
his topic list, so there is no evidence it ever ran.

**Probabilities are measured, not authored.** Every `make inject` run is a
labelled experiment, because we know what we broke. `score_injection.py` counts,
per edge, how often the symptom appears inside the episode window when the cause
is present, across the scopes where the cause fired. That is an empirical
conditional probability. Ordering needs no new storage either: the projector
already stamps signals into episodes with timestamps, so "collector-down
preceded data-loss by under two minutes" is answerable from `alice-signals`
today. That is his `SeqCondition`.

Noisy-OR, `B = 1 - product(1 - Bi)`, is kept **for ranking only**. It assumes
causes are independent and ours are not — `collector-down` and
`fleet-fb-silence` overlap by construction. Good enough to sort a list, not good
enough to silence a page. An unmeasured edge ranks on a neutral prior, and the
card records whether the ranking rests on measurement.

#### Two gates before any edge is promoted

1. **The scorecard measured harm, not effect.** It counted pages that were muted
   and should not have been, and nothing counted symptoms that were correctly
   muted, so **a rule that never fires scored a perfect zero.**
   `correct_suppressions` now counts the symptoms an enabled edge actually
   suppressed, and the scorecard fails if proven edges suppressed nothing.
2. **The cause may arrive too late to suppress anything.** Alertmanager applies
   inhibition when it notifies, and the page route waits
   `alertmanager_page_group_wait`. But `collector-down` is absence-based: it
   needs `heartbeat_grace_seconds` plus a monitor interval before it can
   conclude, while symptoms fired from observed counters reach Alertmanager
   first. The scorecard now reports every cause-to-symptom pair where the
   symptom arrived first and fails on it, and the Alertmanager role still
   refuses to run with proven edges unless
   `alertmanager_page_wait_covers_inhibition` is set.

#### Order of promotion, narrowest blast radius first

1. **`collector-down`** — scoped to one collector, ten symptoms. Needs the
   roster populated first, or it falls closed to the `none` sentinel and does
   nothing.
2. **`telemetry-silence`** — cluster-scoped, but its seven targets are all
   control-plane monitors, disjoint from the collector set. When the poller is
   dead those monitors have no fresh input, so their firing carries no
   information.
3. **`fleet-fb-silence`** — last, and the dangerous one. It mutes
   `collector-down` itself across the whole fleet, so a staged restart that
   trips the 50 percent threshold would hide every real per-collector failure at
   once.

### Operating rules

**Host and machine metrics are not ours.** Another person owns machine health at
CERN, using Mimir. We do not collect processor, memory, load or filesystem
figures for the machine. What we collect is Fluent Bit health and OpenSearch
cluster health — the log pipeline and the log cluster. Building our own host
metrics would create a second source of truth for numbers someone else already
owns, which is worse than not having them. If we ever do need one, it goes into
`cockpit-metrics`, never into a new store.

**The farm runs Ansible, not Kubernetes.** Kubernetes is not available on the EPN
farm; Ansible is. So Ansible manages every tier, on the farm and on our own
machines. Recorded because a container plan was considered in detail and dropped
on this fact alone. Do not reopen it without new information about the farm.

**Decommissioning a machine must wipe `path.data`.** OpenSearch identifies a node
by an identifier stored there, not by its name. If a replacement machine reuses
the hostname, the old and the new both claim that name and `require.box` matches
both, so shards could land on either. A returning machine whose indices were
deleted is otherwise safe — its shard files are dangling, and OpenSearch has not
imported dangling indices automatically since Elasticsearch 7.9, so they sit
unused. But wipe the disk.

**Alarm on storage-tier index health, never on cluster health.** The info tier is
pinned to one box with no replica, so every reboot turns those indices red and
the cluster goes red with them. On a farm that is constant, which makes cluster
health unusable as a signal. Watch the storage-tier indices.

**Node selection in the cockpit is an index filter, never a field filter.**
Picking a machine must produce a filter on `_index`, which lets OpenSearch skip
shards; a `node: epn345` filter over `generic-log-info-*` reads all 200 shards
and discards 199. Item 10 is the enforcement path and lives on the standalone
page, because Dashboards can only filter fields.

Inside Dashboards this rule stays unenforceable, so there it is enforced by
measurement: query insights already records processor use and shard count per
query. Watch it on the storage tier and catch the expensive pattern when it
appears. **This backstop is permanent, not temporary**, because Discover remains
available and cannot be constrained. Daily rollover helps on its own — the
can-match phase skips indices whose time range does not overlap the query, so a
short time range already prunes most of each machine's eight indices.

### Still waiting on EPN access

**Item 1, the parser library.** His parser chains extract numbers from message
bodies: per-timeframe processing time, CTF size, decoding-error counts, tracks
and vertices per timeframe, encoder word counts, beam position. Our detectors
currently see only infrastructure signals, so they can say the log platform is
sick but not that the experiment is sick. **This is the single largest gain
available to us — nothing else changes what the system can detect; this changes
the subject.**

Nothing structural is missing. One input per program, one parser chain on that
tag, and the existing severity router handles the rest. The mechanism is not the
work; the regexes are. Writing 28 parsers against a subset of the data would
produce parsers we cannot test, for message shapes we have never seen.

**The gate is a survey, on one EPN, before a single parser is written:** list
every file under the log roots with path, size and modification time; count lines
per program; sample enough lines per program to see the message shapes; run his
28 regexes against that sample and count matches per regex. Then port only the
parsers with matching lines and drop the rest.

The survey has a second output that matters as much: the real file layout, with
real paths, real names and real rotation. Our tail patterns and the
`source_file` normalisation are guesses until we see it. Every ported parser also
needs a strict numeric mapping in `templates.sh.j2` — he extracts these fields
and stores them as text, so they cannot be aggregated or charted, and parsers
without mappings repeat his mistake. Rule R1 applies to every ported
`Time_Format`.

**Item 13's Operator Cockpit**, for the reasons given above.
