"""Persistence subpackage — an in-memory repository exercised through a
fixture. Marked ``db`` (and one ``slow``) so marker chips have something to
bulk-select across directories.
"""

import pytest


class InMemoryRepo:
    def __init__(self):
        self._rows = {}
        self._next = 1

    def add(self, name):
        rid = self._next
        self._rows[rid] = name
        self._next += 1
        return rid

    def get(self, rid):
        return self._rows.get(rid)

    def delete(self, rid):
        return self._rows.pop(rid, None) is not None

    def all(self):
        return list(self._rows.values())


@pytest.fixture
def repo():
    r = InMemoryRepo()
    r.add("alpha")
    r.add("beta")
    return r


@pytest.mark.db
class TestRepository:
    def test_seeded_rows(self, repo):
        assert repo.all() == ["alpha", "beta"]

    def test_add_returns_id(self, repo):
        rid = repo.add("gamma")
        assert repo.get(rid) == "gamma"

    def test_delete_existing(self, repo):
        assert repo.delete(1) is True
        assert repo.get(1) is None

    def test_delete_missing_is_falsey(self, repo):
        assert repo.delete(999) is False

    @pytest.mark.parametrize("name", ["x", "y", "z"])
    def test_round_trip(self, repo, name):
        rid = repo.add(name)
        assert repo.get(rid) == name


@pytest.mark.db
@pytest.mark.slow
def test_bulk_insert(repo):
    for i in range(100):
        repo.add(f"row-{i}")
    assert len(repo.all()) == 102
