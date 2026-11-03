<script>
  // The diff marker for a leaf row. Reads the per-node "diff" annotation
  // channel and shows a subtle glyph after the test name: green "+" (added
  // since last collect) or amber "~" (its MARKER SET changed). Renders
  // nothing when unset, so it reserves no width; the right-hand badge column
  // (BenchBadge, StatusBadge) is where per-test plugin columns live, on the
  // same annotation model.
  //
  // HONESTY: "~" means markers-changed, NOT a detected code edit. The tooltip
  // says so — we don't claim to see source changes.
  import { annotationFor } from "../lib/annotations.svelte.js";

  let { nodeid } = $props();

  let flag = $derived(annotationFor(nodeid, "diff"));
</script>

{#if flag === "added"}
  <span class="d added" title="added since last collect">+</span>
{:else if flag === "changed"}
  <span
    class="d changed"
    title="markers changed since last collect (not a code-edit check)">~</span
  >
{/if}

<style>
  .d {
    flex: none;
    font-size: 12px;
    font-weight: 700;
    line-height: 1;
    user-select: none;
  }
  .d.added {
    color: #5fd38a;
  }
  .d.changed {
    color: #ffb454;
  }
</style>
