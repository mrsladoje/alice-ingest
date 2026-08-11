# THANASIS_PLAN

What we port from Thanasis's `logstack` into `alice-ingest`, and how.

Source of the comparison: the architecture audit artifact, reviewed 30 Jul 2026.
Their repository is vendored at `thanasis/logstack/`.

Rejected items are not written here. If a thing is absent from this file, we
looked at it and we are not doing it.

## The grounding rule

Thanasis had a live Run 3 stream from the EPN farm. We do not. Run 3 is
finished. We have only the old logs in the S3 bucket, which we replay
synthetically. Lubos may have given us a subset of what exists.

The corpus we hold is a subset. Lubos may send a larger export later, with the
full variety of log families.

So no item enters this plan on merit alone. Each item must also be justified by
data we can actually replay. If the source data is absent, the item waits, and
we re-check it against every new export.

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
Ansible variables. That ties the file to one deployment tool.

Fluent Bit reads environment variables directly. He proves it: `node
${MY_NODE_NAME}` (`thanasis/logstack/config/fluent-bit-worker/flb-worker.yml:140`),
filled by Kubernetes from the node name.

Move every per-node value to an environment variable. Ansible writes an
`EnvironmentFile` for the systemd unit. Kubernetes supplies the same names from
the downward API. One config file, two supervisors, no rewrite at migration.

This rule is what makes Item 9 cheap.

---

## Items

## Item 1 — The parser library (28 regexes)

**Status: ADAPT. Blocked on data, not on design.**

His parser chains extract numbers from message bodies: per-timeframe processing
time, CTF size, decoding-error counts, tracks and vertices per timeframe,
encoder word counts, beam position. Our detectors currently see only
infrastructure signals, so they can say the log platform is sick but not that
the experiment is sick.

### The mechanism is already ours

Nothing structural is missing. We add one input per program, one parser chain on
that tag, and the existing severity router handles the rest (see R2). The
mechanism is not the work. The regexes are.

### Why this is not scheduled yet

Lubos gave us a subset of the S3 data. The LHC is down and Run 3 is over, so no
new logs are being produced; we replay what we hold. We expect a larger export
from Lubos later, which should carry the full variety of log families.

Writing 28 parsers against a subset would produce parsers we cannot test, for
message shapes we have never seen.

### The gate

Before any parser is written:

1. Count how many of his 28 regexes match lines in the S3 corpus we hold.
2. Repeat that count each time Lubos sends more data.
3. Port only the parsers with matching lines. Drop the rest.

Every ported parser also needs a strict numeric mapping in
`deploy/roles/dashboards/templates/templates.sh.j2`. He extracts these fields and
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

---

## Item 3 — Host metrics from the collector, pushed

**Status: ADAPT. His coverage, our method.**

He runs Fluent Bit's processor and memory inputs every five seconds, and the
disk input every sixty, on every node. We have no host metrics at all. Our
poller reads OpenSearch node statistics, so processor, disk and heap figures
exist only for machines that run OpenSearch (`deploy/roles/dashboards/files/metrics_poller.py`).

Amended 11 Aug 2026. The original reason for this item was that the gap appears
once collectors run on machines without OpenSearch. Items 5 and 9 settle that
every worker keeps a small OpenSearch node, on the farm too, so that case never
arrives.

The item still stands, on the surviving reason: OpenSearch reports its own
process, not the machine. Load average, swap use and free space per filesystem
are invisible to the node statistics API, and those are the figures that tell us
whether we are hurting a machine that is doing reconstruction.

### Not Prometheus

Decided 6 Aug 2026. Three reasons:

1. Prometheus pulls by design. Pushing needs the Pushgateway, which is meant for
   short-lived batch jobs, not hosts.
2. It would split the data. Our monitors and anomaly detectors query
   `cockpit-metrics` in OpenSearch. Metrics in a separate store with a separate
   query language would be invisible to them without a bridge.
3. It adds a daemon on machines we do not own. On the EPN staging nodes,
   physicists run test runs. Fluent Bit is already there and already justified.

### Not Fluent Bit's built-in inputs either

They emit fixed field names containing dots, such as `Mem.used`. Those are
awkward to map strictly and awkward to query. Our detectors need stable, strictly
typed fields.

### What we build

A second `exec` input beside the collector health script, which already runs
`fb_health.py` on a timer and pushes one JSON document through the buffered
OpenSearch output (`collector.yaml.j2:66`).

- New script emits one document per interval, `kind: host`, carrying the node id.
- Fields: processor use, load average, memory total and used, swap used, and
  free space per mounted filesystem.
- Interval 30 seconds, matching the metrics poller (`metrics_poller.py:12`), so
  the anomaly work sees an even series.
- Strict mappings for every field in the metrics index template.

Free space is a gain over him. His disk input reports throughput only, so he
cannot see a filesystem filling up.

---

## Item 4 — Nodes register themselves

**Status: ADAPT. Prerequisite for Item 9.**

Today the control host creates every worker's index template, ISM attachment and
rollover write alias, by looping over the Ansible inventory
(`deploy/roles/dashboards/templates/templates.sh.j2:879`,
`deploy/roles/dashboards/templates/ism.sh.j2:145`).

So adding a worker needs an inventory edit and a deploy run. The EPN farm swaps
machines often. That shape does not survive it.

### Change

Each node registers itself before the collector starts, using its own node name:

1. Put its own index template for `generic-log-info-<box>-*`.
2. Attach the retention policy `alice-generic-info-retention`.
3. Ensure a writable rollover index behind the alias, which is what
   `ensure_rollover_index` (`templates.sh.j2:56`) already does.

Under systemd this is `ExecStartPre`. Under Kubernetes it is an init container.
Same script, both places, per R3.

### What this fixes

- Adding a machine needs no central action at all.
- A machine that returns after a reinstall repairs its own write alias at boot,
  instead of waiting for the next deploy. Without this, its alias still points at
  a red index and every write fails.
- A machine that leaves for good needs no cleanup. Its indices expire on the
  8-day retention already configured (`group_vars/all.yml:180`).

---

## Item 5 — Cut the OpenSearch footprint on worker nodes

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
"index.search.idle.after": "5m"
```

**This value must be set against the detector interval, or it does nothing.**
Item 7 keeps anomaly detection running on this tier, and a detector query counts
as a search. So the shard does not sleep permanently — it cycles. If the detector
interval is shorter than `search.idle.after`, the shard never goes idle at all and
this setting is inert.

Set `search.idle.after` below the detector interval. Then each cycle is one
refresh per detector run, instead of one per second.

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

## Item 6 — Cheaper collector filters

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
lane of Item 8 by five seconds. Accepted — and 10 seconds is also acceptable if
measurement shows it is worth the extra saving.

### Rejected

Emitting `@timestamp` as epoch milliseconds to speed the Painless parse. It is
Fluent Bit's record time, set by the parser's `time_format`. Making it an ordinary
numeric field fights the time-key mechanism and breaks the strict date mapping.

---

## Item 7 — Keep anomaly detection on workers, make it cost less

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

## Item 8 — The live lane

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
broker is needed for one extra consumer (see Item 10).

### Browser behaviour

- One ring buffer of the newest 10000 rows. Nothing older is ever kept.
- New records arrive over the WebSocket and enter the buffer. The view does not
  move.
- A button shows how many records arrived since the last look. Pressing it renders
  the newest 10000 and resets the counter.

Because the view always shows the newest 10000, one buffer is enough. There is no
second frozen copy and no unbounded growth.

Two requirements this implies:

- Render only visible rows. A 10000-row table drawn in full will not keep up.
- The server drops for a slow client. It never queues per client.

### Where it lives

Decided in favour of an OpenSearch Dashboards plugin, with the coupling isolated.

Plugin versions must match the Dashboards major, minor **and patch** version. A
plugin built for 3.7.0 does not load on 3.7.1. Version pinning is normal, but
patch releases carry security fixes and CERN networks are scanned, so we will take
them.

So the cost is a rebuild on each security update, a few times a year. Contain it:

- The React application is a self-contained bundle with no Dashboards API in it.
- The plugin is a thin wrapper that mounts that bundle.

Then a version bump is a rebuild, not a rewrite. If a rebuild ever fails, the same
bundle can be served by the nginx we already run, as a page linked from the
cockpit.

---

## Item 9 — Kubernetes for the worker tier only

**Status: ADAPT. Confirmed: the EPN farm already runs Kubernetes.**

Do this after Item 4. Without self-registration a new machine still needs a
central Ansible run, and Kubernetes has bought nothing.

### Worker tier — DaemonSet

A DaemonSet runs one copy on every eligible machine and follows machines in and
out on its own. That is the whole problem on a farm that swaps hardware.

It also lets a farm operator free a machine with a node label, instead of asking
us to run a playbook. On a shared farm the person who needs the machine is not us.

Both the collector and the worker OpenSearch node can run this way. The usual
objections to a search node in Kubernetes do not apply here: the data is
disposable, has no replicas, and is meant to be pinned to that machine.

### Do not repeat his three mistakes

1. **State on `emptyDir`** (`flb-workers.yml:74`). It is deleted when the pod
   leaves the machine, so Fluent Bit forgets every file offset. Use `hostPath`.
   Same for the OpenSearch data path.
2. **Two hand-edited copies of the config.** He maintains `flb-worker.yml` and the
   578-line `kubernetes/flb-worker-conf.yml` separately, with no generator. They
   will drift. One template, two renderers, per R3.
3. **No resource limits at all.** Set them. Set the memory limit generously for
   the OpenSearch pod — a garbage collector that briefly passes the limit is
   killed, and being killed during a merge is a bad day.

### One cluster across both supervisors

```yaml
spec:
  hostNetwork: true
```

Pods then use the machine's own address and ports. To a storage node, a
Kubernetes worker looks exactly like a systemd worker. Seed hosts still point at
the three storage machines, which stay on Ansible. `node.name` and
`node.attr.box` come from the downward API, per R3.

OpenSearch has no concept of a container. With host networking a mixed cluster is
not a special case.

### Storage tier stays on Ansible

Seven reasons, recorded so this is not reopened:

1. There is nothing to schedule. Each node owns specific shards on specific disks.
2. Local volumes pin the pod to the machine anyway. All of the machinery, none of
   the mobility.
3. Memory limits fight the Java runtime. A garbage collector that briefly passes
   the limit gets the container killed.
4. `bootstrap.memory_lock` needs the `IPC_LOCK` capability, an unlimited memlock
   limit in the container, and swap off on the host.
5. `vm.max_map_count` is a host setting, so the host must be configured anyway.
6. Upgrading a search cluster is a specific sequence — disable allocation, flush,
   restart one, wait for green, re-enable. A rolling update knows none of it. That
   is why operators exist, and an operator is one more system to run.
7. Three machines. Ansible does this in one file.

---

## Item 10 — The durable queue: not now, with a named trigger

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
OpenSearch and the Item 8 live lane — are served by two Fluent Bit outputs. A
third means editing the collector on every machine, and that is when a queue pays
for itself.

The live stream during Run 4 is the other trigger. Today a lost record is
recoverable, because our source is an S3 bucket and the replay runs again. On a
live farm it is gone.

### If it is ever built

It must be Apache Kafka. CERN requires fully open source, which rules out
Redpanda — its core is source-available under a business licence, not an
open-source one. Recorded so this is not re-proposed.

Make it the primary path for the durable tier, not a failure path. Fluent Bit has
no on-failure route: retries are internal to the output plugin, and when
`retry_limit` is reached the chunk is dropped with no hook and no alternate
destination. A dead-letter design cannot be built. A queue that always carries the
tier needs no failure detection and no polling.

---

## Operating rules

Small decisions with no build item of their own.

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

This cannot be enforced by the interface. It is enforced by measurement: query
insights already records processor use and shard count per query
(`templates.sh.j2:760-768`). Watch it on the storage tier and catch the expensive
pattern when it appears.

Daily rollover helps here on its own. The can-match phase skips indices whose time
range does not overlap the query, so a short time range already prunes most of
each machine's eight indices.

---
