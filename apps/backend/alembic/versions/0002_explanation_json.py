"""change counterexamples.explanation from text to json

Revision ID: 0002_explanation_json
Revises: 0001_initial_schema
Create Date: 2026-01-20 00:00:00.000000

The explanation column was reserved (nullable Text, always NULL) in the
initial schema for a future AI milestone. That milestone now stores a
structured explanation object (see app.schemas.explanation), so the column
becomes JSON. Because it was always NULL, no data conversion is needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_explanation_json"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The column was reserved and always NULL, so a plain type change is
    # safe. Batch mode keeps this portable to SQLite (used in tests).
    with op.batch_alter_table("counterexamples") as batch_op:
        batch_op.alter_column(
            "explanation",
            existing_type=sa.Text(),
            type_=sa.JSON(),
            existing_nullable=True,
            postgresql_using="explanation::jsonb",
        )


def downgrade() -> None:
    with op.batch_alter_table("counterexamples") as batch_op:
        batch_op.alter_column(
            "explanation",
            existing_type=sa.JSON(),
            type_=sa.Text(),
            existing_nullable=True,
        )
