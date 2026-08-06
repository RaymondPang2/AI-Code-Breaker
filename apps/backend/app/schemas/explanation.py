"""
Contracts for AI-generated explanations of confirmed counterexamples.

Hard boundaries enforced here:

  - This is only ever invoked AFTER deterministic execution has already
    confirmed a real candidate/reference mismatch. Claude explains a bug
    that the runner already proved exists; it does not decide whether the
    candidate is wrong.

  - Claude's output is advisory. It is validated against
    ExplanationResponse, labelled AI-generated, and can NEVER overwrite the
    verified execution results (candidate_result / reference_result) that
    live on the counterexample. Those are passed to Claude read-only and
    are not part of what Claude returns.

  - Line numbers Claude cites are validated against the actual line count
    of the candidate source; out-of-range numbers are dropped rather than
    trusted.

  - The suggested fix (and optional suggested patch) are proposals only.
    Nothing claims they are correct — they are explicitly NOT test-verified.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Bound the structured response so a runaway model reply stays small.
MAX_SUMMARY_LENGTH = 400
MAX_ROOT_CAUSE_LENGTH = 2_000
MAX_WALKTHROUGH_STEPS = 20
MAX_WALKTHROUGH_STEP_LENGTH = 500
MAX_SUGGESTED_FIX_LENGTH = 2_000
MAX_SUSPECTED_LINES = 50
MAX_SUGGESTED_PATCH_LENGTH = 20_000

Confidence = Literal["low", "medium", "high"]


class ExplanationRequest(BaseModel):
    """
    Everything Claude is given to explain a confirmed counterexample.

    Note what is and isn't here: the candidate code IS included (Claude is
    explaining a bug in it), the normalized results and sanitized exception
    details ARE included (read-only evidence), but the reference *code* is
    NOT — as with test generation, Claude never sees the oracle
    implementation. It's told what the reference *returned*, not how.
    """

    function_name: str
    specification: str
    candidate_code: str
    minimized_failing_input: list[int]
    normalized_candidate_result: dict[str, Any]
    normalized_reference_result: dict[str, Any]
    candidate_exception_detail: str | None = Field(
        default=None,
        description="Sanitized candidate exception message, if the candidate raised.",
    )
    reference_exception_detail: str | None = Field(
        default=None,
        description="Sanitized reference exception message, if the reference raised.",
    )
    candidate_line_count: int = Field(
        default=0,
        ge=0,
        description="Number of lines in candidate_code, used to validate cited line numbers.",
    )
    request_suggested_patch: bool = Field(
        default=False,
        description="If true, Claude is also asked for an optional proposed patch (never auto-applied).",
    )


class ExplanationResponse(BaseModel):
    """
    The structured explanation we require Claude to return. Every field is
    bounded and validated; this is untrusted model output.

    `suspected_lines` is validated for shape here (positive ints, bounded
    count). Validation *against the actual source* (dropping out-of-range
    line numbers) happens in the service, which knows candidate_line_count.
    """

    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    root_cause: str = Field(min_length=1, max_length=MAX_ROOT_CAUSE_LENGTH)
    walkthrough: list[str] = Field(default_factory=list, max_length=MAX_WALKTHROUGH_STEPS)
    suspected_lines: list[int] = Field(default_factory=list, max_length=MAX_SUSPECTED_LINES)
    suggested_fix: str = Field(default="", max_length=MAX_SUGGESTED_FIX_LENGTH)
    confidence: Confidence = "low"
    # Optional proposal only — never auto-applied. Present only if requested
    # and if the model provided one.
    suggested_patch: str | None = Field(default=None, max_length=MAX_SUGGESTED_PATCH_LENGTH)

    @field_validator("walkthrough")
    @classmethod
    def clean_walkthrough(cls, value: list[str]) -> list[str]:
        cleaned = []
        for step in value:
            if not isinstance(step, str):
                continue
            step = step.strip()
            if step:
                cleaned.append(step[:MAX_WALKTHROUGH_STEP_LENGTH])
        return cleaned

    @field_validator("suspected_lines", mode="before")
    @classmethod
    def coerce_suspected_lines(cls, value: Any) -> Any:
        """Keep only positive integers (1-based line numbers). Bools and
        non-ints are dropped; full source-range validation happens later in
        the service."""
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            if isinstance(item, bool):
                continue
            if isinstance(item, int) and item >= 1:
                out.append(item)
        return out


class ExplanationUsage(BaseModel):
    """Observability for one explanation attempt. Never holds secrets."""

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    request_count: int = 0
    available: bool = True
    error: str | None = None


class CounterexampleExplanation(BaseModel):
    """
    The stored, API-facing explanation. `source` makes the provenance
    explicit: 'ai' when Claude produced it, 'deterministic' when the
    fallback did.

    `suggested_fix_verified` is ALWAYS False here: nothing in this pipeline
    tests the suggested fix, so we never claim it's correct. The optional
    `suggested_patch` is likewise a proposal only.
    """

    source: Literal["ai", "deterministic"]
    ai_generated: bool = Field(
        description="True if this explanation came from Claude (as opposed to the deterministic fallback)."
    )
    summary: str
    root_cause: str
    walkthrough: list[str] = Field(default_factory=list)
    suspected_lines: list[int] = Field(default_factory=list)
    suggested_fix: str = ""
    suggested_fix_verified: bool = Field(
        default=False,
        description="Always False: the suggested fix is a proposal and is never test-verified by this system.",
    )
    suggested_patch: str | None = Field(
        default=None,
        description="Optional proposed patch. A proposal only — never automatically applied to the user's code.",
    )
    confidence: Confidence = "low"


class ExplanationOutcome(BaseModel):
    """What the explanation service returns: the explanation plus usage."""

    explanation: CounterexampleExplanation
    usage: ExplanationUsage = Field(default_factory=ExplanationUsage)
