"""
Client identity for rate limiting and quotas.

This is deliberately lightweight and is NOT authentication. It resolves a
stable key for a request so limits can be applied per client:

  1. an explicit, opt-in client id header (default 'X-Client-Id') if present
     — lets a well-behaved client avoid being lumped in with everyone behind
     a shared IP, and lets an owner scope their own stored content; or
  2. otherwise the client IP (best-effort, from the socket or a trusted
     proxy header).

A caller providing a client id is trusted only for convenience/scoping, not
for security decisions — someone can spoof it, but doing so only affects
their own quota bucket. Real multi-tenant auth would replace this with signed
sessions or tokens.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from fastapi import Request


def resolve_client_identity(request: "Request", settings: Settings | None = None) -> str:
    """Return a stable identity string for the request."""
    settings = settings or get_settings()

    header_name = settings.client_id_header
    raw = request.headers.get(header_name)
    if raw:
        cleaned = raw.strip()[:128]
        if cleaned:
            return f"cid:{cleaned}"

    # Fall back to client IP. Prefer the socket peer; behind a trusted proxy
    # the first X-Forwarded-For hop may be more accurate, but that header is
    # client-spoofable unless a proxy is known to set it, so we only use it
    # when there's no direct client host.
    client_host = request.client.host if request.client else None
    if not client_host:
        forwarded = request.headers.get("x-forwarded-for", "")
        client_host = forwarded.split(",")[0].strip() or "unknown"

    return f"ip:{client_host}"


def is_anonymous(identity: str) -> bool:
    """True when the identity is IP-derived (no explicit client id given)."""
    return identity.startswith("ip:")


def identity_digest(identity: str) -> str:
    """A short, non-reversible digest of an identity — safe to store/compare
    without persisting a raw IP address."""
    return hashlib.sha256(identity.encode()).hexdigest()[:32]
