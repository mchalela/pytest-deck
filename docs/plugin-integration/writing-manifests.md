# Writing your own manifest

You have a pytest plugin the deck doesn't curate, and you'd like it as a
switch in the sidebar. One TOML file in your repo does it. No code, no fork.

This page walks through building that file. For every key and its rules, see
the [manifest reference](manifest-reference.md).

## Where the file goes

Put manifests in this directory under your project root (the pytest rootdir):

```
<rootdir>/.pytest-deck/plugins/*.toml
```

One manifest per `.toml` file. Commit them alongside your tests; they're part
of how your suite is meant to be run.

The deck reads this directory when the dashboard page loads. A file you add
while the deck is running shows up after you refresh the browser tab.

## Start with a plain switch

The smallest useful manifest turns a plugin on and off, nothing more:

```toml
# .pytest-deck/plugins/my-plugin.toml
id = "my_plugin"
label = "My plugin"
dist = "my-pytest-plugin"
scope = "run"
```

That's the whole file. When the switch is on, the deck adds `-p my_plugin` to
the pytest argv for the run. Every manifest is a switch first: any config
fields you add later append their tokens after that `-p`.

(find-entry-point)=
## Find the entry-point name

`id` must be the plugin's `pytest11` entry-point name, the exact token `-p`
loads. That's often not the name you install: `pytest-json-report` (dist)
registers `pytest_jsonreport`, and `pytest-timeout` registers plain
`timeout`. To read the real names straight from your environment:

```bash
python -c "from importlib.metadata import entry_points; print([e.name for e in entry_points(group='pytest11')])"
```

That prints exactly the names `-p` resolves, for example
`['deck', 'pytest_cov', 'timeout', 'pytest_mpl']`. Find your plugin in the
list. Use that string as `id`, and put the install name in `dist`.

## Add config and a rendered report

Here's a real, installable plugin:
[`pytest-json-report`](https://pypi.org/project/pytest-json-report/), which
writes your run's results as a JSON document. This manifest adds a checkbox
and pipes the plugin's output into the Run info pane (what the detail pane
shows when no test is pinned):

```toml
# .pytest-deck/plugins/json-report.toml
id = "pytest_jsonreport"
label = "JSON report"
dist = "pytest-json-report"
scope = "run"
render = "json"

[transport]
type = "json_file"
arg = "--json-report-file={tmpdir}/report.json"
path = "{tmpdir}/report.json"

[[fields]]
key = "enable"
label = "Emit report"
type = "bool"
default = true
arg = "--json-report"
```

When you enable this switch and run, the deck compiles:

```
-p pytest_jsonreport --json-report --json-report-file=<run-tmpdir>/report.json
```

`{tmpdir}` resolves to a temporary directory scoped to the run, so the report
never lands in your working tree. After the child exits, the deck reads
`report.json` from there and shows its parsed contents as a foldable JSON tree
in the Run info pane.

```{tip}
`pytest-json-report` really does use the `--json-report` and
`--json-report-file` flags shown here. When you adapt this for another plugin,
check that plugin's own flags. The deck emits whatever argv tokens your
manifest declares, verbatim.
```

One rule saves the most debugging: each `arg` is a single argv token, so join
a flag and its value with `=` (`--reruns={value}`, never `--reruns {value}`).
The full compile rules are in the [reference](#manifest-fields).

## Know the limits

- Every key, type, and default: the
  [manifest reference](manifest-reference.md).
- What user manifests can't declare (reserved env vars, curated-only
  transports, first-party renders): the [trust model](#trust-model).
- Rendered payloads are capped at 256 KiB: the
  [render size cap](#render-size-cap).

## Verify it loaded

Launch the deck (or refresh the tab if it's already open) and look at the
sidebar. Your manifest shows up as a switch labeled with your `label`. If it
isn't there, check:

- The **plugin is installed** in the environment running the deck.
- The file is in **`<rootdir>/.pytest-deck/plugins/`** and ends in `.toml`.
- The manifest **validates**: a rejected file is skipped, and the deck's
  terminal prints a warning naming the file and the reason. Python prints
  each distinct warning once per process, so look for it the first time the
  page loads after you start the server (restart the server to see it again).

Fix the reason and refresh the browser tab. `↻ Collect` alone won't do it: the
deck rescans the manifest directory on page load, not on collect.

## Next steps

- [Manifest reference](manifest-reference.md): every key the validator
  accepts.
- [Example: the coverage manifest](coverage-example.md): the fullest curated
  manifest, annotated.
- [Plugin integration overview](overview.md): the model and the trust
  boundary.
