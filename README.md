# alice-ingest — Paper Airplane

```
 █████╗ ██╗     ██╗ ██████╗███████╗
██╔══██╗██║     ██║██╔════╝██╔════╝
███████║██║     ██║██║     █████╗
██╔══██║██║     ██║██║     ██╔══╝
██║  ██║███████╗██║╚██████╗███████╗
╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝╚══════╝
        A Large Ion Collider Experiment
```

A **local recreation** of the ALICE O2 Scalable Logging Architecture, shrunk to
run on a single machine. This is the "paper airplane": the full *shape* of the
real platform at small scale, for hands-on learning and failure experiments.

- **Design target:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the real platform we simplify *from*.
- **Local design + deliberate divergences:** [`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md).
- **Beyond one machine:** Docker Compose (below) remains the local dev path — for a native, no-Docker deploy onto 3 CERN OpenStack VMs with one 3-node OpenSearch cluster, see [`deploy/README.md`](deploy/README.md).

> **This tag (`paper-airplane-v2`)** is *flight 2*: a single node ships **real
> CERN S3 logs** (replayed from the `epn-backup-logs` bucket) — or **mock** logs
> in offline mode — into OpenSearch, with Dashboards index patterns
> **auto-provisioned** on startup. Still no Kafka, no Grafana (next flight).

---

## Quickstart

Prereqs: Docker + Docker Compose v2, and a Docker VM with real resources —
OpenSearch alone wants a **3 GB heap** (on macOS/Colima:
`colima start --cpu 8 --memory 32 --disk 100`).

**1. CERN S3 credentials (one-time).** The replay container reads real ALICE
logs using an AWS profile named `cern_s3`. Put your keys in `~/.aws/credentials`
on the host — Compose mounts this file **read-only** into the replay container
only:

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

**3. View.** Open **http://localhost:5601 → Discover**. The three index patterns
(`infologger`, `generic-log-info`, `generic-log-other`) are already created — no
manual setup. Pick one, set a time range, and browse.

**Teardown.**

```bash
make down            # stop, keep data volumes
make down volume     # stop + wipe all volumes (fresh next run)
```

**No CERN credentials?** Run fully offline with mock producers instead:

```bash
make mocks           # same pipeline + auto-patterns, synthetic logs, no S3
```
