# GROUPING_PLAN.md — Signals, incidents, and notification

**Goal: bounded, explainable notifications per incident episode, without losing any constituent signal.**

Not "one notification per real event" — that wording, used in the first draft of this plan, promises something deterministic grouping cannot guarantee without causal ground truth. What we can guarantee is that notification volume stays bounded, every notification explains which signals it covers, and no raw signal is ever destroyed by aggregation.

This is `PLAN.md` § Stage 7.6 promoted to its own plan. It grew because an audit found defects rather than a missing feature, and because the work spans two substrates and three consumers that § 7.6 does not mention.

**Status (2026-07-30): S1–S7 are implemented in code; no gate has been observed on real VMs.** The earlier status line said nothing here was implemented and that S4 onward was calibration-blocked. Both halves have moved:

- S1–S2 are landed: every action payload names its entity, `alerting.sh`/`templates.sh` apply live mappings as well as templates, both anomaly projections traverse with PIT + `search_after` under explicit `task_id` provenance and one canonical grade floor, `kind_of()` is replaced by `signal_catalog.json`, the four bare triggers are normalised, the two dead `per_alert` policies are gone, `collector_id`/`os_node` have replaced `node` in every monitor and detector, the rollup publishes a global bucket commit, and `max_actionable_alert_count` is pinned.
- S3's harness exists (`make inject`, five scenarios, a seven-metric scorer) but has produced **no measurements**.
- S4–S7 are built rather than blocked. The blocking argument was that their constants are calibration outputs — so the constants ship as *explicitly labelled design-derived placeholders* in `group_vars/all.yml` and in `deploy/README.md` § Calibration, and the injection harness prints the values that replace them. Shipping the mechanism unmeasured is honest; shipping a measured-looking number would not be.

Nothing here may be called done until those runs happen. See `NEW_PLAN.md` § Phase 5.

**Division of responsibility (the load-bearing decision):**

- **`alice-signal-projector`** owns *domain* semantics — identity, entity classification, lifecycle, episode assembly, topology, run state, and the durable record.
- **Alertmanager** owns *notification* semantics — grouping timers, inhibition matching, silences, routing, receivers.
- **Neither is the other's database.** Alertmanager does not persist alerts; `alice-incidents` is the incident record.

---

## 0. Ground truth (counted and verified, not remembered)

**22 monitors**, in two shapes that behave differently for everything here:

| | Count | Bucket key | Members |
|---|---|---|---|
| `bucket_level_monitor` | **16** | `node` (7 metrics) / `entity` (9 trend) | `collector-down`, `collector-unhealthy`, `data-loss`, `shipping-breaking`, `disk-cliff-warn`, `disk-cliff-page`, `heap-spiral` + 9 `trend-*` |
| `query_level_monitor` | **6** | none — one alert per trigger | `ad-high-grade`, `cluster-red`, `shards-stuck`, `telemetry-silence`, `trend-entity-cap`, `trend-rollup-stale` |

Of the nine `trend-*`: **seven are EPN-scoped** (`entity_kind: host`) and **two are collector-scoped** (`il-shipping-lag`, `info-shipping-lag`, `entity_kind: node`).

**Defects found by audit — all verified against the tree, all must be fixed before any grouping is built:**

1. **No payload carries an entity.** All 22 actions ship exactly `{"monitor","trigger","severity","period_end"}`. During a fleet-wide breach `alice-alert-actions` cannot say which host.
2. **`.opendistro-alerting-alerts` holds only ongoing alerts.** Completed alerts are swept to `.opendistro-alerting-alert-history-*` — which this deployment already knows: `reset_derived.py:17` wipes that pattern and `ism.sh.j2:141` gives it a retention policy. A consumer reading only the active index misses resolutions and leaves incidents open forever.
3. **`anomaly_digest.py` can silently drop anomalies.** `harvest()` uses `size: BATCH` (default **500**) with **no `search_after`**, over a `now-2h` window sorted by `data_end_time`, re-read every 60 s. With 17 detectors against ~214 entities, any broad anomaly overruns 500 in that window and the tail is lost. Filtering and sorting on `data_end_time` also misses late-indexed results; an overlapping `execution_end_time` watermark is the correct pattern.
4. **`kind_of()` misclassifies entities.** `ingest-flow` has `category_field: ["node"]`, reads `cockpit-metrics`, and filters `kind:fluentbit` — its entities are **collectors**, but `kind_of()` labels them `cluster node` because it infers from the index name. `dashboards-health` has no category so it becomes `fleet-wide` though it describes one service. **`alice-anomalies` is therefore not a trustworthy canonical input today, and `kind_of()` must not be reused as the topology classifier.**
5. **The `node` field is ambiguous by design.** All seven metrics monitors key on `node`, but it means the **collector** under a `kind:fluentbit` filter and the **OpenSearch node** under `kind:node`. On a worker VM both are `node-01`. Any label match on `node` alone will cross-suppress unrelated conditions. Same trap class as the `host`/`hostname` split that `PLAN.md` § Non-optimal 5 already fixed with `origin_host`.
6. **Three grade floors on one lane.** `alice-anomalies` keeps `grade > 0`, `/ops` counts `> 0.5` (`ops_server.py:83`, reading the raw plugin index), `ad-high-grade` fires at `> 0.7`. Three surfaces, three numbers, same minute.
7. **`alerting.sh.j2` creates indices but never updates mappings.** The index PUT treats `resource_already_exists_exception` as success, and there is no `PUT _mapping` anywhere in the tree. New fields would apply on a fresh cluster and silently not on an existing one.
8. **Trigger shape is inconsistent** — four monitors use a bare trigger object, eighteen use a wrapper. `verify_detection.py:94` handles both correctly, but two separate ad-hoc audits of this set have already been wrong because of it.
9. **Dead config** — `trend-entity-cap` and `trend-rollup-stale` carry a `per_alert` `action_execution_policy`; action execution scope is a bucket-level concept and is ignored on query-level monitors.
10. **Realtime and historical anomaly provenance is not separated.** `anomaly_digest.py:136` and `backtest.py:185` build the *same* key — `f"{detector_id}:{data_end_time}:{scope}"` — and write to the same index, distinguished only by a `run` field (`realtime` vs `backtest`) that is not part of the ID. So one can silently overwrite the other. Worse, the digest, `/ops`, and `ad-high-grade` queries have no `task_id` discriminator. OpenSearch AD's own result query defines realtime results as `must_not exists task_id` and historical results as `term task_id`. The current `data_end_time: now-2h` digest window happens to hide most old-data backtests; S1's correct move to an `execution_end_time` watermark would expose every historical task executed now and project it a second time as `run: realtime`, while `/ops` counts it and `ad-high-grade` can page on it. A new ID alone would preserve that false duplicate rather than fix it. Every reader needs explicit provenance, both writers need one catalog and grade floor, and the projection ID must contain run kind plus concrete source document.
11. **`trend-rollup-stale` can report a healthy lane that is partially failing.** There are two paths. First, `trend_rollup.py` appends each `_meta` row to the same `lines` list as entity documents and issues one `bulk()`; partial item failures are only logged, so metadata can succeed while entity rows fail. Second, a failed cohort query returns `None`, `roll_bucket()` executes `continue`, and metadata for the other cohorts is still written. `trend-rollup-stale` treats *any* recent `_meta` as healthy in both cases, while affected `trend-*` monitors read incomplete slices and hit `return false` — **silently inert, with the lane's watchdog green.** Requiring merely "some entity document exists" would not fix it: one surviving row does not prove completeness, and a successfully processed zero-row cohort is valid. Liveness needs a separate all-cohort bucket-commit protocol with exact counts.
12. **`backtest.py:159` sorts on `_id`.** It works — `indices.id_field_data.enabled` defaults to true — but it loads every `_id` into fielddata on a 1 GB heap, and it depends on a dynamic cluster setting that would break it if anyone tightened it. "Use another doc-valued field" is not enough unless that field is unique. OpenSearch 3.7 supports the exact stable traversal primitive: a PIT plus `_shard_doc` and `search_after`. `_shard_doc` is a PIT-local cursor only; durable identity remains the concrete `_index` plus `_id`.

**Consumers today:** Alerting UI; `/ops` active-alert count (`ops_server.py:75`) and anomalies-last-hour (`ops_server.py:83`, raw plugin index); cockpit panels over `alice-anomalies` *and* a second pattern over the raw results (`gen_cockpit.py`); `alice-alert-actions`, which nothing reads.

---

## 1. The four jobs

1. **Deduplication** — same condition, same entity, still true → notify once. **Have it** (30 min throttle per alert key; plugin `NEW`/`DEDUPED`/`COMPLETED` states).
2. **Grouping** — one fact about many entities → one notification naming count and members. **Alertmanager's job**, once signals carry canonical labels.
3. **Inhibition** — an alert that explains others silences them, scoped by matching labels. **Alertmanager's job**, with rules proven by injection only.
4. **Correlation into incidents** — infer shared root cause without being told the rule. **Not building** (§ S-never).

---

## 2. Architecture

```
  alert indices: current + history      ─┐   (API = reconciliation oracle)
  .opendistro-anomaly-results*          ─┤
  topology + run state (Ansible facts)  ─┘
                    │
                    ▼
          alice-signal-projector          ← domain semantics
             │            │
             ▼            ▼
      alice-signals   alice-incidents      ← durable record
             │
             ▼ (re-send actives on a cadence)
        Alertmanager                       ← notification semantics
             │
             ├──► webhook receiver ──► alice-notifications  (today)
             └──► external channel                          (when one exists)
```

The nginx seam already anticipates this: `deploy/roles/dashboards/tasks/nginx.yml` reserves an "another control-host-only service behind this nginx" slot, and `deploy/README.md:123` documents it. `alertmanager_port` becomes a real variable rather than the phantom flagged in `PLAN.md` § Non-optimal 7.

**Why Alertmanager rather than timers in the projector:** grouping windows, repeat suppression, exact-label inhibition and silences are a well-specified, well-tested problem that we would otherwise re-implement and re-debug. Silences alone are a capability we do not have today and want immediately — every `make replay-fresh`, every deploy, and every use of the `/ops` clear button is a window where alerts are expected and should be muted deliberately rather than ignored by habit.

**What Alertmanager must not become:** the incident database. It does not persist alerts across restart, and it expects senders to keep re-sending active alerts. `alice-incidents` is the record; Alertmanager is the delivery layer in front of it.

---

## 3. The canonical label schema

Alertmanager identity *is* the label set: grouping, inhibition and silences all match on labels, and changing any label value makes it a different alert. So the projector must emit a complete, stable label set on every signal.

**Labels (identity — stable, low cardinality, always present):**

| Label | Values |
|---|---|
| `alertname` | monitor name or detector name |
| `source` | `monitor` \| `detector` |
| `severity` | `page` \| `warn` |
| `cluster_id` | the deployment |
| `entity_kind` | `epn` \| `collector` \| `os_node` \| `service` \| `cluster` \| `fleet` |
| `entity_id` | the entity value, or a sentinel |
| `collector_id` | the EPN's observed parent, or a sentinel |
| `family` | `infologger` \| `other` \| `info` \| sentinel |
| `notification_scope` | `fleet` \| `collector:<id>` — the unit that shares ownership and remediation |

`notification_scope` exists so that grouping never has to be widened globally to express a local concern. `collector_id` is always present as a *diagnostic* label; it is not a grouping dimension, because adding it to `group_by` at 100+ collectors turns one fleet failure into up to 100 notification groups. Where a signal genuinely wants collector-local handling, it carries `notification_scope: collector:<id>` and a child route overrides `group_by` for that route alone. Grouping is notification batching, not causality — the causal record is `alice-incidents`.

**Annotations (evidence — free-form, changing values, no effect on identity):** anomaly grade and confidence, member counts, entity samples, slice values, links into Discover and the cockpit, `topology_version`, `run_id`.

**The sentinel rule is mandatory, not stylistic.** Alertmanager treats a missing label and an empty label as the same thing, and an `equal:` inhibition rule **applies when all its listed labels are missing from both alerts**. So an omitted `collector_id` on a cluster-scoped alert and an omitted `collector_id` on an unrelated alert compare equal, and a broad alert silently inhibits things it has nothing to do with. **Every label is always present with an explicit value** — use `none` / `all`, never omission. `verify_detection.py` asserts label completeness on emitted signals.

`entity_kind` is derived from an **explicit per-monitor and per-detector identity catalog** in the projector — never inferred from index names, which is exactly how `kind_of()` came to call collectors cluster nodes.

---

## Stage S1 — Correct the substrates (do first; everything else is unsafe without it)

This is defect repair, not feature work. It owns § 0 defects **1 and 3–12**. Defect 2 belongs to S4 because alert lifecycle ingestion does not exist until the projector does.

**S1a — entity in every payload.** For the 16 bucket-level monitors, template the bucket key; for the 6 query-level monitors the entity is *constant*, so template a literal scope (`cluster-red` → the cluster, `telemetry-silence` → `alice-metrics`, `trend-rollup-stale` / `trend-entity-cap` → `alice-trend-rollup`, `ad-high-grade` → fleet-wide tripwire).

**Write the template so it survives both action scopes.** If the actionable-alert limit is exceeded the plugin rewrites `per_alert` to `per_execution`, and a template assuming a single alert then renders wrong. `{{#ctx.newAlerts}}{{bucket_keys}}{{/ctx.newAlerts}}` is correct in both scopes — per-alert execution builds the same single-element list — so one template shape holds at 1 entity and at 214. This is why the old plan's separate "digest" stage disappears: it was the same change.

**S1b — mapping updates that actually apply.** Update both the templates and the live concrete indices in their owning bootstrap scripts: `alerting.sh.j2` for `alice-alert-actions`; `templates.sh.j2` for new `alice-anomalies` source/provenance fields, the `trend-rollup` bucket-commit fields, and the `cockpit-metrics` identity fields. Assert the live mappings in `verify_detection.py`. Creating an index and accepting `resource_already_exists_exception` is not a migration, and updating only an index template does not retrofit an existing concrete index. When S4 introduces `alice-signals` and `alice-incidents`, their bootstrap must follow the same create-plus-live-mapping contract.

**S1c — make both anomaly projections lossless and provenance-safe.**

- Realtime results are exactly `must_not exists task_id`; a historical projection is exactly `term task_id`. Never infer run kind from which script happened to read the hit.
- The realtime digest uses an overlapping `execution_end_time` watermark. Each cycle opens a PIT and traverses it with `search_after` on `[execution_end_time, _shard_doc]`; `_shard_doc` is discarded with the PIT and never persisted as a watermark.
- A completed historical task is traversed under its fixed `task_id` with PIT + `search_after` on `[data_end_time, _shard_doc]`.
- Both writers persist raw source identity as a deterministic encoding of concrete `_index` plus `_id`; their output `_id` includes `run_kind` plus that source identity. A source hit can therefore have one legitimate projection for its actual provenance, never one projection per reader.
- Replace `kind_of()` with one explicit per-detector identity catalog. Configure one canonical grade floor and use it in the realtime digest, backtest projection, `/ops`, and cockpit queries. `ad-high-grade` remains a deliberately higher non-canonical tripwire, not a fourth display floor.
- Until `/ops` moves to the canonical incident source in S7, both its raw-results query and `ad-high-grade` must apply `must_not exists task_id`. Every `alice-anomalies` saved search or visualization declares `run: realtime` or `run: backtest`; an unqualified mixture may exist only in an explicitly labelled comparative view, never an operational count.

**S1d — monitor and identity hygiene.** Normalise the four bare trigger objects; delete the dead `per_alert` policy from the two query-level monitors; add `collector_id` to `kind:fluentbit` and `os_node` to `kind:node`, retain `node` only for the migration window, map both fields explicitly, and move affected monitors/detectors onto the explicit fields before retiring `node`.

**S1e — make a rollup heartbeat mean "complete bucket", not "the process wrote something".**

1. Collect all five required cohorts. Any query failure aborts this bucket attempt; do not write a healthy marker for the remaining four.
2. Bulk the entity documents and require success from every item. A partial Bulk response is failure.
3. Write the five per-cohort metadata rows only after the entity Bulk succeeds, and require every metadata item to succeed.
4. With prior writes made searchable (`refresh=wait_for` or an equivalent explicit refresh contract), write one separate global bucket-commit document containing the exact expected cohort set, per-cohort entity counts, truncation status, and `committed_at`.
5. `trend-rollup-stale` reads only global commits with `complete: true`. A truncated cohort may publish diagnostic metadata for `trend-entity-cap`, but it may not produce a complete commit.

A successfully queried cohort with zero entities is complete with count zero. No test may replace exact cohort/count agreement with `entity_docs > 0`. All writes are deterministic, so a failed attempt is safely retried by the existing three-bucket backfill.

**Gate — reconcile identities, not counts.** Count equality can pass with the wrong rows, so the gate is set equality:

- `make deploy` twice → idempotent; one bucket-level and one query-level monitor forced to fire → both `alice-alert-actions` rows name their entity.
- Both anomaly writers store **raw source identity** — a deterministic encoding of concrete `_index` plus `_id` — and the gate compares **exact source-ID sets** under one detector catalog, one floor and a fixed closed `[gte, lt)` window.
- The realtime and historical fixtures each contain **more than one page**; the realtime fixture contains more than the old 500-row cap.
- Execute a historical task whose `execution_end_time` falls inside the realtime overlap. Its `task_id` source set appears exactly once in the backtest projection and **zero** times in the realtime projection.
- The same historical fixture neither increments `/ops`' realtime anomaly count nor fires `ad-high-grade`, and the cockpit's realtime/backtest views remain disjoint.
- A **late-arriving result** whose `execution_end_time` falls inside the overlap must appear on the next cycle.
- **Kill each projection after page one, restart it**, and prove the incomplete traversal replays to exact source-set equality with no loss. In an isolated adapter fixture, the same contract passes with `indices.id_field_data.enabled: false`, proving neither path depends on `_id` sorting without changing the production cluster setting.
- Inject one failed rollup cohort query and, separately, one rejected entity Bulk item. Neither attempt may publish a new complete bucket commit; the stale monitor must eventually fire. A successful zero-row cohort must publish count zero and allow a complete commit.
- `verify_detection.py` fails on a missing entity field, a stale live mapping, or a `kind_of`-style inferred classification.

That makes watermark behaviour the tested contract, not merely pagination.

## Stage S2 — Pin and measure plugin behaviour

Set `plugins.alerting.max_actionable_alert_count` explicitly and assert it. Then measure, on our cluster, the two behaviours this plan has so far only read in upstream source: whether throttling is honoured under `per_execution`, and what exceeding the actionable-alert limit actually produces. Record observed counts, scopes and payloads in `deploy/README.md` § Calibration.

Also decide throttle's role: once the projector is the only thing talking to Alertmanager, monitor throttle governs the audit sink only. **Nothing may wire a Notifications channel straight to Alertmanager** — a throttled webhook stops re-sending, the alert passes `resolve_timeout`, and Alertmanager declares it resolved while it is still firing. Delivery goes through the projector or not at all.

## Stage S3 — Fault injection (the calibration data)

Every threshold downstream is a percentage or a delay over storm behaviour, and we have observed **zero** storms. Injection manufactures them. Run each scenario at least twice.

| Injection | Expected shape | Calibrates |
|---|---|---|
| Kill Fluent Bit on collector `node-01` | `collector-down` + the roster-assigned EPNs going silent | Inhibition scope and the cause→consequence delay that sets `group_wait` |
| Drop one EPN's file mid-replay | one `trend-*-volume` entity | Independent-event recall — a single-entity incident must not be absorbed into a parent |
| CPU-stress a worker | shipping-lag on that `collector_id`, then drops | Causal direction and timing for any collector→child rule |
| Let a replay run end normally | fleet-wide collapse across all volume monitors | Mass-silence classification (§ S4) |
| Stop `alice-metrics` | `telemetry-silence` + every `cockpit-metrics` monitor blind | Suppressor precedence; whether blindness reads as health |

**Score more than notification reduction** — that number alone rewards suppressing everything:

- **signal reconciliation** — every raw alert and anomaly present in `alice-signals`, none lost;
- **independent-event recall** — a genuinely separate fault during a storm still surfaces;
- **incident purity** — members share one cause;
- **fragmentation** — one fault does not become many incidents;
- **time-to-notify** and **time-to-resolve**;
- **false inhibition** — anything muted that should have paged.

## Stage S4 — `alice-signal-projector`, `alice-signals`, `alice-incidents`

Same operational shape as `alice-trend-rollup`: a single Python service with deterministic `_id`, idempotent re-write of a rolling window, document-level pruning, and its own `signal-projector-stale` monitor so the lane watches its own dependency. The live five-VM deployment places it on storage node-04 rather than the already crowded control node; Alertmanager remains on node-03 and admits its internal API only from node-04.

**Two stores, deliberately.** One aggregate store cannot be both "one row per incident" and "no signal ever lost".

- **`alice-signals`** — one normalized, lossless row per source signal: stable `source_id`, state, canonical labels, first/last seen, `incident_id`, optional `suppressed_by`. This is the audit record and the drill-down target.
- **`alice-incidents`** — one stateful episode: scope, member count, entity samples, opened/resolved timestamps, class, and references back into `alice-signals`.

**Lifecycle sync — read the indices directly; the API is a reconciliation oracle, not the ingest path.** The alerting API is adequate for browsing and bounded reconciliation but cannot do lossless incremental sync: `TransportGetAlertsAction` builds `.sort(sortBuilder).size(tableProp.size).from(tableProp.startIndex)` — a single field sort with offset pagination, no PIT, no `search_after`, no updated-since predicate, no stable secondary key. Alerts move from the current index to history *while offsets are being traversed*, which is an ordinary offset-pagination race: duplicated and skipped rows.

So:

- **Current index** — a complete PIT + `search_after` snapshot every cycle.
- **History indices** — overlapping **`end_time`** watermark, PIT + `search_after` on `[end_time, _shard_doc]`, deterministic upserts, dedup on ingest.

**Cursor order and durable identity are separate contracts.** The current snapshot sorts on `[start_time, _shard_doc]`; history sorts on `[end_time, _shard_doc]`. Neither cursor sorts on `_id`, so neither loads `_id` fielddata. For identity, prefer `_source.id`, but accept `hit._id` while the source field is empty: OpenSearch indexes a newly generated alert before copying the Bulk response ID back into the in-memory `Alert`, so the first stored source can legitimately have no `id`. `AlertMover` preserves the hit ID when it writes history. If both forms are present they must agree; if neither is present ingestion fails. One logical alert therefore keeps one signal identity across the current → history move without rejecting the plugin's valid creation state.
- **Advance the watermark only after a complete traversal.**
- **Never infer completion from disappearance** out of the active snapshot.
- **Periodically reconcile** recent IDs and counts against the API as an oracle.

Coupling to a system index is not free; contain it behind one version-pinned adapter with contract tests that run on every OpenSearch upgrade. That is the cheaper of the two risks — the alternative is a quietly lossy ingest path, which is the failure mode this whole plan exists to remove.

**Episode assembly for anomalies — close on recovery evidence, never on generic inactivity.** An anomaly result is a point in a window, not an `ACTIVE`→`COMPLETED` alert, so a fixed grouping window would split one event and merge two. But "no anomaly document arrived" is **not** evidence of recovery, and the AD plugin's own source says why: `ADSaveResultStrategy` enqueues results at `result.getAnomalyGrade() > 0 ? RequestPriority.HIGH : RequestPriority.MEDIUM`, and `ResultBulkTransportAction` states its policy outright — above 80% of the indexing-pressure limit it will "index all non-zero anomaly grade index requests and index zero anomaly grade index requests with probability (1 − index pressure)". **Healthy windows are exactly the ones the cluster drops when it is busy**, so an inactivity close would silently manufacture RESOLVED under load.

The projector keeps the late-arrival-safe `execution_end_time` query watermark but traverses that fixed PIT in `[data_end_time, _shard_doc]` order, because episode transitions must see event windows chronologically. It consumes and discards one bounded page at a time, carries only the per-incident timelines across pages, and writes firing signal rows in bounded deterministic bulks. A failed traversal may therefore leave harmless idempotent signal upserts, but it writes neither the final incident set nor either watermark; the next cycle replays the overlap. The ten-minute PIT lease is renewed on every page and the service has finite systemd memory bounds.

Each detector therefore declares a silence policy — **and a policy without a named evidence source is inactivity-close under another name.** The AD state document does not serve as a per-entity, per-window completion ledger; for real-time detection it is principally written on state change, error, or job stop. So "a completed evaluation with no result" must be *proved*, not assumed.

Every row in the detector catalog carries:

```
silence_policy            healthy | unknown | failure
eligibility_evidence      the concrete source that makes a recovery opportunity eligible
expected_interval
maximum_lateness
close_threshold
healthy_windows_required  K
```

**Three kinds of evidence, and they are not interchangeable:**

| Evidence | What it proves |
|---|---|
| Explicit below-threshold result | AD produced recovery evidence |
| Source-coverage certificate | A source window closed completely over a known eligible population |
| Evaluator receipt | AD actually evaluated this detector/entity/window and called it healthy |

A heartbeat written by the *projector* proves only that the projector ran — it cannot prove AD evaluated anything, and the plugin's real-time state recording is tied to state changes, errors and job stops rather than being a per-entity/window ledger. So: if "silence is healthy" means *the source was authoritatively quiet*, a coverage certificate suffices; if it means *AD evaluated and its grade-zero output was dropped*, only an evaluator-side receipt will do, and there is no honest projector-side substitute. With neither, the episode is STALE.

**Do not build either yet.** None of the current 17 detectors is a defensible initial `silence_is_healthy` candidate. Initial policy:

- **All 14 log detectors: `silence_is_unknown`.**
- **The 3 metrics detectors: `silence_is_failure`** once roster/cadence authority exists; `silence_is_unknown` until then.
- Explicit below-threshold result → RECOVERING.
- Authoritative run/roster removal → CLOSED_EXPECTED.
- Missing result without proof → STALE.

Instrument **stale count and p50/p95 stale dwell per detector and cohort**. Introduce coverage certificates only if stale episodes become a demonstrated operational burden; introduce evaluator receipts only if we specifically need to separate *dropped healthy results* from *evaluations that never happened*.

**If certificates do become necessary, they are per cohort, not per detector.** The 14 log detectors collapse onto the five source cohorts `trend_rollup.py` already scans; the metrics detectors add `osd`, `fluentbit` and `node`. Evaluate coverage once per `(source_cohort, closed_window)` and fan the result out to every open episode using it — that avoids both a per-episode query and a second per-detector service. The 1-minute/30-minute twins share the same **certificate stream**, while their adapters compose evidence into their different eligibility windows; one certificate is not silently reinterpreted as 30 detector evaluations. Extend the existing rollup path rather than adding a service. A certificate carries cohort and window bounds, the exact **eligible** and **observed** entity sets, a query-contract version hash, `complete` / `truncated` / `finalized_at`, maximum lateness, and run/topology version. Missing certificate means unknown; an entity absent from `observed` means nothing unless it is present in an independently authoritative `eligible` set.

**The S1e bucket commit is still not a coverage certificate.** It proves that all required rollup cohorts and writes completed; it does not carry an independently authoritative eligible population or prove detector evaluation. If operational evidence later justifies certificates, write them separately after the S1e commit and their own eligibility/finality checks. With 10-minute buckets, one finalized certificate is **one** recovery opportunity, not ten synthetic 1-minute healthy windows.

`ad-high-grade` is fleet-wide: **its completion may never close an entity-level episode.**

State machine, keyed `(detector_id, entity_id)` and driven by **eligible evaluation opportunities** rather than wall-clock gaps:

```
OPEN → RECOVERING   on an explicit completed alert, or a result below the close threshold
     → RESOLVED     after K eligible healthy windows
OPEN → STALE        when expected evaluations or the source data disappear
OPEN → CLOSED_EXPECTED when the roster or run state says the entity is no longer expected
```

Inactivity may expire *storage*. It may never produce RESOLVED. K and the eligibility window come from S3 and from each detector's own interval — the 1-minute and 30-minute twins cannot share constants.

**Topology is observed, never recomputed — and its source is the roster, not a second mechanism.** Roster snapshots are immutable and keyed by `topology_version`, with an `effective_from` boundary. On first creation the projector selects the snapshot effective at the source event time, stamps its `collector_id` and `topology_version` onto the signal, and never re-enriches that signal from a newer roster. For a monitor alert use its lifecycle start time; for an anomaly use `data_end_time`. If no effective snapshot or assignment exists, fail closed to the explicit sentinel and bar collector-scoped inhibition. Do not derive a parent downstream from `epn_num % NODE_COUNT`: that is *replay placement*, not production topology, it is a deploy-time function of the `workers` group size, and going from two collectors to three re-assigns **two-thirds** of hosts (over `n mod 6`, only `n ∈ {0,1}` keep their index). The projector refuses to compare incidents across differing `topology_version`.

`HEALTH_METRICS_PLAN.md` § 2 and § 4.2 specify the artifact this needs: a **versioned fleet roster** published from Ansible (`kind: roster`, preferably in a small `cockpit-fleet` index) so monitors know who *should* be heartbeating and late signals can resolve the mapping effective at their event time. Retain roster versions for at least the maximum source-history plus projector-overlap horizon. **Publish one versioned history and let both plans read it** — a projector that maintains its own inventory alongside the roster is two sources of truth that will disagree during exactly the reconfiguration that makes topology interesting.

**Mass silence is unknown until proven normal.** Fleet-wide silence cannot by itself distinguish "run ended" from "farm-wide loss". Default is `class: unknown-mass-silence`, **and it pages**. Only authoritative `run_id` / `run_active` / `phase` telemetry may downgrade it to `run-boundary`. That telemetry is `PLAN.md` Deviation 6 unparked — only InfoLogger carries a `run` field today, so this is a schema and enrichment task with its own scope, not a flag. Until it exists, mass silence pages.

**Gate:** replay each S3 scenario through the projector; scores on all seven metrics recorded; no signal absent from `alice-signals` under any rule.

## Stage S5 — Alertmanager and the bridge

New Ansible role, control-host-only, behind the existing nginx seam; `alertmanager_port` becomes real. Single instance — gossip HA is not warranted here, and the projector's re-send makes a restart self-healing (Alertmanager persists silences and the notification log, not alerts).

**The bridge is the projector.** It re-sends every active signal to the alerts API on a cadence comfortably under `resolve_timeout`, and sends an explicit `endsAt` the moment it observes a resolution. Set `resolve_timeout` explicitly rather than inheriting the default, and derive the re-send cadence from it — not the other way round.

**Grouping config, starting values to be replaced by S3 measurements:**

- `group_by: [cluster_id, alertname]`, and it **stays** there. Collector-local handling comes from `notification_scope` (§ 3) plus a child-route `group_by` override, never from widening the global key. Injection tunes `group_wait`, `group_interval` and which routes deserve an exception; it cannot tell us whether humans own a problem fleet-wide or collector-by-collector, and that is the question grouping actually answers.
- `group_wait` from the measured cause→consequence delay, so an inhibiting alert reliably arrives before the alerts it should mute. The default is short; scenario 1 sets ours.
- `group_interval` and `repeat_interval` chosen against the monitor schedules (10 min for `trend-*`) so a group does not re-notify faster than its members can change.

**Receiver, today: a separate `alice-notification-ingest` service**, sharing an OpenSearch client and schema library with `ops_server.py` but not its process. `ops_server.py` mixes UI rendering, replay control, cluster wipes and blocking cluster calls, and suppresses request logging (`log_message` overridden at :407). Notification receipt needs a different reliability contract: bounded bodies and authentication, deterministic IDs so retries are idempotent, every bulk item checked, `2xx` only after durable acceptance, independent restart and resource behaviour, and request/retry/failure metrics. Hosting it in the ops UI would give it none of those.

**Correction to an earlier draft of this plan:** the ordinary webhook receiver reports *notification batches* — `status`, `alerts`, `groupLabels`, `commonLabels`, `commonAnnotations`. It carries **no** indication of which alerts were silenced or inhibited, so "the cockpit shows what was suppressed" was wrong as written. Suppression visibility requires Alertmanager's **event recorder**, which records alert lifecycle transitions, silence creation, notification delivery and mute/inhibit suppressions — and which is **experimental and behind a feature flag**. Expose `/notifications` and `/events` separately, tag documents with `record_kind`, and treat recorder output as **audit evidence, never incident truth**. An experimental facility must not become the source of record.

**Break-glass path (necessary exception to the direct-channel rule).** `signal-projector-stale` cannot page *through* the projector that just died, and the same applies to an `alertmanager-down` monitor. Both get an independent OpenSearch action posting straight to the notification-ingest endpoint, tagged `delivery_path: breakglass`. This deliberately bypasses Alertmanager, is limited to those two dead-man monitors, and must not grow back into a general-purpose sink.

**Receiver, later:** an external route is then a config change, not an architecture change.

**Gate:** stop the projector → active alerts age out and Alertmanager reports resolved (proving the re-send contract is real, and that nothing else is silently keeping them alive); restart it → alerts return; a silence covering `make replay-fresh` mutes the expected storm and expires on its own.

## Stage S6 — Inhibition, one proven rule at a time

Start with **only** what injection demonstrates, and only in the direction it demonstrates:

- `collector-down` inhibits its children, `equal: [cluster_id, collector_id]`.
- A telemetry-source outage inhibits only rules whose *sole* input is that source. `HEALTH_METRICS_PLAN.md` § 4.2 splits `telemetry-silence` into **control-plane silence** (no `kind:cluster`/`osd` docs → the thin poller is dead) and **fleet FB silence** (a fraction of the roster missing heartbeats). That split maps exactly onto this rule and should be adopted for it: control-plane silence inhibits the monitors fed by `cluster`/`index`/`node`/`osd` samples; fleet FB silence inhibits the `kind:fluentbit`-fed ones. One undifferentiated silence alert cannot scope either.

**Explicitly not initially:** `cluster-red` and `data-loss` as suppressors. `data-loss` is frequently an impact rather than a cause, and disk pressure can *produce* cluster-red — so the presumed causal direction is reversible, and an inhibition rule pointing the wrong way mutes the alert you needed. Each further rule requires an injection run showing direction and timing, plus a false-inhibition score of zero.

Every rule is re-checked for the sentinel requirement of § 3 before it ships.

## Stage S7 — Surfacing

`/ops` and the cockpit headline `alice-incidents`, with signal counts and drill-down kept visible as secondary evidence. `/ops` moves onto the same anomaly source and the same grade floor as the cockpit (§ 0 defect 6). `gen_cockpit.py` regeneration under the existing strict import gates. Runbook lines in `deploy/README.md`: what an episode is, how to see its members, how to tell a suppressed signal from an absent one, how to place a silence before maintenance.

## Stage S-never — learned and LLM correlation

Not building text-similarity clustering, learned co-occurrence, or runbook reasoning. Those methods exist to recover a dependency graph and an alert-type vocabulary; ours are small, named, and authoritative, and neither a storm corpus nor mature runbooks exists here. The one idea worth keeping from that literature is already in the plan: separate the signals that fire whether or not anything is broken before grouping anything else.

The plugin's own temporal-overlap correlation is worth running later as a **shadow comparator** against `alice-incidents` — a way to check our rules, never the incident truth.

---

## Interaction with HEALTH_METRICS_PLAN.md

That plan moves Fluent Bit self-telemetry from a central pull to a push from each collector, so it changes the ground this one stands on in four specific ways. Neither plan is a prerequisite for the other in full, but the ordering below is not optional.

1. **`collector-down` changes meaning, and it is this plan's only approved suppressor.** Today it is a bucket-level monitor firing on an observed `fb_up: 0`. After push it must become **absence of an expected heartbeat** (§ 4.2), because a dead collector emits nothing and never forms a bucket. Absence detection carries an inherent lag — heartbeat interval plus grace — so **the cause→consequence delay that S3 measures and S5 turns into `group_wait` will change when that cutover happens.** Either land the health-metrics cutover before S3, or re-run scenario 1 afterwards and re-derive the timers. Do not carry pull-era numbers across the cutover.
2. **The roster is shared, not duplicated** (see S4). It is simultaneously that plan's "who should be heartbeating" and this plan's authoritative topology map.
3. **The `node` ambiguity gets worse before it gets better.** After push, `node` on `kind:fluentbit` docs is stamped by Fluent Bit on the collector, while `node` on `kind:node` docs is still written by the central poller for the OpenSearch node — the same field name, same values, now two producers. § 0 defect 5 and the `entity_kind` enum of § 3 are what keep that from becoming cross-suppression; S1d should land before or with the push cutover, not after.
4. **Scale changes the value of everything here — and cuts against widening `group_by`.** That plan targets 100+ collectors. At that size per-collector *inhibition* matters far more than it does with two, and fleet FB silence becomes its own mass-silence class, which § S4 refuses to auto-classify as normal. But it is also exactly why `collector_id` must stay out of the global grouping key: at 100 collectors that would turn one fleet failure into up to 100 notification groups. Collector-local handling goes through `notification_scope` and a child route (§ 3).

**The identity contract between the two plans is settled and must land before either cutover.** Resolution is additive so the cutover cannot reintroduce the ambiguity:

- `kind:fluentbit` — add `collector_id`, retain `node` temporarily.
- `kind:node` — add `os_node`, retain `node` temporarily.
- Move monitors and detectors onto the explicit fields, then retire `node`.
- Publish immutable roster snapshots as **host/EPN → collector assignments plus `topology_version` and `effective_from`**, not a mutable bare `collectors: [...]` document.

**Landed in both documents (2026-07-30):** `HEALTH_METRICS_PLAN.md` § 2 carries the additive identity split as an explicit exception to its "same field names" contract, § 3.1 emits `collector_id` alongside a temporary `node`, and § 4.2 defines immutable roster versions containing `collectors` + `assignments` + `topology_version` + `effective_from`, with a standing prohibition on recomputing a parent from `epn_num % NODE_COUNT`.

Shared injection harness: that plan's Stage B gate (`systemctl stop fluent-bit` on one worker → page within ~2 min, without the poller manufacturing `fb_up: 0`) **is** this plan's S3 scenario 1. Run it once, record both sets of numbers.

## Deviations from PLAN.md

1. **§ 7.6's premise is wrong.** It says a fleet-wide breach emits ~215 notifications. Above the actionable-alert limit the plugin emits *one* — and with today's payload, an anonymous one. The problem is attribution before volume.
2. **§ 7.6 defers this because "nothing pages a human". Half true** — `/ops` and the cockpit are today's human surface and are already storm-vulnerable.
3. **Alertmanager moves from "reserved seam" to a built component** (Deviation 7 superseded, on the user's decision to prefer the mature tool). External *receivers* remain absent; the seam is now wired to an in-cluster receiver instead.
4. **§ 7.6 treats this as one job at the delivery seam. It is four** — substrate repair, projection, notification, inhibition — across two substrates and three consumers.
5. **Injection is promoted ahead of grouping**, because every threshold is measured, not chosen — the discipline § Stage 5 already had to learn once.
6. **`ad-high-grade` stays a non-canonical tripwire.** It *can* template one representative entity from its top hit, but a single representative is not an entity-resolved lane; the projector reads anomaly results directly.
7. **Run/phase telemetry (Deviation 6) becomes a real dependency**, not for detection but for safely classifying mass silence. Until it lands, mass silence pages.

## Non-optimal / risks

1. **Single-instance control-plane services.** `alice-metrics` and Alertmanager remain on node-03; `alice-trend-rollup` and the projector run on node-04, with the digest on node-05. Each remains a single-instance blind spot, mitigated by staleness monitors and properly fixed only by redundancy that is not worth it at this scale.
2. **Alertmanager does not persist alerts.** Correct behaviour, but it means the re-send contract is load-bearing: if the projector stalls, notifications resolve themselves while the fault continues. The S5 gate tests exactly this, and `signal-projector-stale` must page.
3. **Label cardinality.** `entity_id` over ~214 EPNs is fine for grouping; it would not be fine as a Prometheus metric label. Keep counts and lists in annotations.
4. **Stage 7.5's silence rule and this plan are one piece of work.** "Logged during the baseline, silent for N hours" will be the most storm-prone monitor we own; shipping it before S4–S6 ships a noise generator.
5. **`alice-alert-actions` is scheduled for retirement, not preservation.** Once the projector reads lifecycle state directly, action throttling has no role in the canonical pipeline, and keeping the index would leave exactly the half-authoritative ledger this plan set out to remove. At cutover: drop `actions` from normal monitors, stop writing the index, keep it read-only for a stated period, then delete it deliberately. **Consequence for S1: do not over-invest in its schema** — S1a still adds the entity because the payload defect is real today and the audit rows are needed until the projector exists, but the mapping work stops at what S1's gate requires. The only surviving direct-action path is the two break-glass dead-man monitors in S5.
