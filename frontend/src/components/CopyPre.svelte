<script>
  // A block of pytest output (ANSI coloured) with a copy control in its top
  // right corner. The clipboard gets the plain text: stripAnsi, never the
  // HTML and never the escape codes.
  import { onDestroy } from "svelte";
  import { stripAnsi } from "../lib/ansi.js";
  import { renderBlock } from "../lib/rules.js";

  let { text } = $props();
  let raw = $derived(text ?? "");

  // The button's label doubles as its state: "copy", then "copied" or
  // "failed" for a moment, then back.
  let label = $state("copy");
  let timer = null;

  function settle(next) {
    label = next;
    clearTimeout(timer);
    timer = setTimeout(() => (label = "copy"), 1500);
  }

  // navigator.clipboard exists only in a secure context. localhost counts,
  // so the deck normally has it; a LAN http://host:port does not, and the
  // legacy execCommand path through an off screen textarea still works there.
  function legacyCopy(plain) {
    const ta = document.createElement("textarea");
    ta.value = plain;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    let ok;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }

  async function copy() {
    const plain = stripAnsi(raw);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(plain);
        settle("copied");
        return;
      }
    } catch {
      // Permission denied or no focus: try the legacy path below.
    }
    settle(legacyCopy(plain) ? "copied" : "failed");
  }

  onDestroy(() => clearTimeout(timer));
</script>

<div class="copypre">
  <!-- renderBlock passes every text run through ansiToHtml, which
       HTML-escapes it (XSS guard); the only markup it adds itself is the
       rule spans for pytest's fill lines. Plain sections render identically. -->
  <!-- eslint-disable-next-line svelte/no-at-html-tags -->
  <pre>{@html renderBlock(raw)}</pre>
  <button
    class="mini copy"
    class:failed={label === "failed"}
    title="copy the plain text to the clipboard"
    onclick={copy}>{label}</button
  >
</div>

<style>
  .copypre {
    position: relative;
  }
  /* Lines keep their shape, as in a terminal: a line wider than the pane
     scrolls sideways instead of folding. pytest's width-padded separators are
     rendered as rules (lib/rules.js), so the scrollbar only appears for
     genuinely long lines such as a wide assertion message. */
  pre {
    margin: 0;
    white-space: pre;
    overflow-x: auto;
    font-size: 12px;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 10px;
    color: var(--fg);
  }
  /* Sits in the corner, quiet until the block is hovered so it does not
     compete with the traceback. */
  .copy {
    position: absolute;
    top: 6px;
    right: 6px;
    opacity: 0.6;
  }
  .copypre:hover .copy,
  .copy:focus-visible {
    opacity: 1;
  }
  .copy.failed {
    border-color: var(--st-failed-fg);
    color: var(--st-failed-fg);
  }
</style>
