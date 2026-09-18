# Round 3 review — lens: figures

Seven figures checked: fig01 to fig07. Each PNG was rendered and looked at, every SVG label read,
every caption compared with the drawing, and the fig03 step numbers compared with the seven-step
list in `sections/02-design-a.tex`.

Nothing is illegible. No figure carries a version number, an Ansible role name or a file name.
The fig03 step numbers 1 to 7 match the seven steps of section 3.2 one for one.
fig01 is clean. fig05 is clean.

Three "must" findings and five "should" findings follow.

---

## MUST

### F1. fig02 calls the live lane "severe lines", which is not what it carries

- File: `reports/loggy-report/figures/fig02-architecture.svg:109` (and the same words at line 115)
- Quote: `live lane today · http · severe lines`
- Problem: the live lane carries every InfoLogger record at any severity plus the daemon log plus
  every line above info, so "severe lines" drops two of the three streams.
- Source: `reports/loggy-report/sections/02-design-a.tex:13` ("The live lane is the second copy of
  every record that leaves the worker. That is every InfoLogger record at any severity, the daemon
  log and every line above info severity."); `reports/inputs/dataflow-atlas.md:30` ("The http output
  to the Shifter matches stamped.infologger, stamped.ildaemon and stamped.family.central only").
- Fix: line 109 → `live lane today · http · InfoLogger, daemon log, warn+`;
  line 115 → `InfoLogger, daemon log, warn+ · kafka produce`.
- Severity: must

### F2. fig04's header says the collector writes only to the local node, and it writes twice

- File: `reports/loggy-report/figures/fig04-collector.svg:13`
- Quote: `writes only to the OpenSearch node on the same worker`
- Problem: the collector also posts every live-lane record over HTTP to the shifter host, which
  fig03 draws and labels `outputs / http, twice`, so the fig04 header contradicts the report and
  the neighbouring figure.
- Source: `reports/loggy-report/sections/02-design-a.tex:43` ("The collector also posts it over HTTP
  to the live lane on the shifter host."); `reports/inputs/dataflow-atlas.md:19`.
- Fix: `every OpenSearch write goes to the node on the same worker`
- Severity: must

### F3. fig04's caption states a 128 MB cap that fig04 itself disproves

- File: `reports/loggy-report/sections/02-design-a.tex:55`
- Quote: `A line takes five steps inside the collector, and the 64 chunks it can hold in memory cap its cost to the worker at about 128~MB.`
- Problem: the figure's own memory envelope gives 133 to 228 MB under steady load and 373 MB in a
  sink outage, so 128 MB is the chunk store, not a cap on the collector's memory.
- Source: `reports/loggy-report/sections/02-design-a.tex:59` ("At most 64 chunks sit in memory,
  about 128~MB. The documented worst case is twice that with a 20~\% margin, roughly 307~MB. The
  service is throttled at 384~MB"); the envelope table in
  `reports/loggy-report/figures/fig04-collector.svg:161-171`.
- Fix: `A line takes five steps inside the collector, and the 64 chunks it holds in memory are about 128~MB of the 133 to 228~MB it uses under load.`
- Severity: must

---

## SHOULD

### F4. fig03's column headers split the storage tier from the control and shifter hosts

- File: `reports/loggy-report/figures/fig03-error-line.svg:12`
- Quote: `SHIFTER HOST`
- Problem: the columns read as five separate machine roles, but the control, background and shifter
  hosts are the three storage nodes, which is what fig02 draws.
- Source: `reports/loggy-report/sections/02-design-a.tex:11` ("The three storage nodes are also the
  control host, the background host and the shifter host.")
- Fix: retitle the three right-hand columns so the shared machines are visible, for example
  `STORAGE TIER · THE INDEX`, `STORAGE NODE 3 · SHIFTER HOST`, `STORAGE NODE 1 · CONTROL HOST`.
- Severity: should

### F5. fig03 names an index pattern the report never introduces

- File: `reports/loggy-report/figures/fig03-error-line.svg:140`
- Quote: `or alice-unified`
- Problem: `alice-unified` appears nowhere in the body, so the reader meets a bare identifier with
  no definition.
- Source: `reports/loggy-report/sections/02-design-a.tex:44` names only the central index;
  `reports/inputs/dataflow-atlas.md:20` is the only place the pattern is defined.
- Fix: `or every log index at once`
- Severity: should

### F6. "The ops page" is drawn as a surface but is never named in the report

- File: `reports/loggy-report/figures/fig06-alerts.svg:192`
- Quote: `The ops page`
- Problem: section 3.9 says one door holds four surfaces and names Dashboards, Discover, the live
  page and the Templates page, so a fifth named surface in the figure has no definition. The same
  name also sits on the control host in `fig02-architecture.svg:79`.
- Source: `reports/loggy-report/sections/03-design-b.tex:31` ("one address, one account, four
  surfaces").
- Fix: drop the card from fig06 and the control-host row from fig02, or define the ops page in
  section 3.9 as the fifth surface.
- Severity: should

### F7. fig07's farm pilot panel repeats the staging shard count

- File: `reports/loggy-report/figures/fig07-layouts.svg:113`
- Quote: `two replicas, so a copy on every storage node · cluster manager quorum two`
- Problem: the farm inventory sets three primaries, one per storage node, so the farm pilot panel
  carrying the staging wording tells the reader the pilot has one primary.
- Source: `reports/loggy-report/sections/02-design-a.tex:15` ("The farm inventory sets three
  primaries, one per storage node"); Table 3 at `sections/02-design-a.tex:23-24` ("staging 1
  primary, farm 3").
- Fix: `three primaries, two replicas each · cluster manager quorum two`
- Severity: should

### F8. fig07's legend understates what the report says about the farm

- File: `reports/loggy-report/figures/fig07-layouts.svg:192`
- Quote: `not stated on the farm`
- Problem: the stamper, the projector and the shifter view are grey italic in the farm pilot panel,
  and the report states outright that they have run on staging only, which is stronger than "not
  stated".
- Source: `reports/loggy-report/sections/07-limits-close.tex:35` ("The farm pilot ran the cluster,
  the collectors and Dashboards. The stamper, the projector and the shifter view have run on
  staging only.")
- Fix: `not run on the farm`
- Severity: should

---

## Checked and correct

- fig03 steps 1 to 7 against the seven-step list in `sections/02-design-a.tex:37-45`: one for one.
- fig06 counts against `reports/inputs/dataflow-atlas.md:190-217`: 14 log detectors plus 3 on
  cockpit-metrics equals 17; 13 minute monitors plus 4 ten-minute equals 17 threshold; 9 trend plus
  17 threshold plus 2 hourly equals the 28 that land in the alert index; 2 break-glass make 30.
- fig06 cadences: page wait 30 s, warn wait 5 min, projector re-post 30 s, poller 30 s, absence at
  90 s, rollup 10 min. All match `sections/03-design-b.tex:3-27`.
- fig05: the maintenance order (expire first, checks last), the 20,000-template ceiling, the 300-second
  bucket cadence and the two hourly audits all match `sections/02-design-a.tex:71-73`.
- fig02 index card, `cockpit-metrics` shards and the farm container note all match Table 3.
- fig07 bus panel: live lane only, broker placement not decided, marked agreed and not built.
