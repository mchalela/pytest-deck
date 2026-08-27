// Pane-divider geometry for the 3-column layout. Extracted from
// App.svelte (the collectScheduler.js pattern) so the clamp/persist/restore
// decisions are a testable unit; the pointer-event wiring stays in App.
//
// Model: the layout grid is `left | handle | middle(1fr) | handle | right`.
// Only `left` and `right` are stored (px); the middle takes the rest. Every
// clamp keeps all three columns at or above their minimum so no pane can be
// dragged (or restored) to zero — when the window itself is too small to
// honor all three minimums, the minimums win and the grid overflows
// horizontally rather than collapsing a pane.
//
// Persistence is FRACTIONS of the usable width (not px), so a stored layout
// scales sensibly across window sizes; reads and writes are wrapped in
// try/catch (private mode, quota, disabled storage) and any malformed stored
// value falls back to the defaults — rendering never depends on storage.

export const MIN_LEFT = 180; // sidebar: plugin config forms need real room
export const MIN_MIDDLE = 320; // tree pane
export const MIN_RIGHT = 280; // detail pane (matches its old min-width)
export const HANDLE_W = 6; // px per drag handle; must match the CSS columns

export const STORAGE_KEY = "pytest-deck.panes";

function clamp(v, lo, hi) {
  return Math.min(Math.max(v, lo), hi);
}

// The original layout: 260px sidebar, detail = 40% of what remains.
export function defaultPanes(total) {
  return clampPanes(260, (total - 260) * 0.4, total);
}

// Clamp both panes (restore / window-resize path): left first against the
// other two minimums, then right against the clamped left.
export function clampPanes(left, right, total) {
  const l = clamp(
    Math.round(left),
    MIN_LEFT,
    Math.max(MIN_LEFT, total - MIN_MIDDLE - MIN_RIGHT),
  );
  const r = clamp(
    Math.round(right),
    MIN_RIGHT,
    Math.max(MIN_RIGHT, total - l - MIN_MIDDLE),
  );
  return { left: l, right: r };
}

// Dragging the left↔middle divider: the RIGHT pane is not the user's target,
// so it stays fixed and the middle absorbs the change (down to its minimum).
export function resizeLeft(left, right, total) {
  const max = Math.max(MIN_LEFT, total - MIN_MIDDLE - right);
  return { left: clamp(Math.round(left), MIN_LEFT, max), right };
}

// Dragging the middle↔right divider: symmetric — left stays fixed.
export function resizeRight(left, right, total) {
  const max = Math.max(MIN_RIGHT, total - MIN_MIDDLE - left);
  return { left, right: clamp(Math.round(right), MIN_RIGHT, max) };
}

function isFrac(v) {
  return typeof v === "number" && isFinite(v) && v > 0 && v < 1;
}

// Restore from storage, or defaults on ANY failure (no storage, junk JSON,
// out-of-range fractions). Always returns a clamped {left, right}.
export function loadPanes(storage, total) {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return defaultPanes(total);
    const { left, right } = JSON.parse(raw);
    if (!isFrac(left) || !isFrac(right)) return defaultPanes(total);
    return clampPanes(left * total, right * total, total);
  } catch {
    return defaultPanes(total);
  }
}

// Persist as fractions of `total`. Failures are swallowed — resizing still
// works within the session, it just won't survive a reload.
export function savePanes(storage, panes, total) {
  if (!(total > 0)) return;
  try {
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        left: +(panes.left / total).toFixed(4),
        right: +(panes.right / total).toFixed(4),
      }),
    );
  } catch {
    /* private mode / quota / disabled storage */
  }
}
