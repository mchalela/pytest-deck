"""Tests for ``pytest_deck.outcome.overall_outcome`` — the outcome-derivation oracle.

``overall_outcome`` folds a test's per-phase reports (setup/call/teardown) into a
single display outcome. This module pins the exact derivation rules and
declares this function the **oracle for the JS port**: the frontend implements the
same logic in JavaScript and is validated against these cases. So this module is
deliberately exhaustive over every outcome the spec lists:

    passed / failed / error / skipped / xfailed / xpassed / incomplete

Each case is built from the per-phase shape the wire actually carries (``outcome``
plus optional ``wasxfail``), so a regression in the rules fails loudly here.
"""

import pytest

from pytest_deck.outcome import overall_outcome


def _phase(outcome, wasxfail=None):
    """Build one phase dict in the shape the §3 ``report`` event carries."""
    return {"outcome": outcome, "wasxfail": wasxfail}


# --- the happy path -------------------------------------------------------


def test_all_phases_pass_is_passed():
    phases = {
        "setup": _phase("passed"),
        "call": _phase("passed"),
        "teardown": _phase("passed"),
    }
    assert overall_outcome(phases) == "passed"


def test_call_pass_without_teardown_is_passed():
    # The fold must not wait for teardown to call a passing test passed.
    phases = {"setup": _phase("passed"), "call": _phase("passed")}
    assert overall_outcome(phases) == "passed"


# --- failures vs errors (phase matters) -----------------------------------


def test_failed_call_is_failed():
    phases = {"setup": _phase("passed"), "call": _phase("failed")}
    assert overall_outcome(phases) == "failed"


def test_failed_setup_is_error_and_has_no_call():
    # A setup failure means pytest never produced a ``call`` report at all. The
    # spec maps a failed setup to "error", which is distinct from a test failure.
    phases = {"setup": _phase("failed")}
    assert overall_outcome(phases) == "error"


def test_failed_teardown_is_error():
    # The test body passed, but teardown blew up: that is an error, not a pass.
    phases = {
        "setup": _phase("passed"),
        "call": _phase("passed"),
        "teardown": _phase("failed"),
    }
    assert overall_outcome(phases) == "error"


def test_call_failure_takes_priority_over_teardown_failure():
    # If both call and teardown fail, the spec scans setup, then call, then
    # teardown, so the failed call wins and the result is "failed" (the test
    # itself failed).
    phases = {
        "setup": _phase("passed"),
        "call": _phase("failed"),
        "teardown": _phase("failed"),
    }
    assert overall_outcome(phases) == "failed"


def test_setup_failure_takes_priority_over_later_phases():
    # Setup is scanned first; a failed setup is an error whatever the other
    # phases say.
    phases = {"setup": _phase("failed"), "teardown": _phase("failed")}
    assert overall_outcome(phases) == "error"


# --- skips ----------------------------------------------------------------


def test_skipped_call_is_skipped():
    # pytest.skip() inside the body: setup passes, call is skipped.
    phases = {"setup": _phase("passed"), "call": _phase("skipped")}
    assert overall_outcome(phases) == "skipped"


def test_setup_skip_with_no_call_is_skipped():
    # @pytest.mark.skip: the skip is reported on setup and there is no call.
    phases = {"setup": _phase("skipped")}
    assert overall_outcome(phases) == "skipped"


# --- xfail / xpass --------------------------------------------------------


def test_xfail_is_xfailed():
    # Expected failure: pytest reports the call as "skipped" with a wasxfail
    # reason.
    phases = {
        "setup": _phase("passed"),
        "call": _phase("skipped", wasxfail="known bug"),
    }
    assert overall_outcome(phases) == "xfailed"


def test_xpass_is_xpassed():
    # Unexpected pass: call passed but carries a wasxfail reason.
    phases = {
        "setup": _phase("passed"),
        "call": _phase("passed", wasxfail="this passes now"),
    }
    assert overall_outcome(phases) == "xpassed"


def test_setup_xfail_with_no_call_is_xfailed():
    # An xfail that triggers during setup (say, xfail plus a setup-time skip)
    # carries wasxfail on the setup report and there is no call report. That
    # still counts as xfailed.
    phases = {"setup": _phase("skipped", wasxfail="xfail at setup")}
    assert overall_outcome(phases) == "xfailed"


def test_strict_xpass_reports_as_failed():
    # A strict=True xfail that passes is reported by pytest as a plain call
    # failure with no wasxfail, so the fold must call it "failed": the spec's
    # failure rule wins. This matches the strict xpass in examples/test_outcomes.
    phases = {"setup": _phase("passed"), "call": _phase("failed")}
    assert overall_outcome(phases) == "failed"


# --- incomplete (the safety net) ------------------------------------------


def test_setup_passed_no_call_is_incomplete():
    # Setup ran fine but the call report never arrived (the run was killed
    # mid-test). That is "incomplete", never a silent "passed".
    phases = {"setup": _phase("passed")}
    assert overall_outcome(phases) == "incomplete"


def test_empty_phases_is_incomplete():
    # No reports at all (a test that was selected but the run died before it):
    # incomplete, not passed.
    assert overall_outcome({}) == "incomplete"


def test_only_teardown_passed_is_incomplete():
    # A degenerate set with no setup or call signal we can trust is incomplete:
    # we cannot substantiate a pass without a passing call.
    phases = {"teardown": _phase("passed")}
    assert overall_outcome(phases) == "incomplete"


# --- precedence sanity: failure beats wasxfail/skip -----------------------


def test_failed_call_beats_wasxfail():
    # If a call is both failed and carries wasxfail, the failure scan runs first.
    # (Defensive: this shouldn't happen in practice, but it pins the precedence.)
    phases = {"call": _phase("failed", wasxfail="reason")}
    assert overall_outcome(phases) == "failed"


@pytest.mark.parametrize(
    "phases, expected",
    [
        ({"setup": _phase("passed"), "call": _phase("passed")}, "passed"),
        ({"setup": _phase("passed"), "call": _phase("failed")}, "failed"),
        ({"setup": _phase("failed")}, "error"),
        ({"setup": _phase("passed"), "call": _phase("skipped")}, "skipped"),
        ({"setup": _phase("skipped")}, "skipped"),
        (
            {"setup": _phase("passed"), "call": _phase("skipped", wasxfail="x")},
            "xfailed",
        ),
        (
            {"setup": _phase("passed"), "call": _phase("passed", wasxfail="x")},
            "xpassed",
        ),
        ({"setup": _phase("passed")}, "incomplete"),
    ],
)
def test_outcome_table(phases, expected):
    """One table covering every §3.1 outcome class in a single readable place."""
    assert overall_outcome(phases) == expected
