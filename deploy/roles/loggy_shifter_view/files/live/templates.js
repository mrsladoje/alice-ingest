(function () {
  'use strict';

  var e = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;
  var useCallback = React.useCallback;

  var CONFIG = window.SHIFTER_CONFIG || {};
  var API = CONFIG.templatesUrl || 'api/templates/';
  var PAGE_ROWS = Math.max(1, Math.min(50, CONFIG.templatePageRows || 50));
  var COMPACT_ROWS = 5;
  var LINES_ROWS = 50;
  var SUMMARY_POLL_MS = 30000;
  var EPISODE_POLL_MS = 30000;
  var SEARCH_DEBOUNCE_MS = 250;

  var decodeCount = window.SHIFTER_DECODE_COUNT;

  var SORTS = [
    { value: 'volume', label: '28-day volume' },
    { value: 'last_observed', label: 'last observed' },
    { value: 'first_catalogued', label: 'first catalogued' }
  ];

  var GAP_TEXT = {
    missing_observation_time: 'records without a collection time',
    invalid_observation_time: 'records with an unreadable collection time',
    observation_time_beyond_tolerance:
      'records whose collection time is beyond the accepted tolerance',
    state_limit_reached: 'the worker state limit was reached',
    record_has_no_template:
      'a record produced no template text and was not counted',
    record_stamped_after_ledger_horizon:
      'records stamped after the 48-hour ledger horizon, counted in a late '
      + 'bucket',
    node_watermark_behind_cutoff:
      'the node has not published up to the cutoff',
    node_watermark_idle: 'the node has stopped publishing',
    publication_failed: 'a publication to the storage tier failed'
  };

  var LABEL_TEXT = {
    known_good: 'known good',
    known_bad: 'known bad',
    needs_review: 'needs review',
    noisy: 'noisy',
    watched: 'watched'
  };

  var SCOPE_TEXT = {
    reviewed: 'reviewed for this version and scope',
    new_source: 'a new source appeared since the review',
    broader_version: 'this version is broader than the reviewed one'
  };

  function groupDigits(text) {
    var sign = '';
    var body = text;
    if (body.charAt(0) === '-') { sign = '-'; body = body.slice(1); }
    var out = '';
    var size = body.length;
    for (var i = 0; i < size; i += 1) {
      out += body.charAt(i);
      var left = size - i - 1;
      if (left > 0 && left % 3 === 0) { out += ','; }
    }
    return sign + out;
  }

  function formatCount(value) {
    return groupDigits(decodeCount(value).toString());
  }

  function stampOf(ms) {
    if (!ms) { return ''; }
    var when = new Date(Number(ms));
    if (isNaN(when.getTime())) { return ''; }
    return when.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  }

  function similarityOf(score) {
    if (score == null || isNaN(Number(score))) { return 'no similarity'; }
    return 'similarity ' + Number(score).toFixed(2);
  }

  function isoStamp(text) {
    if (!text) { return ''; }
    return String(text).replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
  }

  function ageOf(ms) {
    if (ms == null) { return 'age not known'; }
    var seconds = Math.round(Number(ms) / 1000);
    if (seconds < 0) { seconds = 0; }
    if (seconds < 90) { return seconds + ' s ago'; }
    var minutes = Math.round(seconds / 60);
    if (minutes < 90) { return minutes + ' min ago'; }
    return Math.round(minutes / 60) + ' h ago';
  }

  function volumeOf(row) {
    var status = row.count_status;
    if (status === 'unavailable') {
      return { kind: 'unavailable', text: 'unavailable',
               note: 'no bucket documents are loaded for a cutoff' };
    }
    if (status === 'unknown') {
      return { kind: 'unknown', text: 'not known',
               note: 'no live node has published past a cutoff' };
    }
    var count = decodeCount(row.count);
    var zero = count === BigInt(0);
    if (status === 'exact') {
      if (zero) {
        return { kind: 'zero', text: '0',
                 note: 'every node with a bucket in the window has published '
                       + 'past the cutoff, so this zero is proved' };
      }
      return { kind: 'exact', text: formatCount(row.count), note: '' };
    }
    if (zero) {
      return { kind: 'incomplete', text: '0 counted',
               note: 'coverage is partial, so this is not a proved zero' };
    }
    return { kind: 'incomplete', text: 'at least ' + formatCount(row.count),
             note: 'coverage is partial for this cutoff' };
  }

  function activityOf(row) {
    if (row.historical || !row.active) {
      return { kind: 'historical', text: 'inactive' };
    }
    return { kind: 'active', text: 'active' };
  }

  function coverageOf(coverage) {
    var status = (coverage && coverage.status) || 'unavailable';
    var complete = (coverage && coverage.complete) || [];
    var behind = (coverage && coverage.behind) || [];
    var idle = (coverage && coverage.idle) || [];
    var total = complete.length + behind.length + idle.length;
    var text;
    if (status === 'complete') {
      text = 'complete — all ' + total + ' nodes published past the cutoff';
    } else if (status === 'partial') {
      text = 'partial — ' + complete.length + ' of ' + total + ' nodes';
      if (behind.length) { text += '; behind: ' + behind.join(', '); }
      if (idle.length) { text += '; idle: ' + idle.join(', '); }
    } else if (status === 'unknown') {
      text = 'unknown — no node has a bucket in this window';
    } else {
      text = 'unavailable — no cutoff is loaded';
    }
    return { kind: status, text: text, behind: behind, idle: idle,
             complete: complete };
  }

  function requestOf(view, after) {
    return {
      query: view.query,
      mode: view.mode,
      include_inactive: view.includeInactive,
      watched_only: view.watchedOnly,
      family: [],
      program: [],
      host: [],
      severity: [],
      sort: view.sort,
      page_size: PAGE_ROWS,
      after: after || null
    };
  }

  function mergeRows(previous, incoming) {
    var seen = {};
    var out = previous.slice();
    previous.forEach(function (row) { seen[row.version_id] = true; });
    incoming.forEach(function (row) {
      if (seen[row.version_id]) { return; }
      seen[row.version_id] = true;
      out.push(row);
    });
    return out;
  }

  function logFilters(link) {
    var criterias = (link && link.criterias) || {};
    var options = (link && link.options) || {};
    var out = { fields: {}, mode: options.mode || 'wildcard' };
    if (options.limit) { out.limit = options.limit; }
    Object.keys(criterias).forEach(function (key) {
      var spec = criterias[key] || {};
      if (key === 'template_version') {
        out.templateVersions = (spec['in'] || []).slice();
        return;
      }
      if (spec.match || spec.exclude) {
        out.fields[key] = { match: spec.match || '',
                            exclude: spec.exclude || '' };
      }
    });
    return out;
  }

  function readJson(response) {
    return response.json().catch(function () { return {}; })
      .then(function (data) { return { ok: response.ok, data: data }; });
  }

  function getJson(name) {
    return window.fetch(API + name, {
      headers: { Accept: 'application/json' }
    }).then(readJson);
  }

  function postJson(name, body) {
    return window.fetch(API + name, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(readJson);
  }

  function useEndpoint(name, intervalMs) {
    var state = useState({ status: 'loading', data: null, error: null });
    var setState = state[1];
    var tokenRef = useRef(0);

    var load = useCallback(function () {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      getJson(name).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState({ status: 'failed', data: null,
                     error: out.data.error || 'the request was refused' });
          return;
        }
        setState({ status: 'done', data: out.data, error: null });
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState({ status: 'failed', data: null, error: String(err) });
      });
    }, [name]);

    useEffect(function () {
      var timer = null;
      var stop = function () {
        if (timer) { window.clearInterval(timer); timer = null; }
      };
      var start = function () {
        if (timer) { return; }
        timer = window.setInterval(load, intervalMs);
      };
      var onVisibility = function () {
        if (document.visibilityState === 'hidden') { stop(); return; }
        load();
        start();
      };
      onVisibility();
      document.addEventListener('visibilitychange', onVisibility);
      return function () {
        document.removeEventListener('visibilitychange', onVisibility);
        stop();
      };
    }, [load, intervalMs]);

    return { state: state[0], reload: load };
  }

  var EMPTY_LIST = {
    status: 'idle', rows: [], after: null, hasMore: false, total: 0,
    totalRelation: 'eq', windowEnd: null, windowEndIso: '', coverage: null,
    semantic: null, queryId: null, note: '', tookMs: 0, error: null,
    loadingMore: false
  };

  function readPage(data) {
    return {
      status: 'done',
      rows: data.rows || [],
      after: data.after || null,
      hasMore: !!data.has_more,
      total: data.total || 0,
      totalRelation: data.total_relation || 'eq',
      windowEnd: data.window_end == null ? null : data.window_end,
      windowEndIso: data.window_end_iso || '',
      coverage: data.coverage || null,
      semantic: data.semantic || null,
      queryId: data.query_id || null,
      note: data.note || '',
      tookMs: data.took_ms || 0,
      error: null,
      loadingMore: false
    };
  }

  function useTemplateList(view, reloadKey) {
    var state = useState(EMPTY_LIST);
    var setState = state[1];
    var stateRef = useRef(EMPTY_LIST);
    var tokenRef = useRef(0);
    stateRef.current = state[0];
    var stamp = JSON.stringify(requestOf(view, null));

    var run = useCallback(function () {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      setState(function (prev) {
        return Object.assign({}, prev, { status: 'loading', error: null });
      });
      postJson('list', JSON.parse(stamp)).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState(Object.assign({}, EMPTY_LIST, {
            status: 'failed',
            error: out.data.error || 'the list was refused'
          }));
          return;
        }
        setState(readPage(out.data));
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState(Object.assign({}, EMPTY_LIST, {
          status: 'failed', error: String(err)
        }));
      });
    }, [stamp]);

    var more = useCallback(function () {
      var current = stateRef.current;
      var token = tokenRef.current;
      if (current.status !== 'done' || !current.hasMore || current.loadingMore
          || !current.after) {
        return;
      }
      setState(function (prev) {
        return Object.assign({}, prev, { loadingMore: true });
      });
      var request = JSON.parse(stamp);
      request.after = current.after;
      postJson('list', request).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState(function (prev) {
            return Object.assign({}, prev, {
              loadingMore: false, hasMore: false,
              error: out.data.error || 'the next page was refused'
            });
          });
          return;
        }
        var page = readPage(out.data);
        setState(function (prev) {
          return Object.assign({}, page, {
            rows: mergeRows(prev.rows, page.rows)
          });
        });
      }).catch(function () {
        if (tokenRef.current !== token) { return; }
        setState(function (prev) {
          return Object.assign({}, prev, { loadingMore: false });
        });
      });
    }, [stamp]);

    useEffect(function () { run(); }, [run, reloadKey]);

    return { state: state[0], run: run, more: more };
  }

  function useCompactList(request, reloadKey) {
    var state = useState({ status: 'loading', rows: [], error: null,
                           total: 0 });
    var setState = state[1];
    var tokenRef = useRef(0);
    var stamp = JSON.stringify(request);

    useEffect(function () {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      postJson('list', JSON.parse(stamp)).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState({ status: 'failed', rows: [], total: 0,
                     error: out.data.error || 'the list was refused' });
          return;
        }
        setState({ status: 'done', rows: out.data.rows || [],
                   total: out.data.total || 0, error: null });
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState({ status: 'failed', rows: [], total: 0,
                   error: String(err) });
      });
    }, [stamp, reloadKey]);

    return state[0];
  }

  function useDetail(versionId) {
    var state = useState({ status: 'idle', data: null, error: null });
    var setState = state[1];
    var tokenRef = useRef(0);

    useEffect(function () {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      if (!versionId) {
        setState({ status: 'idle', data: null, error: null });
        return undefined;
      }
      setState({ status: 'loading', data: null, error: null });
      postJson('detail', { version_id: versionId }).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState({ status: 'failed', data: null,
                     error: out.data.error || 'the detail was refused' });
          return;
        }
        setState({ status: 'done', data: out.data, error: null });
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState({ status: 'failed', data: null, error: String(err) });
      });
      return undefined;
    }, [versionId]);

    return state[0];
  }

  function useLines(versionId) {
    var state = useState({ status: 'idle', data: null, error: null });
    var setState = state[1];
    var tokenRef = useRef(0);

    useEffect(function () {
      tokenRef.current += 1;
      setState({ status: 'idle', data: null, error: null });
    }, [versionId]);

    var fetchLines = useCallback(function (options) {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      setState({ status: 'loading', data: null, error: null });
      postJson('lines', {
        version_id: versionId,
        include_ancestors: !!(options && options.includeAncestors),
        every_node: !!(options && options.everyNode),
        limit: LINES_ROWS
      }).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState({ status: 'failed', data: null,
                     error: out.data.error || 'the lines were refused' });
          return;
        }
        setState({ status: 'done', data: out.data, error: null });
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState({ status: 'failed', data: null, error: String(err) });
      });
    }, [versionId]);

    return { state: state[0], fetch: fetchLines };
  }

  function Field(props) {
    return e('div', { className: 'kv' },
      e('span', { className: 'k' }, props.label),
      e('span', { className: 'v' }, props.children));
  }

  function Volume(props) {
    var shown = volumeOf(props.row);
    return e('span', { className: 'tp-vol tp-vol-' + shown.kind,
                       title: shown.note || undefined },
      e('span', { className: 'tp-volnum' }, shown.text));
  }

  function VolumeWindow(props) {
    var state = props.state;
    if (state.status === 'loading') {
      return e('section', { className: 'tp-snap' },
        e('div', { className: 'tp-snapline' }, 'Reading the cutoff…'));
    }
    if (state.status === 'failed') {
      return e('section', { className: 'tp-snap tp-snap-bad' },
        e('div', { className: 'tp-snapline' },
          'The volume window is unavailable. ' + state.error));
    }
    var data = state.data || {};
    var held = data.window || {};
    var coverage = coverageOf(data.coverage);
    var totals = data.totals || {};
    var counted = { count: totals.records || 0,
                    count_status: totals.records_status || 'unknown' };
    var countedKnown = counted.count_status === 'exact'
      || counted.count_status === 'incomplete';
    var expired = data.expired_window;
    var uncatalogued = data.uncatalogued || {};
    var interval = held.window_start_iso && held.window_end_iso
      ? isoStamp(held.window_start_iso) + '  →  ' +
        isoStamp(held.window_end_iso)
      : 'no cutoff is published';
    var days = held.window_days || 28;

    return e('section', { className: 'tp-snap' },
      e('div', { className: 'tp-snaprow' },
        e('div', { className: 'tp-snapcell' },
          e('span', { className: 'tp-label' }, days + '-day volume window'),
          e('span', { className: 'tp-strong' }, interval)),
        e('div', { className: 'tp-snapcell' },
          e('span', { className: 'tp-label' }, 'Cutoff'),
          e('span', null, held.window_end
            ? stampOf(held.window_end) + '  (' + ageOf(held.age_ms) + ')'
            : 'no live node has published past an hour boundary')),
        e('div', { className: 'tp-snapcell' },
          e('span', { className: 'tp-label' }, 'Coverage'),
          e('span', { className: 'tp-cov tp-cov-' + coverage.kind },
            coverage.text)),
        e('div', { className: 'tp-snapcell' },
          e('span', { className: 'tp-label' }, 'Counted records'),
          e('span', null,
            e(Volume, { row: counted }),
            countedKnown
              ? ' in ' + (totals.versions || 0) + ' versions'
              : ''))),
      expired
        ? e('div', { className: 'tp-snapline tp-warn' },
            'The newest cutoff every live node had published past is ' +
            isoStamp(expired.window_end_iso || '') + ', ' +
            ageOf(expired.age_ms) + ', past the ' +
            Math.round((expired.max_age_ms || 0) / 60000) +
            ' minute ceiling. That window is history. This page serves no '
            + 'current volume until a newer cutoff arrives.')
        : null,
      data.stale && !expired
        ? e('div', { className: 'tp-snapline tp-warn' },
            'This view is not current: either the server has not refreshed '
            + 'or no newer cutoff was published. The cutoff above is what '
            + 'it counts.')
        : null,
      coverage.behind.length
        ? e('div', { className: 'tp-snapline tp-warn' },
            'Behind the cutoff: ' + coverage.behind.join(', ') +
            '. Their buckets up to the cutoff are not all published, so '
            + 'every count is a lower bound.')
        : null,
      coverage.idle.length
        ? e('div', { className: 'tp-snapline tp-warn' },
            'Idle: ' + coverage.idle.join(', ') +
            '. A node that has stopped publishing is partial coverage, '
            + 'never a zero.')
        : null,
      uncatalogued.versions
        ? e('div', { className: 'tp-snapline' },
            groupDigits(String(uncatalogued.versions)) +
            ' counted versions have no definition in the catalog yet (' +
            groupDigits(String(uncatalogued.records)) +
            ' records). They are in the total and not in the list.')
        : null);
  }

  function Health(props) {
    var data = props.summary || {};
    var coverage = data.coverage || {};
    var gaps = coverage.gaps || [];
    var semantic = data.semantic || {};
    var labels = data.labels || {};
    var lines = [];

    gaps.forEach(function (gap) {
      var reason = GAP_TEXT[gap.reason] || gap.reason;
      var where = gap.index ? ' in ' + gap.index : '';
      var records = gap.records ? ' (' + groupDigits(String(gap.records)) +
        ' records)' : '';
      lines.push((gap.node || 'a node') + ': ' + reason + where +
        records + (gap.detail ? ' — ' + gap.detail : ''));
    });
    if (data.rematch_available === false) {
      lines.push('The mining recipe is not importable on this server, so '
        + 'the Lines panel cannot re-match an ancestor\'s lines.');
    }
    if (semantic.status && semantic.status !== 'ready') {
      lines.push('Semantic search is ' + semantic.status +
        (semantic.detail ? ': ' + semantic.detail : '') +
        '. Text search is unaffected.');
    }
    if (labels.last_error) {
      lines.push('Stored labels: ' + labels.last_error);
    }
    if (data.last_error) {
      lines.push('View refresh: ' + data.last_error);
    }
    if (data.note) { lines.push(data.note); }
    if (!lines.length) { return null; }

    return e('section', { className: 'tp-block' },
      e('h2', { className: 'tp-h' }, 'Monitoring health'),
      e('div', { className: 'tp-note' },
        'These are collection faults, not templates. They are listed here so '
        + 'they do not fill the template list.'),
      e('ul', { className: 'tp-list' },
        lines.map(function (line, index) {
          return e('li', { key: index, className: 'tp-listitem' }, line);
        })));
  }

  function Episodes(props) {
    var state = props.state;
    if (state.status === 'failed') {
      return e('section', { className: 'tp-block' },
        e('h2', { className: 'tp-h' }, 'Active episodes'),
        e('div', { className: 'tp-note tp-warn' },
          'The episode summary is unavailable. ' + state.error));
    }
    var data = state.data || {};
    var episodes = data.episodes || [];
    return e('section', { className: 'tp-block' },
      e('h2', { className: 'tp-h' }, 'Active episodes'),
      data.stale
        ? e('div', { className: 'tp-note tp-warn' },
            'This episode summary is stale.')
        : null,
      episodes.length
        ? e('div', { className: 'tp-eps' },
            episodes.map(function (episode) {
              return e('article', {
                className: 'tp-ep sev-' + (episode.severity || 'unknown'),
                key: episode.incident_id || episode.grouping_key
              },
                e('div', { className: 'tp-ephead' },
                  e('span', { className: 'tp-epname' },
                    episode.alertname || 'incident'),
                  e('span', { className: 'tp-epwho' },
                    (episode.entity_kind || 'entity') + ' ' +
                    (episode.entity_id || '')),
                  e('span', { className: 'grow' }),
                  e('span', { className: 'tp-epwhen' },
                    'since ' + stampOf(episode.episode_start))),
                episode.title
                  ? e('div', { className: 'tp-eptitle' }, episode.title)
                  : null,
                episode.diagnosis
                  ? e('div', { className: 'tp-epline' }, episode.diagnosis)
                  : null,
                episode.action
                  ? e('div', { className: 'tp-epaction' },
                      'Next: ' + episode.action)
                  : null,
                (episode.affected || []).length
                  ? e('div', { className: 'tp-epline' },
                      'Affected: ' + episode.affected.join(', '))
                  : null,
                e('div', { className: 'tp-epkey' },
                  'grouped as ' +
                  (episode.grouping_key || 'no grouping key')));
            }))
        : e('div', { className: 'tp-note' },
            'No episode is firing. Signals stay inside a template’s '
            + 'detail panel.'));
  }

  function Row(props) {
    var row = props.row;
    var activity = activityOf(row);
    var scope = (row.origin_hosts || []).slice(0, 3);
    var programs = (row.programs || []).slice(0, 2);
    return e('div', {
      className: 'tp-row' + (props.selected ? ' selected' : ''),
      role: 'button',
      tabIndex: 0,
      onClick: props.onOpen,
      onKeyDown: function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          props.onOpen();
        }
      }
    },
      e('span', { className: 'tp-cell tp-c-sev sev-' +
                             (row.severity_norm || 'unknown') },
        (row.severity_norm || '?').charAt(0).toUpperCase()),
      e('span', { className: 'tp-cell tp-c-template', title: row.template },
        row.template),
      e('span', { className: 'tp-cell tp-c-family' }, row.family),
      e('span', { className: 'tp-cell tp-c-scope' },
        programs.concat(scope).join(' · ')),
      e('span', { className: 'tp-cell tp-c-when' },
        stampOf(row.last_observed)),
      e('span', { className: 'tp-cell tp-c-state' },
        e('span', { className: 'tp-act tp-act-' + activity.kind },
          activity.text),
        row.watched ? e('span', { className: 'tp-tag' }, 'watched') : null,
        row.label
          ? e('span', { className: 'tp-tag' }, LABEL_TEXT[row.label] ||
              row.label)
          : null,
        row.label_conflicts
          ? e('span', { className: 'tp-tag tp-tag-warn' },
              row.label_conflicts + ' conflicting')
          : null),
      e('span', { className: 'tp-cell tp-c-vol' },
        e(Volume, { row: row })));
  }

  function RowHead() {
    return e('div', { className: 'tp-row tp-rowhead' },
      e('span', { className: 'tp-cell tp-c-sev' }, 'S'),
      e('span', { className: 'tp-cell tp-c-template' }, 'Template'),
      e('span', { className: 'tp-cell tp-c-family' }, 'Family'),
      e('span', { className: 'tp-cell tp-c-scope' }, 'Programs and hosts'),
      e('span', { className: 'tp-cell tp-c-when' }, 'Last observed'),
      e('span', { className: 'tp-cell tp-c-state' }, 'State'),
      e('span', { className: 'tp-cell tp-c-vol' }, '28 days'));
  }

  function CompactRows(props) {
    var state = props.state;
    if (state.status === 'loading') {
      return e('div', { className: 'tp-note' }, 'Reading…');
    }
    if (state.status === 'failed') {
      return e('div', { className: 'tp-note tp-unavail', title: state.error },
        'unavailable; the reason is above');
    }
    if (!state.rows.length) {
      return e('div', { className: 'tp-note' }, props.empty);
    }
    return e('div', { className: 'tp-compact' },
      state.rows.map(function (row) {
        return e('div', { className: 'tp-crow', key: row.version_id },
          e('button', {
            className: 'tp-ctext',
            title: row.template,
            onClick: function () { props.onOpen(row.version_id); }
          }, row.template),
          props.showCatalogued
            ? e('span', { className: 'tp-cwhen' },
                row.first_catalogued
                  ? stampOf(row.first_catalogued)
                  : 'no catalogue date is recorded')
            : null,
          e('span', { className: 'tp-cvol' }, e(Volume, { row: row })));
      }));
  }

  function Labels(props) {
    var labels = props.labels || [];
    if (!labels.length) {
      return e('div', { className: 'tp-note' },
        'No label has been recorded for this canonical group.');
    }
    return e('div', { className: 'tp-labels' },
      labels.map(function (label) {
        var history = label.history || [];
        return e('div', { className: 'tp-labelblock', key: label.label_id },
          e('div', { className: 'tp-labelhead' },
            e('span', { className: 'tp-tag' },
              LABEL_TEXT[label.label] || label.label),
            e('span', { className: 'tp-labelwho' },
              (label.author || 'unnamed') + ', self-reported'),
            e('span', { className: 'grow' }),
            e('span', { className: 'tp-labelwhen' },
              'revision ' + (label.revision || 0) + ', ' +
              stampOf(label.updated_at))),
          label.note ? e('div', { className: 'tp-labelnote' }, label.note)
                     : null,
          label.watched
            ? e('div', { className: 'tp-note' }, 'This group is watched.')
            : null,
          history.length
            ? e('ul', { className: 'tp-list' },
                history.map(function (entry, index) {
                  return e('li', { key: index, className: 'tp-listitem' },
                    stampOf(entry.updated_at || entry.at) + ' · ' +
                    (LABEL_TEXT[entry.label] || entry.label || 'no label') +
                    ' · ' + (entry.author || 'unnamed') +
                    (entry.note ? ' · ' + entry.note : ''));
                }))
            : e('div', { className: 'tp-note' },
                'No earlier revision is retained.'));
      }));
  }

  function Lines(props) {
    var state = props.state;
    var options = props.options;
    var setOptions = props.setOptions;
    var fetchWith = function (everyNode) {
      props.onFetch({ includeAncestors: options.includeAncestors,
                      everyNode: everyNode });
    };
    var controls = e('div', { className: 'tp-lines-controls' },
      e('label', { className: 'tp-check' },
        e('input', {
          type: 'checkbox',
          checked: options.includeAncestors,
          onChange: function () {
            setOptions({ includeAncestors: !options.includeAncestors });
          }
        }), ' Include ancestors, re-matched token by token'),
      e('button', { className: 'btn', onClick: function () { fetchWith(false); } },
        state.status === 'idle' ? 'Fetch lines' : 'Fetch again'),
      e('button', {
        className: 'btn',
        title: 'Search every worker\'s local index, not only the nodes the '
          + 'bucket documents name',
        onClick: function () { fetchWith(true); }
      }, 'Search every node'));
    if (state.status === 'loading') {
      return e('div', null, controls,
        e('div', { className: 'tp-note' }, 'Fetching lines…'));
    }
    if (state.status === 'failed') {
      return e('div', null, controls,
        e('div', { className: 'tp-note tp-warn' }, state.error));
    }
    if (state.status !== 'done' || !state.data) {
      return e('div', null, controls,
        e('div', { className: 'tp-note' },
          'Lines are found by their stamp: the selected version and every '
          + 'narrower version it covers (' + props.descendantCount +
          '). The search touches only the nodes whose bucket documents '
          + 'name those versions.'));
    }
    var data = state.data;
    var routed = data.every_node
      ? 'every node'
      : ((data.nodes || []).length
          ? (data.nodes || []).length + ' routed node' +
            ((data.nodes || []).length === 1 ? '' : 's') + ' (' +
            (data.nodes || []).join(', ') + ')'
          : 'no routed node');
    return e('div', null, controls,
      e('div', { className: 'tp-note' },
        data.matched + ' of ' + data.fetched + ' fetched lines carry a '
        + 'stamp in the expanded set of ' + (data.expanded || []).length +
        ' versions' +
        (data.ancestors_included
          ? ', plus ancestor lines that fit the selected template'
          : '') +
        (data.truncated ? '; the page is full and older lines were not read'
                        : '') +
        '. Searched ' + routed +
        (data.cutoff_iso ? ' at cutoff ' + isoStamp(data.cutoff_iso) : '') +
        '.'),
      !data.every_node
        ? e('div', { className: 'tp-note' },
            'The open five-minute bucket is not published yet, so a node '
            + 'that started writing this template minutes ago is not '
            + 'routed. Search every node to include it.')
        : null,
      data.note ? e('div', { className: 'tp-note' }, data.note) : null,
      e('div', { className: 'tp-examples' },
        (data.rows || []).map(function (record, index) {
          return e('div', { className: 'tp-example', key: index },
            e('span', { className: 'tp-exwhen' },
              record['@timestamp'] || stampOf(record.collector_time) || ''),
            e('span', { className: 'tp-exhost' },
              (record.node || '') +
              (record.origin_host ? ' · ' + record.origin_host : '')),
            e('span', { className: 'tp-exmsg' }, record.message));
        })));
  }

  function DrawerNote(props) {
    return e('aside', { className: 'tp-drawer' },
      e('div', { className: 'tp-drawhead' },
        e('strong', null, 'Template'),
        e('span', { className: 'grow' }),
        e('button', { className: 'btn', onClick: props.onClose }, 'Close')),
      e('div', { className: 'tp-drawbody' },
        e('div', { className: props.warn ? 'tp-note tp-warn' : 'tp-note' },
          props.children)));
  }

  function Drawer(props) {
    var state = props.state;
    var lines = props.lines;
    if (state.status === 'failed') {
      return e(DrawerNote, { onClose: props.onClose, warn: true },
        state.error);
    }
    if (state.status !== 'done' || !state.data) {
      return e(DrawerNote, { onClose: props.onClose }, 'Reading…');
    }
    var data = state.data;
    var row = data.version || {};
    var group = data.canonical_group || {};
    var versions = group.versions || [];
    var groupCount = { count: group.count || 0,
                       count_status: group.count_status || 'unknown' };
    var activity = activityOf(row);
    var shown = volumeOf(row);
    var episodes = data.episodes || [];
    var neighbours = data.neighbours || {};
    var suggestions = neighbours.suggestions || [];
    var descendants = data.descendants || [];
    var descendantCount = data.descendant_count || 0;
    var widenedInto = data.widened_into || row.widened_into || [];
    var widenedFrom = data.widened_from || row.widened_from || [];

    return e('aside', { className: 'tp-drawer' },
      e('div', { className: 'tp-drawhead' },
        e('strong', null, row.family + ' template'),
        e('span', { className: 'grow' }),
        e('button', {
          className: 'btn',
          title: 'Open the Logs page filtered on this template\'s stamp and '
            + 'the stamps it covers',
          onClick: function () { props.onOpenLogs(data.log_link); }
        }, 'Open in Logs'),
        e('button', { className: 'btn', onClick: props.onClose }, 'Close')),
      e('div', { className: 'tp-drawbody' },
        e('div', { className: 'tp-version' }, row.template),
        e('div', { className: 'tp-drawvol' },
          e('span', { className: 'tp-label' }, '28-day volume'),
          e('span', { className: 'tp-vol tp-vol-' + shown.kind },
            e('span', { className: 'tp-volnum tp-volbig' }, shown.text)),
          e('span', { className: 'tp-volnote' },
            shown.note || 'summed over the hourly buckets in the window '
              + 'that ends at the cutoff below'),
          e('span', { className: 'tp-volnote' },
            props.windowEndIso
              ? 'cutoff ' + isoStamp(props.windowEndIso)
              : 'no cutoff is published'),
          e('span', { className: 'tp-volnote' },
            'covers ' + descendantCount + ' narrower version' +
            (descendantCount === 1 ? '' : 's') +
            '; a search for its lines expands to them')),

        e('h3', { className: 'tp-h3' }, 'Identity'),
        e(Field, { label: 'version' }, row.version_id),
        e(Field, { label: 'canonical' }, row.canonical_id),
        e(Field, { label: 'normalized' }, row.normalized),
        e(Field, { label: 'severity' }, row.severity_norm || 'not recorded'),

        e('h3', { className: 'tp-h3' }, 'Retained scope'),
        row.historical
          ? e('div', { className: 'tp-note' },
              'This definition is inactive. The scope below is what the '
              + 'catalog retained over its lifetime, not a current affected '
              + 'host or program.')
          : e('div', { className: 'tp-note' },
              'The scope is lifetime-scoped and capped. A search is never '
              + 'routed by it; the bucket documents route it.'),
        e(Field, { label: 'programs' },
          (row.programs || []).length ? row.programs.join(', ')
                                      : 'none retained'),
        e(Field, { label: 'hosts' },
          (row.origin_hosts || []).length ? row.origin_hosts.join(', ')
                                          : 'none retained'),
        e(Field, { label: 'log sources' },
          (row.log_sources || []).length ? row.log_sources.join(', ')
                                         : 'none retained'),
        row.scope_truncated
          ? e('div', { className: 'tp-note tp-warn' },
              'The scope list was capped. The volume above is not capped.')
          : null,

        e('h3', { className: 'tp-h3' }, 'Activity'),
        e(Field, { label: 'state' },
          e('span', { className: 'tp-act tp-act-' + activity.kind },
            activity.text)),
        e(Field, { label: 'first observed' },
          stampOf(row.first_observed) || 'not recorded'),
        e(Field, { label: 'last observed' },
          stampOf(row.last_observed) || 'not recorded'),
        e(Field, { label: 'first catalogued' },
          row.first_catalogued
            ? stampOf(row.first_catalogued)
            : 'the catalog holds no date for this version'),
        e('div', { className: 'tp-note' },
          'First catalogued is informational. A returning template and a '
          + 'widened template are not confirmed first-ever fleet events.'),

        e('h3', { className: 'tp-h3' }, 'Related versions'),
        e(Field, { label: 'widened into' },
          widenedInto.length ? widenedInto.join(', ') : 'nothing observed'),
        e(Field, { label: 'widened from' },
          widenedFrom.length ? widenedFrom.join(', ') : 'nothing observed'),
        e(Field, { label: 'covers' },
          descendantCount
            ? descendantCount + ' narrower version' +
              (descendantCount === 1 ? '' : 's') +
              (descendants.length < descendantCount
                ? ', first ' + descendants.length + ' listed' : '')
            : 'no narrower version'),
        descendants.length
          ? e('ul', { className: 'tp-list' },
              descendants.map(function (versionId) {
                return e('li', { key: versionId, className: 'tp-listitem' },
                  e('button', {
                    className: 'tp-ctext',
                    onClick: function () { props.onOpen(versionId); }
                  }, versionId));
              }))
          : null,
        e('div', { className: 'tp-note' },
          'Widened links are what a worker observed. The cover relation is '
          + 'structural: a narrower version fits under this one token by '
          + 'token, so its lines are included wholesale.'),
        versions.length
          ? e('div', { className: 'tp-group' },
              versions.map(function (version) {
                return e('div', { className: 'tp-gver',
                                  key: version.version_id },
                  e('button', {
                    className: 'tp-ctext',
                    title: version.template,
                    onClick: function () { props.onOpen(version.version_id); }
                  }, version.template),
                  e('span', { className: 'tp-cvol' },
                    e(Volume, { row: version })));
              }))
          : null,
        e('div', { className: 'tp-note' },
          'Canonical group total: ',
          e(Volume, { row: groupCount }),
          '. That total is the sum of the versions listed here and nothing '
          + 'else; a narrower version\'s volume is never added to a wider '
          + 'one.'),

        e('h3', { className: 'tp-h3' }, 'Semantic suggestions'),
        suggestions.length
          ? e('div', { className: 'tp-group' },
              suggestions.map(function (item) {
                return e('div', { className: 'tp-gver',
                                  key: item.version_id },
                  e('button', {
                    className: 'tp-ctext',
                    title: item.template,
                    onClick: function () { props.onOpen(item.version_id); }
                  }, item.template),
                  e('span', { className: 'tp-cvol' },
                    similarityOf(item.score)));
              }))
          : e('div', { className: 'tp-note tp-unavail' },
              'No nearest neighbour is suggested for this template.'),
        neighbours.note
          ? e('div', { className: 'tp-note' }, neighbours.note)
          : null,

        e('h3', { className: 'tp-h3' }, 'Label history'),
        row.label_scope
          ? e(Field, { label: 'scope' },
              SCOPE_TEXT[row.label_scope] || row.label_scope)
          : null,
        row.label_conflicts
          ? e('div', { className: 'tp-note tp-warn' },
              row.label_conflicts + ' conflicting labels are recorded for '
              + 'this canonical group. They stay visible and are not merged.')
          : null,
        e(Labels, { labels: data.labels }),

        e('h3', { className: 'tp-h3' }, 'Related episodes'),
        episodes.length
          ? e('div', null, episodes.map(function (episode) {
              return e('div', { className: 'tp-epsmall',
                                key: episode.incident_id },
                e('div', { className: 'tp-epname' },
                  (episode.alertname || 'incident') + ' · ' +
                  (episode.entity_id || '')),
                episode.action
                  ? e('div', { className: 'tp-epline' },
                      'Next: ' + episode.action)
                  : null,
                e('div', { className: 'tp-epline' },
                  'Signals: ' + ((episode.signals || []).length
                    ? episode.signals.join(', ')
                    : 'none listed')));
            }))
          : e('div', { className: 'tp-note' },
              'No firing episode names a host this template was observed on.'),

        e('h3', { className: 'tp-h3' }, 'Lines'),
        e(Lines, { state: lines.state, onFetch: lines.fetch,
                   options: props.lineOptions,
                   setOptions: props.setLineOptions,
                   descendantCount: descendantCount })));
  }

  function TemplatesPage(props) {
    var view = props.view;
    var setView = props.setView;
    var reloadState = useState(0);
    var reloadKey = reloadState[0];

    var typedState = useState(view.query);
    var typed = typedState[0];
    var setTyped = typedState[1];

    useEffect(function () {
      var timer = window.setTimeout(function () {
        setView(function (prev) {
          return prev.query === typed ? prev
            : Object.assign({}, prev, { query: typed });
        });
      }, SEARCH_DEBOUNCE_MS);
      return function () { window.clearTimeout(timer); };
    }, [typed, setView]);

    var summary = useEndpoint('summary', SUMMARY_POLL_MS);
    var episodes = useEndpoint('episodes', EPISODE_POLL_MS);
    var list = useTemplateList(view, reloadKey);
    var detail = useDetail(view.selected);
    var lines = useLines(view.selected);
    var lineOptionsState = useState({ includeAncestors: false });

    var summaryData = summary.state.data || {};
    var held = summaryData.window || {};
    var cutoff = held.window_end || 0;

    var watched = useCompactList({
      query: '', mode: 'text', include_inactive: false, watched_only: true,
      family: [], program: [], host: [], severity: [], sort: 'volume',
      page_size: COMPACT_ROWS, after: null
    }, cutoff);

    var recent = useCompactList({
      query: '', mode: 'text', include_inactive: false, watched_only: false,
      family: [], program: [], host: [], severity: [],
      sort: 'first_catalogued', page_size: COMPACT_ROWS, after: null
    }, cutoff);

    var open = useCallback(function (versionId) {
      setView(function (prev) {
        return Object.assign({}, prev, { selected: versionId });
      });
    }, [setView]);

    var close = useCallback(function () {
      setView(function (prev) {
        return Object.assign({}, prev, { selected: null });
      });
    }, [setView]);

    var selected = view.selected;
    var onOpenLogs = props.onOpenLogs;
    var openLogs = useCallback(function (link) {
      onOpenLogs(logFilters(link));
    }, [onOpenLogs]);

    var queryId = list.state.queryId;
    useEffect(function () {
      if (!selected || !queryId) { return; }
      var rank = 0;
      list.state.rows.forEach(function (row, index) {
        if (row.version_id === selected) { rank = index + 1; }
      });
      if (!rank) { return; }
      postJson('opened', { query_id: queryId, version_id: selected,
                           rank: rank }).catch(function () { return null; });
    }, [selected, queryId]);

    useEffect(function () {
      if (!selected) { return undefined; }
      var onKey = function (ev) {
        if (ev.key === 'Escape') { close(); }
      };
      window.addEventListener('keydown', onKey);
      return function () { window.removeEventListener('keydown', onKey); };
    }, [selected, close]);

    var patch = function (key, value) {
      setView(function (prev) {
        var next = Object.assign({}, prev);
        next[key] = value;
        return next;
      });
    };

    var semantic = summaryData.semantic || {};
    var semanticReady = semantic.status === 'ready';
    var rows = list.state.rows;

    var controls = e('div', { className: 'tp-controls' },
      e('input', {
        className: 'tp-search',
        type: 'search',
        placeholder: 'Search template text, a version identifier or a '
          + 'canonical identifier',
        value: typed,
        onInput: function (ev) { setTyped(ev.target.value); }
      }),
      e('select', {
        className: 'fg-select tp-sort',
        value: view.sort,
        title: 'How the list is ordered',
        onChange: function (ev) { patch('sort', ev.target.value); }
      }, SORTS.map(function (sort) {
        return e('option', { key: sort.value, value: sort.value }, sort.label);
      })),
      semanticReady
        ? e('button', {
            className: 'chip' + (view.mode === 'semantic' ? ' on' : ''),
            onClick: function () {
              patch('mode', view.mode === 'semantic' ? 'text' : 'semantic');
            }
          }, 'semantic')
        : null,
      e('label', { className: 'tp-check' },
        e('input', {
          type: 'checkbox',
          checked: view.watchedOnly,
          onChange: function () {
            setView(function (prev) {
              return Object.assign({}, prev, {
                watchedOnly: !prev.watchedOnly,
                includeInactive: prev.watchedOnly
                  ? prev.includeInactive : false
              });
            });
          }
        }), ' Watched only'),
      e('label', { className: 'tp-check' },
        e('input', {
          type: 'checkbox',
          checked: view.includeInactive,
          onChange: function () {
            setView(function (prev) {
              return Object.assign({}, prev, {
                includeInactive: !prev.includeInactive,
                watchedOnly: prev.includeInactive ? prev.watchedOnly : false
              });
            });
          }
        }), ' Include inactive templates'),
      e('button', {
        className: 'btn',
        onClick: function () { reloadState[1](reloadKey + 1); }
      }, 'Refresh'));

    var listBody;
    if (list.state.status === 'failed') {
      listBody = e('div', { className: 'tp-note tp-warn' }, list.state.error);
    } else if (list.state.status === 'loading' && !rows.length) {
      listBody = e('div', { className: 'tp-note' }, 'Reading…');
    } else if (!rows.length) {
      listBody = e('div', { className: 'tp-note' },
        view.includeInactive
          ? 'No retained definition matches this search.'
          : 'No active template matches this search. Turn on "Include '
            + 'inactive templates" to look through the retained 90-day '
            + 'history.');
    } else {
      listBody = e('div', { className: 'tp-rows' },
        e(RowHead, null),
        rows.map(function (row) {
          return e(Row, {
            key: row.version_id,
            row: row,
            selected: row.version_id === selected,
            onOpen: function () { open(row.version_id); }
          });
        }));
    }

    return e('div', { className: 'tp' },
      e(VolumeWindow, { state: summary.state }),
      e('div', { className: 'tp-body' },
        e('main', { className: 'tp-main' },
          e(Episodes, { state: episodes.state }),
          e('section', { className: 'tp-block tp-two' },
            e('div', { className: 'tp-half' },
              e('h2', { className: 'tp-h' }, 'Watched templates'),
              e(CompactRows, {
                state: watched, onOpen: open,
                empty: 'No template is watched yet.'
              })),
            e('div', { className: 'tp-half' },
              e('h2', { className: 'tp-h' }, 'Recently catalogued, active'),
              e(CompactRows, {
                state: recent, onOpen: open, showCatalogued: true,
                empty: 'No active template is catalogued in this window.'
              }),
              e('div', { className: 'tp-note' },
                'First catalogued is informational, not a first-ever fleet '
                + 'event.'))),
          e(Health, { summary: summaryData }),
          e('section', { className: 'tp-block' },
            e('div', { className: 'tp-listhead' },
              e('h2', { className: 'tp-h' }, 'Templates'),
              controls),
            list.state.note
              ? e('div', { className: 'tp-note tp-warn' }, list.state.note)
              : null,
            view.includeInactive
              ? e('div', { className: 'tp-note' },
                  'Inactive definitions are retained centrally for 90 days '
                  + 'after their last observation. Any volume shown beside '
                  + 'them belongs to the 28-day window above, never to an '
                  + 'old catalogue total.')
              : null,
            listBody,
            e('div', { className: 'tp-foot' },
              e('span', null,
                rows.length + ' of ' + list.state.total +
                (list.state.totalRelation === 'gte' ? '+' : '') + ' matched'),
              list.state.windowEndIso
                ? e('span', null,
                    'cutoff ' + isoStamp(list.state.windowEndIso))
                : null,
              list.state.tookMs
                ? e('span', null, 'in ' + list.state.tookMs + ' ms')
                : null,
              e('span', { className: 'grow' }),
              list.state.hasMore
                ? e('button', {
                    className: 'btn',
                    disabled: list.state.loadingMore,
                    onClick: list.more
                  }, list.state.loadingMore ? 'Loading…'
                                            : 'Show the next page')
                : null))),
        selected
          ? e(Drawer, {
              state: detail,
              lines: lines,
              lineOptions: lineOptionsState[0],
              setLineOptions: lineOptionsState[1],
              windowEndIso: held.window_end_iso,
              onClose: close,
              onOpen: open,
              onOpenLogs: openLogs
            })
          : null));
  }

  function initialView() {
    return {
      query: '', mode: 'text', sort: 'volume', includeInactive: false,
      watchedOnly: false, selected: null
    };
  }

  window.SHIFTER_TEMPLATES = {
    Page: TemplatesPage,
    initialView: initialView,
    helpers: {
      groupDigits: groupDigits,
      formatCount: formatCount,
      stampOf: stampOf,
      isoStamp: isoStamp,
      ageOf: ageOf,
      volumeOf: volumeOf,
      activityOf: activityOf,
      similarityOf: similarityOf,
      coverageOf: coverageOf,
      requestOf: requestOf,
      mergeRows: mergeRows,
      logFilters: logFilters
    }
  };
}());
