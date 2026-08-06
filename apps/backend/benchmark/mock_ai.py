"""
A deterministic, seeded mock AI test provider for the benchmark.

Real Claude calls are non-deterministic and cost money/latency, which makes
them unsuitable for a reproducible benchmark baseline. This provider stands
in: given a case, it proposes a fixed, seed-derived set of candidate inputs
(some valid, some deliberately invalid) so the benchmark can measure the
invalid-AI-test rate, request count, latency, and token estimates
deterministically.

When a real ANTHROPIC_API_KEY is configured and --use-real-ai is passed, the
runner uses the real provider instead (see runner.py). The mock is the
default so `python -m benchmark.run` works offline and reproducibly.

The mock intentionally includes the case's known counterexample among its
proposals for a subset of cases, so the "ai" strategy has real detections to
report — but never for all cases, so the benchmark shows a realistic mix.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from benchmark.case_schema import BenchmarkCase


@dataclass
class MockAIResponse:
    """What the mock 'provider call' yields for one case."""

    raw_text: str
    proposed_count: int
    invalid_count: int
    latency_ms: float
    input_tokens: int
    output_tokens: int


def _stable_seed(case_id: str, seed: int) -> int:
    h = hashlib.sha256(f"{case_id}:{seed}".encode()).hexdigest()
    return int(h[:8], 16)


def propose_tests(case: BenchmarkCase, seed: int) -> MockAIResponse:
    """
    Deterministically propose test inputs for a case. The proposals are a
    mix of:
      - the case's first known counterexample (for ~2/3 of cases, so 'ai'
        detects a realistic subset),
      - a few plausible valid inputs derived from the seed,
      - one deliberately INVALID input (out-of-range / wrong shape) so the
        invalid-AI-test rate is non-zero and measurable.

    Returns the raw JSON text a provider would return, plus ground-truth
    counts the runner uses for AI metrics (proposed / invalid).
    """
    s = _stable_seed(case.case_id, seed)

    valid_inputs: list[list[int]] = []
    # Include the known counterexample for ~2/3 of cases (by hash), so the
    # AI strategy has genuine—but not universal—detections.
    if s % 3 != 0 and case.known_counterexamples:
        valid_inputs.append(list(case.known_counterexamples[0]))
    # A couple of seed-derived plausible inputs.
    a = s % 7
    b = (s // 7) % 5
    valid_inputs.append([a, b, a - b])
    valid_inputs.append([b])

    # One deliberately invalid proposal: value outside the allowed int range
    # (the AIProposedTest schema rejects it), so it counts as invalid.
    invalid_inputs = [[10**9 + 1]]  # exceeds MAX allowed magnitude

    proposed = valid_inputs + invalid_inputs
    # The raw text mimics a provider returning a JSON array of {"input": [...]}.
    raw = json.dumps([{"input": inp} for inp in proposed])

    # Deterministic pseudo-latency / token estimates derived from the seed
    # and payload size, so cost metrics are reproducible.
    latency_ms = 40.0 + (s % 60)
    input_tokens = 120 + len(json.dumps(case.specification)) // 4
    output_tokens = 20 + len(raw) // 4

    return MockAIResponse(
        raw_text=raw,
        proposed_count=len(proposed),
        invalid_count=len(invalid_inputs),
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
