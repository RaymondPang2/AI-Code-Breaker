"""
Shared comparison rules: candidate vs. reference agreement, and the
mapping from the internal runner protocol (RunnerResult) to the public API
contract (FunctionExecutionResult).

Extracted out of app.services.comparison_service so that
app.services.hypothesis_search_service can use the *exact same* rules for
its differential-testing property. "candidate_result must equal
reference_result under our normalized comparison rules" only means
something if there is exactly one set of normalized comparison rules —
duplicating this logic between the two services would risk them silently
drifting apart.
"""

from __future__ import annotations

from app.schemas.runner import RunnerResult
from app.schemas.submission import FunctionExecutionResult

# Statuses where the implementation raised something recognizable as a
# Python exception, as opposed to timing out, hitting our own harness
# problems, or returning a value we can't cross the JSON boundary with.
_EXCEPTION_LIKE_STATUSES = {"syntax_error", "load_error", "runtime_error"}


def to_execution_result(runner_result: RunnerResult) -> FunctionExecutionResult:
    """
    Map the internal runner protocol (RunnerResult) onto the public API
    contract (FunctionExecutionResult). Kept as an explicit, separate step
    — even though the fields currently line up one-to-one — so the two
    schemas can evolve independently regardless of which execution backend
    or caller (the deterministic analyzer, the Hypothesis search) produced
    the RunnerResult.
    """
    return FunctionExecutionResult(
        status=runner_result.status,
        returned_value=runner_result.return_value,
        exception_type=runner_result.exception_type,
        exception_message=runner_result.exception_message,
        stdout=runner_result.stdout,
        stderr=runner_result.stderr,
        runtime_ms=runner_result.runtime_ms,
    )


def compare_execution_results(
    candidate: FunctionExecutionResult, reference: FunctionExecutionResult
) -> tuple[bool, bool]:
    """
    Decide whether candidate and reference agree on one input.

    Returns (match, is_internal_error).

    Rules, in priority order:
      1. If either side's status is internal_error, this comparison is
         inconclusive because *our own harness* failed, not because the
         two implementations disagree. Always match=False, but flagged
         separately so callers don't report it as a confirmed bug.
      2. If either side timed out, that never counts as a match — not
         even against another timeout. A timeout tells us execution
         didn't finish; it says nothing about whether the two
         implementations would have agreed.
      3. If both sides returned successfully, compare the JSON-decoded
         return values with ordinary value equality.
      4. If both sides raised a recognizable exception (syntax_error,
         load_error, or runtime_error), they match only if the exception
         *type* agrees. Messages are informational only — two different
         wordings of the same exception type still count as agreement.
      5. Anything else (a success on one side vs. an exception on the
         other, an exception vs. unserializable output, etc.) is a
         confirmed disagreement.
    """
    if candidate.status == "internal_error" or reference.status == "internal_error":
        return False, True

    if candidate.status == "timeout" or reference.status == "timeout":
        return False, False

    if candidate.status == "success" and reference.status == "success":
        return candidate.returned_value == reference.returned_value, False

    if (
        candidate.status in _EXCEPTION_LIKE_STATUSES
        and reference.status in _EXCEPTION_LIKE_STATUSES
    ):
        return candidate.exception_type == reference.exception_type, False

    return False, False
