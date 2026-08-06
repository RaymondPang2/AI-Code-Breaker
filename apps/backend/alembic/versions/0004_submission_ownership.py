"""submission ownership, privacy, and share token

Revision ID: 0004_submission_ownership
Revises: 0003_async_job_fields
Create Date: 2026-02-01 00:00:00.000000

Adds owner_digest (hashed client identity, for scoping reads/deletes and
quota), is_public + share_token (opt-in public share links via an
unguessable token instead of enumerable UUIDs). All nullable/defaulted so
the migration is safe on existing rows (which become private, unowned).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_submission_ownership"
down_revision: Union[str, None] = "0003_async_job_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("submissions") as batch_op:
        batch_op.add_column(
            sa.Column("owner_digest", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "is_public",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("share_token", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_submissions_share_token", ["share_token"]
        )
        batch_op.create_index(
            "ix_submissions_owner_digest", ["owner_digest"]
        )


def downgrade() -> None:
    with op.batch_alter_table("submissions") as batch_op:
        batch_op.drop_index("ix_submissions_owner_digest")
        batch_op.drop_constraint("uq_submissions_share_token", type_="unique")
        batch_op.drop_column("share_token")
        batch_op.drop_column("is_public")
        batch_op.drop_column("owner_digest")
