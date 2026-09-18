# Round 1 review — lens: numbers

Scope: every number in `sections/00-abstract.tex` and the eight body parts, including
table cells and captions, traced to `docs/SOAK.md`, `docs/SOAK_RESULTS.md` (retractions
at 11–203, standing warning at 263–306), `docs/TEMPLATING_RESULTS.md`,
`docs/EMBEDDING_RESULTS.md`, `docs/SEMANTIC_RESULTS.md` (13–143), and the briefs
`soak-index.md`, `templating-embedding.md`, `semantic-2.md`,
`target-architecture.md`, `walkthroughs-3-5.md`, `walkthroughs-6-9.md`,
`constraints.md`, `why.md`.

Verified and correct (no finding): the flush table (108.40 / 104.67 / −3.4 %,
36.70 / 27.70 / −24.5 %, 87.1 / 62.7 / −28.0 %, all recomputed); 17.9 % across the
eight-value flush curve; 1.61 % and 9.48 % noise floors; 75.32 and 11.19 core-seconds
per million; 89.3 % main-loop share; threading arms 7.4 / 11.5 / 26.5 %; live lane
41–68 %; 66.7 % InfoLogger lost in the fifteen-minute outage; 865,674 records of
310 bytes, 13.8 and 46.8 a second, 17.4 and 5.1 hours (recomputed); 42,000 sustained,
50,000 for two minutes, 86 % of four cores, six million documents, 82 % of cap,
1.8 % discarded, 263 s drain; 2.1 % / 6.7 % heap and 2.7 % / 5.5 % placement floors;
538×, 4.3×, 54× (recomputed); 23, 78, 9,781, 248,828,513, 312 hosts, 1,000 a minute;
2,500–10,500 journal entries; 41.8 %, 99.83 %, 19.3 %, 5 of 5, 99 defects, 32 rig
defects, 47-cell re-run; 3,822 at 19.91, 2.56×, 7.8×, 74–87 %, 85–87 %, 44.9→10.4 %,
3,011, 4,221, 48.9 per million, 20,000 templates, 208.1 MB of 512 MB, 15 %, 6.5 %,
32 %; 18,037, 31×, 93 %, 0.636, 5.4×, 0.387 / 0.414 / 0.035, 47.1 %, 5,301, 0.685 /
0.634 / +0.028–0.080, 0.689 / 0.371 / +0.318, 0.020, 0.048, 1 ms → 12 ms; all alerting
cadences (30 s, 90 s, 10 min, 5 min, 30 s page wait, 0.5 and 0.7 grades, 22 edges,
30 monitors, 17 detectors, 9 trend rules, 2× / 0.5×, 50 records, 6 baseline buckets,
40 minutes, 120 s settle, 5,000 hourly buckets); index retentions 8 / 35 / 56 / 4 / 66 /
7 days; 64 chunks, 128 MB, 307 MB, 384 MB, 373 MB, 61 s; 500 live records, 20,000 rows;
14 TB at 93 %, 45,596,613 lines, 186 programs, 4.0.1 / 3.4.0 / 3.2.2.

## Must

1. **`sections/04-why.tex:50` quotes a retracted templating cost.**
   Quote: `The pipeline fell from 19.91 to 11.18 core-seconds per million lines.`
   The 11.18 whole-corpus figure was withdrawn: "the published 12.00 is a contaminated
   reading and the whole-corpus cost of 11.18 cannot stand … 8.33 core-seconds per
   million, not 11.18" (`docs/SOAK_RESULTS.md:2693-2704`;
   `briefs/soak-index.md` section 3, retracted table). The same line retires the 1.8×
   multiple, because the 19.91 control was never re-measured
   (`docs/SOAK_RESULTS.md:2708-2713`).
   Fix: "The whole-corpus mining cost is 8.33 core-seconds per million lines with the
   rewritten masker; the shipped control of 19.91 was not re-measured, so no multiple is
   quoted." `briefs/why.md:166` carries the retracted pair and must be corrected too.

2. **96.94 % is the process tree's share, not every line's.**
   Quote (`sections/02-design-a.tex:49`): `On staging the local tier held 96.94~\% of all lines.`
   Source: "**96.94 % of the process tree stays on the node and 3.06 % crosses the
   network**" (`docs/SOAK_RESULTS.md:3115-3116`; `briefs/soak-index.md` 2k). It is a
   fixture measurement on the archive corpus, not a staging deployment figure, and no
   all-lines share exists in the sources.
   Fix: "The process-tree family sends 96.94 % of its lines to the local index and
   3.06 % across the wire." The same wrong denominator appears at
   `sections/04-why.tex:41` (`The local tier holds 96.94~\% of lines.`) and
   `sections/07-limits-close.tex:93` (`96.94~\% of lines stay local.`).

3. **The templating headline is arithmetically wrong.**
   Quote (`sections/06-eval-b.tex:3`): `Templating is built, at a third to a half of its former cost per line.`
   Round 18 is 31 to 57 % cheaper per family (`docs/SOAK_RESULTS.md:5428-5429`), so the
   new cost is 43 % to 69 % of the old, not 33 % to 50 %.
   Fix: "Templating is built, at between two fifths and seven tenths of its former cost
   per line" — or simply state the 31 to 57 % range, as the rest of the subsection does.

4. **Four of 128 mixes physical cores with logical processors.**
   Quote (`sections/01-intro-problem.tex:50`): `A worker gives the platform four of its 128 logical processors and little memory.`
   The soak states the opposite and marks the correction: "**The worker's budget is
   4 *physical* cores, so 8 logical processors.** … This is the opposite of what an
   earlier draft of this document said" (`docs/SOAK_RESULTS.md:275-281`;
   `briefs/constraints.md:181`, and `:24` for the 128 being logical processors).
   Fix: "four of its 64 physical cores, eight of its 128 logical processors". The same
   conflation is at `sections/02-design-a.tex:7` (`four of the EPN's 128~cores`) and in
   Table 2, `sections/01-intro-problem.tex:71` (`Four logical processors of 128`).

5. **DDS's 0.1 % is its share of the corpus, not of its own lines.**
   Quote (`sections/01-intro-problem.tex:36`): `DDS is under-sampled at 0.1~\% of its lines, and operations call it the largest family during data-taking.`
   Source: "Weights are this corpus: InfoLogger 58.1 %, stdout 41.8 %, dds 0.1 %"
   (`docs/SOAK_RESULTS.md:2292`); 43,972 DDS lines of 45,596,613
   (`docs/SOAK_RESULTS.md:2290-2296`; `briefs/templating-embedding.md:208`). The report
   states it correctly at `sections/07-limits-close.tex:31`.
   Fix: "DDS is 43,972 lines, 0.1 % of the corpus, and operations call it the largest
   family during data-taking."

6. **Three of the four farm collector installations were tested.**
   Quote (`sections/05-eval-a.tex:60`): `Four collector releases run there, none tested.`
   The census table marks epn146, epn228 and epn323 "tested before today: yes" and only
   epn-infra13's 3.2.8 "no"; the four installations carry three distinct releases
   (`docs/SOAK_RESULTS.md:3517-3527`; `briefs/constraints.md:290`).
   Fix: "Four installations run there and the storage machine's, two majors behind, had
   never been tested."

## Should

7. **An abstract headline the body never states.**
   Quote (`sections/00-abstract.tex:1`): `The collector costs about a quarter of one core at 20,000 records a second.`
   The figure is real — "The collector uses **0.25 of one core** at 20,000 records a
   second" (`docs/SOAK_RESULTS.md:559-563`; `briefs/soak-index.md` 2a) — but no body
   section gives it; Section 5.2 gives only core-seconds per million.
   Fix: add one sentence to Section 5.2: "At 20,000 records a second that is 0.25 of one
   core, and one thread carries 0.223 of it."

8. **The 20,000-template ceiling is described two contradictory ways.**
   Quote (`sections/02-design-a.tex:71`): `The stamper holds at most 20,000 clusters per worker and drops the least recently used.`
   Against `sections/06-eval-b.tex:11`: "The tree stops learning at 20,000 templates".
   Both are sourced but to different components: LRU eviction in the stamper
   (`briefs/walkthroughs-6-9.md:67,83`) and eviction deliberately refused in the catalog
   tree, "the tree stops learning at 20,000 templates and keeps counting; LRU eviction is
   not used" (`docs/SOAK_RESULTS.md:5124-5136`). After the rework the report presents one
   component, so the reader meets a contradiction.
   Fix: name which ceiling belongs to which path, or state the shipped behaviour once.

9. **The recipe's two costs are the wrong way round.**
   Quote (`sections/06-eval-b.tex:9`): `One per family costs the same as one global setting, 17.65 against 17.57~core-seconds per million weighted.`
   The table reads 17.65 for the shipped global setting and 17.57 for the per-family
   recipe (`docs/SOAK_RESULTS.md:2275`; `briefs/templating-embedding.md:179`), so the
   subject of the sentence carries the comparator's number.
   Fix: "One per family costs the same as one global setting, 17.57 against 17.65
   core-seconds per million weighted."

10. **133 to 228 MB is called a peak here and a steady range elsewhere.**
    Quote (`sections/04-why.tex:6`): `Under load ours peaked between 133 and 228~MB.`
    `sections/02-design-a.tex:59` and `sections/05-eval-a.tex:17` use the same range as
    the steady load and give 373 MB as the measured peak, which matches
    `docs/SOAK.md:18` (working range) and `docs/SOAK.md:87-90` (373 MB sink-outage peak).
    Fix: "Under steady load ours sat between 133 and 228 MB."

11. **The listed cadences sum to 300 seconds, not the stated 270.**
    Quote (`sections/03-design-b.tex:19`): `The last sample lands up to 30~seconds before death. The 90-second grace and 30-second poll add 60 to 150~seconds, the monitor up to 60, the projector up to 30, the page wait 30.`
    The derivation measures from death, and the last heartbeat is not a term in it:
    60–150 + 0–60 + 0–30 + 30 = 90 to 270 (`briefs/walkthroughs-3-5.md:59-68`).
    Fix: drop the last-sample clause from the sum, or mark it explicitly as outside the
    bound: "The bound runs from the death itself; the last sample lands up to 30 seconds
    earlier."

12. **"The configured ceiling is 384 MB" does not say whose.**
    Quote (`sections/06-eval-b.tex:36`): `The configured ceiling is 384~MB.`
    Two different 384 MB values exist, the collector's memory line and the semantic
    model's service ceiling, and the brief warns against conflating them
    (`briefs/target-architecture.md:174,177`). Section 5.2 has just used the other one.
    Fix: "The semantic service is configured with a 384 MB ceiling on the shifter host."

13. **The report counts five, six and seven log families.**
    Quotes: `A worker holds five log families` (`sections/01-intro-problem.tex:17`),
    `The collector had never read three of the six log sources.`
    (`sections/05-eval-a.tex:58`), `The whole-pipeline rewrite made seven families 31 to
    57\,\% cheaper` (`sections/06-eval-b.tex:13`). The six sources are infologger, dds,
    dpl, datadist, ildaemon and journald (`docs/SOAK_RESULTS.md:3014-3021`); the seventh
    priced family is odc (`docs/SOAK_RESULTS.md:5442-5448`), which Table 1 marks "Not
    collected".
    Fix: reconcile once — five families on a worker, split into six parser sources because
    the process tree has two formats, plus the orchestrator log priced but not collected.

14. **The 32-interval warm-up is the injection harness's number, not the detectors'.**
    Quote (`sections/07-limits-close.tex:17`): `Detector warm-up needs about 32 consecutive live intervals, and no run measured its length.`
    The audit found two different figures: the plugin's own guidance is a few hundred
    windows, three to five hours at one minute, while 32 windows is the poison harness's
    warm-up — "The two figures describe different things"
    (`briefs/walkthroughs-6-9.md:27`); `briefs/why.md:178` carries the 32.
    Fix: "A detector needs a few hundred one-minute windows, three to five hours, before
    its grades mean anything; the injection harness waits 32. Neither was measured."

15. **"Processor cost" in the flush sentence does not say whose.**
    Quote (`sections/02-design-a.tex:57`): `Moving the write to the local node from the shipped 5~seconds to 1~second cut processor cost by 24.5~\% and peak memory by 28.0~\%.`
    24.5 % is the collector's own cost; the whole four-core stack moved 3.4 %
    (`docs/SOAK_RESULTS.md:19-27`). The abstract says "the collector's cost" and Table 7
    labels the row; this sentence does not.
    Fix: "cut the collector's own processor cost by 24.5 %".

## Nit

16. **The template count is given as two points, so the reader cannot see a curve.**
    Quote (`sections/06-eval-b.tex:11`): `One run per family gave 3,011 templates. One tree per family across three corpora gave 4,221.`
    The figure of record is a curve of three points over stated corpora: 3,011 on
    45,596,613 lines, 4,092, then 4,221 on 55,963,050 lines
    (`briefs/soak-index.md` 2j; `docs/SOAK_RESULTS.md:2468, 2523-2533, 2632-2652`).
    Fix: give the middle point and the two line counts, as the appendix already does.

No further findings at any severity.
