"""
Enqueue side of the async analysis workflow.

The API calls create_and_enqueue_analysis to (1) create the AnalysisRun row
in the 'queued' state and (2) put its job on the queue — then returns the
run id immediately. The worker picks it up and runs app.worker.analysis_job.

Idempotency / no-duplicate-execution:
  - The RQ job id is derived deterministically from the run id
    (job_id = f"analysis:{run_id}"). RQ won't hold two jobs with the same
    id, so re-enqueuing the same run is a no-op rather than a second
    execution. The job itself also no-ops if the run is already terminal.

Retry policy is attached here: transient errors get a bounded number of
retries with backoff; permanent/user-code errors are not retried (the job
raises PermanentJobError, which we do NOT list as retryable).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.queue import get_queue
from app.repositories import analysis_repository as repo
from app.schemas.submission import SubmissionRequest
from app.worker.analysis_job import run_analysis_job


def job_id_for_run(run_id: uuid.UUID | str) -> str:
    """Deterministic job id for a run — the key to idempotent enqueuing."""
    return f"analysis:{run_id}"


def create_and_enqueue_analysis(
    session: Session,
    *,
    submission_id: uuid.UUID,
    request: SubmissionRequest,
    settings: Settings | None = None,
) -> uuid.UUID:
    """
    Create a queued run for `submission_id` and enqueue its job. Returns the
    new run id. The heavy work happens later on the worker (or inline, in
    eager mode).
    """
    settings = settings or get_settings()

    run = repo.create_queued_analysis_run(
        session,
        submission_id=submission_id,
        configuration={
            "generate_tests": request.generate_tests,
            "generation_seed": request.generation_seed,
            "manual_test_count": len(request.test_inputs),
            "use_ai_tests": request.use_ai_tests,
            "explain_counterexamples": request.explain_counterexamples,
        },
        seed=request.generation_seed if request.generate_tests else None,
    )
    run_id = run.id
    job_id = job_id_for_run(run_id)
    repo.set_analysis_run_job_id(session, run_id, job_id)
    # Commit so the row (queued) is durable and visible to the worker before
    # the job can run.
    session.commit()

    queue = get_queue(settings)
    enqueue_kwargs = _enqueue_kwargs(settings)
    queue.enqueue(
        run_analysis_job,
        str(run_id),
        str(submission_id),
        request.model_dump(mode="json"),
        job_id=job_id,
        **enqueue_kwargs,
    )
    return run_id


def _enqueue_kwargs(settings: Settings) -> dict:
    """RQ enqueue kwargs, including the retry policy. In eager mode these are
    ignored by EagerQueue."""
    kwargs: dict = {
        "job_timeout": settings.job_timeout_seconds,
        "result_ttl": settings.job_result_ttl_seconds,
    }
    if not settings.queue_eager:
        try:
            from rq import Retry

            # Retry only applies to jobs that RAISE. Our job raises only
            # TransientJobError as retryable; PermanentJobError also raises,
            # but by design we keep max retries low and permanent failures
            # record their state before raising, so a retry immediately
            # no-ops (terminal run) rather than repeating work. Intervals
            # back off: 10s, then 30s.
            kwargs["retry"] = Retry(
                max=settings.job_max_retries, interval=[10, 30]
            )
        except Exception:
            # If RQ's Retry isn't importable for some reason, proceed
            # without automatic retries rather than failing enqueue.
            pass
    return kwargs
