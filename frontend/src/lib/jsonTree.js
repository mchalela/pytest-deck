// Pure helpers for the collapsible JSON-tree renderer (JsonTree.svelte).
// Kept out of the component so the classification is node-shim testable and the
// component stays thin. No rendering here — just "what kind of value is this".

// Classify a JSON value for display: "object" | "array" | "string" |
// "number" | "boolean" | "null". Objects/arrays are expandable (branches);
// everything else is a leaf shown inline.
export function valueKind(v) {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  const t = typeof v;
  if (t === "object") return "object";
  if (t === "number") return "number";
  if (t === "boolean") return "boolean";
  return "string"; // string, and anything exotic renders as text
}

export function isBranch(v) {
  const k = valueKind(v);
  return k === "object" || k === "array";
}

// The child entries of a branch as [key, value] pairs (array indices become
// numeric keys). A non-branch has no entries.
export function entriesOf(v) {
  const k = valueKind(v);
  if (k === "array") return v.map((item, i) => [i, item]);
  if (k === "object") return Object.entries(v);
  return [];
}

// A one-line summary shown next to a collapsed branch: "{N}" / "[N]" with the
// child count, e.g. an empty object is "{}" and a 3-element array "[3]".
export function branchSummary(v) {
  const k = valueKind(v);
  if (k === "array") return v.length === 0 ? "[]" : `[${v.length}]`;
  if (k === "object") {
    const n = Object.keys(v).length;
    return n === 0 ? "{}" : `{${n}}`;
  }
  return "";
}

// The inline text for a leaf value: strings quoted, everything else stringified
// (null → "null", booleans/numbers as-is). Rendered as escaped text by the
// component — never HTML.
export function leafText(v) {
  const k = valueKind(v);
  if (k === "string") return JSON.stringify(v); // adds quotes + escapes
  if (k === "null") return "null";
  return String(v);
}
