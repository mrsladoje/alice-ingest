# tools/collector

Three instruments for the collector's parsing and routing. None of them holds a
copy of the configuration: each reads what the production renderer emits from
`deploy/roles/loggy_collector/templates/`, so a parser added there is picked up here
without an edit, and a parser removed there stops being measured.

## `replaycheck.py` — does the shipped configuration do what it claims

Runs real Fluent Bit, in Docker, at a named version, over the fixtures in
`fixtures/`, and checks `fixtures/expect.yaml`.

The outputs are swapped for the file sink, one file per output match, so **which
file a record lands in is the routing assertion** and the JSON inside it is the
field assertion. The InfoLogger records are spoken over TCP, the way
`infoLoggerD` speaks them. The daemon log is mounted at its real absolute path,
because that is the path the production input names.

```
python3 tools/collector/replaycheck.py --version 4.0.1 --version 4.0.14 --restart
```

The farm does not run one Fluent Bit version. The 5 September 2026 census found
**four** deployed: 3.2.8 on the storage node, 4.0.1 on two workers, 4.0.14 on the
third, and the deploy role installs 5.0.8. Pass every one of them; a pass on one
proves nothing about the others, and this check has already caught a property
4.0.1 and 4.0.14 accept that 5.0.8 refuses to start on. `--restart` stops the
collector, appends a line, and starts it again on the same storage tree, which
is what checks that nothing ships twice and that a line written during an outage
still arrives.

`--dump` prints every record instead of checking, which is the way to find out
what a new fixture actually produces before writing its expectations.

`--journal <dir>` runs the `systemd` input over a captured journal directory.
The journal is the one source that cannot be fixtured — binary files, at least
eight megabytes each, belonging to the machine that wrote them — so the input
takes a `path` property, unset in production, that points it at a capture.

Capture one with:

```
ssh epn146 'sudo find /var/log/journal -name "system*.journal"'
ssh epn146 "sudo cat <path>" > <dir>/<machine-id>/system.journal
```

epn146's own journal gives 75,243 records, and the kernel traces in it name
`TfBuilder` — the IOMMU correlation the whole source exists for, which no
OpenStack machine can produce.

## `coverage.py` — how much of a real corpus the parsers classify

Scores the cascade over a corpus written by `tools/templating/corpus.py`.

```
python3 tools/collector/coverage.py corpus.tsv --family stdout --multiline
```

Two numbers come out and only one of them means anything. The match rate is
always 100 %, because the last parser in the cascade has every group optional
and therefore matches everything. **The number that decides whether a line can
be routed, charted or alerted on is whether a severity came out of it**, and
that is what the gate reads.

`--multiline` folds continuations first, the way the tail input does before any
parser runs. Without it the O2PDPSuite module banner is counted as thousands of
lines with no severity, and the figure is 1.6 points too low.

The engine here is Python's `re`, not Onigmo. The patterns use no construct
where the two differ, and `replaycheck.py` runs the same cascade through real
Fluent Bit on fixtures covering every shape counted here. This is the coverage
instrument; Fluent Bit is the authority.

## `mappingcheck.py` — will OpenSearch accept what the collector emits

`replaycheck.py` proves the collector emits the right fields. It says nothing
about whether OpenSearch will keep them, and both failure modes there are quiet
in different ways.

The application indices are `dynamic: false`, so a field nobody mapped is stored
in `_source` and **never searchable** — present in a document view, unfindable by
a query. The InfoLogger index is `dynamic: "strict"`, so a field nobody mapped
**rejects the whole document**. The ingest pipeline sets fields the collector
never emits, which is how a strict rejection arrives through a change to a file
that is not the mapping.

```
docker run -d --name ostest -p 9299:9200 -e discovery.type=single-node \
  -e DISABLE_SECURITY_PLUGIN=true opensearchproject/opensearch:3.7.0
python3 tools/collector/mappingcheck.py
```

It builds the pipeline and the indices from the real bootstrap template, indexes
the records `replaycheck.py` actually produced, and checks every field survived
and is searchable.

The first run of this check found that the bootstrap script would not have run
at all — a heredoc opener had lost its closing quote and the JSON validator had
skipped the malformed block in silence.

## `retrycheck.py` — what a retry writes, through the real output

```
python3 tools/collector/retrycheck.py
python3 tools/collector/retrycheck.py --version 4.0.14 --seconds 200
```

`replaycheck.py` writes to a file, and a file sink has no acknowledgement to
lose. It never retries, so a configuration that duplicates every record on retry
passes it green. This is the check for the property that rig cannot reach.

It stands `retryproxy.py` between Fluent Bit and a real OpenSearch. The proxy
forwards a bulk request, waits for the whole upstream response so the write is
committed, and then answers **503**: the cluster holds the records and the
client has been told it does not. Fluent Bit retries the chunk, and whether that
becomes one document or two is decided by whether the record carries a document
identifier of its own.

Two arms, both needed. The real one asserts the cluster holds one document per
distinct record. The control removes `id_key` from the rendered configuration
and nothing else, and must duplicate — a test that only ever passes proves
nothing about what it is testing.

It refuses to pass a run in which no record was attempted twice, and says so.
Waiting for the document count to go quiet is not the same as waiting for the
fault: Fluent Bit backs its retries off, so the quiet arrives first.

Both arms are compared against an **independent expectation** — the same
fixtures through the same rendered configuration with a file sink, produced
without a cluster, without a proxy and without a retry — per destination index.
Comparing the cluster against the identifiers the proxy watched go past was the
first version, and a record lost before the proxy saw it was missing from both
sides at once. Per destination rather than in total, because a misrouted record
is neither lost nor duplicated and a total would not notice it.

A **journal arm** runs the same fault over a real captured journal, with the
fixture tree mounted empty so every record came through the journal filters.
That is the one source whose records are rewritten by an allowlist, and an
allowlist is a deny list for everything unnamed: it dropped the identifier once
already. `--journal` points at a directory of journal files and the arm is
skipped, with a note, when there are none. Independently of it, the run asserts
statically that every allowlist in the rendered configuration keeps `doc_id`,
which needs no journal and covers the next allowlist somebody adds.

`test_retrycheck.py` tests both of those without containers: a checker fed one
record where four were emitted, a record in the wrong index, and an allowlist
ended four different ways.

The listing every count comes from is keyed by **index and identifier**, not by
identifier alone. The collector assigns one identifier per record and writes it
to whichever index the record is routed to, so `_id` is unique only within an
index: a record that reached two destinations — one of the duplications this
check exists to find — collapsed into a single entry and the listing came back
one short. Short in both arms at once, so the comparison between them still
balanced.

It also refuses to read a request that could not answer as an answer of nothing.
A search that timed out is reported with 200 OK and whatever hits it had
collected; an empty page from a failed shard is byte-identical to an empty page
from an index that is empty; and a refused connection arrives here as `(0, {})`,
which is below every `>= 300` test and reads as an empty result too. All three
now raise, because there is no useful partial answer: every count in the file
comes from this one listing. A 404 is different and stays an answer — a
destination that received nothing does not exist. So does `successful < total`
on a refresh, which is the shipped single-node layout and not a fault.

It feeds the tcp input as well as the files. The InfoLogger mapping is
`dynamic: "strict"` and the output leaves the identifier in the document body as
well as using it as the `_id`, so an unmapped `doc_id` rejects every InfoLogger
record outright — and only this arm would see that.

## `realcheck.py` — the shipped configuration over real traffic

```
python3 tools/collector/realcheck.py corpus.tsv --family dpl --lines 200000
```

`replaycheck.py` runs fixtures; `coverage.py` runs millions of lines through
Python. This runs real lines through **Fluent Bit**, with the production tail
patterns, and reports severity recovery, routing, and what the extractors
actually pulled out.

It also compares the two instruments. If Onigmo and Python disagree by more than
a point about which lines carry a severity, it fails — because every coverage
figure in `docs/SOAK_RESULTS.md` would then rest on the wrong engine. Measured:
0.31 points on `dpl`, 0.03 on `dds`, 0.00 on `datadist`.

`infologger` is out of reach here: it arrives over TCP, not from a file.

## `regexbench.py` — what the regexes cost, in the engine that runs them

```
python3 tools/collector/regexbench.py --corpus fam-stdout.txt --family stdout
```

One container, one core, a null sink, real log lines. The run ends when Fluent
Bit's own metrics say every record has been read, so the figure is processing
time rather than the length of a sleep.

**Always include the `control` arm.** It is byte-for-byte the shipped
configuration under a second name, so whatever gap it shows against `shipped` is
the instrument and the host. Nothing smaller anywhere else in the table means
anything. On the machine this was written on, that gap is 0.8 % and it is the
largest difference in the stdout table.

**Arms are interleaved**, one round of each in turn, and the **minimum** is the
figure to read. The host alternates between two speeds about 20 % apart, on the
timescale of a single arm. Running arms in blocks turns that into a difference
that is not there — it produced a 17 % regex "cost" that vanished on
re-measurement. The median cannot see past it either: over eight rounds the
median spread across five arms within 1 % of each other was 19 %, and the two
extremes were the two identical configurations. The minimum spread was 1 %.

`--input-form replay` puts a full event date in front of every record-start
line, the way the replay engine does. `--input-form live` is what a tail on a
real EPN sees. The same configuration is measured against both, because the
optional leading date is only free if it is free when it never participates.

Round 6's masker findings do not transfer here. Those were measured against
Python's `re`, where a pattern is fast when its first opcode is a literal.
Fluent Bit uses Onigmo, with its own optimiser. A collector regex is cheap or
expensive only as measured here.
