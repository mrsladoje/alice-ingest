# PLAN.md — Anomaly Detection & Alerting: the implementation roadmap

**Goal: a working, self-provisioning detection layer on the cardboard airplane — deterministic alerts + RCF anomaly detection, deployed by** `make deploy`**, verified by the same strict gates as the rest of the stack.**

Scope discipline: this is the product plan. The paper, peer-cohort prototyping, external comparators (IDK-S, SemPCA, etc.) and everything in `ML_AI.md` Part IV are **out of scope** until this plan is done. `ML_AI.md` is kept as historical/research reference; where this plan deviates from its Part V recommendations, the deviation is listed in [§ Deviations](#deviations-from-ml_aimd-part-v).

---



## 0. Ground truth (what the plan builds on)

- **Cluster:** 5× m2.medium (2 vCPU / 3.75 GB). 2 workers (`node-01/02`: data+ingest, box-pinned `generic-log-info-<node_id>`), 3 storage (`alice-ingest-3/4/5`: manager+data+ingest, hold `infologger` + `generic-log-other` + `cockpit-metrics`). OpenSearch 3.7.0 RPM, security disabled, heap **1g** (Stage 1).
- **Plugins:** the RPM bundle ships `anomaly-detection`, `alerting`, `notifications`; the Dashboards RPM ships their UIs. Bootstrap provisions detectors/monitors and asserts them via `verify_detection.py`.
- **Provisioning pattern:** `deploy/roles/dashboards/tasks/main.yml`, `deploy/roles/alerting_monitors/tasks/main.yml` and `deploy/roles/anomaly_detection/tasks/main.yml` run rendered `sh`/`curl` scripts on the control host (`templates.sh` → `patterns.sh` → ndjson import → hydrate), idempotent via PUT/ensure, verified by assertions. New AD/alerting provisioning slots into exactly this pattern.
- **Telemetry:** `metrics_poller.py` → `cockpit-metrics` every 30 s, kinds `cluster` / `index` / `node` / `fluentbit` / `osd` — real-time (poller stamps `@timestamp = now`). This is the only real-time signal on the VM deployment.
- **Logs:** three families — `infologger` (strict mapping, entity field `hostname`), `generic-log-other` and `generic-log-info-<node_id>` (generic mapping, entity field `host`). Dual clock on every log doc: `@timestamp` = event time (June under preserved replay), `collector_time` = Fluent Bit accept wall clock, `ingest_time` via `alice-add-ingest-time`. Shipping lag `ingest_lag_ms = ingest_time − collector_time` is valid on preserved replay; `enter_system_lag_ms` is prod-oriented.

**Prerequisite before any stage starts:** finish the pending v4 real-VM redeploy from lxplus (browser gates, then tag `cardboard-airplane-v4`). Building AD on top of an unlanded migration doubles the debugging surface.

**Status (2026-07-27): all seven stages are implemented in code; not one gate has been run on real VMs.** Every "landed" marker below means *written and committed*, not *observed working*. Nothing here has seen a live cluster: no detector has reached RUNNING, no monitor has fired, no bucket-level trigger script has been evaluated by the alerting plugin, the p99 lag that should set window delay has never been measured, and the HCAD `_profile` numbers this plan repeatedly says not to guess are still guesses. Specifically unverified and worth watching on the first soak:

- **the sub-second `collector_time` stamp.** The stamp is now the first filter in the chain, `match: '*'`, `time_as_table: true`, reading `ts.sec`/`ts.nsec`. The assumption is that at the head of the chain `ts` is still Fluent Bit's own arrival time (tail line-read / tcp receive) and not an event time parsed out of the payload. Check that `ingest_lag_ms` is continuously distributed, not clustered on second boundaries — and that `enter_system_lag_ms` did not shift by ~1 s in either direction.
- **the nil guard on `rewrite_tag` re-emitted records.** `family.info` / `family.other` records pass the `match: '*'` stamp a second time; they should hit `record['collector_time'] ~= nil` and return code 0. If the emitter strips the field instead, those two families get an accept time ~ms later than the truth (harmless) — but if the guard misfires the other way, the field goes missing and every generic-family detector starves.
- **the `buckets_path` forms** (`baseline_24h._count` for a filter sub-aggregation, `slice0>docs` for a metric sub-aggregation) resolving inside a bucket-level trigger, and whether the trigger is *evaluated* or *skipped* when a metric path is null (the lag scripts null-check defensively either way).
- **the rollup service's first hour.** `alice-trend-rollup` starts after bootstrap, so `trend-rollup` is empty on a fresh deploy and every trend monitor is inert until the first buckets land; `trend-rollup-stale` will legitimately fire once during that window. Confirm one 10-minute pass over five combinations finishes well inside the bucket period on 2-vCPU nodes.
- **share-of-fleet arithmetic on real data** — in particular that `fleet_count` summed over a host's own rows behaves as intended for intermittent hosts (buckets where the host is absent contribute nothing to either side).
- the ingest-pipeline script parsing the actual `collector_time` / `_ingest.timestamp` formats on all three families;
- the notification channel + 30m throttle round-tripping through `verify_detection.py` on a cluster that already has monitors from an older deploy.

Treat the first real-VM run as debugging, not confirmation.

---



## Stage 1 — Foundations (small, do first, everything depends on it)

1. **Plugin guard.** Add a task in the opensearch role (or bootstrap preflight) asserting `opensearch-plugin list` contains `opensearch-anomaly-detection`, `opensearch-alerting`, `opensearch-notifications`, and that `GET _plugins/_anomaly_detection/detectors/_search` answers. Fail the deploy loudly, not at first detector-create.
2. **Materialize dual-clock lag.** Fluent Bit stamps `collector_time` (wall clock at accept). The `alice-add-ingest-time` pipeline keeps `ingest_time`, then computes `ingest_lag_ms = ingest_time − collector_time` (shipping lag — valid on preserved replay) and `enter_system_lag_ms = collector_time − @timestamp` (event→collector — huge/meaningless under preserved June replay, useful in production). Both lag fields land in the component mappings. Implementation caveat: the script processor must parse whatever string/number formats Fluent Bit and `_ingest.timestamp` emit — parse defensively; test against real docs from all three families. *Schema change — flagged in Deviations.*
3. **Poller delta fix.** `output_retries_failed` is emitted cumulative-only; add `output_retries_failed_delta` in `metrics_poller.py` (and the `alice-cockpit-metrics` template mapping) so the Stage 2 rule is a plain `> 0` test like its siblings.
4. **Heap bump.** Raise `opensearch_heap_size` from 512m to **1g** (group_vars, one line). See [§ Non-optimal](#non-optimal-things-in-the-current-system) — 512m on a 3.75 GB VM is needlessly tight and directly caps AD model memory (10% of heap). 1g leaves ~2.5 GB for OS/page cache/Fluent Bit; safe on these flavors.

**Gate:** `make deploy` green; `collector_time` + small `ingest_lag_ms` on fresh docs in all three families (preserved replay); plugin assertions pass; cluster stable at 1g heap (heap_percent trend in cockpit).

## Stage 2 — Layer 0: deterministic hard rules (highest signal per unit of work)

Alerting monitors over `cockpit-metrics`, provisioned as code: JSON definitions in `deploy/roles/alerting_monitors/files/monitors/`, a new `alerting.sh.j2` rendered next to `templates.sh` and run from `roles/alerting_monitors/tasks/main.yml`. Idempotency: monitors get random IDs on create, so the script upserts **by monitor name** (search → create-or-update). All fields below already exist in `cockpit-metrics` (after Stage 1.3).


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

**Throttle (landed):** every monitor under `files/monitors/` has a trigger action with `throttle_enabled: true`, `throttle: {value: 30, unit: MINUTES}`, targeting Notifications channel `alice-incluster-alert-sink` (webhook → `alice-alert-actions` write alias on loopback `:9200`). Despite the name, that channel notifies nobody: it is a write-only action log. Bucket-level actions use `action_execution_policy.per_alert` so throttle is **per alert key** (per `node` / `host` / `hostname`), not one global mute for the whole monitor. `templates.sh.j2` owns the index template and the rollover alias, `ism.sh.j2` attaches the retention policy that rolls it weekly and deletes each backing index at 30d, and `alerting.sh.j2` upserts the channel idempotently before monitors; `verify_detection.py` asserts the channel and 30m throttle on every expected monitor.

**Alert delivery (v1):** alerts land in the Alerting plugin's own alert index and UI (already shipped in Dashboards); action payloads also index into `alice-alert-actions` so throttle has a real in-cluster destination, and that action log is bounded by its own ISM policy — it is written, never read, and never delivered to a human. Add an **Alerts panel to the ALICE Cockpit dashboard** (extend `gen_cockpit.py` + regenerate `cockpit.ndjson`) and an active-alert count on the `/ops` page (`ops_server.py` already queries the cluster). No external Slack/email; the nginx "alertmanager slot" from the README stays the future seam. Flagged in Deviations.

**Gate:** `make deploy` twice → identical monitor set (idempotent); kill Fluent Bit on one worker → "collector down" alert names that node within ~2 min; stop `alice-metrics` → telemetry-silence alert fires; restart everything → alerts complete/acknowledge cleanly.

## Stage 3 — Layer 0.5: RCF detectors over cockpit-metrics (real-time today, no new data needed)

Three small detectors (≤5 features each, per plugin guidance), provisioned as code in `deploy/roles/anomaly_detection/files/detectors/` via the same upsert-by-name script, then **started** by the script (create ≠ start):


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

Alerting monitors (same `files/monitors/` + `alerting.sh.j2` path as Stage 2). Schedule **10 min**; severity warn. Every monitor reads the **`trend-rollup`** index written by the `alice-trend-rollup` service (Stage 7.4, landed early — see below), never raw logs.

**Rollup substrate (landed).** `alice-trend-rollup` on the control node aggregates each closed 10-minute wall-clock bucket into one small doc per entity, for five (family, entity-field) combinations, carrying `doc_count`, `ef_count`, **`fleet_count`**, `fleet_ef_count`, `entity_count`, and `p95`/`avg` for both lag fields. Deterministic `_id` + a 3-bucket rolling re-write make it idempotent and self-healing across restarts and late arrivals. A `family: _meta` heartbeat per bucket carries `entity_count`/`truncated`. This one job is what makes items 2/3/4/6/7 of the trend fix-list possible at once.

**Retention is doc-level here, deliberately (Non-optimal §2 exception).** Every other index in this stack uses ISM whole-index delete-by-age. Applied to `trend-rollup` that would delete the entire 7d baseline in one step every 30 days: the monitors would fall back to a 24h baseline that is also empty, hit `return false`, and go **silently inert** for a week while the index refilled — failing safe but invisibly, which is exactly the failure mode `trend-rollup-stale` was added to prevent (and would not catch, since heartbeats resume immediately). So `trend-rollup` carries no ISM policy; the service prunes its own docs hourly with `_delete_by_query` on `ts` (`trend_rollup_retention_days`, 30d). The index is small enough that doc-level deletes and their merge cost are irrelevant.

**Dwell (landed):** a single noisy 10m window must not fire. Each evaluation requires the condition on **three consecutive 10m rollup buckets**, offset by 10m (`-20m→-10m`, `-30m→-20m`, `-40m→-30m`) so the newest bucket read is always complete. **Baseline** = ~7d **excluding the last 40m** (24h fallback when 7d empty). Rising signals fire only on `slice/baseline ≥ 2` across all three; volume additionally fires on all three `≤ 0.5`, never mixed. Same 30m per-alert-key throttle as Stage 2.

**Metrics (revised — the substance of the fix):**

- **Volume is share-of-fleet**, `sum(doc_count)/sum(fleet_count)`, not an absolute rate. A fleet-wide ramp (run start) moves numerator and denominator together and cancels, so the correlated 30-alert storm that Deviation 6 predicted is largely gone without run/phase metadata. Window length cancels too, so the old `10050`/`1410` minute-normalisation constants are deleted. Absence from a slice counts as share 0, so full silence is still caught.
- **E/F and errors are a share of that entity's own volume**, `sum(ef_count)/sum(doc_count)`. A host that doubles traffic and doubles errors now stays quiet instead of firing `trend-*-ef` and `trend-*-volume` for the same fact.
- **Lag is p95, not mean** — `avg` over the per-bucket `p95_*_lag_ms`. Backlogs show in the tail before the average moves.

**Guards (landed):**

- **Minimum counts.** Rising volume needs **≥50 docs in every slice**; rising errors need ≥50 docs **and** ≥10 error docs per slice. Previously only the lag monitors had a floor, so a host with a tiny baseline tripped on a handful of documents. Entities with no error history are compared against a 0.1% share floor rather than dividing by zero.
- **Lag record floor (`trend_min_lag_docs`, 100).** A p95 computed over a handful of records is just the maximum, so the lag monitors require ≥100 records in every slice. Without it the tail statistic degenerates into an outlier statistic precisely when the entity is quietest — the same small-numbers trap the count floors fix, which the first pass left open on the lag lane.
- **Lag floor — now `trend_lag_floor_ms` (250 ms default)**, substituted into the trigger scripts at bootstrap. The 2000 ms floor existed only to survive the whole-second `collector_time` stamp; that stamp is now millisecond-resolution (Non-optimal §8, fixed), so the floor drops by 8×. Raise the variable, not nine JSON files, if the first soak shows residual jitter.
- **Retired-host guard.** Collapse requires a 24h baseline averaging ≥50 docs per bucket in which the entity actually appeared — "was alive yesterday, is quiet now". After 24h of silence a decommissioned host self-clears. (An ending replay run still legitimately trips every host's collapse branch at once; that is real silence, throttled, and not suppressed.)
- **Entity ceiling is visible.** The rollup pages its composite properly (`after_key`) up to `trend_rollup_max_entities`; `trend-entity-cap` warns at `trend_entity_cap_warn` or on any `truncated` heartbeat. Silent blindness past 1000 entities was previously undetectable.
- **The lane's own dependency is monitored.** `trend-rollup-stale` pages when no heartbeat lands for 40m.

**Floors are measured, not chosen (2026-07-27).** Counted directly from S3, where each InfoLogger object is one calendar day for the whole fleet. **Daily volume spans 220×** — 254,003 rows (2.9/s) on 2026-04-01, 426,063 (4.9/s) on 2026-03-28, 3,710,214 (43/s) on 2026-03-30, 7,935,449 (92/s) in `p72`, and **55,592,675 (643/s)** in `p149`. Sampling only the small objects gives a badly wrong picture; the first pass of this note did exactly that and its conclusions were wrong. `il_replay_rate: 500` therefore mimics a **busy** day, not a typical one. On a data-taking day a median host produces ~112 lines per 10-minute bucket, so the **50-line floor** clears comfortably; on an idle day it produces ~2 and the rate rule correctly goes silent. Fleet error share swings 3.2 %→23.8 % across days, which is why error share is compared against each host's own history rather than a fixed number; 10 errors at ~330 lines/bucket is a 3.0 % share, the bottom of the observed range. Host count is stable at **211/214/215** across the three days counted. Concentration is a quiet-day artefact: `epn-infra12` is ~45 % of a quiet day but only 6.8 % of the busy one. Full numbers in `deploy/README.md` § Calibration.

**Do not widen the bucket for production.** An earlier version of this plan concluded the opposite — that production runs ~100× slower than replay and needs 6 h buckets — from two of the quietest objects in the archive. The real range makes 10-minute buckets right for data-taking, which is the regime worth alerting on. The floor then acts as an activity gate: rate rules run when there is data and stay quiet when there is not. What that leaves uncovered is a host dying while the fleet is idle — a **presence** question, not a rate question, which no floor or bucket width can answer and which wants its own rule (see Stage 7.5).

**Entry-lag monitors gate themselves (revised — no cutover flag).** The first version shipped `trend-il-entry-lag` / `trend-info-entry-lag` disabled behind `trend_entry_lag_enabled`, to be flipped at production cutover. Rejected on review: an alert that must be manually enabled is a silent gap the moment anyone forgets, which is precisely the failure class this lane exists to remove (cf. the ISM whole-index wipe and the rollup-stale guard). Replaced with a **ceiling**: any slice above `trend_entry_lag_ceiling_ms` (1 h) is archive age, not pipeline health, so the trigger returns false. Under preserved June replay entry lag is ~1 month and the rule is naturally silent; in production it is seconds and the rule is naturally live. Both ship enabled, the variable is gone, and `verify_detection.py` fails if any `trend-*` monitor is disabled. Shipping-lag monitors deliberately have **no** ceiling — a multi-hour shipping backlog is real and must page. Entry-lag *detectors* stay on either way: an anomaly score is advisory, a throttled page is not.

| Monitor | Family | Entity | Metric |
| --- | --- | --- | --- |
| `trend-il-volume` | `infologger` | `hostname` | share of fleet volume |
| `trend-il-ef` | `infologger` | `hostname` | E/F share of own volume |
| `trend-il-entry-lag` | `infologger` | `origin_host` | p95 `enter_system_lag_ms` (self-gating via ceiling) |
| `trend-il-shipping-lag` | `infologger` | `node` | p95 `ingest_lag_ms` |
| `trend-other-volume` | `generic-log-other` | `host` | share of fleet volume |
| `trend-other-errors` | `generic-log-other` | `host` | error share of own volume |
| `trend-info-volume` | `generic-log-info-*` | `host` | share of fleet volume |
| `trend-info-entry-lag` | `generic-log-info-*` | `origin_host` | p95 `enter_system_lag_ms` (self-gating via ceiling) |
| `trend-info-shipping-lag` | `generic-log-info-*` | `node` | p95 `ingest_lag_ms` |
| `trend-rollup-stale` | — | — | rollup heartbeat absent 40m (severity 1) |
| `trend-entity-cap` | — | — | entity ceiling approached / truncated |

Monitor count is now **22** (11 Stage 2 + 9 trend + 2 lane guards).

**HCAD sizing — the guess was wrong, measured 2026-07-27.** This plan assumed ~31 EPN hosts. Two real InfoLogger dumps pulled from S3 (`tmp_messages_p104`, `tmp_messages_p100`) contain **211 and 214 distinct hostnames**. Ten EPN-scoped detectors × ~214 entities ≈ 2,100 models, far past the ~256-model budget estimated for this heap. This is now the single largest sizing risk in the AD lane. Run `_profile` per detector on the first soak *before* trusting any log detector; likely mitigations are dropping the `-slow` twins for EPN-scoped signals, or restricting the EPN-scoped detectors to hosts carrying meaningful volume. The trend lane is unaffected (214 entities against a 2,000 composite cap, and `trend-entity-cap` watches the ceiling).

**Lag detectors use real p95 (corrected 2026-07-28).** An earlier revision of this plan asserted that a `percentiles` aggregation cannot be an AD feature because it is multi-value, and shipped `max` instead. That was wrong, and was asserted from memory rather than checked. `AbstractRetriever.parseAggregation` on the 3.7 branch handles `InternalTDigestPercentiles` explicitly, taking the **first** percentile from the iterator. So the eight lag detectors now use `{"percentiles": {"field": ..., "percents": [95]}}`. Two silent traps to respect: percentiles iterate **ascending**, so more than one entry in `percents` yields the lowest one with no error; and only the default TDigest implementation is handled, so `"method": "hdr"` throws `Failed to parse aggregation`. `max` was also the inferior signal — a single GC pause moves it, so the model learns a wide, noisy normal band. Feature and category changes reset every affected RCF model, which costs nothing today because none has ever run.

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

1. **ISM retention — DONE (2026-07-27), see Non-optimal §2.** Log families use rollover + per-backing-index delete behind a write alias, so the window held is always at least `retention − rollover period`; `cockpit-metrics` and `trend-rollup` prune by document. Tier intent preserved: info short (8d, rolled daily), other longer (35d), infologger longest (56d).
2. Detector/monitor definitions get the same **strict verify** treatment as v4 provisioning: post-deploy assertion script (counts, RUNNING state, profile health) — deploy fails loudly if the detection layer is degraded.
3. Re-measure p99 lag after any topology change; window delay is config, not folklore. **The shipped 2-minute log window delay is a placeholder — it has never been measured.** First soak measures it.
4. **Rollup substrate for the trend baselines — LANDED EARLY, and not as an OpenSearch transform.** The nine `trend-*` monitors used to re-scan 7 days of the largest indices on every evaluation to derive a baseline that changes negligibly between runs. They now read `trend-rollup`, written by the `alice-trend-rollup` service, so each evaluation is a few thousand tiny docs and the schedule went back to 10 min. This is `ML_AI.md` Part V item 3 ("an Alerting monitor over a scheduled transform") with one deliberate substitution: an in-cluster Python job instead of an OpenSearch scheduled transform — see Deviations 8. Remaining Stage 7 work in this area: nothing structural; confirm on the first soak that a 10-minute rollup pass over five combinations stays well under the bucket period on 2-vCPU nodes.

5. **Silence rule for low-volume entities (not built).** The count floors mean a host below ~50 lines per bucket is not watched for volume *in either direction* — it can neither spike nor collapse. At the measured quiet-day rates that is most of the fleet. Absence is a yes/no, not a statistic, so it needs no floor: *"this entity logged during the baseline window and has logged nothing for N hours"*. That is the one signal that works at any volume, and it closes the only real gap the floors create.

6. **Alert grouping at the delivery seam (not built).** Throttling is keyed per (monitor, entity), so a genuine fleet-wide breach emits one alert per host — ~215 of them. Keeping the per-entity key is right: it preserves *which* host broke, which a per-monitor throttle would destroy. The fix belongs at delivery, not at the throttle: collapse N alerts for the same monitor inside a window into one notification that names the count and the entities (Alertmanager `group_by` semantics). Deferred because nothing currently pages a human — alerts are rows in the `alice-alert-actions` action log, and a storm now costs only bounded disk, since that index rolls weekly and each backing index is deleted at 30d — and because share-of-fleet already removed the most common cause of a fleet-wide storm (run start). Becomes real work the moment the nginx alertmanager seam is wired to an external channel.

---



## Deviations from ML_AI.md Part V

*(informing you explicitly, as requested)*

1. **Dual clock chosen over shifted-clock for unlocking log AD** (Stage 4). Log detectors use `time_field: collector_time`; Discover/cockpit keep `@timestamp`. Shifted-clock replay remains available as optional Discover cosmetics but is **not** required for AD or for valid `ingest_lag_ms`. `images/replay/replay.py` stays PRESERVED. `enter_system_lag_ms` is prod-oriented (invalid as a latency signal under preserved June replay).
2. `ingest_lag_ms` **/** `enter_system_lag_ms` **materialized at ingest** (Stage 1) instead of computing lags inside each AD feature as a script. `ingest_lag_ms = ingest_time − collector_time` (shipping); `enter_system_lag_ms = collector_time − @timestamp` (entry). Cheaper per query, one definition instead of N, and queryable in Discover/cockpit. Adds fields to both component mappings — a (tiny) schema change to mappings that were previously "stay as-is".
3. **Category field corrected per family:** the doc says "category field `host`" — that's right for `generic-log-`* but **wrong for** `infologger`**, whose mapping has** `hostname`, and the strict mapping means a `host` category there would simply fail. Plan uses `hostname`/`host` per family.
4. **Deferred from the doc's "production now" list:** ~~Forecasting (item 4)~~ (adopted 2026-08-05, see Deviation 10), Suggest API (item 5), and cross-detector Correlation (item 6). The **deterministic trend-rule lane** (item 3) is **in scope** — provisioned as Stage 5 Alerting monitors (`trend-*`) with 3×10m dwell vs 7d/24h baseline (excluding last 30m). Reason for still deferring the rest: not needed for a *working* detection product on a 31-EPN replay; each adds tuning surface before the core has soaked.
5. **Two horizons, not three** (1 min + 30 min, dropping the 5 min tier) — with 30-second telemetry and 1-minute log buckets on a 2-vCPU cluster, the middle tier buys little and costs detector count. Add it later if the soak shows gaps.
6. **Run/phase conditioning skipped for now** — only `infologger` even has a `run` field; generic families have no run metadata. Doing this properly is a schema/enrichment task, not a detector flag; parked. Trend monitors may false-warn on run-start volume spikes until gated.
7. **Alert delivery = in-cluster only for v1** (Alerting UI + cockpit + /ops + `alice-alert-actions` sink for throttled actions). The doc says "Alerting → humans/incidents"; there is no external Slack/email anywhere in this stack. The nginx alertmanager seam is where external delivery goes when wanted.

8. **Trend baselines land in a Python service, not an OpenSearch scheduled transform** (Stage 7.4). A transform can group by time + entity and compute percentiles, but every aggregation it runs is confined to a single group, so it cannot produce a **fleet total per bucket** — and a bucket-level Alerting trigger can only read aggregations *inside* its own entity bucket, never a sibling at the parent level. Share-of-fleet therefore has only two shapes: a query-level monitor that can see the whole response but emits one undifferentiated alert with no per-host key, or a rollup that carries `fleet_count` on each entity's own rows. The second keeps per-entity alerts and throttling, so `alice-trend-rollup` writes the fleet total onto every doc. Cost of the deviation: one more service that can die (mitigated by `trend-rollup-stale`), and a rollup that is ours to maintain rather than the plugin's.

9. **Paced replay and historical analysis added (2026-07-29) — not in the original plan.** Stage 4 assumed that pointing log detectors at `collector_time` was enough to unlock real-time AD. It is not, and the first real soak showed why: `make replay` drained the archive in 7–13 minutes, but an RCF model needs **32 consecutive detection intervals** before it leaves Initializing, so all 14 log detectors froze half-trained while the 3 `cockpit-metrics` detectors — fed continuously by the poller — reached Running. Verified in the plugin source: cold start does train from history, but only when an entity appears in a live window, and `MAX_COLD_START_ROUNDS = 2` × `numMinSamples = 32` caps its reach at **64 intervals** back, so a replay that ended hours ago is unreachable. Two additions: (a) `make replay` is now **paced** by default via replay.py's own `*_REPLAY_RATE` knobs — no code change to `images/`, the rates just stretch the same archive over ~1 h — with `make replay-fast` for the old dump and `make replay-loop` for a never-ending feed; (b) `make backtest` runs the plugin's **historical analysis** (`POST detectors/<id>/_start` with a `start_time`/`end_time` body) over the window already in the indices and projects the findings into the cockpit's anomaly index. Historical analysis was **not** in this plan and is adjacent to the Correlation deferral in item 4 — it is adopted because it answers "what would our detectors have caught?" in minutes, without a fresh replay. Its limits are real: it builds task-scoped models that never touch the real-time ones (the Initializing column is unchanged by it), it covers the top **1,000** entities per detector (`max_top_entities_for_historical_analysis`) 10 at a time, and it feeds nothing to the trend/alerting lane, which reads `trend-rollup` off live `collector_time`.



10. **Forecasting adopted, against Deviation 4's own reasoning (2026-08-05, on explicit request).** One forecaster, `disk-fill`, HC on `os_node` over `cockpit-metrics kind:node`, plus one query-level warn monitor `disk-fill-forecast`. Monitor count 25 → 26; forecaster count 0 → 1. The selection rule was deliberately narrow: forecasting predicts *when a value crosses a threshold*, so it needs a metric with a real absolute threshold, slow smooth movement, and a continuous real-time feed. Only `disk_used_percent` passes; heap is a GC sawtooth, lags and rates are spiky and thresholdless, and any log-volume forecaster would freeze in Initializing exactly as the 14 log detectors did under bursty replay (Deviation 9). Suggest and Correlation stay deferred. **What this deviation costs, stated plainly:** Deviation 4 deferred forecasting because it adds tuning surface before the core has soaked. The soak has since run and the episode lane works, so that reason no longer blocks it — but the forecast lane itself is new and has been proved only offline. Three specific risks are managed rather than removed — the result index would take 15 shards against a ~60-shard storage-tier budget if `plugins.forecast.max_primary_shards` were ever unpinned (`forecasters.sh` pins it, `verify_detection.py` fails without it); forecast models draw a *second* 10 % heap slice, separate from the AD tracker, on a 1 GB heap and a cluster where node 3 has already been OOM-killed, which is why the lane is capped at five entities; and the lane is silent for its first ~2 days because an RCF model needs ~40 points at a 60-minute interval, so verify reports warm-up rather than failing. **Cardboard cannot validate the forecast itself** — disk there is a replay-and-delete sawtooth, not an organic ramp — so a green lane proves the contract, not the prediction. The alert is fleet-scoped and names no node, because forecast results store their entity in a nested field; this is the same trade `ad-high-grade` already makes, and the upgrade path (`flatten_custom_result_index` + a bucket-level monitor) is recorded in `deploy/README.md`.

## Non-optimal things in the current system

*(flagging, as requested — first two are fixed by this plan, rest are decisions)*

1. **512 MB heap on 3.75 GB VMs** wastes the hardware and caps AD memory at ~51 MB/node. Fixed in Stage 1 (→1g). If you'd rather not touch a green cluster, the plan still works at 512m for current entity counts — say so and I'll drop Stage 1.4.
2. **Retention — FIXED, and then fixed properly (2026-07-27).** The first pass added ISM policies, but they used `min_index_age` + `delete`, which deletes the **whole index** at that age — a periodic wipe, not a rolling window, so `infologger` would lose 90 days of logs in one step and start empty. The log families now use **rollover**: writes go to a write alias, ISM rolls a new backing index every `log_rollover_period` (7d) or `log_rollover_max_size` (20 GB), and deletes each backing index once it passes retention, so the window you always hold is `retention − rollover period`. `cockpit-metrics` and `trend-rollup` are too small for rollover to pay (daily rollover on `trend-rollup` would be 93 shards to avoid deleting ~40k tiny docs a day) and prune by document hourly instead. Shard budget, not disk, is the binding constraint — ~20 shards per GB of heap, ~60 across the storage tier — so `infologger` and `generic-log-other` dropped from 3 primaries to 1; storage-tier total is now ~45. An existing cluster keeps a concrete index that blocks the alias: bootstrap and `verify_detection.py` both warn loudly, and `make deploy-migrate-rollover` converts once (destructive, acceptable because the data is replayable).
3. **Security disabled** means the AD/alerting REST APIs (create/delete detectors, silence monitors) are unauthenticated on :9200 inside the SG. Acceptable for the cardboard airplane, unacceptable for anything called production — when the prod flight happens, the security plugin comes back on and every bootstrap curl grows auth.
4. **Three severity vocabularies — FIXED.** `I/W/E/F/D` (infologger), `Info/Warning/Error/Fatal/Sys` (stdout), `inf/err/cout` (dds), plus severity-less free-form stdout lines. The collector's last filter now stamps **`severity_norm`** ∈ {`debug`,`info`,`warning`,`error`,`fatal`,`system`,`unknown`} on every record. The rollup counts errors with one `severity_norm:(error or fatal)` filter, the four volume detectors' `ef_count`/`err_count` scripts read `severity_norm`, and the cockpit's saved searches and severity-over-time visualisation use it (regenerated via `gen_cockpit.py`). Vocabularies were confirmed against the real archive, not guessed: a DDS log sampled from S3 contained exactly `inf` (9,037), `err` (1,481), `cout` (1). **`rewrite_tag` routing deliberately still keys on raw `severity`** — `dds:cout` normalizes to `info` but routes to `generic-log-other`, so switching the rules would silently migrate data between the storage and worker tiers. That is a separate decision, not a side effect.
5. **`host` **vs** `hostname` split — FIXED.** The same filter stamps **`origin_host`** (= `hostname` on infologger, `host` on generic), one field meaning "the EPN this log was born on" across all three families. All ten EPN-scoped detectors moved to `category_field: ["origin_host"]`, the rollup uses it for every host-kind combination, and the cockpit's per-host saved search is now a single `origin_host:epn*` instead of `host:epn* or hostname:epn*`. Both new fields are mapped explicitly in the component templates — `infologger` is `dynamic: strict`, so an unmapped field would reject **every** document — and `verify_detection.py` asserts their presence on the live mappings. The raw `severity`/`host`/`hostname` fields are unchanged, so nothing that read them breaks.
6. **Single poller = single blind spot** — mitigated by the telemetry-silence monitor (Stage 2), properly fixed only by running the poller redundantly; not worth it at this scale.
7. **README documents an** `alertmanager_port` **var that doesn't exist** in `group_vars/all.yml` — docs ahead of code; trivial cleanup whenever that file is next touched.
8. **`ingest_lag_ms` had a ~1-second noise floor — FIXED.** `collector_time` was `os.time() * 1000` in a Fluent Bit Lua filter, truncated to whole seconds, so a healthy sub-second shipping path produced lag values that were mostly quantization *and* biased high by ~500 ms (truncation put `collector_time` before the true accept time; `enter_system_lag_ms` was biased low by the same amount). The stamp now runs as the **first** filter in the chain, `match: '*'`, with `time_as_table: true`, and takes the event's own arrival timestamp — `ts.sec * 1000 + ts.nsec / 1e6` — which for `tail` is line-read time and for the InfoLogger `tcp` input is receive time, both millisecond-resolution. A nil guard keeps `rewrite_tag` re-emitted records (`family.*`) from being re-stamped, and the filter returns code 2 so the carefully-set event `@timestamp` is untouched. Consequences: the 2000 ms trend floor drops to `trend_lag_floor_ms` (250 ms) and the four `*-shipping-lag` detectors should now carry sub-second signal. **Unverified on real VMs** — the first soak must confirm `ingest_lag_ms` is continuously distributed rather than clustered on second boundaries; if Fluent Bit turns out to hand the filter an already-parsed event time on some path, that path's lag will look wrong in an obvious way. Cross-VM clock skew is *not* a factor: `chronyd` is enforced in `roles/common`.



## Explicitly out of scope (until this plan ships)

Peer-cohort detector & everything in ML_AI Part IV · LLM/TSFM anything · external Python/Kafka AD pipeline · anomaly-triggered autoscaling (rejected permanently, Part II) · semantic-embedding novelty · Suggest/Correlation (deferred, see Deviations 4; Forecasting was adopted — Deviations 10).