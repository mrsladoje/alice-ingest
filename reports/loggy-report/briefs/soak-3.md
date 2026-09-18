# Soak brief 3 of 5 — docs/SOAK_RESULTS.md lines 1983 to 2998

Source file: docs/SOAK_RESULTS.md. Every line number below points into that file unless a path is given.
Level tags: [A] = architecture-level, belongs in the report. [C] = code-level, evidence only.

## 1. Coverage

This range holds Round 6 stage H, Round 6 stage I and the four open questions. Stage H (lines 1983 to 2607) chooses the template parser, records everything tried on Drain3, rewrites the masker, fixes one recipe per family, mines the whole corpus, then samples sixteen run tags and forty unseen InfoLogger partitions. It ends with a collector defect that the audit found and the limits of the stage. Stage I (lines 2609 to 2943) re-mines the template set from a recipe now in the repository, corrects a stage H cost reading, measures the noise floor of the embedding ladder with a bootstrap, fixes the source split rule, and builds the judged query set. The open questions (lines 2944 to 2996) carry the burst gap, the InfoLogger tap, the 1,000-a-second scope and two unsurveyed hosts. The "Read this first" section (lines 11 to 203) retracts nothing in this range; its retractions concern flush, heap, core placement and rate. The standing warning (lines 263 to 306) applies to every cost here: the stage ran in one Colima VM pinned to four processors on a laptop (line 2607), so only shapes, knees and rankings transfer.

## 2. Valid numbers

| Number (exact) | What it measures | Baseline or reference | What it means for the design | Source line |
|---|---|---|---|---|
| 3,822 templates, 19.91 core-s/M, 26.3 MB peak RSS [A] | Drain3 as shipped over the whole corpus (H1b) | PIPLUP H2 on the same lines | The shipped parser is the cost reference for the stage; stage I later notes this control was not re-measured (line 2711) | 1996 |
| 2,444 templates, 50.87 core-s/M, 206.5 MB [A] | PIPLUP over the whole corpus (H2) | H1b | PIPLUP fails the cost gate | 1997 |
| 2,853 templates, 17.32 core-s/M, 23.1 MB [C] | Drain3 InfoLogger only (H3b) | H4 | Same verdict on one family | 1998 |
| 1,337 templates, 38.80 core-s/M, 106.1 MB [C] | PIPLUP InfoLogger only (H4) | H3b | Same verdict on one family | 1999 |
| 2.56× and 2.24× [A] | PIPLUP cost over Drain3, whole corpus and InfoLogger | Gate 3 allowed ±25 % | PIPLUP rejected on cost | 2007 |
| 7.8× (206 MB against 26 MB) [A] | PIPLUP memory over Drain3 | Drain3 peak RSS | A token-frequency map grows with vocabulary; Drain3 stays | 2008-2009 |
| 28.9 % [A] | Drain3 run-to-run cost spread on identical work (17.32 and 22.33) | Gate width ±25 % | Any cost gap under about 29 % in this round is not believable | 2012-2015 |
| Median real words 3 / 5 / 6; contentless 44.9 % / 28.4 % / 10.4 % [A] | Readability of Drain3 shipped, PIPLUP, Drain3 with recipe, on InfoLogger | Each other | The recipe passes PIPLUP on readability at no cost | 2036-2038 |
| 96.21 core-s/M [C] | The logparser repository's own Drain on a 100,000-line InfoLogger slice | Control for the shelf table only | Shelf parsers must be read against this control, never against our 15 to 22 | 2076-2077 |
| LFA 33.32 (0.35×), AEL 43.55 (0.45×), IPLoM 55.60 (0.58×), LogLSHD 104.64 (1.09×), Logram 466.10 (4.8×), LenMa 691.29 (7.2×), SHISO 1,284.40 (13.3×); Spell, LogMine no result in 600 s [C] | Shelf parsers, cost per million lines | The 96.21 control | Every shelf parser rejected on cost, streaming or readability | 2084-2094 |
| 9 % slower [A] | LogLSHD against the Drain in its own repository | Paper claims 73 % faster | Published speed claims do not transfer to ALICE data | 2096-2098 |
| 1,347 templates, 43.36 core-s/M, 29.9 % contentless [C] | KELP after three source patches plus Drain3 masking | Drain3 with recipe | KELP loses on every column at three times the cost | 2110-2112 |
| 1,145 → 1,549 templates, 49.7 % → 65.4 % contentless, 14.23 → 41.43 core-s/M [C] | PIPLUP preprocessing bolted onto Drain3 | Drain3 alone | PIPLUP's readability did not come from its regexes; negative result | 2118-2121 |
| 801 → 820 templates for +19 % cost [C] | Hand-written ALICE semantic masking rules | Without them | Nothing gained; skip | 2122-2123 |
| infologger 675 / 18.13 / 94.5 % vs 899 / 17.96 / 95.0 %; stdout 936 / 16.70 / 99.7 % vs 1,264 / 16.90 / 99.7 %; dds 224 / 51.16 / 88.1 % vs 701 / 51.14 / 92.0 % [A] | Numeric tokens parametrised (default) against kept, per family: templates, core-s/M, words kept | Each family against itself | Free in processor time; keep default on for InfoLogger and stdout, turn off for dds | 2134-2141 |
| 914, 915, 915 templates on InfoLogger; 688, 766, 890 on stdout; 500 costs 46.41 against 21.48 [C] | drain_max_children at 25, 100, 500 | Each other | Inert on one family, harmful on another; leave at 100 | 2144-2148 |
| 22.3 % of stdout lines improved, 72.5 % worse [C] | Deleting separators with drain_extra_delimiters | Per-line over the whole corpus | Deleting a character is not splitting on it; rejected | 2153-2156 |
| Same 645 templates, words kept 95.3 % → 84.4 % [C] | A severity mask | Without it | Rejected; would merge ERROR with INFO twin | 2157-2158 |
| +4.5 % cost for 8 % fewer templates on stdout [C] | A blanket value-after-= rule | Without it | Rejected; the tree already does this | 2159-2162 |
| Same 178 templates, median literal words 287 → 221 [C] | A flag-name mask on dds | Without it | Pure loss; rejected | 2163-2165 |
| +56 % masking time on InfoLogger [C] | Hash and mixed-case identifier masks | Without them | Rejected; also added templates on stdout | 2166-2167 |
| 94.2 % words at depth 4, 97.6 % at depth 6, 99.7 % at depth 8 [A] | Words kept on 3,000,000 stdout lines, clock left in | Each other | The readability gain belongs to depth; knee at 6 | 2180-2182 |
| 0.1 points [A] | Gain from removing the clock prefix alone at depth 4 | Depth-4 baseline | The prefix removal does not buy readability by itself | 2182-2183 |
| 21.48 → 16.70 core-s/M [A] | stdout mining cost with the clock stripped from the message | Clock left in | Prefix removal earns its place on cost and on routing | 2186-2187 |
| 17.07 → 17.18 core-s/M (0.6 %), 2.6 MB [A] | stdout cost and peak RSS from depth 4 to depth 12 | Depth 4 | No processor or memory argument against depth; depth 8 chosen | 2190-2192 |
| 6.3 % → 1.0 %; 258 templates keep a real `<FLOAT>` [A] | Share of stdout lines on a template containing `<*>`, without and with the FLOAT/NUM merge patch | Same 3,000,000-line run | Keep the two masks apart with the four-line merge patch | 2199-2203 |
| 1.4 % [A] | Line-weighted share of InfoLogger lines on a contentless template | 44 % template-weighted | The template-weighted headline inflated the case against Drain3; a shifter feels the line-weighted one | 2223-2226 |
| 14.93 → 41.77 core-s/M (2.8×) [C] | Cost inflation from two containers on disjoint cpusets | Sequential run | Rig fault; batch discarded | 2230-2232 |
| Recipe: infologger `= ; :` depth 8 sim 0.4 parametrised; stdout `= ;` depth 8 sim 0.4 parametrised, needs a collector rule; dds `=` depth 8 sim 0.5 kept [A] | The per-family configuration of record | Shipped single configuration | This is the parser configuration the system runs | 2246-2250 |
| stdout 88.3 % (`= ;`), 87.7 % (`= ; :`); infologger 78.6 % (`= ; :`), 72.4 % (`= ;`); dds 75.5 % (`=`), 75.3 %, 72.5 % [A] | Words kept by padded-separator set per family | Each family against itself | Three families want three answers; this justifies splitting the configuration | 2258-2262 |
| infologger 908 / 14.84 / 90.7 % → 675 / 18.13 / 94.5 %; stdout 766 / 21.48 / 94.2 % → 936 / 16.70 / 99.7 %; dds 183 / 52.70 / 72.9 % → 224 / 51.16 / 88.1 %; weighted 17.65 → 17.57 [A] | Templates, core-s/M, words kept: shipped against recipe, one clean run, six arms | Shipped configuration | The recipe is cost-neutral (half a percent) and much more readable | 2270-2279 |
| 14.68, 14.84, 14.92, 15.06, 15.10 [C] | Five clean readings of the InfoLogger shipped control | The 22.39 outlier | The earlier 21.7 % saving was not real | 2281-2285 |
| 44.9 % → 10.4 % (InfoLogger), 26.4 % → 7.4 % (stdout) [A] | Contentless templates, shipped against recipe | Shipped | Readability at the same price | 2287-2288 |
| InfoLogger 58.1 %, stdout 41.8 %, dds 0.1 % [A] | Corpus line weights | Operations report dds is highest-volume during data-taking | dds is under-sampled here and is the most expensive per line | 2291-2296 |
| 18,037 templates a core-second; 0.55 core-s for 10,000 templates against roughly 780 core-s to mine; 581 templates a core-second for a full transformer, 17 core-s; 0.07 % [A] | Embedding cost against mining cost | Mining pass | Embedding is not a reason to keep the tree shallow | 2300-2304 |
| 155 → 157 templates [A] | Templates covering 99 % of stdout lines, depth 6 to depth 8 | Depth 6 | Extra depth only splits the tail; busy series untouched | 2307-2310 |
| 74 to 87 % [A] | Share of mining cost that was masking | Whole mining pass | Masking was the step to attack | 2314 |
| infologger 11.93 → 1.82 (−84.7 %); stdout 12.24 → 1.73 (−85.8 %); dds 40.22 → 5.09 (−87.3 %) core-s/M [A] | Masking cost alone, reference against rewritten | Reference rules | Seven rewritten regexes take 85 to 87 % off masking, byte-identical output | 2318-2322 |
| 40 to 75 % slower [C] | UUID, IP, HEX, comma-NUM rules with lookbehind but no leading literal, on dds | Reference | The first hypothesis was wrong; regex first-opcode position is the mechanism | 2340-2343 |
| 0 differences on all 3,000,000 lines per family; 0 on stripped input; 675, 936, 701 clusters identical [A] | Equivalence of the rewritten masker | Reference masker | The rewrite changes cost only | 2357-2361 |
| 3,000,000 random strings, no difference; 89 to 100 % of lines contain a digit [C] | Differential fuzz; digit pre-check viability | Reference | The `regex` module is not byte-identical; digit pre-check cannot pay | 2363-2368 |
| infologger 15.11 / 4.82 / 19.00 / 8.85; stdout 24.69 / 10.36 / 19.33 / 7.23; dds 60.95 / 15.71 / 59.39 / 17.56; weighted 19.16 → 8.18 core-s/M [C] | Whole pipeline, four arms per family on 3,000,000-line samples: shipped, shipped+fast masker, recipe, recipe+fast masker | Shipped | Ratios inside the run are valid; the sample flatters the change (line 2382) | 2374-2384 |
| 908 = 908, 675 = 675, 936 = 936, 183 = 183, 701 = 701 [C] | Template counts between the two masker columns | Each other | End-to-end equivalence check | 2385-2387 |
| 1,306 / 10.59 / 90.2 % / 7.9 % (infologger, 26,505,911 lines); 701 / 15.12 / 92.0 % / 0.0 % (dds, 43,972 lines) [A] | Whole-corpus templates, core-s/M, words kept, contentless | Stage H sample | Costs later re-read as 9.53 and 15.51 (line 2699-2701), inside spread; templates stand | 2401-2403 |
| 1,004 templates, 99.6 % words kept, 7.1 % contentless (stdout, 19,046,730 lines) [A] | Whole-corpus stdout output | Sample | Output stands; the 12.00 cost is retracted (see section 3) | 2402 |
| 3,011 templates on 45,596,613 lines [A] | Whole-corpus template count with recipe and fast masker | H1b's 3,822 | Fewer and better templates; a floor, not a total (line 2468) | 2404-2409 |
| 94.5 % → 90.2 % [A] | InfoLogger word retention, 3,000,000 lines against 26.5 million | Sample | More variety arrives at scale and some of it wildcards | 2411-2413 |
| 86 run tags, 2,013 dds tarballs [A] | Archive size | Round 6 mined one run tag | Sampling by period matters | 2424 |
| dds 1,192,999 lines / 16 runs / 1,570 templates / 15.98 / 87.6 % [A] | Sixteen-run dds sample | 15.12 on the single run | 5.7 % difference on a 28.9 % spread: the saving is a property of the data | 2430-2436 |
| stdout 2,246,331 lines / 16 runs / 1,025 templates / 97.5 % words [A] | Sixteen-run stdout sample (cost 6.29 excluded, line 2438) | Single run 1,004 on 19,046,730 lines | Variety between runs finds templates about 8.6 times faster per line | 2431, 2459-2466 |
| dds +933 +2 +59 +182 +43 +73 +53 +1 +17 +2 +1 +3 +140 +1 +35 +25; stdout +205 +66 +246 +23 +4 +1 +25 +136 +0 +42 +3 +123 +2 +0 +133 +16 [A] | New templates per successive run | Saturation | Neither family has saturated after sixteen runs | 2448-2452 |
| Four templates across the last fourteen million stdout lines; six million InfoLogger lines without one [A] | Novelty from more lines of the same run | Run-boundary novelty | Volume within a run is nearly exhausted | 2455-2457 |
| 2,595 templates from sixteen runs of two families; 70 run tags unsampled [A] | Growth beyond the 3,011 floor | 3,011 | Stage I must treat the count as open-ended | 2468-2472 |
| About 9,000 to about 130,000 dds lines, fifteen-fold [A] | Size swing between adjacent runs | Each other | A run is not a unit of constant size | 2474-2476 |
| 42 templates in the last 1.5 million InfoLogger lines [C] | Boundary effect inside one period | Saturation at 1,306 | Same mechanism as the run boundaries | 2480-2481 |
| 179 InfoLogger objects, partitions 13 to 193; 40 mined; 40 of 139 unseen fed, capped 200,000 lines each [A] | InfoLogger partition sampling | Round 6's 40 | Sample spans periods | 2486-2489 |
| Phase 1: 26,505,911 lines, 1,306 templates, 9.18; Phase 2: 6,927,107 lines, 1,497 templates, 10.24 core-s/M [A] | A saturated tree fed forty unseen partitions | 10.59 whole corpus | Cost holds on unseen data; no drift | 2493-2497 |
| 191 templates, 14.6 % [A] | Gain from forty new periods | 1,306 | Boundary novelty is real in every family and an order of magnitude weaker in InfoLogger | 2499-2507 |
| 81.5 % → 74.0 % [C] | Word retention, phase one against phase two, raw-line denominator | Only against each other | A signal, not a conclusion; not comparable with 90.2 % | 2509-2519 |
| infologger 1,497 (80 partitions), dds 1,570 (16 runs), stdout 1,025 (16 runs); total 4,092; 36 % above 3,011 [A] | Template count with more periods | 3,011 | The count is a curve driven by runs and partitions sampled, not lines | 2523-2533 |
| 345 stdout lines in 3,000,000 [A] | stdout lines that captured a severity under the shipped collector rule | All O2 lines, 99 % of stdout | Severity tiering was silently disabled for the largest family | 2564-2565 |
| 42 % of all lines [A] | Share of the corpus that went to the replicated storage tier as a result | Tier sized as low-volume | The defect loaded the tier the design keeps small | 2569-2571 |
| INFO 96.9 %, WARN 3.0 %, STATE 0.1 %, ALARM 0.0006 %; about 32-fold [A] | stdout severity distribution; fall in central's share after the fix | Before the fix | The fix restores the tiering the design assumes | 2574-2576 |
| 43,960 of 43,972 lines [C] | dds lines on which the dds parser captures time, severity, source and thread | All dds lines | dds and InfoLogger need no collector change | 2586-2588 |
| 7,912 lines in 3,000,000 (stdout); 12 in 43,972 (dds) [C] | Indented continuation lines the corpus builder did not join | Production joins them | Too small to move any figure; banner templates will differ in production | 2590-2595 |
| 43,972 dds lines against round 4's 267,607 [A] | dds corpus size this stage | Round 4 | Every dds figure rests on the smaller set | 2599-2602 |
| Recipe: depth 8, max children 100, similarity 0.4 (infologger, stdout) and 0.5 (dds), numeric tokens parametrised (infologger, stdout) and kept (dds), separators `= ; :` / `= ;` / `=`, clock stripped from stdout and dds, fast masker, four-line merge patch [A] | The recipe as committed to the repository | Stage H table | The same recipe, now reproducible | 2624-2630 |
| 55,963,050 lines, 4,221 templates; infologger 1,497, stdout 1,154, dds 1,570 [A] | One tree per family carried across all three corpora | Stage H's 4,092 | The union is the number stage I embeds; the 129 difference is a denominator effect in stdout | 2632-2652 |
| Phase 1: 45,596,613 lines, 1,306 / 1,004 / 701; Phase 2: 3,439,330 lines, +0 / +150 / +869; Phase 3: 6,927,107 lines, +191 / +0 / +0 [A] | Template gains per corpus | Stage H | Phase 1 reproduces stage H to the template | 2636-2644 |
| 0.23 core-s for 4,221 templates; roughly 380 core-s to mine phase 1; 0.06 % [A] | Embedding bill with the larger set | Mining | Still not a reason to keep the count down | 2654-2658 |
| 981 against 1,004 [C] | stdout templates with and without a second masker pass | Stage H | A 23-template gap exposed a double-masking fault; every stage I number is from the corrected run | 2667-2672 |
| 12.00 published; 6.37 repeat; 6.81 stdout alone; 6.65 interleaved; mean of fresh readings 6.61; within 6.9 % [A] | Four readings of the stdout whole-corpus cell, byte-identical output | Each other | The published 12.00 is 1.8 times the fresh mean and is contaminated | 2679-2691 |
| infologger 9.53 (252.6 core-s); stdout 6.65 (126.7); dds 15.51 (0.7); whole corpus 8.33 core-s/M, 380.0 core-s on 45,596,613 lines [A] | Whole-corpus mining cost, recomputed; the figure of record | 11.18 (retracted) | 8.33, not 11.18; infologger 10 % under and dds 3 % over stage H, inside spread | 2697-2706 |
| 0.065 to 0.076 wide against a gate of 0.03 [A] | 95 % interval width of macro purity for one model | Gate | The ladder's absolute numbers cannot carry a decision; orderings are stable | 2717-2721 |
| 400 replicates, 4,220 templates, k = 10, sources need 10 templates to vote [C] | Bootstrap setup | — | One row of 4,221 dropped for an empty template | 2729-2730 |
| potion-base-8M 0.380 (0.065 / 0.075); potion-base-32M 0.390 (0.066 / 0.076); all-MiniLM-L6-v2 0.410 (0.068 / 0.074) [A] | Observed macro purity and cached / recomputed 95 % widths | Macro null 0.0195 (line 2807) | Single purity is good to about ±0.037; the whole round 3 ladder spanned 0.037 | 2748-2755 |
| MiniLM − 32M +0.020 [+0.008, +0.033] / [+0.002, +0.040], 392 of 400; MiniLM − 8M +0.030 [+0.019, +0.042] / [+0.014, +0.049], 398 of 400; 32M − 8M +0.010 [+0.005, +0.015] / [+0.002, +0.018], 399 of 400 [A] | Paired gaps with 95 % intervals under both bootstraps | Zero | Every interval excludes zero; rank on paired differences | 2767-2777 |
| 0.010 to 0.038 against 0.065 to 0.076; three to six times [A] | Paired widths against marginal widths | Each other | The gap is measured far better than the numbers in it | 2771-2773 |
| 0.380, 0.390, 0.410 now against 0.380, 0.387, 0.414 in round 3; within 0.004 [C] | Ladder rungs, then and now | Not like-for-like | Ordering transfers; values do not | 2792-2797 |
| Templates scored 3,822 → 4,220; sources with ≥ 10 templates 23 → 40; largest share 57 % → 17.7 %; ODC share 57 % → 15.0 %; macro null 0.035 → 0.0195 [A] | The scored set, round 3 against now | Each other | The null halved because concentration halved; purity must be read against its own null | 2801-2812 |
| 22 dds "sources", five above the ten-template floor; dds/epn287 at 747 templates [C] | dds labelled by host | Programs | A dds source is a host, not a program; dds excluded from the evaluation pool | 2821-2839 |
| Dev: 5 sources, 517 templates, null 0.198; Held-out: 5 sources, 259 templates, null 0.197 [A] | The evaluation splits and their nulls | Plan predicted near 0.20 | A dev purity of 0.34 beats a full-set 0.39; every score is against its own null | 2845-2861 |
| 339 of 517 (66 %); 101 of 259 (39 %) [A] | Largest source share in dev and held-out | Concentration | Dev is close to a two-source question; held-out is better spread | 2862-2868 |
| 278 sources [C] | Sources left as training text | — | Includes ODC and all 22 dds hosts | 2870-2871 |
| 635 query-template pairs, 177 judged relevant, 20 queries, 7 shelf models, top ten each [A] | The judged query set | — | Judged before any score existed; no human ground truth (line 2881) | 2875-2879 |
| Per-query relevant / pooled: 27/36, 22/33, 20/28, 17/23, 12/30, 10/34, 9/33, 9/34, 8/45, 8/23, 7/34, 7/40, 6/22, 5/28, 4/21, 2/29, 1/22, 1/35, 1/38, 1/47; total 177/635 [C] | Relevance per query | — | Four queries have one relevant template | 2897-2917 |
| 0.645 [A] | Ceiling of mean precision at ten on this pool | 1.0 | A model at 0.50 has reached 78 % of what is reachable | 2919-2926 |
| 10,000 to 20,000 against 78; gap 128× to 256× [A] | Planned per-worker burst against the busiest worker-second in six months of archive | Each other | The burst arms are sized by a number the archive does not support; three explanations, none chosen | 2948-2973 |
| 9,781 a second [A] | Farm-wide peak in the busiest hour | Bottom of the 10,000 to 20,000 band | If the band is farm-wide it is corroborated and per-worker burst is about 1/300th | 2967-2970 |

## 3. Retracted or superseded

- "Read this first" (lines 11 to 203) withdraws nothing in this range. All withdrawals below come from later text inside the range.
- 1.8× cheaper to mine, on fewer templates (lines 1991-1992 and 2383-2384). Withdrawn as a quoted multiple at lines 2708-2713: the 19.91 control was not re-measured, so no new multiple is quoted. The direction (cheaper) stands.
- Whole-corpus cost 11.18 core-s/M (lines 2384, 2404, 2406). Withdrawn at lines 2693-2704; the figure of record is 8.33.
- stdout whole-corpus cost 12.00 core-s/M (line 2402). A contaminated reading, withdrawn at lines 2693-2694; fresh readings 6.37, 6.81, 6.65.
- 2.3× cheaper on the three-million-line samples (line 2381). Flattered by the sample, per lines 2382-2385. Then the whole-corpus replacement itself moved (above).
- "21.7 % cheaper" for the recipe (line 2281). Withdrawn at lines 2281-2285; the 22.39 control was an outlier; the recipe is cost-neutral.
- "Doubles the cost, 33.35 against 14.93" for parametrize_numeric_tokens (lines 2126-2128). From a contaminated block; the knob had never been measured; corrected table at lines 2134-2136.
- Per-family trees, first attempt: 1,186 templates at 19.94 against 1,058 at 19.81 (lines 2149-2152). Withdrawn at line 2151; the trees were tuned on a wrong conclusion.
- drain_max_children called useless (line 2147). Corrected at lines 2145-2148: harmful on stdout, inert on InfoLogger.
- The clock prefix credited with the whole readability jump (lines 2183-2184). Corrected: the gain belongs to depth (lines 2180-2183).
- A table mixing a 400,000-line figure with a three-million-line figure (lines 2203-2204). Replaced by three figures from one run (lines 2200-2203).
- The readability metric that said 88.8 % of stdout lines improved with padding (lines 2208-2216). Wrong by construction; replaced by two metrics at lines 2217-2221.
- degenerate_templates_pct used as a headline (lines 2223-2226). The line-weighted 1.4 % replaces the template-weighted 44 %.
- SHISO 86× and IPLoM 4× worse than our drain3 (lines 2077-2080). Withdrawn; shelf parsers compare only against the 96.21 control.
- Four depth-sweep and four mask-sweep cost readings of 33 to 101 core-s/M (lines 2238-2240). Discarded; template and word counts from those arms kept.
- H1 and H3 readings of 21.80 and 22.33 (lines 2001-2003). Superseded by H1b and H3b after a rig fault; kept only as a spread measurement.
- The masker hypothesis "the alternation was the problem" (lines 2340-2343). Falsified; the position of the first literal is the mechanism.
- "Fetching InfoLogger across partitions ... has not been run" (lines 2481-2482). Superseded inside the range: the next section (lines 2484-2519) runs exactly that cell.
- Stage H's projected total of 4,092 (line 2528). Superseded by 4,221 at lines 2646-2652; a denominator difference in stdout (1,025 against 1,154), not a disagreement.
- Stage H's prediction that the recipe "plausibly reorders the ladder" (line 2787). Did not happen; the three rungs keep round 3's order (lines 2788-2790).
- Round 3's reading that potion-base-32M against potion-base-8M is "inside the noise" (outside this range). Overturned at lines 2779-2784; see section 7.

## 4. Defects found and fixed

- Collector stdout parser [A]: the ROOT-form rule matched no O2 line, so 99 % of stdout carried no severity key (345 of 3,000,000 captured one), every stdout line went to the replicated storage tier (42 % of all lines); fix is an O2 parser before the ROOT parser and a router rule accepting the uppercase form. Lines 2559-2576.
- Masker regexes [C]: every rule opened with a boundary or lookbehind, so the engine ran the full matcher at every character; moving the first literal in front cuts masking 85 to 87 % with byte-identical output. Lines 2324-2338.
- FLOAT/NUM fold [C]: a naive one-pass fold changed `1.5-3`; the kept implementation carries a flag reproducing the two-pass quirk; first broken line was an O2PDPSuite banner. Lines 2345-2351.
- Frozen recipe not in the repository [C]: it lived only in a gitignored scratch script; now `tools/templating/drainbench.py --recipe`. Lines 2621-2624.
- Double masking in stage I [C]: the fast-masker binding ran a second pass over an already masked line; visible only as stdout 981 against 1,004; corrected before any number was taken. Lines 2660-2672.
- Contaminated stdout cost reading [C]: 12.00 against three fresh readings near 6.61; whole-corpus cost recomputed to 8.33. Lines 2674-2706.
- Source split rule [C]: a dds source is a host, so "drop the largest" would have dropped a host and kept the program it was written to exclude; fix is `--exclude-family dds`, made before any model was scored. Lines 2814-2839.
- Readability metric [C]: literal-token count accepted bare `:` and `=`, so padding scored well by construction; replaced by punctuation-excluded literal tokens and line-weighted words kept. Lines 2206-2221.
- parametrize_numeric_tokens arm [C]: set True on top of the default True, so it never tested anything; re-run properly. Lines 2124-2130.
- PIPLUP file handling [C]: the released code builds a pandas frame of every line; a bench harness drove the online clustering directly. Lines 2017-2023.
- KELP [C]: three source patches (unwrap on None after 25,011 lines, the same in valid_root, a panic on an unseen token) and no masking. Lines 2102-2110.
- Rig, self-inflicted [C]: two containers at once on disjoint cpusets inflated cost 2.8× (repeated once); a masking probe run over the first 5,000,000 lines, all InfoLogger, so its rules never fired. Lines 2228-2236.
- Declared, not fixed [C]: the corpus builder did not join indented continuation lines that production joins; 7,912 of 3,000,000 stdout lines and 12 of 43,972 dds lines. Lines 2590-2595.

## 5. Decisions this range settles

- Drain3 stays as the template parser [A]. PIPLUP costs 2.56× and 2.24× against a ±25 % gate and holds 7.8× the memory; ten shelf parsers all lose on cost, streaming or both; accuracy is unknowable without ground truth. Lines 1985-1992, 2005-2009, 2059-2069, 2071-2094.
- One recipe per family instead of one configuration [A]. The three families want three different padded-separator sets (88.3 % / 78.6 % / 75.5 % words kept at their own best). Lines 2244-2266.
- Depth 8 [A]. Cost moves 0.6 % from depth 4 to 12; words kept 94.2 % → 97.6 % → 99.7 %; knee at 6. Lines 2180-2194.
- Similarity 0.5 on dds only [A]. Depth cannot help 300-token lines; 88.1 % words at the lowest dds cost. Lines 2195-2198.
- Numeric tokens parametrised for InfoLogger and stdout, kept for dds [A]. Free in processor time; dds gains 88.1 % → 92.0 %. Lines 2129-2141.
- drain_max_children stays at 100 [C]. 500 costs 46.41 against 21.48 on stdout. Lines 2144-2148.
- Strip the clock from stdout and dds before mining, capture it as a string, not as a time key [A]. 21.48 → 16.70 core-s/M; a time key would stamp replayed June lines with today's date. Lines 2174-2189, 2578-2579.
- Keep `<FLOAT>` and `<NUM>` apart with the four-line merge patch [A]. 6.3 % → 1.0 % of stdout lines on a wildcard template. Lines 2199-2203.
- Adopt the rewritten masker [A]. 85 to 87 % off masking, 0 differences on 9,000,000 lines and identical template sets. Lines 2312-2322, 2353-2361, 2553-2555.
- Templating code lives beside the bench, not in deployment [C]. No role, service or image mined templates at round 6; the fold cannot be configuration. Lines 2535-2551. See section 7 for the later state.
- Fix the stdout collector rule before anything ships [A]. Lines 2557-2576.
- The template count is open-ended and driven by runs and partitions sampled, not lines [A]. 3,011 is a floor; 4,092 on a fraction of the archive; 4,221 as one carried tree. Lines 2468-2472, 2521-2533, 2632-2652.
- Embedding cost is never a reason to keep the tree shallow [A]. 0.07 % and 0.06 % of one mining pass. Lines 2301-2304, 2654-2658.
- Rank embedding models on paired differences over shared replicates, never on absolute purities [A]. Marginal widths 0.065 to 0.076 against a 0.03 gate; paired intervals all exclude zero. Lines 2715-2777.
- potion-base-32M is ahead of potion-base-8M, small but real [A]. +0.010, 399 of 400 replicates. Lines 2779-2784.
- Exclude dds from the evaluation pool; keep it in training text [C]. Lines 2837-2839.
- Read every purity against the null of its own set [A]. Full set 0.0195; dev 0.198; held-out 0.197. Lines 2841-2861.
- Read every query score against the 0.645 ceiling and quote the unjudged-pair count beside any post-trained score [A]. Lines 2919-2943.
- Negative results, tuning that can be skipped [C]: PIPLUP preprocessing on Drain3 (lines 2118-2121); hand-written semantic masks (2122-2123); dropping PATH or FLOAT (2142-2143); deleting separators (2153-2156); a severity mask (2157-2158); a value-after-= rule (2159-2162); a flag-name mask (2163-2165); hash and mixed-case masks (2166-2167); a digit pre-check (2367-2368); the `regex` module (2365-2367).

## 6. Not measured, not tested, or unmeasured

- "No ground truth, so no accuracy number for any parser, ours included." Line 2603. Also: "None of them was reproduced here, and none of them can be." Line 2067.
- "No journald anywhere in the corpus. The claim that Drain handles multiline kernel traces badly ... is reasoning, not a measurement." Lines 2604-2606.
- "The whole stage ran in one Colima VM pinned to four processors, on a laptop." Line 2607.
- "Fetching InfoLogger across partitions is the obvious next measurement and it has not been run." Lines 2481-2482. Superseded by lines 2484-2519 inside the range.
- "The stdout cost is not a like-for-like comparison and 6.29 must not be quoted against 12.00." Lines 2438-2439.
- "The cell cannot separate the two." (retention 81.5 % against 74.0 %). Line 2513.
- "a control reading of 19.91 for the shipped configuration that I did not re-measure, so I will not quote a new multiple. The absolute is 8.33 and the control is unverified." Lines 2711-2713.
- "A person did not judge these. I did." ... "it loses the claim to human ground truth, and the file should be re-judged by a shifter before any of it is quoted outside this document." Lines 2881-2886.
- "The dump is incomplete." 264 of 2,444 PIPLUP strings absent. Lines 2052-2055.
- "This round does not have the evidence to choose between them, and does not guess" (the burst gap). Lines 2959-2960.
- "Whether an EPN worker's files carry InfoLogger content is a question for Lubos and no soak run settles it." Lines 2984-2985.
- "The derivation here is InfoLogger only. DDS and stdout need the run tarballs read ... so it waits for an idle rig." Lines 2989-2991.
- "`epn146` and `epn323` are unsurveyed. Memory, cores, and what else runs there." Lines 2993-2995.
- From the standing warning: "One micro-benchmark on `epn228` would give the conversion factor. It has not been run." Lines 288-289.
- No post-trained model is scored anywhere in this range; stage I ends at the query set and its ceiling. Lines 2609-2943.

## 7. Cross-range notes

- Round 3 and docs/EMBEDDING_RESULTS.md (outside range): lines 2753-2757 state every three-decimal macro purity there is quoted past what the measurement supports, and the 0.027 that decided round 3 is inside the error bar. Lines 2779-2784 overturn round 3's "inside the noise" verdict on 32M against 8M and note round 3 chose the 8M model as post-training base on that reading. The range does not state a new base choice; the reconciler must check later rounds.
- Round 3's ladder was measured on 3,822 templates of the old configuration (lines 2417-2420); stage I's set is 4,220 with a different null (lines 2801-2812). Any round 3 absolute number must not be placed beside a stage I number.
- Round 4 (outside range): its dds corpus held 267,607 lines against 43,972 here (lines 2599-2602, 2293-2296). Every dds cost in this range rests on the smaller set, and dds is under-weighted at 0.1 % against operations reporting it as the highest-volume family.
- Stage B (outside range): "Where we tap InfoLogger above" is the reference for open question 2 (line 2982). "See the floor caveat above" for open question 1 (line 2973) also points outside the range.
- docs/SOAK_PLAN.md (outside file): the time-ordered split question is what the 81.5 % → 74.0 % retention signal may bear on (lines 2510-2513).
- deploy/README.md (outside file): sizes the replicated storage tier as low-volume; the stdout severity defect sent 42 % of all lines there (lines 2569-2571).
- Live shifter lane (architecture, outside range): the lane matches `^(infologger|family\.central)$`; after the stdout fix it will not see stdout INFO at all. The range leaves this to decide separately (lines 2581-2584). The reconciler must check whether the September review's live-lane message bus decision (a separate decision from the soak's Kafka rejection at lines 1852 onward, outside range) resolves it.
- Lines 2535-2547 say no role, service or image mines templates and the code is a bench. The report describes the system after the September rework; the reconciler must check later rounds for the in-band stamper and templating work, which post-dates this range.
- Rounds 9, 10 and 11 (outside range) are reviews that corrected earlier rounds; the reconciler must check whether they touch stage H or I numbers, in particular 8.33 core-s/M, 4,221 templates and the query ceiling.
- Round 18 (outside range) re-times the templating pipeline after later optimisation; any cost quoted from this range should be checked against it.
- The standing warning (lines 263-306) applies to every core-second here; the epn228 conversion factor is unmeasured.

## 8. Story

- The parser question closed on cost, not on accuracy. PIPLUP costs 2.56 times Drain3 on the whole corpus and holds 7.8 times the memory; no parser's accuracy can be checked because ALICE has no ground truth.
- The real gain was configuration. One recipe per family lifted words kept on stdout from 94.2 % to 99.7 % and cut contentless InfoLogger templates from 44.9 % to 10.4 %, at the same price (17.65 against 17.57 core-seconds per million).
- A human reading forty samples caught the metric that flattered padding; the metric said 88.8 % of lines improved and the human said the opposite.
- The masker was 74 to 87 % of mining cost. Moving one literal in front of each regex cut masking by 85 to 87 % with zero differences on nine million lines.
- The whole-corpus cost of record is 8.33 core-seconds per million lines on 45,596,613 lines; the earlier 11.18 rested on one contaminated stdout reading.
- Templates are a curve, not a number. One run of each family gives 3,011; sixteen runs and eighty partitions give 4,221 and every family is still climbing, so the design must plan for an open-ended set.
- The audit found the collector defect that matters most: only 345 of 3,000,000 stdout lines carried a severity, so 42 % of all lines went to the tier the design keeps small; the fix cuts that share about 32-fold.
- The embedding ladder's absolute purities are noise (±0.037 against a gate of 0.03), but paired rankings hold in 392 to 399 of 400 replicates; the query set caps any score at 0.645 and was judged by the author, not a shifter.
