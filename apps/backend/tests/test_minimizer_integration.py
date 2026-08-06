"""
Integration tests for the minimizer using real buggy functions, executed
end to end through the real runner backend (subprocess, pinned by
tests/conftest.py so Docker isn't required).

These start from a known failing input (as a real caller would — e.g. one
Hypothesis found) and confirm the minimizer both preserves the failure and
actually simplifies it, verifying every step through the runner.
"""

from app.schemas.minimization import MinimizationRequest
from app.services.minimizer_service import minimize_counterexample

SECOND_LARGEST_CANDIDATE = "def second_largest(values):\n    return sorted(values)[-2]\n"
SECOND_LARGEST_REFERENCE = (
    "def second_largest(values):\n"
    "    unique = sorted(set(values))\n"
    "    if len(unique) < 2:\n"
    "        raise ValueError('need at least two distinct values')\n"
    "    return unique[-2]\n"
)


def test_minimizes_second_largest_failure():
    # A noisy failing input: lots of distinct values plus a duplicated
    # maximum (which is what actually triggers the bug). The minimizer
    # should strip it down substantially while keeping the disagreement.
    request = MinimizationRequest(
        function_name="second_largest",
        candidate_code=SECOND_LARGEST_CANDIDATE,
        reference_code=SECOND_LARGEST_REFERENCE,
        failing_input=[3, 8, 1, 9, 9, 4, 7, 2],
    )
    result = minimize_counterexample(request)

    assert result.minimized_failing_input != result.original_failing_input
    assert result.length_reduction > 0
    # The minimized input must still actually expose the bug — the whole
    # point is that every step was runner-verified.
    assert len(result.minimized_failing_input) <= len(result.original_failing_input)
    assert result.verification_executions > 0


def test_minimizes_incorrect_max_on_all_negatives():
    # Bug: sentinel initialized to 0, so all-negative input returns 0
    # instead of the true max. Minimal failing case is a single negative.
    candidate = (
        "def find_max(values):\n"
        "    if not values:\n"
        "        raise ValueError('empty')\n"
        "    best = 0\n"
        "    for v in values:\n"
        "        if v > best:\n"
        "            best = v\n"
        "    return best\n"
    )
    reference = (
        "def find_max(values):\n"
        "    if not values:\n"
        "        raise ValueError('empty')\n"
        "    return max(values)\n"
    )
    request = MinimizationRequest(
        function_name="find_max",
        candidate_code=candidate,
        reference_code=reference,
        failing_input=[-5, -20, -3, -99, -1],
    )
    result = minimize_counterexample(request)

    minimized = result.minimized_failing_input
    # Should collapse to a single negative element — the minimal shape that
    # still triggers "all-negative -> wrongly returns 0".
    assert len(minimized) == 1
    assert minimized[0] < 0
    # Canonical minimization drives it to exactly -1.
    assert minimized == [-1]
    assert result.length_reduction == 4
    assert result.stopped_reason == "fixed_point"


def test_minimizes_dedup_order_bug():
    # Bug: sorts instead of preserving first-occurrence order. Needs at
    # least two out-of-order distinct values to fail.
    candidate = "def dedup(values):\n    return sorted(set(values))\n"
    reference = (
        "def dedup(values):\n"
        "    seen = set()\n"
        "    result = []\n"
        "    for v in values:\n"
        "        if v not in seen:\n"
        "            seen.add(v)\n"
        "            result.append(v)\n"
        "    return result\n"
    )
    request = MinimizationRequest(
        function_name="dedup",
        candidate_code=candidate,
        reference_code=reference,
        failing_input=[50, 10, 30, 20, 40],
    )
    result = minimize_counterexample(request)

    minimized = result.minimized_failing_input
    # Must remain out of sorted order (that's the bug), so at least 2
    # elements, and sorting them must change the list.
    assert len(minimized) >= 2
    assert minimized != sorted(minimized)


def test_minimized_input_is_never_larger_than_original():
    request = MinimizationRequest(
        function_name="second_largest",
        candidate_code=SECOND_LARGEST_CANDIDATE,
        reference_code=SECOND_LARGEST_REFERENCE,
        failing_input=[9, 9],
    )
    result = minimize_counterexample(request)
    assert len(result.minimized_failing_input) <= 2
    assert result.length_reduction >= 0
    assert result.numeric_complexity_reduction >= 0


def test_reports_all_required_fields():
    request = MinimizationRequest(
        function_name="second_largest",
        candidate_code=SECOND_LARGEST_CANDIDATE,
        reference_code=SECOND_LARGEST_REFERENCE,
        failing_input=[5, 8, 8, 2],
    )
    result = minimize_counterexample(request)

    # Every field the milestone requires must be present and coherent.
    assert result.original_failing_input == [5, 8, 8, 2]
    assert isinstance(result.minimized_failing_input, list)
    assert result.verification_executions > 0
    assert result.length_reduction == len(result.original_failing_input) - len(
        result.minimized_failing_input
    )
    assert result.stopped_reason in ("fixed_point", "execution_budget", "timeout")
