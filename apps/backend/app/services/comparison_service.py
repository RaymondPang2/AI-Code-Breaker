"""
Comparison engine.

Runs candidate and reference implementations on every selected test input
(manually supplied, generated, or both — see
app.services.test_selection_service), reports where they agree or
disagree. This does not call Claude for anything; it only selects,
executes, and compares.

Execution backend: by default this runs each implementation inside an
ephemeral Docker container (app.services.docker_runner_service) — see
runner/README.md for the security model. app.core.config.execution_backend
can switch this to the bare-subprocess backend
(app.services.runner_service) for environments without Docker installed;
that backend is NOT a security sandbox and should not be used for a
deployed instance of this project.

Comparison rules live in app.services.comparison_rules, shared with
app.services.hypothesis_search_service — see that module for why.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.schemas.submission import (
    FunctionExecutionResult,
    SubmissionAnalysisResponse,
    SubmissionRequest,
    TestComparisonResult,
)
from app.schemas.test_case import SelectedTestCase
from app.services.comparison_rules import compare_execution_results, to_execution_result
from app.services.execution_backend import get_execute_function
from app.services.test_selection_service import select_test_cases

# Each test input requires two runner launches (candidate + reference).
# MAX_TOTAL_TESTS (app.schemas.submission) already bounds how many test
# inputs a single submission executes, but this bounds how many of those
# runners are ever running *at once* — the point being that "30 test
# inputs" should never mean "60 containers/processes launched
# simultaneously".
MAX_CONCURRENT_RUNNERS = 4


def _run_one_side(
    source_code: str, function_name: str, test_input: list[int]
) -> FunctionExecutionResult:
    execute = get_execute_function()
    runner_result = execute(
        source_code=source_code, function_name=function_name, input_=test_input
    )
    return to_execution_result(runner_result)


def analyze_submission(
    submission: SubmissionRequest,
    ai_test_cases: list[SelectedTestCase] | None = None,
) -> SubmissionAnalysisResponse:
    """
    Select, run, and compare candidate vs. reference for `submission`.

    Ordering: `comparisons[i]` always corresponds to
    `selected_cases[i]`, which itself preserves manual-then-generated-then-AI
    order (see select_test_cases). Jobs are dispatched to a bounded thread
    pool (subprocess.run releases the GIL while the child runs, so this
    achieves real concurrency across the underlying processes) but results
    are collected back out by index, never by completion order — so
    ordering is preserved no matter which subprocess happens to finish
    first.

    `ai_test_cases`, if supplied, are AI-proposed inputs (already validated
    and deduped by app.services.ai_test_generation_service, converted to
    SelectedTestCase with source="ai"). They're purely additive: whether
    each actually exposes a bug is decided here, by real execution and the
    same comparison rules as every other input — never by the AI.
    """
    selected_cases = select_test_cases(submission, ai_test_cases=ai_test_cases)

    if not selected_cases:
        return SubmissionAnalysisResponse(
            function_name=submission.function_name,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            comparisons=[],
            first_failing_input=None,
        )

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RUNNERS) as pool:
        candidate_futures = [
            pool.submit(
                _run_one_side, submission.candidate_code, submission.function_name, case.input
            )
            for case in selected_cases
        ]
        reference_futures = [
            pool.submit(
                _run_one_side, submission.reference_code, submission.function_name, case.input
            )
            for case in selected_cases
        ]
        candidate_results = [future.result() for future in candidate_futures]
        reference_results = [future.result() for future in reference_futures]

    comparisons: list[TestComparisonResult] = []
    first_failing_input: list[int] | None = None
    passed_tests = 0
    failed_tests = 0
    inconclusive_tests = 0

    for case, candidate, reference in zip(selected_cases, candidate_results, reference_results):
        match, is_internal_error = compare_execution_results(candidate, reference)
        comparisons.append(
            TestComparisonResult(
                input=case.input,
                source=case.source,
                category=case.category,
                reason=case.reason,
                candidate=candidate,
                reference=reference,
                match=match,
                internal_error=is_internal_error,
            )
        )
        # Three mutually exclusive buckets: passed (behavior matches), failed
        # (confirmed behavioral mismatch), inconclusive (a harness/runner
        # error prevented a real comparison). Counting failed as
        # "total - passed" was the accounting bug: it folded inconclusive
        # results into failed, producing the impossible "N failed but no
        # behavioral differences" state.
        if is_internal_error:
            inconclusive_tests += 1
        elif match:
            passed_tests += 1
        else:
            failed_tests += 1
            if first_failing_input is None:
                first_failing_input = case.input

    total_tests = len(selected_cases)

    return SubmissionAnalysisResponse(
        function_name=submission.function_name,
        total_tests=total_tests,
        passed_tests=passed_tests,
        failed_tests=failed_tests,
        inconclusive_tests=inconclusive_tests,
        comparisons=comparisons,
        first_failing_input=first_failing_input,
    )
