<script>
  // "Removed since last collect" strip. A compact FLAT list of tests
  // that were dropped by the last reload but still had stake (results and/or a
  // selection), each showing its last-known StatusBadge + a struck-through
  // nodeid. We deliberately do NOT splice ghost nodes back into the nested tree
  // (fragile); this is a separate, self-contained list. The parent clears it on a
  // subsequent collect that doesn't re-remove these ids.
  import StatusBadge from "./StatusBadge.svelte";

  let { ids = [], onopen } = $props();

  // Foldable (mirrors CollectErrorStrip). Usually short and informative (ghost
  // badges show how removed tests last did), so default OPEN — but a big reload
  // that drops a whole module can make it long, so it collapses.
  let open = $state(true);
</script>

{#if ids.length > 0}
  <div class="removed">
    <div
      class="rhead"
      onclick={() => (open = !open)}
      title={open ? "collapse" : "expand"}
    >
      <span class="caret" class:open>▸</span>
      Removed since last collect ({ids.length})
    </div>
    {#if open}
      {#each ids as id (id)}
        <div class="rrow">
          <StatusBadge nodeid={id} {onopen} />
          <span class="rid" title={id}>{id}</span>
        </div>
      {/each}
    {/if}
  </div>
{/if}

<style>
  .removed {
    margin: 8px 0 12px;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel);
  }
  .rhead {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    text-transform: uppercase;
    color: var(--muted);
    letter-spacing: 0.06em;
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
  .rrow:first-of-type {
    margin-top: 6px;
  }
  .rrow {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 22px;
    min-height: 22px;
    padding: 1px 2px;
    overflow: hidden;
  }
  .rid {
    flex: 0 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-decoration: line-through;
    color: var(--muted);
    font-size: 12px;
  }
</style>
