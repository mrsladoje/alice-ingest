# ML_AI.md

**ML & AI on the ALICE O2 logging platform — anomaly detection foundations, verified SOTA verdicts, and the recommended architecture**

Consolidated from two sessions (July 2026):

| Part | Scope | Source |
|---|---|---|
| [Part I](#part-i--anomaly-detection-foundations) | How anomaly detection works, where it lives in our stack, feature design | Teaching session (pre-3.x migration), stack-grounded |
| [Part II](#part-ii--anomalies-and-automation-the-autoscaling-trap) | Why anomaly signals must not drive autoscaling directly; the safe decomposition | Teaching session |
| [Part III](#part-iii--verified-sota-verdicts-2026-07-22) | Adversarially verified research: DL/LLM log AD, OpenSearch 3.x, TSFMs, production practice | Deep-research workflow (105 agents, 3-vote verification) + 2 follow-up passes |
| [Part IV](#part-iv--the-paper-lane-peer-cohort-ad) | Peer-cohort AD prior-art map and the open niche; the must-read ALICE 2025 paper | Academic literature check |
| [Part V](#part-v--recommended-architecture) | What to run now, what to prototype, open questions | Synthesis |

Revised 2026-07-22 after an external review (GPT 5.6): the review's repo claims were checked against the code and its citations were independently verified (all held up, with nuances noted inline). Key corrections it forced: entity-identity fix (`host` vs `node`), cockpit-metrics as the first detection layer, missing-bucket imputation, a real evaluation protocol, and the documented HCAD sizing math.

> **How to read this:** Parts I–II are conceptual foundations (stable). Part III claims passed 3-0 adversarial verification unless flagged; recovered-but-unverified claims are marked `[UNVERIFIED]`. Do not quote an `[UNVERIFIED]` claim externally without re-checking the source.

---

# Part I — Anomaly detection foundations

## 1. What AD replaces, and why

A static threshold ("alert if errors/min > 100") encodes a guess with no context: on an idle Sunday 100 err/min is a fire, mid-run with 31 EPNs it's a calm Tuesday. Anomaly detection replaces *"is this value above a line I guessed?"* with *"is this value surprising, given everything observed so far?"* — the model learns "normal" from the data itself and adapts as normal drifts.

Two foundational facts shape everything:

1. **No labels exist.** Nobody hand-marked the InfoLogger dumps as normal/broken → the setting is **unsupervised**.
2. **Anomalies are rare and different** — normal points crowd together, anomalous points sit in empty space. "How isolated is this point" is a measurable proxy for "how anomalous."

## 2. Random Cut Forest (the engine OpenSearch runs)

The AD plugin is built on **Random Cut Forest** (RRCF, Guha et al., ICML 2016). Intuition: randomly slice the point cloud until every point is isolated; points in dense crowds need many cuts, loners get isolated in one or two. Few-cuts-to-isolate = high anomaly score. A *forest* of independently-sliced trees votes, which makes the score robust.

Properties that matter for us:

- **Streaming/rolling:** the forest maintains a sliding sample under insertions *and* deletions (sublinear update, exact distributional guarantee — RRCF Theorem 3). "Normal" drifts with the data: a run start that 15×'s volume alarms briefly, then becomes the new baseline. A threshold would page for the whole run.
- **Multivariate:** with features (volume, error-rate, latency) each interval is a point in 3D. RCF doesn't compute ratios — it detects *combinations that have never occurred* (empty regions of the space). High volume + zero errors (dead parser) is caught even when each feature alone looks fine.
- **More robust to irrelevant dimensions than classic Isolation Forest** (RRCF picks the cut dimension proportional to bounding-box side length) — but this is relative, not a license for wide feature sets: OpenSearch's own guidance warns that adding features can hurt precision *and* recall and defaults detectors to a maximum of 5 features. Prefer several small detectors organized around failure modes over one broad everything-detector.

## 3. Feature design for this stack

A detector consumes a **heartbeat, not a firehose**: per time bucket (e.g. 1 min), per entity, compute aggregations. Natural features here:

| Feature | Aggregation | Catches |
|---|---|---|
| Volume | `count()` per index family | EPN gone silent (zero is deeply abnormal — thresholds watching for *high* values never see absence). **Requires zero-imputation — see below** |
| E/F count | `count()` where severity in (E, F) | Failure bursts. Note: this is a *count*, not a rate — if a normalized error *fraction* is wanted, compute it explicitly alongside total volume |
| Ingest latency | `avg(ingest_time − @timestamp)` | Pipeline backpressure *before* data loss — the two-timestamp model is a purpose-built AD feature |

**Entity identity — the trap in our own schema.** In this stack `node` is the **collector** (Fluent Bit VM, stamped from `NODE_ID`), while `host`/`hostname` is the **emitting EPN** — one collector fronts a rack of EPNs (`images/node/producers/common.py`, mirrored by the real-S3 replay's epn%N partitioning). A detector with category field `node` builds one model per *collector rack*, not per EPN. For per-EPN detection (and the entire peer-cohort idea) the category field must be `host`. Longer-term, normalize identity explicitly: `collector_id` (current `node`), `epn_id` (current `host`), plus separate dimensions for run, detector, and software version — cohort comparisons are only valid within compatible (run, role, version) slices.

**Missing buckets are not zeros.** By default OpenSearch AD *ignores* missing data — an EPN that stops logging produces no bucket, not a zero, so a plain `count()` feature does **not** catch silence out of the box. The advanced settings offer imputation options; per the docs, **"Fill with Zeros"** is exactly for complete drops in event counts. Policy: zero-impute volume-like features; do *not* blanket-apply it to latency or resource metrics (a missing latency reading is unknown, not 0 ms). Related settings that must be explicit per detector: interval, **window delay** set to cover the high-percentile ingestion lag (missing buckets degrade shingle formation — the docs recommend window delay ≥ expected ingestion delay), and shingle size (default 8).

What this catches that no threshold can: a node going **silent** (absence, not excess); latency **creep** before it becomes data loss; a single detector (e.g. TPC) erroring at another detector's rate while the farm-wide total looks fine (per-category models); and never-seen *combinations* — high volume with zero errors (dead severity parser) sits in empty feature space even though each feature alone is unremarkable.

Rules of thumb:

- **Features must be things that *should* be stable.** Feeding a legitimately-wild counter teaches the model that diving suits are normal.
- **Category field (`host` for per-EPN models, or `detector`)** → one model per entity (high-cardinality AD), so a chronically noisy EPN doesn't poison the fleet baseline. Not `node` — see the entity-identity trap above.
- **Warm-up is real:** RCF needs a few hundred intervals before scores mean anything. Historical/backtest mode over pinned replay data (`RUN_TAG`) trains it immediately. **Replay caveat:** the replay engine deliberately preserves historical event timestamps while ingesting *now* (`images/replay/replay.py`), so during replay `ingest_time − @timestamp` measures the age of the archive, not pipeline latency. Replay validates volume/template/severity models; latency features need a virtual replay clock, rewritten timestamps, or a live controlled test.
- **One horizon is not enough.** At a 1-min interval the default shingle of 8 gives ~8 minutes of context; ALICE has run starts/stops, job transitions, detector phases, deployments, and sustained regime changes. Run multiple horizons (e.g. 1, 5, 30 min), condition on run/job phase where possible, and keep an explicit change-point/regime-shift lane — a streaming model *adapts*, which is a feature for run starts and a bug for persistent faults (they get absorbed as the new normal; the regime lane is what still reports them). **The slow-horizon tiers do not fill this lane by themselves** — a 30-min RCF has longer memory than the 1-min one but still adapts eventually (a slower frog is still a frog), so a drift that persists for days (a degrading node, a slowly leaking parser) escapes every RCF tier: a gradual ramp never surprises the recent window, and a sudden-but-sustained step fires once on the edge then gets absorbed. The lane needs an **anchored reference RCF never keeps** — a deterministic trend rule (Part V item 3) comparing a recent long-window aggregate against a frozen/older baseline, run-phase-gated so benign run-start shifts don't fire; CUSUM/BOCPD is the upgrade if the ratio rule proves too blunt.
- **Anomaly ≠ bad.** The detector's job is *surprise*; a human or a downstream rule decides *harm*. Conflating the two drowns you in false alarms.
- **AD layers on top of rules, doesn't replace them.** Keep hard rules for known-catastrophic conditions (instant, explainable); the forest covers unknown-unknowns.

## 4. Where it lives (three layers)

1. **AD plugin, in-cluster (start here — production path).** Detectors over the existing indices, Alerting plugin for notifications, anomaly results written to indices. Zero new services. First target is not the log indices but **`cockpit-metrics`** — the 30-second operational telemetry the dashboards role already emits (`metrics_poller.py`: cluster health/shards/pending tasks, per-index doc deltas, node heap/CPU/disk, Fluent Bit up/errors/retries/drops, Dashboards latency). Deterministic failures get hard rules (`fb_up=0`, red cluster, unassigned shards, dropped records); RCF covers unfamiliar *combinations* (throughput collapse + retries, heap growth + falling indexing rate). Log-based detectors then serve diagnosis and attribution after telemetry fires — metrics-plus-logs is also what current HPC practice ships (NodeSentry, Part IV).
2. **ML Commons, in-cluster.** Embedding models / k-NN machinery (same plumbing as semantic search) for message-level novelty — semantic search asks "who are my nearest neighbors?", novelty detection asks "how *far* is my nearest neighbor?"; agent framework for LLM-assisted triage. See Part III §3 for what 3.x actually shipped.
3. **External pipeline (Kafka → Python consumers).** Full control, real ops burden. Deliberately deferred — earn it only when Layer 1's ceiling actually blocks something.

---

# Part II — Anomalies and automation (the autoscaling trap)

**Question examined:** can the ingest-latency anomaly trigger Kubernetes to add OpenSearch nodes? **Verdict: technically yes, and it's a runaway-loop design.**

## 1. The control-theory problem

A feedback loop whose correction is *slower* than the problem it corrects **oscillates instead of settling** (the hotel-shower-with-a-long-pipe law). Three specific failures stack up:

1. **A new OpenSearch data node makes latency worse before better.** Shard rebalancing copies gigabytes over the same disk/network that's already the bottleneck → latency spikes → detector fires again → another node. Each oscillation costs a VM.
2. **Lagging symptom, slow remedy.** Latency appears after pressure is real; provisioning + rebalancing takes minutes. Problem moves in seconds, fix moves in minutes → guaranteed oscillation.
3. **Detectors and actuators have opposite personalities.** RCF is deliberately jumpy (better to cry wolf than miss one); an actuator must be calm, with hysteresis and cooldowns. Wiring one straight into the other marries incompatible tuning goals.

## 2. The safe decomposition

| Job | Right tool | Personality |
|---|---|---|
| Detect "something's weird" | RCF detector → alert a human / open incident | jumpy, sensitive |
| Act "add capacity" | scale on a *leading, cause-specific, stable* metric with cooldown | calm, patient |
| Diagnose "is the scaler itself broken?" | detector supervising scaling-events + latency together | watchful |

- **Scale the stateless tier only** (Fluent Bit collectors/workers): adding one is instant, nothing rebalances. The leading signal is **filesystem buffer depth** (`flb-storage` backlog) — the *cause*, upstream of the latency *symptom*.
- **Stateful OpenSearch data nodes are capacity planning, not reactive autoscaling**: added deliberately, off-peak, rebalancing throttled, informed by week-over-week anomaly trends. This maps directly onto the v2 two-tier design: the disposable node-local info tier can flex; the replicated storage tier should not twitch.
- If/when k8s arrives: **KEDA** scaling the stateless collector Deployment off an external query, with a cooldown window; the data-node StatefulSet stays under human control. Do not adopt k8s *because of* autoscaling — stateful storage is what k8s autoscaling is worst at.
- The latency anomaly's actual jobs: page a human, feed capacity-planning trends, and **supervise the autoscaler** (scale-up followed by worse latency is itself an anomalous combination → smoke detector for runaway loops).

---

# Part III — Verified SOTA verdicts (2026-07-22)

Deep-research pass: 5 search angles, 23 sources fetched, 114 claims extracted, top 25 adversarially verified (3 skeptic votes each) → 20 confirmed, 5 refuted. Follow-ups recovered the unverified industry/streaming/cohort claims and ran a targeted academic check.

## 1. Deep-learning log AD — benchmark theater

**Verdict: the published F1 > 0.9 numbers are artifacts; classic unsupervised methods are the defensible production choice.** *(3-0)*

- Le & Zhang, **"How Far Are We?" (ICSE 2022**, [arXiv:2202.04301](https://arxiv.org/abs/2202.04301)) — the canonical, still-unrebutted critique: training-data selection, grouping, class distribution, noise, and early-detection requirements all swing results; "the problem … has not been solved yet." HDFS F1 drops ~90% → ~72% once preprocessing/leakage artifacts are removed.
- **SemPCA** (TOSEM 2024 "Try with Simpler", [DOI 10.1145/3644386](https://dl.acm.org/doi/10.1145/3644386)): unsupervised PCA + TF-IDF template vectors averages F1 0.959 across five datasets vs 0.983 for the best *supervised* DL model — **within ~2 points, ≥5,800× faster to train** (0.279 s vs 1,620 s), i.e. retrainable per-run.
- **DeepLog/LogAnomaly collapse under template drift** (BGL F1 ~0.23–0.27 vs ~0.71 for plain PCA): next-event-prediction designs flag every *unseen* template as an anomaly. ALICE, with constantly evolving O2 software across thousands of nodes, is a worst case for that family. Avoid.
- Caveat: SemPCA's *absolute* F1s inherit LogHub inflation — the relative-parity conclusion transfers, the numbers don't. Any evaluation for us must run on our own InfoLogger/DDS replay data, streaming, under the protocol in §7 (unsupervised training, but *labeled* evaluation via fault injection and holdouts).

## 2. LLM-based log AD — crowded and unproven

**Verdict: not in the detection path; offline triage/explanation only. Not a paper.** *(3-0)*

- **LLM4Log** systematic review ([arXiv:2604.16359](https://arxiv.org/abs/2604.16359)): 145 papers through Nov 2025 across seven log tasks; more LLM-log papers 2020–2025 than all log-analysis papers 1997–2020. RAG-over-logs is settled prior art (consistent with the July-2 research pass).
- Open challenges *named by the surveys themselves*: drift robustness, long-tail events, hallucination/grounding of operator-facing output, latency, cost. "LLMs significantly outperform traditional methods" appears in one 2025 SLR but rests on LogHub-family benchmarks — treat as unproven (contradicted by Drain+RF pipelines hitting 86–95% F1 on fairer setups).

## 3. OpenSearch 3.x — what the 2.17→3.7 migration actually bought

**Verdict: most of the AD platform we'd otherwise build is already in 3.7.0.** *(3-0, official release notes/docs)*

| Version | Shipped |
|---|---|
| 3.0 | No AD-engine changes (Discover contextual-launch UX only). ML Commons: MCP support + plan-execute-reflect agent, both *experimental*. |
| 3.1 | **Forecasting GA** — same RCF family, *online learner* (incremental per-point updates, no batch retraining), Alerting integration. |
| 3.3 | **AD suggest API** + Dashboards "Suggest parameters" button; **real-time frequency scheduling**. ML Commons: agentic search + persistent agent memory **GA**. |
| 3.5 | **Cross-detector anomaly correlation** via temporal-overlap (IoU) similarity ([anomaly-detection#1641](https://github.com/opensearch-project/anomaly-detection/pull/1641)) — groups overlapping anomalies from many detectors into one incident. |

Precision that matters: the 3.5 feature is post-detection *grouping*, **not** cross-signal correlation-break detection — the latter remains unbuilt anywhere (paper-adjacent territory). `CreateAnomalyDetectorTool` (LLM-assisted detector creation) is still experimental, not for production.

## 4. Time-series foundation models — hype for AD

**Verdict: do not build AD on TSFMs; RCF-in-OpenSearch remains the right streaming choice.** *(3-0)*

- Controlled evaluation ([arXiv:2412.19286](https://arxiv.org/pdf/2412.19286)): weighted XGBoost and autoencoder baselines **match or beat** TimeGPT/FPT/Time-MOE/Moirai/Chronos at AD across five datasets, at 0.5–2 min compute vs 6–48+ min, with no zero/few-shot advantage.
- **Refuted in verification** (do not repeat): Toto trained *exclusively* on Datadog telemetry (0-3); Toto SOTA-on-BOOM + #1 GIFT-Eval framing (1-2); "only TimeGPT and FPT can do AD at all" (0-3).
- Useful asset: **BOOM** benchmark (2,807 real observability series, ~350M observations, Apache 2.0 on HuggingFace) — evaluation material if the paper touches metrics AD.
- Independently reinforced by **"When Foundation Models are One-Liners"** ([ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10010437)): MOMENT/Chronos/TimesFM/Time-MoE/TSPulse show no significant TSAD advantage over moving-window variance and squared-difference *one-liners*.
- AD-specialized foundation models, current status (verified 2026-07-22): **TimeRCD** ([arXiv:2509.21190](https://arxiv.org/abs/2509.21190)) **withdrawn by its authors**; **TimeRadar** ([arXiv:2602.19068](https://arxiv.org/abs/2602.19068)) preprint, no venue; **ChronosAD** ([arXiv:2606.01300](https://arxiv.org/abs/2606.01300)) accepted at INDIN 2026 (+4.7% AUC over baselines on 11 benchmarks). None demonstrates fleet-scale online operation or anything OpenSearch-integrable. Role: offline frozen comparators for the paper at most, not production candidates.

## 5. What production actually runs `[UNVERIFIED — recovered claims, consistent across all sources]`

Unanimous pattern: **statistical baselining / forecasting / categorization, or at most autoencoders on metrics. Nobody ships deep log-sequence models in production.**

- **CERN cloud** (CHEP 2021, EPJ 251 02011): production AD was threshold alarming; the ML pipeline (PyOD zoo: OCSVM/LOF/IF/PCA/KNN vs AE variants) was exploratory, batch (Spark+Airflow), metrics-only — **service logs explicitly out of scope**; no conclusive traditional-vs-deep winner. Its core assumption is peer-ensemble deviation within a hostgroup (see Part IV). Continued as **ADMON** in MONIT (metric AD as a service, 2023).
- **CMS** (CHEP 2024/25): CMSWEB AD = autoencoder zoo (CNN-LSTM etc.) on MONIT *metrics*, classical thresholds over reconstruction error, real-time with alerts; judged on RMSE not detection P/R.
- **Uber**: forecasting-based (predict, then threshold against the forecast), pluggable statistical forecasters.
- **Elastic**: unsupervised time-series modeling + **log categorization** (group by template, detect over category streams) — template-level, the same shape as our unified pattern + severity-tiered indices.

## 6. Streaming AD beyond RCF

- **IDK-S** (Xu, Ma, Zhang, Yang, Ting — [AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/38642)): Incremental Distributional Kernel for streaming AD — designed for evolving distributions, lightweight incremental updates without retraining, strong accuracy/speed across 13 benchmarks. **The strongest external streaming comparator** for the research lane; not a reason to replace native RCF unless it wins an ALICE replay/soak comparison.
- **River HalfSpaceTrees** `[UNVERIFIED]`: degrades when anomalies arrive in bursts (documented limitation) — a poor match for log incidents, which are bursty by nature.
- **STUMPY `stumpi`** `[UNVERIFIED]` (streaming matrix profile): O(1)-ish incremental updates, ~450 pts/sec per series, exact, left-matrix-profile for no-hindsight scoring — viable per-node, not obviously at fleet cardinality.
- **RCF 2.0** `[UNVERIFIED]` (the library under the AD plugin): 5–10× smaller models, ~10× faster (de)serialization, 2–4× heap improvement for high-cardinality AD — the practical reason per-node detectors at scale are plausible at all.

## 7. Evaluation science — label-free *training* is fine, label-free *evaluation* is not

**Verdict: an unsupervised detector still needs supervised evaluation — precision, recall, false-alarm burden, and detection latency are unmeasurable without an independent notion of what was anomalous.**

- **TSB-AD** ("The Elephant in the Room", Liu & Paparrizos, [NeurIPS 2024 D&B](https://proceedings.neurips.cc/paper_files/paper/2024/hash/c3f3c690b7a99fba16d0efd35cb83b2c-Abstract-Datasets_and_Benchmarks_Track.html)): flawed datasets and biased measures **reverse method rankings**; simple/statistical methods often beat elaborate neural ones; recommends **VUS-PR** over point-wise F1.
- **"Quo Vadis, Unsupervised TSAD?"** (Sarfraz et al., [ICML 2024](https://proceedings.mlr.press/v235/sarfraz24a.html)): deep TSAD models effectively learn near-linear mappings; rigorous benchmarks and simple baselines matter more than architecture novelty.
- **Point-adjustment inflation** (scoring a whole anomalous segment as caught if any point in it fires) is a known results-inflator — do not use it.
- What this means for us: evaluation = **temporal holdouts across runs and software versions** + **controlled fault injection** (collector loss, parser corruption, backlog, packet loss, duplicates, clock skew, node slowdown, cross-node divergence) + shifter/incident annotations where they exist + event-level metrics (VUS-PR or affiliation-based) + **operational metrics** (false alarms per hour/run, time-to-detect, missed events, alert fragmentation, pages per shift). Operator feedback should be captured as durable labels.

---

# Part IV — The paper lane: peer-cohort AD

## 1. The must-read nearest neighbor

> **"A Real-Time Semi-Supervised Log Anomaly Detection Framework for ALICE O2 Facilities"** — Techaviseschai, Tarnpradab, Chibante Barroso, Phunchongharn. *Applied Sciences* 15(11):5901, May 2025. [DOI 10.3390/app15115901](https://www.mdpi.com/2076-3417/15/11/5901).

BERTopic/HDBSCAN topic modeling over **InfoLogger** messages, F1 0.957, shifter-facing dashboard, ALICE co-author. It is per-message *semantic/topic* AD with **no cross-node dimension**. Consequences: (a) mandatory citation and differentiation for anything we publish; (b) it substantially deflates "semantic novelty over embeddings" as *our* contribution; (c) ask Lubos about it — the team may already have context.

## 2. Prior-art map for peer-cohort AD

**Verdict: PARTIALLY CLAIMED — the mechanism is old, the instantiation we want is open.**

The mechanism (flag a node by disagreement with live peers doing the same work, rather than with its own history):

| Prior art | What it claims |
|---|---|
| Bolton & Hand, *Peer Group Analysis* (2001) | The concept, in fraud detection; small scale, batch. Key framing to reuse: **local anomalies** — behavior normal vs the *global* population but anomalous vs the *peer cohort* — are invisible to every global/per-history detector; this is the entire case for cohort AD over identical nodes |
| Mirgorodskiy et al., SC 2006 | Function-trace outliers among identical MPI processes — the classic HPC peer-outlier paper |
| Kasick et al., FAST 2010 | Peer-similarity across identical parallel-FS servers; faulty = diverging from peers. Closest conceptual ancestor |
| PeerWatch, ICAC 2010 | Correlation loss vs peer VMs of the same app |
| Ghiasvand & Ciorba, ISPDC 2019 ([arXiv:1906.04550](https://arxiv.org/abs/1906.04550)) | **Syslog** similarity across sibling-node "vicinities" — closest *log-based* work; one-off, not streaming |
| Fleet condition monitoring (Hendrickx et al. 2021, [arXiv:1912.12941](https://arxiv.org/abs/1912.12941); Farouq et al. 2021–22) | Formalizes "compare a machine to its fleet, not its history" — physical machines, not logs |
| NEC patent [US 10,367,842](https://patents.google.com/patent/US10367842) (2015/2019) | Peer hosts by *learned behavioral similarity*; embedding-based cohort formation (embed hosts+events word2vec-style, k-means/EM) is **claimed technique, not novelty**; scoring recipe `f(h)=α·f1+β·f2` (f1 = Jaccard drift of the peer set, f2 = avg cosine dissimilarity of new events vs peers in embedding space) — the concrete baseline a cohort prototype must beat or differ from. **Explicitly excludes role-based peer definitions** from claim scope |
| CERN cloud AD (CHEP 2021) | Hostgroup-ensemble deviation as the working assumption — but metrics-only, batch, logs excluded |
| HPC mainstream (Tuncer ISC'17/TPDS'19, Borghesi 2019) | The contrast class: supervised classifiers / per-node history autoencoders — explicitly *not* peer-cohort |
| **NodeSentry** (SC'25, [DOI 10.1145/3712285.3759794](https://dl.acm.org/doi/10.1145/3712285.3759794), [code](https://github.com/AIOps-Lab-NKU/NodeSentry)) | The closest *current* HPC systems paper: node-level AD via coarse clustering of node patterns (Slurm-integrated, amid frequent job transitions) + fine-grained model *sharing* (Transformer/MoE), two production HPC datasets, fault injection, one-month live deployment. **Mandatory related work and design baseline** — it narrows our novelty claim substantially, though it operates on telemetry metrics, not logs |
| **MultiLog** (KDD'24, [arXiv:2406.07976](https://arxiv.org/abs/2406.07976)) | Direct **multi-node log** AD for distributed databases: single-node log views yield high false positives; multivariate multi-node modeling gains ~12%. **Mandatory citation and baseline** — "cross-node log AD is open" is too broad a claim now |

**The open combination** (narrowed after MultiLog/NodeSentry): **streaming** + **log-template-rate vectors** (NodeSentry is metrics; MultiLog is supervised-ish multi-node fusion, not peer-cohort scoring) + **role/run-scoped cohorts** (outside the patent's literal claims) + a **synchronized workload** — a data-taking run *guarantees* "same work at same time," which prior art has to assume — at EPN fleet scale, in production observability. Defensible as a modernization/instantiation citing the full table; not defensible as an invention claim, and no longer describable as "nobody does cross-node log AD."

Framing: *"peer-cohort log anomaly detection for synchronized homogeneous fleets, applied to ALICE EPNs"* — slots into the venue plan from the earlier research pass (ESEM/ICSE-SEIP-adjacent, CHEP/ACAT), and the real-S3 replay data provides exactly the non-LogHub evaluation the critique literature demands.

## 3. Other LHC/CERN publications (for related-work)

CERN cloud AD → ADMON (metrics, production); WLCG "Automatic Monitoring" (CHEP 2023, EPJ 295 07007 — job/transfer records); CMSWEB AD (metrics AEs); INFN FTS log NLP (word2vec error clustering, offline triage); detector-side DQM AD (CMS ECAL [arXiv:2407.20278](https://arxiv.org/abs/2407.20278), DINAMO [arXiv:2501.19237](https://arxiv.org/abs/2501.19237)). No LHC experiment publishes streaming cross-node log AD.

**Watchlist (related work, not detection-path candidates):** **Logs2Graphs/OCDiGCN** journal version (Li, Shi, van Leeuwen, *DAMI* 2026, [DOI 10.1007/s10618-026-01235-6](https://doi.org/10.1007/s10618-026-01235-6)) — graph-based log AD with node-level explanations; relevant only if sequence *explanations* become part of the contribution. **AnomalyGen** ([arXiv:2604.11107](https://arxiv.org/abs/2604.11107)) — code-guided synthesis of *labeled* anomalous log sequences; potentially useful for the fault-injection/evaluation side (Part III §7), not for detection.

---

# Part V — Recommended architecture

## Production now (native in 3.7.0, zero new services)

1. **Layer 0 — deterministic rules + RCF over `cockpit-metrics`.** Hard alerts for `fb_up=0`, unhealthy collector status, dropped records, red cluster, unassigned shards; small RCF detectors for unfamiliar telemetry combinations (throughput collapse + retries, heap growth + falling indexing rate, event-loop delay + request load). This is the cheapest, highest-signal layer and it already has 30 s data.

   **Static-threshold table (Layer 0 hard rules).** Every field below is already emitted every 30 s by `metrics_poller.py` into `cockpit-metrics` — the telemetry exists, only the monitors are missing. Provision these as Alerting monitors idempotently via the bootstrap role (same "detectors as code" mechanism as item 8). Values are initial and operator-tunable; the point is that these conditions are *deterministic* (a known cliff or a binary fault), so they get an instant explainable rule, **not** RCF.

   | Signal (`cockpit-metrics` field) | Rule | Severity | Why static, not RCF |
   |---|---|---|---|
   | `fb_up` | `== 0` | page | collector down is binary, not "surprising" |
   | `cluster_status_code` | `== 2` (red) | page | red = data-loss risk, always bad |
   | `unassigned_shards` | `> 0` sustained 5 min | warn | brief during rebalance, bad if it sticks |
   | `output_dropped_delta` | `> 0` | page | any drop = data loss, no context needed |
   | `output_retries_failed` (delta) | `> 0` | warn | failed retries = shipping breaking |
   | `disk_used_percent` | `> 85` / `> 92` | warn / page | fills → cluster read-only lock. Known hard cliff |
   | `heap_percent` | `> 90` sustained | warn | GC-death-spiral precursor |
   | `fb_healthy` | `== 0` sustained 2 min | warn | Fluent Bit health endpoint failing |

   Static and RCF are **not competitors on these fields** — they watch the same signals for different failure shapes. The static rule catches the *known cliff* (disk-full read-only lock is a fixed OpenSearch behaviour at a fixed %); the RCF detector still covers the *unfamiliar combination* on the same fields (heap climbing *while* indexing rate falls). Run both.
2. **Layer 1 — per-failure-mode RCF log detectors** over the index families (`infologger`, `generic-log-other`, `generic-log-info`), **category field `host` (EPN)** — not `node`, which is the collector. Small feature sets (≤5, per OpenSearch guidance): volume (zero-imputed), E/F count, latency. Explicit per-detector interval, window delay ≥ measured p99 ingestion lag, and per-feature imputation policy.
3. **Multiple horizons + run awareness**: 1/5/30-min detector tiers; condition on run/job phase; keep a change-point lane so persistent faults aren't absorbed as the new normal (Part I §3). The slow-horizon tiers alone don't fill that lane — a 30-min RCF still adapts, just later — so staff it explicitly with a **deterministic trend rule**: per index family and per `host`, compare a recent long-window robust aggregate (e.g. 6 h median of ingest latency / volume / E-F count) against an anchored older baseline (e.g. 7 d median), and alert on a sustained ratio breach (e.g. ≥2× held for a dwell period), **gated by run/job phase** so benign run-start regime changes don't page. Native, zero new services: an Alerting monitor over a scheduled transform, same shape as the Layer 0 hard rules. Upgrade path if too blunt: CUSUM / Bayesian online change-point detection (external compute, real ops burden).
4. **Forecasting** (3.1) on ingest-rate signals — capacity trend + early-warning complement to AD.
5. **Suggest API** (3.3) to parameterize detectors at scale instead of hand-tuning.
6. **Correlation** (3.5) to group per-EPN anomalies into run-level incidents — described accurately: temporal incident *grouping*, not root-cause analysis.
7. **Alerting** → humans/incidents. No automated actuation off anomaly scores (Part II).
8. **Detectors as code**: detector, monitor, alert-throttle, and dashboard definitions provisioned idempotently via REST in the existing bootstrap role (the 3.6 plugin repo added example Terraform scripts for this — example scripts only, not new server functionality; our Ansible equivalent is the same idea).
9. **HCAD capacity gate before any fleet-scale promise.** Sizing is documented: `data nodes × heap × AD-memory% ÷ entity-model size`, default 10% of heap (`plugins.anomaly_detection.model_max_size_percent`), model size measured via the Profile Detector API. Our checked-in layout (5 data-role nodes × 512 MiB heap) gives ≈256 MiB total AD memory — at the docs' illustrative ~1 MB/entity, roughly **~256 resident entity models, not thousands**. Profile each detector, run an HCAD soak test, measure evictions/heap pressure/skipped entities, then choose: more heap, dedicated AD capacity, entity aggregation, or the external cohort service.
10. Backtest detectors over pinned replay data before trusting real-time scores — volume/template/severity features only (replay preserves historical timestamps, so latency features are invalid there; Part I §3).

## Prototype (the paper)

- **Streaming peer-cohort detector**: per-EPN template-rate vectors per interval within a run, scored against the live cohort (Kafka consumer when the bus lands, or OS-side transform meanwhile). Cohorts scoped to compatible (run, role, software version) slices.
- **Baselines to beat, in order of increasing sophistication**: hard rules → robust seasonal/EWMA/moving-variance one-liners (the ICLR 2026 study shows these embarrass foundation models) → OpenSearch RCF → SemPCA → IDK-S (strongest external streaming comparator) → the cohort detector. Optionally TimeRCD/TimeRadar/ChronosAD as offline frozen comparators.
- **Evaluation** (Part III §7): real-S3 replay (DDS + InfoLogger) — deliberately *not* LogHub — with temporal holdouts across runs/versions, controlled fault injection (collector loss, parser corruption, backlog, clock skew, node slowdown, cross-node divergence), no point adjustment, VUS-PR/event-level metrics plus operational metrics (false alarms per run, time-to-detect, pages per shift).

## Explicitly deferred / rejected

| Idea | Status |
|---|---|
| TSFMs for detection | Rejected (Part III §4, reinforced by the ICLR 2026 one-liners study); AD-specialized variants (ChronosAD et al.) are offline paper comparators only |
| LLMs in the detection path | Rejected; optional later: ML Commons agent for offline triage of flagged anomalies |
| DeepLog-family sequence models | Rejected (template-drift fragility) |
| Anomaly-triggered autoscaling of data nodes | Rejected (Part II); collector-tier scaling on buffer depth is the future k8s/KEDA shape |
| Semantic-embedding novelty AD | Deprioritized as a *contribution* (ALICE 2025 paper); still fine as an ops feature |

## Open questions

1. **HCAD soak test on cardboard-airplane**: the sizing *formula* is documented (Part V item 9) but our actual entity-model sizes, eviction behavior, and 3.5 correlation-grouping cost at high entity counts are unmeasured — profile before promising fleet scale.
2. Measured p99 ingestion lag per index family (needed to set window delay correctly) — derivable from the two-timestamp fields on live data, not on replay.
3. Cross-signal correlation-*break* detection (vs 3.5's temporal grouping) — unbuilt everywhere; second candidate paper angle.
4. Production status of the ALICE 2025 framework (paper reads as framework + dashboard; deployed reality unknown — ask).
5. Whether IDK-S's accuracy/cost advantage survives contact with ALICE replay data — the gate for it being anything more than a paper comparator.
6. **Change-point trend-rule tuning**: the deterministic lane's parameters — recent vs anchored baseline window sizes, the ratio-breach threshold and dwell time, and the exact run/job-phase gating — are unset. Derive them from replay plus a steady-state holdout so the rule catches week-scale drift without firing on every run start; decide per-signal whether ratio (latency, volume) or absolute-delta (E-F count) is the right comparison.
