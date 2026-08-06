"""
Schema for one benchmark case.

Each case pairs a correct reference implementation with a deliberately buggy
candidate, plus the metadata the benchmark needs: a natural-language
specification, a bug category, and at least one KNOWN counterexample — an
input on which candidate and reference are expected to differ.

The known counterexample matters for correctness of the benchmark itself:
it lets the runner sanity-check that a case is actually buggy (the two
implementations really do differ on that input) BEFORE measuring whether the
tool detects it. A case whose known counterexample doesn't actually trigger
a difference is a bug in the dataset, and the runner flags it rather than
silently reporting a miss.

Everything stays within the supported one-`list[int]`-argument interface:
both implementations take a single `list[int]` and return a JSON-serializable
value (or raise).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canonical bug categories covered by the dataset. Kept as plain strings so
# adding a category is a data change, not a schema migration.
BUG_CATEGORIES = (
    "off_by_one",
    "duplicate_handling",
    "empty_input",
    "negative_values",
    "incorrect_exception_behavior",
    "mutation_of_input",
    "sorting_assumptions",
    "boundary_conditions",
    "floating_point_behavior",
    "incorrect_loop_termination",
    "incorrect_search_bounds",
    "state_leakage",
    "order_preservation",
)


@dataclass(frozen=True)
class BenchmarkCase:
    """One reference/candidate pair with metadata."""

    case_id: str
    function_name: str
    specification: str
    category: str
    reference_code: str
    candidate_code: str
    # Inputs on which candidate and reference are EXPECTED to differ. At
    # least one is required. Each is a single list[int] argument.
    known_counterexamples: list[list[int]]
    # Optional inputs on which they should AGREE — used to catch false bug
    # reports (the tool flagging a difference where there is none).
    known_agreements: list[list[int]] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.category not in BUG_CATEGORIES:
            raise ValueError(
                f"{self.case_id}: unknown category {self.category!r}. "
                f"Expected one of {BUG_CATEGORIES}."
            )
        if not self.known_counterexamples:
            raise ValueError(
                f"{self.case_id}: at least one known counterexample is required."
            )
        for ce in self.known_counterexamples:
            if not isinstance(ce, list) or not all(isinstance(x, int) for x in ce):
                raise ValueError(
                    f"{self.case_id}: counterexample {ce!r} must be a list[int]."
                )
