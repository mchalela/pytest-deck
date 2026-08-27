# Coverage

pytest-deck reads coverage from [pytest-cov](https://pytest-cov.readthedocs.io),
its flagship plugin. Turn coverage on for a run and you get a total percentage, a
per-file breakdown, and a line-by-line source view showing exactly what ran.

## Turning it on

Coverage needs `pytest-cov` installed in the same environment:

```console
$ pip install pytest-cov
```

Once it's installed, a **Coverage (pytest-cov)** switch appears in the sidebar's
**Plugins** section. Tick it, then press **▶ Run**. Coverage applies to that run
only, on top of your normal test selection.

Expand the switch to set two options:

- **Source (`--cov=`)**: what to measure. Use the **import package name**: the
  name you'd write in `import X`, with underscores. It is not the pip
  distribution name, and not the source folder. A project installed as
  `my-package` but imported as `my_package` uses `--cov=my_package`. You can also
  give a path to the source directory. Leave it empty to measure everything,
  which is `pytest-cov`'s own default.
- **Branch coverage**: also track which branches were taken, not just which
  lines ran.

```{tip}
An empty coverage report almost always means the `--cov` target was never
imported: usually a mistyped name, the hyphenated distribution name, or the
folder name where the import name was needed. See
[Coverage comes back empty](troubleshooting.md#coverage-comes-back-empty-no-data-collected).
```

```{note}
Enabling coverage never drops a `.coverage` file into your project. pytest-deck
points it at a temporary directory for the duration of the run.
```

## Reading the results

After the run, the Run info pane (the detail pane with no test pinned) gains a
coverage section:

- A **total** at the top, for example `Coverage: 87.4%`.
- A **list of measured source files**, sorted worst-covered first, so the files
  that most need attention are at the top. Each row shows the file's path and its
  percentage.

This list is keyed by **source file**, which is different from the test tree in
the middle column. The tree is organized by your *test* files; coverage measures
your *source* files. They're two different sets, so coverage gets its own panel
rather than a column on the tree.

## The source gutter

Click any file in the coverage list to open it in the right pane with a
line-by-line gutter:

- **Green** lines ran during the test.
- **Red** lines are statements that never ran.
- Untinted lines are blanks, comments, and other non-statements, which coverage
  doesn't count either way.

The header shows how many lines were missed. Click **← Run info** to go back to
the coverage list.

```{note}
The gutter reads its source and hit/miss data from the run that produced it. If
you start a new run or re-collect, that data is replaced, so an old file view
closes back to the Run info pane. Just re-run to get a fresh gutter.
```

## When it's stale or unavailable

Coverage describes the code as it was at the time of the run. Re-collecting after
an edit clears it, because the old numbers would describe code you've since
changed (see [Reload and diff](reload-and-diff.md)). Run again for fresh figures.

If coverage was enabled but produced no data, for example because the `--cov`
target was never imported, the panel says so and points you at the `--cov`
target instead of showing an empty result. See
[Coverage comes back empty](troubleshooting.md#coverage-comes-back-empty-no-data-collected)
for the exact message and how to fix it.

## Next steps

- [Plugins](plugins.md): the rest of the sidebar's switches, from benchmarks to
  environment metadata.
- [Plugin integration](../plugin-integration/overview.md): how pytest-deck knows
  about coverage, and how other plugins fit in.
- [Reload and diff](reload-and-diff.md): why coverage resets when you re-collect.
- [Troubleshooting](troubleshooting.md): what to do when coverage comes back
  empty, or a run reports no tests.
