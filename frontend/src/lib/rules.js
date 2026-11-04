// Terminal fill lines rendered as rules.
//
// pytest pads its section banners ("===== test session starts =====",
// "===== 3 failed, 9 passed in 0.4s =====") and traceback frame separators
// ("_ _ _ _ _ _") out to the terminal width. The runner's pty is a fixed 120
// columns while the pane that shows the text is usually narrower, so those
// lines fold into two or three rows of padding. The padding carries no
// information: this module keeps the words and their colours and draws the
// fill as a rule that fits the pane instead. Presentation only: the copy
// button still copies pytest's raw text.
import { ansiToHtml, stripAnsi } from "./ansi.js";

// eslint-disable-next-line no-control-regex -- parsing ANSI requires matching ESC
const SGR = /\x1b\[([0-9;]*)m/g;

// Fewer fill characters than this is a stray "---", not terminal padding.
const MIN_FILL = 6;

// Recognise a fill line. `line` may carry ANSI codes. Returns
// {char, label} for "=== label ===" / "_ _ _ _" shapes, else null. A
// one-sided "--- heading" is a heading, not padding, and stays text.
export function parseRule(line) {
  const s = stripAnsi(line).trim();
  const c = s[0];
  if (c !== "=" && c !== "-" && c !== "_") return null;
  // A run of the fill character, allowing the single spaces of "_ _ _".
  const lead = new RegExp(`^${c}(?: ?${c})*`).exec(s)[0];
  const rest = s.slice(lead.length);
  const tail = new RegExp(`(?:^|\\s)(${c}(?: ?${c})*)$`).exec(rest);
  const label = (tail ? rest.slice(0, tail.index) : rest).trim();
  if (label && !tail) return null;
  const fill = countFill(lead, c) + (tail ? countFill(tail[1], c) : 0);
  if (fill < MIN_FILL) return null;
  return { char: c, label };
}

function countFill(run, c) {
  let n = 0;
  for (const ch of run) if (ch === c) n++;
  return n;
}

// The raw (still ANSI-coded) text of just the label: every SGR sequence of the
// line in order, and only the text characters inside the label. Running the
// result through ansiToHtml paints the label as pytest did.
function rawLabel(rawLine, label) {
  const start = stripAnsi(rawLine).indexOf(label);
  const end = start + label.length;
  let out = "";
  let seen = 0; // text characters consumed so far (stripped index)
  let last = 0;
  SGR.lastIndex = 0;
  for (let m = SGR.exec(rawLine); m; m = SGR.exec(rawLine)) {
    const text = rawLine.slice(last, m.index);
    out += within(text, seen, start, end);
    seen += text.length;
    out += m[0];
    last = m.index + m[0].length;
  }
  out += within(rawLine.slice(last), seen, start, end);
  return out;
}

function within(text, offset, start, end) {
  const a = Math.max(start - offset, 0);
  const b = Math.min(end - offset, text.length);
  return b > a ? text.slice(a, b) : "";
}

// SGR state left open by earlier lines. pygments can colour a multi-line
// token with one code before and one reset after, so each line is rendered
// with the still-active codes prepended. A reset (ESC[0m, ESC[m) clears it.
function carry(active, line) {
  SGR.lastIndex = 0;
  for (let m = SGR.exec(line); m; m = SGR.exec(line)) {
    const isReset = m[1].split(";").every((p) => p === "" || /^0+$/.test(p));
    if (isReset) active.length = 0;
    else active.push(m[0]);
  }
}

const KIND = { "=": "section", "-": "dash", _: "frame" };

// Render an ANSI-coloured block as HTML for a <pre>: text lines through
// ansiToHtml (which escapes every text run), fill lines as rule spans. A rule
// is a block box, so the newline that ended its source line is dropped;
// keeping it would add an empty row under every rule.
export function renderBlock(raw) {
  const lines = String(raw ?? "").split("\n");
  const active = [];
  let out = "";
  lines.forEach((line, i) => {
    const prefix = active.join("");
    const rule = parseRule(line);
    if (rule) {
      out += ruleHtml(rule, prefix + line);
    } else {
      out += ansiToHtml(prefix + line);
      if (i < lines.length - 1) out += "\n";
    }
    carry(active, line);
  });
  return out;
}

function ruleHtml(rule, rawLine) {
  const cls = `rule rule-${KIND[rule.char]}`;
  if (!rule.label)
    return `<span class="${cls} rule-bare" role="separator"></span>`;
  const label = ansiToHtml(rawLabel(rawLine, rule.label));
  // Tint the line like its label, when pytest coloured it.
  const color = /color:(#[0-9a-f]{6})/.exec(label);
  const style = color ? ` style="--rule-color:${color[1]}"` : "";
  return (
    `<span class="${cls}" role="separator"${style}>` +
    `<span class="rule-label">${label}</span></span>`
  );
}
