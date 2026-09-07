#!/usr/bin/env python3
"""A proxy in front of OpenSearch that loses success responses on purpose.

This is the fault the collector's output has to survive and the one no file
sink can produce. A bulk request travels, OpenSearch APPLIES it, and the 200
that says so never gets back: a reset connection, a proxy timeout, a cluster
under load that drops the socket. Fluent Bit cannot tell that from a request
that never arrived, so it retries the whole chunk -- correctly, because the
alternative is losing it.

What happens next is decided entirely by whether the records carry a document
identifier. Without one, OpenSearch generates a fresh identifier per operation
and the retry writes every record a second time. With one, the retry is an
overwrite of the document already there.

So this forwards the request, waits for the whole upstream response -- the write
is committed before anything is lost, which is what makes it a LOST SUCCESS and
not a lost request -- and then tells the client the request failed.

It tells it with a 503 rather than by hanging up. Hanging up is the more literal
fault and it was the first version, but Fluent Bit retried a silently closed
connection inside a 150-second window in most runs and not all of them, and a
harness that injects its fault only sometimes is not a harness. A 503 after the
write has been applied is the same thing from the client's side -- the cluster
holds the records and the client has been told it does not -- and it is retried
every time.

What it counts is read out of the RESPONSE, not out of the request. Counting the
request was the first version and it overstated the total badly: a body is
forwarded on a connection Fluent Bit has already given up on, or the upstream
socket is stale and the forward raises, and neither of those reached Lucene. The
figure the test needs is how many operations OpenSearch actually APPLIED, which
only the response can answer.
"""
import hashlib
import http.client
import json
import os
import socketserver
import sys
import threading

UPSTREAM = os.environ.get("RETRY_UPSTREAM", "sink:9200")
DROP_FIRST = int(os.environ.get("RETRY_DROP_FIRST", "1"))
STATS_PATH = os.environ.get("RETRY_STATS", "/state/stats.json")

APPLIED = (200, 201)

LOCK = threading.Lock()
STATS = {
    "bulk_requests": 0,
    "bulk_responses_dropped": 0,
    "operations_applied": 0,
    "ids": [],
    "operations_without_id": 0,
    # A rejection is not one thing. A 409 on an identifier already written is
    # the cluster REFUSING A DUPLICATE, which is a correct outcome and the
    # proof the retry happened; anything else is a real failure.
    "duplicates_refused": 0,
    "other_rejections": [],
    # The injected fault being retried, counted on the REQUEST side and
    # independent of identifiers, so both arms can wait on the same signal.
    # A test that stops before the retry arrives is measuring nothing, and
    # waiting for the record count to go quiet is not the same as waiting for
    # this: the quiet comes first.
    "repeated_bodies": 0,
    # The fault, measured on the record and not on the chunk: an operation
    # OpenSearch has already seen this record's `doc_id` for. A repeated chunk
    # is not the same thing -- a chunk can be re-sent because the first attempt
    # never reached the cluster at all, and that is not the fault under test.
    # Every record carries `doc_id` in its body whether or not the output uses
    # it as the `_id`, so this counts the same way in both arms.
    "records_attempted_twice": 0,
}
APPLIED_IDS = set()
SEEN_RECORDS = set()
BODIES = set()


def requested(body):
    """The identifier each operation in a bulk body asks for, in order.

    None where the operation names no identifier, which is what OpenSearch
    turns into a generated one -- and is exactly the condition under test.
    """
    out = []
    for index, line in enumerate(body.split(b"\n")):
        if index % 2:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            action = json.loads(line)
        except ValueError:
            continue
        for verb in ("index", "create", "update"):
            if verb in action:
                out.append(action[verb].get("_id"))
                break
    return out


def carried(body):
    """The `doc_id` in each source line, in order. The record's own identity."""
    out = []
    for index, line in enumerate(body.split(b"\n")):
        if not index % 2:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line).get("doc_id"))
        except ValueError:
            out.append(None)
    return out


def record(body, payload):
    """Count what OpenSearch confirmed, pairing each result with its request."""
    asked = requested(body)
    records = carried(body)
    try:
        items = json.loads(payload).get("items", [])
    except ValueError:
        items = []
    applied = without = repeats = 0
    rejected = []
    ids = []
    for position, item in enumerate(items):
        result = next(iter(item.values()), {})
        status = result.get("status")
        if status in APPLIED or status == 409:
            key = records[position] if position < len(records) else None
            if key:
                with LOCK:
                    if key in SEEN_RECORDS:
                        repeats += 1
                    SEEN_RECORDS.add(key)
        if status in APPLIED:
            applied += 1
            wanted = asked[position] if position < len(asked) else None
            if wanted:
                ids.append(wanted)
            else:
                without += 1
        else:
            rejected.append((result.get("status"),
                             (result.get("error") or {}).get("type"),
                             asked[position] if position < len(asked) else None))
    with LOCK:
        STATS["operations_applied"] += applied
        STATS["records_attempted_twice"] += repeats
        STATS["operations_without_id"] += without
        STATS["ids"].extend(ids)
        APPLIED_IDS.update(ids)
        for status, kind, wanted in rejected:
            if status == 409 and wanted in APPLIED_IDS:
                STATS["duplicates_refused"] += 1
            else:
                STATS["other_rejections"].append(
                    {"status": status, "type": kind})
        STATS["other_rejections"] = STATS["other_rejections"][:10]
        publish()


def claim_drop(body):
    digest = hashlib.sha1(body).hexdigest()
    with LOCK:
        STATS["bulk_requests"] += 1
        if digest in BODIES:
            STATS["repeated_bodies"] += 1
        BODIES.add(digest)
        drop = STATS["bulk_responses_dropped"] < DROP_FIRST
        if drop:
            STATS["bulk_responses_dropped"] += 1
        publish()
    return drop


def publish():
    """Write the counters out. Call with LOCK held.

    Both halves are under the lock and the temporary name is per thread. The
    first version took the snapshot under the lock and then wrote it outside,
    through one shared temporary filename: two threads then raced on the same
    path and one of them found it already renamed away -- a FileNotFoundError
    inside a request handler, which loses that response and stalls the very
    retry the test is waiting for. A harness that can break the thing it is
    measuring is not measuring it.
    """
    snapshot = dict(STATS, ids=sorted(set(STATS["ids"])))
    tmp = "%s.%d.tmp" % (STATS_PATH, threading.get_ident())
    with open(tmp, "w") as handle:
        json.dump(snapshot, handle)
    os.replace(tmp, STATS_PATH)


def read_headers(stream):
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = stream.read(1)
        if not chunk:
            return None, None
        head += chunk
    lines = head.split(b"\r\n")
    request = lines[0].decode("latin-1")
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            name, _, value = line.partition(b":")
            headers[name.decode("latin-1").lower()] = value.strip().decode("latin-1")
    return request, headers


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream = http.client.HTTPConnection(UPSTREAM, timeout=60)
        try:
            while True:
                request, headers = read_headers(self.rfile)
                if request is None:
                    return
                method, path, _ = request.split(" ", 2)
                length = int(headers.get("content-length", "0"))
                body = self.rfile.read(length) if length else b""

                bulk = "_bulk" in path and bool(body)
                drop = claim_drop(body) if bulk else False

                send = {k: v for k, v in headers.items()
                        if k not in ("host", "connection")}
                upstream.request(method, path, body=body, headers=send)
                response = upstream.getresponse()
                payload = response.read()
                if bulk and response.status < 300:
                    record(body, payload)

                if drop:
                    # The write is committed upstream. Tell the client it is not.
                    body = b'{"error":"injected lost success"}'
                    self.wfile.write(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: %d\r\n\r\n%s"
                        % (len(body), body))
                    self.wfile.flush()
                    continue

                out = ["HTTP/1.1 %d %s" % (response.status, response.reason)]
                for name, value in response.getheaders():
                    if name.lower() in ("transfer-encoding", "connection"):
                        continue
                    out.append("%s: %s" % (name, value))
                out.append("Content-Length: %d" % len(payload))
                out.append("")
                out.append("")
                self.wfile.write("\r\n".join(out).encode("latin-1") + payload)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            return
        finally:
            upstream.close()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with LOCK:
        publish()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9200
    Server(("0.0.0.0", port), Handler).serve_forever()
