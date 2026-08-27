"""Tests for the JSON-tree pure helpers (`frontend/src/lib/jsonTree.js`).

Plain .js (no runes), so it loads directly under node — no neutralization. Same
node-shim invocation style as the other frontend tests. The recursive
JsonTree.svelte component stays thin over these; the classification is what's
worth locking.

``node`` is required; tests skip cleanly if it's absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
_JSONTREE_JS = _LIB / "jsonTree.js"

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
import {{ valueKind, isBranch, entriesOf, branchSummary,
  leafText }} from {jsontree_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  for (const op of ops) {{
    switch (op.op) {{
      case "valueKind": returns.push(valueKind(op.v)); break;
      case "isBranch": returns.push(isBranch(op.v)); break;
      case "entriesOf": returns.push(entriesOf(op.v)); break;
      case "branchSummary": returns.push(branchSummary(op.v)); break;
      case "leafText": returns.push(leafText(op.v)); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{ returns }}));
}});
"""


def _run_ops(ops):
    script = _HARNESS.format(jsontree_url=json.dumps(_JSONTREE_JS.as_uri()))
    return _run_node(script, ops)


def test_json_tree_is_plain_js_no_runes():
    src = _JSONTREE_JS.read_text()
    for rune in ("$state", "$derived", "$effect", "$props"):
        assert rune not in src


@requires_node
def test_value_kind_classifies_every_json_type():
    res = _run_ops(
        [
            {"op": "valueKind", "v": {}},
            {"op": "valueKind", "v": []},
            {"op": "valueKind", "v": "s"},
            {"op": "valueKind", "v": 3},
            {"op": "valueKind", "v": True},
            {"op": "valueKind", "v": None},
        ]
    )
    assert res["returns"] == ["object", "array", "string", "number", "boolean", "null"]


@requires_node
def test_is_branch_true_only_for_objects_and_arrays():
    res = _run_ops(
        [
            {"op": "isBranch", "v": {"a": 1}},
            {"op": "isBranch", "v": [1]},
            {"op": "isBranch", "v": "s"},
            {"op": "isBranch", "v": None},
        ]
    )
    assert res["returns"] == [True, True, False, False]


@requires_node
def test_entries_of_arrays_use_numeric_indices_objects_use_keys():
    res = _run_ops(
        [
            {"op": "entriesOf", "v": ["x", "y"]},
            {"op": "entriesOf", "v": {"k": 1}},
            {"op": "entriesOf", "v": 5},  # non-branch: no entries
        ]
    )
    arr, obj, leaf = res["returns"]
    assert arr == [[0, "x"], [1, "y"]]
    assert obj == [["k", 1]]
    assert leaf == []


@requires_node
def test_branch_summary_shows_child_counts():
    res = _run_ops(
        [
            {"op": "branchSummary", "v": {}},
            {"op": "branchSummary", "v": {"a": 1, "b": 2}},
            {"op": "branchSummary", "v": []},
            {"op": "branchSummary", "v": [1, 2, 3]},
            {"op": "branchSummary", "v": "s"},
        ]
    )
    assert res["returns"] == ["{}", "{2}", "[]", "[3]", ""]


@requires_node
def test_leaf_text_quotes_strings_and_stringifies_the_rest():
    res = _run_ops(
        [
            {"op": "leafText", "v": "hi"},
            {"op": "leafText", "v": 42},
            {"op": "leafText", "v": True},
            {"op": "leafText", "v": None},
        ]
    )
    # Strings get JSON quotes/escapes; others stringify plainly.
    assert res["returns"] == ['"hi"', "42", "true", "null"]
