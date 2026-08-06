"""
AI targeted test-generation service.

Orchestrates a provider (app.services.ai_provider) to turn a spec +
candidate into validated, deduplicated test inputs — and, crucially,
never lets an AI failure break the pipeline. Every failure mode (timeout,
rate limit, unavailable, malformed output, individual invalid tests)
degrades to "return the tests we could salvage, with metadata explaining
what happened" — most often an empty list, which callers treat as "just
use deterministic + Hypothesis tests."

Claude's proposals are only *candidate inputs*. Whether each actually
exposes a bug is decided later, by real execution through the Docker
comparison engine — never by Claude and never here.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.ai_test_generation import (
    AIProposedTest,
    AITestGenerationOutcome,
    AITestGenerationRequest,
    AITestGenerationResponse,
    AIUsageMetadata,
)
from app.services.ai_provider import (
    AIProviderError,
    AIProviderMalformedResponse,
    AIProviderRateLimited,
    AIProviderTimeout,
    AIProviderUnavailable,
    AITestProvider,
)

logger = logging.getLogger(__name__)


def generate_ai_tests(
    request: AITestGenerationRequest,
    provider: AITestProvider,
    settings: Settings | None = None,
) -> AITestGenerationOutcome:
    """
    Ask the provider for targeted tests and return validated, deduplicated
    ones plus usage metadata. Never raises for provider/parse failures —
    those become an empty test list with an explanatory (non-sensitive)
    error on the usage metadata.
    """
    settings = settings or get_settings()
    max_tests = min(request.max_tests, settings.ai_max_generated_tests)

    try:
        provider_result = provider.generate(request)
    except AIProviderTimeout:
        return _empty_outcome(available=True, error="timeout")
    except AIProviderRateLimited:
        return _empty_outcome(available=True, error="rate_limited")
    except AIProviderUnavailable:
        return _empty_outcome(available=False, error="unavailable")
    except AIProviderError:
        # Any other provider-layer error — stay generic, don't leak detail.
        return _empty_outcome(available=False, error="provider_error")

    try:
        parsed = _parse_response(provider_result.raw_text)
    except AIProviderMalformedResponse:
        usage = provider_result.usage.model_copy(update={"error": "malformed_response"})
        return AITestGenerationOutcome(tests=[], usage=usage)

    validated = _validate_and_dedupe(parsed, max_tests)

    return AITestGenerationOutcome(tests=validated, usage=provider_result.usage)


def _parse_response(raw_text: str) -> list[dict]:
    """
    Turn the model's raw text into a list of raw test dicts. Tolerates the
    model wrapping JSON in markdown fences, but nothing more exotic — if
    it's not JSON with a `tests` array, that's a malformed response.
    """
    text = raw_text.strip()
    if not text:
        raise AIProviderMalformedResponse("empty response")

    # Strip a ```json ... ``` or ``` ... ``` fence if present.
    if text.startswith("```"):
        text = text.strip("`")
        # After stripping backticks a leading "json" language tag may remain.
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderMalformedResponse(f"not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "tests" not in payload:
        raise AIProviderMalformedResponse("missing 'tests' key")
    if not isinstance(payload["tests"], list):
        raise AIProviderMalformedResponse("'tests' is not an array")
    return payload["tests"]


def _validate_and_dedupe(
    raw_tests: list, max_tests: int
) -> list[AIProposedTest]:
    """
    Validate each proposed test against AIProposedTest (which enforces the
    list[int] schema, integer range, and length caps), silently dropping
    any that don't fit — an invalid item from the model must never
    contaminate the run — then deduplicate by input value and truncate to
    max_tests.
    """
    validated: list[AIProposedTest] = []
    seen_inputs: set[tuple[int, ...]] = set()

    for raw in raw_tests:
        if not isinstance(raw, dict):
            continue
        try:
            test = AIProposedTest.model_validate(raw)
        except ValidationError:
            # One malformed/invalid test doesn't sink the batch.
            continue
        key = tuple(test.input)
        if key in seen_inputs:
            continue
        seen_inputs.add(key)
        validated.append(test)
        if len(validated) >= max_tests:
            break

    return validated


def _empty_outcome(*, available: bool, error: str) -> AITestGenerationOutcome:
    return AITestGenerationOutcome(
        tests=[],
        usage=AIUsageMetadata(available=available, error=error, request_count=1),
    )
