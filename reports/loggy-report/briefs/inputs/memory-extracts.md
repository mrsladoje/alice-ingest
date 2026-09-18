# Memory extracts for the constraints brief (copied 2026-09-14; project memory is not visible to workflow agents)

## epn-migration-plan.md


Lubos granted EPN farm access on 2026-08-19. Allocation: `epn146`, `epn228`,
`epn323` as worker nodes (local OpenSearch + producers), `epn-infra13` for the
storage tier plus Dashboards. Only `epn138` and `epn228` are drained; `epn146`
and `epn323` carry physicists' staging ECS runs, and `epn228` is wiped
repeatedly by Sean's CI pipeline.

**Lubos decided the storage tier stays three nodes, as three containers on
`epn-infra13`** — not one node. Reason accepted: cloning the existing three-node
tier is cheaper than collapsing to one and undoing it later. I argued for a
single node first; overruled, and the argument is closed.

Implemented 2026-08-19: `opensearch_install_method: native|container` flag on
the existing role (no second role), new `container_host` role for podman only,
`inventory.epn-sim.yml` for the four-VM lxplus rehearsal. Access instructions
live in the gitignored `docs/EPN-ACCESS.md`; upstream is
`alice-epn.docs.cern.ch/general/ssh.html`.

**Why:** the shared-node facts drive real engineering constraints — hard
`MemoryMax` and CPU caps on anything placed on 146/323, and an OpenSearch role
that survives a bare-metal reinstall on 228.

**How to apply:** treat the three-container layout as settled. Before anything
lands on `epn146` or `epn323`, the Fluent Bit burst ceiling
(`storage.max_chunks_up: 64`) must be measured — it is still a guess, and those
nodes are not ours alone. See [[validate-ansible-before-push]] and
[[lubos-review-and-aug19-deadline]].

## epn-log-survey-findings.md


Survey ran 2026-08-27 on `epn146` via `tools/epnsurvey/survey.sh`. Full write-up
in `docs/LOG_TYPES.md`. Claude can SSH to epn146/epn228/epn323/epn-infra13
passwordless (ProxyJump `login`, sudo works) — no need to ask the user to paste
output.

Findings that are NOT re-derivable from the repo:

- **`/scratch` is NFS 4.2 (`10.162.0.60:/exports/scratch`), 14 TB, 93% full,
  mounted farm-wide.** All four nodes see identical `/scratch/jl`. A tail over
  `/scratch/jl/**` on every node would ingest each file N times, and reading
  another node's directory is cross-worker shipping, which Lubos forbade. Each
  node must tail `/scratch/jl/*/$(hostname).internal/*.log` only. NFS has no
  inotify, so `refresh_interval` is load-bearing.
- **Real path:** `/scratch/jl/<run_tag>/<epnNNN>.internal/<program>[_t<slot>]_reco<N>_<date>_<pid>_{out,err}.log`.
  Thanasis's `/var/log/calib` does NOT exist on any node — his path was a test rig.
  Run dirs also hold `.so`/`.tar.gz`/`.xml`, so tail patterns must end `*.log`.
- **Data is stale:** 11 run tags, all 2022-10-27, 30 hosts, 54,450 files, 64 GB,
  13 programs, all MFT — zero ITS anywhere.
- **Thanasis's 30 regexes:** all compile; 17 score zero (12 ITS + ctf_add,
  ctf_written, io_stats, link_discard, feeid_stats); extractor coverage 61.9%
  over 45,277 sampled lines.
- **Two O2 formats, not one.** DPL/FairMQ `[HH:MM:SS][TYPE]` (no date → R1
  violation confirmed in real data) and DataDistribution
  `[YYYY-MM-DD HH:MM:SS.mmm][I|D|W|E]` (TfBuilderTask, date present, no parser
  exists).
- **ErrorMonitorTask emits raw ANSI escapes**, so every anchored regex fails
  silently. Fluent Bit has no strip-ansi filter.
- **`ctf_written` will miss even live runs**: the O2 build writes `CteationTime`
  (typo) where the regex expects `CreationTime`.
- **Kernel ring holds the payoff**: 8 multi-line `iommu_dma_unmap_page` WARNING
  traces with `Comm: TfBuilder` — an O2 process named inside a kernel stack
  trace. No VM can produce that; needs multiline + keep `Comm:` as join key.
- `/var/log/messages` is 71% slurmd + 28% systemd, both already in journald —
  do NOT tail it. `/var/log/o2-infologger-daemon.log` IS worth tailing
  (`New client: 557/2048` vs the 2048 cap in `/etc/o2.d/infologger/infoLoggerD.cfg`).
- `infoLoggerD` on epn146 ships to `serverHost=epn-infra13` — the node allocated
  to us for storage. Unresolved.

Blocking defect found in our own code: `replay_tarballs`
(`images/replay/replay.py:385`) merges every `_out.log` member into one
`stdout/<host>.log`, destroying the program name before the collector sees it.
Items 1 and 2 cannot proceed until that is un-merged.

Related: [[epn-migration-plan]], [[alice-logging-architecture]],
[[lubos-review-and-aug19-deadline]]

## epn-farm-deploy.md


`deploy/inventory.epn.yml` went fully green on the real farm on 27 August 2026:
epn146, epn228, epn323 as workers plus three OpenSearch containers on
epn-infra13. Cluster `alice-logs`, six nodes, green.

Six things bit, none of which the OpenStack rehearsal in [[epn-migration-plan]]
could have shown:

1. **Reach the nodes as `masladoj`, never `root`.** Root has no key and its
   password is not ours. `sudo` is granted for `/usr/bin` and `/usr/local/bin`
   only, but that does NOT restrict Ansible — `become` enters through
   `/bin/sh`, and everything inside that root shell is unrestricted.
2. **Ansible connects to `ansible_host`, which must stay an IPv4 address**
   because group_vars/all.yml builds the seed list from it. Those addresses are
   internal to the EPN network, so `Host epn*` in `~/.ssh/config` never
   matches. The inventory carries `ansible_ssh_common_args: '-o ProxyJump=login'`.
3. **`epn-infra13` is NOT ours alone**, contrary to docs/EPN-ACCESS.md. It runs
   a second OpenSearch cluster called `logstack` with `epn-infra15`, on 9200 and
   9300, red with ~520 unassigned shards since long before we arrived. Ours
   uses 9201-9203/9301-9303 and `alertmanager_port: 9193`, because 9093 is
   taken there too.
4. **An inventory file's HOST vars outrank `group_vars/all.yml`; its GROUP vars
   do not.** That is how `alertmanager_port`, `admission_control_mode` and
   `log_primary_shards_storage` are set without `-e` flags.
5. **Fluent Bit past 4.0.1 needs OpenSSL 3.4.0 and AlmaLinux 9.5 ships 3.2.2.**
   epn146/epn228 can install nothing newer than 4.0.1; epn323 is AlmaLinux 10.2
   and its repo starts at 4.0.11, so it runs 4.0.14. No single version fits
   both. The real fix is updating the two EL9 nodes, which is the farm owners'
   call.
6. **`--forks 1` is required.** Three inventory hosts share epn-infra13, so the
   `common` role runs three times at once there and the dnf transactions fight
   over the rpm lock. A different host lost the race on each of three runs.

Also pinned `ansible_python_interpreter: /usr/bin/python3` — discovery picks
3.12 on AlmaLinux 9, but the distribution ships python3-cryptography only for
the platform 3.9. And the SELinux tasks in the dashboards role are now guarded,
because SELinux is disabled on all four nodes and both modules error rather
than skip.

Related: [[node3-control-overload]], [[new-plan-implemented-unverified]].
