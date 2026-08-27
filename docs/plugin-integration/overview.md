# Plugin integration overview

A manifest is a small TOML file that teaches pytest-deck how to drive one
pytest plugin. Each manifest becomes a switch in the dashboard's sidebar.
Turn the switch on, and the deck adds that plugin's arguments to the next run.

Here is a complete manifest:

```toml
id = "randomly"                # the entry-point name -p loads
label = "Shuffle (pytest-randomly)"
dist = "pytest-randomly"       # the name you pip install (display only)
scope = "run"
```

Four lines is enough. With this file in place, the switch appears in the
sidebar, and turning it on adds `-p randomly` to the run.

Manifests come from two places, both loaded and validated by the same parser:

- **Curated** manifests ship inside pytest-deck
  (`pytest_deck/manifests/*.toml`). Seven ship today: `pytest-cov`,
  `pytest-mpl`, `pytest-benchmark`, `pytest-metadata`, `pytest-mock`,
  `pytest-asyncio`, and `pytest-django`. The
  [plugins guide](../user-guide/plugins.md) covers what each one does.
- **User** manifests live in your repo at
  `<rootdir>/.pytest-deck/plugins/*.toml`. Drop one in and refresh the browser
  tab; it appears in the sidebar. See
  [Writing your own manifest](writing-manifests.md).

A manifest only becomes a switch if its plugin is actually installed. A switch
for a missing plugin would fail the run, so the panel never offers one.

## The three facets

Every manifest describes a plugin along three axes: how you control it, how
its output comes back, and how that output is shown.

### Control: the switch and its config

The identity and configuration half: `id`, `label`, `dist`, `scope`, an
optional `[env]` table, and typed `[[fields]]` that render as text inputs or
checkboxes. For example, typing `web` into coverage's Source field compiles
the token `--cov=web`. Fields compile purely to a token list, never a shell
string.

A manifest can also declare a `flags` namespace, the command-line options that
belong to its plugin. Matching tokens from your ini `addopts` then ride along
whenever the switch is on. See [Flags](#manifest-flags).

### Transport: how output comes back

- `json_file` / `text_file`: a file the plugin writes during the run and the
  deck reads once the child exits. Coverage uses this:
  `--cov-report=json:{tmpdir}/cov.json`. Available to any manifest.
- `artifact_dir`: a run-scoped directory of files the deck serves over HTTP,
  like pytest-mpl's baseline and diff images. Curated only.
- `fd3`: no file at all. The payload rides the deck's own structured-results
  channel, which is how pytest-metadata's in-memory data gets out. Curated
  only.

### Render: which panel lights up

- `json` / `text`: generic surfaces available to any manifest. A JSON file
  shows as a foldable tree, a text file as plain preformatted text. This is
  how a user manifest for, say, `pytest-json-report` gets its report on
  screen.
- First-party renderings: richer surfaces wired to specific curated plugins.
  Coverage's total percentage, per-file source panel, and per-line hit/miss
  gutter; benchmark's mean-time tree column and per-test stats table;
  metadata's **Environment** section; pytest-mpl's per-test **Attachments**
  pane (`render = "artifacts"`).

(user-curated-boundary)=
## The user / curated boundary

A user manifest gets the switch, config fields, a `flags` namespace, and the
generic `json` / `text` renders. Every first-party pipeline ships with the
deck, end to end: the slimmers (deck-internal steps that shrink bulky output
to a summary), the `fd3` transport, `artifact_dir`, and the custom panels.

A user manifest whose `id` matches a curated one replaces it. Your file
becomes the whole definition for that plugin: the curated fields, `[env]`,
transport, and rich panel are gone. Whatever output you declare renders only
through the generic `json` or `text` surfaces.

In practice: **a user manifest with a `[transport]` must declare
`render = "json"` or `render = "text"`.** One that omits the render, or
reaches for a first-party surface, is rejected at validation with the reason
printed.

Part of this is practical. Some plugins embed bulk per-line or per-sample data
in their output, and a generic `json` tree can't usefully show that. Those
plugins need a curated manifest with a slimmer. See the
[render size cap](#render-size-cap).

(trust-model)=
## Trust model

Curated manifests are code the project ships, so the deck trusts them. A user
manifest is TOML read from whatever repo is checked out, so the deck treats it
as untrusted input. Argv is yours: a user manifest may compile any pytest
tokens it likes, because you already run your own test code. The hard limits
sit where a manifest could reach beyond the test run:

- **User manifests cannot set reserved environment variables.** The `[env]`
  table is applied to the run subprocess, and a few names would break the
  deck's own machinery or redirect writes. The sharpest case is
  `COVERAGE_FILE`: pytest-cov writes a data file to whatever path it names, so
  a hostile repo could aim it at a file like `~/.bashrc` and overwrite it. The
  full list is in the [reference](#manifest-env). Setting any of them rejects
  the whole manifest, with the reason printed.
- **User manifests cannot declare the `artifact_dir` transport.** Its root
  becomes a directory the deck serves over HTTP, which would let a manifest
  expose arbitrary files as URLs.
- **User manifests cannot use the deck's results channel** (the `fd3`
  transport). Only the deck's own first-party records ride that channel.
- **User manifests cannot borrow first-party renders**, even by reusing a
  curated `id` like `pytest_cov`. Those panels make a claim ("this is your
  coverage"), and the deck won't present a user-declared file as first-party
  data.

A user manifest that fails validation is skipped with a console warning naming
the file and the reason. The rest still load.

## Next steps

- [Writing your own manifest](writing-manifests.md): the walkthrough.
- [Manifest reference](manifest-reference.md): every key, with types and
  examples.
- [Example: the coverage manifest](coverage-example.md): the fullest curated
  manifest, annotated end to end.
