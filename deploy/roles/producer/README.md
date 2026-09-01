# `producer`

Installs `alice-replay` on each worker: the S3 log-replay engine that streams
real ALICE EPN logs out of the CERN Ceph bucket and lays them down where the
local Fluent Bit is already tailing. It is the only source of data in the whole
deploy. Nothing downstream — no index, no detector, no monitor, no cockpit panel
— has anything to show until this role has run and an operator has triggered it.

The engine itself is not written here. `images/replay/replay.py` is preserved
ground truth, shared with the Docker Compose stack, and is copied to the VM
byte-for-byte. What this role adds is everything needed to run that one file as
one independent single-partition producer per worker, instead of the single
fan-out process it was written to be.

## Why it is a separate role

- **It produces; `collector` consumes.** They share a directory and a TCP port
  and nothing else. The replay can be reinstalled, re-triggered or left idle for
  a week without touching Fluent Bit's configuration, and the parsing rules can
  change without re-uploading an engine.
- **It is the only role that installs a file from outside `deploy/`.**
  `producer_replay_source` points at `images/replay/replay.py`, so a change to
  the compose stack's engine reaches the VMs through this role and no other.
- **It is the one role that must not disturb what it installed.** A running soak
  is operational state. That constraint shapes several tasks below, and it does
  not apply to anything else in the tree.

## What it does

```
                        HOSTS: workers, EVERY DEPLOY

┌─ 1. RUNTIME + FIREWALL ────────────────────────────────────────────────────┐
│  dnf python3, python3-pip                                                  │
│  {{ replay_http_port }}/tcp   one rich rule per allowed client address     │
│  venv at {{ producers_venv_path }} + boto3     --> restart alice-replay    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 2. INSTALL — two files, one of them untouchable ──────────────────────────┐
│  images/replay/replay.py  --copy-->  app/replay.py            0644 root    │
│  replay_partition_wrapper.py  ---->  app/replay_partition_wrapper.py       │
│  both notify restart alice-replay                                          │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 3. CREDENTIALS — vault only, never in the tree ───────────────────────────┐
│  /root/.aws          0700 root                                             │
│  /root/.aws/credentials   0600 root, no_log, profile {{ s3_aws_profile }}  │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 4. THE SYMLINK — one line that replaces an engine edit ───────────────────┐
│  {{ log_root }}/dds, {{ log_root }}/stdout        created defensively      │
│  /var/log/alice-replay-root/{{ node_id }}  --->  {{ log_root }}            │
│  replay.py's unmodified NODES_ROOT/<collector>/ writes now land locally    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     v
┌─ 5. UNIT — armed, not fired ───────────────────────────────────────────────┐
│  alice-replay.service   AUTOSTART_REPLAY=false by default                  │
│  drop-in directory created; clock.conf deliberately NOT written            │
│  flush handlers, then enable + start                                       │
└────────────────────────────────────────────────────────────────────────────┘
```

## The single-partition wrapper

`replay.py` assumes one process serving every collector. It partitions the ~31
real EPN hosts by `epn_num % NODE_COUNT` and then, per family, either writes
`NODES_ROOT/<collector>/<family>/<host>.log` or opens a TCP connection to
`<collector>:INFOLOGGER_TCP_PORT` per record. Our topology is the opposite: each
VM replays only its own slice, into its own log root, and ships InfoLogger
strictly to `127.0.0.1`.

Editing the engine is out of scope, so the wrapper imports it unmodified and
monkeypatches its two extension points.

| Extension point | What the wrapper does | Why it is needed there |
|---|---|---|
| `list_objects(s3, prefix)` | Drops any key that is a per-host DDS or stdout tarball for a host outside this partition, **before** the S3 GET. | One tarball belongs to exactly one host, so the decision is object-level. Filtering here also saves downloading two thirds of the archive. |
| `il_connect(host)` | Connects to `127.0.0.1` when `host` is this node's own id; returns an inert socket whose `sendall` and `close` do nothing for any other name. | One InfoLogger dump object interleaves rows for many hosts, so the decision is row-level and cannot be made on the key. |

A directory or symlink arrangement alone would fix the DDS and stdout side and
leave InfoLogger broken: `il_connect` would retry forever against a hostname like
`node-02` that does not resolve on this VM.

Two behaviours exist only in the wrapper, never in `images/`:

- **`REPLAY_LOOP`** — wraps `run_replay` so a finished pass starts another, each
  re-sampling the shifted-clock offset. The HTTP handler's lock is held for the
  whole loop, so a second `POST /replay` still gets `409`.
- **`REPLAY_CLOCK=shifted`** — slides InfoLogger timestamps, DDS line prefixes
  and stdout filename-derived times forward by one constant offset, so
  `@timestamp` lands near now. `preserved` is the default and leaves the engine's
  behaviour untouched.

## The two clocks

`preserved` keeps the archive's own June 2026 event times. It is what production
does and what the replay is meant to imitate.

`shifted` computes `now − earliest event in the archive` once and adds it to
every event. It exists because the deployed platform has no live log stream: the
log-family detectors, the entry-lag features and the alerting monitors all read
recent windows, and preserved timestamps put every record months in the past
where none of them look.

The scan for the earliest event is expensive — it re-streams whole DDS tarballs
out of S3 — so the answer is cached at `replay_clock_cache`. The archive never
changes, so the cache never goes stale. **Both this role and `playbooks/replay.yml`
delete that cache whenever the configured clock is not `shifted`**, so a preserved
run cannot inherit a stale offset from a shifted one.

## Deploy state and runtime state

This is the role's central non-obvious decision, and the reason for two tasks
that look like omissions.

`playbooks/replay.yml` writes a systemd drop-in, `clock.conf`, carrying the
clock mode, the three pace rates and the loop settings the operator chose. That
file is **runtime state owned by the playbook**, not by this role.

`make deploy` therefore creates the drop-in *directory* and writes nothing into
it. Rewriting `clock.conf` from group defaults on every deploy would replace a
soak's `REPLAY_LOOP=true` and its selected pace, and the restart that followed
would end the run — silently, in the middle of a detector-training window. A
fresh host loses nothing by this: `replay.service.j2` already carries every
default, so the first `make replay` is what installs a drop-in at all.

The handler follows the same rule. A restart is data-safe, because the marker
guard means it will not re-ingest, but it **terminates an in-flight pass and does
not re-trigger it**. So the role restarts only on a real change to the engine,
the wrapper, the venv, the credentials or the unit — and `make status` reports
the resulting idle worker so the operator knows to trigger it again.

## Armed, not fired

`producer_autostart_replay` is `false`. The deploy installs a producer that is
running, healthy and answering its trigger port, and has loaded nothing. An
operator starts the data load with `make replay` (or `make replay-fresh`, which
wipes the log indices first, or the `/ops` page's replay button).

`replay.py`'s own `AUTOSTART_MARKER` guard is still wired up, at
`{{ log_root }}/.replay-autostart-done`, ported one-for-one from the compose
stack. It is what makes a restart safe rather than a re-ingest, and it is why
setting `producer_autostart_replay: true` fires exactly once per host lifetime,
not once per restart.

## The trigger

The unit runs `replay_partition_wrapper.py serve`, which is `replay.py`'s own
HTTP stub on `replay_http_port`.

| Method and path | Effect |
|---|---|
| `GET /health` | Liveness, and whether a pass is in flight. |
| `POST /replay?family=infologger,dds,stdout` | `202` starts a pass; `409` means one is already running. |

`playbooks/replay.yml` posts to `127.0.0.1` on each worker in turn. The `/ops`
page posts to the workers' external addresses — which is what the firewall rule
in step 1 admits, and why the page's button never goes through Ansible.

## Non-obvious settings

- **`replay.py` is copied `0644` and never edited.** It is the one file the
  compose stack and the VM deploy share. Every divergence lives in the wrapper
  beside it, which is why that file's docstring is longer than its code.
- **`producer_replay_source` defaults into the role's own `files/` but is
  overridden in `group_vars` to `images/replay/replay.py`.** The default keeps
  the role runnable standalone; the override keeps this repository at one copy.
  It is resolved on the **controller**, through `playbook_dir`.
- **`EPN_PARTITION` has no default and the wrapper refuses to guess.** A missing
  value exits with an error rather than replaying another node's slice — which
  would look like working ingest while silently duplicating half the archive.
- **`After=fluent-bit.service` is soft ordering only.** InfoLogger rows retry
  forever inside `il_connect`, and tail files are picked up whenever Fluent Bit
  next polls. If the collector's unit were renamed, the `After=` becomes a no-op
  and systemd does not fail.
- **The unit runs as root with no `ProtectSystem` hardening.** It writes under
  `log_root` through the `NODES_ROOT` symlink and reads
  `/root/.aws/credentials`, both outside a `strict` allowance. This matches the
  preserved container behaviour, where the replay ran as root for the same reason.
- **`il_max_objects: 15`, `dds_max_objects: 0`, `stdout_max_objects: 0`.** Zero
  means no cap. The InfoLogger cap is what bounds a pass to roughly 338k rows per
  worker, which is the measured basis for the paced rates.
- **`replay_max_object_bytes: 157286400`.** Objects above 150 MB are skipped. A
  handful of DDS tarballs in the archive are large enough to stall a pass for
  minutes without adding variety.
- **The default rates are paced, not fast.** `il=94/s, dds=10/s, stdout=10/s`
  stretches the archive over about an hour, so the log detectors get the 32
  consecutive one-minute windows a Random Cut Forest model needs before it can
  leave `Initializing`. A ten-minute dump only ever offers ten windows. The
  `*_fast` rates are reachable only through `make replay-fast`.
- **The credentials template is `no_log: true` and `0600`.** Values come only
  from `group_vars/vault.yml`, and the file is regenerated on every run.
- **The `log_root` subdirectories are created defensively.** The `collector` role
  owns them, but this role must not depend on play ordering to have a place to
  write.

## Role variables

Deliberately thin. Everything cross-cutting lives in `group_vars/all.yml`, which
the role reads directly rather than re-declaring.

| Variable | Default | Meaning |
|---|---|---|
| `producer_service_name` | `alice-replay` | Unit name. Also read by `playbooks/replay.yml`, with a literal fallback. |
| `producer_allowed_client_addresses` | `[]` | Addresses permitted through the firewall to `replay_http_port`. Empty so the role is runnable alone; **the playbook supplies the control host.** |
| `producer_nodes_root` | `/var/log/alice-replay-root` | Stands in for the engine's `NODES_ROOT`. Holds exactly one symlink, `<node_id>` → `log_root`. |
| `producer_aws_dir` | `/root/.aws` | Credentials directory, `0700`. |
| `producer_aws_credentials_file` | `{{ producer_aws_dir }}/credentials` | `AWS_SHARED_CREDENTIALS_FILE`. |
| `producer_autostart_marker` | `{{ log_root }}/.replay-autostart-done` | The engine's once-per-lifetime guard. Lives in `log_root` because, on this VM, `log_root` *is* this partition's `NODES_ROOT/<collector>` directory. |
| `producer_autostart_families` | `infologger,dds,stdout` | What a first-boot autostart would load. |
| `producer_autostart_replay` | `false` | Arms the producer without firing it. See "Armed, not fired". |
| `producer_replay_source` | `{{ role_path }}/files/replay.py` | Overridden in `group_vars` to the repository's one preserved copy. |

### Variables the role requires but does not own

| Group | Variables | Where it lands |
|---|---|---|
| Topology | `node_id`, `epn_partition` (inventory), `node_count` | The symlink name, `EPN_PARTITION`, and the partition formula |
| S3 | `s3_endpoint`, `s3_bucket`, `s3_region`, `s3_aws_profile`, `run_tag`, `infologger_prefix` | Which archive is replayed |
| Secrets | `s3_access_key_id`, `s3_secret_access_key` | The credentials file, from `vault.yml` |
| Paths | `log_root`, `producers_app_root`, `producers_venv_path` | Where files land and which interpreter runs |
| Ports | `replay_http_port`, `infologger_tcp_port` | The trigger, the firewall rule, and where InfoLogger rows go |
| Pacing | `il_replay_rate`, `dds_replay_rate`, `stdout_replay_rate`, `*_max_objects`, `replay_max_object_bytes` | Unit defaults, overridden at run time by `clock.conf` |
| Clock and loop | `replay_clock`, `replay_clock_cache`, `replay_loop`, `replay_loop_pause_seconds` | Unit defaults, same override path |

## How to use it

```yaml
- name: S3-replay producers (worker VMs only)
  hosts: workers
  become: true
  vars_files:
    - "{{ playbook_dir }}/../group_vars/vault.yml"
  roles:
    - producer
```

- **`producer_allowed_client_addresses` comes from `group_vars/all.yml`.** The
  role default is empty, so a play that supplies nothing installs a producer
  whose trigger port answers only the loopback path `playbooks/replay.yml` uses —
  and the `/ops` page's button, which calls the external address, times out.
- **The vault file is not optional.** Without `vault_s3_access_key_id` and its
  pair the credentials template fails, and `no_log` means the error will not tell
  you which variable was missing.
- **Run it after `collector`.** Not for correctness — the ordering is soft — but
  so the first triggered pass has somewhere to be read from.
- **Every worker needs a distinct `epn_partition`, numbered `0 .. node_count-1`.**
  Two workers sharing a partition replay the same hosts twice and every rate and
  volume signal doubles.
- **Load the data with `make replay`.** Use `make replay-fresh` to wipe the log
  indices and derived state first; use `make replay-fast` only when you want the
  data present and do not care whether the detectors can train on it.
- **Check a worker by hand:**
  `curl -s http://<worker>:8088/health`, then
  `journalctl -u alice-replay -f`.

## Couplings

- **`node_count` is `groups['workers'] | length`, and `epn_partition` is per
  host.** Adding a worker re-slices the archive for every existing worker. Their
  local `application-logs-local-<node>` indices then hold a different set of EPN hosts
  than before, so a comparison across the change is not a comparison of like with
  like. Re-slicing means a `make replay-fresh`, not a `make replay`.
- **`log_root` is shared with `collector`.** This role writes DDS and stdout
  files into it through the symlink; Fluent Bit tails them out of it. Changing it
  in one role and not the other produces a healthy producer, a healthy collector,
  and no data.
- **`infologger_tcp_port` is shared with `collector`.** The wrapper connects to
  `127.0.0.1` on it; Fluent Bit listens on it. Same failure mode as above.
- **`replay_http_port` is read in four places.** The unit and the firewall rule
  here, `worker_replay_trigger_urls` and `worker_replay_endpoints` in
  `group_vars`, and through those the `/ops` page's replay button and the
  `replay-end` injection scenario.
- **`producer_replay_source` points outside the role.** The `group_vars`
  override reaches `images/replay/replay.py`, which `docker-compose.yml` also
  builds from. Editing that file changes both stacks. If the role is ever
  extracted, drop the override and the bundled `files/replay.py` default takes
  over.
- **`clock.conf` is owned by `playbooks/replay.yml`, not by this role.** Anything
  that makes the role write it re-introduces the soak-killing behaviour described
  above. The unit's `Environment=` lines are defaults for a host that has never
  been triggered.
- **`replay_clock` and `replay_clock_cache` must move together.** The cache holds
  an offset computed for one archive. Both this role and the replay playbook drop
  it whenever the clock is not `shifted`, and both must keep doing so.
- **The paced rates derive from `il_max_objects` and `run_tag`.** 94 rows/s is
  `~338k / 3600`. Change either variable and the arithmetic in
  `group_vars/all.yml` has to be re-derived, or the pass stops being an hour long
  and the detectors stop getting their 32 windows.
- **The firewall rule takes `producer_allowed_client_addresses`, a list.** Same
  convention as `alertmanager_allowed_client_addresses` and
  `shifter_allowed_client_addresses`. `group_vars` resolves it to
  `[control_host_address]`, so the role names no inventory group and does not
  require a `control` group to exist. Opening the trigger to a second caller is
  one line in `group_vars`.

## What is frozen

Pacing, caps, ports, paths and the clock mode are fully parameterised — they are
configuration, and `playbooks/replay.yml` changes them per run. These are not
knobs and must not become knobs:

- **`images/replay/replay.py` itself.** It is preserved ground truth shared with
  the compose stack. Every divergence belongs in the wrapper.
- **The partition formula `epn_num % NODE_COUNT == EPN_PARTITION`.** It is the
  engine's own, and the local-index layout, the per-node retention and every
  per-host detector assume the same slicing.
- **InfoLogger to `127.0.0.1`, never a hostname.** Locked topology: a worker's
  logs are that worker's. A configurable target would let one VM's replay feed
  another VM's collector, which is exactly what the wrapper exists to prevent.
- **The two-file split.** Engine verbatim, divergences in the wrapper. Merging
  them would make the next upstream change a manual diff.

## Upstream roles rejected

Searched August 2026. There is no vendor role, because there is no vendor
software: the thing being deployed is a Python file in this repository.

- [`linux-system-roles.systemd`](https://github.com/linux-system-roles/systemd)
  — a maintained wrapper for deploying unit files and managing units. It would
  replace two of our fifteen tasks, and it would not hold the `Environment=`
  block, which is where this role's thinking is.
- Capistrano-style generic application-deployment roles
  ([the `ansible-roles` topic](https://github.com/topics/ansible-roles?l=yaml)
  collects several) — built for release directories, symlinked `current` and
  rollback. We deploy two files from the repository at a known commit; a release
  model would add ceremony and no safety.
- No role exists for "venv plus boto3 plus one script", and the two tasks that
  would cover it (`ansible.builtin.pip` with `virtualenv`, and
  `ansible.builtin.copy`) are already one line each.

**Kept ours.** The boilerplate an upstream role could take is the unit file and
the venv — three tasks. The substance is the wrapper, the symlink that avoids an
engine edit, the vault-only credentials, the deliberate refusal to write
`clock.conf`, and a handler whose comment is longer than its body because
restarting this service has a cost. None of that is portable. **What would change
the answer:** replacing the bespoke engine with a packaged log replayer, at which
point this becomes an ordinary "install a daemon" role.

## Used by

- `playbooks/site.yml`, play "S3-replay producers (worker VMs only)", against
  `workers` — the only installer.
- `playbooks/replay.yml` (`make replay`, `replay-fresh`, `replay-fast`), which
  writes the runtime drop-in and posts the trigger.
- `alice_ops`, at run time: the `/ops` replay button, and the `replay-end`
  injection scenario, both through `worker_replay_endpoints`.

## Depends on

- `ansible.posix` for `firewalld`, already in `requirements.yml`.
- `common`, for firewalld and the baseline packages.
- `group_vars/vault.yml`, decrypted, for the two S3 secrets.
- `images/replay/replay.py`, on the controller — outside `deploy/`.
- `collector`, in practice: it owns `log_root` and listens on
  `infologger_tcp_port`. The producer will run without it and write into a void.
