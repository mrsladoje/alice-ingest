# Round 3 review: voice

Lens: voice. Read against `reports/loggy-report/briefs/style.md`, `.claude/skills/simple-english/SKILL.md` (Document rules, British spelling override) and `.claude/skills/humanizer/SKILL.md`, then all eight parts in full.

## Clean at this pass

The sentence-length script prints no prose hit in any of the eight parts. Every hit it returns is a table body, which is exempt. The longest prose sentences are 25 words (`05-eval-a.tex:19` and `07-limits-close.tex:31`), so both sit on the ceiling and neither breaks it.

Also clean, with no findings: banned modals (`should`, `would`, `may`, `might`, `could` return nothing), semicolons, em dashes and en dashes in prose, intensifiers and sales words, American spelling (licence, colour, catalogue, normalises, analysed, judgements are all consistent, and no `-ize` form survives), index names outside `\code{}`, captions that are not full sentences (all sixteen are full sentences that state a point), version numbers (only release 4.0.1, release 5.0.8 and the two OpenSSL releases, all inside the two allowed results), supervision amounts, August dates and merge requests.

Round-2 findings that are fixed: the 34-word dead-collector sentence, the 27-word and 26-word catalogue sentences, the roster modal, the collector caption modal, "clusters" for template groups, the "no multiple stands" sentence, the two Kafka conditions, the Section 2 opening claim, the ops page, the log-detector clock, the round-number opener in 5.2, the dangling "Fixed, severity comes out", the unitless 13.8, "the results file", "green run", "sink", inhibition, the four counterfactual modals, and the verbless "31 times a small transformer".

Round-2 findings still present: number 13 (the page and warn tiers are still used and never defined) and number 18 in a new form (a line format is now counted as a log family).

## Must

### 1. The three lanes are announced and never named
- File: reports/loggy-report/sections/03-design-b.tex:5
- Quote: "Three lanes read numbers: health samples, and counts, error rates and lags from the log records."
- Problem: the colon lists the numbers the lanes read, not the lanes, so the reader never learns that the three lanes are threshold monitors, Random Cut Forest detectors and trend monitors, which Section 3.8 and the Figure 6 caption both lean on.
- Source: reports/loggy-report/briefs/why.md:172 ("three lanes read numbers about the logs: 17 Random Cut Forest detectors, hard-rule monitors, and trend monitors against a frozen seven-day baseline"); reports/loggy-report/briefs/style.md:65 (give the plain goal of a component before its details)
- Fix: "Three lanes read numbers: threshold monitors, Random Cut Forest detectors and trend monitors. The numbers are the health samples, and the counts, error rates and lags taken from the log records."
- Severity: must

### 2. The page tier and the warn tier are never defined, and "page" carries two meanings
- File: reports/loggy-report/sections/03-design-b.tex:5
- Quote: "Threshold monitors catch cliffs in two tiers: a storage disk above 92~\% pages, and above 85 up to 92~\% warns."
- Problem: "pages" and "warns" arrive as verbs for two alert tiers that the report never defines, the same words then return as nouns at 03-design-b.tex:13, 19, 21 and 27, and "page" already means a screen at 02-design-a.tex:11 and 03-design-b.tex:31.
- Source: .claude/skills/simple-english/SKILL.md:39 (one word, one meaning, for the whole document) and :40 (define a concept term at its first use); reports/loggy-report/briefs/walkthroughs-3-5.md:121 ("That is severity 2, the warn tier ... severity 1, the page tier"); reports/loggy-report/reviews/round2-voice.md finding 13, same objection, not applied
- Fix: define the two tiers once, then use them: "An alert carries one of two tiers. A page wakes a person, a warn waits for one. Threshold monitors catch cliffs in both tiers: a storage disk above 92~\% raises a page, and above 85 up to 92~\% raises a warn."
- Severity: must

### 3. "It stores each episode" attaches to the episode, not to the projector
- File: reports/loggy-report/sections/03-design-b.tex:9
- Quote: "It stores each episode as one incident document, grouping the signals of one alert name on one entity."
- Problem: the nearest subject is "An episode", so the sentence reads as an episode storing itself, and the "-ing" rider after the comma is the form the document rules forbid.
- Source: .claude/skills/simple-english/SKILL.md:35 (name the actor, no "-ing" verb after a comma) and :37; reports/loggy-report/briefs/walkthroughs-6-9.md:45 (the projector holds the per-host state in the episode documents)
- Fix: "The projector stores each episode as one incident document. That document groups the signals of one alert name on one entity."
- Severity: must

### 4. A fourteen-sentence paragraph carries the whole storage tier
- File: reports/loggy-report/sections/02-design-a.tex:11
- Quote: "The storage tier is three replicated nodes, each a cluster manager and a data node."
- Problem: the paragraph that opens with this sentence runs to fourteen sentences and four topics (quorum, the three host roles, the services on each, and the two layouts), against a ceiling of six sentences and one topic, and four of its sentences ("The poller samples the cluster. The roster lists ... The catalogue maintenance audits ... The receiver stores ...") repeat one opening shape.
- Source: .claude/skills/simple-english/SKILL.md:32 (one topic per paragraph, six sentences per paragraph at most); .claude/skills/humanizer/SKILL.md:151 (repeated sentence openings)
- Fix: break it into three paragraphs: the tier and its quorum, the three hosts with the services each runs, and the two layouts. Merge the four repeated openings: "The control host also runs the poller, the roster of collectors that must run, the catalogue maintenance and the receiver."
- Severity: must

### 5. "or each file lands once per machine" states a false alternative
- File: reports/loggy-report/sections/01-intro-problem.tex:36
- Quote: "Each collector tails only its own machine's directory, or each file lands once per machine."
- Problem: the clause after "or" is the bad outcome of not doing the first clause, but "or" plus the present indicative reads as a second design that is equally available, so the reader concludes duplication is an option rather than the fault being avoided.
- Source: reports/loggy-report/briefs/constraints.md:85 (the run directory is one shared disk every machine mounts); .claude/skills/simple-english/SKILL.md:34 (condition before command, with a comma)
- Fix: "Each collector tails only its own machine's directory. If a collector tailed the whole disk, every machine collects every file."
- Severity: must

### 6. "Worker", the report's most used noun, is never defined
- File: reports/loggy-report/sections/01-intro-problem.tex:9
- Quote: "We built \loggy{}, a platform that keeps the bulk of the logs on the worker that wrote them and still searches them as one."
- Problem: "worker" appears 73 times across the eight parts and no sentence ever says it is an Event Processing Node, so a reader who has just met "EPN" cannot tell whether a worker is the same machine or a different class of machine.
- Source: reports/loggy-report/briefs/style.md:80 ("A worker is an EPN machine"); .claude/skills/simple-english/SKILL.md:40 (define a concept term at its first use)
- Fix: add the gloss at the first use in the introduction: "We built \loggy{}, a platform that keeps the bulk of the logs on the worker, the EPN that wrote them, and still searches them as one."
- Severity: must

### 7. "Detector" means a piece of the experiment and a scoring model
- File: reports/loggy-report/sections/01-intro-problem.tex:3
- Quote: "Its job is timeframe reconstruction: it assembles the detector data of one time slice into events."
- Problem: the introduction uses "detector" in its ALICE sense, and Section 3.6 then defines the same word as a Random Cut Forest model, so "Seventeen detectors run" and "six of the seventeen detectors" collide with the meaning a CERN reader already holds.
- Source: .claude/skills/simple-english/SKILL.md:39 (one word, one meaning, for the whole document); reports/loggy-report/sections/03-design-b.tex:5 ("A detector is a scheduled Random Cut Forest model")
- Fix: keep one meaning for the word. In the introduction write "it assembles the data of one time slice from the experiment into events", and keep "detector" for the Random Cut Forest models everywhere after that.
- Severity: must

## Should

### 8. A line format is counted as one of the five log families
- File: reports/loggy-report/sections/05-eval-a.tex:54
- Quote: "The collector had never read three of the five families. Those are the second process-tree format, written by the data-distribution processes, the InfoLogger daemon log and the system journal."
- Problem: the second process-tree format is one of two formats inside one family, not a family, so the reader counts six families here against five in Section 2.1, and Section 3.1 adds a third name for the same set, "the five log sources".
- Source: reports/loggy-report/briefs/constraints.md:85 ("A worker holds five log families. The O2 process tree uses two line formats"); reports/loggy-report/reviews/round2-voice.md finding 18, same collision in a new form
- Fix: "The collector had never read three things: the second process-tree format, written by the data-distribution processes, the InfoLogger daemon log and the system journal." Then make Section 3.1 say "the five log families" rather than "the five log sources".
- Severity: should

### 9. The Table 1 caption counts five families over six rows
- File: reports/loggy-report/sections/01-intro-problem.tex:21
- Quote: "Only one of the five families takes the InfoLogger road."
- Problem: the table under the caption has six rows, and nothing tells the reader that the run orchestrator log is the row a worker does not hold, so the caption's count reads as wrong on sight.
- Source: reports/loggy-report/briefs/constraints.md:85; reports/loggy-report/briefs/style.md:82 (a caption is a full sentence that states the point of the figure)
- Fix: "Only one of the five families a worker holds takes the InfoLogger road, and the run orchestrator log is not on a worker at all."
- Severity: should

### 10. "The archive" names three different things
- File: reports/loggy-report/sections/01-intro-problem.tex:38
- Quote: "The archive we replay is recorded farm logs, fed back through the collector at a chosen rate."
- Problem: this is the replayed stored logs, 01-intro-problem.tex:42 uses "the InfoLogger archive" for the six months of dumps, 02-design-a.tex:71 uses "the whole archive" for the mined corpora and 03-design-b.tex:31 uses "the archive" for what Discover searches, so one word carries four referents and the reader cannot tell which number belongs to which body of data.
- Source: task decision on corpus names ("the InfoLogger archive", "the parser corpus", "the stored logs"); .claude/skills/simple-english/SKILL.md:39
- Fix: "The stored logs we replay are recorded farm logs, fed back through the collector at a chosen rate." Then use "the InfoLogger archive" at every place that means the six months of dumps, "the parser corpus" for the 45,596,613 lines, and "the indices" where Discover is meant.
- Severity: should

### 11. "The stale monitor" and "The two" both drop their noun
- File: reports/loggy-report/sections/03-design-b.tex:15
- Quote: "The two watching the projector and Alertmanager take the break-glass path and post straight to the receiver, because that part can be dead. The stale monitor fires after ten minutes without a projector heartbeat."
- Problem: "The two" never says two what, and "the stale monitor" reads as a monitor that has gone stale, while Section 6.2 calls the same rule "the stale-projector monitor".
- Source: .claude/skills/simple-english/SKILL.md:37 (keep articles, keep "that", no telegraph style); reports/loggy-report/briefs/walkthroughs-6-9.md:107 (signal-projector-stale fires when no projector document lands in ten minutes); reports/loggy-report/sections/07-limits-close.tex:21 uses "The stale-projector monitor"
- Fix: "The two monitors that watch the projector and Alertmanager post straight to the receiver, because the part they watch can be the dead part. That is the break-glass path. The stale-projector monitor fires after ten minutes without a projector heartbeat."
- Severity: should

### 12. Section 3.5 opens on a detail and on an actor that does not exist yet
- File: reports/loggy-report/sections/02-design-a.tex:67
- Quote: "A template widens as the miner sees more lines, and each state is one version, the field a record carries (Figure~\ref{fig:templates})."
- Problem: the subsection that exists to show templates being named on the worker opens on version bookkeeping, names "the miner" a paragraph before drain3 is introduced, and ends on "the field a record carries", an appositive with no verb.
- Source: reports/loggy-report/briefs/style.md:7 (state the conclusion first, then the support) and :65 (give the plain goal of a component before its details)
- Fix: "The stamper names every line's template before the line is indexed. A template widens as the stamper sees more lines. Each state is one version, and every record carries that version as a field."
- Severity: should

### 13. Two sentences are pasted verbatim into two sections
- File: reports/loggy-report/sections/04-why.tex:39
- Quote: "The farm inventory sets three primaries, one per storage node, because one primary funnels all InfoLogger through one machine. No farm run has carried real volume."
- Problem: both sentences already stand word for word at 02-design-a.tex:15, and "Nothing pages a human today." is likewise repeated word for word at 02-design-a.tex:11 and 03-design-b.tex:13, which reads as a paste rather than as a second argument.
- Source: .claude/skills/humanizer/SKILL.md:75 (a closer repeated after several sections is a tell); reports/loggy-report/briefs/style.md:69 (close a section on a fact, never on a summary)
- Fix: keep the pair in Section 3.1, where the layout is set, and cut it from Section 4.4, which already carries the shard arithmetic that explains it. Keep "Nothing pages a human today." once, in Section 3.6 where Alertmanager is described.
- Severity: should

### 14. "The injection harness waits 32" gives a number with no unit
- File: reports/loggy-report/sections/07-limits-close.tex:15
- Quote: "A detector needs a few hundred one-minute windows, three to five hours, before its grades mean anything. The injection harness waits 32. Neither was measured."
- Problem: 32 carries no unit, so the reader has to carry "one-minute windows" across a sentence boundary, and "Neither" then points at two things that were never set out as a pair.
- Source: reports/loggy-report/briefs/style.md:28 (every number carries its meaning and its reference); .claude/skills/simple-english/SKILL.md:37
- Fix: "A detector needs a few hundred one-minute windows, three to five hours, before its grades mean anything. The injection harness waits 32 windows. Neither the warm-up nor the wait was measured."
- Severity: should

### 15. A dangling modifier makes Puppet right for every machine
- File: reports/loggy-report/sections/04-why.tex:63
- Quote: "Puppet wants an agent and a certificate on every machine, likely right for the farm and wrong for five machines one person deploys by hand."
- Problem: "likely right for the farm" attaches to "every machine", the nearest noun, so the judgement that belongs to Puppet reads as a judgement about the machines.
- Source: reports/loggy-report/briefs/style.md:18 ("Puppet is likely right for the farm. It is wrong for five machines that one person deploys by hand"); .claude/skills/simple-english/SKILL.md:35
- Fix: "Puppet wants an agent and a certificate on every machine. That is likely right for the farm. It is wrong for five machines that one person deploys by hand."
- Severity: should

### 16. "because three reach about 135" leaves out both the noun and the unit
- File: reports/loggy-report/sections/04-why.tex:39
- Quote: "A heap holds roughly 20 shards per gigabyte, so three 1~GB heaps hold about 60. Staging runs one primary, because three reach about 135 at full retention."
- Problem: "three" has no noun and 135 has no unit, so the reader must guess that three primaries produce about 135 shards against a capacity of about 60.
- Source: reports/loggy-report/briefs/style.md:28; .claude/skills/simple-english/SKILL.md:37
- Fix: "A heap holds roughly 20 shards per gigabyte, so three 1~GB heaps hold about 60 shards. Staging runs one primary, because three primaries reach about 135 shards at full retention."
- Severity: should

### 17. An index name stands in for a log family
- File: reports/loggy-report/sections/06-eval-b.tex:7
- Quote: "Contentless \code{infologger} templates fell from 44.9 to 10.4\,\%."
- Problem: `\code{infologger}` is the storage index named in Table 3, and here it means the InfoLogger log family, so the reader concludes a family and an index are one thing; the same swap returns at 06-eval-b.tex:11, where `\code{infologger}` is set beside "the process tree" in plain text.
- Source: reports/loggy-report/sections/02-design-a.tex:24 (`\code{infologger}` is the index that holds every InfoLogger record); .claude/skills/simple-english/SKILL.md:39
- Fix: "Contentless InfoLogger templates, those whose every token is a wildcard, fell from 44.9 to 10.4\,\% of that family's templates." Use plain "InfoLogger" and "the process tree" at 06-eval-b.tex:11 as well.
- Severity: should

### 18. Two headline terms in the abstract carry no reference
- File: reports/loggy-report/sections/00-abstract.tex:1
- Quote: "Dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634. The main limit: every cost figure comes from a laptop, so only shapes, knees and rankings transfer to the farm."
- Problem: 0.634 has no referent, so the reader cannot tell what the gain is measured against, and "knees" is never defined here or at 05-eval-a.tex:3, where the same three words carry the report's main caveat.
- Source: reports/loggy-report/outline.md:34 ("0.685 nDCG@10 against 0.634 for the dense model alone"); reports/loggy-report/briefs/style.md:28
- Fix: "Dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634 for the dense scan alone. The main limit: every cost figure comes from a laptop, so only the shape of a curve, the rate at which it bends and the ranking of the settings transfer to the farm."
- Severity: should

### 19. "Lag" is measured, compared and alerted on, and never defined
- File: reports/loggy-report/sections/03-design-b.tex:25
- Quote: "Each row holds record, error and fatal counts and the 95th percentile of two lags."
- Problem: the report never says which two lags or what they measure, and two of the nine trend rules then fire on one of them at 03-design-b.tex:27, so the reader cannot tell what the alert is about.
- Source: reports/loggy-report/briefs/walkthroughs-3-5.md:163 ("takes the 95th percentile of the entry lag and the shipping lag"); reports/loggy-report/briefs/why.md:342 (shipping lag is valid on replay, entry lag is not); .claude/skills/simple-english/SKILL.md:40
- Fix: "Each row holds record, error and fatal counts and the 95th percentile of two lags: the time from the line being written to the collector accepting it, and the time from acceptance to the record being stored."
- Severity: should

### 20. Alertmanager is listed as a screen
- File: reports/loggy-report/sections/02-design-a.tex:11
- Quote: "The control host holds every screen: Dashboards, and Alertmanager, which decides when a person is told."
- Problem: Alertmanager has no screen a person opens, and Section 3.9 says every surface is Dashboards or the shifter view, so listing it after "every screen" contradicts the surfaces paragraph.
- Source: reports/loggy-report/briefs/target-architecture.md:54 ("Alertmanager | Decides when someone is told and holds no state of what is true"); reports/loggy-report/sections/03-design-b.tex:31 ("one address, one account, four surfaces", all of them Dashboards or the shifter view)
- Fix: "The control host holds Dashboards, the only screen on it. It also runs Alertmanager, which decides when a person is told."
- Severity: should

## Nothing to report

No nits are listed, as instructed. Two remain unfixed from round 2 and are left out on that rule: the core-second is defined twice (04-why.tex:48 and 05-eval-a.tex:7), and three sentences still open on a numeral (05-eval-a.tex:54, 06-eval-b.tex:28 and 06-eval-b.tex:9).
