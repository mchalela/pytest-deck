"""Tests for ``frontend/src/lib/rules.js``: pytest's fill lines as rules.

``renderBlock`` output is injected via ``{@html}`` in CopyPre and RunConsole,
so besides the parsing decisions these pin two safety claims: every text run
still goes through ``ansiToHtml``'s escaping, and nothing but the rule spans is
added. Same node-shell pattern as test_ansi_js.py; skips without ``node``.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_RULES_JS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "rules.js"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

_HARNESS = f"""
import {{ parseRule, renderBlock }} from {json.dumps(_RULES_JS.as_uri())};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const {{ fn, cases }} = JSON.parse(raw);
  const f = fn === "parseRule" ? parseRule : renderBlock;
  process.stdout.write(JSON.stringify(cases.map(f)));
}});
"""


def _call(fn, cases):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS],
        input=json.dumps({"fn": fn, "cases": cases}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _parse(line):
    (out,) = _call("parseRule", [line])
    return out


def _render(block):
    (out,) = _call("renderBlock", [block])
    return out


# pytest's lines as the --color=yes pty emits them.
SESSION = "\x1b[1m" + "=" * 50 + " test session starts " + "=" * 50 + "\x1b[0m"
CLOSING = (
    "\x1b[31m"
    + "=" * 20
    + " \x1b[31m\x1b[1m3 failed\x1b[0m\x1b[31m in 0.40s\x1b[0m\x1b[31m "
    + "=" * 20
    + "\x1b[0m"
)
FRAME = "_ " * 40 + "_"
SUMMARY = (
    "\x1b[36m\x1b[1m" + "=" * 30 + " short test summary info " + "=" * 30 + "\x1b[0m"
)


@requires_node
@pytest.mark.parametrize(
    "line, char, label",
    [
        (SESSION, "=", "test session starts"),
        (CLOSING, "=", "3 failed in 0.40s"),
        (SUMMARY, "=", "short test summary info"),
        (FRAME, "_", ""),
        ("=" * 80, "=", ""),
        ("-" * 12 + " Captured stdout call " + "-" * 12, "-", "Captured stdout call"),
        # Labels may contain the fill character (test names with underscores).
        (
            "_" * 20 + " test_normalize_bad_element_deep " + "_" * 20,
            "_",
            "test_normalize_bad_element_deep",
        ),
    ],
)
def test_fill_lines_are_rules(line, char, label):
    assert _parse(line) == {"char": char, "label": label}


@requires_node
@pytest.mark.parametrize(
    "line",
    [
        "a = 3.0, b = 'oops'",  # fill character mid-line
        "--- a/toy.py",  # one-sided: a diff header, not padding
        "--> normalize(values)",
        "-----",  # too short to be terminal padding
        "E       assert 5.0 == 99",
        "",
        "    return _accumulate(values)",
    ],
)
def test_ordinary_lines_stay_text(line):
    assert _parse(line) is None


@requires_node
def test_rule_keeps_the_label_and_its_colour():
    html = _render(CLOSING)
    assert 'class="rule rule-section"' in html
    assert "3 failed" in html and "in 0.40s" in html
    assert "=" not in html.replace('="', "")  # no fill characters survive
    # The line is tinted with the label's colour (31 = the red in ansi.js FG).
    assert "--rule-color:#e0697a" in html


@requires_node
def test_bare_separator_has_no_label():
    html = _render(FRAME)
    assert html.startswith('<span class="rule rule-frame rule-bare"')
    assert "rule-label" not in html


@requires_node
def test_rule_swallows_its_own_newline_only():
    html = _render("before\n" + FRAME + "\nafter")
    # Text keeps its newline; the block-level rule drops the one after it.
    assert html.startswith("before\n<span")
    assert html.endswith("</span>after")


@requires_node
def test_text_runs_are_still_escaped():
    html = _render("<b>x</b>\n" + "=" * 20 + " <script>alert(1)</script> " + "=" * 20)
    assert "<b>" not in html and "<script" not in html
    assert "&lt;b&gt;" in html and "&lt;script&gt;" in html


@requires_node
def test_colour_carries_across_lines_until_reset():
    # pygments can open a colour on one line and reset on the next.
    html = _render("\x1b[33m'''doc\nstring'''\x1b[0m\nplain")
    first, second, third = html.split("\n")
    assert first.startswith("<span") and second.startswith("<span")
    assert third == "plain"


@requires_node
def test_plain_block_is_unchanged():
    block = "def f():\n    return 1\n"
    assert _render(block) == block
