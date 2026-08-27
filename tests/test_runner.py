"""Tests for ``pytest_deck.runner.RunManager`` — the async live-run engine.

These drive a **real** pytest subprocess (pty stdio + fd-3 pipe, as in
production) against a tiny fixture suite written to a tmp dir, and assert on the
SSE events the manager fans out. They cover the runner's contract:

* incremental ``report`` streaming in phase order, then ``finished`` with the
  right exit code,
* ``cancel`` kills the process group (no zombie) and emits ``cancelled``/user,
* kill-and-restart: run A → ``cancelled``/superseded, run B → ``finished``,
* error path: a stale nodeid → ``error`` with exit_code 4,
* fan-out: two subscribers see the same run's events.

``pytest-asyncio`` is **not** installed in the dev venv, so rather than add a
dependency we drive the event loop ourselves with ``asyncio.run`` via the
``run_async`` helper. Each test gets a fresh loop, which keeps subprocess/pty
teardown deterministic.
"""

import asyncio
import os

import pytest

from pytest_deck.runner import RunManager

# --- async driver (no pytest-asyncio dependency) --------------------------


def run_async(coro):
    """Run an async test body to completion on a fresh event loop."""
    return asyncio.run(coro)


async def drain(queue, until, timeout=30.0):
    """Collect (name, data) events from a subscriber queue until ``until``.

    ``until`` is a predicate over the accumulated event-name list; collection
    stops once it returns True (e.g. a terminal event arrived). Raises on
    timeout so a hung run fails loudly instead of blocking forever.
    """
    events = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(
                f"timed out waiting; saw events: {[n for n, _ in events]}"
            )
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            raise AssertionError(
                f"timed out waiting; saw events: {[n for n, _ in events]}"
            )
        events.append((ev.name, ev.data))
        if until([n for n, _ in events]):
            return events


def names(events):
    return [n for n, _ in events]


# --- fixture suites -------------------------------------------------------


@pytest.fixture
def suite(tmp_path):
    """A small suite: two quick passers, one quick failer."""
    (tmp_path / "test_suite.py").write_text(
        "def test_a():\n"
        "    assert 1 + 1 == 2\n"
        "\n"
        "def test_b():\n"
        "    assert 'x' in 'xyz'\n"
        "\n"
        "def test_c_fails():\n"
        "    assert 1 == 2\n"
    )
    return tmp_path


@pytest.fixture
def slow_suite(tmp_path):
    """A suite whose tests sleep, so a run is long enough to cancel mid-flight."""
    (tmp_path / "test_slow.py").write_text(
        "import time\n"
        "\n"
        "def test_slow_1():\n"
        "    time.sleep(2.0)\n"
        "\n"
        "def test_slow_2():\n"
        "    time.sleep(2.0)\n"
        "\n"
        "def test_slow_3():\n"
        "    time.sleep(2.0)\n"
    )
    return tmp_path


@pytest.fixture
def warning_suite(tmp_path):
    """A suite whose single test emits a UserWarning (warn call on line 4)."""
    (tmp_path / "test_warns.py").write_text(
        "import warnings\n"
        "\n"
        "def test_warns():\n"
        "    warnings.warn('boom', UserWarning)\n"
    )
    return tmp_path


# --- incremental streaming + phase order ----------------------------------


def test_reports_stream_in_phase_order_then_finished(suite):
    async def body():
        mgr = RunManager(suite)
        q = mgr.subscribe()
        nodeids = [
            "test_suite.py::test_a",
            "test_suite.py::test_b",
            "test_suite.py::test_c_fails",
        ]
        run_id = await mgr.start(nodeids)
        events = await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)

        ns = names(events)
        # The run announced itself, produced reports, and finished.
        assert ns[0] == "started"
        assert "finished" in ns

        # Exactly one report per phase per test: 3 tests x 3 phases = 9.
        reports = [d for n, d in events if n == "report"]
        assert len(reports) == 9, ns

        # Every event carries the run_id (so reconnecting clients disambiguate).
        for _, d in events:
            assert d["run_id"] == run_id

        # Per nodeid the phases arrive as setup, then call, then teardown.
        for nodeid in nodeids:
            seq = [r["when"] for r in reports if r["nodeid"] == nodeid]
            assert seq == ["setup", "call", "teardown"], (nodeid, seq)

        # Raw phase outcomes are faithful: passers pass on call, failer fails.
        def call_outcome(nodeid):
            return next(
                r["outcome"]
                for r in reports
                if r["nodeid"] == nodeid and r["when"] == "call"
            )

        assert call_outcome("test_suite.py::test_a") == "passed"
        assert call_outcome("test_suite.py::test_b") == "passed"
        assert call_outcome("test_suite.py::test_c_fails") == "failed"

        # The failing call carries a rendered traceback; passers don't.
        fail_call = next(
            r
            for r in reports
            if r["nodeid"] == "test_suite.py::test_c_fails" and r["when"] == "call"
        )
        assert fail_call["longrepr"] and "assert" in fail_call["longrepr"]

        # exit_code 1 = tests ran with failures (one test failed).
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 1
        assert finished["duration"] is not None

    run_async(body())


def test_started_event_carries_argv_and_selection(suite):
    async def body():
        mgr = RunManager(suite)
        q = mgr.subscribe()
        nodeids = ["test_suite.py::test_a"]
        await mgr.start(nodeids)
        events = await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)

        started = next(d for n, d in events if n == "started")
        assert started["nodeids"] == nodeids
        assert started["k"] is None and started["m"] is None
        # argv runs python -m pytest with the inner plugin and color on a pty.
        assert "pytest" in started["argv"]
        assert "pytest_deck._inner" in started["argv"]
        assert "--color=yes" in started["argv"]
        # The selected nodeid is forwarded as a positional arg.
        assert "test_suite.py::test_a" in started["argv"]

    run_async(body())


# --- cancel ---------------------------------------------------------------


def test_cancel_kills_group_and_emits_cancelled(slow_suite):
    async def body():
        mgr = RunManager(slow_suite)
        q = mgr.subscribe()
        await mgr.start(
            [
                "test_slow.py::test_slow_1",
                "test_slow.py::test_slow_2",
                "test_slow.py::test_slow_3",
            ]
        )
        # Wait until the run is actually up before cancelling.
        await drain(q, lambda ns: "started" in ns, timeout=10.0)
        run = mgr._run
        pid = run.proc.pid

        cancelled, run_id = await mgr.cancel()
        assert cancelled is True
        assert run_id == run.run_id

        events = await drain(q, lambda ns: "cancelled" in ns, timeout=10.0)
        ns = names(events)
        # A cancelled run emits cancelled/user and never a finished event.
        cancel_ev = next(d for n, d in events if n == "cancelled")
        assert cancel_ev["reason"] == "user"
        assert "finished" not in ns

        # The process group is dead: returncode set, and no live/zombie process.
        assert run.proc.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    run_async(body())


def test_cancel_idle_returns_false(suite):
    async def body():
        mgr = RunManager(suite)
        cancelled, run_id = await mgr.cancel()
        assert cancelled is False
        assert run_id is None
        # After a finished run, cancel is also a no-op (run already settled).
        q = mgr.subscribe()
        await mgr.start(["test_suite.py::test_a"])
        await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)
        cancelled2, _ = await mgr.cancel()
        assert cancelled2 is False

    run_async(body())


# --- kill-and-restart -----------------------------------------------------


def test_starting_run_b_supersedes_run_a(slow_suite):
    async def body():
        mgr = RunManager(slow_suite)
        q = mgr.subscribe()

        run_a = await mgr.start(["test_slow.py::test_slow_1"])
        await drain(q, lambda ns: "started" in ns, timeout=10.0)
        a_proc = mgr._run.proc
        a_pid = a_proc.pid

        # Starting B kills A first (kill-and-restart, single in-flight).
        run_b = await mgr.start(["test_slow.py::test_slow_2"])
        assert run_b != run_a

        # Collect until B finishes; both A's cancelled and B's lifecycle appear
        # on the shared subscriber stream.
        events = await drain(
            q,
            lambda ns: any(n == "finished" for n in ns),
            timeout=30.0,
        )

        # A was superseded; B started and finished.
        a_cancel = [d for n, d in events if n == "cancelled" and d["run_id"] == run_a]
        assert a_cancel, names(events)
        assert a_cancel[0]["reason"] == "superseded"

        b_started = [d for n, d in events if n == "started" and d["run_id"] == run_b]
        assert b_started, names(events)
        b_finished = [d for n, d in events if n == "finished" and d["run_id"] == run_b]
        assert b_finished, names(events)

        # A's process is gone; no finished event for A.
        assert a_proc.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(a_pid, 0)
        assert not [d for n, d in events if n == "finished" and d["run_id"] == run_a]

        await _join_current(mgr)

    run_async(body())


# --- warning record (fd-3 `$deck: "warning"` becomes SSE `warning` event) ----


def test_warning_record_streams_as_warning_event(warning_suite):
    # End-to-end coverage of the warning path: the inner plugin's
    # pytest_warning_recorded hook writes an fd-3 JSON record, and RunManager
    # turns it into a `warning` event.
    async def body():
        mgr = RunManager(warning_suite)
        q = mgr.subscribe()
        run_id = await mgr.start(["test_warns.py::test_warns"])
        events = await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)

        warns = [
            d
            for n, d in events
            if n == "warning" and d["nodeid"] == "test_warns.py::test_warns"
        ]
        assert len(warns) == 1, names(events)
        w = warns[0]
        assert w["run_id"] == run_id
        assert w["category"] == "UserWarning"
        assert "boom" in w["message"]
        assert w["when"] == "runtest"
        assert w["filename"].endswith("test_warns.py")
        assert w["lineno"] == 4  # the warnings.warn(...) line

        # The warning does not fail the run: exit 0, test passed on call.
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0
        call = next(d for n, d in events if n == "report" and d["when"] == "call")
        assert call["outcome"] == "passed"

    run_async(body())


# --- shutdown (lifespan teardown path) --------------------------------------


def test_shutdown_kills_live_run_and_emits_cancelled_user(slow_suite):
    async def body():
        mgr = RunManager(slow_suite)
        q = mgr.subscribe()
        await mgr.start(["test_slow.py::test_slow_1"])
        await drain(q, lambda ns: "started" in ns, timeout=10.0)
        run = mgr._run
        pid = run.proc.pid

        await mgr.shutdown()

        events = await drain(q, lambda ns: "cancelled" in ns, timeout=10.0)
        # shutdown reuses the user-cancel path: cancelled/user, never finished.
        cancel_ev = next(d for n, d in events if n == "cancelled")
        assert cancel_ev["reason"] == "user"
        assert "finished" not in names(events)

        # The process group is dead: returncode set, no live/zombie process.
        assert run.proc.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert not mgr.is_active()

    run_async(body())


def test_shutdown_idle_is_noop(suite):
    async def body():
        # Never-started manager: shutdown must not raise.
        mgr = RunManager(suite)
        await mgr.shutdown()
        assert not mgr.is_active()

        # After a finished run, shutdown is also a no-op: no extra event.
        q = mgr.subscribe()
        await mgr.start(["test_suite.py::test_a"])
        await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)
        await mgr.shutdown()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.2)

    run_async(body())


# --- error path -----------------------------------------------------------


def test_spawn_failure_emits_error_and_settles(suite, monkeypatch):
    # If the pytest subprocess can't even be spawned (exec failure), the run
    # must emit a single `error` event, settle (join returns), and close every
    # fd it opened (the fd-3 pipe pair and the pty pair): no leak, no hang, no
    # zombie.
    async def body():
        def boom(*args, **kwargs):
            raise OSError("no such executable")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

        fds_before = sorted(os.listdir("/proc/self/fd"))
        mgr = RunManager(suite)
        q = mgr.subscribe()
        run_id = await mgr.start(["test_suite.py::test_a"])

        events = await drain(q, lambda ns: "error" in ns, timeout=10.0)
        # The failure is the only event: no started, no finished, no cancelled.
        assert names(events) == ["error"]
        err = events[0][1]
        assert err["run_id"] == run_id
        assert "failed to start pytest" in err["message"]
        assert "no such executable" in err["message"]
        assert err["exit_code"] is None

        # The run settled: join returns promptly, nothing is active.
        await asyncio.wait_for(mgr._run.join(), timeout=5.0)
        assert not mgr.is_active()

        # Cancel is a no-op on the settled run (it still reports its run_id).
        cancelled, cancel_run_id = await mgr.cancel()
        assert cancelled is False
        assert cancel_run_id == run_id

        # All four fds opened before the spawn attempt were closed again.
        fds_after = sorted(os.listdir("/proc/self/fd"))
        assert fds_after == fds_before

    run_async(body())


def test_stale_nodeid_emits_error_exit_code_4(suite):
    async def body():
        mgr = RunManager(suite)
        q = mgr.subscribe()
        await mgr.start(["test_suite.py::test_does_not_exist"])
        events = await drain(q, lambda ns: "error" in ns or "finished" in ns)
        await _join_current(mgr)

        ns = names(events)
        # A nonexistent nodeid is pytest's usage error 4 with no reports, which
        # surfaces as `error`.
        assert "error" in ns, ns
        assert "finished" not in ns
        err = next(d for n, d in events if n == "error")
        assert err["exit_code"] == 4
        assert "not found" in err["message"].lower()

    run_async(body())


# --- fan-out --------------------------------------------------------------


def test_two_subscribers_see_the_same_run(suite):
    async def body():
        mgr = RunManager(suite)
        q1 = mgr.subscribe()
        q2 = mgr.subscribe()
        run_id = await mgr.start(["test_suite.py::test_a", "test_suite.py::test_b"])

        ev1 = await drain(q1, lambda ns: "finished" in ns)
        ev2 = await drain(q2, lambda ns: "finished" in ns)
        await _join_current(mgr)

        # Both subscribers saw the same run's lifecycle and the same #reports.
        for events in (ev1, ev2):
            assert names(events)[0] == "started"
            assert "finished" in names(events)
            assert all(d["run_id"] == run_id for _, d in events)
            assert len([n for n in names(events) if n == "report"]) == 6  # 2x3

        # Same finished exit_code on both streams (0 = all passed).
        f1 = next(d for n, d in ev1 if n == "finished")
        f2 = next(d for n, d in ev2 if n == "finished")
        assert f1["exit_code"] == 0 == f2["exit_code"]

    run_async(body())


def test_console_events_carry_pty_output(suite):
    async def body():
        mgr = RunManager(suite)
        q = mgr.subscribe()
        await mgr.start(["test_suite.py::test_a"])
        events = await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)

        consoles = [d["text"] for n, d in events if n == "console"]
        joined = "".join(consoles)
        # pytest's banner reaches us over the pty, with ANSI color (--color=yes).
        assert "test session starts" in joined
        assert "\x1b[" in joined, "expected ANSI color from --color=yes on a pty"

    run_async(body())


def test_console_raw_buffer_contains_ansi_wrapped_closing_block(suite):
    """Root-cause pin: the raw pty buffer DOES carry pytest's closing
    summary block — but ANSI-wrapped, so a plain ``^=+.*=+$`` match on the raw
    text never fires. The frontend must match on an ANSI-stripped copy
    (RunConsole → consoleTail.js); this test proves the data is there so the
    fix is frontend-only, not a reader-lifecycle (B14/EIO) issue.
    """
    import re

    async def body():
        mgr = RunManager(suite)
        q = mgr.subscribe()
        # One passer and one failer, so we get both a "short test summary info"
        # section and a "1 failed, 1 passed in Xs" closing line.
        await mgr.start(["test_suite.py::test_a", "test_suite.py::test_c_fails"])
        events = await drain(q, lambda ns: "finished" in ns)
        await _join_current(mgr)

        joined = "".join(d["text"] for n, d in events if n == "console")
        lines = joined.replace("\r", "").split("\n")
        stripped = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]

        # The closing block is in the raw buffer (seen through the
        # ANSI-stripped view).
        closing = [
            i
            for i, ln in enumerate(stripped)
            if re.match(r"^=+.*=+$", ln) and " in " in ln
        ]
        assert closing, f"no closing summary line in buffer tail: {stripped[-8:]}"
        assert any("1 failed" in stripped[i] for i in closing)
        assert any("short test summary info" in ln for ln in stripped)

        # ...but every raw closing line is ANSI-wrapped: it neither starts nor
        # ends with '=', so the frontend's raw-buffer regex cannot match it.
        for i in closing:
            assert lines[i] != stripped[i], "expected ANSI colour on the summary"
            assert not re.match(r"^=+.*=+$", lines[i])

    run_async(body())


# --- helpers --------------------------------------------------------------


async def _join_current(mgr):
    """Await the current run's tasks so no fds/tasks/zombies leak between tests."""
    run = mgr._run
    if run is not None:
        await run.join()
