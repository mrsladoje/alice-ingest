# alice-ingest — Cardboard Airplane

```
 █████╗ ██╗     ██╗ ██████╗███████╗
██╔══██╗██║     ██║██╔════╝██╔════╝
███████║██║     ██║██║     █████╗
██╔══██║██║     ██║██║     ██╔══╝
██║  ██║███████╗██║╚██████╗███████╗
╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝╚══════╝
        A Large Ion Collider Experiment
```

A recreation of the **ALICE O2 Scalable Logging Architecture**, built at two scales:

- **Paper airplane** — the whole pipeline shrunk onto **one machine** with Docker
  Compose, for hands-on learning and failure experiments.
- **Cardboard airplane** — the same pipeline as an **actual distributed system**:
  **5 CERN OpenStack VMs**, native systemd services (no Docker), one **5-node
  two-tier OpenSearch cluster** (2 worker + 3 storage), provisioned and configured
  end-to-end with **Ansible**.

Both replay **real CERN S3 logs** (from the `epn-backup-logs` bucket — or **mock**
logs offline) through **Fluent Bit → OpenSearch → Dashboards**, with index
patterns auto-provisioned on startup. Still no Kafka, no Grafana (next flight).

> **The cardboard airplane** is the jump from *paper* to *cardboard*: from "Docker
> Compose on one machine" to real multi-VM provisioning and a genuine OpenSearch
> cluster. **v2** (this tree) splits that cluster into two tiers — 2 worker nodes
> holding disposable, node-local `info` logs, plus 3 replicated storage nodes for
> the valuable `other` + infologger logs. The single-machine path is unchanged and
> remains the local dev loop.

| | Paper airplane | Cardboard airplane |
|---|---|---|
| Runtime | Docker Compose, 1 machine | 5 CERN OpenStack VMs, native systemd |
| OpenSearch | single node | 5-node two-tier cluster (2 worker + 3 storage, quorum 2) |
| Orchestration | `docker compose` | Ansible (provision → configure → teardown) |
| Bring up | `make run` | `make provision && make deploy` |
| Full docs | [`docs/PAPER-AIRPLANE.md`](docs/PAPER-AIRPLANE.md) | [`deploy/README.md`](deploy/README.md) |

Design target — the real platform we simplify *from*:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Cardboard airplane — distributed deploy (5 VMs, two-tier, native, no Docker)

Provisions 5 CERN VMs and configures a 5-node two-tier OpenSearch cluster (2 worker
+ 3 storage) + worker Fluent Bit + S3-replay producers, with Dashboards (nginx TLS +
basic-auth) on one storage control node. Full runbook — topology, the two auth
paths, rolling-safety, verification — is [`deploy/README.md`](deploy/README.md); the
essentials:

**Prereqs.**
1. **OpenStack auth** — on lxplus, `kinit` plus the six `OS_*` exports
   (`v3fedkerb`); see [`deploy/README.md`](deploy/README.md) §3.1.
2. **Control-node toolchain** — `make bootstrap` builds a self-contained `.venv`
   with ansible-core, `openstacksdk`, `keystoneauth1[kerberos]` (needed for
   `v3fedkerb`), and the `python-openstackclient` CLI, plus the Galaxy
   collections. The deploy targets use it automatically; see
   [`deploy/README.md`](deploy/README.md) §3.2.
3. **Secrets (vault)** — the `[cern_s3]` keys and the Dashboards basic-auth
   password live in an encrypted vault, never plaintext:
   ```bash
   cd deploy
   cp group_vars/vault.yml.example group_vars/vault.yml
   $EDITOR group_vars/vault.yml            # fill in the 3 real values
   ansible-vault encrypt group_vars/vault.yml
   ```

> **Clean-slate prerequisite.** v2 assumes the OpenStack project is empty or already
> running v2. It does not know about v1 and won't clean it up. Because it reuses the
> VM names `alice-ingest-1..5`, a still-running **v1** stack (`alice-ingest-1..3`)
> would be silently adopted into a broken mixed cluster — **tear v1 down first**
> (`ansible-playbook teardown.yml` from a `cardboard-airplane-v1` checkout). See
> [`deploy/README.md`](deploy/README.md) §3.

**Fly** (from the repo root):

```bash
make provision     # create the 5 OpenStack VMs (idempotent) — needs OpenStack auth
make deploy        # configure the cluster — prompts for the vault password
```

**View.** Dashboards on the control VM: `https://<control-VM>:5601`, user `alice`
(the vault password), self-signed cert. From outside CERN, tunnel through lxplus:

```bash
ssh -L 5601:<control-VM-internal-ip>:5601 lxplus.cern.ch    # then https://localhost:5601
```

Data is historical (~June 2026, pinned by `RUN_TAG`) — if Discover looks empty,
widen the time range rather than assuming no data.

**Teardown.**

```bash
make teardown      # delete the 5 VMs, re-close 5601, drop the generated inventory
```

---

## Paper airplane — single machine (Docker Compose)

Prereqs: Docker + Docker Compose v2, and a Docker VM with real resources —
OpenSearch alone wants a **3 GB heap** (on macOS/Colima:
`colima start --cpu 8 --memory 32 --disk 100`).

**1. CERN S3 credentials (one-time).** The replay container reads real ALICE logs
using an AWS profile named `cern_s3`. Put your keys in `~/.aws/credentials` on the
host — Compose mounts this file **read-only** into the replay container only:

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
**autostarts** and streams real DDS / stdout / InfoLogger logs through Fluent Bit
into OpenSearch. First boot: OpenSearch takes ~30–90 s to go healthy (longer under
CPU contention — it's booting, not stuck).

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
