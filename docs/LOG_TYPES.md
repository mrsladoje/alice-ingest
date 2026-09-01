# New log types — EPN survey and integration plan

The Item 1 gate from `docs/THANASIS_PLAN.md` is closed. The survey ran on
`epn146` on 27 Aug 2026 and the results are below.

Tools: `tools/epnsurvey/survey.sh` (read-only capture, runs on an EPN) and
`tools/epnsurvey/regex_report.py` (scores a parser library against the bundle).

Source of the scope: `LUBOS_MEETING.md` item 3, and `docs/THANASIS_PLAN.md`
Items 1 and 2.

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

## What to build, in order

Each step names what it needs. Only steps 5 and 6 need anything new from the EPNs.

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

7. **The catch-all tail**, per Lubos's future-proofing: one `*.log` tail over
   this node's own run directory, with `Exclude_Path` for the programs that have
   named inputs. A new program is ingested the day it appears. Add a periodic
   count of files no named input claims, so a new program is visible rather than
   silently absorbed.

---

## Open questions

1. **The clock.** Job logs carry Oct 2022 event times. The journal carries
   Aug 2026. Replayed together with their original timestamps they land four
   years apart, and no correlation between an O2 error and a kernel fault is
   possible. The system family needs an offset that maps the capture window onto
   the replayed run window.

2. **Whether a live run ever lands here.** Every run under `/scratch/jl` is from
   Oct 2022. If staging runs on `epn146` and `epn323` write job logs somewhere
   else, that path has not been found yet and the survey should be repeated
   during a run.

3. **Which InfoLogger server `epn-infra13` is.** Our allocation names it as our
   storage tier, and `infoLoggerD.cfg` names it as the InfoLogger destination.
