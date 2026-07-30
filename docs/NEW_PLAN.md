# NEW_PLAN.md — Ship health metrics + grouping, verified end-to-end

**Goal:** fully complete `HEALTH_METRICS_PLAN.md` and `GROUPING_PLAN.md`, in an order that keeps both plans coherent, then prove on the cardboard cluster that the **contracts and behaviours** are correct — push heartbeats, absence-based collector-down, lossless signals, bounded notifications, and proven inhibition.

**What cardboard can and cannot prove.** This deployment has a handful of collectors, not 100+. A green Phase 5 proves the identity/roster/absence/projection/notification contracts work under fault injection. The health-metrics claim that push *scales* to 100+ remains an architecture argument (O(N) work moves to the edge; doc volume stays trivial). Do not treat cardboard acceptance as a load test of a 100-node farm.

**Non-goals:** peer-cohort / ML research (`ML_AI.md` Part IV); learned or LLM correlation (`GROUPING_PLAN.md` § S-never); Prometheus as the AD store; external Slack/email beyond the in-cluster notification path those plans already define; auto-classifying mass silence as a run boundary (needs run/phase telemetry — until it exists, mass silence pages).

---

## Implementation status (2026-07-30)

**All code for Phases 1–4 is landed. No gate in any phase has been observed, because none can be: the prerequisite soak has not run and this workspace has no path to the cardboard VMs.** What that means concretely:

| Phase | Code | Gate |
|---|---|---|
| Prereq — `PLAN.md` soak | n/a | **not run** |
| 1 — GROUPING S1–S2 | landed | not run |
| 2 — HEALTH METRICS A–D | landed | not run |
| 3 — GROUPING S3 | harness landed (`make inject`, scorer) | **no measurements** |
| 4 — GROUPING S4–S7 | landed | not run |
| 5 — combined acceptance | n/a | not run |

**Deliberate deviation from working rule 2 ("gate before advancing").** That rule cannot be honoured from here — it requires a cluster. Rather than stop at Phase 1, the whole stack is built and every unmeasured constant is shipped as an explicitly labelled placeholder in `group_vars/all.yml` and `deploy/README.md` § Calibration, with the harness that replaces it. The rule still applies to *calling anything done*: nothing above is done, and Phase 5's checklist is entirely unticked.

**Other deviations, all recorded in the child plans:**

1. `HEALTH_METRICS_PLAN.md` § 3.1 named `fluentbit_metrics` or `prometheus_scrape` for the self-metrics input. Both emit metric chunks that Lua cannot reshape and that would break the flat-schema contract § 2 exists to protect. An `exec` input emitting one JSON record is used instead.
2. Stages A/B/C's dual-write window ships as two switches (`health_metrics_emit_legacy_node`, `collector_metrics_scrape_open`) rather than three sequential commits — the same migration expressed as configuration.
3. `roster_assignments` is configuration rather than a live query, because the Stage B gate demands that an unchanged redeploy mint no new `topology_version`; a snapshot recomputed from observed data would append a version every time a new EPN appeared. `make roster-discover` bridges observation to committed configuration.
4. `telemetry-silence` keeps its name and has its predicate narrowed to the control plane, rather than being renamed to `control-plane-silence`. Fewer moving parts, no monitor-retirement machinery needed, and the S6 inhibition rule scopes on the narrowed predicate either way.
5. The projector reads `.opendistro-anomaly-results*` directly through the same shared cursor module as the digest, rather than reading `alice-anomalies`. One traversal contract, two consumers, no second lossy path and no new dependency on the digest being alive.
6. Monitor count went 22 → 25 (`fleet-fb-silence`, `signal-projector-stale`, `alertmanager-down`).
7. S3 scenario 2 is injected at the ingest pipeline rather than by deleting an EPN's file. `replay.py`'s `_write_lines` has no per-host error handling, so a file-level failure kills the whole dds+stdout family instead of one host, and deleting the file makes Fluent Bit re-read the recreated inode from the head. The pipeline drop produces the specified observable exactly and reverses cleanly.
8. **Inhibition ships disabled.** S6 admits one rule at a time and only on injection evidence; zero injections have run, so `alertmanager_proven_inhibit_rules` is `[]` and Alertmanager mutes nothing. The three rule bodies are written and gated in the role template.

**Known incomplete, and named rather than hidden:** suppression attribution. `suppressed_by` and `suppressed_count` are written as sentinel/zero and nothing populates them, because the only source that reports what Alertmanager muted is its experimental feature-flagged event recorder, and § S5 forbids an experimental facility becoming the source of record. The `/events` receiver endpoint exists but nothing sends to it. With inhibition off by default nothing is currently being suppressed, so this is a gap in a lane that is not yet live — but it is a gap, and it blocks the S6 gate rather than the S5 one.

**A second review round found three more P0s and two P1s, all fixed and regression-tested:** an older completed alert could resolve a newer active episode because rows were applied in scan order rather than lifecycle order (now ordered, and a re-opened condition starts a new generation instead of overwriting the previous episode's record); the evidence overlap window let one healthy result count K times (now guarded by `last_healthy_window` / `last_breach_window`); notifications carried no signal or incident ids so nothing could be linked back and the scorer marked every page unnotified (the payload now carries `signal_ids` and `incident_id`, the receiver parses both, and the scorer fails a run whose notifications resolve to no members); `member_count` grew on every cycle because current alerts are fully re-scanned (membership now dedupes); and the `drop-epn-stream` pipeline replaced `alice-add-ingest-time` instead of delegating to it, silently stripping `ingest_time` and both lag fields from every non-target record (it now composes the real pipeline).

**A third review round found four more defects in the episode-generation work, all fixed:** the generation counter churned a new incident document on every overlapping traversal, reset to 1 once the previous terminal alert aged out of the history window, stamped every signal in a mixed cycle with the final generation rather than its own, and let a notification for one generation cover a page from another. The counter is gone. An episode is now identified by `episode_id = <incident_id>.<episode_start>`, where `episode_start` is the event time the episode opened — a value derived from the rows and from the episode already persisted, never from a counter or from processing order. It is therefore stable under replay, unaffected by what happens to be loaded, and distinct per episode all the way through the notification path.

**A fourth round found the same class of defect in the detector lane:** `detector_episode_start` returned the *latest open* episode for every incoming row, including rows whose window predated it, and `episode_id` was stamped onto the row before the breach-window guard could reject the stale transition. Because `alice-signals` uses deterministic source ids, replaying the overlap silently rewrote an older breach's attribution onto the newest episode, pointing notification membership and scorecard evidence at the wrong one. Detector rows are now classified against episode **time boundaries** — `episode_covering()` picks the episode whose `episode_start` is the greatest not after the row's own window, and a row falling after a closed episode opens a new one rather than joining the newest. The projector loads full per-incident episode timelines (`load_episode_timelines`) instead of only the latest episode, which is what makes that lookup possible.

**A fifth round found two defects in the evidence the calibrated run is meant to record**, both in the places flagged as the remaining risk. `apply_detector` cleared `stale_since` on any re-read row, so the overlap re-reading the same evaluation every cycle reset stale dwell to zero while the episode stayed STALE — provenance is now cleared only by a strictly newer accepted evaluation, which also lifts the episode out of STALE. And `classify_mass_silence` put `fleet-fb-silence`'s constant `entity_id: all` into the same set as per-collector rows and divided by roster size, so the class survived a two-collector roster and vanished on any larger one; a firing fleet-level silence now implies `unknown-mass-silence` directly, because its own monitor predicate already proves `fleet_silence_fraction` of the roster is quiet, and only `collector-down` rows are counted against roster size.

**A sixth round closed three faults in the scorer and its stop-projector harness.** False inhibition had considered only signals whose latest state was still firing, so a page that resolved before the scorer ran disappeared from accountability; every page alert opened in the run now remains accountable regardless of its terminal state. The deliberately unlinked break-glass receiver was being subjected to ordinary Alertmanager episode-linkage rules, so a successful `signal-projector-stale` delivery made its own scenario fail; break-glass now has a separate contract restricted to `signal-projector-stale` and `alertmanager-down`, and `stop-projector` requires the former. Finally, systemd `started` was treated as projector recovery even though no catch-up traversal had necessarily completed; projector heartbeats now carry `projector_cycle_ok`, and that scenario waits for a successful heartbeat newer than the restart boundary before scoring.

**The first real deploy exposed a sequencing failure before any soak began.** The Dashboards role waited for pushed Fluent Bit heartbeats even though `site.yml` did not run the worker collector role until afterward. The first cutover could therefore only time out. Detector and projector bootstrap verification now run with the heartbeat expectation disabled; immediately after the collector role, a separate control-host gate waits for a fresh heartbeat from every rostered collector and reruns the complete verifier with the push contract enabled.

**The resumed deploy exposed the next migration boundary.** OpenSearch rejects an update that changes a detector category field. Both health detectors intentionally leave the overloaded `node` field (`ingest-flow` moves to `collector_id`, `node-health` to `os_node`), so update-in-place could never complete. The bootstrap comparator now classifies category changes as immutable and deletes/recreates only those detectors; ordinary definition updates retain their existing IDs and unchanged detectors retain model state.

**Offline proof that the hardest contracts hold.** `deploy/roles/dashboards/files/test_signal_contract.py` runs without a cluster, in the deploy and via `make contract`, and asserts: one logical alert stays one signal row across the current → history move; every canonical label is present with an explicit value on both lanes; an EPN's parent comes from the roster and fails closed to the sentinel when unassigned; an episode opens on a breach, needs K explicit healthy windows to resolve, goes STALE rather than RESOLVED on a missing evaluation, and closes as expected when the roster retires the entity. It also covers duplicate evidence, repeated-firing idempotence, active-plus-old-history ordering in both scan orders, separate episode generations, notification linkage, pipeline composition, resolved-page accountability, the restricted break-glass contract, the post-restart catch-up barrier, the post-collector heartbeat gate, and immutable detector migration — 27 assertions. Each is verified to fail under a deliberate mutation of the code it guards; two early attempts passed under mutation and were rewritten until they did not, which is the only reason the episode-key defects were caught rather than shipped.

**Not built, and deliberately so:** health-metrics Stage E (Prometheus), Alertmanager HA, external receivers, run/phase telemetry, `PLAN.md` § 7.5, coverage certificates and evaluator receipts (§ S4 says build them only on demonstrated operational need).

---

**Source of truth for detail:** this document owns *order, gates, and completion criteria*. Depth stays in the two child plans — do not fork their design here. If a child plan and this one disagree on sequencing, this one wins; if they disagree on mechanism, fix the child plan and point here.

**Deliberate deviation from `GROUPING_PLAN.md` “run once”:** that plan equates health-metrics Stage B kill-FB with S3 scenario 1 and says run it once. This plan splits the work: Phase 2 records the **cause→consequence delay** for absence `collector-down`; Phase 3 measures post-push storm **shape**; Phase 4 re-plays the same injections through the projector for the full seven scores. Timing is captured early; reconciliation scores wait until `alice-signals` exists.

---

## Working rules

1. **No code comments.** Do not add explanatory comments to any produced or edited code, templates, shell, Lua, JSON, YAML, or Ansible. Existing heavy comment blocks in touched files may be trimmed; new commentary must not be introduced. Code is reviewed by hand.
2. **Gate before advancing.** A stage is not done when the code lands — it is done when its gate has been observed on a real cluster. This plan does not allow “code landed, soak later” escapes.
3. **One identity contract.** `collector_id` on Fluent Bit docs, `os_node` on OpenSearch node docs, temporary `node` only through an explicit cutover window that ends in Phase 2. Immutable roster snapshots carry `collectors` + `assignments` + `topology_version` + `effective_from`. Never recompute parents from `epn_num % NODE_COUNT`.
4. **Shared artifacts, one publisher.** Ansible publishes the roster; health monitors and the signal projector both read it. Do not maintain a second inventory.
5. **Calibrate after push.** Do not carry pull-era cause→consequence timings across the Fluent Bit push cutover into Alertmanager `group_wait`.
6. **Hold `PLAN.md` § 7.5.** The low-volume entity silence rule (“logged during baseline, silent for N hours”) is the most storm-prone monitor in that plan. Do **not** ship it until Phase 4 inhibition and grouping are gated. Shipping it earlier is a noise generator (`GROUPING_PLAN.md` Non-optimal 4).

---

## Prerequisite — detection layer actually runs

`PLAN.md` stages are implemented in code but were recorded as **unverified on real VMs**. Both child plans assume Layer 0 monitors and Layer 0.5 detectors fire. This prerequisite is not a short preface: if the soak has not happened, it can dominate calendar time before Phase 1 starts. Do not begin substrate repair on a detection layer that has never been seen to fire.

Before Phase 1:

1. Finish the cardboard redeploy / soak called out in `PLAN.md` § 0 (browser gates, detector RUNNING, trigger evaluation, rollup first hour, lag distribution).
2. Confirm detectors reach RUNNING, at least one bucket-level and one query-level monitor fire with today’s payloads, rollup produces buckets, and `verify_detection.py` is green post-deploy.
3. Record baseline lag / window-delay numbers in `deploy/README.md` § Calibration (placeholders become measured values).

**Gate:** `make deploy` twice idempotent; kill Fluent Bit on one worker → today’s pull-era `collector-down` fires; stop `alice-metrics` → `telemetry-silence` fires; cockpit and `/ops` show live alerts/anomalies.

If this soak is already done and documented in the README, skip to Phase 1 and cite that evidence. If it fails, fix `PLAN.md` gaps first — do not paper over them inside Phase 1.

---

## Phase map (do in order)

```
  Prereq: PLAN.md soak                         ← may be the largest block of work
       │
       ▼
  Phase 1 — GROUPING S1 (+ S2)                 substrate repair + additive identities
       │                                         (retarget existing monitors; no absence yet)
       ▼
  Phase 2 — HEALTH METRICS A→D                 push, roster, absence down, retire `node`
       │                                         (record kill-FB delay here)
       ▼
  Phase 3 — GROUPING S3                        post-push storm shape + timing calibration
       │                                         (not full projector scores yet)
       ▼
  Phase 4 — GROUPING S4→S7                     projector, Alertmanager, inhibition, surfacing
       │                                         (re-play S3 injections; full seven scores)
       ▼
  Phase 5 — Combined acceptance                contract proof on cardboard; scale claim scoped
```

S1–S2 before health metrics because S1d’s identity fields and mapping updates must land before or with the push cutover, and S1’s other defects make every later consumer unsafe. Health metrics before S3 because push changes `collector-down` timing — the delay S3/S5 care about.

---

## Phase 1 — Grouping substrate repair (`GROUPING_PLAN.md` S1–S2)

Implement S1a–S1e and S2 exactly as written in `GROUPING_PLAN.md`.

**Ownership boundary with Phase 2:** Phase 1 lands the *additive identity fields* and moves **existing** monitor/detector predicates onto `collector_id` / `os_node` while the central Fluent Bit scrape still runs. It does **not** invent absence-based `collector-down`, does **not** publish the fleet roster, and does **not** split `telemetry-silence`. Those semantic changes are Phase 2.

| Step | Owns |
|---|---|
| S1a | Entity (or constant scope) in every monitor action payload |
| S1b | Live mapping updates that actually apply (`alerting.sh.j2`, `templates.sh.j2`), including `collector_id` / `os_node` on `cockpit-metrics` |
| S1c | Lossless, provenance-safe anomaly projection (PIT + `search_after`, run kind, one grade floor) |
| S1d | Trigger hygiene; add `collector_id` / `os_node`; retarget existing FB/OS-node buckets onto those fields; retain `node` temporarily |
| S1e | Rollup global bucket-commit; `trend-rollup-stale` reads only complete commits |
| S2 | Pin `max_actionable_alert_count`; measure throttle / actionable-limit behaviour; record in README |

Do **not** start the signal projector, Alertmanager, or inhibition rules yet. Do **not** remove the central Fluent Bit scrape yet.

**Gate:** the S1 gate in `GROUPING_PLAN.md` (set equality on entities and anomaly source IDs, historical vs realtime disjoint, rollup failure injection, `verify_detection.py` fails on missing entity / stale mapping / inferred `kind_of`). S2 numbers written under Calibration. Existing `collector-down` still means observed `fb_up: 0`, now bucketed by `collector_id`.

---

## Phase 2 — Scalable health metrics (`HEALTH_METRICS_PLAN.md` A–D)

Execute Stages A–D of `HEALTH_METRICS_PLAN.md`. Stage E (Prometheus sidecar) stays optional and does not block completion.

**Ownership boundary with Phase 1:** Phase 2 owns push mechanics, the Ansible roster publisher, the change of `collector-down` to **absence of heartbeat**, the silence split (control-plane vs fleet FB), scrape removal, and **retirement of `node`** from monitors, detectors, and emitters once consumers are on the explicit fields. Prefer `cockpit-fleet` for roster history.

| Stage | Outcome |
|---|---|
| A | One worker dual-writes push + pull; ≥95% interval alignment; `collector_id` aggregatable; no health docs in log indices |
| B | Immutable roster from Ansible; absence-based `collector-down` on `collector_id`; silence split; topology version / `effective_from` proven |
| C | Pull FB scrape removed; all workers push; firewall tightened; cockpit copy updated; `node` retired from active consumers and new docs |
| D | Auth/TLS posture consistent with log outputs; backpressure check; retention/volume sanity at **cardboard N**; optional HA of thin poller deferred unless needed |

**Gate:** each stage gate in `HEALTH_METRICS_PLAN.md` § 6. Especially Stage B/C: `systemctl stop fluent-bit` on one worker → that `collector_id` pages within ~2 min **without** the poller writing `fb_up: 0`; thin poller stop → control-plane silence only while FB heartbeats continue; unchanged inventory redeploy does not mint a new topology version; assignment change appends exactly one version.

**Record now (timing only):** cause→consequence delay from kill-FB → absence page, and from kill-FB → first child symptom. These numbers seed Phase 3/5 `group_wait` work. Do **not** treat this run as a full S3 scorecard.

---

## Phase 3 — Fault injection for shape and timing (`GROUPING_PLAN.md` S3)

Run the full S3 injection table **after** push cutover, on absence-based semantics.

**What this phase can score** (no projector yet):

- cause→consequence delays (refresh Phase 2 timings across the full scenario set)
- which alerts appear, in what order, under each injection
- time-to-first-notify for raw monitor/anomaly paths that already exist
- qualitative storm shape for later `group_wait` / `group_interval` choices

**What this phase cannot score yet** (needs Phase 4):

- signal reconciliation into `alice-signals`
- incident purity / fragmentation against `alice-incidents`
- false inhibition (no Alertmanager inhibition until S6)
- notification-volume reduction via grouping

Re-run scenario 1 (kill FB on `node-01`) even though Phase 2 already exercised it, so the full scenario set shares one harness and one README section. That re-run is intentional (see deviation above), not accidental duplication.

**Gate:** each scenario run at least twice; shape and timing results in `deploy/README.md` § Calibration; no scenario left “expected shape unknown.” Full seven-metric scorecard is explicitly deferred to Phase 4.

---

## Phase 4 — Projector, notifications, inhibition (`GROUPING_PLAN.md` S4–S7)

Land in order:

1. **S4** — `alice-signal-projector`, `alice-signals`, `alice-incidents`; lifecycle sync from alert current + history indices; anomaly episode machine with silence policies as specified; topology from roster only.
2. **S5** — Alertmanager behind the existing nginx seam; projector bridge re-send contract; `alice-notification-ingest`; break-glass path only for projector/Alertmanager dead-man monitors. Set `group_wait` from Phase 2/3 absence-era timings, not pull-era numbers or upstream defaults.
3. **S6** — Inhibition one proven rule at a time (`collector-down` → children; split telemetry-silence as in health-metrics § 4.2). No speculative `cluster-red` / `data-loss` suppressors.
4. **S7** — `/ops` and cockpit headline `alice-incidents`; runbook lines in `deploy/README.md`.

After the projector and Alertmanager exist, **re-play every Phase 3 injection** through the full path and score all seven metrics: signal reconciliation, independent-event recall, incident purity, fragmentation, time-to-notify, time-to-resolve, false inhibition. Notification reduction alone is not a pass.

**Gate:** each stage gate in `GROUPING_PLAN.md`. Hard ones: stop projector → Alertmanager resolves (proving re-send); silence covers `make replay-fresh`; every raw alert/anomaly from the re-play appears in `alice-signals`; false-inhibition score zero on the approved rules; seven-metric table recorded under Calibration.

Only after this gate may `PLAN.md` § 7.5 be scheduled as follow-on work (still not part of this plan’s “done”).

---

## Phase 5 — Combined acceptance

Both child plans are “done” only when this checklist is green on cardboard (and recorded). Green means **contract proof**, not a 100-node load certificate.

**Health metrics**

- [ ] All collectors push `kind:fluentbit` with `collector_id`; thin poller owns cluster/index/node/osd only
- [ ] Absence `collector-down` and split silence monitors behave under kill-FB and kill-poller
- [ ] Roster append-only versioning proven (noop redeploy vs real assignment change)
- [ ] `node` **retired** from monitors, detectors, and new metric docs (no open transitional waiver)
- [ ] Stage D hardening items checked or explicitly waived with reason (waivers do not include “scale unproven”)

**Grouping / notifications**

- [ ] S1 defects 1 and 3–12 closed; defect 2 closed via S4 history ingest
- [ ] Projector lossless under page-kill/restart and historical-vs-realtime fixtures
- [ ] Phase 4 re-play: all seven S3 scores recorded; grouping bounds notification volume without dropping signal rows
- [ ] Approved inhibition rules only; sentinel labels always present
- [ ] `/ops` + cockpit on `alice-incidents` with drill-down to `alice-signals`

**Verification discipline**

- [ ] `verify_detection.py` asserts identities, live mappings, roster contract, push heartbeats, absence path, and signal label completeness
- [ ] `make deploy` twice idempotent after the full stack
- [ ] Calibration section in `deploy/README.md` holds absence-era delays, actionable-alert behaviour, Phase 3 shapes, and Phase 4 seven-metric scores
- [ ] No new code comments introduced in the diff (spot-check touched files)
- [ ] README states clearly: cardboard verified contracts; 100+ collector scale is by design, not by farm test

**Optional / explicitly not required for “done”:** health-metrics Stage E (Prometheus); Alertmanager HA; external chat receivers; run/phase telemetry; `PLAN.md` § 7.5 silence rule.

---

## What “fully done” means

| Plan | Done when |
|---|---|
| `HEALTH_METRICS_PLAN.md` | Stages A–D complete and gated on cardboard; `node` retired; Stage E optional |
| `GROUPING_PLAN.md` | S1–S7 complete and gated; Phase 4 seven-metric scorecard recorded; S-never out of scope |
| This plan | Phase 5 checklist green; both rows above true; soak evidence cited in README; scale claim scoped to architecture |

---

## Risks to keep visible

1. **Skipping the prerequisite** builds Phases 1–4 on monitors/detectors that may not evaluate correctly — fix soak failures first.
2. **Calibrating S3 before push** wastes the measurement — Phase order exists to prevent that.
3. **Scoring reconciliation in Phase 3** is impossible without the projector — do not fake a seven-metric pass early.
4. **Two publishers of topology** will disagree on the first interesting reconfiguration — one Ansible roster only.
5. **Over-investing in `alice-alert-actions`** — S1a needs entities for today’s audit path; schema work stops at the S1 gate; the index retires once the projector owns delivery (`GROUPING_PLAN.md` Non-optimal 5).
6. **Inactivity ≠ recovery** for anomaly episodes — do not “fix” silence policies during S4.
7. **Shipping `PLAN.md` § 7.5 early** creates a storm source before inhibition exists — held by working rule 6.
8. **Comment creep under long migrations** — long Lua/filter and monitor JSON diffs are where commentary tends to reappear; keep them clean.
9. **Cardboard green ≠ 100-node proof** — do not expand “done” to include an untested farm.

---

## Pointers

| Need | Read |
|---|---|
| Push architecture, absence monitors, thin poller | `docs/HEALTH_METRICS_PLAN.md` |
| Defects, labels, projector, Alertmanager, inhibition | `docs/GROUPING_PLAN.md` |
| Detection product baseline and soak caveats | `docs/PLAN.md` |
| Deploy / calibration notes | `deploy/README.md` |
