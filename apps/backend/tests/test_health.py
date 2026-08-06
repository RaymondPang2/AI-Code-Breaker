from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_expected_shape():
    response = client.get("/health")
    body = response.json()
    assert body == {"status": "ok", "service": "ai-code-breaker-backend"}


def test_ready_reports_dependency_checks():
    # In tests the DB is a live SQLite file and QUEUE_EAGER is set, so the
    # redis check is skipped (not failed). Readiness should be 200 with a
    # per-dependency breakdown.
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert "redis" in body["checks"]


def test_redact_url_strips_credentials():
    from app.core.logging_config import redact_url

    assert (
        redact_url("postgresql+psycopg://user:secret@db:5432/app")
        == "postgresql+psycopg://db:5432/app"
    )
    assert redact_url("redis://:pw@redis:6379/0") == "redis://redis:6379/0"
    assert "secret" not in redact_url("postgresql://u:secret@h/d")
    assert redact_url("postgresql://db:5432/app") == "postgresql://db:5432/app"
