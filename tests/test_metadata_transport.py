"""pytest-metadata via the ``fd3`` transport.

The inner plugin emits ONE ``plugin_meta`` fd-3 record in RUN mode only (never
collect — the P6 discipline: collect fd-3 stays minimal and has no transport
reader), and only when pytest-metadata is actually loaded and populated its
stash (``config.stash[metadata_key]`` — the sole 3.x API; emit-silent
otherwise, so ``_inner`` stays dependency-free). The runner stashes the record
mid-run (mirroring ``_mpl_names``) and resolves it at the existing
``_read_transports`` seam, so the exactly-one-of ``plugin_data``/
``plugin_empty`` contract (P18) holds unchanged and a cancelled run emits
neither.

Unit tests fake the stash/record; the live end-to-end test uses the real
pytest-metadata (installed in the dev venv, ``importorskip`` elsewhere).
"""

import asyncio
import json
import sys
import time
import types

import pytest

from pytest_deck._inner import DeckInnerPlugin
from pytest_deck.runner import RunManager, _Run


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


# The transports entry the server compiles for the curated metadata manifest.
_FD3_TRANSPORT = {"plugin": "metadata", "render": None, "type": "fd3"}


# === inner plugin: the plugin_meta emission ================================


def _inner_plugin(collectonly):
    """A DeckInnerPlugin over a stub config with a REAL pytest.Stash, its
    ``_emit`` captured into a list (no fd involved)."""
    config = types.SimpleNamespace(
        option=types.SimpleNamespace(collectonly=collectonly),
        stash=pytest.Stash(),
    )
    plugin = DeckInnerPlugin(config)
    emitted = []
    plugin._emit = emitted.append
    return plugin, config, emitted


def test_inner_emits_plugin_meta_in_run_mode():
    metadata_key = pytest.importorskip("pytest_metadata.plugin").metadata_key
    plugin, config, emitted = _inner_plugin(collectonly=False)
    config.stash[metadata_key] = {
        "Python": "3.13.1",
        "Packages": {"pytest": "9.1.1"},
    }
    plugin.pytest_collection_finish(session=None)
    assert emitted == [
        {
            "$deck": "plugin_meta",
            "data": {"Python": "3.13.1", "Packages": {"pytest": "9.1.1"}},
        }
    ]
    json.dumps(emitted[0])  # wire-serializable as emitted


def test_inner_no_plugin_meta_in_collect_mode():
    # Collect subprocesses never carry plugin_meta; the collection line is the
    # only collect-mode record here (P6 untouched).
    metadata_key = pytest.importorskip("pytest_metadata.plugin").metadata_key
    plugin, config, emitted = _inner_plugin(collectonly=True)
    config.stash[metadata_key] = {"Python": "3.13.1"}
    plugin.pytest_collection_finish(types.SimpleNamespace(items=[]))
    assert [p["$deck"] for p in emitted] == ["collection"]


def test_inner_silent_when_metadata_not_loaded():
    # Plugin installed but not loaded (the deck's autoload-disable default): the
    # stash has no metadata_key entry, so no record and no error.
    plugin, config, emitted = _inner_plugin(collectonly=False)
    plugin.pytest_collection_finish(session=None)
    assert emitted == []


def test_inner_silent_when_pytest_metadata_unimportable(monkeypatch):
    # Dependency-free discipline: with pytest_metadata absent the import raises
    # and the emission silently no-ops (None in sys.modules makes
    # `from pytest_metadata.plugin import ...` raise ImportError).
    monkeypatch.setitem(sys.modules, "pytest_metadata.plugin", None)
    plugin, config, emitted = _inner_plugin(collectonly=False)
    plugin.pytest_collection_finish(session=None)
    assert emitted == []


def test_inner_plugin_meta_stringifies_nonserializable():
    # Defensive serialization: a hook-added non-JSON value must not kill the
    # record (json.dumps in _emit would raise), so str() is the fallback, keys
    # included.
    metadata_key = pytest.importorskip("pytest_metadata.plugin").metadata_key
    plugin, config, emitted = _inner_plugin(collectonly=False)
    marker = object()
    config.stash[metadata_key] = {"Obj": marker, "Nested": {1: marker}, "T": (1, "a")}
    plugin.pytest_collection_finish(session=None)
    data = emitted[0]["data"]
    assert data["Obj"] == str(marker)
    assert data["Nested"] == {"1": str(marker)}
    assert data["T"] == [1, "a"]
    json.dumps(emitted[0])


# === runner: fd-3 dispatch stashes the record ==============================


def test_dispatch_fd3_stashes_plugin_meta_not_an_event(tmp_path):
    # Like mpl_name: a private record consulted post-exit, never broadcast as
    # an SSE event.
    stub = _StubManager()
    run = _Run("run-1", stub, tmp_path, [], None, None)
    line = (
        json.dumps({"$deck": "plugin_meta", "data": {"Python": "3.13.1"}}).encode()
        + b"\n"
    )
    run._dispatch_fd3(line)
    assert run._plugin_meta == {"Python": "3.13.1"}
    assert stub.events == []


def test_dispatch_plugin_meta_ignores_bad_data(tmp_path):
    run = _Run("run-1", _StubManager(), tmp_path, [], None, None)
    for bad in (
        {"$deck": "plugin_meta"},
        {"$deck": "plugin_meta", "data": []},
        {"$deck": "plugin_meta", "data": {}},
    ):
        run._dispatch_fd3(json.dumps(bad).encode() + b"\n")
    assert run._plugin_meta is None


# === runner: transport resolution (P18) ====================================


def test_read_transports_fd3_resolves_stash_to_plugin_data(tmp_path):
    run = _Run(
        "run-9", None, tmp_path, [], None, None, transports=[dict(_FD3_TRANSPORT)]
    )
    run._plugin_meta = {"Python": "3.13.1", "Packages": {"pytest": "9.1.1"}}
    assert run._read_transports() == [
        (
            "plugin_data",
            {
                "run_id": "run-9",
                "plugin": "metadata",
                "render": "metadata",
                "data": {"Python": "3.13.1", "Packages": {"pytest": "9.1.1"}},
            },
        )
    ]


def test_read_transports_fd3_absent_record_is_plugin_empty(tmp_path):
    # Enabled but the record never arrived: plugin_empty (exactly one event per
    # declared transport, never both, never neither).
    run = _Run(
        "run-9", None, tmp_path, [], None, None, transports=[dict(_FD3_TRANSPORT)]
    )
    assert run._read_transports() == [
        ("plugin_empty", {"run_id": "run-9", "plugin": "metadata"})
    ]


class _FakeProc:
    returncode = 0

    async def wait(self):
        return 0


def test_cancelled_run_never_resolves_fd3_transport(tmp_path):
    # The cancel pin extended to fd3: even with the record stashed and the
    # transport declared, a cancelled _wait emits `cancelled` only, with no
    # plugin_data and no plugin_empty (transports are read on the finish path).
    stub = _StubManager()
    run = _Run(
        "run-1", stub, tmp_path, [], None, None, transports=[dict(_FD3_TRANSPORT)]
    )
    run.proc = _FakeProc()
    run.started_at = time.time()
    run._plugin_meta = {"Python": "3.13.1"}
    run._cancel_reason = "user"
    asyncio.run(run._wait())
    assert [e.name for e in stub.events] == ["cancelled"]


# === end to end ============================================================


def test_fd3_enabled_but_plugin_absent_emits_plugin_empty(tmp_path):
    # Transport declared but `-p metadata` not passed: the child's inner plugin
    # finds no stash entry (autoload-disable keeps the plugin out even though
    # it is installed in this venv), so exactly one plugin_empty, before the
    # terminal finished (P18).
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"], transports=[dict(_FD3_TRANSPORT)]
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            assert names.index("plugin_empty") < names.index("finished")
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "metadata"}
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def _lag_fd3_reader(monkeypatch):
    """Park the fd-3 reader until AFTER the child exits, plus a beat.

    Deterministic race forcing (twin helper in
    test_artifact_transport): with the reader parked past process exit, the
    ``_wait`` waiter always wins the post-exit race unless it DRAINS the reader
    before resolving transports. The child's fd-3 lines sit buffered in the
    pipe; once the wrapper proceeds, the real reader reads them all to EOF (the
    write end closed at exit). The 0.2s beat dwarfs the ~ms an undrained
    ``_read_transports`` would take, so a missing drain fails deterministically
    — no sleeps-and-hope.
    """
    orig = _Run._read_fd3

    async def lagging(self, loop, read_fd):
        await self.proc.wait()
        await asyncio.sleep(0.2)
        return await orig(self, loop, read_fd)

    monkeypatch.setattr(_Run, "_read_fd3", lagging)


def test_lagging_fd3_reader_still_resolves_plugin_data(tmp_path, monkeypatch):
    # Regression: _wait used to resolve the fd3 transport from self._plugin_meta
    # with zero synchronization against the reader task that populates it, so
    # a lagging reader yielded a spurious plugin_empty for a record that did
    # arrive (P18's exactly-one-of held, but it was the wrong one). The drain
    # in _wait makes it the right one.
    pytest.importorskip("pytest_metadata")
    _lag_fd3_reader(monkeypatch)
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                extra_argv=["-p", "metadata"],
                transports=[dict(_FD3_TRANSPORT)],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1, names
            assert "plugin_empty" not in names
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            assert pd["render"] == "metadata"
            # The drain also flushed the buffered reports before `finished`.
            assert "report" in names
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_live_pytest_metadata_end_to_end(tmp_path):
    # The whole pipe with the real plugin: `-p metadata` under autoload-disable
    # (P13), metadata's tryfirst pytest_configure populates the stash, the inner
    # plugin emits plugin_meta at collection_finish, the runner stashes it and
    # resolves the fd3 transport post-exit, and out comes one plugin_data with
    # render "metadata", before finished.
    pytest.importorskip("pytest_metadata")
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                extra_argv=["-p", "metadata"],
                transports=[dict(_FD3_TRANSPORT)],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1
            assert "plugin_empty" not in names
            assert names.index("plugin_data") < names.index("finished")
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            assert pd["plugin"] == "metadata"
            assert pd["render"] == "metadata"
            data = pd["data"]
            # The real dict: environment scalars + the Packages/Plugins tables.
            assert data["Python"].startswith("3.")
            assert isinstance(data["Platform"], str)
            assert "pytest" in data["Packages"]
            # Under autoload-disable, Plugins reflects the -p-enabled set; that
            # is accurate for deck runs, not a bug.
            assert "metadata" in data["Plugins"]
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    asyncio.run(body())
