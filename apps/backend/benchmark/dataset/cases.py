"""
The benchmark dataset: reference/buggy-candidate pairs.

Every case stays within the supported interface — a single `list[int]`
argument in, a JSON-serializable value (or raised exception) out. Each
candidate has exactly one deliberately introduced bug in the stated
category, and at least one known counterexample where it diverges from the
reference.

These are NOT cherry-picked for detectability. Some bugs (e.g. subtle
floating-point or rare boundary cases) are deliberately hard to hit with
random or small deterministic inputs — that's the point of a benchmark. The
runner reports whatever the tool actually finds, misses included.
"""

from __future__ import annotations

from benchmark.case_schema import BenchmarkCase

CASES: list[BenchmarkCase] = [
    # --- off_by_one ---------------------------------------------------------
    BenchmarkCase(
        case_id="off_by_one_max_window",
        function_name="max_pair_sum",
        specification=(
            "Return the maximum sum of two adjacent elements in the list. "
            "For a list of length < 2, return 0."
        ),
        category="off_by_one",
        reference_code=(
            "def max_pair_sum(v):\n"
            "    if len(v) < 2:\n"
            "        return 0\n"
            "    return max(v[i] + v[i + 1] for i in range(len(v) - 1))\n"
        ),
        candidate_code=(
            "def max_pair_sum(v):\n"
            "    if len(v) < 2:\n"
            "        return 0\n"
            "    # BUG: range(len(v)) walks off the end via v[i+1].\n"
            "    best = v[0] + v[1]\n"
            "    for i in range(len(v) - 2):\n"
            "        best = max(best, v[i] + v[i + 1])\n"
            "    return best\n"
        ),
        known_counterexamples=[[1, 2, 9, 3]],
        known_agreements=[[1, 2], [5, 5, 5]],
        notes="Candidate misses the last adjacent pair (stops one short).",
    ),
    BenchmarkCase(
        case_id="off_by_one_count_threshold",
        function_name="count_ge_last",
        specification=(
            "Return how many elements are greater than or equal to the last "
            "element. For an empty list, return 0."
        ),
        category="off_by_one",
        reference_code=(
            "def count_ge_last(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    last = v[-1]\n"
            "    return sum(1 for x in v if x >= last)\n"
        ),
        candidate_code=(
            "def count_ge_last(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    last = v[-1]\n"
            "    # BUG: strict > drops elements equal to the last (incl. it).\n"
            "    return sum(1 for x in v if x > last)\n"
        ),
        known_counterexamples=[[3, 1, 3], [5, 4, 3, 2, 1]],
        known_agreements=[],
        notes=(
            "Strict vs non-strict off-by-one: candidate never counts the "
            "last element itself, so it undercounts on every non-empty list."
        ),
    ),

    # --- duplicate_handling -------------------------------------------------
    BenchmarkCase(
        case_id="duplicate_second_largest",
        function_name="second_largest",
        specification=(
            "Return the second largest DISTINCT value. If fewer than two "
            "distinct values exist, raise ValueError."
        ),
        category="duplicate_handling",
        reference_code=(
            "def second_largest(v):\n"
            "    u = sorted(set(v))\n"
            "    if len(u) < 2:\n"
            "        raise ValueError('need two distinct values')\n"
            "    return u[-2]\n"
        ),
        candidate_code=(
            "def second_largest(v):\n"
            "    # BUG: doesn't dedupe; returns second largest by position.\n"
            "    s = sorted(v)\n"
            "    return s[-2]\n"
        ),
        known_counterexamples=[[5, 5, 3]],
        known_agreements=[[1, 2, 3]],
        notes="With a repeated max, candidate returns the max again.",
    ),
    BenchmarkCase(
        case_id="duplicate_count_unique",
        function_name="count_unique",
        specification="Return the number of distinct values in the list.",
        category="duplicate_handling",
        reference_code=(
            "def count_unique(v):\n"
            "    return len(set(v))\n"
        ),
        candidate_code=(
            "def count_unique(v):\n"
            "    # BUG: counts adjacent-distinct only; misses non-adjacent dups.\n"
            "    if not v:\n"
            "        return 0\n"
            "    c = 1\n"
            "    for i in range(1, len(v)):\n"
            "        if v[i] != v[i - 1]:\n"
            "            c += 1\n"
            "    return c\n"
        ),
        known_counterexamples=[[1, 2, 1]],
        known_agreements=[[1, 1, 2, 3]],
        notes="Adjacent-dedupe logic fails on non-adjacent duplicates.",
    ),

    # --- empty_input --------------------------------------------------------
    BenchmarkCase(
        case_id="empty_mean",
        function_name="int_mean_floor",
        specification=(
            "Return the floor of the mean of the list. For an empty list, "
            "return 0."
        ),
        category="empty_input",
        reference_code=(
            "def int_mean_floor(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    return sum(v) // len(v)\n"
        ),
        candidate_code=(
            "def int_mean_floor(v):\n"
            "    # BUG: no empty guard -> ZeroDivisionError on [].\n"
            "    return sum(v) // len(v)\n"
        ),
        known_counterexamples=[[]],
        known_agreements=[[2, 4, 6]],
        notes="Empty list triggers division by zero in the candidate.",
    ),
    BenchmarkCase(
        case_id="empty_first_or_default",
        function_name="first_or_zero",
        specification=(
            "Return the first element, or 0 if the list is empty."
        ),
        category="empty_input",
        reference_code=(
            "def first_or_zero(v):\n"
            "    return v[0] if v else 0\n"
        ),
        candidate_code=(
            "def first_or_zero(v):\n"
            "    # BUG: indexes without checking empty -> IndexError on [].\n"
            "    return v[0]\n"
        ),
        known_counterexamples=[[]],
        known_agreements=[[7], [1, 2, 3]],
        notes="Missing empty-list guard.",
    ),

    # --- negative_values ----------------------------------------------------
    BenchmarkCase(
        case_id="negative_abs_max",
        function_name="max_abs",
        specification="Return the maximum absolute value in the list. Empty -> 0.",
        category="negative_values",
        reference_code=(
            "def max_abs(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    return max(abs(x) for x in v)\n"
        ),
        candidate_code=(
            "def max_abs(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    # BUG: forgets abs; returns the max signed value.\n"
            "    return max(v)\n"
        ),
        known_counterexamples=[[-9, 1, 2]],
        known_agreements=[[3, 5, 1]],
        notes="Negative magnitude larger than any positive value.",
    ),
    BenchmarkCase(
        case_id="negative_all_positive",
        function_name="all_positive",
        specification=(
            "Return True if every element is strictly greater than zero, "
            "else False. Empty list -> True (vacuously)."
        ),
        category="negative_values",
        reference_code=(
            "def all_positive(v):\n"
            "    return all(x > 0 for x in v)\n"
        ),
        candidate_code=(
            "def all_positive(v):\n"
            "    # BUG: treats 0 and negatives via >= 0, so 0 passes and\n"
            "    # negatives still fail — but 0 should NOT be positive.\n"
            "    return all(x >= 0 for x in v)\n"
        ),
        known_counterexamples=[[0]],
        known_agreements=[[1, 2, 3], [-1, 2]],
        notes="Zero is incorrectly treated as positive.",
    ),

    # --- incorrect_exception_behavior --------------------------------------
    BenchmarkCase(
        case_id="exception_wrong_type",
        function_name="only_element",
        specification=(
            "Return the single element of a one-element list. If the list "
            "does not have exactly one element, raise ValueError."
        ),
        category="incorrect_exception_behavior",
        reference_code=(
            "def only_element(v):\n"
            "    if len(v) != 1:\n"
            "        raise ValueError('expected exactly one element')\n"
            "    return v[0]\n"
        ),
        candidate_code=(
            "def only_element(v):\n"
            "    # BUG: raises IndexError (wrong type) instead of ValueError\n"
            "    # for the empty case, and returns v[0] for longer lists.\n"
            "    return v[0] if len(v) >= 1 else [][0]\n"
        ),
        known_counterexamples=[[1, 2]],
        known_agreements=[[42]],
        notes="Should raise ValueError on len!=1; candidate returns instead.",
    ),
    BenchmarkCase(
        case_id="exception_missing_raise",
        function_name="checked_head",
        specification=(
            "Return the first element. Raise ValueError on an empty list."
        ),
        category="incorrect_exception_behavior",
        reference_code=(
            "def checked_head(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    return v[0]\n"
        ),
        candidate_code=(
            "def checked_head(v):\n"
            "    # BUG: raises IndexError, not ValueError, on empty input.\n"
            "    return v[0]\n"
        ),
        known_counterexamples=[[]],
        known_agreements=[[9, 8]],
        notes="Wrong exception type on empty input.",
    ),

    # --- mutation_of_input --------------------------------------------------
    BenchmarkCase(
        case_id="mutation_sorted_copy",
        function_name="sorted_head3",
        specification=(
            "Return a NEW list of the three smallest elements in ascending "
            "order (or fewer if the list is shorter). The input list must "
            "not be modified."
        ),
        category="mutation_of_input",
        reference_code=(
            "def sorted_head3(v):\n"
            "    return sorted(v)[:3]\n"
        ),
        candidate_code=(
            "def sorted_head3(v):\n"
            "    # BUG: sorts in place, mutating the caller's list.\n"
            "    v.sort()\n"
            "    return v[:3]\n"
        ),
        known_counterexamples=[[3, 1, 2]],
        known_agreements=[[1, 2, 3]],
        notes=(
            "Detectable only if the harness checks input mutation / uses the "
            "returned value AND the list order mattered downstream. Included "
            "to probe the tool's sensitivity to in-place mutation."
        ),
    ),
    BenchmarkCase(
        case_id="mutation_zero_negatives",
        function_name="clip_negatives",
        specification=(
            "Return a NEW list with each negative value replaced by 0, "
            "preserving order. The input must not be modified."
        ),
        category="mutation_of_input",
        reference_code=(
            "def clip_negatives(v):\n"
            "    return [0 if x < 0 else x for x in v]\n"
        ),
        candidate_code=(
            "def clip_negatives(v):\n"
            "    # BUG: mutates input in place instead of copying.\n"
            "    for i in range(len(v)):\n"
            "        if v[i] < 0:\n"
            "            v[i] = 0\n"
            "    return v\n"
        ),
        known_counterexamples=[[-1, 2, -3]],
        known_agreements=[[1, 2, 3]],
        notes="Returned value matches; the bug is the side effect on input.",
    ),

    # --- sorting_assumptions ------------------------------------------------
    BenchmarkCase(
        case_id="sorting_assume_presorted_median",
        function_name="median_floor",
        specification=(
            "Return the floor of the median of the list. The list is NOT "
            "guaranteed to be sorted. Empty -> 0."
        ),
        category="sorting_assumptions",
        reference_code=(
            "def median_floor(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    s = sorted(v)\n"
            "    n = len(s)\n"
            "    if n % 2:\n"
            "        return s[n // 2]\n"
            "    return (s[n // 2 - 1] + s[n // 2]) // 2\n"
        ),
        candidate_code=(
            "def median_floor(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    # BUG: assumes v is already sorted; doesn't sort.\n"
            "    n = len(v)\n"
            "    if n % 2:\n"
            "        return v[n // 2]\n"
            "    return (v[n // 2 - 1] + v[n // 2]) // 2\n"
        ),
        known_counterexamples=[[3, 1, 2]],
        known_agreements=[[1, 2, 3]],
        notes="Fails whenever the input isn't already sorted.",
    ),
    BenchmarkCase(
        case_id="sorting_assume_min_is_first",
        function_name="minimum",
        specification="Return the minimum element. Empty -> raise ValueError.",
        category="sorting_assumptions",
        reference_code=(
            "def minimum(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    return min(v)\n"
        ),
        candidate_code=(
            "def minimum(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    # BUG: assumes first element is the minimum.\n"
            "    return v[0]\n"
        ),
        known_counterexamples=[[3, 1, 2]],
        known_agreements=[[1, 2, 3]],
        notes="Assumes sorted order to take the min.",
    ),

    # --- boundary_conditions ------------------------------------------------
    BenchmarkCase(
        case_id="boundary_clamp",
        function_name="clamp_sum_100",
        specification=(
            "Return the sum of the list, clamped to a maximum of 100 (i.e. "
            "min(sum, 100)). The boundary value 100 itself is allowed."
        ),
        category="boundary_conditions",
        reference_code=(
            "def clamp_sum_100(v):\n"
            "    return min(sum(v), 100)\n"
        ),
        candidate_code=(
            "def clamp_sum_100(v):\n"
            "    s = sum(v)\n"
            "    # BUG: strict > clamps at 99 for the exact boundary? No —\n"
            "    # it returns 100 only when s>100; at s==100 returns 100 too.\n"
            "    # The real bug: clamps to 99 when s >= 100.\n"
            "    if s >= 100:\n"
            "        return 99\n"
            "    return s\n"
        ),
        known_counterexamples=[[100]],
        known_agreements=[[1, 2, 3], [50, 40]],
        notes="Boundary value 100 handled incorrectly (returns 99).",
    ),
    BenchmarkCase(
        case_id="boundary_single_element_range",
        function_name="value_range",
        specification=(
            "Return max(v) - min(v). For a single-element list the range is "
            "0. Empty -> raise ValueError."
        ),
        category="boundary_conditions",
        reference_code=(
            "def value_range(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    return max(v) - min(v)\n"
        ),
        candidate_code=(
            "def value_range(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    # BUG: mishandles the single-element boundary by using\n"
            "    # v[1] which doesn't exist; but guards len>1 wrongly.\n"
            "    if len(v) == 1:\n"
            "        return v[0]\n"
            "    return max(v) - min(v)\n"
        ),
        known_counterexamples=[[5]],
        known_agreements=[[1, 5], [3, 3, 3]],
        notes="Single-element range should be 0, candidate returns the value.",
    ),

    # --- floating_point_behavior -------------------------------------------
    BenchmarkCase(
        case_id="floating_point_mean",
        function_name="mean_times_three",
        specification=(
            "Return the exact arithmetic value of (sum(v) / len(v)) * 3 for a "
            "non-empty list. Empty -> 0. Values may be returned as floats."
        ),
        category="floating_point_behavior",
        reference_code=(
            "def mean_times_three(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    return sum(v) / len(v) * 3\n"
        ),
        candidate_code=(
            "def mean_times_three(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    # BUG: reorders as (sum*3)/len which is mathematically equal\n"
            "    # but for the intended semantics of averaging first can\n"
            "    # differ in float rounding for some inputs.\n"
            "    return sum(v) * 3 / len(v)\n"
        ),
        known_counterexamples=[[1, 0, 0, 0, 0]],
        known_agreements=[[2, 4]],
        notes=(
            "Float associativity: (s/n)*3 vs (s*3)/n differ in the last ULP "
            "for e.g. sum=1, n=5 (0.6000000000000001 vs 0.6). Deliberately "
            "subtle — small integer inputs rarely trigger it, which is itself "
            "a useful benchmark signal about float-sensitivity."
        ),
    ),
    BenchmarkCase(
        case_id="floating_point_half",
        function_name="half",
        specification=(
            "Return sum(v) divided by 2 as an exact value (float division). "
            "Empty -> 0."
        ),
        category="floating_point_behavior",
        reference_code=(
            "def half(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    return sum(v) / 2\n"
        ),
        candidate_code=(
            "def half(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    # BUG: integer floor division loses the .5 on odd sums.\n"
            "    return sum(v) // 2\n"
        ),
        known_counterexamples=[[3]],
        known_agreements=[[2], [4, 4]],
        notes="Odd sum: 1.5 vs 1 — float vs floor division.",
    ),

    # --- incorrect_loop_termination ----------------------------------------
    BenchmarkCase(
        case_id="loop_termination_sum",
        function_name="total",
        specification="Return the sum of all elements. Empty -> 0.",
        category="incorrect_loop_termination",
        reference_code=(
            "def total(v):\n"
            "    s = 0\n"
            "    for x in v:\n"
            "        s += x\n"
            "    return s\n"
        ),
        candidate_code=(
            "def total(v):\n"
            "    # BUG: stops at the first zero (treats 0 as a sentinel).\n"
            "    s = 0\n"
            "    for x in v:\n"
            "        if x == 0:\n"
            "            break\n"
            "        s += x\n"
            "    return s\n"
        ),
        known_counterexamples=[[1, 0, 5]],
        known_agreements=[[1, 2, 3]],
        notes="Early break on a zero element truncates the sum.",
    ),
    BenchmarkCase(
        case_id="loop_termination_find_last",
        function_name="last_negative_index",
        specification=(
            "Return the index of the LAST negative element, or -1 if none."
        ),
        category="incorrect_loop_termination",
        reference_code=(
            "def last_negative_index(v):\n"
            "    result = -1\n"
            "    for i, x in enumerate(v):\n"
            "        if x < 0:\n"
            "            result = i\n"
            "    return result\n"
        ),
        candidate_code=(
            "def last_negative_index(v):\n"
            "    # BUG: returns the FIRST negative index (early return).\n"
            "    for i, x in enumerate(v):\n"
            "        if x < 0:\n"
            "            return i\n"
            "    return -1\n"
        ),
        known_counterexamples=[[-1, 2, -3]],
        known_agreements=[[1, 2, -3], [1, 2, 3]],
        notes="Returns first rather than last match.",
    ),

    # --- incorrect_search_bounds -------------------------------------------
    BenchmarkCase(
        case_id="search_bounds_contains",
        function_name="contains_target_7",
        specification=(
            "Return True if the value 7 appears anywhere in the list, else "
            "False."
        ),
        category="incorrect_search_bounds",
        reference_code=(
            "def contains_target_7(v):\n"
            "    return 7 in v\n"
        ),
        candidate_code=(
            "def contains_target_7(v):\n"
            "    # BUG: skips the last element (range stops one short).\n"
            "    for i in range(len(v) - 1):\n"
            "        if v[i] == 7:\n"
            "            return True\n"
            "    return False\n"
        ),
        known_counterexamples=[[1, 2, 7]],
        known_agreements=[[7, 1, 2], [1, 2, 3]],
        notes="Search misses the final index.",
    ),
    BenchmarkCase(
        case_id="search_bounds_binary",
        function_name="binary_contains",
        specification=(
            "The input is a sorted (ascending) list. Return True if the value "
            "0 is present, else False."
        ),
        category="incorrect_search_bounds",
        reference_code=(
            "def binary_contains(v):\n"
            "    lo, hi = 0, len(v) - 1\n"
            "    while lo <= hi:\n"
            "        mid = (lo + hi) // 2\n"
            "        if v[mid] == 0:\n"
            "            return True\n"
            "        if v[mid] < 0:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return False\n"
        ),
        candidate_code=(
            "def binary_contains(v):\n"
            "    # BUG: upper bound excludes the last index (hi = len-1 with\n"
            "    # a strict lo < hi loop never examines v[-1]).\n"
            "    lo, hi = 0, len(v) - 1\n"
            "    while lo < hi:\n"
            "        mid = (lo + hi) // 2\n"
            "        if v[mid] == 0:\n"
            "            return True\n"
            "        if v[mid] < 0:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return False\n"
        ),
        known_counterexamples=[[-2, -1, 0], [0]],
        known_agreements=[[-1, 0, 1]],
        notes=(
            "Strict lo<hi loop never examines the final index, so a target "
            "at the top edge (including a single-element list) is missed."
        ),
    ),

    # --- state_leakage ------------------------------------------------------
    BenchmarkCase(
        case_id="state_leakage_default_arg",
        function_name="running_unique_count",
        specification=(
            "Return the number of distinct values in the list. Each call is "
            "independent — no state carries between calls."
        ),
        category="state_leakage",
        reference_code=(
            "def running_unique_count(v):\n"
            "    return len(set(v))\n"
        ),
        candidate_code=(
            "def running_unique_count(v, _seen={}):\n"
            "    # BUG: mutable default argument accumulates across calls,\n"
            "    # so the answer depends on prior calls (state leakage).\n"
            "    for x in v:\n"
            "        _seen[x] = True\n"
            "    return len(_seen)\n"
        ),
        known_counterexamples=[[1, 2]],
        known_agreements=[],
        notes=(
            "State leaks via a mutable default. On the FIRST call the count "
            "may match; the divergence shows across repeated calls. The "
            "runner executes each input in a fresh process, so this is hard "
            "to catch by design — a documented limitation, not a hidden one."
        ),
    ),
    BenchmarkCase(
        case_id="state_leakage_global",
        function_name="normalize_to_first",
        specification=(
            "Return a NEW list where each element has the first element "
            "subtracted from it. Empty -> empty. No state between calls."
        ),
        category="state_leakage",
        reference_code=(
            "def normalize_to_first(v):\n"
            "    if not v:\n"
            "        return []\n"
            "    base = v[0]\n"
            "    return [x - base for x in v]\n"
        ),
        candidate_code=(
            "_BASE = None\n"
            "def normalize_to_first(v):\n"
            "    global _BASE\n"
            "    if not v:\n"
            "        return []\n"
            "    # BUG: caches the base globally on first call and reuses it.\n"
            "    if _BASE is None:\n"
            "        _BASE = v[0]\n"
            "    return [x - _BASE for x in v]\n"
        ),
        known_counterexamples=[[10, 11, 12]],
        known_agreements=[],
        notes=(
            "First call with base 0 matches; divergence needs a prior call "
            "with a different first element. Per-input process isolation "
            "makes this a known blind spot."
        ),
    ),

    # --- order_preservation -------------------------------------------------
    BenchmarkCase(
        case_id="order_preservation_dedupe",
        function_name="dedupe_preserve_order",
        specification=(
            "Return a NEW list with duplicates removed, keeping the FIRST "
            "occurrence of each value and preserving input order."
        ),
        category="order_preservation",
        reference_code=(
            "def dedupe_preserve_order(v):\n"
            "    seen = set()\n"
            "    out = []\n"
            "    for x in v:\n"
            "        if x not in seen:\n"
            "            seen.add(x)\n"
            "            out.append(x)\n"
            "    return out\n"
        ),
        candidate_code=(
            "def dedupe_preserve_order(v):\n"
            "    # BUG: uses set() which loses the original order.\n"
            "    return sorted(set(v))\n"
        ),
        known_counterexamples=[[3, 1, 2]],
        known_agreements=[[1, 2, 3], [1, 1, 1]],
        notes="Sorting destroys the required insertion order.",
    ),
    BenchmarkCase(
        case_id="order_preservation_evens_first",
        function_name="evens_then_odds",
        specification=(
            "Return a NEW list with all even values first (in their original "
            "relative order) followed by all odd values (in their original "
            "relative order)."
        ),
        category="order_preservation",
        reference_code=(
            "def evens_then_odds(v):\n"
            "    evens = [x for x in v if x % 2 == 0]\n"
            "    odds = [x for x in v if x % 2 != 0]\n"
            "    return evens + odds\n"
        ),
        candidate_code=(
            "def evens_then_odds(v):\n"
            "    # BUG: sorts by parity but also by value, reordering within\n"
            "    # each group.\n"
            "    return sorted(v, key=lambda x: (x % 2, x))\n"
        ),
        known_counterexamples=[[4, 2, 1]],
        known_agreements=[[2, 4, 1, 3]],
        notes="Stable-partition order lost; within-group order changes.",
    ),

    # --- extra cases to broaden coverage past 30 ---------------------------
    BenchmarkCase(
        case_id="off_by_one_prefix_sum_len",
        function_name="running_max_last",
        specification=(
            "Return the maximum of the list. Empty -> raise ValueError."
        ),
        category="off_by_one",
        reference_code=(
            "def running_max_last(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    m = v[0]\n"
            "    for x in v[1:]:\n"
            "        if x > m:\n"
            "            m = x\n"
            "    return m\n"
        ),
        candidate_code=(
            "def running_max_last(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    m = v[0]\n"
            "    # BUG: stops before the last element (slice [1:-1]).\n"
            "    for x in v[1:-1]:\n"
            "        if x > m:\n"
            "            m = x\n"
            "    return m\n"
        ),
        known_counterexamples=[[1, 2, 9]],
        known_agreements=[[9, 1, 2], [5]],
        notes="Loop excludes the final element.",
    ),
    BenchmarkCase(
        case_id="duplicate_mode",
        function_name="most_common",
        specification=(
            "Return the value that appears most often. If there's a tie, "
            "return the smallest such value. Empty -> raise ValueError."
        ),
        category="duplicate_handling",
        reference_code=(
            "def most_common(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    from collections import Counter\n"
            "    counts = Counter(v)\n"
            "    best = max(counts.values())\n"
            "    return min(k for k, c in counts.items() if c == best)\n"
        ),
        candidate_code=(
            "def most_common(v):\n"
            "    if not v:\n"
            "        raise ValueError('empty')\n"
            "    # BUG: tie broken by first-seen, not smallest value.\n"
            "    from collections import Counter\n"
            "    counts = Counter(v)\n"
            "    best = max(counts.values())\n"
            "    for x in v:\n"
            "        if counts[x] == best:\n"
            "            return x\n"
        ),
        known_counterexamples=[[2, 1]],
        known_agreements=[[1, 1, 2], [3, 3, 3]],
        notes="Tie-break rule differs (first-seen vs smallest).",
    ),
    BenchmarkCase(
        case_id="negative_sum_of_positives",
        function_name="sum_positive",
        specification="Return the sum of the strictly positive elements. Empty -> 0.",
        category="negative_values",
        reference_code=(
            "def sum_positive(v):\n"
            "    return sum(x for x in v if x > 0)\n"
        ),
        candidate_code=(
            "def sum_positive(v):\n"
            "    # BUG: includes negatives via abs.\n"
            "    return sum(abs(x) for x in v if x != 0)\n"
        ),
        known_counterexamples=[[-5, 3]],
        known_agreements=[[1, 2, 3]],
        notes="Absolute value pulls in negative magnitudes.",
    ),
    BenchmarkCase(
        case_id="boundary_empty_vs_single_product",
        function_name="product",
        specification=(
            "Return the product of all elements. The product of an empty "
            "list is 1."
        ),
        category="boundary_conditions",
        reference_code=(
            "def product(v):\n"
            "    p = 1\n"
            "    for x in v:\n"
            "        p *= x\n"
            "    return p\n"
        ),
        candidate_code=(
            "def product(v):\n"
            "    # BUG: initializes to 0, so every product is 0.\n"
            "    p = 0\n"
            "    for x in v:\n"
            "        p *= x\n"
            "    return p\n"
        ),
        known_counterexamples=[[2, 3]],
        known_agreements=[[0], [5, 0]],
        notes="Wrong multiplicative identity (0 instead of 1).",
    ),
    BenchmarkCase(
        case_id="incorrect_exception_negative_index",
        function_name="safe_get_second",
        specification=(
            "Return the second element (index 1). If the list has fewer than "
            "two elements, raise IndexError."
        ),
        category="incorrect_exception_behavior",
        reference_code=(
            "def safe_get_second(v):\n"
            "    if len(v) < 2:\n"
            "        raise IndexError('need at least two elements')\n"
            "    return v[1]\n"
        ),
        candidate_code=(
            "def safe_get_second(v):\n"
            "    # BUG: returns a wrong value (last) instead of raising when\n"
            "    # there's a single element.\n"
            "    if len(v) < 2:\n"
            "        return v[-1]\n"
            "    return v[1]\n"
        ),
        known_counterexamples=[[7]],
        known_agreements=[[1, 2, 3]],
        notes="Should raise on len<2; candidate returns a value.",
    ),
    BenchmarkCase(
        case_id="loop_termination_while_index",
        function_name="first_run_length",
        specification=(
            "Return the length of the initial run of equal elements at the "
            "start of the list. Empty -> 0."
        ),
        category="incorrect_loop_termination",
        reference_code=(
            "def first_run_length(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    i = 1\n"
            "    while i < len(v) and v[i] == v[0]:\n"
            "        i += 1\n"
            "    return i\n"
        ),
        candidate_code=(
            "def first_run_length(v):\n"
            "    if not v:\n"
            "        return 0\n"
            "    # BUG: uses <= len(v)-1 with a trailing increment that\n"
            "    # over-runs when the entire list is one run.\n"
            "    i = 1\n"
            "    while i < len(v) and v[i] == v[0]:\n"
            "        i += 1\n"
            "    if i == len(v):\n"
            "        i += 1  # off-by-one only when the whole list is a run\n"
            "    return i\n"
        ),
        known_counterexamples=[[5, 5, 5]],
        known_agreements=[[1, 2, 3], [5, 5, 1]],
        notes="Overcounts by one when the entire list is a single run.",
    ),
    BenchmarkCase(
        case_id="order_preservation_stable_pairs",
        function_name="negate_all",
        specification=(
            "Return a NEW list with every element negated, preserving order."
        ),
        category="order_preservation",
        reference_code=(
            "def negate_all(v):\n"
            "    return [-x for x in v]\n"
        ),
        candidate_code=(
            "def negate_all(v):\n"
            "    # BUG: negates but reverses the order too.\n"
            "    return [-x for x in reversed(v)]\n"
        ),
        known_counterexamples=[[1, 2, 3]],
        known_agreements=[[5], [2, 2, 2]],
        notes="Reversal breaks order preservation.",
    ),
    BenchmarkCase(
        case_id="sorting_kth_smallest",
        function_name="third_smallest",
        specification=(
            "Return the third smallest value (by sorted order, duplicates "
            "counted). If fewer than three elements, raise ValueError."
        ),
        category="sorting_assumptions",
        reference_code=(
            "def third_smallest(v):\n"
            "    if len(v) < 3:\n"
            "        raise ValueError('need three')\n"
            "    return sorted(v)[2]\n"
        ),
        candidate_code=(
            "def third_smallest(v):\n"
            "    if len(v) < 3:\n"
            "        raise ValueError('need three')\n"
            "    # BUG: assumes input already sorted; indexes v directly.\n"
            "    return v[2]\n"
        ),
        known_counterexamples=[[9, 8, 7, 6]],
        known_agreements=[[1, 2, 3, 4]],
        notes="Positional index without sorting.",
    ),
    BenchmarkCase(
        case_id="duplicate_has_adjacent",
        function_name="has_any_duplicate",
        specification=(
            "Return True if any value appears more than once anywhere in the "
            "list, else False."
        ),
        category="duplicate_handling",
        reference_code=(
            "def has_any_duplicate(v):\n"
            "    return len(set(v)) != len(v)\n"
        ),
        candidate_code=(
            "def has_any_duplicate(v):\n"
            "    # BUG: only checks adjacent equality.\n"
            "    return any(v[i] == v[i + 1] for i in range(len(v) - 1))\n"
        ),
        known_counterexamples=[[1, 2, 1]],
        known_agreements=[[1, 1, 2], [1, 2, 3]],
        notes="Non-adjacent duplicates missed.",
    ),
    BenchmarkCase(
        case_id="empty_max_default",
        function_name="max_or_neg_one",
        specification="Return the maximum element, or -1 if the list is empty.",
        category="empty_input",
        reference_code=(
            "def max_or_neg_one(v):\n"
            "    return max(v) if v else -1\n"
        ),
        candidate_code=(
            "def max_or_neg_one(v):\n"
            "    # BUG: max() on empty raises ValueError instead of -1.\n"
            "    return max(v)\n"
        ),
        known_counterexamples=[[]],
        known_agreements=[[3, 1], [5]],
        notes="Empty case unhandled.",
    ),
]


def all_cases() -> list[BenchmarkCase]:
    """Return the full dataset. Order is stable for reproducibility."""
    return list(CASES)
