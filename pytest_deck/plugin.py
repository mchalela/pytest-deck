"""Outer plugin: the deck's only pytest11 entry point.

Adds ``--deck`` and, when given, launches the dashboard instead of running the
tests in this process. P2: inert without ``--deck``. P1: the inner fd-emitting
plugin (``pytest_deck._inner``) is deliberately not an entry point; it is
injected only into deck-spawned subprocesses via ``-p``.
"""

import os
from pathlib import Path

import pytest

from .server import serve


def pytest_addoption(parser):
    """Register ``--deck``, a plain flag.

    The collection targets are pytest's own positionals (``pytest --deck a b``),
    never an argument of the flag itself, so the outer pytest parses, roots and
    validates them exactly as a bare ``pytest a b`` would.
    """
    group = parser.getgroup("deck", "pytest-deck interactive dashboard")
    group.addoption(
        "--deck",
        action="store_true",
        default=False,
        dest="deck",
        help="launch the pytest-deck dashboard instead of running tests in this "
        "process; the positional paths or node IDs (if any) scope the "
        "initial collection, as they would for a plain pytest run",
    )
    # P2: registering the option is inert. The value only reaches the server
    # through the --deck branch, so --deck-port without --deck changes nothing.
    group.addoption(
        "--deck-port",
        type=int,
        default=None,
        metavar="PORT",
        dest="deck_port",
        help="port for the dashboard server, which must be free (default: first "
        "free port from 8765; only meaningful with --deck)",
    )


def pytest_cmdline_main(config):
    """Take over the pytest invocation when ``--deck`` is present.

    P3: it is a firstresult hook, so returning an int stops pytest before the
    test loop (the int becomes the exit code). P2: returns None without
    ``--deck``.

    The outer pytest already ran rootdir discovery from the real argument list
    (``determine_setup`` with the positionals, or the invocation dir when there
    are none), so ``config.rootpath`` is exactly the rootdir a bare ``pytest``
    with the same arguments would report (P12). The positionals ride through
    as the initial collection targets.
    """
    if not config.getoption("deck", default=False):
        return None
    return _launch_server(config, str(config.rootpath), _initial_targets(config))


def _initial_targets(config):
    """Return the positionals as absolute targets, or None when there are none.

    The tree must match what a terminal ``pytest`` collects from the same
    place. pytest fills ``config.args`` three ways (``args_source``): the
    user's own positionals ride through as targets; a ``testpaths`` default
    is NOT forwarded, since the subprocess re-applies the same ini itself
    with no positional at all; and the invocation-dir default is forwarded
    only when that dir is below rootdir, because the subprocess runs with
    ``cwd=rootdir`` and could not re-apply it (a bare ``pytest`` from a
    subdir collects just that subdir).

    Each target resolves against the invocation dir, keeping any ``::``
    node-id suffix. A missing path, or a ``::`` selection on a directory, is
    rejected up front with pytest's own wording: the session that would
    normally report it never runs under ``--deck``, and a bad target would
    otherwise give an empty or broken tree later. ``--pyargs`` is refused
    because its module names are not paths and the flag is not forwarded.
    """
    source = config.args_source
    invocation_dir = Path(config.invocation_params.dir)
    if source is config.ArgsSource.TESTPATHS:
        return None
    if source is not config.ArgsSource.ARGS and invocation_dir == config.rootpath:
        return None
    if config.getoption("pyargs", default=False):
        raise pytest.UsageError("--deck does not support --pyargs")
    targets = []
    for arg in config.args:
        path_part, sep, node_part = arg.partition("::")
        path = invocation_dir / path_part
        if not path.exists():
            raise pytest.UsageError(f"file or directory not found: {arg}")
        if node_part and path.is_dir():
            raise pytest.UsageError(
                f"directory argument cannot contain :: selection parts: {arg}"
            )
        targets.append(str(path.resolve()) + sep + node_part)
    return targets


def _launch_server(config, rootdir, targets=None):
    """Launch the dashboard server; return an exit code.

    Factored out so tests can monkeypatch it (or set ``PYTEST_DECK_NO_LAUNCH``).
    """
    if os.environ.get("PYTEST_DECK_NO_LAUNCH"):
        # Test/CI guard: prove the option wired through without binding a socket.
        suffix = f" (targets {' '.join(targets)})" if targets else ""
        print(f"pytest-deck: would launch dashboard for {rootdir}{suffix}")
        return pytest.ExitCode.OK

    # None lets serve() fall forward from 8765; an explicit port binds exactly
    # that or fails loudly.
    port = config.getoption("deck_port", default=None)
    serve(rootdir, port=port, initial_targets=targets, port_flag="--deck-port")
    return pytest.ExitCode.OK
