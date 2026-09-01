# `shifterdev`

Runs the shifter log view on your own machine, against invented data, so the
buttons can be pressed without a farm, a cluster or a tunnel.

```
python3 tools/shifterdev/run.py
```

Then open **http://127.0.0.1:8092/**. Ctrl-C stops everything.

Nothing is installed and nothing is written into the repository. The page and
the server are the real files from `deploy/roles/shifter`; only the data
underneath them is fake.

## What it starts

| Process | Port | What it is |
|---|---|---|
| `mock_opensearch.py` | 9209 | Answers `_search` over 60 000 invented records spread across the last 24 hours, plus everything the feeder has sent since, so a query for the last hour always finds rows however long the stack has been up. It implements the subset of the query language `shifter.py` emits — `bool`, `terms`, `range`, `wildcard`, `regexp`, `match_phrase` — and nothing else. |
| `shifter.py` | 8092 | The real shifter server, unmodified, pointed at the mock cluster. Serves the real page from a temporary directory. |
| `check.py` | — | Drives the real page in headless Chrome and asserts 135 behaviours: the range presets, the CERN date parser and its format menu, the timezone conversion, the calendar's six ways of closing, the exclude window, the approximate count, paging, `Clear`, the error-jump arrows, every toolbar control, saved presets, the inspector's own field list, the record panel that opens under a row (including the flip above the last row), the phone-width layout, the shareable link (its compact encoding, the per-preset link button, the clipboard fallback when the browser blocks the API, the toast, and the round trip back into the filter boxes), the live dock, the same record view opened from a live-lane row, and the inspector's move into the dock when the lane fills the screen. It also fails the run on any uncaught JavaScript error. Exit 0 means every one passed. Run it before and after any change to `shifter.js`. |
| `feeder.py` | 8093 | Pushes invented records into `POST /ingest` at 12 a second, and bursts to 900 a second for 12 seconds every 90 seconds. Every record also goes into the mock cluster, the way a real collector writes to both the lane and OpenSearch. |

## Driving a burst yourself

```
curl http://127.0.0.1:8093/burst    # 900 records/s for 12 s
curl http://127.0.0.1:8093/calm     # stop it early
curl http://127.0.0.1:8092/healthz  # viewers, received, dropped_slow_client
```

A burst is the interesting case. Watch the `N new — show newest` counter climb
while the table stays still, then press it. Turn `Autoscroll` on to see the
other behaviour.

## The invented data

`corpus.py` is shaped after what the real EPN farm actually logs, read off
`alice-epn.cern.ch:8082` on 28 August 2026: `QC`, `ODC`, `ECS`, `Readout` and
`Monitoring` systems, `qc-task-<detector>-<stage>` role names, the fifteen
detector codes, run numbers in the 567 900 range, and the message templates that
dominate a real week — `Could not find the DPL InfoLogger`, `No URL provided for
Bookkeeping`, `Seen TFID equal to 0`. Severity is weighted the way the farm is:
about 88 percent info, 8 percent warning, 3 percent error.

It is invented, not captured. It is here to make the interface honest to use,
not to stand in for a measurement.

## Opening it on your phone

By default the lane binds to `127.0.0.1`, so only this machine can reach it.
To read it on a phone, bind to every interface:

```
SHIFTER_BIND=0.0.0.0 python3 tools/shifterdev/run.py
```

It then prints the address to type into the phone. Both devices have to be on
the same network.

**Bind to `0.0.0.0` only while you are looking at it.** A CERN address is
routable, not private, so anyone on the network can open the page while it is
up. It is invented data on a throwaway port, but stop the harness when you are
done rather than leaving it running.

## Knobs

| Variable | Default | Meaning |
|---|---|---|
| `SHIFTER_PORT` | `8092` | Where the page is served. |
| `SHIFTER_BIND` | `127.0.0.1` | Interface to bind. `0.0.0.0` to reach it from a phone. |
| `MOCK_OS_ROWS` | `60000` | Records generated into the mock cluster at start. |
| `MOCK_OS_MAX_ROWS` | same as `MOCK_OS_ROWS` | Ceiling for the mock cluster. The feeder keeps adding rows, so the oldest are dropped past this. |
| `MOCK_OS_SPAN_SECONDS` | `86400` | How far back they run. |
| `FEED_CALM_RATE` | `12` | Records a second when nothing is happening. |
| `FEED_BURST_RATE` | `900` | Records a second during a burst. |
| `FEED_BURST_SECONDS` | `12` | How long one burst lasts. |
| `FEED_AUTO_BURST_EVERY` | `90` | Seconds between automatic bursts. `0` turns them off. |
