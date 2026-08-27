# Public API

pytest-deck is mainly an application, not a library, so the surface you'd import
or build against is deliberately small. Two modules make it up, plus the command
line:

- The **`--deck` CLI option** and the **`pytest-deck`** console script (see
  [Launching the dashboard](../user-guide/launching.md)).
- {py:mod}`pytest_deck.manifests`, the model that describes how a pytest plugin
  plugs into the dashboard.
- {py:mod}`pytest_deck.outcome`, the single source of truth for folding a test's
  phase reports into one outcome.

```{note}
pytest-deck is pre-1.0, so even this surface may change between releases. The
modules below are the parts most likely to stay stable, and the ones a plugin
manifest author or an integration would touch. Everything else lives in
[Internals](internals.md).
```

The pages below are generated from the modules' own docstrings.

## `pytest_deck.manifests`

```{eval-rst}
.. automodule:: pytest_deck.manifests
   :members:
```

## `pytest_deck.outcome`

```{eval-rst}
.. automodule:: pytest_deck.outcome
   :members:
```
