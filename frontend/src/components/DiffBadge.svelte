<script>
  // The diff marker cell for a leaf row. Reads the per-node "diff"
  // annotation channel and shows a subtle glyph: green "+" (added since last
  // collect) or amber "~" (its MARKER SET changed). One extra flex cell — the
  // slot a future column (coverage %, benchmark) reuses, same annotation model.
  //
  // HONESTY: "~" means markers-changed, NOT a detected code edit. The tooltip
  // says so — we don't claim to see source changes.
  import { annotationFor } from "../lib/annotations.svelte.js";

  let { nodeid } = $props();

  let flag = $derived(annotationFor(nodeid, "diff"));
</script>

<span class="diffcell">
  {#if flag === "added"}
    <span class="d added" title="added since last collect">+</span>
  {:else if flag === "changed"}
    <span
      class="d changed"
      title="markers changed since last collect (not a code-edit check)">~</span
    >
  {/if}
</span>

<style>
  .diffcell {
    flex: none;
    width: 12px;
    text-align: center;
    font-size: 12px;
    line-height: 1;
    user-select: none;
  }
  .d {
    font-weight: 700;
  }
  .d.added {
    color: #5fd38a;
  }
  .d.changed {
    color: #ffb454;
  }
</style>
