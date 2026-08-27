// Per-node annotation store — the forward-compat centerpiece: tree rows are
// column-extensible (INVARIANTS F5).
//
// `byId[nodeid]` is a RECORD keyed by CHANNEL, e.g. { diff: "added" }. Each
// channel is an independent, per-node fact written by a different feature:
//   - "diff"      -> "added" | "changed" (written by reload)
//   - "coverage"  -> 87      (another feature writes onto the SAME record)
//   - "benchmark" -> {...}   (likewise)
// Channels coexist on one nodeid without knowing about each other — that's the
// extensible-column contract. Mirrors results.svelte.js (keyed by nodeid) so the
// components subscribe the same way.
export const annotations = $state({ byId: {} });

// Set one channel's value on a node (creating its record if needed). Reassign the
// record so Svelte tracks the change.
export function setAnnotation(nodeid, channel, value) {
  const rec = { ...(annotations.byId[nodeid] || {}) };
  rec[channel] = value;
  annotations.byId[nodeid] = rec;
}

// Read one channel for a node, or null. Cheap; called per leaf row.
export function annotationFor(nodeid, channel) {
  const rec = annotations.byId[nodeid];
  return rec ? (rec[channel] ?? null) : null;
}

// Drop one channel from EVERY node (e.g. clear stale "diff" flags before the next
// collect) WITHOUT disturbing other channels on the same record. Records left
// empty are pruned to keep the map tidy.
export function clearChannel(channel) {
  const next = {};
  for (const [id, rec] of Object.entries(annotations.byId)) {
    if (!(channel in rec)) {
      next[id] = rec;
      continue;
    }
    const copy = { ...rec };
    delete copy[channel];
    if (Object.keys(copy).length > 0) next[id] = copy;
  }
  annotations.byId = next;
}
