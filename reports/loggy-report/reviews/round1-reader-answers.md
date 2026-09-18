# Round 1 reader answers

Fresh reader. Sources: `report-text.txt` and `reader-questions.md` only. Section numbers refer to the report.

## Answers

### 1. An info-level line from an O2 process

An info line takes the same road as an error line as far as the routing rule, then stays on its worker (Section 3.3). The collector tails the file, adds a document identifier, routes the raw severity "info" to local, the stamper sets a template, and the local node's ingest pipeline stores it (Section 3.2, steps 1 to 4). It lands in `application-logs-local-<node>`, one index per worker, one shard, zero replicas, kept 8 days, pinned to that worker so no byte crosses the network (Table 3, Section 3.3). One copy exists. If the EPN dies, nobody can read it: "a lost worker loses only its own local index" (Table 2) and the disk buffer "does not survive losing the node" (Figure 4).
Where: Sections 3.2, 3.3, Table 2, Table 3, Figure 4. Confidence: sure.

### 2. A collector dies

Every collector pushes a health sample every 30 seconds; a rostered collector silent for 90 seconds is absent (Section 3.6). Within 30 seconds the poller counts each rostered collector's samples over 90 seconds and writes one fleet document with a missing flag; the collector-down monitor runs every minute over the last two minutes of fleet documents and fires at the highest severity (Section 3.7). The projector marks the signal a page, checks the roster, writes the signal row, opens an episode and posts it to Alertmanager; Alertmanager groups on alert name and collector, waits the page tier of 30 seconds, and posts the batch to the receiver, which writes one notification document (Section 3.7). The cadences bound death-to-document at 90 to 270 seconds, but no run has timed it (Sections 3.7, 6.1). The report also says "Nothing pages a human today" (Section 3.6), so "a person being told" ends at a stored notification document.
Where: Sections 3.6, 3.7, 6.1. Confidence: sure.

### 3. Why no queue between collector and store, and what reopens it

Three arguments decided it (Section 4 "Kafka between the collector and the store", Section 5.3). Decoupling: one worker's stack sustains about 42,000 records a second against a busiest worker-second of 78, a margin of 538 times (54 times if the archive under-counts ten times), and 4.3 times the farm's peak second of 9,781. Durability: only InfoLogger is lost in an outage, and its 256 MB disk buffer holds 865,674 records, 17.4 hours at a median worker's rate and 5.1 hours at the busiest worker-second. Cost: three brokers on memory the staging machines lack, the tier rule splitting into two places, and a produce plus consume step raising the 1-second latency floor. Four conditions reopen it: a sustained worker rate above about 1,000 records a second, a required second consumer, an outage longer than the buffer holds, or a measured worker rate within 50 times of 42,000 (Section 5.3).
Where: Sections 4, 5.3, 5.2. Confidence: sure.

### 4. The two Kafka decisions

The first decision is a queue between the collector and the store, for durability and decoupling of the storage path; the soak rejected it on numbers (Sections 4, 5.3). The second is a bus for the live lane only, the second output that copies severe records to the shifter view; it is for decoupling and not durability, because moving that view otherwise means editing more than 200 worker configurations (Sections 3.1, 4 "Kafka for the live lane"). The live-lane bus is the agreed one: "agreed, not built", one topic from every collector, the shifter view its one consumer, broker placement undecided (Sections 3.1, 6.3, Figure 7). It must be Apache Kafka and the primary path, because the pinned collector has no on-failure route (Section 4).
Where: Sections 3.1, 4, 5.3, 6.3, Figure 2, Figure 7. Confidence: sure.

### 5. How much of a worker the platform takes

Processor: a worker gives the platform four of its 128 logical processors, and the local OpenSearch node holds its index on those four cores with "little memory" (Sections 2.3, 3.1, Table 2). The collector runs between 133 and 228 MB under steady load, peaked at 373 MB in a sink outage, and is capped at 384 MB (Sections 3.4, 5.2); the store node ships with a 1 GB heap (Section 5.2). The collector costs about a quarter of one core at 20,000 records a second (Abstract) and the stack "needs four exclusive cores away from reconstruction" (Section 5.2). All measured numbers come from a laptop rig, with two rigs never averaged (Section 5.1); the four-core figure is a constraint the platform is given, not a measurement. The stamper's cost is not measured (Section 4), and sharing the four cores with reconstruction is not measured (Section 6.1), so a total memory figure for the whole worker stack is not stated.
Where: Sections 2.3, 3.1, 3.4, 4, 5.1, 5.2, 6.1, Abstract. Confidence: partly (no total worker memory figure is given).

### 6. Flush interval 5 s to 1 s

Table 6 gives the change at 5,000 records a second on the re-run rig, the same laptop slowed, from a three-alternating-run set (Sections 5.1, 5.2). Collector alone fell from 36.70 to 27.70 core-seconds per million records, minus 24.5 %. Collector peak memory fell from 87.1 to 62.7 MB, minus 28.0 %. Whole stack fell from 108.40 to 104.67 core-seconds per million records, minus 3.4 %. The live-lane latency floor fell from 5 seconds to 1 second. Across eight flush values from 0.125 to 10 seconds the whole-stack cost is a U with its floor at 1 second, best to worst 17.9 % apart (Section 5.2).
Where: Table 6, Sections 3.4, 5.1, 5.2. Confidence: sure.

### 7. What a template is and what the stamper does

A template is the skeleton of a log line: its fixed words, with every variable token a wildcard (Sections 3.5, 5.5). The stamper runs beside the collector on the worker; every log chunk goes out to it over a local Unix socket after the retag step and comes back stamped, and only stamped chunks reach the OpenSearch outputs (Sections 3.2 step 3, 3.5, Figure 4). Inside, a masker replaces varying tokens with placeholders, then one drain3 tree per family turns a literal that varies between lines into a wildcard (Section 3.5). It sets two fields, `template_version` and `template_status`, with status matched, new or no template; an empty message or one over 4,096 characters is indexed with no template (Section 3.5, Figure 5). It holds at most 20,000 clusters per worker, drops the least recently used, and every 300 seconds publishes one bucket document per family and window (Section 3.5).
Where: Sections 3.2, 3.5, 5.5, Figures 4 and 5. Confidence: sure.

### 8. Built and running versus agreed and not built

Built and running on five staging machines: the collector, the stamper, the local data node on each worker, the three-node storage tier, Dashboards, the poller, the projector, the receiver, Alertmanager, the shifter view with live and Templates pages, the trend rollup, the threshold, detector and trend lanes, and the template chain (Sections 3.1 to 3.9, Figure 7). Also built: semantic retrieval, shipped disabled by configuration (Section 5.6); inhibition, built and shipped off (Section 4); seven injection scenarios (Section 5.7); one green deploy of the farm pilot with load not measured (Figure 7, Section 1). Agreed and not built: the live-lane Kafka bus with broker placement undecided; the shared Python as one versioned package; three primaries on the farm, agreed and not applied (Sections 3.1, 4, 6.3). Open or not built: catalog maintenance as ISM policies plus monitors, the bucket indices as a rollover alias, the Telegraf decision, the operator view beside InfoLogger, authentication inside the cluster, and production as a whole (Sections 3.5, 3.9, 6.3).
Where: Sections 1, 3.1, 3.5, 3.9, 4, 5.6, 5.7, 6.3, Figure 2 legend, Figure 7. Confidence: sure for the named items; partly for completeness, because the built list is spread over many sections.

### 9. What 0.685 means

0.685 is an nDCG@10 score, "a rank score that rewards relevant results near the top", for the search box's standing recommendation: a dense scan by exact cosine over all templates, 30 candidates, then a late-interaction reranker (Section 5.6). It is compared against 0.634 for the dense model alone, a gain with an interval of +0.028 to +0.080 (Section 5.6, Table 8). A separate held-out figure, on questions sealed before any score existed, is 0.689 for the dense model against 0.371 for the lexical engine (Section 5.6). Table 8's caption says the judgements are machine-made and the gain holds "on questions in a person's own words and nowhere else". The report does not say what "relevant" means or how many questions were scored.
Where: Section 5.6, Table 8, Abstract. Confidence: sure.

### 10. Why the cost figures do not transfer to the farm

Every cost figure in Section 5 comes from a laptop rig, and "the conversion to a farm worker was never measured" (Sections 5, 6.1). The instrument sums container processor time into core-seconds, two rigs exist and are never averaged, and the rig pinned four exclusive cores that a real worker would share with reconstruction (Sections 5.1, 6.1). So only shapes, knees and rankings transfer; absolute rates and core-seconds do not (Sections 5, 7). The report does not name one single fixing measurement in so many words. The nearest statement is the unmeasured "conversion to a farm worker" (Section 6.1) and the farm pilot's "load not measured here" (Figure 7), which together imply running the cost instrument on a real farm worker.
Where: Sections 5, 5.1, 6.1, 7, Figure 7. Confidence: partly (the single measurement is implied, not stated).

### 11. The six objectives and their design elements

Table 2 lists them (Section 2.4). No bulk over the wire: a local index on every worker, written by a collector that talks only to the search node on its own machine. Split by severity: the collector routes each line by severity to the local index or the storage tier. Distributed: one cluster of two tiers, and every worker answers a search over its own index. Durable: a storage tier of three machines, with two copies of every record above info and of every InfoLogger record. Workers stay cheap: four logical processors of 128, no cluster management on a worker, and health pushed up rather than polled down. Fault tolerant: a storage quorum of two of three, and a lost worker loses only its own local index.
Where: Section 2.4, Table 2. Confidence: sure.

### 12. Detector, monitor, episode, notification

A monitor is a saved rule the cluster runs on a schedule, writing an alert when its condition holds; threshold and trend monitors are kinds of monitor (Section 3.6). A detector is a Random Cut Forest model the cluster runs on a schedule, grading every window with no threshold; 17 run, one per entity (Section 3.6). A signal is one stored row saying which rule or detector fired, on which entity, when; an episode is one trouble on one thing with a start and an end, opened by the projector and closed after consecutive healthy windows (Section 3.6). The report also defines an incident as "the episode document grouping the signals of one alert name on one entity" (Section 3.6). A notification is what the receiver stores: one document per batch that Alertmanager posts, "what a person was told" (Sections 3.6, 3.7, Figure 6).
Where: Sections 3.6, 3.7, Figure 6. Confidence: sure.

## Terms used without a definition at first use

- "soak" (Section 2.2, Appendix B title): never defined.
- "timeframe reconstruction" and "time slice" (Section 1): partly explained, "timeframe" not.
- "O2" and "O2 process" (Figure 1, Table 1): never expanded.
- "the journal" (Section 1): "the log of every system service"; "systemd" and "kernel ring" (Table 1) not explained.
- "Alertmanager" (Section 2.3): first named as a port owner; explained in Sections 3.6 and 4.
- "Dashboards" (Section 2.5): first used as a Grafana replacement; explained in Section 3.9.
- "OpenSearch" (Section 3.1): first named as a data node; described in Section 4.
- "cluster manager", "data node", "ingest", "shard", "primary", "replica", "index" (Figure 2, Section 3.1, Table 3): never defined.
- "ingest pipeline": defined inline in Section 3.2 step 4 only.
- "chunk" (Section 3.4, Figure 4): never defined.
- "live lane", "query lane" (Figure 2, Section 3.2): defined by use only; Section 5.2 finally says the live lane is "the second output that copies records to the shifter view".
- "shifter", "shifter view", "maintainer", "cockpit", "ops page" (Sections 3.1, 3.2, 3.9): "shifter" never defined.
- "the poller", "the roster", "the projector", "the receiver", "the catalog maintenance" (Section 3.1): named before Section 3.6 explains them.
- "control host", "background host", "shifter host" (Figure 2): never explained as roles.
- "break-glass" (Section 3.6): defined only by its use.
- "inhibition", "causal edges" (Sections 4, 6.2): never defined.
- "Random Cut Forest" (Abstract, Section 3.6): the cited paper is the only explanation.
- "forecaster" (Section 3.6): partly explained.
- "core-seconds" (Section 4 uses "core-seconds per million lines" before Section 5.1 defines it).
- "nDCG@10", "dense retrieval", "reranking" (Abstract): defined only in Section 5.6.
- "late-interaction reranker", "lexical engine", "static model", "small transformer", "int8", "fp32", "approximate vector index", "fusion" (Section 5.6): never defined.
- "PIPLUP" (Sections 4, 5.5): never described.
- "drain3", "Drain3", "dpl family tree" (Figure 3, Section 3.5): "dpl" never expanded.
- "Fluent Bit" and "release 4.0.1" (Section 2.3 mentions collector releases before Section 4 names the collector).
- "healthy rig", "re-run rig" (Section 5.1): defined there, but "rig" itself is not.
- "knees" (Abstract, Section 5): never defined.
- "fixture run", "replay", "replay engine", "the corpus", "the archive" (Sections 2.1, 5.4, Figure 7): the replay is never explained.
- "watermark", "rollover alias", "upserted", "ISM" (Sections 3.5, 6.3, Table 9): ISM defined in Section 6.3 only, the others not.
- "Telegraf and Mimir estate" (Section 4): never explained.
- "OpenStack", "Kubernetes", "Grafana", "Puppet", "Salt", "Chef", "Terraform" (Sections 2.5, 4): assumed known.
- "SSE stream" (Figure 3): never expanded.
- "protobuf", "gzip" (Section 5.5): assumed known.
- "F1", "PCA", "deep sequence models", "drift" (Section 4): never defined.
- "boot id", "bulk action create" (Section 3.4): assumed known.
- "warn tier", "page tier", "pages" as a verb (Sections 3.6, 3.7, 3.8): "page" never defined.
- "cross-worker shipping" (Section 2.3): named as a term without explanation.
- "the rework", "the plan's burst figure", "two external reviews" (Sections 4, 6.1, 5.4): refer to history the report does not tell.
- "fault agent", "poison replay" (Section 5.7): partly explained.
- "Jinja", "security plugin", "Metricbeat", "performance analyzer" (Table 9): assumed known.
- "cluster and fleet samples", "fleet document" (Section 3.7, Figure 6): "fleet" never defined.
- "macro source purity": defined in Section 5.6, but "program" as the purity unit is not tied back to the 186 programs of Section 2.1.

## Ambiguous or contradictory passages

1. Copies and hops of an error line. Section 3.2 opens "An error line crosses the wire once and is copied three times before a person reads it", but step 6 adds "The collector also posts the record to the shifter view's live lane". That is a second crossing of the wire and a fourth copy. Figure 3 labels the live lane "second copy · http · per record".

2. Collector memory. Section 3.4 and 5.2 say the collector "ran between 133 and 228 MB under steady load", but Table 6 gives "Collector peak memory, MB 87.1 / 62.7" for the same product. The report does not say why the two ranges differ (rig, rate, or definition of peak).

3. Documented worst case. Section 3.4: "At most 64 chunks sit in memory, about 128 MB. The documented worst case is twice that with a 20 % margin, roughly 307 MB". Figure 4: "Documented worst case 128 MB". The two figures disagree by a factor of 2.4.

4. Flush interval wording. Section 3.4: "Moving the write to the local node from the shipped 5 seconds to 1 second". Section 5.2: "The flush interval is how often the collector pushes buffered records out". A reader cannot be sure these are the same setting until Table 6 is reached.

5. Monitor counts. Section 3.6: "30 monitors run: 28 write to the alert index, 2 take the break-glass path". Figure 6 shows "nine rules, every 10 min", "14 monitors, every minute", "two monitors" break-glass, and a threshold box saying "30 monitors, every minute or ten"; 9 + 14 + 2 = 25, not 30. Section 3.8 also says "The nine trend monitors" plus "A separate monitor pages when a whole family ... writes nothing", which is ten trend-lane monitors. Section 3.5 adds "Two hourly rules". The totals do not reconcile.

6. Dead-collector arithmetic. Section 3.7: "The cadences bound death-to-document at 90 to 270 seconds. The last sample lands up to 30 seconds before death. The 90-second grace and 30-second poll add 60 to 150 seconds, the monitor up to 60, the projector up to 30, the page wait 30." The upper parts sum to 30 + 150 + 60 + 30 + 30 = 300 seconds, not 270.

7. Number of log sources. Section 2.1 and Table 1: "five log families" (the table has six rows, one "Not collected"). Section 3.1: "reads the five log sources". Section 5.4: "The collector had never read three of the six log sources". Section 2.5: "widened from two sources to five". The count moves between five and six.

8. The journal. Section 2.1: "The replayed archive holds InfoLogger, DDS and the process tree, and no journal." Section 5.4: "Later rounds added ... the system journal", and "the collector reads all of it". Section 6.3, sixth item: "the system journal and per-device process logs, absent from the archive" remain to do. It is unclear whether the journal is collected today.

9. What goes local. Abstract: "Info lines stay in one local copy". Table 3: "info and debug lines". Section 3.2 step 2: "info, debug and trace go local". Three different sets.

10. The 11.19 figure. Section 5.1: "Cost per record falls as rate rises: 75.32 core-seconds per million at 1,000 a second and 11.19 at 20,000." It does not say whether that is the collector alone or the whole stack. The Abstract's "about a quarter of one core at 20,000 records a second" for the collector matches 11.19, but Table 6 gives the collector alone 27.70 at 5,000 on the other rig.

11. Template counts. Section 5.5: "mined the archive into 3,822 templates", "One run per family gave 3,011 templates. One tree per family across three corpora gave 4,221." Table 10: "3,011, 4,092, 4,221". Section 5.6: "It searches 5,301 template groups." Five different counts with no bridge between them.

12. Ceiling ownership. Section 5.2: "The stack sustains about 42,000 records a second ... so the ceiling is the storage tier's." Section 6.1: "the collector's ceiling above 50,000 is unknown". Table 10 round 1: "Collector alone: 53,000 a second, two cores." Whether 50,000 or 53,000 is the collector's known ceiling is unclear.

13. Deployment tool. Section 4: Ansible is chosen because it has no agent, then "Puppet wants an agent and a certificate on every machine, runs on a timer, and is right for the farm." It is unclear whether the report recommends Puppet for the farm.

14. "Store plugins lost, because a plugin pins to a store version." (Section 4, under "Shared code as one package"). It is not clear what plugins were candidates or what they lost to.

15. Masker figures. Section 4: "The masker ... was 74 to 87 % of the mining cost, so we rewrote it." Table 7: "Masking step removed by the rewrite 85 to 87 %". Section 5.5: "Seven rewritten expressions took 85 to 87 % off it." Whether "removed" means removed from the masker step or from mining is ambiguous.

16. Word "staging". Section 2.3: "Physicists' staging runs occupy two of the three farm workers", where "staging" means physics runs on the farm. Everywhere else "staging" is the five virtual machines.

17. Live lane and info lines. Section 6.2: "The live lane matches InfoLogger and the central family, so after the severity fix of Section 5.4 it will not see stdout info lines." Section 3.9: "Its live page keeps the severe lines collectors post to it." If the live page only keeps severe lines, not seeing info lines is by design, not a limit.

18. Memory ceilings. Section 3.4 caps the collector service at 384 MB; Section 5.6 says the semantic model's "configured ceiling is 384 MB". It is unclear whether these are the same budget on the same machine.

19. Detector grades. Section 3.6: "A detector grade above 0.5 opens an episode, the paging monitor on grades needs 0.7." No monitor on grades is described elsewhere.

20. Appendix B. The heading "B The soak in one line per round" has no text under it; Table 10 appears inside the References on page 17.

21. Uncited references. [3] (the O2 TDR), [6] (the Drain paper) and [14] (the ALICE log anomaly paper) are listed but never cited in the text.

22. Production storage size. Section 6.3: "Production, the whole farm with three storage nodes". Figure 7: "storage tier · three or more machines".

23. Section 4 "One cluster": "Three hundred data nodes in one cluster is an untested cluster-manager risk", while Section 2.3 speaks of a farm with 128-processor workers and Section 4 elsewhere says "more than 200 workers". The intended farm size (100, 200, 300) is never stated once.

## Knowledge the report assumes

- The ALICE experiment, its online farm, the O2 software, DPL, the run orchestrator, and what timeframe reconstruction is.
- InfoLogger's client library, local daemon, central server and infoBrowser (Figure 1 names them without explanation).
- DDS as a deployment system, and what a "run directory" on a scratch disk is.
- OpenSearch and Elasticsearch concepts: index, shard, primary, replica, cluster manager, data and ingest roles, ingest pipelines, heap sizing, the alerting and anomaly-detection plugins, `.opendistro-*` indices, Index State Management, rollover aliases, cross-cluster search, bulk create.
- Fluent Bit concepts: input, tag, filter, retag, output, chunks, flush interval, forward protocol over a Unix socket, disk buffering, threaded inputs and processors.
- Prometheus Alertmanager concepts: grouping, group wait, silences, inhibition, resolve timeout, webhook receivers.
- Kafka concepts: brokers, topics, consumers, KRaft mode.
- Linux and systemd: the journal, unit memory limits, OpenSSL versions, sockets, boot ids.
- Configuration management: Ansible, Puppet, Salt, Chef, Terraform, agents, SSH push, OpenStack virtual machines, containers.
- The Drain log-parsing algorithm and drain3 (depth, similarity, separators, clusters), masking, and what PIPLUP is.
- Random Cut Forest anomaly detection, anomaly grades, forecasting.
- Information retrieval: nDCG, dense versus lexical retrieval, cosine similarity, late-interaction rerankers, approximate vector indices, fusion, int8 versus fp32 quantization, transformers versus static embedding models.
- Statistics and benchmarking vocabulary: noise floor, control runs, core-seconds, knees, U-shaped cost curves, held-out sets.
- The 2025 reference design [1] and what its two sources, three index families and aggregator were.
- The project's own history: the "soak", its numbered rounds and stages, the "rework", the "two external reviews", and the "plan" with a burst figure of 10,000 to 20,000.
- CERN infrastructure: the Telegraf and Mimir metrics estate, the rule that hosts take no long-running agent, the fully-open-source requirement.
- The size of the farm (the report uses 100, more than 200 and 300 in different places).
