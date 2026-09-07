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

# --- what is actually running, and where it writes -------------------------
# The census question the October 2022 data on /scratch cannot answer: where
# does a run write its job logs TODAY. Asked of the processes themselves rather
# than of the filesystem, because a full-filesystem search on a node carrying a
# staging run is not acceptable.
say "live processes and their open log files"
{
  echo "### processes matching the O2 / DDS / DataDistribution names"
  ps -eo pid,ppid,user,etimes,comm,args --no-headers 2>/dev/null \
    | grep -Ei 'o2-|dpl|TfBuilder|StfBuilder|TfScheduler|dds-|odc|readout|qc-task' \
    | grep -v grep | head -60
  echo
  echo "### open regular .log files held by those processes"
  for pid in $(ps -eo pid,args --no-headers 2>/dev/null \
                 | grep -Ei 'o2-|dpl|TfBuilder|StfBuilder|TfScheduler|dds-|odc|readout|qc-task' \
                 | grep -v grep | awk '{print $1}' | head -40); do
    for fd in /proc/$pid/fd/*; do
      target="$(readlink "$fd" 2>/dev/null)" || continue
      case "$target" in
        *.log|*/log/*) printf '%s\t%s\t%s\n' "$pid" "$(cat /proc/$pid/comm 2>/dev/null)" "$target" ;;
      esac
    done
  done 2>/dev/null | sort -u | head -80
  echo
  echo "### working directories of those processes"
  for pid in $(ps -eo pid,args --no-headers 2>/dev/null \
                 | grep -Ei 'o2-|dpl|TfBuilder|dds-|odc' | grep -v grep \
                 | awk '{print $1}' | head -40); do
    printf '%s\t%s\n' "$pid" "$(readlink /proc/$pid/cwd 2>/dev/null)"
  done 2>/dev/null | sort -u -k2 | head -40
} > "$OUT/live-processes.txt"

say "log-writing packages and their versions"
{
  echo "### fluent-bit"
  command -v fluent-bit >/dev/null 2>&1 && fluent-bit --version 2>&1 | head -2
  /opt/fluent-bit/bin/fluent-bit --version 2>&1 | head -2
  rpm -q fluent-bit 2>&1 | head -2
  echo
  echo "### does this build have the systemd input"
  { /opt/fluent-bit/bin/fluent-bit --help 2>&1 || fluent-bit --help 2>&1; } \
    | grep -iE '^\s*systemd|INPUT.*systemd' | head -3
  echo
  echo "### O2 / infologger packages"
  rpm -qa 2>/dev/null | grep -iE 'infologger|o2-|odc|dds' | sort | head -20
  echo
  echo "### infoLoggerD configuration"
  cat /etc/o2.d/infologger/infoLoggerD.cfg 2>&1 | head -30
} > "$OUT/versions.txt"

say "rotation"
{
  echo "### /etc/logrotate.conf"
  cat /etc/logrotate.conf 2>&1 | head -20
  echo
  echo "### /etc/logrotate.d entries touching the files we tail"
  grep -l -E 'infologger|o2|messages' /etc/logrotate.d/* 2>/dev/null \
    | while read -r f; do echo "--- $f"; cat "$f"; done | head -60
  echo
  echo "### journald configuration"
  grep -vE '^\s*#|^\s*$' /etc/systemd/journald.conf 2>/dev/null
  cat /etc/systemd/journald.conf.d/*.conf 2>/dev/null | grep -vE '^\s*#|^\s*$'
} > "$OUT/rotation.txt"

say "journal volume by unit and priority"
{
  echo "### entries per priority, last ${JOURNAL_SINCE}"
  for p in 0 1 2 3 4 5 6 7; do
    printf '%s\t%s\n' "$p" \
      "$(journalctl --no-pager -q -p "${p}..${p}" --since "$JOURNAL_SINCE" 2>/dev/null | wc -l)"
  done
  echo
  echo "### entries per unit, last ${JOURNAL_SINCE}, top 40"
  journalctl --no-pager -o json --since "$JOURNAL_SINCE" 2>/dev/null \
    | head -n "$JOURNAL_MAX" \
    | sed -n 's/.*"_SYSTEMD_UNIT"[^"]*"\([^"]*\)".*/\1/p' \
    | sort | uniq -c | sort -rn | head -40
  echo
  echo "### how much the five configured units plus kernel would leave behind"
  journalctl --no-pager -o json --since "$JOURNAL_SINCE" 2>/dev/null \
    | head -n "$JOURNAL_MAX" \
    | grep -cvE '"_SYSTEMD_UNIT"[^"]*"(slurmd|crond|fluent-bit|opensearch|alice-replay)\.service"|"_TRANSPORT"[^"]*"kernel"'
} > "$OUT/journal-volume.txt"

say "raw journal files, bounded, so the systemd input can be exercised off the farm"
mkdir -p "$OUT/journal/raw"
find /var/log/journal -name '*.journal' -type f -printf '%s\t%p\n' 2>/dev/null \
  | sort -rn | head -2 | cut -f2 \
  | while read -r jf; do cp -p "$jf" "$OUT/journal/raw/" 2>/dev/null; done
du -sh "$OUT/journal/raw" 2>/dev/null >&2

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
