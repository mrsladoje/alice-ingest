# Round 2 review, figures lens

Scope: the seven figures the report includes (fig01 to fig07), their SVG labels, their
captions and the text that cites them. Checked against the briefs, the audited atlas
walkthroughs (reports/inputs/dataflow-atlas.md lines 11 to 31), the atlas component cards
and edges, outline.md decisions, and figures/STYLE.md.

## Round 1 figure fixes: confirmed landed

- fig02: both bus arrows now carry labels; "the trend rollup, 10 min" has its cadence;
  the `require.box` shard-allocation wording is gone.
- fig03: the collector panel is split in two and the stamper now sits outside it; "dpl" is
  now "the process-tree family tree"; the step-5 edge leaves the collector's `outputs`
  card, not the stamper; the live-lane note is ink-soft, not red.
- fig04: "Chunks in memory 64, about 128 MB" and "Documented worst case 307 MB" are both
  correct now; no text below 10 px remains in the file.
- fig05: the Templates page has its own band headed `04 SHIFTER HOST`; the monitor card
  says "run by the cluster"; "20,000" carries its separator.
- fig06: the threshold box reads "17 monitors: 13 every minute, 4 every ten"; the trend
  arrow reads "9 comparisons, every 10 min"; the 30-monitor split is stated once, outside
  the threshold box; the break-glass path is drawn solid (no `stroke-dasharray`).
- fig07: the farm-pilot services are grey italic against a "not stated on the farm" legend
  entry, the stamper is now listed on the farm worker cards, and the production storage
  band reads "three primaries, two replicas each".

Not carried over from round 1 (both were nits): fig01's "query, tcp 3306" label, and
fig08-flush, which is still rendered in figures/ and still cited by nothing.

## Findings

### MUST

**F1. fig06, `15 monitors, every minute` contradicts the box the arrow enters.**
File: figures/fig06-alerts.svg:60.
Quote: `15 monitors, every minute`
The arrow runs from `cockpit-metrics` into the THRESHOLD RULES card, and that card reads
`17 monitors: 13 every minute, 4 every ten` (fig06-alerts.svg:109). The two numbers cannot
both be right, and the body says 13: "Of 30 monitors, 17 are threshold and detector
monitors, 13 every minute and four every ten" (sections/03-design-b.tex:5). The atlas edge
carries 14, not 15: "cockpit-metrics -> 30 alerting monitors [14 health monitors] ... every
1 min" (reports/inputs/dataflow-atlas.md:539). Fifteen is the count of every-minute
monitors of all kinds (dataflow-atlas.md:214), and two of those fifteen are the break-glass
pair, which this figure already draws on its own path out of the same card.
Fix: write `13 monitors, every minute`. The four ten-minute threshold monitors do not read
`cockpit-metrics` at all (they read `trend-rollup` and the forecast results,
dataflow-atlas.md:538, 542), so either draw that second inbound edge or drop "4 every ten"
from the card.
Severity: must.

**F2. fig07, the staging band is marked "built and measured", and nothing was measured on
staging.**
File: figures/fig07-layouts.svg:8.
Quote: `built and measured`
Every cost figure in the report comes from a laptop rig, not from the five staging virtual
machines: "Every number comes from a laptop. Shapes, knees and rankings transfer. Absolute
rates and core-seconds do not" (briefs/soak-index.md:7), and the body repeats it: "Every
cost figure in Section~\ref{sec:eval} comes from a laptop"
(sections/07-limits-close.tex:5). The mark sits in the same slot as the farm band's
"one green deploy · load not measured" (fig07-layouts.svg:52), so a reader takes it to mean
the staging deployment was measured under load. It was not.
Fix: write `built and running · load not measured`, and keep the green for "built".
Severity: must.

**F3. fig05 spells the catalogue the American way, three times.**
File: figures/fig05-templates.svg:81, 112, 136.
Quote: `The catalog` (line 81), `The catalog` (line 112, the maintenance card title reads
"The catalog maintenance"), `reads catalog, buckets,` (line 136).
Rule 16 of the outline is British spelling (outline.md:28), and the body uses it
throughout: "The catalogue is one index with one document per template version"
(sections/02-design-a.tex:73), "The catalogue maintenance audits the template counts"
(sections/02-design-a.tex:11). The figure is the only place in the report that spells it
"catalog" in prose. The index name `template-catalog` (line 82) is a code identifier and
must stay as it is.
Fix: change the three prose labels to "The catalogue", "The catalogue maintenance" and
"reads catalogue, buckets,". The `aria-label` on line 1 also says "the catalog
maintenance"; change it too.
Severity: must (rule violation).

**F4. fig03's step numbers stop matching the seven-step list they sit above at step 5.**
File: figures/fig03-error-line.svg:166 and 168.
Quote: `5` (circle at cx 226, on the live-lane edge), `6` (circle at cx 412, on the query
edge).
The figure is placed between the sentence and the numbered walkthrough of the same line
(sections/02-design-a.tex:35 to 45). The text's step 5 is "The document hops once to the
storage node holding the primary shard" (02-design-a.tex:42); in the figure that edge is
labelled `document / one hop` and carries no number, while circle 5 sits on the live-lane
edge, which the text calls step 6, and circle 6 sits on the query edge, which the text
calls step 7. The figure has six circles against seven listed steps.
Fix: number the `document / one hop` edge 5, move the live-lane circle to 6 and the query
circle to 7, so the seven circles match the seven list items. The audited atlas walkthrough
1 (reports/inputs/dataflow-atlas.md:15 to 21) has the same order.
Severity: must (wrong number).

### SHOULD

**F5. Four figures are placed narrower than \textwidth, so their smallest labels render
below the legibility floor the style sets.**
Files: sections/01-intro-problem.tex:7, sections/02-design-a.tex:69,
sections/03-design-b.tex:9, sections/07-limits-close.tex:35.
Quote: `\includegraphics[width=0.78\textwidth]{fig01-logging-today.pdf}`
STYLE.md fixes the box at 820 units wide "so every figure is the same text size when placed
at \textwidth (160 mm)", and adds "Keep text at 10 to 14 px in that box; smaller than 9 px
is unreadable in print" (figures/STYLE.md, Type section). \textwidth here is 166 mm
(report/preamble.tex:2, a4paper with 22 mm side margins), so 10 px at full width is
5.7 pt and the 9 px floor is 5.2 pt. At 0.78 fig01's monospace edge labels render at
4.5 pt, the equivalent of 7.8 px. fig05 and fig07 at 0.86 render at 4.9 pt, fig06 at 0.88
at 5.1 pt. Only fig02 clears the floor with room; fig03 and fig04 sit exactly on it.
Fix: set `width=\textwidth` on fig01, fig05, fig06 and fig07, or raise their smallest
font-size so the rendered size stays at or above 5.2 pt.
Severity: should.

**F6. fig07's farm-pilot band drops four services instead of marking them "not stated".**
File: figures/fig07-layouts.svg:147 (the staging card that lists them) against the
farm-pilot containers at lines 92 to 108.
Quote: `Alertmanager` (staging storage 1, line 147 lists `Alertmanager` and line 148 `the
poller`; line 153 `the trend rollup`; line 158 `the live lane`)
The figure has a convention for exactly this case, stated in its own legend: `not stated on
the farm` in grey italic (fig07-layouts.svg:187), and it uses it for the stamper, the
projector and the shifter view. Alertmanager, the poller, the trend rollup and the live
lane appear in the staging band and in the production band but are simply absent from the
farm-pilot containers, so a reader concludes the farm pilot has no Alertmanager and no
poller. The source says only which services are stated, not that these are missing: "Only
the cluster, the collector and Dashboards are stated to run there"
(sections/07-limits-close.tex:33); the brief marks the rest "farm not stated"
(briefs/target-architecture.md:75).
Fix: list the four in the farm-pilot containers in the same grey italic the projector and
the shifter view already use.
Severity: should.

**F7. fig02's control host does not match the paragraph it illustrates.**
File: figures/fig02-architecture.svg:71 and 79.
Quote: `Dashboards, nginx` (line 71), `the ops page` (line 79)
Section 3.1 names six things on the control host: "Dashboards, and Alertmanager ... The
poller samples the cluster. The roster lists the collectors that should be alive. The
catalogue maintenance audits the template counts. The receiver stores what a person was
told" (sections/02-design-a.tex:11). The figure drops the catalogue maintenance, which
fig05 does put on the control host (fig05-templates.svg:112, under `03 CONTROL HOST`, and
dataflow-atlas.md:77 puts it there too), and adds two names the paragraph never uses: the
ops page and nginx. STYLE.md's content rule is "Names follow the report, not the code".
Fix: add a `the catalogue maintenance` card to the control-host group, and either name the
ops page in Section 3.9's list of surfaces or drop its card. "nginx" can become "the one
door", which is the body's own name for it (sections/03-design-b.tex:29).
Severity: should.

**F8. fig07 shows a "replay engine" on every staging worker that the report never defines.**
File: figures/fig07-layouts.svg:18 and 25.
Quote: `replay engine`
The word "replay" appears once in the whole report, inside the Appendix A table of things
with no upstream role: "Replay, faults, ops page, projector, shifter view & None & The
software exists only here" (sections/07-limits-close.tex:79). Nothing tells a reader what
a replay engine is or why a worker has one, and the body's worker tier has four services,
of which this is not one: "The collector reads the five log sources. The stamper beside it
names every line's template ... The health sample pushes the collector's heartbeat up. The
local OpenSearch data node holds one index per worker" (sections/02-design-a.tex:7). The
atlas supports the component itself (dataflow-atlas.md:509, the replay engine writes files
and InfoLogger rows into a worker's log paths), so the label is not wrong, only undefined.
Fix: either add a half-sentence to Section 6.3 or to the fig07 caption saying the replay
engine feeds staging workers with archived farm logs, or drop the line from the two staging
worker cards, where it is the only component with no counterpart in the other two bands.
Severity: should.

**F9. fig06: an edge label runs across an edge.**
File: figures/fig06-alerts.svg:48.
Quote: `health sample, HTTP, every 30 s`
The label is anchored at x 404 with `text-anchor="end"`, so at 10 px Menlo it starts near
x 224. The vertical segment of the `log records, 14 detectors` edge is at x 230
(`M230,84 V232`, fig06-alerts.svg:38), so the rule passes through the first two letters of
"health". Visible in the rendered PNG.
Fix: shorten the label to `health sample, every 30 s` or move it right, so it clears x 236.
Severity: should.

### NIT

**F10. fig04's top-right note is flush with the right edge of the box.**
File: figures/fig04-collector.svg:13.
Quote: `writes only to the OpenSearch node on the same worker`
It is anchored at x 820, the viewBox edge, while the memory-envelope figures in the same
file stop at x 800 (fig04-collector.svg:112 to 116) and the hairline under it runs to 820.
The last glyph sits on the frame.
Fix: anchor it at x 808 to match the other right-aligned items.

**F11. fig01's dashed edge is labelled with the request while the arrow shows the answer.**
File: figures/fig01-logging-today.svg:76.
Quote: `query, tcp 3306`
Round-1 nit, still open. The arrowhead lands on the desktop browser
(fig01-logging-today.svg:74, `M584,303 H722 V222`), so what travels along it is the result
set, not the query.
Fix: write `query results, tcp 3306`.

**F12. fig04 spends the accent three times, on nothing the caption is about.**
File: figures/fig04-collector.svg:66, 84, 89.
Quote: `256 MB on disk`
STYLE.md says "Use the accent once or twice per figure, for the thing the caption is
about." The stamper card, the buffer card and the crossed-out link are all in alice-red,
while the caption is about the five steps and the 64-chunk bound
(sections/02-design-a.tex:55), which are drawn in plain ink.
Fix: keep the red on the disk buffer, which band 02 is about, and set the stamper card in
plain card-and-ink.

**F13. fig08-flush is still built and still cited by nothing.**
File: figures/fig08-flush.svg (and .pdf, .png).
Quote: `\begin{table}[htbp]\caption{Flush 1 second beats the shipped 5 seconds on every
measure, at 5,000 records a second on the re-run rig.}\label{tab:flush}`
(sections/05-eval-a.tex:21) is the only thing in Section 5.2 about the flush decision. No
`\includegraphics` anywhere names fig08. Round-1 nit, still open. The outline marks it
optional (outline.md:171).
Fix: cite it beside Table 7, or delete the three files so the folder holds only what the
report uses.

## Nothing found at these

- No version number appears in any figure. No Ansible role name, playbook name or file
  name appears in any figure; the only monospace identifiers are index names, field names
  and port numbers, which STYLE.md allows.
- The two Kafka decisions are not merged. Only the live-lane bus is drawn, in fig02 (line
  115, "live lane only · one topic, one consumer · agreed, not built") and in fig07 (line
  175, "Kafka brokers carry the live lane only · broker placement not decided"). The
  rejected queue between the collector and the store, and its three brokers on the storage
  machines, appear in no figure.
- No drawn logo, gradient, glow or drop shadow. Every logo is an `<image>` reference into
  figures/logos/.
- Every one of the seven figures is cited by name in the body.
- No caption contradicts its drawing.
