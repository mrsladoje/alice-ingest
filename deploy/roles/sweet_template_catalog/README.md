# `sweet_template_catalog`

Housekeeping for the template catalog on the ALICE EPN farm. It runs on the
control host as one oneshot service, `alice-catalog-maintenance`, on an hourly
timer. Each pass expires template definitions not observed for 90 days, expires
check results older than the hourly buckets they came from, once a day expires
Shifter query history older than a year, and then audits the last completed
hour: every hourly bucket document the workers published must add up, and the
number of records indexed per template version in the two replicated log
indices must not exceed the number the stamper reported. Every failed check and
a report of the pass itself go back into `template-catalog` as documents the
Shifter shows and the `template-count-check` monitor fires on.

Nothing here is worker-local. The pass reads and writes only indices on the
storage tier, through the OpenSearch node on the control host, so the host it
runs on is a scheduling choice. A template whose definition expired here is
catalogued afresh when a worker stamps it again, so `first_catalogued` is the
farm's record of a new template and the `template-new` monitor fires on it.
Staging runs the same layout on fewer machines.

## How it works

```
                          CONTROL HOST, once an hour

┌─ STATE ─────────────────────────────────────────────────────────────────────┐
│  /var/lib/alice-catalog-maintenance/maintenance-state.json                  │
│      the time of each section's last clean run; a missing file means never  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ EXPIRE DEFINITIONS  (delete-by-query on template-catalog) ─────────────────┐
│  kind: template, last_observed <= now - 90 days                             │
│      1000 documents per batch, 500 requests/s, at most 50000 per pass       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ EXPIRE CHECK RESULTS  (delete-by-query on template-catalog) ───────────────┐
│  kind: check, checked_at <= now - 35 days                                   │
│      skipped when the definition expiry above failed                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ EXPIRE QUERY HISTORY  (delete-by-query on shifter-queries) ────────────────┐
│  kind: shifter_query, issued_at <= now - 365 days                           │
│      on its own clock: runs only 24 h after its last clean run              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ LIST BUCKETS  (search on template-buckets-1h-*) ───────────────────────────┐
│  kind: bucket, the last completed hour, one hour behind the clock           │
│      200 documents per page, at most 5000 per pass; more is reported        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ CONSERVATION  (one check per bucket document) ─────────────────────────────┐
│  the per-version counts must sum to the bucket's total                      │
│      a bucket that fails here is not compared against the index             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ STAMPED AGAINST INDEXED  (terms aggregation, one per bucket and index) ────┐
│  application-logs-central, infologger                                       │
│      indexed records per template_version, scoped to the bucket's node,     │
│      family and hour, must be <= the stamped count for every version        │
│      the worker-local index is checked on the worker by alice-stamper       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌─ PUBLISH  (bulk into template-catalog) ─────────────────────────────────────┐
│  failed conservation checks and every stamped check  --> kind: check        │
│  maintenance:catalog, maintenance:queries,                                  │
│  maintenance:checks, one report each                 --> kind:              │
│                                                          catalog_maintenance│
└─────────────────────────────────────────────────────────────────────────────┘
```

- **A section that fails does not advance its clock.** The state file records
  a section's last clean run, and the report carries that age, so a section
  that keeps failing shows as an ever older run.
- **Every delete is bounded.** A pass deletes at most
  `template_catalog_maintenance_max_docs` per section and marks the report
  `bounded`; the rest waits for the next hour.
- **The service exits non-zero on any failure** and prints the whole report as
  one JSON line on stdout, so `journalctl -u alice-catalog-maintenance` is the
  full history.
- **The unit is idle-priority and memory-capped**, with `Nice=10`,
  `IOSchedulingClass=idle` and `MemoryMax`, so it never competes with the
  OpenSearch node beside it.

## Why not an upstream role

No Ansible role does this job: the work is a Python client of the cluster API
that the role ships and schedules. OpenSearch's own retention was checked and
rejected for these documents:

| Alternative | Why rejected |
|---|---|
| [Index State Management](https://docs.opensearch.org/latest/im-plugin/ism/index/) | Deletes whole indices by age. `template-catalog` and `shifter-queries` are single long-lived indices whose documents expire one by one. |
| [Rollover aliases](https://docs.opensearch.org/latest/im-plugin/ism/policies/#rollover) | A definition is upserted by version identifier across months; splitting the catalog by date would duplicate it. |

ISM does the part it can: `sweet_opensearch` age-deletes the
`template-buckets-5m-*` and `template-buckets-1h-*` indices, which are the
large, growing part of this data.

## Requirements

`sweet_opensearch` must have run in both its modes first: install on the
control host, so an OpenSearch node answers on `localhost`, and configure the
cluster, so `template-catalog`, `shifter-queries` and the bucket indices exist
with their mappings and retention. `sweet_collector` must run on the workers,
because a pass audits the bucket documents the stamper publishes. The role
copies `template_contract.py` from `alice_shared_contract_file` into
`alice_shared_dir` itself; the stamper and this job read the same module, so
they agree on every field name.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
template_catalog_maintenance_calendar: "hourly"
template_catalog_maintenance_interval: "1h"
template_catalog_maintenance_memory_max: "256M"
```

The calendar is the timer's `OnCalendar`; the interval only names the timer in
its description. The checks read one hour per pass, so a slower calendar
leaves hours unchecked.

```yaml
template_catalog_maintenance_page: 1000
template_catalog_maintenance_requests_per_second: 500
template_catalog_maintenance_max_docs: 50000
template_catalog_maintenance_timeout: 300
template_catalog_maintenance_bulk_documents: 500
```

The delete-by-query batch, throttle, per-section ceiling and request deadline
in seconds. The last value is how many check results one bulk request carries.

```yaml
template_catalog_query_retention_days: 365
template_catalog_query_cleanup_interval_hours: 24
template_catalog_check_retention_days: 35
```

The query section has its own clock and skips a pass until the interval has
passed since its last clean run. Check retention matches the hourly bucket
retention, so a check outlives the bucket it judged by no more than the ISM
delay.

```yaml
template_catalog_shared_indices: "application-logs-central,infologger"
template_catalog_check_hours: 1
template_catalog_check_lag_hours: 1
template_catalog_check_page: 200
template_catalog_check_max_buckets: 5000
```

The indices the stamped-against-indexed check reads, how many completed hours
one pass checks, and how far behind the current hour the window ends. The lag
gives every worker's last publication of a bucket time to land before it is
compared.

From `group_vars`: `template_catalog_index`, `template_buckets_1h_prefix`,
`shifter_queries_index`, `template_catalog_definition_retention_days`,
`alice_shared_dir`, `alice_shared_contract_file`, `opensearch_http_port`.

## Example Playbook

```yaml
- hosts: control
  become: true
  roles:
    - sweet_template_catalog
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
