"""DDS producer — the dense one (the firehose BURST spikes ×15).

Now emits the REAL DDS agent-log line format rather than the Flight-1 JSON, so it
mirrors the `dds_YYYY-MM-DD.N.log` found inside each dds/*.tar.gz in
s3://epn-backup-logs/. Fluent Bit `tail`s the file; a regex parser (parsers.yaml)
turns each line back into fields and sets event-time from the line's timestamp.

Real line grammar (columns separated by runs of spaces):
  2026-06-20 12:16:17.977940   err    o2-mid-reco-workflow   <0x001f8054:0x00001476db51e640>   Can't propagate property <...>
  └ timestamp (µs) ─────────┘  └sev┘  └ source ───────────┘  └ thread handle ─────────────┘   └ message ┘

Observed vocab: severity ∈ {inf (~86%), err (~14%), cout (rare)}; source is either
`dds-agent` (the agent, mostly inf) or an `o2-*-workflow` (the O2 process, inf+err).
The startup DDS banner is the one multiline case (continuation lines w/o a timestamp).
"""

import datetime
import os
import random

from common import NODE_ID, pick_host, sleep_for_rate

# One log file PER EPN, exactly like the real replay layout
# (dds/<host>.log). The collector's `set_host` Lua extracts the host from this
# path, so writing per-host files is what makes `host` populate — in mocks too,
# not just replay (single-file mocks had no host at all).
DDS_DIR = os.environ.get("DDS_LOG_DIR", "/var/log/node/dds")
BASE_RATE = float(os.environ.get("DDS_RATE", "50"))            # msgs/sec/node
BURST_MULTIPLIER = float(os.environ.get("DDS_BURST_MULTIPLIER", "15"))

# The O2 reconstruction workflows that show up as the DDS log "source" column.
WORKFLOWS = [
    "o2-qc", "o2-gpu-reco-workflow", "o2-itsmft-stf-decoder-workflow",
    "o2-dpl-raw-proxy", "o2-mch-reco-workflow", "o2-mft-reco-workflow",
    "o2-tpcits-match-workflow", "o2-its-reco-workflow", "o2-emcal-reco-workflow",
    "o2-mid-reco-workflow",
]

# dds-agent lifecycle/info messages (all `inf`).
AGENT_MSGS = [
    'Log engine is initialized with severety "inf"',
    "SESSION ID: {sid}",
    "PROTOCOL HEADER ID: {phid}",
    "{pid} (process ID) : {pgid} (process group ID) : {ppid}(parent process ID)",
    "Reading server info from: /var/tmp/dds/{sid}/{sub}/{run}/server_info.cfg",
    "Task <{wf}_reco{r}> activated on this agent",
]
# workflow steady-state info messages.
WORKFLOW_INF = [
    "Device state change: RUNNING",
    "Processing TF {tf}",
    "Sending {n} messages to next stage",
    "Init complete, entering event loop",
]
# workflow error messages (the dominant `err` shape in the real logs).
WORKFLOW_ERR = [
    "Can't propagate property <fmqchan_from_{a}_to_{b}> that doesn't exist for task <{a}_reco{r}>",
    "Failed to allocate SHM region of {n} bytes",
    "Timeout waiting for data from upstream device <{b}>",
]

_SID = "%08x-a4a8-483b-9dbd-1af32756c3fd" % random.randint(0, 0xffffffff)
_SUB = "%08x-fc55-4a60-ab96-d64c44ef04d9" % random.randint(0, 0xffffffff)
_RUNTAG = f"33NXirFsSfT_38917_{NODE_ID}"


def _handle() -> str:
    """Thread handle like <0x001f6455:0x000014ffab8a1780>."""
    return f"<0x{random.randint(0, 0xffffffff):08x}:0x{random.randint(0, 0xffffffffffff):016x}>"


def _now_ts() -> str:
    """Current time as 'YYYY-MM-DD HH:MM:SS.microseconds' (6-digit µs, like real).

    Emitted in UTC on purpose. The line carries no zone, and the `dds_text` parser
    (no %z) reads a zone-less timestamp as UTC — so if we wrote naive local time
    here (datetime.now()), a non-UTC container/VM clock (e.g. CERN's Europe/Zurich,
    UTC+2 in summer) would land every dds doc hours in the FUTURE. Anchoring to UTC
    makes @timestamp correct regardless of the host timezone."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _format(sev: str, source: str, msg: str) -> str:
    # Space-padded columns to resemble the real (aligned) layout. The parser keys
    # off runs of whitespace, so exact widths are cosmetic.
    return f"{_now_ts()}   {sev:<6} {source:<22} {_handle()}    {msg}"


def make_line() -> str:
    if random.random() < 0.5:
        # dds-agent: always inf.
        msg = random.choice(AGENT_MSGS).format(
            sid=_SID, sub=_SUB, run=_RUNTAG, phid=random.randint(10**18, 9 * 10**18),
            pid=random.randint(2_000_000, 2_100_000),
            pgid=random.randint(2_000_000, 2_100_000),
            ppid=random.randint(2_000_000, 2_100_000),
            wf=random.choice(WORKFLOWS), r=random.randint(1, 3),
        )
        return _format("inf", "dds-agent", msg)

    # an O2 workflow: mostly inf, ~20% err (matches the observed err share).
    wf = random.choice(WORKFLOWS)
    if random.random() < 0.20:
        msg = random.choice(WORKFLOW_ERR).format(
            a=random.choice(WORKFLOWS), b=random.choice(WORKFLOWS),
            r=random.randint(1, 3), n=random.randint(1024, 4_000_000_000),
        )
        return _format("err", wf, msg)
    msg = random.choice(WORKFLOW_INF).format(
        tf=random.randint(13_000_000, 13_999_999), n=random.randint(1, 5000),
    )
    return _format("inf", wf, msg)


BANNER = [
    'DDS v3.16.7.g39e3365',
    "DDS configuration v0.6",
    "DDS protocol v2",
    "Report bugs or comments to fairroot@gsi.de",
]


def write_banner(f) -> None:
    """The one multiline case: a `cout` line whose value spills over several
    continuation lines that carry NO timestamp (exercises the multiline parser)."""
    f.write(_format("cout", "dds-agent", BANNER[0]) + "\n")
    for cont in BANNER[1:]:
        f.write(cont + "\n")


def main() -> None:
    print(f"[dds] starting on {NODE_ID} -> {DDS_DIR}/epn*.log "
          f"@ {BASE_RATE}/s (burst x{BURST_MULTIPLIER})", flush=True)
    os.makedirs(DDS_DIR, exist_ok=True)

    # One line-buffered handle per EPN, opened lazily. Each host's file opens with
    # the DDS startup banner (the multiline case), just like a real agent log.
    handles: dict = {}

    def handle(host: str):
        f = handles.get(host)
        if f is None:
            # buffering=1 => line-buffered so Fluent Bit's tail sees each line at once.
            f = open(os.path.join(DDS_DIR, f"{host}.log"), "a", buffering=1)
            write_banner(f)
            handles[host] = f
        return f

    while True:
        handle(pick_host()).write(make_line() + "\n")
        sleep_for_rate(BASE_RATE, BURST_MULTIPLIER)


if __name__ == "__main__":
    main()
