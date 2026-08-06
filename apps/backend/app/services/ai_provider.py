"""
Provider abstraction for AI test generation.

`AITestProvider` is the interface the service depends on. Two
implementations:

  - AnthropicTestProvider: the real one, using the official `anthropic`
    Python SDK. Imported lazily so the SDK is only required when AI
    generation is actually configured/used.
  - MockTestProvider: a scripted, offline provider for the test suite, so
    tests never make live API calls.

Provider failures are surfaced as the exceptions below; the service layer
(app.services.ai_test_generation_service) catches them and degrades
gracefully. The raw candidate/spec prompt is built here, and — critically
— reference code is never part of it.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Protocol

from app.core.config import Settings
from app.schemas.ai_test_generation import (
    AITestGenerationRequest,
    AIUsageMetadata,
)

if TYPE_CHECKING:
    from app.schemas.explanation import ExplanationRequest


class AIProviderError(Exception):
    """Base class for all provider failures."""


class AIProviderTimeout(AIProviderError):
    """The provider took too long to respond."""


class AIProviderRateLimited(AIProviderError):
    """The provider rejected the request due to rate limiting."""


class AIProviderUnavailable(AIProviderError):
    """The provider is unreachable, unconfigured, or returned a server error."""


class AIProviderMalformedResponse(AIProviderError):
    """The provider responded, but not with parseable structured output."""


class ProviderResult:
    """Raw text + usage from a provider call, before schema validation."""

    def __init__(
        self, raw_text: str, usage: AIUsageMetadata
    ) -> None:
        self.raw_text = raw_text
        self.usage = usage


class AITestProvider(Protocol):
    """Interface the AI test-generation service depends on."""

    def generate(self, request: AITestGenerationRequest) -> ProviderResult:
        """
        Call the model and return its raw text response plus usage
        metadata. Raises an AIProviderError subclass on failure. Does NOT
        validate the response shape — that's the service's job, so a
        malformed-but-returned response and a transport failure are handled
        distinctly.
        """
        ...


class AIExplanationProvider(Protocol):
    """Interface the counterexample-explanation service depends on."""

    def explain(self, request: "ExplanationRequest") -> ProviderResult:
        """
        Ask the model to explain a confirmed counterexample. Same
        contract as generate(): returns raw text + usage, raises an
        AIProviderError subclass on transport failure, does not validate
        the response shape.
        """
        ...


# --- Prompt construction (shared, testable, reference-free) ----------------

_SYSTEM_PROMPT = (
    "You are a software testing assistant. You are given a natural-language "
    "specification and a CANDIDATE Python implementation of a function that "
    "takes exactly one argument: a list of integers (list[int]).\n\n"
    "Your ONLY job is to propose test INPUTS that might expose a bug in the "
    "candidate, and briefly explain why each might do so. You do NOT decide "
    "whether the candidate is correct — you never see the reference "
    "implementation, and something else runs your inputs and judges the "
    "results. Propose inputs that probe edge cases, boundaries, and any "
    "reasoning the specification implies the candidate might get wrong.\n\n"
    "Respond with ONLY a JSON object, no prose, no markdown fences, in "
    "exactly this shape:\n"
    '{"tests": [{"input": [<integers>], "category": "<short category>", '
    '"reason": "<why this may expose a bug>"}]}\n\n'
    "Every input must be a JSON array of plain integers (no floats, no "
    "booleans, no nested arrays). Keep lists small and targeted."
)


def build_user_prompt(request: AITestGenerationRequest) -> str:
    """Assemble the user-turn prompt. Contains the spec and CANDIDATE only
    — never reference code."""
    parts = [
        f"Function name: {request.function_name}",
        f"Specification:\n{request.specification}",
        "Input schema: exactly one argument, a list of integers (list[int]).",
        f"Candidate implementation:\n```python\n{request.candidate_code}\n```",
    ]
    if request.categories_already_tested:
        parts.append(
            "Categories already tested (aim for different cases): "
            + ", ".join(request.categories_already_tested)
        )
    if request.non_failing_results_summary:
        parts.append(
            "Inputs already tried that did NOT expose a bug (don't just "
            f"repeat these):\n{request.non_failing_results_summary}"
        )
    parts.append(
        f"Propose up to {request.max_tests} test inputs as the specified JSON object."
    )
    return "\n\n".join(parts)


# --- Real Anthropic implementation -----------------------------------------


class AnthropicTestProvider:
    """Real provider backed by the official `anthropic` Python SDK."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = settings.anthropic_model

    def generate(self, request: AITestGenerationRequest) -> ProviderResult:
        return self._call(_SYSTEM_PROMPT, build_user_prompt(request))

    def explain(self, request: "ExplanationRequest") -> ProviderResult:
        # Imported here to avoid a hard module-level dependency cycle
        # between provider and explanation schemas.
        from app.services.ai_explanation_prompt import (
            EXPLANATION_SYSTEM_PROMPT,
            build_explanation_prompt,
        )

        return self._call(EXPLANATION_SYSTEM_PROMPT, build_explanation_prompt(request))

    def _call(self, system_prompt: str, user_prompt: str) -> ProviderResult:
        # Import lazily so the SDK is only needed when AI features are
        # actually used (keeps it out of the import path for the rest of
        # the app, and out of environments that never call this).
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise AIProviderUnavailable(
                "the 'anthropic' package is not installed"
            ) from exc

        if not self._settings.anthropic_configured:
            raise AIProviderUnavailable("no Anthropic API key configured")

        client = anthropic.Anthropic(
            api_key=self._settings.anthropic_api_key,
            timeout=self._settings.anthropic_timeout_seconds,
        )

        started = time.perf_counter()
        try:
            message = client.messages.create(
                model=self._model,
                max_tokens=self._settings.anthropic_max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise AIProviderTimeout("Anthropic request timed out") from exc
        except anthropic.RateLimitError as exc:
            raise AIProviderRateLimited("Anthropic rate limit exceeded") from exc
        except (anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise AIProviderUnavailable("Anthropic service unavailable") from exc
        except anthropic.APIStatusError as exc:
            # Any other non-2xx. Note: we deliberately do NOT include the
            # raw exception (which could echo request details) in the
            # message beyond the status code.
            raise AIProviderUnavailable(
                f"Anthropic returned an error status ({exc.status_code})"
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        raw_text = _extract_text(message)
        usage = AIUsageMetadata(
            model=getattr(message, "model", self._model),
            input_tokens=_usage_field(message, "input_tokens"),
            output_tokens=_usage_field(message, "output_tokens"),
            latency_ms=latency_ms,
            request_count=1,
            available=True,
        )
        return ProviderResult(raw_text=raw_text, usage=usage)


def _extract_text(message) -> str:
    """Concatenate the text blocks of an Anthropic message response."""
    chunks = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "".join(chunks).strip()


def _usage_field(message, name: str) -> int | None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    return getattr(usage, name, None)


# --- Mock provider for tests -----------------------------------------------


class MockTestProvider:
    """
    Scripted provider for automated tests — no network, fully deterministic.

    Configure it with either a canned raw response string (to exercise the
    malformed/valid parsing paths) or an exception to raise (to exercise
    timeout/unavailable/rate-limit handling).
    """

    def __init__(
        self,
        *,
        raw_response: str | None = None,
        raise_error: Exception | None = None,
        usage: AIUsageMetadata | None = None,
        record_prompts: bool = True,
    ) -> None:
        self._raw_response = raw_response
        self._raise_error = raise_error
        self._usage = usage or AIUsageMetadata(
            model="mock-model", input_tokens=100, output_tokens=50,
            latency_ms=1.0, request_count=1, available=True,
        )
        self._record_prompts = record_prompts
        self.calls: list[AITestGenerationRequest] = []
        self.last_user_prompt: str | None = None

    @classmethod
    def with_tests(cls, tests: list[dict], **kwargs) -> "MockTestProvider":
        """Convenience: build a provider that returns a well-formed response
        containing the given raw test dicts."""
        return cls(raw_response=json.dumps({"tests": tests}), **kwargs)

    def generate(self, request: AITestGenerationRequest) -> ProviderResult:
        if self._record_prompts:
            self.calls.append(request)
            self.last_user_prompt = build_user_prompt(request)
        if self._raise_error is not None:
            raise self._raise_error
        return ProviderResult(raw_text=self._raw_response or "", usage=self._usage)


class MockExplanationProvider:
    """
    Scripted explanation provider for automated tests — no network.

    Configure with a canned raw response (to exercise valid/malformed
    parsing) or an exception (to exercise timeout/unavailable handling).
    """

    def __init__(
        self,
        *,
        raw_response: str | None = None,
        raise_error: Exception | None = None,
        usage: AIUsageMetadata | None = None,
        record_prompts: bool = True,
    ) -> None:
        self._raw_response = raw_response
        self._raise_error = raise_error
        self._usage = usage or AIUsageMetadata(
            model="mock-model", input_tokens=200, output_tokens=120,
            latency_ms=2.0, request_count=1, available=True,
        )
        self._record_prompts = record_prompts
        self.calls: list = []
        self.last_user_prompt: str | None = None

    @classmethod
    def with_explanation(cls, explanation: dict, **kwargs) -> "MockExplanationProvider":
        """Build a provider that returns a well-formed explanation JSON."""
        return cls(raw_response=json.dumps(explanation), **kwargs)

    def explain(self, request: "ExplanationRequest") -> ProviderResult:
        from app.services.ai_explanation_prompt import build_explanation_prompt

        if self._record_prompts:
            self.calls.append(request)
            self.last_user_prompt = build_explanation_prompt(request)
        if self._raise_error is not None:
            raise self._raise_error
        return ProviderResult(raw_text=self._raw_response or "", usage=self._usage)


def get_default_provider(settings: Settings) -> AITestProvider:
    """
    Provider factory used by the API layer. Returns the real Anthropic
    provider; raises AIProviderUnavailable if AI isn't configured, so the
    caller can degrade gracefully without ever constructing a half-built
    client. Tests inject MockTestProvider directly instead of calling this.
    """
    if not settings.anthropic_configured:
        raise AIProviderUnavailable("no Anthropic API key configured")
    return AnthropicTestProvider(settings)


def get_default_explanation_provider(settings: Settings) -> AIExplanationProvider:
    """
    Explanation-provider factory. Same contract as get_default_provider:
    raises AIProviderUnavailable if AI isn't configured, so callers can
    fall back to the deterministic explanation cleanly.
    """
    if not settings.anthropic_configured:
        raise AIProviderUnavailable("no Anthropic API key configured")
    return AnthropicTestProvider(settings)
