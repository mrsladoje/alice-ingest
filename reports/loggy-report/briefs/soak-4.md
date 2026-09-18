# Soak brief 4 of 5: docs/SOAK_RESULTS.md lines 2999 to 4283

Source file: docs/SOAK_RESULTS.md. All line numbers below refer to it.
Every number is copied exactly. A = architecture-level fact, belongs in the report. C = code-level fact, evidence only.

## 1. Coverage

This range holds Rounds 7, 8 and 9, all written after the 27 August retractions, so they stand unless a later round corrects them. Round 7 (lines 2999 to 3501, written 4 September 2026) adds three log sources the collector never read, splits the process-tree family into two formats, prices every collector regex, and freezes a templating recipe per format family, all on the archive corpus and none on the farm (3001, 3005 to 3007). Round 8 (3505 to 3887, written 5 September 2026) is the read-only census of the four allocated EPN machines: it finds a fourth collector version, corrects four Round 7 facts, measures the system journal with the right permissions, indexes real records through the mappings, builds and proves the template catalog, measures the last two recipes on farm data, runs every source through real Fluent Bit, and closes the replay-clock question (3507 to 3509). Round 9 (3890 to 4282) is an external review with eight claims: five hold, and three of those were worse than the review said (3892 to 3893). It retracts every regex cost figure from Rounds 6 to 8 as wall clock, fixes four catalog defects, re-reads the run orchestrator as a real source, adds a rotation check, and replaces synthetic InfoLogger records with real dump rows.

## 2. Valid numbers

Regex-cost numbers from `regexbench.py` in Rounds 7 and 8 are wall clock and are withdrawn at 4192 to 4197. They are not in this table. Templating cost numbers from `drainbench.py` are processor time by construction and stand (4199 to 4200).

| number (exact) | what it measures | baseline or reference | what it means for the design | source line | level |
|---|---|---|---|---|---|
| 45,596,613 lines | archive corpus every Round 7 figure was measured on | none, the corpus size | Round 7 rests on archive data only, not on the farm | 3005 | C |
| 41.8 % | share of the corpus that reached durable storage with no `severity` key before Round 7 | the low-volume sizing in the deploy README | severity routing was silently off for the largest family, the fix is a defect fix, not tuning | 3038 to 3039 | A |
| 20,948,861 lines | process-tree lines scored by the Python coverage tool | none | the sample behind the 99.83 % figure | 3076 | C |
| 99.83 % | process-tree lines from which a severity was recovered, continuations folded first | Stage S0 gate of 99 % | the gate passes | 3085, 3087 to 3088 | A |
| 98.22 % | the same corpus scored line by line, unfolded | 99.83 % folded | folding must happen before scoring or the figure reads 1.6 points low | 3092 | C |
| 0.17 % | process-tree lines with no severity to recover | the frozen 1 % catch-all threshold set in Round 8 | the residual is a module banner and parameter dumps, named and accepted | 3088, 3795 to 3796 | A |
| 99.97 % | DDS lines with a severity recovered, 43,972 lines | 99 % gate | passes, the twelve misses are the startup banner | 3097 | A |
| 96.94 % / 3.06 % | process-tree share that stays on the worker / crosses the network under severity routing | 0 % / 100 % before Round 7 | routing on severity is what makes informational volume node-local | 3115 to 3116 | A |
| 0.39 % | `STATE` share of the process tree, routed durable on purpose | none | a FairMQ transition is the join between launch and RUNNING, an operator asks for it first | 3118 to 3120 | A |
| 86.5 % / 13.5 % | DDS `inf` / `err` share in the corpus | none | the error share is high and rests on a corpus that under-samples DDS, the largest unpriced routing risk | 3122 to 3124 | A |
| 11.13 % | DDS messages that gain a field from one of the three extractors, over 1,236,608 messages | none | the extractors earn a field on about one line in nine | 3222 to 3223 | A |
| 3,487 bytes average, 14,436 longest, 160 MB across the corpus | size of an executed-task command line in DDS | none | capturing it twice doubles the record, so `task` captures the first token only | 3242 to 3243, 4182 to 4186 | A |
| 3.42 % each | share of real DDS records that carry `slot_id`, `channel_id`, `channel`, `task` | none | the extractor share on real traffic, lower than the 11.13 % corpus figure | 3771 | C |
| 46 | distinct programs DDS resolved from real traffic | none | the third DDS column works as program identity | 3779 | A |
| 92,813 lines | DataDistribution lines that Round 6 mined under the wrong recipe | none | the family was hidden inside the process tree until the split | 3266 to 3267, 3280 | C |
| 33,433,018 / 21,200,248 / 1,236,971 / 92,813 | mining-family split of all three corpora: `infologger` / `dpl` / `dds` / `datadist` | none | the sizes each recipe was frozen on | 3277 to 3280 | C |
| 5.37 core-s/M, 191 templates, 60.3 % words kept | frozen `datadist` recipe: no padding, depth 8, similarity 0.4, numeric parametrised | 9.01 core-s/M for `=` padding, 58.9 % words kept | not padding is 40 % cheaper and more readable on this family | 3289, 3303 to 3305, 3312 to 3314 | A |
| 5.21 core-s/M, 920 templates, 1.1 % contentless | frozen `dpl` recipe: no padding, depth 8, similarity 0.4, numeric parametrised | 8.19 core-s/M and 809 templates for Round 6's `= ;` setting | not padding is 36 % cheaper for 111 more templates, which Round 6 showed are affordable | 3324, 3343 to 3348, 3350 to 3352 | A |
| 99.8 % | `dpl` words kept in every one of 32 cells | none | the readability metric is saturated on this family once the clock is stripped | 3318 to 3320, 3340 | C |
| 21.00 core-s/M, 1,570 templates, 87.9 % words kept | frozen `dds` recipe: pad `=`, depth 8, similarity 0.5, numeric kept, re-run on 1,236,971 lines | 88.1 % words kept in Round 6 on a corpus 28 times smaller | Round 6's choice holds on every knob | 3367, 3371, 3379 to 3382 | A |
| 72 % | extra cost of padding `=` over nothing on `dds`, 21.00 against 12.18 core-s/M | buys 11.6 points of words kept, 87.9 % against 76.3 % | the readability metric can see a real trade, so the `dpl` result is trustworthy | 3386 to 3389 | A |
| 21.00 against 5.21 and 5.37 core-s/M | DDS cost per line against `dpl` and `datadist` | none | DDS is the most expensive family and the one the corpus least represents | 3391 to 3394 | A |
| 8.85 core-s/M, 94.5 % words kept | `infologger` recipe, Round 6's figure, not re-run | none | the one row not re-measured against the split families or the current masker | 3400, 4277 to 4280 | A |
| 9 met / 6 not met / 1 with caveat | Stage S0 gate count after Round 7, of sixteen conditions | sixteen conditions | Round 7's state, superseded by Round 8's count | 3422 to 3423, 3790 to 3792 | C |
| 13 met / 3 not met / 0 caveat | Stage S0 gate count after Round 8 | Round 7's 9 / 6 / 1 | five conditions closed in one census, two remain: source-owner approval and the live job-log path | 3790 to 3792, 3801 to 3805 | A |
| 4 | collector versions deployed on the farm: 4.0.1 on epn146 and epn228, 4.0.14 on epn323, 3.2.8 on epn-infra13 | 2 versions assumed before the census | the storage machine runs a version two majors behind that nothing had tested | 3519 to 3527 | A |
| 29 passed, 75,243 journal records, no duplicates | fixture, journal and restart checks on each of 3.2.8, 4.0.1, 4.0.14, 5.0.8 (Round 8) | none | the configuration is good on every version the farm runs | 3532 to 3540 | C |
| 35 of 35 | fixture expectations on each of the four versions (Round 9, after the orchestrator and rotation checks were added) | 29 in Round 8 | supersedes the Round 8 count | 4206 to 4209 | C |
| 162,642 lines | the InfoLogger daemon log on epn146, 10 March to 1 September | "a handful of lines a day" | about 930 a day, with no logrotate entry, growing without bound | 3551 to 3552, 3565 to 3567 | A |
| 36 %, 58,175 lines | share of the daemon log the client-count extractor missed | none | the extractor anchored on one of two shapes, now anchors on the trailing count | 3554 to 3563 | C |
| 1025 of 2048 | peak clients seen on the InfoLogger daemon | ceiling of 2048 | the saturation signal is real and has headroom | 3568 | A |
| 99.99 % | share of real daemon-log records that carry `clients` and `client_limit` after the fix | 64 % for the extractor it replaced | the fix measured on the file it was wrong about | 3774 to 3777 | A |
| 71,964 / 1,992 / 986 / 797 MB | epn146 journal over 30 days, read with sudo: info, warning, kernel, on disk | first pass as ordinary user saw 1,000 to 8,000 entries and no kernel entries | the journal must be read as root or it is nearly empty | 3576 to 3582 | A |
| 76,734 / 972 / 15 / 1.4 GB | epn228 journal over 30 days | same | worker journal volume | 3583 | A |
| 26,765 / 284,552 / 17 / 411 MB | epn323 journal over 30 days | same | 9,485 warnings a day on the newest worker, all durable under priority routing | 3584, 3595 to 3598 | A |
| 12,957,663 / 15,615 / 306 / 3.9 GB | epn-infra13 journal over 30 days | same | nearly all slurmctld, which a unit allow-list would have dropped | 3585, 3592 to 3593 | A |
| 2,500 to 10,500 entries a day | what a worker writes to the journal | millions of process-tree lines a day | collecting all system logs is free, so the unit allow-list is gone | 3587 to 3589 | A |
| 172,935 entries | NetworkManager entries on epn323 the allow-list would have dropped | none | one of two blind spots the allow-list would have created | 3592 to 3593 | A |
| 75,243 / 74,023 / 1,220 | epn146 journal through the shipped configuration: records, node-local (priority 5 to 7), durable (priority 4 and below) | none | priority routing sends 1.6 % of journal entries across the network | 3609 to 3613 | A |
| 234 to 16 | distinct journal fields per record before and after the allowlist | none | the application mapping is `dynamic: false`, so every unnamed field is stored and never searchable | 3614 to 3615, 3629 to 3631 | A |
| 31, none rejected | real collector records indexed through the index templates, including the strict InfoLogger index (Round 8) | none | the mappings were exercised, not read | 3648 | C |
| 43 real records, 0 rejected | the same check in Round 9, every emitted field mapped and searchable | 31 in Round 8 | supersedes the Round 8 count | 4213 | C |
| 11.5 MB a day, 9.1 GB retained | the run orchestrator log on epn-infra13 | none | the only O2 process writing a log on the farm during the census | 3682 to 3684 | A |
| 44,444 lines, 1 severity, 2 templates | the orchestrator on one idle day (Round 8) | Round 9's 25-file sample | superseded: this was a statement about the day sampled, not the source | 3689 to 3690, 4021 to 4037 | C |
| 2,612,951 lines, 5 severities: 1,650,555 `inf`, 961,933 `dbg`, 312 `wrn`, 153 `err`, 8 `fat` | the orchestrator over 25 of 560 retained files (Round 9) | Round 8's one idle day | the orchestrator carries real orchestration failures during a run | 4022 to 4032 | A |
| 981,228 | sampled orchestrator lines that carry both partition and run | none | the join key to InfoLogger, which carries the same pair | 4046 to 4048 | A |
| 190,250 lines, no false positive | the short partition pattern `[A-Za-z0-9]+` matched on a busy and an idle day | a six-character minimum, which would lose `test0` and `idd` | the short form stays | 4050 to 4055 | C |
| 100.00 % / 100.00 % / 0.00 % | orchestrator lines matched, severity recovered, unclassified, 190,052 records | 1.00 % frozen threshold | the parser covers the source | 4064 to 4066 | A |
| 190,014 (99.98 %) / 38 (0.02 %) | orchestrator records node-local / durable | none | almost nothing from the orchestrator crosses the network | 4067 to 4068 | A |
| 24.36 cpu-s/M, 266 templates, 98.7 % words kept, 1.9 % contentless | frozen `odc` recipe: `= ; :`, similarity 0.40, numeric parametrised | 6.27 cpu-s/M and 94.5 % words kept unpadded | 3.9 times the cost, accepted, because the source writes about 100,000 lines a day on one machine, roughly 2.4 processor-seconds a day | 4080, 4083 to 4093 | A |
| 266 against 397 | `odc` templates with numeric parametrised against kept | none | run number and partition are already fields, so keeping numbers only splits a failure shape across runs | 4095 to 4098 | A |
| 108,757 of 275,205 | rows under `system: ODC` in one 2024 InfoLogger partition | none | the orchestrator already reaches OpenSearch by another route for `I`, `E`, `F` | 4102 to 4104 | A |
| 36.7 % | `dbg` share of the orchestrator file | none | the debug tier is not forwarded to InfoLogger, so the file holds what InfoLogger lacks | 4107 to 4109 | A |
| 509 with a run, 7,751 with a partition, of 108,757 | run attribution in InfoLogger's ODC rows | 46 % of a busy day's file lines carry both | the file attributes runs far better than the forwarded rows | 4110 to 4112 | A |
| 38 of 190,052 | orchestrator lines that exist in both places and reach the durable tier | none | the duplication is nearly free because `inf` stays node-local | 4114 to 4116 | A |
| 3.44 core-s/M, 18 templates, 100 % words kept | frozen `ildaemon` recipe, no padding, 163,670 farm lines | 5.54 core-s/M for `= ; :` | the file has two shapes, only cost moves | 3710 to 3713 | A |
| 7.81 core-s/M, 1,362 templates, 96.8 % words kept, 0.7 % contentless | frozen `journald` recipe: pad `=`, numeric kept, 277,541 farm entries | Round 7's guess `= ; :` parametrised: 10.67 core-s/M, 86.6 %, 2.0 % | the measured recipe beats the guess on every axis | 3715 to 3726 | A |
| 199,377 / 99.81 % / 0 / 198,368 local, 1,009 durable | `dpl` real lines through real Fluent Bit: records, severity recovered, unclaimed, routing | Python scoring, 0.31 points apart | the Round 7 coverage figures stand on a check rather than an assumption | 3757, 3762 to 3764 | A |
| 92,811 / 100.00 % / 0 / all local | `datadist` real lines through real Fluent Bit | Python scoring, 0.00 points apart | same | 3758, 3762 to 3763 | A |
| 199,939 / 100.00 % / 0 / 173,246 local, 26,693 durable | `dds` real lines through real Fluent Bit | Python scoring, 0.03 points apart | same, and 13.3 % of DDS goes durable on real traffic | 3759, 3762 to 3763 | A |
| 50,000 / 100.00 % / 0 | `infologger` real records over the socket, Round 8 | none, parsed as JSON | closes the hole in the Round 7 table | 3871 | C |
| 60,000 of 60,000, 100.00 %, 39,762 `I`, 16,723 `E`, 3,515 `W` | InfoLogger from a real archive dump, all sixteen columns (Round 9) | synthetic records that cycled four severities at 25 % each | the archive is 66 % `I` and 28 % `E`, with no `D` | 4243 to 4257 | A |
| 31,277 (52.13 %) / 31,264 (52.11 %) | real InfoLogger rows with partition / run present | none | half of InfoLogger carries the join key | 4252 to 4253 | A |
| 189,802 records, 198 continuations folded, 70,552 (37.17 %) with partition and run | orchestrator real traffic through real Fluent Bit (Round 9) | Python 99.90 % against Onigmo 100.00 % | the engines agree | 4234 to 4241 | A |
| 11.15 s wall, 2.97 s processor | one regex benchmark run under real processor accounting | none | wall clock was mostly the container waiting, so every earlier control figure was false precision | 3903 to 3909 | C |
| about 20 % | the smallest cost difference this host can resolve between two identical collector configurations | control arm at −9.2 % on one corpus and +19.3 % on another | no collector regex change is worth making on cost grounds, because none can be measured | 3951 to 3960 | A |
| 8 to 23 | template catalog unit tests before and after Round 9, five fail against the old code | none | the four catalog defects are guarded | 4013, 4215 | C |
| 17 to 20 | replay unit tests before and after Round 9 | none | evidence | 4216 | C |
| 12 playbooks | Ansible syntax check, all pass | none | evidence | 4217 | C |
| 17 MB | bundle captured from epn146: 162,642 daemon lines and an 8 MB journal | none | the two farm-only sources can now replay on a staging machine | 3829 to 3831 | A |
| 20 lines a second | pace at which the replayed daemon log is written | none | a tail input needs a file that grows | 3823 to 3824 | C |
| 2026-06-20, 2024 to 2026, 2026-08-06 to 2026-09-05 | event-time windows of the archive process tree and DDS, InfoLogger, and a captured journal | about two and a half months apart | no correlation window of hours spans replay and journal under `preserved` | 3841 to 3844 | A |

## 3. Retracted or superseded

Nothing in this range is withdrawn by "Read this first" (lines 11 to 203). That section retracts Stage C onward of Round 2, which is outside these lines. The withdrawals below come from later sections inside this range.

| claim as first written | line | withdrawn or superseded by | line |
|---|---|---|---|
| A 17.3 % cost for `mft_decoder_error` | 3185 | Round 7 itself: block-ordered runs on a two-speed host, interleaved it reads −0.2 % | 3185 to 3189 |
| A −33 % arm for deleting the leading date from the `dpl` regex | 3191 | Round 7 itself: it measured a broken parse | 3191 to 3194 |
| "4.0.1 loses InfoLogger and 4.0.14 does not" | 3199 to 3200 | Round 7 itself: a race in the fixture harness | 3196 to 3202 |
| The cascade-order arm | 3153 | Round 7 itself: it ran without a router | 3204 to 3208 |
| Regex control gap of +0.8 %, minimum of 8 as the estimator, "no arm differs by more than reproducibility" (400,000-line table) | 3146 to 3177 | Round 9: wall clock, not processor time, and the run stopped at the halfway point. "Round 8's 0.8 % control was false precision, not precision" | 3900 to 3916, 4192 to 4197 |
| `task` capturing the whole line costs +20.8 %, "the only configuration difference larger than the instrument" | 3233, 3235 to 3238 | Round 9: re-measured on processor time, +6.5 % against a +2.5 % control, "not a result". Narrow capture stays on a document-size argument | 4157 to 4188 |
| Every processor-cost figure from `regexbench.py` in Rounds 6 to 8 | 3128 to 3238 | Round 9: all wall clock. "No measurable difference" conclusions hold more strongly. Claimed differences are retracted | 4192 to 4197 |
| The InfoLogger daemon log is tab-separated | 3020 (Round 7 table), and `docs/LOG_TYPES.md` | Round 8: spaces, the parser matched none of 162,642 lines | 3549 to 3552 |
| The daemon log is "a handful of lines a day" | Round 7, via `docs/LOG_TYPES.md` | Round 8: about 930 a day | 3565 to 3567 |
| `ildaemon` and `journald` recipes carry the shipped default, `= ; :`, as guesses | 3404 to 3410, 3492 to 3494 | Round 8: both measured on farm corpora, `ildaemon` no padding, `journald` `=` with numeric kept | 3705 to 3739 |
| Stage S0: 9 met, 6 not met, 1 with a caveat | 3422 to 3423 | Round 8: 13 met, 3 not met, 0 caveat | 3788 to 3792 |
| The mappings caveat: templates checked as JSON, no document indexed | 3440 to 3445 | Round 8: 31 records indexed, none rejected | 3633 to 3652 |
| Round 7 coverage figures measured on Python's `re`, which Fluent Bit does not use | 3076 to 3098 | Round 8: confirmed through real Fluent Bit, engines within 0.31 points | 3745 to 3764 |
| Two Fluent Bit versions deployed | implied at 3055 to 3059 | Round 8: four, the storage machine runs 3.2.8 | 3517 to 3527 |
| The template catalog "built and proved": one route, `hash()` avoided, watermark resumes | 3654 to 3678 | Round 9: four defects, "shipped none of its guarantees" | 3962 to 4013 |
| The orchestrator is 100 % informational, two shapes, a heartbeat | 3688 to 3690 | Round 9: five severities across 25 files, real failures on 20 January 2026 | 4021 to 4037 |
| 29 fixture expectations per version | 3537 to 3540 | Round 9: 35 of 35 per version | 4206 to 4209 |
| 31 records indexed | 3648 | Round 9: 43 records, 0 rejected | 4213 |
| The replay clock question is open | 3469 to 3474 | Round 8: decided, left open on purpose, all three closures are worse | 3839 to 3861 |
| Journald filter order, `Comm:` extracted | Round 7 shipped configuration | Round 8: parser ran before the fold, never extracted `Comm:` | 3570 to 3572 |
| Round 9 attributes the +20.8 % and the regex control to Round 8 | 3956, 4159 to 4161, 4196 | Both figures are in Round 7 at 3144 to 3160 and 3226 to 3238. The retraction stands, the round label is wrong | see section 7 |

## 4. Defects found and fixed

Each line: component, what was wrong, where described. All are code-level evidence. Together they are the report's argument for why the soak existed: none of them announced itself (3029 to 3030).

1. Collector severity parser: the O2 `[HH:MM:SS][SEVERITY]` parser was anchored on `[`, but replay prepends a date, so 41.8 % of the corpus reached durable storage with no severity. 3032 to 3039.
2. Collector DDS router: `rewrite_tag` rules keyed on `$severity` matched nothing when no parser claimed the line, so DDS startup lines vanished. Each router now ends with a rule keyed on `log`. 3041 to 3047.
3. Soak configuration renderer: it passed renamed variables to the template, so every rendered configuration was an exception and nothing had been validated since the rename. 3049 to 3053.
4. Collector `systemd` input: Fluent Bit 5.0.8 rejects `multiline.parser` on that input, 4.0.1 and 4.0.14 accept it. The fold moved to a `multiline` filter all versions accept. 3055 to 3059.
5. Shifter view severity map: a second copy of the severity table went stale, so `INFO`, `WARN`, `STATE`, `ALARM` would show as `unknown` in the live view. The two copies are now one table. 3061 to 3067.
6. DataDistribution templating recipe: the `stdout` strip did not match a DataDistribution bracket, so 92,813 lines mined the clock into templates. A family split fixes it. 3264 to 3273.
7. Regex benchmark, four faults in one day: block-ordered arms on a two-speed host, a broken-parse arm, a TCP race in the fixture harness, and a YAML round-trip that deleted routing rules. 3179 to 3212.
8. InfoLogger daemon parser: required a tab, the file uses spaces, matched none of 162,642 lines. 3549 to 3552.
9. Daemon client-count extractor: anchored on the word `client`, missed 36 % of the file, 58,175 lines. Now anchors on the trailing count. 3554 to 3563.
10. Journal filter order: the parser ran before the multiline fold, so `Comm:` was never extracted from a reconstructed fault. 3570 to 3572.
11. Journal unit allow-list: it would have dropped NetworkManager on epn323 (172,935 entries) and slurmctld on epn-infra13 (nearly all of 13 million). Removed. 3589 to 3593.
12. OpenSearch bootstrap script: a heredoc lost its closing quote when the catalog template was added, and the JSON validator skipped the malformed block. It now counts openers against blocks and runs `bash -n`. 3637 to 3642.
13. Template catalog document identifier: designed to use sha1 of family and template, not Python's randomised `hash()`, which would have grown a new document every ten minutes. 3674 to 3677.
14. Replay shifted-clock wrapper: it monkeypatched a function whose return type had changed, so shifted mode would have failed on the first process log. Fixed at three offsets. 3833 to 3838.
15. Collector journal enable flag: it defaulted to `false` and the probe computed `false and <probe>`, so the journal could never have been enabled. Default is now `true`, the probe can only turn it off. 3881 to 3887.
16. Regex benchmark, measured quantity: wall clock labelled as processor time, and the run stopped at the halfway point because a `rewrite_tag` emitter counts as an input. Now cgroup processor time, a 2-second settle, and a record-start target. 3898 to 3928.
17. Template catalog, family label: the split tested for a prefix the collector had already eaten, so every process-tree template was filed as `dpl` and the same template landed under two documents. It now reads the emitted fields. 3967 to 3983.
18. Template catalog, routes: it read only the local index, so it held no InfoLogger, daemon, warning or error templates. It now reads all three routes scoped by `node`. 3985 to 3991.
19. Template catalog, resume: the watermark stored only `collector_time`, so every record in the last millisecond of a pass was dropped for good. It now carries the document identifier and resumes with `search_after`. 3993 to 3999.
20. Template catalog, miner: it built its own miner and left out the FLOAT/NUM merge, which Round 6 measured as 6.3 % against 1.0 % of lines on a one-wildcard template. It now calls the shared recipe miner, and the tree persists between runs. 4001 to 4011.
21. Orchestrator classification: one idle day was read as a heartbeat. 25 files show five severities and real STOP-transition failures. 4021 to 4037.
22. Log rotation: nothing had tested it. A running collector now sees a line before rotation, an append to the renamed file, and a new file, each exactly once. 4125 to 4139.
23. InfoLogger real-traffic check: it fed invented records with cycled severities and no NULL or embedded quote. It now reads a real dump with replay's own reader. 4141 to 4155.

## 5. Decisions this range settles

| decision | evidence | line | level |
|---|---|---|---|
| Six sources, each with a tier rule: `infologger` and `ildaemon` all durable, `dds`, `dpl`, `datadist`, `journald` informational local and warning-or-worse durable | the routing table | 3014 to 3021 | A |
| The routable measure is severity recovered, not match rate, because the last parser matches anything | 99.83 % against a 99 % gate | 3071 to 3088 | A |
| `STATE` is durable on purpose | 0.39 % of the tree, the join between launch and RUNNING | 3118 to 3120 | A |
| Negative result: no collector regex change is worth making on cost grounds. ANSI tolerance, cascade order, the numeric extractor, the DDS extractors, the partition length all sit inside the instrument's noise | control arm swings from −9.2 % to +19.3 %, resolution about a fifth | 3951 to 3960, 4050 to 4055 | A |
| The minimum over interleaved rounds is the estimator, not the median | median spread 19 %, minimum spread 1 % | 3168 to 3177 | C |
| Round 6's masker rule (literal first) does not transfer to the collector: Onigmo is not Python's `re` | every collector regex measured, not reasoned | 3132 to 3137 | C |
| `dds_task` captures the first token, `\S+`, not `.*` | a 3,487-byte average command line written twice per record, keyword subfield stops at 1,024 bytes. Not on cost grounds | 3247 to 3250, 4182 to 4188 | A |
| Check a new extractor for its payload before its pattern on a high-volume family | the same evidence | 3252 to 3257 | A |
| One recipe per format family, seven families, all measured, depth 8 for all because depth is a module global | the seven-family table | 4259 to 4275 | A |
| `dpl` and `datadist` pad nothing, reversing Round 6's `= ;` on `dpl` | 36 % and 40 % cheaper, and the Round 6 pad comparison ran without the clock strip | 3301 to 3314, 3333 to 3352 | A |
| `dds` keeps Round 6's recipe on 28 times the data | 87.9 % against 88.1 % words kept | 3379 to 3382 | A |
| `odc` pads `= ; :` and accepts 3.9 times the cost, because the source is one machine at about 100,000 lines a day | 98.7 % against 94.5 % words kept, roughly 2.4 processor-seconds a day | 4083 to 4093 | A |
| Collect all system logs, no unit allow-list | 2,500 to 10,500 entries a day per worker, two blind spots avoided | 3587 to 3593 | A |
| The journal must be read with root permissions | ordinary-user read showed 1,000 to 8,000 entries and no kernel entries | 3576 to 3578 | A |
| Journal fields trimmed from 234 to 16 because the mapping is `dynamic: false` | unnamed fields are stored and never searchable | 3614 to 3615, 3629 to 3631 | A |
| The template catalog reads a worker's own local index, mines with the shared recipe, ships one document per template. No raw informational line crosses the network | 96.94 % of the tree is local, so a durable-only catalog would see 3.06 % | 3656 to 3663 | A |
| The catalog reads all three routes, scoped by `node`, and resumes on time plus document identifier | the Round 9 defects | 3985 to 3999 | A |
| The catalog document identifier is sha1 of family and template text, and the watermark lives in the catalog | a wipe forces a clean rebuild | 3674 to 3678 | A |
| The replay clock stays open on a staging machine. The correlation detector must be developed on a real worker | all three closures are worse, the shifted clock collapses a lag feature four detectors train on | 3839 to 3861 | A |
| The catch-all threshold is frozen at 1 % and enforced | measured 0.17 % | 3795 to 3796 | A |
| The orchestrator log is a real source with parser, routing, recipe and replay shipped, inert on every worker. Collecting it is a deployment decision about the storage machine | it runs no collector and runs the version two majors behind | 4118 to 4123, 4219 to 4223 | A |
| Collect the orchestrator file even though it forwards to InfoLogger | the debug tier is not forwarded, run attribution is 509 of 108,757 in InfoLogger against 46 % in the file, overlap on the durable tier is 38 of 190,052 | 4100 to 4116 | A |
| The per-detector CTF size report gets no collector parser. It belongs in the ingest pipeline | Onigmo cannot return a repeated capture, no per-record Lua by design | 3487 to 3490 | A |
| The daemon log is replayed by writing at 20 lines a second, the journal by being read in place | a tail needs a growing file, libsystemd reads the binary | 3822 to 3827 | C |
| The journal enable flag states intent and the probe can only turn it off | the flag could never have been enabled | 3881 to 3887 | C |

## 6. Not measured, not tested, or unmeasured

Verbatim, with lines.

- "Nothing here was measured on the EPN farm. **The live source census did not run — the control machine had no Kerberos ticket, so no EPN was reachable this session**" (Round 7). 3006 to 3008.
- "**That error share is high and it rests on a corpus that under-samples DDS**, which operations report is the highest-volume family during data-taking. It is the largest unpriced risk in this routing." 3122 to 3124.
- "`ildaemon` and `journald` carry the shipped default and **have never been measured**, because no corpus of either exists off the farm." 3407 to 3409. Closed in Round 8 at 3705 to 3739.
- "The `infologger` row is round 6's, unchanged and not re-run." 3407. And: "it has not been re-measured against the split families or with the current masker, and it is the only row in the table that can be said of." 4277 to 4280. Still open.
- "no document has been indexed through them. `replaycheck.py` has no OpenSearch in the loop." 3443 to 3444. Closed in Round 8 at 3633 to 3652.
- "**The live job-log path is still unknown.**" 3455. Still open at 3804 to 3805: "Unanswerable while no run is active; the instrument now exists."
- "**No source-owner approval of the registry.** That is a conversation, not a measurement." 3453 to 3454. Still open at 3803.
- "**The journal has no representative replay data.**" 3458. Closed in Round 8 at 3809 to 3831.
- "**No frozen catch-all threshold.**" 3464. Closed at 3795 to 3796.
- "**No frozen corpus manifest.**" 3466. Closed at 3796 to 3798.
- "The **template catalog has no route at all.**" 3478. Built in Round 8, then found defective and fixed in Round 9.
- "The **cockpit does not show `program` yet.**" 3496. Not closed in this range.
- "`infologger` is not in this table. It arrives over TCP rather than from a file, so the file-based layout does not reach it." 3782 to 3784. Closed at 3865 to 3871.
- "**every coverage figure in round 7 was measured on an engine Fluent Bit does not use.**" 3747 to 3748. Closed at 3762 to 3764.
- "**a cross-source correlation detector cannot be developed against replay alone.** It has to be developed against a real node." 3860 to 3861. A standing limit.
- "It runs no collector today" (the storage machine). 3525 to 3526. Standing, the orchestrator blocker at 4118 to 4123.
- "**This host cannot resolve a difference below roughly 20 %.**" 3951. A standing limit on every collector cost figure.
- "The medians show why this corpus is hard to measure at all: the ten rounds run from 7.4 to 22.8 processor-seconds, a factor of three, drifting upward through the run." 4177 to 4179.
- Nothing in this range was measured on farm hardware for cost. The census at 3507 was read-only. The standing warning at 263 to 306 applies to every cost figure here.

## 7. Cross-range notes

- Round 9 misattributes two Round 7 results to Round 8. Line 3956 says "The conclusion round 8 drew", line 3909 says "Round 8's 0.8 % control", lines 4159 to 4161 say "Round 8 found a single arm that beat the control", and line 4196 says "round 8's 20.8 %". The 0.8 % control is at 3151 and the +20.8 % at 3233, both in Round 7. Line 4175 does say "the phantom 17.3 % in round 7" correctly. The retractions stand. A reconciler should cite them as Round 7 results retracted by Round 9.
- Round 7 at 3132 to 3137 and 3252 to 3257 corrects the reach of Round 6's masker finding (Round 6 is outside this range, from line 1983). The Round 6 result stands for Python's `re`. It does not transfer to the collector.
- Round 7 at 3333 to 3340 corrects Round 6's pad comparison for the `dpl` family: 88.3 % against 87.7 % words kept were measured without the clock strip. Round 6's own recipe table read 99.7 % on the same family. The Round 6 `= ;` choice for `dpl` is reversed at 3350 to 3352.
- Round 7 at 3261 to 3262 and 3412 to 3414 says the `stdout` family Round 6 froze no longer exists as a mining unit. The entry stays in code only for reproduction.
- Round 7 at 3364 to 3367 re-runs Round 6's DDS recipe on 1,236,971 lines against Round 6's 43,972. Round 6's choice holds (3379).
- Round 9 at 4004 to 4006 depends on a Round 6 measurement: the FLOAT/NUM merge is the difference between 6.3 % and 1.0 % of lines landing on a one-wildcard template.
- Round 9 at 4192 to 4197 retracts every `regexbench.py` processor-cost figure in Rounds 6 to 8. Round 6 is outside this range. Any Round 6 regex cost figure from that tool is wall clock. Offline `drainbench.py` templating figures in Round 6 are unaffected (4199 to 4200).
- Round 7 at 3038 to 3039 names the low-volume sizing in `deploy/README.md`. Round 7 at 3025 and Round 8 at 3693 name `docs/LOG_TYPES.md` as the routing record. Round 7 at 3422 names `docs/SEMANTIC_PLAN.md` for the sixteen Stage S0 conditions. Round 8 at 3847 to 3849 names `group_vars/all.yml` for why the shifted clock is not the default. A reconciler may want those files.
- Round 8 at 3451 and 3474 refers to a 27 August survey outside this file.
- Round 8 at 3499 says the v4 cockpit waits on a real-VM redeploy. Outside this range.
- Later rounds (10 and 11) are reviews that may correct Round 8 or 9. Not read here.
- Kafka is not mentioned anywhere in this range. Neither Kafka decision is touched.

## 8. Story

- Three of six log sources had never been read by the collector, and the fourth was two formats. Round 7 added `datadist`, `ildaemon` and `journald`, and split `dpl` out of `stdout`. 3001 to 3003, 3014 to 3021.
- The first fixture run found five silent defects. The largest: 41.8 % of the corpus reached durable storage with no severity, because a parser anchor missed the replay's prepended date. After the fix 96.94 % of the process tree stays on the worker and 3.06 % crosses the wire. 3032 to 3039, 3115 to 3116.
- Severity comes out of 99.83 % of process-tree lines and 99.97 % of DDS lines, against a gate of 99 %. The 0.17 % residual is named and a 1 % threshold is now enforced. 3085 to 3089, 3795 to 3796.
- Every collector regex cost is inside the instrument's noise. Two identical configurations differ by up to 19.3 %, so the host cannot resolve a change below about a fifth. Round 7's 17.3 % and 20.8 % effects were wall clock measuring the machine. 3951 to 3960, 4172 to 4175.
- Templating recipes are now measured on all seven families. `dpl` and `datadist` pad nothing and cost 5.21 and 5.37 core-seconds a million. `dds` costs 21.00 and is the family the corpus least represents. `odc` accepts 24.36 because one machine writes it. 4259 to 4269, 3391 to 3394, 4089 to 4093.
- The census corrected four facts and found a fourth collector version, two majors behind, on the storage machine. All four versions now pass 35 of 35 expectations, restart and rotation. 3517 to 3527, 3547 to 3572, 4204 to 4211.
- A worker's journal is 2,500 to 10,500 entries a day, nothing beside millions of process-tree lines, so all system logs are collected. Read as root, epn146 showed 986 kernel entries where an ordinary user saw none. An IOMMU fault naming `TfBuilder` was reconstructed, attributed and routed durable end to end. 3576 to 3589, 3619 to 3627.
- The template catalog shipped none of its guarantees until the external review. Four defects: wrong family label, one route of three, a watermark that dropped records, and a miner that skipped the FLOAT/NUM merge. Tests went from 8 to 23. The replay clock stays apart on a staging machine on purpose, so a cross-source correlation detector needs a real worker. 3962 to 4013, 3854 to 3861.
