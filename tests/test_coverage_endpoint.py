"""The on-demand coverage-detail endpoint (source gutter data path).

``GET /api/coverage/<run_id>/<path:file_path>`` →
``{path, source, executed, missing, excluded}`` on success, 404 on
stale/missing/traversal. The heavy per-line data is read from the last run's
cov.json (retained in the run tmpdir) rather than pushed over SSE.

Deterministic: a fabricated cov.json + temp source tree (no pytest-cov). The
RunManager's ``coverage_file`` lookup is stubbed so the run/tmpdir lifecycle is
controlled without spawning a run; the security + parsing logic runs for real.
"""

import asyncio
import json
import types

import httpx

from pytest_deck.runner import RunManager
from pytest_deck.server import _coverage_detail, create_app


def run_async(coro):
    return asyncio.run(coro)


def asgi_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _project(tmp_path):
    """A temp project: rootdir + a measured source file + a fabricated cov.json.

    Returns ``(rootdir, tmpdir)`` where tmpdir holds ``cov.json`` keyed by the
    rootdir-relative source path.
    """
    rootdir = tmp_path / "proj"
    (rootdir / "pkg").mkdir(parents=True)
    (rootdir / "pkg" / "mod.py").write_text(
        "def a():\n"  # 1
        "    return 1\n"  # 2
        "\n"  # 3
        "def b():\n"  # 4
        "    return 2\n"  # 5
    )
    tmpdir = tmp_path / "run-tmp"
    tmpdir.mkdir()
    cov = {
        "meta": {"format": 3},
        "totals": {"percent_covered": 60.0},
        "files": {
            "pkg/mod.py": {
                "executed_lines": [1, 2, 4],
                "missing_lines": [5],
                "excluded_lines": [3],
                "summary": {"percent_covered": 60.0},
            }
        },
    }
    (tmpdir / "cov.json").write_text(json.dumps(cov))
    return rootdir, tmpdir


def _app_with_coverage(tmp_path, run_id="run-1", located="real"):
    """Build an app whose manager.coverage_file returns our temp project."""
    rootdir, tmpdir = _project(tmp_path)
    app = create_app(rootdir)

    def coverage_file(requested_id):
        if located == "stale" or requested_id != run_id:
            return None
        return tmpdir / "cov.json", rootdir

    app.state.manager.coverage_file = coverage_file
    return app, rootdir, tmpdir


# === RunManager.coverage_file lifecycle (the real lookup) ==================


def _stub_run(manager, run_id, rootdir):
    """Attach a minimal last-run object to a manager (no subprocess)."""
    manager._run = types.SimpleNamespace(run_id=run_id, rootdir=rootdir)


def test_coverage_file_returns_last_run_covjson(tmp_path):
    rootdir, tmpdir = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-1", rootdir)
    mgr._tmpdir = str(tmpdir)
    located = mgr.coverage_file("run-1")
    assert located == (tmpdir / "cov.json", rootdir)


def test_coverage_file_none_for_wrong_run_id(tmp_path):
    rootdir, tmpdir = _project(tmp_path)
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-2", rootdir)
    mgr._tmpdir = str(tmpdir)
    assert mgr.coverage_file("run-1") is None  # not the last run


def test_coverage_file_none_when_no_covjson(tmp_path):
    # A run with coverage disabled: tmpdir exists but has no cov.json.
    rootdir, tmpdir = _project(tmp_path)
    (tmpdir / "cov.json").unlink()
    mgr = RunManager(rootdir)
    _stub_run(mgr, "run-1", rootdir)
    mgr._tmpdir = str(tmpdir)
    assert mgr.coverage_file("run-1") is None


def test_coverage_file_none_when_no_run(tmp_path):
    assert RunManager(tmp_path).coverage_file("run-1") is None


# === happy path ============================================================


def test_coverage_happy_path(tmp_path):
    app, rootdir, _ = _app_with_coverage(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-1/pkg/mod.py")
            assert r.status_code == 200
            data = r.json()
            assert data["path"] == "pkg/mod.py"
            assert data["executed"] == [1, 2, 4]
            assert data["missing"] == [5]
            assert data["excluded"] == [3]
            assert "def a():" in data["source"]
            assert data["source"] == (rootdir / "pkg" / "mod.py").read_text()

    run_async(body())


def test_coverage_response_shape_is_exact(tmp_path):
    # The frontend builds against exactly these keys, no more and no less.
    app, _, _ = _app_with_coverage(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-1/pkg/mod.py")
            assert set(r.json()) == {
                "path",
                "source",
                "executed",
                "missing",
                "excluded",
            }

    run_async(body())


# === staleness / missing ===================================================


def test_coverage_stale_run_id_404(tmp_path):
    # Not the last run (the manager returns None): a clean 404, not a 500.
    app, _, _ = _app_with_coverage(tmp_path, run_id="run-1")

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-0/pkg/mod.py")
            assert r.status_code == 404
            assert "no longer available" in r.json()["error"]

    run_async(body())


def test_coverage_gone_tmpdir_404(tmp_path):
    app, _, _ = _app_with_coverage(tmp_path, located="stale")

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-1/pkg/mod.py")
            assert r.status_code == 404

    run_async(body())


def test_coverage_file_not_measured_404(tmp_path):
    app, _, _ = _app_with_coverage(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            # A real file under rootdir that is not in the cov.json files map.
            r = await client.get("/api/coverage/run-1/pkg/other.py")
            assert r.status_code == 404
            assert "not measured" in r.json()["error"]

    run_async(body())


def test_coverage_source_deleted_since_run_404(tmp_path):
    app, rootdir, _ = _app_with_coverage(tmp_path)
    (rootdir / "pkg" / "mod.py").unlink()  # in the map, but gone on disk

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-1/pkg/mod.py")
            assert r.status_code == 404
            assert "no longer available" in r.json()["error"]

    run_async(body())


# === security: path traversal ==============================================


def test_coverage_rejects_dotdot_traversal(tmp_path):
    # A secret outside rootdir must never be read, even if the URL walks up to
    # it. Starlette collapses `..` segments before routing (a 307 to a path that
    # drops run_id), and our two gates catch anything that reaches the handler,
    # so the secret never leaks whether or not the redirect is followed.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    app, _, _ = _app_with_coverage(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            for attack in (
                "../secret.txt",
                "../../etc/passwd",
                "pkg/../../secret.txt",
                "%2e%2e/secret.txt",  # URL-encoded ..
                "..%2fsecret.txt",  # encoded slash
            ):
                r = await client.get(
                    f"/api/coverage/run-1/{attack}", follow_redirects=True
                )
                assert r.status_code == 404, attack
                assert "TOP SECRET" not in r.text

    run_async(body())


def test_coverage_rejects_absolute_path(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    app, _, _ = _app_with_coverage(tmp_path)

    async def body():
        async with asgi_client(app) as client:
            # An absolute path in the URL: gate 1 (not a cov map key) rejects it.
            r = await client.get(f"/api/coverage/run-1/{secret}")
            assert r.status_code == 404
            assert "TOP SECRET" not in r.text

    run_async(body())


def test_coverage_rejects_symlink_escape(tmp_path):
    # A cov.json key that passes gate 1 but symlinks outside rootdir must be
    # caught by gate 2 (realpath containment).
    rootdir = tmp_path / "proj"
    (rootdir / "pkg").mkdir(parents=True)
    secret = tmp_path / "outside.py"
    secret.write_text("SECRET = 1")
    link = rootdir / "pkg" / "evil.py"
    link.symlink_to(secret)

    tmpdir = tmp_path / "run-tmp"
    tmpdir.mkdir()
    cov = {
        "files": {
            "pkg/evil.py": {
                "executed_lines": [1],
                "missing_lines": [],
                "excluded_lines": [],
            }
        }
    }
    (tmpdir / "cov.json").write_text(json.dumps(cov))

    app = create_app(rootdir)
    app.state.manager.coverage_file = lambda rid: (tmpdir / "cov.json", rootdir)

    async def body():
        async with asgi_client(app) as client:
            r = await client.get("/api/coverage/run-1/pkg/evil.py")
            assert r.status_code == 404
            assert "outside" in r.json()["error"]
            assert "SECRET" not in r.text

    run_async(body())


def test_coverage_null_byte_key_404_not_500(tmp_path):
    # A cov.json files-map key with an embedded null byte passes gate 1 (it is
    # a key), but `.resolve()` raises ValueError. That must be caught and answer
    # 404 like any other bad path, never escape as a 500 (the never-500
    # contract). This is tested at the helper level because a raw null byte
    # can't ride a URL cleanly.
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    key = "pkg/mod\x00.py"
    cov = tmp_path / "cov.json"
    cov.write_text(json.dumps({"files": {key: {"executed_lines": [1]}}}))

    detail, error = _coverage_detail(cov, rootdir, key)
    assert detail is None
    assert error == "file resolves outside the project"


def test_coverage_detail_deep_covjson_never_raises(tmp_path):
    # A crafted deeply-nested cov.json. Python 3.13's json.loads refuses it with
    # RecursionError, which must be caught ("could not be read"); 3.14 parses it
    # and gate 1 rejects the lookup instead. Both land as a 404 at the endpoint,
    # per the never-500 contract.
    rootdir = tmp_path / "proj"
    rootdir.mkdir()
    depth = 20000
    cov = tmp_path / "cov.json"
    cov.write_text('{"files": ' + "[" * depth + "]" * depth + "}")

    detail, error = _coverage_detail(cov, rootdir, "pkg/mod.py")
    assert detail is None
    assert error
