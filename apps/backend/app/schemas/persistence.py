"""
Read-side Pydantic schemas for persisted records (the GET endpoints).

These are deliberately separate from the write-side request models and
from the internal ORM models. They control exactly what leaves the API:
no internal file paths, no container IDs, no raw tracebacks — only the
already-sanitized normalized results. A field has to be added here on
purpose to ever be exposed, so nothing sensitive leaks by default.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SubmissionRead(BaseModel):
    """A persisted submission, as returned by GET /submissions/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    function_name: str
    specification: str
    candidate_code: str
    reference_code: str
    created_at: datetime


class ExecutionRead(BaseModel):
    """One implementation's normalized result on one test case."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str = Field(description="'candidate' or 'reference'.")
    test_case_id: uuid.UUID
    input: list[int] = Field(description="The test-case input this execution ran on.")
    normalized_result: dict[str, Any] = Field(
        description="Sanitized result: status, returned_value, exception_type/message, stdout, stderr."
    )
    runtime_ms: float | None = None
    timed_out: bool = False


class CounterexampleRead(BaseModel):
    """A confirmed failing input for an analysis run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_input: list[int]
    minimized_input: list[int] | None = None
    candidate_result: dict[str, Any]
    reference_result: dict[str, Any]
    explanation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured explanation of the bug (AI-generated or "
            "deterministic fallback). Always labelled with its source and "
            "ai_generated flag; never overwrites the verified "
            "candidate_result / reference_result. May be null if no "
            "explanation has been generated."
        ),
    )


class AnalysisRunRead(BaseModel):
    """
    A persisted analysis run, as returned by
    GET /submissions/{id}/analyses/{analysis_id}.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID
    status: str
    progress: float = 0.0
    error: str | None = None
    total_tests: int
    passed_tests: int
    failed_tests: int
    inconclusive_tests: int = 0
    elapsed_seconds: float | None = None
    seed: int | None = None
    configuration: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    executions: list[ExecutionRead] = Field(default_factory=list)
    counterexamples: list[CounterexampleRead] = Field(default_factory=list)


class AnalysisJobCreatedResponse(BaseModel):
    """Returned immediately by POST /submissions/{id}/analyses. The job runs
    asynchronously; poll the analysis GET endpoint for status + results."""

    submission_id: uuid.UUID
    analysis_id: uuid.UUID
    status: str = Field(description="Initial status — 'queued'.")


class SubmissionCreatedResponse(BaseModel):
    submission_id: uuid.UUID


class ShareRequest(BaseModel):
    """Body for enabling/disabling a public share link."""

    public: bool = True


class ShareResponse(BaseModel):
    submission_id: uuid.UUID
    is_public: bool
    # Present only when public; the opaque token used in the share URL.
    share_token: str | None = None


class DeletionResponse(BaseModel):
    submission_id: uuid.UUID
    deleted: bool
