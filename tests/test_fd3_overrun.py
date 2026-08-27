"""Regression tests for over-long fd-3 line survival (defect #2).

A ``longrepr_text`` line larger than the 1 MiB ``StreamReader`` limit must NOT
kill the fd-3 reader. ``StreamReader.readline()`` raises a plain ``ValueError``
(not ``LimitOverrunError``) on overrun, and the reader must:

* recognise the overrun (vs. a genuine reader failure),
* recover the buffer to the start of the next line,
* emit a truncated/``error`` event,
* and KEEP delivering LATER reports.

CPython produces two distinct overrun messages that need different recovery:

* **separator found** — ``"Separator is found, but chunk is longer than limit"``:
  the over-long line *and* its newline were already consumed; the next line is
  clean. (Happens when the whole giant line + newline is buffered before
  ``readline`` runs.)
* **separator not found** — ``"Separator is not found, and chunk exceed the
  limit"``: the partial line is still buffered with no newline yet; the reader
  must drain forward past the next newline to realign. (The realistic case over a
  real pipe, where data trickles in 64 KiB chunks and the newline is never
  buffered together with the head.)

This module covers BOTH branches:

* ``test_real_subprocess_*`` — an end-to-end run where pytest itself emits a
  >1 MiB ``longrepr`` (the separator-not-found path over a real pipe), with a
  normal test AFTER it whose report must still arrive.
* ``test_recover_*`` — focused unit tests driving the PRODUCTION resilient
  iterator (``runner._fd3_lines``) over an in-memory ``StreamReader``,
  deterministically exercising EACH overrun message and asserting the next line
  survives.

Harness caveat (from the implementer): a >1 MiB ``os.write`` to a 64 KiB pipe
BLOCKS until drained, so we never write a giant line to a pipe from the event
loop. The real-subprocess test lets pytest emit the giant line (pytest writes
it); the unit tests feed an in-memory ``StreamReader`` with ``feed_data`` (no
pipe, no blocking).
"""

import asyncio

import pytest

from pytest_deck.runner import (
    _FD3_LIMIT,
    OVERRUN,
    RunManager,
    _fd3_lines,
    _is_overrun,
)


def run_async(coro):
    return asyncio.run(coro)


# === end-to-end: pytest emits a real >1 MiB longrepr ======================


@pytest.fixture
def huge_suite(tmp_path):
    """A failing test whose traceback exceeds 1 MiB, then a normal test after it.

    A raised ``AssertionError`` with a multi-megabyte message renders a >1 MiB
    ``longrepr_text`` on one fd-3 line (pytest does NOT truncate a raised
    exception's message the way it truncates an ``assert a == b`` operand repr).
    ``test_after_huge`` is selected AFTER it: its report proves the fd-3 reader
    survived the overrun.
    """
    (tmp_path / "test_huge.py").write_text(
        "def test_huge_longrepr():\n"
        "    raise AssertionError('BOOM ' + 'Y' * (2 * 1024 * 1024))\n"
        "\n"
        "def test_after_huge():\n"
        "    assert 1 + 1 == 2\n"
    )
    return tmp_path


def test_real_subprocess_overrun_does_not_kill_reader(huge_suite):
    async def body():
        mgr = RunManager(huge_suite)
        sub = mgr.subscribe()
        await mgr.start(
            [
                "test_huge.py::test_huge_longrepr",
                "test_huge.py::test_after_huge",
            ]
        )

        events = []
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=60)
            if ev is None:
                break
            events.append((ev.name, ev.data))
            if ev.name == "finished":
                break
        await mgr._run.join()

        names = [n for n, _ in events]
        reports = [d for n, d in events if n == "report"]

        # The over-long line was recognised and surfaced as an error event
        # (truncated traceback), not a crash.
        errors = [d for n, d in events if n == "error"]
        assert errors, names
        assert "exceeded the 1 MiB buffer" in errors[0]["message"]
        # The overrun error is non-terminal: the flag lets the frontend surface
        # it without ending the still-running run.
        assert errors[0]["fatal"] is False

        # The reader recovered: every phase of the test after the giant one
        # arrived, in order. This is the whole point of the fix; a giant
        # traceback must not silence every report that follows it.
        after = [r for r in reports if r["nodeid"] == "test_huge.py::test_after_huge"]
        assert [r["when"] for r in after] == ["setup", "call", "teardown"], after
        call = next(r for r in after if r["when"] == "call")
        assert call["outcome"] == "passed"

        # The run still finished cleanly (exit 1 means there were failures).
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 1

    run_async(body())


# === unit: the resilient iterator, both overrun branches ====================
#
# These drive the production ``_fd3_lines`` generator (the exact code
# ``_Run._read_fd3`` iterates) against an in-memory StreamReader, so there is
# no pipe and no >1 MiB write to block on. An over-long line comes back as the
# OVERRUN sentinel; the next (normal) line must survive intact.


async def _collect(reader):
    """Materialize ``_fd3_lines(reader)`` into (intact lines, overrun count)."""
    lines = []
    overruns = 0
    async for item in _fd3_lines(reader):
        if item is OVERRUN:
            overruns += 1
        else:
            lines.append(item)
    return lines, overruns


def test_recover_separator_not_found_then_next_line_survives():
    """Giant line with NO newline buffered yet → 'separator not found' branch.

    Feeding must INTERLEAVE with the iterator: if the newline is already
    buffered when readline first runs, CPython raises the separator-FOUND
    message instead and the drain loop goes untested. We feed the over-long
    head, let the iterator hit the overrun and park mid-drain, feed a second
    over-limit chunk (forcing the readexactly branch), then the newline and a
    clean following line. This is the realistic over-a-pipe case.
    """

    async def body():
        head = b"G" * (_FD3_LIMIT + 5000)  # over the limit, no newline yet

        # Pin that an over-limit buffer with no separator raises the "not found"
        # message, so this test can't silently degrade into the other branch.
        probe = asyncio.StreamReader(limit=_FD3_LIMIT)
        probe.feed_data(head)
        with pytest.raises(ValueError) as ei:
            await probe.readline()
        assert "chunk exceed the limit" in str(ei.value)

        reader = asyncio.StreamReader(limit=_FD3_LIMIT)
        reader.feed_data(head)
        task = asyncio.ensure_future(_collect(reader))
        await asyncio.sleep(0.05)  # iterator hits the overrun, parks in the drain
        # A second over-limit chunk mid-drain exercises readexactly(consumed).
        reader.feed_data(b"G" * (_FD3_LIMIT + 5000))
        await asyncio.sleep(0.05)
        reader.feed_data(b"end-of-the-giant-line\n")
        reader.feed_data(b'{"$deck":"report","report":{"nodeid":"after"}}\n')
        reader.feed_eof()

        lines, overruns = await asyncio.wait_for(task, timeout=10)

        assert overruns == 1
        # The giant line was dropped; the next clean line survived intact.
        assert lines == [b'{"$deck":"report","report":{"nodeid":"after"}}\n'], lines

    run_async(body())


def test_genuine_error_ends_iteration_without_overrun():
    """A genuine (non-overrun) reader error must END the iteration — no OVERRUN
    yielded, no lines invented (the old loop's `break`; recovery applies only to
    overruns)."""

    async def body():
        reader = asyncio.StreamReader(limit=_FD3_LIMIT)
        reader.feed_data(b'{"$deck":"report","report":{"nodeid":"first"}}\n')
        reader.set_exception(ValueError("some other reader failure"))

        lines, overruns = await _collect(reader)

        assert overruns == 0
        # set_exception surfaces immediately, so iteration ends with nothing read.
        assert lines == []

    run_async(body())


def test_recover_separator_found_then_next_line_survives():
    """Whole giant line + newline buffered before readline → 'separator found'.

    Here CPython has already consumed the over-long line through its separator,
    so recovery is a no-op and the very next readline returns the following line.
    """

    async def body():
        reader = asyncio.StreamReader(limit=_FD3_LIMIT)
        # The entire giant line with its newline, plus the next line, all
        # buffered before readline runs: that is the "Separator is found" case.
        giant_line = b"G" * (_FD3_LIMIT + 5000) + b"\n"
        reader.feed_data(giant_line)
        reader.feed_data(b'{"$deck":"report","report":{"nodeid":"after"}}\n')
        reader.feed_eof()

        # Pin that this construction actually hits the separator-found message,
        # so the test can't silently degrade into the other branch.
        probe = asyncio.StreamReader(limit=_FD3_LIMIT)
        probe.feed_data(giant_line)
        probe.feed_eof()
        with pytest.raises(ValueError) as ei:
            await probe.readline()
        assert "chunk is longer than limit" in str(ei.value)

        lines, overruns = await _collect(reader)

        assert overruns >= 1
        assert lines == [b'{"$deck":"report","report":{"nodeid":"after"}}\n'], lines

    run_async(body())


def test_genuine_reader_value_error_is_not_swallowed_as_overrun():
    """A non-overrun ValueError must NOT be misclassified as an overrun.

    ``_is_overrun`` keys off the two specific CPython overrun messages; anything
    else is a real failure and ends ``_fd3_lines`` (no OVERRUN yielded).
    """
    assert _is_overrun(ValueError("Separator is found, but chunk is longer than limit"))
    assert _is_overrun(ValueError("Separator is not found, and chunk exceed the limit"))
    assert not _is_overrun(ValueError("some other reader failure"))
    assert not _is_overrun(ValueError(""))
