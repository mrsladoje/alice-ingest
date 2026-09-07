#!/usr/bin/env python3
"""Put real collector records through the real index templates.

`replaycheck.py` proves the collector emits the right fields. It says nothing
about whether OpenSearch will accept them, and the two failure modes there are
both silent in different ways.

The application indices are `dynamic: false`, so a field nobody mapped is stored
in `_source` and **never searchable** — it looks present in Discover's document
view and cannot be found by a query. The InfoLogger index is
`dynamic: "strict"`, so a field nobody mapped **rejects the whole document**.
The ingest pipeline sets fields the collector never emits, which is exactly how
a strict rejection gets introduced by a change to a file that is not the mapping.

This creates the pipeline and the indices from
`deploy/roles/opensearch_bootstrap/templates/templates.sh.j2`, indexes the
records the collector actually produced, and checks that every field survived
and is searchable.
"""
import argparse
import contextlib
import http.server
import json
import os
import re
import shutil
import tempfile
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TEMPLATES = os.path.join(
    REPO, "deploy", "roles", "opensearch_bootstrap", "templates",
    "templates.sh.j2")

# Values the Ansible render would supply. Only the ones the blocks below use.
JINJA = {
    "log_primary_shards_storage": "1",
    "log_primary_shards_worker": "1",
    "cockpit_metrics_index": "cockpit-metrics",
    "trend_rollup_index": "trend-rollup",
    "template_catalog_index": "template-catalog",
}


def blocks():
    """Every NAME=$(cat <<'JSON' ... JSON) block, with Jinja settled."""
    source = open(TEMPLATES).read()
    found = {}
    for match in re.finditer(r"^([A-Z_]+)=\$\(cat <<'JSON'\n(.*?)\nJSON\n\)",
                             source, re.S | re.M):
        name, body = match.group(1), match.group(2)
        body = re.sub(r"\{%\s*raw\s*%\}|\{%\s*endraw\s*%\}", "", body)
        body = re.sub(r"\{\{\s*([a-z_]+)\s*\}\}",
                      lambda m: JINJA.get(m.group(1), "1"), body)
        found[name] = json.loads(body)
    return found


def call(url, path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


class Incomplete(Exception):
    """A read that could not answer for everything it was asked about.

    Every catalog sequence below is judged on documents this helper returned,
    so a read that comes back short makes the judgement wrong in the direction
    that passes: a stale contribution nobody could see is a stale contribution
    nobody reports. Raised rather than returned, because there is no useful
    partial answer to `which documents does the catalog hold`.
    """


def refresh_is_complete(status, body, what):
    """A refresh reports `total` as the number of configured shard COPIES.

    One node with an unassigned replica reports two total, one successful and
    zero failed on a completely healthy refresh, so `failed` is the only field
    that means something went wrong here.

    False means the index is not there, which is an answer: several sequences
    below delete the catalog on purpose. Anything else that is not a whole
    answer raises.
    """
    if status == 404:
        return False
    if status != 200:
        raise Incomplete("%s answered %d" % (what, status))
    if ((body or {}).get("_shards") or {}).get("failed"):
        raise Incomplete("%s failed on a shard" % what)
    return True


def search_is_complete(status, body, what):
    """A search reports `total` as SHARDS, so every one has to be accounted for.

    A timed-out search is reported with 200 OK and whatever hits it had
    collected, and an empty page from a failed shard is byte-identical to an
    empty page from an index with nothing in it.
    """
    if status == 404:
        return False
    if status != 200:
        raise Incomplete("%s answered %d" % (what, status))
    body = body or {}
    if body.get("timed_out"):
        raise Incomplete("%s timed out and returned a partial answer" % what)
    shards = body.get("_shards") or {}
    if shards.get("failed"):
        raise Incomplete("%s failed on %d shard(s)" % (what, shards["failed"]))
    total = shards.get("total")
    parts = [shards.get(k) for k in ("successful", "skipped", "failed")]
    if isinstance(total, int) and all(isinstance(p, int) for p in parts):
        if sum(parts) < total:
            raise Incomplete("%s answered for %d of %d shards"
                             % (what, sum(parts), total))
    return True


def catalog_documents(url, family=None):
    status, body = call(url, "/template-catalog/_refresh", method="POST")
    if not refresh_is_complete(status, body, "the catalog refresh"):
        return []
    query = {"term": {"kind": "template"}}
    if family:
        query = {"bool": {"filter": [{"term": {"kind": "template"}},
                                     {"term": {"family": family}}]}}
    status, response = call(url, "/template-catalog/_search",
                            {"size": 200, "query": query}, "POST")
    if not search_is_complete(status, response, "the catalog listing"):
        return []
    return [dict(hit["_source"], _id=hit["_id"])
            for hit in response.get("hits", {}).get("hits", [])]


class _Relay(http.server.BaseHTTPRequestHandler):
    """Forward every request to the cluster, and break the ones a rule names.

    Two of the sequences below need a fault that cannot be produced from
    outside the process under test: a lookup that fails between the write and
    the commit, and an index replaced while a scan is already reading it. Both
    are ordinary on a real cluster and neither has a knob, so the cluster is
    reached through here instead of directly.
    """

    upstream = None
    rule = None
    after = None

    def log_message(self, *args):
        return

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        broken = self.rule(self.command, self.path, body)
        if broken:
            payload = b'{"error": "injected by mappingcheck"}'
            self.send_response(broken)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        request = urllib.request.Request(
            self.upstream + self.path, data=body, method=self.command,
            headers={"Content-Type": self.headers.get("Content-Type",
                                                      "application/json")})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        if self.after is not None:
            # Between the upstream applying the request and the client learning
            # that it did. Run here rather than after the response is written,
            # so the client cannot get ahead of it: that window is the whole
            # point of the fault.
            self.after(self.command, self.path, status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = _serve


@contextlib.contextmanager
def fault_proxy(url, rule, after=None):
    """A URL that behaves like the cluster until `rule` says otherwise.

    `rule(method, path, body)` returns a status code to answer with instead of
    forwarding, or None to forward. It may also reach the cluster itself, which
    is how an index gets replaced underneath a scan.

    `after(method, path, status)` runs once the upstream has applied a request
    and before the client is told so. That window -- the write has landed, the
    writer does not know it yet -- is where a catalog replacement does its
    worst.
    """
    handler = type("_Injected", (_Relay,),
                   {"upstream": url, "rule": staticmethod(rule),
                    "after": staticmethod(after) if after else None})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_catalog(url, script, index, state, node="node-01", page=5000,
                max_lines=200000):
    env = dict(os.environ,
               ALICE_TEMPLATING_PATH=os.path.join(REPO, "tools", "templating"),
               OS_URL=url, ALICE_NODE_ID=node, CATALOG_STATE_DIR=state,
               CATALOG_PAGE=str(page), CATALOG_MAX_LINES=str(max_lines))
    return subprocess.run([sys.executable, script, "--index", index],
                          capture_output=True, text=True, env=env)


def catalog_summary(result):
    """The JSON line the service prints, so a pass can be asserted on."""
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not lines:
        raise AssertionError("the catalog pass failed: %s"
                             % (result.stderr or "")[-300:])
    return json.loads(lines[-1])


def load_records(url, index, rows, refresh=True):
    payload = []
    for row in rows:
        payload.append(json.dumps({"index": {"_id": row["_id"]}}))
        payload.append(json.dumps(
            {k: v for k, v in row.items() if k != "_id"}))
    body = ("\n".join(payload) + "\n").encode()
    req = urllib.request.Request(
        url + "/%s/_bulk?refresh=%s" % (index, "true" if refresh else "false"),
        data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"})
    urllib.request.urlopen(req, timeout=60).read()


def load_catalog_module(url, state, node="node-01", page=5000,
                        max_lines=200000):
    """The catalog service as an importable module, not a subprocess.

    One scenario has to fail the service in the middle -- published, but the
    confirmation lost -- and the only honest way to do that is to make the real
    function raise. Everything else runs it as the timer does.
    """
    import importlib
    os.environ.update(
        OS_URL=url, ALICE_NODE_ID=node, CATALOG_STATE_DIR=state,
        ALICE_TEMPLATING_PATH=os.path.join(REPO, "tools", "templating"),
        CATALOG_PAGE=str(page), CATALOG_MAX_LINES=str(max_lines))
    path = os.path.join(REPO, "deploy", "roles", "template_catalog", "files")
    if path not in sys.path:
        sys.path.insert(0, path)
    import template_catalog
    return importlib.reload(template_catalog)


def catalog_state_machine(url, tpl):
    """What the catalog PUBLISHES, across the three sequences that broke it.

    Every assertion here reads the documents in the catalog index. A miner-only
    test cannot see any of these faults: in all three the miner was right and
    the published documents were wrong.
    """
    script = os.path.join(
        REPO, "deploy", "roles", "template_catalog", "files",
        "template_catalog.py")
    index = "catalog-state-probe"
    problems = []

    # Two messages that land in ONE drain cluster and make it generalise: the
    # rate is a float in the first and an integer in the second, so the template
    # moves from <FLOAT> to <NUM> when the second arrives.
    first = ("TfBuilder received timeframe 41 from readout link seven "
             "at rate 1.5 MB/s")
    second = ("TfBuilder received timeframe 42 from readout link seven "
              "at rate 2 MB/s")

    def fresh(rows, refresh_interval=None):
        call(url, "/" + index, method="DELETE")
        call(url, "/template-catalog", method="DELETE")
        settings = {"number_of_shards": 1, "number_of_replicas": 0}
        if refresh_interval is not None:
            # "-1" disables automatic refresh, which is the only way to hold a
            # write in the durable-but-not-searchable state on purpose.
            settings["refresh_interval"] = refresh_interval
        call(url, "/" + index,
             {"mappings": tpl["APPLICATION_MAPPINGS"]["template"]["mappings"],
              "settings": settings},
             "PUT")
        # A production-like refresh interval, not the one-second default. The
        # catalog template ships 30s, and a write acknowledged inside that
        # window is durable but NOT yet searchable. One of the sequences below
        # turns on exactly that difference, and at the default it would pass by
        # accident within a second.
        call(url, "/template-catalog",
             {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]["mappings"],
              "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                           "refresh_interval": "60s"}},
             "PUT")
        if rows:
            load_records(url, index, rows)
        return tempfile.mkdtemp(prefix="catalog-sm-")

    # No field on the record decides when the scan reads it: the ordering key
    # is the shard sequence number, assigned by OpenSearch as it indexes. The
    # times below are content, and two of the sequences set them against the
    # indexing order on purpose.
    def record(doc_id, message, when, indexed=None):
        doc = {"_id": doc_id, "message": message, "log_source": "stdout",
               "severity": "INFO", "log_time": "12:20:37",
               "program": "TfBuilder", "node": "node-01",
               "collector_time": when}
        doc["ingest_time"] = when if indexed is None else indexed
        return doc

    # --- 1. a template that generalises must not become two documents --------
    state = fresh([record("a", first, 1000)])
    run_catalog(url, script, index, state)
    load_records(url, index, [record("b", second, 2000)])
    result = run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    if result.returncode != 0:
        problems.append("the generalisation pass failed: %s"
                        % result.stderr[-300:])
    docs = catalog_documents(url, "dpl")
    live = [d for d in docs if not d.get("superseded_by")]
    if len(live) != 1:
        problems.append(
            "a template that generalised left %d live documents, wanted 1: %s"
            % (len(live), [(d.get("template"), d.get("count")) for d in docs]))
    elif live[0].get("count") != 2:
        problems.append(
            "the generalised template counts %s, wanted 2; the count stayed on "
            "the text it was published under before it generalised"
            % live[0].get("count"))
    elif "<FLOAT>" in str(live[0].get("template")):
        problems.append("the live document still holds the ungeneralised text")
    retired = [d for d in docs if d.get("superseded_by")]
    if retired and any(d.get("count") for d in retired):
        problems.append("a retired document still claims a count: %s"
                        % [(d["_id"], d.get("count")) for d in retired])

    # --- 2. a pass that dies before saving its state must not double ---------
    state = fresh([record("a", first, 1000), record("b", second, 2000)])
    run_catalog(url, script, index, state)
    before = [d for d in catalog_documents(url, "dpl")
              if not d.get("superseded_by")]
    # Exactly what a crash between the bulk write and the state save leaves:
    # the documents are published and the progress is not.
    shutil.rmtree(state, ignore_errors=True)
    os.makedirs(state, exist_ok=True)
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    after = [d for d in catalog_documents(url, "dpl")
             if not d.get("superseded_by")]
    want = sum(d.get("count", 0) for d in before)
    got = sum(d.get("count", 0) for d in after)
    if want != 2:
        problems.append("the first pass counted %d, wanted 2" % want)
    elif got != want:
        problems.append(
            "a retry after a lost progress save changed the count from %d to "
            "%d; the same record was counted twice" % (want, got))

    # --- 3. a retry that finds NEW records waiting ---------------------------
    #
    # The recovery test above replays an unchanged dataset, which is the easy
    # half. The hard half is a retry that reads more than the pass it is
    # retrying: the batch is no longer the one the pass number was spent on, so
    # a guard keyed on the pass number alone rejects the larger count and the
    # difference is lost for good.
    state = fresh([record("a", first, 1000)])
    run_catalog(url, script, index, state)
    # NOTHING is inspected here, deliberately. `catalog_documents` refreshes the
    # index, and an assertion placed here was silently doing the service's work
    # for it: the cleanup that runs after a state loss reads the catalog with a
    # SEARCH, and a write acknowledged inside the refresh interval is durable
    # but not yet searchable. With the refresh in the test, the cleanup found
    # the earlier document and the sequence passed. Without it, the cleanup
    # finds nothing, the old contribution survives, and the rebuild is added on
    # top of it.
    #
    # The progress save is lost, and a second record lands before the retry.
    shutil.rmtree(state, ignore_errors=True)
    os.makedirs(state, exist_ok=True)
    load_records(url, index, [record("b", second, 2000)])
    run_catalog(url, script, index, state)
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    docs = catalog_documents(url, "dpl")
    live = [d for d in docs
            if not d.get("superseded_by") and d.get("count", 0) > 0]
    total = sum(d.get("count", 0) for d in live)
    if total != 2:
        problems.append(
            "a retry that found a new record waiting published a count of %d, "
            "wanted 2; the second record was consumed and never counted"
            % total)
    # The total alone does not say whether the earlier contribution was
    # actually cleared: two documents claiming 1 and 2 and one claiming 2 both
    # fail differently. A stale document is the direct symptom of a cleanup
    # that searched an unrefreshed index and found nothing.
    if len(live) != 1:
        problems.append(
            "a rebuild after state loss left %d documents claiming a count, "
            "wanted 1: %s" % (len(live),
                              [(d.get("template"), d.get("count"))
                               for d in live]))

    # --- 3b. the same thing, with the state file INTACT ----------------------
    #
    # Scenario 3 destroys the whole state directory, which is a reprovisioned
    # node. This is the narrower and likelier one: the batch was written down
    # and published, the commit that would have confirmed it failed, and a new
    # record landed before the retry. The pending batch must be finished as it
    # stands, before anything new is read.
    state = fresh([record("a", first, 1000)])
    module = load_catalog_module(url, state)
    real_commit = module.commit

    def refuse(*args, **kwargs):
        raise RuntimeError("the state save failed")

    module.commit = refuse
    try:
        module.run([(index, False)])
    except RuntimeError:
        pass
    finally:
        module.commit = real_commit
    load_records(url, index, [record("b", second, 2000)])
    module.run([(index, False)])          # finishes the pending batch
    module.run([(index, False)])          # only now reads the new record
    shutil.rmtree(state, ignore_errors=True)
    live = [d for d in catalog_documents(url, "dpl")
            if not d.get("superseded_by")]
    total = sum(d.get("count", 0) for d in live)
    if total != 2:
        problems.append(
            "a lost commit followed by a new record published a count of %d, "
            "wanted 2" % total)
    if len(live) != 1:
        problems.append(
            "the recovered pass left %d live documents, wanted 1" % len(live))

    # --- 3c. a record acknowledged but not yet refreshed ---------------------
    #
    # An earlier version of this claimed to test that and did not: its write
    # helper passed refresh=true, so the record was searchable the instant it
    # was written and the condition never existed.
    #
    # Here the probe index has automatic refresh DISABLED, so the second record
    # is durable and invisible to anything that does not refresh. The service
    # refreshes the source index itself, between reading the shard checkpoint
    # and scanning, so it reads the record on its very next pass — and exactly
    # once, which is what the count asserts.
    state = fresh([], refresh_interval="-1")
    load_records(url, index, [record("m", first, 1000)], refresh=False)
    call(url, "/" + index + "/_refresh", method="POST")
    run_catalog(url, script, index, state)          # reads m
    # Written with refresh=false into an index that never refreshes on its own:
    # durable, and invisible until something asks.
    load_records(url, index, [record("n", second, 2000, indexed=500)],
                 refresh=False)
    picked_up = catalog_summary(run_catalog(url, script, index, state))
    if picked_up.get("lines") != 1:
        problems.append(
            "the pass after an unrefreshed write read %s record(s), wanted 1; "
            "the service refreshes the source index before it scans"
            % picked_up.get("lines"))
    # And nothing is read twice when it is asked for again.
    again = catalog_summary(run_catalog(url, script, index, state))
    if again.get("lines"):
        problems.append("a further pass re-read %d record(s)" % again["lines"])
    shutil.rmtree(state, ignore_errors=True)
    live = [d for d in catalog_documents(url, "dpl")
            if not d.get("superseded_by") and d.get("count", 0) > 0]
    total = sum(d.get("count", 0) for d in live)
    if total != 2:
        problems.append(
            "a record that was acknowledged but not searchable when the "
            "previous pass ran published a count of %d, wanted 2" % total)

    # --- 3d. a record indexed AFTER one that carries a later time ------------
    #
    # What a retry or a filesystem-buffered chunk produces. The record reaches
    # the index second, so its shard sequence number is higher, while every
    # field on it -- both times and the identifier -- sorts before the record
    # that arrived first. Ordered by any of those fields it is behind the saved
    # position and lost; ordered by sequence number it is next.
    state = fresh([])
    load_records(url, index, [record("zzz", first, 2_000_000,
                                     indexed=2_000_000)])
    run_catalog(url, script, index, state)
    load_records(url, index, [record("aaa", second, 1_000_000,
                                     indexed=1_000_000)])
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    live = [d for d in catalog_documents(url, "dpl")
            if not d.get("superseded_by") and d.get("count", 0) > 0]
    total = sum(d.get("count", 0) for d in live)
    if total != 2:
        problems.append(
            "a record indexed second, carrying earlier times and a lower "
            "identifier, published a count of %d, wanted 2; the scan resumed "
            "past it" % total)

    # --- 4. equal timestamps across a page boundary --------------------------
    rows = [record("a", first, 500), record("b", first, 500),
            record("c", first, 500)]
    state = fresh(rows)
    run_catalog(url, script, index, state, page=2, max_lines=2)
    run_catalog(url, script, index, state, page=2, max_lines=2)
    run_catalog(url, script, index, state, page=2, max_lines=2)
    shutil.rmtree(state, ignore_errors=True)
    live = [d for d in catalog_documents(url, "dpl")
            if not d.get("superseded_by")]
    total = sum(d.get("count", 0) for d in live)
    if total != 3:
        problems.append(
            "three records sharing one millisecond across a page boundary "
            "published a count of %d, wanted 3" % total)

    # --- 5. one node generalising must not retire a template another node
    #        is still counting -----------------------------------------------
    #
    # `superseded_by` belongs to the DOCUMENT. Setting it as soon as one node's
    # text moves marks a template the other node is still actively counting as
    # no longer current, and every reader that filters superseded documents out
    # -- which is the entire point of the field -- stops counting that node.
    state_a = fresh([record("a", first, 1000)])
    state_b = tempfile.mkdtemp(prefix="catalog-sm-b-")
    # Both nodes read the same one line, so each publishes a count of 1 against
    # the same template: two counts on one document.
    run_catalog(url, script, index, state_b, node="node-02")
    run_catalog(url, script, index, state_a, node="node-01")
    # Only node-01 sees the line that makes the text generalise, so only
    # node-01's contribution moves. node-02's count of 1 is still true of the
    # text the old document holds.
    load_records(url, index, [record("c", second, 2000)])
    run_catalog(url, script, index, state_a, node="node-01")
    for path in (state_a, state_b):
        shutil.rmtree(path, ignore_errors=True)

    docs = catalog_documents(url, "dpl")
    live = [d for d in docs if not d.get("superseded_by")]
    total = sum(d.get("count", 0) for d in live)
    if total != 3:
        problems.append(
            "one node generalising left %d counts visible on current "
            "templates, wanted 3 (node-01's 2 on the new text and node-02's 1 "
            "on the old); the other node's contribution was retired with it: %s"
            % (total, [(d.get("template"), d.get("count"),
                        d.get("nodes"), d.get("superseded_by")) for d in docs]))

    # --- 6. a generalised template keeps the programs it already had ---------
    #
    # The count moves to a new document when the text moves, and the programs
    # have to move with it. Sending only the batch's own programs names the new
    # document after whichever program wrote in the ten minutes the move fell
    # in, and loses every earlier one.
    def named(doc_id, message, when, program):
        return dict(record(doc_id, message, when), program=program)

    state = fresh([named("a", first, 1000, "TfBuilder")])
    run_catalog(url, script, index, state)
    load_records(url, index, [named("b", second, 2000, "StfSender")])
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    live = [d for d in catalog_documents(url, "dpl")
            if not d.get("superseded_by")]
    programs = sorted({p for d in live for p in (d.get("programs") or [])})
    if programs != ["StfSender", "TfBuilder"]:
        problems.append(
            "a generalised template names %s, wanted both programs that wrote "
            "it" % programs)

    # --- 7. a catalog recreated under the same name is rebuilt, not ignored --
    #
    # The name still resolves, so a check that asks only whether it exists sees
    # nothing wrong. Every document this node published is gone, but the local
    # state says they were published, so nothing is republished -- and the
    # source position has not moved, so nothing new is read. The node reports
    # itself idle against an empty catalog for as long as it runs.
    state = fresh([record("a", first, 1000)])
    run_catalog(url, script, index, state)
    before = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    call(url, "/template-catalog", method="DELETE")
    call(url, "/template-catalog",
         {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]["mappings"],
          "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                       "refresh_interval": "60s"}},
         "PUT")
    result = run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    after = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if before != 1:
        problems.append("the catalog-recreation sequence did not start from a "
                        "count of 1, it started from %d" % before)
    elif after != 1:
        summary = (result.stdout or "").strip().splitlines()[-1:]
        problems.append(
            "a catalog recreated under the same name holds %d after the next "
            "pass, wanted 1; the node stayed silent about what it had already "
            "published: %s" % (after, summary))

    # --- 8. the catalog the FIRST publication created is still identified ----
    #
    # The bulk write creates the index when it is not there, so on a node that
    # has never published, the identity only exists after the write. Leaving it
    # unrecorded makes the next comparison null-against-new, which reads as
    # unchanged -- and the check that exists to notice a replaced catalog
    # notices nothing, at exactly the moment it is most likely to matter.
    state = fresh([record("a", first, 1000)])
    call(url, "/template-catalog", method="DELETE")
    run_catalog(url, script, index, state)
    call(url, "/template-catalog", method="DELETE")
    call(url, "/template-catalog",
         {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]["mappings"],
          "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                       "refresh_interval": "60s"}},
         "PUT")
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if total != 1:
        problems.append(
            "a catalog created by the first publication and then replaced holds "
            "%d, wanted 1; its identity was never recorded, so the replacement "
            "read as no change at all" % total)

    # --- 9. a replacement rebuilds from the trees, not from the source -------
    #
    # The source indices are the short-lived half of this system: informational
    # records age out of the node-local index long before the templates mined
    # from them stop being true. Sending the node back to them after a catalog
    # replacement publishes only what has not aged out yet.
    state = fresh([record("a", first, 1000), record("b", second, 2000)])
    run_catalog(url, script, index, state)
    before = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    # The source record ages out. The template mined from it has not.
    call(url, "/%s/_doc/a" % index, method="DELETE")
    call(url, "/%s/_refresh" % index, method="POST")
    call(url, "/template-catalog", method="DELETE")
    call(url, "/template-catalog",
         {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]["mappings"],
          "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                       "refresh_interval": "60s"}},
         "PUT")
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    after = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if before != 2:
        problems.append("the rebuild sequence did not start from a count of 2, "
                        "it started from %d" % before)
    elif after != 2:
        problems.append(
            "after a catalog replacement the count came back as %d, wanted 2; "
            "the rebuild went to the source index instead of the mining tree, "
            "and one of those records no longer exists" % after)

    # --- 10. an unassigned replica must not stop the primary being read -----
    #
    # A refresh reports `total` as the number of configured COPIES, so a healthy
    # refresh on a shard whose replica is unassigned says two total, one
    # successful, zero failed. Reading that as a fault skipped the index every
    # pass, for as long as the replica stayed unassigned -- and one node with
    # `number_of_replicas: 1` is that state permanently, which is what this
    # single-node cluster is.
    call(url, "/" + index, method="DELETE")
    call(url, "/template-catalog", method="DELETE")
    call(url, "/" + index,
         {"mappings": tpl["APPLICATION_MAPPINGS"]["template"]["mappings"],
          "settings": {"number_of_shards": 1, "number_of_replicas": 1}},
         "PUT")
    call(url, "/template-catalog",
         {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]["mappings"],
          "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                       "refresh_interval": "60s"}},
         "PUT")
    load_records(url, index, [record("a", first, 1000)])
    status, health = call(url, "/_cluster/health/%s" % index)
    state = tempfile.mkdtemp(prefix="catalog-sm-")
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if health.get("unassigned_shards", 0) < 1:
        problems.append(
            "the unassigned-replica sequence did not produce an unassigned "
            "replica, so it tested nothing: %s" % health)
    elif total != 1:
        problems.append(
            "a shard with an unassigned replica published %d, wanted 1; a "
            "refresh that reached every ASSIGNED copy was read as a failure "
            "and the primary was never scanned" % total)

    # --- 11. a pass that cannot establish a destination publishes nothing ---
    #
    # The destination is fixed before the payload is sent, so a lookup this node
    # cannot complete stops the pass rather than being recorded as `no change`.
    # The batch is already durable, so the cost is one replayed batch: the next
    # pass establishes the destination and publishes exactly what this one built.
    state = fresh([record("a", first, 1000)])
    call(url, "/template-catalog", method="DELETE")
    lookups = {"n": 0}

    def lookup_fails_once_the_pass_is_committed(method, path, body):
        if not path.startswith("/template-catalog/_settings"):
            return None
        lookups["n"] += 1
        # The first is the one that decides whether the catalog was replaced;
        # let it through so the pass gets as far as building a batch.
        return None if lookups["n"] == 1 else 503

    with fault_proxy(url, lookup_fails_once_the_pass_is_committed) as proxied:
        blocked = run_catalog(proxied, script, index, state)
    during = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if lookups["n"] < 2:
        problems.append("the destination lookup was never reached, so that "
                        "sequence tested nothing")
    else:
        if blocked.returncode == 0:
            problems.append(
                "a pass that could not establish its destination reported "
                "success; it has to publish nothing and stay pending")
        if during:
            problems.append(
                "a pass that could not establish its destination published %d "
                "document(s) anyway" % during)
        if total != 1:
            problems.append(
                "the batch left pending by an unestablished destination came "
                "back as %d, wanted 1" % total)

    # --- 12. a name is not an index, for every request and not only the first -
    #
    # The scan reads the identity once and addresses the index by NAME after
    # that. Replace it in between -- a reindex behind an alias, someone
    # rebuilding a source index -- and the new index answers to the old name
    # with sequence numbers that start again at zero. The records are saved
    # under the key of the index they did not come from, so the next pass finds
    # no mark for the new one and counts every one of them a second time.
    state = fresh([record("a", first, 1000)])
    swapped = {"done": False}

    def replace_the_index_mid_scan(method, path, body):
        if swapped["done"] or not path.startswith("/%s/_search" % index):
            return None
        swapped["done"] = True
        call(url, "/" + index, method="DELETE")
        call(url, "/" + index,
             {"mappings": tpl["APPLICATION_MAPPINGS"]["template"]["mappings"],
              "settings": {"number_of_shards": 1, "number_of_replicas": 0}},
             "PUT")
        load_records(url, index, [record("a", first, 1000)])
        return None

    with fault_proxy(url, replace_the_index_mid_scan) as proxied:
        run_catalog(proxied, script, index, state)
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if not swapped["done"]:
        problems.append("the mid-scan replacement never fired, so that "
                        "sequence tested nothing")
    elif total != 1:
        problems.append(
            "one source record produced a count of %d, wanted 1; the index was "
            "replaced during the scan and its records were marked as read "
            "under the identity of the index before it" % total)

    # --- 13. the destination is fixed BEFORE the write, not found after it --
    #
    # A lookup that runs after the bulk write cannot say which index received
    # it, and it fails in the direction that hides the problem: replace the
    # catalog in the window between the write landing and the writer learning
    # that it did, and the lookup SUCCEEDS. It names the empty replacement. The
    # node records that as the index it wrote to, the next pass compares that
    # identity against itself, finds no change, and reports itself idle over
    # documents that no longer exist.
    #
    # Read BEFORE the write, the same race can only be wrong the other way
    # round: what is recorded is where this pass INTENDED to write, so a write
    # that lands elsewhere leaves the two disagreeing next pass, which is what
    # makes it a rebuild instead of a silence.
    def replace_the_catalog_after_the_write(bulks):
        seen = {"n": 0}

        def hook(method, path, status):
            if not path.startswith("/_bulk") or seen["n"] >= bulks:
                return
            seen["n"] += 1
            call(url, "/template-catalog", method="DELETE")
            call(url, "/template-catalog",
                 {"mappings": tpl["TEMPLATE_CATALOG_TPL"]["template"]
                  ["mappings"],
                  "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                               "refresh_interval": "60s"}},
                 "PUT")
        return hook, seen

    # 13a. the first publication, where the catalog does not exist yet.
    state = fresh([record("a", first, 1000)])
    call(url, "/template-catalog", method="DELETE")
    hook, seen = replace_the_catalog_after_the_write(1)
    with fault_proxy(url, lambda *a: None, after=hook) as proxied:
        run_catalog(proxied, script, index, state)
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if not seen["n"]:
        problems.append("the after-the-write replacement never fired on the "
                        "first publication, so that sequence tested nothing")
    elif total != 1:
        problems.append(
            "a catalog replaced between the first write and the lookup that "
            "followed it holds %d, wanted 1; the identity recorded was the "
            "replacement's, so the next pass saw no change at all" % total)

    # 13b. the same window, on a node whose catalog already exists and whose
    # trees already hold history. This is the one that loses the most: the
    # rebuild that should have republished everything never runs.
    state = fresh([record("a", first, 1000), record("b", second, 2000)])
    hook, seen = replace_the_catalog_after_the_write(1)
    with fault_proxy(url, lambda *a: None, after=hook) as proxied:
        run_catalog(proxied, script, index, state)
    run_catalog(url, script, index, state)
    shutil.rmtree(state, ignore_errors=True)
    total = sum(d.get("count", 0) for d in catalog_documents(url, "dpl"))
    if not seen["n"]:
        problems.append("the after-the-write replacement never fired on the "
                        "rebuild case, so that sequence tested nothing")
    elif total != 2:
        problems.append(
            "a catalog replaced between the write and the lookup that followed "
            "it holds %d, wanted 2; the history the trees still held was never "
            "republished" % total)

    for line in problems:
        print("   FAIL %s" % line)
    if not problems:
        print("   catalog documents: a generalising template stays one "
              "document, a retry does not double, delayed visibility and "
              "delayed indexing are both read, a page boundary loses nothing, "
              "one node's generalisation leaves another node's count current, "
              "programs survive the move, a recreated catalog is rebuilt, and "
              "the rebuild comes from the tree rather than from source records "
              "that may be gone, an unassigned replica does not hide a "
              "healthy primary, an identity that could not be read is not "
              "recorded as no change, an index replaced during a scan is "
              "not counted twice, and a catalog replaced between the write "
              "and the lookup after it is still rebuilt")
    call(url, "/" + index, method="DELETE")
    return problems


def catalog_round_trip(url, collected, tpl):
    """The catalog must carry template identity off a node without the lines.

    Every record here comes out of the real collector, not out of this file.
    The first version of this check hand-wrote a DataDistribution message with
    its `[date][S] ` prefix still attached -- a shape the production collector
    never indexes, because its own datadist parser eats that prefix into `time`
    and `severity`. The catalog split the tree on exactly that prefix, so the
    check passed while production filed every DataDistribution template as dpl.
    Feeding the collector's own output is what makes the assertion mean
    something.

    Four things are checked and each has a way of going wrong quietly. The
    process tree has to split into its two formats. Records that never touch
    the node-local index -- InfoLogger, the daemon log, and everything the
    routing sends straight to durable storage -- have to be mined too. The
    counts have to add across nodes rather than overwrite. And a second pass
    over the same data has to mine nothing.
    """
    script = os.path.join(
        REPO, "deploy", "roles", "template_catalog", "files",
        "template_catalog.py")
    local = "application-logs-local-node-01"
    central = "catalog-central-probe"
    il = "catalog-infologger-probe"
    for index in (local, central, il, "template-catalog"):
        call(url, "/" + index, method="DELETE")

    # Built from the real index templates, not left to dynamic mapping. A bare
    # index makes `node` a TEXT field, and the catalog reads a shared index with
    # a `term` filter on `node` -- which silently matches nothing against text,
    # because the analyzer has already split "node-01" into two tokens. The
    # scoping looked broken and was not; the probe index was.
    shaped = {local: tpl["APPLICATION_MAPPINGS"]["template"],
              central: tpl["APPLICATION_MAPPINGS"]["template"],
              il: tpl["INFOLOGGER_MAPPINGS"]["template"]}
    for index, template in shaped.items():
        call(url, "/" + index,
             {"mappings": template["mappings"],
              "settings": {"number_of_shards": 1, "number_of_replicas": 0}},
             "PUT")

    # Where the collector actually sent each record decides which index it is
    # loaded into here, so the catalog is asked to read the same partition
    # production gives it.
    route = {"family_local.jsonl": local, "family_central.jsonl": central,
             "ildaemon.jsonl": central, "infologger.jsonl": il}
    loaded = {local: 0, central: 0, il: 0}
    for filename, index in route.items():
        payload = []
        for n, record in enumerate(collected.get(filename, [])):
            doc = {k: v for k, v in record.items() if not k.startswith("__")}
            # A shared index is read scoped to the node that wrote the record,
            # so the node field has to be there for the scoping to be exercised.
            doc.setdefault("node", "node-01")
            doc["collector_time"] = doc.get("collector_time") or 1000 + n
            payload.append(json.dumps({"index": {}}))
            payload.append(json.dumps(doc))
            loaded[index] += 1
        if not payload:
            continue
        body = ("\n".join(payload) + "\n").encode()
        req = urllib.request.Request(
            url + "/%s/_bulk?refresh=true" % index, data=body, method="POST",
            headers={"Content-Type": "application/x-ndjson"})
        urllib.request.urlopen(req, timeout=60).read()

    env = dict(os.environ,
               ALICE_TEMPLATING_PATH=os.path.join(REPO, "tools", "templating"),
               OS_URL=url)
    problems = []
    state = tempfile.mkdtemp(prefix="catalog-state-")

    def run_catalog(node):
        result = subprocess.run(
            [sys.executable, script, "--index", local,
             "--shared-index", central, "--shared-index", il],
            capture_output=True, text=True,
            env=dict(env, ALICE_NODE_ID=node,
                     CATALOG_STATE_DIR=os.path.join(state, node)))
        if result.returncode != 0:
            problems.append("catalog run as %s failed: %s"
                            % (node, result.stderr[-300:]))
            return {}
        return json.loads(result.stdout.strip().splitlines()[-1])

    try:
        first = run_catalog("node-01")
        families = set(first.get("families", []))
        # dpl and datadist prove the process tree split; infologger and
        # ildaemon prove the routes that never reach the node-local index are
        # read at all, which the node-local-only default could not do.
        for wanted in ("dpl", "datadist", "infologger", "ildaemon", "dds"):
            if wanted not in families:
                problems.append(
                    "the catalog mined %s; %s is missing"
                    % (sorted(families), wanted))
        again = run_catalog("node-01")
        if again and again.get("lines") != 0:
            problems.append("a second pass re-mined %d lines; the position is "
                            "not resuming" % again["lines"])
        run_catalog("node-02")
    finally:
        shutil.rmtree(state, ignore_errors=True)

    status, body = call(url, "/template-catalog/_refresh", method="POST")
    refresh_is_complete(status, body, "the round-trip refresh")

    # node-02 is scoped to its own records on the two shared indices and every
    # record there says node-01, so only the node-local index is read twice.
    # That is the point of the scoping: a shared index read by three workers
    # must not have one record counted three times.
    status, response = call(
        url, "/template-catalog/_search",
        {"size": 200, "query": {"term": {"kind": "template"}}}, "POST")
    search_is_complete(status, response, "the round-trip listing")
    hits = response.get("hits", {}).get("hits", [])
    by_family = {}
    for hit in hits:
        by_family.setdefault(hit["_source"]["family"], []).append(hit["_source"])
    if "datadist" not in by_family:
        problems.append("no datadist template reached the catalog; the process "
                        "tree did not split on the fields the collector emits")
    two_node = [h for h in hits
                if sorted(h["_source"].get("nodes", [])) == ["node-01",
                                                             "node-02"]]
    if not two_node:
        problems.append("no template records both nodes; counts are "
                        "overwriting rather than adding")
    scoped = [h for h in hits
              if h["_source"]["family"] in ("infologger", "ildaemon")
              and h["_source"].get("nodes") == ["node-01"]]
    if not scoped:
        problems.append("a durable-tier template was attributed to a node that "
                        "did not write it; the shared-index scoping is not "
                        "holding")
    if not problems:
        print("   catalog: %s, every route read, counts add across nodes, "
              "the position resumes" % ", ".join(sorted(by_family)))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9299")
    ap.add_argument("--version", default="4.0.14")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--catalog", action="store_true",
                    help="also exercise roles/template_catalog end to end")
    args = ap.parse_args()

    tpl = blocks()
    failures = []

    status, _ = call(args.url, "/_ingest/pipeline/alice-add-ingest-time",
                     tpl["INGEST_PIPELINE"], "PUT")
    if status >= 300:
        print("   FAIL the ingest pipeline was rejected")
        return 1

    indices = {
        "application-logs-central-000001": tpl["APPLICATION_MAPPINGS"]["template"],
        "infologger-000001": tpl["INFOLOGGER_MAPPINGS"]["template"],
        "template-catalog": tpl["TEMPLATE_CATALOG_TPL"]["template"],
    }
    for name, template in indices.items():
        call(args.url, "/" + name, method="DELETE")
        body = {"mappings": template["mappings"],
                "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                             "index.default_pipeline": "alice-add-ingest-time"}}
        status, response = call(args.url, "/" + name, body, "PUT")
        if status >= 300:
            failures.append("index %s rejected: %s" % (name, response))

    # The records the collector really produced, not a hand-written sample.
    sys.path.insert(0, HERE)
    import replaycheck
    fixtures = os.path.join(HERE, "fixtures")
    with open(os.path.join(fixtures, "infologger.json")) as fh:
        il_records = json.load(fh)
    records, _, work = replaycheck.run(
        args.version, os.path.join(fixtures, "logs"), il_records, 45.0)

    routed = {"family_central.jsonl": "application-logs-central-000001",
              "family_local.jsonl": "application-logs-central-000001",
              "ildaemon.jsonl": "application-logs-central-000001",
              "infologger.jsonl": "infologger-000001"}
    indexed = rejected = 0
    for filename, index in routed.items():
        for record in records.get(filename, []):
            doc = {k: v for k, v in record.items()
                   if not k.startswith("__")}
            doc.setdefault("@timestamp",
                           int(record.get("__time__", time.time()) * 1000))
            status, response = call(args.url, "/%s/_doc" % index, doc, "POST")
            if status >= 300:
                rejected += 1
                if rejected <= 3:
                    failures.append(
                        "%s rejected a %s record: %s"
                        % (index, filename,
                           json.dumps(response.get("error", response))[:300]))
            else:
                indexed += 1
    call(args.url, "/_refresh", method="POST")

    # Every field the collector emits must be searchable, not merely stored.
    # `dynamic: false` stores an unmapped field and silently never indexes it.
    emitted = set()
    for rows in records.values():
        for record in rows:
            emitted.update(k for k in record if not k.startswith("__"))
    # `log` is the exception and it is deliberate: a line no collector parser
    # claimed keeps its text there, and the pipeline renames it to `message`
    # because the mapping is not dynamic and an unmapped field would be stored
    # without ever being searchable. Its absence downstream is the fix working,
    # so it is asserted separately below rather than counted as a hole.
    TRANSFORMED = {"log"}
    unsearchable = []
    for field in sorted(emitted - TRANSFORMED):
        status, response = call(
            args.url, "/application-logs-central-000001,infologger-000001/_count",
            {"query": {"exists": {"field": field}}}, "POST")
        if status < 300 and response.get("count", 0) == 0:
            unsearchable.append(field)

    if "log" in emitted:
        status, response = call(
            args.url, "/application-logs-central-000001/_count",
            {"query": {"exists": {"field": "log"}}}, "POST")
        if status < 300 and response.get("count", 0) != 0:
            failures.append("the pipeline left %d documents with a `log` field; "
                            "an unmapped field is stored and never searchable"
                            % response["count"])
        status, response = call(
            args.url, "/application-logs-central-000001/_count",
            {"query": {"match_phrase": {"message": "DDS configuration"}}}, "POST")
        if status < 300 and response.get("count", 0) == 0:
            failures.append("a line no parser claimed did not reach `message`; "
                            "its text is unsearchable")
        else:
            print("   a line no parser claimed is searchable through `message`")

    # The two fields the ingest pipeline computes, which no collector emits.
    # They are the reason a strict mapping can reject a document after a change
    # to a file that is not the mapping, and the reason a cross-source search
    # works at all.
    status, response = call(
        args.url, "/application-logs-central-000001,infologger-000001/_search",
        {"size": 0, "aggs": {
            "sev": {"terms": {"field": "severity_norm", "size": 20}},
            "prog": {"terms": {"field": "program", "size": 30}}}}, "POST")
    if status >= 300:
        failures.append("could not aggregate on the pipeline's own fields")
    else:
        aggs = response["aggregations"]
        norms = {b["key"] for b in aggs["sev"]["buckets"]}
        progs = {b["key"] for b in aggs["prog"]["buckets"]}
        for wanted in ("info", "warning", "error", "state"):
            if wanted not in norms:
                failures.append("severity_norm never produced %r" % wanted)
        # One program from each source that carries one, including InfoLogger,
        # whose identity is `facility` and is copied across by the pipeline.
        for wanted, why in (("mft-tracker", "the process tree"),
                            ("dds-agent", "DDS"),
                            ("o2-infologger-daemon", "the daemon log"),
                            ("ctf-writer", "InfoLogger, via facility")):
            if wanted not in progs:
                failures.append("program is missing %r, from %s" % (wanted, why))
        print("   severity_norm: %s" % ", ".join(sorted(norms)))
        print("   program resolved on every source that has one")

    print("   indexed %d records, %d rejected" % (indexed, rejected))
    if unsearchable:
        failures.append("stored but not searchable: %s"
                        % ", ".join(unsearchable))

    if args.catalog:
        # An incomplete read invalidates every count these are judged on, so
        # it fails the run rather than quietly shortening it.
        try:
            failures.extend(catalog_round_trip(args.url, records, tpl))
        except Incomplete as exc:
            failures.append("the catalog round trip could not be judged: %s"
                            % exc)
        try:
            failures.extend(catalog_state_machine(args.url, tpl))
        except Incomplete as exc:
            failures.append("the catalog sequences could not be judged: %s"
                            % exc)

    import shutil
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    for line in failures:
        print("   FAIL %s" % line)
    if not failures:
        print("   every field the collector emits is mapped and searchable")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
