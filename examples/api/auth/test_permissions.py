"""Second file in the auth subpackage — role/permission checks, so a single
subdirectory holds more than one test file.
"""

import pytest

ROLE_PERMS = {
    "admin": {"read", "write", "delete"},
    "editor": {"read", "write"},
    "viewer": {"read"},
}


@pytest.mark.security
@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_everyone_can_read(role):
    assert "read" in ROLE_PERMS[role]


@pytest.mark.security
@pytest.mark.parametrize(
    "role,perm,allowed",
    [
        ("admin", "delete", True),
        ("editor", "delete", False),
        ("viewer", "write", False),
        ("editor", "write", True),
    ],
)
def test_permission_matrix(role, perm, allowed):
    assert (perm in ROLE_PERMS[role]) is allowed


def test_unknown_role_has_no_perms():
    assert ROLE_PERMS.get("ghost", set()) == set()
