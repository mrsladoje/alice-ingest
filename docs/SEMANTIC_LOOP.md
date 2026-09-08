# Autonomous run: finish the semantic retrieval plan

**Paste this to start a fresh session:**

```
/loop Read docs/SEMANTIC_LOOP.md and execute it. Continue until its definition
of done is met, then stop and write the morning report.
```

Start the session with permissions that do not prompt. An approval dialog at
02:00 stops the whole night. Wrap the terminal in `caffeinate -dimsu` as well,
or the machine sleeps and takes the run with it.

---

## 1. What this run is, and the decision that authorises it

Execute `docs/SEMANTIC_PLAN.md` from Stage S2 to Stage S11, unattended, on this
machine.

**The plan's binding rule 2 says human relevance judgements select retrieval
quality. Marko Sladojevic overrode that rule for this run on 8 September 2026:
every human role is played by a subagent.** That decision is his to make and it
is recorded here so no reader has to guess.

The consequence travels with every result. This run produces a
**machine-selected** recommendation. It is a development artefact. It does not
become the production selection until a person judges the pool, and every
document this run writes must say so in its own words rather than assume the
reader knows.

Nothing else in the plan is relaxed.

---

## 2. Non-negotiables

Break any of these and the night is worthless. They exist because an autonomous
agent is the thing most likely to break them quietly.

1. **The held-out set stays sealed.** The main session never reads
   `downloads/frozen/heldout/` — not the queries, not the grades, not a sample.
   Only the custodian subagent opens it. See section 7.
2. **The held-out evaluation runs once.** Not once per finalist revision. Once.
   If its result is bad, that is the result.
3. **No setting is tuned after any held-out number is seen.** If you find a bug
   after Stage S10, record it and stop. Do not fix and re-run.
4. **Development and held-out queries never share an incident or an intent.**
5. **Negative results are never deleted.** A model that fails to load, an arm
   that times out, a stage that regresses: all of it stays in the results file.
6. **Every run writes a manifest** with the fields listed in the plan under *Run
   manifest*. A number without a manifest is not a result.
7. **Write results as each stage closes**, not at the end. A crash at 05:00 must
   leave eight stages of findings on disk.
8. **Do not commit, do not push, do not touch git history.** Leave the working
   tree for review.
9. **Do not weaken a gate to pass it.** If a gate fails, write down that it
   failed and take the branch the plan prescribes.
10. **A model needing `trust_remote_code` is excluded**, unless the operator
    left a file `downloads/frozen/ALLOW_REMOTE_CODE` naming the models. The plan
    removes a candidate that fails security review, and no subagent may perform
    that review.

---

## 3. The state file: how this survives context loss

Every loop iteration begins by reading `downloads/frozen/RUN_STATE.json` and
ends by writing it. Create it on the first iteration:

```json
{
  "started": "<iso timestamp>",
  "stage": "S2",
  "step": "adapters",
  "corpus_id": "03640622026320ba",
  "completed": [],
  "failed": [],
  "notes": [],
  "heldout_opened": false
}
```

Rules:

- One iteration does **one unit of work**, then updates the state and the
  results file. A unit is one arm, one screen, or one gate — not a whole stage.
- `completed` holds unit names that must never run again.
- `failed` holds a unit name, the reason and the timestamp. A failed unit is
  never retried more than twice.
- If `heldout_opened` is true, Stages S2 to S9 are closed for ever. Refuse to
  re-run them.
- If the state file and the results file disagree, the results file wins. It is
  the record; the state file is only a pointer.

---

## 4. What already exists

Verify before starting. If the corpus hash differs, stop and report.

| Thing | Path | Identity |
|---|---|---|
| Frozen corpus | `downloads/frozen/corpus-2026-09-08/corpus.jsonl` | `corpus_id` **03640622026320ba**, sha256 `da154711e7809a6f19fe4ef73e91854d84a6e0a4ce13f5b1992fd1032e44d9ec` |
| Duplicate map | `.../duplicates.json` | 5,301 canonical groups, 18,011 instances |
| Manifest | `.../manifest.json` | inputs, hashes, recipes, clock domains |
| Migrated labels | `downloads/frozen/qrels-2026-09-08/qrels.tsv` | 597 carried, 38 lost |
| Graded labels | `.../qrels-graded.tsv` | 589 graded by six machine assessors |
| Agreement | `downloads/frozen/judging-2026-09-08/agreement.json` | lowest kappa 0.769 |
| Rubric | `docs/JUDGING_RUBRIC.md` | four grades, kappa threshold 0.60 |
| Results so far | `docs/SEMANTIC_RESULTS.md` | rounds 1 to 3 |

Tools that already work: `tools/embed/mkcorpus.py`, `freeze.py`, `qrels.py`,
`judgekit.py`; tests `test_freeze.py` (18), `test_qrels.py` (6),
`test_judgekit.py` (10), `tools/templating/test_corpus.py` (6). Run all four
before starting. If any fails, fix it first and say so in the results.

Two corpus limitations are known and must be repeated in any conclusion they
touch: InfoLogger severity is `absent` for every line, and DataDistribution
program identity is `parse_failed` until the next archive pull.

---

## 5. Environment

**Nothing long runs in the foreground.** A foreground shell call is capped at
ten minutes, and the budgets in section 8 are far longer than that. Every model
download, every encode, every training run starts in the background and is
polled. A 45-minute arm run in the foreground is a killed arm, not a slow one.

```bash
nohup <command> > downloads/frozen/logs/<unit>.log 2>&1 &
# then poll, and wait on a condition rather than on a sleep
until [ -f <the file it writes> ]; do sleep 30; done
```

Create `downloads/frozen/logs/` on the first iteration. One log per unit, named
after the unit, never overwritten.

**Python.** Create one venv at a stable path — not in a session scratchpad,
which does not survive a restart:

```bash
python3 -m venv downloads/frozen/venv
downloads/frozen/venv/bin/pip install -q drain3 pyyaml jinja2 numpy \
    sentence-transformers transformers torch scikit-learn
```

`drain3` must be 0.9.11. Anything else changes the templates.

**Accelerator.** Torch on this machine uses `mps`. Select it explicitly, fall
back to `cpu`, and record which one every measurement used. A latency figure
without its device is not a figure.

**OpenSearch.** Stages S4, S7 and S9 need the production version, 3.7.0. This
recipe is proven in `tools/collector/retrycheck.py`:

```bash
docker run -d --name semantic-os -p 9200:9200 \
  -e discovery.type=single-node -e DISABLE_SECURITY_PLUGIN=true \
  -e OPENSEARCH_JAVA_OPTS="-Xms2g -Xmx2g" \
  opensearchproject/opensearch:3.7.0
```

Other containers are running on this machine. Use a unique name, do not stop
anything you did not start, and remove your own container when the stage ends.

**Models.** Download from Hugging Face anonymously. If a download fails twice,
record the arm as failed and continue. No single missing model may stop a stage.

---

## 6. The stages

Take them in the order written here, which is the order they must run in, not
the order their names sort in. The names follow `docs/SEMANTIC_PLAN.md`, so
sealing the held-out set keeps its plan name S1b even though it runs fourth: it
has to happen after the development intents exist and before any model is
screened.

Each stage names its gate. A gate that fails takes the branch written under it —
it never gets softened.

### S2 — the harness

Build `tools/embed/bench.py`: a retrieval harness with one adapter per model
family.

- Every dense adapter declares: query prefix, document prefix, pooling,
  normalisation, max length, dimensions, dtype, revision. Read these off the
  model card, never guess.
- Every late-interaction adapter also declares token dimensions, query and
  document token limits, and its MaxSim implementation.
- Every adapter carries one golden fixture that matches the official
  implementation inside a stated tolerance.
- Metrics: nDCG@10 primary; MRR@10, pooled Recall@20, Success@5, Judged@10 and
  Judged@20 secondary. Gains 0, 1, 3, 7 for grades 0 to 3. Grades 2 and 3 are
  relevant.
- Rank the full corpus. Break ties on `canonical_id`. Collapse canonical groups
  before ranks are assigned — a group may occupy one rank, never five.
- Paired bootstrap over intent groups, 1,000 replicates, Holm correction for
  exploratory comparisons.
- **Three tasks, three schemas, three leaderboards**: natural-language
  retrieval, template similarity, exact identifier. Separate query files,
  separate run files, separate metric sets. Identifier queries report Success@1
  and Recall@10; similarity queries exclude the query group and all its
  instances from their own result list. Never average one task into another —
  the headline is natural language alone.

**Gate.** Fixtures match. Repeated runs give identical rankings. Unjudged is
distinct from irrelevant. Every metric has a unit test with a hand-computed
answer. **If a metric has no test, it does not get reported.**

### S2b — grow the development set

20 queries is below what the plan needs. Author **at least 80 development
intent groups** with the query-writer subagent (section 7), grounded in
`docs/LOG_TYPES.md` and in real templates from the corpus.

Cover every class the plan lists, including negative queries, ambiguous short
queries, exact identifiers and template-similarity queries. Keep paraphrases of
one intent inside one group. Mark every query `synthetic: true`.

Pool candidates for the new intents with BM25, BM25F and two dense models, take
the top 20 each, deduplicate by canonical group, then grade with four assessor
subagents plus two second-pass assessors on a 20 % sample.

The harness writes those pooled candidates in the column shape of
`qrels.tsv`, so `judgekit.py --deal` deals them unchanged. Do not build a second
pooling format; the blinding, the redaction and the shuffle already live there.

**Gate.** Every assessor pair with 20 or more shared items reaches weighted
kappa 0.60. **If it does not, revise the rubric, say what was unclear, and
re-judge that sample once.** If it fails twice, stop the branch and report.

### S1b — seal the held-out set

Do this **before** any model is screened, and never touch it again.

The custodian subagent authors **at least 60 held-out intent groups** in
`downloads/frozen/heldout/`, from programs and sources that the development
intents do not use, and reports back only the count. The main session records
the count and the directory hash, and reads nothing else.

**The power calculation, recorded before the set is used.** Using the paired
standard deviation measured on development, state the difference that 60 intent
groups can detect at a two-sided 0.05 with 80 % power. If that is larger than
the frozen practical difference, **record the difference the set can actually
detect and limit every held-out conclusion to it.** Never raise the threshold to
make a result significant.

**Gate.** `heldout/queries.jsonl` exists, its sha256 is in the state file, the
power calculation is recorded, and the main session has never opened it.

### S2c — freeze the two numbers that later gates depend on

Both must exist before Stage S3, and both go in the results file with their
recipe. Every later gate reads them, so a run that skips this step is a run
whose gates mean nothing.

**The minimum practically important difference.** Run L0 and L1 on the
development intents. Bootstrap 1,000 replicates over intent groups on their
paired nDCG@10 difference and take the 95 % interval half-width. The practical
difference is that half-width, or **0.02 nDCG@10**, whichever is larger. Record
both numbers and say which one won.

**The product performance contract.** The plan wants an owner's approval and
forbids inventing latency limits during model comparison. No subagent may
approve it, so split it in two and label the halves.

*Hard constraints, read off the deployment* — `deploy/group_vars/all.yml` and
the role defaults hold the frozen processor and memory reservations. Local-only
operation with no external inference service, the memory ceiling, and the index
and model storage budget are facts. They remove a candidate at Stage S10.

*Latency targets are provisional.* Write them down, measure against them, and
record that they are machine-authored. **They never remove a candidate.** Stage
S10 applies decision-order step 3 to the hard constraints only, and says so.

**The corpus-coverage rule, applied before anything is scored.** A query whose
pool holds no grade 2 or 3 has no reachable answer, so it leaves the
effectiveness metrics and is reported as a coverage case. `detector readout
error` is already one of these in the graded pool. Re-check after S2b, and never
score a query no system can win.

### S3 — representations

Compare R0 (template only), R1 (identity plus template), R2 (stable semantic
preamble), R3 (structured fields), R4 (dual) on four probe systems: BM25F,
a dense model, a late-interaction model, `potion-retrieval-32M`.

InfoLogger severity is absent, so any R2 arm must report how many documents
carried a severity at all.

**Gate.** Freeze one representation per promoted model and task. Record every
excluded field and why.

### S4 — lexical and learned sparse

- **L0** plain BM25 over the template text. Mandatory control, never dropped.
- **L1** BM25F over `combined_fields`, weights tuned on development only.
- **L2** the pinned Sweet Search reference, commit
  `8bbbc14b9176ceb192c591b925c41b6a3198b482`, through a thin ALICE data adapter.
  The checkout is at `/Users/admin/Projects/sweet-search-private`, and that
  commit was its HEAD on 8 September 2026 — verify it, and if HEAD has moved,
  read the pinned commit rather than HEAD. **Never modify that repository.** If
  it is unreachable, record L2 as unavailable and say plainly that every
  "custom against reference" conclusion is unsupported without it.
- **L3** the reduced ALICE port: identifier anchoring, field weights,
  per-term rescue, optional trigram fallback, score calibration. Differential
  fixtures against L2 for each feature.
- **SP0** the maintained neural sparse model
  `amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte` on
  OpenSearch 3.7.0.
- Analyzer screen: standard against one identifier-preserving analyzer, tested
  on camelCase, underscores, hex, paths and punctuation.

**Gate.** L3 is promoted only if it beats the frozen practical difference and
does not reduce exact-identifier Success@1 or Recall@10. A custom feature that
adds nothing is removed and the removal is reported.

### S5 — dense screen

Controls: `all-MiniLM-L6-v2`, `potion-retrieval-32M`, `potion-base-32M`.

Core: `lightonai/DenseOn`, `lightonai/mDenseOn`,
`perplexity-ai/pplx-embed-v1-0.6b`, `Qwen/Qwen3-Embedding-0.6B`.

Ceiling: `Qwen/Qwen3-Embedding-8B`, research only.

Excluded unless the operator allowed remote code: anything needing
`trust_remote_code`, `jinaai/jina-embeddings-v5-text-small-retrieval` until its
licence is approved.

Exact exhaustive cosine first, always. Quantisation only after the float
reference exists, and its loss is reported against that reference.

**Gate.** Promote at most two production-eligible dense models plus one
efficient control. Keep the research ceiling out of the production table.

### S6 — late interaction

`lightonai/LateOn`, `mLateOn`, `LateOn-Code-edge`, `LateOn-Code`. Exhaustive
MaxSim over 5,301 documents is the ranking oracle — measure it, do not assume it
is too expensive. Cross-encoder ceiling: `Qwen/Qwen3-Reranker-0.6B`, as a
reranker only.

Candidate screen at depths 20, 50, 100 and 200. Report candidate Recall at each
depth. **A reranker is never blamed for a candidate that was never retrieved.**

Approximate search (NextPlaid, FastPlaid) only after export parity is verified
against native scores inside a stated tolerance.

### S7 — hybrids and fusion

H0 to H10 exactly as the plan lists them. Reciprocal Rank Fusion with the
upstream rank constant of 60 as the fixed control, and the production shard
topology recorded with every fusion number.

**Gate.** A custom fusion is promoted only if its gain exceeds the practical
difference. **If fusion gives nothing, deploy the simpler single system and say
so plainly.**

### S8 — ALICE static models

P0 and P1 stock. P2 Model2Vec distillation. P3 to P6 Tokenlearn from
`BAAI/bge-base-en-v1.5` and one stronger compatible teacher, with and without an
ALICE vocabulary. P7 is research and runs only if P0 to P6 finished.

Cache teacher features **once per teacher** and reuse them across vocabulary and
dimension variants. Dimensions 256 and 512 only, unless a development result
gives a reason for another.

Training text: deduplicated templates, frequency-capped raw messages, rare
high-severity examples. Record every cap. Held-out source text stays out; if it
gets in, label the run transductive.

**Gate.** A static model is promoted only if it beats its stock control on
natural-language retrieval by more than the practical difference. **Source
purity alone cannot promote anything.** If none wins, record the negative result
and keep the stock model — that is a real finding, not a failure of the night.

### S9 — production form and freeze

Build the deployable form of every finalist. Run the development benchmark
through it. Compare with the exact offline run: rank agreement, relevance
change, fusion normalisation, shard counts, candidate depths.

Then write the freeze manifest: corpus and duplicate-map ids, query groups,
rubric, model revisions, adapters, representations, analyzers, weights,
candidate depths, fusion settings, quantisation, topology, seeds, code revision,
practical-difference threshold, parity tolerances.

**Gate.** Every finalist has a deployable form that passes parity. The freeze
manifest exists and is timestamped **before** anything in S10 begins.

### S10 — the held-out evaluation, once

Set `heldout_opened: true` in the state file first. That flag closes S2 to S9
permanently.

Hand the frozen finalists' run files to the custodian. The custodian scores them
against the sealed set, judges the pools blind with its own assessors, and
returns metrics and confidence intervals only.

Report three leaderboards — natural language, template similarity, exact
identifier — and never average them. Report judged coverage beside every number.
Report paired intervals against BM25F, and the direct comparison of the top two.

Apply the winner decision order from the plan, in order: licence and security,
correctness, product contract, primary relevance, practical difference,
identifier and high-severity regressions, cost and complexity, then prefer the
maintained system inside the same band.

Name three systems: production default, low-cost fallback, research ceiling.

### S11 — treatment and control

Run the current search as control and the winner as treatment over the same
development queries. Record both rankings, latency, errors, missing expected
results and new useful results. A preference subagent compares them blind.

This verifies wiring and usefulness. It does not replace S10.

---

## 7. The subagents

Use Sonnet. Give each one only the files it needs. Never let an assessor see a
system name, a score, or another assessor's grades.

| Role | Count | Job |
|---|---|---|
| **assessor** | 4 | Grade a disjoint slice of the pool with `docs/JUDGING_RUBRIC.md`. |
| **second-pass assessor** | 2 | Grade the same 20 % stratified sample, for agreement. |
| **query writer** | 2 | Author development intents from real corpus content, one class each. |
| **custodian** | 1 | Own the held-out set. Author it, seal it, score it at S10, return metrics only. |
| **adversary** | 1 | After each stage, try to break that stage's conclusion. Its objections go in the results file whether or not they hold. |

`tools/embed/judgekit.py --deal` already produces blinded, redacted, shuffled
item files, and `--collect` already computes agreement. Use it rather than
inventing a second judging path.

**The custodian rule.** The custodian's prompt must forbid it from writing held
-out text into any file outside `downloads/frozen/heldout/`, and from putting
query text in its reply. Its reply is counts and metrics.

**The leakage that cannot be removed.** Every assessor, every query writer and
the custodian are the same model family. A held-out set authored and judged by
relatives of the systems being scored is weaker evidence than a human set. Say
this in the morning report in one plain sentence. Do not bury it.

---

## 8. Budgets

These are caps that stop one arm eating the night. They are not predictions of
how long anything takes.

| Unit | Cap |
|---|---|
| One model download | 20 minutes, two attempts, then record as failed |
| One dense or late arm, end to end | 45 minutes |
| One Tokenlearn teacher feature pass | 90 minutes |
| One Tokenlearn variant | 45 minutes |
| One stage | 3 hours, then close it with what it has and move on |

**S2, S2b, S1b and S2c are exempt from the stage cap.** They are the harness,
the queries, the sealed set and the two frozen numbers. Everything downstream is
meaningless without them, so they finish however long they take. A capped
screen is a smaller result; a capped harness is no result at all.

A unit that hits its cap is written down as capped, with what it had reached.
The pipeline continues. **A stage closed early says so in its own section.**

Order of value if the night runs short: S2, S2b, S4, S5, S7, S6, S8. Stage S10
runs only if S9 passed. Never skip S9 to reach S10.

---

## 9. Stop rules

Stop the affected branch, write why, and continue with the rest:

- A model adapter cannot reproduce its official behaviour.
- A licence or a remote-code requirement blocks a candidate.
- Development judged coverage at rank 10 falls below 95 %.
- Candidate recall caps the reranking gain being claimed.
- Approximate search loses more than its stated tolerance.
- Assessor agreement misses the frozen threshold twice.

Stop the **whole run** and wait for a person:

- The corpus hash does not match section 4.
- The held-out set was opened before the freeze manifest existed.
- Any held-out text reaches the main session.
- The results file has been truncated or overwritten rather than appended.

---

## 10. Definition of done, and the morning report

The run is done when every stage above has either passed its gate or has a
written record of why it did not, and `docs/SEMANTIC_RESULTS.md` holds a section
for each.

Then write **one section at the top of the results file**, titled *Morning
report*, no longer than a page:

1. The production default, the low-cost fallback, the research ceiling.
2. Its headline held-out nDCG@10 with a paired interval against BM25F.
3. The one sentence that says these labels are machine-made, that a person has
   not judged this pool, and that the recommendation is therefore provisional.
4. What failed, what was capped, and what was never reached.
5. The three things a person should check first.

Write it for someone who has been asleep and reads exactly one page.

Then end the loop rather than scheduling another wake-up, and say in one line
where the artefacts are.

---

## 11. Conventions this repository keeps

- **No comments in code.** Docstrings that explain a decision are welcome;
  line-by-line commentary is not.
- **No migration, cleanup or back-compatibility code** unless it was asked for.
  Nothing here is in production and it is rebuilt from scratch.
- **Prose leads with the conclusion**, one idea per sentence, and says what a
  number means rather than only what it is.
- **A finding that contradicts an earlier round is stated, not smoothed.** The
  results file already contains four defects this project found in its own work.
  That is the standard.
