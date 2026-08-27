# Quality and CI

Every quality gate runs through [tox](https://tox.wiki), and CI runs the same
envs. So you can reproduce anything CI checks on your own machine with a single
`tox -e` command.

## The test matrix

The test suite runs across every supported Python and pytest combination:

```console
$ tox
```

That covers Python 3.11 through 3.14, each against pytest 8 and pytest 9, plus
the quality envs listed below. To run one combination, name it:

```console
$ tox -e py313-pytest8
```

Each env installs the package into an isolated build and runs the suite in
`tests/`.

## The quality envs

These are the same envs CI's quality job runs:

```console
$ tox -e style,docstyle,style-js,coverage,audit
```

| Env        | What it runs                                                    |
| ---------- | --------------------------------------------------------------- |
| `style`    | `flake8` with `flake8-black` and `flake8-isort` over `pytest_deck/` and `tests/`. |
| `docstyle` | `pydocstyle` over the package, PEP 257 convention.              |
| `style-js` | `prettier --check` and `eslint` over the frontend source.       |
| `coverage` | the suite under `coverage`, failing under **90%**.              |
| `audit`    | `npm audit` on the frontend lockfile, failing on high or critical advisories. |

The `coverage` env measures the spawned pytest subprocesses too, not just the
in-process code, then combines the data files before reporting. That's why the
floor covers the runner and the inner plugin as well.

## The frontend build

The dashboard bundle is built by its own env, which installs the Node
dependencies and runs the Vite build into `pytest_deck/static/`:

```console
$ tox -e frontend
```

CI builds this bundle first, because the Python tests serve the real assets, and
the packaged wheel ships whatever is in `static/`. If you're only changing Python
code, you still need a built bundle present for the server tests to pass. See
[Development](development.md) for building it directly with npm.

## The docs build

The documentation must build clean with warnings treated as errors, which is what
ReadTheDocs enforces:

```console
$ pip install -e ".[docs]"
$ sphinx-build -W --keep-going -b html docs docs/_build/html
```

The `-W` flag turns any warning into a failure, so a broken cross-reference or an
orphan page fails the build. A green build here matches what ReadTheDocs will do.

## What CI enforces

On every push and pull request, CI:

1. builds the frontend bundle once and hands it to the rest of the jobs,
2. runs the full Python matrix (3.11 to 3.14, pytest 8 and 9), and
3. runs the quality job: `style`, `docstyle`, `style-js`, `coverage`, and
   `audit`.

A change is green when every one of those passes. Since they're all tox envs, you
can run the exact same gates locally before you open a pull request.

Dependency currency is watched by Dependabot (`.github/dependabot.yml`): weekly
update PRs for the frontend packages, the Python dependencies, and the pinned
GitHub Actions.

## Next steps

- [Development](development.md): set up your environment and build the frontend.
