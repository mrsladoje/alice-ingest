# Round 3, reader test judge

Lens: grade the twelve round-3 reader answers against the briefs, then report only what the
report itself caused. Answers: `reviews/round3-reader-answers.md`. Comparison:
`reviews/round2-reader-answers.md`.

## Grades

| Q | Grade | Whose fault |
|---|-------|-------------|
| 1 info line, copies, survivor | correct | — |
| 2 dead collector to a person | **partly** | the report |
| 3 queue rejected, what reopens it | correct | — |
| 4 the two Kafka decisions | correct | — |
| 5 processor and memory taken | correct | — |
| 6 flush 5 s to 1 s | correct | — |
| 7 template and stamper | correct | — |
| 8 built against agreed | correct | — |
| 9 what 0.685 means | correct | — |
| 10 why costs do not transfer | correct | — |
| 11 six objectives | correct | — |
| 12 detector, monitor, episode, notification | correct | — |

Eleven of twelve are correct and the reader now marks every answer "sure". One answer carries a
contradiction it took straight from the text.

## What improved against round 2

- Q5 rose from partly to correct. The report now separates the allocated cores and caps from the
  measured memory, and the reader no longer complains that the provenance is missing.
- Q8 rose from partly to correct. Section 6.3 now states the farm pilot's scope, so the reader
  could split built, agreed and open without guessing.
- Q11 was wrong on a number in round 2 ("two copies of every record above info") and is right now
  ("three copies ... one on each storage machine").
- Q12 rose from partly to correct. Episode and incident document no longer read as two things.
- Q3 and Q4 kept their correct grade and lost the round-2 confusion about the live lane's latency
  floor, which the report no longer argues in two directions.
- Round-2 ambiguities 1, 2, 4, 5, 9, 18 (trace lines, farm primaries, monitor counts, detectors
  against detector monitors, live-lane latency, live-lane content) are gone from the round-3 list.

## Reader complaints I checked and rejected

- Monitor and detector arithmetic. Section 3.6 and Figure 6 agree: 13 plus 4 threshold monitors,
  9 trend, 2 hourly on templates, 2 break-glass, 30 in all; 14 plus 3 detectors, 17 in all. The
  reader must add, but nothing is wrong.
- Memory caps. The 384 MB in Section 3.4 sits inside the collector subsection, the 512 MB in 5.5
  names the stamper, and the 384 MB in 5.6 names the shifter host.
- Bucket windows. Section 3.5 says "per family and window" and Table 3 names both windows, so the
  stamper writes both. The text could say it once more plainly, but it is not wrong.
- Farm machine counts in 2.3 against 5.4. Section 5.4 names the storage machine among the four.

## Findings

### 1 (must) The poller does not flag a dead collector within 30 seconds

- File: `sections/03-design-b.tex`, line 19
- Quote: "Within 30~seconds the poller writes the collector's row with a missing flag."
- Problem: the poller writes a row every 30 seconds, but the flag only says missing after 90
  seconds of silence, so this sentence contradicts "A rostered collector silent for 90 seconds is
  absent" in 3.6 and the "60 to 150~seconds" two sentences later.
- Source: `briefs/walkthroughs-3-5.md:64`
- Fix: "Every 30~seconds the poller writes one row for each rostered collector. The row says
  missing once no sample has arrived for 90~seconds."
- Effect: the reader's Q2 answer repeats both figures and cannot be right in both.

### 2 (should) The abstract's equivalence number is nowhere in the body

- File: `sections/00-abstract.tex`, line 1
- Quote: "The templating pipeline is 31 to 57~\\% cheaper per family and byte-identical on
  8,961,245 lines."
- Problem: 8,961,245 never appears again, and the only byte-identical evidence in 5.5 and Table 7
  is the masker's "zero differences on 3,000,000 lines per family", which prices a different
  change.
- Source: `briefs/soak-5.md:55`, `briefs/soak-index.md:27`
- Fix: in `06-eval-b.tex` line 3 write "Templating is built, 31 to 57\\,\\% cheaper per family than
  its first version, and every one of 8,961,245 lines kept its template (Table~\\ref{tab:templating})."
- Effect: the reader listed this as a contradiction and could not check the headline.

### 3 (should) "Passed every check" hides what ran on the farm

- File: `sections/01-intro-problem.tex`, line 9
- Quote: "It runs on five staging machines, and one farm deployment passed every check."
- Problem: the farm pilot ran the cluster, the collectors and Dashboards only, so page one claims
  more maturity than 6.3 allows.
- Source: `briefs/target-architecture.md:215`, `briefs/constraints.md:286`
- Fix: "It runs on five staging machines, and one farm deployment of the cluster, the collectors
  and Dashboards passed every check." Make the same change in the abstract and the conclusion.
- Effect: the reader had to correct page one from Section 6.3 to answer Q8.

### 4 (should) "Page" is both a severity tier and an act

- File: `sections/03-design-b.tex`, line 5
- Quote: "Threshold monitors catch cliffs in two tiers: a storage disk above 92~\\% pages, and
  above 85 up to 92~\\% warns."
- Problem: the same section says "Nothing pages a human today", so one verb carries two meanings
  and the two sentences read as a contradiction.
- Source: `briefs/walkthroughs-3-5.md:121`
- Fix: "Threshold monitors catch cliffs in two tiers: a storage disk above 92~\\% raises a
  page-tier alert, and above 85 up to 92~\\% raises a warn-tier alert." Keep "pages a human" for
  the act alone, here and in 3.8.
- Effect: the reader listed the collision and had to state twice that no person is reached.

### 5 (should) The 53,000 figure carries its caveat ten pages later

- File: `sections/05-eval-a.tex`, line 17
- Quote: "The collector's memory sat between 133 and 228~MB at every rate up to 53,000 records a
  second."
- Problem: 53,000 was read against a test output that always accepts, which only Section 6.1 says,
  so 5.2 reads as a measured rate against the real store.
- Source: `briefs/soak-index.md:109`, `briefs/soak-index.md:510`
- Fix: "The collector's memory sat between 133 and 228~MB at every rate up to 53,000 records a
  second, against a test output that always accepts."
- Effect: the reader put 5.2 and 6.1 side by side as an unresolved pair.

### 6 (should) DDS is called largest in one place and densest in another

- File: `sections/07-limits-close.tex`, line 29
- Quote: "DDS is the densest family during data-taking and the dearest per line."
- Problem: 2.1 says operations call DDS the largest family during data-taking, and the briefs give
  volume and cost per line as two separate facts, so "densest" merges them into one claim.
- Source: `briefs/constraints.md:131`, `briefs/templating-embedding.md:171`
- Fix: "DDS is the largest family during data-taking and the dearest per line."
- Effect: the reader flagged largest and densest as one claim in two words.

### 7 (should) Table 7 says masking was removed, not its cost

- File: `sections/06-eval-b.tex`, line 20
- Quote: "Masking removed by seven rewritten regular expressions & 85 to 87\\,\\% & zero
  differences on 3,000,000 lines per family \\\\"
- Problem: the row reads as if masking itself went away, and its range overlaps the "74 to 87~\\%"
  in Section 4, which is masking's share of the mining cost, a different quantity.
- Source: `briefs/soak-index.md:169`, `briefs/soak-3.md:51`
- Fix: "Masking cost cut by seven rewritten regular expressions & 85 to 87\\,\\% & zero differences
  on 3,000,000 lines per family"
- Effect: the reader called the two figures "two different quantities with overlapping ranges".

### 8 (should) The development set and the held-out set are never separated

- File: `sections/06-eval-b.tex`, line 32
- Quote: "They score 0.685 against 0.634 for the dense scan alone on the development set of 130
  questions, bootstrap interval +0.028 to +0.080."
- Problem: the held-out set arrives in the next sentence with no statement of how it differs, so a
  reader cannot tell which of 0.685 and 0.689 is the stronger evidence.
- Source: `briefs/semantic-2.md:55`, `briefs/semantic-1.md:145`
- Fix: add before the scores: "The development set of 130 questions chose the settings. The
  held-out set stayed sealed until the end."
- Effect: the reader reported both pairs of numbers without being able to rank them.

### 9 (should) Section 4 prices the miner, then says the stamper is unpriced

- File: `sections/04-why.tex`, line 48
- Quote: "The stamper's cost is not measured."
- Problem: the same paragraph gives 8.33 core-seconds per million for mining, so the reader cannot
  tell whether the mining figure is the stamper's cost or something else.
- Source: `briefs/soak-index.md:161`, `briefs/soak-5.md:9`
- Fix: "Those figures price the miner on a stored corpus. What the stamper service costs beside a
  running collector is not measured."
- Effect: the reader listed this as an ambiguity under Q5's cost question.

### 10 (should) Five families, six rows

- File: `sections/01-intro-problem.tex`, line 19
- Quote: "A worker holds five log families, and InfoLogger is one of them
  (Table~\\ref{tab:sources})."
- Problem: Table 1 carries six rows, because the run orchestrator log is listed although it sits on
  the shared infra machine and is not collected.
- Source: `briefs/constraints.md:85`, `briefs/constraints.md:104`
- Fix: caption Table 1 "Five families reach a worker's collector. The sixth row is written on the
  shared infra machine and is not collected."
- Effect: the reader counted six rows against the sentence's five in round 2 and again in round 3.

## Nothing else

No further must-level finding. The remaining items on the reader's undefined-term list (stdout
tree, heap, primary, watermark, upserted, break-glass, cockpit, SSE stream, boot id, poison replay,
knees) are jargon load, which the voice lens owns, not answers the report got wrong.
