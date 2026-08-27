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
    result.stdout.fnmatch_lines(["*--deck=*"])
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

    def fake_launch(config, rootdir, target=None):
        calls.append((rootdir, target))
        return 0

    monkeypatch.setattr(plugin, "_launch_server", fake_launch)
    pytester.makepyfile(test_sample="def test_x(): assert True")

    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck")
    assert result.ret == 0
    assert len(calls) == 1
    rootdir, target = calls[0]
    # Bare ``--deck``: rootdir is the discovered path (non-empty), no subtree target.
    assert rootdir and rootdir != ""
    assert target is None


def test_launch_server_honors_explicit_path(pytester, monkeypatch):
    """``--deck PATH`` derives rootdir via pytest and keeps PATH as the target.

    ``pytester.path`` has no config anchor, so pytest's rootdir for it is the
    dir itself (common-ancestor fallback) — rootdir == PATH here, and PATH also
    rides through as the initial collection target (mirrors ``pytest PATH``).
    """
    calls = []
    monkeypatch.setattr(
        plugin,
        "_launch_server",
        lambda config, rootdir, target=None: calls.append((rootdir, target)) or 0,
    )
    pytester.makepyfile(test_sample="def test_x(): assert True")

    # An existing directory passes validation; tmp_path is the cwd here.
    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", str(pytester.path))
    assert result.ret == 0
    assert calls == [(str(pytester.path), str(pytester.path))]


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
        lambda config, rootdir, target=None: called.append(rootdir) or 0,
    )
    pytester.makepyfile(test_sample="def test_passes(): assert True")

    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck-port", "9000")
    result.assert_outcomes(passed=1)  # the test loop ran normally
    assert called == []  # server path never reached


def test_deck_rejects_nonexistent_path(pytester, monkeypatch):
    """``--deck /no/such/dir`` fails cleanly before launching anything.

    A bad path must be rejected up front (``pytest.UsageError`` → exit 4, a
    clear ``ERROR:`` line, no traceback) rather than handed to the server to
    fail opaquely later. The launcher must never be reached.
    """
    called = []
    monkeypatch.setattr(
        plugin,
        "_launch_server",
        lambda config, rootdir, target=None: called.append(rootdir) or 0,
    )
    pytester.makepyfile(test_sample="def test_x(): assert True")

    bad = str(pytester.path / "does-not-exist")
    # Hermetic force-load under autoload-disable (P13); see _deck_pargs.
    result = pytester.runpytest_inprocess(*_deck_pargs(), "--deck", bad)

    assert result.ret == pytest.ExitCode.USAGE_ERROR  # 4
    assert called == []  # launcher never invoked
    result.stderr.fnmatch_lines(["*ERROR*is not a directory*"])
