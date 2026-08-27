"""Derive a single display outcome from a test's per-phase reports.

Relocated from the prototype ``collector.py`` so the server (and its tests) and
the JavaScript frontend store share **one** spec. The frontend ports this
function verbatim; the Python version stays as the oracle the server tests check
the JS against.

A ``phases`` dict maps ``setup``/``call``/``teardown`` to a small dict with at
least ``outcome`` (``passed``/``failed``/``skipped``) and optionally ``wasxfail``
(a string reason on xfail/xpass reports, else ``None``).
"""


def overall_outcome(phases):
    """Fold per-phase reports into one display outcome.

    Outcomes: passed / failed / error / skipped / xfailed / xpassed / incomplete.

    - a failed *call* gives ``"failed"``; a failed setup or teardown gives
      ``"error"``
    - xfail/xpass (the report carries ``wasxfail``) gives ``"xfailed"`` or
      ``"xpassed"``
    - a skipped call (or a setup-level skip with no call) gives ``"skipped"``
    - setup passed but the call report never arrived (the run was killed or
      crashed mid-test) gives ``"incomplete"``, never a silent ``"passed"``
    """
    # Failures first: a call failure is a test failure; setup/teardown is an error.
    for when in ("setup", "call", "teardown"):
        ph = phases.get(when)
        if ph and ph.get("outcome") == "failed":
            return "failed" if when == "call" else "error"

    call = phases.get("call")
    setup = phases.get("setup")

    # xfail / xpass: the only signal is ``wasxfail`` on a phase.
    if call and call.get("wasxfail") is not None:
        # A passing call that "was expected to fail" is an unexpected pass.
        return "xpassed" if call.get("outcome") == "passed" else "xfailed"
    if setup and setup.get("wasxfail") is not None and not call:
        return "xfailed"

    if call and call.get("outcome") == "skipped":
        return "skipped"
    if not call and setup and setup.get("outcome") == "skipped":
        return "skipped"

    # Setup passed but no call report ever arrived, so the test never completed.
    if setup and setup.get("outcome") == "passed" and not call:
        return "incomplete"

    if call and call.get("outcome") == "passed":
        return "passed"

    # No call and no setup signal we recognize: call it incomplete rather than
    # claim a pass we can't substantiate.
    return "passed" if call else "incomplete"
