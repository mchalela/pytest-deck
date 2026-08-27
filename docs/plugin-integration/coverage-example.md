# Example: the coverage manifest

Coverage (`pytest-cov`) is the deck's fullest curated manifest, so it makes
the best worked example of the model. This page walks through the real shipped
file section by section. To use coverage as a feature, see the
[coverage guide](../user-guide/coverage.md); to write your own manifest, see
[Writing your own manifest](writing-manifests.md).

Here is the manifest in full:

```toml
id = "pytest_cov"
label = "Coverage (pytest-cov)"
dist = "pytest-cov"
scope = "run"

flags = ["--cov", "--cov-*", "--no-cov", "--no-cov-on-fail"]

[env]
COVERAGE_FILE = "{tmpdir}/.coverage"

[transport]
type = "json_file"
arg = "--cov-report=json:{tmpdir}/cov.json"
path = "{tmpdir}/cov.json"

[[fields]]
key = "source"
label = "Source (--cov=)"
type = "string"
default = ""
arg = "--cov={value}"
arg_empty = "--cov"

[[fields]]
key = "branch"
label = "Branch coverage"
type = "bool"
default = false
arg = "--cov-branch"
```

## Identity

```toml
id = "pytest_cov"
label = "Coverage (pytest-cov)"
dist = "pytest-cov"
scope = "run"
```

`id` is the plugin's `pytest11` entry-point name, exactly the token `-p`
loads, so the switch compiles to `-p pytest_cov`. `dist` is the name you
`pip install`; it's display only and never part of the argv. `label` is the
switch text in the sidebar, and `scope = "run"` means coverage applies to
runs, not collection.

## Flags: pytest-cov's option namespace

```toml
flags = ["--cov", "--cov-*", "--no-cov", "--no-cov-on-fail"]
```

This declares pytest-cov's whole command-line surface. It compiles no tokens
of its own. Instead, it lets matching options from your ini `addopts` ride
along: the deck strips `addopts` from its subprocesses, and a self-contained
token in this namespace (say an ini `--cov-report=term-missing`) is added back
to the run automatically while the coverage switch is on. See
[Flags](#manifest-flags).

## Env: keeping `.coverage` out of your tree

```toml
[env]
COVERAGE_FILE = "{tmpdir}/.coverage"
```

pytest-cov writes a `.coverage` data file, and by default it lands in the
child's working directory, which is your project. Setting `COVERAGE_FILE`
redirects it into the run-scoped temp directory instead (`{tmpdir}` is
substituted at spawn). Enabling coverage never drops a stray file into your
working tree.

`COVERAGE_FILE` is a **reserved** env key that a user manifest can't set:
pytest-cov writes to whatever path it names, so an untrusted repo could aim it
at an arbitrary file to overwrite. The curated manifest is shipped code, so
it's trusted to set it, and pins it safely under the tmpdir. See the
[trust model](#trust-model).

## Transport: the JSON the deck reads back

```toml
[transport]
type = "json_file"
arg = "--cov-report=json:{tmpdir}/cov.json"
path = "{tmpdir}/cov.json"
```

`arg` is one more argv token, telling pytest-cov to write a JSON report into
the run tmpdir. `path` is where the deck reads it once the child exits. Both
use `{tmpdir}` and point at the same file. This is the same `json_file`
transport any user manifest can declare.

## Fields: the two config controls

```toml
[[fields]]
key = "source"
label = "Source (--cov=)"
type = "string"
default = ""
arg = "--cov={value}"
arg_empty = "--cov"
```

The `source` field is a text input for what to measure. Typing `web` compiles
`--cov=web`, a literal `{value}` substitution. Leaving it blank falls back to
`arg_empty`: a bare `--cov`, pytest-cov's measure-everything default.
`arg_empty` is why an empty field still does something useful.

```toml
[[fields]]
key = "branch"
label = "Branch coverage"
type = "bool"
default = false
arg = "--cov-branch"
```

The `branch` field is a checkbox. Ticked, it appends `--cov-branch`; unticked,
it emits nothing.

Put together, an enabled coverage switch with `web` in the source field and
branch on compiles to:

```
-p pytest_cov --cov=web --cov-branch --cov-report=json:<run-tmpdir>/cov.json
```

## Why coverage is curated, not a user manifest

Everything above (the switch, the config fields, the `json_file` transport) a
user manifest could declare. What it can't declare is how coverage renders,
and that is why coverage ships curated:

- **A first-party slimmer.** A slimmer is a deck-internal step that extracts a
  summary from a plugin's output before it reaches the browser. The raw
  `cov.json` is bulky per-line data, far more than a generic
  `render = "json"` tree should ship, so the deck slims it down first. Generic
  render is for a plugin's summary output, not a full data dump; see the
  [render size cap](#render-size-cap).
- **First-party render surfaces.** The total percentage, the per-file source
  panel, and the per-line hit/miss gutter are deck-internal renderings wired
  to the coverage manifest specifically. User manifests get `json` and `text`,
  not custom panels.

So the coverage manifest is the concrete counterpart to the
[overview's boundary](#user-curated-boundary): its control and transport are
ordinary, but its rendering is first-party, which is what a curated manifest
is for.

## Next steps

- [Manifest reference](manifest-reference.md): every key used here, in one
  place.
- [Writing your own manifest](writing-manifests.md): the same model, applied
  to a plugin the deck doesn't curate.
- [Coverage in the user guide](../user-guide/coverage.md): using the feature
  day to day.
