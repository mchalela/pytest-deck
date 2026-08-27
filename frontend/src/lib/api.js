// Thin wrappers over the FastAPI endpoints. Selection state
// lives in the browser; these just talk to the backend.

export async function collect(pluginIds = null) {
  // Enabled collect-scoped plugin ids ride as ?plugins= (ids only — the
  // scope-split rule keeps fields/env run-only). Omitted when none are
  // enabled, so the request stays byte-identical to the legacy shape.
  const qs =
    pluginIds && pluginIds.length
      ? `?plugins=${encodeURIComponent(pluginIds.join(","))}`
      : "";
  const res = await fetch("/api/collect" + qs);
  const json = await res.json();
  if (!res.ok) {
    // Expose the HTTP status (as startRun does) so doCollect can tell a 400
    // validation reject (server alive, unknown/disabled ?plugins= id — keep
    // the tree, status-line message) from a 500 hard collect failure (the
    // subprocess broke — full "Collection failed" panel). A non-JSON body /
    // network failure throws above with no .status — that stays the hard path.
    const err = new Error(json.error || "collection failed");
    err.status = res.status;
    throw err;
  }
  return json; // {markers, tree, total, rootdir, errors: [{nodeid, path, longrepr_text}]}
}

// Classify a collect() throw for App's error routing.
//   "reject"     — server alive, 4xx validation answer (unknown/disabled
//                  ?plugins= id): keep the tree, status-line message only.
//   "subprocess" — the collect subprocess really failed (5xx with a JSON
//                  body): the full "Collection failed" panel, whose copy may
//                  claim parity with terminal pytest.
//   "network"    — the fetch itself threw or the body wasn't JSON (statusless
//                  throw: server down / proxy error page): pytest never ran,
//                  so the panel must NOT claim "same error as your terminal".
export function collectFailureKind(err) {
  if (err && typeof err.status === "number") {
    return err.status >= 400 && err.status < 500 ? "reject" : "subprocess";
  }
  return "network";
}

export async function startRun(
  nodeids,
  { k = null, m = null, plugins = null, extra_args = null } = {},
) {
  // The plugins/extra_args keys are OPTIONAL — omitted entirely when
  // unset, so the alpha request shape is unchanged unless the panel is used.
  const body = { nodeids, k, m };
  if (plugins != null) body.plugins = plugins;
  if (extra_args != null) body.extra_args = extra_args;
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if (!res.ok) {
    // A 4xx carries the backend's rejection message as {"error": ...}
    // (server.py's ManifestConfigError → 400). Expose the HTTP status so doRun
    // can tell "server alive but rejected the run" from "server down". A
    // non-JSON body throws out of res.json() above with no .status — that
    // path (and network failures) keeps the server-down handling.
    const err = new Error(json.error || "run failed");
    err.status = res.status;
    throw err;
  }
  return json; // {run_id}
}

export async function cancelRun() {
  const res = await fetch("/api/cancel", { method: "POST" });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || "cancel failed");
  return json; // {cancelled, run_id}
}

// Installed curated plugins for the left-bar panel. Throws on any non-OK
// response (incl. 404 from an older backend) — App hides the section then.
// Returns the whole payload — `plugins` plus `ini_leftovers` (the
// unclassified ini-addopts tokens the panel offers as extra-args suggestions).
export async function fetchPlugins() {
  const res = await fetch("/api/plugins");
  if (!res.ok) throw new Error("plugins fetch failed");
  const json = await res.json();
  return {
    plugins: json.plugins || [],
    ini_leftovers: json.ini_leftovers || [],
  };
}

// The coverage source + hit/miss line sets for one file of a run. Throws a
// tagged error on non-OK (incl. 404 when the run's coverage tmpdir is gone or
// the file isn't measured) carrying the backend's message + status for the
// view to show in place. The path is a rootdir-relative file path (as emitted
// by the coverage panel), URL-encoded so slashes survive as path segments.
export async function fetchCoverageFile(runId, path) {
  const res = await fetch(
    `/api/coverage/${encodeURIComponent(runId)}/${encodeURIComponent(path)}`,
  );
  const json = await res.json();
  if (!res.ok) {
    const err = new Error(json.error || json.detail || "coverage unavailable");
    err.status = res.status;
    throw err;
  }
  return json; // {path, source, executed, missing, excluded}
}

// Reconnect resync: is a run currently live server-side? Used after an SSE
// reconnect to detect a run whose `finished` event we missed during the gap, so
// the client can unstick a zombie run instead of locking the dashboard.
export async function runActive() {
  const res = await fetch("/api/run/active");
  const json = await res.json();
  return !!json.active;
}
