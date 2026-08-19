#!/bin/sh
set -eu

# Apply OpenSearch index templates BEFORE any log is written, so every index is
# born with an EXPLICIT mapping (correct field types, chosen on purpose) instead
# of being auto-created by Fluent Bit's first write and typed by dynamic-mapping
# guesswork (first-write-wins, irreversible without a reindex).
#
# Runs from a throwaway curl container gated on OpenSearch health. node-01
# (Fluent Bit) waits for THIS service to finish (service_completed_successfully),
# which closes the create-index race: no producer writes until the templates
# exist. Templates only shape indices created AFTER they are applied, so this
# ordering is load-bearing, not cosmetic.
#
# Idempotent: PUTs replace in place (HTTP 200) and the legacy delete tolerates a
# 404, so a re-run on an existing cluster is a no-op. Safe on every boot.
#
# Structure (composable templates): ONE shared mapping component per schema,
# but a SEPARATE index template per family so settings can diverge by importance.
#   component  alice-logs-application-mappings    <- dds+stdout UNION, shared by BOTH
#                                                 application-logs-* index templates (DRY schema)
#   component  alice-logs-infologger-mappings <- real O2 InfoLogger 16-col schema
#   index-tpl  alice-logs-application-local   (application-logs-local)   composes application-mappings
#   index-tpl  alice-logs-application-central  (application-logs-central)  composes application-mappings
#   index-tpl  alice-logs-infologger     (infologger)         composes infologger-mappings
#
# SHARDS: 3 per index (one per node) so every index spreads across the whole
# 3-node cluster -> real distribution + query parallelism, not a single-node
# index on a 3-node cluster. number_of_shards is IMMUTABLE (needs reindex/split
# to change), so it is set deliberately now.
#
# REPLICAS scale with a family's value/query-load (replicas = read throughput +
# resilience; changeable live, unlike shards):
#   infologger        -> 2  (hot, high-query, high-value; 3 copies across 3 nodes)
#   application-logs-central -> 2  (warn/error/debug: the valuable, alert-driving tier)
#   application-logs-local  -> 1  (bulky routine info: durable, but no extra read copies)
#
# CODEC: zstd everywhere. It Pareto-beats best_compression (smaller AND faster),
# and codec only affects _source storage/retrieval (not search/aggregation), so
# there is no reason to keep even the hot index on the default LZ4 codec.
#
# DELIBERATELY NOT done here (see chat): (1) dedicated-storage node pinning —
# with only 3 nodes, pinning generic to 1 "worker" node would forbid its replicas
# from allocating and kill its distribution, directly conflicting with the 3-shard
# goal; it needs more nodes to be real. (2) ISM retention (info=short/other=long)
# — meaningful retention needs time-based or rollover-aliased indices, i.e. a
# collector write-path change, not a settings tweak on these static index names.

OS="${OS_URL:-http://opensearch:9200}"

# --- wait until the cluster can accept writes --------------------------------
wait_os() {
  echo "[os-init] waiting for OpenSearch at $OS ..."
  i=1
  while [ "$i" -le 60 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' \
      "$OS/_cluster/health?wait_for_status=yellow&timeout=5s" || echo 000)
    if [ "$code" = "200" ]; then
      echo "[os-init] OpenSearch ready (attempt $i)"
      return 0
    fi
    echo "[os-init] not ready (HTTP $code), retry $i/60"
    i=$((i + 1))
    sleep 3
  done
  echo "[os-init] FATAL: OpenSearch never became ready"
  return 1
}

# --- helpers -----------------------------------------------------------------
# put <path> <json> <name>: retry a few times, fail HARD if it never applies
# (a missing mapping must block producers, not silently let dynamic mapping win).
put() {
  path="$1"; body="$2"; name="$3"
  i=1
  while [ "$i" -le 5 ]; do
    code=$(curl -s -o /tmp/resp -w '%{http_code}' \
      -XPUT "$OS$path" -H 'Content-Type: application/json' -d "$body" || echo 000)
    case "$code" in
      200|201) echo "[os-init] applied: $name"; return 0 ;;
      *) echo "[os-init] WARN: $name -> HTTP $code: $(cat /tmp/resp 2>/dev/null); retry $i/5" ;;
    esac
    i=$((i + 1)); sleep 2
  done
  echo "[os-init] FATAL: could not apply $name"
  return 1
}

# del <path> <name>: best-effort delete, a 404 (already gone) is success.
del() {
  path="$1"; name="$2"
  code=$(curl -s -o /tmp/resp -w '%{http_code}' -XDELETE "$OS$path" || echo 000)
  case "$code" in
    200|404) echo "[os-init] removed (or absent): $name" ;;
    *) echo "[os-init] WARN: delete $name -> HTTP $code: $(cat /tmp/resp 2>/dev/null)" ;;
  esac
}

# --- payloads ----------------------------------------------------------------
# The `dynamic` policy differs by schema shape:
#   generic  -> dynamic:false  (heterogeneous, regex-parsed, SPARSE fields; real
#               stdout is ~99% free-form. Undeclared fields are kept in _source,
#               visible in Discover, just not indexed -> never drop a log, never
#               explode the mapping.)
#   infologger-> dynamic:strict (a closed 16-col DB table; every real row is
#               exactly those columns, so an unexpected field is a pipeline bug
#               and is rejected loudly.)
#
# text vs keyword: only `message` is free text (full-text search). Everything we
# filter/aggregate on (severity, host, facility, ...) is keyword — exact match,
# aggregatable, and half the storage of an analyzed text+keyword pair.

# Shared by BOTH application-logs-* indices: the UNION of dds and stdout fields.
#   shared : @timestamp, node, severity, message, host
#   dds    : source, tid
#   stdout : facility
# node vs host: `node` = the Fluent Bit collector/VM that ingested the record
# (stamped from NODE_ID); `host` = the EPN the log was born on (from the per-host
# file path). One collector fronts many hosts, so they differ (aggregator model).
APPLICATION_MAPPINGS=$(cat <<'JSON'
{
  "template": {
    "mappings": {
      "dynamic": false,
      "properties": {
        "@timestamp":  { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "ingest_time": { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "log_source": { "type": "keyword" },
        "node":       { "type": "keyword" },
        "severity":   { "type": "keyword" },
        "host":       { "type": "keyword" },
        "source":     { "type": "keyword" },
        "tid":        { "type": "keyword" },
        "facility":   { "type": "keyword" },
        "message":    { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 1024 } } }
      }
    }
  },
  "_meta": {
    "family": "application-logs",
    "sources": ["dds", "stdout"],
    "note": "union of dds (+source,+tid) and stdout (+facility); dynamic:false keeps surprises in _source without exploding the mapping"
  }
}
JSON
)

# The real O2 InfoLogger `messages` table: 16 columns, `timestamp` promoted to
# @timestamp by the collector (lua + il_event_time parser), so 15 remain + time.
INFOLOGGER_MAPPINGS=$(cat <<'JSON'
{
  "template": {
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "@timestamp":  { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "ingest_time": { "type": "date", "format": "strict_date_optional_time||epoch_millis" },
        "log_source": { "type": "keyword" },
        "node":       { "type": "keyword" },
        "severity":   { "type": "keyword" },
        "level":      { "type": "short" },
        "hostname":   { "type": "keyword" },
        "rolename":   { "type": "keyword" },
        "pid":        { "type": "integer" },
        "username":   { "type": "keyword" },
        "system":     { "type": "keyword" },
        "facility":   { "type": "keyword" },
        "detector":   { "type": "keyword" },
        "partition":  { "type": "keyword" },
        "run":        { "type": "long" },
        "errcode":    { "type": "long" },
        "errline":    { "type": "integer" },
        "errsource":  { "type": "keyword" },
        "message":    { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 1024 } } }
      }
    }
  },
  "_meta": {
    "family": "infologger",
    "note": "types mirror the real mysqldump CREATE TABLE (verified against s3 infologger-2026 dumps): tinyint->short, mediumint->integer, int unsigned->long (exceeds es integer's signed 2.1B), smallint unsigned->integer. dynamic:strict is safe+protective: every real row is exactly the 16 DB columns plus the collector-stamped fields (@timestamp, ingest_time, log_source, node), so an unexpected field means a pipeline bug and should fail loud. `node` must be declared here BECAUSE strict: the collector stamps it from NODE_ID and strict mode would otherwise reject the write."
  }
}
JSON
)

# application-logs-local : high-volume routine info, the LEAST valuable tier.
#   * 3 shards (spread across the cluster), 1 replica (durable, no extra copies).
APPLICATION_LOCAL_TPL=$(cat <<'JSON'
{
  "index_patterns": ["application-logs-local"],
  "composed_of": ["alice-logs-application-mappings"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 3,
      "number_of_replicas": 1,
      "codec": "zstd",
      "index.default_pipeline": "alice-add-ingest-time"
    }
  },
  "_meta": { "note": "least-valuable tier: 1 replica; short retention (ISM, still to come)" }
}
JSON
)

# application-logs-central : warn/error/debug -> the valuable, alert-driving tier.
#   * 3 shards, 2 replicas (extra durability + read throughput during incidents).
APPLICATION_CENTRAL_TPL=$(cat <<'JSON'
{
  "index_patterns": ["application-logs-central"],
  "composed_of": ["alice-logs-application-mappings"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 3,
      "number_of_replicas": 2,
      "codec": "zstd",
      "index.default_pipeline": "alice-add-ingest-time"
    }
  },
  "_meta": { "note": "valuable warn/error/debug: 2 replicas; long retention (ISM, still to come)" }
}
JSON
)

# infologger : hot, high-query, high-value.
#   * 3 shards spread across all nodes; 2 replicas (3 copies) for read throughput
#     + resilience; zstd (search speed is unaffected by codec).
INFOLOGGER_INDEX_TPL=$(cat <<'JSON'
{
  "index_patterns": ["infologger"],
  "composed_of": ["alice-logs-infologger-mappings"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 3,
      "number_of_replicas": 2,
      "codec": "zstd",
      "index.default_pipeline": "alice-add-ingest-time"
    }
  },
  "_meta": { "note": "hot tier: 3 shards, 2 replicas (3 copies) for read throughput + resilience" }
}
JSON
)

# TWO-TIMESTAMP MODEL. Logs carry two distinct times and conflating them is the
# "timestamps not respected" bug:
#   @timestamp  = EVENT time — when the message was logged AT SOURCE. Extracted by
#                 the collector (dds_text/il_event_time parsers). This is what you
#                 sort/scrub by in Discover.
#   ingest_time = when OpenSearch actually RECEIVED the doc. Stamped here, server-
#                 side, from `_ingest.timestamp` — immune to producer/collector
#                 clock skew, and it lets you measure source->OS latency
#                 (ingest_time - @timestamp) and spot backfilled/replayed data.
# Wired as each index's `index.default_pipeline`, so EVERY write runs it with no
# collector change. Must exist BEFORE the first write (default_pipeline is resolved
# at index time), hence it is applied ahead of the index templates below.
INGEST_PIPELINE=$(cat <<'JSON'
{
  "description": "Stamp ingest_time = the moment OpenSearch received the doc (distinct from @timestamp = source/event time). Two-timestamp model.",
  "processors": [
    { "set": { "field": "ingest_time", "value": "{{{_ingest.timestamp}}}" } }
  ]
}
JSON
)

# --- apply -------------------------------------------------------------------
wait_os

# Retire superseded templates before ours: overlapping index_patterns at equal
# priority is a 400. `alice-logs` = the Flight-1 settings-only template;
# `alice-logs-generic` = the earlier single generic template now split in two
# (it matched application-logs-local, which our new -application-logs-local template also matches).
del "/_index_template/alice-logs"         "legacy index template alice-logs"
del "/_index_template/alice-logs-generic" "superseded index template alice-logs-generic"

# Ingest pipeline first: index templates below reference it as default_pipeline,
# and it must exist before the first write creates an index (else writes error).
put "/_ingest/pipeline/alice-add-ingest-time" "$INGEST_PIPELINE" "ingest pipeline alice-add-ingest-time"

# Component templates first (index templates reference them via composed_of).
put "/_component_template/alice-logs-application-mappings"    "$APPLICATION_MAPPINGS"    "component alice-logs-application-mappings"
put "/_component_template/alice-logs-infologger-mappings" "$INFOLOGGER_MAPPINGS" "component alice-logs-infologger-mappings"

# Index templates: one per family so settings diverge by importance.
put "/_index_template/alice-logs-application-local"  "$APPLICATION_LOCAL_TPL"     "index template alice-logs-application-local"
put "/_index_template/alice-logs-application-central" "$APPLICATION_CENTRAL_TPL"    "index template alice-logs-application-central"
put "/_index_template/alice-logs-infologger"    "$INFOLOGGER_INDEX_TPL" "index template alice-logs-infologger"

echo "[os-init] DONE — explicit mappings in place before first write"
