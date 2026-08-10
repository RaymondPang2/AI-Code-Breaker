"""
Unit tests for app.services.comparison_service.

execute_submission is mocked here (no real subprocess launches) so every
comparison rule — including ones that are awkward to trigger for real,
like an internal_error — can be tested precisely and quickly. End-to-end
behavior against the real runner subprocess is covered separately in
tests/test_analyze.py.
"""

from unittest.mock import patch

from app.schemas.runner import RunnerResult
from app.schemas.submission import SubmissionRequest
from app.services.comparison_service import analyze_submission

CANDIDATE_MARKER = "CANDIDATE_MARKER"
REFERENCE_MARKER = "REFERENCE_MARKER"


def _submission(test_inputs: list[list[int]]) -> SubmissionRequest:
    return SubmissionRequest(
        function_name="f",
        specification="A function under test.",
        candidate_code=CANDIDATE_MARKER,
        reference_code=REFERENCE_MARKER,
        test_inputs=test_inputs,
    )


def _fake_execute_submission(results_by_source_and_input: dict):
    """
    Build a stand-in for execute_submission keyed on (source_code,
    tuple(input)). analyze_submission always passes submission.candidate_code
    or submission.reference_code verbatim as source_code, so the marker
    strings above are enough to tell the two sides apart without any real
    Python source being executed.
    """

    def _fake(source_code, function_name, input_, timeout_seconds=None):
        return results_by_source_and_input[(source_code, tuple(input_))]

    return _fake


def _patched(results_by_source_and_input: dict):
    return patch(
        "app.services.comparison_service.get_execute_function",
        return_value=_fake_execute_submission(results_by_source_and_input),
    )
    

def test_matching_success_values_pass():
    results = {
        (CANDIDATE_MARKER, (1, 2, 3)): RunnerResult(status="success", return_value=6),
        (REFERENCE_MARKER, (1, 2, 3)): RunnerResult(status="success", return_value=6),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1, 2, 3]]))

    assert response.total_tests == 1
    assert response.passed_tests == 1
    assert response.failed_tests == 0
    assert response.comparisons[0].match is True
    assert response.comparisons[0].internal_error is False
    assert response.first_failing_input is None


def test_differing_success_values_fail():
    results = {
        (CANDIDATE_MARKER, (1, 2, 3)): RunnerResult(status="success", return_value=5),
        (REFERENCE_MARKER, (1, 2, 3)): RunnerResult(status="success", return_value=6),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1, 2, 3]]))

    assert response.passed_tests == 0
    assert response.failed_tests == 1
    assert response.comparisons[0].match is False
    assert response.first_failing_input == [1, 2, 3]


def test_same_exception_type_matches_despite_different_message():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(
            status="runtime_error", exception_type="IndexError", exception_message="message A"
        ),
        (REFERENCE_MARKER, (1,)): RunnerResult(
            status="runtime_error", exception_type="IndexError", exception_message="message B"
        ),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is True


def test_different_exception_types_do_not_match():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="runtime_error", exception_type="IndexError"),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="runtime_error", exception_type="ValueError"),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is False
    assert response.first_failing_input == [1]


def test_success_vs_exception_does_not_match():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="runtime_error", exception_type="ValueError"),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is False


def test_timeout_never_matches_a_normal_return():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="timeout", exception_type="TimeoutError"),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is False


def test_timeout_never_matches_an_ordinary_exception():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="timeout", exception_type="TimeoutError"),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="runtime_error", exception_type="ValueError"),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is False


def test_timeout_never_matches_another_timeout():
    """Two timeouts are not treated as agreement — a timeout means we don't
    know what the implementation would have done, on either side."""
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="timeout", exception_type="TimeoutError"),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="timeout", exception_type="TimeoutError"),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    assert response.comparisons[0].match is False


def test_internal_error_is_distinguished_from_user_code_failure():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(
            status="internal_error", exception_type="MalformedRunnerOutput"
        ),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
        (CANDIDATE_MARKER, (2,)): RunnerResult(status="success", return_value=99),
        (REFERENCE_MARKER, (2,)): RunnerResult(status="success", return_value=2),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1], [2]]))

    first, second = response.comparisons
    assert first.internal_error is True
    assert first.match is False
    assert second.internal_error is False
    assert second.match is False
    # first_failing_input should skip the internal_error case and land on
    # the genuine behavioral difference at [2].
    assert response.first_failing_input == [2]


def test_all_internal_errors_leaves_first_failing_input_none():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="internal_error"),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1]]))

    # An internal error is INCONCLUSIVE, not a failure. (This test previously
    # asserted failed_tests == 1, which encoded the accounting bug: it counted
    # inconclusive as failed while first_failing_input stayed None — the exact
    # "N failed but no differences" contradiction.)
    assert response.failed_tests == 0
    assert response.inconclusive_tests == 1
    assert response.first_failing_input is None
    assert (
        response.passed_tests + response.failed_tests + response.inconclusive_tests
        == response.total_tests
    )


def test_ordering_is_preserved():
    inputs = [[i] for i in range(10)]
    results = {}
    for i in range(10):
        results[(CANDIDATE_MARKER, (i,))] = RunnerResult(status="success", return_value=i)
        results[(REFERENCE_MARKER, (i,))] = RunnerResult(status="success", return_value=i)

    with _patched(results):
        response = analyze_submission(_submission(inputs))

    assert [comparison.input for comparison in response.comparisons] == inputs


def test_no_test_inputs_short_circuits_without_calling_runner():
    with patch("app.services.comparison_service.get_execute_function") as mock_get_execute:
        fake_execute = mock_get_execute.return_value
        response = analyze_submission(_submission([]))

    fake_execute.assert_not_called()
    assert response.total_tests == 0
    assert response.passed_tests == 0
    assert response.failed_tests == 0
    assert response.inconclusive_tests == 0
    assert response.comparisons == []
    assert response.first_failing_input is None


def test_passed_plus_failed_equals_total():
    results = {
        (CANDIDATE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
        (REFERENCE_MARKER, (1,)): RunnerResult(status="success", return_value=1),
        (CANDIDATE_MARKER, (2,)): RunnerResult(status="success", return_value=99),
        (REFERENCE_MARKER, (2,)): RunnerResult(status="success", return_value=2),
    }
    with _patched(results):
        response = analyze_submission(_submission([[1], [2]]))

    assert response.passed_tests + response.failed_tests == response.total_tests


# --- Regression: passed/failed/inconclusive accounting -----------------------
#
# The reported impossible state was "18 failed, 0 passed, but no behavioral
# differences". Root cause: failed_tests was computed as total - passed, which
# folded inconclusive (internal_error) tests into failed while first_failing
# stayed None. These tests pin the three-bucket semantics:
#   passed + failed + inconclusive == total.


def test_second_largest_5_5_3_is_a_confirmed_failure():
    # The exact built-in example: candidate sorted(values)[-2] == 5,
    # reference (dedupe) == 3 on [5, 5, 3]. Must be a confirmed FAIL with the
    # counterexample recorded — not inconclusive, not "no differences".
    results = {
        (CANDIDATE_MARKER, (5, 5, 3)): RunnerResult(status="success", return_value=5),
        (REFERENCE_MARKER, (5, 5, 3)): RunnerResult(status="success", return_value=3),
    }
    with _patched(results):
        response = analyze_submission(_submission([[5, 5, 3]]))

    assert response.total_tests == 1
    assert response.passed_tests == 0
    assert response.failed_tests == 1
    assert response.inconclusive_tests == 0
    assert response.first_failing_input == [5, 5, 3]
    assert response.comparisons[0].match is False
    assert response.comparisons[0].internal_error is False
    # Invariant.
    assert (
        response.passed_tests + response.failed_tests + response.inconclusive_tests
        == response.total_tests
    )


def test_all_internal_errors_are_inconclusive_not_failed():
    # Reproduces the reported symptom's true state: every test is an
    # internal_error (runner failing). Must be inconclusive, NOT failed, and
    # must NOT report a counterexample.
    results = {
        (CANDIDATE_MARKER, (5, 5, 3)): RunnerResult(
            status="internal_error", exception_type="EmptyRunnerOutput"
        ),
        (REFERENCE_MARKER, (5, 5, 3)): RunnerResult(
            status="internal_error", exception_type="EmptyRunnerOutput"
        ),
        (CANDIDATE_MARKER, (1, 2)): RunnerResult(
            status="internal_error", exception_type="EmptyRunnerOutput"
        ),
        (REFERENCE_MARKER, (1, 2)): RunnerResult(
            status="internal_error", exception_type="EmptyRunnerOutput"
        ),
    }
    with _patched(results):
        response = analyze_submission(_submission([[5, 5, 3], [1, 2]]))

    assert response.total_tests == 2
    assert response.passed_tests == 0
    assert response.failed_tests == 0, "internal errors must NOT count as failures"
    assert response.inconclusive_tests == 2
    assert response.first_failing_input is None, "no counterexample from harness errors"
    assert (
        response.passed_tests + response.failed_tests + response.inconclusive_tests
        == response.total_tests
    )


def test_mixed_pass_fail_inconclusive_accounting():
    results = {
        (CANDIDATE_MARKER, (5, 5, 3)): RunnerResult(status="success", return_value=5),
        (REFERENCE_MARKER, (5, 5, 3)): RunnerResult(status="success", return_value=3),
        (CANDIDATE_MARKER, (1, 2)): RunnerResult(status="success", return_value=2),
        (REFERENCE_MARKER, (1, 2)): RunnerResult(status="success", return_value=2),
        (CANDIDATE_MARKER, (9,)): RunnerResult(
            status="internal_error", exception_type="EmptyRunnerOutput"
        ),
        (REFERENCE_MARKER, (9,)): RunnerResult(status="success", return_value=9),
    }
    with _patched(results):
        response = analyze_submission(_submission([[5, 5, 3], [1, 2], [9]]))

    assert response.total_tests == 3
    assert response.passed_tests == 1
    assert response.failed_tests == 1
    assert response.inconclusive_tests == 1
    assert response.first_failing_input == [5, 5, 3]
    assert (
        response.passed_tests + response.failed_tests + response.inconclusive_tests
        == response.total_tests
    )
