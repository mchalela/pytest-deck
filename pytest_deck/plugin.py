"""Outer plugin: the deck's only pytest11 entry point.

Adds ``--deck`` and, when given, launches the dashboard instead of running the
tests in this process. P2: inert without ``--deck``. P1: the inner fd-emitting
plugin (``pytest_deck._inner``) is deliberately not an entry point; it is
injected only into deck-spawned subprocesses via ``-p``.
"""

import os

import pytest

from .rootdir import discover_rootdir
from .server import serve


def pytest_addoption(parser):
    """Register ``--deck [PATH]``.

    nargs="?"+const/default distinguishes "--deck" (empty-string sentinel)
    from "--deck PATH" from absent (None).
    """
    group = parser.getgroup("deck", "pytest-deck interactive dashboard")
    group.addoption(
        "--deck",
        nargs="?",
        const="",  # bare ``--deck``: the empty-string sentinel
        default=None,  # ``--deck`` absent: None
        metavar="PATH",
        dest="deck",
        help="launch the pytest-deck dashboard against PATH (default: rootdir), "
        "instead of running tests in this process",
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
    """
    deck = config.getoption("deck", default=None)
    if deck is None:
        return None

    if not deck:
        # Bare ``--deck``: the outer pytest already ran rootdir discovery from its
        # invocation dir, so reuse its answer (it walks up to the config anchor).
        rootdir = str(config.rootpath)
        target = None
    else:
        # ``--deck PATH``: validate up front, otherwise it fails opaquely later
        # inside the subprocess. UsageError is pytest's clean path: an ERROR
        # line and exit 4.
        if not os.path.isdir(deck):
            raise pytest.UsageError(
                f"--deck: {deck!r} is not a directory (path does not exist "
                f"or is not a directory)"
            )
        # Mirror pytest: rootdir walks up from PATH to the config anchor, while
        # PATH itself stays the initial collection target (like ``pytest PATH``).
        target = os.path.abspath(deck)
        rootdir = discover_rootdir(target, config.invocation_params.dir)

    return _launch_server(config, rootdir, target)


def _launch_server(config, rootdir, target=None):
    """Launch the dashboard server; return an exit code.

    Factored out so tests can monkeypatch it (or set ``PYTEST_DECK_NO_LAUNCH``).
    """
    if os.environ.get("PYTEST_DECK_NO_LAUNCH"):
        # Test/CI guard: prove the option wired through without binding a socket.
        suffix = f" (target {target})" if target else ""
        print(f"pytest-deck: would launch dashboard for {rootdir}{suffix}")
        return pytest.ExitCode.OK

    # None lets serve() fall forward from 8765; an explicit port binds exactly
    # that or fails loudly.
    port = config.getoption("deck_port", default=None)
    serve(rootdir, port=port, initial_target=target, port_flag="--deck-port")
    return pytest.ExitCode.OK
