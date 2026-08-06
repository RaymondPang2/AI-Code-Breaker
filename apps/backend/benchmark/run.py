"""
Benchmark CLI.

    python -m benchmark.run --seed 0 --out benchmark/out

Runs the full dataset against the tool's strategies and writes three
artifacts to the output directory:
    results.json   — full report incl. every per-case result
    results.csv    — one row per case
    report.md      — concise, honest Markdown summary

Reproducible: the same --seed produces the same generated inputs, mock-AI
proposals, and search, hence the same report.

By default the AI strategy uses a deterministic mock provider so the run is
offline and reproducible. Pass --use-real-ai to use a configured
ANTHROPIC_API_KEY instead (non-deterministic; costs latency/tokens).
"""

from __future__ import annotations

import argparse
import os

from benchmark.export import to_csv, to_json, to_markdown
from benchmark.runner import RunConfig, run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Code Breaker benchmark")
    parser.add_argument("--seed", type=int, default=0, help="Reproducibility seed.")
    parser.add_argument("--out", default="benchmark/out", help="Output directory.")
    parser.add_argument("--no-ai", action="store_true", help="Skip the AI strategy.")
    parser.add_argument(
        "--no-hypothesis", action="store_true", help="Skip the Hypothesis strategy."
    )
    parser.add_argument(
        "--use-real-ai",
        action="store_true",
        help="Use the real Claude provider (needs ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--hypothesis-max-examples", type=int, default=200,
    )
    parser.add_argument("--hypothesis-timeout", type=float, default=5.0)
    parser.add_argument("--case-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    config = RunConfig(
        seed=args.seed,
        use_ai=not args.no_ai,
        use_hypothesis=not args.no_hypothesis,
        use_real_ai=args.use_real_ai,
        hypothesis_max_examples=args.hypothesis_max_examples,
        hypothesis_timeout_s=args.hypothesis_timeout,
        case_timeout_s=args.case_timeout,
    )

    report = run_benchmark(config)

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "results.json")
    csv_path = os.path.join(args.out, "results.csv")
    md_path = os.path.join(args.out, "report.md")

    with open(json_path, "w") as f:
        f.write(to_json(report))
    with open(csv_path, "w") as f:
        f.write(to_csv(report))
    with open(md_path, "w") as f:
        f.write(to_markdown(report))

    # Console summary (never cherry-picked — this is the same headline as the
    # report, misses included).
    print(f"Benchmark complete — seed {report.seed}, {report.total_cases} cases")
    print(f"  Detection rate:      {report.detection_rate * 100:.1f}% "
          f"({report.detected_count}/{report.total_cases})")
    print(f"  Misses:              {report.miss_count}")
    print(f"  False bug reports:   {report.false_bug_report_count}")
    print(f"  Timed out:           {report.timed_out_count}")
    print(f"  Invalid AI test rate:{report.invalid_ai_test_rate * 100:.1f}%")
    print(f"  Total executions:    {report.total_execution_count}")
    print(f"  Outputs written to:  {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
