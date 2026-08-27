"""Collection errors match vanilla pytest (partial tree + ERRORS).

``collect()`` now returns ``{"items": [...], "errors": [...]}`` (was a bare list).
A per-file import error does NOT sink the whole collection: the good tests still
come back, and each erroring file rides alongside as an ``errors`` record
``{nodeid, path, longrepr_text}`` (traceback ANSI-coloured) — exactly like pytest
listing the collected tests then an ``ERRORS`` section. Exit-code mapping:

* 0 / 5 — clean (items, or an empty suite)
* 2 — partial: some items collected + some files errored (returns items+errors)
* 4 — HARD failure (e.g. a top-level ``conftest.py`` import error) → still
  ``CollectionError`` → ``/api/collect`` 500

``/api/collect`` returns ``200 {markers, tree, total, rootdir, errors:[...]}`` on
a partial collection, and ``500 {error}`` only on a genuine hard failure.

Async endpoint tests use httpx ASGITransport (collect is a synchronous one-shot,
no long-lived stream) driven with ``run_async`` (``pytest-asyncio`` not installed).
"""

import asyncio

import httpx
import pytest

from pytest_deck.collector import CollectionError, collect
from pytest_deck.server import create_app


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


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def mixed_suite(tmp_path):
    """A good test file + a file that fails to import → partial collection."""
    (tmp_path / "test_good.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.smoke\n"
        "def test_ok():\n"
        "    assert True\n"
        "\n"
        "def test_ok2():\n"
        "    assert 1 + 1 == 2\n"
    )
    (tmp_path / "test_broken.py").write_text(
        "import nonexistent_module_xyz  # ImportError at collect time\n"
        "\n"
        "def test_never():\n"
        "    assert True\n"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    smoke: smoke tests\n"
    )
    return tmp_path


@pytest.fixture
def hardfail_suite(tmp_path):
    """A top-level ``conftest.py`` that fails to import → hard failure (exit 4)."""
    (tmp_path / "conftest.py").write_text(
        "import totally_missing_module_abc  # conftest import error → exit 4\n"
    )
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    return tmp_path


@pytest.fixture
def empty_suite(tmp_path):
    """A directory with no tests at all."""
    (tmp_path / "notes.txt").write_text("just some notes, no tests here\n")
    return tmp_path


@pytest.fixture
def testpaths_suite(tmp_path):
    """A project with ``testpaths=[tests]`` and an EXTRA dir of tests outside it.

    Bare ``pytest`` here collects only ``tests/`` (testpaths applies when no
    positional path is given). The deck's default collect must match — it used
    to pass rootdir as a positional, which OVERRODE testpaths and swept in the
    ``extra/`` dir (the dogfooding bug).
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_in.py").write_text("def test_in():\n    assert True\n")
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "test_out.py").write_text("def test_out():\n    assert True\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    return tmp_path


@pytest.fixture
def no_testpaths_suite(tmp_path):
    """A project with NO ``testpaths`` — the deck collects from rootdir."""
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "test_b.py").write_text("def test_b():\n    assert True\n")
    return tmp_path


# === collect() unit behavior ==============================================


def test_collect_mixed_returns_items_and_errors(mixed_suite):
    result = collect(mixed_suite)

    # Shape changed from a bare list to {items, errors}.
    assert set(result) == {"items", "errors"}

    # The good file's tests are still collected (a partial tree, like pytest).
    nodeids = {it["nodeid"] for it in result["items"]}
    assert "test_good.py::test_ok" in nodeids
    assert "test_good.py::test_ok2" in nodeids
    # The broken file contributed no items.
    assert not any(n.startswith("test_broken.py") for n in nodeids)

    # And the broken file rides alongside as an error record.
    assert len(result["errors"]) == 1, result["errors"]
    err = result["errors"][0]
    assert set(err) >= {"nodeid", "path", "longrepr_text"}
    assert "test_broken.py" in err["nodeid"]
    # The traceback is present and ANSI-coloured (the same render as run tracebacks).
    lr = err["longrepr_text"]
    assert lr, "collect error must carry a rendered traceback"
    assert "\x1b[" in lr, "collect-error traceback should be ANSI-coloured"
    assert "nonexistent_module_xyz" in lr  # the real cause is shown


def test_collect_empty_suite_returns_empty(empty_suite):
    result = collect(empty_suite)
    assert result == {"items": [], "errors": []}


def test_collect_hard_failure_raises_collection_error(hardfail_suite):
    # A top-level conftest import error is exit 4, a genuine hard failure rather
    # than a partial collection, so collect() must raise (and /api/collect
    # answers 500).
    #
    # This is a cleaner hard-failure trigger than a missing rootdir: a
    # nonexistent directory fails at subprocess spawn (the cwd doesn't exist)
    # before collection runs, which is a different error path and can't reach
    # /api/collect anyway since the launcher validates the dir up front. Exit 4
    # from a real-but-broken suite is the contract we care about here.
    with pytest.raises(CollectionError):
        collect(hardfail_suite)


# === testpaths honoring (default collect scopes like bare pytest) =========


def test_collect_default_honors_testpaths(testpaths_suite):
    # With no targets there is no positional, so pytest applies
    # ``testpaths=[tests]`` exactly as bare ``pytest`` does. The extra/ dir
    # outside testpaths is not collected.
    result = collect(testpaths_suite)
    nodeids = {it["nodeid"] for it in result["items"]}
    assert "tests/test_in.py::test_in" in nodeids
    assert not any("test_out" in n for n in nodeids), nodeids
    assert result["errors"] == []


def test_collect_explicit_target_overrides_testpaths(testpaths_suite):
    # An explicit target rides as a positional and overrides testpaths: the user
    # asked for that path, mirroring ``pytest extra``.
    result = collect(testpaths_suite, [str(testpaths_suite / "extra")])
    nodeids = {it["nodeid"] for it in result["items"]}
    assert any("test_out" in n for n in nodeids), nodeids
    assert not any("test_in" in n for n in nodeids), nodeids


def test_collect_no_testpaths_collects_from_rootdir(no_testpaths_suite):
    # With no testpaths declared, pytest's default is the cwd (which is the
    # rootdir); the deck must still collect the whole tree, not nothing.
    result = collect(no_testpaths_suite)
    nodeids = {it["nodeid"] for it in result["items"]}
    assert "test_a.py::test_a" in nodeids
    assert "sub/test_b.py::test_b" in nodeids


# === /api/collect endpoint behavior =======================================


def test_api_collect_mixed_is_200_with_tree_and_errors(mixed_suite):
    async def body():
        app = create_app(mixed_suite)
        async with asgi_client(app) as client:
            r = await client.get("/api/collect")
            # A partial collection is data, not an error: 200, not 500.
            assert r.status_code == 200, r.text
            data = r.json()

            # Contract shape now includes errors alongside the tree.
            assert set(data) >= {"markers", "tree", "total", "rootdir", "errors"}

            # The good tests populate the (partial) tree.
            leaves = _leaf_nodeids(data["tree"])
            assert "test_good.py::test_ok" in leaves
            assert "test_good.py::test_ok2" in leaves
            assert data["total"] == 2
            assert "smoke" in data["markers"]

            # The broken file surfaces in errors[] with a coloured traceback.
            assert len(data["errors"]) == 1, data["errors"]
            err = data["errors"][0]
            assert "test_broken.py" in err["nodeid"]
            assert err["longrepr_text"] and "\x1b[" in err["longrepr_text"]

    run_async(body())


def test_api_collect_empty_is_200_with_empty_tree(empty_suite):
    async def body():
        app = create_app(empty_suite)
        async with asgi_client(app) as client:
            r = await client.get("/api/collect")
            assert r.status_code == 200
            data = r.json()
            assert data["total"] == 0
            assert data["tree"] == []
            assert data["errors"] == []
            assert data["markers"] == []

    run_async(body())


def test_api_collect_hard_failure_is_500(hardfail_suite):
    async def body():
        app = create_app(hardfail_suite)
        async with asgi_client(app) as client:
            r = await client.get("/api/collect")
            # A genuine hard failure (a conftest import error, exit 4) answers 500.
            assert r.status_code == 500, r.text
            assert "error" in r.json()

    run_async(body())
