import datetime
import random

HOSTS = ["epn000", "epn146", "epn228", "epn235", "epn323", "epn-infra08",
         "epn-infra09", "epn-infra12", "epn-infra13"]

DETECTORS = ["MCH", "MFT", "TRD", "GLO", "EMC", "TOF", "FV0", "MID", "ITS",
             "FDD", "TPC", "CPV", "HMP", "PHS", "ZDC"]

QC_STAGES = ["Digits", "Clusters", "Tracks", "Tracklets", "RecPoints",
             "Errors", "Rofs", "TracksMc", "MUONTracks"]

RUNS = [567986, 567987, 567991, 568004, 568010]

PARTITIONS = ["", "", "", "pdp-partition-a", "pdp-partition-b"]

USERS = ["epn", "swenzel", "root"]

ERRSOURCES = ["InfoLogger.h", "runnerUtils.cxx", "DataProcessingDevice.cxx",
              "TaskRunner.cxx", "Dispatcher.cxx", "odc-grpc-server.cxx"]

INFO_MESSAGES = [
    "Status request for ODC v0.87.2.0.gf4233f6 (DDS 3.16.7.g39e3365) from [ipv4:10.161.69.39:57220]",
    "Status: found 0 partition(s)",
    "Task {task} initialised with {n} cycles",
    "Monitoring backend connected to influxdb-unix",
    "Processing cycle {n} finished in {ms} ms",
    "Received EndOfStream from device {task}",
    "State change RUN -> READY requested by ECS",
    "Registering {n} objects to the QC database",
    "Publishing {n} MOs for detector {det}",
    "Device {task} entered state RUNNING",
    "GRPGeomHelper loaded objects for run {run}",
    "Timeframe {n} accepted, {ms} ms since arrival",
]

WARNING_MESSAGES = [
    "No URL provided for Bookkeeping. Nothing will be stored nor retrieved.",
    "Seen TFID equal to 0, which is not expected in production data. Will use 1 instead, will not warn further.",
    "Could not update CCDB objects requested by GRPGeomHelper",
    "Do not invoke Process Monitor more frequent then every 1s",
    "Dropping timeframe {n}, the buffer for {task} is full",
    "Cycle {n} took {ms} ms, longer than the configured period",
    "Detector {det} reported {n} empty ROFs in this cycle",
    "Reconnecting to the monitoring backend, attempt {n}",
]

ERROR_MESSAGES = [
    "Could not find the DPL InfoLogger",
    "Failed to open CCDB object for detector {det} at run {run}",
    "Task {task} threw std::runtime_error: no data available",
    "Timeframe {n} dropped, downstream device {task} is not responding",
    "Cannot allocate shared memory segment of {n} MB",
    "Checker {task} could not evaluate quality for {det}",
]

FATAL_MESSAGES = [
    "Device {task} aborted after an unrecoverable transport error",
    "Shared memory segment lost, the partition cannot continue",
]

DEBUG_MESSAGES = [
    "Entering callback for {task}",
    "Cache hit for CCDB path {det}/Calib/Params",
]

WEIGHTS = [
    ("info", 880, INFO_MESSAGES, [11, 13, 21]),
    ("warning", 78, WARNING_MESSAGES, [6, 11]),
    ("error", 33, ERROR_MESSAGES, [1, 6, 11]),
    ("debug", 7, DEBUG_MESSAGES, [21]),
    ("system", 2, INFO_MESSAGES, [1]),
    ("fatal", 1, FATAL_MESSAGES, [1]),
]

SEVERITY_CHAR = {"info": "I", "warning": "W", "error": "E", "fatal": "F",
                 "debug": "D", "system": "Sys"}

_TOTAL = sum(w for _, w, _, _ in WEIGHTS)


def _pick_severity(rng):
    point = rng.randrange(_TOTAL)
    running = 0
    for name, weight, messages, levels in WEIGHTS:
        running += weight
        if point < running:
            return name, messages, levels
    return WEIGHTS[0][0], WEIGHTS[0][2], WEIGHTS[0][3]


def one(rng, when):
    severity, messages, levels = _pick_severity(rng)
    detector = rng.choice(DETECTORS)
    stage = rng.choice(QC_STAGES)
    system = rng.choice(["QC", "QC", "QC", "ODC", "Monitoring", "ECS",
                         "Readout"])
    if system == "QC":
        facility = rng.choice([f"task/{stage}", f"qc-task-{detector}-{stage}"])
        rolename = f"qc-task-{detector}-{stage}"
    elif system == "ODC":
        facility = "ODC"
        rolename = "production"
        detector = None
    elif system == "Monitoring":
        facility = "Library"
        rolename = "monitoring"
        detector = None
    else:
        facility = system.lower()
        rolename = f"{system.lower()}-{rng.randrange(1, 9)}"

    template = rng.choice(messages)
    message = (template
               .replace("{task}", rolename)
               .replace("{det}", detector or "GLO")
               .replace("{run}", str(rng.choice(RUNS)))
               .replace("{n}", str(rng.randrange(1, 9000)))
               .replace("{ms}", str(rng.randrange(4, 4200))))

    has_run = rng.random() < 0.45
    stamp = when.isoformat().replace("+00:00", "Z")
    ingest = (when + datetime.timedelta(
        milliseconds=rng.randrange(80, 1400))).isoformat().replace("+00:00", "Z")
    record = {
        "@timestamp": stamp,
        "collector_time": stamp,
        "ingest_time": ingest,
        "severity": SEVERITY_CHAR[severity],
        "severity_norm": severity,
        "origin_host": rng.choice(HOSTS),
        "log_source": "infologger",
        "level": rng.choice(levels),
        "rolename": rolename,
        "pid": rng.randrange(100000, 4000000),
        "username": rng.choice(USERS),
        "system": system,
        "facility": facility,
        "detector": detector,
        "partition": rng.choice(PARTITIONS) or None,
        "run": rng.choice(RUNS) if has_run else None,
        "message": message,
    }
    if severity in ("error", "fatal"):
        record["errcode"] = rng.choice([2001, 2103, 3004, 4110, 5002])
        record["errline"] = rng.randrange(40, 900)
        record["errsource"] = rng.choice(ERRSOURCES)
    record["hostname"] = record["origin_host"]
    return record


def make(count, span_seconds, seed=7):
    rng = random.Random(seed)
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(seconds=span_seconds)
    rows = []
    for i in range(count):
        offset = span_seconds * (i / float(count))
        jitter = rng.uniform(-0.4, 0.4)
        when = start + datetime.timedelta(seconds=offset + jitter)
        rows.append(one(rng, when))
    rows.sort(key=lambda r: r["@timestamp"])
    return rows
