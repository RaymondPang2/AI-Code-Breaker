"""
Worker entrypoint.

Run with:  python -m app.worker.run_worker
(or via the `worker` service in infra/docker-compose.yml)

It starts an RQ worker listening on the analysis queue. Job functions live
in app.worker.analysis_job; this module only wires the worker to Redis.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.logging_config import configure_logging

logger = logging.getLogger("acb.worker")


def main() -> None:
    settings = get_settings()
    # Structured logging, same format/level as the API (env-driven).
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    if settings.queue_eager:
        raise SystemExit(
            "QUEUE_EAGER is set — jobs run inline, so no worker is needed. "
            "Unset QUEUE_EAGER to run a real worker."
        )

    from rq import Queue, Worker

    from app.queue import get_redis_connection

    connection = get_redis_connection(settings.redis_url)
    queue = Queue(
        name=settings.analysis_queue_name,
        connection=connection,
        default_timeout=settings.job_timeout_seconds,
    )
    logger.info(
        "Starting analysis worker on queue %r (redis=%s)",
        settings.analysis_queue_name,
        settings.redis_url,
    )
    worker = Worker([queue], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
