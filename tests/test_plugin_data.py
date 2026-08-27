"""Tests for the post-run transport path (``plugin_data`` events).

Covers the ``pytest_cov`` slimmer against a real captured coverage JSON
(``tests/data/coverage.json``, a fixture generated once so the slimmer tests
need no pytest-cov), the live end-to-end filter when pytest-cov IS installed
(``test_live_bare_cov_excludes_deck_internals``), the runner's post-exit
transport read (fixture-manifest pattern: a test-only slimmer monkeypatched
into the registry, the child writes the file via the manifest ``[env]`` hook),
and the Part-1 stale-run regression: a dead-but-undrained old run may not
broadcast after the new run's ``started``.
"""

import asyncio
import json
import os
import site
import sysconfig
import types
from pathlib import Path

import pytest

from pytest_deck import plugin_data
from pytest_deck.events import Event
from pytest_deck.plugin_data import parse_mpl_index, slim
from pytest_deck.runner import RunManager, _Run

DATA = Path(__file__).parent / "data"


def run_async(coro):
    return asyncio.run(coro)


def _deck_is_editable():
    """True when ``pytest_deck`` is imported from a source checkout.

    Bare ``--cov`` measures a source checkout (the dogfooding pollution the
    slimmer filters) but not a site-packages install, which coverage.py
    excludes as third-party; the tox matrix installs the wheel, so tests that
    assert on the pollution itself must branch on this.
    """
    paths = sysconfig.get_paths()
    site_dirs = {paths["purelib"], paths["platlib"], *site.getsitepackages()}
    deck = os.path.realpath(plugin_data._DECK_DIR)
    return not any(deck.startswith(os.path.realpath(d) + os.sep) for d in site_dirs)


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


# === the real pytest_cov slimmer ===========================================


def test_pytest_cov_slimmer_over_real_report():
    raw = json.loads((DATA / "coverage.json").read_text())
    data = slim("pytest_cov", raw, "/anywhere")
    assert data == {
        "total": 75.0,
        "files": {"pkg/__init__.py": 100.0, "pkg/mathy.py": 75.0},
    }


def test_pytest_cov_slimmer_normalizes_absolute_paths():
    raw = {
        "totals": {"percent_covered": 50.0},
        "files": {"/repo/pkg/m.py": {"summary": {"percent_covered": 50.0}}},
    }
    assert slim("pytest_cov", raw, "/repo") == {
        "total": 50.0,
        "files": {"pkg/m.py": 50.0},
    }


def test_slim_unknown_plugin_returns_none():
    assert slim("nope", {"totals": {"percent_covered": 1.0}}, "/r") is None


# === coverage self-pollution filter ====================================
#
# Bare --cov measures the deck's own injected _inner (plus the package __init__)
# in the child, while terminal `pytest --cov` never sees them. Entries that sit
# under the deck's installed package and outside rootdir are dropped, and the
# total is recomputed from the survivors. Deck-under-rootdir (dogfooding) keeps
# everything.


def _cov_file(
    covered_lines, num_statements, pct, covered_branches=None, num_branches=None
):
    """A cov.json ``files`` entry with the summary keys the slimmer reads."""
    summary = {
        "covered_lines": covered_lines,
        "num_statements": num_statements,
        "percent_covered": pct,
        "missing_lines": num_statements - covered_lines,
        "excluded_lines": 0,
    }
    if num_branches is not None:
        summary["covered_branches"] = covered_branches
        summary["num_branches"] = num_branches
        summary["missing_branches"] = num_branches - covered_branches
        summary["num_partial_branches"] = 0
    return {"summary": summary}


def test_deck_entries_outside_rootdir_dropped_total_recomputed():
    # The demo-pollution case: game files plus the deck's own _inner/__init__
    # (absolute realpaths, as coverage's abs_file() writes them). The deck
    # entries vanish and the total is the game-only number, never cov.json's
    # polluted totals.
    raw = {
        "totals": {"percent_covered": 100.0 * 61 / 73},  # polluted roll-up
        "files": {
            "game/logic.py": _cov_file(6, 8, 75.0),
            "game/__init__.py": _cov_file(0, 0, 100.0),
            os.path.join(plugin_data._DECK_DIR, "_inner.py"): _cov_file(
                50, 60, 250 / 3
            ),
            os.path.join(plugin_data._DECK_DIR, "__init__.py"): _cov_file(5, 5, 100.0),
        },
    }
    assert slim("pytest_cov", raw, "/some/game/rootdir") == {
        "total": 75.0,  # 100 * 6 / 8, deck lines excluded
        "files": {"game/logic.py": 75.0, "game/__init__.py": 100.0},
    }


def test_dogfooding_deck_under_rootdir_kept_verbatim():
    # The under-rootdir carve-out: the deck run on its own repo, with pytest_deck
    # as the genuine --cov target. Nothing is dropped and cov.json's total passes
    # through verbatim.
    rootdir = os.path.dirname(plugin_data._DECK_DIR)
    raw = {
        "totals": {"percent_covered": 62.5},
        "files": {
            "pytest_deck/_inner.py": _cov_file(3, 5, 60.0),
            "pytest_deck/__init__.py": _cov_file(2, 3, 100 * 2 / 3),
        },
    }
    assert slim("pytest_cov", raw, rootdir) == {
        "total": 62.5,
        "files": {
            "pytest_deck/_inner.py": 60.0,
            "pytest_deck/__init__.py": 100 * 2 / 3,
        },
    }


def test_user_dir_named_pytest_deck_is_not_dropped():
    # The rule is a path-prefix match against the installed package location,
    # never a name match, so a user project's own pytest_deck/ dir stays.
    raw = {
        "totals": {"percent_covered": 50.0},
        "files": {"pytest_deck/util.py": _cov_file(1, 2, 50.0)},
    }
    assert slim("pytest_cov", raw, "/some/user/project") == {
        "total": 50.0,
        "files": {"pytest_deck/util.py": 50.0},
    }


def test_outside_rootdir_user_source_is_kept():
    # Condition (ii) alone never drops anything: outside-rootdir user source that
    # terminal pytest-cov reports stays (dead no-gutter rows are correct).
    raw = {
        "totals": {"percent_covered": 50.0},
        "files": {"/opt/otherlib/mod.py": _cov_file(1, 2, 50.0)},
    }
    assert slim("pytest_cov", raw, "/some/user/project") == {
        "total": 50.0,
        "files": {os.path.relpath("/opt/otherlib/mod.py", "/some/user/project"): 50.0},
    }


def test_branch_mode_recompute_after_drop():
    # --cov-branch: the recompute must include the branch terms, so covered
    # 3+1 over 4+2 rather than the statement-only 3/4.
    raw = {
        "totals": {"percent_covered": 90.0},  # polluted, must not survive
        "files": {
            "game/logic.py": _cov_file(
                3, 4, 100 * 4 / 6, covered_branches=1, num_branches=2
            ),
            os.path.join(plugin_data._DECK_DIR, "_inner.py"): _cov_file(
                9, 10, 95.0, covered_branches=10, num_branches=10
            ),
        },
    }
    data = slim("pytest_cov", raw, "/some/game/rootdir")
    assert data["total"] == 100.0 * 4 / 6
    assert list(data["files"]) == ["game/logic.py"]


def test_recompute_zero_denominator_is_100():
    # coverage's Numbers._percent treats an empty denominator as 100.0 (say the
    # only survivor is an empty __init__.py).
    raw = {
        "totals": {"percent_covered": 20.0},
        "files": {
            "pkg/__init__.py": _cov_file(0, 0, 100.0),
            os.path.join(plugin_data._DECK_DIR, "_inner.py"): _cov_file(1, 5, 20.0),
        },
    }
    assert slim("pytest_cov", raw, "/some/game/rootdir")["total"] == 100.0


def test_all_deck_entries_dropped_returns_none():
    # A panel of deck internals is worse than the "enabled but no data" hint;
    # terminal pytest-cov would say "no data was collected" here. None rides
    # the existing plugin_empty path (test_slim_returns_none_emits_plugin_empty).
    raw = {
        "totals": {"percent_covered": 91.0},
        "files": {
            os.path.join(plugin_data._DECK_DIR, "_inner.py"): _cov_file(9, 10, 90.0),
            os.path.join(plugin_data._DECK_DIR, "__init__.py"): _cov_file(2, 2, 100.0),
        },
    }
    assert slim("pytest_cov", raw, "/some/game/rootdir") is None


def test_cov_total_matches_coverage_py_branch_total(tmp_path):
    # Pin _cov_total against coverage.py's own roll-up on a real branch-mode
    # report over an unfiltered set, so formula drift fails loudly (the
    # dead-key-space lesson: verify against real output, not a guess).
    coverage = pytest.importorskip("coverage")
    mod = tmp_path / "mod.py"
    mod.write_text(
        "def f(x):\n"
        "    if x:\n"
        "        return 1\n"
        "    return 2\n"
        "\n"
        "def g():\n"
        "    return 3\n"
    )
    # config_file=False ignores ambient config: the repo's own pyproject sets
    # `source = ["pytest_deck"]`, which would exclude tmp_path from measurement.
    cov = coverage.Coverage(
        branch=True, data_file=str(tmp_path / ".coverage"), config_file=False
    )
    cov.start()
    ns = {}
    exec(compile(mod.read_text(), str(mod), "exec"), ns)
    ns["f"](True)  # one branch taken, one missed; g() never runs
    cov.stop()
    out = tmp_path / "cov.json"
    cov.json_report(morfs=[str(mod)], outfile=str(out))
    raw = json.loads(out.read_text())
    totals = raw["totals"]
    # Preconditions: genuinely partial, with branch terms in play, so a
    # statement-only formula would not reproduce this number.
    assert totals["num_branches"] > 0 and totals["missing_branches"] > 0
    assert 0 < totals["percent_covered"] < 100
    covered_lines = totals["covered_lines"]
    num_statements = totals["num_statements"]
    assert 100.0 * covered_lines / num_statements != totals["percent_covered"]
    summaries = [info["summary"] for info in raw["files"].values()]
    assert plugin_data._cov_total(summaries) == totals["percent_covered"]


# === the metadata slimmer =============================================


def test_metadata_slimmer_passes_dicts_and_stringifies_scalars():
    # Nested dicts (Packages/Plugins) pass through for JsonTree; scalar values
    # stringify defensively (--metadata via extra args can inject non-strings).
    raw = {
        "Python": "3.13.1",
        "Build": 42,
        "CI": True,
        "Packages": {"pytest": "9.1.1", "rev": 7},
    }
    assert slim("metadata", raw, "/r") == {
        "Python": "3.13.1",
        "Build": "42",
        "CI": "True",
        "Packages": {"pytest": "9.1.1", "rev": "7"},
    }


def test_metadata_slimmer_rejects_non_dict_and_empty():
    # Degrades exactly like a missing transport file (that is, plugin_empty).
    assert slim("metadata", [], "/r") is None
    assert slim("metadata", {}, "/r") is None
    assert slim("metadata", "linux", "/r") is None
    assert slim("metadata", None, "/r") is None


def test_metadata_slimmer_caps_size_with_truthful_reason():
    # About 400 B in practice; the RENDER_MAX_BYTES cap is pure defense. Over
    # the cap it degrades to SlimTooLarge (plugin_empty with a reason: the data
    # exists, it just can't ride the wire), never an oversized SSE payload and
    # never a bare None that would render as "no data reported".
    big = {"blob": "x" * (plugin_data.RENDER_MAX_BYTES + 10), "Python": "3.13"}
    result = slim("metadata", big, "/r")
    assert isinstance(result, plugin_data.SlimTooLarge)
    assert result.reason == "environment metadata too large to render (2 keys)"


def test_slim_malformed_raw_is_quiet():
    # Degrades exactly like a missing file: no event, no exception.
    assert slim("pytest_cov", {"totals": None}, "/r") is None
    assert slim("pytest_cov", [], "/r") is None


# === the benchmark slimmer ====================================


def test_benchmark_slimmer_over_real_save_file():
    # tests/data/benchmark_save.json is a real --benchmark-save file captured
    # from pytest-benchmark 5.2.3 (the dead-key-space lesson: verify against
    # real output, not a hand-typed shape). `fullname` is the literal nodeid,
    # so it keys directly, parametrized ones included.
    raw = json.loads((DATA / "benchmark_save.json").read_text())
    data = slim("benchmark", raw, "/anywhere")
    assert sorted(data["tests"]) == [
        "test_bench.py::test_fib_param[3]",
        "test_bench.py::test_fib_param[8]",
        "test_bench.py::test_fib_small",
    ]
    # The wire stats subset and nothing else (quartiles/outliers stay in the raw).
    rec = data["tests"]["test_bench.py::test_fib_small"]
    assert sorted(rec) == sorted(
        ["min", "max", "mean", "stddev", "median", "iqr", "ops", "rounds", "iterations"]
    )
    assert rec["min"] > 0 and rec["max"] >= rec["min"]
    assert isinstance(rec["rounds"], int)
    # Summary: count + fastest/slowest by mean, for the run-panel line.
    means = {n: r["mean"] for n, r in data["tests"].items()}
    assert data["summary"]["count"] == 3
    assert data["summary"]["fastest"]["nodeid"] == min(means, key=means.get)
    assert data["summary"]["slowest"]["nodeid"] == max(means, key=means.get)
    assert data["summary"]["fastest"]["mean"] == min(means.values())


def test_benchmark_slimmer_tolerates_absent_and_malformed_records():
    # A benchmarked callable that raises writes no record at all, and a record
    # missing fullname, stats, or a numeric mean is schema drift, skipped quietly.
    raw = {
        "benchmarks": [
            {"fullname": "t.py::ok", "stats": {"mean": 1.0, "rounds": 3}},
            {"stats": {"mean": 2.0}},  # no fullname
            {"fullname": "t.py::no_stats"},
            {"fullname": "t.py::bad_mean", "stats": {"mean": "fast"}},
            "not-a-dict",
            {"fullname": "", "stats": {"mean": 3.0}},
        ]
    }
    data = slim("benchmark", raw, "/r")
    assert list(data["tests"]) == ["t.py::ok"]
    assert data["summary"]["count"] == 1
    # Missing subset keys land as None, not KeyError.
    assert data["tests"]["t.py::ok"]["iqr"] is None


def test_benchmark_slimmer_rejects_bad_shapes():
    # Degrades exactly like a missing file (plugin_empty): non-dict raw, no
    # benchmarks list, empty list, zero usable records.
    assert slim("benchmark", [], "/r") is None
    assert slim("benchmark", {}, "/r") is None
    assert slim("benchmark", {"benchmarks": "x"}, "/r") is None
    assert slim("benchmark", {"benchmarks": []}, "/r") is None
    assert slim("benchmark", {"benchmarks": [{"name": "n"}]}, "/r") is None


def test_benchmark_slimmer_caps_size_with_truthful_reason():
    # Same output cap as the metadata slimmer; it bites at roughly 830+ records
    # (about 317 B each). The suite did run and the save file has everything, so
    # the degrade is SlimTooLarge (plugin_empty with "too large (N results)"),
    # never a bare None whose generic hint would claim no benchmark fixtures ran.
    raw = {
        "benchmarks": [
            {"fullname": f"t.py::test_{i}[{'x' * 300}]", "stats": {"mean": 1.0}}
            for i in range(2000)
        ]
    }
    result = slim("benchmark", raw, "/r")
    assert isinstance(result, plugin_data.SlimTooLarge)
    assert result.reason == "benchmark output too large to render (2000 results)"


# === runner: post-exit transport read, then plugin_data before finished ====


def _fake_slimmer(raw, rootdir):
    return {"total": raw["t"], "files": {}}


def test_plugin_data_streams_after_exit_before_finished(tmp_path, monkeypatch):
    monkeypatch.setitem(plugin_data.SLIMMERS, "testplug", _fake_slimmer)
    # A first-party slimmer declares its wire render in SLIM_RENDERS (the
    # render-map rule), so the fixture plugin registers one like a real id.
    monkeypatch.setitem(plugin_data.SLIM_RENDERS, "testplug", "coverage")
    # The child writes the transport file itself, into the run tmpdir it learns
    # via the manifest [env] hook: the same wiring a real plugin would use.
    (tmp_path / "test_writer.py").write_text(
        "import json, os\n"
        "\n"
        "def test_write_report():\n"
        "    with open(os.environ['FAKE_OUT'], 'w') as f:\n"
        "        json.dump({'t': 88.5}, f)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_writer.py::test_write_report"],
                env_templates={"FAKE_OUT": "{tmpdir}/fake.json"},
                transports=[{"plugin": "testplug", "path": "{tmpdir}/fake.json"}],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            # Exactly one plugin_data, strictly before the terminal finished,
            # and no plugin_empty for a plugin that produced data.
            assert names.count("plugin_data") == 1
            assert "plugin_empty" not in names
            assert names.index("plugin_data") < names.index("finished")
            # And after every report (the file is read post-exit).
            last_report = max(i for i, n in enumerate(names) if n == "report")
            assert names.index("plugin_data") > last_report
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd == {
                "run_id": run_id,
                "plugin": "testplug",
                "render": "coverage",
                "data": {"total": 88.5, "files": {}},
            }
        finally:
            await mgr.shutdown()

    run_async(body())


def test_missing_transport_file_emits_plugin_empty(tmp_path, monkeypatch):
    # An absent file (switch declared on but no output, say --no-cov via
    # extras) means plugin_empty: not plugin_data, and not silence.
    monkeypatch.setitem(plugin_data.SLIMMERS, "testplug", _fake_slimmer)
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                transports=[{"plugin": "testplug", "path": "{tmpdir}/absent.json"}],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            assert names.index("plugin_empty") < names.index("finished")
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "testplug"}
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    run_async(body())


def test_slim_returns_none_emits_plugin_empty(tmp_path, monkeypatch):
    # File present but the slimmer returns None (coverage's no-data shape), so
    # plugin_empty. The child writes a valid JSON the slimmer can't use.
    monkeypatch.setitem(
        plugin_data.SLIMMERS, "pytest_cov", plugin_data._slim_pytest_cov
    )
    (tmp_path / "test_writer.py").write_text(
        "import json, os\n"
        "\n"
        "def test_write_empty():\n"
        "    # coverage 'no data' shape: totals without percent_covered.\n"
        "    with open(os.environ['FAKE_OUT'], 'w') as f:\n"
        "        json.dump({'totals': {}, 'files': {}}, f)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_writer.py::test_write_empty"],
                env_templates={"FAKE_OUT": "{tmpdir}/cov.json"},
                transports=[{"plugin": "pytest_cov", "path": "{tmpdir}/cov.json"}],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "pytest_cov"}
        finally:
            await mgr.shutdown()

    run_async(body())


def test_no_transport_declared_emits_neither(tmp_path):
    # Coverage not enabled at all: no plugin_data and no plugin_empty.
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(["test_quick.py::test_ok"])
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert "plugin_empty" not in names
        finally:
            await mgr.shutdown()

    run_async(body())


def test_raw_transport_file_survives_in_tmpdir(tmp_path, monkeypatch):
    # The gutter endpoint reads the raw file later, so slimming must not delete it.
    monkeypatch.setitem(plugin_data.SLIMMERS, "testplug", _fake_slimmer)
    (tmp_path / "test_writer.py").write_text(
        "import json, os\n"
        "\n"
        "def test_write_report():\n"
        "    with open(os.environ['FAKE_OUT'], 'w') as f:\n"
        "        json.dump({'t': 1.0}, f)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_writer.py::test_write_report"],
                env_templates={"FAKE_OUT": "{tmpdir}/fake.json"},
                transports=[{"plugin": "testplug", "path": "{tmpdir}/fake.json"}],
            )
            await _drain(q, lambda ns: "finished" in ns)
            assert (Path(mgr._tmpdir) / "fake.json").is_file()
        finally:
            await mgr.shutdown()

    run_async(body())


def test_live_bare_cov_excludes_deck_internals(tmp_path):
    # End to end: a bare --cov run through the deck (empty Source field) measures
    # the injected pytest_deck._inner in the child (the raw cov.json proves it
    # below), but the panel data must not show it, and the total must be the
    # survivors' number (terminal `pytest --cov` never measures the deck).
    # Wiring mirrors manifests/coverage.toml.
    pytest.importorskip("pytest_cov")
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_quick.py").write_text(
        "import mod\n\n\ndef test_ok():\n    assert mod.add(1, 1) == 2\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_quick.py::test_ok"],
                extra_argv=[
                    "-p",
                    "pytest_cov",
                    "--cov",
                    "--cov-report=json:{tmpdir}/cov.json",
                ],
                env_templates={"COVERAGE_FILE": "{tmpdir}/.coverage"},
                transports=[{"plugin": "pytest_cov", "path": "{tmpdir}/cov.json"}],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1, names
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            assert pd["render"] == "coverage"
            data = pd["data"]
            assert "mod.py" in data["files"]
            root = os.path.realpath(str(tmp_path))
            deck_prefix = plugin_data._DECK_DIR + os.sep
            for path in data["files"]:
                resolved = os.path.realpath(os.path.join(root, path))
                assert not resolved.startswith(deck_prefix), path
            raw = json.loads((Path(mgr._tmpdir) / "cov.json").read_text())
            deck_keys = [
                key
                for key in raw["files"]
                if os.path.realpath(os.path.join(root, key)).startswith(deck_prefix)
            ]
            # Survivors (mod.py + test_quick.py) are fully executed, so the
            # panel total is 100 either way.
            assert data["total"] == 100.0
            if _deck_is_editable():
                # Not vacuous: the raw report really did measure deck internals
                # (the dogfooding find) and its polluted total must not leak
                # through; the slimmer dropped them and recomputed.
                assert deck_keys, "expected the child to have measured deck internals"
                assert raw["totals"]["percent_covered"] < 100.0
            else:
                # Site-packages install (tox matrix, a user's venv): coverage.py
                # excludes third-party packages from bare --cov, nothing pollutes,
                # and the cov.json total passes through verbatim.
                assert not deck_keys, deck_keys
                assert raw["totals"]["percent_covered"] == 100.0
        finally:
            await mgr.shutdown()

    run_async(body())


# === mpl results.json index parser =====================================


def test_parse_mpl_index_real_schema():
    # The real pytest-mpl results.json shape: dotted keys, relative image paths,
    # None for fields mpl didn't produce.
    raw = {
        "tests.test_fig.test_plot": {
            "status": "failed",
            "result_image": "tests.test_fig.test_plot/result.png",
            "baseline_image": "tests.test_fig.test_plot/baseline.png",
            "diff_image": "tests.test_fig.test_plot/diff.png",
        },
        "tests.test_fig.test_ok": {
            "status": "passed",
            "result_image": None,
            "baseline_image": None,
            "diff_image": None,
        },
    }
    out = parse_mpl_index(raw)
    assert out == {
        "tests.test_fig.test_plot": [
            {
                "name": "result",
                "rel_path": "tests.test_fig.test_plot/result.png",
                "kind": "image",
            },
            {
                "name": "baseline",
                "rel_path": "tests.test_fig.test_plot/baseline.png",
                "kind": "image",
            },
            {
                "name": "diff",
                "rel_path": "tests.test_fig.test_plot/diff.png",
                "kind": "image",
            },
        ]
    }
    # A test with no image fields contributes no entry (nothing to attach).
    assert "tests.test_fig.test_ok" not in out


def test_parse_mpl_index_kind_by_extension():
    raw = {"m.t": {"result_image": "m.t/data.csv"}}
    assert parse_mpl_index(raw)["m.t"][0]["kind"] == "file"


def test_parse_mpl_index_degrades_on_bad_shape():
    # Schema drift yields {} (no crash), so the runner emits plugin_empty (P18).
    assert parse_mpl_index([1, 2, 3]) == {}
    assert parse_mpl_index("nope") == {}
    assert parse_mpl_index({"m.t": "not-a-dict"}) == {}


def test_read_artifact_transport_joins_and_degrades(tmp_path):
    # Direct _read_artifact_transport: index present and join map hit gives a
    # payload; an unjoinable key is dropped; an absent index gives None
    # (plugin_empty).
    run = _Run("run-9", None, tmp_path, [], None, None)
    run._mpl_names = {"pkg.mod.test_a": "pkg/mod.py::test_a"}
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "results.json").write_text(
        json.dumps(
            {
                "pkg.mod.test_a": {"result_image": "a/result.png"},
                "pkg.mod.test_unknown": {"result_image": "b/result.png"},
            }
        )
    )
    payload = run._read_artifact_transport(
        "pytest_mpl", str(root), "results.json", "mpl"
    )
    assert payload == {
        "run_id": "run-9",
        "plugin": "pytest_mpl",
        "render": "artifacts",
        "data": {
            "pkg/mod.py::test_a": [
                {"name": "result", "rel_path": "a/result.png", "kind": "image"}
            ]
        },
    }
    # An absent index file gives None (plugin_empty).
    assert (
        run._read_artifact_transport("pytest_mpl", str(root), "gone.json", "mpl")
        is None
    )
    # When nothing joins (empty map) it is None, never an empty-data payload.
    run._mpl_names = {}
    assert (
        run._read_artifact_transport("pytest_mpl", str(root), "results.json", "mpl")
        is None
    )


def test_read_artifact_transport_caps_over_cap_index(tmp_path):
    # An over-cap results.json is refused before json.loads and gives None
    # (plugin_empty), so a huge index can't blow the runner-thread budget.
    run = _Run("run-cap", None, tmp_path, [], None, None)
    run._mpl_names = {"pkg.mod.test_a": "pkg/mod.py::test_a"}
    root = tmp_path / "artifacts"
    root.mkdir()
    big = plugin_data.RENDER_MAX_BYTES + 4096
    (root / "results.json").write_text(json.dumps({"blob": "x" * big}))
    assert (
        run._read_artifact_transport("pytest_mpl", str(root), "results.json", "mpl")
        is None
    )


def test_read_artifact_transport_reads_just_under_cap(tmp_path):
    # A valid index just under the cap still parses and joins (the cap doesn't
    # cut off legitimate indexes).
    run = _Run("run-under", None, tmp_path, [], None, None)
    run._mpl_names = {"pkg.mod.test_a": "pkg/mod.py::test_a"}
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "results.json").write_text(
        json.dumps({"pkg.mod.test_a": {"result_image": "a/result.png"}})
    )
    payload = run._read_artifact_transport(
        "pytest_mpl", str(root), "results.json", "mpl"
    )
    assert payload is not None
    assert "pkg/mod.py::test_a" in payload["data"]


# === render_payload over-cap signal (bulk-data json firehose) ==============


def test_render_json_over_cap_reports_true_size_and_keys(tmp_path):
    # A big json artifact (the common bulk-data shape: a small scaffold plus one
    # enormous embedded array) is refused, but the marker names the true size and
    # top-level keys so the panel is actionable, not a dead "too large".
    big = tmp_path / "big.json"
    payload = {
        "summary": {"total": 3},
        "meta": {"id": "abc"},
        "records": [{"samples": list(range(200000))}],
    }
    big.write_text(json.dumps(payload))
    true_size = big.stat().st_size
    assert true_size > plugin_data.RENDER_MAX_BYTES  # genuinely over cap

    data, truncated = plugin_data.render_payload("json", str(big))
    assert truncated is True
    assert data["_truncated"] is True
    # The true file size, not the capped read (256 KiB); that was the old bug.
    assert data["bytes"] == true_size
    assert data["keys"] == ["summary", "meta", "records"]


def test_render_json_under_cap_parses_normally(tmp_path):
    small = tmp_path / "small.json"
    small.write_text(json.dumps({"a": 1, "b": [1, 2, 3]}))
    data, truncated = plugin_data.render_payload("json", str(small))
    assert truncated is False
    assert data == {"a": 1, "b": [1, 2, 3]}


def test_top_level_keys_degrades_on_odd_input():
    # The bounded key scanner never raises; non-object, garbage, truncated and
    # empty prefixes all yield [].
    tlk = plugin_data._top_level_keys
    assert tlk(b'{"a": 1, "b": 2}') == ["a", "b"]
    assert tlk(b"[1, 2, 3]") == []  # array top, not an object
    assert tlk(b"not json at all") == []
    assert tlk(b'{"machine_info": {"nod') == ["machine_info"]  # truncated mid-value
    assert tlk(b"") == []
    # A value that is itself a string must not be mistaken for a key.
    assert tlk(b'{"k": "a value", "k2": 2}') == ["k", "k2"]


def test_top_level_keys_caps_the_list():
    # A pathological object with thousands of keys reports at most _KEY_SCAN_MAX.
    many = "{" + ",".join(f'"k{i}": {i}' for i in range(500)) + "}"
    keys = plugin_data._top_level_keys(many.encode())
    assert len(keys) == plugin_data._KEY_SCAN_MAX


# === Part 1: the stale-run event leak ======================================


def test_stale_run_events_never_land_after_new_started(tmp_path):
    """A dead-proc old run with an undrained reader is joined before start.

    Fabricates the race directly: the old run's proc has exited
    (``is_alive`` False — the path that used to skip ``join()``) but a reader
    task still holds a buffered line it will broadcast. The fix joins
    unconditionally, so the stale event must flush BEFORE the new ``started``.
    """
    (tmp_path / "test_quick.py").write_text("def test_ok():\n    assert True\n")

    async def body():
        mgr = RunManager(tmp_path)
        q = mgr.subscribe()

        old = _Run("run-0", mgr, tmp_path, [], None, None)
        old.proc = types.SimpleNamespace(returncode=0)  # already exited
        old._done.set()

        async def stale_reader():
            # Simulates a reader draining buffered fd-3 lines post-exit.
            await asyncio.sleep(0.05)
            mgr.broadcast(Event("report", {"run_id": "run-0"}))

        old._tasks = [asyncio.get_running_loop().create_task(stale_reader())]
        mgr._run = old

        try:
            await mgr.start(["test_quick.py::test_ok"])
            events = await _drain(q, lambda ns: "finished" in ns)
            started_i = next(i for i, (n, _) in enumerate(events) if n == "started")
            stale_i = [
                i for i, (_, d) in enumerate(events) if d.get("run_id") == "run-0"
            ]
            # The stale report was flushed, and strictly before `started`.
            assert stale_i and all(i < started_i for i in stale_i)
        finally:
            await mgr.shutdown()

    run_async(body())


def test_plugin_empty_not_emitted_on_cancel(tmp_path, monkeypatch):
    # Guard: cancel/kill path emits `cancelled`, never plugin_data/empty.
    monkeypatch.setitem(plugin_data.SLIMMERS, "testplug", _fake_slimmer)
    (tmp_path / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5.0)\n"
    )

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_slow.py::test_slow"],
                transports=[{"plugin": "testplug", "path": "{tmpdir}/absent.json"}],
            )
            await _drain(q, lambda ns: "started" in ns, timeout=10.0)
            cancelled, _ = await mgr.cancel()
            assert cancelled
            events = await _drain(q, lambda ns: "cancelled" in ns, timeout=10.0)
            names = [n for n, _ in events]
            assert "plugin_empty" not in names
            assert "plugin_data" not in names
        finally:
            await mgr.shutdown()

    run_async(body())
