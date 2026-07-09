"""stdout producer — unstructured O2/ROOT process output.

Now mirrors the real per-process `*_out.log` / `*_err.log` found inside the DDS
tarballs: raw application stdout/stderr with NO timestamp and NO node prefix — a
mix of ROOT-framework lines (`<Level> in <Class::method>: msg`) and free-form
text, plus the deeply-indented module-loading block at process start (the
multiline case). Fluent Bit tails the file; a multiline parser folds indented
continuations, and a regex parser extracts severity where ROOT provides it.

Real samples:
  Info in <TGeoGlobalMagField::SetField>: Global magnetic field set to <MagneticFieldMap>
  Loading O2PDPSuite/epn-20260615-...
    Loading requirement: BASE/1.0 libffi/... lz4/...

Event-time: these logs carry no timestamp, so the collector uses ingest time for
the live mock; the S3 replay producer will derive it from the process-log filename.
Does not react to BURST (multiplier 1).
"""

import os
import random

from common import NODE_ID, pick_host, sleep_for_rate

# One file PER EPN (stdout/<host>.log), mirroring the real per-process layout and
# the replay stack. The collector's `set_host` Lua reads the host from this path,
# so per-host files are what give mock stdout records a `host` at all.
STDOUT_DIR = os.environ.get("STDOUT_LOG_DIR", "/var/log/node/stdout")
BASE_RATE = float(os.environ.get("STDOUT_RATE", "15"))         # msgs/sec/node
BURST_MULTIPLIER = float(os.environ.get("STDOUT_BURST_MULTIPLIER", "1"))

# ROOT framework messages by level: (Class::method, message-template).
ROOT_INFO = [
    ("TGeoGlobalMagField::SetField", "Global magnetic field set to <MagneticFieldMap>"),
    ("TGeoGlobalMagField::Lock", "Global magnetic field <MagneticFieldMap> is now locked"),
    ("TClass::Init", "no dictionary for class {cls} is available"),
    ("TROOT::Append", "Replacing existing TH1: {cls} (Potential memory leak)"),
]
ROOT_WARN = [
    ("TInterpreter::ReadRootmapFile", "class {cls} is already in TClassTable"),
    ("TStreamerInfo::Build", "{cls}: base class has no streamer"),
]
ROOT_ERR = [
    ("TFile::ReadBuffer", "error reading all requested bytes from file {f}, got 0 of {n}"),
    ("TGeoManager::Init", "geometry {cls} already initialized, refusing to overwrite"),
]
CLASSES = ["o2::tpc::ClusterNative", "o2::its::TrackITS", "o2::mch::Cluster",
           "o2::dataformats::MCTruth", "o2::ft0::RecPoints", "o2::emcal::Cell"]

# free-form (no severity) application chatter.
FREE = [
    "Processing TF {tf}",
    "Sending {n} messages on channel <data>",
    "FairMQ device transitioning to RUNNING",
    "Allocated SHM segment of {n} bytes",
    "Cycle {n} completed in {ms} ms",
    "Reached end of data stream, flushing buffers",
]

# The module-loading block printed at process start: a header line + deeply
# indented continuation lines (the multiline case the parser must fold).
LOADING_BLOCK = [
    "Loading O2PDPSuite/epn-20260615-DDv1.6.11-QCv1.193.0-flp-suite-v1.82.0-2",
    "  Loading requirement: BASE/1.0 libffi/v3.2.1-alice1-13 lz4/v1.10.0-4",
    "    zlib/v1.3.1-9 FreeType/v2.10.1-28 GCC-Toolchain/v14.2.0-alice2-2",
    "    ROOT/v6-36-10-alice1-3 FairRoot/v18.4.9-alice3-157 GEANT4/v11.2.0-alice2-2",
    "    Python/v3.10.19-12 boost/v1.90.0-alice1-1 protobuf/v29.3-19",
]


def _root_line() -> str:
    r = random.random()
    if r < 0.85:
        level, (where, tmpl) = "Info", random.choice(ROOT_INFO)
    elif r < 0.97:
        level, (where, tmpl) = "Warning", random.choice(ROOT_WARN)
    else:
        level, (where, tmpl) = "Error", random.choice(ROOT_ERR)
    msg = tmpl.format(cls=random.choice(CLASSES),
                      f=f"/data/reco/tf_{random.randint(1, 9999)}.root",
                      n=random.randint(1024, 5_000_000))
    return f"{level} in <{where}>: {msg}"


def _free_line() -> str:
    return random.choice(FREE).format(
        tf=random.randint(13_000_000, 13_999_999), n=random.randint(1, 5_000_000),
        ms=random.randint(1, 900),
    )


def make_line() -> str:
    # ~55% ROOT-framework lines (some carry severity), ~45% free-form (none).
    return _root_line() if random.random() < 0.55 else _free_line()


def write_loading_block(f) -> None:
    """The multiline case: a header line + indented continuation lines that the
    stdout_multiline parser folds into one record."""
    for line in LOADING_BLOCK:
        f.write(line + "\n")


def main() -> None:
    print(f"[stdout] starting on {NODE_ID} -> {STDOUT_DIR}/epn*.log @ {BASE_RATE}/s",
          flush=True)
    os.makedirs(STDOUT_DIR, exist_ok=True)

    # One line-buffered handle per EPN, opened lazily. Each host's file opens with
    # the indented module-loading block (the multiline case).
    handles: dict = {}

    def handle(host: str):
        f = handles.get(host)
        if f is None:
            f = open(os.path.join(STDOUT_DIR, f"{host}.log"), "a", buffering=1)
            write_loading_block(f)
            handles[host] = f
        return f

    while True:
        handle(pick_host()).write(make_line() + "\n")
        sleep_for_rate(BASE_RATE, BURST_MULTIPLIER)


if __name__ == "__main__":
    main()
