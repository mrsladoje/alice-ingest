# Semantic retrieval plan for ALICE templates

**Status:** approved design, independently audited and corrected, 4 September
2026.

**Scope:** template retrieval, embeddings, lexical retrieval, late interaction,
fusion, domain adaptation, and production validation.

**Results file:** write every completed stage to `docs/SEMANTIC_RESULTS.md`.
Write results during execution, not after all stages finish.

## Conclusion

ALICE must select a retrieval system, not select an embedding from a public
leaderboard.

The work starts with complete production source coverage. It then builds a
human-audited benchmark before comparing retrieval systems.

The final comparison includes maintained OpenSearch baselines, the pinned Sweet
reference, learned sparse retrieval, dense models, late interaction, hybrids,
rerankers, and ALICE-trained static models.

The held-out result runs once after every production-form implementation passes
development equivalence and becomes frozen.

## What this plan supersedes

This plan supersedes the model recommendation in these earlier sections:

- `docs/SOAK_PLAN.md`, **Round 3 — embeddings, cheapest first**.
- `docs/SOAK_PLAN.md`, **Stage I — post-training the small potion model**.
- `docs/EMBEDDING_RESULTS.md`, **The pick: potion-base-32M**.

Those results remain valid historical measurements. They do not select the new
retrieval system.

The following findings remain binding:

- Embedding happens once per new template, not once per log line.
- Embedding throughput is not the main quality constraint.
- Source purity is a useful diagnostic.
- Source purity is not a user retrieval metric.
- Parser changes can alter the template set more than a model change.
- Absolute processor rates from the laptop do not transfer to EPN hardware.

The current judged query set becomes development data. It can never become
held-out data because existing models already used it.

## Goal

The primary goal is accurate operator search over ALICE log templates.

The primary retrieval task is natural-language operator retrieval:

> Given an operator question, return the templates that best explain or support
> that question.

The secondary retrieval task is template-to-template retrieval:

> Given a template, return meaningfully similar templates from other sources,
> programs, nodes, or periods.

Exact identifier lookup is the third task.

The three tasks have separate leaderboards and separate acceptance rules.

The headline score uses natural-language operator retrieval only.

Do not average exact identifier or template-similarity queries into that score.

The benchmark must also define its retrieval unit.

The primary view returns one canonical template with aggregated source
metadata.

A secondary diagnostic view can return source-specific template instances.

Primary metrics collapse semantic duplicate groups before scoring.

## Non-goals

This plan does not reopen the chosen log-mining algorithm.

This plan does not optimize per-line embedding.

This plan does not train a query router from the current small query set.

This plan does not copy code-specific Sweet Search rules into ALICE without an
ablation.

This plan does not deploy a model because it leads BEIR or MTEB.

This plan does not tune any setting against held-out judgments.

## Binding rules

1. Production source coverage comes before final model comparison.
2. Human relevance judgments select retrieval quality.
3. Every model uses its documented prompts and pooling.
4. Every system returns a stable, fully ordered ranking.
5. Development and held-out queries split by incident or intent.
6. Training data never contains held-out queries or judgments.
7. Every reranker reports candidate Recall at K.
8. Every custom method competes against a maintained upstream method.
9. Every finalist has a deployable form before held-out evaluation.
10. License and security failures remove a production candidate.
11. The held-out evaluation runs once.
12. No source may cause duplicate cross-worker collection.
13. Natural-language, identifier, and similarity metrics remain separate.
14. Raw-log retention and template-catalog retention remain separate decisions.

## Required deliverables

The project is complete only when it has these artifacts:

- A versioned source inventory.
- A frozen template corpus and corpus manifest.
- A written relevance rubric.
- A retrieval-unit and duplicate-collapse contract.
- A frozen product performance contract.
- A development query set with graded judgments.
- A sealed held-out query set.
- A benchmark harness with tested model adapters.
- Reproducible run files for every promoted system.
- A pinned Sweet Search reference run.
- An ALICE static-model experiment report.
- Development parity for every production-form finalist.
- One frozen production recommendation.
- One maintained fallback recommendation.
- One treatment-and-control production validation.

## Execution order

The stages run in this order:

1. Stage S0: complete source coverage.
2. Stage S1: define and audit the retrieval benchmark.
3. Stage S2: repair the harness and build development relevance judgments.
4. Stage S3: select input representations.
5. Stage S4: establish lexical and learned sparse baselines.
6. Stage S5: screen dense models.
7. Stage S6: screen late-interaction models.
8. Stage S7: compare hybrids, reranking, and fusion.
9. Stage S8: train ALICE static models.
10. Stage S9: build and freeze production-form finalists.
11. Stage S10: run the frozen held-out evaluation.
12. Stage S11: run live treatment-and-control validation.

No final quality comparison can cross an incomplete earlier gate.

## Stage S0 — complete production source coverage

### Question

Does the evaluation corpus represent every useful production log format and
program identity?

### Why this stage comes first

The current corpus contains three collector paths: InfoLogger, DDS, and merged
stdout.

Merged stdout hides 13 program identities. Missing sources also contain unique
formats, vocabulary, failures, and severity conventions.

The October 2022 survey recorded only Muon Forward Tracker runs.

It cannot prove current production completeness.

The exact live job-log path also remains unresolved.

A model chosen on the current corpus can specialize in the wrong distribution.

The target is representative source coverage. Ten thousand templates is a
plausible result, but it is not an acceptance gate.

### Current production census

Inspect representative live nodes and active runs before changing collection.

Include Muon Forward Tracker and other detector paths.

Include the Inner Tracking System when it is active.

Compare live observations with deployment configuration, systemd units,
runbooks, the 2022 survey, and source-owner knowledge.

The census must record:

- Observation date and source owner.
- Detector and run type.
- Active processes and systemd units.
- Every observed log path and format.
- Program identity and file ownership.
- Writer and reader multiplicity.
- Rotation and restart behavior.
- Expected and observed volume.
- Unknown paths and programs.

Resolve the live job-log path during this census.

Version the source registry by observation date.

The responsible source owner must approve each inclusion or exclusion.

Define a catch-all threshold before replay.

Start a new census after a deployment changes sources or the catch-all exceeds
that threshold.

### Source work

Use `docs/LOG_TYPES.md` as the source integration runbook.

Treat the routing bullets as starting hypotheses.

Measured replay and source-owner review decide the final routes.

#### InfoLogger

- Keep the existing structured input.
- Keep InfoLogger durable for all severities.
- Preserve its named fields in the template document.
- Confirm that missing timestamps cannot cross a replay boundary.

#### DDS

- Keep the existing DDS source.
- Add useful slot, channel, and task extractors.
- Preserve the complete source path and program identity.
- Route informational events locally by default.
- Route warnings and worse to durable storage.

#### DPL and FairMQ program logs

- Stop merging all program files into one stdout file.
- Preserve the program basename before Fluent Bit reads the line.
- Add named parsers for the measured program formats.
- Keep a catch-all input for unknown future programs.
- Count files that only the catch-all input claims.
- Route by parsed severity after parser validation.

#### DataDistribution and `TfBuilderTask`

- Add the full-date, millisecond parser.
- Map the one-letter severity values explicitly.
- Preserve FairMQ state fields when they are stable.
- Test every severity value with a fixed fixture.

#### ANSI ErrorMonitor output

- Use an ANSI-tolerant parser or a deterministic strip step.
- Do not store terminal color sequences as semantic text.
- Test colored and uncolored forms of the same event.
- Require identical normalized templates for both forms.

#### journald and kernel faults

- Enable only selected systemd units and kernel priorities.
- Add multiline reconstruction for kernel traces.
- Preserve `Comm` as the process join field.
- Keep kernel warnings and failures in durable storage.
- Measure informational journald volume before enabling local retention.

#### `o2-infologger-daemon.log`

- Add one explicit input for this file.
- Extract the connected-client count and configured limit.
- Keep saturation events in durable storage.
- Do not wildcard `/var/log`.

#### Shared `/scratch` logs

- Read only the current node's directory.
- Never tail every shared file from every worker.
- Use one ownership rule for every physical file.
- Poll carefully because NFS does not provide local inotify behavior.

#### Explicit exclusions

- Do not tail `/var/log/messages` because journald already contains it.
- Keep security and audit logs outside this project.
- Do not ingest shared scratch files without an ownership proof.

### Production collector integration

Use `tools/soak/mkconfig.py` or its production renderer.

Do not validate a hand-written Fluent Bit configuration.

Replay fixtures for every accepted source through the rendered
`collector.yaml.j2` configuration.

Each replay must verify:

- Multiline reconstruction.
- Parser selection and fallbacks.
- Source and program identity.
- Conditional timestamp and severity extraction.
- Local and durable raw-log routes.
- Template-catalog delivery.
- OpenSearch field mappings.
- Input and output record counts.
- Duplicate prevention.
- Rotation, cursor, and restart behavior.

Keep raw-log routing separate from template-catalog routing.

The shared template catalog must retain unique canonical templates when raw
informational logs remain local.

Send bounded catalog updates and aggregate counts without forwarding those raw
informational lines.

### Routing decision record

Every source must have a written routing record with these fields:

- Source name.
- Program identity rule.
- Timestamp rule.
- Clock domain.
- Severity extraction rule.
- Informational retention location.
- Warning retention location.
- Error retention location.
- Multiline rule.
- Duplicate-ownership rule.
- Expected daily volume.
- Durable-storage reason.

Severity remains the default routing dimension.

Source-specific durability can override severity when operational evidence
requires it.

Base each routing decision on measured volume, severity reliability, durability,
operator value, and duplication risk.

### Template corpus requirements

The corpus must include representative samples from every accepted source.

The corpus manifest must record:

- Corpus identifier.
- Creation time.
- Git revision.
- Parser revision.
- Mining recipe revision.
- Source files and hashes.
- Source time windows.
- Clock domains.
- Lines per source.
- Templates per source.
- Program counts.
- Unknown-program counts.
- Duplicate groups.
- Contentless-template counts.
- Masking rules.
- Truncation rules.

Each semantic template group needs one stable canonical template identifier.

Each source occurrence needs a separate stable source-instance identifier.

Both identifiers must survive model and representation changes.

Group only templates that express the same normalized event meaning.

Do not group templates that are only related.

Freeze deterministic normalization rules and every reviewed alias.

Keep source, parser, program, frequency, and example metadata on each instance.

Aggregate that metadata onto the canonical result without creating extra ranks.

### Stage S0 gate

Stage S0 passes only when all conditions hold:

- Every unique useful format has a parser or explicit exclusion.
- A dated live census covers representative nodes and active runs.
- The responsible source owner approved the source registry.
- The exact live job-log path is known or explicitly excluded.
- Every merged program keeps its original identity.
- Every accepted source has representative replay data.
- The production renderer generated every replay configuration.
- Replay covers parsing, routing, mappings, counts, duplication, and restart.
- Classifiable envelope fields parse on at least 99 percent of sampled lines.
- A person reviews the remaining unmatched lines.
- Every high-value pattern has a fixed parser test.
- Every source has a routing decision record.
- Shared files cannot be collected more than once.
- Replay and live clocks remain distinguishable.
- Catch-all counts remain below the frozen threshold.
- The frozen corpus manifest exists.

Semantic extractor coverage does not need to reach 99 percent.

For each applicable field, use all sampled lines whose format defines that
field as the denominator.

Source and message are required when the source supplies them.

Program is required when the source supplies a program identity.

Timestamp and severity coverage apply only when the source format contains
those fields.

Every missing field must use `absent`, `unknown`, or `parse_failed`.

## Stage S1 — define and audit the retrieval benchmark

### Question

What does a good ALICE search result mean to an operator?

### First action

Audit the existing 20 queries and 635 query-template pairs.

The existing labels came from an AI judge. They are not final ground truth.

The audit owner reads each query and its pooled candidates without model names.

The audit produces examples for every relevance grade.

### Relevance grades

Use four relevance grades:

- `3`: directly answers the operator need.
- `2`: provides strong supporting evidence.
- `1`: is related but not useful alone.
- `0`: is irrelevant or misleading.

If a result depends on missing context, mark that fact in an assessor note.

Do not silently convert uncertainty into relevance.

Use gains `0`, `1`, `3`, and `7` for grades zero through three.

Grades two and three count as relevant for binary metrics.

Grade one does not count as relevant for MRR, Recall, or Success.

Exact-identifier queries use Success at 1 and Recall at 10.

Each intent needs a narrative, inclusion criteria, exclusion criteria, and
known positive examples.

The assessor sees the template, stable metadata, redacted raw examples, and
frequency.

The assessor must not see the system identity or retrieval score.

### Query classes

The benchmark must cover these classes:

- Symptom questions.
- Component or subsystem failures.
- Resource pressure.
- Network and transport failures.
- Storage failures.
- Configuration failures.
- Process crashes and state transitions.
- Detector-specific problems.
- Cross-source semantic-association questions.
- Exact identifiers and error codes.
- Natural-language paraphrases of log language.
- Ambiguous short queries.
- Negative or exclusion queries.
- Similar-template retrieval.

Each query records its class, source, provenance, and expected answer type.

Template retrieval can find related templates from different sources.

It cannot establish temporal or event causality.

Temporal correlation analysis remains outside this plan.

### Task definitions

Natural-language retrieval uses an operator question as the query.

Template-similarity retrieval uses one canonical template as the query.

Exclude the query group and all its source instances from its result list.

Exact-identifier retrieval uses a literal identifier or error code.

Maintain a separate query schema, run file, metric set, and leaderboard for
each task.

Average paraphrases within an intent first.

Then macro-average the independent intent values.

A query without any useful corpus result becomes a corpus-coverage case.

Do not include that query in retrieval-effectiveness metrics.

### Query provenance

Prefer real operator questions from these sources:

- Shifter search history, when available.
- Incident notes.
- Runbooks.
- Tickets.
- Post-incident reviews.
- Questions written by ALICE operators.

Synthetic queries can fill a missing class.

Every synthetic query must carry a synthetic label.

Freeze the target query-class mixture from observed operator demand when
history is available.

Otherwise, label the benchmark as coverage-balanced.

Do not claim that a coverage-balanced benchmark represents production query
frequency.

### Development and held-out construction

The existing 20 queries become calibration queries.

Create at least 80 additional development intent groups.

Keep every paraphrase of one intent inside one group.

Keep every query from one incident inside one group.

Use calibration variance to estimate the held-out query count.

The held-out set must contain at least 60 independent intent groups.

Freeze the minimum practically important difference before screening models.

Use a two-sided significance level of 0.05 and 80 percent target power.

If the available query count cannot reach that power, record the detectable
difference and limit the conclusion.

Recalculate the required count after Stage S2 creates complete development
query-relevance judgments, or qrels.

Freeze the final count before Stage S3 model screening.

An independent benchmark custodian keeps held-out query text access-controlled.

Record the first held-out access time.

The held-out set remains invisible until Stage S10.

### Product performance contract

Freeze the serving contract before model screening.

The contract must define:

- Serving hardware and placement.
- Local-only operation and external-service restrictions.
- Expected concurrency and queries per second.
- Warm and cold latency targets.
- Required 95th and 99th percentile latency.
- Template freshness and update delay.
- Incremental update throughput.
- Total model and index storage budgets.
- Memory budget.
- Required failure fallback.

The product owner must approve these requirements.

Do not invent fixed latency limits during model comparison.

### Frozen judgment-pool design

Stage S2 will build development judgments from diverse pooled systems.

The first shallow pool must include:

- Plain BM25.
- BM25F.
- The pinned Sweet Search reference.
- Learned sparse retrieval.
- At least three dense model families.
- At least two late-interaction model families.
- At least two hybrid systems.
- Exact identifier retrieval.

Take the top 20 results from each system for every query.

Use this shallow pool for normalized Discounted Cumulative Gain at rank 10 and
Mean Reciprocal Rank at rank 10.

Create a separate deep pool for candidate-recall evaluation.

Use at least 20 stratified natural-language intents in the deep subset.

Increase that count when development variance cannot detect the practical
difference.

Take the top 200 results from each first-stage system for those intents.

Report Recall at 100 and Candidate Recall at 200 only on this deep subset.

Label every such result as pooled Recall.

Deduplicate candidates by canonical template group.

Randomize candidate order before human review.

Hide system names and scores during review.

Every promoted development system adds unjudged top-20 results to a residual
pool.

Every promoted first-stage system also adds unjudged deep top-200 results.

Judge that pool blindly before comparing the new system.

Recompute all comparison runs after each qrels revision.

Measure leave-one-system-out pool sensitivity for each promoted model family.

Never change held-out judgments after viewing held-out scores.

### Judgment quality

One primary ALICE operator can own the final decision.

A second operator should judge a stratified sample of at least 10 percent.

If no second operator is available, record that limitation.

Freeze the minimum acceptable agreement before this second review.

Report raw agreement and weighted Cohen's kappa.

Record disagreements and update the rubric before model screening.

Do not force agreement without recording the reason.

Pause screening when measured agreement misses the frozen threshold.

### Stage S1 gate

Stage S1 passes only when all conditions hold:

- The existing query pool has a complete human audit.
- The relevance rubric includes positive and negative examples.
- The assessor agreement method and threshold are frozen.
- Grade gains and the binary relevance threshold are frozen.
- The development query intents cover every required class.
- The three retrieval tasks have separate schemas and leaderboards.
- Query groups prevent paraphrase and incident leakage.
- The minimum practically important difference is frozen.
- The initial power calculation or its limitation is recorded.
- The held-out construction method is frozen.
- Shallow and deep pooling specifications are frozen.
- The product performance contract has owner approval.
- System names remain hidden during judging.

## Stage S2 — repair the harness and build development qrels

### Question

Can the harness reproduce each system and create unbiased development qrels?

### Model adapters

Replace the single optional query prefix with explicit adapters.

Every dense adapter must implement:

- `encode_query`.
- `encode_document`.
- Query prompt or prefix.
- Document prompt or prefix.
- Tokenizer revision.
- Maximum input length.
- Padding direction.
- Pooling method.
- Normalization method.
- Output dimensions.
- Numeric type.
- Model revision.

The adapter must declare its encoding mode for each retrieval task.

Natural-language retrieval uses documented query and document modes.

Template-similarity retrieval compares documented symmetric mode with
query-document and document-document modes.

Freeze the template-similarity mode on development data.

Every late-interaction adapter must also record:

- Token-vector dimensions.
- Query token limit.
- Document token limit.
- Special-token handling.
- Maximum-similarity token aggregation, or MaxSim, implementation.
- Quantization settings.

Every adapter needs one golden query and document fixture.

The fixture must match the official implementation within a declared numerical
tolerance.

### Pooling requirements

Pooling is part of the model definition.

Embedding pooling converts a sequence of token vectors into one fixed-size
vector.

Mean pooling averages valid token vectors.

CLS pooling selects the model's classification-token vector.

Last-valid-token pooling selects the final non-padding token vector.

Static pooling averages learned lookup vectors for the input tokens.

Late interaction does not create one sentence vector.

It keeps token vectors and combines token matches with MaxSim.

MaxSim selects each query token's highest document-token score and sums those
scores.

The harness must support and record:

- Mean pooling.
- CLS pooling.
- Last-valid-token pooling.
- Static token-vector averaging.
- No sentence pooling for late interaction.

Padding tokens must never enter mean pooling.

Last-token pooling must select the final real token.

Normalization remains separate from pooling.

### Ranking requirements

Every system must rank at least the largest evaluation or pooling depth.

The exact-search oracle can rank the full corpus.

Use descending score order.

Use the stable canonical template identifier as the final tie-break.

Do not use unordered `argpartition` output as a ranked list.

The harness must preserve raw scores before fusion.

Primary metrics collapse canonical duplicate groups before rank assignment.

Repeated source instances from one group cannot occupy multiple primary ranks.

Source-instance rankings remain available as diagnostics.

### Judgment requirements

The harness must accept grades zero through three.

It must distinguish unjudged results from irrelevant results.

It must report judged coverage at every evaluated cutoff.

Development comparisons require at least 95 percent judged coverage at rank 10.

Held-out comparisons require 100 percent judged coverage at rank 10.

### Metrics

The primary metric is normalized Discounted Cumulative Gain at rank 10.

Secondary relevance metrics are:

- Mean Reciprocal Rank at 10.
- Pooled Recall at 20.
- Success at 5.
- Judged at 10.
- Judged at 20.

The deep natural-language subset also reports:

- Pooled Recall at 100.
- Pooled Candidate Recall at 100.
- Pooled Candidate Recall at 200.

Exact-identifier retrieval reports:

- Success at 1.
- Recall at 10.

Template-similarity retrieval reports its relevance metrics separately.

Reranking systems also report:

- Candidate Recall at 20.
- Candidate Recall at 50.
- Reranker gain against its candidate order.

Only the deep subset can add pooled Candidate Recall at 100 and 200.

Every Recall value uses the frozen pool's known relevant set.

Do not describe pooled Recall as exhaustive corpus Recall.

Diagnostic metrics are:

- Corpus answerability rate.
- Macro source purity.
- Cross-source neighbor rate.
- Duplicate-template rate.
- Result diversity by program.
- Source-instance coverage.

Diagnostic metrics cannot select the winner alone.

### Statistical method

Calculate one metric value per query.

Average paraphrases within each intent.

Use one intent value in the final paired comparison.

Compare systems with paired query differences.

Bootstrap complete intent or incident groups, not individual templates.

Report the point difference and 95 percent confidence interval.

Correct exploratory multiple comparisons with the Holm method.

Use the frozen minimum practically important difference for promotion decisions.

### Run manifest

Every run records:

- Run identifier.
- Corpus identifier.
- Query-set identifier.
- Judgment-set identifier.
- Code revision.
- Dependency lock hash.
- Model identifier and revision.
- Adapter configuration.
- Representation identifier.
- Search parameters.
- Fusion parameters.
- OpenSearch version.
- Index universally unique identifier and schema revision.
- Searched indices and aliases.
- Primary shard and replica counts.
- Per-clause candidate depth.
- Refresh state.
- Search pipeline revision.
- Hardware.
- Warm or cold state.
- Random seed.
- Start and finish times.

### Performance measurements

Record these measurements separately from relevance:

- Document encoding time.
- Query encoding latency.
- Retrieval-engine latency.
- End-to-end latency at the 50th percentile.
- End-to-end latency at the 95th percentile.
- End-to-end latency at the 99th percentile.
- Peak resident memory.
- Index bytes per template.
- Total index bytes.
- Index construction time.

Measure warm and cold query behavior separately.

Measure the frozen concurrency and query rate.

Repeat the load test while template updates are active.

Compare every measurement with the frozen product contract.

### Harness tests

The harness must include fixed tests for:

- Graded metric calculations.
- Unjudged-document handling.
- Stable tie order.
- Query and document prompts.
- Each pooling method.
- Left and right padding.
- MaxSim scoring.
- Fusion calculations.
- Candidate Recall at K.
- Canonical duplicate collapse.
- Separate task metrics.
- Manifest completeness.

### Development pool execution

Freeze the development intents before retrieval tuning.

Run every system family in the Stage S1 pool design.

Use published defaults or recorded untuned settings for initial pooling.

Build only the minimum adapters needed to create diverse pool candidates.

Stages S3 through S8 perform tuning, ablation, and promotion.

An initial pooling run cannot promote a system.

Collect top 20 results for every query.

Collect top 200 results for the frozen deep natural-language subset.

Collapse canonical duplicates before human review.

Randomize the pool and hide system identity and score.

Complete the shallow and deep human judgments.

Then recalculate held-out power and freeze its final intent count.

Do not start Stage S3 model screening before these qrels exist.

### Stage S2 gate

Stage S2 passes only when all conditions hold:

- Every initial pooling model has a verified adapter.
- Metric fixtures match a trusted reference implementation.
- Ranked outputs are stable across repeated runs.
- Unjudged results remain distinct from irrelevant results.
- Initial shallow and deep development qrels are complete.
- The final held-out intent count is frozen.
- Paired query bootstrap works on grouped queries.
- Canonical duplicates cannot inflate primary metrics.
- A complete run manifest accompanies every result.

## Stage S3 — select input representations

### Question

What information should each retrieval family receive?

Representation means the input text and fields. It does not mean the embedding
model.

### Stable semantic fields

Candidate stable fields include:

- Normalized template.
- Log source.
- Program or facility.
- Detector or subsystem.
- Severity class.
- Stable error-code class.
- Stable operation name.

Do not include volatile fields by default:

- Timestamp.
- Hostname.
- Process identifier.
- Run number.
- Internet Protocol address.
- Raw numeric values.
- Random path segments.

Exact-identifier fields can retain selected values separately.

### Representation arms

#### R0 — template only

Encode or search only the normalized template.

This is the minimal control.

#### R1 — identity plus template

Prepend source and program identity to the template.

Example:

```text
source=dpl program=gpu-reconstruction
failed to allocate <*> bytes for device buffer
```

#### R2 — stable semantic preamble

Prepend all approved stable semantic fields.

Example:

```text
source=dpl program=gpu-reconstruction severity=error detector=tpc
failed to allocate <*> bytes for device buffer
```

#### R3 — structured fields

Keep template, program, detector, severity, and identifiers in separate search
fields.

Use this representation for BM25F and metadata filters.

#### R4 — dual lexical and semantic representation

Use structured fields for lexical retrieval.

Use the best development representation for dense or late retrieval.

Do not duplicate boosted metadata in both paths without an ablation.

### Representation screen

Use four probe systems:

- OpenSearch BM25F.
- DenseOn.
- LateOn.
- `potion-retrieval-32M`.

Compare R0, R1, and R2 on development queries.

Compare R3 and R4 for lexical and hybrid systems.

Inspect regressions on exact identifiers and ambiguous short queries.

This probe removes clearly weak representations.

It does not prove that one representation fits every later model.

Every promoted model must test all surviving compatible representations.

Every final system identifier must include model, representation, and task
encoding mode.

### Stage S3 gate

Freeze one representation per promoted model and retrieval task.

The selected representation must improve development relevance or simplify the
system without a material regression.

Record every excluded field and its exclusion reason.

## Stage S4 — establish lexical and learned sparse baselines

### Question

How much quality can maintained lexical and learned sparse retrieval provide?

### L0 — plain BM25

Use OpenSearch BM25 over normalized template text.

This is the mandatory lexical control.

### L1 — maintained BM25F

Use OpenSearch `combined_fields` over approved structured fields.

Tune field weights only on development queries.

Keep one exact keyword subfield for identifiers.

### L2 — pinned Sweet Search reference

Pin Sweet Search commit
`8bbbc14b9176ceb192c591b925c41b6a3198b482`.

Use `ss-search` as the reference interface.

Invoke its lexical scoring logic through a thin ALICE data adapter.

Do not modify the pinned reference for benchmark convenience.

Record its BM25F, exact-identifier, rescue, and score-calibration components.

Do not describe this complete reference as plain BM25.

### L3 — reduced ALICE Sweet Search port

Implement only portable, independently removable features:

- Exact identifier anchoring.
- Field-specific weights.
- Per-content-term rescue.
- Optional trigram fallback.
- Stable score calibration.

Do not copy Sweet Search's code-file rules.

Do not add graph expansion during this stage.

Do not train a query router during this stage.

Build differential fixtures between L2 and each intended L3 feature.

Any deliberate score difference needs a written ALICE-specific reason.

### SP0 — maintained learned sparse control

Use the official OpenSearch document-only neural sparse model:

- `amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte`.
- `amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1`.

Use the current production OpenSearch version and supported APIs.

The repository pins OpenSearch 3.7.0 on 4 September 2026.

Reconfirm that version before every benchmark series.

Record model deployment, ingestion, query, storage, and update costs.

### Analyzer screen

Compare the standard analyzer with one identifier-preserving analyzer.

Test camelCase, underscores, hexadecimal identifiers, paths, and punctuation.

Keep exact terms in a keyword field.

Do not make fuzzy matching the default path.

### Stage S4 gate

Keep L0 and L1 as permanent controls.

Keep L2 as the exact custom-method reference.

Promote L3 only if it exceeds the frozen practical difference.

L3 must not materially reduce exact-identifier Success at 1 or Recall at 10.

Keep SP0 as the maintained learned sparse control.

A custom feature that adds no measured value is removed.

## Stage S5 — screen dense models

### Question

Which single-vector model retrieves the best ALICE templates?

The public candidate inventory was checked on 4 September 2026.

Refresh official model cards before Stage S5 execution.

Record additions in a plan revision.

Do not silently replace a model revision.

### Continuity controls

Run these controls:

- `all-MiniLM-L6-v2`.
- `potion-retrieval-32M`.
- `potion-base-32M`, the best historical POTION result.

These controls connect the new benchmark to earlier evidence.

### Core dense candidates

Run these models with exact exhaustive cosine search:

- `lightonai/DenseOn`.
- `lightonai/mDenseOn`.
- `perplexity-ai/pplx-embed-v1-0.6b`.
- `Qwen/Qwen3-Embedding-0.6B`.

Use the exact query and document instructions from each model card.

Use the exact pooling method from each model card.

### Instruction and quantization screen

Use the model-authored retrieval instruction as the control.

For models that allow custom query instructions, test one frozen ALICE
operator-search instruction.

Do not apply query instructions to documents.

Select the instruction only on development data.

Run exact floating-point embeddings before any quantized representation.

For Perplexity, compare documented integer and binary outputs with that
floating-point reference.

Record normalization, similarity, storage, latency, and relevance changes.

### Research-only dense candidates

Run `Qwen/Qwen3-Embedding-8B` as the large dense quality ceiling.

Run `jinaai/jina-embeddings-v5-text-small-retrieval` as a license-limited
research candidate.

Jina cannot become a production candidate until its license receives approval.

No license-limited model can be the only quality ceiling.

### Optional diversity candidate

Run `nomic-ai/nomic-embed-text-v2-moe` only if multilingual or mixed-token
queries expose a gap.

It is not part of the mandatory core screen.

### Dimension screen

Only models with documented dimensional reduction enter this screen.

Test 256 dimensions, 512 dimensions, and the native dimension.

Stop smaller dimensions after a material development-quality loss.

### Dense measurements

Record relevance metrics and:

- Query encoding latency.
- Document encoding throughput.
- Vector bytes per template.
- Exact-search latency.
- OpenSearch index latency when supported.
- Peak model memory.
- License.
- Remote-code requirement.

### Stage S5 gate

Promote at most two production-eligible dense models.

Also keep one efficient control.

Keep a research-only ceiling separate from production recommendations.

Use the development Pareto frontier for quality, latency, and memory.

## Stage S6 — screen late-interaction models

### Question

Does token-level matching improve ALICE retrieval enough to justify its cost?

### Core late-interaction candidates

Run these candidates:

- `lightonai/LateOn`.
- `lightonai/mLateOn`.
- `lightonai/LateOn-Code-edge`.
- `lightonai/LateOn-Code`.

The code variants test technical-token transfer. They do not receive a quality
assumption.

### Cross-encoder quality ceiling

Rerank candidates with `Qwen/Qwen3-Reranker-0.6B`.

This arm measures the value of a stronger pairwise reranker.

It is not a first-stage retriever.

Compare its gain and cost with late-interaction reranking.

### Exact quality reference

Run exhaustive MaxSim against every template first.

This run is the quality reference for each late-interaction model.

Do not introduce approximate indexing before this result exists.

At the expected corpus size, exhaustive MaxSim can be practical.

Measure it instead of assuming it is too expensive.

### Candidate-generation screen

Rerank these candidate sets:

- Best dense top 20 and 50 for all development intents.
- BM25F top 20 and 50 for all development intents.
- Best dense top 100 and 200 for the deep subset.
- BM25F top 100 and 200 for the deep subset.
- Union of BM25F and dense candidates.
- Reciprocal Rank Fusion candidates.
- Quantile-fusion candidates.

Report candidate Recall at 20 and 50 for all judged intents.

Report pooled Candidate Recall at 100 and 200 only for the deep subset.

Do not blame the reranker for a missing relevant candidate.

### Approximate multi-vector search

Use exact MaxSim as the ranking oracle.

Test model export and index compatibility before approximate comparison.

Verify exported scores against native model scores within a frozen tolerance.

Then compare FastPlaid or NextPlaid against that oracle.

Do not assume that either engine supports every late-interaction model.

NextPlaid is the incremental production candidate.

FastPlaid is an offline batch-indexing candidate.

Record ranking agreement, relevance loss, latency, and index bytes.

Quantization must report its quality loss against exact float MaxSim.

OpenSearch late-interaction reranking is a separate compatibility arm.

Reject it when local-only deployment needs an unsupported external inference
service.

### Stage S6 gate

Promote the best production-eligible late-interaction model.

Promote one edge model only if it offers a meaningful efficiency option.

Retain the cross-encoder only as a quality ceiling unless it meets the product
contract.

Reject approximate configurations that lose the frozen practical difference.

## Stage S7 — compare hybrids, reranking, and fusion

### Question

Which complete retrieval system works best for ALICE operators?

### Required systems

#### H0 — best maintained lexical system

Use BM25F with the frozen analyzer and field weights.

#### H1 — maintained learned sparse system

Use SP0 with its frozen model and OpenSearch configuration.

#### H2 — best dense system

Use exact dense retrieval with the frozen representation.

#### H3 — pure late interaction

Use exhaustive MaxSim when it meets the latency target.

Otherwise, use the best validated approximate configuration.

#### H4 — maintained Reciprocal Rank Fusion

Use maintained OpenSearch behavior for these pairwise fusions:

- BM25F with dense retrieval.
- BM25F with learned sparse retrieval.
- Learned sparse with dense retrieval.

Run the upstream rank constant of 60 as the fixed control.

A second variant can tune rank constants and weights on development data.

#### H5 — pinned Sweet Search reference system

Run the exact pinned Sweet Search scoring and fusion logic.

Keep its component scores and retrieval contributions.

#### H6 — reduced ALICE quantile fusion

Normalize each score distribution by query.

Fuse the calibrated scores with development-only weights.

This is the reduced ALICE Sweet Search fusion challenger.

#### H7 — dense retrieval with late reranking

Use the best dense model as candidate generator.

Sweep the frozen candidate depths from Stage S6.

#### H8 — lexical-dense union with late reranking

Union candidates from BM25F and dense retrieval.

Rerank the deduplicated union with the best late model.

#### H9 — three-way fusion

Fuse lexical, dense, and late-interaction evidence.

Compare Reciprocal Rank Fusion and quantile score fusion.

#### H10 — cross-encoder reranking ceiling

Rerank the best frozen candidate union with the Qwen3 reranker.

Use this result to measure remaining reranking headroom.

### Sweet Search features deferred from the core comparison

Keep these features outside the first complete-system screen:

- Learned query routing.
- Graph expansion.
- Maximal Marginal Relevance diversification.
- Query-specific file-kind rules.

Add diversification only when the product needs distinct failure families.

Evaluate diversity separately from relevance when it is added.

Add a learned router only after the query count supports reliable validation.

### Fusion requirements

Record every component run before fusion.

Fusion code must consume immutable run files.

Keep raw scores, normalized scores, and final ranks.

Use stable tie-breaks after fusion.

OpenSearch Reciprocal Rank Fusion depends on shard topology and per-shard
candidate depth.

Tune and test it with the frozen production shard topology.

Record rank constants, weights, shard counts, and per-clause candidate depths.

### Stage S7 gate

Promote the best complete system on development data.

Also promote the maintained BM25F baseline.

Compare the pinned Sweet Search reference directly with its reduced ALICE port.

Promote a custom fusion only when its gain exceeds the practical difference.

If fusion gives no material gain, deploy the simpler single system.

## Stage S8 — train ALICE static models

### Question

Can a domain-adapted lookup-table model approach the best retrieval quality at
much lower serving cost?

### Why this stage follows the retrieval screens

The dense screen identifies useful teacher candidates.

The representation screen defines the text that the student must encode.

The development benchmark prevents source-purity optimization from selecting a
bad retriever.

### Terminology

POTION models use Model2Vec static token embeddings.

Inference averages token vectors into one document vector.

Tokenlearn creates a new Model2Vec model from a SentenceTransformer teacher and
sampled passages.

It learns static token features from teacher means and applies required
Principal Component Analysis.

It does not promise in-place continuation of a stock POTION model.

Call each method distillation, Tokenlearn training, or custom supervised
training.

Do not use the vague term `post-training`.

### Training corpus

Build training text from:

- Deduplicated normalized templates.
- Frequency-capped raw messages.
- Rare high-severity examples.
- Stable metadata representations.
- Training-source programs only.

Do not process 56 million repeated messages with equal weight.

Record every sampling cap and frequency transformation.

Keep strict source-held-out text outside teacher adaptation.

If adaptation sees evaluation corpus text, label the run as transductive.

### Static-model variants

Run these controlled variants:

- P0: stock `potion-base-8M`.
- P1: stock `potion-retrieval-32M`.
- P2: Model2Vec distillation from `BAAI/bge-base-en-v1.5` with an ALICE
  vocabulary and no passage training.
- P3: Tokenlearn from `BAAI/bge-base-en-v1.5` with `vocab_size=0`.
- P4: Tokenlearn from `BAAI/bge-base-en-v1.5` with an ALICE vocabulary.
- P5: Tokenlearn from one compatible stronger teacher with `vocab_size=0`.
- P6: Tokenlearn from that stronger teacher with an ALICE vocabulary.
- P7: custom supervised static retrieval research.

Also rerun stock `potion-base-32M` as the historical comparator.

The BGE teacher matches the stock POTION retrieval lineage.

P3 separates corpus learning from vocabulary growth.

Treat P7 as custom research, not an upstream Model2Vec feature.

Attempt stock-model continuation only after Application Programming Interface
inspection proves compatible initialization.

The teacher screen must verify Model2Vec compatibility first.

Do not assume that the strongest dense retriever is the strongest static
teacher.

Decoder last-token models require special caution.

Models with poor static-distillation behavior leave the teacher screen.

Record `vocab_size`, `pca_dims`, and explained variance for every trained model.

Principal Component Analysis is required for Tokenlearn runs.

### Dimensions

Start with 256 and 512 dimensions.

Add another dimension only when the development result gives a reason.

Do not multiply every teacher by every possible dimension.

### Custom supervised retrieval research

Keep supervised training separate from unlabeled Tokenlearn adaptation.

Implement this branch only after P0 through P6 establish the static baseline.

Build triples from:

- Human-positive templates.
- Hard negatives from lexical retrieval.
- Hard negatives from dense retrieval.
- Cross-source confusing templates.

Never use held-out queries or held-out judgments.

Record whether one query has multiple valid positives.

### H100 execution rule

Run one teacher pilot first.

Measure examples per second, peak memory, cache size, and total featurization
time.

Cache teacher features once per teacher.

Reuse cached features across vocabulary and dimension variants.

Only schedule ten daily variants after the pilot supports that estimate.

### Static-model decision

A static model can win one of two roles:

- Default retriever when its relevance reaches the best practical band.
- Maintained CPU fallback when its quality is lower but acceptable.

A static model does not need to beat late interaction to remain useful.

### Stage S8 gate

Promote only static models that exceed the frozen practical difference against
their stock control.

Also require the gain on natural-language retrieval.

A source-purity gain alone cannot promote a model.

If no trained model wins, record the negative result and keep the stock model.

## Stage S9 — build and freeze production-form finalists

### Question

Can each finalist reproduce its development result in the intended production
form?

### Production implementation

Build the deployable form of every development finalist.

Keep exact exhaustive retrieval as a research oracle when production uses
approximation.

Use the frozen production hardware and OpenSearch shard topology.

Preserve the analyzer, fields, prompts, dimensions, and search pipeline.

Do not assume that an offline model export matches its production engine.

Verify native and exported model scores within a frozen numerical tolerance.

### Development equivalence

Run the development benchmark through each production-form implementation.

Compare it with the corresponding exact offline run.

For dense retrieval, report rank agreement and relevance change.

For late interaction, compare approximate search with exact MaxSim.

For fusion, verify normalization, component depths, and stable ties.

For Reciprocal Rank Fusion, preserve shard count and per-shard candidate depth.

Set acceptable parity tolerances only from development data.

Fix production discrepancies before any held-out access.

### Placement

Do not run embedding once per log line.

Document encoding belongs on the new-template cold path.

Query encoding belongs on storage or search infrastructure.

Worker placement requires proof against the frozen resource budget.

### Operational readiness

Every production-form finalist must provide:

- Health checks.
- Bounded memory.
- Bounded index growth.
- Incremental update behavior.
- Deterministic fallback.
- Model revision visibility.
- Corpus revision visibility.
- Safe restart behavior.
- Rollback instructions.

### Freeze before held-out access

Freeze all of these items:

- Corpus and duplicate-map identifiers.
- Query groups and relevance rubric.
- Model revisions and adapters.
- Task encoding modes and representations.
- Analyzer settings and field weights.
- Candidate depths and fusion settings.
- Quantization and export revisions.
- OpenSearch topology and pipeline revision.
- Random seeds and code revision.
- Practical-difference threshold.
- Product performance targets.
- Production parity tolerances.

Archive the freeze manifest before any held-out run.

### Stage S9 gate

Stage S9 passes only when:

- Every selectable finalist has a deployable form.
- Every production form passes development parity.
- Every production form meets the product contract.
- Approximate search stays within its frozen tolerance.
- Required fallback and rollback paths exist.
- The freeze manifest predates held-out access.

## Stage S10 — run the frozen held-out evaluation

### Question

Which frozen production system generalizes to unseen ALICE operator intents?

### Held-out pooling procedure

The benchmark custodian runs every frozen finalist against the hidden query
set.

Model developers receive no intermediate held-out output.

Run frozen research ceilings separately.

A research ceiling cannot become the production winner.

Take the top 20 results from each system for every retrieval task.

Take the top 200 results from each first-stage system for the frozen deep
natural-language subset.

Deduplicate pools by canonical template group.

Hide system identities and scores.

Judge every top-ten result before scoring.

Judge the remaining shallow and deep pools with the frozen rubric.

Compute system metrics only after judging finishes.

Report Recall at 100 and Candidate Recall at 200 only for the deep subset.

Label those metrics as pooled Recall.

### Required reporting

Report separate leaderboards for:

- Natural-language operator retrieval.
- Template-similarity retrieval.
- Exact-identifier retrieval.

Report the natural-language headline score for all independent intents.

Report class, source, and subsystem diagnostics when sample size permits.

Report paired confidence intervals between every finalist and BM25F.

Report the direct comparison between the top two finalists.

Report judged coverage beside every relevance result.

Report leave-one-system-out pool sensitivity.

Report research ceilings outside the production selection table.

Do not report unsupported subgroup conclusions from small samples.

### Winner decision order

Apply these rules in order:

1. Remove systems that fail license or security review.
2. Remove systems that fail correctness validation.
3. Remove systems that fail the frozen product contract.
4. Compare primary held-out natural-language relevance.
5. Apply the frozen practical-difference threshold.
6. Check identifier and high-severity regressions.
7. Compare memory, storage, and operational complexity.
8. Prefer the maintained system inside the same practical band.

The final report can name three systems:

- Production default.
- Low-cost fallback.
- Research quality ceiling.

These roles must remain separate.

### No retuning rule

Do not change a setting after viewing held-out results.

A failed held-out result ends that experiment version.

Further development requires a new plan version and a new held-out set.

### Stage S10 gate

Stage S10 passes only when:

- The freeze manifest predates held-out access.
- All finalists use the same corpus and task query sets.
- Human judging remains blind to system identity.
- Judged coverage at rank 10 reaches 100 percent.
- Deep Recall appears only for the deep judged subset.
- The report includes paired confidence intervals.
- The recommendation follows the frozen decision order.

## Stage S11 — live treatment-and-control validation

### Question

Does the selected system remain useful and safe in the live search path?

### Treatment-and-control microsmoke

Run the current production search as control.

Run the frozen selected system as treatment.

Use the same real operator queries for both systems.

Blind result identity during human preference review when practical.

Record:

- Query text.
- Control ranking.
- Treatment ranking.
- Operator preference.
- Missing expected results.
- New useful results.
- Latency for both systems.
- Search errors.

The microsmoke does not replace held-out evaluation.

It verifies live wiring and operator usefulness.

### Stage S11 gate

Deployment acceptance requires all conditions:

- Treatment beats or matches control in operator review.
- No required query class has a material regression.
- Latency meets the frozen target.
- Memory and storage remain inside their budgets.
- Incremental updates meet the freshness target.
- A new template appears once within that target.
- Failure activates the maintained fallback.
- Rollback has a verified microsmoke.

## Data boundaries

Three different boundaries must remain explicit.

### Retrieval corpus boundary

The retrieval corpus contains every document that production search can return.

All finalists search the same frozen corpus.

The primary corpus unit is the canonical semantic template group.

Source-instance documents remain available for diagnostics and metadata
inspection.

### Adaptation boundary

Strict domain adaptation excludes held-out source and time partitions.

This boundary tests transfer to unseen programs or periods.

Report this transfer challenge separately from the primary retrieval
leaderboard.

Transductive adaptation can see unlabeled retrieval documents.

It must carry a transductive label in every report.

### Query boundary

Development queries can guide model and system selection.

Held-out queries cannot guide any setting.

Queries from one incident or intent stay together.

## Stop rules

Stop the affected branch when any rule triggers:

- The live source census remains unresolved or lacks owner approval.
- Stage S0 lacks a representative production source.
- Raw-log and template-catalog routes are not distinct.
- The canonical duplicate unit remains undefined.
- A model adapter cannot reproduce its official behavior.
- A model license blocks the intended production use.
- A remote-code requirement fails security review.
- Development judged coverage at rank 10 falls below 95 percent.
- Held-out judged coverage at rank 10 falls below 100 percent.
- Deep qrels cannot support the requested Recall cutoff.
- Metrics from different retrieval tasks appear in one leaderboard.
- Candidate Recall at K caps the requested reranking gain.
- A custom feature gives no practical development gain.
- Approximate search loses more than the frozen tolerance.
- NextPlaid or FastPlaid export parity fails.
- Reciprocal Rank Fusion uses a different shard topology from production.
- Training sees held-out queries or judgments.
- The held-out set was opened before the freeze manifest.
- Production parity was postponed until after held-out access.
- A source causes duplicate cross-worker ingestion.
- Production search cannot fall back safely.

A stopped branch stays in the results file.

Do not delete negative or failed results.

## Planned repository artifacts

Do not overwrite the existing untracked `tools/embed/judgements.tsv`.

Audit it and migrate accepted labels into the new graded format.

The implementation should produce these versioned artifacts:

- `docs/SEMANTIC_RESULTS.md`.
- A dated source registry and routing record.
- A corpus manifest under `tools/embed/`.
- A canonical duplicate-group map.
- A frozen product performance contract.
- Separate task and query schemas.
- A query-group manifest under `tools/embed/`.
- Shallow development qrels with grades zero through three.
- Deep development qrels for candidate-recall evaluation.
- A sealed held-out query manifest.
- A sealed shallow and deep held-out pooling manifest.
- Model adapter definitions.
- Immutable run manifests.
- A pinned Sweet Search revision and reference adapter.
- Differential fixtures for the reduced Sweet Search port.
- A production parity manifest for every finalist.
- A live treatment-and-control validation report.
- A license and security record.

Large embeddings and index files belong in ignored experimental storage.

Commit raw query text only after a sensitivity review.

If templates contain restricted information, commit hashes and secure pointers
instead.

## Final definition of done

The semantic retrieval project is done when every statement below is true:

- All useful production log formats appear in the corpus.
- The source census has owner approval and a re-survey trigger.
- Production-rendered collector replay passes for every source.
- Raw-log and template-catalog routing decisions remain separate.
- Every corpus document has canonical and source-instance identifiers.
- The human relevance rubric is frozen.
- Development and held-out query groups cannot leak.
- Natural-language, similarity, and identifier tasks have separate results.
- Every model uses verified prompts and pooling.
- BM25, BM25F, learned sparse, dense, late, hybrid, and static systems ran.
- A cross-encoder reranker established the reranking quality ceiling.
- The pinned Sweet Search reference and reduced ALICE port ran directly.
- Every reranker reports its candidate-recall ceiling.
- ALICE static adaptation has controlled ablations.
- Every selectable finalist passed development production parity.
- The held-out result ran once.
- The winner meets license, latency, memory, and storage requirements.
- A treatment-and-control microsmoke confirms live usefulness.
- A maintained fallback and rollback path exist.

## Research basis

Primary references for the model and evaluation choices:

- [LightOn DenseOn and LateOn](https://huggingface.co/blog/lightonai/denseon-lateon)
- [LightOn mDenseOn and mLateOn](https://huggingface.co/blog/lightonai/mdenseon-mlateon)
- [DenseOn model card](https://huggingface.co/lightonai/DenseOn)
- [LateOn model card](https://huggingface.co/lightonai/LateOn)
- [mDenseOn model card](https://huggingface.co/lightonai/mDenseOn)
- [mLateOn model card](https://huggingface.co/lightonai/mLateOn)
- [LateOn-Code model card](https://huggingface.co/lightonai/LateOn-Code)
- [NextPlaid](https://github.com/lightonai/next-plaid)
- [FastPlaid](https://github.com/lightonai/fast-plaid)
- [Qwen3 Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen3 Embedding 8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)
- [Qwen3 Reranker model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
- [Perplexity embedding model card](https://huggingface.co/perplexity-ai/pplx-embed-v1-0.6b)
- [Jina v5 retrieval model card](https://huggingface.co/jinaai/jina-embeddings-v5-text-small-retrieval)
- [Nomic Embed Text v2 model card](https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe)
- [Model2Vec](https://github.com/MinishLab/model2vec)
- [Tokenlearn 0.2](https://minishlab.github.io/tokenlearn_release/)
- [OpenSearch combined fields](https://docs.opensearch.org/latest/query-dsl/full-text/combined-fields/)
- [OpenSearch Reciprocal Rank Fusion](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)
- [OpenSearch pretrained sparse models](https://docs.opensearch.org/latest/ml-commons-plugin/pretrained-models/)
- [OpenSearch late-interaction reranking](https://docs.opensearch.org/latest/search-plugins/search-relevance/rerank-by-field-late-interaction/)
- [OpenSearch Search Relevance Workbench](https://docs.opensearch.org/latest/search-plugins/search-relevance/using-search-relevance-workbench/)
- [TREC 2024 overview](https://trec.nist.gov/pubs/trec33/papers/overview_33.pdf)
- [ir-measures](https://github.com/terrierteam/ir_measures)

Project evidence remains in:

- `docs/LOG_TYPES.md`.
- `docs/SOAK_PLAN.md`.
- `docs/SOAK_RESULTS.md`.
- `docs/EMBEDDING_RESULTS.md`.
