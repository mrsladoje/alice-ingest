#!/usr/bin/env python3
"""N concurrent viewers on the live lane, for the `lv` cells.

The lane is a server-sent-event stream: one long-lived HTTP connection per
viewer, one `data:` frame per record. The production shape is about five
viewers at a time, so the two cells ask for five and twenty.

**This is not a collector arm.** The lane server's cost belongs to the lane
server, and the point of the cells is to price it separately from the worker's
four cores. Round 1's five-per-cent throttle figure came from one starved
Python receiver, so this one runs on its own pinned cores and the report says
what it used.

    python3 tools/soak/viewers.py --url http://lane:8092/stream \\
        --viewers 5 --duration 300 --summary /out/viewers.json
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request


def viewer(url, index, duration, results, stop):
    """One long-lived stream connection, counted rather than parsed.

    A viewer that parsed every record would measure this script instead of the
    lane, so frames are counted and their bytes added up, nothing more.
    """
    events = 0
    keepalives = 0
    payload_bytes = 0
    first_event = 0.0
    error = ""
    started = time.time()
    try:
        request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(request, timeout=30) as stream:
            for raw in stream:
                if stop.is_set() or time.time() - started >= duration:
                    break
                payload_bytes += len(raw)
                if raw.startswith(b"data:"):
                    events += 1
                    if not first_event:
                        first_event = time.time() - started
                elif raw.startswith(b":"):
                    keepalives += 1
    except Exception as failure:  # noqa: BLE001 - a dropped viewer is a result
        error = "%s: %s" % (type(failure).__name__, failure)
    results[index] = {
        "viewer": index,
        "events": events,
        "keepalives": keepalives,
        "bytes": payload_bytes,
        "seconds": round(time.time() - started, 2),
        "first_event_after": round(first_event, 3),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://lane:8092/stream")
    parser.add_argument("--health-url", default="http://lane:8092/healthz")
    parser.add_argument("--viewers", type=int, default=5)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--stagger", type=float, default=0.2)
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    results = {}
    stop = threading.Event()
    threads = []
    for index in range(args.viewers):
        thread = threading.Thread(target=viewer,
                                  args=(args.url, index, args.duration,
                                        results, stop))
        thread.daemon = True
        thread.start()
        threads.append(thread)
        time.sleep(args.stagger)

    deadline = time.time() + args.duration + 10
    for thread in threads:
        thread.join(timeout=max(1, deadline - time.time()))
    stop.set()

    rows = [results[key] for key in sorted(results)]
    health = {}
    try:
        with urllib.request.urlopen(args.health_url, timeout=10) as response:
            health = json.loads(response.read())
    except Exception:  # noqa: BLE001
        pass

    summary = {
        "asked_viewers": args.viewers,
        "connected_viewers": sum(1 for row in rows if not row["error"]),
        "failed_viewers": [row for row in rows if row["error"]],
        "events_total": sum(row["events"] for row in rows),
        "bytes_total": sum(row["bytes"] for row in rows),
        "events_per_viewer_min": min((row["events"] for row in rows), default=0),
        "events_per_viewer_max": max((row["events"] for row in rows), default=0),
        "seconds": round(max((row["seconds"] for row in rows), default=0), 2),
        "lane_health": health,
        "viewers": rows,
    }
    if args.summary:
        with open(args.summary, "w") as handle:
            json.dump(summary, handle, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
