"""
Pydantic contracts for Claude-driven targeted test generation.

Two boundaries are enforced here:

1. What we send TO Claude (AITestGenerationRequest) — deliberately does
   NOT include reference_code. Claude only ever sees the spec, the
   candidate, the input schema, which categories were already tried, and a
   compact summary of non-failing results. Claude proposes inputs; it never
   sees the oracle.

2. What we accept BACK from Claude (AITestGenerationResponse / AIProposedTest)
   — every proposed input is validated against the supported schema
   (one list[int], bounded length, bounded integer range). Anything that
   doesn't fit is rejected rather than trusted, because model output is
   untrusted input.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.submission import (
    MAX_INPUT_LIST_SIZE,
    MAX_SOURCE_CODE_LENGTH,
    MAX_SPECIFICATION_LENGTH,
)

# Integer range accepted from AI-proposed inputs. Matches the conservative
# bound the Hypothesis search uses, so AI inputs can't smuggle in values
# far outside what the rest of the system considers reasonable.
AI_MIN_INT_VALUE = -10_000
AI_MAX_INT_VALUE = 10_000

# Hard cap on AI-proposed list length. Independent of (and stricter than)
# MAX_INPUT_LIST_SIZE — AI inputs are meant to be small, targeted probes,
# not stress tests.
AI_MAX_INPUT_LIST_SIZE = min(200, MAX_INPUT_LIST_SIZE)

# Max category / reason string lengths accepted from the model, to keep a
# runaway response bounded.
AI_MAX_CATEGORY_LENGTH = 64
AI_MAX_REASON_LENGTH = 300


class AITestGenerationRequest(BaseModel):
    """
    Everything the AI provider is allowed to see. Note the conspicuous
    absence of reference_code — that omission is a hard requirement, not an
    oversight.
    """

    function_name: str
    specification: str = Field(max_length=MAX_SPECIFICATION_LENGTH)
    candidate_code: str = Field(max_length=MAX_SOURCE_CODE_LENGTH)
    categories_already_tested: list[str] = Field(
        default_factory=list,
        description="Category labels already covered, so Claude can aim elsewhere.",
    )
    non_failing_results_summary: str = Field(
        default="",
        max_length=4_000,
        description=(
            "A compact, human-readable summary of inputs already tried that "
            "did NOT expose a disagreement — so Claude doesn't just re-propose "
            "them. Deliberately a summary, not raw execution detail."
        ),
    )
    max_tests: int = Field(
        default=8, ge=1, le=50, description="Upper bound on how many tests to propose."
    )


class AIProposedTest(BaseModel):
    """
    One test Claude proposed. Every field is validated: the input must be a
    real list[int] within the supported schema, or the whole item is
    rejected (Pydantic raises, and the provider layer drops/rejects
    accordingly).
    """

    input: list[int]
    category: str = Field(min_length=1, max_length=AI_MAX_CATEGORY_LENGTH)
    reason: str = Field(min_length=1, max_length=AI_MAX_REASON_LENGTH)

    @field_validator("input", mode="before")
    @classmethod
    def validate_input_schema(cls, value: Any) -> Any:
        """
        Reject anything that isn't a bounded list of *real* integers, before
        Pydantic's own coercion. Bools are explicitly rejected (they're
        ints in Python), and so are floats like 2.0 — the supported schema
        is strictly list[int].
        """
        if not isinstance(value, list):
            raise ValueError("input must be a JSON array of integers")
        if len(value) > AI_MAX_INPUT_LIST_SIZE:
            raise ValueError(
                f"input has {len(value)} elements, exceeding the max of "
                f"{AI_MAX_INPUT_LIST_SIZE}"
            )
        for i, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(
                    f"input[{i}] must be an integer, got {type(item).__name__}"
                )
            if not (AI_MIN_INT_VALUE <= item <= AI_MAX_INT_VALUE):
                raise ValueError(
                    f"input[{i}] = {item} is outside the supported range "
                    f"[{AI_MIN_INT_VALUE}, {AI_MAX_INT_VALUE}]"
                )
        return value


class AITestGenerationResponse(BaseModel):
    """The structured payload we require Claude to return."""

    tests: list[AIProposedTest] = Field(default_factory=list)


class AIUsageMetadata(BaseModel):
    """
    Observability for one AI generation attempt. Never contains the API
    key or any secret. Fields are best-effort — some may be None if the
    provider didn't report them (or if generation was skipped).
    """

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    request_count: int = 0
    available: bool = True
    error: str | None = Field(
        default=None,
        description="Short, non-sensitive reason AI generation didn't produce tests (e.g. 'timeout').",
    )


class AITestGenerationOutcome(BaseModel):
    """What the AI service hands back: the validated tests plus metadata."""

    tests: list[AIProposedTest] = Field(default_factory=list)
    usage: AIUsageMetadata = Field(default_factory=AIUsageMetadata)
