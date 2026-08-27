"""Discover the directories to add to the child's import path.

The deck runs subprocesses with ``--import-mode=importlib`` (P12) because it is
the one mode that collects a ``__init__.py``-less tree with duplicate test-file
basenames without crashing. importlib deliberately never mutates ``sys.path``,
so a test's top-level ``from sibling import helper`` (helper.py adjacent, no
package) works in the user's terminal under the default ``prepend`` mode but
fails with ``ModuleNotFoundError`` in the deck.

We mirror ``prepend`` mode exactly (P20): pytest's ``prepend`` adds one dir per
collected file, its package root (walk up while ``__init__.py`` exists, or the
file's own dir when there is none). We inject precisely that set by calling
pytest's own ``_pytest.pathlib.resolve_pkg_root_and_module_name`` per file, so
the deck can never drift from pytest and, critically, never puts a vendored or
nested dir that holds no collected test on the path. A downward tree walk (the
old behaviour) would add e.g. a vendored ``scipy/signal/`` and shadow the
stdlib ``signal`` mid-collection (GriSPy), a collection-fatal fidelity bug.

P20 (bootstrap-shadow fix): these dirs ride the child's ``-o pythonpath=``
token, a collection-time ``sys.path`` insert, rather than the ``PYTHONPATH``
env (which governs the interpreter bootstrap, where a ``deep/signal.py`` would
shadow the stdlib ``signal`` before pytest runs). ``pythonpath_argv_dirs``
merges them with the user's ini ``pythonpath`` so the single ``-o`` token (the
last one wins) composes with the user's config instead of clobbering it.
"""

import inspect
from pathlib import Path

from _pytest.pathlib import (
    CouldNotResolvePathError,
    resolve_pkg_root_and_module_name,
)

from .rootdir import read_ini_pythonpath

# resolve_pkg_root_and_module_name gained `consider_namespace_packages` in
# pytest 8.2; both 8.x and 9.x carry it. Signature-filter defensively (same
# cross-version pattern as rootdir.py) so an older/newer pytest can't break us.
_RESOLVE_PARAMS = inspect.signature(resolve_pkg_root_and_module_name).parameters


def _pkg_root(path, ns):
    """Return the prepend pkg_root for ``path`` (its own dir if no package)."""
    kwargs = {"consider_namespace_packages": ns}
    kwargs = {k: v for k, v in kwargs.items() if k in _RESOLVE_PARAMS}
    try:
        return resolve_pkg_root_and_module_name(path, **kwargs)[0]
    except CouldNotResolvePathError:
        # No __init__.py chain, so prepend mode puts the file's own dir on the
        # path (the bare sibling-import case P12 exists to fix).
        return path.parent


def pkg_roots_for_files(files, rootdir, ns=False):
    """Return sorted absolute dirs = the prepend pkg_roots of ``files``.

    Mirrors pytest ``prepend`` mode (P20): one dir per existing file, its
    package root, or its own dir when it belongs to no package. ``rootdir`` is
    always included. Non-existent paths (stale nodeids) are skipped for pytest
    to report.
    """
    rootdir = Path(rootdir).resolve()
    dirs = {rootdir}
    for f in files:
        path = Path(f)
        if not path.is_absolute():
            path = rootdir / path
        path = path.resolve()
        if path.is_file():
            dirs.add(_pkg_root(path, ns))
    return sorted(str(d) for d in dirs)


def import_dirs(rootdir, targets=None):
    """Return sorted absolute dirs to place on the child's import path.

    Always includes ``rootdir``. Each target's file part (everything before the
    first ``::``) contributes its prepend package root, mirroring what the
    user's terminal ``pytest`` (prepend mode) would put on ``sys.path`` for
    that file, and nothing more. Targets may be node IDs (``path::test``) or
    plain paths, absolute or ``rootdir``-relative. With no targets only
    ``rootdir`` comes back; the caller (collector) resolves the file-set
    chicken-and-egg with its two-pass collect.
    """
    rootdir = Path(rootdir).resolve()
    files = []
    for target in targets or []:
        path_part = str(target).split("::", 1)[0]
        if path_part:
            files.append(path_part)
    return pkg_roots_for_files(files, rootdir)


def pythonpath_argv_dirs(rootdir, deck_dirs):
    """Compose the ``-o pythonpath=`` dir list: deck dirs then the user's ini.

    P20: ``deck_dirs`` are the deck's sibling-import pkg_roots (from
    ``import_dirs`` / ``pkg_roots_for_files``). We append the user's configured
    ini ``pythonpath`` (resolved absolute by ``read_ini_pythonpath``) so the
    single ``-o pythonpath=`` token (the last ``-o`` wins) leaves the user's
    config intact. Deck dirs come first (they take ``sys.path`` precedence);
    duplicates are dropped while preserving that order.
    """
    ordered = []
    for d in list(deck_dirs or []) + read_ini_pythonpath(rootdir):
        if d not in ordered:
            ordered.append(d)
    return ordered
