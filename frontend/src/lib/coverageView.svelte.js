// The coverage source-gutter view state. Which file is open in the right
// pane, its fetched source + hit/miss line sets, and the loading/error state.
// Transport-free like the other stores (App/api layer does the fetch, calls
// openCoverage/failCoverage) so it stays node-shimmable; the pure
// line-classification is the flagship logic and lives here to be tested.
export const coverageView = $state({
  open: false, // is a coverage file showing in the right pane?
  path: null, // the file path being viewed
  loading: false, // fetch in flight
  error: null, // backend message on 404/stale, else null
  source: "", // full file text
  executed: [], // 1-based line numbers that ran (green)
  missing: [], // 1-based statement lines that did NOT run (red)
});

// Begin a fetch for `path` (marks loading; clears any prior file/error). The
// api layer awaits the request and then calls openCoverage or failCoverage.
export function startCoverageFetch(path) {
  coverageView.open = true;
  coverageView.path = path;
  coverageView.loading = true;
  coverageView.error = null;
  coverageView.source = "";
  coverageView.executed = [];
  coverageView.missing = [];
}

// A successful GET /api/coverage/<run>/<path> payload.
export function openCoverage(data) {
  coverageView.open = true;
  coverageView.loading = false;
  coverageView.error = null;
  coverageView.path = data.path ?? coverageView.path;
  coverageView.source = data.source ?? "";
  coverageView.executed = data.executed ?? [];
  coverageView.missing = data.missing ?? [];
}

// A 404/stale/unreadable response: keep the pane open showing the message.
export function failCoverage(message) {
  coverageView.loading = false;
  coverageView.error = message || "coverage unavailable";
  coverageView.source = "";
  coverageView.executed = [];
  coverageView.missing = [];
}

// Return to the run summary. Also called when a new run makes the file stale
// (the tmpdir is gone) — see the clearPluginData lifecycle wiring.
export function closeCoverage() {
  coverageView.open = false;
  coverageView.path = null;
  coverageView.loading = false;
  coverageView.error = null;
  coverageView.source = "";
  coverageView.executed = [];
  coverageView.missing = [];
}

// The flagship pure function: classify every source line as hit/miss/plain.
// `executed`/`missing` are 1-based line numbers (coverage.py's arcs). A line
// in `missing` is a statement that never ran (red); in `executed` it ran
// (green); in neither it's blank/comment/continuation — untinted ("plain").
// `missing` wins a (shouldn't-happen) overlap so a gap never reads as covered.
export function classifyLines(source, executed, missing) {
  const exec = new Set(executed || []);
  const miss = new Set(missing || []);
  // Split on \n; a trailing newline yields a final "" line we drop so the
  // gutter doesn't show a phantom line past EOF. Strip a trailing \r per
  // line so CRLF files don't leave a stray carriage return under white-space:pre.
  const lines = String(source ?? "").split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines.map((raw, i) => {
    const n = i + 1;
    const text = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    const status = miss.has(n) ? "miss" : exec.has(n) ? "hit" : "plain";
    return { n, text, status };
  });
}

// Count of missed statement lines — the header figure ("N lines missed").
// Count only misses that actually render, i.e. ≤ the source's line count.
// A `missing` line past EOF (source edited shorter since the run) would inflate
// the header past the visible red lines; clamp so the number matches the gutter.
export function missedCount() {
  const lineCount = classifyLines(coverageView.source, [], []).length;
  return coverageView.missing.filter((n) => n >= 1 && n <= lineCount).length;
}
