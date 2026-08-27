"""Tests for the manifest spine (``pytest_deck.manifests``).

Loader validation is strict (curated manifests are code); ``compile_argv`` is
pure and covered across the field-combination matrix; detection tests
monkeypatch the entry-point scan so they don't depend on what happens to be
installed (only the ``deck`` entry point — our own — is assumed real).
"""

import asyncio
import os
from pathlib import Path

import httpx
import pytest

import pytest_deck.server as server_mod
from pytest_deck import manifests as mod
from pytest_deck._subprocess import base_argv
from pytest_deck.import_paths import import_dirs, pythonpath_argv_dirs
from pytest_deck.manifests import (
    Manifest,
    ManifestConfigError,
    ManifestError,
    ManifestField,
    available_manifests,
    compile_argv,
    compile_collect_argv,
    compile_extra_args,
    curated_manifests,
    installed_plugins,
    parse_manifest,
)
from pytest_deck.runner import RunManager, _Run
from pytest_deck.server import create_app

VALID = """\
id = "pytest_cov"
label = "Coverage"
dist = "pytest-cov"
scope = "run"

[[fields]]
key = "source"
label = "Source"
type = "string"
default = ""
arg = "--cov={value}"
arg_empty = "--cov"

[[fields]]
key = "branch"
label = "Branch"
type = "bool"
default = false
arg = "--cov-branch"
"""


# === parse_manifest: happy path ============================================


def test_parse_valid_manifest():
    m = parse_manifest(VALID)
    assert m.id == "pytest_cov"
    assert m.label == "Coverage"
    assert m.dist == "pytest-cov"
    assert m.scope == "run"
    assert [f.key for f in m.fields] == ["source", "branch"]
    source, branch = m.fields
    assert (source.type, source.default, source.arg_empty) == ("string", "", "--cov")
    assert (branch.type, branch.default, branch.arg_empty) == ("bool", False, None)


def test_parse_manifest_no_fields():
    m = parse_manifest('id = "x"\nlabel = "X"\ndist = "x"\nscope = "both"\n')
    assert m.fields == ()


# === parse_manifest: strict validation =====================================


@pytest.mark.parametrize(
    "mutation, match",
    [
        ('id = "x"\nlabel = "X"\ndist = "x"\n', "missing required key 'scope'"),
        ('label = "X"\ndist = "x"\nscope = "run"\n', "missing required key 'id'"),
        ('id = ""\nlabel = "X"\ndist = "x"\nscope = "run"\n', "must be non-empty"),
        (
            'id = "x"\nlabel = "X"\ndist = "x"\nscope = "sometimes"\n',
            "scope must be one of",
        ),
        (
            'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nbogus = 1\n',
            "unknown keys",
        ),
        ('id = 3\nlabel = "X"\ndist = "x"\nscope = "run"\n', "'id' must be str"),
        ("this is not toml", "invalid TOML"),
    ],
)
def test_parse_manifest_rejects_bad_toplevel(mutation, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(mutation)


HEADER = 'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n\n[[fields]]\n'


@pytest.mark.parametrize(
    "field_body, match",
    [
        (
            'key = "k"\nlabel = "K"\ntype = "int"\ndefault = 0\narg = "-x"\n',
            "field type must be one of",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "bool"\narg = "-x"\n',
            "missing required key 'default'",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "bool"\ndefault = "no"\narg = "-x"\n',
            "default must be bool",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "string"\ndefault = 1\narg = "-x{value}"\n',
            "default must be string",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "bool"\ndefault = false\n',
            "missing required key 'arg'",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "bool"\ndefault = false\narg = "-x"\n'
            'arg_empty = "-y"\n',
            "only valid on string fields",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "string"\ndefault = ""\narg = "--flag"\n',
            "must contain",
        ),
        (
            'key = "k"\nlabel = "K"\ntype = "bool"\ndefault = false\narg = "-x"\n'
            "extra = 1\n",
            "unknown keys",
        ),
    ],
)
def test_parse_manifest_rejects_bad_field(field_body, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(HEADER + field_body)


def test_parse_manifest_rejects_duplicate_field_keys():
    doc = HEADER + (
        'key = "k"\nlabel = "K"\ntype = "bool"\ndefault = false\narg = "-x"\n'
        "\n[[fields]]\n"
        'key = "k"\nlabel = "K2"\ntype = "bool"\ndefault = false\narg = "-y"\n'
    )
    with pytest.raises(ManifestError, match="duplicate field keys"):
        parse_manifest(doc)


# === curated manifests + detection =========================================


def test_curated_manifests_load_and_validate():
    # Loading the shipped TOMLs is the validation gate for curated content.
    # (The order is the filename sort; ids need not match filenames.)
    ms = curated_manifests()
    assert [m.id for m in ms] == [
        "asyncio",
        "benchmark",
        "pytest_cov",
        "django",
        "metadata",
        "pytest_mock",
        "pytest_mpl",
    ]
    # Scope rules: collect can diverge without these four, so they are "both";
    # coverage/metadata/mpl stay run-only.
    scopes = {m.id: m.scope for m in ms}
    assert scopes == {
        "asyncio": "both",
        "benchmark": "both",
        "django": "both",
        "pytest_mock": "both",
        "pytest_cov": "run",
        "metadata": "run",
        "pytest_mpl": "run",
    }


def test_installed_plugins_sees_our_own_entry_point():
    # The `deck` pytest11 entry point ships with this very package.
    assert "deck" in installed_plugins()


def test_available_manifests_filters_to_installed(monkeypatch):
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"pytest_cov", "anyio"})
    assert [m.id for m in available_manifests()] == ["pytest_cov"]


def test_available_manifests_empty_when_plugin_absent(monkeypatch):
    monkeypatch.setattr(mod, "installed_plugins", lambda: {"anyio"})
    assert available_manifests() == []


# === compile_argv ==========================================================


@pytest.fixture
def coverage():
    return parse_manifest(VALID)


def test_compile_argv_defaults(coverage):
    # An empty config means field defaults: bare --cov, no branch flag.
    assert compile_argv(coverage, {}) == ["-p", "pytest_cov", "--cov"]


def test_compile_argv_source_and_branch(coverage):
    argv = compile_argv(coverage, {"source": "mypkg", "branch": True})
    assert argv == ["-p", "pytest_cov", "--cov=mypkg", "--cov-branch"]


def test_compile_argv_empty_source_falls_back(coverage):
    argv = compile_argv(coverage, {"source": "", "branch": True})
    assert argv == ["-p", "pytest_cov", "--cov", "--cov-branch"]


def test_compile_argv_source_only(coverage):
    argv = compile_argv(coverage, {"source": "a/b", "branch": False})
    assert argv == ["-p", "pytest_cov", "--cov=a/b"]


def test_compile_argv_braces_in_value_stay_inert(coverage):
    # Literal replace, not str.format: template syntax in values is data.
    argv = compile_argv(coverage, {"source": "{value}{oops}"})
    assert argv == ["-p", "pytest_cov", "--cov={value}{oops}"]


def test_compile_argv_no_arg_empty_omits_token():
    m = Manifest(
        id="x",
        label="X",
        dist="x",
        scope="run",
        fields=(
            ManifestField(
                key="s", label="S", type="string", default="", arg="--s={value}"
            ),
        ),
    )
    assert compile_argv(m, {}) == ["-p", "x"]


def test_compile_argv_rejects_unknown_key(coverage):
    with pytest.raises(ManifestConfigError, match="unknown config keys"):
        compile_argv(coverage, {"sources": "typo"})


@pytest.mark.parametrize(
    "config",
    [{"source": True}, {"source": 3}, {"branch": "yes"}, {"branch": 1}],
)
def test_compile_argv_rejects_wrong_types(coverage, config):
    with pytest.raises(ManifestConfigError, match="must be"):
        compile_argv(coverage, config)


# === compile_extra_args ====================================================


@pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
def test_compile_extra_args_empty(text):
    assert compile_extra_args(text) == []


def test_compile_extra_args_splits_tokens():
    assert compile_extra_args("--tb=short -q") == ["--tb=short", "-q"]


def test_compile_extra_args_respects_quoting():
    assert compile_extra_args("--foo \"a b\" 'c d'") == ["--foo", "a b", "c d"]


def test_compile_extra_args_is_tokens_not_shell():
    # Metacharacters survive as plain argv data; nothing interprets them.
    assert compile_extra_args("--x=$(rm -rf /); echo") == [
        "--x=$(rm",
        "-rf",
        "/);",
        "echo",
    ]


# === GET /api/plugins ======================================================


def test_api_plugins_contract(tmp_path, monkeypatch):
    manifest = parse_manifest(VALID)
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )

    async def body():
        app = create_app(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            r = await client.get("/api/plugins")
            assert r.status_code == 200
            # `ini_defaults` (per manifest) and `ini_leftovers` (top-level) are
            # part of the contract; both are empty with no ini here.
            assert r.json() == {
                "ini_leftovers": [],
                "plugins": [
                    {
                        "id": "pytest_cov",
                        "label": "Coverage",
                        "dist": "pytest-cov",
                        "scope": "run",
                        "render": None,
                        "disabled_reason": None,
                        "ini_defaults": {},
                        "fields": [
                            {
                                "key": "source",
                                "label": "Source",
                                "type": "string",
                                "default": "",
                            },
                            {
                                "key": "branch",
                                "label": "Branch",
                                "type": "bool",
                                "default": False,
                            },
                        ],
                    }
                ],
            }

    asyncio.run(body())


def test_api_plugins_empty_when_none_available(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "available_manifests", lambda rootdir=None: [])

    async def body():
        app = create_app(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            r = await client.get("/api/plugins")
            assert r.status_code == 200
            assert r.json() == {"plugins": [], "ini_leftovers": []}

    asyncio.run(body())


# === run wiring: manifest env + [env] validation ===========================


ENV_MANIFEST = """\
id = "deck"
label = "Deck self"
dist = "pytest-deck"
scope = "run"

[env]
DECK_PROBE = "{tmpdir}/probe"

[[fields]]
key = "noheader"
label = "No header"
type = "bool"
default = false
arg = "--no-header"
"""


def test_parse_manifest_env_table():
    m = parse_manifest(ENV_MANIFEST)
    assert m.env == {"DECK_PROBE": "{tmpdir}/probe"}


def test_parse_manifest_env_defaults_empty():
    m = parse_manifest('id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n')
    assert m.env == {}


@pytest.mark.parametrize(
    "doc, match",
    [
        (
            'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nenv = 3\n',
            "'env' must be a table",
        ),
        (
            'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n[env]\nX = 3\n',
            "value must be a string",
        ),
    ],
)
def test_parse_manifest_rejects_bad_env(doc, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(doc)


def test_curated_coverage_declares_coverage_file_env():
    # Pollution guard: enabling coverage must not drop .coverage in the tree.
    cov = next(m for m in curated_manifests() if m.id == "pytest_cov")
    assert cov.env == {"COVERAGE_FILE": "{tmpdir}/.coverage"}


# === run wiring: argv / env into the subprocess ============================


async def _drain(queue, until, timeout=30.0):
    """Collect (name, data) events until the predicate over names is True."""
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


class _StubManager:
    def __init__(self):
        self.events = []

    def broadcast(self, event):
        self.events.append(event)


def test_alpha_argv_unchanged_without_extras(tmp_path):
    # Regression: no plugins/extra_args means argv byte-identical to the alpha shape.
    run = _Run("run-1", None, tmp_path, ["t.py::x"], None, None)
    # P20: _argv now threads the merged pythonpath dirs into base_argv (a stale
    # `t.py` nodeid resolves to rootdir only, and with no user ini that is just
    # rootdir).
    pp = pythonpath_argv_dirs(
        Path(tmp_path).resolve(), import_dirs(tmp_path, ["t.py::x"])
    )
    expected = base_argv(Path(tmp_path).resolve(), pythonpath_dirs=pp) + [
        "--color=yes",
        "t.py::x",
    ]
    assert run._argv() == expected


def test_extra_argv_rides_after_flags_before_nodeids(tmp_path):
    run = _Run(
        "run-1",
        None,
        tmp_path,
        ["t.py::x"],
        None,
        "smoke",
        extra_argv=["-p", "pytest_cov", "--cov"],
    )
    argv = run._argv()
    assert argv[-1] == "t.py::x"  # positional nodeids stay last
    i = argv.index("-m=smoke")
    assert argv[i + 1 : i + 4] == ["-p", "pytest_cov", "--cov"]


def test_env_templates_substituted_at_spawn(tmp_path, monkeypatch):
    # Unit-level spy: COVERAGE_FILE-style [env] values reach the child env with
    # {tmpdir} replaced by the run tmpdir.
    captured = {}

    async def spy(*argv, **kwargs):
        captured["env"] = kwargs["env"]
        raise RuntimeError("spawn intercepted")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    stub = _StubManager()
    run = _Run(
        "run-1",
        stub,
        tmp_path,
        [],
        None,
        None,
        env_templates={"COVERAGE_FILE": "{tmpdir}/.coverage"},
        tmpdir=str(tmp_path / "run-tmp"),
    )
    asyncio.run(run.start())
    assert captured["env"]["COVERAGE_FILE"] == str(tmp_path / "run-tmp") + "/.coverage"
    # The intercepted spawn surfaced as the error event (clean failure path).
    assert [e.name for e in stub.events] == ["error"]


def test_run_tmpdir_survives_until_next_run(tmp_path):
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(["test_quick.py::test_ok"])
            await _drain(q, lambda ns: "finished" in ns)
            first = mgr._tmpdir
            # Survives run end: the runner reads post-run report files from it.
            assert os.path.isdir(first)
            q2 = mgr.subscribe()
            await mgr.start(["test_quick.py::test_ok"])
            await _drain(q2, lambda ns: "finished" in ns)
            assert not os.path.exists(first)  # replaced at the next run start
            assert os.path.isdir(mgr._tmpdir)
        finally:
            await mgr.shutdown()
        assert mgr._tmpdir is None

    asyncio.run(body())


# === POST /api/run: plugins + extra_args ===================================


def _post_run(app, payload):
    async def body():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post("/api/run", json=payload)

    return asyncio.run(body())


def test_api_run_unknown_plugin_400(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "available_manifests", lambda rootdir=None: [])
    app = create_app(tmp_path)
    r = _post_run(app, {"nodeids": [], "plugins": {"pytest_cov": {}}})
    assert r.status_code == 400
    assert "pytest_cov" in r.json()["error"]
    assert not app.state.manager.is_active()  # nothing was started


def test_api_run_bad_config_400(tmp_path, monkeypatch):
    manifest = parse_manifest(VALID)
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )
    app = create_app(tmp_path)
    r = _post_run(app, {"nodeids": [], "plugins": {"pytest_cov": {"branch": "yes"}}})
    assert r.status_code == 400
    assert "branch" in r.json()["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"plugins": ["pytest_cov"]},
        {"plugins": {"pytest_cov": "on"}},
        {"extra_args": ["-q"]},
    ],
)
def test_api_run_malformed_plugin_body_400(tmp_path, monkeypatch, payload):
    manifest = parse_manifest(VALID)
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )
    app = create_app(tmp_path)
    assert _post_run(app, payload).status_code == 400


def test_api_run_alpha_body_shape_passes_empty_extras(tmp_path):
    # No plugins/extra_args keys means exactly the alpha behavior downstream.
    app = create_app(tmp_path)
    calls = []

    async def fake_start(
        nodeids, k=None, m=None, extra_argv=None, env_templates=None, transports=None
    ):
        calls.append((nodeids, k, m, extra_argv, env_templates, transports))
        return "run-1"

    app.state.manager.start = fake_start
    r = _post_run(app, {"nodeids": ["a.py::t"], "k": "x"})
    assert r.status_code == 202
    assert calls == [(["a.py::t"], "x", None, [], {}, [])]


def test_api_run_plugins_and_extra_args_e2e(tmp_path, monkeypatch):
    # The payoff: switches really change the subprocess argv (started echo),
    # manifest [env] reaches the child, and the run completes.
    (tmp_path / "test_probe.py").write_text(
        "import os\n"
        "\n"
        "def test_env_probe():\n"
        "    val = os.environ.get('DECK_PROBE', '')\n"
        "    assert val.endswith('/probe')\n"
        "    assert os.path.isdir(os.path.dirname(val))\n"
    )
    manifest = parse_manifest(ENV_MANIFEST)  # id "deck": our own inert plugin
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )

    async def body():
        app = create_app(tmp_path)
        mgr = app.state.manager
        try:
            q = mgr.subscribe()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                r = await client.post(
                    "/api/run",
                    json={
                        "nodeids": ["test_probe.py::test_env_probe"],
                        "plugins": {"deck": {"noheader": True}},
                        "extra_args": "--maxfail=1",
                    },
                )
                assert r.status_code == 202
            events = await _drain(q, lambda ns: "finished" in ns)
            started = next(d for n, d in events if n == "started")
            argv = started["argv"]
            assert "--no-header" in argv
            assert "--maxfail=1" in argv
            j = argv.index("deck")
            assert argv[j - 1] == "-p"
            assert argv[-1] == "test_probe.py::test_env_probe"
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0  # the in-suite env assertions held
        finally:
            await mgr.shutdown()

    asyncio.run(body())


# === review fixes: hostile extra_args + trimming ======================


def test_compile_extra_args_unbalanced_quote_raises():
    # Defect fix: shlex's bare ValueError must not escape (it 500'd the API).
    with pytest.raises(ManifestConfigError, match="closing quotation"):
        compile_extra_args("--foo 'unclosed")


def test_api_run_unbalanced_extra_args_400(tmp_path):
    app = create_app(tmp_path)
    r = _post_run(app, {"nodeids": [], "extra_args": "--foo 'unclosed"})
    assert r.status_code == 400
    assert "closing quotation" in r.json()["error"]
    assert not app.state.manager.is_active()  # nothing was started


def test_compile_argv_whitespace_source_falls_back(coverage):
    # Nit fix: "   " must compile to the arg_empty fallback, not "--cov=   ".
    assert compile_argv(coverage, {"source": "   "}) == [
        "-p",
        "pytest_cov",
        "--cov",
    ]


def test_compile_argv_trims_string_values(coverage):
    assert compile_argv(coverage, {"source": " pkg "}) == [
        "-p",
        "pytest_cov",
        "--cov=pkg",
    ]


def test_argv_guard_reasserts_deck_blocks_last(tmp_path):
    # P11: with a hostile `-p xdist` in extra args, the deck's `no:` blocks must
    # be re-appended after the user tokens (last -p wins), nodeids still last.
    run = _Run(
        "run-1",
        None,
        tmp_path,
        ["t.py::x"],
        None,
        None,
        extra_argv=["-p", "xdist", "-n", "2"],
    )
    argv = run._argv()
    assert argv[-5:] == ["-p", "no:xdist", "-p", "no:cacheprovider", "t.py::x"]


def test_argv_guard_absent_without_extras(tmp_path):
    # No extras means no guard duplication; the alpha argv stays byte-identical.
    run = _Run("run-1", None, tmp_path, ["t.py::x"], None, None)
    assert run._argv().count("no:xdist") == 1


def test_hostile_reenable_is_neutralized_e2e(tmp_path):
    # xdist isn't installed here, so prove the last-p-wins guard with the
    # cacheprovider proxy: `-p cacheprovider` in extras, yet no .pytest_cache
    # appears and reports still stream.
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_quick.py::test_ok"], extra_argv=["-p", "cacheprovider"]
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            assert any(n == "report" for n, _ in events)  # transport alive
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()
        assert not (tmp_path / ".pytest_cache").exists()

    asyncio.run(body())


# === [transport] ======================================================


TRANSPORT_DOC = VALID + """
[transport]
type = "json_file"
arg = "--cov-report=json:{tmpdir}/cov.json"
path = "{tmpdir}/cov.json"
"""


def test_parse_manifest_transport():
    m = parse_manifest(TRANSPORT_DOC)
    assert m.transport == {
        "type": "json_file",
        "arg": "--cov-report=json:{tmpdir}/cov.json",
        "path": "{tmpdir}/cov.json",
    }


def test_parse_manifest_transport_defaults_none():
    assert parse_manifest(VALID).transport is None


@pytest.mark.parametrize(
    "table, match",
    [
        ('type = "xml_file"\narg = "-x"\npath = "p"\n', "transport type"),
        ('type = "json_file"\npath = "p"\n', "missing required key 'arg'"),
        ('type = "json_file"\narg = "-x"\n', "missing required key 'path'"),
        ('type = "json_file"\narg = "-x"\npath = "p"\nbogus = 1\n', "unknown keys"),
        # Arg is now string-or-token-list (the benchmark save+storage pair);
        # anything else still rejects loudly.
        (
            'type = "json_file"\narg = 3\npath = "p"\n',
            "'arg' must be a string or an array of token strings",
        ),
        (
            'type = "json_file"\narg = ["-x", 3]\npath = "p"\n',
            "'arg' must be a string or an array of token strings",
        ),
        (
            'type = "json_file"\narg = []\npath = "p"\n',
            "'arg' must be non-empty",
        ),
    ],
)
def test_parse_manifest_rejects_bad_transport(table, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(VALID + "\n[transport]\n" + table)


def test_parse_manifest_transport_requires_registered_slimmer():
    # Strictness: there is no generic pass-through, so an unregistered id with
    # a transport is a manifest validation error.
    doc = (
        'id = "unregistered"\nlabel = "X"\ndist = "x"\nscope = "run"\n'
        '[transport]\ntype = "json_file"\narg = "-x"\npath = "p"\n'
    )
    with pytest.raises(ManifestError, match="no way to render it"):
        parse_manifest(doc)


def test_compile_argv_appends_transport_arg():
    m = parse_manifest(TRANSPORT_DOC)
    argv = compile_argv(m, {"source": "pkg"})
    # The {tmpdir} placeholder survives compile; the runner substitutes it.
    assert argv[-1] == "--cov-report=json:{tmpdir}/cov.json"
    assert argv[:3] == ["-p", "pytest_cov", "--cov=pkg"]


def test_curated_coverage_declares_transport():
    cov = next(m for m in curated_manifests() if m.id == "pytest_cov")
    assert cov.transport == {
        "type": "json_file",
        "arg": "--cov-report=json:{tmpdir}/cov.json",
        "path": "{tmpdir}/cov.json",
    }


def test_api_run_passes_transports_to_manager(tmp_path, monkeypatch):
    manifest = parse_manifest(TRANSPORT_DOC)
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )
    app = create_app(tmp_path)
    calls = []

    async def fake_start(
        nodeids, k=None, m=None, extra_argv=None, env_templates=None, transports=None
    ):
        calls.append((extra_argv, transports))
        return "run-1"

    app.state.manager.start = fake_start
    r = _post_run(app, {"nodeids": [], "plugins": {"pytest_cov": {}}})
    assert r.status_code == 202
    extra_argv, transports = calls[0]
    assert extra_argv[-1] == "--cov-report=json:{tmpdir}/cov.json"
    assert transports == [
        {"plugin": "pytest_cov", "path": "{tmpdir}/cov.json", "render": None}
    ]


# === artifact_dir transport ===========================================


MPL_DOC = """\
id = "pytest_mpl"
label = "Matplotlib"
dist = "pytest-mpl"
scope = "run"
render = "artifacts"

[transport]
type = "artifact_dir"
arg = ["--mpl-results-path={tmpdir}/artifacts", "--mpl-generate-summary=json"]
root = "{tmpdir}/artifacts"
index = "results.json"
index_format = "mpl"
"""


def test_parse_artifact_transport():
    m = parse_manifest(MPL_DOC)
    assert m.render == "artifacts"
    assert m.transport == {
        "type": "artifact_dir",
        "arg": [
            "--mpl-results-path={tmpdir}/artifacts",
            "--mpl-generate-summary=json",
        ],
        "root": "{tmpdir}/artifacts",
        "index": "results.json",
        "index_format": "mpl",
    }


def test_artifact_transport_bypasses_slimmer_gate():
    # id "pytest_mpl" has no SLIMMERS entry; artifact_dir must not require one.
    m = parse_manifest(MPL_DOC)
    assert m.id == "pytest_mpl"


def test_compile_argv_artifact_arg_is_multiple_tokens():
    # Both mpl flags emit as separate tokens (argv-as-tokens), {tmpdir} intact.
    argv = compile_argv(parse_manifest(MPL_DOC), {})
    assert argv == [
        "-p",
        "pytest_mpl",
        "--mpl-results-path={tmpdir}/artifacts",
        "--mpl-generate-summary=json",
    ]


def test_curated_mpl_manifest_loads():
    mpl = next(m for m in curated_manifests() if m.id == "pytest_mpl")
    assert mpl.render == "artifacts"
    assert mpl.transport["type"] == "artifact_dir"
    assert mpl.transport["index_format"] == "mpl"


@pytest.mark.parametrize(
    "old, new, match",
    [
        ('index_format = "mpl"', 'index_format = "png"', "index_format must be one of"),
        ('render = "artifacts"', 'render = "json"', "requires render='artifacts'"),
        ('index = "results.json"', 'index = ""', "'index' must be non-empty"),
        ('root = "{tmpdir}/artifacts"\n', "", "missing required key 'root'"),
        ('index = "results.json"\n', "", "missing required key 'index'"),
        ('index_format = "mpl"\n', "", "missing required key 'index_format'"),
        (
            'arg = ["--mpl-results-path={tmpdir}/artifacts", '
            '"--mpl-generate-summary=json"]',
            'arg = "--mpl-results-path=x"',
            "must be an array of token strings",
        ),
    ],
)
def test_parse_artifact_transport_rejects_bad(old, new, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(MPL_DOC.replace(old, new))


def test_artifacts_render_requires_artifact_dir_type():
    # render="artifacts" on a json_file transport is a mismatch.
    doc = (
        'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\nrender = "artifacts"\n'
        '[transport]\ntype = "json_file"\narg = "-x"\npath = "p"\n'
    )
    with pytest.raises(ManifestError, match="requires transport type 'artifact_dir'"):
        parse_manifest(doc)


# === fd3 transport ====================================================


FD3_DOC = """\
id = "metadata"
label = "Environment"
dist = "pytest-metadata"
scope = "run"

[transport]
type = "fd3"
"""


def test_parse_fd3_transport():
    m = parse_manifest(FD3_DOC)
    assert m.transport == {"type": "fd3"}
    assert m.render is None
    assert m.fields == ()


def test_compile_argv_fd3_emits_no_transport_token():
    # The payload rides the deck's own fd-3 pipe; no output flag exists.
    assert compile_argv(parse_manifest(FD3_DOC), {}) == ["-p", "metadata"]


def test_curated_metadata_manifest_loads():
    meta = next(m for m in curated_manifests() if m.id == "metadata")
    # Switch-only by design: --metadata KEY VALUE is nargs=2, inexpressible by
    # single-token field templates, so extra metadata rides extra-args.
    assert meta.fields == ()
    assert meta.env == {}
    assert meta.render is None
    assert meta.transport == {"type": "fd3"}
    assert meta.scope == "run"


@pytest.mark.parametrize(
    "extra",
    ['arg = "-x"', 'path = "p"', 'root = "{tmpdir}/x"', 'index = "i.json"'],
)
def test_parse_fd3_rejects_other_transport_keys(extra):
    # `type` is the only key an fd3 table may carry: there is no file and no
    # argv token, so any other (otherwise-valid) transport key is dead config.
    with pytest.raises(ManifestError, match="no keys besides 'type'"):
        parse_manifest(FD3_DOC + extra + "\n")


def test_parse_fd3_rejects_render():
    # fd3 renders via the first-party slimmer; a generic render has no file.
    doc = FD3_DOC.replace('scope = "run"', 'scope = "run"\nrender = "json"')
    with pytest.raises(ManifestError, match="Omit 'render'"):
        parse_manifest(doc)


def test_parse_fd3_requires_registered_slimmer():
    doc = FD3_DOC.replace('id = "metadata"', 'id = "noslim"')
    with pytest.raises(ManifestError, match="first-party slimmer"):
        parse_manifest(doc)


def test_api_run_passes_fd3_transport_to_manager(tmp_path, monkeypatch):
    # The compiled transport entry carries `type` as the runner's discriminator
    # (no `path`, since there is no file) and no transport argv token rides.
    manifest = parse_manifest(FD3_DOC)
    monkeypatch.setattr(
        server_mod, "available_manifests", lambda rootdir=None: [manifest]
    )
    app = create_app(tmp_path)
    calls = []

    async def fake_start(
        nodeids, k=None, m=None, extra_argv=None, env_templates=None, transports=None
    ):
        calls.append((extra_argv, transports))
        return "run-1"

    app.state.manager.start = fake_start
    r = _post_run(app, {"nodeids": [], "plugins": {"metadata": {}}})
    assert r.status_code == 202
    extra_argv, transports = calls[0]
    assert extra_argv == ["-p", "metadata"]
    assert transports == [{"plugin": "metadata", "render": None, "type": "fd3"}]


# === benchmark manifest + arg-list transports =================


BENCH_ARGLIST_DOC = """\
id = "benchmark"
label = "Benchmarks"
dist = "pytest-benchmark"
scope = "both"

[transport]
type = "json_file"
arg = ["--benchmark-save=deck", "--benchmark-storage=file://{tmpdir}/benchmarks"]
path = "{tmpdir}/benchmarks/*/0001_deck.json"
"""


def test_parse_json_file_transport_arg_list():
    # json_file/text_file `arg` accepts a token list (the artifact_dir
    # precedent); benchmark needs the save + storage pair.
    m = parse_manifest(BENCH_ARGLIST_DOC)
    assert m.transport == {
        "type": "json_file",
        "arg": [
            "--benchmark-save=deck",
            "--benchmark-storage=file://{tmpdir}/benchmarks",
        ],
        "path": "{tmpdir}/benchmarks/*/0001_deck.json",
    }
    assert m.scope == "both"


def test_compile_argv_json_file_arg_list_is_multiple_tokens():
    argv = compile_argv(parse_manifest(BENCH_ARGLIST_DOC), {})
    assert argv == [
        "-p",
        "benchmark",
        "--benchmark-save=deck",
        "--benchmark-storage=file://{tmpdir}/benchmarks",
    ]


def test_curated_benchmark_manifest_loads():
    bench = next(m for m in curated_manifests() if m.id == "benchmark")
    assert bench.dist == "pytest-benchmark"
    # scope="both": the switch also rides collect (see test_collect_plugins).
    assert bench.scope == "both"
    # No render key: first-party slimmer (render map gives "benchmark" on the wire).
    assert bench.render is None
    assert bench.env == {}
    # Save-file transport, not --benchmark-json (which always embeds stats.data,
    # every raw round timing); the storage redirect also keeps .benchmarks/ out
    # of the user tree. The `*` is the host-derived machine-id dir.
    assert bench.transport == {
        "type": "json_file",
        "arg": [
            "--benchmark-save=deck",
            "--benchmark-storage=file://{tmpdir}/benchmarks",
        ],
        "path": "{tmpdir}/benchmarks/*/0001_deck.json",
    }
    # The three fields: disable + the iterate-speed knobs, nothing else.
    assert [(f.key, f.type) for f in bench.fields] == [
        ("disable", "bool"),
        ("min_rounds", "string"),
        ("max_time", "string"),
    ]


def test_compile_argv_curated_benchmark_fields():
    bench = next(m for m in curated_manifests() if m.id == "benchmark")
    argv = compile_argv(bench, {"disable": True, "min_rounds": "1", "max_time": "0.01"})
    assert argv == [
        "-p",
        "benchmark",
        "--benchmark-disable",
        "--benchmark-min-rounds=1",
        "--benchmark-max-time=0.01",
        "--benchmark-save=deck",
        "--benchmark-storage=file://{tmpdir}/benchmarks",
    ]


# --- gate 1: artifact_dir is curated-only -------------------------------


def test_artifact_dir_rejected_for_user_manifest():
    # Key regression (the read twin of the COVERAGE_FILE write vector): a user
    # manifest (trusted=False) declaring artifact_dir aims the HTTP endpoint's
    # serve base, so it must be rejected loudly, not accepted.
    with pytest.raises(ManifestError, match="reserved for curated manifests"):
        parse_manifest(MPL_DOC, trusted=False)


def test_artifact_dir_reject_message_explains_read_vector():
    # The rejection tells the author why (mirrors the RESERVED_ENV discipline).
    with pytest.raises(ManifestError, match="arbitrary-file-read vector"):
        parse_manifest(MPL_DOC, trusted=False)


def test_artifact_dir_allowed_for_curated_manifest():
    # Sanity: the same doc still parses as trusted (default).
    assert parse_manifest(MPL_DOC, trusted=True).transport["type"] == "artifact_dir"


# --- trust gate: fd3 is curated-only ------------------------------------


def test_fd3_rejected_for_user_manifest():
    # Key regression (same gate shape as artifact_dir): fd-3 is the deck's own
    # structured-results channel, so a user manifest declaring it is rejected
    # at parse, not accepted as dead surface.
    with pytest.raises(ManifestError, match="reserved for curated manifests"):
        parse_manifest(FD3_DOC, trusted=False)


def test_fd3_reject_message_explains_the_channel():
    # The rejection tells the author why (mirrors the RESERVED_ENV discipline).
    with pytest.raises(ManifestError, match="structured-results channel"):
        parse_manifest(FD3_DOC, trusted=False)


def test_fd3_allowed_for_curated_manifest():
    assert parse_manifest(FD3_DOC, trusted=True).transport == {"type": "fd3"}


# === structural manifests + compile_collect_argv ==============


def test_curated_structural_manifests_are_pure_switches():
    # mock/asyncio/django: zero fields, no transport, no render, no env, no
    # guard. Research proved a bare switch-on can't brick a green run.
    ms = {m.id: m for m in curated_manifests()}
    for plugin_id, dist in [
        ("pytest_mock", "pytest-mock"),
        ("asyncio", "pytest-asyncio"),
        ("django", "pytest-django"),
    ]:
        m = ms[plugin_id]
        assert m.dist == dist
        assert m.scope == "both"
        assert m.fields == ()
        assert m.env == {}
        assert m.transport is None
        assert m.render is None
        assert m.disabled_reason is None


def _bare(plugin_id, scope):
    return parse_manifest(
        f'id = "{plugin_id}"\nlabel = "X"\ndist = "x"\nscope = "{scope}"\n'
    )


def test_compile_collect_argv_scope_filtering():
    # Only scope "collect"/"both" contribute; run-only is skipped, not an error.
    manifests = [_bare("a", "both"), _bare("b", "run"), _bare("c", "collect")]
    assert compile_collect_argv(manifests) == ["-p", "a", "-p", "c"]


def test_compile_collect_argv_empty():
    assert compile_collect_argv([]) == []
    assert compile_collect_argv([_bare("x", "run")]) == []


def test_compile_collect_argv_emits_only_the_switch():
    # The scope-split pin: a manifest with fields, env and transport contributes
    # nothing but its `-p <id>` on collect. The transport's output flag on
    # collect would truncate the file the run later reads (FileType('wb')),
    # and [env] must never leak into the collect env.
    doc = TRANSPORT_DOC.replace('scope = "run"', 'scope = "both"')
    doc += '\n[env]\nCOVERAGE_FILE = "{tmpdir}/.coverage"\n'
    m = parse_manifest(doc)
    assert m.fields and m.env and m.transport is not None
    assert compile_collect_argv([m]) == ["-p", "pytest_cov"]


def test_compile_collect_argv_user_manifest_only_switch_rides():
    # A user manifest may declare scope="both": only its installed-id `-p`
    # token rides collect, while its render transport and [env] stay run-only.
    doc = (
        'id = "userplug"\nlabel = "U"\ndist = "u"\nscope = "both"\n'
        'render = "json"\n'
        '[env]\nUSER_VAR = "{tmpdir}/x"\n'
        '[transport]\ntype = "json_file"\narg = "--out={tmpdir}/o.json"\n'
        'path = "{tmpdir}/o.json"\n'
    )
    m = parse_manifest(doc, trusted=False)
    assert compile_collect_argv([m]) == ["-p", "userplug"]


def test_compile_collect_argv_curated_structural_set():
    ms = [
        m for m in curated_manifests() if m.id in ("pytest_mock", "asyncio", "django")
    ]
    assert compile_collect_argv(sorted(ms, key=lambda m: m.id)) == [
        "-p",
        "asyncio",
        "-p",
        "django",
        "-p",
        "pytest_mock",
    ]


# --- gate 2 (parse half): root must be tmpdir-anchored ------------------


def test_artifact_dir_root_must_contain_tmpdir_placeholder():
    # Gate 2 at parse time: a curated root without {tmpdir} (say "/" or an
    # absolute escape) can't be guaranteed under the run tmpdir, so it is
    # rejected.
    doc = MPL_DOC.replace('root = "{tmpdir}/artifacts"', 'root = "/"')
    with pytest.raises(ManifestError, match="must contain '.tmpdir.'"):
        parse_manifest(doc)  # trusted


def test_artifact_dir_root_absolute_escape_rejected_at_parse():
    doc = MPL_DOC.replace('root = "{tmpdir}/artifacts"', 'root = "/etc"')
    with pytest.raises(ManifestError, match="resolves under the run tmpdir"):
        parse_manifest(doc)


# --- curated flags namespaces ----------------------------------


def test_curated_flags_namespaces_pinned():
    # A namespace is a grant (self-contained ini-addopts tokens re-admit under
    # it at run time), so pin each curated declaration exactly and a widening
    # becomes a conscious diff, not drift. Each set was verified against the
    # plugin's real CLI surface (2026-08-13): cov 7.1.0, benchmark 5.2.3,
    # mpl 0.19.0, metadata 3.1.1, asyncio 1.4.0, django 4.14.0; pytest-mock
    # 3.15.1 declares zero CLI options, hence no namespace.
    flags = {m.id: m.flags for m in curated_manifests()}
    assert flags == {
        "pytest_cov": ("--cov", "--cov-*", "--no-cov", "--no-cov-on-fail"),
        "benchmark": ("--benchmark-*",),
        "pytest_mpl": ("--mpl", "--mpl-*"),
        "metadata": ("--metadata", "--metadata-*"),
        "asyncio": ("--asyncio-mode", "--asyncio-debug"),
        "django": (
            "--reuse-db",
            "--create-db",
            "--ds",
            "--dc",
            "--nomigrations",
            "--no-migrations",
            "--migrations",
            "--liveserver",
            "--fail-on-template-vars",
        ),
        "pytest_mock": (),
    }
