"""
Per-client analysis quotas, enforced at analysis-creation time.

Two limits, both keyed by the (hashed) client identity:

  - Concurrency cap: at most `max_concurrent_analyses_per_client` in-flight
    (non-terminal) analyses at once. Bounds how much work one client can have
    queued/running simultaneously.

  - Anonymous lifetime quota: an anonymous (IP-keyed) client may create at
    most `anonymous_analysis_quota` analyses total. Clients that identify
    themselves with a stable client id are not subject to the anonymous
    lifetime quota (but are still subject to concurrency + rate limits).

These raise HTTPException(429) with a clear message when exceeded. They query
current counts from the DB, so they're correct across processes (unlike the
in-memory rate limiter).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.identity import identity_digest, is_anonymous
from app.repositories import analysis_repository as repo


def enforce_analysis_quota(
    session: Session, identity: str, settings: Settings | None = None
) -> None:
    """Raise 429 if creating another analysis would exceed this client's
    concurrency cap or (for anonymous clients) lifetime quota."""
    settings = settings or get_settings()
    digest = identity_digest(identity)

    # Concurrency cap (applies to everyone).
    active = repo.count_active_analyses_for_owner(session, digest)
    if active >= settings.max_concurrent_analyses_per_client:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You already have {active} analyses in progress "
                f"(max {settings.max_concurrent_analyses_per_client}). "
                "Wait for one to finish before starting another."
            ),
        )

    # Anonymous lifetime quota (only for IP-keyed clients; 0 disables).
    if is_anonymous(identity) and settings.anonymous_analysis_quota > 0:
        total = repo.count_total_analyses_for_owner(session, digest)
        if total >= settings.anonymous_analysis_quota:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Anonymous usage quota reached for this client. This is "
                    "an experimental demo with limited capacity."
                ),
            )
