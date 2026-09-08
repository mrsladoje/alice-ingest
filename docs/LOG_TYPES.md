# New log types — EPN survey and integration plan

The survey ran on `epn146` on 27 Aug 2026 and the results are below.

Tools: `tools/epnsurvey/survey.sh` (read-only capture, runs on an EPN) and
`tools/epnsurvey/regex_report.py` (scores a parser library against the bundle).

Source of the scope: `LUBOS_MEETING.md` item 3.

---

## What the survey found

### The job-log tree is on shared NFS, not local disk

`/scratch` is `10.162.0.60:/exports/scratch`, NFS version 4.2, 14 TB, 93 percent
full. It is mounted on every node. `epn146`, `epn228`, `epn323` and
`epn-infra13` all show the same 11 run directories and the same 64 GB.

This is the single most important finding, and it changes the collector design:

1. **A tail over `/scratch/jl/**` on every node ingests every file N times.**
   Each node must tail only its own subdirectory, `/scratch/jl/*/$(hostname).internal/`.
2. Reading another node's directory is cross-worker log shipping under another
   name. Lubos ruled that out (`LUBOS_MEETING.md` item 10). The per-node path
   above is what keeps us inside the rule.
3. Fluent Bit cannot use inotify on NFS. The tail input must poll, so
   `refresh_interval` is load-bearing here, not a tuning detail.

### The real path shape

```
/scratch/jl/<run_tag>/<epnNNN>.internal/<program>[_t<slot>]_reco<N>_<YYYY-MM-DD-HH-MM-SS>_<pid>_{out,err}.log
/scratch/jl/<run_tag>/<epnNNN>.internal/dds_<YYYY-MM-DD>.<N>.log
```

`/var/log/calib` does not exist on any of the four nodes. That path is from
Thanasis's own test rig, so every tail pattern ported from his config must be
rewritten against the tree above.

The run directory also holds shared libraries, `topology.xml`, `DDS.cfg` and a
worker tarball. **The tail pattern must end in `*.log`**, never `*`.

### Volume

11 run tags, all written 27 Oct 2022. 30 hosts per run. 54,450 log files,
64 GB, 13 distinct programs. This is stale data left on the scratch mount, not
a live stream. It is still the right thing to write parsers against, because it
is the real format in the real layout.

| Program | Files | Bytes |
|---|---:|---:|
| internal-dpl-injected-dummy-sink | 1320 | 1.71 GB |
| mft-stf-decoder | 21120 | 991 MB |
| Dispatcher | 1320 | 588 MB |
| ctf-writer | 1320 | 561 MB |
| mft-entropy-encoder | 21120 | 488 MB |
| dds | 330 | 389 MB |
| readout-proxy | 1320 | 328 MB |
| internal-dpl-ccdb-backend | 1320 | 290 MB |
| TfBuilderTask | 660 | 141 MB |
| qc-task-MFT-MFTClusterTask | 1320 | 16 MB |
| MFT-MFTClusterTask-proxy | 1320 | 7.5 MB |
| ErrorMonitorTask | 660 | 3.7 MB |
| internal-dpl-clock | 1320 | 2.8 MB |

Every run is MFT. There is no ITS data on these nodes at all.

---

## Thanasis's parser library, scored against this data

45,277 sampled lines across 14 program-and-stream pairs. All 30 regexes compile.

**17 of the 30 score exactly zero.** Twelve are ITS-specific, and this is MFT
data. The other five are `ctf_add`, `ctf_written`, `io_stats`, `link_discard`
and `feeid_stats`.

Coverage excluding the three catch-all parsers (`generic`, `basename`,
`dds_generic`), which extract no numbers:

| Program | Lines | Extractor match | Coverage |
|---|---:|---:|---:|
| internal-dpl-injected-dummy-sink | 4500 | 4332 | 96.3% |
| Dispatcher | 4500 | 4326 | 96.1% |
| readout-proxy | 4500 | 4314 | 95.9% |
| internal-dpl-ccdb-backend | 4500 | 4278 | 95.1% |
| MFT-MFTClusterTask-proxy | 1410 | 1194 | 84.7% |
| ctf-writer | 4500 | 3192 | 70.9% |
| dds | 4500 | 2550 | 56.7% |
| mft-entropy-encoder | 4500 | 2157 | 47.9% |
| qc-task-MFT-MFTClusterTask (out) | 2707 | 1264 | 46.7% |
| mft-stf-decoder | 4500 | 426 | 9.5% |
| ErrorMonitorTask | 423 | 0 | 0.0% |
| TfBuilderTask | 4500 | 0 | 0.0% |
| internal-dpl-clock | 216 | 0 | 0.0% |
| qc-task-MFT-MFTClusterTask (err) | 21 | 0 | 0.0% |
| **Total** | **45277** | **28033** | **61.9%** |

Hits per parser: `timeslice` 20998, `timer` 1800, `io` 1542, `dds_add_slot` 1152,
`dds_added_channel` 1152, `detector_stats` 1065, `dds_user_task` 246,
`ccdb_reads` 21, `param` 21, `cache_ptr` 18, `its_tracking_step` 18.

### Why the four zero-coverage programs score zero

Each has a different cause, and each needs different work.

**1. TfBuilderTask writes a second, undocumented format.**

```
[2022-10-27 17:41:52.663][D] NEW RUN NUMBER. run_number=0
[2022-10-27 17:41:52.664][I] [STATE][FMQ] Starting FairMQ state machine --> IDLE
```

This is the DataDistribution logger: full date, milliseconds, and a
single-letter level (`I`, `D`, `W`, `E`). Thanasis has no parser for it.

It is also the only O2 format we found that **already carries a date**, so it
satisfies rule R1 with no rewrite. Every other program's format does not.

**2. ErrorMonitorTask emits raw ANSI colour escapes.**

```
[<ESC>[01;36m16:31:31<ESC>[0m][<ESC>[01;32mINFO<ESC>[0m] ...
```

Every regex anchored on `^\[(?<time>\d{2}:` fails on the escape byte. Fluent Bit
has no built-in filter that strips ANSI, so this needs either a tolerant regex
or a strip step. Left alone it fails silently: `generic` would not match either,
and the line lands with no severity and no time.

**3. internal-dpl-clock has nothing to extract.** It emits only state-machine
transitions. `generic` covers 95.8% of it and that is the correct answer.

**4. `ctf_written` scores zero because CTF writing was off in these runs.**

```
TF#3 {Run:0 TF:3 Orbit:0 CteationTime:0 Detectors: } CTF writing is disabled, size was 74381 bytes
```

Note `CteationTime`. The typo is in the O2 build, and Thanasis's `ctf_written`
regex requires `CreationTime:`. So that parser would miss even a run where
writing is enabled, if the typo is present in the deployed O2 version. Confirm
against the O2 version the farm runs before porting it.

### High-value lines that no parser covers

The gap is not only the zero-scoring parsers. These shapes carry numbers and
have no regex at all:

```
New error registered at bc/orbit 0/12 on the FEEID:0x2001 chip#4: DColumns non-increasing
MFTDecoder registered new link link cruID:0x0/lID3 feeID:0x2001 RUSW=5
CTF 43 size report: ITS:N/A TPC:N/A ... MFT:2331,1094,68 MCH:N/A ...
```

The first is the decoder error stream, 1,479 lines in one sampled program,
carrying a FEE identifier, a chip number and an error class. That is a
per-component fault signal and it is exactly the subject Item 1 says we are
missing. Write these three before porting any ITS parser.

---

## System logs

### journald is live and is real data

The journal on `epn146` holds at least 30 days. The 40,000-entry cap was reached
after 19 days, so the real depth is larger. Priorities seen: 6 (info) 38119,
5 (notice) 1222, 4 (warning) 659.

Sources, by share: `slurmd.service` 65%, `init.scope` 26%, `crond.service` 5%,
`kernel` 1.4%.

**This is the one log type we do not need to replay.** Unlike the Run 3 physics
logs, journald is being written right now on every machine we own. Point Fluent
Bit's `systemd` input at it and the data is live.

### The kernel ring is the reason to bother

774 kernel warnings over 16 days. The largest group is eight multi-line traces
of this shape:

```
WARNING: CPU: 12 PID: 3140 at drivers/iommu/dma-iommu.c:1203 iommu_dma_unmap_page+0x..
CPU: 12 PID: 3140 Comm: TfBuilder Kdump: loaded Tainted: G  W  OE
Hardware name: Supermicro AS -4125GS-TNRT-CE1/H13DSG-O-CPU
Call Trace: ...
```

An IOMMU DMA fault, raised inside the **TfBuilder** process. An O2 program name
appearing in a kernel stack trace is precisely the correlation that adding system
logs is for, and no OpenStack VM will ever produce it.

Two requirements follow: a multiline rule, because one trace is roughly twenty
lines; and a parser that keeps `Comm:` as a field, because that is the join key
to the job logs.

### /var/log adds little

`/var/log/messages` on `epn146` is 71% `slurmd` and 28% `systemd`, both of which
are already in the journal. Do not tail it — that would duplicate journald.

Two files are worth their own input:

- `/var/log/o2-infologger-daemon.log` — its own format,
  `YYYY-MM-DD HH:MM:SS.ffffff<TAB>message`, e.g. `New client: 557/2048`. That
  client count against the 2048 limit in `/etc/o2.d/infologger/infoLoggerD.cfg`
  is a saturation signal we have no other source for.
- `/var/log/secure` and `/var/log/audit` — out of scope. Someone else owns them.

`infoLoggerD` on `epn146` ships to `serverHost=epn-infra13`, the node we were
allocated for the storage tier. Confirm with Lubos which InfoLogger server that
is before we place anything there.

---

## What the archive says that the node survey could not

Added 4 September 2026, from the 45,596,613-line archive corpus in
`downloads/round6/`, which was pulled from the CERN S3 bucket and covers 2026
runs. The 27 August survey read `/scratch` on `epn146`, where every run
directory was written on 27 October 2022.

The two disagree, and the archive is the newer evidence.

| | 27 Aug survey, `/scratch` | archive corpus, S3 |
|---|---|---|
| Programs in the process tree | 13 | **186** |
| Detectors | MFT only | ITS, TPC, TRD, MCH, MID, EMC, FDD, FT0, FV0, HMP, ZDC, MFT |
| Newest run | 27 Oct 2022 | 20 Jun 2026 |
| DDS lines available | 43,972 in one run tag | 1,236,971 |

**This retracts one instruction from "What to build, in order".** Step 4 said to
drop the twelve ITS parsers "until ITS data exists on a node we can read". ITS
data exists: `its-tracker`, `its-stf-decoder`, `its-entropy-encoder`,
`ITS-ITSClusterTask-proxy` and `ITS-ITSTrackTask-proxy` are all in the archive,
with 566,587 lines from `its-tracker` alone. The reason for dropping them is
gone. Whether to port them is now an ordinary question about whether each
regex earns its cost, not a question about missing data.

It does not retract the survey. The survey answered a different question —
what is on a live worker's disk right now — and its answers about `/scratch`
being shared NFS, about journald, and about `/var/log` all still stand.

**What neither source answers** is where a live run writes its job logs today.
Every `/scratch` directory is from 2022 and the archive is a backup bucket. That
is a census question and it is still open.

---

## Census — 5 September 2026

Read-only, on all four allocated nodes, via `tools/epnsurvey/survey.sh`. This is
the dated live census Stage S0 requires. The 27 August survey read one node;
this reads four and asks four questions that one could not.

**Observer:** Marko Sladojevic. **Source owner approval: not obtained.** That is
a conversation with Lubos and Federico, not a measurement, and it is still open.

### The nodes

| Node | Tier | Release | Processors | Memory | Uptime | Fluent Bit |
|---|---|---|---:|---:|---|---|
| epn146 | worker | AlmaLinux 9.5 | 128 | 503 GB | 43 weeks | **4.0.1** |
| epn228 | worker | AlmaLinux 9.5 | 128 | 503 GB | 40 weeks | **4.0.1** |
| epn323 | worker | AlmaLinux 10.2 | 192 | 1007 GB | 46 days | **4.0.14** |
| epn-infra13 | storage | AlmaLinux 9.5 | 64 | 376 GB | 31 weeks | **3.2.8** |

🔴 **Four Fluent Bit versions are deployed, not two.** The storage node runs
**3.2.8**, two majors behind everything else. Nothing in this repository has
ever been tested against it. `epn-infra13` runs no collector today, so it does
not break anything now, but any decision to collect on the storage tier — the
ODC log below is the obvious candidate — inherits that version.

**Every one of the four builds has the `systemd` input compiled in.** The
journal is deployable on all of them, and the role's probe will find it.

### No run was active, and no job log is being written

`ps` on all four nodes found only infrastructure: `o2-infologger-daemon`,
`o2-infologger-server`, the CCDB Java service, `o2-epn-shm-manager`, and on the
storage node `odc-grpc-server`. **No DPL device, no TfBuilder, no dds-agent.**

`/scratch/jl` holds **54,450 files on every node, every one written in October
2022**, unchanged since the August survey. Nothing was open for writing under
it by any process.

**So the open question "where does a live run write its job logs" has no answer
today, because no run is running.** That is a better answer than the guess it
replaces, and it is falsifiable: repeat this census during a run and the open
file descriptors of the DPL devices will say directly. `survey.sh` now reads
`/proc/<pid>/fd` for exactly that purpose.

### A live O2 source nobody is collecting

`odc-grpc-server` on `epn-infra13` writes
`/var/log/odc/staging/odc_<date>.<n>.log`: **11.5 MB a day, 9.1 GB retained,
last written minutes before this census.** It is the run orchestrator — the
component that starts DDS sessions — and it is the only O2 process writing a log
on the farm right now.

Its format is close to DDS but not identical: date, three-letter severity,
program, **process identifier**, message, where DDS has a thread identifier.

**It is integrated now.** The 5 September census left this at a routing
decision; the 6 September pass read the real files and built it. See the `odc`
routing decision record below for the format, the recipe, the measured coverage
and the overlap with InfoLogger.

What the second reading changed. The first census sampled an idle day and found
one severity and two shapes, and concluded it was a heartbeat. Sampling 25 of
the 560 retained files instead found **five severities and 2,612,951 lines**:
1,650,555 `inf`, 961,933 `dbg`, 312 `wrn`, 153 `err` and 8 `fat`. The file for
20 January 2026 carries run numbers 2304 and 2305, two `fat` lines for a STOP
transition that timed out, and 36 `err` lines naming the collections that
failed. Idle it is a heartbeat; that is a statement about the day sampled, not
about the source.

🔴 **One thing has not changed: it lives on the storage node, which runs no
collector.** The shipped configuration is inert on every worker, because the
glob matches nothing there. Putting a collector on `epn-infra13` is a deployment
decision and is not taken here.

**What did change is the version worry.** `epn-infra13` runs Fluent Bit 3.2.8
and nothing in this repository had ever been run against it. It has now been:
all 35 fixture expectations, the restart check and the rotation check pass on
3.2.8 exactly as they do on 4.0.1 and 4.0.14. The remaining blocker is the
deployment decision alone.

🟢 **The role no longer installs it.** `fluent_bit_version` defaulted to 5.0.8,
and `epn-infra13` pins nothing in the inventory — so the default is what this
node would have used. It is now `4.0.14`, and the role asserts against a
blocked-prefix list before the package task rather than trusting a comment.

🔴 **The version that fails is 5.0.8, not 3.2.8.** It loses bytes appended to a
file after `logrotate` has renamed it away — which is what this source does
every midnight. No farm node runs 5.x; the soak rig defaults to it. Round 10 in
docs/SOAK_RESULTS.md has the isolation. **Do not upgrade a collector that tails
a rotating file to 5.0.8 without re-testing this.**

### Other live files under /var/log, none of them collected

| File | Node | Size | Owner |
|---|---|---:|---|
| `slurm/slurmctld.log` | epn-infra13 | **12.9 GB** | batch scheduling |
| `telegraf/telegraf.log` | epn-infra13 | 5.5 GB | metrics agent |
| `messages` | epn-infra13 | 882 MB | already in the journal |
| `ccdb/ccdb-memory.log` | epn228 | 902 MB | conditions database |
| `ccdb/ccdb-memory.log` | epn146 | 345 MB | conditions database |
| `slurm/slurmd.log` | epn146 | 55 MB | batch scheduling |

None is an O2 log. Slurm and telegraf are farm infrastructure and telegraf is a
metrics agent, which `deploy/README.md` already places outside this project
because Mimir owns machine health. CCDB is a service log and a candidate if
anyone asks for it. **They are recorded here so that "we did not know" is never
the reason one of them is missing.**

### The journal, measured properly

🔴 **The first attempt measured the wrong thing.** `journalctl` as `masladoj`
shows only that user's own sessions — it says so, in a hint that is easy to
scroll past — so the first pass reported 1,000 to 8,000 entries a node and no
kernel entries at all. Read with `sudo`, the same journals hold:

| Node | Info (p6) | Notice (p5) | Warning (p4) | Error (p3) | Kernel | On disk |
|---|---:|---:|---:|---:|---:|---:|
| epn146 | 71,964 | 2,518 | 1,992 | 0 | **986** | 797 MB |
| epn228 | 76,734 | 2,741 | 972 | 0 | 15 | 1.4 GB |
| epn323 | 26,765 | 2,515 | **284,552** | 0 | 17 | 411 MB |
| epn-infra13 | **12,957,663** | 21,954 | 15,615 | 34 | 306 | 3.9 GB |

Thirty days. Three consequences.

**A worker writes 2,500 to 10,500 journal entries a day.** Against millions of
process-tree lines, collecting all of it is free. That is why the unit
allow-list is gone: Lubos asked for all system logs, and the measurement says
the cost of obeying him is nothing.

**The allow-list would have created two blind spots.** It named slurmd, crond,
fluent-bit, opensearch and alice-replay. On epn323 that drops NetworkManager,
which is 172,935 of its entries; on epn-infra13 it drops slurmctld, which is
nearly all of that node's 13 million.

**epn323 is emitting 9,485 warnings a day and epn146 and epn228 are not.**
Under priority routing those all reach durable storage. That is the routing
working, not failing — epn323 is a month old, is a major release ahead, and is
telling us something the other two are not. It is worth a look before anyone
decides to filter it.

**986 kernel entries on epn146, and ten of them name a process.** The IOMMU
trace is real and current: `WARNING ... iommu_dma_unmap_page` with
`Comm: TfBuilder`. That is the correlation the journal was added for, and it is
now reproduced off the farm — see `tools/collector/replaycheck.py --journal`.

### The InfoLogger daemon log, and two things the earlier record got wrong

Present on epn146, epn228 and epn-infra13. **Absent on epn323.**

🔴 **The separator is spaces, not a tab.** `docs/LOG_TYPES.md` recorded
`YYYY-MM-DD HH:MM:SS.ffffff<TAB>message` and the parser required `\t`. The real
file uses whitespace, so that parser matched **none** of epn146's 162,642 lines.

🔴 **The volume is not "a handful of lines a day".** 162,642 lines spanning
10 March to 1 September — **about 930 a day**, growing without bound: no
`logrotate.d` entry covers this file, only rsyslog's own.

The file has exactly two shapes, and an extractor anchored on the word `client`
matched only the first:

```
New client: 557/2048
4 clients disconnected, now having 962/2048
```

**58,175 lines, 36 % of the file, were silently losing their client count.** The
extractor now anchors on the count at the end of the line, which both shapes
share.

The peak seen is **1025 clients of 2048** — half the ceiling in
`infoLoggerD.cfg`. The saturation signal is real and has headroom worth
watching.

### Rotation

`journald` is `Storage=persistent`. `logrotate.d/rsyslog` covers `messages`,
`secure`, `cron`, `maillog` and `spooler` — all of which this project excludes.
**Nothing rotates `/var/log/o2-infologger-daemon.log`.** The ODC log rotates by
filename, daily, with a numeric generation.

### What the census did not settle

- **Source-owner approval.** Not a measurement.
- **The live job-log path.** Unanswerable while no run is active. The instrument
  to answer it now exists.
- **Whether ODC should be collected**, and on what, given 3.2.8 on the only node
  that has it.

---

## The two clocks, across all seven sources

Every record carries two times and they answer different questions.
`@timestamp` is the event's own time, from the source. `collector_time` is
stamped as the record passes through the collector. Their difference is the
machine-to-collector latency, and four detectors train on it.

Every source added since round 6 was added to that model, not around it. The
`collector_time` filter matches
`^(dds|stdout|infologger|ildaemon|journald|odc)$`, and every body parser that
can supply an event time declares a `time_key`.

| Source | Where `@timestamp` comes from | `collector_time` |
|---|---|---|
| `infologger` | `timestamp`, epoch with milliseconds, a named column | yes |
| `dds` | the full date at the head of the line | yes |
| `datadist` | its own bracket, full date with milliseconds | yes |
| `dpl` | the replay engine's prepended event time. **On a live EPN there is none** | yes |
| `ildaemon` | the full date at the head of the line | yes |
| `journald` | the journal's own entry timestamp, set by the input | yes |
| `odc` | the full date with microseconds at the head of the line | yes |

The extractors — `dds_slot`, `dds_channel`, `dds_task`, `mft_decoder_error`,
`ildaemon_clients`, `kernel_comm` — deliberately declare **no** `time_key`. They
run over `message` after the envelope is parsed, and a `time_key` there would
overwrite an event time that was already correct.

### The one place the model degenerates

`dpl` is the exception and it is structural. O2 prints a clock with no date, so
a line read from a live EPN carries no recoverable event time. Those records
take **ingest time** as `@timestamp` and keep O2's clock as `log_time`, a
keyword, deliberately not a date.

That is close to the truth rather than far from it — the tail reads within
seconds of the write — but it is not the same thing, and it means
`enter_system_lag_ms` on a live process-tree record measures the tail's own
refresh interval rather than the machine.

**Reconstructing it from the file name's date plus `log_time` was considered and
rejected.** The pipeline sees one record at a time and cannot know that a run
crossed midnight, so every line after midnight would be dated a day early. The
replay engine can do it because `_StdoutClock` carries state across the file;
an ingest processor cannot. A wrong date is worse than a date that is honestly
a few seconds late.

### What this means under replay

Under `replay_clock: preserved`, `@timestamp` keeps the archive's own times, so
the derived lag is large by construction. Measured on the shipped configuration:

| Source | Mean `enter_system_lag_ms` under replay |
|---|---:|
| `dds` | 65 days |
| `stdout` | 78 days |
| `ildaemon` | 78 days |
| `infologger` | 443 days |

**This is the documented behaviour, not a regression.** `group_vars/all.yml`
already records that `enter_system_lag_ms` is production-oriented and is not a
valid latency signal under preserved replay, and the month-scale guard in the
detectors exists precisely so that preserved is safe. The two new sources
behave exactly like the three that were already there.

---

## Routing decision records

Written 4 September 2026, against the configuration in
`deploy/roles/collector/templates/`. Every source the collector reads has a row
here. A source with no row is not collected.

Volume shares are measured on the 45,596,613-line archive corpus, not on a day
of farm traffic. The farm's daily figure is not known and is a census question,
so the share is what these rows carry.

### `infologger`

| Field | Decision |
|---|---|
| Program identity | `facility`, already a named column; copied to `program` by the ingest pipeline |
| Timestamp | `timestamp`, epoch seconds with milliseconds, carried by the record |
| Clock domain | event time from the archive, 2024 to 2026 |
| Severity | `severity`, one letter, already a named column |
| Informational | durable |
| Warning | durable |
| Error | durable |
| Multiline | none; one row is one record |
| Duplicate ownership | one TCP connection per collector, row-level routing in the replay wrapper |
| Volume share | 58.1 % of the corpus |
| Durable reason | the only source designed as a log. It carries the run number, so it is the join to physics, and it is what the operator cockpit is built on. |

### `dds`

| Field | Decision |
|---|---|
| Program identity | third column, the agent or workflow; captured as `program` |
| Timestamp | full date with milliseconds, in the line |
| Clock domain | event time from the archive |
| Severity | fourth column, three-letter code |
| Informational | node-local (`inf`, `dbg`) |
| Warning | durable |
| Error | durable |
| Multiline | `dds_multiline`; a line not starting with a date continues the one above |
| Duplicate ownership | one firehose file per node, named after the node |
| Volume share | 0.1 % of the corpus, and that share is wrong for production — operations report DDS is the highest-volume family during data-taking |
| Durable reason | ground truth for what was supposed to be running. `err` is 13.5 % of the family, which is a real cost and rests on a corpus that under-samples DDS. |

Slot, channel and launched-task extraction runs over `message` only, so the
envelope is parsed once rather than four times. `task` captures the launched
binary and not the whole command line: an executed-task line averages 3,487
bytes of O2 arguments, `message` already holds all of them, and copying them
into a second field cost 20.8 % of the entire family.

### `dpl` — the O2 process-log tree

| Field | Decision |
|---|---|
| Program identity | the file name the farm gives the log, `<program>[_t<slot>][_reco<N>]_<start>_<pid>_{out,err}.log` |
| Timestamp | replay prepends a full event date; a live tail has only O2's clock, which has no date, so those records take ingest time and keep the clock in `log_time` |
| Clock domain | mixed. Replay is archive event time; a live tail is ingest time until the farm's own job-log path is resolved. |
| Severity | `[SEVERITY]` in the second bracket, ANSI-tolerant |
| Informational | node-local (`INFO`, `DEBUG`, `TRACE`, ROOT's `Info`) |
| Warning | durable |
| Error | durable. `STATE` and `ALARM` are durable too. |
| Multiline | `stdout_multiline`; an indented line continues the one above |
| Duplicate ownership | each worker tails only its own directory. `/scratch` is one NFS export mounted on every worker, so a pattern one level wider would ingest every line once per node. |
| Volume share | 41.8 % of the corpus, of which 99.6 % is this format |
| Durable reason | severity, not source. 3.06 % of the tree is warning or worse. |

One shape the survey found uncovered is extracted here: the decoder error
stream (`bc`, `orbit`, `feeid`, `chip`, `decoder_error`), 989 lines in
21,200,248 and every one of them from `its-stf-decoder` or `mft-stf-decoder`.
It costs nothing measurable — see `docs/SOAK_RESULTS.md`, where it sits inside
the benchmark's own control arm.

The other two are **not** built, and the reason is the same for both: link
registration matched **zero** of those 21,200,248 lines, and so did the CTF size
report. Both shapes are from the October 2022 data on `/scratch` and the newer
O2 build does not print them.

The size report has a second reason on top. It could not be a collector parser
even if it did appear — Onigmo cannot return a repeated capture, so a
variable-length detector list cannot come out of one regex, and the collector
runs no per-record Lua by design. It would have to be an ingest-pipeline
processor. **That was not built either**, because writing a Painless processor
for a line shape with no occurrences would mean inventing the meaning of its
three numbers from nothing. If the shape returns, the coverage tool will show
it as an unclassified shape and name it.

### `datadist` — DataDistribution and `TfBuilderTask`

| Field | Decision |
|---|---|
| Program identity | same file-name rule as `dpl`; the two share one tail input |
| Timestamp | its own bracket, full date with milliseconds. The only O2 format that needs no repair. |
| Clock domain | event time, in the line, in both replay and live |
| Severity | one letter, `I` `D` `W` `E` `F` |
| Informational | node-local (`I`, `D`) |
| Warning | durable |
| Error | durable |
| Multiline | shares `stdout_multiline` |
| Duplicate ownership | as `dpl` |
| Volume share | 0.44 % of the process tree |
| Durable reason | TfBuilder assembles the timeframes everything downstream reconstructs. A dropped timeframe or a failed region allocation is a run-stopping event. |

### `ildaemon` — `/var/log/o2-infologger-daemon.log`

| Field | Decision |
|---|---|
| Program identity | constant, `o2-infologger-daemon` |
| Timestamp | full date with microseconds, in the line |
| Clock domain | live node time |
| Severity | none. The file has no severity column. |
| Informational | durable |
| Warning | durable |
| Error | durable |
| Multiline | none |
| Duplicate ownership | one explicit absolute path, never a wildcard over `/var/log` |
| Rotation | **none.** No `logrotate.d` entry covers this file; it grows without bound |
| Volume | about 930 lines a day, measured over 162,642 lines spanning 10 March to 1 September 2026 on epn146 |
| Present on | epn146, epn228, epn-infra13. **Absent on epn323** |
| Durable reason | severity does not decide this one. The connected-client count against the ceiling in `infoLoggerD.cfg` is only useful as a series that survives the node, and nothing else reports how close the farm is to running out of log sockets. epn146 has been seen at 1025 of 2048. |

This is the one source where a source-specific rule overrides severity, and the
reason is that it has no severity to route on.

### `journald`

| Field | Decision |
|---|---|
| Program identity | `COMM` from the journal, or `Comm:` parsed out of a kernel trace; copied to `program` by the ingest pipeline |
| Timestamp | the journal's own, carried by the input |
| Clock domain | live node time. This is the one source that needs no replay. |
| Severity | `PRIORITY`, 0 to 7, copied to `severity` |
| Units collected | **all of them.** Lubos asked for all system logs and the census priced it at nothing |
| Fields kept | fourteen, by allowlist. A journal entry arrives with about twenty-five and the corpus holds 234 distinct ones; the mapping is not dynamic, so the rest would be stored and never searchable |
| Informational | node-local (5 to 7) |
| Warning | durable (4 and below) |
| Error | durable |
| Multiline | `kernel_trace`, as a **filter**; 5.0.8 rejects it as an input property |
| Duplicate ownership | each node reads its own journal; there is no shared journal |
| Volume | 2,500 to 10,500 entries a day a worker. epn146: 71,964 info, 1,992 warning, 986 kernel in 30 days. epn323: 284,552 warnings in 30 days, nearly all NetworkManager |
| Exercised | yes, off the farm, against epn146's own journal: 75,243 records, 74,023 local, 1,220 durable, and the kernel traces name `TfBuilder` |
| Durable reason | an IOMMU fault naming `TfBuilder` is the correlation that adding system logs is for, and no OpenStack test machine can produce one. |

### `odc` — the run orchestrator

| Field | Decision |
|---|---|
| Program identity | captured from the line, `odc-grpc-server` on every one of 2,612,951 sampled lines |
| Timestamp | full date with microseconds, in the line |
| Clock domain | live node time |
| Severity | three-letter codes, the same alphabet DDS uses: `inf`, `dbg`, `wrn`, `err`, `fat` |
| Informational | node-local (`inf`, `dbg`) |
| Warning | durable |
| Error | durable |
| Multiline | `odc_multiline`. The orchestrator embeds the topology-generation script's stderr in one record; 199 of 153,400 lines on a busy day are those continuations |
| Extra fields | `partition`, `run`, `pid`. **`partition` and `run` are the reason to collect this at all** |
| Duplicate ownership | one directory on one machine. The tail reads it through a glob; on a worker the glob matches nothing, exactly as the daemon-log input already finds nothing on epn323 |
| Rotation | daily, by date, into `odc_<date>.<n>.log`. The collector reads the directory through a glob, and `tools/collector/replaycheck.py --rotate` exercises a rename under a running collector. **Verified on 3.2.8, 4.0.1 and 4.0.14; it FAILS on 5.0.8**, which loses bytes appended to a file after it has been renamed away — see docs/SOAK_RESULTS.md round 10 |
| Volume | 11.5 MB and about 100,000 lines a day; 560 files retained, back to March 2025 |
| Present on | `epn-infra13` only |
| Exercised | yes, on real data: 190,052 records from a busy day and an idle day, 100 % parsed, 0 % unclassified |
| Durable share | 0.02 %. Measured over 2,612,951 lines: 1,650,555 `inf` and 961,933 `dbg` stay local, against 312 `wrn`, 153 `err` and 8 `fat` |

**It overlaps InfoLogger, and that is priced rather than ignored.** The
orchestrator forwards to InfoLogger under `system: ODC` — one 2024 archive
partition holds 108,757 such rows out of 275,205. So its `inf`, `err` and `fat`
lines already reach OpenSearch by another route.

Collecting the file is still right, for two measured reasons:

1. **The debug tier is not forwarded.** InfoLogger's ODC rows are `I`, `E` and
   `F` only. The file is 36.7 % `dbg`, and that is where the state transitions
   and the device-state counts live.
2. **The partition and run attribution is far more complete in the file.** Of
   108,757 forwarded rows, 509 carry a run number and 7,751 carry a partition.
   In the file, 46 % of a busy day's lines carry both, as an explicit
   `partition:run` column.

The duplication costs almost nothing because of where the severity split puts
it. The lines that exist in both places are overwhelmingly `inf`, and `inf`
stays node-local; on the durable tier the overlap is 38 lines out of 190,052.

🔴 **The blocker is real and is not the parser.** `epn-infra13` runs no
collector today and runs **Fluent Bit 3.2.8**, two majors behind everything
else. The parser, the routing, the mapping, the recipe and the tests are all
shipped and green, and the role enables the input only where the file exists —
so nothing happens on a worker. Turning it on means either putting a collector
on the storage node or moving the file. **That is a deployment decision, not
work, and it is the user's to make.**

### Explicit exclusions

| Source | Why not |
|---|---|
| `/var/log/messages` | 71 % slurmd, 28 % systemd, both already in the journal. Tailing it would double-count. |
| `/var/log/secure`, `/var/log/audit` | out of scope; someone else owns them |
| another worker's `/scratch` directory | cross-worker log shipping under another name |
| host metrics | Mimir owns machine health at CERN |
| `ccdb/ccdb-memory.log` | the conditions database is a service we consume, not an O2 process on the data path. 902 MB on epn228 and 345 MB on epn146, and no operator has asked for it. It is a Java garbage-collection log, so its lines answer questions about the CCDB service's own heap and nothing about a run. Reversible: it is one tail input and one parser if the owners ask. |
| `slurm/slurmctld.log`, `slurm/slurmd.log` | batch scheduling, not O2. 12.9 GB on epn-infra13. The parts that matter — a unit failing to start a job — are already in the journal, which **is** collected |
| `telegraf/telegraf.log` | a metrics agent's own log. `deploy/README.md` puts machine health outside this project |

### What is not decided here

Raw-log routing and template-catalog routing are separate decisions. The
collector routes raw logs and decides nothing about the catalog; a tag in the
collector has no effect on which templates exist.

That separation left a gap, and the gap is now closed by
`deploy/roles/template_catalog`. Informational lines stay on the node, so a
catalog built from the durable tier alone would never see the templates of
96.9 % of the process tree. The role mines templates **on the worker**, from
that worker's own records, and sends only the canonical template text and its
counts. A raw informational line still never crosses the network.

Four things it has to get right, each of which was got wrong first and is now
tested:

- **The family label.** The split between `dpl` and `datadist` is read off the
  FIELDS the collector emits, not off the message text. The first build tested
  the text against DataDistribution's `[date][S] ` prefix — which the
  collector's own parser has already eaten into `time` and `severity` by the
  time the record is indexed, so the test could never match and every
  process-tree template was filed as `dpl`.
- **Which routes it reads.** Local, durable and InfoLogger, not local alone.
  Reading only the node-local index meant no InfoLogger templates at all, no
  daemon log, and none of the warnings and errors — the exact records the
  routing sends straight to durable storage. The two shared indices are read
  scoped to the records this node wrote, so three workers do not each count the
  same line.
- **The resume.** The watermark carries the document identifier as well as the
  time. A watermark holding only a time and resumed with a strict
  greater-than drops every record that shared the last millisecond of the
  previous pass, and a worker writes many records in one millisecond.
- **The mining state.** The drain tree is persisted between timer runs. Drain
  is incremental and order-dependent, so a tree rebuilt from empty every ten
  minutes mines the same messages into different templates depending on which
  batch they fell in. It also installs the same FLOAT/NUM merge rule the offline
  miner uses; without it a worker and the archive produce different text for the
  same template.

---

## What to build, in order

Each step names what it needs. Only steps 5 and 6 need anything new from the EPNs.

**Status, 4 September 2026.** Steps 1, 2, 3, 5 and 7 are built and checked
against real Fluent Bit 4.0.1, 4.0.14 and 5.0.8 by
`tools/collector/replaycheck.py`. Step 4 is reopened rather than done — see
"What the archive says" above; the reason for dropping the ITS parsers is gone,
and which of the remaining ones earn their cost is an open measurement, not a
port. Step 6 is not done and needs an EPN.

1. **Un-merge the process logs in the replay engine.** `replay_tarballs`
   (`images/replay/replay.py:385`) writes every `_out.log` member into one
   `stdout/<host>.log`, so the program name is destroyed before the collector
   sees it. Write each member to `stdout/<host>/<basename>` instead, and raise
   the caps of 3 objects, 4 members and 2000 lines per member. This unlocks
   Items 1 and 2 and needs no EPN data.

2. **Add the DataDistribution parser.** Full date, millisecond, single-letter
   level. It is R1-clean as written and covers TfBuilderTask outright.

3. **Add the three MFT extractors** named above: decoder error, link
   registration, per-detector CTF size report.

   **One of the three is built, and the other two were dropped on evidence.**
   Link registration and the CTF size report match nothing in the 2026 archive.
   Shipping them would repeat the mistake this document already warns about in
   step 4: twelve parsers nobody can test. See the `dpl` routing record.

4. **Port only the 13 parsers that matched.** Drop the 12 ITS parsers until ITS
   data exists on a node we can read. Rewrite every `Time_Format %H:%M:%S` per
   R1 before it enters `parsers.yaml.j2`, and give every extracted number a
   strict mapping in `templates.sh.j2`.

5. **Turn on the `systemd` input**, with an ANSI-tolerant rule and a multiline
   rule for kernel traces. Needs a check that the packaged Fluent Bit build has
   the `systemd` input compiled in.

6. **Capture a journal bundle for replay** on the OpenStack VMs, so EPN-only
   faults such as the IOMMU trace can be replayed where the VMs cannot generate
   them. `survey.sh` already produces this bundle.

   **Built, 6 September 2026.** `tools/epnsurvey/mkbundle.sh <node> <dir>`
   captures the journal AND the InfoLogger daemon log — the two sources with no
   S3 archive behind them. `REPLAY_BUNDLE_SRC=<dir>` ships it; the producer role
   paces the daemon log into the path the collector tails, and the collector
   points its `systemd` input at the shipped journal. With no bundle both
   families skip and say so, which is the ordinary case on a VM.

   The journal is copied as journal **files**, not as an export: `libsystemd`
   reads the binary format, so a JSON export would have to be re-parsed by
   something that is not the production input.

7. **The catch-all tail**, per Lubos's future-proofing: one `*.log` tail over
   this node's own run directory, with `Exclude_Path` for the programs that have
   named inputs. A new program is ingested the day it appears. Add a periodic
   count of files no named input claims, so a new program is visible rather than
   silently absorbed.

   **Built differently, and the difference is the point.** There is no named
   input to exclude from, because there is only one input over the whole tree
   and the programs are told apart by `program`, recovered from the file name.
   `Exclude_Path` and a per-program input list would have had to move in
   lockstep; nothing does. The "count what no named input claims" requirement is
   met instead by `tools/collector/coverage.py`, which counts the lines no
   parser gave a severity to — 0.17 % of 20.9 million, and it names the shapes.

---

## Open questions

1. **The clock — answered on 6 September 2026, and the answer is that no offset
   should be built.**

   The windows, measured rather than assumed: the archive process tree and DDS
   are 2026-06-20, InfoLogger spans 2024 to 2026, and a journal captured from
   epn146 spans 2026-08-06 to 2026-09-05. Under `replay_clock: preserved` a
   replayed O2 error and a captured kernel fault land about two and a half
   months apart, so no correlation window of hours can span them.

   Three ways to close it were considered and all three are worse than leaving
   it open.

   **Shift the archive forward** — `replay_clock: shifted` already does this.
   `deploy/group_vars/all.yml` explains at length why it is not the default:
   `enter_system_lag_ms` is `collector_time - @timestamp`, so rewriting
   `@timestamp` collapses the one measurement that needs the original times, and
   four detectors then train on a constant.

   **Shift the journal backwards** into the archive window. This is the one that
   sounds right and cannot be built: `libsystemd` reads the binary journal
   format, and a captured journal is replayed by being present rather than by
   being written. Rewriting its timestamps means regenerating a journal with
   `systemd-journal-remote`, which is a new component in the replay path for the
   sake of a staging convenience.

   **Capture a journal from the same window as the archive.** The archive run is
   June; a journal from June no longer exists on the nodes. epn146 retains about
   30 days.

   **So the correlation is not available on a staging VM, and it does not need
   to be.** On a real EPN the journal and the job logs are both live and land in
   the same second, which is where an IOMMU fault naming `TfBuilder` is actually
   read. The VM replay exists to exercise the pipeline — parsing, routing,
   mappings, restart — and it does all of that with the two clocks apart.

   What this costs is one thing, and it is worth stating plainly: **a
   cross-source correlation detector cannot be developed against replay alone.**
   It has to be developed against a real node.

2. **Whether a live run ever lands here.** Every run under `/scratch/jl` is from
   Oct 2022. If staging runs on `epn146` and `epn323` write job logs somewhere
   else, that path has not been found yet and the survey should be repeated
   during a run. Still open on 4 September 2026, and it is the one Stage S0 gate
   condition that no amount of archive work can close.

3. **Which InfoLogger server `epn-infra13` is.** Our allocation names it as our
   storage tier, and `infoLoggerD.cfg` names it as the InfoLogger destination.
