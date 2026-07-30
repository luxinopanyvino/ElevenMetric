"""Engine and session factory."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import Column, Table

from app.core.config import settings

logger = logging.getLogger("elevenmetric.db")

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # `timeout` is the busy-wait before SQLite gives up on a locked database.
    # The default is 5 s but only applies to some paths; setting it explicitly
    # (and the matching PRAGMA below) stops a concurrent write — an analysis job
    # persisting a report while a request reads — from failing outright.
    _connect_args = {"check_same_thread": False, "timeout": 30.0}

engine: Engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver glue
    if not settings.database_url.startswith("sqlite"):
        return
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _default_value(column: Column):
    """The Python-side default for a column, or ``None`` if it has none.

    Returns a sentinel-free value: ``None`` means "leave the backfill alone",
    which for a freshly added column is already the stored state.
    """
    default = column.default
    if default is None:
        return None
    if default.is_scalar:
        return default.arg
    if default.is_callable:
        try:
            return default.arg(None)  # SQLAlchemy wraps 0-arg callables
        except Exception:  # pragma: no cover - a default we cannot evaluate
            return None
    return None


def add_missing_columns() -> list[str]:
    """Add columns the models declare but an existing table is missing.

    ``create_all`` creates missing *tables* and leaves existing ones untouched,
    so a model that gains a column leaves every database created before it
    raising ``no such column`` on the first query that touches the table. That
    is a half-finished setup reported as a working one, which is the thing this
    codebase is meant not to do.

    This closes the gap for the one case it can close safely — a newly added
    column that is nullable or carries a default — by adding it and backfilling
    existing rows with that default. It is deliberately not a migration tool:
    renames, drops, type changes and new constraints are real migrations, and a
    column that is ``NOT NULL`` with no default cannot be added to a populated
    table at all. Such a column is skipped with a warning naming it, so the
    limit is announced rather than hit later as a query error.

    Returns the ``table.column`` names it added.
    """
    from app.db.base_class import Base

    inspector = inspect(engine)
    present_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in present_tables:
            continue  # create_all just made it, columns and all
        present = {col["name"] for col in inspector.get_columns(table.name)}
        missing = [col for col in table.columns if col.name not in present]
        if not missing:
            continue

        for column in missing:
            if not column.nullable and column.default is None and column.server_default is None:
                logger.warning(
                    "Cannot add %s.%s automatically: NOT NULL with no default. "
                    "This needs a migration, or a rebuild with `python -m app.db.seed --reset`.",
                    table.name,
                    column.name,
                )
                continue
            _add_column(table, column)
            added.append(f"{table.name}.{column.name}")

    if added:
        logger.info("Schema updated: added %s", ", ".join(added))
    return added


def _add_column(table: Table, column: Column) -> None:
    """``ALTER TABLE ADD COLUMN`` plus a backfill of the column's default.

    The column is always added as nullable with no ``DEFAULT`` clause — SQLite
    rejects a non-constant default here, and a JSON ``default=dict`` is exactly
    that — and the default is then written into the existing rows, which the
    ``ADD COLUMN`` left NULL.
    """
    type_sql = column.type.compile(dialect=engine.dialect)
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'))
        value = _default_value(column)
        if value is not None:
            # Goes through the column's type, so a JSON default is serialised
            # the same way the ORM would have written it.
            conn.execute(table.update().values({column: value}))


def init_db() -> None:
    """Create every table, and reconcile columns added since it was created.

    Real deployments would use Alembic. This keeps an existing demo database
    usable across a model change instead of failing every query that touches a
    new column; see :func:`add_missing_columns` for what it will and will not do.
    """
    from app.db import base  # noqa: F401  (imports all models onto Base.metadata)
    from app.db.base_class import Base

    Base.metadata.create_all(bind=engine)
    add_missing_columns()
