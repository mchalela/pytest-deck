<script>
  // The run-info view: pytest's own terminal output (header + final summary),
  // rendered from the streamed raw-ANSI `console` events. Shown in the detail
  // column when no test is pinned.
  import {
    run,
    resultFor,
    coverageTotal,
    coverageFiles,
    coverageEmpty,
    metadataInfo,
    metadataEmpty,
    benchmarkSummary,
    benchmarkEmpty,
    pluginEmptyReason,
    humanTime,
    renderSections,
    jsonTruncatedBytes,
    jsonTruncatedKeys,
    humanBytes,
  } from "../lib/results.svelte.js";
  import {
    coverageView,
    startCoverageFetch,
    openCoverage,
    failCoverage,
  } from "../lib/coverageView.svelte.js";
  import { fetchCoverageFile } from "../lib/api.js";
  import { ansiToHtml } from "../lib/ansi.js";
  import { headerAndSummary, summaryPieces } from "../lib/consoleTail.js";
  import CoverageSource from "./CoverageSource.svelte";
  import JsonTree from "./JsonTree.svelte";
  import StatusBadge from "./StatusBadge.svelte";

  let { hasTree, onopen } = $props();

  // Click a coverage file → fetch its source + hit/miss lines and open the
  // gutter view (CoverageSource) in place of this summary. Store holds the view
  // state; api.js does the transport. A stale run_id (new run) 404s → the
  // backend's message shows in place. run.id is the current run's id.
  //
  // Snapshot run.id BEFORE the await. If a new run's `started` lands during
  // the fetch (onStarted → clearPluginData → closeCoverage, and run.id changes),
  // the awaited result is for a dead run — drop it silently instead of
  // re-opening the pane the lifecycle just closed. Guards both success and
  // failure paths so a superseded fetch shows neither stale source nor a stale
  // error.
  async function openFile(path) {
    const rid = run.id;
    if (!rid) return;
    startCoverageFetch(path);
    try {
      const data = await fetchCoverageFile(rid, path);
      if (rid !== run.id) return; // superseded by a new run — drop silently
      openCoverage(data);
    } catch (e) {
      if (rid !== run.id) return;
      failCoverage(e.message);
    }
  }

  // Coverage was enabled this run but produced no data (empty --cov
  // target or --no-cov override) — explain the empty panel instead of leaving
  // it silently blank. Mutually exclusive with data, but guarded anyway.
  let covEmpty = $derived(coverageEmpty());

  // Run-level coverage total (plugin_data event). Null when the last run
  // produced no coverage payload — the line simply doesn't render then.
  let covTotal = $derived(coverageTotal());
  // Measured SOURCE files (worst-covered first). Coverage keys source
  // files, not the test tree, so these render as their own flat list rather
  // than a tree column. Null → whole panel hidden (same lifecycle as total).
  let covFiles = $derived(coverageFiles());

  // pytest-metadata's environment dict (render "metadata"), or null when
  // the last run produced none. Rendered as key/value rows; dict values
  // (Packages/Plugins) via JsonTree. Collapsed by default — reference data,
  // not per-run news. metaEmpty mirrors covEmpty (the same hint pattern).
  let meta = $derived(metadataInfo());
  let metaEmpty = $derived(metadataEmpty());
  let metaOpen = $state(false);

  // Benchmark run-level summary → one compact line (count + fastest/
  // slowest by mean); per-test detail lives on the tree column + detail pane.
  // benchEmpty mirrors covEmpty. A plugin_empty may carry a `reason` (the
  // runner's 32 MiB slimmer cap) shown in place of the generic hint.
  let benchSummary = $derived(benchmarkSummary());
  let benchEmpty = $derived(benchmarkEmpty());
  let covReason = $derived(pluginEmptyReason("pytest_cov"));
  let metaReason = $derived(pluginEmptyReason("metadata"));
  let benchReason = $derived(pluginEmptyReason("benchmark"));

  // The compact display name for a summary entry: the nodeid's final `::`
  // segment (full nodeid on the tooltip).
  function testName(nodeid) {
    const parts = String(nodeid || "").split("::");
    return parts[parts.length - 1] || String(nodeid || "");
  }

  function isDict(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  // Generic json/text render sections, one per plugin (coverage excluded —
  // it has its own panel). Each is {plugin, render, data, truncated}.
  let sections = $derived(renderSections());

  // Header + closing block (short-summary section / final banner), matched on
  // an ANSI-stripped copy — the extraction lives in consoleTail.js.
  // Once the run is over (`run.active` false: finished, error or cancelled) a
  // buffer with no banner is shown whole — that text IS pytest's message.
  //
  // The block is then split into pieces: each "FAILED nodeid - msg" line of
  // the short summary whose nodeid the store knows becomes a row with the
  // same clickable status badge as the tree (the badge reads its outcome off
  // the store itself), everything else stays pytest's raw text.
  let pieces = $derived(
    run.console
      ? summaryPieces(
          headerAndSummary(run.console, { finished: !run.active }),
          (id) => resultFor(id) != null,
        )
      : [],
  );
</script>

{#if coverageView.open}
  <CoverageSource />
{:else if !hasTree}
  <p class="ok">Collect a test suite to begin.</p>
{:else if !run.console}
  <p class="ok">
    No run yet. Select tests and press “▶ Run”. pytest’s report shows here;
    click a status for its traceback.
  </p>
{:else}
  {#if covTotal != null}
    <div class="covtotal">Coverage: {covTotal.toFixed(1)}%</div>
  {/if}
  {#if covFiles}
    <div class="covfiles">
      {#each covFiles as f (f.path)}
        <div
          class="covrow"
          title={`open ${f.path} with coverage gutter`}
          onclick={() => openFile(f.path)}
        >
          <span class="covpath">{f.path}</span>
          <span class="covpct">{f.pct.toFixed(1)}%</span>
        </div>
      {/each}
    </div>
  {:else if covEmpty}
    <div class="covnote">
      {covReason ??
        "Coverage enabled but no data collected. Check your --cov target (or --no-cov in extra args)."}
    </div>
  {/if}
  {#if benchSummary}
    <div class="benchline">
      Benchmarks: {benchSummary.count}
      {#if benchSummary.fastest}
        · fastest
        <span class="benchname" title={benchSummary.fastest.nodeid}
          >{testName(benchSummary.fastest.nodeid)}</span
        >
        {humanTime(benchSummary.fastest.mean)}
      {/if}
      {#if benchSummary.slowest && benchSummary.count > 1}
        · slowest
        <span class="benchname" title={benchSummary.slowest.nodeid}
          >{testName(benchSummary.slowest.nodeid)}</span
        >
        {humanTime(benchSummary.slowest.mean)}
      {/if}
    </div>
  {:else if benchEmpty}
    <div class="covnote">
      {benchReason ??
        "Benchmarks enabled but no timing data this run: no benchmark fixtures ran (or --benchmark-disable is on)."}
    </div>
  {/if}
  {#if meta}
    <div class="metasec">
      <div
        class="metahead"
        onclick={() => (metaOpen = !metaOpen)}
        title={metaOpen ? "collapse" : "expand"}
      >
        <span class="caret" class:open={metaOpen}>▸</span>
        Environment
      </div>
      {#if metaOpen}
        <div class="metarows">
          {#each Object.entries(meta) as [key, value] (key)}
            {#if isDict(value)}
              <div class="metadict"><JsonTree {value} name={key} /></div>
            {:else}
              <div class="metarow">
                <span class="metakey">{key}</span>
                <span class="metaval">{value}</span>
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    </div>
  {:else if metaEmpty}
    <div class="covnote">
      {metaReason ??
        "Environment metadata enabled but no data reported this run."}
    </div>
  {/if}
  {#each sections as s (s.plugin)}
    <div class="rendersec">
      <div class="renderhead">{s.plugin}</div>
      {#if s.render === "text"}
        <pre class="rendertext">{s.data ?? ""}</pre>
        {#if s.truncated}
          <div class="rendernote">(truncated, showing first 256 KiB)</div>
        {/if}
      {:else if s.render === "json"}
        {#if jsonTruncatedBytes(s.data) != null}
          <div class="rendernote">
            payload too large ({humanBytes(jsonTruncatedBytes(s.data))}):
            exceeds the 256 KiB render cap, not rendered.
            {#if jsonTruncatedKeys(s.data).length}
              <br />top-level keys: {jsonTruncatedKeys(s.data).join(", ")}
            {/if}
          </div>
        {:else}
          <div class="renderjson"><JsonTree value={s.data} /></div>
          {#if s.truncated}
            <div class="rendernote">(truncated)</div>
          {/if}
        {/if}
      {/if}
    </div>
  {/each}
  <div class="console">
    {#each pieces as p, i (i)}
      {#if p.kind === "entry"}
        <div class="sumrow">
          <StatusBadge nodeid={p.nodeid} {onopen} />
          <span class="sumid" title={p.nodeid}>{p.nodeid}</span>
          <!-- eslint-disable-next-line svelte/no-at-html-tags -- ansiToHtml escapes all text runs -->
          <span class="summsg">{@html ansiToHtml(p.rest)}</span>
        </div>
      {:else}
        <!-- eslint-disable-next-line svelte/no-at-html-tags -- ansiToHtml escapes all text runs -->
        <pre class="consoletext">{@html ansiToHtml(p.raw)}</pre>
      {/if}
    {/each}
  </div>
{/if}

<style>
  .ok {
    color: var(--muted);
  }
  .covtotal {
    font-size: 12px;
    color: var(--accent);
    margin-bottom: 8px;
  }
  /* The one-line benchmark summary (count + fastest/slowest by mean). */
  .benchline {
    font-size: 12px;
    color: var(--accent);
    margin-bottom: 8px;
  }
  .benchline .benchname {
    color: var(--fg);
  }
  .rendersec {
    margin-bottom: 12px;
    border: 1px solid var(--line);
    border-radius: 6px;
    overflow: hidden;
  }
  .renderhead {
    font-size: 11px;
    color: var(--muted);
    padding: 4px 8px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  .rendertext {
    margin: 0;
    max-height: 30vh;
    overflow: auto;
    padding: 8px;
    font-size: 12px;
    white-space: pre;
    color: var(--fg);
  }
  .renderjson {
    max-height: 30vh;
    overflow: auto;
    padding: 6px 8px;
  }
  .rendernote {
    font-size: 10px;
    color: var(--muted);
    padding: 2px 8px 6px;
  }
  .metasec {
    margin-bottom: 12px;
    border: 1px solid var(--line);
    border-radius: 6px;
    overflow: hidden;
  }
  .metahead {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--muted);
    padding: 4px 8px;
    background: var(--panel);
    cursor: pointer;
    user-select: none;
  }
  .caret {
    flex: none;
    width: 12px;
    text-align: center;
    transition: transform 0.1s;
  }
  .caret.open {
    transform: rotate(90deg);
  }
  .metarows {
    max-height: 30vh;
    overflow: auto;
    padding: 6px 8px;
    border-top: 1px solid var(--line);
  }
  .metarow {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
    line-height: 1.5;
  }
  .metakey {
    flex: none;
    color: var(--muted);
  }
  .metaval {
    color: var(--fg);
    overflow-wrap: anywhere;
  }
  .metadict {
    font-size: 12px;
  }
  .covnote {
    font-size: 11px;
    color: var(--st-incomplete-fg);
    margin-bottom: 12px;
  }
  .covfiles {
    max-height: 30vh;
    overflow: auto;
    margin-bottom: 12px;
    border: 1px solid var(--line);
    border-radius: 6px;
  }
  .covrow {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 8px;
    font-size: 11px;
    color: var(--muted);
    cursor: pointer;
  }
  .covrow:hover {
    background: var(--chip);
  }
  .covpath {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .covpct {
    flex: none;
    text-align: right;
  }
  .console {
    font-size: 12px;
    line-height: 1.45;
    color: var(--fg);
  }
  .consoletext {
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
    font: inherit;
    white-space: pre-wrap;
    word-break: break-word;
  }
  /* One short-summary entry: badge first, so it reads like the tree column. */
  .sumrow {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 1px 0;
  }
  .sumid {
    flex: none;
    max-width: 60%;
    overflow-wrap: anywhere;
  }
  .summsg {
    flex: 1 1 auto;
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--muted);
  }
</style>
