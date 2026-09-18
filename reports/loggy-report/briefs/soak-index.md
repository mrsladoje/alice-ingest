# Soak index: the five soak briefs reconciled

Sources: docs/SOAK.md (round 1, 191 lines) and docs/SOAK_RESULTS.md (rounds 2 to 21, 6,107 lines). Every line number below points into docs/SOAK_RESULTS.md unless the path docs/SOAK.md is given. The five briefs are reports/loggy-report/briefs/soak-1.md to soak-5.md. [A] marks a fact for the report. [C] marks evidence only.

Three rules bind every number in this index.

1. Every number comes from a laptop. Shapes, knees and rankings transfer. Absolute rates and core-seconds do not (docs/SOAK_RESULTS.md:265-267). The conversion factor to a farm worker was never measured (docs/SOAK_RESULTS.md:289-290).
2. Two rigs exist inside round 2. The healthy rig ran Stage A and Stage B at 20,000 records a second. The saturated rig ran the 27 August re-run at 5,000 records a second (docs/SOAK_RESULTS.md:180-197). Core-seconds from the two rigs must never sit in one comparison.
3. Two Kafka decisions exist and stay apart. The soak rejected a bus between the collector and OpenSearch on measured numbers (docs/SOAK_RESULTS.md:1852-1979). The September 2026 review separately agreed a message bus for the live lane only, to decouple the shifter view from every worker's collector configuration. That second decision is outside the soak file. Both stand.

## 1. The arc

- Round 1 (18 August 2026) asked four questions of the collector alone against a sink that always accepts. It found a read ceiling of about 53,000 records a second on two cores (docs/SOAK.md:13-14). Memory sat between 133 MB and 228 MB (docs/SOAK.md:18). The 256 MB buffer held 61 seconds at 20,000 a second (docs/SOAK.md:22-23). One flag mattered: `flush: 1` halves memory (docs/SOAK.md:155). It also found that every record lost in a sink outage was InfoLogger, because a socket has no file behind it (docs/SOAK.md:65-70). It changed the documentation: raising the disk buffer raises memory, so the two limits move together (docs/SOAK.md:97-99).
- Round 2 pre-flight asked what a worker really writes. Six months of archive answered. A median worker carries 23 records a second in the busiest hour, and the busiest worker-second is 78 (docs/SOAK_RESULTS.md:364-368). The farm peaks at 9,781 (docs/SOAK_RESULTS.md:374-379). The plan's 1,000 a second was a farm figure read as a worker figure, and the soak kept its rates as a safety margin (docs/SOAK_RESULTS.md:336-337, 233-234).
- Round 2 Stage A built the instrument and its noise floor: 1.61 % at 20,000 a second and 9.48 % at 1,000 (docs/SOAK_RESULTS.md:505-506). The instrument failed four times before any arm ran and was fixed each time (docs/SOAK_RESULTS.md:664-697).
- Round 2 Stage B screened the collector's own settings at 20,000 a second. Every threading arm cost more than the shipped one (docs/SOAK_RESULTS.md:739-742). A spool and a pause knob changed nothing about the InfoLogger loss (docs/SOAK_RESULTS.md:829-832, 887-893). The live lane was the largest single cost, 41 to 68 % more collector time (docs/SOAK_RESULTS.md:946-948). The collector alone took 33,000,000 records at 50,000 a second and lost none (docs/SOAK_RESULTS.md:1054-1070). Nothing from Stage B shipped (docs/SOAK_RESULTS.md:1113-1114).
- Round 2 Stages C to G ran on a rig that was quietly saturated. Every claim from Stage C onward is retracted (docs/SOAK_RESULTS.md:13-15, 90-108). A 47-cell re-run on 27 August at 5,000 a second replaced them and left one product change: flush from 5 seconds to 1 (docs/SOAK_RESULTS.md:19-31). Heap size and internal core placement make no measurable difference (docs/SOAK_RESULTS.md:110-111).
- Round 2 found the first product defect before Stage C: the shipped OpenSearch output re-sent bulks that were already indexed, 5.2 times duplication, fixed with a response buffer size (docs/SOAK_RESULTS.md:1494-1547, 168-169).
- Round 2 addendum measured the full stack's ceiling: about 42,000 records a second sustained, and 50,000 held for two minutes with zero loss (docs/SOAK_RESULTS.md:1824-1826). The decay after that is storage-tier merge pressure (docs/SOAK_RESULTS.md:1783-1802). Overload is safe: below the cap nothing is lost, at the cap the collector discards oldest-first and drains (docs/SOAK_RESULTS.md:1809-1822).
- Round 2 closed the first Kafka question on those numbers. The stack carries 538 times the busiest worker-second (docs/SOAK_RESULTS.md:1879). The collector's buffer already covers a 17.4-hour outage (docs/SOAK_RESULTS.md:1899-1902).
- Round 6 Stage H chose the template parser on cost and kept Drain3 (docs/SOAK_RESULTS.md:2005-2009). It rewrote the masker for 85 to 87 % less masking time with byte-identical output (docs/SOAK_RESULTS.md:2318-2322). It fixed one recipe per family (docs/SOAK_RESULTS.md:2246-2250). Its audit found that only 345 of 3,000,000 stdout lines carried a severity (docs/SOAK_RESULTS.md:2564-2565). So 42 % of all lines went to the small replicated tier (docs/SOAK_RESULTS.md:2569-2571).
- Round 6 Stage I put the recipe in the repository, recomputed the whole-corpus mining cost to 8.33 core-seconds per million lines, and showed the template count is a curve driven by runs sampled: 3,011, then 4,092, then 4,221 (docs/SOAK_RESULTS.md:2697-2706, 2468-2472, 2632-2652).
- Round 7 added three log sources the collector never read and split the process tree into two formats (docs/SOAK_RESULTS.md:3001-3003, 3014-3021). Severity now comes out of 99.83 % of process-tree lines, and 96.94 % of that family stays on the worker (docs/SOAK_RESULTS.md:3085, 3115-3116).
- Round 8 was the read-only census of four farm machines. It found a fourth collector version on the storage machine (docs/SOAK_RESULTS.md:3519-3527). It showed a worker's journal is 2,500 to 10,500 entries a day and removed the unit allow-list (docs/SOAK_RESULTS.md:3587-3593). It measured the last two recipes on farm data (docs/SOAK_RESULTS.md:3705-3739).
- Round 9, an external review, retracted every collector regex cost figure from rounds 6 to 8 as wall clock (docs/SOAK_RESULTS.md:4192-4197). It found the template catalog had shipped none of its guarantees (docs/SOAK_RESULTS.md:3962-4013).
- Rounds 10 to 17, 19 and 20 were further reviews. They found that Fluent Bit 5.0.8 loses appended bytes at rotation (docs/SOAK_RESULTS.md:4313-4318). They found that a retried bulk write duplicated every record (docs/SOAK_RESULTS.md:4919-4950). They took four answers to find the catalog's scan position (docs/SOAK_RESULTS.md:4841-4859). The product now carries a filter-assigned document identifier and writes with create (docs/SOAK_RESULTS.md:4937-4967).
- Round 18 priced the whole templating pipeline on all seven families and made it 31 to 57 % cheaper (docs/SOAK_RESULTS.md:5428-5429). Every one of 8,961,245 lines landed in the same cluster (docs/SOAK_RESULTS.md:5537). Round 21 priced the Forward hop into the stamper and made transport 15 % cheaper by splicing the record's own bytes (docs/SOAK_RESULTS.md:5933-5935, 6005-6022).

## 2. Figures of record

### 2a. Collector cost per record and per rate

The collector's cost per record falls as rate rises, because per-flush work is amortised (docs/SOAK_RESULTS.md:147-149). Per-record costs must never be compared across rates (docs/SOAK_RESULTS.md:1105-1109).

| Number | Meaning | Rig | Line | Level |
|---|---|---|---|---|
| 27.70 core-seconds per million records | The collector's own cost at flush 1, 5,000 a second, mean of three alternating runs | saturated | docs/SOAK_RESULTS.md:25 | A |
| 36.70 core-seconds per million | The same at the shipped flush 5; the change is −24.5 % | saturated | docs/SOAK_RESULTS.md:25 | A |
| about 105 core-seconds per million | The whole four-core stack at 5,000 a second; the collector is about a quarter of it | saturated | docs/SOAK_RESULTS.md:143-144 | A |
| 11.19 core-seconds per million, spread 1.61 % | The collector at 20,000 a second, three control runs; the noise floor at that rate | healthy | docs/SOAK_RESULTS.md:500-505 | A |
| 75.32 core-seconds per million, spread 9.48 % | The collector at 1,000 a second; at that rate the collector measures its own idling | healthy | docs/SOAK_RESULTS.md:506, 524-534 | A |
| 8.97 core-seconds per million, 0.352 of one core | The collector at 50,000 a second, 33,000,000 records, 0 lost, empty queue | healthy | docs/SOAK_RESULTS.md:1063-1065 | A |
| 0.25 of one core | The collector at 20,000 a second; three of four cores do nothing for it | healthy | docs/SOAK_RESULTS.md:559-563 | A |
| 89.3 % | Share of collector time in one single-threaded main loop | healthy | docs/SOAK_RESULTS.md:544, 550-552 | A |
| 41 % to 68 % | Extra collector cost when the live lane is on, healthy rig; 28 % on the saturated rig | both | docs/SOAK_RESULTS.md:946-948, 175-176 | A |

Decision on the flush 1 collector figure. "Read this first" prints three values for the same setting: 27.70 (line 25, three alternating runs), 24.13 (line 63, eight-value curve) and 25.10 (line 83, three-run basin). The Kafka section reuses 27.7 (docs/SOAK_RESULTS.md:1935-1937) and the pinning section reuses 27.8 (docs/SOAK_RESULTS.md:143). The report quotes 27.70 and names the run set. That table is the one that carries the 5-to-1 decision.

Decision on the threading percentages. Lines 740 and 742 print t1 at +11.5 % and t2 at +26.5 %. Line 1118 prints +11.4 % and +26.4 %. The two pairs come from the same cells on different bases: core-seconds (89.9 and 102.0 over 80.6) against per-million figures (12.48 and 14.16 over 11.20). The report quotes the table rows at 740 and 742, since they carry the measurements.

### 2b. Memory envelope

| Number | Meaning | Line | Level |
|---|---|---|---|
| 62.7 MB | Collector peak memory at flush 1, 5,000 a second, steady; 87.1 MB at flush 5, −28.0 % | docs/SOAK_RESULTS.md:26 | A |
| 61.1 MB to 105.7 MB | Peak memory across flush 0.125 to 10; memory rises with the flush interval | docs/SOAK_RESULTS.md:59-66 | A |
| 132.4 MB | Collector peak at 20,000 a second, flush 5, healthy rig, shipped threading | docs/SOAK_RESULTS.md:739 | C |
| 198.5 MB | Collector peak at 50,000 a second, healthy rig, shipped threading; the worst steady case measured | docs/SOAK_RESULTS.md:1066 | A |
| 133 MB to 228 MB | Round 1 working range on two cores against a fake sink | docs/SOAK.md:18 | A |
| 209 MB, 0 dropped, queue empty | Round 1 ten burst cycles of 30 seconds at 50,000 a second | docs/SOAK.md:48-50 | A |
| peak chunks 11, 9, 10, 10, 10, 10; 0 lost | Queue depth under 15,000 a second for 60 seconds, heaps 1g, 2g, 3g | docs/SOAK_RESULTS.md:1714-1718 | A |
| 74.6 MB to 78.0 MB | Collector peak memory under that burst, flat across heaps | docs/SOAK_RESULTS.md:1716-1718 | A |
| peak chunks 12, 9 and 39, 10 and 9; 0 lost | Queue depth under 30,000 a second for 30 seconds; the 39 is a machine-wide outlier | docs/SOAK_RESULTS.md:1723-1745 | A |
| 17 % of the 64-chunk cap, 6 % of the 256 MB backlog cap, drain at most 8 s | The closest any burst cell came to a limit | docs/SOAK_RESULTS.md:1735-1738 | A |
| 166 chunks, 210.7 MB (82 % of cap), 0 dropped, drain 171.5 s | Overload below the buffer cap at 50,000 a second | docs/SOAK_RESULTS.md:1809-1818 | A |
| 151 chunks, 260.9 MB (cap hit), 258,474 dropped (1.8 %), 0 failed retries, 0 errors, drain 262.8 s | Overload at the buffer cap | docs/SOAK_RESULTS.md:1809-1820 | A |
| 103 of 182 chunks on disk | The memory-to-disk spill under overload | docs/SOAK_RESULTS.md:1821-1822 | A |
| 264.5 MB and 269.0 MB | Buffer on disk at its peak in a fifteen-minute sink outage at 20,000 a second | docs/SOAK_RESULTS.md:825 | A |
| 61 s at 256M, 373 MB, 0 throttles; 491 s at 2G, 404 MB, 65,413 throttles | Round 1 sink outage at 20,000 a second; 1,001 queued chunks crossed the 384 MB `MemoryHigh` line | docs/SOAK.md:87-95 | A |

Standing parameters that appear in valid sections: 64 chunks in memory, 256 MB `storage.total_limit_size`, 384 MB `MemoryHigh` (docs/SOAK_RESULTS.md:1735-1736, 1749; docs/SOAK.md:94). The report may carry them even though Stage C is retracted.

### 2c. The flush choice

| Number | Meaning | Line | Level |
|---|---|---|---|
| 1 second | The chosen flush interval, from the shipped 5 | docs/SOAK_RESULTS.md:19 | A |
| 108.40 against 104.67, −3.4 % | Whole four-core stack, flush 5 against flush 1 | docs/SOAK_RESULTS.md:24 | A |
| 107.82 against 106.15 | The shipped arm's best run against the chosen arm's worst; the ranges do not overlap | docs/SOAK_RESULTS.md:30-31 | C |
| 5 s to 1 s | The live lane's latency floor | docs/SOAK_RESULTS.md:27 | A |
| 107.34, 103.50, 97.69, 92.84, 89.37, 90.39, 96.45, 105.33 | Total core-seconds at flush 0.125 to 10; a near-symmetric U with its floor at 1 | docs/SOAK_RESULTS.md:59-68 | A |
| 20.40 to 27.45 | Collector cost across the same eight values; it falls monotonically as flush shortens | docs/SOAK_RESULTS.md:59-66, 70-72 | A |
| 17.9 % | Best to worst across the 20× range | docs/SOAK_RESULTS.md:73 | A |
| 2.21 % and 0.71 % against a 0.85 % floor | Flush 1 over 0.5 and over 0.75; 0.75 and 1 are interchangeable | docs/SOAK_RESULTS.md:85-86 | A |
| 66 MB | Round 1: `flush: 1` halves memory, the one clear win among eleven flags | docs/SOAK.md:155 | A |

The flush value stands unchanged through round 21 (docs/SOAK_RESULTS.md:6093-6094).

### 2d. Heap

| Number | Meaning | Line | Level |
|---|---|---|---|
| 1 GB | The worker OpenSearch heap that ships | docs/SOAK_RESULTS.md:38-39, 1756-1758 | A |
| 2.1 % and 0.4 % against a 6.7 % floor | Re-measured effect of heap 2g; no effect | docs/SOAK_RESULTS.md:102 | A |
| 0 lost in every burst cell across 1g, 2g, 3g | Heap size does not change burst absorption | docs/SOAK_RESULTS.md:1714-1733 | A |

Bursts are absorbed by the collector's disk-backed chunk queue, not by OpenSearch heap (docs/SOAK_RESULTS.md:1747-1754).

### 2e. The sustained ceiling and the overload failure mode

| Number | Meaning | Line | Level |
|---|---|---|---|
| about 42,000 records a second | Sustained rate of the full stack on this rig, reproduced at 41,435 and 42,248 | docs/SOAK_RESULTS.md:1824-1825 | A |
| 50,000 a second for two minutes, zero loss | The peak on this rig | docs/SOAK_RESULTS.md:1826 | A |
| 50,042, 50,000, 34,500, 27,312, 17,875, about 11,800 | Rate per minute of the cleanest cell after 50,000 was offered | docs/SOAK_RESULTS.md:1783-1790 | A |
| collector 0.65 core, worker OpenSearch 2.16 cores; storage tier 3.44 of 4 cores (86 %) | Processor use while the rate decayed; nothing is processor-bound | docs/SOAK_RESULTS.md:1792-1796 | A |
| about six million documents | Index size at which merge pressure cuts indexing throughput; the ceiling belongs to the storage tier | docs/SOAK_RESULTS.md:1798-1802 | A |
| six cells | Full-stack cells at 50,000 a second for five minutes listed in the table | docs/SOAK_RESULTS.md:1769-1776 | C |
| about 53,000 a second on two cores | Round 1 collector-alone read ceiling against a sink that always accepts; the same plateau at a null sink | docs/SOAK.md:13-16, 41-44 | A |
| no number | Round 2 states no collector ceiling; nothing ran above 50,000 | docs/SOAK_RESULTS.md:1090, 1119 | A |

Decision on "eight cells". Line 1766 says eight; the table at 1769 to 1776 lists six. The report says six.

Failure mode under overload. Below the 256 MB cap the collector absorbs the whole shortfall and loses nothing (docs/SOAK_RESULTS.md:1809-1818). At the cap it discards oldest-first with no crash, no error and no failed retry, then drains to empty (docs/SOAK_RESULTS.md:1809-1822). Round 1 shows the same sequence against a bare OpenSearch: saturate, spill, pin at `MemoryHigh`, fill, discard (docs/SOAK.md:103-111). Round 1's 2,000 to 3,000 a second collapse rate must not be quoted (docs/SOAK.md:108).

### 2f. The Kafka margin and the buffer hold time

| Number | Meaning | Line | Level |
|---|---|---|---|
| 538× | The stack's sustained rate over the busiest worker-second, 42,000 over 78 | docs/SOAK_RESULTS.md:1879 | A |
| 4.3× | One worker's stack over the whole farm's peak second, 42,000 over 9,781 | docs/SOAK_RESULTS.md:1880 | A |
| 54× | The margin if the archive under-counts by ten times | docs/SOAK_RESULTS.md:1888 | A |
| 310 bytes | Mean record on the wire | docs/SOAK_RESULTS.md:901 | A |
| 865,674 records | What the 256 MB buffer holds | docs/SOAK_RESULTS.md:903 | A |
| 17.4 hours | The buffer covers a storage-tier outage this long at a real worker's 23 a second, of which 13.8 are InfoLogger | docs/SOAK_RESULTS.md:911, 1901 | A |
| 5.1 hours | The same at the busiest worker-second, 78 a second, 46.8 InfoLogger | docs/SOAK_RESULTS.md:912, 1902 | A |
| 72 seconds | The same at the test rate, 12,000 InfoLogger a second; corroborates round 1's 61 seconds | docs/SOAK_RESULTS.md:910, 914-915 | A |
| 870× | The test rate over a real worker's rate | docs/SOAK_RESULTS.md:917 | A |
| 1 second | The live lane's latency floor, which a produce and a consume step would raise | docs/SOAK_RESULTS.md:1938-1939 | A |

Decision on the buffer-hours arithmetic. Brief 2 flagged that 865,674 records at 23 a second is 10.5 hours, not 17.4. The source divides by the InfoLogger rate only, 60 % of the mix (docs/SOAK_RESULTS.md:905-912). 865,674 over 13.8 is 62,730 seconds, which is 17.4 hours. 865,674 over 46.8 is 18,497 seconds, which is 5.1 hours. The arithmetic closes. The report must say the hours are the InfoLogger output's cover, since the tailed families wait in their files.

The question reopens on four conditions only (docs/SOAK_RESULTS.md:1953-1962). They are a sustained per-worker rate above about 1,000 a second, or a required second consumer. Or outages longer than the cushion, or a real rate within 50× of 42,000.

### 2g. The archive rates against the tested rates

| Number | Meaning | Line | Level |
|---|---|---|---|
| 179 dumps, 248,828,513 records, 312 hosts | Six months of InfoLogger, 31 December 2025 to 29 June 2026 | docs/SOAK_RESULTS.md:319-321 | A |
| 308 workers, 221,707,411 records, 89.1 % | Worker share; four non-worker hosts excluded | docs/SOAK_RESULTS.md:327-328 | A |
| 20,151,049 records, 297 workers | The busiest hour, 15 May 2026, 22:00 to 23:00 UTC | docs/SOAK_RESULTS.md:341-343 | A |
| 23 a second | Median worker in that hour | docs/SOAK_RESULTS.md:364 | A |
| 78 a second | The busiest worker-second | docs/SOAK_RESULTS.md:368 | A |
| 6,785 median, 9,781 peak | The whole farm in that hour | docs/SOAK_RESULTS.md:374-379 | A |
| 1,000, 20,000, 50,000 a second | The tested rates, kept unchanged | docs/SOAK_RESULTS.md:237-239 | A |
| 43× | 1,000 a second over the median worker | docs/SOAK_RESULTS.md:233 | A |
| 256× | 20,000 a second over the busiest worker-second | docs/SOAK_RESULTS.md:234 | A |
| 1,000 messages a minute | The InfoLogger client cut-off per process; the archive rates are floors | docs/SOAK_RESULTS.md:1885-1887 | A |
| 10,000 to 20,000 against 78, gap 128× to 256× | The plan's burst figure against the archive; the burst gap, unsettled | docs/SOAK_RESULTS.md:2948-2960 | A |

### 2h. The parser choice and its cost

| Number | Meaning | Line | Level |
|---|---|---|---|
| Drain3 | The template parser that stays | docs/SOAK_RESULTS.md:1985-1992 | A |
| 3,822 templates, 19.91 core-s/M, 26.3 MB | Drain3 as shipped over the whole corpus, the stage H control | docs/SOAK_RESULTS.md:1996 | A |
| 2,444 templates, 50.87 core-s/M, 206.5 MB | PIPLUP over the whole corpus | docs/SOAK_RESULTS.md:1997 | A |
| 2.56× and 2.24× against a ±25 % gate; 7.8× memory | PIPLUP over Drain3; rejected on cost | docs/SOAK_RESULTS.md:2007-2009 | A |
| 28.9 % | Drain3 run-to-run spread on identical work; no cost gap under that is believable in round 6 | docs/SOAK_RESULTS.md:2012-2015 | A |
| 96.21 core-s/M | The control for the shelf-parser table; every shelf parser lost on cost, streaming or readability | docs/SOAK_RESULTS.md:2076-2094 | C |
| 8.33 core-s/M, 380.0 core-s on 45,596,613 lines | Whole-corpus mining cost with recipe and fast masker, stage I recomputation | docs/SOAK_RESULTS.md:2697-2706 | A |

No accuracy number exists for any parser, because ALICE has no ground truth (docs/SOAK_RESULTS.md:2603).

### 2i. The masker rewrite

| Number | Meaning | Line | Level |
|---|---|---|---|
| 74 to 87 % | Share of mining cost that was masking before the rewrite | docs/SOAK_RESULTS.md:2314 | A |
| infologger 11.93 → 1.82 (−84.7 %); stdout 12.24 → 1.73 (−85.8 %); dds 40.22 → 5.09 (−87.3 %) | Masking cost alone, core-s/M | docs/SOAK_RESULTS.md:2318-2322 | A |
| 0 differences on 3,000,000 lines per family; identical cluster counts | Equivalence of the rewritten masker | docs/SOAK_RESULTS.md:2357-2361 | A |
| first-literal position | The mechanism: a rule opening with a boundary or lookbehind runs the full matcher at every character | docs/SOAK_RESULTS.md:2324-2338 | C |
| does not transfer | The literal-first rule is a Python `re` result; the collector's engine differs, every collector regex measured instead | docs/SOAK_RESULTS.md:3132-3137 | C |
| 86 to 90 % | Pad step cost removed in round 18; the pad, not the masker, was the dearest step | docs/SOAK_RESULTS.md:5484-5485 | A |
| at its floor | The number stage: six rewrites all inside run-to-run spread; the masker ships unchanged from round 6 | docs/SOAK_RESULTS.md:5583-5589 | A |

### 2j. The recipe per family and the template counts

The recipe as committed: depth 8, max children 100, similarity 0.4 for InfoLogger and process logs and 0.5 for dds, numeric tokens parametrised except on dds and journald, clock stripped before mining, fast masker, four-line FLOAT/NUM merge (docs/SOAK_RESULTS.md:2624-2630, 4259-4275). Depth 8 is a module global, so all seven families share it (docs/SOAK_RESULTS.md:4259-4275).

| Family | Padding | Numeric | Templates | Words kept | Cost core-s/M | Line | Level |
|---|---|---|---|---|---|---|---|
| infologger | `= ; :` | parametrised | 675 (3,000,000-line sample) | 94.5 % | 8.85 (round 6 sample arm) | docs/SOAK_RESULTS.md:3400, 2374-2384 | A |
| dpl | none | parametrised | 920 | 99.8 % | 5.21 | docs/SOAK_RESULTS.md:3324, 3401 | A |
| datadist | none | parametrised | 191 | 60.3 % | 5.37 | docs/SOAK_RESULTS.md:3289, 3402 | A |
| dds | `=` | kept | 1,570 | 87.9 % | 21.00 | docs/SOAK_RESULTS.md:3367-3382 | A |
| ildaemon | none | parametrised | 18 | 100 % | 3.44 | docs/SOAK_RESULTS.md:3710-3713 | A |
| journald | `=` | kept | 1,362 | 96.8 % | 7.81 | docs/SOAK_RESULTS.md:3715-3726 | A |
| odc | `= ; :` | parametrised | 266 | 98.7 % | 24.36 | docs/SOAK_RESULTS.md:4080-4093 | A |

Decision on the InfoLogger cost row. Brief 4 calls 8.85 "round 6's figure, not re-run" (docs/SOAK_RESULTS.md:4277-4280). It is the fourth arm of the 3,000,000-line sample at docs/SOAK_RESULTS.md:2374-2384, recipe plus fast masker. The whole-corpus InfoLogger figure is 9.53 (docs/SOAK_RESULTS.md:2699). Round 18 re-measured InfoLogger with the current masker on the same 3,000,000 lines (docs/SOAK_RESULTS.md:5442), so the "not re-measured" note is closed by round 18. Quote round 18 for cost.

Template counts as a curve:

| Number | Meaning | Line | Level |
|---|---|---|---|
| 3,011 templates on 45,596,613 lines | One run of each family; a floor, not a total | docs/SOAK_RESULTS.md:2404-2409, 2468 | A |
| 4,092 | Sixteen runs of dds and stdout plus eighty InfoLogger partitions | docs/SOAK_RESULTS.md:2523-2533 | A |
| 4,221 templates on 55,963,050 lines; infologger 1,497, stdout 1,154, dds 1,570 | One tree per family carried across all three corpora | docs/SOAK_RESULTS.md:2632-2652 | A |
| 20,000 templates, 4.7 times 4,221 | The growth ceiling; at the ceiling the worker stops learning and keeps counting | docs/SOAK_RESULTS.md:5124-5136 | A |
| 208.1 MB peak against a 512 MB unit | Memory at the ceiling; serialisation, not the tree, sets the peak | docs/SOAK_RESULTS.md:5107-5117 | A |
| 0.07 % and 0.06 % | Embedding cost as a share of one mining pass; never a reason to keep the tree shallow | docs/SOAK_RESULTS.md:2301-2304, 2654-2658 | A |

### 2k. The per-source costs and routing shares from rounds 7 to 9

| Number | Meaning | Line | Level |
|---|---|---|---|
| 41.8 % | Share of the corpus that reached durable storage with no severity before round 7 | docs/SOAK_RESULTS.md:3038-3039 | A |
| 99.83 % | Process-tree lines with a severity recovered, continuations folded, against a 99 % gate | docs/SOAK_RESULTS.md:3085-3088 | A |
| 99.97 % | DDS lines with a severity recovered, 43,972 lines | docs/SOAK_RESULTS.md:3097 | A |
| 96.94 % / 3.06 % | Process-tree share that stays on the worker / crosses the network | docs/SOAK_RESULTS.md:3115-3116 | A |
| 0.39 % | `STATE` share of the process tree, routed durable on purpose | docs/SOAK_RESULTS.md:3118-3120 | A |
| 86.5 % / 13.5 % | DDS `inf` / `err` share in the corpus; 13.3 % durable on real traffic | docs/SOAK_RESULTS.md:3122-3124, 3759 | A |
| 2,500 to 10,500 entries a day | A worker's journal; all system logs collected, no unit allow-list | docs/SOAK_RESULTS.md:3587-3589 | A |
| 75,243 / 74,023 / 1,220 | epn146 journal: records, node-local, durable; 1.6 % crosses the network | docs/SOAK_RESULTS.md:3609-3613 | A |
| 234 to 16 | Journal fields per record before and after the allowlist | docs/SOAK_RESULTS.md:3614-3615 | A |
| 1025 of 2048 | Peak clients on the InfoLogger daemon | docs/SOAK_RESULTS.md:3568 | A |
| 190,014 (99.98 %) / 38 (0.02 %) | Orchestrator records node-local / durable | docs/SOAK_RESULTS.md:4067-4068 | A |
| 3,487 bytes average | An executed-task command line in DDS; `task` captures the first token only | docs/SOAK_RESULTS.md:3242-3243, 4182-4186 | A |
| about 20 % | The smallest collector cost difference the host can resolve; no collector regex change is worth making on cost | docs/SOAK_RESULTS.md:3951-3960 | A |
| 4 | Collector versions found on the farm; 3.2.8 on the storage machine, two majors behind | docs/SOAK_RESULTS.md:3519-3527 | A |
| 5 of 5 lost | Bytes appended after rename on Fluent Bit 5.0.8; the 5. prefix is blocked, 4.0.14 is the default. This result depends on the version | docs/SOAK_RESULTS.md:4313-4318, 4526-4533 | A |

Every regex cost figure from the regex benchmark in rounds 6 to 8 is wall clock and is withdrawn (docs/SOAK_RESULTS.md:4192-4197). Templating costs from the templating bench are processor time and stand (docs/SOAK_RESULTS.md:4199-4200).

### 2l. The round 18 whole-pipeline figure

| Family | Lines | Shipped | Candidate | Change | Templates | Line |
|---|---|---|---|---|---|---|
| infologger | 3,000,000 | 22.29 | 9.55 | −57.2 % | 675 = 675 | docs/SOAK_RESULTS.md:5442 |
| dpl | 3,000,000 | 12.98 | 8.59 | −33.8 % | 920 = 920 | docs/SOAK_RESULTS.md:5443 |
| datadist | 92,813 | 13.48 | 9.36 | −30.6 % | 191 = 191 | docs/SOAK_RESULTS.md:5444 |
| dds | 1,236,971 | 47.31 | 22.41 | −52.6 % | 1,570 = 1,570 | docs/SOAK_RESULTS.md:5445 |
| ildaemon | 163,670 | 7.65 | 5.00 | −34.6 % | 18 = 18 | docs/SOAK_RESULTS.md:5446 |
| journald | 277,541 | 16.84 | 8.68 | −48.5 % | 883 = 883 | docs/SOAK_RESULTS.md:5447 |
| odc | 190,250 | 23.56 | 10.93 | −53.6 % | 266 = 266 | docs/SOAK_RESULTS.md:5448 |

Units are core-seconds per million lines. The headline is 31 to 57 % cheaper per family, byte-identical on 8,961,245 lines (docs/SOAK_RESULTS.md:5428-5429, 5537). The host was 1.7 times slower than at round 6 for an unnamed reason (docs/SOAK_RESULTS.md:5458-5464). Round 18 absolutes must not be placed beside round 6 absolutes; quote ratios (docs/SOAK_RESULTS.md:5636-5638). [A]

### 2m. The round 21 transport figure

| Number | Meaning | Line | Level |
|---|---|---|---|
| 15 per cent | Forward transport into the stamper cheaper per record, every returned byte the same | docs/SOAK_RESULTS.md:5933-5935 | A |
| −15.1 / −15.4 % transport, −6.5 % whole hop (infologger); −14.8 / −16.2 %, −4.0 % (dpl) | Transport is about a quarter of the stamper hop | docs/SOAK_RESULTS.md:5975-5982 | A |
| 425 bytes a record, 16 fields, 4,615 records a chunk (infologger); 365 bytes, 12 fields, 5,454 a chunk (dpl) | The captured Forward wire | docs/SOAK_RESULTS.md:5959-5962 | A |
| 32 % and 24 % | Share of the wire that is repeated key names; on a Unix socket it is free, so protobuf and gzip are rejected | docs/SOAK_RESULTS.md:5946-5951 | A |
| encode 1.69 → 0.28, 2.14 → 0.24 | Per-record encode step, shipped against spliced; decode does not move | docs/SOAK_RESULTS.md:5992-6007 | C |
| 15 per cent, not 32 to 41 | The first write-up compared against a prototype baseline; 15 is the figure | docs/SOAK_RESULTS.md:6100-6107 | C |

### 2n. The catalog guarantees

These are the properties the reviews of rounds 9 to 20 proved by reproduction, each run against its own absence (docs/SOAK_RESULTS.md:5009-5013, 5388-5396, 5763-5770, 5884-5892).

| Guarantee | Evidence | Line | Level |
|---|---|---|---|
| Every record carries a document identifier assigned in a filter, and the output writes with create | 43 documents with it, 46 and 55 without; a second create answers 409 | docs/SOAK_RESULTS.md:4937-4967 | A |
| The identifier is a counter of node, process UUID and record, not a content hash | a hash collapses distinct identical lines | docs/SOAK_RESULTS.md:4944-4950 | A |
| Journal records carry the identifier through the allowlist | 112 documents with it against 259 or 301 without | docs/SOAK_RESULTS.md:5250-5253, 5911-5917 | A |
| Catalog counts are absolute per node, not deltas | an increment cannot be retried, a set can | docs/SOAK_RESULTS.md:4364-4370 | A |
| The batch is written down before publication; local state is one file replaced atomically | the publication window is the case that must be retried | docs/SOAK_RESULTS.md:4369, 4474 | A |
| The scan orders by sequence number per shard per concrete index, bounded by the global checkpoint, after stats and refresh | three earlier keys each skipped a record | docs/SOAK_RESULTS.md:4841-4859 | A |
| A timed-out or shard-failed search is never read as empty | partial refresh skips, partial search stops, partial listing aborts | docs/SOAK_RESULTS.md:5027-5047 | A |
| The catalog identity is the index UUID, created explicitly before the first write; re-read per page | 0 against 1 and 0 against 2 documents in reproduction | docs/SOAK_RESULTS.md:5689-5709, 5828-5855 | A |
| A pass number never goes backwards | floor is the larger of clock and last written | docs/SOAK_RESULTS.md:5072-5091 | A |
| The tree persists once per batch, at the batch boundary | 25 records produced 25 serialisations before | docs/SOAK_RESULTS.md:5098-5122 | A |
| The tree stops learning at 20,000 templates and keeps counting; LRU eviction is not used | eviction would reset counts to one | docs/SOAK_RESULTS.md:5124-5136 | A |
| A replaced catalog rebuilds from the trees, not from short-lived sources | the sources have aged out | docs/SOAK_RESULTS.md:5316-5335 | A |
| 75 of 75 unit tests, seventeen document-level sequences | after round 20; 29 tests and three sequences at round 10 | docs/SOAK_RESULTS.md:5898, 5902, 4420 | C |

See section 6, item 5. The report must check which of these survive the September rework of the catalog into the stamper.

## 3. Retracted

The report must not use any of these.

| Claim | Where reported | Retracted at | Reason |
|---|---|---|---|
| Every Stage C to G number of round 2, including the flush grid and the 0.5-over-1 verdict | docs/SOAK_RESULTS.md:1139-1488 | docs/SOAK_RESULTS.md:13-15, 1141-1143 | measured on a saturated rig; cost tracked offer shortfall |
| "Move flush from 5 to 0.5"; −16.4 % total, −22.4 % worker OpenSearch, −36.6 % memory | docs/SOAK_RESULTS.md:1237, 1242-1248 | docs/SOAK_RESULTS.md:19-27 | the chosen value is 1; clean change is −3.4 %, −24.5 %, −28.0 % |
| Flush 0.5 beats flush 1 by 11.1 % (69.78 against 77.53) | docs/SOAK_RESULTS.md:1258-1296 | docs/SOAK_RESULTS.md:85 | reversed: flush 1 beats 0.5 by 2.21 % |
| Core isolation is worth 52 % | withdrawn table | docs/SOAK_RESULTS.md:100 | 2.7 % against a 5.5 % floor, no effect |
| OpenSearch needs 3 of 4 cores, 72 % | withdrawn table | docs/SOAK_RESULTS.md:101 | the 2-core arm reads 101.61 and offers perfectly |
| Heap 2g wins by 8 to 22 %; heap ranking flips with flush | withdrawn table | docs/SOAK_RESULTS.md:102-103 | 2.1 % and 0.4 % against a 6.7 % floor; no flip |
| t3 is 6.2 % cheaper on four cores | withdrawn table | docs/SOAK_RESULTS.md:104 | t3 costs 8.8 % more; t0 stands |
| Flush 0.25 breaks ingestion; a drain ceiling near 13,800 a second | docs/SOAK_RESULTS.md:1429-1476 | docs/SOAK_RESULTS.md:105-106, 1305-1357 | it offers at −0.98 % and costs 6 % more; the ceiling was a failing machine |
| The architectural cap is 120,000 records a second | docs/SOAK_RESULTS.md:1077-1092 | docs/SOAK_RESULTS.md:107 | straight-lined a curve known to bend; about 42,000 sustained replaces it |
| Prediction two "scored right" | withdrawn table | docs/SOAK_RESULTS.md:108 | scored on saturated evidence; unscored |
| Stage A's first noise floors, 1.95 % and 28.07 % | docs/SOAK_RESULTS.md:508-513 | same lines | two builds of the instrument |
| zstd is 14.4 % cheaper on the collector | single cell | docs/SOAK_RESULTS.md:985-989 | four runs each interleave completely; gzip stays |
| Every `mean_cores` figure printed before the recorder fix | round 2 addendum and earlier | docs/SOAK_RESULTS.md:1832, 1837-1839 | samples counted as seconds, about 4× too high; cumulative core-seconds are correct |
| "The obvious next step is a host restart" | docs/SOAK_RESULTS.md:1649-1650 | docs/SOAK_RESULTS.md:184-187 | a restart was never available; the rate was lowered to 5,000 |
| The duplication fix "is not made here" | docs/SOAK_RESULTS.md:1544-1547 | docs/SOAK_RESULTS.md:165-169 | the fix has shipped to the collector template |
| Round 1's ten-million-record loss as a product concern | docs/SOAK.md:55-65 | docs/SOAK_RESULTS.md:926-928 | real and reproducible; needs a rate no worker approaches |
| Round 1's 2,000 to 3,000 a second collapse against OpenSearch | docs/SOAK.md:103-106 | docs/SOAK.md:108-111 | a bare container with no index templates; only the sequence transfers |
| Round 1's live-lane collapse of InfoLogger to 5 % | docs/SOAK.md:120-133 | docs/SOAK_RESULTS.md:129-130 | the artefact came from starving the sink on a shared core |
| The quoted "10 MB" collector memory | docs/SOAK.md:19 | docs/SOAK.md:18-20 | wrong by more than an order of magnitude |
| 1.8× cheaper to mine on fewer templates | docs/SOAK_RESULTS.md:1991-1992, 2383-2384 | docs/SOAK_RESULTS.md:2708-2713 | the 19.91 control was not re-measured; no multiple is quoted |
| Whole-corpus mining cost 11.18 core-s/M; stdout 12.00 | docs/SOAK_RESULTS.md:2384, 2402-2406 | docs/SOAK_RESULTS.md:2693-2706 | one contaminated stdout reading; 8.33 replaces it |
| The recipe is "21.7 % cheaper" | docs/SOAK_RESULTS.md:2281 | docs/SOAK_RESULTS.md:2281-2285 | the 22.39 control was an outlier; the recipe is cost-neutral |
| parametrize_numeric_tokens "doubles the cost, 33.35 against 14.93" | docs/SOAK_RESULTS.md:2126-2128 | docs/SOAK_RESULTS.md:2134-2136 | a contaminated block; the knob is free |
| 88.8 % of stdout lines improved with padding | docs/SOAK_RESULTS.md:2208-2216 | docs/SOAK_RESULTS.md:2217-2221 | the metric accepted bare punctuation as words |
| 44 % contentless InfoLogger templates as a headline | docs/SOAK_RESULTS.md:2223-2226 | same lines | template-weighted; the line-weighted share is 1.4 % |
| SHISO 86× and IPLoM 4× worse than our Drain3 | docs/SOAK_RESULTS.md:2077-2080 | same lines | shelf parsers compare only against the 96.21 control |
| Round 6's `= ;` padding for the process-tree family | docs/SOAK_RESULTS.md:2246-2250 | docs/SOAK_RESULTS.md:3333-3352 | measured without the clock strip; no padding is 36 % cheaper |
| Every regex benchmark cost figure from rounds 6 to 8: the 0.8 % control, the 17.3 % and −33 % arms, the +20.8 % `task` cost | docs/SOAK_RESULTS.md:3128-3238 | docs/SOAK_RESULTS.md:4192-4197, 4157-4188 | wall clock, not processor time; the run stopped halfway |
| The InfoLogger daemon log is tab-separated and "a handful of lines a day" | docs/SOAK_RESULTS.md:3020 | docs/SOAK_RESULTS.md:3549-3552, 3565-3567 | spaces; about 930 lines a day |
| Two collector versions on the farm | docs/SOAK_RESULTS.md:3055-3059 | docs/SOAK_RESULTS.md:3517-3527 | four |
| The orchestrator is 100 % informational, a heartbeat | docs/SOAK_RESULTS.md:3688-3690 | docs/SOAK_RESULTS.md:4021-4037 | five severities across 25 files |
| The template catalog "built and proved" in round 8 | docs/SOAK_RESULTS.md:3654-3678 | docs/SOAK_RESULTS.md:3962-4013 | four defects; it shipped none of its guarantees |
| "All four Fluent Bit versions pass" the rotation check | docs/SOAK_RESULTS.md:4125-4139 | docs/SOAK_RESULTS.md:4287-4288, 4304-4306 | one sample of a test that failed 1 in 3 |
| `rotate_wait: 30` as a fix for rotation loss | round 10 | docs/SOAK_RESULTS.md:4346-4348 | no effect |
| collector_time, then ingest_time, then highest-seen _seq_no as the catalog scan key | rounds 12 to 14 | docs/SOAK_RESULTS.md:4646-4670, 4741-4760, 4824-4846 | each skipped a record; the bounded sequence number replaces them |
| A record total is a completeness check | round 10 | docs/SOAK_RESULTS.md:4485-4489 | one shipped twice and one lost still match |
| The file-sink rig as evidence against retry duplication | round 16 | docs/SOAK_RESULTS.md:4921-4928 | a file sink never retries |
| Journal control counts 301 on 4.0.1 and 259 on 4.0.14 as version figures | docs/SOAK_RESULTS.md:5250-5253, 5787-5788 | docs/SOAK_RESULTS.md:5911-5917 | counts vary run to run |
| Round 21 transport gain of 32 to 41 per cent | first write-up | docs/SOAK_RESULTS.md:6100-6107 | measured against a prototype baseline; 15 per cent |
| Round 21 per-step timings as a budget | docs/SOAK_RESULTS.md:5992-6007 | docs/SOAK_RESULTS.md:6000-6003 | the steps overshoot the whole by a fifth; direction only |
| Round 18 absolutes beside round 6 absolutes | docs/SOAK_RESULTS.md:5442-5448 | docs/SOAK_RESULTS.md:5458-5464, 5636-5638 | the host moved 1.7 times |

## 4. Defects found and fixed

Count: 99 distinct defects across rounds 1 to 21. Two duplicates were removed: the duplication defect and the heap ceiling appear in both brief 1 and brief 2. One defect described twice was merged: the stdout severity parser, rounds 6 and 7. The task named six components. A seventh group, the templating pipeline, is needed because the masker and recipe live beside the catalog and not inside it. Level tags: the collector, OpenSearch output, stamper and catalog defects are [A] where the fix changed what ships; rig defects are [C].

### Collector (16)

1. stdout severity parser: the ROOT-form rule matched no O2 line, and the O2 parser was anchored on `[` while replay prepends a date. Only 345 of 3,000,000 lines carried a severity. 41.8 % of the corpus reached durable storage without one. docs/SOAK_RESULTS.md:2559-2576, 3032-3039. [A]
2. DDS router: rules keyed on severity matched nothing when no parser claimed the line, so DDS startup lines vanished. Each router now ends with a catch-all. docs/SOAK_RESULTS.md:3041-3047. [A]
3. systemd input: one version rejects `multiline.parser` on that input; the fold moved to a filter all versions accept. docs/SOAK_RESULTS.md:3055-3059. [C]
4. Shifter view severity map: a second copy of the table went stale; one table now. docs/SOAK_RESULTS.md:3061-3067. [A]
5. InfoLogger daemon parser required a tab; the file uses spaces; matched none of 162,642 lines. docs/SOAK_RESULTS.md:3549-3552. [A]
6. Daemon client-count extractor missed 36 % of the file; now anchors on the trailing count. docs/SOAK_RESULTS.md:3554-3563. [C]
7. Journal filter order: the parser ran before the fold, so `Comm:` was never extracted. docs/SOAK_RESULTS.md:3570-3572. [C]
8. Journal unit allow-list would have dropped NetworkManager and slurmctld; removed. docs/SOAK_RESULTS.md:3589-3593. [A]
9. Journal enable flag defaulted to false and could never be enabled; default true, the probe can only turn it off. docs/SOAK_RESULTS.md:3881-3887. [C]
10. Orchestrator classified as a heartbeat from one idle day; five severities in 25 files. docs/SOAK_RESULTS.md:4021-4037. [A]
11. Regex derivation: the collector's engine reads `(?m)` as dot-matches-newline; `(?s)` used. docs/SOAK_RESULTS.md:4499-4502. [C]
12. Fluent Bit 5.0.8 loses bytes appended after rename; production stays on 4.x. docs/SOAK_RESULTS.md:4334-4344. [A]
13. Collector role version default pointed at the version that loses data; default 4.0.14 and a guard on the 5. prefix. docs/SOAK_RESULTS.md:4517-4533. [A]
14. Journal allowlist dropped the document identifier, so journal records duplicated on retry. docs/SOAK_RESULTS.md:5224-5237. [A]
15. DataDistribution recipe: the stdout strip did not match its bracket, so 92,813 lines mined the clock; a family split fixes it. docs/SOAK_RESULTS.md:3264-3273. [A]
16. Round 1 documentation: raising the disk buffer does raise memory. docs/SOAK.md:97-99. [A]

### OpenSearch output (2)

17. The shipped output set no response buffer size; large bulk responses overflowed it; the plugin retried bulks already indexed, 5.2× duplication, 751,381 held against 144,000 offered. Fix shipped to the collector template. docs/SOAK_RESULTS.md:1494-1547, 168-169. [A]
18. A retried bulk chunk duplicated every record; a filter-assigned document identifier and create semantics. docs/SOAK_RESULTS.md:4919-4950. [A]

### Stamper (3)

19. Transport re-encoded every record to add two fields; it now splices the original bytes and caches the stamp tail per template version. docs/SOAK_RESULTS.md:6005-6022. [A]
20. A record already carrying a stamp field duplicated the key; a length guard falls back to the full encode. docs/SOAK_RESULTS.md:6039-6056. [C]
21. Forward framing: the unpacker mis-counts after out-of-data at a feed boundary; a fresh scan per receive. docs/SOAK_RESULTS.md:6079-6089. [C]

### Catalog (33)

22. Document identifier designed on sha1 of family and template, not a randomised hash that would grow a new document every ten minutes. docs/SOAK_RESULTS.md:3674-3677. [A]
23. Family label tested for a prefix the collector had already eaten; every process-tree template filed as one family. docs/SOAK_RESULTS.md:3967-3983. [A]
24. Read only the local index; held no InfoLogger, daemon, warning or error templates; reads all three routes now. docs/SOAK_RESULTS.md:3985-3991. [A]
25. Watermark stored only collector time; every record in the last millisecond was dropped. docs/SOAK_RESULTS.md:3993-3999. [A]
26. Own miner left out the FLOAT/NUM merge; calls the shared recipe miner now. docs/SOAK_RESULTS.md:4001-4011. [A]
27. A generalised template became two documents across passes. docs/SOAK_RESULTS.md:4352-4356. [A]
28. A retry after a lost progress save doubled the count; counts absolute and per node. docs/SOAK_RESULTS.md:4358-4370. [A]
29. Wipe detector read a count after an unrefreshed write and re-mined every pass. docs/SOAK_RESULTS.md:4372-4377. [C]
30. Absolute counts did not survive a retry that reads further; the batch is written before publication. docs/SOAK_RESULTS.md:4448-4476. [A]
31. A counter pass number restarts on a reprovisioned node; milliseconds since the epoch. docs/SOAK_RESULTS.md:4475. [A]
32. Idle pass printed nothing and looked dead. docs/SOAK_RESULTS.md:4555-4558. [C]
33. Recovery cleanup ran inside the refresh window and found nothing; refreshes first. docs/SOAK_RESULTS.md:4573-4594. [A]
34. collector_time as ordering key skipped late-visible records. docs/SOAK_RESULTS.md:4646-4670. [A]
35. ingest_time as ordering key; indexing can follow out of order. docs/SOAK_RESULTS.md:4741-4776. [A]
36. Highest-seen sequence number is assigned before indexing; bounded by the global checkpoint. docs/SOAK_RESULTS.md:4824-4859. [A]
37. A timed-out or shard-failed search read as empty. docs/SOAK_RESULTS.md:5027-5047. [A]
38. Position keyed by index name restarts at zero on a recreated index; keyed by name and UUID. docs/SOAK_RESULTS.md:5049-5057. [A]
39. A recreated catalog resolved by name left the node idle; identity is the UUID. docs/SOAK_RESULTS.md:5059-5064. [A]
40. A clock stepped backwards made every update a silent no-op. docs/SOAK_RESULTS.md:5072-5091. [A]
41. The tree serialised itself once per record. docs/SOAK_RESULTS.md:5093-5122. [A]
42. The tree had no size bound; 20,000-template ceiling. docs/SOAK_RESULTS.md:5124-5136. [A]
43. One busy shard starved the others; per-shard budget and rotating start. docs/SOAK_RESULTS.md:5138-5143. [C]
44. Retirement marked while another node still contributed. docs/SOAK_RESULTS.md:5145-5156. [A]
45. A generalised template kept only this batch's programs. docs/SOAK_RESULTS.md:5158-5167. [C]
46. State file rename never synchronised the directory. docs/SOAK_RESULTS.md:5169-5176. [C]
47. Recovery checked the listing, not the refresh that feeds it. docs/SOAK_RESULTS.md:5266-5276. [C]
48. A crash after the clear lost the pass number it used. docs/SOAK_RESULTS.md:5278-5302. [C]
49. First publication recorded no catalog because the write creates the index. docs/SOAK_RESULTS.md:5304-5314. [C]
50. Replacement re-mined from short-lived sources; rebuilds from the trees. docs/SOAK_RESULTS.md:5316-5335. [A]
51. An unassigned replica read as a failed refresh. docs/SOAK_RESULTS.md:5337-5356. [A]
52. A failed identity lookup stored null and read as unchanged. docs/SOAK_RESULTS.md:5657-5676. [A]
53. The index UUID trusted after one read; re-read per page. docs/SOAK_RESULTS.md:5689-5709. [A]
54. Destination read after the write cannot say where the write went; index created and identity recorded before the payload. docs/SOAK_RESULTS.md:5806-5845. [A]

### Templating pipeline: masker, recipe, bench (11)

55. Masker regexes opened with a boundary or lookbehind; literal moved first, 85 to 87 % off. docs/SOAK_RESULTS.md:2324-2338. [C]
56. FLOAT/NUM fold: a one-pass fold changed `1.5-3`; a flag reproduces the two-pass quirk. docs/SOAK_RESULTS.md:2345-2351. [C]
57. The frozen recipe lived only in a gitignored script; now in the repository. docs/SOAK_RESULTS.md:2621-2624. [C]
58. Double masking in stage I, visible as 981 against 1,004 templates. docs/SOAK_RESULTS.md:2660-2672. [C]
59. Contaminated stdout cost reading of 12.00; whole-corpus cost recomputed to 8.33. docs/SOAK_RESULTS.md:2674-2706. [C]
60. Source split rule would have dropped a host, not the program it meant to exclude. docs/SOAK_RESULTS.md:2814-2839. [C]
61. Readability metric accepted bare punctuation as words. docs/SOAK_RESULTS.md:2206-2221. [C]
62. parametrize_numeric_tokens arm set true on top of true, tested nothing. docs/SOAK_RESULTS.md:2124-2130. [C]
63. PIPLUP file handling built a frame of every line; a harness drove clustering directly. docs/SOAK_RESULTS.md:2017-2023. [C]
64. KELP needed three source patches to run. docs/SOAK_RESULTS.md:2102-2110. [C]
65. Pad step used regex escape and a callback per separator; strip walked the whole line; per-token Python loops; wrapper masked and split twice. docs/SOAK_RESULTS.md:5491-5522. [A]

Declared and not fixed: the corpus builder did not join indented continuation lines that production joins. It affects 7,912 of 3,000,000 stdout lines and 12 of 43,972 dds lines. docs/SOAK_RESULTS.md:2590-2595. [C]

### Rig instrument (32)

66. Recorder did not report its own stalls; a missed second folded into the next delta. docs/SOAK_RESULTS.md:676-681.
67. A cell whose recorder wrote nothing passed; now void. docs/SOAK_RESULTS.md:682-684.
68. Generator selftest dropped the whole InfoLogger family and reported 400 a second for 1,000. docs/SOAK_RESULTS.md:686-691.
69. Gate zero proved pacing, not headroom; a ceiling probe of 2,842,277 a second added. docs/SOAK_RESULTS.md:693-697.
70. Two recorder revisions across cells; all six rerun on one build. docs/SOAK_RESULTS.md:477-495.
71. Arm t3 rendered nothing because the config generator did not know the name. docs/SOAK_RESULTS.md:750-755.
72. The sink did not record content encoding in the compression cells. docs/SOAK_RESULTS.md:1009-1012.
73. A4 voided by a laptop stall beside an archive scan; the scan runs only on an idle rig. docs/SOAK_RESULTS.md:666-673, 255-259.
74. Compose file fixed the container at 2g while the heap varied; 2g and 3g heaps never started. docs/SOAK_RESULTS.md:1553-1589.
75. Recorder counted samples as seconds; `mean_cores` about 4× too high. docs/SOAK_RESULTS.md:1832, 1837-1839.
76. Burst script read keys that did not exist; both metrics empty. docs/SOAK_RESULTS.md:1833.
77. Burst report had no validity gate; a 2 % offer gate and recorder-loss gate added. docs/SOAK_RESULTS.md:1834.
78. Generator writes of 15.5 MB a second could not be separated from disk effects; opt-in RAM volume. docs/SOAK_RESULTS.md:1835.
79. Two runaway host processes took 7.5 to 9.3 of 16 cores; the machine must be idle before a cell is trusted. docs/SOAK_RESULTS.md:1843-1850.
80. Two containers at once on disjoint cpusets inflated cost 2.8×; a masking probe ran over lines where no rule fired. docs/SOAK_RESULTS.md:2228-2236.
81. Soak configuration renderer passed renamed variables; every rendered configuration was an exception. docs/SOAK_RESULTS.md:3049-3053.
82. Regex benchmark, four faults in one day: block-ordered arms, a broken-parse arm, a TCP race, a YAML round-trip that deleted routing rules. docs/SOAK_RESULTS.md:3179-3212.
83. Replay shifted-clock wrapper monkeypatched a function whose return type had changed. docs/SOAK_RESULTS.md:3833-3838.
84. Regex benchmark measured wall clock labelled as processor time and stopped halfway. docs/SOAK_RESULTS.md:3898-3928.
85. Log rotation had never been tested; a rotation check added. docs/SOAK_RESULTS.md:4125-4139.
86. InfoLogger real-traffic check fed invented records; now reads a real dump. docs/SOAK_RESULTS.md:4141-4155.
87. Rotation test settled before the tail's next sweep, mutated the tracked tree, fired on a fixed sleep. docs/SOAK_RESULTS.md:4298-4302.
88. Bulk replay check computed every figure over the records that arrived. docs/SOAK_RESULTS.md:4392-4403.
89. Completeness check compared totals; compares the multiset of contents now. docs/SOAK_RESULTS.md:4485-4494.
90. Replay harness left the last corpus line in the multiline parser. docs/SOAK_RESULTS.md:4511-4515.
91. Catalog test helper supplied the refresh whose absence was the defect. docs/SOAK_RESULTS.md:4598-4609.
92. Round-trip probe records had no ingest_time, so the scan mined nothing. docs/SOAK_RESULTS.md:4703-4710.
93. Retry proxy shared a temporary filename across threads. docs/SOAK_RESULTS.md:5360-5365.
94. Retry check had no independent expectation. docs/SOAK_RESULTS.md:5367-5379.
95. Retry check counted a failure but never printed it. docs/SOAK_RESULTS.md:5381-5384.
96. Retry check listing keyed by `_id` alone collapsed cross-destination duplicates. docs/SOAK_RESULTS.md:5719-5735.
97. Retry check read a refused connection as a pass. docs/SOAK_RESULTS.md:5737-5754.
98. Mapping check's own catalog reader ignored refresh, timeout and shard failures. docs/SOAK_RESULTS.md:5860-5880.

Earlier versions of defects 94 to 97 in the retry-check tool are not counted separately. They are operations counted from the request, waiting for a quiet count, and fault injection by silent close. docs/SOAK_RESULTS.md:4978-5001.

### Cluster sink (1)

99. OpenSearch bootstrap script: a heredoc lost its closing quote when the catalog template was added, and the validator skipped the malformed block. docs/SOAK_RESULTS.md:3637-3642. [C]

## 5. Not measured

Verbatim or near-verbatim statements the soak makes about its own gaps, with lines.

Rates and hardware

- "One micro-benchmark on `epn228` would give the conversion factor. It has not been run." docs/SOAK_RESULTS.md:289-290. This binds every absolute number in both files.
- External pinning: "Required. Never measured, because the rig always did it." docs/SOAK_RESULTS.md:122. "The size of that error is unmeasured." docs/SOAK_RESULTS.md:135.
- "The rate at which internal placement starts to matter is unmeasured." docs/SOAK_RESULTS.md:161.
- "The full stack ... has never been run above 5,000 a second in steady state, nor above a 30,000-a-second burst of 30 seconds." docs/SOAK_RESULTS.md:157-160. See section 6, item 1.
- "Nothing came near a cap, so there is headroom — but it is unmeasured headroom, not proven headroom." docs/SOAK_RESULTS.md:1760-1762.
- "Nothing was run above 50,000 a second." docs/SOAK_RESULTS.md:1090. "Finding the real cap needs a rate ramp above 50,000 that runs until something actually breaks." docs/SOAK_RESULTS.md:1101-1103.
- "The whole round should be repeated once the host's performance cores return." docs/SOAK_RESULTS.md:199-200.
- "No long soak. The longest run was twenty minutes." docs/SOAK.md:172-174.
- "The health input is unmeasured." docs/SOAK.md:178-181.
- "Not diagnosed. Why the hypervisor is confined to two cores on a sixteen-core host that is idle." docs/SOAK_RESULTS.md:1647-1648.
- "The host was 1.7 times slower for a reason the instruments on it could not name." docs/SOAK_RESULTS.md:5461-5462.

The archive and the burst gap

- "The true worker rate is higher than 23 a second by an unmeasured amount." docs/SOAK_RESULTS.md:1886-1887. "These are floors on the true rate, not measurements of it." docs/SOAK_RESULTS.md:345.
- "The burst gap is the sharpest open question in this round." docs/SOAK_RESULTS.md:449-450. "This round does not have the evidence to choose between them, and does not guess." docs/SOAK_RESULTS.md:2959-2960.
- "does the 1,000 a second cover all three log families or InfoLogger alone?" docs/SOAK_RESULTS.md:451-453.
- "the derivation covers InfoLogger only. DDS and stdout need the run tarballs read, and the burst shape needs the same." docs/SOAK_RESULTS.md:255-256, 2989-2991.
- "whether an EPN worker's O2 process files actually carry InfoLogger content ... No amount of soak testing settles it". docs/SOAK_RESULTS.md:637-643, 2984-2985.
- "`epn146` and `epn323` are unsurveyed." docs/SOAK_RESULTS.md:2993-2995.
- "No run has been active during any census." docs/SOAK_RESULTS.md:4436. "The live job-log path is still unknown." docs/SOAK_RESULTS.md:3455, 3804-3805.

The collector

- "nothing in this round measures what that back-pressure does to the tcp input". docs/SOAK_RESULTS.md:855-858.
- "a firm collector-side lane cost needs repeats". docs/SOAK_RESULTS.md:966-967.
- Round 1: "What is not established is how slow a real live-lane service would be." docs/SOAK.md:144-145.
- "This host cannot resolve a difference below roughly 20 %." docs/SOAK_RESULTS.md:3951. Repeated at docs/SOAK_RESULTS.md:4434-4435.
- "That error share is high and it rests on a corpus that under-samples DDS ... the largest unpriced risk in this routing." docs/SOAK_RESULTS.md:3122-3124.
- "Nothing here was measured on the EPN farm." docs/SOAK_RESULTS.md:3006-3008. The round 8 census was read-only (docs/SOAK_RESULTS.md:3507).
- "The concurrent gap cannot be produced on a live cluster without pausing an indexing thread." docs/SOAK_RESULTS.md:4863-4864.
- The local rig's collector configuration carries neither the identifier nor the Lua filter. docs/SOAK_RESULTS.md:5199-5201.

Templating and the catalog

- "No ground truth, so no accuracy number for any parser, ours included." docs/SOAK_RESULTS.md:2603.
- "Template grouping correctness ... Whether events that belong together are grouped together is not [measured]." docs/SOAK_RESULTS.md:4431-4433. Still open at docs/SOAK_RESULTS.md:5919.
- "No journald anywhere in the corpus" for the multiline claim. docs/SOAK_RESULTS.md:2604-2606.
- The 19.91 shipped control "I did not re-measure ... the control is unverified." docs/SOAK_RESULTS.md:2711-2713.
- "A person did not judge these. I did." The query set should be re-judged by a shifter. docs/SOAK_RESULTS.md:2881-2886.
- "a cross-source correlation detector cannot be developed against replay alone." docs/SOAK_RESULTS.md:3860-3861.
- "No source-owner approval of the registry." docs/SOAK_RESULTS.md:3453-3454, 3803, 4437-4438.
- Round 18 "left the hop that carries the records to it unmeasured" until round 21. docs/SOAK_RESULTS.md:5928-5929.
- Round 21 per-step timings "must not be read as a budget." docs/SOAK_RESULTS.md:6000-6001.
- "60,000 real records never contain a stamp field, because nothing upstream of the stamper writes one." docs/SOAK_RESULTS.md:6047-6048.

## 6. Contradictions and doubts for the orchestrator

1. Full stack above 5,000 a second. Lines 157-160 say the full stack never ran above 5,000 a second in steady state. Lines 1764-1790 report six full-cluster cells at 50,000 a second for five minutes. Lines 39-41 in the same "Read this first" section quote the 42,000 sustained result. My reading: the cost curve (core-seconds) was measured at 5,000 only, and the ceiling cells measured rate, not cost. The report can carry both if it says so. The orchestrator must confirm that wording.
2. Host state of the 42,000 figure. Line 1881-1882 says it came from a host at about 41 % speed, so it is a floor. Lines 1841-1850 blame runaway processes and report the self-test back at 400,000 a second. Which state the ceiling cells ran under is unverifiable. The report should say "on a laptop" and leave the 41 % out.
3. Journald template count. Round 8 reads 1,362 templates on 277,541 entries (docs/SOAK_RESULTS.md:3720). Round 18 reads 883 = 883 on the same 277,541 lines (docs/SOAK_RESULTS.md:5447). Both use pad `=` and numeric kept. I could not settle the difference from the lines read. Suggest the report quotes no journald template count, or quotes 883 as the round 18 equality check only.
4. Collector cost at flush 1 has three values in one section (27.70, 24.13, 25.10). I chose 27.70. See section 2a.
5. Which catalog the report describes. Rounds 10 to 20 describe a template catalog service with per-node counts and a supersession field. Round 21 already prices "the Forward transport hop into the stamper" (docs/SOAK_RESULTS.md:5926). Git history shows commit 64cc9ae "remove the canonical template identifier, group by the cover relation". A September rework turned the catalog into a stamper on the Forward loop. The soak briefs cannot say which of the section 2n guarantees survive that rework. The target-architecture brief must decide.
6. Round 9 attributes two round 7 results to round 8 (docs/SOAK_RESULTS.md:3909, 3956, 4159-4161, 4196). The 0.8 % control is at line 3151 and the +20.8 % at line 3233. The retractions stand; the round label is wrong.
7. The live lane after the stdout fix. The lane matches InfoLogger and the central family; after the severity fix it will not see stdout INFO at all (docs/SOAK_RESULTS.md:2581-2584). The soak leaves this open. The September live-lane bus decision may resolve it; that is outside the soak file.
8. Round 1's ceiling against round 2's. Round 1 states about 53,000 a second on two cores against a sink that always accepts (docs/SOAK.md:13-14). Round 2 states no collector ceiling number (docs/SOAK_RESULTS.md:1119) and a full-stack ceiling of about 42,000 (docs/SOAK_RESULTS.md:1824). They measure different things and different rigs. The report should not place 53,000 and 42,000 side by side as one curve.
9. Templating absolutes across rounds. The same 3,000,000 InfoLogger lines read 8.85 in round 6 (recipe plus fast masker) and 22.29 in round 18 (shipped code) with a 1.7× host swing (docs/SOAK_RESULTS.md:2374-2384, 5442, 5458-5464). Only ratios transfer. The report must quote round 18's percentages, not any absolute.
10. Round 1's live-lane finding is partly retracted. Round 1 measured InfoLogger falling to 5 % with the lane on (docs/SOAK.md:128-130). Round 2 attributes that artefact to starving the sink on a shared core (docs/SOAK_RESULTS.md:129-130). The coupling mechanism at docs/SOAK.md:135-138 is reasoning, and round 2's lane-on-its-own-tag arm shows a different halving (docs/SOAK_RESULTS.md:1016-1042). The report should carry round 2's lane cost, 41 to 68 %, and not round 1's 5 %.
11. Stage C's duplicated text. Lines 1235-1303 and 1359-1427 are the same text, both retracted (brief 2, note 3). Not a report matter, but a reader of the source should know.
