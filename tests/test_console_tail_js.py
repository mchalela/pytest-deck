"""Tests for ``frontend/src/lib/consoleTail.js`` — the run-console filter.

Runs force ``--color=yes`` on a pty, so pytest's closing
``===== N passed in Xs =====`` line arrives ANSI-wrapped — it neither starts
nor ends with ``=`` and the old raw-buffer ``/^=+.*=+$/`` match never fired,
so the pane rendered header-only. ``headerAndSummary`` must match on an
ANSI-stripped copy while returning the RAW slices (colours preserved), and
keep the whole ``short test summary info`` section when present.

The companion runner test
(test_runner.py::test_console_raw_buffer_contains_ansi_wrapped_closing_block)
proves the raw buffer really carries this shape; these tests pin the filter
against a faithful fixture. Same node-shell pattern as test_ansi_js.py; skips
cleanly without ``node``.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_TAIL_JS = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "lib"
    / "consoleTail.js"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

_HARNESS = f"""
import {{ headerAndSummary }} from {json.dumps(_TAIL_JS.as_uri())};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  process.stdout.write(
    JSON.stringify(
      cases.map((c) =>
        typeof c === "string" ? headerAndSummary(c) : headerAndSummary(...c),
      ),
    ),
  );
}});
"""


def _finished(text):
    """A case run as the pane does once the run is over (``run.active`` false)."""
    return [text, {"finished": True}]


# summaryPieces: each case is [text, knownNodeids]; the pane's `known` is the
# results store, here a fixed list.
_PIECES_HARNESS = f"""
import {{ summaryPieces }} from {json.dumps(_TAIL_JS.as_uri())};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  process.stdout.write(
    JSON.stringify(
      cases.map(([text, known]) => summaryPieces(text, (id) => known.includes(id))),
    ),
  );
}});
"""


def _pieces(cases):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PIECES_HARNESS],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _convert(cases):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# Faithful colored fixtures: the shapes pytest emits under --color=yes on a
# pty (bold header banner, per-test progress dots, cyan short-summary marker,
# color-wrapped closing line), with the \r\n line endings a pty produces.
_HEADER = (
    "\x1b[1m========================= test session starts "
    "=========================\x1b[0m\r\n"
    "platform linux -- Python 3.13.1, pytest-8.4.2, pluggy-1.6.0\r\n"
    "rootdir: /tmp/suite\r\n"
    "collected 2 items\r\n"
)
_PROGRESS = (
    "\r\n"
    "test_suite.py \x1b[32m.\x1b[0m\x1b[31mF\x1b[0m\x1b[31m"
    "                  [100%]\x1b[0m\r\n"
    "\r\n"
    "=================================== FAILURES "
    "===================================\r\n"
    "\x1b[31m\x1b[1m_______________________________ test_c_fails "
    "_______________________________\x1b[0m\r\n"
    "\r\n"
    "    def test_c_fails():\r\n"
    "\x1b[1m\x1b[31mE       assert 1 == 2\x1b[0m\r\n"
    "\r\n"
)
_SHORT_SUMMARY = (
    "\x1b[36m\x1b[1m=========================== short test summary info "
    "===========================\x1b[0m\r\n"
    "\x1b[31mFAILED\x1b[0m test_suite.py::\x1b[1mtest_c_fails\x1b[0m"
    " - assert 1 == 2\r\n"
)
_CLOSING = (
    "\x1b[31m========================= \x1b[31m\x1b[1m1 failed\x1b[0m, "
    "\x1b[32m1 passed\x1b[0m\x1b[31m in 0.04s\x1b[0m\x1b[31m"
    " =========================\x1b[0m"
)
_COLORED_RUN = _HEADER + _PROGRESS + _SHORT_SUMMARY + _CLOSING + "\r\n"

_GREEN_CLOSING = (
    "\x1b[32m========================= \x1b[32m\x1b[1m2 passed\x1b[0m"
    "\x1b[32m in 0.03s\x1b[0m\x1b[32m =========================\x1b[0m"
)
_COLORED_ALL_PASS = (
    _HEADER
    + "\r\ntest_suite.py \x1b[32m..\x1b[0m\x1b[32m"
    + "                 [100%]\x1b[0m\r\n\r\n"
    + _GREEN_CLOSING
    + "\r\n"
)

# The collected line as a pty really carries it: pytest writes "collecting
# ... ", then a bare \r and "collected N items" padded to the terminal width.
# A terminal overwrites, so only the second text is ever visible.
_PTY_COLLECT = (
    "\x1b[1mcollecting ... \x1b[0m\x1b[1m\rcollected 2 items"
    "                                    \x1b[0m\r\n"
)
_PTY_ALL_PASS = _COLORED_ALL_PASS.replace("collected 2 items\r\n", _PTY_COLLECT)

# Exit 4 (a stale node ID): pytest's main() prints its UsageError after the
# closing banner. First a red "ERROR: not found: …" line, then the
# "(no match …)" line, then the colour reset alone on a line of its own.
_NO_TESTS_CLOSING = (
    "\x1b[33m============================ \x1b[33mno tests ran\x1b[0m"
    "\x1b[33m in 0.06s\x1b[0m\x1b[33m =============================\x1b[0m"
)
_NOT_FOUND = "ERROR: not found: /tmp/suite/tests/test_x.py::test_gone"
_NO_MATCH = "(no match in any of [<Module test_x.py>])"
_STALE_NODEID_RUN = (
    _HEADER.replace("collected 2 items", "collected 0 items")
    + "\r\n"
    + _NO_TESTS_CLOSING
    + "\r\n\x1b[31m"
    + _NOT_FOUND
    + "\r\n"
    + _NO_MATCH
    + "\r\n\x1b[0m\r\n"
)

# ONLCR on the pty turns a CRLF inside an exception message into \r\r\n on the
# short-summary line (`raise ValueError("line1\r\nline2")`) and, in the
# traceback, into a \r mid-line followed only by the zero-width reset.
_CRLF_TRACEBACK = (
    "\x1b[1m\x1b[31mE       ValueError: line1\r\x1b[0m\r\n"
    "\x1b[1m\x1b[31mE       line2\x1b[0m\r\n"
)
_CRLF_SUMMARY = (
    "\x1b[36m\x1b[1m=========================== short test summary info "
    "===========================\x1b[0m\r\n"
    "\x1b[31mFAILED\x1b[0m test_suite.py::\x1b[1mtest_crlf\x1b[0m"
    " - ValueError: line1\r\r\n"
)
_CRLF_RUN = _HEADER + _PROGRESS + _CRLF_TRACEBACK + _CRLF_SUMMARY + _CLOSING + "\r\n"

# A test printing a long "=" rule lands it in a captured-stdout section.
_CAPTURED_RULE = (
    "----------------------------- Captured stdout call "
    "-----------------------------\r\n" + "=" * 20000 + "x\r\n"
)
_LONG_RULE_RUN = (
    _HEADER + _PROGRESS + _CAPTURED_RULE + _SHORT_SUMMARY + _CLOSING + "\r\n"
)

# Exit 3: the INTERNALERROR> traceback follows "collected" directly, and the
# "no tests ran" banner still closes the run.
_INTERNAL_CLOSING = (
    "\x1b[32m============================ \x1b[33mno tests ran\x1b[0m"
    "\x1b[32m in 0.06s\x1b[0m\x1b[32m =============================\x1b[0m"
)
_INTERNAL_RUN = (
    _HEADER + "INTERNALERROR> Traceback (most recent call last):\r\n"
    'INTERNALERROR>   File "/tmp/suite/conftest.py", line 2,'
    " in pytest_runtestloop\r\n"
    'INTERNALERROR>     raise RuntimeError("boom")\r\n'
    "INTERNALERROR> RuntimeError: boom\r\n"
    "\r\n" + _INTERNAL_CLOSING + "\r\n"
)

# Exit 4 before the session even starts: no header, no banner, the text itself
# is the message. Two shapes: a conftest ImportError chain (deeper than the
# five-line header fallback) and argparse's usage error for a bad extra arg
# (which ends with a bare ESC[0m line).
_CONFTEST_IMPORT_ERROR = (
    "\x1b[31mImportError while loading conftest '/tmp/suite/conftest.py'."
    "\x1b[0m\r\n"
    "\x1b[31mconftest.py:1: in <module>\x1b[0m\r\n"
    "\x1b[31m    from helpers import util\x1b[0m\r\n"
    "\x1b[31mhelpers/__init__.py:1: in <module>\x1b[0m\r\n"
    "\x1b[31m    from . import util\x1b[0m\r\n"
    "\x1b[31mhelpers/util.py:1: in <module>\x1b[0m\r\n"
    "\x1b[31m    import nonexistent_module_xyz\x1b[0m\r\n"
    "\x1b[31mE   ModuleNotFoundError: No module named 'nonexistent_module_xyz'"
    "\x1b[0m\r\n"
)
_ARGPARSE_ERROR = (
    "\x1b[31mERROR: usage: python -m pytest [options] [file_or_dir]"
    " [file_or_dir] [...]\r\n"
    "python -m pytest: error: unrecognized arguments: --bogus-flag\r\n"
    "  inifile: None\r\n"
    "  rootdir: /tmp/suite\r\n"
    "\x1b[0m\r\n"
)

# Exit 3 before the session header: pytest_configure raised, so the buffer
# opens with the INTERNALERROR> block and no banner follows.
_CONFIGURE_INTERNAL = (
    "INTERNALERROR> Traceback (most recent call last):\r\n"
    'INTERNALERROR>   File "/tmp/suite/conftest.py", line 2, in pytest_configure\r\n'
    'INTERNALERROR>     raise RuntimeError("boom")\r\n'
    "INTERNALERROR> RuntimeError: boom\r\n"
)

# A tqdm/rich-style progress bar: thousands of coloured redraws joined by \r
# on a single line, in a captured-stderr section.
_CAPTURED_STDERR = (
    "----------------------------- Captured stderr call "
    "-----------------------------\r\n"
)
_REDRAWS = "\r".join(f"\x1b[32m####\x1b[0m {i}%" for i in range(5000))
_REDRAW_RUN = _HEADER + _PROGRESS + _CAPTURED_STDERR + _REDRAWS + "\r\n"


@requires_node
def test_ansi_wrapped_closing_line_is_kept_raw():
    # The 1b bug itself: the color-wrapped closing line must be matched (on the
    # stripped copy) and returned raw, ESC codes intact for ansiToHtml.
    (out,) = _convert([_COLORED_ALL_PASS])
    assert _GREEN_CLOSING in out
    assert "2 passed" in out


@requires_node
def test_short_summary_section_is_included_before_the_closing_line():
    (out,) = _convert([_COLORED_RUN])
    # The tail starts at the short-summary marker: FAILED one-liners show.
    assert "short test summary info" in out
    assert "FAILED\x1b[0m test_suite.py" in out
    assert _CLOSING in out
    # The marker precedes the closing line in the output.
    assert out.index("short test summary info") < out.index("1 failed")


@requires_node
def test_progress_dump_is_still_dropped():
    (out,) = _convert([_COLORED_RUN])
    # The middle (progress dots plus the full FAILURES tracebacks) stays
    # dropped; tracebacks live in the detail pane, not the run console.
    assert "[100%]" not in out
    assert "E       assert 1 == 2" not in out
    assert "FAILURES" not in out


@requires_node
def test_header_is_detected_despite_ansi_wrapping():
    (out,) = _convert([_COLORED_RUN])
    # The header is kept from the (bold-wrapped) banner down to "collected N
    # items", and the raw slice is returned.
    assert out.startswith("\x1b[1m========================= test session starts")
    assert "collected 2 items" in out
    assert "rootdir: /tmp/suite" in out


@requires_node
def test_pty_collecting_overwrite_is_resolved_to_the_collected_line():
    # Without terminal semantics the stripped line reads "collecting ...
    # collected 2 items", `^collected ` never fires and the header falls back
    # to a fixed start+5 cut, which here would drag the progress line (index
    # 5, "[100%]") into the header. The cut must land on the collected line,
    # the phantom "collecting ... " prefix must not reach the pane, and the
    # bold set before the \r carries over (a terminal keeps SGR state): the
    # covered stretch's codes settle to "reset, bold", bold being in force.
    (out,) = _convert([_PTY_ALL_PASS])
    assert "collecting" not in out
    assert "\r" not in out
    assert "[100%]" not in out
    out_lines = out.split("\n")
    idx = next(i for i, ln in enumerate(out_lines) if "collected 2 items" in ln)
    assert out_lines[idx].startswith("\x1b[0m\x1b[1mcollected 2 items")
    assert out_lines[idx + 1 :] == ["", _GREEN_CLOSING]


@requires_node
def test_usage_error_printed_after_the_closing_banner_is_kept():
    # Exit 4: the status line sends the user to the run console for pytest's
    # message, so the lines main() prints below the banner must survive the
    # cut, raw and in order, with only the bare trailing reset line trimmed.
    (out,) = _convert([_STALE_NODEID_RUN])
    assert "collected 0 items" in out
    assert _NO_TESTS_CLOSING in out
    assert "\x1b[31m" + _NOT_FOUND in out
    assert _NO_MATCH in out
    assert out.index("no tests ran") < out.index(_NOT_FOUND) < out.index(_NO_MATCH)
    assert out.endswith(_NO_MATCH)


@requires_node
def test_nothing_after_the_closing_banner_leaves_the_tail_unchanged():
    # A normal run ends at its banner: no trailing junk or whitespace appended.
    failed, passed = _convert([_COLORED_RUN, _COLORED_ALL_PASS])
    assert failed.endswith(_CLOSING)
    assert passed.endswith(_GREEN_CLOSING)


@requires_node
def test_plain_uncolored_output_still_works():
    # --color-less output (the pre-fix happy case) keeps working unchanged.
    plain = (
        "== test session starts ==\n"
        "platform linux\n"
        "collected 1 item\n"
        "\n"
        "test_a.py .           [100%]\n"
        "\n"
        "===== 1 passed in 0.01s =====\n"
    )
    (out,) = _convert([plain])
    assert "test session starts" in out
    assert "collected 1 item" in out
    assert "===== 1 passed in 0.01s =====" in out
    assert "[100%]" not in out


@requires_node
def test_mid_run_buffer_without_summary_keeps_header_only():
    (out,) = _convert([_HEADER + "\r\ntest_suite.py \x1b[32m.\x1b[0m"])
    assert "collected 2 items" in out
    assert "test_suite.py \x1b[32m" not in out


@requires_node
def test_unrecognized_text_passes_through_trimmed():
    (out,) = _convert(["just some noise\nno pytest banner here\n"])
    assert out == "just some noise\nno pytest banner here"


@requires_node
def test_cr_before_the_line_end_is_a_no_op():
    # A terminal treats \r right before \n as nothing: the \r\r\n line must
    # stay intact, and so must "collected N items" when a console chunk ends
    # between its \r and \n (blanking it would trigger the start+5 fallback).
    crlf, cut = _convert([_CRLF_RUN, _HEADER[:-1]])
    assert "test_crlf\x1b[0m - ValueError: line1" in crlf
    assert "\r" not in crlf
    assert cut.endswith("collected 2 items")


@requires_node
def test_cr_inside_a_line_still_overwrites():
    # An equal-or-wider new segment covers the old one completely.
    plain = "== test session starts ==\nfirst\rsecond\ncollected 1 item\n"
    (out,) = _convert([plain])
    assert "second" in out
    assert "first" not in out


@requires_node
def test_cr_overlay_keeps_the_earlier_text_beyond_the_new_width():
    # Exact terminal semantics for the text: the new segment overlays column
    # by column and what lies beyond its visible width survives. SGR codes
    # inside the covered stretch are zero-width and are kept in front, settled:
    # a full reset there clears what came before it.
    plain, bold, reset = _convert(
        [
            "== test session starts ==\nabcdef\rXY\ncollected 1 item\n",
            "== test session starts ==\n\x1b[1mabcdef\rXY\ncollected 1 item\n",
            "== test session starts ==\n\x1b[1mab\x1b[0mcdef\rXY\ncollected 1 item\n",
        ]
    )
    assert "\nXYcdef\n" in plain
    assert "\n\x1b[1mXYcdef\n" in bold
    assert "\n\x1b[0mXYcdef\n" in reset


@requires_node
def test_cr_followed_by_a_bare_reset_keeps_the_whole_line():
    # pytest's traceback for a CRLF message: "E   ValueError: line1\r\x1b[0m".
    # The reset has no width, so nothing is covered; a finished run with no
    # banner (shown whole) must render the full E line, reset code kept.
    (out,) = _convert([_finished(_HEADER + _PROGRESS + _CRLF_TRACEBACK)])
    assert "\x1b[0mE       ValueError: line1" in out
    assert "E       line2" in out


@requires_node
def test_coloured_redraw_loop_stays_linear_and_bounded():
    # tqdm/rich progress: 5,000 coloured redraws joined by \r on one line. The
    # SGR prefix carried across redraws must not grow with their number (it
    # did: 20,000 redraws took 14.7 s, on every chunk). The harness timeout
    # catches a hang; the kept line pins the bound and the final redraw.
    live, done = _convert([_REDRAW_RUN, _finished(_REDRAW_RUN)])
    assert live.endswith("collected 2 items")
    line = next(ln for ln in done.split("\n") if ln.endswith(" 4999%"))
    assert len(re.findall(r"\x1b\[[0-9;]*m", line)) <= 4
    assert re.sub(r"\x1b\[[0-9;]*m", "", line) == "#### 4999%"


@requires_node
def test_internalerror_opening_the_buffer_gets_no_blank_separator():
    # Exit 3 from pytest_configure: no session header, INTERNALERROR> on the
    # very first line. Nothing precedes the tail, so no separator either.
    done, live = _convert([_finished(_CONFIGURE_INTERNAL), _CONFIGURE_INTERNAL])
    for out in (done, live):
        assert out.startswith("INTERNALERROR> Traceback")
        assert "\n\n" not in out
        assert out.endswith("INTERNALERROR> RuntimeError: boom")


@requires_node
def test_long_equals_rule_neither_hangs_nor_passes_for_the_banner():
    # /^=+.*=+$/ backtracked super-quadratically on a long "=" run followed by
    # anything else (8000 chars: 29 s), and it ran on every line from the buffer
    # end on every chunk while no banner existed yet. The harness timeout
    # catches a hang; the rule must not be taken for the closing line either.
    done, live = _convert([_LONG_RULE_RUN, _HEADER + _PROGRESS + _CAPTURED_RULE])
    assert "Captured stdout" not in done
    assert "=" * 100 not in done
    assert done.endswith(_CLOSING)
    assert live.endswith("collected 2 items")


@requires_node
def test_internalerror_block_is_kept_from_its_first_line():
    # Exit 3: the INTERNALERROR> traceback sits between "collected" and the
    # "no tests ran" banner, exactly the stretch the cut used to drop.
    (out,) = _convert([_INTERNAL_RUN])
    assert "INTERNALERROR> Traceback (most recent call last):" in out
    assert "INTERNALERROR> RuntimeError: boom" in out
    assert (
        out.index("collected 2 items")
        < out.index("INTERNALERROR> Traceback")
        < out.index("no tests ran")
    )
    assert out.endswith(_INTERNAL_CLOSING)


@requires_node
def test_internalerror_inside_the_header_fallback_window_is_not_doubled():
    # An internal error during collection: no "collected" line, so the header
    # falls back to five lines, which must stop short of the tail.
    plain = (
        "== test session starts ==\n"
        "platform linux\n"
        "INTERNALERROR> Traceback (most recent call last):\n"
        "INTERNALERROR> RuntimeError: boom\n"
    )
    (out,) = _convert([plain])
    assert out.count("INTERNALERROR> Traceback") == 1
    assert out.endswith("INTERNALERROR> RuntimeError: boom")


@requires_node
def test_finished_run_without_a_banner_is_shown_whole():
    # Exit 4 before the session starts: nothing to extract, the buffer itself
    # is pytest's message once the run is over. Mid-run (no banner yet) the
    # header-only cut stands; the pane's `finished` flag tells them apart.
    done, live = _convert([_finished(_CONFTEST_IMPORT_ERROR), _CONFTEST_IMPORT_ERROR])
    assert done.startswith("\x1b[31mImportError while loading conftest")
    assert done.endswith("No module named 'nonexistent_module_xyz'\x1b[0m")
    assert "No module named" not in live
    assert live.endswith("\x1b[31mhelpers/util.py:1: in <module>\x1b[0m")


@requires_node
def test_trailing_reset_line_is_trimmed_on_every_path():
    # argparse's usage error ends with a bare ESC[0m line: neither the
    # whole-buffer nor the header-fallback cut may end the pane on a blank.
    done, live = _convert([_finished(_ARGPARSE_ERROR), _ARGPARSE_ERROR])
    for out in (done, live):
        assert "unrecognized arguments: --bogus-flag" in out
        assert out.endswith("  rootdir: /tmp/suite")


# The short-summary shapes pytest emits: the word coloured, the nodeid's
# `::` tail bold, an optional " - message" (width-trimmed), a setup ERROR, an
# XPASS reason, a collect-time ERROR on a bare file path, and a folded SKIPPED
# line that carries `[n] path:line` instead of a nodeid.
_MARKER = (
    "\x1b[36m\x1b[1m=========================== short test summary info "
    "===========================\x1b[0m"
)
_FAILED_LINE = (
    "\x1b[31mFAILED\x1b[0m test_suite.py::\x1b[1mtest_c_fails\x1b[0m - assert 1 == 2"
)
_ERROR_LINE = (
    "\x1b[31mERROR\x1b[0m test_suite.py::\x1b[1mtest_broken_fixture\x1b[0m"
    " - fixture 'nope' not found"
)
_XPASS_LINE = "\x1b[33mXPASS\x1b[0m test_suite.py::\x1b[1mtest_flaky\x1b[0m - wip"
_COLLECT_ERROR_LINE = "\x1b[31mERROR\x1b[0m tests/test_bad.py"
_SKIPPED_LINE = "\x1b[33mSKIPPED\x1b[0m [1] test_suite.py:12: not today"
_DASHED_ID = "test_suite.py::test_p[a - b]"
_DASHED_LINE = "\x1b[31mFAILED\x1b[0m test_suite.py::\x1b[1mtest_p[a - b]\x1b[0m - boom"
_SUMMARY_BLOCK = "\n".join(
    [
        _MARKER,
        _FAILED_LINE,
        _ERROR_LINE,
        _XPASS_LINE,
        _COLLECT_ERROR_LINE,
        _SKIPPED_LINE,
        _DASHED_LINE,
        _CLOSING,
    ]
)
_PIECES_RUN = "header line\ncollected 6 items\n\n" + _SUMMARY_BLOCK
_KNOWN = [
    "test_suite.py::test_c_fails",
    "test_suite.py::test_broken_fixture",
    "test_suite.py::test_flaky",
    _DASHED_ID,
]


@requires_node
def test_summary_pieces_turn_known_lines_into_entries():
    # Every WORD-nodeid line the store knows becomes an entry: nodeid read off
    # the stripped copy (pytest bolds its `::` tail), `rest` the raw slice
    # after it (the closing reset, then " - message"), colours kept.
    (pieces,) = _pieces([[_PIECES_RUN, _KNOWN]])
    entries = [p for p in pieces if p["kind"] == "entry"]
    assert [e["nodeid"] for e in entries] == [
        "test_suite.py::test_c_fails",
        "test_suite.py::test_broken_fixture",
        "test_suite.py::test_flaky",
        _DASHED_ID,
    ]
    assert entries[0]["rest"] == "\x1b[0m - assert 1 == 2"
    assert entries[1]["rest"] == "\x1b[0m - fixture 'nope' not found"
    assert entries[3]["rest"] == "\x1b[0m - boom"


@requires_node
def test_summary_pieces_keep_everything_else_as_raw_text():
    # The header through the marker is one raw piece; unrecognised summary
    # lines (a collect-time ERROR on a file, a folded SKIPPED) and the closing
    # banner stay raw text too, in order, so the pane renders them unchanged.
    (pieces,) = _pieces([[_PIECES_RUN, _KNOWN]])
    kinds = [p["kind"] for p in pieces]
    assert kinds == ["text", "entry", "entry", "entry", "text", "entry", "text"]
    assert pieces[0]["raw"] == "header line\ncollected 6 items\n\n" + _MARKER
    assert pieces[4]["raw"] == _COLLECT_ERROR_LINE + "\n" + _SKIPPED_LINE
    assert pieces[6]["raw"] == _CLOSING


@requires_node
def test_summary_pieces_leave_unknown_nodeids_alone():
    # A nodeid the store cannot vouch for (a cwd-relative path the tree does
    # not use, a stale line) is not worth a badge: the line stays text.
    (pieces,) = _pieces([[_PIECES_RUN, []]])
    assert [p["kind"] for p in pieces] == ["text"]
    assert pieces[0]["raw"] == _PIECES_RUN


@requires_node
def test_summary_pieces_try_every_dash_for_a_dashed_param_id():
    # "test_p[a - b] - boom": the first " - " lands inside the param id, so
    # the split points are tried left to right until the store recognises
    # one. A line with no message at all (a bare XPASS) is the last candidate,
    # and a line whose split points are all unknown stays text.
    no_msg = "\x1b[33mXPASS\x1b[0m test_suite.py::\x1b[1mtest_flaky\x1b[0m"
    dashed, bare, unknown = _pieces(
        [
            [_MARKER + "\n" + _DASHED_LINE, [_DASHED_ID]],
            [_MARKER + "\n" + no_msg, ["test_suite.py::test_flaky"]],
            [_MARKER + "\n" + _DASHED_LINE, ["test_suite.py::other"]],
        ]
    )
    assert dashed[1]["nodeid"] == _DASHED_ID
    assert dashed[1]["rest"] == "\x1b[0m - boom"
    assert bare[1]["nodeid"] == "test_suite.py::test_flaky"
    assert bare[1]["rest"] == "\x1b[0m"
    assert [p["kind"] for p in unknown] == ["text"]


@requires_node
def test_summary_pieces_without_a_summary_block_are_one_text_piece():
    # An all-passed run has no short summary: the output is a single raw
    # piece equal to the input, and an empty buffer is an empty piece.
    passed, empty = _pieces([[_COLORED_ALL_PASS, _KNOWN], ["", _KNOWN]])
    assert passed == [{"kind": "text", "raw": _COLORED_ALL_PASS}]
    assert empty == [{"kind": "text", "raw": ""}]


@requires_node
def test_summary_pieces_stop_at_the_closing_banner():
    # Lines pytest's main() prints below the banner (exit 4) never become
    # entries even when they look like one and the store knows the id.
    after = _MARKER + "\n" + _CLOSING + "\n" + _FAILED_LINE
    (pieces,) = _pieces([[after, _KNOWN]])
    assert [p["kind"] for p in pieces] == ["text"]
