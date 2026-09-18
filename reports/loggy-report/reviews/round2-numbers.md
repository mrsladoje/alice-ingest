# Round 2 review — lens: numbers

Scope: every number in `sections/00-abstract.tex` and the eight parts, including
table cells and captions, traced against `docs/SOAK_RESULTS.md` (lines 11–203,
263–306 and the sections the briefs point at), `docs/SOAK.md`,
`docs/TEMPLATING_RESULTS.md`, `docs/EMBEDDING_RESULTS.md`,
`docs/SEMANTIC_RESULTS.md` (13–143) and the briefs `soak-index.md`,
`templating-embedding.md`, `semantic-2.md`, `walkthroughs-3-5.md`,
`walkthroughs-6-9.md`, `target-architecture.md`, `constraints.md`, `why.md`.

## Round-1 must findings on numbers: status

| Round-1 must | Status now |
|---|---|
| four of 64 physical cores | Fixed. `01:46` and `02:7` both say four of 64 physical cores, eight of 128 logical processors, which matches `docs/SOAK_RESULTS.md:270-277`. |
| DDS 0.1 % of the corpus | Fixed. `01:36` says "DDS is 0.1 % of the corpus", matching `docs/SOAK_RESULTS.md:2291`. |
| 96.94 % of the process tree | Fixed in the body (`02:49`, `04:39`, `05:54`). Still wrong in Appendix B row 7 — finding 4. |
| the retracted 19.91 to 11.18 pair | 11.18 is gone everywhere. 19.91 survives as a bare figure with no configuration label — finding 3. |
| the collector release census | Fixed. `05:56` says four installations on three releases where two were assumed, matching `docs/SOAK_RESULTS.md:3517-3527`. |
| the templating cost fraction | Fixed. The 18.8 %-of-the-stack claim is gone; `04:48` now says the stamper's cost is not measured. |

## Numbers checked and found sound (no finding)

Archive rates 248,828,513 / 312 / 23 / 78 / 9,781 / 1,000 a minute; flush table
108.40, 104.67, −3.4 %, 36.70, 27.70, −24.5 %, 87.1, 62.7, −28.0 %; flush grid
0.125–10 s and 17.9 %; noise floors 1.61 % and 9.48 %; 75.32 and 11.19;
threading 7.4 / 11.5 / 26.5 % and 89.3 %; live lane 41–68 %; memory 133–228,
373, 384, 768, 307, 64 chunks, 128 MB; buffer 256 MB, 310 bytes, 865,674, 13.8,
17.4 h, 46.8, 5.1 h, 61 s, 66.7 %; ceiling 42,000 / 50,000 / 86 % / six million /
82 % / 1.8 % / 263 s; heap 2.1 % vs 6.7 %, placement 2.7 % vs 5.5 %; Kafka 538×,
4.3×, 54×; 41.8 %, 99.83 %, 96.94 %, 2,500–10,500, 19.3 %, 99 defects, 32 in the
instrument, 5.2×; templating 2.56×, 7.8×, 206.5/26.3, 74–87 %, 85–87 %,
11.93→1.82, 12.24→1.73, 40.22→5.09, 8.33, 17.57/17.65, 44.9→10.4 %, 3,011,
4,092, 4,221, 55,963,050, 48.9/M, 20,000, 208.1/512, 4.7×, 15 %, 6.5 %, 32 %;
semantic 47.1 %, 5,301, 56,628,579, 18,037, 31×, 93 %, 0.387/0.414/0.035, 0.636,
5.4×, 0.685/0.634, +0.028 to +0.080, 130/97, 0.689/0.371, 0.020, 0.685→0.665,
0.048, 384 MB; watching 30 = 17 + 9 + 2 + 2 and 17 = 13 + 4; dead-collector bound
90 = 60+0+0+30 and 270 = 150+60+30+30; trend 50 records, six baseline slices,
250 ms, 40 minutes, 120 s, 24 h; catalogue 300 s, 5,000 buckets, 4,096
characters; live lane 500 records, query lane 20,000 rows; 22 causal edges;
31,000 lines of Python; shard budget 60 and 135; seven injection scenarios;
detector warm-up 32 windows and three to five hours.

## Findings

### MUST

**1. `sections/02-design-a.tex:71` — the stamper does not evict least-recently-used clusters**

Quote: `The stamper holds at most 20,000 clusters per worker and drops the least recently used.`

Problem: the source says the opposite, and the next sentence of the same
paragraph says the opposite too.

Source: `docs/SOAK_RESULTS.md:5129-5133` — "drain3's own `max_clusters` is an LRU
that **evicts**, and eviction cannot be used here. The count published for a
template is the cluster's absolute size in this tree, so an evicted cluster later
rebuilt would report a size of one and SET the catalog count back to one." Also
`briefs/soak-index.md:267` and the report's own `06-eval-b.tex:11`.

Fix: "The stamper holds at most 20,000 clusters per worker. At that ceiling it
stops learning and keeps counting, because evicting a cluster would reset its
published count to one." Then delete the now-redundant following sentence about
the archive pass, or keep only "The archive pass in Section 5.5 reached that
ceiling in neither corpus."

**2. `sections/07-limits-close.tex:11` — the cost curve was not measured at 5,000 only**

Quote: `The cost curve was measured at 5,000~records a second only.`

Problem: the report itself gives collector cost at three other rates, so this
sentence contradicts Section 5.1; what was confined to 5,000 a second is the
full stack, not the cost curve.

Source: `docs/SOAK_RESULTS.md:154-158` — "The **full stack** — collector plus the
worker's OpenSearch node plus the storage tier — has never been run above 5,000 a
second in steady state". The collector alone reads 75.32 at 1,000, 11.19 at
20,000 and 8.97 at 50,000 (`docs/SOAK_RESULTS.md:505-506, 148-149`), which
`05-eval-a.tex:11` already quotes.

Fix: "The whole stack's cost was measured at 5,000 records a second only."

**3. `sections/06-eval-b.tex:5` — two template counts and two costs for one corpus, with no configuration label**

Quote: `The parser we keep, drain3, mined the archive into 3,822 templates at 19.91~core-seconds per million \cite{ref:drain,ref:drain3}.`

Problem: six lines later the same section says the same 45,596,613 lines give
3,011 templates, and `04-why.tex:48` says the same mining now costs 8.33
core-seconds per million and that 19.91 was never re-measured. A reader cannot
tell that 3,822 and 19.91 belong to the shipped drain3 defaults and 3,011 and
8.33 to the per-family recipe with the fast masker.

Source: `docs/SOAK_RESULTS.md:1996` (H1b: 3,822 templates, 19.91 core-s/M, the
stage H control), `docs/SOAK_RESULTS.md:2406-2407` (3,011 against H1b's 3,822 on
the same lines), `docs/SOAK_RESULTS.md:2708-2713` ("a control reading of 19.91
for the shipped configuration that I did not re-measure ... The absolute is 8.33
and the control is unverified"), `briefs/soak-index.md:298`.

Fix: "With drain3's own defaults the archive mined into 3,822 templates, at an
unverified 19.91 core-seconds per million, and PIPLUP cost 2.56 times that on the
same run." Then at line 11 open with "Under the per-family recipe the same
45,596,613 lines give 3,011 templates."

**4. `sections/07-limits-close.tex:103` — 96.94 % is the process-tree family, not all lines**

Quote: `7 & Three sources added. 96.94~\% of lines stay local. \\`

Problem: the figure is the O2 process-tree family's share. Across the whole
corpus InfoLogger is 58.1 % of lines and every InfoLogger record is durable, so
the all-lines local share is nowhere near 96.94 %.

Source: `docs/SOAK_RESULTS.md:3115` — "**96.94 % of the process tree stays on the
node and 3.06 % crosses the network.**"; corpus weights at
`docs/SOAK_RESULTS.md:2291`.

Fix: `7 & Three sources added. 96.94~\% of the process tree stays local. \\`

**5. `sections/07-limits-close.tex:108` — 15 % is the transport, not the hop**

Quote: `21 & Stamper hop 15~\% cheaper. \\`

Problem: this contradicts the body, which says the hop fell 6.5 %. The 15 % is
the transport half alone.

Source: `docs/SOAK_RESULTS.md:5975-5982` — transport −15.1 / −15.4 % and the
whole stamper hop −6.5 % on `infologger`, −4.0 % on `dpl`; the report states this
correctly at `06-eval-b.tex:13`.

Fix: `21 & Stamper transport 15~\% cheaper, the whole hop 4 to 6.5~\%. \\`

### SHOULD

**6. `sections/00-abstract.tex:1` — the abstract's collector cost never appears in the body**

Quote: `The collector costs about a quarter of one core at 20,000 records a second.`

Problem: no section restates it. The body gives only 11.19 core-seconds per
million at 20,000 a second and never converts, so the abstract's headline number
is unsupported inside the report.

Source: `briefs/soak-index.md:44` — "0.25 of one core | The collector at 20,000 a
second; three of four cores do nothing for it | healthy |
docs/SOAK_RESULTS.md:559-563".

Fix: add to `05-eval-a.tex:11`, after the 11.19 figure, "which is about a quarter
of one core at that rate".

**7. `sections/00-abstract.tex:1` — the round-18 equivalence corpus is missing from Section 5.5**

Quote: `The templating pipeline is 31 to 57~\% cheaper per family and byte-identical on 8,961,245 lines.`

Problem: 8,961,245 appears nowhere in Section 5.5. The only evidence cell there
is "zero differences on 3,000,000 lines per family", which is round 6's masker
check, not round 18's. The reader cannot find the abstract's number in the
section that should carry it.

Source: `docs/SOAK_RESULTS.md:5537` — "candidate prepare against shipped
`recipe_prepare`, every line of every family, escaped corpus form | 8,961,245 |
**0 differences**"; `briefs/soak-index.md:238`.

Fix: change `06-eval-b.tex:3` to "...and every one of 8,961,245 lines kept the
same template", or add a Table 8 row "Round 18 equivalence | 0 differences |
8,961,245 lines, all seven families".

**8. `sections/06-eval-b.tex:34` — the 1 ms to 12 ms latency belongs to the dense path, not the recommended two-step search**

Quote: `Median laptop latency rises from 1~ms to 12~ms.`

Problem: the sentence sits in the paragraph describing the dense-scan-plus-rerank
recommendation, so a reader reads 12 ms as that path's latency. The pair is
BM25F against the dense model alone on the live path.

Source: `docs/SEMANTIC_RESULTS.md:1530-1536` (control BM25F 1 ms, treatment
`P-DenseOn` 12 ms at the 50th percentile); `briefs/semantic-2.md:152` — "The
total latency of the final exact-cosine-plus-reranking path is not stated as one
number in this range."

Fix: "Against the lexical engine the dense scan alone moves median laptop latency
from 1 ms to 12 ms; the reranking step adds 0.6 ms and the combined path is not
timed as one number."

**9. `sections/06-eval-b.tex:34` — the gained and lost templates were cut, and they are what make the recommendation a trade**

Quote: `An approximate vector index costs 0.020 nDCG@10, 0.685 to 0.665, and moves between builds, so we use none.`

Problem: the paragraph reports only wins. The outline requires the live-path
trade, and without it the reader concludes the new search is a strict
improvement, which the source explicitly denies.

Source: `docs/SEMANTIC_RESULTS.md:1524-1525` — "It finds 377 relevant templates
the current search misses, and it loses 204 that the current search finds";
`docs/SEMANTIC_RESULTS.md:1541-1543` (29 of 110 queries get a worse answer);
`outline.md` decision list, section 5.6 ("377 relevant templates gained and 204
lost on the live path").

Fix: insert before the latency sentence: "On the live path it finds 377 relevant
templates the lexical engine misses and loses 204 that it finds, and 29 of 110
questions get a worse answer."

**10. `sections/02-design-a.tex:15` — the three farm primaries carry two different statuses**

Quote: `Three primaries are set for the farm and have never carried farm volume.`

Problem: `04-why.tex:39` says of the same number "The farm runs three primaries,
agreed and not applied". "Set" and "not applied" cannot both be true, and the
report's status marks are a stated rule.

Source: `briefs/target-architecture.md:199` — the code default is one primary,
`deploy/README.md` says three, and the farm inventory sets
`log_primary_shards_storage` without a recorded value; `outline.md` decision 3
fixes only the layout per environment, not the status.

Fix: use one wording in both places, "Three primaries are agreed for the farm and
have never carried farm volume", and make Table 3's cell read "farm 3, agreed".

**11. `sections/00-abstract.tex:1` — the abstract states the median-worker rate with the wrong meaning**

Quote: `In six months of archive a median worker's busiest hour is 23~records a second, the busiest worker-second 78.`

Problem: 23 is the median worker's rate inside the farm's busiest hour, not each
worker's own busiest hour. Taking each worker's own busiest hour would give a
different, higher number.

Source: `docs/SOAK_RESULTS.md:341-343, 364` — the busiest hour is 15 May 2026,
22:00 to 23:00 UTC, 20,151,049 records over 297 workers, median worker 23 a
second; `briefs/soak-index.md:141-142`. The conclusion at `07:53` states it
correctly.

Fix: "In the busiest hour of six months of archive the median worker carried 23
records a second, and the busiest worker-second was 78."

**12. `sections/01-intro-problem.tex:46` — 64 physical cores is one machine, not every worker**

Quote: `A worker gives the platform four of its 64 physical cores, eight of its 128 logical processors, and little memory.`

Problem: the 64-and-128 figure is `epn228`. One of the three farm workers has
192 logical processors, so the sentence over-generalises a hardware count that
Table 2 and Section 3.1 then repeat.

Source: `docs/SOAK_RESULTS.md:268-271` (the comparison table names `epn228`:
2 × AMD EPYC 7452, 64 physical, 128 logical); `briefs/constraints.md:26-30` —
"the farm is not one hardware generation. `epn323` has 192 logical processors and
1007 GB, not 128 and 503 GB".

Fix: "A worker gives the platform four physical cores, eight logical processors
of the 128 an EPN of the reference generation has, and little memory."

### NIT

**13. `sections/06-eval-b.tex:11`** — quote: `Sixteen more runs and eighty InfoLogger partitions gave 4,092.` It is sixteen runs in total, not sixteen further runs (`briefs/soak-index.md:198`; `docs/SOAK_RESULTS.md:2523-2528`). Fix: "Sixteen runs and eighty InfoLogger partitions gave 4,092."

**14. `sections/03-design-b.tex:5`** — quote: `Seventeen detectors run, one model per entity, and a silent host scores zero.` Two sentences later the same paragraph says "Of 30 monitors, 17 are threshold and detector monitors". Both counts are right (`reports/inputs/dataflow-atlas.md:193, 214`) but the repeated 17 reads as one number. Fix: write the monitor breakdown as "13 of them run every minute and four every ten" without restating 17.

**15. `sections/01-intro-problem.tex:17`** — quote: `A worker holds five log families, and InfoLogger is one of them (Table~\ref{tab:sources}).` Table 1 has six rows, because it also lists the uncollected run orchestrator log (`outline.md` section 2.1). Fix: "A worker holds five log families, and InfoLogger is one of them. Table 1 adds the run orchestrator's log, which nothing collects."

**16. `sections/07-limits-close.tex:99`** — quote: `2 addendum & About 42,000 a second sustained, zero loss. \\` Zero loss belongs to the 50,000-for-two-minutes cell, not to the sustained rate (`docs/SOAK_RESULTS.md:1824-1826`). Fix: "About 42,000 a second sustained; 50,000 for two minutes with zero loss."

**17. `sections/03-design-b.tex:7`** — quote: `One monitor watches every grade and pages once for the whole fleet above 0.7.` The monitor needs the maximum grade above 0.7 **and** that row's confidence above 0.7 (`briefs/walkthroughs-6-9.md:29`; `ad-high-grade.json:68`). Fix: "...pages once for the whole fleet when a grade and its confidence both pass 0.7."
