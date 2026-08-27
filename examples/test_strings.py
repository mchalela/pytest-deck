import pytest


@pytest.mark.db
def test_upper():
    assert "deck".upper() == "DECK"


@pytest.mark.smoke
def test_concat():
    assert "py" + "test" == "pytest"


@pytest.mark.parametrize("value", ["a", "bb", "ccc", "dddd"])
def test_length(value):
    assert len(value) == value.count(value[0])
