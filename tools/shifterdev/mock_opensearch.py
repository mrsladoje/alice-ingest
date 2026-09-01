import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus

PORT = int(os.environ.get("MOCK_OS_PORT", "9209"))
ROWS = int(os.environ.get("MOCK_OS_ROWS", "60000"))
SPAN = int(os.environ.get("MOCK_OS_SPAN_SECONDS", str(24 * 3600)))
MAX_ROWS = int(os.environ.get("MOCK_OS_MAX_ROWS", str(ROWS)))

DOCS = []
_lock = threading.Lock()
_next_id = 0


def field_value(doc, name):
    if name.endswith(".keyword"):
        name = name[:-len(".keyword")]
    return doc.get(name)


def wildcard_to_regex(pattern):
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


def match_clause(doc, clause):
    kind = next(iter(clause))
    body = clause[kind]

    if kind == "match_all":
        return True

    if kind == "bool":
        should = body.get("should")
        if should is not None:
            minimum = body.get("minimum_should_match", 1)
            hits = sum(1 for c in should if match_clause(doc, c))
            if hits < minimum:
                return False
        for c in body.get("filter", []):
            if not match_clause(doc, c):
                return False
        for c in body.get("must", []):
            if not match_clause(doc, c):
                return False
        for c in body.get("must_not", []):
            if match_clause(doc, c):
                return False
        return True

    if kind == "terms":
        field = next(iter(body))
        value = field_value(doc, field)
        return value in body[field]

    if kind == "range":
        field = next(iter(body))
        value = field_value(doc, field)
        if value is None:
            return False
        spec = body[field]
        for bound, worse in (("gte", True), ("lte", False)):
            if bound not in spec:
                continue
            limit = spec[bound]
            if isinstance(value, (int, float)) and isinstance(limit, (int, float)):
                left, right = value, limit
            else:
                left, right = str(value), str(limit)
            if worse and left < right:
                return False
            if not worse and left > right:
                return False
        return True

    if kind == "wildcard":
        field = next(iter(body))
        spec = body[field]
        value = field_value(doc, field)
        if value is None:
            return False
        flags = re.IGNORECASE if spec.get("case_insensitive") else 0
        return re.search(wildcard_to_regex(spec["value"]), str(value),
                         flags) is not None

    if kind == "regexp":
        field = next(iter(body))
        spec = body[field]
        value = field_value(doc, field)
        if value is None:
            return False
        flags = re.IGNORECASE if spec.get("case_insensitive") else 0
        try:
            return re.search(spec["value"], str(value), flags) is not None
        except re.error:
            return False

    if kind == "match_phrase":
        field = next(iter(body))
        value = field_value(doc, field)
        if value is None:
            return False
        needle = body[field]
        if isinstance(needle, dict):
            needle = needle.get("query", "")
        return str(needle).lower() in str(value).lower()

    return False


def sort_key(doc):
    return (str(doc.get("@timestamp") or ""), str(doc.get("ingest_time") or ""))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(200, {"cluster_name": "alice-logs-mock",
                         "version": {"number": "3.7.0"}})

    def do_POST(self):
        if not (self.path.endswith("/_search")
                or self.path.endswith("/_ingest")):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self._json(400, {"error": "bad body"})
            return

        if self.path.endswith("/_ingest"):
            self._json(200, {"indexed": append(body if isinstance(body, list)
                                              else [body])})
            return

        query = body.get("query") or {"match_all": {}}
        size = int(body.get("size") or 10)
        after = body.get("search_after")

        with _lock:
            snapshot = list(DOCS)
        matched = [d for d in snapshot if match_clause(d, query)]
        matched.sort(key=sort_key, reverse=True)
        if after:
            bound = (str(after[0]), str(after[1]) if len(after) > 1 else "")
            matched = [d for d in matched if sort_key(d) < bound]
        window = matched[:size]

        if body.get("track_total_hits") is False:
            total = {"value": min(len(matched), 10000), "relation": "gte"}
        else:
            total = {"value": len(matched), "relation": "eq"}

        self._json(200, {
            "took": 3,
            "hits": {
                "total": total,
                "hits": [
                    {"_id": doc["_docid"], "_source": doc,
                     "sort": list(sort_key(doc))}
                    for doc in window
                ],
            },
        })


def append(records):
    global _next_id
    with _lock:
        for record in records:
            record["_docid"] = "mock-%06d" % _next_id
            _next_id += 1
            DOCS.append(record)
        if len(DOCS) > MAX_ROWS:
            del DOCS[:len(DOCS) - MAX_ROWS]
        return len(records)


def main():
    print(f"[mock-opensearch] generating {ROWS} records over "
          f"{SPAN // 3600} hours", flush=True)
    append(corpus.make(ROWS, SPAN))
    print(f"[mock-opensearch] listening on 127.0.0.1:{PORT}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
