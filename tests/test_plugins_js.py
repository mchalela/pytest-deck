"""Tests for the frontend plugin-panel store
(`frontend/src/lib/plugins.svelte.js`).

Same node-shim pattern as test_results_js.py / test_diff_js.py: load the REAL
module under node with the single ``$state(`` neutralized to an identity
wrapper, replay named ops from stdin, snapshot the store + returns as JSON.

The store is transport-free (App.svelte fetches /api/plugins and calls
setPlugins) — a reappearing api import fails the shim guard here.

``node`` is required; tests skip cleanly if it's absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
_PLUGINS_JS = _LIB / "plugins.svelte.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def _run_node(script, stdin_payload):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed (rc={proc.returncode}):\n{proc.stderr}"
    return json.loads(proc.stdout)


_HARNESS = """
import {{ plugins, setPlugins, setEnabled, setValue,
  runPayload, affectsCollect, collectPluginIds,
  applySuggestion }} from {plugins_url};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {{
  const ops = JSON.parse(raw);
  const returns = [];
  for (const op of ops) {{
    switch (op.op) {{
      case "setPlugins": setPlugins(op.list, op.leftovers ?? []); break;
      case "setEnabled": setEnabled(op.id, op.on); break;
      case "setValue": setValue(op.id, op.key, op.value); break;
      case "setExtraArgs": plugins.extraArgs = op.value; break;
      case "applySuggestion": applySuggestion(op.index); break;
      case "runPayload": returns.push(runPayload()); break;
      case "affectsCollect": returns.push(affectsCollect(op.id)); break;
      case "collectPluginIds": returns.push(collectPluginIds()); break;
      default: throw new Error("unknown op " + op.op);
    }}
  }}
  process.stdout.write(JSON.stringify({{
    available: plugins.available, list: plugins.list, byId: plugins.byId,
    extraArgs: plugins.extraArgs, suggestions: plugins.suggestions,
    returns: returns,
  }}));
}});
"""


def _neutralized_module(tmp_path):
    """Copy plugins.svelte.js with its single $state( → identity."""
    src = _PLUGINS_JS.read_text()
    assert src.count("$state(") == 1, "expected exactly one $state( to neutralize"
    out = tmp_path / "plugins_neutralized.mjs"
    out.write_text(src.replace("$state(", "("))
    return out


def _run_ops(tmp_path, ops):
    mod = _neutralized_module(tmp_path)
    script = _HARNESS.format(plugins_url=json.dumps(mod.as_uri()))
    return _run_node(script, ops)


# The coverage manifest as served by GET /api/plugins.
_COV = {
    "id": "pytest_cov",
    "label": "Coverage (pytest-cov)",
    "dist": "pytest-cov",
    "scope": "run",
    "fields": [
        {"key": "source", "label": "Source (--cov=)", "type": "string", "default": ""},
        {"key": "branch", "label": "Branch coverage", "type": "bool", "default": False},
    ],
}

# A fieldless structural plugin: the switch is the whole UI.
_BARE = {
    "id": "pytest_asyncio",
    "label": "asyncio",
    "dist": "pytest-asyncio",
    "scope": "run",
    "fields": [],
}

# An installed-but-disabled manifest, shown greyed/inert with the reason.
_DISABLED = {
    "id": "pytest_rerunfailures",
    "label": "Reruns",
    "dist": "pytest-rerunfailures",
    "scope": "run",
    "fields": [{"key": "reruns", "label": "Reruns", "type": "string", "default": "2"}],
    "render": None,
    "disabled_reason": "Reruns — needs attempts model",
}


# --- shim guards ------------------------------------------------------------


def test_plugins_store_uses_exactly_one_state_rune():
    src = _PLUGINS_JS.read_text()
    assert src.count("$state(") == 1
    for other in ("$derived", "$effect", "$props", "$bindable", "$inspect"):
        assert other not in src, f"new rune {other} — revisit the node shim"


def test_plugins_store_is_transport_free():
    src = _PLUGINS_JS.read_text()
    assert '"./api.js"' not in src, "plugins.svelte.js must stay transport-free"
    assert "fetch(" not in src


# --- setPlugins / defaults ----------------------------------------------------


@requires_node
def test_set_plugins_initializes_state_from_field_defaults(tmp_path):
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [_COV, _BARE]}])
    assert res["available"] is True
    assert [p["id"] for p in res["list"]] == ["pytest_cov", "pytest_asyncio"]
    # Every plugin starts disabled; values seeded from the manifest defaults.
    assert res["byId"]["pytest_cov"] == {
        "enabled": False,
        "values": {"source": "", "branch": False},
    }
    assert res["byId"]["pytest_asyncio"] == {"enabled": False, "values": {}}


@requires_node
def test_set_plugins_missing_default_falls_back_by_type(tmp_path):
    manifest = {
        "id": "p",
        "label": "P",
        "dist": "p",
        "scope": "run",
        "fields": [
            {"key": "s", "label": "S", "type": "string"},
            {"key": "b", "label": "B", "type": "bool"},
            {"key": "d", "label": "D", "type": "string", "default": "x"},
        ],
    }
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [manifest]}])
    assert res["byId"]["p"]["values"] == {"s": "", "b": False, "d": "x"}


@requires_node
def test_set_plugins_again_resets_enabled_and_values(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV]},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "setValue", "id": "pytest_cov", "key": "source", "value": "src"},
            {"op": "setPlugins", "list": [_COV]},
        ],
    )
    assert res["byId"]["pytest_cov"] == {
        "enabled": False,
        "values": {"source": "", "branch": False},
    }


# --- runPayload -----------------------------------------------------------------


@requires_node
def test_run_payload_empty_when_nothing_enabled_and_no_extra_args(tmp_path):
    # Neither key present: the request body is byte-identical to alpha's.
    res = _run_ops(
        tmp_path,
        [{"op": "setPlugins", "list": [_COV, _BARE]}, {"op": "runPayload"}],
    )
    assert res["returns"] == [{}]


@requires_node
def test_run_payload_includes_only_enabled_plugins_with_form_values(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV, _BARE]},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "setValue", "id": "pytest_cov", "key": "source", "value": "mypkg"},
            {"op": "setValue", "id": "pytest_cov", "key": "branch", "value": True},
            {"op": "runPayload"},
        ],
    )
    # Presence == enabled; _BARE stays disabled and is omitted entirely.
    assert res["returns"] == [
        {"plugins": {"pytest_cov": {"source": "mypkg", "branch": True}}}
    ]


@requires_node
def test_run_payload_fieldless_plugin_enabled_sends_empty_config(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV, _BARE]},
            {"op": "setEnabled", "id": "pytest_asyncio", "on": True},
            {"op": "runPayload"},
        ],
    )
    assert res["returns"] == [{"plugins": {"pytest_asyncio": {}}}]


@requires_node
def test_run_payload_disabling_removes_the_plugin_but_keeps_edits(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV]},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "setValue", "id": "pytest_cov", "key": "source", "value": "mypkg"},
            {"op": "setEnabled", "id": "pytest_cov", "on": False},
            {"op": "runPayload"},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "runPayload"},
        ],
    )
    # Off means omitted; back on, the edited value survived the toggle.
    assert res["returns"] == [
        {},
        {"plugins": {"pytest_cov": {"source": "mypkg", "branch": False}}},
    ]


@requires_node
def test_run_payload_extra_args_blank_or_whitespace_omitted(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV]},
            {"op": "runPayload"},
            {"op": "setExtraArgs", "value": "   "},
            {"op": "runPayload"},
            {"op": "setExtraArgs", "value": "  -x --tb=short "},
            {"op": "runPayload"},
        ],
    )
    # Blank or whitespace leaves the key out; non-blank passes through trimmed,
    # and it rides alone (no `plugins` key) when no plugin is enabled.
    assert res["returns"] == [{}, {}, {"extra_args": "-x --tb=short"}]


@requires_node
def test_run_payload_plugins_and_extra_args_combine(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV]},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "setExtraArgs", "value": "-x"},
            {"op": "runPayload"},
        ],
    )
    assert res["returns"] == [
        {
            "plugins": {"pytest_cov": {"source": "", "branch": False}},
            "extra_args": "-x",
        }
    ]


# --- unknown-id robustness -------------------------------------------------------


@requires_node
def test_set_enabled_and_set_value_ignore_unknown_ids_and_keys(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV]},
            {"op": "setEnabled", "id": "nope", "on": True},
            {"op": "setValue", "id": "nope", "key": "x", "value": 1},
            {"op": "setValue", "id": "pytest_cov", "key": "bogus", "value": 1},
            {"op": "runPayload"},
        ],
    )
    # No crash, no state invented: unknown ids/keys are silent no-ops.
    assert res["returns"] == [{}]
    assert res["byId"]["pytest_cov"]["values"] == {"source": "", "branch": False}
    assert "nope" not in res["byId"]


# --- disabled manifests --------------------------------------------------


@requires_node
def test_disabled_manifest_is_listed_with_reason(tmp_path):
    # A disabled manifest is still shown (distinct from not-installed): it stays
    # in the list with its disabled_reason for the component to grey out and
    # explain.
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [_COV, _DISABLED]}])
    ids = [p["id"] for p in res["list"]]
    assert "pytest_rerunfailures" in ids
    disabled = next(p for p in res["list"] if p["id"] == "pytest_rerunfailures")
    assert disabled["disabled_reason"] == "Reruns — needs attempts model"


@requires_node
def test_disabled_manifest_cannot_be_enabled(tmp_path):
    # setEnabled is a no-op for a disabled manifest, and it is never included in
    # the run payload even if enable is attempted.
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_DISABLED]},
            {"op": "setEnabled", "id": "pytest_rerunfailures", "on": True},
            {"op": "runPayload"},
        ],
    )
    assert res["byId"]["pytest_rerunfailures"]["enabled"] is False
    assert res["returns"] == [{}]  # not sent


# --- collect-scope trigger + ?plugins= ids -----------------------

# A structural scope="both" manifest: its switch is collect-relevant.
_DJANGO = {
    "id": "django",
    "label": "Django (pytest-django)",
    "dist": "pytest-django",
    "scope": "both",
    "fields": [],
}

# A disabled scope="both" manifest; it must never trigger or ride collect.
_DISABLED_BOTH = {
    "id": "stuck",
    "label": "Stuck",
    "dist": "stuck",
    "scope": "both",
    "fields": [],
    "disabled_reason": "not yet",
}


@requires_node
def test_affects_collect_true_only_for_collect_scoped(tmp_path):
    # The re-collect trigger predicate PluginSwitch consults on toggle: it fires
    # for scope both/collect switches, not for run-only toggles (pytest_cov),
    # disabled manifests, or unknown ids.
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV, _DJANGO, _DISABLED_BOTH]},
            {"op": "affectsCollect", "id": "django"},
            {"op": "affectsCollect", "id": "pytest_cov"},
            {"op": "affectsCollect", "id": "stuck"},
            {"op": "affectsCollect", "id": "nope"},
        ],
    )
    assert res["returns"] == [True, False, False, False]


@requires_node
def test_collect_plugin_ids_only_enabled_collect_scoped(tmp_path):
    # The ?plugins= fragment: enabled plugins whose scope is both/collect. A
    # run-only enable (pytest_cov) never rides collect; toggling off removes
    # the id.
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV, _DJANGO]},
            {"op": "collectPluginIds"},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "collectPluginIds"},
            {"op": "setEnabled", "id": "django", "on": True},
            {"op": "collectPluginIds"},
            {"op": "setEnabled", "id": "django", "on": False},
            {"op": "collectPluginIds"},
        ],
    )
    assert res["returns"] == [[], [], ["django"], []]


@requires_node
def test_collect_plugin_ids_exclude_disabled_manifest(tmp_path):
    # Even a forced-enable attempt on a disabled scope-both manifest never
    # produces a collect id (setEnabled already refuses; belt and suspenders).
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_DISABLED_BOTH, _DJANGO]},
            {"op": "setEnabled", "id": "stuck", "on": True},
            {"op": "setEnabled", "id": "django", "on": True},
            {"op": "collectPluginIds"},
        ],
    )
    assert res["returns"] == [["django"]]


def test_plugin_switch_gates_recollect_on_affects_collect():
    # The component wiring pin: the toggle handler consults affectsCollect and
    # only then fires the oncollectchange callback (App's debounced
    # requestCollect). Field edits (setValue) never touch it.
    src = (_LIB.parent / "components" / "PluginSwitch.svelte").read_text()
    assert "affectsCollect(plugin.id)" in src
    assert "oncollectchange" in src


def test_app_wires_collect_ids_and_recollect_callback():
    # App passes the enabled collect-scoped ids into collect() and hands the
    # existing debounced entry point to the panel as the re-collect trigger.
    src = (_LIB.parent / "App.svelte").read_text()
    assert "collect(collectPluginIds())" in src
    assert "oncollectchange={requestCollect}" in src


@requires_node
def test_enabled_plugins_coexist_with_a_disabled_one(tmp_path):
    # A disabled manifest doesn't block enabling the others.
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV, _DISABLED]},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "setEnabled", "id": "pytest_rerunfailures", "on": True},
            {"op": "runPayload"},
        ],
    )
    assert res["returns"] == [
        {"plugins": {"pytest_cov": {"source": "", "branch": False}}}
    ]


# --- ini_defaults seeding + leftover suggestions ------------------

# _COV as served with harvested ini defaults (addopts had --cov=mypkg).
_COV_INI = {**_COV, "ini_defaults": {"source": "mypkg"}}


@requires_node
def test_ini_defaults_seed_over_manifest_defaults(tmp_path):
    # The pinned precedence at seed time: an ini_default beats the manifest
    # default (branch has no ini_default, so its manifest default holds).
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [_COV_INI]}])
    assert res["byId"]["pytest_cov"]["values"] == {
        "source": "mypkg",
        "branch": False,
    }


@requires_node
def test_user_edit_wins_over_ini_default(tmp_path):
    # The pinned precedence after seed: a user edit beats the ini_default.
    # Clearing the prefilled field sticks; nothing resurrects the ini value (the
    # server excludes harvested tokens from re-admission for the same reason).
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV_INI]},
            {"op": "setValue", "id": "pytest_cov", "key": "source", "value": ""},
            {"op": "setEnabled", "id": "pytest_cov", "on": True},
            {"op": "runPayload"},
        ],
    )
    assert res["returns"] == [
        {"plugins": {"pytest_cov": {"source": "", "branch": False}}}
    ]


@requires_node
def test_set_plugins_again_reseeds_from_ini_defaults(tmp_path):
    # Re-install resets everything, including back to the ini seed (same rule
    # as the existing reset-to-defaults pin, documented precedence).
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV_INI]},
            {"op": "setValue", "id": "pytest_cov", "key": "source", "value": "other"},
            {"op": "setPlugins", "list": [_COV_INI]},
        ],
    )
    assert res["byId"]["pytest_cov"]["values"]["source"] == "mypkg"


@requires_node
def test_type_mismatched_ini_default_is_ignored(tmp_path):
    # Defensive: a wrong-typed ini_default falls back to the manifest default
    # instead of corrupting the form (server emits correct types).
    bad = {**_COV, "ini_defaults": {"source": True, "branch": "yes"}}
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [bad]}])
    assert res["byId"]["pytest_cov"]["values"] == {"source": "", "branch": False}


@requires_node
def test_suggestions_installed_and_default_empty(tmp_path):
    res = _run_ops(tmp_path, [{"op": "setPlugins", "list": [_COV]}])
    assert res["suggestions"] == []
    res = _run_ops(
        tmp_path,
        [{"op": "setPlugins", "list": [_COV], "leftovers": ["-v", "--tb=short"]}],
    )
    assert res["suggestions"] == ["-v", "--tb=short"]


@requires_node
def test_apply_suggestion_appends_to_extra_args_and_clears_chip(tmp_path):
    # One click = typing the token into extra-args: it lands there (spaced
    # onto any existing content), leaves the suggestion list, and rides the
    # run payload like any user-typed extra args. Others stay suggested.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "setPlugins",
                "list": [_COV],
                "leftovers": ["-v", "--tb=short"],
            },
            {"op": "setExtraArgs", "value": "-x"},
            {"op": "applySuggestion", "index": 0},
            {"op": "runPayload"},
        ],
    )
    assert res["extraArgs"] == "-x -v"
    assert res["suggestions"] == ["--tb=short"]
    assert res["returns"] == [{"extra_args": "-x -v"}]


@requires_node
def test_apply_suggestion_quotes_tokens_that_need_it(tmp_path):
    # A token carrying whitespace (a quoted ini value) must survive the server
    # shlex round trip as one token, so it is re-quoted on the way in.
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV], "leftovers": ["not slow"]},
            {"op": "applySuggestion", "index": 0},
        ],
    )
    assert res["extraArgs"] == '"not slow"'
    assert res["suggestions"] == []


@requires_node
def test_apply_suggestion_out_of_range_index_is_a_noop(tmp_path):
    res = _run_ops(
        tmp_path,
        [
            {"op": "setPlugins", "list": [_COV], "leftovers": ["-v"]},
            {"op": "applySuggestion", "index": 5},
            {"op": "applySuggestion", "index": -1},
        ],
    )
    assert res["extraArgs"] == ""
    assert res["suggestions"] == ["-v"]


@requires_node
def test_apply_suggestion_removes_the_clicked_index_with_duplicates(tmp_path):
    # ini addopts can repeat a token: `-p no:cacheprovider -p myplugin` yields
    # two `-p` leftovers, and `--maxfail=2 … --maxfail=2` two more. These are
    # preserved verbatim (never deduped: the "faithful to pytest, never silently
    # dropped" promise). applySuggestion removes by index, so clicking the
    # second `-p` chip removes that one, not the first; a value-based indexOf
    # would drop the wrong duplicate and desync the chips from the DOM.
    # (Component mount isn't in this rig, so render-key uniqueness is proven by
    # construction, the panel keying each chip by `i + " " + tok`, unique per
    # position, and pinned here at the store level: index removal is exact.)
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "setPlugins",
                "list": [_COV],
                "leftovers": ["-p", "no:cacheprovider", "-p", "myplugin"],
            },
            {"op": "applySuggestion", "index": 2},  # the second `-p`
        ],
    )
    # index 2 (`-p`) removed; the first `-p` and everything else stay, in order.
    assert res["suggestions"] == ["-p", "no:cacheprovider", "myplugin"]
    assert res["extraArgs"] == "-p"


@requires_node
def test_suggestion_keys_are_unique_with_duplicate_leftovers(tmp_path):
    # The store shape that guarantees the panel's render key is unique: chips are
    # keyed `i + " " + tok`; with duplicate tokens present the index disambiguates,
    # so no two keys collide (dev build: no each_key_duplicate throw; prod: no
    # mis-keyed reconciliation). Verified here by reproducing the key formula.
    res = _run_ops(
        tmp_path,
        [
            {
                "op": "setPlugins",
                "list": [_COV],
                "leftovers": [
                    "-p",
                    "myplugin",
                    "-p",
                    "other",
                    "--maxfail=2",
                    "--maxfail=2",
                ],
            },
        ],
    )
    sugg = res["suggestions"]
    keys = [f"{i} {tok}" for i, tok in enumerate(sugg)]
    assert len(keys) == len(set(keys)), f"duplicate render keys: {keys}"


def test_plugin_panel_wires_suggestions_to_apply():
    # Component pin: the suggestion chips exist and route through the store's
    # applySuggestion (never mutate extraArgs inline).
    src = (_LIB.parent / "components" / "PluginPanel.svelte").read_text()
    assert "applySuggestion(" in src
    assert "plugins.suggestions" in src


def test_plugin_panel_keys_suggestions_by_index_not_value():
    # The each block must never key by bare token value: ini addopts can repeat
    # a token, and a value key collides (dev: each_key_duplicate throw; prod:
    # mis-keyed reconciliation). Pin the index-composite key and the index-based
    # click.
    src = (_LIB.parent / "components" / "PluginPanel.svelte").read_text()
    assert "(tok)}" not in src, "suggestions keyed by bare value — duplicates collide"
    assert "as tok, i (i + " in src
    assert "applySuggestion(i)" in src


def test_app_passes_leftovers_into_set_plugins():
    src = (_LIB.parent / "App.svelte").read_text()
    assert "setPlugins(j.plugins, j.ini_leftovers)" in src
