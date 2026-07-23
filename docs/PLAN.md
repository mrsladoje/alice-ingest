# PLAN.md — Anomaly Detection & Alerting: the implementation roadmap

**Goal: a working, self-provisioning detection layer on the cardboard airplane — deterministic alerts + RCF anomaly detection, deployed by `make deploy`, verified by the same strict gates as the rest of the stack.**

Scope discipline: this is the product plan. The paper, peer-cohort prototyping, external comparators (IDK-S, SemPCA, etc.) and everything in `ML_AI.md` Part IV are **out of scope** until this plan is done. `ML_AI.md` is kept as historical/research reference; where this plan deviates from its Part V recommendations, the deviation is listed in [§ Deviations](#deviations-from-ml_aimd-part-v).

---

## 0. Ground truth (what the plan builds on)

- **Cluster:** 5× m2.medium (2 vCPU / 3.75 GB). 2 workers (`node-01/02`: data+ingest, box-pinned `generic-log-info-<node_id>`), 3 storage (`alice-ingest-3/4/5`: manager+data+ingest, hold `infologger` + `generic-log-other` + `cockpit-metrics`). OpenSearch 3.7.0 RPM, security disabled, heap **512m everywhere**.
- **Plugins:** the RPM bundle ships `anomaly-detection`, `alerting`, `notifications`; the Dashboards RPM ships their UIs. Nothing verifies or uses them yet.
- **Provisioning pattern:** `deploy/roles/dashboards/tasks/bootstrap.yml` runs rendered `sh`/`curl` scripts on the control host (`templates.sh` → `patterns.sh` → ndjson import → hydrate), idempotent via PUT/ensure, verified by assertions. New AD/alerting provisioning slots into exactly this pattern.
- **Telemetry:** `metrics_poller.py` → `cockpit-metrics` every 30 s, kinds `cluster` / `index` / `node` / `fluentbit` / `osd` — real-time (poller stamps `@timestamp = now`). This is the only real-time signal on the VM deployment.
- **Logs:** three families — `infologger` (strict 16-field mapping, entity field **`hostname`**), `generic-log-other` and `generic-log-info-<node_id>` (generic mapping, entity field **`host`**). All get `ingest_time` via the `alice-add-ingest-time` default pipeline. **Replay preserves historical event timestamps (June 2026) while ingesting now** — so on the VMs, log data is never "current".

**Prerequisite before any stage starts:** finish the pending v4 real-VM redeploy from lxplus (browser gates, then tag `cardboard-airplane-v4`). Building AD on top of an unlanded migration doubles the debugging surface.

---

## Stage 1 — Foundations (small, do first, everything depends on it)

1. **Plugin guard.** Add a task in the opensearch role (or bootstrap preflight) asserting `opensearch-plugin list` contains `opensearch-anomaly-detection`, `opensearch-alerting`, `opensearch-notifications`, and that `GET _plugins/_anomaly_detection/detectors/_search` answers. Fail the deploy loudly, not at first detector-create.
2. **Materialize ingest lag.** Extend the `alice-add-ingest-time` pipeline (in `templates.sh.j2`) with a script processor computing `ingest_lag_ms = ingest_time − @timestamp`, and add the field to both component mappings. This gives: a directly aggregatable AD feature, a Discover/cockpit-queryable latency field, and the input for measuring p99 lag (needed for window delay in Stage 4). Implementation caveat: the script processor must parse the `@timestamp` string Fluent Bit's opensearch output actually emits (no explicit `time_key_format` is set in `collector.yaml`, so verify the on-disk format and parse defensively) — test the pipeline against real docs from all three families before trusting the field. *Schema change — flagged in Deviations.*
3. **Poller delta fix.** `output_retries_failed` is emitted cumulative-only; add `output_retries_failed_delta` in `metrics_poller.py` (and the `alice-cockpit-metrics` template mapping) so the Stage 2 rule is a plain `> 0` test like its siblings.
4. **Heap bump.** Raise `opensearch_heap_size` from 512m to **1g** (group_vars, one line). See [§ Non-optimal](#non-optimal-things-in-the-current-system) — 512m on a 3.75 GB VM is needlessly tight and directly caps AD model memory (10% of heap). 1g leaves ~2.5 GB for OS/page cache/Fluent Bit; safe on these flavors.

**Gate:** `make deploy` green; `ingest_lag_ms` present on fresh docs in all three families; plugin assertions pass; cluster stable at 1g heap (heap_percent trend in cockpit).

## Stage 2 — Layer 0: deterministic hard rules (highest signal per unit of work)

Alerting monitors over `cockpit-metrics`, provisioned as code: JSON definitions in `deploy/roles/dashboards/files/monitors/`, a new `alerting.sh.j2` rendered next to `templates.sh` and run from `bootstrap.yml`. Idempotency: monitors get random IDs on create, so the script upserts **by monitor name** (search → create-or-update). All fields below already exist in `cockpit-metrics` (after Stage 1.3).

| Monitor | Condition | Severity |
|---|---|---|
| collector down | `kind:fluentbit AND fb_up:0` (any doc, last 2 min) | page |
| collector unhealthy | `fb_healthy:0` sustained 2 min | warn |
| cluster red | `cluster_status_code:2` (any doc) | page |
| shards stuck | `unassigned_shards > 0` sustained 5 min | warn |
| data loss | `output_dropped_delta > 0` (any doc) | page |
| shipping breaking | `output_retries_failed_delta > 0` | warn |
| disk cliff | `disk_used_percent > 85` / `> 92` (per node) | warn / page |
| heap spiral | `heap_percent > 90` sustained 5 min (per node) | warn |
| telemetry silence | zero `cockpit-metrics` docs in last 5 min | page |

The last row is new versus the ML_AI table: the poller is a single service on the control host — if it dies, every other monitor goes blind silently. The alerting plugin runs cluster-side, so it can still detect the absence.

Per-node conditions (`fb_up`, disk, heap) use bucket-level monitors keyed on the `node` field so alerts name the culprit. Throttle/dedup on every monitor (e.g. 30 min per alert key) so a stuck condition pages once, not every interval.

**Alert delivery (v1):** alerts land in the Alerting plugin's own alert index and UI (already shipped in Dashboards). Add an **Alerts panel to the ALICE Cockpit dashboard** (extend `gen_cockpit.py` + regenerate `cockpit.ndjson`) and an active-alert count on the `/ops` page (`ops_server.py` already queries the cluster). No external channel exists in this stack; the nginx "alertmanager slot" from the README stays the future seam for webhook/email delivery. Flagged in Deviations.

**Gate:** `make deploy` twice → identical monitor set (idempotent); kill Fluent Bit on one worker → "collector down" alert names that node within ~2 min; stop `alice-metrics` → telemetry-silence alert fires; restart everything → alerts complete/acknowledge cleanly.

## Stage 3 — Layer 0.5: RCF detectors over cockpit-metrics (real-time today, no new data needed)

Three small detectors (≤5 features each, per plugin guidance), provisioned as code in `deploy/roles/dashboards/files/detectors/` via the same upsert-by-name script, then **started** by the script (create ≠ start):

| Detector | Category field | Features | Interval |
|---|---|---|---|
| `ingest-flow` | `node` (collector) | `output_records_delta`, `output_errors_delta`, `output_retries_delta` (zero-imputed) | 1 min |
| `node-health` | `node` (OS node) | `heap_percent`, `cpu_percent`, `indexing_delta`, `disk_used_percent` | 1 min |
| `dashboards-health` | — | `event_loop_delay`, `response_avg_ms`, `requests_total` | 1 min |

Each filters on its `kind`. Window delay: 1 min (data is local, 30 s cadence). Imputation: zeros for the delta/volume features only — a missing `heap_percent` is unknown, not 0 (per-feature policy, exactly as ML_AI Part I §3 warns). These catch the *combinations* the Stage 2 rules can't: throughput collapse + retry climb, heap growth + falling indexing rate.

Entity math: ~2 collectors + 5 nodes → trivial against the AD memory budget even pre-heap-bump; no HCAD concern at this stage.

**Gate:** detectors reach RUNNING and initialize (RCF needs a few hundred intervals ≈ 3–5 h of live poller data at 1 min); `_plugins/_anomaly_detection/detectors/<id>/_profile` shows models resident, no init failures; a synthetic disturbance (e.g. burst-load one worker) produces an anomaly result visible in the AD Dashboards UI.

## Stage 4 — Live-clock replay (the decision that unlocks log AD)

**Problem:** real-time detectors evaluate "now"; replayed logs carry June-2026 timestamps, so on the VMs no log detector will ever see data. `ML_AI.md` named three options (virtual clock, rewritten timestamps, live test) without choosing. **Choice: shifted-clock replay mode.**

- `deploy/roles/producer/files/replay_partition_wrapper.py`: optional `REPLAY_CLOCK=shifted` — compute one offset (`now − earliest event time in the selected run`) at load, add it to every emitted event timestamp (InfoLogger `timestamp` field, DDS line timestamps, stdout filename-derived times). Inter-event spacing, ordering, burstiness, and host partitioning are untouched — it is the real run, slid forward. Default stays `preserved` (historical fidelity for Discover/backtests). **`images/replay/replay.py` stays untouched** (paper airplane PRESERVED).
- `deploy/`: wire the env through the producer role's replay service; add `replay_clock` to `group_vars/all.yml`; let `make replay` pass `-e replay_clock=shifted`.

Bonus this buys beyond unblocking AD: with shifted event times, `ingest_lag_ms` measures the **genuine pipeline latency of the replay stream** (event enters producer → indexed), so the latency feature and the p99-lag measurement both become valid on replay — the doc believed latency needed a live controlled test.

Limitation to accept: a replayed run is finite (~run length), then the stream ends and volume drops to zero — which the detectors will correctly flag. Fine for soak/demo (re-trigger from `/ops`); a `loop` mode is a cheap later add if sustained streams are wanted.

**Gate:** shifted replay lands docs with `@timestamp ≈ now`; `ingest_lag_ms` percentiles are sane (ms–s, not weeks); preserved mode still works unchanged; cockpit unified pattern shows the live stream.

## Stage 5 — Layer 1: log detectors (the actual per-EPN detection)

Measure first: from a shifted replay, take **p99 `ingest_lag_ms` per family** (one Discover/viz query on the Stage 1 field) → that sets each detector's window delay (docs: window delay ≥ expected ingestion delay, or shingles degrade).

Detectors (upsert + start via the Stage 3 mechanism), one per failure mode per family — **note the entity field differs by family**:

| Detector | Index | Category | Features | Interval |
|---|---|---|---|---|
| `il-per-epn` | `infologger` | `hostname` | volume `count()` (zero-imputed), E/F count (`severity` in `E`,`F`), `avg(ingest_lag_ms)` | 1 min |
| `other-per-epn` | `generic-log-other` | `host` | volume (zero-imputed), error count (`severity` in `Error`,`Fatal`,`err`) | 1 min |
| `info-volume` | `generic-log-info-*` | `host` | volume (zero-imputed) | 1 min |
| slow tier | same three, cloned | same | same minus latency | 30 min |

The severity terms lists are per-family because the stack has three severity vocabularies (`I/W/E/D`, `Info/Warning/Error/Fatal/Sys`, `inf/err/cout`) — see Non-optimal §4; the feature filters enumerate, nothing gets normalized yet.

What this catches (the whole point): an EPN going **silent** (zero-imputed volume — absence, which no threshold sees), one EPN erroring while the farm total looks calm (per-category models), latency creep before data loss, never-seen combinations.

HCAD sizing check, honestly: ~31 EPN hosts × 7 detectors ≈ ~220 entity models worst case. Fits the current ~256 MB fleet AD budget, and comfortably fits the ~512 MB post-heap-bump budget — but run `_profile` per detector and record actual model sizes anyway (this number is the input to any future fleet-scale claim; don't guess it).

**Gates:**
- **Backtest:** historical analysis over a *preserved-clock* replay (volume + severity features only — latency is invalid there) → detectors initialize and score plausibly on real run data.
- **Live soak:** shifted replay running, detectors RUNNING, no evictions/skipped entities in profile output.
- **Fault injection (the real acceptance test):** during shifted replay — (a) kill one collector → per-EPN silence anomalies for its rack + Layer 0 page; (b) drop one EPN's file mid-replay → that `host` flagged, others quiet; (c) throttle a worker (CPU stress) → latency-feature anomaly before drops occur. Each detected = the product works; write the observed detection latencies down.

## Stage 6 — Surfacing & alert wiring

1. Index pattern for the AD result index + an **Anomalies panel** on the ALICE Cockpit dashboard (`gen_cockpit.py` regeneration; same strict import gates as v4).
2. Alerting monitors **on top of detector results** (the plugin supports anomaly-trigger monitors) for high-grade anomalies → same alert surface as Stage 2, same throttling.
3. `/ops` page: active alerts + anomalies-last-hour counters.
4. Runbook blurb in `deploy/README.md`: what each monitor/detector means, what to do when it fires, how to re-run backtests.

**Gate:** one screen (cockpit) shows: live status strip, active alerts, recent anomalies; a fault-injection rerun surfaces end-to-end within one detector interval + window delay.

## Stage 7 — Hygiene (product-grade, not glamorous)

1. **ISM retention** — the deploy has *no* retention anywhere. Minimum: rollover+delete for AD result indices and `cockpit-metrics` (both grow forever, silently); decide log-family retention consciously (the v2 tier design implies: info short, other longer, infologger longest).
2. Detector/monitor definitions get the same **strict verify** treatment as v4 provisioning: post-deploy assertion script (counts, RUNNING state, profile health) — deploy fails loudly if the detection layer is degraded.
3. Re-measure p99 lag after any topology change; window delay is config, not folklore.

---

## Deviations from ML_AI.md Part V

*(informing you explicitly, as requested)*

1. **Shifted-clock replay chosen** (Stage 4), implemented in the Ansible partition wrapper only — `images/replay/replay.py` stays PRESERVED for the paper airplane. The doc left "virtual clock / rewritten timestamps / live test" open. Without it, on the VMs every real-time log detector is permanently blind — this was implicit in the doc but never confronted. Side effect: latency features become valid on replay, which the doc believed impossible ("replay can't backtest latency" now only applies to preserved-clock mode).
2. **`ingest_lag_ms` materialized at ingest** (Stage 1) instead of computing `ingest_time − @timestamp` inside each feature as a script. Cheaper per query, one definition instead of N, and queryable in Discover/cockpit. This adds a field to both component mappings — a (tiny) schema change to mappings that were previously "stay as-is".
3. **Category field corrected per family:** the doc says "category field `host`" — that's right for `generic-log-*` but **wrong for `infologger`, whose mapping has `hostname`**, and the strict mapping means a `host` category there would simply fail. Plan uses `hostname`/`host` per family.
4. **Deferred from the doc's "production now" list:** Forecasting (item 4), Suggest API (item 5), cross-detector Correlation (item 6), and the deterministic trend-rule lane (item 3's change-point lane). Reason: none of them is needed for a *working* detection product on a 31-EPN replay; each adds tuning surface before the core has soaked. They are the natural v-next once Stages 1–7 are green — the trend lane first (it covers the "slow drift gets absorbed" blind spot RCF genuinely has), then correlation once there are enough detectors to group.
5. **Two horizons, not three** (1 min + 30 min, dropping the 5 min tier) — with 30-second telemetry and 1-minute log buckets on a 2-vCPU cluster, the middle tier buys little and costs detector count. Add it later if the soak shows gaps.
6. **Run/phase conditioning skipped for now** — only `infologger` even has a `run` field; generic families have no run metadata. Doing this properly is a schema/enrichment task, not a detector flag; parked.
7. **Alert delivery = in-cluster only for v1** (Alerting UI + cockpit + /ops). The doc says "Alerting → humans/incidents"; there is no external channel (Slack/email/webhook receiver) anywhere in this stack, and inventing one now is scope creep. The nginx alertmanager seam is where it goes when wanted.

## Non-optimal things in the current system

*(flagging, as requested — first two are fixed by this plan, rest are decisions)*

1. **512 MB heap on 3.75 GB VMs** wastes the hardware and caps AD memory at ~51 MB/node. Fixed in Stage 1 (→1g). If you'd rather not touch a green cluster, the plan still works at 512m for current entity counts — say so and I'll drop Stage 1.4.
2. **No retention/ISM anywhere** — every index grows unbounded; adding AD result indices and more `cockpit-metrics` makes it worse. Stage 7.1.
3. **Security disabled** means the AD/alerting REST APIs (create/delete detectors, silence monitors) are unauthenticated on :9200 inside the SG. Acceptable for the cardboard airplane, unacceptable for anything called production — when the prod flight happens, the security plugin comes back on and every bootstrap curl grows auth.
4. **Three severity vocabularies** (`I/W/E/F/D` vs `Info/Warning/Error/Fatal/Sys` vs `inf/err/cout`, plus severity-less free-form stdout lines routed to `generic-log-other` via the `$message` catch-all rule) force every cross-family query and every detector filter to enumerate variants. The clean fix is a normalized `severity_norm` stamped at the collector (one lua filter) — deliberately *not* in this plan because it touches mappings, saved searches, and the cockpit at once; worth doing as its own small change if the enumeration keeps annoying.
5. **`host` vs `hostname` split** between families blocks any single unified per-EPN detector or cross-family cohort view. Same normalization bucket as #4.
6. **Single poller = single blind spot** — mitigated by the telemetry-silence monitor (Stage 2), properly fixed only by running the poller redundantly; not worth it at this scale.
7. **README documents an `alertmanager_port` var that doesn't exist** in `group_vars/all.yml` — docs ahead of code; trivial cleanup whenever that file is next touched.

## Explicitly out of scope (until this plan ships)

Peer-cohort detector & everything in ML_AI Part IV · LLM/TSFM anything · external Python/Kafka AD pipeline · anomaly-triggered autoscaling (rejected permanently, Part II) · semantic-embedding novelty · Forecasting/Suggest/Correlation/trend-lane (deferred, see Deviations 4).
