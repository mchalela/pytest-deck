# Plugins

The sidebar's **Plugins** section turns third-party pytest plugins on and off
per run. This page covers every switch that ships with pytest-deck, plus the
extra args field below them.

A switch appears when two things are true: the plugin is installed in your
environment, and pytest-deck has a description of it (a *manifest*; see
[Plugin integration](../plugin-integration/overview.md)). The panel never
offers a switch that couldn't actually load. Tick a switch, press **▶ Run**,
and it applies to that run on top of your normal selection.

These switches ship with pytest-deck today:

| Switch | Plugin | What you get |
| --- | --- | --- |
| Coverage | `pytest-cov` | totals, per-file list, source gutter ([Coverage](coverage.md)) |
| Matplotlib figures | `pytest-mpl` | per-test images ([Attachments](attachments.md)) |
| Benchmarks | `pytest-benchmark` | mean-time tree column, per-test stats, a summary line in the Run info pane |
| Environment | `pytest-metadata` | an Environment section in the Run info pane |
| Mocking | `pytest-mock` | the `mocker` fixture, loaded as in your terminal |
| Async tests | `pytest-asyncio` | async tests collected and run as in your terminal |
| Django | `pytest-django` | Django suites collected and run as in your terminal |

You can add switches for plugins beyond these seven: see
[Writing your own manifest](../plugin-integration/writing-manifests.md).

## Benchmarks

Enable **Benchmarks (pytest-benchmark)** and run. Results show up in three
places:

- **The tree** gains a right-aligned mean-time column on every benchmarked
  test, auto-scaled to ns, µs, ms, or s. Hover a cell for the median and round
  count. The column appears only when the run produced benchmark data, and
  group rows show no rollup (mean times don't aggregate meaningfully).
- **The detail pane** shows the full stats table when you pin a test: min, max,
  mean, std dev, median, IQR, plus ops/sec, rounds, and iterations.
- **The Run info pane** adds one line with the count and the fastest and
  slowest by mean:

```text
Benchmarks: 4 · fastest test_pack 12.4 µs · slowest test_scan 3.1 ms
```

Expand the switch for three timing knobs:

- **Disable timing (`--benchmark-disable`)**: the fixture calls your function
  once, with no timing loop.
- **Min rounds (`--benchmark-min-rounds`)**
- **Max time per test, s (`--benchmark-max-time`)**

Everything else (warmup, timers, histograms, comparisons) can go through the
[extra args](#extra-args) field.

If benchmarks were enabled but no timing data came back, because no benchmark
fixtures ran or timing was disabled, the Run info pane says exactly that instead
of showing nothing.

## Environment (pytest-metadata)

Enable **Environment (pytest-metadata)** and the Run info pane gains a collapsed
**Environment** section: Python version, platform, packages, plugins, and any
extra metadata your suite adds, as key/value rows. Nested values fold like
JSON.

One detail worth knowing: the `Plugins` entry lists the plugins enabled *for
that deck run*, not everything installed. pytest-deck loads plugins explicitly
rather than autoloading, so the list follows your switches.

If metadata was enabled but nothing came back, the Run info pane says so.

## Mocking, async tests, and Django

**Mocking (pytest-mock)**, **Async tests (pytest-asyncio)**, and **Django
(pytest-django)** are plain switches: no config, no output panel. They exist
because pytest-deck loads plugins explicitly instead of autoloading everything
installed, and these three shape collection and execution itself.

Async tests fail without pytest-asyncio. A Django suite that imports models
can't even collect without pytest-django. All three also register ini options
your config may rely on. Flip the switch and the plugin loads exactly as it does
in your terminal.

Some plugins matter at collection time: these three, plus Benchmarks (the
plugin registers its marker). Toggling one of those switches triggers a
re-collect, and the tree change flows through the normal
[reload and diff](reload-and-diff.md) view. Run-only switches like Coverage
don't re-collect.

(extra-args)=
## Extra args and your ini `addopts`

Below the switches sits an **extra pytest args** field. Whatever you type is
split into tokens and handed to pytest as-is, with no shell involved, for
example `-x --tb=short`.

pytest-deck's subprocesses run with plugin autoloading disabled, so the deck
also sets aside the `addopts` line in your pytest config. Left in place, a
`--cov=web` there would crash every run whose Coverage switch is off. Nothing
is dropped silently, though. Say your config has:

```ini
# pytest.ini
[pytest]
addopts = --cov=web --benchmark-warmup=on -q
```

Each token comes back through one of three routes:

- **It prefills a switch field.** `--cov=web` matches the Coverage switch's
  Source field, so it seeds that field's starting value. From then on the form
  is in charge: clear the field and the token stays gone.
- **It rides with its plugin's switch.** `--benchmark-warmup=on` is a
  self-contained token (`--flag` or `--flag=value`) among pytest-benchmark's
  declared flags. It is added back to the run automatically **while the
  Benchmarks switch is on**. Switch the plugin off and all of it turns off
  with it.
- **It becomes a suggestion.** `-q` matches no switch, so it appears as a
  clickable chip under the field, labeled *from your ini addopts*. Click the
  chip to add it to extra args; ignore it and it stays out.

## When plugin data clears

Plugin output describes one run. Starting a new run or re-collecting clears it
all (coverage panels, benchmark columns, the Environment section, attachments),
and the next run repopulates them.

## Next steps

- [Coverage](coverage.md) and [Attachments](attachments.md): the two plugins
  with their own pages.
- [Plugin integration](../plugin-integration/overview.md): how pytest-deck knows
  about these plugins, and how to add your own.
