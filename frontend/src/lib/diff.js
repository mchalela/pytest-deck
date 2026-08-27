// Pure collection-diff logic. NO Svelte / DOM imports — this module is
// Node-importable so the tester can shell `node` over it (like the outcome.js
// parity test). Keep it free of runes and reactive state.
//
// Product decision (locked): "changed" == a test's MARKER SET differs. We do NOT
// look at line numbers or any source-edit signal — lineno is noisy/misleading
// (formatting shifts move every test) and real source-edit detection is a beta
// item. Added/removed (including parametrize variants, which are distinct
// nodeids) are the unambiguous, high-value cases.

import { walkLeaves } from "./tree.js";

// Build a flat index of the collection's leaves: nodeid -> { markers }.
// `tree` is the annotated forest (so `walkLeaves` can recurse). We only retain
// what the diff compares (markers today); the shape stays open for more fields.
export function buildLeafIndex(tree) {
  const idx = new Map();
  if (!tree) return idx;
  walkLeaves(tree, (leaf) => {
    idx.set(leaf.nodeid, { markers: leaf.markers ? [...leaf.markers] : [] });
  });
  return idx;
}

// Do two marker arrays differ as SETS? Sort defensively (collection order is not
// guaranteed stable), compare element-wise. Treats missing/empty as "no markers".
export function markersDiffer(a, b) {
  const sa = [...(a || [])].sort();
  const sb = [...(b || [])].sort();
  if (sa.length !== sb.length) return true;
  for (let i = 0; i < sa.length; i++) {
    if (sa[i] !== sb[i]) return true;
  }
  return false;
}

// Diff two leaf indexes (old vs new), each a Map<nodeid, {markers}>.
//   added   = nodeids in new but not old
//   removed = nodeids in old but not new
//   changed = nodeids in BOTH whose marker sets differ
// First collect (oldIdx null/empty) -> everything empty (no diff to show).
// Identical collections -> all three empty.
export function diffCollections(oldIdx, newIdx) {
  const added = new Set();
  const removed = new Set();
  const changed = new Set();
  if (!oldIdx || oldIdx.size === 0) {
    return { added, removed, changed };
  }
  for (const [id, rec] of newIdx) {
    if (!oldIdx.has(id)) {
      added.add(id);
    } else if (markersDiffer(oldIdx.get(id).markers, rec.markers)) {
      changed.add(id);
    }
  }
  for (const id of oldIdx.keys()) {
    if (!newIdx.has(id)) removed.add(id);
  }
  return { added, removed, changed };
}
