"""XSS-guard tests for ``frontend/src/lib/ansi.js``.

``ansiToHtml`` output is injected via ``{@html}`` in three components; the
eslint ``svelte/no-at-html-tags`` suppressions rest on the claim that every
text run is HTML-escaped before any span is built. These tests pin that claim.

Same node-shell pattern as test_diff_js.py; skips cleanly without ``node``.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ANSI_JS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "ansi.js"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

_HARNESS = f"""
import {{ ansiToHtml }} from {json.dumps(_ANSI_JS.as_uri())};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  process.stdout.write(JSON.stringify(cases.map(ansiToHtml)));
}});
"""


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


@requires_node
def test_html_metacharacters_are_escaped_in_plain_text():
    (out,) = _convert(['<script>alert(1)</script> & <img onerror="x">'])
    assert "<script" not in out
    assert "<img" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


@requires_node
def test_html_inside_ansi_coloured_runs_is_escaped_too():
    # The XSS payload rides inside a coloured span and must still be escaped.
    (out,) = _convert(["\x1b[31m<b onmouseover=evil()>boom</b>\x1b[0m"])
    assert "<b " not in out and "<b>" not in out
    assert "&lt;b onmouseover=evil()&gt;boom&lt;/b&gt;" in out
    assert out.startswith("<span")  # the colour span itself is generated markup


@requires_node
def test_only_generated_spans_may_carry_markup():
    # Every '<' in the output must belong to a generated <span>/</span>.
    (out,) = _convert(["\x1b[1m\x1b[92m<>&\x1b[39;49;00m plain <>&"])
    stripped = out.replace("<span", "").replace("</span", "").replace(">", "")
    assert "<" not in stripped


@requires_node
def test_plain_text_without_ansi_is_passthrough_escaped_only():
    (out,) = _convert(["no colours, just text"])
    assert out == "no colours, just text"
