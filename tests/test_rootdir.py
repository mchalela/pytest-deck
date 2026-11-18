"""Ini readers (``pytest_deck.rootdir``): the ``-o pythonpath=`` merge source.

Rootdir parity itself (``pytest --deck a b`` roots where ``pytest a b`` would)
is the outer plugin's job, since it reuses pytest's own ``config.rootpath``;
see ``tests/test_plugin.py``.
"""

from pytest_deck.rootdir import read_ini_pythonpath

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
