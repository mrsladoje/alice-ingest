# Brief: the target architecture

Written 2026-09-14 for the CERN Summer Student report on loggy. Every claim carries a path:line source. "Architecture" marks a fact the report body should carry. "Code" marks evidence only.

Sources read, and only these ranges: reports/loggy-report/briefs/inputs/rework-context.md (all), reports/inputs/dataflow-atlas.md lines 1 to 31 and 98 to 506, deploy/README.md lines 1 to 165 and the queue passages found by grep (1852 to 1875, 1940 to 1960, 1995 to 2070, 2324 to 2362, 1822 to 1832, 1905 to 1925), reports/inputs/deck-text.md slides 5, 9, 10, 12, 14 and 15, reports/loggy-report/briefs/inputs/memory-extracts.md (all), and the svg and section lines of presentation/index.html by grep.

Audit caveat: the atlas walkthroughs 1 and 2 were audited by the author (reports/inputs/dataflow-atlas.md:5). The 43 component cards were not (reports/inputs/dataflow-atlas.md:5). Every card claim below is therefore "reference, check against code" unless the same fact also appears in an audited walkthrough or in deploy/README.md.

## 1. The system in one paragraph

loggy is a logging platform for the ALICE EPN farm. Every worker runs one collector that tails the O2 log files and listens for InfoLogger rows, routes each line by severity, and writes only to the OpenSearch node on the same machine (reports/inputs/dataflow-atlas.md:142-144). Before any line is indexed, a stamper beside the collector gives it a template identity and counts how often each template appears (reports/inputs/dataflow-atlas.md:151-153). Info-level bulk stays on the worker in an unreplicated index for 8 days (reports/inputs/dataflow-atlas.md:28). Warnings, errors and every InfoLogger record cross the wire once, to a storage tier of three replicated nodes (reports/inputs/dataflow-atlas.md:18, 241). Severe lines also go to a live lane that a person watches in a browser without a cluster query (reports/inputs/dataflow-atlas.md:19). A poller samples the cluster every 30 seconds, and every collector pushes its own heartbeat (rework-context.md:29-34, dataflow-atlas.md:160-162). Seventeen anomaly detectors, one forecaster and thirty rules read the logs and the samples inside the cluster (reports/inputs/dataflow-atlas.md:193-217). A projector turns alerts and anomaly results into incident episodes and keeps Alertmanager told every 30 seconds (reports/inputs/dataflow-atlas.md:457-459). Alertmanager decides when a person is told, and a receiver stores what was sent (reports/inputs/dataflow-atlas.md:432, 441). Two windows exist: OpenSearch Dashboards for the maintainer, behind one nginx door, and a shifter view with a live page and a templates page (reports/inputs/dataflow-atlas.md:378, 477). Ansible from lxplus creates the indices, policies, monitors and detectors at deploy time and leaves nothing running of its own (reports/inputs/dataflow-atlas.md:484-486). Architecture.

Paths in this brief: rework-context.md means reports/loggy-report/briefs/inputs/rework-context.md, memory-extracts.md means reports/loggy-report/briefs/inputs/memory-extracts.md, atlas means reports/inputs/dataflow-atlas.md, deck means reports/inputs/deck-text.md.

## 2. Tiers and hosts

### 2.1 Where the tiers live

Staging: five OpenStack machines, two worker and three storage, one OpenSearch cluster named alice-logs (deploy/README.md:4-5, 58). Machine size m2.medium, 2 vCPU and 3.75 GB RAM (deploy/README.md:148). Architecture.

Farm pilot: three workers, epn146, epn228 and epn323, plus three OpenSearch containers on epn-infra13 (memory-extracts.md:85-87). Cluster alice-logs, six nodes, green on 27 August 2026 (memory-extracts.md:86-87). The three-container layout was decided by the supervisor because cloning the three-node tier is cheaper than collapsing it and undoing that later (memory-extracts.md:12-15). epn-infra13 also carries a second, unrelated OpenSearch cluster on ports 9200 and 9300, so ours uses 9201 to 9203 and 9301 to 9303, and Alertmanager port 9193 because 9093 is taken (memory-extracts.md:100-104). Architecture.

Production: more storage machines than three. No source read gives a number. Not decided in the sources read.

Worker node roles: data and ingest, never cluster manager, attribute role=worker, attribute box=node id (deploy/README.md:61-63). Storage node roles: cluster manager, data, ingest, quorum 2, so one storage node may be lost (deploy/README.md:70-72). The control host is the first storage node (deploy/README.md:75-76). Architecture.

The task text for this brief places the projector on the control host. The sources place it on node-04 with the trend rollup (atlas:455, deploy/README.md:78-80, rework-context.md:165). This brief follows the sources. See section 8.

### 2.2 Worker tier, on every EPN

| Component | Job | Cadence | Reads | Writes | Status | Card line | Rework line |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Collector (Fluent Bit) | Parses five sources, routes by severity, loops every record through the stamper, writes only to its own local OpenSearch node (atlas:144) | streaming (atlas:140) | dds.log, stdout tree, InfoLogger daemon log, ODC logs, the journal, TCP 5170 (atlas:133) | localhost:9200, the live lane over http (atlas:142, 30) | built and running on staging (atlas:15-20, audited); deployed on the farm pilot (memory-extracts.md:108-112) | atlas:139-146 | atlas:146 |
| Stamper (drain3) | Gives every record a template id before it is indexed, publishes counts per template every five minutes (atlas:153) | per record, publish every 300 s, self-check hourly (atlas:149) | the chunk from the collector socket (atlas:151) | stamped chunk back to the collector, bucket documents, definitions, watermark, hourly check (atlas:151) | built and running on staging (atlas:17, audited); farm pilot status not stated in the sources read | atlas:148-155 | atlas:155 |
| Collector health sample | Pushes the collector's own heartbeat into cockpit-metrics every 30 seconds, nothing scrapes the worker (atlas:162) | every 30 s (atlas:158) | Fluent Bit metrics and health endpoints on loopback, the stamper status file (atlas:160) | one record, kind fluentbit, into cockpit-metrics (atlas:160) | built and running on staging; not audited (atlas:5) | atlas:157-164 | atlas:164 |
| Local OpenSearch data node | Holds the worker's own info index and runs the ingest pipeline on the worker (deploy/README.md:61-69) | continuous | bulk requests from the collector (atlas:178) | application-logs-local-node, forwards central and infologger one hop to storage (deploy/README.md:127-133) | built on staging and on the farm pilot (memory-extracts.md:85-87) | atlas:175-182, 220-227 | atlas:182, 227 |
| Replay engine | Stands in for a live EPN: writes the same files and sends the same InfoLogger rows a real node would (atlas:126) | on request, paced by rates (atlas:122) | the CERN S3 archive (atlas:124) | files under the log root, InfoLogger rows over TCP 5170 (atlas:124) | staging only; on the farm the real O2 processes produce the sources (atlas:133) | atlas:121-128 | atlas:128, tester tooling |
| Fault agent | Lets an injection run kill the collector on purpose (atlas:171) | on request (atlas:167) | commands from the control host only (atlas:169) | none | tester tooling, staging (atlas:173) | atlas:166-173 | atlas:173 |

Worker cost: four of the 128 cores an EPN has, and little memory (deck:92). The processor cap is a cap and never a floor (deploy/README.md:1825-1830). Architecture.

### 2.3 Storage tier, three replicated nodes

Three nodes, cluster manager plus data, quorum 2 (deploy/README.md:70-72). On staging they are three virtual machines (deploy/README.md:40-54). On the farm they are three containers on epn-infra13 (memory-extracts.md:12-13, 86). The storage tier runs no collector and no replay (deploy/README.md:55). The indices they hold are in section 3. Architecture.

### 2.4 Control host, the first storage node

| Component | Job | Cadence | Reads | Writes | Status | Card line | Rework line |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dashboards behind nginx | The maintainer's window, and nginx is the single TLS door to it, the ops page, the live lane and Alertmanager (atlas:378) | on request (atlas:374) | the cluster through six index patterns (atlas:376) | 59 saved objects at deploy time (atlas:376) | built and running on staging; Dashboards on the farm pilot (memory-extracts.md:7, 119-120) | atlas:373-380 | atlas:380 |
| Poller | Copies the cluster's own status into documents every 30 seconds and marks which rostered collector went silent (atlas:414) | every 30 s, prune hourly (atlas:410) | _cluster/health, _cat/indices, _nodes/stats, the Dashboards status endpoint, the roster, the fluentbit heartbeats of the last 90 seconds (atlas:412) | cluster, osd, index, node and fleet documents into cockpit-metrics (atlas:412) | built and running on staging; farm not stated | atlas:409-416 | atlas:416 |
| Roster | Says which collectors are supposed to be alive, so silence can be told apart from a node that never existed (atlas:288) | one document per topology change, published by Ansible (atlas:284-286) | the inventory | cockpit-fleet (atlas:283) | built on staging | atlas:283-290 | atlas:290 |
| Catalog maintenance | Once an hour it proves the template counts add up and expires what is old (atlas:423) | hourly oneshot (atlas:419) | hourly bucket documents, the shared log indices (atlas:421) | check results and reports into template-catalog, expiry in shifter-queries (atlas:421) | built on staging; its future form is open (atlas:425) | atlas:418-425 | atlas:425 |
| Alertmanager | Decides when someone is told and holds no state of what is true (atlas:432) | group wait 30 s for page, 5 min for warn, repeat every 4 hours (atlas:428, 430) | alerts posted by the projector (atlas:430) | one webhook to the receiver on localhost (atlas:430) | built on staging; agreed to stay as is (atlas:434) | atlas:427-434 | atlas:434 |
| Receiver | Turns each notification into a stored document, so the platform can prove what a person was told (atlas:441) | per POST (atlas:437) | POSTs from Alertmanager and from the two break-glass monitors (atlas:439) | alice-notifications, one document per batch, 60-second dedupe (atlas:439) | built on staging | atlas:436-443 | atlas:443 |
| Ops page | The operator's status line plus the buttons that drive the test harness (atlas:387) | on request (atlas:383) | counts of families, alerts, anomaly results, incidents, signals (atlas:385) | nothing on the read path | built on staging; the buttons are tester tooling (atlas:385, 389) | atlas:382-389 | atlas:389 |
| Injection runner and poison replay | Break one thing on purpose and score whether a signal, an incident and a notification followed (atlas:396); flood the indices with marked fake errors (atlas:405) | on request | fault agents, replay endpoint, the result indices (atlas:394) | synthetic documents with a run id (atlas:403) | tester tooling (atlas:398, 407) | atlas:391-407 | atlas:398, 407 |

Notification delivery beyond the stored document is not built: nothing e-mails or pages a human today (atlas:493). Architecture.

### 2.5 Background host, the second storage node

| Component | Job | Cadence | Reads | Writes | Status | Card line | Rework line |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trend rollup | Turns raw logs into one small row per host per 10 minutes, so the trend rules stay cheap (atlas:450) | every 600 s, re-rolls the last three closed buckets with a 120-second settle, prune hourly (atlas:446, 448) | the three log families by collector_time (atlas:448) | trend-rollup rows, _meta and _commit rows (atlas:448) | built on staging | atlas:445-452 | atlas:452 |
| Projector | Reads alerts and anomalies, decides what is one incident, and keeps Alertmanager told every 30 seconds (atlas:459) | every 30 s (atlas:455) | the live alert index, alert history, anomaly results past watermarks, the roster snapshot at event time, signal_catalog.json (atlas:457) | alice-signals, alice-incidents, alice-lane-state, heartbeats into cockpit-metrics, posts to Alertmanager (atlas:457) | built on staging; farm not stated | atlas:454-461 | atlas:461 |
| Fault agent | Lets an injection stop the projector to prove break-glass works (atlas:468) | on request | | | tester tooling | atlas:463-470 | atlas:470 |

The projector raises opensearch-unreachable straight to Alertmanager if the cluster is unreachable for two cycles (atlas:457). Alertmanager's raw port is admitted only from this host (atlas:430, deploy/README.md:156-157). Architecture.

### 2.6 Shifter host, the third storage node

| Component | Job | Cadence | Reads | Writes | Status | Card line | Rework line |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Shifter view | The console a person on shift watches: live severe lines with zero cluster cost, and the template view (atlas:477) | streaming and on request (atlas:473) | live lane: infologger and central records posted by every collector to /ingest, last 500 kept in memory (atlas:475); templates page: catalog, buckets, sample lines, incidents, template-triage (atlas:475) | template-triage, shifter-queries (atlas:268, 475); nothing from /ingest is written anywhere (atlas:475) | built and running on staging (atlas:19, audited); farm not stated | atlas:472-479 | atlas:479 |

The optional semantic model ships disabled on staging under a 384 MB memory ceiling (atlas:475). The live lane today is a direct http output on every collector, matched to the tags stamped.infologger, stamped.ildaemon and stamped.family.central only (atlas:30, audited). Architecture.

### 2.7 The bus, agreed and not built

Kafka brokers carry the live lane only (rework-context.md:22-25). The collector gets a Kafka output, and the shifter view consumes the topic (rework-context.md:147-148). The reason is decoupling: one consumer moves instead of 200 collector configurations (rework-context.md:209-211). Durability alone does not justify it, because the collector's disk buffer already survives a restart (rework-context.md:209-210). The Kafka role from the existing merge request !413 is reused, not written (rework-context.md:25, 147). It is the last, optional step of the fix pass (rework-context.md:147-148). Broker placement is not decided in the sources read: step 1 of the fix pass owns "the bus placement" (rework-context.md:117-118). Status: agreed, not built. Architecture.

This is decision (b). Decision (a), the queue between collector and OpenSearch, is separate and stands on its own. See section 5.

### 2.8 Deploy time

Ansible from lxplus creates index templates, ISM policies, the ingest pipeline, the roster snapshot, notification channels, monitors, detectors, the forecaster, index patterns and saved objects, once per deploy (atlas:484). It builds the shelves and the runtime flows fill them (atlas:486). Nothing runs on the machines on its behalf afterwards. Architecture. The move from bash scripts to the uri module is an implementation detail (atlas:488). Code.

## 3. Indices

| Index | Holds | Tier | Shards and replicas | Retention | Source |
| --- | --- | --- | --- | --- | --- |
| application-logs-local-<node> | info and debug bulk, one per worker, pinned to that worker's node | worker | 1 shard, 0 replicas | roll 1 d or 20 GB, delete after 8 d | atlas:220-225, deploy/README.md:64-65 |
| application-logs-central | warnings, errors, fatals, the daemon log, every unparsed line | storage | 2 replicas; primaries: see doubt 1 | roll 7 d or 20 GB, delete 35 d | atlas:238-241, deploy/README.md:71-72 |
| infologger | all InfoLogger records, every severity, strict mapping | storage | 2 replicas; primaries: see doubt 1 | roll 7 d or 20 GB, delete 56 d | atlas:229-232 |
| template-buckets-5m-* and 1h-* | counts per template version per node per window | storage | not stated in the sources read | 5-minute buckets 4 d, hourly buckets 66 d | atlas:247-250 |
| template-catalog | template definitions, watermarks, checks, hourly reports | storage | not stated | expired by catalog maintenance | atlas:256-261 |
| template-triage, shifter-queries | labels and notes a person set, query history | storage | not stated | shifter-queries expired after 365 d | atlas:265-268 |
| cockpit-metrics | health samples: cluster, index, node, osd, fleet, fluentbit, projector, alertmanager | storage | 1 shard, 2 replicas | pruned by document at 7 d by the poller, not by ISM | atlas:274-277, deploy/README.md:49 |
| cockpit-fleet | roster snapshots, one per topology change | storage | not stated | immutable | atlas:283-286 |
| trend-rollup | one row per family and entity per 10-minute bucket | storage | not stated | 30 d by document | atlas:292-295 |
| .opendistro-anomaly-results* | detector verdicts | cluster, plugin-owned | plugin default | history deleted 14 d | atlas:301-304 |
| opensearch-forecast-results* | forecast rows | cluster, plugin-owned | plugin default | 14 d | atlas:310-313 |
| .opendistro-alerting-alerts and history | the live alert per monitor and bucket | cluster, plugin-owned | plugin default | history deleted 30 d | atlas:319-322 |
| alice-alert-actions | monitor action sink, nothing reads it | storage | rollover alias | 30 d | atlas:328-331; deletion candidate atlas:335 |
| alice-lane-state | the projector's bookmark, two documents | storage | single index | none | atlas:337-340 |
| alice-signals | one normalised alert or anomaly row | storage | single index | 30 d | atlas:346-349 |
| alice-incidents | one document per episode | storage | single index | terminal rows 30 d | atlas:355-358 |
| alice-notifications | one document per notification batch | storage | single index | 30 d | atlas:364-367 |

The three log families and the retention line for the report: routine logs 8 days, others 35, InfoLogger 56 (deck:383). The split is by severity, not by source, so the bulk never crosses the wire (deploy/README.md:97-107, deck:356-360). Every log index runs the ingest pipeline through default_pipeline, so the worker holds the ingest role and the local write stays on the worker (deploy/README.md:66-69, atlas:178). Architecture.

## 4. Lanes

Lane definitions are at atlas:110-117.

- Ingest (atlas:110): O2 log sources, replay engine on staging, collector, stamper, ingest pipeline, application-logs-local, application-logs-central, infologger. The audited path is atlas:15-20 and atlas:26-30.
- Cluster state (atlas:111): the ingest pipeline, the plugin-owned result and alert indices, the cluster REST APIs (atlas:175-191, 301-326).
- Platform health (atlas:112): collector health sample, poller, roster, cockpit-metrics, cockpit-fleet (atlas:157-164, 274-290, 409-416).
- Detection (atlas:113): 17 detectors, 1 forecaster, 30 monitors, trend-rollup and the trend rollup service (atlas:193-218, 292-299, 445-452).
- Signals and notification (atlas:114): projector, alice-lane-state, alice-signals, alice-incidents, Alertmanager, receiver, alice-notifications (atlas:337-371, 427-443, 454-461).
- People and views (atlas:115): Dashboards behind nginx, ops page snapshot, shifter view, Alertmanager UI for silences (atlas:373-389, 472-479, 490-496).
- Tester tooling (atlas:116): replay engine, fault agents, injection runner, poison replay, the ops page buttons, the CERN S3 archive (atlas:121-128, 166-173, 391-407, 463-470, 499-505). Not on the production path (atlas:116).
- Deploy time (atlas:117): Ansible from lxplus (atlas:481-488). Not a runtime flow (atlas:117).

## 5. What the rework changes in the picture, and what it does not

Changes the report must draw:

1. The live lane moves from a direct http output on every worker to a Kafka topic with one consumer, the shifter view (rework-context.md:22-25, 147-148, atlas:146, 479). Today moving the live lane means editing 200+ workers (rework-context.md:24). Architecture.
2. Cockpit metrics stays as the poller plus roster and absence logic (rework-context.md:142-143, atlas:416). The report concedes that the cluster, index, node and osd kinds overlap the Telegraf and Mimir estate CERN already runs (rework-context.md:34-36, atlas:281). A telegraf role exists in the target repository (rework-context.md:36). Whether a Telegraf OpenSearch input replaces those kinds is open (atlas:191, rework-context.md:142). Architecture.
3. Shared Python becomes one versioned package: masker, benchmark, contract, cursor, signal identity (rework-context.md:138-141). Packaging does not reduce volume, and the report says so (rework-context.md:189). Architecture, one sentence.
4. OpenSearch plugins were rejected: a plugin pins to a version and needs a Java toolchain (rework-context.md:37-39). The same argument keeps the Templates page in the shifter view (rework-context.md:19-21). Architecture.
5. Build-time generators leave the deploy path, and the deploy ships generated JSON (rework-context.md:82-84, 122-123). Code.
6. alice-alert-actions is a deletion candidate (atlas:335). If deleted, the figure drops it.
7. Catalog maintenance may become ISM policies plus monitors instead of an hourly service (atlas:425, rework-context.md:127-128). Not decided.

Things the report does not describe: the role renames (rework-context.md:15-19, 124-126), the bash-to-uri port (rework-context.md:50-51, 132-137), the merge into one OpenSearch role (rework-context.md:127-129), the common dependency (rework-context.md:130-131), the deletion of migration code (rework-context.md:72-81, 120-121), and the commit ladder (rework-context.md:43-49). These are implementation details of the deploy tree. Code.

The two Kafka decisions, kept apart:

(a) The durable queue between the collector and OpenSearch is not built (deploy/README.md:2324-2327). Three reasons: the OpenStack machines cannot host it, the gain lands on the storage tier where the farm is not short, and the bulk tier must never cross the wire (deploy/README.md:2329-2335). The named trigger is a third consumer of the stream (deploy/README.md:2337-2339). If built it must be Apache Kafka, because CERN requires fully open source and Redpanda is not (deploy/README.md:2348-2350). The deck lists it as the next unbuilt item, three brokers on the three storage machines, costing memory the five machines do not have (deck:614-625). The orchestrator's rules say the soak rejected it on measured numbers at docs/SOAK_RESULTS.md:1852. This brief did not read that file. Architecture.

(b) The September 2026 review separately agreed a bus for the live lane only, to decouple the shifter view from every worker's collector configuration (rework-context.md:22-25). Decision (a) is about durability and the storage tier. Decision (b) is about decoupling and the live lane. The report must not merge them. The queue reasoning of decision (a) goes in the report (rework-context.md:25-27, 103-104). Architecture.

## 6. Parameters worth naming

| Parameter | Value | Why it matters | Source |
| --- | --- | --- | --- |
| Collector flush | 1 second | chosen by soak round 2; from 5 to 1 the collector's processor cost falls 24.5 percent and its peak memory 28.0 percent; the live lane latency floor drops from five seconds to one | deploy/README.md:1909-1915 |
| Disk buffer per output | 256 MB | it holds an outage between 50 seconds and 109 minutes, then the record is gone, there is no second destination | deck:330-336 |
| Disk buffer, measured | 256 MB gave 61 s before the first drop at 20,000 records a second, peak memory 373 MB, 0 throttle events; 2 GB gave 491 s, 404 MB, 65,413 throttle events | raising the buffer raises memory, contrary to the earlier belief | deploy/README.md:2013-2025 |
| Chunks in memory | 64, about 128 MB | bounds memory by the unit, not by hope | deploy/README.md:2008-2011 |
| Collector memory envelope | MemoryHigh 384 MB, MemoryMax 768 MB; documented worst case 128 MB x 2 x 1.2, roughly 307 MB | a first attempt of 256 MB and 512 MB would have throttled the collector during the burst it is meant to survive | deploy/README.md:2036-2043 |
| Collector memory, measured | 133 to 228 MB steady load, 404 MB sink outage | see doubt 6 on which run gave 404 MB | deck:298-301 |
| Worker processor cap | four of the 128 cores | a cap, never a floor | deck:92, deploy/README.md:1825-1830 |
| Worker heap | 1 GB | soak round 2 brought it down from 2 GB; 1, 2 and 3 GB showed no difference above the noise floor because the burst queue is the collector's, upstream of the heap | deploy/README.md:1861-1867 |
| Heap on staging | -Xms1g -Xmx1g on 3.75 GB machines | leaves about 2.5 GB for the OS, page cache and the collector | deploy/README.md:148-151 |
| Heartbeat | every 30 seconds from every collector | nothing scrapes a worker | atlas:158-162 |
| Absence window | heartbeats seen in the last 90 seconds, per rostered collector | heartbeat_missing 0 or 1 | atlas:412 |
| Poller cadence | every 30 seconds | | rework-context.md:30 |
| Detector windows | 1 minute, with 30-minute twins; log detectors use collector_time with a 2-minute window delay | | atlas:194-196 |
| Monitor cadences | 1 minute health, 10 minutes trend, 60 minutes catalog; action throttle 30 minutes | | atlas:212-214 |
| Trend baseline | three 10-minute slices against a 7-day baseline | | atlas:214, deck:586-587 |
| Shipping-lag trend threshold | twice a seven-day baseline, with trend_lag_floor_ms of 250 | normal lag is sub-second | deploy/README.md:1923-1924 |
| Disk forecast | 168 hours of history, 24 hours ahead, fires at 85 percent | the one forecaster, because disk has an absolute ceiling | atlas:205-207 |
| Disk threshold rule | a disk over 92 percent full | | deck:573 |
| Collector silence rule | a collector silent two minutes | | deck:574 |
| Alertmanager timers | page: group wait 30 s, interval 2 min; others 5 and 10 min; repeat every 4 hours; resolve timeout 5 min | Alertmanager holds no state, so the projector re-sends every 30 s | atlas:430 |
| Live lane memory | last 500 records in memory, one queue per viewer | | atlas:475 |
| Live lane output | buffer 1 MB, retry limit 1 | a dead viewer can never push back on OpenSearch | deploy/README.md:2004-2006 |
| Health output | buffer 512 KB, retry limit 5 | a longer queue would flood stale metrics into monitors that read gaps as a dead collector | deploy/README.md:2002-2004 |
| Rollover | 1 d or 20 GB local; 7 d or 20 GB storage | | atlas:221, 230, 239 |
| Semantic model ceiling | 384 MB, ships disabled on staging | | atlas:475 |
| Stamper message limit | empty or longer than 4096 characters gets no_template | | atlas:151 |

The 384 MB figure appears twice with two meanings: the collector's MemoryHigh (deploy/README.md:2036) and the shifter view's semantic model ceiling (atlas:475). The report must not conflate them.

## 7. Figure list

Slide sections in presentation/index.html start at lines 673 (slide 5), 1197 (slide 9), 1337 (slide 10), 1576 (slide 12), 1826 (slide 14) and 1985 (slide 15), per grep of section tags. The svg lines below are from grep of svg tags.

1. Tiers and machines. Shows the worker tier (collector, stamper, local node, local index), the storage tier (three replicated nodes, infologger and central), the control host (Dashboards, nginx, Alertmanager, receiver, poller, ops page), the background host (rollup, projector), the shifter host (shifter view). Edges: localhost write, the one hop to storage, the live lane. Start from slide 5, presentation/index.html:689. Changes needed: the shifter view is now built (atlas:472), the farm layout of three containers on one infra machine (memory-extracts.md:86), and the storage index shard count once doubt 1 is settled.

2. Inside the collector. Shows input, tag, filter, retag by severity, output, plus the stamper loop through the Unix sockets, and the disk buffer with its hold window. Start from slide 9, presentation/index.html:1237 for the five stages and the buffer, and presentation/index.html:1212 for the memory envelope. Change needed: the slide has no stamper loop; add it from atlas:17 and atlas:151.

3. The split cluster and its indices. Shows the local half, one copy, and the replicated half, three machines, with the retention line 8, 35, 56 days. Start from slide 10, presentation/index.html:1355. Change needed: none in content, but the shard count on the storage indices depends on doubt 1.

4. From numbers to one alert, the diagram the supervisor asked for: from cockpit metrics, to alerts and anomaly detection, to the projector and the trend rollup, then Alertmanager (rework-context.md:178-180). Shows the collector heartbeat push, the poller, cockpit-metrics, the rollup, the three detection lanes (threshold rules, Random Cut Forest, trend rules), the alert and result indices, the projector, incidents and signals, Alertmanager, the receiver and the stored notification. Start from slide 14, presentation/index.html:1855. Changes needed: the slide stops at Alertmanager and omits the projector, the rollup, the receiver and alice-notifications (deck:589-598); add them from atlas:445-461 and atlas:436-443.

5. The surfaces behind one door. Shows nginx, Dashboards with the cockpit and Discover, the ops page, the shifter view with the live page and the templates page, and the Alertmanager UI. Start from slide 12, presentation/index.html:1595. Change needed: the slide marks the shifter view as coming soon (deck:463-464); it is built with two pages (atlas:475).

6. The live lane before and after the bus. Left: every collector posts over http to the shifter view's /ingest (atlas:475). Right: every collector writes one Kafka topic, and the shifter view is the one consumer (rework-context.md:147-148). No slide draws this. Slide 15, presentation/index.html:2003, is a table of unbuilt items and draws the other Kafka, the durable queue (deck:614-625). Do not start from it, or the two decisions merge. Draw fresh.

7. Optional. Deploy time against runtime: what Ansible creates once and what the runtime flows fill (atlas:484-486). Slide 11 was not in the read set for this brief. Its svg is at presentation/index.html:1484 per grep; content not verified here.

## 8. Doubts and contradictions

1. Storage index primary shards. deploy/README.md:48 and :71-72 say infologger and application-logs-central have 3 shards and 2 replicas. deck:144 says 1 shard, 2 replicas, one copy on every storage node. atlas:232 says the code default is one primary shard and the README says three. memory-extracts.md:107 says the farm inventory sets log_primary_shards_storage without saying the value. The report must pick one number per environment and cite the code, which this brief did not read.

2. The projector's host. The task text places the projector on the control host. atlas:455, deploy/README.md:78-80 and rework-context.md:165 place it on node-04, away from the UI host on purpose. This brief follows the sources.

3. Monitor count. atlas:211 and rework-context.md:164 say 30 monitors. deck:570 says 28 saved rules and 17 detectors. atlas:214 explains the gap: 28 post to a sink index and 2 break-glass monitors post straight to the receiver. The report should say 30 and explain the 2.

4. Detector twins. atlas:194 says 14 at 1 minute with 7 having 30-minute twins, plus 3 at 1 minute. atlas:196 says fourteen log detectors, each with a 30-minute twin. Both cannot hold with a total of 17. Not resolved in the sources read.

5. Shifter view status. deck:433 and deck:647-654 say the shifter's own view is not built. atlas:472-479 and the audited walkthrough at atlas:19 say it runs on node-05 with two pages. The deck predates the build. The report describes it as built.

6. The 404 MB figure. deck:300-301 labels 404 MB as "measured, sink outage". deploy/README.md:2020-2021 shows 404 MB for the 2 GB buffer run and 373 MB for the 256 MB buffer run. With the shipped 256 MB buffer the measured peak is 373 MB. The report should cite 373 MB for the shipped configuration, or say which buffer gave 404 MB.

7. The hold window. deck:334 says 50 seconds to 109 minutes. deploy/README.md:2020 measured 61 s before the first drop at 256 MB and 20,000 records a second. deploy/README.md:2030-2031 gives roughly four seconds per 100 MB at that rate. The 109-minute upper bound and the 50-second lower bound are not derived in the ranges read. Source for both: the deck only.

8. Retries per output. deck:331 says ten retries. deploy/README.md:1958 gives the old value of 2 and deploy/README.md:1998-2000 says retries stay finite. The new value for the log outputs is not in the ranges read.

9. Farm pilot status per component. memory-extracts.md:85-87 proves the cluster, the three workers and the three containers went green. memory-extracts.md:108-112 proves the collector is installed on the farm. memory-extracts.md:7 and :119-120 imply Dashboards. Nothing read says whether the stamper, the poller, the projector, the rollup, Alertmanager, the receiver or the shifter view ran on the farm. Say "not stated" rather than "deployed".

10. Chunks in memory on the farm. memory-extracts.md:28-30 says the 64-chunk burst ceiling must be measured before anything lands on epn146 or epn323, and is still a guess. memory-extracts.md:85-86 says the deploy went green on those nodes on 27 August. Whether the measurement happened is not stated.

11. Bus placement and broker count for decision (b). Not decided in the sources read (rework-context.md:117-118, 147-148). The three-brokers-on-storage figure in deck:615 belongs to decision (a). Do not carry it over.

12. Catalog maintenance and the bucket indices. Two open choices remain: ISM plus monitors instead of the hourly service (atlas:425), and rollover alias instead of date-named bucket indices (atlas:254). The report should describe the current form and name both as open.

13. Storage heap. deploy/README.md:148 gives 1 GB on staging for every node. The worker heap of 1 GB is a measured choice (deploy/README.md:1861-1867). A separate storage heap value was not in the ranges read.

14. Production storage tier size. The task text says "in production more". No source read gives a number or a decision. Say "not decided".

15. Two 384 MB values, see the note at the end of section 6.

16. The soak rejection of the queue. The rules for this brief cite docs/SOAK_RESULTS.md:1852. This brief did not read that file. Another brief must carry the measured numbers.

17. The live lane page is "React" in deck:455 and "Preact" in atlas:472 and rework-context.md:17. The report should use the atlas term.
