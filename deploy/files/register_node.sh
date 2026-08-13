#!/bin/sh
set -eu

# The single definition of a worker's three cluster objects: its index
# template, its retention attachment, and its write alias. Run by templates.sh
# once per worker during the deploy, and by fluent-bit.service as ExecStartPre
# on the machine itself at every boot. README section 9, Item 3.

NODE="${ALICE_NODE_ID:-}"
OS="${OS_URL:-http://localhost:${ALICE_OS_HTTP_PORT:-9200}}"

# Set by the deploy, where an unreachable cluster should stop the run. Unset at
# boot, so a slow local OpenSearch never holds back the storage-tier lane.
STRICT="${REGISTER_STRICT:-false}"

# Passed through from log_rollover_migrate_existing by the deploy only. Deleting
# a squatting concrete index is never a boot-time decision.
MIGRATE_EXISTING="${REGISTER_MIGRATE_EXISTING:-false}"

# ExecStartPre counts against the unit's TimeoutStartSec. Change both together
# or systemd kills a collector that was only waiting.
WAIT_ATTEMPTS="${REGISTER_WAIT_ATTEMPTS:-30}"
WAIT_SLEEP="${REGISTER_WAIT_SLEEP:-3}"
WAIT_MAX_TIME="${REGISTER_WAIT_MAX_TIME:-10}"

IDLE_AFTER="${ALICE_INFO_SEARCH_IDLE_AFTER:-10s}"
TRANSLOG_SYNC="${ALICE_INFO_TRANSLOG_SYNC_INTERVAL:-30s}"
MERGE_THREADS="${ALICE_INFO_MERGE_THREADS:-1}"
RETENTION_POLICY="${ALICE_INFO_RETENTION_POLICY:-alice-generic-info-retention}"

CURL="curl -s --connect-timeout ${OS_CONNECT_TIMEOUT:-5} --max-time ${OS_MAX_TIME:-60}"

say() { echo "[register-node] $*"; }

# The one failure that must stop the collector: Fluent Bit expands an unset
# variable to an empty string silently, so this would write to
# "generic-log-info-" and the auto-create guard would reject every record.
if [ -z "$NODE" ]; then
  echo "[register-node] FATAL: ALICE_NODE_ID is empty or unset." >&2
  echo "[register-node] FATAL: This node cannot know which index is its own." >&2
  echo "[register-node] FATAL: Ansible writes it into /etc/alice-ingest/node.env" >&2
  echo "[register-node] FATAL: from the inventory's node_id. Re-run the collector role." >&2
  exit 1
fi

ALIAS="generic-log-info-$NODE"

give_up() {
  if [ "$STRICT" = "true" ]; then
    echo "[register-node] FATAL: $1" >&2
    exit 1
  fi
  say "WARN: $1"
  say "WARN: $ALIAS is not registered. This node's own log tier will reject"
  say "WARN: writes until it is. infologger and generic-log-other are"
  say "WARN: unaffected — they go to the storage tier. Retried at next boot."
  exit 0
}

wait_os() {
  i=1
  while [ "$i" -le "$WAIT_ATTEMPTS" ]; do
    code=$(curl -s --connect-timeout 5 --max-time "$WAIT_MAX_TIME" -o /dev/null -w '%{http_code}' \
      "$OS/_cluster/health?wait_for_status=yellow&timeout=5s" || echo 000)
    if [ "$code" = "200" ]; then
      return 0
    fi
    i=$((i + 1))
    [ "$i" -le "$WAIT_ATTEMPTS" ] && sleep "$WAIT_SLEEP"
  done
  return 1
}

put() {
  path="$1"; body="$2"; name="$3"
  i=1
  while [ "$i" -le 5 ]; do
    code=$($CURL -o /tmp/register-node-resp -w '%{http_code}' \
      -XPUT "$OS$path" -H 'Content-Type: application/json' -d "$body" || echo 000)
    case "$code" in
      200|201) say "applied: $name"; return 0 ;;
      *) say "WARN: $name -> HTTP $code: $(cat /tmp/register-node-resp 2>/dev/null); retry $i/5" ;;
    esac
    i=$((i + 1)); sleep 2
  done
  return 1
}

# refresh_interval is deliberately absent and MUST stay absent. Without it a
# shard refreshes every second until no search touches it for
# index.search.idle.after, then goes idle and stops. Setting an explicit
# interval silently turns search idle off — it reads as a tuning improvement and
# is the opposite of one. See README section 9, Item 4.2 for the rest.
index_template_body() {
  printf '{
  "index_patterns": ["generic-log-info-%s-*"],
  "composed_of": ["alice-logs-generic-mappings"],
  "priority": 300,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "codec": "zstd",
      "index.default_pipeline": "alice-add-ingest-time",
      "index.routing.allocation.require.box": "%s",
      "index.search.idle.after": "%s",
      "index.translog.durability": "async",
      "index.translog.sync_interval": "%s",
      "index.merge.scheduler.max_thread_count": %s,
      "index.search.concurrent_segment_search.mode": "none",
      "index.plugins.index_state_management.rollover_alias": "generic-log-info-%s"
    }
  },
  "_meta": { "note": "worker tier: local, disposable, rolled daily, pinned by require.box; registered by register_node.sh" }
}' "$NODE" "$NODE" "$IDLE_AFTER" "$TRANSLOG_SYNC" "$MERGE_THREADS" "$NODE"
}

attach_policy() {
  code=$($CURL -o /tmp/register-node-ism -w '%{http_code}' \
    -XPOST "$OS/_plugins/_ism/add/$ALIAS-*" \
    -H 'Content-Type: application/json' \
    -d "{\"policy_id\":\"$RETENTION_POLICY\"}" || echo 000)
  body=$(cat /tmp/register-node-ism 2>/dev/null || true)
  case "$code" in
    200|201) say "attached $RETENTION_POLICY -> $ALIAS-*" ;;
    *)
      case "$body" in
        *already*|*managed*|*exists*)
          say "already managed: $ALIAS-* ($RETENTION_POLICY)"
          ;;
        *)
          # ism.sh is the authoritative attach and runs later, so on a fresh
          # cluster the policy does not exist yet. Not a hole: the policy's own
          # ism_template claims new generic-log-info-* indices at creation.
          say "WARN: attach $RETENTION_POLICY -> $ALIAS-* HTTP $code: $body"
          ;;
      esac
      ;;
  esac
}

# Repair, not just creation: after a reinstall the alias still points at a
# backing index whose shards died with the disk, so create-if-absent passes and
# every write still fails. Roll rather than delete — red can also mean
# "recovering right now", and deleting that destroys data that was coming back.
repair_write_index() {
  write_index=$($CURL "$OS/_cat/aliases/$ALIAS?h=index,is_write_index" 2>/dev/null \
    | awk '$2 == "true" { print $1 }' | head -n 1)
  if [ -z "$write_index" ]; then
    say "WARN: $ALIAS has no write index; rolling over to create one"
  else
    status=$($CURL "$OS/_cluster/health/$write_index?timeout=5s" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("status", "unknown"))
except Exception:
    print("unknown")' 2>/dev/null || echo unknown)
    if [ "$status" != "red" ]; then
      say "write alias healthy (skip): $ALIAS -> $write_index ($status)"
      return 0
    fi
    say "$ALIAS -> $write_index is red; its shards did not survive. Rolling over."
  fi
  code=$($CURL -o /tmp/register-node-roll -w '%{http_code}' \
    -XPOST "$OS/$ALIAS/_rollover" -H 'Content-Type: application/json' -d '{}' || echo 000)
  case "$code" in
    200|201) say "rolled $ALIAS onto a fresh write index" ;;
    *)
      say "WARN: could not roll $ALIAS -> HTTP $code: $(cat /tmp/register-node-roll 2>/dev/null)"
      say "WARN: this node's log tier stays unwritable. Fix by hand with:"
      say "WARN:   curl -XPOST '$OS/$ALIAS/_rollover' -H 'Content-Type: application/json' -d '{}'"
      ;;
  esac
}

ensure_write_alias() {
  existing=$($CURL "$OS/_cat/aliases/$ALIAS?h=index" 2>/dev/null || true)
  if [ -n "$existing" ]; then
    repair_write_index
    return 0
  fi
  icode=$($CURL -o /dev/null -w '%{http_code}' "$OS/$ALIAS" || echo 000)
  if [ "$icode" = "200" ]; then
    if [ "$MIGRATE_EXISTING" = "true" ]; then
      say "MIGRATING: '$ALIAS' is a legacy concrete index; deleting it so the write alias can take the name"
      dcode=$($CURL -o /dev/null -w '%{http_code}' -XDELETE "$OS/$ALIAS" || echo 000)
      case "$dcode" in
        200|404) say "removed (or absent): concrete index $ALIAS" ;;
        *) give_up "could not delete the concrete index $ALIAS (HTTP $dcode)" ;;
      esac
    elif [ "$STRICT" = "true" ]; then
      # Ingest auto-created it before the alias existed, so its fields come from
      # dynamic mapping: collector_time lands as long instead of date and host
      # as text. That breaks the dual-clock mapping and every terms aggregation
      # on host, and rollover is inactive for the family. Failing here is
      # deliberate — it used to limp on and die further down with an
      # unrelated-looking mapper error.
      echo "[register-node] FATAL: '$ALIAS' is a concrete index, not a rollover write alias." >&2
      echo "[register-node] FATAL: Fix it once with:" >&2
      echo "[register-node] FATAL:" >&2
      echo "[register-node] FATAL:   make deploy-migrate-rollover" >&2
      echo "[register-node] FATAL:" >&2
      echo "[register-node] FATAL: which deletes '$ALIAS' and rebuilds it properly. The data in" >&2
      echo "[register-node] FATAL: it is replayable, so this is safe. A fresh replay also" >&2
      echo "[register-node] FATAL: self-heals this — reset_derived.py drops any log family that" >&2
      echo "[register-node] FATAL: is a concrete index before templates.sh runs." >&2
      exit 1
    else
      say "WARN: '$ALIAS' is a concrete index, not a write alias."
      say "WARN: Run 'make deploy-migrate-rollover' to rebuild it properly."
      return 0
    fi
  fi
  put "/$ALIAS-000001" \
    "$(printf '{"aliases":{"%s":{"is_write_index":true}}}' "$ALIAS")" \
    "first rollover index $ALIAS-000001 (write alias $ALIAS)" \
    || give_up "could not create $ALIAS-000001"
}

say "registering $NODE against $OS"

wait_os || give_up "OpenSearch at $OS never became ready"

put "/_index_template/alice-logs-generic-info-$NODE" "$(index_template_body)" \
  "index template alice-logs-generic-info-$NODE" \
  || give_up "could not put the index template for $NODE"

attach_policy
ensure_write_alias

say "DONE — $NODE owns $ALIAS"
