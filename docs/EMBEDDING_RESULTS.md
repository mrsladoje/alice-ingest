# Round 3 — embeddings · results

The plan is `docs/SOAK_PLAN.md`, section *Round 3 — embeddings, cheapest first*.
Round 4 is `docs/TEMPLATING_RESULTS.md` and supplies this round's input.

Measured 27 August 2026 against the **3,822 templates round 4 mined from 45.6
million lines of the real archive**. Tools are `tools/embed/`.

---

## Read this first

**The plan's premise no longer holds, and the result is the opposite of the one
it predicted.**

It said: *"a small transformer does hundreds to low thousands of short texts per
second per core. Against the burst rate on the cores we have, that is one to two
orders of magnitude short. This round confirms a ceiling; it does not hunt for a
configuration that works."*

That reasoning priced an embedding **per line**. Round 4 established that an
embedding is paid **per template**, and that new templates arrive at about
**one per second** at the reference rate. There is no ceiling to confirm.

**Three findings:**

1. **Static embeddings are 31 times faster than the baseline and keep 93 % of
   its quality.** `potion-base-32M` reaches **18,037 templates per core-second**
   against `all-MiniLM-L6-v2`'s 581, at a macro source purity of 0.387 against
   0.414.
2. **Every rung finds real structure in our logs.** Against a random-neighbour
   null of **0.035**, all six score between 0.380 and 0.417 — eleven to twelve
   times the null. The choice is not between working and not working.
3. 🔴 **int8 dynamic quantization made it 5.4 times SLOWER, not faster.**
   107 templates per core-second against 581 for the same model in fp32, with
   quality unchanged. This is measured on Apple silicon through the `qnnpack`
   backend and **may not hold on the EPN's x86 nodes**, which would use
   `fbgemm`. It is recorded as a warning, not as a property of int8.

**The pick: `potion-base-32M`.** It costs about **0.006 % of a core** at the
observed template rate. `potion-base-8M` is within noise of it — 0.380 against
0.387 — at half the dimensions, and would be the pick if vector storage ever
mattered. On this evidence it does not.

---

## The ladder

3,822 templates, one core, 64-token truncation, length-sorted batches of 64.

| Rung | Dims | Templates /core-s | Core-s /M templates | **Macro purity** | Micro purity | Agreement with L6 |
|---|---:|---:|---:|---:|---:|---:|
| `potion-base-8M` | 256 | 17,687 | 56.0 | 0.380 | 0.759 | 0.303 |
| **`potion-base-32M`** | 512 | **18,037** | **55.9** | **0.387** | 0.760 | 0.304 |
| `potion-retrieval-32M` | 512 | 17,947 | 55.6 | 0.382 | 0.760 | 0.299 |
| `paraphrase-MiniLM-L3-v2` | 384 | 1,105 | 916.1 | 0.404 | 0.773 | 0.369 |
| `all-MiniLM-L6-v2` | 384 | 581 | 1,720.7 | 0.414 | 0.774 | reference |
| `all-MiniLM-L6-v2` int8 | 384 | **107** | 9,301.4 | 0.417 | 0.777 | 0.636 |
| *random-neighbour null* | — | — | — | **0.035** | 0.344 | — |

**The whole quality range is 0.380 to 0.417.** Thirty-one times the cost buys
**7.0 % more macro purity** — 0.387 against 0.414 — and the widest gap on the
whole ladder, cheapest rung against dearest, is **9.7 %**. At a workload of one
template a second, neither is a trade worth making.

**A cross-check worth recording.** The plan quotes `potion-base-32M` at 94.66 %
of `all-MiniLM-L6-v2`'s published MTEB average. On our own log templates, with a
metric of our own, it reaches **93.5 %** of the same model. The published claim
transfers to this data almost exactly, which is not something that could be
assumed in advance.

---

## How quality was measured, and why the obvious metric was thrown away

**The first attempt failed and is recorded here rather than deleted.** The
obvious proxy — do the cheap models put the same templates next to each other as
`all-MiniLM-L6-v2` does — does not work:

| Pair | Ten-nearest-neighbour agreement |
|---|---:|
| `paraphrase-MiniLM-L3-v2` against `all-MiniLM-L6-v2` | 0.369 |
| The same model in int8 against itself in fp32 | 0.636 |
| Any `potion` against `all-MiniLM-L6-v2` | ~0.30 |

**A model disagrees with itself about a third of the time once quantized**, and
two transformers from the same lineage agree on barely a third of their
neighbours. Agreement with a chosen reference is therefore not a measure of
correctness on this text — it measures how close a model sits to one arbitrary
point. It is reported above for completeness and used for nothing.

### What replaced it: source purity

Round 4's miner records, for each template, the program or facility it mostly
came from. 3,346 of the 3,822 templates come from exactly one source, so the
label is clean for **88 %** of them. The question then answers itself from our
own data: **do a template's ten nearest neighbours come from the same program?**

**Macro, not micro.** One source, `infologger/ODC/ODC`, owns 2,180 of the 3,822
templates — 57 %. Averaging over templates makes the number mostly that source
scoring against itself, and every model lands near 0.76. Averaging over
**sources** gives each program one vote, and the null drops from 0.344 to
**0.035**. Only the macro column separates the rungs at all.

Sources with fewer than 10 templates are excluded, and the `stdout/unknown`
bucket — 418 templates whose program name the path regex failed to read — is
excluded too. 23 sources remain.

🔴 **One correction.** An earlier note in this round's working reported the null
as 0.161 and a purity of 0.408 as "2.5× the null". That null was computed as a
sum of squared source shares, which is the micro formula, not the macro one, and
it came out an order of magnitude too high. The macro null is **0.035** and the
models beat it by **eleven to twelve times**, not 2.5. The purity figures
themselves never depended on the null and did not change.

---

## The int8 result, stated carefully

The plan asks for int8 dynamic quantization "measured for speed *and* recall
loss against the float baseline". Both were measured and the speed half is a
warning:

| | fp32 | int8 |
|---|---:|---:|
| Templates per core-second | 581 | **107** |
| Macro source purity | 0.414 | 0.417 |
| Agreement with its own fp32 self | — | 0.636 |

**Quality survives quantization; throughput does not.** Dynamic quantization on
a 6-layer, 384-dimension model has small matrices, and the per-operation quantize
and dequantize around each one costs more than the narrower multiply saves.

**This is measured on one backend on one architecture.** Apple silicon offers
only `qnnpack`; the EPN nodes are x86 and would use `fbgemm`, where the balance
may differ. **Do not carry this number to the farm — carry the instruction to
measure it there before assuming int8 helps.**

---

## What this round did not do

1. **The domain FastText rung was not run.** The plan wants a FastText trained on
   our own templates, on the strength of `docs/RESEARCH.md`'s finding that a
   domain model beat LLM embeddings 0.766 to 0.257 Micro-F1 on an incident
   corpus. Training it properly means training over the 45.6-million-line corpus,
   not over 3,822 templates, and that is its own piece of work. **It remains the
   most interesting untested rung**, and the one with a real chance of beating
   everything above on our text specifically.
2. **The late-interaction stretch arm was not run.** It is a reranking device and
   its cost lands on search rather than on the worker, so it does not compete
   with anything measured here.
3. **Batch size and thread count were not swept.** The plan asks for batch size
   against intra-op thread count. Everything here ran at batch 64 on one thread,
   because at one template a second the batch is one and the sweep would describe
   a load this workload never reaches.
4. **Recall against a labelled retrieval task was not measured**, because no
   labelled task exists for this data. Source purity is a proxy chosen for being
   answerable, and it is a proxy.
