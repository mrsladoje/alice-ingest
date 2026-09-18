# Round 2, lens: reader test judge

Twelve questions (`reviews/reader-questions.md`) were put to a fresh reader who saw only the
report text. The answers are in `reviews/round2-reader-answers.md`. Each answer was checked
against the briefs, and against the audited walkthroughs in `reports/inputs/dataflow-atlas.md`.

Paths: report paths are relative to `reports/loggy-report/`. Brief paths are relative to the same
directory. `reports/inputs/...` is repository-relative.

## Verdict per question, against round 1

| Q | Round 1 | Round 2 | Fault now |
| --- | --- | --- | --- |
| 1 info line | partly wrong (nobody can read it) | correct | none; trace is still missing from Table 3 (S11) |
| 2 dead collector | correct, bound did not add up | correct, bound adds up | the report: the 60-second lower bound now looks smaller than the 90-second rule (S1) |
| 3 queue rejected | correct | correct | none |
| 4 two Kafka decisions | correct | correct | the report: the latency argument cuts both ways (S5), and "The live-lane bus is separate" has no object (N1) |
| 5 what a worker gives | partly, no memory figure | correct, hedged | none; the memory figure landed (round 1 M8 fixed) |
| 6 flush interval | correct | correct | none |
| 7 template and stamper | correct | correct | none |
| 8 built against agreed | correct, hedged | correct, hedged | none; the reader assembled the list unaided |
| 9 0.685 | correct, took development for standing | correct, names the development set | none (round 1 S16 fixed) |
| 10 laptop figures | partly, fixing measurement implied | correct, names the micro-benchmark | the report: Section 6.1 then says the cost curve ran at 5,000 only (M2) |
| 11 six objectives | correct | correct | none |
| 12 monitor, detector, episode, notification | correct | partly: episode against incident | the report (S3) |

Improved: 1, 2, 5, 9, 10. Unchanged and still correct: 3, 4, 6, 7, 8, 11. Worse: none.
Question 12 lost ground, because Section 3.6 now defines "incident" as a second thing.

Round 1 musts that are fixed: the wire crossed twice (M1), who reads a local info line (M2), the
death-to-document sum (M3), Figure 6's threshold box (M4), Figure 4's 128 MB worst case (M5), the
named micro-benchmark (M6), the withdrawn 11.18 figure (M7), the worker's memory budget (M8).
No round 1 must is still present in its round 1 form. Two round 1 musts left a smaller residue:
Figure 6's minute count (S2) and Section 6.1's cost-curve sentence (M2 below).

## must

1. **Section 3.1 says the farm's three primaries are set; they are agreed and not applied.**
   `sections/02-design-a.tex:15` "Three primaries are set for the farm and have never carried farm
   volume." Section 4 says the opposite on the same page spread: `sections/04-why.tex:39` "The farm
   runs three primaries, agreed and not applied". Source: `briefs/why.md:71` "The farm value of one
   primary per storage node is agreed, not applied." The reader read both and reported the clash.
   Fix: "Three primaries on the farm are agreed and not applied." Mark the Table 3 cells the same
   way, for example "staging 1 primary, farm 3 agreed".

2. **Section 6.1 says the cost curve was measured at one rate, and Section 5.1 gives two other
   rates.** `sections/07-limits-close.tex:11` "The cost curve was measured at 5,000~records a second
   only." `sections/05-eval-a.tex:11` gives "the collector alone costs 75.32 core-seconds per
   million at 1,000 a second and 11.19 at 20,000". Source: `briefs/soak-index.md:503` "the cost
   curve (core-seconds) was measured at 5,000 only, and the ceiling cells measured rate, not cost"
   reading `briefs/soak-1.md:220`, which limits the claim to the full stack: "The full stack ... has
   never been run above 5,000 a second in steady state". Fix: "The whole stack's cost was measured
   at 5,000 records a second only. The collector alone was also costed at 1,000 and 20,000."

3. **Section 3.1 says the live lane carries every severe record; it carries every record that
   crosses to the storage tier, severe or not.** `sections/02-design-a.tex:13` "The live lane is the
   second copy of every severe record, posted by the collector straight to the shifter view."
   Source: `reports/inputs/dataflow-atlas.md:30` (audited walkthrough 2) "The http output to the
   Shifter matches stamped.infologger, stamped.ildaemon and stamped.family.central only". Every
   InfoLogger record crosses, including info severity, as Table 1 itself says ("Storage, every
   record"). The reader caught the clash with `sections/07-limits-close.tex:27` "The live lane
   carries InfoLogger and the central family only". Fix: "The live lane is a second copy of every
   record that crosses to the storage tier: InfoLogger, the daemon log and the central family."

## should

1. **The dead-collector lower bound reads as smaller than the 90-second rule.**
   `sections/03-design-b.tex:19` "From the death: the grace and the poll give the first missing flag
   after 60 to 150~seconds". Section 3.6 says "A rostered collector silent for 90~seconds is
   absent", so 60 looks impossible. The condensing dropped the clause that reconciles them: the last
   sample may already be 30 seconds old when the collector dies. Source: `briefs/walkthroughs-3-5.md:181`
   (heartbeat every 30 seconds, 90-second grace, poll every 30 seconds) and the round 1 fix at
   `reviews/round1-reader.md` must 3. Fix: "the grace and the poll give the first missing flag 60 to
   150~seconds after the death, because the last sample may already be 30~seconds old."

2. **Figure 6 prints 15 minute-monitors beside a threshold box that says 13.**
   `figures/fig06-alerts.svg:60` "15 monitors, every minute", against
   `figures/fig06-alerts.svg:109` "17 monitors: 13 every minute, 4 every ten". The 15 is right and
   unexplained: it is the 13 threshold monitors plus the two break-glass monitors, which also run
   every minute. Source: `reports/inputs/dataflow-atlas.md:211-216` as quoted in
   `reviews/round1-reader.md` must 4: "15 monitors every minute (two of them break-glass), 13 every
   ten minutes (nine of them the trend comparisons), two every hour". The reader could not
   reconcile the two labels. Fix: label the edge "13 threshold and 2 break-glass monitors, every
   minute".

3. **"Incident" is defined as a second object beside "episode", and they are one thing.**
   `sections/03-design-b.tex:7` "An episode is one trouble on one thing, with a start and an end. An
   incident is the episode document grouping the signals of one alert name on one entity." Source:
   `briefs/walkthroughs-3-5.md:77` "It writes the signal row (263-291) and opens an incident episode"
   and `briefs/target-architecture.md:66` (the projector "decides what is one incident" and writes
   `alice-incidents`). One object, two names. The reader marked question 12 "partly for episode
   versus incident". Fix: "The projector stores each episode as one incident document, so the two
   words name the same thing: the index is `alice-incidents`."

4. **The receiver is said to store what a person was told, three pages before the report says nobody
   is told.** `sections/02-design-a.tex:11` "The receiver stores what a person was told." Section
   3.6 then says `sections/03-design-b.tex:11` "Nothing pages a human today." Source:
   `briefs/target-architecture.md:59` "Notification delivery beyond the stored document is not
   built: nothing e-mails or pages a human today", confirmed at `briefs/walkthroughs-3-5.md:54` "No
   e-mail or pager is wired beyond that document". Fix: "The receiver stores every notification
   Alertmanager sends, the record of what would reach a person. Nothing pages a human today." Then
   Section 3.6 need not repeat it.

5. **The latency argument against the queue also argues against the agreed live-lane bus, and the
   report does not say so.** `sections/05-eval-a.tex:48` "A produce and a consume step raise the
   live lane's 1-second latency floor." Section 4 then agrees a Kafka bus on exactly that lane.
   Source: `briefs/soak-2.md:59` (the floor is set by flush 1, and "a produce and a consume step
   would add to it") and `briefs/why.md:120-123` (the live-lane bus is agreed for decoupling, not
   durability). The reader listed this as a contradiction. Fix: either drop the sentence from
   Section 5.3, whose other two cost points stand on their own, or add one clause in Section 4:
   "The bus adds a produce and a consume step to that 1-second floor, and the review accepted it for
   the decoupling."

6. **"The data-distribution format" reads as DDS, and it is the process tree's second format.**
   `sections/05-eval-a.tex:54` "Later rounds added the data-distribution format, the InfoLogger
   daemon log and the system journal." Source: `briefs/constraints.md:107-110`: the two O2 line
   formats are DPL/FairMQ and DataDistribution (`TfBuilderTask`), while DDS, the Dynamic Deployment
   System, is a separate family (`briefs/constraints.md:87-91`). The reader wrote "data-distribution
   format (Section 5.4), presumably DDS, not linked". Fix: "Later rounds added the second process-tree
   format, written by the data-distribution processes, plus the InfoLogger daemon log and the system
   journal." Name both formats once in Section 2.1 as well.

7. **"All three corpora" has no antecedent, and three corpus sizes are never related.**
   `sections/06-eval-b.tex:11` "One tree across all three corpora, 55,963,050 lines, gave 4,221."
   Section 2.1 gives 45,596,613 lines and Section 5.6 gives 56,628,579. Sources:
   `briefs/soak-4.md:31` (the three corpora split as infologger 33,433,018, dpl 21,200,248, dds
   1,236,971, datadist 92,813, which is the 55,963,050) and `briefs/semantic-1.md:21` (the retrieval
   corpus holds 56,628,579 lines across all seven formats). Fix: one clause, "the three archive
   pulls the recipes were frozen on, 55,963,050 lines in total", and in Section 5.6 "a later pull of
   the same archive".

8. **Two template counts are both attributed to "the archive" with no setting attached.**
   `sections/06-eval-b.tex:5` "The parser we keep, drain3, mined the archive into 3,822 templates at
   19.91~core-seconds per million" against `sections/06-eval-b.tex:11` "One run of each family,
   45,596,613 lines, gave 3,011 templates, a floor." Sources: `briefs/soak-3.md:14` (3,822 is Drain3
   as shipped, one global tree over the whole corpus) and `briefs/soak-3.md:59` (3,011 is the same
   45,596,613 lines with the per-family recipe and the fast masker). The reader listed this as a
   contradiction. Fix: "mined the archive into 3,822 templates with one global tree" and "One run of
   each family with its own recipe, on the same 45,596,613 lines, gave 3,011".

9. **Shard, primary, replica and quorum are never defined, and four tables rest on them.**
   `sections/01-intro-problem.tex:64` "A storage quorum of two of three, and a lost worker loses only
   its own local index", with Table 3's "1 shard, 0 replicas" and Section 4's shard budget. Source:
   `briefs/style.md` (define a term at first use); the reader listed "shard, primary, replica,
   cluster manager, quorum, heap ... never defined". Fix: one sentence in Section 3.1, "An index is
   split into shards. Each shard has one primary copy and any number of replica copies on other
   machines. A quorum is the number of cluster managers that must agree."

10. **The replay is never explained, and every measurement rests on it.** `sections/01-intro-problem.tex:36`
    "The replayed archive holds InfoLogger, DDS and the process tree, and no journal." Figure 7 puts
    a "replay engine" on each staging worker. Source: `briefs/target-architecture.md:37` (the replay engine
    "stands in for a live EPN: writes the same files and sends the same InfoLogger rows a real node
    would", fed from the CERN S3 archive, staging only) and `briefs/constraints.md:125` (the archive
    corpus of 45,596,613 lines). The reader listed "replay, the replayed archive, replay engine ... never
    explained". Fix: one clause at first use, "the archive we replay, a recorded six months of real
    farm logs fed back through the collector at a chosen rate".

11. **Table 3 drops trace from the local index, and the abstract keeps it.** `sections/02-design-a.tex:22`
    "info and debug lines, one index per worker", against `sections/00-abstract.tex:1` "Info, debug
    and trace lines stay in one local copy" and `sections/02-design-a.tex:39` "info, debug and trace
    go local". Source: `reports/inputs/dataflow-atlas.md:16` (audited) "Info, debug and trace would go
    to family.local". The reader listed the three sets as a contradiction, as the round 1 reader did.
    Fix: write "info, debug and trace lines" in Table 3.

12. **"Four exclusive cores to the collector and the store" hides that the storage tier had four of
    its own.** `sections/07-limits-close.tex:7` "The rig pinned four exclusive cores to the collector
    and the store." Section 5.2 then says "The storage tier runs at 86~\% of its four cores".
    Sources: `briefs/soak-1.md:104` (the rig's allocation: cores 0 to 3 worker services, 4 to 7
    storage tier, 8 to 9 generator, 10 to 11 sink and live lane) and `briefs/soak-index.md:106`
    ("storage tier 3.44 of 4 cores (86 %)"). The reader could not tell whether the four are shared or
    each. Fix: "The rig gave the worker's collector and store four exclusive cores, and the storage
    tier four more."

13. **"Every corpus under-samples it" is wrong for the later rounds.** `sections/07-limits-close.tex:29`
    "DDS is the densest family during data-taking and the dearest per line, and every corpus
    under-samples it: 43,972 lines, 0.1~\% of the weight." Sources: `briefs/templating-embedding.md:208`
    (43,972 lines, 0.1 % of the weight, is the recipe runs) and `briefs/soak-4.md:205` (round 7
    re-ran the DDS recipe on 1,236,971 lines). Fix: "the recipe runs under-sample it at 43,972 lines,
    0.1~\% of the weight, and the later runs used 1,236,971."

14. **PIPLUP decides the parser choice and is never described or cited.** `sections/04-why.tex:48`
    "Without ground truth, cost chose the parser: PIPLUP costs 2.56~times drain3 against a gate of
    plus or minus 25~\%." It appears four times and in Table 7, with no expansion and no reference,
    while the outline's reference list includes it (`outline.md:152`). Source: `briefs/why.md:154`
    (what PIPLUP is and every figure against it). Fix: "PIPLUP, another log-template parser," at
    first use, and a reference entry.

## nit

1. **"The live-lane bus is separate" has no object.** `sections/05-eval-a.tex:50`. The reader
   concluded "Section 5.3 states that the live-lane bus is separate from the four reopen conditions",
   which is not the meaning. Source: `briefs/soak-index.md:9` ("Two Kafka decisions exist and stay
   apart"). Fix: "The live-lane bus of Section 4 is a separate decision."

2. **Section 3.4's memory range carries no rig or rate, and Table 6 gives a different peak.**
   `sections/02-design-a.tex:59` "Under steady load the collector ran between 133 and 228~MB",
   against Table 6's 87.1 and 62.7 MB. Section 5.2 does name Round 1 and its rate. Source:
   `briefs/soak-index.md:59` and `briefs/soak-index.md:55`. Fix: "on the rig of Section 5.1, at up to
   53,000 records a second".

3. **Table 5's ClickHouse verdict is a fragment.** `sections/04-why.tex:29` "17 detectors run by
   us". Source: `briefs/why.md` store entry: the store must ship detection, or the project runs 17
   detectors itself. Fix: "we would run the 17 detectors ourselves".

4. **The six local detectors are never placed inside the seventeen.** `sections/02-design-a.tex:49`
   "The trend rollup, six local anomaly detectors and the shifter view's line sampler read the local
   index." Source: `reports/inputs/dataflow-atlas.md:29` (six local-* detectors of the 17 read the
   local index). Fix: "six of the seventeen detectors".

5. **"Pages" is used as a verb before page and warn are named as the two tiers.**
   `sections/03-design-b.tex:5` "a storage disk above 92~\% pages, and above 85 up to 92~\% warns."
   The page tier and the warn tier then carry Sections 3.7 and 3.8. Source: `briefs/style.md` (define
   at first use). Fix: "reaches the page tier, the severity that would wake a person, and 85 to
   92~\% the warn tier".

## Terms the reader still could not resolve, not raised above

The reader's list repeats round 1 on: cockpit against maintainer cockpit against cockpit-metrics;
break-glass; watermark and upsert; anchored pattern; fixture run and fault agent; int8 and fp32.
Each is one clause of work and none changed an answer, so they are left to the simplicity lens.

## Reader faults, not the report's

- Farm pilot scope. The reader read `report-text.txt`, which loses colour and italics, so the grey
  italic "not stated on the farm" items in Figure 7 (`figures/fig07-layouts.svg:67`, `:99`, `:104`)
  read to them as claims. The figure and its legend are correct.
- Collector memory, 133 to 228 MB against Table 6. Section 5.2 names Round 1 and its rate, and the
  table caption names the rig and the rate. The nit above only shortens the distance.
- Push against poll. The abstract's "Nothing polls it" is about the workers, and Section 3.6 says the
  poller samples the cluster. Both are correct as written.
