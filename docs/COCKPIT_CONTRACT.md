# The Templates page contract

**Author:** Marko Sladojevic
**Date:** 8 September 2026
**Status:** the field names are fixed here. Load acceptance remains pending.
**Implements:** `docs/SHIFTER_COCKPIT_PLAN.md`, sections 1, 4, 5 and 6.

## Conclusion

Every name the Templates page needs is written down once, in this file.

The shared module `deploy/shared/template_contract.py` holds the executable half.
It carries identity, bucket arithmetic, timestamp rules, integer transport, and
the two published document shapes. The worker and the Shifter server both import
it. No later stage may restate one of its rules.

This file holds the half that code cannot carry: the index mappings, the shard
budget, and the HTTP shapes the browser reads.

If this document and the shared module disagree, the module is correct and this
document is a defect.

## 1. Where the shared module lives

The repository path is `deploy/shared/template_contract.py`.

Ansible stages it to `/opt/alice-ingest/shared/template_contract.py` on every
worker and on the Shifter host. Both services read the directory from the
environment variable `ALICE_SHARED_PATH`, which defaults to
`/opt/alice-ingest/shared`. This mirrors `ALICE_TEMPLATING_PATH` in the template
catalog role.

The module imports the standard library only. It does not import drain3, does
not read a file, and does not read the clock. Every function that needs the time
takes it as an argument.

Pin drain3 to version 0.9.11 wherever the version is expressed.

## 2. Identity

There are two identifiers and they answer different questions.

**The version identifier** answers "which exact masked text is this?". It is
`version_id(family, template)`. It is the SHA-1 of `family`, a newline, and the
exact masked template text, cut to 24 hexadecimal characters, behind the family
and a colon. Example: `dpl:e5cad426a5acaa0b4610f900`.

The version identifier is also the document identifier in `template-catalog`.

**The canonical identifier** answers "which event meaning is this?". It is
`canonical_id(template)`, which is `digest(normalize(template))`. Normalization
lowercases the text, replaces every mask with ` <*> `, collapses whitespace, and
strips edge punctuation. The digest is the SHA-256 of the normalized text, cut
to 16 hexadecimal characters. Example: `f14be5aac03b59f8`.

Both functions came from shipped code and their output did not change.
`normalize` and `digest` came from `tools/embed/freeze.py`. `version_id` is the
former `template_id` in `deploy/roles/template_catalog/files/template_catalog.py`.
`deploy/shared/test_template_contract.py` extracts both originals from their
source files and compares them against the shared module.

Normalization removes the difference between `<NUM>`, `<FLOAT>` and `<*>`.
A shared canonical identifier is therefore not proof of unchanged reviewed
meaning. Never inherit a label, a statistic, or an alert suppression from a
canonical identifier alone.

A count belongs to a version, never to a canonical group. A group total is the
sum of the versions the response lists, and nothing else.

## 3. Time

The observation clock is the record's `collector_time`. The collector writes it
as epoch milliseconds. Every time in this contract is epoch milliseconds in
Coordinated Universal Time.

`parse_collector_time(value)` returns `ObservationTime(status, epoch_ms)`.
The four statuses are `ok`, `missing`, `invalid` and `out_of_tolerance`. The
last three carry no substituted clock value: `missing` and `invalid` carry
`None`. A record with any of the three must raise a coverage gap. Use
`gap_for_time(status)` to name the gap.

`observation_time(record, now_ms, tolerance_ms)` adds the tolerance test. The
default tolerance is 300000 milliseconds, which is five minutes. That value is a
proposal for validation, not a measured limit.

A bucket is 600 seconds. The retained window is 1008 buckets, which is seven
days. `window_end` must sit on a ten-minute boundary; `require_window_end`
refuses anything else. The window includes its start and excludes its end.

`completed_window_end(now_ms)` names the cutoff. It floors the given time to the
boundary below it, so the current open bucket is always outside the total.
`completed_window(now_ms)` returns both endpoints, their display strings, and the
open bucket's own bounds.

`next_window_end(now_ms, last_window_end_ms)` raises `ClockFault` when the clock
moved backwards past a published cutoff. Report the fault. Never move a
published cutoff backwards.

Display strings use `iso_utc`, which always writes milliseconds:
`2026-09-08T14:20:00.000Z`.

## 4. Counts across the browser boundary

`encode_int(value)` returns a JSON number when the value is between
-9007199254740991 and 9007199254740991. Above that range it returns a decimal
string. `decode_int(value)` reads both forms and returns an exact Python
integer. `sum_counts(entries)` adds the `count` of each entry exactly.

The browser must not assume a number. Add one helper to
`deploy/roles/shifter/files/live/shifter.js` and call it everywhere a count is
read:

```js
function decodeCount(value) {
  if (typeof value === "string") return BigInt(value);
  if (Number.isSafeInteger(value)) return BigInt(value);
  throw new Error("a count arrived as an unsafe number: " + value);
}
```

Format a count for display from the decoded value. Never add two counts as
JavaScript numbers. Never round an accepted count.

## 5. Coverage

The vocabulary has four words and no others: `complete`, `partial`, `unknown`,
`unavailable`.

A complete manifest with zero entries establishes zero for that producer. A
missing manifest establishes `unknown` and must never become a zero
contribution. `producer_coverage(manifest)` returns `unknown` for `None`.
`zero_is_established(status)` is true only for `complete`.

`fleet_coverage(expected_producer_ids, manifests)` returns the fleet status with
four lists: `contributing`, `missing`, `partial` and `unexpected`. The status is
`complete` when every expected producer published complete coverage. It is
`unknown` when no expected producer published at all. Otherwise it is `partial`.

`select_window(latest_by_producer)` picks one cutoff for the fleet. It selects
the newest `window_end` and reports every producer whose newest manifest names
an older cutoff as stale. Never add a producer's 14:10 total to another
producer's 14:20 total.

The gap reasons are fixed constants: `missing_observation_time`,
`invalid_observation_time`, `observation_time_beyond_tolerance`,
`missing_record_identifier`, `source_document_overwritten`,
`source_records_deleted_before_read`, `source_scan_incomplete`,
`admission_limit_reached`, `state_limit_reached`, `backfill_incomplete`,
`ownership_unassigned`, `central_history_lost`.

A manifest that reports `complete` may not carry a gap. The constructor refuses
that combination.

## 6. Published documents

Two kinds live in `template-metrics`: `snapshot_chunk` and `snapshot_manifest`.
A third kind, `watched_history`, is reserved for the later release and is not
written now.

### 6.1 Document identifiers

```
chunk:<producer_id>:<incarnation>:<window_end>:<generation>:<chunk_number>
manifest:<producer_id>:<incarnation>:<window_end>:<generation>
```

The identifier carries the generation, so a retry replaces the same document
with the same bytes and applies no increment twice. A new generation writes new
identifiers, so the previous committed generation stays readable until its
replacement is complete.

`producer_id` and `incarnation` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
A colon in either is refused.

### 6.2 Chunk entry

One entry per counted version. Every field is present on every entry.

| Field | Type | Meaning |
| --- | --- | --- |
| `version_id` | string | Identity of the exact masked text. Derived, never accepted. |
| `canonical_id` | string | Identity of the normalized text. Derived, never accepted. |
| `family` | string | Mining family. |
| `template` | string | Exact masked template text. |
| `normalized` | string | Canonical normalization of that text. |
| `count` | integer or decimal string | Exact record count in the completed window. |
| `first_observed` | integer | Earliest retained observation, at or after `window_start`. |
| `last_observed` | integer | Latest accepted observation, including the open bucket. |
| `programs` | string array | Sorted, at most 32. |
| `origin_hosts` | string array | Sorted, at most 32. |
| `log_sources` | string array | Sorted, at most 8. |
| `severity_norm` | string or null | Normalized severity for display. |
| `superseded_by` | string or null | The version identifier this text widened into. |
| `open_bucket_observed` | boolean | True when the open bucket holds an observation. |
| `open_bucket_last_observed` | integer or null | Latest open-bucket observation. |
| `scope_truncated` | boolean | True when a scope list was capped. |

An entry carries every field a counted row needs, with one stated exception:
`first_catalogued` is not in the entry. It lives only in the catalog, it is
informational, and no count or coverage state reads it. List rows leave it
`null`. The detail drawer fills it with one bounded catalog lookup under the
detail-query limit, cached until the next refresh replaces the view. Nothing
else a row displays depends on a catalog lookup.

A zero count is legal only with `open_bucket_observed` true. That entry is a
template observed after the cutoff. The page shows it as active and awaiting the
next volume update.

A positive count requires `first_observed` inside the window. `validate_entry`
enforces both rules.

Scope caps never cut the count. They set `scope_truncated`.

### 6.3 Chunk document

```json
{
  "kind": "snapshot_chunk",
  "schema_version": 1,
  "chunk_id": "chunk:epn146:b3f1c0de:1788877200000:3:0",
  "producer_id": "epn146",
  "incarnation": "b3f1c0de",
  "generation": 3,
  "chunk_number": 0,
  "chunk_count": 2,
  "window_start": 1788272400000,
  "window_end": 1788877200000,
  "bucket_seconds": 600,
  "buckets": 1008,
  "entry_count": 500,
  "entries": [],
  "entries_checksum": "0f5a...",
  "encoded_bytes": 612344,
  "published_at": 1788877230000
}
```

A chunk holds at least one entry and at most 500. Its whole encoded document
holds at most 1000000 bytes. `split_entries` splits by both ceilings and raises
`OversizedEntry` for a single entry that fits in no chunk. One entry holds at
most 65536 bytes.

`entries_checksum` is `checksum(entries)`: the SHA-256 of the canonical JSON of
the entry list, cut to 32 hexadecimal characters. Canonical JSON sorts keys and
uses no spaces, so the checksum is stable across processes and hosts.

### 6.4 Manifest document

```json
{
  "kind": "snapshot_manifest",
  "schema_version": 1,
  "manifest_id": "manifest:epn146:b3f1c0de:1788877200000:3",
  "producer_id": "epn146",
  "incarnation": "b3f1c0de",
  "generation": 3,
  "window_start": 1788272400000,
  "window_end": 1788877200000,
  "window_start_iso": "2026-09-01T14:20:00.000Z",
  "window_end_iso": "2026-09-08T14:20:00.000Z",
  "bucket_seconds": 600,
  "buckets": 1008,
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "chunk:epn146:b3f1c0de:1788877200000:3:0",
      "chunk_number": 0,
      "checksum": "0f5a...",
      "entry_count": 500,
      "encoded_bytes": 612344
    }
  ],
  "entry_count": 731,
  "total_count": 4211987,
  "source_checkpoints": [
    {
      "index": "application-logs-central",
      "index_uuid": "Xk8...",
      "shard": 0,
      "checkpoint": 918233
    }
  ],
  "expected_ownership": {
    "producer_id": "epn146",
    "node_id": "epn146.cern.ch",
    "routes": [
      {"index": "application-logs-local-epn146", "scoped_to_node": false},
      {"index": "application-logs-central", "scoped_to_node": true},
      {"index": "infologger", "scoped_to_node": true}
    ],
    "assumed_from": [],
    "retired": false
  },
  "coverage_status": "complete",
  "coverage_gaps": [],
  "counters": {
    "fetched_records": 0,
    "mined_records": 0,
    "counted_records": 0,
    "duplicate_records": 0,
    "expired_records": 0,
    "invalid_time_records": 0,
    "missing_time_records": 0,
    "out_of_tolerance_records": 0,
    "backlog_records": 0,
    "pass_duration_ms": 0,
    "peak_rss_bytes": 0,
    "ledger_versions": 0,
    "ledger_bytes": 0,
    "publication_failures": 0
  },
  "activity_snapshot_time": 1788877215000,
  "published_at": 1788877230000,
  "checksum": "9c1e..."
}
```

All fifteen counters are always present. `pass_counters` refuses an unknown
counter name and fills a missing one with zero.

`activity_snapshot_time` is the latest accepted observation the producer knows.
It is later than `window_end` in normal operation. The page shows it beside the
volume cutoff when the two differ.

`checksum` covers the whole manifest without the `checksum` field itself.

A manifest with `chunk_count` zero is a complete empty snapshot. It establishes
zero for that producer.

### 6.5 Reading a generation

`validate_generation(manifest, chunks)` returns the ordered chunks or raises.
It checks every chunk identifier, every checksum, every entry count, and the
published total against the sum of the entries.

The Shifter server calls it before it changes the shared view. It never mixes
one generation's chunks with another generation's manifest.

`obsolete_generations(generations, keep=2)` names the generations maintenance
may delete. The current and previous generations stay.

## 7. Index settings and mappings

Four indices carry this feature. Three are new.

| Index | Primary shards | Replicas | Shards |
| --- | --- | --- | --- |
| `template-catalog` | 1 | 2 | 3, unchanged |
| `template-metrics` | 1 | 2 | 3, new |
| `template-triage` | 1 | 2 | 3, new |
| `shifter-queries` | 1 | 0 | 1, new |

The three new indices add seven shards. The earlier 45-shard inventory is
historical. Count the real inventory and the real heap allocation before
deployment.

Every index below is fixed. None of them rolls over. Retention is document
expiry through a bounded delete-by-query, which follows the `cockpit-metrics`
and `trend-rollup` pattern already in `templates.sh.j2`.

### 7.1 `template-catalog`, changed

Activity now comes from observations, not from publication. The lifetime count
fields are gone from the page contract: `count`, `counts_by_node`, `last_pass`,
`first_seen`, `last_seen`, `last_seen_node`, `index`, `node` and
`collector_time` are removed from the mapping and from the writer.

The document identifier is the `version_id`.

```json
{
  "index_patterns": ["template-catalog"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 2,
      "codec": "zstd",
      "refresh_interval": "30s",
      "index.routing.allocation.require.role": "storage"
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "kind":                   { "type": "keyword" },
        "schema_version":         { "type": "integer" },
        "version_id":             { "type": "keyword" },
        "canonical_id":           { "type": "keyword" },
        "family":                 { "type": "keyword" },
        "template":               { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
        "normalized":             { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
        "programs":               { "type": "keyword" },
        "programs_truncated":     { "type": "boolean" },
        "origin_hosts":           { "type": "keyword" },
        "origin_hosts_truncated": { "type": "boolean" },
        "log_sources":            { "type": "keyword" },
        "severity_norm":          { "type": "keyword" },
        "nodes":                  { "type": "keyword" },
        "incarnations":           { "type": "keyword" },
        "first_observed":         { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "last_observed":          { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "first_catalogued":       { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "superseded_by":          { "type": "keyword" },
        "supersedes":             { "type": "keyword" },
        "relationship_verified":  { "type": "boolean" },
        "historical_programs":    { "type": "keyword" },
        "historical_origin_hosts":{ "type": "keyword" },
        "historical_scope":       { "type": "boolean" }
      }
    }
  },
  "_meta": {
    "family": "template-catalog",
    "written_by": "alice-template-catalog",
    "note": "One document per template version. Activity is the maximum accepted collector_time, never a publication time. The definition is retained for 90 days after its last observation; the default page view selects the seven-day active subset. There is no lifetime count here: volume comes from the selected template-metrics snapshot."
  }
}
```

Field rules:

- `last_observed` is the maximum of its current value and the accepted record's
  `collector_time`. An older delayed record must never move it backwards. A
  retry or a republication alone must never move it at all.
- `first_observed` is the minimum, kept for display only.
- `first_catalogued` is when this version first entered the catalog. It is
  informational and is not a first-release alert.
- `historical_programs`, `historical_origin_hosts` and `historical_scope`
  belong to an inactive definition. Show them with an explicit historical
  label. They are not current affected hosts or programs.
- `relationship_verified` is true only when the widening was observed on a
  worker and published. A guessed relationship stays false.
- Every write path must produce a complete document. A retirement operation may
  not create a document without template text and observation times.

Cleanup: one central task, hourly, delete-by-query on
`last_observed < now-90d`, throttled and bounded. Skip version conflicts and
reevaluate them next pass. Monitor cleanup age, deleted documents, failures,
and version conflicts.

### 7.2 `template-metrics`, new

```json
{
  "index_patterns": ["template-metrics"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 2,
      "codec": "zstd",
      "refresh_interval": "10s",
      "index.routing.allocation.require.role": "storage"
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "kind":                    { "type": "keyword" },
        "schema_version":          { "type": "integer" },
        "chunk_id":                { "type": "keyword" },
        "manifest_id":             { "type": "keyword" },
        "producer_id":             { "type": "keyword" },
        "incarnation":             { "type": "keyword" },
        "generation":              { "type": "long" },
        "chunk_number":            { "type": "integer" },
        "chunk_count":             { "type": "integer" },
        "window_start":            { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "window_end":              { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "window_start_iso":        { "type": "keyword", "index": false },
        "window_end_iso":          { "type": "keyword", "index": false },
        "bucket_seconds":          { "type": "integer" },
        "buckets":                 { "type": "integer" },
        "entry_count":             { "type": "integer" },
        "total_count":             { "type": "long" },
        "encoded_bytes":           { "type": "long" },
        "entries_checksum":        { "type": "keyword" },
        "checksum":                { "type": "keyword" },
        "coverage_status":         { "type": "keyword" },
        "activity_snapshot_time":  { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "published_at":            { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "entries":                 { "type": "object", "enabled": false },
        "chunks":                  { "type": "object", "enabled": false },
        "source_checkpoints":      { "type": "object", "enabled": false },
        "expected_ownership":      { "type": "object", "enabled": false },
        "coverage_gaps":           { "type": "object", "enabled": false },
        "counters":                { "type": "object", "enabled": false }
      }
    }
  },
  "_meta": {
    "family": "template-metrics",
    "written_by": "alice-template-catalog",
    "note": "Rolling seven-day count snapshots. A snapshot is one manifest and its generation-addressed chunks. Entries are stored and not indexed: the server fetches a generation by identifier and aggregates it in memory, so indexing every entry would buy nothing and cost the whole worker's template cardinality in terms."
  }
}
```

`entries` and the other object fields are stored in `_source` and not indexed.
Fetch them by document identifier and read them from `_source`.

`total_count` is a `long`. A decimal string coerces into it. A seven-day count
cannot approach the `long` ceiling.

Cleanup: hourly, delete-by-query on `published_at < now-1h` for generations that
are neither current nor previous. Preserve a pinned generation while a bounded
reader finishes; the reader pin lasts two minutes. A reader whose generation
disappears must fail explicitly.

### 7.3 `template-triage`, new

```json
{
  "index_patterns": ["template-triage"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 2,
      "codec": "zstd",
      "refresh_interval": "1s",
      "index.routing.allocation.require.role": "storage"
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "kind":                  { "type": "keyword" },
        "schema_version":        { "type": "integer" },
        "label_id":              { "type": "keyword" },
        "canonical_id":          { "type": "keyword" },
        "reviewed_version_ids":  { "type": "keyword" },
        "family":                { "type": "keyword" },
        "template":              { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
        "normalized":            { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
        "label":                 { "type": "keyword" },
        "note":                  { "type": "text" },
        "author":                { "type": "keyword" },
        "watched":               { "type": "boolean" },
        "reviewed_programs":     { "type": "keyword" },
        "reviewed_origin_hosts": { "type": "keyword" },
        "revision":              { "type": "integer" },
        "created_at":            { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "updated_at":            { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "history":               { "type": "object", "enabled": false }
      }
    }
  },
  "_meta": {
    "family": "template-triage",
    "written_by": "alice-shifter",
    "note": "Human decisions. They outlive both the seven-day activity window and the 90-day definition retention, and they extend neither. An author name is self-reported: Shifter has no user login."
  }
}
```

Rules:

- The document identifier is `label:<canonical_id>:<author_slug>`. The author
  slug matches `^[a-z0-9][a-z0-9-]{0,31}$`.
- Two authors produce two documents. Conflicting labels stay visible. The page
  shows the conflict; it does not merge them.
- `label` is one of `known_good`, `known_bad`, `needs_review`, `noisy`,
  `watched`.
- A note holds at most 2000 characters. `history` holds at most 50 revisions.
- Every write sends `if_seq_no` and `if_primary_term` and increments
  `revision`. A conflict returns the stored document to the browser.
- At most 100 templates may be watched. The limit is explicit and enforced.
- A label never expires, never keeps a template active, and never keeps a
  vector in memory.
- A returning exact version recovers its label. A widened version or a new
  source scope requires review.

### 7.4 `shifter-queries`, new

```json
{
  "index_patterns": ["shifter-queries"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "codec": "zstd",
      "refresh_interval": "30s",
      "index.routing.allocation.require.role": "storage"
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "kind":             { "type": "keyword" },
        "schema_version":   { "type": "integer" },
        "query_id":         { "type": "keyword" },
        "query_text":       { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 512 } } },
        "mode":             { "type": "keyword" },
        "include_inactive": { "type": "boolean" },
        "model_revision":   { "type": "keyword" },
        "corpus_revision":  { "type": "keyword" },
        "result_ids":       { "type": "keyword" },
        "result_ranks":     { "type": "object", "enabled": false },
        "opened_ids":       { "type": "keyword" },
        "viewer":           { "type": "keyword" },
        "latency_ms":       { "type": "integer" },
        "issued_at":        { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "updated_at":       { "type": "date", "format": "strict_date_optional_time||epoch_millis" }
      }
    }
  },
  "_meta": {
    "family": "shifter-queries",
    "written_by": "alice-shifter",
    "note": "Research feedback for retrieval evaluation. It keeps identifiers and ranks, never a copy of the template payload. A click is implicit feedback and is not a graded relevance judgment."
  }
}
```

Cleanup: daily, delete-by-query on `issued_at < now-365d`.

### 7.5 How the cluster configuration applies these

Notes moved here from the `sweet_opensearch` role README, because they describe
the schema rather than the role.

- **The three fixed Templates-page indices appear in no ISM policy; the two
  bucket families do.** `template-catalog`, `template-triage` and
  `shifter-queries` never roll over. Their retention is document expiry through
  a bounded delete-by-query, the `cockpit-metrics` and `trend-rollup` pattern.
  The stamper's bucket documents go into date-named indices,
  `template-buckets-5m-<day>` and `template-buckets-1h-<month>`, that an ISM age
  policy on the pattern deletes. They are not rolled over, because a bucket is
  republished in place while it is inside the worker's ledger and a rollover
  alias would put the republication in a new index beside the old document. The
  age is padded by one index period because `min_index_age` counts from
  creation.
- **`template-catalog` gets its mapping pushed onto the live index on every
  bootstrap.** `ensure_index` skips an existing index, and the mapping changed.
  Without the PUT, `last_observed` stays unmapped under `dynamic: false`, the
  90-day expiry matches nothing and the inactive history comes back empty, both
  without an error.
- **The `template-catalog` mapping holds three kinds of document.** Template
  definitions (`kind: template`), keyed by the version identifier and upserted
  by every stamper that observed the version; one watermark per node
  (`kind: watermark`); and the check results (`kind: check`). It carries no
  count: volume is a nested aggregation over the hourly bucket documents.
- **The bucket mappings index `versions` flat and `counts` nested.** A search
  expands a version to its descendants and routes by the `versions` keyword;
  the 28-day sum is one nested aggregation on `counts.version_id` and
  `counts.count`. The total beside the counts is what the conservation check
  compares them against.
- **Both log component mappings carry `template_version`, `template_id` and
  `template_status`.** The InfoLogger mapping is `dynamic: strict`, so the
  stamper's fields are not optional there.

## 8. HTTP responses the Shifter server serves

All Templates requests use `POST` with a JSON body, except the two named `GET`.
All responses are JSON. All errors follow the existing lane's shape:
`{"error": "<plain sentence>"}` with status 400, 502 or 503. A refusal explains
itself; it never returns a truncated count as a complete one.

No endpoint here starts a worker scan. No endpoint opens a second live stream.
The Logs page keeps its own subscription.

### 8.1 `GET /api/templates/summary`

```json
{
  "snapshot": {
    "window_start": 1788272400000,
    "window_end": 1788877200000,
    "window_start_iso": "2026-09-01T14:20:00.000Z",
    "window_end_iso": "2026-09-08T14:20:00.000Z",
    "buckets": 1008,
    "bucket_seconds": 600,
    "published_at": 1788877230000,
    "age_ms": 42000,
    "activity_snapshot_time": 1788877215000,
    "generations": {"epn146": 3, "epn228": 3}
  },
  "coverage": {
    "status": "partial",
    "expected": ["epn146", "epn228", "epn323"],
    "contributing": ["epn146", "epn228"],
    "missing": ["epn323"],
    "partial": [],
    "unexpected": [],
    "stale_cutoffs": {"epn323": 1788876600000},
    "gaps": [
      {"producer_id": "epn146", "reason": "invalid_observation_time",
       "records": 12, "detail": "", "index": "infologger", "shard": 0}
    ]
  },
  "totals": {
    "versions": 5571,
    "canonical_groups": 5301,
    "records": 4211987,
    "records_status": "incomplete"
  },
  "backlog": {"records": 0, "by_producer": {"epn146": 0}},
  "semantic": {
    "status": "ready",
    "groups": 5301,
    "vector_bytes": 10856448,
    "model_revision": "unset"
  },
  "last_complete_snapshot": {
    "window_end": 1788876600000,
    "window_end_iso": "2026-09-08T14:10:00.000Z",
    "coverage_status": "complete",
    "records": 4209110
  },
  "refreshed_at": 1788877235000,
  "stale": false
}
```

`records_status` is one of `exact`, `incomplete`, `unknown`, `unavailable`.
`semantic.status` is one of `ready`, `building`, `unavailable`.
`last_complete_snapshot` is `null` when none is retained. Its interval stays
explicit; the page never presents it as current.

### 8.2 `POST /api/templates/list`

Request:

```json
{
  "query": "",
  "mode": "text",
  "include_inactive": false,
  "watched_only": false,
  "family": [],
  "program": [],
  "host": [],
  "severity": [],
  "sort": "volume",
  "page_size": 50,
  "after": null
}
```

`mode` is `text` or `semantic`. `sort` is `volume`, `last_observed` or
`first_catalogued`. `page_size` is at most 50. `after` is the opaque cursor from
the previous response.

Response:

```json
{
  "rows": [],
  "page_size": 50,
  "after": ["4211987", "dpl:e5cad426a5acaa0b4610f900"],
  "has_more": true,
  "total": 5301,
  "total_relation": "eq",
  "window_end": 1788877200000,
  "window_end_iso": "2026-09-08T14:20:00.000Z",
  "coverage": {"status": "partial", "missing": ["epn323"]},
  "semantic": {"status": "ready", "model_revision": "unset"},
  "query_id": "q-8f2b1c",
  "took_ms": 14
}
```

One row:

```json
{
  "version_id": "dpl:e5cad426a5acaa0b4610f900",
  "canonical_id": "3aba27572ede143e",
  "family": "dpl",
  "template": "failed to allocate <NUM> bytes",
  "normalized": "failed to allocate <*> bytes",
  "programs": ["o2-gpu-reconstruction"],
  "origin_hosts": ["epn146"],
  "log_sources": ["stdout"],
  "severity_norm": "error",
  "scope_truncated": false,
  "count": 4211987,
  "count_status": "exact",
  "contributing_producers": ["epn146", "epn228"],
  "missing_producers": ["epn323"],
  "first_observed": 1788272460000,
  "last_observed": 1788877212000,
  "first_catalogued": 1787040000000,
  "active": true,
  "awaiting_volume": false,
  "historical": false,
  "superseded_by": null,
  "supersedes": [],
  "relationship_verified": true,
  "canonical_versions": ["dpl:e5cad426a5acaa0b4610f900"],
  "label": null,
  "label_conflicts": 0,
  "watched": false,
  "score": null
}
```

Rules the row carries:

- `count` uses the integer transport of section 4.
- `count_status` is `exact`, `incomplete`, `unknown` or `unavailable`. It is
  `exact` only when every expected producer contributed a complete manifest for
  the selected cutoff.
- `awaiting_volume` is true when the version was observed after the cutoff and
  its completed-window count is zero. The page states that the recent activity
  is waiting for the next volume update.
- `active` is `last_observed` within seven days of the activity snapshot time.
- `historical` is true when the row is an inactive definition, whether it came
  from the catalog because `include_inactive` was set or from a snapshot entry
  whose `last_observed` has left the seven-day window. A historical row moves
  its programs and hosts into `historical_programs` and
  `historical_origin_hosts` and leaves `programs` and `origin_hosts` empty, so
  a source hint is never read as a current affected host or program.
- `first_catalogued` is `null` on a list row and filled on a detail row. See
  section 6.2.
- `score` is the semantic similarity, or `null` outside semantic mode. A score
  never changes a count.

### 8.3 `POST /api/templates/detail`

Request: `{"version_id": "dpl:e5cad426a5acaa0b4610f900"}`

Response:

```json
{
  "version": {},
  "canonical_group": {
    "canonical_id": "3aba27572ede143e",
    "versions": [],
    "count": 4213001,
    "count_status": "exact"
  },
  "labels": [],
  "episodes": [],
  "neighbours": {
    "suggestions": [
      {
        "version_id": "dpl:9c11f0a4d2b7e6531ac40d88",
        "canonical_id": "77ce41a09b3d5e12",
        "family": "dpl",
        "template": "failed to allocate <NUM> bytes on device <NUM>",
        "score": 0.94
      }
    ],
    "note": "",
    "semantic": {"status": "ready", "model_revision": "unset"}
  },
  "log_link": {
    "criterias": {"message": {"match": "failed to allocate"}},
    "options": {"mode": "wildcard", "limit": 2000}
  },
  "examples_available": true,
  "examples_reason": ""
}
```

`version` and each item of `canonical_group.versions` use the row shape of
section 8.2. The group count is the sum of the listed versions and nothing else.
Predecessor volume is never added to successor volume automatically.

`episodes` is empty for a historical row. Its hosts are a source hint from
before it went quiet, so no firing episode is presented as related to it.

`neighbours.suggestions` holds at most five active templates, nearest first by
vector distance, and never the selected template itself. It is a suggestion for
a reviewer, not a verified version relationship. `score` is the semantic
similarity of section 8.2. A suggestion changes no count, no label and no alert.
`neighbours.semantic` carries the search state of section 8.2, and the list is
empty whenever that state is not `ready`.

### 8.4 `POST /api/templates/examples`

Request:

```json
{"version_id": "dpl:e5c...", "since": null, "until": null, "limit": 50}
```

Response:

```json
{
  "available": true,
  "rows": [],
  "candidates_fetched": 500,
  "matched": 12,
  "truncated": true,
  "source": "central",
  "note": ""
}
```

When no central example exists the response is
`{"available": false, "reason": "worker_local_only", "rows": []}`.
The other reasons are `too_generic`, `no_candidates` and `no_match`.
`too_generic` means the template holds too little literal text to build a
bounded candidate query. It is a refusal to search, not a statement that no
record matches the template. `examples_reason` on the detail response of
section 8.3 carries the same value, or the empty string when examples can be
fetched.

`rows` use the existing `/api/query` record shape. A matched line is consistent
with the selected template. It is not proof of historical assignment. Candidate
yield is neither recall nor volume.

This endpoint runs only after an explicit request. It fetches at most 500
candidates. It never runs for every visible template.

### 8.5 `POST /api/templates/label`

Request:

```json
{
  "canonical_id": "3aba27572ede143e",
  "reviewed_version_ids": ["dpl:e5c..."],
  "label": "known_bad",
  "note": "",
  "author": "marko",
  "watched": false,
  "revision": 3
}
```

Response `200`: `{"label": {}, "revision": 4}`.
Response `409`: `{"error": "this label changed while you were editing it", "label": {}}`.
The returned label is the stored `template-triage` document.

### 8.6 `GET /api/templates/episodes`

```json
{
  "episodes": [
    {
      "incident_id": "",
      "grouping_key": "",
      "alertname": "",
      "entity_kind": "host",
      "entity_id": "epn146",
      "severity": "error",
      "state": "firing",
      "episode_start": 1788876200000,
      "title": "",
      "diagnosis": "",
      "action": "",
      "affected": [],
      "signals": []
    }
  ],
  "refreshed_at": 1788877235000,
  "stale": false
}
```

The identity fields come from the existing signal projector and Alertmanager
grouping. Shifter reuses them; it builds no browser-side alert lifecycle.
Shared incident summaries refresh no faster than every 30 seconds.

### 8.7 `POST /api/templates/opened`

Request: `{"query_id": "q-8f2b1c", "version_id": "dpl:e5c...", "rank": 3}`
Response: `204` with no body.

This records implicit feedback in `shifter-queries`. It is not a relevance
judgment.

### 8.8 Serving limits

These are the initial proposals from the plan. Set each one in the role defaults
and verify the combined peak against the 384 mebibyte service ceiling.

- Two concurrent OpenSearch detail queries.
- One semantic encoding task at a time.
- At most 50 template rows per page.
- At most 500 example candidates per explicit request.
- Shared incident summaries no faster than every 30 seconds.
- Rolling counts refresh after the scheduled producer interval.
- Templates polling pauses when the page is inactive or the tab is hidden.
- A superseded browser request is discarded.

## 9. Worker state keys

The worker state file keeps one atomic object. These key names are fixed here so
no later stage invents another spelling.

`version`, `incarnation`, `generation`, `published_window_end`, `position`,
`trees`, `ledger`, `identifiers`, `versions`, `pending`, `programs`,
`rotation`, `catalog`, `issued`.

`incarnation` is a persistent identifier for this state. A state reset creates a
new one. `generation` rises by one per published snapshot and starts at one.
`ledger` holds the sparse ten-minute bucket counts per version.
`identifiers` holds the collector's boot-and-sequence ranges for duplicate
detection. It must be exact; an approximate membership filter can discard an
unseen record and is refused.

The existing state-version discard behaviour stays. A state file whose version
does not match is thrown away, not migrated.

## 10. Stated costs

- A chunk holds at most 500 entries and 1000000 encoded bytes. Encoding one
  chunk allocates about the same again, so the publication path peaks near two
  megabytes. The worker unit allows 512 mebibytes.
- One entry holds at most 65536 bytes. A typical entry is about 400 bytes, so a
  full 500-entry chunk is about 200 kilobytes.
- The shared module itself holds compiled regular expressions and constants. Its
  resident cost is under 100 kilobytes.
- 20000 versions across 1008 completed buckets would need 20160000 counters.
  Eight-byte counters alone occupy about 161 megabytes before metadata or
  serialization. Pause input with incomplete coverage before that limit. Never
  evict a positive count.
- One 512-dimension vector per canonical group, at four bytes per value, is
  10856448 bytes for 5301 groups. That is the matrix alone, about 10.9
  megabytes. Measure the whole serving footprint on the target host.

These are computed ceilings, not measurements. Load acceptance still needs the
measurements in plan section 11.
