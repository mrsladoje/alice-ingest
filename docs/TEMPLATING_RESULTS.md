# Round 4 — log templating · results

The plan is `docs/SOAK_PLAN.md`, section *Round 4 — log templating (before
embeddings, deliberately)*. Round 2's results are `docs/SOAK_RESULTS.md`.

Measured 27 August 2026 against **45.6 million lines of real ALICE log text**
pulled from the `epn-backup-logs` archive. Tools are `tools/templating/`.

---

## Read this first

**Three answers, and none of them is the one the plan expected.**

1. **The new-template rate is low and it falls.** 45.6 million lines produce
   **3,822 templates**, and the steady-state rate over the second half of the
   corpus is **48.9 new templates per million lines**. At the reference rate of
   20,000 records a second that is **about one new template per second.**
2. **Drain3 in Python fits the budget.** **19.73 core-seconds per million
   records**, measured in the same Linux VM that carries round 2's numbers.
   Against the four-core stack's 104.67, templating adds **18.8 %**, or **0.39
   of a core at 20,000 records a second.** The plan asked whether production has
   to be rewritten in Rust, C or Go. On this evidence, **not yet.**
3. **Masking is not a cost the tree pays. It is what makes the tree cheap.**
   Removing it makes mining **46 times dearer** and produces 92 times the
   templates.

**What this unblocks.** Round 3 exists to find out whether embeddings are
affordable, and the plan stated in advance that it expected to confirm a
ceiling. It confirms headroom instead. An embedding is paid **once per
template**, not once per line, and one template a second is one to two orders of
magnitude *inside* what even a full transformer does on one core. **Round 3 is
affordable, and the cheapest rung of its ladder is not needed to make it so.**

---

## What was measured, and where

Every cost figure is `time.process_time` — processor seconds, not wall time —
and every one quoted as a headline was measured **inside the Colima `cern` VM
pinned to four processors**, which is the environment round 2's core-seconds
come from. That matters: the same corpus slice costs 17.00 core-seconds per
million natively on macOS and 14.54 in the VM, so the two environments are not
interchangeable and a native number quoted beside a soak number would be wrong.

**The miner is deterministic across both.** Every template count in this
document is identical native and in the VM, at every corpus size tested. Only
the cost differs.

### The corpus

`tools/templating/corpus.py` reads the archive over HTTPS with the `cern_s3`
profile. No lxplus hop and no Kerberos ticket is involved, which is why this
round could run at all.

| Family | Lines | Sources | Where it comes from |
|---|---:|---:|---|
| `infologger` | 26,505,911 | 133 | 40 MySQL partitions, sampled evenly across all 179 |
| `stdout` | 19,046,730 | 157 | O2 process `_out`/`_err` logs, 4 run tarballs |
| `dds` | 267,607 | 24 | the DDS firehose, 24 run tarballs |
| **Total** | **45,596,613** | **314** | |

The InfoLogger objects are one MySQL partition each and the partitions are
ordered, so the first N of them are one slice of time rather than a sample.
`--il-stride` takes every fourth object instead, across the whole archive.

🔴 **The DDS and stdout corpora carry the raw line; the InfoLogger corpus
carries the parsed message.** In production the collector parses first and the
sidecar would receive the message alone. Stripping the DDS timestamp-and-
severity prefix drops its mining cost from 59.52 to **43.58** core-seconds per
million, a 27 % fall. **So the DDS and stdout costs below are upper bounds. The
InfoLogger figures are the ones production would actually pay.**

---

## The new-template rate — the number round 3 depends on

| Family | Lines | Templates | Whole corpus | **Steady state** |
|---|---:|---:|---:|---:|
| `infologger` | 26,505,911 | 2,853 | 107.6 /M | **34.4 /M** |
| `stdout` | 19,046,730 | 813 | 42.7 /M | **0.6 /M** |
| `dds` | 267,607 | 186 | 695.0 /M | **0 /M** |
| **All three** | **45,596,613** | **3,822** | 83.8 /M | **48.9 /M** |

**Read the steady-state column, not the whole-corpus one.** The rate over a
whole corpus is dominated by its first few blocks — the tree starts empty, so
the opening 500,000 lines create 526 templates and nothing later comes close.
The steady-state column is the second half of each family's run, and it is the
only column that describes what a running farm would see.

**New templates arrive in bursts, not at a rate.** A burst is a program
appearing for the first time; between bursts the tree does not move at all:

| Family | Blocks in the second half | **Blocks that produced nothing** |
|---|---:|---:|
| `infologger` | 27 × 500,000 lines | **13** |
| `stdout` | 19 × 500,000 lines | **15** |
| `dds` | 6 × 25,000 lines after line 125,000 | **6** |

DDS is the extreme case and the clearest one. It produced 183 templates in its
first 25,000 lines, three more by line 100,000, and then **nothing at all across
the remaining 142,000 lines.**

### The templates are readable, which is not a given

The five most common InfoLogger templates, with their line counts over one
million lines:

```
127320  RegionAllocatorResource: waiting to allocate a message.
        region=O2DataRegion_TimeFrame alloc=<NUM> region_size=<NUM> free
127319  Memory region 'O2DataRegion_TimeFrame' is too small, or there is a
        large backpressure. <msgs_suppressed=<NUM>>
 95811  partitionId: <*> DDS session: RUNNING; DDS session ID: <UUID>;
        Run Nr.: <NUM>; topology state: <*>
 79541  Status request for ODC <FLOAT>.<NUM> (DDS <FLOAT>) from [ipv4:<IP>]
        user-agent:grpc-go/<FLOAT>.<NUM>: runnning: true
 67211  CTF <NUM> size report: <*> <*> <*> <*> PHS:N/A CPV:N/A ... - Total:<NUM>
```

One masking imperfection is visible and left alone: a version number `0.87.0`
becomes `<FLOAT>.<NUM>` rather than one token. It costs nothing and merges
nothing it should not.

---

## The per-line cost — paid on every line forever

| Family | In the VM | Native macOS |
|---|---:|---:|
| `infologger` | **17.27** | 19.70 |
| `stdout` | 23.21 | 25.91 |
| `dds`, raw line | 50.84 | 59.52 |
| **All three** | **19.73** | 22.26 |

Core-seconds per million records. The all-three figure is weighted by this
corpus's mix, which is 58 % InfoLogger and 42 % stdout.

**In the terms round 2 used:**

| | Core-seconds per million | Cores at 20,000 /s |
|---|---:|---:|
| The whole four-core stack, chosen configuration | 104.67 | 2.09 |
| The collector's own share of it | 27.70 | 0.55 |
| **Templating, added** | **19.73** | **0.39** |

Templating costs **71 % of what the collector itself costs**, and **18.8 % of
the whole stack**. At round 2's sustained ceiling of 42,000 records a second it
would want **0.83 of a core** — still under one, but then a fifth of the budget
rather than a tenth.

**The plan's rewrite question, answered.** *"If the per-line cost is real,
production goes to Rust, C or Go."* The cost is real and it is affordable at the
rates this stack actually sustains. A rewrite buys back at most 0.39 of a core
at the reference rate. **Do not spend it yet.** The number to watch is the rate:
the case for a rewrite arrives with the rate, not with the feature.

---

## Masking is what makes the tree cheap

Over 500,000 lines, in the VM:

| Arm | Cost | Templates |
|---|---:|---:|
| Mask only, no tree | 16.82 | — |
| **Mask plus tree** | **20.34** | **526** |
| Tree with masking removed | 932.07 | 48,306 |

**Removing the masking makes mining 46 times dearer.** This is the opposite of
the intuition that says a regex pass per line is the expensive part. Without
masking every distinct number is a distinct token, so the tree grows 92 times
the clusters it should have, and Drain's similarity search then walks all of
them on every line. The masking pass costs 16.82 and saves 911.73.

**The consequence for a rewrite.** Masking is 83 % of the total cost
(16.82 of 20.34). A faster language buys its gain almost entirely in the regex
engine, not in the tree walk. That is where a Rust version would have to win.

---

## One tree, or one per program

| Shape | Trees | Templates | Cost |
|---|---:|---:|---:|
| **One global tree** | **1** | **3,822** | **19.73** |
| One tree per source | 430 | 13,587 | 16.69 |

Per-source mining is **15 % cheaper** — each tree is small, so each search is
short. It also produces **3.6 times the templates**, because the same message
emitted by four programs is mined four times and stored four times.

**Take the global tree.** The 15 % is real and the 3.6× is worse: it is 3.6×
the embedding work in round 3, a template index full of near-duplicates, and a
worker holding 430 trees instead of one. The plan already wanted the OpenSearch
template index to be the shared copy across workers, and 430 per-worker trees is
the shape that makes sharing meaningless.

---

## What this round does not measure

**Stated rather than hidden**, in the manner round 2's retraction section asks
for:

1. **Transport is not in any number here.** A real sidecar also pays for the
   Fluent Bit output that feeds it and the write back to the local OpenSearch
   node. This round priced the mining, in process. The plan's cgroup-priced
   sidecar is still the only way to get the whole figure.
2. **The archive is October 2022 data.** It is the real format in the real
   layout, and `docs/LOG_TYPES.md` records the same thing about the `/scratch`
   tree — but it is one period, and every run in it is MFT. A detector that
   never appears here contributes templates that this round has not counted.
3. **DDS and stdout are priced on raw lines**, as noted above. Their true cost
   is lower and their template counts are upper bounds.
4. **The new-template rate is measured over an archive, not over a shift.** A
   burst here is a new program appearing in the corpus order. On a live farm the
   same bursts would arrive at run starts and configuration changes, which this
   data cannot time.
5. **Drain's knobs were not swept at scale.** `sim_threshold` 0.3 to 0.7 moved
   the template count from 11 to 14 on a 3,000-line probe and did not move cost
   at all, so the defaults were kept: threshold 0.4, depth 4, 100 children.

---

## One instrument fault, found and fixed

`loop_core_seconds_per_million` divided the whole read loop's processor time by
the **matching** lines. The loop reads the entire corpus whichever family is
asked for, so a small family was billed for reading the others: DDS reported
276.38 core-seconds per million against a true 59.74, a factor of 4.6.

It is now suppressed whenever `--family` is set, and the runs record
`read_lines` beside `lines` so the two can never be confused again.
`mining_core_seconds_per_million` is timed around the miner alone and was
correct throughout — no number in this document rests on the faulty one.
