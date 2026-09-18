# Round 1, lens: reader test judge

Twelve questions were put to a fresh reader who saw only the report text
(`reviews/reader-questions.md`, answers in `reviews/round1-reader-answers.md`).
Each answer was checked against the briefs and, where a brief cites it, the code.

Paths: section and figure paths are relative to `reports/loggy-report/`. Brief paths are
relative to the same directory. `reports/inputs/...` is repository-relative.

## Verdict per question

| Q | Answer | Fault |
| --- | --- | --- |
| 1 info line | partly wrong: "If the EPN dies, nobody can read it" | the report (M2) |
| 2 dead collector | correct, but could not make the bound add up | the report (M3) |
| 3 queue rejected | correct | none |
| 4 two Kafka decisions | correct | none |
| 5 what a worker gives | partly: no memory figure for the stack | the report (M8) |
| 6 flush interval | correct | none |
| 7 template and stamper | correct | none |
| 8 built against agreed | correct, assembled from nine sections | the report (S15) |
| 9 0.685 | correct; took a development number for the standing one | the report (S8) |
| 10 laptop figures | partly: the fixing measurement is implied, never stated | the report (M6) |
| 11 six objectives | correct | none |
| 12 detector, monitor, episode, notification | correct except "17 detectors, one per entity" | the report (S14) |

Eight findings are must, seventeen should, three nit. Appendix B's empty body is left to the
build lens, which already records it.

## must

1. **The error line crosses the wire twice, not once.** `sections/02-design-a.tex:33`
   "An error line crosses the wire once and is copied three times before a person reads it".
   Step 6 of the same list posts the record to the shifter view over HTTP, and Figure 3 labels
   that edge "second copy". Source: `reports/inputs/dataflow-atlas.md:19` (audited walkthrough 1,
   "The second copy goes to the live lane ... posted over HTTP to the live lane inside the Shifter
   on node-05"). Fix: "An error line crosses the wire twice: once to the storage tier, where it is
   copied three times, and once to the live view a person watches."

2. **Section 3.3 never says who reads a local info line, or what survives a dead worker.**
   `sections/02-design-a.tex:49` "An info line takes the same road as far as the routing rule, then
   stays on its worker." The reader concluded that a dead EPN takes its info lines beyond reach.
   Source: `reports/inputs/dataflow-atlas.md:29` (audited): the trend rollup reads it every 10
   minutes, six local detectors read it every minute, the shifter view samples lines from it, and
   "If a worker dies, its raw info lines die with it ... What survives is everything derived from
   them, the rollup rows, the template counts and the detector results". Fix: add two sentences,
   who reads the local index, and that the derived rows survive the machine. The outline also asks
   for the fanout sentence here (`outline.md:72`).

3. **The death-to-document arithmetic double-counts the last heartbeat.**
   `sections/03-design-b.tex:19` "The last sample lands up to 30~seconds before death." The reader
   summed the listed terms to 300 seconds against the stated 270. Source:
   `briefs/walkthroughs-3-5.md:63-68`: the first fleet document with the missing flag lands 60 to
   150 seconds **after death**, which already contains the 0-to-30-second gap and the 90-second
   grace. Fix: drop that sentence and write "Counted from the death: the grace and the poll give
   the first missing flag after 60 to 150 seconds, the monitor adds up to 60, the projector up to
   30, and the page wait 30."

4. **Figure 6 prints the whole monitor count inside the threshold lane.**
   `figures/fig06-alerts.svg:109` "30 monitors, every minute or ten". The other labels give nine
   trend rules, 14 minute monitors and two break-glass monitors, which is 25, so no reader can
   reconcile the figure with Section 3.6's 30. Source: `reports/inputs/dataflow-atlas.md:211-216`:
   15 monitors every minute (two of them break-glass), 13 every ten minutes (nine of them the
   trend comparisons), two every hour. Fix: label the threshold box "17 monitors, 13 every minute
   and 4 every ten", the trend arrow "9 comparisons, every 10 min", and add the same split to
   Section 3.6 so 9 + 17 + 2 hourly + 2 break-glass = 30.

5. **Figure 4 gives 128 MB as the documented worst case.** `figures/fig04-collector.svg:113`
   `<text x="800" y="466">128 MB</text>`, against the label "Documented worst case" on line 106.
   Section 3.4 says the documented worst case is roughly 307 MB. Source:
   `briefs/target-architecture.md:153-154`: 64 chunks are about 128 MB; the documented worst case
   is 128 MB x 2 x 1.2, roughly 307 MB. Fix: "Chunks in memory 64, about 128 MB" and "Documented
   worst case 307 MB".

6. **Section 6.1 never names the measurement that would convert laptop figures to the farm.**
   `sections/07-limits-close.tex:7` "Every cost figure in Section~\ref{sec:eval} comes from a
   laptop, and the conversion to a farm worker was never measured." The reader could only infer
   one. Source: `briefs/soak-1.md:224` quoting the soak: "One micro-benchmark on `epn228` would
   give the conversion factor. It has not been run." Fix: add that sentence.

7. **Section 4 quotes a withdrawn templating figure.** `sections/04-why.tex:50` "The pipeline fell
   from 19.91 to 11.18 core-seconds per million lines." Source: `briefs/soak-3.md:108` "Whole-corpus
   cost 11.18 core-s/M ... Withdrawn at lines 2693-2704; the figure of record is 8.33", and
   `briefs/soak-3.md:107`: the 19.91 control was never re-measured, so no multiple may be quoted.
   Fix: "Mining the whole corpus now costs 8.33 core-seconds per million lines, processor time with
   one core busy for one second." That also defines core-seconds at its first use, five pages
   before Section 5.1 defines it.

8. **The report never says how much memory the platform takes on a worker.**
   `sections/02-design-a.tex:7` "A local OpenSearch data node holds one index per worker on four of
   the EPN's 128~cores, and little memory." The processor half of question 5 is answered, the
   memory half is not: the reader had to assemble 384 MB, 1 GB and "little memory" from four
   sections and still gave no total. Sources: `briefs/constraints.md:182` (collector envelope, hard
   memory caps on the farm nodes), `briefs/target-architecture.md:154` (384 MB service ceiling),
   `briefs/target-architecture.md:157` (1 GB heap for the worker's store node, a measured choice).
   Fix: one sentence here, "The platform is given four of the 128 logical processors, a 384 MB cap
   on the collector and a 1 GB heap on the local store node. The stamper's memory is not measured."

## should

9. **"Soak" is never defined.** `sections/01-intro-problem.tex:44` "The soak tested one collector at
   1,000, 20,000 and 50,000 records a second per worker." The word then carries Sections 5.3 and the
   Appendix B title. Source: `briefs/style.md:31` (define a term the moment it appears) and
   `briefs/soak-index.md:13` (the soak is the measurement campaign, in numbered rounds). Fix: "The
   soak, the project's measurement campaign of numbered rounds, tested ...".

10. **"Chunk" is never defined.** `sections/02-design-a.tex:40` "The stamper, over a local socket,
    sets a template version and status and hands the chunk back." The memory envelope then bounds
    the collector in chunks. Source: `briefs/style.md:31`; `briefs/target-architecture.md:153`
    (chunks in memory, 64, about 128 MB). Fix: at first use, "a chunk, the block of records the
    collector moves and buffers as one unit".

11. **"Shifter" is never defined.** `sections/02-design-a.tex:43` "The collector also posts the
    record to the shifter view's live lane, which holds the last 500 records." The shifter view,
    the shifter host and the shifter's pages then run through the report. Source:
    `reports/inputs/dataflow-atlas.md:477` ("The console a person on shift watches");
    `briefs/style.md:31`. Fix: "the shifter view, the page a person on shift watches".

12. **The 75.32 and 11.19 figures do not say what they measure.** `sections/05-eval-a.tex:11` "Cost
    per record falls as rate rises: 75.32 core-seconds per million at 1,000 a second and 11.19 at
    20,000." The reader could not tell whether this is the collector or the whole stack. Source:
    `briefs/soak-1.md:84-85`: both are the collector alone, three control runs at each rate. Fix:
    "the collector alone costs 75.32 ... and 11.19 ...".

13. **Two collector memory ranges are never reconciled.** `sections/05-eval-a.tex:17` "The
    collector's memory sits between 133 and 228~MB on two cores against an accepting sink", against
    Table 6's collector peak of 87.1 and 62.7 MB. Sources: `briefs/soak-index.md:59` (133 to 228 MB
    is Round 1's working range, collector alone against a fake sink) and `briefs/soak-1.md:21-22`
    (87.1 and 62.7 MB are the flush arms at 5,000 records a second on the re-run rig). Fix: name
    Round 1 and its rate in the sentence, so the table reads as a different rig and rate.

14. **Five families, six sources and six table rows never meet.** `sections/05-eval-a.tex:58` "The
    collector had never read three of the six log sources", against "five log families" in Section
    2.1 and "reads the five log sources" in Section 3.1. "The data distribution log" named on the
    next line appears nowhere in Table 1. Sources: `briefs/constraints.md:85-96` (five families; the
    routing records list seven sources because the process tree splits into DPL and data
    distribution and the orchestrator is added) and `briefs/soak-4.md:216` (Round 7 added datadist,
    ildaemon and journald). Fix: one sentence in Section 2.1 bridging families to sources, and a
    Table 1 row or note that the process tree is two sources.

15. **Five template counts, no bridge.** `sections/06-eval-b.tex:11` "One run per family gave 3,011
    templates. One tree per family across three corpora gave 4,221." The reader also met 3,822 in
    Section 5.5, 4,092 in Table 10 and 5,301 in Section 5.6. Sources: `briefs/soak-3.md:157` and
    `:205` (the count is a curve driven by the runs and partitions sampled, not by lines; 3,011 is a
    floor) and `briefs/semantic-2.md:35` (5,301 is the later semantic corpus). Fix: add "The count
    is a curve, not a number: it grows with the runs and partitions sampled, not with lines", and
    mark 5,301 as the larger corpus the search was built on.

16. **0.685 is a development-set score, and the report does not say so.**
    `sections/06-eval-b.tex:36` "It scores 0.685 against 0.634 for the dense model alone, with an
    interval of +0.028 to +0.080." Source: `briefs/semantic-1.md:83` ("development, interval +0.028
    to +0.080 ... reranking is real on development, untested on held-out") and
    `briefs/semantic-1.md:52` (97 intent groups, 130 queries). Fix: "On the development set of 130
    questions over 97 intent groups it scores 0.685 ... Reranking was never scored on the sealed
    held-out set."

17. **The Puppet sentence reads as a recommendation.** `sections/04-why.tex:65` "Puppet wants an
    agent and a certificate on every machine, runs on a timer, and is right for the farm." The
    reader asked whether the report recommends Puppet for the farm it is building for. Source:
    `briefs/why.md:97` "Likely right for the farm, wrong for five machines one person deploys by
    hand." Fix: keep both halves, "likely right for the farm, wrong for five machines one person
    deploys by hand".

18. **"The plan's burst figure" has no antecedent and no explanation.**
    `sections/07-limits-close.tex:11` "The archive's busiest worker-second is 78~records, the plan's
    burst figure 10,000 to 20,000, and that gap is unsettled." Sources: `briefs/soak-1.md:64` ("The
    plan's rates are farm rates that were read as worker rates") and `briefs/soak-3.md:101-102` (the
    band matches the farm-wide peak of 9,781, so per worker it is about one three-hundredth). Fix:
    name it as the soak plan's assumed per-worker burst and give the likely explanation.

19. **"The rework" is never introduced.** `sections/04-why.tex:62` "The rework agreed a bus for the
    live lane only, for decoupling and not durability, and it is not built." Source:
    `briefs/target-architecture.md:144` "The September 2026 review separately agreed a bus for the
    live lane only". Fix: "A design review in September 2026 agreed a bus ...".

20. **Three ceiling numbers, no ranking.** `sections/07-limits-close.tex:83` "1 & Collector alone:
    53,000 a second, two cores." Section 6.1 says the collector's ceiling above 50,000 is unknown,
    and Section 5.2 gives the stack 42,000. Sources: `briefs/soak-index.md:13` (Round 1's 53,000 is
    a read ceiling against a sink that always accepts) and `briefs/soak-1.md:209` ("This document
    states no collector ceiling number ... nothing ran above 50,000"). Fix: in the table, "Collector
    alone against a sink that always accepts: about 53,000 a second, two cores", and in Section 6.1
    say that figure is superseded because nothing has run above 50,000 with a real store.

21. **The farm's size is never stated, and three numbers stand in for it.**
    `sections/04-why.tex:38` "Cross-cluster search does not scale to 100 machines." Section 4 also
    uses "Three hundred data nodes" and "more than 200 workers", Section 3.1 "200 collector
    configurations". Source: `briefs/constraints.md:307`: the archive shows 308 worker hosts, 297
    active in the busiest hour, and no source gives a current head count. Fix: state the size once
    in Section 2, then use one number.

22. **"17 detectors run, one per entity" says the wrong thing.** `sections/03-design-b.tex:5`. The
    reader repeated it as seventeen entities. Source: `reports/inputs/dataflow-atlas.md:193-194`:
    fourteen log detectors read the three log families per origin host or per collector, three read
    the health samples, and each detector fits one model per entity. Fix: "17 detectors run,
    fourteen over the log families and three over the health samples. Each fits one model per
    machine, and a silent host scores zero."

23. **Only one of the three storage hosts is introduced in prose.**
    `sections/02-design-a.tex:11` "The control host runs Dashboards, the poller, the roster, the
    catalog maintenance, Alertmanager and the receiver." The background host and the shifter host
    appear only inside figures, so the projector, the trend rollup and the shifter view are never
    placed. Source: `briefs/target-architecture.md:63-75`. Fix: add one sentence naming the second
    host (the projector and the trend rollup) and the third (the shifter view).

24. **Three different sets of severities stay local.** `sections/00-abstract.tex:1` "Info lines stay
    in one local copy, every other severity goes to three storage machines", against Table 3 ("info
    and debug lines") and Section 3.2 step 2 ("info, debug and trace go local"). Source:
    `reports/inputs/dataflow-atlas.md:16` (audited): "Info, debug and trace would go to
    family.local." Fix: use "info, debug and trace" in Table 3 and the abstract, or say "routine
    lines" once and define it.

25. **The live-lane limit reads as a contradiction of Section 3.9.**
    `sections/07-limits-close.tex:29` "The live lane matches InfoLogger and the central family, so
    after the severity fix of Section~\ref{sec:eval-sources} it will not see stdout info lines."
    Section 3.9 says the live page keeps the severe lines, so a reader takes this as the design
    working. Source: `reports/inputs/dataflow-atlas.md:30` (an info line is never posted to the live
    lane, by design) with `sections/05-eval-a.tex:58` (before the fix, 41.8 % of the corpus reached
    the storage tier with no severity). Fix: say what changes, "Before the severity fix those
    stdout lines carried no severity and went central, so the live page showed them. They are now
    info and stay on the worker, and the live page shows less than a person may remember."

26. **No single place says what is built.** `sections/07-limits-close.tex:39` "First, the live-lane
    bus, agreed and not built ...". The reader assembled the built list from nine sections and
    marked the answer "partly for completeness". Fix: give Figure 7 a status column in the caption,
    or open Section 6.3 with one sentence listing the built set: collector, stamper, local node,
    storage tier, Dashboards, poller, projector, receiver, Alertmanager, shifter view, rollup, three
    detection lanes and the template chain, all on staging. Source for the set and for what may not
    be claimed on the farm: `briefs/target-architecture.md:215` ("Nothing read says whether the
    stamper, the poller, the projector, the rollup, Alertmanager, the receiver or the shifter view
    ran on the farm. Say 'not stated' rather than 'deployed'.").

## nit

27. **The monitor on detector grades is never introduced.** `sections/03-design-b.tex:7` "A detector
    grade above 0.5 opens an episode, the paging monitor on grades needs 0.7." Source:
    `briefs/walkthroughs-6-9.md:43`: one monitor runs every minute over the last two minutes of
    results and opens one fleet-wide alert when the highest grade and its confidence are above 0.7.
    Fix: "one monitor watches every grade and pages once for the fleet above 0.7".

28. **Table 7's masking row can be read two ways.** `sections/06-eval-b.tex:24` "Masking step removed
    by the rewrite & 85 to 87\,\%". Source: `briefs/soak-3.md:203`: the rewrite cut the masking step
    by 85 to 87 %, and masking was 74 to 87 % of mining. Fix: "Share of the masking step the rewrite
    removed".

29. **"Knees" is never defined.** `sections/00-abstract.tex:1` "so only shapes, knees and rankings
    transfer to the farm". Source: `briefs/style.md:31`. Fix: "the rates where a cost curve bends"
    at the first use in Section 5.
