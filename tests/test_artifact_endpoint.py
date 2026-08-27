"""The raw-artifact serving endpoint (attachments pane data path).

``GET /api/artifacts/<run_id>/<path:file_path>`` → raw binary bytes with a
content-type by extension, 404 on stale/missing/traversal/over-cap. This is the
security-critical surface: it streams arbitrary bytes off a run's tmpdir to the
browser, so it mirrors ``/api/coverage``'s two-gate realpath containment and the
never-500 contract.

Deterministic: a fabricated artifacts dir (no pytest-mpl). The RunManager's
``artifact_root`` lookup is stubbed so the run/tmpdir lifecycle is controlled
without spawning a run; the security + serving logic runs for real.
"""

import asyncio
import struct
import types
import zlib

import httpx

from pytest_deck.runner import RunManager
from pytest_deck.server import (
    _ARTIFACT_MAX_BYTES,
    _artifact_file,
    _serve_artifact,
    create_app,
)


def run_async(coro):
    return asyncio.run(coro)


def asgi_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _png_bytes():
    """A minimal valid 1x1 PNG (real magic + IHDR/IDAT/IEND)."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _project(tmp_path):
    """A temp run: rootdir + an artifacts dir holding a real PNG + a text file.

    Returns ``(rootdir, root)`` where ``root`` is the served artifacts base.
    """
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    root = tmp_path / "run-tmp" / "artifacts"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "result.png").write_bytes(_png_bytes())
    (root / "notes.txt").write_text("plain text artifact")
    return rootdir, root


def _app_with_artifacts(tmp_path, run_id="run-1", located="real"):
    """Build an app whose manager.artifact_root returns our temp artifacts dir."""
    rootdir, root = _project(tmp_path)
    app = create_app(rootdir)

    def artifact_root(requested_id):
        if located == "stale" or requested_id != run_id:
            return None
        return root, rootdir

    app.state.manager.artifact_root = artifact_root
    return app, rootdir, root


# === RunManager.artifact_root lifecycle (the real lookup) ==================


def _stub_run(manager, run_id, rootdir, transports):
    manager._run = types.SimpleNamespace(
        run_id=run_id, rootdir=rootdir, transports=transports
    )


def test_artifact_root_returns_last_run_dir(tmp_path):
    rootdir, root = _project(tmp_path)
    tmpdir = root.parent
    mgr = RunManager(rootdir)
    _stub_run(
        mgr,
        "run-1",
        rootdir,
        [{"render": "artifacts", "root": "{tmpdir}/artifacts"}],
    )
    mgr._tmpdir = str(tmpdir)
    assert mgr.artifact_root("run-1") == (root.resolve(), rootdir)


def test_artifact_root_none_for_wrong_run_id(tmp_path):
    rootdir, root = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-2", rootdir, [{"render": "artifacts", "root": "x"}])
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


def test_artifact_root_none_when_no_artifact_transport(tmp_path):
    rootdir, root = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-1", rootdir, [{"render": "json", "path": "x"}])
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


def test_artifact_root_none_when_dir_absent(tmp_path):
    rootdir, root = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(
        mgr, "run-1", rootdir, [{"render": "artifacts", "root": "{tmpdir}/missing"}]
    )
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


# --- gate 2 (serve-time half): containment under the run tmpdir ---------
# Independent of the parse gate: this proves artifact_root itself refuses an
# escaping root even if one somehow reached run.transports (a curated-code bug).


def test_artifact_root_none_when_root_escapes_tmpdir_absolute(tmp_path):
    # A transport whose root resolves outside the run tmpdir gives None, so the
    # endpoint 404s. (The next test covers "/", the arbitrary-file-read base
    # from the original repro.)
    rootdir, root = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-1", rootdir, [{"render": "artifacts", "root": str(outside)}])
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


def test_artifact_root_none_when_root_is_filesystem_root(tmp_path):
    rootdir, root = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-1", rootdir, [{"render": "artifacts", "root": "/"}])
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


def test_artifact_root_none_when_root_traverses_out_of_tmpdir(tmp_path):
    # A {tmpdir}-anchored template that ../-escapes still resolves out, so None.
    rootdir, root = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(
        mgr, "run-1", rootdir, [{"render": "artifacts", "root": "{tmpdir}/../escape"}]
    )
    (root.parent.parent / "escape").mkdir(parents=True, exist_ok=True)
    mgr._tmpdir = str(root.parent)
    assert mgr.artifact_root("run-1") is None


def test_artifact_root_none_when_no_run(tmp_path):
    assert RunManager(tmp_path).artifact_root("run-1") is None


# === happy path ============================================================


def test_artifact_png_served_inline(tmp_path):
    app, _, root = _app_with_artifacts(tmp_path)
    expected = (root / "sub" / "result.png").read_bytes()

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/sub/result.png")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["content-disposition"].startswith("inline")
            assert r.content == expected

    run_async(body())


def test_artifact_nonimage_served_as_attachment(tmp_path):
    app, _, _ = _app_with_artifacts(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/notes.txt")
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/octet-stream"
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["content-disposition"].startswith("attachment")
            assert r.content == b"plain text artifact"

    run_async(body())


def test_artifact_svg_never_served_as_html(tmp_path):
    # An SVG can carry script; it must go out as image/svg+xml (with nosniff),
    # never text/html, so the browser never treats it as an HTML page. It still
    # renders inline as an SVG document (see _INLINE_TYPES in server.py).
    rootdir, root = _project(tmp_path)
    (root / "vector.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    app = create_app(rootdir)
    app.state.manager.artifact_root = lambda rid: (root, rootdir)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/vector.svg")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/svg+xml"
            assert "html" not in r.headers["content-type"]
            assert r.headers["x-content-type-options"] == "nosniff"

    run_async(body())


# === staleness / missing ===================================================


def test_artifact_stale_run_id_404(tmp_path):
    app, _, _ = _app_with_artifacts(tmp_path, run_id="run-1")

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-0/sub/result.png")
            assert r.status_code == 404
            assert "no longer available" in r.json()["error"]

    run_async(body())


def test_artifact_gone_tmpdir_404(tmp_path):
    app, _, _ = _app_with_artifacts(tmp_path, located="stale")

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/sub/result.png")
            assert r.status_code == 404

    run_async(body())


def test_artifact_missing_file_404(tmp_path):
    app, _, _ = _app_with_artifacts(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/sub/nope.png")
            assert r.status_code == 404

    run_async(body())


def test_artifact_directory_path_404(tmp_path):
    # A real subdirectory under root is not a servable file.
    app, _, _ = _app_with_artifacts(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/sub")
            assert r.status_code == 404

    run_async(body())


# === security: path traversal ==============================================


def test_artifact_rejects_dotdot_traversal(tmp_path):
    # A secret outside the artifacts root must never be read, even if the URL
    # walks up to it.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    app, _, _ = _app_with_artifacts(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            for attack in (
                "../secret.txt",
                "../../secret.txt",
                "../../../etc/passwd",
                "sub/../../secret.txt",
                "%2e%2e/secret.txt",
                "..%2fsecret.txt",
            ):
                r = await client.get(
                    f"/api/artifacts/run-1/{attack}", follow_redirects=True
                )
                assert r.status_code == 404, attack
                assert b"TOP SECRET" not in r.content, attack

    run_async(body())


def test_artifact_rejects_absolute_path(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    app, _, _ = _app_with_artifacts(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get(f"/api/artifacts/run-1/{secret}")
            assert r.status_code == 404
            assert b"TOP SECRET" not in r.content

    run_async(body())


def test_artifact_rejects_symlink_escape(tmp_path):
    # A file inside root that symlinks outside must be caught by gate 1
    # (realpath containment), not followed.
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    root = tmp_path / "run-tmp" / "artifacts"
    root.mkdir(parents=True)
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"SECRETBYTES")
    (root / "evil.png").symlink_to(secret)

    app = create_app(rootdir)
    app.state.manager.artifact_root = lambda rid: (root, rootdir)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/evil.png")
            assert r.status_code == 404
            assert b"SECRETBYTES" not in r.content

    run_async(body())


def test_artifact_null_byte_path_404_not_500(tmp_path):
    # A path with an embedded null byte makes `.resolve()` raise ValueError;
    # that has to 404 like any other bad path, never escape as a 500. Tested at
    # the helper level (a raw null byte can't ride a URL cleanly).
    _, root = _project(tmp_path)
    resolved, error = _artifact_file(root, "sub/result\x00.png")
    assert resolved is None
    assert "outside" in error


# === security: Content-Disposition injection ================================
#
# A Linux filename may legally hold a `"`, a newline, or a control char. Raw
# interpolation would inject an extra header parameter or split the header
# (h11/uvicorn then raise, which is a 500 and breaks never-500). The name has
# to be sanitized: `filename=` carries a quote-free, control-free ASCII fallback
# and `filename*=` carries the exact name percent-encoded. Tested at the helper
# level because such bytes can't ride a URL path segment cleanly.


def _disposition_for(tmp_path, filename, payload=b"payload"):
    root = tmp_path / "run-tmp" / "artifacts"
    root.mkdir(parents=True)
    (root / filename).write_bytes(payload)
    resolved, error = _artifact_file(root, filename)
    assert error is None, error
    response = _serve_artifact(resolved)
    return response.headers["content-disposition"]


def test_artifact_filename_with_quote_not_injected(tmp_path):
    # A `"` in the name must not close the quoted-string and inject a param.
    disp = _disposition_for(tmp_path, 'evil".txt')
    assert disp.startswith("attachment; ")
    # The raw quote is gone from the ASCII fallback; the real name rides
    # filename*= percent-encoded (%22).
    assert 'filename="evil_.txt"' in disp
    assert "filename*=UTF-8''evil%22.txt" in disp
    # Exactly one filename= param (no injected extra).
    assert disp.count("filename=") == 1


def test_artifact_filename_with_newline_no_header_split(tmp_path):
    # A CR/LF must never reach the header value (it would split it, a 500).
    disp = _disposition_for(tmp_path, "a\r\nb.txt")
    assert "\r" not in disp and "\n" not in disp
    assert 'filename="a__b.txt"' in disp
    assert "filename*=UTF-8''a%0D%0Ab.txt" in disp


def test_artifact_filename_with_control_char_sanitized(tmp_path):
    disp = _disposition_for(tmp_path, "tab\tbell\x07.txt")
    assert "\t" not in disp and "\x07" not in disp
    assert 'filename="tab_bell_.txt"' in disp
    assert "filename*=UTF-8''tab%09bell%07.txt" in disp


def test_artifact_filename_non_ascii_preserved_in_rfc5987(tmp_path):
    # Non-ASCII is replaced in the ASCII fallback but preserved (encoded) in
    # filename*=, and never produces a malformed header.
    disp = _disposition_for(tmp_path, "café.txt")
    assert "\r" not in disp and "\n" not in disp
    assert 'filename="caf_.txt"' in disp
    assert "filename*=UTF-8''caf%C3%A9.txt" in disp


def test_artifact_injection_name_serves_200_end_to_end(tmp_path):
    # The full request path: an on-disk name with a quote serves 200 (never
    # 500) with a well-formed disposition. The URL carries the percent-encoded
    # name.
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    root = tmp_path / "run-tmp" / "artifacts"
    root.mkdir(parents=True)
    (root / 'evil".txt').write_bytes(b"contents")
    app = create_app(rootdir)
    app.state.manager.artifact_root = lambda rid: (root, rootdir)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/evil%22.txt")
            assert r.status_code == 200
            assert r.content == b"contents"
            disp = r.headers["content-disposition"]
            assert 'filename="evil_.txt"' in disp
            assert "filename*=UTF-8''evil%22.txt" in disp

    run_async(body())


# === security: size cap ====================================================


def test_artifact_over_cap_404(tmp_path):
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    root = tmp_path / "run-tmp" / "artifacts"
    root.mkdir(parents=True)
    big = root / "huge.png"
    # A sparse file just over the cap; no real bytes written to disk.
    with open(big, "wb") as fh:
        fh.truncate(_ARTIFACT_MAX_BYTES + 1)

    app = create_app(rootdir)
    app.state.manager.artifact_root = lambda rid: (root, rootdir)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/artifacts/run-1/huge.png")
            assert r.status_code == 404
            assert "maximum" in r.json()["error"]

    run_async(body())


def test_artifact_at_cap_is_served(tmp_path):
    # Exactly at the cap is allowed (boundary: only strictly over is refused).
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    root = tmp_path / "run-tmp" / "artifacts"
    root.mkdir(parents=True)
    at = root / "big.png"
    with open(at, "wb") as fh:
        fh.truncate(_ARTIFACT_MAX_BYTES)

    resolved, error = _artifact_file(root, "big.png")
    assert error is None
    assert resolved == at.resolve()
