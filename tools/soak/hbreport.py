import glob
import json
import os
import re
import sys

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")


def heap_of(cell):
    for token in cell.split("-"):
        if re.match(r"^\d+g$", token):
            return token
    return "?"


def rows(prefix):
    out = []
    for path in sorted(glob.glob(os.path.join(RUNS, "*-p6", "summary.json"))):
        try:
            summary = json.load(open(path))
        except ValueError:
            continue
        cell = summary.get("cell") or ""
        if not cell.startswith(prefix):
            continue
        rec = summary.get("recorder") or {}
        drain = summary.get("drain") or {}
        gun = summary.get("gun") or {}
        out.append({
            "cell": cell,
            "heap": heap_of(cell),
            "emitted": gun.get("emitted_total"),
            "ingested": rec.get("ingested_records"),
            "output": rec.get("output_records"),
            "dropped": rec.get("output_dropped"),
            "retries": rec.get("output_retries"),
            "retries_failed": rec.get("output_retries_failed"),
            "errors": rec.get("output_errors"),
            "peak_chunks": rec.get("peak_total_chunks"),
            "peak_fs_up": rec.get("peak_fs_chunks_up"),
            "peak_storage_mb": rec.get("peak_storage_mb"),
            "peak_mem_mb": rec.get("peak_memory_mb"),
            "mem_high": rec.get("memory_high_events"),
            "oom": rec.get("oom_kill_events"),
            "drained": drain.get("drained"),
            "drain_s": drain.get("seconds"),
            "sampling_clean": (rec.get("sampling") or {}).get("clean"),
            "seconds_lost": (rec.get("sampling") or {}).get("seconds_lost"),
            "run": os.path.basename(os.path.dirname(path)),
        })
    mark_void(out)
    return out


def mark_void(rows):
    """A cell is void if the recorder lost time, or if the generator failed to
    deliver what the block's other cells delivered. Both mean the number
    describes the machine, not the knob."""
    emitted = sorted(r["emitted"] for r in rows if r["emitted"])
    ref = emitted[len(emitted) // 2] if emitted else None
    for r in rows:
        why = []
        if r["sampling_clean"] is False:
            why.append("recorder lost %ss" % r["seconds_lost"])
        if ref and r["emitted"] and r["emitted"] < ref * 0.98:
            why.append("offer short %.1f%%" % (100.0 * (1 - r["emitted"] / ref)))
        r["void"] = why


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "HB-"
    data = rows(prefix)
    if not data:
        print("no cells matching %s" % prefix)
        return
    head = ("cell", "chunks/64", "peak mem MB", "lost", "drain s", "valid")
    print("%-22s %-10s %-12s %-6s %-9s %s" % head)
    for r in data:
        lost = None
        if r["dropped"] is not None or r["retries_failed"] is not None:
            lost = (r["dropped"] or 0) + (r["retries_failed"] or 0)
        print("%-22s %-10s %-12s %-6s %-9s %s"
              % (r["cell"], r["peak_chunks"], r["peak_mem_mb"], lost,
                 r["drain_s"],
                 "ok" if not r["void"] else "VOID: " + "; ".join(r["void"])))
    print()
    data = [r for r in data if not r["void"]]
    if not data:
        print("every cell is void")
        return
    by_heap = {}
    for r in data:
        by_heap.setdefault(r["heap"], []).append(r)
    print("%-6s %-12s %-14s %-13s %-8s %s"
          % ("heap", "peak chunks", "peak storage", "peak mem MB", "lost",
             "all drained"))
    for heap in sorted(by_heap):
        group = by_heap[heap]
        chunks = [r["peak_chunks"] for r in group if r["peak_chunks"] is not None]
        store = [r["peak_storage_mb"] for r in group
                 if r["peak_storage_mb"] is not None]
        mem = [r["peak_mem_mb"] for r in group if r["peak_mem_mb"] is not None]
        lost = sum((r["dropped"] or 0) + (r["retries_failed"] or 0)
                   for r in group)
        print("%-6s %-12s %-14s %-13s %-8s %s"
              % (heap, max(chunks) if chunks else None,
                 max(store) if store else None, max(mem) if mem else None,
                 lost, all(r["drained"] for r in group)))


main()
