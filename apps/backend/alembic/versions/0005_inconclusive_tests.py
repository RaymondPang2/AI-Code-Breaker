"""analysis_runs.inconclusive_tests

Revision ID: 0005_inconclusive_tests
Revises: 0004_submission_ownership
Create Date: 2026-02-15 00:00:00.000000

Adds a distinct inconclusive_tests count so passed + failed + inconclusive
== total. Previously failed_tests was computed as total - passed, which
folded harness/runner errors (internal_error) into failures and produced the
impossible "N failed but no behavioral differences" state. Defaulted to 0 so
the migration is safe on existing rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_inconclusive_tests"
down_revision: Union[str, None] = "0004_submission_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "inconclusive_tests",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "inconclusive_tests")
