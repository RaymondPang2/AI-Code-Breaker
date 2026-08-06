"""
Tests for app.services.hypothesis_search_service.run_differential_search.

These exercise the real search end to end — real Hypothesis generation
and shrinking, real runner launches (via the subprocess backend, pinned
by tests/conftest.py so Docker isn't required to run this suite) — using
five deliberately buggy candidate implementations. Each test narrows the
integer range and/or list size for that specific bug so the relevant
counterexample is reliably found within a modest example budget, rather
than depending on luck to stumble into it from a wide, generic range —
a standard property-based-testing practice, not a shortcut around
Hypothesis actually doing the searching.
"""

from app.schemas.hypothesis_search import HypothesisSearchRequest
from app.services.hypothesis_search_service import run_differential_search


def _request(**overrides) -> HypothesisSearchRequest:
    defaults = dict(
        function_name="f",
        specification="A function under test.",
        candidate_code="def f(xs):\n    return xs\n",
        reference_code="def f(xs):\n    return xs\n",
        max_examples=50,
        max_list_size=15,
        timeout_seconds=30.0,
    )
    return HypothesisSearchRequest(**{**defaults, **overrides})


# --- No bug: the property should hold and report no counterexample --------


def test_identical_implementations_find_no_counterexample():
    request = _request(
        function_name="double_all",
        candidate_code="def double_all(xs):\n    return [x * 2 for x in xs]\n",
        reference_code="def double_all(xs):\n    return [x * 2 for x in xs]\n",
        max_examples=30,
        seed=1,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is False
    assert response.minimal_failing_input is None
    assert response.candidate_result is None
    assert response.reference_result is None
    assert response.examples_attempted > 0
    assert response.timed_out is False
    assert response.seed_used == 1


# --- Bug 1: second-largest distinct value ----------------------------------


def test_second_largest_distinct_bug_is_found():
    request = _request(
        function_name="second_largest",
        specification="Return the second largest distinct value.",
        candidate_code="def second_largest(values):\n    return sorted(values)[-2]\n",
        reference_code=(
            "def second_largest(values):\n"
            "    unique = sorted(set(values))\n"
            "    if len(unique) < 2:\n"
            "        raise ValueError('need at least two distinct values')\n"
            "    return unique[-2]\n"
        ),
        # Narrow range -> duplicate values (the bug's trigger) are common.
        min_int_value=-5,
        max_int_value=5,
        max_examples=80,
        seed=2,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.minimal_failing_input is not None
    assert response.candidate_result is not None
    assert response.reference_result is not None
    # The two results must actually differ under our comparison rules.
    assert (
        response.candidate_result.returned_value != response.reference_result.returned_value
        or response.candidate_result.status != response.reference_result.status
    )


# --- Bug 2: off-by-one binary search ----------------------------------------


def test_off_by_one_binary_search_bug_is_found():
    # Correct: `while lo <= hi`. Buggy: `while lo < hi`, which skips the
    # lo == hi case entirely — e.g. never even checks a single-element
    # list, so has_seven([7]) wrongly returns False.
    reference_code = (
        "def has_seven(values):\n"
        "    xs = sorted(values)\n"
        "    lo, hi = 0, len(xs) - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if xs[mid] == 7:\n"
        "            return True\n"
        "        elif xs[mid] < 7:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return False\n"
    )
    candidate_code = reference_code.replace("while lo <= hi:", "while lo < hi:")

    request = _request(
        function_name="has_seven",
        specification="Return True if 7 is present in the list.",
        candidate_code=candidate_code,
        reference_code=reference_code,
        # Narrow range -> the target value 7 shows up often.
        min_int_value=-10,
        max_int_value=10,
        max_examples=80,
        seed=3,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.candidate_result.returned_value != response.reference_result.returned_value


# --- Bug 3: incorrect palindrome handling -----------------------------------


def test_incorrect_palindrome_handling_bug_is_found():
    reference_code = "def is_palindrome(values):\n    return values == values[::-1]\n"
    # Bug: checks adjacent-element equality instead of mirrored equality —
    # only recognizes "all elements identical" as a palindrome, missing
    # genuine palindromes like [1, 2, 1].
    candidate_code = (
        "def is_palindrome(values):\n"
        "    for i in range(len(values) - 1):\n"
        "        if values[i] != values[i + 1]:\n"
        "            return False\n"
        "    return True\n"
    )

    request = _request(
        function_name="is_palindrome",
        specification="Return True if the list reads the same forwards and backwards.",
        candidate_code=candidate_code,
        reference_code=reference_code,
        # Narrow range -> short lists are likely to accidentally form a
        # genuine (non-uniform) palindrome, which is what exposes the bug.
        min_int_value=-3,
        max_int_value=3,
        max_list_size=10,
        max_examples=150,
        seed=4,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.candidate_result.returned_value != response.reference_result.returned_value
    # The minimal failing input should be a genuine palindrome with more
    # than one distinct value (otherwise the buggy check would agree).
    minimal = response.minimal_failing_input
    assert minimal == minimal[::-1]
    assert len(set(minimal)) > 1


# --- Bug 4: deduplication that changes order --------------------------------


def test_dedup_that_changes_order_bug_is_found():
    reference_code = (
        "def dedup(values):\n"
        "    seen = set()\n"
        "    result = []\n"
        "    for v in values:\n"
        "        if v not in seen:\n"
        "            seen.add(v)\n"
        "            result.append(v)\n"
        "    return result\n"
    )
    # Bug: correctly deduplicates, but sorts — losing first-occurrence order.
    candidate_code = "def dedup(values):\n    return sorted(set(values))\n"

    request = _request(
        function_name="dedup",
        specification="Remove duplicates, preserving first-occurrence order.",
        candidate_code=candidate_code,
        reference_code=reference_code,
        max_examples=30,
        seed=5,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.candidate_result.returned_value != response.reference_result.returned_value
    # Both sides should have succeeded (this is an ordering bug, not a
    # crash) — the disagreement is in the returned value itself.
    assert response.candidate_result.status == "success"
    assert response.reference_result.status == "success"
    assert sorted(response.candidate_result.returned_value) == sorted(
        response.reference_result.returned_value
    )


# --- Bug 5: incorrect maximum on all-negative values ------------------------


def test_incorrect_maximum_on_all_negative_values_bug_is_found():
    reference_code = (
        "def find_max(values):\n"
        "    if not values:\n"
        "        raise ValueError('max() arg is an empty sequence')\n"
        "    return max(values)\n"
    )
    # Bug: sentinel initialized to 0 instead of the first element, so an
    # all-negative list never updates `best` and incorrectly returns 0.
    candidate_code = (
        "def find_max(values):\n"
        "    if not values:\n"
        "        raise ValueError('max() arg is an empty sequence')\n"
        "    best = 0\n"
        "    for v in values:\n"
        "        if v > best:\n"
        "            best = v\n"
        "    return best\n"
    )

    request = _request(
        function_name="find_max",
        specification="Return the maximum value in the list.",
        candidate_code=candidate_code,
        reference_code=reference_code,
        # Force every generated value negative -> the bug triggers on
        # essentially the first non-empty example, rather than depending
        # on a symmetric range happening to generate an all-negative list.
        min_int_value=-100,
        max_int_value=-1,
        min_list_size=1,
        max_examples=20,
        seed=6,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.candidate_result.returned_value == 0
    assert response.reference_result.returned_value != 0
    assert all(v < 0 for v in response.minimal_failing_input)


# --- Determinism ------------------------------------------------------------


def test_same_seed_reproduces_the_same_minimal_failing_input():
    kwargs = dict(
        function_name="second_largest",
        specification="Return the second largest distinct value.",
        candidate_code="def second_largest(values):\n    return sorted(values)[-2]\n",
        reference_code=(
            "def second_largest(values):\n"
            "    unique = sorted(set(values))\n"
            "    if len(unique) < 2:\n"
            "        raise ValueError('need at least two distinct values')\n"
            "    return unique[-2]\n"
        ),
        min_int_value=-5,
        max_int_value=5,
        max_examples=80,
        seed=42,
    )
    first = run_differential_search(_request(**kwargs))
    second = run_differential_search(_request(**kwargs))

    assert first.counterexample_found is True
    assert second.counterexample_found is True
    assert first.minimal_failing_input == second.minimal_failing_input
    assert first.examples_attempted == second.examples_attempted


# --- Response shape / bookkeeping -------------------------------------------


def test_response_reports_elapsed_time_and_examples_attempted():
    request = _request(max_examples=10, seed=7)
    response = run_differential_search(request)

    assert response.elapsed_seconds > 0
    assert response.examples_attempted > 0
    assert response.examples_attempted <= 10


def test_tight_timeout_is_reported_as_timed_out():
    # An effectively-zero budget: the very first property_fn call should
    # already exceed it, before any runner launch happens.
    request = _request(
        function_name="second_largest",
        candidate_code="def second_largest(values):\n    return sorted(values)[-2]\n",
        reference_code=(
            "def second_largest(values):\n"
            "    unique = sorted(set(values))\n"
            "    if len(unique) < 2:\n"
            "        raise ValueError('x')\n"
            "    return unique[-2]\n"
        ),
        max_examples=80,
        timeout_seconds=0.001,
        seed=8,
    )
    response = run_differential_search(request)

    assert response.timed_out is True
    assert response.examples_attempted == 0
    assert response.counterexample_found is False


# --- Integration with the deterministic minimizer --------------------------


def test_minimization_is_absent_by_default():
    request = _request(
        function_name="dedup",
        candidate_code="def dedup(values):\n    return sorted(set(values))\n",
        reference_code=(
            "def dedup(values):\n"
            "    seen = set()\n"
            "    result = []\n"
            "    for v in values:\n"
            "        if v not in seen:\n"
            "            seen.add(v)\n"
            "            result.append(v)\n"
            "    return result\n"
        ),
        max_examples=30,
        seed=5,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.minimization is None  # opt-in; not requested here


def test_minimization_runs_when_requested():
    request = _request(
        function_name="dedup",
        candidate_code="def dedup(values):\n    return sorted(set(values))\n",
        reference_code=(
            "def dedup(values):\n"
            "    seen = set()\n"
            "    result = []\n"
            "    for v in values:\n"
            "        if v not in seen:\n"
            "            seen.add(v)\n"
            "            result.append(v)\n"
            "    return result\n"
        ),
        max_examples=30,
        seed=5,
        apply_deterministic_minimization=True,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is True
    assert response.minimization is not None
    # The minimizer starts from whatever Hypothesis produced.
    assert response.minimization.original_failing_input == response.minimal_failing_input
    # And never makes it longer.
    assert len(response.minimization.minimized_failing_input) <= len(
        response.minimal_failing_input
    )
    assert response.minimization.verification_executions > 0


def test_no_minimization_when_no_counterexample_even_if_requested():
    request = _request(
        function_name="double_all",
        candidate_code="def double_all(xs):\n    return [x * 2 for x in xs]\n",
        reference_code="def double_all(xs):\n    return [x * 2 for x in xs]\n",
        max_examples=20,
        seed=1,
        apply_deterministic_minimization=True,
    )
    response = run_differential_search(request)

    assert response.counterexample_found is False
    assert response.minimization is None  # nothing to minimize
