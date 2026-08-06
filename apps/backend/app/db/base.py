"""
SQLAlchemy 2.x engine, session factory, and declarative base.

The engine/session live here so the rest of the app depends on a single
place for database wiring. Models (app.models) import Base from here;
repositories (app.repositories) and the FastAPI dependency
(app.db.session.get_db_session) use SessionLocal.

The DATABASE_URL is read from settings, so tests can point this at an
isolated database (a throwaway SQLite file — see tests/conftest.py)
without any code change.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import CHAR, TypeDecorator

from app.core.config import get_settings

settings = get_settings()

# `future=True` is the default in SQLAlchemy 2.x; stated explicitly for
# clarity. pool_pre_ping avoids handing out connections that the database
# has silently dropped (common with long-idle dev containers).
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class GUID(TypeDecorator):
    """
    Platform-independent UUID primary-key type.

    On PostgreSQL (production/local), this uses the native UUID column via
    postgresql.UUID. On other backends (SQLite, used for isolated tests),
    it stores the value as a 32-char hex string. Either way, Python code
    always sees a `uuid.UUID`, so models and repositories don't have to
    care which backend is underneath — the tests can run on SQLite while
    production runs on Postgres, with identical model code.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID

            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        # Non-postgres: store the bare hex.
        if isinstance(value, uuid.UUID):
            return value.hex
        return uuid.UUID(str(value)).hex

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    type_annotation_map = {
        # Ensure `str` columns without an explicit type get a sane default
        # length-less String (TEXT on Postgres) rather than erroring.
        str: String,
    }
