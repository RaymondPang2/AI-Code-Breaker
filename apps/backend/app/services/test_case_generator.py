"""
Deterministic test-input generator for list[int]-argument functions.

Given a seed, TestCaseGenerator always produces the same set of
categorized inputs — this is what makes a generated bug report
reproducible ("seed 0 always finds this input") and what makes the
generator itself testable without relying on real randomness.

This is NOT Hypothesis. It's a small, fixed catalog of structurally
interesting shapes (empty, singleton, duplicates, sorted/reverse-sorted,
boundary values, etc.) — the corners a human reviewer would think to try
by hand. Property-based, shrinking-capable generation is a later
milestone; this one only ever produces exactly the required categories.
"""

from __future__ import annotations

import random

from app.schemas.submission import MAX_GENERATED_TESTS
from app.schemas.test_case import SelectedTestCase

DEFAULT_SEED = 0

# The exact set of categories this generator is required to produce. Used
# both to size MAX_GENERATED_TESTS's expectations and so tests can assert
# "every required category is present" against a single source of truth
# rather than a hardcoded list duplicated in the test file.
REQUIRED_CATEGORIES = frozenset(
    {
        "empty_list",
        "singleton",
        "two_elements",
        "duplicate_values",
        "all_values_equal",
        "duplicate_maximum",
        "duplicate_minimum",
        "already_sorted",
        "reverse_sorted",
        "negative_values",
        "zeros",
        "mixed_positive_and_negative",
        "integer_boundary_style_values",
        "repeated_patterns",
        "moderate_size_list",
    }
)

# Value/length ranges used by categories that involve arbitrary (but
# seeded, hence reproducible) choices. Kept small and named here rather
# than as magic numbers scattered through the category methods below.
_SMALL_VALUE_RANGE = (-50, 50)
_NEGATIVE_VALUE_RANGE = (-100, -1)
_MODERATE_LIST_LENGTH_RANGE = (30, 60)
_MODERATE_VALUE_RANGE = (-1000, 1000)


class TestCaseGenerator:
    """
    Produces one deterministic, categorized input per required category.

    Usage:
        generator = TestCaseGenerator(seed=42)
        cases = generator.generate()  # list[SelectedTestCase]

    Determinism contract: constructing two generators with the same seed
    and calling generate() on each produces byte-for-byte identical
    results, forever (as long as the category methods themselves don't
    change) — generate() always calls the category methods in the same
    fixed order, so the single shared `random.Random(seed)` instance is
    consumed identically every time.
    """

    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def generate(self) -> list[SelectedTestCase]:
        cases = [
            self._empty_list(),
            self._singleton(),
            self._two_elements(),
            self._duplicate_values(),
            self._all_values_equal(),
            self._duplicate_maximum(),
            self._duplicate_minimum(),
            self._already_sorted(),
            self._reverse_sorted(),
            self._negative_values(),
            self._zeros(),
            self._mixed_positive_and_negative(),
            self._integer_boundary_values(),
            self._repeated_patterns(),
            self._moderate_size_list(),
        ]
        deduped = _deduplicate(cases)
        # Defensive, not currently load-bearing: there are exactly
        # MAX_GENERATED_TESTS category methods above, so this can only
        # ever trim duplicates, never a legitimately distinct category.
        # It exists so the cap is an enforced invariant, not an assumption.
        return deduped[:MAX_GENERATED_TESTS]

    # --- category generators, one per required category -----------------

    def _case(self, values: list[int], category: str, reason: str) -> SelectedTestCase:
        return SelectedTestCase(input=values, source="generated", category=category, reason=reason)

    def _empty_list(self) -> SelectedTestCase:
        return self._case(
            [],
            "empty_list",
            "Empty input; tests behavior when there are no elements at all.",
        )

    def _singleton(self) -> SelectedTestCase:
        value = self._rng.randint(*_SMALL_VALUE_RANGE)
        return self._case(
            [value],
            "singleton",
            "Exactly one element; many list operations special-case this.",
        )

    def _two_elements(self) -> SelectedTestCase:
        a = self._rng.randint(*_SMALL_VALUE_RANGE)
        b = self._rng.randint(*_SMALL_VALUE_RANGE)
        return self._case(
            [a, b],
            "two_elements",
            "Smallest input where pairwise comparisons or 'second element' logic applies.",
        )

    def _duplicate_values(self) -> SelectedTestCase:
        a = self._rng.randint(*_SMALL_VALUE_RANGE)
        b = self._rng.randint(*_SMALL_VALUE_RANGE)
        c = self._rng.randint(*_SMALL_VALUE_RANGE)
        return self._case(
            [a, b, a, c],
            "duplicate_values",
            "Some values repeat while others stay unique; tests handling of partial duplication.",
        )

    def _all_values_equal(self) -> SelectedTestCase:
        value = self._rng.randint(*_SMALL_VALUE_RANGE)
        return self._case(
            [value] * 4,
            "all_values_equal",
            "Every element identical; breaks logic that assumes a distinct min, max, or second-largest exists.",
        )

    def _duplicate_maximum(self) -> SelectedTestCase:
        low = self._rng.randint(-50, 0)
        high = self._rng.randint(1, 50)
        filler = low + 1 if low + 1 != high else low - 1
        return self._case(
            [low, high, high, filler],
            "duplicate_maximum",
            "The maximum value appears more than once; tests whether logic assumes a unique maximum.",
        )

    def _duplicate_minimum(self) -> SelectedTestCase:
        low = self._rng.randint(-50, 0)
        high = self._rng.randint(1, 50)
        filler = high + 1
        return self._case(
            [low, low, high, filler],
            "duplicate_minimum",
            "The minimum value appears more than once; tests whether logic assumes a unique minimum.",
        )

    def _already_sorted(self) -> SelectedTestCase:
        values = sorted(self._rng.randint(*_SMALL_VALUE_RANGE) for _ in range(5))
        return self._case(
            values,
            "already_sorted",
            "Input arrives in ascending order; catches bugs masked by accidental pre-sortedness.",
        )

    def _reverse_sorted(self) -> SelectedTestCase:
        values = sorted((self._rng.randint(*_SMALL_VALUE_RANGE) for _ in range(5)), reverse=True)
        return self._case(
            values,
            "reverse_sorted",
            "Input arrives in descending order; catches assumptions about input ordering.",
        )

    def _negative_values(self) -> SelectedTestCase:
        values = [self._rng.randint(*_NEGATIVE_VALUE_RANGE) for _ in range(4)]
        return self._case(
            values,
            "negative_values",
            "Every value is negative; tests sign-related assumptions (e.g. abs(), unsigned casts).",
        )

    def _zeros(self) -> SelectedTestCase:
        return self._case(
            [0, 0, 0],
            "zeros",
            "Every value is zero; tests falsy-value handling and division-by-zero-adjacent bugs.",
        )

    def _mixed_positive_and_negative(self) -> SelectedTestCase:
        values = [self._rng.randint(*_SMALL_VALUE_RANGE) for _ in range(6)]
        # Guarantee at least one strictly positive and one strictly
        # negative value; randint alone could occasionally miss one.
        values[0] = abs(values[0]) + 1
        values[1] = -abs(values[1]) - 1
        return self._case(
            values,
            "mixed_positive_and_negative",
            "A mix of positive and negative values; tests sign-change handling within one input.",
        )

    def _integer_boundary_values(self) -> SelectedTestCase:
        # Not seeded: these are fixed, named boundaries (32-/64-bit signed
        # int limits) rather than arbitrary choices, so they're the same
        # for every seed by design.
        values = [-(2**31), 2**31 - 1, -(2**63), 2**63 - 1, 0]
        return self._case(
            values,
            "integer_boundary_style_values",
            "Values at common 32-/64-bit integer boundaries; catches overflow-style bugs "
            "in logic ported from fixed-width-integer languages.",
        )

    def _repeated_patterns(self) -> SelectedTestCase:
        a = self._rng.randint(*_SMALL_VALUE_RANGE)
        b = self._rng.randint(*_SMALL_VALUE_RANGE)
        repeats = self._rng.randint(3, 5)
        values = [a, b] * repeats
        return self._case(
            values,
            "repeated_patterns",
            "A short pattern repeated several times; tests algorithms sensitive to periodicity.",
        )

    def _moderate_size_list(self) -> SelectedTestCase:
        length = self._rng.randint(*_MODERATE_LIST_LENGTH_RANGE)
        values = [self._rng.randint(*_MODERATE_VALUE_RANGE) for _ in range(length)]
        return self._case(
            values,
            "moderate_size_list",
            f"A moderately large input ({length} elements) beyond the hand-picked tiny cases.",
        )


def _deduplicate(cases: list[SelectedTestCase]) -> list[SelectedTestCase]:
    """
    Drop any case whose input list is identical to an earlier one (e.g. a
    particular seed's "all_values_equal" could coincidentally match its
    "zeros" case if the equal value happens to be 0 and lengths align).
    Keeps the first occurrence, so the category order above acts as a
    priority order for which category label 'wins' a given input.
    """
    seen: set[tuple[int, ...]] = set()
    deduped: list[SelectedTestCase] = []
    for case in cases:
        key = tuple(case.input)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped
