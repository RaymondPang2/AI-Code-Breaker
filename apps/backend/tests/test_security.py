"""
Security-control tests.

Cover the abuse-protection layer added in the security review:
  - request body-size limit (413)
  - per-client rate limiting (429)
  - per-client concurrency cap (429)
  - anonymous lifetime quota (429)
  - ownership-scoped deletion (owner deletes; others get 404)
  - public share-link flow (opt-in token, revocation)
  - error sanitization (no tracebacks/paths leak to clients)

Rate limiting is disabled globally in conftest for determinism; the tests
here that need it enable it explicitly on a fresh limiter.
"""

import uuid

import pytest

from app.core.config import get_settings
from app.core.identity import identity_digest
from app.repositories import analysis_repository as repo


VALID_SUBMISSION = {
    "function_name": "second_largest",
    "specification": "Return the second largest distinct value.",
    "candidate_code": "def second_largest(v):\n    return sorted(v)[-2]\n",
    "reference_code": "def second_largest(v):\n    return sorted(set(v))[-2]\n",
}


# --- Body size limit --------------------------------------------------------


def test_oversized_request_body_is_rejected_413(client, monkeypatch):
    # Send a candidate_code that blows past the body limit. The middleware
    # rejects it before the schema even runs.
    huge = {**VALID_SUBMISSION, "candidate_code": "x" * (300_000)}
    resp = client.post("/submissions", json=huge)
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


def test_normal_request_body_passes(client):
    resp = client.post("/submissions", json=VALID_SUBMISSION)
    assert resp.status_code == 201


# --- Rate limiting ----------------------------------------------------------


def test_rate_limit_returns_429_when_exceeded(client, monkeypatch):
    from app.core import rate_limit
    from app.api import dependencies

    # Enable limiting and install a tiny limiter (burst of 2).
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
    limiter = rate_limit.TokenBucketRateLimiter(rate_per_minute=0, burst=2)
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(dependencies, "get_rate_limiter", lambda: limiter)

    # First two allowed, third throttled (same client identity / IP).
    r1 = client.post("/submissions", json=VALID_SUBMISSION)
    r2 = client.post("/submissions", json=VALID_SUBMISSION)
    r3 = client.post("/submissions", json=VALID_SUBMISSION)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 429
    assert "rate limit" in r3.json()["detail"].lower()


def test_distinct_client_ids_have_separate_buckets(client, monkeypatch):
    from app.core import rate_limit
    from app.api import dependencies

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
    limiter = rate_limit.TokenBucketRateLimiter(rate_per_minute=0, burst=1)
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(dependencies, "get_rate_limiter", lambda: limiter)

    # Client A uses its one token; client B still has its own.
    a1 = client.post("/submissions", json=VALID_SUBMISSION, headers={"X-Client-Id": "alice"})
    a2 = client.post("/submissions", json=VALID_SUBMISSION, headers={"X-Client-Id": "alice"})
    b1 = client.post("/submissions", json=VALID_SUBMISSION, headers={"X-Client-Id": "bob"})
    assert a1.status_code == 201
    assert a2.status_code == 429
    assert b1.status_code == 201


# --- Concurrency cap --------------------------------------------------------


def test_concurrency_cap_blocks_extra_analyses(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_concurrent_analyses_per_client", 2, raising=False)
    # Prevent the eager job from completing (which would free the slot), so
    # the runs stay "in flight" for the cap check.
    from app.services import analysis_jobs

    def _enqueue_only(session, *, submission_id, request, settings=None):
        run = repo.create_queued_analysis_run(
            session, submission_id=submission_id, configuration={}, seed=None
        )
        session.commit()
        return run.id

    monkeypatch.setattr(analysis_jobs, "create_and_enqueue_analysis", _enqueue_only)

    sid = client.post("/submissions", json=VALID_SUBMISSION).json()["submission_id"]

    # Two allowed (queued, non-terminal), third blocked.
    r1 = client.post(f"/submissions/{sid}/analyses", json={})
    r2 = client.post(f"/submissions/{sid}/analyses", json={})
    r3 = client.post(f"/submissions/{sid}/analyses", json={})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r3.status_code == 429
    assert "in progress" in r3.json()["detail"].lower()


# --- Anonymous quota --------------------------------------------------------


def test_anonymous_quota_blocks_after_limit(client, monkeypatch):
    settings = get_settings()
    # Generous concurrency so only the lifetime quota is the gate.
    monkeypatch.setattr(settings, "max_concurrent_analyses_per_client", 100, raising=False)
    monkeypatch.setattr(settings, "anonymous_analysis_quota", 2, raising=False)

    from app.services import analysis_jobs

    def _enqueue_only(session, *, submission_id, request, settings=None):
        run = repo.create_queued_analysis_run(
            session, submission_id=submission_id, configuration={}, seed=None
        )
        session.commit()
        return run.id

    monkeypatch.setattr(analysis_jobs, "create_and_enqueue_analysis", _enqueue_only)

    sid = client.post("/submissions", json=VALID_SUBMISSION).json()["submission_id"]
    # No X-Client-Id header -> anonymous (IP-keyed).
    r1 = client.post(f"/submissions/{sid}/analyses", json={})
    r2 = client.post(f"/submissions/{sid}/analyses", json={})
    r3 = client.post(f"/submissions/{sid}/analyses", json={})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r3.status_code == 429
    assert "quota" in r3.json()["detail"].lower()


def test_identified_client_not_subject_to_anonymous_quota(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_concurrent_analyses_per_client", 100, raising=False)
    monkeypatch.setattr(settings, "anonymous_analysis_quota", 1, raising=False)

    from app.services import analysis_jobs

    def _enqueue_only(session, *, submission_id, request, settings=None):
        run = repo.create_queued_analysis_run(
            session, submission_id=submission_id, configuration={}, seed=None
        )
        session.commit()
        return run.id

    monkeypatch.setattr(analysis_jobs, "create_and_enqueue_analysis", _enqueue_only)

    headers = {"X-Client-Id": "known-user"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=headers).json()[
        "submission_id"
    ]
    # Beyond the anonymous quota of 1, an identified client keeps going.
    r1 = client.post(f"/submissions/{sid}/analyses", json={}, headers=headers)
    r2 = client.post(f"/submissions/{sid}/analyses", json={}, headers=headers)
    assert r1.status_code == 202
    assert r2.status_code == 202


# --- Ownership-scoped deletion ----------------------------------------------


def test_owner_can_delete_submission(client):
    headers = {"X-Client-Id": "owner-1"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=headers).json()[
        "submission_id"
    ]
    resp = client.delete(f"/submissions/{sid}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    # Gone afterward.
    assert client.get(f"/submissions/{sid}").status_code == 404


def test_non_owner_cannot_delete_and_gets_404(client):
    owner = {"X-Client-Id": "owner-2"}
    other = {"X-Client-Id": "intruder"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=owner).json()[
        "submission_id"
    ]
    # A different client can't delete it — and gets 404, not 403, so it can't
    # even confirm the ID exists.
    resp = client.delete(f"/submissions/{sid}", headers=other)
    assert resp.status_code == 404
    # Still there for the owner.
    assert client.get(f"/submissions/{sid}").status_code == 200


def test_delete_cascades_to_analyses(client, db_session):
    headers = {"X-Client-Id": "cascade-owner"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=headers).json()[
        "submission_id"
    ]
    client.post(f"/submissions/{sid}/analyses", json={"test_inputs": [[5, 5, 5]]})
    # Delete and confirm no analyses remain for that submission.
    assert client.delete(f"/submissions/{sid}", headers=headers).status_code == 200
    runs = repo.list_analysis_runs_for_submission(db_session, uuid.UUID(sid))
    assert runs == []


# --- Share links ------------------------------------------------------------


def test_stored_code_is_private_until_shared(client):
    owner = {"X-Client-Id": "share-owner"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=owner).json()[
        "submission_id"
    ]
    # A random share token doesn't resolve.
    assert client.get("/submissions/shared/not-a-real-token").status_code == 404


def test_share_flow_mint_and_revoke(client):
    owner = {"X-Client-Id": "share-owner-2"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=owner).json()[
        "submission_id"
    ]
    # Enable sharing -> get a token.
    share = client.post(f"/submissions/{sid}/share", json={"public": True}, headers=owner)
    assert share.status_code == 200
    token = share.json()["share_token"]
    assert token
    # The token resolves to the submission content.
    shared = client.get(f"/submissions/shared/{token}")
    assert shared.status_code == 200
    assert shared.json()["function_name"] == "second_largest"
    # Revoke -> token stops working.
    client.post(f"/submissions/{sid}/share", json={"public": False}, headers=owner)
    assert client.get(f"/submissions/shared/{token}").status_code == 404


def test_non_owner_cannot_share(client):
    owner = {"X-Client-Id": "share-owner-3"}
    other = {"X-Client-Id": "not-owner"}
    sid = client.post("/submissions", json=VALID_SUBMISSION, headers=owner).json()[
        "submission_id"
    ]
    resp = client.post(f"/submissions/{sid}/share", json={"public": True}, headers=other)
    assert resp.status_code == 404


# --- Error sanitization -----------------------------------------------------


def test_errors_do_not_leak_internal_detail(client):
    # A bad UUID should produce a clean 422, not a traceback.
    resp = client.get("/submissions/not-a-uuid")
    assert resp.status_code == 422
    body = resp.text.lower()
    assert "traceback" not in body
    assert "/home/" not in body
    assert "sqlalchemy" not in body
