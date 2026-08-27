"""Tests for selection: `-k` / `-m` composition and the blank-omit rule.

A run can carry `-k`/`-m` expressions alongside the positional node IDs.
The load-bearing pieces in ``runner.py``:

* ``_trim_expr`` — strips the expression; an empty-after-strip value becomes
  ``None`` (treated as "no filter").
* ``_Run.__init__`` stores the **trimmed** ``k``/``m`` (these are echoed on
  ``started``).
* ``_Run._argv`` appends ``-k``/``-m`` **only when non-blank** — the safeguard for
  the whitespace-``-m`` gotcha (a blank ``-m`` would deselect everything → pytest
  exit 5).
* ``_wait`` maps exit codes: 4 (+ no reports) → ``error``; 5 (no match) is benign
  → ``finished``.

Selection composes as AND: positional node IDs define the collection set, then
``-k``/``-m`` deselect within it.

Async is driven with ``run_async`` (``pytest-asyncio`` is not installed). The
argv/trim assertions are unit-level on ``_Run``; the rest drive a real pytest
subprocess via ``RunManager`` against a tmp fixture suite (markers registered to
keep the run warning-clean).
"""

import asyncio

import pytest

from pytest_deck.runner import RunManager, _Run, _trim_expr


def run_async(coro):
    return asyncio.run(coro)


async def collect_until(sub, terminal=("finished", "error"), timeout=60.0):
    """Drain a subscriber until a terminal event, returning all (name, data)."""
    events = []
    while True:
        ev = await asyncio.wait_for(sub.get(), timeout=timeout)
        if ev is None:
            break
        events.append((ev.name, ev.data))
        if ev.name in terminal:
            break
    return events


def call_nodeids(events):
    """Node IDs that produced a `call` report (i.e. actually ran a body)."""
    return sorted(
        d["nodeid"] for n, d in events if n == "report" and d["when"] == "call"
    )


# --- fixture suite --------------------------------------------------------


@pytest.fixture
def suite(tmp_path):
    """A marked, named suite for `-k`/`-m` selection assertions.

    Markers are registered in ``pytest.ini`` so a `-m` run doesn't emit
    PytestUnknownMarkWarning noise on fd-3.
    """
    (tmp_path / "test_sel.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.slow\n"
        "def test_alpha():\n"
        "    assert True\n"
        "\n"
        "def test_beta():\n"
        "    assert True\n"
        "\n"
        "@pytest.mark.smoke\n"
        "def test_gamma():\n"
        "    assert True\n"
        "\n"
        "def test_delta():\n"
        "    assert True\n"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    slow: slow tests\n    smoke: smoke tests\n"
    )
    return tmp_path


async def _join(mgr):
    if mgr._run is not None:
        await mgr._run.join()


# === 1. composition = AND =================================================


def test_k_filters_within_selected_nodeids(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        # Three node IDs ticked; -k narrows to the subset whose name has "alpha".
        await mgr.start(
            [
                "test_sel.py::test_alpha",
                "test_sel.py::test_beta",
                "test_sel.py::test_gamma",
            ],
            k="alpha",
        )
        events = await collect_until(sub)
        await _join(mgr)

        # Only the -k subset ran, even though three node IDs were selected.
        assert call_nodeids(events) == ["test_sel.py::test_alpha"]
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0

    run_async(body())


def test_m_filters_within_selected_nodeids(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        # Two ticked node IDs; only test_alpha carries @pytest.mark.slow.
        await mgr.start(
            ["test_sel.py::test_alpha", "test_sel.py::test_beta"],
            m="slow",
        )
        events = await collect_until(sub)
        await _join(mgr)

        assert call_nodeids(events) == ["test_sel.py::test_alpha"]
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0

    run_async(body())


# === 2. blank-omit rule (load-bearing) ====================================


def _selection_argv(run):
    """The selection tail of argv, after the base `python -m pytest ...` prefix.

    The base argv legitimately contains ``-m pytest`` (the module invocation), so
    we only inspect the part after ``--color=yes`` where `-k`/`-m` selection flags
    are appended — otherwise we'd match the wrong ``-m``.
    """
    argv = run._argv()
    return argv[argv.index("--color=yes") + 1 :]


def test_argv_omits_blank_and_whitespace_expressions(suite):
    """Unit-level: blank/whitespace `-k`/`-m` must NOT appear in argv at all."""
    for blank in ("", "   ", "\t", "\n", None):
        run = _Run("run-1", RunManager(suite), suite, [], k=blank, m=blank)
        tail = _selection_argv(run)
        assert "-k" not in tail, f"-k leaked for {blank!r}: {tail}"
        assert "-m" not in tail, f"-m leaked for {blank!r}: {tail}"
        # And the stored effective values are None (echoed on `started`).
        assert run.k is None and run.m is None


def test_argv_includes_trimmed_nonblank_expression(suite):
    run = _Run("run-1", RunManager(suite), suite, [], k="  alpha ", m=" slow\t")
    tail = _selection_argv(run)
    # Present and trimmed, emitted as a single `-opt=value` token so a value
    # starting with `-` can't be re-parsed by argparse as a flag.
    assert "-k=alpha" in tail
    assert "-m=slow" in tail


def test_whitespace_m_is_dropped_and_runs_everything(suite):
    """Subprocess: `-m "   "` must be TRIMMED+OMITTED, not deselect everything.

    A naive "pass m through" would make whitespace-`-m` an empty expression that
    matches NOTHING → pytest exit 5 / zero tests. With the trim safeguard the
    flag is dropped and the full selection runs.
    """

    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        await mgr.start(
            [
                "test_sel.py::test_alpha",
                "test_sel.py::test_beta",
                "test_sel.py::test_gamma",
                "test_sel.py::test_delta",
            ],
            m="   ",
        )
        events = await collect_until(sub)
        await _join(mgr)

        names = [n for n, _ in events]
        # The flag was dropped, so everything ran instead of collapsing to exit 5.
        assert call_nodeids(events) == [
            "test_sel.py::test_alpha",
            "test_sel.py::test_beta",
            "test_sel.py::test_delta",
            "test_sel.py::test_gamma",
        ]
        assert "error" not in names
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0  # ran fine, not 5 (no tests)

    run_async(body())


# === 3. expression-only run (no node IDs) =================================


def test_expression_only_run_no_nodeids(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        # Empty nodeids, selection driven purely by -m slow. No "nothing to run"
        # guard should block it.
        await mgr.start([], m="slow")
        events = await collect_until(sub)
        await _join(mgr)

        assert call_nodeids(events) == ["test_sel.py::test_alpha"]
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0

    run_async(body())


def test_expression_only_run_with_k(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        await mgr.start([], k="beta or gamma")
        events = await collect_until(sub)
        await _join(mgr)

        assert call_nodeids(events) == [
            "test_sel.py::test_beta",
            "test_sel.py::test_gamma",
        ]
        assert next(d for n, d in events if n == "finished")["exit_code"] == 0

    run_async(body())


# === 4. exit 5 = benign no-match, so finished, not error ==================


def test_no_match_k_finishes_with_exit_5_not_error(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        await mgr.start([], k="zzznomatch")
        events = await collect_until(sub)
        await _join(mgr)

        names = [n for n, _ in events]
        # A valid expression matching nothing is benign: finished(5), no error event.
        assert "error" not in names, names
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 5
        # Nothing actually ran.
        assert call_nodeids(events) == []

    run_async(body())


# === 5. exit 4 = invalid expression, so error =============================


def test_invalid_k_expression_emits_error_exit_4(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        # "and or" is a malformed -k expression, a pytest usage error (exit 4).
        await mgr.start(["test_sel.py::test_alpha"], k="and or")
        events = await collect_until(sub)
        await _join(mgr)

        names = [n for n, _ in events]
        assert "error" in names, names
        assert "finished" not in names
        err = next(d for n, d in events if n == "error")
        assert err["exit_code"] == 4

    run_async(body())


# === 6. `started` echoes trimmed/effective values =========================


def test_started_event_echoes_trimmed_k_and_omitted_m(suite):
    async def body():
        mgr = RunManager(suite)
        sub = mgr.subscribe()
        await mgr.start(["test_sel.py::test_alpha"], k="  alpha  ", m="   ")
        events = await collect_until(sub)
        await _join(mgr)

        started = next(d for n, d in events if n == "started")
        # k is trimmed to its effective value; the blank m is omitted, so it's None.
        assert started["k"] == "alpha"
        assert started["m"] is None

    run_async(body())


# --- _trim_expr direct unit coverage --------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("\t\n", None),
        ("slow", "slow"),
        ("  slow  ", "slow"),
        ("a or b", "a or b"),
        ("  a and b  ", "a and b"),
    ],
)
def test_trim_expr(raw, expected):
    assert _trim_expr(raw) == expected
