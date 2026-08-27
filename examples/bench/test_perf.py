"""pytest-benchmark timings — exercises the deck's benchmark column.

Enable the "Benchmarks (pytest-benchmark)" switch in the left bar, run this
file, and the tree grows a mean-time badge per benchmarked test; click one for
the full stats (min / max / stddev / rounds) in the detail pane, and the run
summary line counts the benchmarks. Needs ``pip install pytest-benchmark``;
the switch is hidden until it's installed.

The tests deliberately span magnitudes — sub-µs arithmetic, ~µs string work,
~ms number crunching — so the badge's per-test unit auto-scaling (ns / µs / ms)
is visible in a single screen. ``test_fib`` is parametrized: one function,
three separately-timed nodeids.

The switch is also structural: without it (the deck runs with plugin autoload
disabled) these ERROR with "fixture 'benchmark' not found". Try the switch's
"Disable timing" field too — the suite still passes, but no timings are saved,
so the deck reports the run as having no benchmark data.
"""

import pytest

_WORDS = [str(i) for i in range(1_000)]


def multiply():
    return 3 * 7


def join_words():
    return "-".join(_WORDS)


def crunch():
    return sum(i * i for i in range(200_000))


def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def test_multiply(benchmark):
    # Sub-µs: a single multiplication, so the badge should read in ns.
    assert benchmark(multiply) == 21


def test_join_words(benchmark):
    # ~µs: joining 1000 strings.
    assert len(benchmark(join_words)) == 3_889


def test_crunch(benchmark):
    # ~ms: summing 200k squares in a generator loop.
    assert benchmark(crunch) == 2_666_646_666_700_000


@pytest.mark.parametrize("n", [10, 100, 1000])
def test_fib(benchmark, n):
    # Parametrized: three timed nodeids from one function, growing with n.
    result = benchmark(fib, n)
    assert result == fib(n)
