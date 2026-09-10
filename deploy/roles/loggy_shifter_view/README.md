# Ansible Role: loggy_shifter_view

Installs the shifter view on one storage host of the EPN farm: the log console
a shifter reads during a run, served by one Python service, `alice-shifter`.
The service takes the InfoLogger stream and every central-tier record from
every worker's collector over HTTP, holds the newest records in memory and
pushes them to every open browser over Server-Sent Events. It proxies every
query to the cluster so the browser never talks to OpenSearch, and it serves
the Templates page, which counts and finds log lines by the template identity
the collectors stamped. The role builds the venv with the pinned `drain3` and
`model2vec`, fetches the retrieval model once, installs the server and the
Preact page, and proves the port serves the page before any collector is
pointed at it.

It is the one view that keeps working while the cluster is red: the live lane
is fed straight from the collectors and reads nothing from OpenSearch.

## What the farm looks like

```
      WORKERS — every EPN machine                 STORAGE — epn-infra13

   epn001  collector ──┐                    ┌─ os-node-06 ─────────────────────┐
   epn002  collector ──┼── http :8092 ────> │  alice-shifter                   │
     ...               │   gzip, 1 s flush  │  /ingest  /stream  /api/query    │
   epnNNN  collector ──┘                    │  /api/templates/*  /healthz      │
                                            └───────┬───────────────▲──────────┘
                                                    │ one search    │ browsers, on
                                                    v per press     │ /live/ or 8092
                                            ┌─ os-node-04 = control ───────────┐
                                            │  the cluster, :9201              │
                                            │  Dashboards: nginx proxies /live/│
                                            └──────────────────────────────────┘
```

The `shifter` inventory group names `os-node-06`. On the farm that is the
storage machine, so the service runs beside the three OpenSearch containers
and the control host; moving it to its own machine is an inventory change
only. Browsers reach it as `/live/` on the Dashboards address or directly on
`shifter_port`. Staging runs the same layout on fewer machines, with the
shifter on one small VM and semantic search off.

## How it works

```
┌─ INGEST  POST /ingest ──────────────────────────────────────────────────────┐
│  every worker's collector       http, gzip  --> [infologger] [ildaemon]     │
│      match stamped.(infologger|ildaemon|family.central)  [family.central]   │
│      8M buffer, one retry: a lane that is down never holds up OpenSearch    │
│  a ring of the newest 500 records (shifter_replay_rows), nothing on disk    │
│  the info tier never arrives here; the severity split is the rate limiter   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       v
┌─ STREAM  GET /stream ───────────────────────────────────────────────────────┐
│  Server-Sent Events, one thread and one queue per viewer                    │
│  hello (boot epoch) --> the 500-record backlog --> live records             │
│  a full queue of 2000 drops its oldest record, so a slow viewer stays at    │
│  the live edge; a keepalive comment after 20 s of silence                   │
│  the browser keeps 10000 rows (shifter_buffer_rows) and filters locally     │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ QUERY  POST /api/query ────────────────────────────────────────────────────┐
│  the page --> one OpenSearch search --> the control host                    │
│      indices infologger, application-logs-central                           │
│      5000 rows per press, 500 per page by search_after, 20000 at most       │
│  a wildcard or regular-expression message search with no time range is      │
│  refused (400); no cluster URL is 503; a cluster refusal is 502             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ TEMPLATES  /api/templates/{summary,list,detail,lines,labels,label,...} ────┐
│  template-catalog        watermarks and definitions, a 28-day window        │
│  template-buckets-1h-*   the exact count per template version               │
│  template-buckets-5m-*   which nodes stamped it, to route a Lines search    │
│  application-logs-local-<node>, infologger, application-logs-central: lines │
│  template-triage         labels and notes; shifter-queries: query history   │
│  alice-incidents         the firing episodes beside a template              │
│  semantic: model2vec, potion-retrieval-32M, 512-dimension vectors in memory │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ WHAT THE ROLE INSTALLS, in this order ─────────────────────────────────────┐
│  /opt/loggy/shared/template_contract.py       the contract, vendored        │
│  /opt/loggy/templating/{drainbench,masking}.py  the frozen mining recipe    │
│  /opt/loggy/{shifter,templates_view,semantic,triage}.py   the server        │
│  /opt/loggy/shifter-venv   python3.13..3.10 + drain3 0.9.11 + model2vec     │
│      no interpreter of 3.10 or newer on the host: the play fails here       │
│  /opt/loggy/models/potion-retrieval-32M   fetched once, pinned revision     │
│  /opt/loggy/live/   shifter.js templates.js shifter.css shrunk on the       │
│      control node by terser, else esbuild, else shipped readable;           │
│      preact, hooks, the shim and the favicon copied; index.html rendered    │
│  firewalld   one rich rule per client address, when alice_manage_firewalld  │
│  alice-shifter.service   restarted only when an input above changed         │
│  PROVE   GET /healthz, 12 tries 5 s apart; then GET / must name shifter.js  │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Delivery to the live lane is best effort.** The collector's output has an
  8M buffer and one retry, so a lane that is down or a viewer that is slow can
  never push back on the OpenSearch path. Drops are counted and shown.
- **"Live" means one second.** The collector flushes every second, so that is
  the latency floor of the stream.
- **The unit has no writable path.** `DynamicUser=true` with
  `ProtectSystem=strict`; the buffer is in memory and the files are read only.
- **The severity table is a second copy.** Records bound for OpenSearch are
  normalised by the ingest pipeline; the live lane bypasses it, so `shifter.py`
  carries the same table and its own `origin_host` fallback.

## Why not an upstream role

There is no upstream product here to manage. The server, its modules and the
page are this repository's own files; the third-party pieces are a pinned
`drain3` and `model2vec` in a venv and two vendored Preact files whose versions
and hashes are in `files/live/VENDORED.md`.

## Requirements

`loggy_opensearch` must have run in both of its modes first: the query lane,
the Templates page and the label store are clients of the cluster at the
control host and read `template-catalog`, `template-triage`, `shifter-queries`
and `alice-incidents`, which its configure-the-cluster mode creates. The live
lane needs none of that. The role carries its own copy of
`template_contract.py`; `loggy_collector` and `loggy_template_catalog` ship
the same file, and the three copies must stay identical.

The host needs one of `python3.13` to `python3.10` for `model2vec`, and
outbound HTTPS to the Hugging Face hub on the first run, which fetches the
model once. Run this play before `loggy_collector`; the two proofs at the end
of this role are what make that order safe.

## Role Variables

The variables worth changing. The rest of `defaults/main.yml` is paths and
service names.

```yaml
shifter_replay_rows: 500
shifter_client_queue_max: 2000
shifter_buffer_rows: 10000
shifter_hidden_grace_seconds: 120
shifter_token: ""
shifter_minify: auto
```

The ring is the server's memory and the backlog a new viewer is sent; the
buffer is the browser's window, and the two may differ. A background tab drops
its stream after the grace period (`0` never), an empty token leaves the
firewall as the boundary, and `minify: always` fails the deploy without a tool.

```yaml
shifter_opensearch_url: "http://<control host>:<opensearch_http_port>"
shifter_opensearch_indices: "infologger,application-logs-central"
shifter_opensearch_user: ""
shifter_opensearch_password: ""
shifter_opensearch_verify: true
shifter_query_default_rows: 5000
shifter_query_max_rows: 20000
shifter_query_page_rows: 500
```

The indices match what reaches the live lane, so both halves of the page show
one universe. An empty URL turns the query lane off and the Templates page
with it; the live lane is unaffected.

```yaml
shifter_templates_enabled: true
shifter_view_refresh_seconds: 300
shifter_episode_refresh_seconds: 30
shifter_search_timeout_seconds: 30
shifter_search_terminate_after: 200000
shifter_template_lines_ceiling: 500
shifter_template_watched_max: 100
shifter_template_note_max_chars: 2000
```

The view is rebuilt once per stamper publication interval, five minutes. The
timeout is the OpenSearch-side deadline on every search the page sends; when
it expires or the per-shard ceiling fires, the page reports a match total as a
lower bound rather than an exact number.

```yaml
shifter_semantic_enabled: true
shifter_semantic_backend: "model2vec"
shifter_semantic_model_repo: "minishlab/potion-retrieval-32M"
shifter_semantic_model_revision: "6fc8051fab2a1e0ee76689cf08c853792ac285e7"
shifter_semantic_max_versions: 20000
shifter_vector_cache_bytes: 67108864
shifter_memory_high: "1G"
shifter_memory_max: "2G"
```

A different revision is a different model; with semantic search off the page
keeps its text search, labels and exact counts, and the unit still runs the
venv for `drain3`. At start the server sums its caches, counting the catalog
cache twice because the previous view stays resident while the next is built,
and refuses to start past `shifter_memory_max`.

From `group_vars` and the inventory: `shifter_enabled`, `shifter_host`,
`shifter_port`, `shifter_ingest_path`, `shifter_allowed_client_addresses`,
`alice_app_root`, `alice_shared_dir`,
`template_catalog_index`, `template_triage_index`, `shifter_queries_index`,
`incidents_index`, `template_catalog_active_days`,
`template_catalog_definition_retention_days`, `alice_manage_firewalld`.

- `shifter_port` and `shifter_ingest_path` are written into the collector's
  output by `loggy_collector`; change them in `group_vars`, never here alone.
- `shifter_allowed_client_addresses` needs `firewalld` running when
  `alice_manage_firewalld` is true, and only ever adds: a permanent rule stays,
  so removing an address does not close the port on a host that already ran.
- The Templates environment sits inside the query-lane block of the unit, so
  an empty `shifter_opensearch_url` turns the page off with it.

## The Templates page

The second page of the same app: every template version the farm stamped in
the last 28 days with an exact volume per version, and the lines behind a
version, found by their stamp.

| It reads | For |
|---|---|
| `kind: watermark` in `template-catalog` | how far each node has published, and the cutoff |
| `template-buckets-1h-*` | the count per version, summed over the window |
| `kind: template` in `template-catalog` | the text, the scope and the widening links |
| `template-buckets-5m-*` | which nodes stamped a version, to route a Lines search |
| `template-triage`, `shifter-queries` | labels with their history, and the query log |
| `alice-incidents` | the firing episodes shown beside a template |

The cutoff is the hour boundary every live node has published past; a node
silent for three publication intervals is idle and does not hold it back.
Coverage is `complete` when every node with a bucket in the window has
published past the cutoff, else `partial` and the count is a lower bound. A
view whose cutoff is older than one hour plus the idle horizon plus one
refresh is stale; past twice that it is retired, and neither is shown as
current.

Version W covers version N when both share a family and token count and W has
`<*>` or the same token at every position. The Lines panel expands a version
to what it covers, asks the bucket documents which nodes stamped any of them,
and queries only those nodes' local indices plus the central and InfoLogger
indices; **Include ancestors** re-matches with the recipe the stamper mined
with, which is why the role ships `drainbench.py` and `masking.py`.

## Example Playbook

```yaml
- hosts: shifter
  become: true
  roles:
    - role: loggy_shifter_view
      when: shifter_enabled | bool
```

## Author Information

Marko Sladojevic, CERN ALICE O2/EPN, 2026.
