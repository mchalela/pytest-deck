"""Rootdir discovery parity: ``--deck PATH`` must root exactly where a bare
``pytest PATH`` would.

The deck mirrors pytest by delegating to pytest's own ``determine_setup``
(``pytest_deck.rootdir.discover_rootdir``); getting rootdir wrong pins the run
subprocess's cwd too deep, which makes coverage.py key source files above cwd as
absolute paths that the ``/api/coverage`` security gate rejects. These tests lock
the discovery against pytest's documented stopping rules: walk UP to the first
config anchor (``pyproject.toml`` / ``pytest.ini`` / ``tox.ini`` / ``setup.cfg``
/ ``setup.py``), stop at a legitimate subdir ini boundary, and fall back to the
common ancestor when there is no anchor at all.
"""

import re
import subprocess
import sys

from pytest_deck.rootdir import discover_rootdir, read_ini_pythonpath


def _pytest_rootdir(cwd, arg):
    """The rootdir bare pytest reports for ``pytest <arg>`` launched in ``cwd``."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", arg],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    match = re.search(r"^rootdir: (.+)$", result.stdout, re.M)
    assert match, f"no rootdir line:\n{result.stdout}\n{result.stderr}"
    return match.group(1)


def test_walks_up_to_pyproject_from_subdir(tmp_path):
    # A root pyproject anchor: launching at a subdir roots at the project root.
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    sub = tmp_path / "pkg" / "tests"
    sub.mkdir(parents=True)
    assert discover_rootdir(str(sub), tmp_path) == str(tmp_path)


def test_stops_at_subdir_ini_boundary(tmp_path):
    # A legitimate nested ini is the root; never over-walk past it to the outer
    # pyproject. This matches pytest's first-anchor-wins stopping rule.
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    sub = tmp_path / "sub"
    (sub / "deep").mkdir(parents=True)
    (sub / "pytest.ini").write_text("[pytest]\n")
    assert discover_rootdir(str(sub / "deep"), tmp_path) == str(sub)


def test_walks_up_to_setup_py(tmp_path):
    # No ini anywhere, but a setup.py anchor upward makes that dir the root.
    (tmp_path / "setup.py").write_text("")
    sub = tmp_path / "tests"
    sub.mkdir()
    assert discover_rootdir(str(sub), tmp_path) == str(tmp_path)


def test_no_anchor_falls_back_to_common_ancestor(tmp_path):
    # No config or setup.py anywhere: pytest's fallback is the common ancestor of
    # the target and the invocation dir. Launched from within the target, that is
    # the target itself. The deck matches pytest exactly; it is not the deck's job
    # to invent an anchor pytest wouldn't find.
    sub = tmp_path / "tests"
    sub.mkdir()
    assert discover_rootdir(str(sub), sub) == str(sub)
    # Launched from the parent (target as a positional): the ancestor widens to
    # the parent, exactly as ``pytest tests`` from the parent would root.
    assert discover_rootdir(str(sub), tmp_path) == str(tmp_path)


def test_matches_bare_pytest_for_pyproject_subdir(tmp_path):
    # End-to-end parity with a real pytest process.
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    sub = tmp_path / "tests"
    sub.mkdir()
    (sub / "test_a.py").write_text("def test():\n    assert True\n")
    assert discover_rootdir(str(sub), tmp_path) == _pytest_rootdir(sub, ".")


# === read_ini_pythonpath (the -o pythonpath= merge source) =================


def test_read_ini_pythonpath_reads_and_resolves(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = lib src\n")
    dirs = read_ini_pythonpath(tmp_path)
    # Resolved absolute against the inifile's dir, in listed order.
    assert dirs == [str(tmp_path / "lib"), str(tmp_path / "src")]


def test_read_ini_pythonpath_preserves_order_not_sorted(tmp_path):
    # pythonpath is order-significant: pytest inserts in reverse so the
    # first-listed dir shadows later ones. Sorting would invert that and diverge
    # from the user's terminal pytest (regression guard: this used to be sorted()).
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = zfirst aback\n")
    dirs = read_ini_pythonpath(tmp_path)
    assert dirs == [str(tmp_path / "zfirst"), str(tmp_path / "aback")]


def test_read_ini_pythonpath_none_when_absent(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    assert read_ini_pythonpath(tmp_path) == []
    # No ini at all.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert read_ini_pythonpath(empty) == []


def test_read_ini_pythonpath_malformed_ini_degrades(tmp_path):
    # A broken ini makes pytest's determine_setup raise UsageError. This runs
    # eagerly in the parent on every collect and run, so it has to degrade to []
    # and let the child surface the real error as a clean CollectionError rather
    # than an uncaught 500 (regression guard: this used to be uncaught).
    (tmp_path / "tox.ini").write_text("[pytest\nbroken header no close bracket\n")
    assert read_ini_pythonpath(tmp_path) == []
