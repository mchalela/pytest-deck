"""Tests for user manifests: scan, reserved-env gate, generic render, gating.

The user scan reads ``.pytest-deck/plugins/*.toml`` under rootdir with the SAME
loader as curated but ``trusted=False`` (the reserved-env gate applies).
Untrusted TOML must never subvert deck integrity: an ``[env]`` key shadowing a
reserved deck var rejects the whole manifest. The generic ``render = "json"``/
``"text"`` path rides a transport file's parsed json / raw text on the
``plugin_data`` event's ``render`` discriminator, size-capped.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

import pytest_deck.server as server_mod
from pytest_deck import manifests as mod
from pytest_deck.manifests import (
    RESERVED_ENV,
    ManifestError,
    available_manifests,
    parse_manifest,
    user_manifests,
)
from pytest_deck.plugin_data import (
    RENDER_MAX_BYTES,
    RENDER_MAX_DEPTH,
    render_payload,
)
from pytest_deck.runner import RunManager
from pytest_deck.server import create_app


def _write_user_manifest(rootdir, name, text):
    """Drop a manifest TOML into ``<rootdir>/.pytest-deck/plugins/<name>``."""
    plugins = Path(rootdir) / ".pytest-deck" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / name).write_text(text)


USER_MANIFEST = """\
id = "anyio"
label = "AnyIO (user)"
dist = "anyio"
scope = "run"
"""


# === render field validation ===============================================


def test_parse_render_field_json():
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nrender = "json"\n'
        '[transport]\ntype = "json_file"\narg = "-o{tmpdir}"\npath = "{tmpdir}/o"\n'
    )
    m = parse_manifest(doc)
    assert m.render == "json"


def test_parse_render_field_text_with_text_file_transport():
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nrender = "text"\n'
        '[transport]\ntype = "text_file"\narg = "-o{tmpdir}"\npath = "{tmpdir}/o"\n'
    )
    m = parse_manifest(doc)
    assert m.render == "text"
    assert m.transport["type"] == "text_file"


def test_parse_render_defaults_none():
    assert parse_manifest(USER_MANIFEST).render is None


def test_parse_render_rejects_bad_value():
    doc = 'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nrender = "yaml"\n'
    with pytest.raises(ManifestError, match="render must be one of"):
        parse_manifest(doc)


def test_render_requires_transport():
    doc = 'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nrender = "json"\n'
    with pytest.raises(ManifestError, match="requires a \\[transport\\]"):
        parse_manifest(doc)


def test_transport_without_slimmer_or_render_rejected():
    # An unregistered id with a transport and no render is dead data.
    doc = (
        'id = "novel"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[transport]\ntype = "json_file"\narg = "-x"\npath = "p"\n'
    )
    with pytest.raises(ManifestError, match="no way to render it"):
        parse_manifest(doc)


# === trust rule: user transports need an explicit render ===============


def _shadow_doc(plugin_id):
    return (
        f'id = "{plugin_id}"\nlabel = "Shadow"\ndist = "x"\nscope = "run"\n'
        '[transport]\ntype = "json_file"\narg = "-x"\npath = "{tmpdir}/o.json"\n'
    )


@pytest.mark.parametrize("plugin_id", ["pytest_cov", "benchmark", "metadata"])
def test_user_transport_cannot_ride_first_party_slimmer(plugin_id):
    # Trust rule: an untrusted manifest with a transport can never satisfy the
    # render gate by shadowing a SLIMMERS id, because its file content would
    # render as the first-party coverage/benchmark/environment surface. This
    # deliberately tightens earlier behavior: a no-render user shadow of
    # pytest_cov used to pass this gate.
    with pytest.raises(ManifestError, match="explicit render"):
        parse_manifest(_shadow_doc(plugin_id), trusted=False)


def test_user_transport_reject_names_the_fix():
    # Loud at parse, with the fix named (P17 pattern, never a silent drop).
    with pytest.raises(ManifestError, match="render = 'json' or 'text'"):
        parse_manifest(_shadow_doc("pytest_cov"), trusted=False)


def test_user_shadow_with_explicit_render_still_allowed():
    # The paved path: a shadowing user manifest may declare render="json"/"text".
    # The data is shown, honestly labeled generic, never as first-party.
    doc = _shadow_doc("pytest_cov").replace(
        'scope = "run"', 'scope = "run"\nrender = "json"'
    )
    m = parse_manifest(doc, trusted=False, source="shadow.toml")
    assert m.render == "json"
    assert m.transport["type"] == "json_file"


def test_user_control_only_shadow_still_allowed():
    # Control-only (no transport) shadows stay fine; there is nothing to render.
    doc = 'id = "pytest_cov"\nlabel = "S"\ndist = "x"\nscope = "run"\n'
    assert parse_manifest(doc, trusted=False).transport is None


def test_curated_slimmer_transport_unaffected_by_trust_rule():
    # trusted=True (curated code) keeps the first-party slimmer path.
    m = parse_manifest(_shadow_doc("pytest_cov"))
    assert m.render is None
    assert m.transport["type"] == "json_file"


# === disabled_reason (self-gating) =========================================


def test_parse_disabled_reason():
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        'disabled_reason = "needs attempts model"\n'
    )
    assert parse_manifest(doc).disabled_reason == "needs attempts model"


def test_parse_disabled_reason_defaults_none():
    assert parse_manifest(USER_MANIFEST).disabled_reason is None


def test_parse_disabled_reason_must_be_string():
    doc = 'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\ndisabled_reason = 3\n'
    with pytest.raises(ManifestError, match="'disabled_reason' must be a string"):
        parse_manifest(doc)


# === reserved-env gate (SECURITY) ==========================================


@pytest.mark.parametrize("reserved", sorted(RESERVED_ENV))
def test_user_manifest_rejects_each_reserved_env_key(reserved):
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        f"[env]\n{reserved} = 'anything'\n"
    )
    with pytest.raises(ManifestError, match="reserved by the deck"):
        parse_manifest(doc, trusted=False)


def test_reserved_env_enumerates_the_deck_integrity_vars():
    # The gate is only meaningful if it covers what build_env/BASE_ENV set, what
    # P15 pops, and the arbitrary-file-write vector (COVERAGE_FILE). Guard
    # against silently dropping one from the set.
    assert RESERVED_ENV == {
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


def test_user_manifest_allows_benign_env_key():
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[env]\nMY_PLUGIN_MODE = "fast"\n'
    )
    m = parse_manifest(doc, trusted=False)
    assert m.env == {"MY_PLUGIN_MODE": "fast"}


def test_coverage_file_IS_reserved_for_users():
    # SECURITY: pytest-cov writes a SQLite DB to COVERAGE_FILE, so a user
    # manifest setting it to an arbitrary path overwrites that file. It is
    # reserved, and a user [env] COVERAGE_FILE is rejected.
    assert "COVERAGE_FILE" in RESERVED_ENV
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[env]\nCOVERAGE_FILE = "/home/victim/.bashrc"\n'
    )
    with pytest.raises(ManifestError, match="reserved by the deck"):
        parse_manifest(doc, trusted=False)


def test_curated_coverage_still_sets_coverage_file():
    # The curated coverage.toml is trusted code (it bypasses the gate) and must
    # keep pinning COVERAGE_FILE under the run tmpdir; the reserved-env change
    # must not break the flagship.
    from pytest_deck.manifests import curated_manifests

    cov = next(m for m in curated_manifests() if m.id == "pytest_cov")
    assert cov.env == {"COVERAGE_FILE": "{tmpdir}/.coverage"}


def test_trusted_manifest_skips_reserved_gate():
    # Curated code discipline: a trusted manifest may set anything (build_env
    # wins for BASE_ENV keys, but the loader doesn't police trusted content).
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[env]\nPYTHONPATH = "whatever"\n'
    )
    m = parse_manifest(doc, trusted=True)
    assert m.env == {"PYTHONPATH": "whatever"}


# === user scan =============================================================


def test_user_manifests_empty_when_no_dir(tmp_path):
    assert user_manifests(tmp_path) == []


def test_user_manifests_scans_and_validates(tmp_path):
    _write_user_manifest(tmp_path, "anyio.toml", USER_MANIFEST)
    ms = user_manifests(tmp_path)
    assert [m.id for m in ms] == ["anyio"]
    assert ms[0].label == "AnyIO (user)"


def test_user_manifests_ignores_non_toml(tmp_path):
    _write_user_manifest(tmp_path, "anyio.toml", USER_MANIFEST)
    _write_user_manifest(tmp_path, "README.md", "not a manifest")
    assert [m.id for m in user_manifests(tmp_path)] == ["anyio"]


def test_user_manifests_applies_reserved_gate(tmp_path, recwarn):
    # A user manifest that shadows a reserved var is scanned trusted=False and
    # therefore rejected (skipped, not loaded), with a warning naming why.
    _write_user_manifest(
        tmp_path,
        "hostile.toml",
        'id = "anyio"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[env]\nPYTEST_DECK_FD = "99"\n',
    )
    assert user_manifests(tmp_path) == []
    assert any("reserved by the deck" in str(w.message) for w in recwarn.list)


def test_user_manifests_rejects_artifact_dir(tmp_path, recwarn):
    # Key regression at the scan boundary: a user manifest declaring
    # artifact_dir (the arbitrary-file-read vector) is scanned trusted=False and
    # therefore rejected and skipped, with a warning naming why.
    _write_user_manifest(
        tmp_path,
        "hostile.toml",
        'id = "anyio"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        'render = "artifacts"\n[transport]\ntype = "artifact_dir"\n'
        'arg = ["--x"]\nroot = "/"\nindex = "i.json"\nindex_format = "mpl"\n',
    )
    assert user_manifests(tmp_path) == []
    assert any("reserved for curated manifests" in str(w.message) for w in recwarn.list)


def test_user_manifests_rejects_fd3(tmp_path, recwarn):
    # At the scan boundary: fd-3 is the deck's own structured-results channel
    # (first-party records only), so a user manifest declaring the fd3 transport
    # is scanned trusted=False and therefore rejected and skipped, with a
    # warning naming why (same gate shape as artifact_dir above).
    _write_user_manifest(
        tmp_path,
        "hostile.toml",
        'id = "metadata"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[transport]\ntype = "fd3"\n',
    )
    assert user_manifests(tmp_path) == []
    assert any("reserved for curated manifests" in str(w.message) for w in recwarn.list)


def test_user_manifests_rejects_slimmer_shadow_transport(tmp_path, recwarn):
    # Trust rule at the scan boundary: a user manifest shadowing a SLIMMERS
    # id with a no-render transport is scanned trusted=False and skipped, with
    # the fix named; it is never silently fed through a first-party slimmer.
    _write_user_manifest(
        tmp_path,
        "shadow.toml",
        'id = "pytest_cov"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[transport]\ntype = "json_file"\narg = "-x"\npath = "{tmpdir}/o.json"\n',
    )
    assert user_manifests(tmp_path) == []
    assert any("explicit render" in str(w.message) for w in recwarn.list)


def test_one_bad_manifest_does_not_kill_the_scan(tmp_path, recwarn):
    _write_user_manifest(tmp_path, "good.toml", USER_MANIFEST)
    _write_user_manifest(tmp_path, "bad.toml", "this is not valid toml =====")
    ms = user_manifests(tmp_path)
    assert [m.id for m in ms] == ["anyio"]  # the good one survived
    assert any("bad.toml" in str(w.message) for w in recwarn.list)


def test_user_manifests_skips_symlink_escaping_rootdir(tmp_path, recwarn):
    # A symlink in the plugins dir pointing at a TOML outside rootdir is
    # skipped: "scan under rootdir" means under rootdir. The planted target
    # is a valid manifest, so only the containment check keeps it out.
    outside = tmp_path.parent / "outside_target.toml"
    outside.write_text(USER_MANIFEST)
    plugins = Path(tmp_path) / ".pytest-deck" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / "sneaky.toml"
    try:
        link.symlink_to(outside)
    except OSError:
        import pytest as _pytest

        _pytest.skip("symlinks unsupported here")
    assert user_manifests(tmp_path) == []
    assert any("outside the project root" in str(w.message) for w in recwarn.list)


def test_user_manifests_allows_symlink_within_rootdir(tmp_path):
    # A symlink is fine as long as its target stays under rootdir.
    target = Path(tmp_path) / "real.toml"
    target.write_text(USER_MANIFEST)
    plugins = Path(tmp_path) / ".pytest-deck" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / "link.toml"
    try:
        link.symlink_to(target)
    except OSError:
        import pytest as _pytest

        _pytest.skip("symlinks unsupported here")
    assert [m.id for m in user_manifests(tmp_path)] == ["anyio"]


# === precedence: the user manifest wins on a shared id =====================


def test_user_overrides_curated_on_shared_id(tmp_path, monkeypatch):
    # A user manifest with id "pytest_cov" (same as curated) replaces it.
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"pytest_cov"})
    _write_user_manifest(
        tmp_path,
        "cov.toml",
        'id = "pytest_cov"\nlabel = "My Coverage"\ndist = "pytest-cov"\n'
        'scope = "run"\n',
    )
    available = available_manifests(tmp_path)
    assert [m.id for m in available] == ["pytest_cov"]
    assert available[0].label == "My Coverage"  # user's, not curated's


def test_available_without_rootdir_is_curated_only(monkeypatch):
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"pytest_cov"})
    assert [m.id for m in available_manifests()] == ["pytest_cov"]


def test_user_manifest_filtered_when_plugin_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"pytest_cov"})
    _write_user_manifest(tmp_path, "anyio.toml", USER_MANIFEST)  # anyio not installed
    assert [m.id for m in available_manifests(tmp_path)] == ["pytest_cov"]


def test_user_manifest_shown_when_plugin_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"pytest_cov", "anyio"})
    _write_user_manifest(tmp_path, "anyio.toml", USER_MANIFEST)
    ids = [m.id for m in available_manifests(tmp_path)]
    assert ids == ["anyio", "pytest_cov"]  # sorted by id


# === render_payload (size cap + parse) =====================================


def test_render_payload_text(tmp_path):
    p = tmp_path / "artifact.txt"
    p.write_text("hello world")
    data, truncated = render_payload("text", str(p))
    assert data == "hello world"
    assert truncated is False


def test_render_payload_json(tmp_path):
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps({"a": [1, 2, 3]}))
    data, truncated = render_payload("json", str(p))
    assert data == {"a": [1, 2, 3]}
    assert truncated is False


def test_render_payload_missing_file_is_none(tmp_path):
    assert render_payload("text", str(tmp_path / "absent")) is None
    assert render_payload("json", str(tmp_path / "absent")) is None


def test_render_payload_bad_json_is_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert render_payload("json", str(p)) is None


def test_render_payload_text_truncates_over_cap(tmp_path):
    p = tmp_path / "huge.txt"
    p.write_text("x" * (RENDER_MAX_BYTES + 500))
    data, truncated = render_payload("text", str(p))
    assert truncated is True
    assert len(data) == RENDER_MAX_BYTES


def test_render_payload_json_over_cap_reports_too_large(tmp_path):
    p = tmp_path / "huge.json"
    # A valid-but-huge JSON array beyond the cap: not partially parsed.
    p.write_text("[" + ",".join(["0"] * (RENDER_MAX_BYTES)) + "]")
    data, truncated = render_payload("json", str(p))
    assert truncated is True
    assert data["_truncated"] is True
    assert data["bytes"] > RENDER_MAX_BYTES


def test_render_payload_deep_nested_json_is_none_not_recursionerror(tmp_path):
    # JSON nested far past RENDER_MAX_DEPTH (within the 256 KiB byte cap).
    # Before the depth cap this leaned on json.loads raising RecursionError,
    # a moving target (CPython 3.14 parses 50k deep just fine, 3.13 refuses).
    # The cap makes the degrade deterministic on every interpreter: None
    # (plugin_empty), and nothing escapes to strand the run.
    p = tmp_path / "deep.json"
    depth = 50000
    p.write_text("[" * depth + "0" + "]" * depth)
    assert len(p.read_bytes()) <= RENDER_MAX_BYTES  # within the byte cap
    assert render_payload("json", str(p)) is None


def test_render_payload_depth_cap_boundary(tmp_path):
    # Right at the cap it parses normally (the plugin_data path); one past it
    # degrades to None. Both sides sit far below any interpreter's recursion
    # guard, so this behaves identically on every supported Python.
    ok = tmp_path / "ok.json"
    ok.write_text("[" * RENDER_MAX_DEPTH + "0" + "]" * RENDER_MAX_DEPTH)
    data, truncated = render_payload("json", str(ok))
    assert truncated is False
    depth = RENDER_MAX_DEPTH + 1
    deep = tmp_path / "deep.json"
    deep.write_text("[" * depth + "0" + "]" * depth)
    assert render_payload("json", str(deep)) is None


def test_render_payload_depth_cap_ignores_brackets_in_strings(tmp_path):
    # Brackets inside strings must not count toward depth: a shallow document
    # full of "]]]]" noise still renders.
    p = tmp_path / "s.json"
    p.write_text('{"a": "]]]]' + "[" * 600 + '"}')
    data, truncated = render_payload("json", str(p))
    assert data == {"a": "]]]]" + "[" * 600}
    assert truncated is False


# === render=json / render=text end to end (runner) =========================


async def _drain(queue, until, timeout=30.0):
    events = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, f"timed out; saw {[n for n, _ in events]}"
        ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        events.append((ev.name, ev.data))
        if until([n for n, _ in events]):
            return events


def test_render_json_end_to_end(tmp_path):
    # The child writes a JSON artifact; render="json" rides it parsed on
    # plugin_data with render:"json", no slimmer involved.
    (tmp_path / "test_writer.py").write_text(
        "import json, os\n"
        "\n"
        "def test_write():\n"
        "    with open(os.environ['ART'], 'w') as f:\n"
        "        json.dump({'metrics': {'runs': 3}}, f)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_writer.py::test_write"],
                env_templates={"ART": "{tmpdir}/art.json"},
                transports=[
                    {"plugin": "myplug", "path": "{tmpdir}/art.json", "render": "json"}
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd == {
                "run_id": run_id,
                "plugin": "myplug",
                "render": "json",
                "data": {"metrics": {"runs": 3}},
                "truncated": False,
            }
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_render_text_end_to_end(tmp_path):
    (tmp_path / "test_writer.py").write_text(
        "import os\n"
        "\n"
        "def test_write():\n"
        "    with open(os.environ['ART'], 'w') as f:\n"
        "        f.write('line one\\nline two\\n')\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_writer.py::test_write"],
                env_templates={"ART": "{tmpdir}/art.txt"},
                transports=[
                    {"plugin": "myplug", "path": "{tmpdir}/art.txt", "render": "text"}
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd == {
                "run_id": run_id,
                "plugin": "myplug",
                "render": "text",
                "data": "line one\nline two\n",
                "truncated": False,
            }
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_render_json_missing_artifact_emits_plugin_empty(tmp_path):
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_quick.py::test_ok"],
                transports=[
                    {
                        "plugin": "myplug",
                        "path": "{tmpdir}/absent.json",
                        "render": "json",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_render_json_deep_nested_finishes_not_stranded(tmp_path):
    # End-to-end: the child writes a JSON artifact nested past RENDER_MAX_DEPTH.
    # The run must still finish (plugin_empty) and never strand.
    (tmp_path / "test_writer.py").write_text(
        "import os\n"
        "\n"
        "def test_write():\n"
        "    depth = 50000\n"
        "    with open(os.environ['ART'], 'w') as f:\n"
        "        f.write('[' * depth + '0' + ']' * depth)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_writer.py::test_write"],
                env_templates={"ART": "{tmpdir}/art.json"},
                transports=[
                    {
                        "plugin": "myplug",
                        "path": "{tmpdir}/art.json",
                        "render": "json",
                    }
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "finished" in names  # not stranded
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_finished_emitted_even_if_transport_read_raises(tmp_path, monkeypatch):
    # Defense in depth for the load-bearing invariant: a run that exits always
    # emits `finished`, regardless of any transport-read failure. Force
    # _read_transports to raise an unexpected error; `finished` must still
    # arrive (else the run strands, SSE having no replay).
    from pytest_deck.runner import _Run

    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    def boom(self):
        raise RuntimeError("transport read blew up")

    monkeypatch.setattr(_Run, "_read_transports", boom)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                transports=[
                    {"plugin": "x", "path": "{tmpdir}/whatever", "render": "json"}
                ],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            finished = next(d for n, d in events if n == "finished")
            assert finished["run_id"] == run_id
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    asyncio.run(body())


# === /api/plugins surfaces render + disabled_reason ========================


def test_api_plugins_surfaces_disabled_reason_and_render(tmp_path, monkeypatch):
    gated = parse_manifest(
        'id = "anyio"\nlabel = "Gated"\ndist = "anyio"\nscope = "run"\n'
        'disabled_reason = "needs attempts model"\n'
    )
    monkeypatch.setattr(server_mod, "available_manifests", lambda rootdir=None: [gated])

    async def body():
        app = create_app(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            r = await client.get("/api/plugins")
            assert r.status_code == 200
            plugin = r.json()["plugins"][0]
            assert plugin["disabled_reason"] == "needs attempts model"
            assert plugin["render"] is None

    asyncio.run(body())


def test_api_run_rejects_disabled_plugin(tmp_path, monkeypatch):
    gated = parse_manifest(
        'id = "anyio"\nlabel = "Gated"\ndist = "anyio"\nscope = "run"\n'
        'disabled_reason = "needs attempts model"\n'
    )
    monkeypatch.setattr(server_mod, "available_manifests", lambda rootdir=None: [gated])
    app = create_app(tmp_path)

    async def body():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post("/api/run", json={"plugins": {"anyio": {}}})

    r = asyncio.run(body())
    assert r.status_code == 400
    assert "disabled" in r.json()["error"]
    assert not app.state.manager.is_active()


def test_api_plugins_scans_user_manifests(tmp_path, monkeypatch):
    # End-to-end: a user manifest under rootdir appears on /api/plugins when its
    # plugin is installed.
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"anyio"})
    _write_user_manifest(tmp_path, "anyio.toml", USER_MANIFEST)

    async def body():
        app = create_app(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            r = await client.get("/api/plugins")
            assert r.status_code == 200
            ids = [p["id"] for p in r.json()["plugins"]]
            assert "anyio" in ids

    asyncio.run(body())
