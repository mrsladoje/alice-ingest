# HEALTH_METRICS_PLAN.md — Scalable platform health (Fluent Bit push)

**Goal:** replace the control-host pull of per-collector Fluent Bit metrics with **push from each collector node**, so platform health scales to **100+ nodes** without a central scrape fan-out — while keeping `cockpit-metrics` as the single store for the cockpit, Layer 0 monitors, and Layer 0.5 RCF detectors.

**Non-goals:** Explore / Workspaces / OSD Prometheus datasource; standing up Prometheus as the AD store; redesigning log AD (`PLAN.md`). Prometheus remains an optional later ops sidecar, not a prerequisite for this plan.

**Depends on:** cardboard metrics + detection path in `PLAN.md` (monitors/detectors over `cockpit-metrics` stay consumers of the same index shape).

**Status (2026-07-30): Stages A–D are implemented in code; not one gate has been run on real VMs.** Every stage below is written, wired into `make deploy`, and statically checked; none has been observed. The prerequisite soak in `PLAN.md` § 0 has still not happened either, so this plan inherits that debt rather than clearing it. Two implementation notes that change how the plan reads:

- **§ 3.1 self-metrics input — deviation.** Neither named candidate was used. `fluentbit_metrics` and `prometheus_scrape` both emit *metric* chunks, which Lua filters do not process and which the OpenSearch output ships in a metrics shape rather than the flat `cockpit-metrics` schema § 2 promises to preserve. The health pipeline uses an `exec` input running a small loopback sampler that prints one JSON record, so the existing filter/output chain applies unchanged and the schema contract genuinely holds. It is still one daemon on the node, still the log ship path, still no cross-node scrape — the plan's actual argument survives; only the input plugin differs.
- **§ 6 staging — collapsed to the end state, with the transition kept as a switch.** Stages A/B/C describe a dual-write window that only makes sense while someone is watching a live cluster. The tree ships the Stage C end state (push-only, `node` retired, scrape closed) with `health_metrics_emit_legacy_node` and `collector_metrics_scrape_open` as the two switches that re-open the Stage A comparison window on demand. That is the same migration, expressed as configuration rather than as three commits.

---

## 0. What we have today

One systemd service on the control host — `alice-metrics` / `metrics_poller.py` — every 30 s:

| `kind` | Source | Scaling with N collectors |
|---|---|---|
| `cluster` | `GET _cluster/health` | **O(1)** — one cluster call |
| `index` | `_cat/indices` + `_stats` | **O(indices)** — independent of collectors |
| `node` | `GET _nodes/stats` | **O(1)** call, payload grows with OS nodes |
| `fluentbit` | HTTP scrape each worker `:2020` (`FB_TARGETS=…`) | **O(N)** scrapes from one host |
| `osd` | `GET` Dashboards `/api/status` | **O(1)** |

It bulk-indexes flat docs into `cockpit-metrics`. Counters are cumulative; the poller keeps in-memory `_prev` and emits `*_delta` fields. Unreachable Fluent Bit becomes an explicit `fb_up: 0` doc (down is a sample, not a gap). Workers open `:2020` only to the control node (firewalld).

That design is correct for the 5-VM cardboard airplane. It is the wrong shape for a 100+ collector farm.

---

## 1. Why the current system fails at 100+

### 1.1 Central pull of Fluent Bit is the bottleneck

At N collectors the control host must complete N HTTP GETs to `:2020` every interval (metrics + health). Failures compound: slow nodes stretch the tick; timeouts leave partial fleets; inventory is a static `FB_TARGETS` env var regenerated only on deploy. Adding a node means redeploying the poller unit, not just starting a collector.

### 1.2 Single blind spot on the scrape path

If `alice-metrics` dies, **all** `kind:fluentbit` samples stop — and today that is also how `fb_up: 0` is manufactured. `telemetry-silence` catches total darkness; it does not replace a healthy per-node heartbeat. `PLAN.md` already flags this; at cardboard scale “not worth fixing.” At 100+ it is.

### 1.3 What does *not* need to move

Cluster / index / node / OSD samples are **cluster-scoped**, not per-collector. One (or HA) control-plane agent should keep producing them. Pushing `_cluster/health` from every worker would multiply identical docs by N and confuse aggregations.

**Punchline:** scale the **collector self-telemetry** path. Keep a **thin control-plane poller** for OpenSearch + Dashboards health.

---

## 2. Target architecture

```
  each collector node                         control host (thin)
  ┌─────────────────────────────┐             ┌──────────────────────────┐
  │ Fluent Bit                  │             │ alice-metrics (slim)     │
  │  logs → OS (unchanged)      │             │  cluster / index / node  │
  │  + health pipeline:         │             │  osd                     │
  │    self-metrics → reshape   │             │  (+ fleet / silence aid)│
  │    → kind:fluentbit docs    │             └────────────┬─────────────┘
  │    → OpenSearch             │                          │
  └──────────────┬──────────────┘                          │
                 │                                         │
                 └────────────►  cockpit-metrics  ◄────────┘
                                      │
                    cockpit panels · Layer 0 monitors · Layer 0.5 AD
```

| Signal | Producer (target) | Why |
|---|---|---|
| `kind:fluentbit` | **Fluent Bit on that node** (push) | Scales with collectors; no central target list for scrapes |
| `kind:cluster` / `index` / `node` / `osd` | **Thin `alice-metrics`** on control (or later HA pair) | Naturally centralized; O(1) or O(OS nodes) |
| Fleet roster | Ansible → small `kind:roster` doc (or equivalent) | Lets monitors know *who should* be heartbeating |
| Info-index recreate | Move out of the poller (ops/bootstrap) | Unrelated to health sampling |

**Contract preserved:** same index (`cockpit-metrics`), same `kind` discriminator, same field names the cockpit / monitors / detectors already use — so detection work in `PLAN.md` is not thrown away.

**One additive exception — the `node` identity split (required by `GROUPING_PLAN.md` § S1d).** `node` is ambiguous today: it means the **collector** on `kind:fluentbit` docs and the **OpenSearch node** on `kind:node` docs, and on a worker VM both are `node-01`. After push that field has two *producers* as well as two meanings — Fluent Bit on the collector, the thin poller for the OpenSearch node — and any alert-inhibition rule matching on `node` alone would cross-suppress unrelated conditions. Resolution is additive, so nothing breaks mid-cutover:

- `kind:fluentbit` docs gain **`collector_id`**; `node` is retained temporarily.
- `kind:node` docs gain **`os_node`**; `node` is retained temporarily.
- Monitors and detectors move onto the explicit fields, and only then is `node` retired.

Both new fields are mapped explicitly in `alice-cockpit-metrics`. Its mapping is `dynamic: false`: an unmapped field can remain in `_source`, but it is not indexed or available for the aggregations that monitors and detectors need. `verify_detection.py` therefore asserts both fields on the live mapping, not merely in a sample document.

**Explicitly not required:** Explore, Workspaces, Prometheus, Grafana — for this plan. (Optional later: Prometheus scrapes the same FB `:2020` for long-term ops graphs; AD still reads OpenSearch.)

---

## 3. How Fluent Bit push works (mechanically)

Each collector already runs Fluent Bit with `http_server: on` and health checks (`collector.yaml.j2`). Today that endpoint exists so the **control host can pull**. Target: the collector **also emits** a periodic health document into `cockpit-metrics` using the same process that already talks to OpenSearch for logs.

### 3.1 Pipeline sketch (per collector)

One additional input → filter(s) → OpenSearch output, tagged separately from log families:

1. **Collect self-metrics** (pick one; implementability gate in Stage A):
   - Preferred: Fluent Bit `fluentbit_metrics` input on a 30 s scrape interval, **or**
   - `prometheus_scrape` of `http://127.0.0.1:{{ fluent_bit_http_port }}/api/v1/metrics/prometheus` (loopback — no cross-node scrape).
2. **Reshape** with a Lua (or equivalent) filter into the existing flat schema:
   - `kind: fluentbit`
   - `collector_id: {{ node_id }}` (the explicit identity — see § 2)
   - `node: {{ node_id }}` (retained through the cutover only; removed once monitors and detectors read `collector_id`)
   - `fb_up: 1` (process is alive enough to emit)
   - `fb_healthy: 0|1` from local `/api/v2/health` (or the built-in health_check verdict)
   - cumulative counters: `input_records`, `output_records`, `output_errors`, `output_retries`, `output_retries_failed`, `output_dropped`
   - **deltas:** `*_delta` computed in Lua with per-process previous values (same clamp-at-0 semantics as `metrics_poller.delta`)
3. **Stamp** `@timestamp` = wall clock now (real-time, like today).
4. **Output** `opensearch` → `cockpit-metrics` (storage-tier template already pins the index). Use the same host list / TLS / auth posture as log outputs when security is enabled.

Log pipelines stay untouched. Health docs must **not** enter `generic-log-*` / `infologger` (distinct tag + match).

### 3.2 Why push beats pull (exactly)

| | Current pull | FB push |
|---|---|---|
| Work per interval | Control host does N remote HTTP calls | Each node does **local** work + one index write |
| Adding a collector | Update `FB_TARGETS`, restart poller | Start Fluent Bit with the health pipeline — done |
| Network | Control ← every worker `:2020` (firewall allow-list grows) | Collector → OpenSearch (path already used for logs) |
| Failure of control poller | Entire fleet’s FB metrics go dark | Only cluster/OSD kinds go dark; collectors keep heartbeating |
| Failure of one collector | Poller writes `fb_up: 0` for that node | **No doc** from that node (see §4 — monitors must treat absence) |
| Buffering | None if poller is down | FB filesystem storage can queue health docs like logs |
| Process count | Extra Python service owns FB telemetry | Same daemon already on the node |

Push is better here because **the expensive, N-dependent part is collector self-knowledge**, and the node that has that knowledge is the collector itself. You reuse the log ship path instead of inventing a second scrape mesh.

### 3.3 What push does *not* magically fix

- OpenSearch / Dashboards health still need a control-plane sampler.
- **Dead collector ⇒ no `fb_up: 0` sample.** Detection must move from “saw a zero” to “expected heartbeat missing” (§4).
- Deltas live at the edge; a collector restart resets `_prev` → first interval delta 0 (same as today’s poller restart behaviour).
- Mapping stays `dynamic: false` — new fields still go through `templates.sh` / mapping PUTs.

---

## 4. Detection semantics change (load-bearing)

### 4.1 Today

`collector-down` is a bucket-level monitor: among nodes that **have** recent `kind:fluentbit` docs, fire if `max(fb_up) == 0`. That works because the poller **always** writes a doc for every `FB_TARGETS` entry, including failures.

### 4.2 After push

A crashed / stopped Fluent Bit produces **silence**, not `fb_up: 0`. Collectors that never appear in the aggregation are invisible to the current trigger.

**Required monitor redesign:**

1. **Roster — immutable assignment snapshots, not a mutable collector list.** On deploy, publish a versioned document the monitors *and* `GROUPING_PLAN.md`'s projector can both trust. Never overwrite the only roster: late alerts and anomaly results must be joined to the topology effective at their source event time, not whichever topology is current when the projector happens to ingest them. `topology_version` is a deterministic hash of the canonical collector/assignment content (or an equally stable inventory revision), and the document `_id` derives from it. Re-deploying unchanged inventory is therefore an idempotent no-op with the original `effective_from`; only a real topology change appends a new version. Each snapshot carries:

   - `collectors: [...]` — who should be heartbeating (this plan's need);
   - **`assignments: [{"origin_host": "epn-001", "collector_id": "node-01"}, ...]`** — the authoritative host→collector map, expressed with fixed field names rather than dynamic host-name keys;
   - **`topology_version`** — bumped whenever the collector set or assignment map changes;
   - **`effective_from`** and `published_at` — the event-time boundary and audit time.

   This is the *only* published topology. Prefer a small `cockpit-fleet` index so immutable roster history does not inherit metrics retention; otherwise retain roster snapshots for at least the maximum source-history plus projector-overlap horizon. Nothing downstream may recompute a parent from `epn_num % NODE_COUNT`: that is replay placement rather than production topology, it is a deploy-time function of the `workers` group size, and going from two collectors to three re-assigns two-thirds of hosts. Projector consumers select the greatest `effective_from` not after the source event time, stamp that `collector_id` and `topology_version` once, and refuse to compare across versions. Health monitors select **only the latest effective roster**; they must never union `collectors` across historical snapshots and page retired collectors. Missing snapshots or assignments fail closed: use the explicit sentinel and do not apply collector-scoped inhibition.
2. **`collector-down` (absence)** — for each rostered collector, no `kind:fluentbit` doc with that `collector_id` in the last ~2 minutes → page. Implementation options (choose in Stage B):
   - Alerting query / bucket monitor over roster + absent heartbeat, or
   - Thin control job that emits synthetic `fb_up: 0` stubs carrying the missing `collector_id` (acceptable transitional mechanism, but it must not fall back to a `node`-keyed identity).
3. **`collector-unhealthy`** — unchanged predicate, but bucket on `collector_id`: recent docs with `fb_healthy: 0` (process up, health check failing).
4. **`telemetry-silence`** — split or clarify:
   - **Control-plane silence:** no `kind:cluster` (or `osd`) docs → thin poller dead.
   - **Fleet FB silence:** optional — fraction of roster missing heartbeats (catch “everyone lost OS credentials” without waiting per-node pages).

Layer 0.5 `ingest-flow` stays on `kind:fluentbit` features (`output_*_delta`) but moves its category field from `node` to `collector_id`. It benefits from push as long as feature names and cadence stay comparable (~30 s → 1 min detector interval).

---

## 5. Thin control-plane poller (what remains of `alice-metrics`)

Strip `fluentbit_docs()` and `FB_TARGETS` from `metrics_poller.py` (or replace the unit with a smaller script). Keep:

- `cluster` / `index` / `node` / `osd`; each `kind:node` document adds `os_node` alongside temporary `node`
- optional: synthetic down stubs (if that transition path is chosen); authoritative roster publication remains an Ansible deploy artifact, not a poller-maintained inventory
- **Move** `ensure_info_indices()` out — bootstrap/ops concern, not metrics

Firewall: once nothing scrapes worker `:2020` from control, the rich rule opening metrics to the control node can be **removed or tightened to localhost-only** (health pipeline uses loopback). Keep `:2020` bound for local scrape/debug if useful.

At 100+ OS nodes, `_nodes/stats` payload size may need paging / metric subsetting later — out of scope until measured; still one HTTP call, not N.

---

## 6. Migration roadmap

### Stage A — Prove push on cardboard (one worker, dual-write)

1. Spike the FB health pipeline on **one** worker: self-metrics → Lua reshape → `cockpit-metrics`.
2. Dual-write: leave the poller’s `fluentbit` scrape on; use temporary `node` only as the comparison join between legacy and pushed samples, and assert that every pushed `collector_id` equals that transitional value.
3. Gate: ≥95% interval alignment for 30+ minutes; deltas match within restart edge cases; `collector_id` is indexed and aggregatable; no health docs leak into log indices.

### Stage B — Absence-based collector-down + roster

1. Publish an immutable roster snapshot from Ansible with deterministic `topology_version` and `effective_from`; unchanged inventory reuses the existing version, while a topology change appends exactly one new version and leaves the prior one readable.
2. Land absence (or stub) `collector-down` keyed by `collector_id`; move unhealthy / drop / retry monitors and `ingest-flow` onto the explicit identity.
3. Gate: `systemctl stop fluent-bit` on one worker → that `collector_id` pages within ~2 min **without** the poller manufacturing `fb_up: 0`; restart → clear. Deploy unchanged inventory twice without creating a new version or moving `effective_from`; then change one assignment, prove exactly one version is appended, prove the health monitor uses only that latest roster, and prove late fixtures on both sides of the boundary resolve to their corresponding versions.

### Stage C — Cut over Fluent Bit kind to push-only

1. Remove `fluentbit_docs()` + `FB_TARGETS` from `alice-metrics`.
2. Enable health pipeline on **all** workers via `collector.yaml.j2`.
3. Close control→worker `:2020` firewall hole if unused.
4. Update cockpit copy (`gen_cockpit.py` blurb: “pushed by Fluent Bit” / “cluster samples by alice-metrics”).
5. Gate: `make deploy` twice idempotent; kill FB on one collector → absence alert keyed by `collector_id`; stop thin poller → control-plane silence only (FB heartbeats continue).

### Stage D — Production hardening (before 100+ claim)

1. Auth/TLS on health output consistent with log outputs (security plugin on).
2. Backpressure: health tag should not starve log outputs (separate flush / lower priority if needed).
3. ISM retention for `cockpit-metrics` already in tree — confirm volume at N×30 s docs/interval.
4. Load sketch: N collectors × ~1 doc / 30 s ≈ 2N docs/min; at N=100 → ~200 docs/min — trivial for OS; confirm mapping and monitor query cost with roster cardinality.
5. Optional HA: second thin poller active/passive for cluster/osd kinds only.

### Stage E — Optional ops sidecar (not blocking)

Prometheus / VictoriaMetrics scraping FB `:2020` + node exporters for long-term PromQL/Grafana. **Does not replace** `cockpit-metrics` for OpenSearch Alerting/AD. Do not block Stage C–D on this.

---

## 7. Schema & code touch list (implementation checklist)

| Area | Change |
|---|---|
| `deploy/roles/collector/templates/collector.yaml.j2` | Health input + Lua + `opensearch` output → `cockpit-metrics` |
| Lua filter (new file under collector role) | Reshape + deltas + `collector_id` / temporary `node` / `fb_*` fields |
| `metrics_poller.py` | Add `os_node` alongside temporary `node`; drop FB scrape; keep cluster-scoped kinds; drop or relocate info-index ensure |
| `alice-metrics.service.j2` | Drop `FB_TARGETS` |
| Collector firewall task | Stop opening `:2020` to control (after cutover) |
| `templates.sh.j2` + live mapping update | Map `collector_id`, `os_node`, and whichever roster fields/index are selected; apply them to existing indices |
| Monitors under `files/monitors/` | Move Fluent Bit buckets to `collector_id`, OS-node buckets to `os_node`, land absence-based `collector-down`, split silence monitors |
| `detectors/ingest-flow.json` / `node-health.json` | Move category fields to `collector_id` / `os_node` |
| Roster publish | Idempotently publish content-addressed, event-time-bounded topology snapshots from Ansible; append only when topology changes |
| `gen_cockpit.py` / README | Document push vs thin poller |
| `verify_detection.py` | Assert live mappings, explicit identities, immutable roster history, push heartbeats, and the absence-alert path |

Index template `alice-cockpit-metrics`: no breaking rename during migration. Add `collector_id` and `os_node` explicitly, then retire `node` only after every consumer has moved. If roster versions live in the same index (`kind: roster`), explicitly map `topology_version`, `effective_from`, `published_at`, `collectors`, `assignments.origin_host`, and `assignments.collector_id`; a separate `cockpit-fleet` index is preferred for independent retention.

---

## 8. Decisions locked by this plan

1. **Fluent Bit push** for `kind:fluentbit` — chosen over Prometheus-first and over scaling the Python puller.
2. **Keep `cockpit-metrics`** as the metrics store for cockpit + AD/alerting — no Explore migration.
3. **Thin central poller remains** for cluster/index/node/osd.
4. **`collector-down` becomes absence-of-heartbeat** (with roster), not “poller observed `fb_up: 0`.”
5. **Explicit identities replace overloaded `node`** — `collector_id` for Fluent Bit, `os_node` for OpenSearch nodes, with a temporary additive migration only.
6. **Topology is append-only and event-time versioned** — one mutable current roster is not sufficient for late signals.
7. **Prometheus is optional later**, not the scalability fix for this product path.

---

## 9. Why this is the right jump

The cardboard poller conflates two jobs: **sample the cluster** (central, O(1)) and **sample every collector** (inherently O(N)). The second job belongs on the collectors. Fluent Bit already runs there, already buffers to disk, already writes to OpenSearch — extending it with a health document is the smallest scalable step that keeps Layer 0/0.5 and the ALICE Cockpit on one index.

Prometheus can still show up for farm-wide ops graphs. It should not own the heartbeat that OpenSearch monitors and RCF detectors consume.
