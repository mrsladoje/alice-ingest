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
