#!/usr/bin/env bash
set -uo pipefail

OUT_ROOT="${OUT_ROOT:-/tmp/epnsurvey}"
JL_ROOT="${JL_ROOT:-/scratch/jl}"
VARLOG="${VARLOG:-/var/log}"
SAMPLE_LINES="${SAMPLE_LINES:-1500}"
SAMPLE_FILES_PER_PROGRAM="${SAMPLE_FILES_PER_PROGRAM:-3}"
JOURNAL_SINCE="${JOURNAL_SINCE:-30 days ago}"
JOURNAL_MAX="${JOURNAL_MAX:-40000}"
KERNEL_MAX="${KERNEL_MAX:-20000}"

NODE="$(hostname -s)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_ROOT}/${NODE}-${STAMP}"
mkdir -p "$OUT/samples" "$OUT/journal"

say() { printf '[survey] %s\n' "$*" >&2; }

say "node=${NODE} out=${OUT}"

{
  printf 'node\t%s\n' "$NODE"
  printf 'captured_utc\t%s\n' "$STAMP"
  printf 'kernel\t%s\n' "$(uname -r)"
  printf 'release\t%s\n' "$(cat /etc/redhat-release 2>/dev/null | tr -d '\n')"
  printf 'uptime\t%s\n' "$(uptime -p 2>/dev/null)"
  printf 'nproc\t%s\n' "$(nproc)"
  printf 'memtotal_kb\t%s\n' "$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  printf 'jl_root\t%s\n' "$JL_ROOT"
} > "$OUT/meta.tsv"

say "mounts"
{
  echo "### df -hT"
  df -hT "$JL_ROOT" "$VARLOG" / 2>&1
  echo
  echo "### mount (network filesystems)"
  mount | grep -E 'nfs|lustre|ceph|gpfs' 2>&1
} > "$OUT/mounts.txt"

say "systemd units and journal fields"
{
  echo "### systemctl list-units --type=service --all --no-pager"
  systemctl list-units --type=service --all --no-pager --plain 2>&1
  echo
  echo "### journalctl --disk-usage"
  journalctl --disk-usage 2>&1
  echo
  echo "### distinct _SYSTEMD_UNIT in journal"
  journalctl --no-pager -F _SYSTEMD_UNIT 2>/dev/null | sort
  echo
  echo "### distinct SYSLOG_IDENTIFIER in journal"
  journalctl --no-pager -F SYSLOG_IDENTIFIER 2>/dev/null | sort
} > "$OUT/units.txt"

say "journal export (since ${JOURNAL_SINCE}, max ${JOURNAL_MAX})"
journalctl --no-pager -o json --since "$JOURNAL_SINCE" 2>/dev/null \
  | head -n "$JOURNAL_MAX" > "$OUT/journal/journal.json"
journalctl --no-pager -o json -k --since "$JOURNAL_SINCE" 2>/dev/null \
  | head -n "$KERNEL_MAX" > "$OUT/journal/kernel.json"
say "journal lines: $(wc -l < "$OUT/journal/journal.json") kernel: $(wc -l < "$OUT/journal/kernel.json")"

say "/var/log inventory"
find "$VARLOG" -maxdepth 2 -type f -printf '%p\t%s\t%TY-%Tm-%TdT%TH:%TM\t%u\t%g\n' 2>/dev/null \
  | sort > "$OUT/varlog-inventory.tsv"

for f in messages secure cron dnf.log o2-infologger-daemon.log infologger_syslog; do
  [ -r "$VARLOG/$f" ] || continue
  head -n "$SAMPLE_LINES" "$VARLOG/$f" > "$OUT/samples/varlog__${f//\//_}.sample" 2>/dev/null
done

if [ -d "$JL_ROOT" ]; then
  say "job-log inventory under ${JL_ROOT}"
  find "$JL_ROOT" -maxdepth 3 -name '*.log' -type f \
    -printf '%p\t%s\t%TY-%Tm-%TdT%TH:%TM\n' 2>/dev/null \
    | sort > "$OUT/jl-inventory.tsv"
  say "job-log files: $(wc -l < "$OUT/jl-inventory.tsv")"

  say "run directory listing (one run, one host, all entries)"
  ONE_RUN="$(find "$JL_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | head -1)"
  if [ -n "$ONE_RUN" ]; then
    ONE_HOST="$(find "$ONE_RUN" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | head -1)"
    [ -n "$ONE_HOST" ] && ls -la "$ONE_HOST" > "$OUT/one-run-listing.txt" 2>&1
  fi

  say "program breakdown"
  awk -F'\t' '{
      n = $1
      sub(/.*\//, "", n)
      sub(/_(t[0-9]+_)?reco[0-9]+_[0-9]{4}-.*\.log$/, "", n)
      sub(/_[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+\.log$/, "", n)
      sub(/_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9-]+_[0-9]+_(out|err)\.log$/, "", n)
      files[n]++
      bytes[n] += $2
    }
    END { for (p in files) printf "%s\t%d\t%d\n", p, files[p], bytes[p] }' \
    "$OUT/jl-inventory.tsv" | sort -k3 -rn > "$OUT/programs.tsv"

  while IFS=$'\t' read -r program _files _bytes; do
    [ -n "$program" ] || continue
    safe="${program//\//_}"
    for stream in out err; do
      : > "$OUT/samples/jl__${safe}__${stream}.sample"
      awk -F'\t' -v p="$program" -v s="_${stream}.log" '
          { n = $1; sub(/.*\//, "", n) }
          index(n, p) == 1 && (index(n, s) > 0 || s == "_out.log") && $2 > 0 { print $2 "\t" $1 }' \
        "$OUT/jl-inventory.tsv" \
        | sort -rn | head -n "$SAMPLE_FILES_PER_PROGRAM" | cut -f2- \
        | while read -r path; do
            head -n "$SAMPLE_LINES" "$path" 2>/dev/null
          done >> "$OUT/samples/jl__${safe}__${stream}.sample"
      [ -s "$OUT/samples/jl__${safe}__${stream}.sample" ] || rm -f "$OUT/samples/jl__${safe}__${stream}.sample"
    done
  done < "$OUT/programs.tsv"
else
  say "WARNING: ${JL_ROOT} absent on this node"
fi

say "packing"
BUNDLE="${OUT_ROOT}/epnsurvey-${NODE}-${STAMP}.tar.gz"
tar -czf "$BUNDLE" -C "$OUT_ROOT" "$(basename "$OUT")"
say "bundle: ${BUNDLE} ($(du -h "$BUNDLE" | cut -f1))"
printf '%s\n' "$BUNDLE"
