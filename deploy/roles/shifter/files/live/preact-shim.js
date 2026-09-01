(function (global) {
  'use strict';
  var p = global.preact;
  var h = global.preactHooks;
  global.React = {
    createElement: p.h,
    Fragment: p.Fragment,
    useState: h.useState,
    useEffect: h.useEffect,
    useLayoutEffect: h.useLayoutEffect,
    useRef: h.useRef,
    useMemo: h.useMemo,
    useCallback: h.useCallback,
    useReducer: h.useReducer
  };
  global.ReactDOM = {
    createRoot: function (container) {
      return {
        render: function (vnode) { p.render(vnode, container); },
        unmount: function () { p.render(null, container); }
      };
    }
  };
}(window));
