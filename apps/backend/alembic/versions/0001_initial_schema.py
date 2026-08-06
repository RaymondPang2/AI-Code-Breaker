"""initial schema: submissions, test_cases, analysis_runs, executions, counterexamples

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-01-15 00:00:00.000000

Hand-written initial migration. It mirrors app.models.entities exactly.
Uses a portable UUID column: native UUID on PostgreSQL, CHAR(32) elsewhere
(SQLite in tests) — matching app.db.base.GUID.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _guid():
    """UUID column type: native on Postgres, CHAR(32) on other backends."""
    return sa.types.Uuid(as_uuid=True).with_variant(
        postgresql.UUID(as_uuid=True), "postgresql"
    ).with_variant(sa.CHAR(32), "sqlite")


def upgrade() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", _guid(), primary_key=True),
        sa.Column("function_name", sa.String(length=100), nullable=False),
        sa.Column("specification", sa.Text(), nullable=False),
        sa.Column("candidate_code", sa.Text(), nullable=False),
        sa.Column("reference_code", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_submissions_created_at", "submissions", ["created_at"])

    op.create_table(
        "test_cases",
        sa.Column("id", _guid(), primary_key=True),
        sa.Column("submission_id", _guid(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_test_cases_submission_id", "test_cases", ["submission_id"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", _guid(), primary_key=True),
        sa.Column("submission_id", _guid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_tests", sa.Integer(), nullable=False),
        sa.Column("passed_tests", sa.Integer(), nullable=False),
        sa.Column("failed_tests", sa.Integer(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_analysis_runs_submission_id_created_at",
        "analysis_runs",
        ["submission_id", "created_at"],
    )

    op.create_table(
        "executions",
        sa.Column("id", _guid(), primary_key=True),
        sa.Column("analysis_run_id", _guid(), nullable=False),
        sa.Column("test_case_id", _guid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("normalized_result", sa.JSON(), nullable=False),
        sa.Column("runtime_ms", sa.Float(), nullable=True),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "analysis_run_id", "test_case_id", "role", name="uq_execution_run_case_role"
        ),
        sa.CheckConstraint(
            "role IN ('candidate', 'reference')", name="ck_execution_role_valid"
        ),
    )
    op.create_index(
        "ix_executions_analysis_run_id", "executions", ["analysis_run_id"]
    )
    op.create_index("ix_executions_test_case_id", "executions", ["test_case_id"])

    op.create_table(
        "counterexamples",
        sa.Column("id", _guid(), primary_key=True),
        sa.Column("analysis_run_id", _guid(), nullable=False),
        sa.Column("original_input", sa.JSON(), nullable=False),
        sa.Column("minimized_input", sa.JSON(), nullable=True),
        sa.Column("candidate_result", sa.JSON(), nullable=False),
        sa.Column("reference_result", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_counterexamples_analysis_run_id",
        "counterexamples",
        ["analysis_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_counterexamples_analysis_run_id", table_name="counterexamples")
    op.drop_table("counterexamples")
    op.drop_index("ix_executions_test_case_id", table_name="executions")
    op.drop_index("ix_executions_analysis_run_id", table_name="executions")
    op.drop_table("executions")
    op.drop_index(
        "ix_analysis_runs_submission_id_created_at", table_name="analysis_runs"
    )
    op.drop_table("analysis_runs")
    op.drop_index("ix_test_cases_submission_id", table_name="test_cases")
    op.drop_table("test_cases")
    op.drop_index("ix_submissions_created_at", table_name="submissions")
    op.drop_table("submissions")
