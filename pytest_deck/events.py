"""SSE event fan-out unit: one Event type + the per-client Subscriber buffer.

Split backpressure: results and run-control events are unbounded (SSE is
the sole results channel; a lost report strands a test at ``incomplete``), only
``console`` is bounded, drop-oldest. Unbounded classes:
``report``/``warning``/``plugin_data``/``plugin_empty``/``finished``/
``cancelled``/``error``/``started``. A plain bounded ``asyncio.Queue`` cannot
express this split; do not "simplify" back to one.
"""

import asyncio
import collections
from dataclasses import dataclass

# Per-subscriber cap on buffered console events only.
CONSOLE_MAXLEN = 1000


# eq=False keeps identity eq/hash: value equality would make Event unhashable
# because of the dict field.
@dataclass(frozen=True, slots=True, eq=False)
class Event:
    """A single SSE event: a name plus a JSON-serializable data dict."""

    name: str
    data: dict


class Subscriber:
    """One SSE client's event buffer with split backpressure."""

    def __init__(self):
        """Create an empty, open buffer."""
        self._items = collections.deque()  # ordered (Event | None EOF sentinel)
        self._console_count = 0
        self._waiter = asyncio.Event()
        self._closed = False

    def put(self, event):
        """Enqueue an event, bounding only console; report-class is unbounded."""
        if event.name == "console":
            if self._console_count >= CONSOLE_MAXLEN:
                # Drop the oldest console chunk; results are left untouched.
                for i, item in enumerate(self._items):
                    if item is not None and item.name == "console":
                        del self._items[i]
                        self._console_count -= 1
                        break
            self._console_count += 1
        self._items.append(event)
        self._waiter.set()

    def close(self):
        """Signal end-of-stream to the SSE generator."""
        self._closed = True
        self._items.append(None)
        self._waiter.set()

    async def get(self):
        """Await the next event (or None once closed and drained).

        Single event loop, but ``get`` can be cancelled mid-wait (the SSE reader
        wraps it in ``wait_for``); the clear/recheck below avoids a lost wakeup
        where a ``put`` lands between observing the empty deque and waiting.
        """
        while True:
            if self._items:
                item = self._items.popleft()
                if item is not None and item.name == "console":
                    self._console_count -= 1
                return item
            if self._closed:
                return None
            # Arm the waiter, then re-check: if a put slipped in after the empty
            # check above, the deque is now non-empty and we loop without waiting;
            # otherwise the next put will set() the (now-cleared) waiter.
            self._waiter.clear()
            if self._items or self._closed:
                continue
            await self._waiter.wait()
