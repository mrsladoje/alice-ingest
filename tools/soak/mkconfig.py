#!/usr/bin/env python3

import argparse
import os
import re
import sys

import yaml
from jinja2 import Environment, StrictUndefined

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEMPLATE = os.path.join(
    REPO, "deploy", "roles", "collector", "templates", "collector.yaml.j2")
PARSERS = os.path.join(
    REPO, "deploy", "roles", "collector", "templates", "parsers.yaml.j2")

LOG_MATCHES = {"infologger", "family.local", "family.central"}

# Which tag each input carries, so a filter can be matched back to its input.
INPUT_TAGS = {"dds", "stdout", "infologger"}


def ternary(value, when_true, when_false):
    return when_true if value else when_false


def to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def render(live_lane, flush, buffer_limit, retry_limit,
           lane_host="sink", lane_port=9200, lane_path="/ingest"):
    with open(TEMPLATE, "r") as handle:
        source = handle.read()
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    env.filters["bool"] = to_bool
    env.filters["ternary"] = ternary
    return env.from_string(source).render(
        ansible_managed="soak rig",
        collector_config_dir="/etc/fluent-bit",
        collector_health_script="/opt/alice-ingest/fb_health.py",
        collector_health_interval_seconds=10,
        cockpit_metrics_index="cockpit-metrics",
        fluent_bit_flush_seconds=flush,
        fluent_bit_log_buffer_limit=buffer_limit,
        fluent_bit_log_retry_limit=retry_limit,
        health_metrics_emit_legacy_node=False,
        live_lane_enabled=live_lane,
        live_lane_host=lane_host,
        live_lane_port=lane_port,
        live_lane_ingest_path=lane_path,
    )


DUP = "__dup%d__"


class DupKeyLoader(yaml.SafeLoader):
    pass


def _mapping_with_dups(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        value = loader.construct_object(value_node, deep=True)
        if key in mapping:
            index = 2
            while (key + DUP % index) in mapping:
                index += 1
            key = key + DUP % index
        mapping[key] = value
    return mapping


DupKeyLoader.construct_mapping = _mapping_with_dups
DupKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    lambda loader, node: _mapping_with_dups(loader, node))


def strip_dup_markers(text):
    return re.sub(r"__dup\d+__", "", text)


def block_str(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def filter_tags(item):
    """Which of the three families a filter applies to.

    A filter selects by `match` or by `match_regex`. Only the simple
    alternation the template actually uses is understood; anything else
    returns nothing and the filter stays on the main loop, which is the safe
    answer."""
    match = item.get("match")
    if match in INPUT_TAGS:
        return {match}
    pattern = item.get("match_regex")
    if not pattern:
        return set()
    body = pattern.strip()
    if body.startswith("^(") and body.endswith(")$"):
        names = {name.strip() for name in body[2:-2].split("|")}
        if names <= INPUT_TAGS:
            return names
    return set()


def to_processor(item):
    """A filter, as a processor. Processors run inside the input's own thread;
    filters always run on the main event loop. That difference is the whole
    point of the t2 arm."""
    processor = {key: value for key, value in item.items()
                 if key not in ("match", "match_regex")}
    return processor


def make_threaded(config):
    """t1 — `threaded: on` on both tails and on the tcp input."""
    for item in config["pipeline"]["inputs"]:
        if item.get("name") in ("tail", "tcp"):
            item["threaded"] = "on"


def move_filters_to_processors(config):
    """t2 — the per-tag work moved off the main loop.

    `rewrite_tag` cannot move: changing a tag changes routing, and routing is
    filter-only. So the severity split stays on the main loop whatever else
    happens, and that is the arm's ceiling.
    """
    pipeline = config["pipeline"]
    by_tag = {}
    keep = []
    for item in pipeline["filters"]:
        tags = filter_tags(item)
        if not tags or item.get("name") == "rewrite_tag":
            keep.append(item)
            continue
        for tag in tags:
            by_tag.setdefault(tag, []).append(to_processor(item))
    pipeline["filters"] = keep
    moved = 0
    for item in pipeline["inputs"]:
        chain = by_tag.get(item.get("tag"))
        if not chain:
            continue
        item["processors"] = {"logs": chain}
        moved += len(chain)
    return moved


def lane_on_its_own_tag(config, lane_match):
    """lt — the live lane fed from its own tag.

    A chunk is freed only when every matching output has finished with it, so
    a slow lane holds InfoLogger chunks open. Its own tag makes the lane's
    chunks independent. `keep true` means the original record still reaches
    OpenSearch."""
    pipeline = config["pipeline"]
    router = {
        "name": "rewrite_tag",
        "match_regex": lane_match,
        "rule": "$log_source ^.*$ lane.$log_source true",
        "emitter_name": "lane_router",
    }
    pipeline["filters"].append(router)
    for item in pipeline["outputs"]:
        if item.get("name") == "http" and item.get("uri") not in ("/_bulk",):
            item.pop("match_regex", None)
            item["match"] = "lane.*"
    return router


TAILED = {"dds", "stdout", "family.local", "family.central"}


def keep_families(config, families):
    """t3 — split the pipeline so two Fluent Bit processes can share the work.

    The main-loop ceiling is per process. One process for the tailed families
    and one for InfoLogger doubles the ceiling and isolates the fragile path.
    Both still have to fit inside the same four cores, which is the arm's
    whole question.
    """
    if families == "all":
        return
    want_il = families == "infologger"
    pipeline = config["pipeline"]

    inputs = []
    for item in pipeline["inputs"]:
        tag = item.get("tag")
        if tag == "health":
            inputs.append(item)
            continue
        is_il = tag == "infologger"
        if is_il == want_il:
            inputs.append(item)
    pipeline["inputs"] = inputs

    filters = []
    for item in pipeline["filters"]:
        tags = filter_tags(item)
        if not tags:
            filters.append(item)
            continue
        if want_il:
            if "infologger" in tags:
                filters.append(item)
        elif tags - {"infologger"}:
            filters.append(item)
    pipeline["filters"] = filters

    outputs = []
    for item in pipeline["outputs"]:
        match = item.get("match")
        if match == "health" or item.get("match_regex"):
            outputs.append(item)
            continue
        if want_il:
            if match == "infologger":
                outputs.append(item)
        elif match in TAILED:
            outputs.append(item)
    pipeline["outputs"] = outputs


def swap_infologger_to_tail(pipeline, path):
    """s1 — InfoLogger read from a file instead of straight off the socket.

    DDS and stdout survived round 1's outage because the log file *is* the
    queue. The tcp input has nothing behind the socket, and lost every record
    the outage touched. This gives InfoLogger the same file the other two
    already have.
    """
    inputs = []
    for item in pipeline["inputs"]:
        if item.get("name") != "tcp":
            inputs.append(item)
            continue
        inputs.append({
            "name": "tail",
            "path": path,
            "path_key": "file",
            "tag": item.get("tag", "infologger"),
            "parser": "json",
            "read_from_head": True,
            "refresh_interval": 5,
            "storage.type": item.get("storage.type", "filesystem"),
            "db": "${ALICE_FB_STORAGE_PATH}/infologger.db",
        })
    pipeline["inputs"] = inputs


def is_log_output(item):
    return item.get("match") in LOG_MATCHES or item.get("match_regex") is not None \
        and item.get("name") == "opensearch"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--sink", default="null",
                        choices=["null", "http", "opensearch"])
    parser.add_argument("--sink-host", default="sink")
    parser.add_argument("--sink-port", type=int, default=9200)
    parser.add_argument("--sink-uri", default="/_bulk")
    parser.add_argument("--flush", type=float, default=5)
    parser.add_argument("--max-chunks-up", type=int, default=64)
    parser.add_argument("--storage-type", default="filesystem",
                        choices=["filesystem", "memory"])
    parser.add_argument("--pause-on-overlimit", default="off",
                        choices=["on", "off"])
    parser.add_argument("--mem-buf-limit", default="")
    parser.add_argument("--backlog-mem-limit", default="")
    parser.add_argument("--total-limit-size", default="256M")
    parser.add_argument("--retry-limit", type=int, default=10)
    parser.add_argument("--output-workers", type=int, default=-1)
    parser.add_argument("--compress", default="")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--lua", default="on", choices=["on", "off"])
    parser.add_argument("--health", default="off", choices=["on", "off"])
    parser.add_argument("--live-lane", default="off", choices=["on", "off"])
    parser.add_argument("--live-lane-host", default="sink")
    parser.add_argument("--live-lane-port", type=int, default=9200)
    parser.add_argument("--live-lane-path", default="/ingest")
    parser.add_argument("--lane-compress", default="",
                        choices=["", "off", "gzip", "zstd", "snappy"],
                        help="the live lane's own compression. The template "
                             "ships gzip; this is the only knob that touches "
                             "the lane output rather than the sink outputs.")
    parser.add_argument("--os-buffer-size", default="",
                        help="response buffer for the opensearch outputs. The "
                             "shipped template now sets 'False', which "
                             "reads the whole response. Leave empty for "
                             "the plugin default, which truncates a large "
                             "bulk response and makes the output retry a "
                             "write OpenSearch already applied.")
    parser.add_argument("--parsers-out", default="")
    parser.add_argument("--arm", default="t0",
                        choices=["t0", "t1", "t2"],
                        help="t0 as shipped; t1 threaded inputs; t2 also "
                             "moves the per-tag filters into each input's "
                             "processors, off the main event loop")
    parser.add_argument("--lane-own-tag", default="off", choices=["on", "off"],
                        help="lt — feed the live lane from its own tag so a "
                             "slow lane cannot retain InfoLogger chunks")
    parser.add_argument("--infologger-tap", default="tcp",
                        choices=["tcp", "file"],
                        help="tcp as shipped; file tails what the appender "
                             "wrote, the way a real InfoLogger file tap would")
    parser.add_argument("--infologger-path",
                        default="${ALICE_LOG_ROOT}/infologger/*.log")
    parser.add_argument("--families", default="all",
                        choices=["all", "tailed", "infologger"],
                        help="t3 splits the pipeline across two processes: "
                             "one for the tailed families, one for InfoLogger")
    args = parser.parse_args()

    text = render(args.live_lane == "on", args.flush,
                  args.total_limit_size, args.retry_limit,
                  args.live_lane_host, args.live_lane_port, args.live_lane_path)
    config = yaml.load(text, Loader=DupKeyLoader)

    service = config["service"]
    service["flush"] = args.flush
    service["log_level"] = args.log_level
    service["storage.max_chunks_up"] = args.max_chunks_up
    if args.backlog_mem_limit:
        service["storage.backlog.mem_limit"] = args.backlog_mem_limit

    pipeline = config["pipeline"]

    if args.infologger_tap == "file":
        swap_infologger_to_tail(pipeline, args.infologger_path)

    inputs = []
    for item in pipeline["inputs"]:
        if item.get("tag") == "health" and args.health == "off":
            continue
        if item.get("name") in ("tail", "tcp"):
            item["storage.type"] = args.storage_type
            if args.mem_buf_limit:
                item["mem_buf_limit"] = args.mem_buf_limit
            elif "mem_buf_limit" in item:
                del item["mem_buf_limit"]
            if args.storage_type == "filesystem":
                item["storage.pause_on_chunks_overlimit"] = args.pause_on_overlimit
            else:
                item.pop("storage.pause_on_chunks_overlimit", None)
                item.pop("db", None)
        inputs.append(item)
    pipeline["inputs"] = inputs

    filters = []
    for item in pipeline["filters"]:
        if item.get("match") == "health" and args.health == "off":
            continue
        if args.lua == "off" and item.get("call") == "stamp_collector_time":
            continue
        filters.append(item)
    pipeline["filters"] = filters

    outputs = []
    for item in pipeline["outputs"]:
        if item.get("match") == "health":
            if args.health == "off":
                continue
            if args.sink != "opensearch":
                item["host"] = args.sink_host
                item["port"] = args.sink_port
            outputs.append(item)
            continue
        if item.get("name") == "http" and item.get("uri") == "/ingest":
            if args.lane_compress == "off":
                item.pop("compress", None)
            elif args.lane_compress:
                item["compress"] = args.lane_compress
            outputs.append(item)
            continue

        match = item.get("match")
        new = {"name": args.sink, "match": match}
        if args.sink == "http":
            new.update({
                "host": args.sink_host,
                "port": args.sink_port,
                "uri": args.sink_uri,
                "format": "json_lines",
                "json_date_key": "@timestamp",
                "json_date_format": "iso8601",
                "header": "Content-Type application/json",
                "net.keepalive": "on",
            })
        elif args.sink == "opensearch":
            new.update({
                "host": args.sink_host,
                "port": args.sink_port,
                "index": item.get("index", "soak"),
                "suppress_type_name": True,
                "trace_error": True,
            })
            if args.os_buffer_size:
                new["buffer_size"] = args.os_buffer_size
        if args.sink != "null":
            new["storage.total_limit_size"] = args.total_limit_size
            new["retry_limit"] = args.retry_limit
            if args.compress:
                new["compress"] = args.compress
        else:
            new["storage.total_limit_size"] = args.total_limit_size
            new["retry_limit"] = args.retry_limit
        if args.output_workers >= 0:
            new["workers"] = args.output_workers
        outputs.append(new)
    pipeline["outputs"] = outputs

    keep_families(config, args.families)

    if args.arm in ("t1", "t2"):
        make_threaded(config)
    if args.arm == "t2":
        move_filters_to_processors(config)
    if args.lane_own_tag == "on" and args.live_lane == "on":
        lane_on_its_own_tag(config, r"^(infologger|family\.central)$")

    yaml.add_representer(str, block_str)
    rendered = yaml.dump(config, sort_keys=False, default_flow_style=False,
                         width=4096)
    with open(args.out, "w") as handle:
        handle.write(strip_dup_markers(rendered))

    if args.parsers_out:
        with open(PARSERS, "r") as handle:
            parsers = handle.read()
        with open(args.parsers_out, "w") as handle:
            handle.write(parsers)

    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
