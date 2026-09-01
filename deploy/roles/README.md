# `deploy/roles`

Eighteen roles. Each one owns a single lifecycle: one package, one service,
one closed set of REST objects, or one shared file set. A role never installs a
thing it does not also configure, start and prove.

The table is the map. Every role has its own `README.md` with the reasoning,
the wiring diagram, the variables it reads and the couplings it carries.

## The roles

| Role | Runs on | What it does |
| --- | --- | --- |
| `common` | every VM | Prepares a bare Alma 9 host: swap file, the two kernel parameters OpenSearch needs, baseline packages, clock, firewalld. Runs first. |
| `container_host` | machines carrying more than one node | Installs podman and proves it is new enough to read quadlet unit files. Runs once per machine, not once per node. The kernel parameters stay with `common`. |
| `opensearch` | every node | Installs one OpenSearch node and joins it to the `alice-logs` cluster. Writes the node identity and tier, caps the heap, opens its HTTP and transport ports to cluster members only. Installs from the vendor RPM or as a podman container, chosen by `opensearch_install_method`. |
| `opensearch_bootstrap` | control | Applies the cluster-wide state that must exist exactly once: ingest pipeline, component and index templates, cluster settings, pre-created indices, retention policies. |
| `opensearch_local_index_registration` | control + workers | Installs `register_node.sh`, the one definition of a worker's local index template, retention attachment and write alias. Installed by two callers; starts nothing itself. |
| `alertmanager` | control | Installs Prometheus Alertmanager: severity-tiered grouping, one webhook receiver, inhibit rules generated from the repository's causal edges. Decides when a human is told. |
| `alice_runtime` | control, projector, background | Puts the shared runtime on every host that runs an `alice-*` service: the app root, the two imported Python modules, the two JSON catalogs. No service, no port. |
| `dashboards` | control | Installs OpenSearch Dashboards, caps its Node heap, puts nginx with TLS and basic authentication in front, and imports the index patterns and the Maintainer Cockpit saved objects. |
| `alice_ops` | control | Installs the operator control panel: the loopback HTTP server behind the replay button, plus the two one-shot units it starts on demand (fault injection, poison replay). |
| `alerting_monitors` | control | Provisions the OpenSearch Alerting layer: two notification channels, one cluster setting, 28 monitors. Upserts by name, so a re-run updates instead of duplicating. |
| `cockpit_metrics` | control | Publishes the collector roster and runs the poller that fills `cockpit-metrics`. Every health panel, detector and absence monitor reads one of these two outputs. |
| `anomaly_detection` | control | Provisions the machine-learning layer: 17 Random Cut Forest detectors, 1 disk-fill forecaster, and the script that proves the detection layer is complete. |
| `signal_projector` | projector (+ control) | Runs the projector that turns raw alerts, anomaly results and monitor output into named signals, incidents and lane state. Its notification receiver runs on the control host. |
| `trend_rollup` | background | Runs `alice-trend-rollup`, which turns raw log indices into 10-minute per-entity rows. Twelve monitors read those rows instead of a full day of raw logs. |
| `shifter` | shifter | Installs the shifter view: a single-file Python server, the query proxy, and a vendored Preact page whose live lane tails logs over Server-Sent Events. Keeps working while the cluster is red. |
| `collector` | workers | Installs and configures Fluent Bit. Tails the local log tree, accepts InfoLogger over TCP, parses and routes into three log families, writes to this VM's own OpenSearch node. |
| `producer` | workers | Installs the S3-replay engine under a venv and systemd. Each VM replays only its own `epn_partition` slice; the wrapper narrows the preserved upstream `replay.py` to that slice. |
| `faults` | workers + projector | Installs the fault-injection agent the control host calls. A node may fault only the service it owns — a worker its Fluent Bit, the projector host its projector. |

## Deploy order

`playbooks/site.yml` runs the roles in the order of the table. The order is a
dependency chain, not a preference:

1. **Hosts, then the cluster.** `common` on every VM, `container_host` on any
   machine that carries several nodes, then `opensearch` on every node in one play, so a storage-tier node can be elected cluster manager
   while the cluster comes up together. A rolling gate follows.
2. **Cluster state, then the things that read it.** `opensearch_bootstrap`
   creates the indices before any index pattern, monitor or detector names one.
3. **Data before detection.** `cockpit_metrics` must produce samples before
   `anomaly_detection` can train on them.
4. **Off the control host last.** The projector, the rollup and the live lane
   each run on their own VM, after the control-plane objects they normalize.
5. **Ingest last of all.** `collector`, then `producer`, then `faults` — the
   firehose starts only once everything that reads it exists.
