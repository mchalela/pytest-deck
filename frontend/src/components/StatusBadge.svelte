<script>
  // The right-hand status column for a single test. Reads the live store, so it
  // re-renders the instant a phase report for this nodeid lands.
  import { outcomeFor, resultFor } from "../lib/results.svelte.js";

  let { nodeid, onopen } = $props();

  const LABELS = {
    passed: "PASS",
    failed: "FAIL",
    error: "ERROR",
    skipped: "SKIP",
    xfailed: "XFAIL",
    xpassed: "XPASS",
    incomplete: "INCOMPL",
  };

  let outcome = $derived(outcomeFor(nodeid));
  let res = $derived(resultFor(nodeid));
  let nWarn = $derived(res && res.warnings ? res.warnings.length : 0);
</script>

<span class="statuscell">
  {#if nWarn}
    <span
      class="warnicon"
      title={`${nWarn} warning(s). Click for details`}
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}>⚠</span
    >
  {/if}

  <!-- Every badge is clickable — each state has its own detail-pane message. -->
  {#if !outcome}
    <span
      class="status clickable"
      title="no result yet. Click for info"
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}>—</span
    >
  {:else if outcome === "running"}
    <span
      class="status clickable st-running"
      title="running. Click for info"
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}><span class="lbl">running</span></span
    >
  {:else if outcome === "server-down"}
    <span
      class="status clickable st-server-down"
      title="the server became unreachable mid-run. Click for info"
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}><span class="lbl">server down</span></span
    >
  {:else if outcome === "missing"}
    <span
      class="status clickable st-missing"
      title="selected but not run. Click for why"
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}><span class="lbl">not found</span></span
    >
  {:else}
    <span
      class="status clickable st-{outcome}"
      title="click for traceback / logs"
      onclick={(e) => {
        e.stopPropagation();
        onopen(nodeid);
      }}><span class="lbl">{LABELS[outcome] || outcome}</span></span
    >
  {/if}
</span>

<style>
  .statuscell {
    flex: none;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .warnicon {
    color: #ffce54;
    cursor: pointer;
    font-size: 12px;
    line-height: 1;
  }
  .warnicon:hover {
    color: #ffe08a;
  }
  .status {
    flex: none;
    box-sizing: border-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 18px;
    min-width: 58px;
    padding: 0 8px;
    font-size: 11px;
    line-height: 1;
    border-radius: 9px;
    text-align: center;
    cursor: default;
    white-space: nowrap;
  }
  .status.clickable {
    cursor: pointer;
  }
  .status.clickable .lbl {
    text-decoration: underline dotted;
  }
  .status.clickable:hover {
    filter: brightness(1.15);
  }
  .status.st-passed {
    background: var(--st-passed-bg);
    color: var(--st-passed-fg);
  }
  .status.st-failed {
    background: var(--st-failed-bg);
    color: var(--st-failed-fg);
  }
  .status.st-error {
    background: var(--st-error-bg);
    color: var(--st-error-fg);
  }
  .status.st-skipped {
    background: var(--st-skipped-bg);
    color: var(--st-skipped-fg);
  }
  .status.st-xfailed {
    background: var(--st-xfailed-bg);
    color: var(--st-xfailed-fg);
  }
  .status.st-xpassed {
    background: var(--st-xpassed-bg);
    color: var(--st-xpassed-fg);
  }
  .status.st-incomplete {
    background: var(--st-incomplete-bg);
    color: var(--st-incomplete-fg);
  }
  .status.st-missing {
    background: var(--st-missing-bg);
    color: var(--st-missing-fg);
  }
  .status.st-running {
    background: var(--st-running-bg);
    color: var(--st-running-fg);
  }
  .status.st-server-down {
    background: var(--st-serverdown-bg);
    color: var(--st-serverdown-fg);
  }
</style>
