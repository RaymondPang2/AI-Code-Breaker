"""
Job queue wiring (RQ over Redis).

Why RQ: this project needs exactly one thing from a queue — run a Python
function on a worker, off the request path, with retrievable status. RQ
delivers that with Redis as the only new dependency and plain functions as
jobs (no task-definition DSL, no separate result backend). Celery/Dramatiq
would add moving parts a single-worker portfolio tool doesn't need. RQ's
Job object also gives us custom job ids (for idempotency) and failure
introspection for free.

`get_queue()` returns a real RQ Queue by default. In eager mode
(settings.queue_eager, set by tests and available for local debugging) it
returns a queue whose `enqueue` runs the job synchronously and in-process,
so there's no worker or real Redis to stand up in tests.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from app.core.config import Settings, get_settings


@lru_cache
def get_redis_connection(redis_url: str | None = None):
    """A cached Redis connection. Imported lazily so environments that never
    touch the queue don't need redis installed."""
    import redis

    settings = get_settings()
    return redis.Redis.from_url(redis_url or settings.redis_url)


class EagerJob:
    """A minimal stand-in for rq.job.Job when running eagerly."""

    def __init__(self, job_id: str, result: Any = None, exc: BaseException | None = None):
        self.id = job_id
        self._result = result
        self._exc = exc

    def get_status(self) -> str:
        return "failed" if self._exc else "finished"


class EagerQueue:
    """
    A queue that executes jobs synchronously in-process. Used by tests and
    for local runs without a worker. Mirrors just the slice of the RQ Queue
    API this project uses: enqueue(func, *args, job_id=..., **kwargs).
    """

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(
        self,
        func: Callable[..., Any],
        *args: Any,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> EagerJob:
        # RQ-specific kwargs that don't apply when running inline.
        for rq_only in ("retry", "job_timeout", "result_ttl", "failure_ttl", "meta", "on_failure", "on_success"):
            kwargs.pop(rq_only, None)
        self.enqueued.append(job_id or "")
        try:
            result = func(*args, **kwargs)
            return EagerJob(job_id or "eager", result=result)
        except Exception as exc:  # surfaced to caller like a sync failure
            return EagerJob(job_id or "eager", exc=exc)

    def fetch_job(self, job_id: str):  # parity with RQ Queue
        return None


def get_queue(settings: Settings | None = None):
    """Return the analysis queue: a real RQ Queue, or an EagerQueue when
    settings.queue_eager is set."""
    settings = settings or get_settings()
    if settings.queue_eager:
        return EagerQueue()

    from rq import Queue

    connection = get_redis_connection(settings.redis_url)
    return Queue(
        name=settings.analysis_queue_name,
        connection=connection,
        default_timeout=settings.job_timeout_seconds,
    )
