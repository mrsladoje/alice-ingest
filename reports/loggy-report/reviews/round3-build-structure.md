# Round 3 review: build and structure

Build: `tectonic --keep-logs report.tex` completes. The log holds no undefined
reference, no undefined citation, no multiply defined label, no missing file and no
overfull box. The only two box warnings are underfull hboxes in two bibliography
entries with long URLs, which is normal. The only package messages are font
substitution notes and the xdvipdfmx notes about including PDF figures of version 1.7.
`pdfinfo` reports 17 pages, as expected. All 14 keys in `references.tex` are cited, and
every `\cite` key exists. The abstract is 218 words. Every float lands on the page of
its citation or on the next page. Every caption is a full sentence. No page is more
than a third empty. The table of contents page numbers match the body.

## must

None.

## should

### 1. Table 7 does not carry the claim its cross-reference promises

- File: `reports/loggy-report/sections/06-eval-b.tex`, line 3
- Quote: `Templating is built, 31 to 57\,\% cheaper per family than its first version, with identical output (Table~\ref{tab:templating}).`
- Problem: Table 7 holds the two PIPLUP ratios, the masking gain and the growth
  ceiling, so a reader who follows the reference finds neither the 31 to 57 % figure
  nor the identity check that the sentence and the caption assert.
- Source: `reports/loggy-report/briefs/soak-index.md:238`
- Fix: add a first row to the table, `Whole pipeline after the rewrite & 31 to 57\,\% cheaper & byte-identical on 8,961,245 lines \\`, and change the caption (line 13) to
  "Every row is a ratio or a ceiling measured on one host, and the rewrite left the
  output byte-identical."

### 2. An arrow in Figure 6 strikes through the band label

- File: `reports/loggy-report/figures/fig06-alerts.svg`, line 139
- Quote: `<path d="M140,514 V568" fill="none" stroke="#101010" stroke-width="1.25" marker-end="url(#head)"/>`
- Problem: this arrow runs vertically through the baseline of the band label
  `FROM HITS TO ONE ALERT` at y=538, so on page 9 of the PDF the stroke cuts the word
  ONE in half.
- Source: `reports/loggy-report/figures/fig06-alerts.svg:137`
- Fix: start the arrow below the label, `<path d="M140,546 V568" ... />`.

### 3. The Table 1 caption counts five rows and the table has six

- File: `reports/loggy-report/sections/01-intro-problem.tex`, line 21
- Quote: `\begin{table}[htbp]\caption{Only one of the five families takes the InfoLogger road.}\label{tab:sources}`
- Problem: the table lists six sources, because the run orchestrator log is not one of
  the five worker families, so the caption and the body sentence disagree with what a
  reader counts.
- Source: `reports/loggy-report/briefs/constraints.md:85`
- Fix: "Only one of these six sources takes the InfoLogger road, and the run
  orchestrator log reaches no collector."
