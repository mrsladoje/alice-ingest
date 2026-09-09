# Vendored browser libraries

These files are not ours. They are committed rather than fetched so the page
builds nothing and needs no network at deploy time, which is the same reason the
shifter view is a standalone page and not a Dashboards plugin.

| File | Project | Version | Licence |
|---|---|---|---|
| `preact.umd.js` | Preact | 10.26.4 | MIT |
| `hooks.umd.js` | Preact Hooks | 10.26.4 | MIT |

`preact-shim.js` is ours. It is twenty lines that publish `window.React` and
`window.ReactDOM` on top of those two globals, so `shifter.js` and
`templates.js` call `React.createElement` and `ReactDOM.createRoot` and do not
know the difference.

Fetched from:

    https://unpkg.com/preact@10.26.4/dist/preact.umd.js
    https://unpkg.com/preact@10.26.4/hooks/dist/hooks.umd.js

SHA-256:

    6ba7a5946990492ba7fc40e79530a1164739f586077070e558878a51d341c0b5  preact.umd.js
    ccc0a594540115e4992f4925734868c4ecacfb0e604e86c47b11cdf41a6a56cc  hooks.umd.js

## Text inputs use `onInput`

Preact's `onChange` on a text input is the DOM event, which fires on blur.
Every text input in this page therefore uses `onInput`, which fires on every
keystroke in Preact and in React alike. `onChange` stays on the one checkbox,
where the DOM event is already the right one.

## UMD and no JSX

The UMD build is what lets a browser load a library from a plain `<script>`
tag with no bundler. `shifter.js` and `templates.js` call `React.createElement`
directly, which is what JSX compiles into, so the page runs exactly as written
and there is nothing to build.
