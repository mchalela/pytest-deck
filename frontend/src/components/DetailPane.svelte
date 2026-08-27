<script>
  // The third column. When a test is pinned (ui.detailId), shows its native
  // per-phase tracebacks, captured sections, and warnings. Otherwise shows the
  // run-info console. Native renders (longrepr) already include captured output,
  // so sections are only surfaced for phases with no longrepr (prototype rule).
  import { ui } from "../lib/selection.svelte.js";
  import {
    resultFor,
    outcomeFor,
    artifactsFor,
    artifactUrl,
    humanTime,
    run,
  } from "../lib/results.svelte.js";
  import { annotationFor } from "../lib/annotations.svelte.js";
  import { collectErrorFor } from "../lib/collectErrors.svelte.js";
  import { ansiToHtml } from "../lib/ansi.js";
  import RunConsole from "./RunConsole.svelte";

  let { hasTree, onclose } = $props();

  let pinned = $derived(ui.detailId);
  // A pinned id can be a COLLECTION error (an erroring file) rather than a test
  // result. Those have no run outcome — just pytest's collect traceback (ANSI).
  let collectErr = $derived(pinned ? collectErrorFor(pinned) : null);
  let res = $derived(pinned ? resultFor(pinned) : null);
  let outcome = $derived(pinned ? outcomeFor(pinned) : null);

  let durText = $derived(
    res && res.duration != null
      ? ` · ${(res.duration * 1000).toFixed(1)} ms`
      : "",
  );

  function sectionsFor() {
    if (!res) return { list: [], hadDetail: false };
    const out = [];

    let hadDetail = false;
    for (const when of ["setup", "call", "teardown"]) {
      const ph = res.phases && res.phases[when];
      if (!ph) continue;
      if (ph.longrepr) {
        out.push({ title: `${when}: traceback`, text: ph.longrepr });
        hadDetail = true;
      } else {
        for (const s of ph.sections || []) {
          out.push({ title: `${when}: ${s.title}`, text: s.content });
          hadDetail = true;
        }
      }
    }

    if (res.warnings && res.warnings.length) {
      const text = res.warnings
        .map((w) => {
          const loc = w.filename ? ` (${w.filename}:${w.lineno})` : "";
          return `${w.category}: ${w.message}${loc}`;
        })
        .join("\n\n");
      out.push({ title: `Warnings (${res.warnings.length})`, text });
      hadDetail = true;
    }
    return { list: out, hadDetail };
  }

  let sections = $derived(
    pinned ? sectionsFor() : { list: [], hadDetail: false },
  );

  // Per-test attachments. Only surface the current run's artifacts (they're
  // cleared and re-populated per run) — if run.artifactsRunId doesn't match the
  // run we're tracking, the bytes would be for a different (or gone) tmpdir, so
  // show nothing rather than a mismatched/404 image. Split image/file so images
  // render inline (the mpl baseline/result/diff compare story) and other files
  // fall back to download links.
  let attachRunId = $derived(run.artifactsRunId);
  let attachments = $derived(
    pinned && attachRunId != null && attachRunId === run.id
      ? artifactsFor(pinned)
      : [],
  );
  let attachImages = $derived(attachments.filter((f) => f.kind === "image"));
  let attachFiles = $derived(attachments.filter((f) => f.kind !== "image"));

  // This test's benchmark stats off the plugin annotation channel (written
  // by onPluginData, cleared with the run's plugin data) — the full table the
  // tree column's mean summarizes. Times are seconds → humanTime; ops/rounds/
  // iterations are plain counts.
  let bench = $derived(pinned ? annotationFor(pinned, "benchmark") : null);
  const BENCH_TIME_ROWS = [
    ["min", "min"],
    ["max", "max"],
    ["mean", "mean"],
    ["stddev", "std dev"],
    ["median", "median"],
    ["iqr", "IQR"],
  ];

  function benchCount(v) {
    return typeof v === "number" && isFinite(v)
      ? Math.round(v).toLocaleString("en-US")
      : "";
  }
</script>

<div class="detailpane">
  <div class="detailhead">
    <span class="id" title={pinned || ""}>{pinned || "Run info"}</span>
    {#if pinned}
      <span class="close" title="back to run info" onclick={onclose}
        >✕ deselect</span
      >
    {/if}
  </div>
  <div class="detailbody">
    {#if !pinned}
      <RunConsole {hasTree} />
    {:else if collectErr != null}
      <!-- Collection error (pytest's ERRORS section). No run outcome, just the
           collect traceback — rendered ANSI-coloured like run tracebacks. -->
      <div class="outcome st-error">
        <span class="word">COLLECTION ERROR</span>
      </div>
      <h3>collect: traceback</h3>
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- ansiToHtml escapes all text runs -->
      <pre>{@html ansiToHtml(collectErr)}</pre>
    {:else if !res}
      <p class="ok">no result yet. Run this test.</p>
    {:else if outcome === "running"}
      <div class="outcome st-running">
        <span class="word">RUNNING</span>
      </div>
      <p class="ok">
        in flight. The result will appear here the moment a phase report lands.
      </p>
    {:else if outcome === "server-down"}
      <div class="outcome st-server-down">
        <span class="word">SERVER DOWN</span>
      </div>
      <p class="ok">
        the server became unreachable while this test was pending, so no result
        ever arrived. Restart the server (this tab reconnects on its own) and
        re-run.
      </p>
    {:else if outcome === "missing"}
      <div class="outcome st-missing">
        <span class="word">NOT FOUND</span>
      </div>
      <p class="ok">
        this test was selected but the run produced no report for it. Usually a
        -k / -m expression deselected it, or the test no longer exists. Adjust
        the filter and re-run, or ↻ Collect to refresh the tree.
      </p>
    {:else}
      <div class="outcome st-{outcome}">
        <span class="word">{(outcome || "").toUpperCase()}</span><span
          class="dur">{durText}</span
        >
      </div>
      {#each sections.list as s (s)}
        <h3>{s.title}</h3>
        <!-- ansiToHtml HTML-escapes every text run (XSS guard) and is a no-op on
             text with no ANSI escapes, so plain sections render identically. The
             traceback sections (ph.longrepr) arrive ANSI-coloured. -->
        <!-- eslint-disable-next-line svelte/no-at-html-tags -->
        <pre>{@html ansiToHtml(s.text)}</pre>
      {/each}
      {#if !sections.hadDetail && outcome === "passed"}
        <p class="ok">Passed with no captured output.</p>
      {/if}
      {#if bench}
        <h3>Benchmark</h3>
        <table class="benchtable">
          <tbody>
            {#each BENCH_TIME_ROWS as [key, label] (key)}
              <tr>
                <td class="bkey">{label}</td>
                <td class="bval">{humanTime(bench[key])}</td>
              </tr>
            {/each}
            <tr>
              <td class="bkey">ops/s</td>
              <td class="bval">{benchCount(bench.ops)}</td>
            </tr>
            <tr>
              <td class="bkey">rounds</td>
              <td class="bval">{benchCount(bench.rounds)}</td>
            </tr>
            <tr>
              <td class="bkey">iterations</td>
              <td class="bval">{benchCount(bench.iterations)}</td>
            </tr>
          </tbody>
        </table>
      {/if}
      {#if attachments.length}
        <h3>Attachments</h3>
        {#if attachImages.length}
          <div class="attach-images">
            {#each attachImages as f (f.rel_path)}
              <figure class="attach-fig">
                <!-- rel_path is server-controlled and goes into the src
                     ATTRIBUTE only (Svelte escapes attribute values); each
                     path segment is encodeURIComponent'd by artifactUrl. No
                     {@html} touches any artifact-derived string. -->
                <img
                  class="attach-img"
                  src={artifactUrl(attachRunId, f.rel_path)}
                  alt={f.name}
                  loading="lazy"
                />
                <figcaption>{f.name}</figcaption>
              </figure>
            {/each}
          </div>
        {/if}
        {#if attachFiles.length}
          <ul class="attach-files">
            {#each attachFiles as f (f.rel_path)}
              <li>
                <a href={artifactUrl(attachRunId, f.rel_path)} download
                  >{f.name}</a
                >
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    {/if}
  </div>
</div>

<style>
  /* Width comes from the layout grid's last column (App.svelte owns
     the draggable divider + clamps); this pane just fills its track. */
  .detailpane {
    min-width: 0;
    height: 100%;
    overflow: hidden;
    border-left: 1px solid var(--line);
    background: var(--panel);
    display: flex;
    flex-direction: column;
  }
  .detailhead {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
  }
  .detailhead .id {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
  }
  .detailhead .close {
    cursor: pointer;
    color: var(--muted);
    padding: 0 6px;
    font-size: 12px;
  }
  .detailhead .close:hover {
    color: var(--fg);
  }
  .detailbody {
    flex: 1 1 auto;
    overflow: auto;
    padding: 12px;
  }
  .detailbody h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 14px 0 6px;
  }
  .detailbody h3:first-child {
    margin-top: 0;
  }
  .detailbody pre {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 12px;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 10px;
    color: var(--fg);
  }
  .outcome {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin: 0 0 12px;
    color: var(--fg);
  }
  .outcome .word {
    text-transform: uppercase;
  }
  .outcome .dur {
    color: var(--muted);
    font-weight: 400;
  }
  .outcome.st-passed .word {
    color: var(--st-passed-fg);
  }
  .outcome.st-failed .word {
    color: var(--st-failed-fg);
  }
  .outcome.st-error .word {
    color: var(--st-error-fg);
  }
  .outcome.st-skipped .word {
    color: var(--st-skipped-fg);
  }
  .outcome.st-xfailed .word {
    color: var(--st-xfailed-fg);
  }
  .outcome.st-xpassed .word {
    color: var(--st-xpassed-fg);
  }
  .outcome.st-incomplete .word {
    color: var(--st-incomplete-fg);
  }
  .outcome.st-missing .word {
    color: var(--st-missing-fg);
  }
  .outcome.st-running .word {
    color: var(--st-running-fg);
  }
  .outcome.st-server-down .word {
    color: var(--st-serverdown-fg);
  }
  .ok {
    color: var(--muted);
  }
  /* Attachments — mpl baseline/result/diff read side by side; wrap so the
     images never overflow the pane. */
  .attach-images {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .attach-fig {
    margin: 0;
    flex: 1 1 180px;
    min-width: 0;
  }
  .attach-img {
    display: block;
    max-width: 100%;
    height: auto;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--bg);
  }
  .attach-fig figcaption {
    margin-top: 4px;
    font-size: 11px;
    color: var(--muted);
  }
  .attach-files {
    margin: 0;
    padding-left: 18px;
    font-size: 12px;
  }
  .attach-files a {
    color: var(--accent);
  }
  /* The full benchmark stats table (times auto-scaled, counts plain). */
  .benchtable {
    border-collapse: collapse;
    font-size: 12px;
  }
  .benchtable td {
    padding: 1px 0;
  }
  .benchtable .bkey {
    color: var(--muted);
    padding-right: 16px;
  }
  .benchtable .bval {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
</style>
