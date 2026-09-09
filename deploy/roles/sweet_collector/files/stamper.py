#!/usr/bin/env python3
import base64
import collections
import json
import os
import resource
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get(
    "ALICE_SHARED_PATH", "/opt/sweet/shared"))
sys.path.insert(0, os.environ.get(
    "ALICE_TEMPLATING_PATH", "/opt/sweet/templating"))

from drain3.persistence_handler import PersistenceHandler     # noqa: E402

import drainbench                                             # noqa: E402
import forward                                                # noqa: E402
import template_contract as contract                          # noqa: E402

NODE_ID = os.environ.get("ALICE_NODE_ID", socket.gethostname())
OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
CATALOG_INDEX = os.environ.get("CATALOG_INDEX", contract.CATALOG_INDEX)
LOCAL_INDEX = os.environ.get("STAMPER_LOCAL_INDEX",
                             "application-logs-local-%s" % NODE_ID)
LISTEN_SOCKET = os.environ.get("STAMPER_LISTEN_SOCKET",
                               "/run/alice/stamper.sock")
RETURN_SOCKET = os.environ.get("STAMPER_RETURN_SOCKET",
                               "/run/alice/stamped.sock")
SOCKET_MODE = int(os.environ.get("STAMPER_SOCKET_MODE", "0660"), 8)
STATE_DIR = os.environ.get("STAMPER_STATE_DIR", "/var/lib/alice-stamper")
STATUS_FILE = os.environ.get("STAMPER_STATUS_FILE",
                             "/run/alice/stamper-status.json")
MAX_TEMPLATES = int(os.environ.get("STAMPER_MAX_TEMPLATES", "20000"))
MAX_MESSAGE_LENGTH = int(os.environ.get("STAMPER_MAX_MESSAGE_LENGTH", "4096"))
PUBLISH_SECONDS = int(os.environ.get(
    "STAMPER_PUBLISH_SECONDS", str(contract.PUBLISH_INTERVAL_MS // 1000)))
CHECKPOINT_SECONDS = int(os.environ.get("STAMPER_CHECKPOINT_SECONDS", "600"))
ACK_TIMEOUT = float(os.environ.get("STAMPER_ACK_TIMEOUT", "30"))
REQUEST_TIMEOUT = int(os.environ.get("STAMPER_REQUEST_TIMEOUT", "60"))
BULK_TIMEOUT = int(os.environ.get("STAMPER_BULK_TIMEOUT", "120"))
BULK_DOCUMENTS = int(os.environ.get("STAMPER_BULK_DOCUMENTS", "500"))
TOLERANCE_MS = int(os.environ.get("STAMPER_CLOCK_TOLERANCE_MS",
                                  str(contract.CLOCK_TOLERANCE_MS)))
LEDGER_MS = int(os.environ.get("STAMPER_LEDGER_HOURS",
                               str(contract.LEDGER_HOURS))) * contract.HOUR_MS
CHUNK_HOLD_MS = int(os.environ.get("STAMPER_CHUNK_HOLD_MS",
                                   str(contract.CHUNK_IDENTIFIER_HOLD_MS)))
TICK_SECONDS = float(os.environ.get("STAMPER_TICK_SECONDS", "1"))
LOCAL_CHECK = os.environ.get("STAMPER_LOCAL_CHECK", "true").lower() == "true"

STATE_FILE = "stamper-state.json"
JOURNAL_FILE = "stamper-journal.log"
STATE_VERSION = 1

FINE = contract.FINE_BUCKET_MS
COARSE = contract.COARSE_BUCKET_MS

DEFINITION_APPLY = (
    "ctx._source.kind = params.kind;"
    " ctx._source.schema_version = params.schema_version;"
    " ctx._source.version_id = params.version_id;"
    " ctx._source.canonical_id = params.canonical_id;"
    " ctx._source.family = params.family;"
    " ctx._source.template = params.template;"
    " ctx._source.normalized = params.normalized;"
    " ctx._source.token_count = params.token_count;"
    " if (params.severity_norm != null)"
    " { ctx._source.severity_norm = params.severity_norm; }"
    " if (ctx._source.first_observed == null"
    " || params.first_observed < ctx._source.first_observed)"
    " { ctx._source.first_observed = params.first_observed; }"
    " if (ctx._source.last_observed == null"
    " || params.last_observed > ctx._source.last_observed)"
    " { ctx._source.last_observed = params.last_observed; }"
    " if (ctx._source.first_catalogued == null)"
    " { ctx._source.first_catalogued = params.first_catalogued; }"
    " for (name in params.scope.keySet()) {"
    " def held = ctx._source.get(name);"
    " if (held == null) { held = new ArrayList();"
    " ctx._source.put(name, held); }"
    " def limit = params.limits.get(name);"
    " for (value in params.scope.get(name)) {"
    " if (!held.contains(value)) {"
    " if (held.size() >= limit) {"
    " def flag = params.flags.get(name);"
    " if (flag != null) { ctx._source.put(flag, true); } }"
    " else { held.add(value); } } } }"
)

SCOPE_LIMITS = {"programs": contract.MAX_ENTRY_PROGRAMS,
                "origin_hosts": contract.MAX_ENTRY_HOSTS,
                "log_sources": contract.MAX_ENTRY_SOURCES,
                "nodes": contract.MAX_ENTRY_NODES,
                "widened_into": contract.MAX_ENTRY_LINKS,
                "widened_from": contract.MAX_ENTRY_LINKS}
SCOPE_FLAGS = {"programs": "programs_truncated",
               "origin_hosts": "origin_hosts_truncated",
               "nodes": "nodes_truncated"}


class StamperError(Exception):
    pass


class PublicationFailed(StamperError):
    pass


def log(message):
    print("[stamper] %s" % message, file=sys.stderr, flush=True)


def peak_rss_bytes():
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if sys.platform == "darwin" else usage * 1024


def now_ms():
    return int(time.time() * 1000)


def write_atomically(path, payload):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", dir=directory, prefix=".tmp-",
                                         delete=False)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Transport(object):

    def __init__(self, base_url=OS_URL, timeout=REQUEST_TIMEOUT,
                 bulk_timeout=BULK_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.bulk_timeout = bulk_timeout

    def request(self, path, body=None, method="GET"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return json.load(response)

    def bulk(self, lines):
        body = ("\n".join(lines) + "\n").encode()
        req = urllib.request.Request(
            self.base_url + "/_bulk?refresh=false", data=body, method="POST",
            headers={"Content-Type": "application/x-ndjson"})
        with urllib.request.urlopen(req, timeout=self.bulk_timeout) as response:
            return json.load(response)


def verify_bulk(result, expected_ids, ok_statuses=(200, 201)):
    if not isinstance(result, dict) or not isinstance(result.get("items"),
                                                      list):
        raise PublicationFailed("the bulk answer carries no item list")
    seen = set()
    for item in result["items"]:
        if not isinstance(item, dict) or len(item) != 1:
            raise PublicationFailed("a bulk item is not one named operation")
        body = list(item.values())[0]
        if not isinstance(body, dict):
            raise PublicationFailed("a bulk item body is not an object")
        if body.get("error") or body.get("status") not in ok_statuses:
            raise PublicationFailed("bulk write of %s failed: %s"
                                    % (body.get("_id"),
                                       json.dumps(body.get("error"))))
        seen.add(body.get("_id"))
    missing = [value for value in expected_ids if value not in seen]
    if missing:
        raise PublicationFailed("the bulk answer says nothing about %d "
                                "documents; first: %s"
                                % (len(missing), missing[0]))
    return len(seen)


class _Buffered(PersistenceHandler):

    def __init__(self, initial=None):
        self.state = initial

    def save_state(self, state):
        self.state = state

    def load_state(self):
        return self.state


class Trees(object):

    def __init__(self, max_templates=MAX_TEMPLATES):
        drainbench.install_merged_create_template()
        self.max_templates = max_templates
        self.handlers = {}
        self.miners = {}
        self.clusters = {}
        self.identity = {}
        self.recent = collections.OrderedDict()
        self.learned = 0

    def load(self, trees, clusters, recent=None):
        for family, raw in (trees or {}).items():
            handler = _Buffered(base64.b64decode(raw))
            miner = drainbench.recipe_miner(family, persistence=handler)
            miner.persistence_handler = None
            self.handlers[family] = handler
            self.miners[family] = miner
            held = {}
            for cluster_id, cluster in miner.drain.id_to_cluster.items():
                template = cluster.get_template()
                held[cluster_id] = contract.version_id(family, template)
            for cluster_id, identity in (clusters or {}).get(family,
                                                              {}).items():
                held.setdefault(int(cluster_id), identity)
            self.clusters[family] = held
        for family, cluster_id in recent or []:
            miner = self.miners.get(family)
            if miner is not None and cluster_id in miner.drain.id_to_cluster:
                self.recent[(family, cluster_id)] = None
        for family, miner in self.miners.items():
            for cluster_id in miner.drain.id_to_cluster:
                self.recent.setdefault((family, cluster_id), None)
        self.learned = len(self.recent)

    def dump(self):
        out = {}
        for family, handler in self.handlers.items():
            miner = self.miners[family]
            miner.persistence_handler = handler
            try:
                miner.save_state("stamper checkpoint")
            finally:
                miner.persistence_handler = None
            if handler.state is not None:
                out[family] = base64.b64encode(handler.state).decode("ascii")
        return out

    def cluster_map(self):
        return {family: {str(k): v for k, v in held.items()}
                for family, held in self.clusters.items()}

    def recent_list(self):
        return [[family, cluster_id] for family, cluster_id in self.recent]

    def evict(self):
        (family, cluster_id), _ = self.recent.popitem(last=False)
        self.miners[family].drain.id_to_cluster.pop(cluster_id, None)
        self.clusters[family].pop(cluster_id, None)
        self.learned -= 1
        return family, cluster_id

    def prune(self):
        live = set()
        for held in self.clusters.values():
            live.update(held.values())
        for key in [k for k, v in self.identity.items() if v not in live]:
            del self.identity[key]

    def miner(self, family):
        miner = self.miners.get(family)
        if miner is None:
            self.handlers[family] = _Buffered(None)
            miner = drainbench.recipe_miner(family,
                                            persistence=self.handlers[family])
            miner.persistence_handler = None
            self.miners[family] = miner
            self.clusters[family] = {}
        return miner

    def stamp(self, family, tokens):
        miner = self.miner(family)
        cluster, update = miner.drain.add_tokens(list(tokens))
        key = (family, cluster.cluster_id)
        if update == "cluster_created":
            self.learned += 1
            self.recent[key] = None
            while self.learned > self.max_templates:
                self.evict()
        else:
            self.recent.move_to_end(key)
        template = cluster.get_template()
        identity = self.identity.get((family, template))
        if identity is None:
            identity = contract.version_id(family, template)
            self.identity[(family, template)] = identity
        previous = self.clusters[family].get(cluster.cluster_id)
        self.clusters[family][cluster.cluster_id] = identity
        if update == "cluster_created":
            return identity, template, contract.STAMP_NEW, None
        if previous is not None and previous != identity:
            return identity, template, contract.STAMP_MATCHED, previous
        return identity, template, contract.STAMP_MATCHED, None


class Ledger(object):

    def __init__(self):
        self.buckets = {}
        self.dirty = set()
        self.versions = {}
        self.dirty_versions = set()
        self.canonical = {}

    @staticmethod
    def key(family, bucket_start, late):
        return (family, bucket_start, bool(late))

    def add(self, family, bucket_start, late, identity, count=1):
        key = self.key(family, bucket_start, late)
        held = self.buckets.get(key)
        if held is None:
            held = self.buckets[key] = {"counts": {}, "rev": 0}
        held["counts"][identity] = held["counts"].get(identity, 0) + count
        held["rev"] += 1
        self.dirty.add(key)

    def define(self, identity, family, template, observed_ms, program=None,
               origin_host=None, log_source=None, severity=None,
               widened_from=None):
        held = self.versions.get(identity)
        if held is None:
            held = self.versions[identity] = {
                "family": family, "template": template,
                "first": observed_ms, "last": observed_ms,
                "programs": set(), "hosts": set(), "sources": set(),
                "severity": None, "into": set(), "from": set()}
            changed = True
        else:
            changed = False
            if observed_ms < held["first"]:
                held["first"] = observed_ms
                changed = True
            if observed_ms > held["last"]:
                held["last"] = observed_ms
        for name, value in (("programs", program), ("hosts", origin_host),
                            ("sources", log_source)):
            if value and value not in held[name]:
                held[name].add(value)
                changed = True
        if severity and held["severity"] is None:
            held["severity"] = severity
            changed = True
        if widened_from and widened_from not in held["from"]:
            held["from"].add(widened_from)
            changed = True
            origin = self.versions.get(widened_from)
            if origin is not None and identity not in origin["into"]:
                origin["into"].add(identity)
                self.dirty_versions.add(widened_from)
        if changed:
            self.dirty_versions.add(identity)
        return held

    def coarse_counts(self, family, coarse_start, late):
        counts = {}
        for start in range(coarse_start, coarse_start + COARSE, FINE):
            held = self.buckets.get(self.key(family, start, late))
            if held is None:
                continue
            for identity, count in held["counts"].items():
                counts[identity] = counts.get(identity, 0) + count
        return counts

    def expire(self, now):
        horizon = now - LEDGER_MS
        dropped = 0
        for key in list(self.buckets):
            if key[1] + FINE <= horizon:
                del self.buckets[key]
                self.dirty.discard(key)
                dropped += 1
        return dropped

    def prune_versions(self):
        live = set()
        for held in self.buckets.values():
            live.update(held["counts"])
        for identity in list(self.versions):
            if identity not in live and identity not in self.dirty_versions:
                del self.versions[identity]
                self.canonical.pop(identity, None)

    def to_state(self):
        buckets = []
        for (family, start, late), held in self.buckets.items():
            buckets.append([family, start, late, held["counts"]])
        versions = {}
        for identity, held in self.versions.items():
            versions[identity] = [held["family"], held["template"],
                                  held["first"], held["last"],
                                  sorted(held["programs"]),
                                  sorted(held["hosts"]),
                                  sorted(held["sources"]), held["severity"],
                                  sorted(held["into"]), sorted(held["from"])]
        return {"buckets": buckets, "versions": versions}

    def load_state(self, state):
        for family, start, late, counts in state.get("buckets") or []:
            key = self.key(family, int(start), late)
            self.buckets[key] = {"counts": {k: int(v) for k, v in
                                            counts.items()}, "rev": 0}
            self.dirty.add(key)
        for identity, row in (state.get("versions") or {}).items():
            self.versions[identity] = {
                "family": row[0], "template": row[1], "first": row[2],
                "last": row[3], "programs": set(row[4]), "hosts": set(row[5]),
                "sources": set(row[6]), "severity": row[7],
                "into": set(row[8]), "from": set(row[9])}
            self.dirty_versions.add(identity)


class Journal(object):

    def __init__(self, path):
        self.path = path
        self.seq = 0
        self.bytes = 0
        self.lines = 0
        self._handle = None

    def open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._handle = open(self.path, "a")
        self.bytes = os.fstat(self._handle.fileno()).st_size
        return self

    def replay(self, after_seq):
        try:
            handle = open(self.path)
        except FileNotFoundError:
            return
        with handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    log("a truncated journal line was skipped")
                    continue
                seq = int(line.get("seq") or 0)
                if seq > self.seq:
                    self.seq = seq
                if seq <= after_seq:
                    continue
                yield line

    def append(self, line):
        self.seq += 1
        line["seq"] = self.seq
        text = json.dumps(line, separators=(",", ":")) + "\n"
        self._handle.write(text)
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.bytes += len(text)
        self.lines += 1
        return self.seq

    def truncate(self):
        if self._handle is not None:
            self._handle.close()
        self._handle = open(self.path, "w")
        self.bytes = 0
        self.lines = 0

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class Stamper(object):

    def __init__(self, node_id=NODE_ID, state_dir=STATE_DIR,
                 return_socket=RETURN_SOCKET, transport=None, clock=now_ms,
                 max_templates=MAX_TEMPLATES, local_index=LOCAL_INDEX,
                 catalog_index=CATALOG_INDEX, status_file=STATUS_FILE,
                 local_check=LOCAL_CHECK,
                 max_message_length=MAX_MESSAGE_LENGTH):
        self.node = node_id
        self.state_dir = state_dir
        self.clock = clock
        self.transport = transport
        self.local_index = local_index
        self.catalog_index = catalog_index
        self.status_file = status_file
        self.local_check = local_check
        self.max_message_length = max_message_length
        self.trees = Trees(max_templates)
        self.ledger = Ledger()
        self.journal = Journal(os.path.join(state_dir, JOURNAL_FILE))
        self.client = forward.ForwardClient(return_socket, ACK_TIMEOUT)
        self.chunks = {}
        self.counters = {name: 0 for name in contract.STAMPER_COUNTERS}
        self.published_through = 0
        self.checked_hour = None
        self.last_publish = None
        self.last_checkpoint = None
        self.lock = threading.RLock()
        self.server = None

    def state_path(self):
        return os.path.join(self.state_dir, STATE_FILE)

    def load(self):
        try:
            with open(self.state_path()) as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = None
        after = 0
        if state and state.get("version") == STATE_VERSION:
            self.trees.load(state.get("trees"), state.get("clusters"),
                            state.get("recent"))
            self.ledger.load_state(state.get("ledger") or {})
            self.chunks = {k: int(v) for k, v in
                           (state.get("chunks") or {}).items()}
            self.published_through = int(state.get("published_through") or 0)
            for name, value in (state.get("counters") or {}).items():
                if name in self.counters:
                    self.counters[name] = int(value)
            after = int(state.get("journal_seq") or 0)
        elif state is not None:
            log("state version %r is not %d; starting from an empty ledger"
                % (state.get("version"), STATE_VERSION))
        replayed = 0
        for line in self.journal.replay(after):
            self._apply_journal(line)
            replayed += 1
        self.journal.open()
        if replayed:
            log("replayed %d journal lines after checkpoint %d"
                % (replayed, after))
        self.counters["clusters"] = self.trees.learned
        return replayed

    def _apply_journal(self, line):
        chunk = line.get("chunk")
        if chunk:
            self.chunks[chunk] = int(line.get("at") or 0)
        for family, start, late, counts in line.get("deltas") or []:
            for identity, count in counts.items():
                self.ledger.add(family, int(start), late, identity, int(count))
        for row in line.get("defs") or []:
            identity, family, template, observed = row[:4]
            origin = row[4] if len(row) > 4 else None
            self.ledger.define(identity, family, template, int(observed),
                               widened_from=origin)

    def checkpoint(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            self.ledger.prune_versions()
            self.trees.prune()
            state = {
                "version": STATE_VERSION,
                "node": self.node,
                "saved_at": now,
                "journal_seq": self.journal.seq,
                "published_through": self.published_through,
                "trees": self.trees.dump(),
                "clusters": self.trees.cluster_map(),
                "recent": self.trees.recent_list(),
                "ledger": self.ledger.to_state(),
                "chunks": self.chunks,
                "counters": self.counters,
            }
            write_atomically(self.state_path(), json.dumps(state))
            self.journal.truncate()
            self.last_checkpoint = now

    def stamp(self, record):
        source = record.get("log_source") or "unknown"
        family = contract.family_of(record, source)
        message = record.get("message") or record.get("log") or ""
        if family not in drainbench.RECIPE_SIM or not message:
            record[contract.TEMPLATE_STATUS_FIELD] = contract.STAMP_NO_TEMPLATE
            return None
        if len(message) > self.max_message_length:
            record[contract.TEMPLATE_STATUS_FIELD] = contract.STAMP_NO_TEMPLATE
            self.counters["oversize_records"] += 1
            return None
        tokens = drainbench.recipe_tokens(family, message)
        if not tokens:
            record[contract.TEMPLATE_STATUS_FIELD] = contract.STAMP_NO_TEMPLATE
            return None
        identity, template, status, previous = self.trees.stamp(family, tokens)
        record[contract.TEMPLATE_STATUS_FIELD] = status
        canonical = self.ledger.canonical.get(identity)
        if canonical is None:
            canonical = contract.canonical_id(template)
            self.ledger.canonical[identity] = canonical
        record[contract.TEMPLATE_VERSION_FIELD] = identity
        record[contract.TEMPLATE_ID_FIELD] = canonical
        return family, identity, template, status, previous

    def handle(self, tag, entries, options):
        now = self.clock()
        chunk = options.get(forward.CHUNK_OPTION)
        with self.lock:
            duplicate = chunk is not None and chunk in self.chunks
            deltas = {}
            defs = []
            for _, record in entries:
                stamped = self.stamp(record)
                if stamped is None:
                    self._count_unstamped(record)
                    continue
                family, identity, template, status, previous = stamped
                if duplicate:
                    continue
                self._count(record, family, identity, template, status,
                            previous, now, deltas, defs)
            self._return(tag, entries, chunk)
            if duplicate:
                self.counters["duplicate_chunks"] += 1
                self.counters["chunks"] += 1
                return
            line = {"at": now, "chunk": chunk,
                    "deltas": [[family, start, late, counts]
                               for (family, start, late), counts
                               in deltas.items()],
                    "defs": defs}
            self.journal.append(line)
            for (family, start, late), counts in deltas.items():
                for identity, count in counts.items():
                    self.ledger.add(family, start, late, identity, count)
            if chunk is not None:
                self.chunks[chunk] = now
            self.counters["chunks"] += 1
            self.counters["records"] += len(entries)
            self.counters["journal_bytes"] = self.journal.bytes
            self.counters["journal_lines"] = self.journal.lines
            self.counters["clusters"] = self.trees.learned

    def _count_unstamped(self, record):
        self.counters["no_template_records"] += 1

    def _count(self, record, family, identity, template, status, previous,
               now, deltas, defs):
        if status == contract.STAMP_NEW:
            self.counters["new_records"] += 1
        else:
            self.counters["matched_records"] += 1
        observed = contract.observation_time(record, now, TOLERANCE_MS)
        if observed.status == contract.TIME_MISSING:
            self.counters["missing_time_records"] += 1
            return
        if observed.status == contract.TIME_INVALID:
            self.counters["invalid_time_records"] += 1
            return
        start = contract.bucket_start_ms(observed.epoch_ms, FINE)
        late = start + FINE <= now - LEDGER_MS
        if late:
            start = contract.bucket_start_ms(now, FINE)
            self.counters["late_records"] += 1
        key = (family, start, late)
        counts = deltas.setdefault(key, {})
        counts[identity] = counts.get(identity, 0) + 1
        held = self.ledger.versions.get(identity)
        fresh = (held is None or previous is not None
                 or observed.epoch_ms < held["first"])
        self.ledger.define(
            identity, family, template, observed.epoch_ms,
            program=record.get("program"),
            origin_host=record.get("origin_host") or record.get("host")
            or record.get("hostname"),
            log_source=record.get("log_source"),
            severity=record.get("severity_norm"), widened_from=previous)
        if fresh:
            defs.append([identity, family, template, observed.epoch_ms,
                         previous])

    def _return(self, tag, entries, chunk):
        try:
            self.client.send(tag, entries, chunk_id=chunk)
        except (OSError, forward.ForwardError):
            self.counters["return_failures"] += 1
            try:
                self.client.send(tag, entries, chunk_id=chunk)
            except (OSError, forward.ForwardError) as exc:
                self.counters["return_failures"] += 1
                raise StamperError("the stamped chunk was not accepted by "
                                   "the return socket: %s" % exc)
        self.counters["returned_chunks"] += 1

    def forget_chunks(self, now):
        horizon = now - CHUNK_HOLD_MS
        for chunk in [c for c, at in self.chunks.items() if at < horizon]:
            del self.chunks[chunk]

    def bucket_lines(self, now, keys):
        lines = []
        identifiers = []
        coarse_keys = set()
        for family, start, late in keys:
            held = self.ledger.buckets.get((family, start, late))
            if held is None or not held["counts"]:
                continue
            document = contract.bucket_document(
                self.node, family, FINE, start, held["counts"], now, late)
            lines.append(json.dumps({"index": {
                "_index": contract.bucket_index_name(FINE, start),
                "_id": document["bucket_id"]}}))
            lines.append(json.dumps(document))
            identifiers.append(document["bucket_id"])
            coarse_keys.add((family, contract.coarse_bucket_of(start), late))
        for family, start, late in sorted(coarse_keys):
            counts = self.ledger.coarse_counts(family, start, late)
            if not counts:
                continue
            document = contract.bucket_document(
                self.node, family, COARSE, start, counts, now, late)
            lines.append(json.dumps({"index": {
                "_index": contract.bucket_index_name(COARSE, start),
                "_id": document["bucket_id"]}}))
            lines.append(json.dumps(document))
            identifiers.append(document["bucket_id"])
        return lines, identifiers

    def definition_lines(self, now, identities):
        lines = []
        identifiers = []
        for identity in sorted(identities):
            held = self.ledger.versions.get(identity)
            if held is None:
                continue
            if len(held["template"]) > self.max_message_length:
                continue
            document = contract.definition_document(
                held["family"], held["template"], self.node, held["first"],
                held["last"], now, programs=held["programs"],
                origin_hosts=held["hosts"], log_sources=held["sources"],
                severity_norm=held["severity"], widened_into=held["into"],
                widened_from=held["from"])
            scope = {"programs": document["programs"],
                     "origin_hosts": document["origin_hosts"],
                     "log_sources": document["log_sources"],
                     "nodes": [self.node],
                     "widened_into": document["widened_into"],
                     "widened_from": document["widened_from"]}
            lines.append(json.dumps({"update": {"_index": self.catalog_index,
                                                "_id": identity}}))
            lines.append(json.dumps({
                "script": {"source": DEFINITION_APPLY, "lang": "painless",
                           "params": {
                               "kind": document["kind"],
                               "schema_version": document["schema_version"],
                               "version_id": identity,
                               "canonical_id": document["canonical_id"],
                               "family": document["family"],
                               "template": document["template"],
                               "normalized": document["normalized"],
                               "token_count": document["token_count"],
                               "severity_norm": document["severity_norm"],
                               "first_observed": document["first_observed"],
                               "last_observed": document["last_observed"],
                               "first_catalogued": now,
                               "scope": scope,
                               "limits": SCOPE_LIMITS,
                               "flags": SCOPE_FLAGS}},
                "upsert": document}))
            identifiers.append(identity)
        return lines, identifiers

    def watermark_line(self, now, through):
        document = contract.watermark_document(
            self.node, through, self._ledger_start(now), now,
            counters=self.snapshot_counters())
        return ([json.dumps({"index": {"_index": self.catalog_index,
                                       "_id": document["watermark_id"]}}),
                 json.dumps(document)], [document["watermark_id"]])

    def _ledger_start(self, now):
        starts = [key[1] for key in self.ledger.buckets if not key[2]]
        floor = contract.bucket_start_ms(now - LEDGER_MS, FINE)
        if not starts:
            return floor
        return min(min(starts), floor)

    def snapshot_counters(self):
        held = dict(self.counters)
        held["peak_rss_bytes"] = peak_rss_bytes()
        held["ledger_buckets"] = len(self.ledger.buckets)
        held["ledger_versions"] = len(self.ledger.versions)
        held["clusters"] = self.trees.learned
        held["socket_backlog"] = (self.server.backlog_size()
                                  if self.server is not None else 0)
        return held

    def _send(self, lines, identifiers):
        for start in range(0, len(lines), BULK_DOCUMENTS * 2):
            batch = lines[start:start + BULK_DOCUMENTS * 2]
            wanted = identifiers[start // 2:start // 2 + BULK_DOCUMENTS]
            verify_bulk(self.transport.bulk(batch), wanted)

    def publish(self, now=None):
        now = self.clock() if now is None else now
        if self.transport is None:
            return {"skipped": "no transport"}
        with self.lock:
            self.ledger.expire(now)
            self.forget_chunks(now)
            keys = sorted(self.ledger.dirty)
            revisions = {key: self.ledger.buckets[key]["rev"]
                         for key in keys if key in self.ledger.buckets}
            versions = sorted(self.ledger.dirty_versions)
            lines, identifiers = self.bucket_lines(now, keys)
            more, names = self.definition_lines(now, versions)
            lines += more
            identifiers += names
            through = contract.bucket_start_ms(now, FINE)
            more, names = self.watermark_line(now, through)
            lines += more
            identifiers += names
        try:
            self._send(lines, identifiers)
        except (PublicationFailed, urllib.error.URLError, OSError,
                contract.ContractError) as exc:
            with self.lock:
                self.counters["publication_failures"] += 1
            log("publication failed: %s" % exc)
            self.write_status(now)
            return {"failed": str(exc)}
        with self.lock:
            for key, revision in revisions.items():
                held = self.ledger.buckets.get(key)
                if held is None or held["rev"] == revision:
                    self.ledger.dirty.discard(key)
            for identity in versions:
                self.ledger.dirty_versions.discard(identity)
            self.published_through = through
            self.counters["publications"] += 1
            self.last_publish = now
        report = {"buckets": len(keys), "definitions": len(versions),
                  "published_through": through}
        if self.local_check:
            report["check"] = self.check_local(now)
        self.write_status(now)
        return report

    def check_local(self, now):
        hour = contract.bucket_start_ms(now, COARSE) - COARSE
        if self.checked_hour == hour:
            return None
        with self.lock:
            families = sorted({key[0] for key in self.ledger.buckets
                               if not key[2] and hour <= key[1] < hour + COARSE})
            stamped = {family: self.ledger.coarse_counts(family, hour, False)
                       for family in families}
        lines = []
        identifiers = []
        for family in families:
            body = {
                "size": 0, "track_total_hits": False,
                "query": {"bool": {"filter": [
                    {"term": {contract.NODE_FIELD: self.node}},
                    {"range": {contract.COLLECTOR_TIME_FIELD: {
                        "gte": hour, "lt": hour + COARSE,
                        "format": "epoch_millis"}}},
                    {"prefix": {contract.TEMPLATE_VERSION_FIELD:
                                family + ":"}}]}},
                "aggregations": {"versions": {"terms": {
                    "field": contract.TEMPLATE_VERSION_FIELD,
                    "size": contract.MAX_BUCKET_VERSIONS}}},
            }
            try:
                page = self.transport.request(
                    "/%s/_search?ignore_unavailable=true" % self.local_index,
                    body, "POST")
            except (urllib.error.URLError, OSError) as exc:
                log("the local check could not read %s: %s"
                    % (self.local_index, exc))
                return {"failed": str(exc)}
            document = compare_stamped(self.node, family, hour, stamped[family],
                                       page, self.local_index, now)
            lines.append(json.dumps({"index": {"_index": self.catalog_index,
                                               "_id": document["check_id"]}}))
            lines.append(json.dumps(document))
            identifiers.append(document["check_id"])
        if lines:
            try:
                self._send(lines, identifiers)
            except (PublicationFailed, urllib.error.URLError, OSError) as exc:
                log("the local check was not published: %s" % exc)
                return {"failed": str(exc)}
        self.checked_hour = hour
        return {"hour": hour, "families": families}

    def write_status(self, now):
        if not self.status_file:
            return
        with self.lock:
            counters = self.snapshot_counters()
        payload = {"stamper_up": 1, "stamper_at": now,
                   "stamper_published_through": self.published_through,
                   "stamper_last_publish": self.last_publish or 0}
        for name, value in counters.items():
            payload["stamper_" + name] = value
        try:
            write_atomically(self.status_file, json.dumps(payload))
        except OSError as exc:
            log("the status file was not written: %s" % exc)

    def serve(self, listen_socket=LISTEN_SOCKET, mode=SOCKET_MODE):
        self.load()
        self.server = forward.ForwardServer(listen_socket, self.handle, mode)
        self.server.start()
        log("listening on %s, returning through %s"
            % (listen_socket, self.client.path))
        return self.server

    def tick(self, now=None):
        now = self.clock() if now is None else now
        if (self.last_publish is None
                or now - self.last_publish >= PUBLISH_SECONDS * 1000):
            self.publish(now)
        if (self.last_checkpoint is None
                or now - self.last_checkpoint >= CHECKPOINT_SECONDS * 1000):
            self.checkpoint(now)

    def run(self, stop=None):
        stop = stop or threading.Event()
        self.serve()
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception as exc:                      # noqa: BLE001
                    log("tick failed: %r" % (exc,))
                stop.wait(TICK_SECONDS)
        finally:
            self.close()

    def close(self):
        if self.server is not None:
            self.server.close()
        try:
            self.checkpoint()
        except Exception as exc:                              # noqa: BLE001
            log("the final checkpoint failed: %r" % (exc,))
        self.client.close()
        self.journal.close()


def compare_stamped(node, family, hour, stamped, page, index, now):
    buckets = (((page or {}).get("aggregations") or {}).get("versions")
               or {}).get("buckets") or []
    indexed = {row["key"]: int(row["doc_count"]) for row in buckets}
    over = sorted(identity for identity, count in indexed.items()
                  if count > stamped.get(identity, 0))
    detail = ""
    if over:
        detail = ("%d versions have more indexed records than stamped ones"
                  % len(over))
    if page and page.get("timed_out"):
        detail = "the search timed out; the indexed figure is a floor"
    document = contract.check_document(
        contract.CHECK_STAMPED_AGAINST_INDEXED, node, family, COARSE, hour,
        sum(stamped.values()), sum(indexed.values()), now, index=index,
        detail=detail, versions_short=over)
    document["ok"] = not over and not (page or {}).get("timed_out")
    return document


def main():
    drainbench.install_merged_create_template()
    import signal
    stop = threading.Event()

    def halt(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    stamper = Stamper(transport=Transport())
    stamper.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
