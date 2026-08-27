<script>
  // The coverage source-gutter view. Renders in
  // the right pane in place of the run-info console while a file is open, with
  // a back affordance to return to the run summary. Each line = a gutter cell
  // (line number) + the source text, tinted green (hit) / red (miss) / plain.
  //
  // The source arrives as PLAIN text (the coverage endpoint has no ANSI), so
  // the traceback ANSI highlighter doesn't apply here — lines render as escaped
  // text (Svelte escapes {text} automatically). The gutter tint is the visual.
  import {
    coverageView,
    classifyLines,
    missedCount,
    closeCoverage,
  } from "../lib/coverageView.svelte.js";

  let lines = $derived(
    classifyLines(
      coverageView.source,
      coverageView.executed,
      coverageView.missing,
    ),
  );
  let missed = $derived(missedCount());
</script>

<div class="covsrc">
  <div class="covsrchead">
    <span class="back" title="back to Run info" onclick={closeCoverage}
      >← Run info</span
    >
    <span class="path" title={coverageView.path}>{coverageView.path}</span>
    {#if !coverageView.loading && !coverageView.error}
      <span class="missed">{missed} line{missed === 1 ? "" : "s"} missed</span>
    {/if}
  </div>

  <div class="covsrcbody">
    {#if coverageView.loading}
      <p class="ok">loading coverage…</p>
    {:else if coverageView.error}
      <p class="err">{coverageView.error}</p>
      <p class="ok">
        <span class="back" onclick={closeCoverage}>← back to Run info</span>
      </p>
    {:else}
      <div class="code">
        {#each lines as ln (ln.n)}
          <div class="ln ln-{ln.status}">
            <span class="gutter">{ln.n}</span>
            <span class="src">{ln.text}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .covsrc {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }
  .covsrchead {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 4px 10px;
    border-bottom: 1px solid var(--line);
    margin-bottom: 8px;
  }
  .back {
    flex: none;
    cursor: pointer;
    color: var(--muted);
    font-size: 12px;
  }
  .back:hover {
    color: var(--fg);
  }
  .path {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: var(--accent);
  }
  .missed {
    flex: none;
    font-size: 11px;
    color: var(--st-failed-fg);
  }
  .covsrcbody {
    flex: 1 1 auto;
    overflow: auto;
    min-height: 0;
  }
  .code {
    font-size: 12px;
    line-height: 1.5;
  }
  .ln {
    display: flex;
    align-items: baseline;
    white-space: pre;
  }
  .gutter {
    flex: none;
    width: 44px;
    padding: 0 8px 0 6px;
    text-align: right;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    user-select: none;
    border-left: 3px solid transparent;
  }
  .src {
    flex: 1 1 auto;
    white-space: pre;
    word-break: normal;
    color: var(--fg);
  }
  /* executed: subtle green tint + a green gutter marker */
  .ln-hit .gutter {
    border-left-color: var(--st-passed-fg);
    color: var(--st-passed-fg);
  }
  .ln-hit {
    background: var(--st-passed-bg);
  }
  /* missing: subtle red tint + a red gutter marker */
  .ln-miss .gutter {
    border-left-color: var(--st-failed-fg);
    color: var(--st-failed-fg);
  }
  .ln-miss {
    background: var(--st-failed-bg);
  }
  /* plain (blank/comment/non-statement): no tint */
  .ok {
    color: var(--muted);
  }
  .err {
    color: var(--st-failed-fg);
  }
</style>
