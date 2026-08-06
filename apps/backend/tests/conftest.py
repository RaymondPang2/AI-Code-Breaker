"""
Shared pytest fixtures.

Two cross-cutting concerns are set up here:

1. Execution backend: every test runs with EXECUTION_BACKEND=subprocess
   (no Docker required) unless it opts into the `docker_backend` fixture.

2. Database isolation: tests never touch a real Postgres. Before anything
   from app.* is imported, this module points DATABASE_URL at a throwaway
   file-backed SQLite database, and the `db_session` / `client` fixtures
   create and drop all tables around each test so tests can't see each
   other's rows. SQLite is used deliberately for test isolation and zero
   setup — the portable GUID/JSON types (app.db.base) mean the same model
   code runs on SQLite here and Postgres in production.
"""

import os
import tempfile

# MUST run before any `from app...` import below, because app.db.base
# builds the engine from settings at import time.
_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".sqlite", prefix="acb_test_")
os.close(_TEST_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("EXECUTION_BACKEND", "subprocess")
# Run analysis jobs inline (no worker / no real Redis) during tests. The
# EagerQueue in app.queue executes enqueued jobs synchronously, so the async
# workflow is exercised end to end deterministically. See app.queue.
os.environ.setdefault("QUEUE_EAGER", "1")
# Rate limiting is disabled by default in tests so the existing suite is
# deterministic and isn't throttled. The dedicated security tests re-enable
# it explicitly (via the settings/limiter) to assert the behavior.
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base, engine  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base as ModelsBase  # noqa: E402  (ensures all models are imported)


# Enforce SQLite foreign keys (off by default in SQLite) so ON DELETE
# CASCADE and FK constraints behave like they will on Postgres.
@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _connection_record):
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def pytest_sessionfinish(session, exitstatus):
    try:
        os.unlink(_TEST_DB_PATH)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _default_execution_backend(monkeypatch):
    monkeypatch.setenv("EXECUTION_BACKEND", "subprocess")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def docker_backend(monkeypatch):
    """Opt a single test into the Docker execution backend."""
    monkeypatch.setenv("EXECUTION_BACKEND", "docker")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_tables():
    """Create all tables before a test and drop them after, so each test
    that uses the database starts from an empty schema."""
    ModelsBase.metadata.create_all(bind=engine)
    yield
    ModelsBase.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_tables):
    """A database session for direct repository/service tests."""
    from app.db.base import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_tables):
    """
    A FastAPI TestClient whose DB dependency uses the isolated test
    database. The get_db_session override commits on success so data
    persists across requests within one test (e.g. POST then GET), and the
    surrounding db_tables fixture drops everything afterward.
    """
    from app.db.base import SessionLocal

    def _override_get_db_session():
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _override_get_db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
