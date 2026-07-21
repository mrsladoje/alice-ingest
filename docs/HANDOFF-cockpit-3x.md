# HANDOFF — Migrate the ALICE cockpit to OpenSearch 3.x and finish it properly

## 0. Mission (one paragraph)

Migrate the whole "cardboard airplane" stack — 5 CERN OpenStack VMs, native systemd, Ansible-provisioned — from **OpenSearch + OpenSearch Dashboards 2.17 → the newest GA release available as of July 2026** (web-search to pin the exact current version — 3.x line), and deliver a single, branded, **unified "ALICE Cockpit"** that shows, in one interface: (1) all logs (InfoLogger + DDS + stdout) with *working* saved queries and cross-source surrounding-documents; (2) OpenSearch cluster/index health (per-index shards, replicas, health, docs, size, throughput); (3) **Fluent Bit health per node**; (4) **OpenSearch Dashboards' own health**. It must be fully deployed and verified on the real VMs — demo-ready for Lubos, finished, not half-finished.

## 1. Why we're doing this

- The cockpit already exists on 2.17 and is deployed GREEN: an "ALICE Cockpit" dashboard, 7 saved searches, a unified index pattern (`infologger,generic-log-*`), a browser replay button at `/ops`, the two-tier (2 worker + 3 storage) severity-split cluster, and `make replay` / `make replay-fresh`.
- **The blocker:** OSD 2.17's Data-Explorer Discover has a confirmed bug (GitHub `opensearch-project/OpenSearch-Dashboards` #8339, #4844, #8645): **opening a saved search does not restore its query** — every one opens as `match_all`. We proved the stored object is correct (`language:kuery`, imported 17/17, `overwrite:true`) and it *still* opens empty. It is NOT an encoding problem; it's the app's open flow. 2.x minor bumps don't help (#9364 = 2.18 has related bugs).
- 3.x is a Discover/Explore **redesign** and is where the cockpit-grade features live (Explore/PPL, Workspaces, Live Tail, custom branding, Query Insights, a **native Prometheus datasource**). Prior research (in memory + a "fable" session) already concluded OSD 3.x — not Grafana — is the right cockpit for 2026 (Grafana's log tooling is Loki-gated; SigNoz/HyperDX/OpenObserve mean abandoning OpenSearch storage). Read those memories.

## 2. Read these FIRST (load context before touching anything)

- **Memory** (`~/.claude/projects/-Users-admin-Projects-alice-ingest/memory/`): read `MEMORY.md` (the index), then `cardboard-airplane-v2-spec`, `cardboard-airplane-v3-plan`, `cockpit-research`, `prod-flight-and-paper-plan`, `lxplus-native-deploy-runbook`, `deploy-ansible-tree`, `no-code-comments`, and the fable-written cockpit / 3.x-features / cluster-health-built-ins research memories.
- **Docs**: `docs/CARDBOARD-AIRPLANE-V2.md` (two-tier design + shared-script hazard), `docs/CARDBOARD-AIRPLANE-V3.md` (replay button, ops page, cockpit, the full DQL saved-search saga).
- **Deploy tree**: `deploy/` — `site.yml`, `provision.yml`, `teardown.yml`, `inventory.yml`, `group_vars/all.yml`, `roles/{common,opensearch,dashboards,collector,producer}`, `Makefile`.

## 3. Hard constraints — do NOT violate

- **NO CODE COMMENTS.** The user hand-reviews and dislikes AI-authored comments. Write zero comments in any code, template, or config you produce. (Existing heavy comment blocks may be trimmed, but never add new commentary.)
- **`images/replay/replay.py` is PRESERVED ground truth — never edit it.** Extend behaviour via the monkeypatch seam in `deploy/roles/producer/files/replay_partition_wrapper.py`.
- **Preserve the two-tier severity design.** 2 worker + 3 storage, all `m2.medium`, quota ceiling 10 cores / 5 instances. Worker `node_id`s MUST stay `node-01`/`node-02` with `epn_partition` 0/1 — load-bearing: `replay.py` derives `COLLECTOR_HOSTS=[node-01..node-0N]`, and the producer symlink + the per-worker `generic-log-info-<node_id>` `require.box` pinning only line up with those names. Storage = `node-03/04/05` (the manager quorum). Tier isolation via `require.box` (worker-local) and `require.role: storage`.
- **Ansible renders `.j2` with `trim_blocks=True`** — the newline right after a `%}` block tag is eaten (the heredoc / env-line gluing trap; cost real time in v2). Guard multi-line generated content with a blank line (see `deploy/roles/dashboards/templates/alice-ops.service.j2`).
- **Two stacks, don't cross them.** The single-machine "paper airplane" compose stack (`docker-compose*.yml` + `init/opensearch/templates.sh` + `init/dashboards/patterns.sh`) is SEPARATE from the native deploy, which uses forked `.j2` copies under `deploy/roles/dashboards/templates/`. Decide explicitly whether to bump compose to 3.x too or leave it pinned at 2.17 — do not break it silently.
- **You cannot run the real deploy yourself.** It runs from lxplus (kinit + `v3fedkerb` `OS_*` exports; `make deploy` prompts for the vault password). Hand the user exact commands and have them paste output. Dashboard reached via an lxplus tunnel or, on the CERN network, `https://alice-ingest-3.cern.ch:5601` (user `alice` / `vault_dashboards_basic_auth_password`).
- **You cannot browser-test headlessly.** Docker verifies install/config/import/migration compatibility, but the two acceptance gates — "opening a saved search applies its query" and "the cockpit renders correctly" — MUST be verified by the user in a real browser. Make that an explicit checklist item, not an assumption.

## 4. Research to do FIRST (web search — verify, do not assume; versions move fast)

Use the default web tools (WebSearch / WebFetch). **Do NOT use tavily.** Confirm each of these before building, and pin exact versions:

1. The **newest GA (generally available) release of OpenSearch + OpenSearch Dashboards as of July 2026** — the user explicitly wants the latest GA now, not a pinned-in-the-past number (a prior session saw ~3.7, but web-search and confirm the current newest GA; it may be higher). Pin one exact matching version for both OpenSearch and Dashboards, and confirm it is GA (not a beta/RC).
2. Confirm OSD's major must match OpenSearch's major (OSD 3.x requires an OS 3.x cluster). Confirm whether 2.x indices are readable by 3.x (mostly moot — we fresh-reprovision).
3. **VERIFY THE BUG FIX (this is the whole justification).** Does opening a saved search re-apply its query in OSD 3.x? #8339/#8645 are open on 2.x. Find a 3.x fix, and reproduce in docker: stand up OpenSearch 3.x + OSD 3.x, import our `cockpit.ndjson`, and check. If you cannot browser-test, at minimum confirm import/migration compat and flag that the user must verify "open applies query" in a browser. **If 3.x does NOT fix it, STOP and tell the user** — the migration's main rationale collapses and the fallback is embedded dashboard panels (embeddables apply their query server-side; the existing Errors&Warnings metric proves that path works).
4. **OpenSearch 3.x on AlmaLinux 9:** RPM repo + GPG key, bundled JDK (Java 21?), config deltas vs 2.x (removed/renamed settings), security demo config / `plugins.security.disabled`, heap & JVM option changes, Lucene 10 implications.
5. **OSD 3.x config deltas:** `server.*`, `opensearch.hosts`, the **branding** config keys (2.17 used `opensearchDashboards.branding.applicationTitle` — confirm the 3.x key), and **Workspaces**: how to enable, how to provision a workspace, and whether the saved-objects `_import` API changes under Workspaces (this affects our provisioning).
6. **Explore / PPL and Live Tail** in 3.x: setup and whether/how they can be provisioned.
7. **Cluster/index health surfacing:** built-in **Index Management UI** (per-index shards/replicas/health/docs/size), **Query Insights dashboards** (3.x: top queries, throughput, latency), **Performance Analyzer** (CLI/REST only). The **Prometheus exporter plugin** (upstream-adopted) + OSD 3.x **native Prometheus datasource** for historical cluster-metric trends.
8. **Fluent Bit health:** FB already exposes `/api/v1/metrics/prometheus` on `:2020` (`http_server: on` is already set in `deploy/roles/collector/templates/collector.yaml.j2`). The user says FB meta-observability is already set up — find out *how* it's currently wired and how to surface **per-node FB health** in the cockpit (Prometheus scrape of each worker `:2020` → OSD Prometheus datasource; or FB metrics → an OpenSearch index → viz).
9. **OSD self-health:** `/api/status`. There is no built-in Dashboards self-health dashboard — plan a Prometheus-scrape-of-`/api/status` panel (or node metrics) for "how the Dashboards process itself is doing."

## 5. Recommended cockpit architecture (refine after research)

- **One branded "ALICE Cockpit" Workspace** (OSD 3.x) unifying everything; branding `applicationTitle: "ALICE Cockpit"`.
- **Logs view:** the unified index pattern (default), the 7 DQL saved searches *now applying on open*, cross-source surrounding-documents, the `log_source` filter chip, and Live Tail for the live-feed role (this is the single filtered view that replaces two of Thanasis's three split dashboards).
- **Index/cluster health view:** the built-in **Index Management UI** (shards/replicas/health/docs/size per index) + **Query Insights** (throughput/latency). Embed / link as cockpit tiles.
- **Metrics & health view (Prometheus-backed):** stand up ONE small Prometheus on the control node scraping (a) the OpenSearch Prometheus-exporter plugin on all 5 nodes (cluster/node throughput, JVM, disk, indexing rate), (b) **Fluent Bit `:2020` on the 2 workers** (per-node input/output records, retries, dropped, errors = FB health), (c) **OSD `/api/status`** (Dashboards self-health). Visualize all three via the OSD 3.x native Prometheus datasource as cockpit panels/tabs.
- Keep the browser replay button (`/ops`) and the two-tier storage design intact.

## 6. Migration approach — fresh reprovision (recommended, lowest risk)

The data is replayable (historical S3 replay, not precious), so do **not** attempt an in-place 2→3 rolling upgrade. Instead:

1. Bump the Ansible tree to 3.x (versions, repos, config, security, heap, roles).
2. `make teardown` (delete the 2.17 VMs) → `make provision` (fresh) → `make deploy` → `make replay`.
3. This sidesteps index-format migration entirely and matches the existing lifecycle.

Keep the change surface reviewable, role by role:
- `deploy/group_vars/all.yml` — `opensearch_version` → 3.x, plus any new vars (prometheus, datasource, workspace).
- `deploy/roles/opensearch/` — `tasks/install.yml` (repo/GPG/version), `templates/opensearch.yml.j2` (config deltas, node.roles/attrs unchanged in intent), `templates/heap.options.j2`, `templates/ulimits-override.conf.j2`, security defaults.
- `deploy/roles/dashboards/` — `tasks/install.yml` (version), `templates/opensearch_dashboards.yml.j2` (branding key, workspaces enable), `tasks/bootstrap.yml` (keep the always-run cockpit import), the cockpit generator (`files/gen_cockpit.py` → `files/cockpit.ndjson`), the `/ops` service, nginx vhost.
- `deploy/roles/collector/` — ensure FB `:2020` is reachable to Prometheus (add a host-firewall rule to the control node, mirroring the `/ops` `:8088` rule already added in the producer role).
- **New** `deploy/roles/prometheus/` (control node) — install Prometheus, scrape config (OS exporter on all 5, FB `:2020` on workers, OSD `/api/status`), and the OSD Prometheus datasource wiring.

## 7. Cockpit provisioning

- Extend `deploy/roles/dashboards/files/gen_cockpit.py`, regenerate `cockpit.ndjson`, and keep it byte-in-sync (there's a `diff <(python3 gen_cockpit.py) cockpit.ndjson` check pattern).
- **Preserve the always-run import:** `bootstrap.yml` imports `cockpit.ndjson` on *every* deploy via `_import?overwrite=true` (NOT behind the one-shot marker) and sets the default index — this was a deliberate v3 fix so query edits land on a plain `make deploy`. Verify the `_import` API + default-index + branding still work the same under 3.x Workspaces (research item #5).
- Provision: unified index pattern (default), the 7 DQL saved searches, the health/metrics dashboards, the Workspace, branding, and the Prometheus datasource + its dashboards.

## 8. Definition of DONE (every item verified on the REAL VMs)

- 5-node OpenSearch **3.x** cluster GREEN; two-tier placement correct (`generic-log-info-node-01/02` each 1 shard/0 replica STARTED on its own worker; `generic-log-other` + `infologger` 3 shards × 3 copies on storage only; nothing UNASSIGNED).
- `make deploy` green (`failed=0` all 5); `make replay` loads data; the `/ops` replay button works.
- **Cockpit, verified in a real browser:** opening each of the 7 saved searches **applies its query** (the core fix); unified Discover + surrounding-documents work; branding shows "ALICE Cockpit"; it is one unified interface (Workspace).
- **OpenSearch health** visible in-cockpit: per-index shards/replicas/health/docs/size (Index Management) + throughput (Query Insights / Prometheus).
- **Fluent Bit per-node health** visible in the cockpit.
- **OpenSearch Dashboards self-health** visible in the cockpit.
- Docs written (`docs/CARDBOARD-AIRPLANE-V4.md`), memory updated, README + `deploy/README.md` updated, committed on `main`, tagged `cardboard-airplane-v4`.

## 9. Gotchas learned this session (do not repeat)

- OSD/OS major versions must match — there is no Dashboards-only bump.
- The saved-search-open bug is real and NOT an encoding issue (verified: correct `kuery` object still opens `match_all`). The 3.x fix must be verified in a real browser — that's the acceptance gate. If 3.x doesn't fix it, pivot to embedded dashboard panels.
- The cockpit `_import` was deliberately moved OUT of the marker-guarded `patterns.sh` into always-run `bootstrap.yml` tasks. Keep that; don't re-guard it.
- `trim_blocks` gluing; no code comments; `replay.py` preserved; worker `node-01/02` pinning are all load-bearing.
- lxplus tunnel: a stale Mac `:5601` listener silently blocks new tunnels — `lsof -ti :5601 | xargs kill` on the Mac, then re-open; sanity-check with `curl -k -o /dev/null -w '%{http_code}\n' https://localhost:5601` (want 401).
- For cluster queries: run ON a node via `ansible <host> -m shell -a "curl -s localhost:9200/..."` (9200 is firewalled to VM IPs, not lxplus). Run `ansible` from `deploy/` and do NOT pass `-i inventory.yml` — let `ansible.cfg` merge `inventory.yml` + `inventory.generated.yml` so the real provisioned IPs win.

## 10. Repo map (key files)

- `Makefile` — `bootstrap`, `provision`, `deploy`, `replay`, `replay-fresh`, `teardown`.
- `deploy/site.yml` — plays: common → opensearch (bring-up + rolling gate) → dashboards (control) → collector (workers) → producer (workers).
- `deploy/inventory.yml` — groups `workers` (node-01/02), `storage` (node-03/04/05), `control` (alice-ingest-3); placeholder IPs overridden by `inventory.generated.yml`.
- `deploy/group_vars/all.yml` — versions, ports, S3/replay knobs (`il_max_objects`, `stdout_max_objects`, `replay_max_object_bytes`), vault-backed secrets.
- `deploy/roles/opensearch/` — cluster install + config + heap + rolling handlers.
- `deploy/roles/dashboards/` — OSD install, config (`opensearch_dashboards.yml.j2`), nginx TLS+basic-auth (`dashboards.conf.j2`), the `/ops` service (`files/ops_server.py`, `templates/alice-ops.service.j2`, `tasks/ops.yml`), the bootstrap (`tasks/bootstrap.yml`, `templates/patterns.sh.j2`, `templates/templates.sh.j2`), and the cockpit (`files/gen_cockpit.py` → `files/cockpit.ndjson`).
- `deploy/roles/collector/` — Fluent Bit (`templates/collector.yaml.j2` already has `http_server` on `:2020`; `templates/parsers.yaml.j2`).
- `deploy/roles/producer/` — replay systemd unit (`templates/replay.service.j2`), the preserved `replay.py` copy + `files/replay_partition_wrapper.py`, and the worker firewall rule opening `:8088` to control.
- `images/replay/replay.py` — PRESERVED; the replay engine.
