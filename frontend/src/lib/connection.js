// SSE transport: the single EventSource, the server-down debounce, the
// reconnect self-heal, and the run-state resync (R1–R7). Store mutations live
// in results.svelte.js — this module decides WHEN to call them, never WHAT
// they do. Plain .js: declares no runes, only reads/mutates imported stores.
import { runActive } from "./api.js";
import {
  run,
  markServerDown,
  markReconnecting,
  unstickOrphanedRun,
  clearServerDown,
  onStarted,
  onReport,
  onWarning,
  onConsole,
  onFinished,
  onCancelled,
  onError,
  onPluginData,
  onPluginEmpty,
} from "./results.svelte.js";

let source = null;

// R7: the connection-level `error` event (no data) ALSO fires the named
// "error" listener — parse defensively and return null for data-less or
// malformed frames so appliers only ever see well-formed payloads.
export function parseEventData(data) {
  if (typeof data !== "string" || data === "") return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

// R1/R2: SSE-drop debounce. Must exceed the advertised retry (1s) AND the
// browser's ~3s no-retry-received fallback, or normal reconnects flash a banner.
const SERVER_DOWN_GRACE_MS = 5000;
let serverDownTimer = null;

// R4: bounded self-retry for the resync probe (no second `onopen` will come).
const RESYNC_RETRY_MS = 1000;
const RESYNC_MAX_RETRIES = 4;

// Open the long-lived SSE connection ONCE at page load (F1). A run started via
// POST /api/run streams its events here, tagged with run_id.
export function connectEvents() {
  if (source) return;
  source = new EventSource("/api/events");
  const handlers = {
    started: onStarted,
    report: onReport,
    warning: onWarning,
    console: onConsole,
    finished: onFinished,
    cancelled: onCancelled,
    error: onError,
    plugin_data: onPluginData,
    plugin_empty: onPluginEmpty,
  };
  for (const [name, fn] of Object.entries(handlers)) {
    source.addEventListener(name, (e) => {
      const payload = parseEventData(e.data); // R7 blip guard
      if (payload !== null) fn(payload);
    });
  }
  // R1/R7: the browser `error` event (connection-level, no data). A drop goes
  // to CONNECTING and retries forever — never CLOSED — so we debounce instead
  // of keying off readyState CLOSED. R3 picks hard vs soft by run.active.
  source.onerror = () => {
    if (serverDownTimer !== null) return; // already waiting to decide
    serverDownTimer = setTimeout(() => {
      serverDownTimer = null;
      if (source.readyState === EventSource.OPEN) return; // reconnect beat us → blip
      if (run.active) markReconnecting();
      else markServerDown();
    }, SERVER_DOWN_GRACE_MS);
  };
  // Fires on first connect AND every successful reconnect — the self-heal path.
  source.onopen = () => {
    if (serverDownTimer !== null) {
      clearTimeout(serverDownTimer);
      serverDownTimer = null;
    }
    // Capture BEFORE clearServerDown wipes the flag — it decides whether we
    // must resync a run whose terminal event may have been lost in the gap.
    const wasReconnecting = run.reconnecting;
    if (run.serverDown || run.reconnecting) {
      clearServerDown();
      run.level = "info";
      run.status = "reconnected: server is back";
    }
    if (wasReconnecting && run.active) resyncRunState();
  };
}

// R4/R5: probe /api/run/active after a mid-run gap; unstick if the run is over.
// Bounded self-retry, then FAIL OPEN — a wrong unstick self-corrects (SSE
// re-asserts a live run), a wrong lock does not.
function resyncRunState(attempt = 0) {
  // R5: pin the probed run so a stale answer can't clobber a newer run.
  const probedRunId = run.id;
  runActive()
    .then((active) => {
      if (active) return; // run genuinely still live
      if (run.active && run.id === probedRunId) {
        unstickOrphanedRun();
        run.level = "warn";
        run.status =
          "reconnected: the previous run finished while disconnected " +
          "(some results may be missing; re-run to refresh)";
      }
    })
    .catch(() => {
      // Re-check the run is still stuck on the SAME id before each retry, so a
      // probe that resolved another way (fresh started/report) doesn't respin.
      if (!run.active || run.id !== probedRunId) return;
      if (attempt + 1 < RESYNC_MAX_RETRIES) {
        setTimeout(() => {
          if (run.active && run.id === probedRunId) resyncRunState(attempt + 1);
        }, RESYNC_RETRY_MS);
      } else {
        // R4: retries exhausted → fail open, never locked.
        unstickOrphanedRun();
        run.level = "warn";
        run.status =
          "reconnected: could not confirm run state; reset it " +
          "(re-run to refresh)";
      }
    });
}
