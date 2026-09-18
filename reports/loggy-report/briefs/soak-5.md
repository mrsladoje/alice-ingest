# Soak brief 5 of 5 — Rounds 10 to 21

Source: docs/SOAK_RESULTS.md lines 4284 to 6107. Mandatory context: lines 11 to 203 and lines 263 to 306.
Every line reference below points at docs/SOAK_RESULTS.md unless stated otherwise.
Marking: **[A]** = architecture-level, belongs in the report. **[C]** = code-level, evidence only.

## 1. Coverage

This range holds Rounds 10 to 21. Rounds 10 to 17, 19 and 20 are external reviews of the collector and the template catalog, each one reproducing and fixing defects found by a reviewer (line 4284, 4443, 4567, 4644, 4739, 4824, 4905, 5216, 5645, 5796). Round 10 exposes a data-loss regression in one Fluent Bit version at log rotation (line 4334). Rounds 11 to 15 chase the ordering key that the catalog uses to scan its source indices through four answers (line 4739, 4824). Round 16 reports ten findings, the largest being that a retried bulk write duplicated every record (line 4919). Round 17 finds that the fix reached only the sources the test could see (line 5216). Round 18 prices the whole templating pipeline on all seven log families and is the figure of record for templating cost (line 5417). Rounds 19 and 20 fix catalog identity defects (line 5645, 5796). Round 21 prices the Forward transport hop into the stamper and removes a redundant re-encode (line 5926). No round in this range measures the collector's core-seconds or the flush knob; those results live in lines 11 to 203.

## 2. Valid numbers

All rounds in this range come after the 27 August 2026 retraction and stand unless a later line corrects them. The standing warning applies to every absolute figure: only ratios and rankings transfer off the laptop (line 265).

| number (exact) | what it measures | baseline or reference | what it means for the design | source line |
|---|---|---|---|---|
| 1 in 3 | failure rate of the rotation check on Fluent Bit 5.0.8 before the test was made deterministic | 4.0.14 and 3.2.8 passed 4 of 4 in the same loop | A single green run of a flaky test is not evidence. [C] | 4292-4294 |
| 5 of 5 lost | appended bytes lost after rename on Fluent Bit 5.0.8, deterministic test | 3.2.8, 4.0.1, 4.0.14: 3 of 3 arrive | Fluent Bit 5.x must not be installed; production stays on 4.x. This result depends on the version. [A] | 4313-4318 |
| 189,803 of 189,803 | orchestrator bulk replay, records received against expected | derived from the corpus after folding continuations | The collector loses nothing on the orchestrator family. [A] | 4424, 4546 |
| 60,000 of 60,000 | InfoLogger bulk replay, real archive rows | expected count | The collector loses nothing on InfoLogger. [A] | 4425, 4547 |
| 35 of 35 | fixture expectations on each of four Fluent Bit versions | — | The parse cascade holds across versions. [C] | 4413 |
| 43 real collector records, 0 rejected | index mapping check on OpenSearch 3.7.0 | — | Every produced field is searchable. [C] | 4417 |
| 1 of 189,803 | record missing in the first content-multiset check; last line held in the multiline parser | totals check had passed twice | Completeness checks must compare content, not totals. [C] | 4511-4515 |
| 5.0.8 → 4.0.14 | the collector role default Fluent Bit version, was and now | — | The default pointed at the version that loses data. [A] | 4526-4529 |
| 30 seconds | the shipped catalog index refresh interval | — | Any read after a refresh=false write must refresh first. [A] | 4578-4579 |
| 60s | the probe catalog refresh interval in the test | production 30 | The test window is longer than production so the race is real. [C] | 4607 |
| two minutes | CATALOG_SCAN_SLACK_MS, the scan cutoff in Round 13 | 30-second refresh | Superseded: the slack window is gone in Round 14 (line 4778). [C] | 4676-4679, 4778 |
| 0, 0, 0, 1, 1, 1 | _seq_no of six documents over three shards | — | Sequence numbers are per shard; the scan must read each shard alone. [A] | 4770-4772 |
| max_seq_no 1, local_checkpoint −1 | shard state at the failing scan with one indexing thread held | — | The scan must bound at global_checkpoint, not at the highest number seen. [A] | 4831-4832 |
| 43 applied, 1 attempted twice, 43 documents | retry check with the identifier, Fluent Bit 4.0.1 | control without identifier: 46 applied, 3 twice, 46 documents | A filter-assigned document identifier removes retry duplicates. [A] | 4957-4962 |
| 43 applied, 10 attempted twice, 43 documents | retry check with the identifier, Fluent Bit 4.0.14 | control: 55 applied, 12 twice, 55 documents | Same as above on the second deployed version. [A] | 4961-4962 |
| 409 | the response to a second create of an existing identifier | — | Deduplication refuses rather than overwrites. [A] | 4964-4967 |
| 25 records → 25 full serialisations | drain3 snapshots on every message with the shipped interval | — | Persistence must be attached only at the batch boundary. [C] | 5098-5102 |
| 20,000 clusters: 61.5 MB tree, 208.1 MB peak serialising, 1.10 MB serialised | template tree memory at the chosen ceiling | unit limit 512 MB | The ceiling of 20,000 templates fits with margin. [A] | 5107-5111, 5117 |
| 50,000 clusters: 112.6 MB tree, 437.2 MB peak | template tree memory above the ceiling | 512 MB | 50,000 would nearly fill the unit. [A] | 5112 |
| 100,000 clusters: 856.9 MB peak, 5.36 MB serialised | template tree memory far above the ceiling | 512 MB | Unbounded growth breaks the unit. [A] | 5113 |
| 1.75 KB a cluster; 6.5 KB a cluster transient | memory cost per template, in tree and while serialising | — | Serialisation, not the tree, sets the peak. [A] | 5115-5116 |
| 20,000 templates; 4.7 times 4,221 | the growth ceiling across all families, against the template count of the 55,963,050-line archive pass | — | The ceiling is 4.7 times what the whole archive produced. [A] | 5126-5127 |
| twenty-five fields kept of 234 distinct | the journal allowlist | — | The allowlist dropped doc_id; every allowlist now must keep it. [A] | 5229-5230 |
| 112 applied, 112 documents | journal retry arm, Fluent Bit 4.0.14, with identifier | control: 259 applied, 259 documents | Journal records deduplicate once the identifier passes the allowlist. [A] | 5250-5253 |
| 259 or 301 against 112 | journal control duplicate counts across runs; the stable fact is duplication itself | 112 with the identifier | The control numbers vary run to run and belong to no version (corrects lines 5250-5253 and 5787-5788). [C] | 5911-5917 |
| number_of_replicas: 2 | the shipped worker index layout | — | A refresh reports total as copies; only failed means a fault. [A] | 5343-5344 |
| 31 to 57 % | whole templating pipeline cheaper per family, all seven families, byte-identical output | shipped code, same host state | Templating cost per line falls by a third to a half. [A] | 5428-5429 |
| infologger 22.29 → 9.55, −57.2 %, 675 = 675 templates, 3,000,000 lines | figure of record, core-seconds per million lines | shipped arm | [A] | 5442 |
| dpl 12.98 → 8.59, −33.8 %, 920 = 920, 3,000,000 lines | figure of record | shipped arm | [A] | 5443 |
| datadist 13.48 → 9.36, −30.6 %, 191 = 191, 92,813 lines | figure of record | shipped arm | [A] | 5444 |
| dds 47.31 → 22.41, −52.6 %, 1,570 = 1,570, 1,236,971 lines | figure of record | shipped arm | [A] | 5445 |
| ildaemon 7.65 → 5.00, −34.6 %, 18 = 18, 163,670 lines | figure of record | shipped arm | [A] | 5446 |
| journald 16.84 → 8.68, −48.5 %, 883 = 883, 277,541 lines | figure of record | shipped arm | [A] | 5447 |
| odc 23.56 → 10.93, −53.6 %, 266 = 266, 190,250 lines | figure of record | shipped arm | [A] | 5448 |
| 12.61 against 22.29 | the same shipped code on infologger at midday and in the evening | — | The host was 1.7 times slower for an unnamed reason; quote ratios only. [C] | 5458-5464 |
| 86 to 90 % | pad step cost removed by the rewrite | shipped pad step | The pad was the most expensive single step in every padded family. [A] | 5484-5485 |
| 2 core-seconds a million | the anchored-sub strip cost on a 387-character dds line under Python 3.9 | — | Production Python does not stop an anchored search early. [C] | 5486-5488 |
| 6 to 14 % | strip gap on Python 3.11, where the interpreter already stops early | — | The gain depends on the interpreter; production is 3.9. [C] | 5496-5497 |
| 8,961,245 lines, 0 differences | candidate prepare against shipped prepare, every line of every family | — | The rewrite is byte-identical. [A] | 5537 |
| 8,961,245 × 3 identical | the cluster every line landed in, three candidates | shipped path | The per-line cluster check is the strong one. [C] | 5540, 5547-5550 |
| 800,000 × 3 pad sets and 800,000 × 6 rules, 0 differences | differential fuzz of the pad and strip rewrites, Python 3.9 and 3.11 | — | [C] | 5544-5545 |
| 15 to 21 % | the audit round's masker costs more than round 6's, all in the number stage | round 6 masker | The number stage is at its floor. [C] | 5554-5555 |
| 89 to 100 % | lines carrying a digit | — | No gate can skip the number stage. [A] | 5555-5556 |
| 14 to 29 % | the two-pass number rewrite loses on number-dense families | shipped fold | The Python loop beats a second C scan; masker ships unchanged. [C] | 5583-5589 |
| 68 of 68 on 3.9 and on 3.11 | template_catalog unit tests after Round 18 | — | [C] | 5630 |
| tree arms −40 to −52 %, whole pipeline −13 to −55 % | Python 3.11 cross-check of the same arms, three rounds | shipped arm | The ranking holds on a second interpreter. [C] | 5632 |
| 0 against 1 | catalog count after replacement, lookup failure stored as null against batch left pending | — | Not knowing the catalog identity must keep the batch pending. [A] | 5681-5684 |
| 2 against 1 | count for one source record, name trusted after the first request against identity re-read per page | — | The index identity must be checked on every page. [A] | 5714-5717 |
| 0, 1, 0, 2 | documents after the next pass: identity read after the write against destination fixed before it, first publication and node with history | — | The catalog destination must be fixed before the write. [A] | 5850-5855 |
| 75 of 75 | template_catalog unit tests after Round 20 (was 29 at Round 10) | — | [C] | 5898, 4420 |
| seventeen sequences | catalog document-level sequences after Round 20 (was three at Round 10) | — | [C] | 5902, 4386-4390 |
| 15 per cent | Forward loop cheaper per record, every returned byte the same | shipped forward path | The stamper no longer re-encodes a record it did not change. [A] | 5933-5935 |
| 32 per cent and 24 per cent | share of the infologger and dpl wire that is repeated key names | — | The only wire saving from a schema; on a Unix socket it is free, so protobuf is rejected. [A] | 5946-5948 |
| infologger 60,000 records, 25.5 MB, 425 bytes a record, 16 fields, 4,615 records a chunk | the captured Forward wire | — | [A] | 5959-5961 |
| dpl 120,000 records, 43.8 MB, 365 bytes a record, 12 fields, 5,454 records a chunk | the captured Forward wire | — | [A] | 5962 |
| −15.1 / −15.4 %, −6.5 % | infologger transport alone and whole stamper hop, median of 15 pairs, two passes | shipped path | Transport is about a quarter of the hop. [A] | 5975-5982 |
| −14.8 / −16.2 %, −4.0 % | dpl transport alone and whole stamper hop | shipped path | [A] | 5978 |
| 3.63 and 5.58 | core-seconds a million for the same code on infologger an hour apart | — | Quote ratios only. [C] | 5984-5986 |
| encode 1.69 → 0.28 (infologger), 2.14 → 0.24 (dpl) | per-record encode step, shipped against spliced | — | Encode falls 83 to 89 per cent; decode does not move. [C] | 5992-6007 |
| 288 of 3,000 | fuzzed chunks where the overwrite guard fired and fell back; all still matched | — | The splice falls back safely when a key already exists. [C] | 6055-6056 |
| 180,000 records, 0 differences; 12,000 fuzzed chunks, 0 differences; 20,000 real Fluent Bit round-trip records, 0 errors | byte-identity checks of the splice | shipped forward.py | [C] | 6060-6070 |
| 24 of 24 | stamper unit and acceptance tests | — | [C] | 6070 |
| 15 per cent, not 32 to 41 | the corrected transport gain against the landed baseline | first prototype baseline | The first write-up compared against a weaker baseline. [C] | 6100-6107 |

## 3. Retracted or superseded

"Read this first" (lines 11 to 203) withdraws claims from Stage C onward of Round 2. It withdraws nothing in Rounds 10 to 21. Every retraction below is made inside this range by a later round.

| claim | withdrawn by | line |
|---|---|---|
| Round 9's "all four Fluent Bit versions pass" the rotation check | one sample of a test that fails 1 in 3 | 4287-4288, 4304-4306 |
| rotate_wait: 30 as a fix for the rotation loss | measured no effect, removed | 4346-4348 |
| Round 10: absolute counts fenced by a pass number make a retry safe | a retry that reads further under the same pass number loses the second record | 4448-4464 |
| Round 10: comparing record totals is a completeness check | two records in, one shipped twice and one lost, totals match | 4485-4489 |
| Round 11: the pending-batch machinery covers recovery | the cleanup search after a state loss is refresh-bound | 4569-4571, 4573-4590 |
| Round 12's README sentence: an unsearchable record "is simply read on a later pass" | the position is a sort key; a late record sorts before it | 4646-4653 |
| Round 13: collector_time as ordering key | replaced by ingest_time | 4665-4670 |
| Round 13: the two-minute slack cutoff | removed in Round 14, the sequence number makes it redundant | 4676-4679, 4778-4781 |
| Round 13: ingest_time as ordering key | stamped on receipt, indexing can follow out of order; replaced by _seq_no | 4741-4760 |
| Round 14: the visibility sequence discriminated between ordering keys | its write helper used refresh=true, so the condition never existed; claim removed | 4783-4805 |
| Round 14: _seq_no highest seen as the position | assigned before the write reaches Lucene; bounded by global_checkpoint instead | 4824-4846 |
| Round 15's visibility assertion that a durable, unrefreshed record is read on no pass | the scan now refreshes the source itself; read on the next pass exactly once | 4880-4889 |
| The file-sink test rig as evidence against retry duplication | a file sink never retries, so it never tested the property | 4921-4928 |
| retrycheck first version: operations counted from the request | inflated by about a factor of two; counted from the response now | 4978-4982 |
| retrycheck second version: waiting for the document count to go quiet | the quiet arrives before the retry; waits for a record attempted twice now | 4984-4992 |
| retrycheck fault injection by silently closing the connection | retried only in most runs; answers 503 now | 4994-5001 |
| Round 16 README: the position is collector_time plus an identifier | three replacements out of date; carries the four-key table now | 5178-5184 |
| Round 16: the document identifier reaches every source | the journal allowlist dropped it | 5224-5235 |
| Round 16: the recovery listing rejects a partial answer | it did not check the refresh that feeds the listing | 5266-5276 |
| Round 16: the pass number floor from the clock survives a crash after the clear | the clear's number was held in memory only | 5278-5302 |
| Round 16: the catalog identity read before the write | on a first publication the index does not exist yet, stored null | 5304-5314 |
| Round 16: a replaced catalog triggers a re-mine from the source indices | the sources have aged out; rebuild from the trees instead | 5316-5335 |
| Round 16: successful < total on a refresh is a fault | total counts configured copies; an unassigned replica is normal on the shipped layout | 5337-5356 |
| retrycheck compared documents against identifiers the proxy saw | a record lost before the proxy cancels out; independent file-sink expectation now | 5367-5374 |
| Round 17: the catalog identity read after the write, null on failure | null reads as unchanged; the batch stays pending now | 5657-5676 |
| Rounds 13 to 16: the index UUID read once per pass | replaced index answers to the old name; re-read per page now | 5689-5709 |
| retrycheck listing keyed by _id alone | _id is unique only within an index; keyed by index and identifier | 5719-5728 |
| retrycheck listing tested status >= 300 as failure | a refused connection returns 0 and passes; all incomplete reads raise now | 5737-5754 |
| Rounds 17 and 19: the identity read after the write | a lookup that succeeds names the replacement; the destination is fixed before the write now | 5806-5838 |
| Rounds 17 and 19: journal control 301 on 4.0.1 and 259 on 4.0.14 as version figures | counts vary run to run; 259 on 4.0.1 and 301 on 4.0.14 this run | 5911-5917 |
| Round 21 first write-up: transport gain 32 to 41 per cent | measured against a prototype baseline; 15 per cent against the landed code | 6100-6107 |
| Round 18 absolute core-seconds against round 6's | the host moved 1.7 times between runs; ratios only | 5458-5464, 5636-5638 |
| Round 21 per-step timings as a budget | the steps overshoot the whole by about a fifth; direction only | 6000-6003 |

## 4. Defects found and fixed

Each line: component — what was wrong — line.

1. Rotation test harness — settle fired before the tail's next sweep; the test mutated the tracked fixture tree; the rotation fired on a fixed sleep — 4298-4302. [C]
2. Fluent Bit 5.0.8 — loses bytes appended to a file after it is renamed away; production stays on 4.x and the rig default moved — 4334-4344. [A]
3. Template catalog — a generalised template became two documents across passes — 4352-4356. [A]
4. Template catalog — a retry after a lost progress save doubled the count; counts became absolute and per node — 4358-4370. [A]
5. Template catalog wipe detector — read a document count after a refresh=false write, saw zero and re-mined every pass; asks whether the index exists now — 4372-4377. [C]
6. Bulk replay check — computed every figure over the records that arrived, so loss was invisible — 4392-4403. [C]
7. Template catalog — absolute counts did not survive a retry that reads further; the batch is written down before publication — 4448-4476. [A]
8. Template catalog — a counter pass number restarts on a reprovisioned node and fences it out; milliseconds since the epoch instead — 4475. [A]
9. Completeness check — compared totals; compares the multiset of record contents now — 4485-4494. [C]
10. Regex derivation — Onigmo reads (?m) as dot-matches-newline, Python does not; (?s) used — 4499-4502. [C]
11. Replay harness — the last corpus line stayed in the multiline parser when the sink went quiet; settle now longer than flush timeout — 4511-4515. [C]
12. Collector role — fluent_bit_version defaulted to the version that loses data; default 4.0.14 and a guard against the 5. prefix — 4517-4533. [A]
13. Catalog oneshot — an idle pass printed nothing and looked dead; says so explicitly — 4555-4558. [C]
14. Template catalog recovery — the cleanup search ran inside the 30-second refresh window and found nothing; refreshes first — 4573-4594. [A]
15. Catalog test — the inspection helper supplied the refresh whose absence was the defect — 4598-4609. [C]
16. Catalog scan — collector_time as ordering key skipped late-visible and late-indexed records — 4646-4670. [A]
17. Catalog round-trip check — probe records had no ingest_time, so the scan mined nothing; unscannable counter added — 4703-4710. [C]
18. Catalog scan — ingest_time is stamped on receipt and indexing can follow out of order; _seq_no per shard per concrete index instead — 4741-4776. [A]
19. Catalog scan — _seq_no highest seen is assigned before Lucene indexing; bounded by global_checkpoint with stats, refresh, scan in that order — 4824-4859. [A]
20. Collector output — a retried bulk chunk duplicated every record; a document identifier assigned in a filter, counter of node, process UUID and record — 4919-4950. [A]
21. Catalog scan — a timed-out or shard-failed search read as empty; partial refresh skips the index, partial search stops the shard, partial listing aborts recovery — 5027-5047. [A]
22. Catalog position — keyed by index name; a recreated index restarts at zero; keyed by name and UUID — 5049-5057. [A]
23. Catalog identity — a recreated catalog resolved by name and the node stayed idle; identity is the catalog UUID — 5059-5064. [A]
24. Catalog pass number — a clock stepped backwards made every update a no-op that reported success; floor is the larger of clock and last written — 5072-5091. [A]
25. Template tree — serialised the whole tree once per record; handler attached only at the batch boundary — 5093-5122. [A]
26. Template tree — no size bound; ceiling of 20,000 templates, stops learning and keeps counting — 5124-5136. [A]
27. Catalog scan — one busy shard starved the shards behind it; per-shard budget and rotating start — 5138-5143. [C]
28. Catalog retirement — superseded_by set while another node still contributed; set only when the last contribution goes — 5145-5156. [A]
29. Catalog programs — a generalised template kept only this batch's programs; programs accumulate in state — 5158-5167. [C]
30. State file rename — the directory was never synchronised; one fsync on the directory — 5169-5176. [C]
31. Journal allowlist — dropped doc_id, so journal records duplicated on retry — 5224-5237. [A]
32. Catalog recovery — checked the listing and not the refresh that feeds it — 5266-5276. [C]
33. Catalog clear — a crash after the clear lost the pass number it used; write-ahead in issued — 5278-5302. [C]
34. Catalog identity — first publication recorded no catalog because the write creates the index — 5304-5314. [C]
35. Catalog replacement — re-mined from short-lived sources; rebuilds from the trees — 5316-5335. [A]
36. Catalog refresh check — an unassigned replica read as a failed refresh; only failed means a fault — 5337-5356. [A]
37. Retry proxy — shared temporary filename across threads lost responses — 5360-5365. [C]
38. Retry check — no independent expectation; file-sink expectation per destination — 5367-5379. [C]
39. Retry check — the static allowlist failure was counted but never printed — 5381-5384. [C]
40. Templating pipeline — pad step used regex escape and a callback per separator; strip walked the whole line; drain3 per-token Python loops; wrapper masked and split twice — 5491-5522. [A]
41. Catalog identity — a failed lookup stored null and read as unchanged; batch stays pending — 5657-5676. [A]
42. Catalog scan — the index UUID trusted after one read; re-read per page — 5689-5709. [A]
43. Retry check — listing keyed by _id alone collapsed cross-destination duplicates — 5719-5735. [C]
44. Retry check — incomplete reads read as empty; a refused connection passed status >= 300 — 5737-5754. [C]
45. Catalog destination — read after the write cannot say where the write went; index created explicitly and identity recorded before the payload — 5806-5845. [A]
46. Mapping check — its own catalog reader ignored refresh, timeout and shard failures — 5860-5880. [C]
47. Stamper transport — re-encoded every record to add two fields; splices the original bytes and caches the stamp tail per template version — 6005-6022. [A]
48. Stamper splice — a record already carrying a stamp field duplicated the key; a length guard falls back to the full encode — 6039-6056. [C]
49. Forward framing — msgpack Unpacker.tell() mis-counts after OutOfData at a feed boundary; a fresh scan per receive — 6079-6089. [C]

## 5. Decisions this range settles

| decision | evidence | line |
|---|---|---|
| Fluent Bit 5.x is blocked in the collector role; 4.0.14 is the default. [A] | 5.0.8 loses appended bytes 5 of 5 after rename; three 4.x and 3.x versions never do | 4313-4318, 4526-4533 |
| Catalog counts are absolute per node, not deltas. [A] | an increment cannot be retried; a set can | 4364-4370 |
| Local catalog state is one file replaced atomically, and the batch is written before it is published. [A] | the publication window is the case that must be retried | 4369, 4474 |
| The catalog scan orders by _seq_no per shard per concrete index, bounded by global_checkpoint, after reading stats and refreshing. [A] | three earlier keys each skipped a record in reproduction | 4841-4859 |
| Every record carries a document identifier assigned in a filter, and the output writes with create. [A] | 43 documents with it, 46 and 55 without | 4937-4967 |
| The identifier is a counter of node, process UUID and record, not a content hash. [A] | a hash collapses distinct identical lines | 4944-4950 |
| The template tree ceiling is 20,000 templates; at the ceiling the worker stops learning and keeps counting; drain3's LRU eviction is not used. [A] | 208 MB peak against 512 MB; eviction would reset counts to one | 5124-5136 |
| Tree persistence happens once per batch, at the batch boundary. [A] | 25 records produced 25 serialisations | 5098-5122 |
| The catalog index identity is the UUID, created explicitly before the first write. [A] | 0 against 1 and 0 against 2 documents in reproduction | 5828-5855 |
| Templating pipeline: strip by match and slice, pad by replace and split, drain3 loops as builtins, miner called at the Drain with a token list. [A] | 31 to 57 % cheaper, byte-identical on 8,961,245 lines | 5428-5431, 5491-5522, 5537 |
| Negative: the masker's number stage ships unchanged. [A] | six rewrites all within run-to-run spread; no rewrite wins on every family | 5583-5589 |
| Negative: a two-pass FLOAT then NUM masker loses. [C] | 14 to 29 % worse on number-dense families | 5584-5586 |
| The catalog role should pin drain3 0.9.11. [A] | the rewrites reproduce that version's methods line by line | 5618-5623 |
| Negative: protobuf on the Forward hop is rejected. [A] | the wire is msgpack already; the only saving is 24 to 32 % key names on a Unix socket | 5940-5948 |
| Negative: gzip on the Forward output is rejected. [A] | it spends processor time to save free bandwidth | 5950-5951 |
| The stamper splices the record's own bytes and caches the stamp tail per template version. [A] | encode falls 83 to 89 per cent; transport 15 per cent cheaper | 6005-6022 |
| Negative: partial decodes of the record in Python lose to the C unpacker. [C] | three attempts, 1.46 against 1.30, 12.82 against 1.30 | 6027-6037 |
| Fluent Bit flush stays at 1. [A] | Round 21 did not touch it; the basin stands | 6093-6094 |
| Every fix in Rounds 16 to 20 is run against its own absence before it counts. [C] | mutation tables | 5009-5013, 5388-5396, 5763-5770, 5884-5892 |

## 6. Not measured, not tested, or unmeasured

Verbatim, with lines.

- "**Template grouping correctness.** Cost, count, readability and contentless share are measured. Whether events that belong together are grouped together is not." — 4431-4433. Repeated as "Unchanged and still open" at 4560, 4637, 4732, 4817, 4901, 5212, 5411, 5792, 5919.
- "**Minimum processor cost.** This host cannot resolve below about a fifth, so 'no arm differs measurably' is the strongest statement available here." — 4434-4435.
- "**Active-run source coverage.** No run has been active during any census." — 4436.
- "**Source-owner approval**, and **whether a collector goes on the storage node**. Both are decisions, not work." — 4437-4438.
- "The tenth — the missing directory synchronisation — is a code-level gap the reviewer was explicit about not having reproduced, and it is fixed on the same terms." — 4908-4910.
- "The concurrent gap cannot be produced on a live cluster without pausing an indexing thread" — 4863-4864; tested against a stubbed shard instead, 4865-4868.
- "One thing this round does NOT change: `images/node/fluent-bit/collector.yaml`, the bundled local rig, carries neither the identifier nor the Lua filter it hangs off." — 5199-5201.
- "The host was 1.7 times slower for a reason the instruments on it could not name." — 5461-5462.
- "the `python:3.9-slim` container round 6's method calls for could not be pulled from the Colima VM, so this round is native." — 5639-5641.
- "Nothing in these fixtures or in that journal reaches two destinations, so the collapsed key was hiding nothing here — it was waiting for the first record that did." — 5788-5790.
- "Round 18 made the templating 31 to 57 per cent cheaper and left the hop that carries the records to it unmeasured." — 5928-5929.
- "These do not sum to the end-to-end figures and must not be read as a budget." — 6000-6001.
- "60,000 real records never contain a stamp field, because nothing upstream of the stamper writes one." — 6047-6048.
- From the mandatory sections, applying to every number here: "One micro-benchmark on `epn228` would give the conversion factor. It has not been run." — 289-290.

## 7. Cross-range notes

- Line 4286-4288 and 4304-4306: Round 10 withdraws Round 9's rotation result (outside this range, before line 4284). A reconciler must not carry "all four versions pass" from Round 9.
- Line 4429: Round 10's "still not proven" list is "unchanged from round 9"; the list originates outside this range.
- Line 5126-5127: the 4,221 templates from the 55,963,050-line archive pass come from an earlier round (Rounds 7 to 9, per line 5642). Cite the origin from the brief covering those rounds.
- Line 5419-5426: Round 18 builds on Round 6's masker rewrite and gate (line 1983 onward, outside this range); line 5638 says Round 18 absolutes must not be quoted against Round 6's.
- Line 5642-5643: Round 18's corpora are Rounds 7 to 9's; dpl capped at 3,000,000 archive lines.
- Line 5584-5586: Round 18 settles "a question round 6 left open" about a two-pass masker.
- Line 5199-5203: the local rig's collector.yaml does not carry the identifier, the Lua filter or "the two-clock filter"; the two-clock filter is documented outside this range.
- Line 5928-5930: Round 21 answers an open item from docs/TEMPLATES_FIX_PLAN.md section 9.
- Line 6093-6094: Round 21 depends on the flush 1 result in lines 11 to 203 ("the basin measured earlier still stands").
- Line 5618-5623: the drain3 pin recommendation is directed at the catalog role; whether the role now pins it is not stated in this range.
- Line 5609-5616: Round 18 says the catalog service still pays the wrapper and second split; collecting that gain "is left to the round that owns that file". No round in this range reports doing it.
- Kafka: nothing in this range touches either Kafka decision. Line 5940-5951 rejects protobuf and gzip on the Forward hop only.
- Outside the file: git history shows commit 64cc9ae "remove the canonical template identifier, group by the cover relation" and a September rework of the catalog into a stamper on the Forward loop (docs/TEMPLATES_FIX_PLAN.md). Rounds 10 to 20 describe the template_catalog service with counts_by_node and superseded_by. The reconciler must decide which catalog design the report describes as current. This brief cannot settle it from its range.

## 8. Story

- Ten reviews in a row found the same shape of mistake: a property stated in prose that the code did not have, with a test that passed for another reason (line 4714-4718). Every fix in Rounds 16 to 20 is now run against its own absence before it counts.
- Fluent Bit 5.0.8 loses every byte appended to a file after rotation, 5 of 5 runs; three older versions never do. The role now blocks the 5. prefix and defaults to 4.0.14 (lines 4318, 4528-4530).
- Finding the right scan position took four answers. Anything chosen before indexing cannot order by indexing; only the sequence number bounded by global_checkpoint marks what has completed (lines 4836-4846).
- A retried bulk write duplicated every record, and a file sink could never show it. A filter-assigned identifier turns 46 and 55 documents back into 43, and 259 or 301 journal documents back into 112 (lines 4957-4962, 5911-5917).
- The template tree serialised itself once per record. At 20,000 templates the peak is 208.1 MB against a 512 MB unit, and that count is 4.7 times what the whole archive produced, so 20,000 is the ceiling (lines 5107-5127).
- The whole templating pipeline is 31 to 57 per cent cheaper per family with every one of 8,961,245 lines landing in the same cluster. The pad step, not the masker, was the most expensive single step (lines 5428, 5484, 5537).
- The masker's number stage is at its floor: six rewrites all land inside run-to-run noise, so it ships unchanged (line 5583-5589).
- The Forward hop into the stamper is 15 per cent cheaper because the record is spliced rather than re-encoded; that lands as 4 to 6 per cent on the whole hop because transport is only a quarter of it (lines 5975-5982). Protobuf and gzip on that hop are rejected on reasoning, not measurement (lines 5940-5951).
