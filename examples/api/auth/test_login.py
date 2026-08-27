"""Auth subpackage — login flow. Uses a fixture + parametrization + a marker,
so the tree nests api/ → auth/ → test_login.py → classes → variants.
"""

import pytest


@pytest.fixture
def user():
    return {"name": "ada", "password": "hunter2", "active": True}


@pytest.mark.security
class TestLogin:
    def test_correct_password(self, user):
        assert user["password"] == "hunter2"

    def test_wrong_password_rejected(self, user):
        assert "letmein" != user["password"]

    @pytest.mark.parametrize(
        "attempt,ok",
        [
            ("hunter2", True),
            ("Hunter2", False),
            ("", False),
            (" hunter2 ", False),
        ],
    )
    def test_password_is_case_and_space_sensitive(self, user, attempt, ok):
        assert (attempt == user["password"]) is ok

    def test_inactive_user_cannot_login(self, user):
        user["active"] = False
        assert not user["active"]
