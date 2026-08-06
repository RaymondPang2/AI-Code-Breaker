"""
Counterexample explanation service.

Turns a CONFIRMED counterexample into a human-readable explanation. Two
paths:

  - AI path: ask Claude (via an AIExplanationProvider), validate the
    structured response, validate cited line numbers against the actual
    candidate source, and label it AI-generated.
  - Deterministic fallback: when Claude is unavailable/unconfigured/fails,
    or returns unusable output, build a plain, honest explanation from the
    execution facts alone. Analysis never depends on Claude being up.

Invariants:
  - This is only ever called after deterministic execution confirmed the
    mismatch (the caller is responsible for that; the request carries the
    already-verified normalized results).
  - Nothing here can overwrite those verified results — the explanation is
    a separate artifact. The provider is given the results read-only and
    never returns them.
  - The suggested fix / patch are proposals only; suggested_fix_verified is
    always False.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.explanation import (
    CounterexampleExplanation,
    ExplanationOutcome,
    ExplanationRequest,
    ExplanationResponse,
    ExplanationUsage,
)
from app.services.ai_explanation_prompt import build_explanation_prompt  # noqa: F401 (re-export convenience)
from app.services.ai_provider import (
    AIExplanationProvider,
    AIProviderError,
    AIProviderMalformedResponse,
    AIProviderRateLimited,
    AIProviderTimeout,
    AIProviderUnavailable,
)

logger = logging.getLogger(__name__)


def explain_counterexample(
    request: ExplanationRequest,
    provider: AIExplanationProvider | None,
    settings: Settings | None = None,
) -> ExplanationOutcome:
    """
    Produce an explanation for a confirmed counterexample.

    If `provider` is None, or the provider fails, or its output can't be
    validated, fall back to a deterministic explanation. Never raises for
    provider/parse problems.
    """
    settings = settings or get_settings()

    if provider is None:
        return _deterministic_outcome(request, error="unavailable", available=False)

    try:
        provider_result = provider.explain(request)
    except AIProviderTimeout:
        return _deterministic_outcome(request, error="timeout", available=True)
    except AIProviderRateLimited:
        return _deterministic_outcome(request, error="rate_limited", available=True)
    except AIProviderUnavailable:
        return _deterministic_outcome(request, error="unavailable", available=False)
    except AIProviderError:
        return _deterministic_outcome(request, error="provider_error", available=False)

    try:
        parsed = _parse_response(provider_result.raw_text)
    except AIProviderMalformedResponse:
        # We reached the model but couldn't use its output. Fall back, but
        # keep the real usage numbers (latency/tokens) we did incur.
        outcome = _deterministic_outcome(request, error="malformed_response", available=True)
        outcome.usage = _usage_from_provider(provider_result.usage, error="malformed_response")
        return outcome

    explanation = _build_ai_explanation(parsed, request)
    usage = _usage_from_provider(provider_result.usage, error=None)
    return ExplanationOutcome(explanation=explanation, usage=usage)


def _parse_response(raw_text: str) -> ExplanationResponse:
    text = raw_text.strip()
    if not text:
        raise AIProviderMalformedResponse("empty response")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderMalformedResponse(f"not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AIProviderMalformedResponse("response is not a JSON object")
    try:
        return ExplanationResponse.model_validate(payload)
    except ValidationError as exc:
        raise AIProviderMalformedResponse(f"does not match schema: {exc}") from exc


def _build_ai_explanation(
    parsed: ExplanationResponse, request: ExplanationRequest
) -> CounterexampleExplanation:
    """Convert validated model output into the stored explanation, with
    line numbers validated against the real candidate source."""
    valid_lines = _validate_line_numbers(parsed.suspected_lines, request.candidate_line_count)
    return CounterexampleExplanation(
        source="ai",
        ai_generated=True,
        summary=parsed.summary,
        root_cause=parsed.root_cause,
        walkthrough=parsed.walkthrough,
        suspected_lines=valid_lines,
        suggested_fix=parsed.suggested_fix,
        suggested_fix_verified=False,  # never claimed correct
        suggested_patch=parsed.suggested_patch,
        confidence=parsed.confidence,
    )


def _validate_line_numbers(lines: list[int], line_count: int) -> list[int]:
    """
    Keep only line numbers that actually exist in the candidate source
    (1..line_count), deduplicated and sorted. A model citing a line that
    doesn't exist is dropped rather than surfaced — we never present an
    invalid location as if it were real.
    """
    if line_count <= 0:
        return []
    seen = set()
    valid = []
    for n in lines:
        if 1 <= n <= line_count and n not in seen:
            seen.add(n)
            valid.append(n)
    return sorted(valid)


# --- Deterministic fallback -------------------------------------------------


def _deterministic_outcome(
    request: ExplanationRequest, *, error: str | None, available: bool
) -> ExplanationOutcome:
    return ExplanationOutcome(
        explanation=build_deterministic_explanation(request),
        usage=ExplanationUsage(available=available, error=error, request_count=0),
    )


def build_deterministic_explanation(
    request: ExplanationRequest,
) -> CounterexampleExplanation:
    """
    A plain, honest explanation built only from the confirmed execution
    facts — no model involved. Used whenever the AI path is unavailable or
    unusable, so an explanation always exists.
    """
    candidate = request.normalized_candidate_result
    reference = request.normalized_reference_result
    input_repr = json.dumps(request.minimized_failing_input)

    cand_desc = _describe_result(candidate, request.candidate_exception_detail)
    ref_desc = _describe_result(reference, request.reference_exception_detail)

    summary = (
        f"On input {input_repr}, the candidate {cand_desc}, but the "
        f"reference {ref_desc}."
    )
    root_cause = (
        "This explanation was generated deterministically from the confirmed "
        "execution results (no AI was used). The two implementations produced "
        f"different observable behaviour on {input_repr}: candidate {cand_desc}; "
        f"reference {ref_desc}. The specific line-level cause was not analysed."
    )
    walkthrough = [
        f"The differential tester ran both implementations on {input_repr}.",
        f"The candidate {cand_desc}.",
        f"The reference (correct) behaviour {ref_desc}.",
        "Because these differ, this input is a confirmed counterexample.",
    ]
    return CounterexampleExplanation(
        source="deterministic",
        ai_generated=False,
        summary=summary,
        root_cause=root_cause,
        walkthrough=walkthrough,
        suspected_lines=[],
        suggested_fix="",
        suggested_fix_verified=False,
        suggested_patch=None,
        confidence="low",
    )


def _describe_result(result: dict, exception_detail: str | None) -> str:
    status = result.get("status")
    if status == "success":
        return f"returned {json.dumps(result.get('returned_value'))}"
    if status in {"runtime_error", "syntax_error", "load_error"}:
        exc_type = result.get("exception_type") or "an error"
        if exception_detail:
            return f"raised {exc_type} ({exception_detail})"
        return f"raised {exc_type}"
    if status == "timeout":
        return "timed out"
    return f"produced a {status} result"


def _usage_from_provider(usage, *, error: str | None) -> ExplanationUsage:
    """Map the provider's AIUsageMetadata onto ExplanationUsage, optionally
    overriding the error label."""
    return ExplanationUsage(
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=usage.latency_ms,
        request_count=usage.request_count,
        available=usage.available,
        error=error,
    )
