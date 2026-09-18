# Brief: semantic retrieval, part 1 of 2

Source: docs/SEMANTIC_RESULTS.md lines 1 to 1416 (Morning report, Rounds 1 to 8).
Every line reference below is into that file unless another path is named.
Each fact is tagged [ARCH] when it belongs in the report body, or [CODE] when it is evidence only.

## 1. What semantic retrieval is for, and what unit is searched

[ARCH] Semantic retrieval lets a person find a log template by meaning, not by the exact words in it. Operators ask in their own words. Templates are written in the developers' words. 47.1 % of relevant natural-language (query, document) pairs share no analysed term at all (line 68 to 70). That vocabulary gap is what dense retrieval closes (line 70). The gap does not exist when the query is itself a log template, or a literal identifier. There the lexical engine wins (line 70 to 72).

[ARCH] Three tasks exist and are never averaged: natural-language operator retrieval, template similarity, and exact identifier lookup (line 564 to 567). The natural-language task is the headline (line 567).

[ARCH] The unit searched is the canonical template group: one record for one normalised event meaning, carrying every source instance that produced it (line 167 to 168). The reason is measured: one O2PDPSuite module banner is written by 188 different programs. Without a collapse rule, that one event fills every rank a metric at ten can see (line 162 to 165). A template, not a raw log line, is what the search runs over (line 148 to 150).

[CODE] Normalisation has four rules: placeholders become `<*>`, lower-case, whitespace collapsed, leading and trailing punctuation removed (line 175 to 181). A fifth rule, folding padded separators, was rejected: it merges 2 groups of 4,304 and adds one cross-family group (line 189 to 193).

## 2. What was built

### The corpus (Round 1)

[ARCH] The retrieval corpus is frozen. It holds 5,301 canonical template groups built from 18,011 source instances and 56,628,579 real log lines, across all seven log formats the collector reads (line 148 to 150). Collapsing 5,571 templates gives 5,301 groups: 270 templates were absorbed, 627 groups have more than one source instance, 192 groups span more than one family, 44 groups are contentless and marked rather than deleted (line 214 to 228). The plan expected about ten thousand templates. 5,301 is what the shipped recipe produces, and the plan says this count is not an acceptance gate (line 230 to 232). [CODE] The corpus identifier is `03640622026320ba` (line 152). The first freeze capped each archive family at three million lines and lost a quarter of the corpus: 4,299 groups became 5,301, a gain of 23 % (line 238 to 242). InfoLogger severity is `absent` on all 33,433,018 lines because the archive pull kept only family, source and message (line 287 to 289). Thirteen of sixteen Stage S0 conditions are met, one with a caveat, two need a person: source-owner approval of the registry and the exact live job-log path (line 296 to 311).

### The judgements (Rounds 2, 3, 6, 7)

[ARCH] Relevance judgements grade each (query, template) pair 0 to 3. Grade 3 directly answers, grade 2 is strong supporting evidence, grade 1 is related but not useful alone, grade 0 is irrelevant (line 934 to 939). Every grade in the file was made by a machine assessor. No person has judged the pool (line 76 to 80). [CODE] Round 2 carried 597 of 635 old judged pairs onto the frozen corpus, lost 38, collapsed 8, and left every row ungraded with `grade = -1` (line 324 to 326, 359 to 360). Round 3 had six machine assessors grade 589 carried items against a frozen rubric. The lowest weighted kappa was 0.769 and the highest 0.965, against a threshold of 0.60 (line 402 to 406). Assessors agree on whether a template matters and disagree between grade 2 and grade 3 (line 462 to 467). One calibration query, `detector readout error`, had no relevant template in the pool, a corpus-coverage case (line 501 to 504). Round 6 built the development benchmark: 97 intent groups, 130 queries, 6,410 judged candidates, lowest weighted kappa across nine pairs 0.761 (line 875 to 879). Round 7 topped the pool up by 3,141 candidates to 9,551 rows, with the lowest weighted kappa across 13 pairs at 0.689 (line 1095 to 1108).

### The harness and the adapters (Rounds 4 and 5)

[ARCH] The harness scores three tasks and never averages them. The primary metric is nDCG@10 with gains 0, 1, 3 and 7 for grades 0 to 3. Secondary metrics are MRR@10, pooled Recall@20, Success@5, Judged@10 and Judged@20 (line 565 to 574). Two rules stop a metric flattering a system: an unjudged result earns no gain and counts in Judged@k (line 576 to 579), and a duplicate group takes one rank (line 580 to 584). A query whose pool holds no grade 2 or 3 is a coverage case and is not scored (line 585 to 589). [CODE] Ten model adapters reproduce their official implementations, with facts read off each model card (line 538 to 541, 627 to 631). Two candidates were removed before measurement for licence and remote-code reasons, not quality (line 549 to 562). A machine adversary raised twelve objections and six held. Five were harness defects and are fixed: judged coverage could be gamed by returning fewer results, one adapter declared 512 tokens where its card says 8192, three deep metrics had no hand-computed test, six of ten fixtures compared a library against itself, and no run file existed on disk for any published number (line 749 to 754, 763 to 813). The honest test count is 62 for the harness and 40 for earlier stages, 102 in total, not the 96 Round 4 claimed (line 820 to 831). After repair, six fixtures independently check an encode path and four check only the scoring step (line 848 to 850).

### The benchmark and its two frozen numbers (Round 6)

[ARCH] The minimum practically important difference is 0.0320 nDCG@10. It is the half-width of the bootstrap interval on the paired difference between the two lexical controls, and it beat the 0.02 floor (line 882 to 885). The sealed held-out set has 62 intent groups and can detect 0.0523 nDCG@10 at two-sided 0.05 with 80 % power. It cannot see a 0.0320 difference (line 887 to 893). Reaching 0.0320 would need roughly 165 intent groups. The set is sealed and cannot be enlarged without breaking the plan (line 1009 to 1012). The development query set covers all fourteen query classes: 82 natural-language groups, 10 exact-identifier groups, 8 template-similarity groups (line 895 to 904). Every query is marked `synthetic: true`. None came from an operator (line 912 to 913). The product contract has two halves. Hard constraints read off the deployment include a 512 MB ALICE service memory ceiling, a 512 MB template catalog memory ceiling, a 20,000-template catalog ceiling, 1 GB heap per node, 4 worker processors and no external inference service (line 1019 to 1030). The latency targets are provisional, machine-authored, approved by nobody, and remove no candidate (line 1032 to 1036).

## 3. Valid numbers table

Corrections applied inside the range: Round 5 supersedes Round 4 on test counts, fixture strength and the `mDenseOn` input length. Round 6 lexical numbers replace the Round 4 smoke test. Round 7 and Round 8 re-score the lexical controls on deeper pools, so the latest pool is the valid one for development comparisons. The Morning report warns that Round 11, outside this range, replaces Round 8's fusion and reranking conclusion (line 123 to 125).

| Number | What it measures | Reference | Meaning | Line |
|---|---|---|---|---|
| 5,301 | canonical template groups in the frozen corpus | plan expected about 10,000, not a gate | the corpus is smaller than planned and that is accepted | 148, 230 to 232 |
| 56,628,579 | real log lines behind the corpus | seven formats | the corpus covers every format the collector reads | 148 to 150, 206 |
| 18,011 | source instances collapsed into groups | 5,571 templates before collapse | one event, many programs | 148, 206, 214 |
| 188 | programs that write one module banner | one event | why the unit is the group, not the template | 163 |
| 23 % | corpus gain when the three-million-line cap was removed | 4,299 to 5,301 groups | the cap cost a quarter of the corpus | 238 to 242 |
| 35.4 % | corpus groups with severity `absent` (1,877 of 5,301) | whole corpus | severity cannot be a representation field | 1179 to 1181 |
| 0.769 to 0.965 | weighted kappa, Round 3 machine assessors | threshold 0.60 | the rubric is gradeable | 402 to 406 |
| 0.761 to 0.884 | weighted kappa, Round 6 benchmark, 9 pairs | threshold 0.60 | agreement gate met on the shallow pool | 953 |
| 0.689 to 0.884 | weighted kappa, after Round 7 top-up, 13 pairs | threshold 0.60 | deeper candidates are harder to grade | 1107 to 1109 |
| 0.462 | lowest weighted kappa across six judging rounds | threshold 0.60 | the agreement gate fails on deep top-up rounds (Round 12, outside range) | 90 to 94 |
| 0.66 | precision-at-ten ceiling on the Round 3 pool | 0.645 on old binary labels | a model at 0.50 reaches 76 % of what is reachable | 498 to 499 |
| 97 / 130 / 6,410 | development intent groups / queries / judged candidates | plan gate: at least 80 groups | the benchmark exists and passes the size gate | 875 to 877, 1042 |
| 29.3 % | relevant share of the shallow pool | falls to 25.7 % after the top-up | relevance falls with depth, as it should | 941, 1100 to 1105 |
| 0.0320 | frozen minimum practically important difference, nDCG@10 | floor 0.02 | a system must beat this to count as better | 882 to 885 |
| 0.0523 | smallest difference the 62-group sealed set can detect | target 0.0320 | the held-out set is underpowered by 1.6 times | 887 to 893, 1000 to 1002 |
| 0.147 | paired standard deviation of nDCG@10 difference on development | input to the power calculation | why 62 groups resolve only 0.0523 | 998 to 1000 |
| 0.995 | Judged@10 on the development pool for the lexical controls | gate 0.95 | the pool is deep enough for the systems that built it | 977 to 979 |
| 0.33 | Judged@10 on the Round 4 calibration pool | gate 0.95 | why no model could be screened on the old pool | 719 to 721 |
| 0.525 vs 0.449 | BM25F vs plain BM25 nDCG@10, shallow pool | each other | first real lexical numbers, replaced later | 974 to 975 |
| 0.413 | plain BM25 nDCG@10 after the Round 7 top-up | 0.449 before | a deeper truth lowers the control, not a regression | 1147 to 1151 |
| +0.168 | DenseOn R1 over R0, nDCG@10, interval +0.122 to +0.221 | 0.0320 practical difference, 5.2 times | identity prefix is the largest single gain | 1061 to 1064, 1159 |
| +0.092 | potion R1 over R0, interval +0.055 to +0.132 | 0.0320 | the identity gain holds for the static model too | 1162 |
| −0.014 | DenseOn R2 over R1, interval −0.030 to +0.001 | 0.0320 | severity and detector add nothing | 1161 |
| +0.071 | BM25F multi-field over single-field plain BM25 | 0 | searching more than one field is the whole lexical gain | 1219 to 1220 |
| 0.493 / 0.484 / 0.483 / 0.477 / 0.461 | BM25F nDCG@10 by field-weight setting | default 0.484 | every tuned weight setting is worse than the default | 1204 to 1213 |
| 0.697 / 0.695 | LateOn-Code / LateOn nDCG@10, development leaderboard | DenseOn 0.640 | late interaction is the research ceiling | 1272 to 1275 |
| +0.055 | LateOn over DenseOn, interval +0.024 to +0.085 | 0.0320 | real | 1299 |
| −0.046 | three-way RRF fusion over LateOn, interval −0.070 to −0.026 | 0 | fusion is worse than the best single system (superseded by Round 11) | 1300, 1309 to 1315 |
| +0.011 / +0.010 / +0.003 | DenseOn over Qwen3 0.6B / over mDenseOn, LateOn-Code over LateOn | 0.0320 | every expensive model matches a cheaper one | 1304 to 1306 |
| 0.471 / 0.396 | BM25F / plain BM25 nDCG@10 on the final development pool | DenseOn 0.640 | the deployed engine is 0.169 behind the dense model on development | 1275, 1285, 1287 |
| 1,556 s vs 47 s | Qwen3-Embedding-0.6B vs DenseOn corpus encode time, laptop | 33 times the cost | no measurable quality for the cost | 1322 to 1324, 1336 |
| 1.5 s | potion-retrieval-32M corpus encode time | DenseOn 47 s | the static model is the cheap option | 1334 |
| 3,175 / 2,152 / 1,504 | index bytes per template, DenseOn / potion / BM25F | each other | vector index cost per template | 1334 to 1338 |
| 7,975,771 bytes | lexical index size for 5,301 groups | one primary shard | the lexical index is under 8 MB | 693 |
| −0.0006 | nDCG@10 change from HNSW against exhaustive cosine, P-DenseOn | 0 | approximate search is nearly free at 5,301 documents | 1352, 1356 to 1358 |
| 0.990 / 0.998 | mean overlap at 10 / Kendall tau, P-DenseOn HNSW parity | 1.000 | 102 of 112 queries return an identical top ten | 1352, 1356 to 1358 |
| 0.689 | DenseOn held-out nDCG@10, natural language | BM25F 0.371 | the only held-out number in the recommendation | 24, 48 to 52 |
| +0.318 | DenseOn over BM25F on held-out natural language, interval +0.219 to +0.418 | 0.0523 held-out floor | the headline win, large enough for the underpowered set | 58 to 60 |
| 68 of 93 | blind preference judge decided pairs won by DenseOn | 93 decided of 96 shown | a second reading agrees with the metric | 60 to 61 |
| 0.882 / 1.000 | BM25F held-out nDCG@10 / Success@1 on exact identifiers | DenseOn 0.839, Success@1 0.600 | the lexical engine wins identifiers | 25, 33 |
| 0.690 | BM25F held-out nDCG@10 on template similarity | DenseOn 0.340, gap +0.350 interval +0.090 to +0.628 | the lexical engine wins find-similar | 26, 34, 63 to 65 |
| 8 and 6 | held-out intent groups for identifier and similarity | 47 natural-language groups | read those two wins with their margins | 71 to 73 |
| 0.685 vs 0.634 | DenseOn top 30 reranked by LateOn vs DenseOn alone, development, interval +0.028 to +0.080 | 0.0320 | reranking is real on development, untested on held-out | 32, 48 to 52 |
| 0.6 ms | cost of the reranking step | all latency is in candidate fetch | reranking is not the latency | 39 |
| 194 MB | reranking token matrix for 5,301 templates | grows with the corpus | the one real deployment cost of the recommendation | 54 to 56 |
| 0.031 | gain of an oracle query router | 0.0320 practical difference | no router is worth writing, a toggle wins | 41 to 46 |
| 0.048 | RRF fusion loss against the dense model alone | 0 | fusion of any kind: do not | 35 |
| 0.020 | nDCG@10 cost of the approximate vector index in the Morning report | 0 | not worth it at 5,301 templates | 36 |
| 377 / 204 / 29 of 110 | relevant templates gained / lost / queries worse than today's search | today's search | the winner's losses need a person's eye | 126 to 128 |

## 4. What failed, was capped, or was never reached

- [ARCH] No quantised arm was run, so the loss from shrinking any model is unmeasured (line 83 to 84).
- [ARCH] The memory-ceiling objection in an earlier draft was wrong and withdrawn (line 84 to 85). See section 7.
- [ARCH] The assessor agreement gate fails: lowest weighted kappa 0.462 against 0.60, confined to deep top-up rounds where 86 % of candidates are grade 0 (line 90 to 94).
- [CODE] The environment moved mid-run: installing `tokenlearn` downgraded torch from 2.14.0 to 2.11.0. Different stages ran on different versions (line 95 to 98).
- [ARCH] No static model is promoted. The best trained one beats stock by 0.039 and does not survive Holm correction across fifteen comparisons (line 99 to 101).
- [ARCH] The held-out labels have no measured agreement. The custodian judged alone (line 102 to 104).
- [ARCH] The sealed set is underpowered: 62 groups resolve 0.052, the project cares about 0.032. The fallback comparison is undetermined, not equal (line 105 to 108).
- [ARCH] L2, the pinned reference search system, indexes code symbols and extracted zero entities from 5,301 templates. Every "custom against reference" conclusion is unsupported, and L3 was not built (line 109 to 112, 1222 to 1245).
- [ARCH] SP0, the maintained learned sparse control, was never attempted. No learned sparse system was compared (line 113 to 114, 1400 to 1404).
- [ARCH] Stage S8, training ALICE static models, was never reached. Only the stock controls were measured: 0.479 and 0.471 (line 115 to 117, 1406 to 1410).
- [ARCH] `mLateOn` was capped at 49 minutes 35 seconds against a 45-minute budget, no ranking produced (line 118 to 119, 1394 to 1398).
- [ARCH] `LateOn` wins development and has no deployable form. No multi-vector store was built, so it fails the Stage S9 gate and is the research ceiling (line 1361 to 1366).
- [CODE] The first Stage S3 pass was unreadable: judged coverage ranged 0.559 to 1.000 and biased arms both ways. A 3,141-candidate top-up fixed it (line 1076 to 1097).
- [CODE] Only one second-pass assessor ran in the top-up, because spawning a sixth agent failed on pty exhaustion. Four agreement pairs instead of nine (line 1111 to 1122).
- [CODE] Within-assessor consistency is unmeasured (line 1124 to 1127).
- [CODE] The `mDenseOn` input-length fix turned the declared limit into a padding width. `mDenseOn` died with `Invalid buffer size: 96.00 GiB`, and Qwen3 ran 75 minutes without finishing. Fixed and tested (line 1373 to 1391).
- [CODE] The first index run of the reference search system wrote a 297 MB directory into this repository. It was removed (line 1251 to 1254).

## 5. Decisions settled

- [ARCH] Unit: the canonical template group (line 167 to 168). Settled in Round 1.
- [ARCH] Representation: dense and static models use R1, identity plus template. BM25F uses R3, structured fields (line 1191 to 1195). Severity and detector are excluded (line 1197 to 1201).
- [ARCH] Lexical field weights: the untuned default stays, because every tuned setting is worse (line 1213 to 1216).
- [ARCH] Model per task: DenseOn for natural language, BM25F for exact identifiers and template similarity (line 19 to 26).
- [ARCH] Reranking depth: DenseOn exact cosine scan, top 30 reranked by LateOn MaxSim (line 32). This is the Morning report's build, measured on development only (line 48 to 52).
- [ARCH] Fusion: do not (line 35). Round 8's own fusion finding is at line 1309 to 1319, and the Morning report says Round 11 replaces it (line 123 to 125).
- [ARCH] Approximate vector index: not at 5,301 templates (line 36). Note that Round 8 measured HNSW parity as passing at −0.0006 (line 1352 to 1358). The two readings differ in size, 0.020 against 0.0006, and the Morning report is the later one.
- [ARCH] Router: a one-bit toggle, not a classifier, because an oracle router gains only 0.031 (line 41 to 46).
- [ARCH] Deploy or not: the recommendation is provisional. It does not become the production selection until a person judges the pool (line 76 to 80). The rework context input says the shifter view ships the semantic model disabled (reports/loggy-report/briefs/inputs/rework-context.md:167).
- [ARCH] Expensive models: Qwen3-Embedding-0.6B, mDenseOn and LateOn-Code are indistinguishable from cheaper siblings and are not chosen (line 1321 to 1327).

## 6. Not measured or not run, verbatim

- "No quantised arm was run, so the loss from shrinking any model is unmeasured." (line 83 to 84)
- "The held-out labels have no measured agreement." (line 102)
- "SP0, the maintained learned sparse control, was never attempted. No learned sparse system was compared at all." (line 113 to 114)
- "Stage S8, the ALICE static models, was never reached." (line 115)
- "L3 was not built because it had nothing to be differential against." (line 111 to 112)
- "The reranking configuration never faced the sealed set, because it did not exist when that set was opened." (line 48 to 50)
- "No person has judged this pool." (line 78)
- "Within-assessor consistency is unmeasured in this benchmark." (line 1127)
- "no census has yet seen an active run, so active-run source coverage rests on the archive rather than on a live observation." (line 314 to 316)
- "These are laptop numbers and they do not transfer to EPN hardware. The ratios between them do." (line 1341 to 1342)
- "No such form was built or verified in this run, so `LateOn` fails the Stage S9 gate and is recorded as the research ceiling." (line 1364 to 1366)
- "Cheap static fallback: not established" (line 37)
- "Its +0.054 is measured on 97 development intent groups with full judged coverage." (line 50 to 51). The Morning table gives the same comparison as 0.685 against 0.634, a gap of 0.051 (line 32). The two figures do not agree to the third decimal.

## 7. Cross-range notes for the other semantic brief

- The memory note claim, "semantic model peaks near 360 MiB against a 384 MiB ceiling", is not in this file. A grep for `360 MiB`, `384 MiB`, `360 MB` and `384 MB` over the whole of docs/SEMANTIC_RESULTS.md returns no line. The only memory ceiling in my range is 512 MB for the ALICE service and 512 MB for the template catalog (line 1028 to 1029). The Morning report says a memory-ceiling objection was withdrawn and points at Round 11 (line 84 to 85). The other brief must check Round 11 for the correct memory figure and its source.
- Round 11 replaces Round 8's fusion and reranking conclusion. Read Round 11 before quoting Round 8 (line 123 to 125).
- Round 12 holds the 0.462 kappa failure and the torch downgrade (line 90 to 98). Round 13 holds the static model result, 0.039 without Holm survival (line 99 to 101).
- The held-out evaluation (0.689, +0.318, 68 of 93, the 8 and 6 group identifier and similarity sets) is outside my range. Its round number must come from the other brief.
- DenseOn development nDCG@10 is 0.670 on the Round 7 pool (line 1133), 0.640 on the Round 8 pool (line 1275), and 0.634 in the Morning report's reranking comparison (line 32). The other brief should find which pool the 0.634 was measured on.
- The 204 lost templates and 29 of 110 worse queries (line 126 to 128) are described in a round outside my range.
- Two Kafka decisions are not touched by this brief and must not be merged with anything here.
