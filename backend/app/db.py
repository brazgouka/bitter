"""Database engine, session factory, and table initialisation.

Imported by app.models (Base), app.deps (get_db), and every service that needs
a Session. Uses SQLite by default (portable, zero-dep) and Postgres in Docker.
"""
from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("bitter.db")


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _sync_columns() -> None:
    """Additive dev-only migration: ADD COLUMN for any model field missing on
    an existing table. There is no Alembic yet (see README roadmap); this
    keeps `create_all` (which only creates missing *tables*) from silently
    leaving older databases without newly added nullable columns.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand-new table: create_all already handled it
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                try:
                    conn.execute(
                        text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}')
                    )
                    log.info("migrated: added %s.%s", table.name, col.name)
                except Exception as exc:
                    log.warning("could not add %s.%s: %s", table.name, col.name, exc)


def init_db() -> None:
    """Create all tables, then patch any missing columns on existing tables."""
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
    _sync_columns()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
