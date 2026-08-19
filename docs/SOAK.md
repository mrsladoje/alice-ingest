# What Fluent Bit actually does under load

Measured on 18 August 2026 with `tools/soak`, a container rig that runs the
**production collector configuration** — the same template, parsers, Lua filters
and severity routing that `deploy/roles/collector` installs on a worker.

Twenty-three runs. Every number below comes from one of them, and every run keeps
its own directory under `tools/soak/runs/` with the configuration that produced
it, the records that were sent, and a reading of the collector once a second.

## The four questions, answered

**1. How much can it take?** About **53,000 records a second** on two processor
cores. Below that, delivered equals ingested exactly. Above it, Fluent Bit reads
its tail files more slowly than they are written, and the surplus waits in the
files — no loss, and the backlog drains once the load stops.

**2. How much memory?** Between 133 MB and 228 MB across the working range. The
quoted "10 MB" is wrong by more than an order of magnitude. `MemoryMax` was never
reached and the process was never killed.

**3. Does the durability layer work?** Yes, for 61 seconds at 20,000 records a
second — and then only partly. See "the uncomfortable finding" below.

**4. Which light-mode flags earn their place?** Three of eleven. The rest are
inert at steady state.

## The ceiling

A staircase from 5,000 to 60,000 records a second, one minute per step, against a
sink that always says yes.

| Offered | Ingested | Delivered | Peak memory | Lost |
|---|---|---|---|---|
| 5,000 /s | 5,006 | 4,754 | 45 MB | 0 |
| 20,000 /s | 20,004 | 19,675 | 98 MB | 0 |
| 35,000 /s | 35,004 | 34,679 | 173 MB | 0 |
| 50,000 /s | 46,794 | 46,492 | 191 MB | 0 |
| 60,000 /s | 52,880 | 52,644 | 200 MB | 0 |

The generator reached 60,000 /s with no stall of its own, so the plateau is
Fluent Bit's read rate, not the load. Against a `null` sink — no payload
formatting at all — the same plateau appears at ~53,000 /s and 161 MB, so the
limit is parsing and routing, not the output.

## Bursts are a non-event

Ten cycles of 30 seconds at 50,000 /s followed by 120 seconds at 5,000 /s.
10,954,122 records emitted, 10,953,551 delivered, **nothing dropped**, queue empty
at the end, peak memory 209 MB. No chunk was ever pushed out of memory. The burst
Lubos asked about is handled in the normal path.

## The uncomfortable finding

A fifteen-minute sink outage at 20,000 records a second. The buffer holds for
**61 seconds**, then discards at roughly the rate it ingests. But the loss is not
spread across the three log families:

| Output | Delivered | Dropped |
|---|---|---|
| `infologger` | 4,314,450 | **10,085,550** |
| `family.info` | 3,674,985 | **0** |
| `family.other` | 3,600,948 | **0** |

**Every lost record was InfoLogger.** DDS and stdout lost nothing.

The reason is the source, not the buffer. DDS and stdout arrive by `tail`: when
the collector slows down, the data stays in the log files and is read later. The
file is the backpressure. InfoLogger arrives over TCP, and there is nothing behind
the socket — once the output buffer fills, those records are gone.

Two consequences:

- Raising `fluent_bit_log_buffer_limit` buys time for **InfoLogger only**. The
  other two families do not need it.
- A queue in front of the TCP path would do far more for InfoLogger than any disk
  buffer, because it would give that path the backstop the file-based ones already
  have.

Retries never entered into it. `retries_failed` stayed at zero throughout: the
buffer cap discards records long before the ten retries are exhausted.

## Raising the buffer raises memory

The same outage, run twice, changing only `storage.total_limit_size`:

| Buffer | Protection | Peak memory | `memory.high` throttle events |
|---|---|---|---|
| `256M` | 61 s | 373 MB | 0 |
| `2G` | 491 s | 404 MB | 65,413 |

The window scales cleanly — roughly four seconds of protection per 100 MB at this
rate. But 1,001 queued chunks cost enough memory in bookkeeping to cross the
384 MB `MemoryHigh` line, and the kernel throttled the collector 65,413 times
during exactly the outage it was meant to survive.

**`fluent_bit_log_buffer_limit` and `fluent_bit_memory_high` must be raised
together.** `deploy/README.md` previously said raising the disk buffer does not
raise memory. It does.

## Against a real OpenSearch

The same staircase into a bare single-node OpenSearch collapses at **2,000 to
3,000 records a second**: delivery falls to near zero, chunks spill to disk,
memory pins at exactly 384 MB, the 256 MB cap fills, and 1,575,680 records are
discarded.

**Do not quote that rate.** That OpenSearch had no index templates, so every field
went through dynamic mapping, and no rollover aliases. It is a bare container, not
a bootstrapped node. What transfers is the *sequence* — saturate, spill, pin at
`MemoryHigh`, fill, discard — not the number. The real figure needs a worker.

## The live lane pushes back, on the one path that cannot take it

The live lane is a second `http` output, gzipped, matching
`^(infologger|family\.other)$` — roughly four-fifths of the stream. It was chosen
to be best-effort: a 1 MB queue and one retry, "so a dead viewer never pushes back
on OpenSearch."

It does not push back on OpenSearch. It pushes back on the InfoLogger input.

At 20,000 records a second with the live lane on, and the live lane given its own
receiver so it competes with nothing:

| Path | Offered | Ingested | Delivered |
|---|---|---|---|
| DDS + stdout (`tail`) | 2,400,000 | 2,400,000 | 2,400,000 |
| InfoLogger (`tcp`) | 3,600,000 | **178,653** | 178,653 |

The file-based tiers were untouched. The InfoLogger input took **five per cent** of
what was offered — about 600 records a second against 12,000. The same collapse
appeared in an earlier run against a shared receiver (201,703 delivered), so it is
not an artefact of one sink being busy.

The mechanism fits the design: an `infologger` chunk is held until *every* output
matching it has finished, and the live lane's 1 MB cap means it finishes slowly.
The file-fed families survive because their backlog waits in the log files;
InfoLogger has nothing behind the socket, so the input simply stops accepting.

**This needs one more experiment before it is acted on.** The receiver here is a
single Python process decompressing gzip, so the live lane was slow. What is
established is the *coupling* — a slow live-lane consumer throttles InfoLogger
ingest, which is exactly what the 1 MB / one-retry choice was meant to prevent.
What is not established is how slow a real live-lane service would be. Run it
against the real one before changing the design.

## The light-mode flags

All at 20,000 records a second, all lossless. Three identical baseline runs gave
115.8, 122.3 and 127.7 MB, so the noise floor is about 12 MB and anything inside
it is not a result.

| Knob | Peak memory | Verdict |
|---|---|---|
| `flush: 1` | 66 MB | **Halves memory.** The one clear win. |
| `storage.type: memory` | 100 MB | Cheaper, but loses everything on restart. |
| `workers: 4` | 147 MB | **Worse.** Costs memory, buys nothing here. |
| `compress: gzip` | 111 MB | Inside the noise. |
| `lua: off` | 124 MB | Inside the noise — our enrichment is nearly free. |
| `max_chunks_up: 32` / `256` | 130 / 136 MB | Inside the noise. |
| `mem_buf_limit: 32M` | 126 MB | Inside the noise. |
| `storage.backlog.mem_limit: 64M` | 126 MB | Inside the noise. |
| `total_limit_size: 2G` | 126 MB | Inside the noise *at steady state* — see above for what it does during an outage. |
| `pause_on_chunks_overlimit: on` | 130 MB | Inside the noise. |

`max_chunks_up` doing nothing is not a surprise once the queue depth is read: it
never exceeded 19 chunks against a ceiling of 64. That knob is a backpressure
ceiling, not a working-set control, and it only bites in the outage runs.

## What this does not cover

- **No long soak.** The longest run was twenty minutes. Leaks over days are not
  measured here; the workers have been running this collector under replay load
  since July, which is better evidence than a laptop would give.
- **Not real hardware.** A laptop container is not an `m2.medium` sharing 3.75 GB
  with a 1 GB OpenSearch heap. Take the shapes from here and confirm the absolute
  numbers on a worker.
- **The health input is unmeasured.** It cannot run in the rig: the official
  Fluent Bit image ships no Python interpreter for its `exec` command. It emits one
  record every thirty seconds, so the cost is almost certainly negligible, but that
  is an argument rather than a measurement.

## Reproducing any of it

```
python3 tools/soak/soak.py profiles
python3 tools/soak/soak.py run p0
python3 tools/soak/plot.py tools/soak/runs/<run>
```

`tools/soak/README.md` explains the rig, the three sinks, and how to read a report.
