// Verbatim JS port of pytest_deck/outcome.py `overall_outcome`.
//
// LOAD-BEARING: this must match the Python oracle exactly (the priority order and
// every branch). The server tests check the JS against the Python implementation
// (tests/test_outcome_js_parity.py). Keep the two in lockstep — change both or neither.
//
// `phases` maps "setup"/"call"/"teardown" to {outcome, wasxfail?}. `outcome` is
// "passed"/"failed"/"skipped"; `wasxfail` is a string reason on xfail/xpass, else
// null/undefined.
export function overallOutcome(phases) {
  phases = phases || {};

  // Failures first: a call failure is a test failure; setup/teardown is an error.
  for (const when of ["setup", "call", "teardown"]) {
    const ph = phases[when];
    if (ph && ph.outcome === "failed") {
      return when === "call" ? "failed" : "error";
    }
  }

  const call = phases.call;
  const setup = phases.setup;

  // xfail / xpass — detectable only via `wasxfail` on a phase.
  if (call && call.wasxfail != null) {
    // A passing call that "was expected to fail" is an unexpected pass.
    return call.outcome === "passed" ? "xpassed" : "xfailed";
  }
  if (setup && setup.wasxfail != null && !call) {
    return "xfailed";
  }

  if (call && call.outcome === "skipped") {
    return "skipped";
  }
  if (!call && setup && setup.outcome === "skipped") {
    return "skipped";
  }

  // Setup ran (passed) but no call report → the test never completed.
  if (setup && setup.outcome === "passed" && !call) {
    return "incomplete";
  }

  if (call && call.outcome === "passed") {
    return "passed";
  }

  // No call, no setup signal we recognize — treat as incomplete rather than
  // claiming a pass we can't substantiate.
  return call ? "passed" : "incomplete";
}
