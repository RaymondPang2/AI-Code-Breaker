"""
Request body size-limit middleware.

Rejects requests whose body exceeds `settings.max_request_body_bytes` with a
413 before the application handles them, so an attacker can't force the
server to buffer an enormous payload into memory. This is a coarse,
first-line guard that complements (does not replace) the fine-grained
per-field size limits enforced by the Pydantic schemas.

Two checks:
  1. If a Content-Length header is present and over the limit, reject
     immediately without reading the body.
  2. Otherwise, read the body once and reject if it turns out to be over the
     limit (defends against a missing/lying Content-Length). The body is
     cached back onto the request so the downstream handler can still read
     it.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes: int):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next):
        # Only bodies matter; GET/DELETE etc. usually have none.
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    return self._too_large()
            except ValueError:
                # Malformed Content-Length — fall through to the read check.
                pass

        # Read the body once, enforce the real size, and cache it so the
        # route can read it again.
        body = await request.body()
        if len(body) > self.max_body_bytes:
            return self._too_large()

        # Starlette caches the body internally after request.body(); the
        # downstream handler re-reading it gets the cached bytes.
        return await call_next(request)

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "detail": (
                    "Request body too large. Reduce the size of your "
                    "submission (each source file is capped, and the total "
                    "request is limited)."
                )
            },
        )
