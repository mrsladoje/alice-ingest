# Scalable Logging Architecture

Expanding InfoLogger into a unified, scalable logging platform.
Source: "EPN-Presentation" by Athanasios Papadopoulos (athanasios.papadopoulos@cern.ch), ALICE Summer Student, CERN, 15 Sep 2025. Context: ALICE Event Processing Nodes (EPN) farm.

## Current Architecture (InfoLogger)

- **Injection** — Messages produced via libraries or the `o2-infologger-log` tool, sent to **infoLoggerD**.
- **Collection** — **infoLoggerD** gathers local messages and forwards them to **infoLoggerServer**.
- **Display** — **infoLoggerServer** archives into an SQL database and pushes to **infoBrowser** clients for real-time viewing.

Topology (from diagram):
- **inject** — User process → `libInfoLogger`; or user process `stdout`/`log` (via pipe) → `infoLoggerD` (local socket).
- **collect** — `infoLoggerD` (locally) → `infoLoggerServer` (centrally), over TCP.
- **store/display** — `infoLoggerServer` → **MySQL database** (TCP) and → `infoBrowser` clients (TCP). A separate **InfoLogger AdminDB** is associated with the MySQL database.

## New Architecture

- **Injection** — Unified injection of InfoLogger output (via InfoLoggerD) and DDS logs into a local Fluent Bit worker (collector).
- **Collection** — Fluent Bit gathers local logs and forwards to the **OpenSearch** cluster and to a **Fluent Bit aggregator** via **Kafka**.
- **Display** — OpenSearch exports alerts (via **Alertmanager**) to **Grafana** for visualization; the Fluent Bit aggregator pushes a live feed.

Topology (from diagram):
- **inject** — User process → `libInfoLogger` → `infoLoggerD` (TCP); user process `stdout`/`stderr` and `log files`.
- **collect** — `infoLoggerD` → `fluent-bit (local)`; `log files` → `fluent-bit (local)` (tail).
- **store** — `fluent-bit (local)` → `opensearch (local data node)`.
- **aggregate** — `fluent-bit (local)` → `fluent-bit (aggregator)` via **Kafka**.
- **display** — `opensearch (local data node)` → `alertmanager` → `grafana`; `fluent-bit (aggregator)` → `grafana` (TCP).

### Fluent Bit — Collector & Aggregator

**Collector** (runs on each node):
- Collects InfoLogger output and local DDS log files.
- Performs basic parsing, tagging, and filtering.
- Forwards to OpenSearch (indexing & querying) and Kafka (distribution to other services).

**Aggregator**:
- Subscribes to log topics from Kafka.
- Aggregates across collectors.
- Pushes a real-time stream into Grafana.
- Supports multiple output types for future consumers.

### OpenSearch

- One **OpenSearch Multi-Node Cluster** split into **Worker Nodes** (hold `generic-log-info` / `generic-log-other` indices) and **InfoLogger Storage Nodes** (dedicated `infologger` indices).
- Distributed cluster across worker nodes; each node indexes its own local logs.
- Dedicated storage nodes hold InfoLogger indices (higher access rates).
- Flexible lifecycle management: compression, tiering, and storage policies per index family; automatic archival or deletion after the retention window.

Index families:
- **generic-log-info** — high volume, less queried → strong compression, shorter retention.
- **generic-log-other** (warn/error/debug) — smaller, more valuable → strong compression, longer retention.
- **infologger** — high query rate → fast compression, higher replication, dedicated storage for faster querying.

### Kafka — Extensibility Backbone

- Receives logs from all local Fluent Bit collectors.
- Durable, scalable event bus that decouples log producers from consumers.
- **Current use:** feeds the Fluent Bit aggregator for live Grafana dashboards.
- **Future:** ML pipelines (anomaly detection), error correlation engines (e.g. DBNs) + dependency graphs (e.g. Neo4j), other custom subscribers.

### Grafana + Alertmanager

- Configurable dashboards providing:
  - Real-time monitoring via the Fluent Bit aggregator.
  - Historical search of both DDS log files and InfoLogger from OpenSearch indices.
- Unified alerting through Alertmanager:
  - Grafana alerting rules (thresholds, conditions).
  - Complex alerts exported directly from OpenSearch queries (anomaly detection, frequency thresholds, etc.).
- Dashboards (three views): InfoLogger logs, InfoLogger live feed, DDS logs.

Dashboard field/label schema (from screenshots):
- **InfoLogger Dashboard** — table columns: `@timestamp`, `hostname`, `severity`, `message`, `partition`, `_source`. Filterable labels: Severity, Node, Partition, System (e.g. DPL), Level, Rolename, PID, Username, Facility (e.g. ctf-writer), Detector, Run, Errline, Errsource, free-text Search.
- **DDS Log Dashboard** — table columns: `@timestamp`, `severity` (D / IMPORTANT / WARN / INFO), `node`, `log`, `source_file`. Filterable labels: Severity, Node, free-text Search.

## Advantages

- **Unified ingestion** — InfoLogger and DDS log files combined.
- **Scalability & resilience** — distributed OpenSearch cluster; Kafka decouples producers and consumers (especially for heavy consumers).
- **Flexible retention & storage** — separate index families with tailored policies for more efficient storage use.
- **Modern visualization & alerting** — customizable Grafana dashboards; unified alerts from Grafana and OpenSearch into Alertmanager.
- **Extensible design** — future subscribers via Kafka (ML, correlation engines, graph DBs) or Fluent Bit output plugins (InfluxDB, WebSockets).
- **Community maintained** — built on actively developed open-source projects with wide support.

## Considerations, Changes & Future Improvements

- **Considerations:**
  - From the operators' perspective, no change in how InfoLogger is used today. Grafana is not tailored for this use case the way InfoBrowser is, so dashboards are split into three views: InfoLogger logs, the InfoLogger live feed, and DDS logs.
  - Storage: DDS log files are much denser than InfoLogger logs, so careful index and retention planning is required even with a distributed cluster.
- **Changes:** The only required change is a fork in the InfoLogger daemon, adding an output stream to the Fluent Bit port while preserving all existing InfoLogger behavior.
- **Improvements:** Deployment and scaling via Kubernetes instead of bare-metal installations, for easier updates and monitoring.
