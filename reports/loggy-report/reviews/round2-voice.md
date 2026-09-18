# Round 2 review: voice

Lens: voice. Read against reports/loggy-report/briefs/style.md, .claude/skills/simple-english/SKILL.md (Document rules, Plain mode, British spelling override) and .claude/skills/humanizer/SKILL.md, then all eight parts in full.

Clean at this pass, with no findings: British spelling (licence, colour, catalogue, normalises, analysed, learnt are all consistent; no -ize form survives), semicolons (none), em dashes in prose (none), intensifiers and sales words (none), forced triads (every triad carries three distinct facts), one-line closers (every closing sentence carries a fact), headings repeated in the first sentence (the round-1 hit in 5.7 is gone), version numbers (only the collector releases 4.0.1 and 5.0.8 and the two OpenSSL releases appear, all inside the two allowed results). Contrast pairs such as "tens of InfoLogger records a second, not thousands" and "for decoupling and not durability" are kept, because style.md rule 12 allows a contrast where both halves carry a fact.

Round-1 voice findings that are fixed: "the soak" is now defined, the dashed box replaces "ghost", the live lane is defined at first use, CNCF is spelled out, F1 and principal component analysis are defined, the fault-agent modals are now "can", "stdout" is gone, the nine ordinals are a list, the grade scale 0 to 1 is given, "17 detectors" now opens as "Seventeen detectors".

Round-1 voice findings still present: the "should be alive" modal (moved from 3.6 to 3.1), "clusters" for template groups (moved from 5.5 to 3.5), the "five families" against "six sources" collision, the dangling "Fixed, severity comes out ...", and the project shorthand "green run" (removed from the introduction, left in the conclusion).

## Must

### 1. A 34-word sentence in the dead-collector bound
- File: reports/loggy-report/sections/03-design-b.tex:19
- Quote: "From the death: the grace and the poll give the first missing flag after 60 to 150~seconds, the monitor adds up to 60, the projector up to 30, and the page wait 30."
- Problem: 34 words, four ideas, and the last three clauses drop their verbs and their units, so the reader cannot tell what 60, 30 and 30 count.
- Source: reports/loggy-report/briefs/style.md:11 (25 words is the ceiling, one idea per sentence); .claude/skills/simple-english/SKILL.md:32 and :37 (25 words, no telegraph style)
- Fix: Split into four sentences with units: "The grace period and the poll give the first missing flag 60 to 150~seconds after the death. The monitor adds up to 60~seconds. The projector adds up to 30. The page wait adds another 30."

### 2. A 27-word sentence, and a 26-word sentence, in the catalogue paragraph
- File: reports/loggy-report/sections/02-design-a.tex:73
- Quote: "Two hourly rules read the catalogue: one fires on a template catalogued in the last hour, the other on any failed check in the last two hours."
- Problem: 27 words over the 25-word ceiling, and the same paragraph holds a 26-word sentence ("The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals, and that both storage families agree on versions per worker and hour.").
- Source: reports/loggy-report/briefs/style.md:11; .claude/skills/simple-english/SKILL.md:32
- Fix: "Two hourly rules read the catalogue. One fires on a template catalogued in the last hour. The other fires on any failed check in the last two hours." Split the 26-word sentence at "and that": "The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals. It also proves that both storage families agree on versions per worker and hour."

### 3. A banned modal in the roster definition
- File: reports/loggy-report/sections/02-design-a.tex:11
- Quote: "The roster lists the collectors that should be alive."
- Problem: "should" is a banned modal and the meaning is a requirement, not an expectation; this is the round-1 must from 03-design-b.tex:3 moved into 3.1 rather than fixed.
- Source: .claude/skills/simple-english/SKILL.md:36 ("Modals: can, will, must. Never should, would, may, might, could"); reports/loggy-report/reviews/round1-by-file/03-design-b.tex.json (same quote, severity must)
- Fix: "The roster lists the collectors that must run."

### 4. A banned modal in the collector caption
- File: reports/loggy-report/sections/02-design-a.tex:55
- Quote: "A line takes five steps inside the collector, and the 64 chunks it may hold in memory bound what it can cost the worker."
- Problem: "may hold" is a banned modal where the meaning is capacity, and "bound what it can cost" states no number, so the caption's point is soft.
- Source: .claude/skills/simple-english/SKILL.md:36; reports/loggy-report/briefs/style.md:82 (a caption is a full sentence that states the point)
- Fix: "A line takes five steps inside the collector, and the 64 chunks it can hold in memory cap its cost to the worker at about 128~MB."

### 5. "Clusters" carries a second meaning in 3.5
- File: reports/loggy-report/sections/02-design-a.tex:71
- Quote: "The stamper holds at most 20,000 clusters per worker and drops the least recently used."
- Problem: "cluster" means the search cluster everywhere else in the report, and here it means the group of lines under one template, which is the round-1 must fixed in 5.5 and left standing here.
- Source: .claude/skills/simple-english/SKILL.md:39 (one word, one meaning, for the whole document); reports/loggy-report/reviews/round1-by-file/06-eval-b.tex.json (quote "every line kept its cluster", severity must); reports/loggy-report/sections/06-eval-b.tex:11 uses "templates" for the same ceiling
- Fix: "The stamper holds at most 20,000 templates per worker and drops the least recently used one."

### 6. The shifter view is said never to query the cluster, and also to hold a query lane
- File: reports/loggy-report/sections/03-design-b.tex:29
- Quote: "It never queries the cluster, so it holds when the cluster is down."
- Problem: "It" reads as the shifter view, which contradicts 02-design-a.tex:44 ("Dashboards and the shifter view's query lane, capped at 20,000 rows, read the line from the central index"); only the live page never queries.
- Source: reports/inputs/dataflow-atlas.md:23 ("The Shifter's query lane searches the same central index, capped at 20,000 rows"); reports/loggy-report/briefs/target-architecture.md:75 (live lane with zero cluster cost, templates page reads the catalogue)
- Fix: "The live page never queries the cluster, so it holds when the cluster is down." Add the query lane's name at 02-design-a.tex:44: "the shifter view's query lane, its one search over the central index, capped at 20,000 rows".

### 7. Episode and incident are defined in a circle
- File: reports/loggy-report/sections/03-design-b.tex:7
- Quote: "An incident is the episode document grouping the signals of one alert name on one entity."
- Problem: the sentence before defines an episode as one trouble, so the reader now has two names for one thing and cannot tell an incident from an episode.
- Source: reports/loggy-report/briefs/target-architecture.md:108 ("alice-incidents | one document per episode"); .claude/skills/simple-english/SKILL.md:40 (define a concept term at first use, one per sentence)
- Fix: Keep one name in prose: "The projector stores each episode as one document, which groups the signals of one alert name on one entity." Delete the word "incident" from 3.6, or say once that the stored document is called an incident.

### 8. "No multiple stands" cannot be read
- File: reports/loggy-report/sections/04-why.tex:48
- Quote: "The shipped control of 19.91 was not re-measured, so no multiple stands."
- Problem: "multiple" is used as a noun for a cost ratio and "control" as a noun for a reference run, and neither word is defined anywhere before Section 5.1.
- Source: reports/loggy-report/briefs/soak-3.md:174 ("a control reading of 19.91 for the shipped configuration that I did not re-measure, so I will not quote a new multiple"); .claude/skills/simple-english/SKILL.md:40
- Fix: "The old pipeline's 19.91 was measured once and never re-measured, so we quote no ratio between the two figures."

### 9. Two Kafka conditions are compressed into one unreadable sentence
- File: reports/loggy-report/sections/04-why.tex:60
- Quote: "It must be Apache Kafka, because CERN requires fully open source, and the primary path, because the pinned collector has no on-failure route."
- Problem: the second half elides its verb, so "and the primary path" hangs off "It must be", and "pinned" is never explained as version-pinned by the OpenSSL wall.
- Source: reports/loggy-report/briefs/why.md:123 ("If a queue is built it must be Apache Kafka: CERN requires fully open source ... It must be the primary path, not a failure path, because the pinned collector has no on-failure route"); .claude/skills/simple-english/SKILL.md:37
- Fix: "It must be Apache Kafka, because CERN requires fully open source software. It must carry the live lane as the primary path, because the collector held at release 4.0.1 has no route for a failed output."

## Should

### 10. Section 2 lost its opening claim in the condense
- File: reports/loggy-report/sections/01-intro-problem.tex:13
- Quote: "\section{The problem and the constraints}\label{sec:problem}"
- Problem: the section drops straight into a subsection, so the claim that the constraints made the design is never stated; Section 4 opens the same way.
- Source: reports/loggy-report/outline.md:50 ("Claim: the constraints are what made the design. State them before the design."); reports/loggy-report/briefs/style.md:7 (state the conclusion first)
- Fix: Add one sentence under the heading: "The constraints made the design, so they come first: what a node writes, how much of it, and what the farm will not give up."

### 11. Two 26-word sentences in 3.4
- File: reports/loggy-report/sections/02-design-a.tex:59
- Quote: "The documented worst case is twice that with a 20~\% margin, roughly 307~MB, so the service is throttled at 384~MB and killed at 768."
- Problem: 26 words and three numbers in one sentence, and line 57 runs to 26 words as well ("Writing to the local node every second instead of the shipped 5~seconds cut the collector's processor cost by 24.5~\% and its peak memory by 28.0~\%.").
- Source: reports/loggy-report/briefs/style.md:11; .claude/skills/simple-english/SKILL.md:32
- Fix: "The documented worst case is twice that with a 20~\% margin, roughly 307~MB. The service is throttled at 384~MB and killed at 768." For line 57: "The collector writes to the local node every second, not the shipped 5. That cut its processor cost by 24.5~\% and its peak memory by 28.0~\%."

### 12. The monitor counts are telegraphic, in a twelve-sentence paragraph
- File: reports/loggy-report/sections/03-design-b.tex:5
- Quote: "Of 30 monitors, 17 are threshold and detector monitors, 13 every minute and four every ten."
- Problem: the last clause drops its verb and its unit, so "four every ten" reads as four of something every ten of something, and the paragraph it sits in runs to twelve sentences.
- Source: .claude/skills/simple-english/SKILL.md:37 (keep articles, no telegraph style) and :32 (six sentences per paragraph at most)
- Fix: "Thirty monitors run. Seventeen are threshold and detector monitors: 13 run every minute and four every ten minutes." Break the paragraph after the entity definition.

### 13. The page and warn tiers are used before they are named
- File: reports/loggy-report/sections/03-design-b.tex:11
- Quote: "A page waits 30~seconds, a warn five minutes."
- Problem: "a page" and "a warn" are used as nouns for the two severity tiers with no definition, and 3.7 and 3.8 then lean on "page severity" and "the warn tier".
- Source: .claude/skills/simple-english/SKILL.md:40 (define a concept term at first use); reports/loggy-report/briefs/style.md:22
- Fix: "Alerts carry one of two tiers: a page wakes a person, a warn waits for one. Alertmanager holds a page 30~seconds and a warn five minutes before it sends the batch."

### 14. "Break-glass" is never explained
- File: reports/loggy-report/sections/03-design-b.tex:13
- Quote: "Two break-glass monitors watch the projector and Alertmanager, and only they post straight to the receiver."
- Problem: the term carries the whole self-watching argument here and in 5.7, and the report never says it means the path that works when the normal path is the thing that broke.
- Source: reports/loggy-report/briefs/style.md:22 (define a term the moment it appears); reports/loggy-report/sections/06-eval-b.tex:52 relies on "the break-glass path"
- Fix: "Two monitors watch the projector and Alertmanager themselves. They take the break-glass path: they post straight to the receiver, because the projector they watch may be the dead part."

### 15. The ops page arrives as a fifth surface with no job
- File: reports/loggy-report/sections/03-design-b.tex:29
- Quote: "The ops page counts families, alerts, anomaly results, incidents and signals."
- Problem: the paragraph promises four surfaces and then names a fifth thing that was never introduced, with no word on who opens it or why.
- Source: reports/loggy-report/briefs/target-architecture.md:56 ("Ops page | The operator's status line plus the buttons that drive the test harness ... built on staging; the buttons are tester tooling")
- Fix: Either drop the sentence, or say what it is: "A fifth screen, the ops page, holds the counts a maintainer checks and the buttons that drive the test harness."

### 16. "The log detectors" contradicts "no lane reads the log text"
- File: reports/loggy-report/sections/03-design-b.tex:29
- Quote: "Two clocks run: health and the log detectors use the time the collector accepted a record, Discover the time it was written."
- Problem: 3.6 ends "No lane reads the log text", so a reader meeting "the log detectors" here concludes the report contradicts itself; the second clause is also a comma splice with no verb.
- Source: reports/loggy-report/briefs/why.md:357 ("log detectors key on the collector's accept time, while Discover keeps the event time"); reports/loggy-report/briefs/target-architecture.md:162
- Fix: "Two clocks run. The health samples and the detectors that count log records use the time the collector accepted a record. Discover uses the time the line was written."

### 17. Section 5.2 opens on a round number, not on a conclusion
- File: reports/loggy-report/sections/05-eval-a.tex:17
- Quote: "In Round 1, at up to 53,000 records a second, the collector's memory sat between 133 and 228~MB."
- Problem: the subsection that carries the collector's cost opens with internal round numbering instead of the result, so the reader meets the campaign's bookkeeping before the finding.
- Source: reports/loggy-report/briefs/style.md:7 (state the conclusion first, then the support)
- Fix: "The collector's memory sat between 133 and 228~MB at every rate up to 53,000 records a second."

### 18. Five families, six sources, and an unnamed sixth format
- File: reports/loggy-report/sections/05-eval-a.tex:54
- Quote: "The collector had never read three of the six sources. Six, because the process tree has two line formats."
- Problem: the report counts five families in 2.1 and 3.1 and six sources here, and the next sentence names "the data-distribution format", which 2.1 never named as one of the process tree's two formats.
- Source: reports/loggy-report/briefs/constraints.md:85 and :91 ("A worker holds five log families. The O2 process tree uses two line formats"); constraints.md:126 names the formats dpl and datadist; reports/loggy-report/reviews/round1-by-file/05-eval-a.tex.json (same collision, severity should)
- Fix: Name the two formats in Table 1 and in 2.1, then write here: "The collector had never read three of the five families: the data-distribution format of the process tree, the InfoLogger daemon log and the system journal."

### 19. A dangling modifier hides who fixed the parser
- File: reports/loggy-report/sections/05-eval-a.tex:54
- Quote: "Fixed, severity comes out of 99.83~\% of process-tree lines against a 99~\% gate."
- Problem: the opening participle has no actor and the round-1 review flagged this exact construction, which was removed from 3.4 and left here.
- Source: .claude/skills/simple-english/SKILL.md:35 (simple tenses, active voice, name the actor); reports/loggy-report/reviews/round1-by-file/02-design-a.tex.json (quote "Measured, it ran between 133 and 228~MB", severity should, names this line as the repeat)
- Fix: "We fixed the anchor. Severity now comes out of 99.83~\% of process-tree lines, against a 99~\% gate."

### 20. A rate is quoted with no unit
- File: reports/loggy-report/sections/05-eval-a.tex:38
- Quote: "At a median worker's 23 records a second, that share is 13.8, and the buffer covers 17.4~hours."
- Problem: 13.8 carries no unit, so the reader has to derive that it is InfoLogger records a second, and the same sentence pattern repeats for 46.8.
- Source: reports/loggy-report/briefs/style.md:28 (every number carries its meaning and its reference); reports/loggy-report/outline.md:21 (decision 9, the hold is for the InfoLogger lane)
- Fix: "At a median worker's 23 records a second, InfoLogger is 13.8 records a second, and the buffer covers 17.4~hours. At the busiest worker-second, 78, InfoLogger is 46.8 a second and the buffer covers 5.1~hours."

### 21. An internal file is offered as the reader's evidence
- File: reports/loggy-report/sections/06-eval-b.tex:34
- Quote: "The model's memory peak is not in the results file."
- Problem: "the results file" points at a project artefact the reader has never met, which is the round-1 objection to "our sources" in a new place.
- Source: reports/loggy-report/outline.md:19 (decision 7: say the peak was observed in a local run and is not recorded); reports/loggy-report/reviews/round1-by-file/04-why.tex.json (quote "No numbered comparison against Grafana exists in our sources", severity should)
- Fix: "We saw the model's memory peak in one local run and never recorded it, so we do not quote it."

### 22. Section 6.3 opens on the layouts, not on what remains
- File: reports/loggy-report/sections/07-limits-close.tex:33
- Quote: "Figure~\ref{fig:layouts} shows three layouts."
- Problem: the subsection called "What remains, in build order" opens with a pointer to a figure about where the platform runs, so the reader reaches the build order five sentences late.
- Source: reports/loggy-report/briefs/style.md:7; reports/loggy-report/outline.md:138 (6.3 is the ordered list of what remains)
- Fix: Open with the claim and keep the layouts as the support: "Nine things remain, and the first two are already agreed. Three layouts exist today (Figure~\ref{fig:layouts})."

### 23. "Green run" is project shorthand
- File: reports/loggy-report/sections/07-limits-close.tex:57
- Quote: "What exists now is groundwork rather than a demonstration: five staging machines and one green run on the farm."
- Problem: "green run" is internal shorthand for a deployment whose checks all passed, and the introduction was rewritten to say so in plain words while the conclusion kept the shorthand.
- Source: reports/loggy-report/reviews/round1-by-file/01-intro-problem.tex.json (quote "one deployment went green", severity should); reports/loggy-report/sections/01-intro-problem.tex:9 now reads "passed every check"
- Fix: "... five staging machines, and one deployment on the farm that passed every check."

### 24. "Sink" is used three times and never defined
- File: reports/loggy-report/sections/07-limits-close.tex:11
- Quote: "Round 1's 53,000 was read against a sink that always accepts, so the collector's ceiling with a real store is unknown above 50,000."
- Problem: the report says "output" in 3.4 and "sink" in 3.4, 5.2 and here, so one thing carries two names and the newer one is never explained.
- Source: .claude/skills/simple-english/SKILL.md:39 (one word, one meaning, for the whole document); reports/loggy-report/sections/02-design-a.tex:61 uses "per output" for the same thing
- Fix: Use one word. "Round 1's 53,000 was read against a test output that always accepts, so the collector's ceiling with a real store is unknown above 50,000."

### 25. Inhibition and causal edges arrive undefined, twice
- File: reports/loggy-report/sections/04-why.tex:54
- Quote: "Inhibition is built and ships off, because all 22 causal edges are unproven."
- Problem: neither "inhibition" nor "causal edge" is explained here or at 07-limits-close.tex:23, so the reason a built feature ships disabled cannot be read.
- Source: reports/loggy-report/briefs/style.md:22 (define a term the moment it appears); .claude/skills/simple-english/SKILL.md:40
- Fix: "Inhibition, where one alert silences another it causes, is built and ships off. All 22 cause-and-effect links between alerts are unproven."

### 26. Four counterfactual modals
- File: reports/loggy-report/sections/07-limits-close.tex:5
- Quote: "One micro-benchmark on one farm machine would give the conversion factor."
- Problem: "would" is a banned modal, and the same modal sits at 01-intro-problem.tex:34 ("or each file would land once per machine"), 04-why.tex:39 ("Three would reach about 135 at full retention") and the Appendix A caption at 07-limits-close.tex:69.
- Source: .claude/skills/simple-english/SKILL.md:36
- Fix: "One micro-benchmark on one farm machine gives the conversion factor. None has run." Then: "or each file lands once per machine"; "Three primaries reach about 135 at full retention"; "Each upstream role replaces a few stable tasks and hides something the deployment needs."

## Nit

### 27. Three terms are defined twice
- File: reports/loggy-report/sections/04-why.tex:48
- Quote: "Masking fell from 11.93 to 1.82 core-seconds per million lines on InfoLogger, one core-second being one core busy for one second."
- Problem: the core-second is defined again at 05-eval-a.tex:7, the masker again at 02-design-a.tex:71 and the template again at 06-eval-b.tex:3, which is what the condense passes left behind.
- Source: .claude/skills/simple-english/SKILL.md:40 (define a concept term at its first use)
- Fix: Keep the first definition of each and cut the later one; here, end the sentence at "on InfoLogger".

### 28. Seven sentences in a row start with "We"
- File: reports/loggy-report/sections/01-intro-problem.tex:71
- Quote: "We kept its collector on every node, widened from two sources to five."
- Problem: the paragraph runs We kept, We kept, We changed, We replaced, We dropped, We dropped, We dropped, which reads as a template rather than a paragraph.
- Source: .claude/skills/humanizer/SKILL.md section 7 (repeated sentence openings)
- Fix: Merge the two kept sentences and the three dropped ones: "We kept its collector on every node, widened from two sources to five, and its one cluster of two tiers with its three index families, lifecycle policies and Alertmanager."

### 29. Three sentences start with a numeral
- File: reports/loggy-report/sections/04-why.tex:36
- Quote: "300 data nodes in one cluster is an untested risk for the cluster manager."
- Problem: round 1 fixed "17 detectors run" to "Seventeen detectors run", and three sentences still open on digits, here and at 04-why.tex:39 and 06-eval-b.tex:30.
- Source: reports/loggy-report/reviews/round1-by-file/03-design-b.tex.json (quote "17 detectors run, one per entity", severity should)
- Fix: "Three hundred data nodes in one cluster is an untested risk for the cluster manager." Recast the other two: "The process-tree family keeps 96.94~\% of its lines on the worker."; "Of the relevant question-and-template pairs, 47.1~\% share no analysed term."

### 30. A clipped sentence in 2.1
- File: reports/loggy-report/sections/01-intro-problem.tex:17
- Quote: "Collecting the run orchestrator's log is a deployment decision not taken."
- Problem: the phrase drops "that was", and the sentence sits between two unrelated facts with no connection to either.
- Source: .claude/skills/simple-english/SKILL.md:37 (keep articles, keep "that")
- Fix: "Nobody has decided yet whether to collect the run orchestrator's log."

### 31. A comparison with no verb
- File: reports/loggy-report/sections/06-eval-b.tex:32
- Quote: "A static model encodes 18,037 templates a core-second, 31 times a small transformer, and keeps 93\,\% of its quality."
- Problem: "31 times a small transformer" drops the comparison word, so the reader supplies "faster than" without being told.
- Source: .claude/skills/simple-english/SKILL.md:37
- Fix: "A static model encodes 18,037 templates a core-second, 31 times faster than a small transformer, and keeps 93\,\% of its quality."
