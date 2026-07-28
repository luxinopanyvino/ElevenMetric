"""Engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

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


def init_db() -> None:
    """Create every table. Real deployments would use Alembic migrations."""
    from app.db import base  # noqa: F401  (imports all models onto Base.metadata)
    from app.db.base_class import Base

    Base.metadata.create_all(bind=engine)
