<script>
  // One plugin row: on/off switch + disclosure fold + the typed config form.
  // Purely schema-driven off the manifest fields (string → text, bool →
  // checkbox) — NO plugin-specific code lives here. All mutations go through
  // the store functions so the logic stays node-shim testable.
  import {
    plugins,
    setEnabled,
    setValue,
    affectsCollect,
  } from "../lib/plugins.svelte.js";

  let { plugin, disabled = false, oncollectchange = null } = $props();

  let st = $derived(plugins.byId[plugin.id]);
  let hasFields = $derived((plugin.fields || []).length > 0);
  let open = $state(false);

  // A manifest the backend marked disabled (disabled_reason set) is shown
  // greyed and inert — the switch is disabled and the config fold is suppressed.
  let reason = $derived(plugin.disabled_reason ?? null);
  let isDisabled = $derived(reason != null);
  // Effective disabled = backend-disabled OR the busy/running lockout.
  let switchDisabled = $derived(disabled || isDisabled);

  function onToggle(e) {
    setEnabled(plugin.id, e.currentTarget.checked);
    // A collect-scoped SWITCH changes what collect sees → trigger the
    // existing debounced re-collect (the tree change then flows through the
    // normal reload/diff choreography). Run-only toggles and field edits
    // never re-collect.
    if (affectsCollect(plugin.id)) oncollectchange?.();
    if (e.currentTarget.checked && hasFields) open = true;
  }
</script>

<div class="plugin" class:disabled={isDisabled}>
  <div class="row" class:on={st?.enabled}>
    <input
      type="checkbox"
      checked={st?.enabled || false}
      disabled={switchDisabled}
      onchange={onToggle}
      title={reason || `enable ${plugin.dist} for the next run`}
    />
    {#if hasFields && !isDisabled}
      <span
        class="caret"
        class:open
        onclick={() => (open = !open)}
        title="show config">▸</span
      >
    {:else}
      <span class="caret leaf">▸</span>
    {/if}
    <span
      class="label"
      title={reason || plugin.label}
      onclick={() => hasFields && !isDisabled && (open = !open)}
      >{plugin.label}</span
    >
  </div>

  {#if isDisabled}
    <div class="reason">{reason}</div>
  {/if}

  {#if open && hasFields && !isDisabled}
    <div class="fields">
      {#each plugin.fields as f (f.key)}
        <label class="field">
          {#if f.type === "bool"}
            <input
              type="checkbox"
              checked={st?.values[f.key] || false}
              {disabled}
              onchange={(e) =>
                setValue(plugin.id, f.key, e.currentTarget.checked)}
            />
            <span class="fname">{f.label}</span>
          {:else}
            <span class="fname">{f.label}</span>
            <input
              class="text"
              type="text"
              autocomplete="off"
              spellcheck="false"
              value={st?.values[f.key] ?? ""}
              {disabled}
              oninput={(e) => setValue(plugin.id, f.key, e.currentTarget.value)}
            />
          {/if}
        </label>
      {/each}
    </div>
  {/if}
</div>

<style>
  .plugin.disabled .label {
    color: var(--muted);
  }
  .reason {
    font-size: 10px;
    color: var(--muted);
    font-style: italic;
    margin: 0 0 4px 33px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 0;
  }
  .row input[type="checkbox"] {
    flex: none;
    margin: 0;
    accent-color: var(--accent);
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
  .label {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    cursor: default;
  }
  .row.on .label {
    color: var(--accent);
  }
  .fields {
    display: flex;
    flex-direction: column;
    gap: 6px;
    /* line the form up under the label (checkbox + caret + gaps) */
    margin: 2px 0 6px 33px;
  }
  .field {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }
  .field input[type="checkbox"] {
    flex: none;
    margin: 0;
    accent-color: var(--accent);
  }
  .fname {
    color: var(--muted);
    flex: none;
  }
  .text {
    flex: 1 1 auto;
    min-width: 0;
    padding: 4px 7px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    font: inherit;
    font-size: 12px;
  }
  .text:focus {
    outline: none;
    border-color: var(--accent);
  }
  input:disabled,
  .text:disabled {
    opacity: 0.5;
  }
</style>
