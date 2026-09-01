(function () {
  'use strict';

  var e = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useLayoutEffect = React.useLayoutEffect;
  var useRef = React.useRef;
  var useMemo = React.useMemo;
  var useCallback = React.useCallback;

  var CONFIG = window.SHIFTER_CONFIG || {};
  var BUFFER_MAX = CONFIG.bufferRows || 10000;
  var STREAM_URL = CONFIG.streamUrl || 'stream';
  var QUERY_URL = CONFIG.queryUrl || 'api/query';
  var COCKPIT_URL = CONFIG.cockpitUrl || '/';
  var DEFAULT_LIMIT = CONFIG.queryLimit || 5000;
  var ROW_HEIGHT = 20;
  var OVERSCAN = 14;
  var ROW_DETAIL_H = 236;
  var MAP_FPS_MS = 500;
  var PAGE_ROWS = 500;
  var LOAD_MORE_AT_PX = 400;
  var LOCAL_FILTER_DEBOUNCE_MS = 150;
  var DEFAULT_RANGE = '1h';
  var PHONE_MAX = 640;
  var TABLET_MAX = 1000;
  var COUNTER_INTERVAL_MS = 400;
  var HIDDEN_GRACE_MS = (CONFIG.hiddenGraceSeconds == null
    ? 120 : CONFIG.hiddenGraceSeconds) * 1000;
  var PRESET_KEY = 'alice.shifter.presets.v1';
  var LAYOUT_KEY = 'alice.shifter.layout.v1';
  var DETAIL_KEY = 'alice.shifter.detail.v1';

  var SEVERITIES = ['fatal', 'error', 'warning', 'info', 'debug', 'system',
                    'unknown'];
  var ALERT_SEVERITIES = { fatal: 1, error: 1 };

  var LEVELS = [
    { label: 'Ops', value: 1 },
    { label: 'Support', value: 6 },
    { label: 'Devel', value: 11 },
    { label: 'Trace', value: null }
  ];

  var LIMITS = [1000, 5000, 20000];

  var RANGES = [
    { label: '15m', ms: 15 * 60000 },
    { label: '1h', ms: 3600000 },
    { label: '6h', ms: 6 * 3600000 },
    { label: '24h', ms: 24 * 3600000 },
    { label: '7d', ms: 7 * 24 * 3600000 }
  ];

  function hostOf(rec) {
    return rec.origin_host || rec.hostname || rec.host || rec.node || '';
  }

  function programOf(rec) {
    return rec.rolename || rec.source_file || rec.log_source || '';
  }

  var PHONE_COLUMNS = ['time', 'host', 'message'];
  var TABLET_DROP = ['partition', 'username', 'pid', 'errsource', 'errline'];

  var COLUMNS = [
    { key: 'time', label: 'Time', width: 98, narrow: 76,
      get: function (r) { return clockOf(r); },
      getNarrow: function (r) { return clockOf(r).slice(0, 8); } },
    { key: 'host', label: 'Host', width: 108, narrow: 88, get: hostOf },
    { key: 'program', label: 'Program', width: 152, get: programOf },
    { key: 'system', label: 'System', width: 74,
      get: function (r) { return r.system; } },
    { key: 'facility', label: 'Facility', width: 138,
      get: function (r) { return r.facility; } },
    { key: 'detector', label: 'Det', width: 52,
      get: function (r) { return r.detector; } },
    { key: 'partition', label: 'Partition', width: 108,
      get: function (r) { return r.partition; } },
    { key: 'run', label: 'Run', width: 76,
      get: function (r) { return r.run; } },
    { key: 'level', label: 'Lvl', width: 42,
      get: function (r) { return r.level; } },
    { key: 'pid', label: 'PID', width: 74,
      get: function (r) { return r.pid; } },
    { key: 'username', label: 'User', width: 82,
      get: function (r) { return r.username; } },
    { key: 'errcode', label: 'ErrCode', width: 70,
      get: function (r) { return r.errcode; } },
    { key: 'errline', label: 'ErrLine', width: 66,
      get: function (r) { return r.errline; } },
    { key: 'errsource', label: 'ErrSource', width: 130,
      get: function (r) { return r.errsource; } },
    { key: 'message', label: 'Message', width: 0,
      get: function (r) { return r.message; } }
  ];

  var FILTER_FIELDS = ['host', 'program', 'system', 'facility', 'detector',
                       'partition', 'run', 'level', 'pid', 'username',
                       'errcode', 'errline', 'errsource', 'message'];

  var DEFAULT_COLUMNS = ['time', 'host', 'program', 'system', 'facility',
                         'detector', 'partition', 'run', 'message'];

  function emptyFilters() {
    var f = {
      severities: ['fatal', 'error', 'warning', 'info', 'system', 'unknown'],
      levelMax: null,
      range: DEFAULT_RANGE,
      since: '',
      until: '',
      hide: false,
      hideSince: '',
      hideUntil: '',
      mode: 'wildcard',
      limit: DEFAULT_LIMIT,
      fields: {}
    };
    FILTER_FIELDS.forEach(function (k) {
      f.fields[k] = { match: '', exclude: '' };
    });
    return f;
  }

  var BUILT_IN_PRESETS = [
    { name: 'Errors, last hour', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.severities = ['fatal', 'error'];
        f.range = '1h';
        return f;
      }()) },
    { name: 'Warnings and worse', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.severities = ['fatal', 'error', 'warning'];
        return f;
      }()) },
    { name: 'Everything, last 15 minutes', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.range = '15m';
        return f;
      }()) },
    { name: 'Quality Control only', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.fields.system.match = 'QC';
        return f;
      }()) },
    { name: 'Drop the known noise', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.fields.message.exclude =
          'No URL provided for Bookkeeping%\nCould not find the DPL InfoLogger';
        return f;
      }()) },
    { name: 'One run', builtin: true, filters: (function () {
        var f = emptyFilters();
        f.fields.run.match = '';
        return f;
      }()) }
  ];

  function pad(n, width) {
    var s = String(n);
    while (s.length < width) { s = '0' + s; }
    return s;
  }

  function dateOfRaw(rec) {
    var raw = rec['@timestamp'] || rec.collector_time;
    if (!raw) { return null; }
    var d = new Date(raw);
    return isNaN(d.getTime()) ? null : d;
  }

  function clockOf(rec) {
    var d = dateOfRaw(rec);
    if (!d) { return ''; }
    return pad(d.getUTCHours(), 2) + ':' + pad(d.getUTCMinutes(), 2) + ':' +
      pad(d.getUTCSeconds(), 2) + '.' + pad(d.getUTCMilliseconds(), 3);
  }

  function dateOf(rec) {
    var d = dateOfRaw(rec);
    if (!d) { return ''; }
    return d.toISOString().replace('T', ' ').replace('Z', ' UTC');
  }

  function graceLabel(ms) {
    var s = Math.round(ms / 1000);
    if (s % 60 === 0) { return (s / 60) + ' minutes'; }
    return s + ' seconds';
  }

  function useViewport() {
    var state = useState(function () {
      return typeof window === 'undefined' ? 1400 : window.innerWidth;
    });
    useEffect(function () {
      var timer = null;
      var onResize = function () {
        if (timer) { return; }
        timer = window.setTimeout(function () {
          timer = null;
          state[1](window.innerWidth);
        }, 150);
      };
      window.addEventListener('resize', onResize);
      return function () {
        window.removeEventListener('resize', onResize);
        if (timer) { window.clearTimeout(timer); }
      };
    }, []);
    return state[0];
  }

  function isAlert(rec) {
    return !!ALERT_SEVERITIES[rec.severity_norm];
  }

  function readStore(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (err) {
      return fallback;
    }
  }

  function writeStore(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      return;
    }
  }

  var HASH_SCALARS = [['range', 'range'], ['since', 'since'],
                      ['until', 'until'], ['hideSince', 'hidesince'],
                      ['hideUntil', 'hideuntil'], ['mode', 'mode'],
                      ['limit', 'limit'], ['levelMax', 'levelmax']];

  function encodeFilters(filters) {
    try {
      var base = emptyFilters();
      var parts = [];
      var add = function (k, v) {
        parts.push(k + '=' + encodeURIComponent(String(v)));
      };
      var sev = filters.severities || [];
      if (sev.join(',') !== base.severities.join(',')) {
        parts.push('sev=' + sev.join(','));
      }
      HASH_SCALARS.forEach(function (pair) {
        var v = filters[pair[0]];
        if (v == null || v === '' || v === base[pair[0]]) { return; }
        add(pair[1], v);
      });
      if (filters.hide) { parts.push('hide=1'); }
      FILTER_FIELDS.forEach(function (k) {
        var f = (filters.fields || {})[k] || {};
        if (f.match) { add(k, f.match); }
        if (f.exclude) { add(k + '!', f.exclude); }
      });
      return parts.join('&');
    } catch (err) {
      return '';
    }
  }

  function decodeFilters(raw) {
    try {
      var out = emptyFilters();
      var seen = false;
      raw.split('&').forEach(function (chunk) {
        var eq = chunk.indexOf('=');
        if (eq < 1) { return; }
        var key = chunk.slice(0, eq);
        var val = decodeURIComponent(chunk.slice(eq + 1));
        seen = true;
        if (key === 'sev') {
          out.severities = val ? val.split(',') : [];
          return;
        }
        if (key === 'hide') { out.hide = val === '1'; return; }
        var scalar = '';
        HASH_SCALARS.forEach(function (pair) {
          if (pair[1] === key) { scalar = pair[0]; }
        });
        if (scalar) {
          out[scalar] = (scalar === 'limit' || scalar === 'levelMax')
            ? Number(val) : val;
          return;
        }
        var excl = key.charAt(key.length - 1) === '!';
        var name = excl ? key.slice(0, -1) : key;
        if (out.fields[name]) {
          out.fields[name][excl ? 'exclude' : 'match'] = val;
        }
      });
      return seen ? out : null;
    } catch (err) {
      return null;
    }
  }

  function filterLink(filters) {
    return window.location.origin + window.location.pathname +
      '#' + encodeFilters(filters);
  }

  function writeClipboard(text, done) {
    var fallback = function () {
      var box = document.createElement('textarea');
      box.value = text;
      box.setAttribute('readonly', '');
      box.style.position = 'fixed';
      box.style.top = '-1000px';
      box.style.opacity = '0';
      document.body.appendChild(box);
      box.select();
      if (box.setSelectionRange) { box.setSelectionRange(0, text.length); }
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      document.body.removeChild(box);
      done(ok);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
                                               fallback);
      return;
    }
    fallback();
  }

  function relativeSince(label) {
    var found = null;
    RANGES.forEach(function (r) { if (r.label === label) { found = r; } });
    if (!found) { return ''; }
    return new Date(Date.now() - found.ms).toISOString();
  }

  var FORMATS = [
    { key: 'cern', label: 'dd.mm.yyyy', order: 'dmy', sep: '.' },
    { key: 'slash', label: 'dd/mm/yyyy', order: 'dmy', sep: '/' },
    { key: 'iso', label: 'yyyy-mm-dd', order: 'ymd', sep: '-' },
    { key: 'us', label: 'mm/dd/yyyy', order: 'mdy', sep: '/' }
  ];
  var DEFAULT_FORMAT = 'cern';
  var DATE_CHARS = 10;
  var FORMAT_KEY = 'alice.shifter.dateformat.v1';

  var activeFormat = DEFAULT_FORMAT;

  function useFormat() {
    var state = useState(readFormat);
    activeFormat = state[0];
    return [state[0], function (key) {
      activeFormat = key;
      writeFormat(key);
      state[1](key);
    }];
  }

  function calendarIcon() {
    var svg = 'http://www.w3.org/2000/svg';
    return e('svg', {
      xmlns: svg, viewBox: '0 0 14 14', width: 12, height: 12,
      'aria-hidden': 'true'
    },
      e('rect', { x: 0.5, y: 2.5, width: 13, height: 11, rx: 1,
                  fill: 'none', stroke: 'currentColor' }),
      e('path', { d: 'M0.5 5.5h13', stroke: 'currentColor' }),
      e('path', { d: 'M4 0.5v3M10 0.5v3', stroke: 'currentColor',
                  'stroke-linecap': 'round' }),
      e('rect', { x: 3, y: 7.5, width: 2, height: 2, fill: 'currentColor' }),
      e('rect', { x: 6.5, y: 7.5, width: 2, height: 2, fill: 'currentColor' }));
  }

  function formatOf(key) {
    var found = FORMATS[0];
    FORMATS.forEach(function (f) { if (f.key === key) { found = f; } });
    return found;
  }

  function readFormat() {
    try {
      var saved = window.localStorage.getItem(FORMAT_KEY);
      if (saved) { return formatOf(saved).key; }
    } catch (err) { /* storage refused */ }
    return DEFAULT_FORMAT;
  }

  function writeFormat(key) {
    try { window.localStorage.setItem(FORMAT_KEY, key); } catch (err) { /* storage refused */ }
  }

  var PARTS_RE = new RegExp(
    '^\\s*(\\d{1,4})[.\\/-](\\d{1,2})[.\\/-](\\d{1,4})' +
    '(?:[\\s,T]+(\\d{1,2}):(\\d{2})(?::(\\d{2}))?)?\\s*$');

  function parseStamp(text, key) {
    key = key || activeFormat;
    if (!text || !text.trim()) { return null; }
    var m = PARTS_RE.exec(text);
    if (!m) { return NaN; }
    var a = +m[1], b = +m[2], c = +m[3];
    var day, month, year;
    if (m[1].length === 4) {
      year = a; month = b; day = c;
    } else if (m[3].length !== 4) {
      return NaN;
    } else {
      year = c;
      if (formatOf(key).order === 'mdy') { month = a; day = b; }
      else { day = a; month = b; }
    }
    var hour = m[4] === undefined ? 0 : +m[4];
    var minute = m[5] === undefined ? 0 : +m[5];
    var second = m[6] === undefined ? 0 : +m[6];
    if (month < 1 || month > 12 || day < 1 || day > 31) { return NaN; }
    if (hour > 23 || minute > 59 || second > 59) { return NaN; }
    var when = new Date(year, month - 1, day, hour, minute, second, 0);
    if (when.getFullYear() !== year || when.getMonth() !== month - 1 ||
        when.getDate() !== day) {
      return NaN;
    }
    return when;
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function formatStamp(when, key) {
    key = key || activeFormat;
    var f = formatOf(key);
    var d = pad(when.getDate());
    var mo = pad(when.getMonth() + 1);
    var y = String(when.getFullYear());
    var clock = ' ' + pad(when.getHours()) + ':' + pad(when.getMinutes());
    if (f.order === 'ymd') { return y + f.sep + mo + f.sep + d + clock; }
    if (f.order === 'mdy') { return mo + f.sep + d + f.sep + y + clock; }
    return d + f.sep + mo + f.sep + y + clock;
  }

  function stampHint(key) {
    return formatOf(key || activeFormat).label + ' hh:mm';
  }

  function stampState(text, key) {
    key = key || activeFormat;
    if (!text || !text.trim()) { return 'empty'; }
    var when = parseStamp(text, key);
    return (when && !isNaN(when)) ? 'ok' : 'bad';
  }

  function badStamps(filters, key) {
    key = key || activeFormat;
    var boxes = filters.range ? [] : ['since', 'until'];
    if (filters.hide) { boxes = boxes.concat(['hideSince', 'hideUntil']); }
    return boxes.filter(function (k) {
      return stampState(filters[k], key) === 'bad';
    }).length;
  }

  function localToInstant(value, key) {
    key = key || activeFormat;
    var when = parseStamp(value, key);
    if (!when || isNaN(when)) { return ''; }
    return when.toISOString();
  }

  function toCriterias(filters) {
    var criterias = {
      timestamp: {
        since: filters.range
          ? relativeSince(filters.range)
          : localToInstant(filters.since),
        until: filters.range ? '' : localToInstant(filters.until),
        excludeSince: filters.hide ? localToInstant(filters.hideSince) : '',
        excludeUntil: filters.hide ? localToInstant(filters.hideUntil) : ''
      },
      severity: { in: filters.severities },
      level: { max: filters.levelMax }
    };
    FILTER_FIELDS.forEach(function (k) {
      criterias[k] = {
        match: filters.fields[k].match,
        exclude: filters.fields[k].exclude
      };
    });
    return criterias;
  }

  function useLiveBuffer() {
    var bufferRef = useRef([]);
    var sourceRef = useRef(null);
    var sinceRef = useRef(0);
    var seenIdRef = useRef(0);
    var epochRef = useRef(null);
    var gapRef = useRef(0);
    var reopenedRef = useRef(false);
    var hiddenTimerRef = useRef(null);

    var snapshotState = useState([]);
    var pendingState = useState(0);
    var connectedState = useState(false);
    var pausedState = useState(false);
    var droppedState = useState(0);

    var setSnapshot = snapshotState[1];
    var setPending = pendingState[1];
    var setConnected = connectedState[1];
    var setPaused = pausedState[1];
    var setDropped = droppedState[1];

    var refresh = useCallback(function () {
      sinceRef.current = 0;
      setPending(0);
      setSnapshot(bufferRef.current.slice());
    }, []);

    var push = useCallback(function (rec) {
      var buf = bufferRef.current;
      buf.push(rec);
      if (buf.length > BUFFER_MAX) {
        buf.splice(0, buf.length - BUFFER_MAX);
      }
    }, []);

    var connect = useCallback(function () {
      if (sourceRef.current) { return; }
      var source = new EventSource(STREAM_URL);
      source.onopen = function () {
        reopenedRef.current = true;
        setConnected(true);
      };
      source.onerror = function () { setConnected(false); };
      source.addEventListener('hello', function (ev) {
        var epoch;
        try { epoch = JSON.parse(ev.data).epoch; } catch (err) { return; }
        if (epochRef.current !== null && epochRef.current !== epoch) {
          if (seenIdRef.current) {
            gapRef.current += 1;
            push({
              _gap: gapRef.current,
              _missed: null,
              '@timestamp': new Date().toISOString()
            });
          }
          seenIdRef.current = 0;
        }
        epochRef.current = epoch;
      });
      source.onmessage = function (ev) {
        var rec;
        try { rec = JSON.parse(ev.data); } catch (err) { return; }
        if (typeof rec._id === 'number') {
          var seen = seenIdRef.current;
          if (rec._id <= seen) { return; }
          if (seen && rec._id > seen + 1) {
            var missed = rec._id - seen - 1;
            if (reopenedRef.current) {
              gapRef.current += 1;
              push({
                _gap: gapRef.current,
                _missed: missed,
                '@timestamp': new Date().toISOString()
              });
            } else {
              setDropped(function (d) { return d + missed; });
            }
          }
          seenIdRef.current = rec._id;
        }
        reopenedRef.current = false;
        push(rec);
        sinceRef.current += 1;
      };
      sourceRef.current = source;
    }, [push]);

    var pause = useCallback(function () {
      if (!sourceRef.current) { return; }
      sourceRef.current.close();
      sourceRef.current = null;
      setConnected(false);
      setPaused(true);
      refresh();
    }, [refresh]);

    var resume = useCallback(function () {
      setPaused(false);
      connect();
    }, [connect]);

    useEffect(function () {
      connect();
      return function () {
        if (sourceRef.current) {
          sourceRef.current.close();
          sourceRef.current = null;
        }
      };
    }, [connect]);

    useEffect(function () {
      if (!HIDDEN_GRACE_MS) { return undefined; }
      var clear = function () {
        if (hiddenTimerRef.current) {
          window.clearTimeout(hiddenTimerRef.current);
          hiddenTimerRef.current = null;
        }
      };
      var onVisibility = function () {
        if (document.visibilityState !== 'hidden') { clear(); return; }
        if (hiddenTimerRef.current) { return; }
        hiddenTimerRef.current = window.setTimeout(function () {
          hiddenTimerRef.current = null;
          pause();
        }, HIDDEN_GRACE_MS);
      };
      document.addEventListener('visibilitychange', onVisibility);
      onVisibility();
      return function () {
        document.removeEventListener('visibilitychange', onVisibility);
        clear();
      };
    }, [pause]);

    useEffect(function () {
      var shown = -1;
      var timer = null;
      var tick = function () {
        if (sinceRef.current !== shown) {
          shown = sinceRef.current;
          setPending(shown);
        }
      };
      var start = function () {
        if (timer || document.visibilityState === 'hidden') { return; }
        timer = window.setInterval(tick, COUNTER_INTERVAL_MS);
      };
      var stop = function () {
        if (timer) { window.clearInterval(timer); timer = null; }
      };
      var onVisibility = function () {
        if (document.visibilityState === 'hidden') { stop(); } else { start(); }
      };
      start();
      document.addEventListener('visibilitychange', onVisibility);
      return function () {
        document.removeEventListener('visibilitychange', onVisibility);
        stop();
      };
    }, []);

    useEffect(function () {
      var timer = window.setTimeout(function () {
        if (bufferRef.current.length) { refresh(); }
      }, 700);
      return function () { window.clearTimeout(timer); };
    }, [refresh]);

    return {
      pendingRef: sinceRef,
      snapshot: snapshotState[0],
      pending: pendingState[0],
      connected: connectedState[0],
      paused: pausedState[0],
      dropped: droppedState[0],
      refresh: refresh,
      resume: resume,
      buffered: bufferRef.current.length
    };
  }

  var EMPTY_QUERY = {
    status: 'idle', rows: [], count: 0, countRelation: 'eq', took: 0,
    sql: '', error: null, limit: 0, after: null, hasMore: false,
    loadingMore: false, ranWith: null, token: 0
  };

  function useQuery() {
    var state = useState(EMPTY_QUERY);
    var setState = state[1];
    var stateRef = useRef(EMPTY_QUERY);
    var tokenRef = useRef(0);
    stateRef.current = state[0];

    var post = function (filters, after) {
      return window.fetch(QUERY_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          criterias: toCriterias(filters),
          options: {
            limit: filters.limit, mode: filters.mode,
            pageSize: Math.min(PAGE_ROWS, filters.limit), after: after || null
          }
        })
      }).then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      });
    };

    var run = useCallback(function (filters) {
      var token = tokenRef.current + 1;
      tokenRef.current = token;
      var stamp = encodeFilters(filters);
      setState(function (prev) {
        return Object.assign({}, prev, { status: 'loading', error: null });
      });
      post(filters, null).then(function (out) {
        if (tokenRef.current !== token) { return; }
        if (!out.ok) {
          setState(Object.assign({}, EMPTY_QUERY, {
            status: 'failed', token: token,
            error: out.data.error || 'the query was refused'
          }));
          return;
        }
        var rows = out.data.rows || [];
        setState({
          status: 'done',
          rows: rows,
          count: out.data.count || 0,
          countRelation: out.data.countRelation || 'eq',
          took: out.data.took || 0,
          sql: out.data.queryAsString || '',
          limit: out.data.limit || 0,
          after: out.data.after || null,
          hasMore: !!out.data.hasMore && rows.length < (out.data.limit || 0),
          loadingMore: false,
          ranWith: stamp,
          token: token,
          error: null
        });
      }).catch(function (err) {
        if (tokenRef.current !== token) { return; }
        setState(Object.assign({}, EMPTY_QUERY, {
          status: 'failed', token: token, error: String(err)
        }));
      });
    }, []);

    var more = useCallback(function (filters) {
      var current = stateRef.current;
      var token = tokenRef.current;
      if (current.status !== 'done' || !current.hasMore ||
          current.loadingMore || !current.after) {
        return Promise.resolve(0);
      }
      setState(function (prev) {
        return Object.assign({}, prev, { loadingMore: true });
      });
      return post(filters, current.after).then(function (out) {
        if (tokenRef.current !== token) { return 0; }
        if (!out.ok) {
          setState(function (prev) {
            return Object.assign({}, prev, {
              loadingMore: false, hasMore: false,
              error: out.data.error || 'the next page was refused'
            });
          });
          return 0;
        }
        var older = out.data.rows || [];
        var added = 0;
        setState(function (prev) {
          var seen = {};
          prev.rows.forEach(function (r) { seen[r._id] = true; });
          var fresh = older.filter(function (r) { return !seen[r._id]; });
          added = fresh.length;
          var total = fresh.concat(prev.rows);
          if (total.length > prev.limit) {
            total = total.slice(total.length - prev.limit);
          }
          return Object.assign({}, prev, {
            rows: total,
            after: out.data.after || prev.after,
            hasMore: !!out.data.hasMore && total.length < prev.limit,
            loadingMore: false
          });
        });
        return added;
      }).catch(function () {
        setState(function (prev) {
          return Object.assign({}, prev, { loadingMore: false });
        });
        return 0;
      });
    }, []);

    var clear = useCallback(function () {
      tokenRef.current += 1;
      setState(Object.assign({}, EMPTY_QUERY, { token: tokenRef.current }));
    }, []);

    return { state: state[0], run: run, more: more, clear: clear };
  }

  var NUMERIC_FIELDS = { level: 1, run: 1, pid: 1, errcode: 1, errline: 1 };

  function matchesText(value, needle, numeric) {
    if (!needle) { return true; }
    var raw = String(value == null ? '' : value);
    var parts = needle.split(/\s+/).filter(Boolean);
    for (var i = 0; i < parts.length; i += 1) {
      if (numeric) {
        if (raw === parts[i]) { return true; }
      } else if (raw.toLowerCase()
                 .indexOf(parts[i].toLowerCase().replace(/%/g, '')) !== -1) {
        return true;
      }
    }
    return false;
  }

  function localMatch(rec, filters) {
    if (filters.severities.length &&
        filters.severities.indexOf(rec.severity_norm) === -1) {
      return false;
    }
    if (filters.levelMax != null && rec.level != null &&
        Number(rec.level) > Number(filters.levelMax)) {
      return false;
    }
    for (var i = 0; i < FILTER_FIELDS.length; i += 1) {
      var key = FILTER_FIELDS[i];
      var spec = filters.fields[key];
      if (!spec) { continue; }
      var numeric = !!NUMERIC_FIELDS[key];
      var value = key === 'host' ? hostOf(rec)
        : (key === 'program' ? programOf(rec) : rec[key]);
      if (spec.match && !matchesText(value, spec.match, numeric)) {
        return false;
      }
      if (spec.exclude && matchesText(value, spec.exclude, numeric)) {
        return false;
      }
    }
    return true;
  }

  function ScrollMap(props) {
    var ref = useRef(null);
    var timerRef = useRef(null);
    var paintedRef = useRef(0);
    var rows = props.rows;
    var height = props.height;

    useEffect(function () {
      var paint = function () {
        paintedRef.current = Date.now();
        var canvas = ref.current;
        if (!canvas || !height) { return; }
        var ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (!rows.length) { return; }
        var step = Math.max(1, Math.floor(rows.length / height));
        for (var i = 0; i < rows.length; i += step) {
          var sev = rows[i].severity_norm;
          if (sev === 'warning') { ctx.fillStyle = '#c77700'; }
          else if (sev === 'error') { ctx.fillStyle = '#d62631'; }
          else if (sev === 'fatal') { ctx.fillStyle = '#7b1fa2'; }
          else { continue; }
          ctx.fillRect(0, Math.floor(i * height / rows.length), 10, 1);
        }
      };
      var wait = Math.max(0, MAP_FPS_MS - (Date.now() - paintedRef.current));
      timerRef.current = window.setTimeout(paint, wait);
      return function () {
        if (timerRef.current) { window.clearTimeout(timerRef.current); }
      };
    }, [rows, height]);

    return e('canvas', {
      className: 'scrollmap', ref: ref, width: 10, height: height || 1
    });
  }

  function LogTable(props) {
    var rows = props.rows;
    var columns = props.columns;
    var scrollState = useState(0);
    var heightState = useState(400);
    var ref = useRef(null);
    var outerRef = props.scrollRef;

    var attach = useCallback(function (node) {
      ref.current = node;
      if (outerRef) { outerRef.current = node; }
    }, [outerRef]);

    useEffect(function () {
      var el = ref.current;
      if (!el) { return undefined; }
      var apply = function () { heightState[1](el.clientHeight); };
      apply();
      if (typeof ResizeObserver === 'undefined') {
        window.addEventListener('resize', apply);
        return function () { window.removeEventListener('resize', apply); };
      }
      var ro = new ResizeObserver(apply);
      ro.observe(el);
      return function () { ro.disconnect(); };
    }, []);

    var scrollTop = scrollState[0];
    var viewport = heightState[0];
    var total = rows.length;
    var start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    var end = Math.min(total,
                       Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);

    var onSelect = props.onSelect;
    var selectedKey = props.selectedKey;
    var narrow = props.narrow;
    var detail = props.detail;
    var onCloseDetail = props.onCloseDetail;
    var panelH = Math.max(90, Math.min(ROW_DETAIL_H, viewport - ROW_HEIGHT * 2));

    var body = useMemo(function () {
      var visible = [];
      for (var i = start; i < end; i += 1) {
        var rec = rows[i];
        if (rec._gap) {
          visible.push(e('div', {
            className: 'gap', key: 'gap-' + rec._gap,
            style: { top: (i * ROW_HEIGHT) + 'px', height: ROW_HEIGHT + 'px' }
          }, rec._missed == null
               ? 'the lane server restarted at ' + clockOf(rec) +
                 ' — what it held before that is gone'
               : rec._missed.toLocaleString() + ' records were produced while ' +
                 'this page was not connected — resumed at ' + clockOf(rec)));
          continue;
        }
        var rowKey = rec._id == null ? 'i' + i : rec._id;
        visible.push(e(TableRow, {
          key: rowKey,
          rec: rec, columns: columns, top: i * ROW_HEIGHT, narrow: narrow,
          selected: selectedKey != null && selectedKey === rowKey,
          onSelect: function (r) { return function () { onSelect(r); }; }(rec)
        }));
        if (detail && rec === detail) {
          var under = (i + 1) * ROW_HEIGHT;
          if (under + panelH > total * ROW_HEIGHT && i * ROW_HEIGHT >= panelH) {
            under = i * ROW_HEIGHT - panelH;
          }
          visible.push(e(RowDetail, {
            key: 'rowdetail', rec: detail, top: under, height: panelH,
            onClose: onCloseDetail
          }));
        }
      }
      return e('div', {
        className: 'spacer', style: { height: (total * ROW_HEIGHT) + 'px' }
      }, visible);
    }, [rows, columns, start, end, total, selectedKey, onSelect, narrow,
        detail, onCloseDetail, panelH]);

    var onReachTop = props.onReachTop;

    return e('div', {
      className: 'logtable', ref: attach,
      onScroll: function (ev) {
        var top = ev.target.scrollTop;
        scrollState[1](top);
        if (onReachTop && top < LOAD_MORE_AT_PX) { onReachTop(); }
      }
    }, body);
  }

  function TableRow(props) {
    var rec = props.rec;
    var narrow = props.narrow;
    var cells = props.columns.map(function (col) {
      var raw = narrow && col.getNarrow ? col.getNarrow(rec) : col.get(rec);
      var text = raw == null ? '' : String(raw);
      var width = narrow && col.narrow ? col.narrow : col.width;
      return e('span', {
        key: col.key,
        className: 'cell cell-' + col.key,
        style: width ? { width: width + 'px' } : null,
        title: col.key === 'message' ? text : null
      }, text);
    });
    return e('div', {
      className: 'trow sev-' + (rec.severity_norm || 'unknown') +
                 (props.selected ? ' selected' : ''),
      style: { top: props.top + 'px', height: ROW_HEIGHT + 'px' },
      onClick: props.onSelect
    }, e('span', { className: 'cell cell-sev' },
         (rec.severity_norm || '?').charAt(0).toUpperCase()), cells);
  }

  function TableHead(props) {
    var columns = props.columns;
    var narrow = props.narrow;
    return useMemo(function () {
      return e('div', { className: 'thead' },
        e('span', { className: 'cell cell-sev' }, 'S'),
        columns.map(function (col) {
          var width = narrow && col.narrow ? col.narrow : col.width;
          return e('span', {
            key: col.key,
            className: 'cell cell-' + col.key,
            style: width ? { width: width + 'px' } : null
          }, col.label);
        }));
    }, [columns, narrow]);
  }

  var CAL_WIDTH = 210;
  var WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November',
                'December'];

  function startOfMonth(when) {
    return new Date(when.getFullYear(), when.getMonth(), 1);
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function Calendar(props) {
    var monthState = useState(function () {
      return startOfMonth(props.value || new Date());
    });
    var month = monthState[0];
    var setMonth = monthState[1];
    var ref = useRef(null);
    var placeState = useState(null);
    var place = placeState[0];
    var onClose = props.onClose;

    useLayoutEffect(function () {
      var node = ref.current;
      var wrap = node && node.parentNode;
      if (!wrap) { return undefined; }
      var settle = function () {
        var box = wrap.getBoundingClientRect();
        var width = node.offsetWidth || CAL_WIDTH;
        var height = node.offsetHeight || 0;
        var left = Math.max(4, Math.min(box.left,
          window.innerWidth - width - 4));
        var top = box.bottom + 3;
        if (top + height > window.innerHeight - 4) {
          top = Math.max(4, box.top - height - 3);
        }
        placeState[1]({ left: left, top: top });
      };
      settle();
      window.addEventListener('resize', onClose);
      var grid = wrap.closest ? wrap.closest('.filtergrid') : null;
      if (grid) { grid.addEventListener('scroll', onClose); }
      return function () {
        window.removeEventListener('resize', onClose);
        if (grid) { grid.removeEventListener('scroll', onClose); }
      };
    }, [onClose]);

    useEffect(function () {
      var onDown = function (ev) {
        var wrap = ref.current && ref.current.parentNode;
        if (wrap && wrap.contains(ev.target)) { return; }
        onClose();
      };
      var onKey = function (ev) {
        if (ev.key === 'Escape') { ev.stopPropagation(); onClose(); }
      };
      document.addEventListener('mousedown', onDown);
      document.addEventListener('keydown', onKey, true);
      return function () {
        document.removeEventListener('mousedown', onDown);
        document.removeEventListener('keydown', onKey, true);
      };
    }, [onClose]);

    var first = startOfMonth(month);
    var lead = (first.getDay() + 6) % 7;
    var span = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    var cells = [];
    var i;
    for (i = 0; i < lead; i += 1) { cells.push(null); }
    for (i = 1; i <= span; i += 1) {
      cells.push(new Date(month.getFullYear(), month.getMonth(), i));
    }

    var today = new Date();
    var hour = props.value ? props.value.getHours() : 0;
    var minute = props.value ? props.value.getMinutes() : 0;

    var step = function (delta) {
      return function () {
        setMonth(new Date(month.getFullYear(), month.getMonth() + delta, 1));
      };
    };

    var setClock = function (which) {
      return function (ev) {
        var raw = parseInt(ev.target.value, 10);
        if (isNaN(raw)) { return; }
        var base = props.value || new Date(today.getFullYear(),
          today.getMonth(), today.getDate(), 0, 0, 0, 0);
        props.onPick(new Date(base.getFullYear(), base.getMonth(),
          base.getDate(),
          which === 'h' ? Math.min(23, Math.max(0, raw)) : base.getHours(),
          which === 'm' ? Math.min(59, Math.max(0, raw)) : base.getMinutes(),
          0, 0), true);
      };
    };

    return e('div', {
      className: 'cal',
      ref: ref,
      style: place
        ? { left: place.left + 'px', top: place.top + 'px' }
        : { left: '0px', top: '0px', visibility: 'hidden' }
    },
      e('div', { className: 'cal-head' },
        e('button', { type: 'button', className: 'cal-nav',
                      title: 'Previous month', onClick: step(-1) }, '\u2039'),
        e('span', { className: 'cal-title' },
          MONTHS[month.getMonth()] + ' ' + month.getFullYear()),
        e('button', { type: 'button', className: 'cal-nav',
                      title: 'Next month', onClick: step(1) }, '\u203a')),
      e('div', { className: 'cal-grid' },
        WEEKDAYS.map(function (w) {
          return e('span', { key: w, className: 'cal-wd' }, w);
        }),
        cells.map(function (day, at) {
          if (!day) {
            return e('span', { key: 'pad' + at, className: 'cal-day empty' });
          }
          return e('button', {
            key: day.getDate(),
            type: 'button',
            className: 'cal-day' +
              (props.value && sameDay(day, props.value) ? ' on' : '') +
              (sameDay(day, today) ? ' today' : ''),
            onClick: function () {
              props.onPick(new Date(day.getFullYear(), day.getMonth(),
                day.getDate(), hour, minute, 0, 0));
            }
          }, day.getDate());
        })),
      e('div', { className: 'cal-foot' },
        e('input', { type: 'number', min: 0, max: 23, className: 'cal-clock',
                     value: pad(hour), onInput: setClock('h') }),
        e('span', { className: 'cal-colon' }, ':'),
        e('input', { type: 'number', min: 0, max: 59, className: 'cal-clock',
                     value: pad(minute), onInput: setClock('m') }),
        e('button', { type: 'button', className: 'cal-now',
                      onClick: function () { props.onPick(new Date()); } },
          'now')));
  }

  function FilterGrid(props) {
    var openState = useState(null);
    var openBox = openState[0];
    var setOpenBox = openState[1];
    var closeBox = useCallback(function () { setOpenBox(null); }, []);
    var filters = props.filters;
    var set = props.setFilters;
    var visible = props.visible;

    var setField = function (key, side) {
      return function (ev) {
        var next = Object.assign({}, filters);
        next.fields = Object.assign({}, filters.fields);
        next.fields[key] = Object.assign({}, filters.fields[key]);
        next.fields[key][side] = ev.target.value;
        set(next);
      };
    };

    var toggleColumn = function (key) {
      return function () { props.toggleColumn(key); };
    };

    var setRange = function (ev) {
      var pick = ev.target.value;
      var next = Object.assign({}, filters);
      if (pick === 'exact') {
        next.range = null;
      } else {
        next.range = pick;
        next.since = '';
        next.until = '';
      }
      set(next);
    };

    var setBound = function (side, drop) {
      return function (ev) {
        var next = Object.assign({}, filters);
        next[side] = ev.target.value;
        if (drop) { next.range = null; }
        set(next);
      };
    };

    var setHide = function (ev) {
      var next = Object.assign({}, filters);
      next.hide = ev.target.value === 'hide';
      if (!next.hide) { next.hideSince = ''; next.hideUntil = ''; }
      set(next);
    };

    var whenBox = function (name, side, drop) {
      var open = openBox === name;
      var current = parseStamp(filters[side]);
      if (!current || isNaN(current)) { current = null; }
      var toggle = function () { setOpenBox(open ? null : name); };
      return e('span', { key: name, className: 'fg-whenwrap' },
        e('input', {
          type: 'text',
          inputMode: 'numeric',
          className: 'fg-input fg-when' +
            (stampState(filters[side]) === 'bad' ? ' bad' : ''),
          placeholder: stampHint(),
          title: 'Click the date for a calendar, the time to type it',
          value: filters[side],
          onClick: function (ev) {
            var caret = ev.target.selectionStart;
            if (caret !== null && caret > DATE_CHARS) {
              if (open) { setOpenBox(null); }
              return;
            }
            toggle();
          },
          onInput: setBound(side, drop),
          onKeyDown: props.onKeyDown
        }),
        e('button', {
          className: 'fg-cal',
          type: 'button',
          title: 'Pick from a calendar',
          onClick: toggle
        }, calendarIcon()),
        open ? e(Calendar, {
          value: current,
          onClose: closeBox,
          onPick: function (when, keepOpen) {
            var next = Object.assign({}, filters);
            next[side] = formatStamp(when);
            if (drop) { next.range = null; }
            set(next);
            if (!keepOpen) { setOpenBox(null); }
          }
        }) : null);
    };

    var strip = function (name, label, joiner, from, to, drop, note, picker) {
      var bad = stampState(filters[from]) === 'bad' ||
        stampState(filters[to]) === 'bad';
      return e('div', { className: 'fg-row fg-exact', key: name },
        e('span', { className: 'fg-rowlabel' }, label),
        whenBox(name + '-from', from, drop),
        e('span', { className: 'fg-and' }, joiner),
        whenBox(name + '-to', to, drop),
        e('span', { className: 'fg-hint' + (bad ? ' bad' : '') },
          bad ? 'not a date — write it as ' + stampHint() : note),
        picker ? e('select', {
          className: 'fg-fmt',
          value: props.format,
          title: 'How dates are written in these boxes',
          onChange: function (ev) { props.setFormat(ev.target.value); }
        }, FORMATS.map(function (f) {
          return e('option', { key: f.key, value: f.key }, f.label);
        })) : null);
    };

    return useMemo(function () { return e('div', { className: 'filtergrid' },
      e('div', { className: 'fg-row fg-labels' },
        e('span', { className: 'fg-rowlabel' }, ''),
        COLUMNS.map(function (col) {
          return e('button', {
            key: col.key,
            className: 'colbtn' + (visible[col.key] ? ' on' : ''),
            style: col.width ? { width: col.width + 'px' } : null,
            onClick: toggleColumn(col.key),
            title: visible[col.key] ? 'Hide this column' : 'Show this column'
          }, col.label);
        })),
      e('div', { className: 'fg-row' },
        e('span', { className: 'fg-rowlabel' }, 'match'),
        COLUMNS.map(function (col) {
          if (col.key === 'time') {
            return e('select', {
              key: 'time',
              className: 'fg-select',
              style: { width: col.width + 'px' },
              value: filters.range || 'exact',
              title: 'How far back to search',
              onChange: setRange
            }, RANGES.map(function (r) {
              return e('option', { key: r.label, value: r.label },
                'last ' + r.label);
            }).concat([
              e('option', { key: 'exact', value: 'exact' }, 'exact\u2026')
            ]));
          }
          return e('input', {
            key: col.key,
            className: 'fg-input',
            style: col.width ? { width: col.width + 'px' } : null,
            value: filters.fields[col.key].match,
            onInput: setField(col.key, 'match'),
            onKeyDown: props.onKeyDown
          });
        })),
      e('div', { className: 'fg-row' },
        e('span', { className: 'fg-rowlabel' }, 'exclude'),
        COLUMNS.map(function (col) {
          if (col.key === 'time') {
            return e('select', {
              key: 'time',
              className: 'fg-select',
              style: { width: col.width + 'px' },
              value: filters.hide ? 'hide' : 'none',
              title: 'Hide a window of time from the result',
              onChange: setHide
            },
              e('option', { value: 'none' }, 'nothing'),
              e('option', { value: 'hide' }, 'hide\u2026'));
          }
          return e('input', {
            key: col.key,
            className: 'fg-input',
            style: col.width ? { width: col.width + 'px' } : null,
            value: filters.fields[col.key].exclude,
            onInput: setField(col.key, 'exclude'),
            onKeyDown: props.onKeyDown
          });
        })),
      filters.range ? null : strip('keep', 'between', 'and',
        'since', 'until', true,
        filters.since
          ? 'this console\u2019s clock'
          : 'no start date, so the total below counts only to 10,000',
        true),
      filters.hide ? strip('drop', 'except', 'to', 'hideSince', 'hideUntil',
        false, 'rows in this window are dropped from the result',
        !!filters.range) : null); },
      [filters, visible, openBox, props.format, props.onKeyDown,
       props.toggleColumn]);
  }

  function Dropdown(props) {
    var openState = useState(false);
    var open = openState[0];
    var ref = useRef(null);

    useEffect(function () {
      if (!open) { return undefined; }
      var onDown = function (ev) {
        if (ref.current && !ref.current.contains(ev.target)) {
          openState[1](false);
        }
      };
      document.addEventListener('mousedown', onDown);
      return function () { document.removeEventListener('mousedown', onDown); };
    }, [open]);

    return e('div', { className: 'dd' + (open ? ' open' : ''), ref: ref },
      e('button', {
        className: 'btn' + (props.armed ? ' armed' : ''),
        onClick: function () { openState[1](!open); }
      }, props.label, e('span', { className: 'caret' }, '▾')),
      open ? e('div', { className: 'dd-menu ' + (props.wide ? 'wide' : '') },
        props.children(function () { openState[1](false); })) : null);
  }

  function Advanced(props) {
    var filters = props.filters;
    var set = props.setFilters;
    var patch = function (key, value) {
      var next = Object.assign({}, filters);
      next[key] = value;
      set(next);
    };
    return e('div', { className: 'adv' },
      e('div', { className: 'adv-block' },
        e('div', { className: 'adv-title' }, 'Matching'),
        e('div', { className: 'adv-line' },
          e('button', {
            className: 'chip' + (filters.mode === 'wildcard' ? ' on' : ''),
            onClick: function () { patch('mode', 'wildcard'); }
          }, 'substring, % wildcard'),
          e('button', {
            className: 'chip' + (filters.mode === 'regex' ? ' on' : ''),
            onClick: function () { patch('mode', 'regex'); }
          }, 'regular expression')),
        e('div', { className: 'adv-hint' },
          'Several words in one box mean OR. Message boxes split on new lines.')),
      e('div', { className: 'adv-block' },
        e('div', { className: 'adv-title' }, 'Verbosity and size'),
        e('div', { className: 'adv-line' },
          LEVELS.map(function (l) {
            return e('button', {
              key: l.label,
              className: 'chip' + (filters.levelMax === l.value ? ' on' : ''),
              onClick: function () { patch('levelMax', l.value); }
            }, l.label);
          })),
        e('div', { className: 'adv-line' },
          LIMITS.map(function (n) {
            return e('button', {
              key: n,
              className: 'chip' + (filters.limit === n ? ' on' : ''),
              onClick: function () { patch('limit', n); }
            }, n.toLocaleString() + ' rows');
          }))));
  }

  function Presets(props) {
    var nameState = useState('');
    var saved = props.saved;
    return e('div', { className: 'presets' },
      e('div', { className: 'adv-title' }, 'Saved filters'),
      BUILT_IN_PRESETS.concat(saved).map(function (p, index) {
        return e('div', { className: 'preset', key: p.name + index },
          e('button', {
            className: 'preset-name',
            onClick: function () { props.apply(p.filters); props.close(); }
          }, p.name),
          e('button', {
            className: 'preset-link',
            title: 'Copy a link to this filter',
            onClick: function () { props.copyPreset(p.filters); }
          }, 'link'),
          p.builtin
            ? e('span', { className: 'preset-tag' }, 'built in')
            : e('button', {
                className: 'preset-del',
                title: 'Delete this filter',
                onClick: function () { props.remove(p.name); }
              }, '×'));
      }),
      e('div', { className: 'preset-save' },
        e('input', {
          placeholder: 'name this filter',
          value: nameState[0],
          onInput: function (ev) { nameState[1](ev.target.value); },
          onKeyDown: function (ev) {
            if (ev.key === 'Enter' && nameState[0].trim()) {
              props.save(nameState[0].trim());
              nameState[1]('');
            }
          }
        }),
        e('button', {
          className: 'btn',
          disabled: !nameState[0].trim(),
          onClick: function () {
            props.save(nameState[0].trim());
            nameState[1]('');
          }
        }, 'Save')),
      e('div', { className: 'adv-line' },
        e('button', { className: 'btn', onClick: props.copyLink },
          'Copy link to the filter on screen')),
      e('div', { className: 'adv-hint' },
        'Saved filters live in this browser only. The link works anywhere.'));
  }

  function Inspector(props) {
    return useMemo(function () { return inspectorBody(props); },
                   [props.rec, props.onClose]);
  }

  function fieldRows(rec) {
    var keys = Object.keys(rec).filter(function (k) {
      return k !== '_id' && k !== 'message';
    });
    keys.sort();
    return [e('div', { className: 'kv', key: '@when' },
      e('span', { className: 'k' }, 'event time'),
      e('span', { className: 'v' }, dateOf(rec)))].concat(
      keys.map(function (k) {
        return e('div', { className: 'kv', key: k },
          e('span', { className: 'k' }, k),
          e('span', { className: 'v' }, String(rec[k])));
      }));
  }

  function inspectorBody(props) {
    var rec = props.rec;
    if (!rec) {
      return e('aside', { className: 'inspector' },
        e('div', { className: 'insp-empty' },
          'Click a log line to see all of its fields.'));
    }
    return e('aside', { className: 'inspector' },
      e('div', { className: 'insp-head' },
        e('strong', null, 'Record'),
        e('button', { className: 'btn', onClick: props.onClose }, 'Close')),
      e('div', { className: 'insp-body' },
        fieldRows(rec),
        e('div', { className: 'insp-msg' }, rec.message)));
  }

  function RowDetail(props) {
    var rec = props.rec;
    return e('div', {
      className: 'rowdetail',
      style: { top: props.top + 'px', height: props.height + 'px' },
      onClick: function (ev) { ev.stopPropagation(); }
    },
      e('div', { className: 'rd-head' },
        e('span', { className: 'rd-when' }, dateOf(rec)),
        e('span', { className: 'grow' }),
        e('button', { className: 'btn', onClick: props.onClose }, 'Close')),
      e('div', { className: 'rd-body' },
        fieldRows(rec),
        e('div', { className: 'insp-msg' }, rec.message)));
  }

  function LiveDock(props) {
    var live = props.live;
    var mode = props.dock;
    var rows = props.rows;
    var status = live.paused
      ? 'paused' : (live.connected ? 'live' : 'reconnecting');

    var head = e('div', { className: 'dock-head' },
      e('button', {
        className: 'dock-toggle',
        onClick: props.cycle,
        title: 'Collapsed, half screen, full screen'
      }, mode === 'collapsed' ? '▲ Live lane'
         : (mode === 'half' ? '▲ Live lane' : '▼ Live lane')),
      e('span', { className: 'status ' + status }, status),
      e('span', { className: 'dock-count' },
        rows.length.toLocaleString() + ' held'),
      live.dropped
        ? e('span', { className: 'dropped' },
            live.dropped.toLocaleString() + ' dropped by the server')
        : null,
      e('span', { className: 'grow' }),
      live.paused
        ? e('button', { className: 'btn primary', onClick: live.resume },
            'Resume live stream')
        : e('button', {
            className: 'btn' + (live.pending ? ' primary' : ''),
            disabled: !live.pending,
            onClick: function () { props.showNewest(); }
          }, live.pending
              ? live.pending.toLocaleString() + ' new — show newest'
              : 'up to date'),
      e('label', { className: 'autoscroll' },
        e('input', {
          type: 'checkbox', checked: props.autoscroll,
          onChange: props.toggleAutoscroll
        }), ' Autoscroll'));

    if (mode === 'collapsed') {
      return e('section', { className: 'dock collapsed' }, head);
    }

    return e('section', { className: 'dock ' + mode },
      head,
      live.paused
        ? e('div', { className: 'pausebar' },
            'Stream stopped after ' + graceLabel(HIDDEN_GRACE_MS) +
            ' in the background. Nothing is arriving now.')
        : null,
      e('div', { className: 'dock-body' },
        e(TableHead, { columns: props.columns, narrow: props.narrow }),
        e('div', { className: 'dock-table' },
          e(LogTable, {
            rows: rows, columns: props.columns, narrow: props.narrow,
            scrollRef: props.scrollRef,
            selectedKey: props.selectedKey,
            onSelect: props.onSelect,
            detail: props.detail, onCloseDetail: props.onCloseDetail
          }),
          e(ScrollMap, { rows: rows, height: props.mapHeight }),
          props.dockInspector
            ? e(Inspector, {
                rec: props.inspectorRec, onClose: props.onCloseInspector
              })
            : null)));
  }

  function usePersisted(key, fallback) {
    var state = useState(function () { return readStore(key, fallback); });
    useEffect(function () { writeStore(key, state[0]); }, [state[0]]);
    return state;
  }

  function useJump(scrollRef, rows, selected, setSelected) {
    return useCallback(function (where) {
      var el = scrollRef.current;
      if (!el) { return; }
      var current = -1;
      var target = null;
      var i;
      if (selected && selected._id != null) {
        for (i = 0; i < rows.length; i += 1) {
          if (rows[i]._id === selected._id) { current = i; break; }
        }
      }
      if (current === -1) {
        current = Math.round((el.scrollTop + el.clientHeight / 2) / ROW_HEIGHT);
      }
      if (where === 'bottom') {
        target = rows.length - 1;
      } else {
        var from = where === 'first' ? 0
          : (where === 'last' ? rows.length - 1
            : (where === 'next' ? current + 1 : current - 1));
        var step = (where === 'last' || where === 'prev') ? -1 : 1;
        if (step < 0) { from = Math.min(from, rows.length - 1); }
        for (i = from; i >= 0 && i < rows.length; i += step) {
          if (isAlert(rows[i])) { target = i; break; }
        }
      }
      if (target == null) { return; }
      el.scrollTop = Math.max(0, target * ROW_HEIGHT - el.clientHeight / 2);
      setSelected(rows[target]);
    }, [scrollRef, rows, selected, setSelected]);
  }

  function Toolbar(props) {
    return e('header', { className: 'toolbar' },
        e('button', {
          className: 'btn primary' +
            (props.query.state.status === 'loading' ? ' loading' : '') +
            (props.stale ? ' stale' : ''),
          title: props.stale
            ? 'The rows below were found with different filters'
            : (badStamps(props.filters) > 0
                ? 'Fix the date marked in red first'
                : 'Run the filters below against OpenSearch (Enter)'),
          disabled: badStamps(props.filters) > 0,
          onClick: props.runQuery
        }, props.query.state.status === 'loading'
             ? 'Querying…'
             : (props.stale ? 'Query — filters changed' : 'Query')),
        e('button', {
          className: 'btn',
          title: 'Reset every filter to the last hour and drop the result',
          onClick: props.onClear
        }, 'Clear'),
        e('span', { className: 'sep' }),
        e('div', { className: 'group tips' },
          e('button', { className: 'btn', onClick: function () { props.jump('first'); },
                        'aria-label': 'First error or fatal',
                        'data-tip': 'Oldest ERROR or FATAL in the result.'
                      }, '|◀'),
          e('button', { className: 'btn', onClick: function () { props.jump('prev'); },
                        'aria-label': 'Previous error or fatal',
                        'data-tip': 'ERROR or FATAL before this row. ' +
                          'The rows around it stay in view.'
                      }, '◀'),
          e('button', { className: 'btn', onClick: function () { props.jump('next'); },
                        'aria-label': 'Next error or fatal',
                        'data-tip': 'ERROR or FATAL after this row. ' +
                          'The rows around it stay in view.'
                      }, '▶'),
          e('button', { className: 'btn', onClick: function () { props.jump('last'); },
                        'aria-label': 'Last error or fatal',
                        'data-tip': 'Newest ERROR or FATAL in the result.'
                      }, '▶|'),
          e('button', { className: 'btn', onClick: function () { props.jump('bottom'); },
                        'aria-label': 'Newest row',
                        'data-tip': 'Newest row, at the foot of the result.'
                      }, '▼')),
        e('span', { className: 'sep' }),
        e('div', { className: 'group' },
          SEVERITIES.map(function (name) {
            var on = props.filters.severities.indexOf(name) !== -1;
            return e('button', {
              key: name,
              className: 'chip sev-' + name + (on ? ' on' : ''),
              onClick: props.toggleSeverity(name)
            }, name);
          })),
        e('span', { className: 'grow' }),
        e(Dropdown, {
          label: props.filters.mode === 'regex' ? 'Filters · regex' : 'Filters',
          armed: props.filters.mode === 'regex' || props.filters.levelMax != null ||
                 !!props.filters.range || !!props.filters.since,
          wide: true
        }, function () { return e(Advanced, {
          filters: props.filters, setFilters: props.setFilters
        }); }),
        e(Dropdown, { label: 'Saved' }, function (close) {
          return e(Presets, {
            saved: props.saved,
            close: close,
            apply: function (f) {
              props.setFilters(decodeFilters(encodeFilters(f)) || emptyFilters());
            },
            copyPreset: function (f) { props.copyFilterLink(f); },
            save: function (name) {
              props.setSaved(function (prev) {
                return prev.filter(function (p) { return p.name !== name; })
                  .concat([{ name: name, filters: props.filters }]);
              });
            },
            remove: function (name) {
              props.setSaved(function (prev) {
                return prev.filter(function (p) { return p.name !== name; });
              });
            },
            copyLink: function () { props.copyFilterLink(props.filters); }
          });
        }),
        e('button', {
          className: 'btn' + (props.dockMode !== 'collapsed' ? ' armed' : ''),
          onClick: props.cycleDock
        }, 'Live lane'),
        e('button', {
          className: 'btn' + (props.inspectorOpen ? ' armed' : ''),
          onClick: props.toggleInspector
        }, 'Inspector'),
        e('button', {
          className: 'btn detailmode',
          onClick: props.toggleDetailMode,
          title: 'Where a record opens: beside the table, or under the row'
        }, props.detailMode === 'row' ? 'Under row' : 'Side panel'),
        e('a', { className: 'btn link', href: COCKPIT_URL }, 'Cockpit'))
  }

  function App() {
    var live = useLiveBuffer();
    var query = useQuery();

    var initial = useMemo(function () {
      if (window.location.hash.length > 1) {
        var fromUrl = decodeFilters(window.location.hash.slice(1));
        if (fromUrl) { return fromUrl; }
      }
      return emptyFilters();
    }, []);

    var formatState = useFormat();
    var format = formatState[0];
    var writeFormatState = formatState[1];
    var filtersState = useState(initial);
    var filters = filtersState[0];
    var setFilters = filtersState[1];

    useEffect(function () {
      var onHash = function () {
        var next = decodeFilters(window.location.hash.slice(1));
        if (next) { setFilters(next); }
      };
      window.addEventListener('hashchange', onHash);
      return function () { window.removeEventListener('hashchange', onHash); };
    }, []);

    var settledState = useState(initial);
    useEffect(function () {
      var timer = window.setTimeout(function () {
        settledState[1](filters);
      }, LOCAL_FILTER_DEBOUNCE_MS);
      return function () { window.clearTimeout(timer); };
    }, [filters]);
    var settledFilters = settledState[0];

    var savedState = usePersisted(PRESET_KEY, []);
    var layoutState = usePersisted(LAYOUT_KEY, { columns: DEFAULT_COLUMNS });
    var visible = useMemo(function () {
      var map = {};
      layoutState[0].columns.forEach(function (k) { map[k] = true; });
      return map;
    }, [layoutState[0]]);

    var dockState = useState('collapsed');
    var gridOpenState = useState(true);
    var selectedState = useState(null);
    var inspectorState = useState(true);
    var detailState = usePersisted(DETAIL_KEY, 'auto');
    var autoscrollState = useState(false);

    var queryScrollRef = useRef(null);
    var dockScrollRef = useRef(null);
    var mapHeightState = useState(240);

    var width = useViewport();
    var phone = width <= PHONE_MAX;
    var detailMode = detailState[0] === 'side' || detailState[0] === 'row'
      ? detailState[0] : (phone ? 'row' : 'side');
    var gridOpen = gridOpenState[0];
    var setGridOpen = gridOpenState[1];

    useEffect(function () { setGridOpen(!phone); }, [phone, setGridOpen]);

    useEffect(function () {
      var timer = window.setTimeout(function () {
        window.history.replaceState(null, '', '#' + encodeFilters(filters));
      }, 300);
      return function () { window.clearTimeout(timer); };
    }, [filters]);

    var columns = useMemo(function () {
      var chosen = COLUMNS.filter(function (c) { return visible[c.key]; });
      if (width <= PHONE_MAX) {
        var narrow = chosen.filter(function (c) {
          return PHONE_COLUMNS.indexOf(c.key) !== -1;
        });
        return narrow.length ? narrow : chosen.slice(0, 2);
      }
      if (width <= TABLET_MAX) {
        return chosen.filter(function (c) {
          return TABLET_DROP.indexOf(c.key) === -1;
        });
      }
      return chosen;
    }, [visible, width]);

    var toggleColumn = useCallback(function (key) {
      layoutState[1](function (prev) {
        var list = prev.columns.slice();
        var at = list.indexOf(key);
        if (at === -1) { list.push(key); } else { list.splice(at, 1); }
        return { columns: list };
      });
    }, []);

    var queryMore = query.more;
    var loadingMoreRef = useRef(false);

    var runQuery = useCallback(function () {
      query.run(filters);
      selectedState[1](null);
    }, [filters, query]);

    var setFormat = useCallback(function (key) {
      var was = format;
      setFilters(function (prev) {
        var next = Object.assign({}, prev);
        ['since', 'until', 'hideSince', 'hideUntil'].forEach(function (k) {
          var when = parseStamp(next[k], was);
          if (when && !isNaN(when)) { next[k] = formatStamp(when, key); }
        });
        return next;
      });
      writeFormatState(key);
    }, [format]);

    var loadOlder = useCallback(function () {
      if (loadingMoreRef.current) { return; }
      loadingMoreRef.current = true;
      queryMore(filters).then(function (added) {
        loadingMoreRef.current = false;
        if (!added) { return; }
        var el = queryScrollRef.current;
        if (el) { el.scrollTop = el.scrollTop + added * ROW_HEIGHT; }
      });
    }, [queryMore, filters]);

    var onGridKey = useCallback(function (ev) {
      if (ev.key === 'Enter') { runQuery(); }
    }, [runQuery]);

    var liveRows = useMemo(function () {
      var out = [];
      var snap = live.snapshot;
      for (var i = 0; i < snap.length; i += 1) {
        var rec = snap[i];
        if (rec._gap || localMatch(rec, settledFilters)) { out.push(rec); }
      }
      return out;
    }, [live.snapshot, settledFilters]);

    var queryRows = query.state.rows;
    var placeholder = query.state.status === 'loading'
      ? 'Querying\u2026'
      : (query.state.status === 'done'
          ? 'No rows matched these filters. Widen the time range, or clear a filter.'
          : 'Set filters above, then press Query or hit Enter.');

    var autoscroll = autoscrollState[0];
    var livePendingRef = live.pendingRef;
    var liveRefresh = live.refresh;

    useEffect(function () {
      if (!autoscroll) { return undefined; }
      var timer = window.setInterval(function () {
        if (livePendingRef.current > 0) { liveRefresh(); }
      }, 1000);
      return function () { window.clearInterval(timer); };
    }, [autoscroll, livePendingRef, liveRefresh]);

    useEffect(function () {
      if (!autoscroll) { return; }
      var el = dockScrollRef.current;
      if (el) { el.scrollTop = el.scrollHeight; }
    }, [liveRows.length, autoscroll]);

    var showNewest = useCallback(function () {
      liveRefresh();
      window.setTimeout(function () {
        var el = dockScrollRef.current;
        if (el) { el.scrollTop = el.scrollHeight; }
      }, 0);
    }, [liveRefresh]);

    var selectRecord = useCallback(function (rec) {
      if (detailMode !== 'row') { selectedState[1](rec); return; }
      selectedState[1](function (prev) { return prev === rec ? null : rec; });
    }, [detailMode]);

    var closeDetail = useCallback(function () { selectedState[1](null); }, []);

    var closeInspector = useCallback(function () {
      inspectorState[1](false);
    }, []);

    var jump = useJump(queryScrollRef, queryRows, selectedState[0],
                       selectedState[1]);

    useEffect(function () {
      var el = queryScrollRef.current;
      var apply = function () {
        if (el) { mapHeightState[1](el.clientHeight); }
      };
      apply();
      window.addEventListener('resize', apply);
      return function () { window.removeEventListener('resize', apply); };
    }, [dockState[0], queryRows.length]);

    useEffect(function () {
      if (query.state.status !== 'done' || !query.state.token) { return; }
      var el = queryScrollRef.current;
      if (el) { el.scrollTop = el.scrollHeight; }
    }, [query.state.token, query.state.status]);

    var stale = query.state.status === 'done' &&
                query.state.ranWith !== null &&
                query.state.ranWith !== encodeFilters(filters);

    var stats = useMemo(function () {
      var out = { fatal: 0, error: 0, warning: 0, info: 0, debug: 0 };
      queryRows.forEach(function (r) {
        if (out[r.severity_norm] != null) { out[r.severity_norm] += 1; }
      });
      return out;
    }, [queryRows]);

    var toggleSeverity = function (name) {
      return function () {
        var next = Object.assign({}, filters);
        var list = filters.severities.slice();
        var at = list.indexOf(name);
        if (at === -1) { list.push(name); } else { list.splice(at, 1); }
        next.severities = list;
        setFilters(next);
      };
    };

    var selectedKey = selectedState[0]
      ? (selectedState[0]._id == null ? null : selectedState[0]._id) : null;

    var rowDetail = inspectorState[0] && detailMode === 'row'
      ? selectedState[0] : null;

    useEffect(function () {
      if (!rowDetail) { return undefined; }
      var onKey = function (ev) {
        if (ev.key === 'Escape') { selectedState[1](null); }
      };
      window.addEventListener('keydown', onKey);
      return function () { window.removeEventListener('keydown', onKey); };
    }, [rowDetail]);

    var toastState = useState('');
    var toastTimer = useRef(null);
    var showToast = useCallback(function (text) {
      toastState[1](text);
      if (toastTimer.current) { window.clearTimeout(toastTimer.current); }
      toastTimer.current = window.setTimeout(function () {
        toastState[1]('');
      }, 2200);
    }, []);

    var copyFilterLink = useCallback(function (f) {
      var url = filterLink(f);
      writeClipboard(url, function (ok) {
        showToast(ok ? 'Link copied' : 'The browser blocked the copy');
      });
    }, [showToast]);

    var dockMode = dockState[0];
    var cycleDock = function () {
      dockState[1](dockMode === 'collapsed' ? 'half'
        : (dockMode === 'half' ? 'full' : 'collapsed'));
    };

    return e('div', { className: 'app dock-' + dockMode },
      e(Toolbar, {
        query: query, filters: filters, setFilters: setFilters, stale: stale,
        runQuery: runQuery, jump: jump, toggleSeverity: toggleSeverity,
        saved: savedState[0], setSaved: savedState[1],
        copyFilterLink: copyFilterLink,
        dockMode: dockMode, cycleDock: cycleDock,
        inspectorOpen: inspectorState[0],
        toggleInspector: function () { inspectorState[1](!inspectorState[0]); },
        detailMode: detailMode,
        toggleDetailMode: function () {
          detailState[1](detailMode === 'row' ? 'side' : 'row');
        },
        onClear: function () {
          setFilters(emptyFilters());
          query.clear();
          selectedState[1](null);
        }
      }),

      phone ? e('div', { className: 'fg-toggle' },
        e('button', {
          className: 'btn',
          onClick: function () { setGridOpen(!gridOpen); }
        }, gridOpen ? '▲ Hide the filter grid' : '▼ Filter grid')) : null,

      gridOpen ? e(FilterGrid, {
        filters: filters, setFilters: setFilters, visible: visible,
        format: format, setFormat: setFormat,
        toggleColumn: toggleColumn, onKeyDown: onGridKey
      }) : null,

      e('div', { className: 'workspace' },
        e('main', { className: 'querypane' },
          e(TableHead, { columns: columns, narrow: phone }),
          query.state.status === 'failed'
            ? e('div', { className: 'error' }, query.state.error)
            : e('div', { className: 'querybody' },
                e(LogTable, {
                  rows: queryRows, columns: columns, narrow: phone,
                  scrollRef: queryScrollRef,
                  selectedKey: selectedKey,
                  onSelect: selectRecord,
                  detail: rowDetail, onCloseDetail: closeDetail,
                  onReachTop: query.state.hasMore ? loadOlder : null
                }),
                e(ScrollMap, {
                  rows: queryRows, height: mapHeightState[0]
                }),
                !queryRows.length && query.state.status !== 'failed'
                  ? e('div', { className: 'empty' }, placeholder)
                  : null)),
        inspectorState[0] && detailMode === 'side' && dockMode !== 'full'
          && (!phone || selectedState[0])
          ? e(Inspector, {
              rec: selectedState[0], onClose: closeInspector
            })
          : null),

      e(LiveDock, {
        live: live, dock: dockMode, cycle: cycleDock, rows: liveRows,
        columns: columns, narrow: phone, scrollRef: dockScrollRef,
        mapHeight: dockMode === 'full' ? 400 : 180,
        selectedKey: selectedKey,
        onSelect: selectRecord,
        detail: rowDetail, onCloseDetail: closeDetail,
        dockInspector: dockMode === 'full' && inspectorState[0] &&
                       detailMode === 'side',
        inspectorRec: selectedState[0],
        onCloseInspector: closeInspector,
        showNewest: showNewest,
        autoscroll: autoscrollState[0],
        toggleAutoscroll: function () {
          autoscrollState[1](!autoscrollState[0]);
        }
      }),

      e('footer', { className: 'statusbar' },
        e('span', null, queryRows.length.toLocaleString() + ' of ' +
          query.state.count.toLocaleString() +
          (query.state.countRelation === 'gte' ? '+' : '') + ' matched'),
        query.state.loadingMore
          ? e('span', { className: 'loadingmore' }, 'loading older rows…')
          : (query.state.hasMore
              ? e('span', { className: 'hasmore' },
                  'scroll up for older')
              : null),
        query.state.took
          ? e('span', null, 'in ' + (query.state.took / 1000).toFixed(2) + ' s')
          : null,
        e('span', { className: 'sev-fatal' }, stats.fatal + ' fatal'),
        e('span', { className: 'sev-error' }, stats.error + ' error'),
        e('span', { className: 'sev-warning' }, stats.warning + ' warn'),
        e('span', { className: 'sev-info' }, stats.info + ' info'),
        e('span', { className: 'grow' }),
        phone ? null : e('span', { className: 'sql', title: query.state.sql },
          query.state.sql)),

      toastState[0]
        ? e('div', { className: 'toast', role: 'status' }, toastState[0])
        : null);
  }

  var root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(e(App));
}());
