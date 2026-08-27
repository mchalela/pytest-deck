"""Tests for the coverage source-gutter view store
(`frontend/src/lib/coverageView.svelte.js`).

Same node-shim pattern as test_results_js.py: load the REAL module under node
with its single ``$state(`` neutralized to an identity wrapper, replay named
ops from stdin, snapshot the store + returns as JSON. The flagship logic —
per-line hit/miss/plain classification — is a pure function tested directly;
the fetch/view state (open file, loading, error) rides the same harness.

``node`` is required; tests skip cleanly if it's absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
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


_HARNESS = """
import {{ coverageView, startCoverageFetch, openCoverage, failCoverage,
  closeCoverage, classifyLines, missedCount }} from {covview_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  for (const op of ops) {{
    switch (op.op) {{
      case "startCoverageFetch": startCoverageFetch(op.path); break;
      case "openCoverage": openCoverage(op.data); break;
      case "failCoverage": failCoverage(op.message); break;
      case "closeCoverage": closeCoverage(); break;
      case "classifyLines":
        returns.push(classifyLines(op.source, op.executed, op.missing)); break;
      case "missedCount": returns.push(missedCount()); break;
      case "snap": returns.push({{ ...coverageView }}); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{ view: coverageView, returns }}));
}});
"""


def _neutralized_module(tmp_path):
    src = _COVVIEW_JS.read_text()
    assert src.count("$state(") == 1, "expected exactly one $state( to neutralize"
    out = tmp_path / "coverageview_neutralized.mjs"
    out.write_text(src.replace("$state(", "("))
    return out


def _run_ops(tmp_path, ops):
    mod = _neutralized_module(tmp_path)
    script = _HARNESS.format(covview_url=json.dumps(mod.as_uri()))
    return _run_node(script, ops)


# --- shim guard ---------------------------------------------------------------


def test_coverage_view_uses_exactly_one_state_rune():
    src = _COVVIEW_JS.read_text()
    assert src.count("$state(") == 1
    for other in ("$derived", "$effect", "$props", "$bindable", "$inspect"):
        assert other not in src, f"new rune {other} — revisit the node shim"


def test_coverage_view_is_transport_free():
    src = _COVVIEW_JS.read_text()
    assert '"./api.js"' not in src, "coverageView must stay transport-free"
    assert "fetch(" not in src


# --- classifyLines (the flagship pure function) -------------------------------


@requires_node
def test_classify_lines_maps_each_line_to_hit_miss_or_plain(tmp_path):
    src = "import os\n\n# a comment\nx = compute()\nreturn x\n"
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "classifyLines",
                "source": src,
                "executed": [1, 4],
                "missing": [5],
            }
        ],
    )
    rows = res["returns"][0]
    # 1-based numbering; line 1 & 4 ran (hit), 5 missed, 2/3 blank/comment (plain).
    assert [(r["n"], r["status"]) for r in rows] == [
        (1, "hit"),
        (2, "plain"),
        (3, "plain"),
        (4, "hit"),
        (5, "miss"),
    ]
    assert rows[0]["text"] == "import os"
    assert rows[2]["text"] == "# a comment"


@requires_node
def test_classify_lines_missing_wins_over_executed_on_overlap(tmp_path):
    # Defensive: a line in both sets reads as miss so a gap never looks covered.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "classifyLines",
                "source": "a\nb\n",
                "executed": [1, 2],
                "missing": [2],
            }
        ],
    )
    rows = res["returns"][0]
    assert [(r["n"], r["status"]) for r in rows] == [(1, "hit"), (2, "miss")]


@requires_node
def test_classify_lines_drops_trailing_newline_phantom(tmp_path):
    # A trailing newline must not produce an extra empty gutter line past EOF.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "classifyLines",
                "source": "one\ntwo\n",
                "executed": [],
                "missing": [],
            },
            {
                "op": "classifyLines",
                "source": "no-newline",
                "executed": [],
                "missing": [],
            },
            {"op": "classifyLines", "source": "", "executed": [], "missing": []},
        ],
    )
    two_line, no_nl, empty = res["returns"]
    assert [r["n"] for r in two_line] == [1, 2]
    assert [r["n"] for r in no_nl] == [1]
    # Empty source gives a single empty line (no phantom, not zero lines).
    assert [r["text"] for r in empty] == [""]


@requires_node
def test_classify_lines_100_percent_covered_all_hit(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "classifyLines",
                "source": "a\nb\nc\n",
                "executed": [1, 2, 3],
                "missing": [],
            }
        ],
    )
    assert all(r["status"] == "hit" for r in res["returns"][0])


@requires_node
def test_classify_lines_strips_trailing_cr_on_crlf_source(tmp_path):
    # A CRLF file split on \n leaves a stray \r per line; strip it so the
    # gutter text is clean under white-space:pre. Line numbering is unchanged.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "classifyLines",
                "source": "import os\r\nx = 1\r\n",
                "executed": [1, 2],
                "missing": [],
            }
        ],
    )
    rows = res["returns"][0]
    assert [r["text"] for r in rows] == ["import os", "x = 1"]
    assert [r["n"] for r in rows] == [1, 2]


# --- fetch / view lifecycle ---------------------------------------------------


@requires_node
def test_start_fetch_sets_loading_and_clears_prior_state(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "openCoverage",
                "data": {"path": "old.py", "source": "x", "executed": [1]},
            },
            {"op": "startCoverageFetch", "path": "new.py"},
            {"op": "snap"},
        ],
    )
    snap = res["returns"][0]
    assert snap["open"] is True
    assert snap["path"] == "new.py"
    assert snap["loading"] is True
    assert snap["error"] is None
    assert snap["source"] == ""
    assert snap["executed"] == [] and snap["missing"] == []


@requires_node
def test_open_coverage_populates_and_clears_loading(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "startCoverageFetch", "path": "m.py"},
            {
                "op": "openCoverage",
                "data": {
                    "path": "pkg/m.py",
                    "source": "a\nb\n",
                    "executed": [1],
                    "missing": [2],
                },
            },
            {"op": "missedCount"},
        ],
    )
    v = res["view"]
    assert v["open"] is True and v["loading"] is False and v["error"] is None
    assert v["path"] == "pkg/m.py"
    assert v["source"] == "a\nb\n"
    assert v["executed"] == [1] and v["missing"] == [2]
    assert res["returns"] == [1]  # missedCount


@requires_node
def test_missed_count_ignores_lines_past_eof(tmp_path):
    # Source has 2 lines but `missing` names line 5 (source edited shorter
    # since the run). Only misses that actually render (at most the line count)
    # count, so the header number matches the visible red gutter lines.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "openCoverage",
                "data": {"path": "m.py", "source": "a\nb\n", "missing": [2, 5]},
            },
            {"op": "missedCount"},
        ],
    )
    assert res["returns"] == [1]  # line 2 renders; line 5 (past EOF) dropped


@requires_node
def test_fail_coverage_keeps_pane_open_with_message(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "startCoverageFetch", "path": "gone.py"},
            {
                "op": "failCoverage",
                "message": "coverage for this run is no longer available",
            },
        ],
    )
    v = res["view"]
    assert v["open"] is True  # stays open to show the message
    assert v["loading"] is False
    assert v["error"] == "coverage for this run is no longer available"
    assert v["source"] == "" and v["executed"] == [] and v["missing"] == []


@requires_node
def test_fail_coverage_falls_back_to_generic_message(tmp_path):
    res = _run_ops(tmp_path, [{"op": "failCoverage", "message": None}])
    assert res["view"]["error"] == "coverage unavailable"


@requires_node
def test_close_coverage_resets_to_summary(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "openCoverage",
                "data": {"path": "m.py", "source": "x", "executed": [1]},
            },
            {"op": "closeCoverage"},
        ],
    )
    v = res["view"]
    assert v["open"] is False
    assert v["path"] is None
    assert v["source"] == "" and v["executed"] == [] and v["missing"] == []
    assert v["error"] is None
