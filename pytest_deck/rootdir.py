"""Mirror pytest's own rootdir discovery for the ``--deck PATH`` case.

The deck has to root itself exactly where a bare ``pytest PATH`` would, so a
launch that points at a *subdirectory* (``--deck examples/api``) still finds the
project root by walking up to the config anchor (``pyproject.toml`` /
``pytest.ini`` / ``tox.ini`` / ``setup.cfg`` / ``setup.py``), rather than pinning
rootdir to the subdir. Getting this wrong is not cosmetic: the run subprocess
pins ``cwd=rootdir`` (P12), so a too-deep rootdir makes coverage.py key source
files above cwd as absolute paths, which the ``/api/coverage`` under-rootdir
security gate then (correctly) rejects. Fixing rootdir at the source is the only
safe fix; loosening the gate is not (cov.json keys are attacker-controllable).

For the bare ``--deck`` (no PATH) case the outer pytest has already run this same
search from its invocation dir, so ``config.rootpath`` is reused as-is. This
module is needed only to re-derive rootdir from a target other than the one the
outer invocation used.

We call pytest's own ``determine_setup`` rather than reimplement its algorithm,
so the deck can never drift from pytest. Its signature differs across supported
majors (pytest 8 has no ``override_ini`` and returns a 3-tuple; pytest 9 adds it
and returns a 4-tuple), so we pass only the kwargs it declares and read the first
element (the rootdir), the part of the contract that is stable across both.
"""

import inspect
import shlex
from pathlib import Path

from _pytest.config.findpaths import determine_setup


def discover_rootdir(target, invocation_dir):
    """Return the rootdir pytest would pick for ``pytest <target>``.

    ``target`` is the ``--deck PATH`` (absolute or relative to ``invocation_dir``);
    ``invocation_dir`` is the real cwd pytest was launched from. Mirrors pytest by
    delegating to its ``determine_setup`` with ``target`` as the sole positional
    arg: the config-file upward search and the setup.py / common-ancestor
    fallbacks are pytest's, not ours.
    """
    invocation_dir = Path(invocation_dir).resolve()
    kwargs = {
        "inifile": None,
        "override_ini": [],
        "args": [str(target)],
        "rootdir_cmd_arg": None,
        "invocation_dir": invocation_dir,
    }
    # Drop kwargs the installed pytest doesn't declare (pytest 8 vs 9 signature).
    accepted = inspect.signature(determine_setup).parameters
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    rootdir = determine_setup(**kwargs)[0]
    return str(Path(rootdir).resolve())


def _read_inicfg(rootdir):
    """Return ``(inipath, inicfg)`` via pytest's ``determine_setup``.

    The shared discovery step behind both ini readers (``read_ini_pythonpath``
    and ``read_ini_addopts``): faithful across all four config sources
    (``pytest.ini`` / ``pyproject.toml`` / ``setup.cfg`` / ``tox.ini``) because
    it is pytest's own search. Degrades to ``(None, {})`` on a malformed ini or
    any discovery failure, and never raises: both readers run eagerly in the
    parent process, and the child subprocess surfaces the real ini error
    cleanly.
    """
    rootdir = Path(rootdir).resolve()
    kwargs = {
        "inifile": None,
        "override_ini": [],
        "args": [str(rootdir)],
        "rootdir_cmd_arg": None,
        "invocation_dir": rootdir,
    }
    accepted = inspect.signature(determine_setup).parameters
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    try:
        result = determine_setup(**kwargs)
    except Exception:
        return None, {}
    # pytest 8 returns a 3-tuple (rootdir, inipath, inicfg); pytest 9 returns a
    # 4-tuple with args appended. inipath and inicfg are elements 1 and 2 in both.
    inipath = result[1]
    inicfg = result[2] if len(result) > 2 else {}
    if inipath is None or not inicfg:
        return None, {}
    return inipath, inicfg


def _ini_tokens(raw):
    """Coerce one raw ini value into a token list: the pinned recipe.

    Verified 2026-08-11 on pytest 8.4.2 and 9.1.1, shared by both ini readers
    so they can never drift on the pytest-9 native-TOML edge:

    * unwrap ``ConfigValue`` via ``getattr`` ``mode``/``value``, defaulting
      ``mode`` to ``"ini"`` (pytest 8 stores the bare value, no wrapper);
    * a ``str`` with ``mode == "toml"`` degrades to ``[]``, because a string
      under pytest 9's native ``[tool.pytest]`` section is a config pytest
      itself raises TypeError on, so there are no faithful tokens to produce;
    * any other ``str`` goes through ``shlex.split`` (pytest's own "args" and
      "paths" coercion), and unbalanced quoting (``ValueError``) gives ``[]``;
    * a list of all-``str`` entries becomes a copy;
    * anything else becomes ``[]``.
    """
    mode = getattr(raw, "mode", "ini")
    raw = getattr(raw, "value", raw)
    if isinstance(raw, str):
        if mode == "toml":
            return []
        try:
            return shlex.split(raw)
        except ValueError:
            return []
    if isinstance(raw, list) and all(isinstance(t, str) for t in raw):
        return list(raw)
    return []


def read_ini_addopts(rootdir):
    """Return the user's ini ``addopts`` as a token list.

    Feeds the one-path-per-token addopts pipeline (``manifests.
    classify_addopts``): every token either prefills a manifest field
    (harvest), is re-admitted at run time under an enabled manifest's ``flags``
    namespace, or surfaces as an extra-args suggestion. The ini is the only
    source: the env ``PYTEST_ADDOPTS`` channel stays popped (P15), and the
    ``-o addopts=`` neutralization on the child argv is untouched
    (re-admission means explicit deck-appended tokens, never un-stripping).

    Faithful-tokens contract: the same tokens pytest itself would see.
    Full-line ini comments are stripped by iniconfig upstream, multiline values
    join, and a trailing comment becomes bogus tokens in pytest too (they
    classify as positionals, so they land in the leftovers downstream; no
    special handling here). Returns ``[]`` when there is no ini, no ``addopts``
    key, or on any malformed-ini degrade (``_read_inicfg``/``_ini_tokens``).
    """
    _, inicfg = _read_inicfg(rootdir)
    raw = inicfg.get("addopts")
    if raw is None:
        return []
    return _ini_tokens(raw)


def read_ini_pythonpath(rootdir):
    """Return the user's ini ``pythonpath`` as absolute dir strings, in order.

    P15/P20: the deck injects its sibling-import dirs via ``-o pythonpath=`` (a
    collection-time ``sys.path`` insert, rather than the ``PYTHONPATH`` env,
    which would shadow a stdlib module at the child's bootstrap). A ``-o``
    override replaces the ini value (the last ``-o`` wins), so the deck merges
    the user's own ``pythonpath`` into that one token instead of silently
    clobbering it. We read it via pytest's ``determine_setup`` so discovery is
    faithful across all four config sources (``pytest.ini`` /
    ``pyproject.toml`` / ``setup.cfg`` / ``tox.ini``) and can never drift from
    pytest.

    Faithful to pytest's ``paths``-type coercion (``_pytest.config``
    ``_getini_ini``): a ``str`` value is ``shlex.split`` (whitespace-separated,
    quote-aware); a ``list`` (TOML) is used as-is; each entry resolves against
    the *inifile's directory* (``inipath.parent``), not the invocation dir.
    **Order is preserved** (dedup keeps the first occurrence), because pytest's
    ``pythonpath`` is order-significant: it ``sys.path.insert(0, …)`` in
    reverse, so the first-listed dir shadows later ones. Sorting would invert
    that and diverge from the user's terminal pytest.

    Returns ``[]`` when there is no ini or no ``pythonpath`` key. A malformed
    ini degrades to ``[]`` as well, and never raises: ``determine_setup`` raises
    ``UsageError`` on a broken ini, but this runs eagerly in the parent process
    on every collect and run, so letting it propagate would turn pytest's own
    clean child-side error (exit 4, surfaced as a tidy ``CollectionError``) into
    an uncaught 500. Degrading here lets the child report the real ini error
    through its normal path.

    Coercion rides the shared ``_ini_tokens`` recipe so this reader can't drift
    from ``read_ini_addopts`` on the pytest-9 native-TOML edge: a
    ``[tool.pytest]`` pythonpath given as a string is a config pytest itself
    raises TypeError on, so it degrades to ``[]`` here too (it was previously
    shlex-split, a divergence that was harmless on invalid config).
    """
    inipath, inicfg = _read_inicfg(rootdir)
    raw = inicfg.get("pythonpath")
    if inipath is None or raw is None:
        return []
    entries = _ini_tokens(raw)
    base = Path(inipath).parent
    # Order-preserving dedup, never sorted: pythonpath order is significant.
    dirs = []
    seen = set()
    for entry in entries:
        resolved = str((base / entry).resolve())
        if resolved not in seen:
            seen.add(resolved)
            dirs.append(resolved)
    return dirs
