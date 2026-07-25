# PLAN.md — Anomaly Detection & Alerting: the implementation roadmap

**Goal: a working, self-provisioning detection layer on the cardboard airplane — deterministic alerts + RCF anomaly detection, deployed by** `make deploy`**, verified by the same strict gates as the rest of the stack.**

Scope discipline: this is the product plan. The paper, peer-cohort prototyping, external comparators (IDK-S, SemPCA, etc.) and everything in `ML_AI.md` Part IV are **out of scope** until this plan is done. `ML_AI.md` is kept as historical/research reference; where this plan deviates from its Part V recommendations, the deviation is listed in [§ Deviations](#deviations-from-ml_aimd-part-v).

---



## 0. Ground truth (what the plan builds on)

- **Cluster:** 5× m2.medium (2 vCPU / 3.75 GB). 2 workers (`node-01/02`: data+ingest, box-pinned `generic-log-info-<node_id>`), 3 storage (`alice-ingest-3/4/5`: manager+data+ingest, hold `infologger` + `generic-log-other` + `cockpit-metrics`). OpenSearch 3.7.0 RPM, security disabled, heap **1g** (Stage 1).
- **Plugins:** the RPM bundle ships `anomaly-detection`, `alerting`, `notifications`; the Dashboards RPM ships their UIs. Bootstrap provisions detectors/monitors and asserts them via `verify_detection.py`.
- **Provisioning pattern:** `deploy/roles/dashboards/tasks/bootstrap.yml` runs rendered `sh`/`curl` scripts on the control host (`templates.sh` → `patterns.sh` → ndjson import → hydrate), idempotent via PUT/ensure, verified by assertions. New AD/alerting provisioning slots into exactly this pattern.
- **Telemetry:** `metrics_poller.py` → `cockpit-metrics` every 30 s, kinds `cluster` / `index` / `node` / `fluentbit` / `osd` — real-time (poller stamps `@timestamp = now`). This is the only real-time signal on the VM deployment.
- **Logs:** three families — `infologger` (strict mapping, entity field `hostname`), `generic-log-other` and `generic-log-info-<node_id>` (generic mapping, entity field `host`). Dual clock on every log doc: `@timestamp` = event time (June under preserved replay), `collector_time` = Fluent Bit accept wall clock, `ingest_time` via `alice-add-ingest-time`. Shipping lag `ingest_lag_ms = ingest_time − collector_time` is valid on preserved replay; `enter_system_lag_ms` is prod-oriented.

**Prerequisite before any stage starts:** finish the pending v4 real-VM redeploy from lxplus (browser gates, then tag `cardboard-airplane-v4`). Building AD on top of an unlanded migration doubles the debugging surface.

---



## Stage 1 — Foundations (small, do first, everything depends on it)

1. **Plugin guard.** Add a task in the opensearch role (or bootstrap preflight) asserting `opensearch-plugin list` contains `opensearch-anomaly-detection`, `opensearch-alerting`, `opensearch-notifications`, and that `GET _plugins/_anomaly_detection/detectors/_search` answers. Fail the deploy loudly, not at first detector-create.
2. **Materialize dual-clock lag.** Fluent Bit stamps `collector_time` (wall clock at accept). The `alice-add-ingest-time` pipeline keeps `ingest_time`, then computes `ingest_lag_ms = ingest_time − collector_time` (shipping lag — valid on preserved replay) and `enter_system_lag_ms = collector_time − @timestamp` (event→collector — huge/meaningless under preserved June replay, useful in production). Both lag fields land in the component mappings. Implementation caveat: the script processor must parse whatever string/number formats Fluent Bit and `_ingest.timestamp` emit — parse defensively; test against real docs from all three families. *Schema change — flagged in Deviations.*
3. **Poller delta fix.** `output_retries_failed` is emitted cumulative-only; add `output_retries_failed_delta` in `metrics_poller.py` (and the `alice-cockpit-metrics` template mapping) so the Stage 2 rule is a plain `> 0` test like its siblings.
4. **Heap bump.** Raise `opensearch_heap_size` from 512m to **1g** (group_vars, one line). See [§ Non-optimal](#non-optimal-things-in-the-current-system) — 512m on a 3.75 GB VM is needlessly tight and directly caps AD model memory (10% of heap). 1g leaves ~2.5 GB for OS/page cache/Fluent Bit; safe on these flavors.

**Gate:** `make deploy` green; `collector_time` + small `ingest_lag_ms` on fresh docs in all three families (preserved replay); plugin assertions pass; cluster stable at 1g heap (heap_percent trend in cockpit).

## Stage 2 — Layer 0: deterministic hard rules (highest signal per unit of work)

Alerting monitors over `cockpit-metrics`, provisioned as code: JSON definitions in `deploy/roles/dashboards/files/monitors/`, a new `alerting.sh.j2` rendered next to `templates.sh` and run from `bootstrap.yml`. Idempotency: monitors get random IDs on create, so the script upserts **by monitor name** (search → create-or-update). All fields below already exist in `cockpit-metrics` (after Stage 1.3).


| Monitor             | Condition                                          | Severity    |
| ------------------- | -------------------------------------------------- | ----------- |
| collector down      | `kind:fluentbit AND fb_up:0` (any doc, last 2 min) | page        |
| collector unhealthy | `fb_healthy:0` sustained 2 min                     | warn        |
| cluster red         | `cluster_status_code:2` (any doc)                  | page        |
| shards stuck        | `unassigned_shards > 0` sustained 5 min            | warn        |
| data loss           | `output_dropped_delta > 0` (any doc)               | page        |
| shipping breaking   | `output_retries_failed_delta > 0`                  | warn        |
| disk cliff          | `disk_used_percent > 85` / `> 92` (per node)       | warn / page |
| heap spiral         | `heap_percent > 90` sustained 5 min (per node)     | warn        |
| telemetry silence   | zero `cockpit-metrics` docs in last 5 min          | page        |


The last row is new versus the ML_AI table: the poller is a single service on the control host — if it dies, every other monitor goes blind silently. The alerting plugin runs cluster-side, so it can still detect the absence.

Per-node conditions (`fb_up`, disk, heap) use bucket-level monitors keyed on the `node` field so alerts name the culprit.

**Throttle (landed):** every monitor under `files/monitors/` has a trigger action with `throttle_enabled: true`, `throttle: {value: 30, unit: MINUTES}`, targeting Notifications channel `alice-incluster-alert-sink` (webhook → `alice-alert-actions` index on loopback `:9200`). Bucket-level actions use `action_execution_policy.per_alert` so throttle is **per alert key** (per `node` / `host` / `hostname`), not one global mute for the whole monitor. `alerting.sh.j2` upserts the channel + index idempotently before monitors; `verify_detection.py` asserts the channel and 30m throttle on every expected monitor.

**Alert delivery (v1):** alerts land in the Alerting plugin's own alert index and UI (already shipped in Dashboards); action payloads also index into `alice-alert-actions` so throttle has a real in-cluster destination. Add an **Alerts panel to the ALICE Cockpit dashboard** (extend `gen_cockpit.py` + regenerate `cockpit.ndjson`) and an active-alert count on the `/ops` page (`ops_server.py` already queries the cluster). No external Slack/email; the nginx "alertmanager slot" from the README stays the future seam. Flagged in Deviations.

**Gate:** `make deploy` twice → identical monitor set (idempotent); kill Fluent Bit on one worker → "collector down" alert names that node within ~2 min; stop `alice-metrics` → telemetry-silence alert fires; restart everything → alerts complete/acknowledge cleanly.

## Stage 3 — Layer 0.5: RCF detectors over cockpit-metrics (real-time today, no new data needed)

Three small detectors (≤5 features each, per plugin guidance), provisioned as code in `deploy/roles/dashboards/files/detectors/` via the same upsert-by-name script, then **started** by the script (create ≠ start):


| Detector            | Category field     | Features                                                                             | Interval |
| ------------------- | ------------------ | ------------------------------------------------------------------------------------ | -------- |
| `ingest-flow`       | `node` (collector) | `output_records_delta`, `output_errors_delta`, `output_retries_delta` (zero-imputed) | 1 min    |
| `node-health`       | `node` (OS node)   | `heap_percent`, `cpu_percent`, `indexing_delta`, `disk_used_percent`                 | 1 min    |
| `dashboards-health` | —                  | `event_loop_delay`, `response_avg_ms`, `requests_total`                              | 1 min    |


Each filters on its `kind`. Window delay: 1 min (data is local, 30 s cadence). Imputation: zeros for the delta/volume features only — a missing `heap_percent` is unknown, not 0 (per-feature policy, exactly as ML_AI Part I §3 warns). These catch the *combinations* the Stage 2 rules can't: throughput collapse + retry climb, heap growth + falling indexing rate.

Entity math: ~2 collectors + 5 nodes → trivial against the AD memory budget even pre-heap-bump; no HCAD concern at this stage.

**Gate:** detectors reach RUNNING and initialize (RCF needs a few hundred intervals ≈ 3–5 h of live poller data at 1 min); `_plugins/_anomaly_detection/detectors/<id>/_profile` shows models resident, no init failures; a synthetic disturbance (e.g. burst-load one worker) produces an anomaly result visible in the AD Dashboards UI.

## Stage 4 — Dual clock (unlocks log AD; shifted-clock deferred)

**Problem:** real-time detectors evaluate "now"; replayed logs carry June-2026 `@timestamp`s, so on the VMs no log detector keyed on event time will ever see data. `ML_AI.md` named three options (virtual clock, rewritten timestamps, live test) without choosing.

**Choice (landed): dual clock.** Keep historical `@timestamp` for Discover/cockpit. Stamp a separate `collector_time` (wall clock when Fluent Bit accepted the record) and point **log** anomaly detectors at `time_field: collector_time`. Shipping lag becomes `ingest_lag_ms = ingest_time − collector_time` (valid on preserved replay). `enter_system_lag_ms = collector_time − @timestamp` is implemented for production but is archive age under preserved June data.

**Deferred / optional:** shifted-clock replay (`REPLAY_CLOCK=shifted` in the Ansible partition wrapper — `images/replay/replay.py` stays PRESERVED). Still useful for Discover “live stream” cosmetics; **not** required for AD or for a sane `ingest_lag_ms`. Makefile `make replay` may still pass shifted for demos; `group_vars` default remains `preserved`.

**Gate:** preserved replay docs carry `collector_time ≈ now`, June `@timestamp`, small `ingest_lag_ms`, huge `enter_system_lag_ms`; log detectors provisioned on `collector_time`; Discover still browses by `@timestamp`.

## Stage 5 — Layer 1: log detectors (the actual per-EPN detection)

Measure first: from a preserved (or shifted) replay, take **p99** `ingest_lag_ms` **per family** (shipping lag via dual clock) → that sets each detector's window delay (docs: window delay ≥ expected ingestion delay, or shingles degrade).

**Entity split (load-bearing):** `ingest_lag_ms` is collector→OS shipping → category `node`. `enter_system_lag_ms` is EPN→collector entry → category `hostname` (Infologger) / `host` (generic). Never put shipping lag on an EPN category or entry lag on `node`. Lag-only detectors have **no** ZERO imputation; volume/error detectors do.

Detectors (upsert + start via the Stage 3 mechanism), one per failure mode per family — **note the entity field differs by family**. All log detectors use `time_field: collector_time` (metrics detectors stay on `@timestamp`):


| Detector | Index | Category | Features | Zero-impute | Interval |
| --- | --- | --- | --- | --- | --- |
| `il-per-epn` | `infologger` | `hostname` | volume `count()`, E/F (`severity` in `E`,`F`) | YES | 1 min |
| `il-per-epn-slow` | `infologger` | `hostname` | same | YES | 30 min |
| `il-per-epn-entry-lag` | `infologger` | `hostname` | `avg(enter_system_lag_ms)` only | NO | 1 min |
| `il-per-epn-entry-lag-slow` | `infologger` | `hostname` | same | NO | 30 min |
| `il-collector-shipping-lag` | `infologger` | `node` | `avg(ingest_lag_ms)` only | NO | 1 min |
| `il-collector-shipping-lag-slow` | `infologger` | `node` | same | NO | 30 min |
| `other-per-epn` | `generic-log-other` | `host` | volume, error count (`Error`/`Fatal`/`err`) | YES | 1 min |
| `other-per-epn-slow` | `generic-log-other` | `host` | same | YES | 30 min |
| `info-volume` | `generic-log-info-*` | `host` | volume only | YES | 1 min |
| `info-volume-slow` | `generic-log-info-*` | `host` | same | YES | 30 min |
| `info-per-epn-entry-lag` | `generic-log-info-*` | `host` | `avg(enter_system_lag_ms)` only | NO | 1 min |
| `info-per-epn-entry-lag-slow` | `generic-log-info-*` | `host` | same | NO | 30 min |
| `info-collector-shipping-lag` | `generic-log-info-*` | `node` | `avg(ingest_lag_ms)` only | NO | 1 min |
| `info-collector-shipping-lag-slow` | `generic-log-info-*` | `node` | same | NO | 30 min |


Plus the 3 Stage 3 metrics detectors → **17** total. Info shipping-lag is required in addition to Infologger shipping-lag: info indices are worker-local (`require.box`); Infologger ships to storage.

Under preserved June replay, `enter_system_lag_ms` is archive age (huge) — entry-lag AD + trend scores are meaningless until live EPNs; detectors still ship and start.

The severity terms lists are per-family because the stack has three severity vocabularies (`I/W/E/D`, `Info/Warning/Error/Fatal/Sys`, `inf/err/cout`) — see Non-optimal §4; the feature filters enumerate, nothing gets normalized yet.

What this catches (the whole point): an EPN going **silent** (zero-imputed volume — absence, which no threshold sees), one EPN erroring while the farm total looks calm (per-category models), entry-lag creep per EPN, shipping-lag creep per collector, never-seen combinations.

### Dumb trend lane (deterministic; covers RCF slow-drift blind spot)

Alerting monitors (same `files/monitors/` + `alerting.sh.j2` path as Stage 2). Schedule **10 min**; severity warn; log queries use `collector_time`.

**Dwell (landed):** a single noisy 10m window must not fire. Each evaluation requires the ratio condition on **three consecutive 10m slices** (t0=`now-10m→now`, t1=`now-20m→now-10m`, t2=`now-30m→now-20m`) — ≈30m sustained breach. Rising signals (lag / E-F / errors) fire only if `slice/baseline ≥ 2` on **all three** slices. Volume fires only if **all three** are `≥ 2` **or** **all three** are `≤ 0.5` (no mixed high/low). **Baseline** = avg (or rate) over ~7d **excluding the last 30m** (falls back to 24h excluding last 30m when 7d empty). Same 30m per-alert-key throttle as Stage 2. Run-start volume spikes may false-warn until gated (not blocking).

| Monitor | Index | Entity | Metric |
| --- | --- | --- | --- |
| `trend-il-volume` | `infologger` | `hostname` | doc volume |
| `trend-il-ef` | `infologger` | `hostname` | E/F count |
| `trend-il-entry-lag` | `infologger` | `hostname` | `avg(enter_system_lag_ms)` |
| `trend-il-shipping-lag` | `infologger` | `node` | `avg(ingest_lag_ms)` |
| `trend-other-volume` | `generic-log-other` | `host` | volume |
| `trend-other-errors` | `generic-log-other` | `host` | error count |
| `trend-info-volume` | `generic-log-info-*` | `host` | volume |
| `trend-info-entry-lag` | `generic-log-info-*` | `host` | `avg(enter_system_lag_ms)` |
| `trend-info-shipping-lag` | `generic-log-info-*` | `node` | `avg(ingest_lag_ms)` |

HCAD sizing check, honestly: ~31 EPN hosts × volume/error detectors + collector-scoped shipping-lag models ≈ still within the post-heap-bump AD budget — but run `_profile` per detector and record actual model sizes anyway (this number is the input to any future fleet-scale claim; don't guess it).

**Gates:**

- **Backtest / live soak on preserved clock:** volume + severity + shipping-lag features all valid (no need to rewrite event times) → detectors initialize and score on real run data arriving "now" via `collector_time`. Entry-lag detectors RUNNING even when scores reflect archive age.
- **Trend dwell:** a one-slice spike alone must not satisfy the condition script; sustained three-slice breach required before the trend alert fires.
- **Fault injection (the real acceptance test):** during replay — (a) kill one collector → per-EPN silence anomalies for its rack + Layer 0 page; (b) drop one EPN's file mid-replay → that `host` flagged, others quiet; (c) throttle a worker (CPU stress) → shipping-lag anomaly before drops occur. Each detected = the product works; write the observed detection latencies down.



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

1. **Dual clock chosen over shifted-clock for unlocking log AD** (Stage 4). Log detectors use `time_field: collector_time`; Discover/cockpit keep `@timestamp`. Shifted-clock replay remains available as optional Discover cosmetics but is **not** required for AD or for valid `ingest_lag_ms`. `images/replay/replay.py` stays PRESERVED. `enter_system_lag_ms` is prod-oriented (invalid as a latency signal under preserved June replay).
2. `ingest_lag_ms` **/** `enter_system_lag_ms` **materialized at ingest** (Stage 1) instead of computing lags inside each AD feature as a script. `ingest_lag_ms = ingest_time − collector_time` (shipping); `enter_system_lag_ms = collector_time − @timestamp` (entry). Cheaper per query, one definition instead of N, and queryable in Discover/cockpit. Adds fields to both component mappings — a (tiny) schema change to mappings that were previously "stay as-is".
3. **Category field corrected per family:** the doc says "category field `host`" — that's right for `generic-log-`* but **wrong for** `infologger`**, whose mapping has** `hostname`, and the strict mapping means a `host` category there would simply fail. Plan uses `hostname`/`host` per family.
4. **Deferred from the doc's "production now" list:** Forecasting (item 4), Suggest API (item 5), and cross-detector Correlation (item 6). The **deterministic trend-rule lane** (item 3) is **in scope** — provisioned as Stage 5 Alerting monitors (`trend-*`) with 3×10m dwell vs 7d/24h baseline (excluding last 30m). Reason for still deferring the rest: not needed for a *working* detection product on a 31-EPN replay; each adds tuning surface before the core has soaked.
5. **Two horizons, not three** (1 min + 30 min, dropping the 5 min tier) — with 30-second telemetry and 1-minute log buckets on a 2-vCPU cluster, the middle tier buys little and costs detector count. Add it later if the soak shows gaps.
6. **Run/phase conditioning skipped for now** — only `infologger` even has a `run` field; generic families have no run metadata. Doing this properly is a schema/enrichment task, not a detector flag; parked. Trend monitors may false-warn on run-start volume spikes until gated.
7. **Alert delivery = in-cluster only for v1** (Alerting UI + cockpit + /ops + `alice-alert-actions` sink for throttled actions). The doc says "Alerting → humans/incidents"; there is no external Slack/email anywhere in this stack. The nginx alertmanager seam is where external delivery goes when wanted.



## Non-optimal things in the current system

*(flagging, as requested — first two are fixed by this plan, rest are decisions)*

1. **512 MB heap on 3.75 GB VMs** wastes the hardware and caps AD memory at ~51 MB/node. Fixed in Stage 1 (→1g). If you'd rather not touch a green cluster, the plan still works at 512m for current entity counts — say so and I'll drop Stage 1.4.
2. **No retention/ISM anywhere** — every index grows unbounded; adding AD result indices and more `cockpit-metrics` makes it worse. Stage 7.1.
3. **Security disabled** means the AD/alerting REST APIs (create/delete detectors, silence monitors) are unauthenticated on :9200 inside the SG. Acceptable for the cardboard airplane, unacceptable for anything called production — when the prod flight happens, the security plugin comes back on and every bootstrap curl grows auth.
4. **Three severity vocabularies** (`I/W/E/F/D` vs `Info/Warning/Error/Fatal/Sys` vs `inf/err/cout`, plus severity-less free-form stdout lines routed to `generic-log-other` via the `$message` catch-all rule) force every cross-family query and every detector filter to enumerate variants. The clean fix is a normalized `severity_norm` stamped at the collector (one lua filter) — deliberately *not* in this plan because it touches mappings, saved searches, and the cockpit at once; worth doing as its own small change if the enumeration keeps annoying.
5. `host` **vs** `hostname` **split** between families blocks any single unified per-EPN detector or cross-family cohort view. Same normalization bucket as #4.
6. **Single poller = single blind spot** — mitigated by the telemetry-silence monitor (Stage 2), properly fixed only by running the poller redundantly; not worth it at this scale.
7. **README documents an** `alertmanager_port` **var that doesn't exist** in `group_vars/all.yml` — docs ahead of code; trivial cleanup whenever that file is next touched.



## Explicitly out of scope (until this plan ships)

Peer-cohort detector & everything in ML_AI Part IV · LLM/TSFM anything · external Python/Kafka AD pipeline · anomaly-triggered autoscaling (rejected permanently, Part II) · semantic-embedding novelty · Forecasting/Suggest/Correlation (deferred, see Deviations 4).