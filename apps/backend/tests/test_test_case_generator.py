"""
Tests for app.services.test_case_generator.TestCaseGenerator.
"""

from app.schemas.submission import MAX_GENERATED_TESTS
from app.services.test_case_generator import (
    REQUIRED_CATEGORIES,
    TestCaseGenerator,
    _deduplicate,
)
from app.schemas.test_case import SelectedTestCase

# Seeds empirically confirmed (see project notes) to produce zero
# accidental input collisions across the 15 categories, so these tests can
# assert "all 15 categories present" without being flaky.
CLEAN_SEEDS = [0, 1, 42]


# --- Reproducibility --------------------------------------------------------


def test_same_seed_produces_identical_output():
    first = TestCaseGenerator(seed=7).generate()
    second = TestCaseGenerator(seed=7).generate()

    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


def test_same_seed_is_reproducible_across_many_calls():
    baseline = [c.input for c in TestCaseGenerator(seed=123).generate()]
    for _ in range(5):
        assert [c.input for c in TestCaseGenerator(seed=123).generate()] == baseline


def test_different_seeds_can_produce_different_inputs():
    seed_0 = [c.input for c in TestCaseGenerator(seed=0).generate()]
    seed_1 = [c.input for c in TestCaseGenerator(seed=1).generate()]
    assert seed_0 != seed_1


def test_default_seed_is_deterministic_without_specifying_one():
    default_a = [c.input for c in TestCaseGenerator().generate()]
    default_b = [c.input for c in TestCaseGenerator().generate()]
    assert default_a == default_b


# --- Category coverage -------------------------------------------------------


def test_all_required_categories_are_present_for_clean_seeds():
    for seed in CLEAN_SEEDS:
        categories = {case.category for case in TestCaseGenerator(seed=seed).generate()}
        assert categories == REQUIRED_CATEGORIES, f"seed={seed} missing/extra categories"


def test_generated_count_never_exceeds_max():
    for seed in range(20):
        cases = TestCaseGenerator(seed=seed).generate()
        assert len(cases) <= MAX_GENERATED_TESTS


def test_every_case_has_required_metadata():
    for case in TestCaseGenerator(seed=0).generate():
        assert case.source == "generated"
        assert case.category  # non-empty
        assert case.reason  # non-empty, human-readable


# --- Deduplication ------------------------------------------------------------


def test_deduplicate_removes_exact_duplicate_inputs():
    cases = [
        SelectedTestCase(input=[1, 2, 3], source="generated", category="a", reason="first"),
        SelectedTestCase(input=[1, 2, 3], source="generated", category="b", reason="duplicate"),
        SelectedTestCase(input=[4, 5], source="generated", category="c", reason="distinct"),
    ]
    deduped = _deduplicate(cases)
    assert [c.input for c in deduped] == [[1, 2, 3], [4, 5]]


def test_deduplicate_keeps_first_occurrence_category():
    cases = [
        SelectedTestCase(input=[0, 0, 0], source="generated", category="zeros", reason="r1"),
        SelectedTestCase(input=[0, 0, 0], source="generated", category="all_values_equal", reason="r2"),
    ]
    deduped = _deduplicate(cases)
    assert len(deduped) == 1
    assert deduped[0].category == "zeros"


def test_generate_output_has_no_duplicate_inputs_for_many_seeds():
    for seed in range(30):
        cases = TestCaseGenerator(seed=seed).generate()
        inputs_as_tuples = [tuple(c.input) for c in cases]
        assert len(inputs_as_tuples) == len(set(inputs_as_tuples)), f"seed={seed} produced duplicates"


# --- Per-category shape invariants -------------------------------------------


def _cases_by_category(seed: int = 0) -> dict[str, SelectedTestCase]:
    return {case.category: case for case in TestCaseGenerator(seed=seed).generate()}


def test_empty_list_category_is_actually_empty():
    assert _cases_by_category()["empty_list"].input == []


def test_singleton_category_has_one_element():
    assert len(_cases_by_category()["singleton"].input) == 1


def test_two_elements_category_has_two_elements():
    assert len(_cases_by_category()["two_elements"].input) == 2


def test_all_values_equal_category_is_actually_uniform():
    values = _cases_by_category()["all_values_equal"].input
    assert len(set(values)) == 1
    assert len(values) >= 2


def test_duplicate_maximum_category_repeats_the_max():
    values = _cases_by_category()["duplicate_maximum"].input
    assert values.count(max(values)) >= 2


def test_duplicate_minimum_category_repeats_the_min():
    values = _cases_by_category()["duplicate_minimum"].input
    assert values.count(min(values)) >= 2


def test_already_sorted_category_is_non_decreasing():
    values = _cases_by_category()["already_sorted"].input
    assert values == sorted(values)


def test_reverse_sorted_category_is_non_increasing():
    values = _cases_by_category()["reverse_sorted"].input
    assert values == sorted(values, reverse=True)


def test_negative_values_category_is_all_negative():
    values = _cases_by_category()["negative_values"].input
    assert all(v < 0 for v in values)


def test_zeros_category_is_all_zero():
    values = _cases_by_category()["zeros"].input
    assert all(v == 0 for v in values)
    assert len(values) >= 1


def test_mixed_positive_and_negative_category_has_both_signs():
    values = _cases_by_category()["mixed_positive_and_negative"].input
    assert any(v > 0 for v in values)
    assert any(v < 0 for v in values)


def test_integer_boundary_values_category_includes_extremes():
    values = _cases_by_category()["integer_boundary_style_values"].input
    assert -(2**31) in values
    assert 2**31 - 1 in values
    assert -(2**63) in values
    assert 2**63 - 1 in values


def test_repeated_patterns_category_actually_repeats():
    values = _cases_by_category()["repeated_patterns"].input
    assert len(values) >= 6  # at least 3 repeats of a 2-element pattern
    assert len(values) % 2 == 0
    pattern = values[:2]
    assert values == pattern * (len(values) // 2)


def test_moderate_size_list_category_is_moderately_large():
    values = _cases_by_category()["moderate_size_list"].input
    assert 30 <= len(values) <= 60
