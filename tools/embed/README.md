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
