# Development

A development setup needs **Python 3.11+** and **Node 24 LTS** (for building
the frontend; it is what CI uses, and any Node release Vite 8 supports works).
The project is developed against Python 3.13.

## Get the code and an environment

```console
$ git clone https://github.com/mchalela/pytest-deck
$ cd pytest-deck
```

Create a virtual environment and install the package in editable mode with its
test extra:

```console
$ python -m venv .venv
$ source .venv/bin/activate      # Windows: .venv\Scripts\activate
$ pip install -e ".[test]"
```

The `test` extra pulls in `httpx`, which the server tests use. For building the
documentation, install the `docs` extra as well (`pip install -e ".[docs]"`).

## Build the frontend

The dashboard is a Svelte app under `frontend/` that builds into
`pytest_deck/static/`, which the package ships and the server serves. That built
bundle is git-ignored, so a fresh checkout has no `static/` until you build it:

```console
$ cd frontend
$ npm ci
$ npm run build
```

`npm ci` installs the exact locked dependencies, the same way CI does. After
this, the Python server has a dashboard to serve, and you can run the test suite:

```console
$ pytest
```

## Repo layout

```text
pytest_deck/        the Python package (plugin, server, runner, collector, ...)
  manifests/        curated plugin manifests (coverage, benchmark, metadata, ...)
  static/           the built Svelte bundle (generated, git-ignored)
frontend/           the Svelte 5 + Vite dashboard source
tests/              the plugin's own test suite
docs/               this documentation (Sphinx + MyST)
```

The two halves are separate: the Python package is the plugin and server, and
`frontend/` is the browser app that compiles down into `pytest_deck/static/`.

## The frontend edit loop

For a fast rebuild while working on the dashboard, run Vite in watch mode:

```console
$ cd frontend
$ npm run dev
```

To reproduce the production bundle the package ships, run `npm run build`, which
writes to `pytest_deck/static/`. The `static/` directory is regenerated on every
build, so you never commit it by hand.

## Design references

Two documents live in the repository rather than on this site:

- [ARCHITECTURE.md](https://github.com/mchalela/pytest-deck/blob/main/docs/ARCHITECTURE.md):
  how the pieces fit together, and how data flows through them.
- [INVARIANTS.md](https://github.com/mchalela/pytest-deck/blob/main/docs/INVARIANTS.md):
  the decisions that must not be broken, and why each one is load-bearing.

Read them before changing the subprocess model, the event stream, or the plugin
manifests.

## Next steps

- [Quality and CI](quality-and-ci.md): the checks that gate a change, and how to
  run each one locally.
