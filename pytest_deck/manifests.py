"""Plugin manifests: the control facet of plugin interop.

A manifest is a declarative TOML file describing one pytest plugin the deck can
switch on: identity, a small typed config schema, and per-field argv templates.
Curated manifests ship in ``pytest_deck/manifests/``; the user scan
(``.pytest-deck/plugins/*.toml`` under rootdir) reuses the same
``parse_manifest`` and validation.

Trust: curated manifests are code the project ships; user manifests are
untrusted TOML from the target repo. A user manifest may set any argv tokens or
transport (the user already runs their own test code on localhost, so argv as
tokens is theirs to control), but its ``[env]`` table is applied to the run
subprocess after ``build_env``, so it cannot be allowed to shadow the
deck-integrity vars (``RESERVED_ENV``: the fd number, autoload-disable, the
import path, the P15-neutralized channels). ``parse_manifest`` enforces this for
user-sourced documents (``trusted=False``) and rejects the whole manifest (never
silently drops the key) so the author sees why. Curated manifests skip the check
(``trusted=True``), so they may legitimately set reserved vars like
``COVERAGE_FILE``, which is reserved for user manifests because pytest-cov
writes to that path and an untrusted repo could aim it at an arbitrary file to
clobber (P17).

Identity rule: ``Manifest.id`` is the plugin's ``pytest11`` entry-point name,
exactly the token ``-p`` resolves under ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` (P13),
and the annotation-channel key going forward. Dist name is display-only.

Argv compilation is a pure function from (manifest, config) to a token list,
never to a shell string. Templates substitute ``{value}`` literally (no
``str.format``), so user-typed values cannot inject template syntax.
"""

import importlib.metadata
import importlib.resources
import shlex
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .plugin_data import SLIMMERS

_SCOPES = ("run", "collect", "both")
_FIELD_TYPES = ("string", "bool")
_RENDERS = ("json", "text", "artifacts")

# The index_format values a first-party artifact_dir parser exists for. A closed
# set, one first-party parser per format; see plugin_data.INDEX_PARSERS.
_INDEX_FORMATS = ("mpl",)

_MANIFEST_KEYS = {
    "id",
    "label",
    "dist",
    "scope",
    "fields",
    "flags",
    "env",
    "transport",
    "render",
    "disabled_reason",
}
_FIELD_KEYS = {"key", "label", "type", "default", "arg", "arg_empty"}
_TRANSPORT_KEYS = {"type", "arg", "path", "root", "index", "index_format"}
# json_file and text_file back the generic render="json"/"text" surfaces, and
# json_file also backs the first-party coverage slimmer. artifact_dir backs
# render="artifacts": the plugin writes files under a run-scoped dir plus an
# index file the deck's first-party parser reads. With fd3 the payload arrives
# as a first-party `$deck` record on the deck's own fd-3 pipe: no file, no argv
# token, curated-only (see _parse_fd3_transport).
_TRANSPORT_TYPES = ("json_file", "text_file", "artifact_dir", "fd3")

# SECURITY: env-var names a user manifest's [env] table may never set. That env
# is applied after build_env, so any of these would either subvert deck
# integrity or, in the arbitrary-file-write case, turn a hostile repo into a
# "destroy any file the user can write" oracle:
#   * PYTEST_DECK_FD is the fd-3 transport number (P5); a wrong value silences
#     or misdirects every result.
#   * PYTEST_DISABLE_PLUGIN_AUTOLOAD is the P13 autoload-disable that buys
#     determinism and closes the xdist hazard.
#   * PYTHONPATH carries the deck's own source root, which keeps
#     `-p pytest_deck._inner` importable at bootstrap from a source checkout
#     (P20; a no-op when installed). The sibling-import dirs ride
#     `-o pythonpath=` on argv instead. Clobbering it breaks that bootstrap or
#     injects arbitrary imports (a read/exec vector too).
#   * PYTHONDONTWRITEBYTECODE and PYTHONUNBUFFERED are BASE_ENV run hygiene.
#   * PYTEST_ADDOPTS and PYTEST_PLUGINS are popped by P15; re-adding them
#     re-opens the plugin-loading channels P15 closed.
#   * COLUMNS and LINES are the fixed pty geometry the runner sets.
#   * COVERAGE_FILE is an arbitrary file write. pytest-cov writes a SQLite DB to
#     this path, so a user [env] COVERAGE_FILE="/home/victim/.bashrc" overwrites
#     that file when coverage runs. The deck runs against arbitrary checked-out
#     repos, so this is a real destroy-any-writable-file vector. The curated
#     coverage manifest still sets it (trusted code bypasses this gate) and
#     pins it under the run tmpdir.
# Assessed as no-ops or self-DoS under non-interactive `python -m pytest`:
# PYTHONSTARTUP, PYTHONINSPECT and PYTHONEXECUTABLE (no-ops) and PYTHONHOME
# (self-DoS). COVERAGE_FILE is the only file-write vector the child's env
# exposes; nothing else the deck sets redirects output to a user-chosen path.
RESERVED_ENV = frozenset(
    {
        "PYTEST_DECK_FD",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "COLUMNS",
        "LINES",
        "COVERAGE_FILE",
    }
)

# SECURITY: flags that are never auto-re-admitted from ini addopts, whatever
# `flags` namespace a manifest declares. User manifests declare namespaces too,
# so a namespace alone must not be able to grant these: a hostile
# `flags = ["-o*"]` plus an ini `-o pythonpath=/evil` must not clobber P20.
# Each entry guards a deck-integrity argv mechanism:
#   * -p sets plugin load/block ordering (P11: last -p wins).
#   * -o / --override-ini: the P15 `-o addopts=`/`required_plugins=`
#     neutralization and the P20 `-o pythonpath=` inject are last-wins
#     overrides that a later -o would clobber.
#   * -c swaps the config file the child parses.
#   * --rootdir: P12 keeps rootdir fixed so nodeids stay stable (they are the
#     UI's keys).
#   * --import-mode: P12 relies on importlib for dup-basename trees.
# Matching is unforgeable by construction (_is_reserved_flag). Long options
# match on the exact name or its `=`-form (`--rootdir=/evil`; pytest rejects
# abbreviations). Short options are matched by scanning the whole grouped
# cluster, because pytest groups short options (`-sq` == `-s -q`) and the first
# value-taking one swallows the rest of the token at any position. So a
# reserved short letter riding as a non-leading char (`-xopythonpath=/evil` ==
# `-x` + `-o pythonpath=/evil`, `-spxdist` == `-s` + `-p xdist`) is caught, not
# just a leading `-o`/`-p`/`-c`.
# The denylist deliberately does not apply to the leftover suggestions (step 3):
# clicking one into extra-args is the user's decided-safe tier-2 surface.
RESERVED_FLAGS = frozenset(
    {"-p", "-o", "--override-ini", "-c", "--rootdir", "--import-mode"}
)
# The single-letter reserved short options (each takes a value: `-p name`,
# `-o k=v`, `-c file`). Derived from RESERVED_FLAGS so the two cannot drift;
# _is_reserved_flag scans a short cluster for any of these.
_RESERVED_SHORT = frozenset(
    f[1] for f in RESERVED_FLAGS if len(f) == 2 and f.startswith("-")
)


class ManifestError(Exception):
    """A curated manifest failed validation: a code error, not user input."""


class ManifestConfigError(ValueError):
    """User-supplied config doesn't match the manifest schema (server: 4xx)."""


@dataclass(frozen=True)
class ManifestField:
    """One typed config field: schema for the UI + argv template for compile.

    ``arg`` is the token emitted when the field is "on" (a non-empty string, or
    a true bool); ``{value}`` in it is replaced by the string value.
    ``arg_empty`` (string fields only) is the fallback token for an empty
    value, e.g. a bare ``--cov`` meaning "measure everything".
    """

    key: str
    label: str
    type: str
    default: object
    arg: str
    arg_empty: str = None


@dataclass(frozen=True)
class Manifest:
    """One plugin the deck can enable: identity, scope, config fields, env.

    ``env`` maps env-var names to value templates applied to the run
    subprocess; ``{tmpdir}`` in a value is replaced (literally, like
    ``{value}``) with the run-scoped temp dir. ``COVERAGE_FILE`` is the shipped
    example, so enabling coverage never drops ``.coverage`` into the user's
    tree.
    """

    id: str
    label: str
    dist: str
    scope: str
    fields: tuple = field(default_factory=tuple)
    # The plugin's declared flag namespace: literal tokens ("--cov") or
    # trailing-`*` prefixes ("--cov-*"). It grants re-admission of matching
    # self-contained ini-addopts tokens at run time while this plugin is
    # enabled (classify_addopts); RESERVED_FLAGS always wins over it.
    flags: tuple = field(default_factory=tuple)
    env: dict = field(default_factory=dict)
    # {"type": "json_file", "arg": ..., "path": ...} or None. `arg` is one more
    # compiled token; `path` is what the runner reads after the child exits.
    # For render="artifacts" it is {"type": "artifact_dir", "arg": [tok, ...],
    # "root": ..., "index": ..., "index_format": ...}; an fd3 transport is just
    # {"type": "fd3"}. Every template uses literal `{tmpdir}` substitution.
    transport: dict = None
    # How the deck displays the transport payload. None means first-party
    # rendering keyed off the id (coverage). "json" or "text" is a generic
    # pass-through: the runner reads the transport file and ships its parsed
    # JSON or raw text on the `plugin_data` event's `render` discriminator, with
    # no plugin-specific code. Requires a [transport]; there is nothing to
    # render otherwise.
    render: str = None
    # Self-gating: a non-None reason marks the manifest as not yet usable. It
    # still appears on /api/plugins (flagged disabled), but the frontend greys
    # it out.
    disabled_reason: str = None


def parse_manifest(text, source="<manifest>", trusted=True):
    """Parse and strictly validate one manifest TOML document.

    ``source`` names the document in error messages. Raises ``ManifestError``
    on any unknown key, missing key, or type mismatch: curated manifests are
    code, so failures should be loud at load time, while the user scan catches
    this same error and degrades (one bad file doesn't kill the scan).

    ``trusted``: curated manifests are trusted code; a user manifest is
    untrusted TOML. When ``trusted=False`` the ``[env]`` table is additionally
    checked against ``RESERVED_ENV``, and a key that would shadow a
    deck-integrity var rejects the whole manifest (that table is applied after
    ``build_env``, so curated-code discipline no longer suffices).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{source}: invalid TOML: {exc}") from exc

    _check_keys(data, _MANIFEST_KEYS, source)
    for key in ("id", "label", "dist"):
        _check_type(data, key, str, source, required=True)
        if not data[key]:
            raise ManifestError(f"{source}: {key!r} must be non-empty")
    scope = _require(data, "scope", source)
    if scope not in _SCOPES:
        raise ManifestError(f"{source}: scope must be one of {_SCOPES}, got {scope!r}")

    raw_fields = data.get("fields", [])
    if not isinstance(raw_fields, list):
        raise ManifestError(f"{source}: 'fields' must be an array of tables")
    fields = tuple(_parse_field(f, source) for f in raw_fields)
    keys = [f.key for f in fields]
    if len(keys) != len(set(keys)):
        raise ManifestError(f"{source}: duplicate field keys in {keys}")

    flags = _parse_flags(data.get("flags", []), source)

    env = data.get("env", {})
    if not isinstance(env, dict):
        raise ManifestError(f"{source}: 'env' must be a table")
    for key, value in env.items():
        if not isinstance(value, str):
            raise ManifestError(f"{source}: env {key!r} value must be a string")
        # SECURITY: reject, never silently drop, any user-manifest env key that
        # would shadow a deck-integrity var, so the author sees exactly why.
        if not trusted and key in RESERVED_ENV:
            raise ManifestError(
                f"{source}: env {key!r} is reserved by the deck and cannot be "
                f"set by a user manifest (reserved: {sorted(RESERVED_ENV)})"
            )

    render = _parse_render(data.get("render"), source)
    transport = _parse_transport(
        data.get("transport"), data["id"], render, source, trusted
    )

    disabled_reason = data.get("disabled_reason")
    if disabled_reason is not None and not isinstance(disabled_reason, str):
        raise ManifestError(f"{source}: 'disabled_reason' must be a string")

    return Manifest(
        id=data["id"],
        label=data["label"],
        dist=data["dist"],
        scope=scope,
        fields=fields,
        flags=flags,
        env=env,
        transport=transport,
        render=render,
        disabled_reason=disabled_reason,
    )


def _parse_flags(value, source):
    """Validate the optional top-level ``flags`` namespace.

    Entries are literal option tokens (``--cov``) or trailing-``*`` prefixes
    (``--cov-*``), and nothing else. Every entry has to start with ``-``, ``*``
    may appear once and only at the end, and a wildcard's prefix has to contain
    a non-dash character: a bare or near-bare wildcard (``*``, ``-*``, ``--*``)
    would grant the plugin the entire option space, defeating the point of a
    namespace. Enforced for trusted and untrusted manifests alike, so a curated
    typo is as loud as a hostile user grant (the run-time RESERVED_FLAGS
    denylist is the security backstop; this keeps grants honest).
    """
    if not isinstance(value, list) or not all(isinstance(f, str) for f in value):
        raise ManifestError(f"{source}: 'flags' must be an array of strings")
    for entry in value:
        if not entry.startswith("-"):
            raise ManifestError(
                f"{source}: flags entry {entry!r} must start with '-' "
                f"(option tokens only)"
            )
        star = entry.find("*")
        if star == -1:
            continue
        if star != len(entry) - 1:
            raise ManifestError(
                f"{source}: flags entry {entry!r} may only use '*' as a "
                f"trailing prefix wildcard"
            )
        if not entry[:-1].lstrip("-"):
            raise ManifestError(
                f"{source}: flags entry {entry!r} is too broad: a wildcard "
                f"needs a non-dash prefix (e.g. '--cov-*')"
            )
    return tuple(value)


def _parse_render(value, source):
    """Validate the optional ``render`` field. None or one of _RENDERS."""
    if value is None:
        return None
    if value not in _RENDERS:
        raise ManifestError(
            f"{source}: render must be one of {_RENDERS} (or omitted for "
            f"first-party rendering), got {value!r}"
        )
    return value


def _parse_transport(data, manifest_id, render, source, trusted):
    """Validate an optional ``[transport]`` table.

    A transport is renderable one of two ways: a first-party slimmer keyed by
    ``manifest_id`` (coverage), or a generic ``render`` mode. One of the two
    has to apply, since a transport with neither is dead data.

    ``trusted`` gates ``artifact_dir``, for security: that transport's ``root``
    becomes the HTTP artifact endpoint's serve base, so an untrusted manifest
    declaring one is an arbitrary-file-read vector, and it is rejected here
    (gate 1). It also gates the SLIMMERS render path (the trust rule): an
    untrusted manifest has to declare an explicit ``render``, because shadowing
    a first-party slimmer id would feed arbitrary file content onto a
    first-party surface.
    """
    if data is None:
        if render is not None:
            raise ManifestError(
                f"{source}: render={render!r} requires a [transport] to read"
            )
        return None
    if not isinstance(data, dict):
        raise ManifestError(f"{source}: 'transport' must be a table")
    _check_keys(data, _TRANSPORT_KEYS, source)
    ttype = _require(data, "type", source)
    if ttype not in _TRANSPORT_TYPES:
        raise ManifestError(
            f"{source}: transport type must be one of {_TRANSPORT_TYPES}, "
            f"got {ttype!r}"
        )
    if ttype == "artifact_dir":
        return _parse_artifact_transport(data, render, source, trusted)
    if ttype == "fd3":
        return _parse_fd3_transport(data, manifest_id, render, source, trusted)
    _check_type(data, "path", str, source, required=True)
    # `arg` is a single token string or a token list (the artifact_dir
    # precedent): pytest-benchmark's save-file redirect needs both
    # --benchmark-save and --benchmark-storage. Each element stays one argv
    # token, so the argv-as-tokens discipline is unchanged (compile_argv
    # extends a list).
    arg = _require(data, "arg", source)
    if isinstance(arg, list):
        if not all(isinstance(t, str) for t in arg):
            raise ManifestError(
                f"{source}: transport 'arg' must be a string or an array of "
                f"token strings"
            )
        if not arg:
            raise ManifestError(f"{source}: transport 'arg' must be non-empty")
        arg = list(arg)
    elif not isinstance(arg, str):
        raise ManifestError(
            f"{source}: transport 'arg' must be a string or an array of "
            f"token strings"
        )
    if render is None:
        # Trust rule: an untrusted manifest with a transport must declare an
        # explicit render; it can never satisfy this gate by shadowing a
        # first-party SLIMMERS id (pytest_cov, benchmark, metadata). First-party
        # surfaces make semantic claims ("this is your coverage, benchmark,
        # environment"), and the deck must never present user-file content as
        # first-party-derived. User manifests get the generic surfaces; every
        # first-party pipeline is curated-only end to end. (A no-render user
        # shadow of pytest_cov used to pass; the tightening is deliberate.)
        if not trusted:
            raise ManifestError(
                f"{source}: a user manifest's transport must declare an "
                f"explicit render: set render = 'json' or 'text' "
                f"(first-party slimmers are reserved for curated manifests)"
            )
        # A curated transport still has to render somehow, through a
        # first-party slimmer or a generic render mode. With neither, the
        # payload has nowhere to go.
        if manifest_id not in SLIMMERS:
            raise ManifestError(
                f"{source}: transport declared but no way to render it: set "
                f"render = 'json'|'text' or register a first-party slimmer for "
                f"{manifest_id!r}"
            )
    if render == "artifacts":
        raise ManifestError(
            f"{source}: render='artifacts' requires transport type "
            f"'artifact_dir', got {ttype!r}"
        )
    return {"type": ttype, "arg": arg, "path": data["path"]}


def _parse_artifact_transport(data, render, source, trusted):
    """Validate an ``artifact_dir`` transport.

    Requires ``render='artifacts'`` plus ``root``, ``index`` and a known
    ``index_format``. It does not go through the SLIMMERS gate, because the
    first-party index parser is keyed by ``index_format`` rather than by
    manifest id. ``arg`` is a list of token strings, not one string: pytest-mpl
    needs both ``--mpl-results-path=...`` and ``--mpl-generate-summary=json``
    (otherwise a green run writes no index), so the transport compiles to more
    than one argv token while each element stays a single token, which
    preserves the argv-as-tokens discipline.

    Security invariant: ``artifact_dir`` is curated-only and tmpdir-contained,
    two independent gates. It is the read-side twin of the COVERAGE_FILE write
    vector that ``RESERVED_ENV`` blocks.

    * Gate 1 (curated-only, and the primary one): ``root`` becomes the HTTP
      artifact endpoint's serve base, so an untrusted manifest declaring
      ``artifact_dir`` could aim it at ``/`` and read any file. It is rejected
      for ``trusted=False`` documents, mirroring the RESERVED_ENV rejection
      (loud, and it explains why).
    * Gate 2 (tmpdir containment, defense in depth, and it applies to curated
      manifests too): ``root`` has to embed the literal ``{tmpdir}``
      placeholder so it can only resolve under the run tmpdir.
      ``RunManager.artifact_root`` re-verifies containment at serve time (belt
      and suspenders, so a curated-code bug still can't escape).
    """
    if not trusted:
        raise ManifestError(
            f"{source}: transport type 'artifact_dir' is reserved for curated "
            f"manifests and cannot be declared by a user manifest. Its 'root' "
            f"becomes the artifact endpoint's serve base (arbitrary-file-read "
            f"vector)"
        )
    if render != "artifacts":
        raise ManifestError(
            f"{source}: transport type 'artifact_dir' requires render='artifacts', "
            f"got render={render!r}"
        )
    for key in ("root", "index", "index_format"):
        _check_type(data, key, str, source, required=True)
        if not data[key]:
            raise ManifestError(f"{source}: transport {key!r} must be non-empty")
    # Gate 2 (parse half): root must be tmpdir-anchored so it can only ever
    # resolve under the run tmpdir; the placeholder is substituted literally,
    # like the [env] hook's. artifact_root re-checks containment after
    # substitution.
    if "{tmpdir}" not in data["root"]:
        raise ManifestError(
            f"{source}: artifact_dir transport 'root' must contain '{{tmpdir}}' "
            f"so it resolves under the run tmpdir (got {data['root']!r})"
        )
    if data["index_format"] not in _INDEX_FORMATS:
        raise ManifestError(
            f"{source}: transport index_format must be one of {_INDEX_FORMATS}, "
            f"got {data['index_format']!r}"
        )
    arg = _require(data, "arg", source)
    if not isinstance(arg, list) or not all(isinstance(t, str) for t in arg):
        raise ManifestError(
            f"{source}: artifact_dir transport 'arg' must be an array of token strings"
        )
    if not arg:
        raise ManifestError(f"{source}: artifact_dir transport 'arg' must be non-empty")
    return {
        "type": "artifact_dir",
        "arg": list(arg),
        "root": data["root"],
        "index": data["index"],
        "index_format": data["index_format"],
    }


def _parse_fd3_transport(data, manifest_id, render, source, trusted):
    """Validate an ``fd3`` transport.

    The plugin's payload arrives as a first-party ``$deck`` record on the
    deck's own fd-3 pipe: the inner plugin emits it mid-run, the runner stashes
    it, and ``_read_transports`` resolves the stash post-exit. No file is read
    and no argv token is compiled, so ``type`` is the one key allowed.

    Trust gate (the same shape as artifact_dir's gate 1, P19): fd-3 is the
    deck's structured-results channel, and only first-party inner-plugin
    records ride it (P10). There is no emitter for arbitrary plugins, so an
    ``fd3`` transport in an untrusted (user) manifest is dead surface at best
    and a claim on the deck's own transport at worst, and it is rejected
    loudly at parse time.

    Rendering is first-party only: the wire ``render`` comes from the fd-3
    resolution (``"metadata"``), keyed by a registered SLIMMERS entry. A
    generic ``render`` mode has no file to read, so it has to be omitted.
    """
    if not trusted:
        raise ManifestError(
            f"{source}: transport type 'fd3' is reserved for curated manifests "
            f"and cannot be declared by a user manifest. fd-3 is the deck's "
            f"own structured-results channel (first-party records only)"
        )
    extra = set(data) - {"type"}
    if extra:
        raise ManifestError(
            f"{source}: fd3 transport allows no keys besides 'type', "
            f"got {sorted(extra)}"
        )
    if render is not None:
        raise ManifestError(
            f"{source}: transport type 'fd3' renders via a first-party slimmer "
            f"keyed by manifest id. Omit 'render' (got {render!r})"
        )
    if manifest_id not in SLIMMERS:
        raise ManifestError(
            f"{source}: fd3 transport requires a first-party slimmer registered "
            f"for {manifest_id!r}"
        )
    return {"type": "fd3"}


def _parse_field(data, source):
    """Validate one ``[[fields]]`` table into a ManifestField."""
    if not isinstance(data, dict):
        raise ManifestError(f"{source}: each field must be a table")
    _check_keys(data, _FIELD_KEYS, source)
    for key in ("key", "label", "arg"):
        _check_type(data, key, str, source, required=True)
    ftype = _require(data, "type", source)
    if ftype not in _FIELD_TYPES:
        raise ManifestError(
            f"{source}: field type must be one of {_FIELD_TYPES}, got {ftype!r}"
        )
    default = _require(data, "default", source)
    expected = str if ftype == "string" else bool
    if not isinstance(default, expected):
        raise ManifestError(
            f"{source}: field {data['key']!r} default must be {ftype}, "
            f"got {type(default).__name__}"
        )
    arg_empty = data.get("arg_empty")
    if arg_empty is not None:
        if ftype != "string":
            raise ManifestError(f"{source}: 'arg_empty' is only valid on string fields")
        if not isinstance(arg_empty, str):
            raise ManifestError(f"{source}: 'arg_empty' must be a string")
    if ftype == "string" and "{value}" not in data["arg"]:
        raise ManifestError(
            f"{source}: string field {data['key']!r} arg must contain '{{value}}'"
        )
    return ManifestField(
        key=data["key"],
        label=data["label"],
        type=ftype,
        default=default,
        arg=data["arg"],
        arg_empty=arg_empty,
    )


def _check_keys(data, allowed, source):
    unknown = set(data) - allowed
    if unknown:
        raise ManifestError(f"{source}: unknown keys {sorted(unknown)}")


def _check_type(data, key, expected, source, required=False):
    if key not in data:
        if required:
            raise ManifestError(f"{source}: missing required key {key!r}")
        return
    if not isinstance(data[key], expected):
        raise ManifestError(
            f"{source}: {key!r} must be {expected.__name__}, "
            f"got {type(data[key]).__name__}"
        )


def _require(data, key, source):
    if key not in data:
        raise ManifestError(f"{source}: missing required key {key!r}")
    return data[key]


def curated_manifests():
    """Load every curated manifest shipped in ``pytest_deck/manifests/``.

    ``importlib.resources`` (not ``__file__`` math) so wheel/zip installs work.
    """
    root = importlib.resources.files("pytest_deck") / "manifests"
    manifests = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            manifests.append(parse_manifest(entry.read_text(), source=entry.name))
    return manifests


def user_manifests(rootdir):
    """Load user manifests from ``<rootdir>/.pytest-deck/plugins/*.toml``.

    The same loader and validation as curated, but ``trusted=False``, so the
    reserved-env gate applies. Resilient: a malformed or rejected file is
    skipped with a warning rather than being fatal, since one bad manifest
    should not blank the whole user set (or the panel). Returns a list, and a
    missing directory gives ``[]``.

    Contained to rootdir, where "scan under rootdir" means exactly that: an
    entry whose realpath resolves outside ``rootdir`` (a symlink in the plugins
    dir pointing elsewhere) is skipped, so a hostile repo can't plant a link
    that reads TOML from arbitrary locations. The plugins dir itself has to
    resolve under rootdir as well.
    """
    rootdir = Path(rootdir).resolve()
    plugins_dir = (rootdir / ".pytest-deck" / "plugins").resolve()
    if not plugins_dir.is_dir():
        return []
    try:
        plugins_dir.relative_to(rootdir)
    except ValueError:
        return []  # the plugins dir itself is a symlink out of the tree
    manifests = []
    for entry in sorted(plugins_dir.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".toml"):
            continue
        try:
            resolved = entry.resolve()
            resolved.relative_to(rootdir)  # a symlink escaping rootdir is skipped
        except (OSError, ValueError):
            warnings.warn(
                f"pytest-deck: skipping user manifest {entry.name}: "
                "resolves outside the project root"
            )
            continue
        try:
            text = resolved.read_text(encoding="utf-8")
            manifests.append(parse_manifest(text, source=entry.name, trusted=False))
        except (ManifestError, OSError) as exc:
            # Degrade: skip this one, keep the rest; the warning tells the author why.
            warnings.warn(f"pytest-deck: skipping user manifest {entry.name}: {exc}")
    return manifests


def installed_plugins():
    """Return the set of ``pytest11`` entry-point names in this environment.

    A fresh scan each call: it's cheap, and re-scanning at compile time guards
    the race where a plugin is uninstalled after the panel rendered (``-p`` on
    a missing name exits 1 before collection).
    """
    return {ep.name for ep in importlib.metadata.entry_points(group="pytest11")}


def available_manifests(rootdir=None):
    """Installed manifests to show in the panel: curated + user.

    Curated ones ship in-package; user manifests are scanned from
    ``<rootdir>/.pytest-deck/plugins`` when ``rootdir`` is given. Both are
    filtered to plugins actually installed in this env (no lying switches;
    ``-p <missing>`` exits 1). Precedence: on a shared ``id``, **the user
    manifest wins**, because a user manifest in the target repo is a deliberate
    override of the deck's curated argv, render and env for that plugin (the
    repo is the user's, and the security boundary is the reserved-env gate, not
    read-only curation). Order is stable by id.
    """
    installed = installed_plugins()
    by_id = {}
    for manifest in curated_manifests():
        by_id[manifest.id] = manifest
    if rootdir is not None:
        for manifest in user_manifests(rootdir):
            by_id[manifest.id] = manifest  # user overrides curated
    return [m for m in sorted(by_id.values(), key=lambda m: m.id) if m.id in installed]


def compile_argv(manifest, config):
    """Compile one enabled manifest + its config dict into pytest argv tokens.

    Pure: ``["-p", manifest.id]`` plus per-field tokens. Missing config keys
    fall back to field defaults; unknown keys or wrong value types raise
    ``ManifestConfigError``.
    """
    field_keys = {f.key for f in manifest.fields}
    unknown = set(config) - field_keys
    if unknown:
        raise ManifestConfigError(
            f"{manifest.id}: unknown config keys {sorted(unknown)}"
        )
    argv = ["-p", manifest.id]
    for spec in manifest.fields:
        value = config.get(spec.key, spec.default)
        expected = str if spec.type == "string" else bool
        if not isinstance(value, expected):
            raise ManifestConfigError(
                f"{manifest.id}: field {spec.key!r} must be {spec.type}, "
                f"got {type(value).__name__}"
            )
        if spec.type == "bool":
            if value:
                argv.append(spec.arg)
            continue
        # Trimmed: a whitespace-only value falls to arg_empty, not a junk token.
        value = value.strip()
        if value:
            # Literal replace, not str.format: braces in the value stay inert.
            argv.append(spec.arg.replace("{value}", value))
        elif spec.arg_empty is not None:
            argv.append(spec.arg_empty)
    if manifest.transport is not None:
        # The transport output flag rides with the plugin's tokens; the runner
        # substitutes its `{tmpdir}` placeholder at spawn. `arg` may be a token
        # list (more than one token: mpl's results-path plus generate-summary,
        # benchmark's save plus storage) or a single string. An fd3 transport
        # has no token at all; its payload rides the deck's own fd-3 pipe, not
        # a plugin output flag.
        arg = manifest.transport.get("arg")
        if isinstance(arg, list):
            argv.extend(arg)
        elif arg is not None:
            argv.append(arg)
    return argv


def compile_collect_argv(manifests):
    """Compile enabled manifests into the collect-side argv tokens.

    Pure and deliberately minimal, following the scope-split rule: only
    manifests with scope in ("collect", "both") contribute, and each
    contributes its ``["-p", id]`` switch and nothing else. Fields, transport
    tokens and ``[env]`` are run-only facets by construction, because a plugin
    output flag on collect would truncate its file before the run reads it (the
    ``FileType('wb')`` class), and the collect env stays pristine. Run-only
    manifests are skipped rather than raising an error (the caller validates
    ids; scope filtering is this function's job).
    """
    argv = []
    for manifest in manifests:
        if manifest.scope in ("collect", "both"):
            argv += ["-p", manifest.id]
    return argv


def compile_extra_args(text):
    """Split the tier-2 extra-args field into tokens (shlex, posix rules).

    Empty or whitespace-only input compiles to ``[]``. Output stays a token
    list: it is appended to argv, never joined into a shell string. Unbalanced
    quoting raises ``ManifestConfigError`` (server: 400), never a bare
    ``ValueError``.
    """
    if not text or not text.strip():
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError as exc:
        raise ManifestConfigError(f"extra args: {exc}") from exc


# === The one-path-per-token ini-addopts pipeline =================
#
# The deck neutralizes ini `addopts` on every child (P15: `-o addopts=`), then
# gives each token back through exactly one of three explicit paths:
#
#   1. Harvest. The token matches an available manifest's field arg-template,
#      so it prefills that field on /api/plugins (`ini_defaults`). The form is
#      authoritative from then on: the token itself never reaches argv, so a
#      field the user cleared is never invisibly resurrected.
#   2. Re-admit. A remaining self-contained token (`--flag` or `--flag=value`;
#      never a space-separated value, never a positional, no arity guessing)
#      inside an available manifest's `flags` namespace is appended at run time
#      if and only if that manifest is enabled, after plugin tokens and before
#      user extra-args (inside the P11 re-assert region). RESERVED_FLAGS never
#      re-admit.
#   3. Leftover. Everything else becomes an extra-args suggestion in the UI,
#      applied only on user click. Nothing is silently dropped.
#
# Classification is pure (this module); the server calls it in /api/plugins
# (harvest + leftovers) and _compile_plugins (re-admission, fresh ini read).


@dataclass(frozen=True)
class AddoptsPolicy:
    """The classified ini-addopts tokens (see ``classify_addopts``).

    ``ini_defaults`` maps a manifest id to ``{field_key: value}`` (the
    harvest); ``namespace`` holds ``(token, frozenset(manifest_ids))`` pairs in
    ini order (the re-admission candidates); ``leftovers`` holds the suggestion
    tokens, also in ini order.
    """

    ini_defaults: dict
    namespace: tuple
    leftovers: tuple

    def readmitted(self, enabled_ids):
        """Tokens to append to the run argv for this enabled-manifest set.

        Ini order preserved; a token rides once even if several enabled
        manifests' namespaces cover it.
        """
        enabled = set(enabled_ids)
        return [tok for tok, ids in self.namespace if ids & enabled]


def classify_addopts(tokens, manifests):
    """Classify ini-addopts ``tokens`` against ``manifests``.

    Returns an ``AddoptsPolicy``. Pure; token instances are walked in order and
    each takes exactly one path (harvest, then re-admit candidate, then
    leftover). The first harvest match per field wins, and a later duplicate
    flows onward (it may then re-admit under the namespace, faithful to
    pytest's repeated-flag semantics, e.g. several ``--cov=`` tokens).
    ``disabled_reason`` manifests are skipped entirely (they can never be
    enabled or compiled, so their tokens fall through to leftovers). A token
    matching the namespace of a manifest that is not enabled at run time is
    simply not re-admitted for that run, and it never becomes a leftover
    either, because enabling the plugin is its path (suggesting it would
    compile a plugin flag without its ``-p``, a guaranteed exit 4).
    """
    manifests = [m for m in manifests if m.disabled_reason is None]
    ini_defaults = {}
    claimed = set()  # (manifest_id, field_key) pairs already harvested
    namespace = []
    leftovers = []
    for token in tokens:
        hit = _harvest_match(token, manifests, claimed)
        if hit is not None:
            plugin_id, key, value = hit
            claimed.add((plugin_id, key))
            ini_defaults.setdefault(plugin_id, {})[key] = value
            continue
        if _is_flag_token(token) and not _is_reserved_flag(token):
            ids = frozenset(m.id for m in manifests if _in_namespace(token, m.flags))
            if ids:
                namespace.append((token, ids))
                continue
        leftovers.append(token)
    return AddoptsPolicy(
        ini_defaults=ini_defaults,
        namespace=tuple(namespace),
        leftovers=tuple(leftovers),
    )


def _harvest_match(token, manifests, claimed):
    """First unclaimed field whose arg-template matches ``token``, or None.

    Returns ``(manifest_id, field_key, value)``. Templates encode arity, so
    matching is exact per shape: a bool field matches its literal ``arg``
    (value ``True``); a string field matches its literal ``arg_empty`` (value
    ``""``) or its ``arg`` template split on ``{value}``, anchored on the
    prefix and the suffix, so ``--cov={value}`` matches ``--cov=pkg`` and
    yields ``"pkg"``. No token is ever split on whitespace and no neighboring
    token is consulted.
    """
    for manifest in manifests:
        for spec in manifest.fields:
            if (manifest.id, spec.key) in claimed:
                continue
            if spec.type == "bool":
                if token == spec.arg:
                    return manifest.id, spec.key, True
                continue
            if spec.arg_empty is not None and token == spec.arg_empty:
                return manifest.id, spec.key, ""
            prefix, _, suffix = spec.arg.partition("{value}")
            if (
                len(token) >= len(prefix) + len(suffix)
                and token.startswith(prefix)
                and token.endswith(suffix)
            ):
                return (
                    manifest.id,
                    spec.key,
                    token[len(prefix) : len(token) - len(suffix)],
                )
    return None


def _is_flag_token(token):
    """Self-contained option token: ``-``-leading, more than a bare dash.

    Positionals (paths, nodeids, a space-separated option value, trailing-
    comment junk) fail this and become leftovers, so re-admission can never
    widen the run's test selection.
    """
    return token.startswith("-") and len(token) > 1


def _is_reserved_flag(token):
    """Return True if ``token`` invokes a RESERVED_FLAGS option in any spelling.

    Long options compare on the name before ``=`` (``--rootdir=/evil``); pytest
    9.1.1 rejects long-option abbreviations (``--rootd`` and ``--root`` error
    out), so the exact name and its ``=``-form are the only long vectors.

    Short options are not a single ``-x``: pytest groups them (``-sq`` is
    ``-s -q``) and the first value-taking option in the cluster swallows the
    rest of the token as its value, from any position. So
    ``-xopythonpath=/evil`` is ``-x`` plus ``-o pythonpath=/evil``, and
    ``-spxdist`` is ``-s`` plus ``-p xdist``: a reserved value-taking short
    letter riding as a non-leading char smuggles the reserved option's payload.
    Inspecting ``token[:2]`` alone would wave those through. So we scan the
    whole cluster (up to a glued ``=`` value): if any char is a reserved
    value-taking short letter (``p``, ``o`` or ``c``), the token can carry a
    reserved payload and is treated as reserved. It is over-inclusive on the
    safe side only: ``-kopythonpath`` (where ``o`` is part of ``-k``'s value)
    merely falls to a leftover suggestion, never a silent drop.
    """
    if token.startswith("--"):
        return token.split("=", 1)[0] in RESERVED_FLAGS
    # Short cluster: chars before any glued `=` value (`-oaddopts=...` -> `oaddopts`).
    return bool(_RESERVED_SHORT & set(token[1:].split("=", 1)[0]))


def _in_namespace(token, flags):
    """Return True if ``token`` falls inside a declared ``flags`` namespace.

    A literal entry matches the exact token or its ``=``-form (``--cov``
    covers ``--cov=pkg``, never ``--coverage``); a trailing-``*`` entry is a
    plain prefix match (``--cov-*`` covers ``--cov-report=xml``).
    """
    for entry in flags:
        if entry.endswith("*"):
            if token.startswith(entry[:-1]):
                return True
        elif token == entry or token.startswith(entry + "="):
            return True
    return False
