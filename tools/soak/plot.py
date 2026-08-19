#!/usr/bin/env python3

import argparse
import csv
import json
import os
import sys

WIDTH, HEIGHT = 900, 320
LEFT, RIGHT, TOP, BOTTOM = 70, 24, 28, 42
INK = "#1a1a1a"
GRID = "#d8d8d8"
COLORS = ["#1a1a1a", "#c8102e", "#0057b8", "#7a7a7a", "#00843d"]


def read_csv(path):
    with open(path) as handle:
        rows = list(csv.DictReader(handle))
    columns = {}
    for row in rows:
        for key, value in row.items():
            try:
                columns.setdefault(key, []).append(float(value))
            except (TypeError, ValueError):
                columns.setdefault(key, []).append(0.0)
    return columns


def nice_max(value):
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= magnitude * step:
            return magnitude * step
    return magnitude * 10


def escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chart(path, title, x, series, ylabel, rules=(), marks=()):
    if not x:
        return False
    x_max = max(x) or 1.0
    y_max = nice_max(max([max(values) if values else 0 for _, values in series] + [1]))
    plot_w = WIDTH - LEFT - RIGHT
    plot_h = HEIGHT - TOP - BOTTOM

    def px(value):
        return LEFT + plot_w * (value / x_max)

    def py(value):
        return TOP + plot_h - plot_h * (min(value, y_max) / y_max)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'font-family="Helvetica Neue, Helvetica, Arial, sans-serif" '
           'font-size="11">' % (WIDTH, HEIGHT)]
    out.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (WIDTH, HEIGHT))
    out.append('<text x="%d" y="16" font-size="13" font-weight="600" fill="%s">%s</text>'
               % (LEFT, INK, escape(title)))

    for index in range(5):
        value = y_max * index / 4
        y = py(value)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="0.7"/>' % (LEFT, y, WIDTH - RIGHT, y, GRID))
        label = "%.0f" % value if y_max >= 10 else "%.2f" % value
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s">%s</text>'
                   % (LEFT - 6, y + 3, INK, label))
    out.append('<text x="14" y="%d" fill="%s" transform="rotate(-90 14 %d)" '
               'text-anchor="middle">%s</text>'
               % (TOP + plot_h / 2, INK, TOP + plot_h / 2, escape(ylabel)))

    for index in range(6):
        value = x_max * index / 5
        x_pos = px(value)
        out.append('<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%d</text>'
                   % (x_pos, HEIGHT - 16, INK, int(value)))
    out.append('<text x="%d" y="%d" text-anchor="middle" fill="%s">seconds</text>'
               % (LEFT + plot_w / 2, HEIGHT - 4, INK))

    for value, label in rules:
        y = py(value)
        if value > y_max:
            continue
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="1" stroke-dasharray="5 3"/>'
                   % (LEFT, y, WIDTH - RIGHT, y, COLORS[1]))
        out.append('<text x="%d" y="%.1f" fill="%s">%s</text>'
                   % (WIDTH - RIGHT - 4, y - 4, COLORS[1], escape(label)))

    for at, label in marks:
        x_pos = px(at)
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" '
                   'stroke-width="1" stroke-dasharray="2 3"/>'
                   % (x_pos, TOP, x_pos, TOP + plot_h, COLORS[2]))
        out.append('<text x="%.1f" y="%d" fill="%s">%s</text>'
                   % (x_pos + 3, TOP + 10, COLORS[2], escape(label)))

    legend_x = LEFT
    for index, (name, values) in enumerate(series):
        color = COLORS[index % len(COLORS)]
        points = " ".join("%.1f,%.1f" % (px(x[i]), py(values[i]))
                          for i in range(min(len(x), len(values))))
        out.append('<polyline fill="none" stroke="%s" stroke-width="1.6" '
                   'points="%s"/>' % (color, points))
        out.append('<rect x="%d" y="%d" width="9" height="9" fill="%s"/>'
                   % (legend_x, TOP - 12, color))
        out.append('<text x="%d" y="%d" fill="%s">%s</text>'
                   % (legend_x + 13, TOP - 4, INK, escape(name)))
        legend_x += 22 + 6 * len(name)

    out.append("</svg>")
    with open(path, "w") as handle:
        handle.write("\n".join(out))
    return True


def deltas(values):
    return [max(0.0, values[i] - values[i - 1]) if i else 0.0
            for i in range(len(values))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--mem-high-mb", type=float, default=384)
    parser.add_argument("--mem-max-mb", type=float, default=768)
    args = parser.parse_args()

    rec_path = os.path.join(args.run_dir, "soakrec.csv")
    if not os.path.exists(rec_path):
        raise SystemExit("plot: no soakrec.csv in %s" % args.run_dir)
    rec = read_csv(rec_path)
    charts_dir = os.path.join(args.run_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    summary = {}
    summary_path = os.path.join(args.run_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path) as handle:
            summary = json.load(handle)
    marks = [(entry["t"], entry["action"]) for entry in summary.get("faults", [])
             if isinstance(entry, dict) and "t" in entry]

    t = rec.get("t", [])
    interval = (t[1] - t[0]) if len(t) > 1 else 1.0
    made = []

    ingest = rec.get("in_ingest") or rec.get("in_records", [])
    rates = [value / interval for value in deltas(ingest)]
    out_rates = [value / interval for value in deltas(rec.get("out_records", []))]
    if chart(os.path.join(charts_dir, "throughput.svg"),
             "Records a second, in and out", t,
             [("ingested", rates), ("delivered", out_rates)],
             "records/s", marks=marks):
        made.append("throughput.svg")

    memory = [value / 1e6 for value in rec.get("mem_current", [])]
    if chart(os.path.join(charts_dir, "memory.svg"),
             "Resident memory against the unit's ceilings", t,
             [("memory", memory)], "MB",
             rules=[(args.mem_high_mb, "MemoryHigh %d MB" % args.mem_high_mb),
                    (args.mem_max_mb, "MemoryMax %d MB" % args.mem_max_mb)],
             marks=marks):
        made.append("memory.svg")

    if chart(os.path.join(charts_dir, "queue.svg"),
             "Buffered chunks, and how many left memory", t,
             [("total chunks", rec.get("total_chunks", [])),
              ("on disk only", rec.get("fs_chunks_down", [])),
              ("in memory", rec.get("mem_chunks", []))],
             "chunks", marks=marks):
        made.append("queue.svg")

    if chart(os.path.join(charts_dir, "loss.svg"),
             "Records dropped, retries that failed", t,
             [("dropped", rec.get("out_dropped", [])),
              ("retries failed", rec.get("out_retries_failed", [])),
              ("retries", rec.get("out_retries", []))],
             "records", marks=marks):
        made.append("loss.svg")

    if rec.get("storage_bytes"):
        disk = [value / 1e6 for value in rec["storage_bytes"]]
        if chart(os.path.join(charts_dir, "disk.svg"),
                 "Buffer on disk", t, [("flb-storage", disk)], "MB",
                 marks=marks):
            made.append("disk.svg")

    page = ["<title>soak charts %s</title>" % escape(summary.get("run_id", "")),
            "<h1>soak run %s</h1>" % escape(summary.get("run_id", "")),
            "<p>%s</p>" % escape(summary.get("what", ""))]
    for name in made:
        page.append('<p><img src="charts/%s" style="max-width:100%%"></p>' % name)
    with open(os.path.join(args.run_dir, "charts.html"), "w") as handle:
        handle.write("\n".join(page) + "\n")

    print("\n".join(os.path.join(charts_dir, name) for name in made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
