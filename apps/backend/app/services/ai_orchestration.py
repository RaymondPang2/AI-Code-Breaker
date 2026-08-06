"""
Orchestration glue between a SubmissionRequest, the AI test-generation
service, and the comparison engine.

This is where AI test generation is actually wired into a submission: it
builds the (reference-free) AI request, runs the provider through the
service, converts validated proposals into SelectedTestCase objects
tagged source="ai", and produces the compact "already tried, didn't fail"
summary Claude is given. Kept separate from comparison_service so the core
execution/comparison path has no dependency on the AI provider at all.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.ai_test_generation import (
    AITestGenerationOutcome,
    AITestGenerationRequest,
)
from app.schemas.submission import SubmissionRequest
from app.schemas.test_case import SelectedTestCase
from app.services.ai_provider import AITestProvider
from app.services.ai_test_generation_service import generate_ai_tests
from app.services.test_case_generator import REQUIRED_CATEGORIES


def build_ai_request(
    submission: SubmissionRequest,
    *,
    categories_already_tested: list[str] | None = None,
    non_failing_results_summary: str = "",
    settings: Settings | None = None,
) -> AITestGenerationRequest:
    """
    Build the request sent to the AI provider. Deliberately constructed
    field-by-field from the submission — reference_code is never read here,
    so it structurally cannot end up in the AI request.
    """
    settings = settings or get_settings()
    return AITestGenerationRequest(
        function_name=submission.function_name,
        specification=submission.specification,
        candidate_code=submission.candidate_code,
        categories_already_tested=categories_already_tested
        or sorted(REQUIRED_CATEGORIES),
        non_failing_results_summary=non_failing_results_summary,
        max_tests=settings.ai_max_generated_tests,
    )


def proposals_to_selected_cases(
    outcome: AITestGenerationOutcome,
) -> list[SelectedTestCase]:
    """Convert validated AI proposals into SelectedTestCase objects tagged
    with source="ai"."""
    return [
        SelectedTestCase(
            input=list(test.input),
            source="ai",
            category=test.category,
            reason=test.reason,
        )
        for test in outcome.tests
    ]


def generate_ai_test_cases(
    submission: SubmissionRequest,
    provider: AITestProvider,
    *,
    non_failing_results_summary: str = "",
    settings: Settings | None = None,
) -> tuple[list[SelectedTestCase], AITestGenerationOutcome]:
    """
    Full AI path for one submission: build request, call provider through
    the service (which never raises for provider/parse failures), convert
    to SelectedTestCase. Returns (cases, outcome) so callers can both use
    the cases and record usage/metadata.
    """
    request = build_ai_request(
        submission,
        non_failing_results_summary=non_failing_results_summary,
        settings=settings,
    )
    outcome = generate_ai_tests(request, provider, settings=settings)
    return proposals_to_selected_cases(outcome), outcome
