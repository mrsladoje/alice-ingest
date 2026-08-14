# Episodes and alerting

Anomaly detection ends the moment a monitor fires. This is what happens
after. 

One service (long-running Python process under systemd), **`alice-signal-projector`**, reads every alert and every
detector score, writes each known row as a **signal**, and groups the ones
that are *the same trouble* into **episodes**. Alertmanager then decides who
gets told.

The split is the whole point. **The projector is the judge. The indexes are
the record. Alertmanager is only the messenger.**

- **`alice-signals`** — what happened. Each observation - not deduped, kept for history.
- **`alice-incidents`** — what is wrong right now. Each document is one
  episode, not the identity; `incident_id` is just a field on it. Patient
  file plus this hospital stay.
- **Alertmanager** — when someone is told. It stores nothing durable and
  never becomes the database.

---

## Why anything is needed here

OpenSearch Alerting has no memory. A collector that has been dead for an hour
produces the same alert every minute, thirty times over, each one looking
brand new. Nothing in that stream says:

- these thirty rows are **one** fault, not thirty;
- the fault **started** at 09:14 and **ended** at 09:41;
- forty EPNs breaching one rule are forty **separate** problems, because
  epn034 can recover while epn159 is still bad;
- the detector stopped reporting, which is **not** the same as recovered.

The projector answers all four. Everything below is that one job.

---

## Three words

Incident is identity. Episode is identity plus this visit's time.

| Word | What it is | Example | Where it lives |
| --- | --- | --- | --- |
| **Signal** | One observation: this monitor fired, or this detector scored high, at this time, on this thing. One row. | `collector-down` fired on `node-01` at 09:15. The next minute is another signal. Thirty of these can still be one episode. | `alice-signals` |
| **Incident** | *Which* problem on *which* thing. A name that never changes. It exists even when nothing is wrong right now. Patient file. | `collector-down` on `node-01` — the same key this week and next month. | `incident_id` field |
| **Episode** | That problem *happening once*: the incident plus this visit's start (and later its end). Hospital stay. | Same incident, opened 09:14, closed 09:41. Next week it can open again as a new episode. | `alice-incidents` (the index name is leftover; each document is one episode) |

`incident_id` is a hash of five things: source kind, alert name, entity kind,
entity id, and scope. Collector `node-01` failing `data-loss` always hashes to
the same key, this week and next month.

`episode_id` is `<incident_id>.<episode_start>`, where `episode_start` is the
**event time** the episode opened.

**Why event time and not a counter.** The projector re-reads the last fifteen
minutes of alert history every cycle. A counter would have to be read back
from whatever state happened to be loaded, so the same fault produced a new
incident document every thirty seconds, then reset to 1 once the old rows
aged out. An event-time key is the same key however often you re-read it.

---

## The loop

Every 30 seconds the projector does this:

1. Read **every** current alert, plus alerts that ended recently (15-minute
   overlap).
2. Read new detector results (3-minute overlap). Skip anything carrying
   `task_id` — that is a `make backtest` run and must never page.
3. Normalise each row into one signal shape: entity, severity, scope, and the
   collector that owned that entity **at the time of the event**.
4. Attach each row to an episode.
5. Write `alice-signals` and `alice-incidents`.
6. Push every open episode to Alertmanager.
7. Write a heartbeat into `cockpit-metrics`.

**Where it reads is deliberate.** Each monitor's own action posts JSON to an
in-cluster channel, which lands in `alice-alert-actions`. **Nothing reads that
index.** It is an action log; it exists so the 30-minute throttle has a
destination and so each fire is recorded independently. The projector reads
the alerting indices themselves. A notification that gets lost therefore
cannot lose an incident.

**A firing detector row is grade above 0.5, not 0.7.** The projector opens an
episode at 0.5 so the record starts early. `ad-high-grade` still needs 0.7 to
page. Record and notification have different thresholds on purpose.

---

## Every row must name a thing

`signal_catalog.json` declares, for each monitor and each detector: the entity
kind, where the entity comes from (`bucket_key` or `constant`), the
notification scope, and the feed. None of it is guessed from an index name.

The bucket key is nested inside the stored alert, under
`agg_alert_content.bucket_keys`. The flat top-level field of the same name
exists only in the template context an action renders — same name, different
shape. Reading the flat one made every bucket-level breach look keyless.

**An alert that names no entity is never a fleet-wide breach.** When the key
is missing, the projector blames the **monitor**, not the fleet:

| Class | Meaning |
| --- | --- |
| `monitor-error` | Alerting reported an execution error. The rule is not judging anything. |
| `entity-missing` | The rule is declared per entity, but its alert carried no key. |

The card then reads "monitor X cannot run", which is true and actionable. The
old behaviour used a `none` placeholder, which hashed into `incident_id` and
folded every machine's breach into one anonymous incident reading "whole
fleet" — the exact mirror of the fan-out this lane exists to prevent.

---

## Opening and closing an episode

**Rows join an episode by time boundary, never by "whichever episode is open
now."** The projector loads the incident's episode timeline and picks the
episode whose `episode_start` is the latest one not after the row's own event
time. Attaching to the newest open episode instead would restamp an old
breach onto a new one every time the overlap re-read it — silently, because
signal ids are deterministic.

An episode closes only after **K consecutive healthy windows**:

| Source | Healthy windows to close |
| --- | --- |
| Cliff monitors (`collector-down`, `cluster-red`, disk, heap, …) | 1 |
| Trend monitors and `ad-high-grade` | 3 |
| One-minute detectors | 3 (grade back at 0) |
| Thirty-minute `-slow` detectors | 2 |

**Recovery is idempotent.** Each window is counted by its own timestamp, and
`last_healthy_window` / `last_breach_window` refuse to count the same window
twice. Without that, the re-read overlap alone would manufacture the healthy
windows and resolve a fault that never stopped.

Five states:

| State | Meaning |
| --- | --- |
| `OPEN` | Currently breaching. |
| `RECOVERING` | Some healthy windows counted, not yet enough. Still reported as firing. |
| `RESOLVED` | Enough healthy windows. Terminal. |
| `STALE` | The detector stopped reporting at all. |
| `CLOSED_EXPECTED` | The thing it watched went away. Terminal. |

**`STALE` is the important one.** Every detector declares its expected
interval and how late a result may be. Past that budget, the projector marks
the episode `STALE` and keeps it **firing**. A missing evaluation is not a
recovery. Silence is exactly how a broken detector looks from the outside.

`CLOSED_EXPECTED` covers the opposite case: a collector that left the roster,
or an episode built on the placeholder entity. Left open, either would re-send
to Alertmanager forever and hold a card that names nobody.

---

## One card is one notification

An episode is **per entity**, and that is not negotiable: one state machine
cannot hold two answers, so forty breaching EPNs are forty episodes. But forty
cards on a board that shows twenty is a fan-out that hides every other
problem.

So the cockpit board folds episodes on `group_id`, built from **the same
fields Alertmanager groups on**: cluster, alert name, and — on the collector
route — the notification scope. One card is then exactly one message an
operator would receive. The card names the affected count and samples the
first three entities; **SIGNALS** opens the underlying rows, **DETAILS**
opens one row per entity.

Folding is display only. `incident_id` is unchanged, per-entity recovery is
unchanged, and the rule that no incident may mix entities still holds — which
it would not if the episodes themselves were merged.

---

## Alertmanager: notification semantics only

The projector re-sends every open episode every 30 seconds. Alertmanager's
`resolve_timeout` is 5 minutes, so a live fault is refreshed roughly ten times
inside its own timeout. The projector **refuses to start** if that cadence is
not comfortably inside the timeout — otherwise Alertmanager would resolve live
alerts between cycles.

Routing tiers on `severity` alone, which the projector already stamps:

```
route  group_by [cluster_id, alertname]        5 m wait / 10 m interval / 4 h repeat
├── notification_scope =~ "collector:.+"       + notification_scope in group_by
│   └── severity = "page"                      30 s / 2 m
└── severity = "page"                          30 s / 2 m
```

A dead Fluent Bit reaches a human in about 2.5 minutes. The ten trend
monitors, which run on a ten-minute schedule, batch into one message instead
of ten.

**Inhibition ships off.** Alertmanager can mute a symptom while its cause is
firing. Three rule bodies are written and gated, and the rule list is empty,
so nothing is muted today. A rule pointing the wrong way mutes the alert you
needed — `data-loss` is usually an impact, not a cause, and disk pressure can
*produce* a red cluster. Each rule is enabled only after `make inject` has
measured its direction, its delay, and a false-inhibition score of zero.

**Before maintenance, place a silence.** Alertmanager sits behind the same
vhost at `/alertmanager/`. A deploy, a `make replay-fresh`, or a press of the
ops page's clear button all produce expected alerts. Mute them deliberately
rather than ignore them by habit.

---

## The record of what was sent

Alertmanager posts each notification to `alice-notification-ingest`, which
writes one document into `alice-notifications`. Each carries the `episode_id`
and `signal_id` values it covered, so a page can be joined back to the rows
that caused it.

**Break glass.** `signal-projector-stale` and `alertmanager-down` cannot page
*through* the components they report dead. Those two, and only those two, post
straight to that receiver, tagged `delivery_path: breakglass`. Any other alert
name on that path fails an injection run.

**Not implemented:** attributing a suppression to the rule that caused it.
`suppressed_by` and `suppressed_count` are written as placeholder and zero,
and nothing fills them. To read a suppression today, compare the incident's
members against what the notification covered.

---

## Two extras on the card

**Candidate causes.** When a symptom episode and a cause episode are firing in
the same scope, the card lists the cause by name and combines the per-edge
probabilities into one belief. Unmeasured edges use a prior of 0.5 and are
marked as unmeasured. This only labels the card. It mutes nothing — muting
needs a proven edge, and that needs injection evidence.

**Mass silence pages.** Fleet-wide quiet cannot by itself distinguish "the run
ended" from "the farm is gone", so the projector classes it
`unknown-mass-silence` and it pages. Only authoritative run-state telemetry
may downgrade it, and that telemetry does not exist yet.

---

## The whole path

```
.opendistro-alerting-alerts (+ history)  ─┐
.opendistro-anomaly-results*             ─┤   read every 30 s
cockpit-fleet roster snapshots           ─┘
                 │
                 ▼
        alice-signal-projector      ← identity, episodes, recovery, grouping
          │              │
          ▼              ▼
    alice-signals   alice-incidents  ← the durable record; this is what is true
                         │
                         ▼ re-send every open episode, well inside resolve_timeout
                   Alertmanager      ← grouping, tiers, silences; who gets told
                         │
                         ▼
              alice-notifications    ← what was actually delivered
```

Read it as three questions with three owners. *What happened?*
`alice-signals`. *What is wrong right now?* `alice-incidents`. *Who was
told?* `alice-notifications`.
