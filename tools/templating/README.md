# templating

Round 4 of `docs/SOAK_PLAN.md`: price log templating, and find out how fast new
templates arrive in real ALICE data. Results are in `docs/TEMPLATING_RESULTS.md`.

Two tools. `corpus.py` fetches real log text out of the CERN S3 archive and
flattens it; `drainbench.py` mines templates from that corpus and prices the
work in processor time.

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
