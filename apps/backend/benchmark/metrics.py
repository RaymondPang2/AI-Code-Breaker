"""
Aggregation: fold a list of CaseResult into a BenchmarkReport.

Pure and deterministic — no I/O, no globals — so it's easy to unit-test and
gives the same report for the same inputs. Rates are computed honestly over
ALL cases (misses and errors included); nothing is dropped or cherry-picked.
Averages skip only values that are genuinely absent (None), and that choice
is documented per metric.
"""

from __future__ import annotations

from statistics import mean

from benchmark.results import (
    STRATEGY_AI,
    STRATEGY_DETERMINISTIC,
    STRATEGY_HYPOTHESIS,
    BenchmarkReport,
    CaseResult,
)

ALL_STRATEGIES = (STRATEGY_DETERMINISTIC, STRATEGY_AI, STRATEGY_HYPOTHESIS)


def _safe_mean(values: list[float]) -> float | None:
    """Mean of the present values, or None if there are none. Used where a
    metric legitimately doesn't exist for some cases (e.g. counterexample
    size for undetected cases) — those are excluded rather than counted as
    zero, which would understate the average misleadingly."""
    return mean(values) if values else None


def aggregate(seed: int, cases: list[CaseResult]) -> BenchmarkReport:
    """Aggregate per-case results into the run report."""
    total = len(cases)
    report = BenchmarkReport(seed=seed, total_cases=total, cases=list(cases))

    if total == 0:
        return report

    detected = [c for c in cases if c.detected]
    report.detected_count = len(detected)
    report.detection_rate = len(detected) / total

    # Detection rate by strategy: fraction of ALL cases each strategy
    # independently caught. A case can count for multiple strategies.
    by_strategy: dict[str, float] = {}
    for strat in ALL_STRATEGIES:
        hits = sum(1 for c in cases if c.strategy_detected.get(strat, False))
        by_strategy[strat] = hits / total
    report.detection_rate_by_strategy = by_strategy

    # Detection rate by category: over the cases in that category.
    categories = sorted({c.category for c in cases})
    by_category: dict[str, float] = {}
    for cat in categories:
        cat_cases = [c for c in cases if c.category == cat]
        cat_detected = sum(1 for c in cat_cases if c.detected)
        by_category[cat] = cat_detected / len(cat_cases) if cat_cases else 0.0
    report.detection_rate_by_category = by_category

    # Timing.
    ttf = [
        c.time_to_first_counterexample_s
        for c in cases
        if c.time_to_first_counterexample_s is not None
    ]
    report.mean_time_to_first_counterexample_s = _safe_mean(ttf)
    report.total_time_s = sum(c.total_time_s for c in cases)
    report.timed_out_count = sum(1 for c in cases if c.timed_out)

    # Execution volume.
    report.total_execution_count = sum(c.execution_count for c in cases)
    report.mean_execution_count = report.total_execution_count / total

    # Counterexample size + minimization (only over cases that have them).
    sizes = [c.counterexample_size for c in cases if c.counterexample_size is not None]
    report.mean_counterexample_size = _safe_mean([float(s) for s in sizes])
    min_sizes = [c.minimized_size for c in cases if c.minimized_size is not None]
    report.mean_minimized_size = _safe_mean([float(s) for s in min_sizes])
    reductions = [
        c.minimization_reduction
        for c in cases
        if c.minimization_reduction is not None
    ]
    report.mean_minimization_reduction = _safe_mean([float(r) for r in reductions])

    # AI.
    report.total_ai_tests_proposed = sum(c.ai_tests_proposed for c in cases)
    report.total_ai_tests_invalid = sum(c.ai_tests_invalid for c in cases)
    report.invalid_ai_test_rate = (
        report.total_ai_tests_invalid / report.total_ai_tests_proposed
        if report.total_ai_tests_proposed > 0
        else 0.0
    )
    report.total_claude_requests = sum(c.claude_request_count for c in cases)
    latencies = [
        c.claude_latency_ms for c in cases if c.claude_latency_ms is not None
    ]
    report.mean_claude_latency_ms = _safe_mean(latencies)
    report.total_estimated_input_tokens = sum(
        c.estimated_input_tokens for c in cases
    )
    report.total_estimated_output_tokens = sum(
        c.estimated_output_tokens for c in cases
    )

    # Correctness.
    report.false_bug_report_count = sum(1 for c in cases if c.false_bug_report)
    report.miss_count = sum(1 for c in cases if c.is_miss)

    return report
