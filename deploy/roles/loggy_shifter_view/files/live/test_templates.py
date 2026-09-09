import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = os.path.abspath(os.path.join(HERE, "..", ".."))
REPO = os.path.abspath(os.path.join(ROLE, "..", "..", ".."))

TEMPLATES_JS = os.path.join(HERE, "templates.js")
SHIFTER_JS = os.path.join(HERE, "shifter.js")
SHIFTER_CSS = os.path.join(HERE, "shifter.css")
INDEX_J2 = os.path.join(ROLE, "templates", "shifter-index.html.j2")
TASKS = os.path.join(ROLE, "tasks", "main.yml")
CONTRACT_MD = os.path.join(REPO, "docs", "COCKPIT_CONTRACT.md")
SHARED = os.path.join(REPO, "deploy", "shared")

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

function balanced(text, start) {
  let depth = 0;
  for (let i = text.indexOf('{', start); i < text.length; i += 1) {
    if (text[i] === '{') { depth += 1; }
    else if (text[i] === '}') {
      depth -= 1;
      if (depth === 0) { return text.slice(start, i + 1); }
    }
  }
  throw new Error('unbalanced source at ' + start);
}

function named(text, name) {
  const at = text.indexOf('function ' + name + '(');
  if (at < 0) { throw new Error('no function ' + name); }
  return balanced(text, at);
}

const templatesSource = fs.readFileSync(process.argv[2], 'utf8');
const shifterSource = fs.readFileSync(process.argv[3], 'utf8');
const script = fs.readFileSync(process.argv[4], 'utf8');
const which = process.argv[5] || 'shifter';

const listeners = [];
const created = [];
const sandbox = {
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval
};
sandbox.window = {
  SHIFTER_CONFIG: {},
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
  addEventListener: function (name) { listeners.push(name); },
  removeEventListener: function () { return null; },
  fetch: function () { throw new Error('the harness makes no request'); }
};
sandbox.document = {
  visibilityState: 'visible',
  addEventListener: function (name) { listeners.push(name); },
  removeEventListener: function () { return null; },
  createElement: function (tag) { created.push(tag); return {}; },
  querySelector: function () { return null; },
  head: { appendChild: function () { return null; } },
  title: 'harness'
};
sandbox.React = {
  createElement: function (type) {
    const props = arguments[1] || {};
    const kids = Array.prototype.slice.call(arguments, 2);
    return { type: typeof type === 'function' ? (type.name || 'component')
                                              : type,
             raw: type, props: props, children: kids, isElement: true };
  },
  useState: function (initial) {
    const value = typeof initial === 'function' ? initial() : initial;
    return [value, function () { return null; }];
  },
  useEffect: function () { return null; },
  useLayoutEffect: function () { return null; },
  useRef: function (initial) { return { current: initial }; },
  useMemo: function (factory) { return factory(); },
  useCallback: function (fn) { return fn; }
};
sandbox.EventSource = function () {
  throw new Error('the templates page opened a live stream');
};

const context = vm.createContext(sandbox);
vm.runInContext(named(shifterSource, 'decodeCount'), context);
vm.runInContext('window.SHIFTER_DECODE_COUNT = decodeCount;', context);
if (which === 'contract') {
  const doc = fs.readFileSync(process.argv[6], 'utf8');
  const block = doc.split('```js')[1].split('```')[0];
  vm.runInContext(block.replace('function decodeCount',
                                'function contractDecodeCount'), context);
}
vm.runInContext(templatesSource, context);
vm.runInContext(script, context);
process.stdout.write(JSON.stringify(context.window.__out));
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed, so the page cannot be evaluated")
    path = tmp_path_factory.mktemp("harness") / "harness.js"
    path.write_text(HARNESS)
    return str(path)


def run_js(harness, tmp_path, script, which="shifter"):
    path = _write(tmp_path, "script.js", script)
    argv = [NODE, harness, TEMPLATES_JS, SHIFTER_JS, path, which]
    if which == "contract":
        argv.append(CONTRACT_MD)
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr.strip() or "node refused the page")
    return json.loads(out.stdout)


def source(path):
    with open(path) as handle:
        return handle.read()


def test_the_page_registers_itself_and_opens_no_stream(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      window.__out = {
        keys: Object.keys(window.SHIFTER_TEMPLATES).sort(),
        page: typeof window.SHIFTER_TEMPLATES.Page,
        view: window.SHIFTER_TEMPLATES.initialView()
      };
    """)
    assert out["keys"] == ["Page", "helpers", "initialView"]
    assert out["page"] == "function"
    assert out["view"] == {"query": "", "mode": "text", "sort": "volume",
                           "includeInactive": False, "watchedOnly": False,
                           "selected": None}


def test_the_page_renders_without_data(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var mod = window.SHIFTER_TEMPLATES;
      var tree = mod.Page({ view: mod.initialView(),
                            setView: function () { return null; },
                            onOpenLogs: function () { return null; } });
      var seen = [];
      var walk = function (node) {
        if (!node || !node.isElement) { return; }
        var name = (node.props && node.props.className) || '';
        if (name) { seen.push(name); }
        if (typeof node.raw === 'function') {
          walk(node.raw(node.props));
          return;
        }
        node.children.forEach(function (child) {
          if (Array.isArray(child)) { child.forEach(walk); } else { walk(child); }
        });
      };
      walk(tree);
      window.__out = { classes: seen };
    """)
    joined = " ".join(out["classes"])
    assert "tp-snap" in joined
    assert "tp-main" in joined
    assert "tp-drawer" not in joined


def test_counts_survive_the_browser_boundary(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        big: h.formatCount('9007199254740993'),
        bigger: h.formatCount('123456789012345678901234567890'),
        safe: h.formatCount(4211987),
        zero: h.formatCount(0),
        one: h.formatCount(7),
        grouped: h.groupDigits('1000'),
        negative: h.groupDigits('-1234567')
      };
    """)
    assert out["big"] == "9,007,199,254,740,993"
    assert out["bigger"] == "123,456,789,012,345,678,901,234,567,890"
    assert out["safe"] == "4,211,987"
    assert out["zero"] == "0"
    assert out["one"] == "7"
    assert out["grouped"] == "1,000"
    assert out["negative"] == "-1,234,567"


def test_an_unsafe_number_is_refused_rather_than_rounded(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      var thrown = '';
      try { h.formatCount(9007199254740993); }
      catch (err) { thrown = String(err.message); }
      window.__out = { thrown: thrown };
    """)
    assert "unsafe number" in out["thrown"]


def test_the_helper_matches_the_one_the_contract_publishes(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var cases = ['0', '7', '9007199254740993', '-12', 0, 7, -12,
                   9007199254740991, 9007199254740993, 1.5, true, null,
                   'twelve', ''];
      var run = function (fn) {
        return cases.map(function (value) {
          try { return 'ok:' + fn(value).toString(); }
          catch (err) { return 'throw'; }
        });
      };
      window.__out = { ours: run(decodeCount),
                       theirs: run(contractDecodeCount) };
    """, which="contract")
    assert out["ours"] == out["theirs"]
    assert "throw" in out["ours"]
    assert out["ours"][2] == "ok:9007199254740993"


def test_volume_states_are_visibly_different(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        exact: h.volumeOf({ count: 4211987, count_status: 'exact' }),
        zero: h.volumeOf({ count: 0, count_status: 'exact' }),
        incomplete: h.volumeOf({ count: '99', count_status: 'incomplete' }),
        incompleteZero: h.volumeOf({ count: 0, count_status: 'incomplete' }),
        unknown: h.volumeOf({ count: 0, count_status: 'unknown' }),
        unavailable: h.volumeOf({ count: 0, count_status: 'unavailable' })
      };
    """)
    kinds = [out[name]["kind"] for name in
             ("exact", "zero", "incomplete", "unknown", "unavailable")]
    assert len(set(kinds)) == 5
    texts = [out[name]["text"] for name in
             ("zero", "unknown", "unavailable")]
    assert len(set(texts)) == 3
    for name in out:
        assert "—" not in out[name]["text"]
    assert out["exact"]["text"] == "4,211,987"
    assert out["incomplete"]["text"] == "at least 99"
    assert out["incompleteZero"]["text"] == "0 counted"
    assert out["zero"]["kind"] == "zero"
    assert "published past the cutoff" in out["zero"]["note"]
    assert out["unknown"]["kind"] == "unknown"
    assert out["unavailable"]["kind"] == "unavailable"


def test_a_missing_cutoff_never_reads_as_zero(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = h.volumeOf({ count: 0, count_status: 'unknown' });
    """)
    assert out["text"] != "0"
    assert "0" not in out["text"]


def test_activity_states(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        active: h.activityOf({ active: true, historical: false }),
        inactive: h.activityOf({ active: false, historical: false }),
        historical: h.activityOf({ active: true, historical: true })
      };
    """)
    assert out["active"]["kind"] == "active"
    assert out["inactive"]["kind"] == "historical"
    assert out["historical"]["kind"] == "historical"


def test_coverage_names_the_behind_and_idle_nodes(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        complete: h.coverageOf({ status: 'complete', complete: ['a', 'b'],
                                 behind: [], idle: [] }),
        partial: h.coverageOf({ status: 'partial', complete: ['a'],
                                behind: ['b'], idle: ['c'] }),
        unknown: h.coverageOf({ status: 'unknown', complete: [],
                                behind: [], idle: [] }),
        nothing: h.coverageOf(null)
      };
    """)
    assert out["complete"]["kind"] == "complete"
    assert "all 2 nodes" in out["complete"]["text"]
    assert out["partial"]["behind"] == ["b"]
    assert out["partial"]["idle"] == ["c"]
    assert "1 of 3" in out["partial"]["text"]
    assert "behind: b" in out["partial"]["text"]
    assert "idle: c" in out["partial"]["text"]
    assert out["unknown"]["kind"] == "unknown"
    assert out["nothing"]["kind"] == "unavailable"


def test_the_list_request_matches_the_contract(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        first: h.requestOf({ query: 'gpu', mode: 'text', includeInactive: true,
                             watchedOnly: false, sort: 'last_observed' }, null),
        next: h.requestOf({ query: '', mode: 'semantic',
                            includeInactive: false, watchedOnly: true,
                            sort: 'volume' }, ['4211987', 'dpl:abc'])
      };
    """)
    published = json.loads(re.search(
        r"Request:\n\n```json\n(\{.*?\})\n```",
        source(CONTRACT_MD), re.S).group(1))
    assert sorted(out["first"]) == sorted(published)
    assert out["first"]["include_inactive"] is True
    assert out["first"]["sort"] == "last_observed"
    assert out["first"]["page_size"] <= 50
    assert out["next"]["after"] == ["4211987", "dpl:abc"]
    assert out["next"]["watched_only"] is True


def test_paging_never_repeats_a_version(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      var first = [{ version_id: 'a' }, { version_id: 'b' }];
      var second = [{ version_id: 'b' }, { version_id: 'c' }];
      window.__out = h.mergeRows(first, second).map(function (row) {
        return row.version_id;
      });
    """)
    assert out == ["a", "b", "c"]


def test_the_log_link_becomes_a_stamp_filter(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        stamp: h.logFilters({ criterias: { template_version: {
                                'in': ['dpl:a', 'dpl:b'] } },
                              options: { mode: 'wildcard', limit: 2000 } }),
        full: h.logFilters({ criterias: { message: { match: 'failed to' } },
                             options: { mode: 'wildcard', limit: 2000 } }),
        empty: h.logFilters({ criterias: {}, options: {} }),
        missing: h.logFilters(null)
      };
    """)
    assert out["stamp"]["templateVersions"] == ["dpl:a", "dpl:b"]
    assert out["stamp"]["fields"] == {}
    assert out["full"]["fields"]["message"]["match"] == "failed to"
    assert out["full"]["mode"] == "wildcard"
    assert out["full"]["limit"] == 2000
    assert out["empty"]["fields"] == {}
    assert out["missing"]["fields"] == {}


def test_the_logs_page_carries_the_stamp_filter_end_to_end():
    lane = source(SHIFTER_JS)
    assert "templateVersions: []" in lane
    assert "template_version: { in: (filters.templateVersions || [])" in lane
    assert "add('tv', filters.templateVersions.join(','))" in lane
    assert "if (key === 'tv') {" in lane
    assert "filters.templateVersions.indexOf(rec.template_version) === -1" \
        in lane
    assert "next.templateVersions = link.templateVersions.slice();" in lane
    assert "'stamp × ' + props.filters.templateVersions.length" in lane


def test_times_are_written_in_utc(harness, tmp_path):
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        stamp: h.stampOf(1788877200000),
        none: h.stampOf(0),
        iso: h.isoStamp('2026-09-08T14:20:00.000Z'),
        fresh: h.ageOf(42000),
        old: h.ageOf(7200000),
        unknown: h.ageOf(null),
        future: h.ageOf(-5000)
      };
    """)
    assert out["stamp"].endswith(" UTC")
    assert out["stamp"].startswith("2026-09-08 ")
    assert out["none"] == ""
    assert out["iso"] == "2026-09-08 14:20:00 UTC"
    assert out["fresh"] == "42 s ago"
    assert out["old"] == "2 h ago"
    assert out["unknown"] == "age not known"
    assert out["future"] == "0 s ago"


def test_the_page_mounts_no_second_live_stream():
    text = source(TEMPLATES_JS)
    assert "EventSource" not in text
    assert "/stream" not in text
    assert "streamUrl" not in text


def test_no_count_passes_through_a_float():
    text = source(TEMPLATES_JS)
    assert not re.search(r"(Number|parseInt|parseFloat)\s*\([^)]*count", text)
    assert not re.search(r"toLocaleString\s*\(\s*\)", text)
    assert "decodeCount" in text


def test_the_asset_loads_only_when_the_page_is_opened():
    shell = source(INDEX_J2)
    assert "templates.js" in shell
    assert '<script src="templates.js">' not in shell
    lane = source(SHIFTER_JS)
    assert "TEMPLATES_ASSET" in lane
    assert "createElement('script')" in lane


def test_the_contract_helper_lives_in_the_lane_page():
    lane = source(SHIFTER_JS)
    assert "function decodeCount(value)" in lane
    assert "window.SHIFTER_DECODE_COUNT = decodeCount;" in lane
    assert "window.SHIFTER_DECODE_COUNT" in source(TEMPLATES_JS)


def test_navigation_keeps_the_filter_link():
    lane = source(SHIFTER_JS)
    assert "function hashFor(page, filters)" in lane
    assert "function pageOfHash(raw)" in lane
    assert "hashFor(page, filters)" in lane


def test_polling_pauses_when_the_tab_is_hidden():
    text = source(TEMPLATES_JS)
    assert "visibilitychange" in text
    assert "document.visibilityState === 'hidden'" in text


def test_superseded_requests_are_discarded():
    text = source(TEMPLATES_JS)
    assert text.count("tokenRef.current !== token") >= 6


def test_every_gap_reason_has_words():
    import sys
    sys.path.insert(0, SHARED)
    import template_contract as contract

    block = re.search(r"var GAP_TEXT = \{(.*?)\n  \};",
                      source(TEMPLATES_JS), re.S).group(1)
    named = set(re.findall(r"^\s{4}([a-z_]+):", block, re.M))
    assert named == set(contract.GAP_REASONS)


def test_every_label_has_words():
    block = re.search(r"var LABEL_TEXT = \{(.*?)\n  \};",
                      source(TEMPLATES_JS), re.S).group(1)
    named = set(re.findall(r"^\s{4}([a-z_]+):", block, re.M))
    assert named == {"known_good", "known_bad", "needs_review", "noisy",
                     "watched"}


def test_the_sorts_are_the_three_the_contract_names():
    block = re.search(r"var SORTS = \[(.*?)\n  \];",
                      source(TEMPLATES_JS), re.S).group(1)
    assert set(re.findall(r"value: '([a-z_]+)'", block)) == {
        "volume", "last_observed", "first_catalogued"}


def test_inactive_history_is_off_until_it_is_asked_for():
    text = source(TEMPLATES_JS)
    assert "includeInactive: false" in text
    assert "include_inactive: view.includeInactive" in text


def test_the_deploy_ships_and_shrinks_the_new_asset():
    tasks = source(TASKS)
    assert tasks.count("    - templates.js") == 3
    assert "['shifter.js', 'templates.js', 'shifter.css']" in tasks


def test_the_stylesheet_uses_only_the_page_palette():
    styles = source(SHIFTER_CSS)
    block = styles[styles.index(".nav { gap: 0; }"):]
    for colour in re.findall(r"#[0-9a-fA-F]{3,8}", block):
        assert colour.lower() in {"#fff", "#ffffff", "#f6f8fa", "#8a5300",
                                  "#1d6f36", "#fbfdff"}, colour
    assert "gradient" not in block
    assert "box-shadow: -6px 0 18px rgba(0, 0, 0, 0.16)" in block


def test_the_expired_window_gets_its_own_warning_line():
    text = source(TEMPLATES_JS)
    assert "var expired = data.expired_window;" in text
    assert "'The newest cutoff every live node had published past is '" \
        in text
    assert "' minute ceiling. That window is history. This page serves no '" \
        in text


def test_the_page_shows_the_cutoff_and_names_the_nodes_behind_it():
    text = source(TEMPLATES_JS)
    assert "e('span', { className: 'tp-label' }, 'Cutoff')" in text
    assert "'Behind the cutoff: ' + coverage.behind.join(', ')" in text
    assert "'Idle: ' + coverage.idle.join(', ')" in text
    assert "days + '-day volume window'" in text
    assert "'28-day volume'" in text
    assert "'28 days'" in text
    for word in ("seven-day", "Seven-day", "snapshot", "manifest", "producer",
                 "awaiting_volume", "historical_programs", "superseded_by",
                 "supersedes", "relationship_verified", "'examples'",
                 "EXAMPLE_REASON", "central_history_lost"):
        assert word not in text, word


def test_the_lines_panel_replaces_the_examples(harness, tmp_path):
    text = source(TEMPLATES_JS)
    assert "postJson('lines', {" in text
    assert "include_ancestors: !!(options && options.includeAncestors)" in text
    assert "every_node: !!(options && options.everyNode)" in text
    assert "' Include ancestors, re-matched token by token'" in text
    assert "'Search every node'" in text
    assert "'covers ' + descendantCount + ' narrower version'" in text
    assert "e(Field, { label: 'widened into' }" in text
    assert "e(Field, { label: 'widened from' }" in text
    assert "'Lines are found by their stamp" in text
    out = run_js(harness, tmp_path, """
      var mod = window.SHIFTER_TEMPLATES;
      var tree = mod.Page({ view: Object.assign(mod.initialView(),
                                                { selected: 'dpl:abc' }),
                            setView: function () { return null; },
                            onOpenLogs: function () { return null; } });
      var seen = [];
      var walk = function (node) {
        if (!node || !node.isElement) { return; }
        var name = (node.props && node.props.className) || '';
        if (name) { seen.push(name); }
        if (typeof node.raw === 'function') {
          walk(node.raw(node.props));
          return;
        }
        node.children.forEach(function (child) {
          if (Array.isArray(child)) { child.forEach(walk); } else { walk(child); }
        });
      };
      walk(tree);
      window.__out = { classes: seen };
    """)
    assert "tp-drawer" in " ".join(out["classes"])


def test_the_catalogue_date_is_never_called_a_snapshot_field():
    text = source(TEMPLATES_JS)
    assert "catalogue date not in this snapshot" not in text
    assert "not carried by this snapshot" not in text
    assert "'no catalogue date is recorded'" in text
    assert "'the catalog holds no date for this version'" in text


def test_a_neighbour_is_labelled_a_suggestion_and_carries_no_volume():
    text = source(TEMPLATES_JS)
    assert "'Semantic suggestions'" in text
    assert "var suggestions = neighbours.suggestions || [];" in text
    assert "similarityOf(item.score)" in text
    block = text[text.index("'Semantic suggestions'"):
                 text.index("'Label history'")]
    assert "Volume" not in block
    assert "formatCount" not in block
    assert "No nearest neighbour is suggested for this template." in block


def test_the_suggestion_note_comes_from_the_server(harness, tmp_path):
    text = source(TEMPLATES_JS)
    assert "neighbours.note" in text
    out = run_js(harness, tmp_path, """
      var h = window.SHIFTER_TEMPLATES.helpers;
      window.__out = {
        near: h.similarityOf(0.9412),
        missing: h.similarityOf(null),
        text: h.similarityOf('not a number')
      };
    """)
    assert out["near"] == "similarity 0.94"
    assert out["missing"] == "no similarity"
    assert out["text"] == "no similarity"


def test_every_rendered_count_carries_its_state():
    text = source(TEMPLATES_JS)
    assert "count_status: totals.records_status || 'unknown'" in text
    assert "e(Volume, { row: counted })" in text
    assert "e(Volume, { row: groupCount })" in text
    assert "String(totals.records_status" not in text
    assert "String(group.count_status" not in text
    rendered = text.split("function volumeOf(row) {")[1]
    body = rendered.split("function activityOf(row) {")[1]
    assert "formatCount(" not in body.replace("formatCount: formatCount", "")
