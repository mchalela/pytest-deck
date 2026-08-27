<script>
  // Group-level outcome rollup (e.g. "3✓ 1✗"). xfail counts as a benign pass;
  // xpass/incomplete count as problems — same tally as the prototype.
  import { outcomeFor } from "../lib/results.svelte.js";

  let { leaves } = $props();

  let counts = $derived.by(() => {
    let pass = 0,
      fail = 0,
      err = 0,
      skip = 0,
      any = false;
    for (const id of leaves) {
      const o = outcomeFor(id);
      if (!o || o === "running" || o === "missing" || o === "server-down")
        continue;
      switch (o) {
        case "passed":
        case "xfailed":
          pass++;
          any = true;
          break;
        case "failed":
        case "xpassed":
          fail++;
          any = true;
          break;
        case "error":
        case "incomplete":
          err++;
          any = true;
          break;
        case "skipped":
          skip++;
          any = true;
          break;
      }
    }
    return { pass, fail, err, skip, any };
  });
</script>

{#if counts.any}
  <span class="rollup">
    {#if counts.pass}<span class="p">{counts.pass}✓</span>{/if}
    {#if counts.fail}<span class="f">{counts.fail}✗</span>{/if}
    {#if counts.err}<span class="e">{counts.err}!</span>{/if}
    {#if counts.skip}<span class="s">{counts.skip}∅</span>{/if}
  </span>
{/if}

<style>
  /* gap replaces the old {" "} text separators between present counters */
  .rollup {
    flex: none;
    display: inline-flex;
    gap: 4px;
    font-size: 11px;
    margin-left: 6px;
  }
  .rollup .p {
    color: #5fd38a;
  }
  .rollup .f {
    color: #ff7a93;
  }
  .rollup .e {
    color: #ffb454;
  }
  .rollup .s {
    color: #8fb4e0;
  }
</style>
