"""
Pydantic models for POST /submissions/search — property-based differential
testing via Hypothesis (see app.services.hypothesis_search_service).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.schemas.minimization import MinimizationResult
from app.schemas.submission import CodeSubmissionBase, FunctionExecutionResult

# --- Limits ---------------------------------------------------------------
#
# "Conservative limits" per the spec: each Hypothesis example costs up to
# two Docker container launches (candidate + reference), which is orders
# of magnitude slower than a typical in-process Hypothesis property. These
# bounds keep a single search request's worst case predictable even
# though the per-example cost is much higher than Hypothesis usually
# assumes.

MAX_HYPOTHESIS_EXAMPLES = 200
DEFAULT_HYPOTHESIS_EXAMPLES = 50

MAX_HYPOTHESIS_LIST_SIZE = 200
DEFAULT_MAX_LIST_SIZE = 30

MIN_HYPOTHESIS_INT = -10_000
MAX_HYPOTHESIS_INT = 10_000
DEFAULT_MIN_INT_VALUE = -1_000
DEFAULT_MAX_INT_VALUE = 1_000

MAX_HYPOTHESIS_TIMEOUT_SECONDS = 120.0
DEFAULT_HYPOTHESIS_TIMEOUT_SECONDS = 30.0


class HypothesisSearchRequest(CodeSubmissionBase):
    """
    Request to search for a list[int] input where candidate and reference
    disagree, using Hypothesis to generate and shrink inputs.

    Hypothesis only ever generates the `list[int]` values here — it never
    sees, imports, or executes candidate_code or reference_code. Each
    generated input is executed by both implementations via the same
    Docker runner used by /submissions/analyze (see
    app.services.hypothesis_search_service).
    """

    max_examples: int = Field(
        default=DEFAULT_HYPOTHESIS_EXAMPLES,
        ge=1,
        le=MAX_HYPOTHESIS_EXAMPLES,
        description="Upper bound on how many generated inputs to try.",
    )
    min_list_size: int = Field(default=0, ge=0, le=MAX_HYPOTHESIS_LIST_SIZE)
    max_list_size: int = Field(
        default=DEFAULT_MAX_LIST_SIZE, ge=0, le=MAX_HYPOTHESIS_LIST_SIZE
    )
    min_int_value: int = Field(
        default=DEFAULT_MIN_INT_VALUE, ge=MIN_HYPOTHESIS_INT, le=MAX_HYPOTHESIS_INT
    )
    max_int_value: int = Field(
        default=DEFAULT_MAX_INT_VALUE, ge=MIN_HYPOTHESIS_INT, le=MAX_HYPOTHESIS_INT
    )
    seed: int | None = Field(
        default=None,
        description=(
            "If supplied, Hypothesis runs in deterministic mode: the same "
            "seed always generates the same sequence of examples, so the "
            "same input (or lack of one) is found every time. If omitted, "
            "Hypothesis picks its own seed and results may vary run to run."
        ),
    )
    timeout_seconds: float = Field(
        default=DEFAULT_HYPOTHESIS_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_HYPOTHESIS_TIMEOUT_SECONDS,
        description=(
            "Overall wall-clock budget for the whole search, including "
            "shrinking. Not a per-example deadline — see "
            "app.services.hypothesis_search_service for why."
        ),
    )
    apply_deterministic_minimization: bool = Field(
        default=False,
        description=(
            "If true, after Hypothesis finds (and shrinks) a counterexample, "
            "run the deterministic list[int] minimizer "
            "(app.services.minimizer_service) as an additional pass. Off by "
            "default: Hypothesis already shrinks, so this is an opt-in "
            "fallback/complement that costs extra runner executions."
        ),
    )

    @model_validator(mode="after")
    def check_ranges_are_non_empty(self) -> "HypothesisSearchRequest":
        if self.min_list_size > self.max_list_size:
            raise ValueError("min_list_size must be <= max_list_size")
        if self.min_int_value > self.max_int_value:
            raise ValueError("min_int_value must be <= max_int_value")
        return self


class HypothesisSearchResponse(BaseModel):
    """Result of one differential-testing search."""

    function_name: str
    counterexample_found: bool = Field(
        description="True if Hypothesis found a list[int] input where candidate and reference disagree."
    )
    minimal_failing_input: list[int] | None = Field(
        default=None,
        description=(
            "The smallest failing input Hypothesis found. Fully shrunk "
            "(a local minimum Hypothesis could not shrink further) unless "
            "timed_out is also true, in which case shrinking may have been "
            "cut short — see timed_out."
        ),
    )
    candidate_result: FunctionExecutionResult | None = Field(
        default=None, description="Candidate's result on minimal_failing_input, if one was found."
    )
    reference_result: FunctionExecutionResult | None = Field(
        default=None, description="Reference's result on minimal_failing_input, if one was found."
    )
    examples_attempted: int = Field(
        ge=0, description="Number of generated inputs actually executed against both implementations."
    )
    elapsed_seconds: float = Field(ge=0, description="Total wall-clock time spent on this search.")
    timed_out: bool = Field(
        default=False,
        description=(
            "True if the overall timeout_seconds budget was reached before "
            "Hypothesis concluded on its own (either because no "
            "counterexample had been found yet, or because shrinking of a "
            "found counterexample was still in progress)."
        ),
    )
    seed_used: int | None = Field(
        default=None,
        description="The seed applied for this search, if one was supplied. Reusing it reproduces this exact search.",
    )
    minimization: MinimizationResult | None = Field(
        default=None,
        description=(
            "Result of the deterministic minimizer pass, present only when "
            "apply_deterministic_minimization was requested AND a "
            "counterexample was found. Its original_failing_input is "
            "whatever Hypothesis produced in minimal_failing_input; its "
            "minimized_failing_input is the further-reduced version."
        ),
    )
