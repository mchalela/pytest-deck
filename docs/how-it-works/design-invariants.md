# Design invariants

A handful of decisions shape everything else about pytest-deck. They're the
things it won't casually change, because each one is what keeps a deck run
matching a real command-line pytest run. Here they are, in plain language, with
the reasoning behind them.

(fresh-subprocess)=

## Fresh subprocess, never in-process

Every collect and every run happens in a brand-new `python -m pytest`
subprocess. pytest-deck never imports your test modules into its own server
process.

There are two reasons this matters.

The first is reload fidelity. Once Python has imported a module, it caches it,
and re-importing gives you the cached copy, not your edited code. The only
reliable way to pick up an edit is a fresh interpreter with an empty module
cache. Because pytest-deck spawns a new process for every collect, reloading
after you change a test always sees the current code, exactly as if you had
re-run pytest in your terminal.

The second is isolation. A test that crashes the interpreter, hangs forever, or
exhausts memory takes down only its own throwaway subprocess. The dashboard keeps
running, shows you what happened, and lets you try again.

## Two plugins, opposite roles

pytest-deck ships two plugins with deliberately opposite jobs.

Installing pytest-deck adds one plugin that pytest loads automatically on every
run, but it does nothing unless you pass `--deck`. So an ordinary `pytest` on
your machine is completely unaffected by having pytest-deck installed.

When you do pass `--deck`, that plugin hooks into pytest's main entry point and
returns an exit code straight away. Because that hook stops at the first answer
it's given, pytest never starts its normal test-running loop in that process.
pytest-deck launches the dashboard instead, and your tests run later, in the
subprocesses the dashboard spawns.

The second plugin is the results reporter. It is never auto-loaded. pytest-deck
injects it only into the subprocesses it spawns, which is exactly why it isn't
registered as an ordinary plugin: it should touch nothing but deck-launched runs.

## A dedicated results channel

By default pytest captures a test's output at the operating-system level. It
redirects both stdout and stderr, so anything written there is swallowed and
folded into captured output. That makes the console a poor place to send
machine-readable results, because pytest's own capture would eat them.

So pytest-deck streams its structured results over a separate, dedicated file
descriptor that pytest's capture never touches (3 by convention, though the exact
number is assigned at spawn time). Each line is one JSON object.

The results themselves are produced by pytest's own report-serialization hook,
not by scraping console text. The colored tracebacks you see are rendered by
pytest itself. pytest-deck displays pytest's output; it does not re-format or
re-highlight your tracebacks.

(import-paths)=

## Faithful import paths and rootdir

For the deck to match your terminal, it has to import your tests the same way
pytest does, from the same project root.

pytest-deck picks its project root the same way pytest itself does: it starts
from the path you point it at and walks upward to the nearest config file
(`pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`, or `setup.py`). Pointing
`--deck` at a subfolder still finds the real project root, and a bare `--deck`
reuses the root pytest already determined.

pytest-deck also runs pytest in importlib import mode, which lets it collect
projects that have same-named test files in different folders (and folders
without an `__init__.py`) without the import collisions the default mode can hit.
This is purely about reliable collection; your test IDs are the same under either
import mode.

There's one more subtlety worth calling out, because it's easy to get wrong. To
make a test's plain `from sibling import helper` work the same way it does in your
terminal, pytest-deck hands those folders to pytest through pytest's own pythonpath
setting rather than the `PYTHONPATH` environment variable. pytest adds them to the
import path once it has started up, after Python itself has finished loading and
before collection, so a project module can never accidentally take the place of a
standard-library module during interpreter start. Only pytest-deck's own code sits
on `PYTHONPATH`, and only so the results reporter can load.

(outcome-oracle)=

## One outcome oracle, two implementations

A single test produces several reports as it runs: one for setup, one for the
call, one for teardown. Turning those into the one word you see on a badge (PASS,
FAIL, ERROR, SKIP, and so on) takes a small set of rules. For example, a failure
in the test body is a FAIL, but a failure in setup or teardown is an ERROR; and a
test whose setup passed but whose call report never arrived is marked
*incomplete* rather than silently passed.

Those rules are the outcome oracle, and there is exactly one specification for
them. It's written once in Python and ported to JavaScript for the browser, and
the two are checked against each other so they can never drift. The server sends
the browser the raw per-phase reports and lets the browser fold them, so the
badge you see always follows the same rules the server would apply.

The frontend side of this is covered under [Frontend](frontend.md).
