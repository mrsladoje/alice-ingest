# THANASIS_PLAN

What we port from Thanasis's `logstack` into `alice-ingest`, and how.

Source of the comparison: the architecture audit artifact, reviewed 30 Jul 2026.
Their repository is vendored at `thanasis/logstack/`.

A few later items are not ports. They are decisions that surfaced during the
comparison and have no better home. They are marked where they appear.

Rejected items are not written here. If a thing is absent from this file, we
looked at it and we are not doing it.

## The grounding rule

Thanasis had a live Run 3 stream from the EPN farm. We do not. Run 3 is
finished. We have only the old logs in the S3 bucket, which we replay
synthetically, and that corpus is a subset of what exists.

Amended 12 Aug 2026. Lubos confirmed we get access to the EPN nodes
themselves. That removes the wait for a larger S3 export: the logs can be read
where they are written. It also gives us something S3 never could — the real
file layout, with real paths, real names and real rotation. Our tail patterns
and our `source_file` handling are guesses until we see it.

The order of work follows from that:

1. Adapt this plan. Done in this document.
2. Implement every item that does not need EPN data.
3. Take EPN access, survey the data, then implement the items that were waiting.

So no item enters this plan on merit alone. Each item must also be justified by
data we can actually read. If the source data is absent, the item waits.

## Status key

- **TAKE** — port it, in the form written here.
- **ADAPT** — take the idea, not their implementation.
- **RECORD** — no build work; write it into the deviations list as a known trade.

---

## Rules that apply to every port

### R1 — Rewrite every ported `Time_Format`

Their parsers use `Time_Format %H:%M:%S`, with no date part. Fluent Bit then
fills in the current date. Any log line that crosses midnight, or that is
replayed on a different day, gets a wrong date and no warning. Our S3 replay
does exactly that, so the fault would be certain, not merely possible.

Every parser we port must read a full date, or read an epoch, before it enters
`deploy/roles/collector/templates/parsers.yaml.j2`. Our three current parsers
already do this; ported ones must match.

### R2 — Ported parsers attach to source tags, before the severity split

He routes by source: the tag names the program, and each tag gets its own topic
and its own index. We route by severity: `rewrite_tag` sends every record to
`family.info` or `family.other` (`deploy/roles/collector/templates/collector.yaml.j2:169`).

Our routing stays. His costs four edits in lockstep for every new log source.

Our pipeline already parses per source before it routes per severity — `dds_text`
on the `dds` tag, `stdout_root` on the `stdout` tag. Any ported parser chain
attaches at that point. It never changes the output routing.

### R3 — Per-node values come from the environment, not from Ansible

`collector.yaml.j2` reads `{{ node_id }}`, `{{ log_root }}` and the ports as
Ansible variables. Every one of those values is decided centrally and pushed
down.

Fluent Bit reads environment variables directly. He proves it: `node
${MY_NODE_NAME}` (`thanasis/logstack/config/fluent-bit-worker/flb-worker.yml:140`).

Move every per-node value to an environment variable. Ansible writes an
`EnvironmentFile` for the systemd unit, once, at install time.

Amended 12 Aug 2026. The original reason for this rule was portability to
Kubernetes, which is no longer on the table. The surviving reason is Item 3: a
machine cannot register itself if its own identity only exists in a central
inventory. This rule is what lets a node answer "who am I" at boot without
asking anything.

---

## Items

## Item 1 — The parser library (28 regexes)

**Status: ADAPT. Waiting on EPN access, not on design.**

His parser chains extract numbers from message bodies: per-timeframe processing
time, CTF size, decoding-error counts, tracks and vertices per timeframe,
encoder word counts, beam position. Our detectors currently see only
infrastructure signals, so they can say the log platform is sick but not that
the experiment is sick.

This is the single largest gain available to us. Nothing else in this document
changes what the system can detect; this changes the subject.

### The mechanism is already ours

Nothing structural is missing. We add one input per program, one parser chain on
that tag, and the existing severity router handles the rest (see R2). The
mechanism is not the work. The regexes are.

### Why this is not scheduled yet

Writing 28 parsers against a subset of the data would produce parsers we cannot
test, for message shapes we have never seen. Run 3 is over and the LHC is down,
so no new logs are being produced.

EPN access removes this block. It does not remove the need to look first: local
disks rotate, and what sits on an EPN today may be a thin recent slice.

### The gate

Step zero is a survey, on one EPN, before a single parser is written:

1. List every file under the log roots. Record path, size and modification time.
2. Count lines per program.
3. Sample lines per program, enough to see the message shapes.
4. Run his 28 regexes against that sample and count matches per regex.

Then port only the parsers with matching lines. Drop the rest. Repeat the count
if the data situation changes.

The survey has a second output that matters as much as the count: the real file
layout. That grounds our tail patterns and Item 2.

Every ported parser also needs a strict numeric mapping in
`deploy/roles/opensearch_bootstrap/templates/templates.sh.j2`. He extracts these fields and
stores them as text, so they cannot be aggregated or charted. That is the whole
value, and he does not collect it. Parsers without mappings repeat his mistake.

Rule R1 applies to every ported `Time_Format`.

---

## Item 2 — Keep the source filename

**Status: TAKE.**

He sets `Path_Key source_file` on every tail, then normalizes it to the basename
with a dedicated parser, on all records. Every document then says which file
produced it.

We capture the same value as `file`, use it to extract the host, and then delete
it (`collector.yaml.j2:167` and `:196`, `remove_key: file`).

Work:

1. Stop removing `file`. Rename it `source_file`.
2. Normalize to the basename in the collector.
3. Map it as `keyword` in the unified index template.

Value: the per-process entity key. Detection today groups by host. This lets it
group by the program that wrote the line, which is the level at which an ALICE
fault actually appears.

The EPN survey in Item 1 tells us what the basenames actually look like. Do the
normalization against real names, not invented ones.

---

## Item 3 — Nodes register themselves

**Status: ADAPT.**

Today the control host creates every worker's index template, ISM attachment and
rollover write alias, by looping over the Ansible inventory
(`deploy/roles/opensearch_bootstrap/templates/templates.sh.j2:879`,
`deploy/roles/opensearch_bootstrap/templates/ism.sh.j2:145`).

So adding a worker needs an inventory edit and a deploy run. The EPN farm swaps
machines often. That shape does not survive it.

### Change

Each node registers itself before the collector starts, using its own node name:

1. Put its own index template for `generic-log-info-<box>-*`.
2. Attach the retention policy `alice-generic-info-retention`.
3. Ensure a writable rollover index behind the alias, which is what
   `ensure_rollover_index` (`templates.sh.j2:56`) already does.

Under systemd this is `ExecStartPre`. The node reads its own identity from its
environment, per R3.

### What this fixes

- Adding a machine needs no central action beyond installing it.
- A machine that returns after a reinstall repairs its own write alias at boot,
  instead of waiting for the next deploy. Without this, its alias still points at
  a red index and every write fails. This is the reason that survives regardless
  of how the farm is managed.
- A machine that leaves for good needs no cleanup. Its indices expire on the
  8-day retention already configured (`group_vars/all.yml:180`).

---

## Item 4 — Cut the OpenSearch footprint on worker nodes

**Status: ADAPT. Not from him; forced by putting a data node on a machine that
does reconstruction.**

The design stays as built: one cluster, workers as `data, ingest` nodes but never
cluster-manager eligible, the info tier pinned to its own box with no replicas
(`deploy/roles/opensearch/templates/opensearch.yml.j2:6-13`,
`templates.sh.j2:229`). Lubos requires that the bulk tier never crosses the wire.

What changes is how much the node spends to do it.

### 1. Cap the thread pools — do this first

```yaml
node.processors: 4
```

In the worker branch of `opensearch.yml.j2`. OpenSearch sizes every thread pool
from the processor count it detects. On a 128-core EPN node it builds pools for
128 cores and keeps those threads. One setting caps all of them at once, and it
also shrinks the merge scheduler default, which derives from the same number.

### 2. Never set `refresh_interval` on the info tier

We do not set it today, and that is correct. **Keep it that way, and write a
comment in the template saying so.**

With no explicit interval, a shard refreshes every second until no search touches
it for `index.search.idle.after`. Then it goes idle and stops refreshing.

Setting an explicit interval silently turns search idle off. It looks like a
tuning improvement and is the opposite.

```json
"index.search.idle.after": "10s"
```

**This value must be set against the detector interval, or it does nothing.**
Item 6 keeps anomaly detection running on this tier, and a detector query counts
as a search. So the shard never sleeps for long — it cycles.

Three detectors query `generic-log-info` every minute: `info-volume`,
`info-per-epn-entry-lag` and `info-collector-shipping-lag`. Their seven slow
partners run every 30 minutes. So the shortest gap between searches on this tier
is one minute, and any `search.idle.after` at or above one minute is inert.

The value must be far below that gap, not merely below it. It decides how much of
each minute the shard spends refreshing:

| `search.idle.after` | Refreshes per minute | Saving |
|---|---|---|
| 5m (or any value ≥ 1m) | 60 | none, setting is inert |
| 30s | about 31 | roughly half |
| 10s | about 11 | roughly six times |

Recommend 10 seconds. The cost is that a search arriving during the idle period
waits for one refresh before it answers. For a detector that is irrelevant. For
an operator on Discover it is a fraction of a second, on a tier that is queried
rarely by people.

Measure the refresh rate before and after. If it does not drop, the setting is
inert and the value is wrong.

### 3. Asynchronous translog on the info tier

```json
"index.translog.durability": "async",
"index.translog.sync_interval": "30s"
```

The default fsyncs on every bulk request. This removes that. The cost is up to
30 seconds of records lost on an unclean crash, on a tier that already has zero
replicas and an 8-day life.

Disk input and output is what competes with reconstruction. This is the largest
saving on that axis.

### 4. One merge thread per shard

```json
"index.merge.scheduler.max_thread_count": 1
```

The default is `max(1, min(4, node.processors / 2))`. Merges are the irregular
cost — they arrive when nobody chose. Leave `auto_throttle` at its default of
true; it already adjusts merge input and output against indexing load.

### 5. Pin the worker heap at 2 GB

There is no worker heap setting today. `inventory.yml:28` sets one only for
alice-ingest-3. Set it explicitly for the worker tier.

2 GB of heap holds a handful of small shards without strain. Expect roughly 3 to
4 GB resident, because the Java runtime and Lucene use memory outside the heap.
On a 512 GB EPN machine that is under one percent.

Every other number in this item is derived from this one, so it must be written
down rather than assumed.

### 6. The smaller levers, all of them

- `indices.memory.index_buffer_size: 5%` — the default is 10% of heap. At a 2 GB
  worker heap our write rate does not need 200 MB.
- Keep `bootstrap.memory_lock: true` (`opensearch.yml.j2:36`). Locked memory
  never swaps, so we can never push physics pages to disk.
- Keep the `zstd` codec (`templates.sh.j2:227`). It costs a little processor and
  saves a lot of disk. On a machine where disk is contended, that is the right
  side of the trade.
- Keep query insights on (`templates.sh.j2:760-768`). It is a cluster-wide
  setting with no per-tier scope, and it records at the node that coordinates the
  query. Workers coordinate their own writes but rarely serve searches, so the
  cost there is already near zero — and it is the measurement the node-selection
  operating rule depends on.

### 7. Hold the index count

The info tier rolls daily and deletes at 8 days (`group_vars/all.yml:176,180`).
At 200 boxes that is about 1600 indices and 1600 shards. That is inside what a
cluster handles, and each index stores its own resolved mapping in cluster state.

**Do not shorten the info rollover period.** That is the setting that would break
this at farm scale.

---

## Item 5 — Cheaper collector filters

**Status: ADAPT. Behaviour must not change. Every item below is output-neutral.**

### 1. `normalize_fields` runs twice on every record

`rewrite_tag` re-injects the record with a new tag, and the record then passes the
whole filter chain again from the top. `normalize_fields` matches both the source
tags and the family tags (`collector.yaml.j2:204`), so it runs on `dds` and again
on `family.info`.

`stamp_collector_time` guards against exactly this (`:106`). `normalize_fields`
does not.

Fix: match `^(infologger|family\.(info|other))$`. Same output, half the work.

### 2. `stamp_collector_time` does not need the family tags

The re-emitted record still carries `collector_time`. The second pass only runs
the guard. Drop `family.*` from its match.

### 3. Replace both `set_host` Lua filters with a native parser

`set_host` (`:150` and `:179`) does one pattern match to pull `epnNNN` out of the
file path, in two near-identical Lua filters. A `parser` filter with `key_name:
file` and a named capture does it in C, with no conversion of the record into a
Lua table and back. That conversion is the expensive part of any Lua filter.

This is his `basename` parser applied to our field, and it touches the same field
as Item 2.

### 4. Move `severity_norm` and `origin_host` into the ingest pipeline

After 1 to 3, `normalize_fields` is the last Lua that sees every record. It is
pure enrichment — routing uses the raw `severity` field, so nothing upstream
depends on it.

Add both to `alice-add-ingest-time`, which already exists. The collector then runs
zero per-record Lua. `health_deltas` remains and runs once per interval.

### 5. `flush: 5`

Cuts bulk requests fivefold. `flush` is service-level, so it also delays the live
lane of Item 7 by five seconds. Accepted — and 10 seconds is also acceptable if
measurement shows it is worth the extra saving.

### Rejected

Emitting `@timestamp` as epoch milliseconds to speed the Painless parse. It is
Fluent Bit's record time, set by the parser's `time_format`. Making it an ordinary
numeric field fights the time-key mechanism and breaks the strict date mapping.

---

## Item 6 — Keep anomaly detection on workers, make it cost less

**Status: ADAPT. The detectors and their output do not change.**

The detection plugins stay installed and stay running on the worker tier. The
signals they raise on the bulk tier are worth the cost. What changes is only how
hard the node works to produce the same answer.

Removing plugins from workers is rejected: the results matter, and an uneven
plugin set across a cluster is its own failure mode.

### 1. Disable concurrent segment search on the info tier

```json
"index.search.concurrent_segment_search.mode": "none"
```

This is the best fit for what we want. Concurrent segment search splits one query
across segments and runs the pieces on a separate `index_searcher` thread pool.
Turning it off runs the same query on one thread.

**Identical results. Higher latency. Much smaller processor spike.** The
index-level setting overrides the cluster-level one, so the storage tier keeps it.

The lever is live on our version, not theoretical. From OpenSearch 3.0 concurrent
segment search is on by default, in `auto` mode, and auto mode parallelizes
aggregations while leaving light queries on one thread. Detector feature queries
are aggregations, so they are exactly what it parallelizes. The 3.0 release notes
warn that aggregation workloads may use more processor after upgrade for this
reason.

### 2. Slow historical analysis down instead of making it smaller

```
plugins.anomaly_detection.max_batch_task_per_node: 2      (default 10)
plugins.anomaly_detection.batch_task_piece_interval_seconds: 10   (default 5)
```

The piece interval inserts a pause between pieces of a batch task. Backtests take
longer and return the same result. Nothing about a backtest is time-critical.

These are cluster-wide, so they slow backtests on storage too. Accepted.

### 3. Leave the model memory setting alone

`plugins.anomaly_detection.model_max_size_percent` defaults to 0.1, meaning ten
percent of each node's heap. It is a percentage, so a 2 GB worker already
contributes far less model memory than a storage node. It needs no change.

Lowering it would evict models and force cold starts, which **would** change the
output. Do not touch it.

### 4. Admission control is an emergency valve, not a throttle

OpenSearch can reject `_search` and `_bulk` on a node whose rolling average
processor use passes a limit, answering HTTP 429.

This is worth having so a worker sheds load rather than fighting reconstruction.
But a rejected detector query is a missing detection interval, which **is** a
change in output. So set it high enough that it only fires in a genuine
emergency, and alarm when it fires. It is cluster-wide, so it protects storage on
the same setting.

---

## Item 7 — The live lane

**Status: ADAPT. His idea, scoped by our severity split.**

He pushes every non-system record to Grafana over a WebSocket
(`fluent-bit-aggregator.conf:126-137`). At farm scale that is unreadable: the
browser receives thousands of lines a second and shows a blur.

Our severity routing is the rate limiter his design lacks.

### Scope

`infologger` and `generic-log-other` only. Never the info tier.

### Cost

Zero on the cluster. The lane never touches OpenSearch. That is the point — the
Discover page stays for real queries, and the common act of watching becomes free.

### Path

One extra output on the collector, matching those two tags, posting to our own
endpoint. Volume is low by construction, so the extra worker cost is small. No
broker is needed for one extra consumer (see Item 9).

### Which host runs it

**alice-ingest-5.** Proxied by the nginx already on the control host, so
operators and collectors keep one address.

It must not be the control host. That machine already runs Dashboards, nginx,
Alertmanager and the metrics poller, and the signal projector was deliberately
moved off it for memory (`inventory.yml:40-45`). This service holds one open
connection per viewer, so its cost grows with readers rather than with data.
alice-ingest-5 carries the live lane and is the least loaded of the
three.

Give it a memory limit in its unit file, as the other services have.

### Browser behaviour

- One ring buffer of the newest 10000 rows. Nothing older is ever kept.
- New records arrive over the WebSocket and enter the buffer. The view does not
  move.
- A button shows how many records arrived since the last look. Pressing it renders
  the newest 10000 and resets the counter.

Because the view always shows the newest 10000, one buffer is enough. There is no
second frozen copy and no unbounded growth.

Two requirements this implies:

- Render only visible rows. This is about how many rows exist in the page, not
  about processor speed. Ten thousand drawn rows is ten thousand layout boxes the
  browser must measure, style and paint, and that cost sits in the rendering
  engine. Faster hardware does not remove it. This is a hard requirement at every
  hardware level, now and in Run 4.
- The server drops for a slow client. It never queues per client. Network is the
  one limit on a phone that better hardware does not fix.

### Filtering happens in the browser

Decided 12 Aug 2026. The filter runs on the client, over the buffer it already
holds.

Ten thousand rows in memory, filtered on each change, is ordinary work for
consumer hardware in 2026, and this system is meant to be in use well beyond
that. The lane also carries only `infologger` and `generic-log-other`, which is
low volume by construction. There is no measurement that says the client cannot
do this.

Client-side filtering also gives the better experience: results are instant,
history already held stays searchable, and changing a filter needs no round trip.

Server-side matching stays available if a measured arrival rate ever makes the
network the limit. It is not the starting design.

### React, not Svelte

Decided 12 Aug 2026, on one reason: **whoever maintains this after us.**

This has to still work in Run 4, which means someone we have not met will change
it. React is the safer assumption about what that person already knows. That
reason does not depend on anything else in this document, so it does not move
when the rest does.

It is not a performance decision. Once the list is virtualized the framework is
not the bottleneck — both do the same few DOM operations per frame. Svelte's real
gain is bundle size, worth a fraction of a second on first load and nothing after
it is cached.

Two earlier reasons were dropped when the page became standalone: that Dashboards
is itself React, and that its OUI component library is React only. Neither
applies now. Recorded so nobody re-derives a dead argument.

### Requirements

- **Rich filters.** Severity, host, program, run, and free text, combinable.
- **Mobile.** The view must work on a phone. One column, no horizontal scroll,
  two lines per record, tap to expand.

### Where it lives

**A standalone page, served by the nginx we already run. Not a Dashboards
plugin.** Decided 12 Aug 2026 on Lubos's preference, and the engineering agrees
with him.

A Dashboards plugin must match the host's major, minor **and patch** version. A
plugin built for 3.7.0 does not load on 3.7.1. Patch releases carry security
fixes and CERN networks are scanned, so we would take every one of them. That is
a forced rebuild several times a year, and a rebuild that fails takes the page
down until someone fixes it. It is a permanent maintenance tax on a component
that needs none.

Standalone removes the tax completely. The page has no version relationship with
Dashboards at all.

What it costs, and why each is small here:

- **We serve it ourselves.** nginx is already deployed and already terminates
  basic authentication for Dashboards (`dashboards_basic_auth_user`). The page
  sits behind the same authentication, on the same host, with no new component.
- **We build our own controls.** True, but we were building this view by hand
  anyway. Dashboards gave it a frame, not its content.
- **Navigation.** The cockpit needs a link out to it, and it needs a link back.
  Two links.

This is only correct because the view is a fixed, purpose-built thing. A
general-purpose query tool should not be rebuilt outside Dashboards — Discover
already is one, and it stays where it is for real queries.

### Shared with Item 13

The operator cockpit needs the same thing: a dense, filterable, virtualized log
view that works on a phone. Build that view once. Give it two sources — the live
WebSocket here, and a query against `infologger` there.

Item 13 is standalone for the same reasons, and the shared component is what
makes both cheap. Splitting them across a plugin and a page would mean two
mounting paths for one component, which is the worst of each.

---

## Item 8 — Spread the write load across the storage tier

**Status: TAKE. Not from him. A defect in our current design, found while
answering how we load balance.**

`infologger` and `generic-log-other` are created with one primary shard and two
replicas (`templates.sh.j2:264` and `:244`).

Every collector writes to `host: localhost` (`collector.yaml.j2:233, 243, 253`),
so each worker's own OpenSearch node coordinates its writes. For these two
indices, that node must forward every bulk request to the single node holding the
one primary shard.

All three storage nodes already index every document, because a replica does the
same work as a primary. So processor cost is spread. The funnel is not. One node
receives the whole fleet's InfoLogger traffic, indexes it, and sends two copies
back out. It carries about three times the network of the other two, and it
serializes every write in the cluster.

### Change

```json
"number_of_shards": 3,
"number_of_replicas": 2
```

Each storage node then holds one primary and two replicas. Bulk requests spread
three ways and fan-out spreads three ways. Durability is unchanged: three copies,
still surviving the loss of two nodes.

Set the primary count equal to the number of storage nodes. Revisit it only if
the tier is resized.

### Why it is cheap

Shard count is fixed when an index is created, so this cannot change an existing
index. Both indices already roll over on age or size, so the next rollover picks
up the new template. No reindex and no downtime.

It also costs nothing today. The funnel only bites at farm volume. That is the
argument for doing it now, while it is free.

### Not done

Three primaries with one replica. It would cut per-node indexing and disk by a
third, but it drops us to surviving one node loss. Lubos said farm storage can be
treated as unlimited, so we buy durability with it.

### This is not an argument for a queue

A queue balances consumers. Consumers would still write into a one-primary index,
and the funnel would remain. Item 9's trigger is unchanged.

---

## Item 9 — The durable queue: not now, with a named trigger

**Status: RECORD. No build work.**

A queue in front of the storage tier would add durability across a worker loss and
allow readers to be added without touching the collectors.

We are not building it, for three reasons:

1. The OpenStack machines cannot host it. Getting the current stack up was already
   tight on memory.
2. The gain lands in the wrong place. A queue would mainly relieve the storage
   tier, and Lubos has said storage on the EPN farm can be treated as unlimited.
   The worker tier is where we are short, and a queue does not help there.
3. The bulk tier must not cross the wire at all, so a queue could only ever carry
   `generic-log-other` and `infologger` — the small fraction.

### The trigger

Build it when a **third** consumer of the stream appears. Two consumers —
OpenSearch and the Item 7 live lane — are served by two Fluent Bit outputs. A
third means editing the collector on every machine, and that is when a queue pays
for itself.

The live stream during Run 4 is the other trigger. Today a lost record is
recoverable, because our source is an S3 bucket and the replay runs again. On a
live farm it is gone.

There is a third thing a queue would fix, recorded so it is not forgotten: because
every collector writes to `localhost`, a worker whose own OpenSearch node is down
stops shipping `infologger` too, not only its local tier.

### If it is ever built

It must be Apache Kafka. CERN requires fully open source, which rules out
Redpanda — its core is source-available under a business licence, not an
open-source one. Recorded so this is not re-proposed.

Make it the primary path for the durable tier, not a failure path. Fluent Bit has
no on-failure route: retries are internal to the output plugin, and when
`retry_limit` is reached the chunk is dropped with no hook and no alternate
destination. A dead-letter design cannot be built. A queue that always carries the
tier needs no failure detection and no polling.

Record the queue coordinates on every document: topic, partition, offset and key.
He does this and it is worth copying. The offset is a replay position and the
partition-plus-offset pair is a deduplication key. Neither is reproducible from
any other field. There is no reason to defer this beyond the queue itself, so it
is not a separate item.

---

## Item 10 — An operator form for node and time selection

**Status: ADAPT.** His `Business Forms` panel drives the dashboard variables from
a form: explicit event-time start and end, dropdowns for type, node and topic,
and a free-text search.

Take the form. It is the only mechanism we have that can enforce an operating
rule we have already written.

### Why — the index-filter rule needs an enforcer

The last operating rule says node selection must produce a filter on `_index`,
never a filter on the `node` field. Nothing today stops an operator doing the
wrong one. A form does: the operator picks a machine from a dropdown, and the form
sets the index pattern to `generic-log-info-epn345-*`. The wrong query stops being
reachable rather than being merely discouraged.

### Why — the one-year window is a blunt instrument

`gen_cockpit.py:23` sets `LOG_TIME_FROM = "now-1y"` and line 946 pins it with
`timeRestore`. That is there because replayed logs carry June-2026 event times
while the wall clock is a year later, and the time picker filters `@timestamp`,
which is event time. It works, but it makes the last fifteen minutes unreachable,
and a one-year window is an expensive default on a farm.

Explicit event-time start and end fields remove the need for it. In real Run 4
operation the two clocks converge to within seconds, so the wide default becomes
pure cost with no benefit.

### Where it lives — the standalone page, not Dashboards

Corrected 12 Aug 2026. This item was written assuming a panel inside the
Maintainer Cockpit. That is not buildable.

OpenSearch Dashboards has no native control that selects an index. Its input
controls produce filter pills on **fields**, and nothing native lets one panel
change another panel's index pattern. Our own cockpit shows the available parts:
7 index patterns, 13 saved searches, 31 visualizations, no control panels. He
built his in Grafana, whose variables can drive a data source. Dashboards cannot.

So a Dashboards version of this form would have to filter on the `node` field —
the exact query the operating rule forbids. It would enforce the opposite of what
it is for.

The form therefore lives on the standalone operator page from Item 13, where we
own the query and the dropdown is ordinary work. Item 7 already established the
serving path, so this costs no new component.

### Not shared with Item 12

Item 12's picker looks similar and is not the same thing. It pins machines in
charts over `cockpit-metrics`, where filtering on `collector_id` as a field is
both correct and cheap, and where a native Dashboards control does the job. This
form selects an index over log data. Different data, different mechanism, and
only Item 12's version is native. Build them separately.

### Not taken

His confirm modal showing old against new values. It adds a click to guard a
mistake that one dropdown change undoes.

---

## Item 11 — Document every action on the ops page

**Status: TAKE. Not from him.**

The ops page exposes nine actions: `replay`, `stop`, `replay-fresh`, `wipe`,
`clear`, `poison-replay`, `poison-stop`, `inject` and `inject-stop`
(`deploy/roles/alice_ops/files/ops_server.py:989-1077`). Four of them destroy
data. The page carries some prose today, but not per action.

The documentation goes **on the page, beside each button**. A person about to
press `wipe` does not go and read a repository.

### What each entry must answer

1. What it does, in one sentence.
2. What it deletes, named exactly. Say "nothing" when nothing.
3. Roughly how long it takes.
4. Whether it is safe while other people are using the system.
5. What to do if it fails.

Point 4 is not optional. The staging machines are shared, and physicists run test
runs through the staging experiment control system.

### How it must read

Brief, plain and human. Write to a tired person at three in the morning who has
not used this page before.

- Short sentences. Say the consequence before the mechanism.
- Name real things. "Deletes the replayed logs" beats "clears ingested data".
- No internal shorthand. If a word only makes sense to us, replace it.
- No warning tone on safe actions, and no soft language on destructive ones.

A worked example, as the standard to match:

> **Fresh reload** — Deletes every replayed log and everything found from them,
> then loads the archive again from the start. Takes about an hour. Do not run
> this while someone is looking at a result they care about. If it stops partway,
> run it again; it always starts clean.

### Also

Group the destructive actions visually, apart from the safe ones. Right now they
sit in one column and look alike.

---

## Item 12 — Dashboards that hold their shape at any fleet size

**Status: TAKE. Not from him.**

Today `alice-viz-fb-status` draws one row per collector, and the shipping charts
group by `collector_id` (`gen_cockpit.py:1155-1185`). At two collectors that
reads well. At two hundred it is a grey smear, and it is slow to draw.

### The rule

**A panel's size must not grow with the fleet.** Everything below follows from
that one sentence.

### The layout

1. **A counter strip.** Total, healthy, degraded, down. Four numbers, the same
   four at any scale.
2. **A worst-ten table.** Ten rows, sorted by how bad they are. This is the panel
   an operator actually reads. On a small fleet it shows however many exist, so
   there is no separate small-fleet design.
3. **Charts show distribution, not machines.** Plot the fleet median, the 95th
   percentile and the worst value. One line each, whatever the fleet size.
4. **A picker for detail.** The operator pins specific machines, and only those
   are drawn individually, over the distribution. Default the pinned set to
   whatever is currently unhealthy, so the useful case needs no clicks.

### What already exists

The trend rollup writes `fleet_count` on every document and already computes
share-of-fleet. That is the fleet-relative figure this design needs, so the data
side is largely built.

### Mobile

The same rule delivers this for free. Four counters and a ten-row list fit a
phone. Two hundred chart lines never will.

Requirements: one column, cards rather than wide tables, no horizontal scroll,
and touch targets that a thumb can hit.

### The picker is native, and is not Item 10's dropdown

This picker filters `collector_id` as a field, over `cockpit-metrics` and
`trend-rollup`. That is correct here: those indices are not partitioned per
machine, so a field filter is the only filter available and it is cheap. A native
Dashboards input control does it with no custom code.

Item 10's dropdown selects an index over log data and cannot be native. The two
look alike and share nothing. Do not try to build one control for both.

---

## Item 13 — Two cockpits, for two different people

**Status: TAKE. Not from him. Needs EPN access to finish.**

Rename the current `ALICE Cockpit` to **Maintainer Cockpit**. Build a second one
called **Operator Cockpit**, modelled on the InfoLogger interface people already
use.

### Why two

They answer different questions.

The current cockpit answers "is the log pipeline healthy". That is a maintainer's
question. A shifter never asks it. Their question is "what is the experiment
saying right now". One dashboard serving both serves neither.

### The Maintainer Cockpit stays in Dashboards. The Operator Cockpit does not

The maintainer view is charts and saved searches over indices, which is what
Dashboards is for. It stays where it is and only gets renamed.

The operator view is a standalone page beside Item 7's, served by the same nginx,
sharing the same log component. Three reasons:

1. It has one fixed layout, copied from InfoLogger. Dashboards adds a frame
   around it and nothing else.
2. A shifter should never have to learn Dashboards navigation to read a log line.
   InfoLogger is itself a standalone application, so this matches what they know.
3. No plugin version lock, for the reasons written in Item 7.

### Why it copies InfoLogger

Adoption. Shifters have used that interface for years. A tool that looks like the
one they know needs no training, and a tool that needs training does not get
used. This is the strongest single lever we have on whether this system is still
running in Run 4.

### The data is already there

`templates.sh.j2:172-207` maps `severity`, `level`, `hostname`, `rolename`,
`pid`, `username`, `system`, `facility`, `detector`, `partition`, `run`,
`errcode`, `errline`, `errsource` and `message`.

That is the InfoLogger column set. So this is a user-interface job with no data
work behind it. Confirm field by field against the real interface before
building.

### Do this with EPN access

Two things need the EPNs, and they happen in the same pass as the Item 1 survey:

1. Find the InfoLogger interface documentation in the ALICE EPN documentation.
2. Look at the running interface. Record its columns, its filter controls, its
   severity colours and its default view.

### Shared with Item 7

The log view is the same component: dense, virtualized, filterable, mobile. Item 7
feeds it from a WebSocket. This item feeds it from a query against `infologger`.
Build it once.

---

## Item 14 — One causal edge file, for explanation first and suppression later

**Status: ADAPT.** He builds a causal model in Neo4j:
`ErrorPattern -[CAUSED_BY {prob}]-> Cause`, with `SeqCondition` nodes carrying an
ordered error chain, a time window and context keys. Satisfied conditions combine
as a noisy-OR, and a ranked cause list comes out.

Take the model. Leave the database.

### We already have his graph, pointing the other way

Alertmanager `inhibit_rules` are a causal graph. We never called it one.

| His model | Ours today |
|---|---|
| `ErrorPattern -> Cause` | `source_matchers` to `target_matchers` |
| `SeqCondition.context_keys` | `equal: ['cluster_id', 'collector_id']` |
| `SeqCondition.time_window` | `group_wait` and the episode window |
| `prob` on the edge | asserted as 1.0, never measured |
| emits a ranked cause list | emits nothing; it silences the symptoms |

Twenty-two edges are declared today across three causes
(`deploy/roles/alertmanager/templates/alertmanager.yml.j2:25-47`). Every one is
switched off: `alertmanager_proven_inhibit_rules: []`
(`group_vars/all.yml:136`).

So the difference is direction and confidence, not structure. He advises. We act.

### Not a graph database

About thirty edges, every query one hop. Neo4j exists for millions of nodes and
paths of unknown length. This fits in a JSON file beside `signal_catalog.json`.

The cost is also wrong: a new datastore means new backup, new upgrade path and a
new failure mode on the control host, which already runs Dashboards, nginx,
Alertmanager and the metrics poller. His own prototype consumes a topic that is
absent from his topic list, so there is no evidence it ever ran.

### The design

Declare every edge once, in one file: cause alert name, symptom alert name, scope
keys, probability, and a `proven` flag. The flag decides what the edge does.

- **`proven: false`** — the edge appears on the incident card as a candidate
  cause, ranked. It suppresses nothing.
- **`proven: true`** — the edge is rendered into `inhibit_rules`. It suppresses.

This is the point of the item. **The explanation feature ships now, on no
evidence, at zero risk.** A wrong ranking costs an operator half a minute. A
wrong suppression loses a page. Every edge earns promotion by surviving
injection, and the same file records that it did.

It also removes a duplication we have now: the rules are hand-written in the
template while the enabled set is a separate list in `group_vars/all.yml`. One
file replaces both.

### Probabilities are measured, not authored

His `prob` values are written by hand. Ours are counted.

Every `make inject` run is a labelled experiment, because we know what we broke.
Count how often each symptom appears inside the episode window when that cause is
present, across runs. That is an empirical conditional probability.

Ordering needs no new storage either. The projector already stamps signals into
episodes with timestamps, so "collector-down preceded data-loss by under two
minutes" is answerable from `alice-signals` today. That is his `SeqCondition`.

Keep noisy-OR, `B = 1 - product(1 - Bi)`, for ranking only. It assumes causes are
independent, and ours are not — `collector-down` and `fleet-fb-silence` overlap by
construction. Good enough to sort a list. Not good enough to silence a page.

### Two gates before any edge is promoted

The scorecard cannot approve a promotion as it stands.

1. **It measures harm, not effect.** `score_injection.py` counts pages that were
   muted and should not have been. Nothing counts symptoms that were correctly
   muted. **A rule that never fires scores a perfect zero.** Add a count of
   correct suppressions, or the gate passes vacuously.
2. **The cause may arrive too late to suppress anything.** Alertmanager applies
   inhibition when it notifies, and the page route waits 30 seconds
   (`alertmanager_page_group_wait`). But `collector-down` is absence-based: it
   needs `heartbeat_grace_seconds` of 90, plus a monitor interval, before it can
   conclude. Symptoms fired from observed counters reach Alertmanager first.
   `alertmanager_page_wait_covers_inhibition: false` (`group_vars/all.yml:137`)
   looks like exactly this acknowledgement. Prove the ordering, or fix the timing.

### Order of promotion, narrowest blast radius first

1. **`collector-down`** — scoped to one collector, ten symptoms. Needs the roster
   populated first, or it falls closed to the `none` sentinel and does nothing.
2. **`telemetry-silence`** — cluster-scoped, but its seven targets are all
   control-plane monitors, disjoint from the collector set. When the poller is
   dead those monitors have no fresh input, so their firing carries no
   information.
3. **`fleet-fb-silence`** — last, and the dangerous one. It mutes `collector-down`
   itself across the whole fleet. A staged restart that trips the 50 percent
   threshold would hide every real per-collector failure at once.

### Not taken

Neo4j, and his hand-authored probabilities.

---

## Operating rules

Small decisions with no build item of their own.

### Host and machine metrics are not ours

Decided 12 Aug 2026, on Lubos's instruction. Another person owns machine health
at CERN, using Mimir. We do not collect processor, memory, load or filesystem
figures for the machine.

Lubos approved what we do collect: Fluent Bit health and OpenSearch cluster
health. That is the boundary. We monitor the log pipeline and the log cluster.

Building our own host metrics would create a second source of truth for numbers
someone else already owns, which is worse than not having them. If we ever do
need one, it goes into `cockpit-metrics`, never into a new store.

### The farm runs Ansible, not Kubernetes

Confirmed 12 Aug 2026. Kubernetes is not available on the EPN farm. Ansible is.
So Ansible manages every tier, on the farm and on our own machines.

Recorded because a container plan was considered in detail and dropped on this
fact alone. Do not reopen it without new information about the farm.

### Decommissioning a machine must wipe `path.data`

OpenSearch identifies a node by an identifier stored in `path.data`, not by its
name. If a replacement machine reuses the hostname, the old machine and the new
one both claim that name, and `require.box` matches both. Shards could land on
either.

A returning machine whose indices were deleted is otherwise safe. Its shard files
are dangling, and OpenSearch has not imported dangling indices automatically since
Elasticsearch 7.9. They sit unused. But wipe the disk.

### Alarm on storage-tier index health, never on cluster health

The info tier is pinned to one box with no replica. Every reboot turns those
indices red, so the cluster goes red. On a farm that is constant. Cluster health
is not a usable signal for us. Watch the storage-tier indices.

### Node selection in the cockpit is an index filter, never a field filter

Picking a machine must produce a filter on `_index`, which lets OpenSearch skip
shards. A `node: epn345` filter over `generic-log-info-*` reads all 200 shards and
discards 199.

Item 10 is the enforcement path: the operator picks a machine from a dropdown and
the form sets the index pattern. **That form is on the standalone page, not in
Dashboards**, because Dashboards can only filter fields — a Dashboards version of
it would enforce the opposite of this rule. Item 10 carries the detail.

Inside Dashboards this rule stays unenforceable, so there it is enforced by
measurement: query insights already records processor use and shard count per
query (`templates.sh.j2:760-768`). Watch it on the storage tier and catch the
expensive pattern when it appears. This backstop is permanent, not temporary,
because Discover remains available and cannot be constrained.

Daily rollover helps here on its own. The can-match phase skips indices whose time
range does not overlap the query, so a short time range already prunes most of
each machine's eight indices.

---
