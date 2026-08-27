"""Regression: the huge-suite 1 MiB collection-line bug (user-reported).

``_inner.pytest_collection_finish`` now emits the ``$deck=="collection"`` line
ONLY in collect mode (gated on ``config.option.collectonly``). In RUN mode that
line is pure waste — and worse, its size scales with the suite, so on ~1000+
tests the single JSON line overran the fd-3 reader's 1 MiB ``StreamReader`` limit
and fired a spurious ``error`` event ("a result line exceeded the 1 MiB buffer").
The fix returns early during a run before even building the items list.

These tests drive the REAL fd-3 path (``RunManager``, ``test_runner.py`` style)
with the ``run_async`` helper (``pytest-asyncio`` not installed):

* run mode emits NO ``collection`` record on fd 3 (only ``report``),
* a large suite (1500 tests with long parametrize ids — big enough that the
  collection line WOULD overrun 1 MiB if it were emitted) runs to ``finished``
  with reports and NO "1 MiB buffer" ``error``.

Collect mode still emitting the collection line is covered by ``test_server.py``'s
``/api/collect`` tests and ``test_collect_errors.py`` (the tree they assert on is
built from exactly that line), plus explicitly here.
"""

import asyncio
import json

import pytest

from pytest_deck import runner
from pytest_deck.runner import RunManager


def run_async(coro):
    return asyncio.run(coro)


async def _drain_to_finish(sub, timeout=120.0):
    events = []
    while True:
        ev = await asyncio.wait_for(sub.get(), timeout=timeout)
        if ev is None:
            break
        events.append((ev.name, ev.data))
        if ev.name in ("finished", "error"):
            # Keep draining past an error, since a non-fatal one can precede
            # finished; only a terminal finished stops us.
            if ev.name == "finished":
                break
    return events


# --- run mode emits no collection line ------------------------------------


@pytest.fixture
def small_suite(tmp_path):
    (tmp_path / "test_s.py").write_text(
        "def test_a():\n    assert True\n\n" "def test_b():\n    assert True\n"
    )
    return tmp_path


def test_run_mode_emits_no_collection_line(small_suite, monkeypatch):
    """During a run, NO ``$deck=="collection"`` record reaches the fd-3 reader.

    We spy on ``_Run._dispatch_fd3`` to observe the raw fd-3 records (the
    ``collection`` line is ignored by the dispatcher, so it never becomes an
    event — we must look at the raw line to prove it isn't emitted at all).
    """
    seen_kinds = []
    orig = runner._Run._dispatch_fd3

    def spy(self, line):
        try:
            obj = json.loads(line)
            if "$deck" in obj:
                seen_kinds.append(obj["$deck"])
        except Exception:
            pass
        return orig(self, line)

    monkeypatch.setattr(runner._Run, "_dispatch_fd3", spy)

    async def body():
        mgr = RunManager(small_suite)
        sub = mgr.subscribe()
        await mgr.start(["test_s.py::test_a", "test_s.py::test_b"])
        events = await _drain_to_finish(sub)
        await mgr._run.join()

        names = [n for n, _ in events]
        assert "finished" in names
        # The run produced reports...
        assert "report" in names
        # ...but no collection line ever hit fd 3 (the whole point of the gate).
        assert "collection" not in seen_kinds, seen_kinds
        # Only report and warning records (plus the small mpl_name and
        # plugin_meta ones) ride fd 3 during a run, never the suite-scaled
        # collection line (P6).
        assert set(seen_kinds) <= {
            "report",
            "warning",
            "mpl_name",
            "plugin_meta",
        }, seen_kinds

    run_async(body())


# --- large-suite regression (the actual bug) ------------------------------


@pytest.fixture
def large_suite(tmp_path):
    """A 1500-test suite with LONG parametrize ids.

    The ids are ~800 chars each, so the collection line (nodeid+path per item)
    would blow well past the fd-3 reader's 1 MiB limit IF it were emitted in run
    mode. With the gate in place the run must complete cleanly. 1500 lightweight
    (assert-only) tests run in well under a second, so this stays fast.
    """
    (tmp_path / "test_big.py").write_text(
        "import pytest\n"
        "LONG = 'p' * 800\n"
        "@pytest.mark.parametrize('n', [f'{LONG}_{i}' for i in range(1500)])\n"
        "def test_many(n):\n"
        "    assert isinstance(n, str)\n"
    )
    return tmp_path


def test_large_suite_run_completes_without_1mib_error(large_suite):
    async def body():
        mgr = RunManager(large_suite)
        sub = mgr.subscribe()
        await mgr.start(["test_big.py"])
        events = await _drain_to_finish(sub)
        await mgr._run.join()

        names = [n for n, _ in events]

        # The run finished normally with a full set of reports (1500 x 3 phases).
        assert "finished" in names, names[-5:]
        reports = [d for n, d in events if n == "report"]
        assert len(reports) == 4500, len(reports)
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0

        # The actual regression: no spurious "1 MiB buffer" error (user-reported).
        errors = [d for n, d in events if n == "error"]
        assert errors == [], [e.get("message") for e in errors]
        assert not any(
            "1 MiB buffer" in (d.get("message") or "")
            for n, d in events
            if n == "error"
        )

    run_async(body())
