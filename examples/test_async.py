"""pytest-asyncio — a structural plugin switch for ``async def`` tests.

Enable the "Async tests (pytest-asyncio)" switch in the left bar and these
run green. Needs ``pip install pytest-asyncio``; the switch is hidden until
it's installed.

The demo is the run WITHOUT it: bare pytest cannot execute coroutine tests,
so with the switch off these FAIL with "async def functions are not natively
supported" — flip the switch and re-run to watch them recover.

Each test carries an explicit ``@pytest.mark.asyncio`` (pytest-asyncio's
default strict mode) rather than an ini ``asyncio_mode = auto``, so the file
collects cleanly whether or not the plugin is loaded. The ``asyncio`` mark is
registered in pytest.ini for the same reason.
"""

import asyncio

import pytest


async def fetch(key, delay=0.001):
    await asyncio.sleep(delay)
    return {"status": "ok", "key": key}


@pytest.mark.asyncio
async def test_fetch_returns_ok():
    response = await fetch("users")
    assert response == {"status": "ok", "key": "users"}


@pytest.mark.asyncio
async def test_concurrent_fetches():
    responses = await asyncio.gather(*(fetch(k) for k in ("a", "b", "c")))
    assert [r["key"] for r in responses] == ["a", "b", "c"]
