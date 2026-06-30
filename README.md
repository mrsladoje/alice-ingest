# alice-ingest — Paper Airplane

A **local recreation** of the ALICE O2 Scalable Logging Architecture, shrunk to
run on a single machine. This is the "paper airplane": the full *shape* of the
real platform at small scale, for hands-on learning and failure experiments.

- **Design target:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the real platform we simplify *from*.
- **Local design + deliberate divergences:** [`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md).

> **This tag (`paper-airplane-v1`)** is *flight 1*: a single node ships **mock**
> logs directly into OpenSearch, viewable in OpenSearch Dashboards.
> No Kafka, no Grafana yet — deliberately descoped. Real CERN S3 log replay is
> the next flight.

---

## What's in flight 1

```
  node-01  (one bundled image)
  ┌─────────────────────────────────────────────┐
  │  InfoLogger producer ──TCP:5170──┐           │
  │  DDS producer ────────file────┐  │           │
  │  stdout producer ─────file──┐ │  │           │
  │                             ▼ ▼  ▼            │        ┌──────────────┐
  │              [ Fluent Bit collector ]  ───────┼───────▶│  OpenSearch  │
  │              classify by severity →           │        │ (single node)│
  │              3 index families                 │        └──────┬───────┘
  └─────────────────────────────────────────────┘                │
                                                          ┌────────▼────────┐
                                                          │   Dashboards    │
                                                          │  (Discover UI)  │
                                                          └─────────────────┘
```

Each node runs **three mock producers** (faithful to a real EPN node) plus a
Fluent Bit collector that classifies every record into one of **three index
families** — *by severity / access-pattern, not by producer*:

| Index | Holds |
|-------|-------|
| `infologger` | All InfoLogger records (rich schema, the operationally-critical stream) |
| `generic-log-info` | `INFO`-level DDS + stdout (the high-volume firehose) |
| `generic-log-other` | `WARN` / `ERROR` / `DEBUG` DDS + stdout (what you investigate) |

Fluent Bit buffers to **filesystem** storage, so a brief OpenSearch outage is
ridden out from disk rather than lost (see the durability knobs below).

---

## Prerequisites

- **Docker** with Docker Compose v2.
- A Docker VM with real resources — OpenSearch alone is configured for a **3 GB
  heap**. On macOS with Colima:
  ```
  colima start --cpu 8 --memory 32 --disk 100
  ```
- ARM64 or x86-64 — images are multi-arch.

---

## Run it

```bash
# Build the node image and start everything (OpenSearch comes up first)
docker compose up -d --build

# Watch the node boot (producers + Fluent Bit)
docker compose logs -f node-01
```

OpenSearch needs ~30–60 s to become healthy on first boot; the node waits for it
via a healthcheck.

## Verify

```bash
# 1. Three families populated (note: quote URLs — '?' is a shell glob in zsh)
curl -s 'localhost:9200/_cat/indices?v'
#   expect: infologger / generic-log-info / generic-log-other with docs.count > 0

# 2. Peek at a document
curl -s 'localhost:9200/generic-log-info/_search?size=1&pretty'

# 3. Live pipeline metrics (Fluent Bit monitoring API, inside the container)
docker compose exec node-01 curl -s localhost:2020/api/v1/storage
```

### See it in Dashboards

1. Open **http://localhost:5601**.
2. **☰ → Dashboards Management → Index patterns → Create index pattern**.
3. Create two patterns: `generic-log-*` and `infologger`.
4. When asked for the **time field**, choose **`@timestamp`** (it exists on
   every document; the `time` field is stdout-only and would hide other logs).
5. Go to **Discover**, set the range to **Last 15 minutes**, and filter by
   `severity`, `node`, `detector`, etc.

> **Note:** the log indices show **yellow** health. That's expected on a single
> node — each index has 1 replica with nowhere to be allocated (a replica can't
> share a node with its primary). Data is fully present and queryable. On the
> real multi-node cluster these replicas allocate and go green.

---

## Tuning knobs

Producer rates & burst behaviour are env-tunable (defaults in the producer
sources, `images/node/producers/`):

| Var | Default | Meaning |
|-----|---------|---------|
| `DDS_RATE` / `STDOUT_RATE` / `INFOLOGGER_RATE` | 50 / 15 / 10 | msg/s/node |
| `DDS_BURST_MULTIPLIER` | 15 | DDS spike factor under burst |

**Burst:** `touch /control/burst` (shared mount) spikes the DDS firehose; `rm`
it to return to baseline — used for the durability experiment.

**Durability** (in `images/node/fluent-bit/collector.yaml`): each OpenSearch
output has `storage.total_limit_size` (on-disk buffer cap; oldest dropped when
full) and `retry_limit`. Current values are intentionally small for fast,
visible drop behaviour during testing.

---

## Teardown

```bash
docker compose down            # stop containers, keep data volumes
docker compose down -v         # also wipe OpenSearch data + Fluent Bit buffers
```

---

## Deliberate divergences from the real platform

Security is **off** (no TLS, OpenSearch security plugin disabled), it's a
**single** OpenSearch node (not a federated multi-cluster), data is **mock**
(real CERN S3 replay is next), and absolute volume is scaled down while
**proportions and severity mix are preserved**. Full rationale in
[`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md).
