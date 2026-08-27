"""Spawn a one-shot ``pytest --collect-only`` subprocess and parse the result.

Synchronous on purpose (B10: served from the threadpool): collect is a
request/response one-shot, so streaming buys nothing. The async run path lives
in ``runner.py``.
"""

import json
import os
import subprocess
import threading
from pathlib import Path

from pytest import ExitCode

from ._subprocess import base_argv, build_env
from .import_paths import import_dirs, pkg_roots_for_files, pythonpath_argv_dirs

# Clean collect exits: OK, or NO_TESTS_COLLECTED (an empty suite is not an error).
_OK_COLLECT_CODES = (ExitCode.OK, ExitCode.NO_TESTS_COLLECTED)


class CollectionError(RuntimeError):
    """Raised when pytest fails to collect (e.g. import error in a test file)."""


def _run_pytest(rootdir, extra_argv, dirs=None):
    """Spawn pytest with the inner plugin; return (returncode, stdout, fd3_text).

    Handles the fd-3 pipe handshake, env, import path (`-o pythonpath=`), and
    draining fd 3 in a background thread so a large payload can't deadlock
    against stdout.
    ``dirs`` are the collected-test import dirs (P12 sibling-import fix).
    """
    rootdir = Path(rootdir).resolve()

    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)

    # P20: sibling dirs reach the child as `-o pythonpath=` (a collection-time
    # sys.path insert, so no bootstrap shadowing), merged with the user's ini
    # pythonpath so we never clobber it. build_env keeps only the deck
    # source-root on the PYTHONPATH env.
    pp_dirs = pythonpath_argv_dirs(rootdir, dirs)
    argv = base_argv(rootdir, pythonpath_dirs=pp_dirs) + list(extra_argv)
    env = build_env(write_fd)

    fd3_chunks = []

    def _drain():
        # Drain fd-3 concurrently with stdout; reading it after communicate()
        # would deadlock once the payload exceeds the pipe buffer.
        with os.fdopen(read_fd, "r", errors="replace") as pipe:
            fd3_chunks.append(pipe.read())

    reader_thread = threading.Thread(target=_drain)
    reader_thread.start()

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(rootdir),
            env=env,
            pass_fds=(write_fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        # Parent doesn't write; close its copy so EOF propagates on child exit.
        os.close(write_fd)

    captured = proc.communicate()[0]
    reader_thread.join()

    return proc.returncode, captured, "".join(fd3_chunks)


def _iter_payloads(raw):
    """Yield each ``$deck`` JSON object found among the fd-3 lines.

    Twin of the run path (``_Run._dispatch_fd3``): one poison line should not
    sink the whole collect parse, so the catch is deliberately broad.
    ``json.loads`` raises beyond ``JSONDecodeError`` (``RecursionError`` on a
    deeply nested line; the fd is read with ``errors="replace"`` so decode
    errors can't reach here), and the ``isinstance`` guard covers valid-JSON
    non-dict lines (``"$deck" in 42`` is a TypeError).
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and "$deck" in obj:
            yield obj


def _import_error(errors):
    """Return True if any error record looks like an import-time collect failure.

    Pass-2 trigger (P20): a top-level sibling import that failed during
    collection because its dir wasn't yet on the path. We match on the
    longrepr text since collect_error records carry no structured excinfo.
    """
    for err in errors:
        text = err.get("longrepr_text") or ""
        if "ModuleNotFoundError" in text or "ImportError" in text:
            return True
    return False


def _parse(raw):
    """Split fd-3 payloads into (items, errors, payload_seen)."""
    payload = None
    errors = []
    for obj in _iter_payloads(raw):
        kind = obj.get("$deck")
        if kind == "collection" and payload is None:
            payload = obj
        elif kind == "collect_error":
            errors.append(
                {
                    "nodeid": obj.get("nodeid", ""),
                    "path": obj.get("path", ""),
                    "longrepr_text": obj.get("longrepr_text"),
                }
            )
    items = payload.get("items", []) if payload is not None else []
    return items, errors, payload is not None


def _erroring_sibling_dirs(errors, rootdir):
    """Sibling dirs of the files that raised an import-time collect error.

    Pass-2 sibling inject (P20): a test, or a mid-level ``conftest.py`` in a
    test-less dir, whose top-level sibling import needs its own dir on the
    path. For an error on a test file we add the file's parent; a conftest
    error reports at the directory node, so we add that dir itself. Either way
    it is the erroring file's own dir, never a downward walk into a vendored
    tree.
    """
    rootdir = Path(rootdir).resolve()
    dirs = set()
    for err in errors:
        path_part = str(err.get("path") or err.get("nodeid") or "").split("::", 1)[0]
        if not path_part:
            continue
        path = Path(path_part)
        if not path.is_absolute():
            path = rootdir / path
        path = path.resolve()
        dirs.add(str(path if path.is_dir() else path.parent))
    return dirs


def collect(rootdir, targets=None, plugin_argv=None):
    """Collect under ``rootdir``; return ``{"items": [...], "errors": [...]}``.

    Mirrors vanilla pytest: per-file import errors don't sink the collection,
    so good items come back alongside error records (pytest's ERRORS section).
    Items are ``{nodeid, path, name, markers}``; errors are ``{nodeid, path,
    longrepr_text}`` (ANSI-coloured). ``targets`` narrows to paths or node IDs.
    Raises ``CollectionError`` only when there are neither items nor errors.

    ``plugin_argv`` carries the enabled collect-scoped plugins'
    ``-p <id>`` switches (``manifests.compile_collect_argv``, following the
    scope-split rule: the switch is all that ever rides collect). The tokens
    ride both P20 passes (they're part of ``extra`` below), and the deck's
    ``-p no:`` blocks are re-asserted after them (P11: a plain ``-p name``
    unblocks an earlier ``-p no:name``, and the last ``-p`` wins), which is
    defense in depth even though the server validates the ids.

    P20 two-pass collect resolves the file-set chicken-and-egg: collect with no
    targets doesn't yet know which files are tests, yet top-level sibling
    imports run during collection. Pass 1 injects a minimal import path
    (``rootdir`` alone, or the explicit targets' pkg_roots); only if pass 1 hits
    an import-time collect error do we widen (pass 2) with the collected files'
    pkg_roots plus the erroring files' sibling dirs. It is never a downward walk
    into a vendored tree that would shadow a stdlib module (GriSPy
    ``scipy/signal``).
    """
    extra = ["--collect-only", "-q"]
    if plugin_argv:
        extra += list(plugin_argv)
        # P11: re-assert the deck's blocks after the plugin `-p` tokens so they
        # always come last (mirrors _Run._argv's guard on the run path).
        extra += ["-p", "no:xdist", "-p", "no:cacheprovider"]
    # A positional path overrides the user's ``testpaths`` ini. So on a default
    # collect (no targets) we pass no positional at all and let pytest apply
    # testpaths or its own cwd default (cwd is rootdir), exactly as bare
    # ``pytest`` does, so the deck collects what the user's terminal ``pytest``
    # collects. Explicit targets (``?targets=`` or ``--deck PATH``) still ride
    # as positionals and override testpaths: the user asked for that path,
    # mirroring ``pytest PATH``.
    if targets:
        extra += targets

    # Pass 1 (P20): a minimal import path. With explicit targets that's their
    # prepend pkg_roots (what a subsequent run uses); with none, just rootdir.
    pass1_dirs = import_dirs(rootdir, targets)
    code, captured, raw = _run_pytest(rootdir, extra, dirs=pass1_dirs)
    items, errors, seen = _parse(raw)

    # Pass 2 (P20): only on an import-time collect error. Widen the path with
    # the pkg_roots of the files that did collect plus the erroring files'
    # sibling dirs, then re-collect. This restores the P12 sibling-import fix
    # without ever injecting a vendored/nested dir that holds no collected test.
    if _import_error(errors):
        collected = [item.get("path", "") for item in items]
        widened = set(pkg_roots_for_files(collected, rootdir))
        widened.update(pass1_dirs)
        widened.update(_erroring_sibling_dirs(errors, rootdir))
        code, captured, raw = _run_pytest(rootdir, extra, dirs=sorted(widened))
        items, errors, seen = _parse(raw)

    # Clean exits and INTERRUPTED-with-error-records (per-file import errors) all
    # yield usable data; only no collection line and no errors is a hard failure.
    if not seen and not errors:
        if code in _OK_COLLECT_CODES:
            # Clean exit, no payload: odd but benign, so render an empty tree.
            return {"items": [], "errors": []}
        raise CollectionError(
            f"pytest collection failed (exit code {code}).\n\n{captured.strip()}"
        )

    return {"items": items, "errors": errors}
