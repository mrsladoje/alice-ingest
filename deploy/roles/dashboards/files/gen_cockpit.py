#!/usr/bin/env python3
import json
import sys

UNIFIED_ID = "alice-unified"
UNIFIED_TITLE = "infologger,generic-log-*"
METRICS_ID = "alice-metrics"
METRICS_TITLE = "cockpit-metrics"
AD_RESULTS_ID = "alice-ad-results"
AD_RESULTS_TITLE = ".opendistro-anomaly-results*"
ALERTS_ID = "alice-alerts"
ALERTS_TITLE = ".opendistro-alerting-alerts*"
ANOMALIES_ID = "alice-anomalies"
ANOMALIES_TITLE = "alice-anomalies"
INCIDENTS_ID = "alice-incidents"
INCIDENTS_TITLE = "alice-incidents"
SIGNALS_ID = "alice-signals"
SIGNALS_TITLE = "alice-signals"
TIME_FIELD = "@timestamp"
PANEL_VERSION = "3.7.0"

LOG_TIME_FROM = "now-1y"
HEALTH_RANGE = {"from": "now-1h", "to": "now"}
REFRESH_MS = 30000
STALE_SECONDS = 90

ERRWARN_Q = "severity_norm:(error or fatal or warning)"
REALTIME_Q = "run:realtime"
ANOMALY_FLOOR_Q = "run:realtime and grade > 0.5"

INDEX_REF_NAME = "kibanaSavedObjectMeta.searchSourceJSON.index"

DEFAULT_COLUMNS = ["log_source", "severity_norm", "origin_host", "message"]


def dql(q):
    return {"language": "kuery", "query": q}


def search_source(query=None, index_ref=True, filters=None):
    src = {"query": query or dql(""), "filter": filters or []}
    if index_ref:
        src["indexRefName"] = INDEX_REF_NAME
    return src


def index_ref(pattern=UNIFIED_ID):
    return [{"name": INDEX_REF_NAME, "type": "index-pattern", "id": pattern}]


def saved_search(sid, title, description, query, columns=None,
                 pattern=UNIFIED_ID, sort_field=TIME_FIELD):
    return {
        "type": "search",
        "id": sid,
        "attributes": {
            "title": title,
            "description": description,
            "hits": 0,
            "columns": columns or DEFAULT_COLUMNS,
            "sort": [[sort_field, "desc"]],
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(search_source(dql(query)))
            },
        },
        "references": index_ref(pattern),
    }


def viz(vid, title, vis_state, query=None, index_ref_on=True,
        pattern=UNIFIED_ID):
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
        "references": index_ref(pattern) if index_ref_on else [],
    }


def count_metric(vid, title, query=None):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {}}
        ],
        "params": _metric_params(60),
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
            "showTotal": False,
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


def _val_axis(pos, title="Count"):
    return [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
             "position": pos, "show": True, "style": {},
             "scale": {"type": "linear", "mode": "normal"},
             "labels": {"show": True, "rotate": 0, "filter": False,
                        "truncate": 100},
             "title": {"text": title}}]


def _series(mode, kind="histogram", labels=None):
    out = []
    for i, label in enumerate(labels or ["Count"], start=1):
        out.append({"show": True, "type": kind, "mode": mode,
                    "data": {"label": label, "id": str(i)},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True, "lineWidth": 2,
                    "interpolate": "linear", "showCircles": True})
    return out


def _bar_params(kind, mode, cat_pos, val_pos, y_title="Count"):
    series_kind = "histogram" if kind == "horizontal_bar" else kind
    return {
        "type": kind,
        "grid": {"categoryLines": False},
        "categoryAxes": _cat_axis(cat_pos),
        "valueAxes": _val_axis(val_pos, y_title),
        "seriesParams": _series(mode, series_kind),
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
             "params": {"field": "severity_norm", "orderBy": "1", "order": "desc",
                        "size": 8, "otherBucket": False,
                        "missingBucket": False}},
        ],
        "params": _bar_params("histogram", "stacked", "bottom", "left",
                              "log records"),
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
        "params": _bar_params("horizontal_bar", "normal", "left", "bottom",
                              "log records"),
    }
    return viz(vid, title, state)


def _top_hit(agg_id, field, aggregate, label=None):
    params = {"field": field, "aggregate": aggregate, "size": 1,
              "sortField": TIME_FIELD, "sortOrder": "desc"}
    if label:
        params["customLabel"] = label
    return {"id": agg_id, "enabled": True, "type": "top_hits",
            "schema": "metric", "params": params}


def _metric_params(font_size=40):
    return {
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
                      "labelColor": False, "subText": "",
                      "fontSize": font_size},
        },
    }


def latest_metric(vid, title, field, query, aggregate="concat", label=None):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [_top_hit("1", field, aggregate, label)],
        "params": _metric_params(),
    }
    return viz(vid, title, state, query=dql(query), pattern=METRICS_ID)


def latest_table(vid, title, bucket_field, columns, query, size=15):
    aggs = []
    for i, (field, aggregate, label) in enumerate(columns, start=1):
        aggs.append(_top_hit(str(i), field, aggregate, label))
    aggs.append({"id": str(len(columns) + 1), "enabled": True,
                 "type": "terms", "schema": "bucket",
                 "params": {"field": bucket_field, "orderBy": "_key",
                            "order": "asc", "size": size,
                            "otherBucket": False, "missingBucket": False}})
    state = {
        "title": title,
        "type": "table",
        "aggs": aggs,
        "params": {
            "perPage": size,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state, query=dql(query), pattern=METRICS_ID)


def anomaly_table(vid, title):
    state = {
        "title": title,
        "type": "table",
        "aggs": [
            {"id": "1", "enabled": True, "type": "max", "schema": "metric",
             "params": {"field": "grade", "customLabel": "worst grade"}},
            {"id": "2", "enabled": True, "type": "max", "schema": "metric",
             "params": {"field": "confidence", "customLabel": "confidence"}},
            {"id": "3", "enabled": True, "type": "count", "schema": "metric",
             "params": {"customLabel": "times seen"}},
            {"id": "4", "enabled": True, "type": "max", "schema": "metric",
             "params": {"field": TIME_FIELD, "customLabel": "last seen"}},
            {"id": "5", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "about", "orderBy": "1", "order": "desc",
                        "size": 10, "otherBucket": False,
                        "missingBucket": False,
                        "customLabel": "what looks wrong"}},
            {"id": "6", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "scope", "orderBy": "1", "order": "desc",
                        "size": 5, "otherBucket": False,
                        "missingBucket": False, "customLabel": "where"}},
        ],
        "params": {
            "perPage": 10,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state, query=dql(ANOMALY_FLOOR_Q),
               pattern=ANOMALIES_ID)


def alerts_table(vid, title):
    state = {
        "title": title,
        "type": "table",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count", "schema": "metric",
             "params": {"customLabel": "alerts"}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "monitor_name.keyword", "orderBy": "1",
                        "order": "desc", "size": 10,
                        "otherBucket": False, "missingBucket": False}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "state", "orderBy": "1", "order": "desc",
                        "size": 5, "otherBucket": False, "missingBucket": False}},
        ],
        "params": {
            "perPage": 10,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state, query=dql("state:ACTIVE"), pattern=ALERTS_ID)


def active_alert_metric(vid, title):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {"customLabel": "active alerts"}}
        ],
        "params": _metric_params(48),
    }
    return viz(vid, title, state, query=dql("state:ACTIVE"), pattern=ALERTS_ID)


def open_incident_metric(vid, title):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [
            {"id": "1", "enabled": True, "type": "count",
             "schema": "metric", "params": {"customLabel": "open incidents"}}
        ],
        "params": _metric_params(48),
    }
    return viz(vid, title, state, query=dql("state:firing"),
               pattern=INCIDENTS_ID)


def incident_table(vid, title):
    state = {
        "title": title,
        "type": "table",
        "aggs": [
            {"id": "1", "enabled": True, "type": "max", "schema": "metric",
             "params": {"field": "member_count",
                        "customLabel": "signals covered"}},
            {"id": "2", "enabled": True, "type": "max", "schema": "metric",
             "params": {"field": "last_seen", "customLabel": "last seen"}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "alertname", "orderBy": "1", "order": "desc",
                        "size": 10, "otherBucket": False,
                        "missingBucket": False, "customLabel": "what broke"}},
            {"id": "4", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "notification_scope", "orderBy": "1",
                        "order": "desc", "size": 5, "otherBucket": False,
                        "missingBucket": False, "customLabel": "who owns it"}},
            {"id": "5", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "class", "orderBy": "1", "order": "desc",
                        "size": 5, "otherBucket": False,
                        "missingBucket": False, "customLabel": "class"}},
        ],
        "params": {
            "perPage": 10,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state, query=dql("state:firing"),
               pattern=INCIDENTS_ID)


def scope_kind_table(vid, title):
    state = {
        "title": title,
        "type": "table",
        "aggs": [
            {"id": "1", "enabled": True, "type": "cardinality",
             "schema": "metric",
             "params": {"field": "scope", "customLabel": "how many"}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "scope_kind", "orderBy": "1", "order": "desc",
                        "size": 6, "otherBucket": False,
                        "missingBucket": False, "customLabel": "what kind"}},
        ],
        "params": {
            "perPage": 6,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        },
    }
    return viz(vid, title, state, query=dql(ANOMALY_FLOOR_Q),
               pattern=ANOMALIES_ID)


def metric_timechart(vid, title, metrics, query, group_field=None,
                     kind="line", mode="normal", y_title="Count"):
    aggs = []
    labels = []
    for i, (agg_type, field, label) in enumerate(metrics, start=1):
        aggs.append({"id": str(i), "enabled": True, "type": agg_type,
                     "schema": "metric",
                     "params": {"field": field, "customLabel": label}})
        labels.append(label)
    nxt = len(metrics) + 1
    aggs.append({"id": str(nxt), "enabled": True, "type": "date_histogram",
                 "schema": "segment",
                 "params": {"field": TIME_FIELD, "interval": "auto",
                            "min_doc_count": 0, "drop_partials": False}})
    if group_field:
        aggs.append({"id": str(nxt + 1), "enabled": True, "type": "terms",
                     "schema": "group",
                     "params": {"field": group_field, "orderBy": "_key",
                                "order": "asc", "size": 10,
                                "otherBucket": False,
                                "missingBucket": False}})
    params = _bar_params(kind, mode, "bottom", "left", y_title)
    params["seriesParams"] = _series(mode, kind=kind, labels=labels)
    state = {"title": title, "type": kind, "aggs": aggs, "params": params}
    return viz(vid, title, state, query=dql(query), pattern=METRICS_ID)


def markdown(vid, title, md):
    state = {
        "title": title,
        "type": "markdown",
        "params": {"fontSize": 12, "openLinksInNewTab": False, "markdown": md},
        "aggs": [],
    }
    return viz(vid, title, state, index_ref_on=False)


def status_strip(vid, title):
    spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "autosize": {"type": "fit", "contains": "padding"},
        "padding": 4,
        "data": [{
            "name": "latest",
            "url": {
                "index": METRICS_TITLE,
                "body": {
                    "size": 60,
                    "sort": [{TIME_FIELD: {"order": "desc"}}],
                    "query": {"terms": {
                        "kind": ["cluster", "osd", "fluentbit"]}},
                },
            },
            "format": {"property": "hits.hits"},
            "transform": [
                {"type": "formula", "as": "ts",
                 "expr": "time(toDate(datum._source['@timestamp']))"},
                {"type": "formula", "as": "key",
                 "expr": "datum._source.kind + "
                         "(datum._source.collector_id ? ':' + datum._source.collector_id "
                         ": '')"},
                {"type": "window", "groupby": ["key"],
                 "sort": {"field": "ts", "order": "descending"},
                 "ops": ["rank"], "as": ["rank"]},
                {"type": "filter", "expr": "datum.rank == 1"},
                {"type": "formula", "as": "age",
                 "expr": "(now() - datum.ts) / 1000"},
                {"type": "formula", "as": "stale",
                 "expr": "datum.age > %d" % STALE_SECONDS},
                {"type": "formula", "as": "state",
                 "expr": "datum._source.kind == 'cluster' ? "
                         "upper(datum._source.cluster_status) : "
                         "datum._source.kind == 'osd' ? "
                         "upper(datum._source.osd_state) : "
                         "datum._source.fb_up != 1 ? 'DOWN' : "
                         "datum._source.fb_healthy == 1 ? 'UP' : "
                         "'UNHEALTHY'"},
                {"type": "formula", "as": "label",
                 "expr": "datum._source.kind == 'cluster' ? 'CLUSTER' : "
                         "datum._source.kind == 'osd' ? 'DASHBOARDS' : "
                         "'FLUENT BIT ' + datum._source.collector_id"},
                {"type": "formula", "as": "display",
                 "expr": "datum.stale ? 'STALE ' + format(datum.age, '.0f') "
                         "+ 's' : datum.state"},
                {"type": "formula", "as": "color",
                 "expr": "datum.stale ? '#98A2B3' : "
                         "datum.state == 'GREEN' || datum.state == 'UP' ? "
                         "'#017D73' : "
                         "datum.state == 'YELLOW' || "
                         "datum.state == 'UNHEALTHY' ? '#F5A700' : "
                         "'#BD271E'"},
                {"type": "collect", "sort": {"field": "label"}},
            ],
        }],
        "scales": [{
            "name": "xband",
            "type": "band",
            "domain": {"data": "latest", "field": "label"},
            "range": "width",
            "padding": 0.08,
        }],
        "marks": [
            {"type": "rect", "from": {"data": "latest"},
             "encode": {"update": {
                 "x": {"scale": "xband", "field": "label"},
                 "width": {"scale": "xband", "band": 1},
                 "y": {"value": 0},
                 "y2": {"signal": "height"},
                 "fill": {"field": "color"},
                 "cornerRadius": {"value": 4},
             }}},
            {"type": "text", "from": {"data": "latest"},
             "encode": {"update": {
                 "x": {"scale": "xband", "field": "label",
                       "band": 0.5},
                 "y": {"signal": "height * 0.32"},
                 "text": {"field": "label"},
                 "align": {"value": "center"},
                 "baseline": {"value": "middle"},
                 "fill": {"value": "#FFFFFF"},
                 "fontSize": {"value": 11},
             }}},
            {"type": "text", "from": {"data": "latest"},
             "encode": {"update": {
                 "x": {"scale": "xband", "field": "label",
                       "band": 0.5},
                 "y": {"signal": "height * 0.68"},
                 "text": {"field": "display"},
                 "align": {"value": "center"},
                 "baseline": {"value": "middle"},
                 "fill": {"value": "#FFFFFF"},
                 "fontWeight": {"value": "bold"},
                 "fontSize": {"value": 16},
             }}},
            {"type": "text",
             "encode": {"update": {
                 "x": {"signal": "width / 2"},
                 "y": {"signal": "height / 2"},
                 "text": {"value": "NO DATA — alice-metrics has not "
                                   "written yet"},
                 "align": {"value": "center"},
                 "baseline": {"value": "middle"},
                 "fill": {"value": "#BD271E"},
                 "fontWeight": {"value": "bold"},
                 "fontSize": {"value": 16},
                 "opacity": {"signal":
                             "length(data('latest')) ? 0 : 1"},
             }}},
        ],
    }
    state = {
        "title": title,
        "type": "vega",
        "aggs": [],
        "params": {"spec": json.dumps(spec)},
    }
    return viz(vid, title, state, index_ref_on=False)


def dashboard(panels):
    panels_json = []
    references = []
    for i, (obj_type, obj_id, grid, *extra) in enumerate(panels, start=1):
        ref_name = "panel_%d" % i
        pidx = str(i)
        panels_json.append({
            "version": PANEL_VERSION,
            "gridData": {**grid, "i": pidx},
            "panelIndex": pidx,
            "embeddableConfig": extra[0] if extra else {},
            "panelRefName": ref_name,
        })
        references.append({"name": ref_name, "type": obj_type, "id": obj_id})
    return {
        "type": "dashboard",
        "id": "alice-cockpit",
        "attributes": {
            "title": "ALICE Cockpit",
            "description": "Unified operations view over InfoLogger, DDS and "
                           "stdout logs, plus cluster, Fluent Bit and "
                           "Dashboards health.",
            "hits": 0,
            "panelsJSON": json.dumps(panels_json),
            "optionsJSON": json.dumps({"hidePanelTitles": False,
                                       "useMargins": True}),
            "version": 1,
            "timeRestore": True,
            "timeFrom": LOG_TIME_FROM,
            "timeTo": "now",
            "refreshInterval": {"pause": False, "value": REFRESH_MS},
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

    objects.append({
        "type": "index-pattern",
        "id": METRICS_ID,
        "attributes": {"title": METRICS_TITLE, "timeFieldName": TIME_FIELD},
        "references": [],
    })

    objects.append({
        "type": "index-pattern",
        "id": AD_RESULTS_ID,
        "attributes": {"title": AD_RESULTS_TITLE, "timeFieldName": "data_end_time"},
        "references": [],
    })

    objects.append({
        "type": "index-pattern",
        "id": ALERTS_ID,
        "attributes": {"title": ALERTS_TITLE, "timeFieldName": "start_time"},
        "references": [],
    })

    objects.append({
        "type": "index-pattern",
        "id": ANOMALIES_ID,
        "attributes": {"title": ANOMALIES_TITLE, "timeFieldName": TIME_FIELD},
        "references": [],
    })

    objects.append({
        "type": "index-pattern",
        "id": INCIDENTS_ID,
        "attributes": {"title": INCIDENTS_TITLE, "timeFieldName": TIME_FIELD},
        "references": [],
    })

    objects.append({
        "type": "index-pattern",
        "id": SIGNALS_ID,
        "attributes": {"title": SIGNALS_TITLE, "timeFieldName": TIME_FIELD},
        "references": [],
    })

    objects += [
        saved_search(
            "alice-search-errwarn", "Errors & Warnings — all sources",
            "Every Error/Warning/Fatal across all three sources, via the "
            "collector-stamped severity_norm field.",
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
            "alice-search-host", "One host — edit origin_host:epnNNN",
            "One EPN's whole story; pairs with 'View surrounding documents'. "
            "origin_host is stamped on all three sources.",
            "origin_host:epn*"),
        saved_search(
            "alice-search-system", "By subsystem (ODC / DPL)",
            "InfoLogger records for a given O2 subsystem.",
            "system:(ODC or DPL)"),
        saved_search(
            "alice-search-dds", "DDS problems",
            "Non-info DDS agent/workflow lines.",
            "log_source:dds and not severity_norm:info"),
        saved_search(
            "alice-search-stdout", "stdout crashes",
            "Error/Fatal lines from the O2 process stdout family.",
            "log_source:stdout and severity_norm:(error or fatal)"),
        saved_search(
            "alice-search-active-alerts", "Active alerts — what fired",
            "Every alert currently open, newest first: which rule fired, "
            "which trigger, and how serious it is.",
            "state:ACTIVE",
            columns=["monitor_name", "trigger_name", "severity", "state"],
            pattern=ALERTS_ID, sort_field="start_time"),
        saved_search(
            "alice-search-anomalies", "Anomalies — what looks wrong, and where",
            "One row per anomalous window, newest first. 'about' says what "
            "the detector watches, 'scope' says which host or collector it "
            "was watching. Real-time results only — a historical analysis "
            "run now would otherwise be counted as something happening now.",
            REALTIME_Q,
            columns=["severity", "about", "entity_kind", "entity_id",
                     "grade", "confidence"],
            pattern=ANOMALIES_ID),
        saved_search(
            "alice-search-signals", "Signals — every raw row behind an incident",
            "One lossless row per source signal. Grouping never destroys "
            "these: an incident references them, it does not replace them.",
            "",
            columns=["alertname", "state", "severity", "entity_kind",
                     "entity_id", "collector_id", "incident_id"],
            pattern=SIGNALS_ID),
    ]

    header_md = (
        "## \U0001F6F0️ ALICE Cockpit\n"
        "Unified view over **InfoLogger**, **DDS** and **stdout**. Two "
        "clocks on one page: **health panels are live** (pinned to the last "
        "hour, auto-refresh 30 s) — **log panels follow the time picker**, "
        "preset to the last year because the replayed archives do not share "
        "an event period: InfoLogger is March 2026, DDS and stdout are June "
        "2026. Open the **[unified Discover](/app/data-explorer/discover)** "
        "for *surrounding documents*, or use the *log_source* filter to "
        "focus one family."
    )
    health_links_md = (
        "### \U0001F50E Drill down\n"
        "**[Index Management →]"
        "(/app/opensearch_index_management_dashboards#/indices)** — live "
        "indices: health, shards, replicas, docs, size.\n\n"
        "**[Query Insights →](/app/query-insights-dashboards)** — top-N "
        "queries by latency / CPU / memory, live queries."
    )
    health_detail_md = (
        "### \U0001FA7A Detailed platform health — live, last hour\n"
        "Sampled every 30 s by `alice-metrics` into `cockpit-metrics`. "
        "These panels ignore the time picker."
    )
    objects += [
        markdown("alice-viz-header", "Cockpit header", header_md),
        status_strip("alice-viz-status-strip", "Live status"),
        count_metric("alice-viz-total", "Total records"),
        count_metric("alice-viz-errwarn", "Errors & Warnings",
                     query=dql(ERRWARN_Q)),
        terms_table("alice-viz-bysource", "Records by source", "log_source"),
        markdown("alice-viz-health-links", "Health drill-down links",
                 health_links_md),
        severity_over_time("alice-viz-sev-time", "Severity over time"),
        top_terms_bar("alice-viz-top-hosts", "Top hosts (dds / stdout)",
                      "host"),
        top_terms_bar("alice-viz-top-systems", "Top systems (infologger)",
                      "system"),
        markdown("alice-viz-health-header", "Platform health header",
                 health_detail_md),
        latest_metric("alice-viz-cluster-status", "Cluster status",
                      "cluster_status", "kind:cluster", label="status"),
        latest_metric("alice-viz-unassigned", "Unassigned shards",
                      "unassigned_shards", "kind:cluster", aggregate="max",
                      label="unassigned"),
        latest_metric("alice-viz-osd-status", "Dashboards health",
                      "osd_state", "kind:osd", label="state"),
        latest_table("alice-viz-fb-status", "Fluent Bit by collector",
                     "collector_id",
                     [("fb_up", "max", "up (1=yes)"),
                      ("fb_healthy", "max", "healthy (1=yes)"),
                      ("output_records", "max", "records shipped"),
                      ("output_errors", "max", "errors"),
                      ("output_retries_failed", "max", "failed retries"),
                      ("output_dropped", "max", "dropped")],
                     "kind:fluentbit"),
        metric_timechart("alice-viz-ingest-rate",
                         "Indexing rate by index (ops / 30 s)",
                         [("sum", "indexing_delta", "index ops")],
                         "kind:index and not index_name:cockpit-metrics",
                         group_field="index_name",
                         kind="area", mode="stacked",
                         y_title="ops / 30 s"),
        metric_timechart("alice-viz-index-size", "Index size on disk",
                         [("max", "store_bytes", "bytes")],
                         "kind:index", group_field="index_name",
                         y_title="bytes"),
        metric_timechart("alice-viz-fb-throughput",
                         "Fluent Bit records shipped per node",
                         [("sum", "output_records_delta", "records")],
                         "kind:fluentbit", group_field="collector_id",
                         y_title="records / 30 s"),
        metric_timechart("alice-viz-fb-trouble",
                         "Fluent Bit errors / retries / drops",
                         [("sum", "output_errors_delta", "errors"),
                          ("sum", "output_retries_delta", "retries"),
                          ("sum", "output_dropped_delta", "dropped")],
                         "kind:fluentbit", group_field="collector_id",
                         y_title="events / 30 s"),
        latest_table("alice-viz-index-health", "Indices now", "index_name",
                     [("index_health", "concat", "health"),
                      ("pri", "max", "pri"),
                      ("rep", "max", "rep"),
                      ("docs_count", "max", "docs"),
                      ("store_bytes", "max", "bytes")],
                     "kind:index"),
        metric_timechart("alice-viz-node-heap", "Node JVM heap %",
                         [("avg", "heap_percent", "heap %")],
                         "kind:node", group_field="os_node",
                         y_title="%"),
        metric_timechart("alice-viz-osd-perf", "Dashboards response time",
                         [("avg", "response_avg_ms", "avg ms"),
                          ("max", "response_max_ms", "max ms")],
                         "kind:osd", y_title="ms"),
        markdown("alice-viz-detect-header", "Detection header",
                 "### Detection — live alerts & anomalies\n"
                 "An **alert** is a hard rule that crossed a fixed threshold "
                 "— someone decided in advance what \"too much\" means. An "
                 "**anomaly** is a time window that a learned model scored as "
                 "unlike this host's own recent normal; the *grade* is how "
                 "unusual (0–1) and the *confidence* is how much history the "
                 "model had to judge it on. A high grade at low confidence is "
                 "a young model, not an emergency.\n\n"
                 "The tables summarise; the two panels beneath them list the "
                 "individual records with their timestamps. All of it is "
                 "pinned to the last hour and ignores the time picker.\n\n"
                 "*Where* is the host or collector the model was watching. "
                 "Some detectors watch the cluster as one stream and have no "
                 "host — those read **whole fleet**, and the host counter to "
                 "the left deliberately does not count them."),
        markdown("alice-viz-detect-actions", "Act on this",
                 "### ⚙️ Act on this\n"
                 "This dashboard is read-only. To acknowledge, mute or edit "
                 "a rule:\n\n"
                 "**[Alerts →](/app/alerting#/dashboard)** — acknowledge an "
                 "alert so it stops counting as active.\n\n"
                 "**[Monitors →](/app/alerting#/monitors)** — change a "
                 "threshold, or disable a rule that is crying wolf.\n\n"
                 "**[Anomaly detectors →]"
                 "(/app/anomaly-detection-dashboards#/detectors)** — per-"
                 "detector history, the feature values behind a grade, and "
                 "initialisation state.\n\n"
                 "Acknowledging is not the same as fixing: the rule will fire "
                 "again on the next window that still breaches it."),
        markdown("alice-viz-incident-header", "Incidents header",
                 "### 🎯 Incidents — the headline\n"
                 "An **incident** is one episode: the signals that share a "
                 "cause, counted and named. It is the record, not the "
                 "notification — Alertmanager decides when to tell someone, "
                 "`alice-incidents` decides what is true.\n\n"
                 "Grouping never destroys a signal. Every raw alert and "
                 "anomaly behind an incident is still a row in "
                 "`alice-signals`, in the panel below, with the "
                 "`incident_id` that ties it back here.\n\n"
                 "`unknown-mass-silence` means the fleet went quiet and "
                 "nothing authoritative said a run ended. It pages on "
                 "purpose."),
        open_incident_metric("alice-viz-open-incidents", "Open incidents"),
        incident_table("alice-viz-incidents", "Open incidents by cause"),
        active_alert_metric("alice-viz-active-alerts", "Active alerts"),
        scope_kind_table("alice-viz-anomaly-count",
                         "What is affected (realtime, grade>0.5)"),
        alerts_table("alice-viz-alerts", "Active alerts by rule"),
        anomaly_table("alice-viz-anomalies", "Anomalies by what looks wrong"),
    ]

    live = {"timeRange": HEALTH_RANGE}
    panels = [
        ("visualization", "alice-viz-header",         {"x": 0,  "y": 0,  "w": 48, "h": 6}),
        ("visualization", "alice-viz-status-strip",   {"x": 0,  "y": 6,  "w": 48, "h": 5}),
        ("visualization", "alice-viz-cluster-status", {"x": 0,  "y": 11, "w": 8,  "h": 8}, live),
        ("visualization", "alice-viz-unassigned",     {"x": 8,  "y": 11, "w": 8,  "h": 8}, live),
        ("visualization", "alice-viz-osd-status",     {"x": 16, "y": 11, "w": 8,  "h": 8}, live),
        ("visualization", "alice-viz-fb-status",      {"x": 24, "y": 11, "w": 14, "h": 8}, live),
        ("visualization", "alice-viz-health-links",   {"x": 38, "y": 11, "w": 10, "h": 8}),
        ("visualization", "alice-viz-total",          {"x": 0,  "y": 19, "w": 16, "h": 8}),
        ("visualization", "alice-viz-errwarn",        {"x": 16, "y": 19, "w": 16, "h": 8}),
        ("visualization", "alice-viz-bysource",       {"x": 32, "y": 19, "w": 16, "h": 8}),
        ("visualization", "alice-viz-sev-time",       {"x": 0,  "y": 27, "w": 48, "h": 12}),
        ("visualization", "alice-viz-top-hosts",      {"x": 0,  "y": 39, "w": 24, "h": 12}),
        ("visualization", "alice-viz-top-systems",    {"x": 24, "y": 39, "w": 24, "h": 12}),
        ("search",        "alice-search-errwarn",     {"x": 0,  "y": 51, "w": 48, "h": 16}),
        ("visualization", "alice-viz-health-header",  {"x": 0,  "y": 67, "w": 48, "h": 3}),
        ("visualization", "alice-viz-ingest-rate",    {"x": 0,  "y": 70, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-size",     {"x": 24, "y": 70, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-throughput",  {"x": 0,  "y": 82, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-trouble",     {"x": 24, "y": 82, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-health",   {"x": 0,  "y": 94, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-node-heap",      {"x": 16, "y": 94, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-osd-perf",       {"x": 32, "y": 94, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-incident-header", {"x": 0,  "y": 106, "w": 48, "h": 11}, live),
        ("visualization", "alice-viz-open-incidents",  {"x": 0,  "y": 117, "w": 8,  "h": 9}, live),
        ("visualization", "alice-viz-incidents",       {"x": 8,  "y": 117, "w": 40, "h": 9}, live),
        ("search",        "alice-search-signals",      {"x": 0,  "y": 126, "w": 48, "h": 14}, live),
        ("visualization", "alice-viz-detect-header",  {"x": 0,  "y": 140, "w": 48, "h": 13}),
        ("visualization", "alice-viz-active-alerts",  {"x": 0,  "y": 153, "w": 8,  "h": 7}),
        ("visualization", "alice-viz-anomaly-count",  {"x": 0,  "y": 160, "w": 8,  "h": 9}),
        ("visualization", "alice-viz-detect-actions", {"x": 0,  "y": 169, "w": 8,  "h": 12}),
        ("visualization", "alice-viz-alerts",         {"x": 8,  "y": 153, "w": 20, "h": 14}),
        ("visualization", "alice-viz-anomalies",      {"x": 28, "y": 153, "w": 20, "h": 14}),
        ("search",        "alice-search-active-alerts", {"x": 8,  "y": 167, "w": 20, "h": 14}),
        ("search",        "alice-search-anomalies",     {"x": 28, "y": 167, "w": 20, "h": 14}),
    ]
    objects.append(dashboard(panels))
    return objects


def main():
    for obj in build():
        sys.stdout.write(json.dumps(obj) + "\n")


if __name__ == "__main__":
    main()
