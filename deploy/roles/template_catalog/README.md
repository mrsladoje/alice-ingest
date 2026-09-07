# `template_catalog`

Carries template identity off a worker without carrying its informational logs.

## The problem it exists for

The collector routes by severity. Measured on 20.9 million real process-tree
lines, **96.94 % of them never leave the node they were written on** — that is
the whole point of the two-tier design, and it is why the storage tier is
affordable.

It is also a hole. `docs/SEMANTIC_PLAN.md` requires a shared template catalog,
and a catalog built from the durable tier alone would contain the templates of
3.06 % of the tree. The 96.94 % that stayed local would be invisible to search,
not because their templates are uninteresting but because their *lines* were
cheap.

The plan states the requirement directly: *"Send bounded catalog updates and
aggregate counts without forwarding those raw informational lines."*

## What it does

One oneshot service on a timer, on each worker:

```
  local index  ──►  frozen recipe  ──►  canonical templates  ──►  template-catalog
  (this node)       (mask + mine)       + counts + programs      (storage tier)
```

It reads **every route this node writes to** — its own
`application-logs-local-<node_id>`, plus `application-logs-central` and
`infologger` scoped to the records this node itself wrote — applies the same
per-family recipe the archive miner uses, and sends one document per distinct
template. A raw informational line still never crosses the network.

Reading the node-local index alone was the first design and it was wrong in a
way that is easy to miss: it is the *informational* traffic that stays local, so
a local-only catalog holds no InfoLogger templates at all, no daemon log, and
none of the warnings and errors — the exact records the routing sends straight
to durable storage. The two shared indices are read with a `node` filter, so
three workers reading one shared index do not each add the same count.

## Counts are absolute, and a retry cannot double them

What the update sends is not "add three". It is "this node's count for this
template is 47" — the cluster's whole size in this node's mining tree — and the
script SETS it. An increment cannot be retried, and a pass that dies after the
bulk write is exactly the case that has to be retried.

On top of that every write carries a pass number, and the script refuses a pass
it has already applied to this node's entry. That stops an old pass overwriting
a newer one after a long stall.

## A template that generalises carries its count with it

Drain rewrites a cluster's template as it sees more of the shape: `reader 7
opened` becomes `reader <NUM> opened`. The document identifier is a hash of that
text, so it moves when the text does.

Left alone, that splits one template across two documents which each under-count
— the reviewer's reproduction was one pass giving one document with a count of
two, and two passes giving two documents with a count of one each. The service
remembers the identifier each cluster was last published under and, when it
moves, retires the old document: this node's contribution is removed from it and
`superseded_by` points at the new one. Whatever other nodes put there stays,
because they have not generalised yet and their counts are still true of the
text that document holds.

## The drain tree is state, not a cache

Drain is incremental and order-dependent. A tree rebuilt from empty on every
timer run mines the same messages into different templates depending on which
ten-minute batch they fell in, and the catalog then holds two documents where
there is one template. The tree is persisted per family under
`StateDirectory=alice-template-catalog` and loaded back at the start of each
run.

The service also installs the same FLOAT/NUM merge rule the offline miner uses.
It is a monkeypatch on drain3's `Drain` class rather than configuration, so it
does not travel with the recipe tables and has to be installed explicitly.
Without it a slot holding both a float and an integer becomes a bare wildcard
here and a `<NUM>` in the archive — round 6 measured that as the difference
between 6.3 % and 1.0 % of lines landing on a contentless template.

## The position is a shard sequence number, bounded by the checkpoint

This took four answers and only the fourth is sound. The first three are worth
recording, because each one looks correct and each one is wrong the same way.

| Ordering key | Why it was chosen | Why it fails |
| --- | --- | --- |
| `collector_time` | the collector stamps it, so it is on every record | fixed **before** indexing. A retried chunk lands behind a position already saved past it. |
| `ingest_time` | set by OpenSearch, so it must reflect the cluster | stamped when the ingest node **receives** the document, still before the write. Same failure. |
| highest `_seq_no` seen | assigned by the primary, in write order | assigned before the write reaches Lucene. With concurrent writes number 1 can be searchable while number 0 is still in flight, and remembering 1 steps over 0 for good. |
| `_seq_no` bounded by `global_checkpoint` | the checkpoint is the only value that means *completed* rather than *started* | — |

A fifth thing is needed and it is not an ordering key: **the index the key
belongs to has to still be the same index.** Everything after the first request
addresses it by NAME, and a name is not an index. Delete it and recreate it
under that name in the middle of a scan — a reindex behind an alias, someone
rebuilding a source index — and the new index answers with sequence numbers that
start again at zero, which mean nothing against the checkpoint taken from the
index before it. Its records were then marked as read under the old index's key,
so the next pass found no mark for the new one, read it from the beginning, and
counted every record a second time. The UUID is re-read before each page is
yielded, and a UUID is never reused, so a page that still answers with the same
one came from the index whose checkpoint bounds it.

Every operation at or below the global checkpoint has completed on every in-sync
copy, so nothing below it can still arrive. Each pass therefore reads the
checkpoint **first**, refreshes **second**, and scans **third**, bounded at the
checkpoint. Reading the checkpoint after the refresh would defeat it: the
boundary has to be the one that held before anything was made searchable.
Records above the checkpoint are not skipped, only deferred — a later pass reads
them once the checkpoint has moved past.

The position is keyed by concrete index, **index UUID** and shard. The UUID is
part of the key for the same reason the concrete name is: sequence numbers start
again at zero in a new index, and a name can be reused. Delete an index and
recreate it under the old name and every record in it sits below the saved mark,
so all of them would be skipped for ever. A changed UUID makes it an index this
node has never read, which is what it is.

Each shard gets its own budget and the starting shard rotates between passes.
One shared budget always starting at shard 0 is not a fairness preference but a
starvation bug: a shard busy enough to fill the budget on its own is read every
pass, and the shards behind it are never read at all.

A search or a refresh that FAILED on a shard is rejected rather than read.
OpenSearch reports a timed-out search with 200 OK and whatever hits it
collected, and an empty page from a failed shard is byte-identical to an empty
page from a shard with nothing left in it. Only the flag beside the hits
separates *there is no more* from *I could not tell you*.

**Failed is not the same as `not everywhere`,** and reading them as the same
thing stopped the catalog reading anything at all. A refresh reports `total` as
the number of configured shard **copies**, so one node with
`number_of_replicas: 2` — the shipped worker layout — reports two total, one
successful and zero failed on a completely healthy refresh, and so does every
rolling restart for a while. `failed` is the field that means something went
wrong; an unavailable copy is not a failed operation, and the copy that answered
is the primary. A **search** keeps the arithmetic, because its `total` counts
shards rather than copies: every shard must be accounted for as successful,
skipped or failed.

**Everything that has to move together is in one file, replaced atomically:**
the mining trees, the read position per index, the pass number, and the
identifier each cluster was last published under. Splitting them was a defect
rather than a detail. A bulk write followed by a separate progress save has a
window in which the write landed and the save did not, and the retry counted
every record again.

The file is `$STATE_DIR/catalog-state.json`, and `StateDirectory=` in the unit
creates it. Both the file and its directory are synchronised: the rename is what
publishes the new state, a rename is a directory write, and syncing only the file
leaves it in write-back cache — a power cut there restores the old state file and
the batch it describes is counted a second time.

If the catalog is gone, the node republishes everything it knows rather than
staying silent about templates whose documents no longer exist. *Gone* is judged
by the index **UUID**, not by whether the name resolves and not by a document
count.

**The destination is fixed before the payload is sent, never discovered after
it.** This took three answers. Reading the identity before the bulk write was
the first, and on a node that has never published it read `null`, because the
bulk write is what creates the index — and `null` compares against the new
identity as unchanged. Reading it after the write was the second, and it is
worse, because it fails in the direction that hides the problem: replace the
catalog in the window between the write landing and the writer learning that it
did, and the lookup **succeeds**. It names the empty replacement. The node
records that as the index it wrote to, the next pass compares that identity
against itself, finds no change, and reports itself idle over documents that no
longer exist.

The third answer is to stop asking a question the order of operations cannot
answer. The index is **created explicitly** when it is absent — leaving that to
the bulk write is what made the identity unknowable until afterwards, and
creating an index with no body still applies the composable template, so the
mappings are the shipped ones either way. Another node winning that race is not
an error; its index is this node's index. The identity is then read and written
down before the payload goes anywhere.

Read before the write, the same race can only be wrong the other way round: what
is recorded is where this pass *intended* to write, so a write that lands
somewhere else leaves the two disagreeing on the next pass — which is exactly
what makes it a rebuild instead of a silence.

Two things stop the pass rather than publish. A destination that cannot be read
at all, because an identity this node cannot read must not be recorded as *no
change*. And a destination that is not the catalog the pass was **planned**
against, because this batch holds only what the pass read, while a replacement
needs everything the trees hold — that is the next pass's work, not this one's.
Both leave the batch pending, and republishing it is idempotent, so the cost of
either is one replayed batch.

**It is a rebuild and not a reset.** Throwing the local state away sends the node
back to the source indices, and those are the short-lived half of this system:
informational records age out of the node-local index long before the templates
mined from them stop being true, which is the reason this service exists at all.
The mining trees hold every count this node has published and they survive, so a
replacement keeps the trees, the read position and the accumulated programs,
clears only `published`, and republishes every cluster the trees hold. The tree
is the record of what was seen; the catalog is only where it was sent. A count is read through the refresh
interval, so a count taken straight after a pass reads zero and every pass
concluded the catalog had been wiped. A resolving name fails in the other
direction: delete the catalog and recreate it under the same name and the name
still resolves, while every document this node published is gone — nothing is
republished, nothing new is read, and the node reports itself idle against an
empty catalog for as long as it runs.

Pass numbers are milliseconds since the epoch, but the clock is only a floor.
Every update refuses a pass at or below the one already recorded for the node, so
a clock stepped backwards — NTP correcting, a virtual machine restored from a
snapshot — produces updates that every document ignores while the run reports
success and commits its position. The number is therefore the larger of the clock
and one above what this node is known to have written: from local state, or, when
that is gone, from the `last_pass` the catalog itself records while the node's old
contribution is being cleared.

That number is written down in `issued` **before** anything is written with it,
and the floor is the higher of `pass` and `issued`. It has to be, and the reason
is not obvious: `CLEAR` removes this node from the document's `nodes` list, and
the listing that finds documents to clear filters on `nodes` — so once the clear
has run it can no longer find what it cleared. A crash between the clear and the
first commit leaves a node that cannot rediscover the number the clear used,
falls back to a rolled-back clock, and is fenced out by the guard its own clear
had just written.

## How these are tested

The unit tests check what is computed and what is sent. They cannot check what
the catalog ends up holding, because that is decided by a Painless script and
emulating one in Python would be testing the emulator.

So the properties above are checked against a real OpenSearch by
`tools/collector/mappingcheck.py --catalog`, which reads the PUBLISHED
documents after: a template that generalises across two passes, a pass that
dies before saving its state and is retried, a write that is acknowledged but
not yet refreshed, a record indexed second with earlier timestamps, a page
boundary, one node generalising while another still counts the old text, a
generalisation that must carry its programs, a catalog recreated under the same
name, a catalog replaced after a source record has aged out, and a shard whose
replica cannot be assigned.

Four of them need the cluster to misbehave on cue rather than merely to be in an
awkward state, so those reach it through a forwarding proxy that breaks one
named request, or acts in the gap between a request being applied and the client
being told so: a destination lookup that cannot complete, a source index deleted
and recreated while a scan is reading it, and a catalog replaced in the window
between the bulk write landing and the writer learning that it did — the last on
a first publication and again on a node whose trees already hold history. None
of them has a knob and all of them are ordinary on a real cluster.

The verifier's own reads are validated the same way the service's are, and for
the same reason: every sequence is judged on the documents the reader returned,
so a read that comes back short makes the judgement wrong in the direction that
passes. A stale contribution nobody could see is a stale contribution nobody
reports. `tools/collector/test_mappingcheck.py` covers that without a cluster.

## Why the recipe is copied rather than reimplemented

`tasks/main.yml` ships `tools/templating/drainbench.py` and `masking.py` onto
the node, and the service imports them. It does not carry a second copy of the
masking rules or the recipe tables.

That is deliberate. A template mined on a worker and the same template mined
from the S3 archive have to be the same string, or the canonical identifier is
not canonical and the two catalogs disagree. A second copy of the rules would
drift, and **the drift would be invisible** until someone compared them.
`test_template_catalog.py` asserts every family the collector emits has a recipe
in that shipped module.

## The two things that can silently go wrong

**The document identifier must be stable across processes.** The first version
used Python's `hash()`, which is randomised per process, so the same template
would have grown a new document every ten minutes instead of a count. It is now
sha1 of the family and the template text, and a test runs three subprocesses to
prove it.

**The read position must resume, not restart.** It is kept per node and per index
inside the catalog itself, so a restart resumes where the last pass stopped.
Wiping the catalog therefore forces a clean rebuild, which is the behaviour you
want from a wipe.

## Cost

The timer is every ten minutes with a randomised delay, so three workers do not
mine and bulk on the same tick. One pass is capped at 200,000 lines, so a worker
that was offline does not try to mine a backlog in one go.

The unit runs `Nice=10` with `IOSchedulingClass=idle` and `MemoryMax=512M`. The
mining tree is bounded by the recipe's depth and `max_children`, not by the
corpus, so memory does not grow with the log volume.

The catalog is a cold path by construction: an embedding is paid once per new
template, and a template appearing ten minutes late costs nothing.

## What it does not do

It does not decide the canonical duplicate group. Two templates that mean the
same thing but mine differently are still two documents here; collapsing them is
a decision the retrieval benchmark makes, not a collector-side one.

It does not read another node's index. The no-cross-worker rule holds here for
the same reason it holds in the collector.

## Why the scan orders by sequence number and stops at the checkpoint

This is the fourth answer to the same question. The first three were all the
same mistake in different clothes: each trusted something that does not mark
when a record became readable.

| Tried | What it records | Why it fails |
|---|---|---|
| `collector_time` | when the collector saw the line | a buffered or retried chunk keeps it and is indexed hours later |
| `ingest_time` | when an ingest node *received* the document | processing, primary execution and replication all follow it, and can follow it out of order |
| `_seq_no`, remembering the highest seen | the primary assigning the number | the number is handed out **before** the write reaches Lucene, so number 1 can be searchable while number 0 is still in flight — reading 1 steps over 0 for ever |

The ordering key is `_seq_no`, and the **boundary** is the shard's
`global_checkpoint`: every operation at or below it has completed on every
in-sync copy, so nothing below it can still arrive. `max_seq_no` is the highest
number *assigned* and is not a boundary at all.

Three calls per index per pass, and the order between them is the argument:

1. **read the checkpoint** — everything at or below it has completed;
2. **refresh** — which therefore makes all of that searchable;
3. **scan**, bounded at the checkpoint.

Refreshing before reading the checkpoint would let the checkpoint include
operations the refresh did not cover. Scanning past it would read from the
region that is still settling and remember a position inside a gap.

Anything above the checkpoint is read by a later pass, once it has moved. On a
ten-minute timer that costs nothing, and the extra refresh is one per index per
pass against an index that already refreshes every thirty seconds.

Two more consequences shape the implementation:

- `_seq_no` is per shard of a **concrete** index and every shard starts at
  zero, so a cross-shard sort mixes unrelated sequences. Six documents over
  three shards come back as 0, 0, 0, 1, 1, 1. Each shard is read on its own
  with `preference=_shards:<n>`, and the position is a sequence number per
  concrete index per shard — a rollover alias has several backing indices and
  each has a shard 0.
- Where copies disagree on the checkpoint, the lowest is used. A replica may
  report a staler value than the primary and the smaller number is the safe one.

### How this is tested

The concurrent gap cannot be produced on a live cluster without pausing an
indexing thread, so it is tested where the decision is made: the scan is driven
against a stubbed shard reporting `max_seq_no` 1 with a `global_checkpoint` of
-1 and a searchable record at sequence number 1. The pass must read nothing and
must not search that shard at all. A second test moves the checkpoint and
asserts both records are then read, in order.

Removing the checkpoint bound fails five of those tests, which is what says they
are worth having.

The other place refresh visibility is a correctness question is the cleanup
after a state loss, which reads the catalog with a search to find what this node
had published. That path refreshes first, and not doing so was a defect: a node
that lost its state inside the catalog's 30-second refresh window found nothing
to clear and added its rebuild on top of counts it could no longer identify.

## Used by

`docs/SEMANTIC_PLAN.md` Stage S0, which cannot pass while the catalog is
incomplete, and every retrieval stage after it.
