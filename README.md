# alice-ingest — Paper Airplane

A **local recreation** of the ALICE O2 Scalable Logging Architecture, shrunk to
run on a single machine. This is the "paper airplane": the full *shape* of the
real platform at small scale, for hands-on learning and failure experiments.

- **Design target:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the real platform we simplify *from*.
- **Local design + deliberate divergences:** [`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md).

> **This tag (`paper-airplane-v2`)** is *flight 2*: a single node ships **real
> CERN S3 logs** (replayed from the `epn-backup-logs` bucket) — or **mock** logs
> in offline mode — into OpenSearch, with Dashboards index patterns
> **auto-provisioned** on startup. Still no Kafka, no Grafana (next flight).

---

## Quickstart (real CERN S3 replay — what Lubos runs)

Prereqs: Docker + Docker Compose v2, and a Docker VM with real resources
(OpenSearch alone wants a **3 GB heap** — see [Prerequisites](#prerequisites)).

**1. CERN S3 credentials (one-time).** The replay container reads real ALICE
logs using an AWS profile named `cern_s3`. Drop your keys into
`~/.aws/credentials` on the host — Compose mounts this file **read-only** into
the replay container only:

```ini
# ~/.aws/credentials
[cern_s3]
aws_access_key_id     = <YOUR_CERN_S3_ACCESS_KEY>
aws_secret_access_key = <YOUR_CERN_S3_SECRET_KEY>
```

**2. Launch.**

```bash
make run
```

Brings up OpenSearch + Dashboards + one node + the replay service. Replay
**autostarts** and streams real DDS / stdout / InfoLogger logs through Fluent
Bit into OpenSearch. First boot: OpenSearch takes ~30–90 s to go healthy
(longer under CPU contention — it's booting, not stuck).

**3. View.** Open **http://localhost:5601 → Discover**. The three index
patterns (`infologger`, `generic-log-info`, `generic-log-other`) are already
created — no manual setup. Pick one, set a time range, and browse.

**Teardown.**

```bash
make down            # stop, keep data volumes
make down volume     # stop + wipe all volumes (fresh next run)
```

**No CERN credentials?** Run fully offline with mock producers instead:

```bash
make mocks           # same pipeline + auto-patterns, synthetic logs, no S3
```

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

## Run modes

```bash
make run      # real stack: OpenSearch + Dashboards + node + S3 replay (needs cern_s3 creds)
make mocks    # offline stack: same pipeline, synthetic producers, no S3
make down     # stop (add `volume` to also wipe data): make down volume
```

Both build images on first run and wait for OpenSearch's healthcheck before
starting dependents. Watch the node boot with `docker compose logs -f node-01`.

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

1. Open **http://localhost:5601 → Discover**.
2. The three index patterns (`infologger`, `generic-log-info`,
   `generic-log-other`) are **auto-created** on startup by the `dashboards-init`
   container — all on time field **`@timestamp`** (the one field present on
   every document). No manual setup.
3. Pick a pattern, set the range to **Last 15 minutes**, and filter by
   `severity`, `host`, etc.

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
**single** OpenSearch node (not a federated multi-cluster), data is **real CERN
S3 replay** (or mock in offline mode), and absolute volume is scaled down while
**proportions and severity mix are preserved**. Full rationale in
[`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md).
