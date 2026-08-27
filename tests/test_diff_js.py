"""Tests for the reload-with-diff frontend logic (`diff.js`) and the
forward-compat annotation-channel contract (`annotations.svelte.js`).

Reload-with-diff is frontend-only. The diff math lives in the PURE, Node-importable
`frontend/src/lib/diff.js` (+ `tree.js`); the annotation store lives in
`annotations.svelte.js`, which uses the Svelte 5 `$state` rune and does NOT
import under plain node. We use the node-shell pattern (same as
`test_outcome_js_parity.py` / the ansi.js tests):

* ``diff.js`` / ``tree.js`` — imported and run DIRECTLY (verified Node-importable).
* ``annotations.svelte.js`` — NOT directly importable (``$state is not defined``).
  Rather than mirror its logic in a hand-written snippet (which could drift), we
  load the REAL module with the single ``$state(...)`` rune neutralized to an
  identity wrapper. The store is plain ``{ byId: {} }`` either way and
  ``setAnnotation``/``clearChannel``/``annotationFor`` operate on plain-object
  semantics, so this exercises the actual shipped functions — only the reactive
  proxy is stubbed (it would no-op under node anyway). The module uses exactly one
  rune and no others (asserted by ``test_annotations_uses_only_state_rune``), so
  the neutralization can't silently skip newly-added reactive behavior.

``node`` is required; tests skip cleanly if it's absent (present here: Node 18).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
_DIFF_JS = _LIB / "diff.js"
_TREE_JS = _LIB / "tree.js"
_ANNOT_JS = _LIB / "annotations.svelte.js"


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


# === diff.js: buildLeafIndex / markersDiffer / diffCollections =============

# A reusable harness: import the real diff.js + tree.js, read a list of named
# operations from stdin, run each, and print results. Each op is
# {"fn": ..., "args": [...]}. Sets are returned as sorted arrays for stable JSON.
_DIFF_HARNESS = """
import {{ buildLeafIndex, markersDiffer, diffCollections }} from {diff_url};
import {{ annotate }} from {tree_url};

// Build a Map<nodeid,{{markers}}> from a flat [[nodeid, markers], ...] spec.
function idxFromPairs(pairs) {{
  if (pairs === null) return null;
  const m = new Map();
  for (const [id, markers] of pairs) m.set(id, {{ markers }});
  return m;
}}
const sortedSet = (s) => [...s].sort();

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const out = ops.map((op) => {{
    if (op.fn === "markersDiffer") {{
      return markersDiffer(op.a, op.b);
    }}
    if (op.fn === "buildLeafIndex") {{
      const tree = annotate(op.tree);              // real tree annotation first
      const idx = buildLeafIndex(tree);
      // Return as a sorted [[nodeid, markers], ...] for deterministic compare.
      return [...idx.entries()]
        .map(([id, rec]) => [id, rec.markers])
        .sort((x, y) => (x[0] < y[0] ? -1 : 1));
    }}
    if (op.fn === "diffCollections") {{
      const d = diffCollections(idxFromPairs(op.old), idxFromPairs(op.new));
      return {{
        added: sortedSet(d.added),
        removed: sortedSet(d.removed),
        changed: sortedSet(d.changed),
      }};
    }}
    throw new Error("unknown fn " + op.fn);
  }});
  process.stdout.write(JSON.stringify(out));
}});
"""


def _diff_harness():
    return _DIFF_HARNESS.format(
        diff_url=_import_url(_DIFF_JS), tree_url=_import_url(_TREE_JS)
    )


def _run_diff_ops(ops):
    return _run_node(_diff_harness(), ops)


# --- diffCollections ------------------------------------------------------


@requires_node
def test_diff_added_removed_changed():
    old = [
        ["test_a.py::test_one", ["slow"]],
        ["test_a.py::test_two", []],
        ["test_a.py::test_three", ["db"]],
    ]
    new = [
        ["test_a.py::test_one", ["slow"]],  # unchanged
        ["test_a.py::test_three", ["db", "smoke"]],  # marker set changed
        ["test_a.py::test_four", []],  # added
        # test_two removed
    ]
    (res,) = _run_diff_ops([{"fn": "diffCollections", "old": old, "new": new}])
    assert res["added"] == ["test_a.py::test_four"]
    assert res["removed"] == ["test_a.py::test_two"]
    assert res["changed"] == ["test_a.py::test_three"]


@requires_node
def test_diff_identical_collections_all_empty():
    same = [["test_a.py::t1", ["x"]], ["test_a.py::t2", []]]
    (res,) = _run_diff_ops([{"fn": "diffCollections", "old": same, "new": same}])
    assert res == {"added": [], "removed": [], "changed": []}


@requires_node
def test_diff_first_collect_empty_old_yields_nothing():
    new = [["test_a.py::t1", []], ["test_a.py::t2", ["slow"]]]
    # old=null (no prior collect) and old=[] (empty) both yield all-empty sets.
    res_null, res_empty = _run_diff_ops(
        [
            {"fn": "diffCollections", "old": None, "new": new},
            {"fn": "diffCollections", "old": [], "new": new},
        ]
    )
    assert res_null == {"added": [], "removed": [], "changed": []}
    assert res_empty == {"added": [], "removed": [], "changed": []}


@requires_node
def test_diff_parametrize_variants_added_and_removed():
    # Distinct parametrize variants are distinct nodeids, so they show up as
    # added and removed leaves.
    old = [["test_p.py::test_x[a]", []], ["test_p.py::test_x[b]", []]]
    new = [["test_p.py::test_x[a]", []], ["test_p.py::test_x[c]", []]]
    (res,) = _run_diff_ops([{"fn": "diffCollections", "old": old, "new": new}])
    assert res["added"] == ["test_p.py::test_x[c]"]
    assert res["removed"] == ["test_p.py::test_x[b]"]
    assert res["changed"] == []


@requires_node
def test_diff_marker_only_change_is_changed_not_added_removed():
    old = [["test_a.py::t1", ["slow"]]]
    new = [["test_a.py::t1", ["fast"]]]
    (res,) = _run_diff_ops([{"fn": "diffCollections", "old": old, "new": new}])
    assert res["changed"] == ["test_a.py::t1"]
    assert res["added"] == [] and res["removed"] == []


# --- markersDiffer --------------------------------------------------------


@requires_node
def test_markers_differ_is_order_insensitive():
    ops = [
        {"fn": "markersDiffer", "a": ["a", "b"], "b": ["b", "a"]},  # same set
        {"fn": "markersDiffer", "a": ["a", "b"], "b": ["a", "b"]},  # identical
        {"fn": "markersDiffer", "a": ["a"], "b": ["a", "b"]},  # genuine diff
        {"fn": "markersDiffer", "a": [], "b": []},  # both empty
        {"fn": "markersDiffer", "a": None, "b": []},  # missing vs empty
        {"fn": "markersDiffer", "a": None, "b": None},  # both missing
        {"fn": "markersDiffer", "a": [], "b": ["x"]},  # empty vs one
    ]
    res = _run_diff_ops(ops)
    assert res == [False, False, True, False, False, False, True]


# --- buildLeafIndex -------------------------------------------------------


@requires_node
def test_build_leaf_index_walks_to_leaves_only():
    # A realistic forest: a file holding a class holding params, plus a
    # top-level function. Only true leaves (nodeid-bearing) land in the index;
    # folder/class/group nodes do not.
    tree = [
        {
            "name": "test_mod.py",
            "children": [
                {
                    "name": "TestC",
                    "children": [
                        {
                            "name": "test_m",
                            "children": [
                                {
                                    "name": "[1]",
                                    "leaf": True,
                                    "nodeid": "test_mod.py::TestC::test_m[1]",
                                    "markers": ["slow"],
                                    "children": [],
                                },
                                {
                                    "name": "[2]",
                                    "leaf": True,
                                    "nodeid": "test_mod.py::TestC::test_m[2]",
                                    "markers": [],
                                    "children": [],
                                },
                            ],
                        }
                    ],
                },
                {
                    "name": "test_top",
                    "leaf": True,
                    "nodeid": "test_mod.py::test_top",
                    "markers": ["db", "smoke"],
                    "children": [],
                },
            ],
        }
    ]
    (entries,) = _run_diff_ops([{"fn": "buildLeafIndex", "tree": tree}])
    # Exactly the three leaves, with their markers; no folder/class/group nodes.
    assert entries == [
        ["test_mod.py::TestC::test_m[1]", ["slow"]],
        ["test_mod.py::TestC::test_m[2]", []],
        ["test_mod.py::test_top", ["db", "smoke"]],
    ]


@requires_node
def test_build_leaf_index_handles_missing_markers():
    tree = [
        {
            "name": "test_x.py",
            "children": [
                {
                    "name": "test_nomarks",
                    "leaf": True,
                    "nodeid": "test_x.py::test_nomarks",
                    "children": [],
                    # no "markers" key at all
                }
            ],
        }
    ]
    (entries,) = _run_diff_ops([{"fn": "buildLeafIndex", "tree": tree}])
    # Missing markers normalize to an empty list (so the diff treats it as "none").
    assert entries == [["test_x.py::test_nomarks", []]]


# === annotation forward-compat contract ===================================
#
# annotations.svelte.js is not directly Node-importable ($state rune). We load
# the real module with the single `$state(` neutralized to an identity wrapper,
# so we exercise the actual shipped setAnnotation/clearChannel/annotationFor on
# a plain object (the rune would no-op under node anyway). The neutralization is
# asserted safe by test_annotations_uses_only_state_rune.

_ANNOT_HARNESS = """
import {{ setAnnotation, annotationFor, clearChannel, annotations }}
  from {annot_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  for (const op of ops) {{
    if (op.op === "set") setAnnotation(op.id, op.channel, op.value);
    else if (op.op === "clear") clearChannel(op.channel);
  }}
  // Report the full store plus a couple of point reads for assertions.
  process.stdout.write(JSON.stringify({{
    byId: annotations.byId,
    reads: (ops.find((o) => o.op === "reads")?.keys || []).map(
      ([id, ch]) => annotationFor(id, ch)
    ),
  }}));
}});
"""


def _neutralized_annotations_module(tmp_path):
    """Write a copy of annotations.svelte.js with the $state rune neutralized.

    Replaces the single ``$state(`` with ``(`` (identity) so the store object is
    the plain ``{ byId: {} }`` it wraps. Everything else — the real
    setAnnotation/clearChannel/annotationFor bodies — is preserved verbatim.
    """
    src = _ANNOT_JS.read_text()
    assert src.count("$state(") == 1, "expected exactly one $state( to neutralize"
    neutralized = src.replace("$state(", "(")
    out = tmp_path / "annotations_neutralized.mjs"
    out.write_text(neutralized)
    return out


def _run_annot_ops(tmp_path, ops):
    mod = _neutralized_annotations_module(tmp_path)
    script = _ANNOT_HARNESS.format(annot_url=_import_url(mod))
    return _run_node(script, ops)


def test_annotations_uses_only_state_rune():
    """Guard: the neutralization is only safe if $state is the ONLY rune used.

    If a future edit adds $derived/$effect/$props, the identity-swap would no
    longer faithfully load the module and this test must be revisited — so fail
    loudly here rather than let the contract test silently under-cover.
    """
    src = _ANNOT_JS.read_text()
    assert src.count("$state(") == 1
    for other in ("$derived", "$effect", "$props", "$bindable", "$inspect"):
        assert other not in src, f"new rune {other} — revisit the node shim"


@requires_node
def test_channels_coexist_on_same_node(tmp_path):
    """diff + coverage on the SAME nodeid coexist — the extensible-column contract."""
    nodeid = "test_a.py::test_one"
    res = _run_annot_ops(
        tmp_path,
        [
            {"op": "set", "id": nodeid, "channel": "diff", "value": "added"},
            {"op": "set", "id": nodeid, "channel": "coverage", "value": 87},
            {"op": "reads", "keys": [[nodeid, "diff"], [nodeid, "coverage"]]},
        ],
    )
    # Both channels live on the one record, independent of each other.
    assert res["byId"][nodeid] == {"diff": "added", "coverage": 87}
    assert res["reads"] == ["added", 87]


@requires_node
def test_clear_channel_removes_only_that_channel(tmp_path):
    """clearChannel('diff') drops diff everywhere but leaves coverage intact."""
    nodeid = "test_a.py::test_one"
    other = "test_a.py::test_two"
    res = _run_annot_ops(
        tmp_path,
        [
            {"op": "set", "id": nodeid, "channel": "diff", "value": "added"},
            {"op": "set", "id": nodeid, "channel": "coverage", "value": 87},
            {"op": "set", "id": other, "channel": "diff", "value": "changed"},
            {"op": "clear", "channel": "diff"},
            {"op": "reads", "keys": [[nodeid, "diff"], [nodeid, "coverage"]]},
        ],
    )
    # The diff channel is gone from both nodes...
    assert "diff" not in res["byId"].get(nodeid, {})
    assert other not in res["byId"], "node with only diff should be pruned"
    # ...but coverage survives on the node that had it.
    assert res["byId"][nodeid] == {"coverage": 87}
    assert res["reads"] == [None, 87]  # diff cleared reads null; coverage intact


@requires_node
def test_annotation_for_missing_returns_null(tmp_path):
    res = _run_annot_ops(
        tmp_path,
        [
            {"op": "set", "id": "id1", "channel": "diff", "value": "added"},
            {"op": "reads", "keys": [["id1", "coverage"], ["nope", "diff"]]},
        ],
    )
    # A missing channel on an existing node reads null, and so does a missing node.
    assert res["reads"] == [None, None]


def test_diff_js_files_exist():
    assert _DIFF_JS.is_file()
    assert _TREE_JS.is_file()
    assert _ANNOT_JS.is_file()
