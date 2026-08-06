"""
Reusable FastAPI dependencies for the abuse-protection controls.

- `client_identity` resolves the per-client key (see app.core.identity).
- `enforce_rate_limit` applies the token-bucket limiter to state-changing
  endpoints and raises 429 when exceeded.

Concurrency and quota checks need a DB session and are enforced inside the
route/service (see app.services.quota), not here, so they can query current
in-flight analyses.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.identity import resolve_client_identity
from app.core.rate_limit import get_rate_limiter


def get_settings_dep() -> Settings:
    return get_settings()


def client_identity(
    request: Request, settings: Settings = Depends(get_settings_dep)
) -> str:
    return resolve_client_identity(request, settings)


def enforce_rate_limit(
    identity: str = Depends(client_identity),
    settings: Settings = Depends(get_settings_dep),
) -> str:
    """Apply the per-client rate limit. Returns the identity so routes can
    reuse it (for quota/concurrency checks) without re-resolving."""
    if not settings.rate_limit_enabled:
        return identity
    limiter = get_rate_limiter()
    if not limiter.allow(identity):
        raise HTTPException(
            status_code=429,
            detail=(
                "Rate limit exceeded. Please slow down and try again in a "
                "few seconds."
            ),
        )
    return identity
