// Tree helpers ported from the prototype: annotate nodes with a stable `key` and
// a flat list of their leaf nodeids. The server sends {name, children, leaf?,
// nodeid?, markers?}; we add `key`/`leaves` for selection + folding.

export function annotate(nodes, prefix = "") {
  for (const n of nodes) {
    n.key = prefix + "/" + n.name;
    if (n.leaf) {
      n.leaves = [n.nodeid];
    } else {
      annotate(n.children, n.key);
      n.leaves = n.children.flatMap((c) => c.leaves);
    }
  }
  return nodes;
}

export function walkLeaves(nodes, fn) {
  for (const n of nodes) {
    if (n.leaf) fn(n);
    else walkLeaves(n.children, fn);
  }
}

export function allGroupKeys(nodes, acc = []) {
  for (const n of nodes) {
    if (!n.leaf) {
      acc.push(n.key);
      allGroupKeys(n.children, acc);
    }
  }
  return acc;
}
