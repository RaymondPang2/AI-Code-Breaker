"""
A small in-memory token-bucket rate limiter, keyed by client identity.

Chosen deliberately over a heavier dependency: for a single-instance student
deployment an in-process bucket is enough, has zero extra infrastructure, and
is easy to reason about and test. Its limitation is honest — it does NOT
share state across multiple API processes. For a multi-instance deployment,
back this with Redis (the project already runs Redis for the job queue);
the interface here is small enough to swap.

Each identity gets a bucket that refills at `rate_per_minute/60` tokens per
second up to `burst` capacity. A request costs one token; if the bucket is
empty the request is limited.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketRateLimiter:
    def __init__(self, rate_per_minute: int, burst: int):
        self.refill_per_second = rate_per_minute / 60.0
        self.capacity = float(burst)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Consume `cost` tokens for `key`; return True if allowed."""
        now = self._now()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, last_refill=now)
                self._buckets[key] = bucket
            # Refill based on elapsed time.
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                self.capacity, bucket.tokens + elapsed * self.refill_per_second
            )
            bucket.last_refill = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True
            return False

    def reset(self) -> None:
        """Clear all buckets (used by tests)."""
        with self._lock:
            self._buckets.clear()


# A process-wide limiter instance, configured lazily from settings so the
# same limiter is shared across requests in this process.
_limiter: TokenBucketRateLimiter | None = None


def get_rate_limiter() -> TokenBucketRateLimiter:
    global _limiter
    if _limiter is None:
        from app.core.config import get_settings

        settings = get_settings()
        _limiter = TokenBucketRateLimiter(
            rate_per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
        )
    return _limiter


def reset_rate_limiter() -> None:
    """Reset the process-wide limiter (tests)."""
    global _limiter
    _limiter = None
