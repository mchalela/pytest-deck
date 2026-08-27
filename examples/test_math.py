import time

import pytest

def test_addition():
    print(f"Running test in {__file__}")
    assert 1 + 1 == 2


@pytest.mark.slow
def test_big_sum():
    time.sleep(3)
    assert sum(range(1000)) == 499500


@pytest.mark.parametrize("a,b,expected", [(2, 3, 5), (0, 0, 0), (-1, 1, 0)])
def test_parametrized_add(a, b, expected):
    time.sleep(1)
    assert a + b == expected


@pytest.mark.smoke
class TestMultiplication:

    def test_simple(self):
        assert 2 * 3 == 6

    @pytest.mark.slow
    def test_large(self):
        time.sleep(5)
        assert 1000 * 1000 == 1_000_000
