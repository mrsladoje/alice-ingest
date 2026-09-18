# Round 2 reader answers

Source: only `report-text.txt` and `reader-questions.md`. No other file, no prior knowledge.
Confidence scale: sure, partly, not answerable from the text.

## Answers

### 1. An O2 process writes an info line. Where does it end up, how many copies, who reads it after the EPN dies?

The line stays on the worker that wrote it, in the index `application-logs-local-<node>`, one index per worker with 1 shard and 0 replicas, kept 8 days. That is one copy; the report says "no byte crosses the network" and "Routine lines never leave the worker that wrote them". A cluster search still finds it while the worker is alive, because "the cluster asks every node holding a shard and merges the answers". If the worker dies, "its raw lines die with it", so nobody can read the raw line any more. What survives are the derived rows: "the rollup rows, template counts and detector results survive".
Where: Section 3.3, Table 3, Section 3.1, Table 2 ("a lost worker loses only its own local index").
Confidence: sure.

### 2. The collector on one worker dies. What happens until a person is told, and how long?

The collector is silent, so the poller, which "samples the cluster" every 30 seconds and reads the roster, counts each rostered collector's samples over 90 seconds and writes one row each with a missing flag. The collector-down monitor runs every minute over the last two minutes of those rows and fires at page severity. The projector writes a signal row, opens an episode and posts it to Alertmanager; Alertmanager groups on alert name and collector, waits the page tier of 30 seconds, and posts the batch to the receiver, which stores one notification document. The report bounds "death-to-document" at 90 to 270 seconds from cadences: first missing flag after 60 to 150 seconds, monitor up to 60, projector up to 30, page wait 30. It also says "No run has timed it" and, in Section 3.6, "Nothing pages a human today", so the chain ends at a stored document, not at a person.
Where: Section 3.7, Section 3.6, Section 6.1, Section 5.7.
Confidence: sure for the chain and the bound; the bound is unmeasured.

### 3. Why reject a queue between collector and store, and what reopens it?

The soak rejected the queue on three counts. Decoupling: the stack sustains about 42,000 records a second, 538 times the busiest worker-second of 78 and 4.3 times the farm peak of 9,781, still 54 times if the archive under-counts ten times. Durability: the collector's 256 MB disk buffer "holds hours at a real worker's rate", 17.4 hours of InfoLogger at a median worker. Cost: three brokers and a second system "on memory the machines do not have", the tier rule would split into two places, and a produce plus consume step would raise the 1-second live-lane latency floor. Four conditions reopen it: a sustained worker rate above about 1,000 records a second; a worker rate within 50 times of 42,000; a second consumer; or an outage longer than the buffer holds.
Where: Section 5.3, Section 4 ("Kafka between the collector and the store"), Section 2.5.
Confidence: sure.

### 4. Kafka appears twice. What differs, and which is agreed?

Decision one is a queue between the collector and the store on the bulk path; it was rejected on the soak numbers (Section 5.3) and the 2025 reference design's queue and aggregator were dropped. Decision two is a Kafka bus for the live lane only, the second copy of every severe record that goes to the shifter view; a design review in September 2026 agreed it "for decoupling and not durability". The reason is operational: today a moved shifter view "means editing more than 200 collectors, with a bus one consumer". The live-lane bus is the agreed one, but "it is not built", its brokers are not placed, and it must be Apache Kafka as the primary path. Section 5.3 states that "The live-lane bus is separate" from the four reopen conditions.
Where: Section 3.1, Section 4 (two Kafka paragraphs), Section 5.3, Section 6.3 item 1, Figure 2 and Figure 7.
Confidence: sure.

### 5. How much of a worker does the platform take, and where do the numbers come from?

Processor: four of the worker's 64 physical cores, eight of its 128 logical processors, shared by the four worker-tier parts (collector, stamper, health sample, local OpenSearch node). Memory: a 384 MB cap on the collector (throttled at 384, killed at 768), a 1 GB heap on the local node, and a 512 MB memory limit inside the stamper; the report calls this "little memory". The core and cap figures are design allocations stated as constraints, not measurements. The measured figures are from a laptop rig: the collector sat between 133 and 228 MB under steady load and peaked at 373 MB in a sink outage, and it costs "about a quarter of one core at 20,000 records a second". Section 6.1 adds that sharing the four cores with reconstruction "is not measured".
Where: Section 2.3, Section 3.1, Section 3.4, Section 5.2, Section 5.5, Abstract, Section 6.1.
Confidence: partly. The split between allocated and measured is clear, but the report never says where the "four cores" allocation itself came from.

### 6. Flush 5 s to 1 s changed what, by how much, in what unit, on which machine?

Table 6 gives four measures at 5,000 records a second on the re-run rig, "the same laptop slowed", from three alternating runs. Whole-stack cost fell from 108.40 to 104.67 core-seconds per million records, a 3.4 % drop. Collector-alone cost fell from 36.70 to 27.70 core-seconds per million records, a 24.5 % drop. Collector peak memory fell from 87.1 to 62.7 MB, a 28.0 % drop. The live-lane latency floor fell from 5 seconds to 1 second. Across eight flush values from 0.125 to 10 seconds the whole-stack cost is a U with its floor at 1 second, best to worst 17.9 % apart.
Where: Section 5.2 and Table 6, Section 3.4, Section 5.1, Abstract.
Confidence: sure.

### 7. What is a template, and what does the stamper do before indexing?

A template is "the skeleton of a log line: its fixed words, every variable token a wildcard". It widens as the miner sees more lines; each state is one version, and the version is the field a record carries. The stamper, one process per worker beside the collector, receives each chunk over a Unix socket, runs the drain3 miner with a masker that replaces varying tokens with placeholders and one tree per log family, and returns the chunk stamped with `template_version` and `template_status` (matched, new or no template). Empty messages or messages over 4,096 characters get no template. It holds at most 20,000 clusters per worker and drops the least recently used; every 300 seconds it also publishes bucket documents with counts per version.
Where: Section 3.5, Figure 5, Section 3.4, Section 3.2 step 3, Section 5.5.
Confidence: sure.

### 8. What is built and running, what is agreed and not built?

Built and running on the five staging machines: the collector, the stamper, the local data node, the three-node storage tier, Dashboards, Alertmanager, the poller, the roster, the projector, the trend rollup, the receiver, the shifter view with its live lane over HTTP, templating with the catalogue and its two hourly audits, 30 monitors, 17 detectors and one forecaster. Built but shipped off: semantic retrieval ("shipped disabled by configuration") and inhibition ("built and ships off"). The farm pilot had one green deploy on three workers and one infra machine, but "Only the cluster, the collector and Dashboards are stated to run there" and load was not measured. Agreed and not built: the live-lane Kafka bus, the shared Python package, and the three storage primaries on the farm ("agreed and not applied"). Not built: the operator view beside InfoLogger and production. Open: Telegraf taking the samples, catalogue maintenance as ISM policies, the bucket indices as a rollover alias, in-cluster authentication, source-owner approval.
Where: Section 3.1, Section 3.5, Section 3.6, Section 3.9, Section 4, Section 5.6, Section 6.3 and Figure 7, Introduction.
Confidence: partly. The list is spread over many sections and the farm-pilot scope in Section 6.3 conflicts with Figure 7 (see ambiguities).

### 9. What does 0.685 mean, and what is it compared against?

0.685 is the nDCG@10 score, normalised discounted cumulative gain at rank ten, of the recommended two-step search: a dense scan keeps the 30 templates nearest the question's vector, then a reranker rescores those 30 word by word. It was measured on the development set of 130 questions over 97 intent groups. The comparison is 0.634 for the dense scan alone, a gain with a bootstrap interval of +0.028 to +0.080. On the separate held-out set the dense scan alone scores 0.689 against 0.371 for the store's lexical engine, and reranking was never scored there. An approximate vector index would cost 0.020 (0.685 to 0.665) and fusing with the lexical engine scores 0.048 worse.
Where: Section 5.6 and Table 8, Abstract, Conclusion.
Confidence: sure.

### 10. Why do cost figures not transfer to the farm, and what single measurement would fix that?

Every cost figure in Section 5 comes from a laptop rig, with two rigs (healthy and slowed re-run) that are never averaged, so "Absolute rates and core-seconds do not" transfer; only "shapes, knees and rankings" do. The rig also pinned four exclusive cores and used a warm index, and it saturated unnoticed once, forcing retractions. The fix named is "One micro-benchmark on one farm machine would give the conversion factor. None has run."
Where: Section 5 opening, Section 5.1, Section 6.1, Conclusion, Abstract.
Confidence: sure.

### 11. The six objectives and the element meeting each?

Table 2 lists them. No bulk over the wire: a local index on every worker, written by a collector that talks only to the search node on its own machine. Split by severity: the collector routes each line by severity to the local index or the storage tier. Distributed: one cluster of two tiers, every worker answering a search over its own index. Durable: a storage tier of three machines with two copies of every record above info and of every InfoLogger record. Workers stay cheap: four physical cores of 64, no cluster management on a worker, health pushed up not polled. Fault tolerant: a storage quorum of two of three, and a lost worker loses only its own local index.
Where: Section 2.4, Table 2.
Confidence: sure.

### 12. Detector, monitor, episode, notification?

A monitor is "a saved rule the cluster runs on a schedule, writing an alert when its condition holds"; threshold and trend monitors are examples. A detector is "a scheduled Random Cut Forest model" that learns a number's usual shape and grades each window from 0 to 1 with no threshold; a grade above 0.5 opens an episode. An episode is "one trouble on one thing, with a start and an end", made by the projector from signals (one stored row per rule or detector hit) and closed after consecutive healthy windows; the report also calls the episode document an incident. A notification is what the receiver stores, one document per batch that Alertmanager posted, "what a person was told". So: monitors and detectors produce hits, the projector folds hits into one episode per entity and trouble, Alertmanager batches open episodes, and the receiver records each batch as a notification.
Where: Section 3.6, Figure 6, Section 3.7, Section 4 ("Episodes, and Alertmanager as messenger").
Confidence: sure for monitor, detector and notification; partly for episode versus incident, which the report does not separate cleanly.

## Terms used without a definition at first use

- "tier" and "storage tier" (Table 1, Abstract) before Section 3.1 explains the two tiers.
- "shard", "primary", "replica", "cluster manager", "quorum", "heap" (Table 2, Section 3.1, Table 3) never defined.
- "Dashboards" (Section 2.5) defined only in Section 3.9 as the store's web interface; "Discover" likewise.
- "Alertmanager" (Section 2.5, Table 2) described only in Section 3.6.
- "index families", "lifecycle policies", "aggregator", "the queue" (Section 2.5).
- "live lane", "query lane", "lane" in general (Figure 2, Section 3.1 defines live lane after Figure 2 uses it; "three lanes" in 3.6 is a different sense).
- "page" and "warn" as severity tiers ("a storage disk above 92 % pages", "the page tier", "the warn tier").
- "break-glass" monitors (Section 3.6).
- "sink", "sink outage" (Section 3.4, 5.2).
- "core-second" is used in Section 4 with a definition, but "core-seconds" appears earlier in the noise-floor context of Section 5.1 only after; fine. However "cost" as core-seconds per million is used in the Abstract ("a quarter of one core") without the unit.
- "PIPLUP" (Section 4, 5.5), never expanded.
- "drain tree", "clusters" (stamper "holds at most 20,000 clusters") versus "templates" versus "template groups"; the three words are used for related things without a mapping.
- "the cockpit", "maintainer cockpit", "cockpit-metrics", "the ops page" (Figures 2 and 6, Section 3.9).
- "the roster" is defined in 3.1 and 3.6 but "the fleet" appears in Figure 6 before 3.6 defines it.
- "watermark", "upsert", "rollover alias", "ISM" (Figure 5, Section 3.5, Table 9; ISM is expanded only in Section 6.3).
- "release 4.0.1", "release 5.0.8", "two majors behind" (Sections 2.3, 5.4): release of which product is implied, not stated.
- "data-distribution format" (Section 5.4), presumably DDS, not linked.
- "fixture run", "poison replay", "fault agent", "injection harness" (Sections 5.4, 5.7, 6.1).
- "replay", "the replayed archive", "replay engine" (Section 2.1, Figure 7) never explained.
- "Telegraf", "Mimir", "Grafana", "Metricbeat", "OpenStack", "nginx", "Jinja", "SSE stream" (Sections 2.5, 4, 3.9, Figure 3, Table 9).
- "Kafka", "topic", "consumer", "broker", "KRaft" (Section 3.1, Figure 2, reference 11).
- "int8", "fp32", "static model", "small transformer", "backend" (Section 5.6).
- "Random Cut Forest" is defined in 3.6 only as "a scheduled ... model"; what the model is remains assumed.
- "the archive", "the corpus", "three corpora" (Sections 2.1, 2.2, 5.5): which data set each word names shifts.
- "anchored pattern", "parser anchor" (Sections 2.1, 5.4).
- "forward socket", "bulk action create", "placement setting" (Sections 3.2, 3.3, 3.4).

## Passages that are ambiguous or seem to contradict each other

1. Trace lines. Abstract: "Info, debug and trace lines stay in one local copy." Table 3: application-logs-local holds "info and debug lines". Trace is missing from the table.
2. Farm primaries. Section 3.1: "Three primaries are set for the farm and have never carried farm volume." Section 4: "The farm runs three primaries, agreed and not applied". "Set" and "not applied" disagree.
3. Farm pilot scope. Section 6.3: "Only the cluster, the collector and Dashboards are stated to run there." Figure 7 farm pilot box lists stamper on every worker and three containers holding Dashboards, the projector and the shifter view. Introduction: "One deployment passed every check". The three passages give three sizes for the pilot.
4. Monitor counts. Section 3.6: "Of 30 monitors, 17 are threshold and detector monitors, 13 every minute and four every ten." Figure 6 left panel: "15 monitors, every minute"; right panel: "17 monitors: 13 every minute, 4 every ten". 15 and 13 do not match.
5. Detectors versus detector monitors. "Seventeen detectors run, one model per entity" and "17 are threshold and detector monitors" use the same number for two different kinds of object; whether each detector has its own monitor, or "One monitor watches every grade", is unclear. Section 3.3 also says "six local anomaly detectors ... read the local index" without saying how they fit the 17.
6. Collector memory. Section 3.4: "Under steady load the collector ran between 133 and 228 MB." Table 6: "Collector peak memory, MB 87.1 / 62.7". Both are steady-load figures; the report does not say that the difference is the rig or the rate.
7. Which rate for the cost curve. Section 5.1: "the collector alone costs 75.32 core-seconds per million at 1,000 a second and 11.19 at 20,000." Section 6.1: "The cost curve was measured at 5,000 records a second only." Table 6 gives collector alone 27.70 to 36.70 at 5,000. Three rates, three numbers, and it is not stated which curve Section 6.1 means.
8. Nobody is paged. Section 3.1: "Alertmanager, which decides when a person is told." Section 3.6: "Nothing pages a human today." Section 3.7 title: "a dead collector becomes a notification". The receiver "stores what a person was told" although no person is told.
9. Live-lane latency and the bus. Section 5.3 counts against a queue that "A produce and a consume step raise the live lane's 1-second latency floor", yet Section 4 accepts a Kafka bus on exactly the live lane. The report does not reconcile the two.
10. Death-to-document arithmetic. Section 3.7: "A rostered collector silent for 90 seconds is absent" but "the grace and the poll give the first missing flag after 60 to 150 seconds". A lower bound of 60 is below the 90-second silence rule and is not explained.
11. Template counts. Section 5.5: "drain3, mined the archive into 3,822 templates at 19.91 core-seconds per million" and, two paragraphs later, "One run of each family, 45,596,613 lines, gave 3,011 templates, a floor". Both are called the archive.
12. Corpus sizes. Section 2.1: 45,596,613 lines. Section 5.5: "One tree across all three corpora, 55,963,050 lines". Section 5.6: "a later corpus of 56,628,579 lines". Three corpora, no statement of how they relate.
13. Families versus sources. Section 2.1: "A worker holds five log families" while Table 1 has six rows. Section 5.4: "the six sources. Six, because the process tree has two line formats." The reader must infer that the sixth row (run orchestrator) is not a family and that a source is not a family.
14. Push versus poll. Abstract: "Every worker pushes its health up. Nothing polls it." Section 3.1: "The poller samples the cluster." Figure 6: "Health is pushed. Nothing reaches into a worker to scrape it." The poller does poll something; the reader must work out that it polls the storage cluster, not the workers.
15. Soak rates. Section 2.2: the soak "tested one collector at 1,000, 20,000 and 50,000 records a second per worker". Section 6.1: "the soak plan's per-worker burst of 10,000 to 20,000" and "No run measured a burst." Table 10 Round 1: "about 53,000 a second". The tested set and the plan do not match.
16. Storage-tier cores on the rig. Section 5.2: "The storage tier runs at 86 % of its four cores." Section 6.1: "The rig pinned four exclusive cores to the collector and the store." Whether the storage tier had its own four cores or shared the four is unclear.
17. Collector releases on the farm. Section 2.3: two workers are capped at release 4.0.1 and "The third worker runs a newer release". Section 5.4: "Four installations run there, on three releases where we assumed two" and "Release 5.0.8 ... is blocked". The two accounts are not joined.
18. Live-lane content. Section 3.1: "The live lane is the second copy of every severe record." Section 6.2: "The live lane carries InfoLogger and the central family only." InfoLogger records of info severity are not severe, so the two sentences describe different sets.
19. Abstract: "Every other severity goes to three storage machines." Table 3 adds "unparsed lines" to central, which have no severity.
20. Table 5 ClickHouse verdict "17 detectors run by us" is a fragment; it seems to mean "we would have to run the 17 detectors ourselves" but does not say so.

## Knowledge the report assumes

- ALICE, the online farm, data-taking, timeframes and what "reconstruction" is; O2, DDS and InfoLogger as products; what a "run" is.
- OpenSearch and Elasticsearch concepts: index, shard, primary, replica, cluster manager, quorum, heap, ingest pipeline and processors, bulk API and the `create` action, placement settings, cross-cluster search, ISM policies, rollover aliases, the alerting plugin's monitors, the anomaly-detection plugin's detectors and forecasters, the `.opendistro-*` system indices, and Dashboards with Discover.
- Fluent Bit internals: tag, filter, retag, output, chunks, the forward protocol over a Unix socket, disk buffering, flush interval, single-threaded event loop, threaded inputs, regex parsers and multiline parsers, and its release numbering.
- Prometheus Alertmanager semantics: grouping, group wait, resolve timeout, silences, inhibition, webhooks, and "page" versus "warn" severity conventions.
- Apache Kafka: brokers, topics, producers, consumers, KRaft.
- Deployment tooling: Ansible, Puppet, Salt, Chef, Terraform, agents and SSH push, roles and playbooks, Jinja templates, containers, OpenStack, systemd services and the journal, nginx as a reverse proxy, SSE streams.
- CERN environment: Telegraf and Mimir as the metrics estate, Grafana, the "fully open source" requirement, that hosts take no long-running agent, and that the 2025 reference design exists.
- Log mining: Drain and drain3, masking, similarity and depth settings, PIPLUP as another parser, "ground truth" for parser accuracy.
- Anomaly detection and statistics: Random Cut Forest, principal component analysis, F1, 95th percentile, noise floor, bootstrap intervals, held-out versus development sets, deep sequence models.
- Information retrieval: embeddings, dense versus lexical search, rerankers, approximate vector indexes, nDCG@10, int8 versus fp32 quantization, transformers versus static models.
- Systems measurement: core-seconds, core pinning, memory throttling and kill limits (cgroup-style), OpenSSL versions, CNCF graduation, software licences (Apache 2.0, AGPLv3).
