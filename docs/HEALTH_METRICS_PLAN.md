# HEALTH_METRICS_PLAN.md — Scalable platform health (Fluent Bit push)

**Goal:** replace the control-host pull of per-collector Fluent Bit metrics with **push from each collector node**, so platform health scales to **100+ nodes** without a central scrape fan-out — while keeping `cockpit-metrics` as the single store for the cockpit, Layer 0 monitors, and Layer 0.5 RCF detectors.

**Non-goals:** Explore / Workspaces / OSD Prometheus datasource; standing up Prometheus as the AD store; redesigning log AD (`PLAN.md`). Prometheus remains an optional later ops sidecar, not a prerequisite for this plan.

**Depends on:** cardboard metrics + detection path in `PLAN.md` (monitors/detectors over `cockpit-metrics` stay consumers of the same index shape).

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
   - `node: {{ node_id }}` (same identity monitors already key on)
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

A crashed / stopped Fluent Bit produces **silence**, not `fb_up: 0`. Nodes that never appear in the aggregation are invisible to the current trigger.

**Required monitor redesign:**

1. **Roster** — on deploy (or periodically), write/update a document the monitors can trust, e.g. `kind: roster`, `collectors: ["node-01", …]` (from Ansible `groups['workers']` / future inventory). Alternatively a tiny `cockpit-fleet` index; keep it boring.
2. **`collector-down` (absence)** — for each rostered collector, no `kind:fluentbit` doc with that `node` in the last ~2 minutes → page. Implementation options (choose in Stage B):
   - Alerting query / bucket monitor over roster + absent heartbeat, or
   - Thin control job that only emits synthetic `fb_up: 0` stubs for rostered nodes missing a heartbeat (preserves today’s monitor JSON with minimal edits — acceptable transitional hack).
3. **`collector-unhealthy`** — unchanged idea: recent docs with `fb_healthy: 0` (process up, health check failing).
4. **`telemetry-silence`** — split or clarify:
   - **Control-plane silence:** no `kind:cluster` (or `osd`) docs → thin poller dead.
   - **Fleet FB silence:** optional — fraction of roster missing heartbeats (catch “everyone lost OS credentials” without waiting per-node pages).

Layer 0.5 `ingest-flow` detector stays on `kind:fluentbit` features (`output_*_delta`); it benefits from push as long as field names and cadence stay comparable (~30 s → 1 min detector interval).

---

## 5. Thin control-plane poller (what remains of `alice-metrics`)

Strip `fluentbit_docs()` and `FB_TARGETS` from `metrics_poller.py` (or replace the unit with a smaller script). Keep:

- `cluster` / `index` / `node` / `osd`
- optional: roster publish + synthetic down stubs (if that transition path is chosen)
- **Move** `ensure_info_indices()` out — bootstrap/ops concern, not metrics

Firewall: once nothing scrapes worker `:2020` from control, the rich rule opening metrics to the control node can be **removed or tightened to localhost-only** (health pipeline uses loopback). Keep `:2020` bound for local scrape/debug if useful.

At 100+ OS nodes, `_nodes/stats` payload size may need paging / metric subsetting later — out of scope until measured; still one HTTP call, not N.

---

## 6. Migration roadmap

### Stage A — Prove push on cardboard (one worker, dual-write)

1. Spike the FB health pipeline on **one** worker: self-metrics → Lua reshape → `cockpit-metrics`.
2. Dual-write: leave the poller’s `fluentbit` scrape on; compare poller docs vs push docs for the same `node` (field parity, delta sanity, cadence).
3. Gate: ≥95% interval alignment for 30+ minutes; deltas match within restart edge cases; no health docs leaking into log indices.

### Stage B — Absence-based collector-down + roster

1. Publish roster from Ansible.
2. Land absence (or stub) `collector-down`; keep unhealthy / drop / retry monitors on pushed fields.
3. Gate: `systemctl stop fluent-bit` on one worker → page within ~2 min **without** poller writing `fb_up: 0`; restart → clear.

### Stage C — Cut over Fluent Bit kind to push-only

1. Remove `fluentbit_docs()` + `FB_TARGETS` from `alice-metrics`.
2. Enable health pipeline on **all** workers via `collector.yaml.j2`.
3. Close control→worker `:2020` firewall hole if unused.
4. Update cockpit copy (`gen_cockpit.py` blurb: “pushed by Fluent Bit” / “cluster samples by alice-metrics”).
5. Gate: `make deploy` twice idempotent; kill FB on one node → absence alert; stop thin poller → control-plane silence only (FB heartbeats continue).

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
| Lua filter (new file under collector role) | Reshape + deltas + `node` / `fb_*` fields |
| `metrics_poller.py` | Drop FB scrape; keep cluster-scoped kinds; drop or relocate info-index ensure |
| `alice-metrics.service.j2` | Drop `FB_TARGETS` |
| Collector firewall task | Stop opening `:2020` to control (after cutover) |
| Monitors under `files/monitors/` | Absence-based `collector-down`; clarify silence monitors |
| Roster publish | Small bootstrap script or poller tick |
| `gen_cockpit.py` / README | Document push vs thin poller |
| `verify_detection.py` | Assert push heartbeats + absence alert path |

Index template `alice-cockpit-metrics`: prefer **no** breaking field renames. Add roster fields only if they live in the same index (`kind: roster`).

---

## 8. Decisions locked by this plan

1. **Fluent Bit push** for `kind:fluentbit` — chosen over Prometheus-first and over scaling the Python puller.
2. **Keep `cockpit-metrics`** as the metrics store for cockpit + AD/alerting — no Explore migration.
3. **Thin central poller remains** for cluster/index/node/osd.
4. **`collector-down` becomes absence-of-heartbeat** (with roster), not “poller observed `fb_up: 0`.”
5. **Prometheus is optional later**, not the scalability fix for this product path.

---

## 9. Why this is the right jump

The cardboard poller conflates two jobs: **sample the cluster** (central, O(1)) and **sample every collector** (inherently O(N)). The second job belongs on the collectors. Fluent Bit already runs there, already buffers to disk, already writes to OpenSearch — extending it with a health document is the smallest scalable step that keeps Layer 0/0.5 and the ALICE Cockpit on one index.

Prometheus can still show up for farm-wide ops graphs. It should not own the heartbeat that OpenSearch monitors and RCF detectors consume.
