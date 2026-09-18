# Figure style and toolchain for the loggy report

Every report figure is one standalone SVG file in reports/loggy-report/figures/, rendered to PDF for LaTeX with rsvg-convert. The design is the deck's: Swiss editorial. White ground, near-black text, hairline rules, one accent colour, real logos. Nothing that reads as machine-made: no gradients, no glow, no drop shadows, no dark hero panels, no dot grids, no rounded neon pills.

## Palette (from presentation/css/deck.css)

| Token | Hex | Use |
|---|---|---|
| paper | #fcfcfa | page ground (the PDF page is white, so leave the SVG background transparent) |
| panel | #f1f1ec | a soft tier panel behind a group of cards |
| card | #ffffff | card fill |
| ink | #101010 | text, card strokes, arrows |
| ink-mid | #3a3a36 | secondary text |
| ink-soft | #6b6b66 | labels, monospace annotations, dashed lines |
| rule | #d6d6cf | hairline rules |
| alice-red | #e1251b | the one accent: the hot card stroke, the chosen row, the brace label |
| red-wash | #fdf1f0 | fill of a hot card |
| green | #1c7c46 | only for a "built" status mark, sparingly |

Use the accent once or twice per figure, for the thing the caption is about. Never as decoration.

## Type

- Text: Helvetica Neue, Helvetica, Arial, sans-serif. Title in a card 14px weight 600; subtitle 12px ink-soft; both text-anchor middle inside a card.
- Labels on edges and index names: Menlo, monospace, 10px, ink-soft. Section stage headings: Menlo 11px, letter-spacing 0.18em, uppercase, ink.
- Card: fill card, stroke ink, stroke-width 1.1. Hot card: fill red-wash, stroke alice-red, stroke-width 1.6. Ghost (planned, not built): stroke ink, opacity 0.32 or stroke-dasharray 4 4.
- Line: stroke ink 1.25, arrowhead a small filled triangle marker. Dashed line: ink-soft, dasharray 4 4.
- Hairline: stroke rule 1.
- viewBox 0 0 820 H where H fits the content, so every figure is the same text size when placed at \textwidth (160 mm). Keep text at 10 to 14 px in that box; smaller than 9 px is unreadable in print.

## Mechanics

- Write plain SVG 1.1 with explicit attributes. No CSS classes, no <style> blocks, no var(): rsvg-convert does not read the deck stylesheet. Put every fill, stroke, font-family, font-size and text-anchor on the element or an enclosing <g>.
- Declare xmlns="http://www.w3.org/2000/svg" and xmlns:xlink on the root.
- Use XML entities only: &amp; &lt; &gt; &quot;. Write typographic characters as UTF-8 (’ — ·) not as HTML entities like &rsquo;.
- Logos: never draw one. Use <image href="logos/NAME-official.svg" .../> with a path relative to the figure file; the logos live in reports/loggy-report/figures/logos/ (copied from presentation/assets/*-official.svg). rsvg-convert only loads files in the figure's own directory or below, so the figure must live in figures/ and reference logos/.
- Available logos: alice, ansible (logo and mark), chef, clickhouse (logo and mark), elasticsearch, fluentbit, fluentd, grafana (logo and mark), kafka, mysql, opensearch, prometheus (mark), puppet, puppet-perforce, rabbitmq, react (logo and mark), salt (logo and mark), terraform, vector.
- Render check, mandatory before you finish:
    rsvg-convert -f pdf -o NAME.pdf NAME.svg
    rsvg-convert -f png -w 1600 -o NAME.png NAME.svg
  then open the PNG with the Read tool and look at it. Fix overlaps, clipped text, missing logos, and text that runs outside its card. Delete the PNG when done or leave it; only the SVG and PDF are used.
- Starting points: the deck's diagrams, extracted with inlined styles, are in figures/deck-src/slideNN-K.svg (slide 3 logging today; 5 architecture; 6 federated search; 7 Fluent Bit candidates; 8 OpenSearch candidates; 9 collector memory envelope and the five steps; 10 the two-tier split; 11 Ansible candidates; 12 the four surfaces; 13 log anomaly detection routes; 14 metrics detection lanes; 15 coming soon). Copy what you need; do not edit the deck-src files.

## Content rules

- A figure shows one mechanism. Its caption is a full sentence that states the point, as in "Only the last row is what a user sources."
- Label every arrow with what flows and how (protocol, cadence), in monospace. An unlabelled arrow is a guess.
- Hosts as columns or panels, components as cards, indices as soft grey cards with a monospace name.
- Mark status where the report describes the target design: a solid card is built and running, a ghost card is agreed and not built. Say so in a small legend if the figure mixes them.
- Names follow the report, not the code: the collector, the stamper, the poller, the projector, the receiver, Alertmanager, Dashboards, the shifter view, the storage tier, a worker, the live lane, the bus.
- No Ansible role names, no file names, no version numbers in figures.
