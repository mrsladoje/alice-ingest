# Round 1 review: build and structure

Build: `tectonic --keep-logs report.tex` succeeds. `pdfinfo` reports 18 pages; the target is 16 including the title page.

Checked and clean: no undefined references, no undefined citations, no multiply defined labels, no missing files, no overfull boxes, no package warnings other than font substitutions. The abstract is 219 words. The table of contents matches the headings and page numbers. No figure overflows the text width. No table overflows. Every caption is a full sentence. No page in the body is more than a third empty. The title page is complete (title, subtitle, author, affiliation, supervisors, date, abstract).

## must

1. **Page count is 18, target 16.** `preamble.tex:1` `\documentclass[a4paper,11pt]{article}`. Source: `pdfinfo report/report.pdf` gives `Pages: 18`. Action: the three figures that float two pages past their citations (findings 5 to 7) each leave part-empty text pages behind them; placing them `[htbp]` and giving Appendix B its table in place (finding 2) recovers most of the gap. If that is not enough, cut text in sections 3 and 5, the two longest.

2. **Appendix B has a heading and no body, and its table floats into the bibliography.** `sections/07-limits-close.tex:76` `\section{The soak in one line per round}\label{app:soak}`. In the PDF the heading is followed directly by the "References" heading (printed page 16), and Table 10 lands on printed page 17 between reference [6] and reference [7]. `tab:soak` is referenced nowhere (grep of `ref{tab:soak}` over sections returns nothing). Action: add one sentence under the heading, "Table~\ref{tab:soak} gives one line per soak round.", and set the table to `\begin{table}[H]` (the `float` package is loaded) or put `\clearpage` before `\input{references}` in `report/report.tex:39`.

3. **Bibliography entry `ref:o2tdr` is never cited.** `report/references.tex:4` `\bibitem{ref:o2tdr} ALICE Collaboration. \emph{Technical Design Report for the Upgrade of the Online--Offline Computing System}. CERN-LHCC-2015-006, ALICE-TDR-019, 2015.` Source: grep of `\cite` over sections and report.tex lists 11 keys and not this one. Action: cite it at the first mention of timeframe reconstruction, `sections/01-intro-problem.tex:3`, or delete the entry.

4. **Bibliography entry `ref:drain` is never cited.** `report/references.tex:7` `\bibitem{ref:drain} P. He, J. Zhu, Z. Zheng, M. R. Lyu. Drain: an online log parsing approach with fixed depth tree.` Source: same grep. Action: cite it next to the first `\cite{ref:drain3}` at `sections/06-eval-b.tex:5` ("Drain3, the parser we keep") as `\cite{ref:drain,ref:drain3}`, or delete the entry.

5. **Bibliography entry `ref:aliceilad` is never cited.** `report/references.tex:15` `\bibitem{ref:aliceilad} A. Techaviseschai, S. Tarnpradab, V. Chibante Barroso, P. Phunchongharn. A real-time semi-supervised log anomaly detection framework for ALICE O$^2$ facilities.` Source: same grep. Action: cite it where section 6.3 names anomaly detection on the log text as open (`sections/07-limits-close.tex:39`, the "Seventh" item), or delete the entry.

## should

6. **Figure 4 lands two pages after the sentence that cites it.** `sections/02-design-a.tex:53` "The collector reads each line, decides where it belongs and sends it on (Figure~\ref{fig:collector})." Source: pdftotext page map; the citing sentence is on printed page 5, the figure on printed page 7. Action: change `\begin{figure}[t]` at `sections/02-design-a.tex:55` to `[htbp]`, or move the environment above the paragraph that cites it.

7. **Figure 5 lands two pages after the sentence that cites it.** `sections/02-design-a.tex:67` "The stamper sets it on the worker before indexing, so both tiers carry the field and no raw line leaves its machine (Figure~\ref{fig:templates})." Source: citing sentence on printed page 6, figure on printed page 8. Action: same as finding 6 for `sections/02-design-a.tex:69`.

8. **Figure 6 lands two pages after the sentence that cites it.** `sections/03-design-b.tex:7` "A detector that stops reporting leaves its episode stale and firing (Figure~\ref{fig:alerts})." Source: citing sentence on printed page 7, figure on printed page 9. Action: same as finding 6 for `sections/03-design-b.tex:9`.

9. **Two bibliography entries print with visible word gaps because the URL cannot break.** `report/references.tex:10` `\bibitem{ref:alertmanager} Prometheus Alertmanager documentation. \url{https://prometheus.io/docs/alerting/latest/alertmanager/}.` and `report/references.tex:15` (the `ref:aliceilad` entry). Source: `report/report.log:1178` "Underfull \hbox (badness 10000) in paragraph at lines 10--11" and `report/report.log:1185` "badness 1348 ... lines 15--16"; on printed page 17 entry [9] reads "Prometheus    Alertmanager    documentation." Action: add `\PassOptionsToPackage{hyphens}{url}` before `\usepackage[hidelinks]{hyperref}` in `preamble.tex:15`, or put `\raggedright` after `\begin{thebibliography}{99}`.

## nit

10. **Table 1 uses `tabularx` with no `X` column, so the alignment is underfull.** `sections/01-intro-problem.tex:20` `\begin{tabularx}{\textwidth}{L{0.2\textwidth} L{0.26\textwidth} L{0.24\textwidth} L{0.18\textwidth}}`. Source: `report/report.log:1089` "Underfull \hbox (badness 10000) in alignment at lines 31--31". Action: make the last column `X`, or use `tabular` instead.

11. **One narrow cell in Table 3 sets a ragged line.** `sections/02-design-a.tex:26` cell "health samples from collectors and the poller". Source: `report/report.log:1100` "Underfull \hbox (badness 5260)". Action: widen that column by 0.02\textwidth or shorten the cell to "health samples".

12. **One narrow cell in Table 5 sets a ragged line.** `sections/04-why.tex:29` cell "body, counts, detection, alerting". Source: `report/report.log:1121` "Underfull \hbox (badness 10000)". Action: widen the column or write "body, counts, detection and alerting".

13. **Table 10's caption does not describe the table's content.** `sections/07-limits-close.tex:78` "The review rounds also made the product stamp a document identifier and write with create." The rows list one result per soak round; no row mentions a document identifier. Source: the table body at `sections/07-limits-close.tex:82-97`. Action: "Each soak round left one figure of record, and the review rounds fixed the duplicated bulks by writing with create."

14. **A four-word paragraph tail sits under Figure 1 at the top of printed page 2.** `sections/01-intro-problem.tex:9` "The platform runs on five staging machines, and one deployment went green on three farm workers and one infra machine." Source: printed page 2 opens with the figure, then "workers and one infra machine." Action: no change needed if finding 6 to 8 reflow the pages; otherwise place Figure 1 `[htbp]`.

15. **The PDF writer warns "Object @page.1 already defined".** `preamble.tex:17` `\special{pdf:minorversion 7}`. Source: tectonic standard output during the build ("warning: Object @page.1 already defined"). The cause was not verified in this session; the PDF opens and the reader sees nothing. Action: none unless the warning turns into an error on a later build.
