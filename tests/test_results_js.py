"""Characterization tests for the frontend live-results store
(`frontend/src/lib/results.svelte.js`).

They lock CURRENT behavior of the exported surface: every store mutation plus
the SSE event appliers (onStarted…onError) that connection.js calls. Where a
behavior looks odd (e.g. `reconcileResults` ghosts even result-less dropped
records) it is locked as-is, not "fixed" here.

`results.svelte.js` is transport-free (connection.js owns the EventSource and
api calls) and uses exactly three Svelte 5 ``$state(...)`` runes. Same
node-shell pattern as `test_diff_js.py`: load the REAL module with each
``$state(`` neutralized to an identity wrapper and `./outcome.js` re-pointed
at the real file.

``node`` is required; tests skip cleanly if it's absent (as in
test_outcome_js_parity.py / test_diff_js.py).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
_RESULTS_JS = _LIB / "results.svelte.js"
_OUTCOME_JS = _LIB / "outcome.js"
_ANNOT_JS = _LIB / "annotations.svelte.js"
_COVVIEW_JS = _LIB / "coverageView.svelte.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def _run_node(script, stdin_payload):
    """Run an ES-module script under node, feeding JSON on stdin; parse stdout."""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    return json.loads(proc.stdout)


def _import_url(path):
    return json.dumps(path.as_uri())


# The harness imports the (neutralized) real module and replays a list of named
# operations from stdin. The "seed*" ops poke store state directly (the stores
# are plain objects once the rune is neutralized); the rest call the real
# exported functions. snapRef/sameRef capture and compare object identity, so
# "kept verbatim" (F2) is provable rather than merely deep-equal.
_HARNESS = """
import {{ results, ghosts, run, outcomeFor, resultFor, failedNodeids,
  runInFlight, markRunning,
  markRunRejected, markServerDown, markReconnecting, unstickOrphanedRun,
  clearServerDown, clearResults, reconcileResults, clearPluginData,
  coverageTotal, coverageFiles, coverageEmpty, metadataInfo, metadataEmpty,
  benchmarkData, benchmarkSummary, benchmarkEmpty, pluginEmptyReason,
  humanTime, renderSections,
  jsonTruncatedBytes, artifactsFor, artifactUrl, onStarted, onReport,
  onWarning, onConsole, onFinished,
  onCancelled, onError, onPluginData, onPluginEmpty }} from {results_url};
// The SAME neutralized annotations module instance results imports — shared
// state, so per-file plugin annotations are observable here.
import {{ annotations }} from {annotations_url};
// Same for the coverage-view module: results.clearPluginData closes it on a
// new run, and we assert that here via the shared instance.
import {{ coverageView, openCoverage }} from {covview_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  const refs = {{}};
  const store = (name) => (name === "ghosts" ? ghosts : results);
  for (const op of ops) {{
    switch (op.op) {{
      case "seed": results.byId[op.id] = op.record; break;
      case "seedGhost": ghosts.byId[op.id] = op.record; break;
      case "seedRun": Object.assign(run, op.fields); break;
      case "markRunning": markRunning(op.ids); break;
      case "markRunRejected": markRunRejected(op.message); break;
      case "markServerDown": markServerDown(); break;
      case "markReconnecting": markReconnecting(); break;
      case "unstickOrphanedRun": unstickOrphanedRun(); break;
      case "clearServerDown": clearServerDown(); break;
      case "clearResults": clearResults(); break;
      case "reconcile": returns.push(reconcileResults(new Set(op.ids))); break;
      case "outcomeFor": returns.push(outcomeFor(op.id)); break;
      case "resultFor": returns.push(resultFor(op.id)); break;
      case "failedNodeids": returns.push(failedNodeids()); break;
      case "runInFlight": returns.push(runInFlight()); break;
      case "onStarted": onStarted(op.data); break;
      case "onReport": onReport(op.data); break;
      case "onWarning": onWarning(op.data); break;
      case "onConsole": onConsole(op.data); break;
      case "onFinished": onFinished(op.data); break;
      case "onCancelled": onCancelled(op.data); break;
      case "onError": onError(op.data); break;
      case "onPluginData": onPluginData(op.data); break;
      case "onPluginEmpty": onPluginEmpty(op.data); break;
      case "clearPluginData": clearPluginData(); break;
      case "seedAnnotation":
        annotations.byId[op.id] = {{ ...(annotations.byId[op.id] || {{}}),
          [op.channel]: op.value }}; break;
      case "coverageTotal": returns.push(coverageTotal()); break;
      case "coverageFiles": returns.push(coverageFiles()); break;
      case "coverageEmpty": returns.push(coverageEmpty()); break;
      case "metadataInfo": returns.push(metadataInfo()); break;
      case "metadataEmpty": returns.push(metadataEmpty()); break;
      case "benchmarkData": returns.push(benchmarkData()); break;
      case "benchmarkSummary": returns.push(benchmarkSummary()); break;
      case "benchmarkEmpty": returns.push(benchmarkEmpty()); break;
      case "pluginEmptyReason": returns.push(pluginEmptyReason(op.id)); break;
      case "humanTime": returns.push(humanTime(op.value)); break;
      case "renderSections": returns.push(renderSections()); break;
      case "jsonTruncatedBytes":
        returns.push(jsonTruncatedBytes(op.data)); break;
      case "artifactsFor": returns.push(artifactsFor(op.id)); break;
      case "artifactUrl":
        returns.push(artifactUrl(op.runId, op.relPath)); break;
      case "openCoverageView": openCoverage(op.data); break;
      case "snapCoverageView": returns.push({{ ...coverageView }}); break;
      // Mirrors RunConsole.openFile's post-await guard: apply the fetched
      // result only if the run.id captured before the await still matches.
      case "guardedOpen":
        returns.push(op.rid === run.id);
        if (op.rid === run.id) openCoverage(op.data);
        break;
      case "snapRun": returns.push({{ ...run }}); break;
      case "snapRef": refs[op.key] = store(op.store).byId[op.id]; break;
      case "sameRef":
        returns.push(store(op.store).byId[op.id] === refs[op.key]); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{
    results: results.byId, ghosts: ghosts.byId, run: run, returns: returns,
    annotations: annotations.byId, coverageView: coverageView,
  }}));
}});
"""


def _neutralized_annotations_module(tmp_path):
    """Copy annotations.svelte.js with its single $state neutralized (same
    identity-swap as test_diff_js.py — asserted safe there too)."""
    src = _ANNOT_JS.read_text()
    assert src.count("$state(") == 1, "expected exactly one $state( to neutralize"
    out = tmp_path / "annotations_neutralized.mjs"
    out.write_text(src.replace("$state(", "("))
    return out


def _neutralized_coverageview_module(tmp_path):
    """Copy coverageView.svelte.js with its single $state neutralized
    (results.clearPluginData calls closeCoverage on a new run)."""
    src = _COVVIEW_JS.read_text()
    assert src.count("$state(") == 1, "expected exactly one $state( to neutralize"
    out = tmp_path / "coverageview_neutralized.mjs"
    out.write_text(src.replace("$state(", "("))
    return out


def _neutralized_results_module(tmp_path, annotations_mod, covview_mod):
    """Copy results.svelte.js with $state neutralized and imports re-pointed.

    * each of the three ``$state(`` → ``(`` (identity — plain objects);
    * ``./outcome.js`` → the real outcome.js by absolute file URL;
    * ``./annotations.svelte.js`` → the shared NEUTRALIZED annotations module
      (onPluginData writes plugin channels there);
    * ``./coverageView.svelte.js`` → the NEUTRALIZED coverage-view module (where
      clearPluginData calls closeCoverage).
    Everything else — every exported mutation body — is verbatim. The module
    is transport-free (connection.js owns the EventSource + api calls), so no
    api stub is needed; a reappearing api import should fail this harness.
    """
    src = _RESULTS_JS.read_text()
    assert src.count("$state(") == 3, "expected exactly three $state( to neutralize"
    out_src = src.replace("$state(", "(")

    assert '"./outcome.js"' in out_src
    assert '"./annotations.svelte.js"' in out_src
    assert '"./coverageView.svelte.js"' in out_src
    assert '"./api.js"' not in out_src, "results.svelte.js must stay transport-free"
    out_src = out_src.replace('"./outcome.js"', json.dumps(_OUTCOME_JS.as_uri()))
    out_src = out_src.replace(
        '"./annotations.svelte.js"', json.dumps(annotations_mod.as_uri())
    )
    out_src = out_src.replace(
        '"./coverageView.svelte.js"', json.dumps(covview_mod.as_uri())
    )

    out = tmp_path / "results_neutralized.mjs"
    out.write_text(out_src)
    return out


def _run_ops(tmp_path, ops):
    annotations_mod = _neutralized_annotations_module(tmp_path)
    covview_mod = _neutralized_coverageview_module(tmp_path)
    mod = _neutralized_results_module(tmp_path, annotations_mod, covview_mod)
    script = _HARNESS.format(
        results_url=_import_url(mod),
        annotations_url=_import_url(annotations_mod),
        covview_url=_import_url(covview_mod),
    )
    return _run_node(script, ops)


def _rich_record(outcome="passed"):
    """A realistic settled record: full phases + a warning + a duration."""
    return {
        "phases": {
            "setup": {"outcome": "passed"},
            "call": {"outcome": outcome},
            "teardown": {"outcome": "passed"},
        },
        "warnings": [{"category": "UserWarning", "message": "boom"}],
        "duration": 0.25,
    }


_FRESH = {"phases": {}, "warnings": [], "duration": None, "running": True}


# --- shim guards ------------------------------------------------------------


def test_results_uses_exactly_three_state_runes():
    """Guard: the identity-swap is only faithful while $state is the only rune.

    If a future edit adds more runes, the neutralizer would silently under-load
    the module — fail loudly here instead (same guard as test_diff_js.py).
    """
    src = _RESULTS_JS.read_text()
    assert src.count("$state(") == 3
    for other in ("$derived", "$effect", "$props", "$bindable", "$inspect"):
        assert other not in src, f"new rune {other} — revisit the node shim"


def test_results_js_files_exist():
    assert _RESULTS_JS.is_file()
    assert _OUTCOME_JS.is_file()


# --- markRunning ------------------------------------------------------------


@requires_node
def test_mark_running_creates_fresh_records_overwriting_prior_phases(tmp_path):
    # Characterization: markRunning replaces the whole record. Prior phases,
    # warnings, duration and flags are lost, not merged.
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "t1", "record": _rich_record("failed")},
            {
                "op": "seed",
                "id": "t2",
                "record": {
                    "phases": {},
                    "warnings": [],
                    "duration": None,
                    "serverDown": True,
                },
            },
            {"op": "markRunning", "ids": ["t1", "t2", "t3"]},
        ],
    )
    # Every marked id, pre-existing or brand new, gets the same fresh shape.
    assert res["results"]["t1"] == _FRESH
    assert res["results"]["t2"] == _FRESH  # serverDown flag gone too
    assert res["results"]["t3"] == _FRESH


# --- markRunRejected (4xx from POST /api/run) -----------------------------


@requires_node
def test_mark_run_rejected_rolls_back_chips_and_shows_message_not_outage(tmp_path):
    # The server answered (with a 4xx), so a rejected run is not an outage.
    # Chips roll back to empty records (no serverDown/missing flags), settled
    # records stay, the backend's message lands on the status line at level
    # error, and stale infra flags clear (an answer is proof of life, R6).
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "settled", "record": _rich_record("failed")},
            {
                "op": "seed",
                "id": "down",
                "record": {
                    "phases": {},
                    "warnings": [],
                    "duration": None,
                    "serverDown": True,
                },
            },
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seedRun",
                "fields": {"serverDown": True, "reconnecting": True},
            },
            {
                "op": "markRunRejected",
                "message": "plugin 'nope' is not available (not installed or "
                "not curated)",
            },
        ],
    )
    # Chip: `running` cleared and not replaced by serverDown/missing.
    assert res["results"]["chip"] == {"phases": {}, "warnings": [], "duration": None}
    assert res["results"]["settled"] == _rich_record("failed")
    assert "serverDown" not in res["results"]["down"]  # R6 proof of life
    run = res["run"]
    assert run["status"] == (
        "plugin 'nope' is not available (not installed or not curated)"
    )
    assert run["level"] == "error"
    assert run["serverDown"] is False
    assert run["reconnecting"] is False
    assert run["active"] is False  # never went true; a 4xx sends no `started`


@requires_node
def test_mark_run_rejected_without_message_uses_fallback(tmp_path):
    res = _run_ops(tmp_path, [{"op": "markRunRejected", "message": None}])
    assert res["run"]["status"] == "run rejected"
    assert res["run"]["level"] == "error"


# --- run.pending (the markRunning-to-`started` gap signal) ----------------
#
# `run.active` only flips on the SSE `started` event, so without `pending` a run
# that is being POSTed has no reactive in-flight signal at all. App's
# collect-debounce guard reads `pending || active`; these pins prove that
# pending covers exactly the gap: set synchronously by markRunning, handed over
# to `active` on `started`, and cleared on every path where the run dies or
# provably never went live.


@requires_node
def test_run_pending_set_by_mark_running_handed_over_on_started(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "snapRun"},
            {"op": "markRunning", "ids": ["t1"]},
            {"op": "snapRun"},  # gap: POSTed, no `started` yet
            {"op": "onStarted", "data": {"run_id": "r1", "nodeids": ["t1"]}},
            {"op": "snapRun"},  # live: `active` covers it now
            {"op": "onFinished", "data": {"run_id": "r1", "exit_code": 0}},
            {"op": "snapRun"},  # over: neither
        ],
    )
    snaps = res["returns"]
    assert [(s["pending"], s["active"]) for s in snaps] == [
        (False, False),
        (True, False),
        (False, True),
        (False, False),
    ]


@requires_node
def test_run_pending_cleared_when_the_run_never_goes_live(tmp_path):
    # A 4xx reject and a server-down both mean nothing pending will ever start;
    # a stuck `pending` would defer the debounced collect forever.
    for terminal in (
        {"op": "markRunRejected", "message": "bad config"},
        {"op": "markServerDown"},
    ):
        res = _run_ops(tmp_path, [{"op": "markRunning", "ids": ["t1"]}, terminal])
        assert res["run"]["pending"] is False, terminal


@requires_node
def test_run_pending_cleared_by_terminals_even_with_started_lost(tmp_path):
    # An SSE gap can swallow `started`: the terminal events (and the R4
    # unstick) must still clear `pending` so collect never wedges.
    for terminal in (
        {"op": "onFinished", "data": {"run_id": "r1", "exit_code": 0}},
        {"op": "onCancelled", "data": {"run_id": "r1", "reason": "user"}},
        {"op": "onError", "data": {"run_id": "r1", "message": "boom"}},
        {"op": "unstickOrphanedRun"},
    ):
        res = _run_ops(tmp_path, [{"op": "markRunning", "ids": ["t1"]}, terminal])
        assert res["run"]["pending"] is False, terminal


@requires_node
def test_run_pending_survives_a_non_fatal_error(tmp_path):
    # An fd-3 overrun (`fatal: false`) means the run is still going, so the
    # in-flight signal must hold and a deferred collect keeps deferring.
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["t1"]},
            {"op": "onError", "data": {"run_id": "r1", "fatal": False, "message": "x"}},
        ],
    )
    assert res["run"]["pending"] is True


@requires_node
def test_run_in_flight_covers_pending_and_active(tmp_path):
    # The ▶ Run / ▶ Re-run-failed disabled gate. True from the synchronous
    # markRunning (before the POST returns, which is the double-POST gap)
    # through the live run, and false again only when the run ends or provably
    # never went live.
    res = _run_ops(
        tmp_path,
        [
            {"op": "runInFlight"},  # idle
            {"op": "markRunning", "ids": ["t1"]},
            {"op": "runInFlight"},  # pending: POSTed, no `started` yet
            {"op": "onStarted", "data": {"run_id": "r1", "nodeids": ["t1"]}},
            {"op": "runInFlight"},  # active
            {"op": "onFinished", "data": {"run_id": "r1", "exit_code": 0}},
            {"op": "runInFlight"},  # over
            {"op": "markRunning", "ids": ["t1"]},
            {"op": "markRunRejected", "message": "bad config"},
            {"op": "runInFlight"},  # 4xx reject: never went live
        ],
    )
    assert res["returns"] == [False, True, True, False, False]


# --- markServerDown (R3 hard path) -------------------------------------------


@requires_node
def test_mark_server_down_swaps_running_for_serverdown_and_ends_run(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "settled", "record": _rich_record()},
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seedRun",
                "fields": {"active": True, "level": "info", "status": "before"},
            },
            {"op": "markServerDown"},
        ],
    )
    # Running chip: `running` deleted, `serverDown` set (R3 hard path).
    chip = res["results"]["chip"]
    assert "running" not in chip
    assert chip["serverDown"] is True
    assert chip == {"phases": {}, "warnings": [], "duration": None, "serverDown": True}
    # Non-running records are untouched; no serverDown flag is added.
    assert res["results"]["settled"] == _rich_record()
    # Run store: hard-down state.
    assert res["run"]["active"] is False
    assert res["run"]["serverDown"] is True
    assert res["run"]["reconnecting"] is False
    assert res["run"]["level"] == "error"
    assert res["run"]["status"] == (
        "server unreachable: is it still running? (restart it and re-run)"
    )


# --- markReconnecting (R3 soft path) -----------------------------------------


@requires_node
def test_mark_reconnecting_leaves_chips_and_run_active_untouched(tmp_path):
    # R3: the soft path must not touch chips or run.active. Tearing down a live
    # run on a blip is unrecoverable (there is no SSE replay).
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {"op": "seedRun", "fields": {"active": True, "id": "run-7"}},
            {"op": "markReconnecting"},
        ],
    )
    assert res["results"]["chip"]["running"] is True  # chip untouched
    assert res["run"]["active"] is True  # run stays live locally
    assert res["run"]["id"] == "run-7"
    assert res["run"]["reconnecting"] is True
    assert res["run"]["serverDown"] is False
    assert res["run"]["level"] == "warn"
    assert res["run"]["status"] == "connection lost, reconnecting…"


# --- unstickOrphanedRun (R4) --------------------------------------------------


@requires_node
def test_unstick_orphaned_run_clears_running_flags_and_active_only(tmp_path):
    # R4: fail open. End the run locally and clear orphaned chips, nothing else.
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seed",
                "id": "down",
                "record": {
                    "phases": {},
                    "warnings": [],
                    "duration": None,
                    "serverDown": True,
                },
            },
            {
                "op": "seedRun",
                "fields": {
                    "active": True,
                    "reconnecting": True,
                    "level": "warn",
                    "status": "zzz",
                    "serverDown": False,
                },
            },
            {"op": "unstickOrphanedRun"},
        ],
    )
    # `running` cleared but not replaced by any flag (unlike markServerDown).
    assert res["results"]["chip"] == {"phases": {}, "warnings": [], "duration": None}
    # Per-test serverDown flags untouched.
    assert res["results"]["down"]["serverDown"] is True
    # Only run.active changes; reconnecting/level/status stay as they were.
    assert res["run"]["active"] is False
    assert res["run"]["reconnecting"] is True
    assert res["run"]["level"] == "warn"
    assert res["run"]["status"] == "zzz"


# --- clearServerDown (R6) ------------------------------------------------------


@requires_node
def test_clear_server_down_strips_flag_from_all_records(tmp_path):
    # R6: serverDown is an infra flag, so proof of life clears it globally: from
    # every record (not just the running ones) plus both run-level flags.
    down_rec = dict(_rich_record("failed"), serverDown=True)
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "a", "record": down_rec},
            {
                "op": "seed",
                "id": "b",
                "record": {
                    "phases": {},
                    "warnings": [],
                    "duration": None,
                    "serverDown": True,
                },
            },
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seedRun",
                "fields": {
                    "active": False,
                    "serverDown": True,
                    "reconnecting": True,
                    "level": "error",
                    "status": "down",
                },
            },
            {"op": "clearServerDown"},
        ],
    )
    assert "serverDown" not in res["results"]["a"]
    assert res["results"]["a"]["phases"] == down_rec["phases"]  # rest intact
    assert "serverDown" not in res["results"]["b"]
    assert res["results"]["chip"]["running"] is True  # running untouched
    assert res["run"]["serverDown"] is False
    assert res["run"]["reconnecting"] is False
    # clearServerDown itself leaves level/status/active alone.
    assert res["run"]["level"] == "error"
    assert res["run"]["status"] == "down"
    assert res["run"]["active"] is False


# --- reconcileResults (F2) ------------------------------------------------------


@requires_node
def test_reconcile_keeps_survivors_verbatim_same_object(tmp_path):
    # F2: surviving records are kept verbatim, the very same object, not a copy.
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "keep", "record": _rich_record("failed")},
            {"op": "snapRef", "store": "results", "id": "keep", "key": "k"},
            {"op": "reconcile", "ids": ["keep"]},
            {"op": "sameRef", "store": "results", "id": "keep", "key": "k"},
        ],
    )
    reconcile_ret, same = res["returns"]
    assert reconcile_ret == []  # nothing dropped
    assert same is True  # identity preserved, hence contents verbatim
    assert res["results"]["keep"] == _rich_record("failed")
    assert res["ghosts"] == {}


@requires_node
def test_reconcile_moves_dropped_records_to_ghosts_and_returns_ids(tmp_path):
    empty_rec = {"phases": {}, "warnings": [], "duration": None}
    res = _run_ops(
        tmp_path,
        [
            {"op": "seedGhost", "id": "stale", "record": _rich_record()},
            {"op": "seed", "id": "keep", "record": _rich_record()},
            {"op": "seed", "id": "gone", "record": _rich_record("failed")},
            {"op": "seed", "id": "gone_empty", "record": empty_rec},
            {"op": "snapRef", "store": "results", "id": "gone", "key": "g"},
            {"op": "reconcile", "ids": ["keep", "never_ran"]},
            {"op": "sameRef", "store": "ghosts", "id": "gone", "key": "g"},
        ],
    )
    reconcile_ret, ghost_same = res["returns"]
    # Characterization: every dropped record lands in ghosts and in the return
    # list, even one with no results yet (empty phases). Lock as-is.
    assert reconcile_ret == ["gone", "gone_empty"]
    assert ghost_same is True  # ghosted verbatim (same object, F2)
    assert res["ghosts"]["gone"] == _rich_record("failed")
    assert res["ghosts"]["gone_empty"] == empty_rec
    # Fresh ghost set each reload: the pre-existing ghost is dropped.
    assert "stale" not in res["ghosts"]
    # Survivor kept; a surviving id with no record stays absent (none created).
    assert set(res["results"]) == {"keep"}


@requires_node
def test_reconcile_clears_server_down_before_keeping_or_ghosting(tmp_path):
    # reconcileResults calls clearServerDown first (R6): the flag neither
    # sticks on survivors nor leaks into ghosts.
    down = dict(_rich_record(), serverDown=True)
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "keep", "record": dict(down)},
            {"op": "seed", "id": "gone", "record": dict(down)},
            {"op": "seedRun", "fields": {"serverDown": True, "reconnecting": True}},
            {"op": "reconcile", "ids": ["keep"]},
        ],
    )
    assert "serverDown" not in res["results"]["keep"]
    assert "serverDown" not in res["ghosts"]["gone"]
    assert res["run"]["serverDown"] is False
    assert res["run"]["reconnecting"] is False
    assert res["returns"] == [["gone"]]


# --- outcomeFor / resultFor -------------------------------------------------


@requires_node
def test_outcome_for_precedence_running_serverdown_missing_then_phases(tmp_path):
    failed_phases = {"setup": {"outcome": "passed"}, "call": {"outcome": "failed"}}
    all_flags = {
        "phases": dict(failed_phases),
        "warnings": [],
        "duration": None,
        "running": True,
        "serverDown": True,
        "missing": True,
    }
    no_running = {
        "phases": dict(failed_phases),
        "warnings": [],
        "duration": None,
        "serverDown": True,
        "missing": True,
    }
    only_missing = {
        "phases": dict(failed_phases),
        "warnings": [],
        "duration": None,
        "missing": True,
    }
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "r", "record": all_flags},
            {"op": "seed", "id": "sd", "record": no_running},
            {"op": "seed", "id": "m", "record": only_missing},
            {"op": "seed", "id": "f", "record": _rich_record("failed")},
            {"op": "seed", "id": "p", "record": _rich_record("passed")},
            {
                "op": "seed",
                "id": "inc",
                "record": {
                    "phases": {"setup": {"outcome": "passed"}},
                    "warnings": [],
                    "duration": None,
                },
            },
            {"op": "outcomeFor", "id": "r"},
            {"op": "outcomeFor", "id": "sd"},
            {"op": "outcomeFor", "id": "m"},
            {"op": "outcomeFor", "id": "f"},
            {"op": "outcomeFor", "id": "p"},
            {"op": "outcomeFor", "id": "inc"},
            {"op": "outcomeFor", "id": "unknown"},
        ],
    )
    # Precedence, highest first: running, server-down, missing, then
    # overallOutcome(phases). An absent id reads as null.
    assert res["returns"] == [
        "running",
        "server-down",
        "missing",
        "failed",
        "passed",
        "incomplete",
        None,
    ]


@requires_node
def test_outcome_for_falls_back_to_ghosts_and_results_win_over_ghosts(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "seedGhost", "id": "ghost_only", "record": _rich_record()},
            {"op": "seed", "id": "both", "record": _rich_record("failed")},
            {"op": "seedGhost", "id": "both", "record": _rich_record("passed")},
            {"op": "outcomeFor", "id": "ghost_only"},
            {"op": "outcomeFor", "id": "both"},
            {"op": "resultFor", "id": "ghost_only"},
            {"op": "resultFor", "id": "nope"},
        ],
    )
    ghost_outcome, both_outcome, ghost_result, missing_result = res["returns"]
    assert ghost_outcome == "passed"  # ghost fallback renders the strip badge
    assert both_outcome == "failed"  # live results shadow the ghost
    assert ghost_result == _rich_record()  # resultFor falls back too
    assert missing_result is None


# --- failedNodeids (the re-run-failed set) --------------------------------


def _phases_record(phases, **flags):
    return {"phases": phases, "warnings": [], "duration": None, **flags}


@requires_node
def test_failed_nodeids_includes_failed_and_error_only(tmp_path):
    # Only results folding to failed (call phase) or error (setup/teardown
    # phase) count, never passed/skipped/xfailed/xpassed/incomplete. They come
    # back in insertion order, since the list becomes argv nodeids.
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "f", "record": _rich_record("failed")},
            {
                "op": "seed",
                "id": "e",
                "record": _phases_record({"setup": {"outcome": "failed"}}),
            },
            {"op": "seed", "id": "p", "record": _rich_record("passed")},
            {
                "op": "seed",
                "id": "s",
                "record": _phases_record(
                    {
                        "setup": {"outcome": "passed"},
                        "call": {"outcome": "skipped"},
                    }
                ),
            },
            {
                "op": "seed",
                "id": "xf",
                "record": _phases_record(
                    {
                        "setup": {"outcome": "passed"},
                        "call": {"outcome": "skipped", "wasxfail": "reason"},
                    }
                ),
            },
            {
                "op": "seed",
                "id": "xp",
                "record": _phases_record(
                    {
                        "setup": {"outcome": "passed"},
                        "call": {"outcome": "passed", "wasxfail": "reason"},
                    }
                ),
            },
            {
                "op": "seed",
                "id": "inc",
                "record": _phases_record({"setup": {"outcome": "passed"}}),
            },
            {"op": "failedNodeids"},
        ],
    )
    assert res["returns"] == [["f", "e"]]


@requires_node
def test_failed_nodeids_excludes_flagged_records_and_ghosts(tmp_path):
    # In-flight/infra flags shadow the phases (same precedence as outcomeFor):
    # a failed record that is running/missing/server-down is not re-runnable
    # yet. Ghosts never count; a removed test's nodeid is stale argv.
    failed_phases = {"call": {"outcome": "failed"}}
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "ok", "record": _rich_record("failed")},
            {
                "op": "seed",
                "id": "run",
                "record": _phases_record(failed_phases, running=True),
            },
            {
                "op": "seed",
                "id": "miss",
                "record": _phases_record(failed_phases, missing=True),
            },
            {
                "op": "seed",
                "id": "down",
                "record": _phases_record(failed_phases, serverDown=True),
            },
            {"op": "seedGhost", "id": "ghost", "record": _rich_record("failed")},
            {"op": "failedNodeids"},
        ],
    )
    assert res["returns"] == [["ok"]]


@requires_node
def test_failed_nodeids_empty_when_no_results(tmp_path):
    res = _run_ops(tmp_path, [{"op": "failedNodeids"}])
    assert res["returns"] == [[]]


# --- clearResults -------------------------------------------------------------


@requires_node
def test_clear_results_resets_stores_but_not_connection_state(tmp_path):
    # Characterization: clearResults wipes records/ghosts/console/status/k/m/
    # reports but leaves id/active/serverDown/reconnecting alone.
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "a", "record": _rich_record()},
            {"op": "seedGhost", "id": "g", "record": _rich_record()},
            {
                "op": "seedRun",
                "fields": {
                    "id": "run-3",
                    "active": True,
                    "console": "x",
                    "status": "s",
                    "level": "warn",
                    "k": "kk",
                    "m": "mm",
                    "reports": 7,
                    "serverDown": True,
                    "reconnecting": True,
                },
            },
            {"op": "clearResults"},
        ],
    )
    assert res["results"] == {}
    assert res["ghosts"] == {}
    run = res["run"]
    assert run["console"] == "" and run["status"] == ""
    assert run["level"] == "info"
    assert run["k"] is None and run["m"] is None
    assert run["reports"] == 0
    # Untouched:
    assert run["id"] == "run-3"
    assert run["active"] is True
    assert run["serverDown"] is True
    assert run["reconnecting"] is True


# --- onReport -----------------------------------------------------------------


@requires_node
def test_on_report_creates_record_maps_phases_and_accumulates_duration(tmp_path):
    # Characterization: onReport ensures the record exists, writes one phase
    # entry per event with null/[] defaults, counts every event in run.reports,
    # and sums only non-null durations into r.duration.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onReport",
                "data": {
                    "nodeid": "t.py::a",
                    "when": "setup",
                    "outcome": "passed",
                    "duration": 0.5,
                },
            },
            {
                "op": "onReport",
                "data": {
                    "nodeid": "t.py::a",
                    "when": "call",
                    "outcome": "failed",
                    "duration": 0.25,
                    "longrepr": "Traceback: boom",
                    "wasxfail": "flaky",
                    "sections": [["Captured stdout call", "hi"]],
                },
            },
            {
                "op": "onReport",
                "data": {
                    "nodeid": "t.py::a",
                    "when": "teardown",
                    "outcome": "passed",
                    "duration": None,
                },
            },
        ],
    )
    rec = res["results"]["t.py::a"]
    # Omitted optionals default to null/[]; duration is passed through as-is.
    assert rec["phases"]["setup"] == {
        "outcome": "passed",
        "wasxfail": None,
        "longrepr": None,
        "sections": [],
        "duration": 0.5,
    }
    assert rec["phases"]["call"] == {
        "outcome": "failed",
        "wasxfail": "flaky",
        "longrepr": "Traceback: boom",
        "sections": [["Captured stdout call", "hi"]],
        "duration": 0.25,
    }
    assert rec["phases"]["teardown"] == {
        "outcome": "passed",
        "wasxfail": None,
        "longrepr": None,
        "sections": [],
        "duration": None,
    }
    assert rec["duration"] == 0.75  # 0.5 + 0.25; null teardown adds nothing
    assert rec["warnings"] == []
    assert res["run"]["reports"] == 3  # every report event counted


@requires_node
def test_on_report_clears_running_missing_and_serverdown_flags(tmp_path):
    # A landing report is truth for that nodeid: all three status flags go.
    flagged = {
        "phases": {},
        "warnings": [],
        "duration": None,
        "running": True,
        "missing": True,
        "serverDown": True,
    }
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "flagged", "record": flagged},
            {
                "op": "onReport",
                "data": {
                    "nodeid": "flagged",
                    "when": "call",
                    "outcome": "passed",
                    "duration": None,
                },
            },
        ],
    )
    rec = res["results"]["flagged"]
    assert "running" not in rec
    assert "missing" not in rec
    assert "serverDown" not in rec
    assert rec["duration"] is None  # null duration leaves the sum untouched
    assert rec["phases"]["call"]["outcome"] == "passed"


# --- onStarted ------------------------------------------------------------------


@requires_node
def test_on_started_resets_run_state_and_builds_running_phrase(tmp_path):
    # onStarted is proof of life (R6: seeded serverDown flags clear), resets
    # console/level/reports, echoes k/m (?? null), and builds the status phrase.
    down_rec = {"phases": {}, "warnings": [], "duration": None, "serverDown": True}
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "down", "record": down_rec},
            {
                "op": "seedRun",
                "fields": {
                    "serverDown": True,
                    "reconnecting": True,
                    "console": "old",
                    "level": "error",
                    "status": "old",
                    "reports": 7,
                },
            },
            {
                "op": "onStarted",
                "data": {"run_id": "run-1", "nodeids": ["a", "b"], "k": "x"},
            },
            {"op": "snapRun"},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "snapRun"},
            {
                "op": "onStarted",
                "data": {"run_id": "run-3", "nodeids": [], "k": "x", "m": "slow"},
            },
            {"op": "snapRun"},
        ],
    )
    with_ids, no_ids, with_k_m = res["returns"]
    assert with_ids["id"] == "run-1"
    assert with_ids["active"] is True
    assert with_ids["console"] == ""
    assert with_ids["level"] == "info"
    assert with_ids["k"] == "x" and with_ids["m"] is None  # ?? null echo
    assert with_ids["reports"] == 0
    assert with_ids["serverDown"] is False  # R6
    assert with_ids["reconnecting"] is False
    assert with_ids["status"] == "running: 2 selected, -k 'x'…"
    # No nodeids at all means "all tests", and so does an empty list.
    assert no_ids["status"] == "running: all tests…"
    assert no_ids["k"] is None and no_ids["m"] is None
    assert with_k_m["status"] == "running: all tests, -k 'x', -m 'slow'…"
    # R6: the per-record flag was stripped by clearServerDown.
    assert "serverDown" not in res["results"]["down"]


# --- onFinished -------------------------------------------------------------------


@requires_node
def test_on_finished_exit_5_no_reports_is_benign_no_match(tmp_path):
    # Exit 5 with zero reports: chips are cleared but not marked missing, the
    # status names the empty selection, and seeded serverDown flags clear (R6).
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seed",
                "id": "down",
                "record": {
                    "phases": {},
                    "warnings": [],
                    "duration": None,
                    "serverDown": True,
                },
            },
            {
                "op": "seedRun",
                "fields": {"active": True, "serverDown": True, "reconnecting": True},
            },
            {"op": "onFinished", "data": {"exit_code": 5}},
        ],
    )
    assert res["results"]["chip"] == {
        "phases": {},
        "warnings": [],
        "duration": None,
    }  # no `missing`
    assert "serverDown" not in res["results"]["down"]
    assert res["run"]["active"] is False
    assert res["run"]["serverDown"] is False and res["run"]["reconnecting"] is False
    assert res["run"]["level"] == "info"
    assert res["run"]["status"] == "no tests matched the selection / filter"


@requires_node
def test_on_finished_normal_exit_marks_unreported_chips_missing(tmp_path):
    # Any non-benign finish: chips that never got a report become `missing`;
    # settled records are untouched; status carries the literal exit code.
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "settled", "record": _rich_record("failed")},
            {"op": "markRunning", "ids": ["chip"]},
            {"op": "seedRun", "fields": {"active": True}},
            {"op": "onFinished", "data": {"exit_code": 1}},
        ],
    )
    assert res["results"]["chip"] == {
        "phases": {},
        "warnings": [],
        "duration": None,
        "missing": True,
    }
    assert res["results"]["settled"] == _rich_record("failed")
    assert res["run"]["active"] is False
    assert res["run"]["level"] == "info"
    assert res["run"]["status"] == "run finished (exit 1)"


@requires_node
def test_on_finished_exit_5_with_reports_is_not_benign(tmp_path):
    # The no-match branch needs both exit 5 and run.reports == 0: once any
    # report has landed this run, exit 5 still marks unreported chips missing.
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip", "other"]},
            {"op": "seedRun", "fields": {"active": True}},
            {
                "op": "onReport",
                "data": {
                    "nodeid": "other",
                    "when": "call",
                    "outcome": "passed",
                    "duration": None,
                },
            },
            {"op": "onFinished", "data": {"exit_code": 5}},
        ],
    )
    assert res["results"]["chip"]["missing"] is True
    assert "missing" not in res["results"]["other"]  # it reported
    assert res["run"]["status"] == "run finished (exit 5)"


# --- onCancelled ------------------------------------------------------------------


@requires_node
def test_on_cancelled_clears_running_without_missing_and_warns(tmp_path):
    # Cancelled tests stay incomplete: never `missing`, never a silent pass.
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seedRun",
                "fields": {"active": True, "serverDown": True, "reconnecting": True},
            },
            {"op": "onCancelled", "data": {"reason": "user"}},
        ],
    )
    assert res["results"]["chip"] == {"phases": {}, "warnings": [], "duration": None}
    assert res["run"]["active"] is False
    assert res["run"]["serverDown"] is False  # R6
    assert res["run"]["level"] == "warn"
    assert res["run"]["status"] == "run cancelled (user)"


# --- onError ----------------------------------------------------------------------


@requires_node
def test_on_error_status_shapes_and_chip_clearing(tmp_path):
    # Exit 4 (usage error) points at the console, with a fallback message;
    # anything else reads "run error: <message||unknown>". Chips clear, and
    # nothing is marked missing.
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {"op": "seedRun", "fields": {"active": True}},
            {"op": "onError", "data": {"exit_code": 4, "message": "bad -k"}},
            {"op": "snapRun"},
            {"op": "onError", "data": {"exit_code": 4}},
            {"op": "snapRun"},
            {"op": "onError", "data": {"exit_code": 1, "message": "boom"}},
            {"op": "snapRun"},
            {"op": "onError", "data": {}},
            {"op": "snapRun"},
        ],
    )
    usage, usage_no_msg, other, bare = res["returns"]
    assert usage["status"] == ("bad -k. See the run console for pytest's message")
    assert usage_no_msg["status"] == (
        "invalid run (pytest usage error). See the run console for " "pytest's message"
    )
    assert other["status"] == "run error: boom"
    assert bare["status"] == "run error: unknown"
    for snap in (usage, usage_no_msg, other, bare):
        assert snap["active"] is False
        assert snap["level"] == "error"
    # Running cleared but not marked missing (unlike onFinished).
    assert res["results"]["chip"] == {"phases": {}, "warnings": [], "duration": None}


@requires_node
def test_on_error_non_fatal_overrun_keeps_run_active_and_chips(tmp_path):
    # An fd-3 overrun `error` event carries `fatal: false`: the run is still
    # live and `finished` will arrive. Only level/status change; chips and
    # run.active must survive (regression: an overrun was treated as terminal).
    res = _run_ops(
        tmp_path,
        [
            {"op": "markRunning", "ids": ["chip"]},
            {
                "op": "seedRun",
                "fields": {"active": True, "serverDown": True, "reconnecting": True},
            },
            {
                "op": "onError",
                "data": {
                    "exit_code": None,
                    "message": "a result line exceeded the 1 MiB buffer "
                    "and was dropped (truncated traceback)",
                    "fatal": False,
                },
            },
        ],
    )
    assert res["results"]["chip"]["running"] is True  # chip untouched
    assert res["run"]["active"] is True  # run stays live
    assert res["run"]["serverDown"] is False  # R6: still proof of life
    assert res["run"]["reconnecting"] is False
    assert res["run"]["level"] == "error"
    assert "1 MiB buffer" in res["run"]["status"]


# --- onReport section echo fix -------------------------------

# pytest accumulates captured-output sections on the item across phases
# (item._report_sections), so call/teardown reports re-carry earlier phases'
# sections verbatim (reproduced live: teardown.sections = setup + call +
# teardown). The applier must keep only each phase's own sections.

_S_SETUP = {"title": "Captured stdout setup", "content": "SETUP-OUT\n"}
_S_CALL = {"title": "Captured stdout call", "content": "CALL-OUT\n"}
_S_TEAR = {"title": "Captured stdout teardown", "content": "TEARDOWN-OUT\n"}


def _phase_report(when, sections, outcome="passed"):
    return {
        "nodeid": "t.py::a",
        "when": when,
        "outcome": outcome,
        "duration": None,
        "sections": sections,
    }


@requires_node
def test_on_report_drops_sections_echoed_from_earlier_phases(tmp_path):
    # The exact accumulation pytest produces: each later report re-carries all
    # earlier sections. Only the phase's own section must be stored.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onReport", "data": _phase_report("setup", [_S_SETUP])},
            {
                "op": "onReport",
                "data": _phase_report("call", [_S_SETUP, _S_CALL], "failed"),
            },
            {
                "op": "onReport",
                "data": _phase_report("teardown", [_S_SETUP, _S_CALL, _S_TEAR]),
            },
        ],
    )
    ph = res["results"]["t.py::a"]["phases"]
    assert ph["setup"]["sections"] == [_S_SETUP]
    assert ph["call"]["sections"] == [_S_CALL]
    assert ph["teardown"]["sections"] == [_S_TEAR]


@requires_node
def test_on_report_keeps_identical_output_with_distinct_titles(tmp_path):
    # Genuinely repeated output (the same text printed in setup and teardown)
    # differs in the title's phase suffix, so both are kept; nothing is
    # over-deduped.
    a = {"title": "Captured stdout setup", "content": "X\n"}
    b = {"title": "Captured stdout teardown", "content": "X\n"}
    res = _run_ops(
        tmp_path,
        [
            {"op": "onReport", "data": _phase_report("setup", [a])},
            {"op": "onReport", "data": _phase_report("teardown", [a, b])},
        ],
    )
    ph = res["results"]["t.py::a"]["phases"]
    assert ph["setup"]["sections"] == [a]
    assert ph["teardown"]["sections"] == [b]  # echoed `a` dropped, own `b` kept


@requires_node
def test_on_report_re_reporting_same_phase_keeps_its_own_sections(tmp_path):
    # The dedupe scans the other phases only: replacing a phase's report must
    # not treat its previous self as "already stored" and drop everything.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onReport", "data": _phase_report("call", [_S_CALL])},
            {"op": "onReport", "data": _phase_report("call", [_S_CALL])},
        ],
    )
    assert res["results"]["t.py::a"]["phases"]["call"]["sections"] == [_S_CALL]


# --- onPluginData / plugin-channel lifecycle ------------------------------

# The slimmer emits source-file relative paths, not test-tree nodeids; the two
# are disjoint (coverage measures pkg/mod.py, the deck tree keys test_mod.py).
# The panel renders these source paths directly via coverageFiles(); there is no
# tree-node lookup (the earlier column tried node.name and always got null).
_COV_EVENT = {
    "run_id": "run-1",
    "plugin": "pytest_cov",
    "data": {
        "total": 81.25,
        "files": {"pkg/mod.py": 91.7, "pkg/util.py": 40.0},
    },
}


@requires_node
def test_on_plugin_data_populates_run_total_and_source_file_list(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _COV_EVENT},
            {"op": "coverageTotal"},
            {"op": "coverageFiles"},
        ],
    )
    # The run-level payload keyed by plugin id drives the total; coverageFiles()
    # returns the source-keyed rows sorted by pct ascending (worst first), ties
    # broken on path, which is the actionable order for the coverage panel.
    assert res["run"]["pluginData"]["pytest_cov"]["total"] == 81.25
    total, files = res["returns"]
    assert total == 81.25
    assert files == [
        {"path": "pkg/util.py", "pct": 40.0},
        {"path": "pkg/mod.py", "pct": 91.7},
    ]
    # The per-file map is also mirrored onto the plugin-id channel (P16), keyed
    # by the source path. The coverage render does not consume it; it is kept
    # for forward use.
    assert res["annotations"]["pkg/mod.py"] == {"pytest_cov": 91.7}


@requires_node
def test_coverage_files_null_when_no_coverage_data(tmp_path):
    res = _run_ops(tmp_path, [{"op": "coverageFiles"}])
    assert res["returns"] == [None]


@requires_node
def test_new_run_clears_coverage_and_plugin_channels_not_diff(tmp_path):
    # Lifecycle: a new run invalidates the previous run's plugin data (a
    # missing plugin_data event means no data, e.g. --no-cov, and stale numbers
    # must not survive). Both entry points clear: markRunning (local run) and
    # onStarted (authoritative, covers runs another tab started). The "diff"
    # channel is untouched; only channels onPluginData wrote are dropped.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "seedAnnotation",
                "id": "t.py::a",
                "channel": "diff",
                "value": "added",
            },
            {"op": "onPluginData", "data": _COV_EVENT},
            {"op": "markRunning", "ids": ["t.py::a"]},
            {"op": "coverageTotal"},
            {"op": "coverageFiles"},
            {"op": "onPluginData", "data": _COV_EVENT},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "coverageTotal"},
            {"op": "coverageFiles"},
        ],
    )
    assert res["returns"] == [None, None, None, None]
    assert res["run"]["pluginData"] == {}
    # diff annotation survived both clears; plugin channel entries pruned.
    assert res["annotations"] == {"t.py::a": {"diff": "added"}}


@requires_node
def test_on_plugin_data_tolerates_missing_plugin_or_files(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": {"run_id": "r", "data": {"total": 1}}},
            {"op": "onPluginData", "data": {"run_id": "r", "plugin": "p"}},
            {"op": "coverageTotal"},
        ],
    )
    # No plugin id means the event is ignored; no data means an empty payload
    # and no annotations, and a non-numeric or absent total reads as null.
    assert res["run"]["pluginData"] == {"p": {}}
    assert res["annotations"] == {}
    assert res["returns"] == [None]


# --- render discriminator routing -----------------------------------------


@requires_node
def test_render_coverage_routes_to_coverage_path_unchanged(tmp_path):
    # render:"coverage" populates the coverage panel and gutter path exactly as
    # the coverage shape did: pluginData plus file annotations, not pluginRender.
    ev = {
        "run_id": "r",
        "plugin": "pytest_cov",
        "render": "coverage",
        "data": {"total": 77.0, "files": {"pkg/m.py": 77.0}},
    }
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": ev},
            {"op": "coverageTotal"},
            {"op": "coverageFiles"},
            {"op": "renderSections"},
        ],
    )
    total, files, sections = res["returns"]
    assert total == 77.0
    assert files == [{"path": "pkg/m.py", "pct": 77.0}]
    assert res["annotations"]["pkg/m.py"] == {"pytest_cov": 77.0}
    assert res["run"]["pluginRender"] == {}  # not in the generic channel
    assert sections == []


@requires_node
def test_render_text_routes_to_plugin_render_channel(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {
                    "run_id": "r",
                    "plugin": "pytest_report",
                    "render": "text",
                    "data": "hello\nworld",
                    "truncated": True,
                },
            },
            {"op": "renderSections"},
            {"op": "coverageTotal"},
        ],
    )
    sections, total = res["returns"]
    assert res["run"]["pluginData"] == {}  # not coverage
    assert sections == [
        {
            "plugin": "pytest_report",
            "render": "text",
            "data": "hello\nworld",
            "truncated": True,
        }
    ]
    assert total is None


@requires_node
def test_render_json_routes_and_sections_sorted_by_plugin(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {"plugin": "zeta", "render": "json", "data": {"a": 1}},
            },
            {
                "op": "onPluginData",
                "data": {"plugin": "alpha", "render": "text", "data": "x"},
            },
            {"op": "renderSections"},
        ],
    )
    sections = res["returns"][0]
    # Sorted by plugin id for stable ordering.
    assert [s["plugin"] for s in sections] == ["alpha", "zeta"]
    assert sections[1]["render"] == "json"
    assert sections[1]["data"] == {"a": 1}
    assert sections[1]["truncated"] is False  # default when omitted


@requires_node
def test_json_truncated_sentinel_detected(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "jsonTruncatedBytes", "data": {"_truncated": True, "bytes": 900000}},
            {"op": "jsonTruncatedBytes", "data": {"_truncated": True}},
            {"op": "jsonTruncatedBytes", "data": {"a": 1}},
            {"op": "jsonTruncatedBytes", "data": "text"},
            {"op": "jsonTruncatedBytes", "data": None},
        ],
    )
    # A sentinel yields its byte count (0 when absent); a real payload yields null.
    assert res["returns"] == [900000, 0, None, None, None]


@requires_node
def test_new_run_clears_plugin_render_sections(tmp_path):
    # Lifecycle: generic render output is per-run, so a new run clears it.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {"plugin": "p", "render": "text", "data": "x"},
            },
            {"op": "renderSections"},
            {"op": "markRunning", "ids": ["t.py::a"]},
            {"op": "renderSections"},
            {
                "op": "onPluginData",
                "data": {"plugin": "p", "render": "text", "data": "y"},
            },
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "renderSections"},
        ],
    )
    after_data, after_markrunning, after_started = res["returns"]
    assert len(after_data) == 1
    assert after_markrunning == []  # markRunning cleared it
    assert after_started == []  # started cleared the re-added one too


# --- artifacts (per-test attachments) -------------------------------------

_ARTIFACT_EVENT = {
    "run_id": "run-1",
    "plugin": "pytest_mpl",
    "render": "artifacts",
    "data": {
        "t.py::test_plot[a-b]": [
            {
                "name": "baseline",
                "rel_path": "t.py/test_plot[a-b]/baseline.png",
                "kind": "image",
            },
            {
                "name": "result",
                "rel_path": "t.py/test_plot[a-b]/result.png",
                "kind": "image",
            },
            {
                "name": "diff",
                "rel_path": "t.py/test_plot[a-b]/diff.png",
                "kind": "image",
            },
        ],
        "t.py::test_data": [
            {"name": "raw", "rel_path": "t.py/test_data/raw.csv", "kind": "file"},
        ],
    },
}


@requires_node
def test_on_plugin_data_artifacts_stores_map_keyed_by_nodeid(tmp_path):
    # render:"artifacts" lands on run.artifacts (nodeid -> file list) with the
    # producing run_id stamped alongside, not on coverage or the generic render
    # channel. artifactsFor returns the list for a nodeid, [] otherwise.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _ARTIFACT_EVENT},
            {"op": "artifactsFor", "id": "t.py::test_plot[a-b]"},
            {"op": "artifactsFor", "id": "t.py::test_data"},
            {"op": "artifactsFor", "id": "t.py::no_such"},
            {"op": "coverageTotal"},
            {"op": "renderSections"},
        ],
    )
    plot, data, absent, total, sections = res["returns"]
    assert [f["name"] for f in plot] == ["baseline", "result", "diff"]
    assert data == [
        {"name": "raw", "rel_path": "t.py/test_data/raw.csv", "kind": "file"}
    ]
    assert absent == []  # unknown nodeid: empty list, never undefined
    assert res["run"]["artifacts"] == _ARTIFACT_EVENT["data"]
    assert res["run"]["artifactsRunId"] == "run-1"
    # Not routed to coverage / generic render.
    assert total is None
    assert sections == []
    assert res["run"]["pluginData"] == {}
    assert res["run"]["pluginRender"] == {}


@requires_node
def test_new_run_clears_artifacts(tmp_path):
    # Lifecycle: artifacts belong to the run that produced them. A new run
    # replaces the tmpdir (the <img> src would 404), so both markRunning and
    # onStarted (via clearPluginData) clear the map and its run id.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _ARTIFACT_EVENT},
            {"op": "markRunning", "ids": ["t.py::a"]},
            {"op": "artifactsFor", "id": "t.py::test_plot[a-b]"},
            {"op": "onPluginData", "data": _ARTIFACT_EVENT},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "artifactsFor", "id": "t.py::test_plot[a-b]"},
        ],
    )
    after_markrunning, after_started = res["returns"]
    assert after_markrunning == []
    assert after_started == []
    assert res["run"]["artifacts"] == {}
    assert res["run"]["artifactsRunId"] is None


@requires_node
def test_on_plugin_data_artifacts_tolerates_missing_data(tmp_path):
    # A render:"artifacts" event with no `data` gives an empty map (not a
    # crash); the run_id is still stamped so a mismatch guard can compare it.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {
                    "run_id": "r",
                    "plugin": "pytest_mpl",
                    "render": "artifacts",
                },
            },
            {"op": "artifactsFor", "id": "anything"},
        ],
    )
    assert res["run"]["artifacts"] == {}
    assert res["run"]["artifactsRunId"] == "r"
    assert res["returns"] == [[]]


@requires_node
def test_artifact_url_encodes_each_segment_preserving_slashes(tmp_path):
    # The load-bearing encoding rule: rel_path segments (mpl's parametrized dir
    # names carry `[`, `]` and `.`) are encodeURIComponent'd one segment at a
    # time, then rejoined with `/` under /api/artifacts/<run_id>/. Slashes
    # survive as separators, brackets and spaces are escaped, and the run_id is
    # encoded whole.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "artifactUrl",
                "runId": "run-1",
                "relPath": "t.py/test_plot[a-b]/result image.png",
            },
            {
                "op": "artifactUrl",
                "runId": "run/x",
                "relPath": "a/b.png",
            },
            {"op": "artifactUrl", "runId": "r", "relPath": "single.png"},
        ],
    )
    bracketed, run_slash, single = res["returns"]
    # Each segment encoded; the two path slashes kept; brackets + space escaped.
    assert bracketed == (
        "/api/artifacts/run-1/t.py/test_plot%5Ba-b%5D/result%20image.png"
    )
    # The run_id is encoded as a single component (its slash is escaped), while
    # the rel_path's slash is preserved.
    assert run_slash == "/api/artifacts/run%2Fx/a/b.png"
    assert single == "/api/artifacts/r/single.png"


# --- metadata render (Environment section) ---------------------------------

_METADATA_EVENT = {
    "run_id": "run-1",
    "plugin": "metadata",
    "render": "metadata",
    "data": {
        "Python": "3.13.1",
        "Platform": "Linux-6.17.0",
        "Packages": {"pytest": "9.1.1", "pluggy": "1.6.0"},
        "Plugins": {"metadata": "3.1.1"},
    },
}


@requires_node
def test_render_metadata_routes_to_plugin_meta(tmp_path):
    # render:"metadata" lands on run.pluginMeta (the whole dict, read by the run
    # panel's Environment section). It takes neither the coverage path (no
    # pluginData entry, no file annotations) nor the generic render channel.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _METADATA_EVENT},
            {"op": "metadataInfo"},
            {"op": "coverageTotal"},
            {"op": "renderSections"},
        ],
    )
    info, total, sections = res["returns"]
    assert info == _METADATA_EVENT["data"]
    assert res["run"]["pluginMeta"] == _METADATA_EVENT["data"]
    assert total is None
    assert sections == []
    assert res["run"]["pluginData"] == {}
    assert res["run"]["pluginRender"] == {}
    assert res["annotations"] == {}


@requires_node
def test_new_run_clears_plugin_meta_and_metadata_empty(tmp_path):
    # Lifecycle: the environment dict describes the run that produced it, so
    # both markRunning and onStarted (via clearPluginData) reset it, and a stale
    # metadata plugin_empty hint clears on the same triggers (same pattern).
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _METADATA_EVENT},
            {"op": "markRunning", "ids": ["t.py::a"]},
            {"op": "metadataInfo"},
            {"op": "onPluginEmpty", "data": {"run_id": "r", "plugin": "metadata"}},
            {"op": "metadataEmpty"},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "metadataEmpty"},
            {"op": "metadataInfo"},
        ],
    )
    after_markrunning, empty_set, empty_cleared, after_started = res["returns"]
    assert after_markrunning is None
    assert empty_set is True
    assert empty_cleared is False
    assert after_started is None
    assert res["run"]["pluginMeta"] is None


# --- benchmark render (tree column + run panel line) -----------------------

_BENCH_STATS = {
    "min": 8.8e-8,
    "max": 1.2e-7,
    "mean": 9.4e-8,
    "stddev": 4.0e-9,
    "median": 9.3e-8,
    "iqr": 3.7e-9,
    "ops": 10649405.1,
    "rounds": 10488,
    "iterations": 100,
}

_BENCH_EVENT = {
    "run_id": "run-1",
    "plugin": "benchmark",
    "render": "benchmark",
    "data": {
        "summary": {
            "count": 2,
            "fastest": {"nodeid": "t.py::test_a[3]", "mean": 9.4e-8},
            "slowest": {"nodeid": "t.py::test_b", "mean": 2.7e-7},
        },
        "tests": {
            "t.py::test_a[3]": _BENCH_STATS,
            "t.py::test_b": {**_BENCH_STATS, "mean": 2.7e-7},
        },
    },
}


@requires_node
def test_render_benchmark_mirrors_tests_onto_annotation_channel(tmp_path):
    # render:"benchmark" is the deck's first test-keyed plugin data. The whole
    # payload lands run-level (benchmarkData/benchmarkSummary) and each per-test
    # stats record mirrors onto the "benchmark" annotation channel (P16: the
    # channel key is the plugin id, the reserved DiffBadge slot). Neither the
    # coverage path nor the generic render channel is involved.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _BENCH_EVENT},
            {"op": "benchmarkData"},
            {"op": "benchmarkSummary"},
            {"op": "coverageTotal"},
            {"op": "renderSections"},
        ],
    )
    data, summary, total, sections = res["returns"]
    assert data == _BENCH_EVENT["data"]
    assert summary == _BENCH_EVENT["data"]["summary"]
    # Annotation mirroring, per nodeid, on the plugin-id channel.
    assert res["annotations"]["t.py::test_a[3]"]["benchmark"] == _BENCH_STATS
    assert res["annotations"]["t.py::test_b"]["benchmark"]["mean"] == 2.7e-7
    # No cross-talk with the other branches.
    assert total is None
    assert sections == []
    assert res["run"]["pluginRender"] == {}
    assert res["run"]["pluginMeta"] is None


@requires_node
def test_new_run_clears_benchmark_data_and_annotations(tmp_path):
    # Lifecycle rides clearPluginData unchanged: both markRunning and onStarted
    # clear the run-level payload and the mirrored annotation channel (the
    # pluginChannels tracking) without touching an unrelated channel ("diff").
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "seedAnnotation",
                "id": "t.py::test_a[3]",
                "channel": "diff",
                "value": "added",
            },
            {"op": "onPluginData", "data": _BENCH_EVENT},
            {"op": "markRunning", "ids": ["t.py::test_a[3]"]},
            {"op": "benchmarkData"},
            {"op": "onPluginData", "data": _BENCH_EVENT},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "benchmarkData"},
        ],
    )
    after_markrunning, after_started = res["returns"]
    assert after_markrunning is None
    assert after_started is None
    # The benchmark channel is gone; the diff channel survived (F5).
    assert res["annotations"] == {"t.py::test_a[3]": {"diff": "added"}}


@requires_node
def test_unknown_render_is_ignored(tmp_path):
    # Routing: an unknown render value (a newer backend's shape) is ignored, in
    # the spirit of P10. Previously it fell into the coverage path.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {
                    "run_id": "r",
                    "plugin": "future",
                    "render": "hologram",
                    "data": {"files": {"a.py": 1}},
                },
            },
            {"op": "coverageTotal"},
            {"op": "renderSections"},
        ],
    )
    total, sections = res["returns"]
    assert total is None
    assert sections == []
    assert res["run"]["pluginData"] == {}
    assert res["run"]["pluginRender"] == {}
    assert res["run"]["artifacts"] == {}
    assert res["run"]["pluginMeta"] is None
    assert res["annotations"] == {}


@requires_node
def test_explicit_coverage_render_routes_like_legacy_null(tmp_path):
    # The null-legacy pin, both spellings: render:"coverage" and a no-render
    # event land on the same coverage path, pluginData plus per-file
    # annotations.
    cov = {"total": 75.0, "files": {"pkg/m.py": 75.0}}
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginData",
                "data": {"plugin": "pytest_cov", "render": "coverage", "data": cov},
            },
            {"op": "coverageTotal"},
            {"op": "clearPluginData"},
            {"op": "onPluginData", "data": {"plugin": "pytest_cov", "data": cov}},
            {"op": "coverageTotal"},
        ],
    )
    assert res["returns"] == [75.0, 75.0]
    assert res["annotations"]["pkg/m.py"]["pytest_cov"] == 75.0


@requires_node
def test_plugin_empty_reason_stored_and_cleared(tmp_path):
    # An optional plugin_empty `reason` (the runner's 32 MiB slimmer cap) is
    # kept per plugin, reads as null when absent (callers fall back to the
    # generic hint), and clears on the same lifecycle as the empty flag itself.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "onPluginEmpty",
                "data": {
                    "run_id": "r",
                    "plugin": "benchmark",
                    "reason": "output exceeded the 32 MiB cap",
                },
            },
            {"op": "onPluginEmpty", "data": {"run_id": "r", "plugin": "pytest_cov"}},
            {"op": "benchmarkEmpty"},
            {"op": "pluginEmptyReason", "id": "benchmark"},
            {"op": "pluginEmptyReason", "id": "pytest_cov"},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "benchmarkEmpty"},
            {"op": "pluginEmptyReason", "id": "benchmark"},
        ],
    )
    assert res["returns"] == [
        True,
        "output exceeded the 32 MiB cap",
        None,
        False,
        None,
    ]


@requires_node
def test_human_time_auto_scales(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "humanTime", "value": 9.4e-8},
            {"op": "humanTime", "value": 9.4e-5},
            {"op": "humanTime", "value": 0.0042},
            {"op": "humanTime", "value": 1.5},
            {"op": "humanTime", "value": None},
            {"op": "humanTime", "value": "fast"},
        ],
    )
    assert res["returns"] == ["94.0 ns", "94.0 µs", "4.2 ms", "1.50 s", "", ""]


@requires_node
def test_new_run_closes_open_coverage_source_view(tmp_path):
    # Lifecycle: an open coverage file is stale once a new run replaces the
    # coverage tmpdir (it would 404). clearPluginData, called by markRunning
    # and onStarted, closes it back to the run summary via closeCoverage.
    for trigger in (
        {"op": "markRunning", "ids": ["t.py::a"]},
        {"op": "onStarted", "data": {"run_id": "run-2"}},
    ):
        res = _run_ops(
            tmp_path,
            [
                {
                    "op": "openCoverageView",
                    "data": {"path": "m.py", "source": "x\n", "executed": [1]},
                },
                {"op": "snapCoverageView"},
                trigger,
                {"op": "snapCoverageView"},
            ],
        )
        before, after = res["returns"]
        assert before["open"] is True and before["path"] == "m.py"
        assert after["open"] is False and after["path"] is None
        assert res["coverageView"]["open"] is False


@requires_node
def test_superseded_coverage_fetch_does_not_reopen_view(tmp_path):
    # Race: openFile snapshots run.id before awaiting the fetch. If a new run
    # starts during the await (onStarted calls clearPluginData, which calls
    # closeCoverage, and run.id changes), the resolving old-run fetch must not
    # re-open the pane the lifecycle just closed. This reproduces the exact
    # guard: capture rid at run A, start run B, then apply the run-A result
    # guarded by rid === run.id.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onStarted", "data": {"run_id": "run-A"}},
            # rid captured here = "run-A"; fetch is now "in flight".
            {"op": "onStarted", "data": {"run_id": "run-B"}},  # new run, closes view
            {"op": "snapCoverageView"},
            {
                "op": "guardedOpen",
                "rid": "run-A",
                "data": {"path": "dead.py", "source": "x\n", "executed": [1]},
            },
            {"op": "snapCoverageView"},
        ],
    )
    after_b, guard_applied = res["returns"][0], res["returns"][1]
    final = res["returns"][2]
    assert after_b["open"] is False  # run B closed the view
    assert guard_applied is False  # rid "run-A" != run.id "run-B", so dropped
    assert final["open"] is False  # stayed closed; no dead-run source shown
    assert final["path"] is None


# --- onPluginEmpty / coverageEmpty note lifecycle -----------------------

_EMPTY_EVENT = {"run_id": "run-1", "plugin": "pytest_cov"}


@requires_node
def test_on_plugin_empty_sets_flag_and_coverage_empty_reads_true(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "coverageEmpty"},  # no event yet, so false
            {"op": "onPluginEmpty", "data": _EMPTY_EVENT},
            {"op": "coverageEmpty"},
        ],
    )
    assert res["run"]["pluginEmpty"] == {"pytest_cov": True}
    assert res["returns"] == [False, True]


@requires_node
def test_coverage_empty_false_when_data_present(tmp_path):
    # A data event means not empty, so coverageEmpty stays false. The two
    # events are mutually exclusive per plugin per run, so only one is applied.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _COV_EVENT},
            {"op": "coverageEmpty"},
        ],
    )
    assert res["returns"] == [False]


@requires_node
def test_new_run_clears_plugin_empty_note(tmp_path):
    # Lifecycle: a stale "no data" note after a new run is as wrong as stale
    # data, so it clears on the same triggers (markRunning and started).
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginEmpty", "data": _EMPTY_EVENT},
            {"op": "markRunning", "ids": ["t.py::a"]},
            {"op": "coverageEmpty"},
            {"op": "onPluginEmpty", "data": _EMPTY_EVENT},
            {"op": "onStarted", "data": {"run_id": "run-2"}},
            {"op": "coverageEmpty"},
        ],
    )
    assert res["returns"] == [False, False]
    assert res["run"]["pluginEmpty"] == {}


@requires_node
def test_plugin_data_then_empty_after_clear_are_exclusive(tmp_path):
    # In practice one run yields exactly one of the two. Across a clear, the
    # note follows the latest run: a data run reads not empty, an empty run
    # reads empty, and no stale coverage total/files remain.
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginData", "data": _COV_EVENT},
            {"op": "coverageEmpty"},
            {"op": "coverageTotal"},
            {"op": "clearPluginData"},
            {"op": "onPluginEmpty", "data": _EMPTY_EVENT},
            {"op": "coverageEmpty"},
            {"op": "coverageTotal"},
            {"op": "coverageFiles"},
        ],
    )
    assert res["returns"] == [False, 81.25, True, None, None]


@requires_node
def test_on_plugin_empty_ignores_missing_plugin_id(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "onPluginEmpty", "data": {"run_id": "r"}},
            {"op": "coverageEmpty"},
        ],
    )
    assert res["run"]["pluginEmpty"] == {}
    assert res["returns"] == [False]


# --- onWarning --------------------------------------------------------------------


@requires_node
def test_on_warning_appends_to_record_creating_it_if_needed(tmp_path):
    warn = {
        "category": "DeprecationWarning",
        "message": "dep",
        "filename": "g.py",
        "lineno": 9,
    }
    res = _run_ops(
        tmp_path,
        [
            {"op": "seed", "id": "have", "record": _rich_record()},
            {"op": "onWarning", "data": dict(warn, nodeid="have")},
            {"op": "onWarning", "data": dict(warn, nodeid="new")},
        ],
    )
    have = res["results"]["have"]
    # Appended after the pre-existing warning; phases/duration untouched.
    assert have["warnings"] == [_rich_record()["warnings"][0], warn]
    assert have["phases"] == _rich_record()["phases"]
    assert have["duration"] == _rich_record()["duration"]
    # Unknown nodeid: record created (ensureResult) holding just the warning.
    assert res["results"]["new"] == {"phases": {}, "warnings": [warn], "duration": None}


# --- onConsole --------------------------------------------------------------------


@requires_node
def test_on_console_appends_text_and_ignores_null_or_missing(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "onConsole", "data": {"text": "line1\n"}},
            {"op": "onConsole", "data": {"text": "line2"}},
            {"op": "onConsole", "data": {"text": None}},
            {"op": "onConsole", "data": {}},
        ],
    )
    # Accumulates in order; null/undefined text appends nothing (|| "").
    assert res["run"]["console"] == "line1\nline2"
