"""
Tests for Claude-driven targeted test generation.

Every test uses MockTestProvider — no live API calls, ever. The mock is
scripted with either a canned raw response (to exercise parsing/validation)
or an exception (to exercise timeout/unavailable/rate-limit handling).

Covers the required scenarios: valid response, malformed response, invalid
integers, oversized lists, duplicate tests, provider timeout, provider
unavailable — plus the cross-cutting requirements: reference code is never
sent, and the pipeline degrades gracefully so analysis still works when
Claude is unavailable.
"""

import json

from app.schemas.ai_test_generation import AITestGenerationRequest, AIUsageMetadata
from app.services.ai_provider import (
    AIProviderRateLimited,
    AIProviderTimeout,
    AIProviderUnavailable,
    MockTestProvider,
    build_user_prompt,
)
from app.services.ai_test_generation_service import generate_ai_tests


def _request(**overrides) -> AITestGenerationRequest:
    defaults = dict(
        function_name="second_largest",
        specification="Return the second largest distinct value.",
        candidate_code="def second_largest(v):\n    return sorted(v)[-2]\n",
        categories_already_tested=["empty_list", "singleton"],
        non_failing_results_summary="[1,2,3] -> both returned 2",
        max_tests=8,
    )
    return AITestGenerationRequest(**{**defaults, **overrides})


# --- Valid model response ---------------------------------------------------


def test_valid_response_produces_validated_tests():
    provider = MockTestProvider.with_tests(
        [
            {"input": [5, 5, 5], "category": "all_equal", "reason": "duplicate max"},
            {"input": [-1, -2], "category": "negatives", "reason": "negative values"},
        ]
    )
    outcome = generate_ai_tests(_request(), provider)

    assert len(outcome.tests) == 2
    assert outcome.tests[0].input == [5, 5, 5]
    assert outcome.tests[0].category == "all_equal"
    assert outcome.tests[1].input == [-1, -2]
    assert outcome.usage.error is None
    assert outcome.usage.available is True


def test_valid_response_wrapped_in_markdown_fence():
    raw = '```json\n{"tests": [{"input": [7], "category": "c", "reason": "r"}]}\n```'
    provider = MockTestProvider(raw_response=raw)
    outcome = generate_ai_tests(_request(), provider)
    assert len(outcome.tests) == 1
    assert outcome.tests[0].input == [7]


def test_usage_metadata_is_recorded():
    provider = MockTestProvider.with_tests(
        [{"input": [1], "category": "c", "reason": "r"}],
        usage=AIUsageMetadata(
            model="mock-model", input_tokens=120, output_tokens=40,
            latency_ms=12.5, request_count=1, available=True,
        ),
    )
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.usage.model == "mock-model"
    assert outcome.usage.input_tokens == 120
    assert outcome.usage.output_tokens == 40
    assert outcome.usage.latency_ms == 12.5
    assert outcome.usage.request_count == 1


# --- Malformed response -----------------------------------------------------


def test_malformed_non_json_response_yields_no_tests():
    provider = MockTestProvider(raw_response="I think you should try [1,2,3]!")
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "malformed_response"


def test_response_missing_tests_key_is_malformed():
    provider = MockTestProvider(raw_response='{"suggestions": []}')
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "malformed_response"


def test_empty_response_is_malformed():
    provider = MockTestProvider(raw_response="")
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "malformed_response"


# --- Invalid integers -------------------------------------------------------


def test_invalid_integers_are_dropped_but_valid_kept():
    provider = MockTestProvider.with_tests(
        [
            {"input": [True, False], "category": "bools", "reason": "r"},   # bools -> dropped
            {"input": [2.0, 3.0], "category": "floats", "reason": "r"},     # floats -> dropped
            {"input": ["x"], "category": "strings", "reason": "r"},         # strings -> dropped
            {"input": [42], "category": "valid", "reason": "r"},            # kept
        ]
    )
    outcome = generate_ai_tests(_request(), provider)
    assert len(outcome.tests) == 1
    assert outcome.tests[0].input == [42]


def test_out_of_range_integers_are_dropped():
    provider = MockTestProvider.with_tests(
        [
            {"input": [10_000_000], "category": "huge", "reason": "r"},  # out of range
            {"input": [3], "category": "ok", "reason": "r"},
        ]
    )
    outcome = generate_ai_tests(_request(), provider)
    assert len(outcome.tests) == 1
    assert outcome.tests[0].input == [3]


# --- Oversized lists --------------------------------------------------------


def test_oversized_lists_are_dropped():
    provider = MockTestProvider.with_tests(
        [
            {"input": list(range(500)), "category": "big", "reason": "r"},  # too long
            {"input": [1, 2], "category": "ok", "reason": "r"},
        ]
    )
    outcome = generate_ai_tests(_request(), provider)
    assert len(outcome.tests) == 1
    assert outcome.tests[0].input == [1, 2]


# --- Duplicate tests --------------------------------------------------------


def test_duplicate_inputs_are_deduplicated():
    provider = MockTestProvider.with_tests(
        [
            {"input": [1, 2], "category": "a", "reason": "first"},
            {"input": [1, 2], "category": "b", "reason": "second, dup input"},
            {"input": [3, 4], "category": "c", "reason": "distinct"},
        ]
    )
    outcome = generate_ai_tests(_request(), provider)
    assert len(outcome.tests) == 2
    assert [t.input for t in outcome.tests] == [[1, 2], [3, 4]]


def test_total_tests_are_capped():
    many = [{"input": [i], "category": "c", "reason": "r"} for i in range(50)]
    provider = MockTestProvider.with_tests(many)
    # Service caps at min(request.max_tests, settings.ai_max_generated_tests).
    outcome = generate_ai_tests(_request(max_tests=5), provider)
    assert len(outcome.tests) == 5


# --- Provider timeout -------------------------------------------------------


def test_provider_timeout_degrades_to_no_tests():
    provider = MockTestProvider(raise_error=AIProviderTimeout("timed out"))
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "timeout"
    assert outcome.usage.available is True  # timeout != permanently unavailable


def test_provider_rate_limit_degrades_to_no_tests():
    provider = MockTestProvider(raise_error=AIProviderRateLimited("slow down"))
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "rate_limited"


# --- Provider unavailable ---------------------------------------------------


def test_provider_unavailable_degrades_to_no_tests():
    provider = MockTestProvider(raise_error=AIProviderUnavailable("down"))
    outcome = generate_ai_tests(_request(), provider)
    assert outcome.tests == []
    assert outcome.usage.error == "unavailable"
    assert outcome.usage.available is False


# --- Isolation: reference code is never sent --------------------------------


def test_reference_code_is_never_in_the_prompt():
    # The request model has no reference_code field at all — but assert the
    # built prompt also can't contain a telltale reference marker.
    request = _request(
        candidate_code="def f(v):\n    return sorted(v)[-2]  # CANDIDATE_MARKER\n",
    )
    prompt = build_user_prompt(request)
    assert "CANDIDATE_MARKER" in prompt  # candidate IS included
    assert "reference" not in prompt.lower() or "reference implementation" not in prompt.lower()
    # Structurally, the request type cannot carry reference code:
    assert not hasattr(request, "reference_code")


def test_prompt_includes_spec_candidate_categories_and_summary():
    request = _request()
    prompt = build_user_prompt(request)
    assert request.specification in prompt
    assert request.candidate_code in prompt
    assert "empty_list" in prompt  # a category already tested
    assert "both returned 2" in prompt  # the non-failing summary


# --- Graceful degradation: analysis still works without AI ------------------


def test_service_is_pure_and_never_raises_on_provider_failure():
    # Each provider failure returns an outcome, never propagates an exception.
    for err in (
        AIProviderTimeout("t"),
        AIProviderRateLimited("r"),
        AIProviderUnavailable("u"),
    ):
        provider = MockTestProvider(raise_error=err)
        outcome = generate_ai_tests(_request(), provider)  # must not raise
        assert outcome.tests == []
