# Soak round 2 — results

The plan is `docs/SOAK_PLAN.md`. This file carries what the runs actually said.
It is written stage by stage as each one closes, not assembled at the end.

Round 1 is `docs/SOAK.md`, 18 August 2026. It measured memory. This round
measures **processor time**, which is the resource an EPN worker rations.

---

## Read this first — what the round established, and what it retracts

**Written 27 August 2026, after a 47-cell re-run that invalidated most of what
the sections below claim.** Everything from "Stage C" onward was measured on a
rig that was quietly saturated. This section is the record of what survives.

### The one finding that matters

**Move `fluent_bit_flush_seconds` from 5 to 1.** Three runs of each,
alternating, at a rate where every cell offered without shortfall:

| | Shipped, flush 5 | **Chosen, flush 1** | Change |
|---|---|---|---|
| Total, all four cores | 108.40 | **104.67** | **−3.4 %** |
| **The collector's own cost** | 36.70 | **27.70** | **−24.5 %** |
| **Peak memory** | 87.1 MB | **62.7 MB** | **−28.0 %** |
| Live-lane latency floor | 5 s | **1 s** | 5× |
| Run-to-run spread | 1.5 % | 2.7 % | — |

**The ranges do not overlap** — the shipped arm's best run (107.82) is dearer
than the chosen arm's worst (106.15).

**Read the total honestly: 3.4 % is small.** The four cores are dominated by
OpenSearch and the storage tier, and they barely care about flush. What moves
is the part we are actually allowed to add to a worker: **the collector's own
footprint drops by a quarter and its memory by more than a quarter.**

**Two more results close the round:** heap size makes no difference to burst
absorption, so **1 GB stands**; and the rig sustains about **42,000 records a
second**, holding 50,000 for two minutes with zero loss. Both are in
*Round 2 addendum* below.

**One design question is closed on these numbers, not on preference:** a Kafka
bus between the collector and OpenSearch is rejected. The stack carries 538× the
busiest worker-second ever recorded, and Fluent Bit's buffer already covers a
17.4-hour outage. See *Kafka, decided against on this round's numbers*.

**One deployment requirement comes with it:** the stack's four cores must be
reserved away from the worker's other processes. Every number above assumes
four exclusive cores. See *The core retraction is about one kind of pinning*
below.

### The flush curve, the only knob that moves anything

Eight values, every cell offering at +0.00 %:

| flush | total | collector | memory |
|---|---|---|---|
| 0.125 | 107.34 | **20.40** | **61.1 MB** |
| 0.25 | 103.50 | 21.50 | 60.8 MB |
| 0.5 | 97.69 | 22.60 | 61.3 MB |
| 0.75 | 92.84 | 23.74 | 64.5 MB |
| **1** | **89.37** | 24.13 | 66.0 MB |
| 2 | 90.39 | 25.81 | 74.5 MB |
| 5 | 96.45 | 27.29 | 83.6 MB |
| 10 | 105.33 | 27.45 | 105.7 MB |

- **A near-symmetric U with its floor at 1.** Eight times too short costs about
  what ten times too long costs
- **The collector and the cluster want opposite things.** Collector cost falls
  monotonically as flush shortens, all the way to 0.125; the cluster's rises.
  The U in the total belongs entirely to OpenSearch and the storage tier
- **Best to worst is 17.9 % across a 20× range.** Flush matters, but far less
  than the saturated measurements suggested

The basin was resolved separately with three interleaved runs each, giving
**within-arm spreads under 1 %** — the tightest data of the round:

| flush | mean | spread | collector | memory |
|---|---|---|---|---|
| 0.5 | 94.27 | 0.37 % | 22.38 | 62.9 MB |
| 0.75 | 92.84 | 0.57 % | 23.74 | 64.5 MB |
| **1** | **92.19** | 0.85 % | 25.10 | 66.5 MB |

Flush 1 beats 0.5 by 2.21 % (real, floor 0.85 %) and 0.75 by 0.71 % (a tie).
**0.75 and 1 are interchangeable.**

### Withdrawn

🔴 **Every one of these was reported during the round and is now retracted. All
share one cause: the rig was above its clean capacity, so cells differed in
whether they KEPT UP rather than in what their knob cost.** A cell that falls
behind spends its time on retries and backed-up buffers, and its cost per
record inflates. Cost tracked offer shortfall almost perfectly — the cells that
offered cleanly read about 77 core-seconds per million, the cell that fell
8.15 % behind read 278.

| Claim as reported | Re-measured clean | Verdict |
|---|---|---|
| Core isolation is worth **52 %** | 2.7 % against a 5.5 % floor, ranges overlap | **No effect** |
| OpenSearch needs 3 of 4 cores (**72 %**) | The 2-core arm reads 101.61 and offers perfectly | **No effect** |
| Heap 2g wins by **8–22 %** | 2.1 % and 0.4 % against a 6.7 % floor | **No effect** |
| Heap ranking flips with flush | 3g leads at both flush 0.5 and 5 — no flip | **No interaction** |
| `t3` is **6.2 % cheaper** on four cores | t3 costs **8.8 % more**; stage B was right | **`t0` stands** |
| Flush 0.25 **breaks ingestion** | It offers at −0.98 % and costs 6 % more | **Costs more, breaks nothing** |
| Below flush 0.5 there is a **drain ceiling at ~13,800/s** | An artefact of a failing machine | **No ceiling** |
| The architectural cap is **120,000 records/s** | Straight-lined a curve known to bend | **~42,000/s sustained on this rig** — see the addendum |
| Prediction two "scored right" | Scored on saturated evidence | **Unscored** |

**Heap size and core placement make no measurable difference on a four-core
budget.** That is a useful negative result: it is tuning that can be skipped.

### The core retraction is about one kind of pinning, and production still needs the other

🔴 **Production must pin the logging stack away from everything else on the
worker. Do not read the retraction above as permission to skip that.**

There are two different things called pinning, and only one of them was tested:

| Kind | What it separates | Verdict |
|---|---|---|
| **External** | The logging stack's four cores from the worker's other processes | **Required. Never measured, because the rig always did it** |
| **Internal** | The collector from OpenSearch, inside those four cores | **No effect at the rates tested** |

The retraction covers the internal split only. The external separation was
never an arm of the experiment. It was a fixed property of the rig, present in
every cell. The plan pins the generator to cores 8–9, the fake sink and live
lane to cores 10–11, and the storage tier to cores 4–7, so that nothing but the
collector and the worker's own OpenSearch node touches cores 0–3. Round 1's
five-per-cent artefact came from starving exactly that sink.

**Every cost in this document therefore assumes four exclusive cores.** An EPN
worker runs reconstruction work beside the logging stack. If those processes
share the same four cores, the stack gets less than four, and each number here
becomes optimistic. The size of that error is unmeasured.

**Use a real reservation, not a share weight.** A weight limits the average and
lets a neighbour take the cores during a burst, which is when the collector
needs them. A `cpuset` grants the cores outright.

**Why the internal split reads as no effect.** At the rate measured, neither
service is close to filling its share, so there are spare cycles and keeping
them apart changes nothing. The collector costs about 27.8 core-seconds per
million records at 5,000 a second, and the whole stack about 105.

🔴 **Do not extrapolate those figures to a saturation rate. The cost curve
bends, and this round measured the bend.** The same collector charges 27.8
core-seconds per million at 5,000 a second, 11.20 at 20,000, and **8.97 at
50,000**. Cost per record *falls* as rate rises, because larger batches amortise
the per-flush work. A straight line drawn from the 5,000-a-second point puts
saturation near 38,000 a second, and stage B disproves that directly: the
collector alone took **33 million records at 50,000 a second, lost none, and
ended with an empty queue**, using 0.352 of one core.

**What is therefore known, and what is not:**

- ✅ The collector alone is comfortable at 50,000 a second on four pinned cores
- ❌ The **full stack** — collector plus the worker's OpenSearch node plus the
  storage tier — has never been run above 5,000 a second in steady state, nor
  above a 30,000-a-second burst of 30 seconds
- ❌ The rate at which internal placement starts to matter is **unmeasured**

Internal pinning pays only once one of the two services genuinely wants more
than its share. Nothing measured in this round reached that point.

### What still stands from earlier in the round

- ✅ **The OpenSearch duplication defect.** Mechanism proven, fix shipped to
  `collector.yaml.j2`. See the defect section below
- ✅ **The rig's heap-ceiling defect.** A 2 GB heap in a 2 GB container cannot
  start; `container_ceiling()` fixes it
- ✅ **`t0` beats every threading arm**, now confirmed twice on two machines
- ✅ **gzip beats zstd on the live lane** — measured on the healthy rig with
  four runs per arm
- ✅ **The live lane costs the collector real processor time** — 28 % here,
  41–68 % on the healthy rig. Direction and rough size agree

### The instrument, stated plainly

**These numbers were taken on a machine running at about 41 % of its normal
speed.** Partway through the round the host stopped scheduling work on its
twelve performance cores and confined everything to its four efficiency cores —
system-wide, not specific to the rig, and not cleared by restarting the virtual
machine, by `caffeinate`, or by lifting background task policy. A host restart
was not available.

The response was to lower the reference rate from 20,000 records a second to
**5,000**, chosen by probing with the most demanding configuration in the plan
so that no cell could saturate. Sampling was coarsened from one second to four,
because the recorder was itself competing for the same four cores; core-seconds
come from cumulative counters, so totals are unaffected.

**What that costs:** absolute core-seconds here are not comparable with the
healthy-rig figures earlier in this document, and 5,000 a second is a quarter
of the intended reference rate. **What it preserves:** every comparison is
internally consistent, and the flush ordering was re-validated at the new rate
before anything else ran.

🔴 **The whole round should be repeated once the host's performance cores
return.** The rankings here should transfer. The absolute numbers will not.

---

## The rate scope, decided rather than derived

🔴 **1,000 records a second is treated as PER WORKER throughout this round.
This is a decision, not a measurement.**

Three reasons, recorded so the decision can be argued with later:

1. **It is the conservative reading.** If the figure were per farm, the real
   per-worker rate would be up to two hundred times lower. Testing per worker
   sizes the worker safely; testing per farm would size it for a load it will
   never meet
2. **The rate ladder is only coherent at one scope.** Lubos set a 50,000 a
   second soak target for a single-worker rig. As a farm figure that target
   means nothing, so the ladder it belongs to is a worker ladder
3. **InfoLogger's own flood limit makes it plausible.** The client library
   drops a process that exceeds 1,000 messages in one minute, so 1,000 a second
   from one worker implies roughly sixty concurrent processes — which an EPN
   worker has

**Every rate in this document — 1,000, 20,000 and 50,000 — is therefore a
per-worker rate offered to one collector.**

### What each rate actually is, renamed

The plan calls 1,000 a second "the real steady rate". **It is not, and the
label should not survive this round.**

| Rate | The plan calls it | What it is |
|---|---|---|
| **1,000 /s** | "the real steady rate" | **A safety rate.** 43× the archive median of 23 a second per worker. Kept because the noise floor at anything lower makes an arm unrankable, not because a worker meets it |
| **20,000 /s** | the reference rate | The reference rate, and the only rate where arms can be ranked. 256× the busiest worker-second in six months |
| **50,000 /s** | Lubos's soak target | Above the collector's own ceiling on purpose. Never a pass-or-fail cell |

**The grid is unchanged.** Every cell runs at the rates the plan specifies. What
changes is what a result at 1,000 a second is allowed to conclude.

### The archive says something different, and that is recorded, not hidden

The derivation below was run before this decision was taken. It measures the
real per-worker rate in six months of CERN archive and it **contradicts the
assumption by a wide margin**: during the busiest hour in the whole archive a
worker carried **23 records a second at the median and 78 at its peak**, not
1,000.

**Both statements stand, and they are not in conflict about what to do.** The
soak runs a worker at 13× to 640× what the archive shows one carrying. That is
the safe direction, and it is what the decision above chose deliberately. The
derivation matters for a different question: **what the product will actually
meet in production**, which is where a sizing decision for real hardware should
come from.

**Still outstanding, and not a blocker:** the derivation covers InfoLogger
only. DDS and stdout need the run tarballs read, and the burst shape needs the
same. That scan saturates the network and the processor, and a scan running
beside a cell is what voided stage A's first attempt at A4 — so it runs when
the rig is idle, and its result is added here as a finding.

---

## Standing warning — read before any number below

🔴 **Everything here was measured on a laptop. The shapes, the knees and the
rankings transfer. The absolute rates do not.**

| | Laptop rig | `epn228` |
|---|---|---|
| Processor | Apple silicon, no simultaneous multithreading | 2 × AMD EPYC 7452, 32 cores each, 2 threads per core |
| Core count | 16 physical, 12 given to the rig | 64 physical, 128 logical |
| Generation | Current | 2019 |

Both differences make the laptop **optimistic**:

- **The worker's budget is 4 *physical* cores, so 8 logical processors.** The
  EPYC 7452 runs two threads per core, so asking for four physical cores hands
  the collector eight schedulable threads, not four. This is the opposite of
  what an earlier draft of this document said, and it makes the laptop
  comparison **less** unfavourable than stated there: the rig's 12 Apple cores
  have no simultaneous multithreading at all
- **The replay generator does not come out of that budget.** In production
  there is no generator — the logs arrive on their own. Every `gun` figure in
  this document is rig overhead and must be subtracted before comparing against
  the four cores
- **Per-core speed differs by a wide margin** between a current Apple core and
  a 2019 EPYC core

One micro-benchmark on `epn228` would give the conversion factor. It has not
been run.

**Two accepted divergences from production, both deliberate:**

1. **The storage tier is two nodes with one replica**; production has three
   nodes with two. The coordinating node here waits for two copies where
   production waits for three, so **every worker-side figure below is
   optimistic for that reason as well**. Two containers is what twelve
   processors afford, and it keeps the thing that matters to the worker — the
   bulk still waits for a real replica acknowledgement, which a single node
   with no replicas would not do
2. **Stage E's 1.5-core cell does not exist as written.** `cpuset` pinning
   cannot halve a core. E2 overlaps the collector and the worker's OpenSearch
   node on core 1 instead, which is the nearest honest thing to ask for, and it
   is reported as an overlapped core rather than relabelled as 1.5 cores

---

## The pre-flight blocker, answered from the archive

**The question.** The plan assumes 1,000 records a second **per worker**. If
that figure is really per farm, every rate in the plan is wrong by orders of
magnitude.

**How it was answered.** Nobody was asked. The figure was derived from the
data, with `tools/soak/archive_rate.py`, which reads every InfoLogger dump in
`s3://epn-backup-logs/infologger-2026/` and counts records per host per second
of event time.

**What was read.** 179 dumps, none failed, **248,828,513 records**, from
**312 hosts**, over the window **31 December 2025 to 29 June 2026** — just
under 180 days.

**The hosts split two ways, and the split matters:**

| Group | Hosts | Records | Share |
|---|---|---|---|
| Workers (`epnNNN`) | 308 | 221,707,411 | 89.1 % |
| Everything else — `epn-infra12`, `epn-calib0/1/2` | 4 | 27,121,102 | 10.9 % |

**`epn-infra12` alone produced 26,448,820 records, more than ten per cent of
the archive** — six times the busiest worker. A per-worker figure that
includes it is wrong, so every worker number below excludes those four hosts.

### The answer

**1,000 records a second is a farm figure, not a worker figure. A worker does
tens of records a second, not thousands.**

Most of those 180 days hold no data taking, so a percentile over the whole
window measures how often the farm is idle rather than how hard it works. The
figure that decides the question comes from the **busiest hour in the whole
archive** — 15 May 2026, 22:00–23:00 UTC, 20,151,049 records, every one of the
297 workers active.

🔴 **These are floors on the true rate, not measurements of it.** What the
archive holds is what reached S3 and survived to be dumped, which is smaller
than what the processes emitted, in at least three ways:

- **The client library drops before anything is stored.** A process over 1,000
  messages in one minute is cut off at the source, so the loudest processes are
  precisely the ones under-represented
- **The path has several hops that can shed** — `infoLoggerD`, the server, the
  MySQL insert — and none of them is audited here
- **The archive is a retention window, not a tape.** Whatever aged out is not
  in these numbers

**So read every figure below as "at least this much".** It bounds the rate from
underneath and cannot bound it from above.

**Per worker, during the busiest hour in six months:**

| | records a second, one worker |
|---|---|
| median second | **23** |
| 90th percentile | 33 |
| 95th percentile | 48 |
| 99th percentile | 57 |
| **busiest single worker-second** | **78** |

**The whole farm, over the same hour:**

| | records a second, 297 workers |
|---|---|
| median second | 6,785 |
| 95th percentile | 8,537 |
| busiest single second | **9,781** |

The farm's peak of 9,781 a second sits exactly at the bottom of the plan's
burst band of 10,000 to 20,000. **So the plan's rates are farm rates that have
been read as worker rates.**

For completeness, across all 180 days the farm's median second carries 3
records — that is the archive being mostly idle, not the farm being quiet
during a run.

🔴 **One second in the 180-day window carries 187,210 records farm-wide.** The
busiest-hour scan tops out at 9,781, twenty times lower, so that single second
is not corroborated by anything else in the archive. It is recorded here and
**not used**: it is either a real farm-wide event or an artefact of counting
timestamps in a dump, and telling those apart needs a separate look.

### Why the count can be trusted

A number this far from the plan's assumption deserves a check that it is not
counting the same record twice. It is not:

- **Each object in the bucket is a separate MySQL table, and each table is one
  day.** `tmp_messages_p28` starts at 2026-01-15 00:00:00, `p100` at
  2026-03-28 00:00:00, `p101` at 2026-03-29 00:00:00, `p150` at 2026-05-17
  00:00:00. One dump covers one day and no other dump covers it, so the 179
  dumps are disjoint by construction
- **The busiest hour therefore comes from a single dump**, not from 179
  overlapping ones

**What the records actually are** is worth knowing before the rate is quoted.
The bulk of a worker's traffic is one repeating message: `DPL` /
`readout-proxy` writing a `RAW ... size report` line per time frame. That is
why every worker sits so close to the same 23 a second, and why the top ten
workers in the busiest hour are within 0.6 % of each other. **The steady
per-worker rate is a heartbeat, not a burst of trouble.**

`epn-infra12`'s ten per cent share is the same story from the other side: it is
`ODC` answering status requests, a poller rather than a detector.

### Cross-check against the published figure

The 2025 ALICE log anomaly detection paper — the one this repository already
treats as required reading — says: *"In a single ALICE data-taking run, the
volume of log messages generated can reach up to one million."* Its dataset has
2,194,073 records at most in a sequence, and its keywords name the **FLP**
cluster.

**That figure and this one do not describe the same thing.** One million a run
on the FLP side sits far below 20,151,049 records in one hour on the EPN side.
The gap is expected — there are many more EPN nodes than FLP nodes, and the EPN
runs the reconstruction workflows that write the size reports. It is recorded
here so nobody reads the published number as a contradiction of this one.

An often-quoted "about 7 million FLP messages a day" appears in search results
but not in that paper's text, so it is **not** used here.

### What this changes

- **The sweep tests one worker at hundreds of times its real load.** At the
  reference rate of 20,000 a second, one collector is offered **256 times the
  busiest worker-second ever seen**, and about **twice the whole farm's peak**.
  That is not a reason to change the rates — headroom is the point — but it
  changes what a failure means. A configuration that fails at 20,000 a second
  has not failed at anything a worker will meet
- **The plan's own consequence does not follow.** The plan says that if the
  figure is per farm then "the 53× headroom figure evaporates". It is the other
  way round: each worker runs its own collector, so a per-farm 1,000 a second
  is about **3 a second per collector**, and the headroom grows rather than
  shrinks. The 53× figure was already conservative by a wide margin
- **Burst still matters more than steady state**, and for the reason round 1
  gave rather than this one: at a real worker's tens of records a second, the
  collector's processor cost is a rounding error. What can still hurt is a
  burst, an outage, or an output that stops draining
- 🔴 **The burst gap is the sharpest open question in this round, sharper than
  the tap question.** See "Open questions" below
- 🔴 **Worth putting to Lubos, since the archive cannot answer it:** does the
  1,000 a second cover all three log families or InfoLogger alone? This
  derivation is InfoLogger only — DDS and stdout carry no per-record event time
  in the archive in a form that counts per second without reading every tarball

**Recorded as an assumption:** the runs below use the plan's rates unchanged —
1,000, 20,000 and 50,000 records a second, all three families mixed. They are an
upper bound on what a worker is asked to do, by two to three orders of
magnitude, not a replay of it.

---

## Stage A — the instrument and the noise floor

Seven cells: the control run three times at 1,000 records a second, three times
at 20,000, and the generator measured against itself. Fake sink, shipped
configuration, four cores pinned, one minute of settle excluded from every
measured window.

**Cells: A1–A3 at 1,000/s, A4–A6 at 20,000/s, all PASS. A7 is the generator
selftest.** A4 was run twice; the first attempt was **void**, and why is worth
reading below.

### Which instrument measured which cell

🔴 **The first attempt at stage A did not run on one instrument, so its spread
was not a noise floor. Every cell was rerun.**

The recorder went through two revisions during the stage:

| Revision | What it did | Cells measured with it |
|---|---|---|
| **R1** | Per-container `cpu.stat` and `io.stat`, per-thread processor time | A1, A2, A3, A5, A6 — first attempt |
| **R2** | R1 plus its own sampling health, and the per-second peak divided by real elapsed time instead of an assumed one second | A4 — first attempt's rerun |

**What actually differs between them, checked rather than assumed.**
`core_seconds` is the last non-zero sample minus the first, on `cpu_usec`, and
that arithmetic is byte-for-byte the same in both. So are `mean_cores`,
`saturation_pct`, memory and the disk counters. R2 changes `peak_cores_1s`,
which no ranking uses, and adds a field R1 did not report.

**That reasoning was not accepted as sufficient.** A spread across cells
measured by two builds of the instrument is not a noise floor, whatever the
diff says. **All six cells were rerun on R2**, and every number in this stage
comes from that set.

### The noise floor, which is the point of the stage

🔴 **A difference smaller than these numbers is not a result.**

All six cells on recorder R2, all PASS, gate zero 0.00 % on every one.

| At | Collector core-seconds per million records | Spread across three runs |
|---|---|---|
| **20,000 /s** | 11.09 · 11.20 · 11.27, mean **11.19** | **0.18, which is 1.61 %** |
| **1,000 /s** | 71.50 · 75.81 · 78.64, mean **75.32** | **7.14, which is 9.48 %** |

**The rerun changed the floor in both directions, which is the argument for
having done it.** The first, mixed-instrument attempt read 1.95 % at the
reference rate and 28.07 % at the steady rate. Neither survives. The single
build reads **1.61 %** and **9.48 %**, and those are the numbers stage B is
ranked against.

**The two rates are still not equally usable.** At the reference rate the
collector's cost repeats to within one and a half per cent. At the steady rate
it scatters six times wider.

**Why:** at 1,000 records a second most of what the collector does is not work
on records. Tail files are re-scanned every five seconds, the health check
runs, the event loop wakes and finds nothing. Dividing that fixed cost by a
small number of records magnifies every wobble. At 20,000 a second the real
work dominates and the fixed cost disappears into it.

🔴 **The same two numbers say something larger than a noise floor: 75.32
core-seconds per million records at 1,000 a second against 11.19 at 20,000.**
The collector costs **6.7 times more per record** at the lower rate. That is
not the collector working harder — it is the same fixed overhead divided among
twenty times fewer records. **At 1,000 records a second the collector's cost is
almost entirely overhead, and per-record figures at that rate measure idling.**

**What this settles.** The plan already says screening arms run at the
reference rate only. That choice now has a number behind it: **an arm screened
at 1,000 records a second would have to move the cost by more than 9.5 % to
register**, and no arm in this plan will.

Memory is the mirror image and repeats far better than processor time.

### How Fluent Bit uses four cores — one thread, and it is not close

Per-thread processor time, from `threads.csv`, at 20,000 records a second:

| Thread | Threads | Core-seconds | Share |
|---|---|---|---|
| **`flb-pipeline`** — the main event loop | 2 | **68.6** | **89.3 %** |
| `flb-out-http.0` — the InfoLogger output | 2 | 5.7 | 7.5 % |
| `flb-out-http.1` — local family | 2 | 1.3 | 1.6 % |
| `flb-out-http.2` — central family | 2 | 1.0 | 1.3 % |
| `fluent-bit`, `flb-logger` | 2 | 0.02 | 0 % |

**Read one row further down than the table.** Of the two threads named
`flb-pipeline`, **one did 68.5 core-seconds and the other did 0.1**. There is
one main loop, it is single-threaded, and it does almost everything.

**The `workers` default on this version is 2, measured rather than assumed** —
every `http` output shows exactly two worker threads, and the template sets
`workers` explicitly only on the `health` output. That answers stage A's third
exit criterion.

**The consequence for stage B, stated before stage B runs.** The collector uses
**0.25 of one core** at 20,000 records a second, and 0.223 of that is one
thread. If the main loop is the whole cost, then:

- Three of the four cores are doing nothing for the collector and could go to
  OpenSearch
- The main loop at 20,000 a second sits near **22 % of one core**, which puts
  its own saturation somewhere near **90,000 records a second** — in the same
  region as round 1's measured ceiling of about 53,000, on a different machine
  and a different mix
- **`t2` is the only arm that can move this**, because it is the only one that
  takes work off `flb-pipeline`. `t1` alone moves the input read, not the
  filters

### Three predictions, pre-registered so each can be scored

Written before stage B runs, so none can be adjusted after the fact.

**Prediction one — only `t2` can move the number.** `t1` makes the inputs
threaded, which moves the file read off the main loop. It does not move the
parsers, the Lua or the record modifiers, and those are what `flb-pipeline`
spends its 89.3 % on. `t2` is the only arm that takes that work off the main
loop, so it is the only arm that can beat the 1.61 % noise floor. If `t1` beats
it and `t2` does not, this is wrong.

**Prediction two — one core is the right cap, and stage E will measure the cap
rather than the collector.** At 20,000 records a second the collector used
**0.25 of one core**; three of its four were idle on its behalf. So:

- **The collector should ship capped at one core**, and the other three should
  go to the worker's own OpenSearch node, which is the service that will
  actually want them
- **Stage E's two-core cells will therefore measure the cap, not the
  collector.** E2 and E3 give the collector more room than it has any use for,
  and their numbers will differ from E1 by less than the noise floor
- **The one thing that would overturn this is `t2`.** If moving the per-tag
  work into per-input threads raises the collector's total demand above one
  core, a one-core cap becomes a throttle instead of a fit, and this prediction
  is wrong

**Prediction three — the flush decision cannot rest on steady-state
core-seconds.** Stage C is a flush grid, and its 1,000-a-second row is where a
steady-state answer would come from. That row cannot carry the decision:

- **The row is 43 times the archive median**, so it is not a steady state
  anyone will meet
- **At that rate the collector's cost is 6.7× its per-record cost at 20,000,
  and the difference is overhead, not work** — measured above, 75.32 against
  11.19 core-seconds per million
- **The floor at that rate is 9.48 %**, so only a very large difference between
  flush values could even be seen

So the flush value will be chosen on **burst absorption** — the 50,000
`BURST` row, where flush sets how much is in flight — and on **the live lane's
latency floor**, which *is* the flush interval and is a product argument rather
than a resource one. If a flush value wins on steady-state core-seconds at
1,000 a second by more than the floor, this prediction is wrong.

**All three will be scored in the closing section, right or wrong, in the
manner round 1 scored its own.**

### Where we tap InfoLogger

Three places the InfoLogger stream can be picked up on a worker. Two are
measured in stage B; the third costs nothing new to measure and is not given a
cell.

| Tap | What it is | What it costs | Where it is priced |
|---|---|---|---|
| **`s0`** | The `tcp` input, straight into the collector, port 5170. What ships today | One `tcp` input. **Nothing behind the socket** — round 1 lost every record a fifteen-minute outage touched | **B12** |
| **`s1`** | Socket into a thin appender, appended to a file, and the collector tails that file | One appender process, one extra disk write and read per record, one `tail` input | **B13** |
| **Pure tail of the O2 process files** | No InfoLogger input at all. Tail the files the O2 processes already write, which is what Thanasis ran through Run 3 | **One `tail` input and nothing more.** Every cell in this round already prices a `tail` input, twice over, through DDS and stdout | Already measured, no cell needed |

**`s1` stays an arm.** It is not promoted into `collector.yaml.j2` and it is not
the control. The decision to ship a file tap comes after B13 reports its loss
figure for all three families and its cost in core-seconds per million records —
not before.

🔴 **The third tap has an open question this soak cannot answer: whether an EPN
worker's O2 process files actually carry InfoLogger content.** Thanasis's
worker collector has no InfoLogger input at all — four host inputs and eleven
`tail` patterns over `/var/log/calib/**` — and his planned `infologger` index
was never fed. Whether those files carry what the InfoLogger socket carries is
a question for Lubos. **No amount of soak testing settles it**, because the rig
generates its own records and cannot know what a real worker writes to disk.

### What the instrument now records, and what it caught

| Service | Pinned | Core-seconds | Per million records | Mean cores | Saturation |
|---|---|---|---|---|---|
| Collector | 4 | 76.8 | 10.67 | 0.250 | 6.3 % |
| Generator | 2 | 10.6 | 1.47 | 0.035 | 1.8 % |
| Fake sink | 2 | 2.9 | 0.40 | 0.009 | 0.5 % |

**No unmeasured service came near the 80 % rule** — the generator reached
1.8 % of its two pinned cores and the sink 0.5 %. Round 1's five-per-cent
artefact came from starving exactly these; they are not starved.

**Disk, which round 1 never measured.** At 20,000 records a second one cell
wrote **835 MB from the collector and 865 MB from the generator** — 1.7 GB of
writes for a five-minute cell on one laptop drive. The generator's own input
and output stall time was **2.3 seconds** across the window against the
collector's 0.072. The shared drive is real, it is measurable, and the
generator feels it first.

### The void cell, and the four instrument faults caught around it

🔴 **A4 failed gate zero on its first attempt: the offer came in 4.47 % under
intended, with one second at 97.5 % short.** The recorder shows why — sampling
gaps of up to **10.5 seconds** from t+194s, and 6.6 % of samples late. Both the
generator and the recorder stalled together, which is a laptop event, not a rig
fault. **Every other cell in the stage sampled perfectly: 308 samples, worst
gap 1.0 second, zero late.**

The cell was rerun rather than ranked, exactly as the plan requires. Two faults
in the measuring stick came out of it, and both are fixed:

- **The recorder did not report its own stalls.** Cumulative counters hide a
  missed second by folding it into the next delta, so only the per-second peaks
  looked wrong — A4 claimed 14.7 core-seconds in one second on four cores,
  which is impossible. The recorder now reports its sampling health, the
  per-second peak is divided by the real elapsed time, and **a cell with more
  than 2 % late samples is void**
- **A cell whose recorder wrote nothing was reported as a pass.** Gate zero had
  nothing to complain about and the safety criteria had no data to fail on. A
  run with no per-service figures is now void

**A third fault was caught before it ever reached a cell.** In `selftest` mode
the generator silently dropped the whole `infologger` family — six tenths of the
mix — and reported reaching 400 records a second when asked for 1,000. Gate zero
would have voided every cell in the plan for a fault in the measuring stick. It
now counts what it builds without sending it, and paces to within 0.02 % at
1,000, 20,000 and 50,000.

**And a fourth, in gate zero's own logic.** A paced run that hits its target
exactly proves pacing, not headroom — it never tried to go faster. The selftest
now ends with a ceiling probe. **The generator reaches 2,842,277 records a
second on its two pinned cores**, which is 57× the highest rate this plan uses,
and gate zero requires the cell's rate to sit at least 20 % under that ceiling.

---

## What was built before any cell ran

The rig could not run this plan as it stood. Four things were added.

| # | Built | Why |
|---|---|---|
| 1 | **`tools/soak/vmprobe.py`**, and `soakrec.py` rewritten around it | Round 1 sampled memory, for one container. Round 2 needs processor time and disk traffic for **every** container. Docker runs inside a Colima machine, so one shell is opened there and held; a whole-rig sample costs about **0.8 ms** |
| 2 | **`cpuset` pinning for every service** in `rig/docker-compose.soak.yaml` | The plan enforces with pinning, not a quota, and pinning sets no quota — so `nr_throttled` stays at zero and saturation has to be read from `usage_usec` |
| 3 | **Gate zero**, in `soak.py` | Achieved offer against intended offer, plus a `soak.py selftest` runner that proves the generator's own ceiling. A cell that fails it is **void**, not disqualified |
| 4 | **`tools/soak/cells.py`** | The manifest, run unattended in stage batches, writing one index per stage |

**Per-thread processor time** for the collector lands in `threads.csv`, one row
per thread per second, so the main loop and the output workers separate by
name rather than by thread number.

**Colima was raised from 8 processors to 12**, per the plan's harness
allocation: cores 0–3 to the worker's services, 4–7 to the storage tier, 8–9 to
the generator, 10–11 to the sink and the live lane.


---

## Stage B — the collector screen

Twelve cells at the shipped `flush: 5`, against the fake sink, at the reference
rate of 20,000 records a second unless the row says otherwise. **B6 is the one
cell that cannot be scheduled in advance**: it repeats the winning threading arm
at 50,000, and the winner was not known until the four arms were read.

**The control reproduces across stages.** B1 measured 11.20 core-seconds per
million records; stage A's three control cells at the same rate measured 11.09,
11.20 and 11.27. The instrument and the control are stable, so stage B's
comparisons rest on something.

### The threading arms — a clear answer, and not the one predicted

| Arm | What it changes | Core-seconds | Per million | Against `t0` | Main loop | Main-loop share | Peak memory |
|---|---|---|---|---|---|---|---|
| **`t0`** | as shipped | 80.6 | **11.20** | — | 72.5 | **90 %** | 132.4 MB |
| **`t1`** | `threaded: on` on both tails and the tcp input | 89.9 | **12.48** | **+11.5 %** | 57.6 | 64 % | 148.4 MB |
| **`t3`** | two processes: tailed families in one, InfoLogger in the other, both inside the same four cores | 86.6 | **12.03** | **+7.4 %** | 31.5 (process 1) | — | see below |
| **`t2`** | `t1`, plus every per-tag filter moved into its input's `processors` | 102.0 | **14.16** | **+26.5 %** | **7.0** | **7 %** | 144.5 MB |

**`t3` is the cheapest of the three arms and still costs more than shipping as
is.** The split works — process 1 takes 34.5 core-seconds for the tailed
families, process 2 takes 52.1 for InfoLogger, and both sit inside the same four
cores at 0.112 and 0.169 mean cores. Two main loops instead of one doubles the
ceiling by construction. It costs 7.4 % to have it.

**`t3` ran twice.** Its first attempt produced nothing: `soak.py` passed
`--arm t3` to `mkconfig.py`, which only knows `t0`, `t1` and `t2`, because the
two-process split is expressed by `--families` and not by the arm. The fix was
checked to be identity for the arms already measured — `t0`, `t1` and `t2`
re-render **byte-identical** configuration files under the fixed code — so only
`t3` was rerun.

🔴 **Every arm works exactly as designed, and every one makes the collector
more expensive.** The ranking is `t0` 11.20, `t3` 12.03, `t1` 12.48, `t2` 14.16
core-seconds per million records. **The shipped configuration is the cheapest
thing measured in this stage.** This is the stage's finding and it is worth stating carefully,
because the two halves point in opposite directions:

- **The mechanism does what the plan said it would.** `t2` moves the parsers,
  the Lua and the record modifiers off the main event loop, and the main loop
  falls from **72.5 core-seconds to 7.0** — from 90 % of the collector's cost
  to 7 %. There is no ambiguity about whether `processors` run in the input's
  own thread. They do
- **And the total cost rises by more than a quarter.** 80.6 core-seconds
  becomes 102.0. The work did not get cheaper by moving; it got more expensive,
  because now it is handed between threads, queued, and synchronised

**Both differences are far outside the 1.61 % noise floor** — `t1` is seven
times the floor and `t2` is sixteen times it. These are results, not scatter.

**What it means for the ceiling question the plan asked.** The plan asked
whether a second core buys throughput at all, and said that if `t2` does not
beat `t0`, one core is the architectural cap. The answer is subtler than either
branch:

- **The main loop is no longer the cap under `t2`.** At 7.0 core-seconds over a
  five-minute window the main loop is nearly idle, so the *record ceiling*
  under `t2` is much higher than under `t0`
- **But the collector is not near its ceiling at 20,000 records a second under
  any arm.** `t0` uses 0.25 of one core. Buying ceiling headroom we have no use
  for, at a 26.5 % permanent increase in processor time, is the wrong trade on
  a machine that rations exactly that
- **So `t0` wins on cost and `t2` wins on ceiling**, and which matters depends
  on a rate no worker in the archive comes close to

**Which arm B6 repeats, and why it is not the cost winner.** B5 already ran
`t0` at 50,000 records a second, and B6 is meant to answer whether the winning
arm raises the ceiling. On cost the winner is `t0`, which B5 has already
measured — repeating it would answer nothing. **B6 therefore runs `t2` at
50,000**, because `t2` is the only arm that empties the main loop and so the
only candidate for raising a ceiling that the main loop sets.

### Prediction one, scored: wrong

> *"`t1` makes the inputs threaded, which moves the file read off the main loop.
> It does not move the parsers, the Lua or the record modifiers... `t2` is the
> only arm that can beat the 1.61 % noise floor."*

**Wrong on its main claim.** `t1` moved the number by 11.5 %, seven times the
floor, without touching a single filter. Threading the inputs alone is not
free, and the prediction assumed it would be close to it.

**And wrong in a way the prediction did not consider at all.** It was written
as though moving work off the main loop would *reduce* the collector's cost.
Every arm that moved work off the main loop increased it. The prediction had no
branch for "the mechanism works and the answer is still no", which is exactly
what happened.

### The spool arms — the file tap does not fix the loss

Two 21-minute cells at 20,000 records a second, each with a fifteen-minute sink
outage inside it: 180 seconds of lead, 900 of outage, 180 of load after the
restore. This is round 1's loss finding, run against both taps.

| | `s0` — TCP as shipped | `s1` — appender, file, tail |
|---|---|---|
| InfoLogger lost | **10,086,158 of 15,120,000 — 66.7 %** | **10,383,758 of 15,120,000 — 68.7 %** |
| DDS lost | **0** | **0** |
| stdout lost | **0** | **0** |
| First drop | 60 s into the outage | 36 s into the outage |
| Buffer on disk at its peak | 264.5 MB | 269.0 MB |
| Collector cost | 15.46 core-seconds per million | **9.38** |
| Appender cost | — | **0.55** |

🔴 **`s1` did not reduce the loss. It lost marginally more.** The two figures
are 66.7 % and 68.7 % of the InfoLogger stream, which is the same answer twice.
**Putting a file in front of the socket does not save the records.**

**Why, and this is the finding that matters.** The loss is not at the input, so
a queue in front of the input cannot prevent it:

- **The buffer on disk stopped at 264.5 MB and 269.0 MB**, against the
  `storage.total_limit_size` of **256M** the control sets on every output. The
  cap was reached and Fluent Bit discarded the oldest chunks, because
  `storage.pause_on_chunks_overlimit` is off
- **Between 9 % and 12 % of what the generator offered never entered the
  collector at all** — 22.2 M of 25.2 M in `s0`, 22.9 M of 25.2 M in `s1`. That
  is the tail inputs pausing, and the backlog waiting in the files
- **So DDS and stdout survive for a reason that has nothing to do with
  durability: their inputs stop reading.** The file is a queue **only for what
  has not been read yet**. Once a record is inside Fluent Bit it belongs to an
  output's buffer, and that buffer has a 256 MB cap
- **InfoLogger is 60 % of the mix here**, three times each other family, so its
  output buffer reaches the cap first and drops while the others do not

**What would actually fix it**, in order of how well this round supports it:

1. **Raise `storage.total_limit_size` on the InfoLogger output**, or make it
   proportional to that family's share of the stream. The loss begins precisely
   when the cap is hit
2. **Turn on `storage.pause_on_chunks_overlimit`** so the input stops instead
   of the buffer discarding — the behaviour the tailed families already get for
   free. **This trades loss for back-pressure and is not free**; nothing in
   this round measures what that back-pressure does to the tcp input
3. **The tap is not the lever.** Neither `s0` nor `s1` addresses this

**Two things `s1` did buy, and they should not be lost in the headline:**

- **It made the collector cheaper**, 9.38 core-seconds per million against
  15.46, and the appender adds only 0.55 — about 9.9 against 15.5 all in. A
  `tail` input costs less than a `tcp` input plus JSON parsing
- 🔴 **These are outage cells, so the cost comparison is confounded** by
  different retry work and different ingested totals. It is a signal, not a
  measurement, and it wants a steady-state repeat before anyone ships on it

**`s1` remains an arm.** It is not promoted into `collector.yaml.j2` and it is
not the control. On this evidence it does not earn promotion on durability
grounds, because it does not deliver durability.

### B14 and B15 — the pause knob, and what the loss actually is

B12 and B13 tested the tap and the pause as alternatives. They are
complementary — pausing an input only helps if the backlog has somewhere to
wait — so two more cells turned the pause on.

| Cell | Tap | `pause_on_chunks_overlimit` | InfoLogger lost | Entered the collector |
|---|---|---|---|---|
| B12 | tcp | off | 66.7 % | 88.3 % |
| B13 | file | off | 68.7 % | 90.9 % |
| **B14** | **file** | **on** | **68.7 %** | 91.3 % |
| **B15** | **tcp** | **on** | **66.8 %** | 97.2 % |

🔴 **The pause changed nothing. Same loss, to a tenth of a per cent.**

**Because it is the wrong knob.** `storage.pause_on_chunks_overlimit` is an
*input* setting: it pauses an input when that input's own storage limit is
reached. The loss is not there. It is at the **output's**
`storage.total_limit_size`, and when an output's backlog exceeds that, Fluent
Bit discards the oldest chunks — there is no pause option on that path at all.

### And the loss itself is an artefact of the test rate

The arithmetic nobody did before building an appender:

| | |
|---|---|
| Mean record on the wire | **310 bytes** |
| `storage.total_limit_size` | 256 MB |
| So the buffer holds | **865,674 records** |

Divide that by the InfoLogger rate — 60 % of the mix — and the buffer's outage
tolerance falls out:

| At | InfoLogger records a second | The 256 MB buffer covers |
|---|---|---|
| The soak reference rate, 20,000 /s | 12,000 | **72 seconds** |
| **A real worker, 23 /s** | 13.8 | **17.4 hours** |
| The busiest worker-second in six months, 78 /s | 46.8 | **5.1 hours** |

**72 seconds is independent corroboration of round 1**, which measured the
buffer holding for 61 seconds at the same rate without doing this arithmetic.

🔴 **So the InfoLogger loss finding is a property of testing at 870× a real
worker's rate, not a property of the product.** At what a worker actually
carries, the shipped 256 MB buffer absorbs an outage lasting most of a day.

**What this retires:**

- **The spool arm.** `s1` solves nothing at test rates and is unnecessary at
  real ones. Not shipped
- **The pause knob.** Wrong lever, and needed for nothing
- **Round 1's ten-million-record loss** as a product concern. It is real, it is
  reproducible, and it requires a sustained rate no worker approaches

**What it does not retire:** the buffer is finite, and a long enough outage at a
high enough rate still discards. If anyone wants a hard guarantee rather than a
seventeen-hour cushion, the lever is `storage.total_limit_size` on the
InfoLogger output — sized from this arithmetic — and nothing else.

### The live-lane arms

Lane off is the control, so B1 is the comparison.

| Cell | Arm | Collector per million | Against `t0` | Lane server cost | Lane dropped |
|---|---|---|---|---|---|
| B1 | lane off | 11.20 | — | — | — |
| B7 | `l1` lane on, real `live_lane.py` | 16.24 | **+45.0 %** | 0.37 per million | 51,511 |
| B8 | `lw` lane output at `workers: 0` | 18.76 | **+67.5 %** | 0.37 per million | 213,662 |
| B10 | `lv` 5 viewers | 17.87 | +59.6 % | **2.25 per million** | 158,831 |
| B11 | `lv` 20 viewers | 15.84 | +41.5 % | **9.71 per million** | 35,703 |

**The lane is expensive on the collector — between 41 % and 68 % more processor
time.** That is far outside the noise floor and it is the largest single cost
any arm in this stage adds.

**`workers: 0` on the lane output is worse, not better.** B8 costs 67.5 % where
B7 costs 45 %, and it dropped four times as many lane records. The default is
the better setting.

**The lane server's own cost scales with viewers, roughly linearly**: 2.25
core-seconds per million at five viewers, 9.71 at twenty — about 0.5 per
million per viewer. At the production shape of about five viewers this is
small, **and it lands on the lane server rather than on the worker's four
cores**, which is the argument for Thanasis's choice of pushing the live feed
from the aggregator instead of the worker.

🔴 **The three lane-on cells that should agree do not.** B7, B10 and B11 differ
only in viewer count, which cannot change the collector's cost, yet they read
16.24, 17.87 and 15.84 — a spread of 12 %, seven times the noise floor. The
lane's own drop counts vary by a factor of four across them, and retry work
varies with drops. **The lane cells are noisier than the rest of the stage and
a firm collector-side lane cost needs repeats.** What survives the noise is the
direction and the rough size: the lane costs the collector tens of per cent,
not a few.

### Lane compression — gzip against zstd, four runs each

The shipped template compresses the live lane with gzip. B17 and B18 swap that
for gzip and zstd explicitly, and after a single run of each suggested zstd was
14.4 % cheaper, **both were repeated three more times** — because the lane cells
had already been flagged as the noisiest in the stage.

Collector cost, core-seconds per million, at 20,000 a second into the fake
sink:

| Arm | Run 1 | Run 2 | Run 3 | Run 4 | Mean | Spread |
|---|---|---|---|---|---|---|
| `lc-gzip` | 17.48 | 15.34 | 15.39 | 19.03 | **16.81** | 22.0 % |
| `lc-zstd` | 14.97 | 19.16 | 16.48 | 18.80 | **17.35** | 24.1 % |

🔴 **The 14.4 % gap was noise, and the sign reverses on the means.** Each arm's
own run-to-run spread is 22–24 %, wider than the gap between them, and the four
values interleave completely. **zstd does not reduce the collector's cost.**
The earlier single-cell reading is withdrawn.

**Two things the repeats did settle, both against zstd for our purpose:**

| | gzip | zstd |
|---|---|---|
| Bytes on the wire, 5 minutes | 7.4 / 7.6 / 7.6 MB | 8.3 / 8.4 / 8.3 MB |
| Lane server processor cost | 0.08 per million | **0.04 per million** |

- **zstd puts about 10 % more bytes on the wire** at Fluent Bit's default
  level. gzip compresses the lane payload tighter
- **zstd halves the receiver's decompression cost**, 0.08 to 0.04
  core-seconds per million, with no overlap between the arms. That is a real
  effect and an irrelevant one: **0.08 core-seconds per million is 2
  microseconds of processor time per record**, and it lands on the lane server
  rather than the worker's four cores

**Verdict: keep gzip.** It is not slower on the collector, it sends fewer
bytes, and the only axis zstd wins is too small to spend a configuration change
on.

*(The wire-byte figures come from the three repeat runs only. `sink.py` did not
record `Content-Encoding` when B17 and B18 first ran, and an earlier reading of
the wrong stats file led me to report that zstd was never applied at all. It
was.)*

### `lt` — the lane on its own tag, rejected on evidence

🔴 **`lt` voided gate zero twice, at −46.53 % and −46.64 %. It is the only cell
in the stage that did, and the cause is the arm.**

The plan's rule is that a cell failing gate zero twice means the generator is
the problem. **Here it does not, and the evidence is unambiguous:**

- **Ten other cells offered the same 20,000 records a second, from the same
  generator, with the same fixture, and every one hit its target to +0.00 %**
- **The generator's measured ceiling is 2,842,277 records a second**, 142× this
  rate
- **The recorder was clean in both attempts** — 312 samples, worst gap 1.0
  second, zero late
- **Nothing was dropped.** The collector lost no record from any family; it
  simply could not accept what was offered

**What is happening.** `lt` adds a `rewrite_tag` that copies every matching
record to a `lane.*` tag with `keep true`, which is about four fifths of the
stream re-injected through the emitter and put through the filter chain a
second time. That work lands on the main event loop, the loop stops draining
the tcp socket fast enough, the socket buffer fills, and **the generator blocks
on the write**. Gate zero sees the generator falling short, because it is —
but the back-pressure originates in the configuration under test.

**So the cell is not void, and the arm is not undecided: `lt` is rejected.**
The plan listed this exact risk against the arm — *"duplicating four-fifths of
the stream through the emitter costs memory and main-loop time"* — and the cost
turns out to be that the collector accepts **half** the offered rate.

**The chunk-retention problem `lt` was meant to solve is real and still
unsolved.** A slow lane does hold InfoLogger chunks open, because a chunk is
freed only when every matching output has finished with it. `lt` is not the way
to fix it. On this stage's evidence the better direction is the one Thanasis
took — **move the live feed off the worker entirely** — which also removes the
41–68 % collector cost measured above. The trade is that a worker-fed lane keeps
working when OpenSearch is red, and an aggregator-fed one does not.

### The ceiling cells — 50,000 a second is not above the ceiling

Two `OVER` cells, ten minutes each. **The plan says of them: "A 50,000 records
a second cell will fail the safety gates by construction."**

| | B5 — `t0` | B6 — `t2` |
|---|---|---|
| Offered, and achieved | 50,000 /s | 50,000 /s |
| Ingested | **33,000,000 of 33,000,000 — 100 %** | **33,000,000 of 33,000,000 — 100 %** |
| Lost for good | **0** | **0** |
| Queue at the end | **empty** | **empty** |
| Collector cost | **8.97** per million | **10.72** per million |
| Main loop | 254.4 core-seconds, **86 %** | 12.2 core-seconds, **3 %** |
| Mean cores | 0.352 | 0.512 |
| Peak memory | 198.5 MB | 215.1 MB |

🔴 **Neither cell failed anything. 50,000 records a second is comfortably inside
this collector's ceiling, not above it.** Both took every record offered, lost
none, and drained to an empty queue. The plan's premise for the whole `OVER`
category does not hold on this rig.

**Round 1's ~53,000 a second was measured on two cores.** These cells have four
pinned, and the collector used **0.352 of one** at `t0`. The ceiling moved
because the constraint moved.

**Where the cap is: not established, and an earlier draft of this document
over-claimed it.** That draft straight-lined the main loop from 0.352 cores at
50,000 a second to one full core and reported a cap near 120,000 records a
second. **That figure is withdrawn.** Three reasons it does not hold:

1. **The extrapolation assumes the cost curve stays straight**, and this stage
   already measured that it does not. The same collector charges 11.20
   core-seconds per million at 20,000 a second and 8.97 at 50,000 — the curve
   bends with rate, so projecting one point along a straight line is not a
   measurement of anything
2. **Round 1 found a read-rate limit** that the extrapolation ignores entirely.
   A cap set by how fast the tails can read is not raised by having processor
   time spare
3. **Nothing was run above 50,000 a second**, so the cap is above 50,000 and
   that is the whole of what the evidence supports

**What the cells do establish, and it is enough for the threading decision:**
50,000 records a second — 2,000× a real worker's rate — costs the collector
**0.352 of one core**. The ceiling is far enough away that no arm buying
headroom above it can pay for itself. `t2` empties the main loop, from 86 %
down to 3 %, and charges 19.5 % more per record at 50,000 a second to do it.
**That is the wrong trade for a worker carrying 23 records a second, whatever
the exact cap turns out to be.**

🔴 **Finding the real cap needs a rate ramp above 50,000 that runs until
something actually breaks.** No such cell exists in this plan. Until one runs,
this document states no ceiling number.

**One rate effect worth keeping.** `t0` costs 8.97 core-seconds per million at
50,000 a second against 11.20 at 20,000 — **19.9 % cheaper per record at the
higher rate**. Same fixed overhead, more records to spread it across. It is the
1,000-a-second overhead effect running in the other direction, and it means
**per-record costs must never be compared across rates.**

### What stage B hands to stage C

**Nothing from this stage is adopted. The shipped configuration wins every
comparison it was put in.**

| Question stage B asked | Answer | Ships |
|---|---|---|
| Does threading buy anything? | Every arm costs more: `t3` +7.4 %, `t1` +11.4 %, `t2` +26.4 % | **`t0`, unchanged** |
| Is one core the architectural cap? | No. The collector uses **0.352 cores at 50,000 a second** and failed nothing. The cap is above 50,000; **this document puts no number on it** | **No change** |
| Does a spool fix the InfoLogger loss? | No. `s0` lost 66.7 %, `s1` 68.7 %. The loss is at the output buffer's 256M cap, not at the input | **`s0`, unchanged** — the fix is the buffer, not the tap |
| Does the lane need its own tag? | `lt` halves what the collector accepts. Rejected | **No change** |
| Is `workers: 0` better on the lane output? | No — 67.5 % against 45 %, and four times the lane drops | **The default, unchanged** |

**Three things stage C inherits:**

1. **The 50,000-a-second row will not saturate the collector.** It saturated
   nothing here. With OpenSearch in the path it may well saturate *there*, and
   that is now the only thing those cells can be testing
2. **Per-record costs cannot be compared across rates** — 8.97, 11.20 and 75.32
   core-seconds per million at 50,000, 20,000 and 1,000 are the *same
   collector* doing the same work
3. **The live lane's collector cost of 41–68 % is the largest single lever
   found so far**, and it is a product decision rather than a tuning one. Moving
   the lane off the worker removes it outright, at the cost of a lane that stops
   working when OpenSearch is red

---

## Stage C — the flush grid

> ⚠️ **Superseded.** Everything in this stage was measured on a saturated rig.
> The flush ordering it found is right in shape but wrong in magnitude, and its
> 0.5-over-1 verdict is reversed by the clean re-run. See **Read this first**.


Fifteen cells against the three-node cluster: five flush values crossed with
1,000, 20,000 and 50,000 records a second. The 50,000 row is `BURST` — thirty
seconds at peak, 120 quiet, repeated — because a sustained 50,000 into
OpenSearch saturates identically at every flush value and separates nothing.

**One stated deviation from the shipped template**, `buffer_size: False` on the
`opensearch` outputs. See the defect section below for why the alternative
measures a bug rather than a knob.

### The grid

Core-seconds per million records ingested, by service. **`total` is the number
the four cores actually pay**, because the collector and the worker's OpenSearch
node share them.

| Cell | Rate | Flush | Collector | Worker OS | Storage tier | **Total** | Peak memory | Peak chunks |
|---|---|---|---|---|---|---|---|---|
| C6 | 20,000 | **0.5** | 11.21 | **19.62** | 39.24 | **70.07** | **98.3 MB** | 10 |
| C7 | 20,000 | **1** | 11.42 | 22.93 | 40.42 | **74.77** | 108.7 MB | 10 |
| C8 | 20,000 | **2** | 12.04 | 25.22 | 46.58 | **83.84** | 122.2 MB | 11 |
| C9 | 20,000 | **5** *(shipped)* | 12.15 | 29.55 | 47.74 | **89.43** | 171.5 MB | 17 |
| C10 | 20,000 | **10** | 11.42 | 31.84 | 47.22 | **90.47** | 214.4 MB | 34 |

🔴 **Every axis points the same way, and it is the opposite of what the plan
expected.** Shorter flush is cheaper in processor time, not more expensive:

- **The worker's OpenSearch node costs 62 % more at flush 10 than at flush
  0.5** — 31.84 against 19.62 core-seconds per million. This is the number the
  plan called "the deciding number", and it decides against long flush
- **Total cost across all three services rises monotonically with flush**, from
  70.07 to 90.47 — **29 % more at flush 10 than at flush 0.5**
- **Memory rises monotonically too**, 98.3 MB to 214.4 MB, confirming round 1's
  memory finding rather than trading against it
- **Chunk depth rises from 10 to 34**, against the `storage.max_chunks_up` cap
  of 64. The shipped flush of 5 already sits at 17

**The collector's own cost barely moves** — 11.21 to 12.15, a spread of 8.4 %
where the noise floor on that service is 1.61 %. Real, but small, and swamped by
what happens downstream.

**The plan's reasoning was that a shorter flush means more bulk requests, each
smaller, and that the per-request overhead lands on OpenSearch.** The
measurement says the opposite dominates: **large bulks cost OpenSearch more per
record than small ones do**, across the range tested.

### The 1,000-a-second row is unusable, exactly as predicted

| Flush | 0.5 | 1 | 2 | 5 | 10 |
|---|---|---|---|---|---|
| Total core-seconds per million | 186.29 | 160.50 | 153.68 | 161.38 | **149.15** |

**The ordering is non-monotonic and its cheapest value is flush 10 — the exact
reverse of the reference rate.** With a 9.48 % noise floor on the collector at
this rate and no repeats on the cluster services, this row cannot rank anything.
It is retained as evidence that it cannot, which is what stage A predicted it
would be.

### The burst row

| Flush | 0.5 | 1 | 2 | 5 | 10 |
|---|---|---|---|---|---|
| Total core-seconds per million | 73.37 | **71.85** | 75.44 | 101.69 | 80.35 |
| Peak memory | **125.2 MB** | 154.0 MB | 181.6 MB | 281.8 MB | **333.7 MB** |
| Peak chunks | **12** | 17 | 23 | 50 | **68** |

**Burst absorption favours short flush on every axis that matters.** Memory
grows 2.7× from flush 0.5 to flush 10, and chunk depth grows from 12 to
**68 — past the `storage.max_chunks_up` cap of 64**. Flush 5 is an outlier on
cost at 101.69 and the burst cells are visibly noisier than the steady ones, so
the cost column here is indicative; the memory and chunk columns are not
ambiguous.

### Prediction three, scored: half wrong

> *"The flush decision cannot rest on steady-state core-seconds. It rests on
> burst absorption and on the live lane's latency floor."*

- **Right about the 1,000-a-second row.** It is unusable and it gives the
  reverse ordering, precisely as predicted
- **Wrong about the decision.** The **20,000-a-second row is clean, monotonic
  and decisive on its own** — 70.07 to 90.47 core-seconds per million with the
  ordering intact on collector, worker OpenSearch, storage tier, memory and
  chunk depth. Steady-state core-seconds at the reference rate settle it without
  needing burst absorption or the lane at all

The prediction confused "the rate the plan calls steady" with "steady state".
The reference rate is a steady-state measurement too, and it is the one that
ranks.

### What stage C recommends

**Move `fluent_bit_flush_seconds` from 5 to 0.5.**

*(This section first recommended 1. Four repeats of each arm changed it — the
reasoning is below the flush-5-against-1 table.)*

| | Flush 5, as shipped | Flush 1 | Change |
|---|---|---|---|
| Total core-seconds per million | 89.43 | 74.77 | **−16.4 %** |
| Worker OpenSearch alone | 29.55 | 22.93 | **−22.4 %** |
| Peak memory | 171.5 MB | 108.7 MB | **−36.6 %** |
| Peak chunks | 17 | 10 | −41 % |
| Live-lane latency floor | 5 seconds | **1 second** | 5× more responsive |

### 0.5 against 1, repeated four times each

The paragraph above originally recommended flush 1 over flush 0.5 on the
grounds that 0.5's 6.3 % margin rested on a single cell with no measured noise
floor on the cluster services. **Both were then run three more times,
alternating between the arms** so that any drift over the two hours fell on
both equally. Core-seconds per million, and peak collector memory:

| Arm | Run 1 | Run 2 | Run 3 | Run 4 | Mean | Spread |
|---|---|---|---|---|---|---|
| **flush 0.5**, total | 70.07 | 70.27 | 69.80 | 68.97 | **69.78** | **1.9 %** |
| **flush 1**, total | 74.77 | 84.73 | 76.28 | 74.34 | **77.53** | 13.4 % |
| **flush 0.5**, memory | 98.3 | 100.4 | 98.6 | 98.1 MB | **98.8 MB** | 2.3 % |
| **flush 1**, memory | 108.7 | 108.4 | 109.1 | 109.6 MB | **109.0 MB** | 1.1 % |

**The margin is real and it is larger than the single cells showed.**

- **No overlap on cost.** Flush 0.5's worst run, 70.27, is cheaper than flush
  1's best run, 74.34. On the means the gap is **11.1 %**, not 6.3 %
- **No overlap on memory either.** 98.8 MB against 109.0 MB, with spreads of
  2.3 % and 1.1 %. **Flush 0.5 wins both axes at once**, which answers the
  question of whether one flush value is best for processor time and memory
  together: it is
- **These repeats also produce the cluster noise floor** the earlier paragraph
  said was missing. At flush 0.5 it is **1.9 % on total cost**, and per service
  3.7 % on the worker's OpenSearch node, 4.7 % and 7.2 % on the two storage
  containers. Every cluster comparison in this stage can now be read against a
  measured number instead of an assumed one

🔴 **Flush 1 is six times less repeatable than flush 0.5** — 13.4 % spread
against 1.9 %, driven by one run at 84.73. That is a finding in its own right:
the shorter flush is not only cheaper on average, it is far more predictable
run to run. A longer flush lets more work pile into each batch, and batch size
is what the cost tracks.

**Recommendation changed: take flush 0.5, not flush 1.**

| | Flush 5, as shipped | Flush 1 | **Flush 0.5** |
|---|---|---|---|
| Total core-seconds per million | 89.43 | 77.53 | **69.78** |
| Against shipped | — | −13.3 % | **−22.0 %** |
| Peak memory | 171.5 MB | 109.0 MB | **98.8 MB** |
| Run-to-run spread | not measured | 13.4 % | **1.9 %** |
| Live-lane latency floor | 5 seconds | 1 second | **0.5 seconds** |

🔴 **0.5 is the smallest value the grid tested, and it won.** The grid's edge is
therefore not a boundary — **nothing here shows where the gain stops**.

**Deviation from the plan, taken deliberately: the grid was extended downward
before stage D ran.** `SOAK_PLAN.md` has stage D run "at the flush stage C
chose", and choosing a value that sits on the edge of the grid means every
later stage inherits a setting that was never bounded. Four cells were added —
**C16 and C18 at flush 0.25, C17 and C19 at flush 0.125**, two runs each at
20,000 a second.

### Below flush 0.5 — the cells ran, and the result is withdrawn

> ⚠️ **Superseded twice.** The retraction below is correct, and the clean
> re-run went further: flush 0.25 offers at −0.98 % and costs 6 % more. There
> is no drain ceiling and nothing breaks.


🔴 **All four cells voided gate zero, and I first reported that as a finding
about flush. It is not. It is the rig.**

| Cell | Flush | Offer shortfall | Mean rate achieved |
|---|---|---|---|
| C16 | 0.25 | −37.8 % | 13,253/s |
| C18 | 0.25 | −28.1 % | 14,357/s |
| C17 | 0.125 | −33.8 % | 13,584/s |
| C19 | 0.125 | −30.1 % | 13,964/s |

The reading was that a shorter flush sends more requests, that OpenSearch
cannot retire them fast enough, and that the back-pressure reaches the source —
with the common plateau near 13,800 records a second as the evidence for a
drain ceiling.

**What actually falsified it.** Stage D's first cell at 20,000 records a second
was `D4` — **flush 0.5 with a 1 GB heap, which is the C6 configuration that had
already passed four times**. It voided at 14,124/s, inside the same band. So
the plateau was not specific to short flush.

**The decisive test.** The exact C6 argument list was rerun on the spot:

| | 24–25 August | 26 August, 02:26 |
|---|---|---|
| C6, flush 0.5, default heap | **PASS ×4** | **VOID, −27.3 %, 14,811/s** |

**The same configuration, the same rig, the opposite verdict.** Every cell at
20,000 records a second before midnight on 26 August passed — about thirty of
them across stages A, B and C. Every cell at that rate afterwards failed, on
three unrelated configurations. **The variable that separates them is the
clock, not the setting.**

🔴 **The lesson is the one this document keeps relearning: a plateau shared by
several arms is a shared cause, not a mechanism.** Four cells agreeing at
13,800/s looked like strong evidence precisely because they agreed. They agreed
because they were all measuring the same degraded machine.

**What was ruled out before blaming the rig:** disk (78 GB free, 17 % used),
memory (96 % free, no pressure, no OOM kills), host processor contention with
the rig idle, and the heap-ceiling fault described below — which was real, but
only affected cells at 1,000 records a second, all of which passed.

**Status: the flush grid has no measured lower bound.** Flush 0.5 remains the
winner over 1, 2, 5 and 10, all of which were measured on the healthy rig and
are unaffected. Whether anything below 0.5 is better is **unknown and untested**,
and the four cells that were meant to answer it must be rerun.

### What stage C recommends

**Move `fluent_bit_flush_seconds` from 5 to 0.5.**

*(This section first recommended 1. Four repeats of each arm changed it — the
reasoning is below the flush-5-against-1 table.)*

| | Flush 5, as shipped | Flush 1 | Change |
|---|---|---|---|
| Total core-seconds per million | 89.43 | 74.77 | **−16.4 %** |
| Worker OpenSearch alone | 29.55 | 22.93 | **−22.4 %** |
| Peak memory | 171.5 MB | 108.7 MB | **−36.6 %** |
| Peak chunks | 17 | 10 | −41 % |
| Live-lane latency floor | 5 seconds | **1 second** | 5× more responsive |

### 0.5 against 1, repeated four times each

The paragraph above originally recommended flush 1 over flush 0.5 on the
grounds that 0.5's 6.3 % margin rested on a single cell with no measured noise
floor on the cluster services. **Both were then run three more times,
alternating between the arms** so that any drift over the two hours fell on
both equally. Core-seconds per million, and peak collector memory:

| Arm | Run 1 | Run 2 | Run 3 | Run 4 | Mean | Spread |
|---|---|---|---|---|---|---|
| **flush 0.5**, total | 70.07 | 70.27 | 69.80 | 68.97 | **69.78** | **1.9 %** |
| **flush 1**, total | 74.77 | 84.73 | 76.28 | 74.34 | **77.53** | 13.4 % |
| **flush 0.5**, memory | 98.3 | 100.4 | 98.6 | 98.1 MB | **98.8 MB** | 2.3 % |
| **flush 1**, memory | 108.7 | 108.4 | 109.1 | 109.6 MB | **109.0 MB** | 1.1 % |

**The margin is real and it is larger than the single cells showed.**

- **No overlap on cost.** Flush 0.5's worst run, 70.27, is cheaper than flush
  1's best run, 74.34. On the means the gap is **11.1 %**, not 6.3 %
- **No overlap on memory either.** 98.8 MB against 109.0 MB, with spreads of
  2.3 % and 1.1 %. **Flush 0.5 wins both axes at once**, which answers the
  question of whether one flush value is best for processor time and memory
  together: it is
- **These repeats also produce the cluster noise floor** the earlier paragraph
  said was missing. At flush 0.5 it is **1.9 % on total cost**, and per service
  3.7 % on the worker's OpenSearch node, 4.7 % and 7.2 % on the two storage
  containers. Every cluster comparison in this stage can now be read against a
  measured number instead of an assumed one

🔴 **Flush 1 is six times less repeatable than flush 0.5** — 13.4 % spread
against 1.9 %, driven by one run at 84.73. That is a finding in its own right:
the shorter flush is not only cheaper on average, it is far more predictable
run to run. A longer flush lets more work pile into each batch, and batch size
is what the cost tracks.

**Recommendation changed: take flush 0.5, not flush 1.**

| | Flush 5, as shipped | Flush 1 | **Flush 0.5** |
|---|---|---|---|
| Total core-seconds per million | 89.43 | 77.53 | **69.78** |
| Against shipped | — | −13.3 % | **−22.0 %** |
| Peak memory | 171.5 MB | 109.0 MB | **98.8 MB** |
| Run-to-run spread | not measured | 13.4 % | **1.9 %** |
| Live-lane latency floor | 5 seconds | 1 second | **0.5 seconds** |

🔴 **0.5 is the smallest value the grid tested, and it won.** The grid's edge is
therefore not a boundary — **nothing here shows where the gain stops**.

**Deviation from the plan, taken deliberately: the grid was extended downward
before stage D ran.** `SOAK_PLAN.md` has stage D run "at the flush stage C
chose", and choosing a value that sits on the edge of the grid means every
later stage inherits a setting that was never bounded. Four cells were added —
**C16 and C18 at flush 0.25, C17 and C19 at flush 0.125**, two runs each at
20,000 a second.

### Below flush 0.5 the pipeline stops keeping up

🔴 **All four cells voided gate zero, and the reason is the result.** None of
them failed on cost. They failed because **the collector could not take the
records it was offered**, and the generator was pushed back to about two thirds
of the rate asked for.

| Cell | Flush | Offer shortfall | Mean rate achieved | Falls behind at |
|---|---|---|---|---|
| C16 | 0.25 | **−37.8 %** | 13,253/s | second 160 |
| C18 | 0.25 | **−28.1 %** | 14,357/s | second 62 |
| C17 | 0.125 | **−33.8 %** | 13,584/s | **second 7** |
| C19 | 0.125 | **−30.1 %** | 13,964/s | second 73 |

Gate zero's tolerance is 2 %. Every cell missed by more than fourteen times
that, and they missed in the same direction, in the same size, on both values,
on both runs of each. **This is not scatter.**

**Three things rule out the rig as the cause:**

1. **The generator has 142× headroom.** Its measured ceiling is 2,842,277
   records a second, proved by selftest, and it paced cleanly at 20,000 and
   50,000 in the same profile
2. **The same rig ran flush 0.5 and flush 1 eight times in the preceding two
   hours** with no gate-zero failure at all
3. **The shortfall is progressive, not a spike.** Each cell holds the full rate
   at first and then decays to a floor. A machine hiccup gives one bad second;
   this gives four hundred

**All four plateau in the same narrow band — 13,253 to 14,357 records a
second.** Two different flush values, four runs, and the pipeline settles to
roughly **13,800 a second** regardless of which one is set. That band looks
like a **drain ceiling** rather than a per-value cost: once the flush is short
enough, what limits the pipeline is how many bulk requests OpenSearch can
retire per second, not how much work each one carries.

**The mechanism, and it is the same one stage C already measured running the
other way.** A shorter flush means more requests, each smaller. Between flush
10 and flush 0.5 that trade pays, because a large bulk serialises on one write
thread per shard and small ones pipeline. Below 0.5 the per-request overhead
stops being amortised at all: at four flushes a second the outputs cannot
retire requests fast enough, chunks accumulate, Fluent Bit stops reading its
tails, and the back-pressure reaches the source. **The curve does not flatten
below 0.5. It turns around.**

🔴 **Flush 0.5 is therefore bounded on both sides, which is what these cells
were run to establish** — flush 1 costs 11.1 % more, and flush 0.25 does not
keep up. **0.5 is a minimum, not an edge.**

**One caveat that must travel with this.** The turnaround was measured at
20,000 records a second, which is **870× a real worker's rate**. At 23 records
a second a quarter-second flush carries about six records and no such ceiling
is in reach. What these cells bound is the *burst* behaviour, and that is the
condition the flush setting exists for.

🔴 **Round 1's `flush: 1` verdict was right, and this round's doubt about it was
wrong.** The plan argued the verdict "may be exactly backwards under the new
budget", on the reasoning that it traded abundant memory for scarce processor
time. It does not trade at all: **shorter flush is cheaper in processor time
and in memory at the same time.**

---

## A defect found while commissioning the cluster sink

🔴 **The shipped `opensearch` outputs silently duplicate records under load.
This is a product defect, not a rig artefact, and it was found before stage C
ran a single cell.**

**What was measured.** A 120-second cluster cell at 2,000 records a second,
1,200 of them InfoLogger:

| | Offered | Fluent Bit says delivered | OpenSearch actually holds |
|---|---|---|---|
| As shipped | 144,000 | **1,140** | **751,381 and still climbing** |
| With `buffer_size: False` | 144,000 | **144,000** | **144,001** — the records plus the bootstrap seed |

**5.2× duplication, and the collector reported the opposite** — that it had
delivered almost nothing.

**The mechanism.** The `opensearch` output plugin reads the bulk response into
a fixed buffer, and the shipped template sets no `buffer_size`, so the plugin
default applies. A bulk of a few thousand records produces a response with one
item per record, which exceeds that default. Fluent Bit then reports
`http_do=-1` — the request failed — and retries a bulk **OpenSearch has already
indexed in full**. Every retry indexes the whole chunk again.

- **126 `http_do=-1` events** in a two-minute cell as shipped, **0** with the
  fix
- **Only the InfoLogger output failed.** It carries 60 % of the mix; the other
  two outputs carry 20 % each, produce smaller responses, and never tripped
- **Both symptoms are wrong in opposite directions**: the operator sees an
  output that appears stuck, while the index quietly fills with copies

**Why it has not been seen.** The threshold is response size, so it scales with
records per flush interval. At the archive's real per-worker rate of 23 records
a second the response is a few kilobytes and nothing goes wrong. **A burst is
exactly the condition that crosses it**, which is also when nobody is reading
the collector's own metrics.

**Stages A and B are unaffected.** Both ran against the fake sink through the
`http` output, which is a different plugin with a different response path. Every
number in those stages stands.

### What this does to stage C's control

The plan's control is `collector.yaml.j2` exactly as shipped. **Stage C runs
with one stated deviation: `buffer_size: False` on every `opensearch` output.**

**The reason is that the alternative measures the defect rather than the knob.**
A flush grid run as shipped would compare five flush values by how badly each
one triggers a response-buffer overflow — larger flush, larger bulk, more
duplication — and would return a flush recommendation that is really a
recommendation about buffer sizes.

🔴 **The recommended product change is to set `buffer_size` on the `opensearch`
outputs in `collector.yaml.j2`.** It is not made here: this round measures, and
the template change is a decision for the owner of the deploy. The evidence is
above and it is unambiguous.

---

## A second defect, found while commissioning the heap grid

🔴 **The rig could not test two thirds of its own heap grid, and it failed
silently rather than saying so.**

Stage D varies the worker OpenSearch node's heap across 1g, 2g and 3g. The
first attempt produced this:

| Cell | Heap | Result |
|---|---|---|
| D1 | 1g | PASS |
| D2 | 2g | **OpenSearch never became reachable** |
| D3 | 3g | **OpenSearch never became reachable** |

**The cause is one line in the rig's compose file.** The worker container is
declared `mem_limit: ${OS_MEM_MAX:-2g}` while its heap comes from a separate
variable. A Java virtual machine asked for `-Xmx2g` inside a 2 GB container
cannot start: the heap is only part of what the process maps, and metaspace,
thread stacks and the off-heap Lucene buffers have nowhere to go. At 3g the
request is simply larger than the container.

**The measurement error this creates is worse than the crash.** With the
ceiling pinned, a heap grid does not measure heap. It measures *which heap
values fit inside a fixed container*, and every value above the ceiling reads
as an unreachable cluster rather than as a cost. Had the cells merely been slow
instead of dead, the grid would have produced a confident ranking of nothing.

**The fix** is `container_ceiling()` in `soak.py`: the container is given twice
the heap, which is the ratio the rig's own defaults already used — a 1 GB heap
in a 2 GB container. **The 1g cell is therefore unchanged**, so stage C's cells
and D1 stay comparable with everything that follows.

**Verified before restarting:** a cluster at heap 3g now reports `3 nodes,
status yellow` and all six services report to the recorder.

**Cost of the fault:** stage D was rerun from the beginning. D1 would have been
valid under the new rule, but a stage whose cells ran on two different
instruments cannot rank anything — the same discipline that forced stage A's
rerun.

---

## The rig lost two thirds of its processor throughput mid-run

> ⚠️ **Partly superseded.** The loss is real and system-wide. Two claims in
> this section are wrong: the rig did *not* keep degrading afterwards (that
> reading compared two different fixtures and caught a transient), and a host
> restart was never available. The fix was to lower the rate instead.


🔴 **Every measurement taken after about midnight on 26 August 2026 is void, and
the cause is the host machine rather than anything in the pipeline.**

**What was measured.** The generator's self-test is a pure compute benchmark —
no OpenSearch, no collector, no disk beyond the fixture:

| | 24 August | 26 August, 04:50 |
|---|---|---|
| Generator ceiling | **2,842,277 records/s** | **1,157,678 records/s** |
| | | **41 % of baseline** |

**Where it went.** The virtual machine is configured with twelve processors and
the guest agrees — twelve online, no cgroup limit, nothing else running. But it
only delivers about two:

| Parallel jobs | Wall time | Effective cores |
|---|---|---|
| 1 | 0.86 s | 1.0 |
| 4 | 1.74 s | 2.0 |
| 8 | 3.25 s | 2.1 |
| 12 | 4.79 s | **2.2** |

**Single-core speed is unaffected** — 0.91 s in the guest against 0.88 s on the
host for the same loop. The machine is not slow. **The virtual machine is not
being given more than about two of the host's sixteen cores**, and while it runs
twelve busy jobs the host reports 95.6 % idle.

**What this explains.** Two thirds of the throughput missing accounts for every
symptom: the pipeline could no longer absorb 20,000 records a second, so gate
zero failed on eight consecutive cells, and the recorded cost per record rose
from 69.78 core-seconds per million to **418.60** — the same work taking far
longer on far fewer cores.

**Ruled out, each by measurement rather than by assumption:**

| Suspect | Evidence against |
|---|---|
| Disk | 78 GB free on the docker volume, 17 % used |
| Guest memory | 96 % free, no pressure, zero OOM kills |
| Guest processor limit | twelve online, no `cpu.max`, guest idle at 100 % |
| Host load | 95.6 % idle during a fully loaded run |
| Low power mode | off, on mains power, battery charged |
| Thermal | no warning level recorded by the power manager |
| Virtual machine state | torn down, pruned and restarted — no change |
| Idle demotion | `caffeinate` was already held and lifts nothing |

**Not diagnosed.** Why the hypervisor is confined to two cores on a sixteen-core
host that is idle. The host had been up five days and seventeen hours. **The
obvious next step is a host restart**, which was not taken because the machine
was not mine to restart.

### What this costs, stated plainly

**Nothing measured before 26 August is affected.** Stages A and B, and the whole
flush grid from 0.5 to 10, ran on the healthy rig — about thirty cells at 20,000
records a second, all of which passed gate zero.

**Void, and needing a rerun on a healthy rig:**

| Stage | Cells | State |
|---|---|---|
| C extension | C16–C19 | flush 0.25 and 0.125 — **no lower bound established** |
| D | D4–D7 | the heap grid at 20,000 and 50,000 a second |
| E | E1–E3 | the core split — **never started** |
| G | G1–G4 | the interaction checks — **never started** |
| F | F1–F12 | confirmation — **never started** |

**Survives, because 1,000 records a second is inside the degraded rig's
capacity:** D1, D2 and D3 all passed, which is what proved the heap-ceiling fix
below actually works.

### Resuming

The whole back half runs from one command once the rig is healthy:

```
python3 tools/soak/logburst.py --fixture <any run>/fixture --mode selftest \
    --rate 3000000 --duration 10 --summary /tmp/selftest.json
```

**Check that first.** If the achieved rate is not back near 2.8 million records
a second, nothing below it is worth running. Then:

```
python3 tools/soak/finish.py C
```

which reruns the flush extension and carries on through D, E, G and F, choosing
each winner from the runs. `finish.py D` skips the extension and resumes from
the heap grid using the flush winner already on record.

---

## Round 2 addendum — heap under burst, and the sustained ceiling

Two questions stayed open after the clean programme finished. The heap grid at
5,000 a second found no difference, which could mean the knob does nothing or
that the rate was too gentle to ask. And no cell had ever run the full stack
above 5,000 a second in steady state, so the ceiling was recorded only as
"above 50,000" from a collector-only cell.

Both are now answered.

### Heap under burst — the knob does not bind, at any burst this rig can offer

Twelve cells across two burst shapes. Each cell ran a base of 1,000 a second
with two peak windows, and the question was **absorption, not cost**: records
lost, how deep the queue went against its 64-chunk cap, peak memory, and
whether it drained.

**Block one — 15,000 a second for 60 seconds, twice per cell.** Both windows
delivered as designed, 900,050 and 899,300 records. Every cell clean.

| heap | peak chunks of 64 | backlog of 256 MB | peak memory | lost | drain |
|---|---|---|---|---|---|
| 1g | 11, 9 | 11.8, 10.2 MB | 78.0, 76.6 MB | **0** | 2.0 s, 2.0 s |
| 2g | 10, 10 | 10.9, 10.3 MB | 76.8, 76.2 MB | **0** | 6.0 s, 2.0 s |
| 3g | 10, 10 | 11.5, 10.2 MB | 74.6, 76.4 MB | **0** | 4.0 s, 2.0 s |

**Block two — 30,000 a second for 30 seconds, twice per cell.** Same burst
volume, twice the instantaneous rate.

| heap | peak chunks of 64 | peak memory | lost | drain |
|---|---|---|---|---|
| 1g | 12 | 93.7 MB | **0** | 8.1 s |
| 2g | 9, 39 | 100.7, 351.6 MB | **0** | 2.0 s, 6.0 s |
| 3g | 10, 9 | 94.2, 95.1 MB | **0** | 4.0 s, 6.0 s |

🔴 **The decisive comparison is within an arm, not between arms.** A single
heap repeated varies more than the three heaps differ. At 15,000 a second the
1 GB arm read 11 chunks then 9, while the three heaps read 11, 10 and 10. At
30,000 a second the 2 GB arm read 9 then 39. When one configuration moves more
than the knob does, the knob has no effect.

**Nothing approached a limit in any cell.** Peak queue used 17 % of the
64-chunk cap. Peak backlog used 6 % of the 256 MB cap. No `memory.high` event,
no out-of-memory kill, no dropped record, no failed retry. Every cell drained
in at most 8 seconds against a 600-second window.

**One cell is void and one is an outlier, both recorded rather than hidden.**
`HB-30k30-1g-r2` failed the gate: the recorder lost 32.8 seconds and the
generator delivered 29.6 % short. `HB-30k30-2g-r2` passed every gate but read
39 chunks and 351.6 MB against 9 chunks and 100.7 MB on the identical
configuration, with every container about 20 % dearer — including the load
generator, which is a machine-wide signature rather than a knob effect.

**Why the result had to come out this way.** A burst is absorbed by **Fluent
Bit's disk-backed chunk queue on the collector side**, capped at 64 chunks and
256 MB. OpenSearch's heap sits *downstream* of that queue and holds nothing
across the burst. Its indexing buffer is a staging area that flushes
continuously into segments, not a reservoir that fills and overflows. A larger
buffer delays a flush that was already cheap. The knob and the bottleneck are
in different places, which is why fifteen cells across three load shapes all
agreed.

✅ **Verdict: 1 GB.** It matched 2 GB and 3 GB on cost at 5,000 a second, it
absorbed both burst shapes with zero loss, and it is 2 GB less memory on a
worker.

⚠️ The largest burst tested was 30,000 a second for 30 seconds. Nothing came
near a cap, so there is headroom — but it is unmeasured headroom, not proven
headroom.

### The sustained ceiling — 50,000 a second holds for about two minutes

Eight cells at 50,000 a second for five minutes, across every combination of
storage tier, log-file placement and host condition that could be arranged.

| cell | configuration | held 50,000/s | mean rate | lost |
|---|---|---|---|---|
| `OVER-50k-1g-r2` | full cluster, logs on disk | **212 s** | 42,248/s | **0** |
| `FIX-50k-1g-r1` | single node, no storage tier | 168 s | 31,610/s | **0** |
| `SOAK-50k-ram` | full cluster, logs in RAM | 146 s | 35,468/s | 301,175 |
| `SOAK-50k-clean` | full cluster, logs on disk | 145 s | 41,435/s | 258,474 |
| `SOAK-50k-final` | full cluster, logs in RAM, cluster wiped | 136 s | 30,208/s | **0** |
| `OVER-50k-1g-r1` | full cluster, logs on disk | 130 s | 27,253/s | **0** |

**Every configuration delivers 50,000 a second for two to three and a half
minutes, then decays.** The final cell is the cleanest reading, because the
host was quiet, the cluster was wiped and the generator's files never touched
disk:

| seconds | achieved |
|---|---|
| 0–59 | **50,042/s** |
| 60–119 | **50,000/s** |
| 120–179 | 34,500/s |
| 180–239 | 27,312/s |
| 240–299 | 17,875/s |
| 300–383 | ~11,800/s |

**Nothing is processor-bound when it decays.** In that cell the collector used
0.65 of a core and the worker's OpenSearch node 2.16 — **2.81 of the worker's
four cores, 70 %**. The storage tier used 3.44 of its four, 86 %. The load
generator used **0.07 of two cores, 3.6 %**, and the same generator reaches
**400,000 a second with no shortfall** on this host when run alone.

The remaining explanation is OpenSearch merge pressure. Segments accumulate,
merging competes with indexing, and indexing throughput falls once roughly six
million documents are in. That is a property of two storage containers sharing
four laptop cores, **not of the collector**, and production has three storage
nodes on their own hardware.

### The overload failure mode, which is the useful part

**Pushed past what it can sustain, the collector degrades safely.** Two cells
show both sides of the cap:

| | `SOAK-50k-final` | `SOAK-50k-clean` |
|---|---|---|
| queue peak | 166 chunks | 151 chunks |
| backlog | 210.7 MB — **82 % of cap** | 260.9 MB — **cap hit** |
| dropped | **0** | 258,474 — **1.8 %** |
| failed retries | **0** | **0** |
| errors | **0** | **0** |
| drain | 171.5 s, emptied | 262.8 s, emptied |

Below the cap the collector absorbs the entire shortfall and loses nothing. At
the cap `storage.total_limit_size` discards oldest-first. In both cases there is
no crash, no error, no failed retry, and the queue empties completely once the
load stops. Chunks spill from memory to disk on the way — 103 of 182 were on
disk in one cell — which is the mechanism working as designed.

✅ **Sustainable rate on this rig: about 42,000 a second**, reproduced twice at
41,435 and 42,248.
✅ **Peak: 50,000 a second for two minutes with zero loss.**

### Four instrument faults found and fixed while running this addendum

| Fault | Effect | Fix |
|---|---|---|
| `soakrec.py` counted **samples** as **seconds** | With a 4-second interval, a 552-second window was recorded as 137. Every `mean_cores` figure in the round read about 4× too high — one cell reported a 4-core container using 10.25 cores | Window now taken from the wall clock between the first and last live sample |
| `heapburst.py` read `peak_chunks` and `lost` | Neither key exists. Both metrics reported as empty | Reads `peak_total_chunks`, and derives loss as `output_dropped` plus `output_retries_failed` |
| Burst cells had no validity gate | A cell whose recorder stalled, or whose generator fell short, was averaged in silently | `hbreport.py` marks a cell **VOID** with its reason: recorder time lost, or an offer more than 2 % below the block's median |
| Log files could only live on the virtual disk | A generator writing 15.5 MB a second could not be separated from disk effects | Opt-in RAM-backed volume through `SOAKLOGS_VOLUME`. **Default unchanged**, so every earlier cell stays comparable |

🔴 **Read every `mean_cores` figure printed before this fix as wrong by roughly
four.** Cumulative `core_seconds` was always correct, and the corrected tables
above are recomputed from it.

### One more thing the round says about the host, not the stack

Most of the difficulty in this addendum was a fault outside the rig. Two
`aqe-mcp` server processes were spinning at **7.5 to 9.3 cores** on a 16-core
machine, and they restarted with the terminal sessions after a reboot. While
they ran, twelve processor-bound processes shared **3 cores**. Any cell measured
in that window describes the host.

**Before trusting a cell, check that the machine is idle.** The selftest is the
calibrated instrument, and it now reads 400,000 a second with no shortfall.

## Kafka, decided against on this round's numbers

**The round was not built to answer this, but it answers it.** A message bus
between the collector and OpenSearch buys nothing this stack needs, and it would
remove the mechanism that currently makes severity tiering free. The decision is
recorded here because the evidence that settles it is here.

The reference design in `docs/ARCHITECTURE.md` places Kafka between the local
collector and everything downstream. That design is not wrong in general. It is
wrong for the rates this farm carries.

### A bus does two jobs. Both were measured.

Kafka decouples a fast producer from a slow consumer, and it holds records
durably while the consumer is away. Each job is judged below against a number
from this round, not against a principle.

### Job one — decoupling. The margin is 538×.

| | records a second |
|---|---|
| A real worker, median second of the busiest hour in six months | **23** |
| A real worker, busiest single second in six months | **78** |
| The whole farm, busiest single second, 297 workers | **9,781** |
| **One worker's stack, sustained** | **~42,000** |
| **One worker's stack, peak, zero loss** | **50,000 for two minutes** |

- **The stack carries 538× the worst worker-second ever recorded.**
- **One worker's stack carries 4.3× the whole farm's peak second.**
- The sustained figure was measured on a host running at about **41 % of its
  normal speed**, so it is a floor on the real hardware, not a ceiling.

🔴 **The archive rates are floors too, and they are floors in the opposite
direction.** The InfoLogger client library cuts a process off above 1,000
messages a minute, and several hops downstream can shed. The true worker rate is
higher than 23 a second by an unmeasured amount. **The conclusion survives it:
even if the archive under-counts by ten times, the margin is still 54×.**

**A buffer sized 538× larger than the load is not a buffer. It is a service to
maintain.**

### Job two — durability. Fluent Bit already holds for 17.4 hours.

The buffer arithmetic is in *And the loss itself is an artefact of the test rate*
above. At 310 bytes a record, the shipped 256 MB `storage.total_limit_size`
holds **865,674 records**:

| At | The 256 MB buffer covers a storage-tier outage of |
|---|---|
| A real worker, 23 /s | **17.4 hours** |
| The busiest worker-second in six months, 78 /s | **5.1 hours** |

**And the overload behaviour is safe, which is the part that matters.** Pushed
past the sustainable rate, `SOAK-50k-final` reached 82 % of the buffer cap,
**dropped nothing**, spilled 103 of 182 chunks to disk, and drained to empty.
`SOAK-50k-clean` hit the cap and discarded 1.8 % oldest-first — with **no crash,
no error, and no failed retry**. See *The overload failure mode, which is the
useful part*.

**If anyone wants a hard guarantee instead of a seventeen-hour cushion, the lever
is `storage.total_limit_size`, sized from that arithmetic.** That is one number
in one file, not a broker quorum.

### What adding Kafka would cost, and the routing is the expensive part

🔴 **The tier decision is currently free, and a bus would move it.** Index
templates carry `require.role: worker` or `require.role: storage`. Fluent Bit
writes to the node on its own host, and OpenSearch forwards what belongs
elsewhere. **The collector does not know or care which tier a record lands on.**
With a bus, that rule must be restated as a topic mapping at produce time, or as
a routing consumer. The policy then lives in two places instead of one.

🔴 **A bus either deletes the local trash tier or doubles the collector's
outputs.** Sending high-volume worker-tier records off the host to a broker and
back is pointless. So trash must bypass Kafka — and the collector then runs two
outputs with two buffers. Kafka would wrap only the low-volume storage path,
which is the exact path the seventeen-hour cushion already covers.

The rest of the cost:

- **Brokers need hardware the worker cannot give.** The stack gets four reserved
  cores per worker, taken from reconstruction work. Brokers cannot live there,
  so a quorum needs its own allocation.
- **The collector pays for its own output.** The collector costs **27.7
  core-seconds per million records** at flush 1, and that figure is the one
  resource an EPN worker rations. A Kafka output adds to it.
- **Latency.** The live lane's floor is **1 second**, set by flush 1. A produce
  step and a consume step add to that floor.
- **Two more services to deploy, pin, monitor and version** — a broker quorum and
  a sink connector.

### What Kafka would genuinely buy, stated fairly

- ✅ **A second independent consumer.** Anomaly detection or a columnar cold tier
  could read the stream without querying OpenSearch. **This is the only strong
  argument, and it is a future one.**
- ✅ **Outages longer than the buffer cushion.** That means days, not hours.
- ⚠️ **Agreement with `docs/ARCHITECTURE.md`.** Matching the reference design
  lowers the cost of merging with it. That is a real reason. It is not a
  measurement, and it should not be presented as one.

### The conditions that would reverse this

Any one of these reopens the question:

1. **A sustained per-worker rate above about 1,000 a second** — 13× the busiest
   worker-second in the archive.
2. **A second consumer becomes a requirement**, not a possibility.
3. **Storage-tier outages that routinely exceed the buffer cushion.**
4. **A measurement of the real per-worker rate that lands within 50× of 42,000 a
   second.** The archive figures are floors, and this is the one that could move.

🔴 **One thing a bus would not fix.** Three hundred and more OpenSearch nodes in
a single cluster is a cluster-manager scaling risk. That is a topology problem.
Fewer nodes or cross-cluster search answers it. A message bus does not.

### What this decision does not rest on

**Stated so that nobody reads more certainty into it than there is:**

- ❌ **The full stack has never run above 5,000 a second in steady state**, nor
  above a 30,000-a-second burst of 30 seconds. See *Open questions*, item 1.
- ❌ **Every rate above 5,000 a second in this round came off a degraded host.**
  The rankings transfer. The absolute numbers do not.
- ❌ **The archive bounds the worker rate from underneath only.**

**The decision rests on the size of the margin, not on the precision of either
end of it.** Three orders of magnitude absorb every one of those faults.

---

## Round 6, stage H — the parser, and the configuration we run it with

**Drain3 stays. Its configuration does not.** PIPLUP was measured against Drain3
over the same 45,596,613 lines and lost on cost. Ten more parsers were screened
off the shelf, two of which returned nothing in ten minutes,
and every one of them lost on cost, on streaming, or on both. The round's real
result is not the parser choice. It is two things about the parser we already
ship. **A per-family configuration overtakes the readability PIPLUP was winning
on, at no extra cost. And rewriting the masking regexes — same output, byte for
byte — makes the whole corpus 1.8 times cheaper to mine, on fewer templates.**

| | Templates | Core-s /M | Peak RSS |
|---|---:|---:|---:|
| **H1b** Drain3, whole corpus | 3,822 | **19.91** | 26.3 MB |
| **H2** PIPLUP, whole corpus | 2,444 | 50.87 | 206.5 MB |
| **H3b** Drain3, InfoLogger only | 2,853 | **17.32** | 23.1 MB |
| **H4** PIPLUP, InfoLogger only | 1,337 | 38.80 | 106.1 MB |

H1 and H3 are the same cells before a rig fault was found; they read 21.80 and
22.33. Both readings are kept below, because the spread between them is itself a
result.

### Gate 3 — cost. PIPLUP costs 2.3 to 2.6 times Drain3, and the gate was ±25 %

**2.56× on the whole corpus, 2.24× on InfoLogger.** The gate allowed a quarter.
PIPLUP also holds **7.8× the memory**, 206 MB against 26 MB, because it keeps a
token-frequency map that grows with the vocabulary.

🔴 **The gate is barely wider than Drain3's own noise, and that is a weakness of
the gate.** Drain3 on InfoLogger read 17.32 and 22.33 core-seconds per million on
two runs of identical work — a spread of **28.9 %**. Every cost figure in this
round carries that. A parser that came in 20 % cheaper would not have been
believed, and should not have been.

### Gate 2 — PIPLUP streams, once its file handling is replaced

The released code builds a pandas frame of every line and accumulates a line-id
list per cluster. Neither survives 45.6 million lines. `piplupbench.py` drives
the online clustering directly and counts hits instead of storing identifiers.
**The algorithm streams. The distribution does not.** That is a fair result for
the algorithm and an unfair one for anybody who wants to use it as shipped.

### Gate 1 — PIPLUP produces more readable templates, and it does not matter

PIPLUP returned **2,444 distinct template strings against Drain3's 3,822**, and
its templates carry more literal words. On the twenty most common templates from
each parser, side by side, PIPLUP reads better. **Gate 1 favours PIPLUP — against
the configuration we shipped when the gate was written.**

🔴 **It no longer favours PIPLUP against the recipe.** On InfoLogger:

| | Median real words | Contentless templates |
|---|---:|---:|
| Drain3, as shipped | 3 | 44.9 % |
| **PIPLUP** | 5 | 28.4 % |
| **Drain3 with the recipe** | **6** | **10.4 %** |

**The configuration change closed the gap and passed it, at no cost.** Every
advantage PIPLUP held — more literal words, fewer contentless templates, a whole
clock masked as one token, `partition(s)` surviving — the recipe now delivers on
the parser we already run. PIPLUP still keeps its one genuine edge and it is not
readability: it needs no per-family configuration at all.

Against that, PIPLUP emits **exactly one placeholder type**. All of `<IP>`,
`<UUID>`, `<HEX>` and `<NUM>` collapse into one symbol, so a shifter loses the
"lines containing an address" search they have today.

Two limits on the gate-1 reading itself:

- **The dump is incomplete.** PIPLUP allows one cluster to hold several templates.
  `piplupbench.py` writes only the first, so 264 of the 2,444 strings are absent
  from the file — 407 clusters held a second template. The comparison was read on
  what was written.
- **Everything PIPLUP won here, Drain3 later won by configuration and for free.**
  See *Where it landed*. That is what makes gate 1 not decide the stage.

### Gate 4 — no accuracy claim is verifiable, and this is not a hedge

Log-parsing papers score against **ground truth**: a hand-written answer key
mapping each raw line to its correct template. LogHub-2.0 publishes one for 14
systems. **ALICE has none.** The true template for an O2 line is the `printf`
format string in the O2 source, and nobody has extracted them.

So every published accuracy figure for every parser in this stage — PIPLUP's,
LogLSHD's, KELP's — is a figure on somebody else's logs. **None of them was
reproduced here, and none of them can be.** Gate 4 is answered "unknowable", and
the stage was decided on cost, memory and readability alone.

### The other parsers, all rejected, with the reason

🔴 **One baseline for the whole table.** Every parser below, including the
control, is a `logparser` reference implementation run through one harness on one
100,000-line InfoLogger slice. **The control is that repository's own Drain at
96.21 core-seconds per million.** Ratios are against it. The `drain3` we ship is a
different, faster implementation at 15 to 22 on the same family, so **none of
these numbers may be divided by ours** — an earlier version of this section did
exactly that and made SHISO look 86× worse and IPLoM 4× worse than the table
supports.

| Parser | Cost /M | × the control | Rejected because |
|---|---:|---:|---|
| **LFA** | 33.32 | 0.35 | 78.3 % of templates hold ≤1 literal word |
| **AEL** | 43.55 | 0.45 | batch; no advantage over Drain on any axis |
| **IPLoM** | 55.60 | 0.58 | four-pass batch; cannot stream |
| **Drain** (the repo's own) | 96.21 | 1.00 | the control for this table only |
| **LogLSHD** | 104.64 | 1.09 | **slower than the Drain it claims to beat by 73 %** |
| **Logram** | 466.10 | 4.8 | cost |
| **LenMa** | 691.29 | 7.2 | cost |
| **SHISO** | 1,284.40 | 13.3 | best readability on the shelf, at 13× the control |
| **Spell** | — | — | no result in 600 s |
| **LogMine** | — | — | no result in 600 s |
| **EFParser** | — | — | calls a language model per line at runtime |

**LogLSHD is the one worth naming.** Its paper claims 73 % faster than Drain. On
our data it was **9 % slower than the Drain implementation shipped in its own
repository**. It also needs a Jaccard similarity threshold tuned per dataset —
the paper uses 0.60 to 1.00 across systems — and without ground truth we cannot
pick it.

**KELP took three source patches to survive real data**, and then still lost:

1. `kelp-core/src/lib.rs:884` — `get_freq(...).unwrap()` returned `None` after
   25,011 lines.
2. `lib.rs:766` — the same call in `valid_root`.
3. `lib.rs:178` — `read_template_for_line` panicked on a token it had not seen.

It also ships no masking, so 200,000 lines produced 15,971 templates until
Drain3's masking was bolted on in front. Patched and masked: **1,347 templates at
43.36 core-seconds per million, 29.9 % of them contentless.** A free configuration
change to Drain3 beat it on every column at a third of the cost.

### Everything tried on Drain3 itself, including what failed

**Failed, and each one is a real negative result:**

- **PIPLUP's preprocessing bolted onto Drain3.** The hypothesis was that PIPLUP's
  readability came from its regexes. It did not. Templates went 1,145 → 1,549,
  contentless templates 49.7 % → 65.4 %, cost 14.23 → 41.43. **Wrong by a wide
  margin, in the direction opposite to the prediction.**
- **Hand-written ALICE semantic masking rules.** 801 → 820 templates for +19 %
  cost. Nothing gained.
- **`parametrize_numeric_tokens` — the arm that never ran.** 🔴 The knob defaults
  to `True` in `drain3`. The arm that was supposed to test it set `True` on top of
  `True`, which is why the templates barely moved and why an earlier version of
  this section wrongly reported "doubles the cost, 33.35 against 14.93". **That
  33.35 came from the contaminated block, and the knob had never been measured.**
  Measured properly, with the recipe, it is free in processor time and it buys
  readability by making many more templates:

  | Family | Parametrised (the default) | Kept as routing keys |
  |---|---|---|
  | `infologger` | 675 templates, 18.13, 94.5 % words | 899, 17.96, **95.0 %** |
  | `stdout` | 936 templates, 16.70, 99.7 % words | 1,264, 16.90, 99.7 % |
  | `dds` | 224 templates, 51.16, 88.1 % words | 701, 51.14, **92.0 %** |

  **Keep the default on for InfoLogger and stdout** — half a point of words is not
  worth 34 % more templates. **Turn it off for dds**, where it is the largest
  readability gain still available, 88.1 % to 92.0 %, and 477 extra templates in
  absolute terms is nothing.
- **Dropping a masking rule to save time.** Removing PATH or FLOAT did not make
  mining cheaper. The tree grows instead.
- **`drain_max_children`.** On InfoLogger, 25, 100 and 500 produce 914, 915 and
  915 templates — inert. 🔴 **It is not inert on stdout**, where the same three
  settings give 688, 766 and 890 templates, and 500 cost **46.41 against 21.48**.
  An earlier version of this section called the knob useless on this data. It is
  useless on one family and harmful on another. Leave it at 100.
- **Per-family trees, on the first attempt.** Three tuned trees gave 1,186
  templates at 19.94 core-seconds per million; one global tree gave 1,058 at
  19.81. **That result is withdrawn** — the three trees had been tuned on a
  conclusion that later proved wrong. See below.
- **Deleting separators with `drain_extra_delimiters`.** This looked like the
  winner for two days. Per-line over the whole corpus it improved 22.3 % of
  stdout lines and **made 72.5 % of them worse**, because deleting a character is
  not the same as splitting on it. Found only when a human read forty samples.
- **A severity mask.** Same 645 templates, and words kept fell from 95.3 % to
  84.4 %. It would also merge an ERROR line with its INFO twin.
- **A blanket "the value after `=` is a variable" rule.** It replaces the
  informative `<NUM>` with a bare `<VAL>` and it eats closing brackets. On stdout
  it cost 4.5 % more for 8 % fewer templates. **The tree already does this job:**
  Drain replaces a token with `<*>` the moment it sees a second value there.
- **A flag-name mask, `--id` → `--<*>`.** On dds, where the flags live, it gave
  the same 178 templates and cut median literal words per template from 287 to
  221. Pure loss.
- **Hash and mixed-case identifier masks.** +56 % masking time on InfoLogger, and
  they *added* templates on stdout.

**Worked, and the reason each one works:**

- **Padded separators.** Mask first, then `re.sub(r"([=;:])", r" \1 ", masked)`,
  with the miner's own masking disabled. This gets the tokenisation benefit of a
  delimiter **and keeps the character**. The idea came out of the human audit.
- **The clock out of the message, and depth — in that order of discovery, the
  reverse order of credit.** Drain's tree is a prefix tree: level one is the token
  count, level two is the first token, then `depth − 2` more levels. Every O2
  stdout line begins with `[12:20:44][INFO]`, one token, identical after masking,
  so level two did no work on 19 million lines.

  🔴 **The readability gain belongs to the depth, not to the prefix.** Measured on
  three million stdout lines with the clock left in place: 94.2 % words at depth
  4, 97.6 % at depth 6, **99.7 % at depth 8**. Removing the prefix at depth 4
  gains 0.1 points on its own. An earlier version of this section credited the
  prefix with the whole jump.

  **The prefix fix earns its place on two other grounds.** It cuts stdout mining
  from 21.48 to 16.70 core-seconds per million, and — found while auditing the
  collector for this round — it is the only way the severity router can work at
  all. See *What must change before this ships*.
- **Depth 8.** From depth 4 to depth 12 the stdout cost moved 0.6 %, from 17.07 to
  17.18 core-seconds per million, and peak RSS moved 2.6 MB. **There is no
  processor argument and no memory argument against depth.** The accuracy gain
  has a knee at 6, which is where the tree first sees past the clock; contentless
  templates keep falling to depth 12.
- **Similarity 0.5 on dds, and only on dds.** Depth cannot help dds: its lines are
  300-token shell command lines whose distinguishing word sits past token 50, and
  depth is a prefix budget. Similarity reads the whole line. 88.1 % words, the
  best dds arm, at the lowest dds cost.
- **`<FLOAT>` and `<NUM>` kept apart, merged only where a slot holds both.** Four
  lines patched into `Drain.create_template`. Without it, keeping the two masks
  apart puts **6.3 %** of stdout lines onto a template containing `<*>`; with it,
  **1.0 %**, while 258 templates still show a real `<FLOAT>`. All three figures
  are from the same three-million-line run. 🔴 An earlier version of this section
  put a 400,000-line figure and a three-million-line figure in one table.

### The mistake that mattered, and a human caught it

**The readability metric was wrong, and it was wrong in the direction that
flattered the change being tested.** Literal-token count counted any token with no
`<*>` in it. A bare `:` and a bare `=` passed. Padding adds hundreds of those, so
**padding scored well by construction.**

The metric said 88.8 % of stdout lines improved. The user read forty samples and
said the opposite. **The user was right.** On the biggest stdout templates the
padded configuration destroyed words the shipped configuration kept.

Two metrics replaced it and are used everywhere above:

- **literal tokens, punctuation-only tokens excluded**
- **words kept** — the share of real words in the raw line that survive into its
  template, line-weighted

🔴 **`degenerate_templates_pct` was also used as a headline and it inflated the
case against Drain3.** Line-weighted, only **1.4 %** of InfoLogger lines land on a
contentless template, not the 44 % the template-weighted figure suggests. Both
are reported above; the line-weighted one is the one a shifter feels.

### Two rig faults, both self-inflicted

- **Two containers were run at once on disjoint `--cpuset-cpus`.** Cost inflated
  from 14.93 to 41.77 core-seconds per million, **2.8×**, through memory and cache
  contention. The batch was discarded and re-run sequentially. **The same mistake
  was then repeated once**, launching a pairing job beside a sweep.
- **A masking probe was run over the first 5,000,000 lines, which are all
  InfoLogger.** The `[HH:MM:SS]` rules it was testing never fired, and both arms
  came back byte-identical.

Four arms in the depth sweep and four in the mask sweep read 33 to 101
core-seconds per million against neighbours at 18 to 24. **Those cost figures are
discarded.** Template counts and word counts from the same arms are deterministic
and are kept. The final recipe was re-timed alone, and those are the numbers in
the next table.

### Where it landed — one recipe per family

| Family | Clock and severity | Padded separators | Depth | Similarity | Numeric tokens |
|---|---|---|---:|---:|---|
| `infologger` | already fields | `= ; :` | 8 | 0.4 | parametrised |
| `stdout` | **needs a collector rule** | `= ;` | 8 | 0.4 | parametrised |
| `dds` | already fields | `=` | 8 | 0.5 | **kept** |

Masking is otherwise unchanged from what ships. `<FLOAT>` and `<NUM>` stay
separate, with the four-line merge patch.

**The three families want three different answers to the same knob, and that is
what justifies splitting the configuration:**

| Padded separators | stdout, words kept | infologger | dds |
|---|---:|---:|---:|
| `=` | — | — | **75.5 %** |
| `= ;` | **88.3 %** | 72.4 % | 75.3 % |
| `= ; :` | 87.7 % | **78.6 %** | 72.5 % |

InfoLogger is full of `key: value` and wants the colon. stdout is hurt by it. dds
wants neither colon nor semicolon, because in a shell command line `;` separates
real commands.

### What the recipe costs — one clean run, six arms, nothing else on the machine

| Family | Templates now | Core-s /M now | Words now | Templates, recipe | Core-s /M, recipe | Words, recipe |
|---|---:|---:|---:|---:|---:|---:|
| `infologger` | 908 | 14.84 | 90.7 % | 675 | 18.13 | **94.5 %** |
| `stdout` | 766 | 21.48 | 94.2 % | 936 | **16.70** | **99.7 %** |
| `dds` | 183 | 52.70 | 72.9 % | 224 | **51.16** | **88.1 %** |
| **Weighted, whole corpus** | | **17.65** | | | **17.57** | |

**The recipe is cost-neutral, and it is much more readable.** InfoLogger pays
22 % more, stdout pays 22 % less, dds pays 3 % less, and the corpus-weighted
total moves by half a percent.

🔴 **An earlier version of this section claimed 21.7 % cheaper.** That rested on
an InfoLogger control reading of 22.39 core-seconds per million. Five other clean
runs of the same cell read 14.68, 14.84, 14.92, 15.06 and 15.10. **The 22.39 was
the outlier and the saving was not real.** The gain this recipe delivers is
readability at the same price, not speed.

Contentless templates fall from **44.9 % to 10.4 %** on InfoLogger and from
**26.4 % to 7.4 %** on stdout. Words kept rises on every family.

3,000,000 lines per family for InfoLogger and stdout; all 43,972 for dds.
Weights are this corpus: InfoLogger 58.1 %, stdout 41.8 %, dds 0.1 %.

🔴 **The dds weight is wrong for production.** Operations report dds is the
highest-volume family during data-taking. This corpus holds 43,972 dds lines;
round 4's held 267,607. **Every dds figure here is measured on a family that this
corpus under-samples, and dds is also the most expensive per line by far.**

### The template count rose, and that is affordable

Round 3 measured `potion-base-32M` at **18,037 templates a core-second**.
Embedding 10,000 templates costs **0.55 core-seconds, once**, against roughly 780
core-seconds to mine the corpus. Even a full transformer, at 581 templates a
core-second, costs 17 core-seconds. **The embedding bill is 0.07 % of one mining
pass and it is not a reason to keep the tree shallow.**

The real cost of more templates is statistical, not computational: each split
makes two thinner count series out of one. It was measured. Going from depth 6 to
depth 8 on stdout took the templates covering 99 % of lines from **155 to 157**.
The other 50 templates all landed in the tail, holding fewer than 100 lines each.
**The busy series the detectors will use are untouched.**

### The masker rewritten — 86 % off the most expensive step

**Masking was 74 to 87 % of the mining cost. Seven rewritten regexes take 85 to
87 % off it, and the templates do not change by one line.** This work was done by
a separate agent against a fixed acceptance harness, then verified here.

| Masking alone, core-s /M | Reference | Rewritten | Change |
|---|---:|---:|---:|
| `infologger`, 3,000,000 lines | 11.93 | **1.82** | **−84.7 %** |
| `stdout`, 3,000,000 lines | 12.24 | **1.73** | **−85.8 %** |
| `dds`, 43,972 lines | 40.22 | **5.09** | **−87.3 %** |

**The mechanism is a position, not an algorithm.** Python's `re` uses its
character-skip loop only when a pattern's first opcode is a literal or a class.
Every shipped rule opens with a word boundary or a lookbehind, so the engine ran
the full matcher at every character of every line. Moving the first literal in
front of the assertion fixes that and keeps the match set:

```
before  ((?<=[^A-Za-z0-9])|^)(/[-\w./]+)((?=[^A-Za-z0-9])|$)
after   /(?<![A-Za-z0-9]/)[-A-Za-z0-9_./]+(?![A-Za-z0-9])
```

Four mechanisms stack on top: a `str.__contains__` gate in front of each rule, an
ASCII fast path so the digit and word classes become bitmap tests instead of
Unicode category lookups, FLOAT and NUM folded into one scan without a per-match
callback, and no capturing groups.

🔴 **The hypothesis this round put first was wrong, and the agent falsified it.**
Replacing the word boundary with a plain negative lookbehind, without moving a
literal to the front, made `UUID`, `IP`, `HEX` and comma-`NUM` **40 to 75 %
slower** on dds. The alternation was never the problem. The position was.

**The fold reproduces a quirk rather than fixing it, and that is the point.** The
reference runs FLOAT and then NUM as two passes, so in `1.5-3` the second pass
swallows the sign and the result is `<FLOAT><NUM>`, not `<FLOAT>-<NUM>`. A naive
one-pass fold differs, and the first line it broke was
`Loading O2PDPSuite/epn-20260615-DDv1.6.11-QCv1.193.0-flp-suite-v1.82.0-2`. The
kept implementation carries a flag that reproduces the two-pass behaviour
exactly.

### Verifying it, three ways the acceptance harness could not

The harness compared one million lines per family. Three gaps were closed here.

| Check | Result |
|---|---|
| **All 3,000,000 lines per family**, not the first million | **0 differences** |
| **The input the pipeline really gives the masker** — the line after the clock is stripped, which starts at a different character | **0 differences** |
| **The templates themselves**: full set *and* each template's line count | **identical** — 675, 936 and 701 clusters, same strings, same sizes |

A differential fuzz over 3,000,000 random strings on a hostile alphabet —
Arabic-Indic digits, combining accents, U+FFFD — also found no difference. Two
rejected candidates are worth recording: **the `regex` module is not
byte-identical**, because its word class treats the combining accent U+0301 as a
word character and a `<PATH>` therefore ends elsewhere; and a digit pre-check
cannot pay, because 89 to 100 % of lines per family contain a digit.

### What the whole pipeline costs with both changes

Four arms per family, one run, consecutive, nothing else on the machine.

| Family | Shipped | Shipped + fast masker | Recipe | **Recipe + fast masker** |
|---|---:|---:|---:|---:|
| `infologger` | 15.11 | 4.82 | 19.00 | **8.85** |
| `stdout` | 24.69 | 10.36 | 19.33 | **7.23** |
| `dds` | 60.95 | 15.71 | 59.39 | **17.56** |
| **Weighted** | **19.16** | | | **8.18** |

**2.3× cheaper than what we ship in this run, and far more readable.** stdout
and dds each fall by 71 %. 🔴 **This ratio is from three-million-line samples and
it flatters the change.** The figure of record is the whole-corpus cell below,
**19.91 to 11.18, or 1.8×**, because a larger tree costs more per line and a
sample cannot show that. Template counts are unchanged between the two masker columns —
908 against 908, 675 against 675, 936 against 936, 183 against 183, 701 against
701 — which is the equivalence check again, end to end.

🔴 **This run reads high against its neighbours** — the shipped stdout control at
24.69 where five other clean runs gave 21.4 to 23.2. **Read the ratios inside the
run, not the absolute values across runs.** Every arm above was measured
consecutively in one container.

### The recipe over the whole corpus — fewer templates and 44 % cheaper

Every figure above is 3,000,000 lines per family. This is all 45,596,613, with
the recipe and the fast masker together.

| Family | Lines | Templates | Core-s /M | Words kept | Contentless templates |
|---|---:|---:|---:|---:|---:|
| `infologger` | 26,505,911 | 1,306 | 10.59 | 90.2 % | 7.9 % |
| `stdout` | 19,046,730 | 1,004 | 12.00 | **99.6 %** | 7.1 % |
| `dds` | 43,972 | 701 | 15.12 | 92.0 % | 0.0 % |
| **Whole corpus** | **45,596,613** | **3,011** | **11.18** | | |

**3,011 templates against H1b's 3,822 on the same lines, at 11.18 core-seconds
per million against 19.91.** Fewer templates *and* better ones *and* 44 %
cheaper. The template count falls because the win came from keeping words, not
from splitting clusters — the opposite of what a deeper tree normally does.

**Two scale effects worth recording.** InfoLogger word retention drops from
94.5 % on three million lines to **90.2 %** on all 26.5 million: more variety
arrives, and some of it wildcards. And mining costs more at scale — 10.59 against
8.85 — because the trees are larger. **Neither is visible on a three-million-line
sample, which is why this cell exists.**

**This is the template set stage I must embed.** Round 3's ladder was measured on
the 3,822 templates of the old configuration, where 44.9 % of the InfoLogger ones
carried one real word or none. That is now 7.9 %. **The inputs changed more than
any rung on that ladder differs from another.**

### Sixteen runs instead of one — the costs hold, and the template count does not

Round 6 mined **one** run tag. The archive holds **86**, and 2,013 dds tarballs.
This cell takes sixteen of them, spread evenly across the run-number range, and
mines them with the frozen recipe.

| Family | Lines | Runs | Templates | Core-s /M | Words kept |
|---|---:|---:|---:|---:|---:|
| `dds` | 1,192,999 | 16 | **1,570** | 15.98 | 87.6 % |
| `stdout` | 2,246,331 | 16 | **1,025** | 6.29 | 97.5 % |

**The dds cost holds: 15.98 against 15.12 on the single-run corpus**, a 5.7 %
difference on a rig whose run-to-run spread is 28.9 %. Same extraction, 27 times
the lines, sixteen periods. **The saving is a property of the data, not of one
period.**

🔴 **The stdout cost is not a like-for-like comparison and 6.29 must not be
quoted against 12.00.** This sample caps each source file at 20,000 lines, so it
holds many more programs and far fewer lines of each. The line mix changed, so
the cost changed. Only the dds row answers the question this cell was run to ask.

### Novelty lives at run boundaries, not in volume

**This is the finding.** Templates discovered after each successive run:

```
dds     +933  +2  +59  +182  +43  +73  +53  +1  +17  +2  +1  +3  +140  +1  +35  +25
stdout  +205 +66 +246   +23   +4   +1  +25 +136  +0 +42  +3 +123   +2   +0 +133  +16
```

**Neither family has saturated after sixteen runs.** dds was still adding 25
templates at run 16 and stdout 16, with spikes of +140 and +133 in the last third.

**Set that against what more lines of the same run buy.** In the single-run
corpus, stdout added **four templates across its last fourteen million lines**,
and InfoLogger went six million lines without adding one.

| | Lines | Templates found |
|---|---:|---:|
| stdout, **one** run | 19,046,730 | 1,004 |
| stdout, **sixteen** runs | 2,246,331 | **1,025** |

**Two and a quarter million lines spread over sixteen runs discover as many
templates as nineteen million lines from one run — about 8.6 times more per
line.** Volume within a run is nearly exhausted; variety between runs is not.

🔴 **So the 3,011-template figure is a floor, not a total.** It was measured on
one run of each family. Sixteen runs of two families already produce 2,595
between them and are still climbing, and 70 run tags remain unsampled.
**Stage I must treat the template count as open-ended**, and the new-template
rate as something driven by run boundaries rather than by line volume.

**A run is not a unit of constant size.** Adjacent runs alternate between about
9,000 and about 130,000 dds lines, a fifteen-fold swing. Round 6 sampled four
tarballs of one of them. Any per-run sizing figure needs to say which kind.

**InfoLogger was not re-fetched.** It lives in separate MySQL dump objects rather
than the run tarballs. It is saturated within its period at 1,306 templates, and
the 42 templates that arrived in its last 1.5 million lines are the same
boundary effect seen here. **Fetching InfoLogger across partitions is the obvious
next measurement and it has not been run.**

### Forty InfoLogger partitions round 6 never saw

The archive holds **179 InfoLogger objects**, partitions 13 to 193. Round 6 mined
**40**. This cell rebuilds its tree over exactly those same 26.5 million lines,
then feeds the same tree **40 of the 139 partitions it had never seen**, capped at
200,000 lines each so the sample spans periods instead of exhausting a few.

| Phase | Lines | Templates | Core-s /M |
|---|---:|---:|---:|
| **1** — round 6's 40 partitions | 26,505,911 | 1,306 | 9.18 |
| **2** — 40 unseen partitions | 6,927,107 | **1,497** | **10.24** |

**The cost holds on data the tree has never seen: 10.24 against the 10.59 measured
on the whole corpus.** No drift.

**A saturated tree gains 191 templates, 14.6 %, from forty new periods** — after
going six million lines without gaining one inside its own. Per partition the
gains are small and steady: `+8 +0 +0 +15 +10 +4 +0 +1 +0 +17` and on to `+20 +10
+11 +0 +0 +9 +0 +1 +0 +6`.

**But InfoLogger is much closer to done than the tarball families.** Against
stdout going from 205 to 1,025 templates across sixteen runs, InfoLogger adds
14.6 % across forty partitions. **Novelty at period boundaries is real in every
family and it is an order of magnitude weaker here.**

🔴 **A signal worth naming, not yet a conclusion.** Word retention falls from
81.5 % in phase one to **74.0 %** in phase two, on the same measure. Either a tree
built on one period templates a later period less well, which is the question
`docs/SOAK_PLAN.md` raises about time-ordered splits, or the phase-two templates
are simply younger and less settled. **The cell cannot separate the two.**

🔴 **Those two retention figures are on a different denominator from every other
one in this document and must not be compared with them.** This script measured
retention against the raw line; every other cell measures it against the masked
and padded line the miner actually receives. Phase one against phase two is
sound. Phase one against the 90.2 % elsewhere is not.

### The template count is a curve, not a number

| Family | Round 6 | With more periods | Unsampled |
|---|---:|---:|---|
| `infologger` | 1,306 — 40 partitions | **1,497** — 80 partitions | 99 partitions |
| `dds` | 701 — 1 run | **1,570** — 16 runs | 70 runs |
| `stdout` | 1,004 — 1 run, 19.0 M lines | **1,025** — 16 runs, 2.2 M lines | 70 runs |
| **Total** | **3,011** | **4,092** | |

**4,092 already, 36 % above the whole-corpus figure, on a fraction of the archive,
and every family still climbing.** The number stage I must plan for is not 3,011
and it is not 4,092 either. **It is whatever the archive holds, and the driver is
how many runs and partitions are sampled, not how many lines.**

### Where the code lives, and why it is not in `deploy/`

**There is no production templating to integrate it into.** `deploy/` and
`images/` were searched for `drain3`, `TemplateMiner` and `templating`; the only
two hits are about Jinja. No role, no service and no image mines log templates.
Templating is still a bench, as round 4 left it.

**So it lives in `tools/templating/masking.py`,** beside the one consumer in the
repository. That module holds `REFERENCE`, the seven rules as `drain3` consumes
them and the definition of correct; `reference_mask`, which runs them the way
`drain3` does so a future candidate can be checked without reconstructing
anything; and `mask`, the rewrite. `drainbench.py` binds the fast path onto the
miner and keeps the instruction list in place for parameter extraction.

**The fold cannot ever be configuration.** It is Python, not a `drain3`
`MaskingInstruction`, so it has to live in our own code wherever templating
eventually runs.

Verified again after the move, against the landed file rather than the scratch
copy: **0 differences on all 3,000,000 lines of each family, 0 on the stripped
input, and identical template sets and per-template line counts.**

### What must change before this ships

**One collector rule, and it closes two defects at once.** `stdout_root` knows
only the ROOT form, `2026-06-20 12:15:19.123 Info in <Facility>: message`. Every
O2 line — 99 % of stdout — matched only the catch-all, so the record carried a
`message` and **no `severity` key at all**.

Measured: **345 stdout lines in 3,000,000 captured a severity.** One in ten
thousand.

🔴 **That silently disabled severity tiering for the largest family.** The router
reads `$severity ^Info$ family.local` first and `$message ^.*$ family.central`
second. With no `severity` key the first rule cannot fire, so **every stdout line
went to the replicated storage tier** — 42 % of all lines, onto the tier
`deploy/README.md` sizes as low-volume.

The fix is a `stdout_o2` parser running before `stdout_root`, and a router rule
that accepts the uppercase form. Measured severity distribution on stdout: `INFO`
96.9 %, `WARN` 3.0 %, `STATE` 0.1 %, `ALARM` 0.0006 %. So central's share of
stdout falls roughly **32-fold**.

**The clock is captured as a string, not as `time_key`, on purpose.** It carries
no date, and a `time_key` would stamp replayed June-2026 lines with today's.

🔴 **One consequence to decide separately.** The live shifter lane matches
`^(infologger|family\.central)$` and deliberately excludes the trash tier. Today
it sees all of stdout only because all of stdout is central. After the fix it
will not see stdout `INFO` at all.

**dds and InfoLogger need no collector change.** `dds_text` already captures
time, severity, source and thread on **43,960 of 43,972 lines**, and InfoLogger
arrives as JSON with the fields already apart.

**One divergence between the benchmark and production, declared.** Production
joins indented continuation lines into the preceding record. The corpus builder
read raw file lines and did not. That is **7,912 lines in 3,000,000** on stdout
and **12 in 43,972** on dds — the module-load banner and the FairRoot start-up
art. Too small to move any figure above, but those banner templates will differ
in production.

### Limits of this stage

- **The corpus holds 43,972 dds lines. Round 4's held 267,607.** The rebuild used
  identical flags and reproduced the total line count exactly, 45,596,613, so the
  difference is in which objects the run tags cover. Every dds figure here rests
  on the smaller set.
- **No ground truth, so no accuracy number** for any parser, ours included.
- **No journald anywhere in the corpus.** The claim that Drain handles multiline
  kernel traces badly — a varying *leading* token is its blind spot — is reasoning,
  not a measurement.
- The whole stage ran in one Colima VM pinned to four processors, on a laptop.

## Round 6, stage I — post-training the small model on ALICE text

Stage H changed the templates this stage is about, so nothing in round 3 carries
forward untouched. The order below is the order the work ran in, and each part
was written down as it closed.

### Step 0 — the template set stage I embeds, and it did not exist

`tools/embed/splits.py` reads a four-column dump and the only dump on disk was
from the configuration stage H replaced. So the first job was to mine the corpora
again with the frozen recipe and keep the source label.

🔴 **The frozen recipe was not in the repository.** It lived only in
`downloads/round6/wholecorpus.py`, which is gitignored, so the dump nobody could
reproduce was the input to everything below. The recipe is now
`tools/templating/drainbench.py --recipe`: depth 8, max children 100, similarity
0.4 for `infologger` and `stdout` and 0.5 for `dds`, numeric tokens parametrised
for `infologger` and `stdout` and kept for `dds`, separators `= ; :` padded for
`infologger`, `= ;` for `stdout` and `=` for `dds`, the clock stripped from
`stdout` and `dds`, the fast masker, and the four-line `<FLOAT>`/`<NUM>` merge
patch. It takes several corpora and carries one tree per family across all of
them, because stage I needs one template set rather than one per file.

**All three corpora on disk, in one pass, 55,963,050 lines, 4,221 templates.**

| Phase | Corpus | Lines | `infologger` | `stdout` | `dds` |
|---|---|---:|---:|---:|---:|
| **1** | `corpus.tsv` — one run tag, 40 partitions | 45,596,613 | 1,306 | 1,004 | 701 |
| **2** | `corpus-multirun.tsv` — 16 run tags | 3,439,330 | +0 | **+150** | **+869** |
| **3** | `corpus-il2.tsv` — 40 unseen partitions | 6,927,107 | **+191** | +0 | +0 |
| | **Carried total** | **55,963,050** | **1,497** | **1,154** | **1,570** |

**Phase 1 reproduces stage H to the template**: 1,306, 1,004 and 701, totalling
3,011. Phase 2 takes `dds` to 1,570 and phase 3 takes `infologger` to 1,497, both
of which are stage H's own figures for those samples, arrived at by a different
route. The recipe is reproducible outside the scratch directory.

**4,221 templates, against the 4,092 stage H projected.** The difference is
`stdout` alone and it is a denominator difference, not a disagreement. Stage H
summed three separately built trees and its `stdout` entry, 1,025, is a tree that
saw only the sixteen-run sample. One tree carried across both corpora holds the
union of what each finds: 1,004 from the single run plus 150 the sixteen runs
added, which is 1,154. **The union is the number stage I needs**, because stage I
embeds one set.

**The embedding bill is unchanged by the larger set.** At round 3's measured
18,037 templates a core-second, `potion-base-32M` embeds all 4,221 in **0.23
core-seconds**, against roughly 380 core-seconds to mine phase 1 once. That is
**0.06 % of one extraction pass**, and it is still not a reason to keep the
template count down.

#### A rig fault of my own, and it was invisible on two families out of three

`miner()` binds the fast masker onto the miner so the ordinary path gets it for
free. The recipe cannot use that binding: it masks first and pads the separators
of the masked line afterwards, so leaving the binding in place ran the masker a
second time, over a string whose character context had already moved.

**It was close enough to idempotent to hide.** `infologger` and `dds` reproduced
stage H exactly with the second pass in place — 1,306 and 701. `stdout` came out
**981 against 1,004**, and that 23-template gap is the only thing that gave it
away. Every number in this section is from the corrected run. **Phase 1 against
stage H is the check that caught it**, and it is the reason the check was worth
running at all.

#### Stage H's `stdout` cost of 12.00 does not reproduce, and the whole-corpus figure moves

Four readings of that cell, three of them taken in one hour on an otherwise idle
machine:

| Reading | Harness | Core-s /M |
|---|---|---:|
| Stage H, as published | `wholecorpus.py` | **12.00** |
| Repeat | **the same `wholecorpus.py`, unmodified** | **6.37** |
| This session, `stdout` alone | `drainbench.py --recipe` | **6.81** |
| This session, three families interleaved | `drainbench.py --recipe` | **6.65** |

**The output is byte-for-byte the same in all four**: 1,004 templates, 99.6 %
words kept, 7.1 % contentless templates, 0.2 % of lines on one. Only the clock
disagrees. **The published figure is 1.8 times the mean of the three fresh
readings, which is 6.61** — far outside the 28.9 % run-to-run spread stage H
measured on identical work. The three fresh readings sit within 6.9 % of each
other, measured against the lowest of them.

🔴 **So the published 12.00 is a contaminated reading and the whole-corpus cost
of 11.18 cannot stand.** Recomputed from this session's phase-1 readings, on the
same 45,596,613 lines and the same line mix:

| Family | Lines | Core-s /M | Share of the bill |
|---|---:|---:|---:|
| `infologger` | 26,505,911 | 9.53 | 252.6 core-s |
| `stdout` | 19,046,730 | 6.65 | 126.7 core-s |
| `dds` | 43,972 | 15.51 | 0.7 core-s |
| **Whole corpus** | **45,596,613** | **8.33** | **380.0 core-s** |

**8.33 core-seconds per million, not 11.18.** `infologger` reads 10 % under stage
H and `dds` 3 % over, both inside the rig's spread; the whole of the move is
`stdout`.

**This does not re-open the parser question and it is not meant to.** It makes
the parser already chosen cheaper, which can only strengthen a decision taken on
cost. What it costs is a ratio: stage H's "1.8× cheaper than what we ship" rests
on a control reading of 19.91 for the shipped configuration that **I did not
re-measure**, so I will not quote a new multiple. The absolute is 8.33 and the
control is unverified.

### I0 — the noise floor. The gate fires, and the round changes shape

**The interval is 0.065 to 0.076 wide against a gate of 0.03, so the ladder as
round 3 read it is one measurement and its absolute numbers cannot carry a
decision.** That is the answer the plan asked for and it stands. But the same
bootstrap says something the plan did not anticipate, and it is the more useful
half: **the orderings are stable even though the numbers are not.**

The statistic is deterministic given the templates, so the spread being measured
is not run-to-run jitter — nothing here is affected by what else the machine was
doing. It is sampling spread. This template set is one draw from an archive that
stage H showed keeps producing new templates at every run and partition boundary,
and the question is how much of a gap would survive a different draw.

**400 replicates, 4,220 templates, k = 10, sources needing 10 templates to vote.**
The dump holds 4,221 rows; one carries an empty template and is dropped.

#### Two bootstraps, because the plan's wording admits two

| | What it resamples | What it holds fixed |
|---|---|---|
| **Cached neighbours** | which templates vote | the embedding space and every neighbourhood — a neighbour may be a template the replicate never drew |
| **Recomputed neighbours** | the template set itself | nothing; each replicate finds its neighbours inside itself |

The first is the plan's literal instruction and measures the spread of the
averaging. The second is stricter and asks whether a ranking would survive a
different sample of the archive. **Both are reported below and the gate fires
under both**, so nothing here turns on the choice.

#### The marginal interval — this is the gate

| Model | Observed macro purity | Cached, 95 % width | Recomputed, 95 % width |
|---|---:|---:|---:|
| `potion-base-8M` | 0.380 | 0.065 | 0.075 |
| `potion-base-32M` | 0.390 | 0.066 | 0.076 |
| `all-MiniLM-L6-v2` | **0.410** | 0.068 | 0.074 |

🔴 **A single macro purity for one model on one template set is good to about
±0.037, and the whole round 3 ladder spanned 0.037.** The gate allowed 0.03 and
the narrowest reading is more than twice that. **Every absolute macro purity in
`docs/EMBEDDING_RESULTS.md` quoted to three decimals is quoted past what the
measurement supports**, and the 0.027 that decided round 3 is smaller than the
error bar on either of the two numbers it was the difference of.

#### The paired interval — this is what a ladder actually needs

Two models scored on the same replicate move together, because a replicate that
happens to be easy is easy for both. The gap is therefore measured far better
than either number in it.

| Pair | Observed gap | Cached, 95 % interval | Recomputed, 95 % interval | Replicates the first wins |
|---|---:|---|---|---:|
| `all-MiniLM-L6-v2` − `potion-base-32M` | +0.020 | [+0.008, +0.033] | [+0.002, +0.040] | **392 of 400** |
| `all-MiniLM-L6-v2` − `potion-base-8M` | +0.030 | [+0.019, +0.042] | [+0.014, +0.049] | **398 of 400** |
| `potion-base-32M` − `potion-base-8M` | +0.010 | [+0.005, +0.015] | [+0.002, +0.018] | **399 of 400** |

**Every interval excludes zero, under both bootstraps.** The paired widths are
0.010 to 0.038 against marginal widths of 0.065 to 0.076 — **the gap is measured
three to six times more precisely than the numbers it is made of.**

**So the fix is not to abandon the metric. It is to stop reading it in absolutes.**
Rank on paired differences over shared replicates and report the interval; never
rank on two numbers taken from separate passes.

🔴 **One round 3 conclusion is overturned by this.** Round 3 called
`potion-base-32M` against `potion-base-8M` — 0.387 against 0.380 — "inside the
noise", and chose the 8M model as the post-training base on that reading. The
paired bootstrap puts that gap at +0.010 with an interval excluding zero and the
32M model ahead in **399 of 400 replicates**. It is a small gap and it is a real
one. Round 3 was right that it is small and wrong that it is noise.

#### What did not happen, and the plan expected it might

Stage H predicted the recipe change "plausibly reorders the ladder itself". **It
did not reorder these three rungs.** `all-MiniLM-L6-v2` still leads, then
`potion-base-32M`, then `potion-base-8M`, exactly as in round 3.

The absolute numbers also barely moved — 0.380, 0.390 and 0.410 now against
0.380, 0.387 and 0.414 in round 3, every rung within 0.004. 🔴 **That closeness
is not a like-for-like comparison and must not be read as one.** The two sets are
different sizes with different source distributions and different nulls, and both
numbers sit inside an interval far wider than the difference between them. What
transfers is the ordering, not the value.

#### The set changed underneath the metric, and the null moved with it

| | Round 3 | Now |
|---|---:|---:|
| Templates scored | 3,822 | **4,220** |
| Sources with ≥ 10 templates | 23 | **40** |
| Largest source's share | `infologger/ODC/ODC`, **57 %** | `dds/epn287`, **17.7 %** |
| `infologger/ODC/ODC` share | 57 % | **15.0 %** |
| **Macro null** | **0.035** | **0.0195** |

Both shares are of the scored set — 2,180 of 3,822 then, 747 and 631 of 4,220
now. **The null halved because the concentration halved**, and a purity read
against the wrong null says the opposite of the truth. Every figure above is
against 0.0195.

### The source splits, and a defect in the frozen rule that the new dump exposed

**The split rule was frozen before any result existed, and applied unchanged to
the new dump it would have done something different from what it says it does.**
Fixing that is a correctness change, not a tuning one, and it was made before any
model was scored.

🔴 **A `dds` source is a host, not a program.** `tools/templating/corpus.py`
labels a `dds` line with the EPN whose tarball it came from, so the sixteen-run
corpus turned `dds` into 22 "sources" — `dds/epn287`, `dds/epn001` and so on —
and five of them clear the ten-template floor. **Every EPN runs the same
`dds-agent`.** Those five sources are five samples of one program.

Two things follow, and both break the rule's stated intent:

- **The rule drops "the largest source" to stop one program scoring against
  itself. On this dump the largest source is `dds/epn287` at 747 templates**, so
  the rule would have dropped a host and kept `infologger/ODC/ODC` — the exact
  program it was written to exclude.
- **Four of the top ten would have been EPN hosts.** The stage exists to ask
  whether a model places a program it has never seen; asking it to tell
  `dds/epn287` from `dds/epn001` is a question with no right answer.

**So `dds` is excluded from the evaluation pool and stays in the training text.**
That is one flag, `--exclude-family dds`, with the reason in the file. With it,
"drop the largest" removes `infologger/ODC/ODC` as intended.

#### The two sets, with each set's own null

| | Sources | Templates | **Macro null** |
|---|---:|---:|---:|
| **Dev** | 5 | **517** | **0.198** |
| **Held-out** | 5 | **259** | **0.197** |

**Dev** — `infologger/?/datadist/tfscheduler` 339, `stdout/itstpc-track-matcher`
58, `stdout/gpu-reconstruction` 47, `stdout/its-stf-decoder` 39,
`stdout/pvertex-track-matching` 34.

**Held-out** — `stdout/mft-tracker` 101, `infologger/DPL/readout-proxy` 49,
`stdout/its-tracker` 46, `infologger/DPL/gpu-reconstruction` 37,
`stdout/internal-dpl-ccdb-backend` 26.

**Both nulls land where the plan said they would.** It predicted a five-source
null "near 0.20" against the full set's 0.035, and they read 0.198 and 0.197
against this set's 0.0195. **A dev purity of 0.34 is a better result than a
full-set purity of 0.39**, and a table that put them in one column would say the
opposite. Every number below is against the null of its own set.

🔴 **Dev is 66 % one source.** `infologger/?/datadist/tfscheduler` holds 339 of
517 templates. That is the concentration the "drop the largest" rule exists to
prevent, one rung further down, and the rule does not reach it. **The null is
what makes this safe to report rather than fatal** — 0.198 already prices the
concentration in — but a dev number is close to a two-source question and should
be read that way. Held-out is better spread: its largest source is 101 of 259,
which is 39 %.

**The remaining 278 sources are training text**, including `infologger/ODC/ODC`,
all 22 `dds` hosts, and every source too small to score.

### The query set, judged before any model was scored

**635 query-template pairs, 177 judged relevant, and the judging was done before
a single score existed.** The pool comes from seven shelf models — the three
`potion-base` rungs, both `potion-code` rungs and both MiniLM rungs — each
contributing its top ten for each of the twenty queries in
`tools/embed/queries.txt`. The judgements are `tools/embed/judgements.tsv`.

🔴 **A person did not judge these. I did.** The plan asks for about an hour of
one person's time and says plainly that this metric exists because it is the one
check post-training cannot quietly optimise against. A judgement made here keeps
that property — it is independent of any training, and it was fixed before
scoring — but it **loses the claim to human ground truth**, and the file should
be re-judged by a shifter before any of it is quoted outside this document.

**One rule, applied uniformly:** a template is relevant when it reports the named
condition occurring or failing — an event, an error, a warning, or the state
transition that *is* the condition. Configuration banners, environment dumps,
resource-limit listings and unrelated uses of the query word are not relevant.
That rule is what keeps `MemFree: <NUM> kB` out of "memory pressure" and
`Free SHM memory too low` in.

| Query | Relevant | Pooled |
|---|---:|---:|
| `data lost or frames dropped` | **27** | 36 |
| `incomplete timeframe` | **22** | 33 |
| `network timeout` | **20** | 28 |
| `connection refused` | **17** | 23 |
| `device or GPU error` | 12 | 30 |
| `service restarted` | 10 | 34 |
| `configuration rejected` | 9 | 33 |
| `file could not be opened` | 9 | 34 |
| `process died` | 8 | 45 |
| `run started` | 8 | 23 |
| `memory pressure` | 7 | 34 |
| `queue backed up or slow consumer` | 7 | 40 |
| `run stopped` | 6 | 22 |
| `topology activation failed` | 5 | 28 |
| `partition not found` | 4 | 21 |
| `calibration object missing` | 2 | 29 |
| `authentication failed` | **1** | 22 |
| `detector readout error` | **1** | 35 |
| `disk full` | **1** | 38 |
| `permission denied` | **1** | 47 |
| **Total** | **177** | **635** |

#### 🔴 The best score any model can reach on this set is 0.645, not 1.0

Four queries have exactly one relevant template in the whole pool, so precision
at ten is capped at 0.1 for each of them by arithmetic, whatever the model does.
Summing each query's ceiling of `min(relevant, 10) / 10` and averaging over the
twenty gives **0.645**. **A model scoring 0.50 has reached 78 % of what is
reachable, not 50 %**, and every figure this set produces has to be read against
0.645.

**Those four queries say something about the corpus, not about the models.** This
archive is one period of a working farm. It holds thousands of dropped-timeframe
and connection-failure events and it holds **one** template about disk space, one
about an authentication token and one about a privilege. `disk full`,
`permission denied` and `authentication failed` are conditions the corpus barely
contains, so they measure nothing here — but they are exactly the conditions a
shifter would want covered, which makes their absence a finding about the sample
rather than a defect in the query set.

🔴 **The ceiling is a property of the pool, not of the corpus.** A template that
is genuinely relevant but that none of the seven shelf models retrieved is not in
the pool and cannot be judged. So 0.645 is the ceiling *given what was pooled*,
and a post-trained model that surfaces a relevant template nobody pooled is
scored as if it were wrong. `query_pairs_unjudged` reports how large that penalty
is for each model, and it must be quoted beside any post-trained score.

## Open questions

### 1. The burst gap, and it is the largest number in this document

| | Records a second, one worker |
|---|---|
| **The burst figure the plan carries** | **10,000 to 20,000** |
| **The busiest single worker-second in six months of archive** | **78** |
| **The gap** | **128× to 256×** |

**Two numbers that should describe the same thing differ by more than two
orders of magnitude.** Everything the burst arms test — `BURST` cells, chunk
absorption, the flush value chosen on burst behaviour — is sized by the first
number and unsupported by the second.

**Three explanations would each account for it. This round does not have the
evidence to choose between them, and does not guess:**

1. **A regime the archive window misses.** Start of run and end of run are the
   obvious candidates — the shifter documentation says plainly that *"there
   will always be a flood of errors at EOR"*. The busiest hour scanned here is
   a hour of steady data taking, and a start-of-run or error-storm burst could
   be orders of magnitude above it while lasting seconds
2. **The figure is farm-wide, not per worker.** The farm's peak in that hour
   was 9,781 a second, which lands exactly at the bottom of the 10,000 to
   20,000 band. If the band is a farm figure it is already corroborated, and
   the per-worker burst is roughly 1/300th of it
3. **Retention never held the peak.** The archive is a window. A burst that
   aged out, or that the client's flood limit cut at source, is absent from
   these numbers by construction — see the floor caveat above

**What would settle it:** one answer from Lubos on which of the three he means,
or a targeted scan of the archive around a start-of-run boundary rather than
around the busiest steady hour. The second is cheap and is worth doing when the
rig is idle.

### 2. Where the InfoLogger tap goes in production

See "Where we tap InfoLogger" above. Three candidates, two priced by stage B,
and one — the pure tail of the O2 process files — that costs a tail input and
nothing more. **Whether an EPN worker's files carry InfoLogger content is a
question for Lubos and no soak run settles it.**

### 3. Does 1,000 a second cover all three families or InfoLogger alone

The derivation here is InfoLogger only. DDS and stdout need the run tarballs
read, which saturates the network and the processor, so it waits for an idle
rig.

### 4. `epn146` and `epn323` are unsurveyed

Memory, cores, and what else runs there. Carried forward from the plan
unchanged.
---

## Round 7 — the other log sources, and what they cost

Written 4 September 2026. This round adds three sources the collector never
read, splits a fourth into the two formats it always was, and prices every
regex in the engine that actually runs it.

Everything here was measured on the 45,596,613-line archive corpus in
`downloads/round6/` and through Fluent Bit in Docker. Nothing here was measured
on the EPN farm. **The live source census did not run — the control machine had
no Kerberos ticket, so no EPN was reachable this session** — and one Stage S0
gate condition therefore stays open. Which condition, and what it would change,
is at the end.

### The sources, and where each one goes

| Source | Format | Informational | Warning and worse | New this round |
|---|---|---|---|---|
| `infologger` | 16 named fields | durable | durable | no |
| `dds` | date, severity, agent, thread, message | local | durable | no |
| `dpl` | `[HH:MM:SS][SEVERITY] message` | local | durable | **the format, yes** |
| `datadist` | `[YYYY-MM-DD HH:MM:SS.mmm][X] message` | local | durable | **yes** |
| `ildaemon` | date, tab, message | durable | durable | **yes** |
| `journald` | journal fields, priority 0-7 | local | durable | **yes** |

The full routing record for each — program identity rule, clock domain,
multiline rule, duplicate-ownership rule, durable-storage reason — is in
`docs/LOG_TYPES.md`.

### Five defects the fixtures found, all of them silent

None of these announced itself. Each was found by running the shipped
configuration through real Fluent Bit and looking at what came out.

**1. Severity tiering was off for the largest family, and the fix that turned it
on did not work in production.** Commit `eaa02e3` added a parser for the O2 form
`[HH:MM:SS][SEVERITY]` and measured it on the raw corpus. The replay engine
prepends a full event date to every record-start line, so in the deployed
pipeline the line does not start with `[` and the anchored parser never matched.
Measured on the shipped configuration before this round: **every stdout record
reached durable storage with no `severity` key at all** — 41.8 % of the corpus
onto the tier `deploy/README.md` sizes as low-volume.

**2. A DDS line that no parser claimed was dropped, not routed.** A
`rewrite_tag` rule can only fire on a key that exists. Both DDS rules were keyed
on `$severity`, which is absent precisely when `dds_text` did not claim the
line, so the record matched no rule and vanished. DDS prints a version banner, a
protocol version and a bug-report address at startup, and every one of those was
going missing. Each router now ends with a rule keyed on `log`, which is the key
a record still has exactly when nothing parsed it.

**3. The production renderer could not render the production template.**
`tools/soak/mkconfig.py` passed `live_lane_*` variables to a template that had
been renamed to `shifter_*`, and `StrictUndefined` made every render an
exception. Every configuration validated by the rig since that rename was not
validated at all, because none was produced.

**4. Fluent Bit 5.0.8 refuses to start on a configuration 4.0.1 and 4.0.14
accept.** `multiline.parser` is a valid property of the `systemd` input on the
two 4.x builds and is rejected outright by 5.0.8. The kernel-trace fold now
lives in a `multiline` filter, which all three accept. This is the concrete
reason the check runs against every deployed version rather than one.

**5. The live view carried its own severity map and it had gone stale.** The
shifter does not go through OpenSearch, so it normalises severity itself from a
second copy of the table. That copy knew `I`, `W`, `E`, `F`, `D`, the ROOT
words, `inf`, `err` and `cout`, and nothing else — so every `INFO`, `WARN`,
`STATE` and `ALARM` record produced since `eaa02e3` would have shown as
`unknown` in the live view while reading correctly in Discover. The two copies
are now the same table, and the reason they must be is written above each.

### How much of a real corpus the parsers classify

The match rate of the cascade is always 100 %, because its last parser has every
group optional and therefore matches anything. The number that decides whether a
line can be routed, charted or alerted on is whether a **severity** came out of
it.

`tools/collector/coverage.py`, 20,948,861 process-tree lines from the archive,
continuations folded first the way the tail input folds them:

| | Lines | Share |
|---|---:|---:|
| `dpl` | 20,781,839 | 99.20 % |
| `dpl_noclock` | 36,834 | 0.18 % |
| `datadist` | 92,813 | 0.44 % |
| `stdout_root` (ROOT form and the catch-all) | 37,375 | 0.18 % |
| **severity recovered** | **20,914,128** | **99.83 %** |

The Stage S0 gate asks for 99 % on classifiable envelope fields. **99.83 %
passes it.** The residual 0.17 % is the O2PDPSuite module-load banner and CCDB
parameter dumps, which carry no severity to recover.

🔴 **Fold the continuations first or the figure is 1.6 points too low.** Scored
line by line the same corpus reads 98.22 %, because every indented line of the
module banner counts as a line with no severity. Fluent Bit folds those into the
record above before any parser runs. The corpus stores one line per record and
does not.

DDS, 43,972 lines: **99.97 %**. The twelve that miss are the startup banner —
defect 2 above.

### What routing on severity actually moves

| Severity, process tree | Lines | Share | Tier |
|---|---:|---:|---|
| `INFO` | 20,214,468 | 96.49 % | local |
| `WARN` | 523,400 | 2.50 % | durable |
| `STATE` | 80,673 | 0.39 % | durable |
| `D` | 59,649 | 0.28 % | local |
| none recovered | 34,733 | 0.17 % | durable |
| `I` | 33,164 | 0.16 % | local |
| `Info` | 2,228 | 0.01 % | local |
| `Warning` | 414 | 0.00 % | durable |
| `ALARM` | 130 | 0.00 % | durable |
| `ERROR` | 2 | 0.00 % | durable |

**96.94 % of the process tree stays on the node and 3.06 % crosses the network.**
Before this round the split was 0 % and 100 %.

`STATE` is deliberately durable. A FairMQ transition is 0.39 % of the tree and it
is the join between DDS launching a task and the task reaching RUNNING, which is
what an operator asks for first when a run will not start.

DDS is 86.5 % `inf` and 13.5 % `err`. **That error share is high and it rests on
a corpus that under-samples DDS**, which operations report is the highest-volume
family during data-taking. It is the largest unpriced risk in this routing.

### What the regexes cost, and how little this host can resolve

`tools/collector/regexbench.py`: one container, one core, a null sink, real log
lines. The run ends when Fluent Bit's own metrics say every record has been
read, so the figure is processing time rather than the length of a sleep.

**Round 6's masker findings do not transfer to the collector and it would be
wrong to apply them.** Round 6 measured Python's `re`, where a pattern is fast
when its first opcode is a literal, because that is what enables the
character-skip loop. Fluent Bit does not use Python. It uses Onigmo, with its
own optimiser. Every collector regex below was therefore measured, not reasoned
about.

Two things had to be fixed before any of it could be believed, and both are
described under "what the instrument got wrong" below. Arms are now
**interleaved** — one round of every arm, round-robin — and one arm is a
**control**: byte-for-byte the shipped configuration under a second name.

#### The control arm is the whole result

400,000 process-tree lines, Fluent Bit 4.0.14, eight interleaved rounds:

| Arm | Min s | Median s | Core-s / M | Against shipped, on the minimum |
|---|---:|---:|---:|---:|
| shipped | 4.93 | 5.93 | 12.33 | — |
| **control — the same configuration** | 4.97 | 4.98 | 12.43 | **+0.8 %** |
| ANSI tolerance removed | 4.96 | 5.95 | 12.41 | +0.6 % |
| cascade reordered, `dpl` first | 4.93 | 5.46 | 12.34 | +0.0 % |
| `mft_decoder_error` removed | 4.92 | 5.94 | 12.31 | −0.2 % |

**The largest difference in the table is between the shipped configuration and
itself.** Nothing else in it means anything. The ANSI tolerance is free, the
cascade order does not matter, and the numeric extractor is free — but the
honest statement is weaker than any of those: **no arm differs from the shipped
configuration by more than the instrument's own reproducibility.**

#### The host has two speeds, and the median cannot see past them

Every arm above produced both roughly 4.95-second and roughly 5.95-second runs,
mixed together. That is a 20 % step, it belongs to the machine rather than to
any configuration, and it lands wherever it likes.

The consequence is a rule for reading these tables:

| Estimator | Spread across five arms that are within 1 % of each other |
|---|---|
| Median of 8 | 4.98 to 5.95 — **19 %**, and the two extremes are the two identical configurations |
| **Minimum of 8** | 4.92 to 4.97 — **1 %** |

**The minimum over interleaved rounds is the estimator; the median is not.** The
minimum is the least-contended observation, and on a host that is not idle it is
the only one that reproduces.

#### What the instrument got wrong, before it got this right

Four faults in one round, all in tooling written the same day, and **every one of
them produced a plausible number rather than an error.** They are recorded
because the first two were already written up as results.

🔴 **A 17.3 % cost for `mft_decoder_error` that does not exist.** The arms were
run in blocks — all repeats of one, then the next — on the host described above.
The block that happened to land in the slow state read 17 % worse. Interleaved,
the same arm reads −0.2 %. A pattern change made on the strength of that number
was reverted; the parser keeps the readable, specific form it started with.

🔴 **A −33 % arm that measured a broken parse.** It deleted the optional leading
date from the `dpl` regex while the input still carried one, so the regex failed
on every line and the work it "saved" was the parse itself. Replaced by
`--input-form live`, which changes the input rather than the parser.

🔴 **A "version difference" that was a race in the fixture harness.** It fed the
InfoLogger records over TCP as soon as `docker run -d` returned. Docker's port
proxy answers from that moment, so the client connected to a socket Fluent Bit
had not bound yet and the records went nowhere — intermittently, under load. It
read as "4.0.1 loses InfoLogger and 4.0.14 does not". The harness now asks the
metrics endpoint whether the engine is up before speaking to it, and every
version claim in this round was re-checked afterwards.

🔴 **A cascade-order arm that ran without a router.** The reordering step loaded
the rendered YAML and dumped it back. A `rewrite_tag` filter carries several
`rule:` keys in one mapping and a YAML load keeps only the last, so the arm
measured a pipeline whose routing rules had been deleted. It now moves whole
text blocks and the six `rule:` lines survive.

A fifth fault failed loudly and cost only a run: emptying the storage directory
between arms by removing and recreating it left the container with a bind mount
that no longer resolved.

### The DDS extractors, and the one cost that survived the control

DDS is the ground truth for what was supposed to be running. Three shapes carry
it: the slot that was assigned, the channel that was wired, and the binary that
was launched. Thanasis has a parser for each, and each of his re-parses the
whole line, so his configuration reads the envelope four times. Ours parses the
envelope once and runs the three extractors over `message` only.

They are worth having: over 1,236,608 DDS messages, **11.13 % gain a field**
from one of the three — 45,883 lines each, which is the same count three times
because DDS assigns a slot, wires a channel and launches a task once per slot.

600,000 lines, Fluent Bit 4.0.14, six interleaved rounds, minimum reported:

| Arm | Core-s / M | Against shipped |
|---|---:|---:|
| shipped — three extractors | 24.68 | — |
| control — the same configuration | 24.87 | +0.8 % |
| no extractors at all | 24.86 | +0.7 % |
| **`task` capturing the whole line** | **29.83** | **+20.8 %** |

**This is the only configuration difference this round found that is larger than
the instrument.** The control gap is 0.8 %; this is 20.8 %, twenty-six times it,
with every one of six runs inside a 0.5 % band. Everything else measured on this
host, on either family, sat at or below the control.

**All three extractors are free, and the cost was never the regex.** Adding them
reads 0.7 % against a control that reads 0.8 %. What cost 20.8 % was one capture
group's payload. An executed-task line is an O2 command line — **3,487 bytes on
average, 14,436 at the longest, 160 MB across the corpus** — and `preserve_key`
keeps `message`, so `(?<task>.*)` was making a second copy of nearly the whole
record.

The field that answers "which binary was launched" is the first token.
`(?<task>\S+)` captures `mft-stf-decoder` instead of `mft-stf-decoder --session
default …`, the arguments stay in `message` where they already were, and the
extractor joins the other two at no measurable cost.

**The lesson generalises past this parser, and it is not round 6's lesson.**
Round 6's rule — put a literal at the front, because that is what lets the
engine skip — is about the pattern, and on this family the pattern turned out
not to matter at all: `dds_slot`, `dds_channel` and `dds_task` all sit inside the
control. What matters is **what a match copies out**. On a high-volume family,
check a new extractor for its payload before checking it for its pattern.

### The templating recipe, per format family

Round 6 froze one recipe per family for `infologger`, `stdout` and `dds`. The
`stdout` family was two formats, and one of them was being mined wrong.

**`RECIPE_STRIP["stdout"]` removes the DPL clock and severity before mining. It
does not match a DataDistribution line**, whose own bracket carries a full date
with milliseconds. So 92,813 lines entered the miner with a complete timestamp
still in the text, the masker turned it into `<DATE>` and `<TIME>` tokens, and
those tokens became template positions. Mining the envelope again is how a clock
turns into a template.

`tools/templating/refamily.py` splits the corpus using the collector's own
parser cascade, so the mining family and the collection family stop being
conflated. The split, over all three corpora:

| Mining family | Lines |
|---|---:|
| `infologger` | 33,433,018 |
| `dpl` | 21,200,248 |
| `dds` | 1,236,971 |
| `datadist` | 92,813 |

#### `datadist` — a new recipe, and it wants the opposite of the others

`tools/templating/recipesweep.py`, all 92,813 lines, 32 cells. The eight rows
that matter:

| Padded separators | Depth | Numeric | Templates | Core-s / M | Words kept |
|---|---:|---|---:|---:|---:|
| **(none)** | **8** | **parametrised** | **191** | **5.37** | **60.3 %** |
| (none) | 8 | kept | 270 | 5.34 | 61.0 % |
| (none) | 6 | parametrised | 177 | 5.20 | 59.6 % |
| `=` | 8 | parametrised | 189 | 9.01 | 58.9 % |
| `= ;` | 8 | parametrised | 189 | 9.53 | 58.9 % |
| `= ; :` | 8 | parametrised | 179 | 10.52 | 58.5 % |
| `= ; :` | 6 | parametrised | 160 | 10.42 | 51.7 % |
| `=` | 6 | parametrised | 167 | 8.78 | 52.1 % |

Similarity moved nothing: 0.4 and 0.5 gave identical template counts in every
cell. Contentless templates were 0.0 % everywhere.

**Padding nothing is both the cheapest and the most readable, which is the
reverse of every other family.** Not padding costs 5.37 core-seconds a million
against 9.01 for `=` alone — **40 % cheaper** — and keeps 60.3 % of words against
58.9 %. DataDistribution writes `run_number=0`, which masks to `run_number=<NUM>`
as one readable token; padding splits it into three tokens and gives Drain two
more positions to wildcard.

Depth 8 over depth 6 buys 0.7 points of words kept for 3 % more processor time.
Keeping numeric tokens buys another 0.7 points for 41 % more templates, 270
against 191, which is not worth it on a family this small.

**Frozen for `datadist`: no padded separators, depth 8, similarity 0.4, numeric
tokens parametrised.** 191 templates, 5.37 core-seconds a million, 60.3 % of
words kept, no contentless templates.

#### `dpl` — and a round 6 setting that does not survive the strip

3,000,000 process-tree lines of the DPL format, 32 cells. Words kept was
**99.8 % in every single cell**, so it cannot separate anything here and is left
out of the table.

| Padded separators | Depth | Numeric | Templates | Core-s / M | Contentless |
|---|---:|---|---:|---:|---:|
| **(none)** | **8** | **parametrised** | **920** | **5.21** | **1.1 %** |
| (none) | 8 | kept | 1,252 | 5.21 | 0.7 % |
| (none) | 6 | parametrised | 884 | 5.06 | 1.4 % |
| `=` | 8 | parametrised | 809 | 7.57 | 1.5 % |
| `= ;` (round 6's setting) | 8 | parametrised | 809 | 8.19 | 1.5 % |
| `= ;` | 6 | parametrised | 774 | 8.03 | 1.8 % |
| `= ; :` | 8 | parametrised | 815 | 11.55 | 1.3 % |
| `= ; :` | 6 | parametrised | 778 | 11.40 | 1.7 % |

🔴 **Round 6 chose `= ;` for this family on a metric that cannot see the
choice.** Its pad comparison read 88.3 % words kept for `= ;` against 87.7 % for
`= ; :`. Round 6's own recipe table, on the same family, read 99.7 %. Both
numbers are in `docs/SOAK_RESULTS.md` above and they differ because the pad
comparison was made **without the clock strip in front of the miner**, so the
clock tokens were in the word count and being wildcarded. With the strip in
place — which is how the recipe actually runs, and how the collector now hands
the line over — every pad set keeps 99.8 % and the metric is saturated.

With readability out of the argument the trade is cost against template count.
**Not padding is 36 % cheaper than `= ;` — 5.21 core-seconds a million against
8.19 — and produces fewer contentless templates, 1.1 % against 1.5 %.** It costs
111 more templates, 920 against 809, and round 6 already established that more
templates are affordable: embedding a template costs 0.07 % of one mining pass,
and the extra clusters land in the tail rather than in the busy series a
detector uses.

**Frozen for `dpl`: no padded separators, depth 8, similarity 0.4, numeric
tokens parametrised.** 920 templates, 5.21 core-seconds a million, 1.1 %
contentless.

Similarity moved nothing on this family either: 0.4 and 0.5 gave identical
template counts in 30 of 32 cells and differed by 2 templates in the other two.

Keeping numeric tokens would halve the contentless share, 0.7 % against 1.1 %,
for 36 % more templates. That is the one knob here worth revisiting once the
retrieval benchmark can say whether a contentless template costs a search
result.

#### `dds` — round 6's recipe, re-run on 28 times the data

Round 6 froze the DDS recipe on 43,972 lines and flagged the sample itself as
the weakest thing about it: operations report DDS is the highest-volume family
during data-taking, and round 4's corpus alone held six times more. This corpus
holds **1,236,971**.

| Padded separators | Depth | Sim | Numeric | Templates | Core-s / M | Words kept |
|---|---:|---:|---|---:|---:|---:|
| **`=`** | **8** | **0.5** | **kept** | **1,570** | **21.00** | **87.9 %** |
| `=` | 8 | 0.4 | kept | 1,557 | 20.99 | 87.3 % |
| `=` | 8 | 0.5 | parametrised | 609 | 20.93 | 83.9 % |
| `= ;` | 8 | 0.5 | kept | 1,592 | 23.15 | 87.7 % |
| `= ; :` | 8 | 0.5 | kept | 1,375 | 26.55 | 87.9 % |
| (none) | 8 | 0.5 | kept | 1,536 | 12.18 | 76.3 % |
| (none) | 8 | 0.4 | parametrised | 521 | 11.79 | 69.8 % |

**Round 6's choice holds on every knob**: pad `=`, depth 8, similarity 0.5,
numeric tokens kept. Words kept reads 87.9 % here against round 6's 88.1 % on a
corpus twenty-eight times smaller, which is as close as two different samples of
the same thing get.

**And on this family the readability metric works, which is what makes the `dpl`
result above trustworthy rather than convenient.** Padding `=` costs 72 % more
than padding nothing — 21.00 core-seconds a million against 12.18 — and buys
**11.6 points of words kept**, 87.9 % against 76.3 %. That is a real trade and
the sweep can see it. On `dpl`, with its clock stripped, the same sweep reads
99.8 % in all 32 cells: the metric is saturated there, not broken here.

DDS is the most expensive family per line by a wide margin: 21.00 core-seconds a
million against 5.21 for `dpl` and 5.37 for `datadist`. Round 6 said the same
thing and it is still the number to watch, because it is also the family whose
production volume this corpus is least likely to represent.

### One recipe per format family, as it now stands

| Family | Clock and severity | Padded separators | Depth | Similarity | Numeric tokens | Core-s / M | Words kept |
|---|---|---|---:|---:|---|---:|---:|
| `infologger` | already fields | `= ; :` | 8 | 0.4 | parametrised | 8.85 | 94.5 % |
| `dpl` | stripped by the collector | **(none)** | 8 | 0.4 | parametrised | **5.21** | 99.8 % |
| `datadist` | stripped by the collector | **(none)** | 8 | 0.4 | parametrised | **5.37** | 60.3 % |
| `dds` | already fields | `=` | 8 | 0.5 | **kept** | 21.00 | 87.9 % |
| `ildaemon` | stripped by the collector | `= ; :` | 8 | 0.4 | parametrised | — | — |
| `journald` | already fields | `= ; :` | 8 | 0.4 | parametrised | — | — |

The `infologger` row is round 6's, unchanged and not re-run. `ildaemon` and
`journald` carry the shipped default and **have never been measured**, because
no corpus of either exists off the farm. Both are marked as guesses in
`tools/templating/drainbench.py` and neither should be quoted as a result.

The `stdout` family that round 6 froze no longer exists as a mining unit. It was
two formats; `tools/templating/refamily.py` splits it, and the entry is kept in
the code only so round 6's figures can still be reproduced.

`datadist` keeps 60.3 % of words where `dpl` keeps 99.8 %, and that gap is real
rather than an artefact: a DataDistribution line is mostly state-machine and
buffer arithmetic, so more of it masks away.

### Where Stage S0 stands, counted rather than claimed

`docs/SEMANTIC_PLAN.md` lists sixteen conditions for the Stage S0 gate. Nine are
met, six are not, and one is met with a caveat worth stating.

**Met:**

- Every unique useful format has a parser or an explicit exclusion.
- Every merged program keeps its original identity.
- The production renderer generated every replay configuration — and it could
  not do that at the start of the round.
- Classifiable envelope fields parse on at least 99 percent of sampled lines:
  99.83 % on the process tree, 99.97 % on DDS.
- A person read the remaining unmatched lines, and they are named above.
- Every high-value pattern has a fixed parser test.
- Every source has a routing decision record.
- Shared files cannot be collected more than once.
- Replay covers parsing, routing, counts, duplication and restart, on all three
  deployed Fluent Bit versions.

**Met with a caveat:** the gate asks that replay also cover **mappings**. The
index templates are checked as JSON and read by eye — which is how the
`dynamic: "strict"` trap on the InfoLogger index was caught before it rejected a
document — but no document has been indexed through them. `replaycheck.py` has
no OpenSearch in the loop. That is the cheapest remaining gap to close and it
does not need the farm.

**Not met, and three of the six need an EPN:**

1. **No dated live census.** The control machine had no Kerberos ticket this
   session, so no EPN was reachable. Everything above rests on the S3 archive
   and on the 27 August survey. `tools/epnsurvey/survey.sh` is the instrument
   and it needs one `kinit`.
2. **No source-owner approval of the registry.** That is a conversation, not a
   measurement.
3. **The live job-log path is still unknown.** Every `/scratch` run directory is
   from October 2022 and the archive is a backup bucket. Neither says where a
   run writes today.
4. **The journal has no representative replay data.** It is the one source that
   needs no replay in production and the one source that cannot be exercised
   off the farm, because a laptop has no journal. All three deployed versions
   accept its configuration — which is how the 5.0.8 incompatibility was found
   — but no record has ever been through those filters. Step 6 of "what to
   build, in order" is exactly this: capture a journal bundle from an EPN.
5. **No frozen catch-all threshold.** The unclassified share is measured, 0.17 %,
   and the shapes behind it are named. Nobody has said what number is too many.
6. **No frozen corpus manifest.** The corpus is reproducible from `corpus.py`
   and `refamily.py`, but nothing pins its hashes, revisions or time windows.

One further condition, **replay and live clocks remain distinguishable**, holds
for the archive sources, whose event times are 2022 or 2026. It does not hold
between them and the journal, which is written now: a replayed O2 error and a
live kernel fault still land years apart and cannot be correlated. The offset
that would map one onto the other is unchosen, and it is the open question the
27 August survey already recorded.

### What the collector still does not do

The **template catalog has no route at all.** Raw-log routing and catalog
routing are required to be separate decisions and only the first has been made.
Templates are mined offline from the archive, so nothing about the catalog is
decided by a tag in the collector — which satisfies the separation rule and
leaves a real hole: informational lines stay on the node, so a catalog built
from the durable tier alone would never see the templates of 96.94 % of the
process tree. Bounded catalog updates that travel without the lines themselves
are unbuilt, and that is the next collector-side piece.

The **per-detector CTF size report has no parser and will not get one.** Onigmo
cannot return a repeated capture, so a variable-length detector list cannot come
out of one regex, and the collector runs no per-record Lua by design. It belongs
in the ingest pipeline.

`ildaemon` and `journald` have **no measured templating recipe.** No corpus of
either exists off the farm. Both carry the shipped default and that is a guess,
marked as one in `tools/templating/drainbench.py`.

The **cockpit does not show `program` yet.** The field is mapped, searchable and
carried by the live lane, but the saved searches still have their old columns.
That is a `cockpit.ndjson` change and it was left alone deliberately, because
the v4 cockpit is waiting on a real-VM redeploy and this round should not
disturb it.


---

## Round 8 — the census, and what it changed

Written 5 September 2026, after a read-only census of all four allocated EPN
nodes. Round 7 built five sources against the S3 archive. This round put the
same code in front of the farm and found that four of its facts were wrong.

Access first, because it cost an hour and will cost it again. `~/.ssh/config`
sets `GSSAPIAuthentication no` for `lxplus`, so a Kerberos ticket does nothing
for SSH and `BatchMode` can only fail. Overriding that one setting in a
session-local config reached all four nodes. AFS stays broken on the forwarded
ticket, which does not stop the jump.

### Four Fluent Bit versions are deployed, not two

| Node | Tier | Fluent Bit | Tested before today |
|---|---|---|---|
| epn146 | worker | 4.0.1 | yes |
| epn228 | worker | 4.0.1 | yes |
| epn323 | worker | 4.0.14 | yes |
| epn-infra13 | storage | **3.2.8** | **no** |

The storage node is two majors behind. It runs no collector today, so nothing
is broken — but a 5.0.8 incompatibility was already found this round on a
property 4.x accepts, so a version nobody tests is a version nobody can deploy
onto. **Every one of the four builds has the `systemd` input compiled in**, so
the journal is deployable everywhere.

**3.2.8 was then tested, and it passes everything.** 29 expectations, the
journal over epn146's own entries, and the restart and duplicate check:

| Version | Expectations | Journal | Restart |
|---|---|---|---|
| 3.2.8 | 29 passed | 75,243 records, `TfBuilder` named | no duplicates |
| 4.0.1 | 29 passed | 75,243 records, `TfBuilder` named | no duplicates |
| 4.0.14 | 29 passed | 75,243 records, `TfBuilder` named | no duplicates |
| 5.0.8 | 29 passed | 75,243 records, `TfBuilder` named | no duplicates |

So the configuration is good on **every version the farm actually runs**, and on
the one the deploy role installs. That is not luck: the one incompatibility
found this round, `multiline.parser` on the `systemd` input, was moved to a
filter precisely because it split the versions.

### Four things the census proved wrong

🔴 **The InfoLogger daemon log is separated by spaces, not a tab.**
`docs/LOG_TYPES.md` recorded `YYYY-MM-DD HH:MM:SS.ffffff<TAB>message` and the
parser required `\t`. The real file on epn146 uses whitespace, so **that parser
matched none of its 162,642 lines.**

🔴 **The client-count extractor missed 36 % of that file.** It anchored on the
word `client`, which reaches the first shape and not the second:

```
New client: 557/2048
4 clients disconnected, now having 962/2048
```

58,175 lines were arriving without a count. The extractor now anchors on the
count at the end of the line, which both shapes share.

🔴 **"A handful of lines a day" was wrong by two orders of magnitude.** 162,642
lines from 10 March to 1 September is **about 930 a day**, growing without
bound: no `logrotate.d` entry covers this file. The peak seen is **1025 clients
of 2048**, half the ceiling, so the saturation signal is real and has headroom.

🔴 **The journald filter order was wrong and `Comm:` was never extracted.** The
parser ran before the multiline fold, so it read whichever single entry happened
to carry the field rather than the reconstructed fault. Folding first fixes it.

### The journal, measured with the right permissions

The first pass reported 1,000 to 8,000 entries a node and no kernel entries at
all. `journalctl` as an ordinary user shows only that user's own sessions, and
says so in a hint that is easy to scroll past. Read with `sudo`, over 30 days:

| Node | Info (p6) | Warning (p4) | Kernel | On disk |
|---|---:|---:|---:|---:|
| epn146 | 71,964 | 1,992 | **986** | 797 MB |
| epn228 | 76,734 | 972 | 15 | 1.4 GB |
| epn323 | 26,765 | **284,552** | 17 | 411 MB |
| epn-infra13 | **12,957,663** | 15,615 | 306 | 3.9 GB |

**A worker writes 2,500 to 10,500 entries a day.** Against millions of
process-tree lines that is nothing, which is what makes Lubos's instruction —
collect all system logs — free to obey. The unit allow-list is gone.

**It would have created two blind spots.** The list named slurmd, crond,
fluent-bit, opensearch and alice-replay. On epn323 that drops NetworkManager,
172,935 entries; on epn-infra13 it drops slurmctld, nearly all of 13 million.

**epn323 emits 9,485 warnings a day and the other two workers do not.** Under
priority routing those all reach durable storage. That is the routing working:
epn323 is a month old and a major release ahead, and it is saying something the
others are not.

### The journal source, finally exercised

The journal was the one source that could not be fixtured — binary files, at
least eight megabytes each, belonging to the machine that wrote them. Adding a
`path` property to the input, unset in production, makes a captured journal
readable off the farm.

Against epn146's own journal, through the shipped configuration:

| | |
|---|---:|
| Records | 75,243 |
| Node-local (priority 5 to 7) | 74,023 |
| Durable (priority 4 and below) | 1,220 |
| Distinct fields per record, before the allowlist | **234** |
| Distinct fields after | **16** |

And the payoff, in the real data rather than in a fixture:

```
WARNING: CPU: 59 PID: 3741929 at drivers/iommu/dma-iommu.c:1206 iommu_dma_unmap_page+0x79/0x90
   comm: TfBuilder
```

**The multiline rule reconstructed the fault, the parser pulled the process name
out of it, and the priority rule sent it to durable storage.** That is the
correlation the journal source exists for, working end to end, on a machine that
cannot produce one.

The 234-to-16 trim matters because the application mapping is `dynamic: false`:
every field nobody named is stored on the durable tier and is never searchable.

### The mappings, exercised at last

Round 7 validated the index templates as JSON and by reading them. This round
put real collector records through a real OpenSearch 3.7.0.

It immediately found that **the bootstrap script would not have run**: adding the
catalog index template dropped the closing quote on the `INGEST_PIPELINE`
heredoc, `<<'JSON` instead of `<<'JSON'`. The JSON validator did not catch it
because it looks for well-formed blocks and a malformed one is simply skipped.
It now asserts that the number of heredoc openers equals the number of complete
blocks, and `bash -n` parses the rendered script.

With that repaired, `tools/collector/mappingcheck.py`:

| Check | Result |
|---|---|
| Records indexed, including through the `dynamic: "strict"` InfoLogger index | **31, none rejected** |
| Fields the collector emits that are stored but not searchable | **none** |
| A line no parser claimed, reachable through `message` | yes |
| `severity_norm` values produced | debug, error, info, state, unknown, warning |
| `program` resolved from the file name, the DDS column, the daemon constant, and InfoLogger's `facility` | yes |

### The template catalog, built and proved

The largest unbuilt piece of round 7. The collector keeps 96.94 % of the process
tree node-local, so a catalog built from the durable tier alone would hold the
templates of 3.06 % of it.

`deploy/roles/template_catalog` reads a worker's **own** local index, mines with
the same recipe object the archive miner uses — shipped from `tools/templating`,
not reimplemented — and sends one document per canonical template. **No raw
informational line crosses the network.**

Exercised against the same OpenSearch:

| | |
|---|---|
| Five seeded lines across three formats | 3 canonical templates |
| The process tree split | `dpl` and `datadist`, by the collector's own cascade |
| A second pass over the same data | 0 lines, the watermark resumed |
| The same template seen by a second node | count added, both nodes recorded, programs merged |

Two failure modes were designed out and are tested. The document identifier is
sha1 of family and template text, **not Python's `hash()`**, which is randomised
per process and would have grown a new document every ten minutes instead of a
count. And the watermark lives in the catalog itself, so a wipe forces a clean
rebuild.

### A live O2 source nobody collects

`odc-grpc-server` on epn-infra13 writes `/var/log/odc/staging/odc_<date>.log`:
**11.5 MB a day, 9.1 GB retained, written minutes before the census.** It is the
run orchestrator, and the only O2 process writing a log on the farm right now.

It is not integrated, and both reasons are decisions rather than work. It lives
on the storage node, which runs no collector and runs the untested 3.2.8. And
while no run is active it is 100 % informational and 100 % two shapes — a status
poll and its reply, every three seconds. 44,444 sampled lines, one severity, one
program, two templates.

During a run it would carry real orchestration. It is recorded in
`docs/LOG_TYPES.md` with its format and its volume so the decision is a decision.

### Where does a live run write its job logs

Nowhere, today. `ps` on all four nodes found only infrastructure daemons — no
DPL device, no TfBuilder, no dds-agent — and `/scratch/jl` still holds the same
54,450 files from October 2022 on every node.

**That is a better answer than the guess it replaces, and it is falsifiable.**
`survey.sh` now reads `/proc/<pid>/fd` for every O2-shaped process, so repeating
the census during a run answers the question directly rather than by inference.

### The last two recipes, measured

Round 7 shipped `ildaemon` and `journald` with guessed recipes and marked them as
guesses. Both are now measured on farm-captured corpora.

**`ildaemon`, 163,670 lines from epn146.** Every one of the 32 cells produced the
same **18 templates** with **100 % of words kept**, because the file has two
shapes. Only cost moved, so it pads nothing: 3.44 core-seconds a million against
5.54 for `= ; :`.

**`journald`, 277,541 entries from the three workers.** This family keeps numeric
tokens, which only `dds` also does.

| Padded separators | Numeric | Templates | Core-s / M | Words kept | Contentless |
|---|---|---:|---:|---:|---:|
| **`=`** | **kept** | **1,362** | **7.81** | **96.8 %** | **0.7 %** |
| `=` | parametrised | 554 | 7.80 | 89.1 % | 1.6 % |
| (none) | kept | 1,399 | 5.06 | 94.2 % | 0.6 % |
| `= ; :` | parametrised | 508 | 10.67 | 86.6 % | 2.0 % |

The last row is what round 7 guessed. **The measured recipe beats it on every
axis at once** — cheaper, more readable, fewer contentless templates.

### All six families, measured

| Family | Padded separators | Depth | Sim | Numeric | Corpus |
|---|---|---:|---:|---|---|
| `infologger` | `= ; :` | 8 | 0.4 | parametrised | round 6, not re-run |
| `dpl` | none | 8 | 0.4 | parametrised | 3,000,000 archive lines |
| `datadist` | none | 8 | 0.4 | parametrised | 92,813 archive lines |
| `dds` | `=` | 8 | 0.5 | **kept** | 1,236,971 archive lines |
| `ildaemon` | none | 8 | 0.4 | parametrised | 163,670 farm lines |
| `journald` | `=` | 8 | 0.4 | **kept** | 277,541 farm entries |

**No recipe is a guess any more.**

### The configuration, run over real traffic rather than fixtures

`replaycheck.py` runs a few dozen fixture lines, each chosen because it broke
something once. `coverage.py` runs millions, but through Python's `re` rather
than through Onigmo. Neither answers whether the shipped configuration handles
the farm's actual traffic, and the gap between them was an untested assumption:
**every coverage figure in round 7 was measured on an engine Fluent Bit does not
use.**

`tools/collector/realcheck.py` closes both. It lays real lines out where the
production tail pattern finds them, runs the rendered production configuration
in real Fluent Bit, and reports what came out — then scores the same lines with
Python and compares.

| Family | Real lines | Records | Severity recovered | Unclaimed | Local / durable |
|---|---:|---:|---:|---:|---|
| `dpl` | 200,000 | 199,377 | **99.81 %** | 0 | 198,368 / 1,009 |
| `datadist` | 92,813 | 92,811 | **100.00 %** | 0 | 92,811 / 0 |
| `dds` | 200,000 | 199,939 | **100.00 %** | 0 | 173,246 / 26,693 |
| `ildaemon` | 163,670 | 163,670 | n/a, no severity column | 0 | all durable |

**The two engines agree.** Python and Onigmo differ by 0.31 points on `dpl`,
0.03 on `dds` and 0.00 on `datadist`. The round 7 coverage figures stand, and
they now stand on a check rather than on an assumption.

The extractors on real traffic, which is the number that says whether a parser
earns itself:

| Family | Field | Share of real records |
|---|---|---:|
| `dds` | `slot_id`, `channel_id`, `channel`, `task` | 3.42 % each |
| `dpl` | `program` | 100 % |
| `dpl` | `log_time` | 99.78 % |
| `ildaemon` | `clients`, `client_limit` | **99.99 %** |

That last row is the fix from this round measured on the file it was wrong
about. The extractor it replaced would have reached 64 %.

`dds` resolved **46 distinct programs** from real traffic, which is the third
column doing its job as program identity.

🔴 **`infologger` is not in this table.** It arrives over TCP rather than from a
file, so the file-based layout does not reach it. It is covered by fixtures and
by the mapping check, and not by real bulk traffic.

### Stage S0, recounted

| | Round 7 | Now |
|---|---|---|
| Met | 9 | **13** |
| Not met | 6 | **3** |
| Met with a caveat | 1 | 0 |

Closed today: the dated live census; representative replay data for the journal;
the frozen catch-all threshold, set at 1 % and **enforced** by
`tools/collector/coverage.py` against a measured 0.17 %; the corpus manifest, now
generated by `tools/templating/manifest.py` with input hashes, per-file code
revisions and the recipe recorded field by field; and the mappings caveat, closed
by indexing real records.

Still open:

1. **Source-owner approval.** A conversation with Lubos and Federico.
2. **The live job-log path.** Unanswerable while no run is active; the
   instrument now exists.

The clock is no longer on that list. See below.

### The two sources that could not be replayed, and now can

`ildaemon` and `journald` were integrated in round 7 and **neither had a replay
path**. `grep -c 'ildaemon\|journal' images/replay/replay.py` returned 0. Both
are written on a live EPN and neither is in S3, so a staging VM collected
nothing from either — the sources were in the production configuration and had
no way to be exercised there.

`tools/epnsurvey/mkbundle.sh <node> <dir>` captures both from a real node.
`REPLAY_BUNDLE_SRC=<dir>` ships it. The producer role paces the daemon log into
the path the collector tails; the collector points its `systemd` input at the
shipped journal. With no bundle, both families skip and say so, because a VM
without one is the ordinary case.

The two are replayed differently and the difference is not cosmetic. The daemon
log is **written**, paced at 20 lines a second, because a tail input needs a file
that grows. The journal is **read in place**: `libsystemd` reads the binary
format, so a captured journal is replayed by being present, and a JSON export
would have to be re-parsed by something that is not the production input.

Captured from epn146: 162,642 daemon lines and a 8 MB system journal, 17 MB
total. Replayed: 162,642 lines written to the tail path, 1 journal file read in
place.

🔴 **The bundle work exposed a break this session had already introduced.** The
shifted-clock wrapper monkeypatches `_stdout_event_ts`, whose return type
changed from a string to a `(date, seconds)` pair when the event time started
being rebuilt per line. `REPLAY_CLOCK=shifted` would have failed on the first
process log. Fixed and checked at three offsets.

### The clock, decided rather than deferred

The windows, measured: archive process tree and DDS at 2026-06-20, InfoLogger
2024 to 2026, a captured journal 2026-08-06 to 2026-09-05. About two and a half
months apart under `preserved`, so no correlation window of hours spans them.

Three closures were considered and **all three are worse than leaving it open**.
Shifting the archive forward is what `replay_clock: shifted` does, and
`group_vars/all.yml` already explains why it is not the default: it collapses
`enter_system_lag_ms` and four detectors train on the constant. Shifting the
journal backwards cannot be built without regenerating a journal through
`systemd-journal-remote`, a new component in the replay path for a staging
convenience. Capturing a journal from the archive's own June window is not
possible; epn146 retains about 30 days.

**The correlation is not available on a staging VM and does not need to be.** On
a real EPN the journal and the job logs are both live and land in the same
second, which is where an IOMMU fault naming `TfBuilder` is actually read. The
VM replay exists to exercise parsing, routing, mappings and restart, and it does
all of that with the clocks apart.

The cost, stated plainly: **a cross-source correlation detector cannot be
developed against replay alone.** It has to be developed against a real node.

### Every source, over real traffic, through real Fluent Bit

The round 7 table had four rows and a hole where InfoLogger should have been: it
arrives over TCP, not from a file, so the file-based harness could not reach it.
`realcheck.py` now speaks to the socket the way `infoLoggerD` does.

| Family | Real records | Severity recovered | Unclaimed | Engines agree |
|---|---:|---:|---:|---|
| `infologger` | 50,000 | 100.00 % | 0 | n/a — parsed as JSON, no cascade |
| `dpl` | 199,377 | 99.81 % | 0 | 0.31 points |
| `datadist` | 92,811 | 100.00 % | 0 | 0.00 points |
| `dds` | 199,939 | 100.00 % | 0 | 0.03 points |
| `ildaemon` | 163,670 | no severity column | 0 | n/a |
| `journald` | 75,243 | by priority | 0 | n/a — read via libsystemd |

**All six sources are now verified on real data through the shipped
configuration.** Nothing rests on a fixture alone.

🔴 **A defect that would have shipped: the journal could never have been
enabled.** `collector_journald_enabled` defaulted to `false` and the role's
probe computes `false and <probe>`, which is false whatever the binary says. The
default is now `true` — it states whether we *want* the source — and the probe
can only turn it off. The soak renderer defaults it on too, so the rig validates
the configuration production actually ships rather than one it never runs.

---

## Round 9 — an external review, and four things it was right about

A review by another model read the round 8 work and made eight claims. Five hold
up, and three of those turned out to be worse than the review said. This round
is what checking each of them found and what was changed. Nothing below is
accepted or dismissed on the strength of the claim; each one was reproduced or
refuted against the code.

### The benchmark was measuring the wrong quantity, twice over

The review said `regexbench.py` records elapsed time and prints it under the
name of processor time. That is true, and two further faults sat behind it.

**Elapsed time was mostly not the collector's time at all.** With real processor
accounting in place, a run that takes 11.15 seconds of wall clock uses 2.97
seconds of processor. The other 8 seconds are the container waiting — for the
tail's refresh interval, for the flush timer, and for whatever else this host is
running. Every arm waits the same amount, so wall clock looked beautifully
reproducible while barely depending on the configuration at all. **Round 8's
0.8 % control was false precision, not precision.**

**The run also stopped before the work finished.** The completion test summed
every input's record count, and a `rewrite_tag` emitter is reported as an input.
`stdout_router` re-injects every record it routes, so a 200,000-line file
reports about 400,000 input records and the target of 200,000 was reached at
roughly the halfway point. The parser cascade, the routers and the sink for the
second half ran after the clock had stopped.

Three changes:

| Was | Now |
|---|---|
| wall-clock seconds, labelled `core-s/M` | the container's cumulative processor time, user plus system, read from the engine's cgroup accounting through the Docker API |
| stop when total input records ≥ line count | stop when every counter — source inputs, emitters and outputs — has stood still for 2 seconds |
| target = number of lines written | target = number of RECORD-START lines, because both tails fold continuations and a file of 200,000 lines never produces 200,000 records |

The settle window costs no processor time, which is the only reason a settle
window is affordable at all. Under wall clock it would have been 2 seconds added
to every arm.

### What the honest instrument can actually resolve

200,000 process-tree lines folding to 199,414 records, Fluent Bit 4.0.14, four
interleaved rounds, processor time:

| Arm | Min cpu-s | Median cpu-s | cpu-s / M | Wall s | Against shipped |
|---|---:|---:|---:|---:|---:|
| shipped | 2.97 | 3.24 | 14.89 | 11.15 | — |
| **control — the same configuration** | 2.70 | 3.14 | 13.52 | 11.01 | **−9.2 %** |
| ANSI tolerance removed | 2.69 | 2.91 | 13.50 | 10.91 | −9.3 % |
| `mft_decoder_error` removed | 2.60 | 2.81 | 13.03 | 8.83 | −12.5 % |

189,803 orchestrator records, eight interleaved rounds:

| Arm | Min cpu-s | Median cpu-s | cpu-s / M | Against shipped |
|---|---:|---:|---:|---:|
| shipped | 1.73 | 2.21 | 9.10 | — |
| **control — the same configuration** | 2.06 | 2.37 | 10.85 | **+19.3 %** |
| partition and run capture removed | 1.64 | 2.21 | 8.63 | −5.1 % |
| partition required to be 6+ characters | 1.66 | 2.04 | 8.76 | −3.7 % |

🔴 **This host cannot resolve a difference below roughly 20 %.** The control arm
is byte-for-byte the shipped configuration and it lands 9.2 % below on one
corpus and 19.3 % above on another. Nothing smaller than that in either table
means anything, and that includes every arm in both.

The conclusion round 8 drew survives — no collector regex change is worth making
on cost grounds — but the ground it stands on is different, and weaker, than
round 8 claimed. The correct statement is: **on this host, no arm differs from
the shipped configuration by an amount this instrument can distinguish from
zero, and the smallest amount it can distinguish is about a fifth.**

### The template catalog had four defects and shipped none of its guarantees

The review found three. The fourth is worse than any of them and it was found
while checking the first.

**1. The family label could never be right.** `family_of` split the process tree
by testing the message against DataDistribution's `[date][S] ` prefix. The
collector's own `datadist` parser eats that prefix into `time` and `severity`
before the record is ever indexed, so the test could not match and every
process-tree template was filed as `dpl`.

Nothing was mined wrongly — the `dpl` and `datadist` recipes hold identical
settings today — but the family is half of the catalog document identifier, so
the same template mined from the archive landed under a second document. The
split now reads the FIELDS the collector emits: `log_time` means `dpl`, a
single upper-case severity letter means `datadist`, everything else is `dpl`,
which is the mapping `refamily.py` uses offline.

The existing test passed because it fed a DataDistribution message with the
prefix still attached — a shape production never emits. The end-to-end check now
takes its records from the collector's own output instead of writing them by
hand.

**2. It read one route out of three.** The service read only
`application-logs-local-<node>`. It is the *informational* traffic that stays
local, so a local-only catalog holds no InfoLogger templates at all, no daemon
log, and none of the warnings and errors — the exact records the routing sends
straight to durable storage. It now reads the durable index and the InfoLogger
index too, each scoped with a `node` term to the records this worker itself
wrote, so three workers reading one shared index do not each add the same count.

**3. The resume skipped records.** The watermark stored `collector_time` alone
and the next pass queried `collector_time > watermark`. A worker writes many
records inside one millisecond, so every record sharing the last millisecond of
a pass was dropped for good. Reproduced against the real `scan` and `watermark`
functions: three records in one millisecond with a page size of two, first pass
reads two, second pass returns nothing. The watermark now carries the document
identifier as well and the scan resumes with `search_after` on both.

**4. The worker and the archive mined different templates.** `miner_for` built
its own `TemplateMiner` from the recipe tables instead of calling
`drainbench.recipe_miner`. It looked equivalent and was not: it left out the
FLOAT/NUM merge, which is a monkeypatch on drain3's `Drain` class and does not
travel with the tables. Round 6 measured that patch as the difference between
6.3 % and 1.0 % of lines landing on a template whose content is one wildcard.
The module's own docstring promised these were the same object; they were not.

The tree is also persisted between timer runs now. Drain is incremental and
order-dependent, so a tree rebuilt from empty every ten minutes mines the same
messages into different templates depending on which batch they fell in.

The unit tests went from 8 to 23, and five of them fail against the old code.

### The run orchestrator, read properly this time

The review said `odc-grpc-server` was found by the census and left at a routing
decision. That is true, and the reason it was left there does not survive a
second reading of the data.

Round 8 sampled one idle day, found 100 % `inf` and two shapes, and called it a
heartbeat. Sampling 25 of the 560 retained files instead:

| | Round 8, one idle day | Round 9, 25 files |
|---|---:|---:|
| Lines | 44,444 | 2,612,951 |
| Severities | 1 | 5 |
| `inf` | 44,444 | 1,650,555 |
| `dbg` | 0 | 961,933 |
| `wrn` | 0 | 312 |
| `err` | 0 | 153 |
| `fat` | 0 | 8 |

The file for 20 January 2026 carries run numbers 2304 and 2305, two `fat` lines
for a STOP transition that timed out, and 36 `err` lines naming the collections
that failed and the tasks that exited. "Idle, it is a heartbeat" was a statement
about the day sampled, not about the source.

#### The format, and one field worth the whole source

```
2026-01-20 08:56:29.045314 fat  odc-grpc-server     1091836 2zRVEg9xZRp:2304     Timed out waiting for STOP transition
└──────── time ──────────┘ sev  └── program ────┘   └ pid ┘ └partition┘ run      └───────── message ─────────────────┘
```

`partition` and `run` are the same two values InfoLogger carries, so an
orchestration failure and the detector messages from the same run join on them.
981,228 of the sampled lines carry the pair.

The partition group is `[A-Za-z0-9]+`. A six-character minimum was tried, to
stop a message beginning `word:123 ` being eaten as a partition; the sample
holds `test0` and `idd`, which are real hand-named test partitions, so the
longer form would lose them. The shorter form matched 190,250 lines across a
busy day and an idle day with **no false positive at all**, and both forms cost
the same within what the instrument can resolve.

#### Coverage, on real data

190,052 records from a busy day and an idle day, through the rendered production
cascade with the multiline fold:

| | |
|---|---:|
| Lines matched | 190,052 of 190,052, **100.00 %** |
| Severity recovered | 190,052, **100.00 %** |
| Unclassified | **0.00 %**, against a frozen threshold of 1.00 % |
| Node-local (`inf`, `dbg`) | 190,014, 99.98 % |
| Durable (`err`, `fat`) | 38, 0.02 % |

#### The recipe, swept on the same corpus

32 cells, 190,250 lines. This is the one family where padding is better on both
quality axes at once:

| Pad | Sim | Numeric | Templates | cpu-s / M | Words kept | Contentless |
|---|---:|---|---:|---:|---:|---:|
| (none) | 0.40 | parametrised | 276 | 6.27 | 94.5 % | 1.1 % |
| `=` | 0.40 | parametrised | 267 | 11.22 | 94.5 % | 1.5 % |
| `= ;` | 0.40 | parametrised | 267 | 22.24 | 94.5 % | 1.5 % |
| **`= ; :`** | **0.40** | **parametrised** | **266** | **24.36** | **98.7 %** | **1.9 %** |
| `= ; :` | 0.40 | kept | 366 | 24.20 | 98.9 % | 0.8 % |

**Chosen: `= ; :`, similarity 0.40, numeric tokens parametrised.** It reads
98.7 % of words against 94.5 % unpadded and produces fewer templates at the same
time, because the orchestrator writes `key: value` everywhere — `exit code: 1`,
`id: 3340879470082071756`, `path: "main/..."` — and separating the colon keeps
the key literal instead of letting it merge into the value.

It costs 3.9 times as much as not padding, and that is accepted rather than
traded away. The source writes about 100,000 lines a day on **one** machine, so
the whole family costs roughly 2.4 processor-seconds a day. Cost is the binding
constraint on `dpl`, which is millions of lines a day across every worker; here
it is not, and buying readability with it is the right way round.

Numeric tokens are parametrised — 266 templates against 397 with them kept —
because the numbers that matter, the run number and the partition, are already
their own fields on the record. Keeping them in the template text only splits
one failure shape across every run it happened in.

#### It overlaps InfoLogger, and the overlap is priced

The orchestrator forwards to InfoLogger under `system: ODC`. One 2024 archive
partition holds 108,757 such rows out of 275,205, so its `inf`, `err` and `fat`
lines already reach OpenSearch by another route. Collecting the file is still
right, for two measured reasons:

1. **The debug tier is not forwarded.** InfoLogger's ODC rows are `I`, `E` and
   `F` only. The file is 36.7 % `dbg`, which is where the state transitions and
   the device-state counts live.
2. **The run attribution is far more complete in the file.** Of 108,757
   forwarded rows, 509 carry a run number and 7,751 carry a partition. In the
   file, 46 % of a busy day's lines carry both.

The duplication is nearly free because of where the severity split puts it. The
lines that exist in both places are overwhelmingly `inf`, and `inf` stays
node-local. **On the durable tier the overlap is 38 lines out of 190,052.**

🔴 **The blocker is unchanged and it is not the parser.** `epn-infra13` runs no
collector and runs Fluent Bit 3.2.8, two majors behind everything else. The
parser, the routing, the mapping, the recipe, the replay path, the bundle
capture and the tests are all shipped and green, and the role enables the input
only where the file exists, so the configuration is inert on every worker.
Putting a collector on the storage node is a deployment decision.

### Rotation, which nothing tested before

The review was right that rotation sat outside the restart check, and it is a
different failure. `logrotate` renames the file the tail holds open and the
writer creates a new one under the old name. A tail that keeps following the
renamed inode goes quiet for ever without an error; a tail that treats the new
file as unseen ships everything twice.

`replaycheck.py --rotate` now does it to a **running** collector — renaming a
file the tail has already closed proves nothing. Three lines in three phases:
one before the rotation, one appended to the renamed file afterwards, and one in
a file created after it. All three must arrive, each exactly once.

The orchestrator's log is what this exercises, because it is the one that
actually rotates on the farm: daily, by date, read through a glob.

### InfoLogger was being tested against invented records

The review was right, and the fix was available all along. `realcheck.py` took
the message text from a corpus and filled the other fifteen columns with
plausible constants, cycling four severities in turn. Every timestamp was a
millisecond from the last, every run was the same number, and no field ever held
the NULL or the embedded quote a real dump is full of.

It now reads a real InfoLogger dump out of the archive and parses it with
**replay.py's own reader**, imported rather than restated, so what is fed to the
collector is byte-for-byte what the replay service sends. The synthetic path is
kept under `--synthetic-infologger` and the output says which one ran.

That change is also what surfaced the ODC-to-InfoLogger overlap above: the first
real dump read turned out to be 40 % ODC rows.

### The one round 8 result that did not survive

Round 8 found a single arm that beat the control: capturing the whole command
line in `dds_task` cost **+20.8 %** of the DDS family against a control at
+0.8 %. That number is why the shipped parser captures `\S+` instead of `.*`.

Re-measured against real processor time — 399,889 DDS records, ten interleaved
rounds, Fluent Bit 4.0.14:

| Arm | Min cpu-s | Median cpu-s | cpu-s / M | Against shipped |
|---|---:|---:|---:|---:|
| shipped | 7.42 | 11.20 | 18.56 | — |
| **control — the same configuration** | 7.61 | 13.73 | 19.03 | **+2.5 %** |
| whole command line in `task` | 7.90 | 14.77 | 19.76 | **+6.5 %** |

**+6.5 % against a +2.5 % control is not a result.** The gap between the arm and
the control is four points, which is smaller than the gap between two identical
configurations on the process-tree corpus in the table above. The 20.8 % was
wall clock measuring the host, the same way the phantom 17.3 % in round 7 was.

The medians show why this corpus is hard to measure at all: the ten rounds run
from 7.4 to 22.8 processor-seconds, a factor of three, drifting upward through
the run. Interleaving makes that drift hit every arm equally, which is what
makes the minimum comparable; it does not make the instrument precise.

**The narrow capture stays, on a different argument.** `dds_task` runs with
`preserve_key`, so the record already holds the whole 3,487-byte command line in
`message`. Capturing it again into `task` writes the same 3.4 kB twice into
every such record and into the index, where `task` is a text field whose keyword
subfield stops indexing at 1,024 bytes. That reason is about the document, not
about the processor, and it does not depend on a measurement this host cannot
make. The parser comment now says this rather than quoting 22 %.

### What this round changes about how to read the earlier rounds

Every processor-cost figure in rounds 6 through 8 that came from `regexbench.py`
was wall clock. Where those rounds concluded "no measurable difference", the
conclusion still holds and holds more strongly, because the instrument's real
noise floor is wider than they thought. Where a round concluded a difference WAS
real — round 7's 17.3 % and round 8's 20.8 % — both have now been retracted, and
both were the same mistake made twice.

The offline templating figures are unaffected: `drainbench.py` has always
measured `time.process_time()`, which is processor time by construction.

### Everything re-verified, and 3.2.8 tested for the first time

| Check | Result |
|---|---|
| Fixture expectations, Fluent Bit **3.2.8** | 35 of 35 |
| Fixture expectations, Fluent Bit **4.0.1** | 35 of 35 |
| Fixture expectations, Fluent Bit **4.0.14** | 35 of 35 |
| Fixture expectations, Fluent Bit **5.0.8** | 35 of 35 |
| Restart, all four versions | no duplicates, the line written during the outage arrived once |
| **Rotation, all four versions** | the line before, the append to the renamed file and the new file each arrived exactly once |
| Journal configuration accepted, all four | yes |
| Index mappings, OpenSearch 3.7.0 | 43 real collector records indexed, **0 rejected**; every emitted field mapped and searchable |
| Template catalog, end to end | six families — `datadist`, `dds`, `dpl`, `ildaemon`, `infologger`, `odc` — every route read, counts add across nodes, watermark resumes |
| `template_catalog` unit tests | 23 of 23, up from 8 |
| `replay.py` unit tests | 20 of 20, up from 17 |
| Ansible `--syntax-check`, 12 playbooks | all pass |

🟢 **3.2.8 is no longer an untested version.** It is what `epn-infra13` runs and
what the orchestrator would be collected on, and nothing in this repository had
ever been run against it. It now passes every expectation the other three do,
including the new rotation check. The blocker on collecting the orchestrator is
the deployment decision, and only that.

### Real traffic, per source

Both bulk runs go through real Fluent Bit at a real version, not through a
Python re-implementation.

**The run orchestrator**, 190,000 real lines:

| | |
|---|---:|
| Records produced | 189,802 (198 continuation lines folded in) |
| Severity recovered | **100.00 %** |
| No parser claimed | **0.00 %** |
| Routing | 189,764 node-local, 38 durable |
| `program` extracted | 189,802, 100 % |
| `pid` extracted | 189,802, 100 % |
| `partition` and `run` extracted | 70,552 each, 37.17 % |
| Python `re` against Onigmo | 99.90 % against 100.00 %, the two engines agree |

**InfoLogger**, 60,000 rows out of a real archive dump, all sixteen columns:

| | |
|---|---:|
| Records produced | 60,000 of 60,000 |
| Severity recovered | **100.00 %** |
| No parser claimed | **0.00 %** |
| Routing | 60,000 to `infologger`, none local |
| Real severity mix | 39,762 `I`, 16,723 `E`, 3,515 `W` |
| `partition` present | 31,277, 52.13 % |
| `run` present | 31,264, 52.11 % |

The severity mix is the point of the change. The synthetic records cycled four
severities in turn and produced exactly 25 % of each; the archive is 66 % `I`
and 28 % `E`, and it holds no `D` at all in this partition.

### All seven families, as they now stand

| Family | Padded separators | Depth | Sim | Numeric | cpu-s / M | Words kept | Corpus |
|---|---|---:|---:|---|---:|---:|---|
| `infologger` | `= ; :` | 8 | 0.4 | parametrised | 8.85 | 94.5 % | round 6, not re-run |
| `dpl` | none | 8 | 0.4 | parametrised | 5.21 | 99.8 % | 3,000,000 archive lines |
| `datadist` | none | 8 | 0.4 | parametrised | 5.37 | 60.3 % | 92,813 archive lines |
| `dds` | `=` | 8 | 0.5 | **kept** | 21.00 | 87.9 % | 1,236,971 archive lines |
| `ildaemon` | none | 8 | 0.4 | parametrised | 3.44 | 100 % | 163,670 farm lines |
| `journald` | `=` | 8 | 0.4 | **kept** | 7.81 | 96.8 % | 277,541 farm entries |
| **`odc`** | **`= ; :`** | 8 | 0.4 | parametrised | 24.36 | 98.7 % | 190,250 farm lines |

Depth is one number for every family, because `RECIPE_DEPTH` is a module global
rather than a per-family setting. `odc` would take 266 templates at depth 6
against 266 at depth 8 with the same words kept, so nothing is lost by that; it
is recorded because it is a real constraint on the table above, not a choice
made seven times.

`infologger` is the one row still carrying round 6's numbers. It is not a guess
— it was measured — but it has not been re-measured against the split families
or with the current masker, and it is the only row in the table that can be said
of.

---

## Round 10 — a second review, and what "all green" was worth

The reviewer ran the round 9 work and found four things. All four reproduce.
The first of them also shows that **"all four versions pass" was not a result at
all** — it was one sample of a test that fails about one time in three.

### The rotation check was a coin toss and I reported the flip

Run repeatedly on Fluent Bit 5.0.8, the rotation check fails 1 in 3. The line
appended to the renamed file arrives zero times. 4.0.14 passed 4 of 4 and 3.2.8
passed 4 of 4 in the same loop, so it is not simply the environment either.

Three faults were found in the harness while chasing it, and each is fixed:

| Fault | Why it mattered |
|---|---|
| The settle could complete 2 seconds after the rotation | The output goes quiet within a second of the event, so the ordinary settle fired at 8 seconds — before the tail's next directory sweep had happened at all |
| The test mutated the **tracked** fixture tree in place | Two runs at once left the repository holding a fixture under the wrong name. It now works on a per-run copy |
| The rotation fired on a fixed 6-second sleep | A sleep cannot tell a collector that has read the file from one that has not started. The rotation now waits until the pre-rotation line has provably reached the sink |

**A single green run of a flaky test is not evidence, and presenting one as
evidence was the error.** Every claim about rotation in round 9 should be read
as one sample.

### What the deterministic test then found: a real 5.x regression

Once the rotation fired on a verified precondition instead of a sleep, the
result stopped being random and became a clean split:

| Fluent Bit | Runs | Append to the renamed file |
|---|---:|---|
| 3.2.8 | 3 of 3 | arrives |
| 4.0.1 | 3 of 3 | arrives |
| 4.0.14 | 3 of 3 (and 4 of 4 before the gate) | arrives |
| **5.0.8** | **5 of 5** | **lost, every time** |

**The earlier "passes" on 5.0.8 were the runs where the rotation happened before
the tail had read the file at all**, so the appended bytes were still pending and
arrived with everything else. Making the test correct turned an intermittent
failure into a certain one.

Two hypotheses were tested and both are wrong:

| Hypothesis | Test | Result |
|---|---|---|
| The default `Rotate_Wait` is too short on 5.x | set `rotate_wait: 30` on the input | still lost |
| inotify behaves differently on 5.x over this mount | set `inotify_watcher: false` | still lost, and 4.0.14 with the same setting still arrives |

So the one variable that changes the outcome is the version.

🔴 **Fluent Bit 5.0.8 loses bytes written to a file after it has been renamed
away.** That is what `logrotate` does to a long-running writer that keeps its
own descriptor, which is exactly how the orchestrator's log rotates.

**Production is not exposed today.** The farm runs 3.2.8, 4.0.1 and 4.0.14, and
none of them lose the bytes. **The soak rig defaults to 5.0.8**, which is the one
place this could be mistaken for normal behaviour.

The check is left failing on 5.0.8 on purpose. A version that loses data at
rotation must not be able to report green, so `replaycheck.py` prints what it is
and still exits non-zero.

The `rotate_wait: 30` added while testing the first hypothesis was **removed**
again. It fixes nothing measured, and shipping configuration that a measurement
did not justify is the habit this round exists to correct.

### The catalog: two more defects, and the machinery that closes both

**A template that generalises was becoming two documents.** Drain rewrites a
cluster's template as it sees more of the shape, and the document identifier is
a hash of that text. One pass over two messages gave one document with a count
of two; the same two messages split across two passes gave two documents with a
count of one each. Reproduced exactly as the reviewer described.

**A retry after a lost progress save doubled the count.** The bulk write and the
progress save were two operations with a window between them, and the window is
precisely the case that has to be retried.

Both are closed by the same two changes:

| Was | Now |
|---|---|
| the script ADDED a delta | the script SETS an absolute count — the cluster's whole size in this node's tree. An increment cannot be retried; a set can be repeated for ever |
| counts were one number per document | `counts_by_node`, so one node's contribution can be moved or removed without touching another's |
| no idea what a cluster had been published as | the identifier each cluster was last published under travels in the state file; when it moves, the old document is retired with `superseded_by` and this node's count is taken off it |
| a bulk write, then a separate progress save | the mining trees, the read position, the pass number and the published identifiers are **one file, replaced atomically**. drain3's own per-family persistence was replaced with an in-memory handler for exactly this reason |
| nothing stopped a stale pass | every write carries a pass number and the script refuses one it has already applied to this node's entry |

A third defect surfaced while testing the fix and would have been invisible in
production for a long time. The wipe detector asked the catalog for a document
count immediately after a `refresh=false` bulk write, so it read zero, concluded
the catalog had been wiped, threw the local state away and re-mined the whole
index — **every pass**. It now asks whether the index exists, which no refresh
interval can lie about.

### These are checked against published documents, not against the miner

The reviewer's closing point was that miner-only tests are insufficient, and it
is right: in all three cases the miner was correct and the documents were wrong.
`tools/collector/mappingcheck.py --catalog` now reads what the catalog actually
holds, after:

| Sequence | Assertion |
|---|---|
| a template generalises across two passes | one live document with a count of 2; the superseded one holds a count of 0 and points at it |
| a pass publishes, then its state is destroyed before it is saved, then it runs again | the total count is unchanged |
| three records share one millisecond, page size 2, three passes | the published count is 3 |

### The bulk test could not see missing records

Every figure it printed — severity recovered, unclassified, field extraction —
was computed over the records that ARRIVED. Two records in and one out reported
100 % severity recovery, 0 % unclassified, and exited zero.

It now computes how many records the corpus should produce, which is not how
many lines it holds: three of the four tailed families fold continuations, so
line count and record count are different numbers and comparing the wrong one
either passes when data was lost or fails when nothing was. The collector's own
health records are excluded, because they share the sink and are not part of the
corpus. A shortfall — or a duplicate — prints `INCOMPLETE` and exits non-zero.

`tools/collector/test_realcheck.py` holds the reviewer's reproduction as a test,
along with a duplicate, a health record used as a substitute for a lost one, and
the record-start arithmetic for each family.

### Re-verified, with the rotation stated honestly

| Check | Result |
|---|---|
| Fixture expectations, 3.2.8 / 4.0.1 / 4.0.14 / 5.0.8 | 35 of 35 on each |
| Restart, all four versions | passes on each |
| **Rotation, 3.2.8 / 4.0.1 / 4.0.14** | **passes, repeatedly** |
| **Rotation, 5.0.8** | **fails, repeatedly — the 5.x regression above** |
| Index mappings, OpenSearch 3.7.0 | 43 real collector records, 0 rejected, every field searchable |
| Catalog round trip | six families, every route read, counts add across nodes |
| **Catalog documents** | **generalisation, retry and page boundary all hold** |
| `template_catalog` unit tests | 29 of 29 |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 8 of 8, new |
| Ansible `--syntax-check`, 12 playbooks | all pass |
| Bulk, orchestrator | 189,803 records of 189,803 expected, 100 % severity, 0 % unclassified |
| Bulk, InfoLogger | 60,000 of 60,000 expected, real archive rows |

### What is still not proven

Unchanged from round 9, and none of it is closed by this round:

- **Template grouping correctness.** Cost, count, readability and contentless
  share are measured. Whether events that belong together are grouped together
  is not.
- **Minimum processor cost.** This host cannot resolve below about a fifth, so
  "no arm differs measurably" is the strongest statement available here.
- **Active-run source coverage.** No run has been active during any census.
- **Source-owner approval**, and **whether a collector goes on the storage
  node**. Both are decisions, not work.


---

## Round 11 — the third review, and two failures that survived round 10

Both reproduce, and one of them shows that the round 10 fix was sound but
incomplete rather than wrong.

### Absolute counts do not survive a retry that reads further

Round 10 made the catalog's counts absolute and fenced them with a pass number,
and argued that a retry therefore re-sends identical numbers. **That holds only
if the retry reads the same records.** It does not when new records have landed
in the meantime:

1. the pass publishes a count of 1 and the confirmation is lost;
2. a second record arrives;
3. the retry re-reads BOTH, so the batch is now a count of 2 under the same
   pass number;
4. the guard sees that pass already applied and refuses it;
5. the position is then saved past both records.

The second record is consumed and never counted. **Atomic local state does not
close a publication window** — it only makes the window's two ends consistent
with each other.

Reproduced against a real OpenSearch, both ways round: with the state file lost
entirely the same sequence over-counts (1 + 2 = 3, and a stale document is left
behind) rather than under-counting. Same root cause, opposite sign.

Three changes close it:

| Change | What it fixes |
|---|---|
| The batch is **written down before it is published**, and an unconfirmed batch is finished — from its own stored trees, reading nothing new — before any new record is read | the retry is the same batch, so the fenced counts are the right ones |
| The pass number is **milliseconds since the epoch**, not a counter | a counter restarts at 1 on a reprovisioned node, every stored guard is already higher, and the node is fenced out of its own catalog for ever |
| A node whose local state is empty while the catalog still names it **clears its own contributions first**, in its own pass | a rebuild cannot be added on top of counts the node can no longer identify |

Both sequences are now checked against published documents:

| Sequence | Assertion |
|---|---|
| batch published, commit fails, a new record arrives, two more passes | count 2, one live document |
| the whole state directory is lost, a new record arrives, two more passes | count 2 |

### Comparing totals is not a completeness check

Two different records in; the first shipped twice and the second lost. The
totals match, so round 10's check passed and reported full coverage of a corpus
it half lost. Exactly as the reviewer described.

The check now compares the **multiset of record contents**. The expected side is
derived from the rendered production cascade, with the tail's own multiline rule
applied first, so what is compared is what the collector should have produced
rather than the lines it was given.

Two things had to be right for that to be meaningful, and finding them was the
work:

- **Onigmo reads `(?m)` the way Ruby does** — the dot matches a newline — while
  Python reads it as "`^` and `$` match at line breaks". On a folded record the
  two disagree about where `(?<message>.*)$` ends. The derivation uses `(?s)`,
  which is the Python spelling of what Onigmo does.
- **A record read from a file keeps the newline that ended it**, and the corpus
  lines have already been stripped of theirs. Only that trailing newline is
  normalised; the newlines inside a folded record are content and are compared.

The derivation is validated by measurement, not by argument: on 189,803 real
orchestrator records and 60,000 real InfoLogger rows, the derived multiset and
the collector's output match **exactly, with nothing missing and nothing extra**.

**The new check immediately found a real fault in the harness.** One record of
189,803 was missing — the last line of the corpus, still held inside the tail's
multiline parser when the sink went quiet and the container was stopped. The
settle window is now longer than the flush timeout. A totals check could never
have found that, and had already reported the same corpus complete twice.

### The collector role installed the version that loses data

`fluent_bit_version` defaulted to **5.0.8** — the version round 10 showed loses
bytes appended to a file after `logrotate` renames it away. The farm workers pin
4.x in the inventory, so the statement that they are unaffected stands. But
**`epn-infra13` pins nothing**, and it is the node that would collect the one
source that rotates daily. The default is exactly what would have been used
there.

| | Was | Now |
|---|---|---|
| `fluent_bit_version` | `5.0.8` | `4.0.14` |
| `images/node/Dockerfile` | `fluent-bit=5.0.8` | `fluent-bit=4.0.14` |
| a guard | none | `collector_blocked_version_prefixes: ["5."]`, asserted **before** the package task, overridable only on purpose with `collector_allow_blocked_version` |

The guard was exercised rather than assumed: it rejects 5.0.8 and 5.1.0, accepts
3.2.8, 4.0.1 and 4.0.14, and the override lets 5.0.8 through.

Documenting the hazard in a comment and leaving the default pointing at it was
the same class of mistake as reporting one run of a flaky test.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 29 of 29 |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11, including the combined loss-and-duplication case |
| Catalog documents | generalisation, lost commit with a new record waiting, total state loss with a new record waiting, retry, page boundary |
| Bulk, orchestrator | 189,803 of 189,803, every record exactly once **by content** |
| Bulk, InfoLogger | 60,000 of 60,000, every record exactly once by content |
| Ansible `--syntax-check`, 12 playbooks | all pass |
| Version guard | rejects 5.0.8 and 5.1.0, accepts 3.2.8 / 4.0.1 / 4.0.14, override lets 5.0.8 through |
| Fixture expectations, restart and rotation on 3.2.8 / 4.0.1 / 4.0.14 | 35 of 35, and both checks pass, on each |
| Rotation on 5.0.8 | still fails, deliberately — the round 10 regression, and the role no longer installs that version |
| Index mappings, OpenSearch 3.7.0 | 43 real collector records, 0 rejected, every field searchable |
| Catalog round trip | six families, every route read, counts add across nodes, the position resumes |

One thing the fix broke and the end-to-end check caught: a pass with nothing new
to do now returned early and printed nothing. A oneshot that prints nothing is
indistinguishable from one that died, both in the journal and to anything
reading its output, so the idle pass says so explicitly.

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.


---

## Round 12 — one recovery case that a refresh interval hid

The pending-batch machinery from round 11 is sound, and one path around it was
not. A node that loses its local state inside the catalog's refresh window
rebuilds on top of counts it can no longer see.

### An acknowledged write is not a searchable one

The cleanup that runs after a state loss finds this node's previous
contributions with a **search**. The writes it has to find were made with
`refresh=false` — durable and acknowledged the moment the bulk returns, and not
searchable until the index refreshes. **The shipped catalog template sets that
interval to 30 seconds.**

So:

1. a pass publishes one record and does not refresh;
2. the local state is lost inside those 30 seconds;
3. a second record arrives that generalises the template;
4. the recovery runs, its cleanup search returns nothing, and the old
   contribution stands;
5. the rebuild is added on top of it.

Two source records, catalog counts of 1 and 2, total 3.

The fix is one line on a path that runs only after a state loss: refresh the
catalog before the cleanup reads it. Forcing that refresh changes the total from
3 to 2, which is the reviewer's own diagnosis and it reproduces exactly.

### The test was doing the service's work for it

This is the part worth recording. The sequence was already covered, and it
passed, because the assertion between the publish and the state loss called the
inspection helper — and **that helper refreshes the index**. The test was
supplying the very refresh whose absence was the defect.

Two changes, so it cannot happen again:

| Was | Now |
|---|---|
| the probe catalog index took the default one-second refresh | it is created with `refresh_interval: 60s`, longer than production's 30, so the window is real rather than a race the test usually won |
| an assertion sat between the publish and the state loss | nothing is inspected there at all, and the comment says why |
| the sequence asserted only the total | it also asserts how many documents claim a count, because 1 + 2 and 2 both total differently but only one of them is a stale document |

Verified in both directions: with the refresh removed the tightened test fails
on both assertions and names the stale document; with it in place both pass.

### The same question, asked of every other read

| Read | Refresh-bound? | Verdict |
|---|---|---|
| `catalog_is_missing` | no — it is a HEAD on the index | fine |
| `scan` over the log indices | yes | not a correctness problem. The position only advances over records actually returned, so a record not yet searchable is read on a later pass |
| the cleanup search | yes | **was the defect**; refreshes first now |

That is written into the role's README under what it does not do, rather than
left as a property nobody stated.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 29 of 29 |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11 |
| Catalog documents, six sequences | all pass, including state loss with no refresh |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| Records invisible to the scan | `unscannable: 0` on every index |
| Ansible `--syntax-check`, 12 playbooks | all pass |

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.


---

## Round 13 — the scan key was wrong, and I had written the wrong reason down

The reviewer found a source record being skipped, and the sentence I added to
the role README in round 12 is the thing that was wrong. It said a record
indexed but not yet searchable "is simply read on a later pass, because the
position only ever advances over records that were actually returned."

**That does not follow, and I should not have written it.** The position is a
sort key. A record that becomes searchable later can sort BEFORE it, and
`search_after` then excludes it for ever.

### Two ways it happens, neither exotic

| | |
|---|---|
| **Delayed visibility** | Two records share a `collector_time`. One is searchable, the other acknowledged and not yet refreshed. The pass reads the visible one and saves its position; the other's identifier sorts lower, so it is behind the position when it appears |
| **Delayed indexing** | A record reaches the index carrying an older `collector_time`. Fluent Bit buffers to disk and retries, so a chunk can be indexed minutes or hours after the time stamped on it |

Refreshing before each scan does not help the second one at all, as the reviewer
says: the record is indexed *after* the refresh, with a time that puts it behind.

### The fix is the ordering key, not the refresh

`collector_time` is when the collector saw the line. It carries no relationship
to when OpenSearch indexed it. `ingest_time` does — it is stamped by the
`alice-add-ingest-time` pipeline at index time, so **a record indexed later
always sorts later, whatever time it carries.**

Every index the catalog reads sets that pipeline as its `default_pipeline`: the
two storage-tier templates directly, and the node-local index through
`register_node.sh`. That was checked rather than assumed.

The second half is a cutoff. Nothing younger than `CATALOG_SCAN_SLACK_MS` — two
minutes, against a 30-second refresh interval — is read at all, so a pass cannot
read one member of a tie while the other is still unrefreshed. The catalog lags
real time by the slack, which on a ten-minute timer is nothing.

A document with no `ingest_time` would be invisible to that sort, so each pass
counts them and reports the number as `unscannable`. It should be zero, and
saying so out loud is cheaper than discovering later that it was not.

The saved position now means something different, so the state file carries a
version and a version 1 file is discarded rather than resumed from.

### Both cases are regression tests, and both were shown to fail first

| Sequence | With `collector_time` ordering | With `ingest_time` ordering |
|---|---|---|
| a record becomes searchable after its neighbour was read | count **1**, wanted 2 | count 2 |
| a record indexed late carrying an older collector time | count **1**, wanted 2 | count 2 |

Those are the reviewer's own numbers, reproduced against a real OpenSearch. The
tests were run against the old scan first to prove they have teeth, then against
the new one.

Four more unit tests cover the parts that do not need a cluster: the sort key,
the slack cutoff being larger than the refresh interval, the `_source` carrying
`ingest_time`, and a version 1 state file being discarded.

### The change immediately caught a second thing

Switching the scan to `ingest_time` made the existing round-trip check mine
nothing at all, and the reason is worth keeping: its probe records were written
straight into the index, so nothing stamped `ingest_time` on them. In production
the pipeline does. The check now sets it explicitly and asserts the new
`unscannable` counter is zero — which is exactly the signal that would have
named this immediately had it existed.

### What this says about the previous rounds

Three reviews in a row have found the same shape of mistake: a property asserted
in prose that the code did not have, with a test that passed for a reason other
than the one claimed. The catalog is now eight document-level sequences and 32
unit tests, and every one of the eight was demonstrated to fail against the code
it was written for.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 32 of 32 |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11 |
| Catalog documents, eleven sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| Records invisible to the scan | `unscannable: 0` on every index |
| Ansible `--syntax-check`, 12 playbooks | all pass |

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.


---

## Round 14 — the ordering key, third answer, and the first one that is assigned at indexing

The reviewer reproduced a missing record against the round 13 code and named the
reason exactly: `_ingest.timestamp` is stamped when an ingest node **receives**
a document. Processing, primary execution and replication all follow it. A write
held up while a later one completes therefore carries the *earlier* stamp, lands
behind the saved position, and is never read.

Widening the cutoff does not fix that. It only makes the failure harder to
reproduce, which is worse than leaving it visible.

### Three answers, and why the first two were the same mistake

| Key | What it actually records | Why it fails |
|---|---|---|
| `collector_time` | when the collector saw the line | a buffered or retried chunk keeps it and is indexed hours later |
| `ingest_time` | when an ingest node received the document | indexing follows it, and can follow it out of order |
| **`_seq_no`** | **the primary shard executing the write** | — |

The first two are both fields *on the record*, chosen before indexing. Nothing
chosen before indexing can order by indexing. That is the shape of the mistake,
made twice.

`_seq_no` is assigned by the primary as it executes the write, so a record
indexed later on a shard always has a higher sequence number whatever it says
about itself. **Verified against OpenSearch 3.7.0 rather than reasoned about:**
a document indexed second, whose `collector_time`, `ingest_time` and identifier
all sort before the first, comes back with the higher `_seq_no`.

### What that costs in implementation

`_seq_no` is per shard of a **concrete** index and every shard starts at zero,
so a cross-shard sort mixes unrelated sequences — confirmed by reading one back:
six documents over three shards returned sequence numbers 0, 0, 0, 1, 1, 1.

So each shard is read on its own with `preference=_shards:<n>`, and the position
is a sequence number per concrete index per shard. A rollover alias has several
backing indices and each has a shard 0.

The slack window is gone, and so is the two-minute lag it imposed. It existed
only to paper over the visibility race the sequence number removes. A shard copy
makes a contiguous prefix of its own history searchable, so an acknowledged and
unrefreshed record has not arrived yet rather than arriving behind the mark.

### The visibility test was not testing visibility

The reviewer is right about this too, and it is the more useful finding. The
sequence claimed to cover an acknowledged, unrefreshed record — and its write
helper passed `refresh=true`, so the record was searchable the instant it was
written. The condition never existed. Failing against the previous
implementation proved the previous ordering was wrong; it proved nothing about
this condition.

It now creates the condition for real: the probe index has automatic refresh
**disabled**, the record is written with `refresh=false`, and the pass over it
must report itself idle having read nothing. That assertion is what proves the
record was durable and invisible. Only then is the refresh asked for by name and
the record picked up.

The sequence that discriminates between ordering keys is the other one — a
record indexed second carrying earlier times and a lower identifier — and that
one was run against the round 13 code first, where it published a count of 1
against a wanted 2, together with the page-boundary sequence which also broke.

**I claimed the visibility sequence discriminated as well. It did not, and I
have removed the claim rather than leave it standing.** Its comment now says
what it establishes and points at the sequence that establishes the rest.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 33 of 33, including the shard pinning, the concrete-index keying and both older state versions being discarded |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11 |
| Catalog documents, eleven sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.


---

## Round 15 — the sequence number is not a boundary either

The reviewer reproduced a skipped record against the round 14 scan, with a
debugger holding one indexing thread, and the diagnosis is exact:
**`_seq_no` is assigned before `indexIntoLucene` completes.** Concurrent writes
can therefore become searchable out of order — number 1 visible while number 0
is still in flight. A scan that reads 1 and remembers it has stepped over 0 for
good. At the failing scan the shard reported `max_seq_no` 1 and
`local_checkpoint` −1.

### Four answers, one mistake, three times

| Tried | What it records | Why it fails |
|---|---|---|
| `collector_time` | when the collector saw the line | a retried chunk keeps it and is indexed hours later |
| `ingest_time` | when an ingest node *received* the document | indexing follows it, and can follow out of order |
| `_seq_no`, highest seen | the primary assigning the number | assigned before the write reaches Lucene |
| **`_seq_no` bounded by `global_checkpoint`** | **every operation at or below it has completed** | — |

The first three all trusted a value that marks something *earlier* than the
record becoming readable. `max_seq_no` is the highest number **assigned** and is
not a boundary at all; `global_checkpoint` is the number below which nothing can
still arrive.

### The order of the three calls is the argument

1. **read the checkpoint** — everything at or below it has completed;
2. **refresh** — which therefore makes all of that searchable;
3. **scan**, bounded at the checkpoint.

Refreshing first would let the checkpoint include operations the refresh did not
cover. Scanning past the checkpoint would read from the region still settling
and remember a position inside a gap. Anything above it is read by a later pass.

Where copies disagree, the lowest reported checkpoint is used: a replica may
report a staler value than the primary, and the smaller number is the safe one.

### Tested where the decision is made

The concurrent gap cannot be produced on a live cluster without pausing an
indexing thread, and the reviewer is right that sequential writes with reversed
timestamps cannot stand in for it. So it is tested at the boundary itself: the
scan is driven against a stubbed shard reporting `max_seq_no` 1, a
`global_checkpoint` of −1, and a searchable record at sequence number 1 —
exactly the state the reviewer's debugger produced.

| Assertion | |
|---|---|
| the settling shard is not read | the pass returns nothing **and issues no search at all** |
| the gap is read later | with the checkpoint at 1, both records come back in order |
| the order of calls | stats before refresh, refresh before search |
| disagreeing copies | the lowest checkpoint is the one used |

Removing the checkpoint bound fails five of those tests. That is what says they
are worth having.

### One behaviour changed, and a test had to be restated

The scan now refreshes the source index itself, so a record that is durable but
unrefreshed is picked up on the **next** pass rather than waiting for the
index's own refresh. The visibility sequence asserted the old behaviour — that
such a pass reads nothing — and now asserts the new one: the record is read on
the next pass, exactly once, and a further pass re-reads nothing.

That is a better property than the one it replaced, and it is stated here rather
than quietly absorbed.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 38 of 38 |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11 |
| Catalog documents, eleven sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.

## Round 16 — ten findings, and the one the file sink could never have caught

The seventh review widened past the scan and found ten defects. Nine of them
reproduce. All ten are fixed. The tenth — the missing directory synchronisation
— is a code-level gap the reviewer was explicit about not having reproduced, and
it is fixed on the same terms.

Three of them are the same mistake in three places: **a value that looks like a
boundary and is not**. A file sink that cannot lose an acknowledgement. A search
response that reports failure and is read as emptiness. An index name that is
reused. Round 15 was the fourth ordering key; this round is what the same
question looks like when it is asked of the output, of the response, and of the
index.

### 1. The output duplicated every record it retried

This is the finding the whole file-sink test rig was structurally unable to
produce, and it is worth being exact about why.

`replaycheck.py` writes to a file. A file sink has no acknowledgement to lose,
so it never retries, so a configuration that duplicates every record on retry
passes it green — 35 expectations, three Fluent Bit versions, all green, for a
property it never touched. The tests were not weak. They were about something
else, and I read them as though they were about this.

The fault is ordinary on a loaded cluster: a bulk request arrives, OpenSearch
**applies** it, and the 200 that says so does not get back. Fluent Bit cannot
distinguish that from a request that never arrived, so it retries the whole
chunk — correctly, because the alternative is losing it. Without a document
identifier of its own, every record in that chunk is indexed a second time under
a second generated identifier, and nothing downstream can tell the two apart.

The fix assigns the identifier **in a filter**, not in the output. Filters run
once, before the record reaches the buffer, so the retried chunk carries the
identifier the first attempt already used and the second write lands on the
document that is already there rather than beside it. The same holds across a
restart: a chunk recovered from the filesystem buffer was filtered before it was
written there.

It is a counter and not a hash of the record. Hashing the content collapses two
genuinely distinct lines that happen to be identical, which in a log that
repeats a message every second is most of them. The three parts are the node,
the process and the record: the node makes it unique across the workers sharing
the durable index, the process makes it unique across restarts — a kernel UUID
rather than a clock, because two starts inside one second is exactly what a
crash loop does — and the counter makes it unique within the process.

`tools/collector/retrycheck.py` is the check that was missing. It puts a proxy
in front of OpenSearch that forwards a bulk request, waits for the whole
upstream response so the write is definitely committed, and then tells the
client the request failed.

| | operations applied | records attempted twice | documents |
|---|---:|---:|---:|
| 4.0.1, with the identifier | 43 | 1 | **43** |
| 4.0.1, control, identifier removed | 46 | 3 | **46** |
| 4.0.14, with the identifier | 43 | 10 | **43** |
| 4.0.14, control, identifier removed | 55 | 12 | **55** |

The output writes with `create`, so the second attempt at an identifier the
cluster already holds comes back **409 and is refused** rather than overwritten.
Either answer deduplicates; this is the one that happens, and it is the stricter
of the two, because it can never replace a document that is already correct.
Without the identifier the same second attempt is a new document, which is the
control's whole margin.

Both halves are needed. The first is the property. The second is the proof it is
the identifier and not luck — a test that only ever passes proves nothing about
what it is testing.

**The harness was wrong twice before it was right, and both mistakes were mine
rather than the reviewer's.**

The first version counted operations out of the REQUEST. That number was
inflated by roughly a factor of two, because a body forwarded on a connection
Fluent Bit had already given up on, or one whose upstream socket was stale,
never reached Lucene at all. Counting out of the RESPONSE — pairing each result
with the operation that asked for it — is the only figure that means *applied*.

The second version waited for the document count to go quiet. That is not the
same as waiting for the fault: Fluent Bit backs its retries off, so the quiet
arrives first, and one arm reported "no duplication" from a run that ended
before the retried chunk did. It now waits for a **record attempted twice**,
measured from the record's own `doc_id` in the request body — which exists in
both arms, because the control removes only `id_key` — and fails with a plain
message if that never happens inside the window. A re-delivered *chunk* is not
enough: a chunk can be re-sent because the first attempt never reached the
cluster, which is a different event and not the one under test.

The third version changed how the fault is injected. Silently closing the
connection is the more literal *lost response*, and it is what the first two
versions did — but across repeated runs Fluent Bit retried a silently closed
connection inside a 150-second window in most of them and not all, and a harness
that injects its fault only sometimes is not a harness. It now forwards the
request, waits for the upstream response so the write is committed, and answers
**503**. From the client's side that is the same event — the cluster holds the
records and the client has been told it does not — and it is retried every time.

Three attempts to get an honest measurement out of one property is worth
recording plainly. Two of the three would have printed green.

Each of the three catalog sequences added this round was also run against the
code without its fix, and each one failed there:

| Fix removed | What the sequence said |
|---|---|
| the retirement guard | `one node generalising left 2 counts visible on current templates, wanted 3` |
| accumulated programs | `a generalised template names ['StfSender'], wanted both programs that wrote it` |
| the catalog UUID check | `a catalog recreated under the same name holds 0 after the next pass, wanted 1` |

The same was done for the fixture assertion: removing the identifier from the
Lua filter fails `replaycheck` on six rows across two outputs, without a cluster
being involved at all.

The InfoLogger index earns its own note. Its mapping is `dynamic: "strict"`, and
the output leaves the identifier in the document body as well as using it as the
`_id`, so an unmapped `doc_id` rejects every InfoLogger record outright. That is
why `retrycheck` feeds the TCP input as well as the files, and why `doc_id` is
in both mappings rather than only the permissive one. `expect.yaml` also
requires it on every record now, so a change that drops it fails on the fixtures
without needing a cluster at all.

### 2. A partial answer was read as an empty one

OpenSearch reports a timed-out search, and a search whose shard failed, with
**200 OK** and whatever hits it managed to collect. An empty page from a failed
shard is byte-identical to an empty page from a shard with nothing left in it.
Only the flag beside the hits separates *there is no more* from *I could not
tell you*, and the scan was not reading it — nor the refresh's shard counts.

The scan now rejects both. The two rejections are deliberately not equally
severe:

* a refresh that did not reach every shard **skips that index for the pass**. A
  shard that did not refresh may hold completed operations below the checkpoint
  that are still not searchable, so reading now steps over them.
* a search that timed out or lost a shard **stops that shard**, keeping what was
  already yielded, which is contiguous from the low end and correct.
* a partial listing during recovery **aborts the run**. That asymmetry is the
  point: a partial answer during a scan costs a delay, but a partial answer while
  listing this node's own catalog documents means some were not listed, so they
  are not cleared, and the rebuild lands on top of counts that were never
  removed.

### 3. An index name is not an index

Two versions of the same mistake.

Delete a source index and recreate it under the same name, and its sequence
numbers start again at zero while the saved position still says shard 0 was read
to some large number. Every record in the new index is below the mark and is
skipped for good. The position is now keyed by concrete name **and index UUID**,
so a recreated index is a place this node has never read — which is what it is.

Delete the *catalog* and recreate it under the same name and the failure is
quieter still. The name resolves, so the old check saw nothing wrong. Every
document this node published is gone, but local state says they were published,
so nothing is republished; and the source position has not moved, so nothing new
is read. The node reports itself **idle against an empty catalog** for as long
as it runs. The identity is now the catalog's UUID.

Two weaker tests were already recorded as rejected — a document count, read
through the refresh interval, and a bookkeeping document, indistinguishable from
a failed write. The resolving name was the third and it fails in the opposite
direction to the first: the count said *wiped* when it was not, the name says
*present* when it is not.

### 4. A clock that went backwards silently stopped every count

Pass numbers were milliseconds since the epoch. That was chosen so they survive
the loss of local state — a plain counter restarts at one on a reprovisioned
node, every guard already stored in the catalog is higher, and the node is
fenced out of its own documents for good.

It is necessary and it is not sufficient. Every update refuses a pass at or
below the one already recorded for the node. A clock stepped backwards — NTP
correcting, a virtual machine restored from a snapshot — produces a pass below
that guard, so every update takes the `noop` branch, the records are consumed
and the position is committed, no count moves, and **the run reports success**.

The clock is now a floor and never the whole answer. The number is the larger of
the clock and one above what this node is known to have written: from local
state, or, when that is gone, from the `last_pass` the catalog itself records
while the node's old contribution is being cleared. That last part also fixes a
second-order case — the clear itself used a clock-derived pass, so a rolled-back
clock made the clear a no-op and the rebuild landed on counts that were never
removed.

### 5 and 6. The tree serialised itself once per record, and had no size bound

These compound, so they are one measurement.

drain3 reads a persistence handler as an instruction to snapshot, and its rule
fires on **every** message: a new cluster or a changed template snapshots by
definition, and the periodic rule compares the seconds since the last save
against `snapshot_interval_minutes * 60`, which for the interval this recipe
sets is a comparison against zero that is always true. Twenty-five records
produced twenty-five full jsonpickle serialisations of the whole tree.

Measured on this host, that is not merely slow — it is what decides the memory
ceiling:

| clusters | tree in memory | peak while serialising | serialised size |
|---:|---:|---:|---:|
| 0 | — | 26.5 MB | — |
| 5,000 | — | 76.8 MB | 0.29 MB |
| 20,000 | 61.5 MB | **208.1 MB** | 1.10 MB |
| 50,000 | 112.6 MB | **437.2 MB** | 2.71 MB |
| 100,000 | — | 856.9 MB | 5.36 MB |

The tree costs 1.75 KB a cluster. Serialising it costs another 6.5 KB a cluster,
transiently, because jsonpickle builds the whole string in memory. The unit
allows 512 MB.

The handler is now attached only long enough to load and is reattached for
exactly one save at the batch boundary — which is also the only correct moment,
since a tree made durable at any other instant does not match the read position
saved beside it.

And the growth bound is now stated rather than assumed. Depth and max_children
bound the **shape** of the tree; neither caps how many clusters it holds. The
ceiling is 20,000 templates across all families: 208 MB peak against 512 MB, and
4.7 times the 4,221 templates the whole 55,963,050-line archive pass produced.

drain3's own `max_clusters` is an LRU that **evicts**, and eviction cannot be
used here. The count published for a template is the cluster's absolute size in
this tree, so an evicted cluster later rebuilt would report a size of one and
SET the catalog count back to one. At the ceiling the worker therefore stops
**learning** and keeps **counting**: a line that matches an existing template
still increments it, a line that matches nothing is counted as `unlearned` in
the pass summary. Reaching the ceiling is visible in the journal rather than
silent.

### 7. One busy shard could starve every shard behind it

Every pass started at shard 0 and spent one shared budget. A shard busy enough
to fill the budget on its own is read every pass and the shards behind it are
never read at all, for as long as it stays busy. Each shard now has its own
budget and the starting shard rotates between passes.

### 8. Retirement is a property of the document, not of the node that retires

Two nodes publish the same template. One generalises, so for that node the text
has moved and its count is removed from the old document — correctly. But
`superseded_by` was set at the same time, and `superseded_by` belongs to the
document. Every reader that filters superseded documents out — which is the
entire point of the field — then stopped counting the other node, which is still
actively contributing to that text. The reviewer's reproduction returned two
counts where three were owed.

It is now set only when the last contribution has gone, and cleared again if one
comes back.

### 9. A generalised template was named after the wrong ten minutes

When the text moves, the count moves to a new document, and the programs have to
move with it. The update sent only the programs seen in **this** batch, so the
new document was named after whichever program happened to write in the ten
minutes the move fell in and lost every earlier one.

Program names are a fact about the cluster, not about the batch, so they now
accumulate in the state file beside the tree and travel with the batch that
publishes them. A recovery replays the batch's own copy, so both paths agree.

### 10. The rename was not durable

The state file was synchronised and then renamed. The rename is what publishes
it, a rename is a directory write, and the directory was never synchronised — so
the bytes were on the platter while the rename that pointed at them was still in
write-back cache. A power cut between the two restores the old state file, and
the batch it describes is read and counted a second time. One `fsync` on the
directory, and a test that watches which inodes are synchronised.

### The README claim that was still wrong

The recovery section still described the position as `collector_time` plus a
document identifier resumed with `search_after` — the **first** of the four
ordering keys, three replacements out of date. It now carries the table of all
four and why each of the first three fails, which is more useful than the
correction alone: they fail the same way, and the pattern is the finding.

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 59 of 59 (was 38) |
| `replay.py` unit tests | 20 of 20 |
| `realcheck` unit tests | 11 of 11 |
| `retrycheck`, 4.0.1 and 4.0.14, both arms | 3 repeats, 12 arms, no failure |
| `replaycheck`, 3.2.8 / 4.0.1 / 4.0.14, rotation and restart | 35 of 35, all three |
| Catalog documents, eleven sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| Ansible, 12 playbooks | syntax ok |

One thing this round does NOT change: `images/node/fluent-bit/collector.yaml`,
the bundled local rig, carries neither the identifier nor the Lua filter it
hangs off. That file is the local recreation and not the production source of
truth, and it does not carry the two-clock filter either. It is stated here
rather than left to be discovered.

Twenty-one new unit tests, each written against the reported condition rather
than around it: a settling shard, a timed-out search, a failed refresh, a
recreated source index and a recreated catalog, a rolled-back clock, a save that
must happen once and not twice, a ceiling that counts without learning, a busy
shard, a directory that must be synchronised, and a generalisation that must
keep its programs.

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.

## Round 17 — the fix that only covered the sources the test could reach

Eight findings: six in the shipped code, two in the harness that measured it.
All eight reproduce and all eight are fixed. Two of them are the previous
round's fixes not reaching far enough, which is worth naming as a pattern rather
than as two accidents: a correct mechanism applied to the paths the test
happened to exercise.

### 1. The journal was the one source that lost its identifier

Round 16 gave every record a document identifier and proved a retry no longer
duplicates. It proved it over the fixtures, and no fixture is a journal entry.

The journal filter chain ends in an **allowlist** — twenty-five fields arrive
and the corpus holds 234 distinct ones, so everything unnamed is dropped rather
than paid for on the durable tier and left unsearchable. An allowlist is a deny
list for everything not on it, and `doc_id` is not a field an operator reads, so
it is exactly the kind of thing that gets left off one. It was. Journal records
reached OpenSearch with no identifier at all, and a retried chunk wrote every
one of them twice.

The fix is one line on that list. What took longer was making the test able to
see it, and there are now two answers, because they fail differently.

**Statically**, `retrycheck` renders the production configuration and asserts
that *every* allowlist in it keeps the identifier. That runs everywhere, needs
no cluster, and generalises: the next allowlist added to this pipeline is
checked the day it is added.

**Dynamically**, `retrycheck` now runs a journal arm over a real captured
journal — one file lifted out of the container runtime's own virtual machine,
with the fixture tree mounted empty so every record in the run came through the
journal filters.

| fluent-bit 4.0.14, journal | operations applied | documents |
|---|---:|---:|
| with the identifier | 112 | **112** |
| control, identifier removed | 259 | **259** |

Removing the one line from the allowlist and running it again: *259 of 259
journal operations carried no identifier*. That is the reviewer's reproduction,
in the harness, against the shipped configuration.

The first version of the static check was itself wrong and would have caught
nothing. It only noticed a list that ended at a blank line, and the shipped
configuration ends this one with the next filter. It is now tested against both
endings, a list at the end of the file, and a list holding a key literally
called `name` — in `tools/collector/test_retrycheck.py`, which needs no
containers.

### 2. Recovery checked the listing and not the refresh that feeds it

Round 16 made the source scan reject a refresh that failed on a shard, and made
the recovery listing reject a partial search. It did not check the refresh
*before* that listing.

The listing is a search, so it sees only what a refresh has made searchable. A
refresh that failed on a shard leaves this node's own contribution there
invisible, the listing finds less than there is, and the rebuild lands on top of
counts that were never cleared. Checking the listing and not the refresh that
feeds it is checking the second half of one act.

### 3. A crash after the clear lost the number the clear had used

This one is worth setting out in full, because every part of it is a mechanism
that was added on purpose and the failure is in how they meet.

`CLEAR` removes this node from the document's `nodes` list. The listing that
finds documents to clear filters on `nodes`. So **once the clear has run, the
listing can no longer find what it cleared** — which is correct, and is what
makes it idempotent.

The pass number the clear used was derived from those same documents: the
highest `last_pass` this node had written, so the clear could not be fenced out
by its own earlier guard. It was held in memory.

A crash between the clear and the first commit therefore leaves a node that
cannot rediscover that number. It restarts, lists, finds nothing, falls back to
the clock — and after a rollback the clock is *lower* than the guard the clear
itself just wrote. Every update in the rebuild takes the `noop` branch, the
records are consumed and committed, the count stays at zero, and the run reports
success.

The fix is the write-ahead the batch already had: the pass number becomes
durable in `issued` before anything is written with it, and the floor is the
higher of `pass` and `issued`. Ordinary clock rollback was round 16's finding;
this is the same failure reached by a road that goes around the fix.

### 4. The publication that creates the catalog recorded no catalog

The bulk write creates the index when it is not there, so on a node that has
never published, the identity only exists **after** the write. Round 16 read it
before, got nothing, and stored null.

Null then compares against the new identity as *unchanged*, and the check that
exists to notice a replaced catalog notices nothing — at exactly the moment it
is most likely to matter, because a catalog that was just created is a catalog
somebody is still setting up. The identity is now read once, after the first
successful publication, and stored with it.

### 5. A replaced catalog was rebuilt from the wrong thing

Round 16 detected the replacement and threw the local state away, sending the
node back to the source indices to re-mine.

Those are the short-lived half of this system. Informational records age out of
the node-local index long before the templates mined from them stop being true —
that is the whole reason the catalog exists. Re-reading them publishes only what
has not aged out yet, and the rest is gone for good.

The mining trees hold every count this node has published, and they survived. So
a replacement is now a **rebuild and not a reset**: trees, read position and
accumulated programs are kept, `published` is cleared because those documents no
longer exist, and the pass republishes every cluster the trees hold rather than
only what it read. The tree is the record of what was seen; the catalog is only
where it was sent.

Tested where it bites: publish two records, delete one of them from the source
index, replace the catalog, run. The count comes back as two. Without the fix it
comes back as zero.

### 6. An unassigned replica hid a healthy primary

A refresh reports `total` as the number of configured shard **copies**. One node
with `number_of_replicas: 2` — the shipped worker layout — has two copies that
can never be assigned, and every rolling restart produces the same state for a
while. A completely healthy refresh there says two total, one successful, zero
failed.

Round 16 read `successful < total` as a fault. The index was skipped every pass,
for as long as the replica stayed unassigned, and the service reported itself
idle while the primary held records nobody was reading. It is the strictness
added last round, applied to a number that does not mean what it looked like.

`failed` is the field that means something went wrong. An unavailable copy is
not a failed operation, and the copy that answered is the primary. A **search**
is different and keeps the arithmetic: its `total` counts shards, not copies, so
every shard must be accounted for as successful, skipped or failed.

The single-node cluster the checks run on is exactly this state, so the sequence
that tests it needed nothing but an index with a replica.

### 7 and 8. Two defects in the thing doing the measuring

The proxy wrote its counters through one shared temporary filename, from
multiple threads, outside the lock. Two concurrent writes reproduced a
`FileNotFoundError` inside a request handler — which loses that response and
stalls the very retry the run is waiting for. A harness that can break the thing
it is measuring is not measuring it. Snapshot and publish are now one critical
section, with a per-thread temporary name.

The check compared the documents in OpenSearch against the identifiers it had
watched go past in the proxy. A record lost before the proxy ever saw it is
missing from both sides at once and cancels out; feeding the checker a single
fixture record produced no failure at all. There is now an **independent
expectation**: the same fixtures through the same rendered configuration with a
file sink, produced without a cluster, without a proxy and without a retry. Both
arms are compared against it per destination — the identified arm exactly, the
control allowed to hold more but never less.

Per destination and not in total, because a misrouted record is neither lost nor
duplicated and a total would not notice it. That case is in
`test_retrycheck.py`, and it asserts that the totals match while the comparison
still fails.

**And one of mine, found while running the above.** The static allowlist failure
was appended to the failure list and never printed: the run exited non-zero
saying nothing about why. A failure that is counted and not shown is a failure
nobody acts on.

### Every fix run against its own absence

| Fix removed | What the check said |
|---|---|
| `doc_id` from the journal allowlist | `259 of 259 journal operations carried no identifier` |
| the retirement guard | `one node generalising left 2 counts visible on current templates, wanted 3` |
| accumulated programs | `a generalised template names ['StfSender'], wanted both` |
| the catalog UUID check | `a catalog recreated under the same name holds 0 after the next pass, wanted 1` |
| recording the created catalog's identity | `a catalog created by the first publication and then replaced holds 0, wanted 1` |
| rebuilding from the trees | `after a catalog replacement the count came back as 0, wanted 2` |
| treating an unavailable copy as healthy | `a shard with an unassigned replica published 0, wanted 1` |

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 68 of 68 (was 59) |
| `retrycheck` unit tests | 12 of 12 (new) |
| `replay.py` / `realcheck` unit tests | 20 of 20, 11 of 11 |
| `retrycheck`, 4.0.1 and 4.0.14, fixtures and journal | 8 arms, no failure |
| `replaycheck`, 3.2.8 / 4.0.1 / 4.0.14, rotation and restart | 35 of 35, all three |
| Catalog documents, thirteen sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| Ansible, 12 playbooks | syntax ok |

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.

---

## Round 18 — the rest of the pipeline, on all seven families

Round 6 rewrote the masker and verified it on three families. Since then the
process tree was split into `dpl` and `datadist`, three sources were added,
an envelope strip was put in front of the masker for five families, and the
masker itself grew a `TS` rule and a unit lookahead out of the hand audit.
The masker was no longer the most expensive step, and nothing after it had
ever been priced. This round prices every step of `recipe_prepare` and
drain3's own per-line path, on every line of all seven families, under
round 6's gate: byte-identical output, or rejected.

**The whole pipeline is 31 to 57 % cheaper per family and every template is
the same.** The masker was left alone: six rewrites of its number stage all
land within the round-to-round spread of the host, and none wins on every
family.

### The figure of record

Whole pipeline, every line of every family, four arms interleaved inside one
process, two rounds, minimum, processor time. Python 3.9.25 and drain3
0.9.11, which is what `/usr/bin/python3 -m venv` plus `pip install drain3`
gives an Alma 9 worker. Core-seconds per million lines.

| Family | Lines | Shipped | Strip + pad + loops | + token path | **+ early exit** | Change | Templates |
|---|---:|---:|---:|---:|---:|---:|---:|
| `infologger` | 3,000,000 | 22.29 | 11.13 | 9.71 | **9.55** | **−57.2 %** | 675 = 675 |
| `dpl` | 3,000,000 | 12.98 | 9.58 | 8.58 | **8.59** | −33.8 % | 920 = 920 |
| `datadist` | 92,813 | 13.48 | 10.48 | 9.61 | **9.36** | −30.6 % | 191 = 191 |
| `dds` | 1,236,971 | 47.31 | 25.17 | 22.63 | **22.41** | **−52.6 %** | 1,570 = 1,570 |
| `ildaemon` | 163,670 | 7.65 | 5.94 | 5.06 | **5.00** | −34.6 % | 18 = 18 |
| `journald` | 277,541 | 16.84 | 10.67 | 9.15 | **8.68** | −48.5 % | 883 = 883 |
| `odc` | 190,250 | 23.56 | 12.55 | 11.15 | **10.93** | **−53.6 %** | 266 = 266 |

The three candidate columns are cumulative. The first changes the strip, the
pad and drain3's two per-token loops and still goes through
`TemplateMiner.add_log_message`; the second hands drain a token list and
calls the Drain directly; the third adds one early exit to the best-match
loop. The shipped arm reproduces every template count of the audit round,
883 for `journald` included, so the harness is on the same footing as the
masker it measures.

🔴 **Read the ratios, not the absolute values.** The same shipped code on the
same lines read 12.61 core-seconds a million on `infologger` at midday and
22.29 in the evening, with the load average at 2.3, no thermal event
recorded and 88 % of memory free. The host was 1.7 times slower for a reason
the instruments on it could not name. Every arm above ran inside one process
in alternation, so each ratio was taken on the same host state; the absolute
column is the evening's.

### Where the time went, step by step

300,000 lines per family (every line of the smaller ones), five interleaved
rounds, minimum, Python 3.9, the audit round's masker. A family without a
strip rule or a pad set has no row for it. The token-path column ends at the
token list instead of the joined string.

| Family | strip | mask | pad | prepare | tree | full |
|---|---:|---:|---:|---:|---:|---:|
| `infologger` | — | 4.33 | 9.44 → **0.94** | 13.96 → **5.61** | 9.00 → **4.55** | 24.72 → **10.92** |
| `dpl` | 1.65 → **0.64** | 3.26 | — | 5.08 → **4.32** | 6.49 → **3.44** | 12.05 → **8.28** |
| `datadist` | 1.26 → **0.38** | 4.31 | — | 5.65 → **5.24** | 6.40 → **3.46** | 13.41 → **9.44** |
| `dds` | 4.68 → **0.52** | 11.90 | 13.33 → **1.55** | 30.72 → **14.23** | 11.71 → **5.94** | 44.09 → **20.48** |
| `ildaemon` | 0.42 → **0.38** | 1.38 | — | 2.04 → **2.25** | 4.92 → **2.57** | 7.13 → **4.67** |
| `journald` | — | 3.85 | 4.22 → **0.43** | 8.46 → **4.23** | 6.85 → **3.35** | 16.70 → **8.70** |
| `odc` | 1.44 → **0.76** | 4.21 | 7.21 → **0.78** | 14.20 → **6.69** | 7.37 → **3.77** | 22.81 → **10.64** |

**The pad step was the most expensive single step in every padded family** —
more than the masker round 6 rewrote and more than the tree — and the rewrite
takes 86 to 90 % off it. The strip on Python 3.9 was the next surprise: `sub`
on an anchored pattern walks the whole line after its one match, failing `^`
at every position, which is 2 core-seconds a million on a 387-character `dds`
line. The tree itself halves once its two per-token Python loops become
builtins and the wrapper is bypassed.

### The four mechanisms, each a local identity

1. **The strip uses `match()` and a slice instead of `sub("")`.** Every strip
   pattern is anchored with `^` and none can match the empty string, so `sub`
   can only ever replace one match, at position 0, and `match` finds the same
   one. Python 3.11 gives up on an anchored search after the first failure
   and the gap there is 6 to 14 %; 3.9 does not, and production is 3.9.
2. **The pad step uses `str.replace` and `split()`.** The shipped form built
   the pattern string and called `re.escape` on every line, went through the
   module-level cache, and expanded a `\1` template, which on 3.9 to 3.11 is
   a Python callback per separator matched. The whitespace collapse it did
   with `\s+` and `strip()` is what `split()` does anyway: `\s` in a str
   pattern, `split()` with no separator and `strip()` with no argument all
   use the same whitespace predicate, and the string consumers get
   `" ".join(tokens)`, which is the same string.
3. **drain3's two per-token loops become builtins.** `get_seq_distance`
   counts wildcard slots with `tuple.count` and equal slots with
   `sum(map(eq, ...))`. The reference skips a slot whose template token is
   `<*>` even when the log token is also `<*>`, so those are subtracted back
   out, and only when the log line carries one at all. The FLOAT/NUM merge
   finds the slots that differ with `compress(range, map(ne, ...))` and
   visits only those. Same integers, same float, same tie-break, same
   cluster.
4. **The miner takes the token list and is called at the Drain.**
   `TemplateMiner.add_log_message` masks a line the recipe has already
   masked, splits the string the recipe just joined, makes six profiler
   calls, joins the template and builds a result dict, for every line; and
   for every matched line drain3 builds a new template list, tuples it and
   compares it with the old one even when no slot differed. `add_tokens` does
   the same search and the same creation and rebuilds the template only when
   a slot differs. The tuple comparison stays for the rebuilt case, because
   the merge can rebuild a slot to the value it already had.

**And one early exit.** With `include_params` off, a candidate whose every
slot equals the line and whose template holds no wildcard scores 1.0 with
zero parameters. No later candidate can beat 1.0; a tie needs more
parameters, which a 1.0 score with parameters uncounted rules out; and a tie
on both goes to list order in the reference, which returning at once
honours. A line that repeats a static message stops at its first candidate
instead of scoring the rest of the leaf. It is worth 0 to 3 points on top of
the token path, and it cannot cost anything but one comparison.

### Why it is byte-identical, and how that was checked

| Check | Lines | Result |
|---|---:|---|
| candidate prepare against shipped `recipe_prepare`, every line of every family, escaped corpus form | 8,961,245 | **0 differences** |
| the same on the unescaped form, real tabs and newlines in the message, as production hands it over | every line carrying one | **0 differences** |
| the audit round's masker against drain3's reference masker, raw and stripped line, on Python 3.9 | 8,961,245 | **0 differences** |
| the cluster every single line landed in, shipped path against each of the three candidates | 8,961,245 × 3 | **identical** |
| final template set and each template's line count, per family, per candidate | 7 × 3 | **identical** |
| the landed code in `tools/templating` against the shipped-arm record, every line | 8,961,245 | **identical** |
| the landed code across a save-and-reload of the tree at the midpoint, `dpl`, `dds`, `odc`, `journald` | 790,250 | **identical** |
| differential fuzz of the pad rewrite, Unicode whitespace alphabet, 3.9 and 3.11 | 800,000 × 3 pad sets | **0 differences** |
| differential fuzz of the strip rewrite, envelope alphabet, 3.9 and 3.11 | 800,000 × 6 rules | **0 differences** |

The per-line cluster check is the strong one. Two miners can end with the
same template set and still have put individual lines in different clusters
along the way; comparing the cluster identifier of every line, in order,
rules that out.

### The masker's number stage is at its floor

The audit round's masker costs 15 to 21 % more than round 6's, all of it in
the FLOAT and NUM stage, which is the one stage no gate can skip because 89
to 100 % of lines carry a digit. Six rewrites of that stage were built, all
verified byte-identical against drain3's reference on every line of every
family, raw and stripped, and on 1,000,000 fuzzed strings over an alphabet
of units, clocks and glued digits:

| Rewrite | What it changes |
|---|---|
| `loop` | the shipped fold, the unit looked for only when the token ends in a letter, the parts walked by `zip` over two slices |
| `loopc` | `loop` with the thirteen-literal unit alternation as three character-class branches |
| `fold3` | the unit captured as its own split group, so the loop reads it instead of stripping it off |
| `fold3c` | `fold3` with the class branches |
| `two` | no fold: a FLOAT pass then a NUM pass, both C-level `sub`, the reference's own structure with literal-first patterns |
| `twoc` | `two` with the class branches |

Change against the shipped masker, 200,000 lines per family, five interleaved
rounds, minimum, two separate runs:

| Family | `loop` | `loopc` | `fold3` | `fold3c` | `two` | `twoc` |
|---|---:|---:|---:|---:|---:|---:|
| `infologger` | +1.5 % | −4.3 % | −0.2 / −4.1 % | −7.5 / −8.4 % | +0.9 / +12.5 % | +2.7 / +8.0 % |
| `dpl` | −1.7 % | −2.1 % | +2.5 / −11.8 % | −2.8 / −12.7 % | +0.5 / −9.1 % | −9.6 / −12.7 % |
| `datadist` | −1.6 % | +1.2 % | +5.8 / +12.3 % | −3.0 / +17.9 % | +15.9 / +29.0 % | +6.5 / +23.7 % |
| `dds` | −2.9 % | −8.5 % | −3.1 / −6.7 % | −4.3 / −2.9 % | +17.8 / +14.2 % | +15.3 / +13.8 % |
| `ildaemon` | −4.3 % | −1.8 % | +0.3 / +9.6 % | +4.5 / +8.6 % | +4.9 / +8.0 % | +7.8 / +8.6 % |
| `journald` | −10.9 % | −9.5 % | −6.0 / −2.0 % | −4.1 / −3.5 % | −2.2 / +1.4 % | −7.3 / +0.5 % |
| `odc` | −1.1 % | +0.6 % | +1.8 / +3.6 % | −0.6 / −2.4 % | −2.1 / +4.7 % | −3.8 / −1.9 % |

**No rewrite wins on every family, and the ones that win anywhere win by
less than the spread between two runs of the same code.** The two-pass form
loses 14 to 29 % on the number-dense families, which settles a question
round 6 left open: the Python loop is cheaper than a second C scan that
stops at every digit looking for a dot. The class-branch unit and the
cheaper loop are each worth a few per cent where they are worth anything.
The masker ships unchanged. What this round adds to it is the verification
on the production interpreter: the audit round checked it on the same
corpora on a newer Python, and 3.9 agrees on every line.

### What shipped, and what did not

`tools/templating/drainbench.py`:

- `recipe_tokens(family, message)` is the new entry point and returns the
  token list. `recipe_prepare` keeps its name and its string for the callers
  that want text — the catalog's ceiling path, `manifest.py`, the tests —
  and now produces it the same way.
- `mine(tm, tokens)` adds one prepared line to a recipe miner and returns
  its cluster, straight to the Drain.
- `install_merged_create_template()` now installs four methods: the merge,
  the similarity, the best match and `add_tokens`. `restore_plain_drain()`
  puts the first three back, and `recipe_run` uses it on the way out.
- `recipe_run` mines through the two new functions and reads the retention
  figure off the cluster instead of the result dict.

`tools/templating/masking.py` is unchanged. The catalog service is unchanged
too, and it does not need to change to gain most of this: it calls
`install_merged_create_template()` and `recipe_prepare`, so it gets the
strip, the pad and the drain loops as they stand. What it still pays is the
wrapper and the second split, which is the difference between the first and
the last candidate column above; switching its one mining line to
`recipe_tokens` and `mine` collects that, and is left to the round that owns
that file.

🔴 **These are rewrites of drain3 0.9.11's methods.** They return what that
version's methods return, verified line by line, and a future drain3 that
changes `fast_match` or `add_log_message` would not be tracked by them. The
catalog role installs whatever `pip install drain3` resolves to; it should
pin 0.9.11.

### Re-verified

| Check | Result |
|---|---|
| landed code against the shipped-arm record, every line of every family | identical clusters, identical templates |
| landed code across a persisted and reloaded tree, four families | identical |
| `template_catalog` unit tests, Python 3.9 | 68 of 68, and 68 of 68 on 3.11 |
| fuzz, pad and strip, both interpreters | 0 differences |
| Python 3.11 cross-check of the tree arms, three rounds | same ranking, tree arms −40 to −52 %, whole pipeline −13 to −55 % |

### Limits

- One host, a laptop, and one whose absolute speed moved 1.7 times between
  two runs of identical code. The ratios are sound; the absolute figures are
  the evening's and should not be quoted against round 6's.
- Python 3.9 is the figure of record because it is the farm's; the
  `python:3.9-slim` container round 6's method calls for could not be pulled
  from the Colima VM, so this round is native.
- The corpora are rounds 7 to 9's: `dpl` capped at 3,000,000 archive lines,
  the rest complete. Nothing here changes any recipe or any template.

## Round 19 — the fix that stopped at the first caller

Four findings: two in the shipped code, two in the harness that measures it.
All four reproduce and all four are fixed.

Three of them share a shape, and it is not round 17's shape. Round 17 was a
correct mechanism applied only to the sources the test could reach. This one is
a correct mechanism applied only to the **first** place that needed it. The
index identity is read once and then trusted for every request after it. The
catalog identity is recorded, unless reading it fails. The catalog learned to
refuse an incomplete read, and the harness that checks the catalog never did.

### 1. An identity that could not be read was written down as `null`

Round 17 moved the catalog identity read to *after* the first publication,
because the bulk write is what creates the index on a node that has never
published. The read was wrapped in a `try` that stored `null` on failure. That
is the same hole reopened by a shorter road: `null` is what a node that has
never published looks like, so the check that exists to notice a replaced
catalog compares `null` against the new identity and reads it as **unchanged**.

Publish once, lose one lookup, and the catalog can then be replaced without this
node ever noticing. It republishes nothing, because its state says it already
published; it reads nothing new, because the source position has not moved. It
reports itself idle against an empty index for as long as it runs.

The batch now stays **pending** until the identity is known. Not knowing which
catalog was written to is not the same as knowing it has not changed. Both ways
of not knowing are treated alike — a lookup that failed and a catalog that is
absent straight after a successful write — because both say the same untrue
thing if they are recorded. The batch is already durable and republishing it is
idempotent, so the cost is one replayed batch.

Reproduced against a real cluster, through a proxy that forwards everything and
answers **503** to the identity lookup once the bulk write has gone past:

| | count after the catalog is replaced |
|---|---:|
| the lookup failure swallowed and stored as `null` | **0** |
| the batch left pending | **1** |

Zero against one is the reviewer's "reported idle with zero documents instead of
one", in the harness.

### 2. A name is not an index, for every request and not only the first

Rounds 13 to 15 spent three answers on the ordering key and a fourth on the
index identity, because a deleted and recreated index restarts its sequence
numbers at zero and the saved position would skip everything below the old mark.
The position has been keyed by UUID since. The UUID was read **once**, and every
request after that addressed the index by name.

So the failure moved rather than went away. Replace the index between the
identity read and the search — a reindex behind an alias, someone rebuilding a
source index — and the new index answers to the old name. Its records mean
nothing against the checkpoint taken from the index before it, and they are
marked as read under the key of the index they did not come from. The next pass
keys the position by the new UUID, finds no mark, and reads every one of them a
second time. One source record, a published count of two.

The identity is now re-read before each page is yielded. A UUID is never reused,
so a page that still answers with the same one came from the index whose
checkpoint bounds it; a page that does not ends the read of that index, and the
next pass starts it from the beginning, which is what it is. The cost is one
settings request per page.

Reproduced with a proxy that deletes and recreates the source index, with the
same record in it, when the first search arrives:

| | count for one source record |
|---|---:|
| the name trusted after the first request | **2** |
| the identity re-read per page | **1** |

### 3. The listing collapsed a record that reached two destinations

The collector assigns one identifier per record and writes it to whichever index
the record is routed to. `_id` is unique only **within** an index. The retry
check keyed its listing by `_id` alone, so a record that reached two
destinations — one of the duplications the check exists to find — became a
single entry.

It came back short in **both** arms at once, so the comparison between them
still balanced and the run passed. Keyed by index and identifier now.

The listing is also checked against the cluster's own `_count`. Nothing is
writing by the time those two reads happen, so they have to agree, and when they
did not it was the listing that was wrong. A count says nothing about which
records or where, so it is no substitute — but it is a second opinion the
listing cannot talk itself out of, and it catches this class of mistake without
having to guess which key was wrong.

### 4. A request that could not answer was read as an answer of nothing

The catalog rejects an incomplete refresh and an incomplete search, and rounds
16 and 17 spent two findings getting the distinction right. The harness that
checks the catalog never learned any of it. Its listing ignored `timed_out`,
ignored `_shards.failed`, never looked at the refresh, and — worst of the four —
tested `status >= 300`, which a refused connection passes: `call` returns
`(0, {})` for one, and `0` is below every threshold ever written there.

Every count in that file comes from this one listing, so there is no useful
partial answer. All four now raise. Two things stay answers rather than
failures, for the reasons round 17 established: a **404** is a destination that
received nothing, and `successful < total` on a refresh is the shipped
single-node layout and not a fault.

The settle loop had the same hole one level down. It waits for the document
count to stop changing, so an undercount that repeats ends the run early and
measures a half-written cluster. An incomplete count is now not a number.

### Every fix run against its own absence

Each mutation restores exactly the code that was reported, and the named test is
run before the mutation as well as after — an unresolvable test name fails for
reasons that have nothing to do with the fix, and the first version of this
script did exactly that and reported all five as caught.

| Fix removed | What the check said |
|---|---|
| the identity failure kept pending | `the pass whose catalog identity could not be read reported success` |
| — the same mutation, consequence | `after an identity lookup that failed and a catalog replacement the count came back as 0, wanted 1` |
| the identity re-read per page | `one source record produced a count of 2, wanted 1` |
| the listing keyed by index and identifier | a duplicate across destinations was counted once |
| the completeness checks | a timed-out search, a failed shard, a refused connection and a failed refresh all read as an empty index |
| the listing checked against `_count` | a listing shorter than the cluster's own count was accepted |

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 71 of 71 (was 68) |
| `retrycheck` unit tests | 24 of 24 (was 12) |
| `replay.py` / `realcheck` unit tests | 20 of 20, 11 of 11 |
| `retrycheck`, 4.0.1 and 4.0.14, fixtures and journal | 8 arms, no failure |
| `replaycheck`, 3.2.8 / 4.0.1 / 4.0.14, rotation and restart | 35 of 35 each, rotation and restart green |
| Catalog documents, fifteen sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| Ansible, 12 playbooks | syntax ok, the encrypted vault restored |

The retry numbers are unchanged by the reader fix, which is the answer that was
wanted: with the identifier, 43 operations become 43 documents on both deployed
versions and 112 journal operations become 112 documents; without it, 51 and
then 301 on 4.0.1 and 259 on 4.0.14. Nothing in these fixtures or in that
journal reaches two destinations, so the collapsed key was hiding nothing here —
it was waiting for the first record that did.

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.

## Round 20 — a question the order of operations could not answer

Two findings: one in the shipped code, one in the harness. Both reproduce and
both are fixed.

The first one had been moved twice already, which is the interesting part. It
was not that the fix was in the wrong place; it was that the question being
asked could not be answered from where it was being asked, so moving it only
changed which race lost.

### 1. Reading the identity after the write cannot say where the write went

The question is *which catalog index did this pass publish into*. Three answers
were tried.

**Before the write** (round 17's first form). On a node that has never
published, the bulk write is what creates the index, so the lookup returns
`null` — and `null` compares against the next identity as *unchanged*.

**After the write** (rounds 17 and 19). This is worse, and worse in a way that
looks like an improvement, because the failure mode is silent. Replace the
catalog in the window between the write landing and the writer learning that it
did, and the lookup **succeeds**. It names the empty replacement. The node
records that as the index it wrote to, the next pass compares that identity
against itself, finds no change, and reports itself idle over documents that no
longer exist.

Round 19 hardened that lookup against *failing* — an error, or an absent index —
and both of those were real. Neither is this. A lookup that succeeds and answers
truthfully about the present cannot be made to answer about the past, and there
is no read-after-write here to make atomic.

**So the third answer is to stop asking.** The index is created explicitly when
it is absent, and the identity is written down before the payload is sent.
Creating an index with no body still applies the composable template, so the
mappings are the shipped ones either way, and another node winning that race is
not an error — its index is this node's index.

That inverts the direction the race can fail in. What is recorded is where this
pass *intended* to write, so a write that lands somewhere else leaves the two
disagreeing on the next pass, and disagreement is what triggers the rebuild. The
old order recorded where the write did not necessarily land, in the direction
that suppresses the rebuild.

Two things now stop a pass rather than publish: a destination that cannot be
read at all, and a destination that is not the catalog the pass was **planned**
against. The second is not paranoia — this batch holds only what the pass read,
while a replacement needs everything the trees hold, and that is the next pass's
work. Both leave the batch pending, and republishing a batch is idempotent, so
either costs one replay.

Reproduced against a real cluster, with the proxy acting in the gap between the
bulk write being applied and the response reaching the writer:

| | documents after the next pass |
|---|---:|
| first publication, identity read after the write | **0** |
| first publication, destination fixed before it | **1** |
| a node whose trees hold history, read after | **0** |
| a node whose trees hold history, fixed before | **2** |

Zero instead of one, and zero instead of two. The second is the expensive one:
the rebuild that should have republished everything the trees held never ran.

### 2. The same validated read, in only one of the two checkers

Round 19 taught `retrycheck.py` that a request which could not answer is not an
answer of nothing. `mappingcheck.py` has its own catalog reader, and it is the
one that judges every catalog sequence — and it still ignored refresh failures,
search timeouts and shard failures, and turned any status at or above 300 into
an empty list.

It fails in the direction that passes. Leave a stale contribution behind so the
catalog holds two live documents totalling three; a response that reports a
shard failure and returns only one of them looks exactly like the answer the
sequence wants, one document counting two, and the suite reports no problem at
all. A stale contribution nobody can see is a stale contribution nobody reports.

The reads are validated now, with the distinctions round 17 established kept
intact: a refresh reports `total` as configured **copies**, so only `failed`
means something went wrong there, while a search reports `total` as **shards**
and every one has to be accounted for. A 404 stays an answer, because several
sequences delete the catalog on purpose.

`tools/collector/test_mappingcheck.py` is new and needs no cluster.

### Every fix run against its own absence

| Fix removed | What the check said |
|---|---|
| the destination fixed before the write | first publication holds **0**, wanted 1 |
| — the same mutation, on a node with history | holds **0**, wanted 2; the history the trees still held was never republished |
| creating the absent index explicitly | the identity is left for the bulk write to discover |
| refusing a destination the pass was not planned against | a replacement before the write is published to anyway |
| the search shard-failure check | a partial answer hiding a stale document was accepted |
| the search timeout and status checks | a timed-out search and a rejected search both read as the whole catalog |
| the refresh failure check | a failed refresh read as a read |

### Re-verified

| Check | Result |
|---|---|
| `template_catalog` unit tests | 75 of 75 (was 71) |
| `retrycheck` unit tests | 24 of 24 |
| `mappingcheck` unit tests | 8 of 8 (new) |
| `replay.py` / `realcheck` unit tests | 20 of 20, 11 of 11 |
| Catalog documents, seventeen sequences | all pass |
| Index mappings, OpenSearch 3.7.0 | 43 records, 0 rejected |
| `retrycheck`, 4.0.1 and 4.0.14, fixtures and journal | 8 arms, no failure |
| `replaycheck`, 3.2.8 / 4.0.1 / 4.0.14, rotation and restart | 35 of 35 each |
| Ansible, 12 playbooks | syntax ok, the encrypted vault restored |

`retrycheck` and `replaycheck` exercise the collector rather than the catalog,
so nothing this round touched is on their path. They were re-run anyway.

**One correction to earlier rounds.** The journal control has been quoted as 301
documents on 4.0.1 and 259 on 4.0.14, in rounds 17 and 19, as though the numbers
belonged to the versions. This run produced 259 on 4.0.1 and 301 on 4.0.14. They
are counts of duplicates produced by whichever chunks happened to be retried, so
they vary run to run and say nothing about either version. What is stable, and
what the control is for, is that removing the identifier duplicates at all: 259
or 301 against 112, every time.

Unchanged and still open: template grouping correctness, minimum processor cost,
active-run source coverage, source-owner approval, and the storage-node
deployment decision.


---

## Round 21 — the transport nobody had priced

Round 18 made the templating 31 to 57 per cent cheaper and left the hop that
carries the records to it unmeasured. Section 9 of `docs/TEMPLATES_FIX_PLAN.md`
listed "cost of the Forward loop, both directions" as open. This round prices
it and takes the wasteful half away.

**The Forward loop is 15 per cent cheaper per record and every returned byte is
the same.** The gain is all on the return half: the stamper no longer rebuilds
a record it did not change.

### The question that started it, answered first

The record does not travel as JSON. Fluent Bit's `out_forward` writes msgpack
already, so there is no JSON to replace and protobuf buys nothing:

- A record is an open map that the filters add to. No schema describes it. A
  protobuf `map<string, string>` is larger than the msgpack it replaces.
- Both Forward plugins would stop working. The change needs a Fluent Bit output
  plugin written in C.
- The only wire saving is the repeated key names, 32 per cent of the
  `infologger` stream and 24 per cent of `dpl`. That is bandwidth on a Unix
  socket, and Unix socket bandwidth is free.

`compress: gzip` on the output loses for the same reason: it spends processor
time on both sides to save bandwidth that costs nothing.

### What the wire actually is

Captured off the socket from a rig running the production input, filter chain
and forward output for each family, then replayed. Messages come from the
frozen template catalogue, weighted by line count.

| Family | Records | Wire | Bytes a record | Fields | Records a chunk |
|---|---:|---:|---:|---:|---:|
| `infologger` | 60,000 | 25.5 MB | 425 | 16 | 4,615 |
| `dpl` | 120,000 | 43.8 MB | 365 | 12 | 5,454 |

One message is `[tag, [[[EventTime, metadata], record], ...], {chunk, size}]`.
Fluent Bit writes `str8` for a long string, which is what msgpack-python
writes, and `map32` for the record. The event time carries whole milliseconds
because every parser in the chain formats with `%L`.

### The figure of record

Both arms share `ForwardServer._frame`, so framing is common and cancels.
Streaming over 1 MB reads, arms interleaved inside one process, paired ratios
so host drift cancels, median of 15 pairs, two passes each.

| | transport alone | the whole stamper hop |
|---|---:|---:|
| `infologger` | **−15.1 / −15.4 %** | **−6.5 %** |
| `dpl` | **−14.8 / −16.2 %** | **−4.0 %** |

The right-hand column is the landed `Stamper.handle` with the journal write and
the ledger accounting included. The transport is roughly a quarter of the hop,
so a 15 per cent cut there lands as 4 to 6 per cent overall.

🔴 **Read the ratios, not the absolutes.** The same code read 3.63 and 5.58
core-seconds a million on `infologger` an hour apart on this host, with nothing
else running. Every ratio above was taken on one host state.

### Where the time goes

Per record, landed code, each step timed alone.

| Step | `infologger` | `dpl` |
|---|---:|---:|
| framing alone | 0.41 | 0.22 |
| decode, shipped | 2.77 | 3.74 |
| decode, keeping the record's bytes | 2.77 | 2.37 |
| encode, shipped | 1.69 | 2.14 |
| encode, spliced | **0.28** | **0.24** |

🔴 **These do not sum to the end-to-end figures and must not be read as a
budget.** Each arm was timed with the others' allocations absent, and the sum
overshoots the measured whole by about a fifth. They say which direction each
step moved, nothing more. The end-to-end ratio above is the number of record.

What they do show is where the waste was: **the encode falls by 83 to 89 per
cent, and the decode does not move.** The stamper was adding two fields to a
sixteen-field record and then re-encoding all eighteen.

### The three mechanisms

1. **The record is never re-encoded.** `decode_chunk` keeps each record's own
   msgpack alongside the decoded dict. `encode_spliced` writes the entry
   prefix, a map header for the new pair count, those original bytes, then the
   stamp fields. Fluent Bit's value encodings and msgpack-python's agree, so
   the spliced chunk is byte-for-byte what the shipped path produced.
2. **The stamp tail is encoded once per template version and cached** on the
   ledger, pruned by `prune_versions` with the version it belongs to. This one
   is load-bearing: without it the splice wins almost nothing, because it
   trades one large C `packb` for many small Python `pack` calls and the call
   overhead eats the gain. The status field stays outside the cache — a version
   reads `new` the first time and `matched` afterwards.
3. **One framing pass per receive**, shared by both paths.

### What was tried and rejected

| Candidate | Result |
|---|---|
| `raw=True`, to skip the UTF-8 decode of every key and value | 2.6 against 3.0, and it breaks byte-identity: `use_bin_type=False` writes `raw16` where the shipped path writes `str8` |
| read the keys, skip the values nobody reads | 1.46 against 1.30 for decoding the whole map |
| skip the record, find the wanted keys in its bytes | 12.82 against 1.30 |
| splice the event time too, to dodge the `ext_hook` callback | lost by 6 to 8 points against decoding it |

**The C unpacker's map build beats every Python-level partial decode.** Three
independent attempts, one conclusion, and it is round 18's lesson in another
place: work that stays inside a builtin is cheaper than work that returns to
the interpreter for each item. That is why the decode column above does not
move, and it is the floor while the stamper needs fields from the record.

### The defect the first implementation had

A record that already carried a stamp field broke byte-identity. The shipped
path assigns into the dict and **overwrites**; a splice appends bytes and so
**duplicates the key**, giving one pair too many and two copies of the field.
Decoders take the last value, so the meaning survived and the record count
survived. The bytes did not, and the gate is byte-identity or rejection.

It was found by differential fuzzing, not by the corpus: 60,000 real records
never contain a stamp field, because nothing upstream of the stamper writes
one. A replayed document that already carries the field would.

The guard is arithmetic rather than a name lookup: `handle` records `len(record)`
before stamping and compares the growth against the pair count the tail claims.
They disagree exactly when a key was overwritten instead of added, and the
chunk falls back to `encode_forward` whole. The check is two `len()` calls per
record and does not show up in the measurements. It fired on 288 of 3,000
fuzzed chunks and every one of those still matched.

### Why it is byte-identical, and how that was checked

| Check | Cases | Result |
|---|---:|---|
| spliced reply against the shipped `forward.py`, driving the real `Stamper` | 180,000 records | **0 differences** |
| the same, at 64 KB, 256 KB, 1 MB and 4 MB reads | 180,000 × 4 | **0 differences** |
| differential fuzz, 5 message modes, 8 seeds | 12,000 chunks | **0 differences** |
| the same fuzz, path coverage | 3,000 chunks | 1,541 spliced, 1,171 fell back, 288 fired the guard |
| record pair counts 0 to 21 and 65,533 to 65,537, stamped and not | 54 | **0 differences** |
| malformed messages: 23 shapes, accepted against rejected | 23 | **0 disagreements** |
| real Fluent Bit round trip, output → stamper → input → file | 20,000 records | **0 errors** |
| every returned row, both arms, `doc_id` and `collector_time` excluded | 20,000 | **0 differences** |
| `test_stamper.py`, `test_acceptance_stamper.py` | — | 24 of 24 |

Three of those earn their place. The **read sizes**: at 64 KB a two-megabyte
chunk spans thirty receives, so the framing is exercised at every boundary a
socket can produce. The **pair counts**: 15 to 16 crosses `fixmap` into
`map16` and 65,535 to 65,536 crosses into `map32`, which is where a header
width bug would hide. The **malformed shapes**: the splice must reject exactly
what the shipped decoder rejects, or it accepts corruption the old code caught.

### The trap, which cost two wrong runs

`msgpack.Unpacker.tell()` mis-counts after an `OutOfData` at a feed boundary:
the next object's offset comes back one byte late, then it resynchronises. A
decoder that only iterates objects never sees this, which is why the shipped
`forward.py` was never wrong. A splice needs byte offsets, so it does see it.

`ForwardServer._frame` therefore scans a fresh unpacker over the pending buffer
on every receive and takes an offset only from a `skip()` that succeeded. The
scan covers the incomplete tail alone, because the consumed bytes are dropped
each time.

### What did not change

Fluent Bit's `flush` stays at 1. This round did not touch it and the basin
measured earlier still stands. The stamper's semantics are untouched: the same
records, the same templates, the same counts, the same acknowledgement order.
The shipped `encode_forward` path still runs whenever a message arrives in a
mode the splice does not cover, which is every PackedForward and Message-mode
chunk, and whenever the guard above fires.

### One correction to what this round first reported

The transport gain was first written up as 32 to 41 per cent. That was measured
against a prototype whose baseline created a fresh `Unpacker` for every chunk
and whose stamp tail carried three fields. The landed code shares one framing
pass between both arms and carries a two-field tail, and against it the gain is
15 per cent. The whole-hop figures moved little, because the transport is only
a quarter of the hop either way.
