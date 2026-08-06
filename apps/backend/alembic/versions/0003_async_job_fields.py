"""async job columns on analysis_runs

Revision ID: 0003_async_job_fields
Revises: 0002_explanation_json
Create Date: 2026-01-25 00:00:00.000000

Adds the columns the async job workflow needs on analysis_runs: progress,
error, job_id, started_at, finished_at — and widens `status` from
VARCHAR(16) to VARCHAR(32) to fit the longer status vocabulary
('searching_properties', 'generating_tests', ...). Existing rows keep their
status; new columns are nullable or defaulted so the migration is safe on a
populated table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_async_job_fields"
down_revision: Union[str, None] = "0002_explanation_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "progress", sa.Float(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(sa.Column("error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("job_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch_op:
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("job_id")
        batch_op.drop_column("error")
        batch_op.drop_column("progress")
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
