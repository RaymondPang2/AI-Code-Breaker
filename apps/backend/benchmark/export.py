"""
Exporters for a BenchmarkReport: JSON, CSV, and a concise Markdown report.

The Markdown report is deliberately honest — it leads with the headline
detection rate, then breaks results down by strategy and category, and
ALWAYS includes a "Misses and limitations" section listing every case the
tool failed to detect (or falsely reported). Nothing is hidden or
cherry-picked.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from benchmark.results import BenchmarkReport, CaseResult


def to_json(report: BenchmarkReport) -> str:
    """Full report incl. every per-case result, as pretty JSON."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


# The per-case columns exported to CSV (stable order).
CSV_COLUMNS = [
    "case_id",
    "category",
    "function_name",
    "detected",
    "detected_by",
    "time_to_first_counterexample_s",
    "total_time_s",
    "timed_out",
    "execution_count",
    "counterexample_size",
    "minimized_size",
    "minimization_reduction",
    "ai_tests_proposed",
    "ai_tests_valid",
    "ai_tests_invalid",
    "claude_latency_ms",
    "claude_request_count",
    "estimated_input_tokens",
    "estimated_output_tokens",
    "false_bug_report",
    "is_miss",
    "error",
]


def to_csv(report: BenchmarkReport) -> str:
    """One row per case."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for case in report.cases:
        writer.writerow(case.to_dict())
    return buf.getvalue()


def _fmt(value: object, nd: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def to_markdown(report: BenchmarkReport, *, title: str = "AI Code Breaker — Benchmark Report") -> str:
    """A concise, honest Markdown summary."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"_Generated {now} · seed `{report.seed}` · {report.total_cases} cases_")
    lines.append("")

    # Headline.
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **Bug detection rate:** {_pct(report.detection_rate)} "
                 f"({report.detected_count}/{report.total_cases})")
    lines.append(f"- **Misses:** {report.miss_count}")
    lines.append(f"- **False bug reports:** {report.false_bug_report_count}")
    lines.append(f"- **Timed out:** {report.timed_out_count}")
    lines.append("")

    # Detection by strategy.
    lines.append("## Detection by strategy")
    lines.append("")
    lines.append("| Strategy | Detection rate |")
    lines.append("| --- | --- |")
    for strat, rate in report.detection_rate_by_strategy.items():
        lines.append(f"| {strat} | {_pct(rate)} |")
    lines.append("")

    # Detection by category.
    lines.append("## Detection by category")
    lines.append("")
    lines.append("| Category | Detection rate |")
    lines.append("| --- | --- |")
    for cat, rate in sorted(report.detection_rate_by_category.items()):
        lines.append(f"| {cat} | {_pct(rate)} |")
    lines.append("")

    # Efficiency + cost.
    lines.append("## Efficiency and cost")
    lines.append("")
    lines.append(f"- Mean time to first counterexample: "
                 f"{_fmt(report.mean_time_to_first_counterexample_s)} s")
    lines.append(f"- Total execution count: {report.total_execution_count} "
                 f"(mean {_fmt(report.mean_execution_count)} per case)")
    lines.append(f"- Mean counterexample size: {_fmt(report.mean_counterexample_size)}")
    lines.append(f"- Mean minimized size: {_fmt(report.mean_minimized_size)}")
    lines.append(f"- Mean minimization reduction: {_fmt(report.mean_minimization_reduction)}")
    lines.append(f"- Total wall-clock time: {_fmt(report.total_time_s)} s")
    lines.append("")

    # AI usage.
    lines.append("## Claude / AI usage")
    lines.append("")
    lines.append(f"- AI tests proposed: {report.total_ai_tests_proposed}")
    lines.append(f"- Invalid AI test rate: {_pct(report.invalid_ai_test_rate)} "
                 f"({report.total_ai_tests_invalid} invalid)")
    lines.append(f"- Claude requests: {report.total_claude_requests}")
    lines.append(f"- Mean Claude latency: {_fmt(report.mean_claude_latency_ms)} ms")
    lines.append(f"- Estimated tokens: {report.total_estimated_input_tokens} in / "
                 f"{report.total_estimated_output_tokens} out")
    lines.append("")

    # Misses and limitations — ALWAYS present, never filtered.
    lines.append("## Misses and limitations")
    lines.append("")
    misses = [c for c in report.cases if c.is_miss]
    false_reports = [c for c in report.cases if c.false_bug_report]
    errored = [c for c in report.cases if c.error]

    if not misses and not false_reports and not errored:
        lines.append("No misses, false reports, or errors in this run.")
        lines.append("")
    else:
        if misses:
            lines.append(f"**Undetected bugs ({len(misses)}):** the tool did not "
                         "find a counterexample for these known-buggy cases.")
            lines.append("")
            for c in misses:
                lines.append(f"- `{c.case_id}` ({c.category})"
                             + (f" — {c.error}" if c.error else ""))
            lines.append("")
        if false_reports:
            lines.append(f"**False bug reports ({len(false_reports)}):** the tool "
                         "flagged a difference on an input the dataset marks as an "
                         "agreement.")
            lines.append("")
            for c in false_reports:
                lines.append(f"- `{c.case_id}` ({c.category})")
            lines.append("")
        if errored:
            lines.append(f"**Errored cases ({len(errored)}):**")
            lines.append("")
            for c in errored:
                lines.append(f"- `{c.case_id}` — {c.error}")
            lines.append("")

    # Known structural limitations of the benchmark itself.
    lines.append("### Known structural limitations")
    lines.append("")
    lines.append("- **Mutation and state-leakage cases** are hard to catch by "
                 "design: the runner executes each input in a fresh, isolated "
                 "process, so cross-call state leakage and (in the default "
                 "value-only comparison) input mutation may not surface. These "
                 "cases are included honestly and their misses are reported "
                 "above rather than excluded.")
    lines.append("- **Floating-point cases** may only diverge on specific "
                 "inputs; whether they're caught depends on the generated "
                 "test distribution and seed.")
    lines.append("- Detection depends on the configured strategies, seed, and "
                 "per-case time budget; a different seed can change which "
                 "subtle cases are caught.")
    lines.append("")

    return "\n".join(lines)
