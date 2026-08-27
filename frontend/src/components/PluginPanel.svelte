<script>
  // The left-bar "Plugins" section: one PluginSwitch per installed
  // curated plugin, plus the tier-2 raw extra-args field below the list. App
  // only mounts this when /api/plugins succeeded, so a missing endpoint (older
  // backend) hides the whole section.
  import PluginSwitch from "./PluginSwitch.svelte";
  import { plugins, applySuggestion } from "../lib/plugins.svelte.js";

  let { disabled = false, oncollectchange = null } = $props();
</script>

{#if plugins.list.length}
  <div class="list">
    {#each plugins.list as p (p.id)}
      <PluginSwitch plugin={p} {disabled} {oncollectchange} />
    {/each}
  </div>
{:else}
  <span class="meta">none detected</span>
{/if}

<div class="extra">
  <label class="extra-label" for="extra-args">extra pytest args</label>
  <input
    id="extra-args"
    class="text"
    type="text"
    placeholder="e.g. -x --tb=short"
    title="passed through to pytest verbatim (split into tokens, not a shell command)"
    autocomplete="off"
    spellcheck="false"
    {disabled}
    bind:value={plugins.extraArgs}
  />
  <div class="hint">passed through raw</div>
  {#if plugins.suggestions.length}
    <!-- Leftover ini-addopts tokens the deck strips (P15) and can't route
         through a switch — offered here, applied only on click (equivalent to
         typing the token above), never silently. -->
    <div class="suggest">
      <span class="suggest-label">from your ini addopts:</span>
      <!-- Key by index-composite, not by value: ini addopts can repeat a token
           (`-p a -p b`), and a value key would collide (dev build: each_key
           duplicate throw; prod: mis-keyed reconciliation). applySuggestion
           removes BY INDEX so the clicked chip is the one that leaves. -->
      {#each plugins.suggestions as tok, i (i + "\u0000" + tok)}
        <button
          class="chip"
          {disabled}
          title={`your ini addopts also passes ${tok}. Click to add it to extra args`}
          onclick={() => applySuggestion(i)}>{tok}</button
        >
      {/each}
    </div>
  {/if}
</div>

<style>
  .list {
    display: flex;
    flex-direction: column;
  }
  .meta {
    color: var(--muted);
    font-size: 12px;
  }
  .extra {
    margin-top: 12px;
  }
  .extra-label {
    display: block;
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .text {
    width: 100%;
    padding: 6px 9px;
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
  .text:disabled {
    opacity: 0.5;
  }
  .hint {
    margin-top: 3px;
    font-size: 10px;
    color: var(--muted);
  }
  .suggest {
    margin-top: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
  }
  .suggest-label {
    flex-basis: 100%;
    font-size: 10px;
    color: var(--muted);
  }
  .chip {
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    font: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .chip:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
  .chip:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
