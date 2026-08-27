# Launching the dashboard

There are two ways to start the dashboard: through the `pytest` command you
already use, or with pytest-deck's own command. Both serve the same dashboard,
but they pick the project root differently (see [Standalone](#standalone)).

## Through pytest

```console
$ pytest --deck
```

This collects and serves the current project, using the same rootdir a bare
`pytest` would pick. The dashboard takes over before any tests run, so nothing
runs in this process. The browser drives everything from here.

To limit the dashboard to part of your suite, pass a path:

```console
$ pytest --deck tests/api
```

`pytest --deck tests/api` roots the project exactly where `pytest tests/api`
would. It walks up to your config file (`pyproject.toml`, `pytest.ini`,
`tox.ini`, `setup.cfg`, or `setup.py`) to find the root, and the initial tree
stays scoped to the path you gave. So the collected tree matches what
`pytest tests/api` would collect.

```{note}
The path must be a directory that exists. If you point `--deck` at a missing
path or a file, pytest exits with a usage error before the server starts,
instead of failing later inside the dashboard.
```

`--deck-port` pins the server to a port:

```console
$ pytest --deck --deck-port 9000
```

A pinned port must be free, or the launch fails with a one-line error (see
[Troubleshooting](troubleshooting.md)). Without `--deck-port` the server starts
on `8765`. If that port is taken, it moves to the next free one, up to `8785`,
and tells you:

```text
pytest-deck serving /path/to/your/project
  port 8765 in use → serving on 8766
  → open http://127.0.0.1:8766/  (Ctrl-C to stop)
```

The printed URL always shows the actual port.

## Standalone

You don't need the pytest CLI. The dashboard ships with its own command:

```console
$ pytest-deck tests/api
$ python -m pytest_deck.server tests/api   # the same thing, module form
```

The path is optional and defaults to the current directory.

Unlike `pytest --deck PATH`, the standalone command roots the project at the
path you give. It does not walk up to find the root. `pytest-deck tests/api`
makes `tests/api` the rootdir: test IDs read `test_users.py::test_login` rather
than `tests/api/test_users.py::test_login`, and pytest's working directory and
import path start there. A config file in a parent directory is still found. To
match the root `pytest --deck` would pick, run `pytest-deck` with no path from
the directory that holds your config.

| Option   | Default     | Description                                        |
| -------- | ----------- | -------------------------------------------------- |
| `--host` | `127.0.0.1` | Host interface to bind.                            |
| `--port` | *(auto)*    | Port to serve on; must be free. Default: first free port from `8765`, up to `8785`. |
| `--open` | *(off)*     | Open a browser tab on launch.                      |

```console
$ pytest-deck --port 9000 --open tests/api
```

`--open` is off by default, so a restart doesn't spawn a second tab. The URL is
printed once, and any tab you already have open reconnects on its own.

## What you'll see

On launch, pytest-deck prints where it's serving and the URL to open:

```text
pytest-deck serving /path/to/your/project
  → open http://127.0.0.1:8765/  (Ctrl-C to stop)
```

Open that URL and your suite appears as a foldable tree, ready to select and
run. Leave the terminal running: it hosts the dashboard until you stop it with
`Ctrl-C`.

```{warning}
pytest-deck binds to localhost with no authentication, and it executes your test
code. Don't point `--host` at an untrusted network. See
[Limitations](../limitations.md).
```

## Next steps

- [Using the dashboard](the-dashboard.md): select tests, run them, and read the
  results.
- [Reload and diff](reload-and-diff.md): re-collect after an edit without losing
  your place.
