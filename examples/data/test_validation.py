"""Data-layer validation tests. Classes grouping related rules, plus a
parametrized sweep — gives the tree a second top-level directory to fold.
"""

import pytest


class TestEmailValidation:
    @pytest.mark.parametrize(
        "email,valid",
        [
            ("ada@example.com", True),
            ("no-at-sign.com", False),
            ("a@b.co", True),
            ("@nope.com", False),
            ("trailing@", False),
        ],
    )
    def test_email_shape(self, email, valid):
        looks_valid = "@" in email and not email.startswith("@") and not email.endswith("@")
        assert looks_valid is valid


class TestRangeValidation:
    @pytest.mark.parametrize("age", [0, 18, 65, 120])
    def test_age_in_bounds(self, age):
        assert 0 <= age <= 120

    @pytest.mark.parametrize("age", [-1, 121, 999])
    def test_age_out_of_bounds(self, age):
        assert not (0 <= age <= 120)


@pytest.mark.unit
def test_required_fields_present():
    record = {"id": 1, "name": "widget"}
    assert {"id", "name"} <= record.keys()
