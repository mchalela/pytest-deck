<!--
  Contributor reference: the load-bearing decisions. Read this BEFORE changing
  code. Each invariant is "X MUST hold because Y; breaking it breaks Z", with
  the file where it lives. These are not style preferences: every one was
  either a deliberate architectural choice or a bug already fixed once. If a
  change needs to violate one, that is a design decision to make consciously
  (and update this doc), not a detail to clean up.

  Companion: ARCHITECTURE.md (how it works). Line numbers drift; treat them as
  "look near here", and trust the described behavior over the exact line.
-->

# pytest-deck — Invariants (read before refactoring)

Load-bearing decisions and the reasons behind them. Grep for the anchor names
(`<a id="...">`) — [ARCHITECTURE.md](ARCHITECTURE.md) links here by them.

Legend: **[ARCH]** = deliberate architecture · **[BUG]** = fixed once, will
regress if broken · **[SPEC]** = external constraint (pytest/SSE/hatchling).

---

## <a id="subprocess"></a>Plugin & subprocess

- **P1 [ARCH] The inner plugin MUST NOT be a `pytest11` entry point** (only
  `pytest_deck.plugin` is — `pyproject.toml`). Auto-loading it would write JSON
  onto an inherited/unread fd in *every* pytest run on the machine, corrupting
  unrelated output. It's injected only via `-p pytest_deck._inner`.
- **P2 [ARCH] The outer plugin MUST be inert unless `--deck` is passed** —
  `pytest_cmdline_main` returns `None` when absent (`plugin.py`). Else installing
  pytest-deck perturbs every normal pytest run.
- **P3 [ARCH] `pytest_cmdline_main` MUST return an int when `--deck` is given** —
  it's `firstresult`, and that value is the exit code that suppresses the
  in-process test loop. Else the user's tests run in the launcher process too.
- **P4 [ARCH] Structured output MUST go on the dedicated fd (from
  `PYTEST_DECK_FD`), never captured stdout/stderr** — `--capture=fd` swallows fds
  1 and 2. Results would vanish into pytest's capture buffer.
- **P5 [SPEC] The inner plugin MUST read its fd number from `PYTEST_DECK_FD`, not
  hardcode 3** — `os.pipe()` returns an arbitrary number that `pass_fds`
  preserves. "fd 3" is a convention name only.
- **P6 [BUG] The `collection` line MUST be emitted only in collect mode**
  (gated on `config.option.collectonly`, returning early *before* building the
  items list). In run mode it's ignored downstream and its suite-scaled size
  overflowed the run reader's 1 MiB buffer → spurious "1 MiB buffer" errors on
  1000+ tests.
- **P7 [ARCH] The inner plugin MUST only READ `session.items`, never mutate it** —
  selection is done via CLI on the run subprocess, not in-process deselection.
- **P8 [SPEC] Markers MUST be read via `item.iter_markers()`** (not
  `own_markers`/`keywords`) to capture inherited class/module marks with args.
- **P9 [SPEC] Report serialization MUST use
  `config.hook.pytest_report_to_serializable`**, not text scraping.
- **P10 [ARCH] Every fd-3 record MUST carry a `$deck` key; readers MUST
  parse-by-known-key and skip the rest** — forward-compat for new record types +
  robustness to interleaved noise.
- **P11 [BUG] `-p no:xdist` MUST stay in the argv — and MUST come LAST** as
  long as the single-fd-3 transport is used — xdist workers don't inherit the
  fd-3 pipe, so the transport goes silent (no reports reach the parent). This is
  *the* documented landmine. Presence in `base_argv` alone is NOT sufficient
  anymore: a plain `-p name` in user-controlled extra args unblocks an earlier
  `-p no:name` (the last `-p` wins), so `_Run._argv` re-asserts the deck's
  blocks after any plugin/extra tokens. Footnote: "LAST" means last among the
  deck-controlled OPTION tokens — positional targets/nodeids ride after the
  re-asserted blocks on
  both collector and runner argv, and pytest's `consider_preparse` scans the
  WHOLE argv for `-p`, so a crafted positional pair (`?targets=-p,xdist`)
  would act as a plugin load after the blocks. Accepted within the
  localhost trust model (the same client can already run arbitrary test
  code); recorded so reviews don't rediscover it.
- **P12 [ARCH] `--import-mode=importlib` + fixed `--rootdir` + fixed `cwd` MUST
  all be present.** Fixed `--rootdir`/`cwd` keep nodeids stable across
  collect/run passes (nodeids are the UI's primary keys — a shift breaks
  selection + diff). The rootdir VALUE that gets fixed mirrors pytest's own
  discovery: bare `--deck` reuses the outer `config.rootpath` (pytest already
  searched from the invocation dir); `--deck PATH` re-derives via pytest's own
  `determine_setup` (`rootdir.discover_rootdir`), so PATH walks UP to the config
  anchor exactly as `pytest PATH` would — pinning rootdir to a launched-at subdir
  would push `cwd` too deep and make coverage key files above it as escaping
  absolute paths that the `/api/coverage` gate correctly rejects. `--deck PATH`
  keeps PATH as the initial collection target (rootdir ≠ scope), mirroring
  `pytest PATH`. `importlib` is required for a *different* reason:
  collision-free collection of a `__init__.py`-less tree with DUPLICATE
  test-file basenames (`a/test_utils.py` + `b/test_utils.py` — common), which
  `prepend`/`append` crash with exit 2. (Both import modes give stable nodeids,
  so the mode is NOT about nodeid stability — earlier wording was wrong.)
  importlib never adds the test dir to `sys.path`, so a top-level sibling import
  (`from helper import x`, helper.py adjacent, no package) would fail
  `ModuleNotFoundError`; the deck restores it (see P20 for the exact mechanism)
  by injecting the per-collected-file package roots — mirroring what pytest's own
  `prepend` mode adds — never a downward tree walk.
- **P20 [ARCH][SECURITY] The sibling-import inject MUST equal pytest prepend's
  package roots, injected via `-o pythonpath=`, NEVER a downward walk on
  `PYTHONPATH`.** Two independent correctness requirements, each with a real bug
  behind it:
  - **What dirs** (`import_paths.pkg_roots_for_files`): exactly the package root
    of each collected test/conftest file — walk UP while `__init__.py` exists
    (mirror `_pytest.pathlib.resolve_pkg_root_and_module_name`; no-`__init__`
    siblings fall back to the file's own dir), never every dir walked DOWN from
    rootdir. The old down-walk added 448 dirs on a real project incl. a vendored
    `scipy/` whose `scipy/signal/` **shadowed stdlib `signal`** → collection-fatal.
    Collect-with-no-targets resolves the file-set chicken-and-egg via a
    minimal-path pass-1, computing pkg_roots from its nodeids and re-collecting
    with a sibling inject only if pass-1 reported import-time collect errors
    (`collector.collect`).
  - **How injected** (`_subprocess.base_argv` `-o pythonpath=`): via pytest's OWN
    `pythonpath` ini (a collection-time `sys.path` insert, AFTER interpreter
    bootstrap), NOT the `PYTHONPATH` env var. The env var governs the child's
    Python bootstrap, so an injected dir holding a module shadowing a stdlib name
    imported at startup (`signal`, `subprocess`, `types`, `json`…) crashes the
    child BEFORE pytest runs — where plain pytest collects fine. `-o pythonpath=`
    REPLACES the user's ini value (last `-o` wins → ONE merged token), so the deck
    reads the user's ini `pythonpath` (`rootdir.read_ini_pythonpath`, via pytest's
    `determine_setup`) and merges it AFTER the deck dirs, **order-preserved**
    (pythonpath is order-significant; sorting would invert shadowing) — and
    degrades to `[]` on a malformed ini (the child surfaces the real error).
    Only the deck's OWN source-root stays on `PYTHONPATH` env (deck code, needed
    at bootstrap for `-p pytest_deck._inner`, no top-level modules to shadow).
- **P13 [ARCH] `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is intentional** — no
  third-party plugins load, for determinism + closing the xdist hazard. Changing
  it is a *product decision* (manifests are the sanctioned path), not a cleanup.
- **P14 [ARCH] The base argv/env MUST be built in the single `_subprocess`
  module** so collect and run stay byte-for-byte identical. Divergence = the tree
  shows something different from what runs.
- **P15 [ARCH] Deck subprocesses MUST neutralize every user plugin-loading
  channel that would fail under autoload-disable (P13)** — four mechanisms, four
  defenses: ini `addopts` and ini `required_plugins` via the `"-o", "addopts="` /
  `"-o", "required_plugins="` argv pairs (override-ini wins the first parse pass,
  even over early `-p` tokens inside addopts); env `PYTEST_ADDOPTS` and env
  `PYTEST_PLUGINS` by popping them from the child env (`PYTEST_ADDOPTS` is
  injected before override-ini; `PYTEST_PLUGINS` loads regardless of
  autoload-disable, and the inner plugin rides `-p`, never that var). Otherwise a
  user `addopts = --cov=…`/`required_plugins = pytest-cov`/hostile env would make
  *every* collect/run exit 4 or 1 while their terminal pytest works fine. Rest of
  ini (testpaths, markers, filterwarnings) stays honored. Verified on pytest
  8.4.2 and 9.1.1. Note: a *valid* `PYTEST_PLUGINS` would work under
  autoload-disable — dropping it is a determinism trade (same policy as P13),
  accepted silently for now; manifest re-admission is the fidelity path.
  **On that re-admission path the `-o addopts=` neutralization stays
  byte-unchanged** — ini addopts tokens come back only
  through the explicit one-path-per-token pipeline (`manifests.
  classify_addopts` over `rootdir.read_ini_addopts`, RUN subprocesses only):
  harvest → `ini_defaults` form prefill (the form is authoritative; harvested
  tokens never reach argv, so a cleared field can't be resurrected); re-admit →
  self-contained tokens under an ENABLED manifest's `flags` namespace, appended
  after plugin tokens / before user extra-args (P11 blocks still last), with
  the `RESERVED_FLAGS` denylist (`-p`, `-o`/`--override-ini`, `-c`,
  `--rootdir`, `--import-mode`) unforgeable by any namespace. Unforgeable is
  TRUE BY CONSTRUCTION (`manifests._is_reserved_flag`): long options match the
  exact name or its `=`-form (pytest 9.1.1 rejects abbreviations like `--rootd`),
  and short options are matched by scanning the WHOLE grouped cluster, not just
  `token[:2]` — pytest groups short options (`-sq` == `-s -q`) and the first
  value-taking one swallows the rest at ANY position, so a reserved short letter
  (`p`/`o`/`c`) riding as a non-leading char (`-xopythonpath=/evil` == `-x` +
  `-o pythonpath=/evil`, `-spxdist` == `-s` + `-p xdist`) is caught, closing the
  forgery that reopened the P20 pythonpath clobber and P15 addopts injection.
  Leftovers → click-to-apply extra-args suggestions, never silently
  dropped. Env `PYTEST_ADDOPTS` is NEVER re-admitted (ini only). Both ini
  readers share one pinned coercion helper (`rootdir._ini_tokens` — the
  pytest-9 native-TOML string degrades to `[]`, as pytest itself TypeErrors).
- **P17 [ARCH][SECURITY] A user manifest's `[env]` table MUST NOT set any
  `RESERVED_ENV` key** (`manifests.py`). Manifest env is applied to the run
  subprocess AFTER `build_env`, so an untrusted TOML setting one of these would
  either subvert deck integrity or write/read files outside the run tmpdir:
  `PYTEST_DECK_FD` (fd-3 transport P5), `PYTHONPATH` (the deck source-root inject
  for `-p pytest_deck._inner`, P20 — a read/exec vector), `PYTEST_ADDOPTS`/`PYTEST_PLUGINS` (the P15 plugin-loading
  defenses), `PYTEST_DISABLE_PLUGIN_AUTOLOAD` (P13),
  `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED` (BASE_ENV), `COLUMNS`/`LINES` (pty
  geometry), and **`COVERAGE_FILE` — an arbitrary-file-WRITE vector**: pytest-cov
  writes a SQLite DB to that path, so a user `[env]
  COVERAGE_FILE="/home/victim/.bashrc"` OVERWRITES that file when coverage runs,
  and the deck is run against arbitrary checked-out repos (dogfooding/review), so
  a hostile repo could silently destroy any file the user can write. (An
  earlier assessment that COVERAGE_FILE was "only a data-file redirect, safe
  for users" was wrong.) `parse_manifest(trusted=False)` (the user scan)
  REJECTS the whole manifest — never silently drops the key — so the author sees
  why. CURATED manifests are trusted code and skip the check: the flagship
  `coverage.toml` still sets `COVERAGE_FILE={tmpdir}/.coverage`, pinned under the
  run tmpdir. Assessed as no-ops/self-DoS under non-interactive
  `python -m pytest` (so NOT reserved): `PYTHONSTARTUP`/`PYTHONINSPECT`/
  `PYTHONEXECUTABLE` and `PYTHONHOME`; `COVERAGE_FILE` is the only file-WRITE
  vector the child's env exposes. The set is enumerated FROM what
  `build_env`/`BASE_ENV` set, P15 pops, and the write vector; a test pins it
  exactly (`test_user_manifests`) so dropping a var is caught. Trust reasoning: a
  user manifest lives in the user's own repo and may set any argv/transport (they
  already run their own test code on localhost — argv-as-tokens is theirs); the
  real escalations are env shadowing deck internals and env-driven file writes,
  which this gate closes.
- **P18 [ARCH] `plugin_data` carries a `render` discriminator; the frontend
  switches on it, no plugin-specific backend↔frontend coupling.**
  `"coverage"`/`"benchmark"`/`"metadata"` = the first-party slimmed shapes —
  the wire value comes from the per-id `SLIM_RENDERS` map colocated with
  `SLIMMERS`, never a hardcoded literal in the runner; `"json"`/`"text"` =
  generic pass-through read via `plugin_data.render_payload`; unknown render
  values are IGNORED by the frontend (P10 spirit), never routed into a
  first-party path. **Trust rule: an UNTRUSTED (user) manifest with a
  transport MUST declare an explicit `render = "json"|"text"`
  — it can NEVER satisfy the render gate by shadowing a first-party SLIMMERS
  id.** First-party surfaces make semantic claims ("this is your
  coverage/benchmark/environment"); the deck must never present user-file
  content as first-party-derived (same family as P17's "no lying switches" —
  rejection is loud at parse). With P19/fd3 this completes the rule: every
  first-party pipeline — slimmer, fd3, artifact_dir — is curated-only end to
  end; user manifests get the generic surfaces. The generic payload MUST be
  size-capped (`RENDER_MAX_BYTES` = 256 KiB) with a `truncated` flag — an
  unbounded artifact would blow the fd-3/SSE budget (same spirit as the
  coverage slimming). An over-cap JSON is reported as `{_truncated, bytes}`,
  never partially parsed. The SLIMMER read path has its own, deliberately
  larger cap (`SLIM_MAX_BYTES` = 32 MiB, `runner.py`) — do NOT reuse the
  256 KiB render cap there: a large real cov.json must keep slimming.
  Over-cap → `plugin_empty` with an OPTIONAL `reason` string the frontend
  shows in place of the generic hint (reason absent ⇒ old behavior).
  Scope of the exactly-one contract: "exactly one
  `plugin_data`/`plugin_empty` per declared transport" applies to runs that
  reach `finished` — usage-error (exit 4), cancelled, and killed runs
  deliberately emit NO plugin events (their transport output is meaningless,
  and the frontend already cleared plugin state at `markRunning`).
  Reading any transport artifact MUST degrade to `plugin_empty` on ANY failure
  (malformed / unreadable; for `render = "json"` payloads also nested past
  `RENDER_MAX_DEPTH` = 500 — a FIXED depth cap, because the interpreter
  recursion guard moves between CPython versions (3.14 parses ~50k deep where
  3.13 refuses) and the payload must also survive the SSE layer's `json.dumps`
  on a different stack; the slimmer and artifact-index paths need no depth cap
  — their outputs are structurally bounded and their broad catches already
  degrade) — never raise. And as DEFENSE IN DEPTH the `_read_transports` call in
  `_Run._wait` is wrapped so that even an unexpected transport-read error can
  NEVER prevent the terminal `finished`: **a run that exits ALWAYS emits
  `finished` (or `error`), regardless of transport-read outcome** — SSE has no
  replay (B9), so a `_wait` that emits nothing strands the run in the UI forever.
  And `_wait` MUST drain the fd-3 reader to EOF (`_drain_fd3` — bounded by
  `_JOIN_GRACE`, never raises) BEFORE consulting anything the reader feeds
  (`_saw_report`, the `_plugin_meta`/`_mpl_names` stashes): the waiter and the
  reader race after exit, and an undrained lagging reader resolves a record
  that DID arrive as a spurious `plugin_empty` (the letter of exactly-one-of
  held, but the WRONG event fired). Cancelled runs skip the drain — they
  resolve no transports and emit `cancelled` only.
- **P19 [SEC] The `artifact_dir` transport is curated-only AND tmpdir-contained —
  two independent gates**. It declares a `root` dir the runner reads a file
  index from (mpl `results.json`) and the HTTP endpoint (`/api/artifacts`) then
  serves RAW BINARY BYTES from — so `root` is an arbitrary-file-read-over-HTTP
  base, the read-twin of the `COVERAGE_FILE` write vector (P17). Gate 1: an
  `artifact_dir` transport in an UNTRUSTED (user) manifest is rejected at parse
  (`trusted=False` → `ManifestError`); only curated deck-shipped manifests may
  serve files. Gate 2 (defense in depth, applies even to curated): `root` MUST
  contain the literal `{tmpdir}` placeholder at parse AND `RunManager.artifact_root`
  re-verifies the substituted path resolves UNDER the run tmpdir at serve time —
  a curated-code bug still can't escape. The endpoint itself repeats the same
  two-gate realpath containment under `root`, serves by content-type with
  `nosniff`, forces non-images to attachment download, and NEVER puts a
  client-supplied nodeid in the served path (a nodeid is a response lookup key
  only — mpl-style path
  sanitizers diverge per plugin and break on parametrized/dup-basename tests).
- **P16 [ARCH] A manifest's `id` MUST be the plugin's `pytest11` entry-point
  name** — it is simultaneously the token `-p` resolves under autoload-disable
  (P13), the key the frontend sends config under, and the annotation-channel
  key. Dist names (`pytest-cov`) are display metadata only and MUST never
  appear in argv. Compiled plugin argv MUST stay a token list end to end (never
  a shell string), and `installed_plugins()` MUST be re-scanned at compile time
  — `-p <missing>` exits 1 before collection if a plugin vanished post-scan.
- **P21 [ARCH] Collect-side plugin compilation emits ONLY the `-p <id>` switch**
  (`manifests.compile_collect_argv`): fields, transport tokens, and `[env]` are
  RUN-only facets by construction — a plugin output
  flag on collect would truncate its file before the run reads it (the
  `FileType('wb')` class), and the collect env stays pristine. The tokens ride
  BOTH collector passes (P20) and the deck's `-p no:` blocks are re-asserted
  LAST after them (P11), even though `/api/collect` validates the ids.

---

## <a id="backend"></a>Backend: server & runner

- **B1 [ARCH] Results MUST arrive only over fd-3, never parsed from pty/stdout** —
  the pty carries color-formatted terminal text; fd-3 carries structured data.
- **B2 [BUG] The fd-3 `StreamReader` limit MUST stay ≫ 64 KiB (currently 1 MiB)** —
  a single `longrepr_text` line (full traceback) overflows the default. Do NOT
  shrink the buffer to fit PIPE_BUF-sized "atomic" writes; the design embeds
  full tracebacks.
- **B3 [BUG] The fd-3 reader MUST recover from overrun, not die** — a crash there
  drops all later reports, stranding tests at `incomplete`. The overrun `error`
  event carries `fatal: false` (all other `error` events are terminal); the
  frontend's `onError` MUST keep `run.active` and running chips for non-fatal
  errors — the run is still live and `finished` will still arrive. Same rule
  for a POISON line: `_Run._dispatch_fd3`'s per-line parse+dispatch catch is
  BROAD (`json.loads` raises past `JSONDecodeError` — `RecursionError` on a
  deeply-nested line, `UnicodeDecodeError` on non-UTF-8 bytes — and a
  valid-JSON non-dict breaks the kind dispatch); one bad line is skipped,
  never kills the reader. Collect sibling: `collector._iter_payloads`.
- **B4 [ARCH] `report`/`warning`/`plugin_data`/`plugin_empty`/`finished`/
  `cancelled`/`error`/`started` MUST NOT be dropped under backpressure; only
  `console` may (oldest-first)** — SSE is the sole results channel; a lost
  result strands a test forever (`plugin_data` carries the run's coverage
  totals and `plugin_empty` the "enabled but collected nothing" signal). Do
  **not** replace `Subscriber` (custom deque, `events.py`) with a plain bounded
  `asyncio.Queue` — it can't express the split.
- **B5 [ARCH] The child MUST spawn in its own process group
  (`start_new_session=True`) and be killed via `os.killpg`** — so plugin/subprocess
  children are reaped, not orphaned.
- **B6 [ARCH] A new run MUST kill the current one before spawning, under `_lock`** —
  the single-in-flight invariant; concurrent start/cancel must serialize. The
  old run MUST be `join()`ed **unconditionally** (even when its proc already
  exited): its fd-3/pty reader tasks may still be draining buffered lines, and
  a report they broadcast after the new run's `started` would be mis-tagged.
  `join()` is bounded (`_JOIN_GRACE`) so a grandchild holding EOF can't hang.
- **B7 [ARCH] `started` MUST be emitted and the subprocess spawned before
  `POST /api/run` returns `202`** — so the persistent SSE already carries the run
  when the client sees the run_id; the run then outlives the response.
- **B8 [ARCH] The SSE stream MUST NOT cancel a run on client disconnect — only
  unsubscribe** — other tabs keep watching.
- **B9 [ARCH][SPEC] The stream is live-only: subscribe re-emits `started` only
  while the run is live, and there is NO event replay.** The frontend must treat
  gap events as unrecoverable and resync via `GET /api/run/active`. Removing that
  endpoint or this property breaks the reconnect/unstick logic. *(This is the
  single fact that most shapes the frontend reconnect machine — see R-series.)*
- **B10 [ARCH] `/api/collect` and `/api/run/active` MUST stay plain `def`
  (threadpool); `/api/run`, `/api/cancel`, `/api/events` MUST stay `async`** —
  collect runs a blocking subprocess that would stall the loop as `async`.
- **B11 [BUG] The SSE `retry:` (1000ms) MUST stay ≤ the frontend's server-down
  grace and be sent on every event** — so a normal reconnect isn't misread as an
  outage and the client can tune its debounce to a known cadence.
- **B12 [ARCH] Exit 5 → `finished` (benign "nothing matched"); exit 4 + no reports
  → `error`** (usage/stale-selection; detail only on the console).
- **B13 [ARCH] `-k`/`-m` MUST be single `-opt=value` tokens; blank-after-strip
  values MUST be omitted** — a two-token pair lets argparse eat a `-`-leading
  expression as a flag (silent no-op); a whitespace `-m` deselects everything.
- **B14 [ARCH] Parent MUST close its copies of the fd-3 write-end and pty slave
  right after spawn** — else EOF never propagates and the readers hang.

---

## <a id="outcome-parity"></a>The outcome oracle (cross-layer)

- **O1 [ARCH] `frontend/src/lib/outcome.js` MUST match `pytest_deck/outcome.py`
  behavior exactly** — branch order, `wasxfail` handling, and the "setup passed
  but no call report → `incomplete`, never silently `passed`" rule. Both feed one
  badge spec; `tests/test_outcome_js_parity.py` asserts it. **Change both or
  neither.**
- **O2 [BUG-RISK] The parity test SKIPS SILENTLY when `node` is absent** — a
  Node-less CI would not catch outcome drift. Run it in a Node-present env after
  touching either file. (CI's frontend job provides Node.)

---

## <a id="reconnect"></a>Frontend: reconnect / server-down / resync

*The most-hardened code in the project. Every rule here fixed a real defect.*

- **R1 [SPEC] Detect server-down via a debounce timer + `readyState`, NEVER off
  `CLOSED`** — a dropped SSE (Ctrl-C'd server) goes to CONNECTING and retries
  forever, never CLOSED; a CLOSED source never fires `open` again, so keying off
  it both misses the outage and makes `onopen` self-heal dead code.
- **R2 [BUG] `SERVER_DOWN_GRACE_MS` (5s) MUST exceed the browser reconnect delay
  (~3s fallback) and the advertised `retry` (1s)** — else every normal reconnect
  flashes a false "server down" banner.
- **R3 [ARCH] A mid-run SSE drop MUST use the soft path (`markReconnecting`) and
  MUST NOT clear `running` chips or `run.active`** — because SSE has no replay
  (B9), tearing down a live run on a blip strands its tests at `incomplete`
  irrecoverably. The hard path (`markServerDown`, clears chips) is idle-only.
- **R4 [BUG] `resyncRunState` MUST retry bounded then FAIL OPEN (unstick), never
  lock** — the reconnect that triggers it stays up, so there's no second `onopen`
  to retry a flaky `/api/run/active` probe; a stuck lock is reload-only-escapable
  while a wrong unstick self-corrects (streaming SSE re-asserts via a fresh
  `started`/`report`).
- **R5 [BUG] `resyncRunState` MUST pin `run.id` and only act on a matching id** —
  so a concurrently-started run (another tab) isn't clobbered by a stale probe
  answer.
- **R6 [BUG] `serverDown` MUST be cleared globally on any proof-of-life** (any
  terminal/`started` event, or reload's `reconcileResults`) — it's an infra flag,
  not a test outcome; else it sticks magenta forever and leaks into `ghosts`,
  mislabeling a removed test's last-known badge.
- **R7 [SPEC] The browser `error` event (connection-level, no data) is DISTINCT
  from the named `error` SSE run-event (exit-code carrying)** — do not conflate.

---

## <a id="frontend-misc"></a>Frontend: other

- **F1 [ARCH] The SSE stream is opened exactly once, at App load, guarded against
  re-open** — opening per-component duplicates events.
- **F2 [ARCH] Reload reconciliation MUST keep each surviving record VERBATIM
  (`{phases,warnings,duration}`), not flatten to a scalar** — stays
  list-of-attempts-ready for rerunfailures/subtests. Removed-with-results records
  go to `ghosts` verbatim.
- **F3 [SPEC] Selection `Set` mutations MUST reassign the Set** — Svelte doesn't
  track in-place Set edits.
- **F4 [ARCH] Marker chips MUST only toggle selection, never build `-m`**; `-k`/`-m`
  are the separate expression fields. A run is valid with a selection OR a
  non-empty `-k`/`-m`; only ticked tests get the optimistic `running` sentinel.
- **F5 [ARCH] Annotation channels are independent per-node facts** —
  `clearChannel("diff")` must not disturb other channels (the extensible-column
  contract for future annotation channels).

---

## <a id="packaging"></a>Packaging & build

- **K1 [BUG] `pytest_deck/static/` is git-ignored and re-included via
  `artifacts = ["pytest_deck/static/**"]`, NOT `force-include`** — `force-include`
  adds the tree a second time and collides ("A second file is being added… at the
  same path") on the sdist→wheel path CI uses. This bit us; don't revert it.
- **K2 [ARCH] The frontend MUST be built (`vite build`) before the wheel** — else
  the bundle is absent and the dashboard serves nothing. CI's frontend job does
  this; a hand-built release must too.
- **K3 [SPEC] Dev proxy MUST disable buffering on `/api/events`** — else SSE only
  arrives at stream end.

---

## Where things live

The code cites these invariants by ID in short comments. The SSE fan-out lives
in `events.py`, report shaping in `reports.py`, the inner plugin is the
`DeckInnerPlugin` class, and the frontend transport lives in `connection.js`
with the reload choreography in `reload.js`. The transport is POSIX-only (pipe +
pty); there is no Windows fd fallback.
