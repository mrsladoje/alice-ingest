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
    parser.add_argument("--parsers-out", default="")
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
