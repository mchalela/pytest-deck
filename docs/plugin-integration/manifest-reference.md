# Manifest reference

Every key a manifest file can contain, with types, defaults, and an example
for each. For a guided walkthrough, start with
[Writing your own manifest](writing-manifests.md); for the model behind it,
see the [overview](overview.md).

Validation is strict: an unknown key, a missing required key, or a wrong type
rejects the manifest. A rejected user manifest is skipped with a console
warning naming the file and the reason, and the rest still load. A manifest
file that resolves outside the project root (a symlink into another
directory) is skipped the same way: the deck only reads files that really
live under the rootdir.

## Placeholders

Two placeholders are substituted by literal text replacement (never
`str.format`, so braces in a typed value stay inert):

- `{value}`: in a field's `arg`, replaced with what you typed in the UI.
- `{tmpdir}`: in `[env]` values and in every argv token a manifest compiles
  (a field's `arg`, the `[transport]` `arg` and `path`), replaced with the
  run-scoped temporary directory. Files written there never touch your
  working tree.

## Top-level keys

**`id`**: string, required, non-empty. The plugin's `pytest11` entry-point name, the
exact token `-p` loads. Often not the install name (`pytest-json-report`
registers `pytest_jsonreport`); see
[finding the entry-point name](#find-entry-point).
Example: `id = "pytest_cov"`.

**`label`**: string, required, non-empty. The switch text shown in the
sidebar. Include the dist name when the short label alone wouldn't identify
the plugin.
Example: `label = "Coverage (pytest-cov)"`.

**`dist`**: string, required, non-empty. The distribution name you
`pip install`. Display only; it never reaches the argv.
Example: `dist = "pytest-cov"`.

**`scope`**: string, required. `run`, `collect`, or `both`. Says when the
plugin's `-p` token applies; see [Scope values](#manifest-scope).
Example: `scope = "run"`.

**`fields`**: array of tables, optional. Typed config controls shown under
the switch; see [Config fields](#manifest-fields).

**`flags`**: array of strings, optional. The plugin's flag namespace, which
lets matching ini `addopts` tokens ride along; see [Flags](#manifest-flags).
Example: `flags = ["--cov", "--cov-*"]`.

**`env`**: table, optional. Environment variables set on the run subprocess;
see [Env](#manifest-env).

**`transport`**: table, optional. A post-run file the deck reads back; see
[Transport](#manifest-transport).

**`render`**: string, optional. `json` or `text`: how the transport payload
is displayed. `json` parses the file into a foldable tree; `text` shows the
raw contents preformatted.
Example: `render = "json"`.

Two rules go with it. **A user manifest with a `[transport]` must set
`render`.** And `render` without a `[transport]` is an error, since there is
nothing to read. Curated manifests may omit it when a first-party pipeline
displays their data; see the [trust model](#trust-model).

**`disabled_reason`**: string, optional. If set, the switch appears greyed
out and can't be enabled. Useful for parking a manifest you don't want active
yet without deleting the file.
Example: `disabled_reason = "waiting on plugin 2.0"`.

(manifest-scope)=
## Scope values

- `"run"`: the plugin loads for runs while the switch is on. Collection is
  untouched. The right choice for most plugins.
- `"collect"` or `"both"` (the two are equivalent): the `-p <id>` token also
  rides every collect and reload, so the tree matches what the plugin would
  collect in your terminal. Pick this when the plugin changes collection
  itself, like pytest-asyncio, whose absence can break collecting async
  suites. Toggling such a switch re-collects the tree.

Only the bare `-p <id>` ever rides collection. Fields, `[transport]` tokens,
and `[env]` apply to runs only; a plugin's output flag on a collect pass would
truncate the very file the next run is about to write. There is no
collect-without-run scope: every run re-collects the test IDs it was given, so
a plugin the tree needed is a plugin the run needs too.

(manifest-fields)=
## Config fields (`[[fields]]`)

Each `[[fields]]` table is one control in the panel and one argv template.

**`key`**: string, required. The field's identifier, unique within the
manifest.
Example: `key = "source"`.

**`label`**: string, required. The field's label in the UI.
Example: `label = "Source (--cov=)"`.

**`type`**: string, required. `string` (a text input) or `bool` (a checkbox).
There is no numeric type: a value that's really a number (a timeout, a count,
a threshold) is a `string` field, and what you type passes through verbatim.
Example: `type = "string"`.

**`default`**: matches `type`, required. The field's starting value: a string
for `string` fields, `true` or `false` for `bool`.
Example: `default = false`.

**`arg`**: string, required. The argv token emitted when the field is on. A
`string` field's `arg` must contain `{value}`.
Example: `arg = "--cov={value}"`.

**`arg_empty`**: string, optional, `string` fields only. The token used when
the field is left blank; without it, a blank field emits nothing. Use it when
the bare flag means something on its own.
Example: `arg_empty = "--cov"` (pytest-cov's measure-everything default).

### How `arg` compiles

- A `bool` field emits its `arg` when checked, and nothing when unchecked.
- A `string` field replaces `{value}` with what you typed:
  `arg = "--timeout={value}"` with `60` typed becomes `--timeout=60`. A blank
  or whitespace-only value falls back to `arg_empty`, or emits nothing.
- **Each `arg` and `arg_empty` is a single argv token.** Join a flag and its
  value with `=`, as in `arg = "--reruns={value}"`. A plugin's README may show
  the space-separated form (`--reruns {value}`); don't copy it. That compiles
  to one token pytest can't parse.

(manifest-flags)=
## Flags (`flags`)

The plugin's flag namespace: the command-line options that belong to it.

```toml
flags = ["--cov", "--cov-*", "--no-cov"]
```

Entries are literal tokens (`--cov`, which also covers `--cov=pkg`) or
trailing-`*` prefixes (`--cov-*`, which covers `--cov-report=xml`). Every
entry must start with `-`, `*` may appear only at the end, and a wildcard
needs a non-dash prefix. A bare `*` or `--*` that claimed the whole option
space is rejected.

What the namespace grants: the deck strips your ini `addopts` from its
subprocesses (see [the plugins guide](#extra-args)). A self-contained token
(`--flag` or `--flag=value`, never a space-separated value or a positional)
that matches an enabled manifest's namespace is added back to the run
automatically. Declare `flags` so your switch keeps a user's existing
`addopts` configuration working while it's on.

A few deck-integrity options are never re-admitted, no matter whose namespace
claims them: `-p`, `-o` / `--override-ini`, `-c`, `--rootdir`, and
`--import-mode`. They control which plugins load and where the child looks
for config, and that is the deck's job.

(manifest-transport)=
## Transport (`[transport]`)

One file the plugin writes during the run and the deck reads after the child
exits. A user manifest's transport must be paired with `render = "json"` or
`render = "text"`.

**`type`**: string, required. `json_file` or `text_file` in a user manifest.
Two more types, `artifact_dir` and `fd3`, are curated only; see
[below](#curated-only-transports).
Example: `type = "json_file"`.

**`arg`**: string or array of strings, required. Extra argv tokens telling
the plugin where to write, usually with `{tmpdir}`. Use an array when the
plugin needs more than one flag; each element is one token, and the array
can't be empty.
Example: `arg = "--json-report-file={tmpdir}/report.json"`.

**`path`**: string, required. Where the deck reads the file afterward, with
`{tmpdir}`. Point `arg` and `path` at the same file.
Example: `path = "{tmpdir}/report.json"`.

If the transport file is missing, unreadable, or malformed, the deck shows
nothing for that plugin. The run itself is unaffected.

(curated-only-transports)=
### Curated-only transports

- `artifact_dir`: a run-scoped directory of files, plus an index the deck
  parses (keys `root`, `index`, `index_format`). It backs
  `render = "artifacts"` and the per-test **Attachments** pane (pytest-mpl).
  **A user manifest that declares it is rejected**: its root becomes a
  directory the deck serves over HTTP.
- `fd3`: no file at all. The payload arrives on the deck's own
  structured-results channel (pytest-metadata). **Rejected in a user
  manifest**: only first-party records ride that channel.

See the [trust model](#trust-model) for the reasoning behind both.

(render-size-cap)=
### Render size cap

`render = "json"` and `render = "text"` are for a plugin's summary output,
not a full data dump. A rendered payload is capped at **256 KiB**:

- An over-cap `text` file is shown truncated, and flagged as truncated.
- An over-cap `json` file is not parsed at all. The Run info pane reports its
  true size and its top-level key names instead, so you can see which field
  is bloating it:

  ```text
  payload too large (9.2 MB): exceeds the 256 KiB render cap, not rendered.
  top-level keys: summary, results, raw_samples
  ```

Some plugins embed bulk per-line or per-sample records in their JSON, which
blows the cap even for a tiny suite. A generic `json` render cannot show
these. They need a curated manifest with a first-party slimmer, a
deck-internal step that extracts the summary before it reaches the browser.
pytest-cov ships curated for exactly this reason. If your plugin embeds bulk
data and you want it rendered, file an issue asking for a curated manifest.

(manifest-env)=
## Env (`[env]`)

Environment variables set on the run subprocess. Values are strings and may
use `{tmpdir}`:

```toml
[env]
MY_PLUGIN_CACHE = "{tmpdir}/cache"
```

**Reserved names.** A user manifest may not set any of these. Doing so
rejects the whole manifest, with the reason printed:

```
COLUMNS            LINES
PYTHONPATH         PYTHONDONTWRITEBYTECODE     PYTHONUNBUFFERED
PYTEST_DECK_FD     PYTEST_DISABLE_PLUGIN_AUTOLOAD
PYTEST_ADDOPTS     PYTEST_PLUGINS
COVERAGE_FILE
```

Most of these keep the deck's subprocess model working: the results channel,
the plugin-loading discipline, the import path. `COVERAGE_FILE` is reserved
because pytest-cov writes a data file to whatever path it names, which would
let a repo's manifest overwrite an arbitrary file. See the
[trust model](#trust-model). (The curated coverage manifest sets it itself,
pinned safely under the run tmpdir.)

## Next steps

- [Writing your own manifest](writing-manifests.md): the walkthrough.
- [Example: the coverage manifest](coverage-example.md): most of these keys in
  one real file.
- [Trust model](#trust-model): what user manifests can't declare, and why.
