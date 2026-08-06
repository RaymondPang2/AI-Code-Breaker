"""
Pydantic models for the deterministic counterexample minimizer (see
app.services.minimizer_service).

The minimizer takes an already-confirmed failing list[int] and tries to
simplify it while keeping the candidate/reference disagreement, verifying
every candidate simplification by rerunning both implementations in the
isolated runner.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Conservative defaults, same spirit as the Hypothesis-search limits: each
# verification step costs up to two runner launches, so both a step budget
# and a wall-clock budget exist to bound a single minimization's cost.
DEFAULT_MINIMIZER_MAX_EXECUTIONS = 300
DEFAULT_MINIMIZER_TIMEOUT_SECONDS = 30.0


class MinimizationRequest(BaseModel):
    """
    A confirmed-failing input to minimize, plus the two implementations and
    the budgets for how hard to try.

    This model does NOT re-confirm that `failing_input` actually fails —
    callers are expected to pass an input already known to produce a
    disagreement (e.g. one Hypothesis just found). The minimizer verifies
    every *simplification* it proposes, but treats the starting point as
    given; see app.services.minimizer_service.
    """

    function_name: str
    candidate_code: str
    reference_code: str
    failing_input: list[int]
    max_executions: int = Field(
        default=DEFAULT_MINIMIZER_MAX_EXECUTIONS,
        ge=1,
        description="Upper bound on runner verification executions before stopping.",
    )
    timeout_seconds: float = Field(
        default=DEFAULT_MINIMIZER_TIMEOUT_SECONDS,
        gt=0,
        description="Overall wall-clock budget for the whole minimization.",
    )


class MinimizationResult(BaseModel):
    """Outcome of one minimization run."""

    original_failing_input: list[int]
    minimized_failing_input: list[int]
    verification_executions: int = Field(
        ge=0,
        description="How many times a proposed simplification was checked by rerunning both implementations.",
    )
    length_reduction: int = Field(
        ge=0, description="len(original) - len(minimized); how many elements were removed."
    )
    numeric_complexity_reduction: int = Field(
        description=(
            "complexity(original) - complexity(minimized), where complexity "
            "is the sum of absolute values of all elements. Positive means "
            "the minimized input is numerically simpler; can be 0 if only "
            "length changed."
        )
    )
    stopped_due_to_budget: bool = Field(
        default=False,
        description=(
            "True if minimization halted because it hit max_executions or "
            "timeout_seconds rather than reaching a fixed point (no further "
            "simplification kept the failure)."
        ),
    )
    stopped_reason: str = Field(
        default="fixed_point",
        description="One of: 'fixed_point', 'execution_budget', 'timeout'.",
    )
