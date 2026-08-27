// Collection-error store. Vanilla pytest still collects the good tests
// on a mixed suite and reports each erroring file in an ERRORS section with the
// full traceback, then refuses to run until fixed. We mirror that: the tree shows
// the tests that DID collect, and each erroring file lands here so a compact
// "Collection errors" strip can list them and the detail pane can show the
// (ANSI-coloured) traceback when one is clicked.
//
// `list` is the raw array from /api/collect's `errors`; `byId` indexes it by
// nodeid so the detail pane can look up a clicked error's traceback. Keyed by
// nodeid like results/annotations so components subscribe the same way.
export const collectErrors = $state({ list: [], byId: {} });

// Replace the whole set on each collect (fresh every time, like the diff flags).
export function setCollectErrors(errors) {
  const list = errors || [];
  const byId = {};
  for (const e of list) byId[e.nodeid] = e;
  collectErrors.list = list;
  collectErrors.byId = byId;
}

// The traceback text for a clicked collection error, or null.
export function collectErrorFor(nodeid) {
  const e = collectErrors.byId[nodeid];
  return e ? e.longrepr_text : null;
}
