# presentation

HTML + CSS deck for the ALICE Collaboration Board, Wednesday 19 August 2026.
Fourteen slides: title, outline, InfoLogger today, why it must change, our
suggested architecture, why this design, why Fluent Bit, inside the collector,
OpenSearch, anomaly detection, after a detector fires, two ways to look,
deployment with Ansible, what is not built.

## View

```
open presentation/index.html
```

Keys: `→` / `←` change slide, `f` goes fullscreen.

## Slide types

- `slide--title` — full logo, date, two-weight title, credits along the bottom.
- `slide--content` — small logo, running head, red tick, section title and lede in
  the left column, content in the right. Slide 2 shows it as an outline list.

To add a slide, copy the `slide--content` section. The footer folio numbers itself.
On the outline list, `data-now` on an `<li>` marks the current section in red.

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
across 35 files that are not READMEs. Slides 5, 8, 9, 10, 12 and 14 all carry the
new names; slide 14 says so out loud, as an outstanding item. The deck is deliberately
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

- **Slide 7, why Fluent Bit.** The comparison is sourced, not asserted. The
  language, memory and dependency figures for Fluent Bit and Fluentd are the
  vendor's own table at
  [docs.fluentbit.io](https://docs.fluentbit.io/manual/about/fluentd-and-fluent-bit)
  — "approximately 450 KB" against "greater than 60 MB". Vector's row says **no
  vendor figure** on purpose: independent benchmarks disagree on which of the two
  is smaller under load, so no number is put on a slide. Its governance cell is
  the fact that actually decides the choice — Fluent Bit is a CNCF graduated
  project, Vector is stewarded by Datadog in `vectordotdev/vector`, and its own
  lockup reads "VECTOR BY DATADOG". The Logstash sentence in the caption comes
  from `elastic/logstash`, `config/jvm.options`, which ships `-Xms1g -Xmx1g`.
  The 450 KB is the vendor's idle figure and the caption says so, because slide 8
  bounds our own collector at 384 MB and the two must not read as a contradiction.
- **Slide 8, inside the collector.** The five stages are the real pipeline in
  `roles/collector/templates/collector.yaml.j2`: four inputs carrying four tags,
  the parser and Lua filters, the two `rewrite_tag` rules, and five outputs. The
  retag column is drawn as a **bus**, not as one line per row: `dds` and `stdout`
  both feed both families, and the split is by severity rather than by source.
  Do not redraw it as two parallel lines. Rows three and four cross that column on
  a dashed line because nothing retags them. The memory envelope comes from
  `roles/collector/defaults/main.yml` (`MemoryHigh` 384 MB, `MemoryMax` 768 MB)
  and the same template (`storage.max_chunks_up: 64`). Its fifth row is **blank on
  purpose**: no file in the repository holds a measured resident-set figure, and
  `roles/collector/files/fb_health.py` collects eight counters with no memory
  field. Fill it in after the soak, and nothing else on the slide has to move.
- **Slide 9, OpenSearch.** Retention comes from the ISM policies in
  `roles/opensearch_bootstrap/`. All three lanes share one linear time scale, so
  the bulk lane is visibly one seventh the length of InfoLogger. The figure is
  drawn in time and copies only, never in bytes, because no capacity number
  exists yet.
- **Slide 10, anomaly detection.** The Random Cut Forest figure is animated with
  CSS keyframes only — no JavaScript, no SMIL. Three guards keep it honest: the
  base `.d-cut` rule paints every cut solid, the motion lives inside
  `@media (prefers-reduced-motion: no-preference)`, and an `@media print` block
  forces the finished state so a print-to-PDF cannot catch a half-drawn frame.
  Each cut path carries `pathLength="100"` so one keyframe rule serves lines of
  different lengths.
- **Slide 11, after a detector fires.** Grouping keys and windows come from
  `roles/signal_projector/` and `roles/alertmanager/`. The slide never says a
  person is paged, because Alertmanager's only receiver is a loopback webhook.
- **Slide 12, two ways to look.** The arrow directions are the argument: the
  cockpit arrow points down into the cluster because it pulls, the live-lane arrow
  points up out of Fluent Bit because it is pushed. There is deliberately no line
  from the cluster into the live lane. Do not tidy one in.
- **Slide 13, deployment with Ansible.** The seven ladder rows partition all 19
  plays in `playbooks/site.yml` with no double-counting. Row 05 spans four
  inventory groups because the signal re-verify runs on the control host between
  the projector and background plays.
- **Slide 14, what is not built.** Five findings, each evidenced. It is the only
  slide that states the model-budget risk: three days of the archive hold 211,
  214 and 215 distinct hostnames against an assumed 31.

Every marker `id` in the deck must stay unique, because all fourteen slides live
in one document. Currently: `ah`, `ahs`, `a5`, `a7`, `a7s`, `a8s`, `a9`, `a10`,
`a10s`, `a11`.

### Contradictions found in `deploy/` while building these slides

None of these are fixed. The slides follow the code, not the prose.

| Where | Says | Truth |
|---|---|---|
| `deploy/README.md` §1 | storage indices are 3 shards | templates say 1 primary, 2 replicas |
| `deploy/README.md` §4.3 | 14 plays | `site.yml` has 19 |
| `deploy/README.md` §5 | seven seed saved searches, "ALICE Cockpit" | `cockpit.ndjson` ships 12; `gen_cockpit.py` titles it "Maintainer Cockpit" |
| `roles/live_lane/README.md` | server keeps 10 000 rows | `live_lane.py` trims to 500; the 10 000 is the browser |
| `deploy/README.md` | `alertmanager_proven_inhibit_rules` | that variable is gone; the gate reads `causal_edges.json` |

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

Apache Kafka publishes only raster logos on `kafka.apache.org/logos`, and the
horizontal one there is 117 x 65 — too small to encode without upscaling. The
vector came from Wikimedia Commons, `Apache kafka wordtype.svg`, checked against
the official `kafka-logo-tall.png` before use. Kafka and the Kafka logo are
trademarks of the Apache Software Foundation.

## Export to PDF

Print from Chrome. The `@media print` rule sets a 1600 × 900 page with no margins.
Turn on "Background graphics".
