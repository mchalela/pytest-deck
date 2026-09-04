// The run-console filter. Extracted from RunConsole.svelte so the
// line-matching decisions are a testable unit: keep pytest's header (down to
// "collected N items") and its closing block, dropping the progress dump in
// between (prototype behaviour).
//
// The bug this closes: runs force --color=yes on a pty, so every structural
// line arrives ANSI-wrapped (`\x1b[…m===== 2 passed in 0.03s =====\x1b[0m`) —
// it neither starts nor ends with '=', so the old raw-buffer `/^=+.*=+$/`
// match NEVER fired and the pane rendered header-only. All matching therefore
// runs on an ANSI-STRIPPED copy while the returned slices stay raw, colours
// preserved (the console pane's "pytest's real output, warts included"
// contract). The kept tail also grew: it starts at the "short test summary
// info" section marker when present (the FAILED/ERROR one-liners), else it is
// just the final summary line — plus whatever pytest prints AFTER that line:
// on exit 4 (stale node ID) main() writes its UsageError ("ERROR: not found:
// …", "(no match in any of […])") below the "no tests ran" banner, and that is
// exactly the message the deck's status line points the user at.
//
// Three more shapes the cut must not lose: an INTERNALERROR> block (exit 3)
// sits between "collected" and the banner, so the tail starts at its first
// line; a run that ends with NO banner at all (a conftest ImportError chain,
// an argparse error for a bad extra arg) is all message, so once the run is
// over (`finished`) the whole buffer is returned instead of the header cut;
// and every line is read with terminal semantics — see terminalLine().
import { stripAnsi } from "./ansi.js";

// An SGR sequence at exactly lastIndex (sticky) — the family stripAnsi removes.
// eslint-disable-next-line no-control-regex -- parsing ANSI requires matching ESC
const SGR_AT = /\x1b\[[0-9;]*m/y;

// The state a run of SGR sequences leaves behind: nothing before the last full
// reset matters, and of each distinct sequence only its last occurrence does.
// Bounded by the number of distinct sequences, which keeps overlay() linear
// on a tqdm/rich-style line of thousands of coloured redraws joined by \r —
// the codes carried into one pass are collected again by the next.
function settled(codes) {
  const live = codes.slice(Math.max(codes.lastIndexOf("\x1b[0m"), 0));
  return [...new Set(live.reverse())].reverse().join("");
}

// Lay `next` over `prev` column by column, as a terminal does after a \r: the
// columns `next` covers are gone and the earlier text beyond its visible width
// survives. The SGR codes met in the covered stretch (zero width) go in front
// of `next`, settled, as the state it was printed under — so "collecting ...
// \rcollected N items" keeps its bold. Text is exact; attributes are
// approximate: that boundary state is applied from `next` on, so surviving
// text takes it rather than the state it was painted with, and pytest's
// "E   ValueError: line1\r\x1b[0m" renders plain where a terminal shows bold
// red. Width is approximate too, in ways pytest never exercises: non-SGR CSI
// (\x1b[K, cursor moves) counts as visible text, and it is measured in UTF-16
// code units rather than terminal columns.
function overlay(prev, next) {
  let cols = stripAnsi(next).length;
  const codes = [];
  let i = 0;
  while (i < prev.length) {
    SGR_AT.lastIndex = i;
    const m = SGR_AT.exec(prev);
    if (m) {
      codes.push(m[0]);
      i += m[0].length;
    } else if (cols > 0) {
      cols--;
      i++;
    } else {
      break;
    }
  }
  return settled(codes) + next + prev.slice(i);
}

// One buffer line (split on "\n") as a terminal shows it. A \r right before
// the line end is a no-op — the pty's \r\n, or \r\r\n when an exception
// message itself held CRLF, or a chunk cut between the \r and the \n — while
// each \r INSIDE the line returns the cursor to column 0, so what follows
// overlays what was there.
function terminalLine(line) {
  return line.replace(/\r+$/, "").split("\r").reduce(overlay);
}

// The closing "===== N passed in Xs =====" line (stripped copy). Plain
// startsWith/endsWith rather than /^=+.*=+$/: that regex backtracks
// super-quadratically on a long "=" run followed by anything else, and it ran
// on every line from the buffer end on every chunk — a test printing a long
// "=" rule froze the tab.
function isBanner(l) {
  return l.startsWith("=") && l.endsWith("=") && /\bin\b/.test(l);
}

// Last non-blank line (stripped copy) at or before `end`, never below
// `floor`: trailing empties and bare ESC[0m lines never reach the pane.
function lastNonBlank(plain, end, floor) {
  let i = end;
  while (i > floor && plain[i].trim() === "") i--;
  return i;
}

export function headerAndSummary(text, { finished = false } = {}) {
  const raw = String(text ?? "");
  const lines = raw.split("\n").map(terminalLine);
  const plain = lines.map(stripAnsi);

  let start = plain.findIndex((l) => l.includes("test session starts"));
  if (start < 0) start = 0;
  let collected = plain.findIndex(
    (l, i) => i >= start && /^collected /.test(l),
  );
  if (collected < 0) collected = Math.min(start + 5, lines.length - 1);

  // Where the kept tail starts: an INTERNALERROR> block from its first line
  // (the buffer's very first line when pytest_configure blew up — no session
  // header at all), else the closing banner, widened back to the "short test
  // summary info" marker when present.
  let tailStart = plain.findIndex(
    (l, i) => i >= start && l.startsWith("INTERNALERROR>"),
  );
  for (let i = lines.length - 1; tailStart < 0 && i > collected; i--) {
    if (isBanner(plain[i])) tailStart = i;
  }
  for (let i = tailStart - 1; i > collected; i--) {
    if (plain[i].includes("short test summary info")) {
      tailStart = i;
      break;
    }
  }

  const end = lastNonBlank(plain, lines.length - 1, 0);
  // No closing block: mid-run that is the header so far; once the run is over
  // (a conftest ImportError chain, an argparse error, a cancelled run) the
  // whole buffer IS the message.
  if (tailStart < 0 && finished) {
    return lines
      .slice(0, end + 1)
      .join("\n")
      .trimEnd();
  }
  // The header never runs into the tail (an INTERNALERROR> block can land
  // inside the fixed five-line fallback window, or open the buffer — then
  // there is no header, and no blank separator either).
  let headerEnd = collected;
  if (tailStart >= 0 && tailStart <= headerEnd) headerEnd = tailStart - 1;
  const header = lines
    .slice(start, lastNonBlank(plain, headerEnd, start) + 1)
    .join("\n");
  const tail = tailStart < 0 ? "" : lines.slice(tailStart, end + 1).join("\n");
  const out = [header, tail].filter(Boolean).join("\n\n").trimEnd();
  return out || raw.trimEnd();
}

// The raw (ANSI-preserving) remainder of `raw` after its first `n` visible
// characters. SGR sequences are zero width, as in overlay().
function rawAfterVisible(raw, n) {
  let i = 0;
  while (i < raw.length && n > 0) {
    SGR_AT.lastIndex = i;
    const m = SGR_AT.exec(raw);
    if (m) {
      i += m[0].length;
    } else {
      n--;
      i++;
    }
  }
  return raw.slice(i);
}

// A short-summary line that names a test: pytest's
// _get_line_with_reprcrash_message builds `f"{word} {nodeid}"` and appends
// " - {message}" when there is one (show_xfailed / show_xpassed do the same
// with the reason). SKIPPED lines carry `[n] path:line` instead of a nodeid
// and never match here.
const SUMMARY_ENTRY = /^(FAILED|ERROR|XFAIL|XPASS|PASSED) (\S.*)$/;

// Recognise one line (stripped copy) as an entry for a nodeid `known` accepts.
// The nodeid is normally the text before the first " - ", but a parametrize
// id may itself hold " - ", so each split point is tried left to right and
// the whole remainder last (a line with no message). `rest` is the raw slice
// after the nodeid (the reset that closes pytest's bold nodeid, then the
// message), colours kept for ansiToHtml.
function summaryEntry(raw, plain, known) {
  const m = SUMMARY_ENTRY.exec(plain);
  if (!m) return null;
  const [, word, remainder] = m;
  const candidates = [];
  let dash = remainder.indexOf(" - ");
  while (dash >= 0) {
    candidates.push(remainder.slice(0, dash));
    dash = remainder.indexOf(" - ", dash + 1);
  }
  candidates.push(remainder);
  const nodeid = candidates.find(known);
  if (nodeid === undefined) return null;
  const rest = rawAfterVisible(raw, word.length + 1 + nodeid.length);
  return { kind: "entry", nodeid, rest };
}

// Split headerAndSummary() output into render pieces: the lines of the
// "short test summary info" block (marker exclusive, closing banner
// exclusive) that name a test the store knows become
// {kind: "entry", nodeid, rest}; every other stretch of lines stays one
// {kind: "text", raw} piece, raw and contiguous, so the pane renders it
// exactly as before. `known(nodeid)` is injected (the results store, in the
// pane) so this module stays rune-free and testable under plain node. A
// buffer with no summary block is a single text piece.
export function summaryPieces(text, known) {
  const raw = String(text ?? "");
  const lines = raw.split("\n");
  const plain = lines.map(stripAnsi);
  const marker = plain.findIndex((l) => l.includes("short test summary info"));
  if (marker < 0) return [{ kind: "text", raw }];
  let end = plain.findIndex((l, i) => i > marker && isBanner(l));
  if (end < 0) end = lines.length;

  const pieces = [];
  let buf = [];
  const flush = () => {
    if (buf.length) pieces.push({ kind: "text", raw: buf.join("\n") });
    buf = [];
  };
  lines.forEach((line, i) => {
    const entry =
      i > marker && i < end ? summaryEntry(line, plain[i], known) : null;
    if (entry) {
      flush();
      pieces.push(entry);
    } else {
      buf.push(line);
    }
  });
  flush();
  return pieces;
}
