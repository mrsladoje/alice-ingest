# Soak round 2 — a plan that ends in design changes

Round 1 (`docs/SOAK.md`, 18 August 2026) asked what Fluent Bit does under load and
answered it. This round has a different purpose: **every measurement here exists to
change something we ship.** A number that cannot move a knob, a default or a
topology does not belong in this plan.

The goal, stated once: **take as little of an EPN worker as possible, and lose no
logs while doing it.**

Results go to `docs/SOAK_RESULTS.md`.

## Start here

This document is a runbook as well as a design. To pick it up cold:

1. Read **"Before stage A"** below. If the per-worker question is unanswered, derive
   the figure from the archive and proceed — the fallback is written there
2. Read **"Running a cell"** — where the rig is, what must be built before any run,
   the sixty-cell manifest, and what to do when a cell fails
3. Build items **1, 2 and 3** from that section's build table. Nothing is comparable
   until processor time and disk are sampled and gate zero exists
4. Run **stage A**, and do not compare anything until the noise floor is a number
5. Write findings to `docs/SOAK_RESULTS.md` as each stage closes, not at the end

## Before stage A — one question that must die first

🔴 **Is 1,000 records a second per worker, or per farm?** The plan assumes per worker
throughout. If it is per farm, every rate in it is wrong by orders of magnitude, the
53× headroom figure evaporates, and the flush grid is sweeping the wrong range. **This
is not an open question to carry; it is a pre-flight blocker.**

Two ways to kill it, and we should do both:

- **Ask Lubos.** One sentence
- **Derive it from the archive**, which needs nobody: count records per node per
  second across the replay window in the S3 data, split by family. That also gives us
  the burst shape, which no one has quoted

**If nobody has answered when work starts:** derive the figure from the archive,
**proceed under the derived number**, and record it at the top of
`docs/SOAK_RESULTS.md` as an assumption with its derivation. Do not wait, and do not
silently assume per worker.

**Execution order.** Round numbers follow the original proposal; the order below is
what actually runs, and it is not the same sequence:

| Order | Round | Stage | Why here |
|---|---|---|---|
| 1 | Round 0 — instrument | A | Nothing can be compared until processor time is sampled and the noise floor is known |
| 2 | Round 1 — collector alone | B | Cheap, fast, and its winner sets the core split |
| 3 | Round 2 — collector plus OpenSearch | C, D, E, F, G | Flush first, then heap, then the split, then confirmation |
| 4 | Round 4 — templating | — | Its new-template rate decides whether round 3 is affordable at all |
| 5 | Round 3 — embeddings | — | Runs last, on the numbers round 4 produces |
| 6 | Round 5 — storage tier | — | Deferred; needs the storage machines, not the laptop |

## The budget, now fixed

| | |
|---|---|
| **Processor** | **4 cores, absolute maximum, for everything we deploy on a worker** — collector, the worker's OpenSearch node, any templating or embedding sidecar. Lubos: never more, and aim lower |
| **Memory** | Not the constraint. `epn228` has 503 GB with **150 GB available**; the 337 GB shared-memory segment the O2 data-distribution machinery reserves is untouchable, but our roughly 5 GB sits inside the remainder comfortably |
| **Steady rate** | **1,000 records a second** |
| **Burst rate** | **10,000 to 20,000 records a second** |
| **Soak target** | **50,000 records a second**, on Lubos's instruction |

**Read the first two rows together: processor time is the only scarce resource we
have.** Every trade in this plan spends memory to save cores, never the reverse.

**And read the rate rows against round 1's ceiling of ~53,000 records a second.** At
the steady rate we have about **53× headroom**. At the burst rate, between 2.6× and
5×. At the soak target, none. So the collector's processor cost is a burst question
and a safety-margin question, not a steady-state one.

### The hardware gap, stated before any number is quoted

| | Laptop rig | `epn228` |
|---|---|---|
| Processor | Apple silicon, no simultaneous multithreading | 2 × AMD EPYC 7452, 32 cores each, 2 threads per core |
| Core count | 16, all physical | 64 physical, 128 logical |
| Generation | Current | 2019 |

Two consequences, and both make the laptop **optimistic**:

- **"4 of 128" means 4 logical processors**, which on a machine with two threads per
  core is roughly **2 physical cores** of throughput once the siblings are busy —
  and on a shared EPN they will be
- **Per-core speed differs by a wide margin** between a current Apple core and a
  2019 EPYC core

🔴 We are running on the laptop only, by decision. So `docs/SOAK_RESULTS.md` must
carry a standing warning: **the shapes, the knees and the rankings transfer; the
absolute rates do not.** A single micro-benchmark on `epn228` would give the
conversion factor whenever we want it.

## What round 1 already gave us, and what it left open

| Finding | Status |
|---|---|
| Ceiling ~53,000 records a second; the limit is parsing and routing, not output | Confirmed, unexploited |
| InfoLogger lost 10,085,550 records in an outage; DDS and stdout lost none | Confirmed, unfixed |
| A slow live lane throttled InfoLogger ingest to 5 % of offered | Mechanism real, magnitude an artefact of a slow test receiver |
| `flush: 1` halves memory | Confirmed, **not adopted, and possibly backwards** — see the flush sweep |
| Disk buffer and `MemoryHigh` must be raised together | Confirmed, documented |
| `workers: 4` costs memory and buys nothing | Confirmed for memory; never tested for processor time |

Round 1 measured memory. This round measures **processor time**, which is the
resource the EPN actually rations.

## Standing rules

- **No arm trades delivery for throughput.** Nothing here reduces retries, shortens
  buffers or drops records to look faster
- **Memory may be spent to save cores.** We have 150 GB and 4 cores
- **Every arm renders through `mkconfig.py`**, which renders the real
  `collector.yaml.j2`. No hand-written configurations — that is what stops the rig
  drifting from production
- **Report core-seconds per million records**, per service. Percentages do not
  transfer between machines; this does
- **One factor moves at a time**, from the control configuration below. A run that
  changes two things measures neither
- **Enforce with `cpuset-cpus` pinning**, not a quota, and ship `AllowedCPUs=` in
  the roles to match

## How the sweep is run

A rate ladder crossed with every arm would be several hundred runs. This section is
the design that keeps it to about sixty, and it governs every round below.

### The control

Every arm is one change away from this, and every comparison is against it:

| | |
|---|---|
| Configuration | `collector.yaml.j2` exactly as shipped — `flush: 5`, `storage.max_chunks_up: 64`, `storage.total_limit_size: 256M`, `retry_limit: 10`, filesystem storage on every input |
| Cores | 4, pinned with `cpuset-cpus` |
| Live lane | Off |
| Sink | The fake sink for collector-only stages; from stage C on, a three-node cluster — the worker's own node plus two storage containers |
| Replicas | **1** on `infologger` and `application-logs-central`; **0** on `application-logs-local-*`, as production has it |
| Rollover | Disabled for the duration of a cell; indices pre-created above the cell's write volume |

### The rates, and what each one is for

| Rate | Role | Runs at |
|---|---|---|
| **1,000 /s** | The real steady rate. About 53× under the collector's ceiling | Grid stages only |
| **20,000 /s** | **The reference rate.** High enough to separate arms, inside the ceiling | Every stage |
| **50,000 /s** | Lubos's soak target, and **above what a real OpenSearch will take**. Not a steady-state cell | Ceiling and overload arms only |

🔴 **A 50,000 records a second cell will fail the safety gates by construction** —
the collector's own ceiling is about 53,000 and OpenSearch is far below that. So the
50,000 runs are **two different tests, and neither is a pass/fail cell**:

- **`OVER` — sustained overload.** Ten minutes at 50,000. The question is *what
  breaks, in what order, and does it drain afterwards* — not whether it survives
- **`BURST` — burst cycles.** Thirty seconds at 50,000, then 120 seconds quiet, ten
  cycles. Round 1 showed the collector treats this as a non-event; this repeats it
  with OpenSearch in the path, where it may not be

**Every 50,000 cell in every stage names which of the two it is.** They are not
comparable to each other, and a table that mixes them silently is worse than one that
omits them:

| Stage | 50,000 cells | Pattern |
|---|---|---|
| B — collector screen | `t0` and the winning threading arm | **`OVER`** — the question is the parsing ceiling, which only a sustained rate finds |
| C — flush grid | The 50,000 row, all five flush values | **`BURST`** — a sustained 50,000 into OpenSearch saturates identically at every flush value and separates nothing. Burst absorption does separate them, because flush sets how much is in flight |
| D — heap grid | One cell, the winning heap | **`OVER`** — the point is which failure arrives first, and whether it drains |

**Screening arms run at the reference rate only.** An arm that changes nothing at
20,000 will change nothing at 1,000.

### The harness — what the other 8 processors do

The plan pinned the worker's 4 and said nothing about the rest. Twelve Colima
processors, allocated in full:

| Service | Pinned to | Why |
|---|---|---|
| Collector, and the worker's OpenSearch node | **cores 0–3** | The measured budget. These two share the 4 and nothing else touches them |
| Storage OpenSearch nodes (two containers) | **cores 4–7** | Must never be the bottleneck. Watched against the 80 % rule |
| Generator (`logburst`) | **cores 8–9** | Gate zero depends on it having headroom |
| Fake sink and live lane | **cores 10–11** | Round 1's five-per-cent artefact came from starving exactly this |

**The storage tier in the rig is two nodes with one replica, not three with two.**
Production has three storage nodes and `infologger` / `application-logs-central` at
1 shard and 2 replicas. Two containers with 1 replica is what 12 processors afford,
and it keeps the thing that matters to the worker — **the coordinating node waits for
a real replica acknowledgement**, which a single node with 0 replicas would not do.

🔴 **The divergence, stated so no number is misread:** worker-side figures from this
rig are **optimistic**, because production's bulk waits for three copies, not two.

**Quorum is a hazard here, not a test.** Exactly one storage container is
cluster-manager eligible; the worker node is `data` and `ingest` only, as in
production. Losing the manager stops the cluster. That is realistic — the real worker
also cannot index when the storage tier is gone — but it is **not** the outage arm.
The outage arms stop the *sink*, in collector-only stages where a sink exists.

**Disk is a shared, unmeasured confound, and this round measures it.** One laptop
solid-state drive carries the collector's filesystem buffer, both storage nodes' data
directories and the generator's tail files. Three consequences:

- **Add `io.stat` per container to `soakrec.py`** — read and write bytes, and queued
  operations. Round 1 recorded buffer size but never input and output pressure
- **`logburst`'s disk guard stays at 85 %**, and a cell that trips it is void
- **On a worker the collector's buffer shares the root filesystem with OpenSearch**,
  which turns every index read-only at its 95 % flood-stage watermark. The rig shares
  a drive for the same reason production does

**Index rollover is disabled for the duration of a cell.** A rollover inside a
ten-minute window pollutes segment counts and merge time, and the cells either side of
it are then not comparable. Indices are pre-created with thresholds far above what a
cell writes, and **segment count and merge time are recorded as deltas across the
measured window**, never as absolutes.

### The stages, in order

| Stage | What it settles | Sink | Cells |
|---|---|---|---|
| **A** — instrument and noise floor | `cpu.stat` sampling works; how Fluent Bit uses four cores; the processor-time noise floor | Fake | 7 |
| **B** — collector screen | Threading, live lane, the InfoLogger tap. All at flush 5, collector only (its 50,000 cells are `OVER`) | Fake | 13 |
| **C** — the flush grid | **The memory-against-cores trade.** 5 flush values × 3 rates (the 50,000 row is `BURST`) | Cluster | 15 |
| **D** — the heap grid | 3 heap sizes × 2 rates, at the winning flush, plus one `OVER` cell | Cluster | 7 |
| **E** — core split | How the 4 cores divide, at the winning flush and heap | Cluster | 3 |
| **F** — confirmation | The chosen configuration against the shipped control, at all three rates, repeated | Cluster | 12 |
| **G** — interaction checks | The assumptions below, tested rather than trusted | Cluster | 3 |

**Sixty runs exactly** — every one of them named in the manifest under "Running a
cell". At five minutes for a screening cell, ten for a grid cell and twenty for an
outage run, plus settle and teardown, that is about **ten hours of machine time**:
two working days on the laptop, run unattended in batches.

### Duration and repeats

- **Screening cell:** 1 minute settle, 5 minutes measured
- **Grid cell:** 1 minute settle, 10 minutes measured — round 1's knee criterion needs
  a ten-minute window
- **Outage run:** 20 minutes, with the fifteen-minute sink outage inside it
- **Repeats:** the control is run **three times per rate** in stage A, and the noise
  floor comes from that spread. Grid cells run once; stage F repeats the two
  configurations that matter

🔴 **Establish the processor-time noise floor before comparing anything.** Round 1 did
this for memory, found 12 MB, and ignored every difference inside it. **A difference
smaller than the noise floor is not a result**, and stage A exists to find that
number.

### The disqualification gate

**Gate zero, checked before anything else in the cell is read:**

- **Achieved offer is within 2 % of intended offer.** `logburst.csv` already records
  what the generator actually delivered each second; the gate compares it to the rate
  the cell asked for
- **The generator's own headroom was proved** for this rate, by the `--mode selftest`
  run `tools/soak/README.md` already prescribes

🔴 **Without gate zero the other four are worthless.** A generator that quietly falls
short at 20,000 produces a cell where delivered equals ingested, nothing drops, the
queue is flat — and the whole thing measured 14,000 records a second. It passes, and
it is a lie. **A cell that fails gate zero is void, not disqualified: it is rerun,
not ranked.**

Then, at **1,000 and 20,000 records a second**, a cell must hold all four:

- Delivered records a second equals ingested
- `dropped_records` is zero
- The chunk queue is flat, not climbing
- Chunks up stays under the `storage.max_chunks_up` cap of 64

**A cell that fails any of these is disqualified whatever it saves.** At 50,000 the
four are recorded and described, never used to disqualify — that rate is above the
ceiling on purpose.

**On throttling, and why it is not in the gate.** We pin with `cpuset-cpus`, and
pinning sets no quota — so `nr_throttled` stays at zero whatever happens, and "no
container was throttled" proves nothing at all. **Saturation is read from
`usage_usec` instead:** a service is saturated when its processor time approaches its
pinned core count times elapsed time. The rule that replaces the old one:

- **The worker's services may saturate** — that is the measurement
- **No unmeasured service may exceed 80 % of its pinned cores.** Generator, sink,
  lane and storage node all have to be comfortable, or the worker only looked healthy
  for want of load

### The interactions we assume away, and how we check them

One-factor-at-a-time is only honest if the factors do not interact. Three plausibly
do, so stage G tests them instead of trusting them:

| Assumed independent | Why it might not be | Stage G check |
|---|---|---|
| Threading × flush | Threading moves parsing off the main loop; flush governs output batching. Different halves of the pipeline | Re-run the winning threading arm at the winning flush, and confirm the gain survives |
| Live lane × flush | A longer flush means larger chunks, and a slow lane holds them longer. This one is the least safe assumption | Lane on and off, at the shipped flush and the winning flush |
| Heap × flush | Bulk size against the indexing buffer | Covered by stage D running at the winning flush; one cell at the shipped flush confirms the ranking does not flip |

**Everything else is assumed independent and stated as an assumption**, not proved.

## The design levers this round exists to test

Each row is a change we could ship; each round below accepts or rejects one.

| Lever | Why we think it helps | What could go wrong | Round |
|---|---|---|---|
| `threaded: on` inputs, and filter work moved into per-input `processors` | Filters always run on the main event loop. Processors run in the input's own thread. This is the only way a second core raises the record ceiling | `rewrite_tag` cannot be a processor — changing a tag changes routing, which is filter-only. The severity split stays on the main loop | 1 |
| A spool in front of the InfoLogger socket | DDS and stdout survive outages because the log file *is* the queue. Our `tcp` input has nothing behind the socket | One extra disk write and read per record. Should be near-free; must be proven. **See "the InfoLogger tap" below — the real fix may be upstream** | 1 |
| A separate tag for the live lane | A chunk is freed only when *every* matching output has finished it. Its own tag makes the lane's chunks independent, so a slow lane cannot retain InfoLogger chunks | Duplicating four-fifths of the stream through the emitter costs memory and main-loop time | 1 |
| Two Fluent Bit processes instead of one | The main-loop ceiling is per process. One for the tailed families, one for InfoLogger, doubles the ceiling and isolates the fragile path | Two units, two buffers. **Both must still fit inside the same 4 cores** | 1 |
| Core split and heap size on the worker | We must hand the EPN a `AllowedCPUs` and a `MemoryMax`. Today **no role in `deploy/` sets any processor cap at all** | A heap too large steals page cache from segment merges | 2 |
| Templating in a sidecar, with an in-process cache | Collapses cardinality before anything expensive touches the text | A new service inside the same 4 cores | 4 |
| Static embeddings instead of a transformer | Roughly two orders of magnitude cheaper per text | Lower retrieval quality; not in the OpenSearch built-in model list | 3 |
| **The flush interval** | It is the single knob that trades collector memory against OpenSearch processor time, and it also sets the live lane's latency floor | Round 1 measured it at one rate, for memory only. That verdict may not survive the new budget | 2 |

---

## The InfoLogger tap — the question under round 1's loss finding

Round 1 found InfoLogger losing everything an outage touched, while the tailed
families lost nothing. Before building a spool, note what the real system already
does.

**How InfoLogger works today** (`AliceO2Group/InfoLogger`, and slide 2 of our own
deck):

```
client process --UNIX socket--> infoLoggerD --tcp 6006--> infoLoggerServer
                               (one per node)            (one, central)
                                                                |
                                              tcp 3306 --> MySQL, one table
                                                                |
                                          tcp 6102 live push --> infoBrowser
```

**The finding that matters:** *"infoLoggerD stores messages in a persistent local
file until messages are successfully transmitted and acknowledged by
infoLoggerServer."* Default queue path `/tmp/infoLoggerD/infoLoggerD.queue`. **The
node-local durable queue we wanted to build already exists**, one per node, and it
is acknowledgement-driven.

**Two more facts with teeth:**

- **Flood protection at the source.** The client library drops when a process
  exceeds **500 messages in one second, or 1,000 in one minute**. So a 50,000
  records a second InfoLogger burst implies about a hundred concurrent processes,
  not a few loud ones
- **The client library can write to a file directly** — `O2_INFOLOGGER_MODE` accepts
  `infoLoggerD`, `stdout`, `file:/path`, or `none`

**So the design question is not "how do we add a queue", it is "where do we tap".**

### What Thanasis actually did during Run 3

His stack is in `thanasis/logstack/`, and it settles the question with evidence
rather than argument.

🔴 **His worker collector has no InfoLogger input at all.** Not a `tcp` input, not a
socket, nothing pointed at `infoLoggerD`. `config/fluent-bit-worker/flb-worker.yml`
has four host inputs — `systemd`, `cpu`, `mem`, `disk` — and eleven `tail` patterns
over `/var/log/calib/**`, one per O2 process type, plus a catch-all `generic.logs`
with the named patterns excluded. The Kubernetes variant is the same.

**So during Run 3 the log text came from the files the O2 processes already write on
the worker.** That is tap four in the table above, and it costs nothing to arrange
because the files exist already.

**The `infologger` index was planned, not built.** `config/grafana/dashboards/`
`infologger.json` queries `_index:infologger` filtered on `partition`, `severity`,
`hostname` and `system` — the real InfoLogger schema, and the same shape as our
index. But nothing in his pipeline writes that index. `querylogs.json` excludes
`topic.keyword:(system-metrics system-logs infologger)`, and **his Kafka has fifteen
topics, none of them named `infologger`.** The dashboard is a view waiting for a
feed.

**What this means for round 1's loss finding.** Our `tcp` input on port 5170 exists
because `images/replay/replay.py` sends JSON to it — we built both ends. The one
person who ran this against a real EPN during a real run never used such a path. So
the spool arm still prices the mechanism honestly, but **the tap we ship should
probably be files, like his, and then the InfoLogger loss problem disappears by
construction** — a tailed family cannot lose an outage's worth of records.

### Four more things his configuration tells us

| His choice | What it means for our soak |
|---|---|
| `Flush 1` in the service section | Round 1 found `flush: 1` halves our memory. He already runs it. Our default of 5 seconds is the outlier |
| **No `storage.type filesystem` anywhere** | His workers buffer in memory only. Kafka is his durability layer, with `all-logs` held one hour and 1 GiB, `generic-logs` one day and 10 GiB. We chose the opposite: disk buffer on the worker, no broker |
| **Fourteen Kafka outputs from one worker**, with `all-logs` catching everything through a negative-lookahead match | Most records are serialised **twice**. That is the same cost as our live-lane duplication arm (`lt`) — he pays it as normal operation, which is mild evidence it is affordable |
| **His live feed is pushed from the aggregator, not the worker** — a Grafana Live HTTP push out of the central Fluent Bit | An option we have not considered: move the live lane off the worker entirely. It would end the chunk-retention coupling outright. **But it costs the lane its best property** — ours keeps working when OpenSearch is red precisely because collectors feed it directly. Worth stating as a trade, not adopting |

---

## Running a cell

Everything above is the design. This section is the runbook, and it assumes no prior
knowledge of the rig.

### Where the rig is

`tools/soak/` — read `tools/soak/README.md` first; it explains the three sinks, how to
read a report, and the safety guards. The pieces that matter here:

| File | Role |
|---|---|
| `soak.py` | Runs a whole profile end to end and writes the report. `soak.py profiles` lists them, `soak.py run <profile>` runs one, `soak.py down` tears the rig down |
| `mkconfig.py` | Renders the **real** `deploy/roles/collector/templates/collector.yaml.j2`, then patches the knob under test. This is what stops the rig drifting from production — **never hand-write a collector config** |
| `logburst.py` | The load generator. `--mode selftest` measures its own ceiling |
| `soakrec.py` | The recorder, one row a second |
| `sink.py` | The fake sink; can stall, answer 429, or be stopped |
| `rig/docker-compose.soak.yaml` | The containers |

Each run writes `tools/soak/runs/<timestamp>-<profile>/` containing `report.md`,
`summary.json`, `soakrec.csv`, `logburst.csv`, the exact `conf/collector.yaml` that
ran, and the fixture that was sent.

### Prerequisites, once

1. **Colima at 12 processors.** The `cern` profile is at 8 today; the host has 16:
   `colima stop cern && colima start cern --cpu 12 --memory 32 --disk 100`
2. **Images:** `fluent/fluent-bit`, `python:3.12-slim`, `opensearchproject/opensearch`
3. **Python:** `pyyaml` and `jinja2` — both come with the Ansible environment
4. **Generator headroom proved** at 1,000, 20,000 and 50,000 records a second, per
   gate zero

### What must be built before the runs

**The rig cannot run this plan as it stands.** Build in this order; each group unlocks
the stages beside it.

| # | Build | Unlocks |
|---|---|---|
| 1 | **`cpu.stat` and `io.stat` per container in `soakrec.py`**, plus per-thread processor time for the collector | Everything. Nothing is comparable without it |
| 2 | **`cpuset-cpus` for every service** in `rig/docker-compose.soak.yaml`, following the harness allocation | Everything |
| 3 | **Gate zero in the report** — achieved offer against intended, and a `--mode selftest` runner | Everything |
| 4 | **Arm configurations in `mkconfig.py`:** `t1` (`threaded: on`), `t2` (filters moved to per-input `processors`), `lt` (lane on its own tag through `rewrite_tag`) | Stage B |
| 5 | **The InfoLogger appender** for `s1` — a thin service that accepts the socket and appends to a file the collector tails | Stage B spool arm |
| 6 | **The second collector process** for `t3` — its own unit, config and buffer, both inside the 4 cores | Stage B threading arm |
| 7 | **The cluster sink** — worker node plus two storage containers, joined, `opensearch_bootstrap` templates applied, replicas and rollover per the control, indices pre-created. A new `--sink cluster` beside `null`, `http` and `opensearch` | Stages C–G |
| 8 | **Profiles for stages A–G** in `soak.py`'s `PROFILES` | Convenience; the CLI overrides below work without them |

**What the rig already has**, and does not need building: `--flush`, `--os-heap`,
`--rate`, `--cpus`, `--live-lane`, `--retry-limit`, `--total-limit-size`,
`--max-chunks-up`, `--storage-type`, `--output-workers`, `--lua`, `--compress`,
`--fault-at` / `--fault-seconds` / `--fault-kind`, and the `--disk-guard-pct` guard.

### The cell manifest — sixty runs

Pattern column: **`STEADY`** unless marked. Duration is the measured window; add one
minute of settle to each.

**Stage A — instrument and noise floor · 7 cells · fake sink**

| Cell | Arm | Rate | Pattern | Minutes |
|---|---|---|---|---|
| A1–A3 | Control, three repeats | 1,000 | STEADY | 5 each |
| A4–A6 | Control, three repeats | 20,000 | STEADY | 5 each |
| A7 | `logburst --mode selftest` at 1,000 · 20,000 · 50,000 | — | — | 10 |

The noise floor comes from the spread across A1–A3 and A4–A6. The `workers` default
and the per-thread split are read out of A4–A6, not from a separate run.

**Stage B — collector screen · 13 cells · fake sink · `flush: 5`**

| Cell | Arm | Rate | Pattern | Minutes |
|---|---|---|---|---|
| B1 | `t0` as shipped | 20,000 | STEADY | 5 |
| B2 | `t1` threaded inputs | 20,000 | STEADY | 5 |
| B3 | `t2` processors | 20,000 | STEADY | 5 |
| B4 | `t3` two processes | 20,000 | STEADY | 5 |
| B5 | `t0` | 50,000 | **`OVER`** | 10 |
| B6 | Winning threading arm | 50,000 | **`OVER`** | 10 |
| B7 | `l1` lane on, real `live_lane.py` | 20,000 | STEADY | 5 |
| B8 | `lw` lane at `workers: 0` | 20,000 | STEADY | 5 |
| B9 | `lt` lane on its own tag | 20,000 | STEADY | 5 |
| B10 | `lv` lane server alone, 5 viewers | 20,000 | STEADY | 5 |
| B11 | `lv` lane server alone, 20 viewers | 20,000 | STEADY | 5 |
| B12 | `s0` InfoLogger over TCP, sink outage | 20,000 | outage | 20 |
| B13 | `s1` InfoLogger through the appender, sink outage | 20,000 | outage | 20 |

**Stage C — the flush grid · 15 cells · cluster**

| Cell | Flush | Rate | Pattern | Minutes |
|---|---|---|---|---|
| C1–C5 | 0.5 · 1 · 2 · 5 · 10 | 1,000 | STEADY | 10 each |
| C6–C10 | 0.5 · 1 · 2 · 5 · 10 | 20,000 | STEADY | 10 each |
| C11–C15 | 0.5 · 1 · 2 · 5 · 10 | 50,000 | **`BURST`** | 10 each |

**Stage D — the heap grid · 7 cells · cluster · winning flush**

| Cell | Heap | Rate | Pattern | Minutes |
|---|---|---|---|---|
| D1–D3 | 1 · 2 · 3 GB | 1,000 | STEADY | 10 each |
| D4–D6 | 1 · 2 · 3 GB | 20,000 | STEADY | 10 each |
| D7 | Winning heap | 50,000 | **`OVER`** | 10 |

**Stage E — the core split · 3 cells · cluster · winning flush and heap**

| Cell | Collector cores | OpenSearch cores | Rate | Minutes |
|---|---|---|---|---|
| E1 | 1 | 3 | 20,000 | 10 |
| E2 | 1.5 | 2.5 | 20,000 | 10 |
| E3 | 2 | 2 | 20,000 | 10 |

**Stage F — confirmation · 12 cells · cluster**

The shipped control against the chosen configuration, at 1,000 · 20,000 · 50,000
(`OVER`), twice each. 10 minutes per cell.

**Stage G — interaction checks · 3 cells · cluster**

| Cell | Check | Rate | Minutes |
|---|---|---|---|
| G1 | Winning threading arm at the winning flush | 20,000 | 10 |
| G2 | Lane on at the winning flush — lane off is a stage F cell | 20,000 | 10 |
| G3 | Heap ranking at the shipped flush, to confirm it does not flip | 20,000 | 10 |

### How a cell is invoked

Once build items 1–3 are in, a screening cell is:

```
python3 tools/soak/soak.py run p6 --rate 20000 --cpus 4 --duration 300 --flush 5
```

A grid cell, after build item 7:

```
python3 tools/soak/soak.py run p6 --sink cluster --rate 20000 --duration 600 \
    --flush 2 --os-heap 2g
```

A burst cell uses `p2`, an outage cell uses `p3` with `--fault-at` and
`--fault-seconds`, and the charts come from
`python3 tools/soak/plot.py tools/soak/runs/<run>`.

**Tear down between stages, not between cells:** `python3 tools/soak/soak.py down`.

### When a cell fails

| Outcome | Action |
|---|---|
| **Fails gate zero** | **Void.** Rerun it. If it fails twice, the generator is the problem — fix it before continuing, and say so in the results |
| **Fails a disqualification criterion** | **Disqualified, and recorded.** Do not rerun, do not tune it into passing. A disqualified cell is a result: that configuration is not shippable |
| **Trips the 85 % disk guard** | Void. Clear the run directory and rerun |
| **An unmeasured service passed 80 % of its pinned cores** | Void. The harness was the bottleneck, not the worker |

### What goes in `docs/SOAK_RESULTS.md`

Write it as round 1 wrote `docs/SOAK.md` — findings first, numbers under them, and the
limits stated plainly. Required contents:

1. **The standing warning**, at the top: laptop hardware, so shapes and rankings
   transfer and absolute rates do not — with the epn228 comparison
2. **The per-worker-or-per-farm answer**, and if derived rather than told, its
   derivation
3. **The processor-time noise floor**, before any comparison is drawn
4. **One table per stage**, with every cell, its gate-zero result and its verdict
5. **The decisions taken**, as a list of the exact values the roles should carry:
   `flush`, heap, `AllowedCPUs`, `MemoryMax`
6. **The predictions, scored** — flush, threading, spool — each marked right or wrong.
   A wrong prediction stated plainly is worth more than a quiet correction
7. **What this round does not cover**, in the manner of round 1's own closing section

---

## Round 0 — the instrument, before anything else · **stage A, 7 cells**

**Confirm how Fluent Bit uses four cores.** Our template sets `workers: 0`
explicitly only on the `health` output, so the other four run on a plugin default of
0, 1 or 2. Record per-thread processor time with four cores available. Expect the
main loop pinned near one core and the rest idle.

**Instrumentation to add first:**

- `cpu.stat` in `soakrec.py` — `usage_usec`, `nr_throttled`, `throttled_usec`. None
  of it is sampled today
- Per-thread processor time for the Fluent Bit process, so "main loop" and "output
  workers" are separable
- OpenSearch `thread_pool.write` queue and rejected counts, and garbage-collection
  time (round 2)
- **`io.stat` per container** — read and write bytes, and queued operations. One
  laptop drive carries the collector's buffer, both storage data directories and the
  generator's tail files
- `cpuset-cpus` pinning in the rig compose file, replacing the `cpus` quota
- The Colima `cern` profile raised from 8 processors to 12. The host has 16

**Exit criteria — round 0 is done when all five hold:**

1. `soakrec.csv` carries per-container `usage_usec`, `nr_throttled` and
   `throttled_usec`, and per-thread processor time for the collector
2. **No unmeasured container exceeds 80 % of its pinned cores** under load, read from
   `usage_usec`. `nr_throttled` is not the check — `cpuset` pinning sets no quota, so
   it stays at zero regardless
3. The `workers` default on our Fluent Bit version is written down, not assumed
4. **The processor-time noise floor is a number**, from three control runs at each of
   1,000 and 20,000 records a second
5. **The generator's own ceiling is measured**, by the `--mode selftest` run, at every
   rate this plan uses — gate zero depends on it

---

## Round 1 — Fluent Bit alone · **stage B, 13 cells**

**Everything in this round runs at the shipped `flush: 5`**, against the fake sink,
at the reference rate of 20,000 records a second unless a row says otherwise. Flush
is settled later, in stage C, because its deciding cost lands on OpenSearch — and
stage G then confirms this round's winner survives the flush that wins.

**Threading arms — the ceiling question (6 cells):**

| Arm | Change |
|---|---|
| `t0` | As shipped |
| `t1` | `threaded: on` on both `tail` inputs and on `tcp` |
| `t2` | `t1`, plus the per-tag `parser`, `record_modifier` and `lua` work moved into each input's `processors` block. `rewrite_tag` stays a filter and reads a field the processor set |
| `t3` | Two Fluent Bit processes: tailed families in one, InfoLogger in the other. Both inside the same 4 cores |

Four arms at the reference rate, then the winner and `t0` repeated at 50,000 — the
ceiling question only exists near the ceiling.

**Decision this answers:** whether a second core buys throughput at all. If `t2`
does not beat `t0`, one core is the architectural cap. Given 53× headroom at the
steady rate, the right answer may well be to cap the collector at one core and hand
the other three to OpenSearch.

**Live-lane arms (3 cells, plus 2 for the lane server).** *Lane off is the control —
stage A's runs are the comparison, so there is no separate `l0` cell.* The production shape is
**one output, about five viewers at a time**, so the collector-side cost is a single
`http` output. Viewer count is a lane-server cost, measured separately and not
against the collector's budget.

| Arm | Change |
|---|---|
| `l1` | One lane output to a **real `live_lane.py`**, on its own pinned cores |
| `lw` | Lane output at `workers: 0` against the default |
| `lt` | Lane fed from its own tag, so its chunks are independent |


Separately, not a collector arm:

| Arm | Change |
|---|---|
| `lv` | The lane server alone, at **5 and 20 concurrent viewers**, measured for its own processor and memory cost |

**Two rules, both from round 1's mistakes:**

- **Measure the lane receiver alone first.** Round 1's five-per-cent figure came
  from one Python process, and `docs/SOAK.md` says so. A slow receiver measures the
  receiver
- **We compress, the consumer decompresses.** Our cost is deflate on the roughly
  four-fifths of the stream the lane matches, plus one compressed copy per queued
  chunk

**Spool arm — the loss question (2 cells, 20 minutes each):**

| Arm | Change |
|---|---|
| `s0` | InfoLogger straight into the `tcp` input, as shipped |
| `s1` | InfoLogger into a thin appender, written to a file, tailed |

Run both through the fifteen-minute sink outage that lost ten million InfoLogger
records in round 1. **Success is zero loss on all three families, with `s1`'s cost
stated in core-seconds per million records.**

---

## Round 2 — Fluent Bit plus the worker's own OpenSearch · **stages C, D, E, F, G**

**This is already the production topology.** All four `opensearch` outputs point at
`localhost`. The worker node is a cluster member, and
`index.routing.allocation.require.role: storage` puts `infologger` and
`application-logs-central` shards on the storage tier. The worker node coordinates
and forwards over the transport layer.

**So the rig needs a real cluster — the worker's own node plus two storage
containers, joined, with `opensearch_bootstrap`'s templates applied.** Round 1's single bare container had no templates and no
rollover aliases; it collapsed at 2,000 records a second, and `docs/SOAK.md` says
plainly: do not quote that number.

**Isolation is the point of this round**, and the allocation is in "The harness"
above: the worker's two services own cores 0–3, and nothing else touches them. **A
cell where any unmeasured service passed 80 % of its own pinned cores is void** — the
worker only looked healthy for want of load.

**The failure mode to watch is bulk rejection, not collector memory.** Record the
write thread pool queue and rejected counts in every cell of every stage below.

### Stage C — the flush grid, 15 cells

**The first thing round 2 settles**, because flush is the only setting that moves both
resources at once, in opposite directions — and because stages D, E and F all run at
whatever it chooses.

**What a shorter flush does:** hands chunks to the outputs sooner, so fewer are held
in memory. Round 1 measured `flush: 1` at 66 MB against a baseline of 116 to 128 MB,
at 20,000 records a second, and called it "the one clear win".

**What a shorter flush costs:** more bulk requests, each smaller. That is per-request
overhead on the collector *and* on OpenSearch, plus more small segments and more
merging. **Most of that cost lands on OpenSearch** — which is where our four cores
are tightest.

🔴 **So round 1's verdict may be exactly backwards under the new budget.** It traded
memory, which we now know is abundant at 150 GB, for processor time, which is the
only thing we are rationed on. `flush: 1` was the right answer to a question we no
longer care most about.

**Bulk size is why the answer probably moves with rate.** Taking a record at roughly
400 bytes, and remembering the stream splits across four outputs:

| | `flush: 1` | `flush: 5` |
|---|---|---|
| At 1,000 records a second | A few hundred KB per bulk — small enough that per-request overhead dominates | About 2 MB — near the efficient range |
| At 20,000 records a second | About 8 MB — comfortable | About 40 MB — too large, and `storage.max_chunks_up: 64` starts to bite |

One value has to serve every rate. Finding which one is the point of the sweep.

**The grid:** flush **0.5 · 1 · 2 · 5 · 10** against rates **1,000 · 20,000 ·
50,000** — fifteen cells, **with OpenSearch in the path**. Round 1 tested one flush
value at one rate against a fake sink and measured memory only, so it could not see
the cost that decides this.

The 1,000 and 20,000 rows are pass/fail against the disqualification gate. **The
50,000 row is descriptive** — no flush value survives that rate against a real
OpenSearch, and the useful output there is which failure arrives first.

**Per cell, record:**

- Collector peak memory
- Collector core-seconds per million records
- **OpenSearch core-seconds per million records** — the deciding number
- Bulk requests a second, and mean bulk size in bytes
- Segment count and merge time
- Chunks up against the 64 cap
- Live-lane latency, which is floored by the flush interval

**Data safety is not a flush question, and must not be argued as one.** Every input
sets `storage.type: filesystem`, so records reach disk when they are chunked,
whatever flush says. A longer flush does not risk more data in a crash. The safety
criteria stay the round-1 four: delivered equals ingested, `dropped_records` is zero,
the chunk queue is flat, and chunks up stays under the cap. **A flush value that
fails any of those is disqualified regardless of what it saves.**

**The one non-resource effect:** the live lane's latency floor *is* the flush
interval — `deploy/roles/live_lane/README.md` says "live means five seconds, not
instant". Moving to 1 second makes the lane five times more responsive. That is a
product argument for a shorter flush, and it should be weighed openly rather than
arrived at by accident.

**Prediction, recorded so it can be proved wrong:** `flush: 1` wins memory at every
rate; total core-seconds is lowest somewhere between 2 and 5 seconds at the steady
rate; the burst rate prefers shorter. If those hold, the compromise lands near 2.

**Until it is measured, `fluent_bit_flush_seconds` stays at 5.** We do not adopt
`flush: 1` on round 1's evidence.

### Stage D — the heap grid, 7 cells

Heap **1, 2 and 3 GB** at 1,000 and 20,000 records a second, at the flush stage C
chose, with the container memory cap at roughly twice the heap. Memory is cheap here;
a 3 GB heap in a 2 GB container dies. Then one overload cell at 50,000 for the winner.

Expect a **knee, not a slope** — heap taken from the machine is page cache lost, and
segment merges live in page cache.

### Stage E — the core split, 3 cells

How the 4 cores divide between the collector and the worker's OpenSearch node, at the
winning flush and heap. **Informed by round 0, not fixed at 1+3**: if the collector
settles near 1.5 cores at the reference rate, a 1-core cap measures the cap and
nothing else.

### Stage F — confirmation, 12 cells

The chosen configuration against the shipped control, at all three rates, repeated —
so the headline claim rests on more than one run of each.

### Stage G — the interaction checks, 3 cells

The three assumptions listed under "How the sweep is run", tested rather than
trusted. **If the live lane × flush check flips a ranking, stage C's winner is
re-opened**, because that is the least safe of the three assumptions.

**What round 2 hands over:** the `flush`, heap, `AllowedCPUs` and `MemoryMax` values
the `collector` and `opensearch` roles should carry on an EPN worker. That is the
deliverable, not a chart.

---

## Round 4 — log templating (before embeddings, deliberately)

**Results: `docs/TEMPLATING_RESULTS.md`, 27 August 2026.** Measured against
45.6 million lines of the real archive. The new-template rate settles at 48.9 per
million lines, Drain3 in Python costs 19.73 core-seconds per million, and both
answers point the same way: round 3 is affordable and the rewrite can wait.

**Not in Fluent Bit.** There is no Drain filter, and a Lua or exec call per record
does not survive these rates. A sidecar fed by a Fluent Bit output, writing to the
local OpenSearch node, also gets its own cgroup — the only way to price it.

**Language: Python first, for the measurement.** Drain3 exists and is Python, so we
get a number this week. **If the per-line cost is real, production goes to Rust, C
or Go** — the measurement decides whether that rewrite is worth doing, and by how
much.

**The lookup, placed correctly:**

- **Hot path, in process.** Mask the line, hash it, look it up in memory. Drain's
  fixed-depth prefix tree *is* that cache. Microseconds, no network
- **Cold path, new template only.** Write it to the template index. Embed it there
- **The OpenSearch template index is the durable and shared copy.** It warms the
  tree on restart and lets the storage tier see every worker's templates. Never
  consulted per line — at 20,000 records a second that would be 20,000 queries a
  second

**The number that matters:** the **new-template rate** over a replay of the real
archive. It decides whether round 3 is affordable at all, and it is a genuine result
about ALICE log data in its own right.

**Also measure:** the per-line cost of masking plus tree lookup, in core-seconds per
million records, since that cost is paid on every line forever.

---

## Round 3 — embeddings, cheapest first

**A plain `knn_vector` field is enough** — the vectors do not need to be produced by
OpenSearch's own neural query path. That keeps the cheapest option available and
keeps the cost out of the OpenSearch heap.

Stated before running: a small transformer does hundreds to low thousands of short
texts per second per core. Against the burst rate on the cores we have, that is one
to two orders of magnitude short. **This round confirms a ceiling; it does not hunt
for a configuration that works.**

**The ladder:**

| Rung | What it is | Where it runs |
|---|---|---|
| Static embeddings — Model2Vec `potion` family | A sentence model distilled into a lookup table. No forward pass. `potion-base-32M` reaches 94.66 % of `all-MiniLM-L6-v2`'s MTEB average (52.83) at roughly 500× the speed, published above 20,000 sentences a second on a processor. `potion-base-2M`, `4M`, `8M` are smaller; `potion-retrieval-32M` is retrieval-tuned | Sidecar. Not in the OpenSearch built-in list |
| `paraphrase-MiniLM-L3-v2`, 3 layers, 384 dimensions | The smallest transformer OpenSearch ships | Either |
| `all-MiniLM-L6-v2`, 384 dimensions | The standard baseline | Either |
| A domain-trained FastText over our own templates | `docs/RESEARCH.md` records a domain FastText beating LLM embeddings 0.766 against 0.257 Micro-F1 on an incident corpus. For log text, cheap may also be *better* | Sidecar |

**Knobs that decide the number, swept together:**

- **Truncate to about 64 tokens.** Transformer cost grows with sequence length; a
  padded 512-token batch costs far more for nothing
- **Sort by length within a batch**, so padding is not the dominant cost
- **Batch size against intra-op thread count.** More threads with a small batch
  loses to thread overhead
- **int8 dynamic quantization**, measured for speed *and* recall loss against the
  float baseline

**Report at one core** and scale — the embedder will only ever get a fraction of 4.

**Stretch arm — late interaction, on templates only.** OpenSearch supports late
interaction models for **rescoring and reranking, not first-stage retrieval**, via
the `ml_inference` ingest processor and a rerank-by-field pipeline. One vector per
token makes it the most expensive option per document — wrong for per-line ingest,
but plausibly affordable over a few thousand templates, where the cost lands on
search rather than on the worker.

---

## Round 5 — the storage tier (later)

The workers are rationed; the storage machines are ours. `epn-infra13` has 64
logical processors and 355 GB available. So the question inverts: **how large should
the heap be before it stops helping?**

- Sweep well past 3 GB
- **Expect the knee below about 31 GB.** Above roughly 32 GB the JVM loses
  compressed object pointers, so a larger heap holds fewer objects than a smaller
  one. Everything above is better spent on page cache
- Measure indexing throughput, merge time and search latency together — a heap tuned
  for indexing alone will disappoint a shifter running a query

## Open questions

1. **Where do we tap InfoLogger in production?** See the section above. Thanasis
   tailed the O2 process files and never touched InfoLogger; infoLoggerD's own
   acknowledged queue would cover the rest. Lubos should confirm which we ship
2. *(Moved — see "Before stage A" at the top. It was too load-bearing to sit here.)*
3. **`epn146` and `epn323` are unsurveyed** — memory, cores and what else runs there
