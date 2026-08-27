"""Unit tests for ``pytest_deck.reports`` — serialized report → wire shape.

These are pure functions on the fd-3 seam, so they get direct unit tests
against literal serialized payloads (the dict shapes pytest's
``TestReport._to_json`` produces):

* ``reshape_report`` prefers the inner plugin's native ``longrepr_text`` and
  maps every wire field (incl. the ``wasxfail`` default),
* ``_stringify_longrepr`` is the fallback reconstruction from
  ``reprtraceback``/``reprcrash`` when ``longrepr_text`` is absent,
* ``_stringify_sections`` maps captured-output ``[title, content]`` pairs.
"""

from pytest_deck.reports import (
    _stringify_longrepr,
    _stringify_sections,
    reshape_report,
)

# A realistic serialized longrepr (pytest's TestReport._to_json shape) with one
# traceback frame plus the crash summary line.
_ONE_FRAME_LONGREPR = {
    "reprtraceback": {
        "reprentries": [
            {
                "type": "ReprEntry",
                "data": {
                    "lines": [
                        "    def test_x():",
                        ">       assert 1 == 2",
                        "E       assert 1 == 2",
                    ],
                    "reprfileloc": {
                        "path": "test_mod.py",
                        "lineno": 3,
                        "message": "AssertionError",
                    },
                },
            }
        ]
    },
    "reprcrash": {
        "path": "test_mod.py",
        "lineno": 3,
        "message": "AssertionError: assert 1 == 2",
    },
}


# === reshape_report ========================================================


def test_reshape_report_prefers_native_longrepr_text():
    # When the inner plugin rendered the longrepr natively, that exact text
    # wins; the serialized dict is not reconstructed again.
    report = {
        "nodeid": "test_mod.py::test_x",
        "when": "call",
        "outcome": "failed",
        "duration": 0.25,
        "longrepr_text": "native render",
        "longrepr": _ONE_FRAME_LONGREPR,
        "sections": [["Captured stdout call", "hello\n"]],
        "wasxfail": "reason: flaky",
    }
    out = reshape_report("run-7", report)
    assert out == {
        "run_id": "run-7",
        "nodeid": "test_mod.py::test_x",
        "when": "call",
        "outcome": "failed",
        "duration": 0.25,
        "longrepr": "native render",
        "sections": [{"title": "Captured stdout call", "content": "hello\n"}],
        "wasxfail": "reason: flaky",
    }


def test_reshape_report_reconstructs_when_longrepr_text_absent():
    report = {
        "nodeid": "test_mod.py::test_x",
        "when": "call",
        "outcome": "failed",
        "duration": 0.1,
        "longrepr": _ONE_FRAME_LONGREPR,
    }
    out = reshape_report("run-1", report)
    assert out["longrepr"] == (
        "    def test_x():\n"
        ">       assert 1 == 2\n"
        "E       assert 1 == 2\n"
        "test_mod.py:3: AssertionError\n"
        "\n"
        "AssertionError: assert 1 == 2"
    )


def test_reshape_report_defaults_for_a_minimal_passing_report():
    # A passing phase carries no longrepr, sections, or wasxfail, but every wire
    # field must still be present with its documented default.
    out = reshape_report(
        "run-2",
        {
            "nodeid": "test_mod.py::test_ok",
            "when": "setup",
            "outcome": "passed",
            "duration": 0.01,
        },
    )
    assert out == {
        "run_id": "run-2",
        "nodeid": "test_mod.py::test_ok",
        "when": "setup",
        "outcome": "passed",
        "duration": 0.01,
        "longrepr": None,
        "sections": [],
        "wasxfail": None,
    }


# === _stringify_longrepr ===================================================


def test_longrepr_none_stays_none():
    assert _stringify_longrepr(None) is None


def test_longrepr_plain_string_passes_through():
    assert _stringify_longrepr("already rendered") == "already rendered"


def test_longrepr_non_dict_non_string_is_stringified():
    # e.g. a serialized tuple-ish longrepr (skip reprs) arrives as a list.
    assert _stringify_longrepr(42) == "42"
    assert (
        _stringify_longrepr(["test_mod.py", 3, "Skipped: nope"])
        == "['test_mod.py', 3, 'Skipped: nope']"
    )


def test_longrepr_two_frames_get_blank_line_separators():
    longrepr = {
        "reprtraceback": {
            "reprentries": [
                {
                    "data": {
                        "lines": ["    helper()"],
                        "reprfileloc": {
                            "path": "test_mod.py",
                            "lineno": 8,
                            "message": "",
                        },
                    }
                },
                {
                    "data": {
                        "lines": [">   raise ValueError('boom')"],
                        "reprfileloc": {
                            "path": "helpers.py",
                            "lineno": 2,
                            "message": "ValueError",
                        },
                    }
                },
            ]
        }
    }
    assert _stringify_longrepr(longrepr) == (
        "    helper()\n"
        "test_mod.py:8: \n"
        "\n"
        ">   raise ValueError('boom')\n"
        "helpers.py:2: ValueError"
    )


def test_longrepr_skips_malformed_entries_and_missing_fileloc():
    # Non-dict entries and entries whose data isn't a dict are skipped; a frame
    # without reprfileloc renders its lines only.
    longrepr = {
        "reprtraceback": {
            "reprentries": [
                "not-a-dict",
                {"data": "not-a-dict-either"},
                {"data": {"lines": ["E   boom"]}},
            ]
        }
    }
    assert _stringify_longrepr(longrepr) == "E   boom"


def test_longrepr_crash_only_returns_message_directly():
    longrepr = {"reprcrash": {"message": "OSError: no space left"}}
    assert _stringify_longrepr(longrepr) == "OSError: no space left"


def test_longrepr_crash_appended_after_frames():
    longrepr = {
        "reprtraceback": {"reprentries": [{"data": {"lines": ["E   assert False"]}}]},
        "reprcrash": {"message": "AssertionError"},
    }
    assert _stringify_longrepr(longrepr) == "E   assert False\n\nAssertionError"


def test_longrepr_empty_crash_message_is_ignored():
    longrepr = {
        "reprtraceback": {"reprentries": [{"data": {"lines": ["E   assert False"]}}]},
        "reprcrash": {"message": ""},
    }
    assert _stringify_longrepr(longrepr) == "E   assert False"


def test_longrepr_dict_with_nothing_usable_falls_back_to_str():
    # An empty dict, a non-dict reprtraceback, and empty reprentries all
    # reconstruct to no text at all; the raw dict repr is better than silence.
    assert _stringify_longrepr({}) == "{}"
    weird = {"reprtraceback": "not-a-dict"}
    assert _stringify_longrepr(weird) == str(weird)
    empty_tb = {"reprtraceback": {"reprentries": []}}
    assert _stringify_longrepr(empty_tb) == str(empty_tb)


# === _stringify_sections ===================================================


def test_sections_none_and_empty_become_empty_list():
    assert _stringify_sections(None) == []
    assert _stringify_sections([]) == []


def test_sections_pairs_map_to_title_content_dicts():
    sections = [
        ["Captured stdout call", "out text\n"],
        ("Captured stderr call", "err text\n"),  # tuples work too
    ]
    assert _stringify_sections(sections) == [
        {"title": "Captured stdout call", "content": "out text\n"},
        {"title": "Captured stderr call", "content": "err text\n"},
    ]


def test_sections_coerce_non_string_pairs():
    assert _stringify_sections([[1, 2]]) == [{"title": "1", "content": "2"}]


def test_sections_skip_non_pair_entries():
    sections = [
        ["only-title"],  # too short
        ["a", "b", "c"],  # too long
        "not-a-pair",  # not a list/tuple (even though len() == 10)
        ["Captured log call", "kept\n"],
    ]
    assert _stringify_sections(sections) == [
        {"title": "Captured log call", "content": "kept\n"}
    ]
