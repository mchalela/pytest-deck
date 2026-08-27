# pytest-deck

**An interactive browser dashboard for pytest.**

Collect your suite as a foldable **file → class → test** tree, pick what to run
with checkboxes or clickable marker chips, hit **▶ Run**, and watch
PASS / FAIL / ERROR / SKIP results stream in test by test. Tracebacks and 
captured output are one click away.

```console
$ pip install pytest-deck
$ pytest --deck
```

Installing is **inert until you pass `--deck`**. Ordinary `pytest` runs are
completely unaffected.

## Why pytest-deck

Its defining promise: **a deck run matches a real command-line pytest run.**
Every collect, reload, and run happens in a fresh `python -m pytest` subprocess,
never in-process. What you see in the browser is exactly what you'd get in the
terminal, and a crashing or hanging test can't take the dashboard down with it.

```{grid} 2
:gutter: 3

:::{grid-item-card} 🌳 Browse
Your suite as a foldable file → class → test → parametrized-variant tree, with
live rollup counts on every group.
:::

:::{grid-item-card} 🎯 Select
Checkboxes for individual tests or whole groups, clickable **marker chips** that
bulk-select every matching test, and `-k` / `-m` expression fields to narrow
further.
:::

:::{grid-item-card} ▶️ Run live
Hit **▶ Run** and watch PASS / FAIL / ERROR / SKIP fill in as each test finishes,
streamed over Server-Sent Events. Click any result for the traceback and
captured output.
:::

:::{grid-item-card} 🔁 Re-collect
Edit your tests and hit **↻ Collect**. Added, removed, and changed tests are
flagged, and selection plus results are preserved for tests that still exist.
:::
```

## Next steps

- **New here?** Start with [Installation](getting-started/installation.md) and
  the [Quickstart](getting-started/quickstart.md).
- **Using the dashboard day to day?** See the
  [User Guide](user-guide/launching.md).
- **Integrating a plugin like coverage?** See
  [Plugin Integration](plugin-integration/overview.md).
- **Curious how it's built?** See [How It Works](how-it-works/architecture.md)
  and the [API Reference](api/public-api.md).

```{admonition} Beta software
:class: warning

pytest-deck is pre-1.0 ({{ release }}) and under active development. It binds to
localhost only, has no authentication, and executes your test code, so don't
expose it on an untrusted network. It also does not support pytest-xdist (runs
are serial). See [Limitations](limitations.md) for details.
```

```{toctree}
:maxdepth: 2
:caption: Getting Started
:hidden:

getting-started/installation
getting-started/quickstart
```

```{toctree}
:maxdepth: 2
:caption: User Guide
:hidden:

user-guide/launching
user-guide/the-dashboard
user-guide/reload-and-diff
user-guide/plugins
user-guide/coverage
user-guide/attachments
user-guide/troubleshooting
```

```{toctree}
:maxdepth: 2
:caption: Plugin Integration
:hidden:

plugin-integration/overview
plugin-integration/coverage-example
plugin-integration/writing-manifests
plugin-integration/manifest-reference
```

```{toctree}
:maxdepth: 2
:caption: How It Works
:hidden:

how-it-works/architecture
how-it-works/design-invariants
how-it-works/frontend
```

```{toctree}
:maxdepth: 2
:caption: API Reference
:hidden:

api/public-api
api/internals
```

```{toctree}
:maxdepth: 2
:caption: Project
:hidden:

contributing/development
contributing/quality-and-ci
limitations
roadmap
```
