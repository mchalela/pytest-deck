<script>
  // A small recursive collapsible JSON tree. Objects/arrays are expandable
  // branches; primitives render inline. Arbitrary plugin output — every key and
  // value is rendered as ESCAPED text ({...}), never {@html} (XSS discipline,
  // same as the coverage gutter). Classification lives in ../lib/jsonTree.js so this
  // component stays thin.
  import {
    valueKind,
    isBranch,
    entriesOf,
    branchSummary,
    leafText,
  } from "../lib/jsonTree.js";
  import Self from "./JsonTree.svelte";

  // `name` is the key/index this node hangs under (null at the root).
  let { value, name = null, depth = 0 } = $props();

  // Top two levels open by default; deeper stays collapsed to keep it compact.
  let open = $state(depth < 2);

  let branch = $derived(isBranch(value));
  let kind = $derived(valueKind(value));
  let entries = $derived(branch ? entriesOf(value) : []);
</script>

<div class="node" style={`padding-left: ${depth === 0 ? 0 : 12}px`}>
  {#if branch}
    <div class="branch" onclick={() => (open = !open)}>
      <span class="caret" class:open>▸</span>
      {#if name !== null}<span class="key">{name}:</span>{/if}
      <span class="summary">{branchSummary(value)}</span>
    </div>
    {#if open}
      {#each entries as [k, v] (k)}
        <Self value={v} name={k} depth={depth + 1} />
      {/each}
    {/if}
  {:else}
    <div class="leaf">
      {#if name !== null}<span class="key">{name}:</span>{/if}
      <span class="val val-{kind}">{leafText(value)}</span>
    </div>
  {/if}
</div>

<style>
  .node {
    font-size: 12px;
    line-height: 1.5;
  }
  .branch {
    cursor: pointer;
    user-select: none;
    display: flex;
    align-items: baseline;
    gap: 4px;
  }
  .leaf {
    display: flex;
    align-items: baseline;
    gap: 4px;
    padding-left: 14px; /* align under the caret column */
  }
  .caret {
    flex: none;
    width: 10px;
    color: var(--muted);
    transition: transform 0.1s;
  }
  .caret.open {
    transform: rotate(90deg);
  }
  .key {
    color: var(--muted);
  }
  .summary {
    color: var(--muted);
  }
  .val {
    color: var(--fg);
    font-variant-numeric: tabular-nums;
  }
  .val-string {
    color: var(--st-passed-fg);
  }
  .val-number {
    color: var(--accent);
  }
  .val-boolean,
  .val-null {
    color: var(--st-incomplete-fg);
  }
</style>
