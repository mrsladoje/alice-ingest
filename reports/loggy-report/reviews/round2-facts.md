# Round 2 review, facts lens

Scope: every claim about how the system works and its status, in the eight section files, against the briefs and, where a brief cites code, against the code. Round-1 findings that are now fixed are not repeated.

Verdict: seven must findings, five should findings, three nits. Four of the seven musts are contradictions between two parts, where the code settles which half is wrong.

Round-1 must findings that are now fixed and are not repeated here: the 96.94 % scope in Sections 3.3, 4 and 5.4; the retracted 11.18 core-seconds and the 1.8-times multiple; the four collector installations and their three releases; the trend slice ends; "one per entity" for detectors; the 90-to-270-second sum; the 31,000 lines of Python; the flush-table order of 17.57 and 17.65; the transport 15 % against the hop 6.5 % in Section 5.5.

---

## Must

### 1. The live lane is defined as severe records only, and it carries every InfoLogger record

File: sections/02-design-a.tex:13

> The live lane is the second copy of every severe record, posted by the collector straight to the shifter view.

Problem: the live lane carries every InfoLogger record at any severity, plus the daemon log and the central family, so "every severe record" understates it by the largest family in the mix.

Source: `deploy/roles/loggy_collector/templates/collector.yaml.j2:658` (`match_regex: ^stamped\.(infologger|ildaemon|family\.central)$`); `reports/inputs/dataflow-atlas.md:30` (audited walkthrough 2, step 5); the report's own Table 3, which sends every InfoLogger record to storage regardless of severity.

Fix: "The live lane is the second copy of every record that leaves the worker: every InfoLogger record, the daemon log and every line above info severity." Section 3.9's "Its live page keeps the severe lines collectors post to it" needs the same correction.

### 2. The stamper both drops the least recently used template and stops learning

Files: sections/02-design-a.tex:71 and sections/06-eval-b.tex:11

> The stamper holds at most 20,000 clusters per worker and drops the least recently used. The archive pass in Section~\ref{sec:eval-templating} stops learning at that ceiling and keeps counting.

> The tree stops learning at 20,000 templates, at 208.1~MB inside the stamper's 512~MB memory limit.

Problem: the two parts state opposite behaviours at the same ceiling, and the second is also attributed to an archive pass that produced 4,221 templates and never reached 20,000.

Source: the shipped stamper evicts: `deploy/roles/loggy_collector/files/stamper.py:301-311` (an OrderedDict of recent clusters, `while self.learned > self.max_templates: self.evict()`) and `stamper.py:276-281` (evict pops the oldest); `stamper.py:40` sets the cap at 20,000. The "stops learning and keeps counting" wording is the soak's prescription at `docs/SOAK_RESULTS.md:5129-5136`, which the code does not implement. The memory figure is `docs/SOAK_RESULTS.md:5107-5117`.

Fix: keep the eviction sentence in 3.5, delete "The archive pass ... stops learning at that ceiling and keeps counting", and write in 5.5: "The tree is capped at 20,000 templates, 4.7 times what the whole archive produced, and holds 208.1~MB there, inside the stamper's 512~MB memory limit."

### 3. "A silent host scores zero" reverses what zero imputation does

File: sections/03-design-b.tex:5

> Seventeen detectors run, one model per entity, and a silent host scores zero.

Problem: zero imputation makes the counted value zero, not the anomaly grade, and it is set on the volume detectors only. As written the sentence says silence is graded as normal, which is the opposite of the design's purpose.

Source: `deploy/roles/loggy_anomaly_detection/files/detectors/il-per-epn.json:51-53` (`imputation_option` ZERO); only the volume detectors carry it (`il-per-epn`, `central-per-epn`, `local-volume`, `ingest-flow` and their slow twins); `briefs/why.md:16` ("Volume detectors use zero imputation so silence reads as volume 0, lag detectors do not", `docs/explained/ANOMALY_DETECTION.md:34-37`).

Fix: "Seventeen detectors run, one model per entity, and a volume detector reads a silent host as zero records, so silence is a value it can grade."

### 4. The farm's three primaries are called "not applied" in Section 4 and "set" in Section 3.1

File: sections/04-why.tex:39

> The farm runs three primaries, agreed and not applied, because one primary funnels all InfoLogger through one machine.

Problem: the farm inventory already carries the setting, and Section 3.1 says so ("Three primaries are set for the farm and have never carried farm volume"), so the two parts disagree on status.

Source: `deploy/inventory.epn.yml:204` (`log_primary_shards_storage: 3`, with the comment at :193); `briefs/why.md:6` ("the farm value of one primary per storage node is agreed"); `briefs/target-architecture.md:199` (doubt 1).

Fix: "The farm sets three primaries, one per storage node, because one primary funnels all InfoLogger through one machine. No farm run has yet carried that volume."

### 5. "All four now pass our checks" repeats a retracted result and contradicts the next sentence

File: sections/05-eval-a.tex:56

> All four now pass our checks.

Problem: round 10 withdrew round 9's "all four versions pass" because the rotation check failed one run in three, and the same paragraph then says release 5.0.8 loses every byte after rotation and is blocked. A release cannot pass our checks and be blocked by them.

Source: `briefs/soak-index.md:311` (retraction, `docs/SOAK_RESULTS.md:4287-4288, 4304-4306`); `briefs/soak-5.md:18` ("5 of 5 lost ... deterministic test"); `briefs/soak-5.md:220` ("A reconciler must not carry 'all four versions pass' from Round 9").

Fix: "All four passed the fixture and restart checks. A later review found the rotation check itself was flaky, and release 5.0.8 loses every byte appended after rotation, in five of five runs, so it is blocked."

### 6. The control, background and shifter hosts are never tied to the three storage nodes

File: sections/02-design-a.tex:11

> The background host runs the projector, which turns alerts into episodes, and the trend rollup. The shifter host serves the shifter view, the page the person on shift watches. Staging is five machines: two workers and three storage nodes.

Problem: the paragraph names three more hosts than the five machines it then counts, and no sentence anywhere says the three storage nodes are those hosts. A reader counts eight machines on staging and cannot place the projector, the rollup or the shifter view on the farm pilot either.

Source: `deploy/README.md:75-80` (the control machine is the first storage node, the projector runs on -4, the live lane on -5); `briefs/target-architecture.md:46, 61, 71` ("Control host, the first storage node", "Background host, the second storage node", "Shifter host, the third storage node").

Fix: add one sentence before "Staging is five machines": "The three storage nodes carry these services as well: the first is the control host, the second the background host, the third the shifter host."

### 7. The soak appendix still gives 96.94 % as a share of all lines

File: sections/07-limits-close.tex:103

> 7 & Three sources added. 96.94~\% of lines stay local. \\

Problem: 96.94 % is the share of the O2 process-tree family, which the body now states correctly three times; the appendix cell still reads as all lines, and InfoLogger alone is 58.1 % of the corpus and crosses the wire in full.

Source: `docs/SOAK_RESULTS.md:3115-3116` via `briefs/soak-index.md:211` and `briefs/soak-4.md:23`.

Fix: "7 & Three sources added. 96.94 % of process-tree lines stay local."

---

## Should

### 8. The catalogue's second hourly check is described as two families agreeing with each other

File: sections/02-design-a.tex:73

> The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals, and that both storage families agree on versions per worker and hour.

Problem: the check compares what the workers stamped against what the two shared indices hold, not the two families against each other.

Source: `deploy/roles/loggy_template_catalog/files/catalog_maintenance.py:406-427` (indexed counts per node, family and hour) and :430-447 (stamped against indexed); `briefs/walkthroughs-6-9.md:74, 87`.

Fix: "...and that neither shared index holds more records of a template version than the workers stamped for that worker and hour."

### 9. Table 2 promises two copies where the design keeps three

File: sections/01-intro-problem.tex:62

> Durable & A storage tier of three machines, with two copies of every record above info and of every InfoLogger record \\

Problem: one primary and two replicas is three copies, one on every storage machine, which is what Section 3.2 step 5 and Section 6.3 say; the objective row reads as two copies in total.

Source: `briefs/target-architecture.md:94-95` (2 replicas on both storage families); `reports/inputs/deck-text.md:143-144` via `briefs/constraints.md:282` ("one copy on every storage node"); the report's own "copies it to two replicas" at 02-design-a.tex:42.

Fix: "three copies of every record above info and of every InfoLogger record, one on each storage machine".

### 10. The collector's own clock disappears from the walkthrough that introduces the clocks

File: sections/02-design-a.tex:38

> \item The collector tails the file an O2 process writes, reads machine, program and event time, and adds a document identifier.

Problem: the collector stamps its accept time in the same filter that mints the identifier, and Section 3.9's two clocks, the detectors' window field and the shipping-lag rules all rest on it. As condensed, the only clock the report says anyone stamps is the ingest time in step 4.

Source: `deploy/roles/loggy_collector/templates/collector.yaml.j2:279-288` (`record['doc_id']` then `record['collector_time']` in the same Lua filter); `reports/inputs/dataflow-atlas.md:15` (audited); `briefs/why.md:35` (dual clock).

Fix: "...reads machine, program and event time, and adds a document identifier and the collector's own accept time."

### 11. "Three lanes read those numbers" points only at the health samples

File: sections/03-design-b.tex:5

> Three lanes read those numbers.

Problem: the antecedent is the pushed collector heartbeats and the poller's cluster samples, but fourteen of the seventeen detectors read the three log families, and the nine trend rules read rows rolled up from the logs. As written, detection reads only platform health.

Source: `reports/inputs/dataflow-atlas.md:196` ("Fourteen log detectors read the three log families per origin_host ... Three read cockpit-metrics"); `briefs/walkthroughs-3-5.md:141` (every trend monitor reads trend-rollup).

Fix: "Three lanes read numbers: the health samples above, and the counts, error rates and lags rolled up from the logs themselves."

### 12. The appendix gives the round 21 saving to the whole hop

File: sections/07-limits-close.tex:108

> 21 & Stamper hop 15~\% cheaper. \\

Problem: 15 % is the transport step alone; the hop fell 6.5 %, which is what Section 5.5 now says, so the appendix contradicts the body.

Source: `docs/SOAK_RESULTS.md:5933-5935, 6005-6022` via `briefs/soak-index.md:27` and `briefs/soak-5.md:9`.

Fix: "21 & Stamper transport 15 % cheaper, the hop 6.5 %."

---

## Nits

### 13. The seventh injection scenario breaks nothing

File: sections/06-eval-b.tex:50

> Seven scenarios each score whether a signal, an incident and a notification followed one thing broken on purpose.

The seventh is `observe-only`, a control run with no fault. Source: `deploy/roles/alice_ops/files/inject_run.py:61-69`; `briefs/why.md:34` names six fault scenarios. Fix: "Six fault scenarios and one control run each score whether a signal, an incident and a notification followed."

### 14. One external review retracted the regex costs, not two

File: sections/05-eval-a.tex:58

> Two external reviews retracted every collector regex cost figure.

Round 9 is the review that retracted them, as wall clock. Source: `briefs/soak-index.md:306` (`docs/SOAK_RESULTS.md:4192-4197, 4157-4188`, both round 9); `briefs/soak-4.md:8`. Fix: "An external review retracted every collector regex cost figure."

### 15. Not every worker has 128 logical processors

File: sections/02-design-a.tex:7

> The four take four of a worker's 64 physical cores, eight of its 128 logical processors.

The third farm worker has 192 logical processors and a terabyte of memory, so the farm is not one hardware generation. Source: `docs/LOG_TYPES.md:267` via `briefs/constraints.md:30`. Fix: write "of a worker's 64 physical cores, eight of its 128 logical processors on the machines we measured", or drop the possessive and say the budget is four physical cores.
