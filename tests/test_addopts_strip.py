"""Beta Phase-1: user plugin-loading channels are neutralized in deck subprocesses.

Deck subprocesses run with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` (P13), so any
user channel that references a plugin — ``addopts = --cov=mypkg``,
``required_plugins = pytest-cov``, a ``PYTEST_ADDOPTS``/``PYTEST_PLUGINS`` env
var — would make every collect/run exit 4 or 1 while their terminal pytest works
fine. Four mechanisms, four defenses (P15):

* ini addopts (pytest.ini AND pyproject ``[tool.pytest.ini_options]``) —
  ``"-o", "addopts="`` in ``base_argv`` (override-ini is extracted in the first
  parse pass, so even early ``-p`` tokens inside addopts are covered);
* ini required_plugins — ``"-o", "required_plugins="`` likewise;
* ``PYTEST_ADDOPTS`` — env→argv injection applied *before* override-ini, so
  ``-o`` can't defend against it; ``build_env`` pops it from the child env;
* ``PYTEST_PLUGINS`` — ``consider_env`` loads it regardless of autoload-disable;
  ``build_env`` pops it too (the inner plugin rides ``-p``, never this var).

The rest of the ini (markers, ...) must stay honored.
"""

import asyncio

from pytest_deck._subprocess import build_env
from pytest_deck.collector import collect
from pytest_deck.runner import RunManager


def run_async(coro):
    return asyncio.run(coro)


async def collect_until(sub, terminal=("finished", "error"), timeout=60.0):
    events = []
    while True:
        ev = await asyncio.wait_for(sub.get(), timeout=timeout)
        if ev is None:
            break
        events.append((ev.name, ev.data))
        if ev.name in terminal:
            break
    return events


def _write_suite(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")


# === 1. ini addopts with an unloadable plugin flag =========================


def test_pytest_ini_addopts_cov_is_neutralized(tmp_path):
    """`addopts = --cov=mypkg` in pytest.ini must not sink collection (exit 4)."""
    _write_suite(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov=mypkg\n")

    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]
    assert result["errors"] == []


def test_pyproject_addopts_is_neutralized(tmp_path):
    """Same via pyproject.toml [tool.pytest.ini_options]."""
    _write_suite(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--cov=mypkg -n auto"\n'
    )

    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]


def test_required_plugins_ini_is_neutralized(tmp_path):
    """`required_plugins = pytest-cov` must not fail the autoload-disabled child.

    Without `-o required_plugins=` this is exit 4 ("Missing required plugins")
    on every collect/run — verified on pytest 8.4.2 and 9.1.1.
    """
    _write_suite(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\nrequired_plugins = pytest-cov\n")

    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]
    assert result["errors"] == []


def test_addopts_with_early_p_token_is_neutralized(tmp_path):
    """Early-consumed `-p` tokens inside addopts are covered by `-o addopts=`."""
    _write_suite(tmp_path)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider -p nosuchplugin\n"
    )

    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]


def test_run_path_with_ini_addopts_finishes_clean(tmp_path):
    """The RunManager subprocess shares base_argv → same neutralization."""
    _write_suite(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov=mypkg\n")

    async def body():
        mgr = RunManager(tmp_path)
        sub = mgr.subscribe()
        await mgr.start(["test_ok.py::test_ok"])
        events = await collect_until(sub)
        if mgr._run is not None:
            await mgr._run.join()

        names = [n for n, _ in events]
        assert "error" not in names, names
        finished = next(d for n, d in events if n == "finished")
        assert finished["exit_code"] == 0

    run_async(body())


# === 2. hostile env vars in the parent env =================================


def test_build_env_drops_hostile_env_vars(monkeypatch):
    """Unit: build_env pops both env channels `-o` can't defend against."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov=nope")
    monkeypatch.setenv("PYTEST_PLUGINS", "no_such_module_xyz")

    env = build_env(9)
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env


def test_pytest_addopts_env_never_reaches_child(tmp_path, monkeypatch):
    """End-to-end: a hostile parent PYTEST_ADDOPTS doesn't sink collection."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov=nope")
    _write_suite(tmp_path)
    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]


def test_pytest_plugins_env_never_reaches_child(tmp_path, monkeypatch):
    """End-to-end: PYTEST_PLUGINS loads despite autoload-disable → exit 1 if kept."""
    monkeypatch.setenv("PYTEST_PLUGINS", "no_such_module_xyz")
    _write_suite(tmp_path)
    result = collect(tmp_path)
    assert [i["nodeid"] for i in result["items"]] == ["test_ok.py::test_ok"]


# === 3. rest-of-ini stays honored ==========================================


def test_rest_of_ini_preserved(tmp_path):
    """Only the P15 channels are neutralized: ini marker registration still applies.

    (`testpaths` is moot here — deck always passes an explicit positional target,
    which overrides it in vanilla pytest too.)
    """
    (tmp_path / "test_marked.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.slow\n"
        "def test_slow():\n"
        "    assert True\n"
        "\n"
        "def test_fast():\n"
        "    assert True\n"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --cov=mypkg\nmarkers =\n    slow: slow tests\n"
    )

    # Collect: the registered marker rides through on the item.
    result = collect(tmp_path)
    items = {i["nodeid"]: i for i in result["items"]}
    marks = items["test_marked.py::test_slow"]["markers"]
    assert any(m["name"] == "slow" for m in marks)

    # Run with -m slow: registration honored, so warning-free and only the marked test.
    async def body():
        mgr = RunManager(tmp_path)
        sub = mgr.subscribe()
        await mgr.start([], m="slow")
        events = await collect_until(sub)
        if mgr._run is not None:
            await mgr._run.join()

        called = [
            d["nodeid"] for n, d in events if n == "report" and d["when"] == "call"
        ]
        assert called == ["test_marked.py::test_slow"]
        assert not [d for n, d in events if n == "warning"], "expected no warnings"
        assert next(d for n, d in events if n == "finished")["exit_code"] == 0

    run_async(body())
