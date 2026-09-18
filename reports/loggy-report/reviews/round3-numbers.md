# Round 3 review — lens: numbers

Scope: every number in the abstract, the eight parts, the tables and the captions,
traced to a still-valid results section or to a brief line that cites one.
Checked for unit, rig, scope, reference and internal consistency.
Derived numbers recomputed.

Sources read: docs/SOAK_RESULTS.md (11-203, 263-306, plus targeted ranges),
docs/SOAK.md, docs/SEMANTIC_RESULTS.md, docs/TEMPLATING_RESULTS.md,
briefs/soak-index.md, briefs/templating-embedding.md, briefs/semantic-2.md,
briefs/why.md, briefs/target-architecture.md, briefs/walkthroughs-3-5.md,
briefs/walkthroughs-6-9.md, briefs/constraints.md, reports/inputs/dataflow-atlas.md,
and the deployment files for the two index settings the briefs left open.

Result: 2 must, 3 should, 0 further findings worth the reader's time.

---

## Must

### 1. The 19.3 % spread is processor time, not wall clock

File: sections/05-eval-a.tex, line 58.

Quote: "Two identical configurations differ by up to 19.3~\% in wall clock, so the
rig cannot resolve a change below about a fifth."

Problem: 19.3 % is the spread of the fixed instrument's **processor** time, and the
sentence inverts round 9's finding, because wall clock was the quantity that looked
falsely reproducible at 0.8 %.

Source: docs/SOAK_RESULTS.md:3935-3953 (the table columns are Min cpu-s, Median
cpu-s, cpu-s/M; "the control arm ... lands 9.2 % below on one corpus and 19.3 %
above on another"); docs/SOAK_RESULTS.md:3900-3912 ("Round 8's 0.8 % control was
false precision"); briefs/soak-index.md:220.

Fix: "Two identical configurations differ by up to 19.3~\% in processor time, so the
rig cannot resolve a change below about a fifth."

Severity: must.

### 2. The 31 to 57 % baseline is not the first version

File: sections/06-eval-b.tex, line 3.

Quote: "Templating is built, 31 to 57\,\% cheaper per family than its first version,
with identical output (Table~\ref{tab:templating})."

Problem: the round 18 baseline is the pipeline as shipped at that date, which already
carried the per-family recipes and the rewritten masker, so naming it the first
version double-counts the 85 to 87 % masker gain in the same subsection, and it
collides with "the first version's 19.91" four paragraphs earlier in
sections/04-why.tex line 48.

Source: docs/SOAK_RESULTS.md:5419-5429 ("Round 6 rewrote the masker ... This round
prices every step of recipe_prepare"), docs/SOAK_RESULTS.md:5440-5448 (the Shipped
column); briefs/soak-index.md:238.

Fix: "Templating is built, and the September rewrite of the steps around the miner
made it 31 to 57\,\% cheaper per family, with identical output
(Table~\ref{tab:templating})."

Severity: must.

---

## Should

### 3. The 133 to 228 MB envelope is one rig, and the section contradicts it

File: sections/05-eval-a.tex, line 17.

Quote: "The collector's memory sat between 133 and 228~MB at every rate up to 53,000
records a second."

Problem: that range is round 1 only, on two cores against a sink that always accepts,
and Table 7 two paragraphs later reports 62.7 and 87.1 MB at 5,000 records a second,
so "at every rate" is false as written.

Source: docs/SOAK.md:18 ("Between 133 MB and 228 MB across the working range");
docs/SOAK_RESULTS.md:26 (62.7 MB and 87.1 MB); briefs/soak-index.md:59.

Fix: "Against a sink that always accepts, the collector's memory sat between 133 and
228~MB from 1,000 up to 53,000 records a second. On the four-core rig at 5,000 a
second it peaked at 62.7~MB."

Severity: should.

### 4. Three sources, not three of the five families

File: sections/05-eval-a.tex, line 54.

Quote: "The collector had never read three of the five families. Those are the second
process-tree format, written by the data-distribution processes, the InfoLogger daemon
log and the system journal."

Problem: by the report's own Table 1 the second process-tree format is one of the two
formats of a family the collector already read, so only two of the five families were
unread, and the count of three belongs to sources.

Source: docs/SOAK_RESULTS.md:2999-3021 ("This round adds three sources the collector
never read, splits a fourth into the two formats it always was", and the source table
marking dpl as a new format); briefs/soak-index.md:23; sections/01-intro-problem.tex
line 28 (the process tree is one family "in two line formats").

Fix: "The collector had never read three of its sources. Those are the second
process-tree format, written by the data-distribution processes, the InfoLogger daemon
log and the system journal."

Severity: should.

### 5. The 4.7 times ceiling is against the recipe corpora, not the whole archive

File: sections/02-design-a.tex, line 71.

Quote: "The stamper holds at most 20,000 templates per worker, 4.7 times what the whole
archive produced."

Problem: 4.7 is 20,000 over the 4,221 templates one tree mined from the three recipe
corpora of 55,963,050 lines, and the report's own later corpus of 56,628,579 lines
mined 5,571 templates, which makes the margin 3.6 times, so "the whole archive" claims
a scope the number does not have.

Source: docs/SOAK_RESULTS.md:5124-5136 (20,000 ceiling, 4.7 times 4,221);
docs/SOAK_RESULTS.md:2632-2652 (4,221 on 55,963,050 lines);
docs/SEMANTIC_RESULTS.md:206 (5,571 templates on 56,628,579 lines);
briefs/soak-index.md:199-200.

Fix: "The stamper holds at most 20,000 templates per worker, 4.7 times the 4,221 that
one tree mined from the three recipe corpora."

Severity: should.

---

## Checked and correct

Recomputed or traced without a finding: the abstract's eight figures against the body;
23, 78 and 9,781 records a second and the 248,828,513-record, 312-host archive;
0.25 of a core at 20,000 a second; 75.32 and 11.19 core-seconds per million with the
1.61 % and 9.48 % floors; Table 7 (108.40, 104.67, 36.70, 27.70, 87.1, 62.7, and the
-3.4, -24.5 and -28.0 per cent changes); the 17.9 % flush range; 7.4, 11.5 and 26.5 per
cent for the threading arms; 89.3 %; 41 to 68 %; 66.7 %; 865,674 records of 310 bytes,
13.8 and 46.8 a second, 17.4 and 5.1 hours; 42,000, 50,000, 86 % and six million
documents; 82 %, 1.8 % and 263 seconds; 2.1 against 6.7 and 2.7 against 5.5 per cent;
538, 4.3 and 54 times; 41.8, 99.83, 99 and 96.94 per cent; 2,500 to 10,500 journal
entries; 99 defects with 32 in the instrument; 19.91, 3,822, 17.57 against 17.65,
44.9 to 10.4 per cent, 3,011, 4,092, 4,221, 48.9 per million, 20,000 and 208.1 MB
against 512 MB; 8,961,245 lines; 15, 6.5, 4.0 and 32 per cent on the stamper hop;
2.56 and 7.8 times; 5,301 groups, 56,628,579 lines, 47.1 %, 18,037, 93 %, 31 times,
0.636, 5.4 times, 0.387 against 0.414 and 0.035, 0.685 against 0.634 with +0.028 to
+0.080 on 130 questions, 0.689 against 0.371, 377 and 204, 29 of 110, 0.020, 0.048,
1 ms, 12 ms and 0.6 ms; 30 monitors as 13 plus 4 plus 9 plus 2 plus 2, 17 detectors,
6 local detectors, 1 forecaster; 85, 92, 0.5, 0.7, 30, 90, 120, 250 and 600 second
constants; the 90 to 270 second bound; 8, 35, 56, 4, 66 and 7 day retentions; the
template bucket index at 1 shard and 1 replica (confirmed in
deploy/roles/loggy_opensearch/defaults/main.yml:228); 20 shards per gigabyte, about 60
and about 135; 128, 307, 384 and 768 MB; 64 chunks and 500 live-lane records; 20,000
query rows; 22 causal edges; 31,000 lines of Python; 43,972 and 1,236,971 DDS lines
and the 0.1 % weight.
