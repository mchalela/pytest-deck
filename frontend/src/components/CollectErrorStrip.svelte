<script>
  // "Collection errors" strip — pytest's ERRORS section. A compact FLAT
  // list of files that failed to collect (import error, broken conftest, ...).
  // Vanilla pytest still lists the good tests (the tree above) and reports these
  // separately; clicking one opens its traceback in the detail pane (via the
  // parent's `onopen`, which pins ui.detailId — the pane looks the id up in the
  // collectErrors store). Mirrors RemovedStrip's shape/pattern.
  let { errors = [], onopen } = $props();

  // Foldable: a project with a vendored/nested test tree can produce hundreds of
  // collect errors that push the real tree off-screen. Default collapsed — the
  // header + count stay visible so nothing is hidden.
  let open = $state(false);
</script>

{#if errors.length > 0}
  <div class="collerr">
    <div
      class="chead"
      onclick={() => (open = !open)}
      title={open ? "collapse" : "expand"}
    >
      <span class="caret" class:open>▸</span>
      Collection errors ({errors.length})
    </div>
    {#if open}
      {#each errors as e (e.nodeid)}
        <div
          class="crow"
          onclick={() => onopen(e.nodeid)}
          title="show traceback"
        >
          <span class="cbadge">ERROR</span>
          <span class="cid" title={e.nodeid}>{e.nodeid}</span>
        </div>
      {/each}
    {/if}
  </div>
{/if}

<style>
  .collerr {
    margin: 8px 0 12px;
    padding: 8px 10px;
    border: 1px solid var(--st-error-fg, #ff6b6b);
    border-radius: 6px;
    background: var(--panel);
  }
  .chead {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    text-transform: uppercase;
    color: var(--st-error-fg, #ff6b6b);
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
  .crow:first-of-type {
    margin-top: 6px;
  }
  .crow {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    height: 22px;
    min-height: 22px;
    padding: 1px 2px;
    overflow: hidden;
  }
  .crow:hover {
    background: var(--bg);
    border-radius: 4px;
  }
  .cbadge {
    flex: none;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--st-error-fg, #ff6b6b);
  }
  .cid {
    flex: 0 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--fg);
    font-size: 12px;
  }
</style>
