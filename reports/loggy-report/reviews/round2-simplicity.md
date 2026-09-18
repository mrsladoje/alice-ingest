# Round 2 review — lens: simplicity

Reader profile: knows Linux and search engines. Does not know ALICE, O2, OpenSearch, Fluent Bit,
drain3, Alertmanager. Read the eight parts in order, in full, as a reader would.

Verdict: the round-1 vocabulary fixes mostly landed. What the condense pass then did was cut the
tying sentences: the one that made five staging machines add up, the one that gave the shard
budget its rule, the one that separated the old template count from the new. Three self
contradictions now survive in the prose, and one of them says the shifter view both does and does
not query the cluster.

Counts: 5 must, 14 should, 5 nits.

---

## MUST

### M1. The prose still gives six machines and then says five
- **File:** sections/02-design-a.tex:11
- **Quote:** `Staging is five machines: two workers and three storage nodes.`
- **Problem:** the same paragraph introduces a storage tier of three nodes and then a control
  host, a background host and a shifter host, and no sentence says those three hosts are the
  three storage nodes, so the reader counts six machines and meets "five".
- **Source:** briefs/target-architecture.md:25 — "The control host is the first storage node
  (deploy/README.md:75-76)"; briefs/target-architecture.md:183 lists the control, background and
  shifter hosts as the storage tier's further jobs. Figure 2 already draws "storage node 1 ·
  control host". Round 1 raised this (round1-simplicity.md M6); the fix sentence is not in the
  text.
- **Fix:** before the host list write: "The three storage nodes carry three further jobs. The
  first is the control host, the second the background host, the third the shifter host."
- **Severity:** must

### M2. The stamper both evicts templates and stops learning, and "clusters" collides with the search cluster
- **File:** sections/02-design-a.tex:71
- **Quote:** `The stamper holds at most 20,000 clusters per worker and drops the least recently used.`
- **Problem:** Section 5.5 says the tree stops learning at that ceiling, so the report states two
  different behaviours, and "cluster" means the search cluster everywhere else in the report.
- **Source:** briefs/soak-5.md:185 — "The template tree ceiling is 20,000 templates; at the
  ceiling the worker stops learning and keeps counting; drain3's LRU eviction is not used. [A] |
  208 MB peak against 512 MB; eviction would reset counts to one"; briefs/soak-index.md:267 says
  the same. Only briefs/walkthroughs-6-9.md:83, which is unaudited, says the least recently used
  cluster is dropped.
- **Fix:** "The stamper holds at most 20,000 templates per worker. At that ceiling it stops
  learning and keeps counting, because dropping a template would reset its counts to one." Then
  delete the following sentence about the archive pass, which now says the same thing.
- **Severity:** must

### M3. The shifter view is said never to query the cluster, three pages after its query lane queries the cluster
- **File:** sections/03-design-b.tex:29
- **Quote:** `It never queries the cluster, so it holds when the cluster is down.`
- **Problem:** "It" reads as the shifter view, and walkthrough 1 step 7 has the shifter view's
  query lane reading the central index, so the reader cannot tell which claim is true.
- **Source:** reports/inputs/dataflow-atlas.md:22 (audited) — the live lane streams "with no
  cluster query at all"; dataflow-atlas.md:24 (audited) — "The Shifter's query lane searches the
  same central index, capped at 20,000 rows"; the report's own sections/02-design-a.tex:44.
- **Fix:** "Its live page never queries the cluster, so the page holds when the cluster is down.
  A second lane in the same page does search the central index, capped at 20,000 rows."
- **Severity:** must

### M4. "the archive" names three different things, and two of them carry headline numbers
- **File:** sections/06-eval-b.tex:5
- **Quote:** `The parser we keep, drain3, mined the archive into 3,822 templates at 19.91~core-seconds per million \cite{ref:drain,ref:drain3}.`
- **Problem:** here the archive is the 45,596,613-line parser corpus, in Section 2.2 it is
  248,828,513 InfoLogger records over six months, and in Section 3.9 it is whatever Discover
  searches, so the reader cannot reconcile any two archive numbers.
- **Source:** briefs/constraints.md:124 — "InfoLogger: 179 daily MySQL dumps ... 248,828,513
  records, 312 hosts"; briefs/constraints.md:125 — "The archive corpus used for parsers:
  45,596,613 lines, 186 programs"; the report's own sections/01-intro-problem.tex:36 already
  calls the second one "the corpus".
- **Fix:** keep "the InfoLogger archive" for the six-month dumps, "the parser corpus" for the
  45,596,613 lines, and "the stored logs" for what Discover searches. Here: "drain3 mined the
  parser corpus into 3,822 templates".
- **Severity:** must

### M5. A 34-word sentence breaks the length rule and drops its units
- **File:** sections/03-design-b.tex:19
- **Quote:** `From the death: the grace and the poll give the first missing flag after 60 to 150~seconds, the monitor adds up to 60, the projector up to 30, and the page wait 30.`
- **Problem:** 34 words against the 25-word rule, four numbers, and three of them with no unit, in
  the sentence that has to prove the 90 to 270 second bound.
- **Source:** the report's own rule of at most 25 words a sentence; the cadences in
  sections/03-design-b.tex:17 and briefs/target-architecture.md:163.
- **Fix:** "The first missing flag arrives 60 to 150~seconds after the death. The monitor adds up
  to 60~seconds, the projector up to 30, and the page wait another 30." Four other sentences run
  to 26 words: 01-intro-problem.tex:59, 02-design-a.tex:57, 02-design-a.tex:59 and
  02-design-a.tex:73 (twice).
- **Severity:** must

---

## SHOULD

### S1. Dashboards is used twice before anyone says what it is
- **File:** sections/01-intro-problem.tex:71
- **Quote:** `We replaced Grafana with Dashboards, because detection and alerting come with the store, OpenSearch~\cite{ref:opensearch}.`
- **Problem:** first use of a product name that a reader takes for a common noun; the gloss "the
  store's own web interface" arrives eight pages later at 03-design-b.tex:29, after two more uses.
- **Source:** reports/inputs/dataflow-atlas.md:24 — "The Discover view inside OpenSearch
  Dashboards"; round1-simplicity.md S3 asked for the gloss at first use.
- **Fix:** "We replaced Grafana with OpenSearch Dashboards, the store's own web interface".
- **Severity:** should

### S2. "template" carries the worker tier two pages before it is defined
- **File:** sections/02-design-a.tex:7
- **Quote:** `The stamper beside it names every line's template before indexing.`
- **Problem:** the definition is at 02-design-a.tex:67, after the component list and the whole
  error-line walkthrough, so the reader cannot judge what the stamper is for or why step 3 of the
  walkthrough exists.
- **Source:** the report's own definition at sections/02-design-a.tex:67 — "A template is the
  skeleton of a log line: its fixed words, every variable token a wildcard."
- **Fix:** gloss it here in six words: "names every line's template, the skeleton of the line with
  its variable parts masked, before indexing."
- **Severity:** should

### S3. "four surfaces" is followed by five named things, one of which has no owner
- **File:** sections/03-design-b.tex:29
- **Quote:** `The ops page counts families, alerts, anomaly results, incidents and signals.`
- **Problem:** the reader is promised four surfaces and meets the cockpit, Discover, the ops page,
  the live page and the Templates page, and the ops page appears once with no statement of who
  opens it or how it differs from the maintainer cockpit.
- **Source:** briefs/target-architecture.md:56 — "Ops page | The operator's status line plus the
  buttons that drive the test harness (atlas:387) ... built on staging; the buttons are tester
  tooling". Round 1 raised the same gap (round1-simplicity.md S14).
- **Fix:** either say "five surfaces" and give the ops page its reader ("a maintainer's status
  line over the platform's own indices"), or drop it from the prose and keep it in Figure 2.
- **Severity:** should

### S4. Two different seventeens sit in one paragraph
- **File:** sections/03-design-b.tex:5
- **Quote:** `Of 30 monitors, 17 are threshold and detector monitors, 13 every minute and four every ten.`
- **Problem:** the preceding sentence says "Seventeen detectors run", so the reader reads the two
  seventeens as the same set and concludes the detectors are monitors, which the same paragraph
  denies.
- **Source:** reports/inputs/dataflow-atlas.md:193-217 — 17 detectors are plugin jobs, while the
  30 alerting monitors are a separate list whose every-minute members are 15, two of them
  break-glass; briefs/target-architecture.md:203 keeps the two counts apart.
- **Fix:** avoid the collision: "Of the 30 monitors, 13 run every minute and four every ten on
  thresholds and on detector results. Nine are trend comparisons every ten minutes, two run hourly
  on templates and two are break-glass."
- **Severity:** should

### S5. Six detectors appear before a detector is defined, and never join the seventeen
- **File:** sections/02-design-a.tex:49
- **Quote:** `The trend rollup, six local anomaly detectors and the shifter view's line sampler read the local index.`
- **Problem:** "detector" is defined a page later, and the reader never learns that these six are
  part of the seventeen rather than a separate lane.
- **Source:** reports/inputs/dataflow-atlas.md:30 (audited walkthrough 2) — "six local-* detectors
  read it per minute"; dataflow-atlas.md:193 — fourteen log detectors and three metric detectors
  make the seventeen.
- **Fix:** "The trend rollup, the six detectors of Section~\ref{sec:design-watch} that read a
  worker's own logs, and the shifter view's line sampler read the local index."
- **Severity:** should

### S6. The shard budget lost the rule that gives 60 its meaning
- **File:** sections/04-why.tex:39
- **Quote:** `The shard budget is about 60 across three 1~GB heaps, so staging runs one primary.`
- **Problem:** 60 and the 135 in the next sentence are bare counts; the condense pass cut the
  conversion, so the reader cannot see why a 1 GB heap implies 20 shards.
- **Source:** briefs/why.md:70 — "shard budget is the binding constraint, roughly 20 shards per GB
  of heap, about 60 across three 1 GB nodes (deploy/README.md:2295-2296, docs/PLAN.md:245)".
- **Fix:** "A node carries roughly 20 shards per gigabyte of heap, so three 1~GB heaps hold about
  60. Staging therefore runs one primary, because three would reach about 135 at full retention."
- **Severity:** should

### S7. Four template counts stand in one subsection and only three share a configuration
- **File:** sections/06-eval-b.tex:11
- **Quote:** `One run of each family, 45,596,613 lines, gave 3,011 templates, a floor.`
- **Problem:** 3,822 appears six lines earlier on the same lines, and nothing says it belongs to
  the configuration before the recipe and the masker rewrite, so the reader reads a fall from
  3,822 to 3,011 as a corpus effect.
- **Source:** briefs/soak-3.md:59 — "3,011 templates on 45,596,613 lines [A] | Whole-corpus
  template count with recipe and fast masker | H1b's 3,822 | Fewer and better templates";
  briefs/soak-3.md:187 warns against placing a round-3 absolute beside a stage-I number.
- **Fix:** "The shipped parser before the rewrite gave 3,822 templates on those lines. With the
  per-family recipe and the fast masker, one run of each family gave 3,011, a floor."
- **Severity:** should

### S8. "the cap" in the overload paragraph reads as the rate ceiling
- **File:** sections/05-eval-a.tex:42
- **Quote:** `At 82~\% of the cap the collector absorbs the shortfall and loses nothing.`
- **Problem:** the sentence follows a paragraph about the 42,000 a second ceiling, so the reader
  takes "the cap" for a rate, while it is the 256 MB disk buffer four paragraphs earlier.
- **Source:** briefs/soak-2.md:41 — "queue 166 chunks, backlog 210.7 MB (82 % of cap), 0 dropped
  ... the 256 MB `storage.total_limit_size` cap"; soak-2.md:42 gives the 1.8 % and the 262.8 s
  drain at the same cap.
- **Fix:** "At 82~\% of the 256~MB buffer cap the collector absorbs the shortfall and loses
  nothing. At the cap it discards oldest-first ..."
- **Severity:** should

### S9. The claim that a resend cannot duplicate never says why
- **File:** sections/02-design-a.tex:63
- **Quote:** `The document identifier, node id plus boot id plus a counter, is the record's id in the store, and the output uses bulk action create.`
- **Problem:** the deciding fact is missing: a create action fails when the id already exists. A
  reader who does not know the store's write actions cannot see that a repeat write is refused,
  and the same claim carries the duplication fix in Section 5.4.
- **Source:** briefs/soak-index.md:323 and sections/05-eval-a.tex:60 — "A retried bulk write
  duplicated every record, so the product now writes with create under a stamped document
  identifier."
- **Fix:** add five words: "the output writes with create, which the store refuses when that id
  already exists."
- **Severity:** should

### S10. The embedding paragraph uses four unexplained words in one sentence
- **File:** sections/06-eval-b.tex:32
- **Quote:** `We rejected neighbour agreement with a reference model: an int8 model agrees with its own fp32 on only 0.636 of neighbours.`
- **Problem:** int8, fp32, "static model", "small transformer" and "backend" all arrive undefined,
  and this sentence is the reason a whole evaluation measure was thrown away.
- **Source:** briefs/semantic-2.md and briefs/semantic-1.md describe the ladder; the report defines
  neither number format nor either model family anywhere.
- **Fix:** one clause each: "an eight-bit compressed copy of a model disagrees with its own
  full-precision original on a third of its neighbours", and earlier "a static model, one fixed
  vector per word, against a small transformer, a model that reads the words in context".
- **Severity:** should

### S11. The unit of the whole semantic evaluation is never defined
- **File:** sections/06-eval-b.tex:30
- **Quote:** `It searches 5,301 template groups from a later corpus of 56,628,579 lines.`
- **Problem:** a reader who has met the cover relation in Section 3.5 assumes a template group is a
  template and its narrower versions, which is not what this number counts.
- **Source:** briefs/semantic-1.md:21 — "5,301 canonical template groups built from 18,011 source
  instances and 56,628,579 real log lines ... Collapsing 5,571 templates gives 5,301 groups: 270
  templates were absorbed, 627 groups have more than one source instance".
- **Fix:** "It searches 5,301 template groups, one per distinct template after templates that
  differ only in which program wrote them are collapsed together."
- **Severity:** should

### S12. What actually runs on the farm is settled in a passive sentence on the last page
- **File:** sections/07-limits-close.tex:33
- **Quote:** `Only the cluster, the collector and Dashboards are stated to run there.`
- **Problem:** the reader has carried "one deployment passed every check on three farm workers and
  one infra machine" since page one and has had no reason to doubt that the stamper, the projector
  and the shifter view run there; "are stated" also names no one who states it.
- **Source:** briefs/target-architecture.md:34, :66 and :75 — the stamper, the projector and the
  shifter view are each "built and running on staging" with the farm status "not stated in the
  sources read".
- **Fix:** say it where the farm pilot is introduced, in the active voice: "On the farm pilot only
  the cluster, the collectors and Dashboards have been confirmed running; the stamper, the
  projector and the shifter view have run on staging only."
- **Severity:** should

### S13. The one-cluster argument turns on a farm size the report never states
- **File:** sections/04-why.tex:36
- **Quote:** `Many clusters joined by cross-cluster search, one query forwarded to all, do not scale to 100 machines.`
- **Problem:** 100 machines here and 300 data nodes in the next sentence mean nothing, because the
  body never says how many workers the farm has; the only hint is "more than 200 collectors" in
  another section.
- **Source:** briefs/constraints.md:40 — "The task text calls production 'the 200-plus worker
  farm'. No source I read states a current head count. Mark the count as 'about 300 hosts appear in
  the six-month archive'"; briefs/constraints.md:307.
- **Fix:** give the size once in Section 1 or 2.2 — "about 300 worker machines appear in six months
  of archive" — and then these two numbers can be read.
- **Severity:** should

### S14. Walkthrough step 6 still names no machine for the live lane
- **File:** sections/02-design-a.tex:43
- **Quote:** `\item The collector also posts it over HTTP to the live lane, which keeps the last 500 in memory and pushes each to every open browser.`
- **Problem:** every other step names the machine; this one leaves the reader unable to place the
  live lane, in a walkthrough titled "from an EPN to a screen". "the last 500" also has no noun.
- **Source:** reports/inputs/dataflow-atlas.md:22 (audited) — "posted over HTTP to the live lane
  inside the Shifter on node-05. The Shifter keeps it in memory and streams it to every open
  browser over Server-Sent Events". Round 1 asked for the machine and the carrier
  (round1-simplicity.md M13).
- **Fix:** "The collector also posts it over HTTP to the live lane on the shifter host, which keeps
  the last 500 records in memory and streams each one to every open browser."
- **Severity:** should

### S15. The farm's three primaries are "set" in Section 3 and "not applied" in Section 4
- **File:** sections/02-design-a.tex:15
- **Quote:** `Three primaries are set for the farm and have never carried farm volume.`
- **Problem:** Section 4 says "The farm runs three primaries, agreed and not applied", so one of
  the two is wrong and the sentence in Section 4 contradicts itself as well.
- **Source:** briefs/why.md:68 — "Three primaries per storage family, as the plan asks. At 3
  primaries they reach about 135 shards at full retention against a budget of 60"; :70 — "The
  primary count is one setting and reaches an existing family at its next rollover."
- **Fix:** one status in both places: "The farm layout sets three primaries. It is agreed and has
  never carried farm volume."
- **Severity:** should

---

## NITS

### N1. A memory number loses its unit
- **File:** sections/02-design-a.tex:59
- **Quote:** `The documented worst case is twice that with a 20~\% margin, roughly 307~MB, so the service is throttled at 384~MB and killed at 768.`
- **Source:** briefs/constraints.md:182 gives the envelope in MB throughout.
- **Fix:** "killed at 768~MB".
- **Severity:** nit

### N2. The core-second is defined twice
- **File:** sections/04-why.tex:48
- **Quote:** `one core-second being one core busy for one second`
- **Problem:** Section 5.1 defines it again in the same words.
- **Source:** sections/05-eval-a.tex:7 — "The instrument sums every container's processor time into
  core-seconds, one core busy for one second."
- **Fix:** keep the definition at its first use in Section 4 and drop the repeat, or move the unit
  sentence of Section 5.1 to Section 4.
- **Severity:** nit

### N3. The flush result is stated in the design with no pointer to its measurement
- **File:** sections/02-design-a.tex:57
- **Quote:** `Writing to the local node every second instead of the shipped 5~seconds cut the collector's processor cost by 24.5~\% and its peak memory by 28.0~\%.`
- **Source:** sections/05-eval-a.tex:19 and Table 7 carry the evidence.
- **Fix:** add "(Section~\ref{sec:eval-collector})".
- **Severity:** nit

### N4. The core budget is stated twice in the same words
- **File:** sections/02-design-a.tex:7
- **Quote:** `The four take four of a worker's 64 physical cores, eight of its 128 logical processors.`
- **Source:** sections/01-intro-problem.tex:46 — "A worker gives the platform four of its 64
  physical cores, eight of its 128 logical processors, and little memory."
- **Fix:** in Section 3.1 write "The four share the worker's four-core budget of
  Section~\ref{sec:problem-constraints}".
- **Severity:** nit

### N5. A rate loses its unit
- **File:** sections/05-eval-a.tex:9
- **Quote:** `The re-run rig, the same laptop slowed, offered 5,000.`
- **Source:** briefs/soak-index.md:17 — "A 47-cell re-run on 27 August at 5,000 a second".
- **Fix:** "offered 5,000 records a second".
- **Severity:** nit
