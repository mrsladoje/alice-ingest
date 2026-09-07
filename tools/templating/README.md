# templating

Round 4 of `docs/SOAK_PLAN.md`: price log templating, and find out how fast new
templates arrive in real ALICE data. Results are in `docs/TEMPLATING_RESULTS.md`.

Four tools. `corpus.py` fetches real log text out of the CERN S3 archive and
flattens it; `refamily.py` relabels that corpus by format family;
`drainbench.py` mines templates and prices the work in processor time; and
`recipesweep.py` sweeps one family's recipe to find out which settings earn
their cost.

The collection family and the format family are not the same thing, and
conflating them was a real error. `corpus.py` labels a line by where it was read
from, so everything under the process-log tree is `stdout`. The tree holds two
line formats. `refamily.py` splits it using the collector's own parser cascade,
so the split is whatever the shipped configuration says it is.

## corpus.py

Reads `epn-backup-logs` directly over HTTPS with the `cern_s3` profile — no
lxplus hop, no Kerberos ticket. It reuses `images/replay/replay.py`'s InfoLogger
row parser rather than carrying a second copy of it.

```bash
AWS_PROFILE=cern_s3 python3 tools/templating/corpus.py \
    --out corpus.tsv --families infologger,dds,stdout \
    --il-objects 40 --il-stride 4 --tar-objects 4
```

Output is one record per line, tab separated: family, source, message. Tabs and
newlines inside a message are escaped, so one corpus line is always one record.
A message broken across two lines would be mined as two templates that never
repeat again, which would inflate the one number this round exists to measure.

`--il-stride` samples the InfoLogger objects evenly across the archive rather
than taking the first N. The objects are one MySQL partition each and the
partitions are ordered, so the first N are one slice of time, not a sample.

| Family | Source column | Where it comes from |
|---|---|---|
| `infologger` | `system/facility` | `infologger-2026/*.sql.tar.gz`, a mysqldump of the InfoLogger table |
| `dds` | host | the `dds_*.log` firehose inside each run tarball |
| `stdout` | program name | the `*_{out,err}.log` members of the same tarball, capped per member |

## refamily.py

```bash
python3 tools/templating/refamily.py corpus.tsv --out corpus-formats.tsv
```

Rewrites the family column of every `stdout` line to the format family the
collector's cascade assigns it: `dpl` or `datadist`. The mapping is read out of
the rendered production configuration, not restated here, so a parser added to
the collector splits the corpus the same way with no edit to this tool.

## recipesweep.py

```bash
python3 tools/templating/recipesweep.py corpus-formats.tsv --family datadist
```

Sweeps the four knobs round 6 found actually move — padded separators, tree
depth, similarity threshold, and whether numeric tokens are parametrised — and
reports four numbers per cell: templates, core-seconds per million, words kept,
and the share of templates that are nothing but wildcards.

Round 6's sweeps lived in a gitignored scratch directory and cannot be re-run.
This one ships, which is the point of it.

## drainbench.py

```bash
python3 tools/templating/drainbench.py corpus.tsv \
    --block 100000 --split-cost 200000 --json report.json
```

- `--block N` reports the template count every N lines. **Read the blocks, not
  the total.** The rate over a whole corpus is dominated by the first few
  thousand lines and says nothing about the steady state, which is the only part
  that decides whether round 3 is affordable.
- `--split-cost N` prices masking apart from the tree over the first N lines.
- `--family`, `--sim-threshold`, `--depth`, `--max-children` restrict or retune.

`loop_core_seconds_per_million` is reported only when no `--family` filter is
set. The loop reads every corpus line whichever family is asked for, so dividing
the whole loop's cost by one family's lines bills that family for reading the
others — on a small family that inflates the figure several times over.
`mining_core_seconds_per_million` is timed around the miner alone and is always
the honest one.

Both cost figures are `time.process_time`, so they are processor seconds and
compare directly with the core-seconds per million in `docs/SOAK_RESULTS.md`.
They exclude transport: a real sidecar also pays for the Fluent Bit output that
feeds it and the write back to OpenSearch, and that part needs the cgroup rig.

`recipe_tokens(family, message)` is the per-line path: strip the envelope,
mask, pad the family's separators, and split. `mine(tm, tokens)` adds the
result to a recipe miner and returns its cluster, straight to the Drain rather
than through `TemplateMiner.add_log_message`, which would mask the line a
second time, split the string again, and serialise the tree when a persistence
handler is attached. `recipe_prepare` still returns the same line as one
string for the callers that want text. `install_merged_create_template()`
installs the FLOAT/NUM merge together with rewrites of drain3's similarity and
best-match loops and the `add_tokens` method `mine` calls; all four return what
drain3 0.9.11's own methods return, checked on the cluster of every line of
every family, and together they halve the tree's cost. Round 18 of
`docs/SOAK_RESULTS.md` has the figures, and `downloads/prepare-opt/` the
harness that produced them.

## piplupbench.py

Stage H of `docs/SOAK_PLAN.md`: PIPLUP against Drain3 on the same corpus, read
off the same instrument. Every field it reports has the same name and the same
definition as the drainbench field beside it.

PIPLUP is not on PyPI. Clone the replication package first and point the tool at
it:

```bash
git clone --depth 1 \
    https://github.com/mooselab/PIPLUP-A-Configuration-Free-Statistic-Based-Log-Parser.git piplup
python3 tools/templating/piplupbench.py corpus.tsv --piplup piplup \
    --block 500000 --json report.json --dump-templates templates.tsv
```

`PIPLUP_HOME` works instead of `--piplup`. Needs `regex`, `pandas`, `numpy`,
`scipy`, `tqdm` and `nltk` — the last three only because the package's
`logparser/__init__.py` imports its LogHub evaluator on the way in.

**The released code is a batch parser and cannot run this corpus.** It reads a
whole log file into a pandas frame, keeps every line's identifier inside its
cluster, and writes a structured CSV at the end. Clustering and cluster updating
are genuinely online, so this tool drives those two directly, one message at a
time, and keeps a cluster's own `hit_time` instead of its line list. The
algorithm is untouched; only the file handling around it is replaced.

Defaults are PIPLUP's own published ones — `br_thresh` 2, `hit_limit` 385,
similarity threshold `default`, merging and preprocessing both on. That is the
point of the parser, so `--br-thresh`, `--hit-limit`, `--sim-thresh`,
`--no-merge` and `--no-preprocess` exist for ablation and are not swept here.

**Two counts come out, and they are not the same number.** `clusters` is how many
clusters the tree holds. `templates` is how many distinct template strings those
clusters carry, which is what compares with Drain3's count: a cluster may hold up
to `br_thresh` of them, and the merge step lets two clusters share one.
`clusters_with_two_templates` says how far apart the two counts sit. The dumped
file is one row per cluster and carries the cluster's first template.

## masking.py

The masker, twice. `REFERENCE` is the eight rules as `drain3` consumes them and
`reference_mask` runs them the way `drain3` does, eight `re.sub` passes in order.
That pair is the definition of correct. `mask` is a rewrite that returns the same
string 85 to 87 % faster, which took the whole templating cost down 2.3 times in
round 6.

`drainbench.py` binds `mask` onto the miner and leaves the instruction list in
place, because `drain3` still needs it to extract parameters.

The rewrite is a set of local identities on the regexes. The change that pays is
moving each pattern's first literal in front of its assertion, because `re` uses
its character-skip loop only when the first opcode is a literal or a class:

```
before  ((?<=[^A-Za-z0-9])|^)(/[-\w./]+)((?=[^A-Za-z0-9])|$)
after   /(?<![A-Za-z0-9]/)[-A-Za-z0-9_./]+(?![A-Za-z0-9])
```

`_numbers` folds FLOAT and NUM into one scan and **reproduces a quirk of the
two-pass reference on purpose**: FLOAT runs over the whole line before NUM does,
so `1.5-3` masks to `<FLOAT><NUM>` and not `<FLOAT>-<NUM>`. Byte-identical output
is the requirement, not better output — every template identifier is derived from
this string, so a masker that improved the quirk would renumber the corpus.

FLOAT and NUM also allow the number a unit — `42.797s`, `176000ms`, `5MB` — which
they assert in a lookahead rather than consume, so the mask replaces the digits
and the unit stays in the template. It is part of those two rules and not a rule
of its own, because a separate unit pass cost 1.5 to 2.7 microseconds a line and
could not be made byte-identical: running before the plain FLOAT pass it takes
`5.7` out of `1.5.7ms`, where one left-to-right scan takes `1.5` first.

**Change this file only against the harness.** `downloads/round6/maskopt.py`
takes a candidate exposing `mask(line)`, compares it with the reference line by
line, fails on the first three differences, and only times a candidate that
passed. A candidate that is faster and differs on one line in a million is
rejected.
