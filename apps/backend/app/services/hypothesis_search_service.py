"""
Property-based differential search via Hypothesis.

Hypothesis's job here is strictly limited to one thing: generating (and
shrinking) `list[int]` values. It never sees candidate_code or
reference_code, never imports them, and never executes them in this
process. Every generated input is executed by calling the exact same
execution backend used by app.services.comparison_service — by default,
an ephemeral Docker container (app.services.docker_runner_service); see
app.services.execution_backend and app.core.config.execution_backend.

See the ARCHITECTURE NOTE at the bottom of this file for how Hypothesis's
generation/shrinking loop interacts with the fact that every single
example now costs one or two out-of-process runner launches instead of a
cheap in-memory function call.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from hypothesis import HealthCheck, given
from hypothesis import seed as hypothesis_seed
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from hypothesis.errors import Flaky

from app.schemas.hypothesis_search import HypothesisSearchRequest, HypothesisSearchResponse
from app.schemas.minimization import MinimizationRequest
from app.schemas.submission import FunctionExecutionResult
from app.services.comparison_rules import compare_execution_results, to_execution_result
from app.services.execution_backend import get_execute_function
from app.services.minimizer_service import minimize_counterexample


class _TimeBudgetExceeded(Exception):
    """
    Raised internally once the overall search time budget is exceeded.
    Caught outside the Hypothesis-driven call, never allowed to look like
    a "bug in the submitted code" to a caller.

    Deliberately NOT a subclass of AssertionError, so it can never be
    confused with a genuine candidate/reference disagreement.
    """


@dataclass
class _SearchState:
    """
    Mutable side-channel for what happened during the search, populated
    from inside the Hypothesis-driven property function.

    This exists because the only thing we get back from calling a
    Hypothesis `@given`-wrapped function directly is "it returned
    normally" or "it raised" — Hypothesis does not hand back a structured
    result object. Recording state as we go (and reading it back after the
    call) is the standard pattern for using Hypothesis outside of pytest.

    Crucially: counterexample_* fields are ONLY ever written when a real,
    confirmed disagreement was found (an actual AssertionError case) —
    never on a timeout or a harness internal_error — so whatever ends up
    in this object at the end is always a genuine finding, even if
    Hypothesis's own bookkeeping around it gets disrupted by the time
    budget (see _TimeBudgetExceeded / Flaky handling below).
    """

    examples_attempted: int = 0
    internal_errors_encountered: int = 0
    counterexample_input: list[int] | None = None
    counterexample_candidate: FunctionExecutionResult | None = None
    counterexample_reference: FunctionExecutionResult | None = None


def run_differential_search(request: HypothesisSearchRequest) -> HypothesisSearchResponse:
    """
    Search for a list[int] input where candidate_code and reference_code
    disagree, using Hypothesis to generate and shrink inputs.
    """
    strategy = st.lists(
        st.integers(min_value=request.min_int_value, max_value=request.max_int_value),
        min_size=request.min_list_size,
        max_size=request.max_list_size,
    )

    state = _SearchState()
    start_time = time.perf_counter()

    def property_fn(candidate_input: list[int]) -> None:
        # Checked FIRST, before any runner launch. This is what makes the
        # overall timeout cheap once tripped: every call after the budget
        # is exceeded raises immediately, without touching Docker at all.
        # It's also what keeps behavior consistent for Hypothesis's own
        # flakiness detection — see the ARCHITECTURE NOTE below.
        if time.perf_counter() - start_time > request.timeout_seconds:
            raise _TimeBudgetExceeded()

        state.examples_attempted += 1
        execute = get_execute_function()

        # Candidate and reference run concurrently rather than sequentially
        # — the same pattern comparison_service uses for the same reason:
        # subprocess/docker launches release the GIL while waiting, so
        # this is real wall-clock savings, not just cosmetic. A fresh pool
        # per example is deliberate: pool creation is microseconds, a
        # runner launch is (at minimum) tens of milliseconds, so sharing a
        # pool across the potentially hundreds of calls Hypothesis makes
        # isn't worth the added lifecycle complexity.
        with ThreadPoolExecutor(max_workers=2) as pool:
            candidate_future = pool.submit(
                execute,
                source_code=request.candidate_code,
                function_name=request.function_name,
                input_=candidate_input,
            )
            reference_future = pool.submit(
                execute,
                source_code=request.reference_code,
                function_name=request.function_name,
                input_=candidate_input,
            )
            candidate_runner_result = candidate_future.result()
            reference_runner_result = reference_future.result()

        candidate_result = to_execution_result(candidate_runner_result)
        reference_result = to_execution_result(reference_runner_result)

        match, is_internal_error = compare_execution_results(candidate_result, reference_result)

        if is_internal_error:
            # Our own harness failed on this input (e.g. a malformed
            # runner response). Not evidence of a real disagreement —
            # treated as inconclusive and simply not asserted on, the
            # same way app.services.comparison_service flags but doesn't
            # "count" internal_error comparisons as confirmed bugs.
            state.internal_errors_encountered += 1
            return

        if not match:
            state.counterexample_input = list(candidate_input)
            state.counterexample_candidate = candidate_result
            state.counterexample_reference = reference_result
            raise AssertionError(
                f"candidate and reference disagree on input {candidate_input!r}"
            )

    configured_fn = hypothesis_settings(
        max_examples=request.max_examples,
        # No per-example deadline: Hypothesis's default (a few hundred ms)
        # assumes an in-memory function call. A single example here is at
        # least one, usually two, out-of-process runner launches. See the
        # ARCHITECTURE NOTE below for how the *overall* timeout is
        # enforced instead.
        deadline=None,
        # Don't persist/replay examples across unrelated searches — each
        # request should be self-contained and not influenced by some
        # other submission's prior run.
        database=None,
        suppress_health_check=[
            HealthCheck.too_slow,        # every example is "slow" by Hypothesis's normal standards
            HealthCheck.data_too_large,  # generated lists can be larger than Hypothesis's default comfort zone
        ],
    )(given(strategy)(property_fn))

    if request.seed is not None:
        configured_fn = hypothesis_seed(request.seed)(configured_fn)

    timed_out = False
    try:
        configured_fn()
    except _TimeBudgetExceeded:
        timed_out = True
    except Flaky:
        # See the ARCHITECTURE NOTE: this can happen if the time budget is
        # exceeded in the narrow window between finding a counterexample
        # and Hypothesis re-confirming it while shrinking — the same
        # input then appears to raise a different exception on replay,
        # which Hypothesis's flakiness detector flags. state.* was
        # already populated from the real confirmed failure found
        # earlier, so nothing incorrect is reported; this is still a
        # time-budget outcome from the caller's point of view.
        timed_out = True
    except AssertionError:
        # A genuine disagreement was found and (fully, if time allowed)
        # shrunk. state.counterexample_* holds the details from the last
        # invocation before this was raised, which — because Hypothesis
        # always replays the minimal example immediately before its final
        # re-raise — is the shrunk minimal case, not just the first one
        # stumbled into.
        pass

    elapsed_seconds = time.perf_counter() - start_time

    minimization = None
    if request.apply_deterministic_minimization and state.counterexample_input is not None:
        # A counterexample was confirmed; run the deterministic minimizer
        # as an additional, explainable pass on top of Hypothesis's own
        # shrinking. This is opt-in because it costs further runner
        # executions and Hypothesis has usually already shrunk well.
        minimization = minimize_counterexample(
            MinimizationRequest(
                function_name=request.function_name,
                candidate_code=request.candidate_code,
                reference_code=request.reference_code,
                failing_input=state.counterexample_input,
            )
        )

    return HypothesisSearchResponse(
        function_name=request.function_name,
        counterexample_found=state.counterexample_input is not None,
        minimal_failing_input=state.counterexample_input,
        candidate_result=state.counterexample_candidate,
        reference_result=state.counterexample_reference,
        examples_attempted=state.examples_attempted,
        elapsed_seconds=elapsed_seconds,
        timed_out=timed_out,
        seed_used=request.seed,
        minimization=minimization,
    )


# ============================== ARCHITECTURE NOTE ==============================
# How Hypothesis's generation/shrinking loop interacts with out-of-process
# execution — see also the top-level explanation given alongside this
# implementation.
#
# 1. GENERATION: `st.lists(st.integers(...), ...)` runs entirely inside
#    this process, entirely in memory. It never touches candidate_code or
#    reference_code. This is the "Hypothesis generates only inputs"
#    requirement — structurally true, not just by convention, since
#    property_fn's body is the only place source code strings are even
#    referenced, and they're only ever handed to `execute()`, which
#    launches a separate runner (subprocess or Docker container).
#
# 2. EXECUTION: every single generated input costs a real runner launch —
#    for the Docker backend, that's real container startup overhead (tens
#    to hundreds of milliseconds), not a cheap function call. Hypothesis
#    was designed assuming properties are fast (its default per-example
#    deadline is 200ms and it "expects" to run thousands of examples).
#    Two settings compensate for that mismatch: `deadline=None` (don't
#    flag individual slow examples as a health-check failure) and a
#    conservative `max_examples` (default 50, hard-capped at 200) so a
#    single search has a predictable worst-case number of runner launches
#    (2x max_examples, plus whatever shrinking adds).
#
# 3. SHRINKING: this is the crux of "preserve shrinking behavior as much
#    as practical". Hypothesis shrinks by calling property_fn again with
#    smaller/simpler candidate inputs and checking whether it still
#    raises. There's no special integration needed for this to work with
#    an out-of-process runner — property_fn calling execute() (a runner
#    launch) instead of calling submitted code directly is invisible to
#    Hypothesis's shrinker; it only cares whether the call raised. So
#    shrinking works exactly as it would for any other Hypothesis
#    property. The cost is entirely about *time*: shrinking a failure
#    typically takes dozens of extra calls, and here every one of those
#    calls is one to two more runner launches. That's why the overall
#    time budget (below) matters even more than max_examples once a
#    counterexample has actually been found.
#
# 4. THE OVERALL TIME BUDGET vs. HYPOTHESIS'S OWN MODEL: Hypothesis
#    intentionally has no first-class "stop the whole run after N wall-
#    clock seconds" setting — only a per-example `deadline`, which we've
#    disabled. The standard workaround (used here) is to check elapsed
#    time inside the property function and raise a distinguishing
#    exception once the budget is blown. The check happens as the FIRST
#    line of property_fn, before any runner launch, which matters for two
#    reasons: (a) once the budget is exceeded, every subsequent call
#    (regardless of which input Hypothesis is trying) is cheap — no more
#    runner launches happen, so "wasted" shrink attempts after the budget
#    trips cost microseconds, not more container starts; (b) it keeps
#    behavior *consistent*: once tripped, literally every input raises
#    the same _TimeBudgetExceeded, forever (time only moves forward) — so
#    Hypothesis's replay-to-confirm-not-flaky logic sees the same outcome
#    every time it re-checks, for that phase of execution. The one edge
#    case this doesn't fully avoid: if the budget trips in the narrow
#    window between "a real disagreement was found" and "Hypothesis
#    replays it once more to confirm it's not flaky," that replay now
#    raises _TimeBudgetExceeded instead of the original AssertionError —
#    a different exception for the "same" input across two calls, which
#    is exactly what Hypothesis's flakiness detector exists to catch, so
#    it raises `Flaky`. This is handled explicitly above: our own
#    `_SearchState` was already updated by the earlier, genuine failure,
#    so the response is still correct — it just also reports timed_out.
# =================================================================================
