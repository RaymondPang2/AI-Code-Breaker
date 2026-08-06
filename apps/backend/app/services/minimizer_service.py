"""
Deterministic counterexample minimizer for list[int].

Once a failing input is confirmed (e.g. by Hypothesis, which already
shrinks), this provides a *deterministic, explainable* fallback pass that
simplifies the input further using a fixed sequence of strategies. Unlike
Hypothesis's shrinker — which is powerful but internal and stochastic in
which paths it explores — this minimizer applies a small, auditable set of
transformations in a fixed order, which is easier to reason about and to
explain in a bug report.

Every proposed simplification is VERIFIED by rerunning both implementations
in the isolated runner (Docker by default; see
app.services.execution_backend) and confirming the disagreement still
holds under the same normalized comparison rules used everywhere else
(app.services.comparison_rules). A simplification is only kept if the
failure survives it — the minimizer never returns an input it hasn't
re-confirmed still fails.

Determinism: given the same inputs and a runner that behaves consistently,
this produces the same minimized result every time. Strategies are applied
in a fixed order, candidates within each strategy are generated in a fixed
order, and the first candidate that preserves the failure is taken (greedy,
left-to-right) — no randomness anywhere.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterator

from app.schemas.minimization import MinimizationRequest, MinimizationResult
from app.services.comparison_rules import compare_execution_results, to_execution_result
from app.services.execution_backend import get_execute_function


def _numeric_complexity(values: list[int]) -> int:
    """Sum of absolute values — the "numeric complexity" measure the
    result reports a reduction in. Chosen because every strategy that
    simplifies values (toward 0/1/-1, or reducing magnitude) provably
    lowers this, so 'complexity went down' is a meaningful signal that
    the values themselves got simpler, independent of length."""
    return sum(abs(v) for v in values)


@dataclass
class _Budget:
    """Tracks the two independent stopping conditions (execution count and
    wall-clock) so any strategy can check them uniformly."""

    max_executions: int
    timeout_seconds: float
    start_time: float
    executions_used: int = 0
    stopped_reason: str | None = None  # set to 'execution_budget' or 'timeout' when hit

    def exhausted(self) -> bool:
        if self.executions_used >= self.max_executions:
            self.stopped_reason = "execution_budget"
            return True
        if time.perf_counter() - self.start_time > self.timeout_seconds:
            self.stopped_reason = "timeout"
            return True
        return False


def _fails(
    candidate_code: str,
    reference_code: str,
    function_name: str,
    candidate_input: list[int],
    budget: _Budget,
) -> bool:
    """
    Run both implementations on `candidate_input` in the isolated runner
    and return True iff they still disagree (a confirmed, non-internal
    failure). Increments the execution counter.

    An internal_error here (our own harness failing) is treated as "does
    NOT count as still failing" — we must never keep a simplification on
    the basis of a harness glitch, only on a genuine, reproduced
    disagreement.
    """
    budget.executions_used += 1
    execute = get_execute_function()

    with ThreadPoolExecutor(max_workers=2) as pool:
        candidate_future = pool.submit(
            execute, source_code=candidate_code, function_name=function_name, input_=candidate_input
        )
        reference_future = pool.submit(
            execute, source_code=reference_code, function_name=function_name, input_=candidate_input
        )
        candidate_result = to_execution_result(candidate_future.result())
        reference_result = to_execution_result(reference_future.result())

    match, is_internal_error = compare_execution_results(candidate_result, reference_result)
    if is_internal_error:
        return False
    return not match


# --- Candidate generators, one per strategy --------------------------------
#
# Each takes the current failing list and yields candidate simplifications
# (each strictly "simpler" by that strategy's measure). They only PROPOSE;
# the driver verifies each candidate through the runner and decides whether
# to keep it. Generators never call the runner themselves.


def _remove_chunks(values: list[int]) -> Iterator[list[int]]:
    """Strategy 1: remove contiguous chunks, largest first (delta-debugging
    style). Halving chunk sizes finds big reductions fast before falling
    back to fine-grained removal."""
    n = len(values)
    chunk = n // 2
    while chunk >= 1:
        start = 0
        while start < len(values):
            candidate = values[:start] + values[start + chunk :]
            if candidate != values:
                yield candidate
            start += chunk
        chunk //= 2


def _remove_single_elements(values: list[int]) -> Iterator[list[int]]:
    """Strategy 2: remove one element at a time, left to right."""
    for i in range(len(values)):
        yield values[:i] + values[i + 1 :]


def _replace_with_simpler_values(values: list[int]) -> Iterator[list[int]]:
    """Strategy 3: replace each element with a simpler canonical value
    (0, then 1, then -1), left to right.

    A target is only proposed if it's strictly simpler than the current
    value, defined as: strictly smaller absolute value, OR equal absolute
    value but the current value isn't already canonical (so 5 -> 1 and
    2 -> -1 are allowed; 1 -> -1 and 0 -> anything are not). This
    guarantees every proposal strictly lowers numeric complexity or moves
    toward a canonical form, and never loops."""
    simpler_targets = (0, 1, -1)
    for i, current in enumerate(values):
        if current in simpler_targets:
            # Already canonical (0/1/-1); nothing simpler to offer that
            # wouldn't just be a lateral move between canonical values.
            continue
        for target in simpler_targets:
            if abs(target) > abs(current):
                continue
            candidate = list(values)
            candidate[i] = target
            yield candidate


def _reduce_magnitude(values: list[int]) -> Iterator[list[int]]:
    """Strategy 4: halve each element toward zero, left to right. Repeated
    application (the outer driver loops until fixed point) walks a value
    like 100 -> 50 -> 25 -> ... -> 0."""
    for i, current in enumerate(values):
        if current == 0:
            continue
        halved = int(current / 2)  # truncates toward zero for negatives too
        if halved != current:
            candidate = list(values)
            candidate[i] = halved
            yield candidate


def _remove_duplicate_occurrences(values: list[int]) -> Iterator[list[int]]:
    """Strategy 5: for any value appearing more than once, try removing
    each of its extra occurrences (keeping at least one). Ordered by value
    then by occurrence index for determinism."""
    seen_counts: dict[int, int] = {}
    for v in values:
        seen_counts[v] = seen_counts.get(v, 0) + 1
    duplicated = sorted(v for v, c in seen_counts.items() if c > 1)
    for value in duplicated:
        positions = [i for i, v in enumerate(values) if v == value]
        # Remove each occurrence except the first, one at a time.
        for pos in positions[1:]:
            yield values[:pos] + values[pos + 1 :]


# Strategy order is fixed and meaningful: bulk length reduction first
# (chunks, then singles), then value simplification (canonical values,
# then magnitude), then duplicate pruning. The whole sequence is repeated
# until a full pass produces no improvement (fixed point).
_STRATEGIES: list[tuple[str, Callable[[list[int]], Iterator[list[int]]]]] = [
    ("remove_chunks", _remove_chunks),
    ("remove_single_elements", _remove_single_elements),
    ("replace_with_simpler_values", _replace_with_simpler_values),
    ("reduce_magnitude", _reduce_magnitude),
    ("remove_duplicate_occurrences", _remove_duplicate_occurrences),
]


def minimize_counterexample(request: MinimizationRequest) -> MinimizationResult:
    """
    Deterministically minimize a confirmed failing list[int] input.

    Greedy fixed-point loop: repeatedly run every strategy in order,
    keeping the first candidate from each that still fails, until a full
    pass over all strategies yields no accepted simplification (fixed
    point) or a budget is exhausted.
    """
    original = list(request.failing_input)
    current = list(original)

    budget = _Budget(
        max_executions=request.max_executions,
        timeout_seconds=request.timeout_seconds,
        start_time=time.perf_counter(),
    )

    def still_fails(candidate: list[int]) -> bool:
        return _fails(
            request.candidate_code,
            request.reference_code,
            request.function_name,
            candidate,
            budget,
        )

    improved_in_pass = True
    hit_budget = False

    while improved_in_pass and not hit_budget:
        improved_in_pass = False

        for _strategy_name, generate_candidates in _STRATEGIES:
            # Re-generate candidates against the *current* (possibly
            # already-reduced) input each time we (re)enter a strategy, so
            # accepted simplifications compound.
            restart_strategy = True
            while restart_strategy:
                restart_strategy = False
                for candidate in generate_candidates(current):
                    if budget.exhausted():
                        hit_budget = True
                        break
                    if still_fails(candidate):
                        current = candidate
                        improved_in_pass = True
                        # A change can unlock further reductions within the
                        # same strategy (e.g. after removing one chunk,
                        # other chunks shift) — restart this strategy from
                        # scratch against the new, smaller input.
                        restart_strategy = True
                        break
                if hit_budget:
                    break
            if hit_budget:
                break

    if hit_budget:
        stopped_reason = budget.stopped_reason or "execution_budget"
    else:
        stopped_reason = "fixed_point"

    return MinimizationResult(
        original_failing_input=original,
        minimized_failing_input=current,
        verification_executions=budget.executions_used,
        length_reduction=len(original) - len(current),
        numeric_complexity_reduction=_numeric_complexity(original) - _numeric_complexity(current),
        stopped_due_to_budget=hit_budget,
        stopped_reason=stopped_reason,
    )
