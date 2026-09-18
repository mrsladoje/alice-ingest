# The agreed rework (2026-09-09 review, verdicts accepted 2026-09-10) — target design context

Copied from the project skill lubos-meeting. Sections 2 to 7 only. Code facts were verified on 2026-09-10.

## 2. Lubos's eight points and the agreed verdicts

1. **Merge Dashboards, anomaly detection and the template catalog into the
   opensearch role; rename `os_dashboards` because `os` reads as operating
   system.** Agreed. The opensearch role already switches mode with a flag
   (`opensearch_configure_cluster`), so Dashboards becomes a third mode and
   detection a fourth task file. Thanasis's MR !413 holds `tasks/dashboards.yml`
   inside his opensearch role; that is the precedent Lubos was pointing at. The
   catalog decision is open: check first whether its expiry work can become ISM
   policies plus monitors.
2. **Rename `collector` to say the technology.** Agreed: `loggy_fluentbit`. The
   stamper stays inside it as the Forward-loop filter Fluent Bit calls.
3. **Shifter view stays a separate role (a standalone Preact app), but rename
   it; consider moving the Templates page into Dashboards.** Rename agreed:
   `loggy_shifter_ui`. The Templates page stays in the Preact app. The argument
   to use: a Dashboards plugin must be rebuilt per OpenSearch version, which is
   the objection Lubos himself raised in point 6. Do not use "React is lighter".
4. **Decouple Fluent Bit from the live lane with a message bus.** Agreed.
   Verified: `collector.yaml.j2` has an `http` output straight to the shifter's
   `/ingest`. Moving the live lane today means editing 200+ workers. Reuse the
   `kafka` role in MR !413 instead of writing one. `deploy/README.md` Item 9
   records why the queue was not built on staging (memory); that goes in the
   report.
5. **Cockpit metrics could not be explained and is "a bunch of Python
   files".** The factual model, one sentence: the poller on the control host
   queries the cluster every 30 s (`_cluster/health`, `_cat/indices`,
   `_nodes/stats`, the Dashboards status endpoint) and writes samples into
   `cockpit-metrics`; workers push their own `kind: fluentbit` health sample
   through the normal pipeline; the poller compares those against a roster to
   mark absence. It never scrapes a worker. The code is 4 files, 702 lines. The
   honest weakness is overlap with the Telegraf and Mimir estate CERN already
   runs; `roles/telegraf` exists in the target repository.
6. **Port Python to OpenSearch plugins.** Rejected: plugins pin to a version and
   need a Java toolchain. The credible answer is packaging shared code as one
   versioned `loggy` package, deleting dead code, and moving build-time
   generators out of roles. Lubos's objection has two halves: is it navigable,
   and does it need to exist. Packaging answers the first; deletion and the
   dataflow diagram answer the second.
7. **One merge request, atomic commits, each building on the last, never
   vertical slices.** Agreed. Commit 1 is `hosts.yml` and the environment fix
   only; everything OpenSearch-specific arrives with the OpenSearch commit.
   The mechanism is not cherry-pick: no commit in our history is "hosts.yml
   alone". Each rung is hand-assembled from the fixed tree and must deploy on
   its own. Expect 40 to 60 rungs; do not target a number. All five earlier MRs
   (!481 to !485) and their branches are deleted.
8. **The opensearch role itself.** Bash that configures REST state is a red
   flag; use `ansible.builtin.uri`. Health checks likewise. README lists only
   overridable variables and explains rollover against retention. `common`
   becomes a `meta/main.yml` dependency. The couplings section goes. The
   rejected-upstream-roles table goes to the report, not the README. All agreed.
   The rule that actually holds, and that Lubos's own repository follows:
   shelling out for a one-shot action with no API (mdadm, dnf, podman pull) is
   fine; shelling out to configure state that has an idempotent REST API is not.

## 3. Verified code facts (2026-09-10)

- 14 roles under `deploy/roles`; 31,000 lines of Python; no `meta/` anywhere.
- Bash that configures REST state: 1,585 lines in 7 scripts.
  `templates.sh.j2` 378, `ism.sh.j2` 174, `register_node.sh` 220,
  `detectors.sh.j2` 256, `forecasters.sh.j2` 268, `monitors.sh.j2` 229,
  `patterns.sh.j2` 60. The detector script searches by name, compares state
  with embedded Python, then stops, updates and starts. That is a task chain.
- Duplicated Python, byte-identical: `masking.py` and `drainbench.py` in
  `loggy_collector`, `loggy_shifter_view` and `tools/templating`;
  `template_contract.py` in both roles and `deploy/shared`; `os_cursor.py` in
  four roles; `signal_identity.py` in two. `deploy/shared` is a runtime path
  (`ALICE_SHARED_PATH`) read by three roles.
- Migration code that must go (the stack has never been in production):
  flag `health_metrics_emit_legacy_node` (six sites plus two README sections),
  flag `log_rollover_migrate_existing` (defaults, `templates.sh.j2`,
  `register_node.sh`, README, `test_provisioning.py`), the 8 `patch-*.json.j2`
  fragments and their loader lines, two legacy template deletes in
  `templates.sh.j2`, three retire plays in `playbooks/site.yml` (control
  quiesce and moved units, anomaly digest, old live-lane unit), the retire
  task in `loggy_os_dashboards/tasks/cockpit.yml`, `RETIRED_NODE_CONSUMERS` in
  `verify_detection.py`. Two hits are real guards and stay: the retired-host
  guard in trend rollup and the retired-entity close in the projector.
- Build-time generators that run at deploy time: `gen_cockpit.py` (1,778
  lines) and `gen_monitors.py`. They belong in `tools/`, with the roles
  shipping the generated JSON.
- `vm.max_map_count` is set in both `common` and `loggy_opensearch/tasks/install.yml`.
- Alertmanager is a pure receiver. The projector reads
  `.opendistro-alerting-alerts`, AD results, `cockpit-metrics` and the roster,
  posts alerts to Alertmanager; Alertmanager webhooks to
  `notification_ingest` on the control host, which writes `alice-notifications`.
- Detectors are created after the poller's first samples exist (site.yml
  ordering). Any merge into the opensearch role must keep or deliberately drop
  that wait.
- Thanasis's MR !413: six commits, one role per commit (fluent-bit, opensearch,
  kafka, infologger, alertmanager, grafana), 69 files, `uri` throughout,
  READMEs of 11 to 32 lines. His `logstack` repository is configuration, not a
  Python package. Our READMEs are 151 to 583 lines.

## 4. Deliverables

1. One merge request to `alice-epn/ansible`, atomic commits, each deployable.
2. Documentation in `alice-epn/docs` from three views: sysadmin (redeploy,
   reconfigure), shifter (user), tester (replay, injection).
3. The CERN report. The rejected-upstream table, the queue reasoning and the
   Telegraf/Mimir overlap go here.
4. A poster.

## 5. The agreed order

Fix the current roles first, until one full redeploy on the five staging VMs is
green. Only then build the commit ladder. The ladder must present the final
design, not the history. Fixing after the ladder exists rewrites tested rungs.

## 6. The fix pass, one step per session

Steps 2 to 5 move files that steps 6 to 9 edit. Keep the order.

1. **Target diagram and role map.** Draw the dataflow against the target, not
   the current code. Decide the final role list, the bus placement, what gets
   deleted. One document. Every later step reads it.
2. **Delete all migration code.** The list in section 3. Done when a grep for
   legacy, migrate and retire finds only the two real guards.
3. **Move the generators out.** `gen_cockpit.py`, `gen_monitors.py` to
   `tools/`. Roles ship generated JSON. Done when no role runs a generator.
4. **Rename the standalone roles.** `loggy_collector` to `loggy_fluentbit`,
   `loggy_shifter_view` to `loggy_shifter_ui`. Done when every playbook passes
   syntax check and `test_provisioning.py` passes.
5. **Merge into the opensearch role.** Dashboards as a mode, detection as a
   task file, catalog decision made (ISM plus monitors first, timer if not).
   Preserve or deliberately drop the wait-for-samples ordering.
6. **Common as a dependency.** `meta/main.yml` on the opensearch role. Remove
   the duplicated kernel parameter. Decide who owns firewalld.
7. **Port the opensearch scripts to `uri`.** Templates, ISM, `register_node.sh`.
   ISM updates need a GET for `_seq_no` and `_primary_term` first. Done when a
   second run reports zero changes.
8. **Port the dashboards and detection scripts to `uri`.** Patterns, then
   monitors and channels, then detectors and forecasters with `include_tasks`
   in a loop for stop, compare, update, start.
9. **One `loggy` package for shared code.** Masker, benchmark, contract,
   cursor, signal identity. Delete `deploy/shared` and the byte-identical test.
   Repository likely `alice-epn/loggy`. Single-consumer services stay in roles.
   Roles install a wheel with `ansible.builtin.pip` and run an entry point.
10. **Cockpit metrics decision.** Check the target repository's `telegraf`
    role for an OpenSearch input. Keep roster and absence logic. Rewrite the
    README to the one-sentence model.
11. **All READMEs.** 10 to 40 lines each. Overridable variables only. Rollover
    against retention explained once. No couplings section.
12. **The live-lane bus.** Reuse the `kafka` role from MR !413. Fluent Bit gets
    a Kafka output; the shifter consumes. Optional; last.
13. **Exit gate.** Full staging redeploy green, short soak. Then the ladder.

## 7. Supporting facts a step may need

**The roles today and where `site.yml` runs them.** Python line counts are
from 2026-09-10.

| Role | Hosts | Python | Note |
| --- | --- | --- | --- |
| `common` | all | 0 | swap, sysctl, packages, chrony, firewalld |
| `loggy_opensearch` | all, then control | 0 | two modes; bash configures the cluster |
| `loggy_alertmanager` | control | 0 | pure receiver |
| `loggy_os_dashboards` | control | 1,938 | of which `gen_cockpit.py` 1,778 |
| `alice_ops` | control | 4,457 | operator page, injection, poison replay: tester tooling |
| `loggy_cockpit_metrics` | control | 702 | poller + roster |
| `loggy_anomaly_detection` | control | 3,062 | 17 detectors, 30 monitors, 1 forecaster |
| `loggy_signal_projector` | projector + control | 4,792 | the custom core; receiver on control |
| `loggy_trend_rollup` | background | 782 | 10-minute per-entity rows |
| `loggy_shifter_view` | shifter | 9,526 | Preact app, semantic model ships disabled |
| `loggy_collector` | workers | 3,890 | Fluent Bit + stamper (1,009 lines) |
| `loggy_template_catalog` | control | 921 | hourly oneshot, reads bucket documents |
| `loggy_replay` | workers | 1,261 | S3 replay, tester tooling |
| `faults` | workers + projector | 150 | fault agent, tester tooling |

`deploy/README.md` (2,500+ lines) is the only spec. Section 8 is the
detection runbook, section 9 the logstack port items. `docs/ARCHITECTURE.md`
is Thanasis's original architecture note. The `Makefile` must go before the
ladder; Lubos said so on 2026-08-14.

**The diagram Lubos asked for, in his words:** from cockpit metrics, to alerts
and anomaly detection, to the signal projector and the trend rollup, and
finally Alertmanager, so that anyone can understand how it works.

**Packaging, the criterion.** Package a file when more than one role uses it,
another module imports it, it has third-party dependencies, or it needs a test
suite. Leave a standalone one-consumer service script in its role. Install by
copying the source tree and `pip install --no-index` into a venv; no artifact
registry for a first version. Roles then render config and install a package;
the `Environment=` lines in the units do not change. The scripts read
configuration at import time (module-level `os.environ` reads); packaged code
must move those into a function. Packaging does not reduce volume; say so.

**Thanasis's MR !413, the details that matter.** His `update.yml` reads the
current index settings, computes with `set_fact` whether each differs from the
template, and applies only those: the model for step 7. Two blemishes not to
copy: his template PUTs carry `changed_when: true`, and his role names mix
`fluent_bit` with `infologger-client`. His in-pipeline processing is 200 lines
of Lua inside Fluent Bit, which is why the Python file list surprised Lubos.
`logstack` has four executable files, 579 lines, eleven commits.

**The wider target repository** shells out in about 50 places, including
`roles/logstash` running `firewall-cmd`. Our firewall handling is on the right
side of the line and stays. `roles/telegraf`, `roles/influxdbv2` and
`roles/logstash` are the monitoring estate Lubos meant. Read them before
step 1 so the diagram says where our platform stops and theirs begins.

**The durability argument for the bus.** Fluent Bit's filesystem buffer
already survives a restart, so durability alone does not justify a bus.
Decoupling does: one consumer moves instead of 200 collector configs.

**Things that look like back-compat and are not.** `trend_rollup_backfill_buckets: 3`
recomputes late buckets, normal streaming. The projector's identity test guards
a real invariant. Unconfirmed: the `_reindex` path in
`ensure_alert_actions_alias()` in `templates.sh.j2` moves an old concrete index
behind an alias and reads as back-compat; trace it in step 2 before deleting.

**How to argue the Python volume.** Defensible as necessarily custom: template
mining and the masker, the semantic model, signal projection, triage. Not
defensible: cluster and node metrics that duplicate Telegraf and Mimir, and the
patch fragments. Concede the second half at once. On upstream Alma 9
contribution: right in principle, unrealistic inside a 12-week first Ansible
implementation; say that plainly.

