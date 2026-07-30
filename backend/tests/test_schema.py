"""The database keeps working across a model that gains a column.

`create_all` only creates missing *tables*. Before `add_missing_columns`, adding
a column to a model left every database created earlier raising `no such column`
on the first query touching that table — the API answered 500 on a database it
had just reported as set up. These tests pin the repair.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.db.session import add_missing_columns, engine, init_db
from app.models.catalog import Player


def _columns(table: str) -> set[str]:
    """Straight from SQLite. The Inspector caches, and this fixture changes the
    schema underneath it twice per test."""
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


@pytest.fixture
def players_missing_provenance(db):
    """Put `players` back the way a database created before provenance looked."""
    db.rollback()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE players DROP COLUMN provenance"))
    try:
        yield
    finally:
        db.rollback()
        if "provenance" not in _columns("players"):
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE players ADD COLUMN provenance JSON"))
                conn.execute(text("UPDATE players SET provenance = '{}'"))
        db.expire_all()


def test_query_fails_without_the_column(db, players_missing_provenance):
    """The failure being repaired, so the test cannot pass vacuously."""
    with pytest.raises(Exception, match="no such column"):
        db.execute(select(Player).limit(1)).scalars().all()
    db.rollback()


def test_missing_column_is_added_and_backfilled(db, players_missing_provenance):
    before = db.execute(
        text("SELECT id, name, overall_rating FROM players ORDER BY id")
    ).all()
    assert before, "fixture needs seeded players to prove they survive"

    added = add_missing_columns()

    assert "players.provenance" in added
    assert "provenance" in _columns("players")

    db.expire_all()
    players = db.execute(select(Player)).scalars().all()
    # Backfilled with the column's own default, not left NULL.
    assert all(p.provenance == {} for p in players)

    after = db.execute(
        text("SELECT id, name, overall_rating FROM players ORDER BY id")
    ).all()
    assert after == before, "rebuilding the column must not touch the rows"


def test_repair_is_idempotent(db, players_missing_provenance):
    add_missing_columns()
    assert add_missing_columns() == []


def test_init_db_repairs_an_existing_database(db, players_missing_provenance):
    """The path the app actually takes: startup calls init_db, not the helper."""
    init_db()

    db.expire_all()
    assert db.execute(select(Player).limit(1)).scalars().all()


def test_untouched_schema_needs_no_repair(db):
    assert add_missing_columns() == []
