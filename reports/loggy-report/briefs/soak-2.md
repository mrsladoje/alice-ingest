# Soak brief 2 of 5 — docs/SOAK_RESULTS.md lines 1139 to 1982

Source file: docs/SOAK_RESULTS.md. Every line reference below points into that file.
Mandatory context read: lines 11 to 203 ("Read this first") and lines 263 to 306 (standing laptop warning).
Marking: **[A]** = architecture-level, belongs in the report. **[C]** = code-level, evidence only.

## 1. Coverage

This range holds the middle of round 2 of the soak. It opens with Stage C, the flush grid (lines 1139 to 1488), which "Read this first" supersedes in full (line 1141). It then records two defects: the OpenSearch output duplication defect (lines 1492 to 1547) and the heap-ceiling defect in the rig (lines 1551 to 1589). It then records the rig losing two thirds of its processor throughput on 26 August 2026 (lines 1593 to 1690). The Round 2 addendum follows (lines 1694 to 1850): heap under burst, the sustained ceiling, the overload failure mode, four instrument faults, and a host fault. The range closes with the Kafka decision against a collector-to-OpenSearch bus (lines 1852 to 1979). Only the two defect sections, the addendum, and the Kafka decision still stand. The Stage C flush numbers are retracted. The rig-loss section is partly superseded (line 1595).

Two Kafka decisions exist and this brief covers only the first. (a) The soak rejected a bus between the collector and OpenSearch on measured numbers (line 1852 onward). (b) The September 2026 review separately agreed a message bus for the live lane only, to decouple the shifter view from every worker's collector configuration. That second decision is outside this file. Both stand. Do not merge them.

## 2. Valid numbers

Only sections that still stand appear here. The standing warning applies to every row: the numbers come from a laptop, and only shapes, knees and rankings transfer (lines 265 to 267).

| number (exact) | what it measures | baseline or reference | what it means for the design | source line |
|---|---|---|---|---|
| 144,000 offered, 1,140 reported delivered, 751,381 held "and still climbing" **[A]** | duplication defect as shipped: a 120-second cell at 2,000 records a second | the same cell with the response buffer fix: 144,000 delivered, 144,001 held (records plus the bootstrap seed) | the shipped OpenSearch output re-sent bulks that OpenSearch had already indexed | 1498 to 1504 |
| 5.2× **[A]** | duplication factor as shipped | 1× with the fix | the index filled with copies while the collector reported failure | 1506 |
| 126 `http_do=-1` events, 0 with the fix **[C]** | failed-request events in a two-minute cell | 0 after setting the response buffer size | the bulk response exceeded the plugin's default read buffer; the fix removes every retry | 1516 to 1517 |
| 60 % **[A]** | share of the mix carried by the InfoLogger output, the only output that failed | the other two outputs carry 20 % each | the defect scales with response size, so the busiest stream trips first | 1518 to 1519 |
| 23 records a second **[A]** | the archive's real per-worker rate | the burst condition that crosses the buffer threshold | the defect stays hidden at normal rates and appears only in bursts | 1524 to 1526 |
| 2 GB container, `-Xmx2g` cannot start **[C]** | heap-ceiling defect in the rig's compose file | the rig default: 1 GB heap in a 2 GB container | a heap grid inside a fixed container measures fit, not cost | 1565 to 1576 |
| container = twice the heap **[C]** | the fix, `container_ceiling()` in `soak.py` | the rig's own 1 GB in 2 GB ratio | the 1g cell is unchanged, so earlier cells stay comparable | 1578 to 1581 |
| 2,842,277 records/s on 24 August, 1,157,678 on 26 August, 41 % of baseline **[C]** | the generator self-test, a pure compute benchmark | the healthy-rig figure two days earlier | the host, not the pipeline, lost two thirds of its throughput | 1607 to 1610 |
| 2.2 effective cores from 12 parallel jobs **[C]** | effective cores the virtual machine received | 12 processors configured and online | the machine was starved of cores, not slow per core (0.91 s guest against 0.88 s host) | 1614 to 1626 |
| 69.78 rising to 418.60 core-seconds per million **[C]** | cost per record before and after the loss | the healthy-rig flush 0.5 mean | cost per record inflates when a cell falls behind; the number describes the host | 1630 to 1632 |
| 900,050 and 899,300 records **[C]** | delivered volume of the two 15,000-a-second, 60-second burst windows | 900,000 by design | both windows offered as designed, so the heap comparison is clean | 1711 to 1712 |
| peak chunks 11, 9 (1g), 10, 10 (2g), 10, 10 (3g); lost 0 in every cell **[A]** | queue depth and loss under 15,000 a second for 60 seconds | the 64-chunk cap | heap size does not change burst absorption | 1714 to 1718 |
| peak memory 78.0, 76.6 (1g), 76.8, 76.2 (2g), 74.6, 76.4 MB (3g) **[A]** | collector peak memory under the 15,000-a-second burst | the three heap arms against each other | memory is flat across heaps | 1716 to 1718 |
| peak chunks 12 (1g), 9 and 39 (2g), 10 and 9 (3g); lost 0 **[A]** | queue depth under 30,000 a second for 30 seconds | the 64-chunk cap | a single heap repeated varies more than the three heaps differ | 1723 to 1733 |
| 17 % of the 64-chunk cap, 6 % of the 256 MB backlog cap, drain at most 8 s of 600 s **[A]** | the closest any burst cell came to a limit | the collector's shipped queue caps | nothing approached a limit under any burst the rig could offer | 1735 to 1738 |
| 1 void cell (`HB-30k30-1g-r2`: recorder lost 32.8 s, generator 29.6 % short); 1 outlier (`HB-30k30-2g-r2`: 39 chunks, 351.6 MB, every container about 20 % dearer) **[C]** | recorded exclusions in the burst blocks | the identical configuration read 9 chunks and 100.7 MB | the outlier is a machine-wide signature, not a heap effect | 1740 to 1745 |
| 1 GB heap **[A]** | the chosen worker OpenSearch heap | 2 GB and 3 GB, which matched on cost at 5,000 a second and on burst absorption | 1 GB is 2 GB less memory on a worker for the same result | 1756 to 1758 |
| 30,000 a second for 30 seconds **[A]** | the largest burst tested | the shipped queue caps, never approached | headroom above this burst exists but is unmeasured | 1760 to 1762 |
| 212 s at 50,000/s, mean 42,248/s, 0 lost (`OVER-50k-1g-r2`) **[A]** | the best five-minute cell, full cluster, logs on disk | the other five listed cells: 168 s, 146 s, 145 s, 136 s, 130 s | every configuration holds 50,000 a second for two to three and a half minutes, then decays | 1769 to 1779 |
| 50,042/s (0 to 59 s), 50,000/s (60 to 119 s), 34,500/s, 27,312/s, 17,875/s, ~11,800/s **[A]** | the decay timeline of the cleanest cell, `SOAK-50k-final` | the 50,000 offered | the full stack holds the peak for two minutes, then indexing throughput falls | 1783 to 1790 |
| collector 0.65 core, worker OpenSearch 2.16 cores, 2.81 of 4 (70 %); storage tier 3.44 of 4 (86 %); generator 0.07 of 2 (3.6 %) **[A]** | processor use while the 50,000 cell decayed | the four-core worker budget and the four storage cores | nothing is processor-bound when the rate decays; the cause is downstream | 1792 to 1796 |
| roughly six million documents **[A]** | the index size at which indexing throughput falls | merge pressure in two storage containers on four laptop cores | the ceiling belongs to the storage tier, not the collector; production has three storage nodes on their own hardware | 1798 to 1802 |
| queue 166 chunks, backlog 210.7 MB (82 % of cap), 0 dropped, drain 171.5 s (`SOAK-50k-final`) **[A]** | overload below the buffer cap | the 256 MB `storage.total_limit_size` cap | below the cap the collector absorbs the whole shortfall and loses nothing | 1809 to 1818 |
| queue 151 chunks, backlog 260.9 MB (cap hit), 258,474 dropped (1.8 %), 0 failed retries, 0 errors, drain 262.8 s (`SOAK-50k-clean`) **[A]** | overload at the buffer cap | the same cap | at the cap the collector discards oldest-first with no crash, no error, no failed retry, and it drains to empty | 1809 to 1820 |
| 103 of 182 chunks on disk **[A]** | chunks spilled from memory to disk in one overload cell | the disk-backed queue design | the memory-to-disk spill works as designed under overload | 1821 to 1822 |
| about 42,000 a second, reproduced at 41,435 and 42,248 **[A]** | the sustainable rate on this rig | the 50,000 peak | figure of record for the sustained ceiling | 1824 to 1825 |
| 50,000 a second for two minutes with zero loss **[A]** | the peak on this rig | the sustained 42,000 | figure of record for the peak | 1826 |
| 552 s recorded as 137 s; one 4-core container read as 10.25 cores **[C]** | the `soakrec.py` samples-as-seconds fault | 4-second sampling interval | every `mean_cores` figure printed before the fix reads about 4× too high; cumulative core-seconds were always correct | 1832, 1837 to 1839 |
| 2 % **[C]** | the offer gate that marks a burst cell VOID | the block's median offer | cells with a stalled recorder or a short generator no longer average in silently | 1834 |
| 15.5 MB a second **[C]** | the generator's write rate to log files | the virtual disk | an opt-in RAM-backed volume separates generator writes from disk effects; default unchanged | 1835 |
| 7.5 to 9.3 cores on a 16-core machine; twelve processes shared 3 cores **[C]** | two runaway server processes on the host during the addendum | the idle host | any cell measured in that window describes the host, not the stack | 1843 to 1847 |
| 400,000 a second with no shortfall **[C]** | the self-test reading after the host was idle again | the calibrated instrument | the gate to trust a cell: the machine must be idle first | 1849 to 1850 |
| 23 /s, 78 /s, 9,781 /s (297 workers) **[A]** | real worker median second of the busiest hour, busiest worker-second, busiest farm-second, all over six months | the stack's ~42,000 sustained and 50,000 peak | the stack carries far more than any recorded load | 1871 to 1877 |
| 538× **[A]** | the stack's sustained rate over the busiest worker-second | 42,000 over 78 | a buffer 538× larger than the load is a service to maintain, not a buffer | 1879, 1890 to 1891 |
| 4.3× **[A]** | one worker's stack over the whole farm's peak second | 42,000 over 9,781 | one worker's stack exceeds the farm's peak | 1880 |
| 54× **[A]** | the margin if the archive under-counts by ten times | 538× divided by 10 | the decision survives the archive being a floor | 1888 |
| 1,000 messages a minute **[A]** | the InfoLogger client cut-off per process | the true worker rate, which is higher than 23 a second by an unmeasured amount | the archive rates are floors | 1885 to 1887 |
| 310 bytes a record; 256 MB holds 865,674 records **[A]** | the shipped `storage.total_limit_size` in records | the record size used in the buffer arithmetic | the buffer's capacity in records | 1896 to 1897 |
| 17.4 hours at 23 /s; 5.1 hours at 78 /s **[A]** | the storage-tier outage the 256 MB buffer covers | the real worker rates above | the collector's own buffer already gives the durability a bus would add. See flag in section 7: these hours do not follow from 865,674 records at those rates | 1899 to 1902 |
| 27.7 core-seconds per million records at flush 1 **[A]** | the collector's own cost | the one resource an EPN worker rations | a Kafka output would add to this cost | 1935 to 1937 |
| 1 second **[A]** | the live lane's latency floor, set by flush 1 | a produce and a consume step would add to it | a bus raises the floor | 1938 to 1939 |
| above about 1,000 a second per worker, 13× the busiest worker-second **[A]** | the sustained rate that would reopen the Kafka question | 78 /s | a stated reversal condition | 1957 to 1958 |
| within 50× of 42,000 a second **[A]** | a real per-worker rate measurement that would reopen the question | the archive floors | a stated reversal condition | 1961 to 1962 |
| three hundred and more OpenSearch nodes **[A]** | the cluster-manager scaling risk | a topology problem | a bus does not fix it; fewer nodes or cross-cluster search does | 1964 to 1966 |

## 3. Retracted or superseded

| claim as reported in this range | line where reported | line that withdraws it | verdict |
|---|---|---|---|
| Stage C as a whole: five flush values crossed with three rates on the three-node cluster | 1139 to 1488 | 1141 to 1143; 13 to 15 | measured on a saturated rig; "right in shape but wrong in magnitude"; the 0.5-over-1 verdict is reversed |
| Flush 0.5 total 70.07, flush 10 total 90.47; worker OpenSearch 62 % dearer at flush 10; total 29 % dearer at flush 10 | 1163 to 1176 | 1141; 55 to 63 | superseded by the clean curve, which reads 97.69 at 0.5 and 105.33 at 10 with its floor at 1 |
| "Move `fluent_bit_flush_seconds` from 5 to 0.5"; flush 5 to 1 is −16.4 % total, −22.4 % worker OpenSearch, −36.6 % memory | 1237, 1242 to 1248, 1361, 1366 to 1372 | 1143; 20 to 27 | the chosen value is 1, not 0.5; the clean change from 5 to 1 is −3.4 % total, −24.5 % collector, −28.0 % memory |
| Flush 0.5 beats flush 1 by 11.1 % on means (69.78 against 77.53) with no overlap; flush 1 is six times less repeatable (13.4 % against 1.9 %) | 1258 to 1296, 1382 to 1420 | 1143; 76 to 84 | reversed: flush 1 beats 0.5 by 2.21 % with within-arm spreads under 1 %; 0.75 and 1 are interchangeable |
| The 1,000-a-second row is unusable and its cheapest value is flush 10 | 1191 to 1201 | 1141 | the whole stage is superseded; the row's unusability was predicted and is not itself a finding of record |
| Burst row: flush 10 reaches 68 chunks past the 64 cap; memory 333.7 MB | 1205 to 1216 | 1141 | superseded with the stage; the direction (memory rises with flush) survives in the clean curve at 61.1 to 105.7 MB (55 to 63) |
| Prediction three "half wrong" | 1218 to 1233 | 1141 | scored on saturated evidence |
| "Below flush 0.5 the pipeline stops keeping up"; drain ceiling near 13,800 a second; "0.5 is a minimum, not an edge"; "the curve turns around" | 1429 to 1476 | 1305 to 1357 (first retraction); 1307 to 1309 and 117 to 118 (second) | flush 0.25 offers at −0.98 % and costs 6 % more; no drain ceiling; nothing breaks |
| "Three things rule out the rig as the cause" (142× generator headroom, eight clean prior runs, progressive shortfall) | 1447 to 1456 | 1312 to 1347 | the rig was the cause; the same C6 configuration passed four times, then voided at −27.3 % |
| "Flush 0.5 remains the winner over 1, 2, 5 and 10 ... measured on the healthy rig and unaffected" | 1354 to 1356 | 1141 to 1143; 13 to 15 | contradicted: "Read this first" declares everything from Stage C onward saturated |
| "Nothing measured before 26 August is affected ... the whole flush grid from 0.5 to 10 ran on the healthy rig" | 1654 to 1656 | 13 to 15; 1141 | same contradiction; "Read this first" wins |
| The rig kept degrading after the loss | not visible in the current text | 1595 to 1597 | withdrawn by the superseded banner; the reading compared two fixtures and caught a transient |
| "The obvious next step is a host restart" | 1649 to 1650 | 1597 to 1598; 184 to 187 | a host restart was never available; the fix was to lower the rate to 5,000 a second |
| Void list needing a rerun: C16 to C19, D4 to D7, E1 to E3, G1 to G4, F1 to F12 | 1660 to 1666 | 13 to 15 and 108 to 128 | the 47-cell re-run of 27 August covered these; its results are in "Read this first" |
| "The recommended product change ... is not made here" for the duplication fix | 1544 to 1547 | 165 to 166 | the fix has since shipped to `collector.yaml.j2` |
| "The full stack has never run above 5,000 a second in steady state" | 1972 to 1973; 150 to 152 | 1764 to 1790 in the same range | contradicted by the addendum's six full-cluster cells at 50,000 a second for five minutes; see section 7 |
| The sustained figure "was measured on a host running at about 41 % of its normal speed" | 1881 to 1882 | 1841 to 1850 | unverifiable: the addendum attributes its host trouble to runaway server processes and reports the self-test back at 400,000 a second; the file does not say which host state the 42,000 cells ran under |

## 4. Defects found and fixed

| component | what was wrong | fix | line |
|---|---|---|---|
| Collector, OpenSearch output plugin **[A]** | the shipped template set no response `buffer_size`; large bulk responses overflowed the default buffer; the plugin reported failure and retried bulks OpenSearch had already indexed; 5.2× duplication while the collector reported near-zero delivery | set `buffer_size` on every `opensearch` output; shipped to `collector.yaml.j2` | 1494 to 1547; shipped per 165 to 166 |
| Rig compose file, worker OpenSearch container **[C]** | `mem_limit` fixed at 2g while the heap varied; heaps of 2g and 3g never started; the heap grid silently measured fit, not cost | `container_ceiling()` in `soak.py` gives the container twice the heap; stage D rerun from the start | 1553 to 1589 |
| Rig recorder `soakrec.py` **[C]** | counted samples as seconds; every `mean_cores` figure read about 4× too high | window taken from the wall clock between first and last live sample; tables recomputed from cumulative core-seconds | 1832, 1837 to 1839 |
| Rig `heapburst.py` **[C]** | read keys `peak_chunks` and `lost` that do not exist; both metrics reported empty | reads `peak_total_chunks`; loss derived as `output_dropped` plus `output_retries_failed` | 1833 |
| Rig burst report `hbreport.py` **[C]** | burst cells had no validity gate; a stalled recorder or a short generator averaged in silently | a cell is marked VOID with its reason: recorder time lost, or an offer more than 2 % below the block's median | 1834 |
| Rig log-file placement **[C]** | generator writes of 15.5 MB a second could not be separated from disk effects | opt-in RAM-backed volume via `SOAKLOGS_VOLUME`; default unchanged | 1835 |
| Host, not the stack **[C]** | two runaway server processes took 7.5 to 9.3 of 16 cores and restarted with terminal sessions | check that the machine is idle before trusting a cell; the self-test is the calibrated instrument | 1843 to 1850 |

## 5. Decisions this range settles

| decision | evidence | line |
|---|---|---|
| The OpenSearch outputs must set a response buffer size. **[A]** | 751,381 records held against 144,000 offered as shipped; 144,001 with the fix; 126 failed-request events against 0 | 1501 to 1517, 1544 to 1547 |
| The worker OpenSearch heap stays at 1 GB. Tuning it can be skipped. **[A]** | zero loss and flat queue depth across 1g, 2g, 3g under 15,000/s for 60 s and 30,000/s for 30 s; within-arm variation exceeds between-arm variation | 1704 to 1758; confirmed at 37 to 38 |
| Bursts are absorbed by the collector's disk-backed chunk queue, not by OpenSearch heap. The two sit in different places. **[A]** | fifteen cells across three load shapes agree; the indexing buffer is a staging area, not a reservoir | 1747 to 1754 |
| The sustained ceiling on this rig is about 42,000 records a second; the peak is 50,000 for two minutes with zero loss. **[A]** | reproduced at 41,435 and 42,248; `SOAK-50k-final` timeline | 1783 to 1790, 1824 to 1826 |
| The ceiling belongs to the storage tier's merge pressure, not to the collector. **[A]** | collector at 0.65 core while the rate decays; storage at 86 % of its four cores; decline after roughly six million documents | 1792 to 1802 |
| Overload is safe: below the cap nothing is lost; at the cap the collector discards oldest-first with no crash, no error and no failed retry, then drains to empty. **[A]** | `SOAK-50k-final` 0 dropped at 82 % of cap; `SOAK-50k-clean` 1.8 % dropped at the cap; both drained | 1804 to 1822 |
| A hard durability guarantee, if wanted, is one number: `storage.total_limit_size`. **[A]** | the 256 MB buffer holds 865,674 records | 1896 to 1913 |
| No Kafka bus between the collector and OpenSearch. **[A]** | 538× margin over the busiest worker-second; 17.4-hour buffer cushion; safe overload; a bus would move the tier decision from index templates into a topic mapping and would double the collector's outputs | 1852 to 1979 |
| The tier decision stays in OpenSearch index templates (`require.role: worker` or `storage`); the collector writes only to its local node. **[A]** | a bus would restate that rule at produce time or in a routing consumer, so policy would live in two places | 1917 to 1922 |
| The Kafka question reopens on four named conditions only. **[A]** | sustained per-worker rate above about 1,000/s; a required second consumer; outages longer than the cushion; a real rate within 50× of 42,000 | 1953 to 1962 |
| Negative result: the mean-cores figures printed before the recorder fix are not to be used; cumulative core-seconds are. **[C]** | 4× error from samples counted as seconds | 1832, 1837 to 1839 |

## 6. Not measured, not tested, or unmeasured

Verbatim, with lines:

- "the four cells that were meant to answer it must be rerun" and "Whether anything below 0.5 is better is **unknown and untested**" — line 1356 to 1357. Later answered by the clean re-run (lines 117 to 118, 55 to 63).
- "**Not diagnosed.** Why the hypervisor is confined to two cores on a sixteen-core host that is idle." — line 1647 to 1648.
- "Nothing came near a cap, so there is headroom — but it is unmeasured headroom, not proven headroom." — line 1760 to 1762.
- "The true worker rate is higher than 23 a second by an unmeasured amount." — line 1886 to 1887.
- "❌ **The full stack has never run above 5,000 a second in steady state**, nor above a 30,000-a-second burst of 30 seconds." — line 1972 to 1973. See the contradiction in section 7.
- "❌ **Every rate above 5,000 a second in this round came off a degraded host.** The rankings transfer. The absolute numbers do not." — line 1974 to 1975.
- "❌ **The archive bounds the worker rate from underneath only.**" — line 1976.
- "Run-to-run spread | not measured" for flush 5 as shipped — line 1292 and 1416 (retracted section).
- From the standing warning, and binding on every number here: "One micro-benchmark on `epn228` would give the conversion factor. It has not been run." — line 287 to 288.
- From "Read this first", binding on the Kafka margin: "The size of that error is unmeasured" (sharing the four cores with reconstruction work) — line 149; "The rate at which internal placement starts to matter is **unmeasured**" — line 172 to 173.

## 7. Cross-range notes

1. **Contradiction inside the file on the full-stack steady-state ceiling.** Lines 1972 to 1973 and lines 150 to 152 say the full stack never ran above 5,000 a second in steady state. Lines 1764 to 1790 report six full-cluster cells at 50,000 a second for five minutes, and line 1876 uses the ~42,000 sustained figure. The reconciler must decide which statement the report carries. The likely reading: the addendum was written after the "not rest on" list and the list was not updated.
2. **Buffer-hours arithmetic does not close.** Line 1897 gives 865,674 records in 256 MB at 310 bytes a record. At 23 records a second that is 10.5 hours, and at 78 a second it is 3.1 hours. Lines 1900 to 1902 print 17.4 hours and 5.1 hours. The ratio of the two printed hours matches 78 over 23, so a different record count or rate feeds them. Line 1895 points to a section titled "And the loss itself is an artefact of the test rate", outside this range, for the arithmetic. The reconciler should check that section before the report quotes either hour figure. "Read this first" line 30 repeats 17.4 hours.
3. **The Stage C flush block appears twice, word for word.** Lines 1235 to 1303 and lines 1359 to 1427 are the same text. Both are retracted. A reconciler editing the source should know this is duplication, not two separate runs.
4. **"Read this first" collector-cost figures differ between its own tables.** Line 23 gives the collector 27.70 at flush 1 (three-run alternating comparison). Line 59 gives 24.13 (eight-value curve). Line 80 gives 25.10 (three-run basin). The Kafka section at line 1935 uses 27.7. All three are separate run sets on the same setting. The report should quote one and say which run set it comes from.
5. **The recorder fix at line 1832 reaches back before this range.** Every `mean_cores` figure printed before the fix reads about 4× too high. Stages A and B (before line 1139) may carry such figures. Cumulative core-seconds per million are unaffected. The reconciler for brief 1 should check whether any per-core utilisation number there is a `mean_cores` reading.
6. **Line 1895 depends on the buffer arithmetic in a Stage B section** ("And the loss itself is an artefact of the test rate"), outside this range.
7. **Line 1859 names `docs/ARCHITECTURE.md` as the reference design that places Kafka after the collector.** The Kafka decision here rejects that placement. The September 2026 review's live-lane bus is a separate decision with a separate purpose. The report must state both and must not present the addendum's rejection as a rejection of the live-lane bus.
8. **The duplication fix status changed after this range was written.** Line 1544 to 1547 says the template change "is not made here". Line 165 to 166 says the fix shipped to `collector.yaml.j2`. The report should state that it shipped.
9. **The rig-loss "void" list at lines 1660 to 1666 was later cleared** by the 47-cell re-run recorded at lines 11 to 128. Stage E (core split), G (interaction) and F (confirmation) results live in "Read this first", not in this range. The E result is "no effect" for the internal split only; external pinning of four cores is still required (lines 130 to 178).
10. **Stage C's chunk cap of 64 and backlog cap of 256 MB** (lines 1179 to 1180, 1735 to 1736, 1749) are the same collector settings the overload section and the Kafka section rely on. Those caps are architecture-level parameters and appear in valid sections, so the report may carry them even though Stage C itself is retracted.
11. **The addendum text says "Eight cells at 50,000 a second" at line 1766, but the table at lines 1769 to 1776 lists six.** Two cells are unaccounted for in the text. The reconciler should not write "eight" without checking.
12. **Line 1881 to 1882 says the sustained figure came from a host at 41 % speed, so it is "a floor".** The addendum (lines 1841 to 1850) describes a different host fault and a self-test back at 400,000 a second. Which host state the 42,000 cells ran under is unverifiable from this range. The report should say the figure comes from a laptop and leave the 41 % claim out unless another range confirms it.

## 8. Story

- The flush grid was the wrong experiment on a broken instrument. Every Stage C number here, including the 0.5-over-1 verdict, is retracted; the clean re-run in "Read this first" chose flush 1 (lines 13 to 15, 1141 to 1143).
- The soak found a real product defect before Stage C ran a cell. The shipped OpenSearch output duplicated records 5.2 times under load and reported the opposite; a response buffer size fixes it (lines 1494 to 1517).
- The rig lied twice, and both times the fix was in the rig. A 2 GB heap could not start in a 2 GB container, and the recorder counted samples as seconds, inflating per-core figures 4× (lines 1565 to 1580, 1832).
- A shared plateau is a shared cause. Four cells agreeing at 13,800 records a second looked like a drain ceiling; they were four readings of a host that had lost two thirds of its cores (lines 1344 to 1347, 1609 to 1626).
- OpenSearch heap does not absorb bursts, the collector's disk-backed queue does. Across 1g, 2g and 3g, the queue never used more than 17 % of its 64-chunk cap and nothing was lost; 1 GB stands (lines 1735 to 1758).
- The stack on a laptop sustains about 42,000 records a second and holds 50,000 for two minutes with zero loss. The decay after that is storage-tier merge pressure, not the collector (lines 1783 to 1802, 1824 to 1826).
- Overload is safe by design. Below the 256 MB cap the collector loses nothing; at the cap it discards 1.8 % oldest-first with no crash, no error and no failed retry, then drains to empty (lines 1809 to 1822).
- A Kafka bus between the collector and OpenSearch is rejected on these numbers: the stack carries 538 times the busiest worker-second, the collector's own buffer covers a storage outage of hours, and a bus would move the tier routing out of the index templates (lines 1879, 1899 to 1902, 1917 to 1922). The separate September 2026 live-lane bus decision is unaffected by this.
