<!--
  Contributor reference: how pytest-deck is built and how data flows through
  it. For the "what must not change" landmines, see INVARIANTS.md alongside
  this file. This document is descriptive (how it works today); INVARIANTS.md
  is prescriptive (what you must preserve). Keep both in sync with the code:
  when a change alters a stated fact here, update this doc in the same change.
-->

# pytest-deck — Architecture

pytest-deck is a pytest plugin that serves a live browser dashboard: collect a
suite as a foldable tree, select tests, run them, and watch results stream in.
This document maps the system for anyone about to change it. Companion:
[INVARIANTS.md](INVARIANTS.md) — the load-bearing decisions you must not break.

## 1. The one core idea: everything is a fresh subprocess

Collect, reload, and run are the **same operation** — build a pytest CLI
invocation from the browser's current selection and spawn a fresh
`python -m pytest`. There is no in-process pytest path and no separate "reload"
mechanism.

![The core pipeline: the browser sends a collect/run request; the server builds CLI args and spawns a fresh python -m pytest subprocess; structured JSON comes back on fd 3 and streams to the browser over SSE](_static/pipeline.svg)

<!-- Diagram source: _static/pipeline.dot — regenerate with
     dot -Tsvg docs/_static/pipeline.dot -o docs/_static/pipeline.svg -->


Why a fresh interpreter every time (not `pytest.main()` in-process): reload
correctness above all — Python module reload is unreliable (`sys.modules`
caching, partial `importlib.reload`, stale `Item` objects, pytest's
assertion-rewrite `.pyc` cache), so the only reliable "reload" is a new
interpreter with an empty `sys.modules`. Plus crash/hang isolation from the
server. See [INVARIANTS.md](INVARIANTS.md#subprocess) for the full argument.

## 2. Layers at a glance

| Layer | Files | Responsibility |
|---|---|---|
| Outer plugin | `plugin.py` | adds `--deck`, launches the server, otherwise inert |
| Inner plugin | `_inner.py` | `DeckInnerPlugin`, injected into subprocesses; emits structured JSON on fd 3 |
| Subprocess spec | `_subprocess.py` | the canonical argv/env every collect & run shares |
| Collect | `collector.py` | spawn `--collect-only`, read fd 3 → tree + markers + errors |
| Run | `runner.py` | `_Run` + `RunManager`: spawn a run, stream fd-3 reports + pty console |
| SSE fan-out | `events.py` | `Event` + `Subscriber` — the split-backpressure unit |
| Report shaping | `reports.py` | serialized pytest report → per-phase wire shape |
| Server | `server.py` | FastAPI app: static dashboard + JSON/SSE API |
| Tree/diff/outcome | `tree.py`, `outcome.py` | shape collection into a tree; fold phases → one outcome |
| Frontend | `frontend/src/` | Svelte 5 SPA, built into `pytest_deck/static/` |

## 3. Plugin & subprocess layer

### 3.1 Outer vs inner plugin — two plugins, opposite roles

- **Outer (`plugin.py`)** — the *only* `pytest11` entry point
  (`[project.entry-points.pytest11]` in `pyproject.toml`). Auto-loaded into
  every pytest run on the machine, so it
  must be **inert until asked**: adds `--deck` (`pytest_addoption`) and
  short-circuits in `pytest_cmdline_main` (a `firstresult` hook whose int return
  becomes the exit code, stopping pytest before the test loop so the user's tests
  never run in *this* process). Returns `None` when `--deck` absent → vanilla
  pytest untouched.
- **Inner (`_inner.py`)** — deliberately **NOT** a `pytest11` entry point.
  Injected only into subprocesses via `-p pytest_deck._inner`. Emits structured
  JSON on the inherited fd.

**Why the split is load-bearing:** if the inner plugin auto-loaded, it would write
JSON onto an inherited/unread fd inside every unrelated pytest run, corrupting
their output. If the outer plugin did real work before `--deck`, installing
pytest-deck would perturb every normal run. The split is what keeps install inert.

### 3.2 The fd-3 transport

- **Why a dedicated fd, not stdout.** pytest's `--capture=fd` redirects *both* fd 1
  and fd 2 into a buffer, so structured output there gets captured and mixed with
  test output. A dedicated fd that capture never touches is the clean channel.
- **"fd 3" is a convention, not a hardcode.** The plugin reads the real fd number
  from `PYTEST_DECK_FD` (`os.pipe()` returns whatever's free; `pass_fds` preserves
  the number). Falls back to fd 1 if the env var is missing.
- **JSON-line protocol**, one object per line, each with a `$deck` discriminator:
  `collection` (collect only), `collect_error`, `report` (run), `warning` (run),
  `plugin_meta` (run: pytest-metadata's stash dict, harvested in-process for the
  `metadata` slimmer), `mpl_name` (run, one per item: `nodeid` → dotted
  `module.Class.test` name, the join key the artifact index parser needs).
- **`collectonly`-gating (the huge-suite bug):** `pytest_collection_finish`
  fires in both modes but the `collection` line is meaningful only in collect mode
  — and its size scales with the suite, so on 1000+ tests it overflowed the run
  reader's 1 MiB buffer. Gated on `config.option.collectonly`, returning early in
  run mode *before* building the items list.
- **Forward-compat parse-by-known-key:** readers yield only `$deck`-tagged objects
  and dispatch on known kinds, ignoring the rest — new record types don't break old
  parsers, and interleaved noise never crashes the reader.

### 3.3 The standard subprocess invocation

Built in exactly one place (`_subprocess.py` `base_argv`/`build_env`) so collect
and run are byte-for-byte identical. Every flag is load-bearing:

| Element | Why it must stay |
|---|---|
| `sys.executable -m pytest` | fresh interpreter per invocation — the core reload-correctness/isolation decision; not `pytest.main()` in-process |
| `-p pytest_deck._inner` | injects the inner plugin (required *because* it's not an entry point) |
| `--import-mode=importlib` | collision-free collection of a `__init__.py`-less tree with duplicate test-file basenames (`prepend`/`append` crash those, exit 2). NOT for nodeid stability — both modes are stable; fixed `--rootdir`/`cwd` own that. See P12. |
| `-o pythonpath=<pkg roots + ini pythonpath>` (`base_argv`) | importlib never adds the test dir to `sys.path`, so a top-level sibling import (`from helper import x`, no package) would fail `ModuleNotFoundError`. The deck injects exactly the package root of each collected test/conftest file (`import_paths.pkg_roots_for_files` — mirroring what pytest's `prepend` mode adds, never a downward walk from rootdir), merged with the user's ini `pythonpath` (deck dirs first, order preserved) into ONE `-o pythonpath=` token. Pytest's own `pythonpath` ini inserts these into `sys.path` at collection time, AFTER interpreter bootstrap (P12/P20) |
| `PYTHONPATH=<deck source root only>` (`build_env`) | keeps `pytest_deck._inner` importable at bootstrap from a source checkout (no-op when installed). ONLY that dir rides the env var: it governs the child's interpreter bootstrap, so an injected test dir shadowing a stdlib name (`signal`, `json`, …) would crash the child before pytest even runs — the fixed downward-walk bug, and why the sibling dirs ride `-o pythonpath=` instead (P20) |
| `-p no:cacheprovider` | don't write `.pytest_cache` into the user's tree on every ephemeral run |
| `-p no:xdist` | **fd-3 landmine** — xdist workers don't inherit the fd-3 pipe → transport goes silent |
| `--rootdir` + fixed `cwd` | nodeids (the UI's primary keys) resolve consistently regardless of launch dir |
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` | no third-party plugins load → determinism + closes the xdist hazard; plugin manifests re-admit plugins explicitly (a *product* decision to change) |
| `-o addopts=` + `-o required_plugins=` + drop `PYTEST_ADDOPTS`/`PYTEST_PLUGINS` | user plugin-loading channels that would exit 4/1 under the row above are neutralized; a valid `PYTEST_PLUGINS` is also dropped as a determinism trade (P15) |
| `PYTHONDONTWRITEBYTECODE=1` | don't litter `__pycache__` on every run |
| `PYTEST_DECK_FD=<write_fd>` | tells the inner plugin which inherited fd to emit on |

**Testing plugins under the deck.** A consequence of `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` (P13): a `pytester` test that asserts on an *autoloaded* plugin's options/hooks won't see that plugin when its suite is run through the deck. Pytester's nested pytest — in-process or subprocess — inherits that env var, so no `pytest11` entry point loads in the grandchild and the assertion (`nomatch: '*--youropt=*'`) fails, even though the same test is green under a normal `pytest`/CI run. The fix is on the test side, not the deck: force-load the plugin under test with `-p <plugin>` in the `pytester` call (the more hermetic way to write such tests anyway). Under a normal run the entry point already loaded it, so pass `-p` *only* when autoload is disabled — otherwise pluggy rejects the double-registration. This repo's own `tests/test_plugin.py` uses a `_deck_pargs()` helper that keys off the env var for exactly this reason.

## 4. Backend: server & runner

### 4.1 Endpoint surface

Every route is registered in `create_app(rootdir)` (`server.py`), which builds
one `RunManager` per app and mounts `/assets` last so it can't shadow `/api`.

| Method | Path | sync/async | Why this form |
|---|---|---|---|
| GET | `/`, `/index.html` | `def` | static send |
| GET | `/api/collect?targets=&plugins=` | **`def`** | runs in the threadpool so the *blocking* collect subprocess never stalls the event loop while a run streams. `plugins` = comma-separated manifest ids whose bare `-p` switch the collect child gets (collect-scoped manifests only, P21). Returns `200 {tree, rootdir, errors}` or `500` only on a hard `CollectionError`; per-file import errors ride back as `errors` data, not a 500. |
| GET | `/api/plugins` | **`def`** | available plugin manifests: (curated + user-scanned) ∩ installed `pytest11` entry points, fresh scan per call → `{plugins}` for the left-bar switches (`manifests.py`, P16). Each entry carries `render` and `disabled_reason`. |
| POST | `/api/run` | `async` | awaits body + `manager.start()`; returns `202 {run_id}`. Empty selection = accepted "run all", not a 400. Optional `plugins`/`extra_args` (below) compile to argv; a bad plugin id or config → `400`, run not started. |
| POST | `/api/cancel` | `async` | `200 {cancelled, run_id}` |
| GET | `/api/run/active` | **`def`** | reconnect-resync probe → `{active}`. Pure in-memory predicate. Frontend polls it on reconnect to unstick a run that `finished` during an SSE gap. |
| GET | `/api/coverage/{run_id}/{file_path:path}` | **`def`** | on-demand source-gutter data → `{path, source, executed, missing, excluded}`. Reads the LAST run's cov.json (run tmpdir); `404` on stale/missing/traversal, never `500`. |
| GET | `/api/artifacts/{run_id}/{file_path:path}` | **`def`** | one raw artifact file from the run's tmpdir — the only surface streaming arbitrary binary bytes off disk to the browser (P19). Mirrors `api_coverage`: run-scoped lookup + two-gate realpath containment, `404` on stale/missing/escaping, never `500`. |
| GET | `/api/events` | `async` gen | the single persistent SSE stream |

A run request may carry `plugins` (manifest id → config dict; presence =
enabled) and `extra_args` (one raw string, tier-2 escape hatch). The server
compiles them (`manifests.compile_argv` / `compile_extra_args`, P16) into a
token list appended after the base/selection flags and before the positional
nodeids, plus manifest `[env]` values with `{tmpdir}` substituted by a
run-scoped temp dir (e.g. `COVERAGE_FILE`, so coverage never drops `.coverage`
into the user's tree). The tmpdir survives until the *next* run starts. Fields,
transport, and `[env]` apply to run subprocesses only; collect receives just the
`-p <id>` switch of collect-scoped manifests (P21). The `started` event's argv
echo carries the compiled tokens.

A manifest may also declare a `[transport]` (`type = "json_file"` or
`"text_file"`, an output `arg` token, and a `path` — both `{tmpdir}`-templated).
**The post-run transport.** When the child exits, the runner reads each
declared `path` and broadcasts an event **before** the terminal `finished`.
Each declared transport yields EXACTLY one of two events (never both, never
neither): `plugin_data` when the file is present and yields usable data, or
`plugin_empty` (`{run_id, plugin}`) when the plugin was enabled but produced
no usable data — an absent/unparseable file (e.g. `--no-cov` via extra
args) or coverage's "No data collected" shape. This lets the frontend
distinguish "here's data" from "enabled but collected nothing" (a one-line hint)
from "not enabled" (neither event). Both events are suppressed on the
cancel/kill/usage-error paths (only the normal-exit branch reads transports),
and both join the never-drop backpressure class.

**How the payload is shaped is the manifest's `render` discriminator.**
`plugin_data` always carries `{run_id, plugin, render, data}`; `render` tells the
frontend how to display `data` with NO plugin-specific frontend code:

| `render` | manifest declares | `data` shape | display |
|---|---|---|---|
| `"coverage"` | no `render`; id has a first-party slimmer (`pytest_cov`) | `{total, files: {relpath: pct}}` | the coverage panel / source gutter |
| `"benchmark"` | no `render`; slimmer (`benchmark`) | `{summary, tests: {nodeid: stats}}` | the tree's mean-time column, a stats table per pinned test, fastest/slowest in the Run info pane |
| `"metadata"` | no `render`; slimmer (`metadata`, fed by the `plugin_meta` fd-3 record) | key/value rows | the Environment section of the Run info pane |
| `"artifacts"` | `render = "artifacts"` + a `[transport]` of `type = "artifact_dir"` (`root`, `index`, `index_format`) | `{nodeid: [{name, rel_path, kind}]}` (runner-built index, joined via `mpl_name`) | the attachments pane; files are fetched through `/api/artifacts` |
| `"json"` | `render = "json"` | the parsed JSON value from the file | collapsible tree |
| `"text"` | `render = "text"` | the file's text as a string | `<pre>` block |

`SLIMMERS` and `SLIM_RENDERS` in `plugin_data.py` are the single source of the
first three rows (the render-map rule: the runner never hardcodes a render
name); the artifact index is built in `runner.py` from `INDEX_PARSERS`.

The `"coverage"` shape is the first-party slimmer (`plugin_data.SLIMMERS`, P16
— a transport with neither a slimmer nor a `render` is a manifest validation
error); the raw cov.json stays in the tmpdir for the source gutter. The
generic `"json"`/`"text"` shapes read the file through `plugin_data.render_payload`
and carry an extra `truncated: bool` (the artifact is size-capped at
`RENDER_MAX_BYTES` = 256 KiB so a huge file can't blow the fd-3/SSE budget — text
is cut to the cap; an over-cap JSON is reported as `data = {_truncated, bytes}`
rather than partially parsed into invalid JSON). JSON depth is capped too
(`RENDER_MAX_DEPTH` = 500, deeper degrades to `plugin_empty`): it keeps parsing
and the SSE layer's re-serialization off the interpreter's recursion guard,
which moves between CPython versions.

**On-demand coverage detail.** The SSE `plugin_data` stays slim
(percentages only); the heavy per-line hit/miss data is fetched only when the
user clicks a file, via `GET /api/coverage/{run_id}/{file_path}`. It reads the
last run's retained cov.json (`RunManager.coverage_file`), returns
`{path, source, executed, missing, excluded}` (source read fresh off disk), and
404s cleanly when the run isn't the last one, its tmpdir/cov.json is gone, or
the file wasn't measured. `file_path` is attacker-controlled, so two gates
guard it before any disk read: it MUST be a key in the cov.json `files` map AND
its realpath MUST resolve under rootdir (blocks `../`, absolute paths, and
symlink escapes; Starlette also collapses `..` URL segments before routing).

**User manifests.** Besides the curated manifests shipped in-package,
`available_manifests(rootdir)` scans `<rootdir>/.pytest-deck/plugins/*.toml`
(`user_manifests`) with the SAME loader/validation. Precedence: **user wins** on
a shared `id` — a manifest in the target repo is a deliberate override of the
deck's curated argv/render/env for that plugin (the repo is the user's; argv is
already theirs to control on localhost). The scan degrades gracefully: a
malformed or rejected file is skipped with a `UserWarning` naming it, so one bad
manifest never blanks the panel. Both sets are then filtered to installed
plugins (no lying switches).

**Trust boundary — the reserved-env gate (security-load-bearing).** Curated
manifests are code the project ships; user manifests are untrusted TOML. A user
manifest may set any argv tokens or `[transport]` (the user already runs their
own test code on localhost — argv-as-tokens is theirs), but its `[env]` table is
applied to the run subprocess AFTER `build_env`, so `parse_manifest(..., 
trusted=False)` rejects the whole manifest if any `[env]` key is in
`RESERVED_ENV` — the deck-integrity vars: `PYTEST_DECK_FD` (the fd-3 number),
`PYTEST_DISABLE_PLUGIN_AUTOLOAD` (P13), `PYTHONPATH` (the deck source-root
prepend that keeps `pytest_deck._inner` importable, P20 — a read/exec vector),
`PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED` (BASE_ENV), `PYTEST_ADDOPTS`/
`PYTEST_PLUGINS` (P15 pops these — re-adding them re-opens the plugin-loading
channels P15 closed), `COLUMNS`/`LINES` (the fixed pty geometry), and
`COVERAGE_FILE` — an arbitrary-file-WRITE vector: pytest-cov writes a SQLite DB
to that path, so a user `COVERAGE_FILE="/home/victim/.bashrc"` overwrites that
file (the deck runs against arbitrary repos). The CURATED coverage manifest
still sets it (trusted, bypasses the gate) pinned under the run tmpdir.
Rejection (not silent drop) means the author sees exactly why. A manifest may
also self-gate via `disabled_reason`: it
still appears on `/api/plugins` (frontend greys it with the reason) but a run
that tries to enable it gets a 400.

### 4.2 SSE model

- **One persistent stream, many tabs.** Each `/api/events` makes a `Subscriber`
  added to `RunManager._subscribers`; `broadcast()` fans every event to all. The
  run is decoupled from any one client.
- **Split backpressure — NOT a plain `asyncio.Queue`.** `Subscriber` is a custom
  `deque` + `asyncio.Event` waiter (`events.py`). Only `console` events are
  bounded (drop-oldest, cap 1000). `report`/`warning`/`finished`/`cancelled`/
  `error`/`started` are **never dropped** — SSE is the sole results channel, and a
  lost `call` report strands a test at `incomplete` forever. A bounded `Queue`
  cannot express this split.
- **`retry: 1000` on every event** — pins the browser's reconnect cadence so the
  frontend's server-down debounce can be tuned against a known value.
- **Heartbeat** `ping=15s` keeps the idle stream alive through proxies.
- **Disconnect only unsubscribes — never cancels the run** (other tabs keep
  watching).
- **CRITICAL: no replay.** The stream is live-only. `subscribe()` re-emits *only*
  the current run's `started`, and *only if the run is still live*. There is no
  per-event backfill. A dropped connection permanently loses gap events — which is
  the entire reason `/api/run/active` exists. Any reconnect logic must treat gap
  events as unrecoverable.

### 4.3 RunManager lifecycle

- **Single current run** (`self._run`); `start()`/`cancel()` serialize on
  `_lock`. **Kill-and-restart**: a new run kills the current one first. `run_id` is
  a monotonic `run-N`.
- **`_Run.is_alive`** (= `proc` spawned AND `returncode is None`) is THE single
  liveness predicate; `is_active()`/`subscribe`/`cancel`/`_kill_current`/`kill`
  all delegate to it, and `/api/run/active` exposes it. One definition — change
  it in one place only.
- **A run detaches from its POST.** `run.start()` spawns the subprocess, broadcasts
  `started`, and launches the three reader tasks (`_read_fd3`, `_read_pty`,
  `_wait`) **before** `POST /api/run` returns `202`. The run outlives the response.
- **Kill sequence**: process-group `SIGTERM` → 3s grace → `SIGKILL`
  (`start_new_session=True` so children are reaped, not orphaned).
- **Exit-code mapping**: cancel → `cancelled`; exit 4 + no reports → `error`
  (invalid `-k`/`-m` or stale nodeid; detail only on the pty console); exit 5
  (nothing matched) → `finished`, deliberately NOT an error.

### 4.4 Reading fd-3 without blocking the loop

- Async `StreamReader` over the inherited pipe (`connect_read_pipe`), drained with
  `readline()`. No threads, no `communicate()`.
- **1 MiB buffer limit** (`_FD3_LIMIT = 2**20`) — a single `longrepr_text` line
  (full rendered traceback) overflows the 64 KiB default.
- **Overrun recovery**: `readline` raises a bare `ValueError` on overrun;
  `_is_overrun` disambiguates it from a real failure and `_recover_overrun`
  realigns to the next newline + emits a truncated-report `error` rather than
  killing the reader (which would drop every later report).
- **pty for console, read separately** with `read(4096)` chunks (terminal output
  isn't line-clean); emits `console` events. **The pty is never parsed for
  results** — results come only over fd-3.

## 5. Frontend

**Stack:** Svelte 5 (runes) + Vite, plain (not SvelteKit). No router, no state
library — reactivity is runes over a handful of module-level `$state` stores.

### 5.1 Component tree

```
App.svelte                      (root: collect/run/cancel orchestration, 3-col layout)
├── PluginPanel.svelte          (sidebar: plugin switches, addopts suggestion chips)
│   └── PluginSwitch.svelte     (one switch + its typed config form)
├── MarkerChips.svelte          (select-only chips)
├── CollectErrorStrip.svelte    (per-erroring-file rows)
├── RemovedStrip.svelte         (reload: removed-but-had-stake rows)
│   └── StatusBadge.svelte      (the ghost's last badge)
├── TreeRow.svelte (recursive)
│   ├── StatusBadge.svelte      (leaf: live per-test badge)
│   ├── RollupBadge.svelte      (group: pass/fail tally)
│   ├── DiffBadge.svelte        (leaf: added/changed marker)
│   └── BenchBadge.svelte       (leaf: benchmark mean-time column)
└── DetailPane.svelte           (pinned test: phases, traceback, attachments)
    └── RunConsole.svelte       (nothing pinned: console, Run info, plugin panels)
        ├── CoverageSource.svelte  (a clicked file: source + hit/miss gutter)
        └── JsonTree.svelte (recursive; `render = "json"` payloads)
```

### 5.2 The reactive stores

Module-level `$state` objects; components import and read them, Svelte re-renders
on change.

| Store | File | Role |
|---|---|---|
| `results` | `results.svelte.js` | `byId: {nodeid → {phases, warnings, duration, running?, missing?, serverDown?}}` |
| `ghosts` | `results.svelte.js` | removed-but-retained records (verbatim) so a removed test keeps its badge |
| `run` | `results.svelte.js` | `{id, active, pending, status, level, console, k, m, reports, serverDown, reconnecting, pluginData, pluginEmpty, pluginEmptyReason, pluginRender, artifacts, artifactsRunId, pluginMeta}` |
| `ui` | `selection.svelte.js` | `{selected:Set, collapsed:Set, filter, detailId}` |
| `annotations` | `annotations.svelte.js` | per-node extensible columns (diff, benchmark timings; channel key = plugin id) |
| `collectErrors` | `collectErrors.svelte.js` | pytest ERRORS section |
| `collection` | `collection.svelte.js` | reload-diff generation + prev-leaves |
| `plugins` | `plugins.svelte.js` | the switch list from `/api/plugins` (enabled, config values, `disabled_reason`), addopts leftovers/suggestions, and `runPayload()` for the POST |
| `coverageView` | `coverageView.svelte.js` | the file open in the source gutter: fetch state, source, `classifyLines` hit/miss |

`results.svelte.js` is transport-free: it owns the stores, their mutations, and
the exported SSE event appliers (`onStarted`/`onReport`/…) — WHAT changes. The
transport — WHEN they're called — lives in `connection.js`. The reload
choreography (diff → annotate → reconcile results/selection, order-sensitive)
lives in `reload.js`, called from App's `doCollect`.

**One persistent SSE feed:** `connection.js` `connectEvents()` opens
`EventSource("/api/events")` **once** at load (guarded `if (source) return`).
Named events invoke the appliers; each `report` repaints exactly that leaf's
badge via `outcomeFor(nodeid)`.

### 5.3 Selection lives in the browser

- `ui.selected` (Set of leaf nodeids) → `api.js` POSTs `nodeids` to `/api/run`.
  Set mutations always **reassign** (Svelte only tracks Set reassignment).
- **Marker chips SELECT (tick boxes), they do NOT build `-m`** (locked decision).
  The `-k`/`-m` expression fields are separate. A run is valid with ticked tests
  **OR** a non-empty `-k`/`-m`.

### 5.4 The reconnect / server-down / resync state machine (CRITICAL)

The most-hardened code in the project (6 review rounds), split across
`connection.js` (the EventSource, debounce timer, `onopen` self-heal,
`resyncRunState`) and `results.svelte.js` (the `mark*`/`clear*`/unstick
mutations it calls). Full landmine detail in
[INVARIANTS.md](INVARIANTS.md#reconnect). In brief:

- **Detect via a CONNECTING debounce, never CLOSED** — a Ctrl-C'd server keeps the
  EventSource in CONNECTING (retrying forever), never CLOSED; a CLOSED source never
  fires `open` again (self-heal would be dead code).
- **Grace (5s) > browser reconnect delay + advertised `retry` (1s)** — else every
  normal reconnect flashes a false outage.
- **Hard vs soft:** idle drop → `markServerDown` (clears chips, red banner);
  mid-run drop → `markReconnecting` (soft banner, **preserves** chips/`run.active`
  because SSE has no replay — tearing down loses events irrecoverably).
- **`onopen` self-heals**; **`resyncRunState()`** polls `/api/run/active` to unstick
  a run that finished during the gap, **retries bounded then fails OPEN** (never
  locks), and **pins `run.id`** so a stale probe can't clobber a new run.

### 5.5 The build artifact

- `frontend/src` is **NOT** in the wheel — only the built bundle is.
- Vite builds to `../pytest_deck/static` (`emptyOutDir`), which is **git-ignored**
  and re-included for sdist+wheel via `artifacts = ["pytest_deck/static/**"]` in
  pyproject — **not** `force-include` (which double-adds and collides on the
  sdist→wheel path CI uses). **The frontend MUST be built before the wheel.**
- Dev: `vite dev` proxies `/api` → `127.0.0.1:8765`; the `/api/events` proxy
  disables buffering or SSE only arrives at stream end.

## 6. The outcome oracle (cross-layer)

`outcome.py::overall_outcome` folds a test's per-phase reports
(setup/call/teardown) into one display outcome
(passed/failed/error/skipped/xfailed/xpassed/incomplete). The ordering is
deliberate — failures are checked first, and a test whose setup passed but whose
call report never arrived is **`incomplete`, never silently `passed`**.

This function is **duplicated** in the frontend as a verbatim JS port
(`frontend/src/lib/outcome.js`) so the browser can fold outcomes client-side as
reports stream in. A parity test (`tests/test_outcome_js_parity.py`) checks the
JS against the Python oracle. **They must stay in lockstep — change both or
neither.** See [INVARIANTS.md](INVARIANTS.md#outcome-parity).

## 7. Data flow, end to end

1. Browser loads `/` → the built Svelte SPA from `pytest_deck/static/`.
2. SPA opens ONE persistent SSE stream to `/api/events` and calls `/api/collect`.
3. `/api/collect` spawns a `--collect-only` subprocess, reads the tree+markers
   JSON off fd 3, returns it. The SPA renders the tree.
4. User selects tests (checkboxes / marker chips / `-k`/`-m` fields). Selection
   lives entirely in the browser.
5. User clicks Run → `POST /api/run` with node IDs + k/m, plus any enabled
   plugin manifests' config and extra args (compiled server-side to argv
   tokens, §4.1). The server spawns a run subprocess; its
   `started`/`report`/`console`/`finished` events stream over the
   already-open SSE, tagged with a run_id.
6. The frontend folds each `report` into a live outcome (via `outcome.js`).
7. Reload = re-run step 3 and diff against the previous collection.

See [INVARIANTS.md](INVARIANTS.md) for the constraints that hold this together.
