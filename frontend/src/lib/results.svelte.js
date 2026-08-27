// The live-results store: results/ghosts/run state, their mutations, and the
// SSE event appliers. The transport that feeds them lives in connection.js;
// this module owns WHAT changes, never WHEN. Folding phases → outcome is
// client-side and incremental via overallOutcome().
import { overallOutcome } from "./outcome.js";
import { setAnnotation, clearChannel } from "./annotations.svelte.js";
import { closeCoverage } from "./coverageView.svelte.js";

// nodeid -> { phases: {setup?,call?,teardown?}, warnings: [], duration }
// `running` is a sentinel set on `started` before any phase report lands.
export const results = $state({ byId: {} });

// F2: last-known records for tests removed by a reload, kept VERBATIM so the
// removed strip shows the exact badge (outcomeFor/resultFor fall back here).
export const ghosts = $state({ byId: {} });

// Run/console state.
export const run = $state({
  id: null, // current run_id, or null when idle
  active: false, // a run is in flight (set by the SSE `started` event)
  // `active` only flips on `started`, which arrives over SSE AFTER the
  // POST — so there's a window where a run is spawning but nothing reactive
  // says so. `pending` is the SYNCHRONOUS in-flight signal covering that gap:
  // set in markRunning (before the POST), cleared when the run goes live
  // (`started`), provably never will (4xx reject / server down), or ends with
  // `started` lost (terminal events + the R4 unstick). App's collect-debounce
  // guard reads `pending || active` so a re-collect can never race a live run.
  pending: false,
  status: "", // human-readable status line
  level: "info", // "info" | "warn" | "error" — drives status styling
  console: "", // accumulated pytest pty output (raw ANSI)
  k: null, // effective -k name filter echoed by `started`
  m: null, // effective -m marker expr echoed by `started`
  reports: 0, // count of `report` events seen this run (exit-5 detection)
  serverDown: false, // hard banner: server confirmed unreachable (R3 hard path)
  reconnecting: false, // soft banner: SSE dropped mid-run, chips preserved (R3)
  pluginData: {}, // run-level plugin payloads, keyed by plugin id (P16)
  pluginEmpty: {}, // plugins ENABLED this run that reported no data (id → true)
  pluginEmptyReason: {}, // optional plugin_empty reason strings (id → text)
  pluginRender: {}, // generic json/text render payloads, keyed by plugin id
  artifacts: {}, // per-test artifact file lists, keyed by nodeid
  artifactsRunId: null, // the run_id the current `artifacts` came from
  pluginMeta: null, // pytest-metadata's environment dict (render "metadata")
});

// Annotation channels onPluginData has written (channel key = plugin id,
// P16). Tracked here so a new run / reload invalidates exactly the plugin
// channels without importing the plugins store (panel state and run data are
// separate concerns) and without touching "diff".
const pluginChannels = new Set();

// A missing plugin_data event means NO data this run (e.g. --no-cov), and a
// reload means the numbers describe old code — stale coverage is worse than
// nothing either way. Called at markRunning time, on `started` (covers runs
// this tab didn't start), and from the collect-reload reconciliation.
export function clearPluginData() {
  for (const ch of pluginChannels) clearChannel(ch);
  pluginChannels.clear();
  run.pluginData = {};
  // A stale "no data collected" note after a new run is as wrong as
  // stale data — same lifecycle, cleared on the same triggers.
  run.pluginEmpty = {};
  run.pluginEmptyReason = {}; // Reasons ride the same lifecycle
  // Generic render sections are per-run output — stale on a new run too.
  run.pluginRender = {};
  // Per-test artifacts belong to the run that produced them — a new run
  // replaces the artifact tmpdir (the <img> src would 404), so clear them too.
  run.artifacts = {};
  run.artifactsRunId = null;
  // The environment dict describes the run that produced it — same lifecycle.
  run.pluginMeta = null;
  // An open coverage source view is stale once a new run replaces the
  // coverage tmpdir (its file would 404) — close it back to the run summary.
  closeCoverage();
}

// --- derivation -----------------------------------------------------------

// The display outcome for a nodeid:
//   "running" | "server-down" | overallOutcome | "missing" | null.
// Falls back to ghosts (removed-but-retained) so the removed strip renders.
export function outcomeFor(nodeid) {
  const r = results.byId[nodeid] || ghosts.byId[nodeid];
  if (!r) return null;
  if (r.running) return "running";
  if (r.serverDown) return "server-down";
  if (r.missing) return "missing";
  return overallOutcome(r.phases);
}

export function resultFor(nodeid) {
  return results.byId[nodeid] || ghosts.byId[nodeid] || null;
}

// The nodeids whose CURRENT result folds to failed/error — the
// re-run-failed set. Those two outcomes only (not incomplete/xpassed: the
// button re-runs what the user must fix, not what never finished); live
// results only, never ghosts (a removed test's nodeid is stale argv); and a
// record still carrying an in-flight/infra flag is skipped — same precedence
// as outcomeFor (running/server-down/missing shadow the phases).
export function failedNodeids() {
  const out = [];
  for (const [id, r] of Object.entries(results.byId)) {
    if (r.running || r.serverDown || r.missing) continue;
    const o = overallOutcome(r.phases);
    if (o === "failed" || o === "error") out.push(id);
  }
  return out;
}

// The single "a run is in flight" gate for the header run buttons —
// covers the synchronous markRunning→`started` gap (`pending`) as well as the
// live run (`active`), so a double-click on ▶ Run can't double-POST before
// `started` lands (benign server-side — kill-and-replace under _lock — but
// the gate is correct belt-and-braces).
export function runInFlight() {
  return run.active || run.pending;
}

// --- mutations ------------------------------------------------------------

function ensureResult(nodeid) {
  let r = results.byId[nodeid];
  if (!r) {
    r = { phases: {}, warnings: [], duration: null };
    results.byId[nodeid] = r;
  }
  return r;
}

// Delete every `running` sentinel; `also(rec)` runs on each record that had one.
function clearRunning(also) {
  for (const id of Object.keys(results.byId)) {
    const r = results.byId[id];
    if (r.running) {
      delete r.running;
      if (also) also(r);
    }
  }
}

// Caller (App) marks the selected nodeids running before the run starts, so the
// UI reflects in-flight state immediately and `finished` can detect non-runs.
export function markRunning(nodeids) {
  run.pending = true; // Synchronous — covers the gap until `started` lands
  clearPluginData(); // A new run invalidates the previous run's plugin data
  for (const id of nodeids) {
    results.byId[id] = {
      phases: {},
      warnings: [],
      duration: null,
      running: true,
    };
  }
}

// R3 hard path (idle only): server confirmed unreachable with no run streaming
// to lose — clear optimistic `running` chips, flag them serverDown, red banner.
export function markServerDown() {
  clearRunning((r) => (r.serverDown = true));
  run.active = false;
  run.pending = false; // the pending run never went (or is no longer) live
  run.serverDown = true;
  run.reconnecting = false;
  run.level = "error";
  run.status =
    "server unreachable: is it still running? (restart it and re-run)";
}

// POST /api/run answered a 4xx — the server is alive and REJECTED the run
// before it started (bad plugin config / extra args). NOT server-down: roll
// back the optimistic chips (nothing will ever report for them) and put the
// backend's message on the status line. run.active never went true (no
// `started` can arrive for a rejected run), so it is left alone.
// Also the surface for a collect 4xx (unknown/disabled ?plugins= id) —
// same "alive and rejecting" class, same status-line treatment; clearRunning
// is a no-op there (no chips were marked).
export function markRunRejected(message) {
  clearServerDown(); // an answer is proof of life (R6)
  clearRunning();
  run.pending = false; // nothing pending will ever go live
  run.level = "error";
  run.status = message || "run rejected";
}

// R3 soft path (mid-run): SSE has no replay (B9), so tearing down a live run's
// chips would be irrecoverable — banner only, touch nothing.
export function markReconnecting() {
  run.reconnecting = true;
  run.level = "warn";
  run.status = "connection lost, reconnecting…";
}

// R4: end a run locally whose terminal event was lost during an SSE gap, so
// the dashboard doesn't lock (App gates Collect/Run/filters on run.active).
export function unstickOrphanedRun() {
  run.active = false;
  run.pending = false; // whatever was pending is over too — never wedge collect
  clearRunning();
}

// R6: serverDown is an infra flag, not an outcome — clear it globally on any
// proof of life, or it sticks magenta forever and leaks into ghosts.
export function clearServerDown() {
  for (const id of Object.keys(results.byId))
    delete results.byId[id].serverDown;
  run.serverDown = false;
  run.reconnecting = false; // any "server alive" signal clears the soft state too
}

export function clearResults() {
  results.byId = {};
  ghosts.byId = {};
  run.console = "";
  run.status = "";
  run.level = "info";
  run.k = null;
  run.m = null;
  run.reports = 0;
}

// F2: keep each surviving record VERBATIM (never flatten to a scalar — must
// stay list-of-attempts-ready); removed-with-results records ghost verbatim.
// Returns the dropped ids that had results (for the removed strip).
export function reconcileResults(survivingIds) {
  // R6: a reload proves the server is alive, so lingering flags are stale.
  clearServerDown();
  const next = {};
  const nextGhosts = {};
  const droppedWithResults = [];
  for (const [id, rec] of Object.entries(results.byId)) {
    if (survivingIds.has(id)) {
      next[id] = rec; // verbatim
    } else {
      nextGhosts[id] = rec;
      droppedWithResults.push(id);
    }
  }
  results.byId = next;
  ghosts.byId = nextGhosts; // fresh ghost set each reload
  return droppedWithResults;
}

// --- SSE handling ---------------------------------------------------------

// pytest accumulates captured-output sections on the ITEM across its phases
// (item._report_sections), so the call/teardown reports re-carry every earlier
// phase's sections VERBATIM — rendering each phase's list as-is echoed the
// same captured stdout two or three times in the detail pane. Keep only the
// sections not already stored by ANOTHER phase of this record (an identical
// title+content pair is the echo; genuinely distinct output differs at least
// in the title's phase suffix, e.g. "Captured stdout call" vs "... teardown").
function ownSections(r, when, sections) {
  const seen = new Set();
  for (const [w, ph] of Object.entries(r.phases)) {
    if (w === when) continue; // a re-reported phase keeps its own sections
    for (const s of ph.sections || []) seen.add(JSON.stringify(s));
  }
  return (sections ?? []).filter((s) => !seen.has(JSON.stringify(s)));
}

export function onReport(d) {
  run.reports++;
  const r = ensureResult(d.nodeid);
  delete r.running;
  delete r.missing;
  delete r.serverDown;
  r.phases[d.when] = {
    outcome: d.outcome,
    wasxfail: d.wasxfail ?? null,
    longrepr: d.longrepr ?? null,
    sections: ownSections(r, d.when, d.sections),
    duration: d.duration,
  };
  if (d.duration != null) r.duration = (r.duration || 0) + d.duration;
}

export function onWarning(d) {
  const r = ensureResult(d.nodeid);
  r.warnings.push({
    category: d.category,
    message: d.message,
    filename: d.filename,
    lineno: d.lineno,
  });
}

// Build a "running: N selected, -k 'x', -m 'y'" phrase from a started event.
function runningPhrase(d) {
  const n = d.nodeids ? d.nodeids.length : 0;
  const parts = [n > 0 ? `${n} selected` : "all tests"];
  if (d.k) parts.push(`-k '${d.k}'`);
  if (d.m) parts.push(`-m '${d.m}'`);
  return `running: ${parts.join(", ")}…`;
}

export function onStarted(d) {
  clearServerDown(); // R6: any event is proof of life
  clearPluginData(); // Authoritative — covers runs this tab didn't start
  run.id = d.run_id;
  run.active = true;
  run.pending = false; // the run is live — `active` covers it from here
  run.console = "";
  run.level = "info";
  run.k = d.k ?? null;
  run.m = d.m ?? null;
  run.reports = 0;
  run.status = runningPhrase(d);
}

export function onConsole(d) {
  run.console += d.text || "";
}

export function onFinished(d) {
  run.active = false;
  run.pending = false; // covers a run whose `started` was lost in an SSE gap
  clearServerDown(); // R6
  // Exit 5 (nothing matched) is benign: clear sentinels but don't mark missing.
  const noMatch = d.exit_code === 5 && run.reports === 0;
  clearRunning((r) => {
    if (!noMatch) r.missing = true;
  });
  if (noMatch) {
    run.level = "info";
    run.status = "no tests matched the selection / filter";
  } else {
    run.level = "info";
    run.status = `run finished (exit ${d.exit_code})`;
  }
}

export function onCancelled(d) {
  run.active = false;
  run.pending = false; // same lost-`started` cover as onFinished
  clearServerDown(); // R6
  // Tests still running when cancelled stay incomplete — never silent pass.
  clearRunning();
  run.level = "warn";
  run.status = `run cancelled (${d.reason})`;
}

// Per-plugin run payload, broadcast after the child exits (before
// `finished`). An EXPLICIT switch on the `render` discriminator (P18) —
// each branch owns one wire shape, and an UNKNOWN render value is ignored
// entirely (P10 spirit: parse-by-known-key), never routed into a first-party
// path. Channels written onto the annotation store are tracked in
// pluginChannels so the new-run/reload lifecycle (clearPluginData) stays
// uniform.
export function onPluginData(d) {
  if (!d.plugin) return;
  switch (d.render ?? null) {
    // render:"artifacts" carries a per-test file map (nodeid -> [{name,
    // rel_path, kind}]) feeding the detail-pane Attachments block. Stamp the
    // producing run_id alongside so the pane can guard against showing an
    // older run's artifacts for a test whose result is from a newer run.
    case "artifacts":
      run.artifacts = d.data ?? {};
      run.artifactsRunId = d.run_id ?? null;
      return;
    // pytest-metadata's run-level environment dict (the run panel's
    // Environment section).
    case "metadata":
      run.pluginMeta = d.data ?? null;
      return;
    // pytest-benchmark — the deck's first TEST-keyed plugin data:
    // {summary, tests: {nodeid: stats}}. The whole payload lands run-level
    // (the run-panel line + the tree-column gate read summary); each per-test
    // stats record mirrors onto the plugin-id annotation channel (P16) — the
    // reserved slot the DiffBadge design anticipated — so the tree column and
    // detail pane read it per nodeid, invalidated by clearPluginData.
    case "benchmark": {
      pluginChannels.add(d.plugin);
      run.pluginData = { ...run.pluginData, [d.plugin]: d.data ?? {} };
      const tests = (d.data && d.data.tests) || {};
      for (const [nodeid, stats] of Object.entries(tests))
        setAnnotation(nodeid, d.plugin, stats);
      return;
    }
    // Generic render (json/text) — stash the raw payload for the
    // run-panel section. `truncated` flags a size-capped payload; a json
    // payload over the cap arrives as the sentinel {_truncated:true, bytes:n}
    // (NOT parsed).
    case "json":
    case "text":
      run.pluginRender = {
        ...run.pluginRender,
        [d.plugin]: {
          render: d.render,
          data: d.data,
          truncated: !!d.truncated,
        },
      };
      return;
    // The coverage shape ({total, files}). `null` is the legacy
    // no-`render` event (older backend / the early tests) — same path, so
    // those stay green. The per-file map also mirrors onto the plugin-id
    // channel — but coverage keys SOURCE files, which are NOT tree nodeids,
    // so they render as a dedicated source panel, not a tree column.
    case "coverage":
    case null: {
      pluginChannels.add(d.plugin);
      run.pluginData = { ...run.pluginData, [d.plugin]: d.data ?? {} };
      const files = (d.data && d.data.files) || {};
      for (const [path, value] of Object.entries(files))
        setAnnotation(path, d.plugin, value);
      return;
    }
    default:
      return; // unknown render — a newer backend's shape, ignore (P10)
  }
}

// A `plugin_empty` event — the plugin was ENABLED and declared a
// transport but produced no usable data (empty --cov target, or --no-cov via
// extra args). Mutually exclusive with `plugin_data` per plugin per run, so a
// note can explain the empty panel instead of it reading as broken. An
// optional `reason` (e.g. the runner's 32 MiB slimmer cap) is kept so the
// note can say WHY instead of the generic hint.
export function onPluginEmpty(d) {
  if (!d.plugin) return;
  run.pluginEmpty = { ...run.pluginEmpty, [d.plugin]: true };
  if (d.reason)
    run.pluginEmptyReason = { ...run.pluginEmptyReason, [d.plugin]: d.reason };
}

// --- coverage render selectors (first curated render; components stay thin)

export function coverageTotal() {
  const cov = run.pluginData["pytest_cov"];
  return cov && typeof cov.total === "number" ? cov.total : null;
}

// Did coverage run enabled-but-empty this run? Drives the panel note.
export function coverageEmpty() {
  return run.pluginEmpty["pytest_cov"] === true;
}

// The measured SOURCE files with their line-coverage %, as the slimmer emits
// them ({relpath: pct}). Sorted by pct ASCENDING so the worst-covered files
// surface first — the actionable ordering for a coverage panel; ties break on
// path. Null when there's no coverage data this run (panel hidden; lifecycle
// via clearPluginData).
export function coverageFiles() {
  const cov = run.pluginData["pytest_cov"];
  const files = cov && cov.files;
  if (!files) return null;
  const rows = Object.entries(files).map(([path, pct]) => ({ path, pct }));
  if (rows.length === 0) return null;
  rows.sort((a, b) => a.pct - b.pct || a.path.localeCompare(b.path));
  return rows;
}

// --- metadata render selectors (run panel Environment section) -------------

// pytest-metadata's slimmed environment dict for this run ({key: string|dict}),
// or null when the last run produced none (section hidden; lifecycle via
// clearPluginData).
export function metadataInfo() {
  return run.pluginMeta;
}

// Same hint pattern: metadata was enabled this run but no record arrived.
export function metadataEmpty() {
  return run.pluginEmpty["metadata"] === true;
}

// The optional reason behind a plugin_empty (runner's 32 MiB slimmer cap),
// or null — callers fall back to their generic hint then.
export function pluginEmptyReason(id) {
  return run.pluginEmptyReason[id] ?? null;
}

// --- benchmark render selectors (tree column + run panel line) -------------

// The whole benchmark payload for this run ({summary, tests}), or null when the
// last run produced none. Non-null is THE gate for the tree's mean column — no
// permanent column on non-benchmark suites (lifecycle via clearPluginData).
export function benchmarkData() {
  return run.pluginData["benchmark"] ?? null;
}

// The run-level summary ({count, fastest: {nodeid, mean}, slowest: {...}}), or
// null. Drives the run panel's one compact line.
export function benchmarkSummary() {
  const b = run.pluginData["benchmark"];
  return (b && b.summary) || null;
}

// Same hint pattern: benchmark was enabled this run but produced no records
// (zero benchmarks selected, --benchmark-disable, or a save-file failure).
export function benchmarkEmpty() {
  return run.pluginEmpty["benchmark"] === true;
}

// Human time for benchmark stats (pytest-benchmark reports SECONDS):
// auto-scaled ns/µs/ms/s. Non-numbers → "" so a schema-drifted record renders
// blank, not "NaN".
export function humanTime(s) {
  if (typeof s !== "number" || !isFinite(s) || s < 0) return "";
  if (s < 1e-6) return `${(s * 1e9).toFixed(1)} ns`;
  if (s < 1e-3) return `${(s * 1e6).toFixed(1)} µs`;
  if (s < 1) return `${(s * 1e3).toFixed(1)} ms`;
  return `${s.toFixed(2)} s`;
}

// Generic render sections (json/text plugins), one per plugin, sorted by
// plugin id for stable ordering. Each: {plugin, render, data, truncated}. A
// json payload over the size cap arrives as the sentinel {_truncated:true,
// bytes:n} (unparsed) — components detect it via jsonTruncatedBytes below.
// Empty → [] (no sections rendered).
export function renderSections() {
  return Object.entries(run.pluginRender)
    .map(([plugin, r]) => ({ plugin, ...r }))
    .sort((a, b) => a.plugin.localeCompare(b.plugin));
}

// The oversize-json sentinel byte count, or null if `data` is a real payload.
export function jsonTruncatedBytes(data) {
  if (data && typeof data === "object" && data._truncated === true)
    return typeof data.bytes === "number" ? data.bytes : 0;
  return null;
}

// The oversize-json sentinel's top-level key names (so the "too large" note can
// name what is big, e.g. pytest-benchmark's `benchmarks`), or [] if none.
export function jsonTruncatedKeys(data) {
  if (data && typeof data === "object" && Array.isArray(data.keys))
    return data.keys;
  return [];
}

// Human-readable byte size (B / KB / MB) for the over-cap note.
export function humanBytes(n) {
  if (typeof n !== "number" || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// --- artifacts (per-test attachments) --------------------------------------

// The artifact file list for a nodeid this run, or [] when none. Each entry is
// {name, rel_path, kind} (kind ∈ {"image","file"}) as emitted by the runner —
// components read them directly and build the byte URL via artifactUrl().
export function artifactsFor(nodeid) {
  const files = run.artifacts[nodeid];
  return Array.isArray(files) ? files : [];
}

// The URL to fetch one artifact's bytes: GET /api/artifacts/<run_id>/<rel_path>.
// rel_path is a POSIX path whose SEGMENTS may contain `[`,`]`,`.` etc. (mpl's
// parametrized dir names). Encode each `/`-split segment on its own, then
// rejoin with `/`: encoding the whole thing would escape the slashes (breaking
// the path), leaving it raw would break on the brackets. Empty segments (from a
// leading/duplicate slash) are preserved so the rejoined path is faithful.
export function artifactUrl(runId, relPath) {
  const path = String(relPath)
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/api/artifacts/${encodeURIComponent(runId)}/${path}`;
}

export function onError(d) {
  clearServerDown(); // R6
  // A `fatal: false` error (fd-3 overrun) is informational — the run is
  // still going and `finished` will arrive; don't end the run or clear chips.
  if (d.fatal === false) {
    run.level = "error";
    run.status = `run error: ${d.message || "unknown"}`;
    return;
  }
  run.active = false;
  run.pending = false; // fatal = the run is over, lost `started` or not
  clearRunning();
  run.level = "error";
  // Exit 4 = usage error (invalid -k/-m or stale nodeid); detail is on the console.
  if (d.exit_code === 4) {
    run.status =
      (d.message || "invalid run (pytest usage error)") +
      ". See the run console for pytest's message";
  } else {
    run.status = `run error: ${d.message || "unknown"}`;
  }
}
