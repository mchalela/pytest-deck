<script>
  // One tree row — a leaf test or a group. Recurses into children for groups.
  // The checkbox is ALWAYS the first cell, flush left at every depth; indentation
  // is applied only to the content after the checkbox (prototype behaviour).
  import StatusBadge from "./StatusBadge.svelte";
  import RollupBadge from "./RollupBadge.svelte";
  import DiffBadge from "./DiffBadge.svelte";
  import BenchBadge from "./BenchBadge.svelte";
  import TreeRow from "./TreeRow.svelte";
  import {
    ui,
    toggleSelect,
    setSelected,
    toggleCollapse,
    leafVisible,
    nodeidVisible,
  } from "../lib/selection.svelte.js";

  let { node, depth = 0, isFile = false, onopen } = $props();

  // Visible leaves beneath this node drive group counts + the tri-state checkbox.
  // node.leaves already IS the subtree's flat nodeid list — no walk needed.
  let visibleLeaves = $derived(
    node.leaf
      ? leafVisible(node)
        ? [node.nodeid]
        : []
      : node.leaves.filter(nodeidVisible),
  );

  let selCount = $derived(
    visibleLeaves.filter((id) => ui.selected.has(id)).length,
  );
  let allOn = $derived(
    visibleLeaves.length > 0 && selCount === visibleLeaves.length,
  );
  let someOn = $derived(selCount > 0 && selCount < visibleLeaves.length);
  let isCollapsed = $derived(!node.leaf && ui.collapsed.has(node.key));
  let checked = $derived(node.leaf ? ui.selected.has(node.nodeid) : allOn);

  // Whether this node has any visible descendant (groups with none are hidden).
  let visible = $derived(
    node.leaf ? leafVisible(node) : visibleLeaves.length > 0,
  );

  function onCheck() {
    if (node.leaf) {
      toggleSelect(node.nodeid, !ui.selected.has(node.nodeid));
    } else {
      setSelected(visibleLeaves, !allOn);
    }
  }

  function setIndeterminate(el) {
    if (el) el.indeterminate = !node.leaf && someOn;
  }
</script>

{#if visible}
  <div
    class="row"
    class:group={!node.leaf}
    class:file={isFile}
    class:active-detail={node.leaf && node.nodeid === ui.detailId}
  >
    <input type="checkbox" {checked} use:setIndeterminate onchange={onCheck} />
    <span class="indent" style={`width: calc(var(--indent) * ${depth})`}></span>

    {#if node.leaf}
      <span class="caret leaf">▸</span>
      <DiffBadge nodeid={node.nodeid} />
      <span class="name" title={node.nodeid} onclick={onCheck}>{node.name}</span
      >
      <span class="marks">
        {#each node.markers as m (m)}<span class="mark">{m}</span>{/each}
      </span>
      <span class="spacer"></span>
      <!-- Benchmark mean column — renders only while the run has
           benchmark data (BenchBadge gates itself; leaf rows only, no
           group rollups: means don't aggregate). -->
      <BenchBadge nodeid={node.nodeid} />
      <StatusBadge nodeid={node.nodeid} {onopen} />
    {:else}
      <span
        class="caret"
        class:open={!isCollapsed}
        onclick={() => toggleCollapse(node.key)}>▸</span
      >
      <span class="name" onclick={() => toggleCollapse(node.key)}
        >{node.name}</span
      >
      <span class="count">{visibleLeaves.length}</span>
      <span class="spacer"></span>
      <RollupBadge leaves={node.leaves} />
    {/if}
  </div>

  {#if !node.leaf}
    <div class="children" class:collapsed={isCollapsed}>
      {#each node.children as child (child.key)}
        <TreeRow node={child} depth={depth + 1} isFile={false} {onopen} />
      {/each}
    </div>
  {/if}
{/if}

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 22px;
    min-height: 22px;
    max-height: 22px;
    padding: 1px 4px;
    border-radius: 4px;
    cursor: default;
    overflow: hidden;
  }
  .row:hover {
    background: var(--chip);
  }
  .row > input[type="checkbox"] {
    flex: none;
    margin: 0;
    accent-color: var(--accent);
  }
  .indent {
    flex: none;
  }
  .caret {
    flex: none;
    width: 14px;
    text-align: center;
    color: var(--muted);
    cursor: pointer;
    user-select: none;
    transition: transform 0.1s;
  }
  .caret.leaf {
    visibility: hidden;
  }
  .caret.open {
    transform: rotate(90deg);
  }
  .name {
    flex: 0 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
  }
  .row.group > .name {
    color: var(--group);
  }
  .row.group.file > .name {
    color: var(--accent);
    font-weight: 600;
  }
  .count {
    flex: none;
    color: var(--muted);
    font-size: 11px;
  }
  .marks {
    flex: none;
    display: flex;
    gap: 4px;
    margin-left: 4px;
  }
  .mark {
    font-size: 10px;
    padding: 0 6px;
    border-radius: 10px;
    line-height: 16px;
    background: var(--chip-on);
    color: #bcd6f5;
  }
  .spacer {
    flex: 1 1 auto;
  }
  .children.collapsed {
    display: none;
  }
  .row.active-detail {
    background: var(--chip);
    outline: 1px solid var(--accent);
  }
</style>
