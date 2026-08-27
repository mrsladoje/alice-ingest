# Fluent Bit soak rig

A container rig that pushes the **production collector configuration** to its
limit on one machine, and writes down what happened.

It answers four questions that the repository currently answers by arithmetic
only:

1. How many records a second can this Fluent Bit configuration take?
2. How much memory does it really use? The "Fluent Bit uses 10 MB" figure is
   quoted everywhere and holds for nothing we run.
3. Does the durability layer work — a full disk buffer, a sink that dies, a
   collector that is killed?
4. Which of the light-mode flags actually buy anything?

Everything runs in containers on a laptop. No CERN virtual machine is needed.

## Why the replay cannot do this

`images/replay/replay.py` is bound by the S3 download and by its own Python
parsing, not by Fluent Bit. At the fast preset it emits about 1,300 records a
second in total. Fluent Bit takes far more than that. A limit test driven by
the replay would measure the replay.

So the rig has its own generator, `logburst.py`, which reads a frozen fixture
of real-shaped records and writes them as fast as it is told to.

## The pieces

| File | What it does |
|---|---|
| `mkfixture.py` | Builds a fixture: DDS and stdout line bodies, InfoLogger JSON records. Synthetic by default, or harvested from real replayed logs with `--from-logs`. |
| `logburst.py` | The load generator. Writes DDS and stdout lines into the tail directories and sends InfoLogger JSON to the TCP input. Steady, staircase or burst. |
| `mkconfig.py` | Renders `deploy/roles/collector/templates/collector.yaml.j2` — the real production template — then patches the knobs under test. The rig therefore cannot drift from production. |
| `sink.py` | A fake OpenSearch. Counts every document it receives, and can be told to stall, to answer 429, or to be stopped outright. |
| `soakrec.py` | The recorder. Samples Fluent Bit metrics, storage chunks, and — for **every** container in the rig — processor time, disk traffic and memory, every second. |
| `vmprobe.py` | Reads the cgroup and `/proc` counters. Docker lives in a Colima machine, so one shell is opened there and held; a whole sample costs about a millisecond. |
| `archive_rate.py` | Counts real records per node per second in the CERN S3 archive. This is what settles whether a quoted rate is per worker or per farm. |
| `soak.py` | Runs a whole profile end to end and writes a report. |
| `plot.py` | Turns a run's CSV files into plain SVG charts. No libraries needed. |
| `rig/docker-compose.soak.yaml` | Fluent Bit under a memory cap, the fake sink, the generator, and a real OpenSearch when a profile asks for one. |

## Gate zero, before any other number is read

A generator that quietly falls short produces a cell where delivered equals
ingested, nothing drops and the queue is flat — and the whole thing measured
14,000 records a second when it asked for 20,000. It passes, and it is a lie.

So every report starts with two checks:

1. **Achieved offer within 2 % of intended offer**, from `logburst.csv`
2. **The generator's own ceiling measured at this rate**, by

```
python3 tools/soak/soak.py selftest --rates 1000,20000,50000
```

which writes `tools/soak/selftest.json`. Every later report reads it.

**A cell that fails gate zero is void, not disqualified: it is rerun, not
ranked.**

## Pinning, and why throttling proves nothing

Services are pinned with `cpuset`, not capped with a quota. A quota would make
`nr_throttled` the saturation signal; pinning sets no quota, so `nr_throttled`
stays at zero whatever happens and "nothing was throttled" means nothing.

**Saturation is read from `usage_usec`** — a service is saturated when its
processor time approaches its pinned core count times elapsed time.

| Service | Pinned to | Why |
|---|---|---|
| Collector, and the worker's OpenSearch node | cores 0–3 | The measured budget |
| Storage OpenSearch nodes | cores 4–7 | Must never be the bottleneck |
| Generator | cores 8–9 | Gate zero depends on its headroom |
| Fake sink and live lane | cores 10–11 | Round 1's five-per-cent artefact came from starving exactly this |

Override with `--fb-cpuset`, `--os-cpuset`, `--sink-cpuset`, `--gun-cpuset`
and `--lane-cpuset`. `--cpus N` still works and means the first N cores.

**A cell where any unmeasured service passed 80 % of its pinned cores is
void** — the worker only looked healthy for want of load.

## Three sinks, because they answer different questions

| Sink | Question |
|---|---|
| `null` | What can Fluent Bit itself do on this machine? |
| `http` (the fake sink) | What can the whole pipeline do when the receiver always says yes? |
| `opensearch` (a real container) | What does the product actually survive? |

Without the `null` run, every number is really an OpenSearch number.

## Before you start

- Docker running. On a Mac: `colima start` (this repository's work uses the
  `cern` profile, which sets the CERN resolvers). Round 2 needs it at twelve
  processors, because the rig pins four to the worker and the other eight to
  the harness: `colima start cern --cpu 12 --memory 32 --disk 100`.
- Python 3 with `pyyaml` and `jinja2`. Both come with the Ansible environment.
- Images: `fluent/fluent-bit:5.0.8`, `python:3.12-slim`,
  `opensearchproject/opensearch:2.17.0`.

## Run one

```
python3 tools/soak/soak.py profiles
python3 tools/soak/soak.py run p0
```

Each run writes `tools/soak/runs/<timestamp>-<profile>/` containing:

- `report.md` — the table you read first
- `summary.json` — the same numbers, for a chart
- `soakrec.csv` — one row a second, for every container in the rig
- `threads.csv` — the collector's threads, so the main loop and the output
  workers separate by name
- `logburst.csv` — one row a second from the generator
- `conf/collector.yaml` — exactly the configuration that ran
- `fixture/` — exactly the records that were sent

## The profiles

| Profile | What it proves |
|---|---|
| `p0` | Fluent Bit's own ceiling. Staircase to a `null` sink. |
| `p1` | The pipeline's ceiling. Same staircase, HTTP sink that always says yes. |
| `p1os` | The product's ceiling, against a real OpenSearch. Expect it far lower. |
| `p2` | Burst absorption. 30 s at peak, 120 s quiet, repeated. |
| `p3` | Durability. The sink is stopped mid-run and brought back. |
| `p5` | A long soak at half the knee rate. Leaks and disk growth. |
| `p6` | One fixed rate, for sweeping configuration knobs. |

Override anything from the command line:

```
python3 tools/soak/soak.py run p0 --duration 900 --max 80000
python3 tools/soak/soak.py run p3 --rate 8000 --fault-at 120 --fault-seconds 600
python3 tools/soak/soak.py run p3 --fault-kind stall
```

## What counts as the limit

Pick the highest rate where all four hold over a ten-minute window:

- delivered records a second equals ingested records a second
- `dropped_records` is zero
- memory stays under the `MemoryHigh` figure, 384 MB
- the chunk queue is flat, not climbing

That rate is the knee. Everything above it is a burst test, not a limit.

## Charts for the presentation

```
python3 tools/soak/plot.py tools/soak/runs/<run>
```

Writes `charts/throughput.svg`, `memory.svg` (with the `MemoryHigh` and
`MemoryMax` lines drawn on it), `queue.svg`, `loss.svg` and `disk.svg`, plus a
`charts.html` that shows them together. Plain SVG, black and one red accent, so
they drop into the deck without looking like stock plots. Fault moments are
marked on the time axis.

## Reading the report

**Ingested against re-injected.** The `rewrite_tag` filter feeds records back
into the engine, so Fluent Bit's own input counter is larger than what was
sent. The report separates the two: `ingested` is what the generator delivered,
and the re-injected figure is the routing filter's own traffic.

**Where every record went.** Three numbers, and they mean different things:

- *received by sink* — what actually arrived. The fake sink keeps its counters
  in a file, so stopping and restarting it mid-run does not reset the count.
- *undelivered when the run ended* — ingested minus delivered. Records still in
  the buffer are undelivered, **not lost**. Read the next row before calling it
  loss.
- *lost for good* — `dropped_records` plus retries that gave up. This is the
  only number that means a record will never arrive.

A run whose queue is empty at the end and whose `lost for good` is zero lost
nothing, however large the undelivered figure looked mid-run. Every record also
carries a `soakrun_<id>` token, so the same count can be taken from OpenSearch
with a query.

**Memory.** Docker enforces `memory.max` only. `soak.py` writes `memory.high`
into the container's cgroup through the virtual machine, so the rig matches the
unit file's `MemoryHigh=384M` and `MemoryMax=768M`. The report says which of
the two it managed to set. `memory.high events` counts how often the kernel
throttled the process; `memory.max events` and `oom kills` count the hard stop.

**Chunks.** `peak chunks` is the queue depth, and `on disk only` is how much of
it Fluent Bit pushed out of memory. During an outage the second number is the
durability layer doing its job.

## Sweeping the light-mode knobs

`p6` holds the rate fixed so only the configuration changes:

```
python3 tools/soak/soak.py run p6 --max-chunks-up 32
python3 tools/soak/soak.py run p6 --max-chunks-up 256
python3 tools/soak/soak.py run p6 --storage-type memory
python3 tools/soak/soak.py run p6 --pause-on-overlimit on
python3 tools/soak/soak.py run p6 --mem-buf-limit 32M
python3 tools/soak/soak.py run p6 --backlog-mem-limit 64M
python3 tools/soak/soak.py run p6 --total-limit-size 2G
python3 tools/soak/soak.py run p6 --flush 1
python3 tools/soak/soak.py run p6 --output-workers 4
python3 tools/soak/soak.py run p6 --lua off
python3 tools/soak/soak.py run p6 --compress gzip
python3 tools/soak/soak.py run p6 --mem-max 16m --mem-high 10M
```

The last line is the "10 MB" claim, run honestly.

## Running the back half unattended

Stages D through G each need the previous stage's winner, and a person waiting
between them is what turns the back half into a week. `finish.py` runs stages
C-extension, D, E, G and F end to end and picks each winner from the runs:

```
python3 tools/soak/finish.py
```

**The rules it applies, so that a choice made at four in the morning can be
argued with at nine:**

| Choice | Rule |
|---|---|
| Flush | Lowest mean total core-seconds per million at 20,000 a second, over the cells that passed |
| Heap | The same rule, the same rate |
| Core split | The same rule, across the three cpuset arms |

**A cheaper value is only adopted if it wins by more than the measured noise
floor of 1.9 %.** Inside the floor the more conservative value keeps its place:
the longer flush, which sends fewer requests, and the value that already
carries evidence. A cell that voids or is disqualified is dropped from its
group with a line in the log rather than counted as a zero.

`total` is the collector plus the worker's OpenSearch node plus both storage
containers — every service the worker's four cores pay for. **The generator is
left out on purpose:** in production nothing generates the logs, so counting the
replay against the budget would inflate every figure.

Two things it writes:

- `runs/finish-decisions.json` — every choice, with the group means behind it
- `runs/finish.log` — the running narrative, one line per stage and per choice

**It rewrites `cells.py` as it goes**, filling the winning flush into stage D,
the winning flush and heap into E and G, and the whole chosen configuration
into stage F's second half — the six cells the manifest could not name until C,
D and E had run. Do not edit `cells.py`, `soak.py`, `mkconfig.py` or
`soakrec.py` while it is running: a cell measured on a changed instrument is not
comparable with the cells before it, and that error has already cost this
project one full stage rerun.

**Stage F alternates control and chosen** — `F1, F7, F3, F9, …` rather than all
six controls and then all six chosen — so that any drift over the two hours
falls on both arms equally instead of landing entirely on whichever ran second.

## Fidelity, stated plainly

**What the rig reproduces.** The production pipeline, byte for byte: the same
template, the same five parsers, the same three Lua filters, the same severity
routing, the same buffer and retry settings.

**What it does not.** A laptop is not an `m2.medium` sharing 3.75 GB with a
1 GB OpenSearch heap, and container storage is not the worker root filesystem.
Take the shape of the curve from this rig and confirm the absolute numbers on a
real worker before quoting them.

**The generator's own ceiling.** Measure it before trusting a run near the top:

```
python3 tools/soak/logburst.py --fixture <run>/fixture --mode selftest \
    --rate 400000 --duration 10 --summary /tmp/selftest.json
```

If the achieved rate is close to the rate under test, add `--gun-workers`.

## Safety

`logburst.py` stops itself when the filesystem holding the tail directory
passes `--disk-guard-pct`, 85 % by default, and rotates each tail file at
`--max-file-bytes`, 256 MB by default. Both matter: on a worker, the Fluent Bit
buffer shares the root filesystem with OpenSearch, which turns every index
read-only at its 95 % flood-stage watermark.

Tear the rig down with `python3 tools/soak/soak.py down`.
