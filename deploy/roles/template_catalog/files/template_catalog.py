#!/usr/bin/env python3
"""Carry template identity off a worker without carrying its informational logs.

The collector routes by severity, so 96.9 percent of the process tree never
leaves the node it was written on. That is the point of the design and it is
also a hole: a template catalog built from the durable tier alone would never
see the templates of the lines that stayed behind, and those are most of them.

This closes the hole without undoing the routing. It reads every index this node
writes to, mines templates with the frozen per-family recipe, and sends only the
canonical template text and its counts to the shared catalog. A raw
informational line still never crosses the network. What crosses is one document
per distinct template per family, updated in place.

Five properties matter and each one is tested against the PUBLISHED documents,
not against the miner:

  * The recipe here is the same object the offline miner uses. It is imported
    from tools/templating, not restated, and it installs the same FLOAT/NUM
    merge rule, so a template mined on a worker and the same template mined from
    the archive get the same text.
  * Every route is read, not just the node-local one. Records that go straight
    to the durable tier and InfoLogger's own index are mined here too, scoped to
    the records this node itself wrote, so three workers reading one shared
    index do not each add the same count.
  * The mining tree survives between runs, so the same messages produce the same
    templates whichever ten-minute batch they fell in.
  * A template that GENERALISES carries its count with it. Drain rewrites a
    cluster's template as it sees more of the shape -- `reader 7 opened` becomes
    `reader <NUM> opened` -- and the document identifier is derived from that
    text. Without the migration below, the count stays on the old text and the
    new text starts again at one, so one template becomes two documents that
    each under-count. This tracks the identifier a cluster was last published
    under and retires it when it moves.
  * A retry cannot double a count and cannot lose one. The batch is written
    down BEFORE it is published, and an unconfirmed batch is finished before
    anything new is read. Counts inside it are ABSOLUTE, not increments: what is
    sent is the cluster's whole size in this node's tree, and the update SETS
    it, so republishing the same batch changes nothing.

    Absolute counts alone were not enough, and the hole is worth stating because
    it is not obvious. If a retry RE-READS, it is no longer the same batch: new
    records have arrived, the counts are larger, and the pass guard -- which
    exists to stop a stale pass overwriting a newer one -- rejects them. The
    records are then consumed without ever being counted. Replaying the exact
    pending batch, from its own trees, without reading anything new, is what
    closes it.

  * Nothing is read from an answer that is not whole. A search that timed out
    and a shard that failed both come back 200 OK with whatever hits were
    collected, and an empty page from a failed shard is byte-identical to an
    empty page from a shard with nothing left in it. Only the flag beside the
    hits separates `there is no more` from `I could not tell you`.
  * A name is not an index. The read position is keyed by the index UUID as
    well as its concrete name, because sequence numbers start again at zero in
    a new index and a name can be reused; and the local state is discarded when
    the CATALOG's UUID changes, because a catalog deleted and recreated under
    the same name still resolves while every document this node published is
    gone.
  * The pass number only ever goes up. It is milliseconds since the epoch so
    that it survives the loss of the local state, and it is floored above what
    this node is known to have written so that it survives a clock stepped
    backwards -- which would otherwise have every update take the `noop` branch
    while the run reported success.
  * Memory is bounded by a stated number of templates and not by hope. Depth
    and max_children bound the SHAPE of the tree, neither caps how many clusters
    it holds, and serialising the tree is the largest allocation this process
    makes.

All the state that has to move together -- the mining trees, the read position
per index, the pass number, the programs each cluster has been written by, and
the identifier each cluster was published under -- lives in ONE file that is
replaced atomically, and both the file and its directory are synchronised.
Splitting it was the third defect: a bulk write followed by a separate progress
save has a window in which the write landed and the progress did not, and the
retry counted everything again.
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.environ.get(
    "ALICE_TEMPLATING_PATH", "/opt/alice-ingest/templating"))

from drain3.persistence_handler import PersistenceHandler   # noqa: E402

import drainbench                                           # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
CATALOG_INDEX = os.environ.get("CATALOG_INDEX", "template-catalog")
NODE_ID = os.environ.get("ALICE_NODE_ID", socket.gethostname())
PAGE = int(os.environ.get("CATALOG_PAGE", "5000"))
MAX_LINES = int(os.environ.get("CATALOG_MAX_LINES", "200000"))
STATE_DIR = os.environ.get("CATALOG_STATE_DIR", "/var/lib/alice-template-catalog")
STATE_FILE = "catalog-state.json"
# Bumped when the meaning of the saved position changes. Version 1 stored a
# collector_time key, version 2 an ingest_time key, version 3 a shard sequence
# number keyed by index NAME. Version 4 keys it by index name and index UUID,
# because a name can be reused by a different index whose sequence numbers
# start again at zero. Resuming from any older file would land in the wrong
# place, so it is discarded rather than read.
STATE_VERSION = 4

# The ceiling on how many distinct templates this node will learn, across every
# family. It is a memory bound, and the number comes from measurement rather
# than from a round figure: a drain tree of 20,000 clusters holds 61 MB, and
# serialising it -- jsonpickle builds the whole string in memory -- peaks at
# 208 MB. The unit allows 512 MB. Fifty thousand clusters would peak at 437 MB
# and leave nothing for the rest of the process.
#
# For scale, the whole three-corpus archive pass -- 55,963,050 lines -- mined
# 4,221 templates. This ceiling is 4.7 times that.
MAX_TEMPLATES = int(os.environ.get("CATALOG_MAX_TEMPLATES", "20000"))

CENTRAL_INDEX = os.environ.get("CATALOG_CENTRAL_INDEX", "application-logs-central")
INFOLOGGER_INDEX = os.environ.get("CATALOG_INFOLOGGER_INDEX", "infologger")


def request(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        OS_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def template_id(family, template):
    """A stable identifier for one canonical template.

    sha1 of the family and the template text, NOT Python's hash(): that is
    randomised per process, so the same template would land under a different
    document identifier on every run and the catalog would grow a duplicate an
    hour instead of a count.

    It is derived from the TEXT on purpose, so that two nodes that mined the
    same template independently write to the same document. The cost is that the
    identifier moves when drain generalises the text, and `run` below is what
    pays it.
    """
    digest = hashlib.sha1(("%s\n%s" % (family, template)).encode("utf-8"))
    return "%s:%s" % (family, digest.hexdigest()[:24])


# --- state -------------------------------------------------------------------
class _Buffered(PersistenceHandler):
    """A drain3 persistence handler that keeps the state in memory.

    drain3 would happily write its own file per family. It must not: the tree,
    the read position and the pass number have to become durable at the same
    instant or a crash between them is a lost batch or a doubled one. Holding
    the serialised tree here lets `save_state` below write all of it in one
    atomic replace.
    """

    def __init__(self, initial=None):
        self.state = initial

    def save_state(self, state):
        self.state = state

    def load_state(self):
        return self.state


def state_path():
    return os.path.join(STATE_DIR, STATE_FILE)


def load_state():
    try:
        with open(state_path()) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return fresh_state()
    if state.get("version") != STATE_VERSION:
        # The saved position means something else. Starting over is correct and
        # cheap; the rebuild clears this node's previous counts first.
        return fresh_state()
    state.setdefault("pending", None)
    return state


def save_state(state):
    """One file, replaced atomically. Never two writes that can half-happen.

    Both the file and its DIRECTORY are synchronised. Syncing only the file is
    the common half of this: the bytes are on the platter but the rename that
    publishes them is still in the directory's write-back cache, so a power cut
    between the two leaves the old state file -- and the batch it describes is
    then read and counted a second time. The rename is the durable act here, and
    a rename is a directory write.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=STATE_DIR, prefix=".catalog-state-", delete=False)
    try:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, state_path())
    except BaseException:
        os.unlink(handle.name)
        raise
    fd = os.open(STATE_DIR, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def catalog_identity():
    """WHICH catalog index this is, or None when there is not one.

    The identity is the index UUID, not the name. Asking only whether the name
    resolves was the earlier answer and it is not enough: delete the catalog and
    recreate it under the same name and the name still resolves, while every
    document this node published is gone. The local state then still claims those
    documents exist, so nothing is republished, and because the source position
    has not moved either the node reads nothing new and reports itself idle. The
    catalog stays empty for as long as the node keeps running.

    A UUID changes when the index is recreated and does not change for anything
    else, so it separates `wiped` from `written to but not yet refreshed`. Two
    weaker tests were tried first and both were wrong:

      * a document count is read through the refresh interval. The bulk write
        below uses `refresh=false`, so a count taken straight after a pass
        reads zero and every pass concluded the catalog had been wiped, threw
        its own state away, and re-mined the whole index from the beginning.
      * a missing bookkeeping document is also what a failed write looks like,
        and rebuilding the world every time one of those fails is worse than
        the problem it solves.
    """
    try:
        got = request("/%s/_settings?flat_settings=true" % CATALOG_INDEX)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    for body in got.values():
        uuid = (body.get("settings") or {}).get("index.uuid")
        if uuid:
            return uuid
    return None


def refresh_is_complete(result):
    """Whether a refresh FAILED anywhere, which is not the same as `not everywhere`.

    A refresh reports `total` as the number of configured shard COPIES, so on a
    cluster where a replica is unassigned -- one node and `number_of_replicas: 2`
    is the shipped worker layout, and every rolling restart produces it for a
    while -- a completely healthy refresh reports two total, one successful and
    zero failed. Reading `successful < total` as a fault was the first version
    of this check and it stopped the catalog reading anything at all: the index
    was skipped every pass, for as long as the replica stayed unassigned, and
    the service reported itself idle while the primary held records nobody was
    reading.

    `failed` is the field that means something went wrong. An unavailable copy
    is not a failed operation, and the copy that did answer is the primary.
    """
    if not isinstance(result, dict):
        return False
    return not (result.get("_shards") or {}).get("failed")


def search_is_complete(page):
    """Whether a search answered for every SHARD it was asked about.

    OpenSearch reports a partial answer with 200 OK. A search that timed out, or
    one whose shard failed, returns the hits it managed to collect and a flag,
    and reading that flag is the whole difference between `there is nothing more`
    and `I could not tell you`. Treating the second as the first advances the
    read position over records that were never read.

    Unlike a refresh, a search's `total` counts SHARDS and not copies, so the
    arithmetic below is meaningful here: every shard is either successful,
    skipped or failed, and anything else unaccounted for is a shard that did not
    answer.
    """
    if not isinstance(page, dict):
        return False
    if page.get("timed_out"):
        return False
    shards = page.get("_shards") or {}
    if shards.get("failed"):
        return False
    total = shards.get("total")
    parts = [shards.get(k) for k in ("successful", "skipped", "failed")]
    if isinstance(total, int) and all(isinstance(p, int) for p in parts):
        if sum(parts) < total:
            return False
    return True


# --- reading -----------------------------------------------------------------
def miner_for(family, handler):
    """The frozen recipe, as an object, from the same module the archive uses.

    This calls drainbench.recipe_miner rather than rebuilding a TemplateMiner
    from the recipe tables. Restating the construction looked equivalent and was
    not: it left out the FLOAT/NUM merge rule, which round 6 measured as the
    difference between 6.3 percent and 1.0 percent of lines landing on a
    template whose content is a single wildcard.

    The handler is attached only long enough to LOAD, then detached. drain3
    reads a persistence handler as an instruction to snapshot, and its snapshot
    rule fires on every message: a new cluster or a changed template snapshots by
    definition, and the periodic rule compares the seconds since the last save
    against `snapshot_interval_minutes * 60`, which for the interval 0 this
    recipe sets is a comparison against zero that is always true. Every single
    line therefore serialised the whole tree with jsonpickle. That is slow, and
    it is also the largest allocation this process makes -- three times the size
    of the tree itself -- so it is what decides the memory ceiling. It is also
    pointless here: a tree made durable at any moment other than the batch
    boundary does not match the read position saved beside it. `dump_trees`
    reattaches the handler for exactly one save, at that boundary.
    """
    miner = drainbench.recipe_miner(family, persistence=handler)
    miner.persistence_handler = None
    return miner


def index_shards(name):
    """The concrete indices behind a name, each with its UUID and shard count.

    A name here may be a rollover alias, and `_seq_no` is per SHARD of a
    CONCRETE index -- two backing indices both have a shard 0 whose sequence
    numbers start at zero. So is the UUID part of the key, and for the same
    reason one step further out: delete an index and recreate it under the same
    name and its sequence numbers start again at zero too, while the saved
    position still says shard 0 was read up to some large number. Every record
    in the new index below that number is then skipped for good. Keying the
    position by UUID makes a recreated index a place this node has never read,
    which is what it is.
    """
    try:
        got = request("/%s/_settings?flat_settings=true" % name)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    out = {}
    for concrete, body in got.items():
        try:
            settings = body["settings"]
            out[concrete] = (settings.get("index.uuid") or concrete,
                             int(settings["index.number_of_shards"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def index_uuid(concrete):
    """The UUID of ONE concrete index, or None when it is not there.

    Asked through `index_shards` rather than with a narrower request, so there
    is one place that knows how a settings answer is shaped and one fallback
    when a cluster does not report a UUID at all.
    """
    entry = index_shards(concrete).get(concrete)
    return entry[0] if entry else None


def shard_checkpoints(concrete):
    """The highest sequence number per shard below which nothing is missing.

    `max_seq_no` is the highest number ASSIGNED. It is not a boundary: a
    sequence number is handed out before the write reaches Lucene, so with
    concurrent writes number 1 can be searchable while number 0 is still in
    flight. A scan that reads 1 and remembers it has stepped over 0 for good.

    `global_checkpoint` is the boundary. Every operation at or below it has
    COMPLETED on every in-sync copy, so nothing below it can still arrive. The
    minimum across the reported copies is taken because a replica may report a
    staler value than the primary, and the smaller number is the safe one.
    """
    try:
        got = request("/%s/_stats?level=shards" % concrete)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    shards = got.get("indices", {}).get(concrete, {}).get("shards", {})
    out = {}
    for shard, copies in shards.items():
        marks = [c["seq_no"]["global_checkpoint"] for c in copies
                 if isinstance(c.get("seq_no"), dict)
                 and "global_checkpoint" in c["seq_no"]]
        if marks:
            out[int(shard)] = min(marks)
    return out


def scan(name, marks, node_scoped, rotation=0):
    """Every record this node has not read, up to a boundary that cannot move.

    The ordering key is `_seq_no` and the boundary is the shard's global
    checkpoint. Getting here took four wrong answers and the last two are worth
    stating, because they look right:

      * `collector_time` is when the collector saw the line, and
        `ingest_time` is stamped when an ingest node RECEIVES the document.
        Both are fields fixed BEFORE indexing, and nothing fixed before
        indexing can order by indexing. A retried chunk, or a write held up
        while a later one completes, lands behind a position saved on either.
      * `_seq_no` alone is not enough either. It is assigned before the write
        reaches Lucene, so concurrent writes can become searchable out of
        order: number 1 visible while number 0 is still in flight. Remembering
        the highest number SEEN steps over the gap permanently.

    So the pass reads the checkpoint FIRST, refreshes SECOND, and scans THIRD,
    and that order is the argument:

      * every operation at or below the checkpoint had completed when the
        checkpoint was read;
      * the refresh happens after that, so all of them are searchable by the
        time the scan runs;
      * the scan is bounded at the checkpoint, so it cannot see -- and cannot
        remember -- anything from the region that is still settling.

    Anything above the checkpoint is simply read by a later pass, once the
    checkpoint has moved past it.

    The identity is re-read before every page is yielded, because everything
    after the first request addresses the index by NAME and a name is not an
    index. Delete it and recreate it under the same name in the middle of a
    scan -- which is what a reindex behind an alias, or someone rebuilding a
    source index, does -- and the sequence numbers start again at zero while
    this pass is still reading. The records come from the new index and the
    position is saved under the old index's key, so the next pass finds no mark
    for the new one, reads it from the beginning, and counts every record it
    already counted a second time. The UUID is what separates them, and a UUID
    is never reused, so a page that still answers with the same one came from
    the index whose checkpoint bounds it.

    Each shard gets its OWN budget and the shards are visited in an order that
    rotates from pass to pass. One shared budget starting at shard 0 every time
    is a starvation bug rather than a fairness preference: a shard busy enough to
    fill the whole budget on its own is read every pass, and the shards behind it
    are never read at all, for as long as it stays busy.
    """
    for concrete, (uuid, shards) in sorted(index_shards(name).items()):
        key = "%s/%s" % (concrete, uuid)
        # Read the boundary before making anything searchable, never after.
        checkpoints = shard_checkpoints(concrete)
        try:
            refreshed = request("/%s/_refresh" % concrete, method="POST")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            continue
        if not refresh_is_complete(refreshed):
            # A shard that did not refresh may hold completed operations below
            # the checkpoint that are still not searchable. Reading this index
            # now would step over them. Leave it for the next pass.
            continue
        budget = max(PAGE, MAX_LINES // max(shards, 1))
        replaced = False
        for offset in range(shards):
            shard = (rotation + offset) % shards
            ceiling = checkpoints.get(shard)
            if ceiling is None or ceiling < 0:
                continue
            after = (marks.get(key) or {}).get(str(shard))
            read = 0
            while read < budget:
                bounds = {"lte": ceiling}
                if after is not None:
                    bounds["gt"] = after
                must = [{"range": {"_seq_no": bounds}}]
                if node_scoped:
                    must.append({"term": {"node": NODE_ID}})
                body = {
                    "size": PAGE,
                    "seq_no_primary_term": True,
                    "query": {"bool": {"filter": must}},
                    "sort": [{"_seq_no": "asc"}],
                    "_source": ["message", "log", "log_source", "program",
                                "collector_time", "log_time", "severity"],
                }
                try:
                    page = request(
                        "/%s/_search?preference=_shards:%d" % (concrete, shard),
                        body, method="POST")
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        break
                    raise
                if not search_is_complete(page):
                    # An empty page from a failed shard reads exactly like the
                    # end of the shard. Stop here rather than treat it as the
                    # end; what was already yielded is contiguous and stands.
                    break
                hits = page.get("hits", {}).get("hits", [])
                if not hits:
                    break
                if index_uuid(concrete) != uuid:
                    # A different index is answering to this name now. Its
                    # sequence numbers mean nothing against the checkpoint read
                    # above, and its records must not be marked as read under
                    # the key of the index they did not come from. Drop the rest
                    # of this index; the next pass keys it by its own UUID and
                    # starts it from the beginning, which is what it is.
                    replaced = True
                    break
                for hit in hits:
                    yield hit["_source"], (key, str(shard), hit["_seq_no"])
                    read += 1
                after = hits[-1]["_seq_no"]
            if replaced:
                break


def family_of(record, source):
    """Which mining recipe a record belongs to.

    `log_source` is the collection family and the process tree holds two
    formats, so the split is redone here the way refamily.py does it offline.

    It is redone on the FIELDS, not on the message text. The first build tested
    the message against the DataDistribution strip pattern, which needs the
    `[date][S] ` prefix -- and the collector's own datadist parser has already
    eaten that prefix into `time` and `severity` by the time the record is
    indexed. The test could therefore never match and every process-tree record
    was filed as `dpl`.

    The fields decide it exactly, because the cascade guarantees them:
      * `log_time` exists only when the `dpl` parser claimed the line.
      * a single upper-case letter as severity is DataDistribution's own
        spelling, and no other parser in the cascade produces one.
      * everything else -- dpl_noclock, ROOT, and a line nothing claimed --
        is `dpl`, which is the mapping refamily.py uses offline.
    """
    if source != "stdout":
        return source
    if record.get("log_time"):
        return "dpl"
    severity = record.get("severity") or ""
    if len(severity) == 1 and severity.isupper():
        return "datadist"
    return "dpl"


# --- writing -----------------------------------------------------------------
# The count is SET from the cluster's size in this node's tree, never added to.
# An increment cannot be retried safely and a pass that dies after the bulk
# write is exactly the case that has to be retried. The pass guard on top of it
# stops an OLD pass from overwriting a newer one after a long stall.
APPLY = (
    "if (ctx._source.last_pass == null) { ctx._source.last_pass = [:]; }"
    " def seen = ctx._source.last_pass.get(params.node);"
    " if (seen != null && seen >= params.pass) { ctx.op = 'noop'; return; }"
    " ctx._source.last_pass[params.node] = params.pass;"
    " if (ctx._source.counts_by_node == null) { ctx._source.counts_by_node = [:]; }"
    " ctx._source.counts_by_node[params.node] = params.n;"
    " long total = 0;"
    " for (v in ctx._source.counts_by_node.values()) { total += v; }"
    " ctx._source.count = total;"
    " ctx._source.last_seen = params.now;"
    " ctx._source.last_seen_node = params.node;"
    " ctx._source.remove('superseded_by');"
    " if (ctx._source.nodes == null) { ctx._source.nodes = []; }"
    " if (!ctx._source.nodes.contains(params.node))"
    " { ctx._source.nodes.add(params.node); }"
    " if (ctx._source.programs == null) { ctx._source.programs = []; }"
    " for (p in params.programs)"
    " { if (!ctx._source.programs.contains(p))"
    " { ctx._source.programs.add(p); } }"
)

# What happens to the document a cluster used to be published under when drain
# generalises its template. This node's whole contribution leaves; whatever
# other nodes put there stays, because they have not generalised yet and their
# counts are still true of the text that document holds.
#
# `superseded_by` therefore belongs to the DOCUMENT and not to the node that
# retired it. Setting it as soon as one node generalises marks a template that
# other nodes are still actively counting as no longer current, and every reader
# that filters out superseded documents -- which is the point of the field --
# stops counting those other nodes. So it is set only when the last contribution
# has gone, and cleared again if one comes back.
RETIRE = (
    "if (ctx._source.last_pass == null) { ctx._source.last_pass = [:]; }"
    " def seen = ctx._source.last_pass.get(params.node);"
    " if (seen != null && seen >= params.pass) { ctx.op = 'noop'; return; }"
    " ctx._source.last_pass[params.node] = params.pass;"
    " if (ctx._source.counts_by_node != null)"
    " { ctx._source.counts_by_node.remove(params.node); }"
    " long total = 0;"
    " if (ctx._source.counts_by_node != null)"
    " { for (v in ctx._source.counts_by_node.values()) { total += v; } }"
    " ctx._source.count = total;"
    " if (ctx._source.nodes != null) { ctx._source.nodes.removeIf("
    "   n -> n == params.node); }"
    " if (ctx._source.counts_by_node == null"
    " || ctx._source.counts_by_node.isEmpty())"
    " { ctx._source.superseded_by = params.superseded_by; }"
    " else { ctx._source.remove('superseded_by'); }"
    " ctx._source.last_seen = params.now;"
)


def fresh_state():
    return {"version": STATE_VERSION, "pass": 0, "position": {},
            "published": {}, "trees": {}, "pending": None, "programs": {},
            "rotation": 0, "catalog": None, "issued": 0}


def issue(state, pass_number):
    """Record a pass number as USED before anything is written with it.

    The floor `next_pass` works from has to survive a crash between issuing a
    number and finishing what it was for. `pass` alone does not: it is only set
    when a batch commits, and the clear runs before any batch does.
    """
    state["issued"] = max(int(state.get("issued") or 0), int(pass_number))
    save_state(state)


def miners_from(trees):
    """One miner per family, loaded from serialised trees."""
    handlers, miners = {}, {}
    for family, raw in (trees or {}).items():
        handlers[family] = _Buffered(base64.b64decode(raw))
        miners[family] = miner_for(family, handlers[family])
    return handlers, miners


def dump_trees(handlers, miners):
    """Serialise every tree once, here, at the boundary the state file records."""
    out = {}
    for family, handler in handlers.items():
        miner = miners[family]
        miner.persistence_handler = handler
        try:
            miner.save_state("catalog batch")
        finally:
            miner.persistence_handler = None
        if handler.state is not None:
            out[family] = base64.b64encode(handler.state).decode("ascii")
    return out


def build_payload(batch, published_before):
    """The bulk body for one batch, and what it publishes each cluster as.

    Built from the batch's OWN trees, so a recovery reproduces the identical
    body without re-reading a single record. That is the whole point: a retry
    that re-reads is a different batch, and the pass guard then rejects the
    larger count and the difference is lost.
    """
    _, miners = miners_from(batch["trees"])
    payload, published_after, migrated = [], {}, 0
    now = int(time.time() * 1000)
    for key in batch["touched"]:
        family, cluster_id = key.split("\t", 1)
        miner = miners.get(family)
        cluster = miner.drain.id_to_cluster.get(int(cluster_id)) if miner else None
        if cluster is None:
            continue
        template = cluster.get_template()
        new_id = template_id(family, template)
        was = published_before.get(key)
        names = batch["programs"].get(key, [])
        if was and was != new_id:
            # The template generalised. Take this node's count off the document
            # it used to live on, so the two do not both claim it.
            migrated += 1
            payload.append(json.dumps(
                {"update": {"_index": CATALOG_INDEX, "_id": was}}))
            payload.append(json.dumps({
                "script": {"source": RETIRE, "lang": "painless",
                           "params": {"node": NODE_ID, "pass": batch["pass"],
                                      "now": now, "superseded_by": new_id}},
                "upsert": {"kind": "template", "family": family,
                           "superseded_by": new_id, "count": 0,
                           "counts_by_node": {}, "nodes": [],
                           "last_pass": {NODE_ID: batch["pass"]}},
            }))
        payload.append(json.dumps(
            {"update": {"_index": CATALOG_INDEX, "_id": new_id}}))
        payload.append(json.dumps({
            "script": {
                "source": APPLY, "lang": "painless",
                "params": {"n": cluster.size, "now": now, "node": NODE_ID,
                           "pass": batch["pass"], "programs": names},
            },
            "upsert": {
                "kind": "template",
                "family": family,
                "template": template,
                "programs": names,
                "count": cluster.size,
                "counts_by_node": {NODE_ID: cluster.size},
                "last_pass": {NODE_ID: batch["pass"]},
                "nodes": [NODE_ID],
                "first_seen": now,
                "last_seen": now,
                "last_seen_node": NODE_ID,
            },
        }))
        published_after[key] = new_id
    return payload, published_after, migrated


def publish(payload):
    if not payload:
        return True
    body = ("\n".join(payload) + "\n").encode()
    req = urllib.request.Request(
        OS_URL + "/_bulk?refresh=false", data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.load(response)
    if result.get("errors"):
        failed = [i for i in result.get("items", [])
                  if list(i.values())[0].get("error")]
        print("bulk reported %d failures; first: %s"
              % (len(failed), json.dumps(failed[:1])), file=sys.stderr)
        return False
    return True


UNKNOWN_CATALOG = (
    "the catalog this pass would write to could not be established. The batch "
    "stays pending and nothing is published: an identity this node cannot read "
    "must not be recorded as `no change`."
)

REPLACED_MIDPASS = (
    "the catalog was replaced while this pass was reading. Nothing is "
    "published, because this batch is only what the pass READ and the "
    "replacement needs everything the trees hold. The batch stays pending and "
    "the next pass rebuilds."
)


def establish(planned):
    """Fix WHICH catalog this pass writes to, BEFORE it writes anything.

    A lookup that runs after the write cannot answer this question, and it fails
    in the direction that hides the problem. Replace the catalog between the
    bulk write and the lookup and the lookup SUCCEEDS -- it names the empty
    replacement, the node records that as the index it wrote to, and the next
    pass compares that identity against itself, finds no change, and reports
    itself idle over documents that no longer exist.

    Read before the write, the same race can only be wrong the other way: the
    identity recorded is the one this pass INTENDED, so a write that lands
    somewhere else leaves the two disagreeing on the next pass, which is exactly
    what makes it a rebuild instead of a silence.

    The index is therefore created explicitly when it is absent. Letting the
    bulk write create it is what made the identity unknowable until afterwards.
    Creating an index with no body still applies the composable template, so the
    mappings are the shipped ones either way, and another node winning the race
    is not an error -- its index is this node's index.

    `planned` is the identity this pass was built against. A different answer
    here means the catalog changed while the pass was reading, and this batch
    holds only what the pass READ; the replacement needs everything the trees
    hold, which is the next pass's job. None is returned so the caller stops.
    """
    try:
        identity = catalog_identity()
        if not identity:
            try:
                request("/%s" % CATALOG_INDEX, {}, method="PUT")
            except urllib.error.HTTPError as exc:
                # Another node created it between the lookup and here.
                if exc.code not in (400, 409):
                    raise
            identity = catalog_identity()
    except (urllib.error.URLError, OSError):
        # A destination that cannot be read is not a destination. Nothing is
        # published, and the batch the caller has already written down is
        # replayed by the next pass.
        return None
    if not identity:
        return None
    if planned is not None and identity != planned:
        return None
    return identity


def commit(state, batch, published_after):
    """Make the batch durable AFTER it is published, in one replace.

    The catalog identity is NOT discovered here. It is fixed by `establish`
    before the payload is sent, because a lookup that runs after the write
    cannot say which index received it.
    """
    state["pass"] = batch["pass"]
    state["position"] = batch["position"]
    state["trees"] = batch["trees"]
    state.setdefault("published", {}).update(published_after)
    # The accumulated program names travel with the tree, because they answer a
    # question about the cluster and not about the batch: which programs have
    # ever written this template. A recovery replays the batch's own copy, so
    # the two agree whichever path published it.
    state.setdefault("programs", {}).update(batch.get("programs") or {})
    state["pending"] = None
    save_state(state)
    return True


# Used when this node's local state is gone but its documents are not: a
# reprovisioned worker, or a wiped state directory. It removes this node's
# contribution so the rebuild that follows cannot be added on top of counts the
# node can no longer account for.
CLEAR = (
    "if (ctx._source.last_pass == null) { ctx._source.last_pass = [:]; }"
    " def seen = ctx._source.last_pass.get(params.node);"
    " if (seen != null && seen >= params.pass) { ctx.op = 'noop'; return; }"
    " ctx._source.last_pass[params.node] = params.pass;"
    " if (ctx._source.counts_by_node != null)"
    " { ctx._source.counts_by_node.remove(params.node); }"
    " long total = 0;"
    " if (ctx._source.counts_by_node != null)"
    " { for (v in ctx._source.counts_by_node.values()) { total += v; } }"
    " ctx._source.count = total;"
    " if (ctx._source.nodes != null) { ctx._source.nodes.removeIf("
    "   n -> n == params.node); }"
)


def clear_this_node(floor, remember=None):
    """Forget everything this node has published, before rebuilding it.

    Only called when the local state is empty and the catalog is not. Without
    it a rebuilt node adds a second set of counts on top of the set it can no
    longer identify, and every template it had published under a text that has
    since generalised keeps a stale count for ever.

    Three things here are load-bearing and each was once absent.

    The refresh reads the catalog with a SEARCH, and the writes it has to find
    were made with `refresh=false` -- durable and acknowledged, but not
    searchable until the index refreshes, which the shipped template sets to 30
    seconds. A node that lost its state inside that window found nothing to
    clear, left its old contribution standing, and added the rebuild on top.

    The completeness check is stricter here than in the source scan, and fatally
    so. A partial answer during a scan costs a delay; a partial answer HERE
    means documents this node contributes to were not listed, so they are not
    cleared, and the rebuild lands on top of counts that were never removed. A
    pass that cannot see the whole catalog must not start one.

    The pass number is chosen from what is found, not from the clock. The guard
    inside CLEAR rejects a pass at or below the one already recorded for this
    node, so a node whose clock went backwards would issue clears that every
    document ignores -- and then rebuild on top of the counts it believed it had
    just removed. Reading `last_pass` while listing costs nothing and makes the
    number provably higher than anything this node has written.
    """
    if not refresh_is_complete(
            request("/%s/_refresh" % CATALOG_INDEX, method="POST")):
        # Same reasoning as the listing below, one step earlier. The listing is
        # a SEARCH, so it sees only what a refresh has made searchable; a
        # refresh that failed on a shard leaves this node's own contribution
        # there invisible, the listing finds less than there is, and the rebuild
        # lands on top of counts that were never cleared. Checking the listing
        # and not the refresh that feeds it checks the second half of one act.
        raise SystemExit(
            "the catalog did not refresh on every shard; this node's previous "
            "counts cannot be listed safely, so nothing is rebuilt")
    found = []
    highest = int(floor)
    after = None
    while True:
        body = {"size": 500, "sort": [{"_id": "asc"}],
                "query": {"bool": {"filter": [
                    {"term": {"kind": "template"}},
                    {"term": {"nodes": NODE_ID}}]}},
                "_source": ["last_pass"]}
        if after:
            body["search_after"] = after
        try:
            page = request("/%s/_search" % CATALOG_INDEX, body, method="POST")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 0, next_pass(floor)
            raise
        if not search_is_complete(page):
            raise SystemExit(
                "the catalog answered for only some of its shards; this node's "
                "previous counts cannot be cleared safely, so nothing is rebuilt")
        hits = page.get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            found.append(hit["_id"])
            seen = ((hit.get("_source") or {}).get("last_pass")
                    or {}).get(NODE_ID)
            if isinstance(seen, int) and seen > highest:
                highest = seen
        after = hits[-1]["sort"]

    pass_number = next_pass(highest)
    if remember is not None:
        # Written down BEFORE the clear runs, for the same reason the batch is.
        # CLEAR removes this node from `nodes`, so once it has run the listing
        # above can no longer find those documents -- and a crash between the
        # clear and the commit leaves a node that cannot rediscover the pass it
        # just used. It then rebuilds with a LOWER number, every update takes the
        # `noop` branch behind the guard the clear itself wrote, and the counts
        # stay at zero while the run reports success. That is the ordinary
        # clock-rollback failure again, reached by a different road.
        remember(pass_number)
    payload = []
    for doc_id in found:
        payload.append(json.dumps(
            {"update": {"_index": CATALOG_INDEX, "_id": doc_id}}))
        payload.append(json.dumps({
            "script": {"source": CLEAR, "lang": "painless",
                       "params": {"node": NODE_ID, "pass": pass_number}}}))
    if payload and not publish(payload):
        raise SystemExit("could not clear this node's previous catalog counts")
    return len(found), pass_number


def next_pass(floor=0):
    """A pass number that only ever goes up, whatever the clock does.

    Two things had to be true at once and the clock alone gives only one of
    them.

    It has to survive the loss of the local state, so a plain counter is out: it
    restarts at one on a reprovisioned node, every guard already stored in the
    catalog is higher, and the node is fenced out of its own documents for good.
    Milliseconds since the epoch survive that.

    It also has to stay above every pass this node has already written, and the
    clock does NOT guarantee that. A clock correction backwards -- NTP stepping,
    a virtual machine restored from a snapshot -- produces a pass number below
    the stored guard. Every update then takes the `noop` branch, the records are
    consumed and committed, the counts never move, and the run reports success.

    So the clock is a FLOOR and never the whole answer: the caller passes what
    this node is known to have written, from local state or, after a state loss,
    from the catalog itself.
    """
    return max(int(time.time() * 1000), int(floor) + 1)


def run(indices, dry_run=False):
    drainbench.install_merged_create_template()
    state = load_state()
    catalog = catalog_identity()
    # A DIFFERENT catalog, not merely a present one. Local state describing
    # documents that no longer exist keeps this node silent about every template
    # it had already published: nothing is republished because the state says it
    # already was, and nothing new is read because the source position has not
    # moved, so the node reports itself idle against an empty catalog for as long
    # as it runs. Deleting and recreating the index under the same name is
    # exactly what someone wiping the catalog does.
    #
    # What follows is a REBUILD and not a reset, and the difference is history
    # that cannot be got back any other way. Throwing the local state away sends
    # the node back to the source indices, and those are the short-lived half of
    # this system: informational records age out of the node-local index long
    # before the templates mined from them stop being true. The mining trees hold
    # every count this node has ever published, so a catalog replacement
    # republishes from THEM -- the tree is the record of what was seen, the
    # catalog is only where it was sent.
    was = state.get("catalog")
    rebuild = bool(was) and catalog != was
    if rebuild:
        pending_batch = state.get("pending")
        if pending_batch:
            # Its trees are newer than the committed ones and its records are
            # already consumed. Promote them, then let the rebuild below
            # republish everything they hold; replaying the batch on its own
            # would leave the clusters it did not touch unpublished.
            state["trees"] = pending_batch["trees"]
            state["position"] = pending_batch["position"]
            state.setdefault("programs", {}).update(
                pending_batch.get("programs") or {})
            state["pending"] = None
        # The documents are gone, so nothing is published and nothing can be
        # retired. Everything else -- trees, position, programs -- survives.
        state["published"] = {}
    state["catalog"] = catalog

    # A batch that was written down but never confirmed is finished FIRST, and
    # nothing new is read until it is. This is the window atomic state could not
    # close on its own: a retry that reads further is a different batch, and
    # the pass guard then rejects the larger count and the extra records are
    # consumed without ever being counted.
    pending = state.get("pending")
    if pending:
        payload, published_after, migrated = build_payload(
            pending, state.get("published", {}))
        if dry_run:
            print(json.dumps({"recovered": True, "pass": pending["pass"],
                              "templates": len(pending["touched"])}, indent=2))
            return 0
        destination = establish(catalog)
        if destination is None:
            print(UNKNOWN_CATALOG if catalog is None else REPLACED_MIDPASS,
                  file=sys.stderr)
            return 1
        state["catalog"] = destination
        if not publish(payload):
            return 1
        commit(state, pending, published_after)
        print(json.dumps({"node": NODE_ID, "recovered": True,
                          "pass": pending["pass"], "lines": pending["lines"],
                          "templates": len(pending["touched"]),
                          "migrated": migrated}))
        return 0

    cleared = 0
    floor = max(int(state.get("pass") or 0), int(state.get("issued") or 0))
    if not state.get("pass") and not dry_run and catalog is not None \
            and not rebuild:
        # Local state is empty and the catalog is not: this node has published
        # before and can no longer say what. Its contribution goes before the
        # rebuild, in its own pass, so the two cannot land on one document
        # together. That pass is chosen from what the catalog already records
        # for this node, so it is above every pass this node has written even if
        # the clock is not.
        cleared, cleared_pass = clear_this_node(
            floor, remember=lambda n: issue(state, n))
        floor = max(floor, cleared_pass)

    pass_number = next_pass(floor)
    rotation = int(state.get("rotation", 0))
    handlers, miners = miners_from(state.get("trees", {}))
    programs = {k: set(v) for k, v in (state.get("programs") or {}).items()}
    touched = set()
    lines = 0
    unlearned = 0

    def miner(family):
        if family not in miners:
            handlers[family] = _Buffered(None)
            miners[family] = miner_for(family, handlers[family])
        return miners[family]

    def learned():
        return sum(len(m.drain.id_to_cluster) for m in miners.values())

    # A snapshot, so the marks the scan reads cannot move under it while this
    # loop advances them.
    position = {k: dict(v) for k, v in (state.get("position") or {}).items()}
    marks = {k: dict(v) for k, v in position.items()}
    for index, node_scoped in indices:
        for record, where in scan(index, marks, node_scoped, rotation):
            concrete, shard, seq_no = where
            position.setdefault(concrete, {})[shard] = seq_no
            message = record.get("message") or record.get("log") or ""
            if not message:
                continue
            source = record.get("log_source") or "unknown"
            family = family_of(record, source)
            if family not in drainbench.RECIPE_SIM:
                continue
            prepared = drainbench.recipe_prepare(family, message)
            tree = miner(family)
            if learned() >= MAX_TEMPLATES:
                # At the ceiling this node stops LEARNING and keeps COUNTING.
                # drain3's own limit is an LRU that evicts, and eviction is not
                # available here: the count published for a template is the
                # cluster's absolute size in this tree, so an evicted cluster
                # that is later rebuilt would report a size of one and SET the
                # catalog count back to one. Matching without adding keeps every
                # count that has already been published true, at the price of
                # not recognising shapes that are new after the ceiling. Those
                # are counted in `unlearned` so that the ceiling is visible in
                # the journal rather than silent.
                cluster = tree.drain.match(prepared)
                if cluster is None:
                    unlearned += 1
                    continue
                cluster.size += 1
                cluster_id = cluster.cluster_id
            else:
                cluster_id = tree.add_log_message(prepared)["cluster_id"]
            key = "%s\t%s" % (family, cluster_id)
            touched.add(key)
            program = record.get("program")
            if program:
                programs.setdefault(key, set()).add(program)
            lines += 1

    if rebuild:
        # Not only what this pass read. Everything the trees remember, because
        # the documents that held it no longer exist.
        touched |= {"%s\t%s" % (family, cluster_id)
                    for family, tree in miners.items()
                    for cluster_id in tree.drain.id_to_cluster}

    batch = {
        "version": STATE_VERSION,
        "pass": pass_number,
        "position": position,
        "trees": dump_trees(handlers, miners),
        "touched": sorted(touched),
        # The ACCUMULATED programs for each cluster, not this batch's. When a
        # template generalises, its count moves to a new document and the
        # programs have to move with it; sending only what this batch saw named
        # the new document after whichever program happened to appear in the ten
        # minutes the move fell in, and lost the rest.
        "programs": {k: sorted(programs[k])[:32] for k in touched
                     if programs.get(k)},
        "lines": lines,
    }

    if dry_run:
        payload, _, migrated = build_payload(batch, state.get("published", {}))
        print(json.dumps({"lines": lines, "templates": len(touched),
                          "migrated": migrated, "unlearned": unlearned,
                          "families": sorted(miners)}, indent=2))
        return 0

    if position == state.get("position", {}) and not touched:
        # Nothing new. Say so rather than exiting silently: a oneshot that
        # prints nothing is indistinguishable from one that died, both in the
        # journal and to anything parsing this output.
        state["rotation"] = rotation + 1
        save_state(state)
        print(json.dumps({"node": NODE_ID, "pass": pass_number, "lines": 0,
                          "templates": 0, "migrated": 0, "cleared": cleared,
                          "unlearned": unlearned, "families": sorted(miners),
                          "position": position, "idle": True}))
        return 0

    # Written down before it is published. A crash anywhere after this line
    # leaves a batch the next run finishes exactly as it stood, whatever has
    # arrived in the index since.
    state["pending"] = batch
    state["rotation"] = rotation + 1
    state["issued"] = max(int(state.get("issued") or 0), pass_number)
    save_state(state)

    payload, published_after, migrated = build_payload(
        batch, state.get("published", {}))
    destination = establish(catalog)
    if destination is None:
        print(UNKNOWN_CATALOG if catalog is None else REPLACED_MIDPASS,
              file=sys.stderr)
        return 1
    state["catalog"] = destination
    if not publish(payload):
        # The batch stays pending. The next run republishes THIS batch, not a
        # larger one, and only then reads further.
        return 1
    commit(state, batch, published_after)

    print(json.dumps({"node": NODE_ID, "pass": pass_number, "lines": lines,
                      "templates": len(touched), "migrated": migrated,
                      "cleared": cleared, "unlearned": unlearned,
                      "families": sorted(miners), "position": position}))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", action="append", default=[],
                        help="repeat; an index only this node writes to")
    parser.add_argument("--shared-index", action="append", default=[],
                        help="repeat; an index several nodes write to, read "
                             "scoped to this node's own records")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    indices = ([(name, False) for name in args.index]
               + [(name, True) for name in args.shared_index])
    if not indices:
        # Every route the collector has, not only the node-local one. Reading
        # the local index alone left the catalog with no InfoLogger family at
        # all, no daemon log, and none of the warnings and errors -- which are
        # the records the routing deliberately sends straight to the durable
        # tier and never writes locally.
        indices = [("application-logs-local-%s" % NODE_ID, False),
                   (CENTRAL_INDEX, True),
                   (INFOLOGGER_INDEX, True)]
    return run(indices, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
