"""
Persistence service: the bridge between the in-memory analysis result
(Pydantic) and the database (ORM via repositories).

This is where a SubmissionAnalysisResponse gets turned into persisted
Submission / TestCase / AnalysisRun / Execution / Counterexample rows, and
where persisted rows get assembled back into the read schemas the GET
endpoints return. Routes call these functions; they never assemble ORM
graphs themselves.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.entities import AnalysisRun, Execution, Submission
from app.repositories import analysis_repository as repo
from app.schemas.persistence import (
    AnalysisRunRead,
    CounterexampleRead,
    ExecutionRead,
    SubmissionRead,
)
from app.schemas.submission import SubmissionAnalysisResponse, SubmissionRequest


def _execution_result_to_dict(result) -> dict:
    """FunctionExecutionResult -> the sanitized dict we persist. Uses the
    Pydantic model's own dump so the stored shape always matches the
    public contract."""
    return result.model_dump()


def _write_comparisons(
    session: Session,
    *,
    submission_id: uuid.UUID,
    run_id: uuid.UUID,
    analysis: SubmissionAnalysisResponse,
    counterexample_explanation: dict | None,
    minimized_input: list[int] | None,
) -> None:
    """
    Write the per-comparison rows (test case + candidate/reference executions,
    plus a counterexample for the first confirmed difference) for a given
    submission/run. Shared by both persist paths so they can never drift in
    how a comparison is recorded. The verified results come from real
    execution upstream; nothing here fabricates a result.
    """
    for comparison in analysis.comparisons:
        test_case = repo.create_test_case(
            session,
            submission_id=submission_id,
            input_values=comparison.input,
            category=comparison.category,
            source=comparison.source,
            reason=comparison.reason,
        )
        repo.create_execution(
            session,
            analysis_run_id=run_id,
            test_case_id=test_case.id,
            role="candidate",
            normalized_result=_execution_result_to_dict(comparison.candidate),
            runtime_ms=comparison.candidate.runtime_ms,
            timed_out=comparison.candidate.status == "timeout",
        )
        repo.create_execution(
            session,
            analysis_run_id=run_id,
            test_case_id=test_case.id,
            role="reference",
            normalized_result=_execution_result_to_dict(comparison.reference),
            runtime_ms=comparison.reference.runtime_ms,
            timed_out=comparison.reference.status == "timeout",
        )

        # Persist the first confirmed behavioral difference as a counterexample.
        if (
            not comparison.match
            and not comparison.internal_error
            and analysis.first_failing_input == comparison.input
        ):
            repo.create_counterexample(
                session,
                analysis_run_id=run_id,
                original_input=comparison.input,
                minimized_input=minimized_input,
                candidate_result=_execution_result_to_dict(comparison.candidate),
                reference_result=_execution_result_to_dict(comparison.reference),
                explanation=counterexample_explanation,
            )


def persist_analysis(
    session: Session,
    submission_request: SubmissionRequest,
    analysis: SubmissionAnalysisResponse,
    counterexample_explanation: dict | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Save a completed analysis: the submission, its test cases, the analysis
    run, every execution (candidate + reference per test case), and any
    counterexamples. Returns (submission_id, analysis_run_id).

    `counterexample_explanation`, if provided, is the structured
    explanation for the first confirmed counterexample (see
    app.schemas.explanation). It is stored ALONGSIDE — never in place of —
    the verified candidate_result / reference_result. Explanations are
    advisory; the execution facts remain authoritative.

    One submission row is created per analyze call. (Deduplicating identical
    submissions is a deliberate non-goal for now — each analyze call is its
    own recorded event.)
    """
    submission = repo.create_submission(
        session,
        function_name=submission_request.function_name,
        specification=submission_request.specification,
        candidate_code=submission_request.candidate_code,
        reference_code=submission_request.reference_code,
    )

    run = repo.create_analysis_run(
        session,
        submission_id=submission.id,
        status="completed",
        total_tests=analysis.total_tests,
        passed_tests=analysis.passed_tests,
        failed_tests=analysis.failed_tests,
        inconclusive_tests=analysis.inconclusive_tests,
        elapsed_seconds=None,
        seed=submission_request.generation_seed if submission_request.generate_tests else None,
        configuration={
            "generate_tests": submission_request.generate_tests,
            "generation_seed": submission_request.generation_seed,
            "manual_test_count": len(submission_request.test_inputs),
        },
    )

    _write_comparisons(
        session,
        submission_id=submission.id,
        run_id=run.id,
        analysis=analysis,
        counterexample_explanation=counterexample_explanation,
        minimized_input=None,
    )

    return submission.id, run.id


def persist_results_into_run(
    session: Session,
    *,
    submission_id: uuid.UUID,
    run_id: uuid.UUID,
    analysis: SubmissionAnalysisResponse,
    counterexample_explanation: dict | None = None,
    minimized_input: list[int] | None = None,
) -> None:
    """
    Write executions and counterexamples for an ALREADY-CREATED run (the
    async worker path, where the run was created in the 'queued' state up
    front and its id returned to the caller immediately).

    Idempotency: any pre-existing test cases / executions / counterexamples
    for this run are cleared first, so re-running the job (e.g. a retry that
    got partway before) produces the same final state rather than
    duplicate rows. The verified results themselves are recomputed by the
    caller from real execution, never fabricated here.
    """
    repo.clear_run_children(session, submission_id=submission_id, run_id=run_id)

    _write_comparisons(
        session,
        submission_id=submission_id,
        run_id=run_id,
        analysis=analysis,
        counterexample_explanation=counterexample_explanation,
        minimized_input=minimized_input,
    )


def get_submission_read(session: Session, submission_id: uuid.UUID) -> SubmissionRead | None:
    submission = repo.get_submission(session, submission_id)
    if submission is None:
        return None
    return SubmissionRead.model_validate(submission)


def create_submission_only(
    session: Session,
    *,
    function_name: str,
    specification: str,
    candidate_code: str,
    reference_code: str,
    owner_digest: str | None = None,
) -> uuid.UUID:
    """Create just a Submission row (no run yet) and return its id. Used by
    POST /submissions in the async workflow."""
    submission = repo.create_submission(
        session,
        function_name=function_name,
        specification=specification,
        candidate_code=candidate_code,
        reference_code=reference_code,
        owner_digest=owner_digest,
    )
    return submission.id


def cancel_analysis_run(
    session: Session, submission_id: uuid.UUID, analysis_run_id: uuid.UUID
) -> bool:
    """Mark a run cancelled if it belongs to the submission and isn't
    already terminal. Returns True if it was cancelled."""
    run = repo.get_analysis_run_for_submission(session, submission_id, analysis_run_id)
    if run is None:
        return False
    from app.core import analysis_status

    if analysis_status.is_terminal(run.status):
        return False
    repo.mark_analysis_run_cancelled(session, analysis_run_id)
    return True


def get_analysis_run_read(
    session: Session, submission_id: uuid.UUID, analysis_run_id: uuid.UUID
) -> AnalysisRunRead | None:
    run = repo.get_analysis_run_for_submission(session, submission_id, analysis_run_id)
    if run is None:
        return None
    return _assemble_analysis_run_read(run)


def _assemble_analysis_run_read(run: AnalysisRun) -> AnalysisRunRead:
    executions = [
        ExecutionRead(
            id=execution.id,
            role=execution.role,
            test_case_id=execution.test_case_id,
            input=execution.test_case.input,
            normalized_result=execution.normalized_result,
            runtime_ms=execution.runtime_ms,
            timed_out=execution.timed_out,
        )
        for execution in run.executions
    ]
    counterexamples = [
        CounterexampleRead.model_validate(ce) for ce in run.counterexamples
    ]
    return AnalysisRunRead(
        id=run.id,
        submission_id=run.submission_id,
        status=run.status,
        progress=run.progress,
        error=run.error,
        total_tests=run.total_tests,
        passed_tests=run.passed_tests,
        failed_tests=run.failed_tests,
        inconclusive_tests=run.inconclusive_tests,
        elapsed_seconds=run.elapsed_seconds,
        seed=run.seed,
        configuration=run.configuration,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        executions=executions,
        counterexamples=counterexamples,
    )


# --- Ownership, deletion, and share links -----------------------------------


def owns_submission(
    session: Session, submission_id: uuid.UUID, owner_digest: str
) -> bool:
    """True if the submission exists and is owned by this identity digest.
    Submissions with no owner (legacy/pre-migration) are treated as not
    owned by anyone, so they can't be deleted or shared by a random caller."""
    submission = repo.get_submission(session, submission_id)
    return submission is not None and submission.owner_digest == owner_digest


def delete_submission_owned(
    session: Session, submission_id: uuid.UUID, owner_digest: str
) -> str:
    """
    Delete a submission and all its data, but only if the caller owns it.

    Returns one of: "deleted", "not_found", "forbidden". The caller maps
    these to HTTP status codes. A missing submission and a not-owned
    submission are reported distinctly to the OWNER only via this call; the
    route deliberately collapses "forbidden" into 404 for others so IDs
    can't be probed.
    """
    submission = repo.get_submission(session, submission_id)
    if submission is None:
        return "not_found"
    if submission.owner_digest != owner_digest:
        return "forbidden"
    repo.delete_submission(session, submission_id)
    return "deleted"


def set_share(
    session: Session,
    submission_id: uuid.UUID,
    owner_digest: str,
    *,
    make_public: bool,
) -> tuple[str, str | None]:
    """
    Enable or disable a public share link for a submission the caller owns.

    Returns (status, share_token) where status is "ok" / "not_found" /
    "forbidden". When enabling, a fresh unguessable token is generated; when
    disabling, the token is cleared so any previously-shared link stops
    working.
    """
    import secrets

    submission = repo.get_submission(session, submission_id)
    if submission is None:
        return "not_found", None
    if submission.owner_digest != owner_digest:
        return "forbidden", None

    if make_public:
        token = secrets.token_urlsafe(24)
        repo.set_submission_share(
            session, submission_id, is_public=True, share_token=token
        )
        return "ok", token
    else:
        repo.set_submission_share(
            session, submission_id, is_public=False, share_token=None
        )
        return "ok", None


def get_shared_submission(
    session: Session, share_token: str
) -> SubmissionRead | None:
    """Fetch a submission by its public share token, only if it's public."""
    submission = repo.get_submission_by_share_token(session, share_token)
    if submission is None or not submission.is_public:
        return None
    return SubmissionRead.model_validate(submission)
