# Troubleshooting

The failures you're most likely to hit, and how to clear them. Each one is
**symptom → what it means → fix**. Almost every case here is the same result
you'd get from a terminal `pytest`; the deck just shows it in the browser.

## The launch fails: port already in use

You pinned a port and the command exits immediately with one line:

```text
pytest-deck: port 9000 is already in use (another deck?). Stop it or pass a different --deck-port
```

**What it means.** Something is already listening on that port, usually an
earlier deck you left running.

**Fix.** Stop the other server, or pin a different port with `--deck-port` (or
`--port` for the standalone command). Or drop the flag entirely: without a
pinned port, pytest-deck picks the first free port from `8765` and prints the
URL it chose. See [Launching the dashboard](launching.md).

## "Collection failed" (the whole tree is gone)

You press **↻ Collect** and instead of a tree you get a red **Collection
failed** panel with pytest's error text, for example:

```text
pytest collection failed (exit code 4).

ImportError while loading conftest '/path/to/project/conftest.py'.
conftest.py:3: in <module>
    from helpers import setup_db
E   ModuleNotFoundError: No module named 'helpers'
```

**What it means.** A hard collection failure: pytest couldn't build the test
tree at all. Usually a broken `conftest.py`, an import error at module scope, or
a bad `pytest.ini` / `pyproject.toml` config. Since nothing collected, there's
no tree to show. The first line is the deck's; everything under it is pytest's
own output.

**Fix.** The panel shows the same error a terminal `pytest` would print. Fix the
file it points at, then press **↻ Collect** again.

## Some files error, but others collect fine

The tree is there, but a foldable **Collection errors (2)** row sits above it,
and some of your files are missing from the tree.

**What it means.** This is pytest's per-file ERRORS section. One or a few test
files failed to import (a bad import, a syntax error), but the rest of the suite
collected fine. pytest reports the good tests and lists the broken files
separately; the deck does the same.

**Fix.** Expand the row and click an entry to open its traceback in the detail
pane. Fix that file and press **↻ Collect** to clear it. You can run the tests
that *did* collect in the meantime; the errored files just won't be among them.

## Coverage comes back empty ("no data collected")

You enabled coverage and ran, but got no percentage and no files. The panel
reads:

```text
Coverage enabled but no data collected. Check your --cov target (or --no-cov in extra args).
```

**What it means.** Almost always the **Source (`--cov=`)** value points at
something that was never imported during the run, so `pytest-cov` measured
nothing.

**Fix.** Make the `--cov` target the **import package name**: the underscored
name you'd write in `import X`, not the pip distribution name and not the source
folder. A project installed as `my-package` but imported as `my_package` needs
`--cov=my_package`. Or give a path to the source directory. Leaving Source empty
measures everything. See [Coverage](coverage.md) for the full field description.

## "No tests matched the selection / filter"

A run finishes, but nothing ran, and the status line reads:

```text
no tests matched the selection / filter
```

**What it means.** This is **not an error**. pytest exited cleanly (exit code
5) because your `-k` / `-m` expression excluded every selected test. With no
checkboxes ticked, it means the expression matched nothing in the suite. A
renamed or removed test never lands here; that case is
[below](#the-run-errors-out-selected-tests-not-found).

**Fix.** Loosen or correct the expression, then run again.

## The run errors out: "selected tests not found"

You press **▶ Run** and the run ends at once with a red status line:

```text
invalid filter expression (-k/-m) or selected tests not found. Check the console for pytest's error. See the run console for pytest's message
```

Every selected test's badge reads `INCOMPL`. For a stale test ID, the Run info
pane shows pytest's `ERROR: not found: ...` line under the summary banner.

**What it means.** pytest refused the run before any test started (a usage
error, exit code 4). One of three things happened:

- **A stale test ID.** You selected a test, then renamed or removed it before
  running. The tree still lists it, but pytest can't find that
  `path::Class::test[param]` anymore.
- **A selected test's file no longer imports.** A syntax or import error crept
  in since your last collect, so pytest can't reach the test.
- **A bad expression.** The `-k` or `-m` text isn't valid pytest syntax.

**Fix.** Press **↻ Collect** to refresh the tree against your current source.
A renamed test shows up as removed plus added in the diff, and a broken file
lands in the **Collection errors** row (see
[Reload and diff](reload-and-diff.md)). Re-select and run again. For a bad
expression, fix the text in the header field.

## Some selected tests show "not found"

A run finishes, some tests have results, but one or more badges read
**not found** instead.

**What it means.** pytest ran, but never reported anything for that test. It
was in your selection, and the run ended without it. Usually your `-k` / `-m`
expression deselected it while the rest of the selection ran. Or `-x` in the
extra args stopped the run at the first failure, before pytest reached it.

**Fix.** Adjust the expression (or drop `-x`) and run again. Click the badge
for a short note on the test itself.

## Next steps

- [Coverage](coverage.md): the `--cov` target and reading the results.
- [Reload and diff](reload-and-diff.md): re-collecting, and where removed tests go.
- [Using the dashboard](the-dashboard.md): selecting, running, and reading results.
