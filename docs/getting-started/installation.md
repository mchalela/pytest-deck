# Installation

## Requirements

- **Python 3.11+** (3.11 through 3.14).
- **pytest 8.0+**. pytest-deck runs as a pytest plugin.

No Node.js or frontend tooling needed: the dashboard ships pre-built inside
the package. (Node is only for contributors building the frontend from source.
See [Development](../contributing/development.md).)

## Install from PyPI

```console
$ pip install pytest-deck
```

That's everything: the dashboard server comes along, and pytest-deck registers
itself as a pytest plugin.

Install it into the same environment as your project, so the dashboard collects
and runs against exactly the pytest, plugins, and packages your suite uses:

```console
$ python -m venv .venv
$ source .venv/bin/activate      # Windows: .venv\Scripts\activate
$ pip install pytest-deck
```

## Inert until you use it

The plugin does nothing until you pass `--deck`. Your normal runs are untouched:

```console
$ pytest                 # ordinary pytest; pytest-deck stays out of the way
$ pytest --deck          # launches the dashboard instead
```

## Verify

```console
$ pytest --help
```

You'll find a **pytest-deck interactive dashboard** group listing `--deck`. The
standalone `pytest-deck` command is on your `PATH` too.

## Next steps

Head to the [Quickstart](quickstart.md) to launch the dashboard and run your
first tests from the browser.
