"""Regressions: a poison fd-3 line must never kill a reader.

``_dispatch_fd3`` used to catch only ``json.JSONDecodeError`` around
``json.loads``, so three whole classes of line escaped and killed the fd-3
reader task via ``_read_fd3``'s ``finally`` — silently dropping EVERY later
report (a P10 violation; the orphaned tests then flip to ``missing`` in the
UI):

* a **deeply-nested JSON line** — ``json.loads`` can raise ``RecursionError``
  (a ``RuntimeError``, NOT a ``ValueError`` subclass, so it sailed past the
  narrow catch). How deep counts as too deep is a property of the running
  interpreter and of the C stack its thread happens to have, not of the
  document: the same bytes raise here and parse into a plain list on a CI
  runner with a roomier stack. Both outcomes are survivable, and the raising
  branch is pinned directly (with a stub) rather than by input size;
* **non-UTF-8 bytes** — ``json.loads(bytes)`` raises ``UnicodeDecodeError``
  from the decode step before any JSON parsing;
* a **valid-JSON non-dict line** — the kind dispatch breaks on it
  (``obj.get`` → AttributeError on a list, ``"$deck" in 42`` → TypeError).

The collect path shared the RecursionError and non-dict sub-cases
(``collector._iter_payloads``; it reads the fd with ``errors="replace"``, so
the UnicodeDecodeError class can't occur there).

All the poison lines here are far below the 1 MiB buffer, so the OVERRUN
defense (``test_fd3_overrun``) never engages — these are a separate seam.
Coverage is three layers: probes pinning that each line class really raises
what we claim, unit drives on the production dispatchers, and end-to-end
runs/collects where the child emits the poison itself.
"""

import asyncio
import json

import pytest

from pytest_deck.collector import _iter_payloads, collect
from pytest_deck.runner import RunManager, _Run

# One line per poison class (no trailing newline; appended where needed).
DEEP_NESTED = b"[" * 60000 + b"]" * 60000
NON_UTF8 = b'{"a": "\xff\xfe"}'
NON_DICTS = (b"42", b"[1, 2]", b'"just a string"', b"null", b"true")


async def _drain(queue, until, timeout=30.0):
    """Collect (name, data) events until the predicate over names is True."""
    events = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, f"timed out; saw {[n for n, _ in events]}"
        ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        events.append((ev.name, ev.data))
        if until([n for n, _ in events]):
            return events


class _StubManager:
    def __init__(self):
        self.events = []

    def broadcast(self, event):
        self.events.append(event)


# === probes: each class raises past json.JSONDecodeError ====================


def test_poison_classes_are_never_plain_jsondecodeerror():
    # Pin the failure classes so the tests below can't silently degrade into
    # the already-covered JSONDecodeError path if CPython's json ever changes.
    # DEEP_NESTED is deliberately allowed either outcome: json.loads gives up
    # at a depth set by the available C stack, so it raises RecursionError on
    # a small stack and returns a plain list on a large one. Both are classes
    # the dispatchers must skip, and a JSONDecodeError here would fail the
    # test by escaping.
    try:
        parsed = json.loads(DEEP_NESTED)
    except RecursionError:
        pass
    else:
        assert isinstance(parsed, list)
    with pytest.raises(UnicodeDecodeError):
        json.loads(NON_UTF8)
    # The non-dict class parses fine; it broke the old kind dispatch instead.
    assert json.loads(b"42") == 42


def test_dispatchers_survive_a_recursionerror_from_json(monkeypatch, tmp_path):
    """Pin the ``RecursionError`` catch itself, on every platform.

    A deeply nested literal only reaches that catch on interpreters whose
    stack runs out first, so this drives it with a stub instead: both
    dispatchers must swallow the error, and the run-path seam must keep
    working once ``json`` is itself again.
    """

    def boom(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    stub = _StubManager()
    run = _Run("run-1", stub, tmp_path, [], None, None)
    monkeypatch.setattr(json, "loads", boom)
    run._dispatch_fd3(b'{"$deck": "report"}\n')  # must not raise
    assert list(_iter_payloads('{"$deck": "collection", "items": []}')) == []
    assert stub.events == []
    monkeypatch.undo()

    good = json.dumps(
        {"$deck": "report", "report": {"nodeid": "after", "when": "call"}}
    )
    run._dispatch_fd3(good.encode() + b"\n")
    assert [e.name for e in stub.events] == ["report"]


# === unit: the run-path dispatcher survives every class =====================


def test_dispatch_fd3_survives_poison_then_keeps_dispatching(tmp_path):
    stub = _StubManager()
    run = _Run("run-1", stub, tmp_path, [], None, None)
    for bad in (DEEP_NESTED, NON_UTF8, *NON_DICTS):
        run._dispatch_fd3(bad + b"\n")  # must not raise
    assert stub.events == []  # poison is skipped silently (P10)
    # The seam is intact: a good line after the poison still dispatches.
    good = (
        json.dumps(
            {"$deck": "report", "report": {"nodeid": "after", "when": "call"}}
        ).encode()
        + b"\n"
    )
    run._dispatch_fd3(good)
    assert [e.name for e in stub.events] == ["report"]
    assert stub.events[0].data["nodeid"] == "after"
    assert run._saw_report is True


# === unit: the collect-path sibling =========================================


def test_iter_payloads_skips_poison_lines():
    raw = "\n".join(
        [
            DEEP_NESTED.decode("ascii"),
            "42",
            "[1, 2]",
            "not json at all",
            json.dumps({"$deck": "collection", "items": []}),
        ]
    )
    assert [obj["$deck"] for obj in _iter_payloads(raw)] == ["collection"]


# === end to end: run, all reports survive a mid-run poison line =============


@pytest.fixture
def poison_suite(tmp_path):
    """First test writes all three poison classes to the fd mid-run; three
    normal tests follow whose reports prove the reader survived."""
    (tmp_path / "test_mod.py").write_text(
        "import os\n"
        "def test_aaa_poison():\n"
        "    fd = int(os.environ['PYTEST_DECK_FD'])\n"
        "    os.write(fd, b'[' * 60000 + b']' * 60000 + b'\\n')\n"
        '    os.write(fd, b\'{"a": "\\xff\\xfe"}\\n\')\n'
        "    os.write(fd, b'42\\n')\n"
        "def test_bbb():\n    assert True\n"
        "def test_ccc():\n    assert True\n"
        "def test_ddd():\n    assert True\n"
    )
    return tmp_path


def test_run_survives_poison_lines_all_reports_arrive(poison_suite):
    async def body():
        mgr = RunManager(poison_suite)
        try:
            q = mgr.subscribe()
            await mgr.start(
                [
                    "test_mod.py::test_aaa_poison",
                    "test_mod.py::test_bbb",
                    "test_mod.py::test_ccc",
                    "test_mod.py::test_ddd",
                ]
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            reports = [d for n, d in events if n == "report"]
            # All 12 reports (4 tests x setup/call/teardown). Before the fix the
            # RecursionError killed the reader after test_aaa's setup and only
            # one arrived.
            assert len(reports) == 12, names
            assert {r["nodeid"] for r in reports} == {
                "test_mod.py::test_aaa_poison",
                "test_mod.py::test_bbb",
                "test_mod.py::test_ccc",
                "test_mod.py::test_ddd",
            }
            # Poison is skipped silently: no error events (unlike OVERRUN), and
            # the run finishes normally.
            assert "error" not in names
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    asyncio.run(body())


# === end to end: collect, poison during collection still parses =============


def test_collect_survives_poison_lines(tmp_path):
    # The conftest import runs during collection and shares the collect fd; a
    # poison line there used to blow up _iter_payloads and sink the whole parse.
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "fd = int(os.environ['PYTEST_DECK_FD'])\n"
        "os.write(fd, b'[' * 60000 + b']' * 60000 + b'\\n')\n"
        "os.write(fd, b'42\\n')\n"
    )
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")
    result = collect(tmp_path)
    assert [item["nodeid"] for item in result["items"]] == ["test_quick.py::test_ok"]
    assert result["errors"] == []
