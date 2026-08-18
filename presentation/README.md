# presentation

HTML + CSS deck for the ALICE Collaboration Board, Wednesday 19 August 2026.
Fifteen slides: title, outline, InfoLogger today, why it must change, our
suggested architecture, why this design, why Fluent Bit, inside the collector,
why OpenSearch, how we use OpenSearch, anomaly detection, the interface,
deployment with Ansible, what is not built, thank you.

## View

```
open presentation/index.html
```

Keys: `→` / `←` change slide, `f` goes fullscreen.

## Slide types

- `slide--title` — full logo, date, two-weight title, credits along the bottom,
  and the Nataraja plate in the right column.
- `slide--content` — small logo, running head, red tick, section title and lede in
  the left column, content in the right. Slide 2 shows it as an outline list.
- `slide--closer` — the last slide only. A full-bleed photograph under everything,
  with the thanks set on a solid paper card over the sky.

To add a slide, copy the `slide--content` section. The footer folio numbers itself.
On the outline list, `data-now` on an `<li>` marks the current section in red.

The outline groups the twelve content slides into seven parts, and each part names
the slides it covers: 01 the problem (slides 3-4), 02 the architecture (5-6),
03 ingest at the edge (7-8), 04 storage (9-10), 05 anomaly detection (11),
06 operations (12-13), 07 what is not built (14). Change the outline whenever a
slide is added, removed or renamed, or it stops describing the deck. The closing
slide is deliberately absent from it, because it carries no argument.

Each outline row carries a `.mark`, a 24 by 24 inline SVG drawn in strokes only:
a funnel for the single-server bottleneck, two rows of boxes for the two tiers, a
routed split for the collector, a cylinder for the indices, a flat line with one
red point above it for an anomaly, a panelled window for the interface, and a
dashed empty box for the work not done. They inherit `--ink-mid`, turn red on the
`data-now` row, and are `aria-hidden` because the row text already says it.

Three right-column blocks exist: `.agenda` (outline), `.figure` (an inline SVG
diagram with a caption), and `.findings` (claim plus evidence). Diagram parts use
the `d-` classes in `css/deck.css`; `is-hot` turns a box red, `is-soft` greys it
down, and `is-start` / `is-end` change the text anchor. `.keyfacts` is the red
mono block under a lede. `.d-elabel` is the small stroked box an edge label sits
in, so the label reads as part of its arrow rather than floating beside it: the
line runs into one side and out of the other.

The InfoLogger topology on slide 3 is redrawn from the official architecture
figure in `AliceO2Group/InfoLogger`, `doc/infoLogger_architecture.png`, which is
also the figure in Thanasis's EPN presentation (see `docs/ARCHITECTURE.md`).

Slide 5 is drawn from `deploy/` itself, not from prose. Node roles come from
`roles/opensearch/templates/opensearch.yml.j2`, service placement from
`inventory.yml` and `playbooks/site.yml`, the storage index settings from
`roles/opensearch_bootstrap/templates/templates.sh.j2` with
`log_primary_shards_storage` in `group_vars/all.yml`, and the per-worker info
index from `roles/opensearch_local_index_registration/files/register_node.sh`. Note that
`deploy/README.md` section 1 still draws the storage indices as 3 shards; the
templates say 1, and section 5 agrees with the templates.

Slide 5's arrow labels name the traffic, not the index. Only the **dds** and
**stdout** inputs are split by severity in
`roles/collector/templates/collector.yaml.j2` — hence "application-info" and
"application-other". The **infologger** input is not split at all and goes to the
storage tier whole, which is why it rides the second arrow unconditionally.

The index names are the **new** ones everywhere they appear —
`application-logs-local-<node>` and `application-logs-other`. `deploy/` has not
been renamed and still ships `generic-log-info-<node>` and `generic-log-other`,
across 35 files that are not READMEs. Slides 5, 9, 10, 12 and 14 all carry the new
names; slide 14 says so out loud, as an outstanding item. The deck is deliberately
ahead of the code; rename the templates before anyone runs a command off a slide.
Do not pair any slide with a screenshot of the real cockpit until the rename
lands, because its saved index pattern is literally titled `infologger,generic-log-*`.

Slide 6 states the reasoning. Its claims come from `deploy/README.md` section 2
(severity over source), item 4 (the `node.processors` cap and the 128-core EPN
node), item 6, and section 1 (storage quorum of two, no collector or producer on
that tier).

Slides 7 to 14 were each grounded in `deploy/` before they were drawn, and every
number on them carries a `file:line` citation recorded in the drafting notes.
Slide 7 is the only one that also cites sources outside the repository; they are
named in its entry below.

- **Slide 7, why Fluent Bit.** The comparison is sourced, not asserted, and it
  argues from maturity rather than from megabytes. Language and memory figures for
  Fluent Bit and Fluentd are the vendor's own table at
  [docs.fluentbit.io](https://docs.fluentbit.io/manual/about/fluentd-and-fluent-bit)
  — "approximately 450 KB" against "greater than 60 MB". The fifteen-billion
  deployment count and the sentence "Fluent Bit is a CNCF graduated project under
  the Fluent organization" are the project's own claims on
  [fluentbit.io](https://fluentbit.io/); its copyright line reads 2015 onwards,
  which is the decade. Fluentd's graduation date, April 2019, is from
  [cncf.io](https://www.cncf.io/projects/fluentd/) — it is also the older of the
  two, which the slide says. Vector's row says **no vendor figure** on purpose:
  independent benchmarks disagree on which of the two is smaller under load, so no
  number is put on a slide. Its governance cell is the fact that actually decides
  the choice, and its own lockup reads "VECTOR BY DATADOG". The Logstash sentence
  in the caption comes from `elastic/logstash`, `config/jvm.options`, which ships
  `-Xms1g -Xmx1g`.
  - **The red row is the argument.** Do not reduce it to a tag: the whole Fluent
    Bit row is a `d-card is-hot` and its reason text is red, because the point of
    the slide is that one candidate was chosen and why.
  - **The asterisk is load-bearing.** `CNCF*` on the slide is expanded in the
    caption for an audience that does not live in this stack. If the caption is
    ever trimmed, the asterisk goes with it.
  - The 450 KB is the vendor's idle figure and the caption says so, because slide 8
    bounds our own collector at 384 MB and the two must not read as a contradiction.
- **Slide 8, inside the collector.** Deliberately an **overview, not the
  internals**. It draws the five stages Fluent Bit puts a line through — input,
  tag, filter, retag, output — in plain words, with one concrete example under
  each. It names no index, no parser and no port. An earlier version drew all four
  tag lanes with their parsers and outputs; that is the right level for
  `roles/collector/README.md` and the wrong level for a Collaboration Board. If it
  needs to grow, add a back-up slide rather than reload this one.
  - **Retag is red because it is the design decision.** The split is by severity,
    not by source, and that is the one thing on the slide an architect would argue
    with.
  - **Band 02 is the durability layer**, drawn rather than written: the collector
    keeps reading, a 256 MB on-disk buffer per lane with ten retries absorbs the
    outage, and the link to OpenSearch carries a red cross. The red brace under the
    buffer gives the window — 50 seconds at best, 109 minutes at worst, wide
    because the backoff is jittered — and the note beside it says what happens
    after: the record is gone, because there is no second destination. All of it is
    from `roles/collector/templates/collector.yaml.j2` and
    `roles/collector/README.md`.
  - The memory envelope in the left column comes from
    `roles/collector/defaults/main.yml` (`MemoryHigh` 384 MB, `MemoryMax` 768 MB)
    and the same template (`storage.max_chunks_up: 64`). Its fifth row is **blank
    on purpose**: no file in the repository holds a measured resident-set figure,
    and `roles/collector/files/fb_health.py` collects eight counters with no memory
    field. Fill it in after the soak, and nothing else on the slide has to move.
  - **The figure has no caption on purpose.** It had one; it repeated the diagram.
- **Slide 6, why this design.** Not a prose list. It is an asymmetric board:
  twelve columns, three rows, six cells of deliberately unequal size, separated
  by hairlines rather than gaps. Two cells carry the weight — the red **no bulk
  over the wire** cell and the full-height **split by severity** cell — and four
  are quiet. The labels are the argument, so they are set as words a board member
  can repeat: DISTRIBUTED, DURABLE, WORKERS STAY CHEAP, ONE MACHINE MAY DIE. The
  six icons are hairline geometry drawn here, in one stroke weight; none of them
  is a brand mark. The trade stays on the slide: losing a worker costs that
  worker's own info logs, and we took it. Layout lives in `.stage--dboard`,
  `.d-board` and `.d-cell` in `css/deck.css`; the cells are placed by
  `is-c1` to `is-c6`.
- **Slide 9, why OpenSearch.** Built to the same grammar as slide 7: three axes
  (licence and governance, what it can answer, what it costs us to run), a
  full-width red row for the choice, and the reason line inside that row. Rows
  are OpenSearch, Elasticsearch, ClickHouse and Grafana Loki, each with the real
  mark. Splunk is one typeset line because no Splunk logo was fetchable, and Loki
  has no separate mark, so the Grafana wordmark carries that row and the word
  Loki is set in type. Band 02 answers the second question — what OpenSearch
  gives us that InfoLogger does not — and names what the InfoLogger database
  cannot do beside the MySQL wordmark.
  - **"Detection included, free" rests on the deploy, not on a vendor page.** No
    vendor page lists the free plugin bundle explicitly.
    `roles/opensearch/tasks/main.yml` asserts seven plugins are present after the
    stock package installs, and that assertion passes.
- **Slide 10, how we use OpenSearch.** The separation IS the slide. The local
  half and the replicated half are two panels with an empty gap between them, and
  the arrows that cross it are labelled. Do not close that gap to save space.
  - **The durability sentence has its own red band.** "One storage machine may
    die and we lose nothing" is the line the room will remember, so it is not a
    bullet inside a panel.
  - **Shards are drawn, not counted in prose.** Three squares per storage
    machine, one filled red for the writable slice and two outlined for copies.
  - **The templates set one primary shard on the storage tier today**, so writes
    do not spread across the three machines yet; `deploy/README.md` calls it a
    funnel. The slide says the honest version, and adds that farm hardware gets a
    slice per machine.
  - **Retention is deliberately the smallest thing on the page** — one of three
    watch items, beside the JVM heap and the memory outside it. The old retention
    timeline was the whole slide and it was the least important fact on it. Do not
    bring it back.
- **Slide 11, anomaly detection.** One slide, replacing two. Three bands: where
  the numbers come from, three lanes that read them, and hits folding into one
  alert. It names no detector and lists no monitor. Counts only.
  - **Push versus poll is stated exactly.** Every worker pushes its own counters
    into the cluster; one poller asks the cluster and the dashboards for their own
    health and notices who has gone quiet. Both halves are on the slide because
    only saying "push" would be wrong.
  - The Random Cut Forest scatter is animated with CSS keyframes only — no
    JavaScript, no SMIL. Three guards keep it honest: the base `.d-cut` rule
    paints every cut solid, the motion lives inside
    `@media (prefers-reduced-motion: no-preference)`, and an `@media print` block
    forces the finished state so a print-to-PDF cannot catch a half-drawn frame.
    Each cut path carries `pathLength="100"` so one keyframe rule serves lines of
    different lengths.
  - **The word is "alert", never "page".** A physics audience reads "page" as a
    sheet or a screen.
- **Slide 12, the interface.** Renamed from "two ways to look", which counted the
  surfaces and undercounted them. Four rows: the cockpit for the people who
  maintain the platform, the shifter's view marked NOT BUILT YET, the live lane in
  red carrying the React mark, and Discover for a physicist with a real question.
  Each row says what it answers and who opens it. Port numbers, panel counts,
  saved-object counts, heap figures and row limits were all cut on purpose: they
  are maintenance trivia and they made the slide dense.
- **Slide 13, deployment with Ansible.** Not the play order. The play ladder was
  removed because a Collaboration Board does not want a play list. Band 01 is the
  same comparison grammar as slide 7, on three questions: an agent on every
  machine, when it runs, and whether it also creates the machines. Band 02 is four
  design decisions with hand-drawn icons.
  - **Puppet is treated fairly and the caption concedes the point.** Puppet with
    Foreman is the CERN standard for managed machines, and it is probably right
    for the farm; it is wrong for five machines one person deploys. Saying that
    out loud costs nothing and buys credibility. Do not sharpen it.
  - No Makefile and no `make` command appear anywhere on the slide.
- **Slide 14, what is not built.** Five items in the order we would build them,
  as a table with unequal rows, the Kafka row at twice the height and in red.
  Kafka for durability first, then more log types, then anomaly detection on the
  log text itself, then the shifter's own view, then authentication inside the
  cluster.
  - **Three earlier items were deleted and must not come back**: "the names are
    ahead of the code", "no alert reaches a person", and "the model budget is
    unproven". The first is an internal chore and reads as an admission of
    sloppiness to a room that does not care about index naming.

Every marker `id` in the deck must stay unique, because all fourteen slides live
in one document. Currently: `ah`, `ahs`, `a5`, `a7`, `a7s`, `a9b`, `a10n`,
`a12`.

### Contradictions found in `deploy/` while building these slides

None of these are fixed. The slides follow the code, not the prose.

| Where | Says | Truth |
|---|---|---|
| `deploy/README.md` §1 | storage indices are 3 shards | templates say 1 primary, 2 replicas |
| `deploy/README.md` §4.3 | 14 plays | `site.yml` has 19 |
| `deploy/README.md` §5 | seven seed saved searches, "ALICE Cockpit" | `cockpit.ndjson` ships 12; `gen_cockpit.py` titles it "Maintainer Cockpit" |
| `roles/live_lane/README.md` | server keeps 10 000 rows | `live_lane.py` trims to 500; the 10 000 is the browser |
| `deploy/README.md` | `alertmanager_proven_inhibit_rules` | that variable is gone; the gate reads `causal_edges.json` |

## The title illustration

The title slide carries a drawing of Shiva Nataraja, after the bronze that stands
outside Building 40. It is inline SVG in `index.html`, on a 640 by 720 viewBox, and
it uses the same tokens as the rest of the deck plus three bronzes added to `:root`.

The iconography is bent to the subject. The damaru in the upper left hand is the
drum whose beat makes a log line. The fire in the upper right hand is the one an
expired line goes into, and a red line is rising into it. The open hands catch log
lines flying in through the ring. The ring is a stream that never stops, so the band
under the flames is a dashed stroke that scrolls around it. Apasmara, the demon of
ignorance the dancer stands on, still holds a card marked `grep`. The plaque under
the pedestal reads `$ tail -f /dev/cosmos`, with a cursor that blinks.

Every limb, lock of hair and finger is a filled outline rather than a stroked line,
so it tapers from shoulder to wrist: the outlines were built by offsetting a Bezier
centreline against a width profile and closing the ends with a half-circle. That is
why the path data is long and why it should be edited as geometry, not by hand.

Animation is CSS only and lives entirely inside `@media (prefers-reduced-motion:
no-preference)`. The figure sways about the planted heel, the ring flames flicker on
seven staggered clocks, the drum shakes, the fire flickers, the sash flutters, and
the demon and the cursor blink. The log cards fly along four lanes, each with a
negative `animation-delay` so no lane starts empty. Every card also carries a static
`--p` offset along its lane, which is what places it when the animation does not run
— under reduced motion, and in the PDF export, where `@media print` stops it.

## Design

Swiss editorial: warm white paper, near-black Helvetica, hairline rules, one red
tick that picks up the red of the ALICE octagon. No gradients, no shadows, no
decoration. All type sizes use container units, so the slide keeps its proportions
on any screen or projector.

Tokens live in the `:root` block of `css/deck.css`. Fonts are system fonts only, so
the deck needs no network.

## Files

| File | Purpose |
|---|---|
| `index.html` | Deck markup. One `<section class="slide">` per slide. |
| `css/deck.css` | Tokens, 16:9 frame, title-slide layout. |
| `assets/summer-students-2026.jpg` | The CERN summer students of 2026 outside the Science Gateway. Backs slide 15. From `Summer_Student_Group_2026-2.jpg` in the official group-photo set, resized to 3400 px wide at JPEG quality 72. A photograph, so JPEG, not WebP. |
| `assets/alice-logo.webp` | Official ALICE logo, light backgrounds. In use. |
| `assets/alice-logo-dark-bg.webp` | Official ALICE logo, dark backgrounds. |
| `assets/alice-logo-official.svg` | Same logo as vector, if you prefer it to WebP. |
| `assets/how_to_use_ALICE_logos.pdf` | The collaboration graphic charter. |
| `assets/mysql-logo.webp` | Official MySQL wordmark. In use on slide 3. |
| `assets/mysql-logo-official.svg` | Same wordmark as vector. |
| `assets/fluentbit-logo.webp` | Official Fluent Bit logo. In use on slides 5, 7 and 8. |
| `assets/fluentbit-logo-official.svg` | Same logo as vector. |
| `assets/fluentd-logo.webp` | Official Fluentd logo. In use on slide 7. |
| `assets/fluentd-logo-official.svg` | Same logo as vector. |
| `assets/vector-logo.webp` | Official Vector lockup, "Vector by Datadog". In use on slide 7. |
| `assets/vector-logo-official.svg` | Same lockup as vector. |
| `assets/rabbitmq-logo.webp` | Official RabbitMQ logo with wordmark. In use on slide 7. |
| `assets/rabbitmq-logo-official.svg` | Same logo as vector. |
| `assets/opensearch-logo.webp` | Official OpenSearch logo, symbol plus wordmark. Spare. |
| `assets/opensearch-mark.webp` | The OpenSearch symbol alone. In use on slide 5. |
| `assets/opensearch-logo-official.svg` | Same logo as vector. |
| `assets/ansible-logo.webp` | Official Ansible community wordmark. |
| `assets/ansible-mark.webp` | The Ansible "A" mark alone, on a solid disc. |
| `assets/ansible-logo-official.svg` | Same wordmark as vector. |
| `assets/ansible-mark-official.svg` | Same mark as vector. |
| `assets/kafka-logo.webp` | Official Apache Kafka horizontal lockup. In use on slide 7. |
| `assets/kafka-logo-official.svg` | Same lockup as vector. |
| `assets/elasticsearch-logo.webp` | Official Elasticsearch lockup. In use on slide 9. |
| `assets/clickhouse-logo.webp` | Official ClickHouse lockup. In use on slide 9. |
| `assets/clickhouse-mark.webp` | The ClickHouse bars alone. Spare. |
| `assets/grafana-logo.webp` | Official Grafana lockup, standing for Loki. In use on slide 9. |
| `assets/grafana-mark.webp` | The Grafana sun alone. Spare. |
| `assets/puppet-logo.webp` | Official Puppet logo, classic orange brand. In use on slide 13. |
| `assets/puppet-perforce-logo.webp` | Official Puppet logo, current Perforce brand. Spare. |
| `assets/salt-project-logo.webp` | Official Salt Project logo, current brand. In use on slide 13. |
| `assets/salt-logo.webp` | Official SaltStack wordmark, older brand. Spare. |
| `assets/salt-mark.webp` | The Salt "S" alone. Spare. |
| `assets/terraform-logo.webp` | Official Terraform lockup. In use on slide 13. |
| `assets/chef-logo.webp` | Official Chef logo, stacked. Spare — unreadable at row height. |
| `assets/react-logo.webp` | Official React lockup. Spare. |
| `assets/react-mark.webp` | The React atom alone. In use on slide 12. |

## Where the logo came from

The `ALICE_EPS_Logos.zip` pack on `alice-figure.web.cern.ch`, which holds the
official rainbow logo as EPS, PDF and SVG. Steps used:

1. `pdftocairo -png -transp -r 600` on `RainbowLogos/Rainbow_Logo.pdf`.
2. Crop the transparent margin, scale to 1000 px wide.
3. Encode WebP with `lossless=True, method=6, exact=True` — 148 KB, no pixel loss.

The charter says the octagon and the word ALICE are inseparable, and that on dark
backgrounds the word must be white. Both files here follow that.

Product logos follow the same rule: fetch the real mark, never draw one. The MySQL
wordmark came from `MySQL textlogo.svg` on Wikimedia Commons, rendered to a
transparent PNG in headless Chrome and encoded with the same lossless settings.
Oracle's own `labs.mysql.com` copy answers 403 to a direct download.

Fluent Bit and Fluentd both came from `cncf/artwork` —
`projects/fluentd/fluentbit/horizontal/fluentbit-horizontal-color.svg` and
`projects/fluentd/horizontal/color/fluentd-horizontal-color.svg`. They share a
directory because they are one CNCF project.
OpenSearch came from the project's own brand pack,
`opensearch.org/assets/brand/SVG/Logo/opensearch_logo_default.svg`. That pack
ships no symbol-only file, so `opensearch-mark.webp` is the left-hand symbol cut
out of the same render at the transparent column between symbol and wordmark —
no redrawing.

Ansible came from the project's own `ansible/logos` repository,
`community-logo/Ansible-Community-Logo-RGB-Black.svg` and
`community-marks/Ansible-Community-Mark-Black.svg`. These are the **community**
marks, not the Red Hat product marks. We run the upstream tool, so the community
mark is the correct one and it avoids the product trademark entirely. Both are
near-black (`#161b1f`), which is why they sit so quietly next to the deck's ink.

Vector came from its own repository, `vectordotdev/vector`,
`website/static/img/logos/vector-logo-light.svg` — the light-background variant,
since the deck is on warm white. It is reproduced unmodified, which is why it
reads "VECTOR BY DATADOG": that is the official lockup, and it makes the slide's
governance point without a word of comment. RabbitMQ came from
`rabbitmq/rabbitmq-website`, `static/img/rabbitmq-logo-with-name.svg`.

No Logstash mark is used. Elastic publishes no vector logo that could be fetched
the same way, and drawing a substitute is not allowed here, so slide 7 makes its
Logstash point in the caption instead of in the table.

The comparison slides added eight more vendors, all fetched the same way and all
official. React came from `reactjs/react.dev`,
`public/images/brand/logo_light.svg` and `wordmark_light.svg`. Grafana came from
`grafana/grafana`, `public/img/grafana_text_logo_dark.svg` and
`grafana_icon.svg`. ClickHouse's mark came from `ClickHouse/clickhouse-docs`,
`static/img/clickhouse-logo-mark.svg`. Salt came from `saltstack/salt`,
`doc/_static/salt-logo-full.svg` and `salt-logo.svg`. Elasticsearch, the two
Puppet brands, Terraform, the ClickHouse lockup and Chef came from Wikimedia
Commons. The current Salt Project brand publishes no vector at all, so
`salt-project-logo.webp` came from the vendor's own 1000 px brand PNG in
`saltstack/salt-branding-guide`, and the older SaltStack vector sits beside it.

Two traps. `clickhouse-mark-official.svg` carries an internal dark-mode rule that
flips the bars white if the file is inlined, so use the WebP. Grafana Loki
publishes no separate mark that could be confirmed, so slide 9 sets the Grafana
wordmark and the word Loki in type rather than inventing a Loki logo.

No Splunk mark is used, for the same reason as Logstash: nothing fetchable was
published, and drawing one is not allowed here.

Apache Kafka publishes only raster logos on `kafka.apache.org/logos`, and the
horizontal one there is 117 x 65 — too small to encode without upscaling. The
vector came from Wikimedia Commons, `Apache kafka wordtype.svg`, checked against
the official `kafka-logo-tall.png` before use. Kafka and the Kafka logo are
trademarks of the Apache Software Foundation.

## Export to PDF

Print from Chrome. The `@media print` rule sets a 1600 × 900 page with no margins.
Turn on "Background graphics".
