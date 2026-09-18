# Round 1 review — lens: facts

Every mechanism, number and status claim in the eight parts was checked against
the briefs, and where a brief cites code, against the code at that path:line.
Findings are ordered by severity, then by section.

Nothing was found at "must" level in the abstract, section 1 or section 7.

---

## Must

### 1. The local tier holds 96.94 % of the process tree, not of all lines

- File: `sections/02-design-a.tex:49`
- Quote: `On staging the local tier held 96.94~\% of all lines.`
- Problem: 96.94 % is the share of the O2 process-tree family that stays on the
  worker, not the share of all lines; InfoLogger alone is 58.1 % of the corpus
  and every InfoLogger record crosses the wire, so no more than about 42 % of
  all lines can stay local.
- Source: `docs/SOAK_RESULTS.md:3115` ("96.94 % of the process tree stays on the
  node and 3.06 % crosses the network"); `briefs/soak-index.md:23`;
  `briefs/soak-4.md:23`; the corpus shares at `briefs/constraints.md:126`.
  (`briefs/why.md:59` copies the loose wording from
  `docs/TEMPLATES_FIX_PLAN.md:46`; the measurement is the soak line.)
- Fix: "On staging 96.94 % of the process-tree family stayed on its worker."
  The same error is in `sections/04-why.tex:41` ("The local tier holds 96.94~\%
  of lines.") and in the appendix row `sections/07-limits-close.tex:93`
  ("96.94~\% of lines stay local"); `sections/05-eval-a.tex:58` already states
  it correctly and is the wording to copy.
- Severity: must

### 2. The 11.18 core-seconds pipeline figure was retracted

- File: `sections/04-why.tex:50`
- Quote: `The pipeline fell from 19.91 to 11.18 core-seconds per million lines.`
- Problem: 11.18 was withdrawn — it rested on one contaminated stdout reading —
  and the 19.91 control was never re-measured, so the source refuses to quote a
  before-and-after multiple at all.
- Source: `briefs/soak-3.md:108` ("Whole-corpus cost 11.18 core-s/M ... Withdrawn
  at lines 2693-2704; the figure of record is 8.33"); `briefs/soak-3.md:107`
  ("the 19.91 control was not re-measured, so no new multiple is quoted. The
  direction (cheaper) stands"); `briefs/soak-index.md:299`.
- Fix: Drop the pair. Write: "Masking alone fell from 11.93 to 1.82 core-seconds
  per million on InfoLogger, 12.24 to 1.73 on the process tree and 40.22 to 5.09
  on DDS, with every template unchanged" (`briefs/why.md:166`), and quote the
  whole-corpus cost of record as 8.33 core-seconds per million on 45,596,613
  lines (`briefs/soak-3.md:204`) if a single figure is wanted.
- Severity: must

### 3. 31,000 lines is the whole Python tree, not the five duplicated modules

- File: `sections/04-why.tex:71`
- Quote: `Five modules, about 31,000 lines, are byte-identical copies across the services.`
- Problem: the 31,000 lines are all the Python in the deployment tree across 14
  components; the byte-identical duplication is five named modules, whose size is
  not given in any source.
- Source: `briefs/inputs/rework-context.md:61` ("14 roles under `deploy/roles`;
  31,000 lines of Python") and `:67-71` (the five byte-identical modules);
  `briefs/why.md` entry 31.
- Fix: "About 31,000 lines of Python run the services, and five modules are
  byte-identical copies across them."
- Severity: must

### 4. The trend slices end 30, 20 and 10 minutes before the run

- File: `sections/03-design-b.tex:25`
- Quote: `Each compares the entity's share of the fleet in three slices, ending 40, 30 and 20~minutes before the run, against the previous seven days.`
- Problem: the three ten-minute slices span 40 to 30, 30 to 20 and 20 to 10
  minutes before the run, so they end 30, 20 and 10 minutes before it; the
  freshest slice ends ten minutes back, which is the fact that matters (the lane
  never looks at the last ten minutes).
- Source: `deploy/roles/loggy_anomaly_detection/files/monitors/trend-il-volume.json:61-112`
  (slice0 `-20m` to `-10m`, slice1 `-30m` to `-20m`, slice2 `-40m` to `-30m`);
  `briefs/walkthroughs-3-5.md:150` ("The freshest slice ends 10 minutes before the
  run, not at it").
- Fix: "Each compares three ten-minute slices, the freshest ending ten minutes
  before the run, against the previous seven days."
- Severity: must

### 5. Seventeen detectors, each with one model per entity

- File: `sections/03-design-b.tex:5`
- Quote: `17 detectors run, one per entity, and a silent host scores zero.`
- Problem: "one per entity" says there are seventeen entities. A detector is one
  saved definition with a category field, and the plugin builds one model per
  entity inside it; fourteen read logs, three read the health samples.
- Source: `briefs/target-architecture.md:162` (detector windows and category
  fields); `briefs/walkthroughs-6-9.md` walkthrough 6, step 1
  (`il-per-epn.json:48-50`, `category_field origin_host`, one result row per host
  per window); `briefs/why.md` entry 16.
- Fix: "17 detectors run, each holding one model per entity, and a silent host
  scores zero."
- Severity: must

---

## Should

### 6. 384 MB is where the collector is throttled, not where it is capped

- File: `sections/02-design-a.tex:59`
- Quote: `The documented worst case is twice that with a 20~\% margin, roughly 307~MB, so we cap the service at 384~MB.`
- Problem: 384 MB is the soft limit at which the service is throttled; the hard
  cap is 768 MB. The distinction carries the measured 373 MB peak, which would
  otherwise sit 11 MB under a hard wall.
- Source: `briefs/target-architecture.md:154` ("MemoryHigh 384 MB, MemoryMax
  768 MB; documented worst case 128 MB x 2 x 1.2, roughly 307 MB");
  `deploy/roles/loggy_collector/defaults/main.yml:135-136`.
- Fix: "so the service is throttled at 384 MB and killed at 768." Also mark the
  other 384 MB in `sections/06-eval-b.tex:36` as the shifter host's semantic
  model ceiling, a different limit on a different machine
  (`briefs/target-architecture.md:177` warns against conflating them).
- Severity: should

### 7. Only the volume trend rules compare a share of the fleet

- File: `sections/03-design-b.tex:25`
- Quote: `The nine trend monitors read those rows only.`
- Problem: the sentence that follows applies the share comparison to all nine
  rules. Two of the nine are shipping-lag rules: they compare the entity's lag
  against twice a seven-day baseline with a 250 ms floor, and they are
  collector-scoped, not fleet-share rules.
- Source: `briefs/walkthroughs-3-5.md:165` ("The volume rules compare the
  entity's share of the fleet") and `:166` ("The shipping-lag rules are
  collector-scoped"); `briefs/target-architecture.md:165` (shipping-lag threshold
  twice a seven-day baseline, `trend_lag_floor_ms` 250).
- Fix: "The volume rules compare the entity's share of the fleet ... Two
  shipping-lag rules instead compare the entity's own lag against twice its
  seven-day baseline, above a 250 ms floor."
- Severity: should

### 8. One of the four farm collector releases was untested, not all four

- File: `sections/05-eval-a.tex:60`
- Quote: `Four collector releases run there, none tested.`
- Problem: the census found four releases where two were assumed, and it was the
  storage machine's release, two majors behind, that nothing had tested. The same
  round then ran fixture, journal and restart checks on all four and passed 29 of
  29.
- Source: `briefs/soak-4.md:41` ("the storage machine runs a version two majors
  behind that nothing had tested"); `briefs/soak-4.md:42` ("29 passed, 75,243
  journal records, no duplicates ... on each of 3.2.8, 4.0.1, 4.0.14, 5.0.8").
- Fix: "Four collector releases run there, two more than we assumed, and the
  oldest had never been tested against our configuration. It now is."
- Severity: should

### 9. The report never says which host runs the projector, the rollup or the shifter view

- File: `sections/02-design-a.tex:11`
- Quote: `The control host runs Dashboards, the poller, the roster, the catalog maintenance, Alertmanager and the receiver.`
- Problem: the other two storage machines carry named services and the report
  never places them, so a reader cannot tell that the projector and the trend
  rollup are deliberately off the user-interface host, or where the live lane is
  served from. Sections 3.6 and 3.9 then describe both without a host.
- Source: `briefs/target-architecture.md:27` and `:65-66` (projector and trend
  rollup on the second storage node, `deploy/inventory.yml:62-65`,
  `deploy/README.md:78-80`); `briefs/target-architecture.md:75` (shifter view on
  the third storage node); `briefs/walkthroughs-6-9.md:9` (node-04 projector and
  rollup, node-05 shifter view).
- Fix: Add one sentence: "The second storage node runs the projector and the
  trend rollup, away from the user-interface host; the third serves the shifter
  view."
- Severity: should

### 10. The limits list omits that a dead projector silences the Alertmanager-down rule

- File: `sections/07-limits-close.tex:23`
- Quote: `The stale-projector monitor fires only while one projector heartbeat exists in the last 24~hours, so a day of silence mutes it.`
- Problem: the second break-glass rule has a limit of the same kind and it is not
  listed. It fires on the projector reporting Alertmanager down, never on
  absence, so when the projector dies nothing reports Alertmanager at all.
- Source: `briefs/walkthroughs-6-9.md` walkthrough 8 verdict table
  (`alertmanager-down.json:32, 42-47, 59`: "with no heartbeat at all it returns
  false ... a dead projector silences alertmanager-down") and flag 3 in the same
  brief.
- Fix: Add: "The Alertmanager-down monitor reads the projector's own report, so a
  dead projector silences it too."
- Severity: should

### 11. The production storage tier size is not decided

- File: `sections/07-limits-close.tex:35`
- Quote: `Production, the whole farm with three storage nodes, two replicas and the bus, is not real.`
- Problem: the sources disagree and the report picks one silently. The design
  objective says the tier is bigger in production, so more machines can be lost,
  and the architecture brief says no source gives a number.
- Source: `briefs/target-architecture.md:23` ("Production: more storage machines
  than three. No source read gives a number. Not decided in the sources read.")
  and doubt 14 at `:225`; `briefs/constraints.md:221` (deck objective 6: "In
  production the tier is bigger, so more can die"). The opposite reading sits at
  `briefs/constraints.md:306` ("Production has three storage nodes with two
  replicas", `docs/SOAK_RESULTS.md:294-295`), which is the soak rig's comparison
  point, not a decision.
- Fix: "Production, the whole farm with a storage tier of at least three
  machines, two replicas and the bus, is not real. How much larger the tier grows
  is not decided."
- Severity: should

### 12. The farm's three primaries are set in the farm inventory

- File: `sections/02-design-a.tex:15`
- Quote: `The farm value of three primaries is agreed and not applied.`
- Problem: the farm inventory already carries the setting, so "not applied" is
  wrong; what is missing is a run at farm volume, not the setting.
- Source: `deploy/inventory.epn.yml:204` (`log_primary_shards_storage: 3`, live,
  with the comment at `:193-194` "one primary per storage node, which the tier
  can now afford on 8 GB heaps") against
  `deploy/roles/loggy_opensearch/defaults/main.yml:247`
  (`log_primary_shards_storage: 1`). `briefs/why.md` entry 6 states the status as
  "agreed, not applied" but cites only `deploy/README.md:2289-2321`.
- Fix: "Three primaries are set for the farm and have never carried farm volume."
  Apply the same wording to `sections/04-why.tex:41`.
- Severity: should

### 13. The disk warning rule has an upper bound

- File: `sections/03-design-b.tex:5`
- Quote: `Threshold monitors catch cliffs: a storage disk above 92~\% pages, above 85~\% warns.`
- Problem: as written, a disk at 95 % both pages and warns. The warn rule fires
  above 85 and at most 92, so the two rules do not overlap.
- Source: `briefs/walkthroughs-3-5.md:12` and `:121`
  (`disk-cliff-warn.json:79`: `params.max_disk > 85 && params.max_disk <= 92`;
  `disk-cliff-page.json:79`: `> 92`).
- Fix: "a storage disk above 92 % pages, and between 85 and 92 % warns."
- Severity: should

### 14. The 3,011 template count is the whole-corpus figure, and the curve loses its middle point

- File: `sections/06-eval-b.tex:11`
- Quote: `One run per family gave 3,011 templates. One tree per family across three corpora gave 4,221.`
- Problem: 3,011 is the whole-corpus count with the recipe and the fast masker on
  45,596,613 lines, not "one run per family"; the curve's point is that the count
  grows with the runs and partitions sampled, which needs the middle reading of
  4,092 from sixteen runs and eighty partitions.
- Source: `briefs/soak-3.md:59` ("3,011 templates on 45,596,613 lines ...
  Whole-corpus template count with recipe and fast masker ... a floor, not a
  total"); `briefs/soak-3.md:73` (4,092 from 80 partitions and 16 runs);
  `briefs/soak-3.md:81` (4,221 on 55,963,050 lines); `briefs/soak-index.md:22`.
- Fix: "The whole corpus of 45,596,613 lines gave 3,011 templates, a floor.
  Sampling more runs and partitions gave 4,092, and one tree per family carried
  across all three corpora, 55,963,050 lines, gave 4,221. The count is a curve
  driven by the runs sampled, not by the lines."
- Severity: should

---

## Nit

### 15. DDS is 0.1 % of the corpus, not of its own lines

- File: `sections/01-intro-problem.tex:36`
- Quote: `DDS is under-sampled at 0.1~\% of its lines, and operations call it the largest family during data-taking.`
- Problem: "0.1 % of its lines" reads as a share of DDS itself. The figure is
  DDS's share of the corpus.
- Source: `briefs/constraints.md:126` ("Volume shares in that corpus: infologger
  58.1 percent, dpl 41.8 percent ... dds 0.1 percent");
  `briefs/soak-3.md:47`.
- Fix: "DDS is 0.1 % of the corpus, and operations call it the largest family
  during data-taking."
- Severity: nit

### 16. Puppet is "likely" right for the farm

- File: `sections/04-why.tex:65`
- Quote: `Puppet wants an agent and a certificate on every machine, runs on a timer, and is right for the farm.`
- Problem: the source hedges, and the report states it flat. Nothing was measured
  or decided about Puppet on the farm.
- Source: `briefs/constraints.md:196` and `briefs/why.md` entry 9 ("It is likely
  right for the farm and wrong for five machines one person deploys by hand",
  `reports/inputs/deck-text.md:402-406, 427`).
- Fix: "and is likely right for the farm."
- Severity: nit

---

## Checked and sound (no finding)

The claims below were traced and hold, and are listed so a later round does not
re-open them: the twelve steps of the error-line walkthrough and their order
against the audited atlas (`reports/inputs/dataflow-atlas.md:15-20`); the
info-line path and the placement pin (`:26-30`); the dead-collector chain and its
90 to 270 second bound (`briefs/walkthroughs-3-5.md:61-68`); the cover relation
(`deploy/roles/loggy_collector/files/template_contract.py:171-177`); the grade
floors 0.5 and 0.7 and the page severity of the grade monitor
(`ad-high-grade.json:65, 68`); the two Kafka decisions, which stay in two
paragraphs with no shared number, and the Apache-Kafka and primary-path
conditions (`briefs/why.md` entry 11); the buffer hours 17.4 and 5.1 as the
InfoLogger output's cover (`briefs/soak-index.md:131`); the overload figures
82 %, 1.8 % and 263 seconds (`briefs/soak-2.md:41`); the flush table, the noise
floors, the threading arms, the heap and core-placement non-results; the six log
sources and the three added in round 7 (`docs/SOAK_RESULTS.md:3014-3021`); the
templating and semantic figures of record; the 32 instrument defects and the 99
total (`briefs/soak-index.md:323`, rig instrument group of 32); and the rule
checks — no role, playbook or file name in the body, version numbers in two
places only (the collector release that loses bytes at rotation and the OpenSSL
wall), no supervision amounts, no August dates, no merge requests.
