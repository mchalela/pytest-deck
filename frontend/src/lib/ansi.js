// Minimal ANSI SGR → HTML for the pty console and ANSI-coloured tracebacks.
// Hand-rolled: pytest + pygments emit only the basic 8/16-colour set (never
// 256/truecolor), so a full parser is dead weight. Handles reset/bold/underline,
// fg 30-37 + 90-97, 39 (clear fg), 49 (no-op), zero-padded and semicolon-joined
// params (e.g. pygments' ESC[39;49;00m); anything else is ignored gracefully.
// Every text run is HTML-escaped before any span is built (XSS guard).
// Rough token palette pytest/pygments paint: 31/91 E-lines & diff '-', 90
// comments, 94 keywords+numbers, 33 strings, 92 defs & diff '+', 96 builtins.

const FG = {
  // standard 30-37 (light-mode fallback)
  30: "#3b4252",
  31: "#e0697a",
  32: "#74c990",
  33: "#d8a657",
  34: "#5a93d6",
  35: "#c08be0",
  36: "#5fb3b3",
  37: "#9aa6b5",
  // bright 90-97 (what pytest/pygments emit on a dark terminal — the live palette)
  90: "#6b7686",
  91: "#ff7a93",
  92: "#7ee0a0",
  93: "#ffce54",
  94: "#6cb2ff",
  95: "#d9a8ff",
  96: "#5fd7d7",
  97: "#f0f4f8",
};

// Strip ANSI SGR sequences (the same ESC[...m family ansiToHtml parses) so
// line-oriented matching can run on plain text while the raw slice keeps its
// colours. pytest's closing "===== N passed in Xs =====" arrives
// ESC-wrapped from the --color=yes pty, so raw-buffer regexes never match it.
export function stripAnsi(text) {
  // eslint-disable-next-line no-control-regex -- parsing ANSI requires matching ESC
  return String(text ?? "").replace(/\x1b\[[0-9;]*m/g, "");
}

function esc(s) {
  return s.replace(
    /[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c],
  );
}

export function ansiToHtml(text) {
  if (text == null) return "";
  let out = "";
  let open = false;
  let color = null;
  let bold = false;
  let underline = false;

  // Close any open span at a code boundary; the new span is opened lazily, only
  // right before text is emitted (emitText), so back-to-back codes — e.g. pytest's
  // ESC[1m ESC[31m — never produce a stray empty span.
  const closeSpan = () => {
    if (open) {
      out += "</span>";
      open = false;
    }
  };
  const emitText = (raw) => {
    if (raw === "") return;
    if (!open && (color || bold || underline)) {
      const styles = [];
      if (color) styles.push(`color:${color}`);
      if (bold) styles.push("font-weight:600");
      if (underline) styles.push("text-decoration:underline");
      out += `<span style="${styles.join(";")}">`;
      open = true;
    }
    out += esc(raw); // esc() escapes every text run — the XSS guard.
  };

  // Split on the CSI SGR sequence ESC[...m, keeping the codes. The capture group
  // only matches digits/semicolons, so anything inside a span is always escaped.
  // eslint-disable-next-line no-control-regex -- parsing ANSI requires matching ESC
  const parts = String(text).split(/\x1b\[([0-9;]*)m/);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      emitText(parts[i]);
    } else {
      closeSpan();
      // parseInt drops leading zeros (e.g. "01" -> 1, "00" -> 0). Empty tokens
      // are skipped; a sequence with no usable tokens (ESC[m / ESC[;m) == reset.
      const codes = parts[i]
        .split(";")
        .filter((c) => c !== "")
        .map((c) => parseInt(c, 10));
      if (codes.length === 0) codes.push(0); // bare ESC[m == reset
      for (const code of codes) {
        if (code === 0) {
          color = null;
          bold = false;
          underline = false;
        } else if (code === 1) {
          bold = true;
        } else if (code === 22) {
          bold = false;
        } else if (code === 4) {
          underline = true;
        } else if (code === 24) {
          underline = false;
        } else if (FG[code]) {
          color = FG[code];
        } else if (code === 39 || code === 49) {
          // default fg / default bg — we don't paint backgrounds, so both just
          // clear any active colour.
          color = null;
        }
        // any other code (bg-set, inverse, blink, …) is ignored gracefully.
      }
      // span is (re)opened lazily by emitText on the next text run.
    }
  }
  closeSpan();
  return out;
}
