# Prefer `docker compose` (plugin); fall back to standalone `docker-compose`.
COMPOSE       := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
# Force the pretty (colour + in-place) progress renderer. The overlap buffers
# compose to a file, so compose would otherwise see a non-tty and fall back to
# the verbose plain log; these flags keep the nice output, and the buffered ANSI
# replays cleanly through `tail`.
COMPOSE       += --ansi always --progress tty
COMPOSE_REAL  := $(COMPOSE) -f docker-compose.yml
COMPOSE_MOCKS := $(COMPOSE) -f docker-compose.mocks.yaml
COMPOSE_ALL   := $(COMPOSE) -f docker-compose.yml -f docker-compose.mocks.yaml

.PHONY: run mocks down volume volumes

# Splash screens (skip silently when stdout is not a tty, e.g. CI).
IGNITION := bash assets/collide.sh
DESCENT  := bash assets/shiva.sh

# overlap: run compose command $(1) CONCURRENTLY with splash $(2). Compose runs
# in the background with output buffered (so it can't corrupt the animation);
# once the splash ends we stream the buffered + live output until compose exits.
# Compose's exit code is propagated, so `make` still fails on a compose error.
define overlap
@log=$$(mktemp); ( $(1) ) >$$log 2>&1 & cpid=$$!; $(2); tail -n +1 -f $$log & tpid=$$!; wait $$cpid; rc=$$?; sleep 0.3; kill $$tpid 2>/dev/null; wait $$tpid 2>/dev/null; rm -f $$log; exit $$rc
endef

# Default: real/S3 replay stack (docker-compose.yml, no mocks).
#   make run
run:
ifeq (mocks,$(filter mocks,$(MAKECMDGOALS)))
	@:
else
	$(call overlap,$(COMPOSE_REAL) up -d,$(IGNITION))
endif

# Teardown: `down` tears down BOTH stacks (real + mocks) via the merged config,
# so no orphans survive regardless of which mode was up.
#   make down                -> down both stacks (+ --remove-orphans)
#   make down volume         -> ... and remove ALL volumes (osdata, flb-storage, nodelogs)
#   make down mocks [volume]  -> same total teardown (kept for muscle memory)
down:
ifneq (,$(filter volume volumes,$(MAKECMDGOALS)))
	$(call overlap,$(COMPOSE_ALL) down -v --remove-orphans,$(DESCENT))
else
	$(call overlap,$(COMPOSE_ALL) down --remove-orphans,$(DESCENT))
endif

# Mock-only stack (no S3, no replay).
#   make mocks               -> up
#   make down mocks [volume]  -> handled by the unified `down` target
mocks:
ifeq (down,$(filter down,$(MAKECMDGOALS)))
	@:                      # `down` already tore everything down
else
	$(call overlap,$(COMPOSE_MOCKS) up -d,$(IGNITION))
endif

# Modifier goal for `make down [mocks] volume|volumes` (no-op on its own).
# Both spellings are accepted so `make down volumes` works too.
volume volumes:
	@:

.PHONY: bootstrap provision deploy replay replay-fresh replay-fast replay-loop replay-shifted poison poison-replay posion-replay poison-status poison-stop backtest teardown status inject roster-discover monitors contract

# Control-node toolchain lives in a self-contained venv (override with VENV=...).
# The deploy targets prefer it, but fall back to an already-activated venv on PATH
# so `make bootstrap` is convenient without being mandatory.
VENV ?= $(CURDIR)/.venv
ANSIBLE_PLAYBOOK := $(if $(wildcard $(VENV)/bin/ansible-playbook),$(VENV)/bin/ansible-playbook,ansible-playbook)

bootstrap:
	python3 -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -r deploy/requirements.txt
	cd deploy && $(VENV)/bin/ansible-galaxy collection install -r requirements.yml

provision:
	cd deploy && $(ANSIBLE_PLAYBOOK) provision.yml

# Converges rather than giving up. Every play is idempotent, and site.yml opens
# with a pre-flight that hard-reboots a node it cannot reach, so a pass that
# dies because a node ran out of memory is repaired and resumed by the next one.
# The vault password is read once and held in a 0600 file on tmpfs (never AFS),
# removed on exit, so the retries do not re-prompt.
DEPLOY_ATTEMPTS ?= 2

deploy:
	@vpf=$$(mktemp $${XDG_RUNTIME_DIR:-/dev/shm}/alice-vault.XXXXXX 2>/dev/null || mktemp); \
	chmod 600 "$$vpf"; \
	trap 'rm -f "$$vpf"' EXIT INT TERM; \
	printf 'Vault password: ' >&2; stty -echo 2>/dev/null; \
	IFS= read -r vp; stty echo 2>/dev/null; printf '\n' >&2; \
	printf '%s\n' "$$vp" > "$$vpf"; unset vp; \
	rc=1; \
	for i in $$(seq 1 $(DEPLOY_ATTEMPTS)); do \
	  if [ "$$i" -gt 1 ]; then \
	    printf '\n== deploy pass %s of %s — repairing and resuming ==\n\n' "$$i" "$(DEPLOY_ATTEMPTS)" >&2; \
	  fi; \
	  if (cd deploy && $(ANSIBLE_PLAYBOOK) site.yml --vault-password-file "$$vpf" $(ANSIBLE_EXTRA)); then \
	    rc=0; break; \
	  fi; \
	done; \
	if [ "$$rc" -ne 0 ]; then \
	  printf '\ndeploy did not converge in %s passes — the last run above holds the reason\n' "$(DEPLOY_ATTEMPTS)" >&2; \
	fi; \
	exit $$rc

deploy-migrate-rollover:
	cd deploy && $(ANSIBLE_PLAYBOOK) site.yml --ask-vault-pass -e log_rollover_migrate_existing=true

# Paced by default: the archive is stretched over roughly an hour so the log
# detectors get the 32 consecutive one-minute windows they need to finish
# training. Use replay-fast for the old ten-minute dump when you only want the
# data loaded and do not care about the detection lane.
replay:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml

replay-fresh:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml -e replay_fresh=true

replay-fast:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml -e replay_pace=fast

# Never returns on its own: each pass is followed by another, so collector_time
# never stalls and the detectors stay Running. Stop it with
# `systemctl restart alice-replay` on the workers.
replay-loop:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml -e replay_loop=true

# Rewrites every event timestamp so @timestamp lands near now. Costs you the
# EPN -> collector latency measurement and pushes the archive's later months
# into the future. Not needed for detection — see replay_clock in group_vars.
replay-shifted:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml -e replay_clock=shifted

# Starts a background calibration run on the control VM. The real paced replay
# supplies/trains the baseline; the harness waits for all ten one-minute
# detectors, injects labelled outlier windows into already-modelled entities,
# and scores native AD results, projected episodes, and probe monitors. The
# seven 30-minute detectors are deliberately excluded.
poison-replay:
	cd deploy && $(ANSIBLE_PLAYBOOK) poison_replay.yml $(ANSIBLE_EXTRA)

# Short operator-facing spelling.
poison: poison-replay

# Keep the spelling from the original operator request as a harmless alias.
posion-replay: poison-replay

poison-status:
	cd deploy && $(ANSIBLE_PLAYBOOK) poison_status.yml $(ANSIBLE_EXTRA)

poison-stop:
	cd deploy && $(ANSIBLE_PLAYBOOK) poison_stop.yml $(ANSIBLE_EXTRA)

# Runs each log detector over the window already sitting in the indices and
# writes what it finds into the cockpit's anomaly index. Does not touch the
# real-time detectors.
backtest:
	cd deploy && $(ANSIBLE_PLAYBOOK) backtest.yml $(ANSIBLE_EXTRA)

SCENARIO ?= kill-fluent-bit
OBSERVE  ?= 45
inject:
	cd deploy && $(ANSIBLE_PLAYBOOK) inject.yml \
	  -e scenario=$(SCENARIO) -e observe_minutes=$(OBSERVE) $(ANSIBLE_EXTRA)

roster-discover:
	cd deploy && $(ANSIBLE_PLAYBOOK) roster_discover.yml $(ANSIBLE_EXTRA)

monitors:
	python3 deploy/roles/dashboards/files/gen_monitors.py

contract:
	python3 deploy/roles/dashboards/files/test_poison_replay.py
	python3 deploy/roles/dashboards/files/test_signal_contract.py

status:
	cd deploy && $(ANSIBLE_PLAYBOOK) status.yml

teardown:
	cd deploy && $(ANSIBLE_PLAYBOOK) teardown.yml
