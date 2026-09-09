# Ansible Role: loggy_trend_rollup

Runs `alice-trend-rollup`, the service that turns the raw log indices into one
aggregate row per entity every ten minutes. Each pass reads the InfoLogger,
central and local log indices from the storage tier, counts records and errors
per host and per collector node, takes the p95 and the mean of both ingest
lags, writes one row per entity and one `_meta` row per cohort into
`trend-rollup`, adds a zero row for a host that logged within the last day but
not in this bucket, and closes the bucket with a commit document. Once an hour
it deletes rows older than the retention window.

Twelve alerting monitors read those rows instead of the raw indices, and the
zero rows are what let them tell one silent host from a whole log family gone
quiet.

## How it works

The service runs on one host of the `background` group, next to a storage node,
and queries that node over localhost. On the farm that host is the infra
machine holding the three storage containers; staging runs the same layout on
fewer machines. Everything it writes lands in `trend-rollup`, one shard with
two replicas on the storage tier, so the rows outlive the machine that wrote
them.

```
                    ONE PASS, EVERY trend_rollup_bucket_seconds

┌─ PRUNE, once an hour ───────────────────────────────────────────────────────┐
│  _delete_by_query on trend-rollup, ts older than trend_rollup_retention_days│
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ PICK THE BUCKETS ──────────────────────────────────────────────────────────┐
│  the last closed bucket, trend_rollup_settle_seconds behind the clock,      │
│  and the trend_rollup_backfill_buckets - 1 before it, oldest first          │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v   for each bucket
┌─ COLLECT, one cohort at a time ─────────────────────────────────────────────┐
│  [infologger / host]   infologger                by origin_host             │
│  [infologger / node]   infologger                by node                    │
│  [central / host]      application-logs-central  by origin_host             │
│  [local / host]        application-logs-local-*  by origin_host             │
│  [local / node]        application-logs-local-*  by node                    │
│      composite terms over collector_time in the bucket, paged by            │
│      trend_rollup_page_size; per entity: doc_count, ef_count (error,        │
│      fatal), p95 and avg of enter_system_lag_ms and ingest_lag_ms           │
│      past trend_rollup_max_entities the cohort stops and is truncated       │
│  one failed cohort query abandons the bucket: no rows, no _meta, no commit  │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ IMPUTE SILENCE ────────────────────────────────────────────────────────────┐
│  roster = entities of this cohort with doc_count > 0 in trend-rollup        │
│           over the last 24 h; imputed zeros never count                     │
│  every rostered entity absent from this bucket --> a row with doc_count 0,  │
│           imputed: true, carrying the live fleet_count                      │
│  a cohort that collected nothing imputes nothing: that is the family        │
│           stopping, and log-family-silence reports it once                  │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      v
┌─ WRITE, then COMMIT ────────────────────────────────────────────────────────┐
│  _bulk    one row per entity        _id family.kind.entity.ts               │
│           one _meta row per cohort  entity_count, fleet_count, truncated,   │
│                                     imputed_count, cohort_family/kind       │
│  refresh trend-rollup, then one _commit row for the bucket:                 │
│           complete, truncated, observed against expected cohorts            │
│  a bulk failure or a failed refresh publishes no commit                     │
│                                                                             │
│  --> trend-rollup, storage tier, replicated                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**The bucket width is also the loop period.** `trend_rollup_bucket_seconds`
reaches the service twice, as the width of a row and as the sleep between
passes, so each bucket is written once and rewritten only by the backfill.

**Every row identifier is deterministic.** Rolling a bucket again overwrites
the same documents, so a restart or a backfill never duplicates a row.

**Retention runs in the script.** `loggy_opensearch` creates the index and its
mapping and attaches no lifecycle policy. A retention of 0 or less turns
pruning off, and the index grows without bound.

**The deploy asserts the service.** A changed script or unit restarts the
service before the last task, which polls `ActiveState` six times, five
seconds apart, and fails the play if the service is not active.

## Why not an upstream role

The role is three tasks around one Python file, and every setting lives in the
unit's environment block. An upstream role would hide that block.

| Candidate | Would replace | Why rejected |
|---|---|---|
| A Galaxy systemd role, e.g. [0x0I/ansible-role-systemd](https://github.com/0x0I/ansible-role-systemd) | The unit template and the enable/start pair | Takes a dictionary of unit options and hides the environment block, which is the whole configuration of this service. |
| [OpenSearch rollup jobs](https://opensearch.org/docs/latest/im-plugin/index-rollups/) (Index Management) | The whole service | A native rollup writes nothing for an entity that produced no documents, which is the case the zero rows exist to mark. It cannot write the `_meta` and `_commit` rows either. |

## Requirements

`loggy_opensearch` must have installed a node
on the same host, which the service queries over localhost, and must have
configured the cluster, which creates `trend-rollup` with the mapping the
commit and imputation fields need. `loggy_anomaly_detection` is a consumer,
not a requirement; its monitors return nothing until the first bucket is
committed. Run the role on exactly one host. Nothing stops two copies from
racing on the same document identifiers.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is the script
path.

```yaml
trend_rollup_bucket_seconds: 600
trend_rollup_settle_seconds: 120
trend_rollup_backfill_buckets: 3
```

The settle window is how far behind the clock the newest bucket stays, so that
late documents are searchable before their bucket is rolled; a slower
collector flush needs a longer settle. The monitors' look-back windows assume
the ten-minute width, so a wider bucket without wider windows leaves them
under their minimum-row guards.

```yaml
trend_rollup_page_size: 500
trend_rollup_max_entities: 2000
```

Past the cap a cohort is marked truncated and the bucket's commit is
incomplete. `trend_entity_cap_warn` in `loggy_anomaly_detection` sits below
the cap so that `trend-entity-cap` warns before truncation starts; move the
two together.

```yaml
trend_rollup_retention_days: 30
```

Rows older than this are deleted once an hour. The prune period and the 24 h
silence roster are the script's own defaults and have no variable.

From `group_vars`: `trend_rollup_service_name`, `trend_rollup_index`,
`opensearch_http_port`, `alice_service_memory_high`,
`alice_service_memory_max`. From the inventory: `background_services`, which
the play reads to decide whether the role runs on a host.

## Example Playbook

```yaml
- hosts: background
  become: true
  roles:
    - role: loggy_trend_rollup
      when: "'rollup' in background_services"
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
