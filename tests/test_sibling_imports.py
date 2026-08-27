"""P12/P20 sibling-import fix: importlib + prepend-mirrored import dirs.

The deck runs subprocesses with ``--import-mode=importlib`` — required so a
``__init__.py``-less tree with DUPLICATE test-file basenames collects without
crashing (the real reason for importlib; NOT nodeid stability). importlib never
mutates ``sys.path``, so a test's top-level ``from helper import x`` (helper.py
adjacent, no package) fails ``ModuleNotFoundError`` unless the test's directory
is on the import path.

P20: ``import_paths`` mirrors pytest ``prepend`` mode EXACTLY — one dir per
file, its package root (or its own dir when packageless) — never a downward tree
walk. That is what stops a vendored ``scipy/signal/`` from shadowing the stdlib
``signal`` mid-collection (the GriSPy bug). ``collector`` resolves the
collect-no-targets chicken-and-egg with a minimal pass 1 + a sibling-inject
pass 2 (only on an import-time collect error). The dirs reach the child via
pytest's OWN ``-o pythonpath=`` (a COLLECTION-time ``sys.path`` insert, NOT the
``PYTHONPATH`` env — which governs the bootstrap, where a ``deep/signal.py``
would shadow the stdlib ``signal`` before pytest runs). That single ``-o`` token
(last-wins) is MERGED with the user's ini ``pythonpath`` so it composes with,
never clobbers, the user's config.

These drive the real collect/run subprocesses against tmp fixture projects.
"""

import asyncio

import pytest
from _pytest.pathlib import resolve_pkg_root_and_module_name

from pytest_deck.collector import collect
from pytest_deck.import_paths import import_dirs, pkg_roots_for_files
from pytest_deck.runner import RunManager


def run_async(coro):
    return asyncio.run(coro)


async def _drain(queue, until, timeout=30.0):
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


# --- fixture projects -----------------------------------------------------


@pytest.fixture
def sibling_project(tmp_path):
    """A test importing an ADJACENT helper module, no __init__.py anywhere."""
    (tmp_path / "helper.py").write_text("def value():\n    return 7\n")
    (tmp_path / "test_uses_helper.py").write_text(
        "from helper import value\n"
        "\n"
        "def test_helper():\n"
        "    assert value() == 7\n"
    )
    return tmp_path


@pytest.fixture
def nested_sibling_project(tmp_path):
    """The sibling import lives one directory deeper than rootdir."""
    sub = tmp_path / "pkgless" / "deep"
    sub.mkdir(parents=True)
    (sub / "helper.py").write_text("def deep_value():\n    return 11\n")
    (sub / "test_deep.py").write_text(
        "from helper import deep_value\n"
        "\n"
        "def test_deep():\n"
        "    assert deep_value() == 11\n"
    )
    # A shallow test too, so rootdir isn't the only dir on the path.
    (tmp_path / "test_shallow.py").write_text("def test_ok():\n    assert True\n")
    return tmp_path


@pytest.fixture
def duplicate_basename_project(tmp_path):
    """Two ``test_utils.py`` in sibling dirs, no __init__.py — importlib's raison
    d'etre. ``prepend`` mode would crash this with exit 2."""
    for sub in ("alpha", "beta"):
        d = tmp_path / sub
        d.mkdir()
        (d / "test_utils.py").write_text(f"def test_{sub}():\n    assert '{sub}'\n")
    return tmp_path


# --- import_dirs discovery unit (P20: prepend pkg_roots, no downward walk) --


def test_import_dirs_no_targets_is_rootdir_only(nested_sibling_project):
    # P20: with no targets the deck injects only rootdir. It never walks down
    # and adds the nested test dir (that is pass 2's job, on demand). This is
    # the exact property that keeps a vendored subtree off the path.
    dirs = import_dirs(nested_sibling_project)
    root = str(nested_sibling_project.resolve())
    deep = str((nested_sibling_project / "pkgless" / "deep").resolve())
    assert dirs == [root]
    assert deep not in dirs


def test_import_dirs_file_target_uses_pkg_root(sibling_project):
    # A packageless file: its own dir is the prepend pkg_root.
    target = "test_uses_helper.py::test_helper"
    dirs = import_dirs(sibling_project, [target])
    assert str(sibling_project.resolve()) in dirs
    assert dirs == sorted(dirs)


def test_import_dirs_nested_package_target_uses_package_parent(tmp_path):
    # For a file inside a package, prepend puts the package's parent on the
    # path, not the file's own dir. This mirrors resolve_pkg_root_and_module_name.
    pkg = tmp_path / "pkg" / "sub"
    pkg.mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "test_x.py").write_text("def test_x():\n    assert True\n")
    dirs = import_dirs(tmp_path, ["pkg/sub/test_x.py::test_x"])
    # pkg_root is tmp_path (parent of the top package `pkg`), included as rootdir.
    assert str(tmp_path.resolve()) in dirs
    assert str((tmp_path / "pkg").resolve()) not in dirs
    assert str(pkg.resolve()) not in dirs


def test_import_dirs_does_not_walk_vendored_tree(tmp_path):
    # The GriSPy shape at the unit level: a vendored ``vendor/scipy/signal/``
    # tree must never appear in import_dirs. The old downward walk added it and
    # shadowed the stdlib ``signal``.
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    vendored = tmp_path / "vendor" / "scipy" / "signal"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text("import sigtools\n")
    dirs = import_dirs(tmp_path, ["test_a.py::test_a"])
    assert all("scipy" not in d and "vendor" not in d for d in dirs), dirs


# --- collect: sibling imports now resolve ---------------------------------


def test_sibling_import_collects_clean(sibling_project):
    result = collect(sibling_project)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "test_uses_helper.py::test_helper" in nodeids


def test_nested_sibling_import_collects_clean(nested_sibling_project):
    result = collect(nested_sibling_project)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "pkgless/deep/test_deep.py::test_deep" in nodeids
    assert "test_shallow.py::test_ok" in nodeids


def test_duplicate_basenames_still_collect(duplicate_basename_project):
    # Guards the reason importlib exists: this must never regress to a crash.
    result = collect(duplicate_basename_project)
    assert result["errors"] == []
    nodeids = {item["nodeid"] for item in result["items"]}
    assert nodeids == {
        "alpha/test_utils.py::test_alpha",
        "beta/test_utils.py::test_beta",
    }


def test_custom_python_files_sibling_collects_clean(tmp_path):
    # A custom `python_files` naming (check_*.py) in a subdir, whose module
    # sibling-imports a helper. Pass 2 injects the dir even though no
    # default-glob test file lives there, so errors is 0 (it used to be 1).
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "helper.py").write_text("def val():\n    return 5\n")
    (sub / "check_thing.py").write_text(
        "from helper import val\n" "\n" "def test_thing():\n" "    assert val() == 5\n"
    )
    result = collect(tmp_path)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "sub/check_thing.py::test_thing" in nodeids


def test_env_named_dir_sibling_collects_clean(tmp_path):
    # A test dir literally named `env/` (pytest's default norecursedirs only
    # skips `venv`, so this one is not pruned) with a sibling import.
    envd = tmp_path / "env"
    envd.mkdir()
    (envd / "helper.py").write_text("def h():\n    return 3\n")
    (envd / "test_in_env.py").write_text(
        "from helper import h\n" "\n" "def test_env():\n" "    assert h() == 3\n"
    )
    result = collect(tmp_path)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "env/test_in_env.py::test_env" in nodeids


def test_midlevel_conftest_sibling_collects_clean(tmp_path):
    # A mid-level conftest.py that sibling-imports an adjacent helper (no
    # package). The conftest's dir has to be on the import path even though no
    # test file matching any glob lives there.
    sub = tmp_path / "feature"
    sub.mkdir()
    (sub / "shared.py").write_text("FIXTURE_VALUE = 21\n")
    (sub / "conftest.py").write_text(
        "import pytest\n"
        "from shared import FIXTURE_VALUE\n"
        "\n"
        "@pytest.fixture\n"
        "def shared_value():\n"
        "    return FIXTURE_VALUE\n"
    )
    (sub / "test_feature.py").write_text(
        "def test_uses_conftest(shared_value):\n" "    assert shared_value == 21\n"
    )
    result = collect(tmp_path)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "feature/test_feature.py::test_uses_conftest" in nodeids


# --- run: sibling imports resolve through the run subprocess too ----------


def test_sibling_import_runs_clean(sibling_project):
    async def body():
        mgr = RunManager(sibling_project)
        try:
            q = mgr.subscribe()
            await mgr.start(["test_uses_helper.py::test_helper"])
            events = await _drain(q, lambda ns: "finished" in ns)
            # A ModuleNotFoundError at import would exit 2 with no call report.
            reports = [d for n, d in events if n == "report"]
            call = next(r for r in reports if r["when"] == "call")
            assert call["outcome"] == "passed"
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    run_async(body())


def test_nested_sibling_import_runs_clean(nested_sibling_project):
    async def body():
        mgr = RunManager(nested_sibling_project)
        try:
            q = mgr.subscribe()
            await mgr.start(["pkgless/deep/test_deep.py::test_deep"])
            events = await _drain(q, lambda ns: "finished" in ns)
            call = next(d for n, d in events if n == "report" and d["when"] == "call")
            assert call["outcome"] == "passed"
        finally:
            await mgr.shutdown()

    run_async(body())


# --- user's ini pythonpath is preserved (composed, not clobbered) ---------


def test_user_pythonpath_ini_is_preserved(tmp_path):
    """Our injected dirs must not drop the user's ini ``pythonpath``."""
    lib = tmp_path / "libdir"
    lib.mkdir()
    (lib / "userlib.py").write_text("USER = 99\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = libdir\n")
    # This test both imports the ini-provided lib and lives next to a sibling
    # helper we inject, so both paths have to resolve at the same time.
    (tmp_path / "helper.py").write_text("def h():\n    return 1\n")
    (tmp_path / "test_both.py").write_text(
        "from userlib import USER\n"
        "from helper import h\n"
        "\n"
        "def test_user_lib_survives():\n"
        "    assert USER == 99\n"
        "\n"
        "def test_sibling_helper_works():\n"
        "    assert h() == 1\n"
    )

    result = collect(tmp_path)
    assert result["errors"] == []

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                [
                    "test_both.py::test_user_lib_survives",
                    "test_both.py::test_sibling_helper_works",
                ]
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            calls = [d for n, d in events if n == "report" and d["when"] == "call"]
            assert len(calls) == 2
            assert all(c["outcome"] == "passed" for c in calls)
        finally:
            await mgr.shutdown()

    run_async(body())


# --- the GriSPy regression: a vendored stdlib-shadowing subtree ------------


def test_vendored_signal_does_not_shadow_stdlib(tmp_path):
    # The bug this whole change fixes (P20). A vendored ``vendor/signal/``
    # package collides with the stdlib ``signal``. A conftest that imports the
    # stdlib ``signal`` at collection time must keep getting the real one; if
    # the vendored dir lands on the child path it shadows the stdlib and its
    # import-time ``raise`` sinks collection. Before the fix (the downward
    # walk) ``import_dirs`` added ``vendor/`` and ``collect()`` raised
    # CollectionError; after it, only rootdir is injected and collection
    # succeeds.
    (tmp_path / "conftest.py").write_text(
        "import signal\n" "assert hasattr(signal, 'SIGINT')  # the REAL stdlib one\n"
    )
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    vendored = tmp_path / "vendor" / "signal"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text(
        "raise ImportError('vendored signal must never shadow the stdlib')\n"
    )

    dirs = import_dirs(tmp_path)
    assert all(not d.endswith("vendor") for d in dirs), dirs

    result = collect(tmp_path)
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "test_ok.py::test_ok" in nodeids
    assert result["errors"] == []


# --- pass-2 paths: bare sibling / conftest with no targets -----------------


def test_bare_sibling_no_targets_pass2(sibling_project):
    # Collect with no targets: pass 1 (rootdir only) already covers this
    # top-level sibling because rootdir is the helper's dir. Guards that the
    # packageless sibling still resolves on the no-targets path.
    result = collect(sibling_project)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "test_uses_helper.py::test_helper" in nodeids


def test_deep_bare_sibling_no_targets_pass2(nested_sibling_project):
    # The sibling import is one dir deep, so pass 1 (rootdir only) errors and
    # pass 2 has to inject the erroring file's own dir. This exercises the
    # pass-2 sibling-inject branch end to end.
    result = collect(nested_sibling_project)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert "pkgless/deep/test_deep.py::test_deep" in nodeids
    assert "test_shallow.py::test_ok" in nodeids


# --- parity: our pkg_roots == pytest's own resolver ------------------------


@pytest.mark.parametrize(
    "layout",
    [
        # Each layout picks the files to create, the target, and whether it is
        # packaged.
        {"top_level": True},
        {"nested_pkg": True},
        {"sibling_no_init": True},
    ],
)
def test_pkg_roots_parity_with_pytest(tmp_path, layout):
    # P20: pkg_roots_for_files has to agree with pytest's own
    # resolve_pkg_root_and_module_name for packaged files, and fall back to the
    # file's own dir exactly where pytest raises CouldNotResolvePathError.
    if "top_level" in layout:
        f = tmp_path / "test_top.py"
        f.write_text("def test_a():\n    assert True\n")
    elif "nested_pkg" in layout:
        pkg = tmp_path / "app" / "core"
        pkg.mkdir(parents=True)
        (tmp_path / "app" / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text("")
        f = pkg / "test_models.py"
        f.write_text("def test_a():\n    assert True\n")
    else:  # sibling_no_init
        sub = tmp_path / "plain"
        sub.mkdir()
        f = sub / "test_plain.py"
        f.write_text("def test_a():\n    assert True\n")

    ours = set(pkg_roots_for_files([str(f)], tmp_path))
    try:
        expected = str(resolve_pkg_root_and_module_name(f)[0])
    except Exception:  # CouldNotResolvePathError: prepend uses the file's own dir
        expected = str(f.parent)
    assert expected in ours


def test_pkg_roots_dedup_and_sorted(tmp_path):
    # Two files sharing a pkg_root collapse to one dir; output is sorted.
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "test_b.py").write_text("def test_b():\n    assert True\n")
    dirs = pkg_roots_for_files(
        [str(tmp_path / "test_a.py"), str(tmp_path / "test_b.py")], tmp_path
    )
    assert dirs == sorted(dirs)
    assert dirs.count(str(tmp_path.resolve())) == 1


# --- run path: nodeids give minimal pkg_roots, no shadowing ----------------


def test_run_path_nodeids_give_minimal_dirs(tmp_path):
    # The run path feeds self.nodeids through import_dirs and
    # pkg_roots_for_files. A deep test's nodeid yields exactly rootdir
    # (packageless top) or the file's own dir, and never a sibling vendored
    # tree.
    sub = tmp_path / "pkgless" / "deep"
    sub.mkdir(parents=True)
    (sub / "test_deep.py").write_text("def test_deep():\n    assert True\n")
    vendored = tmp_path / "vendor" / "scipy" / "signal"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text("")
    dirs = import_dirs(tmp_path, ["pkgless/deep/test_deep.py::test_deep"])
    assert str(sub.resolve()) in dirs  # packageless, so its own dir
    assert all("scipy" not in d for d in dirs), dirs


# --- the bootstrap-shadow repro (P20) ---------------------------
# Fail-before, pass-after for the deeper root cause: injecting sibling dirs via
# the PYTHONPATH env shadowed a stdlib module imported at the child's bootstrap
# (before pytest ran). Injecting via `-o pythonpath=` (a collection-time
# sys.path insert) is immune. A `deep/<stdlib>.py` beside a sibling-importing
# test, plus a rootdir conftest that imports that same stdlib name, must
# collect cleanly (1 item, 0 errors), matching plain pytest.


def _shadow_project(tmp_path, stdlib_name):
    """A `deep/<stdlib>.py` (raises on import) beside a sibling-importing test,
    plus a rootdir conftest importing the real stdlib module at collection."""
    (tmp_path / "conftest.py").write_text(
        f"import {stdlib_name}  # must resolve to the REAL stdlib, not deep/\n"
    )
    deep = tmp_path / "deep"
    deep.mkdir()
    # A module named after a bootstrap-imported stdlib name. If deep/ ever lands
    # on the child's PYTHONPATH env, this shadows the stdlib at interpreter
    # startup and the child dies before pytest runs.
    (deep / f"{stdlib_name}.py").write_text(
        "raise RuntimeError('this module must never shadow the stdlib')\n"
    )
    (deep / "helper.py").write_text("v = 42\n")
    (deep / "test_s.py").write_text(
        "from helper import v\n\n\ndef test_s():\n    assert v == 42\n"
    )
    return deep


def _plain_pytest_collects_one(tmp_path):
    """Sanity: plain pytest (no deck) collects exactly 1 test under tmp_path."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    return "1 test" in r.stdout or "test_s.py::test_s" in r.stdout


@pytest.mark.parametrize("stdlib_name", ["signal", "subprocess"])
def test_bootstrap_shadow_does_not_crash_child(tmp_path, stdlib_name):
    # The repro. Before the fix (sibling dirs on the PYTHONPATH env) the deck
    # raised CollectionError because deep/signal.py shadowed the stdlib at the
    # child's bootstrap, while plain pytest collected 1 test. After the fix
    # (dirs on `-o pythonpath=`, a collection-time sys.path insert) the deck
    # matches plain pytest: 1 item, 0 errors.
    _shadow_project(tmp_path, stdlib_name)
    assert _plain_pytest_collects_one(tmp_path)

    result = collect(tmp_path)
    assert result["errors"] == []
    nodeids = [item["nodeid"] for item in result["items"]]
    assert nodeids == ["deep/test_s.py::test_s"]


def test_bootstrap_shadow_runs_clean(tmp_path):
    # The run path shares the same `-o pythonpath=` handshake (P14), so the
    # sibling test has to actually run, not just collect, without the shadow
    # crash.
    _shadow_project(tmp_path, "signal")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(["deep/test_s.py::test_s"])
            events = await _drain(q, lambda ns: "finished" in ns)
            call = next(d for n, d in events if n == "report" and d["when"] == "call")
            assert call["outcome"] == "passed"
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    run_async(body())


# --- merge/compose: user ini pythonpath survives a deep sibling inject ------


def test_user_ini_pythonpath_composes_with_sibling(tmp_path):
    # The deck's `-o pythonpath=` clobbers the user's ini pythonpath unless it
    # is merged. This test forces both to be needed at once, one dir deep (so
    # the deck's pass-2 sibling inject fires and the merged token still has to
    # carry the user's `userlib`): a test that imports usermod (from the ini
    # pythonpath) and a sibling helper. Deck collect and run both succeeding
    # means no clobber.
    userlib = tmp_path / "userlib"
    userlib.mkdir()
    (userlib / "usermod.py").write_text("USER = 123\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = userlib\n")
    deep = tmp_path / "deep"
    deep.mkdir()
    (deep / "helper.py").write_text("def h():\n    return 5\n")
    (deep / "test_both.py").write_text(
        "from usermod import USER\n"
        "from helper import h\n"
        "\n\n"
        "def test_ini_lib():\n    assert USER == 123\n"
        "\n\n"
        "def test_sibling():\n    assert h() == 5\n"
    )

    result = collect(tmp_path)
    assert result["errors"] == []
    nodeids = {item["nodeid"] for item in result["items"]}
    assert nodeids == {
        "deep/test_both.py::test_ini_lib",
        "deep/test_both.py::test_sibling",
    }

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                [
                    "deep/test_both.py::test_ini_lib",
                    "deep/test_both.py::test_sibling",
                ]
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            calls = [d for n, d in events if n == "report" and d["when"] == "call"]
            assert len(calls) == 2
            assert all(c["outcome"] == "passed" for c in calls)
        finally:
            await mgr.shutdown()

    run_async(body())
