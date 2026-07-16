# alice-ingest — Distributed Deployment (3-VM, native, no Docker)

This tree takes the ALICE O2 logging paper-airplane from "Docker Compose on
one machine" to **3 CERN OpenStack VMs, native systemd services, one 3-node
OpenSearch cluster**, provisioned and configured with pure Ansible. It is
purely **additive**: `docker-compose.yml`, `docker-compose.mocks.yaml`,
`images/**`, `init/**` and the root Makefile/README are untouched and remain
the local dev path (Docker Compose on a single machine — see the root
[`README.md`](../README.md)). This document is the canonical plan/runbook for
`deploy/` (it replaces a deleted `docs/DEPLOY-DISTRIBUTED.md`).

---

## 1. Topology

```
                                CERN_NETWORK (internal only)
                                        │
        ┌───────────────────────────────────────────────────────────┐
        │                                                             │
┌───────▼────────┐          ┌────────────────┐          ┌────────────▼───┐
│ alice-ingest-1  │          │ alice-ingest-2  │          │ alice-ingest-3  │
│  (control)      │◄────────►│                 │◄────────►│                 │
│                 │  9200/   │                 │  9200/   │                 │
│                 │  9300    │                 │  9300    │                 │
│ OpenSearch node │          │ OpenSearch node │          │ OpenSearch node │
│  node-01        │          │  node-02        │          │  node-03        │
│  :9200 :9300    │          │  :9200 :9300    │          │  :9200 :9300    │
│                 │          │                 │          │                 │
│ Fluent Bit      │          │ Fluent Bit      │          │ Fluent Bit      │
│  tails local    │          │  tails local    │          │  tails local    │
│  dds/stdout,    │          │  dds/stdout,    │          │  dds/stdout,    │
│  TCP :5170 IL   │          │  TCP :5170 IL   │          │  TCP :5170 IL   │
│  -> localhost   │          │  -> localhost   │          │  -> localhost   │
│  :9200 ONLY     │          │  :9200 ONLY     │          │  :9200 ONLY     │
│                 │          │                 │          │                 │
│ alice-replay    │          │ alice-replay    │          │ alice-replay    │
│  epn%3==0 slice │          │  epn%3==1 slice │          │  epn%3==2 slice │
│                 │          │                 │          │                 │
│ ── control-only:│          │                 │          │                 │
│ OpenSearch      │          │                 │          │                 │
│  Dashboards     │          │                 │          │                 │
│  127.0.0.1:5602 │          │                 │          │                 │
│ nginx (TLS +    │          │                 │          │                 │
│  basic-auth)    │          │                 │          │                 │
│  :5601 (SG-open)│          │                 │          │                 │
│ one-shot cluster│          │                 │          │                 │
│  bootstrap      │          │                 │          │                 │
│ [alertmanager:  │          │                 │          │                 │
│  reserved slot] │          │                 │          │                 │
└─────────────────┘          └─────────────────┘          └─────────────────┘
```

Single OpenSearch cluster, `cluster.name: alice-logs`, 3 data + cluster-manager
eligible nodes (quorum 2). Every VM is identical at the data tier: one
OpenSearch node + one Fluent Bit collector + the S3-replay producer for its
own EPN slice. Exactly one VM — `alice-ingest-1`, first in inventory, the
"control" host — additionally runs OpenSearch Dashboards, an nginx reverse
proxy in front of it, and the one-shot cluster bootstrap (index
templates/ISM, Dashboards index patterns).

---

## 2. Locked design decisions — and why

- **Native, no Docker anywhere in `deploy/`.** Official yum repos + RPMs +
  systemd units, not container images. This is a deliberate divergence from
  the Compose-based local dev stack: on real CERN VMs we want systemd-managed
  services with normal `systemctl`/journald operational ergonomics, not a
  second container runtime to operate on top of OpenStack.
- **Fluent Bit → its own LOCAL OpenSearch node only.** Each VM's collector
  writes to `http://localhost:9200`; there is no cross-VM write hop. The
  OpenSearch cluster itself does the replication. This keeps the write path
  identical to the single-node Compose stack's `fluent-bit -> opensearch`
  edge, just fanned out per-VM, and avoids one VM's Fluent Bit becoming a
  single point of ingestion for the whole farm.
- **One control VM for Dashboards/nginx/bootstrap, not three.** Dashboards
  and the one-shot index-template/pattern bootstrap only need to exist once
  per cluster (any node answers cluster-wide API calls); running three copies
  would just be three more moving parts with no benefit. `alice-ingest-1` was
  picked as "first in inventory" purely as a convention — nothing else about
  it is special.
- **`epn_num % 3` slicing.** `images/replay/replay.py` (preserved,
  unmodified) already partitions EPN hosts across `NODE_COUNT` collectors by
  `epn_num % NODE_COUNT`. With `NODE_COUNT=3` fixed by the 3-VM topology, each
  VM's `epn_partition` (0, 1, or 2 — set once in `inventory.yml`) selects
  exactly the slice that VM's replay should serve. `node` (the collector
  identity) is this VM's `node_id`; `host`/`hostname` (the EPN the log was
  actually born on) is untouched — same distinction as the existing
  multi-node local mode.
- **Heap `-Xms512m -Xmx512m`.** VMs are `m2.medium` (2 vCPU / 3.75 GB RAM),
  smaller than the `m2.large` (7.5 GB) single-VM demo in
  `docs/OPENSTACK_GUIDE.md`, which itself already trimmed Compose's default
  `-Xms1g -Xmx1g`. With OpenSearch + Fluent Bit + the replay producer sharing
  3.75 GB on each of 3 VMs, 512 MB heap per node is the deliberate further
  trim (see `deploy/group_vars/all.yml` `opensearch_heap_size`).
- **Alertmanager is a reserved seam, not built.** `group_vars/all.yml` has a
  commented `alertmanager_port: 9093`, and the dashboards role/nginx vhost
  both leave an explicit "when it lands, it's another control-host-only
  service behind this nginx" comment. Nothing elsewhere needs to change to
  add it later.

---

## 3. Prerequisites

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

### 3.2 Ansible + collections (wherever you run the playbooks from)

```bash
pip install ansible-core           # or your distro's ansible-core package
cd deploy
ansible-galaxy collection install -r requirements.yml
```
`requirements.yml` pulls in `openstack.cloud` (VM/network/security-group
management), `ansible.posix` (sysctl, firewalld), `community.general`
(htpasswd, seport, ini_file-style modules), `community.crypto` (self-signed
TLS for the nginx/Dashboards proxy). `openstack.cloud` also needs the
`openstacksdk` Python package (`pip install openstacksdk`) available to the
same Python Ansible runs under.

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

# 1. Provision the 3 OpenStack VMs (idempotent — safe to re-run).
kinit    # if the ticket has expired
ansible-playbook provision.yml

# 2. Configure everything (OpenSearch cluster, Dashboards+nginx+bootstrap,
#    Fluent Bit, replay producers) — needs the vault password.
ansible-playbook site.yml --ask-vault-pass
```

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

### 4.2 Rolling-safety note (cluster changes)

`site.yml` never bounces all 3 OpenSearch nodes at once. It splits the
OpenSearch role into two plays:

1. **Initial bring-up** (`hosts: alice_nodes`, no `serial`) — all 3 nodes
   start together, deliberately, because a brand-new cluster's
   `cluster.initial_cluster_manager_nodes` quorum (2-of-3) can only be
   reached if at least two nodes are up concurrently; a `serial: 1` rollout
   here would deadlock node 1 waiting on a cluster that can never form.
2. **Rolling-safety gate** (`hosts: alice_nodes`, `serial: 1`) — flushes any
   pending config/restart handlers from step 1 one node at a time, waits for
   that node's HTTP API to respond, then waits for `_cluster/health` to
   return a valid status (`red`/`yellow`/`green` — first-boot tolerant, not
   hard-requiring `green`) before moving to the next node.

Re-running `site.yml` later against an **already-formed** cluster (e.g. a
config change) only ever exercises the second play in earnest — since the
cluster already exists, step 1 is a no-op reconciliation and step 2's
`serial: 1` + health gate is what actually protects quorum, restarting one
OpenSearch node at a time and waiting for it to rejoin before touching the
next. Fluent Bit and the replay producers are stateless per node and are
deployed with no `serial` — a plain restart only re-tails from saved offsets
or resumes from the on-disk `AUTOSTART_MARKER` guard, so bouncing all 3 at
once is safe for those two roles.

---

## 5. Verification

```bash
# From any VM, or via SSH:
curl -s http://localhost:9200/_cluster/health?pretty | grep status   # want: green (all 3 nodes joined)

curl -s 'http://localhost:9200/_cat/indices/infologger,generic-log-*?v'
```
Expected indices (3 shards each, spread across the 3 nodes; replicas per
`init/opensearch/templates.sh`): `infologger` (2 replicas), `generic-log-info`
(1 replica), `generic-log-other` (2 replicas) — all `green` once all 3 nodes
have joined.

**Dashboards:** `https://<control-VM-address>:5601`, basic-auth user `alice`
(password = `vault_dashboards_basic_auth_password`), self-signed cert (browser
will warn — expected, it's a self-signed CERN-internal cert). The three index
patterns (`infologger`, `generic-log-info`, `generic-log-other`) are
auto-provisioned by the one-shot bootstrap — no manual setup.

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
Deletes the 3 VMs (idempotent — a missing VM is not an error), re-closes the
`dashboards` security group's `5601/tcp` ingress rule and removes the group
itself, and deletes the local `inventory.generated.yml` so a later
`site.yml` run can never target a stale IP. It deliberately does **not**
touch `inventory.yml` (committed source of truth), the `masladoj-key`
keypair, or the `ssh` security group — all cheap to keep, reused across VM
generations.

---

## 7. Validation status

This tree was validated with a local Python venv providing `ansible-core`,
`ansible-lint`, and `yamllint` (none were present on `PATH` otherwise).
Everything below ran clean:

| Check | Result |
|---|---|
| `ansible-galaxy collection install -r requirements.yml` (+ `pip install openstacksdk`) | pass — `openstack.cloud` 2.6.0, `ansible.posix` 2.2.1, `community.general` 13.1.0, `community.crypto` 3.2.2, `openstacksdk` 4.17.0 |
| `yamllint deploy/` (relaxed config: line-length/document-start off, `[true,false,yes,no]` truthy) | pass — zero violations |
| `ansible-playbook --syntax-check` on `site.yml`, `provision.yml`, `teardown.yml` | pass — zero syntax errors (benign warning only: `inventory.generated.yml` doesn't exist pre-provision, which is expected) |
| `ansible-lint` (default profile) | pass — 0 failures, 0 warnings, 43/46 files |
| `ansible-lint --profile production` (strictest) | pass — 0 failures, 0 warnings, 43/46 files |
| `ansible-playbook site.yml/provision.yml/teardown.yml --list-tasks` | pass — full play/role/task structure resolves, no missing roles or undefined vars |
| Jinja/variable sanity across all 10 role templates (`*.j2`) | pass — every `{{ }}`/`{% %}` and every `register:` traced to a real source (`group_vars/all.yml`, inventory host vars, or the owning role's own `defaults/main.yml`), none undefined or stale |

**Not validated** (requires the real infrastructure, out of scope for a
static/offline check): an actual `provision.yml` run against CERN OpenStack,
an actual `site.yml` run against real VMs, and therefore the live cluster
health / Dashboards / index verification steps in section 5. Nothing was
skipped silently — this is the honest boundary of what can be checked without
CERN network access and real quota.

---

## 8. Open items

- **Alertmanager** — reserved seam only (`alertmanager_port` commented out in
  `group_vars/all.yml`; the dashboards role's nginx vhost has an explicit
  "when it lands" comment block). Not built, by design (LOCKED topology).
- **Everything else in the LOCKED topology and quality bar is implemented**:
  native no-Docker services, per-VM local Fluent Bit → local OpenSearch write
  path, single control-host Dashboards/nginx/bootstrap, `epn_num % 3`
  slicing, 512 MB heap, inventory-driven discovery/publish/Dashboards-hosts
  Jinja (no IP typed twice), `serial: 1` + health-gate rolling safety for
  OpenSearch, vault-only secrets, `docker-compose.yml`/`docker-compose.mocks.yaml`/`images/**`/`init/**` untouched.
