# Brief: walkthroughs 6 to 9 of the dataflow atlas, checked against the code

Written 2026-09-14 for the CERN Summer Student report on loggy. The atlas is reports/inputs/dataflow-atlas.md. Its author audited walkthroughs 1 and 2 only (atlas:5). Walkthroughs 6 to 9 (atlas:62-97) were not audited. This brief checks every numbered step against deploy/ and records a verdict per claim.

Every claim below carries a path:line. "Architecture" marks a fact the report body should carry. "Code" marks evidence only. Paths: atlas means reports/inputs/dataflow-atlas.md, rework means reports/loggy-report/briefs/inputs/rework-context.md, ad means deploy/roles/loggy_anomaly_detection, sp means deploy/roles/loggy_signal_projector/files/signal_projector.py.

Sources read: atlas lines 62 to 619, rework (all), the style brief, and the code at the evidence pointers plus the files grep found beside them. The full list of code lines is in the verdict tables.

Hosts on staging, from the inventory: node-01 and node-02 are workers, node-03 is the control host, node-04 runs the projector and the trend rollup, node-05 runs the shifter view (deploy/inventory.yml:28-35, 51-75). Code.

## Verdict count

Thirty-two claims checked. Twenty-three confirmed. Seven wrong. Two unverifiable. The wrong ones: the fleet-wide scope of the ad-high-grade monitor, the mechanism of alertmanager-down, the order of expiry and checks in the catalog maintenance, the counts of index templates, write aliases and index patterns, and the claim that the collector play runs last on the workers. None of the wrong claims changes the shape of the design. Two change what the report should say about limits: the 24-hour guard in signal-projector-stale and the dependence of alertmanager-down on a live projector.

---

## Walkthrough 6. A detector sees something unusual

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
| --- | --- | --- | --- |
| 6.1 | il-per-epn evaluates every minute per origin_host | CONFIRMED | ad/files/detectors/il-per-epn.json:35-40 (interval 1 minute), :48-50 (category_field origin_host) |
| 6.1 | Two-minute window delay on collector_time | CONFIRMED | il-per-epn.json:4 (time_field collector_time), :41-46; ad/templates/detectors.sh.j2:7, 36-48 sets the delay from AD_LOG_WINDOW_DELAY_MINUTES; ad/defaults/main.yml:21 (2) |
| 6.1 | Reads InfoLogger volume and error count | CONFIRMED | il-per-epn.json:5-7 (index infologger), :10-18 (volume: value_count of @timestamp), :21-33 (ef_count: sum of 1 where severity_norm is error or fatal) |
| 6.1 | Silent hosts get a zero imputed | CONFIRMED | il-per-epn.json:51-53 (imputation_option ZERO) |
| 6.1 | Model needs a few hundred windows, 3 to 5 hours | UNVERIFIABLE | deploy/README.md:1081 states it as a rule of thumb ("~3–5 h at 1 min"). No measurement in the sources read. deploy/README.md:1614 names a "32-window RCF warm-up" for the poison harness, with shingle_size 8 (il-per-epn.json:47). The two figures describe different things: warm-up to first score, and time until scores mean something. Not measured. |
| 6.2 | Plugin writes grade, confidence and entity per entity | CONFIRMED | sp:23 (index .opendistro-anomaly-results*), sp:439-444 (fields read: detector_id, anomaly_grade, confidence, data_end_time, execution_end_time, entity) |
| 6.3 | ad-high-grade opens an alert at grade and confidence above 0.7 | CONFIRMED, scope WRONG | ad/files/monitors/ad-high-grade.json:7-12 (every 1 minute), :16-18 (index), :68 (max grade > 0.7 and the top row's confidence > 0.7), :32-38 (execution_end_time in the last 2 minutes), :41-47 (real-time results only). The monitor is one fleet-wide tripwire, not one alert per entity: it is a query_level_monitor with size 1 (:5, :20) and its action names entity "all" (:81). Thresholds default from ad/templates/monitors.sh.j2:6-7. |
| 6.3 | Projector reads results past its watermark, turns firing rows into signals | CONFIRMED, with a number the atlas omits | sp:434-465 (watermark on execution_end_time, 3-minute overlap from sp:36-37, PIT in data_end_time order), sp:343 (a row is firing when grade > 0.5, GRADE_FLOOR sp:34, set by deploy/group_vars/all.yml:208), sp:1281-1287 (only firing rows are written as signals), sp:1276 (every row, firing or not, feeds episode state) |
| 6.4 | Forty EPNs breaching one rule are forty episodes | CONFIRMED | sp:364-378 (incident_id hashes source, alertname, entity_kind, entity_id, scope), deploy/roles/loggy_signal_projector/files/signal_identity.py:368-378 (entity_id is the origin_host value), signal_catalog.json:286-298 (il-per-epn: entity_kind epn, category_field origin_host) |
| 6.4 | One can recover while another is still bad | CONFIRMED | sp:878-891 (per-episode recovery: 3 healthy windows at grade at or below 0.0, from signal_catalog.json:296-297), sp:947-955 (an episode goes STALE after 6 minutes without an evaluation: expected_interval 1 plus maximum_lateness 5, signal_catalog.json:294-295) |
| 6.4 | Cockpit folds them into one card per group_id, display only | CONFIRMED for the Cockpit, "display only" WRONG | deploy/roles/loggy_os_dashboards/files/cockpit.ndjson:56 (the open episodes card groups on group_id). sp:388-399: group_id is cluster_id, alertname and notification_scope, "deliberately the same fields Alertmanager groups on". deploy/roles/loggy_alertmanager/templates/alertmanager.yml.j2:7, 14. So the forty episodes also travel as one Alertmanager notification. Per-entity state stays in the episode documents (sp:1013-1032). |

### Corrected walkthrough text

The learned path. Random Cut Forest runs inside the cluster. No Python runs until the projector.

1. **il-per-epn evaluates every minute.** For every origin host, the detector counts the InfoLogger records and the error and fatal records of the last one-minute window. It reads the window two minutes late, so late records still land in it. A host that logged nothing gets a zero, so silence is a value and not a gap. The plugin's own guidance is that a model needs a few hundred windows, three to five hours at one minute, before its grades mean anything (deploy/README.md:1081). That figure is not measured here. Architecture.

2. **A grade per entity.** The plugin writes one result row per host per window, with an anomaly grade between 0 and 1, a confidence, and the host. Nothing is decided yet. Architecture.

3. **Two readers.** The ad-high-grade monitor runs every minute over the last two minutes of results. It opens one fleet-wide alert when the highest grade is above 0.7 and that row's confidence is above 0.7. It is a tripwire, not a per-host rule. Independently, the projector reads every result row past its bookmark, with a three-minute overlap for late rows. A row with grade above 0.5 is a firing signal. A row at or below 0.5 is evidence of recovery and is not stored as a signal. So an anomaly reaches an incident even when the monitor stays quiet. Architecture.

4. **An episode per entity.** Forty hosts breaching one detector are forty episodes, because an episode is one detector on one host. A host recovers after three windows at grade 0, and its episode resolves alone. An episode with no evaluation for six minutes is marked stale. The projector posts every open episode to Alertmanager every 30 seconds. Alertmanager groups them by alert name into one notification. The Cockpit folds them into one card by the same key. The per-host state lives in the episode documents. Architecture.

### Target design

Runtime steps 1 to 4 do not change. What changes is how the detectors and monitors are created.

- The detectors, the forecaster and the monitors become a task file of the OpenSearch role (atlas:200, 218; rework:7-14, 127-129). The shell scripts that search, compare, stop, update and start a detector become ansible.builtin.uri tasks in an include_tasks loop (rework:135-137; atlas:200). Architecture: the report says "created once at deploy time through the plugin's REST API".
- The wait for the poller's first samples is kept or deliberately dropped (rework:90-92, 129; atlas:200). Not decided. See walkthrough 9, step 3.
- The projector stays a role. Its cursor and identity code move into the shared loggy package (atlas:461; rework:138-141). The 0.5 floor, the 3-minute overlap and the per-entity episode rule do not change.
- Alertmanager stays as it is (atlas:434).
- Open: if the poller's cluster, node and Dashboards samples move to Telegraf (rework:142-144; atlas:191), the three metric detectors change their source index. The fourteen log detectors do not. Not decided.
- The monitor definitions are shipped as JSON. gen_monitors.py moves to tools/ (atlas:218; rework:122-123).

---

## Walkthrough 7. From a line to the template catalog

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
| --- | --- | --- | --- |
| 7.1 | Every record gets template_version and template_status before indexing | CONFIRMED | deploy/roles/loggy_collector/files/stamper.py:604-622 (status always set, version only when a template exists), atlas:145 and :151 for the forward loop (audited in walkthrough 1) |
| 7.1 | drain3 keeps at most 20000 clusters per worker, LRU eviction | CONFIRMED | stamper.py:40 (STAMPER_MAX_TEMPLATES 20000), deploy/roles/loggy_collector/defaults/main.yml:183, stamper.py:301-311 (an OrderedDict of recent clusters, moved to the end on every hit, evict while over the cap), stamper.py:276-281 (evict pops the oldest). The cap counts all families together (stamper.py:230, 306). |
| 7.1 | Messages longer than 4096 characters get no_template | CONFIRMED | stamper.py:41, 611-613; defaults/main.yml:184 |
| 7.2 | Publishes every 300 seconds | CONFIRMED | deploy/roles/loggy_collector/files/template_contract.py:43 (PUBLISH_INTERVAL_MS is 5 minutes), stamper.py:42-43, defaults/main.yml:187 (300), stamper.py:970-974 (the tick publishes when 300 seconds have passed) |
| 7.2 | One bucket document per family and window, counts per template_version | CONFIRMED | template_contract.py:298-333 (document: node, family, resolution_seconds, bucket_start, bucket_end, total, version_count, versions, counts), template_contract.py:12-13 (index prefixes template-buckets-5m and template-buckets-1h), stamper.py:854-872 |
| 7.2 | Upserts definitions (programs, hosts, sources, nodes) into the catalog | CONFIRMED | stamper.py:780-819 (a bulk update with a Painless script and an upsert body, scope: programs, origin_hosts, log_sources, nodes, widened_into, widened_from), template_contract.py:508-540, template_contract.py:9 (index template-catalog) |
| 7.2 | Writes a watermark | CONFIRMED | stamper.py:823-829, 869-872 |
| 7.3 | Hourly, the stamper re-counts its own local index | CONFIRMED | stamper.py:894-895 (runs inside publish), 899-945 (once per completed hour, a terms aggregation on template_version over application-logs-local-<node> for the previous hour, one check document per family into the catalog), stamper.py:30-31 (the local index name), defaults/main.yml:206 (enabled) |
| 7.3 | Hourly, the catalog maintenance checks shared indices against buckets | CONFIRMED | deploy/roles/loggy_template_catalog/defaults/main.yml:9-10 (hourly), templates/alice-catalog-maintenance.timer.j2:6, files/catalog_maintenance.py:48-51 (one completed hour, one hour behind, up to 5000 buckets), :341-360 (reads 1-hour buckets), :406-427 (indexed counts per node, family and hour), :430-447 (stamped against indexed), :465-513 (conservation first, then the shared indices application-logs-central and infologger, from :23-25 and defaults:26) |
| 7.3 | Writes check documents, then expires old definitions | Order WRONG | catalog_maintenance.py:573-626: expiry of the catalog runs first (:593), the query log expiry at most once a day (:37-39, :604-614), the checks last (:623). Definitions expire after 90 days (template_contract.py:53; catalog_maintenance.py:27-29), checks after 35 days (defaults:22), query rows after 365 days (defaults:17; template_contract.py:55). |
| 7.4 | template-new and template-count-check run hourly over the catalog | CONFIRMED | ad/files/monitors/template-new.json:7-12 (60 minutes), index template-catalog, fires on a kind template document first catalogued in the last hour, severity 4. template-count-check.json: 60 minutes, fires on a kind check document with ok false in the last 2 hours, severity 3. Both post to the in-cluster sink. |
| 7.4 | Shifter Templates page reads catalog, buckets, sample lines; labels in template-triage | CONFIRMED | deploy/roles/loggy_shifter_view/files/templates_view.py:25-36 (catalog, both bucket patterns, alice-incidents, the local index prefix, infologger and application-logs-central), :1181-1189 (firing episodes from alice-incidents), files/triage.py:19-20 (template-triage, shifter-queries), template_contract.py:10-11 |

### Corrected walkthrough text

The template lane answers "what is the machine actually saying", which no volume rule can.

1. **Stamped in band.** Every record passes through the stamper on the worker before it is indexed. The stamper sets a status on every record: matched, new or no_template. It sets a template version only when a template exists. An empty message, or one longer than 4096 characters, gets no_template and is indexed without a version. The stamper holds at most 20000 clusters per worker across all log families. When the cap is reached it drops the least recently used cluster. Architecture.

2. **Counts every 5 minutes, definitions merged.** Every 300 seconds the stamper publishes one bucket document per family and window: the total, the number of distinct versions, and a count per version. Five-minute buckets go to a per-day index. One-hour buckets go to a per-month index. In the same batch it upserts each changed template definition into the catalog. A script merges the programs, origin hosts, log sources and worker names it has seen into the definition that other workers built. A watermark document records how far this worker has published. Architecture.

3. **Two audits.** Once an hour the stamper counts the template versions in its own local index for the previous hour and writes a check document. The check says whether the index holds more records of a version than the stamper stamped. Once an hour, on the control host, the catalog maintenance runs. It first expires definitions older than 90 days and checks older than 35 days. Once a day it also expires query history older than 365 days. Then it reads up to 5000 one-hour buckets for the hour before last. For each bucket it checks that the per-version counts sum to the total. Then it counts the versions in infologger and application-logs-central for the same worker and hour and compares. It writes the failed checks and a report per section into the catalog. Architecture.

4. **Alertable and browsable.** Two rules run every hour over the catalog. template-new fires when a template was catalogued for the first time in the last hour, at severity 4. template-count-check fires when any check in the last two hours failed, at severity 3. The Shifter Templates page reads the catalog, the buckets, sample lines from the local and shared indices, and the firing episodes. It stores a person's labels and notes in template-triage. Architecture.

### Target design

- Step 1 does not change. The stamper stays inside the Fluent Bit role as the filter the collector calls. The masker and the contract move into the shared loggy package (atlas:155; rework:15-16, 138-141).
- Step 2: open whether the date-named bucket indices stay or a rollover alias is used (atlas:254). The stamper writes straight to OpenSearch. The live-lane bus of step 12 does not touch this lane (atlas:611, "Fluent Bit is not involved").
- Step 3: open whether the hourly maintenance becomes ISM policies plus monitors instead of a timer (atlas:263, 425; rework:12-14, 127-129). ISM can delete whole indices on age. It cannot delete single documents by their own age inside one index, which is what the definition and check expiry does today (catalog_maintenance.py:216-237, 296-313). So the decision depends on whether the catalog index is split by time. Not decided. If the timer goes, the two counting checks need a new home. Not decided. Flag for the report.
- Step 4: the two monitors become uri tasks in the OpenSearch role (rework:135-137). The shifter view is renamed and stays a separate Preact app. The Templates page stays there because a Dashboards plugin must be rebuilt per OpenSearch version (atlas:479; rework:17-21).

---

## Walkthrough 8. The projector itself dies

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
| --- | --- | --- | --- |
| 8.1 | Projector writes a kind projector document every cycle | CONFIRMED | sp:1163-1176 (kind projector with projector_cycle_ok, cycle time and counts), sp:1177-1179 (kind alertmanager with am_up), sp:30 (INTERVAL 30 seconds), deploy/roles/loggy_signal_projector/defaults/main.yml:7, templates/alice-signal-projector.service.j2:28 |
| 8.2 | signal-projector-stale fires when no projector document in 10 minutes | CONFIRMED, with a guard the atlas omits | ad/files/monitors/signal-projector-stale.json:7-12 (every minute), :16-18 (cockpit-metrics), :46 (recent means the last 10 minutes), :65 (fires when recent is empty). Guard: :32 and :65 return false when no projector document exists in the last 1440 minutes. So after 24 hours of silence the monitor stops firing. Code, and a limit for the report. |
| 8.2 | alertmanager-down works the same way from the alertmanager heartbeat | WRONG | ad/files/monitors/alertmanager-down.json:32 (last 5 minutes), :42-47 (max of am_up), :59 (fires when a heartbeat exists and the max am_up is 0). It fires when the projector reports that it cannot reach Alertmanager. It does not fire on absence: with no heartbeat at all it returns false (:59). So a dead projector silences alertmanager-down. |
| 8.2 | Both are severity 1 | CONFIRMED | signal-projector-stale.json:62, alertmanager-down.json:56 |
| 8.3 | Action uses a webhook straight to the receiver on port 8091 | CONFIRMED | ad/templates/monitors.sh.j2:19-20 (channel alice-breakglass-sink, http://127.0.0.1:8091/notifications), :217-218 (only the two dead-man monitors may use it), signal-projector-stale.json:72, alertmanager-down.json:66 (destination_id alice-breakglass-sink), deploy/group_vars/all.yml:234 (notification_ingest_port 8091) |
| 8.3 | Receiver stores it with delivery_path breakglass | CONFIRMED | deploy/roles/loggy_signal_projector/files/notification_ingest.py:194-198 (a payload without an alerts list is a break-glass document), :120-141 (delivery_path breakglass, receiver breakglass, group_key breakglass/<monitor>), :10-11 and :223 (bound to 127.0.0.1:8091), :204-213 (503 when the write fails). Throttle 30 minutes per monitor: signal-projector-stale.json:81-86. |
| 8.3 | Ordinary alerts resolve themselves in Alertmanager because nothing re-sends | CONFIRMED | deploy/roles/loggy_alertmanager/defaults/main.yml:18 (resolve_timeout 5m), templates/alertmanager.yml.j2:3, sp:40-41 (RESOLVE_TIMEOUT_SECONDS 300), sp:1316 (every open episode is re-sent each cycle), sp:1373-1378 (the projector refuses to start if the cycle is not well under the resolve timeout) |
| 8.4 | stop-projector stops the projector through the node-04 fault agent | CONFIRMED | deploy/roles/alice_ops/files/inject_run.py:61-69 (seven scenarios), :245-251 (POST /service-stop with name alice-signal-projector to the projector agent), deploy/roles/alice_ops/templates/alice-inject.service.j2:27 (agent address is the projector host on fault_agent_port), deploy/group_vars/all.yml:151 (port 8089), :93-94 (the projector host's agent may stop only alice-signal-projector), deploy/roles/faults/files/fault_agent.py:9-12, 71-79, deploy/inventory.yml:58-60 (projector group is node-04), deploy/playbooks/site.yml:321-325 (fault agents on workers and the projector host) |
| 8.4 | Requires a signal-projector-stale break-glass delivery to pass | CONFIRMED | deploy/roles/alice_ops/files/score_injection.py:29 (the two break-glass names), :231-243 (splits notifications by delivery_path, requires signal-projector-stale for stop-projector), :406-414 (fails on a missing or an unexpected break-glass delivery). Restore: inject_run.py:293-297 starts the projector and waits for a heartbeat with projector_cycle_ok (:256-270). |

### Corrected walkthrough text

The two rules that cannot page through the components they report dead take a separate door.

1. **The heartbeat stops.** Every 30 seconds, after a full cycle, the projector writes two documents into cockpit-metrics: kind projector, with whether the cycle succeeded, and kind alertmanager, with whether Alertmanager answered. The projector is stopped, by a fault, a crash or a bad deploy. Both documents stop. Architecture.

2. **signal-projector-stale.** The rule runs every minute. It fires when no kind projector document arrived in the last 10 minutes. It has a guard: it only fires while at least one projector document exists in the last 24 hours. After a day of silence it goes quiet. That guard is a limit to state. alertmanager-down is a different rule. It fires when the projector's last 5 minutes of heartbeats all say Alertmanager is down. It needs a live projector to say so. When the projector is dead, only signal-projector-stale fires. Both rules are severity 1. Architecture.

3. **Straight to the receiver.** Both rules post to a channel that no other rule may use: a webhook to the notification receiver on the control host, port 8091, on loopback. It bypasses the projector and Alertmanager, the two components it reports dead. The receiver stores the post as one document with delivery_path breakglass. A failed write answers 503. The same rule fires at most once per 30 minutes. Meanwhile every ordinary alert resolves itself in Alertmanager after 5 minutes, because the projector re-sends every open episode every 30 seconds and nothing else does. Architecture.

4. **How it is proven.** The stop-projector injection asks the fault agent on node-04 to stop the projector. That agent may stop nothing else. After the observation window it starts the projector again and waits for a heartbeat with a successful cycle. The scorer then reads the stored notifications. The run passes only if a signal-projector-stale notification with delivery_path breakglass exists and no other break-glass notification does. Tester tooling. Architecture for the report's evaluation section.

A related exception the atlas card mentions and the walkthrough does not: when the projector cannot reach OpenSearch for two cycles, it raises opensearch-unreachable straight to Alertmanager (sp:43-44, 1126-1160, 1345-1368). That is the only notice of a whole-cluster outage. Architecture.

### Target design

Steps 1 to 4 do not change at runtime.

- The two monitors and the break-glass channel become uri tasks in the OpenSearch role (rework:135-137; atlas:218).
- The receiver stays with the projector role and is packaged in step 9 (atlas:443).
- The live-lane bus of step 12 does not touch this path (rework:22-27).
- Two limits for the report, not for the rework list: the 24-hour guard in signal-projector-stale, and alertmanager-down going quiet when the projector is dead. Neither is in the agreed rework. Not decided.

---

## Walkthrough 9. What a deploy creates

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
| --- | --- | --- | --- |
| 9.1 | After all five nodes are up, the OpenSearch role's second mode runs | CONFIRMED | deploy/playbooks/site.yml:153-157 (bring-up on all five), :159-183 (one node at a time, wait for the HTTP API and a cluster status), :185-192 (the same role on the control host with opensearch_configure_cluster true), deploy/roles/loggy_opensearch/tasks/main.yml:15-19 |
| 9.1 | Runs templates.sh and ism.sh | CONFIRMED | deploy/roles/loggy_opensearch/tasks/configure_cluster.yml:56, 75 |
| 9.1 | Templates, pipeline, write aliases, pre-created indices, ISM policies, auto-create guard | CONFIRMED in kind, counts WRONG on the edge | templates.sh.j2:255-262 (auto-create guard), :268 (pipeline), :270-271 (two component templates), :273-287 (fifteen shared index templates) plus :296-299 (one per worker), :300 and :303-305 (write aliases: one per worker, infologger, application-logs-central, alice-alert-actions), :306-315 (ten pre-created indices), :289-290 (two cluster settings), :317-318 (two live mapping patches). ism.sh.j2:132-165 (eight policies), :167-172 (attached). On staging with two workers that is 17 index templates and 5 write aliases. The edge at atlas:531 says sixteen and four. |
| 9.1 | Bash today, uri in the target | CONFIRMED | rework:50-58, 132-134 |
| 9.2 | Dashboards and nginx come up, patterns created, cockpit imported | CONFIRMED, pattern count WRONG | site.yml:219-223 (order: Dashboards, ops, cockpit metrics, detection), deploy/roles/loggy_os_dashboards/tasks/cockpit.yml:25-31 (patterns.sh), defaults/main.yml:42-45 (three patterns: infologger, application-logs-local-*, application-logs-central), cockpit.yml:33-66 (import with overwrite, verified against the line count), files/cockpit.ndjson (59 objects: 37 visualisations, 15 searches, 6 index patterns, 1 dashboard). So nine index patterns exist after a deploy: three from the script, six inside the import. The card at atlas:376 says six. |
| 9.2 | Roster snapshot published, then the poller starts and reads it | CONFIRMED | deploy/roles/loggy_cockpit_metrics/tasks/main.yml:25-29, tasks/roster.yml:1-11 (publish), :17-47 (read back and assert it names every worker), files/roster_publish.py:53-57 (content hash), :73-78 (no write when unchanged), :94-97 (PUT _create) |
| 9.3 | Channels and monitors first, then wait, then detectors and forecaster | CONFIRMED | ad/tasks/main.yml:4-8, ad/templates/monitors.sh.j2:213-218 (two channels), ad/tasks/detection.yml:1-19 (count kind node and kind osd in cockpit-metrics, 20 retries of 6 seconds), :21-29 (detectors), :31-43 (forecaster), :68-80 (verify). Three detectors read cockpit-metrics: ingest-flow, node-health, dashboards-health (ad/files/detectors/*.json, indices field). |
| 9.4 | Receiver starts before the projector | CONFIRMED | site.yml:225-232 (receiver on the control host), :234-238 (projector on its own node) |
| 9.4 | Projector play gates on a fresh heartbeat with projector_cycle_ok | CONFIRMED | deploy/roles/loggy_signal_projector/tasks/main.yml:138-145 (records the gate start), :162-188 (newest kind projector document after the start with projector_cycle_ok 1), defaults/main.yml:1-2 (36 retries of 5 seconds, 180 seconds), site.yml:327-344 (a missed gate fails the deploy at the end, after everything else converged) |
| 9.4 | The rollup and the Shifter follow | CONFIRMED | site.yml:240-249 (signal-layer re-verify on the control host), :251-256 (rollup), :258-286 (shifter) |
| 9.4 | The collector role runs last on the workers | WRONG | site.yml:288-292 (collector), then :298-302 (catalog maintenance on the control host), :304-311 (post-collector gate), :313-319 (replay on the workers), :321-325 (fault agents on the workers and the projector host), :327-344 (final verdict) |
| 9.4 | A post-collector gate waits for a heartbeat from every rostered collector | CONFIRMED | deploy/roles/loggy_cockpit_metrics/tasks/post_collector.yml:1-25 (one kind fluentbit document per rostered collector in the last 5 minutes, 20 retries of 6 seconds), :27-45 (verify the detection layer again) |

### Corrected walkthrough text

Not a runtime flow, but the reason the shelves exist. The site playbook runs its plays in sequence, and the order is load-bearing.

1. **Cluster configuration from the control host.** The OpenSearch role runs first on all five machines and brings the cluster up. A second pass waits for each node's API and a cluster status, one node at a time. Then the same role runs once more, on the control host only, in its configuration mode. It sets the auto-create guard, so no log family can be created by a stray write. It creates the ingest pipeline, two component templates, fifteen shared index templates and one per worker. It creates the write aliases with their first backing index: one per worker, infologger, application-logs-central and the alert action sink. It pre-creates ten single indices. It creates eight retention policies and attaches them. Bash and curl today. Architecture.

2. **Dashboards, ops page, roster.** Dashboards and nginx come up. A script creates three index patterns. An import brings 59 saved objects, six of them index patterns, so nine patterns exist. The ops page starts. Then the roster snapshot is published, keyed by a hash of the worker list and the host assignments. Nothing is written when the hash is unchanged. Only then does the poller start, because it reads the roster to tell silence from absence. Architecture.

3. **Rules, then models.** Two notification channels and thirty monitors are created first. Then the play waits until cockpit-metrics holds a node sample and a Dashboards sample, for up to 120 seconds. Three detectors read that index. Only then are the seventeen detectors and the forecaster created, and the whole set verified. Any merge into the OpenSearch role must keep or deliberately drop this wait. Architecture.

4. **Receiver, projector, rollup, shifter, collectors, then the rest.** The receiver starts on the control host before the projector, so the webhook target exists. The projector starts on node-04. Its play waits up to 180 seconds for a heartbeat that reports a successful cycle. A missed gate fails the deploy at the very end, after every other play converged. The rollup and the shifter follow. Then the collector role runs on the workers. After it come the catalog maintenance, a gate that waits for a heartbeat from every rostered collector within 5 minutes, the replay engine on the workers, and the fault agents. Architecture.

Three plays exist today that only retire earlier units and files (site.yml:76-145, 261-283). They are migration code and go in the rework (rework:72-80, 120-121).

### Target design

This walkthrough changes the most, because the rework is about deploy shape.

- One merge request, atomic commits, each deployable on its own (rework:43-49, 109-112). The commit ladder presents the final design, not the history (rework:109-112).
- Step 1: the OpenSearch role gains a Dashboards mode and a detection task file (rework:7-14, 127-129). Templates, policies, the pipeline and the per-worker registration move from bash to ansible.builtin.uri. ISM updates need a GET for the sequence number first. Done when a second run reports zero changes (rework:132-134). The common role becomes a dependency of the OpenSearch role, and the duplicated kernel parameter goes (rework:130-131).
- Step 2: gen_cockpit.py and gen_monitors.py move to tools/, and the roles ship the generated JSON (rework:122-123; atlas:380). The index patterns and saved objects are created through uri (atlas:380).
- Step 3: the wait for the poller's samples is kept or deliberately dropped (rework:90-92, 129). If the cluster and node kinds move to Telegraf (rework:142-144), the wait changes its source. Not decided.
- Step 4: the collector role is renamed to say the technology, the shifter role likewise (rework:15-21, 124-126). The projector, receiver, rollup and shifter install one versioned loggy package by pip and run an entry point (rework:138-141, 182-190). The retire plays are deleted (rework:72-80, 120-121). The catalog maintenance timer stays or becomes ISM plus monitors (rework:127-129).
- Optional and last: a Kafka role for the live lane, reused from the supervisor's own merge request (rework:147-148). Two Kafka decisions stand side by side and must not be merged. The soak rejected a bus between the collector and OpenSearch on measured rates: a real worker peaks at 78 records a second and the whole farm at 9,781 (docs/SOAK_RESULTS.md:1852-1875). The September 2026 review separately agreed a bus for the live lane only, so one consumer moves instead of 200 collector configurations (rework:22-27, 205-207). The deploy of the target creates the bus for the live lane and nothing else. deploy/README.md:2329-2335 records why no queue was built on staging: the machines lack the memory, and the bulk tier must never cross the wire.

---

## Flags for the orchestrator

1. ad-high-grade is a fleet-wide tripwire, one alert for the whole fleet, not one per host (ad-high-grade.json:5, 20, 81). The atlas at line 68 reads as if it opened per-entity alerts. The per-entity path is the projector.
2. The projector's firing floor is 0.5 (sp:34, 343; group_vars/all.yml:208). The monitor's is 0.7. The atlas names only 0.7. The report should name both and say which one feeds incidents.
3. alertmanager-down does not fire on absence. It fires on am_up 0 in the last 5 minutes and needs a live projector (alertmanager-down.json:32-59). The atlas at line 85 says it works the same way as signal-projector-stale. It does not.
4. signal-projector-stale stops firing after 24 hours without any projector document (signal-projector-stale.json:32, 65). A limit for the report.
5. Counts on the deploy edge (atlas:531): 17 index templates and 5 write aliases on staging, not 16 and 4 (templates.sh.j2:273-305). Index patterns after a deploy: 9, not 6 (defaults/main.yml:42-45; cockpit.ndjson).
6. The collector play is not last on the workers. Replay and fault agents follow it (site.yml:313-325).
7. The catalog maintenance expires first and checks last (catalog_maintenance.py:593-623). The atlas at line 77 has the order reversed.
8. The "3 to 5 hours" training time is a README rule of thumb, not a measurement (deploy/README.md:1081). Say so in the report or drop the number.
9. Two Kafka decisions, both standing: soak rejection of a collector-to-OpenSearch bus (docs/SOAK_RESULTS.md:1852-1875) and the review's live-lane bus (rework:22-27). Walkthrough 9's target notes keep them apart.

## Open questions

1. If the hourly catalog maintenance becomes ISM policies plus monitors, where do the two counting checks run? ISM deletes indices by age, not single documents (catalog_maintenance.py:216-237). Not decided.
2. Do the bucket indices stay date-named (atlas:254)? Not decided.
3. Does the wait for the poller's first samples survive the merge into the OpenSearch role (rework:90-92, 129)? Not decided.
4. If the poller's cluster and node kinds move to Telegraf (rework:142-144), the three metric detectors, the disk-fill forecaster and the deploy wait change their source. Not decided.
5. Should the report name the 24-hour guard in signal-projector-stale and the projector dependence of alertmanager-down as limits? They are not in the agreed rework list.
