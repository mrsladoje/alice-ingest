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
+ `opensearch-dashboards:3.7.0`): the full `cockpit.ndjson` imports cleanly
(idempotent under `overwrite=true`), all saved searches round-trip with their
`kuery` queries intact, and `defaultIndex` and branding behave exactly as on
2.17. The one thing Docker cannot prove headlessly — that *opening* a saved
search applies its query — is a browser-only acceptance gate (§5), testable on
the same local Docker stack before deploying.

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
marker-guarded). Everything from v3 stays; below the logs section the dashboard
gains a **Platform health** band, all driven by `cockpit-metrics`:

- status tiles: **cluster status**, **unassigned shards**, **Dashboards health**
  (latest-value metric visualizations);
- **Fluent Bit by node** table — `fb_up`, records shipped, errors, failed
  retries, drops per worker;
- **ingest rate by index** (stacked area of doc deltas) and **index size on
  disk** over time;
- **Fluent Bit records shipped per node** and **errors / retries / drops** over
  time;
- **indices now** table (health / pri / rep / docs / size), **node JVM heap %**,
  and **Dashboards response time**;
- drill-down links to the two bundled UIs: **Index Management** (live per-index
  shards/replicas/health/docs/size) and **Query Insights** (top-N queries by
  latency/CPU/memory + live queries — top-N collection enabled persistently by
  `templates.sh`).

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

1. `make deploy` green (`failed=0` on all 5); cluster green; tier placement
   as in v2 (`generic-log-info-node-0N` local, storage indices on 3/4/5 only,
   nothing unassigned).
2. `make replay` loads; `/ops` buttons work.
3. **Browser** (the two gates that cannot be checked headlessly): opening each of
   the 7 saved searches populates the query bar and filters the results — the
   core acceptance criterion of the whole migration; and the cockpit renders,
   including the health band populating within a minute of deploy.
