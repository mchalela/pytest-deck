// Browser-held plugin-panel state. The manifest list comes from
// GET /api/plugins (fetched once at startup in App.svelte — this module is
// transport-free, like results.svelte.js: no api import here). Enabling a
// switch + editing its typed form is pure browser state; the backend compiles
// the runPayload() fragment to argv deterministically, extending the
// selection pattern.
export const plugins = $state({
  available: false, // /api/plugins fetch succeeded — the left-bar section renders at all
  // manifests as served: [{id, label, dist, scope, fields, render,
  // disabled_reason, ini_defaults}].
  // `render` ("json"|"text"|null) + `disabled_reason` (string|null) are new;
  // components read them straight off the manifest. A disabled manifest is shown
  // (greyed, inert) — distinct from not-installed (never in the list at all).
  // The `ini_defaults` map = harvested ini-addopts values seeding the form.
  list: [],
  byId: {}, // id -> { enabled: bool, values: {fieldKey: value} }
  extraArgs: "", // tier-2 escape hatch, passed through raw (server tokenizes)
  // Leftover ini-addopts tokens — suggestions rendered beside the
  // extra-args field; a click appends one there (never applied silently).
  suggestions: [],
});

// A manifest is inert if the backend gave a disabled_reason (e.g. rerunfailures
// "needs attempts model"). It renders but can't be toggled or sent in a run.
function isDisabled(manifest) {
  return manifest != null && manifest.disabled_reason != null;
}

// A field's initial value: the manifest default, else a type-appropriate zero.
function defaultFor(field) {
  if (field.default !== undefined && field.default !== null)
    return field.default;
  return field.type === "bool" ? false : "";
}

// A field's seed value with the pinned precedence — user-set > ini_default
// > manifest default. setPlugins resets ALL state (nothing is user-set at seed
// time), so the seed is ini_default-over-default; every later setValue is a
// user edit and simply overwrites — the ini can never resurrect a cleared
// field (the server excludes harvested tokens from re-admission too). A
// type-mismatched ini_default (defensive: server emits correct types) is
// ignored rather than corrupting the form.
function seedFor(field, iniDefaults) {
  const ini = iniDefaults ? iniDefaults[field.key] : undefined;
  const want = field.type === "bool" ? "boolean" : "string";
  if (ini !== undefined && typeof ini === want) return ini;
  return defaultFor(field);
}

// Install the fetched manifest list; every plugin starts disabled with its
// config values seeded from ini_defaults, then the field defaults (see
// seedFor). Re-installing resets state. `leftovers` are the unclassified
// ini-addopts tokens to offer as extra-args suggestions.
export function setPlugins(list, leftovers = []) {
  const byId = {};
  for (const p of list) {
    const values = {};
    for (const f of p.fields || []) values[f.key] = seedFor(f, p.ini_defaults);
    byId[p.id] = { enabled: false, values };
  }
  plugins.list = list;
  plugins.byId = byId;
  plugins.suggestions = [...leftovers];
  plugins.available = true;
}

export function setEnabled(id, on) {
  const s = plugins.byId[id];
  // A disabled manifest can never be enabled — the switch is inert in the UI,
  // and this guards the store path too.
  const manifest = plugins.list.find((p) => p.id === id);
  if (isDisabled(manifest)) return;
  if (s) s.enabled = !!on;
}

export function setValue(id, key, value) {
  const s = plugins.byId[id];
  if (s && key in s.values) s.values[key] = value;
}

// Does toggling this plugin's SWITCH change what collect sees? True only
// for an enable-able manifest declaring scope "both"/"collect" — its `-p <id>`
// token rides the collect subprocess, so the tree can change. Run-only
// manifests (and disabled ones) never trigger a re-collect; neither do field
// edits (fields are run-only by the scope-split rule).
export function affectsCollect(id) {
  const manifest = plugins.list.find((p) => p.id === id);
  if (!manifest || isDisabled(manifest)) return false;
  return manifest.scope === "both" || manifest.scope === "collect";
}

// The ?plugins= collect fragment: ids of ENABLED collect-scoped plugins, in
// list order. Ids only — config never rides collect.
export function collectPluginIds() {
  const ids = [];
  for (const p of plugins.list) {
    const s = plugins.byId[p.id];
    if (s && s.enabled && affectsCollect(p.id)) ids.push(p.id);
  }
  return ids;
}

// The optional run-request fragment: {plugins?, extra_args?}. A plugin id's
// PRESENCE means enabled (values are the current form state); disabled plugins
// are omitted entirely. Both keys are omitted when they'd be empty, so the
// request body is unchanged from alpha unless the panel is actually used.
export function runPayload() {
  const enabled = {};
  let any = false;
  for (const p of plugins.list) {
    if (isDisabled(p)) continue; // never send a disabled manifest
    const s = plugins.byId[p.id];
    if (s && s.enabled) {
      enabled[p.id] = { ...s.values };
      any = true;
    }
  }
  const out = {};
  if (any) out.plugins = enabled;
  const extra = plugins.extraArgs.trim();
  if (extra) out.extra_args = extra;
  return out;
}

// Quote one suggestion token for the extra-args field iff it needs it —
// the server re-tokenizes with shlex(posix), so a token carrying whitespace or
// quote chars (it was a quoted value in the user's ini) must survive the round
// trip as ONE token. Plain flags pass through untouched.
function quoteToken(token) {
  if (!/[\s"'\\]/.test(token)) return token;
  return '"' + token.replace(/[\\"]/g, (c) => "\\" + c) + '"';
}

// Apply the leftover-ini suggestion at position `index` — EXACTLY
// equivalent to the user typing it into extra-args (tier 2 is their
// decided-safe surface; no denylist here). Removal is BY INDEX, not by value:
// ini addopts can repeat a token (`-p a -p b` → two `-p` leftovers), and the
// panel keys each chip by index-composite, so the clicked chip must remove its
// own position — a value-based indexOf would drop the wrong duplicate.
export function applySuggestion(index) {
  if (index < 0 || index >= plugins.suggestions.length) return;
  const token = plugins.suggestions[index];
  plugins.suggestions = plugins.suggestions.filter((_, j) => j !== index);
  const extra = plugins.extraArgs.trim();
  plugins.extraArgs = extra
    ? extra + " " + quoteToken(token)
    : quoteToken(token);
}
