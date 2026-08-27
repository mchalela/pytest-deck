"""Shared pytest-subprocess handshake for the collect and run paths.

P14: both paths build the same base argv and env in this one module, so they
cannot diverge. The fd-pipe write-end rides ``pass_fds``; its number rides
``PYTEST_DECK_FD``.
"""

import os
import shlex
import sys
from pathlib import Path

BASE_ENV = {
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


def base_argv(rootdir, pythonpath_dirs=None):
    """Return the pytest argv prefix shared by collect and run (before selection).

    ``pythonpath_dirs`` are the directories to place on the child's import path
    (the P12/P20 sibling-import fix, already merged with the user's ini
    ``pythonpath`` by the caller). They ride as a single ``-o pythonpath=``
    token, pytest's own option, which inserts them into ``sys.path`` at
    collection time (``pytest_load_initial_conftests``), after the interpreter
    bootstrap. That is why they stay off the ``PYTHONPATH`` env: that env
    governs the child's bootstrap, so a dir holding a module that shadows a
    stdlib name imported at startup (``signal``, ``subprocess``, ``types``,
    ``json``…) would crash the child before pytest runs (the ``deep/signal.py``
    repro).

    P15: for one key, the last ``-o`` override wins (verified: two
    ``-o pythonpath=`` tokens keep only the last), so we emit a single merged
    token, space-joined and ``shlex``-quoted (pytest coerces the ``paths``-type
    override with ``shlex.split``, so a dir containing a space must be quoted).
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "pytest_deck._inner",
        "--import-mode=importlib",
        "-p",
        "no:cacheprovider",
        # P11: xdist workers don't inherit the fd-3 pipe, so the transport goes silent.
        "-p",
        "no:xdist",
        "--rootdir",
        str(rootdir),
        # P15: neutralize ini addopts and required_plugins. With autoload disabled
        # (P13), a plugin flag like `--cov` would make every subprocess exit 4,
        # and a required_plugins check would fail the same way.
        "-o",
        "addopts=",
        "-o",
        "required_plugins=",
    ]
    if pythonpath_dirs:
        # P15: one merged token, since the last -o for a key wins. Deck dirs first,
        # then the user's ini pythonpath (the caller already appended it), so
        # neither clobbers the other and the deck's sibling dirs take precedence.
        joined = " ".join(shlex.quote(str(d)) for d in pythonpath_dirs)
        argv += ["-o", f"pythonpath={joined}"]
    return argv


def build_env(write_fd):
    """Build the child env: base flags, ``PYTEST_DECK_FD``, source-root prepend.

    P5: ``os.pipe()`` returns an arbitrary number that ``pass_fds`` preserves,
    so "fd 3" is only a convention and the plugin has to be told the real
    number.

    P17: just the deck's own ``project_root`` rides ``PYTHONPATH`` here. It is
    deck code, it has to be importable at bootstrap for
    ``-p pytest_deck._inner``, and (being the source checkout root) it never
    shadows a user module named ``signal``, ``json`` or similar. The sibling
    dirs of the collected tests stay off this env (that was the
    bootstrap-shadowing bug); they go through ``base_argv``'s
    ``-o pythonpath=`` instead, a collection-time ``sys.path`` insert.
    """
    env = {**os.environ, **BASE_ENV}
    # P15: `-o` overrides don't reach the env mechanisms, so drop those too.
    # (PYTEST_PLUGINS loads regardless of autoload-disable; the inner plugin is
    # injected via `-p`, never via this var.)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env["PYTEST_DECK_FD"] = str(write_fd)
    # Keeps ``pytest_deck._inner`` importable from a source checkout (no-op when
    # installed), prepended before the inherited value.
    project_root = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    parts = [project_root, existing] if existing else [project_root]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env
