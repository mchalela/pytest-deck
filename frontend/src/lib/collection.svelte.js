// Cross-collect state for reload+diff. Holds the PREVIOUS collection's
// leaf index so the next collect can diff against it, plus a monotonic generation
// counter (each successful collect bumps it — useful for watcher-driven reloads
// in beta, and for invalidating stale in-flight work).
//
// Kept tiny and reactive only where it needs to be; the diff math itself lives in
// the pure diff.js so it stays Node-testable.

export const collection = $state({
  generation: 0, // bumped each successful collect
});

// The previous collect's leaf index (Map<nodeid, {markers}>), or null before the
// first collect. Not reactive state itself — it's swapped imperatively in
// doCollect; the diff it produces drives the reactive annotation store.
let prevLeaves = null;

// Has there been a prior collect to diff against?
export function hasPrevLeaves() {
  return prevLeaves !== null;
}

// Swap in the new leaf index, bump the generation, and hand back the PRIOR index
// (so the caller can diff new-vs-prior). Returns null on the first collect.
export function swapLeaves(newIdx) {
  const prior = prevLeaves;
  prevLeaves = newIdx;
  collection.generation += 1;
  return prior;
}
