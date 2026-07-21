# Cardboard Airplane v4 — OpenSearch 3.7 and the Finished Cockpit

The follow-up to [`CARDBOARD-AIRPLANE-V3.md`](CARDBOARD-AIRPLANE-V3.md). Same 5-VM
two-tier cluster, same replay button — v4 migrates the whole stack **OpenSearch +
Dashboards 2.17.0 → 3.7.0** (the newest GA line as of July 2026) and finishes the
**ALICE Cockpit** into a genuinely unified view: all logs *plus* OpenSearch cluster
health, per-index state, **Fluent Bit health per node**, and **Dashboards
self-health**, in one dashboard.

---

## 0. Why migrate at all — the saved-search bug

v3 shipped 7 DQL saved searches, but OSD 2.17's Discover has a confirmed upstream
bug (`opensearch-project/OpenSearch-Dashboards` #8339 / #8645): **opening a saved
search does not re-apply its stored query** — every one opens as `match_all`, even
though the stored object is correct. The fix (PR #8689, which restores both the
saved query and the saved filters into Discover on open) merged in October 2024,
shipped in **OSD 2.18**, and is contained in every 3.x release by git ancestry.
The 3.x line then hardened the same path further (#9541 in 3.0, #10357 in 3.3,
#10913 in 3.4, #11239 in 3.5).

3.7.0 was pinned as the migration target: newest GA of both OpenSearch and
Dashboards (released 2026-06-09; the majors must match), carrying the fix plus the
3.x cockpit features (bundled Query Insights dashboards, Index Management UI,
current branding keys).

**De-risked before touching the VMs** (throwaway Docker, real `opensearch:3.7.0`
+ `opensearch-dashboards:3.7.0`, plus a headless-Chromium render smoke): the
full `cockpit.ndjson` imports cleanly (idempotent under `overwrite=true`), all
saved searches round-trip with their `kuery` queries intact, `defaultIndex` and
branding behave exactly as on 2.17, and **all 22 dashboard panels render with
zero errors** — including the failure modes (Fluent Bit down → DOWN within one
sample; poller stopped → the status strip flips to STALE with the sample age;
bulk rejections → detected and logged; a partially failed import → the deploy
fails instead of reporting green). Opening each saved search in a real browser
remains the final human acceptance gate (§5).

## 0a. What v4 deliberately does NOT enable

3.x also ships a redesigned Discover ("Explore"), Workspaces, and a native
Prometheus datasource. All three stay **off** in v4, on purpose:

- **`explore.enabled` stays false** (the 3.7 default). Classic Discover is still
  the default experience in 3.7 and is exactly the code path the #8689 fix lives
  in. Enabling Explore force-enables query-enhancements mode, which still has an
  *open* saved-search-open bug (#10912) — switching it on would risk recreating
  the very bug this migration kills. Classic `search` saved objects open in
  classic Discover even when Explore is on, so Explore adds risk and no value to
  the cockpit.
- **Workspaces stay off.** They exist for multi-team scoping; this platform has
  one team and one use case. The "one unified interface" is delivered by the
  branded title + the single cockpit dashboard + the default unified pattern.
- **No Prometheus server.** The OSD "native Prometheus datasource" is consumable
  only through Explore Metrics (which requires the Explore/Workspace stack, above)
  — and 2.x's Live Tail (Observability event analytics) was removed in 3.0
  anyway, so the PPL live-feed rationale is gone. Live-feed duty falls to
  Discover's auto-refresh interval. Platform metrics come from a poller instead
  (§2) — one small service, no new infrastructure, and every panel stays a
  standard OSD visualization inside the one cockpit dashboard.

## 1. The migration (fresh reprovision)

The data is replayable history, so v4 does not attempt an in-place 2→3 upgrade:
bump the Ansible tree, then `make teardown` → `make provision` → `make deploy` →
`make replay`. Index-format migration never happens.

Install-path changes on AlmaLinux 9:

- Repo baseurls move to the `3.x` line (derived from `opensearch_version`, now
  `3.7.0`, in `group_vars/all.yml`).
- **The v2 SHA1 crypto-policy dance is gone.** 3.x RPMs are signed RSA/SHA512 and
  the `opensearch-release.pgp` key imports under Alma 9's *default* policy
  (verified in an `almalinux:9` container against the real repo), so the
  `update-crypto-policies --set DEFAULT:SHA1` block/always wrapper and the legacy
  SHA1 `opensearch.pgp` key are deleted from both install task files.
- The 2.x `OPENSEARCH_INITIAL_ADMIN_PASSWORD` throwaway-password hack is replaced
  by 3.7's supported `DISABLE_INSTALL_DEMO_CONFIG=true` install env var.
- `opensearch.yml` and `opensearch_dashboards.yml` carry over unchanged —
  `plugins.security.disabled: true`, `node.roles`, `node.attr.*`, discovery
  settings, and the `opensearchDashboards.branding.applicationTitle` key are all
  identical in 3.7 (config-validated against the real 3.7.0 distribution). The
  bundled `securityDashboards` plugin is still removed post-install as before.
- 3.7 bundles JDK 25 and Lucene 10; heap stays `512m` via `jvm.options.d`.

## 2. Platform health — the `alice-metrics` poller

A ~200-line stdlib-Python systemd service on the control node (same pattern as
the proven `/ops` server), polling every 30 s and bulk-indexing flat docs into a
new **`cockpit-metrics`** index (1 shard, 2 replicas, `require.role: storage`,
`dynamic: false` mapping — provisioned by `templates.sh` like every other index):

| `kind` | source | what lands in the doc |
|---|---|---|
| `cluster` | `_cluster/health` | status (+ numeric code), node count, active/relocating/initializing/unassigned shards, pending tasks |
| `index` | `_cat/indices` | per index: health, pri/rep, docs count **+ delta**, store bytes |
| `node` | `_nodes/stats` | per node: heap %, CPU %, disk used %, docs, store bytes, indexing total **+ delta** |
| `fluentbit` | worker `:2020/api/v1/metrics` | per worker: `fb_up` 0/1, input/output records, errors, retries, retries_failed, dropped — cumulative **+ delta** |
| `osd` | OSD `/api/status` | state (+ code), RSS, event-loop delay, load, response avg/max ms, requests, connections |

Counters are cumulative since process start, so the poller also stores per-tick
deltas (clamped at 0 across restarts/wipes) — that is what makes rate charts
("docs added per interval", "records shipped per node") work as plain sum
aggregations. An unreachable Fluent Bit becomes an explicit `fb_up: 0` doc and an
unreachable Dashboards an `osd_state: unreachable` doc — down is a data point,
not a gap. Workers open `:2020` to the control node only (firewalld rich rule in
the collector role, mirroring the `:8088` replay-trigger rule).

## 3. The finished cockpit

`gen_cockpit.py` grows from 17 to **31 saved objects** (still one generated
`cockpit.ndjson`, still imported with `overwrite=true` on **every** deploy, never
marker-guarded). The dashboard now runs **two clocks on one page**: it stores
its own time range (June 2026 → now, covering the replayed event time) plus a
saved **30 s auto-refresh**, while every health panel carries a per-panel
override pinning it to `now-1h → now` — so live health and historical logs stay
simultaneously readable, and the header says exactly that. Top to bottom:

- a **Vega live-status strip**: CLUSTER / DASHBOARDS / FLUENT BIT per worker as
  colored chips with explicit text (GREEN / YELLOW / RED / UP / UNHEALTHY /
  DOWN — never color alone). Each chip computes its sample age in-browser and
  flips to gray **STALE &lt;age&gt;** beyond 90 s, or shows **NO DATA** if the
  poller has never written — a dead poller cannot leave a stale green on
  screen;
- status tiles (cluster status, unassigned shards, Dashboards health) and the
  **Fluent Bit by node** table — up, **healthy** (the native `/api/v2/health`
  verdict, distinct from merely HTTP-reachable), records shipped, errors,
  failed retries, drops;
- the v3 logs section: totals, severity over time, top hosts/systems, the
  Errors & Warnings saved search;
- **Detailed platform health**: **indexing rate by index** (true `_stats`
  indexing-ops deltas — not document-count growth — with the poller's own
  `cockpit-metrics` writes excluded), index size on disk, Fluent Bit
  records/errors/retries/drops per node, **indices now** (health / pri / rep /
  docs / size), node JVM heap %, Dashboards response time — every y-axis in
  real units (ops/30 s, bytes, %, ms);
- drill-down links to the two bundled UIs: **Index Management** and **Query
  Insights** (top-N collection enabled persistently by `templates.sh`); the
  Dashboards `defaultRoute` lands users straight on the cockpit.

## 3a. Provisioning is strict and self-verifying (review-driven hardening)

An external review of the first v4 cut found three release blockers; all are
fixed and locally verified:

- **Field catalogs.** An API-created index pattern stores only
  `title`/`timeFieldName`; classic visualizations then fail with "Could not
  locate that index-pattern-field". After every import, `bootstrap.yml` runs
  `hydrate_patterns.py`, which pulls each pattern's live field list via
  `/api/index_patterns/_fields_for_wildcard`, persists it into the saved
  object, and **fails the deploy** if required fields (`@timestamp`, `kind`,
  `severity`, …) are missing. Because the cockpit import overwrites the
  patterns each deploy, hydration always re-runs after it.
- **Template-before-first-write.** The poller previously started before the
  bootstrap had installed the `cockpit-metrics` template — the first bulk write
  would auto-create the index with dynamic mappings on the wrong tier. Order is
  now: templates → explicit index creation (`infologger`, `generic-log-other`,
  `cockpit-metrics`, per-worker info indices) → import → hydrate → *then* start
  `alice-metrics`.
- **`failed=0` means deployed.** The import and settings tasks no longer mask
  failures: the import response is parsed and must show `success: true` with
  every object imported and zero errors; pattern-creation and
  Dashboards-readiness failures are fatal; the old one-shot marker guard is
  gone (both bootstrap scripts are idempotent and run on every deploy).

The poller is hardened to match: it parses the bulk response (an HTTP 200 with
`errors: true` is a failure — counted, logged with the first reason, and the
service exits for systemd restart after 20 consecutive failures), logs every
unreachable endpoint, and runs as a sandboxed `DynamicUser` unit.

## 4. Explicitly out / unchanged

- **The compose paper airplane stays on 2.17.** `docker-compose*.yml`,
  `images/**` and the shared `init/` scripts are untouched — the single-machine
  stack is a learning vehicle, not the deliverable, and bumping it is orthogonal.
  (The two stacks were already forked at v2; nothing in v4 crosses them.)
- Two-tier topology, `node-01`/`node-02` naming, `replay.py` (preserved),
  `AUTOSTART_REPLAY=false` arming, `/ops` page: all unchanged.
- ISM/retention: still deferred (bounded one-shot loads). `cockpit-metrics` at
  ~15 docs/30 s grows ~40 k docs/day — negligible for the demo lifecycle.

## 5. Verification gates (real VMs)

1. `make deploy` green (`failed=0` on all 5) — which now *implies* the cockpit
   imported, verified and hydrated (§3a); cluster green; tier placement as in
   v2, plus `cockpit-metrics` (1 shard, 2 replicas, storage tier, `dynamic:
   false`), nothing unassigned.
2. `make replay` loads; `/ops` buttons work.
3. **Browser** (the human gates): opening each of the 7 saved searches
   populates the query bar and filters the results — the core acceptance
   criterion of the whole migration; the cockpit renders with the status strip
   LIVE and the health band populating within a minute; and the two-clock
   layout reads sensibly (live health up top, June logs below).
