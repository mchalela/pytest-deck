"""Top-level API route tests — a mix of classes and plain functions so the
tree shows file → class → test as well as file → test at the same level.
"""

import pytest


@pytest.mark.smoke
def test_health_endpoint():
    status = {"ok": True, "code": 200}
    assert status["ok"] and status["code"] == 200


class TestRouting:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/", "home"),
            ("/users", "users"),
            ("/users/42", "user_detail"),
            ("/nope", "not_found"),
        ],
    )
    def test_dispatch(self, path, expected):
        handler = path.strip("/").split("/")[0] or "home"
        table = {"home": "home", "users": "users", "nope": "not_found"}
        resolved = "user_detail" if path.count("/") == 2 else table.get(handler, "not_found")
        assert resolved == expected

    def test_trailing_slash_normalised(self):
        assert "/users/".rstrip("/") == "/users"


class TestContentNegotiation:
    @pytest.mark.parametrize("accept", ["application/json", "text/html", "*/*"])
    def test_supported_types(self, accept):
        supported = {"application/json", "text/html", "*/*"}
        assert accept in supported
