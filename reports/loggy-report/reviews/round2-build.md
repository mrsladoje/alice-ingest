# Round 2 review: build and structure

Build: `tectonic --keep-logs report.tex` succeeds. `pdfinfo` reports 18 pages. The target is 16 including the title page.

Checked and clean: no undefined references, no undefined citations, no multiply defined labels, no missing files, no overfull boxes, no package warnings other than font substitutions and the unchanged "Object @page.1 already defined" note from the PDF writer (round 1 nit, unchanged, not re-reported). All 14 bibliography keys are cited and every citation resolves (the three uncited entries of round 1 are now cited). Every figure and table lands on the page of its citation or the next one (round 1 findings 6 to 8 are fixed). No figure or table overflows the text width. Every caption is a full sentence. The table of contents matches the headings and page numbers. The title page is complete.

## must

1. **Page count is still 18, target 16.** `report/preamble.tex:1` `\documentclass[a4paper,11pt]{article}`. Source: `pdfinfo report/report.pdf` gives `Pages: 18`. Round 1 must finding 1, still present. Action: see the three cuts at the end.

2. **Table 3 says the bucket indices' shard layout is "not stated", and the source states it.** `sections/02-design-a.tex:25` `\code{template-buckets-5m}, \code{-1h} & counts per template version per worker and window & storage & not stated & 4 and 66 days \\`. Source: `deploy/roles/loggy_opensearch/templates/schema/index-template-buckets-5m.json.j2:6-7` and `index-template-buckets-1h.json.j2:6-7` set `"number_of_shards": 1` and `"number_of_replicas": {{ template_buckets_replicas }}`, and `deploy/roles/loggy_opensearch/defaults/main.yml:228` sets `template_buckets_replicas: 1` (no override in `deploy/group_vars/all.yml`). Action: replace "not stated" with "1 shard, 1 replica".

3. **Table 3's caption claims two replicas for every non-local family, and the bucket indices carry one.** `sections/02-design-a.tex:17` `\caption{The bulk sits in one unreplicated index per worker, and every other family sits on the storage tier with two replicas.}`. Source: `deploy/roles/loggy_opensearch/defaults/main.yml:228` `template_buckets_replicas: 1`. Action: "The bulk sits in one unreplicated index per worker, and every other family sits replicated on the storage tier."

4. **A 33-word sentence breaks the 25-word rule.** `sections/03-design-b.tex:19` "From the death: the grace and the poll give the first missing flag after 60 to 150~seconds, the monitor adds up to 60, the projector up to 30, and the page wait 30." Source: the task rules (at most 25 words per sentence); word count 33. Action: "The grace and the poll give the first missing flag 60 to 150 seconds after the death. The monitor adds up to 60 seconds, the projector up to 30, and the page wait 30."

5. **Two sentences of 26 and 27 words break the 25-word rule.** `sections/02-design-a.tex:73` "The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals, and that both storage families agree on versions per worker and hour." (26) and "Two hourly rules read the catalogue: one fires on a template catalogued in the last hour, the other on any failed check in the last two hours." (27). Source: the task rules. Action: "The catalogue maintenance proves that up to 5,000 hourly buckets sum to their totals. It also proves that both storage families agree on versions per worker and hour." and "Two hourly rules read the catalogue. One fires on a template catalogued in the last hour, the other on any failed check in the last two hours."

## should

6. **Figure 6 sits alone on a float page that is about 45 % empty.** `sections/03-design-b.tex:9` `\begin{figure}[htbp]\centering\includegraphics[width=0.88\textwidth]{fig06-alerts.pdf}`. Source: printed page 8 of `report/report.pdf` holds only Figure 6 with blank space above and below it (rendered at 45 and 110 dpi). Action: add `\renewcommand{\floatpagefraction}{0.8}` and `\renewcommand{\topfraction}{0.9}` to `report/preamble.tex` so the figure shares its page with text, or place it `[t]` with width 0.8. Saves about 0.4 page.

7. **Appendix B's heading and its one-sentence body sit on printed page 16, and Table 10 lands alone at the top of page 17 above the references.** `sections/07-limits-close.tex:88` `\begin{table}[H]\footnotesize\caption{Twenty-one rounds ran, and most of them changed the product rather than confirming it.}`. Source: printed pages 16 and 17 of `report/report.pdf`; page 16 ends about a quarter empty after "Table 10 gives one line per round." Round 1 finding 2 is half fixed: the sentence exists, but `[H]` cannot fit the table on page 16 so it starts a new page. Action: put `\clearpage` before `\section{The soak in one line per round}` or, better, cut Table 10 (see cuts).

8. **Two bibliography entries still print with visible word gaps because their URLs cannot break.** `report/references.tex:10` `\bibitem{ref:alertmanager} Prometheus Alertmanager documentation. \url{https://prometheus.io/docs/alerting/latest/alertmanager/}.` Source: `report/report.log:1159` "Underfull \hbox (badness 10000) in paragraph at lines 10--11" and `report/report.log:1166` "badness 1348 ... lines 15--16"; printed page 17 entry [9] reads "Prometheus    Alertmanager    documentation." Round 1 should finding 9, still present. Action: add `\PassOptionsToPackage{hyphens}{url}` before `\usepackage[hidelinks]{hyperref}` in `report/preamble.tex:15`, or `\raggedright` after `\begin{thebibliography}{99}`.

9. **The abstract is 223 words against a 220-word ceiling.** `sections/00-abstract.tex:1` "Dense retrieval with reranking reaches 0.685 nDCG@10 against 0.634 for the dense model." Source: word count of the source after expanding `\loggy{}` and `~` gives 223; the PDF text (`reviews/report-text.txt:23-36`) gives the same after dropping the three "%" tokens. Action: cut "for the dense model" from the quoted sentence and "on a laptop rig" from "sustains about 42,000 a second on a laptop rig" (the laptop limit is restated in the last sentence).

10. **Figure 5 spells "catalog" while the prose spells "catalogue".** `figures/fig05-templates.svg:81` `The catalog`, `:112` `The catalog`, `:136` `reads catalog, buckets,`. Source: `outline.md` decision 16 (British spelling); the prose in `sections/02-design-a.tex:73` says "The catalogue maintenance". The index name `template-catalog` at `:82` is an identifier and may stay. Action: change the three labels to "The catalogue", "The catalogue maintenance" (the box at `:112`) and "reads catalogue, buckets,", then re-export `fig05-templates.pdf`.

## nit

11. **The running head names the last section that starts on a page, not the one the page opens with.** `report/preamble.tex:52` `\rhead{\small\color{inksoft}\nouppercase{\leftmark}}`. Source: printed page 16 opens with "7 Conclusion" and its head reads "B The soak in one line per round"; printed page 2 opens in the Introduction and reads "2 The problem and the constraints". Action: load `extramarks` and use `\firstleftmark`, or accept it.

12. **Table 3's justified X column shows a spaced line.** `sections/02-design-a.tex:22` `info and debug lines, one index per worker`. Source: `report/report.log:1105` "Underfull \hbox (badness 2662) in paragraph at lines 28--28"; printed page 4 shows "info  and  debug  lines,  one  index  per". Action: define `\newcolumntype{Y}{>{\raggedright\arraybackslash}X}` in the preamble and use `Y` for the "What lands there" column.

13. **An en dash survives in the O2 name.** `sections/01-intro-problem.tex:3` "the ALICE Online--Offline computing system". Source: the task rules (no dashes in prose). Action: write "Online-Offline" with a hyphen, as the cited report's title does.

14. **Appendix table 9 writes "dashboards" in lower case for the product the body calls "Dashboards".** `sections/07-limits-close.tex:74` `Store, dashboards, nginx & Vendor playbooks, two community roles, nginx role & ...`. Source: `sections/03-design-b.tex:29` "Dashboards, the store's own web interface". Action: "Store, Dashboards, nginx".

## The three cheapest cuts to reach 16 pages (about 2 pages to save)

1. **Recover the white space.** Figure 6's float page (finding 6, about 0.4 page), page 16's tail before Table 10 (finding 7, about 0.25 page) and printed page 4, where Figure 2 and Table 3 leave about 0.3 page blank. Setting `\floatpagefraction` to 0.8 and `\topfraction` to 0.9 in `report/preamble.tex` addresses all three. Estimated saving: 0.8 to 1 page, no words lost.

2. **Cut Table 10 and Appendix B's heading** (`sections/07-limits-close.tex:84-111`). Every row repeats a figure Section 5 already gives, and the only pointer is Section 2.2's "(Appendix B)", which can become "(Section 5)". Estimated saving: 0.5 page.

3. **Cut Figure 3** (`sections/02-design-a.tex:35`, about 0.45 page) or **Figure 7** (`sections/07-limits-close.tex:35`, about 0.5 page). Figure 3 draws the seven numbered steps that follow it word for word. Figure 7 draws the three layouts that the paragraph above it states in three sentences and Section 3.1 states again. Cutting one of them saves about 0.5 page.
