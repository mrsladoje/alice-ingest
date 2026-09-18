# Constraints brief: the EPN farm, logging before loggy, and what the system must achieve

Sources read, in this order: reports/inputs/deck-text.md (all), docs/ARCHITECTURE.md (all), reports/loggy-report/briefs/inputs/memory-extracts.md (all), deploy/README.md:1-165, docs/SOAK_RESULTS.md:204-462, docs/LOG_TYPES.md (headings, then 1-260, 261-440, 441-506, 507-706), docs/SOAK.md:1-60, docs/SOAK_RESULTS.md:1852-1875 (the Kafka verdict heading only, to anchor the citation).

Every claim carries a `path:line` citation. `[A]` marks a fact that belongs in the report body at the architecture level. `[C]` marks code-level or process-level evidence only. Where a fact is not in the sources I read, the brief says so.

---

## 1. The EPN farm in plain words

**Conclusion.** An Event Processing Node is one machine of the ALICE online farm. It reconstructs timeframes. Logging must not disturb that job. [A]

What an EPN is and what it does:

- The deck names the job in one sentence: "We must not disturb the expensive timeframe reconstruction." (reports/inputs/deck-text.md:92) [A]
- "Those same machines reconstruct events. That is their real job, and log search must not compete with it." (reports/inputs/deck-text.md:193-195) [A]
- The process that assembles timeframes is TfBuilder: "TfBuilder assembles the timeframes everything downstream reconstructs. A dropped timeframe or a failed region allocation is a run-stopping event." (docs/LOG_TYPES.md:605) [A]
- The EPN side runs the reconstruction workflows that write the DPL size reports, and there are many more EPN nodes than FLP nodes (docs/SOAK_RESULTS.md:425-426). [A]
- The steady per-worker log traffic is one repeating message: `DPL` / `readout-proxy` writing a `RAW ... size report` line per time frame (docs/SOAK_RESULTS.md:406-408). [A]

Hardware, from the two sources that measured it:

- The deck says an EPN has 128 cores (reports/inputs/deck-text.md:92). [A]
- `epn228` has 2 × AMD EPYC 7452, 32 cores each, 2 threads per core, so 64 physical cores and 128 logical processors, 2019 generation (docs/SOAK_RESULTS.md:268-272). So the deck's "128 cores" are logical processors, not physical cores. [A]
- The census on 5 September 2026 lists the four allocated machines (docs/LOG_TYPES.md:263-268) [A]:
  - `epn146`: worker, AlmaLinux 9.5, 128 processors, 503 GB memory, uptime 43 weeks (docs/LOG_TYPES.md:265)
  - `epn228`: worker, AlmaLinux 9.5, 128 processors, 503 GB memory, uptime 40 weeks (docs/LOG_TYPES.md:266)
  - `epn323`: worker, AlmaLinux 10.2, 192 processors, 1007 GB memory, uptime 46 days (docs/LOG_TYPES.md:267)
  - `epn-infra13`: storage, AlmaLinux 9.5, 64 processors, 376 GB memory, uptime 31 weeks (docs/LOG_TYPES.md:268)
- So the farm is not one hardware generation. `epn323` has 192 logical processors and 1007 GB, not 128 and 503 GB (docs/LOG_TYPES.md:267). [A]
- Every node mounts one shared scratch file system: `/scratch` is `10.162.0.60:/exports/scratch`, NFS version 4.2, 14 TB, 93 percent full, mounted on every node (docs/LOG_TYPES.md:16-18). [A]
- The farm is batch-scheduled. `slurmd.service` is 65 percent of the journal on `epn146` (docs/LOG_TYPES.md:172). [C]
- `epn228` is a Supermicro AS-4125GS-TNRT-CE1 with an H13DSG-O-CPU board, from a kernel trace (docs/LOG_TYPES.md:187). [C]

How many EPNs there are. The sources do not agree and none is authoritative:

- The InfoLogger archive holds 308 worker hosts (`epnNNN`) over 31 December 2025 to 29 June 2026 (docs/SOAK_RESULTS.md:319-327). [A]
- In the busiest hour, 297 workers were active (docs/SOAK_RESULTS.md:342-343). [A]
- The deck's Loki note speaks of "211 hostnames" (reports/inputs/deck-text.md:272) and the federation slide of "100 machines" (reports/inputs/deck-text.md:181). [A]
- The task text calls production "the 200-plus worker farm". No source I read states a current head count. Mark the count as "about 300 hosts appear in the six-month archive" and cite docs/SOAK_RESULTS.md:327.

What is not known from these sources: local disk size and type on a worker, network speed, GPU count, the number of reconstruction processes per node. The soak notes that "an EPN worker has" roughly sixty concurrent processes, as an inference from the flood limit, not a count (docs/SOAK_RESULTS.md:218-221). No micro-benchmark has been run on `epn228`, so the per-core speed against the laptop rig is not measured (docs/SOAK_RESULTS.md:289-290). [A]

---

## 2. Logging today: InfoLogger

**Conclusion.** InfoLogger carries only what a process sends it, through one daemon per node into one central server and one SQL table. Shifters read those lines by hand. [A]

The path from a process to a screen (deck slide 3 and the 2025 design):

- A process injects through `libInfoLogger`, or pipes its stdout in (reports/inputs/deck-text.md:33-35; docs/ARCHITECTURE.md:8, 13). [A]
- A local socket feeds `infoLoggerD`, one per node (reports/inputs/deck-text.md:53-55). [A]
- `infoLoggerD` forwards over TCP port 6006 to `infoLoggerServer`, one, central (reports/inputs/deck-text.md:56-58; docs/ARCHITECTURE.md:9, 14). [A]
- The server writes over TCP port 3306 into one table in a MySQL database and pushes live to `infoBrowser` clients on TCP port 6102 (reports/inputs/deck-text.md:59-64; docs/ARCHITECTURE.md:10, 15). [A]
- `infoBrowser` is a desktop GUI (reports/inputs/deck-text.md:62-63). [A]
- "One process and one table for the whole farm" (reports/inputs/deck-text.md:65). [A]
- "Shifters read the logs by hand" (reports/inputs/deck-text.md:29). [A]

The three limits named on slide 3 (reports/inputs/deck-text.md:67-76) [A]:

1. One SQL table: "Every message in the farm lands in one messages table." (reports/inputs/deck-text.md:68-70)
2. No aggregation: "infoBrowser lists single lines. It draws no counts over time." (reports/inputs/deck-text.md:71-73)
3. One policy, one copy: "One retention rule covers every message. Nothing is replicated." (reports/inputs/deck-text.md:74-76)

What never reaches InfoLogger (reports/inputs/deck-text.md:28, 32, 36-46) [A]:

- Log files on disk: process stdout, and ten named O2 process logs (reports/inputs/deck-text.md:36-38). Note: the survey later counted 13 programs on `/scratch` and 186 in the archive (docs/LOG_TYPES.md:229). The deck's "ten" is the deck's own count.
- The machine's journal: systemd, every service on the node (reports/inputs/deck-text.md:39-41).
- Host metrics: processor, memory, disk (reports/inputs/deck-text.md:42-44). Monitoring already collects these (reports/inputs/deck-text.md:46). Mimir owns machine health at CERN (docs/LOG_TYPES.md:701).
- "Neither of these reaches InfoLogger" (reports/inputs/deck-text.md:45).

Two InfoLogger limits the deck does not name but the sources measure:

- The client library drops a process that exceeds 1,000 messages in one minute (docs/SOAK_RESULTS.md:218-220, 349-351). The loudest processes are therefore under-represented in the archive (docs/SOAK_RESULTS.md:351). [A]
- The daemon has a connected-client ceiling of 2048 in `infoLoggerD.cfg`. The peak seen on `epn146` is 1025 clients of 2048, half the ceiling (docs/LOG_TYPES.md:206-209, 420-422). [A]
- The archive is a floor, not a measurement: the path has several hops that can shed, and the archive is a retention window (docs/SOAK_RESULTS.md:344-358). [A]

Where the daemon ships today: `infoLoggerD` on `epn146` ships to `serverHost=epn-infra13`, the same machine allocated to loggy's storage tier. Which InfoLogger server that is was not confirmed (docs/LOG_TYPES.md:212-214; memory-extracts.md:71-72). [A]

---

## 3. The log sources on a real node

**Conclusion.** A worker holds five log families. The O2 process tree uses two line formats. The journal is live on every machine. The archive holds InfoLogger, DDS and the process tree, and holds no journal. [A]

The five families, as locked in the deployment tree (deploy/README.md:91-96) [A]:

1. InfoLogger
2. DDS
3. The O2 process-log tree, which holds two line formats under one tail input and one tag (deploy/README.md:93-95)
4. The InfoLogger daemon's own log
5. The journal

The routing records list seven sources, because the process tree splits into `dpl` and `datadist`, and `odc` is added (docs/LOG_TYPES.md:517, 533, 555, 591, 607, 628, 647). [A]

Paths on a real node:

- Process tree: `/scratch/jl/<run_tag>/<epnNNN>.internal/<program>[_t<slot>]_reco<N>_<YYYY-MM-DD-HH-MM-SS>_<pid>_{out,err}.log` (docs/LOG_TYPES.md:33). [A]
- DDS: `/scratch/jl/<run_tag>/<epnNNN>.internal/dds_<YYYY-MM-DD>.<N>.log` (docs/LOG_TYPES.md:34). [A]
- `/var/log/calib` does not exist on any node. It was the previous student's test rig path (docs/LOG_TYPES.md:37-39; memory-extracts.md:50). [C]
- The run directory also holds shared libraries, `topology.xml`, `DDS.cfg` and a worker tarball, so the tail pattern must end in `*.log` (docs/LOG_TYPES.md:41-42). [C]
- InfoLogger daemon log: `/var/log/o2-infologger-daemon.log` (docs/LOG_TYPES.md:206). [A]
- ODC run orchestrator log: `/var/log/odc/staging/odc_<date>.<n>.log`, on `epn-infra13` only (docs/LOG_TYPES.md:297-298, 663). [A]
- The journal: `Storage=persistent` (docs/LOG_TYPES.md:426). [C]

The two O2 line formats:

- DPL/FairMQ: `[HH:MM:SS][TYPE]`, a clock with no date (memory-extracts.md:57-58; docs/LOG_TYPES.md:470-471). A live line carries no recoverable event time, so those records take ingest time and keep the clock in `log_time` (docs/LOG_TYPES.md:470-473). [A]
- DataDistribution (`TfBuilderTask`): `[YYYY-MM-DD HH:MM:SS.mmm][I|D|W|E]`, full date, milliseconds, one-letter level. The previous parser library had no parser for it. It is the only O2 format that already carries a date (docs/LOG_TYPES.md:108-119). [A]
- `ErrorMonitorTask` emits raw ANSI colour escapes, so every anchored regex fails silently. Fluent Bit has no built-in strip (docs/LOG_TYPES.md:121-130). [A]
- The InfoLogger daemon log separates with spaces, not a tab. The first parser matched none of 162,642 lines (docs/LOG_TYPES.md:400-403). [C]

The journal:

- Live on every machine, so it needs no replay (docs/LOG_TYPES.md:175-177, 634). [A]
- A worker writes 2,500 to 10,500 journal entries a day (docs/LOG_TYPES.md:375). [A]
- The kernel ring holds the payoff: eight multi-line IOMMU traces with `Comm: TfBuilder`, an O2 program named inside a kernel stack trace. One trace is roughly twenty lines (docs/LOG_TYPES.md:181-197). Read with `sudo`, `epn146` holds 986 kernel entries in thirty days (docs/LOG_TYPES.md:366-368, 391-393). [A]
- `epn323` emits 9,485 warnings a day, nearly all NetworkManager. `epn146` and `epn228` do not (docs/LOG_TYPES.md:385-389, 643). [A]
- `/var/log/messages` is 71 percent slurmd and 28 percent systemd, both already in the journal. It is not tailed (docs/LOG_TYPES.md:201-202, 698). [A]

What the archive holds:

- InfoLogger: 179 daily MySQL dumps in `s3://epn-backup-logs/infologger-2026/`, 248,828,513 records, 312 hosts, 31 December 2025 to 29 June 2026 (docs/SOAK_RESULTS.md:314-321). [A]
- The archive corpus used for parsers: 45,596,613 lines, 186 programs, twelve detectors, newest run 20 June 2026, 1,236,971 DDS lines (docs/LOG_TYPES.md:220-232). [A]
- Volume shares in that corpus: `infologger` 58.1 percent, `dpl` 41.8 percent of which 99.6 percent is the DPL format, `datadist` 0.44 percent of the process tree, `dds` 0.1 percent (docs/LOG_TYPES.md:530, 568, 604, 546). [A]

What the archive does not hold:

- The journal and the per-device process logs the deck names: "The archive we replay holds neither source, so this one needs a real machine" (reports/inputs/deck-text.md:629-635). Note: the process tree was later found in the archive (docs/LOG_TYPES.md:229). The journal was not. [A]
- DDS is under-sampled. Its 0.1 percent share is wrong for production. Operations report DDS is the highest-volume family during data-taking (docs/LOG_TYPES.md:546). [A]
- The live job-log path. Every `/scratch` directory is from October 2022 and no run was active during either census (docs/LOG_TYPES.md:246-248, 279-293). [A]
- Per-record event time for DDS and stdout in a form that counts per second (docs/SOAK_RESULTS.md:452-454). [A]

What is on `/scratch` today: 11 run tags, all written 27 October 2022, 30 hosts per run, 54,450 files, 64 GB, 13 programs, all MFT (docs/LOG_TYPES.md:46-49, 67). [A]

---

## 4. The volumes

**Conclusion.** A real worker carries tens of InfoLogger records a second. The soak tested one collector at 1,000, 20,000 and 50,000 records a second, all per worker. That is 13 to 640 times the archive rate. [A]

What the archive says one worker carries, during the busiest hour in six months (15 May 2026, 22:00 to 23:00 UTC, 20,151,049 records, 297 workers active) (docs/SOAK_RESULTS.md:341-343) [A]:

| Measure | Records a second, one worker | Source |
|---|---|---|
| median second | 23 | docs/SOAK_RESULTS.md:364 |
| 90th percentile | 33 | docs/SOAK_RESULTS.md:365 |
| 95th percentile | 48 | docs/SOAK_RESULTS.md:366 |
| 99th percentile | 57 | docs/SOAK_RESULTS.md:367 |
| busiest single worker-second | 78 | docs/SOAK_RESULTS.md:368 |

The whole farm, same hour (docs/SOAK_RESULTS.md:372-376) [A]: median second 6,785, 95th percentile 8,537, busiest single second 9,781.

These are floors on the true rate, because the client library drops at the source, the path sheds, and the archive is a retention window (docs/SOAK_RESULTS.md:344-358). [A]

`epn-infra12` alone produced 26,448,820 records, six times the busiest worker. It is `ODC` answering status requests. Every worker figure excludes it and the three `epn-calib` hosts (docs/SOAK_RESULTS.md:330-332, 412-413). [A]

One uncorroborated second in the window carries 187,210 records farm-wide. It is recorded and not used (docs/SOAK_RESULTS.md:386-390). [A]

The tested rates and why they are per worker:

- 1,000 records a second is treated as per worker throughout. This is a decision, not a measurement (docs/SOAK_RESULTS.md:206-207). [A]
- Three reasons: it is the conservative reading, the rate ladder is only coherent at one scope, and the flood limit makes it plausible (docs/SOAK_RESULTS.md:209-221). [A]
- Every rate, 1,000, 20,000 and 50,000, is a per-worker rate offered to one collector (docs/SOAK_RESULTS.md:223-224). [A]
- 1,000 a second is a safety rate, 43 times the archive median of 23 (docs/SOAK_RESULTS.md:233). 20,000 a second is the reference rate, 256 times the busiest worker-second (docs/SOAK_RESULTS.md:234). 50,000 a second is above the collector's own ceiling on purpose (docs/SOAK_RESULTS.md:235). [A]
- At 20,000 a second one collector is offered about twice the whole farm's peak (docs/SOAK_RESULTS.md:434-436). [A]
- The runs mix all three families at the plan's rates. They are an upper bound by two to three orders of magnitude, not a replay (docs/SOAK_RESULTS.md:456-459). [A]
- The derivation covers InfoLogger only. DDS and stdout are not derived per second (docs/SOAK_RESULTS.md:255-259, 452-454). [A]

The collector's own capacity: about 53,000 records a second on two processor cores, memory between 133 MB and 228 MB across the working range (docs/SOAK.md:13-19). Ten burst cycles at 50,000 a second dropped nothing, peak memory 209 MB (docs/SOAK.md:46-51). Everything was measured on a laptop. The shapes transfer, the absolute rates do not (docs/SOAK_RESULTS.md:265-266). [A]

Other daily volumes from the census: the InfoLogger daemon log grows about 930 lines a day with no rotation (docs/LOG_TYPES.md:404-406, 620-621). The ODC log is 11.5 MB and about 100,000 lines a day (docs/LOG_TYPES.md:298, 662). [A]

---

## 5. The constraints

Each bullet is one constraint with its source.

- **Four cores of 128.** A worker "takes four of the 128 cores an EPN has, and little memory" (reports/inputs/deck-text.md:92). On the EPYC 7452, four physical cores are eight logical processors (docs/SOAK_RESULTS.md:275-281). [A]
- **Memory.** "Little memory" (reports/inputs/deck-text.md:92). The collector's measured envelope: 133 to 228 MB steady, 404 MB during a sink outage, 307 MB documented worst case, 64 chunks in memory at 128 MB (reports/inputs/deck-text.md:293-301; docs/SOAK.md:18-20). Hard `MemoryMax` and CPU caps apply to anything placed on `epn146` and `epn323` (memory-extracts.md:23-25). The staging heap is 1 GB on a 3.75 GB machine (deploy/README.md:148-151). [A]
- **The burst ceiling is a guess.** `storage.max_chunks_up: 64` must be measured before anything lands on `epn146` or `epn323` (memory-extracts.md:27-30). [C]
- **No bulk over the wire.** Info logs are indexed on the worker's own disk and never cross the network (reports/inputs/deck-text.md:83-84). The collector writes only to `http://localhost:9200` (deploy/README.md:127-133). [A]
- **No cross-worker shipping.** Reading another node's directory is cross-worker log shipping under another name, which the supervisor ruled out (docs/LOG_TYPES.md:24-26, 700; memory-extracts.md:45-46). [A]
- **Nodes shared with physicists.** `epn146` and `epn323` carry physicists' staging ECS runs. `epn228` is wiped repeatedly by a CI pipeline. Only `epn138` and `epn228` are drained (memory-extracts.md:8-10). "Those nodes are not ours alone" (memory-extracts.md:29). [A]
- **Reconstruction comes first.** Log search must not compete with event reconstruction, so the cluster is asked only when it must be (reports/inputs/deck-text.md:188-199). [A]
- **NFS without inotify.** `/scratch` is one NFS export on every node. A tail over `/scratch/jl/**` would ingest every file once per node. Each node tails only `/scratch/jl/*/$(hostname).internal/`. The tail must poll, so `refresh_interval` is load-bearing (docs/LOG_TYPES.md:14-28; memory-extracts.md:43-48). [A]
- **The scratch mount is 93 percent full** (docs/LOG_TYPES.md:16-17). [A]
- **The OpenSSL wall.** Fluent Bit past 4.0.1 needs OpenSSL 3.4.0 and AlmaLinux 9.5 ships 3.2.2. `epn146` and `epn228` can install nothing newer than 4.0.1. `epn323` runs 4.0.14. No single version fits both. Updating the two EL9 nodes is the farm owners' call (memory-extracts.md:108-112). [A]
- **Four Fluent Bit versions on the farm.** `epn-infra13` runs 3.2.8, two majors behind. Every configuration must be accepted by every deployed version, which is why the journal is folded by a filter (docs/LOG_TYPES.md:270-274; deploy/README.md:122-126). Version 5.0.8 loses bytes after a rotation rename, so no rotating-file collector may move to it untested (docs/LOG_TYPES.md:336-340). [A, the result depends on the versions]
- **The shared infra machine and its port clash.** `epn-infra13` is not ours alone. It runs a second OpenSearch cluster called `logstack` with `epn-infra15` on 9200 and 9300, red with about 520 unassigned shards. loggy uses 9201 to 9203 and 9301 to 9303, and `alertmanager_port: 9193` because 9093 is taken (memory-extracts.md:100-104). [A]
- **The ODC log lives where no collector runs.** The run orchestrator writes on `epn-infra13`, which runs no collector. Collecting it is a deployment decision not taken (docs/LOG_TYPES.md:320-323, 686-692). [A]
- **The flood limit of the InfoLogger client.** A process over 1,000 messages in one minute is cut off at the source (docs/SOAK_RESULTS.md:218-220, 349-351). [A]
- **The daemon's client ceiling.** 2048 sockets in `infoLoggerD.cfg`, seen at 1025 (docs/LOG_TYPES.md:206-209, 420-422). [A]
- **CERN host rules: no agents.** Ansible won because it leaves nothing running on the machines and "A push over SSH puts nothing new on a CERN host" (reports/inputs/deck-text.md:389, 397-401). Puppet with Foreman is the CERN standard and wants an agent and a signed certificate on every node. It is likely right for the farm and wrong for five machines one person deploys by hand (reports/inputs/deck-text.md:402-406, 427). [A]
- **Farm access is as a user, not root.** Reach nodes as `masladoj`. Root has no key. `sudo` is granted for `/usr/bin` and `/usr/local/bin` only (memory-extracts.md:92-95). Farm addresses are internal, reached through a jump host (memory-extracts.md:96-99). [C]
- **One run at a time on the shared machine.** Three inventory hosts share `epn-infra13`, so `--forks 1` is required or the package transactions fight over the rpm lock (memory-extracts.md:113-115). [C]
- **No authentication inside the cluster.** One password guards the web page. Behind it no port asks who you are. That is fine on five owned machines, not on the farm. The farm needs a certificate on every machine, an account for every job, and authentication on every call (reports/inputs/deck-text.md:656-663). [A]
- **Workers never manage the cluster.** A worker "never manages the cluster" (reports/inputs/deck-text.md:92). Worker nodes are never manager-eligible (deploy/README.md:61-62). [A]
- **Host metrics are out of scope.** Mimir owns machine health at CERN (docs/LOG_TYPES.md:701). Monitoring already collects these (reports/inputs/deck-text.md:46). [A]
- **Source-owner approval was not obtained** for reading the node logs. It is a conversation, not a measurement, and it is open (docs/LOG_TYPES.md:258-259). [A]
- **No live log stream exists to test against.** No run was active in either census, no job log was being written, and every `/scratch` directory is from 2022 (docs/LOG_TYPES.md:279-293). [A]
- **The DPL format has no date.** A live line takes ingest time, so lag on a live process-tree record measures the tail's own refresh interval, not the machine (docs/LOG_TYPES.md:470-478). [A]
- **The InfoLogger daemon log never rotates.** No `logrotate.d` entry covers it. It grows without bound (docs/LOG_TYPES.md:404-406, 428). [A]
- **Two accepted divergences of the soak rig.** The rig's storage tier is two nodes with one replica, so every worker-side figure is optimistic. A 1.5-core cell cannot exist under `cpuset` (docs/SOAK_RESULTS.md:292-304). [C]

---

## 6. What the system must achieve

**Conclusion.** Six objectives shape the design. "Together they keep the volume where it is made, and the value where it is safe." (reports/inputs/deck-text.md:82) [A]

The six objectives of slide 4, in the deck's words (reports/inputs/deck-text.md:83-94) [A]:

1. **No bulk over the wire.** "Application logs at info severity are most of the volume. Each worker indexes its own on its own disk. They don't cross the network." (reports/inputs/deck-text.md:83-84)
2. **Split by severity.** "We split some log families by severity, not by source. That puts the volume on one side and the value on the other. Each kind of log then gets its own keep time and its own number of copies." (reports/inputs/deck-text.md:85-86)
3. **Distributed.** "Five machines share the work as one cluster. Together they handle more throughput than any one machine could." (reports/inputs/deck-text.md:87-88)
4. **Durable.** "Some logs matter more than others. We need a storage tier that can ensure higher replication and retention policies for them." (reports/inputs/deck-text.md:89-90)
5. **Workers stay cheap.** "A worker indexes its own logs and answers a query now and then. It never manages the cluster. It takes four of the 128 cores an EPN has, and little memory. We must not disturb the expensive timeframe reconstruction." (reports/inputs/deck-text.md:91-92)
6. **Fault tolerant.** "Two of the three storage machines are enough to keep going. One can die and we lose nothing. In production the tier is bigger, so more can die. Lose a worker and we lose only its local logs." (reports/inputs/deck-text.md:93-94)

What deploy/README.md section 2 locks (deploy/README.md:84-162):

- **Native, no Docker anywhere in the deployment.** Official yum repos, RPMs, systemd units, so operators get `systemctl` and journald ergonomics and no second container runtime on top of OpenStack (deploy/README.md:86-90). [A]
- **Five sources, one severity rule.** Every source has a written routing record. A program that appears tomorrow must be collected the day it appears without a configuration change (deploy/README.md:91-96). [A]
- **Tier by severity, not by source.** `info` is the bulk and the trash, kept local and disposable. `other` is rare and valuable, shipped to the replicated storage tier. Source-based placement would ship the whole stdout-info bulk while discarding DDS errors (deploy/README.md:97-107). [A]
- **Two rules learned the hard way.** A severity nothing recovered routes to durable storage, never local: "silence is not the safe direction". Every router carries a rule keyed on the unparsed record, or a record is dropped without a word (deploy/README.md:109-114). [A]
- **The program name survives.** The replay engine writes one file per process, keeping the name the farm gives it. The same tail pattern and parser serve a live EPN (deploy/README.md:115-121). [A]
- **The journal is folded by a filter, not by the input**, because the farm does not run one Fluent Bit version (deploy/README.md:122-126). [A]
- **The collector writes only to its own local OpenSearch node.** The local info index is pinned to the same machine, so the high-volume path has zero network hops. Central and infologger records go to localhost, then one hop to the storage tier. Only workers run a collector (deploy/README.md:127-133). [A]
- **One control machine for Dashboards, nginx and bootstrap, on the storage tier**, so the UI host never runs the ingest firehose (deploy/README.md:134-138). [A]
- **Slicing by worker count.** `NODE_COUNT` derives from the workers group. Collector identity is `node`, the EPN the log was born on is `host` (deploy/README.md:139-147). [C]
- **Heap 1 GB on a 2 vCPU, 3.75 GB machine**, leaving about 2.5 GB for the OS, page cache and the collector, and raising the anomaly detection model budget (deploy/README.md:148-151). [A, staging parameter]
- **Alertmanager owns notification semantics only.** It is not the incident database. It does not persist alerts across a restart, so the projector's re-send contract is load-bearing and has its own dead-man monitor (deploy/README.md:152-162). [A]

Two further properties the deck states as design goals:

- **One cluster, not many.** Cross-cluster search does not scale to 100 machines. One cluster places shards itself, has one set of users and saved searches, and needs no federation layer of our own (reports/inputs/deck-text.md:150-151, 171-185). [A]
- **Push up, never poll down.** Every worker pushes its own counters. Nothing reaches into a worker to scrape it (reports/inputs/deck-text.md:151, 551-556). [A]

---

## 7. The 2025 reference design, and what loggy did with each element

**Conclusion.** loggy kept the collector, the two-tier cluster, the lifecycle policies and Alertmanager. It changed the tier rule from source to severity. It replaced Grafana with OpenSearch Dashboards. It dropped Kafka between collector and store, the Kafka-fed aggregator and Kubernetes. [A]

The 2025 design is by Athanasios Papadopoulos, ALICE Summer Student, dated 15 September 2025 (docs/ARCHITECTURE.md:4).

| Element | 2025 proposed | loggy | Evidence of the change |
|---|---|---|---|
| Fluent Bit collector on every node | Unified injection of InfoLogger and DDS logs into a local Fluent Bit worker (docs/ARCHITECTURE.md:19, 32-35) | **Kept, and widened.** One process on every worker (reports/inputs/deck-text.md:292). Five sources, not two (deploy/README.md:91-96). Chosen over Fluentd, Vector and Telegraf on maturity and size (reports/inputs/deck-text.md:206-234) | deploy/README.md:91; reports/inputs/deck-text.md:205-206 |
| Collector forwards to OpenSearch and to Kafka | "Forwards to OpenSearch (indexing & querying) and Kafka" (docs/ARCHITECTURE.md:35) | **Changed.** The collector writes only to its own local OpenSearch node (deploy/README.md:127-133). Severity routing in the collector replaces bus routing (deploy/README.md:97-99) | deploy/README.md:127-133 |
| Fluent Bit aggregator fed by Kafka, pushing a live feed to Grafana | docs/ARCHITECTURE.md:27, 37-41 | **Dropped in that form.** The live lane is "a small React page, fed by the collectors. It asks the cluster nothing and survives an outage" (reports/inputs/deck-text.md:451-456). It runs on the fifth storage machine (deploy/README.md:80) | reports/inputs/deck-text.md:455-456; deploy/README.md:80 |
| Kafka as extensibility backbone between collectors and consumers | docs/ARCHITECTURE.md:55-60 | **Dropped between collector and store, on measured numbers.** "A message bus between the collector and OpenSearch buys nothing this stack needs, and it would remove the mechanism that currently makes severity tiering free" (docs/SOAK_RESULTS.md:1852-1861). The deck lists it as roadmap: three brokers on the storage machines, KRaft, trigger is a third reader, cost is memory the five machines do not have (reports/inputs/deck-text.md:611-625). The disk buffer is "A hiccup safety layer, not durability" (reports/inputs/deck-text.md:337-339) | docs/SOAK_RESULTS.md:1852-1857; reports/inputs/deck-text.md:611-625 |
| One cluster, worker nodes plus InfoLogger storage nodes | docs/ARCHITECTURE.md:45-47 | **Kept.** One cluster, worker tier and storage tier, hard-pinned by shard allocation filtering (deploy/README.md:58-73; reports/inputs/deck-text.md:345-348) | deploy/README.md:58-59 |
| Three index families: application-logs-local, application-logs-central (warn/error/debug), infologger | docs/ARCHITECTURE.md:50-53 | **Kept the three names. Changed the rule.** Placement is by severity, not by source (deploy/README.md:97-107). The local index is per worker, one shard, zero replicas, pinned to its machine (deploy/README.md:63-66). Central and infologger have 3 shards and 2 replicas on the storage tier (deploy/README.md:71-73). Debug now stays node-local, where the 2025 design put it in central (docs/LOG_TYPES.md:541, 563, 599, 637; docs/ARCHITECTURE.md:52) | deploy/README.md:97-107 |
| Lifecycle: compression, tiering, retention per family | docs/ARCHITECTURE.md:48 | **Kept.** ISM rolls indices on a clock: routine logs 8 days, others 35, InfoLogger 56 (reports/inputs/deck-text.md:381-383) | reports/inputs/deck-text.md:383 |
| Grafana dashboards, three views | docs/ARCHITECTURE.md:62-70 | **Replaced** by OpenSearch Dashboards behind nginx on the control machine (deploy/README.md:75-78; reports/inputs/deck-text.md:129-130). Four surfaces: maintainer cockpit, live lane, Discover, and the planned shifter view (reports/inputs/deck-text.md:433-468). OpenSearch was chosen because detection and alerting come in the box (reports/inputs/deck-text.md:247-259) | deploy/README.md:76; reports/inputs/deck-text.md:457-462 |
| Alertmanager for unified alerting, fed by Grafana rules and OpenSearch queries | docs/ARCHITECTURE.md:67-69 | **Kept, fed differently.** It sits on the control machine and receives from the projector, which folds many hits into one episode per machine (deploy/README.md:152-162; reports/inputs/deck-text.md:589-598) | deploy/README.md:152-153 |
| Fork of the InfoLogger daemon adding an output stream to Fluent Bit | docs/ARCHITECTURE.md:90 | **Not stated in the sources I read.** The collector reads InfoLogger over "one TCP connection per collector, row-level routing in the replay wrapper" (docs/LOG_TYPES.md:529). How a live daemon would feed the collector is not in my ranges | unverifiable here |
| Kubernetes deployment | docs/ARCHITECTURE.md:91 | **Dropped.** Native systemd, no Docker (deploy/README.md:86-90; reports/inputs/deck-text.md:100). Ansible over SSH, no agent (reports/inputs/deck-text.md:389-401) | deploy/README.md:86 |
| "No change in how InfoLogger is used today" | docs/ARCHITECTURE.md:88 | **Kept as a goal.** The shifter view "should feel close to the InfoLogger view. Familiarity is the point" and is not built (reports/inputs/deck-text.md:463-468) | reports/inputs/deck-text.md:467-468 |
| Storage warning: DDS files are much denser than InfoLogger | docs/ARCHITECTURE.md:89 | **Confirmed by operations.** "DDS is the highest-volume family during data-taking" (docs/LOG_TYPES.md:546) | docs/LOG_TYPES.md:546 |

The two Kafka decisions must stay separate. The soak rejected a bus between the collector and OpenSearch on measured numbers (docs/SOAK_RESULTS.md:1852-1875). The September 2026 review separately agreed a message bus for the live lane only, to decouple the shifter view from every worker's collector configuration. That second decision is stated in the task rules and is not in any line range I was told to read. The report must cite it from another brief.

---

## 8. Three layouts

### Staging: five OpenStack VMs

**Real.** The deployment tree runs on 5 CERN OpenStack VMs as one 5-node two-tier cluster, 2 worker plus 3 storage, native systemd (deploy/README.md:3-6; reports/inputs/deck-text.md:100). [A]

- Workers `alice-ingest-1` and `alice-ingest-2`: data and ingest roles, never manager, a collector writing to localhost only, a replay engine for its EPN slice, one local index with 1 shard and 0 replicas pinned to the machine (deploy/README.md:27-38, 61-69). [A]
- Storage `alice-ingest-3`, `-4`, `-5`: cluster manager, data and ingest roles, quorum 2, holding central and infologger with 3 shards and 2 replicas, no collector and no replay (deploy/README.md:39-56, 70-73). [A]
- `alice-ingest-3` is the control machine: Dashboards, nginx with TLS and auth, bootstrap, Alertmanager, metrics poller, inject (deploy/README.md:41-53, 75-78). The projector runs on `-4`, the live lane on `-5` (deploy/README.md:79-80). [A]
- Each VM is `m2.medium`, 2 vCPU and 3.75 GB, heap 1 GB (deploy/README.md:148-149). [A]
- The replay engine feeds real EPN logs from the archive, sliced by `epn_num % 2` (deploy/README.md:34, 139-147; reports/inputs/deck-text.md:105-106). [C]

**Contradiction to resolve.** The deck's slide 5 says infologger and central have "1 shard, 2 replicas" (reports/inputs/deck-text.md:143-144). deploy/README.md:48 and :72 say 3 shards, 2 replicas. Use the deployment tree's figure and flag the deck.

### The farm pilot: three workers plus three containers on one infra machine

**Real.** The inventory went fully green on the real farm on 27 August 2026: `epn146`, `epn228`, `epn323` as workers plus three OpenSearch containers on `epn-infra13`. Cluster `alice-logs`, six nodes, green (memory-extracts.md:85-87). [A]

- Allocation granted 19 August 2026 (memory-extracts.md:6-8). The storage tier stays three nodes, as three containers, because cloning the existing tier is cheaper than collapsing it and undoing that later (memory-extracts.md:12-15). [A]
- Hardware of the four machines: docs/LOG_TYPES.md:263-268 (see section 1). [A]
- Fluent Bit is installed on all four: 4.0.1 on `epn146` and `epn228`, 4.0.14 on `epn323`, 3.2.8 on `epn-infra13` (docs/LOG_TYPES.md:265-268). [A]
- Ports 9201 to 9203, 9301 to 9303 and 9193, because a second cluster holds the defaults (memory-extracts.md:100-104). [A]
- The container install is a flag on the same role, plus a small container-host role for podman (memory-extracts.md:17-19). [C]

**Not real, or not stated.**

- Whether the workers' collectors were active and delivering on the pilot is not stated in the sources I read. Fluent Bit is installed (docs/LOG_TYPES.md:265-267). No run was active, so no job log was being written on any worker (docs/LOG_TYPES.md:279-293). The journal is the one source that is live there (docs/LOG_TYPES.md:175-177). [A]
- The ODC log on `epn-infra13` is not collected. That machine runs no collector (docs/LOG_TYPES.md:320-323). [A]
- The burst ceiling is unmeasured on `epn146` and `epn323` (memory-extracts.md:27-30). [A]
- No micro-benchmark has run on `epn228` (docs/SOAK_RESULTS.md:289-290). [A]
- Source-owner approval is open (docs/LOG_TYPES.md:258-259). [A]

### Production: the whole worker farm

**Planned. Nothing of it is real.**

- "In production the tier is bigger, so more can die" (reports/inputs/deck-text.md:94). Production has three storage nodes with two replicas (docs/SOAK_RESULTS.md:294-295). [A]
- The archive shows 308 worker hosts, 297 active in the busiest hour (docs/SOAK_RESULTS.md:327, 342-343). The deck reasons at "100 machines" and "211 hostnames" (reports/inputs/deck-text.md:181, 272). No source gives a current head count. [A]
- Authentication inside the cluster is required before the farm and is not built (reports/inputs/deck-text.md:656-663). [A]
- Puppet under Foreman is the CERN standard and "likely right for the farm" (reports/inputs/deck-text.md:402-406, 427). [A]
- The two EL9 worker releases must be updated before a single Fluent Bit version fits, and that is the farm owners' call (memory-extracts.md:108-112). [A]
- The InfoLogger daemon today ships to `epn-infra13`, which is unresolved (docs/LOG_TYPES.md:212-214). [A]
- Kafka for durability, more log types on a real machine, log-text anomaly detection, the shifter view and cluster authentication are the five missing items, in build order (reports/inputs/deck-text.md:604-663). [A]
