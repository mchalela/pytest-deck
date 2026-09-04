# Changelog

## Unreleased

### Added

- **Copy button on tracebacks.** Every traceback, captured-output, and
  warnings block in the detail pane has a **copy** button in its corner that
  puts the plain text on the clipboard, colors stripped.
- **Clickable badges in the run summary.** The `FAILED` and `ERROR` lines of
  pytest's short test summary in the Run info pane now carry the same status
  badge as the tree; click one to pin that test.

## 0.1.0 (beta), 2026-08-27

The beta theme is **plugin interop**: the alpha could collect, select, run, and
reload your suite; the beta makes third-party plugins first-class citizens of
the dashboard.

### Added

- **Plugin switches.** Installed plugins appear in the sidebar as switches with
  typed config forms; each applies to the next run on top of your selection.
  Curated manifests ship for pytest-cov, pytest-benchmark, pytest-mpl,
  pytest-metadata, pytest-mock, pytest-asyncio, and pytest-django.
- **Coverage end-to-end** (pytest-cov): run total, per-file coverage panel, and
  a clickable line-by-line hit/miss source gutter.
- **Attachments** (pytest-mpl): per-test files render inline in the detail
  pane; matplotlib figure comparisons show as images next to the traceback.
- **Benchmark timings** (pytest-benchmark): auto-scaled mean-time column in the
  tree, full stats table per pinned test, fastest/slowest in the run summary.
- **Environment section** (pytest-metadata): Python, platform, packages, and
  suite metadata in the run summary.
- **User manifests.** A one-file TOML in `.pytest-deck/plugins/` adds a switch
  for any plugin the deck doesn't curate, with generic JSON/text output
  renderers.
- **Ini `addopts` pickup.** The deck strips your ini `addopts` from its
  subprocesses (autoloading is off), but no token is dropped silently: each one
  prefills a switch's config field, follows its plugin's switch automatically,
  or appears as a clickable suggestion chip.
- **Re-run failed**: one click re-runs every test whose last result was failed
  or error, ignoring the current selection.
- **Examples suite.** `examples/` in the repo demos every curated plugin.
- **Port selection.** `--deck-port` (pytest CLI) and `--port` (standalone) bind
  exactly the given port or fail with a clear one-line error; with no port
  given the server falls forward from `8765` to the next free port (up to
  `8785`) and announces the one it picked.

### Changed

- Switches whose plugin matters at collection time (asyncio, django, mock,
  benchmark) trigger a re-collect on toggle, flowing through the normal
  reload-and-diff view.
- Toolchain brought current for release: the test matrix covers Python 3.11
  to 3.14, CI and the frontend build run on Node 24 LTS with Vite 8, npm
  audit is a CI gate, and Dependabot watches every pinned ecosystem.
- Deeply nested `render = "json"` payloads (past 500 levels) now degrade to
  the "no data" hint instead of depending on the interpreter's recursion
  limit, which moved in Python 3.14.
- Sibling-import handling reworked: the deck now injects package roots via
  pytest's own `pythonpath` mechanism, fixing collection failures (stdlib
  shadowing) on real-world layouts.

### Fixed

- A round of robustness and security hardening across the subprocess channel,
  the artifact endpoints, and coverage-file isolation, plus many smaller
  streaming and reload fixes found while dogfooding.
- The startup lines (root directory and dashboard URL) are flushed as they
  are printed, so they show up when stdout is a pipe or a log file instead of
  only at exit.
