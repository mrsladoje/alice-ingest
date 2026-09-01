# epnsurvey

Two tools that close the Item 1 gate in `docs/THANASIS_PLAN.md`. Results are in
`docs/LOG_TYPES.md`.

## survey.sh

Read-only capture, run on one EPN node. Writes a `tar.gz` bundle and prints its
path on stdout.

```bash
scp tools/epnsurvey/survey.sh epn146:/tmp/survey.sh
ssh epn146 'sudo bash -c "OUT_ROOT=/tmp/epnsurvey bash /tmp/survey.sh"'
scp epn146:/tmp/epnsurvey/epnsurvey-epn146-*.tar.gz .
```

`sudo` will not accept `OUT_ROOT=` on its own command line on these nodes, so
the assignment goes inside `bash -c`.

Environment overrides: `OUT_ROOT`, `JL_ROOT`, `VARLOG`, `SAMPLE_LINES`,
`SAMPLE_FILES_PER_PROGRAM`, `JOURNAL_SINCE`, `JOURNAL_MAX`, `KERNEL_MAX`.

Bundle contents:

| Path | What |
|---|---|
| `meta.tsv` | node, capture time, kernel, release, cores, memory |
| `mounts.txt` | `df -hT` and every network filesystem |
| `units.txt` | systemd units, journal disk usage, every `_SYSTEMD_UNIT` and `SYSLOG_IDENTIFIER` |
| `journal/journal.json` | `journalctl -o json`, bounded |
| `journal/kernel.json` | `journalctl -k -o json`, bounded |
| `jl-inventory.tsv` | every `*.log` under the job-log root: path, size, mtime |
| `varlog-inventory.tsv` | the same for `/var/log` |
| `programs.tsv` | program, file count, total bytes |
| `one-run-listing.txt` | full `ls -la` of one run directory, so non-log files are visible |
| `samples/` | line samples per program and per `/var/log` file |

A bundle from `epn146` with the defaults is about 5 MB.

## regex_report.py

Scores a Fluent Bit classic parser file against a bundle. Reports per-program
coverage and per-parser hit counts, and names the parsers that score zero.

```bash
python3 tools/epnsurvey/regex_report.py <bundle-dir> \
    --parsers thanasis/logstack/config/fluent-bit-worker/parsers.yml \
    --json report.json
```

It rewrites Oniguruma `(?<name>...)` groups into Python `(?P<name>...)` before
compiling, and reports any parser that will not compile rather than stopping.

Rerun it after each parser is added. A parser that scores zero on real data does
not get ported.
