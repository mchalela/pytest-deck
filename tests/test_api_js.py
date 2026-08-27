"""Tests for the collect() error classification in `frontend/src/lib/api.js`.

collect() must expose the HTTP status on the thrown error (as startRun already
does) so App's doCollect can tell a 400 validation reject (server alive,
unknown/disabled ?plugins= id → keep the tree, status-line message) from a 500
hard collect failure (subprocess broke → the full "Collection failed" panel).
A non-JSON body or a network failure must keep throwing WITHOUT a .status —
that class stays on the hard/server-down path.

api.js is a plain module whose only environment dependency is the global
`fetch`, which node ≥18 has — the harness swaps it for a scripted stub, no
shimming needed. Skips cleanly without node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_API_JS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "api.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)

# Each stdin case scripts one fetch response; the harness records what
# collect() returned or threw ({message, status, hasStatus}) plus the URL the
# stub saw (pins the ?plugins= fragment riding along on the reject case).
_HARNESS = """
import {{ collect }} from {api_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", async () => {{
  const cases = JSON.parse(raw);
  const out = [];
  for (const c of cases) {{
    let seenUrl = null;
    globalThis.fetch = async (url) => {{
      seenUrl = url;
      return {{
        ok: c.ok,
        status: c.status,
        json: async () => {{
          if (c.jsonThrows) throw new SyntaxError("Unexpected token <");
          return c.body;
        }},
      }};
    }};
    try {{
      const json = await collect(c.pluginIds ?? null);
      out.push({{ threw: false, json, url: seenUrl }});
    }} catch (e) {{
      out.push({{
        threw: true,
        message: e.message,
        status: e.status ?? null,
        hasStatus: "status" in e,
        url: seenUrl,
      }});
    }}
  }}
  process.stdout.write(JSON.stringify(out));
}});
"""


def _run_cases(cases):
    script = _HARNESS.format(api_url=json.dumps(_API_JS.as_uri()))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    return json.loads(proc.stdout)


@requires_node
def test_collect_400_reject_carries_status_and_backend_message():
    # The validation-reject class: server alive, answering 400 with {"error"}.
    (res,) = _run_cases(
        [
            {
                "ok": False,
                "status": 400,
                "body": {"error": "plugin 'nope' is not available"},
                "pluginIds": ["nope"],
            }
        ]
    )
    assert res["threw"] is True
    assert res["message"] == "plugin 'nope' is not available"
    assert res["status"] == 400
    assert res["url"] == "/api/collect?plugins=nope"


@requires_node
def test_collect_500_hard_failure_carries_status_too():
    # Subprocess-level failure: still throws with the backend's message; App
    # routes anything outside 4xx to the hard "Collection failed" panel.
    (res,) = _run_cases(
        [
            {
                "ok": False,
                "status": 500,
                "body": {"error": "pytest collection failed (exit code 3)."},
            }
        ]
    )
    assert res["threw"] is True
    assert res["message"] == "pytest collection failed (exit code 3)."
    assert res["status"] == 500
    assert res["url"] == "/api/collect"  # no ids, so no ?plugins= fragment


@requires_node
def test_collect_non_json_body_throws_without_status():
    # res.json() throwing (proxy error page, dead server mid-response) must
    # keep the statusless throw; App treats a missing .status as the hard path.
    (res,) = _run_cases([{"ok": False, "status": 502, "jsonThrows": True}])
    assert res["threw"] is True
    assert res["hasStatus"] is False


@requires_node
def test_collect_ok_returns_payload_unchanged():
    payload = {"tree": [], "markers": [], "total": 0, "errors": []}
    (res,) = _run_cases([{"ok": True, "status": 200, "body": payload}])
    assert res["threw"] is False
    assert res["json"] == payload


# --- collectFailureKind (the Collection-failed panel copy split) ----------
#
# doCollect routes on this: "reject" gives a status-line message (tree kept);
# "subprocess" gives the full panel with the terminal-parity claim; "network"
# gives the panel without it (pytest never ran, so claiming "same error as your
# terminal" would be a lie for a dead server or a proxy error page).

_KIND_HARNESS = """
import {{ collectFailureKind }} from {api_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const cases = JSON.parse(raw);
  const out = cases.map((c) => {{
    const err = new Error(c.message ?? "boom");
    if ("status" in c) err.status = c.status;
    return collectFailureKind(err);
  }});
  process.stdout.write(JSON.stringify(out));
}});
"""


def _kinds(cases):
    script = _KIND_HARNESS.format(api_url=json.dumps(_API_JS.as_uri()))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    return json.loads(proc.stdout)


@requires_node
def test_failure_kind_4xx_is_reject():
    assert _kinds([{"status": 400}, {"status": 404}, {"status": 422}]) == [
        "reject",
        "reject",
        "reject",
    ]


@requires_node
def test_failure_kind_5xx_is_subprocess():
    # The real collect-subprocess failure, the only class whose panel copy
    # may claim parity with terminal pytest.
    assert _kinds([{"status": 500}, {"status": 503}]) == [
        "subprocess",
        "subprocess",
    ]


@requires_node
def test_failure_kind_statusless_throw_is_network():
    # fetch-throw (server down) and non-JSON body (proxy error page) both
    # surface as a statusless Error: the network class.
    assert _kinds([{"message": "Failed to fetch"}]) == ["network"]
