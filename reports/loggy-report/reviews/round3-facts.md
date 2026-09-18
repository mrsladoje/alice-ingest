# Round 3, facts lens

Scope: every mechanism and status claim in the eight parts, against the briefs and, where a brief
cites code, the code. Round-2 must findings were re-checked first.

## Round-2 must findings: all fixed

All 33 round-2 "must" findings across the eight parts are applied, and none broke a neighbouring
sentence. Spot checks that mattered:

- The live-lane definition at 02-design-a.tex:13 and its echo at 03-design-b.tex:31 now agree, and
  both agree with 07-limits-close.tex:27.
- The 20,000-template ceiling now says one thing in both places: eviction in 3.5, the 208.1 MB
  memory figure in 5.5, with no second behaviour.
- The three storage nodes are named as the control, background and shifter hosts, so the five-machine
  count closes.
- The farm's three primaries carry the same status word in 3.1 and in 4.
- 05-eval-a.tex:56 no longer says "all four now pass our checks".
- Appendix B is gone, so its two wrong cells went with it.
- The whole-stack-only 5,000 a second claim is corrected at 07-limits-close.tex:11.

## Must (3)

### 1. "three of the five families" contradicts the report's own family count

File: sections/05-eval-a.tex, line 54.
Quote: "The collector had never read three of the five families."
Problem: the report's five families (Table 1, 01-intro-problem.tex:19) count the O2 process tree as
one family in two line formats, so the three unread sources were two families and one format, not
three families.
Source: briefs/soak-4.md:216 ("Three of six log sources had never been read by the collector, and the
fourth was two formats. Round 7 added `datadist`, `ildaemon` and `journald`, and split `dpl` out of
`stdout`"); briefs/soak-index.md:23.
Fix: "The collector had never read three of the six log sources."
Severity: must.

### 2. The number of open routes for detection on log text is wrong and self-contradicting

File: sections/04-why.tex, line 51.
Quote: "Three routes for text stay open, and none is chosen."
Problem: two of the three text routes were rejected and exactly one stays open, which is what
07-limits-close.tex:46 already says, so the two sections disagree.
Source: docs/explained/ANOMALY_DETECTION.md:330-334, 343-352, read through briefs/why.md:175-176
("k-NN on log messages. Rejected on cost... Templating as the detector. Rejected... Templates then
k-NN stays open").
Fix: "One route stays open: mine the templates, then run nearest-neighbour search on those."
Severity: must.

### 3. The dead-collector walkthrough contradicts its own timing bound

File: sections/03-design-b.tex, line 19.
Quote: "Within 30 seconds the poller writes the collector's row with a missing flag."
Problem: the poller marks absence only after 90 seconds of silence, so the first missing flag arrives
60 to 150 seconds after the death, as the next paragraph of the same section states.
Source: briefs/walkthroughs-3-5.md:64 ("The poller flags a collector whose last heartbeat is older
than 90 seconds (metrics_poller.py:16-17, 100-102). It ticks every 30 seconds... First fleet document
with heartbeat_missing 1: 60 to 150 seconds after death").
Fix: "Every 30 seconds the poller writes one row per rostered collector, and flags the collector that
has been silent for 90 seconds."
Severity: must.

## Should (4)

### 4. The control host is said to hold every screen, and the same paragraph gives one to another host

File: sections/02-design-a.tex, line 11.
Quote: "The control host holds every screen: Dashboards, and Alertmanager, which decides when a person
is told."
Problem: the shifter view is a screen and runs on the shifter host, as the same paragraph says four
sentences later, so the reader gets two placements for the surfaces.
Source: briefs/why.md:141 ("It runs off the control host because its cost grows with readers");
briefs/target-architecture.md:11 (shifter view on node-05).
Fix: "The control host holds the one web door, Dashboards, and Alertmanager, which decides when a
person is told."
Severity: should.

### 5. A farm status claim the sources do not carry

File: sections/07-limits-close.tex, line 35.
Quote: "The stamper, the projector and the shifter view have run on staging only."
Problem: the sources record no farm run for these three services and also record no absence, so the
report states a negative status it cannot support.
Source: briefs/target-architecture.md, doubt 9 ("Nothing read says whether the stamper, the poller,
the projector, the rollup, Alertmanager, the receiver or the shifter view ran on the farm. Say 'not
stated' rather than 'deployed'").
Fix: "Whether the stamper, the projector and the shifter view ran on the farm is not recorded."
Severity: should.

### 6. The recipe definition gives each family a depth it does not own

File: sections/06-eval-b.tex, line 7.
Quote: "A recipe is one family's depth, similarity and separator setting."
Problem: tree depth is one module-wide value shared by all seven families, so depth is not part of a
family's recipe.
Source: briefs/soak-index.md:179 ("The recipe as committed: depth 8, max children 100, similarity 0.4
for InfoLogger and process logs and 0.5 for dds... Depth 8 is a module global, so all seven families
share it").
Fix: "A recipe is one family's similarity and separator setting, on a tree depth every family shares."
Severity: should.

### 7. "All four" changes meaning between two sentences, and release 5.0.8 arrives unannounced

File: sections/05-eval-a.tex, line 56.
Quote: "All four passed the fixture and restart checks."
Problem: the four that passed were four collector releases, one of which, 5.0.8, is not deployed on
the farm, while the preceding sentence counts four farm installations on three releases.
Source: briefs/soak-4.md:41-42 ("4 | collector versions deployed on the farm: 4.0.1 on epn146 and
epn228, 4.0.14 on epn323, 3.2.8 on epn-infra13"; "fixture, journal and restart checks on each of
3.2.8, 4.0.1, 4.0.14, 5.0.8").
Fix: "The checks ran on the three farm releases and on one newer release. All four passed the fixture
and restart checks."
Severity: should.

## Checked and correct

Counts: 30 monitors (15 at one minute, 13 at ten minutes, 2 hourly; the report's 13 + 4 + 9 + 2 + 2
adds to the same 30, verified against the 30 files in the monitor set), 17 detectors (17 files, 6
reading the local index), 1 forecaster, 9 trend comparisons, 2 shipping-lag rules, 2 break-glass
monitors, 7 injection scenarios, 22 causal edges, 99 defects of which 32 in the instrument.

Placements: projector and trend rollup on the background host, catalogue maintenance and receiver on
the control host, shifter view and semantic service on the shifter host, live lane posted by the
collector to the shifter host.

Mechanisms: the create-action document identifier, the 64-chunk and 256 MB envelopes, the 90-second
absence window, the 0.5 episode floor against the 0.7 page tripwire, the 24-hour guard on the
stale-projector monitor, the least-recently-used eviction in the shipped stamper (verified in
stamper.py, Trees.evict), the cover relation, the hourly audits.

Numbers: every figure in Sections 5 and 6 matches its brief, including 1.61 and 9.48 % noise floors,
75.32 and 11.19 core-seconds per million, the flush table, 7.4 / 11.5 / 26.5 % threading arms, 89.3 %,
41 to 68 %, 66.7 %, 865,674 records at 310 bytes, 17.4 and 5.1 hours, 42,000 and 50,000, 86 %, 1.8 %,
263 seconds, 538 and 4.3 and 54 times, 19.3 %, 3,011 / 4,092 / 4,221, 48.9 per million, 208.1 MB,
2.56 and 7.8 times, 18,037 a core-second, 0.636, 5.4 times, 0.685 / 0.634 / 0.689 / 0.371,
377 / 204 / 29 of 110, 0.020, 0.048, 1 ms and 12 ms, 0.6 ms.
