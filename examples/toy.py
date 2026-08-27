"""A small toy module whose calls chain a few frames deep.

Used by test_deep_traceback.py so that failing tests produce a multi-frame
traceback worth looking at in the detail pane.
"""


def normalize(values):
    """Scale a list of numbers so they sum to 1.0."""
    total = _sum_strict(values)
    return [v / total for v in values]


def _sum_strict(values):
    """Sum, but reject empty input (this is where the deep failure happens)."""
    if not values:
        raise ValueError("cannot normalize an empty sequence")
    return _accumulate(values)


def _accumulate(values):
    running = 0
    for v in values:
        running = _add(running, v)
    return running


def _add(a, b):
    # Deliberately strict: rejects non-numbers so a bad element fails deep.
    if not isinstance(b, (int, float)):
        raise TypeError(f"expected a number, got {type(b).__name__}: {b!r}")
    return a + b


def average(values):
    """Mean of a sequence — chains through the same helpers."""
    return _sum_strict(values) / len(values)
