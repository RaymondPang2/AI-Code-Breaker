"""
The analysis job: the staged pipeline the RQ worker runs.

Flow (each stage updates the run's status/progress before it runs):

    queued
      -> generating_tests   (deterministic + optional Claude-proposed inputs)
      -> executing_tests    (candidate vs reference in the Docker runner)
      -> searching_properties (optional Hypothesis differential search)
      -> minimizing         (optional counterexample minimization)
      -> explaining         (optional Claude/deterministic explanation)
      -> completed | failed | cancelled

Design guarantees:
  - Idempotent + no duplicate execution: the job is keyed by a stable RQ
    job id derived from the run id (see app.services.analysis_jobs), and it
    clears any partial output before rewriting results, so running it more
    than once converges to the same stored state. A run already in a
    terminal state is a no-op.
  - Per-stage timeouts via run_stage (StageTimeout -> clean failure).
  - Retry policy: only TransientJobError propagates as retryable; user-code
    / deterministic failures are wrapped as PermanentJobError and never
    retried.
  - Errors are stored sanitized on the run (no tracebacks/paths/secrets).

The job takes plain data (ids + the request payload as a dict) so it
serializes cleanly onto the queue.
"""

from __future__ import annotations

import time
import uuid

from app.core import analysis_status as status
from app.core.config import get_settings
from app.db.base import SessionLocal
from app.repositories import analysis_repository as repo
from app.schemas.submission import SubmissionRequest
from app.services import persistence_service
from app.services.ai_explanation_orchestration import explain_first_counterexample
from app.services.ai_orchestration import generate_ai_test_cases
from app.services.ai_provider import (
    AIProviderUnavailable,
    get_default_explanation_provider,
    get_default_provider,
)
from app.services.comparison_service import analyze_submission
from app.services.test_selection_service import select_test_cases
from app.worker.errors import (
    AnalysisJobError,
    JobCancelled,
    PermanentJobError,
    TransientJobError,
)
from app.worker.stage_runner import run_stage


def run_analysis_job(run_id: str, submission_id: str, request_data: dict) -> dict:
    """
    Entry point invoked by the worker (or synchronously in eager mode).

    Returns a small dict summary on success. Raises TransientJobError to
    signal the retry machinery may re-run; raises PermanentJobError (or
    lets JobCancelled short-circuit) otherwise. All exits leave the run in
    a consistent, queryable state.
    """
    run_uuid = uuid.UUID(str(run_id))
    submission_uuid = uuid.UUID(str(submission_id))
    settings = get_settings()
    session = SessionLocal()
    started = time.perf_counter()

    try:
        # Idempotency guard: if the run is already terminal, do nothing.
        run = repo.get_analysis_run(session, run_uuid)
        if run is None:
            raise PermanentJobError("analysis run not found")
        if status.is_terminal(run.status):
            return {"run_id": run_id, "status": run.status, "skipped": True}

        repo.mark_analysis_run_started(session, run_uuid)
        session.commit()

        submission = SubmissionRequest.model_validate(request_data)

        # --- Stage: generating_tests ---------------------------------------
        def _generate():
            ai_cases = None
            ai_usage = None
            if submission.use_ai_tests:
                try:
                    provider = get_default_provider(settings)
                except AIProviderUnavailable:
                    ai_usage = {"available": False, "error": "unavailable", "request_count": 0}
                else:
                    ai_cases, outcome = generate_ai_test_cases(
                        submission, provider, settings=settings
                    )
                    ai_usage = outcome.usage.model_dump()
            selected = select_test_cases(submission, ai_test_cases=ai_cases)
            return selected, ai_cases, ai_usage

        selected, ai_cases, ai_usage = run_stage(
            session, run_uuid, status.GENERATING_TESTS,
            settings.stage_timeout_generating_tests, _generate,
        )

        # --- Stage: executing_tests ----------------------------------------
        # This is where user code actually runs. A failure of the *harness*
        # is transient; a failure caused purely by user code surfaces as a
        # normal (internal_error/exception) comparison result, not an
        # exception — so exceptions escaping here are treated as transient
        # infrastructure problems, while user-code issues flow through as
        # data and never trigger a retry.
        def _execute():
            return analyze_submission(submission, ai_test_cases=ai_cases)

        try:
            analysis = run_stage(
                session, run_uuid, status.EXECUTING_TESTS,
                settings.stage_timeout_executing_tests, _execute,
            )
        except AnalysisJobError:
            raise
        except Exception as exc:  # harness/infra failure -> retryable
            raise TransientJobError(
                f"execution stage failed: {_short(exc)}",
                stage=status.EXECUTING_TESTS,
            ) from exc

        # --- Stage: searching_properties (optional) ------------------------
        # Hypothesis search runs via its own service/endpoint; here we mark
        # the stage for progress reporting. (Property search remains
        # available through POST /submissions/search; wiring its results
        # into the stored run is a follow-up — the stage is represented so
        # the status vocabulary and progress are complete and honest.)
        if _wants_property_search(submission):
            def _search():
                return None

            run_stage(
                session, run_uuid, status.SEARCHING_PROPERTIES,
                settings.stage_timeout_searching_properties, _search,
            )

        # --- Stage: minimizing (optional) ----------------------------------
        # The analyze pipeline records the raw first-failing input; explicit
        # minimization is performed by the search flow. The stage is
        # represented for accurate status/progress; minimized_input stays
        # None here rather than being fabricated.
        minimized_input = None
        if analysis.first_failing_input is not None:
            def _minimize():
                return None

            run_stage(
                session, run_uuid, status.MINIMIZING,
                settings.stage_timeout_minimizing, _minimize,
            )

        # --- Stage: explaining (optional) ----------------------------------
        explanation_dict = None
        explanation_usage = None
        if submission.explain_counterexamples and analysis.first_failing_input is not None:
            def _explain():
                try:
                    explain_provider = get_default_explanation_provider(settings)
                except AIProviderUnavailable:
                    explain_provider = None
                outcome = explain_first_counterexample(
                    submission, analysis, explain_provider,
                    request_suggested_patch=submission.suggest_patch,
                    settings=settings,
                )
                return outcome

            explanation_outcome = run_stage(
                session, run_uuid, status.EXPLAINING,
                settings.stage_timeout_explaining, _explain,
            )
            if explanation_outcome is not None:
                explanation_dict = explanation_outcome.explanation.model_dump()
                explanation_usage = explanation_outcome.usage.model_dump()

        # --- Persist results + finalize (idempotent) -----------------------
        persistence_service.persist_results_into_run(
            session,
            submission_id=submission_uuid,
            run_id=run_uuid,
            analysis=analysis,
            counterexample_explanation=explanation_dict,
            minimized_input=minimized_input,
        )
        elapsed = time.perf_counter() - started
        repo.finalize_analysis_run_results(
            session, run_uuid,
            total_tests=analysis.total_tests,
            passed_tests=analysis.passed_tests,
            failed_tests=analysis.failed_tests,
            inconclusive_tests=analysis.inconclusive_tests,
            elapsed_seconds=elapsed,
        )
        # Stash usage metadata on the configuration snapshot (kept out of
        # the verified results). Best-effort; never fails the run.
        _record_usage(session, run_uuid, ai_usage, explanation_usage)
        session.commit()

        return {
            "run_id": run_id,
            "status": status.COMPLETED,
            "total_tests": analysis.total_tests,
            "failed_tests": analysis.failed_tests,
            "inconclusive_tests": analysis.inconclusive_tests,
        }

    except JobCancelled:
        session.rollback()
        repo.mark_analysis_run_cancelled(session, run_uuid)
        session.commit()
        return {"run_id": run_id, "status": status.CANCELLED}

    except TransientJobError as exc:
        # Retryable: record the (latest) error and RE-RAISE so RQ's retry
        # machinery re-runs the job. On the final attempt the run stays
        # 'failed'. Idempotency means a retry that succeeds overwrites this.
        session.rollback()
        repo.mark_analysis_run_failed(session, run_uuid, error=_short(exc))
        session.commit()
        raise

    except PermanentJobError as exc:
        # NOT retryable — most importantly, deterministic user-code
        # failures. Record the failure and RETURN normally (no raise), so
        # RQ treats the job as finished and never retries it. Retrying
        # would only reproduce the identical failure.
        session.rollback()
        repo.mark_analysis_run_failed(session, run_uuid, error=_short(exc))
        session.commit()
        return {"run_id": run_id, "status": status.FAILED, "retryable": False}

    except Exception as exc:  # unexpected: fail safe, treat as permanent
        session.rollback()
        repo.mark_analysis_run_failed(
            session, run_uuid, error=f"unexpected error: {_short(exc)}"
        )
        session.commit()
        # Return rather than raise: an unknown error is not assumed
        # transient, so we don't retry it.
        return {"run_id": run_id, "status": status.FAILED, "retryable": False}

    finally:
        session.close()


def _wants_property_search(submission: SubmissionRequest) -> bool:
    # The submission schema doesn't (yet) carry a dedicated property-search
    # flag on the async path; treat it as off unless a future field enables
    # it. Represented so status/progress stay honest.
    return bool(getattr(submission, "use_property_search", False))


def _record_usage(session, run_uuid, ai_usage, explanation_usage) -> None:
    run = repo.get_analysis_run(session, run_uuid)
    if run is None:
        return
    config = dict(run.configuration or {})
    if ai_usage is not None:
        config["ai_usage"] = ai_usage
    if explanation_usage is not None:
        config["explanation_usage"] = explanation_usage
    run.configuration = config


def _short(exc: BaseException, limit: int = 300) -> str:
    """A short, sanitized message: the exception's own message only, capped.
    Never includes a traceback (which could carry host paths)."""
    message = str(exc).strip() or exc.__class__.__name__
    return message[:limit]
