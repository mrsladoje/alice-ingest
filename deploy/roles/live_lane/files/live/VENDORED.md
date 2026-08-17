# Vendored browser libraries

These two files are not ours. They are committed rather than fetched so the page
builds nothing and needs no network at deploy time, which is the same reason the
live lane is a standalone page and not a Dashboards plugin.

| File | Project | Version | Licence |
|---|---|---|---|
| `react.production.min.js` | React | 18.3.1 | MIT |
| `react-dom.production.min.js` | React DOM | 18.3.1 | MIT |

Fetched from:

    https://unpkg.com/react@18.3.1/umd/react.production.min.js
    https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js

SHA-256:

    d949f1c3687aedadcedac85261865f29b17cd273997e7f6b2bfc53b2f9d4c4dd  react.production.min.js
    35f4f974f4b2bcd44da73963347f8952e341f83909e4498227d4e26b98f66f0d  react-dom.production.min.js

## Why this line and not a newer one

The UMD build is what lets a browser load React from a plain `<script>` tag with
no bundler. React 19 removed UMD builds, so 18.3.1 is the last version that can
be used this way. Moving past it means adding a Node build step to a deploy that
currently has no JavaScript toolchain at all.

## Why no JSX

JSX has to be compiled. `live_lane.js` calls `React.createElement` directly,
which is what JSX compiles into, so the page runs exactly as written and there is
nothing to build or to break.
