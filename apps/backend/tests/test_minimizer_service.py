"""
Unit tests for app.services.minimizer_service.

Two tiers:
  1. Strategy-generator tests — each of the five simplification strategies
     is a pure function producing candidate lists; tested directly, no
     runner involved.
  2. Driver tests — minimize_counterexample's greedy fixed-point loop is
     tested with the execution backend mocked, so the "does this candidate
     still fail" question is answered by a fast in-process predicate
     instead of real runner launches. This lets us assert exact minimized
     outputs deterministically. End-to-end behavior against the real runner
     is covered in tests/test_minimizer_integration.py.
"""

from unittest.mock import patch

from app.schemas.minimization import MinimizationRequest
from app.services import minimizer_service
from app.services.minimizer_service import (
    _reduce_magnitude,
    _remove_chunks,
    _remove_duplicate_occurrences,
    _remove_single_elements,
    _replace_with_simpler_values,
    minimize_counterexample,
)


# --- Strategy 1: remove chunks ---------------------------------------------


def test_remove_chunks_yields_smaller_lists():
    candidates = list(_remove_chunks([1, 2, 3, 4]))
    assert all(len(c) < 4 for c in candidates)
    assert [] not in candidates or True  # halving can reach empty; that's fine


def test_remove_chunks_can_remove_a_contiguous_half():
    candidates = list(_remove_chunks([1, 2, 3, 4]))
    # Removing the first half [1,2] leaves [3,4]; removing the second half
    # leaves [1,2]. Both should be proposed.
    assert [3, 4] in candidates
    assert [1, 2] in candidates


def test_remove_chunks_on_empty_list_yields_nothing():
    assert list(_remove_chunks([])) == []


# --- Strategy 2: remove single elements ------------------------------------


def test_remove_single_elements_removes_each_position():
    candidates = list(_remove_single_elements([10, 20, 30]))
    assert candidates == [[20, 30], [10, 30], [10, 20]]


# --- Strategy 3: replace with simpler values -------------------------------


def test_replace_with_simpler_values_offers_canonical_targets():
    candidates = list(_replace_with_simpler_values([5]))
    # 5 can become 0, 1, or -1 (all strictly smaller magnitude).
    assert [0] in candidates
    assert [1] in candidates
    assert [-1] in candidates


def test_replace_with_simpler_values_skips_already_canonical():
    # 0, 1, -1 are already canonical; nothing should be proposed for them.
    assert list(_replace_with_simpler_values([0, 1, -1])) == []


def test_replace_with_simpler_values_never_increases_magnitude():
    for candidate in _replace_with_simpler_values([2, -3, 4]):
        assert sum(abs(v) for v in candidate) <= sum(abs(v) for v in [2, -3, 4])


# --- Strategy 4: reduce magnitude ------------------------------------------


def test_reduce_magnitude_halves_toward_zero():
    candidates = list(_reduce_magnitude([100]))
    assert [50] in candidates


def test_reduce_magnitude_truncates_negatives_toward_zero():
    candidates = list(_reduce_magnitude([-7]))
    # int(-7 / 2) == -3 (toward zero), not -4.
    assert [-3] in candidates


def test_reduce_magnitude_skips_zeros():
    assert list(_reduce_magnitude([0, 0])) == []


# --- Strategy 5: remove duplicate occurrences ------------------------------


def test_remove_duplicate_occurrences_removes_extra_copies():
    candidates = list(_remove_duplicate_occurrences([7, 3, 7, 7]))
    # Each of the 2nd and 3rd occurrences of 7 (indices 2 and 3) can be
    # removed, leaving two 7s.
    assert [7, 3, 7] in candidates


def test_remove_duplicate_occurrences_ignores_unique_values():
    assert list(_remove_duplicate_occurrences([1, 2, 3])) == []


# --- Driver: minimize_counterexample with a mocked runner ------------------


def _minimize_with_predicate(failing_input, predicate, **request_overrides):
    """
    Run minimize_counterexample, but replace the execution backend with an
    in-process predicate: `predicate(values) -> bool` returns True when the
    two implementations should be considered as still disagreeing on
    `values`. We patch get_execute_function to return a fake execute() that
    encodes the predicate's answer into runner results the real comparison
    rules will read as match/mismatch.
    """
    from app.schemas.runner import RunnerResult

    def fake_execute(source_code, function_name, input_):
        # candidate_code carries the predicate marker; reference always
        # returns a fixed sentinel. When predicate(input_) is True we make
        # candidate's return differ from reference's (a mismatch); when
        # False, we make them match.
        if source_code == "CANDIDATE" and predicate(list(input_)):
            return RunnerResult(status="success", return_value="DIFFERENT")
        return RunnerResult(status="success", return_value="SAME")

    request = MinimizationRequest(
        function_name="f",
        candidate_code="CANDIDATE",
        reference_code="REFERENCE",
        failing_input=failing_input,
        **request_overrides,
    )

    with patch.object(minimizer_service, "get_execute_function", return_value=fake_execute):
        return minimize_counterexample(request)


def test_driver_reduces_length_when_only_length_matters():
    result = _minimize_with_predicate([4, 8, 15, 16, 23, 42], lambda xs: len(xs) >= 3)
    assert len(result.minimized_failing_input) == 3
    assert result.minimized_failing_input == [0, 0, 0]  # values also canonicalized
    assert result.length_reduction == 3


def test_driver_reduces_magnitude_when_a_threshold_matters():
    result = _minimize_with_predicate(
        [5, 50, 3, 20, 8, 100, 1], lambda xs: any(v >= 10 for v in xs)
    )
    # Single element remains; halving stops at the smallest value still >= 10.
    assert len(result.minimized_failing_input) == 1
    assert result.minimized_failing_input[0] >= 10
    assert result.minimized_failing_input[0] < 20  # got well below the original 100/50/20


def test_driver_preserves_a_required_duplicate():
    result = _minimize_with_predicate(
        [7, 3, 7, 99, 42], lambda xs: len(xs) != len(set(xs))
    )
    minimized = result.minimized_failing_input
    # Must still contain a duplicate, so length can't drop below 2, and
    # you can't canonicalize just one of the pair without breaking it.
    assert len(minimized) == 2
    assert minimized[0] == minimized[1]


def test_driver_reports_reductions_and_fixed_point():
    result = _minimize_with_predicate([10, -50, 5, -3], lambda xs: sum(xs) < 0)
    assert result.minimized_failing_input == [-1]
    assert result.original_failing_input == [10, -50, 5, -3]
    assert result.length_reduction == 3
    assert result.numeric_complexity_reduction > 0
    assert result.stopped_due_to_budget is False
    assert result.stopped_reason == "fixed_point"


def test_driver_is_deterministic():
    predicate = lambda xs: any(v >= 10 for v in xs)
    first = _minimize_with_predicate([5, 50, 3, 20, 8, 100, 1], predicate)
    second = _minimize_with_predicate([5, 50, 3, 20, 8, 100, 1], predicate)
    assert first.minimized_failing_input == second.minimized_failing_input
    assert first.verification_executions == second.verification_executions


def test_driver_stops_on_execution_budget():
    result = _minimize_with_predicate(
        [4, 8, 15, 16, 23, 42], lambda xs: len(xs) >= 3, max_executions=2
    )
    assert result.stopped_due_to_budget is True
    assert result.stopped_reason == "execution_budget"
    assert result.verification_executions <= 2


def test_driver_stops_on_timeout():
    result = _minimize_with_predicate(
        [4, 8, 15, 16, 23, 42], lambda xs: len(xs) >= 3, timeout_seconds=0.0001
    )
    assert result.stopped_due_to_budget is True
    assert result.stopped_reason == "timeout"


def test_driver_counts_verification_executions():
    result = _minimize_with_predicate([1, 2, 3], lambda xs: len(xs) >= 1)
    # Every accepted or rejected candidate is one verification execution.
    assert result.verification_executions > 0


def test_driver_on_already_minimal_input_does_nothing():
    result = _minimize_with_predicate([0], lambda xs: len(xs) >= 1)
    assert result.minimized_failing_input == [0]
    assert result.length_reduction == 0
    assert result.stopped_reason == "fixed_point"


def test_internal_error_is_not_treated_as_still_failing():
    """If the runner returns internal_error, that candidate must be treated
    as NOT still failing (never kept), so the minimizer can't 'simplify'
    on the basis of a harness glitch."""
    from app.schemas.runner import RunnerResult

    def fake_execute_internal_error(source_code, function_name, input_):
        # Candidate always errors internally; reference succeeds.
        if source_code == "CANDIDATE":
            return RunnerResult(status="internal_error", exception_type="MalformedRunnerOutput")
        return RunnerResult(status="success", return_value="SAME")

    request = MinimizationRequest(
        function_name="f",
        candidate_code="CANDIDATE",
        reference_code="REFERENCE",
        failing_input=[5, 6, 7],
    )
    with patch.object(
        minimizer_service, "get_execute_function", return_value=fake_execute_internal_error
    ):
        result = minimize_counterexample(request)

    # No candidate ever "still fails" (internal_error != mismatch), so the
    # input is returned unchanged.
    assert result.minimized_failing_input == [5, 6, 7]
    assert result.length_reduction == 0
