"""pytest-mpl figures — exercises the deck's attachments pane.

Enable the "Matplotlib figures (pytest-mpl)" switch in the left bar, run, and
click a test: the detail pane's Attachments section shows the figure's
baseline / result / diff images. Needs ``pip install pytest-mpl``; the switch
is hidden until it's installed.

``test_drifted_sine`` is committed with a baseline it deliberately does NOT
match (the baseline is a cosine), so it FAILS on every run and pytest-mpl writes
result + diff images — the three-way compare is the point of the demo. The other
tests match their baselines and pass. pytest-mpl finds ``baseline/`` beside this
file automatically (no --mpl-baseline-path needed).

Regenerate the passing baselines after an intentional change (do NOT regenerate
test_drifted_sine — its mismatch is intentional):
    pytest examples/mpl --mpl-generate-path=examples/mpl/baseline
"""

import matplotlib

matplotlib.use("Agg")  # headless: no display needed under the deck subprocess

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

_X = np.linspace(0, 2 * np.pi, 200)


@pytest.mark.mpl_image_compare
def test_sine_wave():
    fig, ax = plt.subplots()
    ax.plot(_X, np.sin(_X), color="tab:blue", lw=2)
    ax.set_title("sine")
    return fig


@pytest.mark.mpl_image_compare
def test_scatter():
    fig, ax = plt.subplots()
    rng = np.random.default_rng(0)  # seeded, so deterministic: always passes
    ax.scatter(rng.random(50), rng.random(50), c="tab:green")
    ax.set_title("scatter")
    return fig


@pytest.mark.mpl_image_compare
@pytest.mark.parametrize("color", ["red", "orange"])
def test_bars(color):
    # Parametrized: two nodeids share one test function, which exercises the
    # deck's nodeid-to-mpl-dotted-name join on the ``[param]`` suffix.
    fig, ax = plt.subplots()
    ax.bar(["a", "b", "c"], [3, 1, 2], color=color)
    ax.set_title(f"bars-{color}")
    return fig


@pytest.mark.mpl_image_compare
def test_drifted_sine():
    # Renders a sine, but the committed baseline is a cosine, so this always
    # fails and mpl writes result and diff images. The intentional-mismatch demo.
    fig, ax = plt.subplots()
    ax.plot(_X, np.sin(_X), color="tab:red", lw=2)
    ax.set_title("drifted")
    return fig
