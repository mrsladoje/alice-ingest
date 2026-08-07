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
INCIDENT_HISTORY_LOOKBACK = "7d"
TIME_FIELD = "@timestamp"
PANEL_VERSION = "3.7.0"

LOG_TIME_FROM = "now-1y"
HEALTH_RANGE = {"from": "now-1h", "to": "now"}
REFRESH_MS = 30000
STALE_SECONDS = 90

ERRWARN_Q = "severity_norm:(error or fatal or warning)"
REALTIME_Q = "run:realtime"
ANOMALY_FLOOR_Q = "run:realtime and grade > 0.5"

FONT_STACK = ("Inter UI, -apple-system, BlinkMacSystemFont, 'Segoe UI', "
              "Helvetica, Arial, sans-serif")

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


def incident_summary_strip(vid, title):
    open_q = {"term": {"state": "firing"}}
    spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "autosize": {"type": "fit", "contains": "padding"},
        "padding": 8,
        "data": [{
            "name": "stats",
            "url": {
                "index": INCIDENTS_TITLE,
                "body": {
                    "size": 0,
                    "aggs": {"stats": {"filters": {"filters": [
                        open_q,
                        {"bool": {"filter": [open_q,
                                               {"term": {"severity": "page"}}]}},
                        {"bool": {"filter": [open_q,
                                               {"term": {"severity": "warn"}}]}},
                        {"bool": {"filter": [open_q, {"terms": {
                            "episode_state": ["STALE", "RECOVERING"]}}]}},
                    ]}}},
                },
            },
            "format": {"property": "aggregations.stats.buckets"},
            "transform": [
                {"type": "window", "ops": ["row_number"], "as": ["rank"]},
                {"type": "formula", "as": "label",
                 "expr": "datum.rank == 1 ? 'OPEN EPISODES' : "
                         "datum.rank == 2 ? 'PAGE' : "
                         "datum.rank == 3 ? 'WARNING' : 'STALE / RECOVERING'"},
                {"type": "formula", "as": "color",
                 "expr": "datum.rank == 1 ? '#006BB4' : "
                         "datum.rank == 2 ? '#BD271E' : "
                         "datum.rank == 3 ? '#F5A700' : '#98A2B3'"},
            ],
        }],
        "scales": [{
            "name": "cards", "type": "band",
            "domain": {"data": "stats", "field": "rank"},
            "range": "width", "padding": 0.08,
        }],
        "marks": [
            {"type": "rect", "from": {"data": "stats"},
             "encode": {"update": {
                 "x": {"scale": "cards", "field": "rank"},
                 "width": {"scale": "cards", "band": 1},
                 "y": {"value": 0}, "y2": {"signal": "height"},
                 "fill": {"value": "#FFFFFF"},
                 "stroke": {"value": "#D3DAE6"},
                 "cornerRadius": {"value": 4},
             }}},
            {"type": "rect", "from": {"data": "stats"},
             "encode": {"update": {
                 "x": {"scale": "cards", "field": "rank"},
                 "width": {"value": 4},
                 "y": {"value": 0}, "y2": {"signal": "height"},
                 "fill": {"field": "color"},
                 "cornerRadius": {"value": 4},
             }}},
            {"type": "text", "from": {"data": "stats"},
             "encode": {"update": {
                 "x": {"scale": "cards", "field": "rank", "offset": 16},
                 "y": {"signal": "height * 0.32"},
                 "text": {"field": "label"},
                 "fill": {"value": "#69707D"},
                 "font": {"value": FONT_STACK},
                 "fontSize": {"value": 11},
                 "fontWeight": {"value": "bold"},
             }}},
            {"type": "text", "from": {"data": "stats"},
             "encode": {"update": {
                 "x": {"scale": "cards", "field": "rank", "offset": 16},
                 "y": {"signal": "height * 0.72"},
                 "text": {"field": "doc_count"},
                 "fill": {"field": "color"},
                 "font": {"value": FONT_STACK},
                 "fontSize": {"value": 28},
                 "fontWeight": {"value": "bold"},
             }}},
        ],
    }
    state = {"title": title, "type": "vega", "aggs": [],
             "params": {"spec": json.dumps(spec)}}
    return viz(vid, title, state, index_ref_on=False)


def _signals_href(search_id):
    return ("'/app/data-explorer/discover#/view/" + search_id +
            "?_g=(time:(from:\\'' + datum.winFrom + '\\',to:\\'' + "
            "datum.winTo + '\\'))"
            "&_q=(query:(language:kuery,query:\\'episode_id:\"' + "
            "datum._source.episode_id + '\"\\'))'")


def _details_href(search_id):
    return ("'/app/data-explorer/discover#/view/" + search_id +
            "?_g=(time:(from:now-" + INCIDENT_HISTORY_LOOKBACK + ",to:now))"
            "&_q=(query:(language:kuery,query:\\'incident_id:\"' + "
            "datum._source.incident_id + '\"\\'))'")


def _episode_card_text(as_field, expr, dy, fill, size, bold=False,
                       limit="cardWidth - 32"):
    mark = {"type": "text", "from": {"data": "episodes"},
            "encode": {"update": {
                "x": {"value": 16},
                "y": {"signal":
                      "datum.row * (cardHeight + gap) - scroll + %d" % dy},
                "text": ({"field": as_field} if as_field
                         else {"signal": expr}),
                "fill": {"value": fill},
                "font": {"value": FONT_STACK},
                "fontSize": {"value": size},
                "limit": {"signal": limit},
            }}}
    if bold:
        mark["encode"]["update"]["fontWeight"] = {"value": "bold"}
    return [mark]


def _episode_button(x_signal, label, href_field):
    button_opacity = {"signal": "datum._source.episode_id ? 1 : 0"}
    rect = {"type": "rect", "from": {"data": "episodes"},
            "encode": {"update": {
                "x": {"signal": x_signal},
                "y": {"signal":
                      "datum.row * (cardHeight + gap) - scroll + 11"},
                "width": {"value": 64},
                "height": {"value": 20},
                "cornerRadius": {"value": 4},
                "fill": {"value": "rgba(0,107,180,0.08)"},
                "stroke": {"value": "#006BB4"},
                "href": {"field": href_field},
                "cursor": {"value": "pointer"},
                "opacity": button_opacity,
            }}}
    text = {"type": "text", "from": {"data": "episodes"},
            "encode": {"update": {
                "x": {"signal": x_signal + " + 32"},
                "y": {"signal":
                      "datum.row * (cardHeight + gap) - scroll + 21"},
                "text": {"value": label},
                "align": {"value": "center"},
                "baseline": {"value": "middle"},
                "fill": {"value": "#006BB4"},
                "font": {"value": FONT_STACK},
                "fontSize": {"value": 10},
                "fontWeight": {"value": "bold"},
                "href": {"field": href_field},
                "cursor": {"value": "pointer"},
                "opacity": button_opacity,
            }}}
    return [rect, text]


def incident_episode_board(vid, title):
    spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "autosize": {"type": "fit", "contains": "padding"},
        "config": {"kibana": {"renderer": "svg"}},
        "padding": 4,
        "signals": [
            {"name": "gap", "value": 10},
            {"name": "cardHeight", "value": 68},
            {"name": "cardWidth", "update": "width"},
            {"name": "cardRows", "update": "length(data('episodes'))"},
            {"name": "contentHeight",
             "update": "cardRows * cardHeight + "
                       "max(0, cardRows - 1) * gap"},
            {"name": "maxScroll", "update": "max(0, contentHeight - height)"},
            {"name": "scroll", "value": 0,
             "on": [
                 {"events": {"type": "wheel", "consume": True},
                  "update": "clamp(scroll + event.deltaY, 0, maxScroll)"},
                 {"events": {"signal": "maxScroll"},
                  "update": "clamp(scroll, 0, maxScroll)"},
             ]},
            {"name": "thumbHeight",
             "update": "max(24, height * height / "
                           "max(contentHeight, height))"},
            {"name": "thumbY",
             "update": "maxScroll > 0 ? scroll / maxScroll * "
                       "(height - thumbHeight) : 0"},
        ],
        "data": [{
            "name": "episodes",
            "url": {
                "index": INCIDENTS_TITLE,
                "body": {
                    "size": 20,
                    "sort": [{"last_seen": {"order": "desc"}}],
                    "query": {"term": {"state": "firing"}},
                    "_source": ["title", "diagnosis", "affected", "severity",
                                "episode_state", "opened_at", "last_seen",
                                "member_count", "alertname", "episode_id",
                                "incident_id", "worst_grade",
                                "latest_confidence"],
                },
            },
            "format": {"property": "hits.hits"},
            "transform": [
                {"type": "formula", "as": "severityRank",
                 "expr": "datum._source.severity == 'page' ? 0 : 1"},
                {"type": "formula", "as": "last",
                 "expr": "time(toDate(datum._source.last_seen))"},
                {"type": "collect", "sort": {
                    "field": ["severityRank", "last"],
                    "order": ["ascending", "descending"]}},
                {"type": "window", "ops": ["row_number"], "as": ["rank"]},
                {"type": "formula", "as": "row",
                 "expr": "datum.rank - 1"},
                {"type": "formula", "as": "accent",
                 "expr": "datum._source.severity == 'page' ? '#BD271E' : "
                         "datum._source.episode_state == 'STALE' ? '#98A2B3' : "
                         "datum._source.episode_state == 'RECOVERING' ? "
                         "'#017D73' : '#F5A700'"},
                {"type": "formula", "as": "titleText",
                 "expr": "datum._source.title || datum._source.alertname || "
                         "'Untitled episode'"},
                {"type": "formula", "as": "meta",
                 "expr": "upper(datum._source.severity || '?') + '  ·  ' + "
                         "(datum._source.episode_state || 'OPEN') + "
                         "(datum._source.affected ? '  ·  ' + "
                         "datum._source.affected : '') + '  ·  ' + "
                         "(datum._source.member_count || 0) + ' signals  ·  "
                         "since ' + timeFormat(toDate(datum._source.opened_at), "
                         "'%b %d %H:%M')"},
                {"type": "formula", "as": "score",
                 "expr": "datum._source.worst_grade == null ? '' : "
                         "'  ·  worst grade ' + "
                         "format(datum._source.worst_grade, '.3f') + "
                         "' / confidence ' + "
                         "format(datum._source.latest_confidence || 0, '.3f')"},
                {"type": "formula", "as": "diagText",
                 "expr": "datum._source.diagnosis || ''"},
                {"type": "formula", "as": "winFrom",
                 "expr": "utcFormat(toDate(time(toDate("
                         "datum._source.opened_at)) - 300000), "
                         "'%Y-%m-%dT%H:%M:%S.%LZ')"},
                {"type": "formula", "as": "winTo",
                 "expr": "utcFormat(toDate(time(toDate("
                         "datum._source.last_seen)) + 300000), "
                         "'%Y-%m-%dT%H:%M:%S.%LZ')"},
                {"type": "formula", "as": "signalsUrl",
                 "expr": _signals_href("alice-search-signals")},
                {"type": "formula", "as": "detailsUrl",
                 "expr": _details_href("alice-search-incident-history")},
            ],
        }],
        "marks": [
            {"type": "group",
             "encode": {"update": {
                 "x": {"value": 0},
                 "y": {"value": 0},
                 "width": {"signal": "width"},
                 "height": {"signal": "height"},
                 "clip": {"value": True}}},
             "marks": [
                 {"type": "rect", "from": {"data": "episodes"},
                  "encode": {"update": {
                      "x": {"value": 0},
                      "y": {"signal":
                            "datum.row * (cardHeight + gap) - scroll"},
                      "width": {"signal": "cardWidth"},
                      "height": {"signal": "cardHeight"},
                      "fill": {"value": "#FFFFFF"},
                      "stroke": {"value": "#D3DAE6"},
                      "cornerRadius": {"value": 4},
                  }}},
                 {"type": "rect", "from": {"data": "episodes"},
                  "encode": {"update": {
                      "x": {"value": 0},
                      "y": {"signal":
                            "datum.row * (cardHeight + gap) - scroll"},
                      "width": {"value": 4},
                      "height": {"signal": "cardHeight"},
                      "fill": {"field": "accent"},
                      "cornerRadius": {"value": 4},
                  }}},
             ] + _episode_card_text("titleText", None, 18, "#343741", 13,
                                    bold=True,
                                    limit="cardWidth - 190")
               + _episode_card_text(None, "datum.meta + datum.score", 37,
                                    "#69707D", 10)
               + _episode_card_text("diagText", None, 55, "#343741", 11)
               + _episode_button("cardWidth - 162", "SIGNALS", "signalsUrl")
               + _episode_button("cardWidth - 90", "DETAILS", "detailsUrl")},
            {"type": "rect",
             "encode": {"update": {
                 "x": {"signal": "width - 6"},
                 "y": {"value": 0},
                 "width": {"value": 4},
                 "height": {"signal": "height"},
                 "cornerRadius": {"value": 2},
                 "fill": {"value": "#D3DAE6"},
                 "opacity": {"signal": "maxScroll > 0 ? 0.6 : 0"},
             }}},
            {"type": "rect",
             "encode": {"update": {
                 "x": {"signal": "width - 6"},
                 "y": {"signal": "thumbY"},
                 "width": {"value": 4},
                 "height": {"signal": "thumbHeight"},
                 "cornerRadius": {"value": 2},
                 "fill": {"value": "#98A2B3"},
                 "opacity": {"signal": "maxScroll > 0 ? 0.9 : 0"},
             }}},
            {"type": "text", "encode": {"update": {
                 "x": {"signal": "width / 2"},
                 "y": {"signal": "height / 2"},
                 "text": {"value": "NO OPEN EPISODES"},
                 "align": {"value": "center"},
                 "baseline": {"value": "middle"},
                 "fill": {"value": "#017D73"},
                 "font": {"value": FONT_STACK},
                 "fontSize": {"value": 16},
                 "fontWeight": {"value": "bold"},
                 "opacity": {"signal": "length(data('episodes')) ? 0 : 1"},
            }}},
        ],
    }
    state = {"title": title, "type": "vega", "aggs": [],
             "params": {"spec": json.dumps(spec)}}
    return viz(vid, title, state, index_ref_on=False)


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
        saved_search(
            "alice-search-open-incidents", "Open episodes — complete detail",
            "One row per deduplicated open episode. Raw alerts and anomaly "
            "windows are evidence in alice-signals, not separate operational "
            "problems.",
            "state:firing",
            columns=["severity", "title", "affected", "diagnosis",
                     "episode_state", "member_count", "opened_at",
                     "last_seen", "operator_action", "episode_id"],
            pattern=INCIDENTS_ID, sort_field="last_seen"),
        saved_search(
            "alice-search-incident-history",
            "Episode history — every episode of one incident",
            "One row per episode of the same recurring problem, newest "
            "first. The card's Details button opens this filtered to its "
            "own incident_id, so you see how often it came back.",
            "",
            columns=["severity", "title", "episode_state", "member_count",
                     "opened_at", "last_seen", "resolved_at",
                     "operator_action", "episode_id"],
            pattern=INCIDENTS_ID, sort_field="opened_at"),
        saved_search(
            "alice-search-recent-incidents", "Recent resolved episodes",
            "Deduplicated episodes that have recovered, newest first.",
            "state:resolved",
            columns=["severity", "title", "affected", "diagnosis",
                     "member_count", "opened_at", "resolved_at",
                     "episode_id"],
            pattern=INCIDENTS_ID, sort_field="resolved_at"),
    ]

    header_md = (
        "## \U0001F6F0️ ALICE Cockpit\n"
        "The operational headline is the **deduplicated incident episode** "
        "board below. Health panels are live (last hour, refresh 30 s); "
        "raw alerts and anomaly windows are deliberately kept out of the "
        "headline and remain available only as episode evidence. "
        "**Log panels follow the time picker**, "
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
    log_detail_md = (
        "### Raw log evidence — secondary\n"
        "Use this section after an episode tells you where to look. These "
        "panels follow the global time picker and intentionally do not count "
        "as separate incidents."
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
        markdown("alice-viz-log-header", "Log evidence header",
                 log_detail_md),
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
        markdown("alice-viz-detect-header", "Detection drill-down",
                 "### Evidence, not another problem list\n"
                 "The cockpit shows **episodes**, not every alert evaluation "
                 "or anomalous time window. One episode may contain many raw "
                 "signals; `member_count` says how many were folded into it. "
                 "Use **[open episode detail →]"
                 "(/app/data-explorer/discover#/view/alice-search-open-incidents)** "
                 "for every field, **[raw signal evidence →]"
                 "(/app/data-explorer/discover#/view/alice-search-signals)** "
                 "only while investigating, **[episode history →]"
                 "(/app/data-explorer/discover#/view/alice-search-incident-history)** "
                 "to see how often one problem came back, and "
                 "**[resolved episodes →]"
                 "(/app/data-explorer/discover#/view/alice-search-recent-incidents)** "
                 "for recent history."),
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
                 "## Live incident episodes\n"
                 "One card is one deduplicated episode: **what is wrong, "
                 "what is affected, and the exact rule/model meaning**. "
                 "**SIGNALS** opens the raw evidence folded into the episode; "
                 "**DETAILS** its full record. PAGE is urgent; WARNING needs "
                 "attention; STALE means the model stopped evaluating and is "
                 "not evidence of recovery. The board shows five episodes "
                 "and scrolls; it ignores the global time picker, so an "
                 "old-but-open episode cannot disappear."),
        incident_summary_strip("alice-viz-open-incidents",
                               "Episode summary"),
        incident_episode_board("alice-viz-incidents",
                               "Open episodes — deduplicated and actionable"),
        active_alert_metric("alice-viz-active-alerts", "Active alerts"),
        scope_kind_table("alice-viz-anomaly-count",
                         "What is affected (realtime, grade>0.5)"),
        alerts_table("alice-viz-alerts", "Active alerts by rule"),
        anomaly_table("alice-viz-anomalies", "Anomalies by what looks wrong"),
    ]

    live = {"timeRange": HEALTH_RANGE}
    panels = [
        ("visualization", "alice-viz-header",          {"x": 0,  "y": 0, "w": 48, "h": 6}),
        ("visualization", "alice-viz-status-strip",    {"x": 0,  "y": 6, "w": 48, "h": 5}),
        ("visualization", "alice-viz-incident-header", {"x": 0,  "y": 11, "w": 48, "h": 6}),
        ("visualization", "alice-viz-open-incidents",  {"x": 0,  "y": 17, "w": 48, "h": 6}),
        ("visualization", "alice-viz-incidents",       {"x": 0,  "y": 23, "w": 48, "h": 22}),
        ("visualization", "alice-viz-detect-header",   {"x": 0,  "y": 45, "w": 48, "h": 5}),
        ("visualization", "alice-viz-health-header",   {"x": 0,  "y": 50, "w": 48, "h": 3}),
        ("visualization", "alice-viz-cluster-status",  {"x": 0,  "y": 53, "w": 8, "h": 8}, live),
        ("visualization", "alice-viz-unassigned",      {"x": 8,  "y": 53, "w": 8, "h": 8}, live),
        ("visualization", "alice-viz-osd-status",      {"x": 16, "y": 53, "w": 8, "h": 8}, live),
        ("visualization", "alice-viz-fb-status",       {"x": 24, "y": 53, "w": 14, "h": 8}, live),
        ("visualization", "alice-viz-health-links",    {"x": 38, "y": 53, "w": 10, "h": 8}),
        ("visualization", "alice-viz-ingest-rate",     {"x": 0,  "y": 61, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-size",      {"x": 24, "y": 61, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-throughput",   {"x": 0,  "y": 73, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-trouble",      {"x": 24, "y": 73, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-health",    {"x": 0,  "y": 85, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-node-heap",       {"x": 16, "y": 85, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-osd-perf",        {"x": 32, "y": 85, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-log-header",      {"x": 0,  "y": 97, "w": 48, "h": 4}),
        ("visualization", "alice-viz-total",           {"x": 0,  "y": 101, "w": 16, "h": 8}),
        ("visualization", "alice-viz-errwarn",         {"x": 16, "y": 101, "w": 16, "h": 8}),
        ("visualization", "alice-viz-bysource",        {"x": 32, "y": 101, "w": 16, "h": 8}),
        ("visualization", "alice-viz-sev-time",        {"x": 0,  "y": 109, "w": 48, "h": 12}),
        ("visualization", "alice-viz-top-hosts",       {"x": 0,  "y": 121, "w": 24, "h": 12}),
        ("visualization", "alice-viz-top-systems",     {"x": 24, "y": 121, "w": 24, "h": 12}),
        ("search",        "alice-search-errwarn",      {"x": 0,  "y": 133, "w": 48, "h": 16}),
    ]
    objects.append(dashboard(panels))
    return objects


def main():
    for obj in build():
        sys.stdout.write(json.dumps(obj) + "\n")


if __name__ == "__main__":
    main()
