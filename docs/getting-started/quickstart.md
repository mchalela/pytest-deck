# Quickstart

From install to your first tests running in the browser. You'll need an
existing pytest suite.

## 1. Install

```console
$ pip install pytest-deck
```

See [Installation](installation.md) for requirements and virtual-environment
tips.

## 2. Launch the dashboard

From your project directory:

```console
$ pytest --deck
```

pytest-deck starts a local server and prints its URL:

```text
pytest-deck serving /path/to/your/project
  → open http://127.0.0.1:8765/  (Ctrl-C to stop)
```

Open that URL in your browser and you'll see your suite collected as a
foldable tree. Leave the terminal running: it hosts the dashboard
until you stop it with `Ctrl-C`.

```{tip}
To scope the dashboard to part of your suite, pass a path:
`pytest --deck path/to/tests`. The project root is found the same way pytest
finds it, so the results match `pytest path/to/tests`.
```

## 3. Select what to run

What to run is decided in the browser, not on the command line. You can:

- **Check** individual tests, or a whole file or class to take all of them.
- **Click a marker chip** (say `slow` or `integration`) to grab every test
  carrying that marker.
- Filter with the **`-k`** and **`-m`** fields, combined with your selection.

## 4. Run and watch

Click **▶ Run**. Each test flips to **PASS**, **FAIL**, **ERROR**, or **SKIP** the
moment it finishes, with live rollups on every group. Click any result to see
its traceback (in pytest's own colors) alongside the captured output, with a
**copy** button to take the plain text with you.

The run is a real `python -m pytest` subprocess, so the outcome matches what
you'd get running the same selection in your terminal.

## 5. Edit and re-collect

Changed some tests? Click **↻ Collect**. pytest-deck re-collects and shows a
diff:
**added**, **removed**, and **changed** tests are flagged. Your selection and
prior results are kept for everything that still exists, so the edit-run loop
stays fast and you don't lose your place.

## Running without the pytest CLI

The dashboard also runs standalone, without going through `pytest`:

```console
$ pytest-deck path/to/tests
$ python -m pytest_deck.server path/to/tests   # the same thing, module form
```

Both accept `--host`, `--port`, and `--open`. One difference: the standalone
command roots the project at the path you give, while `pytest --deck
path/to/tests` finds the root the way pytest does. See
[Launching the dashboard](../user-guide/launching.md) for the full option table
and the details.

```{warning}
pytest-deck binds to localhost with no authentication and executes your test
code. Don't point `--host` at an untrusted network. See
[Limitations](../limitations.md).
```

## Next steps

- [Launching the dashboard](../user-guide/launching.md): every way to start it.
- [Using the dashboard](../user-guide/the-dashboard.md): a full tour of
  selection, running, and the detail view.
- [Coverage](../user-guide/coverage.md): turn on coverage and see it in the UI.
