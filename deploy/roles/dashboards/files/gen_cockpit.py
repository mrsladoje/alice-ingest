#!/usr/bin/env python3
import json
import sys

UNIFIED_ID = "alice-unified"
UNIFIED_TITLE = "infologger,generic-log-*"
TIME_FIELD = "@timestamp"

ERRWARN_Q = "severity:(E or F or W or Error or Fatal or Warning or err)"

INDEX_REF_NAME = "kibanaSavedObjectMeta.searchSourceJSON.index"

DEFAULT_COLUMNS = ["log_source", "severity", "node", "message"]


def dql(q):
    return {"language": "kuery", "query": q}


def search_source(query=None, index_ref=True, filters=None):
    src = {"query": query or dql(""), "filter": filters or []}
    if index_ref:
        src["indexRefName"] = INDEX_REF_NAME
    return src


def index_ref():
    return [{"name": INDEX_REF_NAME, "type": "index-pattern", "id": UNIFIED_ID}]


def saved_search(sid, title, description, query, columns=None):
    return {
        "type": "search",
        "id": sid,
        "attributes": {
            "title": title,
            "description": description,
            "hits": 0,
            "columns": columns or DEFAULT_COLUMNS,
            "sort": [[TIME_FIELD, "desc"]],
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(search_source(dql(query)))
            },
        },
        "references": index_ref(),
    }


def viz(vid, title, vis_state, query=None, index_ref_on=True):
    attrs = {
        "title": title,
        "visState": json.dumps(vis_state),
        "uiStateJSON": "{}",
        "description": "",
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": json.dumps(
                search_source(query, index_ref=index_ref_on)
            )
        },
    }
    return {
        "type": "visualization",
        "id": vid,
        "attributes": attrs,
        "references": index_ref() if index_ref_on else [],
    }


def count_metric(vid, title, query=None):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {}}
        ],
        "params": {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {
                "percentageMode": False,
                "useRanges": False,
                "colorSchema": "Green to Red",
                "metricColorMode": "None",
                "colorsRange": [{"from": 0, "to": 10000}],
                "labels": {"show": True},
                "invertColors": False,
                "style": {"bgFill": "#000", "bgColor": False,
                          "labelColor": False, "subText": "", "fontSize": 60},
            },
        },
    }
    return viz(vid, title, state, query=query)


def terms_table(vid, title, field):
    state = {
        "title": title,
        "type": "table",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": field, "orderBy": "1", "order": "desc",
                        "size": 10, "otherBucket": False,
                        "missingBucket": False}},
        ],
        "params": {
            "perPage": 10,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": True,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state)


def _cat_axis(pos):
    return [{"id": "CategoryAxis-1", "type": "category", "position": pos,
             "show": True, "style": {}, "scale": {"type": "linear"},
             "labels": {"show": True, "filter": True, "truncate": 100},
             "title": {}}]


def _val_axis(pos):
    return [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
             "position": pos, "show": True, "style": {},
             "scale": {"type": "linear", "mode": "normal"},
             "labels": {"show": True, "rotate": 0, "filter": False,
                        "truncate": 100},
             "title": {"text": "Count"}}]


def _series(mode):
    return [{"show": True, "type": "histogram", "mode": mode,
             "data": {"label": "Count", "id": "1"}, "valueAxis": "ValueAxis-1",
             "drawLinesBetweenPoints": True, "lineWidth": 2,
             "showCircles": True}]


def _bar_params(kind, mode, cat_pos, val_pos):
    return {
        "type": kind,
        "grid": {"categoryLines": False},
        "categoryAxes": _cat_axis(cat_pos),
        "valueAxes": _val_axis(val_pos),
        "seriesParams": _series(mode),
        "addTooltip": True,
        "addLegend": True,
        "legendPosition": "right",
        "times": [],
        "addTimeMarker": False,
        "labels": {"show": False},
        "thresholdLine": {"show": False, "value": 10, "width": 1,
                          "style": "full", "color": "#E7664C"},
    }


def severity_over_time(vid, title):
    state = {
        "title": title,
        "type": "histogram",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "date_histogram",
             "schema": "segment",
             "params": {"field": TIME_FIELD, "interval": "auto",
                        "min_doc_count": 1, "drop_partials": False}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "group",
             "params": {"field": "severity", "orderBy": "1", "order": "desc",
                        "size": 8, "otherBucket": False,
                        "missingBucket": False}},
        ],
        "params": _bar_params("histogram", "stacked", "bottom", "left"),
    }
    return viz(vid, title, state)


def top_terms_bar(vid, title, field):
    state = {
        "title": title,
        "type": "horizontal_bar",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "segment",
             "params": {"field": field, "orderBy": "1", "order": "desc",
                        "size": 10, "otherBucket": False,
                        "missingBucket": False}},
        ],
        "params": _bar_params("horizontal_bar", "normal", "left", "bottom"),
    }
    return viz(vid, title, state)


def markdown(vid, title, md):
    state = {
        "title": title,
        "type": "markdown",
        "params": {"fontSize": 12, "openLinksInNewTab": False, "markdown": md},
        "aggs": [],
    }
    return viz(vid, title, state, index_ref_on=False)


def dashboard(panels):
    panels_json = []
    references = []
    for i, (obj_type, obj_id, grid) in enumerate(panels, start=1):
        ref_name = "panel_%d" % i
        pidx = str(i)
        panels_json.append({
            "version": "2.17.0",
            "gridData": {**grid, "i": pidx},
            "panelIndex": pidx,
            "embeddableConfig": {},
            "panelRefName": ref_name,
        })
        references.append({"name": ref_name, "type": obj_type, "id": obj_id})
    return {
        "type": "dashboard",
        "id": "alice-cockpit",
        "attributes": {
            "title": "ALICE Cockpit",
            "description": "Unified operations view over InfoLogger, DDS and "
                           "stdout logs.",
            "hits": 0,
            "panelsJSON": json.dumps(panels_json),
            "optionsJSON": json.dumps({"hidePanelTitles": False,
                                       "useMargins": True}),
            "version": 1,
            "timeRestore": False,
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(
                    {"query": dql(""), "filter": []})
            },
        },
        "references": references,
    }


def build():
    objects = []

    objects.append({
        "type": "index-pattern",
        "id": UNIFIED_ID,
        "attributes": {"title": UNIFIED_TITLE, "timeFieldName": TIME_FIELD},
        "references": [],
    })

    objects += [
        saved_search(
            "alice-search-errwarn", "Errors & Warnings — all sources",
            "Every Error/Warning/Fatal across all three severity encodings "
            "(infologger E/W/F, stdout Error/Warning/Fatal, dds err).",
            ERRWARN_Q),
        saved_search(
            "alice-search-tcp", "TCP / connection issues",
            "Network trouble by message content.",
            "message:(tcp or connection or refused or timeout)"),
        saved_search(
            "alice-search-detector", "By detector (TPC / ITS / MCH)",
            "InfoLogger records for a given detector.",
            "detector:(TPC or ITS or MCH)"),
        saved_search(
            "alice-search-host", "One host — edit host:epnNNN",
            "One EPN's whole story; pairs with 'View surrounding documents'. "
            "host + hostname cover all three sources.",
            "host:epn* or hostname:epn*"),
        saved_search(
            "alice-search-system", "By subsystem (ODC / DPL)",
            "InfoLogger records for a given O2 subsystem.",
            "system:(ODC or DPL)"),
        saved_search(
            "alice-search-dds", "DDS problems",
            "Non-info DDS agent/workflow lines.",
            "log_source:dds and not severity:inf"),
        saved_search(
            "alice-search-stdout", "stdout crashes",
            "Error/Fatal lines from the O2 process stdout family.",
            "log_source:stdout and severity:(Error or Fatal)"),
    ]

    header_md = (
        "## \U0001F6F0️ ALICE Cockpit\n"
        "Unified view over **InfoLogger**, **DDS** and **stdout**. "
        "Use the *log_source* filter to focus a single family, or open the "
        "**[unified Discover](/app/data-explorer/discover)** and "
        "*View surrounding documents* for cross-source time context."
    )
    index_mgmt_md = (
        "### \U0001F527 Cluster health\n"
        "**[Open Index Management →]"
        "(/app/opensearch_index_management_dashboards#/indices)**\n\n"
        "Indices table: health, shards, replicas, docs, size."
    )
    objects += [
        markdown("alice-viz-header", "Cockpit header", header_md),
        count_metric("alice-viz-total", "Total records"),
        count_metric("alice-viz-errwarn", "Errors & Warnings",
                     query=dql(ERRWARN_Q)),
        terms_table("alice-viz-bysource", "Records by source", "log_source"),
        markdown("alice-viz-indexmgmt", "Cluster health link", index_mgmt_md),
        severity_over_time("alice-viz-sev-time", "Severity over time"),
        top_terms_bar("alice-viz-top-hosts", "Top hosts (dds / stdout)",
                      "host"),
        top_terms_bar("alice-viz-top-systems", "Top systems (infologger)",
                      "system"),
    ]

    panels = [
        ("visualization", "alice-viz-header",      {"x": 0,  "y": 0,  "w": 48, "h": 4}),
        ("visualization", "alice-viz-total",       {"x": 0,  "y": 4,  "w": 12, "h": 8}),
        ("visualization", "alice-viz-errwarn",     {"x": 12, "y": 4,  "w": 12, "h": 8}),
        ("visualization", "alice-viz-bysource",    {"x": 24, "y": 4,  "w": 12, "h": 8}),
        ("visualization", "alice-viz-indexmgmt",   {"x": 36, "y": 4,  "w": 12, "h": 8}),
        ("visualization", "alice-viz-sev-time",    {"x": 0,  "y": 12, "w": 48, "h": 12}),
        ("visualization", "alice-viz-top-hosts",   {"x": 0,  "y": 24, "w": 24, "h": 12}),
        ("visualization", "alice-viz-top-systems", {"x": 24, "y": 24, "w": 24, "h": 12}),
        ("search",        "alice-search-errwarn",  {"x": 0,  "y": 36, "w": 48, "h": 16}),
    ]
    objects.append(dashboard(panels))
    return objects


def main():
    for obj in build():
        sys.stdout.write(json.dumps(obj) + "\n")


if __name__ == "__main__":
    main()
