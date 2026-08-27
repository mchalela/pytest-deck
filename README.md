# pytest-deck

[![CI](https://github.com/mchalela/pytest-deck/actions/workflows/ci.yml/badge.svg)](https://github.com/mchalela/pytest-deck/actions/workflows/ci.yml) [![Docs](https://readthedocs.org/projects/pytest-deck/badge/?version=stable)](https://pytest-deck.readthedocs.io/en/stable/) [![PyPI](https://img.shields.io/pypi/v/pytest-deck)](https://pypi.org/project/pytest-deck/)

An interactive browser dashboard for pytest. Collect your suite as a foldable
tree, pick what to run with checkboxes or marker chips, and watch results stream
in test by test, with tracebacks and captured output one click away.

Its defining promise: **a deck run matches a real command-line pytest run.**
Every collect, reload, and run is a fresh `python -m pytest` subprocess, never
in-process. What you see in the browser is what your terminal would say, and a
crashing or hanging test can't take the dashboard down with it.

<!-- screenshot: HERO. Full dashboard mid-run. Tree on the left with
     PASS/FAIL/SKIP statuses filling in and rollup counts on groups, marker
     chips visible, a failed test pinned so the detail pane shows a traceback
     in pytest's own colors. examples/ is a good subject. Shot or short gif. -->

> **Beta.** Collect, select, run with live streaming, reload with diff, plugin
> switches, coverage down to a source gutter, benchmark timings, and per-test
> attachments all work. See the
> [changelog](https://github.com/mchalela/pytest-deck/blob/main/CHANGELOG.md) and the
> [docs](https://pytest-deck.readthedocs.io/) for the full picture.

## Install

```bash
pip install pytest-deck
```

## Quick start

pytest-deck is a pytest plugin. Launch the dashboard with `--deck`:

```bash
pytest --deck                 # dashboard for the current project (rootdir)
pytest --deck path/to/tests   # dashboard for a specific path
```

Nothing runs in the terminal. The deck prints its URL
(`http://127.0.0.1:8765/` by default); open it in your browser and your suite
is there as a foldable tree.

Without `--deck` the plugin is inert: your normal `pytest` runs are completely
unaffected.

To see everything at once, point the deck at this repo's
[`examples/`](https://github.com/mchalela/pytest-deck/tree/main/examples) suite.
Install the plugins you want to try and run
`pytest --deck examples/`.

For the full tour, start with the
[quickstart](https://pytest-deck.readthedocs.io/en/latest/getting-started/quickstart.html).

## Features

### The live basics

- A foldable **file → class → test → parametrized-variant** tree with rollup
  counts on every group.
- Select with checkboxes, **marker chips** that bulk-select every matching
  test, and `-k` / `-m` expression fields.
- Hit **▶ Run** and **PASS / FAIL / ERROR / SKIP** stream in live as each test
  finishes, tracebacks in pytest's own colors, captured output alongside.
- **Re-run failed**: one click re-runs everything currently failed or errored,
  regardless of the selection.
- **↻ Collect** to re-collect after an edit: added, removed, and changed tests
  are flagged, and selection plus results are preserved for tests that still
  exist.

### Plugins

Installed plugins appear as switches in the sidebar, each with a typed config
form, so you never have to hunt through a plugin's flags. Switches ship
ready-made for:

- pytest-cov
- pytest-benchmark
- pytest-mpl
- pytest-metadata
- pytest-mock
- pytest-asyncio
- pytest-django

For any other plugin, a one-file TOML description (a *manifest*) in
`.pytest-deck/plugins/` adds a switch of your own.

Your ini `addopts` aren't lost either. Each token either prefills a switch's
config form, follows its plugin's switch automatically, or shows up as a
clickable suggestion chip. Nothing is dropped silently.

<!-- screenshot: FLAGSHIP. The coverage gutter. Detail pane showing a source
     file with the hit/miss gutter coloring, the coverage panel with per-file
     percentages and the run total also in frame. Shot or short gif (gif could
     be: tick Coverage → Run → click a file in the panel → gutter appears). -->

<!-- screenshot: SUPPORTING. Benchmark timings. Tree with the right-aligned
     mean-time column populated (mixed ns/µs/ms values from examples/bench),
     one test pinned so the detail pane's stats table is visible. -->

→ [Plugins guide](https://pytest-deck.readthedocs.io/en/latest/user-guide/plugins.html) ·
[Writing your own manifest](https://pytest-deck.readthedocs.io/en/latest/plugin-integration/writing-manifests.html)

### Standalone server

The dashboard also runs without the pytest CLI:

```bash
pytest-deck path/to/tests            # console script
python -m pytest_deck.server path/to/tests
```

One difference from `pytest --deck path/to/tests`: the standalone command
roots the project at the path you give, with no walk up to find the root.

Options: `--host` (default `127.0.0.1`), `--port` (must be free; default:
first free port from `8765`, up to `8785`, announcing any fallback), and
`--open` (open a browser tab on launch; off by default). Through pytest,
`--deck-port` is the same as `--port`.

## How it works

Every collect and run is a fresh pytest subprocess. A small plugin injected
into those subprocesses emits structured JSON on a dedicated file descriptor,
a private channel that keeps machine-readable results separate from pytest's
captured output. A FastAPI backend streams those events to the browser over
Server-Sent Events. The deep dive lives in the docs:
[How It Works](https://pytest-deck.readthedocs.io/en/latest/how-it-works/architecture.html).

## Documentation

Full documentation is at
[pytest-deck.readthedocs.io](https://pytest-deck.readthedocs.io/):

- [Getting started](https://pytest-deck.readthedocs.io/en/latest/getting-started/installation.html): install and first run.
- [User guide](https://pytest-deck.readthedocs.io/en/latest/user-guide/launching.html): the dashboard, plugins, [coverage](https://pytest-deck.readthedocs.io/en/latest/user-guide/coverage.html), [attachments](https://pytest-deck.readthedocs.io/en/latest/user-guide/attachments.html), troubleshooting.
- [Plugin integration](https://pytest-deck.readthedocs.io/en/latest/plugin-integration/overview.html): how manifests work and how to write one.
- [How it works](https://pytest-deck.readthedocs.io/en/latest/how-it-works/architecture.html): architecture and design invariants.

## Limitations

- **pytest-xdist is not supported.** The deck reads each run's results over a
  dedicated pipe, and xdist's worker subprocesses don't inherit that pipe, so
  results would never reach the dashboard. The deck therefore forces
  `-p no:xdist` for its own runs: if xdist is installed in your project it
  stays disabled here, and runs are serial.
- **Localhost only.** The server binds to `127.0.0.1` with no auth, and it
  executes your test code. Don't expose it on an untrusted network.

## Development

Requires Python 3.11+ and Node 24 LTS (for the frontend build).

```bash
# backend
pip install -e ".[test]"
pytest                       # the full suite

# frontend (Svelte 5 + Vite); builds into pytest_deck/static/
cd frontend
npm install
npm run build
```

Run the full matrix (Python 3.11 to 3.14 × pytest 8/9, plus the frontend build)
with `tox`. The
[development guide](https://pytest-deck.readthedocs.io/en/latest/contributing/development.html)
has the details.

> **pytest-deck** is written with AI assistance. The design decisions, the reviews,
> and the bugs are mine. AI-assisted contributions are welcome here, held to the
> same bar.

## License

MIT
