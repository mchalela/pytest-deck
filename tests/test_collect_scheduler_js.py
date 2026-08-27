"""Tests for the debounced collect scheduler
(`frontend/src/lib/collectScheduler.js`).

The regression this pins: a collect-scoped plugin-switch toggle arms the
~200ms debounce timer through the SAME entry point as everything else
(App's requestCollect → scheduler.request); if ▶ Run was clicked inside that
window, the old inline timer (which checked only `busy`) fired DURING the live
run and doCollect swapped the tree mid-stream — bypassing exactly the guard
that disables the ↻ Collect button. The subtle part (proved in review): App's
doRun calls markRunning synchronously but `run.active` only flips on the SSE
`started` event, so the hazard window is the whole debounce period, not just
SSE latency. The fix pairs the extracted scheduler (fire gated by isBlocked,
deferred — never dropped — while blocked) with the synchronous `run.pending`
signal in results.svelte.js.

These tests drive the toggle-trigger path deterministically: `request()` is
what a PluginSwitch toggle reaches (PluginSwitch → oncollectchange →
requestCollect → scheduler.request — the wiring is pinned in
test_plugins_js.py and below), with time driven by injected fake timers. The
integration tests run the scheduler against the REAL results store so the
markRunning→`started` gap itself is exercised, not simulated. Full App
component mounting isn't in this rig, so App's own wiring is pinned by source
inspection (the strongest test the rig allows there).

``node`` is required; tests skip cleanly if it's absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
_LIB = _FRONTEND / "lib"
_SCHED_JS = _LIB / "collectScheduler.js"
_APP = _FRONTEND / "App.svelte"
_RESULTS_JS = _LIB / "results.svelte.js"
_OUTCOME_JS = _LIB / "outcome.js"
_ANNOT_JS = _LIB / "annotations.svelte.js"
_COVVIEW_JS = _LIB / "coverageView.svelte.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def _run_node(script, stdin_payload):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    return json.loads(proc.stdout)


# Deterministic fake timers shared by both harnesses: a manual timer wheel the
# ops advance explicitly, so no real time and no flakiness. `advance` runs due
# callbacks in due-time order (re-arms landing inside the window run too,
# exactly like real setTimeout).
_FAKE_TIMERS = """
let now = 0, nextId = 1;
const timers = new Map();
const setTimer = (fn, ms) => {{
  const id = nextId++;
  timers.set(id, {{ at: now + ms, fn }});
  return id;
}};
const clearTimer = (id) => {{ timers.delete(id); }};
function advance(ms) {{
  const end = now + ms;
  for (;;) {{
    let bestId = null, bestAt = Infinity;
    for (const [id, t] of timers) if (t.at <= end && t.at < bestAt) {{
      bestAt = t.at; bestId = id;
    }}
    if (bestId === null) break;
    const t = timers.get(bestId);
    timers.delete(bestId);
    now = t.at;
    t.fn();
  }}
  now = end;
}}
"""

# Unit harness: isBlocked reads a plain flag the ops flip.
_UNIT_HARNESS = (
    """
import {{ makeCollectScheduler }} from {sched_url};
"""
    + _FAKE_TIMERS
    + """
let blocked = false, fired = 0;
const scheduler = makeCollectScheduler({{
  isBlocked: () => blocked,
  fire: () => fired++,
  setTimer, clearTimer,
}});

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  for (const op of ops) {{
    switch (op.op) {{
      case "request": scheduler.request(); break;   // = a toggle / Collect click
      case "setBlocked": blocked = op.on; break;
      case "advance": advance(op.ms); break;
      case "fired": returns.push(fired); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{ fired, returns }}));
}});
"""
)

# Integration harness: the scheduler guarded by the real results store.
# isBlocked reads run.active || run.pending exactly as App wires it (minus
# `busy`, which is App-local collect-in-flight state). markRunning and the SSE
# appliers drive the store through the genuine run lifecycle.
_STORE_HARNESS = (
    """
import {{ run, markRunning, markRunRejected, onStarted, onFinished }}
  from {results_url};
import {{ makeCollectScheduler }} from {sched_url};
"""
    + _FAKE_TIMERS
    + """
let fired = 0;
const scheduler = makeCollectScheduler({{
  isBlocked: () => run.active || run.pending,
  fire: () => fired++,
  setTimer, clearTimer,
}});

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  for (const op of ops) {{
    switch (op.op) {{
      case "request": scheduler.request(); break;   // = a toggle / Collect click
      case "markRunning": markRunning(op.ids); break;
      case "markRunRejected": markRunRejected(op.message); break;
      case "onStarted": onStarted(op.data); break;
      case "onFinished": onFinished(op.data); break;
      case "advance": advance(op.ms); break;
      case "fired": returns.push(fired); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{ fired, returns }}));
}});
"""
)


def _run_unit(ops):
    script = _UNIT_HARNESS.format(sched_url=json.dumps(_SCHED_JS.as_uri()))
    return _run_node(script, ops)


def _neutralized_results_module(tmp_path):
    """results.svelte.js with $state → identity and its store imports
    re-pointed, as in test_results_js.py (the rune-count guard lives there)."""

    def neutralize(src_path, name, n_state=1):
        src = src_path.read_text()
        assert src.count("$state(") == n_state
        out = tmp_path / name
        out.write_text(src.replace("$state(", "("))
        return out

    annotations_mod = neutralize(_ANNOT_JS, "annotations_neutralized.mjs")
    covview_mod = neutralize(_COVVIEW_JS, "coverageview_neutralized.mjs")
    src = _RESULTS_JS.read_text().replace("$state(", "(")
    src = src.replace('"./outcome.js"', json.dumps(_OUTCOME_JS.as_uri()))
    src = src.replace('"./annotations.svelte.js"', json.dumps(annotations_mod.as_uri()))
    src = src.replace('"./coverageView.svelte.js"', json.dumps(covview_mod.as_uri()))
    out = tmp_path / "results_neutralized.mjs"
    out.write_text(src)
    return out


def _run_store(tmp_path, ops):
    script = _STORE_HARNESS.format(
        results_url=json.dumps(_neutralized_results_module(tmp_path).as_uri()),
        sched_url=json.dumps(_SCHED_JS.as_uri()),
    )
    return _run_node(script, ops)


# --- App wiring pins (source inspection; no component mount in this rig) ----


def test_app_wires_scheduler_with_the_full_gate():
    # App must build the scheduler with the same conditions that disable the
    # Collect button (`busy || run.active`) plus the synchronous run.pending
    # gap signal, and requestCollect (the toggle path's oncollectchange target,
    # pinned in test_plugins_js.py) must delegate to it.
    src = _APP.read_text()
    assert 'from "./lib/collectScheduler.js"' in src
    assert "isBlocked: () => busy || run.active || run.pending" in src
    assert "collectScheduler.request()" in src
    assert "setTimeout" not in src, "inline debounce timer resurfaced in App"


def test_app_collect_reject_branch_keeps_the_tree():
    # doCollect's catch must handle the 4xx validation reject (server alive,
    # unknown/disabled ?plugins= id) before the hard-failure path that nukes
    # the tree, via the run path's markRunRejected surface. doCollect
    # classifies via collectFailureKind (api.js, pinned in test_api_js.py) so
    # the hard path also splits network vs subprocess for the panel copy; the
    # run paths keep the inline 4xx check.
    src = _APP.read_text()
    assert src.count("if (e.status >= 400 && e.status < 500)") == 2  # run paths
    assert "collectFailureKind(e)" in src
    assert 'if (kind === "reject")' in src
    assert src.count("markRunRejected(e.message)") == 3
    # …and doCollect (defined first) rejects before its `tree = null` hard path.
    assert src.index("markRunRejected(e.message)") < src.index("tree = null")
    # The hard path records the network/subprocess split for the panel.
    assert 'network: kind === "network"' in src


# --- unit: debounce + guard (fake timers) ------------------------------------


@requires_node
def test_burst_of_toggles_coalesces_to_one_fire_when_idle(tmp_path):
    res = _run_unit(
        [
            {"op": "request"},
            {"op": "advance", "ms": 100},
            {"op": "request"},  # re-arms: trailing debounce
            {"op": "advance", "ms": 199},
            {"op": "fired"},  # not yet: 1ms short of the trailing window
            {"op": "advance", "ms": 1},
            {"op": "fired"},
        ]
    )
    assert res["returns"] == [0, 1]
    assert res["fired"] == 1


@requires_node
def test_toggle_then_run_never_fires_mid_run_and_defers_to_after(tmp_path):
    # The regression itself: a toggle arms the timer and a run starts inside
    # the window (blocker up before the timer fires). The timer must not fire
    # during the run no matter how long it lasts, and the deferred collect
    # fires exactly once after the run ends.
    res = _run_unit(
        [
            {"op": "request"},  # toggle: timer armed for t=200
            {"op": "advance", "ms": 50},
            {"op": "setBlocked", "on": True},  # ▶ Run at t=50
            {"op": "advance", "ms": 5000},  # long run: many re-arm cycles
            {"op": "fired"},  # never fired mid-run
            {"op": "setBlocked", "on": False},  # run finished
            {"op": "advance", "ms": 250},
            {"op": "fired"},  # deferred collect fired once
            {"op": "advance", "ms": 5000},
            {"op": "fired"},  # …and never again
        ]
    )
    assert res["returns"] == [0, 1, 1]


@requires_node
def test_blocked_fire_is_deferred_not_dropped_even_with_new_requests(tmp_path):
    # Re-requests while blocked keep resetting the trailing timer; unblocking
    # still yields exactly one fire (the latest request).
    res = _run_unit(
        [
            {"op": "setBlocked", "on": True},
            {"op": "request"},
            {"op": "advance", "ms": 300},
            {"op": "request"},
            {"op": "advance", "ms": 300},
            {"op": "fired"},
            {"op": "setBlocked", "on": False},
            {"op": "advance", "ms": 200},
            {"op": "fired"},
        ]
    )
    assert res["returns"] == [0, 1]


# --- integration: scheduler with the real results store ----------------------


@requires_node
def test_toggle_then_run_defers_through_the_mark_running_gap(tmp_path):
    # The proven race, end to end on the real store: markRunning (what doRun
    # calls synchronously) must already block the timer, because `started`
    # arrives later over SSE, well after the 200ms window in this timeline. An
    # expression-only run (no nodeids) is the worst case: no chips, so
    # run.pending is the only signal.
    res = _run_store(
        tmp_path,
        [
            {"op": "request"},  # toggle at t=0 arms the timer for t=200
            {"op": "advance", "ms": 50},
            {"op": "markRunning", "ids": []},  # ▶ Run POSTed at t=50
            {"op": "advance", "ms": 400},  # `started` still not arrived
            {"op": "fired"},  # pending alone held the line
            {"op": "onStarted", "data": {"run_id": "r1", "nodeids": []}},
            {"op": "advance", "ms": 400},  # live run: active holds it now
            {"op": "fired"},
            {"op": "onFinished", "data": {"run_id": "r1", "exit_code": 0}},
            {"op": "advance", "ms": 250},
            {"op": "fired"},  # deferred collect, once, after the run
        ],
    )
    assert res["returns"] == [0, 0, 1]


@requires_node
def test_rejected_run_unblocks_the_deferred_collect(tmp_path):
    # A 4xx reject clears run.pending (the run will never go live), so the
    # deferred collect must then fire instead of wedging forever.
    res = _run_store(
        tmp_path,
        [
            {"op": "request"},
            {"op": "advance", "ms": 50},
            {"op": "markRunning", "ids": ["t1"]},
            {"op": "advance", "ms": 300},
            {"op": "fired"},  # blocked while pending
            {"op": "markRunRejected", "message": "bad plugin config"},
            {"op": "advance", "ms": 250},
            {"op": "fired"},  # unwedged: fired after the reject
        ],
    )
    assert res["returns"] == [0, 1]
