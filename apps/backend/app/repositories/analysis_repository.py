"""
Repository functions: the only place that reads/writes ORM models.

Routes and services call these instead of touching the Session directly,
so query logic lives in one layer and the rest of the app deals in models
and Pydantic schemas rather than SQL.

These functions never commit — session lifecycle (commit/rollback/close)
is owned by app.db.session.get_db_session. They flush where they need a
generated primary key before continuing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import (
    AnalysisRun,
    Counterexample,
    Execution,
    Submission,
    TestCase,
)


def create_submission(
    session: Session,
    *,
    function_name: str,
    specification: str,
    candidate_code: str,
    reference_code: str,
    owner_digest: str | None = None,
) -> Submission:
    submission = Submission(
        function_name=function_name,
        specification=specification,
        candidate_code=candidate_code,
        reference_code=reference_code,
        owner_digest=owner_digest,
    )
    session.add(submission)
    session.flush()  # populate submission.id
    return submission


def get_submission(session: Session, submission_id: uuid.UUID) -> Submission | None:
    return session.get(Submission, submission_id)


def get_submission_by_share_token(
    session: Session, share_token: str
) -> Submission | None:
    """Look up a submission by its unguessable share token (public links)."""
    stmt = select(Submission).where(Submission.share_token == share_token)
    return session.execute(stmt).scalar_one_or_none()


def count_active_analyses_for_owner(session: Session, owner_digest: str) -> int:
    """Count in-flight (non-terminal) analyses created by an owner — used for
    the per-client concurrency cap."""
    from app.core import analysis_status

    stmt = (
        select(func.count(AnalysisRun.id))
        .join(Submission, AnalysisRun.submission_id == Submission.id)
        .where(Submission.owner_digest == owner_digest)
        .where(AnalysisRun.status.notin_(list(analysis_status.TERMINAL_STATUSES)))
    )
    return int(session.execute(stmt).scalar_one())


def count_total_analyses_for_owner(session: Session, owner_digest: str) -> int:
    """Count ALL analyses ever created by an owner — used for the anonymous
    lifetime quota."""
    stmt = (
        select(func.count(AnalysisRun.id))
        .join(Submission, AnalysisRun.submission_id == Submission.id)
        .where(Submission.owner_digest == owner_digest)
    )
    return int(session.execute(stmt).scalar_one())


def set_submission_share(
    session: Session,
    submission_id: uuid.UUID,
    *,
    is_public: bool,
    share_token: str | None,
) -> None:
    submission = session.get(Submission, submission_id)
    if submission is None:
        return
    submission.is_public = is_public
    submission.share_token = share_token


def delete_submission(session: Session, submission_id: uuid.UUID) -> bool:
    """Delete a submission and everything under it (test cases, runs,
    executions, counterexamples) via the ORM cascade. Returns True if a row
    was deleted."""
    submission = session.get(Submission, submission_id)
    if submission is None:
        return False
    session.delete(submission)
    return True


def create_test_case(
    session: Session,
    *,
    submission_id: uuid.UUID,
    input_values: list[int],
    category: str,
    source: str,
    reason: str,
) -> TestCase:
    test_case = TestCase(
        submission_id=submission_id,
        input=list(input_values),
        category=category,
        source=source,
        reason=reason,
    )
    session.add(test_case)
    session.flush()
    return test_case


def create_analysis_run(
    session: Session,
    *,
    submission_id: uuid.UUID,
    status: str,
    total_tests: int,
    passed_tests: int,
    failed_tests: int,
    elapsed_seconds: float | None,
    seed: int | None,
    configuration: dict,
    inconclusive_tests: int = 0,
) -> AnalysisRun:
    run = AnalysisRun(
        submission_id=submission_id,
        status=status,
        total_tests=total_tests,
        passed_tests=passed_tests,
        failed_tests=failed_tests,
        inconclusive_tests=inconclusive_tests,
        elapsed_seconds=elapsed_seconds,
        seed=seed,
        configuration=configuration,
    )
    session.add(run)
    session.flush()
    return run


def create_queued_analysis_run(
    session: Session,
    *,
    submission_id: uuid.UUID,
    configuration: dict,
    seed: int | None,
) -> AnalysisRun:
    """Create a run in the 'queued' state, before any work has happened.
    The worker later fills in results and advances the status."""
    from app.core import analysis_status

    run = AnalysisRun(
        submission_id=submission_id,
        status=analysis_status.QUEUED,
        progress=0.0,
        configuration=configuration,
        seed=seed,
    )
    session.add(run)
    session.flush()
    return run


def set_analysis_run_job_id(
    session: Session, run_id: uuid.UUID, job_id: str
) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is not None:
        run.job_id = job_id


def update_analysis_run_status(
    session: Session,
    run_id: uuid.UUID,
    *,
    status: str,
    progress: float | None = None,
) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        return
    run.status = status
    if progress is not None:
        run.progress = progress


def mark_analysis_run_started(session: Session, run_id: uuid.UUID) -> None:
    from datetime import datetime, timezone

    run = session.get(AnalysisRun, run_id)
    if run is not None:
        run.started_at = datetime.now(timezone.utc)


def mark_analysis_run_failed(
    session: Session, run_id: uuid.UUID, *, error: str
) -> None:
    from datetime import datetime, timezone

    from app.core import analysis_status

    run = session.get(AnalysisRun, run_id)
    if run is None:
        return
    run.status = analysis_status.FAILED
    run.error = error
    run.finished_at = datetime.now(timezone.utc)


def mark_analysis_run_cancelled(session: Session, run_id: uuid.UUID) -> None:
    from datetime import datetime, timezone

    from app.core import analysis_status

    run = session.get(AnalysisRun, run_id)
    if run is None:
        return
    run.status = analysis_status.CANCELLED
    run.finished_at = datetime.now(timezone.utc)


def finalize_analysis_run_results(
    session: Session,
    run_id: uuid.UUID,
    *,
    total_tests: int,
    passed_tests: int,
    failed_tests: int,
    elapsed_seconds: float | None,
    inconclusive_tests: int = 0,
) -> None:
    """Mark a run completed and record its summary counts."""
    from datetime import datetime, timezone

    from app.core import analysis_status

    run = session.get(AnalysisRun, run_id)
    if run is None:
        return
    run.status = analysis_status.COMPLETED
    run.progress = 1.0
    run.total_tests = total_tests
    run.passed_tests = passed_tests
    run.failed_tests = failed_tests
    run.inconclusive_tests = inconclusive_tests
    run.elapsed_seconds = elapsed_seconds
    run.finished_at = datetime.now(timezone.utc)


def create_execution(
    session: Session,
    *,
    analysis_run_id: uuid.UUID,
    test_case_id: uuid.UUID,
    role: str,
    normalized_result: dict,
    runtime_ms: float | None,
    timed_out: bool,
) -> Execution:
    execution = Execution(
        analysis_run_id=analysis_run_id,
        test_case_id=test_case_id,
        role=role,
        normalized_result=normalized_result,
        runtime_ms=runtime_ms,
        timed_out=timed_out,
    )
    session.add(execution)
    return execution


def clear_run_children(
    session: Session,
    *,
    submission_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """
    Delete any executions, counterexamples, and (submission-scoped) test
    cases previously written for this run. Supports idempotent re-runs: a
    retried job clears partial output before rewriting, so the final state
    is the same whether the job ran once or several times.
    """
    from sqlalchemy import delete

    session.execute(
        delete(Execution).where(Execution.analysis_run_id == run_id)
    )
    session.execute(
        delete(Counterexample).where(Counterexample.analysis_run_id == run_id)
    )
    # Test cases are submission-scoped; only this run created them in the
    # async flow (one run per submission there), so clearing by submission
    # is correct and keeps re-runs clean.
    session.execute(
        delete(TestCase).where(TestCase.submission_id == submission_id)
    )
    session.flush()


def create_counterexample(
    session: Session,
    *,
    analysis_run_id: uuid.UUID,
    original_input: list[int],
    minimized_input: list[int] | None,
    candidate_result: dict,
    reference_result: dict,
    explanation: dict | None = None,
) -> Counterexample:
    counterexample = Counterexample(
        analysis_run_id=analysis_run_id,
        original_input=list(original_input),
        minimized_input=list(minimized_input) if minimized_input is not None else None,
        candidate_result=candidate_result,
        reference_result=reference_result,
        explanation=explanation,
    )
    session.add(counterexample)
    return counterexample


def get_analysis_run(
    session: Session, analysis_run_id: uuid.UUID
) -> AnalysisRun | None:
    """Load one analysis run with its executions and counterexamples
    eagerly, so callers don't trigger lazy loads after the session
    lifecycle hands back control."""
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.id == analysis_run_id)
        .options(
            selectinload(AnalysisRun.executions).selectinload(Execution.test_case),
            selectinload(AnalysisRun.counterexamples),
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_analysis_run_for_submission(
    session: Session, submission_id: uuid.UUID, analysis_run_id: uuid.UUID
) -> AnalysisRun | None:
    """Same as get_analysis_run, but only returns the run if it actually
    belongs to the given submission — so the nested route can't be used to
    read another submission's run by guessing IDs."""
    run = get_analysis_run(session, analysis_run_id)
    if run is None or run.submission_id != submission_id:
        return None
    return run


def list_analysis_runs_for_submission(
    session: Session, submission_id: uuid.UUID
) -> list[AnalysisRun]:
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.submission_id == submission_id)
        .order_by(AnalysisRun.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())
