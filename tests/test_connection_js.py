"""Tests for the SSE frame parse guard in `frontend/src/lib/connection.js`.

R7: `addEventListener("error", ...)` on an EventSource receives BOTH the named
`error` SSE run-event (JSON data) AND the browser's connection-level `error`
event fired on blips, which has NO data — so an unguarded `JSON.parse(e.data)`
throws `SyntaxError` on every drop. `parseEventData` is the pure guard: it
returns the parsed payload for a well-formed frame and `null` for data-less or
malformed ones, and every named listener routes through it.

`connection.js` imports the real transport (`./api.js`) and store
(`./results.svelte.js`), so the node shim re-points both at inert stubs; the
module body only defines functions, so nothing else needs a browser. Same
node-shell pattern as `test_results_js.py`; skips cleanly without node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
_CONNECTION_JS = _LIB / "connection.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

# Inert stand-ins for connection.js's two imports (never called by these tests:
# parseEventData is pure and connectEvents is never invoked).
_API_STUB = "export const runActive = () => Promise.resolve(false);\n"
_RESULTS_STUB = "export const run = {};\n" + "".join(
    f"export const {name} = () => {{}};\n"
    for name in (
        "markServerDown",
        "markReconnecting",
        "unstickOrphanedRun",
        "clearServerDown",
        "onStarted",
        "onReport",
        "onWarning",
        "onConsole",
        "onFinished",
        "onCancelled",
        "onError",
        "onPluginData",
        "onPluginEmpty",
    )
)

# Exercises the guard exactly as the listener does: a `null` return means the
# applier is not called. `undefined` is the R7 blip case (the connection-level
# `error` event has no `data`).
_HARNESS = """
import {{ parseEventData }} from {connection_url};
const out = [
  parseEventData(undefined),          // R7 blip: connection-level error event
  parseEventData(null),
  parseEventData(""),                 // empty frame
  parseEventData("not json"),         // malformed frame
  parseEventData("null"),             // valid JSON null → still skipped
  parseEventData('{{"run_id":"r1","exit_code":4}}'),
];
process.stdout.write(JSON.stringify(out));
"""


def _shimmed_connection_module(tmp_path):
    """Copy connection.js with its two imports re-pointed at inert stubs."""
    api_stub = tmp_path / "api_stub.mjs"
    api_stub.write_text(_API_STUB)
    results_stub = tmp_path / "results_stub.mjs"
    results_stub.write_text(_RESULTS_STUB)

    src = _CONNECTION_JS.read_text()
    assert '"./api.js"' in src and '"./results.svelte.js"' in src
    src = src.replace('"./api.js"', json.dumps(api_stub.as_uri()))
    src = src.replace('"./results.svelte.js"', json.dumps(results_stub.as_uri()))

    out = tmp_path / "connection_shimmed.mjs"
    out.write_text(src)
    return out


def test_connection_js_file_exists():
    assert _CONNECTION_JS.is_file()


def test_named_listeners_route_through_the_guard():
    """Pin that the listener wiring actually uses parseEventData — a rewrite
    back to bare `JSON.parse(e.data)` would reintroduce the R7 blip crash."""
    src = _CONNECTION_JS.read_text()
    assert "parseEventData(e.data)" in src


@requires_node
def test_parse_event_data_skips_blip_frames_and_parses_real_ones(tmp_path):
    mod = _shimmed_connection_module(tmp_path)
    script = _HARNESS.format(connection_url=json.dumps(mod.as_uri()))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    out = json.loads(proc.stdout)
    # Every degenerate frame comes back null (skipped); the real frame parses
    # intact. (JSON.stringify maps the leading `undefined` array slot to null
    # too.)
    assert out == [None, None, None, None, None, {"run_id": "r1", "exit_code": 4}]
