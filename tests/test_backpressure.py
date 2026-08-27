"""Regression tests for the split-backpressure subscriber.

* report-class events (``report``/``warning``/``plugin_data``/``plugin_empty``/
  ``finished``/``cancelled``/``error``/``started``) → **unbounded** deque, NEVER
  dropped, delivered in order, and never touching the console budget.
* ``console`` → the only bounded class, capped at ``CONSOLE_MAXLEN``,
  drop-oldest-console on overflow.

Unit-level: they drive ``RunManager.broadcast`` / ``events.Subscriber`` directly
(no subprocess). ``Subscriber.get`` needs a running loop; driven via ``run_async``.
"""

import asyncio

from pytest_deck.events import CONSOLE_MAXLEN, Event
from pytest_deck.runner import RunManager


def run_async(coro):
    return asyncio.run(coro)


async def drain_all(sub):
    """Pull every buffered event out of a subscriber without blocking.

    The subscriber isn't closed, so we can't await ``get()`` to completion; pull
    exactly what's currently buffered by reading until the internal deque empties.
    """
    out = []
    while sub._items:
        ev = await sub.get()
        out.append(ev)
    return out


# --- #1a: reports past the old 1000 cap are not dropped --------------------


def test_reports_beyond_old_cap_all_survive_in_order():
    async def body():
        mgr = RunManager("/tmp")
        sub = mgr.subscribe()

        # Far more than the old 1000-item cap, sent to a subscriber that isn't
        # draining. Every one must survive: the report class is unbounded.
        n = 1005
        for i in range(n):
            mgr.broadcast(Event("report", {"run_id": "run-1", "seq": i}))

        events = await drain_all(sub)

        assert len(events) == n, f"expected {n} reports, got {len(events)}"
        # All are reports, strictly in broadcast order: none dropped or reordered.
        assert all(e.name == "report" for e in events)
        assert [e.data["seq"] for e in events] == list(range(n))

    run_async(body())


REPORT_CLASS_NAMES = [
    "report",
    "warning",
    "plugin_data",
    "plugin_empty",
    "finished",
    "cancelled",
    "error",
    "started",
]


def test_all_report_class_events_are_unbounded():
    async def body():
        # Each class is flooded on its own past the console cap. Cycling the
        # names would leave each kind below the cap, so a misclassification
        # would slip through; one name at a time makes every kind load-bearing.
        total = CONSOLE_MAXLEN + 500
        for name in REPORT_CLASS_NAMES:
            mgr = RunManager("/tmp")
            sub = mgr.subscribe()
            for i in range(total):
                mgr.broadcast(Event(name, {"seq": i}))
            events = await drain_all(sub)
            assert len(events) == total, f"{name}: {len(events)} survived, want {total}"
            assert [e.data["seq"] for e in events] == list(range(total)), name

    run_async(body())


def test_report_class_events_never_touch_the_console_budget():
    # The bound and the drop-victim search are separate conditions (events.py):
    # a kind wrongly counted toward the console budget wouldn't be found as a
    # drop victim (only `console` is), so flooding it alone drops nothing and a
    # size check passes anyway. Guard the real invariant instead: a report-class
    # event must never move `_console_count`. One console chunk is interleaved
    # so a miscounted kind would perturb the budget (and could evict the console).
    async def body():
        for name in REPORT_CLASS_NAMES:
            mgr = RunManager("/tmp")
            sub = mgr.subscribe()
            sub.put(Event("console", {"chunk": "x"}))
            before = sub._console_count
            for i in range(CONSOLE_MAXLEN + 500):
                sub.put(Event(name, {"seq": i}))
            assert sub._console_count == before, (
                f"{name} altered the console budget "
                f"({before} → {sub._console_count})"
            )
            # The lone console chunk must still be buffered, not evicted by a
            # miscounted report-class flood.
            assert (
                sum(1 for e in sub._items if e is not None and e.name == "console") == 1
            ), f"{name} flood evicted the console chunk"

    run_async(body())


# --- #1b: console is bounded; interleaved reports still all survive --------


def test_console_bounded_but_interleaved_reports_all_survive():
    async def body():
        mgr = RunManager("/tmp")
        sub = mgr.subscribe()

        n_console = 2000
        n_reports = 50
        # Interleave so reports are spread across the whole stream; a report at
        # the very start would be lost if drop-oldest ever touched a report.
        step = n_console // n_reports  # a report every `step` consoles
        report_seq = 0
        for i in range(n_console):
            mgr.broadcast(Event("console", {"text": f"c{i}"}))
            if i % step == 0:
                mgr.broadcast(Event("report", {"run_id": "run-1", "seq": report_seq}))
                report_seq += 1
        # Add any remaining reports to reach exactly n_reports.
        while report_seq < n_reports:
            mgr.broadcast(Event("report", {"run_id": "run-1", "seq": report_seq}))
            report_seq += 1

        events = await drain_all(sub)
        reports = [e for e in events if e.name == "report"]
        consoles = [e for e in events if e.name == "console"]

        # Every report survived, in order: zero dropped despite the console flood.
        assert len(reports) == n_reports, len(reports)
        assert [e.data["seq"] for e in reports] == list(range(n_reports))

        # Console is bounded to the cap (drop-oldest-console on overflow).
        assert len(consoles) == CONSOLE_MAXLEN, len(consoles)
        # The survivors are the newest console chunks (the oldest were dropped).
        survivor_texts = [e.data["text"] for e in consoles]
        assert survivor_texts[-1] == f"c{n_console - 1}"
        assert "c0" not in survivor_texts  # the oldest was dropped

    run_async(body())


# --- lost-wakeup regression (the third bug the fix addressed) -------------


def test_get_wakes_on_put_arriving_while_parked():
    """A ``get()`` parked on the empty-buffer waiter must wake on the next put.

    The fixed ``Subscriber.get`` arms the waiter then re-checks the deque to
    avoid a lost wakeup. This drives the real timing: start ``get()`` (it parks
    on ``_waiter.wait()`` because the buffer is empty), then ``put`` from a later
    scheduled task — ``get`` must return that event, not hang.
    """

    async def body():
        mgr = RunManager("/tmp")
        sub = mgr.subscribe()

        async def delayed_put():
            await asyncio.sleep(0.05)  # ensure get() is already parked
            mgr.broadcast(Event("report", {"run_id": "run-1", "seq": 0}))

        producer = asyncio.create_task(delayed_put())
        # If the wakeup were lost, this wait_for would time out and raise.
        ev = await asyncio.wait_for(sub.get(), timeout=5.0)
        await producer
        assert ev.name == "report"
        assert ev.data["seq"] == 0

    run_async(body())


def test_get_returns_none_after_close_when_drained():
    """``get()`` returns the buffered events, then ``None`` once closed+drained."""

    async def body():
        mgr = RunManager("/tmp")
        sub = mgr.subscribe()
        mgr.broadcast(Event("report", {"seq": 0}))
        mgr.broadcast(Event("report", {"seq": 1}))
        mgr.unsubscribe(sub)  # closes the subscriber (appends EOF sentinel)

        a = await sub.get()
        b = await sub.get()
        end = await sub.get()
        assert a.data["seq"] == 0
        assert b.data["seq"] == 1
        assert end is None  # end-of-stream sentinel

    run_async(body())


def test_console_count_decrements_on_get_so_cap_is_per_buffer():
    """Draining console frees room — the cap bounds *buffered* console, not total.

    A client that keeps up should receive every console event; the cap only kicks
    in when console accumulates faster than the client drains it.
    """

    async def body():
        mgr = RunManager("/tmp")
        sub = mgr.subscribe()

        received = 0
        for i in range(CONSOLE_MAXLEN + 200):
            mgr.broadcast(Event("console", {"text": f"c{i}"}))
            # Drain promptly so the buffer never hits the cap.
            ev = await sub.get()
            assert ev.name == "console"
            received += 1

        assert received == CONSOLE_MAXLEN + 200  # nothing dropped when kept up
        assert not sub._items  # fully drained

    run_async(body())
