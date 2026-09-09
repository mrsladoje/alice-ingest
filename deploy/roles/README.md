# `deploy/roles`

Sixteen roles. Each one owns a single lifecycle: one package, one service,
one closed set of REST objects, or one shared file set. A role never installs a
thing it does not also configure, start and prove.

The table is the map. Every role has its own `README.md` with the reasoning,
the wiring diagram, the variables it reads and the couplings it carries.

## The roles

| Role | Runs on | What it does |
| --- | --- | --- |
| `common` | every VM | Prepares a bare Alma 9 host: swap file, the two kernel parameters OpenSearch needs, baseline packages, clock, firewalld. Runs first. |
| `sweet_opensearch` | every node, then control | Two modes, chosen by `opensearch_configure_cluster`. **Install:** puts one OpenSearch node on the machine and joins it to the `alice-logs` cluster — identity and tier, heap cap, HTTP and transport ports open to cluster members only, vendor RPM or podman container per `opensearch_install_method`. **Configure the cluster:** applies the cluster-wide state — ingest pipeline, component and index templates, cluster settings, pre-created indices, retention policies. |
| `alertmanager` | control | Installs Prometheus Alertmanager: severity-tiered grouping, one webhook receiver, inhibit rules generated from the repository's causal edges. Decides when a human is told. |
| `alice_runtime` | control, projector, background | Puts the shared runtime on every host that runs an `alice-*` service: the app root, the two imported Python modules, the two JSON catalogs. No service, no port. |
| `dashboards` | control | Installs OpenSearch Dashboards, caps its Node heap, puts nginx with TLS and basic authentication in front, and imports the index patterns and the Maintainer Cockpit saved objects. |
| `alice_ops` | control | Installs the operator control panel: the loopback HTTP server behind the replay button, plus the two one-shot units it starts on demand (fault injection, poison replay). |
| `sweet_cockpit_metrics` | control | Publishes the collector roster and runs the poller that fills `cockpit-metrics`. Every health panel, detector and absence monitor reads one of these two outputs. |
| `sweet_anomaly_detection` | control | Loads the detection layer into the cluster: two notification channels, 30 alerting monitors, 17 Random Cut Forest detectors, one disk-fill forecaster, and the verify gate that proves the set is complete. Upserts by name, so a re-run updates instead of duplicating. |
| `signal_projector` | projector (+ control) | Runs the projector that turns raw alerts, anomaly results and monitor output into named signals, incidents and lane state. Its notification receiver runs on the control host. |
| `sweet_trend_rollup` | background | Runs `alice-trend-rollup`, which turns raw log indices into 10-minute per-entity rows. Twelve monitors read those rows instead of a full day of raw logs. |
| `sweet_shifter_view` | shifter | Installs the shifter view: a single-file Python server, the query proxy, and a vendored Preact page whose live lane tails logs over Server-Sent Events. Keeps working while the cluster is red. |
| `sweet_collector` | workers | Installs `alice-stamper`, which stamps every record with its template identity, then Fluent Bit, which tails the local log tree, accepts InfoLogger over TCP, routes into three log families and hands every record through the stamper and back before writing to this VM's own OpenSearch node. One socket contract, one namespace. |
| `sweet_template_catalog` | control | The fleet-wide upkeep of what the stamping produces: definition expiry, query-history expiry and the two counting checks, as one oneshot unit on an hourly timer. A cluster API client — nothing it touches is worker-local. |
| `sweet_replay` | workers | Installs the S3-replay engine under a venv and systemd. Each VM replays only its own `epn_partition` slice; the wrapper narrows the preserved upstream `replay.py` to that slice. |
| `faults` | workers + projector | Installs the fault-injection agent the control host calls. A node may fault only the service it owns — a worker its Fluent Bit, the projector host its projector. |

## Deploy order

`playbooks/site.yml` runs the roles in the order of the table. The order is a
dependency chain, not a preference:

1. **Hosts, then the cluster.** `common` on every VM, then `sweet_opensearch`
   in install mode on every node in one play, so a storage-tier node can be elected cluster
   manager while the cluster comes up together. A rolling gate follows.
2. **Cluster state, then the things that read it.** `sweet_opensearch` again,
   in configure-the-cluster mode against `control`, creates the indices before
   any index pattern, monitor or detector names one.
3. **Data before detection.** `sweet_cockpit_metrics` must produce samples before
   `sweet_anomaly_detection` can train on them.
4. **Off the control host last.** The projector, the rollup and the live lane
   each run on their own VM, after the control-plane objects they normalize.
5. **Ingest last of all.** `sweet_collector` on every worker — the stamper's
   socket must exist before Fluent Bit starts, which is why the two are one
   role — then `sweet_template_catalog`, `sweet_replay` and `faults`. The firehose
   starts only once everything that reads it exists. The catalog maintenance
   follows collection because a pass reads bucket documents every worker
   publishes.
