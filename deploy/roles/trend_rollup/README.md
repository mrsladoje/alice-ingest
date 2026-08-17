# `trend_rollup`

Installs and runs `alice-trend-rollup`, the service that turns raw log indices
into 10-minute per-entity aggregate rows in the `trend-rollup` index. Twelve
alerting monitors read those rows instead of the raw indices, because a monitor
that aggregates over a full day of raw logs on every run is a monitor that
times out.

The role copies one Python file, writes one systemd unit, starts it, and then
asserts that the service is still active. It does nothing else.

It is a separate role because it is a separate play. `site.yml` deploys it on
`hosts: background`, not on the control host, and only when the host's
`background_services` list contains `rollup`. In the source tree it was reached
through `include_role` with `tasks_from: rollup.yml`, which is the shape a role
replaces.

## What it does

```
                        HOSTS IN GROUP background

┌─ 1. THE SCRIPT ────────────────────────────────────────────────────────────┐
│  trend_rollup.py -> /opt/alice-ingest/trend_rollup.py   0755 root:root      │
│                                                         --> restart        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. THE UNIT ──────────────────────────────────────────────────────────────┐
│  /etc/systemd/system/alice-trend-rollup.service          0644 root:root     │
│  DynamicUser, ProtectSystem=strict, MemoryHigh/MemoryMax                    │
│  every tuning value is passed as an Environment= line    --> restart        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. START, then PROVE ─────────────────────────────────────────────────────┐
│  systemd enable + start + daemon_reload                                     │
│  flush_handlers          applies a pending restart before the assertion     │
│  ActiveState == active   6 attempts, 5 s apart — 30 seconds                 │
└────────────────────────────────────────────────────────────────────────────┘
```

What the running service does, once a bucket: it pages every entity in each of
the log families, writes one row per entity plus one `_meta` row per cohort,
imputes a zero row for an entity that logged recently but not in this bucket,
writes a commit document for the bucket, and deletes rows older than the
retention window.

## Non-obvious settings

- **The handlers are flushed before the assertion.** Without
  `meta: flush_handlers`, the final task would read the state of the old
  process, because handlers otherwise run at the end of the play. The assertion
  would then pass on a service that the new unit file is about to kill.
- **The service asserts, and the whole trend lane depends on it.** Twelve
  monitors read `trend-rollup`. A rollup that dies leaves those monitors
  querying an index that stops growing, which reads as "nothing is wrong"
  rather than as an outage. That is why a dead service fails the deploy here.
- **`trend_rollup_bucket_seconds` is written into the unit twice.** It is both
  `BUCKET_SECONDS`, the width of an aggregate row, and `INTERVAL`, the sleep
  between loops. One number, two meanings, on purpose: a pass that runs once
  per bucket width writes each bucket once. A faster loop only rewrites the
  same rows, and a slower one leans on `trend_rollup_backfill_buckets` to
  catch up.
- **Retention is enforced by the script, not by an ISM policy.**
  `trend_rollup_retention_days` becomes a `_delete_by_query` over the `ts`
  field, run at most once an hour. `opensearch_bootstrap` creates the index and
  its mapping but attaches no lifecycle policy to it. Setting the value to 0 or
  less turns pruning off, and the index then grows without bound.
- **Two of the script's knobs are deliberately not in the unit.**
  `PRUNE_EVERY_SECONDS` (3600) and `SILENCE_MEMORY_SECONDS` (86400) keep the
  script's own defaults. They have no Ansible variable, so changing them means
  editing `trend_rollup.py`. `SILENCE_MEMORY_SECONDS` is the window that
  decides whether an absent entity still counts as rostered, which is what
  bounds how long a decommissioned host keeps receiving imputed zero rows.
- **`OS_URL` is `localhost`.** The service queries the OpenSearch node on its
  own machine, not a cluster address. See prerequisites.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `dashboards_trend_rollup_script` | `/opt/alice-ingest/trend_rollup.py` | Where the script is installed and what `ExecStart` runs. |
| `trend_rollup_bucket_seconds` | `600` | Width of one aggregate row, in seconds. Also the loop period. See non-obvious settings. |
| `trend_rollup_settle_seconds` | `120` | How far behind the clock the service stays, so that late-arriving documents land before their bucket is rolled. |
| `trend_rollup_backfill_buckets` | `3` | How many earlier buckets are re-rolled on each pass, to repair a bucket the service missed while it was down. |
| `trend_rollup_page_size` | `500` | Composite-aggregation page size when it walks the entities of one cohort. |
| `trend_rollup_max_entities` | `2000` | Hard cap on entities per cohort per bucket. Past the cap the bucket is marked truncated rather than growing unbounded. |
| `trend_rollup_retention_days` | `30` | Rows older than this are deleted. 0 or less disables pruning. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `dashboards_trend_rollup_service_name` | `group_vars/all.yml` | The unit name. `playbooks/status.yml` and the `site.yml` pre-flight both name the same service. |
| `trend_rollup_index` | `group_vars/all.yml` | The index written. `opensearch_bootstrap` creates it and its mapping, and `playbooks/replay.yml` wipes it. |
| `opensearch_http_port` | `group_vars/all.yml` | Builds `OS_URL`. Every service in the tree reads it. |
| `alice_service_memory_high` | `group_vars/all.yml` | `MemoryHigh` on the unit. Shared by all the thin Python services. |
| `alice_service_memory_max` | `group_vars/all.yml` | `MemoryMax` on the unit. Same. |
| `background_services` | `inventory.yml`, per host | Not read by the role. The `site.yml` play uses it to decide whether to run the role at all. |

`trend_entity_cap_warn`, `trend_lag_floor_ms`, `trend_entry_lag_ceiling_ms`,
`trend_min_slice_docs`, `trend_min_slice_errors` and `trend_min_lag_docs` share
the `trend_` prefix but belong to `alerting_monitors`. They are thresholds
inside the monitor definitions, not settings of this service. Nothing here
reads them.

## Prerequisites

The role does not bootstrap the machine or the index. Three things must be true
first, all satisfied by the play order in `playbooks/site.yml`.

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `/opt/alice-ingest` exists on the background host | `alice_runtime` | The copy of the script fails. `ansible.builtin.copy` does not create a missing parent directory. |
| An OpenSearch node answers on `localhost:9200` on this host | `opensearch` | The service starts, fails every query, and `Restart=on-failure` cycles it. The assertion at the end of the role then fails the deploy. |
| The `trend-rollup` index and its mapping exist | `opensearch_bootstrap` | Rows land in a dynamically mapped index. The bucket-commit and silence-imputation fields get the wrong types, and the trend monitors read them wrong. |

`alerting_monitors` is a consumer, not a prerequisite. It may run before or
after this role; its monitors simply return nothing until the first bucket is
written.

## How to use it

In a playbook, against the background group:

```yaml
- name: Background analytics services
  hosts: background
  become: true
  roles:
    - role: trend_rollup
      when: "'rollup' in background_services"
```

- **Keep the `when`.** The `background` group is the place for services moved
  off the crowded control VM. `background_services` is what says which of them
  this particular host carries. Dropping the guard starts a rollup on every
  background host, and two rollups writing the same bucket race on the same
  document identifiers.
- **The role is idempotent.** It restarts `alice-trend-rollup` only when the
  script or the unit file changed.
- **One rollup per cluster.** Nothing in the role enforces that. It is an
  inventory decision.

## Couplings

- **`trend_rollup_index` is shared with three other places.**
  `opensearch_bootstrap` creates the index template, the index and the live
  mapping under that name; `reset_derived.py` clears it during
  `playbooks/replay.yml`; the trend monitors query it. Change it in
  `group_vars/all.yml`, never here.
- **`trend_rollup_bucket_seconds` and the monitors' window sizes move
  together.** A monitor that looks back a fixed number of minutes assumes a
  bucket width. Widening the bucket without widening the window leaves the
  monitor with too few rows to clear its minimum-row guards, and it silently
  stops firing.
- **`trend_rollup_max_entities` and `trend_entity_cap_warn` are a pair.** The
  second lives in `alerting_monitors` and is set below the first, so the
  `trend-entity-cap` monitor warns before the rollup starts truncating.
  Raising the cap without raising the warning threshold makes the monitor fire
  constantly; lowering the cap below the warning makes it never fire at all.
- **`trend_rollup_settle_seconds` and the collector's flush interval move
  together.** The settle window exists to cover the delay between a log line
  being written and it being searchable. A slower collector needs a longer
  settle, or the bucket is rolled before its documents arrive.
- **`test_trend_rollup.py` reads two JSON files from `alerting_monitors`.** It
  asserts that the shipped `trend-il-volume` and `log-family-silence` monitor
  conditions still match the silence contract this script implements. The path
  is `../alerting_monitors/files/monitors`. Moving either role's directory
  breaks the test, and the test is the only thing tying the imputation logic to
  the monitors that depend on it.

## Upstream roles rejected

Recorded so the question is not reopened at review time. Checked in August
2026.

| Candidate | Type | Would replace | Why rejected |
|---|---|---|---|
| Any Galaxy "systemd service" role | Third-party | The unit template and the enable/start pair | Three tasks of generic work around one bespoke Python program. A generic wrapper would hide the environment block, which is where every setting in this role actually lives. |
| OpenSearch rollup jobs (Index Management) | Upstream feature | The whole service | The nearest real alternative, and it was considered. It cannot express the part that matters: imputing a zero row for an entity that fell silent while its cohort kept logging. A native rollup writes nothing for an absent entity, which is exactly the ambiguity `impute_silent` exists to remove. It also cannot write the `_meta` cohort row that `log-family-silence` buckets on. |

**Re-open this decision** if OpenSearch rollup jobs gain a way to emit a row for
a source bucket that produced no documents.

## Used by

- `playbooks/site.yml`, play "Background analytics services (off the control
  host, distributed across storage nodes)", against `background` — the only
  caller.
