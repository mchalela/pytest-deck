# Using the dashboard

This page is the full tour: how to select tests, run them, and read the
results.

The dashboard has three columns: a **sidebar** for markers, filtering, and
plugins; the **test tree** in the middle; and a **detail pane** on the right.
Along the top is a header of run controls.

## The header

- **▶ Run** runs your current selection (see [below](#run-and-watch)).
- **▶ Re-run failed** lights up once a run leaves failures behind. It re-runs
  every test whose last result was `FAIL` or `ERROR`, ignoring your selection
  and the `-k`/`-m` fields (an expression could silently skip a failed test).
- **↻ Collect** re-collects the suite. See [Reload and diff](reload-and-diff.md).
- **■ Cancel** appears only while a run is in flight.
- The **-k** and **-m** fields are pytest expressions, applied on top of your
  tree selection (see [below](k-and-m-fields)).
- A **status line** on the right shows collection counts, live run progress, and
  the final exit code.

Run turns on once you've selected at least one test, or typed a `-k` or `-m`
expression. Both **▶ Run** and **↻ Collect** are disabled while a run is in
flight.

## The test tree

Your suite appears as a foldable **file, class, test** tree, with parametrized
variants as leaves. Click a caret (or a group's name) to fold and unfold it.

Each group row shows how many visible tests it holds. Once you've run, it also
shows a rollup of their outcomes: `✓` passed, `✗` failed, `!` error, `∅`
skipped.

Above the tree, a toolbar gives you **select all**, **clear**, **expand**, and
**collapse**.

### Selecting tests

Every row has a checkbox. Tick a test to select it, or tick a file or class to
take all of its tests at once. Group checkboxes are tri-state: filled when every
visible test under them is selected, and dashed when only some are.

Selection lives entirely in the browser. Nothing is written to disk or the
command line. The sidebar's **Selected** count tracks the running total.

### Marker chips

The sidebar lists every marker in your suite as a clickable chip with its test
count. Click a chip to select every visible test carrying that marker; click it
again to deselect them. A chip lights up when all its tests are selected, and
shows a dashed border when only some are.

Chips are a selection shortcut: they tick checkboxes, they don't build a `-m`
expression. For a marker *expression* like `slow and not db`, use the `-m` field
instead.

### The name filter

The **Filter** box hides any test whose ID doesn't contain the text you type. A
test ID is the full name pytest uses, `path::Class::test[param]`, and the match
is a case-insensitive substring over it. It's a view filter: it narrows what the
tree, chips, and counts show, without changing what's selected.

(k-and-m-fields)=
## `-k` and `-m` fields

The two expression fields in the header pass straight through to pytest:

- **`-k`** is a name filter. `login and not slow` matches every test with
  `login` in its name, minus the ones with `slow`.
- **`-m`** is a marker expression, like `slow and not db`.

They combine with your checkbox selection. A run applies your selected test IDs
*and* the expressions, just as `pytest <test-ids> -k "login and not slow"`
would. You can also run with an expression alone and no checkboxes ticked.

## Run and watch

Press **▶ Run**. Every selected badge flips to `running` at once, then to its
result the moment that test's report lands:

| Badge     | Meaning                                        |
| --------- | ---------------------------------------------- |
| `PASS`    | passed                                         |
| `FAIL`    | failed in the test body                        |
| `ERROR`   | failed in setup or teardown                    |
| `SKIP`    | skipped                                        |
| `XFAIL`   | expected failure                               |
| `XPASS`   | unexpectedly passed                            |
| `INCOMPL` | never reported a result: a crash mid-test, a cancelled run, or a run pytest rejected before it began |

Group rollups tick up as results stream in. A `⚠` next to a badge means the
test raised warnings.

When the run ends, the status line reports pytest's exit code:

```text
run finished (exit 0)
```

The run is a real `python -m pytest` subprocess, so the outcome matches what
you'd get running the same selection in your terminal.

## The detail pane

When no test is pinned, the right column shows the **Run info** pane: pytest's
own session header and final summary, in its native colors. It reads like the
terminal you already know:

```text
============================= test session starts ==============================
platform linux -- Python 3.13.1, pytest-8.4.1, pluggy-1.6.0
...
=========================== short test summary info ============================
FAILED tests/test_login.py::test_bad_password - AssertionError
========================= 1 failed, 5 passed in 0.42s ==========================
```

If a run ends without pytest's summary (you cancelled it, or the server went
away), the pane shows the whole output instead.

Click any status badge to pin that test and see its detail:

- Per-phase **tracebacks** (setup, call, teardown), in pytest's own colors.
- **Captured output**, for phases that produced it.
- A **warnings** section, if any were raised.

Click **✕ deselect** in the pane header to go back to the Run info pane. Badges
are clickable in every state: if a test hasn't run yet, was filtered out by `-k`
or `-m`, or was lost to a server restart, the pane shows a short note explaining
that instead of going blank.

## Next steps

- [Reload and diff](reload-and-diff.md): re-collect after an edit and see what
  changed.
- [Plugins](plugins.md): the sidebar's plugin switches, from coverage to
  benchmarks.
- [Coverage](coverage.md): turn on coverage and read it in the Run info pane
  and source gutter.
