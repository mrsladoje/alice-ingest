# `sweet_template_catalog`

The central maintenance of the template catalog. One oneshot unit on an hourly
timer, on the control host.

## Why it is not part of `sweet_collector`

It shares nothing with the collector but a subject. No socket, no host, no unit,
no file, no variable. Every index it touches — `template-catalog`,
`shifter-queries`, `template-buckets-1h-*`, and the two shared log indices it
reads — lives on the storage tier, so the job is a cluster API client and the
host it runs on is a scheduling choice, not a requirement.

It runs on the control host. On a worker it would put an hourly delete-by-query
on a machine whose job is ingesting, and it would put a mode into
`sweet_collector` that never touches Fluent Bit.

What it does share is a *contract*, not a role: it reads the definitions, the
bucket documents and the watermarks that `alice-stamper` publishes, through the
same `template_contract.py`. That module is shipped from
`group_vars/all.yml`'s `alice_shared_contract_file`, not out of another role's
`files/`, so the two roles stay independent.

`sweet_opensearch` owns the shape of these indices and their ISM retention. This
role owns what the documents mean, and every write to `template-catalog` that is
not a stamper publication.

## Why not ISM

ISM deletes whole indices by age. It cannot delete documents inside one. Three of
the four expiry sections here target documents in single, long-lived indices, and
`template-catalog` must stay one index because a definition is upserted by
version identifier and lives across months. The two counting checks are further
still from ISM: they aggregate, compare across indices, and write a result
document.

ISM already does the part it can. `sweet_opensearch`'s `ism.sh.j2` age-deletes
`template-buckets-5m-*` and `template-buckets-1h-*`, which is the large, growing
part of this data.

The central maintenance of the template catalog. One oneshot unit on a timer,
on one worker.

## What it does

The worker half of the old catalog producer is gone. Every record is stamped
in-band by `roles/sweet_collector`, which also publishes the template
definitions, the exact bucket counts and the per-node watermarks. See
`docs/TEMPLATES_FIX_PLAN.md`. This role only expires and audits what that
produces:

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

## Variables the role requires but does not own

| Variable | Owner | Used for |
|---|---|---|
| `template_catalog_index`, `template_buckets_1h_prefix`, `shifter_queries_index` | `group_vars/all.yml` | The catalog, the hourly bucket pattern and the query history. |
| `alice_shared_dir`, `alice_shared_contract_file` | `group_vars/all.yml` | The shared contract module. |
| `template_catalog_definition_retention_days` | `group_vars/all.yml` | The 90-day definition retention. |
| `opensearch_http_port` | `group_vars/all.yml` | The local cluster endpoint. |

## Tests

`files/test_catalog_maintenance.py` drives the pass against a fake cluster:
the check window, conservation, stamped-against-indexed in both directions, an
unreadable index, a partial listing, the bucket ceiling, the three expiry
sections and their clocks, and the state file.
