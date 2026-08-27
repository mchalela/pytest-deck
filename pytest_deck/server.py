"""FastAPI app: serves the built Svelte dashboard and the JSON/SSE API.

``POST /api/run`` only *starts* a run; all results flow over the persistent
``/api/events`` SSE stream, tagged with the run_id. The frontend folds per-phase
``report`` events into outcomes client-side (oracle: ``outcome.py``).
"""

import argparse
import asyncio
import errno
import json
import socket
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette import EventSourceResponse, ServerSentEvent

from .collector import CollectionError, collect
from .manifests import (
    ManifestConfigError,
    available_manifests,
    classify_addopts,
    compile_argv,
    compile_collect_argv,
    compile_extra_args,
)
from .rootdir import read_ini_addopts
from .runner import RunManager
from .tree import build_tree

_STATIC = Path(__file__).resolve().parent / "static"

# SSE heartbeat in seconds; keeps the idle stream alive through proxies.
_SSE_PING = 15

# B11: the advertised reconnect cadence; it must stay at or below the frontend's
# server-down grace.
_SSE_RETRY_MS = 1000

# Ctrl-C: how long sse_starlette lets the event stream return on its own before
# force-cancelling it mid-send (which would make uvicorn log "ASGI callable
# returned without completing response"). Must exceed the stream's 1.0s get()
# poll so the generator reliably notices the shutdown event first.
_SSE_SHUTDOWN_GRACE = 2.0


def create_app(rootdir, initial_target=None):
    """Build the FastAPI app bound to ``rootdir`` and its single RunManager.

    ``initial_target`` scopes the default (no-``targets``) collection to a
    subtree of ``rootdir``. It is set when the deck was launched as
    ``--deck PATH`` with PATH a subdirectory, so the initial tree mirrors
    ``pytest PATH`` (rootdir walks up to the config anchor; collection stays
    under PATH). ``None`` means the whole rootdir, the default for bare
    ``--deck``.
    """
    rootdir = Path(rootdir).resolve()
    default_targets = [str(Path(initial_target).resolve())] if initial_target else None
    manager = RunManager(rootdir)

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Tear down any in-flight run cleanly when the server stops.
        await manager.shutdown()

    app = FastAPI(title="pytest-deck", lifespan=lifespan)
    # app.state is the external handle (tests, embedding); routes use the closures.
    app.state.manager = manager

    @app.get("/")
    @app.get("/index.html")
    def index():
        return FileResponse(_STATIC / "index.html", media_type="text/html")

    @app.get("/api/collect")
    def api_collect(targets: str = None, plugins: str = None):
        # B10: a plain `def` runs in the threadpool; the blocking collect must
        # not stall the loop.
        target_list = [t for t in targets.split(",") if t] if targets else None
        # No explicit targets: fall back to the launch-time subtree, if any.
        if target_list is None:
            target_list = default_targets
        # `plugins` is the comma-separated list of enabled collect-scoped
        # manifest ids (ids only; config never rides collect). Absent means
        # byte-identical legacy behavior.
        try:
            plugin_argv = _collect_plugin_argv(plugins, rootdir)
        except ManifestConfigError as exc:
            # Server alive but rejecting (unknown or disabled id, e.g. the
            # uninstall race): a 400 with the reason, which the frontend shows
            # on the status line with the current tree intact (the run-reject
            # treatment), never as a hard collect failure or a server-down.
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            result = collect(rootdir, target_list, plugin_argv=plugin_argv)
        except CollectionError as exc:
            # Hard failure only; per-file import errors ride back as data below.
            return JSONResponse({"error": str(exc)}, status_code=500)
        payload = build_tree(result["items"])
        payload["rootdir"] = str(rootdir)
        # Like pytest's ERRORS section: [{nodeid, path, longrepr_text}, ...].
        payload["errors"] = result["errors"]
        return JSONResponse(payload)

    @app.get("/api/plugins")
    def api_plugins():
        # Installed plugins intersected with the curated + user manifests: a
        # switch for an absent plugin would be a lie. A fresh entry-point scan
        # per call guards against install/uninstall between loads; rootdir
        # enables the user-manifest scan (.pytest-deck/plugins).
        manifests = available_manifests(rootdir)
        # Classify the user's ini addopts (P15 strips them from every child).
        # Harvested tokens prefill the config forms via the per-manifest
        # `ini_defaults`; leftover tokens surface as extra-args suggestions
        # (`ini_leftovers`), applied only on user click and never silently
        # dropped. Namespace-matched tokens ride neither key: they re-admit at
        # run time exactly when their manifest is enabled (_compile_plugins).
        policy = classify_addopts(read_ini_addopts(rootdir), manifests)
        plugins = [
            {
                "id": m.id,
                "label": m.label,
                "dist": m.dist,
                "scope": m.scope,
                "render": m.render,
                "disabled_reason": m.disabled_reason,
                "ini_defaults": policy.ini_defaults.get(m.id, {}),
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "type": f.type,
                        "default": f.default,
                    }
                    for f in m.fields
                ],
            }
            for m in manifests
        ]
        return JSONResponse(
            {"plugins": plugins, "ini_leftovers": list(policy.leftovers)}
        )

    @app.post("/api/run")
    async def api_run(request: Request):
        body = await _read_json(request)
        nodeids = body.get("nodeids", [])
        k = body.get("k")
        m = body.get("m")
        # A crafted body must land as a clean 400 on every interpreter, never a
        # 500 and never a live run superseded by garbage. json.loads admits far
        # deeper documents on 3.14 than on 3.13, so nothing past this point may
        # meet an unvalidated value (k/m reach _trim_expr, nodeids ride into
        # argv and the `started` echo). plugins/extra_args have their own gate
        # right below (ManifestConfigError becomes a 400).
        if not isinstance(nodeids, list) or any(
            not isinstance(n, str) for n in nodeids
        ):
            return JSONResponse(
                {"error": "nodeids must be a list of test IDs"}, status_code=400
            )
        for name, value in (("k", k), ("m", m)):
            if value is not None and not isinstance(value, str):
                return JSONResponse(
                    {"error": f"{name} must be a string"}, status_code=400
                )
        # Scope split: the full compile (fields, transport, env) applies to run
        # subprocesses only; collect gets bare `-p` switches via its own
        # ?plugins= param (_collect_plugin_argv).
        try:
            extra_argv, env_templates, transports = _compile_plugins(
                body.get("plugins"), body.get("extra_args"), rootdir
            )
        except ManifestConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        # An empty selection is a deliberate "run all" (pytest's own
        # no-positionals semantics), not a 400; the frontend's canRun guard
        # catches accidents.
        run_id = await manager.start(
            nodeids,
            k=k,
            m=m,
            extra_argv=extra_argv,
            env_templates=env_templates,
            transports=transports,
        )
        return JSONResponse({"run_id": run_id}, status_code=202)

    @app.post("/api/cancel")
    async def api_cancel():
        cancelled, run_id = await manager.cancel()
        return JSONResponse({"cancelled": cancelled, "run_id": run_id})

    @app.get("/api/run/active")
    def api_run_active():
        # B9: the reconnect-resync probe. SSE has no replay, so after a gap the
        # client polls this to unstick a run whose `finished` it missed.
        # B10: plain def.
        return JSONResponse({"active": manager.is_active()})

    @app.get("/api/coverage/{run_id}/{file_path:path}")
    def api_coverage(run_id: str, file_path: str):
        # On-demand per-line hit/miss for the source gutter. The slim SSE
        # `plugin_data` carries only percentages; the heavy per-line data is
        # read here from the last run's cov.json (B10: plain def, threadpool).
        located = manager.coverage_file(run_id)
        if located is None:
            # Stale/missing: not the last run, or its tmpdir/cov.json is gone
            # (a new run started, or the run had no coverage). Never a 500.
            return JSONResponse(
                {
                    "error": "coverage for this run is no longer available "
                    "(re-run to refresh)"
                },
                status_code=404,
            )
        cov_path, rootdir = located
        detail, error = _coverage_detail(cov_path, rootdir, file_path)
        if detail is None:
            return JSONResponse({"error": error}, status_code=404)
        return JSONResponse(detail)

    @app.get("/api/artifacts/{run_id}/{file_path:path}")
    def api_artifacts(run_id: str, file_path: str):
        # Serve one raw artifact file (an image, etc.) the run's plugin wrote
        # into its tmpdir. SECURITY-CRITICAL: this is the only surface that
        # streams arbitrary binary bytes off disk to the browser. Mirrors
        # api_coverage: run-scoped lookup plus two-gate realpath containment,
        # a clean 404 on anything stale, missing or escaping, never a 500
        # (B10: plain def, threadpool; the read is blocking).
        located = manager.artifact_root(run_id)
        if located is None:
            # Not the last run, tmpdir gone, or no artifact_dir transport.
            return JSONResponse(
                {
                    "error": "artifacts for this run are no longer available "
                    "(re-run to refresh)"
                },
                status_code=404,
            )
        root, _rootdir = located
        resolved, error = _artifact_file(root, file_path)
        if resolved is None:
            return JSONResponse({"error": error}, status_code=404)
        return _serve_artifact(resolved)

    @app.get("/api/events")
    async def api_events(request: Request):
        # Set by sse_starlette when the server itself begins shutdown (its
        # patched uvicorn handle_exit), never mid-session, so this adds no new
        # close path the reconnect state machine could see: the stream ends,
        # the socket then dies, and the browser takes the normal server-down
        # route (CONNECTING + debounce, R1). Without it, sse_starlette
        # force-cancels the generator mid-send on Ctrl-C and uvicorn logs an
        # ERROR.
        shutdown = anyio.Event()

        async def event_stream():
            sub = manager.subscribe()
            try:
                while True:
                    if shutdown.is_set():
                        break  # server stopping: return so the response completes
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(sub.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue  # loop to re-check disconnect; ping handles idle
                    if event is None:
                        break  # subscriber closed
                    # A payload that cannot be re-serialized drops just this
                    # event rather than killing the sole results channel for
                    # every tab. Unreachable through first-party payloads (all
                    # bounded or depth-capped); only hand-crafted fd-3 lines
                    # from the child could get here, inside the localhost
                    # trust model.
                    try:
                        payload = json.dumps(event.data)
                    except Exception:
                        continue
                    # B11: advertise the retry cadence on every event.
                    yield ServerSentEvent(
                        event=event.name,
                        data=payload,
                        retry=_SSE_RETRY_MS,
                    )
            finally:
                # B8: a disconnect only unsubscribes; it never cancels the run.
                manager.unsubscribe(sub)

        return EventSourceResponse(
            event_stream(),
            ping=_SSE_PING,
            shutdown_event=shutdown,
            shutdown_grace_period=_SSE_SHUTDOWN_GRACE,
        )

    # Vite's hashed JS/CSS. Mounted last, own prefix, so it never shadows /api.
    assets_dir = _STATIC / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    return app


def _coverage_detail(cov_path, rootdir, file_path):
    """Resolve one file's per-line coverage; return ``(detail, error)``.

    ``detail`` is ``{path, source, executed, missing, excluded}`` on success,
    else ``None`` with an ``error`` string (the caller answers 404).

    Security: ``file_path`` is attacker-controlled (it comes off the URL), so
    two independent gates both have to pass before any source is read off disk:

    1. It has to be a key in the cov.json ``files`` map. Coverage measured only
       real project source, so an arbitrary path is simply absent. This alone
       blocks ``../../etc/passwd``, absolute paths, and unmeasured files.
    2. Its realpath has to resolve under ``rootdir``. That is defence in depth
       against a symlink in the measured tree pointing outside, or a coverage
       key that is itself absolute or traversing.
    """
    try:
        raw = json.loads(Path(cov_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        # RecursionError covers a crafted deeply nested cov.json (3.13's
        # json.loads refuses around 10k deep; 3.14 parses it and it falls to
        # gate 1 instead). This endpoint promises 404, never a 500.
        return None, "coverage data could not be read"

    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict) or file_path not in files:
        # Gate 1: not a measured file. Never touch the filesystem.
        return None, "file not measured in this run's coverage"
    entry = files[file_path] or {}

    rootdir = Path(rootdir).resolve()
    # Gate 2: build and realpath inside the guard. A key with an embedded null
    # byte makes `.resolve()` itself raise ValueError, which must 404 like any
    # other bad path rather than escape as a 500 (the "never 500" contract).
    try:
        resolved = (rootdir / file_path).resolve()
        resolved.relative_to(rootdir)
    except ValueError:
        return None, "file resolves outside the project"

    try:
        source = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # In the map but unreadable/deleted since the run.
        return None, "source file is no longer available"

    def _ints(key):
        value = entry.get(key)
        return (
            [n for n in value if isinstance(n, int)] if isinstance(value, list) else []
        )

    detail = {
        "path": file_path,
        "source": source,
        "executed": _ints("executed_lines"),
        "missing": _ints("missing_lines"),
        "excluded": _ints("excluded_lines"),
    }
    return detail, None


# Extension to content-type for artifact serving. Only these image types are
# served inline (rendered by the browser); any other extension goes out as
# octet-stream plus attachment, so it is downloaded rather than rendered in the
# page context. Bytes that are not really an image but carry one of these
# extensions are covered by the nosniff header in _serve_artifact, not by this
# map. Note that SVG is listed here and renders inline.
_INLINE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}

# Max artifact size we will serve (25 MiB). Images and diffs are small; refuse
# anything absurd rather than stream unbounded bytes into the response.
_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024


def _artifact_file(root, file_path):
    """Resolve one artifact under ``root``; return ``(path, error)``.

    ``path`` is a servable ``Path`` on success, else ``None`` with an ``error``
    string (the caller answers 404).

    Security: ``file_path`` is attacker-controlled (it comes off the URL).
    Unlike coverage there is no "has to be a manifest key" gate; containment
    under ``root`` is the gate. Two independent checks, both inside one guard:

    1. Its realpath has to resolve under ``root``, which blocks ``..``
       traversal, absolute paths, and symlinks escaping the run tmpdir. The
       path is built and resolved inside the try, so a null byte in it
       (``.resolve()`` raises ValueError) 404s like any other bad path instead
       of escaping as a 500.
    2. It has to be a regular file (not a dir, not missing) and within the size
       cap, so nothing unbounded or non-file is ever streamed.
    """
    root = Path(root).resolve()
    try:
        resolved = (root / file_path).resolve()
        resolved.relative_to(root)
    except ValueError:
        return None, "file resolves outside the run's artifacts"
    if not resolved.is_file():
        # A directory, a dangling symlink, or simply absent.
        return None, "artifact not found"
    try:
        if resolved.stat().st_size > _ARTIFACT_MAX_BYTES:
            return None, "artifact exceeds the maximum served size"
    except OSError:
        return None, "artifact not found"
    return resolved, None


def _content_disposition(disposition, name):
    r"""Build a safe ``Content-Disposition`` value for artifact ``name``.

    ``name`` is a single, containment-checked basename, but on Linux it may
    still legally contain a ``"``, a backslash, or control chars (incl. CR/LF).
    Interpolated raw, those either inject an extra header parameter or split
    the header, and h11/uvicorn reject the latter and raise, which would break
    the never-500 contract. So:

    * ``filename=`` carries an ASCII-only, sanitized fallback: every control
      char (< 0x20 and 0x7f), ``"``, ``\``, and non-ASCII byte becomes ``_``.
      Emptied names fall back to ``download``.
    * ``filename*=`` (RFC 5987) carries the exact UTF-8 name, percent-encoded,
      so modern browsers still see the real filename. Percent-encoding makes
      quotes/control chars/newlines inert here too.
    """

    def _safe(c):
        # A control char, quote, backslash, or non-ASCII char becomes "_", so
        # it can't break the quoted-string or split the header.
        unsafe = ord(c) < 0x20 or ord(c) == 0x7F or c in '"\\' or ord(c) > 0x7F
        return "_" if unsafe else c

    ascii_name = "".join(_safe(c) for c in name).strip() or "download"
    encoded = quote(name, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _serve_artifact(path):
    """Return a binary Response for a contained artifact ``path``.

    Known image types are served inline with their real content-type; anything
    else is ``application/octet-stream`` + ``Content-Disposition: attachment``
    so the browser downloads rather than renders it. ``X-Content-Type-Options:
    nosniff`` on every response stops the browser MIME-sniffing an artifact into
    an executable type (e.g. a spoofed image containing script).
    """
    suffix = path.suffix.lower()
    media_type = _INLINE_TYPES.get(suffix)
    headers = {"X-Content-Type-Options": "nosniff"}
    if media_type is None:
        media_type = "application/octet-stream"
        # Non-image: force a download, never an in-page render.
        headers["Content-Disposition"] = _content_disposition("attachment", path.name)
    else:
        headers["Content-Disposition"] = _content_disposition("inline", path.name)
    return FileResponse(path, media_type=media_type, headers=headers)


def _collect_plugin_argv(plugins, rootdir):
    """Validate + compile the ``?plugins=`` collect query param.

    ``plugins`` is a comma-separated list of enabled manifest ids (or None).
    Ids are checked against a fresh ``available_manifests(rootdir)`` scan,
    mirroring the run-body guard (P16: ``-p <missing>`` exits 1, so the
    uninstall race has to fail here as a 400, not in the subprocess); a
    ``disabled_reason`` manifest can't be enabled either. An unknown or
    disabled id raises ``ManifestConfigError`` and the endpoint answers 400. A
    valid run-only id is tolerated and contributes nothing: scope filtering is
    ``compile_collect_argv``'s job, and only ``-p <id>`` tokens ever come back
    (the scope-split rule).
    """
    ids = [p for p in plugins.split(",") if p] if plugins else []
    if not ids:
        return []
    available = {man.id: man for man in available_manifests(rootdir)}
    enabled = []
    for plugin_id in ids:
        manifest = available.get(plugin_id)
        if manifest is None:
            raise ManifestConfigError(
                f"plugin {plugin_id!r} is not available "
                "(not installed or not curated)"
            )
        if manifest.disabled_reason is not None:
            raise ManifestConfigError(
                f"plugin {plugin_id!r} is disabled: {manifest.disabled_reason}"
            )
        enabled.append(manifest)
    return compile_collect_argv(enabled)


def _compile_plugins(plugins, extra_args, rootdir):
    """Compile a run request's plugin config + extra args into argv/env.

    Returns ``(extra_argv, env_templates, transports)``. ``plugins`` maps a
    manifest id to its config dict (being present means enabled); ids are
    checked against a fresh ``available_manifests(rootdir)`` scan (curated plus
    user, P16: ``-p <missing>`` exits 1, so the uninstall race has to fail here
    as a 400, not in the subprocess). A ``disabled_reason`` manifest cannot be
    run (400). Any problem raises ``ManifestConfigError`` and the endpoint
    answers 400.
    """
    if plugins is not None and not isinstance(plugins, dict):
        raise ManifestConfigError("'plugins' must be an object of id to config")
    if extra_args is not None and not isinstance(extra_args, str):
        raise ManifestConfigError("'extra_args' must be a string")
    extra_argv = []
    env_templates = {}
    transports = []
    if plugins:
        available = {man.id: man for man in available_manifests(rootdir)}
        for plugin_id, config in plugins.items():
            manifest = available.get(plugin_id)
            if manifest is None:
                raise ManifestConfigError(
                    f"plugin {plugin_id!r} is not available "
                    "(not installed or not curated)"
                )
            if manifest.disabled_reason is not None:
                raise ManifestConfigError(
                    f"plugin {plugin_id!r} is disabled: {manifest.disabled_reason}"
                )
            if not isinstance(config, dict):
                raise ManifestConfigError(f"{plugin_id}: config must be an object")
            extra_argv += compile_argv(manifest, config)
            env_templates.update(manifest.env)
            if manifest.transport is not None:
                # The runner reads this file post-exit and emits the
                # `plugin_data` event, keyed by the manifest id (P16). `render`
                # picks the shape: None means a first-party slimmer (coverage),
                # "json"/"text" a generic pass-through. render="artifacts"
                # carries root/index/index_format instead of a single `path`;
                # the runner reads the index dir and joins.
                entry = {"plugin": manifest.id, "render": manifest.render}
                if manifest.transport["type"] == "artifact_dir":
                    entry["root"] = manifest.transport["root"]
                    entry["index"] = manifest.transport["index"]
                    entry["index_format"] = manifest.transport["index_format"]
                elif manifest.transport["type"] == "fd3":
                    # No file here; the runner resolves the record it stashed
                    # off the fd-3 pipe mid-run (`type` is the discriminator).
                    entry["type"] = "fd3"
                else:
                    entry["path"] = manifest.transport["path"]
                transports.append(entry)
        # Step 2, re-admit: self-contained ini-addopts tokens inside an
        # enabled manifest's `flags` namespace ride here, after the plugin
        # tokens and before the user's extra args (the P11 re-assert in
        # _Run._argv still lands after all of it). The ini is read fresh per
        # run (the file may have changed since the panel loaded) and
        # classified against all available manifests, so harvest exclusion
        # holds regardless of the current form value (a cleared field is never
        # resurrected) and RESERVED_FLAGS never re-admit (classify_addopts).
        # Ini only: the env PYTEST_ADDOPTS channel stays popped (P15), and the
        # child's `-o addopts=` neutralization is byte-unchanged.
        policy = classify_addopts(read_ini_addopts(rootdir), list(available.values()))
        extra_argv += policy.readmitted(plugins.keys())
    extra_argv += compile_extra_args(extra_args or "")
    return extra_argv, env_templates, transports


async def _read_json(request):
    """Parse a JSON request body, tolerating an empty body (returns ``{}``)."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        # A malformed body rides the empty-body path. ValueError covers
        # JSONDecodeError and bad UTF-8; RecursionError is a pathologically
        # deep body, which 3.13's json.loads refuses but 3.14's accepts.
        # Either way it must not escape a handler as a 500.
        return {}
    return data if isinstance(data, dict) else {}


# Port policy: with no explicit port, fall forward from the default, trying
# _PORT_ATTEMPTS consecutive ports (8765..8785) before failing loudly.
_DEFAULT_PORT = 8765
_PORT_ATTEMPTS = 21


def _bind(host, port):
    """Bind+listen a TCP socket on ``(host, port)``; ``None`` if in use.

    Bind-first, not probe-then-bind: the returned socket is handed straight to
    uvicorn (``Server.run(sockets=…)``), so uvicorn itself can never hit
    EADDRINUSE (whose logged errno-98 ERROR is exactly what this replaces).
    SO_REUSEADDR matches uvicorn's own bind: a just-stopped deck's TIME_WAIT
    remnants don't read as occupied, while a live listener still refuses.
    But SO_REUSEADDR also lets two processes bind() the same port while neither
    listens yet, so listen() happens right here, inside the catch: the loser of
    that race surfaces EADDRINUSE at listen() and falls forward instead of
    exploding later inside uvicorn, and once this returns the socket is a live
    listener that refuses any concurrent bind. (asyncio's
    ``create_server(sock=…)`` re-calls listen(), which on an already-listening
    socket merely updates the backlog.)

    Only EADDRINUSE means "try the next port" (``None``); any other error
    (a bad --host, permissions) is a real failure and raises the friendly exit.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen()
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            return None
        raise SystemExit(
            f"pytest-deck: cannot listen on {host}:{port} ({exc.strerror or exc})"
        ) from None
    return sock


def _bound_socket(host, port, port_flag):
    """Bind the serving socket per the port policy; return ``(sock, port)``.

    An explicit ``port`` binds exactly that or fails loudly (one line, exit 1,
    never uvicorn's raw errno-98 ERROR and never an OverflowError traceback on
    an out-of-range value). ``port=None`` is the auto path: try the default,
    then the next ports in the documented range, announcing any fallback;
    exhausting the range fails the same loud way. ``port_flag`` names the entry
    point's option in the message (``--port`` or ``--deck-port``).

    The returned port is always read back off the bound socket, so an explicit
    ``0`` does the conventional thing: bind an ephemeral port and announce the
    one actually bound.
    """
    if port is not None:
        if not 0 <= port <= 65535:
            # Range-checked here, not left to bind(): CPython raises
            # OverflowError (not OSError) for out-of-range ports, which would
            # escape as a traceback instead of the friendly one-liner.
            raise SystemExit(
                f"pytest-deck: invalid port {port} ({port_flag} must be 0-65535)"
            )
        sock = _bind(host, port)
        if sock is None:
            raise SystemExit(
                f"pytest-deck: port {port} is already in use (another deck?). "
                f"Stop it or pass a different {port_flag}"
            )
        return sock, sock.getsockname()[1]
    for candidate in range(_DEFAULT_PORT, _DEFAULT_PORT + _PORT_ATTEMPTS):
        sock = _bind(host, candidate)
        if sock is not None:
            return sock, sock.getsockname()[1]
    last = _DEFAULT_PORT + _PORT_ATTEMPTS - 1
    raise SystemExit(
        f"pytest-deck: ports {_DEFAULT_PORT}-{last} are all in use. "
        f"Stop a deck or pass a specific {port_flag}"
    )


def _display_url(host, port):
    """Format the URL printed/opened for ``host:port``; bracket IPv6 literals.

    ``--host ::1`` must yield ``http://[::1]:PORT/`` (RFC 3986), not the
    malformed ``http://::1:PORT/``.
    """
    host_part = f"[{host}]" if ":" in host else host
    return f"http://{host_part}:{port}/"


def serve(
    rootdir,
    host="127.0.0.1",
    port=None,
    open_browser=False,
    initial_target=None,
    port_flag="--port",
):
    """Build the app and run uvicorn on the main thread (Ctrl-C exits cleanly).

    ``port=None`` auto-falls-forward from 8765 (see ``_bound_socket``); an
    explicit ``port`` binds exactly that or exits with a friendly one-liner.
    The printed URL and the ``--open`` tab always carry the port actually bound.

    No auto-open by default: a restart looks identical to a first launch, so
    auto-opening spawns a redundant tab; we print the URL instead and the
    original tab self-heals over SSE reconnect. ``initial_target`` scopes the
    initial collection to a subtree (see ``create_app``).
    """
    import uvicorn

    sock, bound_port = _bound_socket(host, port, port_flag)
    try:
        app = create_app(rootdir, initial_target=initial_target)
        url = _display_url(host, bound_port)
        # flush=True: the server runs until Ctrl-C, so a block-buffered stdout
        # (a pipe, a log file, `tee`) would otherwise never show the URL.
        print(f"pytest-deck serving {Path(rootdir).resolve()}", flush=True)
        if port is None and bound_port != _DEFAULT_PORT:
            print(
                f"  port {_DEFAULT_PORT} in use → serving on {bound_port}",
                flush=True,
            )
        print(f"  → open {url}  (Ctrl-C to stop)", flush=True)
        if open_browser:
            webbrowser.open(url)
        config = uvicorn.Config(app, host=host, port=bound_port, log_level="warning")
        # sockets=[...] hands uvicorn our already-bound socket, so it never binds.
        uvicorn.Server(config).run(sockets=[sock])
    except KeyboardInterrupt:
        # Ctrl-C: uvicorn's capture_signals re-raises the captured SIGINT after
        # its graceful shutdown (the high-level uvicorn.run swallows it the same
        # way; Server.run does not). Exit quietly, no traceback.
        pass
    finally:
        # Everything after a successful bind (create_app, the banner, the
        # browser open, uvicorn itself) releases the socket on the way out;
        # idempotent if uvicorn's server close got there first.
        sock.close()


def main(argv=None):
    """Parse CLI args and serve."""
    parser = argparse.ArgumentParser(description="pytest-deck dashboard server")
    parser.add_argument(
        "rootdir", nargs="?", default=".", help="directory to collect tests from"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to serve on, which must be free (default: first free port "
        "from 8765, announced at startup)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open a browser tab on launch (off by default, avoiding a redundant "
        "tab on restart; the URL is printed to open once)",
    )
    args = parser.parse_args(argv)
    serve(args.rootdir, args.host, args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
