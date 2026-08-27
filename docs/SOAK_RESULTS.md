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