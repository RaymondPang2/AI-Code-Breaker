"""
Pydantic models for the submission API.

These models define the wire contract for everything under /submissions.
Validation lives here, via Pydantic validators, rather than in endpoint
functions — so the contract is enforced identically no matter which route
uses these models, now (`/submissions/validate`) or later (execution,
minimization, explanation).

No code is executed anywhere in this module. It only describes shapes and
rejects malformed input.
"""

from __future__ import annotations

import keyword
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.runner import RunnerStatus
from app.schemas.test_case import TestCaseSource

# --- Limits -------------------------------------------------------------
#
# These bounds exist to keep every downstream stage predictable:
#   - request parsing and validation stays fast regardless of who's calling,
#   - the future sandbox runner (M2) gets inputs of known maximum size,
#     which matters for setting per-run CPU/memory/timeout limits,
#   - a single submission can't be used to smuggle a huge payload through
#     the API before any code-execution safeguards exist.
#
# They're generous enough that no legitimate submission should ever hit
# them — this project targets `list[int]`-argument functions, not large
# programs — but bounded so the API's behavior under abuse is a clean 422,
# not an unbounded parse/allocate.

MAX_FUNCTION_NAME_LENGTH = 100
MAX_SPECIFICATION_LENGTH = 2_000
MAX_SOURCE_CODE_LENGTH = 20_000
MAX_TEST_CASES = 20
MAX_INPUT_LIST_SIZE = 1_000

# TestCaseGenerator produces at most one input per required category (see
# app.services.test_case_generator) — this cap matches that category count
# and exists as an explicit, enforced invariant rather than an implicit one.
MAX_GENERATED_TESTS = 15

# The combined ceiling across manual + generated inputs for a single
# analysis run. Deliberately less than MAX_TEST_CASES + MAX_GENERATED_TESTS
# (20 + 15 = 35) so the truncation path in
# app.services.test_selection_service is a real, exercised behavior rather
# than a number that can never actually be reached. Manual inputs are
# never dropped for space — only generated inputs are truncated to fit.
MAX_TOTAL_TESTS = 30


def _strip_if_str(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


class CodeSubmissionBase(BaseModel):
    """
    Fields shared by every request that submits a spec + two
    implementations to compare: /submissions/validate and
    /submissions/analyze (via SubmissionRequest), and
    /submissions/search (via HypothesisSearchRequest, see
    app.schemas.hypothesis_search). Subclasses add whatever's specific to
    how they select or generate inputs.
    """

    function_name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_FUNCTION_NAME_LENGTH,
        description="Name of the function under test in both code samples.",
    )
    specification: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SPECIFICATION_LENGTH,
        description="Natural-language description of the intended behavior.",
    )
    candidate_code: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SOURCE_CODE_LENGTH,
        description="Source code of the implementation being checked.",
    )
    reference_code: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SOURCE_CODE_LENGTH,
        description="Source code of the trusted, correct implementation.",
    )

    @field_validator(
        "function_name", "specification", "candidate_code", "reference_code", mode="before"
    )
    @classmethod
    def strip_surrounding_whitespace(cls, value: Any) -> Any:
        """Normalize by trimming outer whitespace; never touches internal
        formatting (e.g. indentation inside source code)."""
        return _strip_if_str(value)

    @field_validator("function_name")
    @classmethod
    def function_name_must_be_valid_identifier(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError(
                "function_name must be a valid Python identifier "
                "(letters, digits, underscores; cannot start with a digit)"
            )
        if keyword.iskeyword(value):
            raise ValueError("function_name must not be a Python reserved keyword")
        return value


class SubmissionRequest(CodeSubmissionBase):
    """
    A single submission to analyze: a spec, two implementations to compare,
    and an optional set of manually-supplied test inputs.

    Every submitted function is assumed, for now, to accept exactly one
    positional `list[int]` argument — this is enforced structurally by
    `test_inputs` being `list[list[int]]`, not by inspecting the source.
    """

    test_inputs: list[list[int]] = Field(
        default_factory=list,
        description=(
            "Manually supplied test inputs, each a list[int] argument to "
            "pass to both implementations. Optional — combined with "
            "automatically generated inputs when generate_tests is true."
        ),
    )
    generate_tests: bool = Field(
        default=False,
        description=(
            "If true, automatically generated inputs (see "
            "app.services.test_case_generator) are combined with "
            "test_inputs before analysis. Off by default so the number of "
            "executions for a request is always exactly what the caller "
            "supplied unless explicitly opted in."
        ),
    )
    generation_seed: int = Field(
        default=0,
        description=(
            "Seed for automatic test generation. The same seed always "
            "produces the same generated inputs, so a submission's "
            "generated coverage is reproducible."
        ),
    )
    use_ai_tests: bool = Field(
        default=False,
        description=(
            "If true, Claude is asked to propose additional targeted test "
            "inputs, which are validated, deduped, and run through the same "
            "comparison engine as every other input. Off by default. If AI "
            "is not configured or the provider fails, analysis proceeds "
            "normally with deterministic + manual tests — enabling this "
            "never causes a request to fail."
        ),
    )
    explain_counterexamples: bool = Field(
        default=False,
        description=(
            "If true, and a counterexample is confirmed by real execution, "
            "Claude is asked to explain it (after the fact — never to decide "
            "pass/fail). If AI is unavailable or fails, a deterministic "
            "fallback explanation is produced instead, so enabling this "
            "never causes a request to fail."
        ),
    )
    suggest_patch: bool = Field(
        default=False,
        description=(
            "If true (and explain_counterexamples is on), the explanation "
            "may include a proposed patch. The patch is a suggestion only "
            "and is never applied to your code automatically."
        ),
    )

    @field_validator("test_inputs", mode="before")
    @classmethod
    def validate_test_inputs_structure(cls, value: Any) -> Any:
        """
        Validate the raw shape explicitly, before Pydantic's own int
        coercion runs, so error messages are precise and so booleans
        (which are technically ints in Python) are rejected rather than
        silently coerced to 0/1.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("test_inputs must be a JSON array")
        if len(value) > MAX_TEST_CASES:
            raise ValueError(
                f"test_inputs may contain at most {MAX_TEST_CASES} entries "
                f"(got {len(value)})"
            )
        for i, inner in enumerate(value):
            if not isinstance(inner, list):
                raise ValueError(f"test_inputs[{i}] must be a JSON array of integers")
            if len(inner) > MAX_INPUT_LIST_SIZE:
                raise ValueError(
                    f"test_inputs[{i}] has {len(inner)} elements, exceeding "
                    f"the max of {MAX_INPUT_LIST_SIZE}"
                )
            for j, item in enumerate(inner):
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ValueError(
                        f"test_inputs[{i}][{j}] must be an integer, got "
                        f"{type(item).__name__}"
                    )
        return value


class FunctionExecutionResult(BaseModel):
    """
    The outcome of running one implementation (candidate or reference) on
    one input, as reported by the runner subprocess.

    `status` mirrors the runner's own protocol (see app.schemas.runner and
    runner/runner.py) rather than collapsing everything into a single
    "did it raise" boolean — the comparison engine needs to tell a
    genuine `runtime_error` apart from a `timeout` or an `internal_error`,
    and a frontend will eventually want to render each differently too.

    This is a deliberately separate model from RunnerResult even though
    the fields largely mirror it: this one is the stable, public API
    contract; RunnerResult is the internal subprocess protocol. Keeping
    them distinct means the runner can change its wire format later
    (e.g. once it's a Docker container) without changing this response
    shape, as long as the mapping step (see
    app.services.comparison_service) is updated to match.
    """

    status: RunnerStatus = Field(
        description="What happened when this implementation was called."
    )
    returned_value: Any = Field(
        default=None, description="The value returned by the call, if it succeeded."
    )
    exception_type: str | None = Field(
        default=None, description="Exception class name, if one was raised."
    )
    exception_message: str | None = Field(
        default=None, description="Short, sanitized exception message, if any."
    )
    stdout: str = Field(default="", description="Captured, size-capped stdout.")
    stderr: str = Field(default="", description="Captured, size-capped stderr.")
    runtime_ms: float | None = Field(
        default=None, description="Wall-clock execution time in milliseconds."
    )


class TestComparisonResult(BaseModel):
    """
    Candidate vs. reference behavior on a single input.

    Runtime is tracked per-implementation (inside each
    FunctionExecutionResult) rather than as one combined number: candidate
    and reference run as separate subprocesses with independent timeouts,
    so a single shared runtime would hide which side was slow — or which
    side hung.
    """

    input: list[int]
    source: TestCaseSource = Field(
        description="Whether this input was manually supplied or automatically generated."
    )
    category: str = Field(
        description="'manual' for caller-supplied inputs, or the generator "
        "category (e.g. 'duplicate_maximum') for generated ones."
    )
    reason: str = Field(
        description="Human-readable explanation of what this input is intended to exercise."
    )
    candidate: FunctionExecutionResult
    reference: FunctionExecutionResult
    match: bool = Field(
        description="True if candidate and reference are considered "
        "equivalent on this input — see comparison rules in "
        "app.services.comparison_service."
    )
    internal_error: bool = Field(
        default=False,
        description=(
            "True if this comparison is inconclusive because the runner "
            "itself failed (e.g. produced malformed output), rather than "
            "because candidate and reference actually disagree. Such "
            "comparisons always have match=False but should not be "
            "reported to the user as a confirmed bug."
        ),
    )


class SubmissionAnalysisResponse(BaseModel):
    """Full result of running candidate vs. reference across every supplied
    test input."""

    function_name: str
    total_tests: int = Field(ge=0)
    passed_tests: int = Field(ge=0)
    failed_tests: int = Field(
        ge=0,
        description=(
            "Number of CONFIRMED behavioral mismatches (candidate and "
            "reference genuinely disagreed). Does NOT include inconclusive "
            "cases — those are counted separately so passed + failed + "
            "inconclusive == total_tests."
        ),
    )
    inconclusive_tests: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of tests where a harness/runner error (internal_error on "
            "either side) prevented a real comparison. These are NOT failures "
            "— they mean execution infrastructure malfunctioned, not that the "
            "implementations disagree."
        ),
    )
    comparisons: list[TestComparisonResult] = Field(default_factory=list)
    first_failing_input: list[int] | None = Field(
        default=None,
        description=(
            "The first test input (in original order) where candidate and "
            "reference genuinely disagreed. None if every comparison "
            "matched, or if the only disagreements were internal_error "
            "cases rather than confirmed behavioral differences."
        ),
    )
    submission_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "ID of the persisted submission this analysis was saved under. "
            "Use with analysis_run_id to fetch the stored result via "
            "GET /submissions/{id}/analyses/{analysis_id}."
        ),
    )
    analysis_run_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the persisted analysis run.",
    )
    ai_usage: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Observability for the AI test-generation attempt (model, token "
            "usage, latency, request count, availability). Present only when "
            "use_ai_tests was requested. Never contains secrets."
        ),
    )
    counterexample_explanation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured explanation of the first confirmed counterexample "
            "(AI-generated or deterministic fallback), labelled with its "
            "source. Present only when explain_counterexamples was requested "
            "and a counterexample was confirmed. Advisory — never overrides "
            "the verified execution results."
        ),
    )
    explanation_usage: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Observability for the explanation attempt (model, tokens, "
            "latency, availability). Present only when explanation was "
            "requested. Never contains secrets."
        ),
    )


class AnalysisOptionsRequest(BaseModel):
    """
    Options for POST /submissions/{id}/analyses. The immutable submission
    content (spec + code) already lives on the stored submission; this
    carries only how to run the analysis. The route combines the two into a
    full SubmissionRequest for the worker.
    """

    test_inputs: list[list[int]] = Field(default_factory=list)
    generate_tests: bool = False
    generation_seed: int = 0
    use_ai_tests: bool = False
    explain_counterexamples: bool = False
    suggest_patch: bool = False

    @field_validator("test_inputs", mode="before")
    @classmethod
    def _validate_test_inputs(cls, value: Any) -> Any:
        # Reuse SubmissionRequest's structural validation for test inputs.
        return SubmissionRequest.validate_test_inputs_structure(value)
