# Internals

These modules are the machinery behind the dashboard: the server and its
endpoints, the subprocess runner, collection, the event stream, and the small
helpers that keep the deck's behavior matching pytest's own. They're documented
here for contributors.

```{warning}
This is not a public API. These modules have no stability guarantee and can
change or move at any time. If you're building against pytest-deck, use the
[Public API](public-api.md) instead. For the design behind these pieces, see
[How It Works](../how-it-works/architecture.md).
```

The pages below are generated from the modules' own docstrings.

## `pytest_deck.server`

```{eval-rst}
.. automodule:: pytest_deck.server
   :members:
```

## `pytest_deck.runner`

```{eval-rst}
.. automodule:: pytest_deck.runner
   :members:
```

## `pytest_deck.collector`

```{eval-rst}
.. automodule:: pytest_deck.collector
   :members:
```

## `pytest_deck.events`

```{eval-rst}
.. automodule:: pytest_deck.events
   :members:
```

## `pytest_deck.reports`

```{eval-rst}
.. automodule:: pytest_deck.reports
   :members:
```

## `pytest_deck.tree`

```{eval-rst}
.. automodule:: pytest_deck.tree
   :members:
```

## `pytest_deck.import_paths`

```{eval-rst}
.. automodule:: pytest_deck.import_paths
   :members:
```

## `pytest_deck.rootdir`

```{eval-rst}
.. automodule:: pytest_deck.rootdir
   :members:
```
