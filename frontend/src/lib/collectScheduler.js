// The debounced collect scheduler. Extracted from App.svelte so the
// guard decision is a testable unit: a burst of triggers (plugin-switch
// toggles, the ↻ Collect button, a future file watcher) coalesces through one
// trailing ~200ms timer, and the timer's FIRE is gated by `isBlocked()`.
//
// The bug this closes: the old inline timer checked only `busy`, so a
// collect-scoped toggle followed within the debounce window by ▶ Run had the
// timer fire DURING the live run — doCollect raced the run and swapped the
// tree mid-stream, bypassing exactly the guard that disables the Collect
// button. `isBlocked` must therefore consult the full gate (App wires it to
// `busy || run.active || run.pending` — `pending` covers the synchronous
// markRunning→`started` gap where `active` is still false).
//
// Deferred-collect semantics: a blocked fire is NEVER dropped — it re-arms and
// keeps re-checking every `delay` ms until the blocker clears, then fires
// once. So a toggle just before (or during) a run yields exactly one
// re-collect after the run ends, instead of a silently stale tree.
//
// `setTimer`/`clearTimer` are injectable so tests drive time deterministically.
export function makeCollectScheduler({
  delay = 200,
  isBlocked,
  fire,
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
}) {
  let timer = null;

  function tick() {
    timer = null;
    if (isBlocked()) {
      // Collect or run in flight — defer, don't drop: the latest request
      // still runs once the blocker clears.
      request();
      return;
    }
    fire();
  }

  function request() {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(tick, delay);
  }

  return { request };
}
