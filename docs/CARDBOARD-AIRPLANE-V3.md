# Cardboard Airplane v3 — Trigger, More Data, and the Cockpit

The hand-to-Lubos increment on top of the v2 two-tier design. Same 5-VM two-tier cluster, same OpenSearch Dashboards 2.17 stack — v3 adds three things a human operator actually touches: **a replay button, more real data, and a single cockpit dashboard**. Design was grilled to a shared understanding on 2026-07-17; this doc records what shipped.

> v2 (tag `cardboard-airplane-v2`) is the baseline. Nothing about the topology, tiers, or shard placement changes here.

---

## 0. What v3 adds over v2

1. **Ingestion is now a deliberate step, not a side effect of deploy.** The deploy *arms* the pipeline (cluster + collectors + replay service all up) but ingests nothing. A human triggers the load with `make replay`.
2. **More of the real run.** stdout now covers all EPN hosts (not a 3-host sample) and InfoLogger pulls 15 dumps instead of 3, with a size guard that skips the one pathological multi-hundred-MB object.
3. **The ALICE Cockpit.** One provisioned home dashboard — health tiles, severity trend, top talkers, recent errors — over a unified index pattern that spans all three log families, plus seven seed saved searches and cross-source "surrounding documents".

---

## 1. Triggered replay — the "replay button"

**Disarmed by default.** The producer role now templates `AUTOSTART_REPLAY` from `producer_autostart_replay` (default **false**). After `make deploy` the `alice-replay` service on each worker is running and serving its HTTP trigger, but it has loaded nothing. Flip `producer_autostart_replay: true` to restore the v2 auto-load-once-on-first-boot behaviour.

**`make replay`** runs `deploy/playbooks/replay.yml`, which POSTs `/replay` to each worker's local `alice-replay` trigger (`127.0.0.1:8088`). Each worker streams *its own* EPN partition (the v2 single-partition wrapper is unchanged). No vault password is needed — the playbook touches no secrets.

**`make replay-fresh`** = wipe then replay. Because the replay engine has **no dedup**, a second plain `make replay` double-counts every document. `replay-fresh` first deletes the log indices on the control node — `infologger`, `application-logs-central`, and each worker's `application-logs-local-<node_id>` — then **re-creates the per-worker info indices pinned to their box** (`require.box`, exactly as the v2 bootstrap does; the wildcard index template alone can't set a per-index box), and only then triggers the load. Result: a clean reload with the worker-locality guarantee intact.

Typical flow:

```bash
make provision && make deploy      # cluster armed, no data
make replay                        # first clean load  (Lubos presses the button)
make replay-fresh                  # wipe + reload cleanly (re-runs, tuning, etc.)
```

`make replay` returns immediately (HTTP `202` per worker); the stream then flows for a few minutes. `409` from a worker means a replay is already in flight there.

### 1a. The web ops page (browser replay button)

For a non-CLI trigger, a small `alice-ops` service on the control node sits behind the *same* nginx (TLS + `alice` basic-auth) as Dashboards, at **`https://<control>:5601/ops`**. It renders a live document count and two buttons:

- **Reload data (fresh)** → `POST /ops/replay-fresh`: wipes the log indices, re-creates the per-worker `require.box` info indices, then triggers replay on every worker — the always-clean load, safe to click repeatedly.
- **Append replay** → `POST /ops/replay`: another full pass with no dedup (deliberate use only).

The service (`ops_server.py`, stdlib only, `127.0.0.1:8090`) fans out to each worker's `:8088` trigger and talks to OpenSearch on `localhost:9200`; the recreate uses `wait_for_active_shards=0` so it never blocks on allocation. Every button posts in place from the page and the POST returns at once: the action runs as one background job, and the page streams its progress lines from `GET /ops/status`. That keeps the address bar on `/ops/` and keeps a multi-minute fresh reload from hitting the nginx 120s proxy timeout with a 504. Only one job runs at a time; a second request while one is in flight is refused rather than queued. Workers open `:8088` to the control node's IP (host firewall) so the fan-out can reach them. This is CERN-internal only — reaching it from the public internet would be a separate, deliberate LanDB firewall opening.

---

## 2. More data — knobs and the size guard

All in `deploy/group_vars/all.yml`:

| knob | v2 | v3 | why |
|---|---|---|---|
| `stdout_max_objects` | 3 (engine default) | **0** (all hosts) | stdout is nearly free — DDS already downloads the same tarballs per host; we just stop capping which hosts contribute. |
| `il_max_objects` | 3 | **15** | more InfoLogger depth; this is the main storage-tier load knob. |
| `replay_max_object_bytes` | — | **157286400** (150 MB) | skip pathological S3 objects (one InfoLogger dump is ~584 MB). The cap counts **objects, not bytes** — normal ~22 MB dumps and ~104 MB DDS tarballs pass; only the monster is dropped. |

The size guard is applied in the producer role's `replay_partition_wrapper.py` (the same monkeypatch seam v2 uses to narrow `list_objects` to one partition) — `images/replay/replay.py` stays PRESERVED and unedited. stdout member/line caps (`STDOUT_MAX_MEMBERS`, `STDOUT_MAX_LINES_PER_MEMBER`) keep their engine defaults, so "all hosts" widens breadth without unbounding per-file volume.

**Capacity.** m2.medium = 20 GB disk / 3.75 GB RAM / 2 vCPU; stay under ~13 GB index data per VM (OSD watermarks). Durable indices keep **2 replicas** (3 copies over 3 storage VMs) → ~13 GB unique budget. `il_max_objects: 15` is a starting point, not a tuned value: run `make replay` once, then

```bash
ansible storage -i inventory.yml -m shell -a "curl -s localhost:9200/_cat/allocation?v"
```

and push `il_max_objects` higher (via `make replay-fresh`) if the storage tier sits under ~60%. ISM/retention stays deferred — this is a bounded one-shot load, not a live stream.

---

## 3. The ALICE Cockpit

Provisioned by the bootstrap: `deploy/roles/dashboards/files/gen_cockpit.py` generates a static `cockpit.ndjson` (regenerate with `python3 gen_cockpit.py`), which `roles/dashboards/tasks/main.yml` stages and imports via `_import?overwrite=true`, then sets the unified pattern as the default. The import runs on **every** deploy (idempotent overwrite), NOT behind the one-shot marker — so editing the queries and re-running `make deploy` updates the saved objects in place. The per-source index patterns are still created by the marker-guarded `patterns.sh`.

**Unified index pattern** `infologger,application-logs-*` (time field `@timestamp`) is set as the **default** — the "one interface to rule them all" in Discover. The per-source patterns (`infologger`, `application-logs-local-*`, `application-logs-central`) are kept for focused views; a `log_source` filter chip narrows the unified view to one family.

**One home dashboard, "ALICE Cockpit":**
- health tiles — total records, Errors & Warnings count, records-by-source table, and a link tile to the built-in **Index Management** UI (Lubos's "see how OpenSearch is working" — the indices table: health / shards / replicas / docs / size, no custom build);
- **severity over time** — stacked date histogram;
- **top hosts** (dds/stdout `host`) and **top systems** (infologger `system`) bars;
- **recent Errors & Warnings** — the seed saved search embedded as a panel.

Light branding: `opensearchDashboards.branding.applicationTitle: "ALICE Cockpit"`.

### 3a. Surrounding documents (free with the unified pattern)

OSD Discover's **"View surrounding documents"** shows N docs before/after an anchor, time-ordered — but only *within one index pattern*. Pointed at the unified pattern it gives **cross-source** time context: find a TCP error in InfoLogger, then see the DDS and stdout lines from that same instant. No build beyond the unified pattern; the stdout replay's microsecond bump already keeps ordering clean when timestamps collide.

### 3b. Seven seed saved searches

All in **DQL** (`language: kuery`), all shipped in `cockpit.ndjson`. DQL is the OSD
default query language, and using it (rather than Lucene) is what makes an *opened*
saved search re-apply its query reliably — Lucene-language saved queries don't
restore into the Discover bar in OSD 2.17 (they open as match-all):

| search | query |
|---|---|
| Errors & Warnings — all sources | `severity:(E or F or W or Error or Fatal or Warning or err)` |
| TCP / connection issues | `message:(tcp or connection or refused or timeout)` |
| By detector (TPC/ITS/MCH) | `detector:(TPC or ITS or MCH)` |
| One host — edit host:epnNNN | `host:epn* or hostname:epn*` |
| By subsystem (ODC/DPL) | `system:(ODC or DPL)` |
| DDS problems | `log_source:dds and not severity:inf` |
| stdout crashes | `log_source:stdout and severity:(Error or Fatal)` |

**The severity wrinkle.** Severity is encoded differently per source — InfoLogger single-char `I/W/E/F`, stdout words `Info/Warning/Error/Fatal/Sys`, DDS lowercase `inf/err/cout` — so any cross-source severity query ORs every encoding (the mapping is left untouched). Likewise host lives on `host` (dds/stdout) but `hostname` (infologger), so the one-host search ORs both; narrow it to a single EPN and pair it with surrounding-documents.

---

## 4. "System logs" — resolved, nothing built

Lubos mentioned "journalctl / logs from the systems themselves". S3 proof: the only logs in any reachable bucket are DDS run-tarballs + InfoLogger dumps; inside a tarball the per-process `_out.log`/`_err.log` are exactly **our stdout family** (the O2 reco process output) — already ingested, with its Warning/Error already landing on durable storage. There is no journald/syslog anywhere in S3, so literal `journalctl` is out of scope, and no re-tiering is needed: the v2 severity split already does the right thing.

---

## 5. Deferred

- **OSD 2.17 → 3.x bump** — a clean, separate follow-up; unlocks matured Workspaces/Explore, PPL Live Tail, Query Insights.
- **ISM / retention** — still a write-path change (rollover-aliased indices), not a settings tweak.
- **Worker-tier rebalance** — the worker tier goes near-idle now that stdout stays put by severity, but it's fine as designed.
