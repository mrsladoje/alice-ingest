#!/usr/bin/env python3
"""Render the real `sweet_opensearch` script for the rig.

Round 1's OpenSearch was a single bare container with no templates and no
rollover aliases. It collapsed at 2,000 records a second, and `docs/SOAK.md`
says plainly: do not quote that number. The fix is not a better container, it
is the same bootstrap the deploy applies — the component templates, the
tier-aware mappings, the rollover write aliases — so the cluster under test is
the cluster we ship.

The variables come from `deploy/group_vars/all.yml`, exactly as the playbook
reads them, so the rig cannot drift from production by editing a copy.

**One deliberate divergence, and it is stated in the results.** Production runs
three storage nodes and asks for two replicas. Twelve processors afford two
storage containers, so the rig asks for one. The thing that matters to the
worker survives: the coordinating node still waits for a real replica
acknowledgement, which a single node with no replicas would not do. Worker-side
figures from this rig are therefore optimistic.
"""

import argparse
import os
import re
import sys

import yaml
from jinja2 import Environment, StrictUndefined

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLE = os.path.join(REPO, "deploy", "roles", "sweet_opensearch", "templates")
GROUP_VARS = os.path.join(REPO, "deploy", "group_vars", "all.yml")


def ternary(value, when_true, when_false):
    return when_true if value else when_false


def to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_vars():
    with open(GROUP_VARS) as handle:
        return yaml.safe_load(handle) or {}


def render(name, variables):
    with open(os.path.join(ROLE, name)) as handle:
        source = handle.read()
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    env.filters["bool"] = to_bool
    env.filters["ternary"] = ternary
    return env.from_string(source).render(**variables)


def set_replicas(text, replicas):
    """The rig's one divergence. Every replica count lives in schema/."""
    return re.sub(r'("number_of_replicas"\s*:\s*)\d+', r"\g<1>%d" % replicas, text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--node-id", default="node-soak")
    parser.add_argument("--replicas", type=int, default=1,
                        help="the rig has two storage nodes, not three")
    parser.add_argument("--template", default="templates.sh.j2")
    args = parser.parse_args()

    variables = load_vars()
    variables["opensearch_bootstrap_worker_node_ids"] = [args.node_id]
    variables.setdefault("log_rollover_migrate_existing", True)

    try:
        text = render(args.template, variables)
    except Exception as error:  # noqa: BLE001 - report the missing name plainly
        print("mkbootstrap: %s needs a variable group_vars/all.yml does not "
              "define: %s" % (args.template, error), file=sys.stderr)
        return 1

    with open(args.out, "w") as handle:
        handle.write(text)
    os.chmod(args.out, 0o755)

    # templates.sh reads every request body from "$(dirname $0)/schema", so the
    # rig gets the same directory the deploy stages.
    schema_out = os.path.join(os.path.dirname(os.path.abspath(args.out)),
                              "schema")
    os.makedirs(schema_out, exist_ok=True)

    # One rendered index template per worker, the same file the deploy stages
    # and the worker's boot-time self-heal reads.
    per_worker = os.path.join(ROLE, "schema-per-worker",
                              "index-logs-application-local.json.j2")
    for node in variables["opensearch_bootstrap_worker_node_ids"]:
        body = render(os.path.join("schema-per-worker",
                                   "index-logs-application-local.json.j2"),
                      dict(variables, node=node))
        if args.replicas is not None:
            body = set_replicas(body, args.replicas)
        with open(os.path.join(schema_out,
                               "index-logs-application-local-%s.json" % node),
                  "w") as handle:
            handle.write(body)

    schema_source = os.path.join(ROLE, "schema")
    for name in sorted(os.listdir(schema_source)):
        if not name.endswith(".json.j2"):
            continue
        try:
            body = render(os.path.join("schema", name), variables)
        except Exception as error:  # noqa: BLE001 - report the missing name plainly
            print("mkbootstrap: %s needs a variable group_vars/all.yml does "
                  "not define: %s" % (name, error), file=sys.stderr)
            return 1
        if args.replicas is not None:
            body = set_replicas(body, args.replicas)
        with open(os.path.join(schema_out, name[:-len(".j2")]), "w") as handle:
            handle.write(body)

    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
