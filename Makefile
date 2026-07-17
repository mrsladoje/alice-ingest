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

.PHONY: bootstrap provision deploy replay replay-fresh teardown

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

deploy:
	cd deploy && $(ANSIBLE_PLAYBOOK) site.yml --ask-vault-pass

replay:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml

replay-fresh:
	cd deploy && $(ANSIBLE_PLAYBOOK) replay.yml -e replay_fresh=true

teardown:
	cd deploy && $(ANSIBLE_PLAYBOOK) teardown.yml
