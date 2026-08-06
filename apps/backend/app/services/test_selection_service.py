"""
Combines manually supplied test inputs with generated ones (see
app.services.test_case_generator) and, optionally, AI-proposed ones (see
app.services.ai_test_generation_service) into the final, ordered list of
inputs a single analysis run will execute.
"""

from __future__ import annotations

from app.schemas.submission import MAX_TOTAL_TESTS, SubmissionRequest
from app.schemas.test_case import SelectedTestCase
from app.services.test_case_generator import TestCaseGenerator


def select_test_cases(
    submission: SubmissionRequest,
    ai_test_cases: list[SelectedTestCase] | None = None,
) -> list[SelectedTestCase]:
    """
    Build the final, ordered list of inputs to execute for one submission.

    Order and priority:
      1. Every manually supplied input, in the order the caller gave them.
         These are never dropped for space — MAX_TEST_CASES already caps
         how many a caller can supply (well under MAX_TOTAL_TESTS), so
         manual inputs always fit.
      2. If submission.generate_tests is true, generated inputs (seeded by
         submission.generation_seed) fill remaining budget up to
         MAX_TOTAL_TESTS, in the generator's fixed category order.
      3. If ai_test_cases are supplied (already validated + deduped by the
         AI service), they fill any budget still remaining, last. AI is
         purely additive: it never displaces a manual or deterministic
         input, and the whole pipeline works identically whether or not
         any AI tests are provided.

    Deduplication is global, not per-source: an input identical to an
    earlier one (from any source) is skipped rather than executed twice.
    The earlier occurrence always wins, so manual inputs can never be
    silently replaced, and an AI input duplicating a deterministic one is
    dropped in favor of the deterministic one.
    """
    selected: list[SelectedTestCase] = []
    seen: set[tuple[int, ...]] = set()

    def _add_if_new(case: SelectedTestCase) -> bool:
        if len(selected) >= MAX_TOTAL_TESTS:
            return False
        key = tuple(case.input)
        if key in seen:
            return True  # already covered; keep going
        seen.add(key)
        selected.append(case)
        return True

    for values in submission.test_inputs:
        _add_if_new(
            SelectedTestCase(
                input=values,
                source="manual",
                category="manual",
                reason="Manually supplied by the caller.",
            )
        )

    if submission.generate_tests:
        generator = TestCaseGenerator(seed=submission.generation_seed)
        for case in generator.generate():
            if len(selected) >= MAX_TOTAL_TESTS:
                break
            _add_if_new(case)

    if ai_test_cases:
        for case in ai_test_cases:
            if len(selected) >= MAX_TOTAL_TESTS:
                break
            _add_if_new(case)

    return selected[:MAX_TOTAL_TESTS]
