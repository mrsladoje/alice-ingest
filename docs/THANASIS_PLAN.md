# THANASIS_PLAN

What we port from Thanasis's `logstack` into `alice-ingest`, and how.

Source of the comparison: the architecture audit artifact, reviewed 30 Jul 2026.
Their repository is vendored at `thanasis/logstack/`.

Rejected items are not written here. If a thing is absent from this file, we
looked at it and we are not doing it.

## The grounding rule

Thanasis had a live Run 3 stream from the EPN farm. We do not. Run 3 is
finished. We have only the old logs in the S3 bucket, which we replay
synthetically. Lubos may have given us a subset of what exists.

So no item enters this plan on merit alone. Each item must also be justified by
data we can actually replay. If the source data is absent, the item is dropped.

## Status key

- **TAKE** — port it, in the form written here.
- **ADAPT** — take the idea, not their implementation.
- **RECORD** — no build work; write it into the deviations list as a known trade.

---

## Rules that apply to every port

### R1 — Rewrite every ported `Time_Format`

Their parsers use `Time_Format %H:%M:%S`, with no date part. Fluent Bit then
fills in the current date. Any log line that crosses midnight, or that is
replayed on a different day, gets a wrong date and no warning. Our S3 replay
does exactly that, so the fault would be certain, not merely possible.

Every parser we port must read a full date, or read an epoch, before it enters
`deploy/roles/collector/templates/parsers.yaml.j2`. Our three current parsers
already do this; ported ones must match.

---

## Items
