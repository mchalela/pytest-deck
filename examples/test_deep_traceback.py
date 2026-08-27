"""Tests that fail several frames deep, to produce larger tracebacks.

Each failure travels through toy.py's chained helpers
(normalize/average → _sum_strict → _accumulate → _add), so the detail pane
shows a multi-frame traceback rather than a one-liner.
"""

import pytest

from toy import average, normalize


def test_normalize_empty_raises_deep():
    # Fails inside _sum_strict, two calls down from the test via normalize().
    normalize([])


def test_normalize_bad_element_deep():
    # Fails inside _add, four calls down from the test: a str sneaks into the numbers.
    normalize([1.0, 2.0, "oops", 4.0])


def test_average_wrong_result():
    # Runs the full chain successfully, then the assertion itself fails: a plain
    # one-frame assert failure, for contrast with the deep ones above.
    result = average([2, 4, 6, 8])
    assert result == 99


@pytest.mark.parametrize("data", [[10, 20, "x"], [], [1, None, 3]])
def test_normalize_various_bad_inputs(data):
    # Three parametrized failures: two crash deep in _add, one in _sum_strict.
    normalize(data)
