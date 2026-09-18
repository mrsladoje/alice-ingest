# Soak brief 1 of 5: docs/SOAK_RESULTS.md lines 1 to 1138

Source file: docs/SOAK_RESULTS.md. Every line number below points into that file unless another path is named. Each numbered fact is marked [arch] when it belongs in the report or [code] when it is evidence only.

## 1. Coverage

This range holds round 2 of the soak from its front matter to the end of Stage B. Round 2 measures processor time, which is the resource a worker rations (docs/SOAK_RESULTS.md:7). The range contains: the front matter (lines 1 to 9), the section "Read this first" written on 27 August 2026 after a 47-cell re-run (lines 11 to 203), the decision to treat 1,000 records a second as a per-worker rate (lines 204 to 259), the standing warning that every number comes from a laptop (lines 263 to 306), the pre-flight blocker answered from six months of archive (lines 310 to 460), Stage A, the instrument and the noise floor (lines 464 to 699), what was built before any cell ran (lines 701 to 719), and Stage B, the collector screen (lines 722 to 1138). Stage B covers threading arms, spool arms, the pause knob, the buffer arithmetic, the live-lane arms, lane compression, the lane-on-its-own-tag arm, and the two 50,000-a-second cells. No later round appears in this range.

## 2. Valid numbers

Two rigs appear in this range. The healthy rig ran Stage A and Stage B at 20,000 records a second. The saturated rig ran the "Read this first" re-run at 5,000 records a second on a host at about 41 % of its normal speed (line 180). Absolute core-seconds from the two rigs must not be compared (lines 195 to 197). Every number below names its rig.

### 2a. Read this first, the re-run of 27 August 2026 (saturated rig, 5,000 records a second)

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line | Level |
|---|---|---|---|---|---|
| 108.40 core-seconds | Total over four cores, shipped flush 5, mean of three runs | Chosen flush 1 at 104.67 | Flush 1 costs 3.4 % less over the whole stack | docs/SOAK_RESULTS.md:24 | arch |
| 104.67 core-seconds | Total over four cores, chosen flush 1, mean of three runs | Shipped flush 5 at 108.40 | The whole-stack saving is small because OpenSearch dominates the four cores | docs/SOAK_RESULTS.md:24, :33-35 | arch |
| 36.70 core-seconds | The collector's own cost at flush 5 | Flush 1 at 27.70 | The collector's footprint falls by 24.5 % at flush 1 | docs/SOAK_RESULTS.md:25 | arch |
| 27.70 core-seconds | The collector's own cost at flush 1 | Flush 5 at 36.70 | This is the part a worker is allowed to add, and it drops by a quarter | docs/SOAK_RESULTS.md:25, :36-38 | arch |
| 87.1 MB | Peak memory at flush 5 | Flush 1 at 62.7 MB | Memory falls by 28.0 % at flush 1 | docs/SOAK_RESULTS.md:26 | arch |
| 62.7 MB | Peak memory at flush 1 | Flush 5 at 87.1 MB | Shorter flush holds less in flight | docs/SOAK_RESULTS.md:26 | arch |
| 5 s to 1 s | Live-lane latency floor, flush 5 against flush 1 | The flush interval is the floor | The live lane answers five times sooner at flush 1 | docs/SOAK_RESULTS.md:27 | arch |
| 1.5 % and 2.7 % | Run-to-run spread, flush 5 and flush 1 | The gap between arms | The spreads are below the gap, so the ranking holds | docs/SOAK_RESULTS.md:28 | code |
| 107.82 and 106.15 | Shipped arm's best run and chosen arm's worst run | Each other | The ranges do not overlap, so flush 1 wins on every run | docs/SOAK_RESULTS.md:30-31 | code |
| 1 GB | The heap size that stands | Larger heaps tested in the addendum | Heap size makes no difference to burst absorption, so 1 GB ships | docs/SOAK_RESULTS.md:38-39 | arch |
| About 42,000 records a second | Sustained rate of the whole rig, 50,000 held for two minutes with zero loss | Withdrawn 120,000 claim at line 107 | The full stack on this laptop sustains about 42,000 a second, measured in the addendum outside this range | docs/SOAK_RESULTS.md:39-41, :107 | arch |
| 538× | Stack capacity against the busiest worker-second ever recorded | Busiest worker-second 78 a second (line 368) | A Kafka bus between the collector and OpenSearch is rejected on these numbers | docs/SOAK_RESULTS.md:43-46 | arch |
| 17.4 hours | Outage the collector's buffer covers at a real worker's rate | Derived at line 911 | The buffer already absorbs most of a day, so no bus is needed for durability | docs/SOAK_RESULTS.md:45-46, :911 | arch |
| 107.34, 103.50, 97.69, 92.84, 89.37, 90.39, 96.45, 105.33 | Total core-seconds at flush 0.125, 0.25, 0.5, 0.75, 1, 2, 5, 10 | The floor at flush 1 | The flush curve is a near-symmetric U with its floor at 1 | docs/SOAK_RESULTS.md:59-66, :68 | arch |
| 20.40, 21.50, 22.60, 23.74, 24.13, 25.81, 27.29, 27.45 | Collector core-seconds at the same eight flush values | The total in the same rows | Collector cost falls monotonically as flush shortens, so the U belongs to OpenSearch and the storage tier | docs/SOAK_RESULTS.md:59-66, :70-72 | arch |
| 61.1, 60.8, 61.3, 64.5, 66.0, 74.5, 83.6, 105.7 MB | Peak memory at the same eight flush values | Flush 1 at 66.0 MB | Memory rises with flush interval | docs/SOAK_RESULTS.md:59-66 | arch |
| 17.9 % | Best to worst across the 20× flush range | The withdrawn saturated claims | Flush matters, but less than the saturated cells suggested | docs/SOAK_RESULTS.md:73-75 | arch |
| 94.27 at 0.37 %, 92.84 at 0.57 %, 92.19 at 0.85 % | Basin means and spreads for flush 0.5, 0.75, 1, three interleaved runs each | Each other | The tightest data of the round, spreads under 1 % | docs/SOAK_RESULTS.md:77-83 | code |
| 2.21 % and 0.71 % | Flush 1 against 0.5 and against 0.75 | Floor 0.85 % | Flush 1 beats 0.5. Flush 0.75 and 1 are interchangeable | docs/SOAK_RESULTS.md:85-86 | arch |
| About 77 and 278 core-seconds per million | Cost in cells that offered cleanly against the cell that fell 8.15 % behind | Each other | Cost tracked offer shortfall, which is why every saturated claim was withdrawn | docs/SOAK_RESULTS.md:94-97 | code |
| 2.7 % against a 5.5 % floor | Re-measured internal core isolation effect | The noise floor | Internal pinning has no effect at the rates tested | docs/SOAK_RESULTS.md:100 | arch |
| 101.61 | Total of the 2-core OpenSearch arm, offering perfectly | The 3-of-4-cores claim | OpenSearch does not need three of four cores | docs/SOAK_RESULTS.md:101 | arch |
| 2.1 % and 0.4 % against a 6.7 % floor | Re-measured heap 2g effect | The noise floor | Heap size has no effect, tuning that can be skipped | docs/SOAK_RESULTS.md:102, :110-111 | arch |
| 8.8 % more | Re-measured cost of the two-process arm t3 against t0 | t0 | t0 stands, confirmed on a second machine | docs/SOAK_RESULTS.md:104 | arch |
| −0.98 % offer, 6 % more cost | Flush 0.25 re-measured | Flush 1 | Flush 0.25 costs more and breaks nothing | docs/SOAK_RESULTS.md:105 | code |
| 27.8, 11.20, 8.97 core-seconds per million | Collector cost at 5,000, 20,000 and 50,000 records a second | Each other | Cost per record falls as rate rises, so a straight line from one point is not a ceiling | docs/SOAK_RESULTS.md:147-149 | arch |
| About 105 core-seconds per million | Whole-stack cost at 5,000 a second | The collector's 27.8 | The collector is about a quarter of the stack's cost at that rate | docs/SOAK_RESULTS.md:141-146 | arch |
| 33 million records at 50,000 a second, 0.352 of one core | The collector alone, lost none, empty queue | The straight-line saturation guess near 38,000 | The collector alone is comfortable at 50,000 a second on four pinned cores | docs/SOAK_RESULTS.md:150-153, :156 | arch |
| 28 % on the saturated rig, 41 to 68 % on the healthy rig | Live-lane cost on the collector | Lane off | The direction and rough size agree across rigs | docs/SOAK_RESULTS.md:175-176 | arch |
| About 41 % | The host's speed during the re-run | Its normal speed | Absolute numbers from the re-run do not compare with the healthy rig | docs/SOAK_RESULTS.md:180-181 | code |
| 5,000 records a second | Reference rate of the re-run | The intended 20,000 | Chosen by probing so that no cell could saturate. It is a quarter of the intended rate | docs/SOAK_RESULTS.md:187-189, :196 | code |
| Four seconds | Sampling interval of the re-run | One second before | Totals come from cumulative counters, so they are unaffected | docs/SOAK_RESULTS.md:189-191 | code |

### 2b. The rate scope and the archive derivation

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line | Level |
|---|---|---|---|---|---|
| 1,000 records a second | Treated as per worker, by decision not measurement | The archive median 23 a second | Tests one worker at a safe multiple of its real load | docs/SOAK_RESULTS.md:206-207, :233 | arch |
| 43× | 1,000 a second against the archive median of 23 a second per worker | Archive median | 1,000 a second is a safety rate, not a steady rate anyone meets | docs/SOAK_RESULTS.md:233 | arch |
| 256× | 20,000 a second against the busiest worker-second in six months | Busiest worker-second 78 | 20,000 is the only rate where arms can be ranked | docs/SOAK_RESULTS.md:234 | arch |
| 13× to 640× | Range of test load over what the archive shows one worker carrying | Archive | The safe direction, chosen on purpose | docs/SOAK_RESULTS.md:248-249 | arch |
| 179 dumps, 248,828,513 records, 312 hosts | What the archive scan read | None | Six months of InfoLogger, 31 December 2025 to 29 June 2026 | docs/SOAK_RESULTS.md:319-321 | arch |
| 308 workers, 221,707,411 records, 89.1 % | Worker share of the archive | Whole archive | Worker numbers exclude the four non-worker hosts | docs/SOAK_RESULTS.md:327 | arch |
| 4 hosts, 27,121,102 records, 10.9 % | Non-worker share | Whole archive | One infrastructure host distorts any per-worker figure | docs/SOAK_RESULTS.md:328 | arch |
| 26,448,820 records | One infrastructure host alone | Six times the busiest worker | Excluded from every worker number | docs/SOAK_RESULTS.md:330-332 | code |
| 20,151,049 records, 297 workers | The busiest hour, 15 May 2026, 22:00 to 23:00 UTC | Whole archive | The hour that decides the question | docs/SOAK_RESULTS.md:341-343 | arch |
| 23, 33, 48, 57, 78 records a second | One worker in the busiest hour: median, 90th, 95th, 99th percentile, busiest second | The plan's 1,000 | A worker does tens of records a second, not thousands | docs/SOAK_RESULTS.md:364-368, :336-337 | arch |
| 6,785, 8,537, 9,781 records a second | The whole farm in the busiest hour: median, 95th percentile, busiest second | The plan's burst band 10,000 to 20,000 | The plan's rates are farm rates that were read as worker rates | docs/SOAK_RESULTS.md:374-379 | arch |
| 3 records a second | Farm median second over all 180 days | The busiest hour | The archive is mostly idle | docs/SOAK_RESULTS.md:382-383 | code |
| 187,210 records | One farm-wide second in the 180-day window | Busiest-hour peak 9,781 | Not corroborated, recorded and not used | docs/SOAK_RESULTS.md:386-390 | code |
| Within 0.6 % | Spread of the top ten workers in the busiest hour | Each other | The steady per-worker rate is a heartbeat, one repeating size-report line | docs/SOAK_RESULTS.md:405-410 | arch |
| Up to one million a run, 2,194,073 records | The published 2025 ALICE paper figure, FLP side | 20,151,049 in one EPN hour | The two figures describe different clusters and do not conflict | docs/SOAK_RESULTS.md:417-427 | arch |
| About 3 a second per collector | What a per-farm 1,000 a second would mean per worker | The plan's 53× headroom | Headroom grows rather than shrinks under the farm reading | docs/SOAK_RESULTS.md:441-444 | arch |
| About twice the whole farm's peak | 20,000 a second offered to one collector | Farm peak 9,781 | A configuration that fails at 20,000 has not failed at anything a worker meets | docs/SOAK_RESULTS.md:434-439 | arch |

### 2c. The standing warning

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line | Level |
|---|---|---|---|---|---|
| 16 physical cores, 12 given to the rig | The laptop | The production machine: 64 physical, 128 logical | Shapes, knees and rankings transfer. Absolute rates do not | docs/SOAK_RESULTS.md:265-272 | arch |
| 4 physical cores, 8 logical processors | The worker's budget for the logging stack | The laptop's 4 cores with no multithreading | The laptop comparison is less unfavourable than an earlier draft said | docs/SOAK_RESULTS.md:276-281 | arch |
| Two storage nodes, one replica | The rig's storage tier | Production: three nodes, two replicas | Every worker-side figure is optimistic for this reason | docs/SOAK_RESULTS.md:294-299 | arch |

### 2d. Stage A, the instrument and the noise floor (healthy rig)

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line | Level |
|---|---|---|---|---|---|
| 11.09, 11.20, 11.27, mean 11.19, spread 0.18 which is 1.61 % | Collector core-seconds per million at 20,000 a second, three control runs on recorder R2 | The gap an arm must exceed | A difference under 1.61 % at 20,000 a second is not a result | docs/SOAK_RESULTS.md:500-505, :497 | arch |
| 71.50, 75.81, 78.64, mean 75.32, spread 7.14 which is 9.48 % | Collector core-seconds per million at 1,000 a second, three control runs | The gap an arm must exceed | At 1,000 a second no arm in the plan can register | docs/SOAK_RESULTS.md:506, :531-534 | arch |
| 1.95 % and 28.07 % | The first, mixed-instrument floors, withdrawn | The single-build floors 1.61 % and 9.48 % | Two builds of the instrument cannot give a noise floor | docs/SOAK_RESULTS.md:508-513 | code |
| 6.7 times | Per-record cost at 1,000 a second against 20,000 | 75.32 against 11.19 | At 1,000 a second the collector's cost is almost all overhead | docs/SOAK_RESULTS.md:524-530 | arch |
| 68.6 core-seconds, 89.3 % | The main event loop's share of the collector at 20,000 a second | Whole collector | One single-threaded loop does almost everything | docs/SOAK_RESULTS.md:544, :550-552 | arch |
| 5.7, 1.3, 1.0 core-seconds | The three output worker groups | Main loop 68.6 | Outputs are cheap beside the loop | docs/SOAK_RESULTS.md:545-547 | code |
| 68.5 and 0.1 core-seconds | The two threads named as the pipeline | Each other | There is one main loop and it is single-threaded | docs/SOAK_RESULTS.md:550-552 | code |
| 2 | Default output workers per http output, measured | The template sets it only on the health output | Answers Stage A's third exit criterion | docs/SOAK_RESULTS.md:554-557 | code |
| 0.25 of one core, 0.223 in one thread | Collector at 20,000 a second | Four pinned cores | Three of four cores do nothing for the collector | docs/SOAK_RESULTS.md:559-563 | arch |
| Near 22 % of one core, near 90,000 a second | Main loop share and its projected saturation | Round 1's ceiling of about 53,000 | A pre-Stage-B estimate, not a measurement | docs/SOAK_RESULTS.md:565-568 | code |
| 76.8 core-seconds, 10.67 per million, 0.250 mean cores, 6.3 % saturation | Collector in one 20,000-a-second cell | Four pinned cores | The instrument records every service | docs/SOAK_RESULTS.md:649 | code |
| 10.6 core-seconds, 1.47 per million, 1.8 % | Generator on two pinned cores | The 80 % rule | Rig overhead, to be subtracted before comparing against four cores | docs/SOAK_RESULTS.md:650, :653-655, :282-285 | code |
| 2.9 core-seconds, 0.40 per million, 0.5 % | Fake sink on two pinned cores | The 80 % rule | Not starved | docs/SOAK_RESULTS.md:651, :653-655 | code |
| 835 MB and 865 MB | Disk written by the collector and the generator in one five-minute cell | 1.7 GB on one laptop drive | The shared drive is real and the generator feels it first | docs/SOAK_RESULTS.md:657-662 | code |
| 2.3 seconds against 0.072 | Input and output stall time, generator against collector | Each other | The generator feels the shared drive first | docs/SOAK_RESULTS.md:659-661 | code |
| 4.47 % under, one second 97.5 % short, gaps up to 10.5 seconds, 6.6 % samples late | The void first attempt at A4 | 308 samples, worst gap 1.0 second, zero late on every other cell | The cell was rerun, not ranked | docs/SOAK_RESULTS.md:666-673 | code |
| 2 % | Late-sample threshold above which a cell is void | None | Instrument rule added after A4 | docs/SOAK_RESULTS.md:676-681 | code |
| 400 records a second when asked for 1,000 | The generator selftest fault, six tenths of the mix dropped | 1,000 asked | Fixed. Now paces to within 0.02 % at 1,000, 20,000 and 50,000 | docs/SOAK_RESULTS.md:686-691 | code |
| 2,842,277 records a second | The generator's measured ceiling on two pinned cores | 57× the highest rate in the plan | Gate zero requires a cell to sit at least 20 % under this ceiling | docs/SOAK_RESULTS.md:695-697 | code |
| About 0.8 ms | Cost of one whole-rig sample | None | The recorder is cheap enough to sample every container | docs/SOAK_RESULTS.md:707 | code |
| 8 to 12 processors | The rig's processor allocation, raised | The plan's allocation: cores 0 to 3 worker services, 4 to 7 storage tier, 8 to 9 generator, 10 to 11 sink and live lane | Every service is pinned to its own cores | docs/SOAK_RESULTS.md:716-719 | code |

### 2e. Stage B, the collector screen (healthy rig, 20,000 records a second, flush 5, fake sink)

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line | Level |
|---|---|---|---|---|---|
| 11.20 | Control B1, collector core-seconds per million | Stage A's 11.09, 11.20, 11.27 | The control reproduces across stages | docs/SOAK_RESULTS.md:730-733 | code |
| 80.6 core-seconds, 11.20 per million, main loop 72.5 at 90 %, 132.4 MB | Arm t0, as shipped | The other three arms | The shipped configuration is the cheapest thing measured | docs/SOAK_RESULTS.md:739, :757-759 | arch |
| 89.9, 12.48 per million, +11.5 %, main loop 57.6 at 64 %, 148.4 MB | Arm t1, threaded inputs | t0 | Threading the inputs alone costs 11.5 % | docs/SOAK_RESULTS.md:740 | arch |
| 86.6, 12.03 per million, +7.4 %, main loop 31.5 in process 1 | Arm t3, two collector processes | t0 | The cheapest arm and still dearer than shipping as is | docs/SOAK_RESULTS.md:741, :744-748 | arch |
| 102.0, 14.16 per million, +26.5 %, main loop 7.0 at 7 %, 144.5 MB | Arm t2, filters moved into input processors | t0 | The mechanism works and the total cost rises by more than a quarter | docs/SOAK_RESULTS.md:742, :763-770 | arch |
| 34.5 and 52.1 core-seconds, 0.112 and 0.169 mean cores | The two t3 processes, tailed families and InfoLogger | Four shared cores | Two main loops double the ceiling by construction, at 7.4 % | docs/SOAK_RESULTS.md:745-748 | code |
| 7× and 16× the floor | t1 and t2 against the 1.61 % floor | Noise floor | These are results, not scatter | docs/SOAK_RESULTS.md:772-773 | code |
| 10,086,158 of 15,120,000, 66.7 % | InfoLogger lost in s0, the tcp tap, during a fifteen-minute sink outage | s1 at 68.7 % | The tap is not the lever | docs/SOAK_RESULTS.md:821, :829-832 | arch |
| 10,383,758 of 15,120,000, 68.7 % | InfoLogger lost in s1, the appender and file tap | s0 at 66.7 % | A file in front of the socket does not save the records | docs/SOAK_RESULTS.md:821, :829-832 | arch |
| 0 | DDS lost and stdout lost in both spool cells | InfoLogger's loss | The tailed inputs pause, so the file is a queue only for the unread part | docs/SOAK_RESULTS.md:822-823, :842-846 | arch |
| 60 s and 36 s | Time into the outage of the first drop, s0 and s1 | Each other | Loss begins when the output buffer cap is hit | docs/SOAK_RESULTS.md:824, :836-839 | code |
| 264.5 MB and 269.0 MB | Buffer on disk at its peak, s0 and s1 | The 256M cap on every output | The cap was reached and the oldest chunks were discarded | docs/SOAK_RESULTS.md:825, :836-839 | arch |
| 22.2 M of 25.2 M and 22.9 M of 25.2 M | Records that entered the collector, s0 and s1 | What the generator offered | Between 9 % and 12 % never entered, because tail inputs paused | docs/SOAK_RESULTS.md:840-843 | code |
| 15.46 and 9.38 core-seconds per million, appender 0.55 | Collector cost in s0 and s1, plus the appender | Each other | A tail input costs less than a tcp input plus JSON parsing. Confounded by outage retry work, a signal not a measurement | docs/SOAK_RESULTS.md:826-827, :863-868 | code |
| 66.7 %, 68.7 %, 68.7 %, 66.8 % | InfoLogger lost in B12, B13, B14, B15 with the pause knob off, off, on, on | Each other | The pause changed nothing to a tenth of a per cent. It is an input knob and the loss is at the output | docs/SOAK_RESULTS.md:882-893 | arch |
| 88.3 %, 90.9 %, 91.3 %, 97.2 % | Entered the collector in B12 to B15 | Each other | The pause knob changes how much enters, not how much is lost | docs/SOAK_RESULTS.md:882-885 | code |
| 310 bytes | Mean record on the wire | The 256 MB buffer | The buffer holds 865,674 records | docs/SOAK_RESULTS.md:901-903 | arch |
| 865,674 records | What the 256 MB buffer holds | InfoLogger at 60 % of the mix | The outage tolerance follows by division | docs/SOAK_RESULTS.md:903-907 | arch |
| 72 seconds | Buffer cover at the reference rate, 12,000 InfoLogger records a second | Round 1's measured 61 seconds | Independent corroboration of round 1 | docs/SOAK_RESULTS.md:910, :914-915 | arch |
| 17.4 hours | Buffer cover at a real worker's 23 a second, 13.8 InfoLogger records a second | 72 seconds at test rate | At what a worker carries, the shipped buffer absorbs most of a day | docs/SOAK_RESULTS.md:911, :917-920 | arch |
| 5.1 hours | Buffer cover at the busiest worker-second, 78 a second, 46.8 InfoLogger | 17.4 hours | Even the worst second seen gives hours of cover | docs/SOAK_RESULTS.md:912 | arch |
| 870× | Test rate over a real worker's rate | Real worker 23 a second | The InfoLogger loss is a property of the test rate, not the product | docs/SOAK_RESULTS.md:917-920 | arch |
| 16.24, +45.0 %, lane server 0.37 per million, 51,511 dropped | B7, lane on with the real lane server | B1 at 11.20 | The lane costs the collector tens of per cent | docs/SOAK_RESULTS.md:941, :946-948 | arch |
| 18.76, +67.5 %, 213,662 dropped | B8, lane output at workers 0 | B7 at +45.0 % | Workers 0 is worse and drops four times as many lane records | docs/SOAK_RESULTS.md:942, :950-952 | arch |
| 17.87, +59.6 %, lane server 2.25 per million, 158,831 dropped | B10, five viewers | B7 | Viewer count moves the lane server, not the collector | docs/SOAK_RESULTS.md:943, :953-959 | arch |
| 15.84, +41.5 %, lane server 9.71 per million, 35,703 dropped | B11, twenty viewers | B10 | About 0.5 core-seconds per million per viewer, on the lane server | docs/SOAK_RESULTS.md:944, :953-959 | arch |
| 41 % to 68 % | The live lane's cost on the collector across lane-on cells | Lane off | The largest single cost any arm adds, and a product decision | docs/SOAK_RESULTS.md:946-948, :1132-1135 | arch |
| 12 % | Spread between B7, B10 and B11, which should agree | Seven times the noise floor | Lane cells are noisy. A firm lane cost needs repeats | docs/SOAK_RESULTS.md:961-968 | code |
| 17.48, 15.34, 15.39, 19.03, mean 16.81, spread 22.0 % | Four runs of lane compression with gzip | zstd | The runs interleave completely | docs/SOAK_RESULTS.md:982 | code |
| 14.97, 19.16, 16.48, 18.80, mean 17.35, spread 24.1 % | Four runs with zstd | gzip | zstd does not reduce the collector's cost. The 14.4 % single-run gap was noise | docs/SOAK_RESULTS.md:983, :985-989 | arch |
| 7.4 / 7.6 / 7.6 MB against 8.3 / 8.4 / 8.3 MB | Bytes on the wire in five minutes, gzip against zstd | Each other | zstd puts about 10 % more bytes on the wire | docs/SOAK_RESULTS.md:994, :997-998 | arch |
| 0.08 against 0.04 core-seconds per million | Lane server decompression cost, gzip against zstd | Each other | Real, and 2 microseconds per record on the lane server, so irrelevant. Keep gzip | docs/SOAK_RESULTS.md:995, :999-1007 | arch |
| −46.53 % and −46.64 % | Gate zero shortfall in the two attempts at lt, the lane on its own tag | Ten other cells at +0.00 % | The arm halves what the collector accepts. Rejected | docs/SOAK_RESULTS.md:1016-1042 | arch |
| 33,000,000 of 33,000,000, 0 lost, empty queue | B5 and B6 at 50,000 a second for ten minutes, t0 and t2 | The plan's claim that 50,000 fails by construction | 50,000 a second is inside the collector's ceiling | docs/SOAK_RESULTS.md:1054-1070 | arch |
| 8.97 and 10.72 core-seconds per million | Collector cost at 50,000 a second, t0 and t2 | 11.20 at 20,000 | t2 charges 19.5 % more per record at 50,000 to empty the main loop | docs/SOAK_RESULTS.md:1063, :1095-1099 | arch |
| 254.4 core-seconds at 86 % and 12.2 at 3 % | Main loop at 50,000 a second, t0 and t2 | Each other | t2 empties the loop and still costs more | docs/SOAK_RESULTS.md:1064 | code |
| 0.352 and 0.512 mean cores | Collector at 50,000 a second, t0 and t2 | Four pinned cores | The ceiling is far enough away that no arm buying headroom pays for itself | docs/SOAK_RESULTS.md:1065, :1095-1099 | arch |
| 198.5 MB and 215.1 MB | Peak memory at 50,000 a second, t0 and t2 | Each other | Memory at the highest rate tested | docs/SOAK_RESULTS.md:1066 | code |
| 19.9 % cheaper | t0 per-record cost at 50,000 against 20,000 | 8.97 against 11.20 | Per-record costs must never be compared across rates | docs/SOAK_RESULTS.md:1105-1109 | arch |
| 8.97, 11.20, 75.32 | The same collector at 50,000, 20,000 and 1,000 a second | Each other | Same work, three per-record costs, because fixed overhead is divided by rate | docs/SOAK_RESULTS.md:1129-1131 | arch |

## 3. Retracted or superseded

Every item below is withdrawn by "Read this first", by a later section in this range, or by the range's own correction notes. The withdrawing line is named.

1. Core isolation is worth 52 %. Withdrawn at docs/SOAK_RESULTS.md:100. Re-measured clean: 2.7 % against a 5.5 % floor, ranges overlap, no effect. [arch]
2. OpenSearch needs 3 of 4 cores, 72 %. Withdrawn at docs/SOAK_RESULTS.md:101. The 2-core arm reads 101.61 and offers perfectly. [arch]
3. Heap 2g wins by 8 to 22 %. Withdrawn at docs/SOAK_RESULTS.md:102. Re-measured 2.1 % and 0.4 % against a 6.7 % floor. [arch]
4. Heap ranking flips with flush. Withdrawn at docs/SOAK_RESULTS.md:103. 3g leads at both flush 0.5 and 5. [arch]
5. t3 is 6.2 % cheaper on four cores. Withdrawn at docs/SOAK_RESULTS.md:104. t3 costs 8.8 % more. Stage B was right. [arch]
6. Flush 0.25 breaks ingestion. Withdrawn at docs/SOAK_RESULTS.md:105. It offers at −0.98 % and costs 6 % more. [arch]
7. A drain ceiling at about 13,800 a second below flush 0.5. Withdrawn at docs/SOAK_RESULTS.md:106. An artefact of a failing machine. [arch]
8. The architectural cap is 120,000 records a second. Withdrawn twice: docs/SOAK_RESULTS.md:107 and docs/SOAK_RESULTS.md:1077-1092. The replacement is about 42,000 a second sustained on this rig, from the addendum at line 39 to 41. [arch]
9. Prediction two "scored right". Withdrawn at docs/SOAK_RESULTS.md:108. Scored on saturated evidence, now unscored. [code]
10. All of the above share one cause: the rig was above its clean capacity, docs/SOAK_RESULTS.md:91-97. Every number from "Stage C" onward was measured on a saturated rig, docs/SOAK_RESULTS.md:13-15. Those sections are outside this range.
11. The plan's label "the real steady rate" for 1,000 a second. Withdrawn at docs/SOAK_RESULTS.md:226-233. It is a safety rate, 43× the archive median.
12. The plan's claim that a per-farm figure makes "the 53× headroom figure evaporate". Withdrawn at docs/SOAK_RESULTS.md:440-444. Headroom grows under the farm reading.
13. An earlier draft's claim that the worker budget compares unfavourably with the laptop because of multithreading. Corrected at docs/SOAK_RESULTS.md:276-281. Four physical cores are eight logical processors, so the laptop comparison is less unfavourable than stated.
14. Stage A's first noise floors, 1.95 % and 28.07 %. Withdrawn at docs/SOAK_RESULTS.md:508-513. Two builds of the instrument, so not a floor. All six cells rerun on one build.
15. Prediction one, that only t2 could move the number. Scored wrong at docs/SOAK_RESULTS.md:797-810. t1 moved it by 11.5 % without touching a filter, and every arm that moved work off the main loop raised the cost.
16. The single-cell reading that zstd is 14.4 % cheaper on the collector. Withdrawn at docs/SOAK_RESULTS.md:985-989. Four runs each interleave completely.
17. An earlier reading that zstd was never applied at all. Withdrawn at docs/SOAK_RESULTS.md:1009-1012. It was applied. The wire-byte figures come from the three repeat runs only.
18. The plan's premise that a 50,000-a-second cell fails the safety gates by construction. Disproved at docs/SOAK_RESULTS.md:1054-1070. Neither cell failed anything.
19. Round 1's ten-million-record loss as a product concern. Retired at docs/SOAK_RESULTS.md:926-928. Real and reproducible, but it needs a sustained rate no worker approaches.
20. The plan's rule that a cell failing gate zero twice means the generator is the problem. Overridden for lt at docs/SOAK_RESULTS.md:1016-1042. The generator was clean and the arm itself caused the shortfall.

## 4. Defects found and fixed

Each line names the component, what was wrong, and where it is described.

1. The collector's OpenSearch output duplicated records. Mechanism proven, fix shipped to the collector template. docs/SOAK_RESULTS.md:168-169. The defect section itself is outside this range. [arch]
2. The rig's heap ceiling. A 2 GB heap in a 2 GB container cannot start. A container-ceiling function fixes it. docs/SOAK_RESULTS.md:170-171. [code]
3. The recorder did not report its own stalls. Cumulative counters folded a missed second into the next delta, so A4 claimed 14.7 core-seconds in one second on four cores. The recorder now reports sampling health, divides the per-second peak by real elapsed time, and voids a cell with more than 2 % late samples. docs/SOAK_RESULTS.md:676-681. [code]
4. A cell whose recorder wrote nothing was reported as a pass. A run with no per-service figures is now void. docs/SOAK_RESULTS.md:682-684. [code]
5. The generator selftest silently dropped the whole InfoLogger family, six tenths of the mix, and reported 400 records a second when asked for 1,000. It now counts what it builds and paces to within 0.02 % at 1,000, 20,000 and 50,000. docs/SOAK_RESULTS.md:686-691. [code]
6. Gate zero proved pacing, not headroom. The selftest now ends with a ceiling probe, 2,842,277 records a second, and a cell must sit at least 20 % under it. docs/SOAK_RESULTS.md:693-697. [code]
7. The first Stage A attempt used two revisions of the recorder across cells. All six cells were rerun on one build. docs/SOAK_RESULTS.md:477-495. [code]
8. The arm t3 produced nothing on its first run because the config generator did not know the arm name. The fix was checked to re-render t0, t1 and t2 byte-identical, so only t3 was rerun. docs/SOAK_RESULTS.md:750-755. [code]
9. The sink did not record the content encoding when the compression cells first ran, which led to a wrong reading. Fixed for the repeat runs. docs/SOAK_RESULTS.md:1009-1012. [code]
10. The first attempt at A4 was voided by a laptop stall, sampling gaps up to 10.5 seconds, with an archive scan running beside it. The rule that follows: the archive scan runs only when the rig is idle. docs/SOAK_RESULTS.md:666-673, :255-259. [code]

## 5. Decisions this range settles

1. Flush moves from 5 to 1 second. Evidence: the collector's own cost falls 24.5 % and peak memory 28.0 %, ranges do not overlap. docs/SOAK_RESULTS.md:19-31. Flush 0.75 and 1 are interchangeable, docs/SOAK_RESULTS.md:85-86. [arch]
2. The heap stays at 1 GB. Evidence: heap size makes no difference to burst absorption, docs/SOAK_RESULTS.md:38-39, and heap 2g re-measures at 2.1 % and 0.4 % against a 6.7 % floor, docs/SOAK_RESULTS.md:102. Negative result: heap tuning can be skipped, docs/SOAK_RESULTS.md:110-111. [arch]
3. Internal core placement between the collector and OpenSearch is not tuned. Evidence: 2.7 % against a 5.5 % floor, docs/SOAK_RESULTS.md:100, :123. Negative result: tuning that can be skipped, docs/SOAK_RESULTS.md:110-111. [arch]
4. External pinning of the stack's four cores away from the worker's other processes is required, with a real reservation and not a share weight. Evidence: every number assumes four exclusive cores and the rig always did it. docs/SOAK_RESULTS.md:48-51, :115-139. [arch]
5. A Kafka bus between the collector and OpenSearch is rejected on this round's numbers. Evidence: 538× the busiest worker-second, buffer covers 17.4 hours. docs/SOAK_RESULTS.md:43-46. The full argument is outside this range at line 1852 onward. This decision is separate from the September 2026 message bus for the live lane only. [arch]
6. 1,000 records a second is a per-worker rate, by decision. Evidence: three reasons at docs/SOAK_RESULTS.md:206-224. [arch]
7. The grid keeps its rates, 1,000, 20,000 and 50,000, unchanged. docs/SOAK_RESULTS.md:237-239, :456-460. [arch]
8. Screening arms run at 20,000 a second only. Evidence: the floor at 1,000 a second is 9.48 %, so no arm in the plan can register there. docs/SOAK_RESULTS.md:531-534. [code]
9. The shipped threading, t0, ships unchanged. Evidence: t3 +7.4 %, t1 +11.5 %, t2 +26.5 %, all outside the 1.61 % floor. docs/SOAK_RESULTS.md:757-773, :1118. Confirmed on the second rig, docs/SOAK_RESULTS.md:104, :172. [arch]
10. The tcp tap, s0, ships unchanged. The spool arm, s1, is not shipped. Evidence: s0 lost 66.7 %, s1 lost 68.7 %. The loss is at the output buffer's 256M cap, not at the input. docs/SOAK_RESULTS.md:829-846, :923-924, :1120. [arch]
11. The pause knob is not turned on. Evidence: it is an input setting and changes nothing, 66.7 % to 66.8 %. docs/SOAK_RESULTS.md:887-893, :925. [arch]
12. If a hard outage guarantee is ever wanted, the lever is the InfoLogger output's buffer size, sized from 310 bytes per record, and nothing else. docs/SOAK_RESULTS.md:929-933, :852-854. [arch]
13. The lane output keeps its default workers. Evidence: workers 0 costs 67.5 % against 45 % and drops four times as many lane records. docs/SOAK_RESULTS.md:950-952, :1122. [code]
14. The live lane keeps gzip. Evidence: zstd is not cheaper on the collector, sends about 10 % more bytes, and its only win is 0.04 core-seconds per million on the lane server. docs/SOAK_RESULTS.md:985-1007. [arch]
15. The lane on its own tag, lt, is rejected. Evidence: the collector accepts half the offered rate. docs/SOAK_RESULTS.md:1016-1042, :1121. [arch]
16. The stage points at moving the live feed off the worker entirely. Evidence: the lane costs the collector 41 to 68 %, and the chunk-retention problem stays unsolved on the worker. Trade: a worker-fed lane works when OpenSearch is red, an aggregator-fed one does not. docs/SOAK_RESULTS.md:1044-1050, :1132-1135. This is the finding the September 2026 live-lane message bus builds on, but the bus itself is not in this range. [arch]
17. This document states no collector ceiling number. Evidence: nothing ran above 50,000 a second and the cost curve bends. docs/SOAK_RESULTS.md:1077-1103, :1119. [arch]
18. Per-record costs are never compared across rates. Evidence: 8.97, 11.20 and 75.32 for the same collector. docs/SOAK_RESULTS.md:1105-1109, :1129-1131. [arch]
19. The archive scan runs only on an idle rig. Evidence: a scan beside a cell voided A4. docs/SOAK_RESULTS.md:255-259. [code]
20. Nothing from Stage B is adopted. docs/SOAK_RESULTS.md:1113-1114. [arch]

## 6. Not measured, not tested, or unmeasured

Verbatim statements, with lines.

1. "Required. Never measured, because the rig always did it" (external pinning). docs/SOAK_RESULTS.md:122.
2. "The size of that error is unmeasured." (sharing the four cores with reconstruction work). docs/SOAK_RESULTS.md:135.
3. "The **full stack** — collector plus the worker's OpenSearch node plus the storage tier — has never been run above 5,000 a second in steady state, nor above a 30,000-a-second burst of 30 seconds". docs/SOAK_RESULTS.md:157-160.
4. "The rate at which internal placement starts to matter is **unmeasured**". docs/SOAK_RESULTS.md:161.
5. "The whole round should be repeated once the host's performance cores return." docs/SOAK_RESULTS.md:199-200.
6. "the derivation covers InfoLogger only. DDS and stdout need the run tarballs read, and the burst shape needs the same." docs/SOAK_RESULTS.md:255-256.
7. "One micro-benchmark on `epn228` would give the conversion factor. It has not been run." docs/SOAK_RESULTS.md:289-290.
8. "Stage E's 1.5-core cell does not exist as written." docs/SOAK_RESULTS.md:301.
9. "These are floors on the true rate, not measurements of it." docs/SOAK_RESULTS.md:345.
10. "It is recorded here and **not used**" (the 187,210-record second). docs/SOAK_RESULTS.md:388-389.
11. "The burst gap is the sharpest open question in this round, sharper than the tap question." docs/SOAK_RESULTS.md:449-450.
12. "does the 1,000 a second cover all three log families or InfoLogger alone?" docs/SOAK_RESULTS.md:451-453.
13. "whether an EPN worker's O2 process files actually carry InfoLogger content ... No amount of soak testing settles it". docs/SOAK_RESULTS.md:637-643.
14. "nothing in this round measures what that back-pressure does to the tcp input". docs/SOAK_RESULTS.md:855-858.
15. "It is a signal, not a measurement, and it wants a steady-state repeat before anyone ships on it" (s1 collector cost). docs/SOAK_RESULTS.md:866-868.
16. "a firm collector-side lane cost needs repeats". docs/SOAK_RESULTS.md:966-967.
17. "Nothing was run above 50,000 a second". docs/SOAK_RESULTS.md:1090.
18. "Finding the real cap needs a rate ramp above 50,000 that runs until something actually breaks. No such cell exists in this plan." docs/SOAK_RESULTS.md:1101-1103.
19. "Prediction two ... Unscored". docs/SOAK_RESULTS.md:108.

## 7. Cross-range notes

1. "Read this first" (lines 11 to 203) retracts every number from "Stage C" onward, docs/SOAK_RESULTS.md:13-15. Stage C starts after line 1138. The reconciler must apply the withdrawn table at lines 98 to 108 to briefs 2 and 3.
2. The 42,000-a-second sustained rate and the 1 GB heap result live in "Round 2 addendum", outside this range. docs/SOAK_RESULTS.md:38-41, :107.
3. The Kafka rejection is argued in "Kafka, decided against on this round's numbers", from line 1852. docs/SOAK_RESULTS.md:43-46. Keep it separate from the September 2026 live-lane bus.
4. The OpenSearch duplication defect and its fix are described in a "defect section below", outside this range. docs/SOAK_RESULTS.md:168-169.
5. Line 175 says the live lane costs "28 % here", meaning the saturated 5,000-a-second rig. The 41 to 68 % comes from Stage B in this range, docs/SOAK_RESULTS.md:946-948. The two rigs must not be averaged.
6. Line 148 mixes rigs in one sentence: 27.8 at 5,000 a second is the saturated rig, 11.20 at 20,000 and 8.97 at 50,000 are the healthy rig. The curve shape holds. The absolute values do not align, docs/SOAK_RESULTS.md:195-197.
7. Two flush 1 totals appear in "Read this first": 104.67 (three alternating runs, line 24), 89.37 (eight-value curve, line 63) and 92.19 (basin, line 83). They are different run sets on the saturated rig. Quote the one that matches the comparison being made.
8. A grep for the flush decision showed the phrase "from 5 to 0.5" at docs/SOAK_RESULTS.md:1237 and :1361. Those lines were not read. They sit in the retracted Stage C to E span. "Read this first" at line 19 chooses 1 and supersedes them.
9. The t3 figure differs by rig: +7.4 % on the healthy rig (line 741) and 8.8 % more on the re-run (line 104). Both say t0 stands.
10. Line 1118 quotes t1 as +11.4 % and t2 as +26.4 %. Lines 740 and 742 quote +11.5 % and +26.5 %. Both pairs are in this range. The table rows at 740 and 742 carry the per-million figures they derive from. Flag for the reconciler.
11. Round 1 facts referenced here and held in docs/SOAK.md: a ceiling of about 53,000 a second measured on two cores (docs/SOAK_RESULTS.md:567, :1073-1074), a 61-second buffer hold (line 914-915), a fifteen-minute outage losing every InfoLogger record (line 628), a ten-million-record loss (line 926), a five-per-cent artefact from starving the sink (lines 132-133, :654-655), and a read-rate limit (lines 1087-1088).
12. The plan is docs/SOAK_PLAN.md, docs/SOAK_RESULTS.md:3. The archive scanner is tools/soak/archive_rate.py, line 316.
13. Stage B hands three things to Stage C at lines 1124 to 1135. Stage C is in the retracted span.
14. The 2025 ALICE anomaly detection paper cross-check at lines 415 to 430 depends on docs/ML_AI.md treating that paper as required reading.

## 8. Story

- A worker writes tens of records a second, not thousands. In the busiest hour of six months the median worker carried 23 records a second and the busiest single second was 78 (lines 364-368). The plan's 1,000 was a farm figure read as a worker figure (line 336-337).
- The soak keeps its rates anyway, so it loads one collector at 256 times the busiest worker-second ever seen (line 234). A configuration that fails at 20,000 a second has not failed at anything a worker will meet (lines 434-439).
- Before any arm ran, the instrument itself failed four times and was fixed each time (lines 664-697). The noise floor at 20,000 a second is 1.61 %, and at 1,000 a second it is 9.48 %, because at the low rate the collector measures its own idling (lines 505-530).
- The collector is one single-threaded event loop doing 89.3 % of the work, and it uses 0.25 of one core at 20,000 a second (lines 544, 559-560). Every threading arm made the collector dearer, t3 by 7.4 %, t1 by 11.5 %, t2 by 26.5 % (lines 739-742). The shipped configuration won.
- The InfoLogger loss under a fifteen-minute outage, 66.7 % of the stream, is a property of the test rate. The 256 MB output buffer holds 865,674 records, which is 72 seconds at test rate and 17.4 hours at a real worker's rate (lines 901-912). A file tap and a pause knob changed nothing (lines 829-832, 887).
- The live lane is the largest single cost found: 41 to 68 % more collector processor time (lines 946-948). Sending the lane from its own tag halved what the collector accepts (line 1016-1042). The finding points at moving the live feed off the worker (lines 1048-1050).
- At 50,000 a second the collector took all 33,000,000 records, lost none, and used 0.352 of one core (lines 1060-1065). No ceiling number is stated, because nothing ran above 50,000 (line 1090).
- The re-run of 27 August 2026 withdrew most of what followed Stage B and left one knob: flush from 5 to 1 second, which cuts the collector's own cost by 24.5 % and its memory by 28.0 % (lines 19-28). Heap size and internal core placement make no measurable difference and can be skipped (lines 110-111).
