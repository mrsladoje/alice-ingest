# Cardboard Airplane v2 — Dedicated Storage Tier

**Grill summary / design handshake.** This is the agreed design for the second native OpenStack deliverable, evolving `cardboard-airplane-v1` (3 flat worker VMs, one shared cluster, no storage tier) into a **two-tier cluster**: disposable local "trash" on the worker nodes, durable replicated storage on a dedicated storage tier.

> v1 (tag `cardboard-airplane-v1`) is the baseline we evolve *from*. This doc records the decisions and the file-by-file change surface; it is the spec, not the implementation.

**Status:** design locked (2026-07-16). One domain question is out to Lubos — *is `dds-other` operationally valuable, or is the whole dds stream plumbing noise?* — and the default below (route `dds-other` to durable storage with the rest of the `other` tier) is the safe answer if he doesn't reply. Nothing here blocks on it.

---

## 0. Goal — what v2 adds over v1

v1 put all three index families on one flat 3-node cluster, every index spread across every node. v2 introduces a **value-based storage split**:

- **Worker tier** — keeps the high-volume, low-value "trash" **strictly local to the VM that produced it**, with **no replicas**. This simulates the real constraint: the dds/info firehose must never be fired across the network.
- **Storage tier** — a dedicated, replicated, manager-eligible cluster tier that durably holds the low-volume, high-value logs, with a proper 3-node quorum.

Definition of done: provision → deploy → verify placement (each index lands *strictly* on its intended tier) → tear down, all green, mirroring v1's lifecycle.

---

## 1. The core decision — tier by **severity**, not by source

Lubos called dds "trash" (a *source* rule). Laying every segment out on both axes shows **severity**, not source, is the axis that aligns cost and value:

| segment | volume | value | home |
|---|---|---|---|
| dds-**info** | huge | low | worker (local, disposable) |
| stdout-**info** | high | low | worker (local, disposable) |
| dds-**other** | low | ? (pending Lubos) | storage (safe default) |
| stdout-**other** | low | high | storage (durable) |
| infologger | medium | high | storage (durable) |

`info` is simultaneously the **bulk** and the **trash** → keep it local (saves both network and storage). `other` is simultaneously **rare** and **valuable** → ship it to storage (cheap, because low-volume, and worth replicating). Source-based placement can't do that: "dds local, stdout to storage" would ship the entire stdout-info bulk across the network while disposably discarding dds errors.

The only ambiguous cell is `dds-other`. Default: fold it into `generic-log-other` → storage. Keeping a possibly-valuable log is a cheaper mistake than trashing it. Revisit if Lubos says dds is worthless end-to-end (then dds stays entirely local).

The collector **already routes by severity** (`rewrite_tag` → `family.info` / `family.other`), so dds and stdout stay merged exactly as in v1 — only the *destination* of each family changes.

---

## 2. Topology — 5 VMs, single cluster

| tier | VMs | flavor | vCPU | OpenSearch node role | runs producers + collector |
|---|---|---|---|---|---|
| **worker** | 2 | m2.medium | 2 | `data` + `ingest` | yes |
| **storage** | 3 | m2.medium | 2 | `cluster_manager` + `data` + `ingest` | no |

**Quota fit** (`openstack quota show`: cores 10, instances 5, ram 40960):
- cores: 5 × 2 = **10 / 10** (exact ceiling)
- instances: **5 / 5** (exact ceiling)
- ram: 5 × ~3.75 GB ≈ 18.75 / 40 GB

The binding constraint was **instances (5)**, not cores — that is why the split is 2+3 and not 3+3. The core budget alone would allow 3+3, but the 6th instance is impossible without a quota bump.

**Capacity note:** dropping from 3 workers to 2 means each remaining worker replays ~50% more EPN hosts (`%2` instead of `%3`) *and* carries its share of the local info ingest, on the same m2.medium flavor v1 used for 3 workers. Replay rates (`dds_replay_rate`, `il_replay_rate`) are env-tunable if a worker saturates; this is a simulation, so throttling is acceptable.

---

## 3. Cluster design — one cluster, hard-pinned tiers

**One OpenSearch cluster.** No cross-cluster search. Strict tier isolation is enforced by **shard allocation filtering** with `require` rules (a *hard* constraint: a shard that cannot satisfy its rule goes **unassigned**, i.e. the cluster reports yellow, rather than *ever* leaking onto the wrong tier).

**Node attributes / roles**
- Worker nodes: `node.roles: [data, ingest]`, `node.attr.role: worker`, `node.attr.box: <node_id>`.
- Storage nodes: `node.roles: [cluster_manager, data, ingest]`, `node.attr.role: storage`.

**Why `ingest` on every node.** Every index sets `default_pipeline: alice-add-ingest-time`, and OpenSearch runs a `default_pipeline` on an **ingest-role** node — but explicitly listing `node.roles` *replaces* the implicit default role set (which includes `ingest`), so an index write against a cluster with zero ingest nodes throws. Both tiers therefore carry `ingest`. Keeping it on the **workers** is also what lets the local info write run its pipeline locally instead of hopping to another node — preserving the zero-network-hop info path.

**Quorum.** The 3 storage nodes are the only manager-eligible nodes → majority = 2, tolerates the loss of one storage node. Workers never carry `cluster_manager` and never participate in manager election.

---

## 4. Index design & placement

| index | tier | shards | replicas | allocation rule | contents |
|---|---|---|---|---|---|
| `generic-log-info-<node_id>` (one per worker) | worker | 1 | **0** | `require.box: <node_id>` | dds-info + stdout-info |
| `generic-log-other` | storage | 3 | **2** | `require.role: storage` | dds-other + stdout-other |
| `infologger` | storage | 3 | **2** | `require.role: storage` | infologger |

**Why `generic-log-info` must be per-node, not just `require.role: worker`.** A single info index with shards spread across both workers would still force node-01 (writing to its local coordinator) to forward roughly half its documents to a shard on node-02 — the exact cross-network firehose v2 exists to kill. A per-node index with **1 shard pinned to its own VM** (`require.box`) means the write path is: worker → `localhost` coordinator → local shard → **zero network hops**. This is the mechanism that makes "kept local" literally true.

**Storage indices unchanged from v1 in shape:** 3 shards + 2 replicas across 3 storage nodes = 9 shard copies, a balanced 3 per node. Only their *allocation* is new (pinned to the storage tier).

**Mapping/codec/pipeline** carry over from v1 verbatim: `dynamic:false` for generic, `dynamic:strict` for infologger, `zstd` codec, the `alice-add-ingest-time` two-timestamp pipeline as each index's `default_pipeline`.

---

## 5. Data flow / routing changes

The v1 collector pipeline is preserved. The **only edit** is the `family.info` output index name; the other two outputs are unchanged and listed here only to show where their data now lands.

- `family.info` output index: `generic-log-info` → `generic-log-info-{{ node_id }}` (still written to `localhost`, now landing in the node's own pinned local index). **This is the one change.**
- `family.other` output (**unchanged**): same name, still written to `localhost`. The local coordinator now forwards it to the storage-tier shards — one network hop, acceptable because this tier is low-volume.
- `infologger` output (**unchanged**): same as `family.other`.

Everything upstream (tail inputs, multiline parsers, severity `rewrite_tag`, the lua/parser filters, `node`/`host`/`log_source` stamping) is identical to v1.

---

## 6. Durability model & accepted trade-offs

- **Storage tier:** 3 copies (1 primary + 2 replicas) of `infologger` and `generic-log-other`, mirroring v1's durability, now isolated on nodes that never run the ingest firehose.
- **Worker tier:** 0 replicas by design. **If a worker VM dies, its `generic-log-info-<node_id>` index is lost with no recovery.** This is an explicit, accepted decision — it is the trash tier; local-and-disposable is the point. Stated here so it is a decision, not a surprise.

---

## 7. Shared-script hazard — read before editing anything under `init/`

**`init/opensearch/templates.sh` and `init/dashboards/patterns.sh` are shared, verbatim, by three consumers:** `docker-compose.yml`, `docker-compose.mocks.yaml` (both mount `./init/...` and run the scripts), and the native deploy (`deploy/roles/dashboards/tasks/bootstrap.yml` copies them byte-for-byte). **Editing them in place changes the paper-airplane compose stacks too.**

Concretely, a naive v2 edit breaks compose: adding `index.routing.allocation.require.role: storage` to `generic-log-other`/`infologger` leaves those shards **permanently unassigned** on the single-node compose stack (no node carries `node.attr.role: storage`), turning it red. Likewise the info index is a single `generic-log-info` in compose but per-node `generic-log-info-<node_id>` in native.

**Resolution: the native bootstrap must not reuse the shared scripts.** Fork them into tier-aware Ansible templates under the `dashboards` role (rendered native-only), and point `bootstrap.yml` at the rendered copies. The shared `init/` scripts stay untouched so the compose stacks keep working. This is the cleanest split; env-gating one shared shell script would add branching to a file that must stay comment-free.

## 7b. Implementation change surface (from v1)

1. **`deploy/inventory.yml`** — split `alice_nodes` into two groups: `workers` (2 hosts) and `storage` (3 hosts, `role: storage`). **Worker node-ids are pinned to `node-01` and `node-02` with `epn_partition` 0 and 1** — this is load-bearing: `replay.py` derives `COLLECTOR_HOSTS = [node-01 … node-0{NODE_COUNT}]` and the producer symlink `NODES_ROOT/<node_id> → log_root` only lines up when `COLLECTOR_HOSTS[epn_partition] == node_id`. A worker named anything else routes its own partition into a black hole. Storage nodes take `node-03/04/05`. Workers also get `box: <node_id>`. The OpenSearch cluster is `workers` + `storage`; producers and collectors run on `workers` only; `control` (Dashboards + bootstrap) moves onto a **storage** node.
2. **`deploy/group_vars/all.yml`** — `node_count` derives from the **`workers`** group (→ 2), not from the full cluster (deriving it from all 5 nodes would set `NODE_COUNT=5` and silently strand partitions 2–4). Seed hosts / initial manager list derive from the `storage` group; Dashboards points at the storage nodes.
3. **`deploy/roles/opensearch/templates/opensearch.yml.j2`** — add `node.roles` (`[data, ingest]` for workers, `[cluster_manager, data, ingest]` for storage — `ingest` is mandatory because every index has a `default_pipeline` and explicit `node.roles` drops the implicit `ingest` role) and `node.attr.role` / `node.attr.box` per tier; `discovery.seed_hosts` and `cluster.initial_cluster_manager_nodes` reference storage nodes only.
4. **`deploy/roles/collector/templates/collector.yaml.j2`** (native-only; the compose bundle at `images/node/fluent-bit/collector.yaml` is untouched) — one line: `family.info` output index → `generic-log-info-{{ node_id }}`.
5. **Native OpenSearch bootstrap (forked from `init/opensearch/templates.sh`, per §7):** info template matches `generic-log-info-*` (1 shard, 0 replicas); `generic-log-other` and `infologger` templates add `index.routing.allocation.require.role: storage`; a per-worker step pre-creates each `generic-log-info-<node_id>` with its own `require.box: <node_id>` (a wildcard template cannot set a per-index `box`; the pre-create merges with the template for mapping/codec/pipeline). **Ordering is load-bearing:** the `alice-add-ingest-time` ingest pipeline and the index templates must be applied *before* the per-node pre-creates, because `default_pipeline` and the mapping are resolved at index-creation time (same reason v1 applies the pipeline first).
6. **Native Dashboards index-patterns (forked from `init/dashboards/patterns.sh`, per §7):** the `generic-log-info` pattern becomes `generic-log-info-*` so the per-node indices are visible in Discover. `infologger` and `generic-log-other` unchanged.
7. **`deploy/site.yml`** — plays retarget to the new groups (opensearch on `workers` + `storage`; collector + producer on `workers`; dashboards on the storage `control` host).
8. **`deploy/provision.yml`** — provision 5 VMs across the two groups (all m2.medium); security groups unchanged except the Dashboards group now attaches to the storage control host.
9. **`deploy/README.md`** — documents the index families, replica counts, and 3-node assumptions; update for the two-tier layout (stale otherwise, docs-only).

The `epn %3 → %2` re-partition needs **no code change** to `replay_partition_wrapper.py` or `images/replay/replay.py` — both read `NODE_COUNT` from the environment, which now resolves to 2 (verified: `replay.py` line 68/72/82).

---

## 8. Constraints for implementation agents

- **No code comments.** Do not add explanatory comments to any produced or edited code, templates, or config. The existing heavy comment blocks in touched files may be trimmed but new commentary must not be introduced. Code is reviewed by hand.
- Preserve v1 behaviour everywhere not listed in §7b. `images/replay/replay.py` remains PRESERVED ground truth — never edited.
- Every allocation rule uses `require` (hard), never `include`/`prefer`.

---

## 9. Open / deferred

- **Lubos — `dds-other` value.** Default routes it to storage. If dds is worthless end-to-end, re-cut so all dds stays on the worker tier and only stdout-other + infologger reach storage.
- **Retention (ISM).** Thanasis gives `other` longer retention than `info`. Still deferred — meaningful retention needs time-based / rollover-aliased indices (a write-path change), not a settings tweak on these static index names. Same reasoning as v1.
- **Quota ceiling.** v2 sits at exactly 10 cores / 5 instances. A 3+3 tier (restoring symmetry and a spare) needs an `instances` quota bump to 6.
- **Access to Thanasis' repo** — requested from Lubos.
