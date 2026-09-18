# Outline: the loggy project report

Format: LaTeX, A4, one column, 12 to 16 pages, CERN Summer Student Programme 2026 report. Author Marko Sladojevic. Supervisors Lubos Krcal and Federico Ronchetti. The system is described as the agreed target design after the September 2026 rework, at component level. Status marks say what is built and what is agreed and not built. All four result files are in scope.

Word budget: about 7,000 words of body text, 8 figures, 8 tables. That lands at 14 pages with Libertine 11 pt.

Every section below names its claim (the sentence the section exists to prove), its briefs, its figures and tables, and its word budget. Writers draft from the briefs only.

## Decisions taken while reading the briefs

These settle contradictions the briefs flagged. Change any of them before the draft starts.

1. Title: "loggy: a logging platform for the ALICE Event Processing Nodes". Subtitle: "Keep the volume where it is made, and the value where it is safe."
2. The projector and the trend rollup run on the second storage node, off the control host, as the sources say. The task text that put the projector on the control host was wrong.
3. Storage-tier indices: on staging one primary shard and two replicas (one copy on every storage machine). On the farm three primaries, one per storage node, two replicas each. Both stated, each with its layout.
4. Counts: 30 monitors (28 post into the alert index, 2 break-glass monitors post straight to the receiver), 17 detectors, 1 forecaster. The deck's 28 is the older count.
5. The shifter view is built: a small Preact page with a live page and a Templates page, fed by the collectors, holding when the cluster is down. The InfoLogger-like operator view is a separate thing and is not built. The two never merge.
6. Templating is presented as built for search and for the template catalog. (Revised 2026-09-18.) A standalone section after the measurements covers anomaly detection on the log text, which the platform does through templates (new-template rule built, per-template counts published and not yet read): every other route weighed and why each lost, templates embedded instead of lines, template counts as the numeric lane, semantic search as the only use of embeddings, and nearest-neighbour novelty closed because a new template is already the novelty signal.
7. Semantic retrieval is built and evaluated and ships disabled by configuration. The report gives the 384 MB service ceiling from the configuration and says the model's memory peak was observed in a local run and is not recorded in the results file.
8. Numbers: round 18 and round 21 figures are quoted as percentages, never as absolute core-seconds. The flush decision uses the three-alternating-run set (36.70 to 27.70 collector core-seconds, 87.1 to 62.7 MB). The ceiling is about 42,000 records a second sustained on the laptop rig, 50,000 held for two minutes with zero loss. Cost per record was measured at 5,000 a second on the re-run rig, and the ceiling separately; the report says both in one sentence.
9. Buffer hold: the InfoLogger output has its own 256 MB buffer, which holds 865,674 records of 310 bytes. InfoLogger is 60 % of a worker's mix, so the source's 17.4 hours (at 23 records a second) and 5.1 hours (at 78) are correct for that lane and the report quotes them with that meaning. (Corrected after the draft: an earlier version of this decision derived 10.5 and 3.1 hours from the whole-mix rate, which was wrong.)
10. Memory envelope: 133 to 228 MB steady, 373 MB peak in the sink-outage run with the shipped 256 MB buffer (the deck's 404 MB was the 2 GB buffer run).
11. Two Kafka decisions, two paragraphs, never one. The soak rejected a queue between the collector and the store on measured numbers. The rework agreed a bus for the live lane only, for decoupling, not durability. The three-brokers-on-the-storage-machines figure from the deck belongs to the rejected queue and is not carried over. Broker placement for the live-lane bus is not decided, and the report says so.
12. Version numbers appear twice only, where a result depends on them: the collector release that loses bytes at file rotation, and the OpenSSL wall that pins two farm nodes to an older collector.
13. No Ansible role names, playbook names or file names in the body. Components are named by their job.
14. The rejected upstream roles table and the Telegraf and Mimir overlap go in the deployment section and an appendix, as the review asked.
15. The deck's "ten named O2 process logs" is not repeated. The survey counted 13 programs on the scratch disk and 186 in the archive.
16. British spelling.

## Front matter

Title, author, "CERN, ALICE, Event Processing Nodes", supervisors, September 2026, abstract (about 220 words: goal, method, headline numbers with references, main limit), table of contents.

Abstract headline numbers: a median worker carries 23 records a second in the busiest hour of six months of archive, the busiest worker-second is 78; the collector costs about a quarter of one core at 20,000 records a second; the whole stack on a worker sustains about 42,000 records a second on a laptop rig; moving the flush interval from 5 seconds to 1 cut the collector's own cost by 24.5 % and its peak memory by 28 %; the templating pipeline is 31 to 57 % cheaper per family after the rewrite and byte-identical on 8,961,245 lines; dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634 for the dense model alone. Main limit: every cost figure comes from a laptop rig, and only shapes, knees and rankings transfer to the farm.

## 1 Introduction (500 words)

Claim: the logs an EPN writes mostly never reach the one tool people use to read them, and a platform can keep them where they are made and still search them as one.

- The EPN farm and its real job (timeframe reconstruction). What an EPN is, in one sentence.
- Logging today: InfoLogger moves what a process sends it, into one process and one table for the whole farm. Three limits: one table, no aggregation, one policy and one copy. What never reaches it: process logs on disk, the journal, host metrics.
- What this project built, in one paragraph, and what the report shows: the problem, the design, why each choice, what the measurements say, what is not done.
- Reading guide: the walkthroughs (sections 3.2, 3.3, 3.7, 3.8) follow one line or one failure through the whole system.

Briefs: constraints.md (sections 1, 2), style.md.
Figure 1: Logging today. The InfoLogger path (process, local daemon, central server, one table, the desktop browser) beside the three things a node writes that never take that road. From slide 3.

## 2 The problem and the constraints (1,100 words)

Claim: the constraints are what made the design. State them before the design.

2.1 What a node writes. The five log families (DDS, the O2 process tree in two line formats, the InfoLogger daemon log, the run orchestrator log, the systemd journal) plus InfoLogger records. Where they live on a real node. Two rules learned from the survey: a shared scratch disk seen by every node, and a process that prints raw terminal colour codes. What the archive holds and what it does not (the journal is absent). Table 1: the log sources on a worker (family, what writes it, how it reaches the collector, which tier it lands on).

2.2 What a worker carries. Six months of archive: 179 dumps, 248,828,513 records, 312 hosts. Busiest hour: 23 records a second at the median worker, 78 at the busiest worker-second, 9,781 across the farm. Floors, not measurements, because the InfoLogger client drops a process above 1,000 messages a minute. Why the soak tested 1,000, 20,000 and 50,000 a second per worker anyway: a safety margin chosen, not derived.

2.3 The constraints. One bullet each with its source: four of 128 logical processors and little memory, with reconstruction on the other 124; no bulk over the wire; no cross-worker shipping; nodes shared with physicists and one wiped by a build pipeline; a shared infra machine that already runs another cluster on the default ports; no long-running agents on CERN hosts; the collector release wall on the two older nodes; the InfoLogger client's flood limit.

2.4 What the platform must do. The six objectives in the deck's words. Table 2: the six objectives and the design element that meets each.

2.5 The 2025 reference design. What the previous summer student proposed (a collector, one cluster with two tiers, a queue feeding an aggregator, Grafana with Alertmanager) and what loggy kept, changed and dropped, one sentence each.

Briefs: constraints.md, soak-index.md (2g), why.md (entry on the reference design).

## 3 The design (2,600 words)

Claim: one cluster, split in two by severity. The bulk never leaves the worker. The valuable half is copied three times. Everything that watches the platform is pushed up, never polled down.

3.1 One cluster, two tiers (400). The worker tier (collector, stamper, health sample, a local OpenSearch data node, one local index per worker with one copy). The storage tier (three replicated nodes; on the farm three containers on one infra machine). The control host, the background host and the shifter host, each with its services named by job. The bus, agreed and not built, as a ghost. Index families with shards, replicas and retention: local info 8 days, central 35, InfoLogger 56. Figure 2: the target architecture. Table 3: index families (what lands there, tier, shards and replicas per layout, retention).

3.2 Walkthrough: one error line, from an EPN to a screen (350). The audited atlas walkthrough 1, in the report's voice: tail, stamp with a document identifier and a clock, parse, route by severity, the stamper loop, write to localhost, the ingest pipeline, one hop to the storage primary, two replica copies, the live lane copy, the query from Dashboards. Figure 3: the path of one error line, hosts as columns.

3.3 Walkthrough: an info line stays on its worker (250). The audited atlas walkthrough 2: the same steps until the retag, then the local index pinned to the same machine, and how one query from a storage machine still reaches it. Federated search: ask once, every node answers, the cost of fanout, and why the live lane exists so that the cluster is asked only when it must be.

3.4 Inside the collector (400). The five steps a line takes. The memory envelope (64 chunks, 128 MB documented worst case, 133 to 228 MB measured steady, 373 MB measured in a sink outage with the shipped buffer). The disk buffer: what it is (a hiccup layer that survives a restart) and what it is not (durability; a lost node loses its records). The document identifier and the create action, so a resend cannot duplicate. Flush 1 second. Figure 4: inside the collector, with the stamper loop and the buffer. From slide 9.

3.5 Templates in band (300). What a template is. The stamper beside the collector: masker, one drain tree per family, template_version and template_status on every record before it is indexed, bucket counts every 300 seconds, the catalog, the hourly checks, the cover relation. The walkthrough 7 chain from a line to the catalog, corrected (expiry runs first, checks last). Figure 5: from a line to the template catalog.

3.6 Watching the platform (450). Health is pushed: every collector ships its own sample every 30 seconds; the poller samples the cluster and marks absence from a roster after 90 seconds. Three lanes read the numbers: threshold rules (the cliffs we know), Random Cut Forest detectors (one model per entity, no threshold; 17 detectors plus one forecaster), trend rules (nine comparisons against a frozen seven-day baseline every ten minutes). What each lane is for, defined before its details. Episodes: many hits, one machine, one trouble. The projector turns alerts and detector results into signals and incidents, and re-posts active episodes to Alertmanager every 30 seconds because Alertmanager keeps nothing. Alertmanager owns notification semantics only. The receiver stores what a person was told. The break-glass path for the two monitors that watch the projector and Alertmanager themselves. Figure 6: from numbers to one alert, the chain the supervisor asked to see drawn.

3.7 Walkthrough: a dead collector becomes a notification (300). Atlas walkthrough 3, corrected: heartbeats stop, the poller derives absence, the collector-down monitor fires per collector, the projector opens an episode with a page severity, Alertmanager groups on the alert name after a 30-second wait, the receiver stores one notification. Bound: 90 to 270 seconds from death to the stored record, from the cadences; not timed as a measurement.

3.8 Walkthrough: an EPN goes quiet, or loud (200). Atlas walkthrough 5, corrected: rollup every ten minutes, small rows, nine trend rules against a week of history, warn tier batched per rule name. What the template lane adds: which messages changed.

3.9 The surfaces (200). One door on the control host. The maintainer cockpit (is the pipeline healthy), the live lane (what is arriving now, holds when the cluster is down), Discover (anything in the archive), the Templates page. Two clocks: ingest time for health, event time for logs. The InfoLogger-like operator view is not built.

Briefs: target-architecture.md, walkthroughs-3-5.md, walkthroughs-6-9.md, the atlas walkthroughs 1 and 2 (reports/inputs/dataflow-atlas.md lines 11 to 31), soak-index.md (2b), why.md.

## 4 Why these choices (1,300 words)

Claim: each choice has a named alternative and a fact that decided it.

One short paragraph per choice, in the deck's form: goal, candidates, the deciding fact, status. Order:
- The collector: Fluent Bit against Fluentd, Vector and Telegraf; and why not a queue at the edge (a broker moves records, it does not make them). Table 4: collector candidates.
- The store: OpenSearch against Elasticsearch, ClickHouse and Loki. Detection decided it. Table 5: store candidates.
- One cluster against many clusters joined by cross-cluster search. The open limit at hundreds of nodes.
- The split by severity, not by source, and the shard and replica counts.
- Native systemd on staging; containers only on the shared infra machine.
- Dashboards; the shifter view as a standalone page rather than a Dashboards plugin (a plugin is rebuilt per store release).
- The stamper in band with drain3, against PIPLUP and the other parsers, on cost. The masker rewrite.
- Threshold rules, Random Cut Forest and trend rules inside the cluster, against learned models on the log text (not chosen; text detection goes through templates, see the log-text section).
- Episodes, and Alertmanager as receiver only.
- The two Kafka decisions, two paragraphs.
- Deployment: push over SSH against Puppet, Salt, Chef and Terraform; placement is data; gates stop the run. Table 6: configuration tools.
- Cockpit metrics against the Telegraf and Mimir estate CERN already runs: the overlap conceded, the roster and absence logic kept.
- Shared Python as one package rather than store plugins.
- Upstream roles that were weighed and not used: pointer to Appendix A.

Briefs: why.md, soak-index.md (2f, 2h, 2i), constraints.md (section 7).

## 5 What the measurements say (2,300 words)

Claim: the numbers settle the worker's cost, the collector's ceiling and the templating cost, and they say plainly what they cannot settle.

5.1 The instrument and its limits (300). Everything was measured on a laptop; shapes, knees and rankings transfer, absolute rates do not. Two rigs (healthy at 20,000 a second; re-run at 5,000 on a slowed host), never averaged. Noise floor 1.61 % at 20,000 a second and 9.48 % at 1,000. Cost per record falls as rate rises. Most of stage C onward was measured on a saturated rig and retracted; the report uses only what survived the re-run. The nine defects the rig itself had are the reason to state this.

5.2 The collector under load (600). Round 1 memory (133 to 228 MB steady, the buffer and memory limits move together). The flush decision: Table 7 (shipped 5 s against chosen 1 s: total, collector cost, peak memory, live-lane latency floor). The threading arms all cost more than the shipped setting. The live lane is the largest single cost (41 to 68 % more collector time). A fifteen-minute sink outage at 20,000 a second loses two thirds of InfoLogger at the buffer cap; at a real worker's rate the same buffer holds hours (decision 9). The ceiling: 42,000 a second sustained, 50,000 for two minutes with zero loss; overload is safe, oldest-first discard, drain to empty. Heap size and internal core placement make no measurable difference. External pinning of four exclusive cores is required and was never an arm. Figure 7 (optional): the flush curve, total against collector cost.

5.3 Kafka between the collector and the store, decided on numbers (200). Decoupling: the stack carries 538 times the busiest worker-second. Durability: the buffer already holds hours. Cost: three brokers, memory the machines do not have, and the routing. What would reverse it. Kept apart from the live-lane bus.

5.4 The other sources and the real node (400). Rounds 7 to 9: three sources the collector never read, the process tree split into two formats, severity out of 99.83 % of process-tree lines, 96.94 % of that family stays on the worker; the census of four farm machines (four collector releases deployed, the journal is 2,500 to 10,500 entries a day, one collector release loses bytes at rotation); the external reviews that retracted every regex cost figure and found the catalog had shipped none of its guarantees. Rounds 10 to 20: 99 defects found and fixed across the collector, the stamper, the catalog and the instrument, with the four answers it took to find a correct scan position and the duplicated retries the file sink could never catch. Say what the soak was for: this list.

5.5 Templating (400). Round 4 and the soak: the parser choice on cost (PIPLUP 2.3 to 2.6 times drain3), the masker 85 to 87 % cheaper with identical output, one recipe per family, the template count as a curve (3,011, 4,092, 4,221 from 55,963,050 lines), the steady-state new-template rate 48.9 per million, round 18 (31 to 57 % cheaper per family, byte-identical on 8,961,245 lines), round 21 (the Forward hop into the stamper 15 % cheaper, protobuf and gzip rejected on that hop). Table 8: templating figures of record.

5.6 Embeddings and semantic retrieval (300). The unit is the template group, not the line. Corpus 5,301 groups from 56,628,579 lines. The ladder, why neighbour agreement was thrown away for source purity, the int8 warning (one backend, laptop). The final standing recommendation: dense scan by exact cosine, 30 candidates, late-interaction rerank, 0.685 against 0.634 (+0.028 to +0.080), held-out 0.689 against 0.371 (+0.318), 377 relevant templates gained and 204 lost on the live path, no approximate index, no fusion. Latencies are laptop figures. Ships disabled. Table 9: semantic figures of record.

5.7 The detection chain, proven by injection (100). Seven scenarios, the fault agents, what each proves (a stopped projector reaches the break-glass path), and what is not timed.

Briefs: soak-index.md, soak-1.md to soak-5.md, templating-embedding.md, semantic-1.md, semantic-2.md, walkthroughs-6-9.md.

## 6 Limits and what remains (700 words)

Claim: the platform runs, and these are the things it does not yet do or that were not measured.

6.1 Not measured. The laptop-to-farm conversion factor (one micro-benchmark on epn228 would give it); the cost of sharing the four cores with reconstruction; the burst gap; the full stack's cost above 5,000 a second; the dead-collector chain timing; detector warm-up; the collector's real ceiling; the semantic path on farm hardware; the 64-chunk burst ceiling on shared nodes.

6.2 Limits found in the design. The stale-projector monitor goes quiet after 24 hours of silence; the Alertmanager-down monitor needs a live projector; per-host trend rules skip a slice whose fleet count is zero rather than being inhibited; one cluster's manager set at hundreds of nodes; no authentication inside the cluster; the live lane will not see stdout info lines after the severity fix; DDS is under-sampled in every corpus and is the dearest family per line.

6.3 What remains, in build order. The live-lane bus (agreed); shared code as one package (agreed); the Telegraf decision for cluster and node samples (open); the ISM-plus-monitors form of catalog maintenance (open); the operator view close to InfoLogger (needs a live run); the system journal and per-device logs on a real machine; anomaly detection on the log text (three routes, none chosen); authentication inside the cluster; source-owner approval for reading node logs.

Briefs: soak-index.md (5, 6), walkthroughs-3-5.md, walkthroughs-6-9.md, target-architecture.md (5, 8), why.md, constraints.md (8).

## 7 Conclusion (250 words)

The question, the answer (yes, the logs can stay where they are made and be searched as one, on four cores, and the watching costs the worker nothing it did not push itself), the numbers that carry it, the one limit (laptop figures), what exists now: groundwork on five machines and one green run on the farm.

## Acknowledgements (60 words)

Supervisors. The previous summer student's reference design.

## References (about 12)

The 2025 reference design; Fluent Bit, OpenSearch, drain3 (He et al., Drain, 2017), Random Cut Forest (Guha et al., 2016), Alertmanager, Ansible documentation; the ALICE InfoLogger anomaly detection paper (Appl. Sci. 2025, DOI 10.3390/app15115901); PIPLUP; the nDCG definition; CNCF graduation criteria; the project repositories.

## Appendix A: upstream roles weighed and not used (a table, half a page)

From why.md and the role READMEs: role, what it offered, why it was not used.

## Appendix B: the soak in one line per round (half a page)

From soak-index.md section 1.

## Figures (SVG, Swiss editorial, figures/STYLE.md)

1. Logging today. From slide 3.
2. The target architecture: worker tier, storage tier, control, background and shifter hosts, the bus as a ghost, edges labelled with protocol and cadence. From slide 5 and the atlas.
3. One error line, from an EPN to a screen. Hosts as columns, the twelve steps of walkthrough 1. New.
4. Inside the collector: five steps, the stamper loop, the disk buffer, the memory envelope. From slide 9.
5. From a line to the template catalog. From slide 13 and walkthrough 7.
6. From numbers to one alert: heartbeats and the poller, three lanes, the projector, Alertmanager, the receiver, the break-glass path. From slide 14 and walkthroughs 3 and 8.
7. The three layouts: staging (five VMs), the farm pilot (three workers and three containers on one infra machine), production (many workers, a storage tier, the bus). New.
8. Optional: the flush curve. New, a plain line chart.

## Tables

1. Log sources on a worker. 2. The six objectives and what meets each. 3. Index families. 4. Collector candidates. 5. Store candidates. 6. Configuration tools. 7. Flush 5 s against 1 s. 8. Templating figures of record. 9. Semantic figures of record. Appendix A table.
