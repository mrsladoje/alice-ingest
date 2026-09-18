# Why brief: every technology and design choice, the alternatives, and the evidence

Author of the platform: Marko Sladojevic. Supervisors: Lubos Krcal, Federico Ronchetti. Product name: loggy.

How to read an entry. "Level: architecture" means the fact belongs in the report body. "Level: code" means the fact is evidence only and names a file or a setting. Every claim carries a `path:line`. A number is copied as the source writes it.

Two Kafka decisions exist and stay apart. Entry 10 is the soak's rejection of a bus between the collector and OpenSearch. Entry 11 is the rework's agreement to a bus for the live lane only. Neither cancels the other.

---

## 1. Fluent Bit as the collector

Choice: Fluent Bit runs on every worker as the log forwarder. `reports/inputs/deck-text.md:205`, `reports/inputs/deck-text.md:216`.
Goal: read each line beside the program that writes it, decide where the line belongs, and send it on, storing nothing. `reports/inputs/deck-text.md:205`.
Alternatives:
- Fluentd. The same project one size up, written in C and Ruby, over 60 MB by the vendor's figure. A Ruby package tree on every worker was not wanted. `reports/inputs/deck-text.md:219-222`. An earlier design note gives the same verdict: Fluent Bit, not Fluentd, for fidelity and one configuration language. `docs/PAPER-AIRPLANE.md:68`.
- Vector. Rust, small and fast, no vendor memory figure, benchmarks disagree on which is smaller. One company, Datadog, decides its future. `reports/inputs/deck-text.md:223-226`.
- Telegraf. Go, governed by InfluxData, a metrics agent. ALICE already runs it for machine and service metrics. It emits line protocol, which is a metric and not a log line. `reports/inputs/deck-text.md:227-234`.
Evidence: maturity and size decided it. `reports/inputs/deck-text.md:206`. Fluent Bit is C, about 450 KB idle by the vendor's figure, CNCF graduated, fifteen billion deployments on its own project page, a decade in production. `reports/inputs/deck-text.md:213-218`. Under load ours peaked between 133 and 228 MB. `reports/inputs/deck-text.md:241`. Measured envelope: 128 MB of chunks in memory, 307 MB documented worst case, 404 MB during a sink outage. `reports/inputs/deck-text.md:293-301`.
Status: built and measured.
Level: architecture.

## 2. Not a message queue as the collector

Choice: no broker replaces the forwarder at the edge. `reports/inputs/deck-text.md:236-240`.
Goal: state why a queue cannot do the collector's job.
Alternatives: a broker at the edge. It moves records and does not make them. It cannot tail a file, parse a line or stamp a clock, so a forwarder still runs in front of it. `reports/inputs/deck-text.md:237-239`.
Evidence: a queue costs three more machines and a quorum. `reports/inputs/deck-text.md:240`.
Status: agreed, not built (no queue at the edge).
Level: architecture.

## 3. OpenSearch as the store

Choice: OpenSearch holds every log family and runs detection inside the cluster. `reports/inputs/deck-text.md:247-258`.
Goal: index every field and the message body, count over time, and bring anomaly detection with it. `reports/inputs/deck-text.md:247`.
Alternatives:
- Elasticsearch. Three licences, one company. Anomaly detection and alerting sit in paid tiers, so the detection half becomes a purchase. `reports/inputs/deck-text.md:260-263`.
- ClickHouse. Apache 2.0, one company. It wins on disk and loses on machinery: we would run 17 detectors and 28 alerting rules ourselves. `reports/inputs/deck-text.md:264-267`.
- Loki. AGPLv3, one company. It indexes labels and not the text, so counting errors per host is a scan. Its guidance forbids 211 hostnames as labels. `reports/inputs/deck-text.md:268-272`.
Evidence: an open licence was the floor and detection decided it. `reports/inputs/deck-text.md:248`. OpenSearch is the only candidate that is fully open, indexes the body, and ships detection and alerting. `reports/inputs/deck-text.md:258`. CERN IT's own monitoring service runs it. `reports/inputs/deck-text.md:259`. Plugins in the RPM bundle: anomaly-detection, alerting, notifications. `docs/PLAN.md:14`.
Status: built and measured.
Level: architecture.

## 4. One cluster, not cross-cluster search

Choice: all five machines form one cluster and Dashboards asks it once. `reports/inputs/deck-text.md:150-151`.
Goal: let one person search every machine's local logs at once. `reports/inputs/deck-text.md:150`.
Alternatives: many clusters joined by cross-cluster search. Pros of many: one bad query cannot slow the others, each cluster upgrades on its own schedule. `reports/inputs/deck-text.md:175-179`.
Evidence: built-in cross-cluster search does not scale to 100 machines. `reports/inputs/deck-text.md:181`. One cluster places shards itself, keeps one set of users and saved searches, and needs no federation layer of our own. `reports/inputs/deck-text.md:182-185`. The cost is fanout: every machine pays, and a search is as slow as the slowest machine. `reports/inputs/deck-text.md:188-191`. Reconstruction comes first, so we ask only when we must. `reports/inputs/deck-text.md:192-196`.
Open counter-fact: three hundred and more OpenSearch nodes in one cluster is a cluster-manager scaling risk, which a bus does not fix. `docs/SOAK_RESULTS.md:1964-1966`.
Status: built and measured on five machines. Not tested at farm node count.
Level: architecture.

## 5. Two tiers split by severity, not by source

Choice: info-severity records stay in a local index on the worker. Everything above info, plus all of InfoLogger, ships to the replicated storage tier. `reports/inputs/deck-text.md:356-360`, `deploy/README.md:97-101`.
Goal: keep the volume where it is made and the value where it is safe. `reports/inputs/deck-text.md:82`.
Alternatives: placement by source, such as "dds local, stdout to storage". It would ship the whole stdout-info bulk across the network while discarding dds errors. `deploy/README.md:102-104`. The reference design routes by source: one tag, one topic and one index per program, which costs four edits in lockstep per new log source. `deploy/README.md:1756-1758`.
Evidence: info is at once the bulk and the trash, so it stays local and disposable. Other severities are rare and valuable, so they are replicated. `deploy/README.md:99-101`. The collector writes only to the OpenSearch node on its own machine. `deploy/README.md:127-133`. The local tier holds 96.94 % of lines and never leaves the node. `docs/TEMPLATES_FIX_PLAN.md:46`. Two rules learned the hard way: a severity nothing recovered routes to durable storage, and every router carries a rule keyed on `log`. `deploy/README.md:109-114`.
Status: built and measured.
Level: architecture.

## 6. Shard and replica counts

Choice: each worker's local index has 1 shard and 0 replicas. The storage-tier families have 1 primary shard and 2 replicas, one copy on every storage node. `reports/inputs/deck-text.md:111`, `reports/inputs/deck-text.md:144`, `deploy/README.md:2289-2291`.
Goal: survive the loss of a storage machine at no cost to the cheap half. `reports/inputs/deck-text.md:370-372`.
Alternatives:
- Three primaries per storage family, as the plan asks. At 3 primaries they reach about 135 shards at full retention against a budget of 60. `deploy/README.md:2293-2299`.
- Three primaries with one replica. It cuts per-node indexing and disk by a third but survives only one node loss. `deploy/README.md:2315-2317`.
Evidence: shard budget is the binding constraint, roughly 20 shards per GB of heap, about 60 across three 1 GB nodes. `deploy/README.md:2295-2296`, `docs/PLAN.md:245`. With one primary, one machine receives the whole fleet's InfoLogger traffic and serialises every write. That funnel bites only at farm volume. `deploy/README.md:2307-2312`, `deploy/README.md:2321-2322`. Farm storage can be treated as unlimited, so durability is bought with it. `deploy/README.md:2316-2317`. The primary count is one setting and reaches an existing family at its next rollover. `deploy/README.md:2318-2321`.
Status: built and measured on staging. The farm value of one primary per storage node is agreed, not applied.
Level: architecture for the counts and the budget. Code for the setting name `log_primary_shards_storage` at `deploy/README.md:2290`.

## 7. Native systemd on staging, no containers

Choice: official yum repositories, RPMs and systemd units on the five staging machines. `deploy/README.md:86-90`, `reports/inputs/deck-text.md:100`.
Goal: run services with ordinary `systemctl` and journald ergonomics on CERN machines. `deploy/README.md:88-90`.
Alternatives: a container runtime on top of OpenStack, as in the local development stack. It is a second runtime to operate. `deploy/README.md:87-90`. A Kubernetes plan was considered in detail and dropped because Kubernetes is not available on the EPN farm. `deploy/README.md:2549-2552`.
Evidence: the divergence from the container development stack is deliberate. `deploy/README.md:87-88`.
Status: built and measured.
Level: architecture.

## 8. Containers for the storage tier on the shared farm machine

Choice: on the farm, the storage tier stays three OpenSearch nodes, run as three podman containers on one machine, `epn-infra13`. `deploy/README.md:376-378`, `deploy/inventory.epn.yml:149`.
Goal: keep the three-node tier design on the one storage machine the farm allocates. `deploy/README.md:376-378`.
Alternatives: one native node on that machine. The supervisor decided that cloning the existing three-node tier is cheaper than collapsing to one and undoing it later. `reports/loggy-report/briefs/inputs/memory-extracts.md:12-15`.
Evidence: the same role installs the vendor RPM when a machine carries one node, or podman containers when it carries several. `deploy/roles/loggy_opensearch/README.md:138`. Everything else, tier attributes, allocation filters, replica counts, templates and monitors, is unchanged from the five-machine layout. `deploy/README.md:391-393`. This layout does not rehearse fault tolerance: three replicas on one disk survive nothing. `deploy/README.md:414-415`. The farm inventory went green on 27 August 2026 with three workers and three containers on `epn-infra13`. `reports/loggy-report/briefs/inputs/memory-extracts.md:85-87`.
Status: built, deployed once on the farm. Not measured under load there.
Level: architecture for the layout. Code for the install-method flag.

## 9. Ansible for deployment and provisioning

Choice: Ansible pushes over SSH and also creates the machines. `reports/inputs/deck-text.md:389`, `reports/inputs/deck-text.md:397-401`.
Goal: configure the machines only when a person starts a run, leaving nothing running on them. `reports/inputs/deck-text.md:389`.
Alternatives:
- Puppet with Foreman. The CERN standard, repairs drift on a timer, wants an agent and a signed certificate on every node. `reports/inputs/deck-text.md:402-406`. Likely right for the farm, wrong for five machines one person deploys by hand. `reports/inputs/deck-text.md:427`.
- Salt and Chef. Salt fans out fastest, but only at farm scale. Chef needs a second language. Both put an agent on every node. `reports/inputs/deck-text.md:407-411`.
- Terraform. Builds machines well, and its vendor calls configuring them a separate job. That is two tools. `reports/inputs/deck-text.md:412-415`.
- Shell scripts. Never a candidate: a script does a thing but never checks the thing. `reports/inputs/deck-text.md:427`.
Evidence: the agent decided it. `reports/inputs/deck-text.md:390`. The farm runs Ansible, not Kubernetes. `deploy/README.md:2549-2550`. Placement is data: one inventory file says where each service runs, and moving a service changed no code. `reports/inputs/deck-text.md:419-420`. 12 checks fail the deploy rather than report success. `reports/inputs/deck-text.md:424`.
Status: built and measured.
Level: architecture.

## 10. Kafka between the collector and the store: rejected on soak numbers

Choice: no message bus between the collector and OpenSearch. `docs/SOAK_RESULTS.md:43-46`, `docs/SOAK_RESULTS.md:1854-1857`.
Goal: decide on measurement whether a bus buys decoupling or durability this stack needs. `docs/SOAK_RESULTS.md:1863-1867`.
Alternatives: the reference design places Kafka between the local collector and everything downstream. It is not wrong in general and is wrong for these rates. `docs/SOAK_RESULTS.md:1859-1861`. A durable queue on the three storage machines with KRaft was the deck's next item. `reports/inputs/deck-text.md:611-625`.
Evidence, decoupling: a real worker's busiest second in six months is 78 records a second, the whole farm's busiest second is 9,781, and one worker's stack sustains about 42,000 with 50,000 for two minutes at zero loss. `docs/SOAK_RESULTS.md:1871-1877`. The margin is 538×. Even if the archive under-counts ten times, the margin is 54×. `docs/SOAK_RESULTS.md:1879`, `docs/SOAK_RESULTS.md:1887-1888`.
Evidence, durability: the 256 MB buffer holds 865,674 records at 310 bytes each, which covers a 17.4-hour storage-tier outage at 23 records a second and 5.1 hours at 78. `docs/SOAK_RESULTS.md:1896-1902`. Past the sustainable rate the buffer dropped nothing at 82 % of cap and discarded 1.8 % oldest-first at the cap, with no crash. `docs/SOAK_RESULTS.md:1904-1908`.
Evidence, cost: the tier decision is free today because index templates carry the tier and the collector does not know which tier a record lands on. A bus moves that policy into two places. `docs/SOAK_RESULTS.md:1917-1922`. A bus either deletes the local trash tier or doubles the collector's outputs. `docs/SOAK_RESULTS.md:1924-1928`. The collector costs 27.7 core-seconds per million records at flush 1 and a Kafka output adds to it. `docs/SOAK_RESULTS.md:1935-1937`. The staging machines cannot host brokers on memory. `deploy/README.md:2328-2330`.
What would reverse it: a sustained per-worker rate above about 1,000 a second, a second consumer becoming a requirement, outages beyond the buffer cushion, or a real per-worker rate within 50× of 42,000. `docs/SOAK_RESULTS.md:1955-1962`. The one strong argument for a bus is a second independent consumer, and it is a future one. `docs/SOAK_RESULTS.md:1945-1947`.
Limits of the decision: the full stack never ran above 5,000 a second in steady state, and every higher rate came off a degraded host. The decision rests on the size of the margin. `docs/SOAK_RESULTS.md:1972-1979`.
Status: built and measured (the rejection). The bus is not built.
Level: architecture.

## 11. Kafka for the live lane: agreed in the September 2026 rework

Choice: a message bus decouples the collector from the live lane. The collector gets a Kafka output and the shifter view consumes it. `reports/loggy-report/briefs/inputs/rework-context.md:22-25`, `reports/loggy-report/briefs/inputs/rework-context.md:147-149`.
Goal: move the live lane's consumer once instead of editing the collector on every worker. `reports/loggy-report/briefs/inputs/rework-context.md:205-207`.
Alternatives: keep the direct `http` output from every collector to the shifter's `/ingest`. Moving the live lane then means editing 200+ workers. `reports/loggy-report/briefs/inputs/rework-context.md:23-24`. Push the live feed from a central aggregator instead of each worker, as the reference design does. That costs the lane its best property: it keeps working when OpenSearch is red because collectors feed it directly. `docs/SOAK_PLAN.md:391`.
Evidence: durability alone does not justify the bus, because the collector's filesystem buffer already survives a restart. Decoupling does. `reports/loggy-report/briefs/inputs/rework-context.md:205-207`. The staging memory reason for not building the queue earlier goes in the report. `reports/loggy-report/briefs/inputs/rework-context.md:25-27`. If a queue is built it must be Apache Kafka: CERN requires fully open source, which rules out Redpanda. It must be the primary path, not a failure path, because the pinned collector has no on-failure route. `deploy/README.md:2348-2353`. Record topic, partition, offset and key on every document. `deploy/README.md:2356-2358`. The rework reuses an existing kafka role rather than writing one. `reports/loggy-report/briefs/inputs/rework-context.md:24-25`.
Status: agreed, not built. It is the last and optional step of the fix pass. `reports/loggy-report/briefs/inputs/rework-context.md:147-149`.
Level: architecture.

## 12. OpenSearch Dashboards for the maintainer cockpit and Discover

Choice: the Maintainer Cockpit and Discover live in OpenSearch Dashboards. `deploy/README.md:2433-2434`, `reports/inputs/deck-text.md:445-462`.
Goal: charts and saved searches over indices, and free-form search for a physicist with a real question. `deploy/README.md:2433`, `reports/inputs/deck-text.md:459-462`.
Alternatives: Grafana, which the reference design used for both display and alerting. `docs/ARCHITECTURE.md:21`, `docs/ARCHITECTURE.md:81`. The reference design's own note says Grafana is not tailored for this use the way InfoBrowser is. `docs/ARCHITECTURE.md:88`. Grafana variables can drive a data source, which Dashboards cannot do for an index. `deploy/README.md:2369-2370`.
Evidence: no head-to-head comparison with numbers exists in the assigned sources. Dashboards ships the detection and alerting UIs with the RPM. `docs/PLAN.md:14`. The one door serves four surfaces behind one address and one account. `reports/inputs/deck-text.md:436-439`.
Status: built and measured (Dashboards in use). The comparison against Grafana: no evidence found beyond the lines above.
Level: architecture.

## 13. The shifter view as a standalone Preact app, not a Dashboards plugin

Choice: the live lane, the Templates page and the shifter view are one standalone page behind the same nginx. `deploy/README.md:2193`, `reports/loggy-report/briefs/inputs/rework-context.md:17-19`.
Goal: show what is arriving now, and what the experiment is saying, in a view that feels like InfoLogger. `reports/inputs/deck-text.md:453-456`, `reports/inputs/deck-text.md:465-468`.
Alternatives: a Dashboards plugin. A plugin must match the host's major, minor and patch version, so every security patch forces a rebuild and a failed rebuild takes the page down. `deploy/README.md:2193-2197`. A general-purpose query tool should stay in Dashboards, and Discover does. `deploy/README.md:2198-2200`.
Evidence: the argument to use in the review is the per-version rebuild, which the supervisor himself raised. "React is lighter" is not the argument. `reports/loggy-report/briefs/inputs/rework-context.md:19-21`. The lane never touches OpenSearch, so its cost on the cluster is zero and it holds when the cluster is down. `deploy/README.md:2184-2186`, `reports/inputs/deck-text.md:454-456`. It runs off the control host because its cost grows with readers. `deploy/README.md:2188-2191`. The framework files are vendored, so nothing builds and nothing fetches at deploy time. `deploy/README.md:2278-2284`, `deploy/roles/loggy_shifter_view/README.md:109-112`. The shared log view component serves both the live feed and the future InfoLogger-like query, which is what makes both cheap. `deploy/README.md:2451-2456`.
Verified browser behaviour: 1200 buffered records paint 28 row elements, and a viewer that never reads had 7785 records dropped while the server stayed up. `deploy/README.md:2209-2217`. A tab hidden for two minutes closes its own stream and reconnects only on a press. `deploy/README.md:2225-2229`, `deploy/README.md:2261-2266`.
Server-Sent Events, not WebSocket: the lane is one-way, and WebSocket would mean hand-rolled framing for a channel never used. `deploy/README.md:2270-2275`.
Status: built and measured (live lane). The InfoLogger-like shifter view needs EPN access to finish. `deploy/README.md:2437-2445`.
Level: architecture. The framework files and hashes are code-level.

## 14. The template stamper in band, with drain3

Choice: a Python service beside the collector stamps every record with its template identity, reached through a Forward loop over a Unix socket, using the same drain3 that mines the archive. `docs/TEMPLATES_FIX_PLAN.md:40`, `docs/TEMPLATES_FIX_PLAN.md:46-49`, `deploy/roles/loggy_collector/README.md:12-14`.
Goal: give both tiers the template field once, at the edge, without a raw line leaving its node. `docs/TEMPLATES_FIX_PLAN.md:46`, `docs/TEMPLATES_FIX_PLAN.md:50`.
Alternatives:
- Port the parser into a Fluent Bit filter. Filters are C, Lua or Wasm, outputs add Go, and none can host drain3. Any port is a second implementation of a masker whose byte-identical output is the identity. `docs/TEMPLATES_FIX_PLAN.md:47-48`.
- An HTTP hop instead of Forward. It replaces the record time with arrival time. `docs/TEMPLATES_FIX_PLAN.md:49`.
- PIPLUP. It costs 2.56× Drain3 on the whole corpus and 2.24× on InfoLogger against a gate of ±25 %, and holds 7.8× the memory, 206 MB against 26 MB. `docs/SOAK_RESULTS.md:2005-2009`. Its shipped code does not stream. `docs/SOAK_RESULTS.md:2019-2023`. It emits exactly one placeholder type, so a shifter loses the "lines containing an address" search. `docs/SOAK_RESULTS.md:2046-2048`. Its readability edge vanished once Drain3 ran with the recipe: median real words 6 against 5, contentless templates 10.4 % against 28.4 %. `docs/SOAK_RESULTS.md:2032-2040`.
- LFA, AEL, IPLoM, LogLSHD, Logram, LenMa, SHISO, Spell, LogMine, EFParser, all rejected with a reason in one table. LogLSHD was 9 % slower than the Drain in its own repository despite a paper claim of 73 % faster. `docs/SOAK_RESULTS.md:2082-2100`.
- KELP. It needed three source patches to survive real data, shipped no masking, and lost to a free Drain3 configuration change on every column at a third of the cost. `docs/SOAK_RESULTS.md:2102-2112`.
Evidence: no accuracy claim is verifiable because ALICE has no ground truth, so the stage was decided on cost, memory and readability. `docs/SOAK_RESULTS.md:2059-2069`. Exact counting uses acknowledgement after journaling and deduplication by chunk identifier. `docs/TEMPLATES_FIX_PLAN.md:129-130`. Bucket indices are date-named with an age policy, not a rollover alias, because a republished bucket must overwrite its earlier document. `docs/TEMPLATES_FIX_PLAN.md:5`.
Status: built, not measured on the rig or the farm. The Forward-loop cost arm is open. `docs/TEMPLATES_FIX_PLAN.md:5`, `docs/TEMPLATES_FIX_PLAN.md:265`.
Level: architecture for the in-band stamp and the parser choice. Code for socket paths and Forward options.

## 15. The masker rewrite

Choice: seven rewritten masking regexes, byte-identical output. `docs/SOAK_RESULTS.md:2314-2315`.
Goal: cut the most expensive step of template mining without changing one template. `docs/SOAK_RESULTS.md:2312-2315`.
Alternatives: leave the shipped masker. Masking was 74 to 87 % of the mining cost. `docs/SOAK_RESULTS.md:2314`.
Evidence: masking alone fell from 11.93 to 1.82 core-seconds per million on InfoLogger, 12.24 to 1.73 on stdout, 40.22 to 5.09 on dds. `docs/SOAK_RESULTS.md:2318-2322`. The mechanism is a position, not an algorithm: Python's regex engine skips characters only when a pattern opens with a literal or a class. `docs/SOAK_RESULTS.md:2324-2328`. Template counts are unchanged between the two masker columns, 908 against 908, 675 against 675, 936 against 936, 183 against 183, 701 against 701. `docs/SOAK_RESULTS.md:2385-2387`. The figure of record for the whole pipeline is 19.91 to 11.18, or 1.8×, on the whole corpus. `docs/SOAK_RESULTS.md:2383-2384`.
Status: built and measured.
Level: architecture for the cost figure. Code for the regex detail.

## 16. Random Cut Forest detectors, threshold rules and trend rules, not learned log-text models

Choice: three lanes read numbers about the logs: 17 Random Cut Forest detectors, hard-rule monitors, and trend monitors against a frozen seven-day baseline. `reports/inputs/deck-text.md:570-587`, `docs/explained/ANOMALY_DETECTION.md:316-317`.
Goal: catch known cliffs, unfamiliar shapes and slow drift on counts, error rates and lag. `docs/explained/ANOMALY_DETECTION.md:316-317`.
Alternatives:
- k-NN on log messages. Rejected on cost: every line runs through a model at ingest and millions of vectors sit in memory already rationed. `docs/explained/ANOMALY_DETECTION.md:330-334`.
- Templating as the detector. Rejected because the signal runs out: the set of templates is finite and "new template" stops firing. `docs/explained/ANOMALY_DETECTION.md:343-347`. Templates then k-NN stays open. `docs/explained/ANOMALY_DETECTION.md:349-352`.
- Deep sequence models: F1 about 0.23 to 0.27 under drift against about 0.71 for plain PCA. Time-series foundation models: gradient boosting and moving-variance one-liners match or beat them at a fraction of the compute. A language model in the detection path: cost and latency per line, no fair benchmark win. An external pipeline off a bus: a service to keep alive, while in-cluster detectors cost none. `docs/explained/ANOMALY_DETECTION.md:356-361`.
Evidence: the detectors, the forest and the alerting ship with OpenSearch, and feature engineering was the work. `reports/inputs/deck-text.md:553`. Static rules catch cliffs, the forest catches unfamiliar shapes and per-entity silence, trend monitors catch the drift the forest learns as normal. `docs/explained/ANOMALY_DETECTION.md:316-317`. Volume detectors use zero imputation so silence reads as volume 0, lag detectors do not. `docs/explained/ANOMALY_DETECTION.md:34-37`. Warm-up needs about 32 consecutive live intervals, which is why paced replay exists. `docs/explained/ANOMALY_DETECTION.md:38-41`. Two horizons, 1 minute and 30 minutes. `docs/explained/ANOMALY_DETECTION.md:60-62`, `docs/PLAN.md:228`. Trend monitors compare three 10-minute slices to a 7-day baseline. `docs/explained/ANOMALY_DETECTION.md:219-222`.
Trend baselines live in a Python rollup service, not an OpenSearch transform, because a transform cannot produce a fleet total per bucket. `docs/PLAN.md:232`. A native rollup writes nothing for a silent entity, which is the case the zero rows exist to mark. `deploy/roles/loggy_trend_rollup/README.md:94`.
One forecaster only, on disk fill, because forecasting needs a metric with a real threshold, smooth movement and a continuous feed. `docs/explained/ANOMALY_DETECTION.md:107-110`, `docs/PLAN.md:238`.
Status: built and measured. Detector and monitor counts moved: the deck says 28 rules and 17 detectors, the current READMEs say 30 monitors and 17 detectors, the rework counts 1 forecaster. `reports/inputs/deck-text.md:570`, `deploy/roles/loggy_cockpit_metrics/README.md:112-113`, `reports/loggy-report/briefs/inputs/rework-context.md:164`.
Level: architecture.

## 17. Episodes and the projector

Choice: one service reads every alert and detector score, writes signals, and groups the same trouble into episodes. `docs/explained/EPISODES_AND_ALERTING.md:6-9`.
Goal: turn thirty rows about one dead collector into one fault with a start and an end. `docs/explained/EPISODES_AND_ALERTING.md:25-35`.
Alternatives: leave it to OpenSearch Alerting, which has no memory and produces the same alert every minute. `docs/explained/EPISODES_AND_ALERTING.md:25-27`. Use Alertmanager as the incident database. It stores nothing durable. `docs/explained/EPISODES_AND_ALERTING.md:18-19`.
Evidence: the projector is the judge, the indexes are the record, Alertmanager is the messenger. `docs/explained/EPISODES_AND_ALERTING.md:11-12`. An episode key uses event time, not a counter, because a counter re-read from state minted a new document every thirty seconds. `docs/explained/EPISODES_AND_ALERTING.md:59-63`. An episode closes only after K consecutive healthy windows, 1 for cliff monitors, 3 for trend and grade monitors. `docs/explained/EPISODES_AND_ALERTING.md:142-149`. A detector that stops reporting marks the episode STALE and keeps it firing. `docs/explained/EPISODES_AND_ALERTING.md:166-169`. The record opens at grade 0.5 while paging needs 0.7. `docs/explained/EPISODES_AND_ALERTING.md:101-104`. Every row must name a thing, and a keyless alert blames the monitor rather than the fleet. `docs/explained/EPISODES_AND_ALERTING.md:118-129`.
Status: built and measured. The soak has run and produced working episodes. `docs/PLAN.md:238`.
Level: architecture.

## 18. Alertmanager as receiver only

Choice: Alertmanager owns notification semantics only: grouping timers, inhibition matching, silences and routing. `deploy/README.md:158-159`, `docs/explained/EPISODES_AND_ALERTING.md:197`.
Goal: decide when a person is told and how often it repeats. `reports/inputs/deck-text.md:595-597`.
Alternatives: an upstream Alertmanager role. It would put the route tree in Jinja inside group variables where a reviewer cannot read it. `deploy/roles/loggy_alertmanager/README.md:124`. A graph database for causal edges. About thirty edges, every query one hop, and a new datastore means new backup and failure modes. `deploy/README.md:2487-2491`.
Evidence: it does not persist alerts across a restart and expects the sender to re-send, so the projector's re-send contract is load-bearing and has its own dead-man monitor. `deploy/README.md:160-162`. The projector re-sends every open episode every 30 seconds inside a 5-minute resolve timeout, and refuses to start otherwise. `docs/explained/EPISODES_AND_ALERTING.md:199-203`. A dead collector reaches a human in about 2.5 minutes. `docs/explained/EPISODES_AND_ALERTING.md:214`. Inhibition ships off: all 22 causal edges ship unproven, so the generated inhibition block is empty. `deploy/README.md:2482-2485`, `docs/explained/EPISODES_AND_ALERTING.md:218-223`. Probabilities on edges are measured from injection runs, not authored. `deploy/README.md:2493-2497`.
Status: built and measured. Inhibition: built, gated, not enabled.
Level: architecture.

## 19. The break-glass path

Choice: two watchdog monitors post straight to the notification receiver, tagged as break-glass. `docs/explained/EPISODES_AND_ALERTING.md:239-242`.
Goal: page about a dead projector or a dead Alertmanager without routing through the thing reported dead. `docs/explained/ANOMALY_DETECTION.md:271-274`.
Alternatives: route every alert through the projector. Then a dead projector silences its own alarm. `docs/explained/ANOMALY_DETECTION.md:176-177`.
Evidence: any other alert name on that path fails an injection run. `docs/explained/EPISODES_AND_ALERTING.md:242`. The mirror case, a dead cluster, is covered from outside: the projector raises `opensearch-unreachable` after two failed probes, the one alert that survives a cluster-wide outage. `docs/explained/ANOMALY_DETECTION.md:277-283`. Both dying together needs an off-site heartbeat, which does not exist. `docs/explained/ANOMALY_DETECTION.md:284-285`.
Status: built and measured.
Level: architecture.

## 20. Retention windows and Index State Management

Choice: routine logs 8 days, other severities 35, InfoLogger 56, rolled and deleted by Index State Management. `reports/inputs/deck-text.md:381-383`, `deploy/roles/loggy_opensearch/defaults/main.yml:257-259`.
Goal: give each kind of log its own keep time and its own number of copies. `reports/inputs/deck-text.md:86`.
Alternatives: age-delete of whole indices. It deletes the whole index at that age, a periodic wipe, so InfoLogger would lose 90 days in one step. `docs/PLAN.md:245`. Per-document expiry for the template catalog, because those are single long-lived indices whose documents expire one by one. `deploy/roles/loggy_template_catalog/README.md:99-100`.
Evidence: rollover writes to an alias, rolls a new backing index every rollover period or 20 GB, and deletes each backing index past retention, so the window held is retention minus rollover period. `docs/PLAN.md:245`. The info tier rolls daily and deletes at 8 days, about 1600 indices at 200 boxes, and shortening the period is the one setting that would break farm scale. `deploy/README.md:1882-1886`. A machine that leaves needs no cleanup because its indices expire. `deploy/README.md:1799-1800`.
Status: built and measured.
Level: architecture for the windows. Code for the variable names.

## 21. Flush every 1 second

Choice: the collector flushes every 1 second, down from 5. `docs/SOAK_RESULTS.md:19`, `deploy/README.md:1909`.
Goal: cut the collector's own cost on the worker and the live lane's latency floor. `docs/SOAK_RESULTS.md:33-36`.
Alternatives: flush 5, the shipped value. Below 0.5 the cluster pays back more than the collector saves. `deploy/README.md:1912-1913`.
Evidence: across three runs each, the collector's own cost fell from 36.70 to 27.70 core-seconds, −24.5 %, and peak memory from 87.1 MB to 62.7 MB, −28.0 %. `docs/SOAK_RESULTS.md:22-26`. The total fell 3.4 %, and the ranges do not overlap. `docs/SOAK_RESULTS.md:24`, `docs/SOAK_RESULTS.md:30-31`. The live-lane latency floor drops from 5 s to 1 s. `docs/SOAK_RESULTS.md:27`. Flush is the only knob that moves anything. `docs/SOAK_RESULTS.md:53`. Any change moves the shipping-lag metric by up to four seconds, so trend baselines must be cleared on the first deploy. `deploy/README.md:1917-1928`.
Status: built and measured.
Level: architecture.

## 22. Heap 1 GB

Choice: the OpenSearch heap is 1 GB on staging and on the farm workers. `deploy/README.md:148-151`, `deploy/README.md:1861-1869`.
Goal: leave memory for page cache and the collector on a 3.75 GB machine, and give the worker its gigabyte back. `deploy/README.md:149-150`, `deploy/README.md:1866`.
Alternatives: 512 MB, which capped detector model memory at about 51 MB per node. `docs/PLAN.md:244`. 2 GB or 3 GB on the worker. `deploy/README.md:1862-1864`.
Evidence: a sweep of 1, 2 and 3 GB at two rates and two burst shapes found no difference the noise floor could see, because the queue that absorbs a burst is the collector's. `deploy/README.md:1863-1866`, `docs/SOAK_RESULTS.md:38-39`. Halving the worker heap halved the model budget to about 102 MB, a consequence stated and not tested. `deploy/README.md:2094-2097`.
Status: built and measured.
Level: architecture.

## 23. Four exclusive cores by cpuset

Choice: the logging stack on a worker gets four cores reserved outright. `docs/SOAK_RESULTS.md:48-50`, `reports/inputs/deck-text.md:92`.
Goal: keep the collector and the worker's own OpenSearch node from competing with reconstruction. `docs/SOAK_RESULTS.md:132-135`.
Alternatives: a share weight. It limits the average and lets a neighbour take the cores during a burst, when the collector needs them. `docs/SOAK_RESULTS.md:137-139`. An internal split between the collector and OpenSearch inside the four cores had no effect at the rates tested. `docs/SOAK_RESULTS.md:123`.
Evidence: every cost in the soak assumes four exclusive cores. The external separation was never an arm of the experiment and was a fixed property of the rig. `docs/SOAK_RESULTS.md:122`, `docs/SOAK_RESULTS.md:125-130`. If reconstruction shares the four cores, every number becomes optimistic by an unmeasured amount. `docs/SOAK_RESULTS.md:132-135`.
Status: agreed, not built on the farm. Not measured as an experiment arm.
Level: architecture.

## 24. Worker-tier cost caps for a machine that does reconstruction

Choice: the info tier on a worker runs with capped thread pools, search idle at 10 s, async translog, one merge thread per shard, index buffer at 5 %, memory lock, zstd codec, concurrent segment search off, and admission control in monitor-only mode. `deploy/README.md:1825-1880`, `deploy/README.md:2076-2109`.
Goal: cut what a data node costs beside reconstruction without changing detector output. `deploy/README.md:1820-1823`, `deploy/README.md:2072-2074`.
Alternatives: an explicit refresh interval. It silently turns search idle off. `deploy/README.md:1831-1835`. Any search idle value at or above one minute is inert because detectors query every minute. `deploy/README.md:1837-1841`. Admission control enforced. It would reject the detector queries and writes the plan says must never be rejected. `deploy/README.md:2098-2107`. Removing detection plugins from workers. Rejected: an uneven plugin set is its own failure mode. `deploy/README.md:2072-2074`.
Evidence: search idle at 10 s gives about 11 refreshes a minute against 60. `deploy/README.md:1843-1848`. Async translog loses up to one sync interval on an unclean crash on a tier with zero replicas and an 8-day life. `deploy/README.md:1853-1857`. Concurrent segment search off runs the same aggregation on one thread with a much smaller processor spike. `deploy/README.md:2081-2083`. Set admission control to enforced on the farm. `deploy/README.md:2108`.
Status: built, not measured. The refresh-rate check before and after is still an instruction. `deploy/README.md:1850-1852`.
Level: architecture for the goal and the search idle table. Code for setting names.

## 25. Collector disk buffer 256 MB and 10 retries

Choice: each log output holds up to 256 MB on disk and retries ten times, with the farm value 2 GB recorded beside it. `deploy/README.md:1964-1970`, `reports/inputs/deck-text.md:330-334`.
Goal: survive a short storage-tier outage without losing records. `reports/inputs/deck-text.md:337-338`.
Alternatives: the inherited 1 MB and 2 retries, which held less than one chunk and gave a durability budget of 10 to 30 seconds. `deploy/README.md:1957-1962`. Unlimited retries. A permanent failure would then retry one chunk forever. `deploy/README.md:1998-2000`. A dead-letter route. The pinned collector version has no on-failure route. `deploy/README.md:2055-2060`.
Evidence: ten retries hold 50 seconds at best and about 109 minutes at worst. `deploy/README.md:1965-1967`. A 2 GB buffer raised protection from 61 s to 491 s but pushed peak memory to 404 MB with 65,413 throttle events, so the memory ceiling must rise with the buffer. `deploy/README.md:2015-2028`. The ceilings 384 MB and 768 MB come from the vendor's estimate: 128 MB × 2 × 1.2 is roughly 307 MB. `deploy/README.md:2036-2041`. The buffer is a hiccup safety layer, not durability: it does not survive losing the node. `reports/inputs/deck-text.md:337-339`.
Status: built and measured.
Level: architecture for the window. Code for variable names.

## 26. Node identity from the environment, and self-registration

Choice: Ansible writes a node environment file once, the collector reads its identity from it, and a script registers the machine's own index objects at every boot. `deploy/README.md:1762-1767`, `deploy/README.md:1786-1797`.
Goal: let a machine join, leave and return without a central action. `deploy/README.md:1799-1802`.
Alternatives: identity only in a central inventory. Then a machine cannot register itself. `deploy/README.md:1766-1767`. Deleting a red alias after a reinstall. Red can mean recovering, and deleting would destroy returning data. `deploy/README.md:1805-1810`.
Evidence: a returning machine repairs its own write alias at boot. `deploy/README.md:1800-1802`. A missing node identifier would write into an index named `application-logs-local-`, so the unit fails if the file is absent. `deploy/README.md:1769-1773`.
Status: built and measured.
Level: architecture for self-registration. Code for file paths.

## 27. The program name survives, one file per process

Choice: the replay writes one file per process, and the collector recovers `program` from the file name. `deploy/README.md:115-121`.
Goal: keep the program name the farm gives a log, which is the level at which an ALICE fault appears. `deploy/README.md:1779-1782`.
Alternatives: flatten every process log of a node into one file, which destroyed the name before the collector read a line. `deploy/README.md:116-117`.
Evidence: the same tail pattern and parser serve a live worker, whose job logs have the same shape. `deploy/README.md:119-121`. The source file name is stored as a keyword so it can be aggregated, unlike the reference design's text fields. `deploy/README.md:1777-1784`.
Status: built and measured.
Level: architecture.

## 28. The semantic model shipped disabled

Choice: the Templates page ships with its semantic search backend switched off on staging. `deploy/inventory.yml:72-73`, `reports/loggy-report/briefs/inputs/rework-context.md:167`.
Goal: offer natural-language search over templates without spending memory the staging shifter machine lacks.
Alternatives: enabled by default, which the role's own default sets. `deploy/roles/loggy_shifter_view/defaults/main.yml:72`. A query router instead of a toggle: an oracle router gains only 0.031, below the practical difference, so no classifier is worth writing. `docs/SEMANTIC_RESULTS.md:41-46`. Fusion of any kind: 0.048 worse than the dense model alone. `docs/SEMANTIC_RESULTS.md:35`. An approximate vector index: costs 0.020 at 5,301 templates. `docs/SEMANTIC_RESULTS.md:36`.
Evidence: dense retrieval wins natural-language queries at 0.689 held-out nDCG@10 against 0.371 for the lexical engine, and loses exact identifiers and template similarity. `docs/SEMANTIC_RESULTS.md:22-26`. The mechanism: 47.1 % of relevant natural-language pairs share no analysed term. `docs/SEMANTIC_RESULTS.md:68-71`. The reranking token matrix is 194 MB for 5,301 templates, the one real deployment cost. `docs/SEMANTIC_RESULTS.md:54-56`. The labels are machine-made and the recommendation is provisional until a person judges it. `docs/SEMANTIC_RESULTS.md:76-79`. The shifter service on staging runs under a 384M memory ceiling. `deploy/inventory.yml:74-75`. The measured memory peak that the disabled default rests on is not in the assigned sources.
Status: built, not measured in deployment. Disabled on staging.
Level: architecture for the toggle and the numbers. Code for the flag.

## 29. int8 embeddings

Choice: not settled. int8 is measured as free on the index and as slower on compute.
Goal: shrink the vector store for template search without losing ranking quality.
Alternatives: float32, float16, binary, and fewer dimensions. Binary saves 97 % and costs 0.082, keeping only half the float top ten. `docs/SEMANTIC_RESULTS.md:2043-2047`. 256 dimensions costs 0.0295, just inside the practical difference of 0.032. `docs/SEMANTIC_RESULTS.md:2055-2060`.
Evidence: int8 costs 0.0006 nDCG@10 and saves three quarters of the index, 768 bytes per vector against 3,072. `docs/SEMANTIC_RESULTS.md:2042-2045`. On the embedding side, int8 dynamic quantisation made the baseline model 5.4 times slower, 107 templates per core-second against 581, measured on Apple silicon and recorded as a warning. `docs/EMBEDDING_RESULTS.md:34-38`. The embedding pick was a static model at 18,037 templates per core-second, 31 times faster than the baseline with 93 % of its quality. `docs/EMBEDDING_RESULTS.md:27-30`, `docs/EMBEDDING_RESULTS.md:40`. The semantic round ran no quantised arm of its own winner. `docs/SEMANTIC_RESULTS.md:83-84`.
Status: open. Whether int8 vectors ship: no evidence found.
Level: architecture.

## 30. Cockpit metrics against Telegraf and Mimir

Choice: a poller on the control host samples the cluster every 30 s, workers push their own collector health sample, and the poller marks absence against a roster. `reports/loggy-report/briefs/inputs/rework-context.md:29-34`.
Goal: watch the log pipeline and the log cluster, never the machine. `deploy/README.md:2541-2544`.
Alternatives: a Prometheus exporter, Metricbeat, or the performance analyzer plugin. Each writes to a second store the monitors and detectors do not read. `deploy/roles/loggy_cockpit_metrics/README.md:116-120`. Host metrics of our own. Another person owns machine health at CERN with Mimir, and a second source of truth is worse than none. `deploy/README.md:2541-2547`.
Evidence: nothing reaches into a worker to scrape it. `reports/inputs/deck-text.md:556`, `reports/loggy-report/briefs/inputs/rework-context.md:33`. The code is 4 files, 702 lines. `reports/loggy-report/briefs/inputs/rework-context.md:34`. The honest weakness is overlap with the Telegraf and Mimir estate CERN already runs, and that half is conceded at once. `reports/loggy-report/briefs/inputs/rework-context.md:35-36`, `reports/loggy-report/briefs/inputs/rework-context.md:216-218`. The rework checks the target repository's telegraf role for an OpenSearch input and keeps the roster and absence logic. `reports/loggy-report/briefs/inputs/rework-context.md:142-144`.
Status: built and measured (the poller). The Telegraf decision: open.
Level: architecture.

## 31. Packaging shared Python, not porting to OpenSearch plugins

Choice: shared code becomes one versioned `loggy` package, dead code is deleted, and build-time generators leave the roles. `reports/loggy-report/briefs/inputs/rework-context.md:37-42`.
Goal: make 31,000 lines of Python navigable and justify what must exist. `reports/loggy-report/briefs/inputs/rework-context.md:40-42`, `reports/loggy-report/briefs/inputs/rework-context.md:61`.
Alternatives: port the Python to OpenSearch plugins. Rejected: plugins pin to a version and need a Java toolchain. `reports/loggy-report/briefs/inputs/rework-context.md:37-38`.
Evidence: byte-identical copies of five modules exist across roles. `reports/loggy-report/briefs/inputs/rework-context.md:67-71`. The criterion: package a file when more than one role uses it, another module imports it, it has third-party dependencies, or it needs tests. Packaging does not reduce volume. `reports/loggy-report/briefs/inputs/rework-context.md:183-189`. Defensible as custom: template mining and the masker, the semantic model, signal projection, triage. Not defensible: cluster and node metrics that duplicate Telegraf and Mimir, and the patch fragments. `reports/loggy-report/briefs/inputs/rework-context.md:215-218`.
Status: agreed, not built.
Level: architecture.

## 32. Rejected upstream roles

Choice: every role in the tree is our own. Each README records the upstream candidates checked and why each lost. The rework moves this table to the report. `reports/loggy-report/briefs/inputs/rework-context.md:54`, `reports/loggy-report/briefs/inputs/rework-context.md:103-104`.
Goal: stop the question being reopened at each review. `deploy/roles/loggy_opensearch/README.md:572`.
Alternatives and evidence, by component:
- OpenSearch: the vendor's playbook is a playbook and not a role, reads inventory groups directly, is built around the security plugin this cluster disables, and does not support AlmaLinux 9. Two community roles are single-maintainer or stale and cannot express the two-tier node roles. `deploy/roles/loggy_opensearch/README.md:576-579`. None could hold the tier design or the plugin gate. `deploy/roles/loggy_opensearch/README.md:581-583`.
- Collector: the vendor publishes no Ansible content. One community role is Debian-only, one emits classic configuration without YAML multiline parsers, four are low-activity install-plus-dictionary roles. `deploy/roles/loggy_collector/README.md:99-106`. An upstream role would replace three tasks, and the pipeline is the role. `deploy/roles/loggy_collector/README.md:108-110`.
- Alertmanager: the Prometheus collection role is the closest call. It would move the route tree into Jinja inside group variables and its listen-address variable replaces one three roles read. `deploy/roles/loggy_alertmanager/README.md:116-126`.
- Detection: the vendor playbook has no task that talks to the Alerting or Anomaly Detection API. The Terraform provider is a second tool with its own state. Galaxy has nothing. `deploy/roles/loggy_anomaly_detection/README.md:114-116`.
- Cockpit metrics: exporter, Metricbeat and performance analyzer each write to a store the monitors do not read. `deploy/roles/loggy_cockpit_metrics/README.md:118-120`.
- Dashboards and nginx: the vendor playbook's substance is the security plugin this cluster removes. The maintained nginx role owns the site-wide configuration. The crypto collection is used, not rejected. `deploy/roles/loggy_os_dashboards/README.md:107-109`.
- Common host preparation: swap, timesync, firewall and kernel-settings roles each replace a few stable tasks and split or hide something this tree needs. `deploy/roles/common/README.md:144-147`.
- Trend rollup: a systemd role hides the environment block, and a native OpenSearch rollup writes nothing for a silent entity. `deploy/roles/loggy_trend_rollup/README.md:93-94`.
- Template catalog: Index State Management deletes whole indices by age, and rollover would duplicate a catalog upserted by version. `deploy/roles/loggy_template_catalog/README.md:99-100`.
- Replay, faults, operations page, projector, shifter view: no vendor exists because the software exists only here. `deploy/roles/loggy_replay/README.md:87-93`, `deploy/roles/faults/README.md:214-228`, `deploy/roles/alice_ops/README.md:235-239`, `deploy/roles/loggy_signal_projector/README.md:176-180`, `deploy/roles/loggy_shifter_view/README.md:109-112`.
Status: agreed and recorded.
Level: architecture for the table. Role names are code-level and must not appear in the report body.

## 33. The S3 replay as the test harness

Choice: a service on every worker streams that machine's slice of the farm's archived logs out of the CERN S3 backup bucket, and lays them down where the collector reads. `deploy/roles/loggy_replay/README.md:3-6`.
Goal: give a machine with no live log stream real data. `deploy/roles/loggy_replay/README.md:12-13`.
Alternatives: mock logs. The archive is real: 45.6 million lines mined, and six months of worker rates. `docs/EMBEDDING_RESULTS.md:6-7`, `docs/SOAK_RESULTS.md:1873-1874`. A burst replay: it drained in 7 to 13 minutes and left all 14 log detectors half-trained, so replay is paced by default. `docs/PLAN.md:234`.
Evidence: the archive gives a floor on the worker rate because the InfoLogger client cuts a process off above 1,000 messages a minute. `docs/SOAK_RESULTS.md:1884-1888`. Replay keeps the archive's event time and the collector stamps its own accept time, so shipping lag is valid on replay and entry lag is not. `docs/PLAN.md:17`, `docs/PLAN.md:224`. Today a lost record is recoverable because the replay runs again. On a live farm it is gone. `deploy/README.md:2340-2342`. The archive holds neither the journal nor the ten per-device process logs. `reports/inputs/deck-text.md:634-635`.
Status: built and measured.
Level: architecture.

## 34. The injection scenarios

Choice: six fault scenarios drive the alerting thresholds. `deploy/README.md:1550-1556`.
Goal: measure every grouping and inhibition threshold from storm behaviour rather than choose it. `deploy/README.md:1546-1547`.
Alternatives: a stress role driven from the controller, or a chaos framework that owns the experiment loop. Neither exposes an endpoint the scoring run can call and cancel. `deploy/roles/faults/README.md:217-225`.
Evidence: the scenarios are kill the collector, drop one EPN's stream, CPU-stress a worker, stop the metrics poller, end the replay, stop the projector. `deploy/README.md:1550-1556`. Scenario 2 drops records through a temporary ingest pipeline rather than a file, because a file drop kills the whole family or duplicates on re-read. `deploy/README.md:1599-1608`. Each injection is a labelled experiment, and a scorer counts per edge how often the symptom follows the cause. `deploy/README.md:2493-2497`. The scorecard fails if proven edges suppressed nothing, and fails when a symptom arrives before its cause. `deploy/README.md:2510-2523`.
Status: built and measured on staging.
Level: architecture.

## 35. Dual clock, not shifted-clock replay

Choice: log detectors key on the collector's accept time, while Discover keeps the event time. `docs/PLAN.md:224`.
Goal: unlock real-time detection on replayed June logs without rewriting timestamps. `docs/PLAN.md:224`.
Alternatives: shifted-clock replay, which stays optional and cosmetic. `docs/PLAN.md:224`.
Evidence: lags are materialised at ingest, one definition instead of N, and queryable in Discover. `docs/PLAN.md:225`. The category field differs per family, `hostname` on InfoLogger and `host` on the generic families, folded into one `origin_host`. `docs/PLAN.md:226`, `docs/PLAN.md:248`.
Status: built and measured.
Level: architecture.

## 36. The operator's node picker outside Dashboards, and the index-filter rule

Choice: picking a machine must filter on the index, never on a field, and the form lives on the standalone page. `deploy/README.md:2567-2571`.
Goal: let OpenSearch skip shards instead of reading all 200 and discarding 199. `deploy/README.md:2568-2569`.
Alternatives: a Dashboards control. Dashboards only filters fields, which enforces the opposite of the rule. `deploy/README.md:2366-2372`.
Evidence: inside Dashboards the rule is enforced by measurement through query insights, and daily rollover prunes most of each machine's eight indices. `deploy/README.md:2573-2579`. Panels must not grow with the fleet: four counters, ten-row tables, distribution lines. `deploy/README.md:2399-2415`.
Status: the index-filter rule is agreed. The form is not built. `deploy/README.md:2362`.
Level: architecture.

## 37. Authentication inside the cluster

Choice: not built. One password guards the web page and a firewall rule is the whole boundary. `reports/inputs/deck-text.md:658-660`.
Goal: an account for every job and authentication on every call. `reports/inputs/deck-text.md:662-663`.
Evidence: fine on five machines we own, not on the farm. `reports/inputs/deck-text.md:661`. The detection APIs are unauthenticated inside the security group. `docs/PLAN.md:246`.
Status: open.
Level: architecture.

---

## Choices with no evidence found in the assigned sources

- OpenSearch Dashboards against Grafana as a measured or argued head-to-head. Only the reference design's use of Grafana and one line on Grafana variables exist. `docs/ARCHITECTURE.md:21`, `deploy/README.md:2370`. Marked "no evidence found".
- Whether int8 vectors ship in the deployed Templates page. Marked "no evidence found".
- The measured memory peak of the semantic model that decided the disabled default on staging. Marked "no evidence found".
- Preact against React as a framework decision. The README says React is vendored, the rework and the role README say Preact. `deploy/README.md:2277-2285`, `reports/loggy-report/briefs/inputs/rework-context.md:17`, `deploy/roles/loggy_shifter_view/README.md:111`. No source records the switch or its reason.
- Four cores by cpuset applied on the farm. The soak requires it. No source shows it applied. `docs/SOAK_RESULTS.md:115-116`.
