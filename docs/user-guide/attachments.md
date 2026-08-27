# Attachments

Some plugins produce files for each test: images, diffs, snapshots. When a
plugin pytest-deck knows about does that, the deck shows the files inline in
the detail pane, under an **Attachments** heading, for the test you have open.

The flagship example is [pytest-mpl](https://github.com/matplotlib/pytest-mpl),
which compares matplotlib figures against stored baselines.

## Turning on pytest-mpl

pytest-mpl needs to be installed in the same environment:

```console
$ pip install pytest-mpl
```

Once it's installed, a **Matplotlib figures (pytest-mpl)** switch appears in the
sidebar's **Plugins** section. Tick it and press **▶ Run**. It applies to that
run only, on top of your normal test selection.

Expand the switch for one option:

- **Compare against baseline (`--mpl`)**: on by default. This activates
  pytest-mpl's actual image comparison. Off, the plugin only checks that your
  `@pytest.mark.mpl_image_compare` tests run.

pytest-mpl finds your reference images in a `baseline/` folder beside each test
module (its default), which works as-is under the deck. If your suite keeps all
baselines in one central directory, pass an absolute `--mpl-baseline-path`
through the [extra args](#extra-args) field.

## Reading the attachments

Open a test that produced figures, and the detail pane grows an **Attachments**
section:

- **Images render inline**, each captioned with its role: `result`, `baseline`,
  or `diff` (not the filename). For a failing comparison you'll see all three
  side by side, so you can spot what moved without leaving the browser.
- **Other files** appear as download links.

Attachments belong to the test you have selected, and to the run that produced
them. Start a new run or re-collect and they're cleared, then repopulated by the
next run. Like [coverage](coverage.md), the figures describe the code as it was
when it ran.

```{note}
pytest-mpl only writes result and diff images for comparisons that **fail**. A
passing figure matches its baseline, so there's nothing to show. Open a failing
`mpl_image_compare` test to see the baseline, result, and diff side by side.
```

## Next steps

- [Plugins](plugins.md): the sidebar's plugin switches in the round.
- [Coverage](coverage.md): another plugin's results, presented the same way.
- [Plugin integration](../plugin-integration/overview.md): how pytest-deck knows
  about these plugins, and how to add your own.
