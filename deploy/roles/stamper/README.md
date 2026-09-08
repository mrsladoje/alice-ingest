# `stamper`

Stamps every record with its template identity before it reaches OpenSearch.
One long-running service per worker, in-band between Fluent Bit's filters and
its outputs. `docs/TEMPLATES_FIX_PLAN.md` is the design.

## The loop

```
tail / tcp / systemd
  → parsers, doc_id, severity_norm, rewrite_tag                 (unchanged)
  → out_forward   unix_path=/run/alice/stamper.sock  require_ack_response=on  workers=1
  → alice-stamper (Python: family_of → recipe_tokens → drain3 → three fields)
  → in_forward    unix_path=/run/alice/stamped.sock  tag_prefix=stamped.  storage.type=filesystem
  → opensearch outputs, match stamped.family.local / stamped.family.central / stamped.infologger / stamped.ildaemon
  → live lane,  match_regex ^stamped\.(infologger|ildaemon|family\.central)$
```

The `health` tag bypasses the stamper. Forward over a Unix socket and not HTTP
because the parsers set the record time from the log line and an HTTP hop
would replace it with arrival time; the acceptance test proves the time comes
back intact.

drain3 does the stamping, with the frozen recipe, the masker and the four
Drain patches this role vendors in `files/`, copied onto the node and imported.
Any port would be a second implementation of a masker whose byte-identical
output is the identity.

`files/drainbench.py` and `files/masking.py` are copies of the two files of the
same name in `tools/templating`, which is where they are edited. The role keeps
its own copies so it depends on nothing outside its own directory and can be
lifted into another Ansible tree unchanged. `deploy/test_provisioning.py` fails
if a copy and its source ever differ, and skips that check in a tree that has no
`tools/`.

## Three fields on every record

| Field | Meaning |
|---|---|
| `template_version` | `version_id(family, template)`, the exact text the tree returned. Never rewritten. |
| `template_id` | `canonical_id(template)`, the mask-class-collapsed text. |
| `template_status` | `matched`; `new` when the record created the cluster; `unlearned` when the tree is at its state limit and refused a cluster; `no_template` when the recipe reduced the record to nothing. |

Both component mappings carry them. The InfoLogger mapping is `dynamic:
strict`, so an unmapped field would reject every document.

## Exact counting

Per chunk, in this order: stamp every record; send the chunk back through the
second socket and wait for the Forward input's acknowledgement; count every
record into the ledger and append one journal line; acknowledge the origin.

Three mechanisms make the count exact.

1. **Acknowledge after journaling.** Forward with `require_ack_response` is
   at-least-once. A crash before the acknowledgement replays the chunk; a
   crash after it loses nothing.
2. **Deduplicate by chunk identifier.** The journal line holds the chunk
   identifier Fluent Bit sends with every chunk. A resent chunk is stamped and
   returned again, so the index side stays complete, and it is not counted
   again. `create` with `doc_id` refuses the duplicate documents. The
   identifier set covers the resend window: one hour.
3. **Publish the bucket total beside the per-template counts**, so the storage
   tier checks that the parts sum to the whole without trusting the worker.

The observation clock is `collector_time`. A record whose collector time is
older than the 48-hour ledger goes into a flagged late bucket at stamp time:
the total stays exact, the attribution error is bounded and visible.

The one window left open is stated in the plan: a crash after the return and
before the journal line means the resent chunk is re-mined, and its count can
go to a version that differs from the one the indexed records carry. It
affects one chunk per crash and the stamped-against-indexed check shows it.

## State on the worker

Under `StateDirectory=alice-stamper`: a checkpoint (`stamper-state.json`: the
drain tree per family, the ledger, the pending definitions, the chunk
identifiers, the journal sequence) written atomically every
`stamper_checkpoint_seconds`, and the journal since it. On start the journal
is replayed into the ledger before a connection is accepted. The tree and the
journal are independent: counts come from the journal alone, so a tree older
than the journal costs a re-created cluster and never a count.

After a restart every bucket in the ledger is dirty and republished.
Overwrites make that safe.

## What it publishes, every five minutes

- **Bucket documents** into `template-buckets-5m-<day>` and
  `template-buckets-1h-<month>`: one node, one family, one bucket start, one
  resolution; the total and the nested per-version counts. The identifier is
  those four keys, so a republication overwrites. The index is named from the
  bucket's own start so the republication lands where the first write did.
  Two resolutions from one ledger; summation is exact.
- **Definitions** into `template-catalog`, keyed by the version identifier,
  upserted with a union script: programs, origin hosts, log sources, nodes,
  first and last observation, and the observed widening links as sets
  (`widened_into`, `widened_from`).
- **One watermark** per node into `template-catalog`: `published_through` is
  the start of the open five-minute bucket at publication time, and the
  stamper's counters ride along.
- **The worker-side check.** Once an hour, for the previous completed hour
  and each family: a terms aggregation on `template_version` over this node's
  local index must be at or below the stamped count for every version. The
  result is a `kind: check` document in the catalog.

A failed publication keeps every bucket dirty and increments
`publication_failures`; the next cycle retries.

## Health

The stamper writes its counters to `/run/alice/stamper-status.json` on every
cycle. The collector's `fb_health.py` reads that file and merges every
`stamper_*` field into the record it already pushes into `cockpit-metrics`, so
the stamper rides the existing health path: records and chunks, duplicate
chunks, return failures, unlearned and no-template records, late records,
journal bytes and lines, clusters per process, ledger size, publications and
failures, peak memory, socket backlog. Deltas for the counters that move are
computed by the same Lua filter that computes Fluent Bit's own.

## Failure semantics

- **Stamper down.** Fluent Bit buffers to disk and retries without limit
  (`retry_limit: no_limits` on the forward output), bounded by the storage
  limit. Tailed files survive any outage because the tail database resumes.
  The InfoLogger TCP input has no source to re-read, so its loss boundary is
  the buffer cap, as before.
- **Stamper slow.** The input pauses through the same buffer.
- **Stamper crash mid-chunk.** The chunk was not acknowledged; Fluent Bit
  resends it; the identifier makes the resend harmless.
- **State limit.** At `stamper_max_templates` clusters the tree stops learning
  and stamps `unlearned`; the count is in the health record.

systemd restarts the service (`Restart=always`). The unit gets the same memory
limits as the collector.

## Tests

`files/test_stamper.py` covers the Forward codec in every message mode, the
acknowledge-after-handler rule, byte-identical stamps against the offline
miner, widening links, exact counts and chunk deduplication, the failed return
path, journal replay after a crash, late buckets, unstamped records, the state
limit, bucket conservation at both resolutions, definitions and the watermark,
failed publications, republication after restart, the local check and the
cover relation.

`files/test_acceptance_stamper.py` needs a real Fluent Bit binary (it looks in
`/opt/fluent-bit/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, or `$FLUENT_BIT`)
and skips otherwise. It runs the whole loop — tail → forward output → stamper
→ forward input → file output — on a generated corpus and diffs every stamp
against the offline miner: zero differences, the same bar the masker passed.
It also proves the record time survives the loop and that the bucket totals
conserve. A second test kills the stamper between the return and the journal
line and proves the counts and the delivered records agree afterwards.

## Role variables

| Variable | Default | Meaning |
|---|---|---|
| `stamper_drain3_version` | `0.9.11` | Pinned; the stamping path calls reviewed internal methods. |
| `stamper_msgpack_version` | `1.1.2` | The Forward protocol codec. |
| `stamper_socket_dir` | `/run/alice` | Both sockets and the status file. The collector role owns the directory. |
| `stamper_state_dir` | `/var/lib/alice-stamper` | `StateDirectory=`. |
| `stamper_max_templates` | `20000` | Learning ceiling across every family. |
| `stamper_publish_seconds` | `300` | The publication cycle. |
| `stamper_checkpoint_seconds` | `600` | The ledger checkpoint; sets the journal size against the replay time. |
| `stamper_ack_timeout_seconds` | `30` | How long a returned chunk may wait for the Forward input's acknowledgement. |
| `stamper_ledger_hours` | `48` | From `group_vars/all.yml`; the worker keeps this much and nothing older. |
| `stamper_local_check` | `true` | The worker-side stamped-against-indexed check. |
| `stamper_memory_high`, `stamper_memory_max` | the collector's | Same limits as Fluent Bit. |

### Variables the role requires but does not own

| Variable | Owner |
|---|---|
| `template_catalog_index`, `alice_shared_dir`, `alice_shared_contract_file`, `stamper_ledger_hours` | `group_vars/all.yml` |
| `node_id`, `opensearch_http_port` | inventory and `group_vars/all.yml` |
| `fluent_bit_memory_high`, `fluent_bit_memory_max` | `roles/collector/defaults/main.yml`, in scope because both roles run in one play |
