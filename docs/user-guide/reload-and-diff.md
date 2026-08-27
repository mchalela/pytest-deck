# Reload and diff

Edit your tests, then press **↻ Collect** in the header. pytest-deck re-collects
the suite and compares the new collection against the previous one, so you can
see what changed without losing your place.

The status line summarizes the diff, for example:

```text
reload: +3 ~1 −2 · 142 tests
```

That reads as three tests added, one changed, two removed, out of 142 collected.

## What the diff flags

Re-collecting compares the two collections by test ID, the full
`path::Class::test[param]` name pytest uses. Each parametrized variant is its
own test ID, so adding or removing a parameter counts as an added or removed
test.

**Added** tests get a green `+` in the tree. These are test IDs present now that
weren't there before.

**Changed** tests get an amber `~`. A test is flagged changed when its **set of
markers** differs from the last collection. Markers are the one edit collection
can report for certain; pytest-deck doesn't try to detect changes to your test
code. So a `~` means the markers on this test moved, not that the test body
changed.

**Removed** tests are test IDs that were there before and are gone now. They
can't sit in the tree anymore. Any removed test that still had a selection or a
prior result moves to a foldable **Removed since last collect** list above the
tree. Each row shows the test's last-known result badge and its test ID, struck
through. A removed test with no selection and no result simply disappears;
there's nothing left to show for it.

## What is preserved

For every test that still exists, a re-collect keeps:

- your **selection** (its checkbox stays ticked), and
- its **prior result badge**, until you run again.

So the edit, run, re-collect loop stays fast. Fix a test, press **↻ Collect**,
and your selection and the surrounding results are still there. Only tests that
genuinely went away lose their place, and even those linger in the removed list
if they mattered.

```{note}
Coverage numbers are cleared on a re-collect. They describe the code as it was
before your edit, so keeping them would be misleading. Run again to get fresh
coverage. See [Coverage](coverage.md).
```

## Next steps

- [Coverage](coverage.md): turn on coverage and read it in the Run info pane and
  source gutter.
- [Using the dashboard](the-dashboard.md): the full tour of selecting, running,
  and reading results.
