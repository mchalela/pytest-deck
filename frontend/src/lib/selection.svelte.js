// Browser-held selection + UI state (selection lives in the browser per the
// spec). Reactive sets so tree rows and marker chips stay in sync.
export const ui = $state({
  selected: new Set(), // selected leaf nodeids
  collapsed: new Set(), // collapsed group keys
  filter: "", // name filter text
  detailId: null, // nodeid pinned in the detail pane, or null for run-info
});

// Svelte's reactivity tracks Set mutations only if we reassign; wrap mutations.
export function toggleSelect(nodeid, on) {
  const s = new Set(ui.selected);
  if (on) s.add(nodeid);
  else s.delete(nodeid);
  ui.selected = s;
}

export function setSelected(nodeids, on) {
  const s = new Set(ui.selected);
  for (const id of nodeids) {
    if (on) s.add(id);
    else s.delete(id);
  }
  ui.selected = s;
}

export function toggleCollapse(key) {
  const s = new Set(ui.collapsed);
  if (s.has(key)) s.delete(key);
  else s.add(key);
  ui.collapsed = s;
}

export function setCollapsed(keys, on) {
  const s = new Set(ui.collapsed);
  for (const k of keys) {
    if (on) s.add(k);
    else s.delete(k);
  }
  ui.collapsed = s;
}

// Reload reconciliation: keep the selection (and the pinned detail)
// only for tests still present in the new collection. Intersect `ui.selected`
// with the surviving ids; clear `ui.detailId` if its test was removed.
export function reconcileSelection(survivingIds) {
  const s = new Set();
  for (const id of ui.selected) {
    if (survivingIds.has(id)) s.add(id);
  }
  ui.selected = s;
  if (ui.detailId && !survivingIds.has(ui.detailId)) ui.detailId = null;
}

// The name filter matches on nodeid only, so visibility is a nodeid predicate.
export function nodeidVisible(nodeid) {
  const q = ui.filter.trim().toLowerCase();
  if (q && !nodeid.toLowerCase().includes(q)) return false;
  return true;
}

export function leafVisible(leaf) {
  return nodeidVisible(leaf.nodeid);
}
