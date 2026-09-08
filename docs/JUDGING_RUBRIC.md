# Relevance rubric for ALICE template retrieval

**Who this is for:** anyone grading a query against a log template — an ALICE
operator, or a machine assessor standing in for one during development.

**What you are grading:** an operator asked a question. A retrieval system
returned one log template. You decide how well that template answers the
question.

You see the template, what wrote it, how often it occurs, and redacted examples
of real lines. You never see which system returned it, or what score it gave.

---

## The four grades

| Grade | Meaning |
|---|---|
| **3** | Directly answers the operator's need. |
| **2** | Provides strong supporting evidence. |
| **1** | Related, but not useful on its own. |
| **0** | Irrelevant, or misleading. |

Grades 2 and 3 count as relevant. Grade 1 does not.

### Grade 3 — it answers the question

The template reports the named condition **occurring or failing**. It is an
event, an error, a warning, or the state transition that *is* the condition.

An operator who searched this question and saw this line would stop searching.

Query `memory pressure` → `Free SHM memory too low` is grade 3.
Query `process died` → `Program has crashed with signal <NUM>` is grade 3.

### Grade 2 — it supports the answer

The template is evidence for the same incident, but it does not name the
condition. It is the symptom beside the cause, the retry that follows the
failure, or the state the failure left behind.

Query `network timeout` → `Reconnecting to <*> after <NUM> attempts` is grade 2.
Query `memory pressure` → `RegionAllocatorResource: waiting to allocate a
message` is grade 2: it is what memory pressure looks like, without saying so.

### Grade 1 — related, but not useful alone

The template shares the subject but reports no problem and no event. Routine
configuration, a normal startup banner, or a resource listing that mentions the
same thing.

Query `memory pressure` → `MemFree: <NUM> kB` is grade 1. It is about memory and
it reports nothing happening.
Query `disk full` → `Storage path set to <PATH>` is grade 1.

### Grade 0 — irrelevant or misleading

The template does not concern the condition at all, or it would send an operator
in the wrong direction.

A word match is not relevance. Query `authentication failed` → `Failed to
connect to remote host` is grade 0: the word "failed" is shared, the condition
is not.

A contentless template — one that is only wildcards and punctuation, such as
`<*> = <NUM>` — is always grade 0.

---

## Rules that decide the hard cases

1. **Judge the template, not the program.** A template from a program that
   sounds relevant is not relevant for that reason.
2. **A word in common is not evidence.** `failed`, `error` and `timeout` appear
   in thousands of unrelated lines.
3. **Configuration is not an event.** A line that sets, lists or reports a value
   is at most grade 1, unless it reports a rejection or a failure.
4. **Severity does not set the grade.** An `INFO` line that reports frames
   dropped is still grade 3. An `ERROR` about an unrelated condition is 0.
5. **Frequency does not set the grade.** A template seen once can be grade 3.
6. **If it depends on context you cannot see, write that in the note** and grade
   what is in front of you. Do not turn uncertainty into relevance.
7. **Grade each item on its own.** Do not compare it with the other candidates
   for the same query, and do not try to produce a particular number of
   relevant results.

---

## The note

Every grade carries one short sentence saying why. It exists so a disagreement
between two assessors can be read rather than guessed at.

---

## The agreement threshold, frozen before any grading

Two assessors judge an overlapping sample. Their agreement measures whether this
rubric is clear, not whether either of them is right.

**The minimum acceptable weighted Cohen's kappa is 0.60.** Below that, the
rubric is too vague to grade against, and it must be revised and the sample
re-judged before any model is scored on the result.

Raw agreement is reported beside it and is not the gate: with four ordered
grades, two assessors who differ by one grade everywhere would still look
reasonable on a raw count. Kappa with quadratic weights prices a two-grade
disagreement four times as heavily as a one-grade one, and prices agreement that
chance alone would produce at zero.

This threshold is written down before the first grade was read, and it applies
whoever the assessor is.
