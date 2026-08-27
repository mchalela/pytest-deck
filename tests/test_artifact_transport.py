"""artifact_dir transport — mpl results.json → nodeid join, end to end.

pytest-mpl isn't installed in this venv, so the child suite SYNTHESIZES a
results.json with mpl's real schema (dotted ``module.cls.name`` keys, relative
``result_image``/``baseline_image``/``diff_image`` paths). The dotted keys are
computed in a conftest fixture using mpl's OWN formula against the live
``item`` — the same formula the inner plugin (``pytest_runtest_setup``) uses —
so the test proves the JOIN, not a hand-copied key. It covers a plain function,
a class method, AND a parametrized case (the load-bearing correctness detail:
``item.name`` carries the ``[param]`` id on both sides).

If pytest-mpl's ``generate_test_name`` ever diverges from the deck inner
plugin's copy, this join returns empty and the assertions fail loudly rather
than silently dropping artifacts.
"""

import asyncio

from pytest_deck.plugin_data import RENDER_MAX_BYTES
from pytest_deck.runner import RunManager, _Run

# The conftest computes each nodeid's mpl dotted name (mpl's generate_test_name)
# and writes result/baseline/diff entries into results.json under the artifacts
# dir.
CONFTEST = """\
import json, os
from pathlib import Path

_results = {}

def _dotted(item):
    module = item.module.__name__
    if item.cls is not None:
        return f"{module}.{item.cls.__name__}.{item.name}"
    return f"{module}.{item.name}"

def pytest_runtest_setup(item):
    dotted = _dotted(item)
    base = f"{dotted}/result.png"
    (Path(os.environ['ART_DIR']) / dotted).mkdir(parents=True, exist_ok=True)
    (Path(os.environ['ART_DIR']) / base).write_bytes(b'PNG')
    _results[dotted] = {
        "status": "failed",
        "result_image": base,
        "baseline_image": None,
        "diff_image": None,
    }

def pytest_sessionfinish(session):
    art = Path(os.environ['ART_DIR'])
    art.mkdir(parents=True, exist_ok=True)
    (art / "results.json").write_text(json.dumps(_results))
"""

SUITE = """\
import pytest

def test_plain():
    assert True

class TestFigures:
    def test_method(self):
        assert True

@pytest.mark.parametrize("n", [1, 2])
def test_param(n):
    assert True
"""


def _drain(queue, until, timeout=30.0):
    async def go():
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

    return go()


def test_artifact_join_maps_dotted_names_to_nodeids(tmp_path):
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_fig.py").write_text(SUITE)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                [],  # whole suite
                env_templates={"ART_DIR": "{tmpdir}/artifacts"},
                transports=[
                    {
                        "plugin": "pytest_mpl",
                        "render": "artifacts",
                        "root": "{tmpdir}/artifacts",
                        "index": "results.json",
                        "index_format": "mpl",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1
            assert "plugin_empty" not in names
            assert names.index("plugin_data") < names.index("finished")
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            assert pd["plugin"] == "pytest_mpl"
            assert pd["render"] == "artifacts"
            data = pd["data"]
            # The join keys by nodeid, not by mpl's dotted name.
            assert "test_fig.py::test_plain" in data
            assert "test_fig.py::TestFigures::test_method" in data
            # Parametrized cases join individually (item.name carries [n]).
            assert "test_fig.py::test_param[1]" in data
            assert "test_fig.py::test_param[2]" in data
            entry = data["test_fig.py::test_param[1]"]
            assert entry == [
                {
                    "name": "result",
                    "rel_path": "test_fig.test_param[1]/result.png",
                    "kind": "image",
                }
            ]
            # artifact_root points at the served dir and it holds the file.
            root, _ = mgr.artifact_root(run_id)
            rel = data["test_fig.py::test_param[1]"][0]["rel_path"]
            assert (root / rel).is_file()
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def _lag_fd3_reader(monkeypatch):
    """Park the fd-3 reader until AFTER the child exits, plus a beat.

    Deterministic race forcing (twin helper in
    test_metadata_transport — see it for the full rationale): a missing drain
    in ``_wait`` fails deterministically, never sleeps-and-hope.
    """
    orig = _Run._read_fd3

    async def lagging(self, loop, read_fd):
        await self.proc.wait()
        await asyncio.sleep(0.2)
        return await orig(self, loop, read_fd)

    monkeypatch.setattr(_Run, "_read_fd3", lagging)


def test_lagging_fd3_reader_still_joins_artifacts(tmp_path, monkeypatch):
    # The worse trigger: `_mpl_names` is fed per item right up to just before
    # exit, so a reader lagging at exit left the join map empty and the
    # artifact transport degraded to plugin_empty for artifacts that exist on
    # disk. The fd-3 drain in _wait (before _read_transports) makes the join
    # whole.
    _lag_fd3_reader(monkeypatch)
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_fig.py").write_text(SUITE)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                [],  # whole suite
                env_templates={"ART_DIR": "{tmpdir}/artifacts"},
                transports=[
                    {
                        "plugin": "pytest_mpl",
                        "render": "artifacts",
                        "root": "{tmpdir}/artifacts",
                        "index": "results.json",
                        "index_format": "mpl",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1, names
            assert "plugin_empty" not in names
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            # Every dotted name joined: the stash was complete at resolve time.
            assert set(pd["data"]) == {
                "test_fig.py::test_plain",
                "test_fig.py::TestFigures::test_method",
                "test_fig.py::test_param[1]",
                "test_fig.py::test_param[2]",
            }
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_artifact_absent_index_emits_plugin_empty(tmp_path):
    # Switch on but the plugin wrote no index (say all green, no summary):
    # plugin_empty, never a crash (P18: a run that exits still emits finished).
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                transports=[
                    {
                        "plugin": "pytest_mpl",
                        "render": "artifacts",
                        "root": "{tmpdir}/artifacts",
                        "index": "results.json",
                        "index_format": "mpl",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "pytest_mpl"}
            # No artifacts dir materialized, so artifact_root is None (a clean
            # 404).
            assert mgr.artifact_root(run_id) is None
        finally:
            await mgr.shutdown()

    asyncio.run(body())


# --- An over-cap index degrades to plugin_empty and still emits finished


def test_over_cap_index_degrades_and_still_finishes(tmp_path):
    # A pathological results.json (larger than RENDER_MAX_BYTES) must never be
    # json.load()'d uncapped. It degrades to plugin_empty, and the run still
    # emits `finished` (P18: a run that exits always finishes; SSE has no replay).
    big = RENDER_MAX_BYTES + 4096
    conftest = f"""\
import json, os
from pathlib import Path

def pytest_sessionfinish(session):
    art = Path(os.environ['ART_DIR'])
    art.mkdir(parents=True, exist_ok=True)
    # A valid-JSON but over-cap index: one huge string value.
    (art / "results.json").write_text(json.dumps({{"blob": "x" * {big}}}))
"""
    (tmp_path / "conftest.py").write_text(conftest)
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                env_templates={"ART_DIR": "{tmpdir}/artifacts"},
                transports=[
                    {
                        "plugin": "pytest_mpl",
                        "render": "artifacts",
                        "root": "{tmpdir}/artifacts",
                        "index": "results.json",
                        "index_format": "mpl",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            # Over cap means refused, so plugin_empty: never plugin_data, never
            # OOM.
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            assert "finished" in names
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "pytest_mpl"}
        finally:
            await mgr.shutdown()

    asyncio.run(body())
