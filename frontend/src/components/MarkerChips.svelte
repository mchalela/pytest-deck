<script>
  // Marker chips — SELECT-only (locked decision, INVARIANTS F4). Clicking a chip
  // toggles selection of every visible test carrying that marker; it does NOT
  // build a -m expression. The chip lights up when all its tests are selected,
  // dashes when only some are.
  import { ui, setSelected, leafVisible } from "../lib/selection.svelte.js";
  import { walkLeaves } from "../lib/tree.js";

  let { markers, tree } = $props();

  function leavesWithMarker(m) {
    const out = [];
    walkLeaves(tree, (l) => {
      if (l.markers.includes(m) && leafVisible(l)) out.push(l.nodeid);
    });
    return out;
  }

  // Recompute chip state whenever selection, filter, or tree changes.
  let chips = $derived.by(() => {
    void ui.selected;
    void ui.filter; // track deps
    return markers.map((m) => {
      const ids = leavesWithMarker(m);
      const sel = ids.filter((id) => ui.selected.has(id)).length;
      const allOn = ids.length > 0 && sel === ids.length;
      const partial = sel > 0 && !allOn;
      return { m, ids, allOn, partial };
    });
  });

  function click(chip) {
    setSelected(chip.ids, !chip.allOn);
  }
</script>

{#if !markers.length}
  <span class="meta">none</span>
{:else}
  <div class="chips">
    {#each chips as chip (chip.m)}
      <span
        class="chip"
        class:on={chip.allOn}
        class:partial={chip.partial}
        title={`select / deselect the ${chip.ids.length} test(s) marked '${chip.m}'`}
        onclick={() => click(chip)}
        >{chip.m}<span class="count">{chip.ids.length}</span></span
      >
    {/each}
  </div>
{/if}

<style>
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .chip {
    padding: 4px 10px;
    border-radius: 14px;
    background: var(--chip);
    border: 1px solid var(--line);
    cursor: pointer;
    user-select: none;
    font-size: 12px;
  }
  .chip.on {
    background: var(--chip-on);
    border-color: var(--accent);
    color: #fff;
  }
  .chip.partial {
    border-color: var(--accent);
    border-style: dashed;
  }
  .chip .count {
    color: var(--muted);
    margin-left: 4px;
  }
  .chip.on .count {
    color: #bcd6f5;
  }
  .meta {
    color: var(--muted);
    font-size: 12px;
  }
</style>
