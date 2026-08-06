"""
Tests for app.services.test_selection_service.select_test_cases.
"""

from app.schemas.submission import MAX_TOTAL_TESTS, SubmissionRequest
from app.services.test_selection_service import select_test_cases

BASE_KWARGS = dict(
    function_name="f",
    specification="A function under test.",
    candidate_code="def f(xs):\n    return xs\n",
    reference_code="def f(xs):\n    return xs\n",
)


def _submission(**overrides) -> SubmissionRequest:
    return SubmissionRequest(**{**BASE_KWARGS, **overrides})


def test_generate_tests_disabled_by_default_returns_only_manual():
    submission = _submission(test_inputs=[[1, 2], [3]])
    selected = select_test_cases(submission)

    assert len(selected) == 2
    assert all(case.source == "manual" for case in selected)
    assert [case.input for case in selected] == [[1, 2], [3]]


def test_generate_tests_enabled_adds_generated_cases():
    submission = _submission(test_inputs=[[1, 2]], generate_tests=True, generation_seed=0)
    selected = select_test_cases(submission)

    assert len(selected) > 1
    assert selected[0].source == "manual"
    assert any(case.source == "generated" for case in selected)


def test_manual_inputs_always_come_before_generated():
    submission = _submission(test_inputs=[[9, 9], [8, 8]], generate_tests=True, generation_seed=0)
    selected = select_test_cases(submission)

    manual_count = sum(1 for case in selected if case.source == "manual")
    assert [case.source for case in selected[:manual_count]] == ["manual"] * manual_count


def test_same_seed_yields_the_same_generated_cases():
    submission_a = _submission(test_inputs=[], generate_tests=True, generation_seed=99)
    submission_b = _submission(test_inputs=[], generate_tests=True, generation_seed=99)

    selected_a = select_test_cases(submission_a)
    selected_b = select_test_cases(submission_b)

    assert [c.input for c in selected_a] == [c.input for c in selected_b]


def test_duplicate_manual_inputs_are_deduplicated():
    submission = _submission(test_inputs=[[1, 2], [1, 2], [3]])
    selected = select_test_cases(submission)

    assert [case.input for case in selected] == [[1, 2], [3]]


def test_generated_input_matching_a_manual_input_is_skipped():
    # Run once without a manual duplicate to discover what the generator
    # would produce for a known seed, then supply that exact input
    # manually and confirm it isn't duplicated in the combined output.
    seed = 0
    baseline = select_test_cases(_submission(test_inputs=[], generate_tests=True, generation_seed=seed))
    generated_input = baseline[0].input

    submission = _submission(
        test_inputs=[generated_input], generate_tests=True, generation_seed=seed
    )
    selected = select_test_cases(submission)

    matching_inputs = [case for case in selected if case.input == generated_input]
    assert len(matching_inputs) == 1
    # The manual supply wins: the surviving case is tagged manual, not
    # silently replaced by the generated one with the same input.
    assert matching_inputs[0].source == "manual"


def test_combined_total_never_exceeds_max_total_tests():
    # 20 manual (the schema's own per-request max) + generation enabled
    # would total more than MAX_TOTAL_TESTS, forcing real truncation.
    manual_inputs = [[i] for i in range(20)]
    submission = _submission(test_inputs=manual_inputs, generate_tests=True, generation_seed=0)
    selected = select_test_cases(submission)

    assert len(selected) <= MAX_TOTAL_TESTS
    # All manual inputs are preserved; only generated ones were truncated.
    manual_in_result = [case.input for case in selected if case.source == "manual"]
    assert manual_in_result == manual_inputs


def test_no_manual_and_generation_disabled_returns_empty():
    submission = _submission(test_inputs=[])
    selected = select_test_cases(submission)
    assert selected == []
