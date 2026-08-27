// Reload reconciliation: diff the new collection against the previous one and
// reconcile annotations, results, and selection. The ORDER here is the whole
// point — see the inline notes. Pure orchestration over the stores; Node-
// shimmable like diff.js.
import { diffCollections } from "./diff.js";
import { swapLeaves } from "./collection.svelte.js";
import { setAnnotation, clearChannel } from "./annotations.svelte.js";
import { ui, reconcileSelection } from "./selection.svelte.js";
import { reconcileResults, clearPluginData } from "./results.svelte.js";

// Returns { removedIds, statusLine } for App to render.
export function reconcileAfterCollect(json, newIdx) {
  const survivors = new Set(newIdx.keys());
  const prev = swapLeaves(newIdx); // also bumps the collection generation
  const d = diffCollections(prev, newIdx);

  clearChannel("diff"); // F5: drop stale diff flags only, other channels intact
  clearPluginData(); // Coverage %s describe the pre-reload code state
  for (const id of d.added) setAnnotation(id, "diff", "added");
  for (const id of d.changed) setAnnotation(id, "diff", "changed");

  // Order-sensitive: reconcileResults FIRST (it names the dropped-with-results
  // ids and ghosts them — F2); droppedSelected BEFORE reconcileSelection
  // prunes ui.selected, or the stake information is lost.
  const droppedWithResults = reconcileResults(survivors);
  const droppedSelected = [...d.removed].filter((id) => ui.selected.has(id));
  reconcileSelection(survivors);

  // Removed strip = removed ids with stake (results and/or selection), deduped.
  const strip = new Set([...droppedWithResults, ...droppedSelected]);
  for (const id of strip) setAnnotation(id, "diff", "removed");

  return {
    removedIds: [...strip],
    statusLine:
      `reload: +${d.added.size} ~${d.changed.size} −${d.removed.size}` +
      ` · ${json.total} tests`,
  };
}
