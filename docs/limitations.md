# Limitations

The honest list of what pytest-deck doesn't do today. Everything here describes
the current release, not a temporary bug.

## Network and security

**The dashboard is a single-user, localhost tool.** The server binds to
`127.0.0.1` by default, has **no authentication**, and executes your test code
on request. Don't point `--host` at an untrusted network.

## Running tests

**pytest-xdist is not supported.** Every deck run forces `-p no:xdist`, so runs
are serial even if xdist is installed and configured in your project. The deck
reads results over a private pipe into its subprocess; xdist's workers wouldn't
inherit it, so results would never reach the dashboard.

**Plugin autoloading is disabled inside deck runs.** Installed plugins don't
load on their own; they load through the sidebar switches (or the extra args
field). If a run behaves differently from your terminal, a plugin switch you
haven't turned on is the first thing to check. See
[Plugins](user-guide/plugins.md).

**Your ini `addopts` are set aside, then surfaced.** With autoloading off, a
plugin flag like `--cov` in `addopts` would fail every run, so the deck
neutralizes the line. Every token comes back through the UI instead: it
prefills a switch's config form, follows its plugin's switch, or appears as a
clickable suggestion chip. Nothing is dropped silently, but `addopts` never
applies by itself. See
[Extra args and your ini addopts](#extra-args).

## Scope

**One project per dashboard.** A deck serves a single project root. To work on
two projects, start two decks; each takes its own port. Without an explicit
port, the server starts on `8765` and falls forward to the next free port, up
to `8785`, announcing the one it picked. A pinned port must be free or the
launch fails.

**Rich panels exist only for the plugins pytest-deck knows about.** Seven
switches ship ready-made (pytest-cov, pytest-mpl, pytest-benchmark,
pytest-metadata, pytest-mock, pytest-asyncio, pytest-django). Your own
manifests (one-file TOML descriptions in `.pytest-deck/plugins/`) can add a
switch for any other plugin, but their output renders only through the generic
JSON and text panels. The coverage gutter, benchmark column, and attachments
pane are reserved for the switches that ship with pytest-deck. See
[what your own manifests can and can't render](#user-curated-boundary).

## Docs and stability

**No screenshots yet.** The documentation describes the UI in prose for now.

**Pre-1.0.** The [public API](api/public-api.md) is small on purpose, and even
it may change between releases. The UI may too.

## Next steps

- [Troubleshooting](user-guide/troubleshooting.md): symptoms and fixes for the
  things that can go wrong within these limits.
- [Roadmap](roadmap.md): where the project is headed.
