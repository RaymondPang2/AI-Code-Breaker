"""
FastAPI dependency that yields a database session per request and always
closes it, committing on success and rolling back on error.

Routes never construct sessions themselves; they depend on get_db_session,
and pass the session to repository/service functions. This keeps session
lifecycle in exactly one place.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.base import SessionLocal


def get_db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
