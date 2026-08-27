"""The one-path-per-token ini-addopts pipeline.

P15 neutralizes ini ``addopts`` on every deck child (``-o addopts=``); this
slice gives every stripped token back through EXACTLY ONE explicit path:

1. HARVEST  — matches an available manifest's field arg-template → prefills
   the form (``ini_defaults`` on /api/plugins). The form stays authoritative:
   harvested tokens never reach argv themselves, so a user-cleared field is
   never invisibly resurrected.
2. RE-ADMIT — remaining SELF-CONTAINED tokens (``--flag``/``--flag=value``)
   inside an ENABLED manifest's ``flags`` namespace are appended at run time,
   after plugin tokens, before user extra-args (the P11 re-assert still lands
   last). ``RESERVED_FLAGS`` never re-admit, whatever any namespace grants.
3. LEFTOVER — everything else surfaces as an extra-args suggestion
   (``ini_leftovers``), applied only on user click. Never silently dropped.

Parsing rides ``rootdir.read_ini_addopts`` with the PINNED coercion recipe
(verified 2026-08-11 on pytest 8.4.2 + 9.1.1), shared with
``read_ini_pythonpath`` via ``_ini_tokens`` so the two readers cannot drift on
the pytest-9 native-TOML edge. Ini ONLY — env ``PYTEST_ADDOPTS`` stays popped.
"""

import asyncio

import httpx
import pytest

import pytest_deck.server as server_mod
from pytest_deck._subprocess import base_argv
from pytest_deck.manifests import (
    RESERVED_FLAGS,
    ManifestError,
    classify_addopts,
    compile_argv,
    parse_manifest,
)
from pytest_deck.rootdir import _ini_tokens, read_ini_addopts, read_ini_pythonpath
from pytest_deck.runner import RunManager, _Run
from pytest_deck.server import create_app

PYTEST_MAJOR = int(pytest.__version__.split(".")[0])


class FakeConfigValue:
    """pytest 9's ConfigValue shape: .value + .mode (pytest 8 stores bare)."""

    def __init__(self, value, mode):
        self.value = value
        self.mode = mode


# === the pinned coercion recipe (_ini_tokens unit table) ====================


@pytest.mark.parametrize(
    "raw, expected",
    [
        # bare str (pytest 8): shlex.split
        ("--cov=x -v", ["--cov=x", "-v"]),
        # bare list of str (pytest 8 pyproject ini_options): copied
        (["--cov=x", "-v"], ["--cov=x", "-v"]),
        # ConfigValue str, mode=ini (pytest 9 pytest.ini / ini_options): split
        (FakeConfigValue("--cov=x -v", "ini"), ["--cov=x", "-v"]),
        # ConfigValue list, mode=ini: copied
        (FakeConfigValue(["--cov=x"], "ini"), ["--cov=x"]),
        # ConfigValue str, mode=toml (pytest 9 native [tool.pytest]): degrades,
        # since pytest itself TypeErrors on that config and there are no
        # faithful tokens
        (FakeConfigValue("--cov=x -v", "toml"), []),
        # ConfigValue list, mode=toml: copied (the valid native-TOML shape)
        (FakeConfigValue(["--cov=x", "-v"], "toml"), ["--cov=x", "-v"]),
        # unbalanced quote: shlex raises ValueError, so it degrades
        ("--foo 'unclosed", []),
        # multiline ini value joins into one token stream
        ("--cov=x\n-v\n--tb=short", ["--cov=x", "-v", "--tb=short"]),
        # trailing comment: iniconfig only strips full-line comments, so pytest
        # itself sees these bogus tokens; faithful means the same tokens
        ("--cov=x -v  # comment", ["--cov=x", "-v", "#", "comment"]),
        # list with a non-str entry: degrades (never half-coerce)
        (["--cov=x", 3], []),
        (FakeConfigValue(["-v", None], "toml"), []),
        # anything else: degrades
        (3, []),
        (None, []),
        ({"a": 1}, []),
    ],
)
def test_ini_tokens_pinned_recipe(raw, expected):
    assert _ini_tokens(raw) == expected


# === read_ini_addopts against real config files =============================


def test_read_ini_addopts_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov=mypkg -v\n")
    assert read_ini_addopts(tmp_path) == ["--cov=mypkg", "-v"]


def test_read_ini_addopts_pyproject_ini_options_list(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ["--cov=mypkg", "-v"]\n'
    )
    assert read_ini_addopts(tmp_path) == ["--cov=mypkg", "-v"]


def test_read_ini_addopts_pyproject_ini_options_string(tmp_path):
    # ini_options values keep mode="ini" even on pytest 9, so they shlex-split.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--cov=mypkg -v"\n'
    )
    assert read_ini_addopts(tmp_path) == ["--cov=mypkg", "-v"]


def test_read_ini_addopts_multiline_and_quoting(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        '[pytest]\naddopts =\n    --cov=mypkg\n    -k "not slow"\n'
    )
    assert read_ini_addopts(tmp_path) == ["--cov=mypkg", "-k", "not slow"]


def test_read_ini_addopts_trailing_comment_faithful(tmp_path):
    # pytest itself shlex-splits a trailing comment into bogus positionals, and
    # faithful means the same tokens (they classify as leftovers downstream).
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -v  # be loud\n")
    assert read_ini_addopts(tmp_path) == ["-v", "#", "be", "loud"]


def test_read_ini_addopts_unbalanced_quote_degrades(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --foo 'unclosed\n")
    assert read_ini_addopts(tmp_path) == []


def test_read_ini_addopts_absent(tmp_path):
    assert read_ini_addopts(tmp_path) == []  # no ini at all
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: x\n")
    assert read_ini_addopts(tmp_path) == []  # ini without addopts


def test_read_ini_addopts_malformed_ini_degrades(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options\nbroken")
    assert read_ini_addopts(tmp_path) == []


@pytest.mark.skipif(PYTEST_MAJOR < 9, reason="native [tool.pytest] is pytest 9+")
def test_read_ini_addopts_native_toml_modes(tmp_path):
    # pytest 9 native [tool.pytest]: a list keeps its tokens, while a string is
    # a config pytest itself TypeErrors on, so it degrades to [] (the pinned
    # recipe).
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest]\naddopts = ["--cov=mypkg", "-v"]\n'
    )
    assert read_ini_addopts(tmp_path) == ["--cov=mypkg", "-v"]
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\naddopts = "-v"\n')
    assert read_ini_addopts(tmp_path) == []


@pytest.mark.skipif(PYTEST_MAJOR < 9, reason="native [tool.pytest] is pytest 9+")
def test_read_ini_pythonpath_native_toml_string_degrades(tmp_path):
    # The carry-along: read_ini_pythonpath shares the same coercion helper, so
    # the pytest-9 native-TOML string edge degrades there too (it used to be
    # shlex-split where pytest itself TypeErrors).
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\npythonpath = "a b"\n')
    assert read_ini_pythonpath(tmp_path) == []
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\npythonpath = ["sub"]\n')
    assert read_ini_pythonpath(tmp_path) == [str(tmp_path / "sub")]


# === manifest `flags` namespace validation ==================================


def _doc(flags_line=""):
    return f'id = "x"\nlabel = "X"\ndist = "x"\nscope = "run"\n{flags_line}'


def test_parse_flags_valid_literals_and_prefixes():
    m = parse_manifest(_doc('flags = ["--cov", "--cov-*", "-x"]\n'))
    assert m.flags == ("--cov", "--cov-*", "-x")


def test_parse_flags_defaults_empty():
    assert parse_manifest(_doc()).flags == ()


@pytest.mark.parametrize(
    "flags_line, match",
    [
        ('flags = "nope"\n', "must be an array of strings"),
        ("flags = [3]\n", "must be an array of strings"),
        ('flags = ["cov"]\n', "must start with '-'"),
        ('flags = [""]\n', "must start with '-'"),
        ('flags = ["*"]\n', "must start with '-'"),
        # bare or near-bare wildcards would grant the whole option space: rejected
        ('flags = ["-*"]\n', "too broad"),
        ('flags = ["--*"]\n', "too broad"),
        ('flags = ["---*"]\n', "too broad"),
        # '*' is allowed only as a trailing wildcard, once
        ('flags = ["--c*v"]\n', "trailing prefix wildcard"),
        ('flags = ["--cov-**"]\n', "trailing prefix wildcard"),
    ],
)
def test_parse_flags_rejects_bad_entries(flags_line, match):
    with pytest.raises(ManifestError, match=match):
        parse_manifest(_doc(flags_line))
    # Same strictness for untrusted documents: a hostile grant must not get a
    # laxer parse than a curated typo.
    with pytest.raises(ManifestError, match=match):
        parse_manifest(_doc(flags_line), trusted=False)


def test_user_manifest_may_declare_flags():
    # Namespaces are not curated-only: the run-time RESERVED_FLAGS denylist is
    # the security backstop, not the parser.
    m = parse_manifest(_doc('flags = ["--my-plugin-*"]\n'), trusted=False)
    assert m.flags == ("--my-plugin-*",)


# === classification: one path per token ======================================

COV = parse_manifest("""\
id = "pytest_cov"
label = "Coverage"
dist = "pytest-cov"
scope = "run"
flags = ["--cov", "--cov-*", "--no-cov", "--no-cov-on-fail"]

[[fields]]
key = "source"
label = "Source"
type = "string"
default = ""
arg = "--cov={value}"
arg_empty = "--cov"

[[fields]]
key = "branch"
label = "Branch"
type = "bool"
default = false
arg = "--cov-branch"
""")

TIMEOUT = parse_manifest("""\
id = "timeout"
label = "Timeout"
dist = "pytest-timeout"
scope = "run"
flags = ["--timeout", "--timeout-*"]
""")

STUCK = parse_manifest("""\
id = "stuck"
label = "Stuck"
dist = "stuck"
scope = "run"
flags = ["--stuck-*"]
disabled_reason = "needs attempts model"
""")

MANIFESTS = [COV, TIMEOUT, STUCK]


def test_classify_exercises_all_three_paths_at_once():
    tokens = [
        "--cov=mypkg",  # harvested into source
        "--cov-branch",  # harvested into branch (bool)
        "--cov-report=term",  # re-admit candidate (namespace --cov-*)
        "--timeout=30",  # re-admit candidate (namespace --timeout)
        "-v",  # leftover: flag outside every namespace
        "tests/unit",  # leftover: positional
    ]
    policy = classify_addopts(tokens, MANIFESTS)
    assert policy.ini_defaults == {"pytest_cov": {"source": "mypkg", "branch": True}}
    assert policy.namespace == (
        ("--cov-report=term", frozenset({"pytest_cov"})),
        ("--timeout=30", frozenset({"timeout"})),
    )
    assert policy.leftovers == ("-v", "tests/unit")
    # Re-admission is per enabled manifest, with ini order preserved:
    assert policy.readmitted(["pytest_cov", "timeout"]) == [
        "--cov-report=term",
        "--timeout=30",
    ]
    assert policy.readmitted(["pytest_cov"]) == ["--cov-report=term"]
    assert policy.readmitted([]) == []


def test_classify_first_harvest_wins_duplicates_flow_onward():
    # First --cov= claims the field; the duplicate flows to step 2 and rides
    # the namespace (faithful to pytest's repeated-flag semantics).
    policy = classify_addopts(["--cov=a", "--cov=b"], MANIFESTS)
    assert policy.ini_defaults == {"pytest_cov": {"source": "a"}}
    assert policy.readmitted(["pytest_cov"]) == ["--cov=b"]
    assert policy.leftovers == ()


def test_harvested_token_never_readmitted_cleared_field_not_resurrected():
    # The dedup rule: the harvest-matched token is excluded from re-admission
    # regardless of what the form later says, because argv comes from the form
    # alone. A user who clears the prefilled field must get no --cov=mypkg back.
    policy = classify_addopts(["--cov=mypkg"], MANIFESTS)
    assert policy.ini_defaults == {"pytest_cov": {"source": "mypkg"}}
    assert policy.readmitted(["pytest_cov"]) == []
    assert policy.leftovers == ()
    # ...and the cleared-form compile emits the arg_empty fallback, not the
    # ini value (the form is the single source of truth):
    assert compile_argv(COV, {"source": "", "branch": False}) == [
        "-p",
        "pytest_cov",
        "--cov",
    ]


def test_harvest_matches_arg_empty_as_empty_value():
    # A bare `--cov` in the ini is pytest-cov's measure-everything default: it
    # harvests to the empty-string field value (arg_empty compiles it back).
    policy = classify_addopts(["--cov"], MANIFESTS)
    assert policy.ini_defaults == {"pytest_cov": {"source": ""}}
    assert policy.readmitted(["pytest_cov"]) == []


def test_namespace_token_of_unenabled_manifest_is_not_readmitted():
    # Toggle off means actually off. It is not a leftover either: enabling the
    # plugin is its path (suggesting it would compile a plugin flag without
    # its -p, a guaranteed exit 4).
    policy = classify_addopts(["--timeout=30"], MANIFESTS)
    assert policy.readmitted(["pytest_cov"]) == []
    assert policy.leftovers == ()


def test_disabled_manifest_grants_nothing_tokens_fall_to_leftovers():
    # A disabled_reason manifest can never be enabled or compiled, so its
    # namespace and fields are inert and its tokens surface as suggestions.
    policy = classify_addopts(["--stuck-mode=x"], MANIFESTS)
    assert policy.namespace == ()
    assert policy.leftovers == ("--stuck-mode=x",)


def test_space_separated_values_are_never_readmitted():
    # No arity guessing: `--cov-report term` re-admits only the self-contained
    # flag token; the detached value is a positional, hence a leftover.
    policy = classify_addopts(["--cov-report", "term"], MANIFESTS)
    assert policy.readmitted(["pytest_cov"]) == ["--cov-report"]
    assert policy.leftovers == ("term",)


def test_trailing_comment_junk_classifies_as_leftovers():
    policy = classify_addopts(["-v", "#", "be", "loud"], MANIFESTS)
    assert policy.leftovers == ("-v", "#", "be", "loud")
    assert policy.namespace == ()


def test_namespace_literal_covers_eq_form_but_not_prefix_lookalike():
    # "--timeout" (literal) covers --timeout and --timeout=30, never
    # --timeouts=3 (a different option).
    policy = classify_addopts(["--timeout", "--timeout=30", "--timeouts=3"], MANIFESTS)
    assert policy.readmitted(["timeout"]) == ["--timeout", "--timeout=30"]
    assert policy.leftovers == ("--timeouts=3",)


# === RESERVED_FLAGS (SECURITY) ==============================================


def test_reserved_flags_enumerates_the_deck_argv_mechanisms():
    # A RESERVED_ENV-style pin: each entry guards a deck argv invariant. -p is
    # the P11 plugin blocks, -o/--override-ini are the P15 neutralization and
    # P20 pythonpath inject (both last-wins), -c is the config swap, and
    # --rootdir / --import-mode are P12. Guard against silently dropping one.
    assert RESERVED_FLAGS == {
        "-p",
        "-o",
        "--override-ini",
        "-c",
        "--rootdir",
        "--import-mode",
    }


# A namespace broad enough to cover every reserved spelling. The denylist must
# win anyway; that is the point: namespaces cannot grant reserved flags.
GREEDY = parse_manifest(
    _doc(
        'flags = ["-p*", "-o*", "-c*", "--override-ini*", '
        '"--rootdir*", "--import-mode*"]\n'
    )
)


@pytest.mark.parametrize(
    "token",
    [
        "-p",
        "-pxdist",  # glued short form; argparse accepts it
        "-o",
        "-oaddopts=-p xdist",
        "-o=pythonpath=/evil",
        "--override-ini",
        "--override-ini=pythonpath=/evil",
        "-c",
        "-cevil.ini",
        "--rootdir",
        "--rootdir=/evil",
        "--import-mode",
        "--import-mode=prepend",
    ],
)
def test_reserved_spelling_never_readmitted_despite_matching_namespace(token):
    policy = classify_addopts([token], [GREEDY])
    assert policy.readmitted(["x"]) == []
    # Not dropped either: it surfaces as a suggestion (tier-2 is the user's
    # decided-safe surface, where the denylist deliberately does not apply).
    assert policy.leftovers == (token,)


def test_hostile_user_manifest_namespace_cannot_clobber_p20():
    # The attack from the plan: a user manifest granting itself "-o*" plus an
    # ini `addopts = -o pythonpath=/evil`. The manifest parses (namespaces are
    # not curated-only) but the denylist blocks re-admission: no -o ever rides
    # the child argv, so the deck's last-wins -o pythonpath= (P20) stands.
    hostile = parse_manifest(
        'id = "evil"\nlabel = "E"\ndist = "evil"\nscope = "run"\n' 'flags = ["-o*"]\n',
        trusted=False,
    )
    policy = classify_addopts(["-o", "pythonpath=/evil"], [hostile])
    assert policy.readmitted(["evil"]) == []
    # Both tokens surface as click-to-apply suggestions, never a silent drop.
    assert policy.leftovers == ("-o", "pythonpath=/evil")


# --- short-option grouping forgery ----------------------
#
# pytest groups short options (`-sq` == `-s -q`) and the first value-taking one
# swallows the rest of the token as its value at any position, so a reserved
# short letter (`-p`/`-o`/`-c`) riding as a non-leading char smuggles a reserved
# payload. Empirically verified on pytest 9.1.1: `-xopythonpath=/evil` and
# `-sopythonpath=/evil` both apply `-o pythonpath=/evil`; `-xcevil.ini` consumes
# `-c`; `-spxdist` consumes `-p`. A greedy `-x*`/`-s*` namespace would otherwise
# grant re-admission of these, so the cluster scan must reject them.
GREEDY_SHORT = parse_manifest(_doc('flags = ["-x*", "-s*", "-q*"]\n'))


@pytest.mark.parametrize(
    "token",
    [
        "-xopythonpath=/evil",  # the exploit: -x + -o pythonpath=/evil
        "-sopythonpath=",  # -s + -o pythonpath=
        "-qcfoo",  # -q + -c foo (config swap)
        "-xoaddopts=foo",  # -x + -o addopts= (reopens P15)
        "-spxdist",  # -s + -p xdist (non-leading -p, reopens P11)
    ],
)
def test_forged_short_cluster_never_readmitted(token):
    # The reserved letter rides as a non-leading char; the whole-cluster scan in
    # _is_reserved_flag classifies it reserved despite the greedy `-x*`/`-s*`
    # namespace, so it falls to leftover and is never re-admitted.
    policy = classify_addopts([token], [GREEDY_SHORT])
    assert policy.readmitted(["x"]) == []
    assert policy.leftovers == (token,)


@pytest.mark.parametrize("token", ["-xvs", "-x", "-svq"])
def test_benign_short_cluster_still_readmits(token):
    # No reserved letter (p/o/c) anywhere in the cluster, so it re-admits
    # normally. Mutation guard: reverting the scan to `token[:2]` keeps this
    # test green while test_forged_short_cluster_never_readmitted goes red.
    policy = classify_addopts([token], [GREEDY_SHORT])
    assert policy.readmitted(["x"]) == [token]
    assert policy.leftovers == ()


def test_forged_short_o_cannot_clobber_child_pythonpath(tmp_path, monkeypatch):
    # Argv-level P20 pin: a hostile user manifest granting itself `-x*` plus an
    # ini `addopts = -xopythonpath=/evil`. The token parses and classifies as
    # reserved (leftover, not re-admitted), so no `-o` reaches extra_argv and
    # the child's only `-o pythonpath=` is the deck's P20 inject, last-wins
    # uncontested.
    hostile = parse_manifest(
        'id = "evil"\nlabel = "E"\ndist = "evil"\nscope = "run"\nflags = ["-x*"]\n',
        trusted=False,
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -xopythonpath=/evil\n")
    monkeypatch.setattr(server_mod, "available_manifests", _fake_available([hostile]))
    extra_argv, _env, _tr = server_mod._compile_plugins({"evil": {}}, None, tmp_path)
    assert not any("pythonpath" in tok for tok in extra_argv)
    assert not any("/evil" in tok for tok in extra_argv)

    # Assemble the real child argv and confirm the forged path never lands.
    run = _Run("run-1", RunManager(tmp_path), tmp_path, [], None, None, extra_argv)
    argv = run._argv()
    assert "/evil" not in " ".join(argv)
    # The last `-o pythonpath=` on argv is the deck's (P20), not the forged one.
    idxs = [i for i, t in enumerate(argv) if t == "-o"]
    pp = [argv[i + 1] for i in idxs if argv[i + 1].startswith("pythonpath=")]
    assert pp and all("/evil" not in v for v in pp)


def test_p15_neutralization_tokens_byte_unchanged(tmp_path):
    # Re-admission is explicit deck-appended tokens, never un-stripping: the
    # base argv's P15 pairs are pinned byte-for-byte.
    argv = base_argv(tmp_path)
    i = argv.index("addopts=")
    assert argv[i - 1 : i + 3] == ["-o", "addopts=", "-o", "required_plugins="]


# === server wiring ===========================================================


def _fake_available(manifests):
    return lambda rootdir=None: manifests


def test_compile_plugins_readmits_after_plugin_tokens_before_extra_args(
    tmp_path, monkeypatch
):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --cov=mypkg --cov-report=term --timeout=30 -v\n"
    )
    monkeypatch.setattr(
        server_mod, "available_manifests", _fake_available([COV, TIMEOUT])
    )
    extra_argv, _env, _tr = server_mod._compile_plugins(
        {"pytest_cov": {"source": "mypkg", "branch": False}},
        "--maxfail=1",
        tmp_path,
    )
    # Ordering: plugin tokens, then the re-admitted ones (enabled namespace only;
    # the harvested --cov=mypkg is excluded, --timeout=30 is not enabled, -v has
    # no namespace), then user extra-args. One --cov=mypkg only: the form's, not
    # the ini's.
    assert extra_argv == [
        "-p",
        "pytest_cov",
        "--cov=mypkg",
        "--cov-report=term",
        "--maxfail=1",
    ]


def test_compile_plugins_cleared_field_not_resurrected(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov=mypkg\n")
    monkeypatch.setattr(server_mod, "available_manifests", _fake_available([COV]))
    extra_argv, _env, _tr = server_mod._compile_plugins(
        {"pytest_cov": {"source": "", "branch": False}}, None, tmp_path
    )
    # The cleared form compiles arg_empty; the harvested ini token never rides.
    assert extra_argv == ["-p", "pytest_cov", "--cov"]
    assert "--cov=mypkg" not in extra_argv


def test_compile_plugins_no_enabled_plugins_readmits_nothing(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov-report=term\n")
    monkeypatch.setattr(server_mod, "available_manifests", _fake_available([COV]))
    extra_argv, _env, _tr = server_mod._compile_plugins(None, "-x", tmp_path)
    assert extra_argv == ["-x"]  # toggle off = actually off


def test_compile_plugins_env_addopts_never_readmitted(tmp_path, monkeypatch):
    # Ini only: PYTEST_ADDOPTS is popped from the child env (P15) and must not
    # feed the pipeline either.
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov-report=term")
    monkeypatch.setattr(server_mod, "available_manifests", _fake_available([COV]))
    extra_argv, _env, _tr = server_mod._compile_plugins(
        {"pytest_cov": {}}, None, tmp_path
    )
    assert "--cov-report=term" not in extra_argv


def test_run_argv_ordering_with_readmitted_tokens(tmp_path):
    # P11 with re-admitted tokens present: they ride inside the extra_argv
    # region (after plugin tokens, before user extras), the deck's -p no:
    # blocks are re-asserted last among option tokens, and nodeids come last
    # of all.
    extra_argv = [
        "-p",
        "pytest_cov",
        "--cov=mypkg",  # plugin tokens (form-compiled)
        "--cov-report=term",  # re-admitted
        "--maxfail=1",  # user extra-args
    ]
    run = _Run("run-1", None, tmp_path, ["t.py::x"], None, None, extra_argv=extra_argv)
    argv = run._argv()
    i = argv.index("--cov=mypkg")
    assert argv[i + 1 : i + 3] == ["--cov-report=term", "--maxfail=1"]
    assert argv[-5:] == ["-p", "no:xdist", "-p", "no:cacheprovider", "t.py::x"]


# === /api/plugins wire shape =================================================


def _get_plugins(app):
    async def body():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/api/plugins")

    return asyncio.run(body())


def test_api_plugins_serves_ini_defaults_and_leftovers(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --cov=mypkg --timeout=30 -v\n"
    )
    monkeypatch.setattr(
        server_mod, "available_manifests", _fake_available([COV, TIMEOUT])
    )
    r = _get_plugins(create_app(tmp_path))
    assert r.status_code == 200
    payload = r.json()
    by_id = {p["id"]: p for p in payload["plugins"]}
    assert by_id["pytest_cov"]["ini_defaults"] == {"source": "mypkg"}
    assert by_id["timeout"]["ini_defaults"] == {}
    # --timeout=30 is namespace-covered, so it is not a leftover (it re-admits
    # at run time if and only if timeout is enabled); -v is the only suggestion.
    assert payload["ini_leftovers"] == ["-v"]


# === live end-to-end (real pytest-cov, real subprocess) ======================


pytest_cov_installed = pytest.mark.skipif(
    "pytest_cov"
    not in __import__("pytest_deck.manifests", fromlist=["x"]).installed_plugins(),
    reason="pytest-cov not installed",
)


async def _drain(queue, until, timeout=60.0):
    events = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, f"timed out; saw {[n for n, _ in events]}"
        ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        events.append((ev.name, ev.data))
        if until([n for n, _ in events]):
            return events


@pytest_cov_installed
def test_live_harvest_prefill_and_run_argv(tmp_path):
    # The payoff, unmocked: a real ini `addopts = --cov=mypkg -v` against the
    # real curated manifest scan. The cov field prefills, -v is suggested, and
    # the run argv carries the form-compiled --cov=mypkg exactly once (never the
    # ini token twice, never the unclicked -v).
    (tmp_path / "mypkg.py").write_text("def f():\n    return 1\n")
    (tmp_path / "test_ok.py").write_text(
        "from mypkg import f\n\ndef test_ok():\n    assert f() == 1\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --cov=mypkg -v\n")

    app = create_app(tmp_path)
    r = _get_plugins(app)
    payload = r.json()
    cov = next(p for p in payload["plugins"] if p["id"] == "pytest_cov")
    assert cov["ini_defaults"] == {"source": "mypkg"}
    assert payload["ini_leftovers"] == ["-v"]

    async def body():
        mgr = app.state.manager
        try:
            q = mgr.subscribe()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                r = await client.post(
                    "/api/run",
                    json={
                        "nodeids": ["test_ok.py::test_ok"],
                        "plugins": {"pytest_cov": {"source": "mypkg", "branch": False}},
                    },
                )
                assert r.status_code == 202
            events = await _drain(q, lambda ns: "finished" in ns)
            argv = next(d for n, d in events if n == "started")["argv"]
            assert argv.count("--cov=mypkg") == 1  # the form's, once
            assert "-v" not in argv  # unclicked suggestion never rides
            # P15 stayed byte-unchanged alongside re-admission:
            i = argv.index("addopts=")
            assert argv[i - 1] == "-o"
            finished = next(d for n, d in events if n == "finished")
            assert finished["exit_code"] == 0
        finally:
            await mgr.shutdown()

    asyncio.run(body())
