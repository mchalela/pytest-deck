"""pytest-mock — a structural plugin switch (the ``mocker`` fixture).

Enable the "Mocking (pytest-mock)" switch in the left bar and these tests run
green. The switch has no panel, no fields, no data channel — the fixture is
the plugin's entire surface. Needs ``pip install pytest-mock``; the switch is
hidden until it's installed.

The demo is what happens WITHOUT it: the deck runs pytest with plugin autoload
disabled, so with the switch off these tests ERROR at setup with "fixture
'mocker' not found" — flip the switch and re-run to watch them recover.

Patches target ``toy.py`` (the traceback-demo module next to this file).
"""

import toy


def test_normalize_uses_the_patched_total(mocker):
    # Patch the module-level helper: normalize() should divide by whatever
    # _sum_strict returns, and should have been asked about our exact list.
    patched = mocker.patch("toy._sum_strict", return_value=10.0)
    assert toy.normalize([2, 3, 5]) == [0.2, 0.3, 0.5]
    patched.assert_called_once_with([2, 3, 5])


def test_accumulate_spied_add_calls(mocker):
    # Spy: the real _add still runs (the sum is right), but every call and the
    # last return value are recorded.
    spy = mocker.spy(toy, "_add")
    assert toy._accumulate([1, 2, 3]) == 6
    assert spy.call_count == 3
    assert spy.spy_return == 6
