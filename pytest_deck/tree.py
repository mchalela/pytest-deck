"""Build the nested test tree the dashboard renders, from collected items.

Moved out of the prototype stdlib server unchanged. The shape is consumed by the
frontend: a forest of nodes keyed by node-id path segments, with marker names
collected for the filter chips.
"""


def split_nodeid(nodeid):
    """Split a nodeid into hierarchy segments.

    ``pkg/test_x.py::TestC::test_m[1-2]`` becomes
    ``["pkg/test_x.py", "TestC", "test_m", "[1-2]"]``. The parametrize suffix
    becomes its own segment so variants fold under their base test.
    """
    head, _, params = nodeid.partition("[")
    segments = head.split("::")
    if params:
        segments.append("[" + params)  # keep the bracket for display
    return segments


def build_tree(items):
    """Build a generic nested tree keyed by nodeid path segments.

    Each node: ``{name, label, children, leaf, nodeid, markers}``. Only true
    leaves (actual collectible tests) carry a ``nodeid``; folder/group nodes
    don't, but the UI can still select all leaves beneath them.
    """
    root = {"name": "", "children": {}}
    all_markers = set()

    for item in items:
        nodeid = item["nodeid"]
        marks = [m["name"] for m in item["markers"] if m["name"] != "parametrize"]
        all_markers.update(marks)

        segments = split_nodeid(nodeid)
        node = root
        for seg in segments:
            node = node["children"].setdefault(seg, {"name": seg, "children": {}})
        # The final node is the real test leaf.
        node["leaf"] = True
        node["nodeid"] = nodeid
        node["markers"] = marks

    def freeze(node):
        children = [freeze(c) for c in node["children"].values()]
        out = {"name": node["name"], "children": children}
        if node.get("leaf"):
            out["leaf"] = True
            out["nodeid"] = node["nodeid"]
            out["markers"] = node.get("markers", [])
        return out

    forest = [freeze(c) for c in root["children"].values()]

    return {
        "markers": sorted(all_markers),
        "tree": forest,
        "total": len(items),
    }
