# `template_catalog`

The central maintenance of the template catalog. One oneshot unit on a timer,
on one worker.

## What it does

The worker half of this role is gone. Every record is now stamped in-band by
`roles/stamper`, which also publishes the template definitions, the exact
bucket counts and the per-node watermarks. See `docs/TEMPLATES_FIX_PLAN.md`.

What stays here runs on exactly one host, named by
`template_catalog_maintenance_host` in `group_vars/all.yml`:

- **Definition expiry.** A template definition is deleted 90 days after its
  last observation (`kind: template`, by `last_observed`).
- **Check expiry.** Check results are deleted after the hourly bucket
  retention (`kind: check`, by `checked_at`).
- **Query-history expiry.** `shifter-queries` documents older than one year
  are deleted. The unit runs hourly; this section skips a pass until 24 hours
  have passed since its last clean run.
- **The two counting checks** (plan section 4). Both read the hourly bucket
  documents of the last completed hour behind a one-hour lag.
  - *Conservation.* For every bucket document, the nested counts must sum to
    the total. A mismatch is a ledger bug.
  - *Stamped against indexed.* A terms aggregation on `template_version` over
    each shared index (`application-logs-central`, `infologger`), scoped to the
    node and the hour, must be at or below the stamped count for every
    version. The worker-local index is checked on the worker by the stamper
    itself, once an hour, with the same document shape.

  Failing conservation checks and every stamped-against-indexed result are
  written into `template-catalog` as `kind: check`
  (`contract.check_document`), so the Shifter can show them. The
  `template-count-check` monitor fires on any `ok: false` check in the last
  two hours.

## Every pass publishes what it did

Each pass writes three documents into `template-catalog` at the fixed
identifiers `maintenance:catalog`, `maintenance:queries` and
`maintenance:checks`, `kind: catalog_maintenance`. Each carries the age of that
section's last clean run, what it deleted or found, its failure count, and the
whole section report under `detail`.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `template_catalog_maintenance_calendar` | `hourly` | `OnCalendar` on the timer. |
| `template_catalog_maintenance_memory_max` | `256M` | `MemoryMax` on the unit. |
| `template_catalog_maintenance_page` | `1000` | Documents per delete-by-query batch. |
| `template_catalog_maintenance_requests_per_second` | `500` | The delete-by-query throttle. |
| `template_catalog_maintenance_max_docs` | `50000` | Documents one pass may delete per section. |
| `template_catalog_maintenance_timeout` | `300` | Deadline on one request. |
| `template_catalog_query_retention_days` | `365` | Expiry of `shifter-queries` by `issued_at`. |
| `template_catalog_query_cleanup_interval_hours` | `24` | The query section's own clock. |
| `template_catalog_check_retention_days` | `35` | Expiry of `kind: check` documents. |
| `template_catalog_shared_indices` | `application-logs-central,infologger` | The indices the central stamped-against-indexed check reads. |
| `template_catalog_check_hours` | `1` | Completed hours checked per pass. |
| `template_catalog_check_lag_hours` | `1` | Hours behind the current hour the checked window ends. |
| `template_catalog_check_page` | `200` | Bucket documents per listing page. |
| `template_catalog_check_max_buckets` | `5000` | Bucket documents one pass may check. |

### Variables the role requires but does not own

| Variable | Owner | Used for |
|---|---|---|
| `template_catalog_index`, `template_buckets_1h_prefix`, `shifter_queries_index` | `group_vars/all.yml` | The catalog, the hourly bucket pattern and the query history. |
| `alice_shared_dir`, `alice_shared_contract_file` | `group_vars/all.yml` | The shared contract module. |
| `template_catalog_maintenance_host` | `group_vars/all.yml` | Names an inventory group, so it cannot be a role default. |
| `template_catalog_definition_retention_days` | `group_vars/all.yml` | The 90-day definition retention. |
| `opensearch_http_port` | `group_vars/all.yml` | The local cluster endpoint. |

## Tests

`files/test_catalog_maintenance.py` drives the pass against a fake cluster:
the check window, conservation, stamped-against-indexed in both directions, an
unreadable index, a partial listing, the bucket ceiling, the three expiry
sections and their clocks, and the state file.
