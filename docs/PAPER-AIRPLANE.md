# Paper Airplane — Local Recreation of the Scalable Logging Architecture

**Grill summary / design handshake.** This is the agreed design for the *local* prototype ("paper airplane") that recreates Athanasios Papadopoulos's Scalable Logging Architecture (see `ARCHITECTURE.md`) on a single machine (M3 Max, 128 GB).

> `ARCHITECTURE.md` is the **target** we simplify *from*, not our spec. Where this doc diverges from it, the divergence is **deliberate and listed** at the bottom.

---

## 0. Goal — definition of done

**"Done = the full shape at small scale" (grill option C).** A mock log of each type flows end-to-end and is visible in Grafana **two ways** — a live-tail panel *and* a historical-search panel — with the **3 index families** populated under per-family lifecycle policies, and **alerts firing into Alertmanager** from both Grafana and OpenSearch.

Not a load test. 10 collectors on one host proves *functional/integration* correctness and lets us run the failure experiments below — it does **not** prove PB-scale.

---

## 1. Topology — two toggleable profiles

Switch via **docker-compose profiles**: `--profile direct` vs `--profile bus`. Kafka is present in **both** (it always feeds the live-tail path); the only deltas are how OpenSearch gets fed.

```
            ┌─────────────────────────── node-01 … node-10 ───────────────────────────┐
            │  bundled image:  [InfoLogger producer]──socket──┐                        │
            │                  [DDS producer]──file─┐         │                        │
            │                  [stdout producer]──file─┐      │                        │
            │                                      ▼  ▼  ▼                             │
            │                              [ Fluent Bit collector ]  (classifies →      │
            │                                      │   │           3 index families)    │
            └──────────────────────────────────────┼───┼──────────────────────────────┘
                                                    │   │
              profile=direct:  ── OpenSearch ◄──────┘   └──────► Kafka (3 topics) ──┐
                                                                                    │
              profile=bus:     ── Kafka (3 topics) ─► Kafka Connect ─► OpenSearch   │
                                        │            (OpenSearch Sink)              │
                                        └────────────────────────────────────────► │
                                                                                    ▼
                                                              [ Fluent Bit aggregator ]
                                                                        │ Grafana Live push
                                                                        ▼
        OpenSearch ──► Grafana (historical search)  +  Grafana (live-tail panel) ◄──┘

        Alerting:  Grafana rules ──► Alertmanager
                   OpenSearch monitor ──(v2 webhook)──► Alertmanager
```

- **`direct` (Thanasis' tee):** collector → OpenSearch directly **and** collector → Kafka → aggregator → Grafana Live.
- **`bus`:** collector → Kafka → **Kafka Connect OpenSearch Sink** → OpenSearch **and** Kafka → aggregator → Grafana Live. Collector's OpenSearch output is off; the Connect consumer is added.

**Payoff experiment:** under `BURST`, `docker compose stop opensearch` for ~60 s. `direct` drops logs once the collector's filesystem buffer fills; `bus` loses nothing and catches up from Kafka. Twin experiment: kill a Kafka broker mid-burst → zero loss (RF=3).

---

## 2. Component decisions

| Area | Decision |
|---|---|
| **Nodes** | 10 explicit, templated services `node-01..node-10` (Jinja/script-generated, not hand-maintained). Each is **one bundled image** running all 3 producers + a Fluent Bit collector under a tiny supervisor. Distinct `NODE_ID`/hostname per node (so dashboard node-filtering works). |
| **Per-node sources** | Each node emits **all three** types (faithful to a real EPN node). InfoLogger → **socket** (FB `forward`/TCP input, localhost); DDS → **file** (FB `tail`); stdout → **file** (FB `tail`). No `infoLoggerD` daemon — the mock producer speaks the forward wire-path directly. |
| **Producer schemas** | Real schemas so real CERN S3 logs slot in later with zero reshaping. InfoLogger: rich fields (`hostname, severity, partition, system, facility, detector, run, errline, errsource, …`). DDS: lean (`severity D/IMPORTANT/WARN/INFO, node, log, source_file`). stdout: unstructured. |
| **Volume** | **Proportional**, not absolute (PB-scale is impossible locally; we match *ratios*). DDS-dominant per Thanasis ("DDS much denser"): **DDS : stdout : InfoLogger ≈ 10 : 3 : 2** (~50/15/10 msg/s/node → ~750/s total baseline), all env-tunable. `BURST` knob spikes DDS 10–20×. |
| **Severity mix** | Built-in distribution per producer ~**90% info / 8% warn / 1.5% error / 0.5% debug** — so `generic-log-other` populates and the error-rate alert has fuel. InfoLogger error lines carry the rich fields. |
| **Classification** | At the **collector** (Fluent Bit). `source == infologger → infologger`; else by severity: `info → generic-log-info`, `warn/error/debug → generic-log-other`. (Index families are by severity/access-pattern, **not** by producer type.) |
| **Kafka topics** | **3 topics = 3 index families** (collector routes into them); Kafka Connect maps **topic → index 1:1**. ~3 partitions/topic. |
| **Kafka brokers** | **3-broker KRaft** cluster (combined broker+controller, no Zookeeper). **RF=3** on log topics + internal topics (`__consumer_offsets`, Connect config/offset/status). `min.insync.replicas=2`. |
| **Consumer (bus only)** | **Kafka Connect + OpenSearch Sink connector** (Aiven OSS) — the production-grade choice. Config via Connect REST API. |
| **OpenSearch** | **3-node cluster**, role-tagged via shard allocation awareness: **1× `worker`** (holds `generic-log-info`/`-other`), **2× `storage`** (hold `infologger`; replica lands on the 2nd storage node → genuinely demonstrates *dedicated storage + higher replication*). All 3 cluster-manager-eligible (quorum 3). Heap-capped ~2 g each. **No co-location** with producer nodes. |
| **Index lifecycle (ISM)** | Per-family: `generic-log-info` → strong compression, short retention; `generic-log-other` → strong compression, long retention; `infologger` → fast access, higher replication. (Exact rollover/retention numbers left to implementation — already hands-on with ISM.) |
| **Aggregator** | **Fluent Bit** (not Fluentd — fidelity + one config language). Consumes all 3 Kafka topics → **Grafana Live** push endpoint (`/api/live/push/<stream>`). |
| **Grafana** | Live-tail panel (Grafana Live) + historical search over OpenSearch. Three dashboards: InfoLogger logs, InfoLogger live feed, DDS logs. Node/severity/partition filters. |
| **Alerting** | **Both, staged** (grill option C): (1) Grafana unified alerting → external Alertmanager *first*; (2) then **one** OpenSearch Alerting monitor → Alertmanager v2 webhook (custom destination). Alertmanager dedups/routes — demonstrating *why it's there* (unifies two alert sources). |
| **Security** | **Off everywhere** (OpenSearch security plugin disabled, no TLS, Kafka `PLAINTEXT`). Deliberate divergence — see below. |
| **Orchestration** | docker-compose; **ARM-native images**; KRaft (no Zookeeper); heap caps; profiles for the topology toggle. Give the Docker VM real resources (e.g. `colima start --cpu 8 --memory 32 --disk 100`). |

---

## 3. Deliberate divergences from Thanasis (documented, not accidental)

1. **Single 3-node cluster — no federation.** Thanasis' real scale forces many clusters + Cross-Cluster (federated) Search; the airplane is one cluster. *(Federated search = a later flight.)*
2. **No OpenSearch co-location** on producer nodes ("each node indexes its own local logs") — fights the minimal-CPU-on-experiment-nodes constraint; not worth it locally.
3. **No `infoLoggerD` daemon fork** (Thanasis' only code change). Mock InfoLogger producer speaks the forward/TCP wire-path directly.
4. **Security off** — orthogonal to every lesson here (topology, backpressure, durability, families, alerting). Hardening is a separate flight, not part of making the plane fly.
5. **Absolute volume scaled down** — proportions + severity distribution preserved; PB-scale is not.

---

## 4. Open items / later flights (out of scope for first flight)

- **S3/MinIO replay** of real CERN logs — *not yet decided*. Recommended as flight 2: stand up local MinIO as the S3 stand-in, add an `S3-replay` producer mode behind the same interface, point ISM searchable-snapshots at MinIO (this also de-risks the on-prem object-store question from the SOTA verdict).
- **Exact ISM policy numbers** (rollover size, retention windows).
- **Kafka Connect hardening** (dead-letter queue, converter/SMT specifics).
- **Columnar cold tier** (ClickHouse) behind Kafka — structure compose so it can slot in later; do not build yet.
- **Security hardening pass** (TLS, OpenSearch security plugin, Kafka SASL).
- **Cross-cluster / federated search** experiment (two clusters + CCS).
