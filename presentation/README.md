# presentation

HTML + CSS deck for the ALICE Collaboration Board, Wednesday 19 August 2026.

The project's official name is **Modern Logging Platform with Machine Learning
for HPC**. It is the title, the running head on every slide and the `<title>`
tag. Do not shorten it and do not reintroduce "Scalable logging for ALICE O2".

Sixteen slides: title, outline, logging today, what we want to build, our
suggested architecture, federated search, why Fluent Bit, inside the collector,
why OpenSearch, how we use OpenSearch, deployment with Ansible, the interface,
log anomaly detection, anomaly detection on metrics, what is not built, thank
you.

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

The outline groups the fourteen content slides into four parts, and each part
names the slides it covers: 01 the problem, and the design (slides 3-6),
02 choosing the tech (7-11), 03 what people see, and what watches it (12-14),
04 what is next (15). "Choosing the tech" exists only here: there is no divider
page and no running section mark anywhere else. Change the outline whenever a
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
`roles/collector/templates/collector.yaml.j2` — hence "application-local" and
"application-central". The **infologger** input is not split at all and goes to the
storage tier whole, which is why it rides the second arrow unconditionally.

The index names on the slides are the ones the code ships —
`application-logs-local-<node>` and `application-logs-central`. `deploy/` carries
the same two names, across 35 files that are not READMEs. Slides 5, 9, 10, 12 and
15 all use them. The deck no longer runs ahead of the code, so a command read off
a slide works as written, and a slide may be paired with a screenshot of the real
cockpit: its saved index pattern is titled `infologger,application-logs-*`.

Slide 4 states the reasoning. Its claims come from `deploy/README.md` section 2
(severity over source), item 4 (the `node.processors` cap and the 128-core EPN
node), item 6, and section 1 (storage quorum of two, no collector or producer on
that tier).

Slides 3 to 15 were each grounded in `deploy/` before they were drawn, and every
number on them carries a `file:line` citation recorded in the drafting notes.
Slide 7 is the only one that also cites sources outside the repository; they are
named in its entry below.

- **Slide 3, logging today.** Rebuilt and widened. The point Lubos made is that
  we are not replacing InfoLogger, we are replacing more than that, so the slide
  now opens with what a node actually writes: application messages through
  libInfoLogger or piped stdout, log files on disk including ten named O2 process
  logs, the machine's own journal, and host metrics. A red brace under the middle
  two says **neither of these reaches InfoLogger**. The InfoLogger topology then
  runs underneath it, unchanged, as one road out of four.
  - **Host metrics are drawn grey and outside the brace**, with the note that
    monitoring already collects them. ALICE O2 runs Telegraf for machine and
    service metrics, so putting metrics in the "goes nowhere" bracket would have
    been wrong and correctable from the floor.
  - **The limits of today live here now**, in band 05, because the slide that
    used to carry them was merged away. One SQL table, ten thousand rows, lines
    not counts, archives by hand. They are stated flatly as facts about a system
    built for a different era. The argument is made positively on slide 4.
  - **"No counts over time" is scoped to infoBrowser**, not to InfoLogger as a
    whole, because the server has a windowed statistics socket. The slide says
    "infoBrowser lists messages. It draws no counts over time."
  - **The ten-thousand-row cap is attributed to infoBrowser by name**, because
    the limit is configurable in ILG.
  - Where the journal and the on-disk files actually go at ALICE today is not
    publicly documented. The slide claims only that they do not reach InfoLogger.
    Do not add "stays on the node" or a MONIT destination without a source.
- **Slide 6, federated search.** New. Lubos asked for it and considers it a big
  feature, so it is framed as a feature: **one box searches the whole farm**. Five
  bands: ask once and every node answers; one cluster and not many; data stays
  where it was written; avoid fanout where we can; we push and we do not poll.
  A soft caution band at the foot carries what one cluster costs us.
  - **The slide does not claim we evaluated and rejected cross-cluster search.**
    The repository records no such decision. It says we run one cluster, that the
    other way is many clusters joined by cross-cluster search, and that each extra
    cluster is one more thing to upgrade and run. Do not upgrade that to a
    rejection.
  - **"Many sets of credentials" is not used as a reason.** The security plugin is
    off, so there are no in-cluster credentials today and the floor could say so.
  - **"Most questions never fan out" is not claimed**, because it is not measured.
    The three avoidance mechanisms are shown instead, and the default search
    pattern's fanout is put honestly in the caution band.
  - **Fanout is defined in red on first use**: one query that asks every machine.
    It takes as long as the slowest machine and costs more as the farm grows.
  - The pinning mechanism is drawn from `register_node.sh`: the machine sets
    `node.attr.box = node-01`, the index demands `require.box = node-01`, and that
    shard never leaves node-01.

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
  - **Telegraf is the fourth row, and it is marked A DIFFERENT JOB, not rejected.**
    Go, no vendor figure, InfluxData, MIT rather than Apache 2.0. It is a metrics
    agent and a good one; ALICE O2 already runs it for machine and service
    metrics, which is confirmed by ALICE's own papers (EPJ Web of Conferences 245,
    01042 and ICALEPCS 2019 TUDPP01). It emits measurements in line protocol — a
    name, tags, numbers and a clock — and a log line is not that shape. Reject it
    on the job, never on quality. There is no Telegraf logo in `assets/`, so the
    name is set in type, as Splunk is on slide 9.
    - Do **not** write "Telegraf cannot read logs". It can tail a file. The
      argument is the shape of what comes out, not the input.
    - Do **not** widen the claim to CERN. It is confirmed for ALICE O2 only. CERN
      IT's public MONIT documentation names Fluent Bit, Node Exporter and
      Collectd, and zero of nine MONIT pages mention Telegraf.
  - **Band 03 renames the disk buffer, and this was a specific instruction.** It
    is a **hiccup safety layer, not durability**. Every input writes to a local
    disk buffer on the worker first; it survives a restart of Fluent Bit and a
    short network outage; it does **not** survive losing the node, and those
    records are gone. One red line then teases Kafka as the real durability, on
    the roadmap. Keep both halves — saying only the good half is what was wrong
    before.
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
- **Slide 4, what we want to build.** Not a prose list. It is an asymmetric board:
  twelve columns, three rows, six cells of deliberately unequal size, separated
  by hairlines rather than gaps. Two cells carry the weight — the red **no bulk
  over the wire** cell and the full-height **split by severity** cell — and four
  are quiet. The labels are the argument, so they are set as words a board member
  can repeat: DISTRIBUTED, DURABLE, WORKERS STAY CHEAP, FAULT TOLERANT. The
  six icons are hairline geometry drawn here, in one stroke weight; none of them
  is a brand mark. The trade stays on the slide: losing a worker costs that
  worker's own info logs, and we took it. Layout lives in `.stage--dboard`,
  `.d-board` and `.d-cell` in `css/deck.css`; the cells are placed by
  `is-c1` to `is-c6`.
  - **This slide absorbed "why it must change", which no longer exists.** Lubos
    asked for the two to merge and for the tone to be positive: what we gain, not
    what is broken. The concrete limits of today moved backwards to slide 3 and
    must not be restated here.
  - **It now runs before the architecture, framed as the objective.** It states
    the six properties the platform must have; slide 5 then shows the machines
    that meet them.
  - **Its CSS delta is scoped to `.stage--buys`**, a third class on this slide's
    own stage div: `<div class="stage stage--dboard stage--buys">`. Remove that
    class and the whole delta stops applying. It is scoped on purpose, because
    `stage--dboard` is a shared board style.
  - **DURABLE names its own exclusion in the same sentence**: "Durable means the
    storage tier, not a worker's disk buffer." That is not padding. Slide 7 spends
    a whole band renaming the worker's buffer, and the two must not contradict.
  - **Six cells, not seven.** The board now has about 525px where the six cells
    were designed for 385px, and the extra went to air and larger icons. Adding a
    seventh cell takes that back.
  - The deleted slide's date block (`.deadline`, "LHC switched off 29 June 2026 /
    Run 4 first beam June 2030") went with it, and the now-unused `.deadline` rule
    was removed from `deck.css`. Those two dates are currently nowhere in the deck.
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
- **Slide 14, anomaly detection on metrics.** One slide, replacing two. Three bands: where
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
- **Slide 11, deployment with Ansible.** Not the play order. The play ladder was
  removed because a Collaboration Board does not want a play list. Band 01 is the
  same comparison grammar as slide 7, on three questions: an agent on every
  machine, when it runs, and whether it also creates the machines. Band 02 is four
  design decisions with hand-drawn icons.
  - **Puppet is treated fairly and the caption concedes the point.** Puppet with
    Foreman is the CERN standard for managed machines, and it is probably right
    for the farm; it is wrong for five machines one person deploys. Saying that
    out loud costs nothing and buys credibility. Do not sharpen it.
  - No Makefile and no `make` command appear anywhere on the slide.
- **Slide 15, what is not built.** Five items in the order we would build them,
  as a table with unequal rows, the Kafka row at twice the height and in red.
  Kafka for durability first, then more log types, then anomaly detection on the
  log text itself, then the shifter's own view, then authentication inside the
  cluster.
  - **The prose inside each cell is now bullets**, at Lubos's request, so a glance
    lands. The uneven-row table stays and the Kafka row stays double height and
    red. KRaft is defined on the slide: Kafka keeping its own cluster state, with
    no separate coordination service beside it.
  - **Row 2 cross-references slide 3 and row 3 cross-references slide 13.** Both
    say a slide number out loud. If the running order changes, both strings must
    change with it.
  - **Three earlier items were deleted and must not come back**: "the names are
    ahead of the code", "no alert reaches a person", and "the model budget is
    unproven". The first is an internal chore and reads as an admission of
    sloppiness to a room that does not care about index naming.

- **Slide 13, log anomaly detection.** New, and marked COMING SOON in a solid red
  badge. Everything the platform runs today watches numbers about the logs. Nothing
  reads the words in the line. Lubos considers the log text to be the real work,
  and it is one of the project's next focuses, so it gets its own page ahead of the
  metrics one.
  - **All three routes are shown as equal, and none is chosen.** Templating alone,
    k-NN alone, and the two fused. This is deliberate: the choice is genuinely not
    made, and the slide says so twice. Do not quietly pick one.
  - **The trade-offs come from `docs/explained/ANOMALY_DETECTION.md`, verbatim in
    substance.** Templating alone: the signal runs out, because software can only
    print the messages in its source, so once every template has been seen it stops
    firing. k-NN alone: rejected on cost, every line through a model at ingest and
    millions of vectors in memory already rationed. Fused: it fixes the cost and
    inherits some of the saturation, and that document names it as still open.
  - **The pipeline is push-based with little work on the worker**: extract the
    template and count it, which needs no model; push template counts and any
    unseen template upward; the storage tier holds the vocabulary and does the
    scoring. Nothing polls the worker.
  - Nothing on this slide is built. If the badge is ever removed, the slide is a
    lie.
- **Slide 14 opens with a scope line, and it is not an apology.** "This lane
  watches the numbers about the logs: counts, error rates and lag. It does not
  read the log text. The log text has its own lane, on the page before." The
  slide moved to second-last at Lubos's request; the content did not change,
  because it is built and it runs.

Every marker `id` in the deck must stay unique, because all sixteen slides live
in one document. Currently: `a9b`, `a10n`, `a12`, `mk03a`, `mk03s`, `mk04a`,
`mk05a`, `mk08a`, `mk08b`, `mk13a`, `mk13r`.

## Template geometry, revised 18 August 2026

The template used to eat 40% of every slide. Lubos said so and he was right. It
now eats about 25%, and the room that freed went to the figures, not to type.
These six numbers are the change. Do not put them back.

| Rule | Was | Is | Why |
|---|---|---|---|
| `.slide` padding | `8cqh 7cqw` | `5cqh 5cqw` | the biggest single win, 54px of height |
| `.slide--title` padding | — | `6cqh 7cqw` | the title slide keeps its old air; only content slides are tight |
| `.masthead` padding-bottom | `3cqh` | `2cqh` | with `.slide--title .masthead` restoring `3cqh` |
| `.slide--content .masthead img` | `7.5cqh` | `5.5cqh` | the running logo is a mark, not a headline |
| `.slide--content .stage` padding | `5cqh 0` | `1.5cqh 0` | the stage was double-padding inside an already padded slide |
| `.slide--content .stage` columns | `30cqw 1fr` | `24cqw 1fr` | 35% of the width for two words was the horizontal version of the same waste |
| `.keyfacts` font-size | `1.35cqh` | `1.70cqh` | 12px to 15px, still under the lede's `1.95cqh` so it stays a caption |

Content height went from about 535px of 900 to about 675px. The figure column
went from about 51cqw to about 61cqw, and because every figure is an SVG that
keeps its aspect ratio, figures grew about 20% in both directions on their own.
That is the mechanism: **the vertical room is spent by widening the figure**, not
by any height rule. If you ever narrow the figure column again, the slides will
not just get narrower, they will get shorter and leave slack.

Type inside figures was deliberately **not** enlarged. The instruction was more
air and bigger pictures, not bigger words.

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
Turn on "Background graphics". A correct export is **sixteen pages**.

Headless, for a check:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=deck.pdf --virtual-time-budget=8000 \
  "file://$PWD/index.html"
```
