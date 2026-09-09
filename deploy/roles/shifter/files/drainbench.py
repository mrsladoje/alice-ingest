#!/usr/bin/env python3
"""Mine templates out of a corpus written by corpus.py and price the work.

Two numbers come out of this, and they answer different questions.

The new-template rate decides whether round 3 is affordable: an embedding is
paid once per template, so a rate that keeps falling means the cost is bounded
and a rate that stays flat means it is not. It is reported per decile, because
the rate over the whole corpus is dominated by the first few thousand lines and
tells you nothing about the steady state.

The per-line cost is paid on every line forever. It is measured as processor
time, not wall time, and mining is timed apart from reading so that a slow disk
is not billed to Drain.
"""
import argparse
import json
import os
import re
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter, defaultdict
from itertools import compress
from operator import eq, ne

from drain3 import TemplateMiner
from drain3.drain import Drain, LogCluster
from drain3.template_miner_config import TemplateMinerConfig


from masking import REFERENCE as MASKING, mask as fast_mask


RECIPE_DEPTH = 8
RECIPE_MAX_CHILDREN = 100
# What the collector removes before a line ever reaches the miner. Each entry
# mirrors one parser in deploy/roles/sweet_collector/templates/parsers.yaml.j2, and it
# has to: mining the envelope again turns a clock into template tokens and makes
# every minute its own template.
#
# `stdout` is the pre-4-September family, kept so the round 6 figures can still
# be reproduced. The tree is two formats and they are now mined apart, because
# DataDistribution's own bracket carries a full date that the DPL strip rule
# never removed.
RECIPE_STRIP = {
    "stdout": re.compile(r"^\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]\[[A-Za-z]+\]\s*"),
    "dpl": re.compile(r"^\[(?:\x1b\[[0-9;]*m)?\d{1,2}:\d{2}:\d{2}(?:\.\d+)?(?:\x1b\[[0-9;]*m)?\]\[(?:\x1b\[[0-9;]*m)?[A-Za-z]+(?:\x1b\[[0-9;]*m)?\]\s*"),
    "datadist": re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}\.\d+\]\[[A-Z]\]\s*"),
    "dds": re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+[a-z]{3}\s+"),
    # Whitespace, not a tab. docs/LOG_TYPES.md recorded a tab and the 5 September
    # 2026 census read the real file: it is spaces.
    "ildaemon": re.compile(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}\.\d+\s+"),
    # Mirrors the `odc` parser: date, severity, program, process identifier,
    # then the optional partition and run number the collector captures into
    # their own fields.
    "odc": re.compile(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}\.\d+\s+[a-z]{3}\s+\S+\s+\d+\s+(?:[A-Za-z0-9]+:\d+\s+)?"),
    "journald": None,
    "infologger": None,
}
# Measured by tools/templating/recipesweep.py. Neither format in the process tree
# pads anything, which reverses round 6's setting for the family they came from.
# Round 6 compared pad sets on words kept WITHOUT the clock strip in front; with
# the strip in place words kept is 99.8 % in every cell and cannot separate them,
# so the trade is cost against template count. Not padding is 36 % cheaper on
# `dpl` and 40 % cheaper on `datadist`. See docs/SOAK_RESULTS.md.
#
# `ildaemon` and `journald` were measured on 5 September 2026 against corpora
# captured from the farm: 163,670 daemon lines from epn146 and 277,541 journal
# entries from the three workers.
#
# `ildaemon` produced the same 18 templates in all 32 cells with 100 % of words
# kept, because the file has exactly two shapes. Only cost moved, so it pads
# nothing.
#
# `odc` was measured on 6 September 2026 against 190,250 real orchestrator lines
# — a busy day carrying two runs and an idle day of status polls. It is the one
# family where padding is better on BOTH quality axes at once: `= ; :` reads
# 98.7 % of words against 94.5 % unpadded AND produces fewer templates, 266
# against 276. The orchestrator writes `key: value` everywhere — `exit code: 1`,
# `id: 3340879470082071756`, `path: "main/..."` — so separating the colon keeps
# the key literal instead of letting it merge into the value.
#
# It costs 24.4 core-seconds per million against 6.3 unpadded, and that is
# accepted rather than traded away. The source writes about 100,000 lines a day
# on ONE machine, so the whole family costs roughly 2.4 processor-seconds a day.
# Cost is the binding constraint on `dpl`, which is millions of lines; here it
# is not, and buying readability with it is the right way round.
#
# Numeric tokens are parametrised: 266 templates against 397 with them kept. The
# numbers that matter — the run number and the partition — are already their own
# fields on the record, so keeping them in the template text only splits one
# failure shape across every run it happened in.
#
# `journald` keeps numeric tokens, which no other family but dds does. Keeping
# them reads 96.8 % of words against 89.1 % parametrised, for 1,362 templates
# against 554 — and on a source writing a few thousand entries a day that is a
# trade worth making. The shipped guess before the measurement was `= ; :` with
# parametrised numbers, and the measured recipe beats it on every axis at once:
# cheaper, more readable, fewer contentless templates.
RECIPE_PAD = {"stdout": "=;", "dpl": "", "datadist": "",
              "infologger": "=;:", "dds": "=", "ildaemon": "",
              "journald": "=", "odc": "=;:"}
RECIPE_SIM = {"infologger": 0.4, "stdout": 0.4, "dpl": 0.4, "datadist": 0.4,
              "dds": 0.5, "ildaemon": 0.4, "journald": 0.4, "odc": 0.4}
RECIPE_NUMERIC = {"infologger": True, "stdout": True, "dpl": True,
                  "datadist": True, "dds": False, "ildaemon": True,
                  "journald": False, "odc": True}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_PLAIN_CREATE_TEMPLATE = Drain.create_template
_PLAIN_GET_SEQ_DISTANCE = Drain.get_seq_distance
_PLAIN_FAST_MATCH = Drain.fast_match
_PADS = {}


def _merged_create_template(self, seq1, seq2):
    """Keep <FLOAT> and <NUM> apart, and merge them only where one slot holds both.

    Without this the two masks cannot coexist: the first line whose slot sees a
    float and an integer wildcards the whole position and takes the neighbouring
    words with it. Round 6 measured 6.3 % of stdout lines landing on a template
    containing <*> without the patch and 1.0 % with it, while 258 templates still
    show a real <FLOAT>. It is Python rather than a drain3 masking rule, so it
    cannot be expressed as configuration and has to travel with our own code.

    The slots that differ are found with compress over map(ne), and only those
    are visited; the result is the same list the per-slot loop built."""
    ret_val = list(seq2)
    changed = list(compress(range(len(ret_val)), map(ne, seq1, seq2)))
    if not changed:
        return ret_val
    param = self.param_str
    for i in changed:
        t1 = seq1[i]
        t2 = seq2[i]
        if "<FLOAT>" in t1 or "<FLOAT>" in t2:
            folded = t1.replace("<FLOAT>", "<NUM>")
            if folded == t2.replace("<FLOAT>", "<NUM>"):
                ret_val[i] = folded
                continue
        ret_val[i] = param
    return ret_val


def _get_seq_distance(self, seq1, seq2, include_params):
    """drain3's similarity, with the two per-token loops as builtins.

    The reference skips a slot whose template token is the wildcard even when
    the log token is the same wildcard, so those slots are subtracted back out
    of the equality count, and only when the log line carries one at all. Same
    integers, same float, same tie-break."""
    assert len(seq1) == len(seq2)
    n = len(seq1)
    if n == 0:
        return 1.0, 0
    param = self.param_str
    param_count = seq1.count(param)
    sim_tokens = sum(map(eq, seq1, seq2))
    if param_count and param in seq2:
        sim_tokens -= sum(1 for a, b in zip(seq1, seq2) if a == param and b == param)
    if include_params:
        sim_tokens += param_count
    return float(sim_tokens) / n, param_count


def _fast_match(self, cluster_ids, tokens, sim_th, include_params):
    """drain3's best-match loop with the distance inlined and one early exit.

    With include_params False a candidate whose every slot equals the line and
    whose template holds no wildcard scores 1.0 with zero parameters. No later
    candidate can beat 1.0, and a tie needs more parameters, which a 1.0 score
    with parameters uncounted rules out; a tie on both goes to list order in
    the reference, which returning here honours. A line repeating a static
    message therefore stops at its first candidate."""
    max_sim = -1
    max_param_count = -1
    max_cluster = None
    get = self.id_to_cluster.get
    param = self.param_str
    n = len(tokens)
    param_in_tokens = param in tokens
    for cluster_id in cluster_ids:
        cluster = get(cluster_id)
        if cluster is None:
            continue
        seq1 = cluster.log_template_tokens
        assert len(seq1) == n
        if n == 0:
            cur_sim, param_count = 1.0, 0
        else:
            param_count = seq1.count(param)
            sim_tokens = sum(map(eq, seq1, tokens))
            if param_count and param_in_tokens:
                sim_tokens -= sum(1 for a, b in zip(seq1, tokens) if a == param and b == param)
            if include_params:
                sim_tokens += param_count
            elif sim_tokens == n:
                return cluster
            cur_sim = float(sim_tokens) / n
        if cur_sim > max_sim or (cur_sim == max_sim and param_count > max_param_count):
            max_sim = cur_sim
            max_param_count = param_count
            max_cluster = cluster
    if max_sim >= sim_th:
        return max_cluster
    return None


def _add_tokens(self, content_tokens):
    """drain3's add_log_message on a token list, the merge rule applied in place.

    The wrapper splits the string the recipe just joined, makes profiler calls,
    and for every matched line builds a new template, tuples it and compares it
    with the old one even when no slot differed. This does the same search and
    the same cluster creation, and rebuilds the template only when a slot
    actually differs. The tuple comparison stays for the rebuilt case, because
    the merge can rebuild a slot to the value it already had."""
    match_cluster = self.tree_search(self.root_node, content_tokens, self.sim_th, False)
    if match_cluster is None:
        self.clusters_counter += 1
        cluster_id = self.clusters_counter
        match_cluster = LogCluster(content_tokens, cluster_id)
        self.id_to_cluster[cluster_id] = match_cluster
        self.add_seq_to_prefix_tree(self.root_node, match_cluster)
        return match_cluster, "cluster_created"
    template = match_cluster.log_template_tokens
    changed = list(compress(range(len(template)), map(ne, content_tokens, template)))
    update_type = "none"
    if changed:
        new_template = list(template)
        param = self.param_str
        for i in changed:
            t1 = content_tokens[i]
            t2 = template[i]
            if "<FLOAT>" in t1 or "<FLOAT>" in t2:
                folded = t1.replace("<FLOAT>", "<NUM>")
                if folded == t2.replace("<FLOAT>", "<NUM>"):
                    new_template[i] = folded
                    continue
            new_template[i] = param
        new_template = tuple(new_template)
        if new_template != template:
            match_cluster.log_template_tokens = new_template
            update_type = "cluster_template_changed"
    match_cluster.size += 1
    self.id_to_cluster[match_cluster.cluster_id]
    return match_cluster, update_type


def install_merged_create_template():
    """Make the FLOAT/NUM merge the active rule for every miner in this process.

    `recipe_run` sets it for the length of one offline run and puts the plain
    rule back. Anything else mining with the frozen recipe — the on-node catalog
    service above all — has to install it too, or it mines the same lines into
    different templates than the archive does and the two catalogs disagree
    without either being wrong on its own terms.

    The similarity and best-match rewrites ride along: they return what drain3's
    own return, verified line by line on every family, and cost half as much."""
    Drain.create_template = _merged_create_template
    Drain.get_seq_distance = _get_seq_distance
    Drain.fast_match = _fast_match
    Drain.add_tokens = _add_tokens


def restore_plain_drain():
    Drain.create_template = _PLAIN_CREATE_TEMPLATE
    Drain.get_seq_distance = _PLAIN_GET_SEQ_DISTANCE
    Drain.fast_match = _PLAIN_FAST_MATCH


def recipe_miner(family, persistence=None):
    """One miner carrying the configuration round 6 stage H froze for this family.

    The three families want three different answers to the same knobs, which is
    what justifies splitting the configuration at all. InfoLogger is full of
    `key: value` and wants the colon padded; stdout is hurt by it; dds wants
    neither colon nor semicolon because in a shell command line `;` separates
    real commands. dds also reads the whole line by similarity rather than by
    prefix depth, because its distinguishing word sits past token 50."""
    tm = miner(RECIPE_SIM[family], RECIPE_DEPTH, RECIPE_MAX_CHILDREN,
                persistence=persistence)
    tm.masker.masking_instructions = []
    tm.masker.mask = _already_masked
    tm.drain.parametrize_numeric_tokens = RECIPE_NUMERIC[family]
    return tm


def _already_masked(line):
    """The miner must not mask, because the recipe masks and then pads.

    `miner` binds the fast masker onto the miner so the ordinary path gets it for
    free. The recipe cannot use that: it masks first and pads the separators of
    the masked line afterwards, so leaving the binding in place runs the masker a
    second time over a string whose character context has already moved. It is
    close enough to idempotent to hide — `infologger` and `dds` reproduced stage H
    to the template with the second pass in place, and `stdout` came out 981
    against 1,004."""
    return line


def _pads(pad):
    pads = _PADS.get(pad)
    if pads is None:
        pads = _PADS[pad] = tuple((c, " " + c + " ") for c in pad)
    return pads


def recipe_tokens(family, message):
    """Strip the envelope, mask, pad, and split: the token list the miner sees.

    The strip patterns are anchored and cannot match empty, so match() and a
    slice find the one replacement sub() could ever make. Each padded separator
    is a str.replace, and the whitespace collapse the old form did with \s+ is
    what split() does anyway; \s in a str pattern, split() with no separator and
    strip() with no argument all use the same whitespace predicate."""
    strip = RECIPE_STRIP[family]
    if strip is not None:
        m = strip.match(message)
        if m is not None:
            message = message[m.end():]
    message = fast_mask(message)
    pad = RECIPE_PAD[family]
    if pad:
        for ch, padded in _pads(pad):
            message = message.replace(ch, padded)
    return message.split()


def recipe_prepare(family, message):
    """The same line as one string, for the callers that want text."""
    strip = RECIPE_STRIP[family]
    if strip is not None:
        m = strip.match(message)
        if m is not None:
            message = message[m.end():]
    message = fast_mask(message)
    pad = RECIPE_PAD[family]
    if pad:
        for ch, padded in _pads(pad):
            message = message.replace(ch, padded)
        message = " ".join(message.split())
    return message


def mine(tm, tokens):
    """Add one prepared line to a recipe miner and return its cluster.

    Straight to the Drain: TemplateMiner.add_log_message would mask a line the
    recipe has already masked, split the string the recipe just joined, join the
    template and build a result dict for every line, and with a persistence
    handler attached it snapshots the whole tree."""
    return tm.drain.add_tokens(tokens)[0]


def recipe_run(paths, families, limit, retention_stride):
    """Mine every corpus with the frozen recipe and keep the source label.

    Stage I needs one template set, not one per corpus, so the trees carry across
    the files and each file's contribution is recorded as it lands. The template
    count is a curve rather than a number — round 6 mined one run tag of 86 and
    forty InfoLogger partitions of 179 — so what a corpus adds on top of the one
    before it is itself the reading."""
    install_merged_create_template()
    try:
        miners = {f: recipe_miner(f) for f in families}
        sources = {f: defaultdict(Counter) for f in families}
        cpu = dict.fromkeys(families, 0.0)
        lines = Counter()
        kept = Counter()
        words = Counter()
        first_phase = {}
        phases = []
        for phase, path in enumerate(paths, 1):
            before = {f: len(miners[f].drain.clusters) for f in families}
            before_cpu = dict(cpu)
            phase_lines = Counter()
            with open(path, "r", errors="replace") as fh:
                for raw in fh:
                    parts = raw.rstrip("\n").split("\t", 2)
                    if len(parts) != 3:
                        continue
                    family, source, message = parts
                    tm = miners.get(family)
                    if tm is None:
                        continue
                    if limit and phase_lines[family] >= limit:
                        continue
                    t0 = time.process_time()
                    tokens = recipe_tokens(family, message)
                    cluster = mine(tm, tokens)
                    cpu[family] += time.process_time() - t0
                    lines[family] += 1
                    phase_lines[family] += 1
                    key = (family, cluster.cluster_id)
                    sources[family][cluster.cluster_id]["%s/%s" % (family, source)] += 1
                    if key not in first_phase:
                        first_phase[key] = phase
                    if retention_stride and lines[family] % retention_stride == 0:
                        mined = set(_WORD.findall(cluster.get_template()))
                        for word in _WORD.findall(" ".join(tokens)):
                            words[family] += 1
                            if word in mined:
                                kept[family] += 1
            phases.append({
                "corpus": path,
                "lines": dict(phase_lines),
                "templates_after": {f: len(miners[f].drain.clusters) for f in families},
                "templates_added": {f: len(miners[f].drain.clusters) - before[f]
                                    for f in families},
                "core_seconds_per_million": {
                    f: round(1e6 * (cpu[f] - before_cpu[f]) / phase_lines[f], 2)
                    for f in families if phase_lines[f]},
            })
        rows = []
        per_family = {}
        for family in families:
            tm = miners[family]
            per_family[family] = {
                "lines": lines[family],
                "templates": len(tm.drain.clusters),
                "core_seconds_per_million": (round(1e6 * cpu[family] / lines[family], 2)
                                             if lines[family] else None),
                "words_kept_pct": (round(100.0 * kept[family] / words[family], 1)
                                   if words[family] else None),
            }
            for cluster in sorted(tm.drain.clusters, key=lambda c: c.size, reverse=True):
                owner = sources[family][cluster.cluster_id]
                rows.append({
                    "count": cluster.size,
                    "family": family,
                    "source": (owner.most_common(1) or [("%s/?" % family, 0)])[0][0],
                    "sources": len(owner),
                    "template": cluster.get_template(),
                    "phase": first_phase[(family, cluster.cluster_id)],
                })
        rows.sort(key=lambda r: r["count"], reverse=True)
        return {
            "corpora": list(paths),
            "recipe": {"depth": RECIPE_DEPTH, "max_children": RECIPE_MAX_CHILDREN,
                       "sim_threshold": RECIPE_SIM, "parametrize_numeric_tokens": RECIPE_NUMERIC,
                       "padded_separators": RECIPE_PAD},
            "per_family": per_family,
            "phases": phases,
            "lines": sum(lines.values()),
            "templates": len(rows),
            "peak_rss_bytes": (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                               * (1 if sys.platform == "darwin" else 1024)),
            "all_templates": rows,
        }
    finally:
        restore_plain_drain()


def miner(sim_threshold, depth, max_children, persistence=None):
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_threshold
    config.drain_depth = depth
    config.drain_max_children = max_children
    config.drain_max_clusters = None
    config.masking_instructions = []
    config.profiling_enabled = False
    config.snapshot_interval_minutes = 0
    tm = TemplateMiner(persistence_handler=persistence, config=config)
    from drain3.masking import MaskingInstruction
    tm.masker.masking_instructions = [
        MaskingInstruction(m["regex_pattern"], m["mask_with"]) for m in MASKING
    ]
    tm.masker.mask = fast_mask
    return tm


def cost_split(messages, sim_threshold, depth, max_children):
    """Price masking apart from the tree, on the same lines.

    The third figure is the one worth reading: with masking removed every
    distinct number becomes a distinct token, the tree grows clusters it should
    never have had, and the similarity search walks all of them. Masking is not
    an overhead the tree pays — it is what keeps the tree cheap."""
    masked = miner(sim_threshold, depth, max_children)
    t0 = time.process_time()
    for m in messages:
        masked.masker.mask(m)
    mask_only = time.process_time() - t0

    full = miner(sim_threshold, depth, max_children)
    t0 = time.process_time()
    for m in messages:
        full.add_log_message(m)
    full_cost = time.process_time() - t0

    bare = miner(sim_threshold, depth, max_children)
    bare.masker.masking_instructions = []
    t0 = time.process_time()
    for m in messages:
        bare.add_log_message(m)
    bare_cost = time.process_time() - t0

    n = len(messages) or 1
    return {
        "lines": len(messages),
        "mask_only_core_seconds_per_million": round(1e6 * mask_only / n, 2),
        "full_core_seconds_per_million": round(1e6 * full_cost / n, 2),
        "unmasked_core_seconds_per_million": round(1e6 * bare_cost / n, 2),
        "templates_masked": len(full.drain.clusters),
        "templates_unmasked": len(bare.drain.clusters),
    }


def run(path, family_filter, sim_threshold, depth, max_children, limit, deciles):
    tm = miner(sim_threshold, depth, max_children)
    lines = 0
    read_lines = 0
    new_templates = 0
    per_family = Counter()
    per_family_templates = defaultdict(set)
    cluster_sources = defaultdict(Counter)
    curve = []
    mining_cpu = 0.0
    started = time.process_time()
    decile_mark = 0

    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            read_lines += 1
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            family, source, message = parts
            if family_filter and family != family_filter:
                continue
            t0 = time.process_time()
            result = tm.add_log_message(message)
            mining_cpu += time.process_time() - t0
            lines += 1
            per_family[family] += 1
            per_family_templates[family].add(result["cluster_id"])
            cluster_sources[result["cluster_id"]]["%s/%s" % (family, source)] += 1
            if result["change_type"] == "cluster_created":
                new_templates += 1
            if deciles and lines % deciles == 0:
                curve.append({
                    "lines": lines,
                    "templates": len(tm.drain.clusters),
                    "new_in_block": len(tm.drain.clusters) - decile_mark,
                    "cpu_seconds": round(mining_cpu, 3),
                })
                decile_mark = len(tm.drain.clusters)
            if limit and lines >= limit:
                break

    total_cpu = time.process_time() - started
    templates = len(tm.drain.clusters)
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak_rss *= 1024
    return {
        "corpus": path,
        "family_filter": family_filter or "all",
        "lines": lines,
        "templates": templates,
        "sim_threshold": sim_threshold,
        "depth": depth,
        "max_children": max_children,
        "template_rate_per_million": round(1e6 * templates / lines, 1) if lines else None,
        "mining_core_seconds_per_million": round(1e6 * mining_cpu / lines, 2) if lines else None,
        "loop_core_seconds_per_million": (
            round(1e6 * total_cpu / lines, 2)
            if lines and not family_filter else None),
        "peak_rss_bytes": peak_rss,
        "read_lines": read_lines,
        "per_family_lines": dict(per_family),
        "per_family_templates": {k: len(v) for k, v in per_family_templates.items()},
        "curve": curve,
        "all_templates": [
            {"count": c.size, "id": c.cluster_id, "template": c.get_template(),
             "source": (cluster_sources[c.cluster_id].most_common(1) or [("?", 0)])[0][0],
             "sources": len(cluster_sources[c.cluster_id])}
            for c in sorted(tm.drain.clusters, key=lambda c: c.size, reverse=True)
        ],
        "top_templates": [
            {"count": c.size, "template": c.get_template()}
            for c in sorted(tm.drain.clusters, key=lambda c: c.size, reverse=True)[:25]
        ],
    }


def per_source(path, family_filter, sim_threshold, depth, max_children, limit):
    """One tree per source against one tree for everything.

    A per-source tree cannot confuse two programs that happen to share a
    prefix, but it also cannot share a template between them, and every worker
    would hold as many trees as it sees programs. The comparison is here so the
    sidecar's shape is a measured choice rather than a habit."""
    miners = {}
    lines = 0
    cpu = 0.0
    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            family, source, message = parts
            if family_filter and family != family_filter:
                continue
            key = "%s/%s" % (family, source)
            tm = miners.get(key)
            if tm is None:
                tm = miners[key] = miner(sim_threshold, depth, max_children)
            t0 = time.process_time()
            tm.add_log_message(message)
            cpu += time.process_time() - t0
            lines += 1
            if limit and lines >= limit:
                break
    total = sum(len(tm.drain.clusters) for tm in miners.values())
    return {
        "lines": lines,
        "trees": len(miners),
        "templates": total,
        "core_seconds_per_million": round(1e6 * cpu / lines, 2) if lines else None,
    }


def main_recipe(args):
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    report = recipe_run(args.corpus, families, args.limit, args.retention_stride)
    if args.dump_templates:
        with open(args.dump_templates, "w") as fh:
            for row in report["all_templates"]:
                fh.write("%d\t%s\t%d\t%s\t%d\n" % (
                    row["count"], row["source"], row["sources"],
                    row["template"], row["phase"]))
    report.pop("all_templates", None)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="+")
    ap.add_argument("--recipe", action="store_true",
                    help="mine with the per-family configuration round 6 stage H froze, "
                         "carrying one tree per family across every corpus named")
    ap.add_argument("--families", default="infologger,stdout,dds")
    ap.add_argument("--retention-stride", type=int, default=20,
                    help="sample one line in this many for the words-kept figure")
    ap.add_argument("--family", default="")
    ap.add_argument("--sim-threshold", type=float, default=0.4)
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--max-children", type=int, default=100)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--block", type=int, default=100000)
    ap.add_argument("--json", default="")
    ap.add_argument("--dump-templates", default="",
                    help="write every mined template, count first, to this path")
    ap.add_argument("--per-source", action="store_true",
                    help="also mine one tree per source and compare")
    ap.add_argument("--split-cost", type=int, default=0,
                    help="price masking apart from the tree over this many lines")
    args = ap.parse_args()

    if args.recipe:
        return main_recipe(args)

    report = run(args.corpus[0], args.family, args.sim_threshold, args.depth,
                 args.max_children, args.limit, args.block)
    if args.split_cost:
        messages = []
        with open(args.corpus[0], "r", errors="replace") as fh:
            for raw in fh:
                parts = raw.rstrip("\n").split("\t", 2)
                if len(parts) != 3:
                    continue
                if args.family and parts[0] != args.family:
                    continue
                messages.append(parts[2])
                if len(messages) >= args.split_cost:
                    break
        report["cost_split"] = cost_split(messages, args.sim_threshold,
                                          args.depth, args.max_children)
    if args.per_source:
        report["per_source"] = per_source(args.corpus[0], args.family,
                                          args.sim_threshold, args.depth,
                                          args.max_children, args.limit)
    if args.dump_templates:
        with open(args.dump_templates, "w") as fh:
            for row in report["all_templates"]:
                fh.write("%d\t%s\t%d\t%s\n" % (
                    row["count"], row["source"], row["sources"], row["template"]))
    report.pop("all_templates", None)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("curve", "top_templates")}, indent=2))
    print("\nblocks of %d lines: new templates" % args.block, file=sys.stderr)
    for point in report["curve"]:
        print("  %9d  %6d total  %5d new" % (
            point["lines"], point["templates"], point["new_in_block"]), file=sys.stderr)


if __name__ == "__main__":
    main()
