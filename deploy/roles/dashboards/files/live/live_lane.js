(function () {
  'use strict';

  var e = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;
  var useMemo = React.useMemo;
  var useCallback = React.useCallback;

  var CONFIG = window.LIVE_LANE_CONFIG || {};
  var BUFFER_MAX = CONFIG.bufferRows || 10000;
  var STREAM_URL = CONFIG.streamUrl || 'stream';
  var COCKPIT_URL = CONFIG.cockpitUrl || '/';
  var ROW_HEIGHT = 40;
  var ROW_HEIGHT_NARROW = 58;
  var OVERSCAN = 12;
  var COUNTER_INTERVAL_MS = 400;

  var SEVERITIES = ['fatal', 'error', 'warning', 'info', 'debug', 'system',
                    'unknown'];

  function programOf(rec) {
    return rec.rolename || rec.source_file || rec.log_source || '';
  }

  function hostOf(rec) {
    return rec.origin_host || rec.hostname || rec.host || rec.node || '';
  }

  function clockOf(rec) {
    var raw = rec['@timestamp'];
    if (!raw) { return ''; }
    var d = new Date(raw);
    if (isNaN(d.getTime())) { return String(raw); }
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' +
           pad(d.getSeconds());
  }

  function dateOf(rec) {
    var raw = rec['@timestamp'];
    if (!raw) { return ''; }
    var d = new Date(raw);
    return isNaN(d.getTime()) ? String(raw) : d.toISOString();
  }

  function useNarrow() {
    var query = '(max-width: 760px)';
    var initial = window.matchMedia ? window.matchMedia(query).matches : false;
    var narrow = useState(initial);
    useEffect(function () {
      if (!window.matchMedia) { return undefined; }
      var mql = window.matchMedia(query);
      var onChange = function (ev) { narrow[1](ev.matches); };
      if (mql.addEventListener) { mql.addEventListener('change', onChange); }
      else { mql.addListener(onChange); }
      return function () {
        if (mql.removeEventListener) {
          mql.removeEventListener('change', onChange);
        } else { mql.removeListener(onChange); }
      };
    }, []);
    return narrow[0];
  }

  function useLiveBuffer() {
    var bufferRef = useRef([]);
    var sinceRef = useRef(0);
    var snapshotState = useState([]);
    var pendingState = useState(0);
    var connectedState = useState(false);
    var droppedState = useState(0);

    var setSnapshot = snapshotState[1];
    var setPending = pendingState[1];
    var setConnected = connectedState[1];
    var setDropped = droppedState[1];

    useEffect(function () {
      var source = new EventSource(STREAM_URL);
      var lastId = 0;
      source.onopen = function () { setConnected(true); };
      source.onerror = function () { setConnected(false); };
      source.onmessage = function (ev) {
        var rec;
        try { rec = JSON.parse(ev.data); } catch (err) { return; }
        if (typeof rec._id === 'number') {
          if (lastId && rec._id > lastId + 1) {
            setDropped(function (d) { return d + (rec._id - lastId - 1); });
          }
          lastId = rec._id;
        }
        var buf = bufferRef.current;
        buf.push(rec);
        if (buf.length > BUFFER_MAX) {
          buf.splice(0, buf.length - BUFFER_MAX);
        }
        sinceRef.current += 1;
      };
      return function () { source.close(); };
    }, []);

    useEffect(function () {
      var timer = window.setInterval(function () {
        setPending(sinceRef.current);
      }, COUNTER_INTERVAL_MS);
      return function () { window.clearInterval(timer); };
    }, []);

    var refresh = useCallback(function () {
      sinceRef.current = 0;
      setPending(0);
      setSnapshot(bufferRef.current.slice());
    }, []);

    useEffect(function () {
      var timer = window.setTimeout(function () {
        if (bufferRef.current.length) { refresh(); }
      }, 700);
      return function () { window.clearTimeout(timer); };
    }, [refresh]);

    return {
      snapshot: snapshotState[0],
      pending: pendingState[0],
      connected: connectedState[0],
      dropped: droppedState[0],
      refresh: refresh,
      buffered: bufferRef.current.length
    };
  }

  function matches(rec, filters) {
    if (filters.severities.length &&
        filters.severities.indexOf(rec.severity_norm) === -1) {
      return false;
    }
    if (filters.host && hostOf(rec).toLowerCase()
        .indexOf(filters.host.toLowerCase()) === -1) {
      return false;
    }
    if (filters.program && programOf(rec).toLowerCase()
        .indexOf(filters.program.toLowerCase()) === -1) {
      return false;
    }
    if (filters.run && String(rec.run == null ? '' : rec.run)
        .indexOf(filters.run) === -1) {
      return false;
    }
    if (filters.text) {
      var needle = filters.text.toLowerCase();
      var hay = (rec.message || '') + ' ' + programOf(rec) + ' ' + hostOf(rec);
      if (hay.toLowerCase().indexOf(needle) === -1) { return false; }
    }
    return true;
  }

  function Row(props) {
    var rec = props.rec;
    var narrow = props.narrow;
    var cells = narrow
      ? [
          e('div', { className: 'r-line1', key: 'l1' },
            e('span', { className: 'r-time' }, clockOf(rec)),
            e('span', { className: 'r-host' }, hostOf(rec)),
            e('span', { className: 'r-prog' }, programOf(rec))),
          e('div', { className: 'r-line2', key: 'l2' }, rec.message)
        ]
      : [
          e('span', { className: 'r-time', key: 't' }, clockOf(rec)),
          e('span', { className: 'r-sev', key: 's' }, rec.severity_norm),
          e('span', { className: 'r-host', key: 'h' }, hostOf(rec)),
          e('span', { className: 'r-prog', key: 'p' }, programOf(rec)),
          e('span', { className: 'r-msg', key: 'm' }, rec.message)
        ];
    return e('div', {
      className: 'row sev-' + (rec.severity_norm || 'unknown') +
                 (props.selected ? ' selected' : ''),
      style: { top: props.top + 'px', height: props.height + 'px' },
      onClick: props.onSelect,
      role: 'button',
      tabIndex: 0
    }, cells);
  }

  function LogView(props) {
    var rows = props.rows;
    var narrow = props.narrow;
    var rowHeight = narrow ? ROW_HEIGHT_NARROW : ROW_HEIGHT;
    var scrollState = useState(0);
    var heightState = useState(600);
    var ref = useRef(null);

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
    var start = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
    var end = Math.min(total,
                       Math.ceil((scrollTop + viewport) / rowHeight) + OVERSCAN);

    var visible = [];
    for (var i = start; i < end; i += 1) {
      var rec = rows[i];
      visible.push(e(Row, {
        key: rec._id == null ? i : rec._id,
        rec: rec,
        narrow: narrow,
        top: i * rowHeight,
        height: rowHeight,
        selected: props.selectedId != null && rec._id === props.selectedId,
        onSelect: function (r) {
          return function () { props.onSelect(r); };
        }(rec)
      }));
    }

    return e('div', {
      className: 'logview',
      ref: ref,
      onScroll: function (ev) { scrollState[1](ev.target.scrollTop); }
    }, e('div', {
      className: 'spacer',
      style: { height: (total * rowHeight) + 'px' }
    }, visible));
  }

  function Detail(props) {
    var rec = props.rec;
    if (!rec) { return null; }
    var keys = Object.keys(rec).filter(function (k) { return k !== '_id'; });
    keys.sort();
    return e('aside', { className: 'detail' },
      e('div', { className: 'detail-head' },
        e('strong', null, 'Record'),
        e('button', { className: 'ghost', onClick: props.onClose }, 'Close')),
      e('div', { className: 'detail-body' },
        e('div', { className: 'kv' },
          e('span', { className: 'k' }, 'event time'),
          e('span', { className: 'v' }, dateOf(rec))),
        keys.map(function (k) {
          return e('div', { className: 'kv', key: k },
            e('span', { className: 'k' }, k),
            e('span', { className: 'v' }, String(rec[k])));
        })));
  }

  function Filters(props) {
    var f = props.filters;
    var set = props.setFilters;
    var update = function (key) {
      return function (ev) {
        var next = Object.assign({}, f);
        next[key] = ev.target.value;
        set(next);
      };
    };
    var toggleSeverity = function (name) {
      return function () {
        var next = Object.assign({}, f);
        var list = f.severities.slice();
        var at = list.indexOf(name);
        if (at === -1) { list.push(name); } else { list.splice(at, 1); }
        next.severities = list;
        set(next);
      };
    };
    return e('div', { className: 'filters' },
      e('div', { className: 'sevs' }, SEVERITIES.map(function (name) {
        var on = f.severities.indexOf(name) !== -1;
        return e('button', {
          key: name,
          className: 'chip sev-' + name + (on ? ' on' : ''),
          onClick: toggleSeverity(name),
          'aria-pressed': on ? 'true' : 'false'
        }, name);
      })),
      e('div', { className: 'inputs' },
        e('input', { placeholder: 'host', value: f.host,
                     onChange: update('host') }),
        e('input', { placeholder: 'program', value: f.program,
                     onChange: update('program') }),
        e('input', { placeholder: 'run', value: f.run,
                     onChange: update('run') }),
        e('input', { className: 'wide', placeholder: 'search text',
                     value: f.text, onChange: update('text') }),
        e('button', {
          className: 'ghost',
          onClick: function () {
            set({ severities: [], host: '', program: '', run: '', text: '' });
          }
        }, 'Clear')));
  }

  function App() {
    var live = useLiveBuffer();
    var narrow = useNarrow();
    var filtersState = useState({
      severities: [], host: '', program: '', run: '', text: ''
    });
    var selectedState = useState(null);
    var filters = filtersState[0];
    var selected = selectedState[0];

    var rows = useMemo(function () {
      var out = [];
      var snap = live.snapshot;
      for (var i = snap.length - 1; i >= 0; i -= 1) {
        if (matches(snap[i], filters)) { out.push(snap[i]); }
      }
      return out;
    }, [live.snapshot, filters]);

    var status = live.connected ? 'live' : 'reconnecting';

    return e('div', { className: 'app' },
      e('header', null,
        e('h1', null, 'Live log'),
        e('span', { className: 'status ' + status }, status),
        e('span', { className: 'count' },
          rows.length.toLocaleString() + ' of ' +
          live.snapshot.length.toLocaleString() + ' shown'),
        live.dropped
          ? e('span', { className: 'dropped' },
              live.dropped.toLocaleString() + ' dropped by the server')
          : null,
        e('a', { className: 'ghost link', href: COCKPIT_URL },
          'Maintainer Cockpit')),
      e(Filters, { filters: filters, setFilters: filtersState[1] }),
      e('div', { className: 'newbar' + (live.pending ? ' armed' : '') },
        e('button', {
          className: 'refresh',
          disabled: !live.pending,
          onClick: function () { live.refresh(); selectedState[1](null); }
        }, live.pending
            ? live.pending.toLocaleString() + ' new — show newest'
            : 'up to date')),
      e('main', null,
        e(LogView, {
          rows: rows,
          narrow: narrow,
          selectedId: selected ? selected._id : null,
          onSelect: function (rec) { selectedState[1](rec); }
        }),
        selected
          ? e(Detail, {
              rec: selected,
              onClose: function () { selectedState[1](null); }
            })
          : null));
  }

  var root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(e(App));
}());
