# presentation

HTML + CSS deck for the ALICE Collaboration Board, Wednesday 19 August 2026.
Four slides so far: title, outline, InfoLogger today, why it must change.

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
the `d-` classes in `css/deck.css`; `is-hot` turns a box red.

The InfoLogger topology on slide 3 is redrawn from the official architecture
figure in `AliceO2Group/InfoLogger`, `doc/infoLogger_architecture.png`, which is
also the figure in Thanasis's EPN presentation (see `docs/ARCHITECTURE.md`).

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

## Export to PDF

Print from Chrome. The `@media print` rule sets a 1600 × 900 page with no margins.
Turn on "Background graphics".
