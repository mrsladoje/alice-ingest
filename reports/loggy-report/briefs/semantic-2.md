# Semantic retrieval brief 2 of 2: rounds 9 to 17 and the final standing recommendation

Source: `docs/SEMANTIC_RESULTS.md`. Lines read: 13 to 143 (Morning report) and 1417 to 2379 (rounds 9 to 17). Every claim cites `docs/SEMANTIC_RESULTS.md:LINE`. Numbers are copied exactly.

Marking: **[A]** = architecture-level fact, belongs in the report. **[C]** = code-level or run-level evidence only.

## 0. The final standing recommendation, after every correction

The final recommendation is one search box with one toggle, and it is provisional (docs/SEMANTIC_RESULTS.md:28, :79). **[A]**

| Component | Final choice | Final number | Line |
|---|---|---|---|
| Search box, natural-language questions | Dense model (`DenseOn`) scans all templates by exact cosine, returns 30 candidates, late-interaction model (`LateOn`) reranks them by MaxSim | 0.685 nDCG@10 against 0.634 for the dense model alone, interval +0.028 to +0.080, deterministic | docs/SEMANTIC_RESULTS.md:2363 |
| Exact-identifier toggle | BM25F, the lexical engine already deployed | Success@1 1.000 against 0.600 for the dense model, on development data | docs/SEMANTIC_RESULTS.md:2364, :33 |
| Find-similar action on a template | BM25F | 0.690 against 0.340 for the dense model | docs/SEMANTIC_RESULTS.md:2365 |
| Fusion of lexical and dense scores | none | Reciprocal Rank Fusion at the upstream constant is 0.048 worse than the dense model alone | docs/SEMANTIC_RESULTS.md:2366 |
| Approximate vector index (HNSW) | not at this corpus size | costs 0.020 nDCG@10 and varies between graph builds | docs/SEMANTIC_RESULTS.md:2367, :2345 |
| Cheap static fallback | not established; P7, trained on judgements, is the only lead | training on labels buys +0.063 on unseen queries | docs/SEMANTIC_RESULTS.md:37 |

The reranking step costs 0.6 ms; all remaining latency is candidate fetching (docs/SEMANTIC_RESULTS.md:39, :2224). **[A]**
The reranking token matrix is 194 MB for 5,301 templates and grows with the corpus (docs/SEMANTIC_RESULTS.md:54-55). **[A]**
The recommendation does not become the production selection until a person judges the pool (docs/SEMANTIC_RESULTS.md:76-79). **[A]**

Two corrections produced this table:

- Round 16 replaced "dense model alone" with "dense candidates plus late reranking at depth 30" (docs/SEMANTIC_RESULTS.md:2206-2210). Round 14 had discarded that system for missing a self-chosen parity tolerance by 0.0014 (docs/SEMANTIC_RESULTS.md:2254-2258).
- Round 17 replaced the approximate index with an exact cosine scan. Rebuilding the index moved the same configuration from 0.684 to 0.665 nDCG@10 and parity from 0.9902 to 0.9482 (docs/SEMANTIC_RESULTS.md:2300-2306, :2312-2313).

## 1. What semantic retrieval is for, and what unit is searched

Semantic retrieval lets an operator find log templates by asking a question in their own words (docs/SEMANTIC_RESULTS.md:58, :68-70). **[A]**
The mechanism it fixes is a vocabulary gap: 47.1 % of relevant natural-language (query, document) pairs share no analysed term (docs/SEMANTIC_RESULTS.md:67-70). **[A]**
Operators ask in their own words and templates are written in the developers' words (docs/SEMANTIC_RESULTS.md:69-70). **[A]**

The unit searched is the log template, not the raw log line. The corpus holds 5,301 templates (docs/SEMANTIC_RESULTS.md:36, :54, :2350). **[A]**
The exact cosine scan is one matrix-vector product against a 5,301 by 768 matrix (docs/SEMANTIC_RESULTS.md:2350-2351). **[A]**

Three tasks exist and the plan forbids averaging them: natural language, exact identifier, template similarity (docs/SEMANTIC_RESULTS.md:19-26). **[A]**
The vocabulary gap does not exist when the query is itself a template or a literal identifier; there the lexical engine wins (docs/SEMANTIC_RESULTS.md:70-73). **[A]**

## 2. What was built

**The corpus.** The corpus is 5,301 log templates (docs/SEMANTIC_RESULTS.md:2350). A freeze manifest was written at 03:46:01Z and the held-out set was opened at 04:07:47Z, 22 minutes later (docs/SEMANTIC_RESULTS.md:1432-1434). The sealed queries and intents still hash to the values recorded on 7 September at 23:29:28Z (docs/SEMANTIC_RESULTS.md:1441-1442). Templates are long-tailed: 13,097 judged items hold templates of 300 characters or fewer, 2,862 hold 301 to 1,000, and 1,402 hold over 1,000 (docs/SEMANTIC_RESULTS.md:1664-1666). The longest is 10,451 characters (docs/SEMANTIC_RESULTS.md:1656). **[A]** for size and unit; **[C]** for timestamps.

**The judgements.** Machines wrote the queries and machines graded every judgement; no person has judged the pool (docs/SEMANTIC_RESULTS.md:76-78). The development pool grew to 18,624 pooled candidates across five judging rounds, with every arm at 1.000 judged coverage except the lexical systems at 0.995 (docs/SEMANTIC_RESULTS.md:1705-1706). By round 13 it held 19,917 graded candidates across six rounds (docs/SEMANTIC_RESULTS.md:1864). The held-out set holds 60 natural-language queries over 48 intent groups with 2,793 pooled candidates, all judged, at 1.000 coverage at rank 10 and rank 20 (docs/SEMANTIC_RESULTS.md:1453-1456). The custodian also authored 8 identifier and 6 similarity intent groups (docs/SEMANTIC_RESULTS.md:1487-1488). The development labels have 17 assessor pairs; the held-out labels have no measured agreement because one custodian judged alone (docs/SEMANTIC_RESULTS.md:1511-1515). **[A]** for the fact that labels are machine-made; **[C]** for pool sizes.

**The harness and adapters.** The harness checks judged coverage, manifests, tie-breaks and bootstrap intervals (docs/SEMANTIC_RESULTS.md:2376). Every run manifest carries its own dependency lock hash (docs/SEMANTIC_RESULTS.md:1852-1853). A frozen practical difference of 0.032 nDCG@10 is the bar for a material effect (docs/SEMANTIC_RESULTS.md:1471, :2059-2060). A production-form adapter uses the search engine's `knn_vector` field to retrieve candidates and MaxSim reranks them from a token matrix held beside it (docs/SEMANTIC_RESULTS.md:2080-2082). The harness has no notion that a number from a randomised construction must be reproduced before it is quoted (docs/SEMANTIC_RESULTS.md:2376-2378). A circular measurement passes every check the harness makes (docs/SEMANTIC_RESULTS.md:2120-2122). **[C]**

**The benchmark.** The benchmark is three separate leaderboards that must not be averaged (docs/SEMANTIC_RESULTS.md:1482-1483). The complete development leaderboard lists 25 systems from H7 at 0.695 down to a distilled table at 0.120 (docs/SEMANTIC_RESULTS.md:1710-1736). The plan required eleven hybrid systems, H0 to H10 (docs/SEMANTIC_RESULTS.md:1570-1583). A blind preference judge saw 96 changed result lists and preferred the dense model on 68 of 93 decided pairs (docs/SEMANTIC_RESULTS.md:60-62). Two adversarial reviews were run; the second raised eight objections of which five hold (docs/SEMANTIC_RESULTS.md:1931-1933). **[C]**

## 3. Valid numbers table, corrections applied

| Number | What it measures | Reference | Meaning | Line |
|---|---|---|---|---|
| 0.689 | Held-out nDCG@10, `P-DenseOn`, natural language | BM25F 0.371; `P-potion-retrieval` 0.353 | Dense model wins the headline task on the sealed set | docs/SEMANTIC_RESULTS.md:1449-1451 |
| +0.318, interval +0.219 to +0.418, p 0.000 | Held-out paired difference, dense over BM25F | Set floor 0.052; practical difference 0.032 | Six times the floor; not borderline | docs/SEMANTIC_RESULTS.md:1462, :1421-1424 |
| −0.017, interval −0.098 to +0.067, p 0.696 | Held-out paired difference, static fallback over BM25F | Floor 0.052 | Undetermined, not equal | docs/SEMANTIC_RESULTS.md:1463, :1426-1429 |
| 0.819 / 0.822 / 0.906 | Held-out MRR@10 / Recall@20 / Success@5, `P-DenseOn` | BM25F 0.443 / 0.437 / 0.594 | Same direction on every metric | docs/SEMANTIC_RESULTS.md:1449-1450 |
| 47 of 48 | Held-out intent groups entering the paired bootstrap | one corpus-coverage case excluded | Rule from round 6 applied | docs/SEMANTIC_RESULTS.md:1465-1467 |
| 96 of 110 | Live-path queries whose visible results change | control BM25F | 81 score higher, 29 lower | docs/SEMANTIC_RESULTS.md:1523-1524, :1533 |
| +377 / −204 | Relevant templates gained / lost at rank 10 on the live path | control BM25F | A trade, not a strict improvement | docs/SEMANTIC_RESULTS.md:1534, :1527 |
| 1 ms / 12 ms | Live-path p50 latency, control / treatment | machine-authored 300 ms p95 target | Twelve-fold rise, inside target; laptop figures | docs/SEMANTIC_RESULTS.md:1536, :1546-1549 |
| 2 ms / 226 ms | Live-path p99 latency, control / treatment | same | Single slow query | docs/SEMANTIC_RESULTS.md:1538, :1548 |
| 28.6 % | Relevant documents in dense top ten absent from BM25F's top 200 | — | Why unweighted fusion dilutes the winner | docs/SEMANTIC_RESULTS.md:1599-1600 |
| 39.2 % / 54.6 % | Relevant fraction of BM25F / dense top ten | — | Fusion gives an equal vote to a weaker list | docs/SEMANTIC_RESULTS.md:1602-1603 |
| 596 MB / 258 MB | float32 weights, dense model / static fallback | no memory ceiling applies | Cost of shrinking is unmeasured | docs/SEMANTIC_RESULTS.md:1630-1632, :1642-1645 |
| 0.635 | Development nDCG@10, dense model alone | BM25F 0.468; L0 plain BM25 0.396 | Development baseline for every hybrid | docs/SEMANTIC_RESULTS.md:1718, :1731, :1734 |
| 0.634 | Dense model alone, as quoted in the final recommendation | — | Rounds 16 and 17 use 0.634 where the leaderboard shows 0.635 | docs/SEMANTIC_RESULTS.md:2207, :2363 |
| 0.643 | Development nDCG@10, tuned RRF (constant 10, lexical weight 0.5) | unweighted RRF at 60: 0.586 | Maximum of sixteen configurations, no selection correction | docs/SEMANTIC_RESULTS.md:1715, :1721, :1739-1742, :1994-1996 |
| 0.692 / 0.695 / 0.691 | H7 with approximate candidates at depths 50 / 100 / 200 | within 0.004 of each other | Noise, not a peak at 100 | docs/SEMANTIC_RESULTS.md:1987-1990 |
| 0.749 | Candidate recall at depth 100 for H7 | — | Caveat round 12 omitted | docs/SEMANTIC_RESULTS.md:1990-1992 |
| 0.670, candidate recall 0.274 | H10 cross-encoder at depth 20 | — | Starved, not weak; depth 50 capped | docs/SEMANTIC_RESULTS.md:1751-1756 |
| 0.5114, +0.039, interval +0.007 to +0.070, Holm 0.26 | Best trained static model over stock 0.4750 | practical difference 0.032 | Clears raw bars, fails Holm across fifteen arms | docs/SEMANTIC_RESULTS.md:1876, :1882, :1907-1915 |
| 0.08 | Holm-adjusted p under a narrower four-arm family | threshold not met | "Marginal", not "clear failure" | docs/SEMANTIC_RESULTS.md:2000-2004 |
| 0.0002 | Difference between distilled (P2) and Tokenlearn (P3) tables | — | Training on ALICE text adds nothing; the training did run | docs/SEMANTIC_RESULTS.md:1889-1892, :2016-2019 |
| 0.1186, −0.356 | Static table distilled from the dense model as teacher | stock 0.4750 | Strongest retriever is the worst teacher | docs/SEMANTIC_RESULTS.md:1884, :1772-1778 |
| 247 vs 672 vs 686 | Distinct documents returned across 112 queries, dense-teacher table vs BGE table vs stock | label-free measure | The collapse needs no labels to see | docs/SEMANTIC_RESULTS.md:1680-1683 |
| 0.065 | Mean cosine between the 29,525 shared tokens, vocabulary on vs off | near-orthogonal | Vocabulary gain is a full re-basis, not the new tokens | docs/SEMANTIC_RESULTS.md:2007-2011 |
| 0.462 | Lowest weighted kappa across 25 assessor pairs | threshold 0.60 | Gate fails, on deep top-up rounds with 86 % grade 0 | docs/SEMANTIC_RESULTS.md:1699, :1815, :1822-1826 |
| 0.689 to 0.884 | Weighted kappa on the original benchmark rounds 1 and 2 | threshold 0.60 | The pool every headline rests on passes | docs/SEMANTIC_RESULTS.md:1837-1839 |
| 0.767 and 0.608 | Gwet's AC1 on the two failing pairs | threshold 0.60 | Labels are better than kappa suggests; gate as written still fails | docs/SEMANTIC_RESULTS.md:2022-2028 |
| 0 hits | Term query on `identifiers.exact` for one token before the fix | 1 hit for the whole joined string | Every exact-identifier term clause returned nothing | docs/SEMANTIC_RESULTS.md:1949-1953 |
| −0.042 / +0.035 | L3 identifier anchoring / field weights, re-measured after the fix | practical difference 0.032 | Anchoring still hurts; field weights now kept | docs/SEMANTIC_RESULTS.md:1963-1965, :1977-1980 |
| 0.421 | L3 full port after the fix | BM25F 0.468 | Port not promoted | docs/SEMANTIC_RESULTS.md:1982-1983 |
| 0.6305, −0.0006, 768 bytes | int8 quantisation of the dense vectors | float32 0.6311 at 3,072 bytes | int8 is free; saves three quarters of the index | docs/SEMANTIC_RESULTS.md:2040-2046 |
| 0.5490, −0.0821 | binary quantisation | same | Costs more than twice the practical difference | docs/SEMANTIC_RESULTS.md:2043-2047 |
| 0.6016, −0.0295 | 256 dimensions | 768 at 0.6311; practical difference 0.032 | Just inside the practical difference | docs/SEMANTIC_RESULTS.md:2059-2062 |
| 5.0 / 6.3 / 7.7 ms | Cold p50 / p95 / p99 serving latency | 300 ms provisional target | Far inside target | docs/SEMANTIC_RESULTS.md:2068, :2073 |
| 9.9 / 26.4 / 62.0 ms | p50 / p95 / p99 at 8 workers, 613 queries per second | same | Far inside target | docs/SEMANTIC_RESULTS.md:2070 |
| 0.690 → 0.677, overlap 0.9786 | H7 production form at depth 100, offline vs production | tolerance 0.98 | Fails parity by 0.0014 | docs/SEMANTIC_RESULTS.md:2086-2094 |
| 0.533 vs 0.470 vs 0.461 | P7 supervised static model on 43 unseen queries vs P2 vs stock | practical difference 0.032 | +0.063 over best distilled; one split, machine labels | docs/SEMANTIC_RESULTS.md:2131-2133, :2143-2147, :2154-2155 |
| 0.115 | Overfitting gap in P7's first reported number | — | First figure was measuring memorisation | docs/SEMANTIC_RESULTS.md:2110, :2138-2141 |
| 0.6866 vs 0.6845, 59 MB vs 194 MB, 72 ms, overlap 0.913 | FastPlaid approximate multi-vector search vs exhaustive oracle | export parity 0.95 | Quality free; parity fails as written | docs/SEMANTIC_RESULTS.md:2159-2169 |
| 0.108 | Variance in top 16 components, stock static table | distilled tables 0.159 to 0.183 | Stock table is flatter | docs/SEMANTIC_RESULTS.md:2186-2196 |
| 0.684, 16.5 ms p95, rerank 0.6 ms | Approximate index depth 30 plus reranking, first build | dense alone 0.634 | Superseded by round 17 | docs/SEMANTIC_RESULTS.md:2219, :2300-2302 |
| 0.665, overlap 0.9482 | Same configuration, second index build | first build 0.684, 0.9902 | Approximate index unstable across builds | docs/SEMANTIC_RESULTS.md:2312-2313 |
| 0.685, +0.054, interval +0.028 to +0.080 | Exact cosine depth 30 plus reranking, deterministic | dense alone 0.634 | **Final search-box number** | docs/SEMANTIC_RESULTS.md:2335, :2363 |
| 0.660 / 0.670 / 0.681 / 0.688 / 0.690 | Exact cosine plus reranking at depths 15 / 20 / 25 / 50 / 100 | all intervals exclude zero | Monotonic; from depth 25 clears 0.032 | docs/SEMANTIC_RESULTS.md:2331-2342 |
| 0.020, 37 % | Cost of the approximate index at depth 30, and share of the reranking gain | 0.685 exact vs 0.665 approximate | Do not use HNSW at 5,301 templates | docs/SEMANTIC_RESULTS.md:2344-2346 |
| 0.031 | Gain of an oracle query router | practical difference 0.032 | No router is worth writing; use a toggle | docs/SEMANTIC_RESULTS.md:2287-2292 |

## 4. What failed, was capped, or was never reached

- The held-out identifier and similarity leaderboards were sealed but not scored in round 9, and round 9 states they cannot now be scored (docs/SEMANTIC_RESULTS.md:1486-1493). The Morning report states they were run afterwards and shows held-out numbers (docs/SEMANTIC_RESULTS.md:24-26, :86-89). See flag in section 7.
- Six of eleven required hybrid systems were never run in the first pass: H1, the tuned H4 variant, H5, H7, H8, H10; H6 ran unweighted and so was not H6 (docs/SEMANTIC_RESULTS.md:1570-1590). Round 12 later ran H4 tuned, H7, H8 and H10 (docs/SEMANTIC_RESULTS.md:1710-1716).
- The H10 cross-encoder depth-50 arm was capped: depth 20 took 1,845 seconds for 2,200 pairs, so depth 50 needed an estimated 77 minutes against a 45-minute budget (docs/SEMANTIC_RESULTS.md:1753-1756).
- `mLateOn` was capped at 49 minutes against a 45-minute budget with no ranking produced (docs/SEMANTIC_RESULTS.md:118-119).
- SP0, the learned sparse control, is excluded on security grounds: the container fails every HTTPS request to the model host, and the local model needs `trust_remote_code=True` with no operator allowlist (docs/SEMANTIC_RESULTS.md:1799-1809). No learned sparse system was compared (docs/SEMANTIC_RESULTS.md:1809).
- L2, the pinned reference, extracted zero entities from 5,301 templates; L3 was built but the port in full scores 0.421 against 0.468 and is not promoted (docs/SEMANTIC_RESULTS.md:109-113, :1982-1983).
- The assessor agreement gate fails at 0.462 against 0.60; the rubric-revision branch was not taken (docs/SEMANTIC_RESULTS.md:1822-1835).
- The environment moved mid-run: installing `tokenlearn` downgraded torch from 2.14.0 to 2.11.0; stages S3 to S7, S9 and the held-out evaluation ran on the first, the static models and the search-engine screen on the second (docs/SEMANTIC_RESULTS.md:1846-1853). **[C]**
- The exact-identifier term clause never worked before round 14 because the keyword field indexed the joined identifier string whole; fixed to a keyword array (docs/SEMANTIC_RESULTS.md:1937-1958). **[C]**
- No trained static model is promoted (docs/SEMANTIC_RESULTS.md:1864-1868).
- H7 at depth 100 failed production parity by 0.0014 (docs/SEMANTIC_RESULTS.md:2093-2094). FastPlaid failed export parity at 0.95 (docs/SEMANTIC_RESULTS.md:2165).
- Round 16's depth-30 approximate figures did not survive a second index build (docs/SEMANTIC_RESULTS.md:2300-2302). Every approximate-search figure in the file rests on one graph construction (docs/SEMANTIC_RESULTS.md:2321-2324).
- P7's first number, 0.650, was measuring memorisation; the harness did not catch it (docs/SEMANTIC_RESULTS.md:2112-2122).
- A label bias against templates over 1,000 characters exists: 2.1 % relevant against 22.9 % for short templates; an assessor graded with a 300-character read window (docs/SEMANTIC_RESULTS.md:1654-1667, :1676-1678).
- Four times a rule the run wrote for itself was given the authority of a measurement: the memory ceiling, the round 8 fusion verdict, the parity threshold, and the single unreproduced build (docs/SEMANTIC_RESULTS.md:2260-2274, :2371-2374). Three of the four were caught by the operator rather than the instrument (docs/SEMANTIC_RESULTS.md:2378-2379).

## 5. Decisions settled

- **Which model.** `lightonai/DenseOn` rev `cb9947eb`, representation R1, for candidates; `lightonai/LateOn` rev `62911e10` for reranking (docs/SEMANTIC_RESULTS.md:32). BM25F for the identifier toggle and the find-similar action (docs/SEMANTIC_RESULTS.md:2364-2365). **[A]**
- **Which unit.** Templates; 5,301 of them (docs/SEMANTIC_RESULTS.md:2350). **[A]**
- **Which reranking depth.** 30 candidates by exact cosine, then MaxSim (docs/SEMANTIC_RESULTS.md:2363). Depth 30 gives +0.054 with interval +0.028 to +0.080; depth 25 is the first depth that clears 0.032 (docs/SEMANTIC_RESULTS.md:2335, :2339-2340). **[A]**
- **Candidate generation.** Exact cosine scan, not an approximate index; reconsider when the corpus grows by two orders of magnitude (docs/SEMANTIC_RESULTS.md:2306, :2354-2357). **[A]**
- **Fusion.** None (docs/SEMANTIC_RESULTS.md:2366). **[A]**
- **Router.** A toggle, not a query router; an oracle router gains only 0.031 (docs/SEMANTIC_RESULTS.md:2287-2292). **[A]**
- **Static fallback.** Keep the stock static model; P7 is a lead, not a deployable result (docs/SEMANTIC_RESULTS.md:1864, :2154-2155). **[A]**
- **Quantisation and dimensions.** int8 costs 0.0006; 256 dimensions costs 0.0295; both inside the practical difference (docs/SEMANTIC_RESULTS.md:2045, :2059-2060). No quantised arm was run as a full leaderboard entry (docs/SEMANTIC_RESULTS.md:83-84). **[A]**
- **Memory ceiling.** Withdrawn. The `alice_service_memory_*` variables govern thin control-host pollers, not a retrieval model; real EPN nodes are not memory constrained (docs/SEMANTIC_RESULTS.md:1634-1645). No unit has been chosen to host the embedder (docs/SEMANTIC_RESULTS.md:1627-1628). **[A]**
- **Deploy or not.** The recommendation is provisional and does not become the production selection until a person judges the pool (docs/SEMANTIC_RESULTS.md:76-79). The 29 queries that get a worse answer deserve a person's eye before anything ships (docs/SEMANTIC_RESULTS.md:126-128, :1541-1544). The file does not record a deployment. **[A]**

**On the memory note ("peaks near 360 MiB against a 384 MiB ceiling, ships disabled").** The figures 360 MiB and 384 MiB do not appear anywhere in `docs/SEMANTIC_RESULTS.md` (targeted string search of the whole file, no match). The only memory figures in this file are 596 MB and 258 MB of float32 weights (docs/SEMANTIC_RESULTS.md:1630-1631), the 194 MB token matrix (docs/SEMANTIC_RESULTS.md:54), and the 59 MB FastPlaid index (docs/SEMANTIC_RESULTS.md:2162). This file withdraws every memory-ceiling objection (docs/SEMANTIC_RESULTS.md:1642-1645). The note's figures are therefore unverifiable from this file; they must come from another document, probably the shifter cockpit plan.

## 6. Not measured or not run, verbatim

- "No quantised arm was run, so the loss from shrinking any model is unmeasured." (docs/SEMANTIC_RESULTS.md:83-84)
- "The held-out labels have no measured agreement." (docs/SEMANTIC_RESULTS.md:102)
- "SP0, the maintained learned sparse control, was never attempted." (docs/SEMANTIC_RESULTS.md:114) — round 12 refines this to "excluded, not skipped" (docs/SEMANTIC_RESULTS.md:1799)
- "Stage S8, the ALICE static models, was never reached." (docs/SEMANTIC_RESULTS.md:115) — rounds 12 and 13 later ran it (docs/SEMANTIC_RESULTS.md:1860)
- "were **not scored**" and "they cannot now be scored" — held-out identifier and similarity (docs/SEMANTIC_RESULTS.md:1488, :1493)
- "It is undetermined, not equal" — static fallback against BM25F on the sealed set (docs/SEMANTIC_RESULTS.md:1428)
- "they are not a substitute for the load test the plan asks for, which was not run." (docs/SEMANTIC_RESULTS.md:1549-1551)
- "Whether weighting recovers the loss is untested" (docs/SEMANTIC_RESULTS.md:1609)
- "no quantised arm was run, so the cost of shrinking either is still unmeasured." (docs/SEMANTIC_RESULTS.md:1631-1632)
- "The effect on nDCG@10 is small. It is not zero, and nobody has measured it." — kappa disagreement effect (docs/SEMANTIC_RESULTS.md:1841-1842)
- "this run can no longer claim one environment" (docs/SEMANTIC_RESULTS.md:1853)
- "explained variance is missing from every Stage S8 run" (docs/SEMANTIC_RESULTS.md:2031-2032); "This run did not capture it at training time, and that cannot be recovered after the fact." (docs/SEMANTIC_RESULTS.md:2181-2183)
- "The update test is weak and should be read as such" — only five template updates landed in the window (docs/SEMANTIC_RESULTS.md:2073-2077)
- "This run did neither, because doing so after seeing the parity number is the tuning the plan forbids." — raising depth or tightening HNSW at depth 100 (docs/SEMANTIC_RESULTS.md:2102-2104)
- "No repeat build was made until the operator asked for one more sweep" (docs/SEMANTIC_RESULTS.md:2321-2322)
- The total latency of the final exact-cosine-plus-reranking path is not stated as one number in this range. The exact scan ran in "single-digit milliseconds" (docs/SEMANTIC_RESULTS.md:2351-2352) and the rerank costs 0.6 ms (docs/SEMANTIC_RESULTS.md:2224). The 16.5 ms p95 figure belongs to the approximate-index path (docs/SEMANTIC_RESULTS.md:2219).
- No EPN-hardware latency exists; all latency figures are laptop figures on a warm in-process index (docs/SEMANTIC_RESULTS.md:1548-1549).

## 7. Cross-range notes for the other semantic brief (rounds 1 to 8)

- **Held-out identifier and similarity numbers.** Round 9 says these were not scored and cannot now be scored (docs/SEMANTIC_RESULTS.md:1486-1493). The Morning report shows held-out 0.882 / Success@1 1.000 / DenseOn 0.839 for identifier and 0.690 / 0.340 for similarity, and says the leaderboards "were run afterwards" (docs/SEMANTIC_RESULTS.md:24-26, :86-89). Rounds 16 and 17 label the identifier Success@1 comparison as "on development" (docs/SEMANTIC_RESULTS.md:2364). Whoever holds the round that ran them must say which set they are on. If it is outside both ranges, the orchestrator must decide.
- **Round 8's fusion verdict is superseded.** Round 11 and round 12 replace "fusion is harmful" with "unweighted fusion of BM25F with DenseOn gives nothing"; tuned fusion scores 0.643 and reranking 0.695 on development (docs/SEMANTIC_RESULTS.md:1585-1590, :1739-1742). Brief 1 must not carry round 8's conclusion as standing.
- **Round 4's fixture claim** was an earlier over-assertion caught by the adversary (docs/SEMANTIC_RESULTS.md:1647-1650). Brief 1 should mark it as corrected.
- **Round 6 power calculation.** 62 intent groups resolve 0.052 while the project cares about 0.032 (docs/SEMANTIC_RESULTS.md:1470-1471). Round 9 says the limitation did not bite by luck, not design (docs/SEMANTIC_RESULTS.md:1469-1474).
- **Stage S2c hard-constraint list** carried a memory ceiling that is now withdrawn (docs/SEMANTIC_RESULTS.md:1642-1644). Brief 1 must not present any candidate as removed by memory.
- **Stage S5 removed `pplx-embed-v1-0.6b`** on the `trust_remote_code` rule; the same rule later excludes SP0 (docs/SEMANTIC_RESULTS.md:1807-1809).
- **The 0.634 vs 0.635 figure.** The development leaderboard lists the dense model alone at 0.635 (docs/SEMANTIC_RESULTS.md:1718); rounds 16 and 17 use 0.634 (docs/SEMANTIC_RESULTS.md:2207, :2363). The report should quote 0.634 for the final comparison, because the interval +0.028 to +0.080 is computed against it.
- **DenseOn as a static teacher collapses** to 0.120 on development and 0.1186 in round 13 (docs/SEMANTIC_RESULTS.md:1736, :1884). Both are the same arm; quote whichever table the report uses.
