import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os_cursor  # noqa: E402

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
SIGNALS_INDEX = os.environ.get("SIGNALS_INDEX", "alice-signals")
INCIDENTS_INDEX = os.environ.get("INCIDENTS_INDEX", "alice-incidents")
NOTIFICATIONS_INDEX = os.environ.get(
    "NOTIFICATIONS_INDEX", "alice-notifications")
ALERTS_CURRENT = ".opendistro-alerting-alerts"
ALERTS_HISTORY = ".opendistro-alerting-alert-history-*"
AD_RESULTS = ".opendistro-anomaly-results*"
GRADE_FLOOR = float(os.environ.get("GRADE_FLOOR", "0.5"))

SCENARIO = os.environ.get("SCENARIO", "unnamed")
START_MS = int(os.environ.get("START_MS", "0"))
END_MS = int(os.environ.get("END_MS", str(int(time.time() * 1000))))
INDEPENDENT_ENTITY = os.environ.get("INDEPENDENT_ENTITY", "")
REQUIRE_INDEPENDENT_RECALL = os.environ.get(
    "REQUIRE_INDEPENDENT_RECALL", "false").lower() == "true"
STRICT = os.environ.get("STRICT", "true").lower() == "true"
PAGE = int(os.environ.get("PAGE", "500"))
BREAKGLASS_ALERTS = {"signal-projector-stale", "alertmanager-down"}


def out(msg):
    print(msg, file=sys.stderr, flush=True)


def collect(target, query, sort_field, source=None):
    rows = []
    try:
        for hits in os_cursor.scan(OS_URL, target, query, sort_field,
                                   source=source, page=PAGE):
            rows.extend(hits)
    except os_cursor.CursorError as e:
        out(f"WARNING: could not traverse {target}: {e}")
    return rows


def window(field):
    return {"range": {field: {
        "gte": START_MS, "lte": END_MS, "format": "epoch_millis"}}}


def raw_alerts():
    ids = set()
    for target, field in ((ALERTS_CURRENT, "start_time"),
                          (ALERTS_HISTORY, "start_time")):
        rows = collect(target, {"bool": {"filter": [window(field)]}}, field)
        for hit in rows:
            src = hit.get("_source") or {}
            if src.get("id"):
                ids.add(str(src["id"]))
    return ids


def raw_anomalies():
    query = {"bool": {
        "filter": [window("execution_end_time"),
                   {"range": {"anomaly_grade": {"gt": GRADE_FLOOR}}}],
        "must_not": [{"exists": {"field": "task_id"}}]}}
    rows = collect(AD_RESULTS, query, "execution_end_time",
                   source=["detector_id", "anomaly_grade",
                           "execution_end_time"])
    return {str(h.get("_id")) for h in rows}


def signals():
    rows = collect(
        SIGNALS_INDEX,
        {"bool": {"filter": [window("@timestamp")]}}, "@timestamp")
    return [h.get("_source") or {} for h in rows]


def incidents():
    rows = collect(
        INCIDENTS_INDEX,
        {"bool": {"filter": [window("@timestamp")]}}, "@timestamp")
    return [h.get("_source") or {} for h in rows]


def notifications():
    rows = collect(
        NOTIFICATIONS_INDEX,
        {"bool": {"filter": [window("@timestamp"),
                             {"term": {"record_kind": "notification"}}]}},
        "@timestamp")
    return [h.get("_source") or {} for h in rows]


def main():
    if START_MS <= 0:
        out("FATAL: START_MS is required (epoch millis of the injection)")
        return 1

    alert_ids = raw_alerts()
    anomaly_ids = raw_anomalies()
    sig = signals()
    inc = incidents()
    notes = notifications()
    ordinary_notes = [
        n for n in notes if n.get("delivery_path") != "breakglass"]
    breakglass_notes = [
        n for n in notes if n.get("delivery_path") == "breakglass"]
    breakglass_delivered = {
        str(n.get("alertname") or "") for n in breakglass_notes}
    unexpected_breakglass = sorted(
        name for name in breakglass_delivered
        if name not in BREAKGLASS_ALERTS)
    required_breakglass = (
        {"signal-projector-stale"} if SCENARIO == "stop-projector" else set())
    missing_breakglass = sorted(required_breakglass - breakglass_delivered)

    projected = {str(s.get("source_id")) for s in sig}
    missing_alerts = sorted(alert_ids - projected)
    missing_anomalies = sorted(anomaly_ids - projected)
    reconciliation = {
        "raw_alerts": len(alert_ids),
        "raw_anomalies": len(anomaly_ids),
        "projected_signals": len(sig),
        "missing_alert_ids": missing_alerts[:20],
        "missing_anomaly_ids": missing_anomalies[:20],
        "lossless": not missing_alerts and not missing_anomalies,
    }

    recall = None
    if INDEPENDENT_ENTITY:
        hit = [s for s in sig if s.get("entity_id") == INDEPENDENT_ENTITY]
        recall = {
            "entity": INDEPENDENT_ENTITY,
            "signals": len(hit),
            "surfaced": bool(hit),
            "incidents": sorted({s.get("incident_id") for s in hit}),
        }

    by_incident = {}
    for s in sig:
        by_incident.setdefault(s.get("incident_id"), []).append(s)

    impure = {}
    for key, members in by_incident.items():
        mixed = {}
        for field in ("entity_id", "collector_id", "topology_version",
                      "entity_kind"):
            values = {m.get(field) for m in members}
            if len(values) > 1:
                mixed[field] = sorted(str(v) for v in values)
        if mixed:
            impure[key] = mixed

    group_impure = {}
    unlinked_notifications = []
    for n in ordinary_notes:
        ids = {str(x) for x in (n.get("signal_ids") or [])}
        episodes_covered = {str(x) for x in (n.get("episode_ids") or [])}
        members = [s for s in sig
                   if str(s.get("source_id")) in ids
                   or str(s.get("episode_id")) in episodes_covered]
        if not members:
            unlinked_notifications.append(n.get("group_key", "?"))
            continue
        scopes = {m.get("notification_scope") for m in members}
        collectors = {m.get("collector_id") for m in members}
        if "collector" in scopes and len(collectors) > 1:
            group_impure[n.get("group_key", "?")] = sorted(
                str(c) for c in collectors)

    by_entity_kind = {}
    for i in inc:
        key = f"{i.get('alertname')}/{i.get('entity_kind')}"
        by_entity_kind[key] = by_entity_kind.get(key, 0) + 1
    expected_entities = {}
    for s in sig:
        key = f"{s.get('alertname')}/{s.get('entity_kind')}"
        expected_entities.setdefault(key, set()).add(s.get("entity_id"))
    fragmentation = {
        k: {"incidents": v, "distinct_entities": len(expected_entities.get(k, ()))}
        for k, v in by_entity_kind.items()
        if v > len(expected_entities.get(k, ()))
    }

    notified = set()
    notified_incidents = set()
    for n in ordinary_notes:
        notified.update(str(x) for x in (n.get("signal_ids") or []))
        notified_incidents.update(
            str(x) for x in (n.get("episode_ids") or []))
    pages = [s for s in sig if s.get("severity") == "page"]
    unnotified = [s.get("source_id") for s in pages
                  if str(s.get("source_id")) not in notified
                  and str(s.get("episode_id")) not in notified_incidents
                  and str(s.get("alertname") or "") not in breakglass_delivered]

    first_note = min((n.get("@timestamp") for n in notes), default=None)
    resolved = [i.get("resolved_at") for i in inc if i.get("resolved_at")]
    first_resolve = min(resolved) if resolved else None

    report = {
        "scenario": SCENARIO,
        "window_ms": [START_MS, END_MS],
        "signal_reconciliation": reconciliation,
        "independent_event_recall": recall,
        "incident_purity": {
            "incidents": len(by_incident),
            "incidents_mixing_entity_or_topology": impure,
            "notification_groups_mixing_collectors": group_impure,
            "notifications_with_no_resolvable_members": unlinked_notifications,
            "pure": not impure and not group_impure,
        },
        "fragmentation": {
            "incidents_per_alertname_and_kind": by_entity_kind,
            "fragmented": fragmentation,
        },
        "time_to_notify_ms": (first_note - START_MS) if first_note else None,
        "time_to_resolve_ms": (
            first_resolve - START_MS) if first_resolve else None,
        "false_inhibition": {
            "page_signals": len(pages),
            "page_signals_never_notified": unnotified[:20],
            "score": len(unnotified),
        },
        "breakglass": {
            "delivered": sorted(breakglass_delivered),
            "unexpected": unexpected_breakglass,
            "missing_required": missing_breakglass,
        },
        "notification_volume": len(notes),
    }
    print(json.dumps(report, indent=2, default=str))

    out("")
    out(f"scenario {SCENARIO}")
    out(f"  signal reconciliation  : "
        f"{'LOSSLESS' if reconciliation['lossless'] else 'LOSSY'} "
        f"({len(sig)} rows for {len(alert_ids)} alerts + "
        f"{len(anomaly_ids)} anomalies)")
    if recall:
        out(f"  independent-event recall: "
            f"{'surfaced' if recall['surfaced'] else 'ABSORBED'} "
            f"({recall['entity']})")
    out(f"  incident purity        : "
        f"{'pure' if not impure else str(len(impure)) + ' mixed'}")
    out(f"  fragmentation          : "
        f"{'none' if not fragmentation else json.dumps(fragmentation)}")
    out(f"  time to notify         : {report['time_to_notify_ms']} ms")
    out(f"  time to resolve        : {report['time_to_resolve_ms']} ms")
    out(f"  false inhibition       : {len(unnotified)} "
        f"(must be 0 on the approved rules)")
    out(f"  break-glass             : "
        f"delivered={sorted(breakglass_delivered)} "
        f"unexpected={unexpected_breakglass} "
        f"missing={missing_breakglass}")
    out(f"  notifications           : {len(notes)}")

    verdict = []
    if not reconciliation["lossless"]:
        verdict.append(
            "signal reconciliation is lossy — raw alerts or anomalies never "
            "reached alice-signals, which is the one thing grouping may never "
            "do")
    if unlinked_notifications:
        verdict.append(
            f"{len(unlinked_notifications)} notifications carry no signal or "
            f"incident id that resolves to a row — the delivery record cannot "
            f"be tied back to what it covered, so purity and false inhibition "
            f"are both unmeasurable: {unlinked_notifications[:5]}")
    if unexpected_breakglass:
        verdict.append(
            f"unexpected alerts used the break-glass path: "
            f"{unexpected_breakglass}; only "
            f"{sorted(BREAKGLASS_ALERTS)} may bypass Alertmanager")
    if missing_breakglass:
        verdict.append(
            f"required break-glass notifications were not delivered: "
            f"{missing_breakglass}")
    if impure or group_impure:
        verdict.append(
            "incident purity failed — members of one episode disagree on "
            "entity, topology version, or the collector a collector-scoped "
            "notification group covers")
    if fragmentation:
        verdict.append(
            f"fragmentation — more incidents than distinct entities: "
            f"{fragmentation}")
    if unnotified:
        verdict.append(
            f"false inhibition score {len(unnotified)}, must be 0 — a page "
            f"was muted that should have reached a human")
    if REQUIRE_INDEPENDENT_RECALL and not (recall and recall["surfaced"]):
        verdict.append(
            "independent-event recall failed — the separately injected entity "
            "was absorbed into the storm instead of surfacing")
    if first_note is None and pages:
        verdict.append(
            "pages fired but nothing was ever notified — time-to-notify is "
            "unmeasurable and the delivery path is not proven")

    if verdict:
        out("")
        for line in verdict:
            out(f"  FAIL: {line}")
        if STRICT:
            out("  scorecard FAILED (set STRICT=false to report without "
                "gating)")
            return 1
        out("  STRICT=false — reporting only, not gating")
        return 0
    out("")
    out("  scorecard PASSED on every metric it can measure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
