"""
ORM models for AI Code Breaker persistence.

Relationship overview (see the README's "Schema relationships" section for
the full narrative):

    Submission 1───* AnalysisRun 1───* Execution
        │                  │
        │                  └───* Counterexample
        │
        └───* TestCase

  - A Submission is the immutable record of what was submitted (spec + two
    implementations). One submission can be analyzed many times.
  - A TestCase is one input that was run for a submission. It belongs to
    the submission (the inputs are a property of what's being tested), and
    each Execution references the test case it ran.
  - An AnalysisRun is one execution of the pipeline over a submission —
    its status, totals, timing, seed, and configuration.
  - An Execution is one implementation (candidate OR reference) running on
    one test case within one analysis run.
  - A Counterexample is a confirmed failing input for an analysis run,
    with original + minimized forms and an explanation field reserved for
    a later (AI) milestone.

All primary keys are UUIDs (app.db.base.GUID). All timestamps are stored
timezone-aware in UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import GUID, Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Submission(Base):
    """What was submitted: a spec and two implementations to compare."""

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=_uuid)
    function_name: Mapped[str] = mapped_column(String(100), nullable=False)
    specification: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_code: Mapped[str] = mapped_column(Text, nullable=False)
    reference_code: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Non-reversible digest of the creating client's identity (see
    # app.core.identity). Used to scope reads/deletes and to count a
    # client's analyses for quota/concurrency — without storing a raw IP.
    owner_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Stored code is private by default. A submission is only reachable via a
    # public share link when is_public is True, and only through its
    # unguessable share_token — never by enumerating UUIDs.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    share_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )

    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Listing submissions newest-first is the obvious access pattern.
        Index("ix_submissions_created_at", "created_at"),
    )


class TestCase(Base):
    """One input that was run for a submission."""

    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    # The list[int] input, stored as JSON (a JSON array of integers).
    input: Mapped[list] = mapped_column(JSON, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    submission: Mapped["Submission"] = relationship(back_populates="test_cases")
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="test_case", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_test_cases_submission_id", "submission_id"),
    )


class AnalysisRun(Base):
    """One run of the analysis pipeline over a submission."""

    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    # Lifecycle status: one of app.core.analysis_status.ALL_STATUSES
    # ('queued', 'generating_tests', ..., 'completed', 'failed',
    # 'cancelled'). Kept as a plain string rather than a DB enum so adding
    # a new status is a code change, not a migration.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")

    # Coarse 0..1 progress, derived from the current stage. Stored so the
    # polling GET endpoint can report it without recomputing.
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Short, sanitized error message when status == 'failed'. Never contains
    # tracebacks, host paths, or secrets — see app.worker error handling.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The RQ job id backing this run. Used for idempotency (a run maps to a
    # single job id) and cancellation. Null until enqueued.
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    total_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Tests where a harness/runner error prevented a real comparison. Kept
    # distinct from failed_tests so passed + failed + inconclusive == total.
    inconclusive_tests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Present when generation was used; null otherwise.
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-form configuration snapshot (generate_tests flag, execution
    # backend, limits in effect, etc.) as JSON, so how a run was configured
    # is reproducible without a schema change every time an option is added.
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    # When the worker actually started / finished processing (distinct from
    # created_at, which is when the job was enqueued).
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    submission: Mapped["Submission"] = relationship(back_populates="analysis_runs")
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan"
    )
    counterexamples: Mapped[list["Counterexample"]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Listing a submission's runs newest-first.
        Index("ix_analysis_runs_submission_id_created_at", "submission_id", "created_at"),
    )


class Execution(Base):
    """One implementation (candidate or reference) run on one test case."""

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=_uuid)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False
    )
    # 'candidate' | 'reference'. The CheckConstraint below enforces this
    # at the database level, so no invalid role can ever be written even
    # if a future code path forgets to.
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    # The normalized result, mirroring FunctionExecutionResult's public
    # shape (status, returned_value, exception_type, exception_message,
    # stdout, stderr). Stored as JSON. NOTE: exception_message/stdout/stderr
    # are already sanitized upstream (runner.py) — no host paths, container
    # IDs, or raw tracebacks — so nothing sensitive is persisted here.
    normalized_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    runtime_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="executions")
    test_case: Mapped["TestCase"] = relationship(back_populates="executions")

    __table_args__ = (
        Index("ix_executions_analysis_run_id", "analysis_run_id"),
        Index("ix_executions_test_case_id", "test_case_id"),
        # One candidate and one reference execution per (run, test case);
        # guards against accidentally writing duplicates.
        UniqueConstraint(
            "analysis_run_id", "test_case_id", "role", name="uq_execution_run_case_role"
        ),
        # role is a controlled vocabulary; reject anything else at the DB.
        CheckConstraint(
            "role IN ('candidate', 'reference')", name="ck_execution_role_valid"
        ),
    )


class Counterexample(Base):
    """A confirmed failing input for an analysis run."""

    __tablename__ = "counterexamples"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=_uuid)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    original_input: Mapped[list] = mapped_column(JSON, nullable=False)
    # Null until/unless a minimization pass ran.
    minimized_input: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Normalized candidate/reference results on the failing input (same
    # sanitized shape as Execution.normalized_result).
    candidate_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    reference_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Structured, AI-generated-or-deterministic explanation of the bug
    # (see app.schemas.explanation.CounterexampleExplanation). Stored as
    # JSON, nullable — populated only when an explanation has been
    # generated. It never overwrites candidate_result / reference_result,
    # which remain the verified execution facts.
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="counterexamples")

    __table_args__ = (
        Index("ix_counterexamples_analysis_run_id", "analysis_run_id"),
    )
