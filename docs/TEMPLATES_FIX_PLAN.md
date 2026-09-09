# Templates fix: stamp every record, count exactly, resolve widening by structure

**Author:** Marko Sladojevic  
**Date:** 8 September 2026.  
**Status:** steps 1 to 6 of section 11 implemented on 8 September 2026 (`roles/sweet_collector`, the collector loop, the mappings and ISM policies, the maintenance checks in `roles/sweet_template_catalog`, the Shifter). Not run on the rig or the farm: step 7 and the four measurements of section 9 are still open. The three checks of section 9 passed locally against Fluent Bit 5.1.2: both Forward plugins accept `unix_path`, the input acknowledges a chunk option, and the output sends Forward mode with the event-time extension and record metadata, which the stamper's decoder accepts in every mode. Decisions taken while implementing, where the text below is silent or differs: the bucket indices are date-named (`template-buckets-5m-<day>`, `template-buckets-1h-<month>`) with an ISM age policy rather than a rollover alias, because a republished bucket must overwrite the document it wrote before, and a rollover alias would put it in a new index; the canonical identifier stays as `template_id` (open decision 1 left as is); the observed widening link is kept as the set-valued `widened_into` / `widened_from` on the definition (open decision 3, kept); the check results and the maintenance reports live in `template-catalog` as `kind: check` and `kind: catalog_maintenance`; the `template-count-check` monitor fires on any failed check; the stamper's counters reach `cockpit-metrics` through `fb_health.py` reading a status file.  
**Supersedes:** in `docs/SHIFTER_COCKPIT_PLAN.md`: the rule that the work "does not add `template_id` to raw logs", the seven-day window, the ten-minute cadence, the read-back producer on the worker, the rolling-total snapshot publication as a manifest and generation chunks in `template-metrics`, and the single-successor `superseded_by` field. What stands from that plan: the identity functions, the observation clock, the coverage statuses, the rule that a stale figure is never shown as current, the idle-producer rule, the 90-day definition retention, and the labels.  
**Does not change:** the templating itself. drain3 with the frozen recipe, the masker and the four Drain patches stay exactly as `docs/SOAK_RESULTS.md` and `docs/TEMPLATING_RESULTS.md` measured them.

## Conclusion

Every record carries its template identity before it reaches OpenSearch.
drain3 stamps it, in-band, on the worker that collected the record.
Fluent Bit hands each record through the stamper and back over a Unix socket, then writes to OpenSearch as today.

Counts are exact by construction and verified by two checks.
The 28-day history lives on the storage tier as bucket documents.
The worker keeps 48 hours of ledger and nothing older.
No raw line leaves its node. The worker publishes definitions, counts and watermarks only.

A stamp is the hash of the exact template text and it is never rewritten.
A widened template is found through a structural cover relation, computed in the Shifter, not through a rewrite.
A search for a template's lines touches only the workers that stamped it in the queried window.

Two phases share one set of plumbing.
Phase one learns per worker, as the catalog does today.
Phase two publishes one central tree and every worker matches against it.

The stack is rebuilt from scratch.
No migration, no compatibility path, no second code path for the old shape.

## 1. The problem

A record in OpenSearch has no template identity today. That has four costs.

- The Shifter rebuilds a regular expression from the template text to find its lines. `shifter.py` carries the reconstruction, the match cache and a client-side re-match.
- The catalog service reads every record back out of OpenSearch and mines it a second time. Most of `template_catalog.py` exists to make that read-back complete: `_seq_no` paging, shard checkpoints, coverage status, the handoff document.
- The Templates page mines at query time.
- Semantic search and anomaly detection have no cheap join from a template to its records.

The fix is to stamp the record once, at the edge, with the same drain3 that mines the archive.

## 2. Decisions

| Decision | Reason |
|---|---|
| Stamp before OpenSearch, on the worker | Both tiers get the field. The local tier holds 96.94 % of lines and never leaves the node. |
| drain3 does the stamping | It is benchmarked and patched. Any port is a second implementation of a masker whose byte-identical output is the identity. |
| An in-band Python service, reached through a Fluent Bit Forward loop | Fluent Bit filters are C, Lua or Wasm, and its output plugins add Go. None can host drain3. |
| Forward over a Unix socket, not HTTP | The parsers set the record time from the log line and the outputs write it as `@timestamp`. An HTTP hop replaces it with arrival time. Forward carries it intact. |
| No raw line leaves its node | The two-tier design and `docs/SEMANTIC_PLAN.md` both require it. The worker publishes definitions, counts and watermarks, never lines. |
| The stamp is the version identifier, the hash of family and exact text | Its meaning never changes. A record stamped T1 matched exactly T1. |
| Never rewrite a stamped record | A rewrite is a reindex per affected document on every widening, cannot reach expired records, and turns a fact into a moving target. |
| History on the storage tier, 48 hours on the worker | The local index keeps eight days. Worker state is lost on reinstall. The storage tier is replicated and retained by policy. |
| Search expansion by the cover relation, computed in the Shifter | Drain only widens, so the structural relation contains every observed transition. It needs no stored link and no mapping change. |
| Route a search by the bucket documents | The `nodes` list on the catalog document is capped and lifetime-scoped. The bucket documents are exact and time-scoped. |
| One central tree is phase two | Per-worker learning causes cross-worker divergence. The plumbing does not change when the tree policy changes. |

## 3. The stamper

### Shape

```
tail / tcp / systemd
  → parsers, doc_id, severity_norm, rewrite_tag                 (unchanged)
  → out_forward   unix_path=/run/alice/stamper.sock  require_ack_response=on  workers=1
  → alice-stamper (Python: family_of → recipe_tokens → drain3 → fields)
  → in_forward    unix_path=/run/alice/stamped.sock  tag_prefix=stamped.  storage.type=filesystem
  → opensearch outputs, match stamped.family.local / stamped.family.central / stamped.infologger / stamped.ildaemon
  → live lane,  match_regex ^stamped\.(infologger|ildaemon|family\.central)$
```

The `health` tag bypasses the stamper and keeps its own output.

### Fluent Bit configuration

- One `forward` output matching every log tag, with `unix_path`, `require_ack_response: on`, `workers: 1`, `retry_limit: no_limits` and the existing filesystem storage limit. The current limit of ten retries would drop records after a few minutes of stamper outage. One worker keeps one connection, so the stamper sees chunks in the order Fluent Bit flushed them.
- One `forward` input with `unix_path`, `unix_perm`, `tag_prefix: stamped.`, `threaded: on` and filesystem storage. Filesystem storage here means a stamped chunk is on disk before the OpenSearch outputs take it, so a Fluent Bit crash after the return does not lose it.
- Every OpenSearch output and the live lane match the `stamped.` tags. Nothing else in the outputs changes: `id_key: doc_id` and `write_operation: create` keep the index write idempotent.

### The service

One process per worker, one thread for drain3. The drain3 tree is not thread-safe, and one thread is enough at worker rates. The soak arm in section 9 measures the ceiling. The Forward server accepts more than one connection and still processes chunks one at a time.

Dependencies on the worker: drain3 and jsonpickle as today, plus msgpack for the Forward protocol, including its event-time extension type that carries seconds and nanoseconds.

Per record: `family_of` from the contract, `recipe_tokens` from `drainbench`, `mine` on the persisted tree for that family. Three fields are written.

| Field | Type | Meaning |
|---|---|---|
| `template_version` | keyword | `version_id(family, template)`, the exact text the tree returned |
| `template_id` | keyword | `canonical_id(template)`, the mask-class-collapsed text |
| `template_status` | keyword | `matched`; `new` when the record created the cluster; `unlearned` when the tree is at its state limit and refused a cluster; `no_template` when the recipe reduced the record to nothing |

All three go into both component mappings. The InfoLogger mapping is `dynamic: strict`, so an unmapped field rejects every document.

Per chunk, in this order:

1. Stamp every record.
2. Send the chunk back through the second socket with a chunk option, and wait for the Forward input's acknowledgement.
3. Count every record into the ledger and append one journal line.
4. Acknowledge the origin.

Section 4 states what the journal line holds and why the order is this one.

Persisted on the worker: the drain3 tree per family, through the existing in-memory persistence handler; a ledger checkpoint on a timer; the journal since the last checkpoint. On start the service replays the journal into the ledger before it accepts a connection. The tree snapshot and the journal are independent. Counts come from the journal alone, so a tree older than the journal costs a re-created cluster, which the catalog already records as an incarnation, and never a count. After a restart the stamper republishes every bucket in its ledger. Overwrites make that safe.

The unit gets the same memory limits as the collector. The socket directory is owned by the collector's user. The stamper reports its own counters through the existing health path into `cockpit-metrics`: records and chunks per interval, duplicate chunks, journal bytes, clusters per family, publication failures, peak memory, and the socket backlog.

### Failure semantics

- Stamper down: Fluent Bit buffers to disk and retries without limit, bounded by the storage limit. Tailed files survive any outage because the tail database resumes. The InfoLogger TCP input has no source to re-read, so its loss boundary is the buffer cap. The soak already measured that boundary at two thirds of InfoLogger traffic. This is not new exposure. It is the existing exposure with one more process in front of it.
- Stamper slow: the input pauses through the same buffer, as it does when OpenSearch is slow.
- Stamper crash mid-chunk: the chunk was not acknowledged. Fluent Bit resends it. Section 4 makes the resend harmless.
- systemd restarts the service. The tree persistence bounds the mining state as today, through the existing state limit and its gap reason.

### What this deletes

The worker half of `template_catalog` is replaced by the stamper. The following has no purpose once every record passes through the stamper exactly once: `_seq_no` paging, `index_shards`, `shard_checkpoints`, the scan coverage arithmetic, the handoff document, and the per-record `doc_id` set in the ledger. The snapshot publication in `snapshot.py`, the `template-metrics` index, and the manifest, generation, reader-pin and snapshot-age constants go with it. The central maintenance job stays. The Shifter's regular-expression reconstruction and its match cache go. Mining at query time goes.

## 4. Exact counting

### The invariant

For every node, family and bucket: the sum of the per-template counts equals the number of records the stamper accepted whose collector time falls in that bucket.

The observation clock stays `collector_time`, the accept clock. A file re-read after a long outage is new observation time. That is correct for volume and the contract states it.

### Three mechanisms make it true

1. **Acknowledge after journaling.** Forward with `require_ack_response` is at-least-once. The stamper counts a chunk, appends one journal line, then acknowledges. A crash before the acknowledgement replays the chunk. A crash after it loses nothing.
2. **Deduplicate by chunk identifier.** The Forward protocol sends a unique chunk identifier with every chunk when acknowledgements are on. The journal line holds that identifier and the per-bucket deltas the chunk produced. A resent chunk whose identifier is in the journal is stamped and sent back again, so the index side stays complete, and it is not counted again. `create` with `doc_id` refuses the duplicate documents. This replaces the per-record identity set, and it is far smaller. The identifier set only needs to cover the resend window, so it is held for one hour.
3. **Publish the bucket total beside the per-template counts.** The storage tier can then check that the parts sum to the whole without trusting the worker.

The per-chunk order in section 3 leaves one bounded window. A crash after the return and before the journal line means the resent chunk is re-mined, and the count can go to a version that differs from the one the indexed records carry. It affects one chunk per crash, and the stamped-against-indexed check shows it. Storing every record's stamp in the journal would close the window at the cost of a journal entry per record. That is not worth it.

### Bucket documents

Every five minutes the stamper publishes the buckets that changed since the last publication. A bucket document is one node, one family, one bucket start, one resolution. It carries the total and a nested list of version identifier and count. Its document identifier is those four keys, so a republication overwrites and never adds.

Two resolutions come from the same ledger. Summation is exact, so the hourly figure loses nothing.

| Index | Bucket | Retention | Purpose |
|---|---|---|---|
| `template-buckets-5m` | 5 minutes | 3 days | live series, the open-bucket routing question in section 6 |
| `template-buckets-1h` | 1 hour | 35 days | the 28-day window and any baseline |

Retention is an ISM policy, the same mechanism as the log indices. 35 days covers 28 days plus the late horizon plus a margin.

A bucket stays writable while it is inside the worker's 48-hour ledger, so a record stamped late after a backlog lands in the right bucket and the next publication corrects the document. A record stamped later than 48 hours after its collector time goes into a flagged late bucket at stamp time. The total stays exact. The attribution error is bounded and visible.

### Definitions

On the same five-minute cycle the stamper publishes every template version it created or widened since the last cycle. The document is keyed by the version identifier, so 228 workers upserting the same text produce one document. It carries the family, the exact text, the programs, the origin hosts, the log sources, first and last observation, and the observed widening links as a set. The central definition retention of 90 days is unchanged.

### Watermarks

Each publication also upserts one watermark document per node: the end of the latest bucket it published and the publication time. A window sum is complete when every node with a bucket in the window has a watermark at or past the window's end. A node whose watermark stops advancing falls under the idle-producer rule from the cockpit plan and the sum reports partial coverage naming that node.

### Two checks prove it

- **Conservation.** For every bucket document, the sum of the nested counts equals the total. A mismatch is a ledger bug and raises an alert through the existing alert path.
- **Stamped against indexed.** A terms aggregation on `template_version` over the local, central and InfoLogger indices, scoped to the node and the bucket, must be less than or equal to the stamped count. Equality means nothing was lost after stamping. The difference is a per-template loss metric. It runs on the worker for the local index and centrally for the shared indices, inside the retention of each.

Both checks run from the central maintenance job on a timer. Their results are documents, not log lines, so the Shifter can show them.

### The window figure

The 28-day volume of a version is a sum over the hourly bucket documents in the window, at a cutoff the Shifter chooses from the watermarks. It carries the coverage status from the contract. It is never estimated from partial data, and a stale figure is never shown as current. The Shifter caches the sum per view refresh.

A baseline comparison, the current bucket against the same slot over the previous 28 days, is a consumer of the hourly documents. It is not part of the invariant.

### Sizes

Rough estimate, assuming 150 active templates per node per five-minute bucket. Section 9 measures the assumption.

| Resolution | Count entries per day, farm-wide |
|---|---|
| 5 minutes | about 10 million |
| 1 hour | about 0.8 million |

## 5. Identity under widening

### What Drain does

Inside one tree the cluster keeps its integer identifier and its text mutates. The patched `_add_tokens` reports `cluster_template_changed`. At that instant the stamper holds the old text and the new text, so the link "T1 became T2 on this node at this time" is an exact observation. `template_catalog.py:1203` already records it.

The line that caused the widening is stamped T2. Every earlier line stays T1. The T1 fragment is usually small, because Drain widens a template mostly in its first few lines, when every token is still literal.

### The stamped record never changes

A record stamped T1 matched exactly T1. That is a fact and it stays. The plan's rule stands: a record counts against the exact version returned when it was mined, and volume is never moved between versions.

### The cover relation

Template W covers template N when they share a family and a token count, and at every position W has `<*>` or the same token as N. `<*>` stands for exactly one token in Drain, so the test is exact. A mask class such as `<NUM>` sits between: `<*>` covers it, and it covers only itself.

```
W:  sent <*>   bytes to <IP>
N:  sent <NUM> bytes to <IP>     N fits under W
X:  sent <*>   <*>   to <IP>     X covers both, and also "sent error report to <IP>"
```

- Narrower templates are safe to include wholesale. Every line stamped N also fits W.
- Wider templates are not. X holds lines that never fit W. An ancestor's lines are included only after a re-match against W's pattern, which `line_matches_template` already performs on the returned page.

Drain only widens, so every observed transition is a cover relation. The structural relation therefore contains every observed link, and search uses the structural relation alone. The observed link stays as provenance, because it names which of several parents a cluster actually became. It becomes a set: one version can be widened into different successors on different workers when different tokens vary first.

The Shifter computes the relation in memory when it builds a view. It groups versions by family and token count and compares all pairs inside a group. The largest group is a few hundred versions. Nothing is stored and no mapping changes.

The canonical identifier collapses mask classes only. When a literal becomes `<*>`, the canonical identifier changes too. The cover relation contains the canonical grouping as a special case. Section 10 lists the decision whether to keep both.

Drain at similarity 0.4 produces some very wide templates with many descendants. The view shows the descendant count beside a selected template so the operator sees what a search will expand to.

### One worker widened, the others did not

Worker A stamps T2. Worker B still stamps T1. Both versions are in the catalog. T1 stays active because B still observes it and last observation is a maximum across workers. A search for T2 expands to its descendants and finds B's lines. A search for T1 expands the same way, and the re-match keeps the lines that fit T1. B converges when it meets the same variation. If it never does, the two versions are two observations and they coexist correctly.

## 6. Search and routing

### Lines for a template

1. Expand the selected version to itself and its descendants under the cover relation.
2. Take the node set from the bucket documents: a terms aggregation on `node`, filtered by the expanded version set and the query window.
3. Query the named local indices for those nodes, plus the central and InfoLogger indices, which live on the storage tier and fan out to nothing. Filter with one terms query on `template_version`.
4. If the operator asked for an ancestor's lines too, fetch them and re-match against the selected template on the page.

Route by `node`, the collector, not by `origin_host`. The local index lives where the collector wrote it.

The open five-minute bucket is not published yet. A node that started emitting the template three minutes ago is missing from the node set. The page shows the cutoff beside the result, as the plan already requires, and offers "search every node" for that case.

### Why routing matters

The local indices are one shard per worker. A search over the local pattern wakes every worker's data node, and the answer arrives when the slowest physics node answers. Naming the indices keeps a template search on the few nodes that have it.

### Consumers that become cheap

- Semantic search embeds templates, not lines. A query resolves to version identifiers, then to lines by one terms query.
- Anomaly detection reads a count series per version per node from the bucket documents. No mining at detection time. Which series get a detector is a separate decision under the existing model budget of the anomaly-detection plugin. This plan supplies the series, not the detectors.
- The Templates page volume is a sum over bucket documents.

## 7. Phase two: one central tree

Per-worker learning is the source of cross-worker divergence. Phase two removes it at the source and changes nothing in the plumbing.

1. The central maintenance job builds one tree per family by feeding every published template version into a drain3 miner in a fixed order.
2. It publishes the tree state as a document. The stamper pulls it on its five-minute cycle and swaps it in atomically.
3. The stamper calls `match` against the central tree. Its docstring: "New cluster will not be created as a result of this call, nor any cluster modifications." On a miss it learns in its local tree and stamps the local version, as in phase one. The miss never leaves the node as a line. It leaves as a definition on the next cycle.
4. Widening then happens once, centrally, and every worker switches at the same publication. Records before the switch carry T1, so the cover relation still applies, but the switch is farm-synchronous.

Phase two starts only if phase one's cross-worker divergence proves to matter in use. Measure it before building it: the share of versions active on more than one node whose cover set differs across nodes.

## 8. What this does not do

- Rewrite a stamped record, ever, for any reason.
- Stamp a per-cluster identifier that survives widening. It is local to one worker's tree, two chains that converge on one text would carry different identifiers, and the stamp would stop being a fact about the record.
- Port the masker or Drain to Lua, Wasm, C or Go.
- Add Kafka or replace Fluent Bit.
- Use query-time pattern analysis in OpenSearch Dashboards as a substitute for stamping.
- Keep the read-back producer or the snapshot publication beside the new path.
- Ship any raw informational line off its node.

## 9. Measurements and checks that gate the implementation

| Question | Method | Decides |
|---|---|---|
| Cost of the Forward loop, both directions | One soak arm on the existing rig, core-seconds per million records, against the 11.19 collector baseline and the 19.73 drain3 figure | Whether one stamper thread per worker holds at the soak rate |
| Active templates per node per five-minute bucket | Replay the corpus per node, count distinct versions per bucket | The bucket index sizes in section 4 |
| Template return intervals | From the same replay, the gap between observations of one version | The 28-day window, which `docs/SHIFTER_COCKPIT_PLAN.md` asked to measure before changing |
| Unmatched rate against a frozen tree | Freeze a tree on the first half of the corpus, replay the second half through `match`, count misses | Whether phase two is affordable and how large the local fallback tree grows |

Three checks need no measurement, only the packaged Fluent Bit on AlmaLinux and the soak rig.

- Both the Forward input and the Forward output accept `unix_path`. The documentation lists both.
- The Forward input answers with an acknowledgement when the client sends the chunk option. The return path in section 3 depends on it.
- Which Forward message mode the output sends. The stamper's server must accept that mode, and the plain and packed modes differ in framing.

## 10. Open decisions

- Keep the canonical identifier and its grouping, or let the cover relation replace it. Replacing it means one concept instead of two and one field fewer on every record.
- The window length. 28 days is the working figure until the return-interval measurement is in.
- Whether the observed widening link is worth keeping as provenance once search no longer needs it.
- Where the storage-tier checks and phase two's tree build run. The central maintenance job is the working answer.
- The ledger checkpoint interval on the worker. It sets the journal size against the replay time on start.

## 11. Implementation sequence

1. **Contract.** Add the three record fields, the bucket document, the watermark document, the set-valued link, and the 28-day, five-minute and 48-hour constants. Remove the seven-day constants and the snapshot constants.
2. **Mappings and retention.** Both component mappings gain the three fields. Two bucket indices with their ISM policies. The watermark and definition documents stay in the catalog index.
3. **Stamper role.** A new role, not more code in `template_catalog`, so the review sees one service with one job. The Forward server, the return path, the journal, the ledger checkpoint, the three publications, the health counters. Its acceptance test replays the corpus through the real Fluent Bit binary and the stamper, and diffs every stamp against the offline miner. Zero differences, the same bar the masker passed. A second test kills the stamper mid-chunk and proves the counts and the index agree afterwards.
4. **Collector role.** The Forward loop, the `stamped.` matches, the unit's memory limits and the socket directory. Syntax-check every playbook before the push.
5. **Catalog role.** Delete the worker producer and the snapshot publication. Keep the central maintenance job and add the two checks.
6. **Shifter.** Terms queries by version, the cover relation, the cutoff from watermarks, routed search, the descendant count, the "search every node" option. Delete the regular-expression reconstruction, the snapshot reader and query-time mining.
7. **Soak.** The arm from section 9 first, then the full acceptance on the rig, then the farm.
