#!/usr/bin/env bash
# assets/shiva.sh — `make down` outro.
#
#   The earth trembles, cracks, and splits in half. We fall away from it
#   until it is a pale blue dot, then nothing — only stars. And in the
#   starfield: Nataraja, the dancing Shiva of the CERN forecourt, inside
#   a flickering ring of flame. Creation, preservation, dissolution.
#
#   bash assets/shiva.sh          play (~9 s, Ctrl-C safe)
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
  printf '%s\n' "the dance ends — stack down"
  exit 0
fi

cleanup(){ printf '%s' "${E}[0m${E}[?25h${E}[${ROWS};1H"; printf '\n'; }
trap cleanup EXIT
trap 'exit 130' INT TERM
printf '%s' "${E}[?25l"

# ── frame buffer ──────────────────────────────────────────────────────────
BUF=''
buf(){ BUF+="$1"; }
fg(){ BUF+="${E}[38;5;${1}m"; }
at(){ BUF+="${E}[${1};${2}H${3}"; }
cls(){ BUF+="${E}[0m${E}[2J${E}[H"; }
flush(){ printf '%s' "$BUF"; BUF=''; }
nap(){ [ -n "${ART_FAST:-}" ] || sleep "$1"; }

CX=$((COLS/2)); CY=$((ROWS/2))

chrome(){ fg 240; at 1 2 "alice-ingest ▸ descent"; }

typeat(){ # row col color text delay
  local r=$1 c=$2 col=$3 s=$4 d=$5 i
  fg $col
  for ((i=0;i<${#s};i++)); do at $r $((c+i)) "${s:i:1}"; flush; nap $d; done
}

# ── integer trig, 48 spokes (for the ring of flame) ──────────────────────
QC=(100 99 97 92 87 79 71 61 50 38 26 13 0)
COS=(); SIN=(); i=0; j=0
for ((i=0;i<48;i++)); do
  j=$i; [ $j -gt 24 ] && j=$((48-j))
  if [ $j -le 12 ]; then COS[i]=${QC[j]}; else COS[i]=$(( -QC[24-j] )); fi
done
for ((i=0;i<48;i++)); do SIN[i]=${COS[$(( (i+36)%48 ))]}; done

# ── painters: char-class → color; spaces opaque (globe) or skipped ───────
LSH=(28 34 40)   # land shades
OSH=(25 27 33)   # ocean shades

paint_globe(){ # str row -> colored string on stdout (spaces stay opaque)
  local s=$1 r=$2 i ch n=${#1}
  for ((i=0;i<n;i++)); do
    ch=${s:i:1}
    case "$ch" in
      '#') printf '%s' "${E}[38;5;${LSH[$(((r+i)%3))]}m#";;
      '~') printf '%s' "${E}[38;5;${OSH[$(((r*2+i)%3))]}m~";;
      '%') printf '%s' "${E}[38;5;202m/";;
      ' ') printf ' ';;
      *)   printf '%s' "${E}[38;5;246m$ch";;
    esac
  done
}

paint_dance(){ # str mode(color-number|map) -> stdout; spaces transparent
  local s=$1 m=$2 i ch n=${#1}
  for ((i=0;i<n;i++)); do
    ch=${s:i:1}
    if [ "$ch" = ' ' ]; then printf '%s' "${E}[1C"; continue; fi
    if [ "$m" != map ]; then printf '%s' "${E}[38;5;${m}m$ch"; continue; fi
    case "$ch" in
      '%') printf '%s' "${E}[38;5;208m%";;   # agni, flame of destruction (his left hand)
      '@') printf '%s' "${E}[38;5;45m@";;    # damaru, drum of creation (his right hand)
      '#') printf '%s' "${E}[38;5;94m#";;    # apasmara, the dwarf of ignorance
      '~') printf '%s' "${E}[38;5;59m~";;
      '█') printf '%s' "${E}[38;5;172m█";;   # bronze — main body mass
      '▓') printf '%s' "${E}[38;5;130m▓";;   # bronze in shadow
      '▒') printf '%s' "${E}[38;5;178m▒";;   # gold highlight (face, front arm)
      '░') printf '%s' "${E}[38;5;95m░";;    # dull patina (crown tip, apasmara)
      '▄') printf '%s' "${E}[38;5;130m▄";;
      '▀') printf '%s' "${E}[38;5;136m▀";;
      '≈') printf '%s' "${E}[38;5;136m≈";;   # flying locks and sash
      '-') printf '%s' "${E}[38;5;94m-";;    # half-closed eyes
      '·') printf '%s' "${E}[38;5;220m·";;   # earrings
      *)   printf '%s' "${E}[38;5;222m$ch";;
    esac
  done
}

# ── the world ─────────────────────────────────────────────────────────────
GLOBE=(
'            _.--------._            '
'        ,-`    ~~~ ###   `-,        '
'      ,`   #### ~~~~ ####   `,      '
'     /   ###### ~~ ### ~~~ #  \     '
'    /   ## ###~~~ ~~~~~ ### ~~ \    '
'   |   ~~~ ### ~~~ #### ~~~ ##  |   '
'   |   ~~ ##### ~ ###### ~~~ #  |   '
'   |   ~ ###### ~~ ##### ~~~ ~  |   '
'    \   ~~~~ ## ~~~ ~~ ### ~~  /    '
'     \   ### ~~~ #### ~~ ###  /     '
'      `,   ### ~~~~~ ###   ,`       '
'        `-,    ~~ ##    ,-`         '
'            `--------`              '
)
GH=13; MID=19
OFF=(0 1 0 -1 -1 0 1 2 1 0 -1 0 0)          # crack meander per row
TOP=$((CY-7)); LEFT=$((CX-18))

GROW=(); CCH=(); CUTA=(); LP=(); RP=(); LEDGE=(); REDGE=()
r=0; po=0; o=0
for ((r=0;r<GH;r++)); do
  GROW[r]=$(paint_globe "   ${GLOBE[r]}   " $r)   # 3-space pad absorbs shake
  o=${OFF[r]}
  if [ $r -eq 0 ]; then CCH[r]='|'
  else
    po=${OFF[$((r-1))]}
    if   [ $o -gt $po ]; then CCH[r]='\'
    elif [ $o -lt $po ]; then CCH[r]='/'
    else CCH[r]='|'; fi
  fi
done

# ── stars, born as the world dies ─────────────────────────────────────────
SX=(); SY=(); SCH=(); s=0
for ((s=0;s<60;s++)); do
  SX[s]=$((2+RANDOM%(COLS-4)))
  SY[s]=$((3+RANDOM%(ROWS-8)))    # never in the bottom caption band — text stays clean
  case $((RANDOM%6)) in
    0|1) SCH[s]='.';; 2) SCH[s]='·';; 3) SCH[s]='+';; 4) SCH[s]='˚';; 5) SCH[s]='✦';;
  esac
done
STC=(238 244 250 253 226 231 111)

draw_stars(){ # n — draw first n stars, random twinkle color each call
  local n=$1 k
  [ $n -gt 60 ] && n=60
  for ((k=0;k<n;k++)); do
    fg ${STC[$((RANDOM%7))]}; at ${SY[k]} ${SX[k]} "${SCH[k]}"
  done
}

# ── scene 1: serene, for a moment ────────────────────────────────────────
scene_serene(){
  local r
  cls; chrome; draw_stars 8
  for ((r=0;r<GH;r++)); do at $((TOP+r)) $((LEFT-3)) "${GROW[r]}"; done
  flush; nap 0.35
  typeat $((ROWS-1)) 2 245 'shutdown requested — the world winds down' 0.015
  nap 0.4
}

# ── scene 2: the crack ────────────────────────────────────────────────────
scene_crack(){
  local DX=(1 -1 1 -2 1 -1 2 -1 1 -2 1 0 0) st r dx col
  for ((st=0;st<GH;st++)); do
    dx=${DX[st]}
    for ((r=0;r<GH;r++)); do at $((TOP+r)) $((LEFT-3+dx)) "${GROW[r]}"; done
    for ((r=0;r<=st;r++)); do
      col=$(( (st+r)%2 ? 227 : 231 ))
      fg $col; at $((TOP+r)) $((LEFT+dx+MID+OFF[r])) "${CCH[r]}"
    done
    fg 245; at $((ROWS-1)) 2 "[core] seismic event — integrity $((100-st*7))%      "
    flush; nap 0.09
  done
  nap 0.2
}

# ── scene 3: the world splits ────────────────────────────────────────────
scene_split(){
  local r raw cut gap k
  for ((r=0;r<GH;r++)); do
    raw=${GLOBE[r]}; cut=$((MID+OFF[r])); CUTA[r]=$cut
    LP[r]=$(paint_globe "${raw:0:cut}" $r)
    RP[r]=$(paint_globe "${raw:cut}" $r)
    LEDGE[r]=${raw:cut-1:1}; REDGE[r]=${raw:cut:1}
  done
  for gap in 2 4 6 8; do
    cls; chrome; draw_stars $((gap*2))
    for ((r=0;r<GH;r++)); do
      at $((TOP+r)) $((LEFT-gap)) "${LP[r]}"
      at $((TOP+r)) $((LEFT+CUTA[r]+gap)) "${RP[r]}"
      if [ "${LEDGE[r]}" != ' ' ]; then       # magma on the torn edges
        fg $(( (r+gap)%2 ? 202 : 196 )); at $((TOP+r)) $((LEFT-gap+CUTA[r]-1)) "▓"
      fi
      if [ "${REDGE[r]}" != ' ' ]; then
        fg $(( (r+gap)%2 ? 196 : 202 )); at $((TOP+r)) $((LEFT+CUTA[r]+gap)) "▓"
      fi
    done
    fg 208
    for k in 1 2 3 4 5; do
      at $((TOP+1+RANDOM%11)) $((LEFT+MID-gap+RANDOM%(gap*2))) "·"
    done
    fg 245; at $((ROWS-1)) 2 "[core] integrity lost — planetary split in progress"
    flush; nap 0.12
  done
  nap 0.15
}

# ── scene 4: falling away ────────────────────────────────────────────────
MED=(
'     _.--._     '
'   ,`##~~%#`,   '
'  ( ~~##%## ~)  '
'  ( ##%##~~~ )  '
'   `,%#~~~,-`   '
'     `--_-`     '
)
SML=(
'   .--.   '
'  (#%~~)  '
'  (~%##)  '
'   `--`   '
)

scene_zoomout(){
  local r ln
  cls; chrome; draw_stars 20
  for ((r=0;r<6;r++)); do
    ln=$(paint_globe "${MED[r]}" $r); at $((CY-3+r)) $((CX-8)) "$ln"
  done
  flush; nap 0.22
  cls; chrome; draw_stars 34
  for ((r=0;r<4;r++)); do
    ln=$(paint_globe "${SML[r]}" $r); at $((CY-2+r)) $((CX-5)) "$ln"
  done
  flush; nap 0.22
  cls; chrome; draw_stars 46
  ln=$(paint_globe '(%)' 1); at $CY $((CX-1)) "$ln"
  flush; nap 0.2
  cls; chrome; draw_stars 60
  fg 75; at $CY $CX "·"
  fg 240; at $((ROWS-3)) $((CX-14)) "a pale blue dot, winking out"
  flush; nap 0.55
  cls; chrome; draw_stars 60
  flush; nap 0.3
}

# ── scene 5: Nataraja ────────────────────────────────────────────────────
# Big sprite (terminals ≥29 rows), block-shaded bronze after the CERN statue:
# damaru raised in his right hand (viewer's left), agni in his left (viewer's
# right), abhaya palm at the chest, gaja-hasta arm sweeping across toward the
# raised left leg; right leg planted on Apasmara; lotus pedestal below.
DANCEB=(
'                 ░▒▓▒░            %   '
'     @ ▓▓ ≈≈≈≈≈ ▒▓███▓▒ ≈≈≈≈≈ ▓▓ %%   '
'         ▓▓      ▒-▒-▒      ▓▓        '
'           ▓▓   ·  ▓█▓  · ▓▓          '
'          ▀▀ ▓▓▄███████▄▓▓            '
'          ▒▒▓▓▓ ███████▓▓             '
'                ██▒▒▒▒▒▓              '
'              ▒▒▒▒████▓               '
'            ▒▒   ▓███▓ ≈≈≈≈≈≈         '
'            ░ ≈≈ ▒▒▒▒▒   ≈≈≈≈≈        '
'                ▄███████▄             '
'                 ███                  '
'                 ██                   '
'                 ██                   '
'                  ██                  '
'                  ▓█                  '
'            ░░░░░▓███▄░░░░▒░          '
'          ░░░░░░░░░░░░░░░░░░          '
'        ▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀        '
'       ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀       '
)
# The raised LEFT leg is not part of the base sprite — it is a bright-gold
# overlay painted AFTER the body, so wherever it crosses the dark bronze
# standing leg the gold visibly sits in front. One straight leg, the same
# length as the standing leg, pivoting at the right hip through the rise:
#   0 — hanging down beside the standing leg (both feet down)
#   1 — lifted, sweeping across in front of the standing shin
#   2 — crossed low, foot past the far side
#   3 — risen OVER the hip, foot above hip height (final hold)
draw_leg(){ # pose color   (uses TOPD/LEFTD from the caller)
  local p=$1 c=$2
  fg "$c"
  case $p in
    0) at $((TOPD+11)) $((LEFTD+21)) "██"
       at $((TOPD+12)) $((LEFTD+21)) "██"
       at $((TOPD+13)) $((LEFTD+21)) "██"
       at $((TOPD+14)) $((LEFTD+21)) "██"
       at $((TOPD+15)) $((LEFTD+21)) "█▄";;
    1) at $((TOPD+11)) $((LEFTD+18)) "███"
       at $((TOPD+12)) $((LEFTD+15)) "███"
       at $((TOPD+13)) $((LEFTD+12)) "███"
       at $((TOPD+14)) $((LEFTD+9))  "▄██";;
    2) at $((TOPD+11)) $((LEFTD+16)) "█████"
       at $((TOPD+12)) $((LEFTD+11)) "█████"
       at $((TOPD+13)) $((LEFTD+7))  "▄███";;
    3) at $((TOPD+10)) $((LEFTD+12)) "███████"
       at $((TOPD+9))  $((LEFTD+8))  "████"
       at $((TOPD+8))  $((LEFTD+6))  "▄██";;
  esac
}

# Compact fallback for short terminals.
DANCE=(
'          ,_,          '
'   @      (_)      %   '
'    \   ,- | -,   /    '
'     \ / ,\|/, \ /     '
' o==--`\ \\|// /`--==o '
'        `-\|/-`        '
'           |           '
'          >|<          '
'         ,/|\,         '
'        ` /|\ `        '
'       ,-` | `-,       '
'      (   ,|.   )      '
'       `-` |\  ,`      '
'       ,__/| \,`       '
'      (,#####  `,_)    '
'        ~~~~~~~~~      '
)
scene_shiva(){
  local RY RX f i x y x2 y2 ci ch r K DH HALF TOPD CYD LEFTD BIG lp lc
  local SRC=() DR1=() DR2=() DR3=()
  local FCOL=(202 208 214 220 226)
  if [ $ROWS -ge 23 ]; then                    # the full bronze fits a 24-row terminal
    SRC=("${DANCEB[@]}"); HALF=19; BIG=1
    TOPD=$((CY-10)); [ $TOPD -lt 2 ] && TOPD=2
    CYD=$((TOPD+10))
  else
    SRC=("${DANCE[@]}"); HALF=11; BIG=0
    TOPD=$((CY-8)); [ $TOPD -lt 2 ] && TOPD=2
    CYD=$((TOPD+8))
  fi
  DH=${#SRC[@]}; LEFTD=$((CX-HALF))
  # ring hugs the figure when rows are scarce — like the statue, tangent to it
  RY=$((ROWS-3-CYD))
  [ $RY -gt $((CYD-2)) ] && RY=$((CYD-2))
  [ $RY -gt 13 ] && RY=13
  RX=$((RY*2+6)); [ $RX -gt $((COLS/2-4)) ] && RX=$((COLS/2-4))
  for ((r=0;r<DH;r++)); do
    DR1[r]=$(paint_dance "${SRC[r]}" 236)      # silhouette…
    DR2[r]=$(paint_dance "${SRC[r]}" 244)      # …half-light…
    DR3[r]=$(paint_dance "${SRC[r]}" map)      # …bronze
  done
  local CAP1='« the cosmic dance — creation · preservation · dissolution »'
  local CAP2='the Nataraja at CERN — every shutdown, a small dissolution'
  local CAP3='[ stack down · the dance will begin again ]'
  for ((f=0;f<20;f++)); do
    if [ $BIG -eq 1 ]; then                    # wipe the leg's rise path from last frame
      for r in 8 9 10 11 12 13 14 15; do
        at $((TOPD+r)) $((LEFTD+6)) "                 "
      done
    fi
    draw_stars 60                              # the stars sparkle around the dance
    for ((i=0;i<48;i++)); do                   # ring of flame, flickering
      x=$(( CX + RX*COS[i]/100 )); y=$(( CYD + RY*SIN[i]/100 ))
      [ $y -lt 2 ] && continue; [ $y -gt $((ROWS-3)) ] && continue
      ci=${FCOL[$(((i+f)%5))]}
      case $(((i+f*2)%4)) in
        0) ch=')';; 1) ch="'";; 2) ch='(';; 3) ch='*';;
      esac
      fg $ci; at $y $x "$ch"
      if [ $(((i+f)%3)) -eq 0 ]; then          # flame tongues licking outward
        x2=$(( CX + (RX+2)*COS[i]/100 )); y2=$(( CYD + (RY+1)*SIN[i]/100 ))
        if [ $y2 -ge 2 ] && [ $y2 -le $((ROWS-3)) ] && [ $x2 -ge 2 ] && [ $x2 -le $((COLS-2)) ]; then
          fg 202; at $y2 $x2 "'"
        fi
      fi
    done
    if   [ $f -lt 2 ]; then for ((r=0;r<DH;r++)); do at $((TOPD+r)) $LEFTD "${DR1[r]}"; done
    elif [ $f -lt 4 ]; then for ((r=0;r<DH;r++)); do at $((TOPD+r)) $LEFTD "${DR2[r]}"; done
    else                    for ((r=0;r<DH;r++)); do at $((TOPD+r)) $LEFTD "${DR3[r]}"; done
    fi
    if [ $BIG -eq 1 ]; then                    # the left leg rises once, slowly, and holds
      if   [ $f -lt 2 ]; then lp=0; lc=236
      elif [ $f -lt 4 ]; then lp=0; lc=244
      else
        lp=$(( (f-4)/2 )); [ $lp -gt 3 ] && lp=3
        lc=220
      fi
      draw_leg $lp $lc
    fi
    # captions type on WHILE the dance continues — a few glyphs per frame,
    # pinned to the bottom band where no star is ever placed
    if [ $f -ge 4 ]; then
      K=$(( (f-3)*8 )); [ $K -gt ${#CAP1} ] && K=${#CAP1}
      fg 216; at $((ROWS-2)) $((CX-${#CAP1}/2)) "${CAP1:0:K}"
    fi
    if [ $f -ge 13 ]; then
      fg 245; at $((ROWS-1)) $((CX-${#CAP2}/2)) "$CAP2"
    fi
    if [ $f -ge 15 ]; then
      fg 240; at $ROWS $((CX-${#CAP3}/2)) "$CAP3"
    fi
    flush; nap 0.12
  done
}

# ── roll film ─────────────────────────────────────────────────────────────
scene_serene
scene_crack
scene_split
scene_zoomout
scene_shiva
buf "${E}[0m"; flush
