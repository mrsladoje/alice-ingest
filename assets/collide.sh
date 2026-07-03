#!/usr/bin/env bash
# assets/collide.sh — `make run` splash.
#
#   CERN ring → zoom to Point 2 → ALICE detector layers → two Pb beams
#   accelerate down the pipe → collision flash → particle spray (curved
#   tracks, magnetic field) → zoom into the tracks → they resolve into
#   the ALICE wordmark. Fake stack telemetry scrolls on the right.
#
#   bash assets/collide.sh        play (~9 s, Ctrl-C safe)
#   ART_FAST=1                    skip all sleeps (smoke test)
#   ART_FORCE=1                   play even if stdout is not a tty
#
# Pure ANSI escapes, integer math only. Works on macOS /bin/bash 3.2.

[ -t 1 ] || [ -n "${ART_FORCE:-}" ] || exit 0

case "${LC_ALL:-}${LC_CTYPE:-}${LANG:-}" in
  *[Uu][Tt][Ff]*) : ;;
  *) export LC_ALL=en_US.UTF-8 ;;
esac

E=$'\033'
COLS=$(tput cols 2>/dev/null); [ -n "$COLS" ] || COLS=80
ROWS=$(tput lines 2>/dev/null); [ -n "$ROWS" ] || ROWS=24

if [ "$ROWS" -lt 20 ] || [ "$COLS" -lt 70 ]; then
  printf '%s\n' "ALICE ▸ igniting (terminal too small for the show)"
  exit 0
fi

cleanup(){ printf '%s' "${E}[0m${E}[?25h${E}[${ROWS};1H"; printf '\n'; }
trap cleanup EXIT
trap 'exit 130' INT TERM
printf '%s' "${E}[?25l"

# ── frame buffer: build a whole frame, print it in one write ─────────────
BUF=''
buf(){ BUF+="$1"; }
fg(){ BUF+="${E}[38;5;${1}m"; }
at(){ BUF+="${E}[${1};${2}H${3}"; }
cls(){ BUF+="${E}[0m${E}[2J${E}[H"; }
flush(){ printf '%s' "$BUF"; BUF=''; }
nap(){ [ -n "${ART_FAST:-}" ] || sleep "$1"; }

# ── layout: stage on the left, telemetry panel on the right ──────────────
LOGW=38
if [ "$COLS" -ge 100 ]; then LOGS_ON=1; STAGE_W=$((COLS-LOGW-2)); else LOGS_ON=0; STAGE_W=$COLS; fi
CX=$((STAGE_W/2)); CY=$((ROWS/2))
SEP=$((STAGE_W+1)); LOGC=$((STAGE_W+3)); LOG_TOP=4; LOG_N=0

# ── integer trig: 48 spokes, cos×100; sin is cos phase-shifted 90° ───────
QC=(100 99 97 92 87 79 71 61 50 38 26 13 0)
COS=(); SIN=(); i=0; j=0
for ((i=0;i<48;i++)); do
  j=$i; [ $j -gt 24 ] && j=$((48-j))
  if [ $j -le 12 ]; then COS[i]=${QC[j]}; else COS[i]=$(( -QC[24-j] )); fi
done
for ((i=0;i<48;i++)); do SIN[i]=${COS[$(( (i+36)%48 ))]}; done

ring(){ # cx cy rx ry color char [step]
  local cx=$1 cy=$2 rx=$3 ry=$4 col=$5 ch=$6 st=${7:-1} i x y
  fg "$col"
  for ((i=0;i<48;i+=st)); do
    x=$(( cx + rx*COS[i]/100 )); y=$(( cy + ry*SIN[i]/100 ))
    [ $y -lt 3 ] && continue; [ $y -gt $((ROWS-1)) ] && continue
    [ $x -lt 2 ] && continue; [ $x -gt $((STAGE_W-1)) ] && continue
    at $y $x "$ch"
  done
}

# ── telemetry panel ───────────────────────────────────────────────────────
LOGT=(
  "lhc|Pb beams injected · 6.8Z TeV"
  "lhc|ramp complete — stable beams"
  "node-01|supervisord: all RUNNING"
  "dds|topology up · 3 producers"
  "infologger|severity stream online"
  "fluent-bit|inputs online: 3 tails"
  "fluent-bit|o2 multiline parser ok"
  "collector|tail → lua → http live"
  "opensearch|3 nodes joined cluster"
  "opensearch|cluster health: GREEN"
  "dashboards|serving on :5601"
  "replay|s3 ctf window mounted"
  "collector|1.2 MB/s · 0 retries"
  "alice|run 3 · taking data ✓"
)

draw_log(){
  local ln=${LOGT[$1]} tag msg row
  tag=${ln%%|*}; msg=${ln#*|}; row=$((LOG_TOP+$1))
  [ $row -ge $ROWS ] && return 0
  fg 71;  at $row $LOGC "$tag"
  fg 245; buf " $msg"
}

log_tick(){
  [ "$LOGS_ON" = 1 ] || return 0
  [ $LOG_N -lt ${#LOGT[@]} ] || return 0
  draw_log $LOG_N; LOG_N=$((LOG_N+1))
}

chrome(){
  local r
  fg 240; at 1 2 "alice-ingest ▸ ignition"
  if [ "$LOGS_ON" = 1 ]; then
    fg 236
    for ((r=1;r<=ROWS;r++)); do at $r $SEP "│"; done
    fg 245; at 2 $LOGC "telemetry"
    fg 236; at 3 $LOGC "─────────────────────────"
    for ((r=0;r<LOG_N;r++)); do draw_log $r; done
  fi
}

# ── scene 1: the LHC ring, blinking at Point 2 ───────────────────────────
scene_lhc(){
  local rx ry ax ay b s maxry
  cls; chrome
  fg 245; at 2 $((CX-10)) "CERN · LHC · Point 2"
  rx=$((STAGE_W/3)); [ $rx -gt 26 ] && rx=26
  ry=$((rx/3)); maxry=$(((ROWS-8)/2)); [ $ry -gt $maxry ] && ry=$maxry
  ring $CX $CY $rx $ry 240 "·"
  fg 244
  at $((CY + ry*SIN[30]/100)) $((CX + rx*COS[30]/100 - 5)) "ATLAS"
  at $((CY + ry*SIN[42]/100)) $((CX + rx*COS[42]/100 + 1)) "CMS"
  at $((CY + ry*SIN[18]/100)) $((CX + rx*COS[18]/100 - 4)) "LHCb"
  ax=$((CX + rx*COS[6]/100)); ay=$((CY + ry*SIN[6]/100))
  flush; log_tick; flush; nap 0.45
  for b in 1 2 3; do
    fg 196; at $ay $ax "◉"; at $((ay+1)) $((ax-2)) "ALICE"; flush; nap 0.16
    fg 88;  at $ay $ax "◉"; flush; nap 0.10
  done
  log_tick
  for s in 3 6 10 15; do          # dive into Point 2
    ring $ax $ay $((s*2)) $s 238 "·" 2
    flush; nap 0.07
  done
}

# ── scene 2: the detector, layer by layer ────────────────────────────────
DRX=0; DRY=0

layer_labels(){                    # redrawable — particles fly over them
  fg 214; at $((CY - DRY*30/100 - 1)) $((CX+1)) "ITS"
  fg 39;  at $((CY - DRY*60/100 - 1)) $((CX+1)) "TPC"
  fg 71;  at $((CY - DRY*82/100 - 1)) $((CX+1)) "TRD"
  fg 240; at $((CY - DRY - 1))        $((CX+1)) "TOF"
}

scene_detector(){
  local f maxrx
  cls; chrome
  DRY=$(((ROWS-9)/2)); [ $DRY -gt 9 ] && DRY=9
  DRX=$((DRY*2+4)); maxrx=$((STAGE_W/2-6)); [ $DRX -gt $maxrx ] && DRX=$maxrx
  fg 203; at 2 $((CX-20)) "ALICE · A Large Ion Collider Experiment"
  for f in 45 75 100; do          # barrel grows as we fall in
    ring $CX $CY $((DRX*f/100)) $((DRY*f/100)) 236 "·" 2
    flush; nap 0.07
  done
  ring $CX $CY $((DRX*30/100)) $((DRY*30/100)) 214 "·"
  ring $CX $CY $((DRX*60/100)) $((DRY*60/100)) 39  "·"
  ring $CX $CY $((DRX*82/100)) $((DRY*82/100)) 71  "·"
  ring $CX $CY $DRX $DRY 240 "▒"
  layer_labels
  flush; log_tick; flush; nap 0.35
  log_tick
}

# ── scene 3: two Pb bunches accelerate down the beam pipe ────────────────
PIPE_STR=''
pipe(){ fg 238; at $CY 2 "$PIPE_STR"; }

scene_beams(){
  local t xl xr half x
  for ((x=2;x<STAGE_W;x++)); do PIPE_STR+='─'; done
  pipe; flush
  fg 220; at $((CY-2)) 4 "Pb ▸"; at $((CY-2)) $((STAGE_W-8)) "◂ Pb"
  flush; nap 0.3
  half=$(((STAGE_W-8)/2))
  for t in 1 2 3 4 5 6 7 8; do    # quadratic ramp — they accelerate
    pipe
    xl=$(( 4 + half*t*t/64 )); xr=$(( STAGE_W-4 - half*t*t/64 ))
    fg 220
    [ $((xl-3)) -ge 2 ] && at $CY $((xl-3)) "░▒▓█"
    at $CY $xr "█▓▒░"
    if [ $t -ge 4 ]; then
      buf "${E}[0m"; at $((CY-2)) 4 "    "; at $((CY-2)) $((STAGE_W-8)) "    "
    fi
    flush; nap 0.06
  done
}

# ── scene 4: impact ───────────────────────────────────────────────────────
scene_boom(){
  fg 231; at $CY $CX "█"; flush; nap 0.05
  fg 231
  at $((CY-1)) $((CX-1)) '\ /'
  at $CY $((CX-2)) '──✦──'
  at $((CY+1)) $((CX-1)) '/ \'
  flush; nap 0.09
  ring $CX $CY 6  3 226 "*" 4
  ring $CX $CY 12 6 208 "·" 2
  flush; nap 0.08
}

# ── scene 5: particle spray, tracks curling in the field ────────────────
scene_spray(){
  local NP=26 p t d s k x y hch
  local DIRA=() SPD=() CRL=() PCOL=() PX=() PY=()
  local PAL=(196 202 208 214 220 226 190 118 82 51 45 39 33 99 135 171 201 207 213 199)
  for ((p=0;p<NP;p++)); do
    DIRA[p]=$(( (p*48/NP + RANDOM%3) % 48 ))
    SPD[p]=$((60+RANDOM%80))
    CRL[p]=$((RANDOM%9-4))
    PCOL[p]=${PAL[$((RANDOM%20))]}
  done
  for ((t=1;t<=14;t++)); do
    if [ $((t%2)) -eq 0 ]; then hch='+'; else hch='*'; fi
    for ((p=0;p<NP;p++)); do
      d=${DIRA[p]}; s=${SPD[p]}; k=${CRL[p]}
      x=$(( CX + COS[d]*s*t/5000  + k*SIN[d]*t*t/8000 ))
      y=$(( CY + SIN[d]*s*t/10000 - k*COS[d]*t*t/32000 ))
      if [ -n "${PX[p]:-}" ]; then          # old head becomes trail dot
        fg ${PCOL[p]}; at ${PY[p]} ${PX[p]} "·"
      fi
      if [ $x -ge 2 ] && [ $x -le $((STAGE_W-1)) ] && [ $y -ge 3 ] && [ $y -le $((ROWS-1)) ]; then
        fg ${PCOL[p]}; at $y $x "$hch"
        PX[p]=$x; PY[p]=$y
      fi
    done
    case $t in 1) fg 231;; 2) fg 226;; 3) fg 208;; *) fg 94;; esac
    at $CY $CX "✦"                          # cooling afterglow at the vertex
    layer_labels                            # keep ITS/TPC/TRD/TOF above the debris
    [ $((t%2)) -eq 0 ] && log_tick
    flush; nap 0.07
  done
  nap 0.25
}

# ── scene 6: pan + zoom into the tracks… ─────────────────────────────────
scene_zoomcut(){
  ring $((CX+3)) $((CY-1)) 6  3  253 "·" 2; flush; nap 0.06
  ring $((CX+6)) $((CY-2)) 14 7  248 "·" 2; flush; nap 0.06
  ring $((CX+8)) $((CY-2)) 26 13 240 "·" 2; flush; nap 0.07
  cls; chrome
}

# ── …and the tracks were letters all along ───────────────────────────────
LOGO=(
' █████╗ ██╗     ██╗ ██████╗███████╗'
'██╔══██╗██║     ██║██╔════╝██╔════╝'
'███████║██║     ██║██║     █████╗  '
'██╔══██║██║     ██║██║     ██╔══╝  '
'██║  ██║███████╗██║╚██████╗███████╗'
'╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝╚══════╝'
)
GRAD=(231 229 223 216 209 202)

scene_logo(){
  local W=35 H=6 LX LY r c ch pass idx roll line k frow fcol
  local REV=()
  LX=$((CX-W/2)); LY=$((CY-5)); [ $LY -lt 4 ] && LY=4
  # every collision leaves a paper trail — log fragments drift in around the mark
  local FRAG=(
    'tpc: clusters ok'  'sev=I src=dds'     'level=warn retry=1' '_bulk 200 · 3ms'
    'tail: +128 lines'  'parser=o2-multi'   'epn-023 readout ok' '{"lvl":"info"}'
    'offset=418230'     'flb: chunk flush'  'idx alice-logs-*'   'sev=W ecs ok'
  )
  local FRAGC=(239 238 23 59 238 239 238 23 238 239 59 238)
  local SR=( $((LY-4)) $((LY-3)) $((LY-2)) $((LY-3)) $((LY+1)) $((LY+3)) \
             $((LY+5)) $((LY+8)) $((LY+8)) $((LY-2)) $((LY+4)) $((LY-4)) )
  local SC=( $((LX+8))  $((LX-4))  $((LX+20)) $((LX+26)) $((LX-16)) $((LX+W+2)) \
             $((LX-14)) $((LX-2))  $((LX+24)) $((LX-12)) $((LX+W+3)) $((LX+30)) )
  for pass in 1 2 3; do                     # cells condense out of the spray
    for ((r=0;r<H;r++)); do
      line=${LOGO[r]}
      for ((c=0;c<W;c++)); do
        idx=$((r*W+c))
        [ -n "${REV[idx]:-}" ] && continue
        ch=${line:c:1}
        [ "$ch" = ' ' ] && continue
        roll=$((RANDOM%5))
        if [ $roll -lt $pass ]; then
          fg ${GRAD[r]}; at $((LY+r)) $((LX+c)) "$ch"; REV[idx]=1
        elif [ $roll -eq 4 ]; then
          fg 238; at $((LY+r)) $((LX+c)) "·"
        fi
      done
    done
    for ((k=pass-1;k<12;k+=3)); do          # 4 log fragments per pass
      frow=${SR[k]}; fcol=${SC[k]}
      [ $frow -lt 3 ] && continue
      [ $frow -gt $((ROWS-1)) ] && continue
      [ $fcol -lt 2 ] && continue
      [ $((fcol+${#FRAG[k]})) -gt $((STAGE_W-1)) ] && continue
      fg ${FRAGC[k]}; at $frow $fcol "${FRAG[k]}"
    done
    flush; nap 0.14; log_tick
  done
  for ((r=0;r<H;r++)); do                   # final crisp pass
    fg ${GRAD[r]}; at $((LY+r)) $LX "${LOGO[r]}"
  done
  flush; nap 0.1
  local SUB='A Large Ion Collider Experiment' i2
  fg 252
  for ((i2=0;i2<${#SUB};i2++)); do
    at $((LY+H+1)) $((CX-15+i2)) "${SUB:i2:1}"; flush; nap 0.012
  done
  fg 203; at $((LY+H+3)) $((CX-16)) "— beams stable · systems green —"
  flush
}

# ── roll film ─────────────────────────────────────────────────────────────
scene_lhc
scene_detector
scene_beams
scene_boom
scene_spray
scene_zoomcut
scene_logo
while [ "$LOGS_ON" = 1 ] && [ $LOG_N -lt ${#LOGT[@]} ]; do log_tick; flush; nap 0.08; done
buf "${E}[0m"; flush
