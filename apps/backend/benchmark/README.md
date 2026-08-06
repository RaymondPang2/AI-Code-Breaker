# AI Code Breaker — Evaluation Benchmark

A reproducible benchmark that measures how well AI Code Breaker detects bugs,
across a labelled dataset of deliberately buggy Python functions.

## What it measures

For each case the runner exercises the tool's detection strategies and
records:

- **Bug detection rate** — fraction of known-buggy cases where any strategy
  finds a real (execution-verified) counterexample.
- **Detection rate by strategy** — deterministic / AI / Hypothesis,
  independently (a case can be caught by more than one).
- **Detection rate by category** — per bug category.
- **Time to first counterexample** — wall-clock to the first divergence.
- **Total execution count** — candidate + reference runs performed.
- **Counterexample size** and **minimization reduction** — before/after
  minimizing the failing input.
- **Invalid AI test rate** — fraction of AI-proposed inputs rejected by
  validation.
- **Claude latency, request count, estimated token usage**.
- **False bug reports** — the tool flagging a difference on an input the
  dataset marks as an agreement (should match).
- **Analyses that time out**.

## The dataset

`benchmark/dataset/cases.py` holds 36 cases (≥30 required) spanning all
thirteen categories: off-by-one, duplicate handling, empty input, negative
values, incorrect exception behavior, mutation of input, sorting
assumptions, boundary conditions, floating-point behavior, incorrect loop
termination, incorrect search bounds, state leakage, and order preservation.

Each case has a correct reference, a candidate with exactly one deliberate
bug, a natural-language spec, a category, and at least one known
counterexample. Everything stays within the supported one-`list[int]`
interface. `tests/test_benchmark_dataset.py` verifies every case is
genuinely buggy (candidate and reference actually differ) and that listed
agreements actually agree — so the ground truth can't silently rot.

## Running it

```bash
cd apps/backend
python -m benchmark.run --seed 0 --out benchmark/out
```

Writes three artifacts to the output directory:

- `results.json` — the full report, including every per-case result.
- `results.csv` — one row per case.
- `report.md` — a concise Markdown summary.

Useful flags: `--seed N` (reproducibility), `--no-ai`, `--no-hypothesis`,
`--use-real-ai` (use a configured `ANTHROPIC_API_KEY` instead of the mock),
`--hypothesis-max-examples`, `--hypothesis-timeout`, `--case-timeout`.

## Reproducibility

Every seed-dependent input flows from a single `--seed`: the deterministic
edge-case generator, the mock-AI proposals, and the property search are all
seeded from it. The same seed yields byte-identical detection results and
metrics (wall-clock timing aside, which is inherently machine-dependent).

The AI strategy uses a **deterministic mock provider** by default so the
benchmark runs offline, for free, and reproducibly. `--use-real-ai` swaps in
real Claude when a key is present; those runs are non-deterministic by
nature and cost latency/tokens.

## Honesty

This benchmark does not cherry-pick. Rates are computed over **all** cases,
misses included. The Markdown report always contains a "Misses and
limitations" section naming every undetected case and every false report.

Known structural limitations (also stated in the report):

- **Mutation and state-leakage cases** are hard to catch by design. The
  runner executes each input in a fresh, isolated process, so cross-call
  state leakage and (under value-only comparison) input mutation typically
  don't surface. These four cases are in the dataset on purpose and show up
  as honest misses rather than being hidden — they document a real blind
  spot of a per-input, value-comparison approach.
- **Floating-point cases** only diverge on specific inputs; whether they're
  caught depends on the generated distribution and seed.
- The benchmark measures *detection logic*, not sandbox security: it
  executes the dataset's (trusted) code in-process for speed, whereas the
  production path uses the isolated Docker runner.

## Baseline (seed 0, mock AI)

The committed `benchmark/out/` shows a representative run: 32/36 detected
(88.9%), the 4 misses being exactly the mutation/state-leakage cases, 0
false bug reports. Re-run to regenerate.

## Tests

```bash
pytest tests/test_benchmark_aggregation.py tests/test_benchmark_dataset.py
```

Aggregation tests cover the metric math (rates over all cases, per-strategy
overlap, averages that skip absent values rather than counting them as zero,
division-by-zero guards) and the exporters (JSON completeness, CSV row
count, Markdown always surfacing misses). Dataset tests verify ground-truth
integrity.
