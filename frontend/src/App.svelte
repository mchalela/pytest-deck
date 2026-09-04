<script>
  // Top-level dashboard: header + 3 columns (sidebar | tree | detail). Owns the
  // collect/run/cancel orchestration; selection + results live in the stores.
  import TreeRow from "./components/TreeRow.svelte";
  import MarkerChips from "./components/MarkerChips.svelte";
  import DetailPane from "./components/DetailPane.svelte";
  import RemovedStrip from "./components/RemovedStrip.svelte";
  import CollectErrorStrip from "./components/CollectErrorStrip.svelte";
  import PluginPanel from "./components/PluginPanel.svelte";
  import {
    collect,
    collectFailureKind,
    startRun,
    cancelRun,
    fetchPlugins,
  } from "./lib/api.js";
  import {
    plugins,
    setPlugins,
    runPayload,
    collectPluginIds,
  } from "./lib/plugins.svelte.js";
  import {
    collectErrors,
    setCollectErrors,
  } from "./lib/collectErrors.svelte.js";
  import { annotate, walkLeaves, allGroupKeys } from "./lib/tree.js";
  import { buildLeafIndex } from "./lib/diff.js";
  import { swapLeaves, hasPrevLeaves } from "./lib/collection.svelte.js";
  import { clearChannel } from "./lib/annotations.svelte.js";
  import {
    ui,
    setSelected,
    setCollapsed,
    leafVisible,
  } from "./lib/selection.svelte.js";
  import {
    run,
    failedNodeids,
    runInFlight,
    markRunning,
    markRunRejected,
    markServerDown,
    clearResults,
    clearPluginData,
  } from "./lib/results.svelte.js";
  import { connectEvents } from "./lib/connection.js";
  import { reconcileAfterCollect } from "./lib/reload.js";
  import { makeCollectScheduler } from "./lib/collectScheduler.js";
  import {
    HANDLE_W,
    clampPanes,
    resizeLeft,
    resizeRight,
    loadPanes,
    savePanes,
  } from "./lib/paneLayout.js";

  let tree = $state(null); // forest of nodes
  let markers = $state([]);
  let status = $state("collecting…");
  let busy = $state(false); // collect in flight (disables Collect/Run)
  // A HARD collect failure — the whole tree is unavailable, distinct from
  // "haven't collected yet". `{message, network}`: a subprocess failure (e.g.
  // a broken conftest pytest can't import) holds pytest's own error text; a
  // network failure (fetch threw — server unreachable) is framed differently,
  // since pytest never ran (no false "same as your terminal" claim).
  let collectError = $state(null);

  // Node ids removed by the last reload that still had results/selection.
  // Rendered in the RemovedStrip; cleared on a collect that doesn't re-remove them.
  let removedIds = $state([]);

  // The pytest expression fields, browser-held like the selection.
  //  -k = name filter (substring over test/class/param-id/module)
  //  -m = marker expression (boolean over marker names)
  // These are SEPARATE from the marker chips, which stay select-only.
  let kField = $state("");
  let mField = $state("");

  // Open the SSE stream once, at load.
  connectEvents();

  // Draggable pane dividers. Side-column widths in px (the middle takes
  // the rest); the clamp/persist/restore rules live in paneLayout.js — this is
  // only the pointer wiring. Pointer capture keeps the drag alive when the
  // cursor leaves the 6px handle.
  let layoutEl = $state(null);
  function paneStorage() {
    try {
      return window.localStorage;
    } catch {
      return null; // loadPanes/savePanes degrade to defaults / no-op
    }
  }
  function usableWidth() {
    return (layoutEl ? layoutEl.clientWidth : window.innerWidth) - 2 * HANDLE_W;
  }
  let panes = $state(
    loadPanes(paneStorage(), window.innerWidth - 2 * HANDLE_W),
  );

  let drag = null; // {side, startX, left, right} while a divider drag is live
  function startDrag(e, side) {
    drag = { side, startX: e.clientX, left: panes.left, right: panes.right };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault(); // no text selection while dragging
  }
  function moveDrag(e) {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    panes =
      drag.side === "left"
        ? resizeLeft(drag.left + dx, panes.right, usableWidth())
        : resizeRight(panes.left, drag.right - dx, usableWidth());
  }
  function endDrag() {
    if (!drag) return;
    drag = null;
    savePanes(paneStorage(), panes, usableWidth());
  }
  function onWindowResize() {
    panes = clampPanes(panes.left, panes.right, usableWidth());
  }

  // The collect entry point. Coalesced through a ~200ms trailing DEBOUNCE
  // (collectScheduler.js) so a burst of triggers fires once; the guard is the
  // SAME gate that disables the Collect button (`busy || run.active`) plus
  // `run.pending` — markRunning sets it synchronously, closing the gap where a
  // run is spawning but `started` hasn't flipped `run.active` yet. A blocked
  // fire defers (re-arms) instead of dropping, so a collect-scoped toggle just
  // before ▶ Run re-collects once AFTER the run instead of racing it. The
  // debounce is the single entry point — watcher-ready for beta (a file
  // watcher would call requestCollect), but there's NO watcher now: reload is
  // the ↻ Collect button.
  const collectScheduler = makeCollectScheduler({
    isBlocked: () => busy || run.active || run.pending,
    fire: () => doCollect(),
  });
  function requestCollect() {
    collectScheduler.request();
  }

  async function doCollect() {
    status = "collecting…";
    busy = true;
    try {
      // Enabled collect-scoped plugin ids ride along (ids only) so the
      // tree reflects the toggled `-p` switches.
      const json = await collect(collectPluginIds());
      collectError = null; // recovered — a prior hard failure was fixed
      annotate(json.tree);
      // Collection errors (pytest's ERRORS section): refreshed every collect, so
      // fixing a file's import error and re-collecting clears its entry.
      setCollectErrors(json.errors || []);
      const newIdx = buildLeafIndex(json.tree);
      const firstCollect = !hasPrevLeaves();

      if (firstCollect) {
        // First collect — clean-reset behaviour, no diff to compute.
        ui.selected = new Set();
        ui.collapsed = new Set();
        ui.detailId = null;
        clearResults();
        clearChannel("diff");
        clearPluginData(); // Plugin channels are as stale as the results
        removedIds = [];
        status = collectStatusLine(json);
      } else {
        // Reload — diff + reconcile (preserve surviving selection/results)
        // instead of a wholesale reset. The choreography lives in reload.js.
        const r = reconcileAfterCollect(json, newIdx);
        removedIds = r.removedIds;
        status = r.statusLine;
      }

      const nErr = (json.errors || []).length;
      if (nErr > 0)
        status += ` · ${nErr} collection error${nErr > 1 ? "s" : ""}`;

      tree = json.tree;
      markers = json.markers;
      if (firstCollect) swapLeaves(newIdx); // record baseline (no prior to diff)
    } catch (e) {
      // A 4xx is the server ALIVE and rejecting the request before any
      // pytest ran (unknown/disabled ?plugins= id — server.py's
      // ManifestConfigError → 400). The current tree is still valid, so keep
      // it and surface the message on the status line — the run path's
      // markRunRejected surface, not the full "Collection failed" panel
      // (whose "same error your terminal pytest would show" copy would be a
      // lie for a deck-side reject).
      const kind = collectFailureKind(e);
      if (kind === "reject") {
        markRunRejected(e.message);
        return; // finally still clears busy
      }
      // Hard failure — the whole tree is unavailable; frame it in the main
      // area. A "network" throw (fetch failed / non-JSON body — server
      // down) is framed as a connection problem, since pytest never ran.
      tree = null;
      markers = [];
      setCollectErrors([]);
      collectError = { message: e.message, network: kind === "network" };
      status = "collection failed";
    } finally {
      busy = false;
    }
  }

  // First-collect status line: "{total} tests · {files} files".
  function collectStatusLine(json) {
    return `${json.total} tests · ${json.tree.length} files`;
  }

  async function doRun() {
    const k = kField.trim() || null;
    const m = mField.trim() || null;
    // Run is valid with ticked tests OR a non-empty -k/-m (expression-only run).
    if (!tree || (ui.selected.size === 0 && !k && !m)) {
      status = "nothing selected and no filter to run";
      return;
    }
    const nodeids = [...ui.selected];
    // Only the ticked tests get the in-flight sentinel; an expression may match
    // others we can't predict, so they fill in as `report` events arrive.
    markRunning(nodeids);
    try {
      // The plugins/extra_args fragment (omitted keys when the panel is idle).
      await startRun(nodeids, { k, m, ...runPayload() });
      // results stream in over SSE; run.status tracks progress.
    } catch (e) {
      // A 4xx answer is the server ALIVE and rejecting the run before it
      // started (bad plugin config / extra args) — show the backend's message
      // and roll back the optimistic chips; no outage banner.
      if (e.status >= 400 && e.status < 500) {
        markRunRejected(e.message);
        return;
      }
      // The run detaches from the POST — `started` can beat a lost 202 over
      // SSE, so only mark server-down if no run actually went live.
      if (!run.active) markServerDown();
    }
  }

  // One-shot re-run of every current failed/error result. The SAME run
  // path as doRun with the explicit nodeid list, except -k/-m are OMITTED (an
  // active expression could silently deselect a failed test the user asked to
  // re-run) and ui.selected stays untouched (not a selection mutation).
  // Plugin-panel state still rides along. Reruns overwrite results, as always.
  async function doRerunFailed() {
    const nodeids = failedIds;
    if (nodeids.length === 0) return;
    markRunning(nodeids);
    try {
      await startRun(nodeids, runPayload());
    } catch (e) {
      if (e.status >= 400 && e.status < 500) {
        markRunRejected(e.message);
        return;
      }
      if (!run.active) markServerDown();
    }
  }

  async function doCancel() {
    await cancelRun();
  }

  function selectAll(on) {
    if (!tree) return;
    const ids = [];
    walkLeaves(tree, (l) => {
      if (leafVisible(l)) ids.push(l.nodeid);
    });
    setSelected(ids, on);
  }

  function expandAll() {
    ui.collapsed = new Set();
  }
  function collapseAll() {
    if (tree) setCollapsed(allGroupKeys(tree), true);
  }

  let selCount = $derived(ui.selected.size);
  // Run is enabled when there's a selection OR a non-empty -k/-m expression
  // (expression-only runs are valid). Still blocked while collecting or running.
  let canRun = $derived(
    !!tree &&
      (ui.selected.size > 0 || kField.trim() !== "" || mField.trim() !== ""),
  );
  // The nodeids whose current result folds to failed/error (live results only;
  // the selector excludes ghosts and in-flight records). Recomputes as reports
  // land, enabling the re-run-failed button only when idle with failures.
  let failedIds = $derived(failedNodeids());
  // The header status line: prefer live run status while a run is active/ended.
  let headerStatus = $derived(run.status || status);
  // Only colour the status when it comes from a run (run.status set the level).
  let statusLevel = $derived(run.status ? run.level : "info");

  doCollect();
  // Fetch the installed-plugin manifests once at startup, alongside the
  // initial collect. On error/404 (older backend) the section stays hidden.
  // ini_leftovers ride along as the extra-args suggestion chips.
  fetchPlugins()
    .then((j) => setPlugins(j.plugins, j.ini_leftovers))
    .catch(() => {});
</script>

<header>
  <h1>pytest-<span class="accent">deck</span></h1>
  <button onclick={requestCollect} disabled={busy || run.active}
    >↻ Collect</button
  >
  <!-- The run buttons gate on runInFlight() (active OR pending), closing
       the pre-`started` double-POST gap the plain run.active check left. -->
  <button
    class="primary"
    onclick={doRun}
    disabled={busy || runInFlight() || !canRun}>▶ Run</button
  >
  <button
    onclick={doRerunFailed}
    disabled={busy || runInFlight() || failedIds.length === 0}
    title="re-run every test whose last result is failed or error (ignores the selection and -k/-m)"
    >▶ Re-run failed</button
  >
  {#if run.active}
    <button onclick={doCancel}>■ Cancel</button>
  {/if}
  <div class="exprs">
    <input
      class="expr"
      placeholder="-k name filter, e.g. &quot;login and not slow&quot;"
      title="pytest -k: case-insensitive substring over test name / class / param id / module"
      autocomplete="off"
      spellcheck="false"
      disabled={busy || run.active}
      bind:value={kField}
    />
    <input
      class="expr"
      placeholder="-m marker expr, e.g. &quot;slow and not db&quot;"
      title="pytest -m: boolean expression over marker names"
      autocomplete="off"
      spellcheck="false"
      disabled={busy || run.active}
      bind:value={mField}
    />
  </div>
  <span class="meta status-{statusLevel}">{headerStatus}</span>
</header>

<svelte:window onresize={onWindowResize} />

<div
  class="layout"
  bind:this={layoutEl}
  style="grid-template-columns: {panes.left}px {HANDLE_W}px minmax(0, 1fr) {HANDLE_W}px {panes.right}px"
>
  <aside class="sidebar">
    <h2>Markers (click to select)</h2>
    {#if tree}
      <MarkerChips {markers} {tree} />
    {/if}
    <h2 style="margin-top:20px">Filter</h2>
    <input
      class="search"
      placeholder="filter by name…"
      autocomplete="off"
      bind:value={ui.filter}
    />
    <h2 style="margin-top:20px">Selected</h2>
    <div class="meta">{selCount} tests selected</div>
    {#if plugins.available}
      <h2 style="margin-top:20px">Plugins</h2>
      <PluginPanel
        disabled={busy || run.active}
        oncollectchange={requestCollect}
      />
    {/if}
    <div class="sidenote">
      beta · live streaming · click a status for details
    </div>
  </aside>

  <div
    class="divider"
    role="separator"
    aria-orientation="vertical"
    aria-label="resize sidebar"
    onpointerdown={(e) => startDrag(e, "left")}
    onpointermove={moveDrag}
    onpointerup={endDrag}
    onpointercancel={endDrag}
  ></div>

  <main class="treepane">
    <div class="toolbar">
      <button class="mini" onclick={() => selectAll(true)}>select all</button>
      <button class="mini" onclick={() => selectAll(false)}>clear</button>
      <button class="mini" onclick={expandAll}>expand</button>
      <button class="mini" onclick={collapseAll}>collapse</button>
    </div>
    <CollectErrorStrip
      errors={collectErrors.list}
      onopen={(id) => (ui.detailId = id)}
    />
    <RemovedStrip ids={removedIds} onopen={(id) => (ui.detailId = id)} />
    {#if collectError}
      <div class="collectfail">
        <div class="cfhead">Collection failed</div>
        {#if collectError.network}
          <p class="cfmsg">
            The deck server couldn't be reached, so pytest never ran. This is a
            connection problem, not a test-suite one. Check the terminal running <code
              >pytest --deck</code
            >
            and press
            <strong>↻ Collect</strong> again.
          </p>
        {:else}
          <p class="cfmsg">
            pytest couldn't collect this suite, usually a broken
            <code>conftest.py</code>, an import error, or a bad
            <code>pytest.ini</code>. Fix the cause below and press
            <strong>↻ Collect</strong> again. This is the same error your
            terminal <code>pytest</code> would show.
          </p>
        {/if}
        <pre class="cferr">{collectError.message}</pre>
      </div>
    {:else if !tree}
      <div class="empty">No tests collected yet.</div>
    {:else if tree.length === 0}
      <div class="empty">No tests collected.</div>
    {:else}
      {#each tree as node (node.key)}
        <TreeRow
          {node}
          depth={0}
          isFile={true}
          onopen={(id) => (ui.detailId = id)}
        />
      {/each}
    {/if}
  </main>

  <div
    class="divider"
    role="separator"
    aria-orientation="vertical"
    aria-label="resize detail pane"
    onpointerdown={(e) => startDrag(e, "right")}
    onpointermove={moveDrag}
    onpointerup={endDrag}
    onpointercancel={endDrag}
  ></div>

  <DetailPane
    hasTree={!!tree}
    onclose={() => (ui.detailId = null)}
    onopen={(id) => (ui.detailId = id)}
  />
</div>

<style>
  header {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 20px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  header h1 {
    font-size: 16px;
    margin: 0;
    font-weight: 600;
  }
  header h1 .accent {
    color: var(--accent);
  }
  .meta {
    color: var(--muted);
    font-size: 12px;
  }
  /* status-info stays muted (neutral, e.g. "no tests matched"); only real
     errors go red, so exit-5 never reads as a failure. */
  .meta.status-warn {
    color: var(--accent);
  }
  .meta.status-error {
    color: #ff6b6b;
  }
  .exprs {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .expr {
    width: 200px;
    padding: 6px 9px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    font: inherit;
    font-size: 12px;
  }
  .expr:focus {
    outline: none;
    border-color: var(--accent);
  }
  .expr:disabled {
    opacity: 0.5;
  }
  /* 5-track grid — sidebar | handle | tree | handle | detail. The
     column widths are set inline from the `panes` state (paneLayout.js owns
     the clamps); the single row is minmax(0, 1fr) so the panes scroll inside
     the fixed-height layout instead of growing it. */
  .layout {
    display: grid;
    grid-template-rows: minmax(0, 1fr);
    height: calc(100vh - 53px);
  }
  .divider {
    cursor: col-resize;
    touch-action: none;
    background: transparent;
  }
  .divider:hover {
    background: var(--accent);
    opacity: 0.4;
  }
  .sidebar {
    min-width: 0;
    border-right: 1px solid var(--line);
    padding: 16px;
    overflow-y: auto;
    background: var(--panel);
    display: flex;
    flex-direction: column;
  }
  .sidebar h2 {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--muted);
    letter-spacing: 0.08em;
    margin: 0 0 10px;
  }
  .search {
    width: 100%;
    padding: 7px 10px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    font: inherit;
  }
  .sidenote {
    margin-top: auto;
    padding-top: 16px;
    font-size: 11px;
    color: var(--muted);
    border-top: 1px solid var(--line);
  }
  .treepane {
    overflow: auto;
    min-width: 0;
    padding: 12px 16px;
  }
  .toolbar {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .empty {
    color: var(--muted);
    padding: 40px;
    text-align: center;
  }
  .collectfail {
    margin: 12px;
    padding: 12px 14px;
    border: 1px solid var(--st-error-fg, #ff6b6b);
    border-radius: 6px;
    background: var(--panel);
  }
  .cfhead {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--st-error-fg, #ff6b6b);
    margin-bottom: 6px;
  }
  .cfmsg {
    font-size: 12px;
    color: var(--fg);
    margin: 0 0 10px;
    line-height: 1.5;
  }
  .cfmsg code {
    font-size: 11px;
    background: var(--bg);
    padding: 1px 4px;
    border-radius: 3px;
  }
  .cferr {
    margin: 0;
    max-height: 50vh;
    overflow: auto;
    padding: 10px;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 6px;
    color: var(--fg);
  }
</style>
