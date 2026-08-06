"""
Tests for Claude-generated counterexample explanations.

All use MockExplanationProvider — no live API calls. Covers: valid
response, malformed response, line-number validation, deterministic
fallback when unavailable/timed-out, the suggested-patch proposal, and the
invariant that the explanation never overwrites verified execution results.
"""

import json

from app.schemas.explanation import ExplanationRequest
from app.services.ai_explanation_service import (
    build_deterministic_explanation,
    explain_counterexample,
)
from app.services.ai_provider import (
    AIProviderRateLimited,
    AIProviderTimeout,
    AIProviderUnavailable,
    MockExplanationProvider,
)

CANDIDATE = "def second_largest(v):\n    return sorted(v)[-2]\n"


def _request(**overrides) -> ExplanationRequest:
    defaults = dict(
        function_name="second_largest",
        specification="Return the second largest distinct value.",
        candidate_code=CANDIDATE,
        minimized_failing_input=[5, 5],
        normalized_candidate_result={"status": "success", "returned_value": 5},
        normalized_reference_result={"status": "runtime_error", "exception_type": "ValueError"},
        candidate_exception_detail=None,
        reference_exception_detail="need at least two distinct values",
        candidate_line_count=len(CANDIDATE.splitlines()),
        request_suggested_patch=False,
    )
    return ExplanationRequest(**{**defaults, **overrides})


def _valid_explanation(**overrides) -> dict:
    base = {
        "summary": "Candidate returns a duplicate instead of the second distinct value.",
        "root_cause": "sorted(v)[-2] ignores distinctness, so [5,5] yields 5.",
        "walkthrough": ["sorted([5,5]) is [5,5]", "[-2] is 5", "but there's no second distinct value"],
        "suspected_lines": [2],
        "suggested_fix": "Deduplicate before indexing.",
        "confidence": "high",
    }
    base.update(overrides)
    return base


# --- Valid response ---------------------------------------------------------


def test_valid_explanation_is_parsed_and_labelled_ai():
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    outcome = explain_counterexample(_request(), provider)

    exp = outcome.explanation
    assert exp.source == "ai"
    assert exp.ai_generated is True
    assert exp.summary.startswith("Candidate returns")
    assert exp.suspected_lines == [2]
    assert exp.confidence == "high"
    assert exp.suggested_fix_verified is False  # never claimed correct
    assert outcome.usage.error is None


def test_valid_explanation_wrapped_in_markdown_fence():
    raw = "```json\n" + json.dumps(_valid_explanation()) + "\n```"
    provider = MockExplanationProvider(raw_response=raw)
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "ai"
    assert outcome.explanation.ai_generated is True


def test_usage_metadata_recorded():
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    outcome = explain_counterexample(_request(), provider)
    assert outcome.usage.model == "mock-model"
    assert outcome.usage.input_tokens == 200
    assert outcome.usage.latency_ms == 2.0
    assert outcome.usage.request_count == 1


# --- Line-number validation -------------------------------------------------


def test_out_of_range_line_numbers_are_dropped():
    # Candidate has 2 lines; 99 and 0 must be dropped, 2 kept.
    provider = MockExplanationProvider.with_explanation(
        _valid_explanation(suspected_lines=[2, 99, 0, -1])
    )
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.suspected_lines == [2]


def test_line_numbers_deduped_and_sorted():
    provider = MockExplanationProvider.with_explanation(
        _valid_explanation(suspected_lines=[2, 1, 2, 1])
    )
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.suspected_lines == [1, 2]


# --- Malformed response -----------------------------------------------------


def test_malformed_response_falls_back_deterministically():
    provider = MockExplanationProvider(raw_response="here's what I think...")
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "deterministic"
    assert outcome.explanation.ai_generated is False
    assert outcome.usage.error == "malformed_response"


def test_schema_invalid_response_falls_back():
    # Valid JSON, but missing required fields / bad confidence.
    provider = MockExplanationProvider(raw_response='{"summary": "x", "confidence": "bogus"}')
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "deterministic"
    assert outcome.usage.error == "malformed_response"


# --- Fallback: provider failures --------------------------------------------


def test_timeout_falls_back_deterministically():
    provider = MockExplanationProvider(raise_error=AIProviderTimeout("t"))
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "deterministic"
    assert outcome.explanation.ai_generated is False
    assert outcome.usage.error == "timeout"


def test_rate_limited_falls_back():
    provider = MockExplanationProvider(raise_error=AIProviderRateLimited("r"))
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "deterministic"
    assert outcome.usage.error == "rate_limited"


def test_unavailable_falls_back():
    provider = MockExplanationProvider(raise_error=AIProviderUnavailable("u"))
    outcome = explain_counterexample(_request(), provider)
    assert outcome.explanation.source == "deterministic"
    assert outcome.usage.available is False
    assert outcome.usage.error == "unavailable"


def test_none_provider_uses_deterministic_fallback():
    outcome = explain_counterexample(_request(), None)
    assert outcome.explanation.source == "deterministic"
    assert outcome.usage.error == "unavailable"


# --- Deterministic explanation content --------------------------------------


def test_deterministic_explanation_describes_both_results():
    exp = build_deterministic_explanation(_request())
    assert exp.ai_generated is False
    assert "[5, 5]" in exp.summary
    assert "returned 5" in exp.summary          # candidate
    assert "raised ValueError" in exp.summary   # reference
    assert exp.suspected_lines == []            # deterministic doesn't guess lines
    assert exp.suggested_fix == ""
    assert exp.confidence == "low"


# --- Suggested patch is a proposal only -------------------------------------


def test_suggested_patch_is_returned_but_marked_unverified():
    provider = MockExplanationProvider.with_explanation(
        _valid_explanation(suggested_patch="def second_largest(v):\n    return sorted(set(v))[-2]\n")
    )
    outcome = explain_counterexample(_request(request_suggested_patch=True), provider)
    assert outcome.explanation.suggested_patch is not None
    # Even with a patch, we never claim the fix is verified/correct.
    assert outcome.explanation.suggested_fix_verified is False


def test_patch_prompt_only_requested_when_flag_set():
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    explain_counterexample(_request(request_suggested_patch=True), provider)
    assert "suggested_patch" in (provider.last_user_prompt or "")

    provider2 = MockExplanationProvider.with_explanation(_valid_explanation())
    explain_counterexample(_request(request_suggested_patch=False), provider2)
    # The patch instruction should not be appended when not requested.
    assert "suggested_patch" not in (provider2.last_user_prompt or "")


# --- Isolation: never overwrite verified results, no reference code ---------


def test_explanation_does_not_carry_execution_results_back():
    # The explanation object has no field that could overwrite the stored
    # candidate_result / reference_result — it's a separate artifact.
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    outcome = explain_counterexample(_request(), provider)
    exp_fields = set(outcome.explanation.model_dump().keys())
    assert "candidate_result" not in exp_fields
    assert "reference_result" not in exp_fields


def test_reference_code_never_in_prompt():
    request = _request()
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    explain_counterexample(request, provider)
    prompt = provider.last_user_prompt or ""
    # The candidate IS in the prompt; the request has no reference_code field.
    assert "second_largest" in prompt
    assert not hasattr(request, "reference_code")


def test_prompt_includes_line_numbered_candidate():
    request = _request()
    provider = MockExplanationProvider.with_explanation(_valid_explanation())
    explain_counterexample(request, provider)
    prompt = provider.last_user_prompt or ""
    # Line-numbered format: "1| def ..." and "2| ...".
    assert "1| def second_largest" in prompt
    assert "2|" in prompt
