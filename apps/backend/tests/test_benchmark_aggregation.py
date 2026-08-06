"""
Tests for benchmark result aggregation (benchmark.metrics.aggregate) and the
exporters. These are the correctness backbone of the benchmark: if
aggregation is wrong, every reported number is wrong.

No app dependencies (fastapi/pydantic/hypothesis) are needed — the benchmark
result types are plain dataclasses — so these run anywhere pytest does.
"""

from benchmark.metrics import aggregate
from benchmark.results import (
    STRATEGY_AI,
    STRATEGY_DETERMINISTIC,
    STRATEGY_HYPOTHESIS,
    STRATEGY_NONE,
    CaseResult,
)
from benchmark.export import to_csv, to_json, to_markdown


def _case(**kw) -> CaseResult:
    base = dict(case_id="c", category="off_by_one", function_name="f")
    base.update(kw)
    return CaseResult(**base)


def test_empty_report_is_safe():
    report = aggregate(seed=1, cases=[])
    assert report.total_cases == 0
    assert report.detection_rate == 0.0
    assert report.detected_count == 0


def test_detection_rate_counts_all_cases_including_misses():
    cases = [
        _case(case_id="a", detected=True, detected_by=STRATEGY_DETERMINISTIC),
        _case(case_id="b", detected=False, detected_by=STRATEGY_NONE, is_miss=True),
        _case(case_id="c", detected=True, detected_by=STRATEGY_AI),
        _case(case_id="d", detected=False, detected_by=STRATEGY_NONE, is_miss=True),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.detected_count == 2
    assert report.detection_rate == 0.5  # misses are NOT dropped
    assert report.miss_count == 2


def test_detection_rate_by_strategy_allows_overlap():
    # A single case can be detected by multiple strategies.
    cases = [
        _case(
            case_id="a",
            detected=True,
            strategy_detected={
                STRATEGY_DETERMINISTIC: True,
                STRATEGY_AI: True,
                STRATEGY_HYPOTHESIS: False,
            },
        ),
        _case(
            case_id="b",
            detected=True,
            strategy_detected={
                STRATEGY_DETERMINISTIC: False,
                STRATEGY_AI: False,
                STRATEGY_HYPOTHESIS: True,
            },
        ),
    ]
    report = aggregate(seed=0, cases=cases)
    by = report.detection_rate_by_strategy
    assert by[STRATEGY_DETERMINISTIC] == 0.5
    assert by[STRATEGY_AI] == 0.5
    assert by[STRATEGY_HYPOTHESIS] == 0.5


def test_detection_rate_by_category():
    cases = [
        _case(case_id="a", category="off_by_one", detected=True),
        _case(case_id="b", category="off_by_one", detected=False, is_miss=True),
        _case(case_id="c", category="empty_input", detected=True),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.detection_rate_by_category["off_by_one"] == 0.5
    assert report.detection_rate_by_category["empty_input"] == 1.0


def test_means_skip_absent_values_not_treat_as_zero():
    # Only one case has a counterexample size; the mean should be that value,
    # not diluted by the undetected case counting as 0.
    cases = [
        _case(case_id="a", detected=True, counterexample_size=4, minimized_size=2,
              minimization_reduction=2),
        _case(case_id="b", detected=False, is_miss=True),  # no size
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.mean_counterexample_size == 4.0
    assert report.mean_minimized_size == 2.0
    assert report.mean_minimization_reduction == 2.0


def test_execution_count_totals_and_mean():
    cases = [
        _case(case_id="a", execution_count=10),
        _case(case_id="b", execution_count=30),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.total_execution_count == 40
    assert report.mean_execution_count == 20.0


def test_invalid_ai_test_rate():
    cases = [
        _case(case_id="a", ai_tests_proposed=4, ai_tests_invalid=1),
        _case(case_id="b", ai_tests_proposed=6, ai_tests_invalid=2),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.total_ai_tests_proposed == 10
    assert report.total_ai_tests_invalid == 3
    assert report.invalid_ai_test_rate == 0.3


def test_invalid_ai_rate_zero_when_no_proposals():
    cases = [_case(case_id="a", ai_tests_proposed=0, ai_tests_invalid=0)]
    report = aggregate(seed=0, cases=cases)
    assert report.invalid_ai_test_rate == 0.0  # no division by zero


def test_claude_metrics_aggregate():
    cases = [
        _case(case_id="a", claude_request_count=1, claude_latency_ms=100.0,
              estimated_input_tokens=50, estimated_output_tokens=10),
        _case(case_id="b", claude_request_count=1, claude_latency_ms=200.0,
              estimated_input_tokens=70, estimated_output_tokens=20),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.total_claude_requests == 2
    assert report.mean_claude_latency_ms == 150.0
    assert report.total_estimated_input_tokens == 120
    assert report.total_estimated_output_tokens == 30


def test_false_bug_reports_and_timeouts_counted():
    cases = [
        _case(case_id="a", detected=True, false_bug_report=True),
        _case(case_id="b", detected=True, timed_out=True),
        _case(case_id="c", detected=True),
    ]
    report = aggregate(seed=0, cases=cases)
    assert report.false_bug_report_count == 1
    assert report.timed_out_count == 1


def test_time_to_first_counterexample_mean_over_detected_only():
    cases = [
        _case(case_id="a", detected=True, time_to_first_counterexample_s=0.2),
        _case(case_id="b", detected=True, time_to_first_counterexample_s=0.4),
        _case(case_id="c", detected=False, is_miss=True),  # no TTF
    ]
    report = aggregate(seed=0, cases=cases)
    assert abs(report.mean_time_to_first_counterexample_s - 0.3) < 1e-9


# --- Exporters --------------------------------------------------------------


def test_json_roundtrips_and_includes_all_cases():
    import json

    cases = [_case(case_id=f"c{i}", detected=bool(i % 2)) for i in range(5)]
    report = aggregate(seed=7, cases=cases)
    data = json.loads(to_json(report))
    assert data["seed"] == 7
    assert data["total_cases"] == 5
    assert len(data["cases"]) == 5  # every case present, none filtered


def test_csv_has_one_row_per_case_plus_header():
    cases = [_case(case_id=f"c{i}") for i in range(3)]
    report = aggregate(seed=0, cases=cases)
    csv_text = to_csv(report)
    rows = [r for r in csv_text.strip().splitlines() if r]
    assert len(rows) == 1 + 3  # header + 3 cases


def test_markdown_always_reports_misses_section():
    cases = [
        _case(case_id="hit", detected=True),
        _case(case_id="missed", detected=False, is_miss=True, category="state_leakage"),
    ]
    report = aggregate(seed=0, cases=cases)
    md = to_markdown(report)
    assert "Misses and limitations" in md
    assert "missed" in md  # the undetected case is named, not hidden
    assert "Detection by strategy" in md
    assert "Detection by category" in md


def test_markdown_clean_run_states_no_misses():
    cases = [_case(case_id="a", detected=True), _case(case_id="b", detected=True)]
    report = aggregate(seed=0, cases=cases)
    md = to_markdown(report)
    assert "No misses, false reports, or errors" in md
