#!/usr/bin/env bash
# Build a replay bundle from a live EPN.
#
# InfoLogger, DDS and the process tree come out of the S3 archive. The
# InfoLogger daemon log, the journal and the run orchestrator's log do not —
# they are written on a live EPN and nothing backs them up — so the only way a
# staging VM can carry any of them is a capture taken from the farm and shipped
# with the deploy.
#
# Read-only on the node. Everything it reads needs sudo, because journalctl as
# an ordinary user shows only that user's own sessions and says so in a hint
# that is easy to miss.
#
#   tools/epnsurvey/mkbundle.sh epn146 ./bundle
#   REPLAY_BUNDLE_SRC=./bundle make deploy
set -euo pipefail

NODE="${1:?usage: mkbundle.sh <node> <out-dir> [ssh-config]}"
OUT="${2:?usage: mkbundle.sh <node> <out-dir> [ssh-config]}"
SSH_CONFIG="${3:-}"
JOURNAL_FILES="${JOURNAL_FILES:-1}"
# How many daily orchestrator files to take. The default is 2 rather than 1
# because one file is either a run or an idle day and almost never both, and a
# bundle carrying only idle days cannot exercise the partition and run fields
# at all.
ODC_FILES="${ODC_FILES:-2}"

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=30)
[ -n "$SSH_CONFIG" ] && ssh_opts+=(-F "$SSH_CONFIG")

say() { printf '[mkbundle] %s\n' "$*" >&2; }

mkdir -p "$OUT/ildaemon" "$OUT/journal" "$OUT/odc"

say "node=${NODE} out=${OUT}"

# --- the InfoLogger daemon log ----------------------------------------------
say "daemon log"
if ssh "${ssh_opts[@]}" "$NODE" 'sudo test -r /var/log/o2-infologger-daemon.log'; then
  ssh "${ssh_opts[@]}" "$NODE" 'sudo cat /var/log/o2-infologger-daemon.log' \
    > "$OUT/ildaemon/o2-infologger-daemon.log"
  say "daemon log: $(wc -l < "$OUT/ildaemon/o2-infologger-daemon.log") lines"
else
  say "daemon log: absent on ${NODE} (epn323 has none), skipping"
fi

# --- the journal -------------------------------------------------------------
#
# Copied as journal FILES, not as exported text. libsystemd reads the binary
# format, so a JSON export cannot be replayed through the systemd input — it
# would have to be re-parsed by something else, which is a different code path
# from the one production runs.
say "journal"
MACHINE_ID="$(ssh "${ssh_opts[@]}" "$NODE" 'cat /etc/machine-id')"
mkdir -p "$OUT/journal/$MACHINE_ID"
ssh "${ssh_opts[@]}" "$NODE" \
  "sudo find /var/log/journal -name 'system*.journal' -printf '%T@\t%p\n' 2>/dev/null | sort -rn | head -${JOURNAL_FILES} | cut -f2" \
  | grep '\.journal$' | while read -r remote; do
      base="$(basename "$remote")"
      say "  ${base}"
      ssh "${ssh_opts[@]}" "$NODE" "sudo cat '$remote'" \
        > "$OUT/journal/$MACHINE_ID/$base"
    done

# --- the run orchestrator ----------------------------------------------------
#
# Only the storage node runs it, so this is expected to find nothing on a
# worker. Newest files first: the current one is being written and is the one
# whose format is current.
say "orchestrator log"
ssh "${ssh_opts[@]}" "$NODE" \
  "sudo find /var/log/odc/staging -name 'odc_*.log' -printf '%T@\t%p\n' 2>/dev/null | sort -rn | head -${ODC_FILES} | cut -f2" \
  | grep '\.log$' | while read -r remote; do
      base="$(basename "$remote")"
      say "  ${base}"
      ssh "${ssh_opts[@]}" "$NODE" "sudo cat '$remote'" > "$OUT/odc/$base"
    done
if ! find "$OUT/odc" -name '*.log' | grep -q .; then
  say "orchestrator log: absent on ${NODE} (only the storage node runs it), skipping"
fi

cat > "$OUT/MANIFEST" <<EOF
node        $NODE
machine_id  $MACHINE_ID
captured    $(date -u +%Y-%m-%dT%H:%M:%SZ)
by          $(id -un)
daemon_log  $( [ -s "$OUT/ildaemon/o2-infologger-daemon.log" ] && wc -l < "$OUT/ildaemon/o2-infologger-daemon.log" || echo 0 ) lines
journal     $(find "$OUT/journal" -name '*.journal' | wc -l | tr -d ' ') files
odc         $(find "$OUT/odc" -name '*.log' | wc -l | tr -d ' ') files, $(cat "$OUT"/odc/*.log 2>/dev/null | wc -l | tr -d ' ') lines
EOF

say "bundle ready:"
cat "$OUT/MANIFEST" >&2
du -sh "$OUT" >&2
