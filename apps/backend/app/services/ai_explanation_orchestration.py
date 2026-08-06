"""
Orchestration glue for counterexample explanation.

Builds an ExplanationRequest from a submission and the confirmed
first-failing comparison, then runs it through the explanation service.
Kept out of comparison_service and persistence_service so neither the
execution path nor the storage path depends on the AI provider.

Ordering guarantee: this is only ever invoked by the route AFTER
analyze_submission has produced a confirmed, non-internal-error
first_failing_input — i.e. after deterministic execution has proven the
mismatch. Claude explains; it never gates or overrides that.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.explanation import ExplanationOutcome, ExplanationRequest
from app.schemas.submission import (
    SubmissionAnalysisResponse,
    SubmissionRequest,
    TestComparisonResult,
)
from app.services.ai_explanation_service import explain_counterexample
from app.services.ai_provider import AIExplanationProvider


def _find_first_confirmed_failure(
    analysis: SubmissionAnalysisResponse,
) -> TestComparisonResult | None:
    """The comparison corresponding to first_failing_input — the confirmed,
    non-internal-error mismatch, if any."""
    if analysis.first_failing_input is None:
        return None
    for comparison in analysis.comparisons:
        if (
            comparison.input == analysis.first_failing_input
            and not comparison.match
            and not comparison.internal_error
        ):
            return comparison
    return None


def build_explanation_request(
    submission: SubmissionRequest,
    comparison: TestComparisonResult,
    *,
    request_suggested_patch: bool = False,
) -> ExplanationRequest:
    candidate = comparison.candidate
    reference = comparison.reference
    return ExplanationRequest(
        function_name=submission.function_name,
        specification=submission.specification,
        candidate_code=submission.candidate_code,
        minimized_failing_input=comparison.input,
        normalized_candidate_result=candidate.model_dump(),
        normalized_reference_result=reference.model_dump(),
        candidate_exception_detail=candidate.exception_message,
        reference_exception_detail=reference.exception_message,
        candidate_line_count=len(submission.candidate_code.splitlines()),
        request_suggested_patch=request_suggested_patch,
    )


def explain_first_counterexample(
    submission: SubmissionRequest,
    analysis: SubmissionAnalysisResponse,
    provider: AIExplanationProvider | None,
    *,
    request_suggested_patch: bool = False,
    settings: Settings | None = None,
) -> ExplanationOutcome | None:
    """
    Produce an explanation for the analysis's first confirmed counterexample,
    or None if there was no confirmed failure. Never raises for provider
    failures — the service falls back deterministically.
    """
    settings = settings or get_settings()
    comparison = _find_first_confirmed_failure(analysis)
    if comparison is None:
        return None
    request = build_explanation_request(
        submission, comparison, request_suggested_patch=request_suggested_patch
    )
    return explain_counterexample(request, provider, settings=settings)
