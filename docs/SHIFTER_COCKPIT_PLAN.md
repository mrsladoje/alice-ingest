# The shifter cockpit: recent activity, template history, and labels

**Author:** Marko Sladojevic  
**Date:** 8 September 2026. Revised to separate seven-day activity from 90-day central template history.  
**Status:** agreed requirements; implementation and load acceptance remain pending.  
**Supersedes:** the transfer, sidecar, index, and vector-storage design of *The Template Lane*, dated 2 September 2026.

## Conclusion

Build a compact Templates page inside the existing Shifter application.

Show templates observed within seven days in the default view.
Show an exact seven-day volume for each template at a stated collection snapshot.
Keep inactive template definitions centrally for 90 days after their latest observation.
Expose them through an explicit "Include inactive templates" option.
Expire old count contributions, cached vectors, and inactive worker mining state on their separate seven-day rules.
Preserve manual labels and notes independently.

The first release includes seven-day counting.
Search, labels, freshness, and relevant incident episodes ship with it.
Later work adds watched-template histories and detection through the existing alert pipeline.

Opening or refreshing the page must never start a worker scan.
Workers extend their existing scheduled mining pass.
The Shifter server shares completed snapshots between viewers.
Vectors remain in server memory.

This document defines the intended behavior.
Existing catalog code does not yet implement this retention or counting contract.
Local tests and previous review rounds do not establish production load acceptance.

## 1. Requirements and boundaries

### Separate retention periods

Use separate configuration settings for these requirements:

- Default activity view: seven days since the latest observation.
- Exact volume: a rolling seven-day interval.
- Central template definitions: 90 days since the latest observation.
- Worker counters and inactive mining state: the bounded seven-day working window.
- Manual labels and notes: durable retention independent of template expiry.

Ninety days is the agreed starting policy for central history.
It is not a measured optimum or a guarantee that every shutdown is covered.
Measure template return intervals before changing that policy.

### Activity and exact volume

The active set uses seven days since each template's most recent accepted observation.
Each new matching log restarts that template's inactivity period.
Template creation time does not determine expiry.
The observation also extends the central definition's separate 90-day retention deadline.

For example, a template created on 1 September receives another matching log on 7 September at 14:20.
It remains active until 14 September at 14:20 unless another observation extends that deadline.
It then remains available as inactive central history until its 90-day deadline.

Keep activity expiry separate from the volume interval below.
Recent observations in the current open bucket protect a template before they enter the next completed volume window.

The interval ends at a completed ten-minute boundary in Coordinated Universal Time (UTC).
It includes its start and excludes its end.
Seven days contain 1,008 ten-minute buckets.

For example, a window ending on 8 September at 14:20 starts on 1 September at 14:20.
The page displays both endpoints and the snapshot publication time.

The volume is an integer count of records assigned to that template within the interval.
It is not a lifetime total, an estimate, or a count of matching search results.

Ten-minute buckets cannot provide an exact interval ending at an arbitrary current second.
The page must show its actual cutoff.
It must never describe a stale snapshot as a current seven-day total.

### Observation time and count scope

Use the record's existing `collector_time` as the observation clock.
This field describes collection in this system.
The original event timestamp remains available in log details.

Publication time does not establish observation time.
Retries, snapshot reconstruction, and catalog recreation must preserve the original observation time.

Count each stored source record once under the registered route ownership.
Use the collector's stable `doc_id` as the record identity.
Source progress includes the concrete index UUID, shard, and sequence number.
The worker state also has a persistent incarnation identifier.

Sequence numbers identify indexing operations rather than immutable document identities.
Retries across rollover can place the same identifier in different backing indices.
They must still contribute once.

Admit immutable source documents with stable identifiers.
Verify the deployed create operation and request document versions during scans.
An unexpected overwrite or missing stable identifier creates an explicit coverage error.
This design does not infer corrections from mutable source documents.

This counts observed log records, not deduplicated real-world events.
An upstream duplicate remains another observed record unless the source provides a validated stable event identifier.

A source record with missing or invalid observation time prevents complete coverage for its affected scope.
Report that condition explicitly.
Do not replace its time with the current clock or silently discard it.

A completed shard checkpoint proves a processed indexing prefix.
It does not prove that every earlier collection timestamp has already arrived.
Therefore, counts are exact for the processed source checkpoints shown in the snapshot.
Late indexed records can correct a retained bucket in a later snapshot.

### Required first-release behavior

- Hide templates from the default view after seven days without an observation.
- Retain inactive definitions centrally for 90 days after their latest observation.
- Publish exact integer volumes for the displayed seven-day interval.
- Show missing producers, scan backlog, source gaps, and snapshot age.
- Use one scheduled mining pass for identity and counts.
- Reuse Shifter styling, navigation, and bounded log queries.
- Reuse the existing signal, incident, and notification grouping rules.
- Keep semantic similarity outside count attribution and alert suppression.

Seven-day activity and volume coexist with 90-day central metadata retention.
Longer central history must not extend worker counters, identifier ledgers, or source scans.
Counting is no longer deferred to the second release.

## 2. Existing components and their limits

### Shifter

The existing application uses vendored Preact and a Python server.
The live lane receives collector records through Server-Sent Events.
The query lane sends bounded searches through `POST /api/query`.
The browser does not contact OpenSearch directly.

The live lane can operate during a cluster outage.
New search and catalog work must preserve that behavior.

The default query scope is `infologger,application-logs-central`.
It excludes worker-local informational logs.

The service currently sets `MemoryHigh=192M` and `MemoryMax=384M`.
The latter permits 384 mebibytes for the whole service.
Physical memory on the host does not establish available service memory.

Sources:

- `deploy/roles/sweet_shifter_view/files/shifter.py`
- `deploy/roles/sweet_shifter_view/files/live/shifter.js`
- `deploy/roles/sweet_shifter_view/defaults/main.yml`
- `deploy/roles/sweet_shifter_view/templates/alice-shifter.service.j2`

### Template catalog producer

The existing producer scans the worker-local route and the two shared routes.
Shared-route reads select records owned by that producer.
It mines new records outside the collector delivery path.

The producer reads each shard checkpoint before refresh.
It then scans through that checkpoint and saves progress by index UUID and shard.
Preserve that ordering and its index-recreation safeguards.

The persisted mining tree is state.
Replaying template strings through an empty miner does not reconstruct equivalent state.

Current counts are lifetime cluster totals.
Current `first_seen` and `last_seen` record publication activity.
Neither supplies the required seven-day volume or inactivity clock.

Current template retirement moves lifetime contributions when a template widens.
The new volume design must replace that behavior for seven-day counts.

The present scan budget applies separately to each concrete index.
Three single-shard source indices can permit 600,000 fetched records in one pass.
Rollover aliases can expose additional concrete indices.

The worker already has a memory limit, reduced process priority, and staggered execution.
Those settings do not bound OpenSearch search and refresh work.

Previous tests remain evidence for the existing implementation.
They do not validate the changes specified here.

Sources:

- `deploy/roles/template_catalog/files/template_catalog.py`
- `deploy/roles/template_catalog/defaults/main.yml`
- `deploy/roles/template_catalog/templates/alice-template-catalog.service.j2`
- `docs/SOAK_RESULTS.md`, rounds 9 through 20

### Existing alert path

The signal projector writes `alice-signals` and `alice-incidents`.
Alertmanager already groups notifications.
The projector exposes the corresponding grouping identity for cockpit cards.

Reuse that identity in Shifter.
Keep each affected entity's recovery state within its episode.

Current rollups also distinguish individual silence from whole-family silence.
They create zero rows for recently active hosts only when peer activity supports that interpretation.
Missing producer coverage is not evidence of zero template volume.

Sources:

- `deploy/roles/sweet_signal_projector/files/signal_projector.py`
- `deploy/roles/sweet_alertmanager/templates/alertmanager.yml.j2`
- `deploy/roles/sweet_trend_rollup/files/trend_rollup.py`

## 3. Where work runs and how it stays bounded

Workers perform scheduled mining and maintain seven-day counters.
The storage tier holds catalog metadata and committed rolling-count snapshots.
Shifter reads these shared records and serves a cached page.
Historical searches read bounded pages from central metadata only.
They must not load the entire 90-day catalog into Shifter memory.

There is no new collector proxy.
There is no per-record embedding or vector write.
There is no browser-triggered fleet scan.

### Worker limits

Use one total row budget across routes, concrete indices, and shards.
Use a cooperative elapsed-time budget for scanning and mining.
Persist fair continuation across every dimension.
A busy first shard must not starve later work.

Retain the checkpoint-before-refresh rule.
Count refreshes and remote searches in the workload measurements.
Skip unnecessary work only when its correctness is established.

Initial implementation limits are proposals for validation:

- Fetch at most 200,000 source records per producer pass.
- Fetch at most 5,000 records per search page.
- Stop additional scanning after 120 seconds of cooperative work.
- Permit only one active producer pass per worker.
- Retain the existing 512-mebibyte worker memory ceiling.
- Retain the current 20,000-cluster admission ceiling until measurements justify another value.

Request deadlines must also bound blocking network operations.
A process timeout must not discard a pending batch or advance uncommitted checkpoints.

These limits are ceilings, not throughput targets.
Measure whether they keep pace with source arrival rates.
If they cannot, coverage remains incomplete and deployment acceptance fails.

Report fetched records, mined records, expired records, invalid timestamps, backlog, unlearned records, pass duration, peak memory, and publication failures.
An admission limit must never silently convert lost coverage into a complete total.

### Shifter limits

Use one background refresh task for catalog and rolling counts.
Read bounded snapshot pages and aggregate them incrementally.
Do not retain every producer's full response after aggregation.

Publish one immutable in-memory view when a refresh finishes.
Requests share that view.
A partial response must not erase entries from the last valid view.

Use separate limits for cached metadata, vectors, decoded responses, and temporary aggregation memory.
Set every limit in role defaults and verify their combined peak against the service memory ceiling.
Refuse oversized work with an explicit status.
Do not truncate counts and then claim completeness.

Initial serving limits are proposals for validation:

- Permit two concurrent OpenSearch detail queries.
- Run one semantic encoding or catalog embedding task at a time.
- Return at most 50 template rows per page.
- Fetch at most 500 example candidates per explicit request.
- Refresh shared incident summaries no faster than every 30 seconds.
- Refresh rolling counts after the scheduled producer interval.

Bound expensive search requests at the server and OpenSearch.
Discard superseded browser requests.
Pause Templates polling when the page is inactive or the browser tab is hidden.

The Logs page owns its live subscription.
The Templates page must not mount an additional live stream.
Preserve existing filter links and page state during navigation.

No failure in semantic search may stop ingestion, the live stream, or ordinary log queries.

## 4. Exact seven-day counting

### Selected storage shape

Keep the seven-day bucket ledger on each worker.
Publish rolling totals to the storage tier.
Do not publish every template bucket from every worker in the first release.

This choice supplies the requested volume without a large shared template time-series index.
The second release can publish a limited watched-template history.

Each worker ledger retains 1,008 completed ten-minute buckets and the current open bucket.
Ended buckets remain correctable while they are inside the retained interval.
An ended time interval is not a claim that late records cannot arrive.

Use sparse bucket entries and compact integer storage.
Update running totals when records arrive and when buckets expire.
Avoid a dense Python object for every template and every time slot.

Keep an exact retained identifier ledger in the same durable state.
Use compact sequence ranges or bitmaps for the collector's boot-and-sequence identifiers.
Check duplicates across all backing indices owned by that producer before mining.
Expire identifiers only after their observations leave the retained window.
Do not use an approximate membership filter that can discard an unseen record.
Include identifier storage in the byte and serialization budgets.

Cap ledger entries, encoded bytes, and serialization peak memory.
Measure them together with mining state.
Do not increase the worker memory allocation without recording the measured requirement.

Bound active text versions separately from mining clusters.
One cluster can produce several retained versions as its text widens.
Twenty thousand versions across all completed buckets require 20,160,000 counters.
Eight-byte counters alone occupy approximately 161 megabytes before metadata or serialization.
Pause further input with incomplete coverage before exhausting a state limit.
Never evict positive counts to remain within that limit.

### Template identity and attribution

Count each record against the exact template text version returned when that record is mined.
The version identifier includes the family and exact masked template text.

When a template widens, new records can belong to the new version.
Earlier records retain their original version attribution until they expire.
Do not move old counts through `superseded_by`.

Preserve metadata for an earlier version while its seven-day count remains positive or its open bucket contains observations.
This requirement applies to worker snapshots.
The central catalog retains that version's definition for its separate 90-day period.
The page can show that version's successor as a relationship.
It must not add predecessor volume to successor volume automatically.

Group versions for search using the existing canonical normalization.
Sum each version contribution exactly once when presenting a canonical group.
A group count means the sum of its explicitly listed versions.

Similarity between vectors does not establish count identity.
A positional log match does not establish historical mining assignment.

### One record through the producer

1. Read the record within its source checkpoint and route ownership.
2. Validate its stable identifier, immutable version, and observation timestamp.
3. Skip expired or already counted observations, while preserving safe source progress.
4. Register and mine an eligible observation once.
5. Increment its assigned version in the appropriate ten-minute bucket.
6. Update the version's retained source metadata.
7. Persist the pending batch before publication.
8. Publish the batch through the committed snapshot protocol.

Keep the first and last observation times needed by retained versions.
Derive displayed programs and source scope from retained observations.
If detailed scope is capped, expose truncation without truncating the volume.

The same atomic state covers mining trees, count and identifier ledgers, source positions, version metadata, and pending publication.
A failed pass cannot save one part and lose another.

### Common cutoff and idle producers

Anchor producer runs to common UTC ten-minute boundaries.
Stagger their start within each period.
Do not use cumulative random delay that makes producers skip different cutoff intervals.

Each run names its target `window_end`.
All contributions to a fleet total must use that same cutoff.
Never sum a producer's 14:10 total with another producer's 14:20 total.

Freeze the cutoff and required ledger buckets together when preparing the pending snapshot.
Keep published cutoffs monotonic across clock changes.
Report a clock fault instead of moving the window backwards.
A delayed publication retains its original interval and must not appear current.

Expire old buckets on every scheduled pass.
Publish changed totals even when no new logs arrive.
Publish a complete empty snapshot when a producer has no retained observations.

The current open bucket remains outside the displayed total.
Include its observed template identities and latest observation times in snapshot metadata.
These observations renew activity without changing the completed-window total.
A timestamp beyond the accepted collection-clock tolerance creates an explicit coverage error.

### Snapshot publication and retries

Use `template-metrics` as a fixed index for compact rolling-count snapshots.
A snapshot contains bounded chunks and a manifest.

Each chunk identifies its producer, state incarnation, cutoff, generation, and chunk number.
Use explicit document kinds to separate snapshot chunks, manifests, and any later watched history.
Each entry includes version identity, canonical identity, seven-day count, retained observation times, and sufficient template metadata.
Chunks contain the metadata needed to display their counted versions.
They must not depend on a later lookup against mutable catalog metadata.

A manifest includes chunk identifiers, checksums, source checkpoints, expected ownership, coverage status, and the displayed interval.
Split chunks by both entry count and encoded byte size.
An oversized entry must produce a visible error.

Persist the exact chunk contents and identifiers in the pending state.
Retries replace the same documents with the same values.
Do not send increment operations that a retry can apply twice.

Verify every bulk response item.
Persist complete version metadata in the central catalog before acknowledging its snapshot publication.
Retain the true observation times and use idempotent maximum-time updates.
Repeated publication must not renew the 90-day deadline without a newer observation.
Confirm that all chunks are searchable before publishing the committed manifest.
Finish the pending publication before consuming another source batch.

A commit marker alone is insufficient if new writes overwrite the previous snapshot.
Use generation-addressed chunks.
Keep the previous committed generation usable until its replacement is complete.

Shifter reads only committed manifests and their verified chunks.
It validates the entire selected generation before changing the shared view.
It never combines part of an old generation with part of a new generation.

Use integer arithmetic throughout aggregation.
Preserve exact integers across the browser boundary.
Use decimal strings when a value exceeds JavaScript's safe integer range.

### Coverage and recovery

A complete empty manifest can establish zero for that producer.
A missing manifest establishes unknown coverage.

Resolve expected producers from the source registry and ownership roster.
A retired producer's retained records remain part of the seven-day window.
Before retirement, transfer its count and identifier ledgers to an explicitly assigned owner.
Otherwise, report its missing contribution until its records leave the window.

If producers lack a matching cutoff, publish a partial total for the selected current interval.
Show the missing scope.
Do not reuse their differently dated totals.

Show the last complete snapshot separately when useful.
Its historical interval must remain explicit.

Initial startup requires a bounded backfill of the full seven-day observation window.
Source retention must cover that window and the expected processing delay.
If source deletion removes unread records, record a persistent coverage gap.
The total remains incomplete until that gap leaves the window or is repaired.

A state reset creates a new incarnation and requires controlled backfill.
Do not combine replacement coverage with overlapping old-incarnation coverage.
An ownership transition must select exactly one contribution for each source range.
Accept one committed incarnation per producer and cutoff.
Fence superseded writers so they cannot replace the selected incarnation.

Catalog recreation can restore active metadata from committed snapshots.
It must not republish every template from lifetime mining state as newly observed.
Seven-day worker snapshots cannot restore definitions last observed eight to 90 days earlier.
Preserve or export central metadata before a planned catalog recreation and restore that history separately.
If historical recovery data is unavailable, report lost historical coverage explicitly.
Do not extend worker history to compensate for a lost central catalog.

### Why this is smaller than a complete shared time series

At 5,301 canonical groups, 1,008 bucket rows per group would create 5,343,408 rows before replicas.
That assumes every group occurs in every bucket.
Adding producer or host dimensions multiplies the possible row count.

The selected design stores bucket history locally and publishes only rolling totals.
Its shared size depends on active contributions, bounded chunk size, and retained generations.
Its worker cost depends on occupied ledger entries.

Measure both costs.
Sparse storage is an implementation choice, not proof that a workload is small.

## 5. Retention and storage lifecycle

### Active catalog

Replace publication-based activity with actual retained observation times.
The central catalog holds definitions observed within 90 days.
The default Shifter view selects its active seven-day subset.

Apply the seven-day inactivity filter to default lists and active semantic search.
Apply the 90-day retention filter to explicit historical searches.
Logical visibility must not wait for physical cleanup.
Remove inactive groups' vectors from memory during the same refresh.

Update `last_observed` with the maximum of its current value and the accepted record's `collector_time`.
Mark a template inactive after seven days without a newer accepted observation.
Keep its central definition until 90 days have passed without a newer accepted observation.
An older delayed record must not move this timestamp backwards.
Retries and publication alone must not extend the inactivity deadline.
An old delayed batch cannot reactivate it with a current publication timestamp.

Display the activity snapshot time separately from the volume cutoff when they differ.
A template observed after that cutoff can remain active with zero volume in the completed window.
Show that recent activity is awaiting the next volume update.

Volume expiry applies to individual observations.
A new log does not renew older observations or reset the accumulated seven-day count.

For a stale source, state that inactivity is based on available observations.
Missing coverage must not appear as a confirmed quiet source.

### Inactive central definitions

Keep exact masked text, version and canonical identifiers, observation dates, and verified version relationships.
Keep bounded historical source hints with an explicit historical label.
They must not appear as current affected hosts or programs.

Reuse the existing fixed `template-catalog` index.
Do not create a separate history index or retain dormant vectors.
Historical retention adds no worker count buckets or automatic raw-log searches.

An exact returning version becomes active through its next accepted observation.
Retain its previous central identity and independently reviewed labels.
Do not restore expired volume contributions.

Central definitions cannot reconstruct an expired worker mining tree.
A returning message can therefore produce a different template version.
Apply the existing identity and label-scope checks before associating its history.

Do not infer seven-day volume from an inactive status or a last-observed timestamp.
Use the selected count snapshot and its coverage status.
Show zero only when that snapshot proves zero for the relevant scope.
Otherwise, show incomplete or unavailable volume.
An inactive definition can still have observations inside a slightly earlier completed volume window.

### Physical catalog cleanup

One central maintenance task removes definitions after their 90-day observation deadline.
Use a bounded, throttled delete-by-query on `last_observed`.
Do not schedule the same cleanup on every worker.

Run cleanup hourly.
Default visibility still follows seven-day inactivity between cleanup runs.
Historical visibility follows the separate 90-day deadline.
Monitor cleanup age, deleted documents, failures, and version conflicts.

Concurrent new observations must survive cleanup.
Skip conflicting deletions and reevaluate them in the next pass.
Do not retry a previously selected identifier without checking its current observation time.

Every catalog creation path must write complete metadata.
A retirement operation must not recreate a document without template text or observation time.
Replace the existing retirement-upsert behavior before enabling expiry.

### Worker state cleanup

The worker's mining tree and count ledger have their own seven-day lifecycle.
Central metadata retention must not keep worker state alive for 90 days.

Expire version ledger entries after their final retained bucket leaves the window and their open bucket contains no observations.
Remove associated source metadata and cached publication state.
This local cleanup must not delete the retained central definition.

Remove a mining cluster only when no retained observation or pending batch still requires it.
Remove its tree references and related metadata atomically.
Preserve active clusters and monotonic cluster identifiers.

If tree routing requires reconstruction, rebuild it from retained cluster objects.
Do not mine their template strings as new records.
Do not use least-recently-used eviction to remove active clusters.

Keep state checkpoints needed to prevent duplicate source consumption.
Small checkpoint and incarnation records are control state, not retained template history.

### Snapshot cleanup

Retain the current and previous committed generations per producer.
Keep one pending generation while publication completes.
Bound their total bytes and monitor stalled publication.

Discard obsolete generations only after their replacement is committed and searchable.
Preserve pinned generations while a bounded reader finishes.
Readers must fail explicitly if their required generation disappears.

A maintenance expiry ceiling also removes abandoned generations.
A producer must not leave unlimited snapshots after repeated failures.
Set the initial shared snapshot age ceiling to one hour and reader pin duration to two minutes.
An older pending retry may complete recovery, but its interval cannot become the current view.
Maintenance removes its stale shared documents after recovery readers release them.

Rolling snapshots retain no bucket history beyond the worker's seven-day ledger.
They contain totals for their stated interval.
Old snapshots are excluded from the current view even before physical deletion.

### Manual labels and query history

Manual labels and notes remain separate from the active catalog.
Preserve these human decisions after both seven-day inactivity and 90-day definition expiry.
They do not extend catalog retention or keep a template active, embedded, or counted.

A returning exact version can recover its reviewed label.
A broader version or new source scope requires review.
Preserved labels do not prove first-ever fleet novelty.

Bound note length and label history document size.
Use revision checks so simultaneous edits cannot silently overwrite each other.
An author name is self-reported because Shifter has no user login.

Keep query history for one year with document expiry.
Record query text, model revision, displayed result identifiers, ranks, and opened identifiers.
Do not retain copied template payloads merely because the query log lives longer.

Clicks are implicit feedback.
They are not graded relevance judgments.

### Relationship to Index State Management

Index State Management (ISM) automates lifecycle actions for whole indices.
Its delete action does not expire individual templates by their observation time.
Rolling a mutable catalog would also distribute one identity across backing indices.

Use fixed indices with document expiry for this design.
This follows the existing `cockpit-metrics` and `trend-rollup` retention pattern.
Provision cleanup settings, mappings, and verification through Ansible.
The catalog policy uses 90 days since observation; the worker volume window remains seven days.

Rollover alone cannot define an exact seven-day query window.
Any future rollover design must retain every record needed by that window.
Deletion timing must account for the rollover period and delayed processing.

Daily replicated backing indices would also multiply the shard requirement.
Do not introduce them without a new measured shard and query budget.

Official references:

- [ISM policies](https://docs.opensearch.org/latest/im-plugin/ism/policies/)
- [Delete by Query API](https://docs.opensearch.org/latest/api-reference/document-apis/delete-by-query/)

### Indices and shard budget

Keep the three existing log routes.
A new source remains a field value rather than a new index.

Change the `template-catalog` mapping and writer for observation-based activity.
Retain 90-day definitions in that same index and select active metadata by observation time.
Remove lifetime count fields from the page's count contract.

Add these fixed indices:

- `template-metrics`: rolling-count chunks and manifests; one primary and two replicas.
- `template-triage`: manual decisions; one primary and two replicas.
- `shifter-queries`: research feedback; one primary and no replica.

These additions require seven shards under those settings.
If the earlier 45-shard inventory still applies, the result is 52 shards.
That inventory is historical.
Verify the actual count and heap allocation before deployment.

The earlier plan's 49-shard and 52-shard first-release figures no longer describe separate stages.
Required counting now lands with the first release.

The vectors add no OpenSearch index.
A longer catalog retention period adds documents without adding rollover shards.
Measure central document count, bytes, update cost, and historical query cost under that period.
A later watched-template history must fit an explicit budget before it is enabled.

## 6. Search, identity, and labels

### Exact search in Shifter memory

Keep exact vector search in the Shifter server.
Do not add an approximate vector index, embedding service, or per-line vector work.

The current frozen seven-family manifest records 5,571 family templates and 5,301 canonical groups.
At 512 dimensions and four bytes per value, one vector per group occupies 10,856,448 bytes.
That is approximately 10.9 megabytes for the matrix alone.

The older 4,221-template measurement came from a three-family archive experiment.
Neither sample establishes a maximum fleet cardinality.

The earlier 0.23 processor-seconds estimate covered encoding the older corpus.
The benchmark loads the model before its timer starts.
It does not measure cold startup, catalog transfer, or request latency.

Measure total serving memory and latency on the deployment host.
Include model loading, Python objects, buffers, viewer queues, decoded responses, and temporary refresh allocations.

Set hard limits on active search groups and vector memory.
If a limit is reached, expose unavailable semantic coverage.
Do not silently describe a truncated search corpus as complete.

Embed each unseen active canonical group once through the bounded background task.
Encode the group's normalized text and share its vector between its listed versions.
Replace cached snapshots atomically.
Remove a group's vector when its final active version expires.

Vectors need not persist.
A restart rebuilds them from a committed active snapshot.
Provide ordinary catalog access while semantic search is unavailable.
The inactive-history option uses bounded text and identifier searches against central metadata.
It does not extend semantic search to dormant definitions or rebuild their vectors automatically.

### Shared functions

Use the existing normalization and digest behavior for search grouping.
Move those unchanged functions into a small shared module.

Do not import the offline `freeze.py` program into Shifter.
Its module imports assume the repository layout and bring additional mining and corpus dependencies.

Normalization removes distinctions between typed masks and general wildcards.
Therefore, a canonical identifier is not proof of unchanged reviewed meaning.

Each label records the reviewed version identifiers, template text, and source scope.
Conflicting labels remain visible.
Nearest semantic neighbors appear as suggestions.

Never inherit statistics or suppress an alert solely because two vectors are close.

### Retrieval evidence

`docs/SEMANTIC_PLAN.md` owns model selection and its frozen evaluation gates.
This page does not select a retrieval winner.

The earlier twenty-query evaluation is a development result.
Its precision ceiling applies to its judged pool.
It does not prove that missing failure conditions are absent from the archive.

Collect real operator questions through the page.
Keep explicit human judgments separate from clicks.
Use current evaluation results only with their corpus and judgment provenance.

Sources:

- `downloads/frozen/corpus-2026-09-08/manifest.json`
- `tools/embed/embedbench.py`
- `tools/embed/freeze.py`
- `docs/SEMANTIC_PLAN.md`
- `docs/SEMANTIC_RESULTS.md`
- `docs/SOAK_RESULTS.md`

## 7. Log examples and matching

Default to retained central examples through the existing query endpoint.
Some active templates represent worker-local informational records.
The page must state when no central example is available.

Do not fetch examples for every visible template.
Fetch one bounded candidate page after an explicit request.

Any later worker-local lookup requires a selected host and a bounded time range.
It also requires a page limit, concurrency limit, and server deadline.
There is no automatic worker lookup when central examples are absent.

Use the shared family mapping and preparation recipe.
Raw `stdout` records can map to different mining families through their other fields.

The positional matcher must handle numeric-mask generalization and every selected template variant.
Strict token equality alone does not cover the existing floating-point and integer mask rule.

Literal-token candidate queries must account for masking and indexed field behavior.
A mostly wildcard template must not trigger an unrestricted search.

Label returned lines as consistent with the selected template.
Two versions can accept the same line.
A match does not prove historical assignment to either version.

Candidate yield measures how many fetched lines pass the matcher.
Recall requires independently known matching records.
Do not use candidate yield as a recall measurement or as the seven-day volume.

## 8. The Templates page

Add one page beside Logs inside the current Shifter application.
Use the existing typography, spacing, keyboard behavior, and responsive layout.
Load page-specific code only when needed.

Keep the initial view compact:

- Snapshot interval, freshness, and coverage.
- Relevant active episodes with affected scope and next action.
- Watched templates with seven-day volume.
- Recently catalogued active templates.
- One template search field.

Use one paginated list and one detail drawer.
The drawer shows version text, retained source scope, seven-day volume, label history, related versions, and a log link.

Keep "Include inactive templates" off by default.
When enabled, add retained central definitions with clear activity status and last-observed time.
Historical lookup supports template text and identifiers.
Use the same bounded list and detail drawer.
Join any displayed volume to the selected seven-day snapshot rather than an old catalog total.
Show incomplete historical coverage after catalog loss when restoration was unavailable.

Distinguish complete zero, unknown coverage, and unavailable examples.
Show lifetime research measurements only in documentation.

“First catalogued” is informational.
It is not a first-release alert.
A returning template and a widened template must not appear as confirmed first-ever fleet events.

Episode cards reuse the existing notification grouping identity.
Raw signals remain supporting evidence in the drawer.
Infrastructure failures can appear as monitoring-health information without filling the main template list.

Keep every automatic page request on fixed aggregate or catalog indices.
Shared snapshots make worker work independent of viewer count.

## 9. Later detection and domain metrics

Seven-day volume is required in the first release.
Detection and detailed histories remain a later stage.

Start with a limited watch list and ten-minute host summaries.
A human-maintained list still needs an explicit entity limit.
Do not create a detector for every host and template combination.

Publish watched history during the existing mining pass.
Use an explicit source-host identity.
The present producer node identifier does not establish the original source host.

Candidate signals include watched-template changes, template diversity changes, and peer-relative volume.
Run-phase conditioning requires a validated phase source.
Per-template silence requires known coverage and expected activity.

Reuse current completeness, minimum-count, silence, recovery, and notification contracts.
Register new monitors in the shared signal catalog.
Do not build a browser-side alert lifecycle.

The seven-day count retention rule also constrains detector baselines.
Ninety-day definitions do not supply 90 days of volume history.
A detector that requires older count history needs a separately approved retention change.

### Existing domain numbers

Connection saturation from `clients` and `client_limit` is a useful candidate.
Those observations come from events rather than a guaranteed continuous sampling stream.

Add observation time, freshness, valid-limit checks, and missing-data behavior before alerting.
Start with a deterministic threshold.
Process, slot, and run identifiers are categories rather than continuous saturation measurements.

### Explanations

Keep `significant_terms` and `rare_terms` explanations behind an explicit request.
Use selected fields, bounded foreground and background intervals, limited buckets, and shared caching.
A single aggregation can still be expensive.

OpenSearch documents additional processing for filtered significance baselines.
See [significant terms](https://docs.opensearch.org/latest/aggregations/bucket/significant-terms/).

Language models remain outside the detection path.
A later explanation step must not decide whether a template is counted, retained, or suppressed.

## 10. Implementation sequence

1. Define timestamp, ownership, version attribution, and count snapshot contracts.
2. Add total scan budgets and durable seven-day ledgers.
3. Add immutable rolling snapshots and safe retry recovery.
4. Implement separate seven-day activity, 90-day definition retention, snapshot cleanup, and worker-state expiry.
5. Provision fixed indices, bounded maintenance, and health reporting.
6. Add shared Shifter snapshots, exact volume, and coverage states.
7. Add Templates navigation, inactive history, detail, labels, and bounded examples.
8. Add bounded semantic search after target-host memory measurements.
9. Complete count, lifecycle, browser, and load acceptance.
10. Extend watched detection through the existing alert plan.

Pin Drain3 to version 0.9.11 before relying on the reviewed internal methods.
Apply the round-18 mining path through the shared recipe.
Verify version attribution and numeric-mask behavior after that change.

This work does not reopen collector routing or add `template_id` to raw logs.
It does not implement a model migration framework.
Routine catalog recreation still requires the recovery behavior defined here.

## 11. Acceptance gates

### Exact counts

Use a fixture ledger with independently known per-record assignments and observation times.

Verify both seven-day boundaries and a full 1,008-bucket interval.
Verify idle expiry, late records, invalid times, and clock skew.

Verify retries before publication, after partial bulk success, after manifest publication, and before local checkpoint commit.
The same source record must contribute once in every case.
Verify collector retries after a scan and across index rollover.
Verify duplicate identifiers, unexpected overwrites, and identifier-ledger expiry.
The source checkpoint alone must never serve as proof of unique record counting.

Verify widening, canonical grouping, predecessor expiry, and independent worker branches.
The sum of displayed version contributions must equal the eligible fixture count.

Verify integer precision through the server and browser.
No arithmetic may round an accepted count.
Verify rolling snapshot totals against independent raw-record counts under each registered source route.
Version-at-assignment fixtures must preserve the chosen mining order and recipe.

### Coverage and retention

Verify initial seven-day backfill and source deletion during backlog.
Verify missing producers, ownership changes, retirement handoff, state reset, and admission limits.

Missing coverage must remain visible.
A missing manifest must never become a zero contribution.

Verify removal from default lists and vectors after seven days without observations.
Verify independent expiry of count contributions, worker ledgers, mining trees, and local publication metadata.
Verify that inactive central definitions remain searchable until their 90-day deadline.
Verify logical and physical central deletion at that deadline, including both boundary timestamps.
Preserve required control state and manual decisions beyond definition expiry.
Verify that inactive queries do not load dormant vectors or trigger worker work.
Verify zero, positive, and unavailable historical-view counts against the selected completed snapshot.

Verify deletion during concurrent observation updates.
Verify that a new matching log extends inactivity expiry without retaining older volume contributions.
Verify that the same observation also extends the central definition's 90-day deadline.
Verify renewal immediately before expiry, including an observation in the current open bucket.
The template must remain visible while that observation waits for the next completed volume window.
Verify deletion followed by widening, a returning template, and another producer publishing the same version.
No path may create an incomplete catalog document.

Verify catalog recreation from current committed snapshots.
Verify separate restoration of central history that seven-day snapshots cannot reconstruct.
Report historical loss when that restoration source is unavailable.
Definitions older than 90 days must not return from old mining state, backups, or abandoned batches.
Valid inactive history must not reappear in the default active list through restoration alone.

### Resource and browser acceptance

Compare a baseline with the new feature under the same replay and ingestion load.
Measure processor usage, peak memory, ingestion delay, search duration, refresh requests, backlog, and publication volume.

Repeat with one viewer and the expected concurrent shift population.
Worker scan counts and scheduled publication work must remain unchanged as viewers increase.

Verify page navigation, hidden-page polling, stale data, missing examples, label conflicts, and semantic failure.
The existing live stream and query page must remain usable.

Accept only if backlog stays bounded and the measured workload fits the configured limits.
Record the actual shard inventory and storage requirement.
Static checks alone do not close these gates.

## 12. Remaining external inputs

Seven-day activity and volume are decided.
Central definition retention starts at 90 days after the latest observation.
Manual labels and notes remain independently durable.
Seven-day counting belongs to the first release.
These policies are agreed; the optimal historical duration remains unmeasured.

Source owners must confirm source coverage and route ownership.
A shifter must review the operator questions and relevance judgments.

Choose the initial retrieval model through the existing evaluation process.
Target-host measurements must establish the serving and worker budgets before deployment.
