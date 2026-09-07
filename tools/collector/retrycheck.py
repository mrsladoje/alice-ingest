#!/usr/bin/env python3
"""Retry the collector's OpenSearch output and count what the cluster ends up with.

`replaycheck.py` writes to a file sink, so it can prove which records the
collector emits and how it routes them. It cannot prove anything about
duplication, because a file sink has no acknowledgement to lose: it never
retries, so a configuration that duplicates every record on retry passes it
green. This is the check that was missing.

The fault injected is the one that actually happens on a loaded cluster: the
bulk request arrives, OpenSearch APPLIES it, and the success response does not
get back. Fluent Bit cannot distinguish that from a request that never arrived,
so it retries the whole chunk. Whether that produces one document or two is
decided by whether the records carry a document identifier of their own.

Two things are asserted and both are needed. That the cluster holds one document
per distinct record is the property. That more operations were applied than there
are documents is the proof the retry really happened -- without it the first
assertion passes on a run where nothing was ever retried, which is how a test of
this kind quietly stops testing anything.

Run:
    python3 tools/collector/retrycheck.py
    python3 tools/collector/retrycheck.py --version 4.0.1 --version 4.0.14
"""
import argparse
import collections
import json
import os
import http.client
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MKCONFIG = os.path.join(REPO, "tools", "soak", "mkconfig.py")
FIXTURES = os.path.join(HERE, "fixtures")
PROXY = os.path.join(HERE, "retryproxy.py")

NODE_ID = "node-01"
HTTP_PORT = 2024
IL_PORT = 5175
# How many applied writes to report back as failures. One is the fault; three
# is how many independent chances the run gets to observe it being retried,
# because which chunk the first one lands on is arbitrary. A run where no
# record is attempted twice is reported as proving nothing rather than as a
# pass, so this is about the harness being usable, not about the assertion
# being loose.
DROPS = 3

# Where to look for a captured journal. On this laptop the container runtime's
# own virtual machine keeps one, and one file out of it is enough: the point is
# that the records went through the journal filters, not how many there were.
DEFAULT_JOURNAL = "/var/log/journal"


def capture_journal(path):
    """One journal file, copied into a directory of its own, or None.

    A whole journal directory is every entry the machine has ever written, and
    reading it would make this arm the slowest part of the check for no gain.
    """
    probe = "retrycheck-journal-%d" % os.getpid()
    out = os.path.join(CACHE, "journal")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    found = sh("docker", "run", "--rm", "--name", probe,
               "-v", "%s:/j:ro" % path, "alpine:latest",
               "sh", "-c", "find /j -name '*.journal' | head -1")
    name = (found.stdout or "").strip()
    if not name:
        return None
    copied = sh("docker", "run", "--rm", "-v", "%s:/j:ro" % path,
                "-v", "%s:/out" % out, "alpine:latest",
                "cp", name, "/out/system.journal")
    if copied.returncode != 0:
        return None
    return out
OS_PORT = 9297
# Every index the log outputs write to. The health output is not among them:
# it is Fluent Bit's own counters, it goes to a different index under a
# different template, and it is left out of this deliberately rather than
# forgotten -- see the note at the end of run_arm.
INDICES = ["application-logs-local-%s" % NODE_ID, "application-logs-central",
           "infologger"]

# Colima shares the user's home and not /private/tmp, so a work tree outside it
# gives the container an empty bind mount and no error.
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "alice-retrycheck")


def sh(*cmd, **kw):
    return subprocess.run(list(cmd), capture_output=True, text=True, **kw)


def call(path, body=None, method="GET", timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (OS_PORT, path), data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        # A cluster still starting closes the connection without answering.
        return 0, {}


def wait_cluster(deadline=180.0):
    end = time.time() + deadline
    while time.time() < end:
        status, body = call("/_cluster/health", timeout=5)
        if status == 200 and body.get("status") in ("green", "yellow"):
            return True
        time.sleep(2.0)
    return False


def wait_fluentbit(deadline=60.0):
    """Wait until the engine has bound its port, not until Docker returns."""
    end = time.time() + deadline
    while time.time() < end:
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/api/v1/metrics" % HTTP_PORT,
                timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def install_templates():
    """The shipped mappings, so `doc_id` is exercised under the real templates.

    It matters that this is not a bare index. The InfoLogger mapping is
    `dynamic: "strict"`, and the output leaves the identifier in the document
    body as well as using it as the `_id`, so an unmapped `doc_id` rejects every
    InfoLogger record outright. A test on a dynamic index would never see that.
    """
    sys.path.insert(0, HERE)
    import mappingcheck
    tpl = mappingcheck.blocks()
    call("/_ingest/pipeline/alice-add-ingest-time", tpl["INGEST_PIPELINE"],
         "PUT")
    mappings = {
        "application-logs-local-%s" % NODE_ID: tpl["APPLICATION_MAPPINGS"],
        "application-logs-central": tpl["APPLICATION_MAPPINGS"],
        "infologger": tpl["INFOLOGGER_MAPPINGS"],
    }
    failures = []
    for name, template in mappings.items():
        call("/" + name, method="DELETE")
        body = {"mappings": template["template"]["mappings"],
                "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                             "index.default_pipeline": "alice-add-ingest-time"}}
        status, response = call("/" + name, body, "PUT")
        if status >= 300:
            failures.append("index %s rejected: %s" % (name, response))
    return failures


class Incomplete(Exception):
    """A read that could not answer for everything it was asked about.

    Raised rather than returned, because every count in this file is derived
    from the listing and a short listing makes all of them wrong in the same
    direction. There is no useful partial answer here.
    """


def _complete(status, body, what):
    """A read either answered for the whole index or it did not count.

    An empty page from a failed shard is byte-identical to an empty page from an
    index with nothing in it, and a timed-out search is reported with 200 OK and
    whatever hits it had collected. `call` turns a refused connection into
    `(0, {})`, which is below every `>= 300` test ever written here and reads as
    an empty result too. All three ways of finding nothing had to be separated
    from actually finding nothing.
    """
    if status == 404:
        return False
    if status != 200:
        raise Incomplete("%s answered %d" % (what, status))
    if body.get("timed_out"):
        raise Incomplete("%s timed out and returned a partial answer" % what)
    shards = body.get("_shards") or {}
    if shards.get("failed"):
        raise Incomplete("%s failed on %d shard(s)" % (what, shards["failed"]))
    return True


def documents():
    """Every document, keyed by index AND identifier, with what it holds.

    Keyed by both, because `_id` is unique only WITHIN an index. The collector
    assigns one identifier per record and the record is written to whichever
    index it is routed to, so a record that reached TWO destinations -- one of
    the duplications this check exists to find -- collapsed into a single entry
    and the listing came back one short, in both arms at once.

    The destination is part of the answer and not a detail: a record that ends
    up in the wrong index is neither lost nor duplicated, and a count that only
    adds up across all three indices would not notice.
    """
    for name in INDICES:
        status, body = call("/%s/_refresh" % name, method="POST")
        _complete(status, body, "the refresh of %s" % name)
    found = {}
    for name in INDICES:
        after = None
        while True:
            # Paged, not capped. A fixed `size` silently truncates, and a
            # truncated listing understates both arms by the same amount, which
            # is the shape of mistake this check exists to catch.
            body = {"size": 1000, "sort": [{"_id": "asc"}],
                    "_source": ["doc_id", "message", "log"]}
            if after:
                body["search_after"] = after
            status, page = call("/%s/_search" % name, body, "POST")
            if not _complete(status, page, "the listing of %s" % name):
                break
            hits = page.get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                found[(name, hit["_id"])] = dict(hit.get("_source", {}),
                                                 _index=name)
            after = hits[-1]["sort"]
    return found


def total_documents():
    """How many documents there are, without listing them, or None.

    The settle loop asks once a second and a captured journal is tens of
    thousands of records, so listing them there would cost more than the run.

    None means the count could not be trusted this time. The loop waits for the
    number to stop changing, so an undercount that repeats is a run that stops
    early and measures a half-written cluster.
    """
    total = 0
    for name in INDICES:
        status, body = call("/%s/_refresh" % name, method="POST")
        try:
            if not _complete(status, body, "the refresh of %s" % name):
                continue
            status, body = call("/%s/_count" % name)
            if not _complete(status, body, "the count of %s" % name):
                continue
        except Incomplete:
            return None
        total += body.get("count", 0)
    return total


def cross_check(found, counted):
    """Two independent reads of a cluster nothing is writing to any more.

    They have to agree, and when they did not the listing was the one that was
    wrong: a duplicate across destinations collapsed under a key that was only
    the identifier, so the reader answered 43 where the cluster held 44. A count
    is no substitute for the listing -- it cannot say WHICH records, or where --
    but it is a second opinion the listing cannot talk itself out of.
    """
    if counted is not None and counted != len(found):
        raise Incomplete(
            "the listing returned %d documents and the cluster counts %d"
            % (len(found), counted))


def contents(found):
    """What arrived, per index, as a multiset of record bodies."""
    out = {}
    for source in found.values():
        body = source.get("message") or source.get("log") or ""
        out.setdefault(source["_index"], collections.Counter())[body] += 1
    return out


def reference(version, seconds):
    """What the collector EMITS, measured independently of the cluster.

    Without this the check compared the documents in OpenSearch against the
    identifiers it had watched go past in the proxy, and a record lost before
    the proxy ever saw it is missing from both sides at once. Feeding the
    checker a single fixture record produced no failure at all.

    The reference is the same fixtures through the same rendered configuration
    with a FILE sink, so it is produced without a cluster, without a proxy and
    without a retry. It is the fixture contents and their destinations, which is
    the thing both arms are supposed to reproduce.
    """
    sys.path.insert(0, HERE)
    import replaycheck
    with open(os.path.join(FIXTURES, "infologger.json")) as handle:
        il_records = json.load(handle)
    records, _, work = replaycheck.run(
        version, os.path.join(FIXTURES, "logs"), il_records, seconds)
    shutil.rmtree(work, ignore_errors=True)
    routed = {"family_local.jsonl": "application-logs-local-%s" % NODE_ID,
              "family_central.jsonl": "application-logs-central",
              "ildaemon.jsonl": "application-logs-central",
              "infologger.jsonl": "infologger"}
    wanted = {}
    for filename, index in routed.items():
        for row in records.get(filename, []):
            body = row.get("message") or row.get("log") or ""
            wanted.setdefault(index, collections.Counter())[body] += 1
    return wanted


def identifier_survives_every_filter(config_path):
    """Every allowlist in the rendered configuration must keep `doc_id`.

    An allowlist is a DENY list for everything unnamed, and the identifier is
    not a field an operator reads -- so it is exactly the kind of thing that
    gets left off one. It was: the journal allowlist dropped it, and the journal
    became the one source whose records reached OpenSearch with no identifier at
    all, duplicating every one of them on a retry.

    This is checked on the rendered text rather than only in a running arm,
    because exercising the journal needs a captured journal and most hosts do
    not have one. A static answer that always runs beats a dynamic one that
    usually does not.
    """
    missing = []
    current = None
    keys = None
    with open(config_path) as handle:
        lines = handle.readlines()
    for line in lines:
        stripped = line.strip()
        if keys is not None and stripped.startswith("- ") \
                and not stripped.startswith("- name:"):
            keys.append(stripped[2:].strip())
            continue
        if keys is not None:
            # Leaving the list, however it ends -- a blank line, the next
            # setting, or the next filter. Checking only some of those endings
            # was the first version, and the shipped configuration ends this
            # list with the next filter, so it checked nothing at all.
            if "doc_id" not in keys:
                missing.append((current, sorted(keys)))
            keys = None
        if stripped.startswith("- name:"):
            current = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("allowlist_key:"):
            keys = []
    if keys is not None and "doc_id" not in keys:
        missing.append((current, sorted(keys)))
    return missing


def strip_identifier(path):
    """The negative control: the same run with the identifier taken out.

    A test that only ever passes proves nothing about what it is testing. This
    removes `id_key` from the rendered configuration and nothing else, so the
    run differs from the real one in exactly the property under test. It must
    duplicate; if it does not, the fault was never injected and the green run
    above is worthless.
    """
    kept = [line for line in open(path) if "id_key:" not in line]
    with open(path, "w") as handle:
        handle.writelines(kept)


def run_arm(version, network, drop_first, seconds, with_id=True,
            journal=None):
    """One Fluent Bit run through the proxy, and what the cluster then holds.

    With `journal` set, the fixtures are replaced by a captured systemd journal
    and the tail inputs see an empty tree, so every record in the run came
    through the journal filters. That is the only way to reach the one source
    whose records are rewritten by an allowlist.
    """
    for name in INDICES:
        call("/" + name, method="DELETE")
    install_templates()

    work = tempfile.mkdtemp(prefix="arm-", dir=CACHE)
    config_dir = os.path.join(work, "cfg")
    state_dir = os.path.join(work, "state")
    storage = os.path.join(work, "storage")
    for path in (config_dir, state_dir, storage):
        os.makedirs(path)
    shutil.copy(PROXY, os.path.join(state_dir, "retryproxy.py"))

    subprocess.run([
        sys.executable, MKCONFIG,
        "--out", os.path.join(config_dir, "collector.yaml"),
        "--parsers-out", os.path.join(config_dir, "parsers.yaml"),
        "--sink", "opensearch", "--sink-host", "proxy", "--sink-port", "9200",
        "--flush", "1", "--odc", "on", "--odc-path", "/logs/odc/*.log",
    ] + (["--journald", "on", "--journald-path", "/journal"] if journal
         else ["--journald", "off"]), check=True, capture_output=True)
    if not with_id:
        strip_identifier(os.path.join(config_dir, "collector.yaml"))

    proxy = "retrycheck-proxy-%d" % os.getpid()
    engine = "retrycheck-fb-%d" % os.getpid()
    for name in (proxy, engine):
        sh("docker", "rm", "-f", name)

    subprocess.run([
        "docker", "run", "-d", "--name", proxy,
        "--network", network, "--network-alias", "proxy",
        "-v", "%s:/state" % state_dir,
        "-e", "RETRY_UPSTREAM=sink:9200",
        "-e", "RETRY_DROP_FIRST=%d" % drop_first,
        "python:3.11-slim", "python3", "/state/retryproxy.py", "9200",
    ], check=True, capture_output=True)

    ildaemon = os.path.join(FIXTURES, "logs", "varlog",
                            "o2-infologger-daemon.log")
    if journal:
        # An empty log tree, so the tail inputs contribute nothing and every
        # record in this run is a journal entry.
        logs = os.path.join(work, "logs")
        os.makedirs(logs)
        mounts = ["-v", "%s:/logs:ro" % logs,
                  "-v", "%s:/journal:ro" % os.path.abspath(journal)]
    else:
        logs = os.path.join(FIXTURES, "logs")
        mounts = ["-v", "%s:/logs:ro" % logs,
                  "-v", "%s:/var/log/o2-infologger-daemon.log:ro" % ildaemon]
    try:
        subprocess.run([
            "docker", "run", "-d", "--name", engine, "--network", network,
            "-p", "%d:2020" % HTTP_PORT,
            "-v", "%s:/etc/fluent-bit:ro" % config_dir,
            "-v", "%s:/storage" % storage,
            "-e", "ALICE_NODE_ID=%s" % NODE_ID,
            "-e", "ALICE_LOG_ROOT=/logs",
            "-e", "ALICE_FB_STORAGE_PATH=/storage",
            "-e", "ALICE_FB_HTTP_LISTEN=0.0.0.0",
            "-e", "ALICE_FB_HTTP_PORT=2020",
            "-p", "%d:5175" % IL_PORT,
            "-e", "ALICE_INFOLOGGER_TCP_PORT=5175",
            "-e", "ALICE_OS_HTTP_PORT=9200",
        ] + mounts + [
            "fluent/fluent-bit:%s" % version,
            "/fluent-bit/bin/fluent-bit", "-c", "/etc/fluent-bit/collector.yaml",
        ], check=True, capture_output=True)
        if not wait_fluentbit():
            raise SystemExit("fluent-bit never served its metrics endpoint")
        # The InfoLogger index is the one with a `dynamic: "strict"` mapping,
        # and the output leaves the identifier in the document body as well as
        # using it as the `_id`. An unmapped `doc_id` rejects every one of these
        # records outright, so a run that never feeds the tcp input would miss
        # the strictest consequence of the change.
        if not journal:
            sys.path.insert(0, HERE)
            import replaycheck
            with open(os.path.join(FIXTURES, "infologger.json")) as handle:
                replaycheck.feed_infologger(json.load(handle), IL_PORT)
        # Wait for the FAULT, then for the records to go quiet -- in that
        # order, because the quiet comes first. Fluent Bit backs its retries
        # off, so a run that stops when the document count stops moving stops
        # before the retried chunk arrives, and then reports that nothing was
        # duplicated. That is a green run for the wrong reason, and this rig
        # has produced one before.
        stats_file = os.path.join(state_dir, "stats.json")

        def proxy_stats():
            try:
                with open(stats_file) as handle:
                    return json.load(handle)
            except (OSError, ValueError):
                return {}

        settled = last = 0
        stable = 0
        while settled < seconds:
            time.sleep(1.0)
            settled += 1
            if not proxy_stats().get("records_attempted_twice"):
                continue
            total = total_documents()
            stable = stable + 1 if total and total == last else 0
            last = total if total is not None else -1
            if stable >= 8:
                break
        logs = sh("docker", "logs", engine).stderr
    finally:
        for name in (engine, proxy):
            sh("docker", "stop", "-t", "5", name)
            sh("docker", "rm", "-f", name)

    with open(os.path.join(state_dir, "stats.json")) as handle:
        stats = json.load(handle)
    found = documents()
    cross_check(found, total_documents())
    shutil.rmtree(work, ignore_errors=True)
    return stats, found, logs


def compare(label, got, wanted, allow_extra):
    """One arm's contents against the reference, per destination."""
    problems = []
    for index in sorted(set(wanted) | set(got)):
        want, have = wanted.get(index, collections.Counter()), got.get(
            index, collections.Counter())
        short = want - have
        if short:
            problems.append(
                "%s: %s is missing %d record(s) the collector emitted, first: "
                "%r" % (label, index, sum(short.values()),
                        sorted(short)[0][:80]))
        extra = have - want
        if extra and not allow_extra:
            problems.append(
                "%s: %s holds %d record(s) more than the collector emitted, "
                "first: %r" % (label, index, sum(extra.values()),
                               sorted(extra)[0][:80]))
    return problems


def journal_check(version, network, seconds, journal):
    """The same fault, over records that went through the journal allowlist.

    An allowlist is a DENY list for everything unnamed, and the identifier is
    not a field an operator reads, so it is exactly what gets left off one. It
    was: the journal allowlist dropped `doc_id`, and the journal became the one
    source whose records reached OpenSearch with no identifier at all. The
    fixture arms above cannot see that, because no fixture is a journal entry.

    Nothing here needs to know how many entries the captured journal holds. That
    every applied operation carried an identifier is the direct test, and it
    needs no expectation at all.
    """
    failures = []
    print("== fluent-bit %s, journal" % version)
    stats, found, _ = run_arm(version, network, drop_first=DROPS,
                              seconds=seconds, journal=journal)
    applied = stats["operations_applied"]
    distinct = set(stats["ids"])
    print("   %d journal operations applied, %d distinct records, %d documents"
          % (applied, len(distinct), len(found)))

    if not found:
        failures.append("the captured journal produced no documents at all, so "
                        "this arm tested nothing")
        return failures
    if stats["operations_without_id"]:
        failures.append(
            "%d of %d journal operations carried no identifier -- a filter on "
            "the journal path is dropping it"
            % (stats["operations_without_id"], applied))
    if not stats["records_attempted_twice"]:
        failures.append("no journal record was attempted twice, so nothing "
                        "was tested. Raise --seconds.")
    elif len(found) != len(distinct):
        failures.append(
            "%d journal documents for %d distinct records -- the retry %s"
            % (len(found), len(distinct),
               "duplicated" if len(found) > len(distinct) else "lost records"))
    else:
        print("   every retried journal record is one document, not two")

    control, control_found, _ = run_arm(version, network, drop_first=DROPS,
                                        seconds=seconds, with_id=False,
                                        journal=journal)
    print("   control, identifier removed: %d journal documents"
          % len(control_found))
    if not control["records_attempted_twice"]:
        failures.append("the journal control attempted no record twice, so it "
                        "proves nothing. Raise --seconds.")
    elif len(control_found) <= len(found):
        failures.append(
            "the journal control did not duplicate: %d documents without the "
            "identifier against %d with it"
            % (len(control_found), len(found)))
    else:
        print("   the journal control duplicates -- %d documents against %d -- "
              "so journal records are deduplicated by the identifier too"
              % (len(control_found), len(found)))
    for line in failures:
        print("   FAIL %s" % line)
    return failures


def check(version, network, seconds):
    failures = []
    print("== fluent-bit %s" % version)

    wanted = reference(version, seconds)
    expected = sum(sum(c.values()) for c in wanted.values())
    print("   reference: %d records the collector emits, across %d indices"
          % (expected, len(wanted)))
    if expected < 40:
        failures.append(
            "the reference run produced only %d records; the fixtures hold 43, "
            "so the comparison below would not mean anything" % expected)

    stats, found, logs = run_arm(version, network, drop_first=DROPS,
                                 seconds=seconds)
    applied = stats["operations_applied"]
    refused = stats["duplicates_refused"]
    distinct = set(stats["ids"])
    print("   %d operations applied, %d refused as duplicates, %d distinct "
          "records, %d documents"
          % (applied, refused, len(distinct), len(found)))

    if stats["other_rejections"]:
        failures.append("OpenSearch rejected operations for a reason other "
                        "than a duplicate: %s"
                        % json.dumps(stats["other_rejections"][:3]))
    if stats["operations_without_id"]:
        failures.append(
            "%d of %d applied operations carried no document identifier, so a "
            "retry writes a second document"
            % (stats["operations_without_id"], applied))
    if not stats["bulk_responses_dropped"]:
        failures.append("no success was ever reported as a failure; nothing "
                        "was tested")
    elif not stats["records_attempted_twice"]:
        # The whole point of the run. If the dropped response never led to a
        # second attempt at the same record, the document count below is not
        # evidence of anything. A re-delivered CHUNK is not enough: a chunk can
        # be re-sent because the first attempt never reached the cluster.
        failures.append(
            "no record was attempted twice inside the run window, so nothing "
            "was tested. Raise --seconds.")
    else:
        # The output writes with `create`, so the cluster answers a second
        # attempt at an identifier it already holds with 409 rather than
        # overwriting. Either answer is deduplication; this one is stricter,
        # because it can never replace a document that is already correct.
        print("   %d records were attempted twice, %d of those refused as "
              "duplicates" % (stats["records_attempted_twice"], refused))

    if not found:
        failures.append("no document reached OpenSearch at all")
    elif len(found) != len(distinct):
        failures.append(
            "%d documents for %d distinct records -- the retry %s"
            % (len(found), len(distinct),
               "duplicated" if len(found) > len(distinct) else "lost records"))
    else:
        print("   every retried record is one document, not two")

    missing = distinct - {doc_id for _, doc_id in found}
    if missing:
        failures.append("%d records never became documents, first: %s"
                        % (len(missing), sorted(missing)[0]))
    # Against the reference, not against what the proxy happened to watch go
    # past. A record lost before the proxy saw it is absent from both of those.
    failures.extend(compare("with the identifier", contents(found), wanted,
                            allow_extra=False))

    mismatched = [k for k, v in found.items() if v.get("doc_id") != k[1]]
    if mismatched:
        failures.append(
            "%d documents are not stored under the identifier the collector "
            "assigned" % len(mismatched))

    control, control_found, _ = run_arm(version, network, drop_first=DROPS,
                                        seconds=seconds, with_id=False)
    control_applied = control["operations_applied"]
    print("   control, identifier removed: %d operations applied, %d documents"
          % (control_applied, len(control_found)))
    print("   control: %d records attempted twice"
          % control["records_attempted_twice"])
    if not control["bulk_responses_dropped"]:
        failures.append("the control reported no success as a failure, so it "
                        "proves nothing")
    elif not control["records_attempted_twice"]:
        failures.append(
            "the control attempted no record twice, so it proves nothing. "
            "Raise --seconds.")
    elif control["operations_without_id"] != control_applied:
        failures.append(
            "the control still carried an identifier on %d of %d applied "
            "operations, so it is not a control"
            % (control_applied - control["operations_without_id"],
               control_applied))
    elif control["duplicates_refused"]:
        failures.append(
            "the control had %d operations refused as duplicates, which "
            "without an identifier is impossible"
            % control["duplicates_refused"])
    elif len(control_found) != control_applied:
        # Every applied operation without an identifier is a NEW document, so
        # these must be equal. If they are not, the counts are not measuring
        # what they claim and neither arm means anything.
        failures.append(
            "the control applied %d operations and holds %d documents; without "
            "an identifier those must be equal"
            % (control_applied, len(control_found)))
    elif len(control_found) <= len(found):
        failures.append(
            "the control did not duplicate: the same input gave %d documents "
            "without an identifier and %d with one, so this run is not evidence "
            "that the identifier is what prevents the duplication"
            % (len(control_found), len(found)))
    else:
        print("   the control duplicates -- %d documents without the "
              "identifier against %d with it -- so the result above is the "
              "identifier and not luck"
              % (len(control_found), len(found)))
    # The control may hold MORE than the reference -- that is the point of it --
    # but it must not hold less. A control that lost records would show the same
    # inequality for the wrong reason.
    failures.extend(compare("control", contents(control_found), wanted,
                            allow_extra=True))

    for line in logs.splitlines():
        if "retry" in line and "succeeded" in line:
            print("   fluent-bit: %s" % line.split("] ", 2)[-1].strip())
            break
    for failure in failures:
        print("   FAIL %s" % failure)
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="append", default=[],
                    help="repeat; defaults to the two deployed versions")
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--journal", default=DEFAULT_JOURNAL,
                    help="a directory of systemd journal files; the journal "
                         "arm is skipped when it holds none")
    args = ap.parse_args()
    versions = args.version or ["4.0.1", "4.0.14"]

    os.makedirs(CACHE, exist_ok=True)
    journal = capture_journal(args.journal)
    if not journal:
        print("   NOTE no systemd journal at %s, so the journal arm is "
              "skipped; the allowlist check above still covers it statically"
              % args.journal)
    network = "retrycheck-%d" % os.getpid()
    cluster = "retrycheck-os-%d" % os.getpid()
    sh("docker", "network", "create", network)
    sh("docker", "rm", "-f", cluster)
    subprocess.run([
        "docker", "run", "-d", "--name", cluster,
        "--network", network, "--network-alias", "sink",
        "-p", "%d:9200" % OS_PORT,
        "-e", "discovery.type=single-node",
        "-e", "DISABLE_SECURITY_PLUGIN=true",
        "-e", "OPENSEARCH_JAVA_OPTS=-Xms768m -Xmx768m",
        "opensearchproject/opensearch:3.7.0",
    ], check=True, capture_output=True)

    failures = []
    try:
        rendered = os.path.join(CACHE, "rendered.yaml")
        subprocess.run([
            sys.executable, MKCONFIG, "--out", rendered,
            "--parsers-out", os.path.join(CACHE, "rendered-parsers.yaml"),
            "--sink", "opensearch", "--odc", "on", "--journald", "on",
        ], check=True, capture_output=True)
        dropped = identifier_survives_every_filter(rendered)
        if dropped:
            for name, keys in dropped:
                # Printed here and not only counted. A failure that is tallied
                # and never shown is a failure nobody acts on, and this one is
                # raised before any container starts, where nothing else prints.
                message = (
                    "the %s allowlist drops doc_id, so every record it touches "
                    "reaches OpenSearch with no identifier and a retry writes "
                    "it twice; it keeps %s" % (name, keys))
                print("   FAIL %s" % message)
                failures.append(message)
        else:
            print("   every allowlist in the rendered configuration keeps the "
                  "identifier")

        if not wait_cluster():
            print("   FAIL OpenSearch never came up")
            return 1
        for version in versions:
            try:
                failures.extend(check(version, network, args.seconds))
                if journal:
                    failures.extend(
                        journal_check(version, network, args.seconds, journal))
            except Incomplete as exc:
                message = ("fluent-bit %s: %s, so nothing this arm counted "
                           "means anything" % (version, exc))
                print("   FAIL %s" % message)
                failures.append(message)
    finally:
        sh("docker", "rm", "-f", cluster)
        sh("docker", "network", "rm", network)
        shutil.rmtree(CACHE, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
