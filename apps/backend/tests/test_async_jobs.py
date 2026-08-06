"""
Tests for the asynchronous analysis job workflow.

These run with QUEUE_EAGER=1 (set in conftest), so enqueuing a job executes
it synchronously in-process — no worker or real Redis required. Redis-backed
behavior (job ids, retry config) is asserted at the queue/enqueue boundary
using fakeredis where a Redis connection is genuinely needed.
"""

import uuid

import pytest

from app.core import analysis_status as status
from app.repositories import analysis_repository as repo
from app.schemas.submission import SubmissionRequest
from app.services import analysis_jobs, persistence_service
from app.worker import analysis_job
from app.worker.errors import PermanentJobError, TransientJobError


# --- Helpers ----------------------------------------------------------------

BUGGY = {
    "function_name": "second_largest",
    "specification": "Return the second largest distinct value.",
    "candidate_code": "def second_largest(v):\n    return sorted(v)[-2]\n",
    "reference_code": (
        "def second_largest(v):\n"
        "    u = sorted(set(v))\n"
        "    if len(u) < 2:\n"
        "        raise ValueError('need two distinct')\n"
        "    return u[-2]\n"
    ),
}


def _create_submission(client) -> str:
    resp = client.post("/submissions", json=BUGGY)
    assert resp.status_code == 201, resp.text
    return resp.json()["submission_id"]


def _create_analysis(client, submission_id, **options) -> dict:
    resp = client.post(f"/submissions/{submission_id}/analyses", json=options)
    assert resp.status_code == 202, resp.text
    return resp.json()


# --- API flow ---------------------------------------------------------------


def test_create_submission_returns_id(client):
    submission_id = _create_submission(client)
    assert uuid.UUID(submission_id)  # parses


def test_create_analysis_returns_queued_then_completes(client):
    submission_id = _create_submission(client)
    body = _create_analysis(client, submission_id, test_inputs=[[5, 5, 5], [3, 1, 2]])

    # The POST returns an analysis id and a 'queued' status immediately...
    assert body["submission_id"] == submission_id
    assert uuid.UUID(body["analysis_id"])
    assert body["status"] == status.QUEUED

    # ...and because tests run eagerly, the job has already completed by the
    # time we poll.
    analysis_id = body["analysis_id"]
    got = client.get(f"/submissions/{submission_id}/analyses/{analysis_id}")
    assert got.status_code == 200
    run = got.json()
    assert run["status"] == status.COMPLETED
    assert run["progress"] == 1.0
    assert run["total_tests"] >= 2
    # The bug on [5,5,5] is a confirmed counterexample.
    assert run["failed_tests"] >= 1
    assert len(run["counterexamples"]) == 1


def test_get_unknown_analysis_is_404(client):
    submission_id = _create_submission(client)
    resp = client.get(f"/submissions/{submission_id}/analyses/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_create_analysis_for_unknown_submission_is_404(client):
    resp = client.post(f"/submissions/{uuid.uuid4()}/analyses", json={})
    assert resp.status_code == 404


# --- Job-level behavior (calling the job function directly) ------------------


def _make_run(db_session, request: SubmissionRequest):
    """Create submission + queued run and return (submission_id, run_id)."""
    submission_id = persistence_service.create_submission_only(
        db_session,
        function_name=request.function_name,
        specification=request.specification,
        candidate_code=request.candidate_code,
        reference_code=request.reference_code,
    )
    run = repo.create_queued_analysis_run(
        db_session,
        submission_id=submission_id,
        configuration={},
        seed=None,
    )
    db_session.commit()
    return submission_id, run.id


def test_job_marks_run_completed_and_records_counterexample(db_session):
    request = SubmissionRequest(**BUGGY, test_inputs=[[5, 5, 5]])
    submission_id, run_id = _make_run(db_session, request)

    result = analysis_job.run_analysis_job(
        str(run_id), str(submission_id), request.model_dump(mode="json")
    )
    assert result["status"] == status.COMPLETED

    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    assert run.status == status.COMPLETED
    assert run.progress == 1.0
    assert run.started_at is not None
    assert run.finished_at is not None
    assert len(run.counterexamples) == 1


def test_job_is_idempotent_on_rerun(db_session):
    request = SubmissionRequest(**BUGGY, test_inputs=[[5, 5, 5], [3, 1, 2]])
    submission_id, run_id = _make_run(db_session, request)
    payload = request.model_dump(mode="json")

    analysis_job.run_analysis_job(str(run_id), str(submission_id), payload)
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    first_exec_count = len(run.executions)
    first_ce_count = len(run.counterexamples)

    # A run that already completed is a no-op on re-invocation.
    result = analysis_job.run_analysis_job(str(run_id), str(submission_id), payload)
    assert result.get("skipped") is True
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    assert len(run.executions) == first_exec_count
    assert len(run.counterexamples) == first_ce_count


def test_job_rewrites_cleanly_when_not_terminal(db_session):
    """If a run is re-run while NOT terminal (e.g. a retry after a transient
    failure), results are cleared and rewritten rather than duplicated."""
    request = SubmissionRequest(**BUGGY, test_inputs=[[5, 5, 5], [3, 1, 2]])
    submission_id, run_id = _make_run(db_session, request)
    payload = request.model_dump(mode="json")

    analysis_job.run_analysis_job(str(run_id), str(submission_id), payload)
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    exec_count = len(run.executions)

    # Force the run back to a non-terminal state and re-run.
    repo.update_analysis_run_status(db_session, run_id, status=status.QUEUED, progress=0.0)
    db_session.commit()
    analysis_job.run_analysis_job(str(run_id), str(submission_id), payload)
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    # Same count, not doubled.
    assert len(run.executions) == exec_count


def test_cancelled_run_short_circuits(db_session):
    request = SubmissionRequest(**BUGGY, test_inputs=[[5, 5, 5]])
    submission_id, run_id = _make_run(db_session, request)
    # Cancel before the job runs.
    repo.mark_analysis_run_cancelled(db_session, run_id)
    db_session.commit()

    result = analysis_job.run_analysis_job(
        str(run_id), str(submission_id), request.model_dump(mode="json")
    )
    # Already terminal -> skipped, stays cancelled.
    assert result.get("skipped") is True
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    assert run.status == status.CANCELLED


# --- Error classification / retry policy ------------------------------------


def test_permanent_error_is_recorded_and_not_raised(db_session, monkeypatch):
    """A PermanentJobError (e.g. user-code/deterministic failure) records the
    failure and returns normally, so RQ never retries it."""
    request = SubmissionRequest(**BUGGY, test_inputs=[[1, 2]])
    submission_id, run_id = _make_run(db_session, request)

    def _boom(*a, **k):
        raise PermanentJobError("candidate failed to import")

    monkeypatch.setattr(analysis_job, "analyze_submission", _boom)

    # Does NOT raise — permanent errors are returned as failed.
    result = analysis_job.run_analysis_job(
        str(run_id), str(submission_id), request.model_dump(mode="json")
    )
    assert result["status"] == status.FAILED
    assert result["retryable"] is False
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    assert run.status == status.FAILED
    assert run.error


def test_transient_error_is_raised_for_retry(db_session, monkeypatch):
    """A TransientJobError re-raises so RQ's retry machinery re-runs it."""
    request = SubmissionRequest(**BUGGY, test_inputs=[[1, 2]])
    submission_id, run_id = _make_run(db_session, request)

    def _blip(*a, **k):
        raise TransientJobError("redis blip")

    # Patch the executing stage to raise a transient error. Because the job
    # wraps unexpected exceptions from execution as transient already, we
    # raise an explicit TransientJobError to assert the re-raise path.
    monkeypatch.setattr(analysis_job, "analyze_submission", _blip)

    with pytest.raises(TransientJobError):
        analysis_job.run_analysis_job(
            str(run_id), str(submission_id), request.model_dump(mode="json")
        )
    db_session.expire_all()
    run = repo.get_analysis_run(db_session, run_id)
    assert run.status == status.FAILED  # recorded before re-raise


def test_execution_exceptions_are_wrapped_transient(db_session, monkeypatch):
    """An unexpected exception escaping the execution stage is treated as a
    transient infra failure (retryable), NOT a permanent user-code error."""
    request = SubmissionRequest(**BUGGY, test_inputs=[[1, 2]])
    submission_id, run_id = _make_run(db_session, request)

    def _kaboom(*a, **k):
        raise RuntimeError("subprocess harness crashed")

    monkeypatch.setattr(analysis_job, "analyze_submission", _kaboom)

    with pytest.raises(TransientJobError):
        analysis_job.run_analysis_job(
            str(run_id), str(submission_id), request.model_dump(mode="json")
        )


# --- Cancellation endpoint --------------------------------------------------


def test_cancel_endpoint_marks_cancelled_before_run(client, monkeypatch):
    # Make enqueue a no-op so the eager job doesn't immediately complete the
    # run, letting us exercise the cancel endpoint against a queued run.
    from app.services import analysis_jobs as jobs

    created = {}

    def _fake_enqueue(session, *, submission_id, request, settings=None):
        run = repo.create_queued_analysis_run(
            session, submission_id=submission_id, configuration={}, seed=None
        )
        session.commit()
        created["run_id"] = run.id
        return run.id

    monkeypatch.setattr(jobs, "create_and_enqueue_analysis", _fake_enqueue)

    submission_id = _create_submission(client)
    body = _create_analysis(client, submission_id)
    analysis_id = body["analysis_id"]

    resp = client.post(
        f"/submissions/{submission_id}/analyses/{analysis_id}/cancel"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == status.CANCELLED


# --- Queue boundary (idempotent job id) -------------------------------------


def test_job_id_is_deterministic_for_a_run():
    run_id = uuid.uuid4()
    assert analysis_jobs.job_id_for_run(run_id) == f"analysis:{run_id}"
    # Same run id always yields the same job id (idempotent enqueue key).
    assert analysis_jobs.job_id_for_run(run_id) == analysis_jobs.job_id_for_run(run_id)
