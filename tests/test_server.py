"""Tests for the FastAPI app (``pytest_deck.server.create_app``).

Two transports, deliberately:

* **httpx ASGITransport** for the non-streaming endpoints (``/api/collect``,
  ``/api/run`` → 202, ``/api/cancel``). In-process, fast, no socket.
* **a real uvicorn server on a random TCP port** for the SSE tests
  (``/api/events``). The implementer found httpx's ASGITransport *starves* a
  long-lived SSE stream when a second request hits the same client — a
  test-harness artifact, not a server bug — so the streaming assertions run the
  app exactly as it ships, over real TCP (the ``probe_uvicorn.py`` pattern).

``pytest-asyncio`` is not installed; async bodies are driven with ``asyncio.run``.
"""

import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

from pytest_deck.server import _bind, _display_url, _read_json, create_app, main, serve

# --- fixture suite --------------------------------------------------------


@pytest.fixture
def suite(tmp_path):
    """A small suite with a marker and a parametrized test for tree assertions."""
    (tmp_path / "test_suite.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.smoke\n"
        "def test_a():\n"
        "    assert True\n"
        "\n"
        "def test_b():\n"
        "    assert True\n"
        "\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_p(n):\n"
        "    assert n > 0\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    smoke: smoke test\n")
    return tmp_path


@pytest.fixture
def slow_suite(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        "import time\n"
        "\n"
        "def test_slow_1():\n"
        "    time.sleep(2.0)\n"
        "\n"
        "def test_slow_2():\n"
        "    time.sleep(2.0)\n"
    )
    return tmp_path


def run_async(coro):
    return asyncio.run(coro)


# --- async driver helpers -------------------------------------------------


def asgi_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# === /api/collect (ASGI is fine: synchronous one-shot) =====================


def test_collect_returns_tree_shape(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.get("/api/collect")
            assert r.status_code == 200
            data = r.json()

            # The contract shape: markers, tree, total and rootdir (§4 + tree.py).
            assert set(data) >= {"markers", "tree", "total", "rootdir"}
            assert data["rootdir"] == str(suite)
            # 2 plain tests + 2 parametrized variants = 4 collected items.
            assert data["total"] == 4
            # The smoke marker surfaces as a filter chip (parametrize excluded).
            assert "smoke" in data["markers"]
            assert "parametrize" not in data["markers"]

            # The tree is a forest; the test file is a top-level node.
            top_names = [node["name"] for node in data["tree"]]
            assert "test_suite.py" in top_names

            # Parametrized variants fold under their base test as leaf nodes.
            leaves = _leaf_nodeids(data["tree"])
            assert "test_suite.py::test_a" in leaves
            assert "test_suite.py::test_p[1]" in leaves
            assert "test_suite.py::test_p[2]" in leaves

    run_async(body())


def test_collect_with_targets_narrows(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.get(
                "/api/collect", params={"targets": "test_suite.py::test_a"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["total"] == 1
            assert _leaf_nodeids(data["tree"]) == ["test_suite.py::test_a"]

    run_async(body())


def test_initial_target_scopes_default_collection(tmp_path):
    # ``--deck PATH`` with a subdirectory PATH still roots at ``tmp_path``, but
    # it scopes the default (no ``targets``) collection to that subtree, just
    # like ``pytest PATH`` would. Two files at different depths prove the
    # scoping bites.
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "test_top.py").write_text("def test_top():\n    assert True\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "test_sub.py").write_text("def test_sub():\n    assert True\n")

    async def body():
        app = create_app(tmp_path, initial_target=str(sub))
        async with asgi_client(app) as client:
            # Default collection: only the subtree.
            r = await client.get("/api/collect")
            assert r.json()["rootdir"] == str(tmp_path)
            assert _leaf_nodeids(r.json()["tree"]) == ["sub/test_sub.py::test_sub"]
            # An explicit targets query still overrides the launch-time scope.
            r2 = await client.get("/api/collect", params={"targets": "test_top.py"})
            assert _leaf_nodeids(r2.json()["tree"]) == ["test_top.py::test_top"]

    run_async(body())


def test_bare_deck_default_collect_honors_testpaths(tmp_path):
    # With a bare ``--deck`` (initial_target=None) the default collect passes no
    # positional at all, so pytest applies ``testpaths`` and the deck collects
    # exactly what the user's terminal ``pytest`` collects (the dogfooding fix).
    # The ``extra/`` dir outside testpaths must not appear.
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_in.py").write_text("def test_in():\n    assert True\n")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "test_out.py").write_text("def test_out():\n    assert True\n")

    async def body():
        app = create_app(tmp_path)  # no initial_target = bare --deck
        async with asgi_client(app) as client:
            r = await client.get("/api/collect")
            assert _leaf_nodeids(r.json()["tree"]) == ["tests/test_in.py::test_in"]

    run_async(body())


# === /api/run + /api/cancel (ASGI is fine: request/response) ===============


def test_run_returns_202_with_run_id(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.post(
                "/api/run", json={"nodeids": ["test_suite.py::test_a"]}
            )
            assert r.status_code == 202
            run_id = r.json()["run_id"]
            assert run_id == "run-1"  # monotonic counter (§9 q8)
            # Let the run finish so teardown is clean.
            await app.state.manager.shutdown()

    run_async(body())


def test_cancel_idle_returns_false(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.post("/api/cancel", json={})
            assert r.status_code == 200
            assert r.json() == {"cancelled": False, "run_id": None}

    run_async(body())


def test_cancel_during_run_returns_true(slow_suite):
    async def body():
        app = create_app(slow_suite)
        async with asgi_client(app) as client:
            r = await client.post(
                "/api/run", json={"nodeids": ["test_slow.py::test_slow_1"]}
            )
            run_id = r.json()["run_id"]
            # Give the subprocess a moment to actually be running.
            await asyncio.sleep(0.3)
            r = await client.post("/api/cancel", json={})
            assert r.status_code == 200
            body_json = r.json()
            assert body_json["cancelled"] is True
            assert body_json["run_id"] == run_id

    run_async(body())


def test_run_active_idle_returns_false(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.get("/api/run/active")
            assert r.status_code == 200
            assert r.json() == {"active": False}

    run_async(body())


def test_run_active_during_run_returns_true(slow_suite):
    # The reconnect-resync signal (results.svelte.js onopen): a client that
    # missed the `finished` event during an SSE gap polls this to decide whether
    # to unstick a zombie run. It has to report True while the subprocess is
    # alive and False once it is done.
    async def body():
        app = create_app(slow_suite)
        async with asgi_client(app) as client:
            await client.post(
                "/api/run", json={"nodeids": ["test_slow.py::test_slow_1"]}
            )
            await asyncio.sleep(0.3)  # let the subprocess actually start
            r = await client.get("/api/run/active")
            assert r.json() == {"active": True}
            # Cancel it and confirm it flips to inactive.
            await client.post("/api/cancel", json={})
            r = await client.get("/api/run/active")
            assert r.json() == {"active": False}

    run_async(body())


@pytest.mark.parametrize(
    "payload",
    [
        {"nodeids": "test_a.py::test_x"},  # not a list
        {"nodeids": [["deep"]]},  # non-string element
        {"nodeids": [], "k": 123},
        {"nodeids": [], "m": ["slow"]},
    ],
)
def test_run_bad_body_shape_400_and_no_run_started(suite, payload):
    # json.loads admits far deeper (and weirder) bodies on 3.14 than on 3.13.
    # Whatever parses has to be validated down to a clean 400: never a 500
    # inside a handler, never a value riding into argv or the `started` echo,
    # and never a live run superseded by garbage.
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            r = await client.post("/api/run", json=payload)
            assert r.status_code == 400
            assert "error" in r.json()
            assert not app.state.manager.is_active()

    run_async(body())


def test_run_deep_body_never_500(suite):
    # A raw 20k-deep valid-JSON body. On 3.13 json.loads refuses it, which lands
    # on the empty-body "run all" path (202); on 3.14 it parses and the shape
    # validation answers 400. Either way the invariant is the same: no 500, and
    # the server keeps answering afterwards.
    async def body():
        app = create_app(suite)
        depth = 20000
        raw = '{"k": ' + "[" * depth + "]" * depth + "}"
        async with asgi_client(app) as client:
            r = await client.post(
                "/api/run",
                content=raw,
                headers={"content-type": "application/json"},
            )
            assert r.status_code in (202, 400)
            r2 = await client.get("/api/run/active")
            assert r2.status_code == 200
        await app.state.manager.shutdown()

    run_async(body())


# === / (the built dashboard) ==============================================


def test_index_serves_the_dashboard_html(suite):
    async def body():
        app = create_app(suite)
        async with asgi_client(app) as client:
            for path in ("/", "/index.html"):
                r = await client.get(path)
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/html")

    run_async(body())


# === _read_json (request-body tolerance) ===================================


class _RawRequest:
    """Just enough of a Request for ``_read_json``: an async ``body()``."""

    def __init__(self, raw):
        self._raw = raw

    async def body(self):
        return self._raw


def test_read_json_tolerates_empty_invalid_and_non_dict_bodies():
    # POST /api/run with an empty or garbage body means "run all", never a 500:
    # every malformed shape collapses to {}.
    async def body():
        assert await _read_json(_RawRequest(b"")) == {}
        assert await _read_json(_RawRequest(b"{not json")) == {}
        assert await _read_json(_RawRequest(b"[1, 2]")) == {}  # valid, not a dict
        assert await _read_json(_RawRequest(b'"a string"')) == {}
        assert await _read_json(_RawRequest(b'{"k": "smoke"}')) == {"k": "smoke"}
        # A pathologically deep body must never raise out of the helper. On
        # 3.13 json.loads refuses it (the RecursionError becomes {}); on 3.14 it
        # parses and api_run's shape validation takes over. The contract here
        # is simply a dict and no exception.
        deep = 20000
        raw = ('{"k": ' + "[" * deep + "]" * deep + "}").encode()
        assert isinstance(await _read_json(_RawRequest(raw)), dict)

    run_async(body())


# === serve() + main() (CLI entry, uvicorn/webbrowser stubbed) ==============


@pytest.fixture
def serve_stubs(monkeypatch):
    """Stub ``uvicorn.Server.run`` and ``webbrowser.open``; capture the calls.

    ``serve`` binds the socket ITSELF and hands it to ``Server.run(sockets=…)``
    (so uvicorn can never hit EADDRINUSE), hence the stub sits on ``Server.run``
    — the bind is real, only the event loop is stubbed out. The bound address is
    recorded at call time (``serve`` closes the socket after ``run`` returns).
    """
    uvicorn_calls = []
    opened = []

    def fake_run(self, sockets=None):
        uvicorn_calls.append((self.config, [s.getsockname() for s in sockets]))

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    return uvicorn_calls, opened


def free_port(host="127.0.0.1"):
    """An ephemeral port that is free right now (bind-0, read, close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@contextmanager
def occupied_port(host="127.0.0.1"):
    """Hold a plain listening socket open; yield its port (the "other deck")."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        s.listen(1)
        yield s.getsockname()[1]


def test_serve_prints_url_and_does_not_open_browser_by_default(
    suite, serve_stubs, capsys
):
    uvicorn_calls, opened = serve_stubs
    port = free_port()
    serve(suite, host="127.0.0.1", port=port)

    # The URL is printed for the user to open once; no auto-open on restart.
    out = capsys.readouterr().out
    assert str(suite.resolve()) in out
    assert f"http://127.0.0.1:{port}/" in out
    assert opened == []

    # An explicit free port is bound exactly, and handed to uvicorn pre-bound.
    assert len(uvicorn_calls) == 1
    config, socknames = uvicorn_calls[0]
    assert config.host == "127.0.0.1"
    assert config.port == port
    assert socknames == [("127.0.0.1", port)]


def test_serve_opens_browser_when_asked(suite, serve_stubs, capsys):
    uvicorn_calls, opened = serve_stubs
    port = free_port()
    serve(suite, host="localhost", port=port, open_browser=True)
    capsys.readouterr()  # drain the printed banner

    assert opened == [f"http://localhost:{port}/"]
    config, _ = uvicorn_calls[0]
    assert config.host == "localhost"
    assert config.port == port


# === port policy (explicit = bind-or-fail-loud; auto = fall forward) ========


def test_serve_explicit_busy_port_fails_loud_no_traceback(suite, serve_stubs):
    # The pin: an occupied explicit port exits nonzero with the friendly
    # one-liner, never uvicorn's raw errno-98 ERROR line (uvicorn never runs).
    uvicorn_calls, _ = serve_stubs
    with occupied_port() as port:
        with pytest.raises(SystemExit) as exc:
            serve(suite, host="127.0.0.1", port=port, port_flag="--deck-port")
    msg = str(exc.value)
    assert msg == (
        f"pytest-deck: port {port} is already in use (another deck?). "
        f"Stop it or pass a different --deck-port"
    )
    assert exc.value.code == msg  # a string code means stderr message + exit 1
    assert uvicorn_calls == []


@pytest.mark.parametrize("bad", [70000, -1])
def test_serve_explicit_out_of_range_port_fails_loud(suite, serve_stubs, bad):
    # The pin: CPython raises OverflowError (not OSError) from bind() on an
    # out-of-range port. Without the up-front range check that escaped as a
    # raw traceback (pytest INTERNALERROR) instead of the friendly exit.
    uvicorn_calls, _ = serve_stubs
    with pytest.raises(SystemExit) as exc:
        serve(suite, host="127.0.0.1", port=bad, port_flag="--deck-port")
    msg = str(exc.value)
    assert msg == f"pytest-deck: invalid port {bad} (--deck-port must be 0-65535)"
    assert exc.value.code == msg  # a string code means stderr message + exit 1
    assert uvicorn_calls == []


def test_serve_explicit_port_zero_announces_the_real_port(suite, serve_stubs, capsys):
    # The pin: port 0 means "bind an ephemeral port", so the banner, the --open
    # tab and uvicorn's config must all carry the real bound port (getsockname),
    # never the literal 0.
    uvicorn_calls, opened = serve_stubs
    serve(suite, host="127.0.0.1", port=0, open_browser=True)

    out = capsys.readouterr().out
    config, socknames = uvicorn_calls[0]
    bound = config.port
    assert bound != 0
    assert socknames == [("127.0.0.1", bound)]  # announced == getsockname
    assert f"http://127.0.0.1:{bound}/" in out
    assert opened == [f"http://127.0.0.1:{bound}/"]


def test_serve_auto_uses_default_port_when_free(suite, serve_stubs, capsys):
    uvicorn_calls, _ = serve_stubs
    base = free_port()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("pytest_deck.server._DEFAULT_PORT", base)
        serve(suite, port=None)

    out = capsys.readouterr().out
    assert f"http://127.0.0.1:{base}/" in out
    assert "in use" not in out  # no fallback announcement on the happy path
    config, socknames = uvicorn_calls[0]
    assert config.port == base and socknames == [("127.0.0.1", base)]


def test_serve_auto_falls_forward_and_announces_actual_port(suite, serve_stubs, capsys):
    # The pin: the default path skips a busy default port, announces the
    # fallback, and the printed URL and --open tab carry the actual bound port.
    uvicorn_calls, opened = serve_stubs
    with occupied_port() as base:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("pytest_deck.server._DEFAULT_PORT", base)
            serve(suite, port=None, open_browser=True)

    out = capsys.readouterr().out
    config, socknames = uvicorn_calls[0]
    bound = config.port
    assert bound != base
    assert socknames == [("127.0.0.1", bound)]
    assert f"port {base} in use → serving on {bound}" in out
    assert f"http://127.0.0.1:{bound}/" in out
    assert opened == [f"http://127.0.0.1:{bound}/"]


def test_serve_auto_range_exhaustion_fails_loud(suite, serve_stubs):
    # The pin: the scan is bounded. With every candidate busy we get the
    # friendly failure, not an infinite (or unbounded) walk up the port space.
    uvicorn_calls, _ = serve_stubs
    with occupied_port() as p1:
        # Occupy p1+1 as well; the scan range is monkeypatched to exactly these
        # two candidates. If the OS already handed p1+1 to someone else, skip.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
            try:
                s2.bind(("127.0.0.1", p1 + 1))
            except OSError:
                pytest.skip("adjacent port unavailable; ephemeral collision")
            s2.listen(1)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("pytest_deck.server._DEFAULT_PORT", p1)
                mp.setattr("pytest_deck.server._PORT_ATTEMPTS", 2)
                with pytest.raises(SystemExit) as exc:
                    serve(suite, port=None)
    assert str(exc.value) == (
        f"pytest-deck: ports {p1}-{p1 + 1} are all in use. "
        f"Stop a deck or pass a specific --port"
    )
    assert uvicorn_calls == []


def test_bind_listens_so_a_concurrent_bind_is_refused():
    # The pin for the SO_REUSEADDR fall-forward race: two SO_REUSEADDR sockets
    # may both bind() the same port while neither listens, which defers the
    # loser's EADDRINUSE to listen() inside uvicorn (a raw traceback). _bind
    # listens before returning, so a concurrent _bind on the same port fails at
    # its own bind() and falls forward (None), inside our catch.
    sock = _bind("127.0.0.1", 0)
    try:
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        port = sock.getsockname()[1]
        assert _bind("127.0.0.1", port) is None
    finally:
        sock.close()


def test_display_url_brackets_ipv6_literals():
    # --host ::1 must print http://[::1]:PORT/ (RFC 3986), never ::1:PORT.
    assert _display_url("127.0.0.1", 8765) == "http://127.0.0.1:8765/"
    assert _display_url("localhost", 8080) == "http://localhost:8080/"
    assert _display_url("::1", 8080) == "http://[::1]:8080/"


def test_serve_closes_socket_when_startup_fails_after_bind(
    suite, serve_stubs, monkeypatch
):
    # The pin: an exception between the bind and uvicorn (create_app here) must
    # not leak the listening socket; the port frees immediately.
    def boom(rootdir, initial_target=None):
        raise RuntimeError("startup failed")

    monkeypatch.setattr("pytest_deck.server.create_app", boom)
    port = free_port()
    with pytest.raises(RuntimeError, match="startup failed"):
        serve(suite, host="127.0.0.1", port=port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


# === Ctrl-C shutdown (the docstring's "exits cleanly" promise) ==============


def test_serve_swallows_uvicorn_ctrl_c_reraise(suite, monkeypatch, capsys):
    # The pin: uvicorn's capture_signals re-raises the captured SIGINT after the
    # graceful shutdown, so KeyboardInterrupt escapes Server.run (the high-level
    # uvicorn.run swallows it; Server.run does not). serve() has to return
    # normally and quietly, like the alpha-era uvicorn.run path did, and the
    # finally still has to release the socket.
    def fake_run(self, sockets=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    port = free_port()
    serve(suite, host="127.0.0.1", port=port)  # must not raise

    out = capsys.readouterr().out
    assert f"http://127.0.0.1:{port}/" in out
    # The socket was closed on the way out: the port binds again immediately.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_ctrl_c_with_live_sse_client_exits_quietly(suite):
    """Real-process pin for the whole Ctrl-C path (needs signals → subprocess).

    SIGINT with an open /api/events stream must exit 0 with NO traceback and NO
    "ASGI callable returned without completing response" — the stream's
    shutdown_event makes the generator RETURN (response completes) before
    sse_starlette's grace period force-cancels it mid-send.
    """
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "pytest_deck.server", str(suite), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # keep the SIGINT off pytest's process group
    )
    sse = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                sse = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        assert sse is not None, "server never started listening"

        # A raw, live SSE client: request the stream and read the headers so
        # the response is mid-flight when the signal lands.
        sse.sendall(
            b"GET /api/events HTTP/1.1\r\nHost: deck\r\n"
            b"Accept: text/event-stream\r\n\r\n"
        )
        sse.settimeout(10)
        assert b"200" in sse.recv(4096)

        os.kill(server.pid, signal.SIGINT)
        out, _ = server.communicate(timeout=30)
    finally:
        if sse is not None:
            sse.close()
        if server.poll() is None:  # pragma: no cover - only on test failure
            server.kill()
            server.communicate()

    assert server.returncode == 0, out
    assert "Traceback" not in out, out
    assert "KeyboardInterrupt" not in out, out
    assert "ASGI callable" not in out, out


def test_startup_banner_reaches_a_piped_stdout_while_serving(suite):
    """Real-process pin: the URL must be visible while the server is running.

    With stdout on a pipe (a log file, ``tee``, an IDE task runner) Python
    block-buffers ``print``; the server runs until Ctrl-C, so an unflushed
    banner would surface only at exit, i.e. never. Read the pipe while the
    server is live and demand the URL line there.
    """
    port = free_port()
    # PYTHONUNBUFFERED (set by container CI images, and by the deck's own
    # build_env when this suite is dogfooded) would make stdout unbuffered and
    # let the pin pass without the flush; strip it so the test bites.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    server = subprocess.Popen(
        [sys.executable, "-m", "pytest_deck.server", str(suite), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    seen = b""
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:  # pragma: no cover - only on test failure
            pytest.fail("server never started listening")

        # Read whatever the running server has already written; a buffered
        # banner would leave the pipe empty until exit and time this out.
        deadline = time.time() + 10
        while b"Ctrl-C to stop" not in seen and time.time() < deadline:
            ready, _, _ = select.select([server.stdout], [], [], 1)
            if ready:
                chunk = os.read(server.stdout.fileno(), 4096)
                if not chunk:  # pragma: no cover - server died
                    break
                seen += chunk
    finally:
        os.kill(server.pid, signal.SIGINT)
        try:
            server.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on failure
            server.kill()
            server.communicate()

    text = seen.decode("utf-8", "replace")
    assert f"open http://127.0.0.1:{port}/" in text, text
    assert "pytest-deck serving" in text, text


def test_main_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pytest_deck.server.serve",
        lambda rootdir, host, port, open_browser: calls.append(
            (rootdir, host, port, open_browser)
        ),
    )
    main([])
    # The documented defaults: rootdir ".", localhost, port None (auto
    # fall-forward from 8765), browser off.
    assert calls == [(".", "127.0.0.1", None, False)]


def test_main_parses_rootdir_host_port_and_open(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pytest_deck.server.serve",
        lambda rootdir, host, port, open_browser: calls.append(
            (rootdir, host, port, open_browser)
        ),
    )
    main(["myproj", "--host", "0.0.0.0", "--port", "9000", "--open"])
    assert calls == [("myproj", "0.0.0.0", 9000, True)]


# === /api/events (real uvicorn over TCP, SSE streaming) ===================


def test_events_stream_named_events_for_a_run(suite):
    async def body():
        async with running_server(suite) as base:
            # Sanity: collect works over real TCP too.
            async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                r = await c.get("/api/collect")
                assert r.status_code == 200 and r.json()["total"] == 4

            ev = []
            stop = asyncio.Event()
            reader = asyncio.create_task(sse_reader(base, ev, stop))
            await asyncio.sleep(0.5)  # let the SSE stream connect to uvicorn

            async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                r = await c.post(
                    "/api/run",
                    json={
                        "nodeids": [
                            "test_suite.py::test_a",
                            "test_suite.py::test_b",
                        ]
                    },
                )
                assert r.status_code == 202
                run_id = r.json()["run_id"]

            await asyncio.wait_for(reader, timeout=30)

            ns = [n for n, _ in ev]
            # The named SSE events for a run arrive in order.
            assert ns[0] == "started"
            assert "report" in ns
            assert "console" in ns
            assert "finished" in ns
            # 2 tests x 3 phases = 6 report events.
            assert ns.count("report") == 6, ns
            # Every event is tagged with the run_id.
            assert all(d.get("run_id") == run_id for _, d in ev if isinstance(d, dict))
            finished = next(d for n, d in ev if n == "finished")
            assert finished["exit_code"] == 0

    run_async(body())


def test_events_fan_out_to_two_subscribers(suite):
    async def body():
        async with running_server(suite) as base:
            ev1, ev2 = [], []
            stop = asyncio.Event()
            t1 = asyncio.create_task(sse_reader(base, ev1, stop))
            t2 = asyncio.create_task(sse_reader(base, ev2, stop))
            await asyncio.sleep(0.5)  # let both streams connect

            async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                r = await c.post(
                    "/api/run", json={"nodeids": ["test_suite.py::test_a"]}
                )
                run_id = r.json()["run_id"]

            await asyncio.wait_for(asyncio.gather(t1, t2), timeout=30)

            # Both subscribers saw the same run's started + finished + reports.
            for ev in (ev1, ev2):
                ns = [n for n, _ in ev]
                assert "started" in ns
                assert "finished" in ns
                assert ns.count("report") == 3  # 1 test x 3 phases
                assert any(
                    d.get("run_id") == run_id for _, d in ev if isinstance(d, dict)
                )

    run_async(body())


def test_events_emit_cancelled_for_a_live_cancel(slow_suite):
    async def body():
        async with running_server(slow_suite) as base:
            ev = []
            stop = asyncio.Event()
            reader = asyncio.create_task(
                sse_reader(base, ev, stop, stop_on="cancelled")
            )
            await asyncio.sleep(0.5)

            async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                r = await c.post(
                    "/api/run", json={"nodeids": ["test_slow.py::test_slow_1"]}
                )
                run_id = r.json()["run_id"]
                await asyncio.sleep(0.4)  # let the run actually start
                r = await c.post("/api/cancel", json={})
                assert r.json()["cancelled"] is True

            await asyncio.wait_for(reader, timeout=30)

            ns = [n for n, _ in ev]
            assert "cancelled" in ns, ns
            assert "finished" not in ns
            cancel_ev = next(d for n, d in ev if n == "cancelled")
            assert cancel_ev["reason"] == "user"
            assert cancel_ev["run_id"] == run_id

    run_async(body())


# === SSE / uvicorn helpers ================================================


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class running_server:
    """Async context manager: a real uvicorn server on a random port.

    The SSE path must run over real TCP because httpx's ASGITransport starves a
    long-lived stream (the implementer's ``probe_uvicorn.py`` finding).

    The server runs **in its own thread with its own event loop**, separate from
    the test's ``asyncio.run`` loop. This matters because each test spins up a
    fresh ``asyncio.run`` loop; running uvicorn's ``Server.serve()`` directly on
    those successive loops leaves global/socket state that bleeds into the next
    test (force-cancelled SSE generators → "ASGI callable returned without
    completing response" → truncated streams downstream). A dedicated thread per
    server fully isolates the run subprocess's asyncio tasks and the SSE
    generators from the client loop, so tests don't contaminate each other.
    """

    def __init__(self, rootdir):
        self.app = create_app(rootdir)
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = None
        self._server = None

    async def __aenter__(self):
        import threading

        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        # uvicorn installs signal handlers by default; disable them since we run
        # off the main thread and tear the server down explicitly.
        self._server.install_signal_handlers = lambda: None

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        # Wait (on the test loop) for the server thread to bind.
        for _ in range(300):
            if self._server.started:
                break
            await asyncio.sleep(0.02)
        else:  # pragma: no cover - server failed to start
            raise AssertionError("uvicorn server did not start")
        return self.base

    async def __aexit__(self, *exc):
        # Ask uvicorn to exit and join its thread so the port and any in-flight
        # run are fully released before the next test's event loop starts.
        self._server.should_exit = True
        for _ in range(500):
            if not self._thread.is_alive():
                break
            await asyncio.sleep(0.02)


def _parse_sse(buf, events):
    """Parse complete SSE event blocks out of ``buf``; append (name, data)."""
    while "\r\n\r\n" in buf or "\n\n" in buf:
        sep = "\r\n\r\n" if "\r\n\r\n" in buf else "\n\n"
        block, buf = buf.split(sep, 1)
        name = data = None
        for line in block.replace("\r\n", "\n").split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data is not None:
            try:
                events.append((name, json.loads(data)))
            except json.JSONDecodeError:
                events.append((name, data))
    return buf


async def sse_reader(base, events, stop, stop_on="finished"):
    """Connect to ``/api/events`` and parse events until a terminal one lands.

    Tolerates the connection being torn down at server shutdown
    (``RemoteProtocolError``/read errors) — by then we've already collected the
    events we asserted on, and a clean break keeps cross-test loops from leaking
    a half-open SSE response into the next ``asyncio.run``.
    """
    buf = ""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            async with c.stream("GET", base + "/api/events") as resp:
                async for chunk in resp.aiter_text():
                    buf += chunk
                    buf = _parse_sse(buf, events)
                    if stop.is_set() or any(n == stop_on for n, _ in events):
                        break
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout):
        pass


def _leaf_nodeids(tree):
    """Flatten the tree forest to the list of leaf nodeids."""
    out = []

    def walk(node):
        if node.get("leaf"):
            out.append(node["nodeid"])
        for child in node.get("children", []):
            walk(child)

    for node in tree:
        walk(node)
    return out
