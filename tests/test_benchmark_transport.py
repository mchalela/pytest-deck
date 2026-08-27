"""pytest-benchmark via the save-file ``json_file`` transport.

The manifest redirects ``--benchmark-save`` into the run tmpdir
(``--benchmark-storage=file://{tmpdir}/benchmarks``); the save-file path holds
ONE glob segment (``{tmpdir}/benchmarks/*/0001_deck.json`` — the middle dir is
a host-derived machine id) which the runner resolves post-exit. Save-file
``fullname`` IS the literal pytest nodeid (verified 5.2.3), so the slimmer
joins directly — no inner-plugin records.

Rides along (needed regardless of plugin): the ``SLIM_MAX_BYTES`` cap on
first-party slimmer reads (over-cap → ``plugin_empty`` with a ``reason``), and
the per-id ``SLIM_RENDERS`` map replacing the hardcoded ``render: "coverage"``.

Unit tests build ``_Run`` directly; the live end-to-end tests use the real
pytest-benchmark (installed in the dev venv, ``importorskip`` like the metadata tests).
"""

import asyncio
import json
from pathlib import Path

import pytest

from pytest_deck import plugin_data
from pytest_deck.plugin_data import SLIM_RENDERS, SLIMMERS
from pytest_deck.runner import (
    SLIM_MAX_BYTES,
    RunManager,
    _resolve_transport_path,
    _Run,
)

DATA = Path(__file__).parent / "data"


async def _drain(queue, until, timeout=60.0):
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


# The transports entry the server compiles for the curated benchmark manifest.
def _bench_transport():
    return {
        "plugin": "benchmark",
        "render": None,
        "path": "{tmpdir}/benchmarks/*/0001_deck.json",
    }


# === glob resolution ========================================================


def test_resolve_glob_free_path_passes_through(tmp_path):
    # Even for a nonexistent file: the read degrades there, not here.
    assert _resolve_transport_path(str(tmp_path / "cov.json")) == str(
        tmp_path / "cov.json"
    )


def test_resolve_glob_zero_matches_is_none(tmp_path):
    assert _resolve_transport_path(str(tmp_path / "b" / "*" / "0001_deck.json")) is None


def test_resolve_glob_one_match(tmp_path):
    d = tmp_path / "b" / "Linux-CPython-3.13-64bit"
    d.mkdir(parents=True)
    (d / "0001_deck.json").write_text("{}")
    assert _resolve_transport_path(str(tmp_path / "b" / "*" / "0001_deck.json")) == str(
        d / "0001_deck.json"
    )


def test_resolve_glob_multiple_matches_takes_lexicographically_last(tmp_path):
    # A fresh per-run tmpdir normally means exactly one match; last is the
    # pinned, deterministic tie-break.
    for name in ("aaa", "mmm", "zzz"):
        d = tmp_path / "b" / name
        d.mkdir(parents=True)
        (d / "0001_deck.json").write_text("{}")
    assert _resolve_transport_path(str(tmp_path / "b" / "*" / "0001_deck.json")) == str(
        tmp_path / "b" / "zzz" / "0001_deck.json"
    )


def test_read_transports_glob_zero_matches_emits_plugin_empty(tmp_path):
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[_bench_transport()],
    )
    assert run._read_transports() == [
        ("plugin_empty", {"run_id": "run-1", "plugin": "benchmark"})
    ]


def _write_save_file(tmpdir, payload_text, machine="Linux-CPython-3.13-64bit"):
    d = Path(tmpdir) / "benchmarks" / machine
    d.mkdir(parents=True)
    (d / "0001_deck.json").write_text(payload_text)


def test_read_transports_resolves_glob_and_slims(tmp_path):
    _write_save_file(tmp_path, (DATA / "benchmark_save.json").read_text())
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[_bench_transport()],
    )
    [(name, payload)] = run._read_transports()
    assert name == "plugin_data"
    assert payload["plugin"] == "benchmark"
    assert payload["render"] == "benchmark"
    assert "test_bench.py::test_fib_param[3]" in payload["data"]["tests"]
    assert payload["data"]["summary"]["count"] == 3


def test_zero_byte_save_file_degrades_to_plugin_empty(tmp_path):
    # The 0-byte-file pin: a benchmark interrupted mid-write (or an empty save)
    # parses as no JSON and degrades to plugin_empty, never a crash.
    _write_save_file(tmp_path, "")
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[_bench_transport()],
    )
    assert run._read_transports() == [
        ("plugin_empty", {"run_id": "run-1", "plugin": "benchmark"})
    ]


# === SLIM_MAX_BYTES (first-party read hardening) ============================


def test_slim_max_bytes_is_32_mib_not_the_render_cap():
    # Mutation pin: the value is deliberate. It is 32 MiB, not RENDER_MAX_BYTES
    # (256 KiB), because a large real cov.json must keep slimming (coverage
    # must not regress).
    assert SLIM_MAX_BYTES == 32 * 2**20
    assert SLIM_MAX_BYTES > plugin_data.RENDER_MAX_BYTES


def test_over_cap_reason_is_derived_from_the_constant():
    # Fix-2 pin: the human string is computed from SLIM_MAX_BYTES, not a
    # hardcoded "32 MiB" literal that could drift when the cap changes.
    import pytest_deck.runner as runner_mod

    assert runner_mod._over_cap_reason() == "output exceeded the 32 MiB cap"


def test_over_cap_slimmer_read_is_plugin_empty_with_reason(tmp_path, monkeypatch):
    # The boundary via a monkeypatched cap (writing real 32 MiB files is
    # wasteful): cap+1 bytes gives plugin_empty with the reason string, while
    # exactly at cap is read and slimmed normally. This proves the code reads
    # the module constant, and that the message follows the effective cap
    # (1 KiB here, never "32 MiB").
    import pytest_deck.runner as runner_mod

    monkeypatch.setattr(runner_mod, "SLIM_MAX_BYTES", 1024)
    over = tmp_path / "over.json"
    over.write_bytes(b"x" * 1025)
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[{"plugin": "pytest_cov", "render": None, "path": str(over)}],
    )
    assert run._read_transports() == [
        (
            "plugin_empty",
            {
                "run_id": "run-1",
                "plugin": "pytest_cov",
                "reason": "output exceeded the 1 KiB cap",
            },
        )
    ]


def test_at_cap_slimmer_read_still_slims(tmp_path, monkeypatch):
    import pytest_deck.runner as runner_mod

    doc = {"totals": {"percent_covered": 50.0}, "files": {}}
    raw = json.dumps(doc).encode()
    pad = b" " * (1024 - len(raw))  # trailing whitespace is valid JSON padding
    at_cap = tmp_path / "at.json"
    at_cap.write_bytes(raw + pad)
    assert at_cap.stat().st_size == 1024
    monkeypatch.setattr(runner_mod, "SLIM_MAX_BYTES", 1024)
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[{"plugin": "pytest_cov", "render": None, "path": str(at_cap)}],
    )
    [(name, payload)] = run._read_transports()
    assert name == "plugin_data"
    assert payload["render"] == "coverage"
    assert payload["data"]["total"] == 50.0


# === the render-cap degrade carries its own truthful reason =================


def test_over_render_cap_benchmark_slim_is_plugin_empty_with_reason(tmp_path):
    # Fix-1 pin (mutation-verified): a save file whose slimmed dict beats
    # RENDER_MAX_BYTES gives plugin_empty with a "too large (N results)" reason.
    # The suite ran and saved everything, so a plain plugin_empty (the slimmer
    # returning None, the reverted behavior) would render the false "no
    # benchmark fixtures ran" hint and fail this test's reason assertion.
    records = [
        {"fullname": f"t.py::test_{i}[{'x' * 300}]", "stats": {"mean": 1.0}}
        for i in range(2000)
    ]
    _write_save_file(tmp_path, json.dumps({"benchmarks": records}))
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[_bench_transport()],
    )
    assert run._read_transports() == [
        (
            "plugin_empty",
            {
                "run_id": "run-1",
                "plugin": "benchmark",
                "reason": "benchmark output too large to render (2000 results)",
            },
        )
    ]


def test_over_render_cap_metadata_slim_is_plugin_empty_with_reason():
    # The same pin for the fd3 branch (_read_fd3_transport must propagate the
    # SlimTooLarge degrade, not flatten it to a bare plugin_empty).
    run = _Run(
        "run-1",
        None,
        ".",
        [],
        None,
        None,
        transports=[{"plugin": "metadata", "render": None, "type": "fd3"}],
    )
    run._plugin_meta = {
        "blob": "x" * (plugin_data.RENDER_MAX_BYTES + 10),
        "Python": "3.13.1",
    }
    assert run._read_transports() == [
        (
            "plugin_empty",
            {
                "run_id": "run-1",
                "plugin": "metadata",
                "reason": "environment metadata too large to render (2 keys)",
            },
        )
    ]


# === the per-id render map ==================================================


def test_every_slimmer_id_has_a_render_map_entry():
    # A first-party slimmer without a wire render would emit render:null and
    # fall into the frontend's legacy-coverage branch, so declare both together.
    assert set(SLIM_RENDERS) == set(SLIMMERS)
    assert SLIM_RENDERS == {
        "pytest_cov": "coverage",
        "metadata": "metadata",
        "benchmark": "benchmark",
    }


def test_wire_render_comes_from_the_map_not_a_literal(tmp_path, monkeypatch):
    # Mutation check: change the map entry and the wire render follows; a
    # reintroduced hardcoded "coverage" would fail here.
    monkeypatch.setitem(plugin_data.SLIM_RENDERS, "pytest_cov", "mutated")
    cov = tmp_path / "cov.json"
    cov.write_text(json.dumps({"totals": {"percent_covered": 10.0}, "files": {}}))
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        tmpdir=str(tmp_path),
        transports=[{"plugin": "pytest_cov", "render": None, "path": str(cov)}],
    )
    [(_, payload)] = run._read_transports()
    assert payload["render"] == "mutated"


def test_fd3_wire_render_comes_from_the_map_too(tmp_path, monkeypatch):
    # The fd3 branch's literal "metadata" fell to the same map.
    monkeypatch.setitem(plugin_data.SLIM_RENDERS, "metadata", "mutated")
    run = _Run(
        "run-1",
        None,
        tmp_path,
        [],
        None,
        None,
        transports=[{"plugin": "metadata", "render": None, "type": "fd3"}],
    )
    run._plugin_meta = {"Python": "3.13.1"}
    [(_, payload)] = run._read_transports()
    assert payload["render"] == "mutated"


# === end to end with the real pytest-benchmark ==============================

SUITE = """\
import pytest

def work():
    return sum(range(50))

def test_bench_plain(benchmark):
    benchmark(work)

@pytest.mark.parametrize("n", [1, 2])
def test_bench_param(benchmark, n):
    benchmark(work)

def test_not_benchmarked():
    assert True
"""

# Keep the timing loop minimal; the e2e proves the pipe, not the numbers.
_FAST = ["--benchmark-min-rounds=1", "--benchmark-max-time=0.000001"]


def test_live_pytest_benchmark_end_to_end(tmp_path):
    # The whole pipe with the real plugin: `-p benchmark` under autoload-disable
    # (P13) plus the save/storage redirect puts the save file under the run
    # tmpdir, the runner glob-resolves the machine-id dir post-exit, and out
    # comes one plugin_data with render "benchmark", keyed by real nodeids
    # including a parametrized one, before finished (P18).
    pytest.importorskip("pytest_benchmark")
    (tmp_path / "test_b.py").write_text(SUITE)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_b.py"],
                extra_argv=[
                    "-p",
                    "benchmark",
                    "--benchmark-save=deck",
                    "--benchmark-storage=file://{tmpdir}/benchmarks",
                    *_FAST,
                ],
                transports=[_bench_transport()],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert names.count("plugin_data") == 1
            assert "plugin_empty" not in names
            assert names.index("plugin_data") < names.index("finished")
            pd = next(d for n, d in events if n == "plugin_data")
            assert pd["run_id"] == run_id
            assert pd["plugin"] == "benchmark"
            assert pd["render"] == "benchmark"
            tests = pd["data"]["tests"]
            # fullname == nodeid, byte for byte, including the [param] id; the
            # un-benchmarked test has no record.
            assert sorted(tests) == [
                "test_b.py::test_bench_param[1]",
                "test_b.py::test_bench_param[2]",
                "test_b.py::test_bench_plain",
            ]
            for rec in tests.values():
                assert rec["mean"] > 0
                assert rec["rounds"] >= 1
            assert pd["data"]["summary"]["count"] == 3
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
            # The storage redirect kept .benchmarks/ out of the user tree.
            assert not (tmp_path / ".benchmarks").exists()
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_live_zero_benchmark_run_emits_plugin_empty(tmp_path):
    # Enabled but no benchmark fixture ran: benchmark saves nothing (it warns
    # "not saving anything"), so there is no save file and exactly one
    # plugin_empty.
    pytest.importorskip("pytest_benchmark")
    (tmp_path / "test_b.py").write_text(SUITE)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            run_id = await mgr.start(
                ["test_b.py::test_not_benchmarked"],
                extra_argv=[
                    "-p",
                    "benchmark",
                    "--benchmark-save=deck",
                    "--benchmark-storage=file://{tmpdir}/benchmarks",
                ],
                transports=[_bench_transport()],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            assert names.index("plugin_empty") < names.index("finished")
            pe = next(d for n, d in events if n == "plugin_empty")
            assert pe == {"run_id": run_id, "plugin": "benchmark"}
        finally:
            await mgr.shutdown()

    asyncio.run(body())


def test_live_benchmark_disable_emits_plugin_empty(tmp_path):
    # The disable field: the fixture runs (the suite imports fine), the timing
    # loop is off, no save file is written, and so plugin_empty, by design.
    pytest.importorskip("pytest_benchmark")
    (tmp_path / "test_b.py").write_text(SUITE)

    async def body():
        mgr = RunManager(tmp_path)
        try:
            q = mgr.subscribe()
            await mgr.start(
                ["test_b.py::test_bench_plain"],
                extra_argv=[
                    "-p",
                    "benchmark",
                    "--benchmark-disable",
                    "--benchmark-save=deck",
                    "--benchmark-storage=file://{tmpdir}/benchmarks",
                ],
                transports=[_bench_transport()],
            )
            events = await _drain(q, lambda ns: "finished" in ns)
            names = [n for n, _ in events]
            assert "plugin_data" not in names
            assert names.count("plugin_empty") == 1
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    asyncio.run(body())
