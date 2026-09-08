# embed

Round 3 of `docs/SOAK_PLAN.md`: price the embedding ladder and pick a rung.
Results are in `docs/EMBEDDING_RESULTS.md`.

Input is the template dump round 4 produces:

```bash
python3 tools/templating/drainbench.py corpus.tsv --block 0 \
    --dump-templates templates.tsv
python3 tools/embed/embedbench.py templates.tsv --json embed-report.json
```

The dump is four columns — count, dominant source, how many sources the template
appeared under, and the template itself. `embedbench.py` also accepts the older
two-column dump, and then says the purity check cannot run rather than inventing
labels for it.

## What it measures

**Throughput at one core.** `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and
`torch.set_num_threads` are all pinned to 1 before anything loads. The embedder
will only ever get a fraction of four cores, so a figure taken with every core
busy would describe the laptop rather than the plan.

**Source purity.** Do a template's `k` nearest neighbours come from the same
program? Reported three ways, and only one of them is worth reading:

| Column | What it is |
|---|---|
| `source_purity` | Averaged over templates. One source owns 57 % of them, so every model lands near 0.76 and nothing separates. |
| **`macro_source_purity`** | **Averaged over sources, one vote each. This is the one that separates the rungs.** |
| `neighbour_agreement` | Overlap with the reference model's neighbours. Kept for completeness; a model disagrees with its own quantized self 36 % of the time, so it measures nothing. |

Both purities come with a null — the score random neighbours would reach on the
same source distribution. **Read every purity against its own null**, and never
against the other one's: the micro null is 0.344 and the macro null is 0.035.

## Options

| Flag | Default | Why you would change it |
|---|---|---|
| `--k` | 10 | Neighbourhood size for purity and agreement. |
| `--max-tokens` | 64 | Templates average 13 tokens; a longer window pads for nothing. |
| `--batch-size` | 64 | Batches are length-sorted first, so padding is near zero. |
| `--min-templates` | 10 | Sources below this are left out of the macro purity. |
| `--reference` | `all-MiniLM-L6-v2` | The model `neighbour_agreement` is measured against. |
| `--only` | all rungs | Comma-separated rung names, for a single-model run. |

Models download from Hugging Face on first use, anonymously. No token is read or
needed.

`RUNGS` at the bottom of the file is the ladder. Adding a rung is one entry:
`kind` is `static` for a Model2Vec lookup table and `transformer` for anything
sentence-transformers loads, and `quantize` turns on int8 dynamic quantization —
which was **5.4 times slower** than fp32 on Apple silicon. See the results.

---

## Round 6 — the split, the post-trained rungs, and the query set

Round 3 scored one shelf of models on one set of templates. Stage I of
`docs/SOAK_PLAN.md` asks a different question: does training on our own text beat
the shelf, and does the gain survive a program the model has never seen? That
needs three more pieces.

### splits.py

Splits the mined templates **by source program, never at random**, and writes the
training text with every evaluation source removed.

```bash
python3 tools/embed/splits.py templates.tsv --out splits/ --corpus corpus.tsv
```

Writes `dev.tsv`, `heldout.tsv`, `train-lines.txt` and `splits.json`. The
selection rule is in the file's docstring and is fixed before anything is
scored, so the sets cannot be chosen to flatter a result.

🔴 **Every purity is read against the null of its own set.** The macro null of
0.035 in `docs/EMBEDDING_RESULTS.md` is a property of the 23-source set, not of
the metric. A five-source set sits near 0.20, and a table that puts the two in
one column says the opposite of the truth. `splits.json` carries each set's own
null for exactly this reason.

### posttrain.py

Three post-trained models, at very different prices:

| Mode | What it does | Cost |
|---|---|---|
| `vocab` | Mines a vocabulary from the training lines and distils the teacher against it. Every ALICE identifier becomes one vector instead of four subword fragments | Minutes |
| `tokenlearn` | The full POTION recipe — teacher means over the training text, train the static model against them, re-regularise by frequency, PCA and SIF | Hours |
| `fasttext` | A gensim FastText over the same lines. Round 3's named untested rung | Tens of minutes |

```bash
python3 tools/embed/posttrain.py vocab --train-lines splits/train-lines.txt \
    --out models/alice-vocab-bge --teacher baai/bge-base-en-v1.5
python3 tools/embed/posttrain.py vocab --train-lines splits/train-lines.txt \
    --out models/alice-vocab-code --teacher nomic-ai/CodeRankEmbed --trust-remote-code
```

**Two teachers, because the shelf has two.** `potion-base-8M` and
`potion-base-32M` are both distilled from `baai/bge-base-en-v1.5` at 256 and 512
dimensions; `potion-code-16M` is distilled from `nomic-ai/CodeRankEmbed`. Log
text is closer to code than to prose, so which teacher suits it is a question to
measure rather than assume. **Both are chosen on dev only.**

`nomic-ai/CodeRankEmbed` needs `--trust-remote-code`, and its remote code needs
two things worked around — see `load_teacher`. Both teachers then take the same
code path, which is the only way the arms compare.

### The query set

`queries.txt` holds twenty plain-English queries. Retrieval runs with
`--queries`, and `--pool-out` writes every model's top ten per query as a file to
judge:

```bash
python3 tools/embed/embedbench.py splits/dev.tsv --queries tools/embed/queries.txt \
    --pool-out pool.tsv
# judge the middle column, then
python3 tools/embed/embedbench.py splits/dev.tsv --queries tools/embed/queries.txt \
    --judgements judged.tsv
```

🔴 **This metric exists because the other one can be gamed by this exact
training.** Macro source purity asks whether a template's neighbours come from
the same program, and post-training on program-specific text raises that score
without the embedding becoming more useful. The query set is the only check the
training cannot optimise against.

**An unjudged pair counts as not relevant**, which penalises a model whose new
neighbours nobody pooled. `query_pairs_unjudged` reports how large that penalty
is for each model.

### New rungs

| Rung | Kind | Why it is here |
|---|---|---|
| `potion-code-16M`, `potion-code-16M-v2` | static | Stage I3. Code-distilled, 256 dimensions |
| `CodeRankEmbed` | transformer | The code teacher itself, as a ceiling for the arms distilled from it |
| `CodeRankEmbed-onnx-int8` | onnx | Round 3 measured int8 through torch's `qnnpack` and found it 5.4× **slower**, and said the number might not survive another backend. This is another backend |

`--model NAME=KIND=ID` adds anything else, including a local post-trained
directory: `--model alice-vocab=static=models/alice-vocab-bge`.

**Models with an asymmetric query prefix carry it in the rung**, and it is
applied to queries only, never to templates. `CodeRankEmbed` was finetuned for
natural-language-query-against-code, so its prefix belongs on the twenty queries
and nowhere else.

---

## The frozen retrieval corpus

`docs/SEMANTIC_PLAN.md` Stage S0 ends with a corpus that can be named, and Stage
S1 cannot start until the unit that corpus is searched in is decided. Two tools
produce both, and neither restates a rule that lives somewhere else: mining is
`tools/templating/drainbench.py`'s shipped recipe, and severity and program come
from the rendered production parser cascade.

### mkcorpus.py — one file per family

```bash
python3 tools/embed/mkcorpus.py --out downloads/frozen
```

Four columns: family, source, severity, message. The fourth column is new and
the third is why. Six of the seven families keep their severity inside the line,
where the cascade finds it; the journal keeps it in `PRIORITY`, outside the
message, exactly as the collector reads it. A three-column corpus would have
thrown that away and then reported the journal as a source with no severity.

The archive families come from the refamilied pull, whole. **There is no line
cap, and an early version of this had one.** Three million lines of InfoLogger
mine 675 templates; all 33.4 million mine 1,497. A cap on the corpus being
frozen buys minutes and costs coverage. The three the archive does not hold —
the InfoLogger daemon log, the journal and the orchestrator — come from the farm
captures `tools/epnsurvey/mkbundle.sh` takes.

### freeze.py — canonical groups and their source instances

```bash
python3 tools/embed/freeze.py downloads/frozen/fam-*.tsv \
    --out downloads/frozen/corpus-<date>
```

Writes `corpus.jsonl`, `duplicates.json` and `manifest.json`.

**The retrieval unit is the canonical template group.** One record is one
normalised event meaning, carrying every source instance that produced it. The
same GPU allocation failure written by forty programs is one result with forty
instances attached, not forty results — otherwise one event takes every rank a
metric at ten can see.

| Identifier | What it hashes | What it survives |
|---|---|---|
| `canonical_id` | the normalised template text | a model, representation or re-mine change |
| `instance_id` | family, program and template | the same, and it stays distinct per program |

Normalisation is deterministic and is written into `duplicates.json` beside the
groups it produced: every mask placeholder and every Drain wildcard becomes
`<*>`, the text is lower-cased, whitespace collapses, and edge punctuation goes.
`<NUM> bytes` and `<FLOAT> bytes` are the same event; `new client: <*>` and
`new client` are not, which is why the edge rule strips punctuation and never a
placeholder.

Two fields are honestly empty rather than guessed, and both are recorded in the
manifest per family:

- **InfoLogger severity is `absent`.** The archive pull kept family, source and
  message; severity is a column of the mysqldump and was never written to the
  corpus. It returns on the next pull, not by inference here.
- **DataDistribution program was `parse_failed`.** `corpus.py` asked for a
  `_recoN_` segment that DataDistribution file names do not carry, so every one
  of its lines was labelled `unknown`. The rule now mirrors the shipped
  `stdout_path` parser and is anchored to the file name; the corpus in hand
  predates the fix.

### qrels.py — carrying the old labels onto the frozen corpus

```bash
python3 tools/embed/qrels.py --corpus downloads/frozen/corpus-<date>/corpus.jsonl \
    --out downloads/frozen/qrels-<date>
```

Writes `queries.jsonl`, `qrels.tsv` and `migration.json`.

Stage S1 opens with an audit of the 635 pairs in `judgements.tsv`. This tool
does the half a machine may do, and refuses the half it may not.

**It re-anchors a label from template text to a canonical identifier.** The old
file names a template by its text, and text moves whenever the recipe or a
parser moves. An identifier does not.

**It never invents a grade.** Every migrated row leaves with `grade = -1` and
`state = unreviewed`. The old binary label rides along as `prior_binary`, next
to the name of what produced it. The plan is explicit that these labels came
from an AI judge, that they are development data for ever, and that a graded
judgement is a person's decision.

Two counts in `migration.json` are the ones to read. **Lost** is a template the
frozen corpus no longer holds, so its label has nothing to point at.
**Collapsed onto a judged group** is two old templates that are now one
canonical group: the second one is dropped, because counting it twice would
make one answer look like two pieces of evidence.

A row that matches only after separators are unpadded says so in `matched_by`.
That tolerance exists because the process tree was mined as `stdout` then and as
`dpl` now, and the two recipes disagree about padding `= ;`. It is a migration
convenience and not the canonical rule: folding separators into the canonical
rule merges two groups out of 4,304, which is not worth a looser corpus.

### judgekit.py — dealing a judging pool, and reading the grades back

```bash
python3 tools/embed/judgekit.py --deal \
    --qrels downloads/frozen/qrels-<date>/qrels.tsv \
    --corpus downloads/frozen/corpus-<date>/corpus.jsonl \
    --out downloads/frozen/judging-<date>
# assessors fill in grades-<name>.tsv, then
python3 tools/embed/judgekit.py --collect \
    --qrels downloads/frozen/qrels-<date>/qrels.tsv \
    --graded downloads/frozen/judging-<date> \
    --out downloads/frozen/qrels-<date>/qrels-graded.tsv \
    --report downloads/frozen/judging-<date>/agreement.json
```

The plan puts three conditions on a judgement, and all three are mechanical, so
they live here rather than in an assessor's discipline. The assessor sees the
template, its stable metadata, its frequency and **redacted** examples — the
same masker the miner uses, so an address or a path never reaches the reviewer.
The assessor never sees which system returned the candidate, its score, or the
old binary label. Candidate order is randomised from a recorded seed.

`--deal` splits the queries across primary assessors, and gives every
second-pass assessor the same stratified sample, so agreement is measured on
identical items rather than on whatever happened to overlap.

`--collect` merges the primary grades into the qrels and measures every pair of
assessors that shares at least 20 items: raw agreement, agreement within one
grade, agreement on relevant-or-not, and weighted Cohen's kappa. The threshold
kappa must clear is frozen in `docs/JUDGING_RUBRIC.md` **before** any grade is
read.

`assessor_kind` records what the assessor was. A machine assessor is named like
any other and marked `model`. The plan forbids machine labels from selecting a
retrieval system, and this file does not change that; what a machine pass buys
is a rehearsal of the pipeline and a measurement of whether the rubric is clear
enough for two independent assessors to agree.
