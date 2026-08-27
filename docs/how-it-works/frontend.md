# Frontend

The dashboard is a Svelte 5 (runes) single-page app, built with Vite into
`pytest_deck/static/` and served by the FastAPI backend. It's a plain SPA, no
meta-framework. Reactivity is a handful of module-level rune stores, driven by
one connection to the server.

## One connection

The browser opens a single Server-Sent Events connection when the page loads and
keeps it for the life of the tab. Every result, every warning, and every
lifecycle event arrives on that one stream. Opening a run doesn't open a new
connection; the run posts its selection separately, and its results flow back on
the stream that's already there.

Because the stream is live-only and doesn't replay past events, a reconnection
after a dropped connection re-checks whether a run is still active by asking the
server, so a run whose ending was missed during the gap doesn't leave the UI
stuck.

## When versus what

The frontend keeps a clean split between the transport and the state it feeds.
One layer owns *when* something changes: it manages the connection, decides that
an event has arrived, and calls into the stores. The stores own *what* changes:
they hold the results, the selection, and the run status, and each event has one
place that applies it. Keeping these apart is what makes the streaming logic
testable and the state predictable.

## State lives in the browser

What to run is decided entirely in the browser, never on the server. You build a
selection three ways, and they combine:

- **Checkboxes** on individual tests or whole groups.
- **Marker chips**, which select every test carrying a marker. A chip is a
  selection shortcut: it ticks the matching checkboxes. It does not build a `-m`
  expression.
- The **`-k`** and **`-m`** fields, which pass straight through to pytest.

The server only hears about your selection when you press Run. Until then it's
all browser state.

## Reload and diff

Reloading is a frontend feature. The backend keeps no memory from one collect to
the next; asking it to collect always returns the current tree and nothing more.
The browser is what remembers the previous collection and computes the
difference.

When you re-collect, the browser compares the new set of test IDs against the old
one. IDs that appeared are flagged as added, IDs that vanished as removed, and an
ID present in both whose **set of markers** differs is flagged as changed. That
last one is worth being precise about: "changed" means the markers moved, not
that the test's code was edited. pytest-deck does not try to detect source edits.

Your selection and prior results are kept for every test that still exists, so an
edit-run-reload loop doesn't lose your place. Tests that genuinely went away, but
still had a result or a selection, are listed separately so nothing disappears
without a trace.

(folding-outcomes-live)=

## Folding outcomes live

As each test's per-phase reports stream in, the browser folds them into the one
outcome you see on its badge, using the same rules the server would. That shared
rule set is described under
[One outcome oracle, two implementations](outcome-oracle).

## Next steps

- [Design invariants](design-invariants.md): the decisions this frontend is
  built to respect.
- [Internals](../api/internals.md): the server-side modules behind the stream.
