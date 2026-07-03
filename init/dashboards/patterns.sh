#!/bin/sh
set -eu

# Provision OpenSearch Dashboards index patterns so `make run` needs no manual
# clicking. Runs once per stack start from a throwaway curl container, then exits.
# Fixed saved-object IDs make every POST idempotent: a re-run just gets HTTP 409
# ("already exists"), which we treat as success — safe on every boot.

OSD="${OSD_URL:-http://opensearch-dashboards:5601}"
OS="${OS_URL:-http://opensearch:9200}"
PATTERNS="infologger generic-log-info generic-log-other"
TIME_FIELD="@timestamp"

create_pattern() {
  id="$1"
  code=$(curl -s -o /tmp/resp -w '%{http_code}' \
    -XPOST "$OSD/api/saved_objects/index-pattern/$id" \
    -H 'osd-xsrf: true' -H 'Content-Type: application/json' \
    -d "{\"attributes\":{\"title\":\"$id\",\"timeFieldName\":\"$TIME_FIELD\"}}")
  case "$code" in
    200|201) echo "[init] created index-pattern: $id" ;;
    409)     echo "[init] index-pattern already exists: $id" ;;
    *)       echo "[init] WARN: $id -> HTTP $code: $(cat /tmp/resp)" ;;
  esac
}

# Dashboards reports "started" to compose BEFORE its saved-objects API is live,
# so depends_on alone races. Poll GET /api/status until HTTP 200. Best-effort:
# if it never comes up within the budget we still try to create the patterns
# (they refresh lazily in Discover) rather than fail the whole stack.
wait_ready() {
  echo "[init] waiting for Dashboards API at $OSD ..."
  i=1
  while [ "$i" -le 60 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$OSD/api/status" || echo 000)
    if [ "$code" = "200" ]; then
      echo "[init] Dashboards ready (attempt $i)"
      return 0
    fi
    echo "[init] Dashboards not ready (HTTP $code), retry $i/60"
    i=$((i + 1))
    sleep 3
  done
  echo "[init] WARN: Dashboards not ready after 60 tries; proceeding best-effort"
  return 0
}

# Best-effort wait for an index to exist so @timestamp is mapped when the
# pattern is created (populated field list up front). Bounded so a still-empty
# index — e.g. generic-log-other before any warning/error lands — never hangs us.
wait_index() {
  id="$1"
  j=1
  while [ "$j" -le 20 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$OS/$id" || echo 000)
    [ "$code" = "200" ] && return 0
    j=$((j + 1))
    sleep 3
  done
  return 1
}

wait_ready
for p in $PATTERNS; do
  if wait_index "$p"; then
    echo "[init] index present: $p"
  else
    echo "[init] index $p not present yet; creating pattern anyway"
  fi
  create_pattern "$p"
done
echo "[init] index-pattern provisioning done"
