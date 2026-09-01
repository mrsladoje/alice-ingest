# `shifter`

Installs the shifter log view on its own VM: a single-file Python server, a
vendored React page, a firewall rule per client, a systemd unit, and two proofs
that the port answers before any collector is told to push to it.

## What this is, and what it is for

It is the shifter's log window, and it has two lanes in one page.

**The live lane** is the platform's `tail -f`: log lines from every node as they
arrive, with no query to write and no search cluster in the path. It is for
watching — a run starting, a service being restarted, a fault injection landing.

**The query lane** answers questions about the past. The page posts a filter set
to `POST /api/query`, the lane server turns it into one OpenSearch search and
returns the rows. The browser never talks to OpenSearch itself, so the cluster
keeps one caller, one credential and one origin.

The live lane is docked at the foot of the page and expands in three steps —
collapsed, half screen, full screen — so a shifter can read a query result and
still see the stream move. That shape is deliberate: during a burst a fixed,
expandable lane keeps the thing you are reading still, which a full-screen tail
cannot.

The layout follows the ALICE InfoLogger GUI on purpose. A shifter who knows that
tool should not have to learn a new one: same dense grid, same per-column match
and exclude boxes, same severity chips, same jump-to-error arrows.

Collectors POST records to it over HTTP. It holds the newest
`shifter_replay_rows` in memory, pushes every record to every open browser
over Server-Sent Events, and writes nothing to disk. Each browser then keeps its
own window of the newest `shifter_buffer_rows` records and filters inside that
window, locally.

Two properties follow from that shape, and they are the whole reason the lane
exists:

- **Watching costs the cluster nothing.** A live tail in Discover is a query
  re-run every few seconds against the same nodes that are indexing the data,
  once per watcher. The lane never reads OpenSearch, so a control-room screen
  left open all shift adds no search load at all.
- **It keeps working while the cluster is red.** The one moment everybody wants
  to read logs is the moment OpenSearch is unhappy. Every other viewing surface
  in this repository — Discover, the cockpit, the episode board — is a client of
  the cluster and goes dark with it. This one is fed straight from the
  collectors and does not.

The cost it does have grows with **readers, not with data**: one open connection
and one thread per viewer. That is why it runs on its own host and not on the
control host.

## What reaches it

| | |
|---|---|
| **In scope** | `infologger` and `application-logs-central` — the InfoLogger stream, and every stdout record whose severity is not `Info`. |
| **Never** | `application-logs-local`. The info tier is the bulk of the volume, and pushing all of it to a browser at farm scale gives a blur nobody can read. The severity split *is* the rate limiter here. |
| **Latency** | About `fluent_bit_flush_seconds` (1 s). The collector's flush interval is service-level, so it is the lane's latency floor too. "Live" means one second, not instant. Soak round 2 cut this from five. |
| **Delivery** | Best-effort, deliberately. The collector's HTTP output gets a 1 MB buffer and one retry, so a lane that is down or a viewer that is slow can never push back on the OpenSearch path. |
| **Enrichment** | The lane does its own. Field normalization lives in the `alice-add-ingest-time` ingest pipeline, which only records going to OpenSearch pass through. `shifter.py` therefore carries its own copy of the severity table and its own `origin_host` fallback. |

The producing end is the `collector` role: one `http` output, gzip-compressed,
matching those two tags. Turning `shifter_enabled` off removes that output
entirely.

## What the page gives you

- **A one-hour window by default.** A fresh page asks for the last hour, not for
  all time. Widen it in `Filters` when you need to. This is the difference
  between a bounded range query and a scan of the whole archive, and it is the
  single biggest thing the page does for the cluster.
- **Rows arrive a page at a time.** `Query` fetches `shifter_query_page_rows`
  (500) and lands you on the newest of them. Scroll up towards the older end and
  the next page is pulled and put on top, with the scroll position corrected so
  nothing jumps under your eyes, until `limit` rows are held. The status bar says
  *scroll up for older* while there is more.
- **The Query button says when the rows are stale.** Typing in the filter grid
  never queries — the same rule InfoLogger has, for the same reason. When what
  you have typed no longer matches the rows on screen the button turns amber and
  reads `Query — filters changed`. It is a state, not a toast: it cannot be
  missed by looking away, and it does not have to be dismissed.
- **The live lane filters as you type**, over the rows the browser already
  holds. That costs the cluster nothing, so it is not held back.
- **One filter grid over fifteen columns.** A row of column buttons that show
  and hide each column, a `match` row and an `exclude` row under it. Several
  words in one box mean OR; the message boxes split on new lines instead, so a
  list of known-noise messages can be pasted in. `%` and `_` are wildcards.
  Enter runs the query.
- **Severity chips and jump-to-error arrows** in the toolbar: `|◀ ◀ ▶ ▶| ▼`
  walk the result to the oldest, previous, next and newest ERROR or FATAL row,
  and to the newest row of all. `◀` and `▶` step from the selected row. The
  rows around each error stay in view, which the severity chips cannot do.
  Each arrow explains itself when the pointer rests on it.
- **The time range sits in the grid, under the Time column.** A shifter changes
  it more often than any other filter, so it is not behind a dropdown. The
  `match` cell under `TIME` is a picker: last 15m, 1h, 6h, 24h or 7d, plus
  `exact…`. It defaults to the last hour, which keeps the first query off the
  whole index, and `Clear` puts it back there. Picking `exact…` opens a
  `between` row under the grid with two date-and-time boxes; leaving one empty
  means no bound on that side. The `exclude` cell under `TIME` does the same
  the other way: `hide…` opens an `except` row, and rows inside that window are
  dropped with a `must_not` range.
- **Dates are written the way CERN writes them: `dd.mm.yyyy hh:mm`.** Each box
  is a text field you can type into, with a calendar button on its right that
  opens a calendar. Clicking the date half of the box opens it too; clicking
  the hours or minutes puts a cursor there instead, because those are what a
  shifter nudges by hand. The calendar closes on a pick, on Escape, on a click
  outside and when the grid scrolls. It is the page's own calendar, not the
  browser's: `showPicker()` fires no event when its calendar closes, so a page
  driving it can only ever reopen it, never tell whether it is already open. The picker is a hidden
  `datetime-local` input driven by `showPicker()`; the page reads the value
  back out and rewrites it in the chosen format. It has to work that way
  because a visible `datetime-local` takes its field order from the reader's
  locale and cannot be told to put the day first.
- **A format menu sits at the right of the row.** `dd.mm.yyyy` is the default;
  `dd/mm/yyyy`, `yyyy-mm-dd` and `mm/dd/yyyy` are the others. Changing it
  rewrites whatever is already in the boxes, so a date never silently changes
  meaning. The choice is kept in `localStorage` under
  `alice.shifter.dateformat.v1`. The parser reads the chosen order, which is
  what settles an ambiguous `03.04.2026`. A date it cannot read turns the box
  red and holds the `Query` button until you fix it. What you type is the
  console's own clock; the page converts it to UTC before it asks OpenSearch,
  so an 09:00 typed in Geneva really means 09:00 in Geneva.
- **A query with no start date is allowed, but the total stops counting.**
  Paging 500 rows at a time is not what makes a wide query cheap — the first
  page also asks OpenSearch for an exact match total, and counting every match
  is what reads the whole index. So the total is exact only when a start date
  is set. Without one the server sends `track_total_hits: false`, OpenSearch
  stops counting at 10,000, and the status bar reads `10,000+` to say the real
  number is at least that. The rows themselves are unaffected — sorting
  newest-first over an indexed date stays fast on its own.
- **The rest of the filtering power stays behind a dropdown.** `Filters` holds
  the substring-or-regular-expression switch, the verbosity ceiling
  (Ops 1, Support 6, Devel 11, Trace) and the row limit. The grid stays the way
  a shifter reads it; the power does not take up space.
- **Saved filters.** `Saved` carries five built-in starting points and anything
  you name and keep. Saved filters live in this browser's `localStorage`, so
  they do not follow a person to another console. A link does. Every row in the
  menu has its own `link` button that copies a URL for that row, and
  `Copy link to the filter on screen` copies one for the filter you are
  currently looking at.
- **A link carries only what you changed.** The URL fragment lists the fields
  that differ from an empty filter, as `name=value` pairs joined by `&`, with a
  trailing `!` for an exclude box — `#program=o2-eventbuilder&system=QC` and
  `#sev=fatal,error&range=6h`. Four filled boxes make a 49-character fragment.
  The same four written as whole-state JSON took 1,344. The page reads
  the fragment on load and on any later change to it, so pasting a link into a
  tab that is already open applies it too.
- **Copying makes no dialog.** The button writes to the clipboard directly and
  raises a small `Link copied` toast for two seconds. Where the browser refuses
  the clipboard API — it is only offered over HTTPS or on `localhost`, so a
  plain `http://<ip>:<port>` console does not get it — the page falls back to a
  hidden text box and a copy command, which works everywhere.
- **A view that never jumps.** New records enter the buffer while you read. A
  `N new — show newest` button tells you how many arrived and renders them only
  when you press it. `Autoscroll` opts out of that and pins the lane to the
  newest row instead.
- **Column layout is remembered** per browser, in `localStorage`.
- **An inspector, in one of two places.** `Inspector` turns the record view on
  and off; the button beside it says where it opens. `Side panel` is the column
  on the right. `Under row` opens the same fields in a panel anchored to the
  clicked row, inside the scroller, so it moves with the row and the columns the
  table had to drop are readable without leaving the line they belong to. The
  panel closes on a second click of the same row, on `Close` and on Escape —
  Escape does nothing while the panel is shut, so a side-panel selection is
  never lost to a stray keypress. Near the end of the result it opens above the
  row instead of below it, so it never hangs off the bottom of the pane.
  Switching the button while a row is selected carries that record straight to
  the new place. Its place is kept in `localStorage` under
  `alice.shifter.detail.v1`; with nothing kept there, a window over 640 px
  starts on the side panel and a narrower one starts under the row.
  Both tables carry it: a live-lane row opens the same panel inside the
  lane, sized down to whatever height the dock currently has. A full-screen
  live lane hides the query workspace, so the side panel moves into the dock
  and stands beside the lane table rather than disappearing with it. Only the
  rows on screen are rendered, so the tab stays responsive with ten
  thousand records in it, and a closed panel costs nothing at all.
- **A severity scroll map** beside each table: one coloured tick per warning,
  error and fatal over the whole result, so a cluster of errors is visible
  without scrolling to it.
- **A status word and counts** — `live`, `reconnecting` or `paused`; how many
  rows matched out of how many the query found; per-severity counts; the query
  the server actually ran; and, when it happened, how many records the server
  dropped for this viewer.
- **Honest gaps.** A missing run of records draws a marker row naming how many
  were missed; a lane restart draws a marker saying what it held before is gone.
  Neither is silent.
- **The same page on a phone, narrowed — not a second design.** Nothing is
  restyled and nothing is hidden behind a different layout. Three things adapt:

  | Width | What changes |
  |---|---|
  | over 1000 px | Everything, as designed. |
  | 1000 px and under | The toolbar wraps. `Partition`, `User`, `PID`, `ErrSource` and `ErrLine` drop out of the table. The inspector narrows. |
  | 760 px and under | A side-panel inspector becomes an overlay instead of a column, and only appears once a row is selected. |
  | 640 px and under | The table keeps time, host and message. The record panel opens under the row instead, so the nine columns that dropped out are still one tap away. The clock loses its milliseconds. The filter grid folds behind a `▼ Filter grid` strip. Dropdowns become full-width sheets. |

  The saved column layout is never touched by this. Widen the window and the
  columns you chose come straight back.
- **Touch targets grow on their own.** `@media (pointer: coarse)` raises every
  button, chip and input to a thumb-sized target. A mouse never sees it.
- **A link back to the Maintainer Cockpit**, matching the *LIVE LOG LANE* button
  in the cockpit that opens this page in a new tab.

## How to reach it

- `/live/` on the Dashboards vhost — nginx on the control host proxies it, so
  operators keep one address and one tunnel. This is the normal way in.
- `http://<shifter host>:8092/` (`shifter_port`) directly, from an address the
  firewall rule allows.

## What it is not

- **Not an analysis tool.** The query lane answers "show me the lines", not
  "how many, over what period, trending which way". No aggregation, no charts,
  nothing on disk. Dashboards keeps that half.
- **Not a search tool when the cluster is down.** The query lane is a client of
  OpenSearch and goes dark with it. The live lane does not — it is fed straight
  from the collectors, which is the whole reason the two share one page.
- **Not complete.** Records can be dropped at both ends — by the collector when
  its small buffer fills, by the server for a viewer that cannot keep up. Both
  are counted and both are shown; neither is prevented.
- **Not the whole log stream.** The info tier never arrives here. If a record is
  missing and it was an `Info`, that is the design, not a fault.
- **Not authenticated by itself** in the default configuration. See
  `shifter_token` below: the firewall is the boundary.

## The server's endpoints

| Method and path | Who calls it | What it does |
|---|---|---|
| `POST /ingest` (`shifter_ingest_path`) | the collectors | Accepts one record or an array, gzip or plain. Returns `204`, `400` on a body it cannot parse, `401` when a token is set and not presented. |
| `GET /stream` | the page | Server-Sent Events. Opens with a `hello` event carrying the boot epoch, then the replay backlog, then live records. A keepalive comment after every `SHIFTER_KEEPALIVE_SECONDS` of silence. |
| `POST /api/query` | the page | Takes `{criterias, options}` — `options.after` continues a page. Builds one OpenSearch query, returns `{rows, count, countRelation, limit, pageSize, after, hasMore, took, queryAsString}`. `400` when the filters would scan the term dictionary with no time range, `503` when `SHIFTER_OS_URL` is empty, `502` when OpenSearch refuses. The live stream is unaffected by all three. |
| `GET /healthz` | Ansible, and you | JSON: `ok`, `epoch`, `viewers`, `buffered`, `queryConfigured`, `received`, `posts`, `bad_posts`, `queries`, `bad_queries`, `dropped_slow_client`. This is where you look to tell "nothing is arriving" apart from "nobody is watching". |
| `GET /` and the assets | the browser | The page shell, the script, the stylesheet and the two React files. Flat: only basenames inside the static directory are served. |

## Why it is a separate role

It is the only part of the platform that runs on the `shifter` group. It was
already deployed from its own play in `site.yml` through `tasks_from`, which is
the shape of a role that has not been given its own directory yet. It shares no
file, no service and no handler with the Dashboards stack.

## What it does

```
                            HOSTS: shifter

┌─ 1. DIRECTORIES ───────────────────────────────────────────────────────────┐
│  /opt/alice-ingest          0755 root — also created by other roles         │
│  /opt/alice-ingest/live     0755 root — the static document root            │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. SHRINK, on the control node, never on the target ──────────────────────┐
│  terser first, else esbuild                shifter.js + shifter.css        │
│  neither on PATH, shifter_minify=auto      ship the readable source        │
│  the output is measured before it is allowed to replace a working page     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. PAYLOAD ───────────────────────────────────────────────────────────────┐
│  shifter.py                 0755 the server                                │
│  shifter.js + shifter.css   0644 the page                                  │
│  preact + hooks + shim      0644 vendored, see files/live/VENDORED.md      │
│  alice-favicon.svg          0644 the browser tab icon — the official       │
│                                  ALICE octagon, cropped from the SVG       │
│                                  in presentation/assets                    │
│  index.html                 0644 rendered — buffer size + cockpit link     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. FIREWALL — named clients only, never the world ────────────────────────┐
│  8092/tcp  one rich rule per address in shifter_allowed_client_addresses   │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. START, then PROVE ─────────────────────────────────────────────────────┐
│  systemd unit + enable      restarts only when a payload file changed      │
│  GET /healthz               12 attempts, 5 s apart — 1 minute              │
│  GET /                      asserts the served page references shifter.js  │
└────────────────────────────────────────────────────────────────────────────┘
```

## Non-obvious settings

- **The restart is conditional, not a handler.** The service task computes
  `restarted` or `started` from the four `register` results above it. A handler
  would fire at the end of the play, after the two health proofs had already
  run against the old process.
- **`DynamicUser=true` with `ProtectSystem=strict`.** The server has no writable
  path and needs none: the buffer is in memory and the static files are read
  only. There is no `shifter` system account to create.
- **The page proof reads the body, not the status code.** `GET /` returning 200
  only proves the server is up. Asserting `shifter.js` appears in the body
  proves the static directory was populated, which is the failure that actually
  happens.
- **React is committed, not fetched.** The deploy has no network to unpkg and no
  build step. `files/live/VENDORED.md` records the versions, the source URLs and
  the SHA-256 sums.
- **The page closes its own stream when its tab is hidden, not when the user
  stops typing.** After `shifter_hidden_grace_seconds` in the background the
  page closes the `EventSource` and offers a resume button; a visible tab is
  never closed, so a control room screen keeps running untouched. The cost this
  removes is mostly the browser's: `/live/` is proxied over HTTP/1.1 from the
  same origin as Dashboards, so each forgotten tab holds one of the six
  connections that origin is allowed. The server reclaims its thread at the next
  keepalive write, up to `SHIFTER_KEEPALIVE_SECONDS` later, not immediately.
- **A reconnect is deduplicated, because the server replays its backlog to
  every new connection.** The page ignores any `_id` at or below the highest it
  has seen, so resuming adds no duplicate row. If the first record after a
  reconnect is not the next `_id`, the page draws a marker row naming how many
  records it never received, and does not count them as a slow-client drop.
- **The stream begins with a `hello` event carrying the server's boot epoch.**
  `_seq` restarts at 0 on every service restart, and this role restarts the
  service whenever a payload file changes. The watermark above would then reject
  the whole new sequence and leave an open page reading *live* with nothing
  arriving. A changed epoch clears the watermark and draws a restart marker. The
  epoch is also reported by `/healthz`.
- **A full per-viewer queue drops the oldest record, not the newest.** The
  obvious `put_nowait` behaviour is to refuse the incoming record, and that is
  what this server used to do. It is the wrong end: under a sustained burst the
  queue of a slow viewer stays full, so that viewer keeps receiving the 2000
  oldest records and falls permanently behind the live edge, never catching up.
  Dropping the oldest costs the same number of records and keeps the viewer at
  the edge, which is the only part of a burst anybody is watching for. The
  sequence numbers make the loss visible either way.
- **The browser never queries OpenSearch directly.** It could — the cluster
  speaks HTTP and the page is JavaScript. It does not, because that needs CORS
  on the cluster and a credential shipped to every viewer. Proxying through the
  lane server keeps one caller, one credential, one origin, and one place to put
  a row ceiling on what a viewer may ask for.
- **A message search with `%` or a regular expression is refused without a time
  range.** Both compile to a term-dictionary scan on `message.keyword`. On
  `origin_host` that is a few hundred terms and free; on `message` across the
  archive it is tens of millions, and it is the one query shape in this page that
  can hurt the cluster. The server refuses it with a sentence saying what to do,
  rather than accepting it and being slow. A plain phrase is never refused — that
  runs as `match_phrase` on the analysed field and uses the index properly.
- **Paging is `search_after`, not `from`/`size`.** `from` is capped by
  `index.max_result_window` at 10 000, which is below `shifter_query_max_rows`.
  `search_after` has no such ceiling. It sorts on `@timestamp` then `ingest_time`
  and carries the last row's sort values forward. There is no point-in-time
  handle, so a document indexed between two pages can in principle shift the
  window; the page therefore drops any row whose `_id` it already holds, and the
  default one-hour range means the window is nearly always closed anyway.
- **Assets carry an `ETag` and `max-age=600`; the page shell carries neither.**
  A warm reload on a phone is 300 bytes and four `304`s. The ETag gets a `-gz`
  suffix when the body is compressed, because a gzipped and a plain response are
  different representations and a cache between us and the browser is entitled to
  hold both.
- **Every response over 1 kB is gzipped, and the stream never is.** A
  five-thousand-row query is 2.4 MB of JSON and 216 kB gzipped — an 11.6-fold
  cut, which on a phone connection is the difference between 12.7 seconds and
  1.1. `_send` compresses when the client asked for it; `stream()` writes its own
  frames and is deliberately left alone, because a compressed Server-Sent Events
  stream would buffer and stop being live. `SHIFTER_GZIP_MIN_BYTES` (default 1024)
  is the threshold, and is a server environment knob rather than a role
  variable — there has been no reason to change it per site.
- **`shifter_token` is empty by default.** An empty value makes the unit omit
  `SHIFTER_TOKEN`, and the server then accepts any POST from an address the
  firewall let through. The firewall is the boundary.
- **The server's memory is `shifter_replay_rows`, not
  `shifter_buffer_rows`.** One list holds the newest `shifter_replay_rows`
  records and is trimmed to it; that same list is the backlog a new viewer is
  sent. `shifter_buffer_rows` is the *browser's* window. The unit also passes
  it as `SHIFTER_BUFFER_ROWS`, which the server reads into a constant and does not
  use.

## Role variables

Values the role owns. Override any of them in `group_vars` to change them
site-wide, or in `inventory.yml` for one group or host.

| Variable | Default | Meaning |
|---|---|---|
| `shifter_service_name` | `alice-shifter` | systemd unit name. |
| `shifter_script` | `/opt/alice-ingest/shifter.py` | Installed server. See couplings. |
| `shifter_static_dir` | `/opt/alice-ingest/live` | Document root. Passed to the unit as `SHIFTER_STATIC_DIR`. |
| `shifter_cockpit_url` | `/app/dashboards` | The "back to the cockpit" link in the page. Relative on purpose — the reverse proxy in front of Dashboards is on another host and another scheme. |
| `shifter_bind` | `0.0.0.0` | `SHIFTER_BIND`. All interfaces, because the collectors reach it across the network. |
| `shifter_token` | `""` | `SHIFTER_TOKEN`. Empty means no shared secret on the ingest path. |
| `shifter_buffer_rows` | `10000` | Rows one browser tab holds and filters over. Rendered into the page as `bufferRows`; also passed to the unit as `SHIFTER_BUFFER_ROWS`, which the server does not act on. |
| `shifter_replay_rows` | `500` | The server's whole in-memory buffer, and therefore what a viewer who connects mid-stream is sent, so a fresh page is not blank. |
| `shifter_client_queue_max` | `2000` | Per viewer. A full queue drops its **oldest** record, never the incoming one, so a viewer that falls behind stays at the live edge instead of lagging a fixed 2000 records forever. |
| `shifter_opensearch_url` | control host, `opensearch_http_port` | `SHIFTER_OS_URL`. Empty disables the query lane; the live lane is unaffected. |
| `shifter_opensearch_indices` | `infologger,application-logs-central` | `SHIFTER_OS_INDICES`. What the query lane searches. Matches what reaches the live lane, so both halves of the page show the same universe. |
| `shifter_opensearch_user` / `_password` | `""` | `SHIFTER_OS_USER` / `SHIFTER_OS_PASSWORD`. Basic auth, omitted from the unit when the user is empty. |
| `shifter_opensearch_verify` | `true` | `SHIFTER_OS_VERIFY`. Set false only for a self-signed cluster certificate. |
| `shifter_query_default_rows` | `5000` | What one `Query` press returns. Rendered into the page as `queryLimit`. |
| `shifter_query_max_rows` | `20000` | `SHIFTER_QUERY_MAX_ROWS`. The ceiling the server clamps any requested limit to. |
| `shifter_query_page_rows` | `500` | `SHIFTER_QUERY_PAGE_ROWS`. Rows in one page. The page pulls the next one as the shifter scrolls up. |
| `shifter_hidden_grace_seconds` | `120` | How long a browser tab may sit in the background before the page closes its own stream. A visible tab is never closed. `0` turns the behaviour off. Rendered into the page shell as `hiddenGraceSeconds`. |
| `shifter_memory_high` | `192M` | `MemoryHigh` on the unit. |
| `shifter_memory_max` | `384M` | `MemoryMax` on the unit. |
| `shifter_allowed_client_addresses` | `[]` | Addresses allowed through the firewall to the lane port. The playbook supplies it. |

### Variables the role requires but does not own

These are site-wide. They are deliberately **not** duplicated into this role's
defaults, because a second copy is a second place to change one value.

| Variable | Owner | Used for |
|---|---|---|
| `shifter_port` | `group_vars/all.yml` | The listen port, the firewall rule and both health probes. The `collector` role writes the same number into its output. |
| `shifter_ingest_path` | `group_vars/all.yml` | `SHIFTER_INGEST_PATH`. The `collector` role writes the same path into its output. |
| `shifter_enabled` | `group_vars/all.yml` | Read by the play that calls this role and by the `collector` role. The role itself never reads it. |
| `shifter_host` | `group_vars/all.yml` | Read by the `collector` role only. It names the `shifter` inventory group, so it cannot be a role default. |
| `alice_app_root` | `group_vars/all.yml` | `/opt/alice-ingest`, the parent of the static directory. Shared with every other alice service. |

`shifter_allowed_client_addresses` replaces an inline
`groups['workers'] + groups['control']` expression. A role default must not name
an inventory group, so the playbook resolves the addresses and passes the list.
An empty list is valid and means no firewall rule is opened, which is what the
default gives a run outside this repository.

## Prerequisites

| Prerequisite | Provided by | What breaks without it |
|---|---|---|
| `firewalld` installed and running | `common` role | The firewall task fails. `ansible.posix.firewalld` needs the daemon up to apply an immediate rule. |
| `python3` present | base image | The unit's `ExecStart` is `/usr/bin/python3`. The server has no third-party dependency. |

Nothing else. The role does not need OpenSearch, Dashboards or the collectors to
be up. It is deliberately the one path that survives a dead cluster.

## How to use it

In a playbook, against the live lane host:

```yaml
- name: Live lane — the standalone log viewer, off the control host
  hosts: shifter
  become: true
  roles:
    - role: shifter
      when: shifter_enabled | bool
      vars:
        shifter_allowed_client_addresses: >-
          {{ (groups['workers'] + groups['control'])
             | map('extract', hostvars, 'ansible_host') | unique | list }}
```

- **Run it before the `collector` role.** The collectors are configured to push
  to this port. The two health proofs at the end of this role are what make that
  order safe.
- **The role is idempotent.** It restarts the service only when the server, the
  page assets, the rendered page shell or the unit file changed.

## Couplings

- **`shifter_port` and `shifter_ingest_path` are shared with the
  `collector` role.** The collector's HTTP output writes both numbers into
  `collector.yaml`. Change them in `group_vars/all.yml`, which both roles read.
  Changing them here alone gives a lane that listens where nothing pushes.
- **The severity table is a second copy of the collector's normalization.**
  Records that go to OpenSearch are normalized by the `alice-add-ingest-time`
  ingest pipeline; the lane bypasses OpenSearch, so `shifter.py` carries the
  same table. A severity added on one side and not the other shows here as
  `unknown`.
- **`shifter_script` and `alice_app_root` must stay consistent.** The
  script path is a literal, not `{{ alice_app_root }}/shifter.py`, so
  moving the app root moves the static directory but not the server file.
- **The four `register` names and the restart condition change together.**
  Adding a payload task without adding its `register` to the `state:`
  expression gives a file that is installed but never picked up.
- **`shifter_buffer_rows` is rendered into the page shell.** The browser trims
  its table to it. The server's own trim is `shifter_replay_rows`; the two are
  separate windows and are allowed to differ.
- **`shifter_allowed_client_addresses` only ever adds.** firewalld keeps a
  permanent rule once given one, so removing an address does not close the port
  on a host that already ran. Closing it means `state: disabled` or a fresh
  provision.

## Upstream roles rejected

No upstream candidate was looked for and none applies. The role installs one
file this repository wrote, one page this repository wrote and one unit. There
is no third-party product here to be managed by a community role.

## Used by

- `playbooks/site.yml`, play "Live lane — the standalone log viewer, off the
  control host", against `shifter` — the only caller.
