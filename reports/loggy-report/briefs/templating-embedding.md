# Brief: templating and embeddings

Sources read: docs/TEMPLATING_RESULTS.md (all 236 lines, round 4), docs/EMBEDDING_RESULTS.md (all 159 lines, round 3), docs/SOAK_RESULTS.md lines 1983 to 2060 and 2244 to 2320 (round 6, stage H).

Tags: [A] is an architecture-level fact that belongs in the report. [C] is code-level evidence only.

How to read the two result files. Each opens with "Read this first". In both files that section retracts the plan's premise, not the file's own numbers. Round 4 retracts the plan's expectation of a cost ceiling and a rewrite (docs/TEMPLATING_RESULTS.md:13, docs/TEMPLATING_RESULTS.md:28-33). Round 3 retracts the plan's per-line pricing of embeddings (docs/EMBEDDING_RESULTS.md:13-23). Three explicit corrections sit inside the files and the soak. They are listed in section 7.

## 1. What templating is and what it is for

[A] A template is the skeleton of a log line with the variable parts masked. The file does not define the term in one sentence. It shows it. The five most common InfoLogger templates carry `<NUM>`, `<UUID>`, `<FLOAT>`, `<IP>` and `<*>` where the line carried a value (docs/TEMPLATING_RESULTS.md:109-119). The masking pass replaces numbers, addresses and identifiers with those tokens before the tree sees the line (docs/TEMPLATING_RESULTS.md:170-173).

[A] Templating serves two purposes in the design.

1. It turns a stream of lines into count series per template. Each template is one series the detectors count over time (docs/SOAK_RESULTS.md:2306-2310).
2. It makes embeddings affordable. An embedding is paid once per template, not once per line (docs/TEMPLATING_RESULTS.md:30-31, docs/EMBEDDING_RESULTS.md:21-23).

[A] The miner is Drain3, a streaming parser that keeps a tree of clusters (docs/TEMPLATING_RESULTS.md:19, docs/TEMPLATING_RESULTS.md:172-173). The soak later confirmed the choice against eleven other parsers (docs/SOAK_RESULTS.md:1985-1988).

## 2. Templating, round 4

### The corpus

[A] Round 4 measured 45,596,613 lines of real ALICE log text from 314 sources, on 27 August 2026 (docs/TEMPLATING_RESULTS.md:6-7, docs/TEMPLATING_RESULTS.md:61).

| Family | Lines | Sources | Origin | Line |
|---|---:|---:|---|---|
| infologger | 26,505,911 | 133 | 40 MySQL partitions, sampled evenly across all 179 | docs/TEMPLATING_RESULTS.md:58 |
| stdout | 19,046,730 | 157 | O2 process out and err logs, 4 run tarballs | docs/TEMPLATING_RESULTS.md:59 |
| dds | 267,607 | 24 | the DDS firehose, 24 run tarballs | docs/TEMPLATING_RESULTS.md:60 |
| Total | 45,596,613 | 314 | | docs/TEMPLATING_RESULTS.md:61 |

[A] The archive is October 2022 data and every run in it is MFT (docs/TEMPLATING_RESULTS.md:210-213).

[A] The InfoLogger corpus carries the parsed message. The DDS and stdout corpora carry the raw line (docs/TEMPLATING_RESULTS.md:67-68). In production the collector parses first, so the InfoLogger figures are the ones production pays (docs/TEMPLATING_RESULTS.md:68-72). Stripping the DDS prefix drops its cost from 59.52 to 43.58 core-seconds per million, a 27 % fall (docs/TEMPLATING_RESULTS.md:69-71).

[C] The corpus reader pulls the archive over HTTPS with the cern_s3 profile, with no lxplus hop and no Kerberos ticket (docs/TEMPLATING_RESULTS.md:52-54).

### What was measured

[A] Every cost is processor time, not wall time (docs/TEMPLATING_RESULTS.md:39). Every headline cost was measured inside a Linux VM pinned to four processors, the same environment as round 2 (docs/TEMPLATING_RESULTS.md:40-42). The same slice costs 17.00 core-seconds per million natively and 14.54 in the VM, so the two are not interchangeable (docs/TEMPLATING_RESULTS.md:42-44). Template counts are identical in both environments at every corpus size (docs/TEMPLATING_RESULTS.md:46-48).

### Valid numbers, templating

| Number | What it measures | Reference | Meaning | Line |
|---|---|---|---|---|
| 3,822 | templates mined from the whole corpus, one global tree | 45,596,613 lines | the whole vocabulary of the archive fits in a few thousand skeletons | docs/TEMPLATING_RESULTS.md:83 |
| 48.9 /M | steady-state new-template rate, all three families | second half of each family's run | about one new template per second at 20,000 records a second | docs/TEMPLATING_RESULTS.md:83, docs/TEMPLATING_RESULTS.md:17-18 |
| 34.4 /M | steady-state rate, infologger | 2,853 templates over 26,505,911 lines | the family that still moves | docs/TEMPLATING_RESULTS.md:80 |
| 0.6 /M | steady-state rate, stdout | 813 templates over 19,046,730 lines | near static | docs/TEMPLATING_RESULTS.md:81 |
| 0 /M | steady-state rate, dds | 186 templates over 267,607 lines | static after the first 100,000 lines | docs/TEMPLATING_RESULTS.md:82, docs/TEMPLATING_RESULTS.md:100-102 |
| 526 | templates created by the opening 500,000 lines | empty tree at start | whole-corpus rates are dominated by the first blocks | docs/TEMPLATING_RESULTS.md:86-87 |
| 19.73 | core-seconds per million records, all three families, in the VM | corpus mix 58 % infologger, 42 % stdout | the per-line cost of templating | docs/TEMPLATING_RESULTS.md:134, docs/TEMPLATING_RESULTS.md:136-137 |
| 17.27 | core-seconds per million, infologger, in the VM | native 19.70 | the cost production would pay on parsed messages | docs/TEMPLATING_RESULTS.md:131 |
| 23.21 | core-seconds per million, stdout, in the VM | native 25.91 | upper bound, raw lines | docs/TEMPLATING_RESULTS.md:132 |
| 50.84 | core-seconds per million, dds raw line, in the VM | native 59.52 | upper bound, raw lines | docs/TEMPLATING_RESULTS.md:133 |
| 0.39 | cores for templating at 20,000 records a second | the whole four-core stack takes 2.09 cores | templating adds 18.8 % to the stack | docs/TEMPLATING_RESULTS.md:143-148 |
| 0.55 | cores for the collector alone at 20,000 records a second | 27.70 core-seconds per million | templating costs 71 % of what the collector costs | docs/TEMPLATING_RESULTS.md:144, docs/TEMPLATING_RESULTS.md:147 |
| 0.83 | cores for templating at 42,000 records a second | round 2's sustained ceiling | still under one core, a fifth of the budget rather than a tenth | docs/TEMPLATING_RESULTS.md:148-150 |
| 16.82 | core-seconds per million, mask only, no tree | 500,000 lines in the VM | masking is 83 % of the mining cost | docs/TEMPLATING_RESULTS.md:166, docs/TEMPLATING_RESULTS.md:176-177 |
| 20.34 | core-seconds per million, mask plus tree | 526 templates | the full mining cost on that slice | docs/TEMPLATING_RESULTS.md:167 |
| 932.07 | core-seconds per million, tree with masking removed | 48,306 templates | 46 times dearer, 92 times the templates | docs/TEMPLATING_RESULTS.md:168, docs/TEMPLATING_RESULTS.md:170-173 |
| 430 | trees when mining one tree per source | 13,587 templates, 16.69 core-seconds per million | 15 % cheaper, 3.6 times the templates | docs/TEMPLATING_RESULTS.md:187-191 |

### The new-template rate, and why round 3 depends on it

[A] The number to read is the steady-state column. The whole-corpus rate is dominated by the first blocks, because the tree starts empty (docs/TEMPLATING_RESULTS.md:85-89).

[A] New templates arrive in bursts, not at a rate. A burst is a program appearing for the first time (docs/TEMPLATING_RESULTS.md:91-92). In the second half of the corpus 13 of 27 infologger blocks, 15 of 19 stdout blocks and 6 of 6 dds blocks produced nothing (docs/TEMPLATING_RESULTS.md:96-98). DDS produced 183 templates in its first 25,000 lines, three more by line 100,000, then nothing across the remaining 142,000 lines (docs/TEMPLATING_RESULTS.md:100-102).

[A] Round 3 depends on this rate because an embedding is paid once per template. One template a second is one to two orders of magnitude inside what a full transformer does on one core (docs/TEMPLATING_RESULTS.md:30-33).

[A] The templates are readable. One imperfection is left alone: a version number becomes `<FLOAT>.<NUM>` rather than one token. It merges nothing it should not (docs/TEMPLATING_RESULTS.md:121-123).

### The per-line cost

[A] Templating costs 19.73 core-seconds per million records, 0.39 of a core at 20,000 records a second (docs/TEMPLATING_RESULTS.md:145). The plan asked whether production must move to Rust, C or Go. The answer is not yet. A rewrite buys back at most 0.39 of a core at the reference rate. The case for a rewrite arrives with the rate, not with the feature (docs/TEMPLATING_RESULTS.md:152-156).

### Why masking makes the tree cheap

[A] Removing the masking makes mining 46 times dearer. Without masking every distinct number is a distinct token. The tree grows 92 times the clusters, and the similarity search walks all of them on every line (docs/TEMPLATING_RESULTS.md:170-174). The masking pass costs 16.82 and saves 911.73 core-seconds per million (docs/TEMPLATING_RESULTS.md:174). Masking is 83 % of the total cost, 16.82 of 20.34 (docs/TEMPLATING_RESULTS.md:176-177). A faster language would have to win in the regex engine, not in the tree walk (docs/TEMPLATING_RESULTS.md:177-178).

### One tree or one per program

[A] We take one global tree (docs/TEMPLATING_RESULTS.md:193). One tree per source is 15 % cheaper and produces 3.6 times the templates, because the same message from four programs is mined and stored four times (docs/TEMPLATING_RESULTS.md:189-191). The 3.6 times is 3.6 times the embedding work, an index full of near-duplicates, and 430 trees per worker instead of one (docs/TEMPLATING_RESULTS.md:193-197).

### What round 4 does not measure

See section 6 for the verbatim list (docs/TEMPLATING_RESULTS.md:206-222).

### The instrument fault

[C] One instrument divided the whole read loop's processor time by the matching lines only. The loop reads the entire corpus whichever family is asked for, so a small family was billed for reading the others. DDS reported 276.38 core-seconds per million against a true 59.74, a factor of 4.6 (docs/TEMPLATING_RESULTS.md:228-231). The instrument is now suppressed whenever one family is selected, and runs record read lines beside matched lines (docs/TEMPLATING_RESULTS.md:233-234). The mining timer is timed around the miner alone and was correct throughout. No number in the file rests on the faulty one (docs/TEMPLATING_RESULTS.md:235-236).

## 3. Embeddings, round 3

[A] Round 3 measured six embedding configurations on the 3,822 templates from round 4, on 27 August 2026 (docs/EMBEDDING_RESULTS.md:6-7). Each ran on one core, with 64-token truncation and length-sorted batches of 64 (docs/EMBEDDING_RESULTS.md:49).

### The ladder

[A] The ladder runs from static embeddings, cheapest, to a full transformer, dearest, plus that transformer quantized to int8 (docs/EMBEDDING_RESULTS.md:53-58). Model names are code-level. The report can call them "a static embedding model" and "a small transformer".

### Valid numbers, embeddings

| Number | What it measures | Reference | Meaning | Line |
|---|---|---|---|---|
| 18,037 | templates per core-second, potion-base-32M, 512 dims | 581 for all-MiniLM-L6-v2 | 31 times faster than the transformer baseline | docs/EMBEDDING_RESULTS.md:54, docs/EMBEDDING_RESULTS.md:27-30 |
| 55.9 | core-seconds per million templates, potion-base-32M | 1,720.7 for the baseline | the cost of embedding a million templates once | docs/EMBEDDING_RESULTS.md:54, docs/EMBEDDING_RESULTS.md:57 |
| 0.387 | macro source purity, potion-base-32M | baseline 0.414, null 0.035 | 93 % of the baseline's quality, eleven to twelve times the null | docs/EMBEDDING_RESULTS.md:54, docs/EMBEDDING_RESULTS.md:27-33 |
| 17,687 | templates per core-second, potion-base-8M, 256 dims | purity 0.380 | within noise of the pick at half the dimensions | docs/EMBEDDING_RESULTS.md:53, docs/EMBEDDING_RESULTS.md:41-43 |
| 17,947 | templates per core-second, potion-retrieval-32M | purity 0.382 | a third static rung, no better | docs/EMBEDDING_RESULTS.md:55 |
| 1,105 | templates per core-second, paraphrase-MiniLM-L3-v2, 384 dims | purity 0.404 | the smaller transformer | docs/EMBEDDING_RESULTS.md:56 |
| 581 | templates per core-second, all-MiniLM-L6-v2 fp32, 384 dims | purity 0.414 | the reference transformer | docs/EMBEDDING_RESULTS.md:57 |
| 107 | templates per core-second, all-MiniLM-L6-v2 int8 | 581 in fp32 | 5.4 times slower, on one backend on Apple silicon | docs/EMBEDDING_RESULTS.md:58, docs/EMBEDDING_RESULTS.md:34-38 |
| 0.417 | macro source purity, int8 | 0.414 in fp32 | quality survives quantization | docs/EMBEDDING_RESULTS.md:58, docs/EMBEDDING_RESULTS.md:130 |
| 0.035 | macro source purity of a random neighbour | micro null 0.344 | the floor every rung is read against | docs/EMBEDDING_RESULTS.md:59 |
| 0.380 to 0.417 | the whole quality range across six rungs | null 0.035 | all six find real structure, eleven to twelve times the null | docs/EMBEDDING_RESULTS.md:31-33, docs/EMBEDDING_RESULTS.md:61 |
| 7.0 % | more macro purity bought by thirty-one times the cost | 0.387 against 0.414 | not a trade worth making at one template a second | docs/EMBEDDING_RESULTS.md:61-64 |
| 9.7 % | widest quality gap on the ladder, cheapest against dearest | | the ladder is flat on quality | docs/EMBEDDING_RESULTS.md:62-63 |
| 93.5 % | potion-base-32M's purity as a share of the baseline's, on our templates | the plan's published figure 94.66 % on a public benchmark | the published claim transfers to this data | docs/EMBEDDING_RESULTS.md:66-70 |
| 0.006 % | of a core, potion-base-32M at the observed template rate | one template a second | the embedding bill is negligible | docs/EMBEDDING_RESULTS.md:40-41 |
| 3,346 | templates from exactly one source | 3,822 templates | the source label is clean for 88 % of templates | docs/EMBEDDING_RESULTS.md:95-96 |
| 2,180 | templates owned by one source, infologger/ODC/ODC | 3,822 templates, 57 % | why the micro average is useless | docs/EMBEDDING_RESULTS.md:99-100 |
| 23 | sources that remain in the macro average | sources under 10 templates and the 418-template stdout/unknown bucket excluded | the population the purity number describes | docs/EMBEDDING_RESULTS.md:105-107 |

### Why the obvious metric was thrown away, and what source purity is

[A] The obvious metric was neighbour agreement with a reference model: do the cheap models put the same templates next to each other as the transformer does (docs/EMBEDDING_RESULTS.md:76-79). It does not work. The same model in int8 agrees with itself in fp32 on 0.636 of ten nearest neighbours. Two transformers from the same lineage agree on 0.369 (docs/EMBEDDING_RESULTS.md:82-84). Agreement with a reference measures distance from one arbitrary point, not correctness. It is reported for completeness and used for nothing (docs/EMBEDDING_RESULTS.md:86-90).

[A] Source purity replaced it. The miner records, for each template, the program it mostly came from. The question is then: do a template's ten nearest neighbours come from the same program (docs/EMBEDDING_RESULTS.md:94-97). The average is taken over sources, not over templates, so each program gets one vote. Averaged over templates, every model lands near 0.76 because one source owns 57 % of the templates. Only the macro column separates the rungs (docs/EMBEDDING_RESULTS.md:99-103).

### The int8 result, stated carefully

[A] Quality survives quantization. Throughput does not (docs/EMBEDDING_RESULTS.md:130). int8 reached 107 templates per core-second against 581 in fp32, with purity 0.417 against 0.414 (docs/EMBEDDING_RESULTS.md:126-127). The model's matrices are small, so the quantize and dequantize around each one costs more than the narrower multiply saves (docs/EMBEDDING_RESULTS.md:130-132). This was measured on one backend on Apple silicon. The farm's x86 machines use a different backend, where the balance may differ. The instruction is to measure it there before assuming int8 helps (docs/EMBEDDING_RESULTS.md:134-137). It is a warning, not a property of int8 (docs/EMBEDDING_RESULTS.md:37-38).

### What round 3 did not do

See section 6 for the verbatim list (docs/EMBEDDING_RESULTS.md:143-159).

## 4. Relation to the soak, round 6 stage H

Stage H revisited the parser choice and the masker over the same 45,596,613 lines (docs/SOAK_RESULTS.md:1985-1986).

### Numbers the soak superseded

| Round 4 number | Line | Soak number | Line | What changed |
|---|---|---|---|---|
| Drain defaults kept: threshold 0.4, depth 4, 100 children | docs/TEMPLATING_RESULTS.md:220-222 | one recipe per family: depth 8, similarity 0.4 for infologger and stdout, 0.5 for dds, padded separators `= ; :`, `= ;` and `=` | docs/SOAK_RESULTS.md:2246-2250 | the shipped configuration is now per family. Numeric tokens are parametrised for infologger and stdout and kept for dds (docs/SOAK_RESULTS.md:2248-2250). stdout needs a collector rule for its clock and severity (docs/SOAK_RESULTS.md:2249) |
| Masking is 83 % of the mining cost, 16.82 of 20.34 | docs/TEMPLATING_RESULTS.md:176-177 | masking was 74 to 87 % of the mining cost. Seven rewritten regexes take 85 to 87 % off it. Templates do not change by one line | docs/SOAK_RESULTS.md:2314-2316 | infologger masking alone fell from 11.93 to 1.82 core-seconds per million on 3,000,000 lines, −84.7 % (docs/SOAK_RESULTS.md:2318-2320). The whole-pipeline cost after the rewrite is not in the lines read for this brief |
| Contentless templates, Drain3 as shipped, infologger: 44.9 %, median 3 real words | docs/SOAK_RESULTS.md:2036 | with the recipe: 10.4 %, median 6 real words | docs/SOAK_RESULTS.md:2038 | round 4 called the templates readable on five examples (docs/TEMPLATING_RESULTS.md:104-119). The soak measured readability and the shipped configuration lost to PIPLUP on it, 5 words and 28.4 %, until the recipe overtook both (docs/SOAK_RESULTS.md:2037, docs/SOAK_RESULTS.md:2040-2043) |
| Per-family template counts on the whole corpus: 2,853, 813, 186 | docs/TEMPLATING_RESULTS.md:80-82 | on 3,000,000-line slices, shipped against recipe: infologger 908 to 675, stdout 766 to 936, dds 183 to 224 | docs/SOAK_RESULTS.md:2272-2274, docs/SOAK_RESULTS.md:2290 | the recipe changes the count in both directions. The whole-corpus count under the recipe is not in the lines read |
| Round 4 single-run costs are quoted without a noise figure | docs/TEMPLATING_RESULTS.md:129-134 | Drain3 on infologger read 17.32 and 22.33 on two runs of identical work, a spread of 28.9 % | docs/SOAK_RESULTS.md:2011-2015 | every round 4 cost carries that spread. A difference under about a quarter is not evidence |

### Numbers that still stand

| Number | Round 4 or 3 line | Soak confirmation | Line |
|---|---|---|---|
| 3,822 templates, whole corpus, one global tree | docs/TEMPLATING_RESULTS.md:83 | H1b reproduced 3,822 | docs/SOAK_RESULTS.md:1996 |
| 19.73 core-seconds per million, whole corpus | docs/TEMPLATING_RESULTS.md:134 | H1b read 19.91, and 21.80 before a rig fault was fixed | docs/SOAK_RESULTS.md:1996, docs/SOAK_RESULTS.md:2001-2002 |
| 17.27 core-seconds per million, infologger | docs/TEMPLATING_RESULTS.md:131 | H3b read 17.32, and 22.33 before the rig fault | docs/SOAK_RESULTS.md:1998, docs/SOAK_RESULTS.md:2001-2002 |
| 2,853 infologger templates | docs/TEMPLATING_RESULTS.md:80 | H3b reproduced 2,853 | docs/SOAK_RESULTS.md:1998 |
| Drain3 is the parser | docs/TEMPLATING_RESULTS.md:19 | Drain3 stays. PIPLUP costs 2.56 times on the whole corpus and 2.24 times on infologger, and holds 7.8 times the memory, 206.5 MB against 26.3 MB | docs/SOAK_RESULTS.md:1985, docs/SOAK_RESULTS.md:1996-1997, docs/SOAK_RESULTS.md:2007-2009 |
| Masking is what makes the tree cheap | docs/TEMPLATING_RESULTS.md:170-174 | masking was 74 to 87 % of mining cost, so the rewrite targeted it | docs/SOAK_RESULTS.md:2314 |
| 18,037 templates per core-second, static model; 581, transformer | docs/EMBEDDING_RESULTS.md:54, docs/EMBEDDING_RESULTS.md:57 | the soak reuses both to price 10,000 templates: 0.55 core-seconds once, or 17 core-seconds for the transformer, against roughly 780 core-seconds to mine the corpus. 0.07 % of one mining pass | docs/SOAK_RESULTS.md:2300-2304 |
| Steady-state new-template rate, 48.9 per million | docs/TEMPLATING_RESULTS.md:83 | not remeasured in the lines read | |
| The int8 warning | docs/EMBEDDING_RESULTS.md:34-38 | not revisited in the lines read | |
| Source purity as the quality metric and every purity figure | docs/EMBEDDING_RESULTS.md:53-59 | not revisited in the lines read | |

[A] The soak adds one new fact that round 4 could not: more templates are affordable. Depth 6 to depth 8 on stdout moved the templates covering 99 % of lines from 155 to 157. The other 50 templates landed in the tail with fewer than 100 lines each. The busy series the detectors use are untouched (docs/SOAK_RESULTS.md:2306-2310).

[A] The soak also corrects the corpus weight. Its recipe runs hold 43,972 dds lines against round 4's 267,607. Operations report dds is the highest-volume family during data-taking, and dds is the most expensive family per line. Every dds figure in the recipe section is measured on an under-sampled family (docs/SOAK_RESULTS.md:2290-2296).

## 5. Decisions settled and their evidence

1. [A] The parser is Drain3. PIPLUP lost gate 3 on cost, 2.56 times on the whole corpus against a gate of ±25 % (docs/SOAK_RESULTS.md:2005-2008). Ten more parsers lost on cost, on streaming, or on both (docs/SOAK_RESULTS.md:1986-1988). PIPLUP's one genuine edge is that it needs no per-family configuration (docs/SOAK_RESULTS.md:2043-2044). Its one placeholder type would take away the "lines containing an address" search (docs/SOAK_RESULTS.md:2046-2048).
2. [A] Production stays in Python. A rewrite buys back at most 0.39 of a core at 20,000 records a second (docs/TEMPLATING_RESULTS.md:152-156).
3. [A] One global tree, not one per source. 430 trees produce 3.6 times the templates for a 15 % saving (docs/TEMPLATING_RESULTS.md:186-197).
4. [A] Masking stays in front of the tree. Removing it costs 46 times more (docs/TEMPLATING_RESULTS.md:170). `<FLOAT>` and `<NUM>` stay separate (docs/SOAK_RESULTS.md:2252-2253).
5. [A] One masking and tree recipe per family. The three families want three different answers to the padded-separator knob: stdout keeps 88.3 % of words with `= ;`, infologger 78.6 % with `= ; :`, dds 75.5 % with `=` (docs/SOAK_RESULTS.md:2255-2266). The recipe is cost-neutral, 17.65 to 17.57 core-seconds per million weighted, and contentless templates fall from 44.9 % to 10.4 % on infologger and 26.4 % to 7.4 % on stdout (docs/SOAK_RESULTS.md:2275-2279, docs/SOAK_RESULTS.md:2287-2288).
6. [A] The masker regexes are rewritten. Same templates byte for byte, 85 to 87 % off the masking step (docs/SOAK_RESULTS.md:2314-2316).
7. [A] The embedding model is a static one, potion-base-32M. It costs 0.006 % of a core at the observed template rate and keeps 93 % of the transformer's quality (docs/EMBEDDING_RESULTS.md:27-30, docs/EMBEDDING_RESULTS.md:40-41).
8. [A] Embeddings are paid per template, not per line. This is what made round 3 affordable (docs/EMBEDDING_RESULTS.md:21-23).
9. [A] Quality is judged by macro source purity, not by neighbour agreement with a reference (docs/EMBEDDING_RESULTS.md:86-90, docs/EMBEDDING_RESULTS.md:99-103).
10. [A] Do not assume int8 helps. Measure it on the farm's x86 machines first (docs/EMBEDDING_RESULTS.md:134-137).

## 6. Not measured, verbatim

From round 4 (docs/TEMPLATING_RESULTS.md:206-222):

1. "Transport is not in any number here. A real sidecar also pays for the Fluent Bit output that feeds it and the write back to the local OpenSearch node. This round priced the mining, in process. The plan's cgroup-priced sidecar is still the only way to get the whole figure." (docs/TEMPLATING_RESULTS.md:206-209)
2. "The archive is October 2022 data. It is the real format in the real layout, and `docs/LOG_TYPES.md` records the same thing about the `/scratch` tree — but it is one period, and every run in it is MFT. A detector that never appears here contributes templates that this round has not counted." (docs/TEMPLATING_RESULTS.md:210-213)
3. "DDS and stdout are priced on raw lines, as noted above. Their true cost is lower and their template counts are upper bounds." (docs/TEMPLATING_RESULTS.md:214-215)
4. "The new-template rate is measured over an archive, not over a shift. A burst here is a new program appearing in the corpus order. On a live farm the same bursts would arrive at run starts and configuration changes, which this data cannot time." (docs/TEMPLATING_RESULTS.md:216-219)
5. "Drain's knobs were not swept at scale. `sim_threshold` 0.3 to 0.7 moved the template count from 11 to 14 on a 3,000-line probe and did not move cost at all, so the defaults were kept: threshold 0.4, depth 4, 100 children." (docs/TEMPLATING_RESULTS.md:220-222) Superseded in part by the recipe (docs/SOAK_RESULTS.md:2246-2250).

From round 3 (docs/EMBEDDING_RESULTS.md:143-159):

1. "The domain FastText rung was not run. The plan wants a FastText trained on our own templates, on the strength of `docs/RESEARCH.md`'s finding that a domain model beat LLM embeddings 0.766 to 0.257 Micro-F1 on an incident corpus. Training it properly means training over the 45.6-million-line corpus, not over 3,822 templates, and that is its own piece of work. It remains the most interesting untested rung, and the one with a real chance of beating everything above on our text specifically." (docs/EMBEDDING_RESULTS.md:143-149)
2. "The late-interaction stretch arm was not run. It is a reranking device and its cost lands on search rather than on the worker, so it does not compete with anything measured here." (docs/EMBEDDING_RESULTS.md:150-152)
3. "Batch size and thread count were not swept. The plan asks for batch size against intra-op thread count. Everything here ran at batch 64 on one thread, because at one template a second the batch is one and the sweep would describe a load this workload never reaches." (docs/EMBEDDING_RESULTS.md:153-156)
4. "Recall against a labelled retrieval task was not measured, because no labelled task exists for this data. Source purity is a proxy chosen for being answerable, and it is a proxy." (docs/EMBEDDING_RESULTS.md:157-159)

From the soak, stage H:

1. int8 on the farm's x86 backend: not measured (docs/EMBEDDING_RESULTS.md:134-137). Not revisited in the lines read.
2. PIPLUP's template dump is incomplete. 264 of its 2,444 strings are absent from the file, because 407 clusters held a second template. The readability comparison was read on what was written (docs/SOAK_RESULTS.md:2052-2055).
3. Gate 4, accuracy: "no accuracy claim is verifiable, and this is not a hedge" (docs/SOAK_RESULTS.md:2059). The body of that gate is outside the lines read.
4. The dds family in the recipe runs is under-sampled at 43,972 lines, 0.1 % of the weight (docs/SOAK_RESULTS.md:2290-2296).

## 7. Corrections recorded in the sources

1. [C] Round 4's read-loop instrument over-billed small families by up to a factor of 4.6. No published number rests on it (docs/TEMPLATING_RESULTS.md:228-236).
2. [C] Round 3's working once reported the null as 0.161 and a purity of 0.408 as 2.5 times the null. That null used the micro formula. The macro null is 0.035 and the models beat it eleven to twelve times. The purity figures did not change (docs/EMBEDDING_RESULTS.md:109-114).
3. [C] The soak once claimed the recipe was 21.7 % cheaper. That rested on an infologger control reading of 22.39 core-seconds per million. Five other clean runs read 14.68, 14.84, 14.92, 15.06 and 15.10. The 22.39 was the outlier and the saving was not real. The recipe delivers readability at the same price (docs/SOAK_RESULTS.md:2281-2285).
