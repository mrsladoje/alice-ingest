# Round 1 review — lens: figures

Scope: the seven figures the report cites (fig01 to fig07), their SVG sources, their PNG renders and
their captions. Checked against figures/STYLE.md, outline.md decisions, the briefs and the atlas.

Figure numbering is correct: report-text.txt shows Figures 1 to 7 in order, each cited once in the body
(report-text.txt:93, 339, 344, 363, 439, 523, 1062). No figure carries a version number. The two Kafka
decisions are kept apart in every figure: fig02, fig03 and fig07 draw only the live-lane bus, and the
rejected collector-to-store queue and its three brokers on the storage machines appear nowhere. No
drawn logo: every mark is an `<image href="logos/…-official.svg">`. No gradient, shadow, glow, dark
panel or dot grid.

---

## must

### 1. fig06: the threshold lane is labelled with the whole platform's monitor count

- File: `figures/fig06-alerts.svg:109`
- Quote: `<text x="690" y="348" font-family="Menlo, monospace" font-size="10" fill="#6b6b66" text-anchor="middle">30 monitors, every minute or ten</text>`
- Problem: 30 is the total across all three lanes, so the threshold card double-counts the nine trend
  rules drawn in the box beside it and contradicts this figure's own inbound edge, `14 monitors, every minute`.
- Source: `sections/03-design-b.tex:5` ("30 monitors run: 28 write to the alert index, 2 take the
  break-glass path below"); `briefs/target-architecture.md:203`; `deploy/roles/loggy_anomaly_detection/files/monitors/`
  holds 30 definitions of which 11 are `trend-*` and 2 are `template-*`.
- Fix: `14 of the 30 monitors, every minute or ten`, and move the total to a caption or a footline that
  covers all three lanes.

### 2. fig05: the Templates page is drawn on the control host

- File: `figures/fig05-templates.svg:130` (column heading at `figures/fig05-templates.svg:13`)
- Quote: `<text x="728" y="413" font-size="12" font-weight="600" fill="#101010" text-anchor="middle">The Templates page</text>`
- Problem: the Templates page is a page of the shifter view, which runs on the shifter host, not on the
  control host column this figure puts it in; fig02 places the same component on storage node 3.
- Source: `briefs/walkthroughs-6-9.md:9` (node-05 runs the shifter view); `briefs/target-architecture.md:183`
  (the shifter host holds the shifter view); `figures/fig02-architecture.svg:96-98`.
- Fix: give the Templates page its own narrow column or band headed `SHIFTER HOST`, or retitle column 03
  `CONTROL AND SHIFTER HOSTS` and mark which card sits on which.

### 3. fig03: the stamper is drawn inside the collector

- File: `figures/fig03-error-line.svg:24`
- Quote: `<rect x="14" y="106" width="194" height="238" fill="#f1f1ec" stroke="none"/>`
- Problem: that grey panel is titled "the collector" and its lower edge (y=344) encloses the stamper card
  (y=276 to 336), so the stamper reads as a stage inside Fluent Bit; it is a separate process beside the
  collector, which is why the same figure labels the hop `forward socket out and back`.
- Source: `briefs/target-architecture.md:11` ("a stamper beside the collector gives it a template identity");
  `reports/inputs/dataflow-atlas.md:15` ("The stamper, a Python process running drain3, listens there");
  `figures/fig04-collector.svg:66-68` draws it outside the five collector steps.
- Fix: end the panel at y=250, above the stamper card, so it covers only identity/clock and the routing rule.

### 4. fig03: "dpl" is a code name the report never uses

- File: `figures/fig03-error-line.svg:40`
- Quote: `<text x="110" y="305" font-family="Menlo, monospace" font-size="10" fill="#6b6b66">drain3, dpl family tree</text>`
- Problem: the report never defines `dpl`; it calls this family the O2 process tree, so the label is code
  vocabulary that a reader cannot resolve.
- Source: `figures/STYLE.md:50` ("Names follow the report, not the code"); the report's name for the family
  is at `sections/01-intro-problem.tex:36` and `sections/05-eval-a.tex:58` ("process-tree lines"); `dpl`
  appears in no section file.
- Fix: `drain3, the process-tree family tree`.

### 5. fig07: farm-pilot services are drawn as built although no source says they ran there

- File: `figures/fig07-layouts.svg:96` and `figures/fig07-layouts.svg:101`
- Quote: `<text x="646.0" y="326" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" font-size="11" fill="#3a3a36" text-anchor="middle">the projector</text>`
- Problem: the farm-pilot containers are solid cards, and the legend reads solid = "built and running", but
  the sources prove only the six-node cluster, the three workers, the three containers and the collector on
  the farm; the projector and the shifter view (line 101) are not stated.
- Source: `briefs/target-architecture.md:215` ("Nothing read says whether the stamper, the poller, the
  projector, the rollup, Alertmanager, the receiver or the shifter view ran on the farm. Say 'not stated'
  rather than 'deployed'."); `briefs/inputs/memory-extracts.md:85-87`.
- Fix: keep the container cards solid for the OpenSearch nodes and set the service lines in ink-soft with a
  one-line note `services on the farm: not stated`, or drop the service lines from the farm row.

### 6. fig07: the production panel says two replicas put a copy on every storage node

- File: `figures/fig07-layouts.svg:163`
- Quote: `<text x="404" y="634" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" font-size="10" fill="#6b6b66" text-anchor="start">two replicas, so a copy on every storage node; cluster manager quorum two</text>`
- Problem: the same panel is headed `storage tier · three or more machines` (line 117) and draws a
  `storage N` card, and with more than three storage nodes two replicas are three copies, not one per node.
  The sentence is true only for the staging and farm-pilot rows above it.
- Source: `outline.md:16-17` (decision 3: farm three primaries, one per storage node, two replicas each);
  `briefs/target-architecture.md:90-93` (2 replicas, primaries per layout).
- Fix: in the production row only, `three primaries, two replicas each; cluster manager quorum two`.

---

## should

### 7. fig02: the two arrows at the bus carry no label

- File: `figures/fig02-architecture.svg:116-117`
- Quote: `<path d="M806,268 H764" fill="none" stroke="#101010" stroke-width="1.25" stroke-opacity="0.32" marker-end="url(#ghosthead)"/>`
- Problem: both bus edges are unlabelled, so what the collectors publish and what the shifter view consumes
  is a guess; every other edge in the figure names its cargo and protocol.
- Source: `figures/STYLE.md:47` ("Label every arrow with what flows and how (protocol, cadence), in
  monospace. An unlabelled arrow is a guess."); `briefs/target-architecture.md:81` (the collector gets a
  Kafka output and the shifter view consumes the topic).
- Fix: label the inbound arrow `severe lines · kafka produce` and the outbound arrow `one consumer · kafka fetch`.

### 8. fig02: the trend rollup is the only service without a cadence

- File: `figures/fig02-architecture.svg:90`
- Quote: `<text x="400" y="486.5" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" font-size="10.5" fill="#3a3a36" text-anchor="start">the trend rollup</text>`
- Problem: its two siblings on the same host row carry cadences ("the poller, 30 s", "the projector, 30 s"),
  so the reader reads the rollup as continuous; it runs every ten minutes.
- Source: `briefs/walkthroughs-3-5.md:163` (runs every 600 seconds); `briefs/target-architecture.md:65`.
- Fix: `the trend rollup, 10 min`.

### 9. fig03: the accent is spent on something the caption is not about

- File: `figures/fig03-error-line.svg:113`
- Quote: `<text x="322" y="320" font-family="Menlo, monospace" font-size="10" fill="#e1251b" text-anchor="middle">the bus will replace this edge</text>`
- Problem: the caption's subject is the one hop to the primary and its two replicas, which the hot card
  already carries; a second red mark on the live-lane note makes the bus look like the point of the figure.
- Source: `figures/STYLE.md:20` ("Use the accent once or twice per figure, for the thing the caption is
  about. Never as decoration."); caption at `sections/02-design-a.tex:35`.
- Fix: set the note in ink-soft (`#6b6b66`) and keep the red for the primary shard only.

### 10. fig06: "a week of history" reads as the retention of the rollup index

- File: `figures/fig06-alerts.svg:32`
- Quote: `<text x="101" y="214" font-size="10" fill="#6b6b66" text-anchor="middle">a week of history</text>`
- Problem: the sibling index card in the same figure uses that slot for retention ("one row per sample,
  seven days kept"), so a reader takes a week as the rollup's retention; the index keeps rows for 30 days
  and the week is the baseline the trend rules compare against.
- Source: `briefs/target-architecture.md:101` (trend-rollup, 30 d by document);
  `sections/03-design-b.tex:25` (three slices against the previous seven days).
- Fix: `rows kept 30 days; the rules read the last seven`.

### 11. fig02: "role = worker" reads as a role name in a report that bans them

- File: `figures/fig02-architecture.svg:12` and `figures/fig02-architecture.svg:36`
- Quote: `<text x="388" y="58" font-family="Menlo, monospace" font-size="10" fill="#6b6b66" text-anchor="end">role = worker</text>`
- Problem: it is a shard-allocation attribute of the node, but it is set in configuration vocabulary and
  collides with the ban on role names; the figure already says what the node is in plain words ("local node,
  data, ingest").
- Source: `figures/STYLE.md:51` ("No Ansible role names, no file names, no version numbers in figures.");
  `briefs/target-architecture.md:25` (the attribute exists, for pinning the local index).
- Fix: `the local index is pinned here`, or drop the tag.

### 12. fig05: the two hourly monitors are drawn on the control host

- File: `figures/fig05-templates.svg:123`
- Quote: `<text x="728" y="317" font-size="12" font-weight="600" fill="#101010" text-anchor="middle">Two monitors, hourly</text>`
- Problem: monitors are saved rules the cluster runs, not a service on the control host; fig06 states this
  ("written by the plugins inside the cluster"), so the two figures disagree on where the rules live.
- Source: `sections/03-design-b.tex:5` ("A monitor is a saved rule the cluster runs on a schedule");
  `figures/fig06-alerts.svg:124` ("the alerts and the grades, written by the plugins inside the cluster").
- Fix: move the monitor card into the storage-tier column, or add `run by the cluster` under its title.

### 13. fig07: the farm-pilot workers lose the stamper

- File: `figures/fig07-layouts.svg:96` region; the staging and production equivalents are at
  `figures/fig07-layouts.svg:17` and `figures/fig07-layouts.svg:122`
- Quote: `<text x="64.0" y="341" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" font-size="11" fill="#3a3a36" text-anchor="middle">local data node</text>`
- Problem: the farm worker cards list collector, local data node and real log sources; staging and production
  list the stamper as well, so the middle row reads as a design without templates rather than as a status
  statement.
- Source: `briefs/target-architecture.md:215` (the stamper's farm status is not stated);
  `sections/02-design-a.tex:7` (the stamper is part of every worker).
- Fix: list the stamper on the farm cards too and mark the whole service list `not stated` there, in step
  with finding 5.

---

## nit

### 14. fig01: the query arrow points the way the answer travels, not the query

- File: `figures/fig01-logging-today.svg:76`
- Quote: `<text x="653" y="296" font-family="Menlo, monospace" font-size="10" fill="#6b6b66" text-anchor="middle">query, tcp 3306</text>`
- Problem: the arrowhead lands on the browser while the label names the request, and the source slide labels
  that edge `query` with no port.
- Source: `reports/inputs/deck-text.md:61-63` (the deck's slide-3 edge list: `tcp 3306`, `one table`, `query`);
  `briefs/constraints.md:55`.
- Fix: `query results, tcp 3306`.

### 15. fig05: a five-digit number without the report's separator

- File: `figures/fig05-templates.svg:41`
- Quote: `<text x="143" y="265" font-family="Menlo, monospace" font-size="10" fill="#6b6b66" text-anchor="middle">at most 20000 clusters per worker</text>`
- Problem: the report writes this number with a comma, so the figure and the sentence beside it differ.
- Source: `sections/02-design-a.tex:71` ("The stamper holds at most 20,000 clusters per worker").
- Fix: `at most 20,000 clusters per worker`.

### 16. fig04: two labels sit below the 10 px floor

- File: `figures/fig04-collector.svg:12` and `figures/fig04-collector.svg:104`
- Quote: `<text font-family="Menlo, monospace" font-size="9.5" letter-spacing="0.04em" fill="#6b6b66" x="108" y="19">the collector, one process on every worker</text>`
- Problem: 9.5 px in the 820-wide box is under the size the style sets for print, and the memory-envelope
  row labels use the same size.
- Source: `figures/STYLE.md:29` ("Keep text at 10 to 14 px in that box; smaller than 9 px is unreadable in print.").
- Fix: raise both to 10 px.

### 17. fig06: the break-glass path uses the dash pattern that means "not built" elsewhere

- File: `figures/fig06-alerts.svg:127`
- Quote: `<text x="782" y="472" font-family="Menlo, monospace" font-size="10" fill="#6b6b66" text-anchor="end">break-glass:</text>`
- Problem: the path is drawn `stroke-dasharray="4 4"` in ink-soft, the same stroke fig02 and fig07 use for
  agreed-and-not-built; break-glass is built and proven by injection.
- Source: `figures/STYLE.md:26` (ghost = planned, not built); `sections/06-eval-b.tex:52` (the stop-projector
  run proves the break-glass path); `figures/fig07-layouts.svg:179-180` (the legend's dashed = planned).
- Fix: draw it solid in ink-soft, or add a word to the label so the dash reads as "bypass", not "planned".

### 18. an eighth figure is rendered but cited nowhere

- File: `figures/fig08-flush.svg:4`
- Quote: `<text x="60" y="22">CORE-SECONDS, WHOLE RUN SET</text>`
- Problem: fig08-flush.svg and its PDF are finished and current, but no section includes them, so the folder
  carries an asset the report does not use.
- Source: `outline.md:171` (figure 8 is optional); no `includegraphics{fig08` in `sections/`.
- Fix: either cite it beside Table 7 in Section 5.2 or delete the three fig08 files.
