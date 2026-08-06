"""
Result types for a benchmark run.

Two levels:
  - CaseResult: what happened for one case (detected?, by which strategy,
    time-to-first-counterexample, counterexample size, minimization, AI
    usage, whether it was a false report or timed out).
  - BenchmarkReport: the aggregate metrics across all cases.

These are plain dataclasses (not Pydantic) so aggregation is pure, easy to
test, and trivially serializable to JSON/CSV. All aggregation lives in
metrics.py and is unit-tested; nothing here fabricates or filters results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Which strategy first exposed the bug for a case.
STRATEGY_DETERMINISTIC = "deterministic"
STRATEGY_AI = "ai"
STRATEGY_HYPOTHESIS = "hypothesis"
STRATEGY_NONE = "none"  # not detected by any strategy


@dataclass
class CaseResult:
    """Outcome of running the tool against one benchmark case."""

    case_id: str
    category: str
    function_name: str

    # Detection.
    detected: bool = False
    # Which strategy first found a counterexample (deterministic / ai /
    # hypothesis / none).
    detected_by: str = STRATEGY_NONE
    # A per-strategy breakdown of whether that strategy independently found a
    # counterexample (a case can be found by more than one).
    strategy_detected: dict[str, bool] = field(default_factory=dict)

    # Timing.
    time_to_first_counterexample_s: float | None = None
    total_time_s: float = 0.0
    timed_out: bool = False

    # Execution volume.
    execution_count: int = 0

    # Counterexample size + minimization.
    counterexample_size: int | None = None  # len of the first failing input
    minimized_size: int | None = None
    minimization_reduction: int | None = None  # size before - size after

    # AI metrics.
    ai_tests_proposed: int = 0
    ai_tests_valid: int = 0
    ai_tests_invalid: int = 0
    claude_latency_ms: float | None = None
    claude_request_count: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0

    # Correctness of the benchmark signal itself.
    # A "false bug report" = the tool flagged a difference on an input the
    # dataset marks as an agreement (candidate and reference should match).
    false_bug_report: bool = False
    # Sanity: did the tool's verdict match the known ground truth (this case
    # IS buggy, so detected==True is correct; detected==False is a miss).
    is_miss: bool = False

    # Free-form, non-sensitive error note if the case errored out.
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Aggregate metrics across all cases in a run."""

    seed: int
    total_cases: int

    # Detection.
    detected_count: int = 0
    detection_rate: float = 0.0
    detection_rate_by_strategy: dict[str, float] = field(default_factory=dict)
    detection_rate_by_category: dict[str, float] = field(default_factory=dict)

    # Timing.
    mean_time_to_first_counterexample_s: float | None = None
    total_time_s: float = 0.0
    timed_out_count: int = 0

    # Execution volume.
    total_execution_count: int = 0
    mean_execution_count: float = 0.0

    # Counterexample size + minimization.
    mean_counterexample_size: float | None = None
    mean_minimized_size: float | None = None
    mean_minimization_reduction: float | None = None

    # AI.
    total_ai_tests_proposed: int = 0
    total_ai_tests_invalid: int = 0
    invalid_ai_test_rate: float = 0.0
    total_claude_requests: int = 0
    mean_claude_latency_ms: float | None = None
    total_estimated_input_tokens: int = 0
    total_estimated_output_tokens: int = 0

    # Correctness.
    false_bug_report_count: int = 0
    miss_count: int = 0

    # Per-case detail (kept for JSON export / auditing — never filtered).
    cases: list[CaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d
