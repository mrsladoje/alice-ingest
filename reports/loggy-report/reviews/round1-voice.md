# Round 1 review: voice

Lens: voice. Sources read: reports/loggy-report/briefs/style.md, .claude/skills/simple-english/SKILL.md (Document rules, Plain mode, British spelling), .claude/skills/humanizer/SKILL.md, all eight section files, reports/loggy-report/outline.md.

Clean at every severity: no semicolons, no em dashes or en dashes, no bold in prose, no "we introduce" or "we propose", no intensifier or sales word from the banned list (robust, seamless, rigorous, powerful, comprehensive, leverage, crucial, key as an adjective, highly, optimal, critical, significant), no American spelling of organise, colour, centre, analyse or licence, and no version number beyond the two the outline allows.

Findings: 48 in total, 11 must, 34 should, 3 nit.

## Must

### M1. reports/loggy-report/sections/05-eval-a.tex:36

Quote:

> The live lane, the second output that copies records to the shifter view, is the largest cost any setting adds: 41 to 68~\% more collector processor time on the healthy rig.

Problem: The sentence runs to 31 words and carries three ideas, and it defines the live lane in section 5 although section 3 already used the term four times.

Source: reports/loggy-report/briefs/style.md:11 (25 words is the ceiling, most sentences under 15); reports/loggy-report/briefs/soak-1.md:136

Fix: Define the live lane at its first use in section 3.1, then write here: "The live lane is the largest cost any setting adds. It costs 41 to 68~\% more collector processor time on the healthy rig."

### M2. reports/loggy-report/sections/02-design-a.tex:13

Quote:

> The ghost in Figure~\ref{fig:arch} is a Kafka bus, agreed for the live lane only.

Problem: Two words the reader cannot resolve: "ghost" is drawing shorthand for a dashed box, and "live lane" appears here for the first time with no definition.

Source: reports/loggy-report/briefs/target-architecture.md:11 ("Severe lines also go to a live lane that a person watches in a browser without a cluster query")

Fix: "The dashed box in Figure~\ref{fig:arch} is a Kafka bus, agreed for the live lane only." Add the definition earlier, in 3.1: "The live lane is a copy of every severe line, pushed straight to a page a person watches, so no one queries the cluster to see what is arriving."

### M3. reports/loggy-report/sections/02-design-a.tex:11

Quote:

> The control host runs Dashboards, the poller, the roster, the catalog maintenance, Alertmanager and the receiver.

Problem: Six components are named before any of them is defined; the poller, the roster and the receiver are only explained two subsections later.

Source: .claude/skills/simple-english/SKILL.md:40 (define a concept term at its first use); reports/loggy-report/briefs/target-architecture.md:51-52

Fix: Give each name its job in the same breath, or split into two sentences: "The control host runs Dashboards and Alertmanager. Beside them sit the poller, which samples the cluster, the roster, which says which collectors must be running, the catalogue maintenance and the receiver, which stores what a person was told."

### M4. reports/loggy-report/sections/01-intro-problem.tex:44

Quote:

> The soak tested one collector at 1,000, 20,000 and 50,000 records a second per worker.

Problem: "The soak" is internal shorthand for the measurement campaign and is never defined, yet it carries three later claims and the title of Appendix B.

Source: reports/loggy-report/briefs/soak-index.md:1-3 (rounds 1 to 21 of measurement, docs/SOAK.md and docs/SOAK_RESULTS.md)

Fix: Define it once here: "We ran the platform under a synthetic load for twenty-one rounds, and we call that campaign the soak. It tested one collector at 1,000, 20,000 and 50,000 records a second per worker."

### M5. reports/loggy-report/sections/03-design-b.tex:3

Quote:

> The poller samples the cluster at the same cadence and reads the roster: one document listing which collectors should be alive.

Problem: "should be alive" uses a modal the style forbids, and the meaning is "must be running", not an expectation.

Source: .claude/skills/simple-english/SKILL.md:36 ("Modals: can, will, must. Never should, would, may, might, could.")

Fix: "... and reads the roster: one document naming every collector that must be running."

### M6. reports/loggy-report/sections/06-eval-b.tex:52

Quote:

> A fault agent on each worker may kill its collector, and the agent on the projector's machine may stop only the projector.

Problem: Two forbidden modals, where the meaning is capability: the agent can kill the collector, and the other agent can stop only the projector.

Source: .claude/skills/simple-english/SKILL.md:36

Fix: "A fault agent on each worker can kill its collector. The agent on the projector's machine can stop only the projector."

### M7. reports/loggy-report/sections/04-why.tex:50

Quote:

> The pipeline fell from 19.91 to 11.18 core-seconds per million lines.

Problem: The unit core-second is used in section 4 but defined only in section 5.1, so the number means nothing at first reading.

Source: reports/loggy-report/sections/05-eval-a.tex:7 ("core-seconds, one core busy for one second")

Fix: Define the unit at this first use: "The pipeline fell from 19.91 to 11.18 core-seconds per million lines, one core-second being one core busy for one second." Or move the sentence's number into section 5.

### M8. reports/loggy-report/sections/04-why.tex:6

Quote:

> We chose Fluent Bit~\cite{ref:fluentbit}, written in C and graduated by the CNCF~\cite{ref:cncf}.

Problem: CNCF is never spelled out and "graduated" is never explained, so the deciding fact of the whole paragraph is unreadable outside the field.

Source: reports/loggy-report/briefs/why.md:19 ("CNCF graduated, fifteen billion deployments on its own project page, a decade in production")

Fix: "We chose Fluent Bit~\cite{ref:fluentbit}. It is written in C, and the Cloud Native Computing Foundation has graduated it, which is that foundation's top maturity mark~\cite{ref:cncf}."

### M9. reports/loggy-report/sections/04-why.tex:53

Quote:

> Deep sequence models reach an F1 of about 0.23 to 0.27 under drift, against about 0.71 for plain PCA.

Problem: Three terms arrive undefined in one sentence: F1, PCA and drift, and F1 has no scale attached.

Source: reports/loggy-report/briefs/why.md:177 ("F1 about 0.23 to 0.27 under drift against about 0.71 for plain PCA")

Fix: "Deep sequence models score about 0.23 to 0.27 on F1, the balance of catching a fault and not crying wolf, where 1 is perfect. Principal component analysis, a far older method, scores about 0.71 on the same logs after the log text has changed."

### M10. reports/loggy-report/sections/03-design-b.tex:23

Quote:

> Every ten minutes the rollup re-rolls the last three closed buckets after a 120-second settle for late records.

Problem: The rollup, "re-rolls", "closed buckets" and "settle" all arrive undefined, and the sentence carries three ideas.

Source: reports/loggy-report/briefs/target-architecture.md:65 ("Turns raw logs into one small row per host per 10 minutes, so the trend rules stay cheap")

Fix: "A rollup service turns the raw logs into one small row per family and host per ten minutes. It runs every ten minutes and recomputes the last three ten-minute windows, because a record can land 120 seconds late."

### M11. reports/loggy-report/sections/06-eval-b.tex:13

Quote:

> The whole-pipeline rewrite made seven families 31 to 57\,\% cheaper, and every line kept its cluster.

Problem: "cluster" means the group of lines under one template here, but everywhere else in the report it means the search cluster, so one word carries two meanings.

Source: .claude/skills/simple-english/SKILL.md:39 (one word, one meaning, for the whole document); reports/loggy-report/sections/02-design-a.tex:3 ("\loggy{} is one cluster")

Fix: "The whole-pipeline rewrite made seven families 31 to 57\,\% cheaper, and every line kept the same template."

## Should

### S1. reports/loggy-report/sections/07-limits-close.tex:3

Quote:

> This section lists what the platform does not yet do and what was not measured.

Problem: The opening sentence restates the heading and states no conclusion, so the section starts with nothing.

Source: .claude/skills/humanizer/SKILL.md:338 (a heading repeated in the first sentence); reports/loggy-report/briefs/style.md:7 (state the conclusion first)

Fix: Delete it and open on the conclusion: "The platform runs, and every cost figure in it comes from a laptop. These are the things that were never measured and the limits the design still carries."

### S2. reports/loggy-report/sections/01-intro-problem.tex:48

Quote:

> Eight constraints shape the design.

Problem: The opener counts instead of concluding, and 2.4 opens with the same formula ("Six objectives shape the design") one subsection later.

Source: reports/loggy-report/briefs/style.md:7; reports/loggy-report/sections/01-intro-problem.tex:60

Fix: "A worker gives the platform four of its 128 logical processors, and every other constraint follows from that scarcity." Then change 2.4's opener so the two do not rhyme.

### S3. reports/loggy-report/sections/06-eval-b.tex:52

Quote:

> Injection proves the chain from a fault to a stored notification.

Problem: The first sentence repeats the heading "The detection chain, proven by injection" before the section says anything.

Source: .claude/skills/humanizer/SKILL.md:338

Fix: Open on the result instead: "Seven injected faults reached a stored notification, and one of them proved the break-glass path."

### S4. reports/loggy-report/sections/00-abstract.tex:1

Quote:

> Info lines stay in one local copy, every other severity goes to three storage machines.

Problem: A comma splices two independent clauses, so one sentence carries two ideas; the abstract does it again in "Every worker pushes its health up, nothing polls it."

Source: .claude/skills/simple-english/SKILL.md:32 (one topic per sentence, 25 words); reports/loggy-report/briefs/style.md:11

Fix: "Info lines stay in one local copy. Every other severity goes to three storage machines." Split the health sentence the same way.

### S5. reports/loggy-report/sections/02-design-a.tex:59

Quote:

> Measured, it ran between 133 and 228~MB under steady load and peaked at 373~MB in a sink outage with the shipped buffer.

Problem: The sentence opens on a dangling participle that hides the actor, and then carries two measurements; "Fixed, severity comes out of 99.83~\% ..." in 5.4 repeats the pattern.

Source: .claude/skills/simple-english/SKILL.md:35 (simple tenses, active voice, name the actor); reports/loggy-report/sections/05-eval-a.tex:58

Fix: "Under steady load the collector ran between 133 and 228~MB. In a sink outage with the shipped buffer it peaked at 373~MB." Repair the "Fixed," opener in 5.4 the same way.

### S6. reports/loggy-report/sections/02-design-a.tex:61

Quote:

> At 20,000 records a second it gave 61~seconds before the first drop, at a real worker's rate hours.

Problem: A comma splice joins two clauses and the second one has no verb, so the reader has to rebuild it.

Source: reports/loggy-report/briefs/target-architecture.md:152 ("256 MB gave 61 s before the first drop at 20,000 records a second")

Fix: "At 20,000 records a second the buffer held 61~seconds before the first drop. At a real worker's rate it holds hours."

### S7. reports/loggy-report/sections/02-design-a.tex:73

Quote:

> Two hourly rules close the chain: one fires on a template first catalogued in the last hour, the other on any failed check in the last two hours.

Problem: 28 words, over the 25-word ceiling, with the second clause elliptical.

Source: reports/loggy-report/briefs/style.md:11

Fix: "Two hourly rules close the chain. One fires on a template catalogued in the last hour. The other fires on any failed check in the last two hours."

### S8. reports/loggy-report/sections/02-design-a.tex:73

Quote:

> one fires on a template first catalogued in the last hour

Problem: "catalogued" is British, but the report spells the same word's noun American ("catalog") eleven times, against the agreed British spelling.

Source: reports/loggy-report/outline.md:28 (British spelling); reports/loggy-report/briefs/style.md:74

Fix: Use "catalogue" and "catalogued" everywhere: "the catalogue", "the catalogue maintenance", "the template catalogue".

### S9. reports/loggy-report/sections/02-design-a.tex:75

Quote:

> Version W covers version N when they share a family and a token count and W has, at every position, a wildcard or N's token.

Problem: Two bare symbols, W and N, stand in for template versions, and the style forbids symbols and shorthand in prose.

Source: reports/loggy-report/briefs/style.md:5 (rules illustrated by plain sentences from the deck); /Users/admin/.claude/CLAUDE.md rule 5 (no symbols, spell the thing out)

Fix: "A wider version covers a narrower one when both belong to the same family, hold the same number of tokens, and the wider version has either a wildcard or the narrower version's own token at every position."

### S10. reports/loggy-report/sections/02-design-a.tex:7

Quote:

> A local OpenSearch data node holds one index per worker on four of the EPN's 128~cores, and little memory.

Problem: The clause attaches the whole platform's budget of four cores to the data node alone, and "holds ... and little memory" does not parse.

Source: reports/loggy-report/briefs/target-architecture.md:11; reports/loggy-report/sections/01-intro-problem.tex:50 ("A worker gives the platform four of its 128 logical processors")

Fix: "A local OpenSearch data node holds one index per worker. The four parts together take four of the EPN's 128 logical processors and little memory."

### S11. reports/loggy-report/sections/02-design-a.tex:41

Quote:

> The local node's ingest pipeline, processors run before a document is stored, stamps the ingest time and normalises the severity.

Problem: The definition is wedged in as an appositive with no article, and the passive "is stored" hides who stores the document.

Source: .claude/skills/simple-english/SKILL.md:35; .claude/skills/simple-english/SKILL.md:40

Fix: "The local node runs an ingest pipeline: a set of processors that touch every document before the node stores it. It stamps the ingest time and normalises the severity."

### S12. reports/loggy-report/sections/03-design-b.tex:5

Quote:

> 17 detectors run, one per entity, and a silent host scores zero.

Problem: "entity" is never defined, and the sentence starts with a numeral.

Source: reports/loggy-report/briefs/target-architecture.md:101 ("one row per family and entity per 10-minute bucket"); reports/loggy-report/briefs/why.md:178

Fix: "Seventeen detectors run, one for each watched thing: a host, a log family or an index. A silent host scores zero."

### S13. reports/loggy-report/sections/03-design-b.tex:7

Quote:

> A detector grade above 0.5 opens an episode, the paging monitor on grades needs 0.7.

Problem: A comma splices two clauses, and the grade has no scale, so 0.5 and 0.7 carry no meaning.

Source: reports/loggy-report/briefs/style.md:28 (every number carries its meaning and its reference)

Fix: "A detector grades every window from 0 to 1. A grade above 0.5 opens an episode. The monitor that pages on grades needs 0.7."

### S14. reports/loggy-report/sections/05-eval-a.tex:13

Quote:

> Measurements after the collector screen came from a rig that had saturated unnoticed, so we retracted them.

Problem: "screen" is used as a noun here and as a verb four lines earlier, and neither use is defined.

Source: .claude/skills/simple-english/SKILL.md:39; reports/loggy-report/sections/05-eval-a.tex:9 ("The healthy rig screened the collector at 20,000 records a second")

Fix: Pick one word and define it: "The first pass tested the collector's own settings at 20,000 records a second. Every measurement after that pass came from a rig that had saturated unnoticed, so we retracted them."

### S15. reports/loggy-report/sections/05-eval-a.tex:34

Quote:

> The collector is one single-threaded loop doing 89.3~\% of its work.

Problem: The "-ing" rider makes the sentence ambiguous: the number is the share of work in that one loop, not a property of the loop.

Source: .claude/skills/simple-english/SKILL.md:35 (no "-ing" verb after a comma, name the actor); .claude/skills/humanizer/SKILL.md:233

Fix: "The collector runs one single-threaded loop, and that loop does 89.3~\% of its work. Threads elsewhere cannot help it."

### S16. reports/loggy-report/sections/05-eval-a.tex:54

Quote:

> Or an outage longer than the buffer holds, or a measured worker rate within 50 times of 42,000.

Problem: A sentence begins with "Or" because a list of four conditions was split across two sentences.

Source: .claude/skills/simple-english/SKILL.md:42 (a vertical list is for three or more parallel items); reports/loggy-report/briefs/style.md:11

Fix: Make the four conditions one list: "The question reopens on four conditions only: a sustained worker rate above about 1,000 records a second; a second consumer of the same records; an outage longer than the buffer holds; a measured worker rate within 50 times of 42,000." A vertical list also works.

### S17. reports/loggy-report/sections/05-eval-a.tex:58

Quote:

> The collector had never read three of the six log sources.

Problem: The report counts five log families in 2.1 and 3.1 and six log sources here, so family and source are used as the same word with different counts.

Source: reports/loggy-report/sections/01-intro-problem.tex:17 ("A worker holds five log families"); reports/loggy-report/sections/02-design-a.tex:7 ("The collector reads the five log sources")

Fix: Fix the count and the word: "The collector had never read three of the six sources in Table~\ref{tab:sources}." Then say once, in 2.1, that the table holds six rows and that only five reach a collector.

### S18. reports/loggy-report/sections/04-why.tex:38

Quote:

> Cross-cluster search does not scale to 100 machines.

Problem: Cross-cluster search is named as the losing alternative but never defined, and the paragraph then writes "Three hundred data nodes" in words after "100 machines" in digits.

Source: reports/loggy-report/briefs/why.md:48-49 ("many clusters joined by cross-cluster search ... does not scale to 100 machines")

Fix: "The alternative is many clusters joined by cross-cluster search, where one cluster forwards a query to the others. It does not scale to 100 machines. 300 data nodes in one cluster is an untested risk for the cluster manager."

### S19. reports/loggy-report/sections/04-why.tex:47

Quote:

> No numbered comparison against Grafana exists in our sources.

Problem: "our sources" points at the project's own research notes, which the reader cannot see, instead of saying plainly what was not done.

Source: reports/loggy-report/briefs/style.md:81 (say "not measured" or "not tested"); reports/loggy-report/briefs/style.md:89

Fix: "We did not compare Dashboards and Grafana on numbers."

### S20. reports/loggy-report/sections/04-why.tex:56

Quote:

> The projector turns thirty rows about one dead collector into one fault with a start and an end.

Problem: "one fault" names the thing the report has already named an episode, so the same idea carries two words.

Source: reports/loggy-report/sections/03-design-b.tex:7 ("An episode is one trouble on one thing, with a start and an end."); .claude/skills/simple-english/SKILL.md:39

Fix: "The projector turns thirty rows about one dead collector into one episode with a start and an end."

### S21. reports/loggy-report/sections/04-why.tex:68

Quote:

> This overlaps the Telegraf and Mimir estate CERN runs, so we concede that half.

Problem: "that half" has no antecedent and "estate" is unexplained, so the concession cannot be read.

Source: reports/loggy-report/briefs/why.md:19; reports/loggy-report/outline.md:104 ("the overlap conceded, the roster and absence logic kept")

Fix: "CERN already runs Telegraf and Mimir for machine metrics. Our cluster and node samples repeat that work, and we concede it. The roster and the absence logic have no equivalent there, so they stay."

### S22. reports/loggy-report/sections/06-eval-b.tex:32

Quote:

> It finds a template by meaning, closing a measured gap: 47.1\,\% of relevant question-and-template pairs share no analysed term.

Problem: An "-ing" phrase after a comma bolts the reason onto the fact, which the style forbids.

Source: .claude/skills/simple-english/SKILL.md:35; .claude/skills/humanizer/SKILL.md:233

Fix: "It finds a template by meaning. The gap it closes is measured: 47.1\,\% of relevant question-and-template pairs share no analysed term."

### S23. reports/loggy-report/sections/06-eval-b.tex:36

Quote:

> The standing recommendation is a dense scan by exact cosine over all templates, 30 candidates, then a late-interaction reranker.

Problem: Three terms arrive undefined in one sentence, and the "interval" two sentences later is never said to be a bootstrap interval.

Source: reports/loggy-report/briefs/semantic-2.md:13 ("Dense model scans all templates by exact cosine, returns 30 candidates, late-interaction model reranks them by MaxSim"); reports/loggy-report/briefs/semantic-2.md:47 (bootstrap intervals)

Fix: "The standing recommendation has two steps. First a dense scan: the search compares the question's vector with every template's vector and keeps the 30 nearest. Then a reranker scores those 30 word by word and reorders them." Call the interval a bootstrap interval where it appears.

### S24. reports/loggy-report/sections/06-eval-b.tex:34

Quote:

> An embedding is paid once per template, not per line.

Problem: The passive hides who pays, and "an embedding is paid" is not what a reader can picture.

Source: .claude/skills/humanizer/SKILL.md:186 (passive voice and missing subjects); reports/loggy-report/briefs/style.md:47

Fix: "The platform computes one embedding per template, not one per line. An embedding is a vector that stands for the template's meaning."

### S25. reports/loggy-report/sections/06-eval-b.tex:11

Quote:

> The tree stops learning at 20,000 templates, inside the 512~MB unit.

Problem: "unit" is deployment shorthand for the service's memory limit, and the report never uses the word that way anywhere else.

Source: reports/loggy-report/briefs/soak-5.md:34 ("20,000 clusters: 61.5 MB tree, 208.1 MB peak serialising ... unit limit 512 MB")

Fix: "The tree stops learning at 20,000 templates. At that ceiling it peaks at 208.1~MB, inside the stamper's 512~MB memory limit."

### S26. reports/loggy-report/sections/07-limits-close.tex:11

Quote:

> The archive's busiest worker-second is 78~records, the plan's burst figure 10,000 to 20,000, and that gap is unsettled.

Problem: "the plan" points at an internal document the reader has never met, and the second clause has no verb.

Source: reports/loggy-report/briefs/soak-index.md:1 ("The plan's 1,000 a second was a farm figure read as a worker figure")

Fix: "The archive's busiest worker-second is 78~records. The design target for a burst is 10,000 to 20,000 a second. Nothing measured a real burst, so the gap stands."

### S27. reports/loggy-report/sections/07-limits-close.tex:17

Quote:

> Detector warm-up needs about 32 consecutive live intervals, and no run measured its length.

Problem: The number 32 carries no meaning because the length of an interval is never given.

Source: reports/loggy-report/briefs/why.md:178 ("Warm-up needs about 32 consecutive live intervals"); reports/loggy-report/briefs/walkthroughs-6-9.md:27 ("32-window RCF warm-up", one-minute windows)

Fix: "A detector needs about 32 consecutive one-minute windows of live data before it scores, so roughly half an hour. No run measured how long the scores take to mean something."

### S28. reports/loggy-report/sections/07-limits-close.tex:39

Quote:

> First, the live-lane bus, agreed and not built: one topic from every collector, the shifter view its one consumer, broker placement undecided.

Problem: Nine ordinals run through one paragraph of verbless clauses, where the style and the skill both ask for a list.

Source: .claude/skills/simple-english/SKILL.md:42 (a vertical list is for three or more parallel items or steps); reports/loggy-report/briefs/style.md:105 (end with an explicit "what remains" list)

Fix: Set the nine items as a numbered vertical list, one full sentence each with its status mark: "1. The live-lane bus, agreed and not built. Every collector writes one topic and the shifter view is the only consumer. Broker placement is undecided."

### S29. reports/loggy-report/sections/02-design-a.tex:55

Quote:

> \caption{Five steps on the worker, and 64 chunks in memory bound the collector's memory.}

Problem: The caption is a fragment joined to a clause and repeats "memory" twice, so it states no point.

Source: reports/loggy-report/briefs/style.md:82 (captions are full sentences that state the point of the figure)

Fix: \caption{A line takes five steps inside the collector, and the 64 chunks it may hold in memory bound what it can cost the worker.}

### S30. reports/loggy-report/sections/07-limits-close.tex:78

Quote:

> \caption{The review rounds also made the product stamp a document identifier and write with create.}

Problem: The caption states one incidental result instead of the point of a table that lists every round, and "also" refers to nothing.

Source: reports/loggy-report/briefs/style.md:82

Fix: \caption{Twenty-one rounds ran, and most of them changed the product rather than confirming it.}

### S31. reports/loggy-report/sections/01-intro-problem.tex:5

Quote:

> Most of what a machine writes never takes that road (Figure~\ref{fig:today}): the process logs on disk and the journal, the log of every system service.

Problem: 26 words, over the ceiling, and the colon list makes the reader hold two ideas at once.

Source: reports/loggy-report/briefs/style.md:11; reports/loggy-report/briefs/style.md:8 ("That is one road. Most of what a node writes never takes it.")

Fix: "Most of what a machine writes never takes that road (Figure~\ref{fig:today}). The process logs sit on disk. The journal holds the log of every system service."

### S32. reports/loggy-report/sections/01-intro-problem.tex:9

Quote:

> The platform runs on five staging machines, and one deployment went green on three farm workers and one infra machine.

Problem: "went green" is project shorthand for a deployment run that ended with every check passing, and the report never says so.

Source: reports/loggy-report/briefs/style.md:79 (name the component by its job, no internal names); reports/loggy-report/outline.md:144 ("one green run on the farm")

Fix: "The platform runs on five staging machines. One deployment run finished with every check passing on three farm workers and one infra machine." Repair the same phrase in the conclusion.

### S33. reports/loggy-report/sections/07-limits-close.tex:29

Quote:

> The live lane matches InfoLogger and the central family, so after the severity fix of Section~\ref{sec:eval-sources} it will not see stdout info lines.

Problem: "stdout" is shell shorthand that appears once and is never defined, where the report elsewhere says "the O2 process tree".

Source: reports/loggy-report/sections/01-intro-problem.tex:26 ("one output and one error file each"); reports/loggy-report/briefs/style.md:43 (prefer the concrete noun)

Fix: "... so after the severity fix of Section~\ref{sec:eval-sources} it will not see the info lines from the process output files."

### S34. reports/loggy-report/sections/02-design-a.tex:13

Quote:

> A moved viewer then moves one consumer, not 200 collector configurations.

Problem: The subject "a moved viewer" cannot move a consumer, and "viewer" is a third name for the shifter view.

Source: reports/loggy-report/briefs/target-architecture.md:130 ("Today moving the live lane means editing 200+ workers")

Fix: "Move the shifter view to another machine and one consumer moves with it. Today the same move edits more than 200 collector configurations."

## Nit

### N1. reports/loggy-report/sections/02-design-a.tex:7

Quote:

> The worker tier runs on every EPN and has four parts.

Problem: The reader counts three parts in the three sentences that follow, because the stamper hides in a subordinate clause.

Source: reports/loggy-report/briefs/target-architecture.md:11 (collector, stamper, health sample, local node)

Fix: "The worker tier runs on every EPN: a collector, a stamper beside it, a health sample and a local search node." Then take each in turn.

### N2. reports/loggy-report/sections/06-eval-b.tex:5

Quote:

> Drain3, the parser we keep, mined the archive into 3,822 templates at 19.91~core-seconds per million

Problem: The tool is spelled "Drain3" here and "drain3" in 3.5 and 4, so one name has two forms.

Source: reports/loggy-report/sections/02-design-a.tex:71 ("The stamper runs drain3, a template miner"); .claude/skills/simple-english/SKILL.md:39

Fix: Use "drain3" everywhere, and open the sentence with the subject: "The parser we keep, drain3, mined the archive into 3,822 templates ..."

### N3. reports/loggy-report/sections/04-why.tex:41

Quote:

> We split by severity, not by source, because info is the bulk and the trash and stays local.

Problem: "the trash" is deck shorthand that carries a judgement the report never supports, and it sits in the same clause as a fact.

Source: reports/loggy-report/briefs/style.md:57 ("That puts the volume on one side and the value on the other.")

Fix: "We split by severity, not by source, because the info lines are the bulk and the cheap half, so they stay local."
