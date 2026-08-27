<script>
  // The benchmark mean-time cell for a leaf row — the tree column the
  // DiffBadge slot design reserved ("the slot a future column reuses"). Reads
  // the "benchmark" plugin annotation channel (P16: channel key = manifest id)
  // written by onPluginData; lifecycle rides clearPluginData.
  //
  // The CELL renders for every leaf only while the run has benchmark data
  // (benchmarkData() non-null) so the means align as a column — and not at all
  // on non-benchmark suites (no permanent gutter). A leaf without a record
  // (not benchmarked, or its callable raised — no save-file entry) renders an
  // empty cell. NO group-row rollups: means don't aggregate.
  import { annotationFor } from "../lib/annotations.svelte.js";
  import { benchmarkData, humanTime } from "../lib/results.svelte.js";

  let { nodeid } = $props();

  let active = $derived(benchmarkData() != null);
  let stats = $derived(annotationFor(nodeid, "benchmark"));
  let tip = $derived(
    stats
      ? `mean ${humanTime(stats.mean)} · median ${humanTime(stats.median)} · ${stats.rounds ?? "?"} rounds`
      : "",
  );
</script>

{#if active}
  <span class="benchcell" title={tip}>{stats ? humanTime(stats.mean) : ""}</span
  >
{/if}

<style>
  .benchcell {
    flex: none;
    min-width: 62px;
    text-align: right;
    font-size: 11px;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
</style>
