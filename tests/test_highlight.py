"""Regression tests for COLORED tracebacks (the longrepr_text ANSI feature).

The inner plugin (`_inner._render_longrepr`) renders ``longrepr_text`` with
pytest's FULL coloured terminal output: ``hasmarkup=True`` forces the SGR codes
in, and pygments source-line highlighting is ENABLED (``code_highlight`` left at
its default True) so the frames get the green/blue/teal terminal look the user
wanted.

Three guarantees this pins:

* **Core color is present** — a failing test's ``report`` carries pytest's own
  ANSI codes (``\\x1b[31m`` red, ``\\x1b[1m`` bold, ``\\x1b[0m`` reset) on the
  ``E`` lines / location.
* **Pygments highlighting is present** — the source frames carry pygments'
  16-color bright-fg codes (e.g. ``\\x1b[94m`` keywords/numbers, ``\\x1b[92m``
  function names, ``\\x1b[90m`` comments, ``\\x1b[33m`` strings, ``\\x1b[96m``
  builtins) plus the compound default-color reset ``\\x1b[39;49;00m``.
* **Still 16-color only** — pygments uses ``TerminalFormatter``, so 256-color
  (``\\x1b[38;5;Nm``) and truecolor (``\\x1b[38;2;Nm``) are ABSENT. The frontend
  ``ansi.js`` palette only maps the 16-color set, so this invariant matters.
* **Color is additive, not destructive** — stripping the ANSI yields the same
  human-readable content (the ``E`` lines, the ``file.py:NN:`` location, the
  source frames).

Driven against a real pytest subprocess (the ``test_runner.py`` style), with the
``run_async`` helper (``pytest-asyncio`` is not installed). A JS-converter test
for ``ansi.js`` lives at the bottom (shelled to node, like the outcome-parity
test) to pin the security-critical HTML-escape behavior.
"""

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pytest_deck.runner import RunManager


def run_async(coro):
    return asyncio.run(coro)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return _ANSI_RE.sub("", text)


# 256-color and truecolor SGR tokens that must stay out: pytest uses pygments'
# ``TerminalFormatter`` (16-color only), and the frontend ``ansi.js`` palette
# only maps the 16-color set, so anything richer would render as an unstyled
# run.
_RICH_COLOR_MARKERS = (
    "\x1b[38;5;",  # 256-color foreground
    "\x1b[48;5;",  # 256-color background
    "\x1b[38;2;",  # 24-bit truecolor foreground
    "\x1b[48;2;",  # 24-bit truecolor background
)

# Pygments 16-color bright-fg source-highlighting codes that should be present
# when ``code_highlight`` is enabled. Only a representative subset has to
# appear, not every code on every fixture: a plain failing function reliably
# colors keywords/numbers (94), the function name (92) and any comment (90); a
# string compare adds strings (33); builtins add cyan (96). See _assert_* below.
_PYGMENTS_PRESENT_CODES = ("\x1b[94m", "\x1b[92m", "\x1b[90m", "\x1b[33m", "\x1b[96m")


def _assert_no_rich_color_codes(text):
    """16-color-only invariant: no 256-color / truecolor SGR codes appear."""
    for marker in _RICH_COLOR_MARKERS:
        assert marker not in text, (
            f"256-color/truecolor code {marker!r} leaked in — pytest should emit "
            "16-color pygments (TerminalFormatter) only"
        )


def _assert_pygments_highlighting_present(text, required):
    """Assert the given pygments bright-fg codes are present (highlighting on)."""
    for code in required:
        assert code in text, (
            f"expected pygments source-highlighting code {code!r} — "
            "code_highlight should be enabled"
        )


# --- fixture suite --------------------------------------------------------


@pytest.fixture
def fail_suite(tmp_path):
    """A failing test whose traceback gets bold-red core color AND pygments.

    The body has a ``def`` (function name → ``\\x1b[92m``), keywords/numbers
    (``\\x1b[94m``) and a comment (``\\x1b[90m``), so pygments source
    highlighting reliably emits its bright-fg codes on the rendered frame.
    """
    (tmp_path / "test_color.py").write_text(
        "def test_color_fail():\n"
        "    actual = 7 * 6 + 1  # compute the answer\n"
        "    assert actual == 42\n"
    )
    return tmp_path


@pytest.fixture
def diff_suite(tmp_path):
    """A failure that renders a multi-line COLORED diff (dict + string compare).

    Unlike a scalar ``assert 43 == 42``, a dict/string comparison makes pytest
    emit its assertion-diff coloring — bright fg (``\\x1b[90m``..) and the
    compound reset (``\\x1b[39;49;00m``). This is the exact case that would have
    tripped the old "only the trio" assertion, so it pins the real invariant
    (no pygments) against it.
    """
    (tmp_path / "test_diff.py").write_text(
        "def test_dict_diff():\n"
        "    assert {'a': 1, 'b': 2, 'c': 3} == {'a': 1, 'b': 99, 'c': 3}\n"
        "\n"
        "def test_str_diff():\n"
        "    assert 'the quick brown fox' == 'the quick brown dog'\n"
    )
    return tmp_path


# Pygments codes reliably present on the fail_suite frame: keyword/number (94)
# and the function name (92). Other codes (90 comments, 33 strings, 96 builtins)
# are fixture-dependent, so the strict presence check uses just this pair.
_PYGMENTS_MIN = ("\x1b[94m", "\x1b[92m")


async def _run_and_get_call_longrepr(rootdir, nodeid):
    """Run ``nodeid`` and return the ``call`` phase's ``longrepr`` text."""
    mgr = RunManager(rootdir)
    sub = mgr.subscribe()
    await mgr.start([nodeid])
    longrepr = None
    while True:
        ev = await asyncio.wait_for(sub.get(), timeout=60)
        if ev is None:
            break
        if ev.name == "report" and ev.data["when"] == "call":
            longrepr = ev.data["longrepr"]
        if ev.name == "finished":
            break
    await mgr._run.join()
    return longrepr


# === backend: colored longrepr ===========================================


def test_failing_report_longrepr_has_vanilla_ansi_color(fail_suite):
    async def body():
        lr = await _run_and_get_call_longrepr(
            fail_suite, "test_color.py::test_color_fail"
        )
        assert lr is not None, "failing call produced no longrepr"

        # pytest's vanilla SGR codes are present on the E-line / location.
        assert "\x1b[31m" in lr, "expected red (31) on the failure"
        assert "\x1b[1m" in lr, "expected bold (1) on the failure"
        assert "\x1b[0m" in lr, "expected a reset (0) closing the colored runs"

    run_async(body())


def test_longrepr_has_pygments_highlighting_but_no_256_color(fail_suite):
    """Pygments source highlighting is ON, but stays 16-color only.

    With ``code_highlight`` enabled, the rendered frame carries pygments'
    16-color bright-fg codes (keywords/numbers ``\\x1b[94m``, function name
    ``\\x1b[92m``, etc.). Because pytest uses ``TerminalFormatter``, NO 256-color
    (``\\x1b[38;5;Nm``) or truecolor (``\\x1b[38;2;Nm``) codes appear — the
    frontend ``ansi.js`` only maps the 16-color set, so that invariant matters.
    """

    async def body():
        lr = await _run_and_get_call_longrepr(
            fail_suite, "test_color.py::test_color_fail"
        )
        assert lr is not None
        # Pygments highlighting is present on the source frame.
        _assert_pygments_highlighting_present(lr, _PYGMENTS_MIN)
        # But it stays 16-color only: no 256-color, no truecolor.
        _assert_no_rich_color_codes(lr)

    run_async(body())


def test_color_is_additive_stripping_yields_readable_content(fail_suite):
    """Color must be additive: strip the ANSI → the same human-readable text.

    The E-line, the ``file.py:NN:`` location and the source frames must all be
    present in the de-colored text, exactly as before the feature landed.
    """

    async def body():
        lr = await _run_and_get_call_longrepr(
            fail_suite, "test_color.py::test_color_fail"
        )
        assert lr is not None

        plain = strip_ansi(lr)
        # No escape bytes remain after stripping.
        assert "\x1b" not in plain

        # The readable content survives intact (this is what the detail pane
        # showed before color; color only wraps it).
        assert "def test_color_fail():" in plain  # the source frame
        assert "E       assert 43 == 42" in plain  # the rendered E-line
        assert re.search(r"test_color\.py:3:", plain), plain  # file:NN: location
        assert "AssertionError" in plain

        # And the word "assert" survives even without stripping. test_runner.py
        # asserts this on a different suite; pin the invariant here too, since
        # ANSI only wraps the E-line and the word itself stays plain text.
        assert "assert" in lr

    run_async(body())


@pytest.mark.parametrize(
    "nodeid", ["test_diff.py::test_dict_diff", "test_diff.py::test_str_diff"]
)
def test_colored_diff_failure_stays_16_color(diff_suite, nodeid):
    """A COLORED diff failure renders fine and stays 16-color (no 256/truecolor).

    A dict/string comparison renders a multi-line colored diff: pytest's
    assertion-diff codes (bright fg + the ``\\x1b[39;49;00m`` compound reset)
    PLUS pygments highlighting of the frames. Both are expected. The surviving
    invariant is that it's still 16-color only — no 256-color/truecolor.
    """

    async def body():
        lr = await _run_and_get_call_longrepr(diff_suite, nodeid)
        assert lr is not None, "colored diff failure produced no longrepr"

        # It rendered with color (bold + red are still there on a diff failure).
        assert "\x1b[1m" in lr
        assert "\x1b[31m" in lr
        assert "\x1b[0m" in lr

        # The multi-line colored-diff path emits assertion-diff coloring (bright
        # fg + the compound reset). Pin at least one so this genuinely exercises
        # a colored diff, not a scalar compare.
        assert "\x1b[39;49;00m" in lr or re.search(r"\x1b\[9[0-7]m", lr), (
            "expected pytest assertion-diff coloring on a dict/string diff; "
            "this fixture must render a colored diff to be meaningful"
        )

        # Pygments highlighting is present on the diff's source frame too.
        _assert_pygments_highlighting_present(lr, _PYGMENTS_MIN)

        # The surviving invariant: still 16-color only, no 256-color or
        # truecolor.
        _assert_no_rich_color_codes(lr)

        # And color stays additive: stripping yields the readable diff body.
        plain = strip_ansi(lr)
        assert "\x1b" not in plain
        assert "AssertionError" in plain

    run_async(body())


# === frontend converter: ansi.js (shelled to node) =======================

_ANSI_JS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "ansi.js"
)


def _ansi_fg_hex(code):
    """Read the hex ``ansi.js`` assigns to SGR fg ``code`` from its FG map.

    Derived from source so a future palette tune can't silently desync the
    JS-converter assertions (the bug that pinned the wrong red here before).
    """
    src = _ANSI_JS.read_text()
    m = re.search(rf"\b{code}:\s*\"(#[0-9a-fA-F]{{3,8}})\"", src)
    assert m, f"could not find FG[{code}] in {_ANSI_JS}"
    return m.group(1)


# A tiny ES-module wrapper: import the real ansi.js, read the input strings as
# a JSON list on stdin, and print the ansiToHtml results back as a JSON list.
# Same pattern as test_outcome_js_parity.py.
_NODE_WRAPPER = """
import {{ ansiToHtml }} from {module_url};
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const inputs = JSON.parse(raw);
  const out = inputs.map((s) => ansiToHtml(s));
  process.stdout.write(JSON.stringify(out));
}});
"""


def _run_ansi_js(inputs):
    module_url = json.dumps(_ANSI_JS.as_uri())
    script = _NODE_WRAPPER.format(module_url=module_url)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node wrapper failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def test_ansi_js_file_exists():
    assert _ANSI_JS.is_file(), f"missing JS converter: {_ANSI_JS}"


@requires_node
def test_ansi_js_renders_bold_red_and_escapes_html():
    # A realistic pytest-style colored E-line, plus an injected HTML payload in
    # the *text* to exercise the XSS guard.
    bold_red_line = "\x1b[1m\x1b[31mE       assert 43 == 42\x1b[0m"
    injected = "\x1b[31m<script>alert(1)</script> & <b>x</b>\x1b[0m"
    no_color = "plain text, no escapes"

    html_bold_red, html_injected, html_plain = _run_ansi_js(
        [bold_red_line, injected, no_color]
    )

    # (a) bold and red render together in one span (the lazy span open merges
    # the back-to-back ESC[1m ESC[31m into a single styled span).
    assert html_bold_red.count("<span") == 1, html_bold_red
    # Code 31 (normal red) maps to whatever the current ansi.js FG map assigns
    # it (read from source so a palette tune can't silently desync this
    # assertion). Note that 31 is not bright red (91); pinning a hardcoded hue
    # is what drifted before.
    red31 = _ansi_fg_hex(31)
    assert f"color:{red31}" in html_bold_red, (html_bold_red, red31)
    assert "font-weight:600" in html_bold_red  # bold (1)
    assert "E       assert 43 == 42" in html_bold_red
    assert html_bold_red.count("</span>") == 1

    # (b) injected HTML in the text is escaped (the XSS guard). No live tags.
    assert "&lt;script&gt;" in html_injected
    assert "&lt;/script&gt;" in html_injected
    assert "<script>" not in html_injected  # never a live script tag
    assert "&amp;" in html_injected  # the bare & is escaped
    assert "&lt;b&gt;" in html_injected  # injected <b> escaped, not rendered

    # (c) no raw escape byte leaks into any output.
    for out in (html_bold_red, html_injected, html_plain):
        assert "\x1b" not in out

    # Sanity: plain text with no SGR produces no span wrapper at all.
    assert "<span" not in html_plain
    assert html_plain == "plain text, no escapes"


@requires_node
def test_ansi_js_roundtrips_a_real_pytest_longrepr(fail_suite):
    """End-to-end: a REAL colored longrepr → ansi.js → escaped HTML, no raw ESC.

    Ties the backend feature to the frontend converter: whatever ANSI the plugin
    emits, ``ansiToHtml`` must render without leaking escape bytes and while
    preserving the readable text.
    """

    async def body():
        lr = await _run_and_get_call_longrepr(
            fail_suite, "test_color.py::test_color_fail"
        )
        assert lr is not None
        return lr

    lr = run_async(body())
    (html,) = _run_ansi_js([lr])
    assert "\x1b" not in html  # no raw escape leaked through
    assert "assert 43 == 42" in html  # readable content preserved
    # The red E-line (code 31) got colored with the current map's value for 31.
    assert f"color:{_ansi_fg_hex(31)}" in html  # the red E-line got colored
    # Pygments frame highlighting also rendered (keyword/number is code 94).
    assert f"color:{_ansi_fg_hex(94)}" in html
    assert "<script" not in html  # nothing injectable in a real traceback either
