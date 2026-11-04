"""Tests for the OUTER plugin (``pytest_deck.plugin``): option registration and
the ``--deck`` short-circuit.

We never launch a real server here: the launch is guarded two ways —
``PYTEST_DECK_NO_LAUNCH`` (for subprocess runs) and monkeypatching
``plugin._launch_server`` (for in-process runs).
"""

import os

import pytest

import pytest_deck.plugin as plugin


def _deck_pargs():
    """Force-load the outer plugin ONLY when autoload is disabled (P13).

    These tests assert on the deck's ``--deck`` option, so the plugin must be
    present in the inner pytest run. Under a normal ``pytest``/CI invocation the
    installed ``pytest11`` entry point already autoloads it, and passing ``-p``
    would double-register (pluggy raises "already registered"). But when the
    deck runs its subprocesses with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` (P13),
    autoload is off and pytester's grandchild inherits that env, so the plugin
    is absent unless we force-load it. Keying off the env var makes each test
    hermetic in both worlds.
    """
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        return ("-p", "pytest_deck.plugin")
    return ()


def test_deck_option_is_registered(pytester, monkeypatch):
    """``--deck`` shows up in ``pytest --help`` once the plugin is loaded.

    Run as a subprocess so the installed ``pytest11`` entry point loads the
    outer plugin exactly as it would for a real user.

    ``PYTEST_DECK_NO_LAUNCH`` is set defensively: ``--help`` never reaches the
    launcher today, but if a regression made the hook proceed, the real server
    would block the subprocess and hang this test instead of failing it.
    """
    monkeypatch.setenv("PYTEST_DECK_NO_LAUNCH", "1")
    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_subprocess(*_deck_pargs(), "--help")
    result.stdout.fnmatch_lines(["*--deck *"])
    result.stdout.fnmatch_lines(["*--deck-port=*"])


def test_deck_short_circuits_and_does_not_run_tests(pytester, monkeypatch):
    """With ``--deck``, pytest must NOT run the user's tests in this process.

    ``PYTEST_DECK_NO_LAUNCH`` makes the launcher a no-op that returns exit 0, so
    the run short-circuits before the test loop. The created test must report as
    neither passed nor failed.
    """
    monkeypatch.setenv("PYTEST_DECK_NO_LAUNCH", "1")
    pytester.makepyfile(test_sample="""
        def test_should_not_run():
            assert False
        """)
    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_subprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    # The launcher ran instead of the test loop.
    result.stdout.fnmatch_lines(["pytest-deck: would launch dashboard for *"])
    # Proof the test loop never ran: no pytest summary line, and the failing
    # test never reported. (A real run would print a "1 failed" summary.)
    result.stdout.no_fnmatch_line("*failed*")
    result.stdout.no_fnmatch_line("*test_should_not_run*")


def test_without_deck_tests_run_normally(pytester, monkeypatch):
    """Sanity: installing the plugin is inert — without ``--deck`` tests run.

    ``PYTEST_DECK_NO_LAUNCH`` guards against a hang: if a regression made the
    hook launch the server even when ``--deck`` is absent, the real
    ``serve_forever`` would block this subprocess forever. With the guard the
    launcher is a no-op, so such a regression fails fast (the test loop never
    runs, ``assert_outcomes`` mismatches) instead of hanging.
    """
    monkeypatch.setenv("PYTEST_DECK_NO_LAUNCH", "1")
    pytester.makepyfile(test_sample="""
        def test_passes():
            assert True
        """)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_launch_server_invoked_with_rootpath(pytester, monkeypatch):
    """In-process: ``--deck`` (no path) calls the launcher with the rootdir.

    Monkeypatching ``_launch_server`` proves the hook reaches it without binding
    a socket, and that ``firstresult`` short-circuiting returns our exit code.
    """
    calls = []

    def fake_launch(config, rootdir, targets=None):
        calls.append((rootdir, targets))
        return 0

    monkeypatch.setattr(plugin, "_launch_server", fake_launch)
    pytester.makepyfile(test_sample="def test_x(): assert True")

    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    assert len(calls) == 1
    rootdir, targets = calls[0]
    # Bare ``--deck``: rootdir is the discovered path (non-empty), no targets.
    assert rootdir and rootdir != ""
    assert targets is None


def _launch_calls(monkeypatch):
    """Monkeypatch ``_launch_server``; return the list of (rootdir, targets) calls."""
    calls = []
    monkeypatch.setattr(
        plugin,
        "_launch_server",
        lambda config, rootdir, targets=None: calls.append((rootdir, targets)) or 0,
    )
    return calls


def test_launch_server_honors_explicit_path(pytester, monkeypatch):
    """``pytest --deck PATH`` keeps PATH as the target, resolved absolute.

    ``pytester.path`` has no config anchor, so pytest's rootdir for it is the
    dir itself (common-ancestor fallback): rootdir == PATH here, and PATH also
    rides through as the initial collection target (mirrors ``pytest PATH``).
    """
    calls = _launch_calls(monkeypatch)
    pytester.makepyfile(test_sample="def test_x(): assert True")

    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", str(pytester.path))
    assert result.ret == 0
    assert calls == [(str(pytester.path), [str(pytester.path)])]


def test_launch_server_forwards_every_positional(pytester, monkeypatch):
    """``pytest --deck a b`` forwards BOTH paths, in order, as absolute targets.

    The regression this pins: ``--deck`` used to take a single PATH of its
    own and silently drop any other positional. The flag's position must not
    matter either (``pytest a b --deck`` is the same invocation).
    """
    calls = _launch_calls(monkeypatch)
    for pkg in ("a", "b"):
        d = pytester.mkdir(pkg)
        (d / f"test_{pkg}.py").write_text("def test(): assert True\n")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", "a", "b")
    assert result.ret == 0
    result = pytester.runpytest_inprocess(*_deck_pargs(), "a", "b", "--deck")
    assert result.ret == 0
    expected = [str(pytester.path / "a"), str(pytester.path / "b")]
    assert calls == [(str(pytester.path), expected)] * 2


def test_launch_server_keeps_file_and_nodeid_targets(pytester, monkeypatch):
    """Files and node IDs are positionals too; the ``::`` suffix survives."""
    calls = _launch_calls(monkeypatch)
    pytester.makepyfile(
        test_sample="def test_x(): assert True\ndef test_y(): assert True"
    )

    result = pytester.runpytest_inprocess(
        *_deck_pargs(), "--deck", "test_sample.py", "test_sample.py::test_y"
    )
    assert result.ret == 0
    sample = str(pytester.path / "test_sample.py")
    assert calls == [(str(pytester.path), [sample, f"{sample}::test_y"])]


def test_rootdir_walks_up_from_positionals(pytester, monkeypatch):
    """Rootdir parity: ``--deck pkg/tests`` roots where ``pytest pkg/tests`` would.

    The deck reuses ``config.rootpath``, so the upward walk to the config
    anchor is pytest's own; the target stays the subdir (rootdir != scope).
    Getting this wrong pins the run subprocess's cwd too deep (P12), which
    makes coverage key files above it as absolute paths the ``/api/coverage``
    gate rejects.
    """
    calls = _launch_calls(monkeypatch)
    pytester.makefile(".toml", pyproject="[tool.pytest.ini_options]\n")
    sub = pytester.mkdir("pkg") / "tests"
    sub.mkdir()
    (sub / "test_a.py").write_text("def test(): assert True\n")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", "pkg/tests")
    assert result.ret == 0
    assert calls == [(str(pytester.path), [str(sub)])]


def test_launched_from_subdir_matches_terminal_pytest(pytester, monkeypatch):
    """From a subdir of rootdir, ``--deck`` scopes like ``pytest`` run there would.

    pytest's invocation-dir default (``args_source != ARGS``, no ``testpaths``)
    collects just the subdir, and the subprocess (``cwd=rootdir``) could not
    re-apply that, so the plugin forwards it. A relative positional typed in
    the subdir must resolve against the subdir too, not against rootdir.
    """
    calls = _launch_calls(monkeypatch)
    pytester.makefile(".toml", pyproject="[tool.pytest.ini_options]\n")
    pytester.makepyfile(test_top="def test(): assert True\n")
    sub = pytester.mkdir("sub")
    (sub / "test_a.py").write_text("def test(): assert True\n")
    monkeypatch.chdir(sub)

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", "test_a.py")
    assert result.ret == 0
    root = str(pytester.path)
    assert calls == [(root, [str(sub)]), (root, [str(sub / "test_a.py")])]


def test_deck_rejects_pyargs_and_dir_selection(pytester, monkeypatch):
    """``--pyargs`` and ``dir::name`` fail up front like pytest would, exit 4."""
    calls = _launch_calls(monkeypatch)
    sub = pytester.mkdir("sub")
    (sub / "test_a.py").write_text("def test(): assert True\n")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", "--pyargs", "sub")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*ERROR*--deck does not support --pyargs*"])
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", "sub::test")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*ERROR*directory argument cannot contain ::*"])
    assert calls == []


def test_testpaths_are_not_forwarded_as_targets(pytester, monkeypatch):
    """A bare ``--deck`` under a ``testpaths`` ini passes NO targets.

    pytest fills ``config.args`` from ``testpaths`` itself, but those are not
    the user's positionals: the deck hands the subprocess no positional at all
    so it applies the same ini default (see the server's default-collect test).
    """
    calls = _launch_calls(monkeypatch)
    pytester.makeini("[pytest]\ntestpaths = tests\n")
    tests_dir = pytester.mkdir("tests")
    (tests_dir / "test_in.py").write_text("def test(): assert True\n")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    assert calls == [(str(pytester.path), None)]


def test_deck_port_reaches_serve(pytester, monkeypatch):
    """End-to-end: ``--deck --deck-port N`` hands N to ``serve()``.

    ``plugin.serve`` is monkeypatched so no socket is bound; the assertion is
    the wiring — the option value arrives as ``port=`` with the plugin's own
    flag name for the friendly busy-port message.
    """
    monkeypatch.delenv("PYTEST_DECK_NO_LAUNCH", raising=False)
    calls = []
    monkeypatch.setattr(
        plugin,
        "serve",
        lambda rootdir, **kw: calls.append((rootdir, kw)),
    )
    pytester.makepyfile(test_sample="def test_x(): assert True")

    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(
        *_deck_pargs(), "--deck", "--deck-port", "9999"
    )
    assert result.ret == 0
    assert len(calls) == 1
    _, kw = calls[0]
    assert kw["port"] == 9999
    assert kw["port_flag"] == "--deck-port"


def test_deck_defaults_to_auto_port(pytester, monkeypatch):
    """Without ``--deck-port``, serve gets ``port=None`` (auto-fall-forward)."""
    monkeypatch.delenv("PYTEST_DECK_NO_LAUNCH", raising=False)
    calls = []
    monkeypatch.setattr(
        plugin,
        "serve",
        lambda rootdir, **kw: calls.append(kw),
    )
    pytester.makepyfile(test_sample="def test_x(): assert True")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    assert calls[0]["port"] is None


def test_deck_port_without_deck_is_inert(pytester, monkeypatch):
    """P2 pin: ``--deck-port`` WITHOUT ``--deck`` perturbs nothing.

    The outer plugin auto-loads into every pytest run on the machine, so a
    stray ``--deck-port 9000`` must not launch the server or change the run —
    the tests run normally and the launcher/serve path is never reached.
    """
    monkeypatch.delenv("PYTEST_DECK_NO_LAUNCH", raising=False)
    called = []
    monkeypatch.setattr(
        plugin,
        "_launch_server",
        lambda config, rootdir, targets=None: called.append(rootdir) or 0,
    )
    pytester.makepyfile(test_sample="def test_passes(): assert True")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck-port", "9000")
    result.assert_outcomes(passed=1)  # the test loop ran normally
    assert called == []  # server path never reached


def test_deck_rejects_nonexistent_path(pytester, monkeypatch):
    """``pytest --deck /no/such/path`` fails cleanly before launching anything.

    pytest's own "file or directory not found" check lives in the session the
    deck short-circuits, so the plugin repeats it up front (``pytest.UsageError``
    → exit 4, a clear ``ERROR:`` line, no traceback) rather than handing the
    path to the server to fail opaquely later. The launcher must never be
    reached.
    """
    called = []
    monkeypatch.setattr(
        plugin,
        "_launch_server",
        lambda config, rootdir, targets=None: called.append(rootdir) or 0,
    )
    pytester.makepyfile(test_sample="def test_x(): assert True")

    bad = str(pytester.path / "does-not-exist")
    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", bad)

    assert result.ret == pytest.ExitCode.USAGE_ERROR  # 4
    assert called == []  # launcher never invoked
    result.stderr.fnmatch_lines(["*ERROR*file or directory not found*"])
