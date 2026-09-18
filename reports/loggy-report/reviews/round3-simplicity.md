# Round 3 review — lens: simplicity

Reader profile: knows Linux and search engines. Does not know ALICE, O2, OpenSearch, Fluent Bit,
drain3, Alertmanager. Read the eight parts in order, in full.

Verdict: the round-2 fixes landed. What is left is of three kinds. Two statements of status now
contradict each other across sections (the text routes, the Telegraf concession). One organising
sentence promises three lanes and names none. And a handful of counts and terms still arrive with
no reference a reader of this report can reach: "the whole archive", "the three corpora", "two
lags", "output", "contentless", "waits 32".

Counts: 5 must, 16 should. Nits omitted as asked.

---

## MUST

### M1. "Three lanes" are promised and never named
- **File:** sections/03-design-b.tex:5
- **Quote:** `Three lanes read numbers: health samples, and counts, error rates and lags from the log records.`
- **Problem:** the colon promises three lanes and delivers two kinds of number, so the reader never
  learns which three lanes exist or what each is for.
- **Source:** briefs/why.md:172 — "three lanes read numbers about the logs: 17 Random Cut Forest
  detectors, hard-rule monitors, and trend monitors against a frozen seven-day baseline";
  briefs/why.md:178 — "Static rules catch cliffs, the forest catches unfamiliar shapes and
  per-entity silence, trend monitors catch the drift the forest learns as normal."
- **Fix:** "Three lanes read those numbers. Threshold monitors catch the cliffs we know. Detectors
  catch unfamiliar shapes. Trend monitors catch slow drift. The numbers are health samples, and
  counts, error rates and lags from the log records."
- **Severity:** must

### M2. "Three of the five families" counts a line format as a family
- **File:** sections/05-eval-a.tex:54
- **Quote:** `The collector had never read three of the five families.`
- **Problem:** Table 1 makes the O2 process tree one family, so the reader cannot fit "the second
  process-tree format" into the five and loses the family count that Sections 2 and 3 rest on.
- **Source:** briefs/soak-4.md:216 — "Three of six log sources had never been read by the
  collector, and the fourth was two formats"; briefs/constraints.md:87 lists the five families with
  the process tree as one.
- **Fix:** "The collector had never read three of its log sources. Those are the second
  process-tree format, written by the data-distribution processes, the InfoLogger daemon log and
  the system journal."
- **Severity:** must

### M3. The report gives two different answers on detection over the log text
- **File:** sections/04-why.tex:51
- **Quote:** `Three routes for text stay open, and none is chosen.`
- **Problem:** Section 6.3 says one route stays open and two lost, so the reader cannot tell whether
  anything was decided about reading the log text.
- **Source:** briefs/why.md:176 — "Templating as the detector. Rejected because the signal runs out
  ... Templates then k-NN stays open"; the same brief rejects k-NN on raw log messages on cost. The
  report's own sections/07-limits-close.tex:46 states the surviving route.
- **Fix:** "One route stays open: templates first, then nearest-neighbour search. Search on raw
  messages lost on cost, and the new-template alarm lost because the signal runs out."
- **Severity:** must

### M4. A warm-up number with no unit, and a "neither" with no pair
- **File:** sections/07-limits-close.tex:15
- **Quote:** `The injection harness waits 32.`
- **Problem:** 32 carries no unit at all, and "Neither was measured" names no two things, so the
  warm-up limit cannot be read.
- **Source:** briefs/why.md:178 — "Warm-up needs about 32 consecutive live intervals, which is why
  paced replay exists."
- **Fix:** "The injection harness waits 32 one-minute windows. Neither the real warm-up nor that
  wait was measured on a live feed."
- **Severity:** must

### M5. The control host holds "every screen", and the shifter host serves one
- **File:** sections/02-design-a.tex:11
- **Quote:** `The control host holds every screen: Dashboards, and Alertmanager, which decides when a person is told.`
- **Problem:** four sentences later the shifter host serves the shifter view, which is a screen, so
  the reader cannot place the surfaces, and the one web door of Section 3.9 is never introduced.
- **Source:** briefs/target-architecture.md:50 — "nginx is the single TLS door to it, the ops page,
  the live lane and Alertmanager"; briefs/target-architecture.md:11 — "OpenSearch Dashboards for the
  maintainer, behind one nginx door, and a shifter view".
- **Fix:** "The control host holds the one web door every person opens. Behind it sit Dashboards and
  Alertmanager, which decides when a person is told."
- **Severity:** must

---

## SHOULD

### S1. The shared-disk rule reads as harmless
- **File:** sections/01-intro-problem.tex:36
- **Quote:** `Each collector tails only its own machine's directory, or each file lands once per machine.`
- **Problem:** the consequence is inverted: "lands once per machine" sounds correct, while it means
  every machine indexes every file, so the reader misses why the rule exists.
- **Source:** briefs/constraints.md:188 — "A tail over `/scratch/jl/**` would ingest every file once
  per node. Each node tails only `/scratch/jl/*/$(hostname).internal/`."
- **Fix:** "Each collector tails only its own machine's directory. A tail over the whole disk would
  make every machine index every file."
- **Severity:** should

### S2. The live lane carries a sentence two pages before its definition
- **File:** sections/01-intro-problem.tex:73
- **Quote:** `We dropped its queue on the numbers in Section~\ref{sec:eval-kafka}, and the aggregator the queue fed, because the collectors feed the live lane directly.`
- **Problem:** the live lane is the reason a whole part of the 2025 design was dropped, and it is
  defined only at sections/02-design-a.tex:13.
- **Source:** sections/02-design-a.tex:13 — "The live lane is the second copy of every record that
  leaves the worker."
- **Fix:** "...because the collectors feed the live lane, the second copy of every record that
  leaves a worker, directly."
- **Severity:** should

### S3. Table 1 shows six rows against five families
- **File:** sections/01-intro-problem.tex:21
- **Quote:** `Only one of the five families takes the InfoLogger road.`
- **Problem:** the table lists six rows and nothing marks the run orchestrator log as outside the
  five, so the reader's count never matches the prose.
- **Source:** briefs/constraints.md:87 — the five families are InfoLogger, DDS, the O2 process tree,
  the daemon log and the journal; the orchestrator log sits on the shared infra machine.
- **Fix:** caption "Only one of a worker's five families takes the InfoLogger road", and set the last
  row's tier cell to "None, and not a worker family".
- **Severity:** should

### S4. "Output" is a collector part the report never explains
- **File:** sections/02-design-a.tex:61
- **Quote:** `The disk buffer is a hiccup layer of 256~MB per output: when the store does not answer, chunks spill to disk.`
- **Problem:** "output" then carries the create-action claim at 02:63, the Kafka decision at 04:60
  and the retracted ceiling at 07:11, and a reader outside this collector cannot picture one.
- **Source:** briefs/soak-2.md:22 — "the InfoLogger output, the only output that failed ... the other
  two outputs carry 20 % each".
- **Fix:** "An output is the collector's connection to one destination. Each output has a 256~MB disk
  buffer, a hiccup layer: when the store does not answer, chunks spill to disk."
- **Severity:** should

### S5. Neither hourly check states what it proves, and "neither shared index" names nothing
- **File:** sections/02-design-a.tex:73
- **Quote:** `The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals. It also proves that neither shared index holds more records of a template version than the workers stamped.`
- **Problem:** the reader gets two procedures with no goal, and the two shared indices are never
  named, so the second check has no subject.
- **Source:** sections/02-design-a.tex:23 and :24 name the two storage indices; briefs/why.md:157 —
  "Exact counting uses acknowledgement after journaling and deduplication by chunk identifier."
- **Fix:** "The first check proves that no count is lost: up to 5,000 hourly buckets sum to their
  family totals. The second proves that no record is counted twice. Neither the central index nor
  the InfoLogger index holds more records of a template version than the workers stamped."
- **Severity:** should

### S6. The template ceiling is 4.7 times a corpus the report never named
- **File:** sections/02-design-a.tex:71
- **Quote:** `The stamper holds at most 20,000 templates per worker, 4.7 times what the whole archive produced.`
- **Problem:** "the whole archive" is not one of the report's three named corpora, so the reader
  attaches 4.7 to the 248,828,513-record InfoLogger archive and cannot recover the real figure.
- **Source:** briefs/soak-5.md:38 — "20,000 templates; 4.7 times 4,221 ... the template count of the
  55,963,050-line archive pass"; briefs/soak-3.md:81 gives the 4,221.
- **Fix:** "The stamper holds at most 20,000 templates per worker, 4.7 times the 4,221 templates
  mined from 55,963,050 archive lines (Section~\ref{sec:eval-templating})."
- **Severity:** should

### S7. "Two lags" are never defined and never named
- **File:** sections/03-design-b.tex:25
- **Quote:** `Each row holds record, error and fatal counts and the 95th percentile of two lags.`
- **Problem:** two trend rules and part of the detector lane rest on lag, and the reader meets the
  word here with no definition and no pair.
- **Source:** reports/inputs/dataflow-atlas.md:18 (audited) — the pipeline "computes ingest_lag_ms
  and enter_system_lag_ms"; dataflow-atlas.md:601 defines shipping lag as ingest time minus
  collector time.
- **Fix:** "Each row holds record, error and fatal counts and the 95th percentile of two delays: the
  line's age when the collector read it, and the shipping time from the collector to the store."
- **Severity:** should

### S8. The one-cluster choice gives no fact that separates it from the alternative
- **File:** sections/04-why.tex:36
- **Quote:** `Many clusters joined by cross-cluster search, one query forwarded to all, do not scale to 100 machines.`
- **Problem:** the next sentence makes fanout the cost of any fan-out search, so the reader sees the
  same objection against the design that was chosen.
- **Source:** briefs/why.md:49 — "One cluster places shards itself, keeps one set of users and saved
  searches, and needs no federation layer of our own."
- **Fix:** add after the fanout sentence: "One cluster places its shards itself, keeps one set of
  users and saved searches, and needs no joining layer of our own."
- **Severity:** should

### S9. Table 4's third column holds two different kinds of thing
- **File:** sections/04-why.tex:9
- **Quote:** `Candidate & Language & Vendor figure & Governed by & Verdict \\`
- **Problem:** the column carries memory sizes for two rows, "none published" for a third and a data
  format for Telegraf, so the reader cannot read the table across.
- **Source:** briefs/why.md:18 — "Telegraf. Go, governed by InfluxData, a metrics agent ... It emits
  line protocol, which is a metric and not a log line."
- **Fix:** head the column "Memory at rest", put "not published" in Telegraf's cell, and move
  "metrics, not log lines" into its verdict.
- **Severity:** should

### S10. A store verdict reads as a count, not a reason
- **File:** sections/04-why.tex:29
- **Quote:** `ClickHouse & Apache 2.0 & counts on disk, no detection & one company & 17 detectors run by us \\`
- **Problem:** the verdict cell states a number of detectors, so the reader cannot see that the cost
  is building and running them ourselves.
- **Source:** briefs/why.md:38 — "It wins on disk and loses on machinery: we would run 17 detectors
  and 28 alerting rules ourselves."
- **Fix:** "we would build and run the detection ourselves".
- **Severity:** should

### S11. "We concede" in Section 4 against "is open" in Section 6
- **File:** sections/04-why.tex:66
- **Quote:** `CERN already runs Telegraf and Mimir for machine metrics, so we concede our cluster and node samples.`
- **Problem:** Section 6.3 calls the same decision open, so the reader cannot tell whether the
  cluster and node samples stay in the platform.
- **Source:** briefs/why.md:307 — "Status: built and measured (the poller). The Telegraf decision:
  open"; briefs/target-architecture.md:131 — "Whether a Telegraf OpenSearch input replaces those
  kinds is open."
- **Fix:** "CERN already runs Telegraf and Mimir for machine metrics, and our cluster and node
  samples overlap them. Whether that estate takes the samples over is open."
- **Severity:** should

### S12. "The stamper's cost is not measured" sits beside a section of stamper costs
- **File:** sections/04-why.tex:48
- **Quote:** `The stamper's cost is not measured.`
- **Problem:** Section 5.5 prices masking, mining and the transport hop in core-seconds, so the
  reader reads a flat contradiction instead of the real split between a bench run and the running
  service.
- **Source:** briefs/why.md:158 — "Status: built, not measured on the rig or the farm. The
  Forward-loop cost arm is open."
- **Fix:** "Every templating cost in Section~\ref{sec:eval-templating} comes from mining runs over
  stored lines. The stamper running beside a live collector was never priced."
- **Severity:** should

### S13. The instrument counts containers, and the platform was said to run native services
- **File:** sections/05-eval-a.tex:7
- **Quote:** `The instrument sums every container's processor time into core-seconds, one core busy for one second.`
- **Problem:** Section 4 says staging deliberately runs no container runtime, and the rig is never
  introduced as a containerised laptop copy, so the reader thinks the measurements came from the
  deployment.
- **Source:** sections/04-why.tex:42 — "Staging runs vendor packages as system services, because a
  container runtime on OpenStack is a second runtime."
- **Fix:** "The rig is the whole stack in containers on one laptop. The instrument sums every
  container's processor time into core-seconds, one core busy for one second."
- **Severity:** should

### S14. The threading arms are named in collector-internal words
- **File:** sections/05-eval-a.tex:34
- **Quote:** `Every threading arm cost more against the 1.61~\% floor: two collector processes 7.4~\%, threaded inputs 11.5~\%, filters in input processors 26.5~\%.`
- **Problem:** "threaded inputs" and "filters in input processors" name parts the report never
  describes, so the reader cannot tell what was tried or why the result matters.
- **Source:** briefs/soak-1.md:112 — "Arm t1, threaded inputs"; briefs/soak-1.md:114 — "Arm t2,
  filters moved into input processors"; briefs/soak-1.md:260 gives the three costs.
- **Fix:** "Every threading arm cost more against the 1.61~\% floor: a second collector process
  7.4~\%, a thread per log source 11.5~\%, and the parsing moved into those threads 26.5~\%."
- **Severity:** should

### S15. "Contentless" carries a readability result and is never defined
- **File:** sections/06-eval-b.tex:7
- **Quote:** `Contentless \code{infologger} templates fell from 44.9 to 10.4\,\%.`
- **Problem:** this is the only evidence that the per-family recipe is worth anything, and the reader
  cannot tell what a contentless template is.
- **Source:** briefs/templating-embedding.md:179 — "contentless templates fall from 44.9 % to 10.4 %
  on infologger and 26.4 % to 7.4 % on stdout"; briefs/soak-3.md:21 reads the same figure as
  readability.
- **Fix:** "Contentless templates, whose every word is a wildcard and which tell a reader nothing,
  fell from 44.9 to 10.4\,\% on \code{infologger}."
- **Severity:** should

### S16. "The three corpora" appears once, undefined
- **File:** sections/06-eval-b.tex:9
- **Quote:** `One tree across the three corpora behind the recipes, 55,963,050 lines, gave 4,221.`
- **Problem:** the report names the parser corpus and the InfoLogger archive, never three corpora, so
  the biggest template count has no stated source.
- **Source:** briefs/soak-3.md:81 — "55,963,050 lines, 4,221 templates ... One tree per family
  carried across all three corpora".
- **Fix:** "One tree carried across the parser corpus and two further archive pulls, 55,963,050 lines
  together, gave 4,221."
- **Severity:** should

### S17. The dense path has three names
- **File:** sections/06-eval-b.tex:40
- **Quote:** `Search box, nDCG@10 & 0.685 & 0.634 dense model alone \\`
- **Problem:** the abstract calls it dense retrieval, the prose the dense scan and the table the dense
  model, so a reader tracking the 0.634 baseline cannot be sure it is one thing.
- **Source:** sections/06-eval-b.tex:32 — "for the dense scan alone"; sections/00-abstract.tex:1 —
  "Dense retrieval with reranking".
- **Fix:** use "dense scan" in all three places.
- **Severity:** should

### S18. Semantic retrieval ships disabled and no reason is given
- **File:** sections/06-eval-b.tex:28
- **Quote:** `Semantic retrieval is built, evaluated, and shipped disabled by configuration (Table~\ref{tab:semantic}).`
- **Problem:** the subsection then shows it winning on every measure, so the reader finds a choice
  with no deciding fact behind it.
- **Source:** briefs/semantic-1.md:121 — "the recommendation is provisional. It does not become the
  production selection until a person judges the pool"; briefs/target-architecture.md:77 — "ships
  disabled on staging under a 384 MB memory ceiling".
- **Fix:** "...and shipped disabled by configuration, because the recommendation stands only until a
  person judges the retrieved templates on real questions."
- **Severity:** should

### S19. The abstract's headline score has no meaning and no baseline
- **File:** sections/00-abstract.tex:1
- **Quote:** `Dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634.`
- **Problem:** the abstract never says what the measure rewards or what 0.634 belongs to, so the one
  retrieval number a reader takes away cannot be read.
- **Source:** sections/06-eval-b.tex:32 — "normalised discounted cumulative gain at rank ten,
  nDCG@10, which rewards relevant results near the top", against "0.634 for the dense scan alone".
- **Fix:** "Reranking the 30 nearest templates scores 0.685 against 0.634 without it, on a
  search-quality measure where 1 is perfect."
- **Severity:** should

### S20. The episode grade pages "for the fleet" with no explanation
- **File:** sections/03-design-b.tex:9
- **Quote:** `A grade above 0.5 opens an episode. One monitor watches every grade and pages once for the fleet above 0.7.`
- **Problem:** every other rule names one entity, and this one names the fleet with no reason, so the
  reader cannot tell who is paged about what.
- **Source:** briefs/why.md:178 (episodes entry) — "The record opens at grade 0.5 while paging needs
  0.7"; the same entry notes "a keyless alert blames the monitor rather than the fleet".
- **Fix:** "A grade above 0.5 opens an episode. One monitor reads every detector's grades and pages
  once for the whole fleet when any grade passes 0.7."
- **Severity:** should

### S21. The two alert tiers are used throughout and never defined
- **File:** sections/03-design-b.tex:5
- **Quote:** `Threshold monitors catch cliffs in two tiers: a storage disk above 92~\% pages, and above 85 up to 92~\% warns.`
- **Problem:** page and warn then carry the projector, the Alertmanager timers and the trend lane,
  and the reader never learns that they are the two severities of a notification.
- **Source:** sections/03-design-b.tex:13 — "It holds a page 30~seconds and a warn five minutes";
  briefs/target-architecture.md:165 and the trend entry use the same two tiers.
- **Fix:** "An alert carries one of two tiers. A page asks for a person now, a warn waits in a batch.
  A storage disk above 92~\% pages, and above 85 up to 92~\% warns."
- **Severity:** should
