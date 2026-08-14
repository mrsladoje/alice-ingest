# Anomaly detection

Two OpenSearch plugins do the work. **Detectors** (Anomaly Detection) score
“is this interval surprising?”. **Monitors** (Alerting) run scheduled searches
and fire when a Painless condition is true. They are not the same thing: a
detector writes a grade; a monitor decides whether anyone should be paged.

---

## How OpenSearch detectors work

A detector does **not** look at raw log lines. Each interval it runs
aggregations over an index, optionally split by a **category field** (one
Random Cut Forest model per entity — high-cardinality AD). Those aggregations
become a point in a small feature space (volume, error count, lag, heap, …).

The engine is **Random Cut Forest**. It randomly slices that point cloud until
every point is isolated. Points in a dense crowd need many cuts; loners need
one or two. Few cuts → high anomaly score. A forest of trees votes, which is
why the score is stable.

Properties that actually matter here:

- **Streaming.** The forest’s idea of “normal” drifts. A run-start volume
spike alarms briefly, then becomes the new baseline. That is why slow leaks
and sustained faults can go quiet — the model absorbs them.
- **Multivariate.** It does not compute ratios. It flags *combinations that
have never happened* (throughput collapse + retry climb; heap up + indexing
down).
- **Shingles.** `shingle_size: 8` means it looks at the last eight intervals
as one sequence, not a single bucket.
- **Window delay.** It waits (1 min for metrics, 2 min for logs) so late docs
can still land in the bucket. Score too early and the shingle degrades.
- **Missing buckets are not zeros** unless we say so. An EPN that stops
logging produces *no* point, which the forest ignores. Volume detectors
therefore use **ZERO imputation** so silence looks like volume=0. Lag
detectors do **not** — a missing lag is unknown, not 0 ms.
- **Warm-up.** ~32 consecutive live intervals before the detector leaves
Initializing. Create ≠ start. A burst replay that finishes in minutes never
trains the log models; paced replay and historical analysis exist because of
that.

Each run writes a result doc (`anomaly_grade`, `confidence`) into
`.opendistro-anomaly-results*`. Grade is surprise, not “bad”. `ad-high-grade`
is what turns a high grade into an alert.

**Dual clock.** Metrics detectors key on `@timestamp` (the poller stamps wall
clock). Log detectors key on `collector_time` (when Fluent Bit accepted the
line). Replayed June logs therefore look “live” to AD. `ingest_lag_ms`
(collector → OpenSearch) is a real shipping signal even on replay.
`enter_system_lag_ms` (event → collector) is archive age under preserved
replay, so those scores are noise until live EPNs exist.

---



## The detectors (17 + one forecaster)

Same failure mode, two horizons: **1 min** (shingle ≈ 8 minutes of context)
and `-slow` **at 30 min** (hours). The slow twin is the same features, not a
different idea.

### Telemetry (over `cockpit-metrics`, real-time poller)


| Detector            | Per             | Watches                                                                                                                                 |
| ------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `ingest-flow`       | collector       | output records / errors / retries. Zero-imputed. Catches a collector going quiet or retrying while the hard rules have not yet tripped. |
| `node-health`       | OpenSearch node | heap, CPU, indexing rate, disk. No zero-impute (missing heap ≠ 0). Catches combinations like heap climbing while indexing falls.        |
| `dashboards-health` | whole cluster   | Dashboards event-loop delay, response time, request rate. No category field — one model.                                                |




### Logs, per EPN (`origin_host`)


| Detector                          | Index                | Watches                                                                                                  |
| --------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------- |
| `il-per-epn` (+ slow)             | `infologger`         | volume + error/fatal count. Silence and error bursts on one EPN while the farm looks fine.               |
| `other-per-epn` (+ slow)          | `generic-log-other`  | same idea for stdout/DDS “other”.                                                                        |
| `info-volume` (+ slow)            | `generic-log-info-*` | volume only (info is high-volume; error mix lives in `other`).                                           |
| `il-per-epn-entry-lag` (+ slow)   | `infologger`         | p95 `enter_system_lag_ms`. How late the EPN’s logs arrived at the collector. Meaningless on June replay. |
| `info-per-epn-entry-lag` (+ slow) | `generic-log-info-*` | same for the info family.                                                                                |




### Logs, per collector (`node`)

Shipping lag is a collector problem, not an EPN problem — that split is
load-bearing.


| Detector                               | Watches                                               |
| -------------------------------------- | ----------------------------------------------------- |
| `il-collector-shipping-lag` (+ slow)   | p95 `ingest_lag_ms` on Infologger (ships to storage). |
| `info-collector-shipping-lag` (+ slow) | same on info (worker-local indices, different path).  |


No “other” shipping-lag detector: `generic-log-other` sits with Infologger on
storage.

### Forecaster (same RCF family, different job)

`disk-fill` predicts `disk_used_percent` per OpenSearch node on a 60-minute
interval. Forecasting answers “when does this cross a threshold?”, so it only
exists for a metric with a real cliff, smooth movement, and a continuous feed.
Disk is the one that qualifies.

---



## How OpenSearch monitors work

A monitor is a **cron job inside the cluster**. Every N minutes OpenSearch
runs a search the monitor authored, hands the result to a small Painless
script, and if the script returns true it fires an action (we POST a JSON
payload to an in-cluster notification channel). That is the whole loop.
Nothing is trained. The script is a threshold, a ratio, or a presence test —
the point of this lane is to catch known cliffs and slow drifts that RCF
will absorb.

The two monitor types differ only in **how many times that script runs**
and **what it can name**.

### Query-level: one question, one answer

The search returns a single result. The script looks at that result once and
says yes or no. If yes, one alert fires, and it is about the whole
deployment — there is no per-machine key to attach.

`cluster-red` is the simplest case. Every minute it asks: in the last two
minutes, is there any `cockpit-metrics` document with `kind:cluster` and
`cluster_status_code:2`? The script is `hits.total.value > 0`. The cluster
is either red or it isn't, so one alert named `alice-logs` is the right
shape.

`telemetry-silence` is the same shape with the test flipped: are there
*zero* `kind:cluster` / `kind:osd` documents in the last five minutes? If
the poller is dead the answer is yes, and one alert named `alice-metrics`
fires. Asking “which poller?” would be meaningless — there is one.

Use query-level when the fact is fleet-wide or there is only one of the
thing (`ad-high-grade`, `fleet-fb-silence`, `trend-rollup-stale`).

### Bucket-level: group the hits, then ask per group

A **bucket** is one group in a `GROUP BY`. The monitor's search does not
just count matching documents; it groups them by a field (`collector_id`,
`os_node`, `origin_host`, …) and computes a number *inside each group*.
The script then runs **once per group**. Groups that fail become separate
alerts, each named after that group's key.

`collector-down` groups the last two minutes of `kind:fleet` documents by
`collector_id`. Each collector is a bucket. Inside the bucket it takes
`max(heartbeat_missing)`. The script is `params.max_missing == 1`. If
collectors `node-01` and `node-02` are both missing, two alerts fire —
`collector-down` on `node-01`, `collector-down` on `node-02`. A query-level
monitor on the same data could only say “some collector is down”.

`disk-cliff-page` is the same idea on OpenSearch nodes: group by `os_node`,
take `max(disk_used_percent)`, fire for each node above 92%. You get
“alice-ingest-3 is full”, not “a node is full”.

Throttle follows the same split. Query-level has one mute for the whole
monitor (30 min). Bucket-level mutes **per alert key**, so `node-01` being
down does not silence a later `node-02` page.

### What happens after the script says yes

The action POSTs JSON to `alice-incluster-alert-sink`. Most of that later
becomes Alertmanager via the signal projector. Two watchdogs
(`signal-projector-stale`, `alertmanager-down`) skip that path on purpose —
they cannot page through the thing they are reporting dead.

---



## The monitors



### Layer 0 — hard rules on `cockpit-metrics`

Hard cliffs, no model. They all query `cockpit-metrics`.

Two writers fill that index:

- **Each collector**, every few seconds: “Fluent Bit is alive, here are my counters.”
- **The poller** (`alice-metrics`), every 30s: OpenSearch/Dashboards health, plus a checklist of collectors that were *expected* to speak and didn’t.

A dead collector writes nothing, so “down” is the poller noticing a missing heartbeat, not a `fb_up:0` flag.


| Monitor                | Fires when                                                                   | Means                                                                                                  |
| ---------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `collector-down`       | One expected collector sent no heartbeat for 2 min                           | That machine (or Fluent Bit on it) is gone. Alert names it.                                            |
| `collector-unhealthy`  | Collector is still heartbeating, but Fluent Bit’s health endpoint is failing | Box is up; the pipeline inside it is sick.                                                             |
| `fleet-fb-silence`     | Half or more of the expected collectors are silent at once                   | One shared failure (creds, network, deploy), not N separate deaths. One fleet page instead of a storm. |
| `shipping-breaking`    | A collector retried writes to OpenSearch and still failed                    | The pipe is failing. Records may still be buffered.                                                    |
| `data-loss`            | A collector discarded records                                                | Those lines are gone.                                                                                  |
| `cluster-red`          | Cluster health is red                                                        | A primary shard has no home. Data missing, not just a replica.                                         |
| `shards-stuck`         | Some shard has had no home for 5 min                                         | Allocation is blocked. A short yellow after restart is normal; five minutes is not.                    |
| `disk-cliff-warn`      | An OpenSearch node’s disk is 85–92% full                                     | Approaching the read-only watermark (~95%). Names the node.                                            |
| `disk-cliff-page`      | An OpenSearch node’s disk is over 92%                                        | Same cliff, imminent. `disk-fill-forecast` predicts this crossing later.                               |
| `heap-spiral`          | An OpenSearch node’s JVM heap stayed above 90% for 5 min                     | Not a brief GC spike — the node is thrashing.                                                          |
| `admission-rejections` | An OpenSearch node refused a search or a write in the last 5 min             | CPU overload shed load. A refused search skips a detector tick; a refused write drops logs.            |
| `telemetry-silence`    | No cluster or Dashboards health sample for 5 min                             | The poller is dead. Collector heartbeats still arrive on their own, so the index is not empty.         |




### Trend lane — the anchored baseline RCF never keeps

Reads `trend-rollup` (10-minute pre-aggregates written by
`alice-trend-rollup`), not raw logs. Compares **three consecutive 10m
slices** (~30 min dwell) to a **7-day baseline** (24h fallback), excluding
the last 40 minutes so the dwell window is not in the baseline.


| Monitor                                                      | Metric                                                                                                                             |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `trend-il-volume`, `trend-other-volume`, `trend-info-volume` | Host’s **share of fleet** volume. Fleet-wide ramp (run start) cancels. 2× rise or ≤0.5× collapse, all three slices same direction. |
| `trend-il-ef`, `trend-other-errors`                          | Error **share of that host’s own volume**. Doubling traffic and errors together stays quiet.                                       |
| `trend-il-entry-lag`, `trend-info-entry-lag`                 | p95 entry lag vs baseline, ≥2×. Self-gates: slices above 1 h are treated as archive age, so June replay is silent.                 |
| `trend-il-shipping-lag`, `trend-info-shipping-lag`           | p95 shipping lag vs baseline. No ceiling — a multi-hour backlog is real.                                                           |


Guards: ≥50 docs (volume/errors), ≥10 error docs, ≥100 docs for lag (otherwise
p95 is just the max), 250 ms lag floor, retired-host guard on collapse.

### Alerts on model output (detectors can't alert without these)

**One sentence essence:** detectors only write a score; `ad-high-grade` and
`disk-fill-forecast` are the monitors that turn those scores into alerts.

Same monitors as Layer 0. Same Alerting plugin, same alert, same 30-minute
throttle. The only difference is **which index they search**.

A detector never pages. Every interval it writes a document into
`.opendistro-anomaly-results*` with a grade (0–1, how surprising) and a
confidence. The `disk-fill` forecaster does the same into
`opensearch-forecast-results*`, with a predicted disk percent. Left alone,
those documents just sit there.

These two monitors query those indices the way `cluster-red` queries
`cockpit-metrics`. If the score is high enough, they fire.

They are query-level, so you get one fleet alert (“a detector scored
high”), not one alert per host. `make backtest` writes into the same
anomaly index but tags those docs with `task_id`; both monitors ignore
anything with that field, so a historical run cannot page.


| Monitor              | Reads                          | Fires when                                                          | Means                                                       |
| -------------------- | ------------------------------ | ------------------------------------------------------------------- | ----------------------------------------------------------- |
| `ad-high-grade`      | `.opendistro-anomaly-results*` | Live result in the last 2 min with grade > 0.7 and confidence > 0.7 | An RCF detector found an interval unlike its recent normal. |
| `disk-fill-forecast` | `opensearch-forecast-results*` | Live forecast of disk % will exceed 85% inside the horizon          | Act before `disk-cliff-warn` sees the real 85%.             |




### Watchdogs for our own machinery

These watch the detection stack, not the experiment. If they fail, other monitors go blind or alerts vanish while looking healthy.

**Break-glass:** `signal-projector-stale` and `alertmanager-down` cannot
page through the path they are reporting dead. Their action skips the
projector and posts straight to a side channel.


| Monitor                  | Fires when                                                                 | Means                                                                                                                                                               |
| ------------------------ | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trend-rollup-stale`     | The rollup has worked in the last 24h, but no complete commitexists in the last 40 min | `alice-trend-rollup` is stuck or dropping a cohort. Every `trend-*` monitor is now reading stale data. The 24h check stops a fresh deploy paging on an empty index. |
| `trend-entity-cap`       | A rollup cohort hit ~1,800 hosts or flagged itself truncated               | The rollup stops after a cap. Extra hosts are silently unmonitored, not errored.                                                                                    |
| `signal-projector-stale` | Projector (that writes episodes from alerts) wrote nothing for 10 min (after it had written before)           | Incidents and Alertmanager re-sends have stopped. Active pages would quietly look resolved.                                                                         |
| `alertmanager-down`      | Projector saw Alertmanager unreachable in the last 5 min                   | Nobody is receiving pages. Alertmanager itself holds no history, so this is the only notice.                                                                        |


---



## How the two lanes fit

```
logs / cockpit-metrics
        │
        ├─► RCF detectors ──► `.opendistro-anomaly-results*` ──► ad-high-grade
        │         │
        │         └ surprise, combinations, silence (zero-imputed volume)
        │
        ├─► trend-rollup ──► trend-* monitors
        │         └ slow drift vs a frozen 7d baseline (RCF would absorb this)
        │
        └─► Layer 0 monitors
                  └ known cliffs: down, red, drops, disk 92%, …
```

Static rules catch cliffs. RCF catches unfamiliar shapes and per-entity
silence. Trend monitors catch the frog-boil that RCF will learn as normal.