"""
Routes under /submissions.

Kept intentionally thin: validation lives in the schemas; execution and
comparison live in the services; persistence lives behind
app.services.persistence_service (which uses repositories, never raw SQL
here). This module wires HTTP paths to those layers and owns nothing else.

Sensitive-data note: the GET endpoints return only the read schemas in
app.schemas.persistence, which by construction exclude internal file
paths, container IDs, and raw tracebacks — the stored normalized results
were already sanitized upstream by runner/runner.py.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.persistence import (
    AnalysisJobCreatedResponse,
    AnalysisRunRead,
    DeletionResponse,
    ShareRequest,
    ShareResponse,
    SubmissionCreatedResponse,
    SubmissionRead,
)
from app.schemas.submission import (
    AnalysisOptionsRequest,
    SubmissionRequest,
)
from app.core import analysis_status
from app.core.identity import identity_digest
from app.api.dependencies import client_identity, enforce_rate_limit
from app.services import analysis_jobs, persistence_service
from app.services.quota import enforce_analysis_quota

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.post("/validate", response_model=SubmissionRequest)
def validate_submission(submission: SubmissionRequest) -> SubmissionRequest:
    """
    Validate and normalize a submission. Does not execute any code.

    By the time this function body runs, FastAPI has already parsed and
    validated `submission` against SubmissionRequest — an invalid request
    never reaches this line; FastAPI returns 422 with structured field
    errors instead. Returning the parsed model *is* the normalized version
    of the request (whitespace trimmed, structure confirmed).
    """
    return submission


# --- Async job workflow -----------------------------------------------------


@router.post(
    "",
    response_model=SubmissionCreatedResponse,
    status_code=201,
)
def create_submission(
    submission: SubmissionRequest,
    session: Session = Depends(get_db_session),
    identity: str = Depends(enforce_rate_limit),
) -> SubmissionCreatedResponse:
    """
    Create a submission (spec + candidate + reference). Returns its id.

    Rate-limited per client. The stored submission is owned by the creating
    client (a hashed identity), which scopes later reads/deletes. Only the
    immutable content is stored here — run options belong to the analysis-job
    request. Analysis is started separately via POST /submissions/{id}/analyses.
    """
    submission_id = persistence_service.create_submission_only(
        session,
        function_name=submission.function_name,
        specification=submission.specification,
        candidate_code=submission.candidate_code,
        reference_code=submission.reference_code,
        owner_digest=identity_digest(identity),
    )
    return SubmissionCreatedResponse(submission_id=submission_id)


@router.post(
    "/{submission_id}/analyses",
    response_model=AnalysisJobCreatedResponse,
    status_code=202,
)
def create_analysis(
    submission_id: uuid.UUID,
    options: AnalysisOptionsRequest,
    session: Session = Depends(get_db_session),
    identity: str = Depends(enforce_rate_limit),
) -> AnalysisJobCreatedResponse:
    """
    Create an analysis JOB for a stored submission and return immediately
    with a 'queued' status. The heavy work (generate, execute, search,
    minimize, explain) runs on a background worker; poll
    GET /submissions/{id}/analyses/{analysis_id} for status and results.

    Rate-limited and quota-checked per client: bounded concurrent analyses,
    plus an anonymous lifetime quota for keyless clients.
    """
    stored = persistence_service.get_submission_read(session, submission_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Enforce per-client concurrency + anonymous quota before enqueuing work.
    enforce_analysis_quota(session, identity)

    # Combine the immutable stored content with the run options into the
    # full request the worker consumes.
    request = SubmissionRequest(
        function_name=stored.function_name,
        specification=stored.specification,
        candidate_code=stored.candidate_code,
        reference_code=stored.reference_code,
        test_inputs=options.test_inputs,
        generate_tests=options.generate_tests,
        generation_seed=options.generation_seed,
        use_ai_tests=options.use_ai_tests,
        explain_counterexamples=options.explain_counterexamples,
        suggest_patch=options.suggest_patch,
    )
    analysis_id = analysis_jobs.create_and_enqueue_analysis(
        session, submission_id=submission_id, request=request
    )
    return AnalysisJobCreatedResponse(
        submission_id=submission_id,
        analysis_id=analysis_id,
        status=analysis_status.QUEUED,
    )


@router.post(
    "/{submission_id}/analyses/{analysis_id}/cancel",
    response_model=AnalysisRunRead,
)
def cancel_analysis(
    submission_id: uuid.UUID,
    analysis_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> AnalysisRunRead:
    """
    Request cancellation of an analysis run. If it hasn't reached a terminal
    state, it's marked 'cancelled'; the worker checks for cancellation
    between stages and stops. Already-terminal runs are returned unchanged.
    """
    run = persistence_service.get_analysis_run_read(session, submission_id, analysis_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    if not analysis_status.is_terminal(run.status):
        persistence_service.cancel_analysis_run(session, submission_id, analysis_id)
        session.commit()
        run = persistence_service.get_analysis_run_read(
            session, submission_id, analysis_id
        )
    return run


@router.get("/shared/{share_token}", response_model=SubmissionRead)
def get_shared_submission(
    share_token: str,
    session: Session = Depends(get_db_session),
) -> SubmissionRead:
    """
    Fetch a submission via its public share token. Works only if the owner
    made it public; unknown or revoked tokens return 404. This is the only
    unauthenticated read path to stored code, and it requires possession of
    the unguessable token (UUIDs are never sufficient). Registered before the
    dynamic /{submission_id} route so "shared" is never parsed as an ID.
    """
    shared = persistence_service.get_shared_submission(session, share_token)
    if shared is None:
        raise HTTPException(status_code=404, detail="Not found")
    return shared


@router.get("/{submission_id}", response_model=SubmissionRead)
def get_submission(
    submission_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> SubmissionRead:
    """Fetch a persisted submission by ID."""
    submission = persistence_service.get_submission_read(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


@router.get(
    "/{submission_id}/analyses/{analysis_id}", response_model=AnalysisRunRead
)
def get_analysis_run(
    submission_id: uuid.UUID,
    analysis_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> AnalysisRunRead:
    """
    Fetch one persisted analysis run for a submission, with its executions
    and any counterexamples. Returns 404 if the run doesn't exist or
    doesn't belong to this submission (so IDs from another submission
    can't be read through this path).
    """
    run = persistence_service.get_analysis_run_read(session, submission_id, analysis_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return run


# --- Retention / deletion + share links -------------------------------------


@router.delete("/{submission_id}", response_model=DeletionResponse)
def delete_submission(
    submission_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    identity: str = Depends(client_identity),
) -> DeletionResponse:
    """
    Delete a submission and ALL of its analyses/executions/counterexamples.

    Only the owning client may delete. To avoid letting a caller probe which
    IDs exist, a not-found and a not-owned submission both return 404 — the
    owner is the only party who can tell them apart (by having created it).
    """
    outcome = persistence_service.delete_submission_owned(
        session, submission_id, identity_digest(identity)
    )
    if outcome == "deleted":
        session.commit()
        return DeletionResponse(submission_id=submission_id, deleted=True)
    # "not_found" and "forbidden" both surface as 404.
    raise HTTPException(status_code=404, detail="Submission not found")


@router.post("/{submission_id}/share", response_model=ShareResponse)
def set_submission_share(
    submission_id: uuid.UUID,
    body: ShareRequest,
    session: Session = Depends(get_db_session),
    identity: str = Depends(client_identity),
) -> ShareResponse:
    """
    Enable or disable a public share link for a submission you own. Enabling
    mints a fresh unguessable token; disabling revokes it so any previously
    shared link stops resolving. Stored code is private by default.
    """
    status, token = persistence_service.set_share(
        session, submission_id, identity_digest(identity), make_public=body.public
    )
    if status == "ok":
        session.commit()
        return ShareResponse(
            submission_id=submission_id,
            is_public=body.public,
            share_token=token,
        )
    raise HTTPException(status_code=404, detail="Submission not found")

