# Round 3 reader answers

A fresh reader answered the twelve questions from the report text only.
Source: `reports/loggy-report/reviews/report-text.txt`.
Confidence values: sure, partly, not answerable from the text.

## 1. An info line from an O2 process: where it lands, how many copies, who reads it after the EPN dies

The info line stays on the worker that wrote it. Section 3.3 says the line takes the same road as an error line as far as the routing rule, then stops. Section 3.2 step 2 and Table 3 say info, debug and trace go to the local index `application-logs-local-<node>`, which has 1 shard, 0 replicas and 8 days of retention. A placement setting pins that index to the worker, so exactly one copy exists (Section 3.3). If the worker dies, its raw lines die with it, but the rollup rows, template counts and detector results survive. While the worker lives, one cluster search still finds the line, and that search is as slow as the slowest worker.

**Section:** 3.3, with Table 3 and Section 3.2. **Confidence:** sure.

## 2. A collector dies: what happens, and how long it takes

Section 3.7 walks this case. The collector is silent, so the platform reasons from absence: within 30 seconds the poller writes the collector's row with a missing flag. The collector-down monitor reads those rows every minute and fires at page severity. The projector opens an episode and posts it to Alertmanager, which groups on alert name and collector, waits 30 seconds, and posts the batch to the receiver. The cadences bound death-to-document at 90 to 270 seconds, because the first missing flag arrives 60 to 150 seconds after the death. Section 3.7 and Section 6.1 both say no run has timed this, and Section 3.6 says nothing pages a human today, so the end point is a stored notification.

**Section:** 3.7, with 3.6 and 6.1. **Confidence:** sure.

## 3. Why a queue between collector and store was rejected, and what reopens it

Section 5.3 says the soak rejected the queue on three counts. Decoupling: the stack sustains 538 times the busiest worker-second of 78 records, and 4.3 times the farm peak of 9,781 records a second. Durability: only InfoLogger is lost in an outage, and the collector's 256 MB disk buffer covers 17.4 hours at a median worker's rate. Cost: three brokers need memory the staging machines do not have, and the tier rule splits into two places (Section 4). Four conditions reopen it: a sustained worker rate above about 1,000 records a second, a rate within 50 times of 42,000, a second consumer, or an outage longer than the buffer holds.

**Section:** 5.3, with Section 4. **Confidence:** sure.

## 4. The two Kafka decisions, and which one is agreed

The two decisions are about two different places in the design. The first is a queue between the collector and the store, and Section 5.3 rejected it on measured numbers. The second is a bus for the live lane only, which Section 4 says the September 2026 rework agreed for decoupling and not for durability. The live lane is the second copy of every record that leaves the worker (Section 3.1). The bus is the agreed one, and it is not built: its brokers are not placed. Section 4 adds two rules for it: it must be Apache Kafka, because CERN requires fully open source software, and it must be the primary path, because the collector held at release 4.0.1 has no route for a failed output.

**Section:** 4 and 5.3, with 3.1 and 6.3. **Confidence:** sure.

## 5. How much of a worker the platform takes, and where the numbers come from

Section 2.3 says a worker gives the platform four physical cores and eight logical processors, out of 64 physical cores on a 2019 EPN. Section 3.1 sets the memory budget: a 384 MB cap on the collector and a 1 GB heap on the local search node. Section 5.2 measured collector memory between 133 and 228 MB in steady load, and 373 MB in a sink outage with the 256 MB buffer. The abstract says the collector costs about a quarter of one core at 20,000 records a second, and Section 5.1 gives 11.19 core-seconds per million records at that rate. Section 5.1 and Section 6.1 say every cost figure comes from a laptop rig, and two rigs exist that were never averaged. The stamper's own cost is not measured (Section 4).

**Section:** 2.3, 3.1, 5.1, 5.2, 6.1. **Confidence:** sure.

## 6. What the flush change from 5 seconds to 1 second changed

Table 6 in Section 5.2 gives four measures. Whole-stack cost fell 3.4 %, from 108.40 to 104.67 core-seconds per million records. The collector alone fell 24.5 %, from 36.70 to 27.70 core-seconds per million records. Collector peak memory fell 28.0 %, from 87.1 to 62.7 MB, and the live-lane latency floor fell from 5 seconds to 1 second. Section 5.2 says these come from three alternating runs at 5,000 records a second on the re-run rig, which is the same laptop slowed. Section 5.2 adds that eight flush values from 0.125 to 10 seconds make a U curve with its floor at 1 second, 17.9 % between best and worst.

**Section:** 5.2, Table 6, with 5.1. **Confidence:** sure.

## 7. What a template is, and what the stamper does

Section 3.1 defines a template as the skeleton of the line, with every variable token a wildcard. Section 3.5 adds that a template widens as the miner sees more lines, and each state is one version, which is the field a record carries. The stamper runs drain3: a masker replaces varying tokens with placeholders, then one tree per log family turns a literal that varies between lines into a wildcard. Each chunk of records crosses a local socket to the stamper and comes back with two fields, `template_version` and `template_status` (Section 3.2, step 3). The status is matched, new or no template, and an empty message or one over 4,096 characters is indexed in full with no template. Only stamped chunks reach the search outputs, and the stamper holds at most 20,000 templates per worker (Figure 4, Section 3.5).

**Section:** 3.5, with 3.1, 3.2 and Figure 4. **Confidence:** sure.

## 8. What is built, and what is agreed and not built

Section 6.3 separates three layouts. Built and running: staging of five machines, two workers and three storage machines, with the collector, stamper, local and central indices, projector, trend rollup, shifter view, live lane, Dashboards, Alertmanager, poller and receiver (Figure 7). The farm pilot is three workers and three containers on one infra machine, and one deployment passed every check; that pilot ran the cluster, the collectors and Dashboards only, so the stamper, projector and shifter view have run on staging only. Agreed and not built: the live-lane Kafka bus, with one topic and the shifter view as its one consumer, and the shared Python as one versioned package. Semantic retrieval is built, evaluated and shipped disabled by configuration (Section 5.6), and inhibition is built and ships off (Section 4). Production, the whole farm with three or more storage machines and the bus, is not real, and seven further items in Section 6.3 are open, not agreed.

**Section:** 6.3 and Figure 7, with 5.6. **Confidence:** sure.

## 9. What 0.685 means, and what it is compared against

Section 5.6 says 0.685 is a search quality score: normalised discounted cumulative gain at rank ten, written nDCG@10, which rewards relevant results near the top. It is the score of the full semantic path: a dense scan keeps the 30 templates nearest the question's vector, and a reranker rescores those 30 word by word. The comparison is 0.634 for the dense scan alone, on the development set of 130 questions, with a bootstrap interval of +0.028 to +0.080. A different pair of numbers appears on the held-out set: the dense scan alone scores 0.689 against 0.371 for the store's word-matching search, and reranking was never scored there. Table 8 says these are machine-made judgements.

**Section:** 5.6 and Table 8. **Confidence:** sure.

## 10. Why the cost figures do not transfer, and the one measurement that fixes it

Section 5.1 and Section 6.1 say every cost figure comes from a laptop rig, not from a farm machine. The rig also had two forms, a healthy rig and a slowed re-run rig, which were never averaged, and the rig saturated unnoticed after the first pass, so every later measurement of that pass was retracted. The rig gave the collector and the store four exclusive cores and always pinned them, so sharing cores with reconstruction is not measured. Section 5.1 states the rule: shapes, knees and rankings transfer to the farm, absolute rates and core-seconds do not. Section 6.1 names the single fix: one micro-benchmark on one farm machine gives the conversion factor, and none has run.

**Section:** 6.1, with 5.1. **Confidence:** sure.

## 11. The six objectives and the element that meets each

Table 2 in Section 2.4 pairs each objective with one design element.

- No bulk over the wire: a local index on every worker, written by a collector that talks only to the search node on its own machine.
- Split by severity: the collector routes each line by severity to the local index or to the storage tier.
- Distributed: one cluster of two tiers, and every worker answers a search over its own index.
- Durable: a storage tier of three machines, with three copies of every record above info and of every InfoLogger record, one copy on each storage machine.
- Workers stay cheap: four physical cores, no cluster management on a worker, and health numbers pushed up rather than polled down.
- Fault tolerant: the cluster runs while two of the three storage machines agree, and a lost worker loses only its own local index.

Section 2.4 sums them up as one sentence: keep the volume where it is made and the value where it is safe.

**Section:** 2.4, Table 2. **Confidence:** sure.

## 12. Detector, monitor, episode and notification

Section 3.6 defines all four. A monitor is a saved rule that the cluster runs on a schedule, and it writes an alert when its condition holds; thirty monitors run. A detector is a scheduled Random Cut Forest model, one model per entity, that learns a number's usual shape and grades each window from 0 to 1 with no threshold; seventeen detectors run. An episode is one trouble on one thing, with a start and an end; the projector groups the signals of one alert name on one entity into one incident document, opens an episode on a grade above 0.5, and closes it after consecutive healthy windows. A notification is what Alertmanager sends after its grouping wait, and the receiver stores one document per notification: what a person was told. The chain is therefore hits, then signals, then episodes, then notifications (Figure 6), and nothing pages a human today.

**Section:** 3.6, with Figure 6. **Confidence:** sure.

---

## Terms used without a definition at first use

- `stdout`, "process stdout", "the stdout tree" (Figure 1, Table 1, Figure 3).
- "heap", "1 GB heap", "a heap holds roughly 20 shards per gigabyte" (3.1, 4).
- "ingest" as a node role, in "data, ingest" (Figure 2); the ingest pipeline is defined later, in 3.2 step 4.
- "primary" and "primaries" as separate from "shard" (Table 3 uses "staging 1 primary"; "primary shard" is only in Figure 3).
- "rollover alias" (3.5) and "rollover" (Table 9).
- "watermark" and "merged, watermark" (Figure 5).
- "upserted", in "a catalogue upserted by version" (Table 9).
- "break-glass" (Figure 6, 3.6); the path is shown but the term is never defined.
- "cockpit", "the maintainer cockpit", "the incident board" (3.9, Figure 6).
- "the ops page" (Figure 2), before Figure 6 says it counts open incidents.
- "SSE stream" (Figure 3).
- "alice-unified" and "family.central" (Figure 3).
- "anchored pattern" and "regex" (2.1, 5.4).
- "fixture run", "the fixture and restart checks", "the rotation check" (5.4).
- "poison replay" (5.7).
- "boot id", inside the document identifier (3.4).
- "OpenStack" (4), "nginx" (3.9 figure, Table 9), "Jinja" (Table 9).
- "OpenSSL", and the bare release numbers 4.0.1, 5.0.8 and 3.4.0, whose product is never named (2.3, 5.4).
- "protobuf", "gzip", "Unix socket", "repeated key names ... of that wire" (5.5).
- "principal component analysis" (4).
- "an eight-bit copy" and "full-precision original" (5.6).
- "transformer", described only as a model that "reads words in context" (5.6).
- "bootstrap interval" (5.6), "an approximate vector index" (5.6), "analysed term" (5.6).
- "development set" against "held-out set" (5.6); the difference is never stated.
- "knees", in "shapes, knees and rankings" (Abstract, 5.1).
- "Metricbeat", "performance analyzer", "Terraform provider", "Prometheus collection role" (Table 9).
- "timeframe reconstruction" is named but not explained beyond one clause (1).
- "drain tree" (Figure 5), against "drain3, a template miner" in the text.
- "Dense retrieval with reranking" in the Abstract, before 5.6 explains either half.

## Ambiguous or contradictory passages

- Detector count. Section 3.6: "Seventeen detectors run, one model per entity." Figure 6 splits the same lane as "log records, 14 detectors", "3 detectors, the forecaster" and "17 detectors and one forecaster". The parts add up, but the reader must do that work.
- Monitor count. Section 3.6: "Thirty monitors run: 13 every minute and four every ten minutes on thresholds and detector results." Figure 6 instead says "17 monitors: 13 every minute, 4 every ten" and "28 of the 30 monitors land here: 9 trend, 17 threshold, 2 hourly on templates". Threshold monitors are 17 in the figure and 13 plus 4 in the text.
- Memory caps. Section 3.4: "The service is throttled at 384 MB and killed at 768 MB." Section 5.5: "208.1 MB inside the stamper's 512 MB limit." Section 5.6: "We cap the service at 384 MB on the shifter host." Three different caps use the word "service", and only one names its owner.
- Corpus sizes. The Abstract says the templating pipeline is "byte-identical on 8,961,245 lines". Table 7 says "zero differences on 3,000,000 lines per family". Section 5.5 uses 45,596,613 and 55,963,050 lines, and Section 5.6 uses 56,628,579. The Abstract figure never appears in the body.
- Number of families. Section 2.1: "A worker holds five log families". Table 1 then lists six rows, the sixth being "Run orchestrator log ... Not collected". Table 7 counts "seven rewritten regular expressions".
- Masker figures. Section 4: "The masker ... was 74 to 87 % of the mining cost". Table 7: "Masking removed by seven rewritten regular expressions 85 to 87 %". The two percentages measure different things and use overlapping ranges.
- First templating version. Section 4: "The first version's 19.91 was measured once and not re-measured, so we quote no ratio between the two." The Abstract and Section 7 still state "31 to 57 % cheaper per family".
- Farm claim. The Abstract and Section 1: "one farm deployment passed every check." Section 6.3 narrows it: "The farm pilot ran the cluster, the collectors and Dashboards. The stamper, the projector and the shifter view have run on staging only."
- The 53,000 figure. Section 5.2: "memory sat between 133 and 228 MB at every rate up to 53,000 records a second." Section 5.1: "The rig saturated unnoticed after that first pass, so we retracted every later measurement." Section 6.1: "Round 1's 53,000 was read against a test output that always accepts."
- DDS size. Section 2.1: "DDS is 0.1 % of that corpus, and operations call it the largest family during data-taking." Section 6.2: "DDS is the densest family during data-taking." Largest and densest are used as if they were one claim.
- The word "page". Section 3.1 and 3.6: "Nothing pages a human today." The same section says "a storage disk above 92 % pages" and "pages once for the fleet above 0.7". Page is both a severity tier and an action.
- Stamper cost. Section 4: "The stamper's cost is not measured." Section 5.5 gives mining costs such as "8.33 core-seconds per million". Whether mining cost is the stamper's cost is never stated.
- Farm size. Section 3.1: "more than 200 collector configurations". Section 4: "do not scale to 100 machines" and "Three hundred data nodes in one cluster, one per worker in the archive". Section 2.2: "312 hosts". No single farm size is given.
- Bucket windows. Section 3.5: "Every 300 seconds the stamper publishes one bucket document per family and window." The audits then read 1-hour buckets, and Table 3 lists both `-5m` and `-1h` indices. Who writes the 1-hour buckets is not said.
- Worker availability. Section 2.3: "Physicists' staging runs occupy two of the three farm workers and a build pipeline wipes the third", and then "Two of the three workers cannot install a collector newer than release 4.0.1". Section 5.4 audits "four farm machines". The three sets of machines may or may not be the same.

## Assumed knowledge

- Search store internals: index, shard, primary, replica, quorum, heap sizing, ingest pipeline, index merging, cross-cluster search, Index State Management, saved monitors, the anomaly detection plugin, and the Discover screen.
- Log shipper internals: the input, tag, filter, retag and output pipeline, chunks, the forward protocol, tail versus socket inputs, multiline parsers, and a disk buffer used as a hiccup layer.
- Alerting practice: alert grouping, group wait timers, silences, inhibition, webhooks, and the idea that an alerting system forgets an alert unless it is re-posted.
- Messaging: Kafka topics, brokers, producers and consumers, and what decoupling and durability mean for a queue.
- Configuration management: push over SSH against agent-based tools, certificates for agents, and machine building against machine configuring.
- Linux and operations: systemd, the journal, the kernel ring buffer, stdout redirection, file rotation, TCP ports, network mounts and change events, OpenSSL versions, containers, and virtual machines.
- Licensing: Apache 2.0, AGPLv3, and what "three licences" implies for a paid feature.
- Machine learning and information retrieval: F1, precision against recall, principal component analysis, embeddings, static word vectors against transformers, eight-bit quantization, nearest-neighbour search, approximate vector indexes, reranking, nDCG, macro averaging, bootstrap confidence intervals, and train against held-out evaluation.
- Benchmarking: core-seconds, cost per million records, noise floors, saturation, and the 95th percentile.
- CERN and ALICE domain: the EPN farm, O2, timeframes, detector data, a run, data-taking, the shifter role, InfoLogger, DDS, and a shared infra machine.
- Report-internal knowledge: the 2025 reference design, and the project's own names for its parts, such as the soak, the roster, the projector, the receiver, the cockpit and the shifter view.
