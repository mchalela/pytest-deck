"""Parity test: the JS port (`frontend/src/lib/outcome.js`) MUST agree with the
Python oracle (`pytest_deck.outcome.overall_outcome`) on every case.

The JS `overallOutcome` is the only thing deriving the displayed pass/fail badge
from per-phase reports, so it must never drift from the
Python oracle. This test drives BOTH implementations from ONE shared case matrix
(``PARITY_CASES``) and asserts they return the identical outcome for each case:

* Python side: calls ``overall_outcome`` directly.
* JS side: shells out to ``node`` running a tiny ES-module wrapper that imports
  the real ``outcome.js``, reads the case matrix as JSON on stdin, and prints one
  result per line.

The matrix covers every outcome type and §3.1 rule (and is kept in step with
``test_outcome.py``'s Python cases). A future edit to either implementation that
changes any case's result fails this test.

``node`` is required for the JS half; the test skips cleanly if it's absent so a
Node-less CI doesn't hard-fail. (CI's quality job installs Node 24 for it.)
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pytest_deck.outcome import overall_outcome

_OUTCOME_JS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "outcome.js"
)


def _phase(outcome, wasxfail=None):
    """One phase dict in the wire shape both implementations consume."""
    return {"outcome": outcome, "wasxfail": wasxfail}


# The single source of truth, fed to both the Python oracle and the Node port.
# Each entry is (label, phases, expected); ``expected`` documents intent and is
# asserted against the Python oracle too, so the matrix can't silently encode a
# wrong expectation that both sides happen to share.
PARITY_CASES = [
    (
        "passed",
        {
            "setup": _phase("passed"),
            "call": _phase("passed"),
            "teardown": _phase("passed"),
        },
        "passed",
    ),
    (
        "passed_no_teardown",
        {"setup": _phase("passed"), "call": _phase("passed")},
        "passed",
    ),
    ("failed_call", {"setup": _phase("passed"), "call": _phase("failed")}, "failed"),
    ("error_failed_setup", {"setup": _phase("failed")}, "error"),
    (
        "error_failed_teardown",
        {
            "setup": _phase("passed"),
            "call": _phase("passed"),
            "teardown": _phase("failed"),
        },
        "error",
    ),
    (
        "call_fail_beats_teardown_fail",
        {
            "setup": _phase("passed"),
            "call": _phase("failed"),
            "teardown": _phase("failed"),
        },
        "failed",
    ),
    (
        "setup_fail_beats_later",
        {"setup": _phase("failed"), "teardown": _phase("failed")},
        "error",
    ),
    ("skipped_call", {"setup": _phase("passed"), "call": _phase("skipped")}, "skipped"),
    ("skipped_setup_no_call", {"setup": _phase("skipped")}, "skipped"),
    (
        "xfailed_call",
        {"setup": _phase("passed"), "call": _phase("skipped", "known bug")},
        "xfailed",
    ),
    (
        "xpassed_call",
        {"setup": _phase("passed"), "call": _phase("passed", "passes now")},
        "xpassed",
    ),
    (
        "xfailed_setup_no_call",
        {"setup": _phase("skipped", "xfail at setup")},
        "xfailed",
    ),
    (
        "strict_xpass_is_failed",
        {"setup": _phase("passed"), "call": _phase("failed")},
        "failed",
    ),
    ("failed_call_beats_wasxfail", {"call": _phase("failed", "reason")}, "failed"),
    ("incomplete_setup_passed_no_call", {"setup": _phase("passed")}, "incomplete"),
    ("incomplete_empty", {}, "incomplete"),
    ("incomplete_teardown_only", {"teardown": _phase("passed")}, "incomplete"),
]


# A small ES-module wrapper: import the real outcome.js, map each phase-dict
# through ``overallOutcome``, print one result per line. Driven via ``node
# --input-type=module`` so we don't write a temp file. The matrix arrives on
# stdin as a JSON list of phase-dicts; the real module is imported by its
# JSON-quoted ``file://`` URL.
_NODE_WRAPPER = """
import {{ overallOutcome }} from {module_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  const out = cases.map((phases) => overallOutcome(phases));
  process.stdout.write(out.join("\\n"));
}});
"""


def _run_js(cases):
    """Run the JS port over ``cases`` via node; return its outcome list."""
    module_url = json.dumps(_OUTCOME_JS.as_uri())  # file:// URL, JSON-quoted
    script = _NODE_WRAPPER.format(module_url=module_url)
    payload = json.dumps([phases for _, phases, _ in cases])
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        proc.returncode == 0
    ), f"node wrapper failed (rc={proc.returncode}):\n{proc.stderr}"
    return proc.stdout.split("\n")


requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def test_outcome_js_file_exists():
    """The JS port must exist where the parity test expects it."""
    assert _OUTCOME_JS.is_file(), f"missing JS port: {_OUTCOME_JS}"


@requires_node
def test_js_matches_python_on_every_case():
    """The JS port returns the SAME outcome as the Python oracle for each case."""
    js_results = _run_js(PARITY_CASES)
    assert len(js_results) == len(
        PARITY_CASES
    ), f"node returned {len(js_results)} results for {len(PARITY_CASES)} cases"

    mismatches = []
    for (label, phases, expected), js in zip(PARITY_CASES, js_results):
        py = overall_outcome(phases)
        # The Python oracle must match the documented expectation (so the shared
        # matrix can't drift into a wrong-but-agreed answer)...
        assert py == expected, f"{label}: python {py!r} != expected {expected!r}"
        # ...and the JS port must match the Python oracle exactly.
        if js != py:
            mismatches.append(f"{label}: js={js!r} python={py!r}")
    assert not mismatches, "JS/Python outcome drift:\n" + "\n".join(mismatches)


@requires_node
def test_every_outcome_type_is_covered():
    """Sanity: the matrix exercises every distinct outcome the spec defines."""
    expected_types = {e for _, _, e in PARITY_CASES}
    assert expected_types == {
        "passed",
        "failed",
        "error",
        "skipped",
        "xfailed",
        "xpassed",
        "incomplete",
    }
