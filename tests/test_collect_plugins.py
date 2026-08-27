"""Collect-scope plugin wiring.

The scope-split rule end to end: ``/api/collect`` gains a ``?plugins=`` query
param of enabled collect-scoped manifest IDS (validated like the run-body
guard), ``manifests.compile_collect_argv`` turns them into bare ``-p <id>``
switches (the ONLY facet that ever rides collect), the tokens ride BOTH P20
collector passes, and the deck's ``-p no:`` blocks are re-asserted LAST (P11).

Live tests drive the real subprocess against pytest-mock / pytest-asyncio /
pytest-django suites (importorskip-guarded) — including the cold-start recovery
story: a suite that collect-errors WITHOUT the switch collects clean WITH it.
"""

import asyncio
import json

import httpx
import pytest

import pytest_deck.collector as collector_mod
import pytest_deck.server as server_mod
from pytest_deck.collector import collect
from pytest_deck.manifests import parse_manifest
from pytest_deck.server import create_app

requires = pytest.importorskip  # alias for the live-test guards below


def run_async(coro):
    return asyncio.run(coro)


def asgi_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _leaf_nodeids(tree):
    out = []

    def walk(node):
        if node.get("leaf"):
            out.append(node["nodeid"])
        for child in node.get("children", []):
            walk(child)

    for node in tree:
        walk(node)
    return out


def _bare(plugin_id, scope, disabled_reason=None):
    doc = f'id = "{plugin_id}"\nlabel = "X"\ndist = "x"\nscope = "{scope}"\n'
    if disabled_reason is not None:
        doc += f'disabled_reason = "{disabled_reason}"\n'
    return parse_manifest(doc)


# === collector: argv shape (spy on _run_pytest) ============================


@pytest.fixture
def spy_run(monkeypatch):
    """Replace collector._run_pytest with a spy returning a canned clean pass."""
    calls = []

    def fake(rootdir, extra_argv, dirs=None):
        calls.append(list(extra_argv))
        return 0, "", json.dumps({"$deck": "collection", "items": []})

    monkeypatch.setattr(collector_mod, "_run_pytest", fake)
    return calls


def test_collect_plugin_tokens_before_blocks_before_targets(tmp_path, spy_run):
    collect(tmp_path, ["test_a.py"], plugin_argv=["-p", "django"])
    (extra,) = spy_run
    i = extra.index("django")
    assert extra[i - 1] == "-p"
    # P11: the deck's `no:` blocks are re-asserted after the plugin tokens
    # (last -p wins: a plain `-p name` unblocks an earlier `-p no:name`).
    assert extra[i + 1 : i + 5] == ["-p", "no:xdist", "-p", "no:cacheprovider"]
    # Positional targets stay last, after every -p token.
    assert extra[-1] == "test_a.py"


@pytest.mark.parametrize("plugin_argv", [None, []])
def test_collect_without_plugins_is_byte_identical_legacy(
    tmp_path, spy_run, plugin_argv
):
    # An absent param (None) and the compiled-empty case ([]) both produce the
    # exact previous extra argv: no guard duplication, nothing riding.
    collect(tmp_path, plugin_argv=plugin_argv)
    assert spy_run == [["--collect-only", "-q"]]


def test_plugin_tokens_ride_both_collector_passes(tmp_path, monkeypatch):
    # P20: pass 2 (the sibling-inject re-collect on an import-time error) must
    # carry the same plugin tokens as pass 1, or a plugin whose absence breaks
    # collection would flip-flop between passes.
    calls = []
    error_fd3 = json.dumps(
        {
            "$deck": "collect_error",
            "nodeid": "deep/test_x.py",
            "path": "deep/test_x.py",
            "longrepr_text": "ModuleNotFoundError: No module named 'helper'",
        }
    )
    ok_fd3 = json.dumps({"$deck": "collection", "items": []})

    def fake(rootdir, extra_argv, dirs=None):
        calls.append(list(extra_argv))
        return (2, "", error_fd3) if len(calls) == 1 else (0, "", ok_fd3)

    monkeypatch.setattr(collector_mod, "_run_pytest", fake)
    collect(tmp_path, plugin_argv=["-p", "asyncio"])
    assert len(calls) == 2  # the import error really triggered pass 2
    for extra in calls:
        i = extra.index("asyncio")
        assert extra[i - 1] == "-p"
        assert extra[i + 1 : i + 5] == ["-p", "no:xdist", "-p", "no:cacheprovider"]


# === GET /api/collect?plugins= (validation + compile) ======================


def _get_collect(app, params=None):
    async def body():
        async with asgi_client(app) as client:
            return await client.get("/api/collect", params=params or {})

    return run_async(body())


@pytest.fixture
def collect_spy(monkeypatch):
    """Spy on server-side collect(); returns the captured (targets, plugin_argv)."""
    calls = []

    def fake(rootdir, targets=None, plugin_argv=None):
        calls.append((targets, plugin_argv))
        return {"items": [], "errors": []}

    monkeypatch.setattr(server_mod, "collect", fake)
    return calls


def test_api_collect_plugins_param_compiles_switch(tmp_path, monkeypatch, collect_spy):
    monkeypatch.setattr(
        server_mod,
        "available_manifests",
        lambda rootdir=None: [_bare("django", "both"), _bare("asyncio", "both")],
    )
    r = _get_collect(create_app(tmp_path), {"plugins": "django,asyncio"})
    assert r.status_code == 200
    assert collect_spy == [(None, ["-p", "django", "-p", "asyncio"])]


def test_api_collect_run_only_id_contributes_nothing(
    tmp_path, monkeypatch, collect_spy
):
    # A valid enabled run-only id is tolerated (scope filtering is the compile
    # function's job) and compiles to no token, so collect stays legacy-shaped.
    monkeypatch.setattr(
        server_mod,
        "available_manifests",
        lambda rootdir=None: [_bare("pytest_cov", "run")],
    )
    r = _get_collect(create_app(tmp_path), {"plugins": "pytest_cov"})
    assert r.status_code == 200
    assert collect_spy == [(None, [])]


def test_api_collect_absent_param_is_legacy(tmp_path, collect_spy):
    r = _get_collect(create_app(tmp_path))
    assert r.status_code == 200
    assert collect_spy == [(None, [])]


def test_api_collect_unknown_plugin_400(tmp_path, monkeypatch, collect_spy):
    # Mirrors the run-body guard: a fresh available_manifests scan, so the
    # uninstall race fails here as a 400, never as `-p <missing>` exit 1 in the
    # child.
    monkeypatch.setattr(server_mod, "available_manifests", lambda rootdir=None: [])
    r = _get_collect(create_app(tmp_path), {"plugins": "nope"})
    assert r.status_code == 400
    assert "nope" in r.json()["error"]
    assert collect_spy == []  # rejected before any subprocess


def test_api_collect_disabled_plugin_400(tmp_path, monkeypatch, collect_spy):
    monkeypatch.setattr(
        server_mod,
        "available_manifests",
        lambda rootdir=None: [_bare("stuck", "both", disabled_reason="not yet")],
    )
    r = _get_collect(create_app(tmp_path), {"plugins": "stuck"})
    assert r.status_code == 400
    assert "not yet" in r.json()["error"]
    assert collect_spy == []


def test_api_collect_plugins_and_targets_compose(tmp_path, monkeypatch, collect_spy):
    monkeypatch.setattr(
        server_mod,
        "available_manifests",
        lambda rootdir=None: [_bare("django", "both")],
    )
    r = _get_collect(
        create_app(tmp_path), {"targets": "test_a.py", "plugins": "django"}
    )
    assert r.status_code == 200
    assert collect_spy == [(["test_a.py"], ["-p", "django"])]


# === live: pytest-mock (byte-identical collection) ==========================


def test_mock_collection_byte_identical_with_and_without(tmp_path):
    requires("pytest_mock")
    (tmp_path / "test_mock.py").write_text(
        "import os\n"
        "\n"
        "def test_with_mocker(mocker):\n"
        "    mocker.patch('os.getcwd', return_value='/fake')\n"
        "    assert os.getcwd() == '/fake'\n"
        "\n"
        "def test_plain():\n"
        "    assert True\n"
    )
    base = collect(tmp_path)
    with_plugin = collect(tmp_path, plugin_argv=["-p", "pytest_mock"])
    assert base == with_plugin  # collection is byte-identical either way
    assert [i["nodeid"] for i in base["items"]] == [
        "test_mock.py::test_with_mocker",
        "test_mock.py::test_plain",
    ]
    assert base["errors"] == []


# === live: pytest-asyncio (hostile ini, collect-fatal without) ==============


@pytest.fixture
def asyncio_suite(tmp_path):
    """The benchmark class of collect divergence: unknown mark + unknown ini
    under a user ``filterwarnings = error`` (honored, P15)."""
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nasyncio_mode = auto\nfilterwarnings = error\n"
    )
    (tmp_path / "test_async.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.asyncio\n"
        "async def test_async_marked():\n"
        "    assert True\n"
        "\n"
        "def test_plain():\n"
        "    assert True\n"
    )
    return tmp_path


def test_asyncio_hostile_ini_collects_clean_only_with_plugin(asyncio_suite):
    requires("pytest_asyncio")
    # Without the switch, the unknown `asyncio_mode` ini key warns under the
    # user's filterwarnings=error, and pytest re-raises it as an INTERNALERROR
    # with exit 3 (not the exit-2 collect interrupt). The deck still renders the
    # error strip because the inner plugin writes its fd-3 records before that
    # re-raise, and the collector tolerates any exit code once records exist.
    # By design, its only hard-failure gate is no records and no errors
    # (collector.py's CollectionError branch).
    without = collect(asyncio_suite)
    assert without["items"] == []
    assert len(without["errors"]) == 1
    # With `-p asyncio`, the mark and ini keys are known, so it is clean: both tests.
    with_plugin = collect(asyncio_suite, plugin_argv=["-p", "asyncio"])
    assert with_plugin["errors"] == []
    assert [i["nodeid"] for i in with_plugin["items"]] == [
        "test_async.py::test_async_marked",
        "test_async.py::test_plain",
    ]


# === live: pytest-django (the cold-start recovery story, over the API) ======


@pytest.fixture
def django_suite(tmp_path):
    """A model-importing django suite (the research djsuite fixture): ini DSM +
    a module-level model import that needs django.setup() at COLLECT time."""
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nDJANGO_SETTINGS_MODULE = mysite.settings\n"
    )
    site = tmp_path / "mysite"
    site.mkdir()
    (site / "__init__.py").write_text("")
    (site / "settings.py").write_text(
        'SECRET_KEY = "x"\n'
        'INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]\n'
        "DATABASES = {\n"
        '    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}\n'
        "}\n"
        "USE_TZ = True\n"
    )
    (tmp_path / "test_models.py").write_text(
        "import pytest\n"
        "from django.contrib.auth.models import User  # module-level model import\n"
        "\n"
        "@pytest.mark.django_db\n"
        "def test_create_user():\n"
        '    User.objects.create_user("u", "u@example.com", "pw")\n'
        "    assert User.objects.count() == 1\n"
    )
    (tmp_path / "test_case.py").write_text(
        "from django.test import TestCase\n"
        "\n"
        "class UserTests(TestCase):\n"
        "    def test_math(self):\n"
        "        assert 1 + 1 == 2\n"
    )
    (tmp_path / "test_plain.py").write_text("def test_plain():\n    assert True\n")
    return tmp_path


def test_django_cold_start_recovery_over_the_api(django_suite):
    requires("pytest_django")
    requires("django")

    # The owner-mandated story, backend-level: cold collect (no switch) shows a
    # collection error on the model-importing file (the error strip); the user
    # flips the django switch; the ?plugins=django re-collect is clean with all
    # 3 items. Uses the real available_manifests scan: django is curated and
    # installed, so the id validates without stubbing.
    async def body():
        app = create_app(django_suite)
        async with asgi_client(app) as client:
            cold = await client.get("/api/collect")
            assert cold.status_code == 200
            data = cold.json()
            # ImproperlyConfigured at import becomes an error record, not a hard 500.
            assert len(data["errors"]) == 1
            assert data["errors"][0]["nodeid"].startswith("test_models.py")
            assert "test_models.py::test_create_user" not in _leaf_nodeids(data["tree"])

            warm = await client.get("/api/collect", params={"plugins": "django"})
            assert warm.status_code == 200
            data = warm.json()
            assert data["errors"] == []
            assert sorted(_leaf_nodeids(data["tree"])) == [
                "test_case.py::UserTests::test_math",
                "test_models.py::test_create_user",
                "test_plain.py::test_plain",
            ]

    run_async(body())
