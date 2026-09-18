# Round 1 review — lens: simplicity

Reader profile used: knows Linux and search engines. Does not know ALICE, O2, OpenSearch,
Fluent Bit, drain3, Alertmanager. Read the eight parts in order, in full, once, as a reader
would, and stopped at every place where the next sentence could not be understood from the
sentences before it.

Verdict: the report is readable in shape and unreadable in detail. Eight names carry the whole
design — the store, the shifter view, the catalog, a template version, the live lane, an
entity, the soak, O2 — and none of the eight is defined at the sentence where it first appears.
Three machines run the platform and only one of them is named in the prose; the other two exist
only inside the figures, so the five staging machines never add up.

Counts: 18 must, 24 should, 3 nits. 45 findings.

---

## MUST

### M1. "O2" is never expanded, anywhere
- **File:** reports/loggy-report/sections/01-intro-problem.tex:26
- **Quote:** `O2 process tree & Every reconstruction process, one output and one error file each, in two line formats & Tail of the files & By severity \\`
- **Problem:** O2 is the name of one of the five log families, the name of the processes in
  two walkthroughs and the name in the title of an uncited reference, and the report never
  says what the two characters mean.
- **Source:** reports/loggy-report/report/references.tex, `ref:o2tdr`: "Technical Design Report
  for the Upgrade of the Online--Offline Computing System" — the reference is in the
  bibliography and never cited.
- **Fix:** in 01-intro-problem.tex:3, after the timeframe sentence, add: "The reconstruction
  software is O2, the ALICE Online--Offline computing system~\cite{ref:o2tdr}." That also
  makes the unused reference used.

### M2. DDS is expanded wrongly
- **File:** reports/loggy-report/sections/01-intro-problem.tex:17
- **Quote:** `DDS is the distributed deployment system that starts the processing tasks.`
- **Problem:** DDS is the Dynamic Deployment System, not the distributed deployment system;
  a reader who looks it up finds a different product name.
- **Source:** ALICE O2 quickstart, "FairMQ and DPL"
  (https://aliceo2group.github.io/quickstart/fair-dpl.html): topologies are described "via the
  Dynamic Deployment System (DDS)"; A. Manafov, *DDS: The Dynamic Deployment System*.
  The briefs never expand it — briefs/constraints.md:100 gives only the file path.
- **Fix:** "DDS, the Dynamic Deployment System, starts the reconstruction processes on the
  node and writes one file a day."

### M3. "the soak" is used as a proper noun and never defined
- **File:** reports/loggy-report/sections/01-intro-problem.tex:44
- **Quote:** `The soak tested one collector at 1,000, 20,000 and 50,000 records a second per worker.`
- **Problem:** first use, three pages before Section 5 and fourteen pages before Appendix B,
  and the reader has no idea whether a soak is a program, a test rig or a period of time.
- **Source:** briefs/soak-index.md:1-3 — "Soak index: the five soak briefs reconciled.
  Sources: docs/SOAK.md (round 1) and docs/SOAK_RESULTS.md (rounds 2 to 21)".
- **Fix:** at first use write: "The soak is this project's measurement campaign: twenty-one
  rounds of load runs and code reviews, each round fixing what the round before it found
  (Appendix~\ref{app:soak})."

### M4. "the live lane" is used four pages before it is defined
- **File:** reports/loggy-report/sections/02-design-a.tex:13
- **Quote:** `The ghost in Figure~\ref{fig:arch} is a Kafka bus, agreed for the live lane only.`
- **Problem:** the phrase carries a whole design decision, a whole cost result and a whole
  build item, and its first appearance is inside a sentence about something else.
- **Source:** the definition exists, but only at
  reports/loggy-report/sections/05-eval-a.tex:36 — "The live lane, the second output that
  copies records to the shifter view".
- **Fix:** move the definition to its first use: "a Kafka bus for the live lane, the second
  copy of every severe record that the collector posts straight to the shifter view so a
  person can watch lines arrive without asking the cluster."

### M5. "shifter" is never explained, so "the shifter view" names nothing
- **File:** reports/loggy-report/sections/02-design-a.tex:43
- **Quote:** `\item The collector also posts the record to the shifter view's live lane, which holds the last 500 records.`
- **Problem:** the word appears eleven times across four sections. A shifter is the person on
  shift in the control room. A reader outside ALICE reads it as a verb or as a component name
  and never learns who the page is for.
- **Source:** briefs/target-architecture.md:183 names "the shifter host (shifter view)";
  outline.md:84 describes the surface as the one the operator watches.
- **Fix:** at first use: "the shifter view, the page the person on shift watches".

### M6. The prose never says the control host is one of the three storage nodes, so the machines do not add up
- **File:** reports/loggy-report/sections/02-design-a.tex:11
- **Quote:** `The control host runs Dashboards, the poller, the roster, the catalog maintenance, Alertmanager and the receiver.`
- **Problem:** the previous sentence says the storage tier is three nodes and staging is
  "three virtual machines beside two workers". The reader then meets a control host, and later
  a background host and a shifter host in the figures only. Counting the prose gives six or
  seven machines, while the introduction promises five. This is the report's worst structural
  gap: two names for one machine with no sentence tying them.
- **Source:** briefs/target-architecture.md:25 — "The control host is the first storage node
  (deploy/README.md:75-76)"; briefs/target-architecture.md:183 — "the control host (Dashboards,
  nginx, Alertmanager, receiver, poller, ops page), the background host (rollup, projector),
  the shifter host (shifter view)".
- **Fix:** add one sentence before the component list: "The three storage nodes carry three
  further jobs. The first is the control host and holds every screen. The second is the
  background host and runs the projector and the trend rollup. The third is the shifter host
  and runs the shifter view. Staging is therefore five machines: two workers and three storage
  nodes."

### M7. "the catalog" is never defined
- **File:** reports/loggy-report/sections/02-design-a.tex:73
- **Quote:** `The chain to the catalog has four stages: stamp, buckets, audits, rules.`
- **Problem:** "the catalog maintenance" appears first in the bare component list at
  02-design-a.tex:11, and the catalog itself is never said to be a thing that holds documents.
  The reader reaches "the catalog maintenance proves that up to 5,000 hourly buckets sum to
  their totals" without knowing what is being maintained.
- **Source:** docs/TEMPLATES_FIX_PLAN.md:209 — the catalog holds one definition document per
  template version, with labels attached to a version; briefs/walkthroughs-6-9.md:73 — "one
  check document per family into the catalog".
- **Fix:** define at 02-design-a.tex:73: "The catalog is one index holding one document per
  template the farm has ever seen, plus the results of the checks below."

### M8. "a template version" is never defined, although every count in Section 3.5 is per version
- **File:** reports/loggy-report/sections/02-design-a.tex:40
- **Quote:** `\item The stamper, over a local socket, sets a template version and status and hands the chunk back.`
- **Problem:** the reader has just been told what a template is, not that a template changes
  over time. Without that, "a count per version", "agree on versions per worker and hour" and
  the whole cover relation are unreadable.
- **Source:** docs/TEMPLATES_FIX_PLAN.md:205 — "Drain only widens, so every observed transition
  is a cover relation... one version can be widened into different successors on different
  workers when different tokens vary first."
- **Fix:** in 02-design-a.tex:67, after the definition of a template, add: "A template widens
  as the miner sees more lines: a word that was fixed becomes a wildcard. Each state of a
  template is one version, and a version is what a record carries."

### M9. The cover relation is defined without saying what it is for
- **File:** reports/loggy-report/sections/02-design-a.tex:75
- **Quote:** `Version W covers version N when they share a family and a token count and W has, at every position, a wildcard or N's token.`
- **Problem:** the paragraph's last-but-one sentence is a bare formal definition with no goal
  before it, in a report whose rule is to give the plain goal first. The reader cannot tell
  whether this is a search feature, a storage rule or a maintenance check.
- **Source:** docs/TEMPLATES_FIX_PLAN.md:54 — "Search expansion by the cover relation, computed
  in the Shifter. Drain only widens, so the structural relation contains every observed
  transition. It needs no stored link and no mapping change."; :209 — "The cover relation
  contains that grouping as a special case, so the identifier was removed."
- **Fix:** put the goal first: "One search for a template must find the records stamped with
  its older, narrower versions. A wider version covers a narrower one when they share a family
  and a token count and the wider has, at every position, a wildcard or the narrower's token.
  That relation groups a template's versions, so no separate identifier is stored."

### M10. 96.94 % is given two different meanings in three places
- **File:** reports/loggy-report/sections/02-design-a.tex:49
- **Quote:** `On staging the local tier held 96.94~\% of all lines.`
- **Problem:** Section 5 gives the same number as a share of one family only. A reader who
  believes Section 3 concludes that InfoLogger, the daemon log and the journal together are
  3 % of a worker's output, which the rest of the report contradicts. The third instance is
  04-why.tex:41, "The local tier holds 96.94~\% of lines."
- **Source:** briefs/soak-index.md:211 — "96.94 % / 3.06 % | Process-tree share that stays on
  the worker / crosses the network"; briefs/soak-4.md:23 gives the same scope, against a
  baseline of 0 % / 100 % before Round 7.
- **Fix:** in all three places write "of the O2 process tree", never "of all lines". At
  02-design-a.tex:49: "On staging 96.94~\% of the O2 process tree stayed on its worker."

### M11. "six log sources" contradicts "five log families", and "the data distribution log" names nothing the reader has met
- **File:** reports/loggy-report/sections/05-eval-a.tex:58
- **Quote:** `The collector had never read three of the six log sources. Later rounds added the data distribution log, the InfoLogger daemon log and the system journal.`
- **Problem:** Section 2.1 says five families and Table 1 shows six rows, one of which is not
  collected. Now there are six sources. Worse, "the data distribution log" is a seventh name:
  a reader maps it onto DDS, "the distributed deployment system", and is wrong.
- **Source:** briefs/soak-4.md:149 — "Six sources, each with a tier rule: `infologger` and
  `ildaemon` all durable, `dds`, `dpl`, `datadist`, `journald` informational local and
  warning-or-worse durable"; briefs/constraints.md:85 — "A worker holds five log families. The
  O2 process tree uses two line formats."
- **Fix:** name the split once in Section 2.1 — "The O2 process tree splits into two formats:
  the reconstruction processes, and the data-distribution process that has its own line shape"
  — then at 05-eval-a.tex:58 write "the data-distribution format" and keep one count of
  sources throughout.

### M12. The 15 % and the 6.5 % are labelled as the same measurement
- **File:** reports/loggy-report/sections/06-eval-b.tex:15
- **Quote:** `Splicing the stamped fields into a record's own bytes made the collector-to-stamper hop 15\,\% cheaper per record. Transport is about a quarter, so the whole hop fell 6.5\,\% on \code{infologger}.`
- **Problem:** the hop cannot fall 15 % and 6.5 % in two consecutive sentences. The 15 % is the
  transport step alone; the 6.5 % is the hop. As written the arithmetic also fails: a quarter
  of 15 % is under 4 %.
- **Source:** briefs/soak-5.md:72 — "−15.1 / −15.4 %, −6.5 % | infologger transport alone and
  whole stamper hop, median of 15 pairs, two passes | shipped path | Transport is about a
  quarter of the hop."
- **Fix:** "Splicing the stamped fields into a record's own bytes made the transport step
  15~\% cheaper per record. Transport is about a quarter of the whole collector-to-stamper
  hop, so the hop fell 6.5~\% on \code{infologger}."

### M13. Walkthrough 1, step 6: neither the machine nor the carrier is named
- **File:** reports/loggy-report/sections/02-design-a.tex:43
- **Quote:** `\item The collector also posts the record to the shifter view's live lane, which holds the last 500 records.`
- **Problem:** every other step says where it runs and what moves the bytes. This step says
  neither, in a walkthrough whose promise is "from an EPN to a screen". The reader cannot
  place the shifter view on any machine in the whole report body.
- **Source:** reports/inputs/dataflow-atlas.md:22 (audited) — "The same stamped record is also
  posted over HTTP to the live lane inside the Shifter on node-05. The Shifter keeps it in
  memory and streams it to every open browser over Server-Sent Events, with no cluster query
  at all."
- **Fix:** "The collector also posts the record over HTTP to the live lane on the shifter
  host, which keeps the last 500 records in memory and pushes each one to every open browser
  over a held-open HTTP connection."

### M14. Walkthrough 2 never says what carries a search to a worker's own index
- **File:** reports/loggy-report/sections/02-design-a.tex:49
- **Quote:** `A placement setting pins the index to its worker, so no byte crosses the network.`
- **Problem:** this is the half of the design a reader most needs and the whole subsection is
  three sentences. Nothing says who reads a local line afterwards, how one search reaches 200
  workers, or what fanout costs. Section 4 then uses "fanout" as if it had been introduced.
- **Source:** reports/inputs/dataflow-atlas.md:30-31 (audited walkthrough 2, steps 4 and 5) —
  the rollup, six detectors and the shifter's Lines panel read it, and the info bulk is never
  posted to the live lane because "on a farm of 200 workers the info bulk would saturate the
  network"; outline.md:72 requires "how one query from a storage machine still reaches it.
  Federated search: ask once, every node answers, the cost of fanout, and why the live lane
  exists so that the cluster is asked only when it must be."
- **Fix:** add two sentences: "One search still finds it. The cluster asks every node that
  holds a shard of the pattern and merges the answers, so a search over all workers is as slow
  as the slowest worker. That is why the live lane exists: the everyday question, what is
  arriving now, never asks the cluster at all."

### M15. nDCG is never expanded
- **File:** reports/loggy-report/sections/00-abstract.tex:1
- **Quote:** `Dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634 for the dense model alone.`
- **Problem:** the acronym appears in the abstract, twice in Section 5.6, once in a table and
  once in the conclusion, and is glossed ("a rank score that rewards relevant results near the
  top") but never expanded. A reader cannot look up a name that was never written.
- **Source:** reports/loggy-report/report/references.tex, `ref:ndcg`: Järvelin and Kekäläinen,
  "Cumulated gain-based evaluation of IR techniques".
- **Fix:** at 06-eval-b.tex:36 write "normalised discounted cumulative gain over the top ten
  results, nDCG@10, a rank score that rewards relevant results near the top~\cite{ref:ndcg}".

### M16. CNCF is never expanded, and "graduated" is unexplained jargon
- **File:** reports/loggy-report/sections/04-why.tex:6
- **Quote:** `We chose Fluent Bit~\cite{ref:fluentbit}, written in C and graduated by the CNCF~\cite{ref:cncf}.`
- **Problem:** this is the deciding fact for the collector choice, and it is two pieces of
  unexplained vocabulary. The table repeats "CNCF, graduated" as the verdict column.
- **Source:** reports/loggy-report/report/references.tex, `ref:cncf`: "Cloud Native Computing
  Foundation, graduated projects and graduation criteria".
- **Fix:** "written in C and a graduated project of the Cloud Native Computing Foundation,
  which means several independent companies maintain it and no single vendor can withdraw
  it~\cite{ref:cncf}."

### M17. "entity" is never defined, and the detection design rests on it
- **File:** reports/loggy-report/sections/03-design-b.tex:5
- **Quote:** `17 detectors run, one per entity, and a silent host scores zero.`
- **Problem:** the word carries the shape of the whole alerting chain — one detector per
  entity, one episode per entity, a trend rule on an entity's share — and the reader never
  learns what an entity is.
- **Source:** briefs/walkthroughs-6-9.md:31 — "entity_id is the origin_host value...
  il-per-epn: entity_kind epn, category_field origin_host"; briefs/walkthroughs-6-9.md:45 —
  "an episode is one detector on one host".
- **Fix:** "17 detectors run, one per entity: one machine, or one log family on one machine —
  the thing a rule is about."

### M18. "the 512 MB unit" uses a systemd word as a noun for a memory limit
- **File:** reports/loggy-report/sections/06-eval-b.tex:11
- **Quote:** `The tree stops learning at 20,000 templates, inside the 512~MB unit.`
- **Problem:** "unit" has not been used in this sense anywhere in the report. The reader
  cannot tell whether 512 MB is a machine, a service, a file or a cap, so the sentence's claim
  is lost.
- **Source:** briefs/soak-5.md:38 — "20,000 templates; 4.7 times 4,221 | the growth ceiling
  across all families"; the report's own Table 8 row gives "208.1~MB against 512~MB".
- **Fix:** "The tree stops learning at 20,000 templates, and holds 208.1~MB there, inside the
  stamper's 512~MB memory limit."

---

## SHOULD

### S1. Six components are named in one list, none with its job
- **File:** reports/loggy-report/sections/02-design-a.tex:11
- **Quote:** `The control host runs Dashboards, the poller, the roster, the catalog maintenance, Alertmanager and the receiver.`
- **Problem:** this is the reader's first meeting with five of the six, and the report's own
  rule is that components are named by their job. Every job arrives one to three pages later:
  poller and roster at 03-design-b.tex:3, Alertmanager and receiver at 03-design-b.tex:11,
  catalog maintenance at 02-design-a.tex:73.
- **Source:** outline.md:68 — "The control host, the background host and the shifter host,
  each with its services named by job"; outline.md decision 13 — "Components are named by
  their job."
- **Fix:** give each three words: "the poller, which samples the cluster; the roster, the list
  of collectors that should be alive; the catalog maintenance, which audits the template
  counts; Alertmanager, which decides when a person is told; and the receiver, which stores
  what was sent."

### S2. "the store" is never tied to OpenSearch
- **File:** reports/loggy-report/sections/01-intro-problem.tex:81
- **Quote:** `We replaced Grafana with Dashboards, because detection and alerting come with the store.`
- **Problem:** first use of "the store" as a name. OpenSearch is not named until
  02-design-a.tex:7, and no sentence ever says they are the same thing. The report also uses
  "a local OpenSearch data node", "the search node", "the cluster" and "the worker's store
  node" for parts of it.
- **Source:** briefs/soak-index.md:9 — "The soak rejected a bus between the collector and
  OpenSearch"; briefs/why.md:59 — "The collector writes only to the OpenSearch node on its own
  machine."
- **Fix:** at first use in Section 2.5 write "the store, OpenSearch~\cite{ref:opensearch}",
  and keep one word for a machine's part of it thereafter.

### S3. Dashboards and Discover are product screen names used as plain nouns
- **File:** reports/loggy-report/sections/03-design-b.tex:30
- **Quote:** `Dashboards holds the maintainer cockpit and Discover, which searches the archive \cite{ref:opensearch}.`
- **Problem:** a reader takes "Dashboards" for a common noun and "Discover" for a verb. Neither
  is ever said to be the store's own web interface and its search screen. Dashboards first
  appears at 01-intro-problem.tex:81 and 02-design-a.tex:11 with no gloss at all.
- **Source:** reports/inputs/dataflow-atlas.md:24 — "The Discover view inside OpenSearch
  Dashboards, behind the nginx door on port 5601".
- **Fix:** at first use: "Dashboards, the store's own web interface, and inside it Discover,
  its search screen."

### S4. The stamper is introduced with no job
- **File:** reports/loggy-report/sections/02-design-a.tex:7
- **Quote:** `The collector reads the five log sources, with the stamper beside it.`
- **Problem:** one of the four parts of the worker tier arrives as a bare name in a
  subordinate clause. Its job is first visible at 02-design-a.tex:40, a page later.
- **Source:** briefs/walkthroughs-6-9.md:66 — "Every record gets template_version and
  template_status before indexing".
- **Fix:** "with the stamper beside it, a small service that names every line's template before
  it is indexed."

### S5. Random Cut Forest gets no plain gloss
- **File:** reports/loggy-report/sections/03-design-b.tex:5
- **Quote:** `A detector is a Random Cut Forest model the cluster runs on a schedule, grading every window with no threshold \cite{ref:rcf}.`
- **Problem:** the reader is told the model's proper name and its schedule, not what it does.
  "grading every window with no threshold" says how it differs from a rule, not what a grade
  measures.
- **Source:** reports/loggy-report/report/references.tex, `ref:rcf`: "Robust random cut forest
  based anomaly detection on streams".
- **Fix:** "A detector is a model that learns a number's usual shape from the number itself and
  grades how unusual each new window is, from 0 to 1, with no threshold a person had to
  choose. Ours is a Random Cut Forest~\cite{ref:rcf}."

### S6. A detector grade is compared to 0.5 and 0.7 with no scale stated
- **File:** reports/loggy-report/sections/03-design-b.tex:7
- **Quote:** `A detector grade above 0.5 opens an episode, the paging monitor on grades needs 0.7.`
- **Problem:** two thresholds and no range. The reader cannot tell whether 0.7 is nearly
  certain or barely anything.
- **Source:** briefs/walkthroughs-6-9.md:43 — "A row with grade above 0.5 is a firing signal. A
  row at or below 0.5 is evidence of recovery"; :32 — "3 healthy windows at grade at or below
  0.0".
- **Fix:** state the scale once where the detector is defined (S5 above), then this sentence
  reads without help.

### S7. "monitor" and "rule" are two names for the same thing
- **File:** reports/loggy-report/sections/04-why.tex:53
- **Quote:** `Threshold rules catch the cliffs we know.`
- **Problem:** Section 3.6 defines a monitor ("a saved rule the cluster runs on a schedule")
  and then names the three lanes "Threshold monitors", "Trend monitors", "detectors". Section 4
  renames two of the three "threshold rules" and "trend rules", and Section 6 uses "per-host
  trend rules". A reader who was careful in Section 3 now looks for a fourth thing.
- **Source:** reports/loggy-report/sections/03-design-b.tex:5 — "A monitor is a saved rule the
  cluster runs on a schedule, writing an alert when its condition holds."
- **Fix:** pick "monitor" and use it everywhere after the definition.

### S8. "bucket" means two different things
- **File:** reports/loggy-report/sections/03-design-b.tex:23
- **Quote:** `Every ten minutes the rollup re-rolls the last three closed buckets after a 120-second settle for late records.`
- **Problem:** Section 3.5 uses bucket for a template count document over a five-minute window;
  Section 3.7 uses it for a ten-minute slice of the trend rollup, and then "six baseline
  buckets" at 03-design-b.tex:25. The trend bucket is never defined, and "re-rolls" and
  "closed" are new words.
- **Source:** reports/loggy-report/sections/02-design-a.tex:73 — "Every 300~seconds the stamper
  publishes one bucket document per family and window"; briefs/target-architecture.md:183 names
  the rollup as a separate component on the background host.
- **Fix:** define the trend bucket at first use — "one ten-minute slice of counts per family and
  host" — and say that the rollup recomputes the last three slices once they can no longer
  receive late records.

### S9. The rollup is introduced with no job and no machine
- **File:** reports/loggy-report/sections/03-design-b.tex:23
- **Quote:** `Every ten minutes the rollup re-rolls the last three closed buckets after a 120-second settle for late records.`
- **Problem:** first and only introduction of a component, as the subject of a sentence about
  its schedule. The reader never learns that it exists so that a trend rule never has to scan
  raw logs, nor where it runs.
- **Source:** briefs/target-architecture.md:183 — "the background host (rollup, projector)";
  outline.md decision 2 — "The projector and the trend rollup run on the second storage node,
  off the control host, as the sources say."
- **Fix:** "On the background host a rollup turns raw lines into small rows, so a trend rule
  never scans a log. Every ten minutes it re-counts the last three ten-minute slices..."

### S10. The projector's machine is never named in the prose
- **File:** reports/loggy-report/sections/03-design-b.tex:7
- **Quote:** `The projector reads every alert and detector result every 30~seconds.`
- **Problem:** the projector is the busiest component in Section 3.6 and both alert
  walkthroughs, and only Figure 2 says where it runs. Section 5.7 then says "the agent on the
  projector's machine may stop only the projector", which assumes the reader knows which
  machine that is.
- **Source:** briefs/target-architecture.md:27 — "The sources place it on node-04 with the
  trend rollup (atlas:455, deploy/README.md:78-80, rework-context.md:165)";
  briefs/target-architecture.md:201 — "away from the UI host on purpose".
- **Fix:** "On the background host, away from the screens on purpose, the projector reads..."
  (fold into M6 and S9, which fix the same gap).

### S11. "fleet" and "fleet document" are undefined
- **File:** reports/loggy-report/sections/03-design-b.tex:17
- **Quote:** `Within 30~seconds the poller counts each rostered collector's samples over 90~seconds and writes one fleet document each, with a missing flag.`
- **Problem:** first use of "fleet", in the walkthrough the supervisor asked to see. It returns
  at 03-design-b.tex:25 as "the entity's share of the fleet" and at 07-limits-close.tex:36 as
  "a slice whose fleet count is zero", each time meaning something slightly different.
- **Source:** briefs/walkthroughs-6-9.md:152 — the roster "names every worker";
  reports/inputs/dataflow-atlas.md:26-31 uses the whole worker set as the comparison group.
- **Fix:** define at first use: "the fleet, every worker on the roster", and write "one row per
  rostered collector" instead of "fleet document".

### S12. The report never says whether "runs on every EPN" is the target or the built state
- **File:** reports/loggy-report/sections/02-design-a.tex:7
- **Quote:** `The worker tier runs on every EPN and has four parts.`
- **Problem:** present tense, no status mark, three pages after the introduction said it runs
  on three farm workers. A reader who trusts this sentence believes the farm is deployed. The
  same sentence also promises four parts and then lists three in a row before the fourth.
- **Source:** reports/loggy-report/sections/01-intro-problem.tex:9 — "one deployment went green
  on three farm workers and one infra machine"; outline.md:3 — "Status marks say what is built
  and what is agreed and not built."
- **Fix:** "In the target design the worker tier runs on every EPN; today it runs on three. It
  has four parts."

### S13. "went green" is unexplained jargon in the sentence that states the project's result
- **File:** reports/loggy-report/sections/01-intro-problem.tex:9
- **Quote:** `The platform runs on five staging machines, and one deployment went green on three farm workers and one infra machine.`
- **Problem:** "went green" is a build-pipeline idiom. In the one sentence that tells the
  reader how far the project got, it is the load-bearing word, and the conclusion repeats it as
  "one green run on the farm".
- **Source:** briefs/style.md and outline.md:144 use it as shorthand, not as report prose; the
  fact underneath is a deployment that completed with every check passing.
- **Fix:** "one deployment ran to the end with every check passing on three farm workers and one
  infra machine."

### S14. The ops page appears once, with no job and no further mention
- **File:** reports/loggy-report/sections/03-design-b.tex:30
- **Quote:** `The ops page is the operator's status line.`
- **Problem:** one of the four surfaces, introduced and abandoned in six words. "Status line"
  is not a job: the reader cannot tell what it shows, who opens it or how it differs from the
  maintainer cockpit and the shifter view's live page.
- **Source:** briefs/target-architecture.md:50 — nginx is "the single TLS door to it, the ops
  page, the live lane and Alertmanager"; briefs/target-architecture.md:123 lists the ops page
  buttons under tester tooling.
- **Fix:** say what it answers in one clause, or drop it and say "three surfaces".

### S15. Template counts are quoted without the corpus each came from
- **File:** reports/loggy-report/sections/06-eval-b.tex:11
- **Quote:** `One run per family gave 3,011 templates. One tree per family across three corpora gave 4,221.`
- **Problem:** three template counts appear in this subsection — 3,822 six lines earlier, then
  3,011 and 4,221 — and none carries the number of lines behind it. The reader cannot tell
  whether the growth is a corpus effect or a method effect, which is the point of the sentence.
- **Source:** briefs/soak-3.md:59 — "3,011 templates on 45,596,613 lines... baseline H1b's
  3,822"; briefs/soak-5.md:38 — "the template count of the 55,963,050-line archive pass" for
  4,221.
- **Fix:** "One run per family gave 3,011 templates on 45,596,613 lines. One tree per family
  across three corpora, 55,963,050 lines, gave 4,221. The count is a curve driven by how many
  runs were sampled, not a total."

### S16. The semantic corpus is given in groups with no line count
- **File:** reports/loggy-report/sections/06-eval-b.tex:32
- **Quote:** `It searches 5,301 template groups.`
- **Problem:** 5,301 has no reference next to it. Every other corpus in the report is quoted in
  lines, so the reader cannot judge whether 5,301 is a large or a tiny search problem.
- **Source:** briefs/semantic-1.md:21 — "5,301 canonical template groups built from 18,011
  source instances and 56,628,579 real log lines, across all seven log formats";
  outline.md:124 — "Corpus 5,301 groups from 56,628,579 lines."
- **Fix:** "It searches 5,301 template groups, the whole of 56,628,579 real log lines reduced to
  their skeletons."

### S17. "the lexical engine" and "the small transformer" are never introduced
- **File:** reports/loggy-report/sections/06-eval-b.tex:34
- **Quote:** `A static model encodes 18,037 templates a core-second, 31 times the small transformer.`
- **Problem:** two unnamed comparison systems carry four numbers between them, including the
  report's headline held-out result of 0.689 against 0.371. The reader never learns that the
  lexical engine is the store's ordinary word-matching search, which is the whole point of the
  comparison.
- **Source:** briefs/semantic-1.md:74 — "lexical index size for 5,301 groups | one primary
  shard"; briefs/semantic-2.md:35-36 — the dense path scans "a 5,301 by 768 matrix", against
  the store's own text search.
- **Fix:** name both at first use: "31 times a small transformer model", and "0.371 for the
  store's ordinary word-matching search, the lexical engine."

### S18. "a late-interaction reranker" is unexplained, and it is the recommendation
- **File:** reports/loggy-report/sections/06-eval-b.tex:36
- **Quote:** `The standing recommendation is a dense scan by exact cosine over all templates, 30 candidates, then a late-interaction reranker.`
- **Problem:** the sentence that states what the project recommends contains three pieces of
  unexplained vocabulary. A reader who knows search engines can decode "exact cosine" and "30
  candidates" but not "late-interaction".
- **Source:** briefs/semantic-1.md:85 — "194 MB reranking token matrix for 5,301 templates...
  the one real deployment cost of the recommendation", i.e. the reranker compares the question's
  words against each candidate's words one by one.
- **Fix:** "then a reranker that scores the 30 candidates by matching the question's words
  against each template's words one by one, rather than by one vector each."

### S19. 0.020 and 0.048 are quoted with no unit
- **File:** reports/loggy-report/sections/06-eval-b.tex:36
- **Quote:** `An approximate vector index costs 0.020 and moves between builds, so we use none.`
- **Problem:** the unit is nDCG@10, and the sentence never says so. The next sentence repeats
  the problem with "Fusion with the lexical engine scores 0.048 worse."
- **Source:** briefs/semantic-1.md:88 — "0.020 | nDCG@10 cost of the approximate vector index in
  the Morning report"; briefs/semantic-2.md:99 — "0.685 exact vs 0.665 approximate".
- **Fix:** "An approximate vector index costs 0.020 nDCG@10, 0.685 falling to 0.665, and moves
  between builds, so we use none."

### S20. Two new noise floors appear with no explanation of why they differ from §5.1
- **File:** reports/loggy-report/sections/05-eval-a.tex:44
- **Quote:** `A 2~GB heap for the worker's store node moves cost 2.1~\% against a 6.7~\% floor, so 1~GB ships.`
- **Problem:** Section 5.1 gave the floors as 1.61 % and 9.48 %, tied to rates. Here 6.7 % and
  5.5 % appear tied to nothing. The reader cannot tell whether these are a third rig, a
  different measure, or a mistake, and the whole "no measurable difference" argument rests on
  them.
- **Source:** briefs/soak-index.md:94 — "2.1 % and 0.4 % against a 6.7 % floor | Re-measured
  effect of heap 2g; no effect | docs/SOAK_RESULTS.md:102"; briefs/soak-index.md:282 — "2.7 %
  against a 5.5 % floor, no effect".
- **Fix:** say they belong to the re-run rig: "On the re-run rig, whose own floor for this
  comparison is 6.7~\%, a 2~GB heap moved cost 2.1~\%, so 1~GB ships."

### S21. "89.3 % of its work" has no stated denominator
- **File:** reports/loggy-report/sections/05-eval-a.tex:34
- **Quote:** `The collector is one single-threaded loop doing 89.3~\% of its work.`
- **Problem:** 89.3 % of what — of its processor time, of its records, of its outputs? The
  sentence exists to explain why every threading arm lost, and the reader cannot follow it.
- **Source:** briefs/soak-index.md:16 — "Round 2 Stage B screened the collector's own settings
  at 20,000 a second. Every threading arm cost more than the shipped one."
- **Fix:** name the denominator: "One single-threaded loop does 89.3~\% of the collector's
  processor work, so adding threads adds coordination and no throughput."

### S22. "cell" and "arm" are instrument vocabulary used without definition
- **File:** reports/loggy-report/sections/05-eval-a.tex:13
- **Quote:** `A 47-cell re-run left one product change, the flush interval of Section~\ref{sec:eval-collector}.`
- **Problem:** "cell" appears here, "arm" at 05-eval-a.tex:34 and 05-eval-a.tex:44, "round"
  throughout Appendix B. All three are the report's private words for the shape of a
  measurement campaign and none is defined.
- **Source:** briefs/soak-index.md:17 — "A 47-cell re-run on 27 August at 5,000 a second
  replaced them and left one product change"; briefs/soak-index.md:15 — "The instrument failed
  four times before any arm ran."
- **Fix:** define both in §5.1: "An arm is one setting under test. A cell is one arm at one
  rate, run three times."

### S23. 32 defects and 99 defects are never related
- **File:** reports/loggy-report/sections/05-eval-a.tex:13
- **Quote:** `We fixed 32 defects in the instrument.`
- **Problem:** Section 5.4 then says "We fixed 99 defects in the collector, the stamper, the
  catalog and the instrument." A reader cannot tell whether 99 includes the 32 or sits beside
  it, so neither number means anything.
- **Source:** briefs/soak-index.md:15 — instrument failures are counted separately from product
  defects; briefs/soak-5.md:9 — Rounds 10 to 21 are product reviews.
- **Fix:** say it once: "99 defects in total, 32 of them in the instrument itself."

### S24. Puppet is named the right tool for the farm, and the report then chooses Ansible without settling it
- **File:** reports/loggy-report/sections/04-why.tex:65
- **Quote:** `Puppet wants an agent and a certificate on every machine, runs on a timer, and is right for the farm.`
- **Problem:** the paragraph's own deciding fact is "CERN hosts take no long-running agent",
  and then it concedes the losing candidate is right for the deployment target. The reader is
  left with a choice that argues against itself and no sentence resolving it.
- **Source:** briefs/constraints.md section 7 and briefs/why.md on deployment: the no-agent rule
  is a constraint on the machines this project could use, not on the farm's own estate.
- **Fix:** finish the thought: "Puppet is right for the farm's own estate, which already runs
  agents; it was not available to this project, whose machines take none."

---

## NITS

### N1. "worker" is used before it is tied to "EPN"
- **File:** reports/loggy-report/sections/01-intro-problem.tex:9
- **Quote:** `We built \loggy{}, a platform that keeps the bulk of the logs on the worker that wrote them and still searches them as one.`
- **Problem:** the report's two most frequent nouns for one machine, and the tie arrives at
  02-design-a.tex:7, "The worker tier runs on every EPN".
- **Source:** reports/loggy-report/sections/01-intro-problem.tex:3 defines the EPN only.
- **Fix:** "on the EPN, the worker, that wrote them" at first use.

### N2. "cores" and "logical processors" are two names for the same budget
- **File:** reports/loggy-report/sections/02-design-a.tex:7
- **Quote:** `A local OpenSearch data node holds one index per worker on four of the EPN's 128~cores, and little memory.`
- **Problem:** Section 2.3 says "four of its 128 logical processors". A reader who knows Linux
  knows the two are not the same thing, and will wonder which is meant.
- **Source:** reports/loggy-report/sections/01-intro-problem.tex:50 — "A worker gives the
  platform four of its 128 logical processors and little memory."
- **Fix:** use "logical processors" in both places, and give "little memory" a number once.

### N3. "the run" means a data-taking run and a monitor execution in the same section
- **File:** reports/loggy-report/sections/03-design-b.tex:25
- **Quote:** `Each compares the entity's share of the fleet in three slices, ending 40, 30 and 20~minutes before the run, against the previous seven days.`
- **Problem:** "run" here means the monitor's own execution. At 01-intro-problem.tex:36 it
  means a data-taking run, and the storage-tier walkthroughs use "run" for a soak run. In an
  ALICE report the first reading wins.
- **Source:** reports/loggy-report/sections/01-intro-problem.tex:36 — "No run was active during
  either survey, so no live job log was seen."
- **Fix:** "ending 40, 30 and 20~minutes before the monitor fires."
