# Brief: dataflow atlas walkthroughs 3, 4 and 5 checked against the code

Source under audit: reports/inputs/dataflow-atlas.md, lines 32 to 61. The atlas author audited walkthroughs 1 and 2 only. This brief audits walkthroughs 3, 4 and 5 step by step against deploy/.

Verdict in one sentence: the three walkthroughs hold on mechanism, direction, ports and almost every number; four claims are wrong and two are unverifiable.

The four wrong claims:

1. Walkthrough 4 step 4 gives the forecast alert entity_kind os_node. The disk-fill-forecast alert is fleet-scoped with entity_id all (deploy/roles/loggy_signal_projector/files/signal_catalog.json:68-71). Only disk-cliff-warn and disk-cliff-page carry entity_kind os_node (signal_catalog.json:40-53).
2. Walkthrough 5 step 4 says ten trend alerts arrive as one batch. Alertmanager groups on cluster_id and alertname (deploy/roles/loggy_alertmanager/templates/alertmanager.yml.j2:7). Ten alerts of one name arrive as one batch; ten different trend names arrive as ten batches.
3. Walkthrough 5 step 3 says log-family-silence "outranks any per-host alert". No inhibition rule exists: every causal edge carries proven false (deploy/roles/loggy_signal_projector/files/causal_edges.json, all 22 edges) and the rendered inhibit_rules block is empty (alertmanager.yml.j2:30-47). The per-host monitors defer by skipping a slice whose fleet_count is 0 (deploy/roles/loggy_anomaly_detection/files/monitors/trend-il-volume.json, trigger condition). That is a query-side design, not a rank.
4. Walkthrough 4 step 2 says warn fires "between 85 and 92 percent". The condition is above 85 and at most 92 (deploy/roles/loggy_anomaly_detection/files/monitors/disk-cliff-warn.json:79). A reading of exactly 85.0 fires nothing.

The two unverifiable claims:

1. Walkthrough 3 step 6, "roughly 2.5 minutes". This is a design estimate from deploy/README.md:1462, not a measurement. deploy/README.md:1511 states that zero injection runs have happened. docs/SOAK_RESULTS.md holds no kill-fluent-bit timing (grep for kill-fluent-bit, time-to-notify and alice-notifications found nothing). From the code constants the bound is 90 to 270 seconds (derivation below).
2. Walkthrough 3 step 1, "fluent-bit dies on node-01" as a real event. The scenario exists as injection tooling (deploy/roles/alice_ops/files/inject_run.py per atlas line 394) but no run is recorded.

Marking: [A] = architecture-level, belongs in the report. [C] = code-level, evidence only.

---

## Walkthrough 3. A dead Fluent Bit becomes a notification (atlas lines 32 to 42)

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
|---|---|---|---|
| intro (34) | Nothing observes fb_up 0; the platform reasons from absence | CONFIRMED | fb_health.py always writes fb_up 1 (deploy/roles/loggy_collector/files/fb_health.py:20-24); collector-down reads kind fleet, not fb_up (deploy/roles/loggy_anomaly_detection/files/monitors/collector-down.json:4, 25-27) |
| 1 (36) | Collector pushes kind fluentbit every 30 seconds | CONFIRMED | exec input interval collector_health_interval_seconds (deploy/roles/loggy_collector/templates/collector.yaml.j2:187-192); that resolves to cockpit_metrics_interval_seconds (deploy/roles/loggy_collector/defaults/main.yml:27), value 30 (deploy/group_vars/all.yml:156); kind fluentbit (fb_health.py:21) |
| 1 (36) | Pushes go into cockpit-metrics through an OpenSearch output | CONFIRMED | output match health, host localhost, index cockpit_metrics_index (collector.yaml.j2:639-648); cockpit_metrics_index is cockpit-metrics (deploy/group_vars/all.yml:155) |
| 1 (36) | Nothing on the control host notices by itself | CONFIRMED | poller never contacts a worker (deploy/roles/loggy_cockpit_metrics/files/metrics_poller.py:342-346 banner) |
| 2 (37) | Poller runs every 30 seconds | CONFIRMED | INTERVAL default 30 (metrics_poller.py:12); loop sleeps INTERVAL (metrics_poller.py:360) |
| 2 (37) | Roster says node-01 and node-02 should heartbeat | CONFIRMED | fleet_collector_node_ids is the node_id of every workers host (deploy/group_vars/all.yml:61); workers are node-01 and node-02 (deploy/inventory.yml:31-37); poller reads the newest roster (metrics_poller.py:114) |
| 2 (37) | Aggregates fluentbit samples of the last 90 seconds | CONFIRMED | HEARTBEAT_GRACE_SECONDS default 90 (metrics_poller.py:16-17); role default 90 (deploy/roles/loggy_cockpit_metrics/defaults/main.yml:9); query kind fluentbit, @timestamp gte now-90s, terms on collector_id (metrics_poller.py:93-105) |
| 2 (37) | One kind fleet document per rostered collector, heartbeat_missing 0 or 1 | CONFIRMED | metrics_poller.py:126-141 |
| 3 (38) | collector-down runs every minute | CONFIRMED | interval 1 MINUTES (collector-down.json:8-11) |
| 3 (38) | Bucketed on collector_id, triggers when max heartbeat_missing is 1 | CONFIRMED | composite source collector_id (collector-down.json:47-50); max heartbeat_missing (56-59); script params.max_missing == 1 (79); reads a 2-minute window (32) |
| 3 (38) | Writes an alert with bucket key node-01 into the alert index | CONFIRMED | monitor severity 1 bucket-level trigger (collector-down.json:70-72); projector reads .opendistro-alerting-alerts (deploy/roles/loggy_signal_projector/files/signal_projector.py:24) and takes the bucket key from agg_alert_content (signal_projector.py:196-200) |
| 3 (38) | Action posts a payload into alice-alert-actions, which nothing reads | CONFIRMED | destination alice-incluster-alert-sink, throttle 30 MINUTES (collector-down.json:86, 96-99); the sink URL is the index _doc endpoint (atlas card, deploy/roles/loggy_anomaly_detection/templates/monitors.sh.j2:17 per atlas line 334); no reader found (grep alice-alert-actions across deploy/roles returns writers only) |
| 4 (39) | Projector cycles every 30 seconds | CONFIRMED | signal_projector_interval_seconds 30 (deploy/roles/loggy_signal_projector/defaults/main.yml:7); INTERVAL default 30 (signal_projector.py:30) |
| 4 (39) | Catalog resolves collector-down to entity_kind collector, entity from bucket key | CONFIRMED | signal_catalog.json:12-18 |
| 4 (39) | Severity page because monitor severity is 1 | CONFIRMED | severity_of returns page for "1", else warn (signal_projector.py:171-172); applied at signal_projector.py:271 |
| 4 (39) | notification_scope collector:node-01 attached from the roster | CONFIRMED | topology_for checks entity_id against the roster snapshot's collectors (signal_projector.py:154-160); scope_label renders collector:<collector_id> (signal_projector.py:381-385); the roster snapshot is chosen by event time (signal_projector.py:141-145) and cached 60 seconds (113) |
| 4 (39) | Writes a signal row and opens an incident episode | CONFIRMED | signal document built at signal_projector.py:263-291; episode opened or reused in apply_monitor (signal_projector.py:779-816); page severity promotes the episode (798-799) |
| 5 (40) | Posts every active episode to Alertmanager with labels and annotations | CONFIRMED | labels_for (signal_projector.py:1035-1047); alertmanager_payload with incident_id, episode_id, diagnosis, operator_action, candidate_causes (1050-1083); POST /api/v2/alerts (1086-1091) |
| 5 (40) | Re-posts every 30 seconds | CONFIRMED | same INTERVAL loop (signal_projector.py:30, 1411); startup banner says re-sending every open episode (1373-1375) |
| 5 (40) | Alert resolves by itself after 5 minutes if the projector stops | CONFIRMED | alertmanager_resolve_timeout 5m (deploy/roles/loggy_alertmanager/defaults/main.yml:18) rendered at alertmanager.yml.j2:3; projector refuses to start if 2 × INTERVAL reaches the timeout (signal_projector.py:1376-1378) |
| 5 (40) | Alertmanager port 9093 on the control host | CONFIRMED | alertmanager_port 9093 (deploy/group_vars/all.yml:231); ALERTMANAGER_URL built from alertmanager_host_address (deploy/roles/loggy_signal_projector/templates/alice-signal-projector.service.j2:18); that address is the control host (deploy/group_vars/all.yml:77); only the projector host may reach the port (deploy/group_vars/all.yml:232-233; deploy/roles/loggy_alertmanager/tasks/main.yml:32-43) |
| 6 (41) | Page route: 30 seconds group wait, grouped on cluster_id, alertname, notification_scope | CONFIRMED | collector route group_by adds notification_scope (alertmanager.yml.j2:12-14); nested page route (16-19); alertmanager_page_group_wait 30s, page group interval 2m (deploy/roles/loggy_alertmanager/defaults/main.yml:22-23) |
| 6 (41) | Alertmanager posts the batch to the receiver on port 8091 | CONFIRMED | webhook url http://127.0.0.1:notification_ingest_port/notifications (alertmanager.yml.j2:50-54); notification_ingest_port 8091 (deploy/group_vars/all.yml:234) |
| 6 (41) | Receiver writes one alice-notifications document with delivery_path alertmanager and the episode id | CONFIRMED | payload with alerts becomes notification_docs (deploy/roles/loggy_signal_projector/files/notification_ingest.py:194-196); it harvests signal_ids and incident ids from annotations (59-70); one bulk write with a deterministic id (204-207); 503 on failure (208-213); dedupe window in deterministic_id (52-56) |
| 6 (41) | Roughly 2.5 minutes after the daemon died | UNVERIFIABLE | Design estimate, deploy/README.md:1462. Not measured: zero injection runs (deploy/README.md:1511); nothing in docs/SOAK_RESULTS.md. Code bound 90 to 270 seconds, see below |
| 6 (41) | No e-mail or pager is wired beyond that document | CONFIRMED | the only receiver is the webhook (alertmanager.yml.j2:49-54) |
| 7 (42) | Cockpit incident board draws one card per episode | CONFIRMED | index pattern alice-incidents (deploy/roles/loggy_os_dashboards/files/cockpit.ndjson:5); visualizations Incidents header, Episode summary, Open episodes (cockpit.ndjson:54-56); saved searches (19-21) |
| 7 (42) | Ops page headlines it | CONFIRMED | open_incidents counts state firing in alice-incidents (deploy/roles/alice_ops/files/ops_server.py:210-213); Open incidents metric (985-987) |
| 7 (42) | Shifter Active episodes panel lists it | CONFIRMED | firing episodes, newest first, 20 rows (deploy/roles/loggy_shifter_view/files/templates_view.py:51, 1181-1186); 30-second poll (templates_view.py:45; deploy/roles/loggy_shifter_view/files/live/templates.js:16); panel heading (templates.js:616-623) |

### Time bound, derived from the code constants [C]

The bound is 90 to 270 seconds from the last heartbeat to the stored notification. It is not measured.

- The last heartbeat lands 0 to 30 seconds before death (collector.yaml.j2:187-192; deploy/group_vars/all.yml:156).
- The poller flags a collector whose last heartbeat is older than 90 seconds (metrics_poller.py:16-17, 100-102). It ticks every 30 seconds (metrics_poller.py:12). First fleet document with heartbeat_missing 1: 60 to 150 seconds after death.
- collector-down runs every 60 seconds (collector-down.json:8-11): add 0 to 60 seconds.
- The projector cycles every 30 seconds (signal_projector.py:30): add 0 to 30 seconds.
- Alertmanager waits 30 seconds before the first page notification (deploy/roles/loggy_alertmanager/defaults/main.yml:22): add 30 seconds.
- Sum: 90 seconds at best, 270 seconds at worst. The README's 2.5 minutes sits inside this range.

### Corrected walkthrough text

A dead collector is silent. The platform reasons from absence, not from a failure sample [A].

1. Heartbeats stop. While alive, each collector pushes one health document with kind fluentbit into cockpit-metrics every 30 seconds (collector.yaml.j2:187-192, 639-648; deploy/group_vars/all.yml:155-156). The document always says fb_up 1 (fb_health.py:23). When Fluent Bit dies on node-01, the pushes stop. Nothing on the control host notices by itself [A].
2. The poller derives absence. Every 30 seconds (metrics_poller.py:12) the poller reads the newest roster snapshot, which lists node-01 and node-02 (deploy/group_vars/all.yml:61; deploy/inventory.yml:31-37). It counts the fluentbit documents of the last 90 seconds per collector_id (metrics_poller.py:16-17, 93-105). It writes one kind fleet document per rostered collector with heartbeat_missing 0 or 1 (metrics_poller.py:126-141). For node-01 the value is now 1 [A].
3. collector-down fires. The monitor runs every minute (collector-down.json:8-11). It reads fleet documents from the last 2 minutes (collector-down.json:32), buckets on collector_id (47-50), and triggers when max heartbeat_missing equals 1 (79). The alert carries severity 1 (72). The plugin writes the alert with the bucket key node-01 into .opendistro-alerting-alerts (signal_projector.py:24, 196-200). The action posts a payload into the in-cluster sink index, throttled to once per 30 minutes (collector-down.json:86, 96-99). Nothing reads that index [A for the alert, C for the sink].
4. The projector makes it a signal and an incident. Within 30 seconds (signal_projector.py:30) the projector scans the live alerts. It resolves collector-down through its catalog to entity_kind collector with the bucket key as entity_id (signal_catalog.json:12-18). Severity 1 becomes page (signal_projector.py:171-172). It confirms node-01 against the roster snapshot effective at the alert's start time (signal_projector.py:141-160) and labels the signal notification_scope collector:node-01 (381-385). It writes the signal row (263-291) and opens an incident episode (779-816) [A].
5. Told to Alertmanager, again and again. The projector posts every active episode to Alertmanager on the control host, port 9093 (deploy/group_vars/all.yml:77, 231; alice-signal-projector.service.j2:18), with labels and annotations (signal_projector.py:1035-1083). It re-posts every 30 seconds (signal_projector.py:1411). Alertmanager forgets an alert 5 minutes after the last post (deploy/roles/loggy_alertmanager/defaults/main.yml:18). Stop the projector and the alert resolves on its own after those 5 minutes [A].
6. Grouped, then stored. The alert matches the collector route, which groups on cluster_id, alertname and notification_scope (alertmanager.yml.j2:12-14). Severity page selects the fast timers: 30 seconds before the first notification, 2 minutes between notifications of an open group (alertmanager.yml.j2:16-19; defaults 22-23). Alertmanager posts the batch to the receiver on 127.0.0.1 port 8091 (alertmanager.yml.j2:52; deploy/group_vars/all.yml:234). The receiver writes one alice-notifications document tagged delivery_path alertmanager, carrying the signal and incident ids from the annotations (notification_ingest.py:59-70, 194-207). The design estimate is about 2.5 minutes from death to stored notification (deploy/README.md:1462). The code constants bound it between 90 and 270 seconds. No run has measured it (deploy/README.md:1511). No e-mail or pager exists beyond that document (alertmanager.yml.j2:49-54) [A].
7. Where a person sees it. The Cockpit reads alice-incidents and draws the episode (cockpit.ndjson:5, 54-56). The ops page counts firing incidents (ops_server.py:210-213, 985-987). The Shifter's Active episodes panel lists up to 20 firing episodes and refreshes every 30 seconds (templates_view.py:45, 51, 1181-1186; templates.js:16, 616-623) [A].

### Target design after the September 2026 rework

The chain keeps every mechanism. The rework moves ownership and packaging; it does not change a number in this walkthrough.

- Steps 1 and 2 [A]: open question, step 10 of the fix pass (reports/loggy-report/briefs/inputs/rework-context.md, section 6 item 10; atlas lines 164, 281, 416). The cluster, index, node and osd samples overlap the Telegraf estate. The fluentbit heartbeat, the roster and the absence logic stay whichever way that question closes (rework-context.md, section 2 item 5; atlas line 290). The collector role becomes loggy_fluentbit (rework-context.md, section 2 item 2). The report should describe the push-and-roster model as final.
- Step 3 [A]: the monitor keeps its query, cadence and threshold. Its creation moves into the OpenSearch role as a task file, through uri tasks, with the JSON shipped instead of generated (rework-context.md, section 6 items 3, 5, 8; atlas line 218). The sink index alice-alert-actions is a deletion candidate (atlas line 335). If it goes, the action target changes and the alert index remains the only hand-off.
- Steps 4 and 5 [A]: the projector stays a role. Its cursor and identity code move into the shared loggy package (rework-context.md, section 6 item 9; atlas line 461). Cadence and resolve timeout do not change.
- Step 6 [A]: Alertmanager stays as is (atlas line 434). The receiver stays with the projector role (atlas line 443). Timers do not change.
- Step 7 [A]: Dashboards becomes a mode of the OpenSearch role (atlas line 380). The Shifter role becomes loggy_shifter_ui and keeps the Templates page with its episodes panel (rework-context.md, section 2 item 3). The live-lane bus (fix-pass step 12) does not touch this chain: it carries log records to the Shifter, not alerts.

Unchanged steps: all seven. Changed: where the code lives and how Ansible creates the monitor.

---

## Walkthrough 4. A storage node's disk fills (atlas lines 44 to 51)

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
|---|---|---|---|
| intro (46) | No logs involved, only the poller and the plugin jobs | CONFIRMED | both monitors and the forecaster read cockpit-metrics kind node (disk-cliff-warn.json:16-27; disk-cliff-page.json:16-27; deploy/roles/loggy_anomaly_detection/files/forecasters/disk-fill.json:5-6, 42-45) |
| 1 (48) | Poller reads _nodes/stats fs on localhost every 30 seconds | CONFIRMED | GET _nodes/stats/jvm,os,fs,indices (metrics_poller.py:219); OS_URL default http://localhost:9200 (metrics_poller.py:8); interval 30 (12) |
| 1 (48) | One kind node document per node with disk_used_percent, heap, CPU, keyed by os_node | CONFIRMED | metrics_poller.py:224-247; disk_used_percent is (total minus available) over total of the node's whole filesystem, rounded to one decimal (226-229) |
| 2 (49) | disk-cliff-warn fires between 85 and 92 percent, severity 2 | WRONG (boundary) | condition is params.max_disk > 85 && params.max_disk <= 92 (disk-cliff-warn.json:79): above 85, up to and including 92; severity 2 (72); every minute (8-11); 2-minute window (32) |
| 2 (49) | disk-cliff-page fires above 92, severity 1 | CONFIRMED | params.max_disk > 92 (disk-cliff-page.json:79); severity 1 (72); every minute (8-11) |
| 2 (49) | Both bucketed on os_node | CONFIRMED | composite source os_node (disk-cliff-warn.json:47-50; disk-cliff-page.json:47-50); max of disk_used_percent (56-59 in both) |
| 3 (50) | Forecaster runs once an hour | CONFIRMED | forecast_interval 60 Minutes (disk-fill.json:21-25) |
| 3 (50) | Reads max disk_used_percent per os_node from kind node | CONFIRMED | feature max disk_used_percent (disk-fill.json:10-17); category_field os_node (36-38); filter kind node (39-48) |
| 3 (50) | 168 hours of history, 24 hours forward | CONFIRMED | history 168 and horizon 24 (disk-fill.json:33-34); both count forecast intervals of 60 minutes (21-25), so 168 hours and 24 hours |
| 3 (50) | disk-fill-forecast fires when the prediction crosses 85 percent | CONFIRMED | diskThreshold 85.0, peak > diskThreshold (deploy/roles/loggy_anomaly_detection/files/monitors/disk-fill-forecast.json:68); reads opensearch-forecast-results* (17); results of the last hour only, real-time only (32-46); runs every 10 minutes (8-11); severity 2 (65) |
| 4 (51) | Projector projects the alert with entity_kind os_node | WRONG for the forecast alert | disk-cliff-warn and disk-cliff-page: entity_kind os_node from the bucket key (signal_catalog.json:40-53). disk-fill-forecast: entity_kind fleet, entity_id all (signal_catalog.json:68-74; disk-fill-forecast.json:81). The monitor description explains why: the forecast result stores its entity as a nested field, so the monitor cannot key per node (disk-fill-forecast.json:4) |
| 4 (51) | A warn waits 5 minutes; a page 30 seconds | CONFIRMED | alertmanager_group_wait 5m, alertmanager_page_group_wait 30s (deploy/roles/loggy_alertmanager/defaults/main.yml:19, 22); page route at alertmanager.yml.j2:20-23; these alerts carry notification_scope fleet (signal_catalog.json:44, 51, 73), so they group on cluster_id and alertname only (alertmanager.yml.j2:7) |
| 4 (51) | Data loss and disk pressure are not inhibition sources, direction can run either way | CONFIRMED with a precision | deploy/README.md:1516-1519 names cluster-red and data-loss as the two deliberate non-suppressors; disk pressure is the argument (it can produce cluster-red). In causal_edges.json the only causes are collector-down, telemetry-silence and fleet-fb-silence (causal_edges.json:4-16); data-loss and disk-cliff appear only as symptoms (34, 161, 171, 231). Today no inhibition applies at all: every edge is proven false, so inhibit_rules renders empty (alertmanager.yml.j2:30-47; deploy/README.md:1507-1509) |

### Corrected walkthrough text

This is the metric path. No log is read; the poller samples the cluster and the plugin jobs read those samples [A].

1. Sampled every 30 seconds. The poller on the control host reads _nodes/stats on localhost (metrics_poller.py:8, 219) and writes one kind node document per OpenSearch node into cockpit-metrics (224-247). The document carries disk_used_percent, meaning used bytes over total bytes of the node's whole filesystem, plus heap percent and CPU percent, keyed by os_node (226-235) [A].
2. Two thresholds. Every minute, disk-cliff-warn fires when the node's maximum disk_used_percent over the last 2 minutes is above 85 and at most 92 (disk-cliff-warn.json:8-11, 32, 79). That is severity 2, the warn tier (72). disk-cliff-page fires above 92, severity 1, the page tier (disk-cliff-page.json:72, 79). Both bucket on os_node, so the alert names the node (47-50 in both) [A].
3. And a forecast a day ahead. Once an hour the disk-fill forecaster reads the maximum disk_used_percent per os_node (disk-fill.json:10-17, 21-25, 36-38). It trains on 168 hourly points, one week, and predicts 24 hourly points ahead, one day (33-34). Every 10 minutes the disk-fill-forecast monitor reads the forecast results of the last hour and fires when the highest predicted value is above 85 percent (disk-fill-forecast.json:8-11, 32-38, 68). That alert is fleet-scoped: it says a node will cross 85, not which one (disk-fill-forecast.json:4, 81) [A].
4. Same tail as every alert. The projector projects disk-cliff-warn and disk-cliff-page with entity_kind os_node and the node name (signal_catalog.json:40-53). It projects disk-fill-forecast with entity_kind fleet and entity_id all (68-74). All three carry notification_scope fleet, so Alertmanager groups them on cluster_id and alertname (alertmanager.yml.j2:7). A warn waits 5 minutes before the first notification; a page waits 30 seconds (defaults 19, 22). cluster-red and data-loss are deliberately not suppressors: data-loss is usually an impact, and disk pressure can produce cluster-red, so a rule could point the wrong way (deploy/README.md:1516-1519). Today no inhibition rule is active at all (alertmanager.yml.j2:30-47) [A].

### Target design after the September 2026 rework

- Step 1 [A]: open, step 10 (rework-context.md, section 6 item 10). The kind node sample duplicates what a Telegraf OpenSearch input reads (atlas line 191, 281). If Telegraf takes it, the monitors and the forecaster must read Telegraf's index instead. The atlas records no decision. The report should present the poller as the current source and name the overlap.
- Steps 2 and 3 [A]: monitors and the forecaster keep their thresholds, cadences, history and horizon. Their creation moves into the OpenSearch role as a task file with uri tasks and an include_tasks loop for stop, compare, update, start (rework-context.md, section 6 items 5 and 8; atlas lines 200, 209, 218). The wait-for-samples ordering before detector creation is kept or deliberately dropped (rework-context.md, section 3; atlas line 200).
- Step 4 [A]: unchanged. Alertmanager stays as is (atlas line 434); the projector stays a role (atlas line 461).

Unchanged steps: 2, 3, 4 in behaviour. Open: step 1's source of the node sample.

---

## Walkthrough 5. An EPN goes quiet, or loud (atlas lines 53 to 60)

### Verdict table

| Step | Claim in ten words | Verdict | Evidence |
|---|---|---|---|
| intro (55) | Trend lane never scans raw logs at alert time | CONFIRMED | every trend monitor reads the index trend-rollup only (trend-il-volume.json indices; same for the other eight, checked by script over deploy/roles/loggy_anomaly_detection/files/monitors/trend-*.json) |
| 1 (57) | Rollup runs on node-04 | CONFIRMED | background group is alice-ingest-4, node_id node-04, background_services rollup (deploy/inventory.yml:46, 62-65); site.yml runs loggy_trend_rollup on background (deploy/playbooks/site.yml:251-255) |
| 1 (57) | Every 10 minutes | CONFIRMED | BUCKET_SECONDS 600, INTERVAL defaults to BUCKET_SECONDS (deploy/roles/loggy_trend_rollup/files/trend_rollup.py:10, 15); role default trend_rollup_bucket_seconds 600 (deploy/roles/loggy_trend_rollup/defaults/main.yml:3) |
| 1 (57) | One composite aggregation per cohort over the last three closed buckets | CONFIRMED | five cohorts (trend_rollup.py:22-53); composite on the entity field over collector_time (117-128); BACKFILL_BUCKETS 3 (12); loop rolls the three buckets oldest first after a 120-second settle (11, 490-498) |
| 1 (57) | Documents and errors per origin_host and per collector node, plus p95 lags and fleet totals | CONFIRMED | cohorts by origin_host and by node (trend_rollup.py:22-53); error filter on severity_norm error or fatal (20, 147-151); p95 of enter_system_lag_ms and ingest_lag_ms (152-164); fleet_count and fleet_ef_count summed per cohort (298-300, 310-311). Note: atlas step 1 lists infologger, central and local as the three sources. Central is rolled by host only, not by node (trend_rollup.py:34-39) |
| 2 (58) | One row per family, entity and bucket, deterministic id | CONFIRMED | doc_id family.entity_kind.entity.start_ms (trend_rollup.py:322-323); the row fields (303-316) |
| 2 (58) | Zero rows only for hosts that logged in the last 24 hours | CONFIRMED | SILENCE_MEMORY_SECONDS 86400 (trend_rollup.py:18); recent_entities requires doc_count above 0 inside that window (212-244); impute_silent writes doc_count 0 rows for the absent ones (247-284) |
| 2 (58) | A _commit row closes the bucket | CONFIRMED | commit_docs with complete flag and the cohort list (trend_rollup.py:361-383); written only after entity rows, _meta rows and a refresh succeeded (447-473) |
| 3 (59) | Nine trend rules every 10 minutes | CONFIRMED | eleven trend-*.json files; trend-entity-cap and trend-rollup-stale are lane self-checks, the other nine are comparisons; all schedule 10 MINUTES (script over the files; e.g. trend-il-volume.json schedule) |
| 3 (59) | Three fresh slices against a 7-day baseline, falling back to 24 hours | CONFIRMED | slice0, slice1, slice2 cover period_end minus 40 to minus 10 minutes; baseline_7d from minus 7 days to minus 40 minutes; baseline_24h fallback when the 7-day fleet sum is 0 (trend-il-volume.json query and trigger); all nine carry baseline_7d, baseline_24h and three slices (script check). The freshest slice ends 10 minutes before the run, not at it |
| 3 (59) | Fires on share, not raw count; rise 2x, collapse 0.5x, 50 records floor, 6 baseline buckets | CONFIRMED (added detail) | minDocs 50.0, minBaselineBuckets 6.0, ratios 2.0 and 0.5 across all three slices (trend-il-volume.json trigger); severity 2 |
| 3 (59) | log-family-silence watches the _meta rows | CONFIRMED | family _meta, cohort_kind host (log-family-silence.json query); _meta rows written per cohort (trend_rollup.py:331-359) |
| 3 (59) | Fires when a family averaging 50 records per bucket is at zero | CONFIRMED (added detail) | rdocs 0 over the last 40 minutes, at least 6 rows in 24 hours, docs over rows at least 50 (log-family-silence.json trigger); severity 1, page |
| 3 (59) | It outranks any per-host alert | WRONG | no inhibition exists: all edges proven false (causal_edges.json), inhibit_rules empty (alertmanager.yml.j2:30-47). The per-host monitors skip a slice whose fleet_count is 0 (trend-il-volume.json trigger, f0 <= 0 returns false; description). log-family-silence is not even a cause in causal_edges.json (4-16). Correct wording: the per-host rules stay silent by design when the whole family is at zero |
| 3 (59) | trend-rollup-stale pages if no commit lands for 40 minutes | CONFIRMED | family _commit, complete true, none in the last 40 minutes, with at least one in 24 hours (trend-rollup-stale.json query and trigger); severity 1 |
| 4 (60) | Trend alerts are severity 2, warn, 5 minutes group wait | CONFIRMED | severity 2 on all nine (script check); alertmanager_group_wait 5m, group_interval 10m (deploy/roles/loggy_alertmanager/defaults/main.yml:19-20) |
| 4 (60) | A storm of ten trend alerts arrives as one batch | WRONG | group_by cluster_id, alertname (alertmanager.yml.j2:7). Seven trend monitors carry notification_scope fleet; trend-il-shipping-lag and trend-local-shipping-lag carry scope collector (script check; signal_catalog.json:181, 221) and group further on notification_scope (alertmanager.yml.j2:12-14). One batch holds the alerts of one name; ten hosts under trend-il-volume are one batch, ten different rule names are ten batches. deploy/README.md:1459-1461 states the opposite intent, one group not ten, so the README and the rendered route disagree; the route is what runs |

### Corrected walkthrough text

The trend lane never scans raw logs at alert time. It compares small pre-aggregated rows against a week of history [A].

1. Rolled up every 10 minutes. The rollup service on node-04 (deploy/inventory.yml:62-65; site.yml:251-255) runs every 600 seconds (trend_rollup.py:10, 15). Each run re-rolls the last three closed 10-minute buckets, oldest first, after a 120-second settle (11-12, 490-498). It runs one composite aggregation on collector_time per cohort (117-128). The five cohorts are infologger by origin_host, infologger by collector node, central by origin_host, local by origin_host and local by collector node (22-53). Each aggregation counts documents, counts error and fatal records, and takes the 95th percentile of the entry lag and the shipping lag (147-164). The fleet total per cohort is the sum over its entities (298-300) [A].
2. Small rows, deterministic ids. One row per family, entity and bucket, with the id family.entity_kind.entity.bucket_start (trend_rollup.py:303-323). A host that logged in the last 24 hours but not in this bucket gets a row with doc_count 0 (18, 212-284), so its share of the fleet is a real zero. A host silent for more than 24 hours gets no row and stops looking silent. A _meta row per cohort carries the fleet totals (332-359). A _commit row closes the bucket only after every entity row, every _meta row and a refresh succeeded (361-383, 447-473) [A].
3. Nine trend rules, every 10 minutes. The nine comparison rules run every 10 minutes and read trend-rollup only. Each compares three 10-minute slices, ending 40, 30 and 20 minutes before the run, against the entity's baseline over the previous 7 days; when the 7-day baseline is empty it uses 24 hours (trend-il-volume.json query). The volume rules compare the entity's share of the fleet, so a fleet-wide ramp cancels out; they fire on a rise to 2 times the baseline share or a collapse to 0.5 times, in all three slices, with a floor of 50 records per slice and at least 6 baseline buckets (trend-il-volume.json trigger). When the whole family is at zero the share is undefined and the per-host rule stays silent (trend-il-volume.json trigger, f0 <= 0). log-family-silence owns that case: it reads the _meta rows and pages when a family that averaged at least 50 records per bucket over 24 hours has written nothing for 40 minutes (log-family-silence.json). trend-rollup-stale pages when no complete _commit row landed for 40 minutes, given one landed in the last 24 hours (trend-rollup-stale.json) [A].
4. Warn tier, batched. Trend alerts are severity 2, so the projector labels them warn (signal_projector.py:171-172). Alertmanager waits 5 minutes before the first notification and 10 minutes between notifications of an open group (defaults 19-20). It groups on cluster_id and alertname (alertmanager.yml.j2:7), so ten hosts firing the same rule arrive as one batch, and ten different rules arrive as ten batches. The shipping-lag rules are collector-scoped and also group on notification_scope (alertmanager.yml.j2:12-14; signal_catalog.json:181, 221). Which messages changed is the template lane's question [A].

### Target design after the September 2026 rework

- Steps 1 and 2 [A]: the rollup stays a role; only its shared cursor code moves into the loggy package (atlas line 452; rework-context.md, section 6 item 9). The trend-rollup index is unchanged (atlas line 299). trend_rollup_backfill_buckets 3 is normal streaming, not back-compat (rework-context.md, section 7).
- Step 3 [A]: the monitors keep their queries and thresholds. Their creation moves into the OpenSearch role as a task file with uri tasks; the JSON is shipped, not generated at deploy time (rework-context.md, section 6 items 3, 5, 8; atlas line 218).
- Step 4 [A]: unchanged. Alertmanager stays as is (atlas line 434).
- The live-lane bus (fix-pass step 12) is not on this path. It carries infologger and central records from Fluent Bit to the Shifter (atlas lines 146, 479, 517). The trend lane reads the indices, not the bus.

Unchanged steps: all four in behaviour. Changed: where the monitor JSON lives and how Ansible creates it.

---

## Facts the report must carry [A]

- The dead-collector chain: heartbeat every 30 seconds, 90-second grace, monitor every minute, projector every 30 seconds, page group wait 30 seconds, resolve timeout 5 minutes. Evidence: deploy/group_vars/all.yml:156; metrics_poller.py:16-17; collector-down.json:8-11; signal_projector.py:30; deploy/roles/loggy_alertmanager/defaults/main.yml:18, 22.
- The time from death to stored notification is bounded at 90 to 270 seconds by those constants and is not measured (deploy/README.md:1462, 1511).
- Disk thresholds: warn above 85 up to 92, page above 92, forecast alert at a predicted 85, one week of hourly history, one day ahead (disk-cliff-warn.json:79; disk-cliff-page.json:79; disk-fill-forecast.json:68; disk-fill.json:21-34).
- The forecast alert names no node (disk-fill-forecast.json:4; signal_catalog.json:68-74).
- Inhibition is designed but off: all 22 edges are unproven and the rendered block is empty (causal_edges.json; alertmanager.yml.j2:30-47; deploy/README.md:1507-1511).
- Trend rules: 10-minute cadence, three slices ending 10 minutes before the run, 7-day baseline with 24-hour fallback, share-based ratios 2.0 and 0.5, floor 50 records, 6 baseline buckets (trend-il-volume.json). Silence page at 40 minutes for a family or for the rollup commit (log-family-silence.json; trend-rollup-stale.json).
- Zero imputation memory is 24 hours (trend_rollup.py:18).
- Alertmanager batches per alert name, per collector for collector-scoped alerts (alertmanager.yml.j2:7, 12-14).

## Code-level facts, evidence only [C]

- collector-down reads a 2-minute window of fleet documents (collector-down.json:32); disk-cliff monitors likewise (disk-cliff-warn.json:32).
- The projector's roster cache lives 60 seconds (signal_projector.py:113).
- The receiver's dedupe id is a hash plus a time window (notification_ingest.py:52-56).
- The forecast monitor restricts itself to real-time results by excluding task_id (disk-fill-forecast.json:41-46).
- The rollup's entity cap is 2000 per cohort; over-cap buckets cannot commit complete (trend_rollup.py:14, 262-269).
- Central is rolled by host only; infologger and local by host and by node (trend_rollup.py:22-53).

## Open questions for the orchestrator

1. Should the report state the 90-to-270-second bound, the 2.5-minute design estimate, or both, given no measurement exists?
2. Step 10 of the fix pass is open: if Telegraf supplies the node samples, walkthrough 4 step 1 changes source. The atlas records no decision.
3. The sink index alice-alert-actions is a deletion candidate (atlas line 335). Walkthrough 3 step 3 loses its last clause if it goes.
4. Should the report mention that inhibition is designed and gated off, or omit inhibition entirely?
