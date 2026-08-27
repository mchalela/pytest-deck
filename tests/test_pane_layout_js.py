"""Tests for ``frontend/src/lib/paneLayout.js`` — the pane-divider geometry.

The clamp/persist/restore logic is extracted from App.svelte (the
collectScheduler.js pattern) exactly so it can be pinned here; the pointer
wiring in App is manual-verify. Rules under test:

* no pane can be dragged or restored below its minimum (nothing collapses),
* dragging one divider never moves the OTHER side pane,
* persistence is width-fractions with try/catch on both ends — junk/absent/
  throwing storage always degrades to the defaults, never a render failure.

Same node-shell pattern as test_ansi_js.py; skips cleanly without ``node``.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_PL_JS = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "lib"
    / "paneLayout.js"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

# Each stdin case names an exported function; storage-facing cases script a
# fake Storage (stored value / throwing getItem/setItem / no storage at all).
_HARNESS = f"""
import * as pl from {json.dumps(_PL_JS.as_uri())};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  const out = [];
  for (const c of cases) {{
    const store = {{}};
    const storage = c.noStorage
      ? null
      : {{
          getItem: () => {{
            if (c.getThrows) throw new Error("denied");
            return c.stored ?? null;
          }},
          setItem: (k, v) => {{
            if (c.setThrows) throw new Error("quota");
            store[k] = v;
          }},
        }};
    if (c.fn === "consts") {{
      out.push({{
        MIN_LEFT: pl.MIN_LEFT,
        MIN_MIDDLE: pl.MIN_MIDDLE,
        MIN_RIGHT: pl.MIN_RIGHT,
        HANDLE_W: pl.HANDLE_W,
      }});
    }} else if (c.fn === "loadPanes") {{
      out.push(pl.loadPanes(storage, c.total));
    }} else if (c.fn === "savePanes") {{
      pl.savePanes(storage, c.panes, c.total);
      out.push({{ saved: store[pl.STORAGE_KEY] ?? null }});
    }} else if (c.fn === "roundtrip") {{
      pl.savePanes(storage, c.panes, c.total);
      const reread = {{ getItem: () => store[pl.STORAGE_KEY] ?? null }};
      out.push(pl.loadPanes(reread, c.loadTotal ?? c.total));
    }} else {{
      out.push(pl[c.fn](...c.args));
    }}
  }}
  process.stdout.write(JSON.stringify(out));
}});
"""


def _run_cases(cases):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _consts():
    (c,) = _run_cases([{"fn": "consts"}])
    return c


# --- defaults / clamping --------------------------------------------------


@requires_node
def test_defaults_match_the_pre_b7_layout():
    # 260px sidebar; detail = 40% of the remainder (the old flex layout).
    (p,) = _run_cases([{"fn": "defaultPanes", "args": [1600]}])
    assert p == {"left": 260, "right": round((1600 - 260) * 0.4)}


@requires_node
def test_clamp_raises_undersized_panes_to_their_minimums():
    c = _consts()
    (p,) = _run_cases([{"fn": "clampPanes", "args": [10, 10, 1600]}])
    assert p == {"left": c["MIN_LEFT"], "right": c["MIN_RIGHT"]}


@requires_node
def test_clamp_keeps_the_middle_at_its_minimum():
    # Oversized side panes are pulled back so middle >= MIN_MIDDLE.
    c = _consts()
    (p,) = _run_cases([{"fn": "clampPanes", "args": [5000, 5000, 1600]}])
    assert p["left"] + p["right"] == 1600 - c["MIN_MIDDLE"]
    assert p["left"] >= c["MIN_LEFT"] and p["right"] >= c["MIN_RIGHT"]


@requires_node
def test_tiny_window_degrades_to_minimums_never_zero():
    # Too small to honor all three minimums: the side panes keep theirs (the
    # grid overflows horizontally), and no pane ever hits zero.
    c = _consts()
    (p,) = _run_cases([{"fn": "clampPanes", "args": [300, 300, 400]}])
    assert p == {"left": c["MIN_LEFT"], "right": c["MIN_RIGHT"]}


# --- divider drags --------------------------------------------------------


@requires_node
def test_left_drag_clamps_against_middle_min_with_right_fixed():
    c = _consts()
    total, right = 1600, 500
    (p,) = _run_cases([{"fn": "resizeLeft", "args": [5000, right, total]}])
    # right pane untouched; left stops where the middle hits its minimum.
    assert p["right"] == right
    assert p["left"] == total - c["MIN_MIDDLE"] - right


@requires_node
def test_left_drag_clamps_at_min_left():
    c = _consts()
    (p,) = _run_cases([{"fn": "resizeLeft", "args": [-50, 500, 1600]}])
    assert p == {"left": c["MIN_LEFT"], "right": 500}


@requires_node
def test_right_drag_clamps_against_middle_min_with_left_fixed():
    c = _consts()
    total, left = 1600, 300
    (p,) = _run_cases([{"fn": "resizeRight", "args": [left, 5000, total]}])
    assert p["left"] == left
    assert p["right"] == total - c["MIN_MIDDLE"] - left


@requires_node
def test_right_drag_clamps_at_min_right():
    c = _consts()
    (p,) = _run_cases([{"fn": "resizeRight", "args": [300, 0, 1600]}])
    assert p == {"left": 300, "right": c["MIN_RIGHT"]}


@requires_node
def test_in_range_drags_pass_through_rounded():
    p1, p2 = _run_cases(
        [
            {"fn": "resizeLeft", "args": [333.6, 500, 1600]},
            {"fn": "resizeRight", "args": [300, 449.4, 1600]},
        ]
    )
    assert p1 == {"left": 334, "right": 500}
    assert p2 == {"left": 300, "right": 449}


# --- persistence ----------------------------------------------------------


@requires_node
def test_save_load_roundtrip_restores_the_same_layout():
    (p,) = _run_cases(
        [{"fn": "roundtrip", "panes": {"left": 320, "right": 560}, "total": 1600}]
    )
    # Fractions round to 4 decimals, so px come back within 1 at the same width.
    assert abs(p["left"] - 320) <= 1
    assert abs(p["right"] - 560) <= 1


@requires_node
def test_roundtrip_scales_with_the_window_as_fractions():
    (p,) = _run_cases(
        [
            {
                "fn": "roundtrip",
                "panes": {"left": 400, "right": 400},
                "total": 1600,
                "loadTotal": 800,
            }
        ]
    )
    # Half the window gives half the px (clamped; both fractions land above mins).
    assert abs(p["left"] - 200) <= 1
    assert abs(p["right"] - 280) <= 1  # 200 clamped up to MIN_RIGHT


@requires_node
def test_load_with_nothing_stored_returns_defaults():
    res = _run_cases(
        [
            {"fn": "loadPanes", "total": 1600},  # empty storage
            {"fn": "defaultPanes", "args": [1600]},
        ]
    )
    assert res[0] == res[1]


@requires_node
def test_load_degrades_to_defaults_on_any_bad_storage():
    default = _run_cases([{"fn": "defaultPanes", "args": [1600]}])[0]
    bad = _run_cases(
        [
            {"fn": "loadPanes", "total": 1600, "stored": "not json {"},
            {"fn": "loadPanes", "total": 1600, "stored": '{"left":0,"right":0.4}'},
            {"fn": "loadPanes", "total": 1600, "stored": '{"left":1.5,"right":0.4}'},
            {"fn": "loadPanes", "total": 1600, "stored": '{"left":"a","right":0.4}'},
            {"fn": "loadPanes", "total": 1600, "stored": '{"right":0.4}'},
            {"fn": "loadPanes", "total": 1600, "getThrows": True},
            {"fn": "loadPanes", "total": 1600, "noStorage": True},
        ]
    )
    assert all(p == default for p in bad)


@requires_node
def test_load_with_valid_fractions_scales_and_clamps():
    c = _consts()
    (p,) = _run_cases(
        [{"fn": "loadPanes", "total": 2000, "stored": '{"left":0.25,"right":0.05}'}]
    )
    assert p["left"] == 500
    assert p["right"] == c["MIN_RIGHT"]  # 100px clamped up


@requires_node
def test_save_swallows_storage_failures():
    res = _run_cases(
        [
            {
                "fn": "savePanes",
                "panes": {"left": 300, "right": 500},
                "total": 1600,
                "setThrows": True,
            },
            {
                "fn": "savePanes",
                "panes": {"left": 300, "right": 500},
                "total": 1600,
                "noStorage": True,
            },
            # Degenerate width (pre-layout measurement) is guarded: no write.
            {"fn": "savePanes", "panes": {"left": 300, "right": 500}, "total": 0},
        ]
    )
    assert all(r == {"saved": None} for r in res)


@requires_node
def test_save_writes_fractions_not_px():
    (r,) = _run_cases(
        [{"fn": "savePanes", "panes": {"left": 400, "right": 640}, "total": 1600}]
    )
    assert json.loads(r["saved"]) == {"left": 0.25, "right": 0.4}
