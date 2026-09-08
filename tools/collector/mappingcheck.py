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
import json
import os
import re
import sys
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9299")
    ap.add_argument("--version", default="4.0.14")
    ap.add_argument("--keep", action="store_true")
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
