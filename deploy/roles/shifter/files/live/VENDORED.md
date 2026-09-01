# Vendored browser libraries

These files are not ours. They are committed rather than fetched so the page
builds nothing and needs no network at deploy time, which is the same reason the
live lane is a standalone page and not a Dashboards plugin.

| File | Project | Version | Licence |
|---|---|---|---|
| `preact.umd.js` | Preact | 10.26.4 | MIT |
| `hooks.umd.js` | Preact Hooks | 10.26.4 | MIT |

`preact-shim.js` is ours. It is twenty lines that publish `window.React` and
`window.ReactDOM` on top of those two globals, so `shifter.js` keeps calling
`React.createElement` and `ReactDOM.createRoot` and does not know the difference.

Fetched from:

    https://unpkg.com/preact@10.26.4/dist/preact.umd.js
    https://unpkg.com/preact@10.26.4/hooks/dist/hooks.umd.js

SHA-256:

    6ba7a5946990492ba7fc40e79530a1164739f586077070e558878a51d341c0b5  preact.umd.js
    ccc0a594540115e4992f4925734868c4ecacfb0e604e86c47b11cdf41a6a56cc  hooks.umd.js

## Why Preact and not React

React 18.3.1 was here first and worked. It cost **142.6 kB** on the wire,
**47.3 kB** gzipped, of which `react-dom` alone was 131.8 kB. Preact with hooks
is **15.2 kB**, **6.0 kB** gzipped: the same component model, the same hooks, the
same `h(type, props, children)` call that `React.createElement` compiles to, for
one ninth of the bytes. On a phone on the CERN wireless network that is the
difference between a page that appears and a page that is fetched.

Nothing in `shifter.js` changed for the swap except three `onChange` handlers
on text inputs, which became `onInput` — see below.

## The one behavioural difference that matters

React rewrites `onChange` on a text input to fire on every keystroke. Preact's
core does not: `onChange` there is the DOM event, which fires on blur. Every
text input in this page therefore uses `onInput`, which means the same thing in
both libraries. `onChange` survives on the one checkbox, where the DOM event is
already the right one.

If Preact ever has to be reverted, restore the two React UMD files, put them back
in the asset loop in `tasks/main.yml` and in the page shell, delete the shim, and
leave `onInput` alone. It works in React too.

## Why UMD and no JSX

The UMD build is what lets a browser load a library from a plain `<script>` tag
with no bundler. JSX would have to be compiled; `shifter.js` calls
`React.createElement` directly, which is what JSX compiles into, so the page runs
exactly as written and there is nothing to build or to break.
