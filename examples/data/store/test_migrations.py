"""A second file under data/store/ so the deepest directory isn't a single
file — ordered schema migrations applied in sequence.
"""

import pytest

MIGRATIONS = ["001_init", "002_add_users", "003_add_index", "004_add_audit"]


@pytest.mark.db
@pytest.mark.parametrize("migration", MIGRATIONS)
def test_migration_names_are_numbered(migration):
    prefix = migration.split("_")[0]
    assert prefix.isdigit() and len(prefix) == 3


@pytest.mark.db
def test_migrations_are_ordered():
    prefixes = [m.split("_")[0] for m in MIGRATIONS]
    assert prefixes == sorted(prefixes)


@pytest.mark.integration
@pytest.mark.slow
def test_full_migration_chain_applies():
    applied = []
    for m in MIGRATIONS:
        applied.append(m)
    assert applied == MIGRATIONS
