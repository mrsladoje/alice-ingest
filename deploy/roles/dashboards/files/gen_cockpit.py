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
INCIDENTS_ID = "alice-incidents"
INCIDENTS_TITLE = "alice-incidents"
SIGNALS_ID = "alice-signals"
SIGNALS_TITLE = "alice-signals"
INCIDENT_HISTORY_LOOKBACK = "7d"
GROUP_CARDS = 20
GROUP_ENTITY_SAMPLES = 3
GROUP_CHILD_ROWS = 25
TIME_FIELD = "@timestamp"
PANEL_VERSION = "3.7.0"

LOG_TIME_FROM = "now-1y"
HEALTH_RANGE = {"from": "now-1h", "to": "now"}
# The fleet counters answer "how many collectors are in this state now", so they
# get a window just wider than the heartbeat grace instead of the health hour.
# Over an hour a collector that flapped once would be counted down for the rest
# of it, which reads as a fleet in trouble when the fleet is fine.
FLEET_NOW_RANGE = {"from": "now-3m", "to": "now"}
REFRESH_MS = 30000
STALE_SECONDS = 90

ERRWARN_Q = "severity_norm:(error or fatal or warning)"

FONT_STACK = ("Inter UI, -apple-system, BlinkMacSystemFont, 'Segoe UI', "
              "Helvetica, Arial, sans-serif")

LIVE_LANE_PANEL_ID = "alice-live-lane"
LIVE_LANE_MD = ("[▶ LIVE LOG LANE](/live/)\n\n"
                "*newest records, no query*\n"
                "*opens in a new tab*")
LIVE_LANE_STYLE = [
    ("", "text-align: center; font-family: %s" % FONT_STACK),
    ("p", "margin: 0"),
    ("a", "display: block; padding: 11px 6px; border: 1px solid #006BB4; "
          "border-radius: 4px; background: rgba(0,107,180,0.08); "
          "color: #006BB4; font-size: 13px; font-weight: bold; "
          "letter-spacing: 0.4px; text-decoration: none"),
    ("a:hover, a:focus", "background: rgba(0,107,180,0.16); "
                         "text-decoration: none"),
    ("em", "display: block; margin-top: 7px; color: #69707D; "
           "font-size: 11px; font-style: normal; line-height: 1.4"),
]

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
            "&_q=(query:(language:kuery,query:\\'group_id:\"' + "
            "datum.key + '\"\\'))'")


def _details_href(search_id):
    return ("'/app/data-explorer/discover#/view/" + search_id +
            "?_g=(time:(from:now-" + INCIDENT_HISTORY_LOOKBACK + ",to:now))"
            "&_q=(query:(language:kuery,query:\\'group_id:\"' + "
            "datum.key + '\"\\'))'")


def _group_entity_list():
    """Join the sampled entity keys without pluck(), which needs Vega 5.19."""
    parts = []
    for index in range(GROUP_ENTITY_SAMPLES):
        separator = "', ' + " if index else ""
        parts.append(
            f"(length(datum.entities.buckets) > {index} ? {separator}"
            f"datum.entities.buckets[{index}].key : '')")
    return " + ".join(parts)


def _kid_href(search_id):
    """Details for one entity inside a card: the group plus that entity."""
    return ("'/app/data-explorer/discover#/view/" + search_id +
            "?_g=(time:(from:now-" + INCIDENT_HISTORY_LOOKBACK + ",to:now))"
            "&_q=(query:(language:kuery,query:\\'group_id:\"' + "
            "datum.key + '\" and entity_id:\"' + datum.kid.key + '\"\\'))'")


# Every mark on a card hangs off this one expression. A card below the open
# one is pushed down by that card's child block, so unfolding a card moves the
# rest of the board instead of drawing over it. One card opens at a time, so
# the offset is a single signal rather than a running total per row.
CARD_TOP = ("datum.row * (cardHeight + gap) + "
            "(datum.row > expandedRow ? expandedBlock : 0) - scroll")
KID_TOP = (CARD_TOP + " + cardHeight + childPad + "
           "(datum.kidRank - 1) * childHeight")


def _episode_card_text(name, as_field, expr, dy, fill, size, bold=False,
                       limit="cardWidth - 32"):
    mark = {"type": "text", "name": name, "from": {"data": "episodes"},
            "encode": {"update": {
                "x": {"value": 30},
                "y": {"signal": CARD_TOP + " + %d" % dy},
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
    button_opacity = {"signal": "datum.key ? 1 : 0"}
    rect = {"type": "rect", "from": {"data": "episodes"},
            "encode": {"update": {
                "x": {"signal": x_signal},
                "y": {"signal": CARD_TOP + " + 11"},
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
                "y": {"signal": CARD_TOP + " + 21"},
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


def _chevron_marks():
    """The expand affordance, and the marks that answer a click on the card.

    Vega dispatches a click to the topmost mark under the pointer, so the
    title, meta and diagnosis text all need their own handler entry or a
    click that lands on a word does nothing. They share one update
    expression because they share one datum — the group.
    """
    return [
        {"type": "text", "name": "cardChevron", "from": {"data": "episodes"},
         "encode": {"update": {
             "x": {"value": 14},
             "y": {"signal": CARD_TOP + " + 22"},
             "text": {"signal": "datum.isOpen ? '▾' : '▸'"},
             "align": {"value": "center"},
             "baseline": {"value": "middle"},
             "fill": {"value": "#006BB4"},
             "font": {"value": FONT_STACK},
             "fontSize": {"value": 13},
             "cursor": {"value": "pointer"},
             "opacity": {"signal": "datum.expandable ? 1 : 0"},
         }}},
    ]


def _kid_marks():
    row_fill = {"signal": "datum.kidState == 'RECOVERING' ? '#017D73' : "
                          "datum.kidState == 'STALE' ? '#98A2B3' : '#343741'"}
    return [
        {"type": "rect", "name": "kidRow", "from": {"data": "kids"},
         "encode": {"update": {
             "x": {"value": 30},
             "y": {"signal": KID_TOP},
             "width": {"signal": "cardWidth - 46"},
             "height": {"signal": "childHeight - 2"},
             "cornerRadius": {"value": 3},
             "fill": {"value": "#F5F7FA"},
             "href": {"field": "kidUrl"},
             "cursor": {"value": "pointer"},
         }}},
        {"type": "rect", "from": {"data": "kids"},
         "encode": {"update": {
             "x": {"value": 30},
             "y": {"signal": KID_TOP},
             "width": {"value": 3},
             "height": {"signal": "childHeight - 2"},
             "fill": {"field": "kidAccent"},
         }}},
        {"type": "text", "from": {"data": "kids"},
         "encode": {"update": {
             "x": {"value": 42},
             "y": {"signal": KID_TOP + " + childHeight / 2 - 1"},
             "text": {"field": "kidText"},
             "baseline": {"value": "middle"},
             "fill": row_fill,
             "font": {"value": FONT_STACK},
             "fontSize": {"value": 10},
             "limit": {"signal": "cardWidth - 70"},
             "href": {"field": "kidUrl"},
             "cursor": {"value": "pointer"},
         }}},
        {"type": "text", "from": {"data": "kidsMore"},
         "encode": {"update": {
             "x": {"value": 42},
             "y": {"signal":
                   CARD_TOP + " + cardHeight + childPad + "
                   "length(datum.kids) * childHeight + childHeight / 2 - 1"},
             "text": {"signal":
                      "'+' + (datum.entity_total.value - "
                      "length(datum.kids)) + ' more — open DETAILS for the "
                      "complete list'"},
             "baseline": {"value": "middle"},
             "fill": {"value": "#69707D"},
             "font": {"value": FONT_STACK},
             "fontSize": {"value": 10},
             "fontStyle": {"value": "italic"},
             "limit": {"signal": "cardWidth - 70"},
         }}},
    ]


def incident_episode_board(vid, title):
    spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "autosize": {"type": "fit", "contains": "padding"},
        "config": {"kibana": {"renderer": "svg"}},
        "padding": 4,
        "signals": [
            {"name": "gap", "value": 10},
            {"name": "cardHeight", "value": 68},
            {"name": "childHeight", "value": 18},
            {"name": "childPad", "value": 6},
            {"name": "cardWidth", "update": "width"},
            {"name": "cardRows", "update": "length(data('episodes'))"},
            # The open card is held as its group_id, never as the datum: the
            # panel refetches on every dashboard refresh, and a frozen datum
            # would keep laying the board out from counts that no longer
            # exist. A key survives the refetch and matches the new rows.
            {"name": "expandedKey", "value": "",
             "on": [{"events": [{"markname": "cardChevron", "type": "click"},
                                {"markname": "cardHit", "type": "click"},
                                {"markname": "cardTitle", "type": "click"},
                                {"markname": "cardMeta", "type": "click"},
                                {"markname": "cardDiag", "type": "click"}],
                     "update": "datum.expandable ? "
                               "(expandedKey == datum.key ? '' : datum.key) "
                               ": expandedKey"}]},
            {"name": "expandedRow",
             "update": "length(data('openCards')) ? "
                       "data('openCards')[0].row : -1"},
            {"name": "expandedBlock",
             "update": "length(data('openCards')) ? "
                       "data('openCards')[0].kidBlock : 0"},
            {"name": "contentHeight",
             "update": "cardRows * cardHeight + "
                       "max(0, cardRows - 1) * gap + expandedBlock"},
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
                    "size": 0,
                    "query": {"term": {"state": "firing"}},
                    "aggs": {"groups": {
                        "terms": {"field": "group_id",
                                  "size": GROUP_CARDS,
                                  "order": {"last": "desc"}},
                        "aggs": {
                            "last": {"max": {"field": "last_seen"}},
                            "opened": {"min": {"field": "opened_at"}},
                            "members": {"sum": {"field": "member_count"}},
                            "worst": {"max": {"field": "worst_grade"}},
                            "conf": {"max": {"field": "latest_confidence"}},
                            "entity_total": {
                                "cardinality": {"field": "entity_id"}},
                            "entities": {
                                "terms": {"field": "entity_id",
                                          "size": GROUP_ENTITY_SAMPLES,
                                          "order": {"_key": "asc"}}},
                            # The card's children. Fetched with the parents in
                            # this one request, so expanding a card is layout,
                            # not a second round trip. Metric sub-aggregations
                            # only — a top_hits per child would multiply into
                            # hundreds of stored hits for one board refresh.
                            "children": {
                                "terms": {"field": "entity_id",
                                          "size": GROUP_CHILD_ROWS,
                                          "order": {"kid_last": "desc"}},
                                "aggs": {
                                    "kid_last": {
                                        "max": {"field": "last_seen"}},
                                    "kid_grade": {
                                        "max": {"field": "worst_grade"}},
                                    "kid_open": {"filter": {"term": {
                                        "episode_state": "OPEN"}}},
                                    "kid_stale": {"filter": {"term": {
                                        "episode_state": "STALE"}}},
                                    "kid_page": {"filter": {"term": {
                                        "severity": "page"}}},
                                }},
                            "pages": {
                                "filter": {"term": {"severity": "page"}}},
                            "opens": {
                                "filter": {"term": {"episode_state": "OPEN"}}},
                            "stales": {
                                "filter": {"term": {
                                    "episode_state": "STALE"}}},
                            "top": {"top_hits": {
                                "size": 1,
                                "sort": [{"severity": {"order": "asc"}},
                                         {"last_seen": {"order": "desc"}}],
                                "_source": ["title", "diagnosis", "affected",
                                            "severity", "episode_state",
                                            "entity_kind", "alertname",
                                            "class"]}},
                        },
                    }},
                },
            },
            "format": {"property": "aggregations.groups.buckets"},
            "transform": [
                {"type": "formula", "as": "src",
                 "expr": "datum.top.hits.hits[0]._source"},
                {"type": "formula", "as": "severityRank",
                 "expr": "datum.pages.doc_count > 0 ? 0 : 1"},
                # Never name this "last": a formula that shadows the
                # aggregation object leaves datum.last.value undefined for
                # every later expression, and the link windows silently
                # become NaN.
                {"type": "formula", "as": "lastMs",
                 "expr": "datum.last.value"},
                {"type": "collect", "sort": {
                    "field": ["severityRank", "lastMs"],
                    "order": ["ascending", "descending"]}},
                {"type": "window", "ops": ["row_number"], "as": ["rank"]},
                {"type": "formula", "as": "row",
                 "expr": "datum.rank - 1"},
                {"type": "formula", "as": "groupState",
                 "expr": "datum.opens.doc_count > 0 ? 'OPEN' : "
                         "datum.stales.doc_count > 0 ? 'STALE' : "
                         "'RECOVERING'"},
                {"type": "formula", "as": "accent",
                 "expr": "datum.pages.doc_count > 0 ? '#BD271E' : "
                         "datum.groupState == 'STALE' ? '#98A2B3' : "
                         "datum.groupState == 'RECOVERING' ? '#017D73' : "
                         "'#F5A700'"},
                {"type": "formula", "as": "titleText",
                 "expr": "datum.src.title || datum.src.alertname || "
                         "'Untitled episode'"},
                {"type": "formula", "as": "kindLabel",
                 "expr": "datum.src.entity_kind == 'epn' ? 'EPNs' : "
                         "datum.src.entity_kind == 'collector' ? "
                         "'collectors' : "
                         "datum.src.entity_kind == 'os_node' ? "
                         "'OpenSearch nodes' : "
                         "datum.src.entity_kind == 'monitor' ? 'monitors' : "
                         "'entities'"},
                {"type": "formula", "as": "entityList",
                 "expr": _group_entity_list()},
                {"type": "formula", "as": "affectedText",
                 "expr": "datum.entity_total.value <= 1 ? "
                         "(datum.src.affected || '') : "
                         "(datum.entity_total.value + ' ' + datum.kindLabel + "
                         "': ' + datum.entityList + "
                         f"(datum.entity_total.value > {GROUP_ENTITY_SAMPLES}"
                         " ? ' +' + (datum.entity_total.value - "
                         f"{GROUP_ENTITY_SAMPLES}) + ' more' : ''))"},
                {"type": "formula", "as": "mixedText",
                 "expr": "datum.pages.doc_count > 0 && "
                         "datum.pages.doc_count < datum.doc_count ? "
                         "'  ·  ' + datum.pages.doc_count + ' page' : ''"},
                {"type": "formula", "as": "meta",
                 "expr": "upper(datum.pages.doc_count > 0 ? 'page' : "
                         "(datum.src.severity || '?')) + datum.mixedText + "
                         "'  ·  ' + datum.groupState + "
                         "(datum.affectedText ? '  ·  ' + "
                         "datum.affectedText : '') + '  ·  ' + "
                         "datum.doc_count + "
                         "(datum.doc_count == 1 ? ' episode / ' "
                         ": ' episodes / ') + "
                         "(datum.members.value || 0) + "
                         "' signals  ·  since ' + "
                         "timeFormat(toDate(datum.opened.value), "
                         "'%b %d %H:%M')"},
                {"type": "formula", "as": "score",
                 "expr": "datum.worst.value == null ? '' : "
                         "'  ·  worst grade ' + "
                         "format(datum.worst.value, '.3f') + "
                         "' / confidence ' + "
                         "format(datum.conf.value || 0, '.3f')"},
                {"type": "formula", "as": "diagText",
                 "expr": "datum.src.diagnosis || ''"},
                {"type": "formula", "as": "winFrom",
                 "expr": "utcFormat(toDate(datum.opened.value - 300000), "
                         "'%Y-%m-%dT%H:%M:%S.%LZ')"},
                {"type": "formula", "as": "winTo",
                 "expr": "utcFormat(toDate(datum.last.value + 300000), "
                         "'%Y-%m-%dT%H:%M:%S.%LZ')"},
                {"type": "formula", "as": "signalsUrl",
                 "expr": _signals_href("alice-search-signals")},
                {"type": "formula", "as": "detailsUrl",
                 "expr": _details_href("alice-search-incident-history")},
                {"type": "formula", "as": "kids",
                 "expr": "datum.children.buckets"},
                # A one-entity card has nothing to unfold: its single child
                # row would just repeat the card above it.
                {"type": "formula", "as": "expandable",
                 "expr": "length(datum.kids) > 1"},
                {"type": "formula", "as": "isOpen",
                 "expr": "datum.expandable && datum.key == expandedKey"},
                {"type": "formula", "as": "kidRows",
                 "expr": "datum.isOpen ? length(datum.kids) + "
                         "(datum.entity_total.value > length(datum.kids) "
                         "? 1 : 0) : 0"},
                {"type": "formula", "as": "kidBlock",
                 "expr": "datum.kidRows > 0 ? "
                         "childPad * 2 + datum.kidRows * childHeight : 0"},
            ],
        }, {
            "name": "openCards",
            "source": "episodes",
            "transform": [{"type": "filter", "expr": "datum.isOpen"}],
        }, {
            "name": "kids",
            "source": "episodes",
            "transform": [
                {"type": "filter", "expr": "datum.isOpen"},
                {"type": "flatten", "fields": ["kids"], "as": ["kid"]},
                {"type": "window", "ops": ["row_number"], "as": ["kidRank"],
                 "groupby": ["key"]},
                {"type": "formula", "as": "kidState",
                 "expr": "datum.kid.kid_open.doc_count > 0 ? 'OPEN' : "
                         "datum.kid.kid_stale.doc_count > 0 ? 'STALE' : "
                         "'RECOVERING'"},
                {"type": "formula", "as": "kidAccent",
                 "expr": "datum.kid.kid_page.doc_count > 0 ? '#BD271E' : "
                         "datum.kidState == 'STALE' ? '#98A2B3' : "
                         "datum.kidState == 'RECOVERING' ? '#017D73' : "
                         "'#F5A700'"},
                {"type": "formula", "as": "kidText",
                 "expr": "datum.kid.key + '  ·  ' + datum.kidState + "
                         "(datum.kid.kid_page.doc_count > 0 ? "
                         "'  ·  PAGE' : '') + "
                         "(datum.kid.kid_grade.value == null ? '' : "
                         "'  ·  grade ' + "
                         "format(datum.kid.kid_grade.value, '.3f')) + "
                         "'  ·  last seen ' + "
                         "timeFormat(toDate(datum.kid.kid_last.value), "
                         "'%b %d %H:%M')"},
                {"type": "formula", "as": "kidUrl",
                 "expr": _kid_href("alice-search-incident-history")},
            ],
        }, {
            "name": "kidsMore",
            "source": "episodes",
            "transform": [
                {"type": "filter",
                 "expr": "datum.isOpen && "
                         "datum.entity_total.value > length(datum.kids)"},
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
                 {"type": "rect", "name": "cardHit",
                  "from": {"data": "episodes"},
                  "encode": {"update": {
                      "x": {"value": 0},
                      "y": {"signal": CARD_TOP},
                      "width": {"signal": "cardWidth"},
                      "height": {"signal": "cardHeight"},
                      "fill": {"value": "#FFFFFF"},
                      "stroke": {"value": "#D3DAE6"},
                      "cornerRadius": {"value": 4},
                      "cursor": {"signal":
                                 "datum.expandable ? 'pointer' : 'default'"},
                  }}},
                 {"type": "rect", "from": {"data": "episodes"},
                  "encode": {"update": {
                      "x": {"value": 0},
                      "y": {"signal": CARD_TOP},
                      "width": {"value": 4},
                      "height": {"signal": "cardHeight"},
                      "fill": {"field": "accent"},
                      "cornerRadius": {"value": 4},
                  }}},
                 # The open card's children are drawn in a tinted well that
                 # runs from the card's bottom edge to the last child row, so
                 # a long list still reads as belonging to one card.
                 {"type": "rect", "from": {"data": "openCards"},
                  "encode": {"update": {
                      "x": {"value": 12},
                      "y": {"signal": CARD_TOP + " + cardHeight"},
                      "width": {"signal": "cardWidth - 12"},
                      "height": {"signal": "datum.kidBlock"},
                      "fill": {"value": "#FAFBFD"},
                      "stroke": {"value": "#D3DAE6"},
                      "cornerRadius": {"value": 4},
                  }}},
             ] + _chevron_marks()
               + _episode_card_text("cardTitle", "titleText", None, 18,
                                    "#343741", 13, bold=True,
                                    limit="cardWidth - 204")
               + _episode_card_text("cardMeta", None,
                                    "datum.meta + datum.score", 37,
                                    "#69707D", 10)
               + _episode_card_text("cardDiag", "diagText", None, 55,
                                    "#343741", 11)
               + _kid_marks()
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


def fleet_counter(vid, title, query, font_size=48):
    state = {
        "title": title,
        "type": "metric",
        "aggs": [
            {"id": "1", "enabled": True, "type": "cardinality",
             "schema": "metric",
             "params": {"field": "collector_id", "customLabel": "collectors"}}
        ],
        "params": _metric_params(font_size),
    }
    return viz(vid, title, state, query=dql(query), pattern=METRICS_ID)


def worst_table(vid, title, columns, query, order_by, size=10,
                bucket_field="collector_id"):
    aggs = []
    for i, (agg_type, field, label) in enumerate(columns, start=1):
        aggs.append({"id": str(i), "enabled": True, "type": agg_type,
                     "schema": "metric",
                     "params": {"field": field, "customLabel": label}})
    aggs.append({"id": str(len(columns) + 1), "enabled": True,
                 "type": "terms", "schema": "bucket",
                 "params": {"field": bucket_field, "orderBy": str(order_by),
                            "order": "desc", "size": size,
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


def distribution_timechart(vid, title, field, query, y_title="Count",
                           kind="line"):
    labels = ["fleet median", "95th percentile", "worst"]
    aggs = [
        {"id": "1", "enabled": True, "type": "percentiles", "schema": "metric",
         "params": {"field": field, "percents": [50],
                    "customLabel": labels[0]}},
        {"id": "2", "enabled": True, "type": "percentiles", "schema": "metric",
         "params": {"field": field, "percents": [95],
                    "customLabel": labels[1]}},
        {"id": "3", "enabled": True, "type": "max", "schema": "metric",
         "params": {"field": field, "customLabel": labels[2]}},
        {"id": "4", "enabled": True, "type": "date_histogram",
         "schema": "segment",
         "params": {"field": TIME_FIELD, "interval": "auto",
                    "min_doc_count": 0, "drop_partials": False}},
    ]
    params = _bar_params(kind, "normal", "bottom", "left", y_title)
    params["seriesParams"] = _series("normal", kind=kind, labels=labels)
    state = {"title": title, "type": kind, "aggs": aggs, "params": params}
    return viz(vid, title, state, query=dql(query), pattern=METRICS_ID)


def collector_picker(vid, title):
    ref_name = "control_0_index_pattern"
    state = {
        "title": title,
        "type": "input_control_vis",
        "aggs": [],
        "params": {
            "controls": [{
                "id": "collector-pin",
                "type": "list",
                "label": "Pin collectors",
                "fieldName": "collector_id",
                "indexPattern": ref_name,
                "parent": "",
                "options": {
                    "type": "terms",
                    "multiselect": True,
                    "dynamicOptions": True,
                    "size": 100,
                    "order": "desc",
                },
            }],
            "updateFiltersOnChange": True,
            "useTimeFilter": True,
            "pinFilters": False,
        },
    }
    return {
        "type": "visualization",
        "id": vid,
        "attributes": {
            "title": title,
            "visState": json.dumps(state),
            "uiStateJSON": "{}",
            "description": "",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(
                    search_source(index_ref=False))
            },
        },
        "references": [
            {"name": ref_name, "type": "index-pattern", "id": METRICS_ID}
        ],
    }


def markdown(vid, title, md):
    state = {
        "title": title,
        "type": "markdown",
        "params": {"fontSize": 12, "openLinksInNewTab": True, "markdown": md},
        "aggs": [],
    }
    return viz(vid, title, state, index_ref_on=False)


def _button_style(panel_id, rules):
    root = "#markdown-%s" % panel_id
    less_src, css = [], []
    for sel, decls in rules:
        if sel:
            less_src.append("%s { %s; }" % (sel, decls))
            css.append("%s{%s}" % (
                ",".join(root + " " + s.strip() for s in sel.split(",")),
                decls))
        else:
            less_src.append("%s;" % decls)
            css.append("%s{%s}" % (root, decls))
    return "\n".join(less_src), "".join(css)


def markdown_button(vid, title, panel_id, md, rules):
    less_src, css = _button_style(panel_id, rules)
    params = {
        "id": panel_id,
        "type": "markdown",
        "series": [{
            "id": panel_id + "-series",
            "axis_position": "right",
            "chart_type": "line",
            "color": "#68BC00",
            "formatter": "number",
            "split_mode": "everything",
            "stacked": "none",
            "fill": 0.5,
            "line_width": 1,
            "point_size": 1,
            "separate_axis": 0,
            "label": "",
            "hidden": False,
            "type": "timeseries",
            "metrics": [{"id": panel_id + "-count", "type": "count"}],
        }],
        "index_pattern": METRICS_TITLE,
        "time_field": TIME_FIELD,
        "interval": "auto",
        "axis_position": "left",
        "axis_formatter": "number",
        "axis_scale": "normal",
        "show_grid": 0,
        "show_legend": 0,
        "filter": {"language": "kuery", "query": ""},
        "ignore_global_filter": 1,
        "default_index_pattern": METRICS_TITLE,
        "default_timefield": TIME_FIELD,
        "markdown": md,
        "markdown_less": less_src,
        "markdown_css": css,
        "markdown_openLinksInNewTab": 1,
        "markdown_vertical_align": "middle",
        "markdown_scrollbars": 0,
    }
    state = {"title": title, "type": "metrics", "aggs": [], "params": params}
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
            "title": "Maintainer Cockpit",
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
            "alice-search-signals", "Signals — every raw row behind an incident",
            "One lossless row per source signal. Grouping never destroys "
            "these: an incident references them, it does not replace them. "
            "The card's SIGNALS button opens this filtered to its group_id "
            "over the group's own window, so every affected entity is here at "
            "once — click a value in the entity_id field list to narrow to "
            "one EPN or one collector.",
            "",
            columns=["alertname", "state", "severity", "entity_kind",
                     "entity_id", "collector_id", "class", "incident_id"],
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
            "Episode history — every episode behind one card",
            "One row per episode, newest first. The card's DETAILS button "
            "opens this filtered to its group_id, so one row is one affected "
            "entity and repeated rows for the same entity are how often the "
            "problem came back. Sort or filter on entity_id to read one EPN.",
            "",
            columns=["severity", "title", "affected", "entity_id",
                     "episode_state", "member_count", "opened_at",
                     "last_seen", "resolved_at", "operator_action",
                     "episode_id"],
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
        "## \U0001F6F0️ Maintainer Cockpit\n"
        "This dashboard answers **is the log pipeline healthy**, which is a "
        "maintainer's question. A shifter asks what the experiment is saying "
        "instead, and reads the **[live log →](/live/)** page for that.\n\n"
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
    fleet_detail_md = (
        "### \U0001F5A5 The collector fleet — live, last hour\n"
        "**No panel here grows with the fleet.** Four counters, two ten-row "
        "tables and distribution lines read the same at two collectors and at "
        "two hundred, on a desktop or on a phone. The counters read the last "
        "**three minutes**, not the hour the panels below use, so they answer "
        "*how many collectors are in this state now*; a machine that flapped "
        "inside those three minutes can still appear in two of them. To follow "
        "named machines instead of the "
        "distribution, pin them with the picker — it filters `collector_id` "
        "as a field, which is correct here because `cockpit-metrics` is not "
        "partitioned per machine. **Selecting a machine's logs is a different "
        "act and must filter `_index`, never the `node` field.**"
    )
    log_detail_md = (
        "### Raw log evidence — secondary\n"
        "Use this section after an episode tells you where to look. These "
        "panels follow the global time picker and intentionally do not count "
        "as separate incidents."
    )
    objects += [
        markdown("alice-viz-header", "Cockpit header", header_md),
        markdown_button("alice-viz-live-lane", "Live log lane",
                        LIVE_LANE_PANEL_ID, LIVE_LANE_MD, LIVE_LANE_STYLE),
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
        markdown("alice-viz-fleet-header", "Collector fleet header",
                 fleet_detail_md),
        markdown("alice-viz-log-header", "Log evidence header",
                 log_detail_md),
        latest_metric("alice-viz-cluster-status", "Cluster status",
                      "cluster_status", "kind:cluster", label="status"),
        latest_metric("alice-viz-unassigned", "Unassigned shards",
                      "unassigned_shards", "kind:cluster", aggregate="max",
                      label="unassigned"),
        latest_metric("alice-viz-osd-status", "Dashboards health",
                      "osd_state", "kind:osd", label="state"),
        fleet_counter("alice-viz-fleet-total", "Collectors",
                      "kind:fluentbit"),
        fleet_counter("alice-viz-fleet-healthy", "Healthy",
                      "kind:fluentbit and fb_up:1 and fb_healthy:1"),
        fleet_counter("alice-viz-fleet-degraded", "Degraded",
                      "kind:fluentbit and fb_up:1 and fb_healthy:0"),
        fleet_counter("alice-viz-fleet-down", "Down",
                      "kind:fluentbit and fb_up:0"),
        worst_table("alice-viz-fb-unhealthy", "Not healthy now",
                    [("min", "fb_up", "up (1=yes)"),
                     ("min", "fb_healthy", "healthy (1=yes)"),
                     ("sum", "output_dropped_delta", "dropped"),
                     ("sum", "output_errors_delta", "errors")],
                    "kind:fluentbit and (fb_up:0 or fb_healthy:0)",
                    order_by=3),
        worst_table("alice-viz-fb-worst", "Worst ten by records lost",
                    [("sum", "output_dropped_delta", "dropped"),
                     ("sum", "output_retries_failed_delta", "failed retries"),
                     ("sum", "output_errors_delta", "errors"),
                     ("min", "fb_up", "up (1=yes)")],
                    "kind:fluentbit", order_by=1),
        collector_picker("alice-viz-fb-picker", "Pin collectors"),
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
        distribution_timechart("alice-viz-fb-throughput",
                               "Records shipped — fleet distribution",
                               "output_records_delta", "kind:fluentbit",
                               y_title="records / 30 s"),
        distribution_timechart("alice-viz-fb-trouble",
                               "Records lost — fleet distribution",
                               "output_dropped_delta", "kind:fluentbit",
                               y_title="dropped / 30 s"),
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
                 "The cockpit shows **grouped episodes**, not every alert "
                 "evaluation or anomalous time window. One card is one rule "
                 "and scope, one episode inside it is one entity, and "
                 "`member_count` says how many raw signals that episode "
                 "folded in. "
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
                 "One card is **one notification**: every open episode of the "
                 "same rule and scope, grouped exactly as Alertmanager groups "
                 "them. The card names how many entities are affected and "
                 "samples the first three, so 40 EPNs breaching one rule is "
                 "one card, not 40. Per-entity state is still its own episode "
                 "document — the count on the card says how many were folded "
                 "in.\n\n"
                 "**Click a card to unfold it.** The ▸ chevron opens the "
                 "affected entities in place, newest first, each with its own "
                 "state, worst grade and last-seen time. One card is open at "
                 "a time, and a child row opens that single entity's history. "
                 f"The list stops at {GROUP_CHILD_ROWS} entities; past that "
                 "the card says how many more there are and DETAILS has the "
                 "complete list.\n\n"
                 "**SIGNALS** opens every raw row behind the whole group; "
                 "filter the `entity_id` field there to read one EPN. "
                 "**DETAILS** opens one row per affected entity, each with "
                 "its own state and history. Both replace this tab and the "
                 "browser Back button returns here, because a drawn button "
                 "cannot open a tab; every text link on this dashboard opens "
                 "its own tab instead. PAGE is urgent; WARNING needs "
                 "attention; STALE means the model stopped evaluating and is "
                 "not evidence of recovery. The board scrolls and ignores the "
                 "global time picker, so an old-but-open episode cannot "
                 "disappear."),
        incident_summary_strip("alice-viz-open-incidents",
                               "Episode summary"),
        incident_episode_board("alice-viz-incidents",
                               "Open episodes — deduplicated and actionable"),
        active_alert_metric("alice-viz-active-alerts", "Active alerts"),
        alerts_table("alice-viz-alerts", "Active alerts by rule"),
    ]

    live = {"timeRange": HEALTH_RANGE}
    now = {"timeRange": FLEET_NOW_RANGE}
    panels = [
        ("visualization", "alice-viz-header",          {"x": 0,  "y": 0, "w": 39, "h": 6}),
        ("visualization", "alice-viz-live-lane",       {"x": 39, "y": 0, "w": 9,  "h": 6}, live),
        ("visualization", "alice-viz-status-strip",    {"x": 0,  "y": 6, "w": 48, "h": 5}),
        ("visualization", "alice-viz-incident-header", {"x": 0,  "y": 11, "w": 48, "h": 8}),
        ("visualization", "alice-viz-open-incidents",  {"x": 0,  "y": 19, "w": 48, "h": 6}),
        ("visualization", "alice-viz-incidents",       {"x": 0,  "y": 25, "w": 48, "h": 22}),
        ("visualization", "alice-viz-detect-header",   {"x": 0,  "y": 47, "w": 48, "h": 5}),
        ("visualization", "alice-viz-health-header",   {"x": 0,  "y": 52, "w": 48, "h": 3}),
        ("visualization", "alice-viz-cluster-status",  {"x": 0,  "y": 55, "w": 12, "h": 8}, live),
        ("visualization", "alice-viz-unassigned",      {"x": 12, "y": 55, "w": 12, "h": 8}, live),
        ("visualization", "alice-viz-osd-status",      {"x": 24, "y": 55, "w": 12, "h": 8}, live),
        ("visualization", "alice-viz-health-links",    {"x": 36, "y": 55, "w": 12, "h": 8}),
        ("visualization", "alice-viz-fleet-header",    {"x": 0,  "y": 63, "w": 48, "h": 4}),
        ("visualization", "alice-viz-fleet-total",     {"x": 0,  "y": 67, "w": 12, "h": 7}, now),
        ("visualization", "alice-viz-fleet-healthy",   {"x": 12, "y": 67, "w": 12, "h": 7}, now),
        ("visualization", "alice-viz-fleet-degraded",  {"x": 24, "y": 67, "w": 12, "h": 7}, now),
        ("visualization", "alice-viz-fleet-down",      {"x": 36, "y": 67, "w": 12, "h": 7}, now),
        ("visualization", "alice-viz-fb-unhealthy",    {"x": 0,  "y": 74, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-worst",        {"x": 24, "y": 74, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-fb-picker",       {"x": 0,  "y": 86, "w": 12, "h": 8}, live),
        ("visualization", "alice-viz-fb-throughput",   {"x": 12, "y": 86, "w": 18, "h": 12}, live),
        ("visualization", "alice-viz-fb-trouble",      {"x": 30, "y": 86, "w": 18, "h": 12}, live),
        ("visualization", "alice-viz-ingest-rate",     {"x": 0,  "y": 98, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-size",      {"x": 24, "y": 98, "w": 24, "h": 12}, live),
        ("visualization", "alice-viz-index-health",    {"x": 0,  "y": 110, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-node-heap",       {"x": 16, "y": 110, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-osd-perf",        {"x": 32, "y": 110, "w": 16, "h": 12}, live),
        ("visualization", "alice-viz-log-header",      {"x": 0,  "y": 122, "w": 48, "h": 4}),
        ("visualization", "alice-viz-total",           {"x": 0,  "y": 126, "w": 16, "h": 8}),
        ("visualization", "alice-viz-errwarn",         {"x": 16, "y": 126, "w": 16, "h": 8}),
        ("visualization", "alice-viz-bysource",        {"x": 32, "y": 126, "w": 16, "h": 8}),
        ("visualization", "alice-viz-sev-time",        {"x": 0,  "y": 134, "w": 48, "h": 12}),
        ("visualization", "alice-viz-top-hosts",       {"x": 0,  "y": 146, "w": 24, "h": 12}),
        ("visualization", "alice-viz-top-systems",     {"x": 24, "y": 146, "w": 24, "h": 12}),
    ]
    objects.append(dashboard(panels))
    return objects


def main():
    for obj in build():
        sys.stdout.write(json.dumps(obj) + "\n")


if __name__ == "__main__":
    main()
