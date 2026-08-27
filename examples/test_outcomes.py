"""A spread of outcomes to exercise the dashboard's status column and detail pane:
plain failures, errors, skips, xfail, xpass, and warnings.
"""

import warnings

import pytest

# --- plain failures -------------------------------------------------------


def test_simple_failure():
    expected = 42
    actual = 7 * 6 + 1
    assert actual == expected


def test_failure_with_message():
    items = ["a", "b", "c"]
    assert "z" in items, "expected 'z' to be present in the list"


@pytest.mark.parametrize("a,b", [(1, 1), (2, 3), (9, 10)])
def test_parametrized_some_fail(a, b):
    # Two of the three cases fail, handy for seeing a mixed group rollup.
    assert a == b


def test_failure_with_output():
    print("computing the answer...")
    print("intermediate value = 41")
    assert 41 == 42


# --- errors (failures outside the test body) ------------------------------


@pytest.fixture
def broken_fixture():
    raise RuntimeError("could not set up the database connection")


def test_error_in_setup(broken_fixture):
    # Never reached: the fixture raises during setup, so this shows up as an ERROR.
    assert True


def test_error_from_missing_fixture(does_not_exist):
    assert True


# --- skips ----------------------------------------------------------------


@pytest.mark.skip(reason="feature not implemented yet")
def test_unconditional_skip():
    assert False


@pytest.mark.skipif(True, reason="only runs on Windows")
def test_conditional_skip():
    assert False


def test_runtime_skip():
    pytest.skip("skipped from inside the test body")


# --- xfail / xpass --------------------------------------------------------


@pytest.mark.xfail(reason="known bug #123, fix pending")
def test_expected_failure():
    # Fails as expected, so it reports as XFAIL.
    assert 1 == 2


@pytest.mark.xfail(reason="this actually passes now")
def test_unexpected_pass():
    # Expected to fail but passes, so it reports as XPASS.
    assert 1 == 1


@pytest.mark.xfail(strict=True, reason="must stay failing")
def test_strict_xpass_is_a_failure():
    # Under strict xfail, an unexpected pass is reported as a plain failure.
    assert True


@pytest.mark.xfail(reason="raises the wrong-but-expected error")
def test_xfail_raising():
    raise ValueError("boom, but expected")


# --- warnings -------------------------------------------------------------


def test_emits_user_warning():
    warnings.warn("this API is deprecated, use new_api()", UserWarning)
    assert True


def test_emits_deprecation_warning():
    warnings.warn("old_behaviour() will be removed in 2.0", DeprecationWarning)
    assert True


def test_warns_then_fails():
    warnings.warn("careful: precision may be lost", RuntimeWarning)
    assert round(0.1 + 0.2, 1) == 0.5


@pytest.mark.filterwarnings("error::UserWarning")
def test_warning_promoted_to_error():
    # The filter turns the UserWarning into an error, so this test fails.
    warnings.warn("promoted to an error by filterwarnings", UserWarning)
